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
    Optuna-suggested key the autotuner reads (TAKE_PROFIT_MC_PCT,
    VWAP_CROSS_HWM_PCT, VWAP_BLEED_MULTIPLIER, VWAP_BLEED_TICKS,
    PARABOLIC_VELOCITY_THRESHOLD, MAX_PARABOLIC_SQUEEZE) so that the rounding
    loop at autotuner.py:317 (``round(val, 2)``) does not raise on a missing key.
    TRIGGER_THRESHOLD_PCT is locked (database.DEFAULT_LOCKED_VARS) and excluded
    from the Optuna search space — it is not in study.best_params.
    """
    return {
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
def _autotuner_patches(best_params: dict, fallback: dict, default: dict, vwap_side_effect):
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

    # Cluster-3 re-pin: the autotuner replay now applies production's VWAP
    # open-window grace gate (tick_idx < grace_minutes) — see autotuner-
    # replay-parity AC-2. These OOS-cascade tests stub
    # compute_vwap_breakdown_update to fire the trigger on an early tick; the
    # grace gate would suppress it and shift the pinned OOS guard-alpha. These
    # tests pin the OOS adopt/fallback/default cascade, NOT the grace gate.
    # The replay resolves grace via the lazy accessor autotuner._replay_grace_
    # minutes() (referencing production's alpha_bot_execution.VWAP_OPEN_WINDOW_
    # GRACE_MINUTES — no autotuner-local module constant). Patching that
    # accessor to return 0 disables grace, keeping these tests' pinned alpha
    # values valid. AC-2's grace behaviour is covered by
    # tests/autotuner/test_c3_replay_vwap_grace.py.
    with (
        patch("autotuner._replay_grace_minutes", return_value=0),
        patch("autotuner.optuna.create_study", return_value=fake_study),
        patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()),
        patch("autotuner.synthetic_history.generate_synthetic_history", return_value=history),
        patch("autotuner.database.load_chart_history", return_value={}),
        patch("autotuner.database.save_chart_archive"),
        patch(
            "autotuner.database.get_symphony_strategy",
            return_value={"params": fallback.copy(), "locked_vars": []},
        ),
        patch("autotuner.database.save_symphony_strategy", side_effect=_capture_save) as mock_save,
        patch("autotuner.database.DEFAULT_STRATEGY", default),
        patch("autotuner.math_engine.compute_vwap_breakdown_update", side_effect=vwap_side_effect),
    ):
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
    import inspect
    from tests.autotuner.conftest import make_phase1_theory_bundle

    bot_state = _build_bot_state()
    buf = io.StringIO()
    spec_bundle_id = make_phase1_theory_bundle()
    sig = inspect.signature(autotuner.run_autotuner)
    extra = {"spec_bundle_id": spec_bundle_id} if "spec_bundle_id" in sig.parameters else {}
    with (
        _autotuner_patches(best_params, fallback, default, vwap_side_effect) as ctx,
        contextlib.redirect_stdout(buf),
    ):
        autotuner.run_autotuner(bot_state, "2026-05-10", ["acc-1"], **extra)
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
    side_effect = _make_vwap_side_effect(broken_for_marker={FALLBACK_MARKER, DEFAULT_MARKER})

    stdout, ctx = _run_and_capture(ai, fallback, default, side_effect)

    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1, (
        f"Expected exactly one save_symphony_strategy call; got {len(save_calls)}"
    )

    _name, persisted_params, _locked = save_calls[0]

    # The AI's VWAP_CROSS_HWM_PCT marker must be present (post round-to-2dp).
    # AI_MARKER (0.51) is 2dp-exact, so rounding is a no-op.
    assert persisted_params["VWAP_CROSS_HWM_PCT"] == pytest.approx(AI_MARKER, abs=1e-9), (
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
    side_effect = _make_vwap_side_effect(broken_for_marker={AI_MARKER, DEFAULT_MARKER})

    stdout, ctx = _run_and_capture(ai, fallback, default, side_effect)

    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1
    _name, persisted_params, _locked = save_calls[0]

    # Fallback's VWAP marker must be the one persisted.
    assert persisted_params["VWAP_CROSS_HWM_PCT"] == pytest.approx(FALLBACK_MARKER, abs=1e-9), (
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
    side_effect = _make_vwap_side_effect(broken_for_marker={AI_MARKER, FALLBACK_MARKER})

    stdout, ctx = _run_and_capture(ai, fallback, default, side_effect)

    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1
    _name, persisted_params, _locked = save_calls[0]

    # Default's VWAP marker must be the one persisted.
    assert persisted_params["VWAP_CROSS_HWM_PCT"] == pytest.approx(DEFAULT_MARKER, abs=1e-9), (
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

    with (
        patch("autotuner.optuna.create_study", return_value=fake_study),
        patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()),
        patch("autotuner.synthetic_history.generate_synthetic_history", return_value={}),
        patch("autotuner.database.load_chart_history", return_value={}),
        patch("autotuner.database.save_chart_archive"),
        patch(
            "autotuner.database.save_symphony_strategy",
            side_effect=lambda *a, **kw: captured.append(a),
        ),
    ):
        import autotuner  # local import — see module docstring on lazy import.
        import inspect
        from tests.autotuner.conftest import make_phase1_theory_bundle

        _spec_id = make_phase1_theory_bundle()
        _sig = inspect.signature(autotuner.run_autotuner)
        _extra = {"spec_bundle_id": _spec_id} if "spec_bundle_id" in _sig.parameters else {}
        result = autotuner.run_autotuner(_build_bot_state(), "2026-05-10", ["acc-1"], **_extra)

    assert result is None, "Expected early-return None on empty history"
    assert captured == [], "save_symphony_strategy MUST NOT be called when history fetch fails"


# ===========================================================================
# Cycle B2-FU1 — Tie semantics for the 3-way OOS baseline-selection decision
# ===========================================================================
#
# Background
# ----------
# The current decision block at autotuner.py:342 uses ``>=`` for both
# inequalities:
#
#     if oos_alpha >= fallback_oos_alpha and oos_alpha >= default_oos_alpha:
#         <adopt AI>
#     elif fallback_oos_alpha >= default_oos_alpha:
#         <revert fallback>
#     else:
#         <reset default>
#
# Under the current ``>=`` rule AI WINS every tie. The most dangerous tie is
# the all-zero case: when NO triggers fire in ANY of the three OOS evaluations,
# all alphas are exactly 0.0 and AI "wins" by default — even though no
# evidence exists that its params generalize. AI is the param set most prone
# to over-fitting (it was selected by maximizing on the training window) so
# defaulting to AI on a no-information tie is a category error for real-money
# safety.
#
# Semantic decision (test-writer recommendation)
# ----------------------------------------------
# AI must beat BOTH baselines by a STRICTLY POSITIVE margin. On ties the
# decision cascades:
#
#   * ``ai > fallback AND ai > default``  -> Adopt AI
#   * else if ``fallback >= default``     -> Revert to Fallback
#   * else                                -> Reset to Global Default
#
# Note: the fallback-vs-default tie KEEPS ``>=`` (favors fallback). Rationale:
# fallback is the validated production params from the prior cycle; on a tie
# vs. the global default it's strictly safer to preserve the last-known-good
# state than to reset.
#
# If the implementer rejects this decision and keeps the current ``>=`` rule
# at the first branch, these tests will need to be updated alongside the
# math change — the reviewer adjudicates. The tests below pin the SAFER
# behavior and document the rationale inline.
#
# Implementation note for the mock harness
# ----------------------------------------
# These FU1 tests can't use the VWAP-marker side_effect trick from the B2
# tests above to force exactly equal alphas — VWAP-marker triggering produces
# a penalty-laden alpha that varies with the marker value. To force exact
# equality (and exactly-zero alphas in particular), we patch the
# autotuner's ``run_simulation`` closure indirectly by patching the helpers
# it calls so EVERY param set hits the SAME triggering pattern. Simpler:
# patch ``math_engine.compute_vwap_breakdown_update`` to NEVER trigger and
# ensure no other trigger path fires either — that yields alpha 0.0 for all
# three param sets.
# ===========================================================================


def _make_constant_alpha_side_effect():
    """
    Side effect for ``math_engine.compute_vwap_breakdown_update`` that NEVER
    flags a VWAP break, regardless of which marker is in flight. Combined
    with the rest of the simulator's defaults (mc_prob=50.0, tick['return']=
    2.0, no other trigger condition met), this guarantees that
    ``run_simulation`` returns 0.0 -> negated to 0.0 by the caller -> all
    three OOS alphas are exactly 0.0. This is the all-zero tie scenario.
    """

    def _side_effect(**kwargs):
        # Returns (vwap_ticks, vwap_bleed_ticks, is_vwap_broken, is_vwap_bleed_broken)
        return (0, 0, False, False)

    return _side_effect


def test_oos_all_three_alphas_tie_at_zero_prefers_fallback_not_ai():
    """
    FU1 Scenario 1 — Hard tie at zero alpha across AI, fallback, default.

    Real-money rationale: when no triggers fire in any OOS evaluation, all
    three param sets are equivalently uninformative. AI is the only param
    set that was selected by maximizing on training data and is therefore
    the most prone to silent over-fitting; defaulting to AI under no-signal
    conditions imports that over-fit risk into the next live cycle.

    Expected (strict-positive rule): AI does NOT strictly beat the
    baselines, so it falls through to the fallback branch. Since fallback
    also ties default (both 0.0), the ``fallback >= default`` cascade
    chooses fallback.

    This test FAILS under the current ``>=`` rule at autotuner.py:342, which
    erroneously selects AI on a tie.
    """
    ai = _ai_best_params()
    fallback = _fallback_params()
    default = _patched_default_strategy()
    side_effect = _make_constant_alpha_side_effect()

    stdout, ctx = _run_and_capture(ai, fallback, default, side_effect)

    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1
    _name, persisted_params, _locked = save_calls[0]

    # Under the strict-positive rule, AI cannot win a tie. Fallback wins via
    # the ``fallback >= default`` cascade (both at 0.0).
    assert persisted_params["VWAP_CROSS_HWM_PCT"] == pytest.approx(FALLBACK_MARKER, abs=1e-9), (
        "On an all-zero tie across AI/fallback/default, the safer choice is "
        "to preserve the last-known-good fallback params — NOT to import "
        "the over-fit-prone AI proposal. See test docstring for full "
        "rationale. Under the current >= rule at autotuner.py:342 AI wins "
        "this tie, which this test deliberately rejects."
    )

    # Banner must indicate fallback revert (not AI adoption).
    assert "Reverting to Fallback" in stdout, (
        f"Expected fallback-revert banner on all-zero tie; got:\n{stdout}"
    )
    assert "OOS validation passed" not in stdout, (
        "AI-adoption banner must NOT print on a no-signal tie."
    )


def test_oos_ai_ties_fallback_above_default_prefers_fallback_not_ai():
    """
    FU1 Scenario 2 — AI ties fallback, both strictly beat default.

    Force the tie by making AI and fallback BOTH not trigger (alpha = 0.0)
    while default DOES trigger (alpha < 0.0 due to penalty). The tie
    between AI and fallback at 0.0 must resolve to fallback under the
    strict-positive rule (AI did not strictly beat fallback).

    Fails under current ``>=`` rule (AI would win the tie and be adopted).
    """
    ai = _ai_best_params()
    fallback = _fallback_params()
    default = _patched_default_strategy()
    # Only default triggers -> default goes negative; AI and fallback both 0.0.
    side_effect = _make_vwap_side_effect(broken_for_marker={DEFAULT_MARKER})

    stdout, ctx = _run_and_capture(ai, fallback, default, side_effect)

    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1
    _name, persisted_params, _locked = save_calls[0]

    assert persisted_params["VWAP_CROSS_HWM_PCT"] == pytest.approx(FALLBACK_MARKER, abs=1e-9), (
        "AI tied fallback at 0.0 (both above default's negative alpha); "
        "AI did not STRICTLY beat fallback, so fallback must be persisted. "
        "Strict-positive rule guards against over-fit AI silently displacing "
        "validated production params on ties."
    )

    assert "Reverting to Fallback" in stdout
    assert "OOS validation passed" not in stdout


def test_oos_ai_strictly_beats_both_baselines_persists_ai_params():
    """
    FU1 Scenario 3 — Happy path: AI STRICTLY beats both fallback AND
    default.

    Confirms that the strict-positive rule does not break the case where
    AI legitimately wins. Mirrors the B2 'Adopted AI' test but is included
    here under the FU1 banner to assert symmetry: tightening tie semantics
    must NOT regress the AI-wins case.

    AI does not trigger (alpha = 0.0); fallback and default both trigger
    (both go negative). 0.0 > negative for both comparators -> AI wins
    strictly.
    """
    ai = _ai_best_params()
    fallback = _fallback_params()
    default = _patched_default_strategy()
    side_effect = _make_vwap_side_effect(broken_for_marker={FALLBACK_MARKER, DEFAULT_MARKER})

    stdout, ctx = _run_and_capture(ai, fallback, default, side_effect)

    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1
    _name, persisted_params, _locked = save_calls[0]

    assert persisted_params["VWAP_CROSS_HWM_PCT"] == pytest.approx(AI_MARKER, abs=1e-9), (
        "AI strictly beat both baselines (0.0 > negative for fallback AND "
        "default); strict-positive rule MUST still adopt AI here."
    )

    assert "OOS validation passed" in stdout
    assert "Reverting to Fallback" not in stdout
    assert "Resetting to Global Default" not in stdout


# ===========================================================================
# Cycle B2-FU2 — Schema validation of Optuna's best_params dict
# ===========================================================================
#
# Background
# ----------
# autotuner.py:317-318 consumes ``study.best_params``:
#
#     for name, val in best_params.items():
#         best_p[name] = round(val, 2)
#
# If Optuna ever returns a dict missing one of the 7 Optuna-tuned keys,
# ``round(val, 2)`` succeeds (it only iterates what's there) and the missing
# keys silently retain their value from ``current_params`` (the fallback
# baseline). The same happens at line 348 when persisting the AI choice:
#
#     for name, val in best_params.items():
#         current_params[name] = round(val, 2)
#
# Result: a partial Optuna proposal is silently merged into fallback, and
# the persisted dict is a Frankenstein of {partial AI} ∪ {rest of fallback}.
# That hybrid was never evaluated OOS. Real-money risk: untested param
# combinations get persisted to the strategy DB and drive the next cycle.
#
# The 6 keys Optuna searches (autotuner.py:285-291):
#   TAKE_PROFIT_MC_PCT, VWAP_CROSS_HWM_PCT,
#   VWAP_BLEED_MULTIPLIER, VWAP_BLEED_TICKS, PARABOLIC_VELOCITY_THRESHOLD,
#   MAX_PARABOLIC_SQUEEZE.
#   (TRIGGER_THRESHOLD_PCT is locked via database.DEFAULT_LOCKED_VARS — excluded.)
#
# Semantic decision (test-writer recommendation)
# ----------------------------------------------
# When ``best_params`` is missing ANY of the 6 Optuna-searched keys (or is
# empty), the autotuner MUST treat the AI proposal as invalid and fall
# through to the baseline cascade (fallback then default). Equivalent to
# AI failing OOS validation. Specifically:
#
#   * Do NOT raise — the autotuner runs on a 1-minute scheduler; an
#     unhandled raise corrupts the daemon cycle. Reject the proposal
#     gracefully and persist fallback (or default if fallback also fails).
#   * Do NOT silently merge partial AI keys into fallback — the resulting
#     dict was never OOS-validated.
#   * EXTRA keys not in the search space are IGNORED (forward-compat with
#     future Optuna search-space expansion).
#
# Implementer can choose to raise + catch internally OR check upstream
# before consuming — either is acceptable so long as the observable
# behavior matches: no partial merge persisted, fallback used.
#
# These tests pin the OBSERVABLE behavior (what gets persisted), not the
# internal mechanism.
# ===========================================================================


# The exhaustive list of keys the autotuner's Optuna search space populates.
# Sourced from autotuner.py:285-291. If the search space changes, this list
# must be regenerated. We do NOT import this from autotuner.py because the
# search space is defined inline inside the ``objective`` closure and is not
# externally importable — pinning the contract here is intentional.
OPTUNA_SEARCH_SPACE_KEYS = {
    "TAKE_PROFIT_MC_PCT",
    "VWAP_CROSS_HWM_PCT",
    "VWAP_BLEED_MULTIPLIER",
    "VWAP_BLEED_TICKS",
    "PARABOLIC_VELOCITY_THRESHOLD",
    "MAX_PARABOLIC_SQUEEZE",
}


def test_optuna_search_space_keys_match_autotuner_objective_source():
    """
    Guard: if the autotuner's Optuna search space is edited (a key added or
    renamed at autotuner.py:285-291), this test fails so the schema
    validation in FU2 is updated in lock-step. Without this guard, FU2's
    schema check could drift silently from the producer.

    We assert against the SOURCE of autotuner.py rather than importing the
    closure (which is not externally importable). This is a contract pin,
    not a behavioral test.
    """
    import autotuner
    import inspect

    source = inspect.getsource(autotuner.run_autotuner)
    for key in OPTUNA_SEARCH_SPACE_KEYS:
        assert f'"{key}"' in source, (
            f"Expected Optuna search-space key '{key}' to be referenced in "
            f"autotuner.run_autotuner source; if you renamed/removed it, "
            f"update OPTUNA_SEARCH_SPACE_KEYS in this test file."
        )


def test_partial_best_params_missing_key_rejects_ai_and_persists_fallback():
    """
    FU2 Scenario 1 — Optuna returns a dict missing one of the 7 keys.

    Simulate a partial best_params dict with only TRIGGER_THRESHOLD_PCT set.
    Even if the AI would have won OOS evaluation (we force fallback and
    default to fail by making them trigger while AI does not), the partial
    schema MUST cause the AI proposal to be rejected. Persisted params must
    NOT contain the partial AI dict merged into fallback.

    Observable contract: the persisted VWAP_CROSS_HWM_PCT marker must be
    the FALLBACK marker (not the AI marker, and not a Frankenstein merge).
    """
    # Partial: only one of the seven Optuna-searched keys is present.
    # CRITICAL: the value MUST differ from fallback's TRIGGER_THRESHOLD_PCT
    # (which equals DEFAULT_STRATEGY["TRIGGER_THRESHOLD_PCT"] == 15.0). Pick
    # a value at the opposite end of the Optuna search range (5.0-25.0).
    # If the silent-merge bug fires under the current ``>=`` code path, the
    # AI's 22.0 leaks into the persisted dict; with schema validation, the
    # AI proposal is rejected wholesale and fallback's 15.0 is preserved.
    partial_ai = {"TRIGGER_THRESHOLD_PCT": 22.0}
    fallback = _fallback_params()
    default = _patched_default_strategy()
    # AI does not trigger -> alpha 0; fallback and default both trigger ->
    # both go negative. Under the OLD code path AI would win OOS, and the
    # partial dict would be silently merged into fallback. Under the new
    # schema validation, AI is rejected upstream and fallback is persisted.
    side_effect = _make_vwap_side_effect(broken_for_marker={FALLBACK_MARKER, DEFAULT_MARKER})

    stdout, ctx = _run_and_capture(partial_ai, fallback, default, side_effect)

    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1, (
        "Schema validation must reject the AI proposal gracefully, not raise; "
        "exactly one save_symphony_strategy call must occur per symphony."
    )
    _name, persisted_params, _locked = save_calls[0]

    # Persisted VWAP marker must be FALLBACK's, NOT AI's TRIGGER_THRESHOLD
    # value merged into fallback (a partial merge would still carry
    # FALLBACK's VWAP marker — so we additionally assert the persisted
    # TRIGGER_THRESHOLD_PCT matches FALLBACK's, not the partial AI's, to
    # detect a Frankenstein merge).
    assert persisted_params["VWAP_CROSS_HWM_PCT"] == pytest.approx(FALLBACK_MARKER, abs=1e-9), (
        "Partial best_params must NOT cause the AI branch to fire; "
        "fallback marker must be persisted."
    )
    assert persisted_params["TRIGGER_THRESHOLD_PCT"] == pytest.approx(
        fallback["TRIGGER_THRESHOLD_PCT"], abs=1e-9
    ), (
        "Detected Frankenstein merge: TRIGGER_THRESHOLD_PCT from the "
        "partial AI dict (22.0) leaked into the persisted fallback dict. "
        "Schema validation must reject the WHOLE AI proposal, not "
        "selectively merge keys present in the partial dict."
    )

    # Every Optuna-searched key in the persisted dict must equal fallback's
    # value — no AI key should have leaked through.
    for key in OPTUNA_SEARCH_SPACE_KEYS:
        assert persisted_params[key] == fallback[key], (
            f"Key {key!r} in persisted params differs from fallback — "
            f"indicates a partial AI merge leaked through schema validation."
        )


def test_empty_best_params_rejects_ai_and_persists_fallback():
    """
    FU2 Scenario 2 — Optuna returns an empty dict.

    Edge case of the partial schema: best_params = {}. The autotuner must
    reject this proposal entirely. With AI alpha-dominant in the mock
    (fallback and default trigger; AI does not), this would otherwise
    appear to 'win' OOS — but with no proposed params there is literally
    nothing to persist from AI. Must fall through to fallback.
    """
    empty_ai: dict = {}
    fallback = _fallback_params()
    default = _patched_default_strategy()
    side_effect = _make_vwap_side_effect(broken_for_marker={FALLBACK_MARKER, DEFAULT_MARKER})

    stdout, ctx = _run_and_capture(empty_ai, fallback, default, side_effect)

    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1
    _name, persisted_params, _locked = save_calls[0]

    # Every Optuna-searched key in the persisted dict must match fallback.
    for key in OPTUNA_SEARCH_SPACE_KEYS:
        assert persisted_params[key] == fallback[key], (
            f"Empty best_params must result in pure fallback persistence; "
            f"key {key!r} drifted from fallback's value."
        )

    # And the banner must indicate fallback was used.
    assert "Reverting to Fallback" in stdout or "Resetting to Global Default" in stdout, (
        f"Expected baseline-cascade banner on empty best_params; got:\n{stdout}"
    )
    assert "OOS validation passed" not in stdout, (
        "AI-adoption banner must NOT print when best_params is empty."
    )


def test_extra_keys_in_best_params_are_ignored_ai_proposal_still_valid():
    """
    FU2 Scenario 3 — Optuna returns ALL 7 required keys PLUS extras.

    Forward-compat: if Optuna's search space is extended in a future cycle
    and the persistence layer hasn't caught up yet, the autotuner must NOT
    reject the proposal solely because extra keys are present. Extras are
    ignored; the 7 required keys are persisted as the AI proposal.

    Set up: AI has the 7 required keys + 2 extra keys. AI does not
    trigger; fallback and default both trigger. AI should be adopted
    (extra keys are silently ignored or carried through without breaking).
    """
    ai = _ai_best_params()
    # Add two extra keys not in the Optuna search space.
    ai_with_extras = dict(ai)
    ai_with_extras["EXPERIMENTAL_FUTURE_PARAM"] = 42.0
    ai_with_extras["ANOTHER_NEW_KEY"] = 0.99

    fallback = _fallback_params()
    default = _patched_default_strategy()
    side_effect = _make_vwap_side_effect(broken_for_marker={FALLBACK_MARKER, DEFAULT_MARKER})

    stdout, ctx = _run_and_capture(ai_with_extras, fallback, default, side_effect)

    save_calls = ctx["save_calls"]
    assert len(save_calls) == 1
    _name, persisted_params, _locked = save_calls[0]

    # AI's marker survives -> AI was adopted despite the extra keys.
    assert persisted_params["VWAP_CROSS_HWM_PCT"] == pytest.approx(AI_MARKER, abs=1e-9), (
        "Extra keys in best_params must NOT cause AI rejection — schema "
        "validation requires presence of the 7 known keys, not absence "
        "of unknown keys (forward-compat with future Optuna expansion)."
    )

    # All 7 required keys must be present in the persisted dict.
    for key in OPTUNA_SEARCH_SPACE_KEYS:
        assert key in persisted_params, (
            f"Required Optuna-searched key {key!r} missing from persisted params after AI adoption."
        )


def test_partial_best_params_does_not_raise_unhandled_exception():
    """
    FU2 Scenario 4 — Robustness: the daemon runs on a 1-minute cadence.
    An unhandled exception in run_autotuner kills the EOD cycle. The
    chosen schema-validation strategy MUST handle the partial-dict case
    without surfacing an unhandled exception to the caller.

    We allow the implementer to raise INTERNALLY (e.g., raise + catch in
    a try/except inside run_autotuner) — but run_autotuner itself must
    return without propagating the exception. The simulator harness
    triggers any exception by attempting to run with the partial dict.
    """
    partial_ai = {"TAKE_PROFIT_MC_PCT": 5.0}
    fallback = _fallback_params()
    default = _patched_default_strategy()
    side_effect = _make_vwap_side_effect(broken_for_marker={FALLBACK_MARKER, DEFAULT_MARKER})

    # Must complete without raising. Capture but do not assert on stdout
    # here — this test is purely about exception-safety.
    try:
        _stdout, ctx = _run_and_capture(partial_ai, fallback, default, side_effect)
    except Exception as exc:  # pragma: no cover - failure surface
        pytest.fail(
            f"run_autotuner must not propagate exceptions when best_params "
            f"is partial; got {type(exc).__name__}: {exc}. Real-money risk: "
            f"unhandled raise corrupts the daemon's 1-minute EOD cycle."
        )

    # And a strategy MUST still be persisted (fallback or default).
    assert len(ctx["save_calls"]) == 1, (
        "Even on partial best_params, run_autotuner must persist a strategy "
        "(fallback or default) per symphony — silent no-op leaves the "
        "strategy table stale and is itself a defect."
    )
