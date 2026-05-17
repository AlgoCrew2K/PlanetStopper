"""
I2 — Stop-compounding deterministic simulation.

Pure side calculation. NO engine code modified. Imports `math_engine` only to
reproduce its exact arithmetic (no monkey-patching, no state).

Run:
    python docs/research/risk/scripts/i2_compounding_sim.py

Prints a markdown table of stop tightness over the trading day under the three
mechanism configurations:
    (1) PARA only           — para_armed=True,  breakeven_locked=False
    (2) PARA + breakeven    — same as (1) (the multiplier fires when EITHER is
                              true; locking breakeven does not double-multiply,
                              but locking ALSO floors the trigger at 0.0 which
                              shows up in the trigger-level table)
    (3) All three           — same as (2) PLUS time-squeeze decay (always on;
                              cannot be disabled without modifying the engine).

Because the time-squeeze decay drives `dynamic_multiplier` and `dynamic_min_stop`
unconditionally, the "PARA only" and "PARA + breakeven" columns are computed
with time_ratio=0.0 (open) — they represent the counterfactual "what would the
stop be if only that mechanism existed at this moment?" The third column uses
the live time_ratio at each minute.

The default operator setting is EXECUTION_START_TIME = "10:30". The time_ratio
anchors on that, not 09:30 ET (see alpha_bot_execution.py:805-807).
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

# Allow running from repo root or from the scripts dir.
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import math_engine  # noqa: E402

# Default parameters (.env defaults from alpha_bot_execution.py / database.py)
MAX_PARABOLIC_SQUEEZE = 0.50           # alpha_bot_execution.py:62
SYMPHONY_VOL_TYPICAL = 1.20            # typical 20-day vol for a Composer symphony (pct points)
SYMPHONY_VOL_LOW = 0.60                # quiet regime
SYMPHONY_VOL_HIGH = 2.40               # volatile regime
HWM_PCT = 1.00                         # safe_hwm 1.0% (assume slight positive drift)
CURRENT_RETURN_FLAT = 1.00             # flat random-walk price; current_return == HWM

EXECUTION_START = dt.time(10, 30)
MARKET_CLOSE = dt.time(16, 0)


def time_ratio_for(t: dt.time) -> float:
    today = dt.date(2026, 5, 17)
    open_dt = dt.datetime.combine(today, EXECUTION_START)
    close_dt = dt.datetime.combine(today, MARKET_CLOSE)
    now_dt = dt.datetime.combine(today, t)
    raw = (now_dt - open_dt).total_seconds() / (close_dt - open_dt).total_seconds()
    return max(0.0, min(1.0, raw))


def stop_distance(
    *,
    symphony_vol: float,
    time_ratio: float,
    para_armed: bool,
    breakeven_locked: bool,
) -> tuple[float, float, float]:
    """Returns (active_trailing_stop_distance, dynamic_multiplier, dynamic_min_stop)."""
    dyn_mult, dyn_min = math_engine.compute_time_squeeze_decay(time_ratio)
    active = math_engine.compute_active_trailing_stop(
        symphony_vol=symphony_vol,
        dynamic_multiplier=dyn_mult,
        dynamic_min_stop=dyn_min,
        para_armed=para_armed,
        breakeven_locked=breakeven_locked,
        parabolic_squeeze_multiplier=MAX_PARABOLIC_SQUEEZE,
    )
    return active, dyn_mult, dyn_min


def trigger_level(
    *,
    symphony_vol: float,
    hwm_pct: float,
    current_return: float,
    time_ratio: float,
    para_armed: bool,
    breakeven_locked: bool,
) -> float:
    active, _, _ = stop_distance(
        symphony_vol=symphony_vol,
        time_ratio=time_ratio,
        para_armed=para_armed,
        breakeven_locked=breakeven_locked,
    )
    base_stop_level = hwm_pct - active
    if breakeven_locked:
        return max(base_stop_level, 0.0)
    return base_stop_level


def build_table(symphony_vol: float) -> str:
    times = [
        dt.time(10, 30),
        dt.time(11, 0),
        dt.time(12, 0),
        dt.time(13, 0),
        dt.time(14, 0),
        dt.time(15, 0),
        dt.time(15, 30),
        dt.time(15, 45),
        dt.time(15, 55),
        dt.time(16, 0),
    ]
    rows = []
    rows.append(
        f"| Time (ET) | time_ratio | decay | dyn_mult | min_stop | "
        f"PARA-only dist | +breakeven dist | +time-sq dist | trigger (all 3) |"
    )
    rows.append("|---|---|---|---|---|---|---|---|---|")
    for t in times:
        tr = time_ratio_for(t)
        decay = math.log10(1 + 9 * tr)
        dyn_mult_open, dyn_min_open = math_engine.compute_time_squeeze_decay(0.0)
        # Column "PARA only": squeeze active, breakeven off, NO time-squeeze (tr=0)
        active_para_only, _, _ = stop_distance(
            symphony_vol=symphony_vol, time_ratio=0.0,
            para_armed=True, breakeven_locked=False,
        )
        # Column "PARA + breakeven": both flags set, NO time-squeeze (tr=0)
        active_para_be, _, _ = stop_distance(
            symphony_vol=symphony_vol, time_ratio=0.0,
            para_armed=True, breakeven_locked=True,
        )
        # Column "All three": flags + live time-ratio
        active_all3, dyn_mult, dyn_min = stop_distance(
            symphony_vol=symphony_vol, time_ratio=tr,
            para_armed=True, breakeven_locked=True,
        )
        trig_all3 = trigger_level(
            symphony_vol=symphony_vol, hwm_pct=HWM_PCT,
            current_return=CURRENT_RETURN_FLAT, time_ratio=tr,
            para_armed=True, breakeven_locked=True,
        )
        rows.append(
            f"| {t.strftime('%H:%M')} | {tr:.3f} | {decay:.3f} | {dyn_mult:.3f} | "
            f"{dyn_min:.3f} | {active_para_only:.4f} | {active_para_be:.4f} | "
            f"{active_all3:.4f} | {trig_all3:+.4f} |"
        )
    return "\n".join(rows)


def daily_drag_estimate(symphony_vol: float) -> dict[str, float]:
    """Estimate Kaminski-Lo style 'value subtracted' per trading day under flat
    random walk. Approximate: P(touch stop in 1 minute | sigma per minute) given
    a flat price walk. Use 1-sigma normal-tail probability that current_return
    drops below trigger within a single tick — integrated over all minutes.

    This is a CRUDE rule of thumb, not a formal Kaminski-Lo replication. It
    illustrates the COMPOUNDING SHAPE only.
    """
    import statistics
    # 1-min realized vol scaling: daily vol -> per-minute vol = vol / sqrt(390)
    per_min_vol = symphony_vol / math.sqrt(390)
    times = [dt.time(10, 30 + 5 * i) if (30 + 5 * i) < 60 else
             dt.time(11 + (30 + 5 * i) // 60, (30 + 5 * i) % 60)
             for i in range(0, 66)]  # every 5 min 10:30 - 15:55, approx
    cum_hazard = {"para_only": 0.0, "para_be": 0.0, "all3": 0.0}
    for t in times:
        tr = time_ratio_for(t)
        # All three columns assume current_return is at HWM (1%) on flat walk.
        # Stop distance below HWM; hazard = P(N(0, per_min_vol) < -(dist - 0.10)),
        # i.e., we need return to drop more than (dist - magnitude_floor) below HWM
        # in one tick. Use normal CDF.
        def hazard(dist: float) -> float:
            slack = dist - 0.10  # MAGNITUDE_FLOOR_PCT
            if slack <= 0:
                return 0.5  # stop is essentially at HWM
            z = slack / max(per_min_vol, 1e-6)
            # 1 - Phi(z) approximation via erfc
            return 0.5 * math.erfc(z / math.sqrt(2))
        d_para, _, _ = stop_distance(symphony_vol=symphony_vol, time_ratio=0.0,
                                     para_armed=True, breakeven_locked=False)
        d_be, _, _ = stop_distance(symphony_vol=symphony_vol, time_ratio=0.0,
                                   para_armed=True, breakeven_locked=True)
        d_all, _, _ = stop_distance(symphony_vol=symphony_vol, time_ratio=tr,
                                    para_armed=True, breakeven_locked=True)
        cum_hazard["para_only"] += hazard(d_para)
        cum_hazard["para_be"] += hazard(d_be)
        cum_hazard["all3"] += hazard(d_all)
    return cum_hazard


if __name__ == "__main__":
    print("=" * 80)
    print(f"I2 stop-compounding simulation — date 2026-05-17")
    print(f"Defaults: MAX_PARABOLIC_SQUEEZE={MAX_PARABOLIC_SQUEEZE}, HWM={HWM_PCT}%, current_return={CURRENT_RETURN_FLAT}%")
    print("=" * 80)
    for label, vol in [("LOW vol (0.60%)", SYMPHONY_VOL_LOW),
                       ("TYPICAL vol (1.20%)", SYMPHONY_VOL_TYPICAL),
                       ("HIGH vol (2.40%)", SYMPHONY_VOL_HIGH)]:
        print(f"\n## symphony_vol = {vol} ({label})\n")
        print(build_table(vol))
        cum = daily_drag_estimate(vol)
        print(f"\nApprox cumulative hazard (5-min-bucket sum of 1-min stop-touch p):")
        print(f"  PARA only            : {cum['para_only']:.3f}")
        print(f"  PARA + breakeven     : {cum['para_be']:.3f}")
        print(f"  PARA + BE + time-sq  : {cum['all3']:.3f}  "
              f"(ratio vs PARA-only = {cum['all3']/max(cum['para_only'],1e-9):.2f}x)")
