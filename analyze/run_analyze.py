"""Score posts. Two-phase when CASCADE=1:
  1) VADER gives EVERY new post a real baseline sentiment (instant, free) — nothing
     is left unscored, so the table isn't skewed positive.
  2) The configured LLM (LLM_PROVIDER) adds decision-grade DEPTH to the negative/
     neutral posts. Batch-resilient: one failed batch is skipped, not fatal.

Phases are separately invocable so Airflow can show them as distinct tasks:
  python -m analyze.run_analyze --phase baseline   # VADER + PII mask, free, always
  python -m analyze.run_analyze --phase llm        # LLM depth on negatives (may need a key)
  python -m analyze.run_analyze                     # both (default)   [--limit N] [--verify]
"""
import json, time, argparse
from config import BATCH_SIZE, SLEEP_BETWEEN_BATCHES, GEMINI_MODEL, CASCADE, LLM_PROVIDER, PII_MASK
from db import init_db, get_unanalyzed, get_needs_llm, upsert_analysis
from analyze.llm import analyze_batch
from analyze import pii


def to_row(a) -> dict:
    d = a.model_dump()
    aspects = d.pop("aspects", [])
    return {
        **{k: d[k] for k in [
            "source_id", "sentiment", "score", "emotion", "emotion_intensity", "intent", "urgency",
            "urgency_reason", "product", "root_cause", "rbi_category", "recommended_team",
            "recommended_action", "fraud_type", "theme", "summary", "confidence"]},
        "sarcasm": int(d["sarcasm"]), "churn_risk": int(d["churn_risk"]),
        "fraud_signal": int(d["fraud_signal"]), "pii_present": int(d["pii_present"]),
        "aspects_json": json.dumps([x if isinstance(x, dict) else x for x in aspects], ensure_ascii=False),
        "cluster_id": None, "model": LLM_PROVIDER if LLM_PROVIDER != "gemini" else GEMINI_MODEL,
    }


def run_baseline(limit=None):
    """Phase 1 — VADER baseline for every new post (real sentiment, free) + PII mask."""
    new = get_unanalyzed(limit=limit)
    if not (CASCADE and new):
        print("baseline: nothing new")
        return 0
    from analyze import cascade
    rows = cascade.fast_all(new)
    by_id = {p["source_id"]: p for p in new}
    for row in rows:
        if PII_MASK:
            row.update(pii.masked_fields(by_id[row["source_id"]].get("text", "")))
        upsert_analysis(row)
    print(f"VADER baseline scored {len(new)} new posts (real sentiment)")
    return len(new)


def run_llm(limit=None, verify=False):
    """Phase 2 — LLM depth on negative/neutral posts (new + backlog). Batch-resilient."""
    todo = get_needs_llm(limit=limit) if CASCADE else get_unanalyzed(limit=limit)
    if not todo:
        print("nothing needs LLM depth.")
        return 0
    print(f"LLM depth [{LLM_PROVIDER}] on {len(todo)} negative/neutral posts, batches of {BATCH_SIZE} ...")
    done = 0
    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        # PII mask BEFORE the LLM sees the text; keep the masked fields to persist
        masked = {p["source_id"]: (pii.masked_fields(p.get("text", "")) if PII_MASK
                                   else {"text_masked": p.get("text", ""), "pii_types": "", "pii_present": 0})
                  for p in batch}
        llm_batch = [{**p, "text": masked[p["source_id"]]["text_masked"]} for p in batch]
        try:
            results = analyze_batch(llm_batch)
        except Exception as e:
            print(f"  batch {i // BATCH_SIZE + 1} failed ({str(e)[:70]}) — skipping")
            continue
        by_id = {r.source_id: r for r in results}
        for p in batch:
            a = by_id.get(p["source_id"])
            if not a:
                continue
            if verify and LLM_PROVIDER == "gemini" and (a.fraud_signal or a.urgency.value == "critical"):
                from analyze.gemini_client import verify_fraud
                a.fraud_signal = verify_fraud(masked[p["source_id"]]["text_masked"])
            row = to_row(a)
            mf = masked[p["source_id"]]
            row["text_masked"] = mf["text_masked"]      # persist through INSERT-OR-REPLACE
            row["pii_types"] = mf["pii_types"]
            row["pii_present"] = 1 if (row.get("pii_present") or mf["pii_present"]) else 0
            upsert_analysis(row)
            done += 1
        print(f"  {done}/{len(todo)} enriched")
        if i + BATCH_SIZE < len(todo):
            time.sleep(SLEEP_BETWEEN_BATCHES)
    print(f"done: {done} posts enriched with {LLM_PROVIDER}.")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--verify", action="store_true", help="2nd-pass fraud check (gemini only)")
    ap.add_argument("--phase", choices=["baseline", "llm", "both"], default="both")
    args = ap.parse_args()
    init_db()
    if args.phase in ("baseline", "both"):
        run_baseline(limit=args.limit)
    if args.phase in ("llm", "both"):
        run_llm(limit=args.limit, verify=args.verify)


if __name__ == "__main__":
    main()
