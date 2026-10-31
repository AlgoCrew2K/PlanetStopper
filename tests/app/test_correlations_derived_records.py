"""The committed derived run summaries differ from the real captured row only where
``tests/fixtures/retirement/derived/PROVENANCE.md`` says they do (suite fixture rule 4)."""

from __future__ import annotations

import json

import pytest

from tests.advisors import _series_captures as captures
from tests.app._correlations_view_support import DERIVED_DIR, load_derived_summary

_ZERO_COUNTS = {
    "pairs_evaluated": 0,
    "pairs_screened": 0,
    "blocked_uncertainty": 0,
    "blocked_stress_too_thin": 0,
    "blocked_stress_below_threshold": 0,
    "blocked_no_candidate": 0,
}
_NULL_COUNTS = {key: None for key in _ZERO_COUNTS}

_CHANGES = {
    "summary_series_unavailable.json": {
        "status": "series_unavailable",
        "symphonies_with_series": 1,
        "series_days_min": 250,
        "series_days_max": 250,
        **_ZERO_COUNTS,
    },
    "summary_insufficient_symphonies.json": {
        "status": "insufficient_symphonies",
        "roster_size": 1,
        "symphonies_with_series": 0,
        "symphonies_without_series": [],
        "series_days_min": None,
        "series_days_max": None,
        **_ZERO_COUNTS,
    },
    "summary_internal_error.json": {
        "status": "internal_error",
        "error_type": "TimeoutError",
        "roster_size": None,
        "symphonies_with_series": None,
        "symphonies_without_series": [],
        "series_days_min": None,
        "series_days_max": None,
        **_NULL_COUNTS,
    },
}


@pytest.mark.parametrize("name", sorted(_CHANGES))
def test_a_derived_summary_is_the_real_row_with_only_the_listed_fields_changed(name):
    real = captures.load_captured_summary_row()
    changes = _CHANGES[name]
    assert set(changes) <= set(real), "a change names a field the real row does not have"
    assert load_derived_summary(name) == {**real, **changes}


def test_every_committed_derived_summary_is_accounted_for():
    on_disk = sorted(p.name for p in DERIVED_DIR.glob("summary_*.json"))
    assert on_disk == sorted(_CHANGES)


def test_every_derived_summary_is_strict_json():
    for name in _CHANGES:
        text = (DERIVED_DIR / name).read_text(encoding="utf-8")
        json.loads(text, parse_constant=lambda token: pytest.fail(f"non-strict constant {token}"))
