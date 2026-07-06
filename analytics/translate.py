"""Translate Hindi / Hinglish mentions to English. LLM (Groq 8b) when available;
passthrough fallback keeps it keyless. Stores to `translations`.

Run:  python -m analytics.translate
"""
import db
import config
from analytics.features import ensure_tables, TRANS_COLS

PROMPT = ("Translate this Indian banking social-media post to concise English. "
          "Output ONLY the English translation:\n\"{t}\"")


def translate(limit=25):
    ensure_tables()
    rows = db.df(f"""SELECT c.source_id, c.lang, c.clean_text FROM clean_posts c
                     LEFT JOIN translations t ON c.source_id = t.source_id
                     WHERE t.source_id IS NULL AND c.lang IN ('hi','hi-en')
                       AND c.clean_text <> '' LIMIT {int(limit)}""")
    if rows.empty:
        print("no untranslated hi/hi-en posts")
        return 0
    try:
        from analyze.llm import generate_text
        use_llm = True
    except Exception:
        use_llm = False
    out = []
    model = "passthrough"
    for _, r in rows.iterrows():
        eng, model = (r["clean_text"] or ""), "passthrough"
        if use_llm:
            try:
                eng = (generate_text(PROMPT.format(t=(r["clean_text"] or "")[:400]),
                                     model=getattr(config, "BRIEF_MODEL", None)) or r["clean_text"]).strip()[:500]
                model = config.LLM_PROVIDER
            except Exception:
                eng, model = (r["clean_text"] or ""), "passthrough"
        out.append(dict(source_id=r["source_id"], lang=r["lang"], english=eng, model=model,
                        created_at=db.now()))
    db.upsert_rows("translations", out, "source_id", TRANS_COLS)
    print(f"translated {len(out)} posts ({model})")
    return len(out)


if __name__ == "__main__":
    translate()
