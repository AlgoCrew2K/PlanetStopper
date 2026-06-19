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


# Replay machinery: the two top-level replay functions plus any shared
# per-tick exit core. The replay may extract a shared core (the preferred
# single-source-of-truth design) — the AST checks walk the union so the
# refactor is accommodated, not forbidden.
_REPLAY_MACHINERY_NAMES = (
    "_collect_sim_returns",
    "run_simulation",
    "replay_exit_sequence",
    "_replay_exit_tick",
    "_replay_tick",
    "_simulate_exit_tick",
)


def _replay_machinery_nodes() -> list[ast.FunctionDef]:
    return [
        node
        for node in ast.walk(_AUTOTUNER_TREE)
        if isinstance(node, ast.FunctionDef) and node.name in _REPLAY_MACHINERY_NAMES
    ]


def _numeric_constants_in(node: ast.AST) -> list[float]:
    out: list[float] = []
    for n in ast.walk(node):
        if (
            isinstance(n, ast.Constant)
            and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)
        ):
            out.append(float(n.value))
    return out


# ===========================================================================
# AC-7 — structural: no bare 390 literal anywhere in the replay machinery.
# ===========================================================================


def test_replay_machinery_has_no_390_session_length_literal() -> None:
    """AC-7: the unnamed `390` / `390.0` full-session-length literal must be
    gone from the replay machinery (the two replay functions and any shared
    per-tick exit core). time_ratio must derive from the actual tick count
    (or session datetimes), not assume a fixed 390-bar session.

    Walks the whole replay machinery so a shared-core refactor that relocates
    the time_ratio computation cannot hide a surviving 390 literal.

    RED: the replay contains `tick_idx / 390.0`.
    """
    offenders: list[str] = []
    for func in _replay_machinery_nodes():
        if 390.0 in _numeric_constants_in(func):
            offenders.append(func.name)
    assert not offenders, (
        f"Replay machinery {offenders} still contains the bare 390 session-"
        f"length literal. time_ratio must be derived from the actual session "
        f"length — tick_idx / max(1, len(ticks) - 1) — so half-day sessions "
        f"reach full end-of-day stop tightening."
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

    monkeypatch.setattr(autotuner.math_engine, "compute_time_squeeze_decay", _spy_decay)

    n_ticks = 210  # a US equity half-day: 09:30-13:00
    ticks = [
        {
            "time": "09:30",
            "return": 0.5,
            "mc_prob": 50.0,
            "vol": 0.5,
            "vwap_diff": 0.0,
            "base_atr_pct": 0.5,
            "valid_vwap_weight": 0.0,
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

    monkeypatch.setattr(autotuner.math_engine, "compute_time_squeeze_decay", _spy_decay)

    ticks = [
        {
            "time": "09:30",
            "return": 0.5,
            "mc_prob": 50.0,
            "vol": 0.5,
            "vwap_diff": 0.0,
            "base_atr_pct": 0.5,
            "valid_vwap_weight": 0.0,
        }
        for _ in range(30)
    ]
    history = {"sym-A": {"2026-04-06": ticks}}
    autotuner.run_simulation(_default_params(), history, ["sym-A"], "2026-05-01", {})

    assert seen, "compute_time_squeeze_decay was never called."
    assert seen[0] == 0.0, f"First tick (session open) must have time_ratio 0.0; got {seen[0]}."


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

    monkeypatch.setattr(autotuner.math_engine, "compute_time_squeeze_decay", _spy_decay)

    ticks = [
        {
            "time": "09:30",
            "return": 0.5,
            "mc_prob": 50.0,
            "vol": 0.5,
            "vwap_diff": 0.0,
            "base_atr_pct": 0.5,
            "valid_vwap_weight": 0.0,
        }
        for _ in range(390)
    ]
    history = {"sym-A": {"2026-04-06": ticks}}
    autotuner.run_simulation(_default_params(), history, ["sym-A"], "2026-05-01", {})

    assert seen and max(seen) == 1.0, (
        f"A full 390-tick session must reach time_ratio 1.0; got "
        f"{max(seen) if seen else 'no calls'}."
    )


def test_replay_single_tick_day_time_ratio_reaches_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-7 / risk-engine-specialist MEDIUM M-C3.1 — the degenerate
    single-tick day (n_ticks == 1).

    `time_ratio = tick_idx / max(1, n_ticks - 1)`. For a 1-tick day the
    only tick has tick_idx = 0, so time_ratio = 0 / max(1, 0) = 0.0 — the
    single tick is treated as session OPEN (loosest stop). Production
    derives time_ratio from wall-clock open/close datetimes, so a 1-bar
    session's single tick is at/near the CLOSE (time_ratio near 1.0,
    tightest stop). That is a genuine replay-vs-production divergence on a
    degenerate day: the replay under-tightens.

    RULING (risk-engine-specialist M-C3.1, leaning option b — faithful
    parity): a single-tick day must reach time_ratio 1.0, matching
    production's "single bar approximately at close". This test pins that
    intended behaviour.

    A 1-tick trading day is effectively unreachable in real Alpaca minute
    data (even half-days are ~210 bars) — but a synthetic / data-gap day
    could hit it, and the behaviour must be pinned, not incidental.

    RED: the current `tick_idx / max(1, n_ticks - 1)` formula yields 0.0
    for the single tick. The fix special-cases n_ticks == 1 to time_ratio
    1.0 (production parity).
    """
    real_decay = autotuner.math_engine.compute_time_squeeze_decay
    seen: list[float] = []

    def _spy_decay(time_ratio):
        seen.append(time_ratio)
        return real_decay(time_ratio)

    monkeypatch.setattr(autotuner.math_engine, "compute_time_squeeze_decay", _spy_decay)

    # A single-tick day — the degenerate n_ticks == 1 case.
    ticks = [
        {
            "time": "09:30",
            "return": 0.5,
            "mc_prob": 50.0,
            "vol": 0.5,
            "vwap_diff": 0.0,
            "base_atr_pct": 0.5,
            "valid_vwap_weight": 0.0,
        }
    ]
    history = {"sym-A": {"2026-04-06": ticks}}
    autotuner.run_simulation(_default_params(), history, ["sym-A"], "2026-05-01", {})

    assert seen, "compute_time_squeeze_decay was never called for the 1-tick day."
    assert len(seen) == 1, (
        f"A 1-tick day must drive compute_time_squeeze_decay exactly once; got {len(seen)} calls."
    )
    # Exact equality: a faithful single-tick day maps the lone tick to the
    # session close — time_ratio 1.0 exactly, no tolerance.
    assert seen[0] == 1.0, (
        f"Single-tick day: the lone tick's time_ratio was {seen[0]}, not "
        f"1.0. A 1-bar session's only tick is at the close (production "
        f"derives time_ratio from wall-clock datetimes) — the replay must "
        f"reach full end-of-day stop tightening, not treat it as the open "
        f"(0.0). M-C3.1: special-case n_ticks == 1 to time_ratio 1.0."
    )
