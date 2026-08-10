"""Issue categories — the operational taxonomy the bank actually triages on.

This is a SECOND axis, orthogonal to `intent`. Intent says what the post is doing
(complaining, asking, praising). Category says what it is ABOUT, which is what
decides who picks it up:

Category is deliberately a TOPIC, not a complaint bucket. An early cut keyed
"branch_complaint" and it came out 71 positive to 35 negative — the rules match
what a post discusses, and a five-star review of a branch is not a complaint.
Encoding polarity into the category name would have mislabelled the majority of
its own bucket. "Branch complaints" is therefore category=branch plus
sentiment=negative, which the dashboard filter composes in two clicks and which
also gives you positive branch feedback for free.

    intent=complaint + category=branch_complaint   -> Branch Operations
    intent=complaint + category=technical_issue    -> App Engineering
    intent=complaint + category=response_gap       -> Social Media desk

Derived, not asked of the LLM. Three reasons:
  1. it applies retroactively to every post already scored, with no re-run and
     no token spend;
  2. every assignment is explainable — `explain()` returns the rule that fired,
     which matters when someone asks why a post was routed to Fraud Ops;
  3. it stays stable. Re-running an LLM over the same corpus reshuffles a few
     percent of labels each time, and a category that moves under you is not
     something you can build an SLA on.

The inputs are the LLM's own fields (intent, aspect, fraud_type, product) plus
the post text, so this is a deterministic rollup of model output, not a
lexicon-only guess.
"""
import json
import re

# ordered: first match wins. Order encodes triage precedence, not frequency —
# a scam report that also mentions the app is a scam report first.
CATEGORIES = [
    ("scam",            "Scam / impersonation"),
    ("fraud",           "Fraud / unauthorised debit"),
    ("employee",        "Employee & workplace"),
    ("branch",          "Branch & ATM"),
    ("response_gap",    "Response gap"),
    ("technical_issue", "Technical issue"),
    ("charges_dispute", "Charges & fees"),
    ("mis_selling",     "Mis-selling"),
    ("service_delay",   "Service delay"),
    ("market_news",     "Market & investor news"),
    ("praise",          "Praise"),
    ("other",           "Other"),
]
CATEGORY_LABEL = dict(CATEGORIES)
CATEGORY_KEYS = [k for k, _ in CATEGORIES]

# Regex over word boundaries, not substring: "scam" must not fire on "scamper",
# and — the one that actually bit — "atm" must not fire inside "format".
def _rx(*words):
    return re.compile(r"\b(" + "|".join(words) + r")\b", re.I)


SCAM_RX = _rx("scam", "scamm?er", "impersonat\\w*", "phish\\w*", "fake (call|sms|link|app|site)",
              "fraud ?call", "lottery", "kyc (update|expir\\w+) (link|sms)", "otp share")
FRAUD_RX = _rx("fraud", "unauthoris?zed", "unauthorised", "debited without", "money (stolen|deducted)",
               "cyber ?crime", "chargeback", "siphon\\w*", "hacked")
EMPLOYEE_RX = _rx("employee", "staff", "manager", "colleague", "hr", "appraisal", "salary hike",
                  "work ?life", "toxic", "resign\\w*", "onboarding", "team lead", "boss")
BRANCH_RX = _rx("branch", "atm", "cash ?deposit machine", "cdm", "passbook", "teller", "queue",
                "counter", "locker")
RESPONSE_RX = _rx("no (response|reply|revert|update|resolution)", "still waiting", "no one (called|responded)",
                  "day \\d+", "days? (now|later)", "follow ?up", "ticket .{0,12}(open|pending|unresolved)",
                  "nobody (is )?respond\\w*", "worst (service|support)")
TECH_RX = _rx("app (crash\\w*|not working|down|stuck|hang\\w*|freez\\w*)", "server (down|error|issue)",
              "not working", "error", "failed transaction", "login (issue|fail\\w*|problem)",
              "otp not (receiv\\w+|com\\w+)", "downtime", "maintenance", "glitch", "bug", "timeout")
CHARGES_RX = _rx("charge[sd]?", "fee[s]?", "penalt\\w+", "deduct\\w+ .{0,15}(charge|fee)", "amc",
                 "annual fee", "hidden (charge|cost)", "minimum balance", "mab")
MISSELL_RX = _rx("mis ?sold", "mis ?selling", "forced", "without (my )?consent", "auto ?debit\\w* insurance",
                 "sold me", "trick\\w+ (me|into)", "did ?n.?t (ask|want)")
DELAY_RX = _rx("delay\\w*", "pending", "not (yet )?(credited|disbursed|processed|received)",
               "taking (too )?long", "since \\d+ (day|week|month)", "still not")
# Broker notes and stock coverage dominated the catch-all. They are about the listed
# company, not the bank's service, and mixing them into CX sentiment skews the index.
MARKET_RX = _rx("target price", "brokerage", "buy|sell|hold rating", "q[1-4] result",
                "net profit", "nifty", "sensex", "share price", "stock[s]?", "market cap",
                "earnings", "analyst", "bse|nse", "shareholder", "dividend")

# Sources that are definitionally about one category, whatever the text says.
SOURCE_CATEGORY = {
    "ambitionbox": "employee",   # employee review site — internal channel
    "gmaps": "branch",           # reviews are pinned to a physical branch
}

# LLM aspect -> category, used when the text is too terse for a keyword to fire
# (app-store reviews are frequently three words long).
ASPECT_CATEGORY = {
    "branch_atm": "branch",
    "fraud_security": "fraud",
    "fees_charges": "charges_dispute",
    "mobile_app": "technical_issue",
    "internet_banking": "technical_issue",
}


def _aspects(row):
    """aspects_json is a JSON list of {aspect, sentiment} — tolerate every shape
    it has taken across model versions, including a bare string."""
    raw = row.get("aspects_json")
    if not raw:
        return []
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        v = [v]
    out = []
    for a in v or []:
        if isinstance(a, dict):
            a = a.get("aspect") or a.get("name")
        if isinstance(a, str):
            out.append(a)
    return out


def _g(row, key, default=""):
    """Row may be a dict or a pandas Series; NaN reads as empty."""
    v = row.get(key, default)
    if v is None or (isinstance(v, float) and v != v):
        return default
    return v


def explain(row):
    """Return (category_key, reason). Reason names the rule that fired so the
    routing can be audited rather than trusted."""
    text = str(_g(row, "text_masked") or _g(row, "text"))
    intent = str(_g(row, "intent"))
    source = str(_g(row, "source"))
    fraud_type = str(_g(row, "fraud_type"))
    aspects = _aspects(row)

    # 1. Source is definitional — an AmbitionBox review is an employee review even
    #    when it talks about the app, because the author is staff, not a customer.
    if source in SOURCE_CATEGORY:
        return SOURCE_CATEGORY[source], f"source={source}"

    # 2. Scam before fraud: scam is a subset the fraud desk handles differently
    #    (no chargeback path — it is an awareness/takedown problem).
    if fraud_type in ("impersonation", "phishing", "scam-report"):
        return "scam", f"fraud_type={fraud_type}"
    if SCAM_RX.search(text):
        return "scam", "text matches scam/impersonation"

    if intent == "fraud_report" or str(_g(row, "fraud_signal")) in ("1", "True", "true"):
        return "fraud", "intent=fraud_report or fraud_signal set"
    if FRAUD_RX.search(text):
        return "fraud", "text matches fraud/unauthorised"

    # Gated on intent: a customer venting "worst bank, my shares are stuck" is a
    # complaint that happens to say "shares", not investor coverage.
    if MARKET_RX.search(text) and intent not in ("complaint", "fraud_report",
                                                 "churn_threat", "legal_threat"):
        return "market_news", "text matches market/investor coverage"

    if EMPLOYEE_RX.search(text):
        return "employee", "text matches employee/workplace"
    if BRANCH_RX.search(text) or "branch_atm" in aspects:
        return "branch", "text or aspect matches branch/ATM"

    if RESPONSE_RX.search(text):
        return "response_gap", "text matches no-response/waiting"

    if TECH_RX.search(text):
        return "technical_issue", "text matches technical failure"
    if MISSELL_RX.search(text):
        return "mis_selling", "text matches mis-selling"
    if CHARGES_RX.search(text):
        return "charges_dispute", "text matches charges/fees"
    if DELAY_RX.search(text):
        return "service_delay", "text matches delay/pending"

    # 3. Aspect fallback for posts too short to keyword-match at all.
    for a in aspects:
        if a in ASPECT_CATEGORY:
            return ASPECT_CATEGORY[a], f"aspect={a}"

    if intent == "praise":
        return "praise", "intent=praise"
    return "other", "no rule matched"


def derive(row):
    return explain(row)[0]
