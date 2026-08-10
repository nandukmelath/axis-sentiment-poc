"""run_cycle.py's STEPS list — the exact sequence Actions runs every 2 hours.

No network, no subprocess execution: this pins the STRUCTURE (what runs, in what
order, with what flags) so a future edit cannot silently reintroduce the bug this
was built to fix — baseline and LLM-depth scoring sharing one --limit, which
capped free lexicon scoring at the same conservative number the paid LLM pass
needs, and left a growing fraction of the corpus completely unscored on any
fetch-heavy cycle. Verified live on 2026-08-11: a 594-post fetch hit the DQ
"analysis coverage >= 95%" gate at 91.5%; splitting the two phases and running
baseline uncapped took the same corpus to 100% with zero extra LLM calls.
"""
import run_cycle


def _find(phase_substr):
    for step in run_cycle.STEPS:
        if "analyze.run_analyze" in step and phase_substr in step:
            return step
    return None


def test_schema_init_runs_first():
    """A fresh Postgres needs tables before anything else queries them."""
    assert run_cycle.STEPS[0] == ["-c", "import db; db.init_db()"]


def test_fetch_runs_before_refresh():
    """Refresh re-polls posts already in raw_posts — it has nothing to do on an
    empty table, so fetch must land first."""
    fetch_i = run_cycle.STEPS.index(["-m", "fetch.run_fetch"])
    refresh_i = run_cycle.STEPS.index(["-m", "fetch.refresh"])
    assert fetch_i < refresh_i


def test_baseline_and_llm_are_separate_steps():
    """The actual bug: one shared --limit capped free VADER scoring at the same
    number the paid LLM pass needs, silently leaving posts unscored past the cap."""
    baseline = _find("baseline")
    llm = _find("llm")
    assert baseline is not None and llm is not None
    assert baseline != llm


def test_baseline_phase_is_uncapped():
    """VADER is local, instant, and free — capping it saves nothing and is what
    caused the 91.5% coverage failure. It must run with no --limit."""
    baseline = _find("baseline")
    assert "--limit" not in baseline


def test_llm_phase_stays_capped():
    """The LLM pass makes real API calls against real rate limits — removing its
    cap was never the fix and would be a cost/quota regression."""
    llm = _find("llm")
    assert "--limit" in llm


def test_baseline_runs_before_llm():
    """LLM depth only applies to negative/neutral posts under CASCADE — those
    labels come from the baseline pass, so it must go first."""
    baseline_i = run_cycle.STEPS.index(_find("baseline"))
    llm_i = run_cycle.STEPS.index(_find("llm"))
    assert baseline_i < llm_i


def test_categories_run_after_analysis():
    """issue_category is derived from the LLM's own output fields (intent,
    fraud_type, aspects) — it has nothing to read until analysis has run."""
    llm_i = run_cycle.STEPS.index(_find("llm"))
    cat_i = run_cycle.STEPS.index(["-m", "analyze.run_categories"])
    assert llm_i < cat_i
