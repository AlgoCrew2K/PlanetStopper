"""R3-a deliverable (a) — walk-forward objective-variance smoke, ALL 6 tuned dims.

Pre-retune checklist item (a) (DECISIONS.md a/b; feature-plans/math-r3a-checklist.md):
prove the production Optuna objective is genuinely sensitive to EVERY tuned
search-space dimension, at the WALK-FORWARD level (not merely wiring-level),
before the R3-d live-money retune leans on it.

WHY THIS EXISTS (audit basis DE-MATH-AUDIT-001): the existing partial smoke
`tests/autotuner/test_ac7_inert_dims_objective_variance_smoke.py` proves
`TAKE_PROFIT_MC_PCT` at walk-forward and the two parabolic dims only at the
*wiring* level (hand-specified ticks with mc_prob supplied directly). The three
VWAP dims (`VWAP_CROSS_HWM_PCT`, `VWAP_BLEED_MULTIPLIER`, `VWAP_BLEED_TICKS`)
have NO walk-forward variance proof at all. A dim with no demonstrated objective
variance is a dim Optuna spends trials tuning as noise — objective-inert live
money. This file closes that gap to full 6-dim walk-forward coverage.

SOURCE OF TRUTH (AC-1): the tuned set is enumerated from
`autotuner.OPTUNA_SEARCH_SPACE_KEYS` (autotuner.py:157) — NOT hardcoded here.
`TRIGGER_THRESHOLD_PCT` is deliberately EXCLUDED: it is a frozen non-tuned
default read via `p.get("TRIGGER_THRESHOLD_PCT", 15.0)` (autotuner.py:1173),
never a `trial.suggest_*` call, and it is in `database.DEFAULT_LOCKED_VARS`.

DRIFT-GUARD (AC-1, PM binding ruling): `OPTUNA_SEARCH_SPACE_KEYS` is only a
*validation contract* inside `run_autotuner` (used at :2765/:2772/:2775); it does
NOT drive the `trial.suggest_*` calls (those are hardcoded literals). So the
constant could silently drift from the real search space — a dev adds a 7th
`suggest_*` but forgets the constant, and an AC-1 that checked the constant alone
would pass green while a zero-demo dim ships to live money. We triangulate:
    constant  ==  {suggest_* names in run_autotuner's objective}  ==  SWEPT_DIMS
Drift on ANY leg fails. (Verified on the fork base: no current drift — all three
legs are the same 6 dims; the guard is armed for the future.)

NON-VACUITY IS THE CRUX (memory: an "N results" test hid a dead factor): every
per-dim variance assertion is paired with an inert-stub control
(`force_inert=True`) proving the assertion COLLAPSES when the dim is made inert.
A gate that cannot fail on a wrong implementation is theater.

RED CONTRACT: this file drives the not-yet-built probe module
`scripts/objective_variance_probe.py` (deliverable (a), r3a-tuner). Until that
module exists (with per-dim discriminating bar-derived fixtures that FIRE each
dim's decision codepath), every probe-dependent test FAILS with a clear message.
The drift-guard and TRIGGER-exclusion assertions also fail-by-absence until the
probe exposes its enumerators. Import is guarded so RED is clean FAILUREs, not
collection errors.
"""

from __future__ import annotations

import pytest

import autotuner

# ---------------------------------------------------------------------------
# Guarded import of the deliverable-(a) probe (built by r3a-tuner during GREEN).
# Guarded so a missing module yields clean per-test FAILUREs (assert below),
# never a collection error that muddies the reference count.
# ---------------------------------------------------------------------------
try:
    from scripts import objective_variance_probe as _probe
except Exception:  # noqa: BLE001 — any import failure = not-yet-built = RED
    _probe = None


def _require_probe():
    assert _probe is not None, (
        "scripts.objective_variance_probe is not yet built (RED). r3a-tuner "
        "builds it during GREEN: the source-derived dim enumerator + the "
        "per-dim bar-derived walk-forward sweep with codepath-fired counters."
    )
    return _probe


# The dims this smoke actually sweeps + asserts variance on. AC-1 binds this to
# OPTUNA_SEARCH_SPACE_KEYS, so dropping a dim here (e.g. because a fixture is
# hard to build) makes AC-1 FAIL rather than silently shrinking coverage.
SWEPT_DIMS = frozenset(
    {
        "TAKE_PROFIT_MC_PCT",
        "VWAP_CROSS_HWM_PCT",
        "VWAP_BLEED_MULTIPLIER",
        "VWAP_BLEED_TICKS",
        "PARABOLIC_VELOCITY_THRESHOLD",
        "MAX_PARABOLIC_SQUEEZE",
    }
)

# Minimum objective delta (max-min across the sweep) that counts as GENUINE
# sensitivity rather than float/seed jitter. Double-precision noise on O(1..100)
# guard-alpha objectives is ~1e-13; a real fired-vs-not-fired exit moves the
# objective by O(0.1)+. 1e-6 sits ~7 orders above the noise floor and ~5 below
# the smallest real exit-outcome delta, so it cleanly separates "genuinely
# sensitive" from "numerically noisy" (plan Edge Cases: near-degenerate
# variance). A dim that can only move the objective below this floor is a WEAK
# fixture to be fixed, never a reason to lower the floor.
_MIN_MEANINGFUL_OBJECTIVE_DELTA = 1e-6


def _distinct_objectives_span(objectives: dict) -> float:
    values = list(objectives.values())
    return max(values) - min(values)


# ===========================================================================
# AC-1 — dim enumeration is source-derived, drift-guarded, TRIGGER excluded.
# ===========================================================================


def test_probe_production_tuned_dims_equals_optuna_search_space_keys() -> None:
    """The probe's enumeration authority IS autotuner.OPTUNA_SEARCH_SPACE_KEYS."""
    probe = _require_probe()
    assert frozenset(probe.production_tuned_dims()) == frozenset(
        autotuner.OPTUNA_SEARCH_SPACE_KEYS
    ), (
        "probe.production_tuned_dims() must return exactly "
        "autotuner.OPTUNA_SEARCH_SPACE_KEYS (the imported constant is the "
        "primary enumeration authority per PM ruling)."
    )


def test_swept_dims_equals_production_search_space() -> None:
    """AC-1 core: the dims this smoke sweeps == the production tuned set.

    A future dim added to OPTUNA_SEARCH_SPACE_KEYS WITHOUT adding a sweep here
    makes this FAIL — the un-demoed dim cannot silently ship to the retune.
    """
    assert frozenset(autotuner.OPTUNA_SEARCH_SPACE_KEYS) == SWEPT_DIMS, (
        "SWEPT_DIMS has drifted from autotuner.OPTUNA_SEARCH_SPACE_KEYS "
        f"(only-in-swept={sorted(SWEPT_DIMS - set(autotuner.OPTUNA_SEARCH_SPACE_KEYS))}, "
        f"only-in-production={sorted(set(autotuner.OPTUNA_SEARCH_SPACE_KEYS) - SWEPT_DIMS)}). "
        "Every production tuned dim MUST have a walk-forward variance demo before "
        "the R3-d retune; add the missing dim's fixture + sweep, do not shrink SWEPT_DIMS."
    )


def test_optuna_search_space_keys_matches_actual_suggest_calls() -> None:
    """DRIFT-GUARD (PM binding): the validation constant == the REAL search space.

    OPTUNA_SEARCH_SPACE_KEYS does not DRIVE the trial.suggest_* calls (they are
    hardcoded literals at autotuner.py:2473-2495); it is only asserted against
    downstream. If a 7th suggest_* is added to run_autotuner's objective without
    updating the constant, the constant would understate the real search space
    and SWEPT_DIMS==constant would pass green while a zero-demo dim ships live.
    This closes that leg: constant == {suggest_* names in run_autotuner}.
    """
    probe = _require_probe()
    suggested = frozenset(probe.suggest_names_in_run_autotuner_objective())
    assert suggested == frozenset(autotuner.OPTUNA_SEARCH_SPACE_KEYS), (
        "OPTUNA_SEARCH_SPACE_KEYS has drifted from the actual trial.suggest_* "
        f"calls in run_autotuner's objective (only-in-suggests="
        f"{sorted(suggested - set(autotuner.OPTUNA_SEARCH_SPACE_KEYS))}, "
        f"only-in-constant={sorted(set(autotuner.OPTUNA_SEARCH_SPACE_KEYS) - suggested)}). "
        "The constant is a validation contract that MUST mirror the real search "
        "space — a drift here means a dim is tuned (or not) contrary to the "
        "declared set. Fix the constant to match run_autotuner's suggests."
    )


def test_trigger_threshold_pct_is_not_a_tuned_dim() -> None:
    """TRIGGER_THRESHOLD_PCT is a frozen default (autotuner.py:1173), never tuned."""
    assert "TRIGGER_THRESHOLD_PCT" not in autotuner.OPTUNA_SEARCH_SPACE_KEYS, (
        "TRIGGER_THRESHOLD_PCT must NOT be in OPTUNA_SEARCH_SPACE_KEYS — it is a "
        "frozen non-tuned default and is in DEFAULT_LOCKED_VARS."
    )
    probe = _require_probe()
    assert "TRIGGER_THRESHOLD_PCT" not in probe.suggest_names_in_run_autotuner_objective(), (
        "TRIGGER_THRESHOLD_PCT must never appear as a trial.suggest_* call in "
        "run_autotuner's objective."
    )


# --- AC-1 non-vacuity: the source extractor is genuinely source-derived -----
# The extractor is exercised on SYNTHETIC source so we prove it (a) grows to
# report a NEW dim (not a hardcoded 6) and (b) scopes to the named enclosing
# function (excludes the calibration-sweep objective's 2 dims).

_SYNTH_SOURCE_SEVEN_DIMS = """
def run_autotuner(bot_state):
    def objective(trial):
        p = {}
        p["TAKE_PROFIT_MC_PCT"] = trial.suggest_float("TAKE_PROFIT_MC_PCT", 2.0, 10.0)
        p["VWAP_CROSS_HWM_PCT"] = trial.suggest_float("VWAP_CROSS_HWM_PCT", 0.5, 2.5)
        p["VWAP_BLEED_MULTIPLIER"] = trial.suggest_float("VWAP_BLEED_MULTIPLIER", 0.5, 3.0)
        p["VWAP_BLEED_TICKS"] = trial.suggest_int("VWAP_BLEED_TICKS", 3, 30)
        p["PARABOLIC_VELOCITY_THRESHOLD"] = trial.suggest_float("PARABOLIC_VELOCITY_THRESHOLD", 1.0, 4.0)
        p["MAX_PARABOLIC_SQUEEZE"] = trial.suggest_float("MAX_PARABOLIC_SQUEEZE", 0.1, 0.8)
        p["NEW_UNDEMOED_DIM"] = trial.suggest_float("NEW_UNDEMOED_DIM", 0.0, 1.0)
        return 0.0
    return objective

def run_calibration_sweep(bot_state):
    def objective(trial):
        p = {}
        p["PARABOLIC_VELOCITY_THRESHOLD"] = trial.suggest_float("PARABOLIC_VELOCITY_THRESHOLD", 1.0, 4.0)
        p["VWAP_CROSS_HWM_PCT"] = trial.suggest_float("VWAP_CROSS_HWM_PCT", 0.3, 2.0)
        return objective
"""


def test_source_extractor_reports_a_new_dim_not_a_hardcoded_six() -> None:
    """If the extractor were a hardcoded 6-set it could NEVER detect a 7th dim.

    Feed synthetic source with a 7th suggest_* — the extractor must return 7,
    including NEW_UNDEMOED_DIM. This is what makes the drift-guard bite.
    """
    probe = _require_probe()
    names = frozenset(
        probe._extract_suggest_names_from_source(_SYNTH_SOURCE_SEVEN_DIMS, "run_autotuner")
    )
    assert "NEW_UNDEMOED_DIM" in names, (
        "extractor did not detect the 7th suggest_* dim in synthetic source — it "
        "is not genuinely parsing the search space (a hardcoded set can't grow)."
    )
    assert len(names) == 7, f"expected 7 suggest_* dims in synthetic source, got {sorted(names)}"


def test_source_extractor_scopes_to_named_function_excludes_calibration_sweep() -> None:
    """Scoping proof: extracting run_autotuner must NOT pull run_calibration_sweep's dims.

    The calibration objective suggests only 2 dims under a DIFFERENT enclosing
    function; the enumerator must not conflate the two search spaces.
    """
    probe = _require_probe()
    run_at = frozenset(
        probe._extract_suggest_names_from_source(_SYNTH_SOURCE_SEVEN_DIMS, "run_autotuner")
    )
    calsweep = frozenset(
        probe._extract_suggest_names_from_source(_SYNTH_SOURCE_SEVEN_DIMS, "run_calibration_sweep")
    )
    assert "NEW_UNDEMOED_DIM" in run_at and "NEW_UNDEMOED_DIM" not in calsweep, (
        "extractor is not scoping to the named enclosing function — it must read "
        "ONLY the requested function's objective closure."
    )
    assert calsweep == frozenset({"PARABOLIC_VELOCITY_THRESHOLD", "VWAP_CROSS_HWM_PCT"}), (
        f"calibration-sweep scoping returned {sorted(calsweep)}, expected the 2 "
        "calibration dims only."
    )


# ===========================================================================
# AC-2 — per-dim WALK-FORWARD objective variance (all 6 dims).
# ===========================================================================


@pytest.mark.parametrize("dim", sorted(SWEPT_DIMS))
def test_dim_produces_nonzero_walkforward_objective_variance(dim: str) -> None:
    """Sweeping this dim over its bar-derived fixture MUST move the objective.

    A dim whose swept values all yield the same objective is either inert-wired
    or unexercised by the fixture — both are defects this smoke exists to catch.
    """
    probe = _require_probe()
    result = probe.walkforward_dim_sweep(dim)
    objectives = dict(result.objectives)
    assert len(objectives) >= 2, (
        f"[{dim}] sweep must cover >=2 distinct in-range values (AC-2); got {sorted(objectives)}."
    )
    span = _distinct_objectives_span(objectives)
    assert span > _MIN_MEANINGFUL_OBJECTIVE_DELTA, (
        f"[{dim}] walk-forward objective did NOT vary across the sweep "
        f"(objectives={objectives}, span={span!r} <= "
        f"{_MIN_MEANINGFUL_OBJECTIVE_DELTA!r}). This dim is objective-inert at "
        "walk-forward — Optuna would tune it as noise applied to live money."
    )


@pytest.mark.parametrize("dim", sorted(SWEPT_DIMS))
def test_dim_variance_assertion_collapses_when_dim_forced_inert(dim: str) -> None:
    """NON-VACUITY CRUX: prove the AC-2 assertion CAN fail.

    force_inert=True pins the swept dim to a single baseline value for every
    sweep point (its value no longer reaches the objective). The objective must
    then COLLAPSE to one distinct value — demonstrating that the AC-2 variance
    assertion above is a real gate, not a tautology that passes regardless.
    """
    probe = _require_probe()
    result = probe.walkforward_dim_sweep(dim, force_inert=True)
    objectives = dict(result.objectives)
    span = _distinct_objectives_span(objectives)
    assert span <= _MIN_MEANINGFUL_OBJECTIVE_DELTA, (
        f"[{dim}] with the dim FORCED INERT the objective still varied "
        f"(objectives={objectives}, span={span!r}). Either force_inert does not "
        "actually pin the dim, or the observed variance in the live sweep comes "
        "from something OTHER than this dim — the AC-2 gate would be vacuous. "
        "The sweep's variance must be attributable to the dim under test."
    )


# ===========================================================================
# AC-3 — the fixture provably FIRES each dim's decision codepath.
# ===========================================================================


@pytest.mark.parametrize("dim", sorted(SWEPT_DIMS))
def test_dim_decision_codepath_actually_fires_in_fixture(dim: str) -> None:
    """A dim whose branch never executes cannot yield honest variance.

    The probe instruments the replay (counting delegates on the exact
    math_engine primitive each dim feeds) and reports codepath_fires. Zero fires
    means the fixture never exercised the dim — a vacuous zero, not a real
    negative. This is the guard against "the objective happened to vary for an
    unrelated reason."
    """
    probe = _require_probe()
    result = probe.walkforward_dim_sweep(dim)
    assert int(result.codepath_fires) > 0, (
        f"[{dim}] the dim's decision codepath NEVER fired across the sweep "
        f"(codepath_fires={result.codepath_fires!r}). The fixture does not "
        "exercise this dim — fix the fixture so the branch arms/fires at least "
        "once; never relax the variance assertion to make it pass."
    )


# ===========================================================================
# AC-4 — determinism: seeded, two-run byte-identical.
# ===========================================================================


@pytest.mark.parametrize("dim", sorted(SWEPT_DIMS))
def test_walkforward_sweep_is_deterministic(dim: str) -> None:
    """Two sweeps on the same SHA must produce identical objectives per point.

    Every stochastic source (the replay MC seed via derive_cycle_mc_seed, numpy)
    is pinned; no wall-clock, no network, no live API. A non-deterministic sweep
    makes the variance number un-auditable.
    """
    probe = _require_probe()
    first = dict(probe.walkforward_dim_sweep(dim).objectives)
    second = dict(probe.walkforward_dim_sweep(dim).objectives)
    assert first == second, (
        f"[{dim}] walk-forward sweep is not deterministic: run1={first}, "
        f"run2={second}. Seed every stochastic source so the objective per swept "
        "point is byte-identical across runs."
    )


# ===========================================================================
# AC-5 — bounded SMOKE: never the production trial floor, no run_autotuner.
# ===========================================================================


def test_sweep_budget_is_bounded_not_production_scale() -> None:
    """A SMOKE, not a 500-trial production walk-forward.

    The probe must declare a small day/value budget and must never invoke the
    production trial floor. Coverage of all dims is non-negotiable; speed is the
    tunable (plan Edge Cases: budget blowout).
    """
    probe = _require_probe()
    # Generous ceilings — the point is "clearly a smoke", not a specific number.
    assert 2 <= int(probe.SWEEP_VALUES_PER_DIM) <= 8, (
        f"SWEEP_VALUES_PER_DIM={probe.SWEEP_VALUES_PER_DIM!r} — a bounded smoke "
        "sweeps a small number (>=2) of in-range values per dim."
    )
    assert int(probe.SWEEP_MAX_DAYS) <= 60, (
        f"SWEEP_MAX_DAYS={probe.SWEEP_MAX_DAYS!r} — a bounded smoke uses a short "
        "bar-derived window, not a full production history."
    )


def test_sweep_does_not_invoke_full_run_autotuner(monkeypatch) -> None:
    """The smoke scores via run_simulation, NEVER the full run_autotuner study.

    run_autotuner would spin OPTUNA_N_TRIALS_PRODUCTION (500) trials + CPCV +
    BHY + PBO — the opposite of a bounded smoke. Poison run_autotuner: if the
    sweep touches it, this raises.
    """
    probe = _require_probe()

    def _poison(*_a, **_k):
        raise AssertionError(
            "walkforward_dim_sweep invoked autotuner.run_autotuner — the bounded "
            "smoke must score via run_simulation, never the production study."
        )

    monkeypatch.setattr(autotuner, "run_autotuner", _poison)
    # Any one dim suffices to prove the code path avoids run_autotuner.
    probe.walkforward_dim_sweep(sorted(SWEPT_DIMS)[0])


# ===========================================================================
# CONFIG-ROBUSTNESS (r3a-test sufficiency review of the (a) GREEN diff; PM
# ruling 2026-07-18). The whole (a) certification must hold UNDER THE CONFIG
# THE RETUNE ACTUALLY RUNS. The replay's action phase + VWAP open-window grace
# are gated by `alpha_bot_execution.EXECUTION_START_TIME`:
#   - code default (09:30, alpha_bot_execution.py:71 os.getenv fallback) — what
#     conftest's autouse `_pin_execution_start_time_to_code_default` pins tests to.
#   - droplet PRODUCTION (9:35, the worktree .env value) — the config the R3-d
#     retune runs `run_autotuner` under on the droplet.
# The original fixtures front-load their discriminating ticks at 09:30-09:34
# (parabolic/TP) or inside the 9:35 open-window grace (VWAP), so the ENTIRE
# sensitivity proof goes all-dead (span=0, fires=0 for every dim) at 9:35 —
# a certification that evaporates under production config is not a certification
# (memory: prove each factor varies UNDER THE REAL CONFIG). These tests pin
# variance + fires at BOTH start-times; 9:35 is the load-bearing case and is
# RED until the fixtures are re-timed to clear action-start + the 15-min grace
# at both (discriminating ticks at ~10:00+). Each param OVERRIDES the conftest
# 09:30 pin via monkeypatch (an in-test monkeypatch wins over the autouse one
# and is reverted at teardown).
# ===========================================================================

# Config values (NOT producer-computed): the two start-times the (a) proof must
# survive. Sourced above — code default vs the droplet .env production value.
_CODE_DEFAULT_EXECUTION_START = "09:30"
_DROPLET_PRODUCTION_EXECUTION_START = "9:35"


@pytest.mark.parametrize(
    "start_time",
    [_CODE_DEFAULT_EXECUTION_START, _DROPLET_PRODUCTION_EXECUTION_START],
)
@pytest.mark.parametrize("dim", sorted(SWEPT_DIMS))
def test_dim_variance_and_fire_hold_under_retune_execution_start_time(
    dim: str, start_time: str, monkeypatch
) -> None:
    """The (a) sensitivity proof must be CONFIG-ROBUST, not an artifact of the
    test-pinned 09:30. At the droplet-production 9:35 (the config R3-d runs
    under) each dim must STILL show non-zero walk-forward variance AND fire its
    codepath — otherwise the certification the retune leans on is dead under the
    config the retune actually uses.
    """
    import alpha_bot_execution

    # Override the conftest autouse 09:30 pin to the parametrized start-time.
    monkeypatch.setattr(alpha_bot_execution, "EXECUTION_START_TIME", start_time)

    probe = _require_probe()
    result = probe.walkforward_dim_sweep(dim)
    objectives = dict(result.objectives)
    span = _distinct_objectives_span(objectives)

    assert span > _MIN_MEANINGFUL_OBJECTIVE_DELTA, (
        f"[{dim} @ EXECUTION_START_TIME={start_time}] walk-forward objective did "
        f"NOT vary (objectives={objectives}, span={span!r}). The dim's sensitivity "
        "proof is an artifact of the test-pinned 09:30 and is dead under this "
        "start-time. Re-time the fixture's discriminating ticks past the "
        "action-start + 15-min VWAP grace at BOTH start-times (~10:00+)."
    )
    assert int(result.codepath_fires) > 0, (
        f"[{dim} @ EXECUTION_START_TIME={start_time}] the dim's decision codepath "
        f"never fired (codepath_fires={result.codepath_fires!r}) — the "
        "discriminating ticks fall before the action phase or inside the VWAP "
        "open-window grace at this start-time. Re-time them to ~10:00+ so they "
        "clear action-start + the 15-min grace at both 09:30 and 9:35."
    )
