"""R3-a deliverable (a) — walk-forward objective-variance probe.

Proves the production Optuna objective (`autotuner.run_autotuner`'s per-trial
`objective` closure) is genuinely sensitive to EVERY tuned search-space
dimension at the walk-forward level, and that the non-vacuity control
(`force_inert`) can actually fail the variance assertion. Off-execution-path,
D-1 never-raises-by-design (bounded, deterministic, no live I/O) — consumed
ONLY by `tests/autotuner/test_r3a_walkforward_variance_all_dims.py`, never by
`alpha_bot_execution.py` or any live codepath.

RED CONTRACT: built to `.claude/tdd-handoff.md` (R3-a deliverable (a)), blind
to `feature-plans/math-r3a-checklist.md`. Scores via `autotuner.run_simulation`
over bar-derived history built through the REAL `synthetic_history.build_replay_day`
pipeline — the same one `tests/autotuner/test_ac7_inert_dims_objective_variance_smoke.py`
Section 3 uses — never the full `autotuner.run_autotuner` study.

DETERMINISM (AC-4): no extra seeding is needed here. `build_replay_day` derives
its Monte-Carlo seed deterministically from `sym_id`+`date_str`
(`math_engine.derive_cycle_mc_seed`), and every per-tick primitive
(`_replay_exit_tick` and everything it calls) is pure. Fixed `sym_id`/dates/
bars therefore yield byte-identical objectives across repeated calls.

CODEPATH-FIRE FIDELITY: `walkforward_dim_sweep`'s codepath-fire count is read
from `autotuner.replay_exit_sequence` — the SAME per-tick core
(`_replay_exit_tick`) `run_simulation` scores with, not a second simulation.
`replay_exit_sequence` does not accept an `exit_confirm_ticks` override (by
design — see its docstring) and always uses the module default
(`math_engine.EXIT_CONFIRM_TICKS`). `run_simulation` instead resolves a
regime-conditional `exit_confirm_ticks` per simulated day
(`_replay_resolve_regime_exit_ticks`), which could in principle diverge from
the default. Every fixture below uses EXACTLY ONE scoring date per symphony
(`len(dates) == 1`), so `_replay_resolve_regime_exit_ticks` always sees an
empty trailing-date window (`sorted_dates[:0]`) -- `regime_classifier.
classify_regime([])` returns None -> `apply_regime_exit_adjustment` leaves
`EXIT_CONFIRM_TICKS` unchanged. `run_simulation`'s actual confirm-tick count
is therefore always the same module default `replay_exit_sequence` uses, so
the fire-trace is faithful to what was scored. (`TAKE_PROFIT_MC_PCT`'s fixture
reuses AC-7's proven 3-day fixture and is the one exception to the
single-date rule -- but its fire-check reads `tp_armed`, which is gated by
`compute_tp_confirmation`'s own `TP_CONFIRM_TICKS` constant, never by the
regime-conditional `exit_confirm_ticks` -- so the divergence is structurally
irrelevant to that dim's fire-check.)

GRACE-WINDOW TRAP + CONFIG ROBUSTNESS (why every fixture pads
`_NEUTRAL_PAD_TICKS` (30) neutral ticks before its discriminating ticks):
`_replay_in_open_window_grace` forces `is_vwap_broken`/`is_vwap_bleed_broken`
to False for every tick inside `[EXECUTION_START_TIME, EXECUTION_START_TIME +
VWAP_OPEN_WINDOW_GRACE_MINUTES)` (15 minutes by default), and
`_replay_in_action_phase` skips EVERY check (trailing-stop, TP, para-arm, VWAP
alike) entirely before `EXECUTION_START_TIME`. Both gates are keyed off
`alpha_bot_execution.EXECUTION_START_TIME`, which is operator-configurable
(env `EXECUTION_START_TIME`, default "09:30" -- the value the test-suite
conftest pins; the droplet's PRODUCTION `.env` sets it to "9:35", the config
the R3-d retune actually runs `run_autotuner` under). A fixture whose
discriminating ticks sit at tick_idx 0-14 proves sensitivity only under the
code default -- at "9:35" the action-phase gate alone (offset 5 min) skips a
tick_idx-0 discriminating tick outright, and the grace window shifts to
`[5, 20)`, swallowing a 15-tick VWAP pad entirely. Every fixture below starts
its discriminating ticks at tick_idx `_NEUTRAL_PAD_TICKS` (10:00 ET) --
comfortably past both gates at 09:30, at 9:35, and at any other plausible
operator start-time -- so the sensitivity proof holds under the config the
retune actually runs under, not just the test-pinned default (memory: prove
each factor varies UNDER THE REAL CONFIG).
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable
from dataclasses import dataclass

import autotuner
import synthetic_history

# ---------------------------------------------------------------------------
# AC-5: bounded-smoke budget declarations. Every fixture below sweeps exactly
# SWEEP_VALUES_PER_DIM values and uses at most SWEEP_MAX_DAYS scoring dates
# (TAKE_PROFIT_MC_PCT's 3-day AC-7-derived fixture is the max; every other
# dim uses a single day). Never the OPTUNA_N_TRIALS_PRODUCTION floor, never
# autotuner.run_autotuner.
# ---------------------------------------------------------------------------
SWEEP_VALUES_PER_DIM = 2
SWEEP_MAX_DAYS = 3


def production_tuned_dims() -> frozenset[str]:
    """AC-1: the tuned-dim enumeration authority IS the production constant."""
    return frozenset(autotuner.OPTUNA_SEARCH_SPACE_KEYS)


def _extract_suggest_names_from_source(source: str, enclosing_func: str) -> frozenset[str]:
    """Pure AST seam: `trial.suggest_*("<NAME>", ...)` literals scoped to one
    named enclosing function's subtree (drift-guard, AC-1).

    Scoping is structural: only the AST subtree of the FIRST top-level (or
    nested) FunctionDef/AsyncFunctionDef named ``enclosing_func`` is walked --
    a sibling function (e.g. run_calibration_sweep) is never visited, so its
    suggest_* calls are never collected. Genuinely source-derived: a 7th
    suggest_* call anywhere in the target function's subtree is picked up
    without any hardcoded name list.
    """
    tree = ast.parse(source)
    target: ast.AST | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == enclosing_func:
            target = node
            break
    if target is None:
        return frozenset()

    names: set[str] = set()
    for node in ast.walk(target):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr.startswith("suggest_")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.add(node.args[0].value)
    return frozenset(names)


def suggest_names_in_run_autotuner_objective() -> frozenset[str]:
    """AC-1 drift-guard: the REAL suggest_* names inside run_autotuner's
    objective closure, read live from autotuner.py's own source."""
    source = inspect.getsource(autotuner)
    return _extract_suggest_names_from_source(source, "run_autotuner")


@dataclass(frozen=True)
class DimSweepResult:
    """Result of one `walkforward_dim_sweep` call.

    objectives: swept value -> `autotuner.run_simulation` objective.
    codepath_fires: count of ticks (across every scoring date and swept
        value) where this dim's decision codepath actually engaged, per
        `_DimFixture.fire_predicate` on the `replay_exit_sequence` trace.
    """

    objectives: dict[float | int, float]
    codepath_fires: int


@dataclass(frozen=True)
class _DimFixture:
    """Everything walkforward_dim_sweep needs to score + fire-check one dim.

    ``bars_by_date``: date -> ordered list of (close, vwap) pairs for the
    single held ticker "AAA" (allocation 1.0). close and vwap are independent
    per-tick inputs to `synthetic_history.build_replay_day` -- vwap == close
    keeps the VWAP checks structurally inert (weighted_vwap_diff == 0);
    vwap != close is what actually exercises the 3 VWAP dims (AC-7's existing
    helper always sets vwap == close, which is why it has no VWAP coverage).
    """

    sym_id: str
    dates: tuple[str, ...]
    bars_by_date: dict[str, list[tuple[float, float]]]
    hist_data_up_to_yesterday: dict
    yesterday_close: float
    spy_today: float
    baseline_params: dict
    sweep_values: tuple[float | int, ...]
    baseline_value: float | int
    fire_predicate: Callable[[list[dict]], int]


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

_TICKER = "AAA"

# "Never trips" defaults for dims not under test in a given fixture -- mirrors
# tests/autotuner/test_ac7_inert_dims_objective_variance_smoke.py's
# _BASE_PARAMS philosophy (large/inert values for every dim except the one
# being swept).
_INERT_BASELINE_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 99.0,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 999,
    "PARABOLIC_VELOCITY_THRESHOLD": 4.0,
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}


def _short_hist_up_to_yesterday(n_days: int = 10) -> dict:
    """<39 raw days -> run_monte_carlo's eligible-pool floor (MC_MIN_HISTORY_DAYS
    + MC_VOL_WINDOW_DAYS - 1 == 39) is never met -> mc_prob is deterministically
    the insufficient-history sentinel (None) on every tick, so MA-10's fail-open
    arms the protective stop unconditionally -- decoupling a fixture from any
    Monte-Carlo noise. <20 days also makes calculate_20d_vol degenerate (0.0),
    which compute_active_trailing_stop falls back from via VOL_FALLBACK (safe
    for the parabolic/squeeze/VWAP-threshold dims, which don't depend on vol)."""
    out = {}
    for i in range(1, n_days + 1):
        date = f"2026-01-{i:02d}"
        out[date] = {"SPY": {"daily_ret": 0.0}, _TICKER: {"daily_ret": 0.0}}
    return out


def _real_vol_hist_up_to_yesterday(n_days: int = 25) -> dict:
    """>=LOOKBACK_DAYS(20) so calculate_20d_vol is genuinely non-zero (needed
    ONLY for VWAP_BLEED_MULTIPLIER -- compute_vwap_bleed_arm_threshold has no
    vol fallback, so a degenerate vol=0.0 would clamp the arm threshold to
    -0.5 regardless of the swept multiplier, falsely making that dim inert).
    Still <39 raw days, so MC stays insufficient (armed-state stays simple,
    identical fail-open reasoning as _short_hist_up_to_yesterday). Alternating
    +/-2% AAA daily returns give an exactly-known population stdev of 2.0
    (mean 0, |deviation| == 2 every day)."""
    out = {}
    for i in range(1, n_days + 1):
        date = f"2026-01-{i:02d}"
        ret = 0.02 if i % 2 == 0 else -0.02
        out[date] = {"SPY": {"daily_ret": 0.0}, _TICKER: {"daily_ret": ret}}
    return out


def _build_history_data(fixture: _DimFixture) -> dict:
    """Build multi-day history_data via the REAL synthetic_history.build_replay_day
    -- generalizes test_ac7_...smoke.py's _build_wf_history_data to independent
    (close, vwap) pairs per tick (AC-7's helper always sets vwap == close).

    Independent of `params` -- build_replay_day only derives raw tick data
    (return/mc_prob/vol/vwap_diff/valid_vwap_weight); the swept strategy
    params are consumed later, by _replay_exit_tick via run_simulation /
    replay_exit_sequence. Built once per fixture and reused for every swept
    value.
    """
    holdings = [{"ticker": _TICKER, "allocation": 1.0}]
    yesterday_closes = {_TICKER: fixture.yesterday_close}
    out: dict[str, dict] = {fixture.sym_id: {}}
    for date in fixture.dates:
        bars = fixture.bars_by_date[date]
        timestamps = [f"{date}T09:{30 + i:02d}:00Z" for i in range(len(bars))]
        intraday_by_date = {
            date: {
                _TICKER: [
                    {"t": t, "c": c, "vwap": v} for t, (c, v) in zip(timestamps, bars, strict=True)
                ]
            }
        }
        out[fixture.sym_id][date] = synthetic_history.build_replay_day(
            sym_id=fixture.sym_id,
            date_str=date,
            holdings=holdings,
            intraday_by_date=intraday_by_date,
            timestamps=timestamps,
            hist_data_up_to_yesterday=fixture.hist_data_up_to_yesterday,
            yesterday_closes=yesterday_closes,
            spy_today=0.0,
        )
    return out


def _closes(*returns_pct: float, y_close: float = 100.0) -> list[float]:
    """Convert a sequence of percent returns (each independently relative to
    the FIXED prior-day close -- build_replay_day recomputes every tick's
    return against the SAME yesterday_closes entry, never the previous tick)
    into the corresponding close prices."""
    return [y_close * (1.0 + r / 100.0) for r in returns_pct]


# Every fixture's discriminating ticks start at this tick_idx (10:00 ET) --
# past the action-phase gate and the 15-min VWAP grace at BOTH the code
# default (09:30) and the droplet-production (9:35) EXECUTION_START_TIME, with
# headroom for other plausible operator configs (module docstring, "GRACE-
# WINDOW TRAP + CONFIG ROBUSTNESS").
_NEUTRAL_PAD_TICKS = 30


def _neutral_bars(
    n: int = _NEUTRAL_PAD_TICKS, *, price: float = 100.0
) -> list[tuple[float, float]]:
    """n ticks of flat, non-qualifying (close, vwap) pairs: zero return, zero
    vwap_diff. Pads every fixture's discriminating ticks past the action-phase
    gate and the VWAP open-window grace, at every EXECUTION_START_TIME the
    proof must survive (module docstring)."""
    return [(price, price)] * n


# ---------------------------------------------------------------------------
# Dim 1/6: TAKE_PROFIT_MC_PCT -- reuses AC-7 Section 3's proven bar-derived
# fixture verbatim (real per-tick mc_prob discriminates the [5, 10) TP-arm
# boundary on 2026-04-07's opening tick).
# ---------------------------------------------------------------------------

_TP_WF_HISTORY_ROWS = [
    ("2026-01-01", 0.008699, -0.003372),
    ("2026-01-02", -0.011317, -0.004389),
    ("2026-01-03", -0.004296, -0.012604),
    ("2026-01-04", -0.000152, 0.012547),
    ("2026-01-05", -0.008223, 0.002952),
    ("2026-01-06", 0.004126, -0.015204),
    ("2026-01-07", -0.008957, -0.014317),
    ("2026-01-08", -0.003915, -0.013142),
    ("2026-01-09", -0.006295, 0.007542),
    ("2026-01-10", 0.004041, -0.010652),
    ("2026-01-11", -0.001306, 0.016507),
    ("2026-01-12", -0.003925, -0.007241),
    ("2026-01-13", -0.007962, -0.006551),
    ("2026-01-14", -0.003467, -0.004219),
    ("2026-01-15", -0.0043, -0.007718),
    ("2026-01-16", -0.007974, 0.004053),
    ("2026-01-17", -0.000363, -0.002374),
    ("2026-01-18", 0.007428, 0.008943),
    ("2026-01-19", -0.000522, -0.013318),
    ("2026-01-20", 0.002671, 0.016431),
    ("2026-01-21", -0.000467, 0.016775),
    ("2026-01-22", 0.009194, 0.009092),
    ("2026-01-23", 0.007846, 0.006637),
    ("2026-01-24", -0.000806, -0.010847),
    ("2026-01-25", -0.006622, 0.004824),
    ("2026-01-26", -0.002043, 0.014662),
    ("2026-01-27", -0.006684, -0.010073),
    ("2026-01-28", 0.005139, 0.00115),
    ("2026-02-01", -0.009975, -0.007617),
    ("2026-02-02", 0.006892, 0.001378),
    ("2026-02-03", -0.009569, -0.019655),
    ("2026-02-04", -0.011437, 0.002203),
    ("2026-02-05", 0.002328, 0.003094),
    ("2026-02-06", 0.007038, -0.0099),
    ("2026-02-07", 0.007598, -0.000543),
    ("2026-02-08", -0.006786, -0.003091),
    ("2026-02-09", -0.006137, 0.012108),
    ("2026-02-10", 0.007932, 0.012117),
    ("2026-02-11", 0.011648, 0.000198),
    ("2026-02-12", -0.002905, -0.015545),
    ("2026-02-13", -0.000372, 0.018246),
    ("2026-02-14", -0.006657, -0.005041),
    ("2026-02-15", -0.004881, 0.001285),
    ("2026-02-16", 0.004643, 0.014488),
    ("2026-02-17", -0.01139, 0.016439),
]


def _tp_mc_pct_fixture() -> _DimFixture:
    hist = {
        d: {"SPY": {"daily_ret": s}, _TICKER: {"daily_ret": a}} for d, s, a in _TP_WF_HISTORY_ROWS
    }
    # Reuses AC-7's exact verified closes (101.5/80.0/78.0/76.0/74.0, and
    # 101.65 -- not 101.5 -- on 04-07's opening tick): mc_prob is a real kNN
    # bootstrap result, not something derivable from a target percent, so the
    # discriminating tick's close is reused byte-for-byte from the proven
    # fixture rather than re-derived. Each day is prefixed with
    # _NEUTRAL_PAD_TICKS neutral ticks (config robustness -- module docstring)
    # -- build_replay_day's per-tick mc_prob is a pure function of THAT tick's
    # own close-derived lpc + hist_data_up_to_yesterday + the day-keyed MC
    # seed, with no cross-tick memory, so the discriminating tick's mc_prob is
    # bit-identical whether it sits at tick_idx 0 or _NEUTRAL_PAD_TICKS.
    bars_by_date = {
        "2026-04-06": _neutral_bars() + [(c, c) for c in [101.5, 80.0, 78.0, 76.0, 74.0]],
        "2026-04-07": _neutral_bars() + [(c, c) for c in [101.65, 80.0, 78.0, 76.0, 74.0]],
        "2026-04-08": _neutral_bars() + [(c, c) for c in [101.5, 80.0, 78.0, 76.0, 74.0]],
    }

    def fire(trace: list[dict]) -> int:
        return sum(1 for t in trace if t["tp_armed"])

    return _DimFixture(
        sym_id="sym-a-tp-mc-pct",
        dates=("2026-04-06", "2026-04-07", "2026-04-08"),
        bars_by_date=bars_by_date,
        hist_data_up_to_yesterday=hist,
        yesterday_close=100.0,
        spy_today=0.4,
        baseline_params=dict(_INERT_BASELINE_PARAMS),
        sweep_values=(5.0, 10.0),
        baseline_value=5.0,
        fire_predicate=fire,
    )


# ---------------------------------------------------------------------------
# Dim 2/6: PARABOLIC_VELOCITY_THRESHOLD -- short (MC-insufficient) history so
# MA-10 fail-open arms the stop unconditionally, isolating the velocity-arm
# effect from MC noise. _NEUTRAL_PAD_TICKS neutral ticks (config robustness),
# then a velocity spike (0.0 -> 3.0) and a 3-tick pullback to 2.3; a LOW
# threshold (1.0) arms para on the spike (velocity 3.0 >= 1.0), a HIGH
# threshold (4.0) does not (3.0 < 4.0) -- changing whether MAX_PARABOLIC_SQUEEZE
# (fixed baseline 0.5) tightens active_stop_dist enough for the pullback to
# confirm a Trailing Stop exit. The pullback (2.3, not the tighter margin a
# tick_idx-0 fixture could use) is calibrated for the MUCH tighter
# dynamic_multiplier/dynamic_min_stop decay a late-day tick_idx produces
# (time_ratio = tick_idx / (n_ticks - 1) is close to 1.0 this late in a padded
# day), leaving a comfortable margin between the armed (confirms) and unarmed
# (never confirms) outcomes.
# ---------------------------------------------------------------------------


def _parabolic_velocity_threshold_fixture() -> _DimFixture:
    bars = _neutral_bars() + [(c, c) for c in _closes(3.0, 2.3, 2.3, 2.3)]
    baseline = dict(_INERT_BASELINE_PARAMS)
    baseline["MAX_PARABOLIC_SQUEEZE"] = 0.5

    def fire(trace: list[dict]) -> int:
        return sum(1 for t in trace if t["para_armed"])

    return _DimFixture(
        sym_id="sym-a-para-vel",
        dates=("2026-05-01",),
        bars_by_date={"2026-05-01": bars},
        hist_data_up_to_yesterday=_short_hist_up_to_yesterday(),
        yesterday_close=100.0,
        spy_today=0.0,
        baseline_params=baseline,
        sweep_values=(1.0, 4.0),
        baseline_value=4.0,
        fire_predicate=fire,
    )


# ---------------------------------------------------------------------------
# Dim 3/6: MAX_PARABOLIC_SQUEEZE -- same padded velocity-spike shape, but
# PARABOLIC_VELOCITY_THRESHOLD fixed low (1.0) so both swept squeeze values
# arm para IDENTICALLY on the spike; the pullback (to 2.6 -- widened from a
# tick_idx-0 fixture's margin for the same late-day-decay reason as the
# velocity-dim fixture above) confirms a Trailing Stop under a TIGHT squeeze
# (0.1) but not under a WIDE one (0.8).
# ---------------------------------------------------------------------------


def _max_parabolic_squeeze_fixture() -> _DimFixture:
    bars = _neutral_bars() + [(c, c) for c in _closes(3.0, 2.6, 2.6, 2.6)]
    baseline = dict(_INERT_BASELINE_PARAMS)
    baseline["PARABOLIC_VELOCITY_THRESHOLD"] = 1.0

    def fire(trace: list[dict]) -> int:
        return sum(1 for t in trace if t["exit_reason"] == "Trailing Stop")

    return _DimFixture(
        sym_id="sym-a-max-squeeze",
        dates=("2026-05-01",),
        bars_by_date={"2026-05-01": bars},
        hist_data_up_to_yesterday=_short_hist_up_to_yesterday(),
        yesterday_close=100.0,
        spy_today=0.0,
        baseline_params=baseline,
        sweep_values=(0.1, 0.8),
        baseline_value=0.5,
        fire_predicate=fire,
    )


# ---------------------------------------------------------------------------
# Dim 4/6: VWAP_CROSS_HWM_PCT -- NEW bar-derived coverage (AC-7's helper
# always sets vwap == close, so this dim has never been walk-forward-tested).
# _NEUTRAL_PAD_TICKS padding ticks (config robustness), then a tick that sets
# HWM=1.5% with vwap above close (weighted_vwap_diff < 0), followed by 3 ticks
# holding at +1.3% (< HWM) with the same vwap divergence -- confirms VWAP
# Breakdown (System A) at LOW threshold (1.0, since safe_hwm 1.5 >= 1.0) but
# never at HIGH threshold (2.0, since 1.5 < 2.0). The trailing-stop safety
# margin here is asymptotically invariant to the pad length: the tightest
# tick's active_stop_dist depends only on tick_idx/(n_ticks-1) == 1.0 at the
# FINAL tick, unchanged regardless of how many neutral ticks precede it.
# ---------------------------------------------------------------------------


def _vwap_cross_hwm_pct_fixture() -> _DimFixture:
    neutral = _neutral_bars()
    spike_close = _closes(1.5)[0]
    hold_close = _closes(1.3)[0]
    # vwap set well above close on every discriminating tick -> weighted_vwap_diff
    # is comfortably negative (System A's coverage+sign gate) without depending
    # on the exact close value.
    discriminating = [(spike_close, spike_close * 1.015), *([(hold_close, hold_close * 1.017)] * 3)]
    bars = neutral + discriminating

    def fire(trace: list[dict]) -> int:
        return sum(1 for t in trace if t["exit_reason"] == "VWAP Breakdown")

    return _DimFixture(
        sym_id="sym-a-vwap-cross-hwm",
        dates=("2026-05-02",),
        bars_by_date={"2026-05-02": bars},
        hist_data_up_to_yesterday=_short_hist_up_to_yesterday(),
        yesterday_close=100.0,
        spy_today=0.0,
        baseline_params=dict(_INERT_BASELINE_PARAMS),
        sweep_values=(1.0, 2.0),
        baseline_value=99.0,
        fire_predicate=fire,
    )


# ---------------------------------------------------------------------------
# Dim 5/6: VWAP_BLEED_TICKS -- _NEUTRAL_PAD_TICKS padding ticks (config
# robustness), then exactly 3 ticks of a -0.55% dip (vwap == close, so System
# A stays inert; VWAP_BLEED_MULTIPLIER fixed at its production-bounds minimum
# 0.5 -> arm threshold clamps to -0.5, so -0.55 qualifies every dip tick
# without ever breaching the trailing stop -- see the module docstring). A LOW
# tick-count (3) confirms VWAP Bleed Cut on the 3rd dip tick; a HIGH one (10)
# never does (only 3 dip ticks exist).
# ---------------------------------------------------------------------------


def _vwap_bleed_ticks_fixture() -> _DimFixture:
    neutral = _neutral_bars()
    dip_close = _closes(-0.55)[0]
    dip = [(dip_close, dip_close)] * 3
    bars = neutral + dip
    baseline = dict(_INERT_BASELINE_PARAMS)
    baseline["VWAP_BLEED_MULTIPLIER"] = 0.5

    def fire(trace: list[dict]) -> int:
        return sum(1 for t in trace if t["exit_reason"] == "VWAP Bleed Cut")

    return _DimFixture(
        sym_id="sym-a-vwap-bleed-ticks",
        dates=("2026-05-03",),
        bars_by_date={"2026-05-03": bars},
        hist_data_up_to_yesterday=_short_hist_up_to_yesterday(),
        yesterday_close=100.0,
        spy_today=0.0,
        baseline_params=baseline,
        sweep_values=(3, 10),
        baseline_value=999,
        fire_predicate=fire,
    )


# ---------------------------------------------------------------------------
# Dim 6/6: VWAP_BLEED_MULTIPLIER -- the one dim needing REAL (non-degenerate)
# volatility: compute_vwap_bleed_arm_threshold has no vol fallback, so a
# degenerate vol=0.0 clamps the arm threshold to -0.5 regardless of the
# multiplier, falsely making this dim inert. 25 days of alternating +/-2% AAA
# returns give a known population stdev of 2.0 (>= LOOKBACK_DAYS=20, still
# < 39 so MC stays insufficient). _NEUTRAL_PAD_TICKS padding ticks (config
# robustness), then 3 ticks of a -1.2% dip: at mult=0.5 the arm threshold
# clamps to -1.0 (dip qualifies, confirms VWAP Bleed Cut); at mult=3.0 it
# clamps to -3.0 (dip never qualifies). VWAP_BLEED_TICKS fixed at its
# production-bounds minimum (3) so the LOW-multiplier case confirms within
# the 3 dip ticks.
# ---------------------------------------------------------------------------


def _vwap_bleed_multiplier_fixture() -> _DimFixture:
    neutral = _neutral_bars()
    dip_close = _closes(-1.2)[0]
    dip = [(dip_close, dip_close)] * 3
    bars = neutral + dip
    baseline = dict(_INERT_BASELINE_PARAMS)
    baseline["VWAP_BLEED_TICKS"] = 3

    def fire(trace: list[dict]) -> int:
        return sum(1 for t in trace if t["exit_reason"] == "VWAP Bleed Cut")

    return _DimFixture(
        sym_id="sym-a-vwap-bleed-mult",
        dates=("2026-05-04",),
        bars_by_date={"2026-05-04": bars},
        hist_data_up_to_yesterday=_real_vol_hist_up_to_yesterday(),
        yesterday_close=100.0,
        spy_today=0.0,
        baseline_params=baseline,
        sweep_values=(0.5, 3.0),
        baseline_value=1.5,
        fire_predicate=fire,
    )


_FIXTURE_BUILDERS: dict[str, Callable[[], _DimFixture]] = {
    "TAKE_PROFIT_MC_PCT": _tp_mc_pct_fixture,
    "PARABOLIC_VELOCITY_THRESHOLD": _parabolic_velocity_threshold_fixture,
    "MAX_PARABOLIC_SQUEEZE": _max_parabolic_squeeze_fixture,
    "VWAP_CROSS_HWM_PCT": _vwap_cross_hwm_pct_fixture,
    "VWAP_BLEED_TICKS": _vwap_bleed_ticks_fixture,
    "VWAP_BLEED_MULTIPLIER": _vwap_bleed_multiplier_fixture,
}


def walkforward_dim_sweep(dim: str, *, force_inert: bool = False) -> DimSweepResult:
    """AC-2/AC-3: sweep `dim` over its bar-derived fixture, scoring via
    `autotuner.run_simulation` (never `autotuner.run_autotuner`).

    force_inert=True pins `dim` to ONE fixed baseline value for EVERY swept
    point -- the swept value never reaches `params[dim]` at all, a genuine
    "ignore the swept param" path (not a special-cased short-circuit). The
    resulting `.objectives` dict keeps the SAME nominal keys as the non-inert
    call but every value is computed from the identical, single pinned param
    set -- so the objectives are byte-identical, not merely close.
    """
    builder = _FIXTURE_BUILDERS.get(dim)
    if builder is None:
        raise ValueError(f"no walk-forward fixture registered for dim {dim!r}")
    fixture = builder()
    history_data = _build_history_data(fixture)
    grace_minutes = autotuner._replay_grace_minutes()

    objectives: dict[float | int, float] = {}
    fires = 0
    last_date = fixture.dates[-1]
    for value in fixture.sweep_values:
        params = dict(fixture.baseline_params)
        params[dim] = fixture.baseline_value if force_inert else value
        objectives[value] = autotuner.run_simulation(
            params, history_data, [fixture.sym_id], last_date, {}
        )
        for date in fixture.dates:
            ticks = history_data[fixture.sym_id][date]
            trace = autotuner.replay_exit_sequence(ticks, params, grace_minutes=grace_minutes)
            fires += fixture.fire_predicate(trace)

    return DimSweepResult(objectives=objectives, codepath_fires=fires)
