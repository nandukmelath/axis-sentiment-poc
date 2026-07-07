"""MAX-HARVEST run — pull as much Axis data as the sources give, classify ALL of it with
LLM depth (drains the 6-provider FreeLLMAPI pool; VADER baseline holds any post the LLM
can't reach once tokens dry — nothing is lost), then build every insight layer.

Crank the harvest with env:
  FETCH_MULT=8            scale every source cap 8x
  SB_PAGES=10            X/ScrapeBadger pages (100 tweets/page)
  SLEEP_BETWEEN_BATCHES=1.5   faster drain (gpt-oss-120b on Cerebras is fast)

Run:  FETCH_MULT=8 SB_PAGES=10 SLEEP_BETWEEN_BATCHES=1.5 python -m run_harvest
"""
import json, time


def _step(summary, name, fn):
    t = time.time()
    try:
        out = fn()
        summary[name] = out if out is not None else "ok"
    except Exception as e:  # noqa — one failing stage never aborts the harvest
        summary[name + "_error"] = str(e)[:180]
        print(f"  ! {name} failed: {str(e)[:120]}")
    print(f"  [{name}] {round(time.time()-t,1)}s")
    return summary


def _counts():
    import db
    from sqlalchemy import text
    e = db.get_engine()
    out = {}
    with e.connect() as c:
        for t in ("raw_posts", "analysis", "clusters"):
            try:
                out[t] = c.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            except Exception:
                out[t] = None
        try:
            out["needs_llm_remaining"] = len(db.get_needs_llm())
        except Exception:
            out["needs_llm_remaining"] = None
    return out


def run():
    summary = {}
    from db import init_db
    init_db()

    try:
        import config
        for w in config.validate():
            print(f"  [config] {w}")
    except Exception:
        pass

    print("== BEFORE ==", _counts())

    # 1. FETCH — all sources, all-time (no window), high limits
    from fetch.run_fetch import run as fetch_run
    _step(summary, "fetched", lambda: fetch_run(window=None))

    # 2. TRANSFORM (Beam)
    from transform.beam_transform import run as transform_run
    _step(summary, "transformed", transform_run)

    # 3. VADER baseline for every new post (free, instant, PII-masked)
    from analyze.run_analyze import run_baseline, run_llm
    _step(summary, "vader_scored", lambda: run_baseline())

    # 4. LLM DEPTH — the token drain. Batches; on total 429 the batch skips, VADER holds.
    _step(summary, "llm_enriched", lambda: run_llm(limit=None))

    # 5. EMBED + CLUSTER (real embeddings if a Gemini key; else TF-IDF) -> emerging issues
    from analyze import embed_cluster
    _step(summary, "clustered", embed_cluster.main)

    # 6. WAREHOUSE — dims/facts/marts (star schema)
    from warehouse import build as wh_build
    _step(summary, "warehouse", lambda: wh_build.main("all"))

    # 7. RESOLUTION — LLM per resolved thread (CX / recovery) — more tokens
    try:
        from warehouse.resolution import build_interactions
        _step(summary, "resolution", build_interactions)
    except Exception as e:
        summary["resolution_error"] = str(e)[:120]

    # 8. INSIGHT MARTS — product/influencer/team/fraud/trend/geo + churn/forecast/entities
    from analytics import features, intelligence
    _step(summary, "features", features.build_all)
    _step(summary, "intelligence", intelligence.build_all)

    # 9. TRANSLATE non-English mentions (LLM) — more tokens
    try:
        from analytics.translate import translate
        _step(summary, "translated", lambda: translate(limit=200))
    except Exception as e:
        summary["translate_error"] = str(e)[:120]

    # 10. COMPETITOR share-of-voice (Axis vs HDFC/ICICI/SBI/Kotak)
    try:
        from analytics.competitor import run as competitor_run
        _step(summary, "competitor", lambda: competitor_run(limit=40))
    except Exception as e:
        summary["competitor_error"] = str(e)[:120]

    # 11. ACTIONS — alerts, weekly digest, draft replies (LLM)
    from analytics import actions
    _step(summary, "alerts", actions.build_alerts)
    _step(summary, "digest", actions.weekly_digest)
    _step(summary, "draft_replies", lambda: actions.draft_replies(limit=25))

    # 12. EXEC BRIEF — single big LLM narrative
    try:
        from analyze import exec_summary
        _step(summary, "exec_brief", exec_summary.main)
    except Exception as e:
        summary["exec_brief_error"] = str(e)[:120]

    # 13. OPS — quality, drift, cost
    from analytics import ops
    _step(summary, "ops", ops.run_all)

    # 14. DQ GATE
    try:
        from warehouse import dq_checks
        _step(summary, "dq", dq_checks.main)
    except Exception as e:
        summary["dq_error"] = str(e)[:120]

    after = _counts()
    summary["after"] = after
    print("== AFTER ==", after)
    print("HARVEST " + json.dumps(summary, default=str))
    return summary


if __name__ == "__main__":
    run()
