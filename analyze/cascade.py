"""Cheap-classifier-first cascade.
VADER (free, instant) gives EVERY post a real baseline sentiment (positive/negative/
neutral) so nothing is left unscored and the table isn't skewed positive. The LLM then
adds decision-grade DEPTH (aspect/intent/urgency/team/fraud) to the negative/neutral
posts — the ones a bank must act on.
"""
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from config import VADER_POS

_an = SentimentIntensityAnalyzer()

RISK_KW = ["fraud", "scam", "otp", "phish", "cheat", "stolen", "lawyer", "court", "ombudsman",
           "legal", "refund", "fail", "debited", "stuck", "not working", "down", "blocked",
           "charged", "unauthori", "complaint", "kyc", "hack", "block", "error", "crash"]
FRAUD_KW = ["fraud", "scam", "otp", "phish", "cheat", "stolen", "hack", "kyc", "fake"]


def classify_fast(text):
    t = text or ""
    comp = _an.polarity_scores(t)["compound"]
    low = t.lower()
    hits = [k for k in RISK_KW if k in low]
    sent = "positive" if comp >= 0.05 else "negative" if comp <= -0.05 else "neutral"
    return {"sentiment": sent, "score": round(comp, 3), "hits": hits, "compound": comp}


def fast_row(post):
    """A complete analysis row from VADER — REAL sentiment for every post."""
    f = classify_fast(post.get("text", ""))
    low = (post.get("text") or "").lower()
    sent = f["sentiment"]
    pos, neg = sent == "positive", sent == "negative"
    risky = bool(f["hits"])
    fraud = 1 if any(k in low for k in FRAUD_KW) else 0
    return {
        "source_id": post["source_id"],
        "sentiment": sent, "score": f["score"],
        "emotion": "joy" if pos else ("anger" if (neg and risky) else "neutral"),
        "emotion_intensity": 3 if neg else 2, "sarcasm": 0,
        "intent": "praise" if pos else ("complaint" if neg else "other"),
        "urgency": "high" if (neg and risky) else ("medium" if neg else "low"),
        "urgency_reason": "VADER baseline (pending LLM depth)" if sent != "positive" else "VADER (clear positive)",
        "product": "unspecified", "root_cause": "", "rbi_category": "not_applicable",
        "recommended_team": "none", "recommended_action": "", "churn_risk": 0,
        "fraud_signal": fraud, "fraud_type": "suspected" if fraud else "none", "pii_present": 0,
        "theme": "", "summary": (post.get("text") or "")[:90], "confidence": 0.4,
        "aspects_json": "[]", "cluster_id": None, "model": "vader-fast",
    }


def needs_llm(row):
    """Negatives/neutral get LLM depth; clear positives don't need it."""
    return row["sentiment"] != "positive"


def fast_all(posts):
    return [fast_row(p) for p in posts]


# kept for backwards-compat (old split API)
def split(todo):
    light, llm = [], []
    for p in todo:
        r = fast_row(p)
        (llm if needs_llm(r) else light).append(r if not needs_llm(r) else p)
    return light, llm
