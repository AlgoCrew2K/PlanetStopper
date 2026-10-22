"""TEST-ONLY stub helpers for tests that drive the retirement tick worker.

Cycle 2e (feature-plans/retirement-visibility.md AC-3/AC-4): the 03:45 tick
worker (app._retirement_recommender_tick_worker) no longer calls
``advisors.retirement_recommender.build_recommendations``; it calls
``build_recommendations_with_summary(source="composer")`` which returns a
``(recs, summary)`` tuple. Tests that used to patch ``build_recommendations``
patch the new entry point through ``with_summary`` below instead, keeping
each test's original intent (what the worker does with a given list of recs).

``make_summary`` mints a FRESH ``run_id`` (uuid4 hex) on every call, exactly
as the real producer does -- so the multi-night reuse tests also pin the
"run_id is excluded from the explanation-reuse snapshot" requirement for free
(a stable pair must still reuse its prior explanation even though every
night's run_id differs).

Not imported by any production module. The summary key set mirrors the AC-3
contract documented in .claude/tdd-handoff.md.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

# AC-3: the exact label the composer source stamps (spec-pinned literal).
COMPOSER_BASIS_LABEL = "realized daily returns (Composer, deposit-adjusted)"


def make_summary(**overrides) -> dict:
    """A complete AC-3 run summary with a fresh run_id on every call."""
    summary = {
        "run_id": uuid.uuid4().hex,
        "run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "composer",
        "basis_label": COMPOSER_BASIS_LABEL,
        "status": "ok",
        "error_type": None,
        "roster_size": 2,
        "symphonies_with_series": 2,
        "symphonies_without_series": [],
        "series_days_min": 250,
        "series_days_max": 250,
        "lookback_days": 250,
        "pairs_evaluated": 1,
        "pairs_screened": 0,
        "blocked_uncertainty": 0,
        "blocked_stress_too_thin": 0,
        "blocked_stress_below_threshold": 0,
        "blocked_no_candidate": 0,
        "recommendations": 0,
        "screen_threshold": 0.65,
        "stress_gate_min_aligned_days": 181,
    }
    summary.update(overrides)
    return summary


def with_summary(recs_fn: Callable[..., list]) -> Callable[..., tuple[list, dict]]:
    """Adapt a legacy ``build_recommendations``-shaped callable
    (``(**kw) -> list[dict]``) into a ``build_recommendations_with_summary``
    -shaped one (``(**kw) -> (list[dict], summary)``).

    ``summary["recommendations"]`` mirrors what the real producer reports
    (``len`` of the returned list); the worker overwrites it with the
    persisted count anyway.
    """

    def _build(**kwargs):
        recs = recs_fn(**kwargs)
        return recs, make_summary(recommendations=len(recs))

    return _build
