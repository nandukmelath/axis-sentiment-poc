"""Apache Beam transform stage — normalise / dedup / language-detect / spam / PII mask.
Sits between ingest (raw_posts) and enrich (analysis). Keyless: DirectRunner locally,
DataflowRunner in the cloud. No LLM involved."""
