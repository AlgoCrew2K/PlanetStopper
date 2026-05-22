"""
Cluster 3 AC-4 / AC-5 — synthetic_history Monte-Carlo parity with production.

AC-4: synthetic_history's run_monte_carlo call must pass
neighbor_k = production's MC_DEFAULT_NEIGHBOR_K (150), not the hardcoded 5.
With k=5 the bootstrap CDF is a 5-step function and P(beat) snaps to
{0,20,40,60,80,100} — the replay's mc_prob is noise relative to production,
disagreeing on the exit-gate side in 10-20% of cycles. Path count may stay
lower (paths only add unbiased variance) — only k must match.

AC-5: synthetic_history's insufficient-MC handling must match production's
fail-safe. run_monte_carlo returns the None sentinel
(MC_INSUFFICIENT_HISTORY_SENTINEL) when MC history is too short. The current
code substitutes MC_INSUFFICIENT_REPLAY_VALUE = 22.5 — a fabricated in-band
probability. Production (Cluster 2's contract) treats None as "MC opinion
absent": no arm, no disarm, no TP, no MC veto of the trailing stop. The
replay must carry the same fail-safe, not fabricate 22.5.

These are STRUCTURAL + BEHAVIOURAL tests on synthetic_history.py. They do not
make live API calls — synthetic_history's run_monte_carlo dependency is
patched with a recording double that captures the neighbor_k argument.

RED EXPECTATION: against pre-fix synthetic_history.py
  * test_synthetic_history_passes_production_neighbor_k fails — k=5 is passed.
  * test_synthetic_history_no_fabricated_insufficient_mc_value fails — the
    22.5 substitute constant is still present and used.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import math_engine
import synthetic_history


_SYNTH_PATH = pathlib.Path(synthetic_history.__file__)
_SYNTH_SRC = _SYNTH_PATH.read_text(encoding="utf-8")
_SYNTH_TREE = ast.parse(_SYNTH_SRC)


def _run_monte_carlo_calls() -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(_SYNTH_TREE):
        if isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr == "run_monte_carlo") \
                    or (isinstance(f, ast.Name) and f.id == "run_monte_carlo"):
                calls.append(node)
    return calls


# ===========================================================================
# AC-4 — neighbor_k must equal production's MC_DEFAULT_NEIGHBOR_K.
# ===========================================================================


def test_synthetic_history_run_monte_carlo_call_exists() -> None:
    """Precondition for AC-4/AC-5: synthetic_history must call run_monte_carlo
    exactly once (the replay's per-tick MC). If this changes, the AC-4/AC-5
    assertions below need re-targeting."""
    calls = _run_monte_carlo_calls()
    assert len(calls) == 1, (
        f"Expected exactly one run_monte_carlo call in synthetic_history.py; "
        f"found {len(calls)}."
    )


def test_synthetic_history_does_not_pass_neighbor_k_literal_5() -> None:
    """AC-4: synthetic_history must NOT pass a bare `5` as the neighbor count.

    run_monte_carlo's signature is
    (holdings, hist_data, spy_today, paths, neighbor_k, *, seed). The 5th
    positional arg (or a neighbor_k= kwarg) is the neighbor count. A literal
    5 there is the k=5 defect.

    RED: pre-fix the call is run_monte_carlo(..., 300, 5, seed=...).
    """
    call = _run_monte_carlo_calls()[0]

    # Positional neighbor_k is arg index 4 (0-based).
    bad_positional = (
        len(call.args) > 4
        and isinstance(call.args[4], ast.Constant)
        and call.args[4].value == 5
    )
    bad_kwarg = any(
        kw.arg == "neighbor_k"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value == 5
        for kw in call.keywords
    )
    assert not (bad_positional or bad_kwarg), (
        "synthetic_history passes neighbor_k=5 to run_monte_carlo. With k=5 "
        "the kNN bootstrap CDF is a 5-step function and mc_prob snaps to "
        "{0,20,40,60,80,100}. It must pass production's MC_DEFAULT_NEIGHBOR_K "
        "(150) so the replay's mc_prob matches the live engine's estimand."
    )


def test_synthetic_history_neighbor_k_references_production_constant() -> None:
    """AC-4: the neighbor count must be the SHARED named constant
    math_engine.MC_DEFAULT_NEIGHBOR_K (or a name bound to it) — not a bare
    `150`. If production re-tunes MC_DEFAULT_NEIGHBOR_K the replay must track
    it automatically.

    Accepts: a `neighbor_k` arg that is an attribute access ending in
    MC_DEFAULT_NEIGHBOR_K, or a Name whose source-level binding references it.

    RED: pre-fix there is no reference to MC_DEFAULT_NEIGHBOR_K anywhere in
    synthetic_history.py.
    """
    references_constant = any(
        (isinstance(node, ast.Attribute) and node.attr == "MC_DEFAULT_NEIGHBOR_K")
        or (isinstance(node, ast.Name) and node.id == "MC_DEFAULT_NEIGHBOR_K")
        for node in ast.walk(_SYNTH_TREE)
    )
    assert references_constant, (
        "synthetic_history.py never references math_engine.MC_DEFAULT_"
        "NEIGHBOR_K. The replay's neighbor count must be the shared "
        "production constant so a re-tune flows through — not a duplicated "
        "literal 150."
    )


def test_synthetic_history_neighbor_k_value_matches_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-4 (behavioural): when synthetic_history builds a replay day, the
    neighbor_k it actually passes to run_monte_carlo must equal
    math_engine.MC_DEFAULT_NEIGHBOR_K.

    Patches math_engine.run_monte_carlo with a recorder that captures the
    neighbor_k argument from the FIRST call, then exercises the per-day
    builder. No live API call: run_monte_carlo is the only network-free MC
    primitive and it is fully replaced here.

    RED: pre-fix the recorder captures 5, not 150.
    """
    captured: dict = {}

    def _recording_mc(holdings, hist_data, spy_today, paths, neighbor_k, *, seed):
        # neighbor_k is the 5th positional parameter of run_monte_carlo.
        captured.setdefault("neighbor_k", neighbor_k)
        captured.setdefault("paths", paths)
        return 50.0  # any in-band probability — value irrelevant to AC-4

    monkeypatch.setattr(
        synthetic_history.math_engine, "run_monte_carlo", _recording_mc
    )

    builder = _per_day_builder_or_skip()
    builder()

    assert "neighbor_k" in captured, (
        "run_monte_carlo was not invoked by the synthetic-history per-day "
        "builder — AC-4 cannot be exercised."
    )
    assert captured["neighbor_k"] == math_engine.MC_DEFAULT_NEIGHBOR_K, (
        f"synthetic_history passed neighbor_k={captured['neighbor_k']} to "
        f"run_monte_carlo; production uses MC_DEFAULT_NEIGHBOR_K="
        f"{math_engine.MC_DEFAULT_NEIGHBOR_K}. They must match."
    )


# ===========================================================================
# AC-5 — insufficient-MC handling must match production's None fail-safe.
# ===========================================================================


def test_synthetic_history_no_fabricated_insufficient_mc_value() -> None:
    """AC-5: the MC_INSUFFICIENT_REPLAY_VALUE = 22.5 substitute must be gone.

    Production treats run_monte_carlo's None sentinel as "MC opinion absent"
    (no arm/disarm/TP, no MC veto). Substituting a fabricated in-band 22.5
    makes the replay behave as if MC ran and returned a real probability —
    it can fabricate or suppress state transitions production would not make.

    RED: pre-fix synthetic_history.py defines MC_INSUFFICIENT_REPLAY_VALUE.
    """
    assert not hasattr(synthetic_history, "MC_INSUFFICIENT_REPLAY_VALUE"), (
        "synthetic_history still defines MC_INSUFFICIENT_REPLAY_VALUE (22.5). "
        "The replay must NOT fabricate an in-band probability for an "
        "insufficient-MC tick — it must carry production's None fail-safe "
        "(MC opinion absent: no MC-driven state transition, no veto)."
    )


def test_synthetic_history_insufficient_mc_does_not_become_inband_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-5 (behavioural): when run_monte_carlo returns the insufficient
    sentinel (None), the tick's mc_prob must NOT be a fabricated in-band
    number like 22.5. It must carry the fail-safe value the replay's exit
    path treats as 'MC unavailable'.

    Acceptable post-fix: the tick stores None (the replay then handles None
    the way compute_exit_confirmation does — see AC-1/AC-6), OR a documented
    fail-safe that the replay's arm/TP gates treat as absent. What is NOT
    acceptable is the specific fabricated 22.5.

    RED: pre-fix the tick's mc_prob becomes 22.5.
    """
    def _insufficient_mc(holdings, hist_data, spy_today, paths, neighbor_k, *, seed):
        return math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL  # None

    monkeypatch.setattr(
        synthetic_history.math_engine, "run_monte_carlo", _insufficient_mc
    )

    builder = _per_day_builder_or_skip()
    ticks = _extract_ticks(builder())

    assert ticks, (
        "The synthetic-history per-day builder produced no ticks — AC-5 "
        "cannot be exercised."
    )
    for t in ticks:
        mc = t.get("mc_prob")
        assert mc != 22.5, (
            f"An insufficient-MC tick stored the fabricated value 22.5 as "
            f"mc_prob. The replay must carry the production fail-safe (MC "
            f"opinion absent), not a fabricated in-band probability."
        )


# ===========================================================================
# Test harness — locate and exercise synthetic_history's per-day builder
# without any live API call.
# ===========================================================================


def _per_day_builder_or_skip():
    """Return a zero-arg callable that builds one synthetic-history day.

    synthetic_history's tick-building logic lives inside a `process_day`
    nested function (synthetic_history.py:~200-267). To exercise it for
    AC-4/AC-5 behavioural tests the implementer must expose a module-level,
    independently-callable per-day builder (e.g. `build_replay_day(...)`)
    that takes pre-fetched bar data — so the MC parity can be tested without
    a live Alpaca fetch.

    Skips with a clear directive if no such seam exists yet.
    """
    for name in ("build_replay_day", "build_synthetic_day", "process_replay_day"):
        if hasattr(synthetic_history, name):
            fn = getattr(synthetic_history, name)
            return lambda: fn(**_minimal_day_inputs())
    pytest.fail(
        "synthetic_history exposes no module-level per-day builder. AC-4/AC-5 "
        "behavioural parity needs the per-day tick-building logic (currently "
        "the `process_day` closure) exposed as an independently-callable, "
        "API-free function so neighbor_k / insufficient-MC handling is "
        "testable from fixtures. Expose e.g. build_replay_day(...)."
    )


def _minimal_day_inputs() -> dict:
    """Minimal fixture inputs for a single replay day — one symphony, one
    holding, a handful of intraday bars and >= 20 days of daily history.

    PROVENANCE — verified against the real producer, NOT assumed. The
    hist_data_up_to_yesterday shape is exactly what synthetic_history's
    process_day builds and passes (synthetic_history.py:244-291): a
    DATE-keyed dict {date_str: {ticker: {c, daily_ret, high, low, close}}}.
    math_engine.calculate_20d_vol / calculate_14d_atr_pct consume it that way
    (iterate historical_data.values() -> day_data.values()); a ticker-keyed
    or list shape crashes them. The fixture carries >= 20 dated entries so
    calculate_20d_vol's LOOKBACK_DAYS gate is satisfied (run_monte_carlo
    itself is monkeypatched in the AC-4/AC-5 tests, so its own history gate
    is not the variable under test).

    intraday_by_date / timestamps / holdings / yesterday_closes / spy_today
    match the implementer's build_replay_day signature.
    """
    # 24 dated daily-history entries (> LOOKBACK_DAYS=20). Each date maps a
    # ticker to the producer's per-ticker record shape.
    hist_data_up_to_yesterday: dict[str, dict[str, dict[str, float]]] = {}
    for d in range(1, 25):
        date_str = f"2026-03-{d:02d}"
        close = 100.0 + d * 0.5
        hist_data_up_to_yesterday[date_str] = {
            "SPY": {
                "c": close,
                "daily_ret": 0.001,  # small flat daily return
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
            }
        }

    return {
        "sym_id": "sym-A",
        "date_str": "2026-04-06",
        "holdings": [{"ticker": "SPY", "allocation": 1.0}],
        "intraday_by_date": {
            "2026-04-06": {
                "SPY": [
                    {"c": 100.0 + i * 0.1, "vwap": 100.0, "t": f"2026-04-06T09:{30 + i:02d}:00Z"}
                    for i in range(5)
                ]
            }
        },
        "timestamps": [f"2026-04-06T09:{30 + i:02d}:00Z" for i in range(5)],
        "hist_data_up_to_yesterday": hist_data_up_to_yesterday,
        "yesterday_closes": {"SPY": 100.0},
        "spy_today": 100.0,
    }


def _extract_ticks(builder_result) -> list[dict]:
    """Normalize the per-day builder result to a flat list of tick dicts.

    The builder may return ticks directly, or a (date, {sym: ticks}) tuple
    mirroring process_day. Handles both so the AC-5 test is robust to the
    implementer's chosen return shape.
    """
    result = builder_result
    if isinstance(result, tuple) and len(result) == 2:
        result = result[1]
    if isinstance(result, dict):
        flat: list[dict] = []
        for v in result.values():
            if isinstance(v, list):
                flat.extend(v)
        return flat
    if isinstance(result, list):
        return result
    return []
