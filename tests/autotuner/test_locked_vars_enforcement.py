"""
Golden-fixture RED tests for locked_vars enforcement in run_autotuner.

The bug (confirmed pre-cycle):
    symphony_strategies.locked_vars is a NO-OP for vars in OPTUNA_SEARCH_SPACE_KEYS.
    The objective() always calls suggest_* for ALL 6 keys (autotuner.py:2117-2122)
    and the result-application block (autotuner.py:2475-2492) overwrites current_params
    with the winning trial's full param dict before save_symphony_strategy (line 2518).
    A locked search-space var is silently overwritten each cycle.

Acceptance criteria (per TEAM_BRIEF.md):
  AC-1: a locked var in OPTUNA_SEARCH_SPACE_KEYS is NOT changed by run_autotuner.
        Both enforcement points required:
        (a) search space — do not call suggest_* for a locked var; use its current value.
        (b) result application — do NOT overwrite a locked var in current_params.
  AC-2: UNLOCKED search-space vars still get tuned normally.
  AC-3: DEFAULT_LOCKED_VARS (TRIGGER_THRESHOLD_PCT, outside the search space) still
        behaves as before — no regression.
  AC-4: run_autotuner completes cleanly with >=1 locked search-space var;
        the OPTUNA_SEARCH_SPACE_KEYS.issubset(best_params.keys()) assertion at
        autotuner.py:2339 must not fire.

Golden fixture: tests/fixtures/math/locked_vars_enforcement_golden.json

All 4 ACs are RED on HEAD (pre-fix). These tests go GREEN only when the
implementer's fix is applied.

Mocking strategy
----------------
The same _autotuner_patches context from test_oos_baseline_selection.py is
reimplemented here in a minimal form targeted to locked_vars. The key additions:

* get_symphony_strategy returns a strategy with locked_vars=[locked_var_key]
  so the run sees a locked var in the search space.
* A dedicated LOCKED_MARKER is set as the locked var's initial value in
  current_params. Post-run, this value MUST survive unchanged (AC-1).
* AI best_params includes the locked var's key with a DIFFERENT value
  (AI_LOCKED_OVERRIDE). If the bug is present, this value overwrites the
  locked var. After the fix, the pre-run value must be preserved regardless.
* An UNLOCKED_AI_MARKER is set on an unlocked var in best_params. When AI
  wins OOS, this marker must be present in the persisted params (AC-2).

NOTE: these tests do NOT mock the math engine or Optuna's numerical
output — they mock only network/DB I/O and the Optuna study object.
The VWAP side_effect approach from test_oos_baseline_selection.py is
reused to control which OOS branch fires.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

# All autotuner/database imports are deferred (lazy) to avoid module-level
# side-effects (optuna.logging.set_verbosity at import time). See the
# module docstring in test_oos_baseline_selection.py for the rationale.

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math"
    / "locked_vars_enforcement_golden.json"
)


@pytest.fixture(scope="module")
def golden() -> dict:
    with open(_FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Marker constants
#
# These are NOT hardcoded producer outputs — they are LABELS used to tag
# specific param slots so assertions can identify which param set was
# persisted. Values are chosen:
#   1. Within legal search-space bounds for their respective keys.
#   2. With 2 decimal place precision (survive autotuner's round(..., 2)).
#   3. Sufficiently far apart to be unambiguous.
#
# LOCKED_MARKER  — the pre-run value of the locked var in current_params.
#                  Must survive unchanged after run_autotuner (AC-1 core).
# AI_LOCKED_OVERRIDE — what the AI best_params contains for the locked var.
#                  If the bug is present, this replaces LOCKED_MARKER.
#                  After the fix, it must NOT appear in persisted params
#                  for the locked var.
# UNLOCKED_AI_MARKER — the AI's value for the unlocked var (VWAP_CROSS_HWM_PCT).
#                  Must appear in persisted params when AI wins (AC-2).
# UNLOCKED_INITIAL — the pre-run value of the unlocked var. Must be
#                  replaced by UNLOCKED_AI_MARKER when AI wins (AC-2).
# ---------------------------------------------------------------------------

# VWAP_BLEED_MULTIPLIER bounds: [0.5, 3.0] (autotuner._SS_VWAP_BLEED_MULT_MIN/MAX)
LOCKED_MARKER = 1.23
AI_LOCKED_OVERRIDE = 2.47  # What a broken autotuner would persist for the locked var

# VWAP_CROSS_HWM_PCT bounds: [0.5, 2.5] (autotuner._SS_VWAP_CROSS_HWM_MIN/MAX)
UNLOCKED_AI_MARKER = 1.75  # AI's proposal for the unlocked var
UNLOCKED_INITIAL = 0.88    # Pre-run current_params value for the unlocked var


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_bot_state() -> dict:
    return {"sym-1": {"name": "Test Symphony A", "account_uuid": "acc-1"}}


def _build_history_5d() -> dict:
    tick = {
        "return": 2.0,
        "mc_prob": 50.0,
        "vol": 1.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 1.0,
        "valid_vwap_weight": 1.0,
    }
    dates = [f"2026-05-{d:02d}" for d in range(1, 6)]
    return {"sym-1": {d: [tick] for d in dates}}


def _make_current_params(golden: dict, *, locked_key: str, locked_value: float,
                         unlocked_key: str, unlocked_value: float) -> dict:
    """Build a current_params dict that includes ALL search-space keys plus
    TRIGGER_THRESHOLD_PCT (the DEFAULT_LOCKED_VARS key outside the search space).

    Only the locked and unlocked marker keys are set to their test values;
    all other keys receive values derived from database.DEFAULT_STRATEGY
    at runtime (see usage in the test helpers — we call database.DEFAULT_STRATEGY
    at call time to avoid hardcoding producer values here).
    """
    import database

    p = database.DEFAULT_STRATEGY.copy()
    p[locked_key] = locked_value
    p[unlocked_key] = unlocked_value
    return p


def _make_ai_best_params(golden: dict, *, locked_key: str,
                          unlocked_key: str) -> dict:
    """Return an ai best_params dict with all OPTUNA_SEARCH_SPACE_KEYS present.

    The locked_key is set to AI_LOCKED_OVERRIDE — which the fix must NOT let
    land in the persisted params.
    The unlocked_key is set to UNLOCKED_AI_MARKER — which must land if AI wins.
    Other keys get representative in-bounds values (not from a producer — just
    valid floats that pass schema validation and the issubset check).
    """
    keys = golden["search_space_keys"]
    # Give every key a mid-range value so the schema check passes. We set
    # the specific marker keys explicitly; others use arbitrary valid values
    # that are NOT significant to the test assertions.
    params: dict = {}
    for k in keys:
        if k == locked_key:
            params[k] = AI_LOCKED_OVERRIDE
        elif k == unlocked_key:
            params[k] = UNLOCKED_AI_MARKER
        elif k == "VWAP_BLEED_TICKS":
            params[k] = 10  # int key; mid-range of [3, 30]
        elif k == "TAKE_PROFIT_MC_PCT":
            params[k] = 5.0
        elif k == "PARABOLIC_VELOCITY_THRESHOLD":
            params[k] = 2.0
        elif k == "MAX_PARABOLIC_SQUEEZE":
            params[k] = 0.45
        else:
            params[k] = 1.0
    return params


def _make_vwap_side_effect_for_key(
    marker: float, broken_for_markers: set[float]
):
    """Return a side_effect for math_engine.compute_vwap_breakdown_update.

    Mirrors the approach from test_oos_baseline_selection.py: checks the
    vwap_cross_hwm_pct kwarg against the provided marker set. Returns
    is_vwap_broken=True only for calls whose kwarg matches a broken marker.
    The autotuner always invokes the helper with kwargs.
    """
    def _side_effect(**kwargs):
        m = kwargs.get("vwap_cross_hwm_pct")
        is_broken = any(abs(m - b) < 1e-9 for b in broken_for_markers)
        return (0, 0, is_broken, False)
    return _side_effect


@contextlib.contextmanager
def _patches(
    current_params: dict,
    locked_vars: list[str],
    ai_best_params: dict,
    default_params: dict,
    vwap_side_effect,
):
    """Wire the minimal set of mocks for a run_autotuner invocation that exercises
    the locked_vars path. All assertions happen post-context-manager via the
    returned save_calls list.
    """
    fake_study = MagicMock(name="fake_optuna_study")
    fake_study.best_params = ai_best_params
    fake_study.best_value = 1.0
    fake_study.optimize = MagicMock(return_value=None)

    history = _build_history_5d()
    save_calls: list[tuple] = []

    def _capture_save(symphony_name, params, lv):
        save_calls.append((symphony_name, dict(params), list(lv)))

    with patch("autotuner._replay_grace_minutes", return_value=0), \
         patch("autotuner.optuna.create_study", return_value=fake_study), \
         patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()), \
         patch("autotuner.synthetic_history.generate_synthetic_history",
               return_value=history), \
         patch("autotuner.database.load_chart_history", return_value={}), \
         patch("autotuner.database.save_chart_archive"), \
         patch("autotuner.database.get_symphony_strategy",
               return_value={"params": current_params.copy(),
                              "locked_vars": list(locked_vars)}), \
         patch("autotuner.database.save_symphony_strategy",
               side_effect=_capture_save), \
         patch("autotuner.database.DEFAULT_STRATEGY", default_params), \
         patch("autotuner.math_engine.compute_vwap_breakdown_update",
               side_effect=vwap_side_effect):
        yield {"save_calls": save_calls, "fake_study": fake_study}


def _invoke_run_autotuner(current_params, locked_vars, ai_best_params,
                           default_params, vwap_side_effect):
    """Run run_autotuner with all patches in place; return (stdout, save_calls)."""
    import autotuner
    import inspect
    from tests.autotuner.conftest import make_phase1_theory_bundle

    spec_bundle_id = make_phase1_theory_bundle()
    sig = inspect.signature(autotuner.run_autotuner)
    extra = {"spec_bundle_id": spec_bundle_id} if "spec_bundle_id" in sig.parameters else {}

    buf = io.StringIO()
    with _patches(current_params, locked_vars, ai_best_params,
                  default_params, vwap_side_effect) as ctx, \
         contextlib.redirect_stdout(buf):
        autotuner.run_autotuner(_build_bot_state(), "2026-05-10", ["acc-1"], **extra)

    return buf.getvalue(), ctx["save_calls"]


# ---------------------------------------------------------------------------
# AC-1 — locked var is preserved end-to-end, ALL three result branches
#
# We test all three branches because the result-application block has THREE
# distinct code paths (autotuner.py:2475-2492), all of which currently
# overwrite current_params unconditionally. The fix must guard every branch.
# ---------------------------------------------------------------------------


def test_locked_search_space_var_not_overwritten_when_ai_branch_wins(golden):
    """AC-1 (search-space enforcement + result-application, AI-wins branch).

    VWAP_BLEED_MULTIPLIER is both in OPTUNA_SEARCH_SPACE_KEYS and in
    locked_vars. The AI best_params contains AI_LOCKED_OVERRIDE for it.
    After run_autotuner the persisted value must equal LOCKED_MARKER,
    NOT AI_LOCKED_OVERRIDE.

    This test is RED on HEAD: the AI-branch result-application at
    autotuner.py:2480-2481 does
        for name, val in best_params.items():
            current_params[name] = round(val, 2)
    unconditionally — LOCKED_MARKER is overwritten by AI_LOCKED_OVERRIDE.
    """
    import database

    locked_key = golden["ac1_locked_var"]["key"]          # VWAP_BLEED_MULTIPLIER
    unlocked_key = golden["ac2_unlocked_var"]["key"]       # VWAP_CROSS_HWM_PCT

    current_params = _make_current_params(
        golden,
        locked_key=locked_key,
        locked_value=LOCKED_MARKER,
        unlocked_key=unlocked_key,
        unlocked_value=UNLOCKED_INITIAL,
    )
    ai_params = _make_ai_best_params(golden, locked_key=locked_key,
                                      unlocked_key=unlocked_key)
    default_params = database.DEFAULT_STRATEGY.copy()

    # Make AI win: AI (unlocked_key = UNLOCKED_AI_MARKER) must NOT trigger;
    # default (uses DEFAULT_MARKER — a distinct float from database.DEFAULT_STRATEGY)
    # must trigger. We tag default via its VWAP_CROSS_HWM_PCT — take the
    # DEFAULT_STRATEGY value for that key as the trigger marker.
    default_hwm = float(default_params.get(unlocked_key, 0.90))
    side_effect = _make_vwap_side_effect_for_key(
        marker=UNLOCKED_AI_MARKER,
        broken_for_markers={default_hwm, UNLOCKED_INITIAL},
    )

    _stdout, save_calls = _invoke_run_autotuner(
        current_params, [locked_key], ai_params, default_params, side_effect
    )

    assert len(save_calls) == 1, (
        f"Expected exactly one save_symphony_strategy call; got {len(save_calls)}"
    )
    _name, persisted, _lv = save_calls[0]

    # AC-1 core assertion: locked var value unchanged.
    assert persisted[locked_key] == pytest.approx(LOCKED_MARKER, abs=1e-9), (
        f"AC-1 FAIL (AI branch): '{locked_key}' was expected to remain "
        f"{LOCKED_MARKER!r} (the pre-run locked value) but was persisted "
        f"as {persisted[locked_key]!r}. This is the core locked_vars bug: "
        f"the result-application at autotuner.py:2480 overwrites locked "
        f"vars unconditionally. Tolerance 1e-9 is appropriate because the "
        f"lock enforcement must be exact — no rounding of the locked value."
    )


def test_locked_search_space_var_not_overwritten_when_fallback_branch_fires(golden):
    """AC-1 (result-application, fallback branch).

    The fallback result-application at autotuner.py:2485-2486 does
        for k, v in fallback_params.items():
            current_params[k] = v
    The fallback_params come from get_symphony_strategy['params'] — they
    contain LOCKED_MARKER for the locked key (that's the current value).
    On the surface this looks safe, but the implementation derives
    fallback_params from the SAME current_params dict before the loop
    sets them — if the fix changes how current_params is seeded, this
    branch must also guard the locked var.

    More critically: if a future change to run_autotuner's fallback
    derivation pulls from database.DEFAULT_STRATEGY instead, this
    branch would be broken. The test asserts the contract, not the
    current implementation detail.

    This test is RED if the fallback branch resets the locked var to
    a value different from LOCKED_MARKER.
    """
    import database

    locked_key = golden["ac1_locked_var"]["key"]
    unlocked_key = golden["ac2_unlocked_var"]["key"]

    current_params = _make_current_params(
        golden,
        locked_key=locked_key,
        locked_value=LOCKED_MARKER,
        unlocked_key=unlocked_key,
        unlocked_value=UNLOCKED_INITIAL,
    )
    ai_params = _make_ai_best_params(golden, locked_key=locked_key,
                                      unlocked_key=unlocked_key)
    default_params = database.DEFAULT_STRATEGY.copy()

    # Make FALLBACK win: AI (UNLOCKED_AI_MARKER) triggers (goes negative);
    # fallback (UNLOCKED_INITIAL) does NOT trigger; default triggers.
    default_hwm = float(default_params.get(unlocked_key, 0.90))
    side_effect = _make_vwap_side_effect_for_key(
        marker=UNLOCKED_AI_MARKER,
        broken_for_markers={UNLOCKED_AI_MARKER, default_hwm},
    )

    _stdout, save_calls = _invoke_run_autotuner(
        current_params, [locked_key], ai_params, default_params, side_effect
    )

    assert len(save_calls) == 1
    _name, persisted, _lv = save_calls[0]

    assert persisted[locked_key] == pytest.approx(LOCKED_MARKER, abs=1e-9), (
        f"AC-1 FAIL (fallback branch): '{locked_key}' was expected to remain "
        f"{LOCKED_MARKER!r} but was persisted as {persisted[locked_key]!r}. "
        f"The fallback result-application (autotuner.py:2485) must not "
        f"overwrite locked vars. Tolerance 1e-9 is appropriate: exact match "
        f"is required for the lock invariant."
    )


def test_locked_search_space_var_not_overwritten_when_default_branch_fires(golden):
    """AC-1 (result-application, global-default branch).

    The default branch at autotuner.py:2490-2491 does
        for k, v in default_params.items():
            current_params[k] = v
    default_params comes from database.DEFAULT_STRATEGY — which contains a
    DIFFERENT value for the locked key. This is the most dangerous branch:
    the global default reset would clobber the user's locked value with the
    factory default.

    This test is RED on HEAD: the global-default branch iterates
    default_params unconditionally and overwrites the locked var.
    """
    import database

    locked_key = golden["ac1_locked_var"]["key"]
    unlocked_key = golden["ac2_unlocked_var"]["key"]

    current_params = _make_current_params(
        golden,
        locked_key=locked_key,
        locked_value=LOCKED_MARKER,
        unlocked_key=unlocked_key,
        unlocked_value=UNLOCKED_INITIAL,
    )
    ai_params = _make_ai_best_params(golden, locked_key=locked_key,
                                      unlocked_key=unlocked_key)
    default_params = database.DEFAULT_STRATEGY.copy()

    # Make DEFAULT win: both AI and fallback trigger (both go negative);
    # default does NOT trigger (stays at 0 — alpha 0 > negative).
    default_hwm = float(default_params.get(unlocked_key, 0.90))
    side_effect = _make_vwap_side_effect_for_key(
        marker=UNLOCKED_AI_MARKER,
        broken_for_markers={UNLOCKED_AI_MARKER, UNLOCKED_INITIAL},
    )

    _stdout, save_calls = _invoke_run_autotuner(
        current_params, [locked_key], ai_params, default_params, side_effect
    )

    assert len(save_calls) == 1
    _name, persisted, _lv = save_calls[0]

    assert persisted[locked_key] == pytest.approx(LOCKED_MARKER, abs=1e-9), (
        f"AC-1 FAIL (default branch): '{locked_key}' was expected to remain "
        f"{LOCKED_MARKER!r} but was persisted as {persisted[locked_key]!r}. "
        f"The global-default result-application (autotuner.py:2490) must "
        f"not overwrite locked vars with factory defaults. This is the most "
        f"dangerous branch: it replaces the operator's intentionally-locked "
        f"value with an undifferentiated default. Tolerance 1e-9: exact "
        f"match required for the lock invariant."
    )


# ---------------------------------------------------------------------------
# AC-1 (search-space enforcement) — locked var must not be suggested
#
# The objective() closure at autotuner.py:2115-2122 calls suggest_* for
# all 6 keys unconditionally. When a var is locked, it must NOT be passed
# to suggest_*; the trial should use the current_params value directly.
#
# We verify this via a recorded spy on the study object. A study that
# DOES receive suggest_float/suggest_int for the locked key is the bug.
# After the fix, no suggest_* call must reference the locked key.
# ---------------------------------------------------------------------------


def test_locked_var_excluded_from_optuna_suggest_calls(golden):
    """AC-1(a) search-space enforcement: objective() must not call suggest_*
    for a locked var.

    We intercept the trial object by giving the fake study an optimize shim
    that calls the objective once with a MagicMock trial whose suggest_float
    and suggest_int are recording wrappers. The keys suggested to the trial
    are captured and asserted upon after the run.

    This test is RED on HEAD: the objective at autotuner.py:2119 does
        p["VWAP_BLEED_MULTIPLIER"] = trial.suggest_float("VWAP_BLEED_MULTIPLIER", ...)
    unconditionally regardless of locked_vars.
    """
    import database

    locked_key = golden["ac1_locked_var"]["key"]
    unlocked_key = golden["ac2_unlocked_var"]["key"]

    current_params = _make_current_params(
        golden,
        locked_key=locked_key,
        locked_value=LOCKED_MARKER,
        unlocked_key=unlocked_key,
        unlocked_value=UNLOCKED_INITIAL,
    )
    ai_params = _make_ai_best_params(golden, locked_key=locked_key,
                                      unlocked_key=unlocked_key)
    default_params = database.DEFAULT_STRATEGY.copy()
    default_hwm = float(default_params.get(unlocked_key, 0.90))
    side_effect = _make_vwap_side_effect_for_key(
        marker=UNLOCKED_AI_MARKER,
        broken_for_markers={default_hwm, UNLOCKED_INITIAL},
    )

    # We need to intercept the trial object passed into the objective closure.
    # Strategy: replace study.optimize with a shim that calls the objective
    # exactly once with a MagicMock trial. The trial's suggest_float /
    # suggest_int are configured to record the 'name' positional argument and
    # return a plausible float/int so the objective doesn't blow up.
    #
    # Key design choice: use a MagicMock trial with side_effects that record
    # and return real values from the search space. This avoids spinning up
    # a real Optuna in-process study (which requires more infrastructure) while
    # still exercising the objective's suggest_* call pattern.
    suggested_keys: list[str] = []

    import autotuner as _autotuner

    # Lookup table: key -> a representative in-bounds value the spy trial
    # returns when suggest_* is called with that key. Values are NOT
    # hardcoded producer outputs; they are mid-range constants used only
    # so the objective doesn't arithmetic-fault on nonsense values.
    _suggest_return_values = {
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.5,
        "VWAP_BLEED_MULTIPLIER": 1.75,
        "VWAP_BLEED_TICKS": 15,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.5,
        "MAX_PARABOLIC_SQUEEZE": 0.45,
    }

    def _make_spy_trial():
        spy = MagicMock(name="spy_trial")
        spy.number = 0

        def _suggest_float(name, *a, **kw):
            suggested_keys.append(name)
            return float(_suggest_return_values.get(name, 1.0))

        def _suggest_int(name, *a, **kw):
            suggested_keys.append(name)
            return int(_suggest_return_values.get(name, 10))

        spy.suggest_float = _suggest_float
        spy.suggest_int = _suggest_int
        # study.best_params is set on the outer fake_study; best_value is
        # also on the outer fake_study. The spy trial just needs to let the
        # objective run to completion.
        return spy

    def _run_objective_once_with_spy(func, n_trials, **kwargs):
        """Shim: calls the objective exactly once with the spy trial.

        Exceptions from the objective are swallowed — the trial run itself
        is not what we are asserting; only the suggest_* calls matter.
        """
        spy_trial = _make_spy_trial()
        try:
            func(spy_trial)
        except Exception:
            # Objective may raise if downstream helpers fail with mock data;
            # that is acceptable — we only need suggest_* calls to be recorded.
            pass

    fake_study = MagicMock(name="fake_optuna_study")
    fake_study.best_params = ai_params
    fake_study.best_value = 1.0
    fake_study.optimize = _run_objective_once_with_spy

    history = _build_history_5d()
    save_calls: list[tuple] = []

    def _capture_save(symphony_name, params, lv):
        save_calls.append((symphony_name, dict(params), list(lv)))

    import inspect
    from tests.autotuner.conftest import make_phase1_theory_bundle

    spec_bundle_id = make_phase1_theory_bundle()
    sig = inspect.signature(_autotuner.run_autotuner)
    extra = {"spec_bundle_id": spec_bundle_id} if "spec_bundle_id" in sig.parameters else {}
    buf = io.StringIO()

    with patch("autotuner._replay_grace_minutes", return_value=0), \
         patch("autotuner.optuna.create_study", return_value=fake_study), \
         patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()), \
         patch("autotuner.synthetic_history.generate_synthetic_history",
               return_value=history), \
         patch("autotuner.database.load_chart_history", return_value={}), \
         patch("autotuner.database.save_chart_archive"), \
         patch("autotuner.database.get_symphony_strategy",
               return_value={"params": current_params.copy(),
                              "locked_vars": [locked_key]}), \
         patch("autotuner.database.save_symphony_strategy",
               side_effect=_capture_save), \
         patch("autotuner.database.DEFAULT_STRATEGY", default_params), \
         patch("autotuner.math_engine.compute_vwap_breakdown_update",
               side_effect=side_effect), \
         contextlib.redirect_stdout(buf):
        _autotuner.run_autotuner(_build_bot_state(), "2026-05-10", ["acc-1"], **extra)

    # Precondition: some suggest_* calls must have been recorded. If none were,
    # the spy shim did not fire — likely because optimize was not called with
    # the objective. The test cannot be meaningful if the objective never ran.
    assert suggested_keys, (
        f"AC-1(a) precondition failed: no suggest_* calls were recorded. "
        f"The spy shim's optimize replacement must have been called with the "
        f"objective closure. Check that patch('autotuner.optuna.create_study') "
        f"is returning the fake_study and that fake_study.optimize is being "
        f"invoked by run_autotuner."
    )

    assert locked_key not in suggested_keys, (
        f"AC-1(a) FAIL: objective() called suggest_* for locked key "
        f"'{locked_key}'. Suggested keys recorded: {suggested_keys}. "
        f"A locked var must NOT be passed to Optuna's suggest_* API — "
        f"the trial must use the current_params value directly (see "
        f"autotuner.py:2115-2122 and the fix brief). This test confirms "
        f"the search-space enforcement point is present."
    )

    # AC-2 precursor: unlocked vars ARE still suggested.
    assert unlocked_key in suggested_keys, (
        f"AC-2 precondition: expected '{unlocked_key}' (unlocked) to be "
        f"suggested by the objective — it was absent from {suggested_keys}. "
        f"The fix must only exclude LOCKED vars, not all search-space vars."
    )


# ---------------------------------------------------------------------------
# AC-2 — unlocked vars still get tuned normally
# ---------------------------------------------------------------------------


def test_unlocked_search_space_var_is_tuned_when_ai_wins(golden):
    """AC-2: an UNLOCKED var in OPTUNA_SEARCH_SPACE_KEYS must still be
    updated when the AI proposal wins OOS.

    Pre-run value is UNLOCKED_INITIAL; AI proposal is UNLOCKED_AI_MARKER.
    After run_autotuner with AI winning, the persisted value must equal
    UNLOCKED_AI_MARKER (post round-to-2dp — both markers are 2dp-exact).

    This test is GREEN on HEAD (unlocked vars are currently always tuned).
    After the fix it must remain GREEN — over-restriction is a correctness
    defect too.
    """
    import database

    locked_key = golden["ac1_locked_var"]["key"]
    unlocked_key = golden["ac2_unlocked_var"]["key"]
    ai_unlocked_expected = golden["ac2_unlocked_var"]["ai_value"]  # 1.50 per fixture

    # Build AI params where the unlocked key equals the fixture's ai_value
    # (not the test-local UNLOCKED_AI_MARKER) so the fixture governs the
    # expected value — no producer hardcode.
    ai_params = _make_ai_best_params(golden, locked_key=locked_key,
                                      unlocked_key=unlocked_key)
    ai_params[unlocked_key] = float(ai_unlocked_expected)

    current_params = _make_current_params(
        golden,
        locked_key=locked_key,
        locked_value=LOCKED_MARKER,
        unlocked_key=unlocked_key,
        unlocked_value=UNLOCKED_INITIAL,
    )
    default_params = database.DEFAULT_STRATEGY.copy()
    default_hwm = float(default_params.get(unlocked_key, 0.90))

    # AI wins: only fallback and default trigger (negative alpha); AI does not.
    side_effect = _make_vwap_side_effect_for_key(
        marker=float(ai_unlocked_expected),
        broken_for_markers={default_hwm, UNLOCKED_INITIAL},
    )

    _stdout, save_calls = _invoke_run_autotuner(
        current_params, [locked_key], ai_params, default_params, side_effect
    )

    assert len(save_calls) == 1
    _name, persisted, _lv = save_calls[0]

    assert persisted[unlocked_key] == pytest.approx(
        float(ai_unlocked_expected), abs=1e-2
    ), (
        f"AC-2 FAIL: unlocked var '{unlocked_key}' was expected to be "
        f"tuned to the AI value {ai_unlocked_expected!r} (from fixture) "
        f"but persisted as {persisted[unlocked_key]!r}. Tolerance 1e-2 "
        f"is appropriate: the autotuner rounds params to 2 decimal places "
        f"(autotuner.py:2481), so the comparison must tolerate rounding."
    )


# ---------------------------------------------------------------------------
# AC-3 — DEFAULT_LOCKED_VARS regression (TRIGGER_THRESHOLD_PCT)
# ---------------------------------------------------------------------------


def test_default_locked_var_trigger_threshold_pct_preserved_when_ai_wins(golden):
    """AC-3: TRIGGER_THRESHOLD_PCT (in DEFAULT_LOCKED_VARS, OUTSIDE the
    Optuna search space) is still preserved after the fix.

    Pre-condition: TRIGGER_THRESHOLD_PCT is NOT in OPTUNA_SEARCH_SPACE_KEYS,
    so it was never overwritten by the objective. The fix must not regress
    this: TRIGGER_THRESHOLD_PCT must equal its pre-run current_params value
    in the persisted dict regardless of which branch fires.

    We test only the AI-wins branch here; if the fix accidentally changes
    how non-search-space keys are handled, this test catches it.
    """
    import database

    locked_key = golden["ac1_locked_var"]["key"]           # in search space
    unlocked_key = golden["ac2_unlocked_var"]["key"]
    default_locked_key = golden["ac3_default_locked_var"]["key"]  # TRIGGER_THRESHOLD_PCT

    # Use the current DEFAULT_STRATEGY value for TRIGGER_THRESHOLD_PCT as
    # the pre-run value. Do NOT hardcode it — derive from producer.
    trigger_pre_run = database.DEFAULT_STRATEGY[default_locked_key]

    current_params = _make_current_params(
        golden,
        locked_key=locked_key,
        locked_value=LOCKED_MARKER,
        unlocked_key=unlocked_key,
        unlocked_value=UNLOCKED_INITIAL,
    )
    current_params[default_locked_key] = trigger_pre_run

    ai_params = _make_ai_best_params(golden, locked_key=locked_key,
                                      unlocked_key=unlocked_key)
    default_params = database.DEFAULT_STRATEGY.copy()
    default_hwm = float(default_params.get(unlocked_key, 0.90))

    # AI wins.
    side_effect = _make_vwap_side_effect_for_key(
        marker=UNLOCKED_AI_MARKER,
        broken_for_markers={default_hwm, UNLOCKED_INITIAL},
    )

    _stdout, save_calls = _invoke_run_autotuner(
        current_params, [locked_key, default_locked_key],
        ai_params, default_params, side_effect
    )

    assert len(save_calls) == 1
    _name, persisted, _lv = save_calls[0]

    assert persisted[default_locked_key] == pytest.approx(
        trigger_pre_run, abs=1e-9
    ), (
        f"AC-3 FAIL: '{default_locked_key}' (DEFAULT_LOCKED_VARS, outside "
        f"the search space) was expected to remain at its pre-run value "
        f"{trigger_pre_run!r} but was persisted as "
        f"{persisted[default_locked_key]!r}. The fix must not regress the "
        f"DEFAULT_LOCKED_VARS behavior. Tolerance 1e-9: exact match required."
    )


def test_default_locked_var_trigger_threshold_pct_preserved_when_default_branch_fires(golden):
    """AC-3: TRIGGER_THRESHOLD_PCT preserved even when default branch fires.

    The global-default branch at autotuner.py:2490 iterates
    default_params.items() — DEFAULT_STRATEGY — which contains
    TRIGGER_THRESHOLD_PCT. If a future change to DEFAULT_STRATEGY modifies
    that key's value, the fix must guard it here too.

    After the fix: the locked default_locked_key in locked_vars must be
    preserved even when the default branch fires (its value in current_params
    is the pre-run value, not the DEFAULT_STRATEGY reset).
    """
    import database

    locked_key = golden["ac1_locked_var"]["key"]
    unlocked_key = golden["ac2_unlocked_var"]["key"]
    default_locked_key = golden["ac3_default_locked_var"]["key"]

    trigger_pre_run = database.DEFAULT_STRATEGY[default_locked_key]

    current_params = _make_current_params(
        golden,
        locked_key=locked_key,
        locked_value=LOCKED_MARKER,
        unlocked_key=unlocked_key,
        unlocked_value=UNLOCKED_INITIAL,
    )
    current_params[default_locked_key] = trigger_pre_run

    ai_params = _make_ai_best_params(golden, locked_key=locked_key,
                                      unlocked_key=unlocked_key)
    default_params = database.DEFAULT_STRATEGY.copy()

    # Default wins: AI and fallback both trigger.
    side_effect = _make_vwap_side_effect_for_key(
        marker=UNLOCKED_AI_MARKER,
        broken_for_markers={UNLOCKED_AI_MARKER, UNLOCKED_INITIAL},
    )

    _stdout, save_calls = _invoke_run_autotuner(
        current_params, [locked_key, default_locked_key],
        ai_params, default_params, side_effect
    )

    assert len(save_calls) == 1
    _name, persisted, _lv = save_calls[0]

    assert persisted[default_locked_key] == pytest.approx(
        trigger_pre_run, abs=1e-9
    ), (
        f"AC-3 FAIL (default branch): '{default_locked_key}' must be "
        f"preserved at {trigger_pre_run!r} even when the global-default "
        f"branch fires. The fix must guard all three result-application "
        f"branches. Tolerance 1e-9: exact match required."
    )


# ---------------------------------------------------------------------------
# AC-4 — run completes cleanly with >=1 locked search-space var
# ---------------------------------------------------------------------------


def test_run_autotuner_completes_without_exception_when_search_space_var_is_locked(golden):
    """AC-4: run_autotuner must complete without an unhandled exception when
    a search-space var is locked.

    The OPTUNA_SEARCH_SPACE_KEYS.issubset(best_params.keys()) assertion at
    autotuner.py:2339 checks that all search-space keys are present in
    best_params. When the fix excludes a locked var from suggest_*, that
    key is absent from best_params — triggering the assertion/schema-invalid
    path.

    The fix MUST handle this — either by injecting the locked vars' current
    values back into best_params before the check, or by relaxing the check
    to only require the UNLOCKED search-space keys. The run must complete
    and exactly one save_symphony_strategy call must occur.

    This test is RED on HEAD: without the fix, the locked var IS in
    best_params (because suggest_* fires for it), so the assertion passes
    accidentally. After the fix, if the issubset guard is not updated, the
    run silently rejects the AI proposal and falls through to fallback/default
    — the test requires the run to COMPLETE (not raise), but the behavior
    of which branch fires is not asserted here (AC-1 covers the value).
    """
    import database

    locked_key = golden["ac1_locked_var"]["key"]
    unlocked_key = golden["ac2_unlocked_var"]["key"]

    current_params = _make_current_params(
        golden,
        locked_key=locked_key,
        locked_value=LOCKED_MARKER,
        unlocked_key=unlocked_key,
        unlocked_value=UNLOCKED_INITIAL,
    )
    ai_params = _make_ai_best_params(golden, locked_key=locked_key,
                                      unlocked_key=unlocked_key)
    # Drop the locked key from ai_params to simulate what happens when the
    # fix correctly excludes the locked var from suggest_* — best_params
    # won't carry it. This is the state the issubset check sees post-fix.
    ai_params_without_locked = {k: v for k, v in ai_params.items() if k != locked_key}

    default_params = database.DEFAULT_STRATEGY.copy()
    default_hwm = float(default_params.get(unlocked_key, 0.90))
    side_effect = _make_vwap_side_effect_for_key(
        marker=UNLOCKED_AI_MARKER,
        broken_for_markers={default_hwm, UNLOCKED_INITIAL},
    )

    try:
        _stdout, save_calls = _invoke_run_autotuner(
            current_params, [locked_key], ai_params_without_locked,
            default_params, side_effect
        )
    except Exception as exc:
        pytest.fail(
            f"AC-4 FAIL: run_autotuner raised an unhandled exception when "
            f"the locked search-space var '{locked_key}' was absent from "
            f"best_params (simulating the post-fix state where suggest_* "
            f"is not called for locked vars): {type(exc).__name__}: {exc}. "
            f"The fix must handle the issubset assertion at autotuner.py:2339 "
            f"gracefully — e.g. inject locked var values back into best_params "
            f"before the check, or relax the check to UNLOCKED keys only."
        )

    assert len(save_calls) == 1, (
        f"AC-4 FAIL: expected exactly one save_symphony_strategy call even "
        f"when the locked var is absent from best_params; got {len(save_calls)}. "
        f"The run must complete and persist a strategy (AI, fallback, or default)."
    )


def test_run_autotuner_completes_with_multiple_locked_search_space_vars(golden):
    """AC-4 (multi-lock): run completes cleanly when more than one
    search-space var is locked.

    Locking 2 of the 6 search-space keys leaves only 4 suggested keys.
    The run must still complete without an exception and persist exactly
    one strategy.
    """
    import database

    locked_key_1 = golden["ac1_locked_var"]["key"]        # VWAP_BLEED_MULTIPLIER
    locked_key_2 = "PARABOLIC_VELOCITY_THRESHOLD"         # second search-space key
    unlocked_key = golden["ac2_unlocked_var"]["key"]

    current_params = _make_current_params(
        golden,
        locked_key=locked_key_1,
        locked_value=LOCKED_MARKER,
        unlocked_key=unlocked_key,
        unlocked_value=UNLOCKED_INITIAL,
    )
    # Give the second locked key a distinct pre-run value derived from
    # the search-space bounds (mid-range), not hardcoded.
    import autotuner as _at
    parabolic_locked_value = (
        _at._SS_PARA_VEL_MIN + _at._SS_PARA_VEL_MAX
    ) / 2.0
    current_params[locked_key_2] = parabolic_locked_value

    # Remove both locked keys from ai_params — simulating post-fix best_params.
    ai_params = _make_ai_best_params(golden, locked_key=locked_key_1,
                                      unlocked_key=unlocked_key)
    ai_params_without_locked = {
        k: v for k, v in ai_params.items()
        if k not in {locked_key_1, locked_key_2}
    }

    default_params = database.DEFAULT_STRATEGY.copy()
    default_hwm = float(default_params.get(unlocked_key, 0.90))
    side_effect = _make_vwap_side_effect_for_key(
        marker=UNLOCKED_AI_MARKER,
        broken_for_markers={default_hwm, UNLOCKED_INITIAL},
    )

    try:
        _stdout, save_calls = _invoke_run_autotuner(
            current_params, [locked_key_1, locked_key_2],
            ai_params_without_locked, default_params, side_effect
        )
    except Exception as exc:
        pytest.fail(
            f"AC-4 FAIL (multi-lock): run_autotuner raised an unhandled "
            f"exception when both '{locked_key_1}' and '{locked_key_2}' "
            f"were locked (absent from best_params): "
            f"{type(exc).__name__}: {exc}."
        )

    assert len(save_calls) == 1, (
        f"AC-4 FAIL (multi-lock): expected one save call; got {len(save_calls)}."
    )

    # Both locked vars must retain their pre-run values.
    _name, persisted, _lv = save_calls[0]
    assert persisted[locked_key_1] == pytest.approx(LOCKED_MARKER, abs=1e-9), (
        f"AC-1 FAIL (multi-lock): '{locked_key_1}' must retain {LOCKED_MARKER!r}."
    )
    assert persisted[locked_key_2] == pytest.approx(parabolic_locked_value, abs=1e-2), (
        f"AC-1 FAIL (multi-lock): '{locked_key_2}' must retain "
        f"{parabolic_locked_value!r} (mid-range of search space). "
        f"Tolerance 1e-2 allows for the autotuner's 2-decimal-place rounding."
    )


# ---------------------------------------------------------------------------
# Structural invariants — cross-cutting
# ---------------------------------------------------------------------------


def test_locked_vars_list_is_persisted_verbatim_by_save_symphony_strategy(golden):
    """The locked_vars list passed to save_symphony_strategy must match the
    locked_vars list that was read from get_symphony_strategy.

    run_autotuner reads locked_vars at autotuner.py:2083 and passes it
    unchanged to save_symphony_strategy at autotuner.py:2518. The fix must
    not alter the locked_vars list itself — only the params values should
    change.
    """
    import database

    locked_key = golden["ac1_locked_var"]["key"]
    unlocked_key = golden["ac2_unlocked_var"]["key"]

    current_params = _make_current_params(
        golden,
        locked_key=locked_key,
        locked_value=LOCKED_MARKER,
        unlocked_key=unlocked_key,
        unlocked_value=UNLOCKED_INITIAL,
    )
    ai_params = _make_ai_best_params(golden, locked_key=locked_key,
                                      unlocked_key=unlocked_key)
    default_params = database.DEFAULT_STRATEGY.copy()
    default_hwm = float(default_params.get(unlocked_key, 0.90))
    side_effect = _make_vwap_side_effect_for_key(
        marker=UNLOCKED_AI_MARKER,
        broken_for_markers={default_hwm, UNLOCKED_INITIAL},
    )

    locked_vars_input = [locked_key]
    _stdout, save_calls = _invoke_run_autotuner(
        current_params, locked_vars_input, ai_params, default_params, side_effect
    )

    assert len(save_calls) == 1
    _name, _params, persisted_lv = save_calls[0]

    assert persisted_lv == locked_vars_input, (
        f"Structural invariant: the locked_vars list passed to "
        f"save_symphony_strategy must match what was read from "
        f"get_symphony_strategy. Expected {locked_vars_input!r}, "
        f"got {persisted_lv!r}. The fix must not mutate the locked_vars "
        f"list — only guard param values during result-application."
    )


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# BLOCK-2 (reviewer finding) -- FrozenTrial.params must not be mutated
#
# On the haircut path (autotuner.py:2294), best_params = winner_trial.params.
# The locked-vars injection block must copy best_params first:
#     best_params = dict(best_params)
# so the FrozenTrial's internal dict is never modified in-place.
#
# We test this via run_autotuner: study.best_params is a shared dict
# reference (simulating winner_trial.params missing the locked key),
# then we verify the ORIGINAL dict is unchanged after run_autotuner.
# ---------------------------------------------------------------------------


def test_locked_var_injection_does_not_mutate_original_best_params_dict(golden):
    """BLOCK-2: the locked-var injection must NOT mutate the original
    best_params dict (winner_trial.params on the haircut path).

    study.best_params is fed as a shared dict reference missing the locked
    key (simulating post-fix winner_trial.params). After run_autotuner the
    original dict must be unchanged -- proving a copy was made before injection.

    RED on 0486402 if best_params = dict(best_params) is absent before
    the injection loop at autotuner.py:2353.
    """
    import database

    locked_key = golden["ac1_locked_var"]["key"]
    unlocked_key = golden["ac2_unlocked_var"]["key"]

    current_params = _make_current_params(
        golden,
        locked_key=locked_key,
        locked_value=LOCKED_MARKER,
        unlocked_key=unlocked_key,
        unlocked_value=UNLOCKED_INITIAL,
    )
    # Build ai_params WITHOUT the locked key -- this is what winner_trial.params
    # looks like post-fix (locked var was excluded from suggest_*).
    ai_params_full = _make_ai_best_params(golden, locked_key=locked_key,
                                           unlocked_key=unlocked_key)
    # shared_ref simulates winner_trial.params: absent locked key, same object.
    shared_ref: dict = {k: v for k, v in ai_params_full.items() if k != locked_key}
    snapshot_before = dict(shared_ref)
    original_id = id(shared_ref)

    default_params = database.DEFAULT_STRATEGY.copy()
    default_hwm = float(default_params.get(unlocked_key, 0.90))
    side_effect = _make_vwap_side_effect_for_key(
        marker=UNLOCKED_AI_MARKER,
        broken_for_markers={default_hwm, UNLOCKED_INITIAL},
    )

    import autotuner as _autotuner
    import inspect
    from tests.autotuner.conftest import make_phase1_theory_bundle

    history = _build_history_5d()
    save_calls: list[tuple] = []

    def _capture_save(symphony_name, params, lv):
        save_calls.append((symphony_name, dict(params), list(lv)))

    fake_study = MagicMock(name="fake_optuna_study")
    # study.best_params IS shared_ref -- same object as winner_trial.params.
    # autotuner reads study.best_params at ~line 2295 (non-haircut path uses
    # this directly). The injection at ~2360 must copy before writing.
    fake_study.best_params = shared_ref
    fake_study.best_value = 1.0
    fake_study.optimize = MagicMock(return_value=None)

    spec_bundle_id = make_phase1_theory_bundle()
    sig = inspect.signature(_autotuner.run_autotuner)
    extra = {"spec_bundle_id": spec_bundle_id} if "spec_bundle_id" in sig.parameters else {}
    buf = io.StringIO()

    with patch("autotuner._replay_grace_minutes", return_value=0), \
         patch("autotuner.optuna.create_study", return_value=fake_study), \
         patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()), \
         patch("autotuner.synthetic_history.generate_synthetic_history",
               return_value=history), \
         patch("autotuner.database.load_chart_history", return_value={}), \
         patch("autotuner.database.save_chart_archive"), \
         patch("autotuner.database.get_symphony_strategy",
               return_value={"params": current_params.copy(),
                              "locked_vars": [locked_key]}), \
         patch("autotuner.database.save_symphony_strategy",
               side_effect=_capture_save), \
         patch("autotuner.database.DEFAULT_STRATEGY", default_params), \
         patch("autotuner.math_engine.compute_vwap_breakdown_update",
               side_effect=side_effect), \
         contextlib.redirect_stdout(buf):
        _autotuner.run_autotuner(_build_bot_state(), "2026-05-10", ["acc-1"], **extra)

    # shared_ref must be unchanged: no locked key injected into the original.
    # If best_params = dict(best_params) is present, injection operates on a
    # copy and shared_ref stays as-is. If absent, shared_ref gains locked_key.
    assert shared_ref == snapshot_before, (
        f"BLOCK-2 FAIL: the original best_params dict was mutated by the "
        f"locked-var injection (autotuner.py injection block). "
        f"Before run: {snapshot_before}. "
        f"After run:  {dict(shared_ref)}. "
        f"Extra keys added: {set(shared_ref) - set(snapshot_before)}. "
        f"The fix requires `best_params = dict(best_params)` BEFORE the "
        f"injection loop so winner_trial.params is not mutated."
    )
    assert id(shared_ref) == original_id, (
        "Precondition: shared_ref identity must be stable across the run."
    )
