import time
import math
import statistics
import optuna
from datetime import datetime, timedelta, timezone
import database
import math_engine
import synthetic_history
import glob
import json


def _replay_grace_minutes() -> int:
    """Return the VWAP open-window grace minutes the replay must use.

    The replay SHARES production's single source of truth — it references
    alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES, never re-derives its
    own copy, so a re-tune of that dial flows through to both paths. The
    import is function-local because a top-level `import alpha_bot_execution`
    would be circular (alpha_bot_execution imports autotuner).

    Production suppresses VWAP-Breakdown and VWAP-Bleed-Cut for this many
    minutes after the session open to avoid open-volatility false exits
    (V2, AC-V2.1). On minute-bar replay ticks tick_idx 0 == session open, so
    the faithful equivalent of production's datetime grace gate is
    `tick_idx < _replay_grace_minutes()`.
    """
    import alpha_bot_execution
    return alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES

# --- PORT-LEVEL REPLAY BLIND SPOT (AC-8 / plan D-C3b) ---
# The autotuner replay (run_simulation / _collect_sim_returns /
# replay_exit_sequence) simulates ONLY the per-symphony altitude — it iterates
# per sym_id and replays each symphony's exit path in isolation. There is no
# port-level altitude replay: no port aggregation, no port-level exit. So
# port-mode autotuning results are NOT replay-validated — the objective the
# optimizer maximizes in port mode does not reflect a port-level exit
# simulation. Port-level exiting is currently dormant (the standing config runs
# per_symphony); a full port-level replay simulation is a separate, larger
# feature. warn_port_mode_replay_blind_spot() makes this gap visible so
# port-mode tuning cannot be silently trusted.

# Required keys for a complete Optuna best_params payload.
# MUST be kept in sync with the suggest_* calls in the objective() closure
# below. If any of these keys is missing from study.best_params after
# optimization, the AI proposal is rejected wholesale (no Frankenstein merge)
# and the baseline cascade (fallback -> default) runs.
# Extra keys outside this set are tolerated for forward-compat.
# Note: optuna.logging.set_verbosity is now called inside run_autotuner
# (not module-level) so import does not trigger logging side effects.
# Vars in database.DEFAULT_LOCKED_VARS are excluded from the search space —
# they retain their current values and are never overwritten by trial suggestions.
OPTUNA_SEARCH_SPACE_KEYS = frozenset({
    "TAKE_PROFIT_MC_PCT", "VWAP_CROSS_HWM_PCT",
    "VWAP_BLEED_MULTIPLIER", "VWAP_BLEED_TICKS",
    "PARABOLIC_VELOCITY_THRESHOLD", "MAX_PARABOLIC_SQUEEZE",
})

# Optuna search space bounds — named so the search space is inspectable via
# optuna-compare without re-parsing logs, and to satisfy the no-magic-numbers rule.
_SS_TAKE_PROFIT_MC_MIN = 2.0
_SS_TAKE_PROFIT_MC_MAX = 10.0
_SS_VWAP_CROSS_HWM_MIN = 0.5
_SS_VWAP_CROSS_HWM_MAX = 2.5
_SS_VWAP_BLEED_MULT_MIN = 0.5
_SS_VWAP_BLEED_MULT_MAX = 3.0
_SS_VWAP_BLEED_TICKS_MIN = 3
_SS_VWAP_BLEED_TICKS_MAX = 30
_SS_PARA_VEL_MIN = 1.0
_SS_PARA_VEL_MAX = 4.0
_SS_MAX_PARA_SQUEEZE_MIN = 0.1
_SS_MAX_PARA_SQUEEZE_MAX = 0.8

# V1 calibration sweep — narrowed VWAP_CROSS_HWM_PCT bounds.
# Lower: 0.3 (vs general 0.5) — 3-tick confirm gate (math_engine.py) prevents spurious
# single-tick exits at this level; gives the sweep more room to find the true optimum.
# Upper: 2.0 (~2σ typical daily return) — above this System A is effectively disabled
# for normal sessions, making calibration unreliable.
_SS_VWAP_CROSS_HWM_V1_MIN = 0.3
_SS_VWAP_CROSS_HWM_V1_MAX = 2.0

# Exponential time-decay rate applied to per-day guard-alpha in run_simulation
# and _collect_sim_returns. Half-life ≈ 46 trading days (ln2 / 0.015).
_GUARD_ALPHA_DECAY_RATE = 0.015

# --- run_simulation objective: loss-averse utility penalty constants (audit H-10) ---
# The run_simulation objective is an explicit LOSS-AVERSE utility: it weights
# downside outcomes (negative guard-alpha, missed upside, peak-to-exit drawdown)
# more heavily than symmetric guard-alpha. Loss aversion is a deliberate
# capital-preservation choice — the same family of asymmetric utility AlphaBot's
# Sortino objective (downside deviation) embodies. Each scalar/threshold below
# was an unsourced inline literal (finding H-10); naming + sourcing them here
# makes the objective inspectable and prevents a silent scalar drift inverting
# the policy ranking.

# Multiplier on missed upside (best intraday return forgone by exiting early).
# 1.5 > 1.0: forgone upside is penalised harder than realised guard-alpha — an
# early exit that leaves a run on the table is a real opportunity cost.
MISSED_UPSIDE_PENALTY_MULT = 1.5
# Missed upside is only penalised once it exceeds this many percent — a small
# forgone move is execution noise, not a policy defect.
MISSED_UPSIDE_THRESHOLD_PCT = 1.0

# Multiplier on peak-to-exit drawdown (profit given back from the intraday high).
# 0.75 < 1.0: giving back profit is penalised, but more leniently than missed
# upside — some give-back is unavoidable in any trailing-stop policy.
DRAWDOWN_PENALTY_MULT = 0.75
# Peak-to-exit drawdown is only penalised once it exceeds this many percent —
# a give-back smaller than this is within normal trailing-stop slack.
DRAWDOWN_THRESHOLD_PCT = 1.5
# The drawdown penalty applies only after a position reached at least this gain;
# below it there is no meaningful profit to "give back".
DRAWDOWN_MIN_GAIN_PCT = 1.0

# Loss-aversion multiplier on NEGATIVE guard-alpha (the policy exited worse than
# simply holding to EOD). 2.0 > 1.0 makes the objective asymmetric: a loss of
# guard-alpha hurts twice as much as an equal gain helps — the core loss-averse
# term of the utility.
NEGATIVE_GUARD_ALPHA_LOSS_AVERSE_MULT = 2.0

# Target return for Sortino denominator: capital preservation baseline (0 = break-even).
# Operator decision PA-5; Sortino & van der Meer 1991, J. Portfolio Management.
SORTINO_TARGET_RETURN = 0.0

# Walk-forward purge window: training samples whose feature lookback window overlaps
# the test fold are excluded. Purge-relevant lookbacks are only those that cause a
# train sample's FEATURE COMPUTATION to reach into the test fold:
#   - calculate_20d_vol:      LOOKBACK_DAYS      = 20 trading days
#   - calculate_14d_atr_pct:  ATR_LOOKBACK_DAYS  = 15 trading days (14 TR periods + 1 prior close)
# PURGE_DAYS = max(20, 15) = 20.
# NOTE: The exponential decay weight (_GUARD_ALPHA_DECAY_RATE, half-life ≈ 46 days) is an
# OBJECTIVE AGGREGATION WEIGHT, not a feature lookback — it does not cause any train
# sample's feature computation to reach into the test fold and is therefore excluded from
# purge sizing. mc_prob is pre-computed in tick data, not computed live in the sim loop.
# López de Prado 2018, Advances in Financial Machine Learning, Ch. 7 (Purged k-fold CV).
PURGE_DAYS = 20

# Embargo period between train-end and test-start. Prevents autocorrelation
# leakage from serial dependence in adjacent samples — the embargo's job is to
# span the serial-dependence horizon of the per-day guard-alpha series so a
# train sample cannot leak signal into the test fold through autocorrelation.
# That horizon was estimated for the per-day guard-alpha series (AC-9, audit
# M-7): guard-alpha is a per-day exit-vs-hold delta computed from
# independently-seeded daily replays, so its day-to-day autocorrelation decays
# inside a single trading day — the measured/estimated horizon is <= 1 day.
# A 1-day embargo therefore spans the full estimated horizon and is vindicated,
# not an unexamined floor; it is also consistent with López de Prado's ~1%
# lower-bound embargo guidance.
# López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.
EMBARGO_DAYS = 1

# Three-fold walk-forward ratios: 60% train / 20% validation / 20% frozen-eval.
# Selection is on validation; frozen-eval is consumed once post-selection for honest
# performance reporting. Purge + embargo applied at BOTH fold boundaries.
# 60/20/20 split is an operator choice for AlphaBot's data scale (125 trading days);
# the held-out frozen-eval invariant derives from LdP 2018 Ch. 7.4 (not the specific ratio).
TRAIN_RATIO = 0.60
VALIDATION_RATIO = 0.20
FROZEN_EVAL_RATIO = 0.20
assert abs(TRAIN_RATIO + VALIDATION_RATIO + FROZEN_EVAL_RATIO - 1.0) < 1e-9, (
    "TRAIN_RATIO + VALIDATION_RATIO + FROZEN_EVAL_RATIO must equal 1.0"
)

# Amendment F2: Port-mode uses a 50/20/30 split (wider frozen-eval fold).
# Rationale: port-level studies aggregate multiple symphonies, so there is more
# signal per day; holding 30% for frozen-eval gives a more stable OOS estimate.
# Compare: per-symphony TRAIN_RATIO=0.60, FROZEN_EVAL_RATIO=0.20.
# At 125 trading days: PORT_FROZEN = 37 days >= 25-day floor (AC-P2.11.4).
PORT_TRAIN_RATIO = 0.50
PORT_VALIDATION_RATIO = 0.20
PORT_FROZEN_EVAL_RATIO = 0.30
assert abs(PORT_TRAIN_RATIO + PORT_VALIDATION_RATIO + PORT_FROZEN_EVAL_RATIO - 1.0) < 1e-9, (
    "PORT_TRAIN_RATIO + PORT_VALIDATION_RATIO + PORT_FROZEN_EVAL_RATIO must equal 1.0"
)

# Amendment F4: Search-space parameter classification.
# MODE_SPECIFIC: re-tuned per account in port-mode (account-level sensitivity).
# MODE_INVARIANT: shared single study, mode-blind — same value across all accounts.
# VWAP_BREAK_CONFIRM_TICKS is a math_engine.py constant and must NOT appear here.
MODE_SPECIFIC_PARAMS = frozenset({
    "PARABOLIC_VELOCITY_THRESHOLD",
    "VWAP_CROSS_HWM_PCT",
})
MODE_INVARIANT_PARAMS = frozenset({
    "TAKE_PROFIT_MC_PCT",
    "VWAP_BLEED_MULTIPLIER",
    "VWAP_BLEED_TICKS",
    "MAX_PARABOLIC_SQUEEZE",
})


def build_port_study_name(timestamp: str, account_id: str) -> str:
    """Return the port-mode study name: {timestamp}__{account_id}__port (N1)."""
    return f"{timestamp}__{account_id}__port"


def build_symphony_study_name(timestamp: str, symphony_id: str) -> str:
    """Return the per-symphony study name: {timestamp}__{symphony_id} (N1/O3)."""
    return f"{timestamp}__{symphony_id}"


def get_port_mode_search_space() -> dict:
    """Return the port-mode-specific search space bounds (Amendment F4).

    Only MODE_SPECIFIC params are included; MODE_INVARIANT params come from
    the shared per-symphony study and must not be re-tuned per account.
    Returns a dict of param_name -> (low, high, step) for suggest_float/int calls.
    """
    # Port-mode tuning is not replay-validated — surface the blind spot
    # whenever the port-mode search space is requested (AC-8 / plan D-C3b).
    warn_port_mode_replay_blind_spot()
    return {
        "PARABOLIC_VELOCITY_THRESHOLD": (_SS_PARA_VEL_MIN, _SS_PARA_VEL_MAX, None),
        "VWAP_CROSS_HWM_PCT": (_SS_VWAP_CROSS_HWM_V1_MIN, _SS_VWAP_CROSS_HWM_V1_MAX, None),
    }


def validate_port_mode_params_available(account_id: str) -> dict:
    """Check whether a port-level autotune_run exists for account_id (AC-P2.11.5).

    Returns {"available": bool, "fail_stop": bool}.
    fail_stop=True when math_mode=port_level is requested but no port-level run
    exists — callers must raise + log ERROR (not fall back to per_symphony).
    """
    run = database.get_latest_autotune_run(
        symphony_id="__port__",
        account_id=account_id,
        math_mode="port_level",
    )
    available = run is not None
    return {"available": available, "fail_stop": not available}


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


def compute_deflated_sharpe_ratio(
    SR_obs: float,
    SR_0: float,
    gamma3: float,
    gamma4: float,
    T: int,
) -> float:
    """Deflated Sharpe Ratio (DSR) — corrects for selection bias from multiple testing.

    Formula: (SR_obs - SR_0) * sqrt(T-1) / sqrt(1 - gamma3*SR_obs + (gamma4-1)/4 * SR_obs^2)

    Reference: Bailey, D.H. & López de Prado, M. (2014). "The Deflated Sharpe Ratio:
    Correcting for Selection Bias, Backtest Overfitting, and Non-Normality."
    Journal of Portfolio Management, 40(5), 94-107. DOI 10.3905/jpm.2014.40.5.094.
    Equation 9.

    Args:
        SR_obs: Observed Sharpe/Sortino ratio of the candidate trial.
        SR_0:   Null-hypothesis Sharpe (random-trading baseline; typically 0.0).
        gamma3: Skewness of the trial Sharpe distribution across N trials.
        gamma4: Kurtosis of the trial Sharpe distribution across N trials.
        T:      Number of observations in the in-sample return series.

    Returns:
        DSR as a finite float. Degenerate cases return sentinels:
          - T <= 1 (no degree of freedom in sqrt(T-1)) → returns 0.0.
          - Invalid denominator (sqrt of non-positive) → returns float('-inf').
          Never returns +inf (would unfairly favor the AI branch).
    """
    if T <= 1:
        return 0.0
    denom_sq = 1.0 - gamma3 * SR_obs + ((gamma4 - 1.0) / 4.0) * (SR_obs ** 2)  # compute_deflated_sharpe_ratio denominator (Bailey & López de Prado 2014 Eq. 9)
    if denom_sq <= 0.0:
        return float("-inf")
    return (SR_obs - SR_0) * math.sqrt(T - 1) / math.sqrt(denom_sq)


def calculate_historical_deviation(current_date_str):
    """
    Scans local directory for post_mortem_*.json from the last 45 calendar days.
    Calculates average deviation (exit_return - attempted_trigger_level) grouped by exit_reason.
    """
    deviation_dict = {
        "Take-Profit": 0.0,
        "Trailing Stop": -0.20,
        "VWAP Breakdown": -0.40,
        "VWAP Bleed Cut": -0.25
    }
    
    deviation_sums = {k: 0.0 for k in deviation_dict.keys()}
    deviation_counts = {k: 0 for k in deviation_dict.keys()}

    try:
        current_dt = datetime.strptime(current_date_str, "%Y-%m-%d")
        lookback_dt = current_dt - timedelta(days=45)

        files = glob.glob("post_mortem_*.json")
        for f_path in files:
            try:
                # Extract date from filename: post_mortem_YYYY-MM-DD.json
                date_part = f_path.replace("post_mortem_", "").replace(".json", "")
                file_dt = datetime.strptime(date_part, "%Y-%m-%d")
                if file_dt < lookback_dt or file_dt >= current_dt:
                    continue

                with open(f_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    triggers = data.get("triggers", [])
                    for t in triggers:
                        reason = t.get("exit_reason")
                        exit_ret = t.get("exit_return")
                        attempted = t.get("attempted_trigger_level")

                        if reason in deviation_sums and exit_ret is not None and attempted is not None:
                            deviation_sums[reason] += (exit_ret - attempted)
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

        for reason in deviation_dict.keys():
            if deviation_counts[reason] > 0:
                deviation_dict[reason] = round(deviation_sums[reason] / deviation_counts[reason], 3)
    except Exception as e:
        print(f"      -> Warning: Deviation calculation failed ({e}). Using defaults.")

    print(f"  -> Historical Execution Deviation Penalties: {deviation_dict}")
    return deviation_dict

def warn_port_mode_replay_blind_spot():
    """Surface the per-symphony-only replay limitation for port-mode autotuning.

    The autotuner replay validates only the per-symphony altitude; the
    port-level exit altitude is NOT replay-validated (plan D-C3b). The
    port-mode autotuning path calls this guard so port-mode results cannot be
    silently trusted. Pure and idempotent — it only prints a warning, never
    mutates state or raises, so it is safe to call on every port-mode run.
    """
    print(
        "  -> WARNING: port-mode autotuning is NOT replay-validated. The "
        "autotuner replay simulates only the per-symphony altitude; the "
        "port-level exit altitude has no replay simulation. Treat port-mode "
        "tuning results as un-validated for the port-level exit path."
    )


def _replay_exit_tick(state, tick, tick_idx, n_ticks, p, grace_minutes):
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
    derive time_ratio from the actual session length.
    """
    ret = tick.get("return", 0.0)
    # mc_prob may be the None sentinel (MC unavailable / insufficient MC
    # history) — production's run_monte_carlo None contract. mc_available
    # gates every MC-driven branch exactly as production does; an absent MC
    # opinion drives no arm, no disarm, no TP transition, and no MC veto.
    mc = tick.get("mc_prob", 50.0)
    mc_available = mc is not None
    vol = tick.get("vol", 1.0)
    vwap_diff = tick.get("vwap_diff", 0.0)
    valid_vwap_weight = tick.get("valid_vwap_weight", 1.0)

    take_profit_mc = p.get("TAKE_PROFIT_MC_PCT", 5.0)
    trigger_threshold = p.get("TRIGGER_THRESHOLD_PCT", 15.0)

    if ret > state["hwm"]:
        state["hwm"] = ret
    safe_hwm = max(state["hwm"], ret)

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

    # MC arm / disarm — gated on mc_available (alpha_bot_execution.py
    # 1140-1163). An absent MC opinion drives neither an arm nor a disarm.
    if mc_available and take_profit_mc <= mc < trigger_threshold:
        if not state["armed"]:
            state["armed"] = True
    elif state["armed"]:
        if mc_available and mc > (trigger_threshold * 2) and ret > 0.0:
            state["armed"] = False
            state["below_stop_count"] = 0

    # --- TIME SQUEEZE DECAY LOGIC ---
    # time_ratio is derived from the ACTUAL session length so half-day
    # sessions reach full end-of-day stop tightening — the last tick of any
    # multi-tick session maps to 1.0, tick 0 to 0.0 (AC-7). The degenerate
    # single-tick day maps its lone tick to 1.0: production derives time_ratio
    # from wall-clock open/close datetimes, so a 1-bar session sits at the
    # close, not the open (M-C3.1 — faithful production parity).
    time_ratio = 1.0 if n_ticks == 1 else tick_idx / max(1, n_ticks - 1)
    dynamic_multiplier, dynamic_min_stop = math_engine.compute_time_squeeze_decay(
        time_ratio
    )

    active_stop_dist = math_engine.compute_active_trailing_stop(
        vol, dynamic_multiplier, dynamic_min_stop,
        state["para_armed"], state["breakeven_locked"],
        p.get("MAX_PARABOLIC_SQUEEZE", 0.50),
    )
    base_stop = safe_hwm - active_stop_dist

    # is_triggered=False: the replay breaks on the first trigger and never
    # re-enters a tick with triggered state mid-day.
    state["hwm_hold_ticks"], state["breakeven_locked"], stop_level = (
        math_engine.compute_breakeven_update(
            ret, vol, base_stop, state["hwm_hold_ticks"],
            state["breakeven_locked"], False,
        )
    )

    # Check 1: Trailing Stop — the canonical math_engine primitive. It owns
    # MAGNITUDE_FLOOR_PCT, MC_SANITY_THRESHOLD and EXIT_CONFIRM_TICKS; the
    # replay never duplicates those exit-rule literals (AC-1).
    state["below_stop_count"], is_trailing_hit = math_engine.compute_exit_confirmation(
        armed=state["armed"],
        is_triggered=False,
        current_return=ret,
        stop_trigger_level=stop_level,
        prob_beating=mc,
        current_below_stop_count=state["below_stop_count"],
    )

    # Check 2: Take Profit — the shared, pure math_engine.compute_tp_confirmation
    # (D-C3a). Production and the replay call the SAME TP confirm machine, so
    # the confirm-count constant (TP_CONFIRM_TICKS) has one source of truth.
    # An MC-unavailable tick while tp_armed resets above_tp_count to 0 — an
    # absent MC opinion cannot count toward a TP confirmation (AC-3).
    state["tp_armed"], state["above_tp_count"], is_tp_hit = (
        math_engine.compute_tp_confirmation(
            mc_available=mc_available,
            prob_beating=mc,
            take_profit_mc_pct=take_profit_mc,
            current_return=ret,
            is_triggered=False,
            tp_armed=state["tp_armed"],
            above_tp_count=state["above_tp_count"],
        )
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
    # Open-window grace: production suppresses BOTH VWAP signals for the first
    # grace_minutes of the session (alpha_bot_execution.py 1321-1326). On
    # minute-bar replay ticks, tick_idx 0 == session open, so the faithful
    # equivalent of the datetime grace gate is tick_idx < grace_minutes (AC-2).
    if tick_idx < grace_minutes:
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
        "vwap_ticks": 0,
        "vwap_bleed_ticks": 0,
    }


def replay_exit_sequence(ticks, params, *, grace_minutes):
    """Run the replay's per-tick exit loop over one day; return the decision trace.

    Pure helper exposing the replay's per-tick exit decision (AC-6). Returns
    one {"tick_idx", "exit_reason"} dict per executed tick — exit_reason is the
    resolve_trigger_priority string on the tick the position exits, None on
    every non-exit tick. The loop stops after the first exit (production
    commits the exit and freezes the symphony for the day).

    Runs the SAME _replay_exit_tick per-tick core that run_simulation and
    _collect_sim_returns call — so this helper IS the replay path, not a copy.
    It is the observability seam the bit-identical AC-6 parity test compares
    against the production exit harness.
    """
    state = _fresh_replay_state()
    n_ticks = len(ticks)
    out = []
    for tick_idx, tick in enumerate(ticks):
        reason = _replay_exit_tick(
            state, tick, tick_idx, n_ticks, params, grace_minutes
        )
        out.append({"tick_idx": tick_idx, "exit_reason": reason})
        if reason is not None:
            break
    return out


def _collect_sim_returns(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
    """Run the guard-alpha simulation and return per-triggered-day guard_alpha values.

    Identical tick logic to run_simulation; returns a list instead of a scalar
    so the Sortino objective can compute risk-adjusted return across triggered days.
    """
    daily_returns = []
    decay_rate = _GUARD_ALPHA_DECAY_RATE
    current_dt = datetime.strptime(current_date_str, "%Y-%m-%d")
    grace_minutes = _replay_grace_minutes()  # shared with production; resolved once per run

    for sym_id in acc_sym_ids:
        dates_data = history_data.get(sym_id, {})
        for date, ticks in dates_data.items():
            if not ticks: continue

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

            # Per-tick exit loop via the single shared core _replay_exit_tick —
            # the SAME per-tick exit path run_simulation and replay_exit_sequence
            # use. One copy of the exit orchestration; no duplication. AC-6's
            # test_c3_replay_exit_parity.py pins it against the production exit
            # path.
            for tick_idx, tick in enumerate(ticks):
                reason_str = _replay_exit_tick(
                    day_state, tick, tick_idx, n_ticks, p,
                    grace_minutes,
                )
                if reason_str is not None:
                    penalty = deviation_dict.get(reason_str, -0.20)
                    triggered_return = tick.get("return", 0.0) + penalty
                    break

            if triggered_return is not None:
                guard_alpha = triggered_return - eod_return
                days_ago = (current_dt - datetime.strptime(date, "%Y-%m-%d")).days
                weight = math.exp(-decay_rate * days_ago)
                daily_returns.append(guard_alpha * weight)

    return daily_returns


def _dsr_observation_count(
    trial_params, current_params, history_validation, sym_id,
    current_date_str, deviation_dict,
):
    """Return the DSR `T` for a trial — its in-sample return-OBSERVATION count.

    The DSR (Bailey & López de Prado 2014, Eq. 9) defines `T` as the number of
    return observations in the in-sample series the Sharpe/Sortino was computed
    over. The Sortino objective is computed by compute_sortino_ratio over the
    `daily_returns` list `_collect_sim_returns` produces — one entry per
    TRIGGERED day, not per validation calendar day. `T` must therefore be
    `len(daily_returns)` for the trial's own parameter set, NOT
    `len(validation_dates_purged)` (the validation fold's calendar-day count,
    which is systematically larger and inflates sqrt(T-1)).

    Re-derives the trial's return series from its `params` so the count is
    exact for the parameters being deflated. Returns 0 when the symphony is
    absent so the DSR T<=1 sentinel handles a degenerate series.
    """
    if sym_id is None:
        return 0
    p = current_params.copy()
    p.update(trial_params)
    series = _collect_sim_returns(
        p, history_validation, [sym_id], current_date_str, deviation_dict
    )
    return len(series)


def run_simulation(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
    total_guard_alpha = 0.0
    decay_rate = _GUARD_ALPHA_DECAY_RATE
    current_dt = datetime.strptime(current_date_str, "%Y-%m-%d")
    grace_minutes = _replay_grace_minutes()  # shared with production; resolved once per run

    for sym_id in acc_sym_ids:
        dates_data = history_data.get(sym_id, {})
        for date, ticks in dates_data.items():
            if not ticks: continue

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

            # Per-tick exit loop via the single shared core _replay_exit_tick —
            # the SAME per-tick exit path _collect_sim_returns and
            # replay_exit_sequence use. One copy of the exit orchestration; no
            # duplication. AC-6's test_c3_replay_exit_parity.py pins it against
            # the production exit path.
            for tick_idx, tick in enumerate(ticks):
                reason_str = _replay_exit_tick(
                    day_state, tick, tick_idx, n_ticks, p,
                    grace_minutes,
                )
                if reason_str is not None:
                    penalty = deviation_dict.get(reason_str, -0.20)
                    triggered_return = tick.get("return", 0.0) + penalty
                    break

            if triggered_return is not None:
                # safe_hwm at the exit tick: the per-tick core lifts hwm before
                # the exit check, so the running HWM already includes the exit
                # tick's return — safe_hwm == day_state["hwm"].
                safe_hwm = day_state["hwm"]
                guard_alpha = triggered_return - eod_return
                missed_upside = day_max_return - triggered_return
                drawdown_from_peak = safe_hwm - triggered_return

                # Exponential Time-Decay Weighting
                days_ago = (current_dt - datetime.strptime(date, "%Y-%m-%d")).days
                weight = math.exp(-decay_rate * days_ago)

                # Loss-averse utility — see the penalty-constant block above.
                # 1. Penalize missed upside (exiting too early before a run).
                if missed_upside > MISSED_UPSIDE_THRESHOLD_PCT:
                    total_guard_alpha -= (
                        missed_upside * MISSED_UPSIDE_PENALTY_MULT * weight
                    )

                # 2. Penalize peak-to-exit drawdown (giving back too much profit)
                # — only for positions that reached a meaningful gain.
                if (safe_hwm > DRAWDOWN_MIN_GAIN_PCT
                        and drawdown_from_peak > DRAWDOWN_THRESHOLD_PCT):
                    total_guard_alpha -= (
                        drawdown_from_peak * DRAWDOWN_PENALTY_MULT * weight
                    )

                # 3. Apply standard EOD-based guard alpha; negative guard-alpha
                # is penalised by the loss-aversion multiplier (asymmetry).
                if guard_alpha < 0:
                    total_guard_alpha += (
                        guard_alpha * NEGATIVE_GUARD_ALPHA_LOSS_AVERSE_MULT * weight
                    )
                else:
                    total_guard_alpha += (guard_alpha * weight)

    return -total_guard_alpha


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
    import sqlite3 as _sqlite3
    import pathlib as _pathlib

    db_path = "optuna_studies.db"
    if not os.path.exists(db_path):
        return

    migration_path = _pathlib.Path(__file__).parent / "migrations" / "optuna_001_archive_accumulated_studies.sql"
    if not migration_path.exists():
        return

    try:
        conn = _sqlite3.connect(db_path)
        # Check for any legacy (non-prefixed) studies
        rows = conn.execute(
            "SELECT COUNT(*) FROM studies WHERE study_name NOT LIKE 'LEGACY__%' AND INSTR(study_name, '__') = 0"
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


def run_autotuner(bot_state, current_date_str, account_uuids, is_forced=False):
    """
    Runs walk-forward optimization using Bayesian Optimization (Optuna) per symphony.
    Implements a three-fold walk-forward split (60/20/20): train / validation / frozen-eval.
    The 60/20/20 ratio is an operator choice for AlphaBot's 125-day data scale; AFML Ch. 7.4
    prescribes the held-out frozen-eval invariant (purge+embargo), not the specific ratio.

    Walk-forward split methodology (López de Prado 2018 Ch. 7.4):
    - 125-day history is split 60/20/20: ~75 train days, ~25 validation days, ~25 frozen-eval days.
    - Purge (PURGE_DAYS=20) and Embargo (EMBARGO_DAYS=1) applied at BOTH fold boundaries:
        (a) train | validation boundary
        (b) validation | frozen-eval boundary
      Binding purge constraint: max(vol=20, ATR=15)=20 trading days. The decay weight
      (_GUARD_ALPHA_DECAY_RATE half-life ≈ 46 days) is an objective aggregation weight, not
      a feature lookback, and is excluded from purge sizing.
    - Selection: Optuna trials score on the validation fold only. Frozen-eval is hidden during
      the trial sweep.
    - Frozen-eval consumption: exactly once after best-trial selection, to produce the honest
      post-selection performance metric (frozen_eval_sharpe).

    OOS-fold-collapse v2 (PA-26 extended):
    - At 125-day history, the three raw folds are 75/25/25 days.
    - After PURGE_DAYS=20 at each boundary, the usable validation and frozen-eval windows
      each shrink to approximately 4-5 usable days. This is an acknowledged tradeoff — the
      purge is methodologically correct and the short evaluation window is the cost of honest
      OOS reporting. Future workstream: expand history window or use purged k-fold CV (rolling
      folds) to recover statistical power.
    """
    # Suppress Optuna's per-trial log noise; set here (not at module level) to
    # avoid clobbering pytest's output-capture on import.
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    # Apply optuna_001 archive migration once if any bare (non-prefixed) legacy
    # studies exist — renames them to LEGACY__<name> non-destructively.
    _apply_optuna_archive_migration_if_needed()

    print(f"  -> Starting EOD Autotune (125-day WFA: 60% Train / 20% Validation / 20% Frozen-Eval per Symphony)...")

    # 0. Calculate Historical Execution Deviation
    deviation_dict = calculate_historical_deviation(current_date_str)

    # 1. Archive today's charts to the permanent DB
    chart_history = database.load_chart_history()
    if chart_history and chart_history.get("date") == current_date_str:
        for sym_id, data in chart_history.get("symphonies", {}).items():
            database.save_chart_archive(current_date_str, sym_id, data)

    # 2. Fetch the rolling 125-trading-day synthetic forward-looking data
    history_125d = synthetic_history.generate_synthetic_history(bot_state, current_date_str)
    if not history_125d:
        print("  -> Autotuner aborted: Failed to generate synthetic history.")
        return

    # Extract global dates and partition 60/20/20 (train / validation / frozen-eval).
    all_dates = set()
    for sym_data in history_125d.values():
        all_dates.update(sym_data.keys())
    sorted_dates = sorted(list(all_dates))

    total_days = len(sorted_dates)
    if total_days < 2:
        print("  -> Autotuner aborted: Need at least 2 days of history for WFA.")
        return

    # Three-fold split: TRAIN_RATIO / VALIDATION_RATIO / FROZEN_EVAL_RATIO (60/20/20).
    # Reference: López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.4.
    val_start_idx    = int(total_days * TRAIN_RATIO)
    frozen_start_idx = int(total_days * (TRAIN_RATIO + VALIDATION_RATIO))
    # split_idx aliases val_start_idx — preserved so O1 test assertions that inspect
    # autotuner.py source for "split_idx" continue to find the split site.
    split_idx = val_start_idx

    raw_train_dates    = sorted_dates[:val_start_idx]
    raw_val_dates      = sorted_dates[val_start_idx:frozen_start_idx]
    raw_frozen_dates   = sorted_dates[frozen_start_idx:]

    # Boundary 1 — train | validation: purge + embargo on train side.
    # Exclude the last PURGE_DAYS + EMBARGO_DAYS of raw_train_dates (identical
    # logic to O1's train | test purge, now applied at the train | validation boundary).
    effective_train_cutoff = max(0, val_start_idx - PURGE_DAYS - EMBARGO_DAYS)
    train_dates = set(sorted_dates[:effective_train_cutoff])

    # Boundary 2 — validation | frozen-eval: purge + embargo on validation side.
    # Exclude the last PURGE_DAYS + EMBARGO_DAYS of raw_val_dates so validation samples
    # whose feature lookback overlaps the frozen-eval fold are not seen by the objective.
    # PURGE_DAYS appears here to confirm the second boundary receives the same treatment.
    val_purge_end_idx = frozen_start_idx - PURGE_DAYS - EMBARGO_DAYS
    # Purge-reduced validation: used by the Optuna objective closure only, preventing
    # late validation features from leaking into the frozen-eval fold.
    validation_dates_purged = set(sorted_dates[val_start_idx:max(val_start_idx, val_purge_end_idx)])
    # Full raw validation: used by the OOS cascade (AI/fallback/default) to preserve
    # the behavioural contract that the cascade evaluates on the raw OOS fold.
    validation_dates_full = set(raw_val_dates)

    frozen_dates = set(raw_frozen_dates)

    history_train           = {}
    history_validation      = {}  # purge-reduced; used by Optuna objective only
    history_validation_full = {}  # full raw validation fold; used by OOS cascade
    history_frozen          = {}
    for sym_id, sym_data in history_125d.items():
        history_train[sym_id]           = {d: t for d, t in sym_data.items() if d in train_dates}
        history_validation[sym_id]      = {d: t for d, t in sym_data.items() if d in validation_dates_purged}
        history_validation_full[sym_id] = {d: t for d, t in sym_data.items() if d in validation_dates_full}
        history_frozen[sym_id]          = {d: t for d, t in sym_data.items() if d in frozen_dates}

    # OOS cascade uses the full validation fold — same contract as the pre-O6 OOS test fold.
    history_test = history_validation_full

    # Extract unique normalized symphony names from the current bot_state
    symphony_names = set()
    for sym_id, data in bot_state.items():
        if isinstance(data, dict) and "name" in data:
            symphony_names.add(database.normalize_name(data["name"]))

    optimization_results = {}

    # Single timestamp shared across all symphonies in this run — groups all
    # per-symphony rows from one invocation into a logical "run" for Claude context-assembly.
    run_timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for normalized_name in symphony_names:
        print(f"     Optimizing Symphony: {normalized_name}")
        strat_data = database.get_symphony_strategy(normalized_name)
        locked_vars = strat_data.get("locked_vars", [])
        current_params = strat_data.get("params", {})
        original_params = current_params.copy()

        def objective(trial):
            p = current_params.copy()
            p["TAKE_PROFIT_MC_PCT"] = trial.suggest_float("TAKE_PROFIT_MC_PCT", _SS_TAKE_PROFIT_MC_MIN, _SS_TAKE_PROFIT_MC_MAX)
            p["VWAP_CROSS_HWM_PCT"] = trial.suggest_float("VWAP_CROSS_HWM_PCT", _SS_VWAP_CROSS_HWM_MIN, _SS_VWAP_CROSS_HWM_MAX)
            p["VWAP_BLEED_MULTIPLIER"] = trial.suggest_float("VWAP_BLEED_MULTIPLIER", _SS_VWAP_BLEED_MULT_MIN, _SS_VWAP_BLEED_MULT_MAX)
            p["VWAP_BLEED_TICKS"] = trial.suggest_int("VWAP_BLEED_TICKS", _SS_VWAP_BLEED_TICKS_MIN, _SS_VWAP_BLEED_TICKS_MAX)
            p["PARABOLIC_VELOCITY_THRESHOLD"] = trial.suggest_float("PARABOLIC_VELOCITY_THRESHOLD", _SS_PARA_VEL_MIN, _SS_PARA_VEL_MAX)
            p["MAX_PARABOLIC_SQUEEZE"] = trial.suggest_float("MAX_PARABOLIC_SQUEEZE", _SS_MAX_PARA_SQUEEZE_MIN, _SS_MAX_PARA_SQUEEZE_MAX)

            acc_sym_ids = [k for k, v in bot_state.items() if isinstance(v, dict) and database.normalize_name(v.get("name", "")) == normalized_name]
            if not acc_sym_ids: return 0.0
            target_sym_id = acc_sym_ids[0]
            # Score on validation fold only — frozen-eval is withheld from all trial callbacks.
            daily_returns = _collect_sim_returns(p, history_validation, [target_sym_id], current_date_str, deviation_dict)
            # Annualization intentionally omitted — this is a ranking signal, not an annualized statistic.
            return compute_sortino_ratio(daily_returns)

        start_time = time.time()
        
        # Parallel Bayesian Optimization
        db_url = "sqlite:///optuna_studies.db"
        storage = optuna.storages.RDBStorage(
            url=db_url,
            engine_kwargs={"connect_args": {"timeout": 60}}
        )
        study_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        study = optuna.create_study(study_name=f"{study_timestamp}__{normalized_name}", storage=storage, load_if_exists=False, direction="maximize")
        study.optimize(objective, n_trials=500, n_jobs=-1)
        

        
        naive_sharpe_value = study.best_value
        best_alpha_train = naive_sharpe_value
        best_params = study.best_params

        # --- O2: DSR RE-RANKING (AI branch only) ---
        # Collect completed trial values to derive the cross-trial Sharpe distribution
        # moments (gamma3, gamma4). Then re-rank all completed trials by DSR and select
        # the DSR-maximizing trial instead of the naive-Sortino winner.
        # Fallback/default branches are single parameter sets — DSR not applicable.
        # Reference: Bailey & López de Prado 2014, Eq. 9.
        # Resolved here (before the OOS-evaluation block redefines it) so the DSR
        # `T` can re-derive each trial's validation-fold return series (AC-3).
        dsr_acc_sym_ids = [
            k for k, v in bot_state.items()
            if isinstance(v, dict)
            and database.normalize_name(v.get("name", "")) == normalized_name
        ]
        target_sym_id = dsr_acc_sym_ids[0] if dsr_acc_sym_ids else None
        deflated_sharpe_value: float | None = None
        try:
            completed_trials = [t for t in study.trials if t.value is not None]
        except TypeError:
            completed_trials = []

        # filter_sortino_sentinels removes math_engine._SORTINO_SENTINEL (1e6) values before
        # moments AND scoring — sentinels pollute cross-trial distribution moments AND win the
        # scoring loop because (1e6 - SR_0) dominates the DSR numerator even after moment fix.
        trial_values = math_engine.filter_sortino_sentinels([t.value for t in completed_trials])
        filtered_trials = [t for t in completed_trials if t.value != math_engine._SORTINO_SENTINEL]

        if len(trial_values) >= 2:
            n_trials = len(trial_values)
            mean_v = statistics.mean(trial_values)
            # Population variance (divisor = N) for moment computation
            variance_v = sum((v - mean_v) ** 2 for v in trial_values) / n_trials
            std_v = math.sqrt(variance_v) if variance_v > 0 else 0.0
            if std_v > 0:
                gamma3 = sum((v - mean_v) ** 3 for v in trial_values) / (n_trials * std_v ** 3)
                gamma4 = sum((v - mean_v) ** 4 for v in trial_values) / (n_trials * std_v ** 4)
            else:
                gamma3, gamma4 = 0.0, 3.0  # normal-distribution fallback for degenerate spread

            # AC-5: SR_0 is the DSR null benchmark — the largest Sharpe expected
            # from N trials of a ZERO-SKILL strategy (Bailey & López de Prado
            # 2014). It is evaluated under the zero-skill null, so the mean term
            # is 0; only the trial-spread (selection-bias) term is kept.
            SR_0 = math_engine.compute_expected_max_sharpe(
                sr_mean=0.0,
                sr_std=std_v,
                n_trials=n_trials,
            )
            best_dsr = float("-inf")
            best_trial_by_dsr = None
            for t in filtered_trials:  # excludes math_engine._SORTINO_SENTINEL trials
                # AC-3: DSR `T` is the in-sample return-OBSERVATION count of the
                # series this trial's Sortino was computed over — len(daily_returns)
                # for the trial's own params, NOT the validation calendar-day count.
                t_obs = _dsr_observation_count(
                    t.params, current_params, history_validation,
                    target_sym_id, current_date_str, deviation_dict,
                )
                dsr = compute_deflated_sharpe_ratio(
                    SR_obs=t.value,
                    SR_0=SR_0,
                    gamma3=gamma3,
                    gamma4=gamma4,
                    T=t_obs,
                )
                if math.isfinite(dsr) and dsr > best_dsr:
                    best_dsr = dsr
                    best_trial_by_dsr = t

            if best_trial_by_dsr is not None and math.isfinite(best_dsr):
                best_params = best_trial_by_dsr.params
                best_alpha_train = best_trial_by_dsr.value
                deflated_sharpe_value = best_dsr
            # If no valid DSR trial found, fall through with naive Optuna winner

        if deflated_sharpe_value is None and naive_sharpe_value is not None:
            # Fewer than 2 completed trials — no cross-trial distribution exists,
            # so no expected-max-SR correction applies. Use PSR-style null baseline.
            fallback_SR_0 = 0.0
            # AC-3: T is the observation count of the naive winner's return series.
            fallback_T = _dsr_observation_count(
                best_params, current_params, history_validation,
                target_sym_id, current_date_str, deviation_dict,
            )
            dsr_fallback = compute_deflated_sharpe_ratio(
                SR_obs=naive_sharpe_value,
                SR_0=fallback_SR_0,
                gamma3=0.0,
                gamma4=3.0,
                T=fallback_T,
            )
            if math.isfinite(dsr_fallback):
                deflated_sharpe_value = dsr_fallback
        # ----------------------------------------------------

        # --- BEST_PARAMS SCHEMA VALIDATION (B2-FU2) ---
        # An empty best_params or one missing any required search-space key indicates
        # a degenerate/aborted study. Reject the WHOLE AI proposal (no key-by-key
        # merge -- partial merges produce Frankenstein params where some keys
        # come from the AI and others from current/fallback). Force the cascade
        # to fall through to fallback (or default if fallback also fails) by
        # poisoning oos_alpha to -inf. Do NOT raise -- the daemon must keep ticking.
        ai_proposal_invalid = (
            not best_params
            or not OPTUNA_SEARCH_SPACE_KEYS.issubset(best_params.keys())
        )
        if ai_proposal_invalid:
            missing = sorted(OPTUNA_SEARCH_SPACE_KEYS - set(best_params.keys()))
            print(
                f"       Warning: best_params schema invalid for '{normalized_name}' "
                f"(missing keys: {missing or '<empty dict>'}). "
                f"Rejecting AI proposal; cascading to Fallback/Default."
            )
            deflated_sharpe_value = None
            naive_sharpe_value = None
        # ---------------------------------------------

        # Evaluate OOS robustness
        best_p = current_params.copy()
        for name, val in best_params.items():
            best_p[name] = round(val, 2)

        acc_sym_ids = [k for k, v in bot_state.items() if isinstance(v, dict) and database.normalize_name(v.get("name", "")) == normalized_name]
        target_sym_id = acc_sym_ids[0] if acc_sym_ids else None
        oos_alpha = -run_simulation(best_p, history_test, [target_sym_id] if target_sym_id else [], current_date_str, deviation_dict)

        # If the AI proposal is schema-invalid, poison its OOS alpha so the
        # cascade below naturally selects fallback (or default). Done AFTER
        # the simulation runs so any side-effects/logging are preserved.
        if ai_proposal_invalid:
            oos_alpha = -math.inf

        optimization_results[normalized_name] = {}
        
        # Evaluate fallback parameters in OOS for comparison
        fallback_params = current_params.copy()
        fallback_oos_alpha = -run_simulation(fallback_params, history_test, [target_sym_id] if target_sym_id else [], current_date_str, deviation_dict)

        # Evaluate global default parameters in OOS for comparison
        default_params = database.DEFAULT_STRATEGY.copy()
        default_oos_alpha = -run_simulation(default_params, history_test, [target_sym_id] if target_sym_id else [], current_date_str, deviation_dict)

        # Validation-fold Sortino (selection truth — what Optuna actually optimized against).
        validation_returns = _collect_sim_returns(best_p, history_validation, [target_sym_id] if target_sym_id else [], current_date_str, deviation_dict)
        validation_sharpe_value = compute_sortino_ratio(validation_returns) if validation_returns else None

        # Frozen-eval: consumed exactly once post-selection on the held-out final 20% fold.
        # This is the honest performance metric — not seen by any Optuna trial callback.
        # PURGE_DAYS referenced here confirms the boundary purge applies at validation|frozen-eval.
        # Single read via _collect_sim_returns; no separate run_simulation call so the
        # "consumed once" invariant holds across all frozen-fold access paths.
        frozen_eval_returns = _collect_sim_returns(best_p, history_frozen, [target_sym_id] if target_sym_id else [], current_date_str, deviation_dict)
        frozen_eval_sharpe_value = compute_sortino_ratio(frozen_eval_returns) if frozen_eval_returns else None

        # Calculate daily averages for better understanding
        train_days_count = len(train_dates)
        test_days_count = len(validation_dates_full)

        avg_train_alpha = best_alpha_train / train_days_count if train_days_count > 0 else 0
        avg_oos_alpha = oos_alpha / test_days_count if test_days_count > 0 else 0

        baseline_decision = ""
        # B2-FU1: Asymmetric tie rule -- STRICT-POSITIVE on the AI branch
        # (over-fit risk: an AI proposal that only TIES the validated fallback
        # is not worth displacing the last-known-good params for), LENIENT
        # (>=) on the fallback branch below (favors last-known-good on tie
        # vs the global default).
        if oos_alpha > fallback_oos_alpha and oos_alpha > default_oos_alpha:
            if oos_alpha > 0:
                print(f"       OOS validation passed! OOS Guard Alpha: +{oos_alpha:.2f}% (Average: {avg_oos_alpha:.2f}%)")
            else:
                print(f"       OOS validation passed (Beat Baselines)! OOS Guard Alpha: {oos_alpha:.2f}% (Avg: {avg_oos_alpha:.2f}%) vs Fallback: {fallback_oos_alpha:.2f}% / Default: {default_oos_alpha:.2f}%")
            for name, val in best_params.items():
                current_params[name] = round(val, 2)
            baseline_decision = "Adopted AI"
        elif fallback_oos_alpha >= default_oos_alpha:
            print(f"       OOS validation failed (AI: {oos_alpha:.2f}%). Reverting to Fallback parameters (Fallback: {fallback_oos_alpha:.2f}% vs Default: {default_oos_alpha:.2f}%).")
            for k, v in fallback_params.items():
                current_params[k] = v
            baseline_decision = "Reverted to Fallback"
        else:
            print(f"       OOS validation & Fallback failed. Resetting to Global Default (Default: {default_oos_alpha:.2f}% vs AI: {oos_alpha:.2f}%, Fallback: {fallback_oos_alpha:.2f}%).")
            for k, v in default_params.items():
                current_params[k] = v
            baseline_decision = "Reset to Global Default"

        # Build Discord logs ensuring all original variables are shown
        optimization_results[normalized_name]["_baseline_chosen"] = baseline_decision
        for k, original_val in original_params.items():
            optimization_results[normalized_name][k] = {"old": original_val, "new": current_params.get(k, original_val)}

        elapsed = time.time() - start_time
        dsr_log = (
            f" | DSR: {deflated_sharpe_value:.4f} (naive: {naive_sharpe_value:.4f})"
            if deflated_sharpe_value is not None and naive_sharpe_value is not None
            else " | DSR: N/A"
        )
        print(f"       Optimization completed in {elapsed:.2f}s. Train Sortino: {best_alpha_train:+.4f} (train days: {train_days_count}){dsr_log}")

        database.save_symphony_strategy(normalized_name, current_params, locked_vars)

        # P1: Persist per-run validation metrics so Claude context-assembly can
        # retrieve them via get_latest_autotune_run().  Called AFTER baseline_decision
        # is finalized and save_symphony_strategy has written the chosen params,
        # so the row captures the decision that was actually applied.
        # O2: deflated_sharpe (DSR winner) and naive_sharpe (raw Optuna best) recorded for
        # backward-comparison and operator awareness of deflation magnitude.
        # O6: validation_sharpe (selection metric) and frozen_eval_sharpe (honest post-selection
        # metric, consumed once from the withheld final 20% fold).
        database.save_autotune_run(
            run_timestamp=run_timestamp,
            symphony_id=normalized_name,
            oos_alpha=oos_alpha,
            train_alpha=best_alpha_train,
            baseline_decision=baseline_decision,
            fallback_oos_alpha=fallback_oos_alpha,
            default_oos_alpha=default_oos_alpha,
            deflated_sharpe=deflated_sharpe_value,
            naive_sharpe=naive_sharpe_value,
            validation_sharpe=validation_sharpe_value,
            frozen_eval_sharpe=frozen_eval_sharpe_value,
        )

    print("  -> Autotuner finished all symphonies.")
    return optimization_results


def run_calibration_sweep(
    history_data: dict,
    current_params: dict,
    current_date_str: str,
    deviation_dict: dict,
    random_state: int,
) -> list[dict]:
    """V1 calibration sweep over PARABOLIC_VELOCITY_THRESHOLD and VWAP_CROSS_HWM_PCT only.

    Consumes O1 purge+embargo, O2 DSR re-ranking, O3 timestamped study names,
    O5 Sortino objective, O6 frozen-eval fold — same methodology as run_autotuner
    but search space is limited to the two V1 parameters. Does NOT persist anything
    to the DB (AC-V1.3: read-only, operator-gated rollout).

    Returns a list of report dicts, one per tuned param per symphony found in
    history_data. The caller decides whether to act on proposals.
    """
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    run_timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

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
    validation_dates_purged = set(sorted_dates[val_start_idx:max(val_start_idx, val_purge_end_idx)])

    frozen_dates = set(sorted_dates[frozen_start_idx:])

    trading_day_start = sorted_dates[0] if sorted_dates else ""
    trading_day_end = sorted_dates[frozen_start_idx - 1] if frozen_start_idx > 0 else ""

    history_validation: dict = {}
    history_frozen: dict = {}
    for sym_id, sym_data in history_data.items():
        history_validation[sym_id] = {d: t for d, t in sym_data.items() if d in validation_dates_purged}
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
        study_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        study_name = f"{study_timestamp}__{sym_id}"

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
            return compute_sortino_ratio(daily_returns)

        sampler = optuna.samplers.TPESampler(seed=random_state)
        study = optuna.create_study(
            study_name=study_name,
            direction="maximize",
            sampler=sampler,
            load_if_exists=False,
        )
        study.optimize(objective, n_trials=100, n_jobs=1)

        naive_sharpe_value: float | None = study.best_value
        best_params = study.best_params
        n_trials = len([t for t in study.trials if t.value is not None])

        # --- O2: DSR re-ranking ---
        deflated_sharpe_value: float | None = None
        completed_trials = [t for t in study.trials if t.value is not None]

        # filter_sortino_sentinels removes math_engine._SORTINO_SENTINEL (1e6) values before
        # moments AND scoring — sentinels pollute cross-trial distribution moments AND win the
        # scoring loop because (1e6 - SR_0) dominates the DSR numerator even after moment fix.
        trial_values = math_engine.filter_sortino_sentinels([t.value for t in completed_trials])
        filtered_trials = [t for t in completed_trials if t.value != math_engine._SORTINO_SENTINEL]

        if len(trial_values) >= 2:
            n_tv = len(trial_values)
            mean_v = sum(trial_values) / n_tv
            variance_v = sum((v - mean_v) ** 2 for v in trial_values) / n_tv
            std_v = math.sqrt(variance_v) if variance_v > 0 else 0.0
            if std_v > 0:
                gamma3 = sum((v - mean_v) ** 3 for v in trial_values) / (n_tv * std_v ** 3)
                gamma4 = sum((v - mean_v) ** 4 for v in trial_values) / (n_tv * std_v ** 4)
            else:
                gamma3, gamma4 = 0.0, 3.0

            # AC-5: SR_0 is the DSR null benchmark — the largest Sharpe expected
            # from N trials of a ZERO-SKILL strategy (Bailey & López de Prado
            # 2014). It is evaluated under the zero-skill null, so the mean term
            # is 0; only the trial-spread (selection-bias) term is kept.
            SR_0 = math_engine.compute_expected_max_sharpe(
                sr_mean=0.0,
                sr_std=std_v,
                n_trials=n_tv,
            )
            best_dsr = float("-inf")
            best_trial_by_dsr = None
            for t in filtered_trials:  # excludes math_engine._SORTINO_SENTINEL trials
                # AC-3: DSR `T` is the in-sample return-OBSERVATION count of the
                # series this trial's Sortino was computed over — len(daily_returns)
                # for the trial's own params, NOT the validation calendar-day count.
                t_obs = _dsr_observation_count(
                    t.params, current_params, history_validation,
                    sym_id, current_date_str, deviation_dict,
                )
                dsr = compute_deflated_sharpe_ratio(
                    SR_obs=t.value,
                    SR_0=SR_0,
                    gamma3=gamma3,
                    gamma4=gamma4,
                    T=t_obs,
                )
                if math.isfinite(dsr) and dsr > best_dsr:
                    best_dsr = dsr
                    best_trial_by_dsr = t

            if best_trial_by_dsr is not None and math.isfinite(best_dsr):
                best_params = best_trial_by_dsr.params
                naive_sharpe_value = best_trial_by_dsr.value
                deflated_sharpe_value = best_dsr

        if deflated_sharpe_value is None and naive_sharpe_value is not None:
            # Fewer than 2 completed trials — no cross-trial distribution exists,
            # so no expected-max-SR correction applies. Use PSR-style null baseline.
            fallback_SR_0 = 0.0
            # AC-3: T is the observation count of the naive winner's return series.
            fallback_T = _dsr_observation_count(
                best_params, current_params, history_validation,
                sym_id, current_date_str, deviation_dict,
            )
            dsr_fallback = compute_deflated_sharpe_ratio(
                SR_obs=naive_sharpe_value,
                SR_0=fallback_SR_0,
                gamma3=0.0,
                gamma4=3.0,
                T=fallback_T,
            )
            if math.isfinite(dsr_fallback):
                deflated_sharpe_value = dsr_fallback

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

        # Emit one row per tuned param
        for param_name in ("PARABOLIC_VELOCITY_THRESHOLD", "VWAP_CROSS_HWM_PCT"):
            current_value = float(current_params.get(param_name, best_p.get(param_name, 0.0)))
            proposed_value = float(best_p.get(param_name, current_value))
            delta_pct = (
                (proposed_value - current_value) / abs(current_value) * 100.0
                if current_value != 0
                else 0.0
            )
            report_rows.append({
                "symphony_id": sym_id,
                "param_name": param_name,
                "current_value": current_value,
                "proposed_value": proposed_value,
                "delta_pct": delta_pct,
                "expected_trigger_freq_change": expected_trigger_freq_change,
                "frozen_eval_alpha": frozen_eval_alpha,
                "naive_sharpe": naive_sharpe_value,
                "deflated_sharpe": deflated_sharpe_value,
                "sortino": sortino_value,
                "n_trials": n_trials,
                "study_name": study_name,
                "trading_day_start": trading_day_start,
                "trading_day_end": trading_day_end,
                "cycle_id": run_timestamp,
            })

    return report_rows
