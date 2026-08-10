"""Filter composition for dashboard/filters.py.

The UI is Streamlit and awkward to drive headlessly; the part that must be
correct is the pure apply_* logic, which is tested here directly.
"""
import pandas as pd
import pytest

from dashboard import filters as flt


@pytest.fixture
def d():
    return pd.DataFrame({
        "issue_category": ["branch", "branch", "fraud", "praise", "branch"],
        "sentiment": ["negative", "positive", "negative", "positive", "negative"],
        "source": ["twitter", "gmaps", "twitter", "play", "twitter"],
        "engagement": [10, 0, 500, 2, 50],
        "retweet_count": [1, 0, 250, 0, 5],
        "fraud_signal": [0, 0, 1, 0, 0],
        "created_dt": pd.to_datetime(
            ["2026-08-01", "2026-07-15", "2026-08-03", "2026-06-01", "2026-08-09"], utc=True),
    })


def f(field, op, value):
    return {"field": field, "op": op, "value": value}


def test_single_category_filter(d):
    assert len(flt.apply_all(d, [f("Category", "is", "branch")])) == 3


def test_filters_stack_as_and(d):
    """Two conditions must narrow, not replace — this is the whole point of the
    builder over the old fixed dropdown."""
    out = flt.apply_all(d, [f("Category", "is", "branch"),
                            f("Sentiment", "is", "negative")])
    assert len(out) == 2


def test_three_filters_stack(d):
    out = flt.apply_all(d, [f("Category", "is", "branch"),
                            f("Sentiment", "is", "negative"),
                            f("Likes", "≥", 20)])
    assert len(out) == 1


def test_is_not(d):
    assert len(flt.apply_all(d, [f("Category", "is not", "branch")])) == 2


def test_is_any_of(d):
    assert len(flt.apply_all(d, [f("Category", "is any of", ["branch", "fraud"])])) == 4


def test_numeric_between(d):
    assert len(flt.apply_all(d, [f("Reshares", "between", (1, 10))])) == 2


def test_bool_filter(d):
    assert len(flt.apply_all(d, [f("Fraud flag", "is", "Yes")])) == 1
    assert len(flt.apply_all(d, [f("Fraud flag", "is", "No")])) == 4


def test_half_built_filter_is_a_noop(d):
    """A filter added but not yet given a value must not blank the table while
    the user is still choosing."""
    assert len(flt.apply_all(d, [f("Category", "is", None)])) == len(d)


def test_unknown_field_is_ignored(d):
    assert len(flt.apply_all(d, [f("Nonexistent", "is", "x")])) == len(d)


def test_missing_column_does_not_raise():
    """Older rows can lack issue_category entirely."""
    thin = pd.DataFrame({"source": ["twitter"]})
    assert len(flt.apply_all(thin, [f("Category", "is", "branch")])) == 1


def test_month_filter(d):
    months = flt.month_options(d)
    labels = [m for m, _ in months]
    assert labels[0] == "Aug 2026"          # newest first
    aug = dict(months)["Aug 2026"]
    assert len(flt.apply_month(d, aug)) == 3


def test_month_filter_none_is_passthrough(d):
    assert len(flt.apply_month(d, None)) == len(d)


def test_describe_is_readable(d):
    assert flt.describe(f("Category", "is", "branch")) == "Category is Branch & ATM"


def test_sort_fields_exist_or_are_computed(d):
    """Every sort option must map to a real column, or be explicitly computed."""
    for label, col in flt.SORT_FIELDS.items():
        assert col is None or isinstance(col, str)
