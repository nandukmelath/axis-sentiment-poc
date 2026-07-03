"""Streaming WORKER — runs forever. VADER-baselines new posts (real sentiment,
instant), then adds LLM depth (LLM_PROVIDER) to the negative/neutral ones.
Batch-resilient; re-clusters every RECLUSTER_EVERY newly-enriched posts.

Run:  python -m stream.worker
"""
import os, sys, time, subprocess
from config import BATCH_SIZE, SLEEP_BETWEEN_BATCHES, CASCADE, LLM_PROVIDER, PII_MASK
from db import init_db, get_unanalyzed, get_needs_llm, upsert_analysis
from analyze.run_analyze import to_row
from analyze.llm import analyze_batch
from analyze import cascade, pii

RECLUSTER_EVERY = int(os.getenv("RECLUSTER_EVERY", "20"))


def main():
    init_db()
    since_cluster = 0
    print(f"analyze worker streaming [{LLM_PROVIDER}]. Ctrl+C to stop.")
    while True:
        try:
            # phase 1: VADER baseline for new posts (+ PII mask)
            new = get_unanalyzed(limit=BATCH_SIZE)
            if new and CASCADE:
                by_new = {p["source_id"]: p for p in new}
                for row in cascade.fast_all(new):
                    if PII_MASK:
                        row.update(pii.masked_fields(by_new[row["source_id"]].get("text", "")))
                    upsert_analysis(row)
                print(f"[worker] VADER baseline {len(new)}")

            # phase 2: LLM depth on negatives/neutral (mask before LLM)
            todo = get_needs_llm(limit=BATCH_SIZE) if CASCADE else new
            if not todo:
                time.sleep(5)
                continue
            masked = {p["source_id"]: (pii.masked_fields(p.get("text", "")) if PII_MASK
                                       else {"text_masked": p.get("text", ""), "pii_types": "", "pii_present": 0})
                      for p in todo}
            llm_batch = [{**p, "text": masked[p["source_id"]]["text_masked"]} for p in todo]
            try:
                results = analyze_batch(llm_batch)
            except Exception as e:
                print(f"[worker] batch failed ({str(e)[:70]}) — skip")
                time.sleep(SLEEP_BETWEEN_BATCHES)
                continue
            by_id = {r.source_id: r for r in results}
            n = 0
            for p in todo:
                a = by_id.get(p["source_id"])
                if a:
                    row = to_row(a)
                    mf = masked[p["source_id"]]
                    row["text_masked"] = mf["text_masked"]
                    row["pii_types"] = mf["pii_types"]
                    row["pii_present"] = 1 if (row.get("pii_present") or mf["pii_present"]) else 0
                    upsert_analysis(row)
                    n += 1
            since_cluster += n
            print(f"[worker] enriched {n}")
            if since_cluster >= RECLUSTER_EVERY:
                subprocess.run([sys.executable, "-m", "analyze.embed_cluster"], check=False)
                since_cluster = 0
            time.sleep(SLEEP_BETWEEN_BATCHES)
        except KeyboardInterrupt:
            print("\nworker stopped.")
            break
        except Exception as e:
            print(f"[worker] error: {str(e)[:100]}")
            time.sleep(10)


if __name__ == "__main__":
    main()
