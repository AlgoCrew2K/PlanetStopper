"""
Cluster 3 AC-7 — the replay's time_ratio must be derived from the actual
session length, not the unnamed 390.0 literal.

The replay computes `time_ratio = tick_idx / 390.0` in both _collect_sim_
returns (~line 354) and run_simulation (~line 491). 390 is the minute count
of a full 09:30-16:00 session. On a half-day (~210 min) the replay's
time_ratio only reaches ~0.54, so compute_time_squeeze_decay never applies
full end-of-day stop tightening. Production derives time_ratio from the
actual open/close datetimes, clamped to [0,1], reaching 1.0 at the real
close.

The plan's prescribed fix: `time_ratio = tick_idx / max(1, len(ticks) - 1)`
— so the LAST tick of any session (full or half day) reaches time_ratio 1.0.

RED EXPECTATION: against pre-fix autotuner.py
  * test_replay_has_no_390_literal fails — the bare 390.0 is present.
  * test_replay_time_ratio_reaches_one_on_half_day fails — a 210-tick day
    only reaches ~0.535.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import autotuner


_AUTOTUNER_PATH = pathlib.Path(autotuner.__file__)
_AUTOTUNER_TREE = ast.parse(_AUTOTUNER_PATH.read_text(encoding="utf-8"))


def _function_node(name: str) -> ast.FunctionDef:
    for node in ast.walk(_AUTOTUNER_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in autotuner.py")


def _numeric_constants_in(func: ast.FunctionDef) -> list[float]:
    out: list[float] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            out.append(float(node.value))
    return out


# ===========================================================================
# AC-7 — structural: no bare 390 literal in either replay function.
# ===========================================================================


@pytest.mark.parametrize("func_name", ["_collect_sim_returns", "run_simulation"])
def test_replay_function_has_no_390_session_length_literal(func_name: str) -> None:
    """AC-7: the unnamed `390` / `390.0` full-session-length literal must be
    gone from both replay functions. time_ratio must derive from the actual
    tick count (or session datetimes), not assume a fixed 390-bar session.

    RED: both functions contain `tick_idx / 390.0`.
    """
    func = _function_node(func_name)
    consts = _numeric_constants_in(func)
    assert 390.0 not in consts, (
        f"autotuner.{func_name} still contains the bare 390 session-length "
        f"literal. time_ratio must be derived from the actual session length "
        f"— tick_idx / max(1, len(ticks) - 1) — so half-day sessions reach "
        f"full end-of-day stop tightening."
    )


# ===========================================================================
# AC-7 — behavioural: a half-day session reaches time_ratio 1.0 at its close.
# ===========================================================================


def _default_params() -> dict:
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 99.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 999,
        "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def test_replay_time_ratio_reaches_one_on_last_tick_of_half_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-7: on a half-day session (e.g. 210 minute-bar ticks) the LAST
    tick's time_ratio passed to compute_time_squeeze_decay must reach 1.0 —
    so the replay applies full end-of-day stop tightening, exactly like
    production at the real close.

    Captures every time_ratio the replay feeds compute_time_squeeze_decay
    over a 210-tick day. The wrapped function still calls the real
    implementation — only the time_ratio argument is observed.

    RED: with `tick_idx / 390.0` the max time_ratio over 210 ticks is
    209/390 ~= 0.536, never reaching 1.0.
    """
    real_decay = autotuner.math_engine.compute_time_squeeze_decay
    seen: list[float] = []

    def _spy_decay(time_ratio):
        seen.append(time_ratio)
        return real_decay(time_ratio)

    monkeypatch.setattr(
        autotuner.math_engine, "compute_time_squeeze_decay", _spy_decay
    )

    n_ticks = 210  # a US equity half-day: 09:30-13:00
    ticks = [
        {
            "time": "09:30", "return": 0.5, "mc_prob": 50.0, "vol": 0.5,
            "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0,
        }
        for _ in range(n_ticks)
    ]
    history = {"sym-A": {"2026-11-27": ticks}}  # Black-Friday half day

    autotuner.run_simulation(_default_params(), history, ["sym-A"], "2026-12-01", {})

    assert seen, "compute_time_squeeze_decay was never called by the replay."
    max_ratio = max(seen)
    # The last tick of any session must reach time_ratio == 1.0 exactly.
    # Tolerance: exact float equality is appropriate — tick_idx / (len-1) with
    # tick_idx == len-1 is an exact 1.0 in IEEE-754 (n/n == 1.0 for all n).
    assert max_ratio == 1.0, (
        f"On a {n_ticks}-tick half-day session the replay's max time_ratio "
        f"was {max_ratio}, not 1.0. With the 390 literal it tops out at "
        f"{(n_ticks - 1) / 390.0:.4f} and never applies full end-of-day "
        f"tightening. time_ratio must be tick_idx / max(1, len(ticks) - 1)."
    )


def test_replay_time_ratio_first_tick_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-7: tick 0 (session open) must map to time_ratio 0.0 — the loosest
    stop. Guards against an off-by-one in the session-length derivation
    (e.g. tick_idx / len(ticks) would make tick 0 nonzero only if mis-shifted,
    or the last tick never reach 1.0).
    """
    real_decay = autotuner.math_engine.compute_time_squeeze_decay
    seen: list[float] = []

    def _spy_decay(time_ratio):
        seen.append(time_ratio)
        return real_decay(time_ratio)

    monkeypatch.setattr(
        autotuner.math_engine, "compute_time_squeeze_decay", _spy_decay
    )

    ticks = [
        {
            "time": "09:30", "return": 0.5, "mc_prob": 50.0, "vol": 0.5,
            "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0,
        }
        for _ in range(30)
    ]
    history = {"sym-A": {"2026-04-06": ticks}}
    autotuner.run_simulation(_default_params(), history, ["sym-A"], "2026-05-01", {})

    assert seen, "compute_time_squeeze_decay was never called."
    assert seen[0] == 0.0, (
        f"First tick (session open) must have time_ratio 0.0; got {seen[0]}."
    )


def test_replay_time_ratio_full_day_still_reaches_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-7 (regression): a full 390-tick session must STILL reach time_ratio
    1.0 on its last tick. The fix must not regress the full-day case — it
    generalizes the full-day behaviour, it does not change it.
    """
    real_decay = autotuner.math_engine.compute_time_squeeze_decay
    seen: list[float] = []

    def _spy_decay(time_ratio):
        seen.append(time_ratio)
        return real_decay(time_ratio)

    monkeypatch.setattr(
        autotuner.math_engine, "compute_time_squeeze_decay", _spy_decay
    )

    ticks = [
        {
            "time": "09:30", "return": 0.5, "mc_prob": 50.0, "vol": 0.5,
            "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0,
        }
        for _ in range(390)
    ]
    history = {"sym-A": {"2026-04-06": ticks}}
    autotuner.run_simulation(_default_params(), history, ["sym-A"], "2026-05-01", {})

    assert seen and max(seen) == 1.0, (
        f"A full 390-tick session must reach time_ratio 1.0; got "
        f"{max(seen) if seen else 'no calls'}."
    )
