"""Cross-sell recommendation engine for the RM cockpit.

Rule-based (transparent + auditable — a bank can sign off on rules, not a black box).
Maps a customer's biggest pain area + the products they already hold to the next-best
product and a one-line pitch the RM can say on the call. The LLM can later polish the
phrasing, but the RECOMMENDATION itself stays rule-driven for compliance.

    product, pitch = recommend(products_held, rbi_category, recommended_team, intent)
"""

# Each rule: (predicate(held, rbi, team, intent) -> bool, (product, pitch))
_RULES = [
    (lambda held, rbi, team, intent: "credit card" not in held and rbi in ("levy_of_charges", "deposit_accounts", "not_applicable") and team in ("cards", "customer_support", "none"),
     ("Axis ACE Credit Card (Lifetime-Free)",
      "Waive the grievance forward: offer the lifetime-free ACE card — cashback offsets the fee they're upset about.")),
    (lambda held, rbi, team, intent: "personal loan" not in held and team == "payments_upi",
     ("Pre-approved Personal Loan",
      "Heavy UPI / transaction activity — pitch an instant pre-approved personal loan at the RM's discretionary rate.")),
    (lambda held, rbi, team, intent: "fixed deposit" not in held and intent in ("query", "suggestion"),
     ("Axis Fixed Deposit / Auto-Sweep",
      "Rate-shopping intent — pitch a higher-yield FD with auto-sweep so idle balance earns more.")),
    (lambda held, rbi, team, intent: "home loan" not in held and rbi == "loans_advances",
     ("Home Loan Balance Transfer",
      "Loan-active customer — offer a balance-transfer top-up at a lower rate to consolidate the relationship.")),
    (lambda held, rbi, team, intent: "insurance" not in held and "credit card" in held,
     ("Axis Max Life Term / Health Cover",
      "Card-active, no insurance — bundle a term/health plan; easy add-on with auto-debit.")),
    (lambda held, rbi, team, intent: "demat" not in held and team in ("none", "customer_support"),
     ("Axis Direct Demat + 3-in-1",
      "No investing product yet — pitch the 3-in-1 demat to deepen wallet share.")),
]

_FALLBACK = ("Relationship / portfolio review",
             "No obvious product gap — book a portfolio review, reinforce loyalty, and pre-empt churn.")


def recommend(products_held, rbi_category, recommended_team, intent):
    held = {p.strip().lower() for p in (products_held if isinstance(products_held, (set, list)) else [])} \
        if not isinstance(products_held, str) else {p.strip().lower() for p in products_held.split(",") if p.strip()}
    rbi = rbi_category or "not_applicable"
    team = recommended_team or "none"
    intent = intent or "other"
    for pred, out in _RULES:
        try:
            if pred(held, rbi, team, intent):
                return out
        except Exception:
            continue
    return _FALLBACK
