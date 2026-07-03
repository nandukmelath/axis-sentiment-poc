"""Turn thousands of posts into a ranked list of ISSUES.
Embed -> cluster (cosine) -> per-cluster size / emerging-share / owning team.
'500 people saying the same thing = one issue, count 500.'

Run:  python -m analyze.embed_cluster
"""
import json
import numpy as np
import pandas as pd
from collections import Counter
from sklearn.cluster import AgglomerativeClustering

from config import CLUSTER_DISTANCE
from db import init_db, df, set_cluster, upsert_cluster


def _embed(texts):
    """Embeddings if an LLM/key is available; otherwise a KEYLESS TF-IDF fallback so
    clustering still runs offline (no Gemini, no key)."""
    try:
        from analyze.llm import embed_texts
        return np.array(embed_texts(texts), dtype=float)
    except Exception as e:
        print(f"  embeddings unavailable ({str(e)[:60]}) -> TF-IDF fallback (keyless)")
        from sklearn.feature_extraction.text import TfidfVectorizer
        try:
            X = TfidfVectorizer(max_features=512, stop_words="english").fit_transform([t or "" for t in texts])
            arr = X.toarray().astype(float)
            if arr.shape[1] == 0:
                raise ValueError("empty vocab")
            # cosine clustering rejects all-zero rows (stopword-only / empty posts) —
            # give them a tiny uniform signature so they cluster together instead of crashing.
            zero = ~arr.any(axis=1)
            if zero.any():
                arr[zero] = 1e-6
            return arr
        except Exception:
            return np.eye(max(len(texts), 1))


def _mode(series, default=""):
    vals = [v for v in series if v]
    return Counter(vals).most_common(1)[0][0] if vals else default


def main():
    init_db()
    data = df("""SELECT a.source_id, a.theme, a.summary, a.score, a.recommended_team,
                        r.text, r.created_at
                 FROM analysis a JOIN raw_posts r ON a.source_id = r.source_id""")
    if data.empty:
        print("no analyzed posts yet — run analyze.run_analyze first.")
        return

    n = len(data)
    print(f"embedding {n} posts ...")
    vecs = _embed(data["text"].fillna("").tolist())

    if n == 1:
        labels = np.array([0])
    else:
        model = AgglomerativeClustering(
            n_clusters=None, distance_threshold=CLUSTER_DISTANCE,
            metric="cosine", linkage="average")
        labels = model.fit_predict(vecs)
    data["cluster_id"] = labels
    print(f"found {len(set(labels))} issue clusters (distance<{CLUSTER_DISTANCE}).")

    # reference 'now' = latest post time, so emerging works on static seed data too
    ts = pd.to_datetime(data["created_at"], errors="coerce", utc=True)
    ref = ts.max()
    recent_cut = ref - pd.Timedelta(hours=24) if pd.notna(ref) else None

    rows = []
    for cid, g in data.groupby("cluster_id"):
        idx = g.index
        recent_share = 0.0
        if recent_cut is not None:
            gts = ts.loc[idx]
            recent_share = float((gts >= recent_cut).mean())
        title = _mode(g["theme"]) or (g["summary"].iloc[0] if len(g) else f"cluster {cid}")
        rows.append({
            "cluster_id": int(cid),
            "title": title,
            "size": int(len(g)),
            "recent_share": round(recent_share, 2),
            "avg_score": round(float(g["score"].mean()), 3),
            "top_team": _mode(g["recommended_team"], "none"),
            "sample_ids": json.dumps(g["source_id"].head(5).tolist()),
        })
        for sid in g["source_id"]:
            set_cluster(sid, int(cid))
    for r in rows:
        upsert_cluster(r)

    rows.sort(key=lambda x: -x["size"])
    print("\n=== TOP ISSUES (by volume) ===")
    for r in rows[:10]:
        print(f"  [{r['size']:>3}] {r['title'][:60]:<60} team={r['top_team']:<16} avg={r['avg_score']:+.2f}")

    emerging = [r for r in rows if r["recent_share"] >= 0.6 and r["size"] >= 2 and r["avg_score"] < 0]
    if emerging:
        print("\n=== EMERGING NEGATIVE ISSUES (spiking in last 24h) ===")
        for r in sorted(emerging, key=lambda x: -x["size"]):
            print(f"  [{r['size']:>3}] {r['title'][:60]}  ({int(r['recent_share']*100)}% recent)")


if __name__ == "__main__":
    main()
