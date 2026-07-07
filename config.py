"""Central config. Loads .env so every module sees the keys."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

DB_PATH = str(BASE / "axis.db")

# ---- LLM ----
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")   # 2.0-* free tier is often disabled
EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "12"))          # posts per Gemini call
SLEEP_BETWEEN_BATCHES = float(os.getenv("SLEEP_BETWEEN_BATCHES", "4.5"))  # ~13 rpm < free 15
MAX_RETRIES = 5

# ---- clustering ----
CLUSTER_DISTANCE = 0.35   # cosine distance threshold for issue clustering (lower = tighter)

# ---- brand / targets ----
BRAND = "Axis Bank"
BRAND_ALIASES = ["Axis Bank", "AxisBank", "@AxisBank", "Axis Mobile", "#AxisBank", "Axis"]
COMPETITORS = ["HDFC Bank", "ICICI Bank", "SBI", "Kotak Mahindra"]

# ---- fetch config (Phase 1) ----
# Ranked by Axis-sentiment signal density (verified 2026-07-02).
# r/CreditCardsIndia = richest complaint vein (rewards devaluation, rejections, ombudsman).
# Customer-sentiment subs first, then equity-sentiment (stock) subs, then city/local.
SUBREDDITS = [
    "CreditCardsIndia", "IndianCreditCards", "personalfinanceindia",
    "IndiaInvestments", "IndiaFinance", "india",
    "IndianStreetBets", "DalalStreetTalks", "IndianStockMarket",
    "bangalore", "mumbai", "developersIndia",
]
# how many top comments to pull per matched submission (comments carry most sentiment)
REDDIT_COMMENTS_PER = int(os.getenv("REDDIT_COMMENTS_PER", "6"))
PLAY_APP_ID = "com.axis.mobile"   # confirm from the Axis Mobile Play Store URL
PLAY_COUNTRY = "in"
PLAY_LANG = "en"
APPSTORE_APP_ID = os.getenv("APPSTORE_APP_ID", "")   # numeric iOS app id for Axis Mobile; set to enable
NEWS_RSS = "https://news.google.com/rss/search?q=%22Axis+Bank%22&hl=en-IN&gl=IN&ceid=IN:en"
# Google News: multiple queries for wider coverage (all keyless).
NEWS_QUERIES = ["Axis Bank", "Axis Bank UPI", "Axis Bank fraud", "Axis Bank credit card",
                "Axis Magnus", "Axis Bank RBI"]
TWITTER_QUERIES = ["Axis Bank", "@AxisBank"]

# ---- extra FREE / keyless sources ----
BLUESKY_QUERIES = ["Axis Bank", "AxisBank", "Axis Magnus"]      # public.api.bsky.app searchPosts (no auth)
HN_QUERY = "Axis Bank"                                          # Hacker News Algolia (no key)
MASTODON_TAGS = ["AxisBank", "Axis"]                            # public hashtag timelines (no auth)
APPSTORE_SEARCH = "Axis Mobile"                                # auto-resolve iOS app id when APPSTORE_APP_ID unset
# X/Twitter ingestion mode: 'csv' (import fetch/twitter_import.csv — reliable, default),
# 'scrape' (free Nitter scraper — usually dead in 2026), 'auto' (scrape then csv fallback).
TWITTER_MODE = os.getenv("TWITTER_MODE", "csv")

# ---- streaming firehoses (Phase 5) ----
MASTODON_INSTANCE = os.getenv("MASTODON_INSTANCE", "mastodon.social")
MASTODON_TOKEN = os.getenv("MASTODON_TOKEN", "")   # some instances need a token for public streaming

# ---- cheap-classifier-first cascade (Phase 5) ----
# VADER scores everything free/instantly; only escalate negative/ambiguous/high-stakes to Gemini.
CASCADE = os.getenv("CASCADE", "1") == "1"
VADER_POS = float(os.getenv("VADER_POS", "0.6"))   # compound >= this AND no risk keyword = fast-pass

# ---- X.com authenticated crawler (Phase 6) ----
# ToS-gray automated scraping via a logged-in browser session. Use a BURNER X account.
# Confirmed official Axis handles (verified live via follower data 2026-07-02)
X_HANDLES = ["AxisBank", "AxisBankSupport", "AxisDirect_In", "AxisMaxLifeIns"]
X_SEARCH_QUERIES = os.getenv(
    "X_SEARCH_QUERIES",
    '"Axis Bank";@AxisBank;to:AxisBank;to:AxisBankSupport;to:AxisDirect_In;to:AxisMaxLifeIns').split(";")
X_STATE_FILE = os.getenv("X_STATE_FILE", str(BASE / "fetch" / "x_state.json"))  # saved login cookies
X_SCROLLS = int(os.getenv("X_SCROLLS", "8"))       # how far to scroll each query
X_HEADLESS = os.getenv("X_HEADLESS", "1") == "1"

# ---- X historical backfill (date-windowed) ----
X_BACKFILL_QUERY = os.getenv(
    "X_BACKFILL_QUERY",
    '(@AxisBank OR @AxisBankSupport OR @AxisDirect_In OR @AxisMaxLifeIns OR "Axis Bank")')
X_BACKFILL_DAYS = int(os.getenv("X_BACKFILL_DAYS", "365"))     # how far back
X_BACKFILL_WINDOW = int(os.getenv("X_BACKFILL_WINDOW", "7"))   # days per search window
X_BACKFILL_SCROLLS = int(os.getenv("X_BACKFILL_SCROLLS", "12"))
X_BACKFILL_SLEEP = float(os.getenv("X_BACKFILL_SLEEP", "6"))   # polite pause between windows

# ---- ScrapeBadger X API (paid, ToS-clean, the preferred X source) ----
SB_QUERY = os.getenv("SB_QUERY", X_BACKFILL_QUERY)   # accounts OR-query
SB_PAGES = int(os.getenv("SB_PAGES", "5"))           # pages per fetch (100 tweets/page)
SB_QUERY_TYPE = os.getenv("SB_QUERY_TYPE", "Latest")

# ---- LLM provider (pluggable) ----
# gemini | groq | openai | openrouter | deepseek | together | cerebras | ollama
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")
LLM_MODEL = os.getenv("LLM_MODEL", "")   # override model id; else provider default below
# Automatic failover chain: when the primary provider hits its rate/daily limit (429),
# the dispatcher flips to the next configured provider. Set e.g.
#   LLM_FALLBACKS=cerebras,openrouter,gemini,ollama
# Each provider uses its own key; providers with no key are skipped. ollama = keyless local.
# Default chain uses the Gemini key you likely already have, then keyless local Ollama.
# Providers with no key / not installed are skipped automatically.
LLM_FALLBACKS = [p.strip() for p in os.getenv("LLM_FALLBACKS", "gemini,ollama").split(",") if p.strip()]
# Model for the exec brief (single big call) — a lighter model with a bigger free daily
# budget, so the brief still generates when the main classify model's daily cap is hit.
BRIEF_MODEL = os.getenv("BRIEF_MODEL", "llama-3.1-8b-instant")
# OpenAI-compatible providers: name -> (base_url, api_key_env, default_model)
# FreeLLMAPI (nandukmelath/freellmapi) aggregates 16 free providers (~1.7B tokens/mo) behind ONE
# endpoint with its own router + failover — set LLM_PROVIDER=freellmapi to route everything through it.
OPENAI_COMPAT = {
    "freellmapi": (os.getenv("FREELLM_BASE_URL", "http://localhost:3001/v1"), "FREELLM_API_KEY",
                   os.getenv("FREELLM_MODEL", "auto")),
    "groq":       ("https://api.groq.com/openai/v1",   "GROQ_API_KEY",       "llama-3.3-70b-versatile"),
    "openai":     ("https://api.openai.com/v1",        "OPENAI_API_KEY",     "gpt-4o-mini"),
    "openrouter": ("https://openrouter.ai/api/v1",     "OPENROUTER_API_KEY", "meta-llama/llama-3.3-70b-instruct"),
    "deepseek":   ("https://api.deepseek.com",         "DEEPSEEK_API_KEY",   "deepseek-chat"),
    "together":   ("https://api.together.xyz/v1",      "TOGETHER_API_KEY",   "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "cerebras":   ("https://api.cerebras.ai/v1",       "CEREBRAS_API_KEY",   "llama-3.3-70b"),
}
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
# FETCH_MULT scales every source's cap (env, default 1) — set high for a max harvest run.
_FETCH_MULT = float(os.getenv("FETCH_MULT", "1"))
FETCH_LIMITS = {k: max(v, int(v * _FETCH_MULT)) for k, v in
                {"news": 30, "play": 40, "appstore": 40, "reddit": 40, "youtube": 60, "twitter": 30,
                 "bluesky": 25, "hackernews": 30, "mastodon": 20}.items()}

def validate():
    """Startup sanity check — returns a list of human-readable config warnings (never raises)."""
    warns = []
    p = LLM_PROVIDER
    if p in OPENAI_COMPAT and not os.getenv(OPENAI_COMPAT[p][1]):
        warns.append(f"LLM_PROVIDER={p} but {OPENAI_COMPAT[p][1]} not set — LLM depth will fall back (keyless).")
    if p == "gemini" and not os.getenv("GEMINI_API_KEY"):
        warns.append("LLM_PROVIDER=gemini but GEMINI_API_KEY not set — cluster embeddings fall back to TF-IDF.")
    if not any(os.getenv(k) for k in ("SCRAPEBADGER_API_KEY", "REDDIT_CLIENT_ID", "YOUTUBE_API_KEY")):
        warns.append("No keyed sources configured — running on keyless sources only "
                     "(news/play/appstore/mastodon/hackernews).")
    return warns


# ---- warehouse / RM + CX layer ----
# Official Axis reply handles (normalised, no @) — used to detect a BANK response in a
# public thread so we can measure resolution + satisfaction. Reuses the verified X list.
AXIS_HANDLES = [h.lower() for h in X_HANDLES]
SLA_RESPONSE_HOURS = float(os.getenv("SLA_RESPONSE_HOURS", "24"))  # first-response SLA window
# Mask PII (card/PAN/Aadhaar/phone/OTP/email) before any text reaches a third-party LLM.
PII_MASK = os.getenv("PII_MASK", "1") == "1"

# ---- Apache Beam transform stage (keyless; DirectRunner local, DataflowRunner cloud) ----
BEAM_RUNNER = os.getenv("BEAM_RUNNER", "DirectRunner")
