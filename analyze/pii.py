"""PII masking — runs BEFORE any text reaches a third-party LLM.

Masks Indian-banking PII: card PANs (Luhn-checked), CVV/OTP/PIN, account numbers,
tax PAN, Aadhaar, phone, email. Deterministic + fully offline (regex + Luhn), so it
also runs on the free / on-prem path with zero cost. This is the RBI / DPDP data-
safety layer: the raw text stays in bronze (`raw_posts`) behind access control, and
only the masked `text_masked` is sent to Gemini/Groq and stored in silver.

    masked, types = mask("card 4111 1111 1111 1111 charged, call 9876543210")
    # -> ("card XXXX-XXXX-XXXX-1111 charged, call XXXXXXXX10", ["card", "phone"])
"""
import re

EMAIL_RE = re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
TAXPAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")            # Indian income-tax PAN, e.g. ABCDE1234F
OTP_RE = re.compile(r"(?i)\b(otp|cvv|pin|code)\b[^\d]{0,10}(\d{3,6})\b")
DIGITS_RE = re.compile(r"\b\d(?:[ \-]?\d){8,21}\b")             # long digit runs (9-22 digits): card/acct/aadhaar/phone


def _luhn(num: str) -> bool:
    total, alt = 0, False
    for ch in reversed(num):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _keep(digits: str, keep: int = 4, ch: str = "X") -> str:
    if len(digits) <= keep:
        return ch * len(digits)
    return ch * (len(digits) - keep) + digits[-keep:]


def mask(text):
    """Return (masked_text, [pii_types]). Idempotent — masking already-masked text is a no-op."""
    if not text:
        return text or "", []
    types = []
    out = text

    if EMAIL_RE.search(out):
        types.append("email")
        out = EMAIL_RE.sub("[EMAIL]", out)

    if TAXPAN_RE.search(out):
        types.append("pan")
        out = TAXPAN_RE.sub("[PAN]", out)

    def _otp_sub(m):
        types.append(m.group(1).lower())
        return f"{m.group(1)} [REDACTED]"
    out = OTP_RE.sub(_otp_sub, out)

    def _dig_sub(m):
        raw = m.group(0)
        d = re.sub(r"\D", "", raw)
        n = len(d)
        if 13 <= n <= 19 and _luhn(d):
            types.append("card")
            return "XXXX-XXXX-XXXX-" + d[-4:]
        if n == 12:
            types.append("aadhaar_or_account")   # 12 digits is ambiguous (Aadhaar vs acct); redacted either way
            return "XXXX-XXXX-" + d[-4:]
        if n == 10:
            types.append("phone")
            return "XXXXXXXX" + d[-2:]
        if n >= 9:
            types.append("account")
            return _keep(d, 4)
        return raw
    out = DIGITS_RE.sub(_dig_sub, out)

    # de-dup preserving first-seen order
    seen = set()
    types = [t for t in types if not (t in seen or seen.add(t))]
    return out, types


def masked_fields(text):
    """Convenience for writers: the three silver columns derived from raw text."""
    m, types = mask(text)
    return {"text_masked": m, "pii_types": ",".join(types), "pii_present": 1 if types else 0}
