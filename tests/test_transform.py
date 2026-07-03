"""Apache Beam transform — the pure per-record functions (clean/lang/spam/dedup)."""
from transform import beam_transform as bt


def test_clean_strips_urls_and_hashes():
    row = bt._clean({"text": "Axis UPI down https://t.co/abc check now", "created_at": "2026-07-01"})
    assert "http" not in row["clean_text"]
    assert row["_n_urls"] == 1
    assert row["text_hash"]                      # non-empty md5


def test_dedup_same_text_same_hash():
    a = bt._clean({"text": "Axis UPI is DOWN!!!", "created_at": "1"})
    b = bt._clean({"text": "axis upi is down", "created_at": "2"})
    assert a["text_hash"] == b["text_hash"]      # normalised hash ignores case/punct


def test_lang_hindi_devanagari():
    assert bt._detect_lang("एक्सिस बैंक यूपीआई काम नहीं कर रहा") == "hi"


def test_lang_hinglish():
    assert bt._detect_lang("mera axis upi kaam nahi kar raha hai kyun") == "hi-en"


def test_lang_english_default():
    assert bt._detect_lang("Axis UPI is not working today") == "en"


def test_spam_promo_flagged():
    assert bt._is_spam("Free recharge! click here join now t.me/scam", 0) == 1


def test_spam_many_urls():
    assert bt._is_spam("check", 3) == 1


def test_not_spam_normal_complaint():
    assert bt._is_spam("Axis UPI failed and money got debited", 0) == 0


def test_mark_dups_earliest_canonical():
    rows = [{"created_at": "2026-07-02"}, {"created_at": "2026-07-01"}, {"created_at": "2026-07-03"}]
    out = list(bt._mark_dups(("h", rows)))
    dups = [r["is_duplicate"] for r in sorted(out, key=lambda r: r["created_at"])]
    assert dups == [0, 1, 1]                     # earliest is canonical
