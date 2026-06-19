"""
Focused computation-path tests for AC-5: pbo_veto_status derivation.

The existing AC-5 tests in test_calibration_sweep_report.py prove that the
report layer passes pbo_veto_status through from pre-built fixture rows.
They do NOT exercise the computation at autotuner.py:3178:

    pbo_veto_status = haircut_outcome == "no_trial_cleared"

These tests call run_calibration_sweep directly with controlled seam patches
so the haircut_outcome branch is deterministic. The emitted rows' pbo_veto_status
is then asserted against the expected derived value — catching any wrong-string
comparison, accidental inversion, or constant-assignment bug.

No live API calls. No new fixture file needed: each test builds its own minimal
synthetic history (130 stub-day dict, 1 symphony) directly in the test body.
"""

import types

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORKTREE_DAYS = 130  # > _CALSWEEP_MIN_HISTORY_DAYS (125) so AC-4 skip is not triggered


def _make_stub_history(n_days: int = _WORKTREE_DAYS) -> dict:
    """Minimal history dict shaped like synthetic_history.fetch_bars output.

    Keys are YYYY-MM-DD strings; values are lists of tick dicts with a
    "return" key (the only field _collect_sim_returns accesses).  We produce
    one tick per day so the structure is valid but all returns are 0.0 —
    the Sortino objective will produce a zero or sentinel value, which is
    fine because we are patching _collect_sim_returns anyway.
    """
    from datetime import date, timedelta

    start = date(2025, 1, 1)
    ticks = [{"return": 0.0, "close": 100.0}]
    return {
        "SYM-X": {
            (start + timedelta(days=i)).isoformat(): ticks
            for i in range(n_days)
        }
    }


def _make_current_params() -> dict:
    """Minimal current_params dict sufficient for run_calibration_sweep."""
    return {
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        # Additional keys the sweep's _collect_sim_returns may look up:
        "PARABOLIC_RATCHET_PCT": 0.02,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "MAX_SQUEEZE_FLOOR": 0.05,
        "VOLATILITY_LOOKBACK": 20,
    }


def _make_mock_winner_trial(para_val: float = 1.8, vwap_val: float = 0.9) -> object:
    """Minimal mock Optuna trial with .params and .value for the 'cleared' branch.

    run_calibration_sweep accesses winner_trial.params and winner_trial.value
    (autotuner.py:3129-3130).  We use a plain types.SimpleNamespace.
    """
    return types.SimpleNamespace(
        params={
            "PARABOLIC_VELOCITY_THRESHOLD": para_val,
            "VWAP_CROSS_HWM_PCT": vwap_val,
        },
        value=0.35,  # arbitrary positive Sortino — not asserted on
    )


# ---------------------------------------------------------------------------
# AC-5 computation tests
# ---------------------------------------------------------------------------


def test_pbo_veto_status_is_true_when_no_trial_cleared_haircut_gate(monkeypatch):
    """pbo_veto_status must be True (bool) when haircut_outcome == 'no_trial_cleared'.

    Drives the real pbo_veto_status = haircut_outcome == 'no_trial_cleared'
    computation at autotuner.py:3178.  Patches:
      - _collect_sim_returns → [] (no returns, prevents Sortino from crashing
        on real data; also makes _count_triggers return 0 for both current and
        proposed, so flag_for_operator_review stays False — we don't care about
        that field here).
      - filter_sortino_sentinels → returns the single stub sentinel so the
        haircut IS entered (not the 'not_run' path).
      - _haircut_select → returns (None, None, None) so no trial clears the
        gate → haircut_outcome='no_trial_cleared' → pbo_veto_status=True.

    Tolerance note: `is True` (not just `== True`) catches any non-bool truthy
    value produced by a wrong comparison or accidental truthiness assignment.
    """
    import autotuner

    # A sentinel trial object — its content does not matter because _haircut_select
    # is patched to return (None, None, None).  It only needs to be non-empty so
    # filter_sortino_sentinels returns a non-empty list.
    _stub_trial = types.SimpleNamespace(value=0.1, user_attrs={})

    monkeypatch.setattr(autotuner, "_collect_sim_returns", lambda *a, **kw: [])
    monkeypatch.setattr(autotuner, "filter_sortino_sentinels", lambda trials: [_stub_trial])
    monkeypatch.setattr(autotuner, "_haircut_select", lambda trials, n_effective: (None, None, None))

    rows = autotuner.run_calibration_sweep(
        history_data=_make_stub_history(),
        current_params=_make_current_params(),
        current_date_str="2025-07-01",
        deviation_dict={},
        random_state=42,
    )

    assert rows, "run_calibration_sweep returned no rows — stub history too small or other skip"

    for row in rows:
        assert "pbo_veto_status" in row, f"pbo_veto_status key missing from row: {row.keys()}"
        assert row["pbo_veto_status"] is True, (
            f"Expected pbo_veto_status is True when no trial cleared the haircut gate; "
            f"got {row['pbo_veto_status']!r} (type {type(row['pbo_veto_status']).__name__}). "
            f"haircut_outcome={row.get('haircut_outcome')!r}"
        )
        assert row.get("haircut_outcome") == "no_trial_cleared", (
            f"Expected haircut_outcome='no_trial_cleared', got {row.get('haircut_outcome')!r}"
        )


def test_pbo_veto_status_is_false_when_haircut_clears_a_winner(monkeypatch):
    """pbo_veto_status must be False (bool) when haircut_outcome == 'cleared'.

    Drives the real pbo_veto_status = haircut_outcome == 'no_trial_cleared'
    computation at autotuner.py:3178.  Patches:
      - _collect_sim_returns → [] (harmless; Sortino degrades cleanly).
      - filter_sortino_sentinels → returns one stub trial so the haircut runs.
      - _haircut_select → returns (mock_winner, 0.01, 1.8) so a winner is
        found → haircut_outcome='cleared' → pbo_veto_status=False.

    Tolerance note: `is False` (not just `== False`) guards against a non-bool
    falsy value such as None or "" that would pass a loose `not row[...]` check.
    """
    import autotuner

    _stub_trial = types.SimpleNamespace(value=0.1, user_attrs={})
    _winner = _make_mock_winner_trial()

    monkeypatch.setattr(autotuner, "_collect_sim_returns", lambda *a, **kw: [])
    monkeypatch.setattr(autotuner, "filter_sortino_sentinels", lambda trials: [_stub_trial])
    monkeypatch.setattr(
        autotuner,
        "_haircut_select",
        lambda trials, n_effective: (_winner, 0.01, 1.8),
    )

    rows = autotuner.run_calibration_sweep(
        history_data=_make_stub_history(),
        current_params=_make_current_params(),
        current_date_str="2025-07-01",
        deviation_dict={},
        random_state=42,
    )

    assert rows, "run_calibration_sweep returned no rows — stub history too small or other skip"

    for row in rows:
        assert "pbo_veto_status" in row, f"pbo_veto_status key missing from row: {row.keys()}"
        assert row["pbo_veto_status"] is False, (
            f"Expected pbo_veto_status is False when a trial cleared the haircut gate; "
            f"got {row['pbo_veto_status']!r} (type {type(row['pbo_veto_status']).__name__}). "
            f"haircut_outcome={row.get('haircut_outcome')!r}"
        )
        assert row.get("haircut_outcome") == "cleared", (
            f"Expected haircut_outcome='cleared', got {row.get('haircut_outcome')!r}"
        )


def test_pbo_veto_status_is_false_when_haircut_not_run(monkeypatch):
    """pbo_veto_status must be False (bool) when haircut_outcome == 'not_run'.

    The 'not_run' branch (autotuner.py:3115) is reached when
    filter_sortino_sentinels returns an empty list (all completed trials were
    sentinels).  pbo_veto_status = haircut_outcome == 'no_trial_cleared'
    must evaluate False here — 'not_run' != 'no_trial_cleared'.

    This guards against a wrong implementation that treats any non-'cleared'
    outcome as a veto (the 'not_run' case has insufficient data to form a veto
    opinion and must NOT block the operator with a false veto signal).
    """
    import autotuner

    monkeypatch.setattr(autotuner, "_collect_sim_returns", lambda *a, **kw: [])
    # filter_sortino_sentinels returns [] → no haircut trials → 'not_run' branch
    monkeypatch.setattr(autotuner, "filter_sortino_sentinels", lambda trials: [])

    rows = autotuner.run_calibration_sweep(
        history_data=_make_stub_history(),
        current_params=_make_current_params(),
        current_date_str="2025-07-01",
        deviation_dict={},
        random_state=42,
    )

    assert rows, "run_calibration_sweep returned no rows — stub history too small or other skip"

    for row in rows:
        assert "pbo_veto_status" in row, f"pbo_veto_status key missing from row: {row.keys()}"
        assert row["pbo_veto_status"] is False, (
            f"Expected pbo_veto_status is False when haircut was not run (insufficient data); "
            f"got {row['pbo_veto_status']!r} (type {type(row['pbo_veto_status']).__name__}). "
            f"haircut_outcome={row.get('haircut_outcome')!r}"
        )
        assert row.get("haircut_outcome") == "not_run", (
            f"Expected haircut_outcome='not_run', got {row.get('haircut_outcome')!r}"
        )
