import dataclasses
import hashlib
import math
import os
from collections import OrderedDict

import numpy as np

# Sentinel returned by compute_sortino_ratio (autotuner.py) when downside_deviation==0
# (all trial returns beat the target). The value is finite and looks like a valid trial
# result to Optuna's TPE, but its magnitude (~1e6) would dominate the cross-trial
# distribution that the Harvey & Liu selection haircut counts over. Filtering before the
# haircut prevents a degenerate trial from masquerading as a genuine signal.
# Source: autotuner.py compute_sortino_ratio — returns 1e6 for zero downside_deviation.
_SORTINO_SENTINEL = 1e6


def filter_sortino_sentinels(sortino_values: list[float]) -> list[float]:
    """Remove sentinel values from a Sortino trial series before the selection haircut.

    Returns a new list with all _SORTINO_SENTINEL (1e6) entries removed. The input
    list is not mutated. Legitimate trial values — including negative Sortinos and 0.0
    (empty-returns series) — are preserved unchanged.

    Call this on the trial Sortino values BEFORE the Harvey & Liu selection haircut.
    A sentinel's ~1e6 magnitude would produce a t-statistic so large its p-value
    underflows to the clamp floor, letting a degenerate (zero-downside) trial win the
    haircut over a genuinely-skilled one.
    """
    return [v for v in sortino_values if v != _SORTINO_SENTINEL]


def _reject_non_finite(**kwargs):
    """
    Reject NaN / +Inf / -Inf in named float parameters at function entry.

    Policy: math layers must never silently propagate non-finite values into
    exit decisions (a NaN comparison short-circuits to False and can suppress
    a legitimate stop trigger; an Inf can spuriously trigger one). Callers
    pass ONLY float-typed parameters by name; ints and bools are intentionally
    NOT validated (truthiness pins in existing tests rely on bool inputs).
    """
    for name, v in kwargs.items():
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError(f"NaN input not allowed: {name}={v!r}")


def _reject_non_finite_in_records(records, *field_names):
    """Iterate list of dicts and reject any non-finite float in named fields.

    Missing keys are skipped silently (matches production's existing
    .get()/`in`-guarded handling of optional fields); only present-and-float
    values are validated via _reject_non_finite.
    """
    for record in records:
        kwargs = {field: record[field] for field in field_names if field in record}
        _reject_non_finite(**kwargs)


# ---------------------------------------------------------------------------
# Module-level named constants (project rule: no magic numbers in math_engine)
# ---------------------------------------------------------------------------

LOOKBACK_DAYS = 20  # 20-day realized-volatility window — Planet Stopper risk-sizing standard
ATR_LOOKBACK_DAYS = 15  # 14-day true-range window (standard ATR period) + 1 prior close required to compute the first TR; matches Planet Stopper's risk-sizing assumption
PCT_SCALAR = 100.0  # decimal return -> percentage points (math layer normalizes to pct)

# ---------------------------------------------------------------------------
# CSCV PBO gate constants (compute_pbo — Phase-3 reduced-N CSCV acceptance gate)
# Source: Bailey & López de Prado 2014, "The Probability of Backtest Overfitting",
# Journal of Computational Finance 20(4), DOI 10.21314/JCF.2014.005.
# ---------------------------------------------------------------------------
_CSCV_TOP_K: int = 20  # top-K PRE-BHY configs fed into compute_pbo (selection-process robustness)
_CSCV_S: int = 8  # number of contiguous chronological blocks for the IS/OOS partition
# Reject threshold: derived — random config selection implies PBO=0.5 (IS-best ranks
# uniformly over 1..K on OOS, so lambda<=0 with probability 0.5). PBO>0.5 means the
# IS-best performs WORSE-than-random on OOS — definitive evidence of backtest overfitting.
# Source: Bailey&LdP 2014 §3.4 "Interpretation of PBO".
PBO_REJECT_THRESHOLD: float = 0.5

# Monte Carlo gating constants (run_monte_carlo)
# Out-of-band sentinel returned when MC history is insufficient. run_monte_carlo
# returns None (NOT an in-band probability) so the caller can distinguish "MC
# could not run" from a genuine probability. compute_exit_confirmation treats
# None as "MC confirmation unavailable" — it does NOT veto the protective stop,
# which still fires on the ticks-below-stop condition alone (fail-safe).
# CRRA utility constants — Phase-1 HARDEN-core (M1 plan §Deliverables, W-H4 + W-H2).
# Tolerance around gamma == 1 for the log-utility branch. Avoids 1/(1-gamma) blow-up
# near gamma=1. The gamma->1 limit of (W^(1-gamma) - 1)/(1-gamma) is ln(W)
# (L'Hopital; Merton 1969 / Samuelson 1969).
# Named constant: no-magic-numbers rule (project CLAUDE.md).
CRRA_LOG_UTILITY_GAMMA_TOL: float = 1e-9
# Floor applied to the wealth argument W_i = max(WEALTH_ARG_FLOOR, 1 + r_i) before
# compute_crra_utility. Prevents log(0) / W^(1-gamma) blow-up for catastrophic returns
# (-100% or near). Floor is on INPUT W; never on output U (W-H4 contract).
# Source: decision-science-council-synthesis.md §3.9 W-H4.
WEALTH_ARG_FLOOR: float = 0.001

MC_INSUFFICIENT_HISTORY_SENTINEL = None
MC_MIN_HISTORY_DAYS = (
    20  # Minimum history rows for MC simulation to run; below this we short-circuit
)
MC_VOL_WINDOW_DAYS = 20  # Rolling SPY vol window; arithmetic uses (DAYS - 1) for inclusive endpoint
MC_DEFAULT_SIMULATION_PATHS = 5000  # Default MC path count — CLT stability vs runtime tradeoff
MC_DEFAULT_NEIGHBOR_K = (
    150  # Default kNN regime locality — smaller=tighter regime match, larger=smoother estimate
)
# Seed modulus maps the SHA-256 digest into a 64-bit seed space [0, 2^64).
# The daemon runs ~98,280 cycles/year (252 trading days x 390 market minutes);
# the birthday-bound expected collision count at modulus M is ~ n^2 / (2M). At
# 2^31 that was ~2.2 expected collisions/year — a birthday-bound problem the
# prior comment mischaracterised; at 2^64 it falls to ~2.6e-19, i.e. negligible.
# numpy's default_rng / SeedSequence accept the full 64-bit space.
MC_SEED_MODULUS = 2**64

# Regime-match-quality guard threshold (vision-audit Critical Rec #2 / math-reaudit Surface 3).
# chi-squared 0.99-quantile with df=2 (number of z-scored kNN features: SPY return + rolling vol).
# The test statistic this gate compares against is the MEAN of K squared (z-scored Euclidean)
# distances over the K nearest candidate-pool neighbours, NOT a single squared distance. Under
# the null that today is drawn from the same joint distribution as the candidate pool, a single
# squared Mahalanobis distance follows chi2(df=2); the MEAN of K such draws concentrates near
# 2 (the mean of chi2(2)) — its 99th percentile is approximately 2.40 (chi2(2*K)/K at K=150).
# Setting the threshold to chi2(2)_{0.99} ~= 9.21 (the SINGLE-DRAW 0.99-quantile) is therefore
# intentionally CONSERVATIVE: an effective false-positive rate well below 1% for the mean
# statistic. This is the correct direction for operator safety — the gate fires only on
# extreme regime breaks, not on ordinary daily variance.
# Source: Mahalanobis (1936) "On the generalised distance in statistics"; Aggarwal (2017)
# Outlier Analysis 2nd ed. Sec 2.3 / 4.4; Knorr & Ng (1998) VLDB '98 kNN-outlier framework.
# chi2(2)_{0.99} = 9.21034037197618 (scipy.stats.chi2.ppf(0.99, df=2)).
# Operator override: read from env var MC_REGIME_MATCH_CHI2_THRESHOLD at call time; module
# constant is the default.
MC_REGIME_MATCH_CHI2_THRESHOLD: float = 9.21034037197618

# CVaR tail percentile for compute_portfolio_cvar (M2 Phase-1 anchor).
# CVaR at 5% = expected shortfall = mean return in the worst 5% of simulated paths.
# Source: decision-science-council-synthesis.md §2.6 / council-attack-rubric.md M-2.
CVAR_TAIL_PCT = 0.05  # 5th-percentile tail — worst 5% of simulation paths

# M2 diagnostic constants (plan §Deliverables 'Constants').
# CVAR_ALPHA_DEFAULT: tail probability for compute_cvar_5pct_general_distribution.
# Source: decision-science-council-synthesis.md §2.6 / H-2 binding.
CVAR_ALPHA_DEFAULT = 0.05  # 5th-percentile expected shortfall (CVaR at 5%)
# CVAR_MIN_TAIL_OBS: minimum distinct tail observations required to produce a
# non-sentinel estimate. At k=0 the R-U formula degenerates; guard returns sentinel.
CVAR_MIN_TAIL_OBS = 1  # require at least 1 genuine below-VaR (or atom) observation


# kNN historical regime-match result (Phase-1; the forward-path co-signal was REJECTED
# per decision-science council — see docs/audit/vision-audit-2026-05-27/SYNTHESIS.md CVaR-divergence wall).
# Phase-1 rule: ZERO production consumers permitted — tests only.
# Fail-safe invariant: cvar_pct is None IMPLIES breach is False.
# Enforcement: __post_init__ raises ValueError on the illegal combination.
@dataclasses.dataclass(frozen=True)
class CVaRAssessment:
    """Typed result for the kNN historical regime-match (Phase-1; the forward-path
    co-signal was REJECTED per decision-science council — see docs/audit/vision-audit-2026-05-27/SYNTHESIS.md
    CVaR-divergence wall).

    cvar_pct: 5th-percentile CVaR as a percentage (negative = loss).
              None is the out-of-band insufficient sentinel (mirrors
              MC_INSUFFICIENT_HISTORY_SENTINEL). None here means the simulator
              could not produce an estimate; the heuristic floor still protects.
    breach:   True when CVaR exceeds the operator breach threshold.
              MUST be False when cvar_pct is None (fail-safe).
    tail_obs_count: tail observations used for the CVaR estimate; 0 when None.
    insufficient_reason: human-readable explanation when cvar_pct is None.
    """

    cvar_pct: float | None
    breach: bool
    tail_obs_count: int
    stderr: float | None
    insufficient_reason: str | None

    def __post_init__(self) -> None:
        # Fail-safe: an absent CVaR estimate is never a breach signal.
        if self.cvar_pct is None and self.breach:
            raise ValueError(
                "CVaRAssessment: cvar_pct is None but breach is True — fail-safe violated. "
                "An absent CVaR estimate must not trigger a breach; the heuristic floor "
                "(trailing stop) remains the safety backstop."
            )
        # Contract: no tail observations exist when the estimate is absent.
        if self.cvar_pct is None and self.tail_obs_count != 0:
            raise ValueError(
                f"CVaRAssessment: cvar_pct is None but tail_obs_count is "
                f"{self.tail_obs_count} (must be 0 for an insufficient sentinel)."
            )
        # Fail-safe pairing: stderr is only meaningful when cvar_pct is present.
        if self.cvar_pct is None and self.stderr is not None:
            raise ValueError(
                "CVaRAssessment: cvar_pct is None but stderr is not None — fail-safe violated. "
                "A standard error has no meaning when no estimate was produced."
            )
        # Fail-safe pairing: a present estimate must carry its uncertainty.
        if self.cvar_pct is not None and self.stderr is None:
            raise ValueError(
                "CVaRAssessment: cvar_pct is present but stderr is None — fail-safe violated. "
                "Every estimate must carry its standard error for S-3 display contract."
            )


@dataclasses.dataclass(frozen=True)
class RegimeMatchAssessment:
    """Typed result for the MC regime-match-quality guard (vision-audit Critical Rec #2).

    mean_sq_mahalanobis: mean squared Mahalanobis-style distance from today's z-scored
                         query point (SPY return, rolling vol) to its K nearest candidate-
                         pool neighbours. None is the out-of-band insufficient sentinel —
                         mirrors MC_INSUFFICIENT_HISTORY_SENTINEL; eligible pool was below
                         MC_MIN_HISTORY_DAYS so no score can be computed.
    is_unprecedented:    True when mean_sq_mahalanobis > threshold_used. The default threshold
                         (chi2(2)_{0.99}) is the SINGLE-DRAW 0.99-quantile applied to a
                         MEAN-of-K test statistic, so the gate is intentionally conservative
                         — fires only on extreme regime breaks (effective false-positive rate
                         well below 1%). MUST be False when mean_sq_mahalanobis is None
                         (fail-safe: an absent diagnostic is not a suppression signal).
    neighbor_k:          The K used for the kNN mean-squared-distance test statistic.
    threshold_used:      The threshold actually applied at call time (the module-level
                         MC_REGIME_MATCH_CHI2_THRESHOLD default or the env-var override).
                         Telemetry field: lets an operator audit which threshold was in
                         force at any suppression decision.
    insufficient_reason: Human-readable explanation when mean_sq_mahalanobis is None.
                         None when a score was successfully computed.
    """

    mean_sq_mahalanobis: float | None
    is_unprecedented: bool
    neighbor_k: int
    threshold_used: float
    insufficient_reason: str | None

    def __post_init__(self) -> None:
        # Fail-safe: an absent diagnostic is not a suppression signal.
        if self.mean_sq_mahalanobis is None and self.is_unprecedented:
            raise ValueError(
                "RegimeMatchAssessment: mean_sq_mahalanobis is None but "
                "is_unprecedented is True — fail-safe violated. "
                "An absent regime-match diagnostic must not trigger MC suppression; "
                "the insufficient-pool sentinel is (None, False)."
            )


# Time-squeeze decay constants (drives intraday tightening of trailing stops)
# PROVENANCE: f(t) = 1 - sqrt(1 - t), the i.i.d.-returns "remaining-session
# uncertainty" curve. Under the standard square-root-of-time scaling for
# i.i.d. log-returns with constant per-unit-time variance, the standard
# deviation of remaining-session returns scales as sqrt(1-t); tightness
# (1 - remaining_std / full_std) is therefore 1 - sqrt(1-t). Endpoints
# f(0)=0, f(1)=1. Concave, monotone, front-loaded (open-loaded). No fitted
# constants — the formula is closed-form THEORY with zero free parameters.
# The curve is less aggressive midday (~0.45 pp wider stop at t=0.5) than
# the prior log10 heuristic; monotone-converging at endpoints (f(0)=0
# exactly, f(1)=1 exactly). Cited: Danielsson & Zigrand (2003), LSE FMG
# DP-439, https://eprints.lse.ac.uk/24827/1/dp439.pdf.
# Research note: docs/research/m3-provenance/literature-pass.md.
# freeze_discipline = THEORY.
# DECAY_CURVE_SCALAR removed — closed by the sqrt-time derivation (M3).
MULT_OPEN = 1.5  # dynamic_multiplier at market open (loosest stop)
MULT_CLOSE = 0.5  # dynamic_multiplier at market close (tightest)
MIN_STOP_OPEN = 0.3  # min stop floor at market open, in percentage points
MIN_STOP_CLOSE = 0.15  # min stop floor at market close, in percentage points
VOL_FALLBACK = 1.0  # neutral fallback for safe_vol when symphony_vol <= 0 (preserves vol-scale arithmetic in the degenerate-vol case)

# Breakeven-lock constants (drives HWM-hold-based stop tightening)
BREAKEVEN_ACTIVATION_MIN = (
    0.4  # lower clamp for dynamic activation threshold (in percentage points)
)
BREAKEVEN_ACTIVATION_MAX = 3.0  # upper clamp for dynamic activation threshold
BREAKEVEN_ACTIVATION_DEADBAND = (
    0.2  # current_return must be within this distance below dynamic_activation to count a tick
)
HWM_HOLD_TICKS_THRESHOLD = (
    5  # consecutive qualifying ticks needed to lock breakeven (transition is one-way)
)
TRIGGERED_OVERRIDE_LEVEL = (
    -999.0
)  # sentinel stop level when position is already triggered (suppresses re-exit)


def compute_para_arm_decision(
    current_return: float,
    prev_return: float,
    para_threshold: float,
    currently_armed: bool,
) -> tuple[float, bool]:
    """
    Pure decision for parabolic-squeeze arming.

    Returns (velocity, should_arm_transition).
      - velocity = current_return - prev_return (no scaling, no clamping)
      - should_arm_transition = True iff (velocity >= threshold) AND (not currently_armed)
      - Once armed, never re-arms. Caller is responsible for state mutation,
        prints, and DB logging.

    Extracted from alpha_bot_execution.py:559-569 to comply with the project
    file-map rule that math layers live in math_engine.py.
    """
    _reject_non_finite(
        current_return=current_return, prev_return=prev_return, para_threshold=para_threshold
    )
    velocity = float(current_return) - float(prev_return)
    should_arm = bool((velocity >= para_threshold) and (not currently_armed))
    return velocity, should_arm


def compute_time_squeeze_decay(time_ratio: float) -> tuple[float, float]:
    """
    Returns (dynamic_multiplier, dynamic_min_stop) for the time-squeeze decay
    curve.

    - time_ratio MUST be in the closed interval [0.0, 1.0] (fraction of trading
      session elapsed: 0.0 = action-window open / EXECUTION_START_TIME, 1.0 = close).
      A value outside that range raises ValueError — reject-don't-coerce policy (M-1).
    - decay_curve = 1 - sqrt(1 - time_ratio), the i.i.d.-returns remaining-
      session uncertainty curve (Danielsson & Zigrand 2003). Ranges from 0.0
      at open to 1.0 at close. Concave, monotone, front-loaded (open-loaded).
      Zero free parameters — THEORY provenance. Research note:
      docs/research/m3-provenance/literature-pass.md §1.
    - dynamic_multiplier linearly interpolates from MULT_OPEN at decay=0
      to MULT_CLOSE at decay=1.
    - dynamic_min_stop linearly interpolates from MIN_STOP_OPEN at decay=0
      to MIN_STOP_CLOSE at decay=1.

    Pure. No I/O. No state. No datetime handling.

    Extracted from alpha_bot_execution.py:574-585 (cycle 4 of math-layer
    extraction). Formula re-derived M3 from first principles (M3 redrive).
    """
    _reject_non_finite(time_ratio=time_ratio)
    if time_ratio < 0.0 or time_ratio > 1.0:
        raise ValueError(f"time_ratio must be in the range [0, 1]; got {time_ratio!r}")
    decay_curve = 1.0 - math.sqrt(1.0 - time_ratio)
    dynamic_multiplier = float(MULT_OPEN - (MULT_OPEN - MULT_CLOSE) * decay_curve)
    dynamic_min_stop = float(MIN_STOP_OPEN - (MIN_STOP_OPEN - MIN_STOP_CLOSE) * decay_curve)
    return dynamic_multiplier, dynamic_min_stop


def compute_active_trailing_stop(
    symphony_vol: float,
    dynamic_multiplier: float,
    dynamic_min_stop: float,
    para_armed: bool,
    breakeven_locked: bool,
    parabolic_squeeze_multiplier: float,
) -> float:
    """
    Computes the active trailing-stop distance (in percentage points).

    Logic (extracted verbatim from alpha_bot_execution.py):
      safe_vol = symphony_vol if symphony_vol > 0 else VOL_FALLBACK
      active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)
      if para_armed or breakeven_locked:
          active *= parabolic_squeeze_multiplier
      return active

    parabolic_squeeze_multiplier MUST be strictly positive. A value <= 0 is
    rejected at entry with ValueError — reject-don't-coerce policy (M-2): a
    zero multiplier collapses the stop distance to 0 (instant exit on the
    first noise tick) and a negative one yields a negative distance that
    places the stop ABOVE the high-water mark. Clamping would mask a mistuned
    Optuna MAX_PARABOLIC_SQUEEZE parameter; rejection surfaces it.

    Pure. No I/O. No state. CALLER is responsible for normalizing
    bot_state[...].get("para_armed") and ...get("breakeven_locked") to
    strict Python bool BEFORE passing (typically via bool(...)).
    """
    _reject_non_finite(
        symphony_vol=symphony_vol,
        dynamic_multiplier=dynamic_multiplier,
        dynamic_min_stop=dynamic_min_stop,
        parabolic_squeeze_multiplier=parabolic_squeeze_multiplier,
    )
    if parabolic_squeeze_multiplier <= 0:
        raise ValueError(
            f"parabolic_squeeze_multiplier must be > 0; got {parabolic_squeeze_multiplier!r}"
        )
    safe_vol = symphony_vol if symphony_vol > 0 else VOL_FALLBACK
    active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)
    if para_armed or breakeven_locked:
        active *= parabolic_squeeze_multiplier
    return float(active)


def compute_breakeven_update(
    current_return: float,
    symphony_vol: float,
    base_stop_level: float,
    current_hold_ticks: int,
    currently_breakeven_locked: bool,
    is_triggered: bool,
) -> tuple[int, bool, float]:
    """
    Computes the breakeven-lock state update and the resolved stop trigger level.

    Returns (new_hold_ticks, new_breakeven_locked, stop_trigger_level).

    Logic:
      dynamic_activation = clamp(symphony_vol, BREAKEVEN_ACTIVATION_MIN, BREAKEVEN_ACTIVATION_MAX)
      if current_return >= dynamic_activation - BREAKEVEN_ACTIVATION_DEADBAND:
          new_hold_ticks = current_hold_ticks + 1
      else:
          new_hold_ticks = 0
      new_breakeven_locked = currently_breakeven_locked or (new_hold_ticks >= HWM_HOLD_TICKS_THRESHOLD)
      if new_breakeven_locked:
          stop_trigger_level = max(base_stop_level, 0.0)   # 0.0 is structural — semantic anchor "breakeven = no worse than zero loss"
      else:
          stop_trigger_level = base_stop_level
      if is_triggered:
          stop_trigger_level = TRIGGERED_OVERRIDE_LEVEL

    HWM-ANCHORED TRAILING STOP: the caller supplies ``base_stop_level`` as
    ``high_water_mark - vol_scaled_distance``, recomputed every tick. This
    function resolves that base each call with NO cross-tick clamp — the
    resolved stop tracks the HWM upward as the HWM rises, and is allowed to
    move DOWN when the vol-scaled distance widens (e.g. a mid-day volatility
    spike). Freezing the resolved level would discard a legitimate widening
    and keep the stop too tight for realized vol; a true HWM-anchored stop
    recomputes HWM - distance each tick. The high-water mark itself is
    monotone non-decreasing within a position, but that monotonicity is
    engine-side state — not enforced here.

    LATCHING INVARIANT: once currently_breakeven_locked=True, new_breakeven_locked
    is ALWAYS True regardless of any other input. The inline producer never
    resets breakeven_locked to False; that one-way transition must be preserved.

    BREAKEVEN FLOOR: once the breakeven latch fires, the resolved stop is
    floored at 0.0 — "lock gains hard". The stop may still move down between
    post-lock ticks but never below the 0.0 breakeven floor. This floor is the
    only ratchet in the resolved stop level. When is_triggered=True the stop
    is the committed-exit sentinel TRIGGERED_OVERRIDE_LEVEL (-999.0).

    Trailing-stop construction reference: Fu, Y.B. & Zhang, Z.G. (2012),
    Int. J. Operations Research 9(3), 129-140.

    Pure. No I/O. No state. Caller assigns the returned new_hold_ticks and
    new_breakeven_locked back into bot_state.
    """
    _reject_non_finite(
        current_return=current_return,
        symphony_vol=symphony_vol,
        base_stop_level=base_stop_level,
    )
    dynamic_activation = max(BREAKEVEN_ACTIVATION_MIN, min(BREAKEVEN_ACTIVATION_MAX, symphony_vol))
    if current_return >= (dynamic_activation - BREAKEVEN_ACTIVATION_DEADBAND):
        new_hold_ticks = current_hold_ticks + 1
    else:
        new_hold_ticks = 0
    new_breakeven_locked = bool(
        currently_breakeven_locked or (new_hold_ticks >= HWM_HOLD_TICKS_THRESHOLD)
    )
    if new_breakeven_locked:
        stop_trigger_level = max(base_stop_level, 0.0)
    else:
        stop_trigger_level = base_stop_level
    if is_triggered:
        stop_trigger_level = TRIGGERED_OVERRIDE_LEVEL
    return int(new_hold_ticks), new_breakeven_locked, float(stop_trigger_level)


# Exit-confirmation constants (gates trailing-stop trigger)
MAGNITUDE_FLOOR_PCT = 0.10  # return must drop at least this far BELOW stop_trigger_level to count toward exit confirmation
# MC underperformance fraction (run_monte_carlo output) AT OR ABOVE this value
# confirms a regime breakdown -> the protective stop is ALLOWED to fire; BELOW it
# the MC second opinion vetoes a capitulation ("the analog distribution says today
# is normal noise, don't sell at a local low"). run_monte_carlo returns the fraction
# of regime-matched analog days that beat us (HIGH ~100 when badly underperforming,
# LOW ~0 when outperforming). Value 60.0 is preserved verbatim from the pre-H1
# MC_SANITY_THRESHOLD; H1 is a naming + gate-operator fix (the old name/operator
# read the value inverted), NOT a retune. Source: fork-base hand-set risk-policy dial.
MC_BREAKDOWN_THRESHOLD = 60.0
EXIT_CONFIRM_TICKS = 3  # consecutive qualifying ticks needed to flip is_trailing_stop_hit


def compute_exit_confirmation(
    armed: bool,
    is_triggered: bool,
    current_return: float,
    stop_trigger_level: float,
    prob_underperforming: float | None,
    current_below_stop_count: int,
    exit_confirm_ticks: int = EXIT_CONFIRM_TICKS,
) -> tuple[int, bool]:
    """
    Computes the trailing-stop exit-confirmation state update.

    Returns (new_below_stop_count, is_trailing_stop_hit).

    Logic (extracted verbatim from alpha_bot_execution.py):
      if not armed or is_triggered:
          return current_below_stop_count, False     # whole block skipped; state unchanged
      below_stop_condition = (current_return <= stop_trigger_level - MAGNITUDE_FLOOR_PCT)
                             and mc_breakdown_ok
      if below_stop_condition:
          new_count = current_below_stop_count + 1
          hit = (new_count >= exit_confirm_ticks)
          return new_count, hit
      else:
          return 0, False                            # reset on miss

    EXIT_CONFIRM_TICKS THRESHOLD: the number of consecutive qualifying ticks
    required to flip is_trailing_stop_hit. ``exit_confirm_ticks`` defaults to the
    module-level EXIT_CONFIRM_TICKS so all existing callers are unaffected. The
    regime-conditional exit lever (Phase 3c) passes a regime-adjusted threshold
    here via apply_regime_exit_adjustment(...) WITHOUT mutating the module-level
    constant — keeping the per-call threshold an explicit, auditable argument
    rather than global state.

    MC BREAKDOWN GATE (fail-safe): prob_underperforming is the fraction of
    regime-matched analog days that beat us (run_monte_carlo output) — HIGH
    (~100) when we are badly underperforming, LOW (~0) when outperforming. A
    real prob_underperforming AT OR ABOVE MC_BREAKDOWN_THRESHOLD confirms a
    regime breakdown and ALLOWS the exit (the magnitude-qualifying tick counts).
    BELOW the threshold the MC second opinion vetoes the exit (the count resets)
    — "the analog distribution says today is normal noise, don't capitulate at a
    local low." When prob_underperforming is None the MC second opinion is
    UNAVAILABLE (insufficient MC history, or the regime-match-quality guard
    overrode it); the gate FAILS OPEN so the protective stop still fires on the
    magnitude condition alone. Insufficient MC data must never disable the
    protective stop.

    GUARD INVARIANT: when (not armed) or is_triggered, the function returns
    the INPUT below_stop_count unchanged AND False. This preserves the inline
    behavior where the entire stop-check block is skipped — the count is
    NEITHER incremented NOR reset under those conditions.

    Pure. No I/O. No state. Caller handles print transitions by comparing
    input current_below_stop_count to returned new_below_stop_count.
    """
    if exit_confirm_ticks <= 0:
        # Reject-don't-coerce (M-2): a zero/negative threshold would arm a stop
        # that fires on the very first qualifying tick — no confirmation ladder
        # at all. apply_regime_exit_adjustment is bounded >= 1, so 0 can only
        # arrive from a direct caller bypassing the adjustment; this is the
        # last-line guard against disabling the confirmation ladder.
        raise ValueError(
            f"exit_confirm_ticks must be > 0 (confirmation ladder cannot be "
            f"disabled); got {exit_confirm_ticks!r}"
        )
    _reject_non_finite(
        current_return=current_return,
        stop_trigger_level=stop_trigger_level,
    )
    if prob_underperforming is not None:
        _reject_non_finite(prob_underperforming=prob_underperforming)
    if (not armed) or is_triggered:
        return int(current_below_stop_count), False

    # MC breakdown gate: unavailable (None) -> pass (fail-safe); otherwise a real
    # confirmed-high underperformance (>= MC_BREAKDOWN_THRESHOLD) ALLOWS the exit,
    # while a low reading vetoes it (the MC second opinion says this dip is normal).
    mc_breakdown_ok = prob_underperforming is None or prob_underperforming >= MC_BREAKDOWN_THRESHOLD
    below_stop_condition = (
        current_return <= (stop_trigger_level - MAGNITUDE_FLOOR_PCT)
    ) and mc_breakdown_ok

    if below_stop_condition:
        new_count = int(current_below_stop_count) + 1
        hit = bool(new_count >= exit_confirm_ticks)
        return new_count, hit
    else:
        return 0, False


# ---------------------------------------------------------------------------
# Phase 3c — regime-conditional exit lever (single knob: the exit-confirmation
# tick threshold). The regime LABEL is produced by an offline daily diagnostic
# (regime_classifier.classify_regime) and read from a cached DB row on the live
# path — regime_classifier is NEVER imported here (no blocking I/O on the
# 1-minute execution path; PHASE3_DECISIONS.md §Sub-phase 3a). This module only
# maps a precomputed label string to a bounded confirmation-tick count.
# ---------------------------------------------------------------------------

# Safety envelope for any regime-adjusted confirmation-tick count. No regime may
# push the lever outside [LOWER, UPPER]: LOWER >= 1 forbids a zero-confirmation
# stop (would fire on the first noise tick); UPPER caps how unarmed the restraint
# direction can make the stop. UPPER is 8 (~2.7x base EXIT_CONFIRM_TICKS) — wide
# enough for genuine restraint, tight enough that the stop still confirms within
# a few minutes. Source: PHASE3_DECISIONS.md §Regime->response (bounded table).
REGIME_TICKS_LOWER_BOUND = 1
REGIME_TICKS_UPPER_BOUND = 8

# Per-regime confirmation-tick targets (fixed theory-set constants, NOT learned).
# Direction rationale (Kaminski-Lo 2014, "Strategic Allocation to Commodity
# Factor Premiums"): serial-correlation sign governs whether a trailing stop
# helps or hurts.
#   TRENDING       -> returns PERSIST; a stop trigger is more likely a genuine
#                     reversal than noise -> MORE-ACTIVE -> fewer ticks.
#   MEAN-REVERTING -> returns REVERSE; a trigger is more likely a noise tick that
#                     will revert -> RESTRAINT -> more ticks (harder to confirm).
#   HIGH-VOL       -> stressed regime; keep the stop PROTECTIVE (not loosened to
#                     restraint territory) but bounded so it cannot collapse to a
#                     single-tick hair trigger -> at-or-below base, >= 1.
# Source: PHASE3_DECISIONS.md §Regime->response mapping; 00-ADAPTIVE-RECOMMENDATION.md §1A.
TRENDING_EXIT_TICKS = 2  # < EXIT_CONFIRM_TICKS (3): more-active
MEAN_REVERTING_EXIT_TICKS = 5  # > EXIT_CONFIRM_TICKS (3): restraint, within UPPER bound
HIGH_VOL_EXIT_TICKS = 3  # == EXIT_CONFIRM_TICKS: protective (not loosened), bounded

# Explicit, auditable label -> tick mapping. Only the three production labels
# emitted by regime_classifier.classify_regime are recognized; any other string
# (or None) falls through to the safe default (base_ticks unchanged).
_REGIME_EXIT_TICKS_TABLE: dict[str, int] = {
    "trending": TRENDING_EXIT_TICKS,
    "mean-reverting": MEAN_REVERTING_EXIT_TICKS,
    "high-vol": HIGH_VOL_EXIT_TICKS,
}


def apply_regime_exit_adjustment(regime_label: str | None, base_ticks: int) -> int:
    """Map a cached regime label to a bounded exit-confirmation tick count.

    WHAT: returns the number of consecutive qualifying ticks
    compute_exit_confirmation should require before firing the trailing stop,
    given the current (precomputed, offline) regime label. The result is always
    an int clamped to [REGIME_TICKS_LOWER_BOUND, REGIME_TICKS_UPPER_BOUND].

    ONE-KNOB BUDGET: the regime adjustment moves exactly ONE response knob —
    the exit_confirm_ticks threshold passed to compute_exit_confirmation. No
    other lever (stop distance, MC_BREAKDOWN_THRESHOLD, multipliers) is touched.
    This honours the ≈1-knob budget (00-ADAPTIVE-RECOMMENDATION.md §1A): the
    per-regime tick values are FIXED theory-anchored constants, NOT tuned by
    Optuna, so the adaptive layer adds essentially one bounded degree of freedom.

    WHY (direction, Kaminski-Lo 2014): the sign of serial correlation decides
    whether a stop helps. In a TRENDING regime returns persist, so a stop trigger
    is probably a real reversal -> be MORE-ACTIVE (fewer ticks). In a
    MEAN-REVERTING regime returns reverse, so a trigger is probably noise that
    will bounce back -> show RESTRAINT (more ticks, harder to confirm). A HIGH-VOL
    regime is stressed: keep the stop PROTECTIVE (at or below base) but bounded so
    it cannot become a single-tick hair trigger.

    SAFE DEFAULT (hard constraint 4): None, an empty string, or any label outside
    the three recognized production labels returns base_ticks unchanged — current
    behavior, no adjustment. Matching is exact and case-sensitive: the classifier
    emits lowercase labels ('trending', 'mean-reverting', 'high-vol'); an
    unrecognized or stale label must be treated as absent.

    Pure: no I/O, no side effects, no global state. Reads only its arguments and
    module-level named constants (project rule: no magic numbers in the body).
    """
    target = _REGIME_EXIT_TICKS_TABLE.get(regime_label) if regime_label is not None else None
    if target is None:
        # Unknown / absent / stale label -> safe default: no adjustment.
        return int(base_ticks)
    # Clamp the theory-set target into the safety envelope (named constants only).
    bounded = max(REGIME_TICKS_LOWER_BOUND, min(REGIME_TICKS_UPPER_BOUND, target))
    return int(bounded)


# Take-profit confirmation constant (gates the take-profit trigger)
TP_CONFIRM_TICKS = (
    2  # consecutive above-threshold ticks (with return > 0) needed to confirm a take-profit exit
)


def compute_tp_confirmation(
    mc_available: bool,
    prob_underperforming: float | None,
    take_profit_mc_pct: float,
    current_return: float,
    is_triggered: bool,
    tp_armed: bool,
    above_tp_count: int,
) -> tuple[bool, int, bool]:
    """
    Computes the take-profit confirmation state update.

    Returns (new_tp_armed, new_above_tp_count, is_tp_hit).

    Logic (extracted verbatim from alpha_bot_execution.py Check 2):
      if mc_available and prob_underperforming < take_profit_mc_pct:
          if not tp_armed and not is_triggered:
              -> arm: tp_armed = True, above_tp_count = 0
          else: state unchanged
      elif tp_armed and not is_triggered:
          if mc_available and prob_underperforming >= take_profit_mc_pct:
              above_tp_count += 1
              if above_tp_count >= TP_CONFIRM_TICKS:
                  if current_return > 0:  -> is_tp_hit = True
                  else:                   -> disarm: tp_armed = False, above_tp_count = 0
          else:
              -> above_tp_count = 0   (MC dipped back below, or MC unavailable)

    TP ARM SEMANTIC (rename-only — operator + threshold UNCHANGED): the TP
    "Exceptional Gain" arm fires on prob_underperforming < take_profit_mc_pct.
    Since prob_underperforming is LOW (~0) when the portfolio is OUTPERFORMING
    the analog pool, "underperformance below the TP threshold" correctly reads
    as "we are exceptionally ahead of regime peers — arm the take-profit." H1
    only renamed the metric (value unchanged); this condition's behavior is
    identical to the pre-rename gate (legacy metric name `< take_profit_mc_pct`).

    MC-AVAILABILITY GATE (fail-safe): an absent MC opinion (mc_available
    False) is not an "exceptional gain" signal and cannot arm or confirm a
    take-profit exit. While tp_armed, an MC-UNAVAILABLE tick falls into the
    inner `else` and RESETS above_tp_count — an absent opinion cannot count
    toward a confirmation. A sub-threshold MC dip while MC IS available also
    resets the counter via the same `else` branch (the count is not preserved
    across a dip — production keeps no special-case).

    Pure. No I/O. No state. Caller handles print/log transitions by comparing
    input (tp_armed, above_tp_count) to the returned values.
    """
    if prob_underperforming is not None:
        _reject_non_finite(prob_underperforming=prob_underperforming)
    _reject_non_finite(current_return=current_return)

    is_tp_hit = False
    if mc_available and prob_underperforming < take_profit_mc_pct:
        if not tp_armed and not is_triggered:
            return True, 0, False
        return tp_armed, int(above_tp_count), False
    elif tp_armed and not is_triggered:
        if mc_available and prob_underperforming >= take_profit_mc_pct:
            new_count = int(above_tp_count) + 1
            if new_count >= TP_CONFIRM_TICKS:
                if current_return > 0:
                    is_tp_hit = True
                else:
                    # MC rose to confirm but return non-positive: disarm.
                    return False, 0, False
            return tp_armed, new_count, is_tp_hit
        # MC dipped back below the threshold, or MC unavailable: reset count.
        return tp_armed, 0, False

    # Not in the TP arm band and not armed: state unchanged.
    return tp_armed, int(above_tp_count), False


def compute_vwap_signals(
    holdings: list[dict],
    live_vwaps: dict[str, dict],
) -> tuple[float, float]:
    """
    Computes the allocation-weighted VWAP-deviation signal across holdings.

    Returns (weighted_vwap_diff, valid_vwap_weight).

    For each holding:
      - skip if its ticker is not present in live_vwaps
      - read p = live_vwaps[ticker]["last_price"], v = live_vwaps[ticker]["vwap"]
      - skip if v <= 0 (degenerate vwap)
      - else accumulate:
          weighted_vwap_diff += allocation * (p - v) / v
          valid_vwap_weight  += allocation

    Pure. No I/O. No state. No input mutation. Caller is responsible for
    normalizing each holding's "ticker" field BEFORE calling.

    Extracted from alpha_bot_execution.py:472-484 (cycle 8 of math-layer
    extraction).
    """
    _reject_non_finite_in_records(holdings, "allocation")
    for ticker in live_vwaps:
        entry = live_vwaps[ticker]
        _reject_non_finite(**{k: entry[k] for k in ("last_price", "vwap") if k in entry})
    # Zero-volume tickers have no economically meaningful VWAP and must be
    # excluded from BOTH accumulators. NO_VOLUME is the structural threshold:
    # a session volume at or below it means the ticker did not trade.
    NO_VOLUME = 0
    weighted_vwap_diff = 0.0
    valid_vwap_weight = 0.0
    for h in holdings:
        ticker = h.get("ticker")
        allocation = h.get("allocation", 0.0)
        if ticker in live_vwaps:
            entry = live_vwaps[ticker]
            # Volume is optional in live data (fetch_intraday_vwaps only emits
            # entries with cumulative volume > 0, and omits the key). A MISSING
            # volume key therefore qualifies; only an explicitly present
            # non-positive volume skips the entry.
            volume = entry.get("volume")
            if volume is not None and volume <= NO_VOLUME:
                continue
            p = entry["last_price"]
            v = entry["vwap"]
            if v > 0:
                weighted_vwap_diff += allocation * (
                    (p - v) / v
                )  # (p-v)/v first: preserves inline's IEEE-754 evaluation order
                valid_vwap_weight += allocation
    return float(weighted_vwap_diff), float(valid_vwap_weight)


# VWAP bleed-arm constants (dynamic exit threshold for VWAP-bleed system; always negative)
VWAP_BLEED_ARM_MIN = (
    -3.0
)  # most-negative clamp; deepest bleed threshold allowed (further drops do not arm any sooner)
VWAP_BLEED_ARM_MAX = (
    -0.5
)  # least-negative clamp; arm threshold must be at least this deep (shallower drops never arm)


def compute_vwap_bleed_arm_threshold(
    symphony_vol: float,
    bleed_multiplier: float,
) -> float:
    """
    Returns the dynamic VWAP-bleed arm threshold (in percentage points,
    always negative — bleeding means dropping below zero).

    Computation (extracted verbatim from alpha_bot_execution.py):
      raw = -(symphony_vol * bleed_multiplier)
      result = max(VWAP_BLEED_ARM_MIN, min(VWAP_BLEED_ARM_MAX, raw))

    Interpretation:
      - High vol × high multiplier produces a more-negative raw, clamped at
        VWAP_BLEED_ARM_MIN (most permissive arm — current_return must drop
        deeper to trigger bleed counter).
      - Low vol produces a near-zero raw, clamped at VWAP_BLEED_ARM_MAX
        (most cautious — shallower drops can arm).

    Pure. No I/O. No state.

    Extracted from alpha_bot_execution.py:525-526 (cycle 9 of math-layer
    extraction).
    """
    _reject_non_finite(symphony_vol=symphony_vol, bleed_multiplier=bleed_multiplier)
    raw = -(symphony_vol * bleed_multiplier)
    return float(max(VWAP_BLEED_ARM_MIN, min(VWAP_BLEED_ARM_MAX, raw)))


# VWAP breakdown constants (gates the VWAP exit state machine)
VWAP_WEIGHT_THRESHOLD = 0.5  # minimum allocation coverage to evaluate VWAP signals; below this, the weighted diff is too unreliable
VWAP_BREAK_CONFIRM_TICKS = (
    3  # consecutive qualifying ticks for System A (profit-protection break) to flip is_vwap_broken
)


def compute_vwap_breakdown_update(
    is_triggered: bool,
    valid_vwap_weight: float,
    weighted_vwap_diff: float,
    safe_hwm: float,
    current_return: float,
    vwap_cross_hwm_pct: float,
    vwap_bleed_arm_pct: float,
    vwap_bleed_ticks_threshold: int,
    current_vwap_ticks: int,
    current_vwap_bleed_ticks: int,
) -> tuple[int, int, bool, bool]:
    """
    Computes the VWAP-breakdown state machine update.

    Returns (new_vwap_ticks, new_vwap_bleed_ticks, is_vwap_broken,
    is_vwap_bleed_broken).

    BRANCH 1 — is_triggered guard (the ONLY shared short-circuit):
        State preserved unchanged. No signals. This is the only condition that
        gates System B; once past it, the two systems are fully independent.
        Returns (current_vwap_ticks, current_vwap_bleed_ticks, False, False)

    SYSTEM B (bleed) — evaluated FIRST and UNCONDITIONALLY (H2 decouple):
        System B keys ONLY on the symphony return; it references NEITHER the
        VWAP sign NOR coverage, so it is NOT behind the System-A gate.
            Condition: current_return <= vwap_bleed_arm_pct
            Met:  new_vwap_bleed_ticks = current_vwap_bleed_ticks + 1
                  is_vwap_bleed_broken = (new_vwap_bleed_ticks >= vwap_bleed_ticks_threshold)
            Miss: new_vwap_bleed_ticks = 0  (System B's OWN reset — a bounce
                  ABOVE the bleed-arm stops the ladder; it is not a one-way ratchet)
        H2 FIX: pre-fix the System-A gate below wiped this counter on a VWAP
        bounce (weighted_vwap_diff >= 0) or low coverage (weight <= threshold),
        resetting a real bleed ladder for reasons unrelated to System B's arm.
        System B now survives those — it resets ONLY when its own arm misses.

    SYSTEM A (profit-protection break) — keeps its OWN gate (System-A-only):
        Gate (System-A-only now): valid_vwap_weight > VWAP_WEIGHT_THRESHOLD
              AND weighted_vwap_diff < 0
            Gate miss: new_vwap_ticks = 0, is_vwap_broken = False (System A
                       resets on its OWN gate miss — UNCHANGED from pre-fix).
            Gate pass: Condition: safe_hwm >= vwap_cross_hwm_pct
                       AND current_return < safe_hwm
                Met:  new_vwap_ticks = current_vwap_ticks + 1
                      is_vwap_broken = (new_vwap_ticks >= VWAP_BREAK_CONFIRM_TICKS)
                Miss: new_vwap_ticks = 0

    Boundary semantics (each pinned by a fixture):
      - Gate weight uses strict `>` (0.5 exact does NOT pass)
      - Gate diff uses strict `<` (0 exact does NOT pass)
      - System A safe_hwm uses `>=` (cross exact DOES arm)
      - System A current_return uses strict `<` (safe_hwm exact does NOT trigger break)
      - System B current_return uses `<=` (bleed_arm exact DOES trigger)

    Pure. No I/O. No state. Caller handles print transitions.

    Extracted from alpha_bot_execution.py:641-667 (cycle 10 of math-layer
    extraction).
    """
    _reject_non_finite(
        valid_vwap_weight=valid_vwap_weight,
        weighted_vwap_diff=weighted_vwap_diff,
        safe_hwm=safe_hwm,
        current_return=current_return,
        vwap_cross_hwm_pct=vwap_cross_hwm_pct,
        vwap_bleed_arm_pct=vwap_bleed_arm_pct,
    )
    if is_triggered:
        return int(current_vwap_ticks), int(current_vwap_bleed_ticks), False, False

    # System B — bleed (H2: decoupled from the System-A gate). Evaluated FIRST
    # and unconditionally (only the is_triggered short-circuit above gates it).
    # Keys ONLY on the symphony return falling to/below the dynamic bleed-arm
    # threshold — references NEITHER VWAP sign NOR coverage. Pre-fix this counter
    # sat behind the System-A coverage+sign gate and was wiped on a VWAP bounce
    # or low coverage, resetting a real bleed ladder for unrelated reasons.
    if current_return <= vwap_bleed_arm_pct:
        new_vwap_bleed_ticks = int(current_vwap_bleed_ticks) + 1
        is_vwap_bleed_broken = bool(new_vwap_bleed_ticks >= vwap_bleed_ticks_threshold)
    else:
        # System B's OWN reset: the bleed stopped (return rose back above the
        # bleed-arm). Not a one-way ratchet — resets independently of System A.
        new_vwap_bleed_ticks = 0
        is_vwap_bleed_broken = False

    # System A — profit-protection break. Keeps its OWN coverage+sign gate; a
    # gate miss resets ONLY the System-A counter (System B already handled above).
    if not (valid_vwap_weight > VWAP_WEIGHT_THRESHOLD and weighted_vwap_diff < 0):
        return 0, new_vwap_bleed_ticks, False, is_vwap_bleed_broken

    # System A — profit-protection break (gate passed).
    # PROVENANCE: the gate `safe_hwm >= vwap_cross_hwm_pct` is the regime
    # boundary of a two-regime trailing-stop system. Regime 1 (below gate):
    # primary trailing-stop only (compute_active_trailing_stop with
    # time-squeeze decay). Regime 2 (at-or-above gate): primary stop OR
    # VWAP-cross profit-protection (System A) — adds the System-A break
    # tick counter once HWM has banked enough to justify a tighter exit
    # policy. The structural choice of a regime switch is justified by the
    # optimal-stopping formalism for trailing stops with running maxima
    # (Leung & Zhang, 2019, "Optimal Trading with a Trailing Stop,"
    # Applied Mathematics & Optimization 80, 669-698,
    # DOI: 10.1007/s00245-019-09559-0). The maximality principle (Peskir,
    # 1998, Annals of Probability 26(4), 1614-1640) characterizes the
    # optimal trailing boundary as the maximal solution to a first-order
    # nonlinear ODE; the regime-switch construction is a discretization of
    # this boundary into inactive/active regions, with the boundary location
    # (vwap_cross_hwm_pct) remaining an Optuna-searched parameter within the
    # BHY haircut surface. Research note:
    # docs/research/m3-provenance/literature-pass.md §2.
    # Honest-flag (risk-m3): the regime-switch discretization above is an
    # interpretive extension of Peskir 1998's continuous-boundary result,
    # NOT a formally proven theorem in Peskir 1998 itself. The gate remains
    # the best available THEORY anchor under NN1; empirical performance +
    # freeze_discipline = THEORY are the binding operator guarantees.
    if safe_hwm >= vwap_cross_hwm_pct and current_return < safe_hwm:
        new_vwap_ticks = int(current_vwap_ticks) + 1
        is_vwap_broken = bool(new_vwap_ticks >= VWAP_BREAK_CONFIRM_TICKS)
    else:
        new_vwap_ticks = 0
        is_vwap_broken = False

    # System B (new_vwap_bleed_ticks / is_vwap_bleed_broken) was computed above,
    # decoupled from the System-A gate.
    return new_vwap_ticks, new_vwap_bleed_ticks, is_vwap_broken, is_vwap_bleed_broken


def is_in_open_window_grace(
    current_et,
    execution_start_hhmm: str,
    grace_minutes: int,
) -> bool:
    """
    Returns True iff the supplied instant falls in
    [exec_start, exec_start + grace_minutes), where exec_start is
    execution_start_hhmm interpreted in US/Eastern wall-clock.

    The instant MUST be a timezone-aware datetime — a naive datetime raises
    ValueError. The grace window is defined in ET wall-clock; the instant is
    converted to ET before comparison, so a caller passing the same physical
    instant in any timezone (e.g. UTC) gets the same answer.

    Pure function — no I/O, no state. Returns False before exec_start
    (pre-action-gate territory handled by the existing action gate).
    """
    import datetime as _dt
    from zoneinfo import ZoneInfo

    if current_et.tzinfo is None or current_et.tzinfo.utcoffset(current_et) is None:
        raise ValueError(
            "is_in_open_window_grace requires a timezone-aware datetime; "
            "a naive datetime cannot be interpreted as an ET wall-clock time"
        )

    et = current_et.astimezone(ZoneInfo("America/New_York"))
    h, m = map(int, execution_start_hhmm.split(":"))
    exec_start_dt = et.replace(hour=h, minute=m, second=0, microsecond=0)
    grace_end_dt = exec_start_dt + _dt.timedelta(minutes=grace_minutes)
    return exec_start_dt <= et < grace_end_dt


# Canonical priority order: VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop.
# Order matches H2 acceptance criteria (alpha_bot_execution.py:1081 comment) and the math audit.
_TRIGGER_PRIORITY_ORDER: list[str] = [
    "VWAP Breakdown",
    "Take-Profit",
    "VWAP Bleed Cut",
    "Trailing Stop",
]


def resolve_trigger_priority(
    is_vwap_broken: bool,
    is_tp_hit: bool,
    is_vwap_bleed_broken: bool,
    is_trailing_stop_hit: bool,
) -> tuple[str | None, list[str]]:
    """Resolve which trigger fired and which co-fired, in canonical priority order.

    Returns (None, []) when no flag is True.
    Returns (winner, []) for a single trigger.
    Returns (winner, [co-fired, ...]) when multiple flags are True.

    Pure function — no I/O, no side effects.
    """
    flag_map: dict[str, bool] = {
        "VWAP Breakdown": is_vwap_broken,
        "Take-Profit": is_tp_hit,
        "VWAP Bleed Cut": is_vwap_bleed_broken,
        "Trailing Stop": is_trailing_stop_hit,
    }
    fired = [name for name in _TRIGGER_PRIORITY_ORDER if flag_map[name]]
    if not fired:
        return None, []
    return fired[0], fired[1:]


def derive_cycle_mc_seed(cycle_id: str) -> int:
    """Deterministic seed for a given cycle_id (YYYYMMDD_HHMM format).

    Pure function — no I/O, no global state. Safe across daemon restarts.
    Same cycle_id always produces the same seed (SHA-256 digest reduced into
    the 64-bit MC_SEED_MODULUS space).
    """
    return int(hashlib.sha256(cycle_id.encode()).hexdigest(), 16) % MC_SEED_MODULUS


# Bounded LRU cache for _sorted_dates, keyed by id(historical_data).
# Planet Stopper is a long-running daemon constructing a fresh historical_data
# dict every minute during market hours (~390 dicts/day). An unbounded
# strong-ref cache would leak ~1 MB x 390 min/day = ~400 MB/day.
# OrderedDict-based LRU caps memory to _SORTED_DATES_CACHE_MAXSIZE entries
# (~8 MB ceiling). Maxsize is chosen to be >= 8 so one full symphony cycle
# (up to 5 consumers + 3 headroom for multi-symphony overlap) never evicts
# a hot entry mid-cycle.
# Each entry is (object_ref, sorted_list). object_ref lets us detect
# CPython id reuse (rare: only if a prior dict is GC'd and a new one
# allocated at the same address before the eviction runs).
_SORTED_DATES_CACHE_MAXSIZE = 32  # >= 8 (per-cycle hot path), << 64 (memory ceiling)
_sorted_dates_cache: OrderedDict = OrderedDict()


def _sorted_dates(historical_data: dict) -> list[str]:
    """Return sorted(historical_data.keys()) memoised by object identity.

    Keying by id(historical_data) means:
    - Repeated calls with the SAME dict object (one symphony cycle) pay
      O(1) — the sorted list is returned from cache without re-sorting.
    - A new dict object — even one with identical keys — always recomputes,
      so no stale results can leak across cycles (id-keyed, not value-keyed).

    Eviction: LRU with maxsize _SORTED_DATES_CACHE_MAXSIZE. Ensures the
    daemon's memory footprint stays bounded over multi-day runs.

    Cache validity: each entry stores the dict alongside the sorted list.
    On lookup, `stored_dict is historical_data` guards against CPython id
    reuse — if the ids match but the objects differ, the stale entry is
    evicted and recomputed.

    Returns a list (not a tuple) because consumers slice it:
    `valid_dates[-LOOKBACK_DAYS:]` etc.
    """
    id_ = id(historical_data)
    entry = _sorted_dates_cache.get(id_)
    if entry is not None:
        stored_dict, sorted_list = entry
        if stored_dict is historical_data:
            _sorted_dates_cache.move_to_end(id_)  # LRU bump — mark as recently used
            return sorted_list
        # id reuse after GC — fall through to recompute and overwrite

    sorted_list = sorted(historical_data.keys())
    _sorted_dates_cache[id_] = (historical_data, sorted_list)
    if len(_sorted_dates_cache) > _SORTED_DATES_CACHE_MAXSIZE:
        _sorted_dates_cache.popitem(last=False)  # evict least-recently-used entry
    return sorted_list


def _compute_rolling_spy_vol(spy_returns: np.ndarray) -> np.ndarray:
    """Vectorized rolling population-std over spy_returns.

    Replaces the Python for-loop in run_monte_carlo (PERF-001). Also
    reusable by compute_portfolio_cvar (~line 1344) and
    compute_regime_match_quality (~line 1557) which carry identical loops.

    Contract:
      - Output is same length as input.
      - spy_vols[0] == 0.0 (pinned; matches the pre-PERF-001 production
        behaviour where i==0 sets 0.0 unconditionally).
      - Uses ddof=0 (population std) to match np.std default.
      - Bit-exact with the reference Python loop for the full-window phase
        (indices >= MC_VOL_WINDOW_DAYS - 1), which is the only phase that
        feeds the kNN candidate pool in run_monte_carlo.
      - Growing-window phase (indices 1..MC_VOL_WINDOW_DAYS-2) matches the
        reference to within 1e-15 via cumsum arithmetic.
      - Empty and 1-element inputs are handled without raising.
      - Pure: input array is never mutated.
    """
    from numpy.lib.stride_tricks import as_strided

    n = len(spy_returns)
    if n == 0:
        return np.array([], dtype=float)

    w = MC_VOL_WINDOW_DAYS
    result = np.zeros(n, dtype=float)

    # Full-window phase: indices [w-1 .. n-1].
    # as_strided produces contiguous windows; np.std(axis=1, ddof=0) is
    # bit-exact with np.std(spy_returns[i-w+1:i+1]) for each i.
    # ddof=0 (population std) is INTENTIONAL: consistent across all vol estimators
    # + matches the walk-forward-tuned stop calibration + the PERF-001 ddof=0
    # contract; ddof=1 is deferred to a dedicated re-tune effort (audit M1, policy
    # not bug).
    full_start = w - 1
    if n > full_start:
        full_n = n - full_start
        s = spy_returns.strides[0]
        windows = as_strided(spy_returns, shape=(full_n, w), strides=(s, s))
        result[full_start:] = np.std(windows, axis=1, ddof=0)

    # Growing-window phase: indices [1 .. w-2].
    # These fall outside the kNN candidate pool (run_monte_carlo excludes the
    # first w-1 days), but compute_portfolio_cvar / compute_regime_match_quality
    # use the full array. Computed via cumsum: std(x[0:k])^2 = E[x^2] - E[x]^2.
    # Matches the reference to within float64 rounding (< 1e-15).
    grow_end = min(w - 1, n)  # indices 1..grow_end-1 (exclusive of full_start)
    if grow_end > 1:
        cs = np.cumsum(spy_returns[:grow_end])
        cs2 = np.cumsum(spy_returns[:grow_end] ** 2)
        k_vals = np.arange(2, grow_end + 1, dtype=float)
        means = cs[1:] / k_vals
        var = cs2[1:] / k_vals - means**2
        result[1:grow_end] = np.sqrt(np.maximum(var, 0.0))

    return result


def run_monte_carlo(
    holdings,
    historical_data,
    spy_today_return,
    simulation_paths=MC_DEFAULT_SIMULATION_PATHS,
    neighbor_k=MC_DEFAULT_NEIGHBOR_K,
    seed: int | None = None,
):
    """
    Vectorized Monte Carlo simulation using Nearest Neighbors matching.
    """
    _reject_non_finite(spy_today_return=spy_today_return)
    for day_data in historical_data.values():
        for ticker_data in day_data.values():
            _reject_non_finite_in_records([ticker_data], "daily_ret")
    for h in holdings:
        _reject_non_finite(
            last_percent_change=h.get("last_percent_change"),
            allocation=h.get("allocation"),
        )
    current_symphony_return = sum(
        (h.get("last_percent_change", 0.0) * PCT_SCALAR) * h.get("allocation", 0.0)
        for h in holdings
        if h.get("last_percent_change") is not None
    )
    valid_dates = _sorted_dates(historical_data)
    # Sufficiency is judged on the ELIGIBLE kNN pool, not the raw history: the
    # first MC_VOL_WINDOW_DAYS - 1 early-window days are excluded below (their
    # rolling vol is short-sample biased), so a raw history needs at least
    # MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1) days to leave a
    # non-degenerate eligible pool. A raw-sufficient but eligible-insufficient
    # history collapses into the SAME insufficient path (one sentinel, one
    # fail-safe path) — never a degenerate 1-neighbour kNN.
    eligible_days = len(valid_dates) - (MC_VOL_WINDOW_DAYS - 1)
    if eligible_days < MC_MIN_HISTORY_DAYS:
        # Insufficient MC history: emit the out-of-band sentinel (None), never
        # an in-band probability. The caller (compute_exit_confirmation) treats
        # this as "MC confirmation unavailable" and does NOT veto the
        # protective stop — fail-safe.
        return MC_INSUFFICIENT_HISTORY_SENTINEL

    # 1. Calculate distances based on SPY return and rolling 20-day volatility
    spy_returns = np.array(
        [historical_data[date].get("SPY", {}).get("daily_ret", 0.0) for date in valid_dates]
    )

    spy_vols = _compute_rolling_spy_vol(spy_returns)

    spy_today_ret_dec = spy_today_return / PCT_SCALAR
    # Invariant: len(spy_returns) > MC_VOL_WINDOW_DAYS - 1 because the eligible-
    # days guard above returns early unless eligible_days >= MC_MIN_HISTORY_DAYS,
    # i.e. len(valid_dates) >= MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1).
    today_vol = np.std(np.append(spy_returns[-(MC_VOL_WINDOW_DAYS - 1) :], spy_today_ret_dec))

    # Early-window exclusion: the first MC_VOL_WINDOW_DAYS - 1 days have their
    # rolling vol computed on a short, downward-biased sample (and spy_vols[0]
    # is a hard-set 0.0). today_vol always uses a full window, so admitting
    # these days lets them be mis-selected as artificially low-vol neighbours.
    # Exclude them from the kNN candidate pool. The eligible-days guard above
    # ensures the resulting pool holds at least MC_MIN_HISTORY_DAYS candidates.
    candidate_idx = np.arange(MC_VOL_WINDOW_DAYS - 1, len(spy_returns))
    cand_returns = spy_returns[candidate_idx]
    cand_vols = spy_vols[candidate_idx]

    # Standardize both kNN features with window-fitted (candidate-pool) z-score
    # params, transforming today's query point with the SAME params, so neither
    # feature dominates the Euclidean distance by a unit artifact. A zero-std
    # feature (no spread to standardize) collapses to 0.0 — guarded against the
    # 0/0 divide so the output stays finite.
    # ddof=0 (population std) is INTENTIONAL: consistent across all vol estimators
    # + matches the walk-forward-tuned stop calibration + the PERF-001 ddof=0
    # contract; ddof=1 is deferred to a dedicated re-tune effort (audit M1, policy
    # not bug).
    ret_mean, ret_std = float(np.mean(cand_returns)), float(np.std(cand_returns))
    vol_mean, vol_std = float(np.mean(cand_vols)), float(np.std(cand_vols))

    def _z(values, mean, std):
        if std == 0.0:
            return np.zeros_like(values, dtype=float)
        return (values - mean) / std

    cand_returns_z = _z(cand_returns, ret_mean, ret_std)
    cand_vols_z = _z(cand_vols, vol_mean, vol_std)
    today_ret_z = 0.0 if ret_std == 0.0 else (spy_today_ret_dec - ret_mean) / ret_std
    today_vol_z = 0.0 if vol_std == 0.0 else (today_vol - vol_mean) / vol_std

    # Euclidean distance across 2 standardized dimensions
    distances = np.sqrt((cand_returns_z - today_ret_z) ** 2 + (cand_vols_z - today_vol_z) ** 2)

    # 2. Get top K indices (into the candidate pool)
    if len(distances) <= neighbor_k:
        nearest_pool_indices = np.arange(len(distances))
    else:
        # argpartition is faster than full sort
        nearest_pool_indices = np.argpartition(distances, neighbor_k)[:neighbor_k]

    nearest_days = [valid_dates[candidate_idx[i]] for i in nearest_pool_indices]

    # 3. Weights and Tickers
    tickers = [h["ticker"] for h in holdings]
    weights = np.array([h.get("allocation", 0.0) for h in holdings])

    # 4. Build Returns Matrix (K days x N tickers)
    returns_matrix = np.zeros((len(nearest_days), len(tickers)))

    for i, date in enumerate(nearest_days):
        day_data = historical_data[date]
        spy_ret = day_data.get("SPY", {}).get("daily_ret", 0.0)
        for j, ticker in enumerate(tickers):
            if ticker in day_data:
                returns_matrix[i, j] = day_data[ticker].get("daily_ret", 0.0)
            else:
                returns_matrix[i, j] = spy_ret

    # 5. Calculate path returns (dot product is highly optimized in numpy)
    nearest_day_returns = returns_matrix.dot(weights) * PCT_SCALAR

    # 6. Random selection & Cumulative Distribution
    # Isolated Generator — does NOT touch the numpy global RNG (AC-H3.1).
    rng = np.random.default_rng(seed)
    sim_results = rng.choice(nearest_day_returns, size=simulation_paths)

    sim_results.sort()
    below_count = np.searchsorted(sim_results, current_symphony_return)
    return ((simulation_paths - below_count) / simulation_paths) * PCT_SCALAR


def calculate_20d_vol(holdings, historical_data):
    """
    Calculates the 20-day historical volatility of the given holdings based on historical_data.
    Vectorized for performance.
    """
    _reject_non_finite_in_records(holdings, "allocation")
    for day_data in historical_data.values():
        for ticker_data in day_data.values():
            _reject_non_finite_in_records([ticker_data], "daily_ret")
    valid_dates = _sorted_dates(historical_data)[-LOOKBACK_DAYS:]
    if len(valid_dates) < LOOKBACK_DAYS:
        return 0.0

    tickers = [h.get("ticker") for h in holdings]
    weights = np.array([h.get("allocation", 0.0) for h in holdings])

    returns_matrix = np.zeros((len(valid_dates), len(tickers)))

    for i, date in enumerate(valid_dates):
        day_data = historical_data[date]
        spy_ret = day_data.get("SPY", {}).get("daily_ret", 0.0)
        for j, ticker in enumerate(tickers):
            if ticker in day_data:
                returns_matrix[i, j] = day_data[ticker].get("daily_ret", 0.0)
            else:
                returns_matrix[i, j] = spy_ret

    daily_returns = returns_matrix.dot(weights) * PCT_SCALAR

    if len(daily_returns) == 0:
        return 0.0

    # ddof=0 (population std) is INTENTIONAL: consistent across all vol estimators
    # + matches the walk-forward-tuned stop calibration + the PERF-001 ddof=0
    # contract; ddof=1 is deferred to a dedicated re-tune effort (audit M1, policy
    # not bug).
    return float(np.std(daily_returns))


def calculate_14d_atr_pct(holdings, historical_data):
    """
    Calculates the 14-day Volatility-Adjusted (ATR) percentage for the holdings.
    Falls back to calculate_20d_vol if high/low data is missing.
    """
    _reject_non_finite_in_records(holdings, "allocation")
    for day_data in historical_data.values():
        for ticker_data in day_data.values():
            _reject_non_finite_in_records([ticker_data], "high", "low", "close")
    valid_dates = _sorted_dates(historical_data)[-ATR_LOOKBACK_DAYS:]
    if len(valid_dates) < ATR_LOOKBACK_DAYS:
        return calculate_20d_vol(holdings, historical_data)

    tickers = [h.get("ticker") for h in holdings]
    weights = np.array([h.get("allocation", 0.0) for h in holdings])

    atr_pct_array = np.zeros(len(tickers))

    for j, ticker in enumerate(tickers):
        tr_list = []
        last_close = None
        has_missing_data = False

        for date in valid_dates:
            day_data = historical_data[date].get(ticker)
            if (
                not day_data
                or "high" not in day_data
                or "low" not in day_data
                or "close" not in day_data
            ):
                has_missing_data = True
                break

            high = day_data["high"]
            low = day_data["low"]
            close = day_data["close"]

            if last_close is not None:
                tr = max(high - low, abs(high - last_close), abs(low - last_close))
                tr_list.append(tr)
            last_close = close

        if has_missing_data or len(tr_list) == 0:
            return calculate_20d_vol(holdings, historical_data)

        avg_tr = np.mean(tr_list)
        recent_close = last_close
        if recent_close and recent_close > 0:
            atr_pct_array[j] = (avg_tr / recent_close) * PCT_SCALAR
        else:
            return calculate_20d_vol(holdings, historical_data)

    portfolio_atr_pct = atr_pct_array.dot(weights)
    return float(portfolio_atr_pct)


@dataclasses.dataclass(frozen=True)
class CVaREstimate:
    """Result type for compute_cvar_5pct_general_distribution (M2 H-2 / R-U estimator).

    Distinct from CVaRAssessment: carries .stderr (H-2 binding) and is the return
    type of the pure-math kNN-pool estimator. CVaRAssessment is the typed result
    for the kNN historical regime-match (Phase-1; the forward-path co-signal was REJECTED
    per decision-science council — see docs/audit/vision-audit-2026-05-27/SYNTHESIS.md CVaR-divergence wall).

    cvar_pct: Rockafellar-Uryasev general-distribution CVaR. None when the pool
              is empty or has fewer than CVAR_MIN_TAIL_OBS genuine tail observations.
    tail_obs_count: distinct tail observations used:
                    tail_obs_count = floor(alpha*N) + (1 if fractional_weight > 0 else 0)
                    (k_below + 1 if atom contributes, else k_below). This is the
                    H-2 stderr denominator — NOT the resample count. Auditable via
                    the persisted cvar_n_tail column.
    stderr: std(tail_values, ddof=1)/sqrt(tail_obs_count). None when sentinel.
    insufficient_reason: human-readable explanation when cvar_pct is None.
    """

    cvar_pct: "float | None"
    tail_obs_count: int
    stderr: "float | None"
    insufficient_reason: "str | None"


def compute_cvar_stderr_distinct_tail(
    returns: list,
    alpha: float = CVAR_ALPHA_DEFAULT,
) -> "float | None":
    """Compute CVaR stderr using the DISTINCT GENUINE tail observation count.

    H-2 binding: the denominator is the number of real tail observations (k_below
    if fractional_weight=0, else k_below+1 including the atom). This is never the
    resample count (5000), which would understate the stderr by sqrt(5000/k)≈25x.

    Returns None when the pool is empty or has fewer than CVAR_MIN_TAIL_OBS tail obs.
    Raises ValueError on non-finite inputs (A-2 closure).

    Source: decision-science-council-synthesis.md H-2; plan §Deliverables Code.
    """
    if not returns:
        return None
    # A-2 closure: reject non-finite before any arithmetic
    for v in returns:
        if not math.isfinite(v):
            raise ValueError(
                f"NaN input not allowed: value={v!r} in returns passed to "
                "compute_cvar_stderr_distinct_tail"
            )
    sorted_pool = sorted(returns)
    n = len(sorted_pool)
    k = int(alpha * n)  # floor(alpha * N) — number of strictly-below-VaR values
    fractional_weight = alpha * n - k  # how much of the VaR atom contributes

    if k < CVAR_MIN_TAIL_OBS:
        return None

    # Distinct tail obs: k below-VaR values + 1 if atom has non-zero weight
    n_tail_distinct = k + (1 if fractional_weight > 0 else 0)

    tail_values = sorted_pool[: k + (1 if fractional_weight > 0 else 0)]

    if len(tail_values) < 2:
        # ddof=1 requires at least 2 observations; return None (sentinel).
        return None

    mean_tail = sum(tail_values) / len(tail_values)
    variance = sum((v - mean_tail) ** 2 for v in tail_values) / (len(tail_values) - 1)
    if variance == 0.0:
        # Degenerate pool: all tail values identical; stderr undefined.
        return None
    std_tail = math.sqrt(variance)
    return std_tail / math.sqrt(n_tail_distinct)


def compute_cvar_5pct_general_distribution(
    returns: list,
    alpha: float = CVAR_ALPHA_DEFAULT,
) -> CVaREstimate:
    """Rockafellar-Uryasev (2002) general-distribution CVaR on a discrete pool.

    Implements the R-U general-distribution formula with correct atom handling:
      CVaR = (1/alpha) * (1/N) * (sum_below + fractional_weight * VaR)
    where:
      k                = floor(alpha * N)         — count strictly below VaR
      VaR              = sorted_pool[k]            — alpha-quantile atom
      fractional_weight = alpha*N - k             — atom partial weight [0, 1)
      sum_below        = sum(sorted_pool[:k])

    The naive estimator (mean of k strictly-below-VaR values) omits the atom
    contribution, producing a ~4% upward bias in the N=150 fixture.

    stderr uses the DISTINCT GENUINE tail count as denominator (H-2 binding).
    Canonical formula (Acerbi-Tasche atom-contribution discipline):
      tail_obs_count = floor(alpha*N) + (1 if fractional_weight > 0 else 0)

    Returns CVaREstimate with cvar_pct=None (sentinel) when:
      - pool is empty
      - n_tail_distinct < CVAR_MIN_TAIL_OBS

    Raises ValueError on non-finite inputs (A-2 closure).

    Source: Rockafellar & Uryasev (2002), Optimization of Conditional Value-at-Risk;
            Acerbi & Tasche (2002), On the coherence of expected shortfall — atom-contribution discipline;
            decision-science-council-synthesis.md §2.6; plan §Deliverables Code.
    """
    if not returns:
        return CVaREstimate(
            cvar_pct=None,
            tail_obs_count=0,
            stderr=None,
            insufficient_reason="Empty pool: no return observations to estimate CVaR.",
        )

    # A-2 closure: reject non-finite before sort or arithmetic
    for v in returns:
        if not math.isfinite(v):
            raise ValueError(
                f"NaN input not allowed: value={v!r} in returns passed to "
                "compute_cvar_5pct_general_distribution"
            )

    sorted_pool = sorted(returns)
    n = len(sorted_pool)
    k = int(alpha * n)  # floor(alpha * N)
    fractional_weight = alpha * n - k  # atom partial weight

    # Sentinel guard: k=0 means no strictly-below-VaR values exist.
    # A pool with no genuine below-VaR entries cannot produce a meaningful CVaR
    # (the VaR atom alone is all positive returns — no tail severity to estimate).
    # CVAR_MIN_TAIL_OBS guards on k, not on k + atom — consistent with the
    # "data-starved" operator signal: we need at least one strictly-below-VaR observation.
    if k < CVAR_MIN_TAIL_OBS:
        return CVaREstimate(
            cvar_pct=None,
            tail_obs_count=0,
            stderr=None,
            insufficient_reason=(
                f"Insufficient tail observations: k={k} strictly-below-VaR entries "
                f"< CVAR_MIN_TAIL_OBS ({CVAR_MIN_TAIL_OBS}). "
                "Pool has no genuine sub-alpha-quantile entries."
            ),
        )

    # Distinct tail obs count (H-2 denominator): strictly-below + atom if it contributes.
    n_tail_distinct = k + (1 if fractional_weight > 0 else 0)

    var_atom = sorted_pool[k]  # alpha-quantile atom (VaR)
    sum_below = sum(sorted_pool[:k])

    # H-2 stderr on distinct tail count (never the resample count)
    tail_values = sorted_pool[: k + (1 if fractional_weight > 0 else 0)]

    if len(tail_values) < 2:
        # Single-observation tail: std undefined; return sentinel.
        return CVaREstimate(
            cvar_pct=None,
            tail_obs_count=0,
            stderr=None,
            insufficient_reason=(
                f"Degenerate tail: only {len(tail_values)} tail observation(s); "
                "stderr requires at least 2 distinct observations."
            ),
        )

    mean_tail = sum(tail_values) / len(tail_values)
    variance = sum((v - mean_tail) ** 2 for v in tail_values) / (len(tail_values) - 1)

    if variance == 0.0:
        # All tail observations are identical — degenerate pool. The CVaR is the common
        # value but stderr = 0, violating the sentinel discipline (stderr must be positive
        # for a non-sentinel estimate). Return sentinel so the display layer skips.
        return CVaREstimate(
            cvar_pct=None,
            tail_obs_count=0,
            stderr=None,
            insufficient_reason=(
                "Degenerate tail: all tail observations are identical "
                "(std=0); stderr undefined for this pool."
            ),
        )

    # R-U general-distribution formula
    cvar_val = (1.0 / alpha) * (1.0 / n) * (sum_below + fractional_weight * var_atom)
    stderr_val = math.sqrt(variance) / math.sqrt(n_tail_distinct)

    return CVaREstimate(
        cvar_pct=cvar_val,
        tail_obs_count=n_tail_distinct,
        stderr=stderr_val,
        insufficient_reason=None,
    )


def compute_portfolio_cvar(
    cycle_id: str,
    holdings: list,
    historical_data: dict,
    spy_today_return: float,
    simulation_paths: int = MC_DEFAULT_SIMULATION_PATHS,
    neighbor_k: int = MC_DEFAULT_NEIGHBOR_K,
    *,
    mode: "str | None" = None,
) -> "CVaRAssessment":
    """M2 Phase-1 CVaR diagnostic — 5th-percentile expected shortfall.

    Computes the CVaR (Expected Shortfall) at CVAR_TAIL_PCT (5%) for the
    portfolio using the same kNN regime-matching pool as run_monte_carlo.
    The seed is exclusively derive_cycle_mc_seed(cycle_id) fed into an
    isolated np.random.default_rng — never the global RNG (plan risk R3).

    Phase-1 anchor: this is the SINGLE replay-determinism anchor (§3.7).
    Same (cycle_id, holdings, historical_data) → bit-identical cvar_pct and
    tail_obs_count across independent calls (Gate-1 / §3.8).

    mode: when "live" or "replay", persists the row via database.record_cvar_diagnostic
          (the H4 helper — never a raw INSERT). When None, computes and returns
          only (determinism tests omit mode to stay DB-free). The mode parameter
          has no default at the H4 helper call site; callers in production always
          pass it explicitly (plan risk R4 / arch constraint 4).

    Returns CVaRAssessment. Zero broker-order symbols in this function body
    or its call-chain (rubric M-2 / live-mode-audit.csv M2 rows = 0).
    """
    _reject_non_finite(spy_today_return=spy_today_return)
    for day_data in historical_data.values():
        for ticker_data in day_data.values():
            _reject_non_finite_in_records([ticker_data], "daily_ret")
    for h in holdings:
        _reject_non_finite(
            last_percent_change=h.get("last_percent_change", 0.0),
            allocation=h.get("allocation", 0.0),
        )

    valid_dates = _sorted_dates(historical_data)
    # Eligible pool mirrors run_monte_carlo: first MC_VOL_WINDOW_DAYS - 1 days
    # are excluded (short-sample vol bias), so sufficiency is checked on the
    # eligible count, not the raw history count.
    eligible_days = len(valid_dates) - (MC_VOL_WINDOW_DAYS - 1)
    if eligible_days < MC_MIN_HISTORY_DAYS:
        result = CVaRAssessment(
            cvar_pct=None,
            breach=False,
            tail_obs_count=0,
            stderr=None,
            insufficient_reason=(
                f"Insufficient history: {eligible_days} eligible days "
                f"< MC_MIN_HISTORY_DAYS ({MC_MIN_HISTORY_DAYS}). "
                "CVaR estimate unavailable."
            ),
        )
        if mode is not None:
            import database as _db  # lazy import — math_engine is pure-math at module level

            _db.record_cvar_diagnostic(
                cycle_id=cycle_id,
                symphony_id="",
                cvar_5pct=None,
                cvar_5pct_stderr=None,
                cvar_n_tail=0,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode=mode,
            )
        return result

    # 1. SPY return and rolling-vol arrays (identical to run_monte_carlo)
    spy_returns = np.array(
        [historical_data[date].get("SPY", {}).get("daily_ret", 0.0) for date in valid_dates]
    )
    spy_vols = _compute_rolling_spy_vol(spy_returns)

    spy_today_ret_dec = spy_today_return / PCT_SCALAR
    today_vol = np.std(np.append(spy_returns[-(MC_VOL_WINDOW_DAYS - 1) :], spy_today_ret_dec))

    # Exclude early-window candidates (same logic as run_monte_carlo)
    candidate_idx = np.arange(MC_VOL_WINDOW_DAYS - 1, len(spy_returns))
    cand_returns = spy_returns[candidate_idx]
    cand_vols = spy_vols[candidate_idx]

    # Standardized kNN features
    # ddof=0 (population std) is INTENTIONAL: consistent across all vol estimators
    # + matches the walk-forward-tuned stop calibration + the PERF-001 ddof=0
    # contract; ddof=1 is deferred to a dedicated re-tune effort (audit M1, policy
    # not bug).
    ret_mean, ret_std = float(np.mean(cand_returns)), float(np.std(cand_returns))
    vol_mean, vol_std = float(np.mean(cand_vols)), float(np.std(cand_vols))

    def _z(values, mean, std):
        if std == 0.0:
            return np.zeros_like(values, dtype=float)
        return (values - mean) / std

    cand_returns_z = _z(cand_returns, ret_mean, ret_std)
    cand_vols_z = _z(cand_vols, vol_mean, vol_std)
    today_ret_z = 0.0 if ret_std == 0.0 else (spy_today_ret_dec - ret_mean) / ret_std
    today_vol_z = 0.0 if vol_std == 0.0 else (today_vol - vol_mean) / vol_std

    distances = np.sqrt((cand_returns_z - today_ret_z) ** 2 + (cand_vols_z - today_vol_z) ** 2)

    if len(distances) <= neighbor_k:
        nearest_pool_indices = np.arange(len(distances))
    else:
        nearest_pool_indices = np.argpartition(distances, neighbor_k)[:neighbor_k]

    nearest_days = [valid_dates[candidate_idx[i]] for i in nearest_pool_indices]

    # 2. Portfolio return matrix (K days x N tickers)
    tickers = [h["ticker"] for h in holdings]
    weights = np.array([h.get("allocation", 0.0) for h in holdings])

    returns_matrix = np.zeros((len(nearest_days), len(tickers)))
    for i, date in enumerate(nearest_days):
        day_data = historical_data[date]
        spy_ret = day_data.get("SPY", {}).get("daily_ret", 0.0)
        for j, ticker in enumerate(tickers):
            if ticker in day_data:
                returns_matrix[i, j] = day_data[ticker].get("daily_ret", 0.0)
            else:
                returns_matrix[i, j] = spy_ret

    nearest_day_returns = returns_matrix.dot(weights) * PCT_SCALAR

    # 3. CVaR via Rockafellar-Uryasev general-distribution estimator on the DISTINCT
    # kNN pool (nearest_day_returns, N≈neighbor_k). The simulation_paths parameter
    # belongs to run_monte_carlo (bootstrap MC); CVaR uses the pool directly so that
    # tail_obs_count reflects genuine distinct observations (~8), not a resample
    # fraction (5%*5000=250). H-2 binding: denominator is the pool's k+atom count.
    pool = nearest_day_returns.tolist()
    cvar_estimate = compute_cvar_5pct_general_distribution(pool, alpha=CVAR_ALPHA_DEFAULT)
    # cvar_estimate.stderr already carries the H-2 distinct-tail denominator
    # (computed inside compute_cvar_5pct_general_distribution); no separate
    # compute_cvar_stderr_distinct_tail call needed.

    result = CVaRAssessment(
        cvar_pct=cvar_estimate.cvar_pct,
        breach=False,  # Phase-1: no breach threshold defined; breach is always False
        tail_obs_count=cvar_estimate.tail_obs_count,
        stderr=cvar_estimate.stderr,
        insufficient_reason=cvar_estimate.insufficient_reason,
    )

    if mode is not None:
        import database as _db  # lazy import — math_engine is pure-math at module level

        _db.record_cvar_diagnostic(
            cycle_id=cycle_id,
            symphony_id="",
            cvar_5pct=cvar_estimate.cvar_pct,
            cvar_5pct_stderr=cvar_estimate.stderr,
            cvar_n_tail=cvar_estimate.tail_obs_count,
            cvar_5pct_long=None,
            cvar_n_tail_long=None,
            mode=mode,
        )

    return result


def compute_crra_utility(W: float, gamma: float) -> float:
    """CRRA utility transform for the M1 autotuner objective.

    Computes u(W; gamma) where:
        gamma != 1: u(W) = (W^(1 - gamma) - 1) / (1 - gamma)
        gamma == 1: u(W) = ln(W)  (log-utility limit, L'Hopital)

    The '-1' numerator term MUST be present. The canonical form and the form
    without '-1' are monotone-equivalent for argmax, but NOT mean-equivalent:
    mean(U) is the M1 trial objective and the haircut t-stat numerator; the
    missing '-1' would shift mean(U) by -1/(1-gamma). Fixture W-H2 uses this
    exact form; tests validate bit-identical agreement.

    W is the FLOORED wealth argument (W >= WEALTH_ARG_FLOOR). This function
    validates that W is finite at entry via _reject_non_finite; the floor must
    be applied by the caller (autotuner.derive_floored_wealth_argument) BEFORE
    calling this function. Flooring inside this function would silently hide a
    caller contract violation.

    References:
      - decision-science-council-synthesis.md §3.9 W-H2 (wealth argument)
      - decision-science-v3-and-divergence-evaluation.md §A.1 H-1 (W-H4 floor)
      - math_engine.py CRRA_LOG_UTILITY_GAMMA_TOL (gamma==1 branch tolerance)
    """
    _reject_non_finite(W=W, gamma=gamma)
    if abs(gamma - 1.0) < CRRA_LOG_UTILITY_GAMMA_TOL:
        # Log-utility limit: gamma -> 1  =>  u(W) = ln(W)
        return math.log(W)
    return (W ** (1.0 - gamma) - 1.0) / (1.0 - gamma)


def compute_crra_eu_objective(daily_returns: list[float], gamma: float) -> float:
    """CRRA expected-utility objective for the M1 autotuner: mean(U) over the fold.

    For each daily return r_i (decimal fraction, e.g. 0.01 = 1%):
      1. Reject non-finite r_i via _reject_non_finite (A-2 star NaN closure).
      2. Derive floored wealth argument: W_i = max(WEALTH_ARG_FLOOR, 1 + r_i).
         Floor is on INPUT W; never on output U (W-H4 contract).
      3. Compute CRRA utility: U_i = compute_crra_utility(W_i, gamma).
    Returns mean(U) = sum(U) / T. Returns 0.0 for an empty series.

    The trial objective is mean(U), NOT the certainty-equivalent in return units.
    CE = ((1 + (1-gamma)*mean(U))^(1/(1-gamma)) - 1) is a monotone transform with
    identical trial rankings; it is computed separately for the audit display only.

    References:
      - decision-science-council-synthesis.md §3.9 W-H2 / W-H4
      - council-attack-rubric.md A-2 (NaN-propagation closure)
    """
    if not daily_returns:
        return 0.0
    U_values = []
    for r in daily_returns:
        _reject_non_finite(r=r)
        W = max(WEALTH_ARG_FLOOR, 1.0 + r)
        U_values.append(compute_crra_utility(W, gamma))
    return sum(U_values) / len(U_values)


def compute_regime_match_quality(
    historical_data: dict,
    spy_today_return: float,
) -> RegimeMatchAssessment:
    """Regime-match-quality guard for the MC bootstrap (vision-audit Critical Rec #2).

    Computes a Mahalanobis-style chi-squared test statistic on the K nearest
    candidate-pool neighbours of today's query point, using the same z-score
    standardization that run_monte_carlo applies (AC-1). If the mean squared
    distance to the K nearest neighbours exceeds MC_REGIME_MATCH_CHI2_THRESHOLD
    (chi2(2)_{0.99} ~= 9.21 — the SINGLE-DRAW 0.99-quantile applied as a
    conservative threshold against the MEAN-of-K statistic; effective false-positive
    rate well below 1%), today's joint state is far from every recent neighbour and
    the MC bootstrap is unrepresentative; the caller should suppress the MC veto by
    passing prob_underperforming=None to compute_exit_confirmation, exercising the
    existing MC-unavailable fail-safe path. The gate fires only on extreme regime breaks.

    Pure function. O(eligible pool size). No I/O, no blocking work (architecture
    constraint #1).

    The threshold is read from the MC_REGIME_MATCH_CHI2_THRESHOLD environment
    variable at call time if present; the module-level constant is the default.
    threshold_used on the returned assessment records the value actually applied.

    References:
      - Mahalanobis (1936) "On the generalised distance in statistics."
        Proc. Nat. Inst. Sci. India 2, 49-55.
      - Aggarwal (2017) Outlier Analysis 2nd ed. Springer. Sec 2.3, 4.4.
      - Knorr & Ng (1998) "Algorithms for mining distance-based outliers
        in large datasets." VLDB '98, 392-403.
    """
    _reject_non_finite(spy_today_return=spy_today_return)
    for day_data in historical_data.values():
        for ticker_data in day_data.values():
            _reject_non_finite_in_records([ticker_data], "daily_ret")
    # Operator-overridable threshold (env var beats module-level default).
    _env_override = os.environ.get("MC_REGIME_MATCH_CHI2_THRESHOLD")
    threshold = (
        float(_env_override) if _env_override is not None else MC_REGIME_MATCH_CHI2_THRESHOLD
    )

    # K for the kNN test statistic — reuses the canonical MC default constant.
    k = MC_DEFAULT_NEIGHBOR_K

    valid_dates = _sorted_dates(historical_data)

    # Eligible-pool sufficiency: mirrors run_monte_carlo's exact boundary so the
    # two guards are bit-consistent on the short-history short-circuit.
    eligible_days = len(valid_dates) - (MC_VOL_WINDOW_DAYS - 1)
    if eligible_days < MC_MIN_HISTORY_DAYS:
        return RegimeMatchAssessment(
            mean_sq_mahalanobis=None,
            is_unprecedented=False,  # fail-safe: absent diagnostic is not a suppression signal
            neighbor_k=k,
            threshold_used=threshold,
            insufficient_reason=(
                f"regime-match quality: eligible pool {eligible_days} days "
                f"< MC_MIN_HISTORY_DAYS ({MC_MIN_HISTORY_DAYS}); "
                "assessment unavailable"
            ),
        )

    # Build SPY return series (decimal) from the pool — same source as run_monte_carlo.
    spy_returns = np.array(
        [historical_data[date].get("SPY", {}).get("daily_ret", 0.0) for date in valid_dates]
    )

    # Rolling 20-day vol for each pool day — same computation as run_monte_carlo.
    spy_vols = _compute_rolling_spy_vol(spy_returns)

    # Today's decimal return and rolling vol — same derivation as run_monte_carlo.
    spy_today_ret_dec = spy_today_return / PCT_SCALAR
    today_vol = np.std(np.append(spy_returns[-(MC_VOL_WINDOW_DAYS - 1) :], spy_today_ret_dec))

    # Early-window exclusion — mirrors run_monte_carlo's candidate_idx construction.
    candidate_idx = np.arange(MC_VOL_WINDOW_DAYS - 1, len(spy_returns))
    cand_returns = spy_returns[candidate_idx]
    cand_vols = spy_vols[candidate_idx]

    # Z-score both features using candidate-pool statistics — same params as run_monte_carlo
    # so squared Euclidean distances approximate Mahalanobis distances (zero-std guard
    # collapses to 0.0 to keep output finite).
    # ddof=0 (population std) is INTENTIONAL: consistent across all vol estimators
    # + matches the walk-forward-tuned stop calibration + the PERF-001 ddof=0
    # contract; ddof=1 is deferred to a dedicated re-tune effort (audit M1, policy
    # not bug).
    ret_mean, ret_std = float(np.mean(cand_returns)), float(np.std(cand_returns))
    vol_mean, vol_std = float(np.mean(cand_vols)), float(np.std(cand_vols))

    def _z(values, mean, std):
        if std == 0.0:
            return np.zeros_like(values, dtype=float)
        return (values - mean) / std

    cand_returns_z = _z(cand_returns, ret_mean, ret_std)
    cand_vols_z = _z(cand_vols, vol_mean, vol_std)
    today_ret_z = 0.0 if ret_std == 0.0 else (spy_today_ret_dec - ret_mean) / ret_std
    today_vol_z = 0.0 if vol_std == 0.0 else (today_vol - vol_mean) / vol_std

    # Squared Euclidean distance from today's z-scored query to each candidate day.
    sq_distances = (cand_returns_z - today_ret_z) ** 2 + (cand_vols_z - today_vol_z) ** 2

    # K nearest neighbours by squared distance; when pool is smaller than K, use all.
    if len(sq_distances) <= k:
        nearest_sq = sq_distances
    else:
        nearest_idx = np.argpartition(sq_distances, k)[:k]
        nearest_sq = sq_distances[nearest_idx]

    # Mean squared Mahalanobis-style distance over K nearest neighbours (Surface 3 spec:
    # "if the K nearest neighbours have a mean distance above a threshold, the bootstrap
    # is unrepresentative"). This is the test statistic compared to the chi2(2)_{0.99} threshold.
    mean_sq = float(np.mean(nearest_sq))
    is_unprecedented = mean_sq > threshold

    return RegimeMatchAssessment(
        mean_sq_mahalanobis=mean_sq,
        is_unprecedented=is_unprecedented,
        neighbor_k=k,
        threshold_used=threshold,
        insufficient_reason=None,
    )


def compute_pbo(
    configs_date_returns: "list[dict[str, float]]",
    eligible_dates: "list[str]",
    gamma: float,
    S: "int | None" = None,
) -> float:
    """Compute the Probability of Backtest Overfitting (PBO) via reduced-N CSCV.

    Canonical reduced-N Combinatorially Symmetric Cross-Validation per Bailey &
    López de Prado 2014, "The Probability of Backtest Overfitting", Journal of
    Computational Finance 20(4), DOI 10.21314/JCF.2014.005.

    Algorithm:
      1. Partition eligible_dates into S=_CSCV_S contiguous chronological blocks
         (even tiling: extra dates distributed one-each to the earliest blocks).
      2. For each of C(S, S//2) = C(8,4) = 70 IS/OOS combinations (S//2 IS blocks,
         S//2 OOS blocks):
         a. Score each config on IS-block dates using CRRA-EU mean utility.
         b. Find the IS-best config (argmax).
         c. Score all K configs on OOS-block dates; rank the IS-best 1-based ascending
            (rank_c=1 means worst OOS, rank_c=K means best OOS).
         d. omega_bar_c = (rank_c - 0.5) / K
         e. lambda_c    = ln(omega_bar_c / (1 - omega_bar_c))
      3. PBO = (count of combos where lambda_c <= 0) / (total combos).

    PBO > PBO_REJECT_THRESHOLD (0.5) means the IS-best performs worse-than-random
    on OOS — definitive evidence of backtest overfitting. Veto fires when
    pbo > PBO_REJECT_THRESHOLD (strict inequality; pbo=0.5 exactly is accepted).

    Parameters
    ----------
    configs_date_returns:
        K config dicts, each mapping date-string -> decimal return. Dates not
        present in a config score 0.0 CRRA-EU (matching compute_crra_eu_objective
        empty-series contract).
    eligible_dates:
        Sorted list of date strings covering the CPCV-eligible window
        (sorted_dates[:frozen_start_idx]).
    gamma:
        CRRA risk-aversion coefficient passed through to compute_crra_eu_objective.
    S:
        Block count override (default: _CSCV_S=8). Exposed for test tractability
        with smaller grids; production always uses the default.

    Returns
    -------
    float in [0.0, 1.0] — fraction of IS/OOS combinations where the IS-best
    config ranked at or below the median on OOS (lambda_c <= 0). Fully
    deterministic (no random or wall-clock dependency).

    Notes
    -----
    compute_pbo is ORTHOGONAL to _haircut_select / compute_n_effective:
      - BHY/n_effective = multiplicity axis (how many independent tests were run)
      - PBO             = sample-robustness axis (does IS-selection generalise OOS?)
    Never call _haircut_select, compute_n_effective, or any BHY symbol here.

    Reference: Bailey & López de Prado 2014 §3.4 "Interpretation of PBO".
    """
    import itertools

    n_blocks = S if S is not None else _CSCV_S
    K = len(configs_date_returns)
    n_dates = len(eligible_dates)

    if K == 0 or n_dates == 0:
        return 0.0

    # --- Step 1: partition eligible_dates into n_blocks contiguous chronological blocks.
    # Even tiling: divide n_dates as evenly as possible; extra dates go to the first
    # (remainder) blocks (one extra each), so block sizes differ by at most 1.
    base_size, remainder = divmod(n_dates, n_blocks)
    blocks: list[list[str]] = []
    start = 0
    for i in range(n_blocks):
        size = base_size + (1 if i < remainder else 0)
        blocks.append(eligible_dates[start : start + size])
        start += size

    # --- Step 2: iterate over C(n_blocks, n_blocks//2) IS/OOS combinations.
    half = n_blocks // 2
    all_block_indices = range(n_blocks)
    combinations = list(itertools.combinations(all_block_indices, half))

    lambda_lte_zero_count = 0
    total_combos = len(combinations)

    for is_indices in combinations:
        oos_indices = tuple(i for i in all_block_indices if i not in set(is_indices))

        # Build IS and OOS date sets for this combination.
        is_dates: set[str] = set()
        for idx in is_indices:
            is_dates.update(blocks[idx])
        oos_dates: set[str] = set()
        for idx in oos_indices:
            oos_dates.update(blocks[idx])

        # --- Step 2a-b: score each config on IS dates; find IS-best.
        is_scores: list[float] = []
        for cfg in configs_date_returns:
            is_returns = [cfg[d] for d in is_dates if d in cfg]
            is_scores.append(compute_crra_eu_objective(is_returns, gamma))

        is_best_idx = is_scores.index(max(is_scores))

        # --- Step 2c: score all K configs on OOS dates; rank IS-best 1-based ascending.
        oos_scores: list[float] = []
        for cfg in configs_date_returns:
            oos_returns = [cfg[d] for d in oos_dates if d in cfg]
            oos_scores.append(compute_crra_eu_objective(oos_returns, gamma))

        is_best_oos = oos_scores[is_best_idx]
        # 1-based ascending rank: rank_c=1 → worst OOS (lowest score), rank_c=K → best OOS.
        # PESSIMISTIC tie-breaking: the IS-best takes the LOWEST rank within its OOS-tie
        # group — `rank_c = (# configs strictly below) + 1`. This is the only change
        # from the prior optimistic `sum(s <= is_best_oos)`; it matters exactly on ties:
        #   - All-tie OOS (every config ties the IS-best): rank_c = 0 + 1 = 1 →
        #     omega_bar ≈ 0.5/K → lambda < 0 → counted as OVERFIT (PBO→1). A strategy
        #     that distinguishes nothing OOS is overfit, not robust. (Optimistic `<=`
        #     gave rank_c=K → PBO→0, the bug.)
        #   - Genuinely dominant config (all others strictly below): rank_c = (K-1)+1 = K
        #     → omega_bar ≈ (K-0.5)/K → lambda > 0 → NOT overfit (PBO→0), unchanged from
        #     `<=`. The +1 restores the IS-best's own position; a bare strict `<` (no +1)
        #     would drop the dominant config to rank K-1 and wrongly flip it to overfit.
        rank_c = sum(1 for s in oos_scores if s < is_best_oos) + 1

        # --- Step 2d-e: omega_bar and lambda.
        omega_bar = (rank_c - 0.5) / K
        # Clamp omega_bar away from 0 and 1 to prevent log domain errors on
        # degenerate inputs (all configs identical → all ties → omega_bar could be 0 or 1
        # depending on tie-breaking; the clamp is epsilon-level, not distorting).
        omega_bar = max(1e-15, min(1.0 - 1e-15, omega_bar))
        lambda_c = math.log(omega_bar / (1.0 - omega_bar))

        if lambda_c <= 0.0:
            lambda_lte_zero_count += 1

    if total_combos == 0:
        return 0.0
    return lambda_lte_zero_count / total_combos
