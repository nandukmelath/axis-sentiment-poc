"""fact_interaction — the customer-service / CX fact.

When a public complaint mentions an Axis handle and someone from Axis replies in the
same thread, did it get resolved? was the customer satisfied? We reconstruct the thread
from the already-stored `conversation_id`, detect the BANK response by matching the
author against config.AXIS_HANDLES, and classify the outcome.

Heuristic-first (offline, deterministic — mirrors the VADER cascade), so it runs with
zero LLM cost. Set RESOLUTION_LLM=1 to add an LLM pass that refines resolution_type /
satisfaction on ambiguous threads (uses analyze.llm — same pluggable provider).

Grain = one customer issue/thread (accumulating snapshot). Run:  python -m warehouse.resolution
"""
import os
import pandas as pd

import db
import config
from warehouse.build import FACT_INTERACTION_COLS, _norm

AXIS = set(config.AXIS_HANDLES)
RESOLUTION_LLM = os.getenv("RESOLUTION_LLM", "0") == "1"

_RTYPE_KW = [
    ("refund", ["refund", "reversed", "credited back"]),
    ("escalated", ["dm", "direct message", "escalat", "raised a ticket", "raised the ticket", "team will"]),
    ("apology", ["sorry", "apolog", "regret", "inconvenience"]),
    ("info", ["please share", "kindly share", "help you", "assist"]),
]


def _rtype(text):
    t = (text or "").lower()
    for label, kws in _RTYPE_KW:
        if any(k in t for k in kws):
            return label
    return "responded"


def _hours_between(a, b):
    try:
        ta = pd.to_datetime(a, utc=True, format="mixed")
        tb = pd.to_datetime(b, utc=True, format="mixed")
        return (tb - ta).total_seconds() / 3600.0
    except Exception:
        return None


def build_interactions():
    d = db.df("""SELECT r.source_id, r.conversation_id, r.author, r.created_at, r.source, r.text,
                        a.score, a.sentiment
                 FROM raw_posts r LEFT JOIN analysis a ON r.source_id = a.source_id
                 WHERE r.conversation_id IS NOT NULL AND r.conversation_id <> ''""")
    if d.empty:
        db.execute("DELETE FROM fact_interaction")
        print("fact_interaction: no threaded conversations")
        return

    bridge = db.df("SELECT author, customer_key FROM bridge_handle_customer")
    bmap = dict(zip(bridge["author"], bridge["customer_key"])) if not bridge.empty else {}

    rows = []
    for conv, g in d.groupby("conversation_id"):
        g = g.sort_values("created_at")
        g = g.assign(h=g["author"].map(_norm))
        cust = g[~g["h"].isin(AXIS)]
        bank = g[g["h"].isin(AXIS)]
        if cust.empty:
            continue
        inbound = cust.iloc[0]
        opened = inbound["created_at"]

        first_resp, n_replies, rtype = None, int(len(bank)), "none"
        if not bank.empty:
            fr = bank.iloc[0]
            first_resp = fr["created_at"]
            rtype = _rtype(fr["text"])

        satisfied, post_score = None, None
        if first_resp is not None:
            fups = cust[cust["created_at"] > first_resp]
            if not fups.empty:
                last = fups.iloc[-1]
                post_score = last["score"]
                satisfied = 1 if (last["sentiment"] == "positive" or (last["score"] or 0) > 0.2) else 0

        inbound_score = inbound["score"]
        resolved = 1 if (first_resp is not None and
                         (satisfied == 1 or rtype in ("refund", "escalated"))) else 0
        latency_h = _hours_between(opened, first_resp) if first_resp is not None else None
        recovery = (post_score - inbound_score) if (post_score is not None and inbound_score is not None) else None

        rows.append({
            "issue_id": f"conv:{conv}", "conversation_id": conv, "source": inbound["source"],
            "customer_key": bmap.get(inbound["author"]), "author": inbound["author"],
            "inbound_source_id": inbound["source_id"], "opened_at": opened, "first_response_at": first_resp,
            "response_latency_min": round(latency_h * 60, 1) if latency_h is not None else None,
            "n_bank_replies": n_replies, "resolved": resolved, "customer_satisfied": satisfied,
            "resolution_type": rtype, "inbound_score": inbound_score, "post_response_score": post_score,
            "recovery_delta": round(recovery, 3) if recovery is not None else None,
            "updated_at": db.now(),
        })

    if RESOLUTION_LLM and rows:
        _llm_refine(rows)

    db.execute("DELETE FROM fact_interaction")
    db.upsert_rows("fact_interaction", rows, "issue_id", FACT_INTERACTION_COLS)
    resolved = sum(r["resolved"] for r in rows)
    print(f"fact_interaction: {len(rows)} threads · {resolved} resolved")


def _llm_refine(rows):
    """Optional: one cheap LLM call to disambiguate 'responded but unclear' threads."""
    try:
        from analyze.llm import generate_text
    except Exception:
        return
    for r in rows:
        if r["resolved"] or r["first_response_at"] is None:
            continue
        prompt = (f"A bank replied to a customer complaint on social media. resolution_type='{r['resolution_type']}'. "
                  f"Did this likely resolve the issue and satisfy the customer? Answer strictly 'resolved' or 'open'.")
        try:
            ans = (generate_text(prompt) or "").strip().lower()
            if "resolved" in ans:
                r["resolved"] = 1
                if r["customer_satisfied"] is None:
                    r["customer_satisfied"] = 1
        except Exception:
            continue


if __name__ == "__main__":
    build_interactions()
