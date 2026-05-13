"""
Cycle B2 — Regression pin for the autotuner's 3-way OOS baseline-selection
decision tree (autotuner.run_autotuner, decision block @ ~lines 342-359).

This is the ONLY gate between a broken Optuna study and corrupted strategy
parameters being persisted to the state DB. The 3 reachable outcomes are:

    1. "Adopted AI"           — AI-tuned best_params win OOS (>= fallback AND >= default)
    2. "Reverted to Fallback" — AI fails OOS, fallback >= default
    3. "Reset to Global Default" — AI fails OOS AND fallback < default

Each scenario is exercised end-to-end through ``run_autotuner`` with all
non-decision dependencies mocked (Optuna study, synthetic history, DB writes,
math-engine VWAP helper). The decision branch is asserted by inspecting the
final ``database.save_symphony_strategy`` call payload AND by asserting on
the captured stdout banner that the decision block prints.

Mocking strategy
----------------
* ``optuna.create_study``               -> fake study with controlled
                                           ``best_params`` and ``best_value``;
                                           ``study.optimize`` is a no-op so
                                           ``run_simulation`` is NOT invoked
                                           during training.
* ``synthetic_history.generate_synthetic_history``
                                         -> minimal 5-day fixture (4 train / 1
                                           test after the hard-coded 0.8 split).
* ``database.load_chart_history``        -> empty (skip archive loop).
* ``database.save_chart_archive``        -> no-op.
* ``database.get_symphony_strategy``     -> known fallback params + locked vars.
* ``database.save_symphony_strategy``    -> recorded for assertions.
* ``math_engine.compute_vwap_breakdown_update``
                                         -> side_effect keyed by
                                           ``vwap_cross_hwm_pct`` kwarg, which
                                           is the marker tag we set per-param-
                                           set (AI / fallback / default) to
                                           differentiate which OOS call is in
                                           flight; returning ``is_vwap_broken``
                                           drives a trigger inside the
                                           simulator and yields a controllable
                                           non-zero alpha for that param set.

Notes
-----
* These tests touch NO live APIs, NO real Optuna storage, NO real SQLite.
* Floats are compared with ``pytest.approx`` only where unavoidable (alphas);
  the primary assertions are on the *persisted param dict identity*, which
  is the actual production risk.
"""

from __future__ import annotations

import io
import contextlib
from unittest.mock import MagicMock, patch

import pytest

# NOTE: ``autotuner`` and ``database`` are imported inside test functions
# instead of at module load time. ``autotuner`` transitively imports
# ``optuna`` and runs ``optuna.logging.set_verbosity(...)`` at import — that
# call mutates Python's root logging handlers in a way that, on Windows under
# pytest's default capture, closes one of pytest's tmpfile descriptors during
# session teardown when this module is collected BEFORE other modules that
# also write to stdout during their tests. Lazy import side-steps that
# collection-time side-effect. (Observed empirically on this branch; running
# ``pytest tests/autotuner tests/execution`` collected 0 tests until the
# autotuner import was deferred. ``pytest tests/autotuner`` alone passed.)


# ---------------------------------------------------------------------------
# Marker values used to tag each of the 3 OOS param sets so the patched
# math_engine.compute_vwap_breakdown_update side_effect can distinguish them.
# These MUST be distinct and MUST also lie in the legal Optuna search range
# for VWAP_CROSS_HWM_PCT (0.5-2.5) so that future param-validation code
# wouldn't reject them — but the autotuner does not validate, and the value
# is only used to drive the side_effect mapping below.
# ---------------------------------------------------------------------------
AI_MARKER = 0.51
FALLBACK_MARKER = 0.52
DEFAULT_MARKER = 0.53


def _build_bot_state() -> dict:
    """Single-symphony bot_state — minimum needed for run_autotuner to iterate."""
    return {
        "sym-1": {
            "name": "Test Symphony A",
            "account_uuid": "acc-1",
        }
    }


def _build_history(n_days: int = 5) -> dict:
    """
    Minimal synthetic history with ``n_days`` distinct dates so the 80/20
    split produces non-empty train AND test partitions. Each day has a single
    tick whose ``return`` is the value emitted when a trigger fires.

    The return is intentionally large enough that the
    ``guard_alpha = triggered_return - eod_return`` term inside run_simulation
    is non-trivial. With single-tick days, ``eod_return == ticks[-1].return``,
    so a triggered exit on that one tick yields ``triggered_return - return ==
    deviation_penalty`` only — meaning the alpha is dominated by the penalty
    sign and the day_max_return path.
    """
    tick = {
        "return": 2.0,
        "mc_prob": 50.0,
        "vol": 1.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 1.0,
        "valid_vwap_weight": 1.0,
    }
    dates = [f"2026-05-{d:02d}" for d in range(1, n_days + 1)]
    return {"sym-1": {d: [tick] for d in dates}}


def _patched_default_strategy() -> dict:
    """
    A DEFAULT_STRATEGY clone with VWAP_CROSS_HWM_PCT tagged with DEFAULT_MARKER
    so we can distinguish the 'default' OOS evaluation in the side_effect.
    All other keys mirror the real defaults (sourced from database.DEFAULT_STRATEGY)
    — we MUST NOT hardcode the rest, otherwise we silently drift from the
    producer's contract. See feedback_no_hardcoded_test_values.
    """
    import database  # local import — see module docstring on lazy import.

    d = database.DEFAULT_STRATEGY.copy()
    d["VWAP_CROSS_HWM_PCT"] = DEFAULT_MARKER
    return d


def _fallback_params() -> dict:
    """
    Fallback params (what get_symphony_strategy returns) — clone of the real
    defaults but tagged with FALLBACK_MARKER for differentiation.
    """
    import database  # local import — see module docstring on lazy import.

    d = database.DEFAULT_STRATEGY.copy()
    d["VWAP_CROSS_HWM_PCT"] = FALLBACK_MARKER
    return d


def _ai_best_params() -> dict:
    """
    AI-tuned best_params (what study.best_params returns). MUST include every
    Optuna-suggested key the autotuner reads (TRIGGER_THRESHOLD_PCT,
    TAKE_PROFIT_MC_PCT, VWAP_CROSS_HWM_PCT, VWAP_BLEED_MULTIPLIER,
    VWAP_BLEED_TICKS, PARABOLIC_VELOCITY_THRESHOLD, MAX_PARABOLIC_SQUEEZE) so
    that the rounding loop at autotuner.py:317 (``round(val, 2)``) does not
    raise on a missing key.
    """
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": AI_MARKER,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def _make_vwap_side_effect(broken_for_marker: set[float]):
    """
    Return a side_effect for ``math_engine.compute_vwap_breakdown_update`` that
    flags ``is_vwap_broken=True`` ONLY when the call is made with a
    ``vwap_cross_hwm_pct`` matching one of the markers in ``broken_for_marker``.

    The autotuner always invokes the helper with kwargs (autotuner.py:234-245),
    so we can read ``vwap_cross_hwm_pct`` reliably from kwargs.

    Why mark via VWAP_CROSS_HWM_PCT specifically: it is the only Optuna-tuned
    parameter that is passed verbatim (un-rounded for fallback/default, rounded
    to 2dp for AI — markers chosen with 2dp precision to survive the rounding
    at autotuner.py:317-318).
    """
    # Tolerance: post-rounding to 2 decimal places, markers stay distinct
    # (0.51, 0.52, 0.53 are 2dp-exact). Use exact equality on tag floats.
    def _side_effect(**kwargs):
        marker = kwargs.get("vwap_cross_hwm_pct")
        is_broken = any(abs(marker - m) < 1e-9 for m in broken_for_marker)
        return (0, 0, is_broken, False)

    return _side_effect


# ---------------------------------------------------------------------------
# Common patching context manager
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _autotuner_patches(best_params: dict, fallback: dict, default: dict,
                       vwap_side_effect):
    """Wire all the mocks needed for one run_autotuner invocation."""
    fake_study = MagicMock(name="fake_optuna_study")
    fake_study.best_params = best_params
    fake_study.best_value = 1.0  # benign; the decision logic ignores train alpha
    fake_study.optimize = MagicMock(return_value=None)  # no-op: skips training sim

    history = _build_history(n_days=5)

    save_calls: list[tuple] = []

    def _capture_save(symphony_name, params, locked_vars):
        # Deep copy params dict so post-call mutation of current_params doesn't
        # corrupt the captured snapshot.
        save_calls.append((symphony_name, dict(params), list(locked_vars)))

    with patch("autotuner.optuna.create_study", return_value=fake_study), \
         patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()), \
         patch("autotuner.synthetic_history.generate_synthetic_history",
               return_value=history), \
         patch("autotuner.database.load_chart_history", return_value={}), \
         patch("autotuner.database.save_chart_archive"), \
         patch("autotuner.database.get_symphony_strategy",
               return_value={"params": fallback.copy(), "locked_vars": []}), \
         patch("autotuner.database.save_symphony_strategy",
               side_effect=_capture_save) as mock_save, \
         patch("autotuner.database.DEFAULT_STRATEGY", default), \
         patch("autotuner.math_engine.compute_vwap_breakdown_update",
               side_effect=vwap_side_effect):
        yield {
            "save_calls": save_calls,
            "mock_save": mock_save,
            "fake_study": fake_study,
        }


# ---------------------------------------------------------------------------
# Helper: invoke run_autotuner with captured stdout
# ---------------------------------------------------------------------------


def _run_and_capture(best_params, fallback, default, vwap_side_effect):
    import autotuner  # local import — see module docstring on lazy import.

    bot_state = _build_bot_state()
    buf = io.StringIO()
    with _autotuner_patches(best_params, fallback, default, vwap_side_effect) as ctx, \
         contextlib.redirect_stdout(buf):
        autotuner.run_autotuner(bot_state, "2026-05-10", ["acc-1"])
    return buf.getvalue(), ctx


# ===========================================================================
# Scenario 1 — AI wins OOS  ->  "Adopted AI"
# ===========================================================================


def test_oos_ai_beats_fallback_and_default_persists_ai_best_params():
    """
    Decision-tree semantics (verified against autotuner.py:342-349):
      run_simulation returns ``-total_guard_alpha``; the autotuner then NEGATES
      again at the call sites (``oos_alpha = -run_simulation(...)``). So the
      caller-side alpha equals ``total_guard_alpha`` — and triggering a VWAP
      break produces a NEGATIVE total_guard_alpha (penalty subtracted; see
      autotuner.py:266-279). A non-triggering simulation returns 0.

    Therefore, for AI to WIN we want AI to NOT trigger (alpha=0) while
    fallback AND default DO trigger (negative alphas). 0 > negative_X for
    both comparators -> "Adopted AI" branch.
    """
    ai = _ai_best_params()
    fallback = _fallback_params()
    default = _patched_default_strategy()
    # AI does NOT trigger; fallback and default DO trigger -> AI wins.
    side_effect = _make_vwap_side_effect(
        broken_for_marker={FALLBACK_MARKER, DEFAULT_MARKER}
    )

    stdout, ctx = _run_and_capture(ai, fallback, default, side_effect)

    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1, (
        f"Expected exactly one save_symphony_strategy call; got {len(save_calls)}"
    )

    _name, persisted_params, _locked = save_calls[0]

    # The AI's VWAP_CROSS_HWM_PCT marker must be present (post round-to-2dp).
    # AI_MARKER (0.51) is 2dp-exact, so rounding is a no-op.
    assert persisted_params["VWAP_CROSS_HWM_PCT"] == pytest.approx(
        AI_MARKER, abs=1e-9
    ), (
        "AI VWAP marker must survive into persisted params — proves the "
        "'Adopted AI' branch executed (autotuner.py:347-349)."
    )

    # Stdout must announce the AI-adopted decision banner. The autotuner
    # prints either 'OOS validation passed!' or 'OOS validation passed (Beat
    # Baselines)!' depending on whether alpha > 0 — both indicate AI adoption.
    assert "OOS validation passed" in stdout, (
        f"Expected 'Adopted AI' banner in stdout; got:\n{stdout}"
    )

    # Negative assertions: must NOT have logged the fallback or default banner.
    assert "Reverting to Fallback" not in stdout
    assert "Resetting to Global Default" not in stdout


# ===========================================================================
# Scenario 2 — Fallback wins  ->  "Reverted to Fallback"
# ===========================================================================


def test_oos_ai_fails_fallback_beats_default_persists_fallback_params():
    """
    AI sees no trigger (alpha 0), fallback triggers (positive penalty-driven
    alpha), default sees no trigger (alpha 0). With the penalty regime making
    fallback's alpha strictly LESS than zero (penalty is negative)... wait —
    we need fallback to BEAT default. Triggering produces a penalty-laden
    alpha that is negative; non-triggering yields alpha 0. So a NON-trigger
    actually scores higher (0) than a trigger (negative).

    Therefore to make FALLBACK win we make AI and DEFAULT trigger (both go
    negative) and FALLBACK NOT trigger (stays at 0).
    """
    ai = _ai_best_params()
    fallback = _fallback_params()
    default = _patched_default_strategy()
    side_effect = _make_vwap_side_effect(
        broken_for_marker={AI_MARKER, DEFAULT_MARKER}
    )

    stdout, ctx = _run_and_capture(ai, fallback, default, side_effect)

    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1
    _name, persisted_params, _locked = save_calls[0]

    # Fallback's VWAP marker must be the one persisted.
    assert persisted_params["VWAP_CROSS_HWM_PCT"] == pytest.approx(
        FALLBACK_MARKER, abs=1e-9
    ), (
        "Fallback VWAP marker must be persisted — proves the 'Reverted to "
        "Fallback' branch executed (autotuner.py:351-354)."
    )

    assert "Reverting to Fallback" in stdout, (
        f"Expected fallback-revert banner in stdout; got:\n{stdout}"
    )
    assert "Resetting to Global Default" not in stdout


# ===========================================================================
# Scenario 3 — Default wins  ->  "Reset to Global Default"
# ===========================================================================


def test_oos_ai_and_fallback_both_fail_persists_global_default_params():
    """
    AI and fallback both trigger -> both alphas go negative.
    Default does NOT trigger -> alpha 0 (best).
    Decision branch should be 'Reset to Global Default'.
    """
    ai = _ai_best_params()
    fallback = _fallback_params()
    default = _patched_default_strategy()
    side_effect = _make_vwap_side_effect(
        broken_for_marker={AI_MARKER, FALLBACK_MARKER}
    )

    stdout, ctx = _run_and_capture(ai, fallback, default, side_effect)

    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1
    _name, persisted_params, _locked = save_calls[0]

    # Default's VWAP marker must be the one persisted.
    assert persisted_params["VWAP_CROSS_HWM_PCT"] == pytest.approx(
        DEFAULT_MARKER, abs=1e-9
    ), (
        "Default VWAP marker must be persisted — proves the 'Reset to "
        "Global Default' branch executed (autotuner.py:355-359)."
    )

    assert "Resetting to Global Default" in stdout, (
        f"Expected global-default reset banner in stdout; got:\n{stdout}"
    )
    assert "Reverting to Fallback" not in stdout


# ===========================================================================
# Cross-cutting structural invariants — these guard the decision contract
# itself, independent of which branch is taken.
# ===========================================================================


@pytest.mark.parametrize(
    "broken_markers, expected_marker_persisted",
    [
        # AI wins: AI does NOT trigger, fallback & default DO trigger.
        ({FALLBACK_MARKER, DEFAULT_MARKER}, AI_MARKER),
        # Fallback wins: fallback does NOT trigger, AI & default DO.
        ({AI_MARKER, DEFAULT_MARKER}, FALLBACK_MARKER),
        # Default wins: default does NOT trigger, AI & fallback DO.
        ({AI_MARKER, FALLBACK_MARKER}, DEFAULT_MARKER),
    ],
)
def test_run_autotuner_persists_exactly_one_strategy_per_symphony(
    broken_markers, expected_marker_persisted
):
    """
    Regardless of which decision branch wins, ``save_symphony_strategy`` must
    be called EXACTLY ONCE per symphony, and the persisted params dict must
    carry the marker for the winning param set. This invariant guards against
    a regression where (e.g.) the engine writes twice, writes the wrong set,
    or silently no-ops on tie-breaking.
    """
    ai = _ai_best_params()
    fallback = _fallback_params()
    default = _patched_default_strategy()
    side_effect = _make_vwap_side_effect(broken_for_marker=broken_markers)

    _stdout, ctx = _run_and_capture(ai, fallback, default, side_effect)

    assert len(ctx["save_calls"]) == 1
    _name, persisted_params, _locked = ctx["save_calls"][0]
    assert persisted_params["VWAP_CROSS_HWM_PCT"] == pytest.approx(
        expected_marker_persisted, abs=1e-9
    )


def test_run_autotuner_normalizes_symphony_name_when_persisting():
    """
    The persisted symphony_name must be the *normalized* form
    (``database.normalize_name``) — not the raw bot_state name. This is the
    primary key for the symphony_strategies table; a raw vs normalized
    mismatch would corrupt cross-cycle lookups.
    """
    ai = _ai_best_params()
    fallback = _fallback_params()
    default = _patched_default_strategy()
    side_effect = _make_vwap_side_effect(broken_for_marker={AI_MARKER})

    _stdout, ctx = _run_and_capture(ai, fallback, default, side_effect)

    import database  # local import — see module docstring on lazy import.

    name_arg, _params, _locked = ctx["save_calls"][0]
    assert name_arg == database.normalize_name("Test Symphony A")


def test_run_autotuner_aborts_cleanly_when_synthetic_history_empty():
    """
    If ``generate_synthetic_history`` returns a falsy result, the autotuner
    must abort BEFORE invoking the decision branch and BEFORE persisting any
    strategy. Otherwise a transient data-fetch failure would silently corrupt
    state with default/fallback params.
    """
    fake_study = MagicMock()
    fake_study.best_params = _ai_best_params()
    fake_study.best_value = 1.0

    captured = []

    with patch("autotuner.optuna.create_study", return_value=fake_study), \
         patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()), \
         patch("autotuner.synthetic_history.generate_synthetic_history",
               return_value={}), \
         patch("autotuner.database.load_chart_history", return_value={}), \
         patch("autotuner.database.save_chart_archive"), \
         patch("autotuner.database.save_symphony_strategy",
               side_effect=lambda *a, **kw: captured.append(a)):
        import autotuner  # local import — see module docstring on lazy import.
        result = autotuner.run_autotuner(_build_bot_state(), "2026-05-10", ["acc-1"])

    assert result is None, "Expected early-return None on empty history"
    assert captured == [], (
        "save_symphony_strategy MUST NOT be called when history fetch fails"
    )
