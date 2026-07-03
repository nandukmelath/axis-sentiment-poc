"""PII masker — the compliance-critical unit. Must never leak card/PAN/Aadhaar/phone/OTP."""
from analyze import pii


def test_card_luhn_masked_keep_last4():
    m, t = pii.mask("my card 4111 1111 1111 1111 was charged")
    assert "4111 1111 1111 1111" not in m
    assert "XXXX-XXXX-XXXX-1111" in m
    assert "card" in t


def test_non_luhn_16_digits_is_account_not_card():
    m, t = pii.mask("ref 1234 5678 9012 3456 today")   # 16 digits, fails Luhn
    assert "card" not in t
    assert "account" in t


def test_pan_masked():
    m, t = pii.mask("PAN ABCDE1234F please")
    assert "ABCDE1234F" not in m and "[PAN]" in m and "pan" in t


def test_aadhaar_masked():
    m, t = pii.mask("aadhaar 1234 5678 9012")
    assert "1234 5678 9012" not in m and "aadhaar" in t


def test_phone_masked_keep_last2():
    m, t = pii.mask("call me on 9876543210 now")
    assert "9876543210" not in m and "phone" in t
    assert m.strip().endswith("10 now") or "XXXXXXXX10" in m


def test_otp_masked():
    m, t = pii.mask("the OTP is 445566 do not share")
    assert "445566" not in m and "otp" in t


def test_email_masked():
    m, t = pii.mask("reach ravi.kumar@example.com")
    assert "ravi.kumar@example.com" not in m and "email" in t


def test_short_numbers_not_masked():
    m, t = pii.mask("I have 3 cards and 2 accounts")
    assert t == [] and m == "I have 3 cards and 2 accounts"


def test_idempotent():
    once, _ = pii.mask("card 4111 1111 1111 1111 call 9876543210")
    twice, _ = pii.mask(once)
    assert once == twice          # masking already-masked text is a no-op


def test_masked_fields_shape():
    f = pii.masked_fields("card 4111 1111 1111 1111")
    assert set(f) == {"text_masked", "pii_types", "pii_present"}
    assert f["pii_present"] == 1 and "card" in f["pii_types"]


def test_empty_text_safe():
    assert pii.mask("") == ("", [])
    assert pii.mask(None) == ("", [])
