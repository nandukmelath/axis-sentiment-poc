"""Apache Beam transform stage (raw_posts -> clean_posts).

Slots BETWEEN ingest and enrich in the Airflow DAG. Does the heavy, embarrassingly-
parallel cleaning that shouldn't sit inside the LLM path:

  Clean      strip URLs / RT / zero-width, collapse whitespace, normalise
  Dedup      md5 of normalised text -> GroupByKey -> keep earliest as canonical, mark rest
  LangDetect Devanagari -> hi, romanised-Hindi tokens -> hi-en, else en   (heuristic, keyless)
  Spam       url-spam / promo / channel-blast / repeated-char heuristics
  PII mask   reuse analyze.pii (regex+Luhn) so the clean staging text is already safe

Fully KEYLESS — no LLM, no cloud. DirectRunner locally; swap BEAM_RUNNER=DataflowRunner
(or --runner) for cloud scale. Beam gives the same pipeline on one core or a cluster.

Run:  python -m transform.beam_transform [--runner DirectRunner] [--limit N] [--all]
"""
import argparse
import hashlib
import re

import pandas as pd
import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions

import db
import config
from analyze import pii


def _ts_key(r):
    """Sortable ns timestamp tolerant of mixed date formats; unknown dates sort LAST so a
    row with no parseable date is never chosen as the canonical."""
    t = db.parse_dt(r.get("created_at"))
    return t.value if pd.notna(t) else 2 ** 63 - 1

URL_RE = re.compile(r"https?://\S+")
WS_RE = re.compile(r"\s+")
DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
HINGLISH = {"hai", "nahi", "nahin", "kar", "karo", "kyun", "kyu", "mera", "meri", "paisa",
            "paise", "atak", "raha", "rahi", "ho", "kaise", "bhai", "yaar", "kab", "chal"}
PROMO = ["subscribe", "giveaway", "buy now", "limited offer", "click here", "join now",
         "t.me/", "whatsapp +", "dm for", "promo code", "earn money", "free recharge"]
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿]")


def _clean(row):
    """Normalise text + compute a dedup hash. Keeps the original untouched in bronze."""
    raw = row.get("text") or ""
    n_urls = len(URL_RE.findall(raw))
    t = URL_RE.sub("", raw)
    t = re.sub(r"^\s*RT\b[:,]?", "", t)          # drop retweet prefix
    t = t.replace("​", "").replace("﻿", "")
    t = WS_RE.sub(" ", t).strip()
    norm = re.sub(r"[^a-z0-9ऀ-ॿ ]", "", t.lower())
    row = dict(row)
    row["clean_text"] = t
    row["text_hash"] = hashlib.md5(norm.encode("utf-8"), usedforsecurity=False).hexdigest()
    row["_n_urls"] = n_urls
    return row


def _mark_dups(kv, seen_hashes=frozenset()):
    """Earliest post in a text_hash group is canonical; rest are dups. STATEFUL across
    incremental runs: if a canonical for this hash already exists in clean_posts (seen_hashes),
    the whole incoming group is marked duplicate — otherwise each run would mint a new canonical
    for the same text. Sort by real parsed time (mixed formats) so 'earliest' is correct."""
    h, rows = kv
    rows = sorted(rows, key=_ts_key)
    already = h in seen_hashes
    for i, r in enumerate(rows):
        r["is_duplicate"] = 1 if (already or i > 0) else 0
        yield r


def _detect_lang(t):
    if DEVANAGARI_RE.search(t):
        return "hi"
    toks = set(re.findall(r"[a-z]+", t.lower()))
    return "hi-en" if len(toks & HINGLISH) >= 2 else "en"


def _is_spam(t, n_urls):
    low = t.lower()
    if n_urls >= 3:
        return 1
    if any(k in low for k in PROMO):
        return 1
    if len(EMOJI_RE.findall(t)) >= 6:
        return 1
    if re.search(r"(.)\1{6,}", t):               # 7+ repeated chars (aaaaaaa)
        return 1
    return 0


def _enrich(row):
    """Language + spam + PII mask (mask reused from analyze.pii — keyless)."""
    clean = row["clean_text"]
    masked, ptypes = pii.mask(clean)
    return {
        "source_id": row["source_id"],
        "clean_text": masked,
        "lang": _detect_lang(clean),
        "text_hash": row["text_hash"],
        "is_duplicate": row["is_duplicate"],
        "spam_flag": _is_spam(clean, row.get("_n_urls", 0)),
        "pii_types": ",".join(ptypes),
        "transformed_at": db.now(),
    }


class WriteCleanPosts(beam.DoFn):
    """Sink: upsert each bundle into clean_posts (dialect-aware upsert from db.py)."""
    def start_bundle(self):
        self.buf = []

    def process(self, row):
        self.buf.append(row)

    def finish_bundle(self):
        if self.buf:
            db.upsert_rows("clean_posts", self.buf, "source_id", db.CLEAN_COLS)
            self.buf = []


def run(runner=None, limit=None, all_posts=False):
    db.init_db()
    rows = db.df("SELECT r.source_id, r.text, r.created_at FROM raw_posts r").to_dict("records") \
        if all_posts else db.get_untransformed(limit=limit)
    if not rows:
        print("transform: nothing new to transform")
        return 0
    runner = runner or config.BEAM_RUNNER
    # canonical hashes already in clean_posts, so an incremental batch doesn't mint a 2nd canonical
    seen = set(db.df("SELECT DISTINCT text_hash FROM clean_posts WHERE is_duplicate=0")["text_hash"]) \
        if not all_posts else set()
    print(f"Beam transform [{runner}] on {len(rows)} posts ...")
    opts = PipelineOptions(flags=[], runner=runner)   # flags=[] so Beam ignores our argparse args
    with beam.Pipeline(options=opts) as p:
        (p
         | "Create" >> beam.Create(rows)
         | "Clean" >> beam.Map(_clean)
         | "KeyByHash" >> beam.Map(lambda r: (r["text_hash"], r))
         | "GroupDup" >> beam.GroupByKey()
         | "MarkDup" >> beam.FlatMap(_mark_dups, seen_hashes=seen)
         | "Enrich" >> beam.Map(_enrich)
         | "Write" >> beam.ParDo(WriteCleanPosts()))
    # stats
    s = db.df("""SELECT COUNT(*) n, SUM(is_duplicate) dups, SUM(spam_flag) spam,
                        SUM(CASE WHEN pii_types<>'' THEN 1 ELSE 0 END) pii
                 FROM clean_posts""").iloc[0]
    langs = db.df("SELECT lang, COUNT(*) n FROM clean_posts GROUP BY lang ORDER BY n DESC")
    print(f"clean_posts: {int(s['n'])} rows | dups={int(s['dups'] or 0)} "
          f"spam={int(s['spam'] or 0)} pii={int(s['pii'] or 0)} | "
          f"lang={dict(zip(langs['lang'], langs['n']))}")
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runner", default=None, help="DirectRunner (default) | DataflowRunner")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--all", action="store_true", help="re-transform ALL raw_posts, not just new")
    a = ap.parse_args()
    run(runner=a.runner, limit=a.limit, all_posts=a.all)


if __name__ == "__main__":
    main()
