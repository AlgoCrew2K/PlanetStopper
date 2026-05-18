import hashlib
import math

import numpy as np
from scipy.stats import norm

# Euler-Mascheroni constant (Bailey & López de Prado 2014, DOI: 10.3905/jpm.2014.40.5.094,
# Eq. 9 expected-max-SR appendix). Used to derive SR_0 for selection-bias correction.
_GAMMA_EULER_MASCHERONI = 0.5772156649015329

# Sentinel returned by compute_sortino_ratio (autotuner.py) when downside_deviation==0
# (all trial returns beat the target). The value is finite and looks like a valid trial
# result to Optuna's TPE, but its magnitude (~1e6) dominates the cross-trial distribution
# mean and std, distorting gamma3/gamma4 and SR_0 fed to compute_deflated_sharpe_ratio.
# Filtering before moment computation prevents sentinel pollution of DSR Eq. 9.
# Source: autotuner.py compute_sortino_ratio — returns 1e6 for zero downside_deviation.
_SORTINO_SENTINEL = 1e6


def compute_expected_max_sharpe(sr_mean: float, sr_std: float, n_trials: int) -> float:
    """Expected maximum Sharpe across N independent trials (Bailey & López de Prado 2014,
    DOI: 10.3905/jpm.2014.40.5.094, Eq. 9 expected-max-SR appendix).

    SR_0 = sr_mean + sr_std * ((1 - gamma_E) * Phi^-1(1 - 1/N) + gamma_E * Phi^-1(1 - 1/(N*e)))

    Used as SR_0 in compute_deflated_sharpe_ratio to correct for selection bias
    across N Optuna trials.
    """
    if n_trials <= 0:
        raise ValueError(f"n_trials must be >= 1; got {n_trials}")
    if sr_std < 0:
        raise ValueError(f"sr_std must be >= 0; got {sr_std}")
    if n_trials == 1 or sr_std == 0:
        return float(sr_mean)
    ppf1 = norm.ppf(1.0 - 1.0 / n_trials)
    ppf2 = norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return sr_mean + sr_std * ((1.0 - _GAMMA_EULER_MASCHERONI) * ppf1 + _GAMMA_EULER_MASCHERONI * ppf2)


def filter_sortino_sentinels(sortino_values: list[float]) -> list[float]:
    """Remove sentinel values from a Sortino trial series before DSR moment computation.

    Returns a new list with all _SORTINO_SENTINEL (1e6) entries removed. The input
    list is not mutated. Legitimate trial values — including negative Sortinos and 0.0
    (empty-returns series) — are preserved unchanged.

    Call this on trial_values BEFORE computing mean, std, gamma3, gamma4, or SR_0.
    Sentinel values pollute moments: their ~1e6 magnitude dominates the cross-trial
    distribution and distorts DSR Eq. 9 (Bailey & López de Prado 2014).
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

LOOKBACK_DAYS = 20  # 20-day realized-volatility window — AlphaBot risk-sizing standard
ATR_LOOKBACK_DAYS = 15  # 14-day true-range window (standard ATR period) + 1 prior close required to compute the first TR; matches AlphaBot's risk-sizing assumption
PCT_SCALAR = 100.0  # decimal return -> percentage points (math layer normalizes to pct)

# Monte Carlo gating constants (run_monte_carlo)
MC_INSUFFICIENT_HISTORY_PROB = 100.0  # Sentinel probability when MC history insufficient — emits 100% to skip MC exit gate
MC_MIN_HISTORY_DAYS = 20              # Minimum history rows for MC simulation to run; below this we short-circuit
MC_VOL_WINDOW_DAYS = 20              # Rolling SPY vol window; arithmetic uses (DAYS - 1) for inclusive endpoint
MC_DEFAULT_SIMULATION_PATHS = 5000   # Default MC path count — CLT stability vs runtime tradeoff
MC_DEFAULT_NEIGHBOR_K = 150          # Default kNN regime locality — smaller=tighter regime match, larger=smoother estimate
# Seed modulus maps SHA-256 digest to numpy Generator's safe int range [0, 2^31);
# 2^31 gives ~98k distinct values/year with no collisions across YYYYMMDD_HHMM space.
MC_SEED_MODULUS = 2**31

# Time-squeeze decay constants (drives intraday tightening of trailing stops)
DECAY_CURVE_SCALAR = 9       # log10(1 + 9*t) maps t in [0,1] to decay in [0,1]; produces the characteristic AlphaBot intraday decay curve
MULT_OPEN = 1.5              # dynamic_multiplier at market open (loosest stop)
MULT_CLOSE = 0.5             # dynamic_multiplier at market close (tightest)
MIN_STOP_OPEN = 0.3          # min stop floor at market open, in percentage points
MIN_STOP_CLOSE = 0.15        # min stop floor at market close, in percentage points
VOL_FALLBACK = 1.0           # neutral fallback for safe_vol when symphony_vol <= 0 (preserves vol-scale arithmetic in the degenerate-vol case)

# Breakeven-lock constants (drives HWM-hold-based stop tightening)
BREAKEVEN_ACTIVATION_MIN = 0.4         # lower clamp for dynamic activation threshold (in percentage points)
BREAKEVEN_ACTIVATION_MAX = 3.0         # upper clamp for dynamic activation threshold
BREAKEVEN_ACTIVATION_DEADBAND = 0.2    # current_return must be within this distance below dynamic_activation to count a tick
HWM_HOLD_TICKS_THRESHOLD = 5          # consecutive qualifying ticks needed to lock breakeven (transition is one-way)
TRIGGERED_OVERRIDE_LEVEL = -999.0      # sentinel stop level when position is already triggered (suppresses re-exit)


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
    _reject_non_finite(current_return=current_return, prev_return=prev_return, para_threshold=para_threshold)
    velocity = float(current_return) - float(prev_return)
    should_arm = bool((velocity >= para_threshold) and (not currently_armed))
    return velocity, should_arm


def compute_time_squeeze_decay(time_ratio: float) -> tuple[float, float]:
    """
    Returns (dynamic_multiplier, dynamic_min_stop) for the time-squeeze decay
    curve.

    - time_ratio in [0.0, 1.0] is fraction of trading session elapsed
      (0.0 = market open, 1.0 = close). CALLER clamps before passing; this
      function does not validate.
    - decay_curve = log10(1 + DECAY_CURVE_SCALAR * time_ratio), ranges from
      0.0 at open to 1.0 at close.
    - dynamic_multiplier linearly interpolates from MULT_OPEN at decay=0
      to MULT_CLOSE at decay=1.
    - dynamic_min_stop linearly interpolates from MIN_STOP_OPEN at decay=0
      to MIN_STOP_CLOSE at decay=1.

    Pure. No I/O. No state. No datetime handling.

    Extracted from alpha_bot_execution.py:574-585 (cycle 4 of math-layer
    extraction).
    """
    _reject_non_finite(time_ratio=time_ratio)
    decay_curve = math.log10(1 + DECAY_CURVE_SCALAR * time_ratio)
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
    previously_persisted_stop_level: float | None = None,
) -> tuple[int, bool, float]:
    """
    Computes the breakeven-lock state update and the resolved stop trigger level.

    Returns (new_hold_ticks, new_breakeven_locked, stop_trigger_level).

    Logic (extracted verbatim from alpha_bot_execution.py):
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

    LATCHING INVARIANT: once currently_breakeven_locked=True, new_breakeven_locked
    is ALWAYS True regardless of any other input. The inline producer never
    resets breakeven_locked to False; that one-way transition must be preserved.

    MONOTONICITY INVARIANT (trailing-stop ratchet): a trailing stop must never
    move DOWN over a position's lifetime. When the caller supplies the
    previously-persisted stop level (``previously_persisted_stop_level``), the
    returned ``stop_trigger_level`` is clamped to be no lower than that prior
    value — this enforces the ratchet across cycles. The clamp is bypassed
    when ``is_triggered=True`` because the sentinel TRIGGERED_OVERRIDE_LEVEL
    (-999.0) is a committed-exit marker, not a live stop boundary. When
    ``previously_persisted_stop_level is None`` (default), the clamp is a
    no-op and behavior is identical to the pre-monotonicity contract — this
    preserves backward-compatibility with fixture-driven callers that do not
    thread prior state.

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
    new_breakeven_locked = bool(currently_breakeven_locked or (new_hold_ticks >= HWM_HOLD_TICKS_THRESHOLD))
    if new_breakeven_locked:
        stop_trigger_level = max(base_stop_level, 0.0)
    else:
        stop_trigger_level = base_stop_level
    if is_triggered:
        # Triggered stop bypasses monotonicity by design — exit is committed.
        stop_trigger_level = TRIGGERED_OVERRIDE_LEVEL
    elif previously_persisted_stop_level is not None:
        # Trailing-stop ratchet: never move the stop DOWN across cycles.
        stop_trigger_level = max(previously_persisted_stop_level, stop_trigger_level)
    return int(new_hold_ticks), new_breakeven_locked, float(stop_trigger_level)


# Exit-confirmation constants (gates trailing-stop trigger)
MAGNITUDE_FLOOR_PCT = 0.10       # return must drop at least this far BELOW stop_trigger_level to count toward exit confirmation
MC_SANITY_THRESHOLD = 60.0       # MC probability >= this value blocks exit ("if we still think we beat the benchmark, don't capitulate")
EXIT_CONFIRM_TICKS = 3           # consecutive qualifying ticks needed to flip is_trailing_stop_hit


def compute_exit_confirmation(
    armed: bool,
    is_triggered: bool,
    current_return: float,
    stop_trigger_level: float,
    prob_beating: float,
    current_below_stop_count: int,
) -> tuple[int, bool]:
    """
    Computes the trailing-stop exit-confirmation state update.

    Returns (new_below_stop_count, is_trailing_stop_hit).

    Logic (extracted verbatim from alpha_bot_execution.py):
      if not armed or is_triggered:
          return current_below_stop_count, False     # whole block skipped; state unchanged
      below_stop_condition = (current_return <= stop_trigger_level - MAGNITUDE_FLOOR_PCT)
                             and (prob_beating < MC_SANITY_THRESHOLD)
      if below_stop_condition:
          new_count = current_below_stop_count + 1
          hit = (new_count >= EXIT_CONFIRM_TICKS)
          return new_count, hit
      else:
          return 0, False                            # reset on miss

    GUARD INVARIANT: when (not armed) or is_triggered, the function returns
    the INPUT below_stop_count unchanged AND False. This preserves the inline
    behavior where the entire stop-check block is skipped — the count is
    NEITHER incremented NOR reset under those conditions.

    Pure. No I/O. No state. Caller handles print transitions by comparing
    input current_below_stop_count to returned new_below_stop_count.
    """
    _reject_non_finite(
        current_return=current_return,
        stop_trigger_level=stop_trigger_level,
        prob_beating=prob_beating,
    )
    if (not armed) or is_triggered:
        return int(current_below_stop_count), False

    below_stop_condition = (
        current_return <= (stop_trigger_level - MAGNITUDE_FLOOR_PCT)
    ) and (prob_beating < MC_SANITY_THRESHOLD)

    if below_stop_condition:
        new_count = int(current_below_stop_count) + 1
        hit = bool(new_count >= EXIT_CONFIRM_TICKS)
        return new_count, hit
    else:
        return 0, False


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
        _reject_non_finite(
            **{k: entry[k] for k in ("last_price", "vwap") if k in entry}
        )
    weighted_vwap_diff = 0.0
    valid_vwap_weight = 0.0
    for h in holdings:
        ticker = h.get("ticker")
        allocation = h.get("allocation", 0.0)
        if ticker in live_vwaps:
            entry = live_vwaps[ticker]
            p = entry["last_price"]
            v = entry["vwap"]
            if v > 0:
                weighted_vwap_diff += allocation * ((p - v) / v)  # (p-v)/v first: preserves inline's IEEE-754 evaluation order
                valid_vwap_weight += allocation
    return float(weighted_vwap_diff), float(valid_vwap_weight)


# VWAP bleed-arm constants (dynamic exit threshold for VWAP-bleed system; always negative)
VWAP_BLEED_ARM_MIN = -3.0    # most-negative clamp; deepest bleed threshold allowed (further drops do not arm any sooner)
VWAP_BLEED_ARM_MAX = -0.5    # least-negative clamp; arm threshold must be at least this deep (shallower drops never arm)


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
VWAP_WEIGHT_THRESHOLD = 0.5         # minimum allocation coverage to evaluate VWAP signals; below this, the weighted diff is too unreliable
VWAP_BREAK_CONFIRM_TICKS = 3        # consecutive qualifying ticks for System A (profit-protection break) to flip is_vwap_broken


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

    BRANCH 1 — is_triggered guard:
        State preserved unchanged. No signals.
        Returns (current_vwap_ticks, current_vwap_bleed_ticks, False, False)

    BRANCH 2 — gate fails (NOT armed for VWAP eval):
        Gate: valid_vwap_weight > VWAP_WEIGHT_THRESHOLD
              AND weighted_vwap_diff < 0
        Both counters RESET to 0. No signals.

    BRANCH 3 — gate passes:
        System A (profit-protection break, INDEPENDENT of B):
            Condition: safe_hwm >= vwap_cross_hwm_pct
                       AND current_return < safe_hwm
            Met:  new_vwap_ticks = current_vwap_ticks + 1
                  is_vwap_broken = (new_vwap_ticks >= VWAP_BREAK_CONFIRM_TICKS)
            Miss: new_vwap_ticks = 0
        System B (bleed, INDEPENDENT of A):
            Condition: current_return <= vwap_bleed_arm_pct
            Met:  new_vwap_bleed_ticks = current_vwap_bleed_ticks + 1
                  is_vwap_bleed_broken = (new_vwap_bleed_ticks >= vwap_bleed_ticks_threshold)
            Miss: new_vwap_bleed_ticks = 0

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

    if not (valid_vwap_weight > VWAP_WEIGHT_THRESHOLD and weighted_vwap_diff < 0):
        return 0, 0, False, False

    # System A
    if safe_hwm >= vwap_cross_hwm_pct and current_return < safe_hwm:
        new_vwap_ticks = int(current_vwap_ticks) + 1
        is_vwap_broken = bool(new_vwap_ticks >= VWAP_BREAK_CONFIRM_TICKS)
    else:
        new_vwap_ticks = 0
        is_vwap_broken = False

    # System B
    if current_return <= vwap_bleed_arm_pct:
        new_vwap_bleed_ticks = int(current_vwap_bleed_ticks) + 1
        is_vwap_bleed_broken = bool(new_vwap_bleed_ticks >= vwap_bleed_ticks_threshold)
    else:
        new_vwap_bleed_ticks = 0
        is_vwap_bleed_broken = False

    return new_vwap_ticks, new_vwap_bleed_ticks, is_vwap_broken, is_vwap_bleed_broken


# Default grace window length — operational policy: suppress open-volatility VWAP signals
# for the first 15 min after EXECUTION_START_TIME (V2, AC-V2.1).
VWAP_OPEN_WINDOW_GRACE_MINUTES_DEFAULT = 15


def is_in_open_window_grace(
    current_et,
    execution_start_hhmm: str,
    grace_minutes: int,
) -> bool:
    """
    Returns True iff current_et falls in [exec_start, exec_start + grace_minutes).

    Pure function — no I/O, no state. Returns False before exec_start
    (pre-action-gate territory handled by the existing action gate).
    """
    import datetime as _dt
    h, m = map(int, execution_start_hhmm.split(":"))
    exec_start = _dt.time(h, m)
    exec_start_dt = current_et.replace(hour=h, minute=m, second=0, microsecond=0)
    grace_end_dt = exec_start_dt + _dt.timedelta(minutes=grace_minutes)
    current_time_naive = current_et.replace(tzinfo=None)
    exec_start_naive = exec_start_dt.replace(tzinfo=None)
    grace_end_naive = grace_end_dt.replace(tzinfo=None)
    return exec_start_naive <= current_time_naive < grace_end_naive


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
    Same cycle_id always produces the same seed (SHA-256 truncated to 31 bits).
    """
    return int(hashlib.sha256(cycle_id.encode()).hexdigest(), 16) % MC_SEED_MODULUS


def run_monte_carlo(holdings, historical_data, spy_today_return, simulation_paths=MC_DEFAULT_SIMULATION_PATHS, neighbor_k=MC_DEFAULT_NEIGHBOR_K, seed: int | None = None):
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
        for h in holdings if h.get("last_percent_change") is not None
    )
    valid_dates = sorted(list(historical_data.keys()))
    if len(valid_dates) < MC_MIN_HISTORY_DAYS:
        return MC_INSUFFICIENT_HISTORY_PROB

    # 1. Calculate distances based on SPY return and rolling 20-day volatility
    spy_returns = np.array([historical_data[date].get("SPY", {}).get("daily_ret", 0.0) for date in valid_dates])
    
    spy_vols = np.zeros_like(spy_returns)
    for i in range(len(spy_returns)):
        start_idx = max(0, i - (MC_VOL_WINDOW_DAYS - 1))
        if i > 0:
            spy_vols[i] = np.std(spy_returns[start_idx:i+1])
        else:
            spy_vols[i] = 0.0
            
    spy_today_ret_dec = spy_today_return / PCT_SCALAR
    # Invariant: len(spy_returns) >= MC_VOL_WINDOW_DAYS - 1 because the guard at line ~445 returns
    # early when len(valid_dates) < MC_MIN_HISTORY_DAYS, and MC_MIN_HISTORY_DAYS >= MC_VOL_WINDOW_DAYS.
    today_vol = np.std(np.append(spy_returns[-(MC_VOL_WINDOW_DAYS - 1):], spy_today_ret_dec))

    # Euclidean distance across 2 dimensions
    distances = np.sqrt((spy_returns - spy_today_ret_dec)**2 + (spy_vols - today_vol)**2)
    
    # 2. Get top K indices
    if len(distances) <= neighbor_k:
        nearest_indices = np.arange(len(distances))
    else:
        # argpartition is faster than full sort
        nearest_indices = np.argpartition(distances, neighbor_k)[:neighbor_k]
    
    nearest_days = [valid_dates[i] for i in nearest_indices]
    
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
    valid_dates = sorted(list(historical_data.keys()))[-LOOKBACK_DAYS:]
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
    valid_dates = sorted(list(historical_data.keys()))[-ATR_LOOKBACK_DAYS:]
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
            if not day_data or "high" not in day_data or "low" not in day_data or "close" not in day_data:
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
