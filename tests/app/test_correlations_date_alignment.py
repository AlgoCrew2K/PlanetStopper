"""
RED — Correlations panel must align per-symphony returns by calendar date,
not by list position (DE-CORR-DATE-ALIGN-001).

Bug: app.py's /ai-advisor route assembly (app.py:5711-5716) builds
`_series_dict[_sym_id] = _live_rets` from `analytics.compute_per_symphony_
returns`, discarding the parallel `_dates` list it also returns. The result
is a plain `list[float]` per symphony, handed straight to
`advisors.correlation_diagnostic.compute_pairwise_correlations`.

`compute_pairwise_correlations`'s `_extract_aligned_pairs` helper aligns
list-shaped input by raw index position and dict-shaped (date-keyed) input
by shared date-key intersection (advisors/correlation_diagnostic.py:86-138 —
this contract is correct and unchanged by this fix). Per-symphony return
series are sparse and event-driven (a symphony only has a row on a date it
triggered an exit — reporting.py's `if sym.get("triggered")` gate), so two
symphonies' trigger-day calendars are largely unrelated. Passing plain lists
therefore pairs symphony A's Nth trigger-day return with symphony B's Nth
trigger-day return regardless of whether those are the same calendar date —
the panel silently correlates returns from unrelated dates.

These tests exercise the REAL assembly + REAL correlation_diagnostic (no
mocking of compute_per_symphony_returns, list_available_symphonies, or
compute_pairwise_correlations) — only the I/O boundary
(analytics.get_history_with_cache_invalidation) and an unrelated DB call
(database.get_advisor_observations_for_role) are mocked, matching the
proven-safe minimal mock set already used by tests/app/test_correlations_
tab.py for the /ai-advisor route.

Consumer-suite sweep (no updates needed elsewhere):
  - tests/app/test_correlations_tab.py fully mocks
    compute_pairwise_correlations via sys.modules stub injection
    (_inject_correlation_diagnostic_stub); it supplies canned PairResult-like
    objects directly and never exercises the assembly or alignment logic —
    none of its assertions touch n_obs/window/position-vs-date behavior.
  - tests/ai_advisor/test_correlation_diagnostic_math.py and
    test_correlation_diagnostic_guards.py unit-test correlation_diagnostic.py
    directly (both list-input and date-keyed-input paths, including the
    existing test_window_is_none_for_plain_list_series /
    test_window_is_populated_for_date_keyed_series pins) — that module is
    unchanged by this fix (the defect is purely app.py's assembly), so these
    stay green as-is.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app as app_module
from advisors import correlation_diagnostic

# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


def _history_from_series(
    symphony_dates: dict[str, list[str]],
    symphony_rets: dict[str, list[float]],
) -> dict:
    """Build a `_history`-shaped dict matching analytics.load_post_mortem_
    history's documented output shape:

        {date_str: {symphony_id: {"live_ret": float, "f_ret": float}}}

    This is exactly the shape analytics.compute_per_symphony_returns and
    analytics.list_available_symphonies consume (schema-derived from
    analytics.py:121-199, 207-214, 305-340) — the same internal shape the
    route obtains from analytics.get_history_with_cache_invalidation, which
    is the only call mocked here.

    f_ret is set equal to live_ret; compute_per_symphony_returns requires
    both keys present (skips a day otherwise) but only live_ret feeds the
    correlation assembly under test, so its value is irrelevant here.
    """
    history: dict = {}
    for sym_id, dates in symphony_dates.items():
        rets = symphony_rets[sym_id]
        assert len(dates) == len(rets), f"{sym_id}: dates/rets length mismatch in fixture"
        for date_str, ret in zip(dates, rets):
            history.setdefault(date_str, {})[sym_id] = {"live_ret": ret, "f_ret": ret}
    return history


def _find_pair(matrix: list, sym_a: str, sym_b: str):
    """Find the (sym_a, sym_b) PairResult in a correlation_matrix, order-independent."""
    return next((e for e in matrix if {e.sym_a, e.sym_b} == {sym_a, sym_b}), None)


@pytest.fixture
def test_client():
    """Flask test client with testing mode enabled."""
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


# ---------------------------------------------------------------------------
# RED-1: date-alignment vs position-alignment — the bug pin
# ---------------------------------------------------------------------------


def test_correlations_route_aligns_pairs_by_calendar_date_not_position(test_client):
    """The /ai-advisor Correlations panel must align two symphonies' return
    series by shared calendar date, not by raw list position.

    Fixture: AlphaSym and BetaSym trigger on overlapping-but-offset 5-day
    calendars, sharing exactly 3 real dates (2026-01-07/08/09). AlphaSym's
    values on the shared dates are its chronologically LAST 3 entries;
    BetaSym's are its chronologically FIRST 3 entries — so position-based
    alignment (index 0..4 of each list, blindly paired) pairs values from
    entirely different real dates, while date-based alignment correctly
    isolates just the 3 shared-date pairs.

    Expected values are computed by calling the REAL
    correlation_diagnostic.compute_pairwise_correlations directly against a
    date-keyed dict built from the same fixture values — never a hardcoded
    literal. The buggy-today reference is computed the same way against the
    plain-list form, to prove the fixture makes date- vs position-alignment
    diverge structurally (n_obs and window), not by numeric coincidence.
    """
    alpha_dates = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    alpha_rets = [100.0, -100.0, 3.0, 4.0, 5.0]
    beta_dates = ["2026-01-07", "2026-01-08", "2026-01-09", "2026-01-10", "2026-01-11"]
    beta_rets = [30.0, 40.0, 50.0, 15.0, -5.0]

    history = _history_from_series(
        {"AlphaSym": alpha_dates, "BetaSym": beta_dates},
        {"AlphaSym": alpha_rets, "BetaSym": beta_rets},
    )

    # Correct reference: the real diagnostic against date-keyed series.
    alpha_date_keyed = dict(zip(alpha_dates, alpha_rets))
    beta_date_keyed = dict(zip(beta_dates, beta_rets))
    expected = correlation_diagnostic.compute_pairwise_correlations(
        {"AlphaSym": alpha_date_keyed, "BetaSym": beta_date_keyed}
    )[0]

    # What today's buggy position-alignment produces on the identical values —
    # documents the bug independent of the route.
    buggy_ref = correlation_diagnostic.compute_pairwise_correlations(
        {"AlphaSym": alpha_rets, "BetaSym": beta_rets}
    )[0]

    # Fixture sanity: date- vs position-alignment must diverge structurally
    # (guaranteed by construction — 3 shared dates vs 5 raw positions, and
    # window is None for any list-input per _extract_aligned_pairs's
    # documented contract), so the test below cannot pass vacuously.
    assert expected.n_obs != buggy_ref.n_obs, (
        "fixture bug: expected date-aligned n_obs must differ from the "
        "position-aligned n_obs for this test to be non-vacuous"
    )
    assert expected.window != buggy_ref.window, (
        "fixture bug: expected date-aligned window must differ from the "
        "position-aligned window (always None) for this test to be non-vacuous"
    )

    captured: dict = {}

    def _capture_render(template, **ctx):
        captured.update(ctx)
        return "<html></html>"

    with (
        patch.object(app_module, "render_template", side_effect=_capture_render),
        patch("database.get_advisor_observations_for_role", return_value=[]),
        patch("analytics.get_history_with_cache_invalidation", return_value=history),
    ):
        resp = test_client.get("/ai-advisor")

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"

    matrix = captured.get("correlation_matrix", [])
    entry = _find_pair(matrix, "AlphaSym", "BetaSym")
    assert entry is not None, (
        f"expected an AlphaSym/BetaSym pair in correlation_matrix; got {matrix!r}"
    )

    assert entry.n_obs == expected.n_obs, (
        f"route must align by calendar date (n_obs={expected.n_obs}, the true "
        f"shared-trigger-day count) not by list position "
        f"(n_obs={buggy_ref.n_obs}, every position blindly paired); "
        f"got n_obs={entry.n_obs}"
    )
    assert entry.window == expected.window, (
        f"route must pass date-keyed series so window reflects the real "
        f"calendar overlap ({expected.window}); list-input always yields "
        f"window=None per _extract_aligned_pairs's documented contract; "
        f"got window={entry.window}"
    )
    # Same deterministic two-pass Pearson formula, re-run on the same finite
    # floats the route should be assembling internally post-fix — the two
    # computations should match to full float precision. abs=1e-9 only
    # absorbs summation-order float noise, not a tolerance for a different
    # formula or a different set of input observations.
    assert entry.correlation == pytest.approx(expected.correlation, abs=1e-9), (
        f"expected date-aligned correlation {expected.correlation}, got {entry.correlation}"
    )


# ---------------------------------------------------------------------------
# RED-2: fully-overlapping calendars — regression pin
# ---------------------------------------------------------------------------


def test_correlations_route_fully_overlapping_dates_still_correlate_correctly(test_client):
    """When two symphonies trigger on the EXACT SAME calendar dates, the
    Correlations panel must still produce the correct date-aligned result
    (n_obs, window, correlation) after the date-alignment fix — the fix must
    not break the trivially-aligned case.

    This is RED today too, not only after a regression: list-input always
    yields window=None (per _extract_aligned_pairs's contract) regardless of
    how much the underlying calendars actually overlap, so today's route
    fails the window assertion even though n_obs/correlation happen to
    coincidentally match in this fully-overlapping case. Pinning window here
    too guards against the fix introducing an off-by-one in date sorting.
    """
    gamma_dates = ["2026-02-01", "2026-02-02", "2026-02-03", "2026-02-04"]
    gamma_rets = [1.0, 2.0, 3.0, 4.0]
    delta_dates = ["2026-02-01", "2026-02-02", "2026-02-03", "2026-02-04"]
    delta_rets = [2.0, 4.0, 6.0, 8.0]

    history = _history_from_series(
        {"GammaSym": gamma_dates, "DeltaSym": delta_dates},
        {"GammaSym": gamma_rets, "DeltaSym": delta_rets},
    )

    gamma_date_keyed = dict(zip(gamma_dates, gamma_rets))
    delta_date_keyed = dict(zip(delta_dates, delta_rets))
    expected = correlation_diagnostic.compute_pairwise_correlations(
        {"GammaSym": gamma_date_keyed, "DeltaSym": delta_date_keyed}
    )[0]

    captured: dict = {}

    def _capture_render(template, **ctx):
        captured.update(ctx)
        return "<html></html>"

    with (
        patch.object(app_module, "render_template", side_effect=_capture_render),
        patch("database.get_advisor_observations_for_role", return_value=[]),
        patch("analytics.get_history_with_cache_invalidation", return_value=history),
    ):
        resp = test_client.get("/ai-advisor")

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"

    matrix = captured.get("correlation_matrix", [])
    entry = _find_pair(matrix, "GammaSym", "DeltaSym")
    assert entry is not None, (
        f"expected a GammaSym/DeltaSym pair in correlation_matrix; got {matrix!r}"
    )

    assert entry.n_obs == expected.n_obs, (
        f"fully-overlapping calendars must yield n_obs={expected.n_obs}; got n_obs={entry.n_obs}"
    )
    assert entry.window == expected.window, (
        f"fully-overlapping calendars must yield window={expected.window} "
        f"(date-keyed alignment); got window={entry.window}"
    )
    assert entry.correlation == pytest.approx(expected.correlation, abs=1e-9), (
        f"expected correlation {expected.correlation}, got {entry.correlation}"
    )
