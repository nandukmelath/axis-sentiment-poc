# Axis Social Intelligence — from listening to *action*

**Audience:** Kasim / Arpan (build), Ranjit (sponsor).
**One line:** we already turn public social chatter about Axis into a decision-grade,
per-post record. This layer joins it to the customer, tracks whether Axis's *response*
actually fixed the problem, and hands the RM a pain point + a cross-sell line before the call.

A sentiment score is trivia. A **routed, resolved, revenue-linked signal** is what a bank buys.

---

## The two business outcomes

### 1. RM enablement (revenue / cross-sell)
Before an RM calls a customer, the **RM Cockpit** shows:
- the customer's current sentiment + trend,
- their top **pain point** (the exact unresolved issue, with evidence),
- a **next-best cross-sell** with a one-line pitch (rule-based, auditable), and
- a ready **talking point**: *"Lead with the UPI issue; once acknowledged, pitch the pre-approved personal loan."*

The RM walks in informed, resolves the grievance first, and pitches the right product —
higher cross-sell conversion, lower churn.

### 2. Customer-service resolution loop (CX / ops)
When a customer complains publicly and **@AxisBankSupport replies**, we reconstruct the
thread and answer the questions that matter:
- Did Axis respond, and how fast (SLA)?
- Was it **resolved**? Was the customer **satisfied** (from their follow-up)?
- Did their sentiment **recover**?

This is a separate fact table (`fact_interaction`) feeding admin analytics: **follow-up
bifurcation** (pending / in-progress / resolved / unresolved) by RBI category and owning team.

---

## The north-star metric

> **Sentiment Recovery Rate** — of the complaints Axis responded to, the % where the
> customer's sentiment recovered to neutral/positive, plus median time-to-recovery.

It is the one number that proves the whole machine creates value:
**complaint → routed → acted → sentiment recovered.** Everything else is an input to it.

*(On the current dataset the live dashboard shows a 25% recovery rate on 960 mentions with
a ~19h median response — illustrative of the metric, not a benchmark.)*

---

## The data foundation (why the data team will be comfortable)

- **PII masked before the cloud.** Card numbers (Luhn-checked), PAN, Aadhaar, phone, OTP
  and email are redacted *before* any text reaches a third-party LLM. Raw text stays in the
  access-controlled bronze layer. On-prem (Ollama/Llama-3) is a config flip for full RBI
  in-country residency — **DPDP / RBI-residency safe by design.**
- **SCD Type 2 history.** The author dimension keeps versioned history per handle, so we can
  answer *"what was this person's influence/segment **at the time** they complained"* — a
  200-follower account that later became a 50k journalist is triaged on who they were then.
- **Kimball star schema.** Conformed dims (team, source, category, date), fact + marts,
  incremental + testable. Local demo runs on SQLite; prod is dbt-core snapshots/tests on
  Supabase — the shape the data team already reviews.

---

## What does it cost to run?

The cost driver is the LLM classification. Two levers keep it near-zero:

1. **Cascade** — a free local model (VADER) scores *every* post instantly; only the
   negative/ambiguous ones (the ones a bank must act on, ~half) escalate to the LLM.
2. **Batching** — 12 posts per LLM call.

**Estimation formula (plug in Axis volume):**
```
LLM calls / day ≈ (mentions/day × 0.5) / 12
tokens / call   ≈ 3,000 in  +  2,200 out   (schema prompt + 12 posts, structured output)
cost / day      ≈ calls × (in_tokens × price_in  +  out_tokens × price_out)
```
*(Confirm `price_in` / `price_out` against the chosen provider's current pricing page before quoting.)*

**Cost bands:**
| Volume | Option | Cost |
|---|---|---|
| Pilot (hundreds–few thousand/day) | Gemini Flash / Groq **free tier** | **≈ $0** (rate-limited) |
| Bank scale (tens of thousands/day) | paid Flash-class API | low tens of $/day |
| Any volume, max residency | **on-prem Ollama** | **$0 marginal** (fixed GPU) + RBI-safe |

Headline: **at pilot volume it runs at ≈ $0** (cascade + free tier / on-prem); at full scale
it's a small, predictable per-mention cost with the on-prem escape hatch always available.

---

## Ask

Green-light a **2-week pilot** on live Axis data: point `DATABASE_URL` at a Supabase
instance, connect a read-only CRM extract for the handle→customer bridge, and run the
Airflow DAG on a schedule. Deliverable: the RM Cockpit + Admin Analytics dashboards live,
and a first Sentiment Recovery Rate baseline for one product line.
