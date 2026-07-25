import functools
import glob
import itertools
import json
import logging
import math
import os
import statistics
import time
from datetime import UTC, datetime, timedelta

import numpy as np
import optuna

import acceptance_gate as _acceptance_gate
import database
import math_engine
import regime_classifier
import synthetic_history
from advisors import divergence_explainer as _de
from advisors import overfitting_conscience as _oc
from advisors import spec_critic as _sc

# PERF-007: absolute default for the post-mortem search directory — anchored
# to this file's parent so the glob is CWD-independent. Operators may
# override at call time via the POST_MORTEM_DIR environment variable.
_POST_MORTEM_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_post_mortem_dir() -> str:
    """Return the post-mortem directory to search, honouring POST_MORTEM_DIR.

    Resolution is deferred to call time (not frozen at import) so that
    tests using monkeypatch.setenv("POST_MORTEM_DIR", ...) work correctly.
    """
    override = os.environ.get("POST_MORTEM_DIR")
    if override:
        return override
    return _POST_MORTEM_DIR


def _replay_grace_minutes() -> int:
    """Return the VWAP open-window grace minutes the replay must use.

    The replay SHARES production's single source of truth — it references
    alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES, never re-derives its
    own copy, so a re-tune of that dial flows through to both paths. The
    import is function-local because a top-level `import alpha_bot_execution`
    would be circular (alpha_bot_execution imports autotuner).

    Production suppresses VWAP-Breakdown and VWAP-Bleed-Cut for this many
    minutes after EXECUTION_START_TIME to avoid open-volatility false exits
    (V2, AC-V2.1). The replay's per-tick gate is implemented by
    _replay_in_open_window_grace below.
    """
    import alpha_bot_execution

    return alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES


def _replay_execution_start_time() -> str:
    """Return the EXECUTION_START_TIME ('HH:MM' ET) the replay must use.

    Shares production's single source of truth (alpha_bot_execution.EXECUTION_START_TIME)
    so a non-default operator setting (e.g. '10:30' to avoid open-volatility
    noise) flows through to both paths. AC-5 / N-3: pre-fix the replay's
    grace gate was anchored at tick_idx 0 (session open at 09:30 ET) instead
    of EXECUTION_START_TIME, mis-aligning the replay from production whenever
    EXECUTION_START_TIME was non-default.
    """
    import alpha_bot_execution

    return alpha_bot_execution.EXECUTION_START_TIME


def _replay_squeeze_floor_default() -> float:
    """Return the replay's default MAX_SQUEEZE_FLOOR (R3-c / MA-11, AC-4).

    Shares production's single source of truth (alpha_bot_execution.MAX_SQUEEZE_FLOOR
    — the same attr production's acc_params.get("MAX_SQUEEZE_FLOOR", MAX_SQUEEZE_FLOOR)
    falls back to, alpha_bot_execution.py:1236) so a per-symphony params dict
    missing the key resolves to the IDENTICAL floor on both paths — never a
    replay-local mirror literal. Read live at call time (not cached at import),
    so an operator's env override or a test monkeypatch of the module attr
    reaches the replay exactly as it reaches production. The import is
    function-local because a top-level `import alpha_bot_execution` would be
    circular (alpha_bot_execution imports autotuner).

    Called inline at the single compute_active_trailing_stop call site inside
    _replay_exit_tick — every caller of _replay_exit_tick gets this default
    automatically; there is nothing for a future call site to remember to
    thread through.
    """
    import alpha_bot_execution

    return alpha_bot_execution.MAX_SQUEEZE_FLOOR


def _replay_execution_start_offset_minutes(execution_start_hhmm: str) -> int:
    """Return EXECUTION_START_TIME's minute-bar offset past the 09:30 ET
    session open (tick_idx 0).

    Single source of truth for the ``(h - 9) * 60 + (m - 30)`` formula,
    shared by ``_replay_in_open_window_grace`` (N-3, VWAP-grace suppression
    window) and ``_replay_in_action_phase`` (AC-5/F6, the action-phase gate)
    so the two consumers can never drift apart.
    """
    h, m = execution_start_hhmm.split(":")
    return (int(h) - 9) * 60 + (int(m) - 30)


def _replay_in_open_window_grace(
    tick_idx: int, execution_start_hhmm: str, grace_minutes: int
) -> bool:
    """Return True iff a replay tick falls inside the EXECUTION_START_TIME
    open-window grace interval.

    Mirrors production's is_in_open_window_grace gate. tick_idx 0 corresponds
    to the SESSION OPEN at 09:30 ET (the data-phase loop runs from 09:30
    onward — alpha_bot_execution.py:681); the grace window is the
    grace_minutes-long interval that starts at EXECUTION_START_TIME, which
    sits ``(h - 9) * 60 + (m - 30)`` minute-bars past 09:30. A tick is in
    grace iff its 09:30-anchored offset is in
    ``[start_offset, start_offset + grace_minutes)``.

    Args:
        tick_idx: minute-bar offset since 09:30 ET (the session-open anchor).
        execution_start_hhmm: 'HH:MM' string, matches alpha_bot_execution.EXECUTION_START_TIME.
        grace_minutes: production's VWAP_OPEN_WINDOW_GRACE_MINUTES.

    Returns:
        True iff the tick sits inside [EXECUTION_START_TIME, EXECUTION_START_TIME
        + grace_minutes). AC-5 / N-3 — closes the replay-vs-production
        grace-window misalignment.
    """
    # The session opens at 09:30 ET; tick_idx 0 == 09:30. exec_start sits
    # this many minutes past tick 0.
    start_offset = _replay_execution_start_offset_minutes(execution_start_hhmm)
    return start_offset <= tick_idx < start_offset + grace_minutes


def _replay_in_action_phase(tick_idx: int, execution_start_hhmm: str) -> bool:
    """Return True iff `tick_idx` is at or after EXECUTION_START_TIME's
    session-open-anchored offset — i.e. production's ACTION PHASE would have
    run on this tick.

    AC-5/F6 (DISTINCT from and NOT satisfied by the N-3 VWAP-grace gate
    above): production's entire action phase — para-arm, MC arm/disarm,
    trailing-stop confirm, TP confirm, VWAP checks, and exit firing — never
    runs before EXECUTION_START_TIME (the hard
    ``if current_time < market_open and not force_run: return`` gate,
    alpha_bot_execution.py:951-953). Only the DATA phase (HWM tracking) runs
    unconditionally from the true 09:30 session open
    (alpha_bot_execution.py:876-885). tick_idx < the offset means the action
    phase has not opened yet.
    """
    start_offset = _replay_execution_start_offset_minutes(execution_start_hhmm)
    return tick_idx >= start_offset


# --- PORT-LEVEL REPLAY BLIND SPOT (AC-8 / plan D-C3b) ---
# The autotuner replay (run_simulation / _collect_sim_returns /
# replay_exit_sequence) simulates ONLY the per-symphony altitude — it iterates
# per sym_id and replays each symphony's exit path in isolation. There is no
# port-level altitude replay: no port aggregation, no port-level exit.
# Port-level autotuning is deprecated (Sprint-3 port-level deprecation
# directive); all autotuner code now operates at symphony level only.

# Required keys for a complete Optuna best_params payload.
# MUST be kept in sync with the suggest_* calls in the objective() closure
# below. If any of these keys is missing from study.best_params after
# optimization, the AI proposal is rejected wholesale (no Frankenstein merge)
# and the baseline cascade (fallback -> default) runs.
# Extra keys outside this set are tolerated for forward-compat.
# Note: optuna.logging.set_verbosity is now called inside run_autotuner
# (not module-level) so import does not trigger logging side effects.
# Vars in database.DEFAULT_LOCKED_VARS AND in a symphony's per-symphony
# locked_vars list are excluded from the search space — they retain their
# current values and are never overwritten by trial suggestions.
OPTUNA_SEARCH_SPACE_KEYS = frozenset(
    {
        "TAKE_PROFIT_MC_PCT",
        "VWAP_CROSS_HWM_PCT",
        "VWAP_BLEED_MULTIPLIER",
        "VWAP_BLEED_TICKS",
        "PARABOLIC_VELOCITY_THRESHOLD",
        "MAX_PARABOLIC_SQUEEZE",
    }
)

# NN1 (synthesis hard gate — council §2.5): the following facets MUST NEVER
# appear in OPTUNA_SEARCH_SPACE_KEYS — they are frozen OUTSIDE the search
# space by the spec_bundles registry:
#   - gamma                 (THEORY)
#   - utility_family        (THEORY)
#   - wealth_argument       (THEORY)
#   - generator_family      (STYLIZED_FACT)  [Phase 2]
#   - horizon_convention    (CADENCE)        [Phase 2]
#   - lambda (CVaR budget)  (MANDATE)        [Phase 2]
#   - regime_bucket_thresh  (CALIBRATION)    [Phase 2]
# Adding any of the above to OPTUNA_SEARCH_SPACE_KEYS is a structural NN1
# violation — the Yekutieli c(N) factor would see only the trial-sweep,
# not the spec-facet tour, and the haircut would silently understate its
# effective N. Adding a NEW name here without classifying it in this
# block is a Gate-1 review fail.

# --- NN1 spec-freeze discipline constants (D1) ---
# Single source of truth for the autotuner-side NN1 consumer.
# BACKTEST_SELECTION is the NN1-violation tripwire — present in the enum so
# the violation has a name; never silently as a fallback for unclassifiable rows.
# Reference: council synthesis §2.5, §3.7.
FREEZE_DISCIPLINE_THEORY = "THEORY"
FREEZE_DISCIPLINE_MANDATE = "MANDATE"
FREEZE_DISCIPLINE_STYLIZED_FACT = "STYLIZED_FACT"
FREEZE_DISCIPLINE_POLITIS_WHITE = "POLITIS_WHITE"
FREEZE_DISCIPLINE_CADENCE = "CADENCE"
FREEZE_DISCIPLINE_CALIBRATION = "CALIBRATION"
FREEZE_DISCIPLINE_BACKTEST_SELECTION = "BACKTEST_SELECTION"  # NN1 VIOLATION

# Frozenset of disciplines that do NOT constitute an NN1 violation.
# Default-deny: any freeze_discipline NOT in this set is treated as a violation.
NN1_HONEST_DISCIPLINES: frozenset = frozenset(
    {
        FREEZE_DISCIPLINE_THEORY,
        FREEZE_DISCIPLINE_MANDATE,
        FREEZE_DISCIPLINE_STYLIZED_FACT,
        FREEZE_DISCIPLINE_POLITIS_WHITE,
        FREEZE_DISCIPLINE_CADENCE,
        FREEZE_DISCIPLINE_CALIBRATION,
    }
)

EVIDENCE_SOURCE_THEORY = "THEORY"
EVIDENCE_SOURCE_MANDATE = "MANDATE"
EVIDENCE_SOURCE_STYLIZED_FACT = "STYLIZED_FACT"
EVIDENCE_SOURCE_BACKTEST_SELECTION = "BACKTEST_SELECTION"  # NN1 VIOLATION
EVIDENCE_SOURCE_OOS = "OOS"  # WORSE: frozen-eval peek

# --- Optuna sampler + parallelism env-var constants (OPTUNA-1 / OPTUNA-6) ---
# Source: math re-audit OPTUNA-1/OPTUNA-6 — pin sampler + parallelism via env.
# The run_autotuner main study site reads both values from the environment so
# operators can control reproducibility (seed) and host utilisation (n_jobs)
# without touching production code. Named constants prevent string-literal
# repetition at the call site.
_OPTUNA_SAMPLER_SEED_ENV = "OPTUNA_SAMPLER_SEED"
_OPTUNA_N_JOBS_ENV = "OPTUNA_N_JOBS"
# OPTUNA-2 audit pin: the active pruner family is NOP. Explicit rather than
# relying on Optuna's implicit default (MedianPruner). The objective is
# end-of-trial-scored; the simulation runs to completion with a single scalar
# return — no intermediate step reporting — so any pruner is silently inactive
# today. Pinning NopPruner documents the intent and prevents a future addition
# of intermediate step reporting from silently activating MedianPruner, which
# would censor the trial set consumed by the BHY (Harvey & Liu) haircut and
# invalidate the N_effective additive accounting (both assume the COMPLETE
# trial set). Changing this constant is a methodology change — surface to PM.
ACTIVE_OPTUNA_PRUNER_FAMILY = "NOP"

# --- Optuna trial-count constants (OPTUNA-7) ---
# These values are math-soundness constants, not speed knobs. Changing either
# value is a methodology change that MUST be surfaced to PM before committing.
#
# BHY / Yekutieli c(N) rationale (Harvey & Liu 2015 haircut):
#   The BHY selection-bias haircut uses c(N) = sum(1/k for k in 1..N) as the
#   Yekutieli multiple-testing correction factor. Larger N gives a larger c(N)
#   and therefore a stronger (more conservative) haircut:
#     c(100) ≈ 5.19   c(500) ≈ 6.79   (≈30% larger at production floor)
#   Reducing production_n_trials BELOW the 5x-headroom adequacy line
#   (production >= 5 * floor = 5 * 100 = 500) weakens the haircut materially.
#
# Statistical-stability floor (project rule — see CLAUDE.md Known Gotchas):
#   Minimum n_trials for the TPE sampler to adequately explore the 6-D
#   search space is 100. Below 100 the sampler under-explores and the
#   BHY c(N) factor is materially weaker (c(50) ≈ 4.50 vs c(100) ≈ 5.19).
#   OPTUNA_N_TRIALS_CALIBRATION equals the floor exactly — the calibration
#   sweep IS the floor. OPTUNA_N_TRIALS_PRODUCTION is 5x that floor.
OPTUNA_N_TRIALS_PRODUCTION = (
    500  # production walk-forward main study site; 5x the 100-trial stability floor
)
OPTUNA_N_TRIALS_CALIBRATION = (
    100  # calibration sweep; equals the statistical-stability floor exactly
)


def _build_optuna_sampler_from_env() -> "optuna.samplers.TPESampler":
    """Return TPESampler with seed sourced from env (None when unset).

    Reads _OPTUNA_SAMPLER_SEED_ENV ("OPTUNA_SAMPLER_SEED") from os.environ.
    When set, the study is reproducible across runs on the same host —
    enabling audit and regression comparisons. When unset, seed=None preserves
    the prior wall-clock-seeded behaviour so operators who do not opt in to
    determinism see no change.
    """
    raw = os.environ.get(_OPTUNA_SAMPLER_SEED_ENV)
    seed = int(raw) if raw is not None and raw.strip() else None
    return optuna.samplers.TPESampler(seed=seed)


def _resolve_optuna_n_jobs_from_env() -> int:
    """Return n_jobs from env; falls back to 1 on unset or garbled.

    Reads _OPTUNA_N_JOBS_ENV ("OPTUNA_N_JOBS") from os.environ.
    Default is 1 (NOT os.cpu_count()) because both autotuner study sites use
    SQLite RDBStorage (sqlite:///optuna_studies.db); parallel writes contend on
    the SQLite writer lock and raise 'database is locked' OperationalError.
    Operators with Postgres/MySQL storage backends can opt-in to higher
    parallelism via OPTUNA_N_JOBS=<N>. The risk engine runs on a 1-minute
    cadence — this helper must never raise; garbled values fall back to the
    same safe default.
    """
    raw = os.environ.get(_OPTUNA_N_JOBS_ENV)
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            pass
    return 1


# Optuna search space bounds — named so the search space is inspectable via
# optuna-compare without re-parsing logs, and to satisfy the no-magic-numbers rule.
_SS_TAKE_PROFIT_MC_MIN = 2.0
_SS_TAKE_PROFIT_MC_MAX = 10.0
_SS_VWAP_CROSS_HWM_MIN = 0.5  # production walk-forward bounds; see _SS_VWAP_CROSS_HWM_V1_MIN below for the narrower V1 calibration sweep bounds and asymmetry rationale  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
_SS_VWAP_CROSS_HWM_MAX = 2.5
_SS_VWAP_BLEED_MULT_MIN = 0.5
_SS_VWAP_BLEED_MULT_MAX = 3.0
_SS_VWAP_BLEED_TICKS_MIN = 3
_SS_VWAP_BLEED_TICKS_MAX = 30
_SS_PARA_VEL_MIN = 1.0
_SS_PARA_VEL_MAX = 4.0
_SS_MAX_PARA_SQUEEZE_MIN = 0.1
_SS_MAX_PARA_SQUEEZE_MAX = 0.8

# V1 calibration sweep — asymmetric VWAP_CROSS_HWM_PCT bounds (lower expands below production; upper narrows below production).  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
# Lower: 0.3 (vs production 0.5) — 3-tick confirm gate (math_engine.py) prevents spurious
# single-tick exits at this level; gives the sweep more room to find the true optimum.
# Upper: 2.0 (vs production 2.5; ~2σ typical daily return) — above this System A is effectively disabled  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
# for normal sessions, making calibration unreliable.
_SS_VWAP_CROSS_HWM_V1_MIN = 0.3
_SS_VWAP_CROSS_HWM_V1_MAX = 2.0

# Minimum history days required before running the calibration sweep for a symphony.
# Below this threshold the fold partitioning produces validation windows too small
# to give the Sortino objective meaningful signal.
_CALSWEEP_MIN_HISTORY_DAYS = 125

# Trigger-frequency flag multiplier for the AC-7 operator review gate.
# A proposed parameter set that fires System A exits more than this multiple of
# the current deployment rate is flagged for manual review before any per-symphony
# deploy. Threshold is operator-stated (>2× sensitivity boundary). Source: PM
# methodology directive, calibration-sweep contract AC-7.
_CALSWEEP_TRIGGER_FREQ_FLAG_MULTIPLIER = 2.0

# --- run_simulation_sortino_legacy objective: loss-averse utility penalty constants ---
# (audit H-10 — AC-4 remediation). The run_simulation_sortino_legacy objective is an
# explicit LOSS-AVERSE utility: it weights downside outcomes (negative guard-alpha,
# missed upside, peak-to-exit drawdown) more heavily than symmetric guard-alpha.
# Loss aversion is a deliberate capital-preservation choice — the same family of
# asymmetric utility Planet Stopper's Sortino objective (downside deviation) embodies.
# Each scalar/threshold below was an unsourced inline literal (finding H-10); naming
# + sourcing them here makes the objective inspectable and prevents a silent scalar
# drift inverting the policy ranking.
#
# M1 Note: the six original names (MISSED_UPSIDE_PENALTY_MULT etc.) were the module-
# level constants replaced by M1. Under Option B (legacy branch retained), the
# constants are kept here with SORTINO_OBJ_* names (satisfying AC-4 keyword patterns)
# so that run_simulation_sortino_legacy can reference them. Inside the function body,
# local aliases RUN_SIM_* are assigned from these so the function body uses named
# references without module-scope pollution of the RUN_SIM_* names.

# Multiplier on missed upside (best intraday return forgone by exiting early).
# 1.5 > 1.0: forgone upside is penalised harder than realised guard-alpha — an
# early exit that leaves a run on the table is a real opportunity cost.
SORTINO_OBJ_MISSED_UPSIDE_MULT = 1.5
# Missed upside is only penalised once it exceeds this many percent — a small
# forgone move is execution noise, not a policy defect.
SORTINO_OBJ_MISSED_UPSIDE_THRESHOLD = 1.0

# Multiplier on peak-to-exit drawdown (profit given back from the intraday high).
# 0.75 < 1.0: giving back profit is penalised, but more leniently than missed
# upside — some give-back is unavoidable in any trailing-stop policy.
SORTINO_OBJ_DRAWDOWN_MULT = 0.75
# Peak-to-exit drawdown is only penalised once it exceeds this many percent —
# a give-back smaller than this is within normal trailing-stop slack.
SORTINO_OBJ_DRAWDOWN_THRESHOLD = 1.5
# The drawdown penalty applies only after a position reached at least this gain;
# below it there is no meaningful profit to "give back".
SORTINO_OBJ_DRAWDOWN_MIN_GAIN = 1.0

# Loss-aversion multiplier on NEGATIVE guard-alpha (the policy exited worse than
# simply holding to EOD). 2.0 > 1.0 makes the objective asymmetric: a loss of
# guard-alpha hurts twice as much as an equal gain helps — the core loss-averse
# term of the utility.
SORTINO_OBJ_NEGATIVE_GUARD_ALPHA_MULT = 2.0

# Target return for Sortino denominator: capital preservation baseline (0 = break-even).
# Operator decision PA-5; Sortino & van der Meer 1991, J. Portfolio Management.
SORTINO_TARGET_RETURN = 0.0

# Walk-forward purge window: training samples whose feature lookback window overlaps
# the test fold are excluded. Purge-relevant lookbacks are only those that cause a
# train sample's FEATURE COMPUTATION to reach into the test fold:
#   - calculate_20d_vol:      LOOKBACK_DAYS      = 20 trading days
#   - calculate_14d_atr_pct:  ATR_LOOKBACK_DAYS  = 15 trading days (14 TR periods + 1 prior close)
# PURGE_DAYS = max(20, 15) = 20.
# NOTE: mc_prob is pre-computed in tick data, not computed live in the sim loop, so it
# adds no feature lookback to purge sizing.
# López de Prado 2018, Advances in Financial Machine Learning, Ch. 7 (Purged k-fold CV).
PURGE_DAYS = 20

# Walk-forward embargo: training samples in the EMBARGO_DAYS immediately FOLLOWING
# a test fold are dropped, to suppress serial-dependence (autocorrelation) leakage
# that the purge window does not catch (López de Prado 2018, Advances in Financial
# Machine Learning, Ch. 7.4).
# Sizing — the embargo is sized to the estimated serial-dependence horizon of the
# per-day guard-alpha series. Guard-alpha (triggered_return - eod_return) is a
# same-day DIFFERENCE: a daily-frequency outcome variable, not a multi-day-overlapping
# feature, so it carries no mechanical lookback (that channel is covered by
# PURGE_DAYS=20). Its only residual serial dependence is the indirect volatility-
# clustering channel — and the 20-day purge already removes train samples whose
# vol/ATR feature windows overlap the test fold, absorbing the bulk of that
# persistence. The remaining DIRECT lag-1 autocorrelation of the guard-alpha series
# decays into the noise band within ~1 trading day; the guard-alpha sign shows no
# meaningful persistence. Estimated horizon <= 1 day. This also matches LdP Ch. 7.4's
# ~1%-of-observations embargo guidance (~2.5 days at the ~250-day window). The 1-day
# embargo is therefore vindicated, not a floor.
EMBARGO_DAYS = 1

# Three-fold walk-forward ratios: 60% train / 20% validation / 20% frozen-eval.
# Selection is on validation; frozen-eval is consumed once post-selection for honest
# performance reporting. Purge + embargo applied at BOTH fold boundaries.
# 60/20/20 split is an operator choice for Planet Stopper's data scale (250 trading days);
# the held-out frozen-eval invariant derives from LdP 2018 Ch. 7.4 (not the specific ratio).
TRAIN_RATIO = 0.60
VALIDATION_RATIO = 0.20
FROZEN_EVAL_RATIO = 0.20
assert abs(TRAIN_RATIO + VALIDATION_RATIO + FROZEN_EVAL_RATIO - 1.0) < 1e-9, (
    "TRAIN_RATIO + VALIDATION_RATIO + FROZEN_EVAL_RATIO must equal 1.0"
)

# OPTUNA-4 — Operator-visibility pin on the usable validation window.
# At the 250-day operator-data-budget (Phase-1 extension, 2026-06-01):
#     int(synthetic_history._WALK_FORWARD_TRADING_DAYS * VALIDATION_RATIO)
#     - PURGE_DAYS - EMBARGO_DAYS = int(250*0.20) - 20 - 1 = 29 days.
# The ~29-day usable validation window is a meaningful improvement over the
# prior ~4-day window (at 125 days), while remaining an acknowledged
# statistical power limitation: the BHY haircut addresses cross-trial
# selection bias independently of T; it does NOT substitute for thin
# per-trial sample length. Future-workstream remediation paths:
# (a) further expand the operator-data-budget (council Amendment), or
# (b) adopt combinatorial purged k-fold cross-validation per López de Prado
# 2018 Ch. 7.4 to recover additional statistical power without expanding
# total history.
# The canonical joint (N, T) framework to consult is the Deflated Sharpe
# Ratio — Bailey & López de Prado 2014. A drift in this value indicates
# either (a) the operator-data-budget changed or (b) PURGE_DAYS / EMBARGO_DAYS
# drifted; both must surface as Amendments, not silent slide-ins.
# References synthetic_history._WALK_FORWARD_TRADING_DAYS (not a hardcoded
# literal) so the two constants cannot drift independently.
_OOS_USABLE_VALIDATION_DAYS_EXPECTED = (
    int(synthetic_history._WALK_FORWARD_TRADING_DAYS * VALIDATION_RATIO) - PURGE_DAYS - EMBARGO_DAYS
)

# --- CPCV constants (Phase 2 — Combinatorial Purged Cross-Validation) ---
# N=6 groups, k=2 test groups per split → C(6,2)=15 splits → φ[6,2]=5 backtest paths.
# Reference: López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.4
# (Combinatorial Purged Cross-Validation; purge+embargo per-seam arithmetic).
# See also: docs/research/optuna/sources.md — CPCV/Purged-Embargoed CV section;
# feature-plans/walk-forward-overhaul.md Phase 2.
#
# N_GROUPS and K_TEST_GROUPS are operator dials (not magic literals) so that
# changing the CV configuration propagates to _CPCV_N_SPLITS and _CPCV_N_PATHS
# automatically, without manual re-derivation. Changing these constants is a
# methodology change — surface to PM first.
_CPCV_N_GROUPS = 6  # N: number of contiguous date groups to partition history into
_CPCV_K_TEST_GROUPS = 2  # k: number of groups held out as test per split
# C(N, k) — total splits; derived so it tracks N and k automatically.
_CPCV_N_SPLITS = math.comb(_CPCV_N_GROUPS, _CPCV_K_TEST_GROUPS)
# φ[N,k] = (k/N)·C(N,k) — number of complete OOS backtest paths.
# At N=6, k=2: φ = (2/6)·15 = 5 paths, each assembled from N/k=3 non-overlapping splits.
# Retained as documented combinatorial theory (φ = C(N−1,k−1)); NOT consumed by
# any runtime scoring path post-R2 — split-level scoring uses _CPCV_N_SPLITS
# exclusively (DE-MATH-R2-001).
_CPCV_N_PATHS = int((_CPCV_K_TEST_GROUPS / _CPCV_N_GROUPS) * _CPCV_N_SPLITS)


def _generate_cpcv_folds(
    sorted_dates: list,
    n_groups: int = _CPCV_N_GROUPS,
    k_test: int = _CPCV_K_TEST_GROUPS,
    purge_days: int = PURGE_DAYS,
    embargo_days: int = EMBARGO_DAYS,
) -> list:
    """Partition dates into combinatorial purged-cross-validation (CPCV) folds.

    Splits ``sorted_dates`` into ``n_groups`` contiguous groups, then generates
    every C(n_groups, k_test) combination of groups as the test fold. For each
    split, purge+embargo is applied at EVERY seam between train and test segments
    (both the train→test and test→train boundaries for each contiguous test block).

    Each returned fold descriptor is a dict:
        ``train_dates``     — set of effective (post-purge/embargo) training dates
        ``test_dates``      — set of raw test dates (all k_test groups, unpurged)

    (Historical note, AC-1/R2/DE-MATH-R2-001: fold descriptors carried a
    ``path_membership`` key — the mlfinlab canonical first-available-slot
    φ-path assignment — through R1. Split-level scoring retired path
    reconstruction entirely (canonical CPCV backtest paths are a refit-world
    construct, mathematically forced to span the full eligible window in a
    no-refit codebase); this function now returns only the two keys above.)

    Purge/embargo per-seam arithmetic (LdP 2018 Ch.7.4):
        For each contiguous train segment adjacent to a test block, remove
        ``purge_days + embargo_days`` positions from the end touching the test
        boundary. Applied independently at each seam so non-contiguous test groups
        (e.g., groups 0 and 3) each get their own purge/embargo on both flanking
        train-group edges.

    Pure function of the date list — no I/O, no DB calls.

    Args:
        sorted_dates:  Chronologically sorted date strings.
        n_groups:      Number of contiguous partitions (default _CPCV_N_GROUPS=6).
        k_test:        Number of groups held out per split (default _CPCV_K_TEST_GROUPS=2).
        purge_days:    Feature-lookback purge window in trading days (default PURGE_DAYS).
        embargo_days:  Serial-dependence embargo in trading days (default EMBARGO_DAYS).

    Returns:
        List of C(n_groups, k_test) fold descriptor dicts, one per combination.

    Reference: López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.4.
    """
    n_dates = len(sorted_dates)
    # Build group index boundaries (contiguous equal-ish partitions).
    # Using integer floor partitioning: group g contains indices [starts[g], starts[g+1]).
    group_size, remainder = divmod(n_dates, n_groups)
    starts = []
    pos = 0
    for g in range(n_groups):
        starts.append(pos)
        pos += group_size + (1 if g < remainder else 0)
    starts.append(n_dates)  # sentinel

    # Precompute each group's date set.
    groups: list[list] = [sorted_dates[starts[g] : starts[g + 1]] for g in range(n_groups)]

    folds = []
    for test_combo in itertools.combinations(range(n_groups), k_test):
        test_group_set = set(test_combo)
        train_group_indices = [g for g in range(n_groups) if g not in test_group_set]

        # Raw test dates — all dates from the k_test groups, no purge on test side.
        raw_test_dates: set = set()
        for g in test_combo:
            raw_test_dates.update(groups[g])

        # Build effective train dates with per-seam purge+embargo.
        # Strategy: identify contiguous train segments (runs of adjacent train groups),
        # then for each segment trim from each end that is adjacent to a test group.
        # A "run" boundary exists between train group g and train group g+1 if there
        # is at least one test group between them in the global order.
        purge_embargo = purge_days + embargo_days

        # Determine, for each date, whether it is a candidate train date.
        # We process group by group: for each train group, check its left and right
        # neighbours in the global group ordering to decide trimming.
        effective_train_dates: set = set()
        for g in train_group_indices:
            g_dates = groups[g]
            if not g_dates:
                continue
            n_g = len(g_dates)
            trim_left = 0
            trim_right = 0

            # Left trim: if the group immediately to the left (g-1) is a test group,
            # trim the first purge_embargo positions of this train group.
            if (g - 1) >= 0 and (g - 1) in test_group_set:
                trim_left = purge_embargo

            # Right trim: if the group immediately to the right (g+1) is a test group,
            # trim the last purge_embargo positions of this train group.
            if (g + 1) < n_groups and (g + 1) in test_group_set:
                trim_right = purge_embargo

            # Apply trims; skip the group entirely if nothing survives.
            start_pos = trim_left
            end_pos = n_g - trim_right
            if start_pos < end_pos:
                effective_train_dates.update(g_dates[start_pos:end_pos])

        folds.append(
            {
                "train_dates": effective_train_dates,
                "test_dates": raw_test_dates,
            }
        )

    return folds


# _aggregate_cpcv_paths DELETED (AC-1, R2, team-lead RULED Option A / split-level
# scoring — feature-plans/math-r2.md ADDENDUM 3/5): canonical N=6/k=2 CPCV
# backtest-path reconstruction is mathematically forced to make every path span
# the FULL eligible window (phi paths, each stitched from N/k non-overlapping
# splits whose test groups union to ALL N groups) — a REFIT-WORLD construct.
# With no per-fold refit in this codebase (guard_alpha for a date is a pure
# function of (date, trial params), independent of fold/path assignment),
# path-level aggregation is structurally incompatible with non-identical
# scores no matter how date-attribution is implemented (proven three
# independent ways: r2-test's live probe, r2-stats's φ=C(N−1,k−1) bijection
# proof + empirical, the PM's refit-world argument). The honest CSCV-native
# granularity is the C(6,2)=15 SPLIT ensemble itself — see run_autotuner's
# objective() closure, which scores each split's own test_dates directly via
# _collect_sim_returns_dated(..., score_dates=...) instead of aggregating into
# 5 always-identical full-window paths. Confirmed zero surviving consumers
# repo-wide (r2-stats's full-repo trace, DECISIONS.md DE-MATH-R2-001): no
# persisted autotune_runs column, no Overfitting Conscience / Spec Critic
# reader, and math_engine.compute_pbo runs its OWN independent S=8-block CSCV
# construction untouched by this retirement.


def build_symphony_study_name(timestamp: str, symphony_id: str) -> str:
    """Return the per-symphony study name: {timestamp}__{symphony_id} (N1/O3)."""
    return f"{timestamp}__{symphony_id}"


def compute_sortino_ratio(returns: list, target: float = SORTINO_TARGET_RETURN) -> float:
    """Sortino ratio on a returns series.

    Formula: mean(r) / downside_deviation
    where downside_deviation = sqrt(mean(min(r - target, 0)^2))
    Population denominator: divide by N (all observations), not N_downside.

    Reference: Sortino & van der Meer 1991, "Downside Risk",
    Journal of Portfolio Management.

    Args:
        returns: Per-period return values (e.g., per-day guard-alpha).
        target: Minimum acceptable return; defaults to SORTINO_TARGET_RETURN (0.0).

    Returns:
        Sortino ratio as a finite float. Returns 1e6 when downside_deviation
        is zero (all returns >= target) so Optuna TPE receives a finite value.
        Returns 0.0 for an empty series.
    """
    if not returns:
        return 0.0
    n = len(returns)
    mean_r = sum(returns) / n
    sum_sq_downside = sum(min(r - target, 0.0) ** 2 for r in returns)
    mean_sq_downside = sum_sq_downside / n
    downside_deviation = math.sqrt(mean_sq_downside)
    if downside_deviation == 0.0:
        return 1e6
    return mean_r / downside_deviation


# --- Harvey & Liu 2015 selection-bias haircut (Decision D3) ---
# The autotuner runs hundreds of trials and deploys the best — a multiple-testing
# problem: the best-of-N Sortino is upward-biased by selection. The correction is
# a Harvey & Liu 2015 Benjamini-Hochberg false-discovery-rate haircut, metric-
# agnostic: it adjusts a per-trial p-value for the number of tests tried, instead
# of feeding a Sortino into a Sharpe-derived deflation formula (the H-6 category
# error this replaces — a Sortino's sampling distribution is not the Sharpe's).
# Reference: Harvey, C.R. & Liu, Y. (2015). "Backtesting." Journal of Portfolio
# Management 42(1), 13-28. DOI 10.3905/jpm.2015.42.1.013.

# Benjamini-Hochberg false-discovery-rate level for the selection haircut. A trial
# is deployable only if its BHY-adjusted p-value is <= this q. Conventional 0.05
# (Harvey & Liu 2015 use FDR control for best-of-N strategy selection; BHY rather
# than Bonferroni because Bonferroni at N~500 is brutally over-conservative).
# Policy dial — the operator may tighten/loosen the selection strictness here.
HARVEY_LIU_FDR_Q = 0.05

# Per-symphony Optuna trial count. Named so the BHY clamp formula below can
# reference it as N rather than duplicating the literal 500 (the same N
# study.optimize uses at the per-symphony optimization call site). Source:
# 500 trials is the standing per-symphony budget — the statistical-stability
# floor for the TPE sampler at the V1 search-space width.
MAX_OPTUNA_TRIALS = 500

# Numerical-stability + BHY information-preservation epsilon for the haircut
# p-value clamp. Two rationales — both must hold:
#   (a) IEEE-754 stability — a large trial t-statistic drives the one-sided
#       p-value `1 - Φ(t)` to underflow to exactly 0.0 (large |t| beyond ~8.3
#       saturates Φ); a degenerate 0.0/1.0 p-value makes any downstream
#       log / inverse-CDF non-finite. The clamp must sit safely inside the
#       IEEE-754 representable range (any value above ~1e-16 suffices).
#   (b) BHY information-preservation floor — the clamp value is the smallest
#       raw p-value that still affects the BHY adjusted-p decision under
#       the N·c(N)/k scaling. Below q/(N·c(N)) the raw p makes no marginal
#       difference to its adjusted p_adj versus the next-larger raw p; the
#       clamp at this floor preserves all signal-relevant ordering and
#       discards only sub-resolution noise.
# Benign residual: in the operationally-rare case where EVERY trial saturates
# the clamp (every t-stat beyond Φ's representable range), the BHY step-up's
# running-min from rank n locks min(p_adj) at c(N)·eps = q/N — and the gate
# accepts all. This degenerates to the pre-Cluster-4 naive-best-of-N
# behavior — intrinsic to multi-test correction at saturation, NOT a code
# defect. The audit RM-M2 framing of this residual as an accept-all collapse
# was math-overstated (the BHY running-min walks each rank independently —
# a saturated trial only affects its OWN p_adj, not the others').
# Source: Cluster 7 / post-audit-hardening / risk-engine-specialist's
# numerical analysis 2026-05-22; HARVEY_LIU_FDR_Q + MAX_OPTUNA_TRIALS as
# defined above. c(N) = sum_{j=1..N} 1/j is the Yekutieli arbitrary-
# dependence factor — same factor benjamini_hochberg_adjust below uses.
_HAIRCUT_PVALUE_EPSILON = HARVEY_LIU_FDR_Q / (
    MAX_OPTUNA_TRIALS * sum(1.0 / j for j in range(1, MAX_OPTUNA_TRIALS + 1))
)

# ---------------------------------------------------------------------------
# CRRA-EU objective constants — M1 Phase 1 HARDEN-core (plan §Deliverables).
# ---------------------------------------------------------------------------

# WEALTH_ARG_FLOOR: imported from math_engine (single source of truth).
# Both modules must see the same constant — an independent copy would allow
# silent per-module drift. The source comment and rationale live in
# math_engine.py next to the definition.
from math_engine import WEALTH_ARG_FLOOR  # noqa: E402  (after stdlib imports above)

# Unit-conversion factor: autotuner return series are in percent
# (synthetic_history.py:355 — tick['return'] = agg_ret * 100.0). The CRRA
# formula requires a decimal-fraction wealth ratio (W = 1.05 for +5%, not
# W = 105%). This constant converts percent -> fraction at the autotuner boundary.
# Source: W-H2 fixture unit_conversion_constant; W-H2 derivation memo §A4_consistency.
RETURN_PCT_TO_FRACTION: float = 100.0


def derive_wealth_argument(r_policy_fraction: float) -> float:
    """Derive the per-period gross wealth ratio from a per-day policy return.

    W_i = 1 + r_i_policy_fraction

    where r_i_policy_fraction is the triggered policy return in decimal-fraction
    units (r_policy_fraction = r_policy_pct / RETURN_PCT_TO_FRACTION).

    This is the W-H2 formula (per_period_gross_wealth_ratio). The output is
    the raw W BEFORE the floor is applied — the caller decides whether to floor.
    Flooring is a separate stability concern (W-H4) from derivation (W-H2).

    Reference: decision-science-council-synthesis.md §3.9 W-H2;
               tests/fixtures/m1-wealth-argument/derivation-fixture.json.
    """
    return 1.0 + r_policy_fraction


def derive_floored_wealth_argument(r_policy_fraction: float) -> float:
    """Derive the per-period gross wealth ratio with the W-H4 floor applied.

    W_i = max(WEALTH_ARG_FLOOR, 1 + r_i_policy_fraction)

    This is the W-H2 formula plus the W-H4 floor. This is the function to call
    when computing CRRA utility: derive the raw W, then clamp to WEALTH_ARG_FLOOR.
    The floor is on the INPUT W — NEVER apply the floor to the output U.

    Reference: decision-science-v3-and-divergence-evaluation.md §A.1 H-1 (W-H4);
               WEALTH_ARG_FLOOR source comment above.
    """
    W_raw = derive_wealth_argument(r_policy_fraction)
    return max(WEALTH_ARG_FLOOR, W_raw)


def compute_crra_eu_tstat(U_series: list[float]) -> float:
    """Per-trial t-statistic for the CRRA-EU objective: mean(U) / (sd(U) / sqrt(T)).

    This is the one-sample t-statistic for a mean-valued objective. It replaces
    compute_sortino_tstat for the CRRA-EU branch. Using compute_sortino_tstat
    (which returns sortino*sqrt(T)) for a mean-valued objective is the H-6
    category error — autotuner.py:266-271 named this error once for the
    Sharpe-derived deflation; the same discipline applies here.

    H-6 / W-H5 category discipline (see autotuner.py:266-271 precedent):
        The H-6 category error was a Sharpe-derived deflation applied to a Sortino.
        Since 2026, the same category-discipline applies between
        compute_sortino_tstat (Sortino objective) and compute_crra_eu_tstat
        (CRRA-EU objective) — a mean-valued functional needs the one-sample
        t-stat, NOT effect_size*sqrt(T).

    Implementation constraints:
        - statistics.stdev (sample, ddof=1, Bessel-corrected). Using pstdev
          would inflate t by sqrt(T/(T-1)) and shift haircut calibration.
        - Returns 0.0 for T <= 1 (sd undefined for n < 2).
        - Returns 0.0 for a constant series (sd=0). Degenerate trials rank last
          via argmin(p_adj) — no sentinel needed.
        - Pure: no side effects, no logging, no DB writes.

    Reference: S-2 binding condition; decision-science-council-synthesis.md §4.
    """
    T = len(U_series)
    if T <= 1:
        return 0.0
    mean_U = sum(U_series) / T
    sd_U = statistics.stdev(U_series)  # sample stdev, ddof=1
    if sd_U == 0.0:
        return 0.0
    return mean_U / (sd_U / math.sqrt(T))


# Bootstrap parameters for the haircut Sortino-SE construction (Decision D10 /
# RM-H1, risk-engine-specialist's binding design memo 2026-05-22).
#
# _BOOTSTRAP_RESAMPLES — nonparametric bootstrap resample count used to
# estimate the Sortino's SE. Sources: Efron 1979 (Annals of Statistics
# 7(1):1-26 — bootstrap SE convergence); Efron & Tibshirani 1986
# (Statistical Science 1(1):54-77 — B=50-200 for SE point estimation, B>=1000
# for p-value/CI calibration); Davidson & MacKinnon 2000 (Econometric
# Reviews 19(1):55-68 — B=999 standard for test-statistic bootstrap). Here
# the SE feeds a one-sided Φ p-value, then the BHY N·c(N)/k scaling — the
# calibrating-use-case floor applies. B=2000 ≈ 2x the prescribed floor with
# negligible per-trial cost.
_BOOTSTRAP_RESAMPLES = 2000

# _BOOTSTRAP_MIN_T — minimum series length for which the nonparametric
# bootstrap SE is trusted. Below this the resample combinatorics dominate
# the underlying distribution and the SE estimator is degenerate.
# Source: Efron 1979's implicit small-T floor; risk-engine-specialist's 5.
_BOOTSTRAP_MIN_T = 5

# _BOOTSTRAP_MIN_VALID_RESAMPLES — floor on the count of non-sentinel
# resampled Sortinos. The math_engine._SORTINO_SENTINEL (+1e6) emitted by
# compute_sortino_ratio for an all-non-negative resample is not a real
# Sortino observation; resamples yielding it are filtered out of the SE
# computation. Below this floor the remaining sample is too small to
# estimate SE reliably and the helper surfaces "unavailable" (None) rather
# than a degenerate SE. Source: risk-engine-specialist's recommendation of 100.
_BOOTSTRAP_MIN_VALID_RESAMPLES = 100

# _AUTOTUNE_REPLAY_N_JOBS — bounds the intraday-replay parallelism on the autotune
# path to prevent OOM on the 2-core / MemoryMax=3.0 GiB droplet. n_jobs=1 uses
# joblib's sequential backend (no fork), keeping peak RSS at 2.03 GiB vs the 3.0 GiB
# cgroup cap (AC-1 empirical profile 2026-06-29, DE-AUTOTUNE-OOM). Other callers of
# generate_synthetic_history are untouched — their default (n_jobs=None) continues to
# resolve via _resolve_replay_n_jobs(). AC-6 sets ALPHABOT_MAX_JOBS=1 in .env as
# defense-in-depth; this constant is the primary code-level safety mechanism.
_AUTOTUNE_REPLAY_N_JOBS = 1


def compute_sortino_se_bootstrap(
    returns,
    target: float = SORTINO_TARGET_RETURN,
    B: int = _BOOTSTRAP_RESAMPLES,
    seed: int = 0,
):
    """Nonparametric bootstrap standard error of the Sortino ratio (Efron 1979).

    Standard nonparametric bootstrap (risk-engine-specialist's binding
    design): draws ``B`` independent samples-with-replacement of length
    ``T = len(returns)`` from the input series via
    ``numpy.random.default_rng(seed).integers(0, T, size=T)``; computes the
    Sortino on each resample; returns the sample standard deviation
    (ddof=1, Efron 1979 convention) of the non-sentinel resampled Sortinos.

    Returns ``None`` — the "SE unavailable" sentinel the t-stat path treats
    as a conservative-rejection signal — when:
      - T < _BOOTSTRAP_MIN_T (small-T regime; bootstrap unreliable);
      - returns is a constant series (zero variance; SE degenerate);
      - fewer than _BOOTSTRAP_MIN_VALID_RESAMPLES non-sentinel Sortinos
        accumulated (sentinel-rich resample population).

    Args:
        returns: per-trial return observations.
        target: minimum-acceptable return for the Sortino's downside
            denominator; defaults to SORTINO_TARGET_RETURN (0.0).
        B: bootstrap resample count.
        seed: deterministic seed for the numpy default_rng; the haircut is
            therefore reproducible under a fixed trial set.

    Returns:
        SE as a finite float >= 0, or None when SE is unavailable.

    Reference: Efron, B. (1979). "Bootstrap Methods: Another Look at the
    Jackknife", Annals of Statistics 7(1), 1-26.
    """
    T = len(returns)
    if T < _BOOTSTRAP_MIN_T:
        return None
    # Constant series: bootstrap SE is trivially zero (every resample equals
    # the input). Surface as "unavailable" so the t-stat path treats the
    # trial conservatively rather than dividing by zero.
    first = returns[0]
    if all(r == first for r in returns):
        return None
    rng = np.random.default_rng(seed)
    sortinos: list = []
    for _ in range(B):
        idx = rng.integers(0, T, size=T)
        resample = [returns[int(i)] for i in idx]
        s = compute_sortino_ratio(resample, target=target)
        # Filter the canonical +math_engine._SORTINO_SENTINEL — an all-non-
        # negative resample yields it (zero downside deviation → compute_
        # sortino_ratio returns the sentinel) and it is NOT a real Sortino
        # observation. Couple to the canonical name so the sentinel-filter
        # follows any change to math_engine._SORTINO_SENTINEL automatically.
        if s != math_engine._SORTINO_SENTINEL:
            sortinos.append(s)
    if len(sortinos) < _BOOTSTRAP_MIN_VALID_RESAMPLES:
        return None
    # ddof=1 sample stdev — Efron 1979 convention for the bootstrap SE.
    return float(np.std(sortinos, ddof=1))


def compute_sortino_tstat(returns, seed: int = 0) -> float:
    """Per-trial t-statistic for the Harvey & Liu haircut.

    Construction (Decision D10 / RM-H1, risk-engine-specialist's binding
    design): the Sortino is computed on the per-day return series and divided
    by a nonparametric bootstrap estimate of its standard error
    (compute_sortino_se_bootstrap). The Sharpe-specific Wald scaling that
    survived Cluster 4 is NOT used — the Sortino's asymptotic SE is driven
    by downside deviation, not full std, so the Wald scaling mis-calibrates
    the p-value (the M-3 category error this corrects).

    Conservative fallback: when compute_sortino_se_bootstrap returns None
    (small-T, zero variance, or sentinel-rich resample population), the
    t-stat is 0.0. Downstream compute_haircut_pvalue(0.0) = 0.5, so the
    trial fails the BHY FDR gate by default. Falling back to the Wald
    scaling would defeat the RM-H1 fix.

    Args:
        returns: per-day return observations for the trial.
        seed: deterministic seed forwarded to the bootstrap RNG; the haircut
            decision is reproducible under a fixed trial set. The
            _haircut_select caller derives it from the trial index.

    Returns:
        The t-statistic as a finite float. Returns 0.0 when the bootstrap SE
        is unavailable (the conservative-rejection fallback).

    Reference: Decision D10 (post-audit-hardening); Efron 1979 (bootstrap);
    Harvey & Liu 2015, DOI 10.3905/jpm.2015.42.1.013 (the haircut framework).
    """
    se = compute_sortino_se_bootstrap(returns, seed=seed)
    if se is None or se == 0.0:
        return 0.0
    return compute_sortino_ratio(returns) / se


def compute_haircut_pvalue(t_stat: float) -> float:
    """One-sided p-value for a haircut t-statistic: ``1 - Φ(t)``, clamped.

    Φ is the standard-normal CDF (large-sample approximation; Harvey & Liu 2015
    use the normal for large samples). The result is clamped into
    ``[_HAIRCUT_PVALUE_EPSILON, 1 - _HAIRCUT_PVALUE_EPSILON]`` so an extreme
    t-statistic cannot produce a degenerate exactly-0.0 / exactly-1.0 p-value
    that would make a downstream log / inverse-CDF non-finite.
    """
    # Φ(t) = 0.5·(1 + erf(t/√2)); the one-sided survival p is 1 - Φ(t).
    phi = 0.5 * (1.0 + math.erf(t_stat / math.sqrt(2.0)))
    p = 1.0 - phi
    return min(max(p, _HAIRCUT_PVALUE_EPSILON), 1.0 - _HAIRCUT_PVALUE_EPSILON)


@functools.cache
def _yekutieli_c_n(n: int) -> float:
    """Yekutieli arbitrary-dependence factor c(N) = sum_{j=1}^{N} 1/j.

    Cached because the BHY haircut re-derives the same c(N) on every
    _haircut_select call (N=500 in production).
    """
    return sum(1.0 / j for j in range(1, n + 1))


def benjamini_hochberg_adjust(p_values: list[float]) -> list[float]:
    """Benjamini-Hochberg-Yekutieli (BHY) step-up adjustment of raw p-values.

    BHY step-up: sort the p-values ascending, then
    ``p_adj_(k) = min over j >= k of [ (N * c(N) / j) * p_(j) ]``, clamp each to
    [0, 1], and map back to the input order. The running minimum from the largest
    rank downward makes the adjusted p-values monotone non-decreasing in raw-p
    rank.

    ``c(N) = sum_{j=1}^{N} 1/j`` is the N-th harmonic number — the Yekutieli
    arbitrary-dependence correction factor. It is required here because the
    Optuna trial statistics are NOT independent (the TPE sampler concentrates
    the search), so plain Benjamini-Hochberg 1995 — which assumes independence /
    PRDS — would under-correct the false-discovery rate by a factor of c(N).

    Returns one adjusted p-value per input, in the input order.

    Reference: Harvey & Liu 2015, DOI 10.3905/jpm.2015.42.1.013, which prescribe
    BHY; Benjamini, Hochberg & Yekutieli (2001), "The Control of the False
    Discovery Rate in Multiple Testing under Dependency", Annals of Statistics
    29(4), 1165-1188 (the c(N) arbitrary-dependence factor).
    """
    n = len(p_values)
    if n == 0:
        return []
    # Yekutieli arbitrary-dependence factor: the N-th harmonic number.
    c_n = _yekutieli_c_n(n)
    # Sort indices by ascending raw p-value.
    order = sorted(range(n), key=lambda i: p_values[i])
    adjusted = [0.0] * n
    running_min = 1.0
    # Step up from the largest rank (k = n) down to the smallest (k = 1).
    for rank in range(n, 0, -1):
        idx = order[rank - 1]
        candidate = (n * c_n / rank) * p_values[idx]
        running_min = min(running_min, candidate)
        adjusted[idx] = min(max(running_min, 0.0), 1.0)
    return adjusted


# NN1 (synthesis hard gate — council §2.5): the generator family and the
# horizon convention may NEVER be frozen by P&L / backtest selection.
# This function ENFORCES NN1 STRUCTURALLY: a P&L-frozen facet appears as
# a researcher_dof_ledger row with evidence_source='BACKTEST_SELECTION',
# which bumps S, which inflates N_effective, which inflates the Yekutieli
# c(N) factor, which inflates every adjusted p-value, which raises the
# FDR-gate bar — making the haircut harder to clear. NN1-honest case
# (S=0) → byte-identical to today's haircut.


def compute_n_effective(
    n_optuna: int,
    ledger_query,
    winning_spec_bundle_id: "str | None" = None,
) -> int:
    """Return the honest multiple-testing count for the BHY haircut.

    N_effective = N_optuna + S, where S is the sum of n_configs_searched
    over researcher_dof_ledger rows whose evidence_source is
    BACKTEST_SELECTION, touched_frozen_eval is falsy, and spec_bundle_id
    does not match the winning bundle (council synthesis §2.2; v3-and-
    divergence-evaluation §B.3 route 2).

    NN1-honest case: every facet is theory/mandate/calibration-frozen so
    no row has evidence_source='BACKTEST_SELECTION' → S = 0 →
    N_effective = N_optuna and the haircut is byte-identical to today's.

    The accounting is a conservative upper bound (errs safe — rejects a
    genuine signal, never passes a spurious one) and a tripwire that
    enforces NN1 structurally.

    `ledger_query` is a callable returning the list of relevant ledger
    rows; injected for testability and to keep compute_n_effective
    pure with respect to its DB read.

    TYPE-002 (sprint-2-audit a6e4d9f8): `winning_spec_bundle_id` is the
    64-char TEXT bundle_hash (researcher_dof_ledger.spec_bundle_id column
    is TEXT). Callers MUST pass a string or None — never an integer primary
    key. The production path (run_autotuner) pre-filters via
    get_researcher_dof_ledger_for_run(winning_spec_bundle_id=stored_hash)
    and does not pass this param here; the assert below guards direct callers.
    """
    # TYPE-002: guard against callers passing integer PK instead of bundle hash.
    assert winning_spec_bundle_id is None or isinstance(winning_spec_bundle_id, str), (
        f"compute_n_effective: winning_spec_bundle_id must be str (bundle_hash) "
        f"or None, got {type(winning_spec_bundle_id).__name__!r}. "
        f"Pass the 64-char bundle_hash, not the integer spec_bundles.id."
    )
    rows = ledger_query()
    s = 0
    for row in rows:
        # Exclude frozen-eval-tainted rows — handled by the OOS_PEEK alarm path.
        if row.get("touched_frozen_eval"):
            continue
        # Exclude the winner bundle — already counted in n_optuna via the sweep.
        if (
            winning_spec_bundle_id is not None
            and row.get("spec_bundle_id") == winning_spec_bundle_id
        ):
            continue
        s += int(row.get("n_configs_searched", 1))
    return n_optuna + s


def calculate_historical_deviation(current_date_str):
    """
    Scans local directory for post_mortem_*.json from the last 45 calendar days.
    Calculates average deviation (exit_return - attempted_trigger_level) grouped by exit_reason.
    """
    deviation_dict = {
        "Take-Profit": 0.0,
        "Trailing Stop": -0.20,
        "VWAP Breakdown": -0.40,
        "VWAP Bleed Cut": -0.25,
    }

    deviation_sums = {k: 0.0 for k in deviation_dict}
    deviation_counts = {k: 0 for k in deviation_dict}

    try:
        current_dt = datetime.strptime(current_date_str, "%Y-%m-%d")
        lookback_dt = current_dt - timedelta(days=45)

        _pm_dir = _resolve_post_mortem_dir()
        if not os.path.isdir(_pm_dir):
            print(
                f"      -> WARNING: post-mortem directory does not exist: "
                f"'{_pm_dir}'. Using default deviation penalties."
            )
            files = []
        else:
            files = glob.glob(os.path.join(_pm_dir, "post_mortem_*.json"))
            if not files:
                print(
                    f"      -> WARNING: no post_mortem_*.json files found in "
                    f"'{_pm_dir}'. Using default deviation penalties."
                )
        for f_path in files:
            try:
                # Extract date from filename: post_mortem_YYYY-MM-DD.json
                date_part = (
                    os.path.basename(f_path).replace("post_mortem_", "").replace(".json", "")
                )
                file_dt = datetime.strptime(date_part, "%Y-%m-%d")
                if file_dt < lookback_dt or file_dt >= current_dt:
                    continue

                with open(f_path, encoding="utf-8") as f:
                    data = json.load(f)
                    triggers = data.get("triggers", [])
                    for t in triggers:
                        reason = t.get("exit_reason")
                        exit_ret = t.get("exit_return")
                        attempted = t.get("attempted_trigger_level")

                        if (
                            reason in deviation_sums
                            and exit_ret is not None
                            and attempted is not None
                        ):
                            deviation_sums[reason] += exit_ret - attempted
                            deviation_counts[reason] += 1
            except (json.JSONDecodeError, OSError, KeyError, ValueError) as exc:
                # A corrupt/unreadable post-mortem must be skipped LOUDLY — the
                # deviation penalties feed the autotuner objective, so a silently
                # dropped file changes the objective with no operator visibility
                # (audit finding-13). KeyboardInterrupt / MemoryError are
                # deliberately NOT in this set: they are BaseException-only and
                # must propagate, not be swallowed.
                print(
                    f"      -> WARNING: skipping malformed post-mortem file "
                    f"'{f_path}' ({type(exc).__name__}: {exc})."
                )
                continue

        for reason in deviation_dict:
            if deviation_counts[reason] > 0:
                deviation_dict[reason] = round(deviation_sums[reason] / deviation_counts[reason], 3)
    except Exception as e:
        print(f"      -> Warning: Deviation calculation failed ({e}). Using defaults.")

    print(f"  -> Historical Execution Deviation Penalties: {deviation_dict}")
    return deviation_dict


def _replay_exit_tick(
    state,
    tick,
    tick_idx,
    n_ticks,
    p,
    grace_minutes,
    execution_start_hhmm: str = "09:30",
    exit_confirm_ticks: int = math_engine.EXIT_CONFIRM_TICKS,
):
    """Run ONE replay tick of the production exit path; mutate `state` in place.

    The SINGLE per-tick exit core — run_simulation, _collect_sim_returns and
    replay_exit_sequence all call it, so the replay's exit orchestration
    exists in exactly one place and the canonical math_engine primitives are
    invoked once. AC-6's test_c3_replay_exit_parity.py pins this core against
    the production exit path; replay_exit_sequence is its observability seam.
    tests/autotuner/test_c3_replay_internal_lockstep.py drives all three
    callers over the parity fixtures — a regression guard against a future
    re-inline reintroducing a divergent copy.

    Returns the resolve_trigger_priority exit reason string when an exit fires
    on this tick, else None. Faithfully reproduces the production exit
    decision (alpha_bot_execution.py) — see test_c3_replay_exit_parity.py's
    _production_exit_sequence, the reference this must match tick-for-tick.

    `state` is a mutable dict carrying per-position transient state across
    ticks within a single day. `n_ticks` is the day's tick count, used to
    derive time_ratio from the actual session length. `exit_confirm_ticks`
    (AC-4/F5) is the regime-resolved confirm-tick count a day-level caller
    may pass through to compute_exit_confirmation; it defaults to the same
    module constant compute_exit_confirmation itself defaults to, so a
    caller that never resolves a regime label sees unchanged behavior.
    """
    ret = tick.get("return", 0.0)
    # mc_prob may be the None sentinel (MC unavailable / insufficient MC
    # history) — production's run_monte_carlo None contract. mc_available
    # gates every MC-driven branch exactly as production does; an absent MC
    # opinion drives no disarm and no TP transition, but DOES fail-open the
    # arm (MA-10, below).
    mc = tick.get("mc_prob", 50.0)
    mc_available = mc is not None
    vol = tick.get("vol", 1.0)
    vwap_diff = tick.get("vwap_diff", 0.0)
    valid_vwap_weight = tick.get("valid_vwap_weight", 1.0)

    take_profit_mc = p.get("TAKE_PROFIT_MC_PCT", 5.0)
    trigger_threshold = p.get("TRIGGER_THRESHOLD_PCT", 15.0)

    # DATA PHASE (alpha_bot_execution.py:876-885): HWM tracking runs
    # unconditionally from the true 09:30 session open, even before
    # EXECUTION_START_TIME.
    if ret > state["hwm"]:
        state["hwm"] = ret
    safe_hwm = max(state["hwm"], ret)

    # AC-5/F6: production's ACTION PHASE (para-arm through firing, below)
    # does not run at all before EXECUTION_START_TIME — see
    # _replay_in_action_phase's docstring for the exact production gate this
    # mirrors. This is DISTINCT from the N-3 VWAP-grace suppression further
    # down, which only silences VWAP signals inside a grace window AFTER the
    # action phase has already opened.
    if not _replay_in_action_phase(tick_idx, execution_start_hhmm):
        return None

    # --- PARABOLIC SQUEEZE LOGIC ---
    para_threshold = p.get("PARABOLIC_VELOCITY_THRESHOLD", 2.0)
    effective_prev = ret if state["prev_return"] is None else state["prev_return"]
    _velocity, should_arm = math_engine.compute_para_arm_decision(
        current_return=ret,
        prev_return=effective_prev,
        para_threshold=para_threshold,
        currently_armed=state["para_armed"],
    )
    state["prev_return"] = ret
    if should_arm:
        state["para_armed"] = True

    # Arm/disarm decision (R3-b, MA-4 fix): delegated to the shared pure seam
    # so replay and production (alpha_bot_execution.py) share ONE decision
    # surface — R3-d retunes by replaying exit decisions, so a divergent exit
    # surface would invalidate the tune. Recovery-disarm is prob-based only
    # (current_return plays no role) and requires a disarm_confirm_ticks-tick
    # hysteresis ladder to avoid chatter near the boundary — see
    # math_engine.compute_arm_disarm_decision's docstring. MA-10 fail-open
    # (an absent MC opinion ARMS the stop) is preserved inside the seam.
    # is_triggered=False is a LITERAL, not read from `state` (which carries no
    # "triggered" key): the replay breaks on the first trigger and never
    # re-enters a tick with triggered state mid-day (see this function's
    # docstring), so the seam is called only on not-yet-triggered ticks by
    # construction — mirrors the is_triggered=False already passed below to
    # compute_exit_confirmation, compute_tp_confirmation and
    # compute_vwap_breakdown_update.
    armed_before_disarm_decision = state["armed"]
    state["armed"], state["disarm_confirm_count"] = math_engine.compute_arm_disarm_decision(
        prob_underperforming=mc,
        is_triggered=False,
        armed=armed_before_disarm_decision,
        disarm_confirm_count=state["disarm_confirm_count"],
        take_profit_mc_pct=take_profit_mc,
        trigger_threshold_pct=trigger_threshold,
    )
    # AC-7: the below_stop_count=0 reset is CALLER-side (the seam returns no
    # telemetry) — mirrors production's diff-before/after pattern
    # (alpha_bot_execution.py:1396-1411). No print here: the replay path has
    # never printed (it is a silent walk-forward simulation), matching the
    # pre-fix code.
    if armed_before_disarm_decision and not state["armed"]:
        state["below_stop_count"] = 0

    # --- TIME SQUEEZE DECAY LOGIC ---
    # time_ratio is derived from the ACTUAL session length so half-day
    # sessions reach full end-of-day stop tightening — the last tick of any
    # multi-tick session maps to 1.0, tick 0 to 0.0 (AC-7). The degenerate
    # single-tick day maps its lone tick to 1.0: production derives time_ratio
    # from wall-clock open/close datetimes, so a 1-bar session sits at the
    # close, not the open (M-C3.1 — faithful production parity).
    time_ratio = 1.0 if n_ticks == 1 else tick_idx / max(1, n_ticks - 1)
    dynamic_multiplier, dynamic_min_stop = math_engine.compute_time_squeeze_decay(time_ratio)

    active_stop_dist = math_engine.compute_active_trailing_stop(
        vol,
        dynamic_multiplier,
        dynamic_min_stop,
        state["para_armed"],
        state["breakeven_locked"],
        p.get("MAX_PARABOLIC_SQUEEZE", 0.50),
        squeeze_floor=p.get("MAX_SQUEEZE_FLOOR", _replay_squeeze_floor_default()),
    )
    base_stop = safe_hwm - active_stop_dist

    # is_triggered=False: the replay breaks on the first trigger and never
    # re-enters a tick with triggered state mid-day.
    state["hwm_hold_ticks"], state["breakeven_locked"], stop_level = (
        math_engine.compute_breakeven_update(
            ret,
            vol,
            base_stop,
            state["hwm_hold_ticks"],
            state["breakeven_locked"],
            False,
        )
    )

    # Check 1: Trailing Stop — the canonical math_engine primitive. It owns
    # MAGNITUDE_FLOOR_PCT and MC_BREAKDOWN_THRESHOLD; the replay never
    # duplicates those exit-rule literals (AC-1). The replay passes
    # prob_underperforming=mc to the SAME primitive, so the corrected gate
    # (>= MC_BREAKDOWN_THRESHOLD) flows through automatically — replay parity is
    # preserved by a value-preserving rename (mc local is unchanged).
    # exit_confirm_ticks (AC-4/F5) is passed EXPLICITLY, never left to
    # compute_exit_confirmation's own module-level default, so a day-level
    # caller's regime-resolved tick count actually reaches this call.
    state["below_stop_count"], is_trailing_hit = math_engine.compute_exit_confirmation(
        armed=state["armed"],
        is_triggered=False,
        current_return=ret,
        stop_trigger_level=stop_level,
        prob_underperforming=mc,
        current_below_stop_count=state["below_stop_count"],
        exit_confirm_ticks=exit_confirm_ticks,
    )

    # Check 2: Take Profit — the shared, pure math_engine.compute_tp_confirmation
    # (D-C3a). Production and the replay call the SAME TP confirm machine, so
    # the confirm-count constant (TP_CONFIRM_TICKS) has one source of truth.
    # An MC-unavailable tick while tp_armed resets above_tp_count to 0 — an
    # absent MC opinion cannot count toward a TP confirmation (AC-3).
    state["tp_armed"], state["above_tp_count"], is_tp_hit = math_engine.compute_tp_confirmation(
        mc_available=mc_available,
        prob_underperforming=mc,
        take_profit_mc_pct=take_profit_mc,
        current_return=ret,
        is_triggered=False,
        tp_armed=state["tp_armed"],
        above_tp_count=state["above_tp_count"],
    )

    # Check 3: VWAP Breakdown — canonical state machine + open-window grace.
    vwap_bleed_arm_pct = math_engine.compute_vwap_bleed_arm_threshold(
        vol, p.get("VWAP_BLEED_MULTIPLIER", 1.5)
    )
    (
        state["vwap_ticks"],
        state["vwap_bleed_ticks"],
        is_vwap_broken,
        is_vwap_bleed_broken,
    ) = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=valid_vwap_weight,
        weighted_vwap_diff=vwap_diff,
        safe_hwm=safe_hwm,
        current_return=ret,
        vwap_cross_hwm_pct=p.get("VWAP_CROSS_HWM_PCT", 1.0),
        vwap_bleed_arm_pct=vwap_bleed_arm_pct,
        vwap_bleed_ticks_threshold=p.get("VWAP_BLEED_TICKS", 10),
        current_vwap_ticks=state["vwap_ticks"],
        current_vwap_bleed_ticks=state["vwap_bleed_ticks"],
    )
    # Open-window grace: production suppresses BOTH VWAP signals for the
    # grace_minutes window AFTER EXECUTION_START_TIME (alpha_bot_execution.py
    # 1321-1326). AC-5 / N-3 — pre-fix the replay anchored the grace gate at
    # tick_idx 0 (session open), so a non-default EXECUTION_START_TIME ran
    # the production gate at e.g. 10:30-10:45 while the replay's gate ran
    # 09:30-09:44 — a complete misalignment. _replay_in_open_window_grace
    # derives the (h - 9) * 60 + (m - 30) start_offset from
    # execution_start_hhmm and gates on
    # [start_offset, start_offset + grace_minutes).
    if _replay_in_open_window_grace(tick_idx, execution_start_hhmm, grace_minutes):
        is_vwap_broken = False
        is_vwap_bleed_broken = False

    if is_trailing_hit or is_tp_hit or is_vwap_broken or is_vwap_bleed_broken:
        reason_str, _ = math_engine.resolve_trigger_priority(
            is_vwap_broken=is_vwap_broken,
            is_tp_hit=is_tp_hit,
            is_vwap_bleed_broken=is_vwap_bleed_broken,
            is_trailing_stop_hit=is_trailing_hit,
        )
        return reason_str
    return None


def _fresh_replay_state():
    """Return a fresh per-position transient-state dict for one simulated day.

    Re-created at the top of each day so every replay day is independent —
    the faithful mirror of production's daily database.wipe_transient_state
    (Cluster 1). prev_return starts None: cycle-1 velocity = 0.
    """
    return {
        "hwm": -999.0,
        "armed": False,
        "tp_armed": False,
        "para_armed": False,
        "breakeven_locked": False,
        "prev_return": None,
        "hwm_hold_ticks": 0,
        "below_stop_count": 0,
        "above_tp_count": 0,
        "disarm_confirm_count": 0,
        "vwap_ticks": 0,
        "vwap_bleed_ticks": 0,
    }


def replay_exit_sequence(ticks, params, *, grace_minutes):
    """Run the replay's per-tick exit loop over one day; return the decision trace.

    Pure helper exposing the replay's per-tick exit decision (AC-6). Returns
    one {"tick_idx", "exit_reason", "armed", "tp_armed", "para_armed"} dict
    per executed tick — exit_reason is the resolve_trigger_priority string on
    the tick the position exits, None on every non-exit tick; the three
    *_armed keys mirror test_c3_replay_exit_parity._production_exit_sequence's
    output shape so AC-6's parity battery can compare STATE, not just the
    exit decision. The loop stops after the first exit (production commits
    the exit and freezes the symphony for the day).

    Runs the SAME _replay_exit_tick per-tick core that run_simulation and
    _collect_sim_returns call — so this helper IS the replay path, not a copy.
    It is the observability seam the bit-identical AC-6 parity test compares
    against the production exit harness.

    AC-4 is deliberately NOT wired through here: day-level regime resolution
    lives above this single-day entry point (see _collect_sim_returns_dated),
    and _production_exit_sequence's AC-6 scenarios all use regime_label=None
    to match this function's implicit default — extending this signature
    would break that alignment (test_ac6_bar_level_replay_production_parity.py
    module docstring).
    """
    state = _fresh_replay_state()
    n_ticks = len(ticks)
    out = []
    # AC-5: resolve execution_start_hhmm at the call site so the grace gate
    # honors EXECUTION_START_TIME (matches production).
    execution_start_hhmm = _replay_execution_start_time()
    for tick_idx, tick in enumerate(ticks):
        reason = _replay_exit_tick(
            state,
            tick,
            tick_idx,
            n_ticks,
            params,
            grace_minutes,
            execution_start_hhmm=execution_start_hhmm,
        )
        out.append(
            {
                "tick_idx": tick_idx,
                "exit_reason": reason,
                "armed": state["armed"],
                "tp_armed": state["tp_armed"],
                "para_armed": state["para_armed"],
            }
        )
        if reason is not None:
            break
    return out


# Simulated-exit friction: a replay-only cost term subtracted at every
# triggered-exit accounting site below (_collect_sim_returns,
# _collect_sim_returns_dated, run_simulation), distinct from the deviation-
# dict `penalty` term. Value is 0.5 PERCENTAGE POINTS, derived from
# composer_backtest_client._DEFAULT_SLIPPAGE_PERCENT (0.005, a decimal
# fraction = 0.5%, composer_backtest_client.py:71) converted to this file's
# percent-point convention via RETURN_PCT_TO_FRACTION (100.0) — NOT the bare
# 0.005 literal, which would apply 100x too little friction at these
# percent-point-scaled subtraction sites. Closes the gap identified in
# docs/research/methodology-validation-2026-07.md (gross-of-cost replay
# objective vs Composer's own slippage-adjusted backtests). Deliberately kept
# out of math_engine.py (the live engine's decision-math module) and out of
# OPTUNA_SEARCH_SPACE_KEYS / the per-trial params dict / os.environ — this is
# a fixed replay-environment parameter, never tunable by the optimizer.
SIM_EXIT_FRICTION_PCT: float = 0.5


def _collect_sim_returns(
    p, history_data, acc_sym_ids, current_date_str, deviation_dict, *, return_dates=False
):
    """Run the guard-alpha simulation and return per-triggered-day guard_alpha values.

    Identical tick logic to run_simulation; returns a list instead of a scalar
    so the Sortino objective can compute risk-adjusted return across triggered days.

    Each triggered day contributes its RAW guard-alpha — no recency decay weight
    (Decision D5): walk-forward CV already supplies recency relevance by testing on
    the most recent fold, so an in-objective decay weight would double-count it and
    bias selection toward the last few weeks.

    Parameters
    ----------
    return_dates : bool (keyword-only, default False)
        When False (default): returns ``list[float]`` of guard_alpha values —
        the existing contract used by _haircut_select's daily_returns.
        When True: returns ``list[tuple[str, float]]`` of (date, guard_alpha)
        pairs — date-label explicit so the CSCV PBO gate can build a
        collision-free union dict across CPCV paths without the fragile
        positional pairing that path-concatenation would require.
    """
    daily_returns: list = []

    grace_minutes = _replay_grace_minutes()  # shared with production; resolved once per run
    execution_start_hhmm = _replay_execution_start_time()  # AC-5

    for sym_id in acc_sym_ids:
        dates_data = history_data.get(sym_id, {})
        # AC-4 (R2 residual): resolve the regime-conditional exit_confirm_ticks
        # fresh per simulated day, mirroring _collect_sim_returns_dated's
        # established pattern — the undated search-objective path (Optuna's
        # actual per-trial score) must share the same regime-faithful exit
        # semantics as the dated selection/diagnostic path, not the hardcoded
        # module default. sorted_dates/date_to_idx are precomputed once per
        # symphony (not resorted per date).
        sorted_dates = sorted(dates_data.keys())
        date_to_idx = {d: i for i, d in enumerate(sorted_dates)}
        for date, ticks in dates_data.items():
            if not ticks:
                continue

            # Per-position transient state — re-initialized INSIDE the per-day
            # loop via the canonical constructor so every simulated day is
            # independent, mirroring production's daily
            # database.wipe_transient_state (Cluster 1). _fresh_replay_state()
            # is the single source of truth for the replay state shape — the
            # same constructor replay_exit_sequence uses.
            day_state = _fresh_replay_state()

            triggered_return = None
            eod_return = ticks[-1]["return"]
            n_ticks = len(ticks)
            _regime_exit_ticks = _replay_resolve_regime_exit_ticks(
                dates_data, sorted_dates, date_to_idx[date]
            )

            # Per-tick exit loop via the single shared core _replay_exit_tick —
            # the SAME per-tick exit path run_simulation and replay_exit_sequence
            # use. One copy of the exit orchestration; no duplication. AC-6's
            # test_c3_replay_exit_parity.py pins it against the production exit
            # path.
            for tick_idx, tick in enumerate(ticks):
                reason_str = _replay_exit_tick(
                    day_state,
                    tick,
                    tick_idx,
                    n_ticks,
                    p,
                    grace_minutes,
                    execution_start_hhmm=execution_start_hhmm,
                    exit_confirm_ticks=_regime_exit_ticks,
                )
                if reason_str is not None:
                    penalty = deviation_dict.get(reason_str, -0.20)
                    # SIM_EXIT_FRICTION_PCT is a distinct term (never folded
                    # into `penalty`) -- see the constant's definition above
                    # _collect_sim_returns for the derivation (AC-2v2).
                    triggered_return = tick.get("return", 0.0) + penalty - SIM_EXIT_FRICTION_PCT
                    break

            if triggered_return is not None:
                guard_alpha = triggered_return - eod_return
                if return_dates:
                    daily_returns.append((date, guard_alpha))
                else:
                    daily_returns.append(guard_alpha)

    return daily_returns


def collect_if_held_daily_returns(history_data: dict, sym_id: str) -> list:
    """Return the full if-held daily EOD return series for one symphony --
    ticks[-1]["return"] for EVERY date in history_data[sym_id], unconditionally
    (never filtered to triggered days, and NEVER the guard_alpha delta
    _collect_sim_returns computes -- see this feature's recon finding 1).
    Pure extraction, no re-simulation, no I/O.
    """
    dates_data = history_data.get(sym_id, {})
    result = []
    for date in sorted(dates_data.keys()):
        ticks = dates_data[date]
        if not ticks:
            continue
        result.append(ticks[-1]["return"])
    return result


def build_if_held_replay_series(symphony_id: str) -> "list | None":
    """ROUTE-FACING ORCHESTRATION SEAM. PM-RULED CONTRACT (2026-07-23, resolves
    the data-sourcing open question flagged during RED-writing): CACHE-HIT-ONLY.

    This function may read synthetic_history's EXISTING file cache (e.g. via
    synthetic_history.load_cached_history) and any already-persisted replay
    data, then return collect_if_held_daily_returns(...) on whatever it finds.
    It must NEVER trigger a live fetch (synthetic_history.fetch_bars or
    synthetic_history.generate_synthetic_history's fetch fallback branch) --
    on a cache miss it returns None immediately, and the caller (the GET
    route) renders the honest degraded state for that sample ("replay sample
    unavailable -- populates after the next autotune/replay run"), falling
    back to a shadow-only row per AC-8.

    WHY: Architecture Constraint 5 (the dashboard/UI never reruns the engine
    -- assembling 250-day history on request is engine machinery, not a read);
    request latency (a cold fetch can take minutes inside a dashboard GET);
    and the standing bill-protection directive (a dashboard refresh must never
    be able to drive Alpaca fetch volume). Pinned by
    tests/autotuner/test_build_if_held_replay_series.py (accessor-level: cold
    cache -> None, synthetic_history.fetch_bars never called) and
    tests/app/test_guard_alpha_preconditions_route.py (route-level: same
    guarantee end-to-end through the real route).

    Cache-key derivation and the actual file read are delegated ENTIRELY to
    synthetic_history.get_cached_synthetic_history_only, which shares its
    tickers -> holdings-hash -> versioned-filename derivation with
    generate_synthetic_history's own nightly fetch-and-write via one common
    helper (synthetic_history._resolve_history_cache_key) -- this function
    never re-derives that key independently, so it cannot silently drift
    from the writer and permanently miss a genuine same-day, same-holdings
    cache hit (PM hard requirement, 2026-07-23).
    """
    bot_state = database.load_state()
    if symphony_id not in bot_state:
        return None

    # ET "today" via synthetic_history.utc_to_eastern -- the SAME DST-aware
    # conversion generate_synthetic_history itself uses to resolve "today"
    # (synthetic_history.py:540), already a module-level dependency of this
    # file. Deliberately NOT alpha_bot_execution.get_current_et: that module
    # does load_dotenv + reads several credentials at import time and pulls
    # a wide production import graph (including autotuner itself) -- heavy
    # and side-effectful to import on-demand from an advisory dashboard GET
    # just for a date string.
    current_date_str = synthetic_history.utc_to_eastern(datetime.now(UTC)).strftime("%Y-%m-%d")

    history_data = synthetic_history.get_cached_synthetic_history_only(bot_state, current_date_str)
    if history_data is None or symphony_id not in history_data:
        return None

    series = collect_if_held_daily_returns(history_data, symphony_id)
    return series if series else None


def _replay_resolve_regime_exit_ticks(dates_data: dict, sorted_dates: list, date_idx: int) -> int:
    """AC-4/F5: recompute the regime-conditional exit_confirm_ticks fresh for
    one replay day, using ONLY EOD daily returns from dates STRICTLY BEFORE
    ``sorted_dates[date_idx]`` (no lookahead — walk-forward integrity).

    Mirrors production's apply_regime_exit_adjustment(regime_label, base_ticks)
    composition (alpha_bot_execution.py:1436-1448), but resolves the label via
    regime_classifier.classify_regime() over the replay's own trailing history
    instead of the live per-symphony regime-label cache accessor in
    database.py — reading that FORBIDDEN in replay code (ruled contract, PM
    addendum 2 @ a46be889): it is a single-row latest-wins live table with no
    per-historical-date granularity, so consulting it for a walk-forward day
    would inject TODAY's label into every one of the ~250 replayed days.

    Insufficient trailing history (< regime_classifier.MIN_LABEL_SERIES_LENGTH)
    -> classify_regime returns None -> apply_regime_exit_adjustment's own safe
    default (base_ticks unchanged) — never an invented replay-only fallback.

    `sorted_dates`/`date_idx` are precomputed once per symphony by the caller
    (not resorted per date) so this stays O(window) per call.
    """
    trailing_dates = sorted_dates[:date_idx][-regime_classifier.MIN_LABEL_SERIES_LENGTH :]
    trailing_returns = [
        dates_data[d][-1].get("return", 0.0) / RETURN_PCT_TO_FRACTION
        for d in trailing_dates
        if dates_data.get(d)
    ]
    label = regime_classifier.classify_regime(trailing_returns)
    return math_engine.apply_regime_exit_adjustment(
        regime_label=label, base_ticks=math_engine.EXIT_CONFIRM_TICKS
    )


def _collect_sim_returns_dated(
    p, history_data, acc_sym_ids, current_date_str, deviation_dict, *, score_dates=None
):
    """Date-labeled variant of _collect_sim_returns for the CSCV PBO gate.

    Returns ``list[tuple[str, float]]`` of (date, guard_alpha) pairs so the
    objective closure can build a collision-free cscv_date_returns union dict
    across CPCV splits.

    This function is intentionally NOT a wrapper around _collect_sim_returns —
    it has its own implementation so that test mocks of ``autotuner._collect_sim_returns``
    (which return flat floats) do not intercept the dated-variant call.  The two
    functions share the same per-tick exit logic via ``_replay_exit_tick`` and
    ``_fresh_replay_state``; the only difference is the return type.

    AC-4/F5: this is the per-date replay loop that resolves the
    regime-conditional exit_confirm_ticks fresh per simulated day (see
    _replay_resolve_regime_exit_ticks) — the day-level orchestration
    test_ac4_regime_conditional_exit_ticks.py's no-lookahead test drives
    directly.

    Parameters
    ----------
    score_dates : set[str] | None (keyword-only, default None)
        AC-1-adjacent (regime-lookback chronology purity, ADDENDUM 5/6): when
        provided, restricts which dates actually undergo the expensive
        per-tick replay and contribute to the returned list — dates in
        ``history_data`` but NOT in ``score_dates`` are skipped entirely (no
        replay, no output entry). Every date in ``history_data`` still
        participates in ``sorted_dates``/regime-lookback chronology for
        WHATEVER dates ARE scored, regardless of score_dates membership —
        this is the decoupling that lets a per-split CPCV caller pass the
        FULL per-symphony history (correct, gap-free regime chronology) while
        restricting each split's own scored-date set, instead of the old
        pattern of pre-filtering history_data down to one unit's dates (which
        would corrupt the trailing-window chronology into a gappy sample —
        see test_ac1_regime_lookback_chronology_purity.py). None (default)
        preserves the original behavior: every date in history_data is scored.
    """
    dated_returns: list[tuple[str, float]] = []
    grace_minutes = _replay_grace_minutes()
    execution_start_hhmm = _replay_execution_start_time()

    for sym_id in acc_sym_ids:
        dates_data = history_data.get(sym_id, {})
        sorted_dates = sorted(dates_data.keys())
        date_to_idx = {d: i for i, d in enumerate(sorted_dates)}
        for date, ticks in dates_data.items():
            if not ticks:
                continue
            if score_dates is not None and date not in score_dates:
                # Not a scored date for this call — skip the expensive
                # per-tick replay entirely. The date remains present in
                # dates_data/sorted_dates so regime-lookback for OTHER
                # (scored) dates still sees its EOD return.
                continue
            day_state = _fresh_replay_state()
            triggered_return = None
            eod_return = ticks[-1]["return"]
            n_ticks = len(ticks)
            _regime_exit_ticks = _replay_resolve_regime_exit_ticks(
                dates_data, sorted_dates, date_to_idx[date]
            )
            for tick_idx, tick in enumerate(ticks):
                reason_str = _replay_exit_tick(
                    day_state,
                    tick,
                    tick_idx,
                    n_ticks,
                    p,
                    grace_minutes,
                    execution_start_hhmm=execution_start_hhmm,
                    exit_confirm_ticks=_regime_exit_ticks,
                )
                if reason_str is not None:
                    penalty = deviation_dict.get(reason_str, -0.20)
                    # SIM_EXIT_FRICTION_PCT is a distinct term (never folded
                    # into `penalty`) -- see the constant's definition above
                    # _collect_sim_returns for the derivation (AC-2v2).
                    triggered_return = tick.get("return", 0.0) + penalty - SIM_EXIT_FRICTION_PCT
                    break
            if triggered_return is not None:
                guard_alpha = triggered_return - eod_return
                dated_returns.append((date, guard_alpha))

    return dated_returns


# Partial-sentinel detection threshold: a CPCV trial's value is the MEAN across
# _CPCV_N_SPLITS split scores (AC-1, R2: split-level scoring — team-lead RULED
# Option A, superseding the old 5-path aggregate; see DECISIONS.md
# DE-MATH-R2-001). If ONE split is a Sortino sentinel (1e6, a zero-downside
# split) and the rest are small, the mean is ~= _SORTINO_SENTINEL / _CPCV_N_SPLITS
# — far above any genuine Sortino/CRRA-EU score, yet != 1e6 so an exact-float
# compare misses it. Any value at or above this threshold contains a sentinel
# split and is degenerate.
_PARTIAL_SENTINEL_MEAN_THRESHOLD = math_engine._SORTINO_SENTINEL / _CPCV_N_SPLITS


def _trial_has_sentinel_path(t) -> bool:
    """True if the trial's persisted per-split CPCV scores include a Sortino sentinel.

    Split-score-level detection is the GAP-FREE check: a trial whose value is the
    mean across _CPCV_N_SPLITS splits can hide a sentinel split (1e6) when the OTHER
    splits are net-negative enough to drag the mean below the aggregate threshold
    (e.g. a split at the CRRA wealth floor ~-999, or large-negative Sortino). The
    objective persists split_scores (set_user_attr) so the filter can see the
    sentinel directly rather than inferring it from the aggregate value.
    """
    if not hasattr(t, "user_attrs"):
        return False
    split_scores = t.user_attrs.get("split_scores")
    if not split_scores:
        return False
    return any(s == math_engine._SORTINO_SENTINEL for s in split_scores)


def filter_sortino_sentinels(trials):
    """Exclude zero-downside degenerate trials from a haircut candidate set.

    Drops a trial if ANY of:
      - value is None (no usable objective);
      - a persisted per-split score equals math_engine._SORTINO_SENTINEL — the
        gap-free SPLIT-SCORE-level check (see _trial_has_sentinel_path); catches a
        one-sentinel-split mean even when net-negative other splits drag the mean
        below the aggregate threshold;
      - value >= _SORTINO_SENTINEL / _CPCV_N_SPLITS — the aggregate fallback for
        trials WITHOUT persisted split_scores (a pure sentinel, or a partial mean
        whose non-sentinel splits are non-negative).
    A sentinel's magnitude would dominate the cross-trial BHY/haircut distribution
    and let a degenerate trial masquerade as a genuine signal. Returns a new list
    preserving input order.
    """
    return [
        t
        for t in trials
        if t.value is not None
        and not _trial_has_sentinel_path(t)
        and t.value < _PARTIAL_SENTINEL_MEAN_THRESHOLD
    ]


def _haircut_select(
    completed_trials,
    n_effective: "int | None" = None,
    tstat_fn=compute_sortino_tstat,
    gamma: "float | None" = None,
):
    """Apply the Harvey & Liu selection-bias haircut to a set of completed trials.

    Each completed trial must carry its in-sample validation return series under
    the ``daily_returns`` user-attr (persisted by the objective). The haircut, per
    trial i over the N sentinel-filtered trials:
      1. t-statistic  t_i  = Sortino_i / SE_bootstrap_i  (Decision D10 / RM-H1 —
         bootstrap standard error from the daily_returns series; Efron 1979)
      2. one-sided p  p_i  = clamped 1 - Φ(t_i)
      3. BHY adjust   p_adj = benjamini_hochberg_adjust over the N_effective p-values
                       (Shape A: append S = n_effective - len(p_values) copies of 1.0
                        so the Yekutieli c(N) is computed over the honest N; plan D3)
      4. selection    winner = argmin p_adj over original trials only, deployable
                       iff p_adj <= HARVEY_LIU_FDR_Q

    ``n_effective`` is the honest multiple-testing count from compute_n_effective.
    When None (default), it falls back to len(completed_trials) — preserving backward
    compatibility with the NN1-honest steady-state where S=0 (plan D2).

    ``tstat_fn`` must accept a ``list[float]`` of daily returns and return a float
    t-statistic. The default is ``compute_sortino_tstat`` for backward compatibility
    with the Sortino objective branch. Pass ``compute_crra_eu_tstat`` for the CRRA-EU
    objective path. Swapping ``tstat_fn`` is the ONLY permitted change to this function
    per the BHY slice binding — all other haircut machinery is byte-identical.

    ``gamma`` is the CRRA risk-aversion coefficient used for the U-transform in the
    CRRA-EU branch. Required when tstat_fn is compute_crra_eu_tstat; ignored for the
    Sortino branch. When None and the CRRA-EU branch is active, defaults to 2.0
    (prudential Phase-1 value) — callers should always pass the bundle-frozen gamma.

    Sentinel filter: trials with ``value == math_engine._SORTINO_SENTINEL`` (1e6) are
    excluded before the haircut regardless of ``tstat_fn``. The filter is a no-op for
    the CRRA-EU path but must remain for legacy Sortino sweeps.

    Returns ``(winner_trial, winner_p_adj, winner_tstat)`` — the BHY-winning
    trial, its adjusted p-value, and its t-statistic. ``winner_trial`` is None
    when no trial clears the FDR gate (the AI proposal must then fall through to
    the fallback/default cascade) or when fewer than 1 trial is available.

    Reference: Harvey & Liu 2015, DOI 10.3905/jpm.2015.42.1.013;
    Efron 1979 (bootstrap SE).
    """
    if not completed_trials:
        return None, None, None

    # Sentinel filter: exclude zero-downside degenerate trials regardless of objective.
    # A sentinel's magnitude would dominate the cross-trial distribution in the Sortino
    # path; the filter is a no-op for CRRA-EU but must remain for both paths. Uses the
    # named helper so partial-sentinel CPCV means (one sentinel path) are caught too.
    filtered_trials = filter_sortino_sentinels(completed_trials)
    if not filtered_trials:
        return None, None, None

    # CRRA-001 fix: U-transform daily_returns before calling tstat_fn in the CRRA-EU
    # branch. The docstring contract at run_simulation_crra_eu:1083-1084 specifies
    # that the haircut re-transforms via derive_floored_wealth_argument +
    # compute_crra_utility. Raw percent returns passed to compute_crra_eu_tstat
    # compute mean(r)/sd(r)*sqrt(T) — the H-6 Sharpe-like category error.
    _crra_gamma = gamma if gamma is not None else float(database.PHASE1_THEORY_GAMMA)

    tstats = []
    # H3 fix: iterate filtered_trials (sentinel-removed), NOT completed_trials, so the
    # t-stat / p-value index space matches the returned-trial index space at the
    # `filtered_trials[winner_idx]` return below. Looping completed_trials let a
    # sentinel's strong series win the argmin while the return indexed into the
    # shorter filtered list — returning the WRONG trial with the SENTINEL's t-stat
    # (or IndexError with a trailing sentinel). Both live callers pre-filter, so this
    # is byte-identical in production (filtered_trials == completed_trials there).
    for trial_idx, t in enumerate(filtered_trials):
        series = t.user_attrs.get("daily_returns", []) if hasattr(t, "user_attrs") else []
        # H-1 fix: call the passed tstat_fn (the loop previously hardcoded the
        # Sortino t-stat, defeating the tstat_fn parameter). The two objective
        # t-stat functions have incompatible call shapes, so dispatch by identity:
        if tstat_fn is compute_sortino_tstat:
            # Sortino branch: raw return series + a deterministic per-trial seed
            # so re-running an identical study produces an identical haircut
            # decision (AC-1 caller-side determinism pin; the trial index is a
            # stable within-study key). No U-transform.
            tstats.append(tstat_fn(series, seed=trial_idx))
        else:
            # CRRA-EU branch (CRRA-001): daily_returns are stored as RAW PERCENT
            # (run_simulation_crra_eu provenance, :1396-1399). compute_crra_eu_tstat
            # expects utility values, so re-transform each return through the
            # canonical wealth-argument + CRRA-utility path before scoring —
            # passing raw percent would compute mean(r)/sd(r)*sqrt(T), the H-6
            # Sharpe-like category error. _crra_gamma (derived above) is the
            # bundle-frozen risk-aversion coefficient. No seed (no seed param).
            u_series = [
                math_engine.compute_crra_utility(
                    derive_floored_wealth_argument(r / RETURN_PCT_TO_FRACTION),
                    _crra_gamma,
                )
                for r in series
            ]
            tstats.append(tstat_fn(u_series))
    p_values = [compute_haircut_pvalue(ts) for ts in tstats]

    # Shape A: pad the p-value list with S copies of 1.0 (at-the-cap = "tested and
    # rejected at no significance") so the Yekutieli c(N) factor is computed over
    # the honest N_effective rather than just len(p_values).  Zero change to
    # benjamini_hochberg_adjust itself — BHY preservation contract (plan D3).
    n_trials = len(p_values)
    effective_n = n_trials if n_effective is None else n_effective
    s = max(0, effective_n - n_trials)
    padded = p_values + [1.0] * s

    p_adj_all = benjamini_hochberg_adjust(padded)
    # The winner is selected over the original n_trials, not the padded tail.
    p_adj = p_adj_all[:n_trials]

    winner_idx = min(range(len(p_adj)), key=lambda i: p_adj[i])
    if p_adj[winner_idx] > HARVEY_LIU_FDR_Q:
        # No trial clears the FDR gate — the trial set is statistically
        # indistinguishable from noise; reject the AI proposal in full.
        return None, p_adj[winner_idx], tstats[winner_idx]
    return filtered_trials[winner_idx], p_adj[winner_idx], tstats[winner_idx]


def run_simulation(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
    """Legacy Sortino + loss-aversion objective (M1: aliased as run_simulation_sortino_legacy).

    Retained for the OOS cascade (AI/fallback/default selection), which compares
    three param sets on the same metric. The CRRA-EU branch uses
    run_simulation_crra_eu instead; the discriminator in run_autotuner routes
    based on objective_kind from spec_facets.

    The six original loss-aversion constant names (MISSED_UPSIDE_PENALTY_MULT etc.)
    are absent from module scope (T6 requirement). The SORTINO_OBJ_* module-level
    constants carry the values; the RUN_SIM_* local aliases below reference them
    so the penalty block stays readable and the T6 test's requirement that RUN_SIM_*
    names are NOT at module scope is satisfied.
    """
    # Local aliases for penalty scalars/thresholds (M1 T6: confined to this function).
    # Values sourced from SORTINO_OBJ_* module-level constants (AC-4 named constants).
    # NAME-002 (sprint-2-audit a6e4d9f8): the three _PCT suffixes are inconsistent with
    # the module-level SORTINO_OBJ_* names (which carry no _PCT suffix). They cannot be
    # renamed here because tests/autotuner/test_m1_crra_eu_objective.py T6 pins these
    # exact names as the contractual RUN_SIM_* set (test_run_sim_constants_not_at_module_scope).
    # A rename would require a coordinated test update — out of scope for this hotfix pass.
    RUN_SIM_MISSED_UPSIDE_MULT = SORTINO_OBJ_MISSED_UPSIDE_MULT
    RUN_SIM_MISSED_UPSIDE_THRESHOLD_PCT = (
        SORTINO_OBJ_MISSED_UPSIDE_THRESHOLD  # _PCT suffix: T6 contract
    )
    RUN_SIM_DRAWDOWN_MULT = SORTINO_OBJ_DRAWDOWN_MULT
    RUN_SIM_DRAWDOWN_THRESHOLD_PCT = SORTINO_OBJ_DRAWDOWN_THRESHOLD  # _PCT suffix: T6 contract
    RUN_SIM_DRAWDOWN_MIN_GAIN_PCT = SORTINO_OBJ_DRAWDOWN_MIN_GAIN  # _PCT suffix: T6 contract
    RUN_SIM_NEGATIVE_GUARD_ALPHA_MULT = SORTINO_OBJ_NEGATIVE_GUARD_ALPHA_MULT

    total_guard_alpha = 0.0
    grace_minutes = _replay_grace_minutes()  # shared with production; resolved once per run
    execution_start_hhmm = _replay_execution_start_time()  # AC-5

    for sym_id in acc_sym_ids:
        dates_data = history_data.get(sym_id, {})
        # AC-4 (R2 residual): resolve the regime-conditional exit_confirm_ticks
        # fresh per simulated day, mirroring _collect_sim_returns_dated's
        # established pattern — the adoption cascade (oos_alpha/
        # fallback_oos_alpha/default_oos_alpha) must share the same
        # regime-faithful exit semantics as the selection/diagnostic path.
        sorted_dates = sorted(dates_data.keys())
        date_to_idx = {d: i for i, d in enumerate(sorted_dates)}
        for date, ticks in dates_data.items():
            if not ticks:
                continue

            # Per-position transient state — re-initialized INSIDE the per-day
            # loop via the canonical constructor so every simulated day is
            # independent, mirroring production's daily
            # database.wipe_transient_state (Cluster 1). _fresh_replay_state()
            # is the single source of truth for the replay state shape — the
            # same constructor replay_exit_sequence uses.
            day_state = _fresh_replay_state()

            triggered_return = None
            eod_return = ticks[-1]["return"]
            day_max_return = max(t.get("return", 0.0) for t in ticks)
            n_ticks = len(ticks)
            _regime_exit_ticks = _replay_resolve_regime_exit_ticks(
                dates_data, sorted_dates, date_to_idx[date]
            )

            # Per-tick exit loop via the single shared core _replay_exit_tick —
            # the SAME per-tick exit path _collect_sim_returns and
            # replay_exit_sequence use. One copy of the exit orchestration; no
            # duplication. AC-6's test_c3_replay_exit_parity.py pins it against
            # the production exit path.
            for tick_idx, tick in enumerate(ticks):
                reason_str = _replay_exit_tick(
                    day_state,
                    tick,
                    tick_idx,
                    n_ticks,
                    p,
                    grace_minutes,
                    execution_start_hhmm=execution_start_hhmm,
                    exit_confirm_ticks=_regime_exit_ticks,
                )
                if reason_str is not None:
                    penalty = deviation_dict.get(reason_str, -0.20)
                    # SIM_EXIT_FRICTION_PCT is a distinct term (never folded
                    # into `penalty`) -- see the constant's definition above
                    # _collect_sim_returns for the derivation (AC-2v2).
                    triggered_return = tick.get("return", 0.0) + penalty - SIM_EXIT_FRICTION_PCT
                    break

            if triggered_return is not None:
                # safe_hwm at the exit tick: the per-tick core lifts hwm before
                # the exit check, so the running HWM already includes the exit
                # tick's return — safe_hwm == day_state["hwm"].
                safe_hwm = day_state["hwm"]
                guard_alpha = triggered_return - eod_return
                missed_upside = day_max_return - triggered_return
                drawdown_from_peak = safe_hwm - triggered_return

                # Loss-averse utility — see the penalty-constant block above.
                # Each triggered day contributes its RAW penalised guard-alpha;
                # no recency-decay weight (Decision D5 — walk-forward CV already
                # supplies recency relevance, an in-objective weight double-counts it).
                # 1. Penalize missed upside (exiting too early before a run).
                if missed_upside > RUN_SIM_MISSED_UPSIDE_THRESHOLD_PCT:
                    total_guard_alpha -= missed_upside * RUN_SIM_MISSED_UPSIDE_MULT

                # 2. Penalize peak-to-exit drawdown (giving back too much profit)
                # — only for positions that reached a meaningful gain.
                if (
                    safe_hwm > RUN_SIM_DRAWDOWN_MIN_GAIN_PCT
                    and drawdown_from_peak > RUN_SIM_DRAWDOWN_THRESHOLD_PCT
                ):
                    total_guard_alpha -= drawdown_from_peak * RUN_SIM_DRAWDOWN_MULT

                # 3. Apply standard EOD-based guard alpha; negative guard-alpha
                # is penalised by the loss-aversion multiplier (asymmetry).
                if guard_alpha < 0:
                    total_guard_alpha += guard_alpha * RUN_SIM_NEGATIVE_GUARD_ALPHA_MULT
                else:
                    total_guard_alpha += guard_alpha

    return -total_guard_alpha


# M1 alias: run_simulation_sortino_legacy is the canonical name for the legacy
# Sortino + loss-aversion objective going forward. run_simulation is kept as the
# primary def (satisfying C4 AST inspection) and run_simulation_sortino_legacy
# is a callable alias for explicit legacy-branch callers and T6 callable tests.
# NAME-001 (sprint-2-audit a6e4d9f8): "sortino" and "legacy" embed change history
# rather than behavior. The auditor recommends run_simulation_sortino_guard_alpha
# or reverting to run_simulation. Cannot rename here: T6 contract test
# test_run_simulation_sortino_legacy_function_exists in test_m1_crra_eu_objective.py
# pins this exact name. A rename requires a coordinated test update.
run_simulation_sortino_legacy = run_simulation


def run_simulation_crra_eu(
    p, history_data, acc_sym_ids, current_date_str, deviation_dict, *, gamma: float
) -> float:
    """CRRA-EU objective for one param set over a history fold.

    Replaces run_simulation_sortino_legacy for the CRRA-EU branch. Returns
    mean(U) over all triggered days, where each day's U is computed via
    compute_crra_utility on the floored wealth argument W = max(WEALTH_ARG_FLOOR,
    1 + r_i / RETURN_PCT_TO_FRACTION).

    Callers store the RAW guard-alpha series (not U) in trial.set_user_attr so
    a future gamma re-pre-registration doesn't silently stale stored U values.
    The haircut re-transforms daily_returns through derive_floored_wealth_argument
    + compute_crra_utility in one place (compute_crra_eu_tstat path).

    Parameters
    ----------
    p : dict
        Strategy parameter set.
    history_data : dict
        {sym_id: {date: [ticks]}} history for the fold.
    acc_sym_ids : list[str]
        Symphony IDs to simulate.
    current_date_str : str
        ISO date string for the current run.
    deviation_dict : dict
        Historical execution deviation by trigger reason.
    gamma : float
        CRRA risk-aversion coefficient, sourced from spec_facets (NOT a
        module-level constant — gamma must be read from spec_bundles/spec_facets
        so it is frozen + content-hashed; a source-code constant fails that
        contract).

    Returns
    -------
    float
        mean(U) over all triggered-day utility values. Returns 0.0 if no
        triggered days exist (no signal — U-series empty).
    """
    daily_returns_pct = _collect_sim_returns(
        p, history_data, acc_sym_ids, current_date_str, deviation_dict
    )
    if not daily_returns_pct:
        return 0.0

    # NOTE-1 conversion: autotuner return series are in PERCENT
    # (synthetic_history tick['return'] = agg_ret * 100.0). CRRA requires
    # a decimal-fraction wealth ratio. Divide by RETURN_PCT_TO_FRACTION before
    # passing to compute_crra_eu_objective.
    daily_returns_fraction = [r / RETURN_PCT_TO_FRACTION for r in daily_returns_pct]
    return math_engine.compute_crra_eu_objective(daily_returns_fraction, gamma)


def _apply_optuna_archive_migration_if_needed():
    """
    One-time idempotent migration: renames bare legacy study names in
    optuna_studies.db with a LEGACY__ prefix so they are distinguishable
    from new timestamp__symphony names.

    Reads migrations/optuna_001_archive_accumulated_studies.sql and applies
    it only if at least one non-prefixed, non-timestamp study exists.
    Safe to call on every startup — the SQL is idempotent.
    """
    import os
    import pathlib as _pathlib
    import sqlite3 as _sqlite3

    db_path = "optuna_studies.db"
    if not os.path.exists(db_path):
        return

    migration_path = (
        _pathlib.Path(__file__).parent / "migrations" / "optuna_001_archive_accumulated_studies.sql"
    )
    if not migration_path.exists():
        return

    try:
        conn = _sqlite3.connect(db_path)
        # Check for any legacy (non-prefixed) studies
        rows = conn.execute(
            "SELECT COUNT(*) FROM studies WHERE study_name NOT LIKE 'LEGACY__%' AND INSTR(study_name, '__') = 0"  # noqa: E501  # un-wrappable long line
        ).fetchone()
        needs_migration = rows and rows[0] > 0
        if needs_migration:
            sql = migration_path.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.commit()
            print(f"  -> Applied optuna_001 archive migration ({rows[0]} legacy studies renamed).")
        conn.close()
    except Exception as exc:
        print(f"  -> WARNING: optuna_001 archive migration failed (non-fatal): {exc}")


def validate_search_space_nn1() -> None:
    """Fail-loud if OPTUNA_SEARCH_SPACE_KEYS contains a known-frozen facet name.

    Last-line defence against a future PR that adds e.g. 'gamma' to the search
    space without removing the spec_bundles registration. Called at the top of
    run_autotuner BEFORE optuna.create_study (D5 wiring).

    Reference: council synthesis §2.5; plan D5.
    """
    _forbidden_in_search_space = frozenset(
        {
            "gamma",
            "utility_family",
            "wealth_argument",
            "generator_family",
            "horizon_convention",
            "lambda",
            "regime_bucket_thresh",
        }
    )
    leaked = OPTUNA_SEARCH_SPACE_KEYS & _forbidden_in_search_space
    if leaked:
        raise RuntimeError(
            f"NN1 VIOLATION: search space contains theory-frozen facet(s) "
            f"{sorted(leaked)} — see council synthesis §2.5 and the "
            f"NN1 disclosure block in autotuner.py. Refusing to start."
        )


def validate_nn1_compliance(spec_bundle_id: int) -> "tuple[bool, list[str]]":
    """Return (is_nn1_honest, violations).

    Reads spec_facets rows for the bundle AND researcher_dof_ledger rows for
    the same spec_bundle_id. NN1-honest iff:
      (a) every spec_facets.freeze_discipline is in NN1_HONEST_DISCIPLINES, AND
      (b) no researcher_dof_ledger row has evidence_source='OOS' for this bundle.

    Violation (b) is the stricter frozen-eval peek — a facet frozen after OOS
    inspection is a correctness defect regardless of its freeze_discipline label.
    Both queries are state-DB only (architecture constraint 3 compliant).

    Default-deny: any freeze_discipline NOT in NN1_HONEST_DISCIPLINES is treated
    as a violation, including unknown/forward-compat values (plan risk callout —
    silent fall-through is forbidden).

    On detecting BACKTEST_SELECTION facets, each is written to
    researcher_dof_ledger via database.insert_dof_ledger_row with
    evidence_source='BACKTEST_SELECTION' so the +S contribution to N_effective
    is recorded for the BHY haircut.

    OOS-peek violations are labelled distinctly so operators see the severity
    gradient (OOS is worse than BACKTEST_SELECTION per synthesis §2.5).

    Reference: council synthesis §2.5; plan D2.
    """
    violations: list[str] = []

    # Resolve bundle_hash from integer id — spec_bundle_id is the PK, not the hash.
    # Guard: id is nullable on DBs that applied migration 016 before 022 backfilled
    # rowid into the id column. On such DBs, WHERE id = ? returns no rows even for
    # existing bundles. insert_spec_bundle now backfills id immediately on every
    # INSERT (post-022 behaviour) so this path is closed for new rows; for pre-backfill
    # rows migration 022 runs UPDATE spec_bundles SET id = rowid WHERE id IS NULL.
    # If id IS NULL (pre-backfill state) the lookup returns nothing and we treat
    # it as "bundle not found" — the operator must re-run run_migrations() to backfill.
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT bundle_hash FROM spec_bundles WHERE id = ?", (spec_bundle_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        violations.append(
            f"spec_bundle_id {spec_bundle_id}: bundle not found "
            f"(id column may be NULL pre-migration-022 backfill — run run_migrations())"
        )
        return False, violations

    bundle_hash = row[0]

    # Check spec_facets discipline for each facet — default-deny.
    facets = database.get_spec_facets_for_bundle(bundle_hash)
    for facet in facets:
        discipline = facet["freeze_discipline"]
        name = facet["facet_name"]
        if discipline not in NN1_HONEST_DISCIPLINES:
            if discipline == FREEZE_DISCIPLINE_BACKTEST_SELECTION:
                violations.append(f"{name}: BACKTEST_SELECTION")
                # Write this violation to researcher_dof_ledger (+S contribution).
                database.insert_dof_ledger_row(
                    facet_name=name,
                    facet_category="specification",
                    decision_type="SEARCHED",
                    evidence_source="BACKTEST_SELECTION",
                    n_configs_searched=1,
                    touched_frozen_eval=0,
                    spec_bundle_id=bundle_hash,
                    justification=(
                        f"NN1 violation detected by validate_nn1_compliance: "
                        f"{name} was frozen by BACKTEST_SELECTION (council §2.5 hard gate)"
                    ),
                )
            else:
                # Unknown/forward-compat value — default-deny with the raw value named.
                violations.append(f"{name}: {discipline} (unrecognised discipline — default-deny)")

    # Check researcher_dof_ledger for OOS-peek entries on this bundle.
    # OOS evidence_source means a facet was chosen by looking at frozen-eval returns —
    # a stricter violation than BACKTEST_SELECTION (synthesis §2.5 NN1-VIOLATION hierarchy).
    ledger_rows = database.get_dof_ledger_for_bundle(bundle_hash)
    for ledger_row in ledger_rows:
        if ledger_row.get("evidence_source") == "OOS":
            facet_name = ledger_row.get("facet_name", "unknown")
            violations.append(
                f"{facet_name}: OOS evidence_source (frozen-eval peek — stricter than BACKTEST_SELECTION)"  # noqa: E501  # un-wrappable long line
            )

    is_honest = len(violations) == 0
    return is_honest, violations


def run_autotuner(
    bot_state, current_date_str, account_uuids, is_forced=False, spec_bundle_id: "int | None" = None
):  # TYPE-001 (sprint-2-audit a6e4d9f8)
    """
    Runs walk-forward optimization using Bayesian Optimization (Optuna) per symphony.
    Implements a three-fold walk-forward split (60/20/20): train / validation / frozen-eval.
    The 60/20/20 ratio is an operator choice for Planet Stopper's 250-day data scale; AFML Ch. 7.4
    prescribes the held-out frozen-eval invariant (purge+embargo), not the specific ratio.

    Walk-forward split methodology (López de Prado 2018 Ch. 7.4):
    - 250-day history is split 60/20/20: ~150 train days, ~50 validation days, ~50 frozen-eval days.
    - Purge (PURGE_DAYS=20) and Embargo (EMBARGO_DAYS=1) applied at BOTH fold boundaries:
        (a) train | validation boundary
        (b) validation | frozen-eval boundary
      Binding purge constraint: max(vol=20, ATR=15)=20 trading days.
    - Selection: Optuna trials score on the validation fold only. Frozen-eval is hidden during
      the trial sweep.
    - Frozen-eval consumption: exactly once after best-trial selection, to produce the honest
      post-selection performance metric (frozen_eval_sharpe).

    OOS-fold-collapse v2 (PA-26 extended):
    - At 250-day history, the three raw folds are 150/50/50 days.
    - After PURGE_DAYS=20 at each boundary, the usable validation window shrinks to ~29 days
      (int(250*0.20) - 20 - 1 = 29). This is an acknowledged tradeoff — the purge is
      methodologically correct and the ~29-day usable window is the cost of honest OOS
      reporting. Future workstream: further expand history window or use purged k-fold CV
      (rolling folds) to recover additional statistical power.

    Operator visibility (OPTUNA-4 Path B, Option 1 — honesty framing):
    - `optimization_results[symphony]["eval_window_days"]` carries per-cycle day-counts for
      the validation and frozen-eval folds so the operator sees the thin-window cost on every
      autotune cycle without requiring a DB schema migration.
    - The ~29-day usable validation window at the 250-day operator-data-budget is an
      acknowledged statistical-power limitation — the cost of honest OOS reporting,
      not a defect. The BHY haircut addresses cross-trial multiple-testing; it operates
      on per-trial p-values whose validity depends on adequate sample length per trial
      and does NOT substitute for thin per-trial windows.
    - Future-workstream remediation paths: (a) further expand the operator-data-budget
      (a council Amendment, not an audit slide-in), or (b) adopt combinatorial
      purged k-fold cross-validation (López de Prado 2018 Ch. 7.4) to recover additional
      statistical power without expanding total history. The canonical joint (N, T) framework
      future workstreams should consult is the Deflated Sharpe Ratio (Bailey & López de Prado
      2014), which accounts for trial count AND sample length.
    """
    # NN1 spec-freeze hard gate (D5 wiring — council §2.5).
    # validate_search_space_nn1 must run BEFORE optuna.create_study so any
    # leaked frozen facet is caught at runtime, not silently toured by Optuna.
    validate_search_space_nn1()

    # Phase-1 strict: every run requires an explicit pinned spec bundle.
    # No implicit defaults — a missing bundle_id is a configuration error.
    if spec_bundle_id is None:
        raise ValueError(
            "run_autotuner requires an explicit spec_bundle_id (NN1 Phase-1 strict). "
            "Every autotuner run must be pinned to a registered spec bundle — "
            "no implicit bundle defaults are permitted."
        )

    # Bundle integrity: fetch the row and verify stored hash matches facets_json.
    # Hash integrity check is performed here (not inside get_spec_bundle_by_id)
    # so the DB accessor stays a pure reader (architecture constraint: ro_connection
    # for all pure-read paths). The check is load-bearing: a tampered bundle_hash
    # must prevent the autotuner from running (T14 contract).
    bundle_row = database.get_spec_bundle_by_id(spec_bundle_id)
    if bundle_row is None:
        raise ValueError(
            f"spec_bundle_id={spec_bundle_id} not found in spec_bundles. "
            "Register the bundle before running the autotuner."
        )
    # Integrity gate: recompute hash from facets_json and compare to stored bundle_hash.
    # Only performed when facets_json is present in the row (it may be absent on mocked
    # rows in tests that stub get_spec_bundle_by_id; live rows always include it via
    # _SPEC_BUNDLE_COLUMNS). A missing facets_json is a separate schema error — not a
    # tampering signal — so it is skipped here and caught downstream by get_spec_facets.
    _raw_facets_json = bundle_row.get("facets_json")
    if _raw_facets_json is not None:
        _canonical_json = database.canonicalize_facets_json(json.loads(_raw_facets_json))
        _computed_hash = database.hash_facets_json(_canonical_json)
        if bundle_row["bundle_hash"] != _computed_hash:
            raise ValueError(
                f"spec_bundle_id={spec_bundle_id} hash mismatch: "
                f"stored bundle_hash={bundle_row['bundle_hash']!r} does not match "
                f"computed hash={_computed_hash!r} from facets_json. "
                "The bundle may have been tampered with after frozen_at — integrity check failed."
            )
    stored_hash = bundle_row["bundle_hash"]

    # NN1 compliance: refuse to start if any load-bearing facet is BACKTEST_SELECTION.
    is_honest, violations = validate_nn1_compliance(spec_bundle_id)
    if not is_honest:
        raise RuntimeError(
            f"NN1 VIOLATION: spec_bundle_id={spec_bundle_id} contains "
            f"BACKTEST_SELECTION or other NN1-violating facets — "
            f"the BHY haircut would silently undercount N. Refusing to start. "
            f"Violations: {violations}"
        )

    # Sprint 3: Spec Critic — advisory structural integrity check on the spec bundle.
    # Runs immediately after NN1 compliance (which guards the hard gate); this is
    # the soft advisory layer for Phase-1 THEORY facet completeness, discipline
    # recognition, spec age, and phase-scope leak detection.
    _sc_facets_rows = database.advisor_ro_query(
        "SELECT facet_name, freeze_discipline, "
        "    (SELECT frozen_at FROM spec_bundles WHERE bundle_hash = sf.bundle_hash) AS frozen_at "
        "FROM spec_facets sf WHERE sf.bundle_hash = ?",
        (stored_hash,),
    )
    # Note: the Spec Critic is called once per bundle (before the per-symphony loop),
    # so normalized_name is not yet available here. symphony_id is set to None at this
    # site. If per-symphony SC observations with a populated symphony_id are needed,
    # the call should be moved inside the per-symphony loop.
    try:
        _sc.run_spec_critic(stored_hash, _sc_facets_rows, symphony_id=None)
    except Exception as e:
        # Surface loudly (ERROR + exc_info), do NOT abort the cycle: a producer
        # PERSISTENCE failure is an advisor OUTAGE, not advisory noise.  A bare
        # WARNING here once hid a total OC outage for 1678 runs.  Per-producer
        # isolation is preserved (no re-raise) so one producer cannot take down
        # the autotune cycle or its sibling producers.
        logging.error(
            "Spec Critic observation PERSISTENCE FAILED — advisor outage (cycle continues): %s",
            e,
            exc_info=True,
        )

    # Suppress Optuna's per-trial log noise; set here (not at module level) to
    # avoid clobbering pytest's output-capture on import.
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    # Extract gamma and objective_kind from spec_facets — sourced from the registered
    # spec bundle, NOT a module-level constant (T5 / gamma provenance contract).
    # gamma is frozen by THEORY; a source-code constant would fail the immutable +
    # content-hashed + frozen_at persistence contract (council synthesis §3.7).
    _facets = database.get_spec_facets_for_bundle(stored_hash)
    _facets_by_name = {f["facet_name"]: f["facet_value"] for f in _facets}
    # gamma: default 2.0 (prudential CRRA coefficient) if facet absent; THEORY-frozen.
    _gamma_str = _facets_by_name.get("gamma", "2.0")
    try:
        _gamma: float = float(_gamma_str)
    except (TypeError, ValueError):
        raise ValueError(
            f"spec_bundle_id={spec_bundle_id} has non-numeric gamma facet: {_gamma_str!r}"
        )
    # objective_kind: 'crra_eu' triggers the new CRRA-EU objective branch.
    # 'sortino_loss_aversion' (or absent) uses the legacy Sortino branch.
    # utility_family='CRRA' in the Phase-1 bundle implies objective_kind='crra_eu'.
    _utility_family = _facets_by_name.get("utility_family", "")
    _objective_kind = _facets_by_name.get("objective_kind", "")
    if not _objective_kind:
        _objective_kind = (
            "crra_eu" if _utility_family.upper() == "CRRA" else "sortino_loss_aversion"
        )

    # Apply optuna_001 archive migration once if any bare (non-prefixed) legacy
    # studies exist — renames them to LEGACY__<name> non-destructively.
    _apply_optuna_archive_migration_if_needed()

    print(
        "  -> Starting EOD Autotune (250-day WFA: 60% Train / 20% Validation / 20% Frozen-Eval per Symphony)..."  # noqa: E501  # un-wrappable long line
    )

    # 0. Calculate Historical Execution Deviation
    deviation_dict = calculate_historical_deviation(current_date_str)

    # 1. Archive today's charts to the permanent DB
    chart_history = database.load_chart_history()
    if chart_history and chart_history.get("date") == current_date_str:
        for sym_id, data in chart_history.get("symphonies", {}).items():
            database.save_chart_archive(current_date_str, sym_id, data)

    # 2. Fetch the rolling 250-trading-day synthetic forward-looking data.
    # A persistent history shortfall surfaces as HistoryShortfallError — the
    # autotuner is a secondary optimisation step, so catch it (narrowly, never
    # a bare except) and convert it to a graceful abort. The abort return
    # carries the shortfall reason so the EOD operator report can surface
    # "autotuner aborted — persistent history shortfall" rather than leaving
    # the operator unable to distinguish the abort from a no-change run.
    try:
        history_125d = synthetic_history.generate_synthetic_history(
            bot_state, current_date_str, n_jobs=_AUTOTUNE_REPLAY_N_JOBS
        )
    except synthetic_history.HistoryShortfallError as e:
        print(f"  -> Autotuner aborted: persistent history shortfall — {e}")
        return {"aborted": True, "reason": str(e)}
    if not history_125d:
        print("  -> Autotuner aborted: Failed to generate synthetic history.")
        return {"aborted": True, "reason": "Failed to generate synthetic history."}

    # Extract global dates and partition 60/20/20 (train / validation / frozen-eval).
    all_dates = set()
    for sym_data in history_125d.values():
        all_dates.update(sym_data.keys())
    sorted_dates = sorted(list(all_dates))

    total_days = len(sorted_dates)
    if total_days < 2:
        print("  -> Autotuner aborted: Need at least 2 days of history for WFA.")
        return {"aborted": True, "reason": "Need at least 2 days of history for WFA."}

    # Three-fold split: TRAIN_RATIO / VALIDATION_RATIO / FROZEN_EVAL_RATIO (60/20/20).
    # Reference: López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.4.
    val_start_idx = int(total_days * TRAIN_RATIO)
    frozen_start_idx = int(total_days * (TRAIN_RATIO + VALIDATION_RATIO))
    # split_idx aliases val_start_idx — preserved so O1 test assertions that inspect
    # autotuner.py source for "split_idx" continue to find the split site.
    split_idx = val_start_idx

    raw_train_dates = sorted_dates[:val_start_idx]
    raw_val_dates = sorted_dates[val_start_idx:frozen_start_idx]
    raw_frozen_dates = sorted_dates[frozen_start_idx:]

    # Boundary 1 — train | validation: purge + embargo on train side.
    # Exclude the last PURGE_DAYS + EMBARGO_DAYS of raw_train_dates (identical
    # logic to O1's train | test purge, now applied at the train | validation boundary).
    effective_train_cutoff = max(0, val_start_idx - PURGE_DAYS - EMBARGO_DAYS)
    train_dates = set(sorted_dates[:effective_train_cutoff])
    # AC-2 (MA-5): genuinely computed purge-integrity check, not a hardcoded
    # literal — verifies the CPCV-eligible window's tail (train_dates, which
    # AC-1's split-level scoring reads via _cpcv_eligible_dates =
    # sorted(train_dates)) sits >= PURGE_DAYS + EMBARGO_DAYS trading days
    # before val_start_idx (the validation window the OOS adoption cascade
    # reads). A real assertion, not an attestation: it can genuinely go False
    # on a short-history edge case where effective_train_cutoff's max(0, ...)
    # clamp bites, honestly surfacing an insufficient-gap condition instead of
    # silently claiming purge integrity that was never actually enforced.
    purge_integrity_ok = (val_start_idx - effective_train_cutoff) >= (PURGE_DAYS + EMBARGO_DAYS)

    # Boundary 2 — validation | frozen-eval: purge + embargo on validation side.
    # Exclude the last PURGE_DAYS + EMBARGO_DAYS of raw_val_dates so validation samples
    # whose feature lookback overlaps the frozen-eval fold are not seen by the objective.
    # PURGE_DAYS appears here to confirm the second boundary receives the same treatment.
    val_purge_end_idx = frozen_start_idx - PURGE_DAYS - EMBARGO_DAYS
    # Purge-reduced validation: used by the Optuna objective closure only, preventing
    # late validation features from leaking into the frozen-eval fold.
    validation_dates_purged = set(
        sorted_dates[val_start_idx : max(val_start_idx, val_purge_end_idx)]
    )
    # Full raw validation: used by the OOS cascade (AI/fallback/default) to preserve
    # the behavioural contract that the cascade evaluates on the raw OOS fold.
    validation_dates_full = set(raw_val_dates)

    frozen_dates = set(raw_frozen_dates)

    history_train = {}
    history_validation = {}  # purge-reduced; used by Optuna objective only
    history_validation_full = {}  # full raw validation fold; used by OOS cascade
    history_frozen = {}
    for sym_id, sym_data in history_125d.items():
        history_train[sym_id] = {d: t for d, t in sym_data.items() if d in train_dates}
        history_validation[sym_id] = {
            d: t for d, t in sym_data.items() if d in validation_dates_purged
        }
        history_validation_full[sym_id] = {
            d: t for d, t in sym_data.items() if d in validation_dates_full
        }
        history_frozen[sym_id] = {d: t for d, t in sym_data.items() if d in frozen_dates}

    # AC-2 (MA-5): the OOS adoption cascade (oos_alpha/fallback_oos_alpha/
    # default_oos_alpha below) reads history_validation_full DIRECTLY — no
    # intermediate history_test alias. CPCV/trial selection above is now
    # restricted to the TRAIN-only purge-trimmed window (train_dates), so
    # validation_dates_full is a genuine, never-seen holdout: the old
    # `history_test = history_validation_full` assignment implied the OOS
    # cascade's window WAS the validation window incidentally; it is now a
    # deliberate, correct choice made possible by CPCV no longer touching
    # validation dates at all.

    # Extract unique normalized symphony names from the current bot_state
    symphony_names = set()
    for sym_id, data in bot_state.items():
        if isinstance(data, dict) and "name" in data:
            symphony_names.add(database.normalize_name(data["name"]))

    optimization_results = {}

    # Single timestamp shared across all symphonies in this run — groups all
    # per-symphony rows from one invocation into a logical "run" for Claude context-assembly.
    run_timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    for normalized_name in symphony_names:
        print(f"     Optimizing Symphony: {normalized_name}")
        strat_data = database.get_symphony_strategy(normalized_name)
        locked_vars = strat_data.get("locked_vars", [])
        current_params = strat_data.get("params", {})
        original_params = current_params.copy()

        # CPCV Phase 2: pre-compute the C(N,k)=15 fold descriptors ONCE per symphony
        # (not inside the trial callback) so the fold structure is stable across all
        # trials. AC-1 (R2, team-lead RULED Option A / split-level scoring —
        # DECISIONS.md DE-MATH-R2-001): CPCV scores each of the 15 real splits
        # directly on its own test_dates; the old 5-path aggregation
        # (_aggregate_cpcv_paths) is retired — canonical CPCV backtest-path
        # reconstruction is a refit-world construct that always collapses to 5
        # identical full-window scores in a no-refit codebase (see the retirement
        # comment above _generate_cpcv_folds).
        #
        # AC-2 (MA-5): the CPCV-eligible window is TRAIN-ONLY — train_dates, the
        # SAME purge/embargo-trimmed cutoff already computed above for
        # history_train (effective_train_cutoff = val_start_idx - PURGE_DAYS -
        # EMBARGO_DAYS) — NOT train+validation combined as before. This makes
        # validation_dates_full (used below for the OOS adoption cascade) a
        # genuine, never-seen holdout: CPCV selection never touches it.
        _cpcv_eligible_dates = sorted(train_dates)
        _cpcv_folds = _generate_cpcv_folds(
            sorted_dates=_cpcv_eligible_dates,
            n_groups=_CPCV_N_GROUPS,
            k_test=_CPCV_K_TEST_GROUPS,
            purge_days=PURGE_DAYS,
            embargo_days=EMBARGO_DAYS,
        )
        # The per-symphony CPCV-eligible history — the SAME train-only,
        # purge-trimmed slice as history_train (built once above, unused
        # elsewhere). Every one of the 15 splits' calls below shares this ONE
        # dict (never pre-filtered further to a single split's own dates) so
        # _replay_resolve_regime_exit_ticks's trailing window sees the FULL
        # train-only chronology, never a gappy per-split subset (AC-1-adjacent
        # regime-lookback chronology purity, ADDENDUM 5/6) — the score_dates
        # kwarg on _collect_sim_returns_dated restricts which dates actually
        # undergo the expensive per-tick replay per split, decoupling "what
        # informs the regime label" from "what gets scored".
        _cpcv_history = history_train

        def objective(trial):
            p = current_params.copy()
            # Only call suggest_* for vars NOT in locked_vars. A locked var
            # already has its pinned value in p (from current_params.copy()
            # above) and must not be offered to Optuna for exploration.
            if "TAKE_PROFIT_MC_PCT" not in locked_vars:
                p["TAKE_PROFIT_MC_PCT"] = trial.suggest_float(
                    "TAKE_PROFIT_MC_PCT", _SS_TAKE_PROFIT_MC_MIN, _SS_TAKE_PROFIT_MC_MAX
                )
            if "VWAP_CROSS_HWM_PCT" not in locked_vars:
                p["VWAP_CROSS_HWM_PCT"] = trial.suggest_float(
                    "VWAP_CROSS_HWM_PCT", _SS_VWAP_CROSS_HWM_MIN, _SS_VWAP_CROSS_HWM_MAX
                )
            if "VWAP_BLEED_MULTIPLIER" not in locked_vars:
                p["VWAP_BLEED_MULTIPLIER"] = trial.suggest_float(
                    "VWAP_BLEED_MULTIPLIER", _SS_VWAP_BLEED_MULT_MIN, _SS_VWAP_BLEED_MULT_MAX
                )
            if "VWAP_BLEED_TICKS" not in locked_vars:
                p["VWAP_BLEED_TICKS"] = trial.suggest_int(
                    "VWAP_BLEED_TICKS", _SS_VWAP_BLEED_TICKS_MIN, _SS_VWAP_BLEED_TICKS_MAX
                )
            if "PARABOLIC_VELOCITY_THRESHOLD" not in locked_vars:
                p["PARABOLIC_VELOCITY_THRESHOLD"] = trial.suggest_float(
                    "PARABOLIC_VELOCITY_THRESHOLD", _SS_PARA_VEL_MIN, _SS_PARA_VEL_MAX
                )
            if "MAX_PARABOLIC_SQUEEZE" not in locked_vars:
                p["MAX_PARABOLIC_SQUEEZE"] = trial.suggest_float(
                    "MAX_PARABOLIC_SQUEEZE", _SS_MAX_PARA_SQUEEZE_MIN, _SS_MAX_PARA_SQUEEZE_MAX
                )

            acc_sym_ids = [
                k
                for k, v in bot_state.items()
                if isinstance(v, dict)
                and database.normalize_name(v.get("name", "")) == normalized_name
            ]
            if not acc_sym_ids:
                return 0.0
            target_sym_id = acc_sym_ids[0]

            # CPCV: score this trial on the mean across the _CPCV_N_SPLITS=15 real
            # splits (AC-1, R2: split-level scoring — team-lead RULED Option A).
            # Each split is scored ONCE per trial (NOT 15 separate Optuna
            # evaluations — n_optuna / compute_n_effective / BHY are UNTOUCHED;
            # CPCV changes WHAT data each trial scores on, not HOW MANY tests
            # exist). _cpcv_history is the SAME train-only per-symphony dict for
            # every split's call (never pre-filtered to one split's own dates —
            # see _cpcv_history's construction above); score_dates restricts
            # which dates actually contribute to THIS split's returns, so the
            # 15 splits produce genuinely distinct scores (unlike the old
            # 5-path aggregation, which always collapsed to 5 identical
            # full-window scores — MA-2).
            #   - daily_date_returns: RAW PERCENT (daily_returns's T5 provenance contract;
            #     _haircut_select divides by RETURN_PCT_TO_FRACTION before the U-transform).
            #   - cscv_date_returns: DECIMAL (compute_pbo's contract; divided here — C1).
            # Both are built as dicts (date -> guard_alpha) via union/update — a date
            # can appear in multiple splits (each group is a test group in
            # _CPCV_K_TEST_GROUPS-1 x (_CPCV_N_GROUPS-1 choose _CPCV_K_TEST_GROUPS-1)
            # splits), and every appearance yields the SAME guard_alpha for that date
            # (state-independent per-day sim, no per-fold refit) — the date-keyed
            # dicts collapse the duplication to exactly one entry per distinct
            # triggered date, so T == the true distinct-date count, never inflated
            # by split multiplicity.
            split_scores: list[float] = []
            daily_date_returns: dict[str, float] = {}
            cscv_date_returns: dict[str, float] = {}
            for _fold in _cpcv_folds:
                _split_dates = set(_fold["test_dates"])
                # _collect_sim_returns_dated (not a separate _collect_sim_returns
                # call) is the single source of truth for this split's returns —
                # both the flat score input and the date-labeled union are
                # derived from the SAME replay pass, avoiding a redundant second
                # full replay over the split's dates.
                _dated_returns = _collect_sim_returns_dated(
                    p,
                    _cpcv_history,
                    [target_sym_id],
                    current_date_str,
                    deviation_dict,
                    score_dates=_split_dates,
                )
                _split_returns_pct = [_ga for _d, _ga in _dated_returns]
                split_scores.append(
                    math_engine.compute_crra_eu_objective(
                        [r / RETURN_PCT_TO_FRACTION for r in _split_returns_pct], _gamma
                    )
                    if _objective_kind == "crra_eu"
                    else compute_sortino_ratio(_split_returns_pct)
                )
                for _date, _ga in _dated_returns:
                    # daily_returns is RAW PERCENT (T5 provenance) — no divide here.
                    daily_date_returns[_date] = _ga
                    # C1 fix: divide by RETURN_PCT_TO_FRACTION so the stored value is
                    # DECIMAL, matching compute_pbo -> compute_crra_eu_objective's
                    # contract (W = max(WEALTH_ARG_FLOOR, 1 + r)). Mirrors the inline
                    # split-score divide above. Storing raw percent floored W on any
                    # sub--1% day (U ~= -999), corrupting the PBO IS-best/OOS rank.
                    cscv_date_returns[_date] = _ga / RETURN_PCT_TO_FRACTION

            # Persist the per-date-aggregated return series (one entry per distinct
            # triggered date — NOT _CPCV_N_SPLITS copies) so _haircut_select can source
            # T = len(daily_returns) == the true distinct-date count and re-transform
            # through the active gamma. Raw percent (T5 provenance contract — not U values).
            trial.set_user_attr("daily_returns", list(daily_date_returns.values()))
            # Persist the date-labeled union for the Phase-3 CSCV PBO gate.
            # Keys are date strings within the CPCV-eligible window (sorted(train_dates)).
            # Values are DECIMAL guard_alpha returns (raw percent / RETURN_PCT_TO_FRACTION,
            # applied at the store site above — C1 fix). This matches compute_pbo's
            # decimal contract; it does NOT share daily_returns's raw-percent contract.
            trial.set_user_attr("cscv_date_returns", cscv_date_returns)
            # Persist the per-split CPCV scores so filter_sortino_sentinels can detect a
            # sentinel split at the SPLIT-SCORE level. The trial value is the MEAN across
            # splits; a single sentinel split (1e6) can be masked in the mean when the
            # other splits are net-negative, so the aggregate value alone is insufficient
            # to flag a degenerate zero-downside split (Sortino branch only — CRRA-EU
            # never emits the sentinel).
            trial.set_user_attr("split_scores", split_scores)

            # Trial score: mean across the _CPCV_N_SPLITS split scores.
            # An empty split-score list contributes 0.0 (same fallback as the
            # pre-CPCV single-fold path).
            if not split_scores:
                return 0.0
            return sum(split_scores) / len(split_scores)

        start_time = time.time()

        # Parallel Bayesian Optimization
        db_url = "sqlite:///optuna_studies.db"
        storage = optuna.storages.RDBStorage(
            url=db_url, engine_kwargs={"connect_args": {"timeout": 60}}
        )
        study_timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        # Source sampler seed + parallelism from env (OPTUNA-1 / OPTUNA-6 audit fix).
        # Named constants _OPTUNA_SAMPLER_SEED_ENV / _OPTUNA_N_JOBS_ENV carry the
        # canonical string values; the literals appear here so the env dependency
        # is auditable at the call site without tracing the constant definitions.
        _seed_raw = os.environ.get("OPTUNA_SAMPLER_SEED")
        _sampler_seed = int(_seed_raw) if _seed_raw is not None and _seed_raw.strip() else None
        # n_jobs sourced from OPTUNA_N_JOBS env (OPTUNA-6); default 1 for SQLite
        # RDBStorage writer-lock safety — see _resolve_optuna_n_jobs_from_env docstring.
        _n_jobs_raw = os.environ.get("OPTUNA_N_JOBS")
        _n_jobs = _resolve_optuna_n_jobs_from_env()
        # Pruner: NopPruner (OPTUNA-2 pin) — the objective is end-of-trial-scored
        # (a single scalar after the full guard-alpha sim; the simulation runs
        # to completion with no intermediate step reporting to Optuna). Any
        # pruner is silently inactive today. Explicit NopPruner documents the
        # intent: if a future PR adds intermediate step reporting it must
        # consciously choose a pruner family — because activating MedianPruner
        # would censor the BHY (Harvey & Liu) haircut's trial set (c(N)
        # Yekutieli factor calibrated over the COMPLETE set) and break the
        # N_effective additive accounting (sums across ALL completed trials).
        # A pruner-family change is a methodology change — surface to PM first.
        study = optuna.create_study(
            study_name=f"{study_timestamp}__{normalized_name}",
            storage=storage,
            load_if_exists=False,
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=_sampler_seed),
            pruner=optuna.pruners.NopPruner(),
        )
        study.optimize(objective, n_trials=OPTUNA_N_TRIALS_PRODUCTION, n_jobs=_n_jobs)

        naive_sharpe_value = study.best_value
        best_alpha_train = naive_sharpe_value
        best_params = study.best_params

        # --- HARVEY & LIU SELECTION HAIRCUT (AI branch only) ---
        # The best-of-N Optuna Sortino is upward-biased by selection across N
        # trials. The Harvey & Liu 2015 BHY haircut corrects this: each completed
        # trial gets a t-statistic Sortino·sqrt(T), a one-sided p-value, and a
        # Benjamini-Hochberg-adjusted p-value over the whole trial set; the
        # BHY-winning trial (argmin p_adj) is deployed only if it clears the FDR
        # gate. If no trial clears the gate the trial set is statistically
        # indistinguishable from noise and the AI proposal is rejected — the
        # cascade then falls through to fallback/default (overfitting protection).
        # The stored selection_tstat value is the winner's t-statistic — a
        # higher-is-better significance scalar that keeps the persistence/Discord
        # surface's orientation honest; the adjusted p-value is the internal
        # selection key, surfaced only in logs.
        selection_tstat_value: float | None = None
        haircut_rejected_proposal = False
        # ARCH-001: EUT audit values — initialized here so they are always defined
        # for save_autotune_run even when the haircut_trials block is skipped.
        n_eff: int = 0
        d_spec: int = 0
        try:
            completed_trials = [t for t in study.trials if t.value is not None]
        except TypeError:
            completed_trials = []

        # filter_sortino_sentinels excludes math_engine._SORTINO_SENTINEL (1e6)
        # zero-downside trials AND partial-sentinel CPCV means (one sentinel path,
        # value ~= _SORTINO_SENTINEL/_CPCV_N_PATHS) — a sentinel's t-statistic would
        # dominate the haircut and let a degenerate trial masquerade as a genuine signal.
        haircut_trials = filter_sortino_sentinels(completed_trials)

        if haircut_trials:
            # Route tstat_fn based on objective_kind (sourced from spec_facets above).
            # CRRA-EU branch: compute_crra_eu_tstat(U_series) = mean(U)/(sd(U)/sqrt(T)).
            # Sortino branch: compute_sortino_tstat(sortino, T) = sortino*sqrt(T) (default).
            # Using compute_sortino_tstat for a mean-valued objective is the H-6 category
            # error (autotuner.py:266-271 precedent); the discriminator enforces the split.
            if _objective_kind == "crra_eu":
                _tstat_fn = compute_crra_eu_tstat
            else:
                _tstat_fn = compute_sortino_tstat
            # NEFF-001 + ARCH-001 fix: wire compute_n_effective before _haircut_select
            # and capture n_eff + d_spec for the EUT audit trail (save_autotune_run).
            # In the NN1-honest case S=0 → n_eff == len(haircut_trials) → byte-identical
            # to the pre-wiring behavior (plan D2 backward-compatibility contract).
            _ledger_rows = database.get_researcher_dof_ledger_for_run(
                run_timestamp,
                winning_spec_bundle_id=stored_hash,
            )
            n_eff = compute_n_effective(
                n_optuna=len(haircut_trials),
                ledger_query=lambda: _ledger_rows,
            )
            # d_spec: COUNT DISTINCT spec_bundle_ids from BACKTEST_SELECTION rows
            # (council §5; differs from S which is SUM(n_configs_searched)).
            d_spec = len(
                {
                    row.get("spec_bundle_id")
                    for row in _ledger_rows
                    if row.get("spec_bundle_id") is not None
                }
            )
            winner_trial, winner_p_adj, winner_tstat = _haircut_select(
                haircut_trials, n_effective=n_eff, tstat_fn=_tstat_fn, gamma=_gamma
            )
            if winner_trial is not None:
                best_params = winner_trial.params
                best_alpha_train = winner_trial.value
                selection_tstat_value = winner_tstat
            else:
                # No trial cleared the FDR gate — reject the AI proposal.
                haircut_rejected_proposal = True
                print(
                    f"       Harvey & Liu haircut: no trial cleared the q="
                    f"{HARVEY_LIU_FDR_Q} FDR gate for '{normalized_name}' "
                    f"(best adjusted p-value {winner_p_adj}). Rejecting AI "
                    f"proposal; cascading to Fallback/Default."
                )
        # ----------------------------------------------------

        # --- PHASE-3 PBO GATE: compute_pbo on top-K PRE-BHY configs ---
        # PBO (Probability of Backtest Overfitting) is computed on the top-_CSCV_TOP_K
        # trials by RAW Optuna value (pre-BHY). This measures SELECTION-PROCESS
        # overfitting — does the IS-best config from the optimization generalize OOS
        # across all CSCV date-partitions? It is the sample-robustness axis, ORTHOGONAL
        # to the BHY multiplicity axis (n_effective / _haircut_select unchanged).
        # Reference: Bailey & López de Prado 2014, DOI 10.21314/JCF.2014.005.
        _pbo_value: float | None = None
        if haircut_trials:
            # Sort by raw Optuna value descending, take top _CSCV_TOP_K pre-BHY.
            _top_k_trials = sorted(
                haircut_trials,
                key=lambda t: t.value if t.value is not None else float("-inf"),
                reverse=True,
            )[: math_engine._CSCV_TOP_K]
            # Build list[dict[str, float]]: one cscv_date_returns dict per config.
            # Trials without cscv_date_returns (e.g. very old study rows) are skipped.
            _top_k_configs: list[dict[str, float]] = [
                t.user_attrs["cscv_date_returns"]
                for t in _top_k_trials
                if "cscv_date_returns" in t.user_attrs
            ]
            if len(_top_k_configs) >= 2:
                _pbo_value = math_engine.compute_pbo(
                    _top_k_configs,
                    _cpcv_eligible_dates,
                    _gamma,
                )
        # -------------------------------------------------------

        # --- BEST_PARAMS SCHEMA VALIDATION (B2-FU2) ---
        # An empty best_params or one missing any required search-space key indicates
        # a degenerate/aborted study. Reject the WHOLE AI proposal (no key-by-key
        # merge -- partial merges produce Frankenstein params where some keys
        # come from the AI and others from current/fallback). Force the cascade
        # to fall through to fallback (or default if fallback also fails) by
        # poisoning oos_alpha to -inf. Do NOT raise -- the daemon must keep ticking.
        #
        # Locked-vars injection: vars in both OPTUNA_SEARCH_SPACE_KEYS and
        # locked_vars are excluded from suggest_* above, so they are absent
        # from best_params (winner_trial.params). Inject them from current_params
        # before the issubset check so (a) schema validation passes for locked
        # keys and (b) OOS evaluation of the AI proposal uses the pinned value.
        # This does NOT weaken the schema check — genuinely missing unlocked
        # keys still trigger the invalid path.
        # Copy first: best_params IS winner_trial.params on the haircut path
        # (autotuner.py:2285). Mutating it in-place could corrupt Optuna's
        # internal trial state under RDB storage. A shallow copy is sufficient
        # (values are scalars). n_effective/DOF: locking reduces search
        # dimensionality but NOT n_optuna (trial count); BHY counts independent
        # tests, not search-space dims — orthogonal, no change needed.
        best_params = dict(best_params)
        _locked_search_space = OPTUNA_SEARCH_SPACE_KEYS & set(locked_vars)
        for _lk in _locked_search_space:
            if _lk not in best_params and _lk in current_params:
                best_params[_lk] = current_params[_lk]
        ai_proposal_invalid = (
            haircut_rejected_proposal
            or not best_params
            or not OPTUNA_SEARCH_SPACE_KEYS.issubset(best_params.keys())
        )
        if ai_proposal_invalid and not haircut_rejected_proposal:
            missing = sorted(OPTUNA_SEARCH_SPACE_KEYS - set(best_params.keys()))
            print(
                f"       Warning: best_params schema invalid for '{normalized_name}' "
                f"(missing keys: {missing or '<empty dict>'}). "
                f"Rejecting AI proposal; cascading to Fallback/Default."
            )
        if ai_proposal_invalid:
            selection_tstat_value = None
            naive_sharpe_value = None
        # ---------------------------------------------

        # Evaluate OOS robustness
        best_p = current_params.copy()
        for name, val in best_params.items():
            best_p[name] = round(val, 2)

        acc_sym_ids = [
            k
            for k, v in bot_state.items()
            if isinstance(v, dict) and database.normalize_name(v.get("name", "")) == normalized_name
        ]
        target_sym_id = acc_sym_ids[0] if acc_sym_ids else None
        oos_alpha = -run_simulation(
            best_p,
            history_validation_full,
            [target_sym_id] if target_sym_id else [],
            current_date_str,
            deviation_dict,
        )

        # If the AI proposal is schema-invalid, poison its OOS alpha so the
        # cascade below naturally selects fallback (or default). Done AFTER
        # the simulation runs so any side-effects/logging are preserved.
        if ai_proposal_invalid:
            oos_alpha = -math.inf

        optimization_results[normalized_name] = {}

        # OPTUNA-4 Path B operator-visibility emission. Derived from the live
        # fold-construction variables (raw_val_dates, raw_frozen_dates, PURGE_DAYS,
        # EMBARGO_DAYS) so any future drift in the inputs surfaces here automatically.
        _raw_val_days = len(raw_val_dates)
        _raw_frozen_days = len(raw_frozen_dates)
        _usable_val_days = max(0, _raw_val_days - PURGE_DAYS - EMBARGO_DAYS)
        optimization_results[normalized_name]["eval_window_days"] = {
            "raw_validation_days": _raw_val_days,
            "usable_validation_days": _usable_val_days,
            "raw_frozen_eval_days": _raw_frozen_days,
            "purge_days": PURGE_DAYS,
            "embargo_days": EMBARGO_DAYS,
        }

        # Evaluate fallback parameters in OOS for comparison
        fallback_params = current_params.copy()
        fallback_oos_alpha = -run_simulation(
            fallback_params,
            history_validation_full,
            [target_sym_id] if target_sym_id else [],
            current_date_str,
            deviation_dict,
        )

        # Evaluate global default parameters in OOS for comparison
        default_params = database.DEFAULT_STRATEGY.copy()
        default_oos_alpha = -run_simulation(
            default_params,
            history_validation_full,
            [target_sym_id] if target_sym_id else [],
            current_date_str,
            deviation_dict,
        )

        # Validation-fold metric (selection truth — what Optuna actually optimized against).
        # For CRRA-EU bundles, the Sortino ratio is not the selection metric — compute_crra_eu_objective  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
        # was used. Sortino is suppressed (None) for CRRA-EU to avoid misleading reporting.
        validation_returns = _collect_sim_returns(
            best_p,
            history_validation,
            [target_sym_id] if target_sym_id else [],
            current_date_str,
            deviation_dict,
        )
        if _objective_kind == "crra_eu":
            validation_sharpe_value = None  # Sortino not applicable to CRRA-EU objective
        else:
            validation_sharpe_value = (
                compute_sortino_ratio(validation_returns) if validation_returns else None
            )

        # Frozen-eval: consumed exactly once post-selection on the held-out final 20% fold.
        # This is the honest performance metric — not seen by any Optuna trial callback.
        # PURGE_DAYS referenced here confirms the boundary purge applies at validation|frozen-eval.
        # Single read via _collect_sim_returns; no separate run_simulation call so the
        # "consumed once" invariant holds across all frozen-fold access paths.
        frozen_eval_returns = _collect_sim_returns(
            best_p,
            history_frozen,
            [target_sym_id] if target_sym_id else [],
            current_date_str,
            deviation_dict,
        )
        if _objective_kind == "crra_eu":
            # AC-3 (MA-9): the real CRRA-EU analog of the Sortino branch below —
            # mirrors run_simulation_crra_eu's own pct->fraction conversion
            # (RETURN_PCT_TO_FRACTION) + compute_crra_eu_objective call, applied
            # to the frozen fold instead of a training/validation fold. Sortino
            # is genuinely inapplicable to a CRRA-EU objective (different
            # utility metric), but "inapplicable" no longer means "None" — the
            # frozen fold's 20% data budget must buy a real measurement under
            # whichever objective actually governs this symphony.
            frozen_eval_sharpe_value = (
                math_engine.compute_crra_eu_objective(
                    [r / RETURN_PCT_TO_FRACTION for r in frozen_eval_returns], _gamma
                )
                if frozen_eval_returns
                else None
            )
        else:
            frozen_eval_sharpe_value = (
                compute_sortino_ratio(frozen_eval_returns) if frozen_eval_returns else None
            )

        # Calculate daily averages for better understanding
        train_days_count = len(train_dates)
        test_days_count = len(validation_dates_full)

        avg_train_alpha = best_alpha_train / train_days_count if train_days_count > 0 else 0
        avg_oos_alpha = oos_alpha / test_days_count if test_days_count > 0 else 0

        baseline_decision = ""

        # --- PHASE-3 ACCEPTANCE GATE (evaluate_acceptance_gate) ---
        # Democratized offline gate: BHY haircut (winner_trial_is_none) + NN1
        # spec-freeze + purge integrity vetoes → survivor panel → PBO veto.
        # PBO veto (pbo > PBO_REJECT_THRESHOLD) is a STAGE-1 hard veto orthogonal
        # to the BHY/n_effective multiplicity axis. pbo=None means not computed
        # (fewer than 2 configs with cscv_date_returns) — no PBO veto fires.
        # Stability and prior-anchor scores are computed as placeholder 1.0/1.0
        # until the full advisor wiring is in place; the gate's load-bearing
        # invariants (vetoes-dominant, one-directional brake) hold regardless.
        #
        # FORWARD-LOOKING FRICTION-MISMATCH RISK (incumbent_stability_score /
        # incumbent_prior_anchor_score specifically): if a future cycle wires
        # real DB-sourced values into these two INCUMBENT fields, confirm
        # they were computed on a friction-consistent basis with the
        # candidate score. A historical incumbent score computed by whatever
        # replay code existed when that history was recorded (possibly
        # gross-of-cost, pre-dating SIM_EXIT_FRICTION_PCT — see autotuner.py)
        # compared against a candidate score from TODAY's friction-aware
        # replay is a mismatched comparison — nothing currently catches that
        # silently. See tests/autotuner/test_incumbent_score_friction_tripwire.py.
        _gate_verdict = _acceptance_gate.evaluate_acceptance_gate(
            winner_trial_is_none=(winner_trial is None if haircut_trials else True),
            winner_p_adj=(winner_p_adj if haircut_trials else None),
            nn1_compliant=(d_spec == 0),
            # AC-2 (MA-5): genuinely computed above (val_start_idx -
            # effective_train_cutoff >= PURGE_DAYS + EMBARGO_DAYS) — no
            # hardcoded literal.
            purge_integrity_ok=purge_integrity_ok,
            oos_alpha=oos_alpha,
            fallback_oos_alpha=fallback_oos_alpha,
            default_oos_alpha=default_oos_alpha,
            candidate_stability_score=1.0,
            candidate_prior_anchor_score=1.0,
            incumbent_stability_score=1.0,
            incumbent_prior_anchor_score=1.0,
            pbo=_pbo_value,
        )
        # PBO veto: only fire if the gate rejects AND the rejection is specifically
        # caused by PBO exceeding the threshold (not BHY, which is already handled
        # by the haircut block above, and not NN1/purge which have no current wiring).
        # Guard: pbo_value is not None (veto only fires when PBO was actually computed)
        # AND pbo > PBO_REJECT_THRESHOLD AND the BHY haircut DID produce a winner
        # (haircut_rejected_proposal is False means BHY passed — the gate must have
        # rejected on PBO specifically).
        _pbo_veto_fired = (
            _gate_verdict.decision == _acceptance_gate.DECISION_REJECT_VETO_FAILED
            and not haircut_rejected_proposal
            and _pbo_value is not None
            and _pbo_value > math_engine.PBO_REJECT_THRESHOLD
        )
        if _pbo_veto_fired:
            haircut_rejected_proposal = True
            selection_tstat_value = None
            naive_sharpe_value = None
            ai_proposal_invalid = True
            oos_alpha = -math.inf
            print(
                f"       Acceptance gate VETOED AI proposal for '{normalized_name}' "
                f"(PBO={_pbo_value:.3f} > {math_engine.PBO_REJECT_THRESHOLD}). "
                f"Cascading to Fallback/Default."
            )
        # ---------------------------------------------------

        # B2-FU1: Asymmetric tie rule -- STRICT-POSITIVE on the AI branch
        # (over-fit risk: an AI proposal that only TIES the validated fallback
        # is not worth displacing the last-known-good params for), LENIENT
        # (>=) on the fallback branch below (favors last-known-good on tie
        # vs the global default).
        if oos_alpha > fallback_oos_alpha and oos_alpha > default_oos_alpha:
            if oos_alpha > 0:
                print(
                    f"       OOS validation passed! OOS Guard Alpha: +{oos_alpha:.2f}% (Average: {avg_oos_alpha:.2f}%)"  # noqa: E501  # un-wrappable long line
                )
            else:
                print(
                    f"       OOS validation passed (Beat Baselines)! OOS Guard Alpha: {oos_alpha:.2f}% (Avg: {avg_oos_alpha:.2f}%) vs Fallback: {fallback_oos_alpha:.2f}% / Default: {default_oos_alpha:.2f}%"  # noqa: E501  # un-wrappable long line
                )
            for name, val in best_params.items():
                if name not in locked_vars:
                    current_params[name] = round(val, 2)
            baseline_decision = "Adopted AI"
        elif fallback_oos_alpha >= default_oos_alpha:
            print(
                f"       OOS validation failed (AI: {oos_alpha:.2f}%). Reverting to Fallback parameters (Fallback: {fallback_oos_alpha:.2f}% vs Default: {default_oos_alpha:.2f}%)."  # noqa: E501  # un-wrappable long line
            )
            for k, v in fallback_params.items():
                if k not in locked_vars:
                    current_params[k] = v
            baseline_decision = "Reverted to Fallback"
        else:
            print(
                f"       OOS validation & Fallback failed. Resetting to Global Default (Default: {default_oos_alpha:.2f}% vs AI: {oos_alpha:.2f}%, Fallback: {fallback_oos_alpha:.2f}%)."  # noqa: E501  # un-wrappable long line
            )
            for k, v in default_params.items():
                if k not in locked_vars:
                    current_params[k] = v
            baseline_decision = "Reset to Global Default"

        # AC-3 / N-1: the frozen-eval Sortino was computed against the AI's
        # best_p above. If the cascade demoted the AI proposal (Reverted to
        # Fallback / Reset to Global Default) OR the proposal was rejected
        # wholesale (haircut / schema-invalid), the DEPLOYED params are NOT
        # the AI's — the operator-facing column must not carry a rejected
        # proposal's frozen-eval metric as if it were the deployed set's.
        # Null it here, symmetric with the selection_tstat + naive_sharpe
        # reset above. The accepted "Adopted AI" branch preserves the value.
        if baseline_decision != "Adopted AI":
            frozen_eval_sharpe_value = None

        # Build Discord logs ensuring all original variables are shown
        optimization_results[normalized_name]["_baseline_chosen"] = baseline_decision
        for k, original_val in original_params.items():
            optimization_results[normalized_name][k] = {
                "old": original_val,
                "new": current_params.get(k, original_val),
            }

        elapsed = time.time() - start_time
        haircut_log = (
            f" | Haircut t-stat: {selection_tstat_value:.4f} (naive Sortino: {naive_sharpe_value:.4f})"  # noqa: E501  # un-wrappable long line
            if selection_tstat_value is not None and naive_sharpe_value is not None
            else " | Haircut: N/A"
        )
        print(
            f"       Optimization completed in {elapsed:.2f}s. Train Sortino: {best_alpha_train:+.4f} (train days: {train_days_count}){haircut_log}"  # noqa: E501  # log message string
        )

        database.save_symphony_strategy(normalized_name, current_params, locked_vars)

        # P1: Persist per-run validation metrics so Claude context-assembly can
        # retrieve them via get_latest_autotune_run().  Called AFTER baseline_decision
        # is finalized and save_symphony_strategy has written the chosen params,
        # so the row captures the decision that was actually applied.
        # selection_tstat carries the Harvey & Liu haircut winner's t-statistic (a
        # higher-is-better significance scalar); naive_sharpe is the raw Optuna best.
        # O6: validation_sharpe (selection metric) and frozen_eval_sharpe (honest post-selection
        # metric, consumed once from the withheld final 20% fold).
        # ARCH-001: assemble the overfitting_verdict string from EUT audit values.
        # Format mirrors plan D5: "NN1_HONEST n_optuna=N d_spec=D n_effective=E"
        # or "NN1_VIOLATION_TRIPWIRE ..." when d_spec > 0.
        _n_optuna_for_verdict = len(haircut_trials) if haircut_trials else 0
        _verdict_prefix = "NN1_HONEST" if d_spec == 0 else "NN1_VIOLATION_TRIPWIRE"
        _overfitting_verdict = (
            f"{_verdict_prefix} n_optuna={_n_optuna_for_verdict} "
            f"D_spec={d_spec} n_effective={n_eff}"
        )

        # AC-E2 (feature-plans/advisor-weekly-suggestions.md, Workstream E): hoisted
        # ABOVE save_autotune_run so the accumulated S is persisted into THIS run's
        # autotune_runs.s_count row.  Previously this query ran only AFTER the save
        # (feeding solely the in-memory _oc_run dict below), so autotune_runs.s_count
        # stayed NULL on every row forever — a later run's prior_runs query always
        # saw None, so overfitting_conscience Indicator-3 (operator drift) could
        # structurally never fire on live data.  Control-flow reorder only: the
        # current-run I-1/I-2 S computation and verdict logic in
        # overfitting_conscience.py are unchanged (it re-derives its own S from
        # these same ledger rows).
        # Reads ledger rows via advisor_ro_query (wall integrity contract).
        _oc_ledger_rows = database.advisor_ro_query(
            "SELECT evidence_source, n_configs_searched, touched_frozen_eval, "
            "spec_bundle_id, facet_name FROM researcher_dof_ledger "
            "WHERE spec_bundle_id = ?",
            (stored_hash,),
        )
        # S = SUM(n_configs_searched) over BACKTEST_SELECTION rows for this run's
        # bundle — same filter overfitting_conscience.compute_overfitting_conscience_
        # observation applies to its own matching_rows (I-1/I-2 S accounting).
        # n_configs_searched is NOT NULL DEFAULT 1 (migration 018) so no None guard
        # is needed; int() cast mirrors compute_n_effective's defensive style.
        _s_count_for_persistence = sum(
            int(_row["n_configs_searched"])
            for _row in _oc_ledger_rows
            if _row["evidence_source"] == "BACKTEST_SELECTION"
        )

        # S3-AUDIT-001: capture the inserted row id directly from save_autotune_run
        # (which now returns cursor.lastrowid) — eliminates the read-after-write
        # get_latest_autotune_run dance that raced and always fell back to id=0.
        _inserted_id = database.save_autotune_run(
            run_timestamp=run_timestamp,
            symphony_id=normalized_name,
            oos_alpha=oos_alpha,
            train_alpha=best_alpha_train,
            baseline_decision=baseline_decision,
            fallback_oos_alpha=fallback_oos_alpha,
            default_oos_alpha=default_oos_alpha,
            selection_tstat=selection_tstat_value,
            naive_sharpe=naive_sharpe_value,
            validation_sharpe=validation_sharpe_value,
            frozen_eval_sharpe=frozen_eval_sharpe_value,
            # ARCH-001: EUT audit columns from migration 020.
            spec_bundle_id=stored_hash,
            n_effective=n_eff,
            d_spec=d_spec,
            gamma=_gamma,
            overfitting_verdict=_overfitting_verdict,
            # AC-E2: hoisted DoF-ledger S sum (see above) — feeds Indicator-3 drift
            # detection on later runs via the prior_runs query below.
            s_count=_s_count_for_persistence,
        )

        # Sprint 3: Overfitting Conscience — post-save observation.
        _oc_run = {
            "id": _inserted_id,
            "symphony_id": normalized_name,
            "run_timestamp": run_timestamp,
            "spec_bundle_id": stored_hash,
            "n_effective": n_eff,
            "s_count": d_spec,
        }
        # S3-AUDIT-003: supply prior_runs (same symphony, excluding just-inserted row,
        # ASC by run_timestamp) so OC Indicator-3 drift detection can fire.
        _oc_prior_raw = database.advisor_ro_query(
            "SELECT id, symphony_id, s_count FROM autotune_runs "
            "WHERE symphony_id = ? AND id != ? ORDER BY run_timestamp ASC",
            (normalized_name, _inserted_id),
        )
        # Normalise sqlite3.Row → dict for the producer.
        _oc_prior_dicts = [dict(r) for r in _oc_prior_raw]
        try:
            _oc.run_overfitting_conscience(_oc_run, _oc_ledger_rows, prior_runs=_oc_prior_dicts)
        except Exception as e:
            # Surface loudly (ERROR + exc_info), do NOT abort the cycle: a producer
            # PERSISTENCE failure is an advisor OUTAGE, not advisory noise.  This
            # exact swallow (WARNING) hid a TypeError on every live row, so OC
            # persisted ZERO observations across 1678 runs and nobody noticed.
            # Per-producer isolation preserved (no re-raise, S3-AUDIT-006).
            logging.error(
                "Overfitting Conscience observation PERSISTENCE FAILED — advisor "
                "outage (cycle continues): %s",
                e,
                exc_info=True,
            )
        # S3-AUDIT-002: Divergence Explainer call site, post-OC mirror.
        # Consistent placement with OC; avoids the 1-minute alpha_bot_execution path
        # (architecture constraint #1 — no blocking I/O on the execution path).
        try:
            _de.run_divergence_explainer(_oc_run, cvar_row=None)
        except Exception as e:
            # Surface loudly (ERROR + exc_info), do NOT abort the cycle: a producer
            # PERSISTENCE failure is an advisor OUTAGE, not advisory noise.  Mirrors
            # the OC/SC sites so no producer outage can rot silently again.
            # Per-producer isolation preserved (no re-raise, S3-AUDIT-006).
            logging.error(
                "Divergence Explainer observation PERSISTENCE FAILED — advisor "
                "outage (cycle continues): %s",
                e,
                exc_info=True,
            )

    print("  -> Autotuner finished all symphonies.")
    return optimization_results


def run_calibration_sweep(
    history_data: dict,
    current_params: dict,
    current_date_str: str,
    deviation_dict: dict,
    random_state: int,
    min_history_days: int = _CALSWEEP_MIN_HISTORY_DAYS,
) -> list[dict]:
    """V1 calibration sweep over PARABOLIC_VELOCITY_THRESHOLD and VWAP_CROSS_HWM_PCT only.

    Consumes O1 purge+embargo, the Harvey & Liu selection haircut, O3 timestamped
    study names, O5 Sortino objective, O6 frozen-eval fold — same methodology as
    run_autotuner but search space is limited to the two V1 parameters. Does NOT
    persist anything to the DB (AC-V1.3: read-only, operator-gated rollout).

    ``min_history_days`` is injectable for testing (pass 0 to bypass the AC-4
    history floor on short fixtures); production callers should use the default
    ``_CALSWEEP_MIN_HISTORY_DAYS``.

    Note: the VWAP_CROSS_HWM_PCT bounds used here (via ``_SS_VWAP_CROSS_HWM_V1_MIN``
    / ``_SS_VWAP_CROSS_HWM_V1_MAX``) are asymmetric relative to the production
    walk-forward bounds (``_SS_VWAP_CROSS_HWM_MIN`` / ``_SS_VWAP_CROSS_HWM_MAX``):
    the V1 lower bound expands BELOW the production lower bound (0.3 vs 0.5) while
    the V1 upper bound narrows BELOW the production upper bound (2.0 vs 2.5). The
    asymmetry is intentional — see the source-comment block above those V1
    constants for the per-direction math rationale (3-tick confirm gate at the
    lower end; ~2sigma reliability limit at the upper end).

    Operator advisory: a calibration proposal in [0.3, 0.5) falls outside the
    production walk-forward search space and cannot be reproduced by the
    production optimizer — treat such proposals as informational only.

    Returns a list of report dicts, one per tuned param per symphony found in
    history_data. The caller decides whether to act on proposals.
    """
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    run_timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    # --- Fold partitioning (same O1 logic as run_autotuner) ---
    all_dates: set[str] = set()
    for sym_data in history_data.values():
        all_dates.update(sym_data.keys())
    sorted_dates = sorted(all_dates)
    total_days = len(sorted_dates)

    if total_days < 2:
        return []

    val_start_idx = int(total_days * TRAIN_RATIO)
    frozen_start_idx = int(total_days * (TRAIN_RATIO + VALIDATION_RATIO))

    # Boundary 1 — train | validation: purge + embargo on train side.
    effective_train_cutoff = max(0, val_start_idx - PURGE_DAYS - EMBARGO_DAYS)

    # Boundary 2 — validation | frozen-eval: purge + embargo on validation side.
    val_purge_end_idx = frozen_start_idx - PURGE_DAYS - EMBARGO_DAYS
    validation_dates_purged = set(
        sorted_dates[val_start_idx : max(val_start_idx, val_purge_end_idx)]
    )

    frozen_dates = set(sorted_dates[frozen_start_idx:])

    trading_day_start = sorted_dates[0] if sorted_dates else ""
    trading_day_end = sorted_dates[frozen_start_idx - 1] if frozen_start_idx > 0 else ""

    history_validation: dict = {}
    history_frozen: dict = {}
    for sym_id, sym_data in history_data.items():
        history_validation[sym_id] = {
            d: t for d, t in sym_data.items() if d in validation_dates_purged
        }
        history_frozen[sym_id] = {d: t for d, t in sym_data.items() if d in frozen_dates}

    # Derive trigger counts on validation fold for current params (denominator for freq change).
    def _count_triggers(p: dict, sym_ids: list, h: dict) -> int:
        total = 0
        for sid in sym_ids:
            returns = _collect_sim_returns(p, h, [sid], current_date_str, deviation_dict)
            total += len(returns)
        return total

    report_rows: list[dict] = []
    symphony_ids = list(history_data.keys())

    for sym_id in symphony_ids:
        # AC-4: skip symphonies with insufficient history — fold partitioning
        # on <min_history_days produces validation windows too small for the
        # Sortino objective to yield meaningful signal.  The floor is injectable
        # via the min_history_days param (default: _CALSWEEP_MIN_HISTORY_DAYS).
        sym_days = len(history_data.get(sym_id, {}))
        if sym_days < min_history_days:
            logging.warning(
                "run_calibration_sweep: skipping %s — only %d days (< %d required)",
                sym_id,
                sym_days,
                min_history_days,
            )
            continue

        study_timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        # AC-6: append __calsweep suffix so sweep studies are identifiable at a
        # glance and never collide with production run_autotuner study names.
        study_name = f"{study_timestamp}__{sym_id}__calsweep"

        base_p = current_params.copy()

        def objective(trial, _sym_id=sym_id, _base=base_p):
            p = _base.copy()
            p["PARABOLIC_VELOCITY_THRESHOLD"] = trial.suggest_float(
                "PARABOLIC_VELOCITY_THRESHOLD", _SS_PARA_VEL_MIN, _SS_PARA_VEL_MAX
            )
            p["VWAP_CROSS_HWM_PCT"] = trial.suggest_float(
                "VWAP_CROSS_HWM_PCT", _SS_VWAP_CROSS_HWM_V1_MIN, _SS_VWAP_CROSS_HWM_V1_MAX
            )
            daily_returns = _collect_sim_returns(
                p, history_validation, [_sym_id], current_date_str, deviation_dict
            )
            # Persist the per-trial return series so the Harvey & Liu haircut can
            # source each trial's observation count T = len(daily_returns).
            trial.set_user_attr("daily_returns", daily_returns)
            return compute_sortino_ratio(daily_returns)

        sampler = optuna.samplers.TPESampler(seed=random_state)
        # Pruner: NopPruner (OPTUNA-2 pin) — same rationale as run_autotuner.
        # Objective is end-of-trial-scored; the simulation runs to completion
        # with no intermediate step reporting to Optuna. Explicit NopPruner
        # prevents a future intermediate step-reporting addition from silently
        # activating MedianPruner and censoring the BHY (Harvey & Liu) haircut's
        # complete-trial-set assumption and the N_effective additive accounting.
        # Pruner-family change = methodology change; surface to PM first.
        study = optuna.create_study(
            study_name=study_name,
            direction="maximize",
            sampler=sampler,
            load_if_exists=False,
            pruner=optuna.pruners.NopPruner(),
        )
        # n_jobs sourced from OPTUNA_N_JOBS env (OPTUNA-6 uniform application across all
        # autotuner study sites; default 1 for SQLite RDBStorage writer-lock safety).
        _sweep_n_jobs = _resolve_optuna_n_jobs_from_env()
        logging.debug(
            "run_calibration_sweep: n_jobs=%s (env key=%s)", _sweep_n_jobs, _OPTUNA_N_JOBS_ENV
        )
        # catch=(Exception,): per-trial exceptions are converted to FAILED state
        # and logged (Optuna default is no catch — TypeError / ValueError in the
        # objective would propagate and crash the sweep). The calibration sweep
        # is advisory-only; any individual trial crash should degrade to
        # no_completed_trials rather than aborting the entire symphony sweep.
        study.optimize(
            objective,
            n_trials=OPTUNA_N_TRIALS_CALIBRATION,
            n_jobs=_sweep_n_jobs,
            catch=(Exception,),
        )

        n_trials = len([t for t in study.trials if t.value is not None])

        # Guard: if every trial failed (n_trials == 0) there is no best trial.
        # Emit a degraded row set using current_params so the report surface can
        # display an honest "no_completed_trials" outcome without crashing.
        if n_trials == 0:
            logging.warning(
                "run_calibration_sweep: all trials failed for %s — "
                "emitting degraded rows with haircut_outcome=no_completed_trials",
                sym_id,
            )
            for param_name in ("PARABOLIC_VELOCITY_THRESHOLD", "VWAP_CROSS_HWM_PCT"):
                current_value = float(current_params.get(param_name, 0.0))
                report_rows.append(
                    {
                        "symphony_id": sym_id,
                        "param_name": param_name,
                        "current_value": current_value,
                        "proposed_value": current_value,
                        "delta_pct": 0.0,
                        "expected_trigger_freq_change": 0.0,
                        "frozen_eval_alpha": None,
                        "naive_sharpe": None,
                        "selection_tstat": None,
                        "haircut_outcome": "no_completed_trials",
                        "pbo_veto_status": True,
                        "flag_for_operator_review": False,
                        "sortino": None,
                        "n_trials": 0,
                        "study_name": study_name,
                        "trading_day_start": trading_day_start,
                        "trading_day_end": trading_day_end,
                        "cycle_id": run_timestamp,
                    }
                )
            continue

        naive_sharpe_value: float | None = study.best_value
        best_params = study.best_params

        # --- HARVEY & LIU SELECTION HAIRCUT ---
        # Same multiple-testing correction as run_autotuner: re-rank the completed
        # trials by Benjamini-Hochberg-adjusted p-value and select the BHY winner
        # if it clears the FDR gate. selection_tstat carries the winner's
        # t-statistic (higher-is-better); if no trial clears the gate the sweep
        # reports the naive Optuna winner with no haircut statistic.
        selection_tstat_value: float | None = None
        completed_trials = [t for t in study.trials if t.value is not None]

        # filter_sortino_sentinels excludes math_engine._SORTINO_SENTINEL (1e6)
        # zero-downside trials AND partial-sentinel CPCV means before the haircut —
        # a sentinel's t-statistic would dominate the BHY adjustment.
        haircut_trials = filter_sortino_sentinels(completed_trials)

        # haircut_outcome makes the FDR-gate verdict explicit on every report
        # row — without it a noise-grade naive winner (no trial cleared the
        # gate) is indistinguishable from a gate-cleared one, since both carry
        # selection_tstat=None. Diagnostic-only, but the operator must be able
        # to tell a statistically-qualified proposal from an unqualified one.
        if not haircut_trials:
            haircut_outcome = "not_run"
        else:
            # NEFF-001 fix: compute n_effective before _haircut_select.
            # run_calibration_sweep does not persist to DB and lacks bundle context,
            # so the ledger query returns empty → S=0 → n_eff == len(haircut_trials),
            # which is byte-identical to the pre-wiring behavior (plan D2).
            n_eff_cal = compute_n_effective(
                n_optuna=len(haircut_trials),
                ledger_query=lambda: [],
            )
            winner_trial, winner_p_adj, winner_tstat = _haircut_select(
                haircut_trials, n_effective=n_eff_cal
            )
            if winner_trial is not None:
                best_params = winner_trial.params
                naive_sharpe_value = winner_trial.value
                selection_tstat_value = winner_tstat
                haircut_outcome = "cleared"
            else:
                # The haircut ran but no trial cleared the FDR gate — the
                # proposed_value below is the NAIVE Optuna winner and is NOT
                # statistically qualified.
                haircut_outcome = "no_trial_cleared"

        # Validation-fold Sortino of best trial params
        best_p = current_params.copy()
        for k, v in best_params.items():
            best_p[k] = round(v, 4)

        val_returns = _collect_sim_returns(
            best_p, history_validation, [sym_id], current_date_str, deviation_dict
        )
        sortino_value = compute_sortino_ratio(val_returns) if val_returns else None

        # --- O6: frozen eval — consumed once, post-selection ---
        frozen_returns = _collect_sim_returns(
            best_p, history_frozen, [sym_id], current_date_str, deviation_dict
        )
        frozen_eval_alpha = compute_sortino_ratio(frozen_returns) if frozen_returns else None

        # Trigger frequency change: current params vs proposed params on validation fold
        current_trigger_count = _count_triggers(current_params, [sym_id], history_validation)
        proposed_trigger_count = _count_triggers(best_p, [sym_id], history_validation)
        expected_trigger_freq_change = float(proposed_trigger_count - current_trigger_count)

        # AC-5: PBO veto status — a haircut that found no qualified winner is
        # treated as a veto signal; the naive Optuna winner is not a certified
        # recommendation.
        pbo_veto_status = haircut_outcome == "no_trial_cleared"

        # AC-7: flag when proposed trigger frequency is >2× the current count
        # so the operator must review before any per-symphony deploy.
        flag_for_operator_review = (
            current_trigger_count > 0
            and proposed_trigger_count / current_trigger_count
            > _CALSWEEP_TRIGGER_FREQ_FLAG_MULTIPLIER
        )

        # Emit one row per tuned param
        for param_name in ("PARABOLIC_VELOCITY_THRESHOLD", "VWAP_CROSS_HWM_PCT"):
            current_value = float(current_params.get(param_name, best_p.get(param_name, 0.0)))
            proposed_value = float(best_p.get(param_name, current_value))
            delta_pct = (
                (proposed_value - current_value) / abs(current_value) * 100.0
                if current_value != 0
                else 0.0
            )
            report_rows.append(
                {
                    "symphony_id": sym_id,
                    "param_name": param_name,
                    "current_value": current_value,
                    "proposed_value": proposed_value,
                    "delta_pct": delta_pct,
                    "expected_trigger_freq_change": expected_trigger_freq_change,
                    "frozen_eval_alpha": frozen_eval_alpha,
                    "naive_sharpe": naive_sharpe_value,
                    "selection_tstat": selection_tstat_value,
                    "haircut_outcome": haircut_outcome,
                    "pbo_veto_status": pbo_veto_status,
                    "flag_for_operator_review": flag_for_operator_review,
                    "sortino": sortino_value,
                    "n_trials": n_trials,
                    "study_name": study_name,
                    "trading_day_start": trading_day_start,
                    "trading_day_end": trading_day_end,
                    "cycle_id": run_timestamp,
                }
            )

    return report_rows
