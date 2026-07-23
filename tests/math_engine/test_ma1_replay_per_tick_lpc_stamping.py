"""
AC-1 (MA-1 CRITICAL) — replay holdings must carry a REAL per-tick
``last_percent_change`` before every ``run_monte_carlo`` call on the replay
path.

Bug (audit finding MA-1): ``synthetic_history.build_replay_day`` already
computes a per-ticker per-tick fractional return —
``ret = (bar["c"] - yesterday_closes.get(ticker, c)) / y_close`` — for the
tick's aggregate-return sum (``agg_ret``), but never stamps that value onto a
holdings copy as ``last_percent_change`` before calling
``math_engine.run_monte_carlo`` at synthetic_history.py:428. Every holding is
therefore excluded from ``run_monte_carlo``'s ``current_symphony_return`` sum
(math_engine.py:1162-1166 excludes any holding whose ``last_percent_change``
is ``None``), so the replay's MC output is day-constant / degenerate: 3 of 6
nightly-tuned params (TAKE_PROFIT_MC_PCT, PARABOLIC_VELOCITY_THRESHOLD,
MAX_PARABOLIC_SQUEEZE) become objective-inert noise.

lpc semantic (settled, not assumed — see feature-plans/math-r1.md:22 and the
team's cross-checks): a FRACTION vs. the prior session close, matching
Composer's own ``last_percent_change`` field. r1-review arithmetic-verified
this against tests/fixtures/composer/symphony_stats_meta.json
(last_dollar_change / (value - last_dollar_change) == last_percent_change to
3 decimals; the /100 "percent" hypothesis is off by 100x on implied dollars).

Architecture ruling (team-lead, ratifying r1-engine's ownership plan): the
fix is confined ENTIRELY to synthetic_history.py — build_replay_day already
has everything it needs (yesterday_closes, per-tick bar close) to stamp lpc
onto a FRESH holdings copy just before the run_monte_carlo call. The plan's
originally-cited alpha_bot_execution.py construction sites (:888-894,
:1557-1560) are OFF-LIMITS: bot_state["current_holdings"] built there is read
back on the LIVE path (:1191, triggered-symphony shadow override) into the
LIVE run_monte_carlo call (:1270) — stamping lpc there would be an AC-8 live
path regression AND wouldn't even satisfy AC-1 correctly (a day-stale
snapshot value, not a real per-tick one). See test_ac8_live_path_zero_diff*
for the pin that those two sites stay untouched.

Joblib-parallelism safety (r1-engine's finding): ``holdings`` is a
closure-captured object reused across every day of the same symphony's
replay under ``Parallel(n_jobs=...)`` (synthetic_history.py:673) — the fix
must build a FRESH per-tick holdings copy, never mutate the caller's
``holdings`` list/dicts in place.

Call-site completeness (approval condition from team-lead): after this fix,
synthetic_history.build_replay_day:428 must remain the ONLY
``math_engine.run_monte_carlo`` call reachable from the replay path
(synthetic_history.py + autotuner.py) — a second, uncontrolled call site
would bypass the lpc-stamping fix entirely.

We import ``synthetic_history`` directly (safe: no network I/O at import
time — see tests/synthetic_history/test_bounded_replay_parallelism.py, which
already imports it directly and is the more recent/authoritative pattern for
behavioral testing of this module).
"""

from __future__ import annotations

import ast
import json
import math
import pathlib

import pytest

import math_engine
import synthetic_history

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SYNTH_PATH = _REPO_ROOT / "synthetic_history.py"
_AUTOTUNER_PATH = _REPO_ROOT / "autotuner.py"
_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "math_engine"
_MC_SENSITIVITY_FIXTURE = _FIXTURE_DIR / "ma1_lpc_per_tick_mc_sensitivity.json"
_BUILD_REPLAY_DAY_FIXTURE = _FIXTURE_DIR / "ma1_build_replay_day_lpc_stamping.json"
_GAPPED_BAR_FIXTURE = _FIXTURE_DIR / "ma1_gapped_bar_lpc_exclusion.json"


def _load_json(path: pathlib.Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Shared historical_data builder — mirrors the convention already established
# in test_mc_deterministic_seed.py (alternating / rows spec kinds).
# ---------------------------------------------------------------------------


def _date_key(i: int) -> str:
    base_day = 1 + i
    month = 1 + (base_day - 1) // 28
    day = 1 + (base_day - 1) % 28
    return f"2026-{month:02d}-{day:02d}"


def _build_history(spec: dict) -> dict:
    """Build a historical_data dict from either an 'alternating' spec (simple
    2-value SPY/AAA sign flip — fine for math_engine.run_monte_carlo called
    directly, since it never invokes the regime-match-quality guard) or a
    'rows' spec (explicit deterministic per-day values — required wherever
    math_engine.compute_regime_match_quality is in the call path, e.g. inside
    build_replay_day: an alternating 2-value series is a near-zero-variance
    feature that trips the Mahalanobis chi-squared guard and forces
    is_unprecedented=True, suppressing mc_prob to None regardless of lpc).
    """
    kind = spec["kind"]
    if kind == "alternating":
        history: dict = {}
        for i in range(spec["num_days"]):
            sign = 1 if i % 2 == 0 else -1
            history[_date_key(i)] = {
                "SPY": {"daily_ret": sign * spec["spy_amplitude"]},
                "AAA": {"daily_ret": sign * spec["aaa_amplitude"]},
            }
        return history
    if kind == "rows":
        return {
            row["date"]: {
                "SPY": {"daily_ret": row["SPY"]},
                "AAA": {"daily_ret": row["AAA"]},
            }
            for row in spec["rows"]
        }
    raise ValueError(f"Unknown historical_data_spec kind: {kind!r}")


# ===========================================================================
# 1 — Call-site completeness (approval condition): exactly ONE
#     math_engine.run_monte_carlo call reachable from the replay path, at
#     synthetic_history.build_replay_day.
# ===========================================================================


def _find_run_monte_carlo_calls(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "run_monte_carlo"
            and isinstance(func.value, ast.Name)
            and func.value.id == "math_engine"
        ):
            calls.append(node)
    return calls


def _enclosing_function_name(tree: ast.Module, target: ast.Call) -> str | None:
    """Return the name of the nearest enclosing FunctionDef containing `target`."""
    best: str | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for child in ast.walk(node):
            if child is target:
                best = node.name
    return best


def test_replay_path_has_exactly_one_run_monte_carlo_call_site() -> None:
    """AC-1 approval condition (team-lead): the replay path (synthetic_history.py
    + autotuner.py) must have EXACTLY ONE math_engine.run_monte_carlo call,
    living in synthetic_history.build_replay_day.

    A second, uncontrolled call site (e.g. hand-rolled inside autotuner.py's
    replay loop) would bypass the per-tick lpc-stamping fix entirely and
    silently resurrect the MA-1 degeneracy on that path.
    """
    synth_tree = ast.parse(_SYNTH_PATH.read_text(encoding="utf-8"), filename=str(_SYNTH_PATH))
    autotuner_tree = ast.parse(
        _AUTOTUNER_PATH.read_text(encoding="utf-8"), filename=str(_AUTOTUNER_PATH)
    )

    synth_calls = _find_run_monte_carlo_calls(synth_tree)
    autotuner_calls = _find_run_monte_carlo_calls(autotuner_tree)

    assert not autotuner_calls, (
        f"autotuner.py has {len(autotuner_calls)} math_engine.run_monte_carlo call(s) "
        f"at line(s) {[c.lineno for c in autotuner_calls]} — the replay path must route "
        "ALL Monte Carlo computation through synthetic_history.build_replay_day so the "
        "AC-1 lpc-stamping fix cannot be bypassed by a parallel call site."
    )
    assert len(synth_calls) == 1, (
        f"Expected exactly 1 math_engine.run_monte_carlo call in synthetic_history.py, "
        f"found {len(synth_calls)} at line(s) {[c.lineno for c in synth_calls]}. "
        "A second call site means the lpc-stamping fix must be duplicated (and can "
        "silently drift) — collapse to one call site inside build_replay_day."
    )
    owner = _enclosing_function_name(synth_tree, synth_calls[0])
    assert owner == "build_replay_day", (
        f"The sole math_engine.run_monte_carlo call in synthetic_history.py "
        f"(line {synth_calls[0].lineno}) lives inside {owner!r}, expected "
        "'build_replay_day' — the AC-1 fix must stamp lpc at the point run_monte_carlo "
        "is actually invoked."
    )


# ===========================================================================
# 2 — Direct math-layer sensitivity: a real, materially different
#     last_percent_change on otherwise-identical inputs moves mc_prob.
# ===========================================================================


def test_run_monte_carlo_output_moves_with_last_percent_change() -> None:
    """AC-1 math-layer half: math_engine.run_monte_carlo (unmodified — its
    lpc-exclusion contract at :1162-1166 is correct and stays as-is) produces
    a DIFFERENT mc_prob for a materially different last_percent_change on an
    otherwise-identical holdings/historical_data/seed input.

    This pins the SENSITIVITY the audit's lead probe demonstrated (lpc swings
    of a few percent moved mc_prob dramatically). No mc_prob value is
    hardcoded — only the inequality between the two live-computed scenarios.
    """
    fx = _load_json(_MC_SENSITIVITY_FIXTURE)
    history = _build_history(fx["historical_data_spec"])

    results = {}
    for label, scenario in fx["scenarios"].items():
        results[label] = math_engine.run_monte_carlo(
            scenario["holdings"],
            history,
            fx["spy_today_return"],
            fx["simulation_paths"],
            fx["neighbor_k"],
            seed=fx["numpy_seed"],
        )

    for label, value in results.items():
        assert value is not None, (
            f"scenario {label!r} returned the insufficient-history sentinel (None) — "
            "the fixture's historical_data_spec must yield >= MC_MIN_HISTORY_DAYS "
            "eligible days; revise num_days in ma1_lpc_per_tick_mc_sensitivity.json."
        )
        assert math.isfinite(value), f"scenario {label!r} returned non-finite mc_prob {value!r}."

    assert results["lpc_low"] != results["lpc_high"], (
        "run_monte_carlo returned the SAME mc_prob "
        f"({results['lpc_low']!r}) for two holdings differing only in "
        "last_percent_change (-0.03 vs +0.03). If AC-1's premise holds, "
        "current_symphony_return must shift with lpc and move the MC output — "
        "an identical result here means either lpc is being ignored somewhere "
        "upstream of this direct math_engine call, or the fixture's historical "
        "distribution is degenerate (revise amplitudes)."
    )


# ===========================================================================
# 3 — build_replay_day wiring: per-tick lpc is stamped before EVERY
#     run_monte_carlo call, using the SAME (c - y_close)/y_close basis the
#     loop already computes for agg_ret.
# ===========================================================================


class _McSpy:
    """Wraps math_engine.run_monte_carlo, recording (holdings, kwargs) per
    call while delegating to the real function so downstream behavior
    (regime-match-quality guard, None-sentinel handling) is unaffected."""

    def __init__(self, real_fn):
        self._real_fn = real_fn
        self.calls: list[dict] = []

    def __call__(self, holdings, *args, **kwargs):
        # Deep-enough copy for later inspection — holdings dicts must not be
        # aliased, or a later in-place mutation would corrupt this recording.
        self.calls.append({"holdings": [dict(h) for h in holdings]})
        return self._real_fn(holdings, *args, **kwargs)


@pytest.fixture()
def mc_spy(monkeypatch):
    spy = _McSpy(math_engine.run_monte_carlo)
    monkeypatch.setattr(synthetic_history.math_engine, "run_monte_carlo", spy)
    return spy


def _build_replay_day_inputs(fx: dict):
    date_str = fx["date_str"]
    ticker = fx["holdings"][0]["ticker"]
    intraday_by_date = {
        date_str: {ticker: [{"t": t["t"], "c": t["c"], "vwap": t["vwap"]} for t in fx["ticks"]]}
    }
    timestamps = [t["t"] for t in fx["ticks"]]
    yesterday_closes = {ticker: fx["yesterday_close"]}
    hist_data_up_to_yesterday = _build_history(fx["historical_data_spec"])
    # Fresh holdings list per call — callers must never share a single list
    # across assertions that also check mutation safety (test 5 below builds
    # its own separate copy for that reason).
    holdings = [dict(h) for h in fx["holdings"]]
    return {
        "sym_id": fx["sym_id"],
        "date_str": date_str,
        "holdings": holdings,
        "intraday_by_date": intraday_by_date,
        "timestamps": timestamps,
        "hist_data_up_to_yesterday": hist_data_up_to_yesterday,
        "yesterday_closes": yesterday_closes,
        "spy_today": fx["spy_today_return"],
    }


def test_build_replay_day_stamps_real_per_tick_lpc_before_every_mc_call(mc_spy) -> None:
    """The holdings dict passed into EVERY run_monte_carlo call from
    build_replay_day must carry last_percent_change equal to the tick's
    (bar_close - prior_session_close) / prior_session_close fraction — the
    SAME basis the loop already computes for agg_ret, reused rather than
    invented.

    RED (pre-fix): last_percent_change is absent from every captured call's
    holdings, because the loop never stamps the ret it already computes.
    """
    fx = _load_json(_BUILD_REPLAY_DAY_FIXTURE)
    kwargs = _build_replay_day_inputs(fx)

    synthetic_history.build_replay_day(**kwargs)

    assert len(mc_spy.calls) == len(fx["ticks"]), (
        f"Expected one run_monte_carlo call per tick ({len(fx['ticks'])}), "
        f"observed {len(mc_spy.calls)}. build_replay_day's per-tick loop must "
        "call run_monte_carlo exactly once per tick."
    )

    for tick_spec, call in zip(fx["ticks"], mc_spy.calls):
        expected_lpc = (tick_spec["c"] - fx["yesterday_close"]) / fx["yesterday_close"]
        assert expected_lpc == pytest.approx(tick_spec["expected_lpc"], abs=1e-9), (
            "fixture self-check failed — expected_lpc in the fixture disagrees with "
            "the formula computed here; fix the fixture, not the test."
        )
        stamped = call["holdings"][0]
        assert "last_percent_change" in stamped, (
            f"tick at {tick_spec['t']}: run_monte_carlo was called with holdings "
            f"{stamped!r} — missing 'last_percent_change'. Without it, "
            "math_engine.run_monte_carlo:1162-1166 excludes this holding from "
            "current_symphony_return and the MC output is degenerate for this tick "
            "(MA-1)."
        )
        assert stamped["last_percent_change"] == pytest.approx(expected_lpc, abs=1e-9), (
            # abs=1e-9 tolerance: both sides are the same float arithmetic
            # ((c - y_close) / y_close) on exact decimal fixture inputs — this
            # guards only against representation-level float noise, not a
            # genuine formula mismatch.
            f"tick at {tick_spec['t']}: stamped last_percent_change="
            f"{stamped['last_percent_change']!r}, expected {expected_lpc!r} "
            f"(({tick_spec['c']} - {fx['yesterday_close']}) / {fx['yesterday_close']}). "
            "The per-tick lpc must be derived from the SAME ret the loop already "
            "computes for agg_ret, not a different basis."
        )
        assert stamped["ticker"] == fx["holdings"][0]["ticker"]
        assert stamped["allocation"] == fx["holdings"][0]["allocation"]


def test_mc_prob_varies_across_ticks_as_price_moves(mc_spy) -> None:
    """AC-1's charter assertion, end to end: with a real per-tick lpc now
    flowing into run_monte_carlo, the returned tick series' mc_prob values are
    NOT all identical as the bar price swings across the day — the
    day-constant-MC degeneracy (MA-1's core symptom) is dead.

    RED (pre-fix): every tick's mc_prob is computed from a holdings list with
    NO last_percent_change entries (all excluded from current_symphony_return
    = 0.0 for every tick), so the whole day's mc_prob series collapses to one
    constant value.
    """
    fx = _load_json(_BUILD_REPLAY_DAY_FIXTURE)
    kwargs = _build_replay_day_inputs(fx)

    ticks = synthetic_history.build_replay_day(**kwargs)

    assert len(ticks) == len(fx["ticks"]), (
        f"Expected {len(fx['ticks'])} ticks back from build_replay_day, got {len(ticks)}."
    )
    mc_values = [t["mc_prob"] for t in ticks if t["mc_prob"] is not None]
    assert len(mc_values) >= 2, (
        "Fewer than 2 non-None mc_prob values were returned — cannot assess variance. "
        "The fixture's historical_data_spec must yield sufficient eligible MC history "
        "(see ma1_build_replay_day_lpc_stamping.json's derivation note)."
    )
    assert len(set(mc_values)) > 1, (
        f"All {len(mc_values)} non-None mc_prob values in the tick series are IDENTICAL "
        f"({mc_values[0]!r}) despite the bar price swinging from "
        f"{fx['ticks'][0]['c']} to {fx['ticks'][-1]['c']} across the day "
        f"(prior close {fx['yesterday_close']}). This is the exact MA-1 day-constant-MC "
        "degeneracy the audit found — per-tick lpc is not reaching run_monte_carlo."
    )


# ===========================================================================
# 4 — Edge case: missing/zero prior close must not divide by zero or raise.
# ===========================================================================


def test_missing_prior_close_does_not_raise_or_produce_non_finite_lpc(mc_spy) -> None:
    """Edge case from the plan: 'zero prior close / degenerate price data (no
    div-by-zero — mirror production's guard)'. When a ticker is absent from
    yesterday_closes, the existing agg_ret loop falls back to
    ``yesterday_closes.get(ticker, c)`` (i.e. y_close == c, so ret == 0.0
    without ever dividing) and only computes ret at all when y_close > 0. The
    lpc-stamping fix must mirror this — never raise, never stamp NaN/inf.
    """
    fx = _load_json(_BUILD_REPLAY_DAY_FIXTURE)
    kwargs = _build_replay_day_inputs(fx)
    kwargs["yesterday_closes"] = {}  # ticker missing entirely -> y_close falls back to c

    ticks = synthetic_history.build_replay_day(**kwargs)  # must not raise

    assert len(ticks) == len(fx["ticks"])
    for call in mc_spy.calls:
        lpc = call["holdings"][0].get("last_percent_change")
        assert lpc is None or math.isfinite(lpc), (
            f"Missing prior close produced a non-finite stamped last_percent_change "
            f"({lpc!r}). Production's guard (y_close = yesterday_closes.get(ticker, c), "
            "ret only computed when y_close > 0) must be mirrored so a missing prior "
            "close degrades safely (0.0 or unstamped/None), never NaN/inf."
        )


# ===========================================================================
# 5 — Mutation safety (r1-engine's joblib-Parallel finding): the caller's
#     holdings object must never be mutated in place.
# ===========================================================================


def test_input_holdings_object_is_never_mutated_across_repeated_calls(mc_spy) -> None:
    """holdings is a closure-captured object reused across EVERY day of the
    same symphony's replay under Parallel(n_jobs=...) (synthetic_history.py
    generate_synthetic_history/process_day -> build_replay_day, called once
    per (symphony, day) with the SAME holdings reference for a given
    symphony). The fix must build a fresh per-tick copy and never mutate the
    caller's list/dicts in place — an in-place stamp would leak a stale
    last_percent_change into the NEXT day's (or a concurrent worker's) call
    for the same symphony.
    """
    fx = _load_json(_BUILD_REPLAY_DAY_FIXTURE)
    shared_holdings = [dict(h) for h in fx["holdings"]]
    original_keys = set(shared_holdings[0].keys())
    assert original_keys == {"ticker", "allocation"}, (
        "Fixture self-check: pre-fix holdings shape must be exactly "
        "{ticker, allocation} — matches the real construction sites' output."
    )

    for _ in range(2):  # simulate two days' worth of calls sharing one holdings object
        kwargs = _build_replay_day_inputs(fx)
        kwargs["holdings"] = shared_holdings  # deliberately the SAME object each time
        synthetic_history.build_replay_day(**kwargs)

        assert set(shared_holdings[0].keys()) == original_keys, (
            f"shared_holdings[0] gained new key(s) after build_replay_day: "
            f"{set(shared_holdings[0].keys()) - original_keys}. build_replay_day must "
            "stamp lpc onto a FRESH per-tick holdings copy, never mutate the caller's "
            "shared holdings object in place — under joblib Parallel(), this object is "
            "reused across every day of the same symphony's replay."
        )


# ===========================================================================
# 6 — Genuine gapped intraday bar (ADDENDUM 7 @ cd7e668d): a DIFFERENT
#     branch from the missing-prior-close edge case above, empirically
#     falsified by r1-review's independent check when a bilateral closure
#     between r1-test and r1-review incorrectly treated them as equivalent.
#
#     Missing prior close (test 4 above): yesterday_closes.get(ticker, c)
#     falls back to c -> ret = 0.0 -> a REAL, finite value, INCLUDED in the
#     MC sum.
#     Genuine gapped bar (this test): the ticker has NO bar entry for this
#     tick at all (a trading halt / data gap) -> the outer per-ticker guard
#     (`if ticker in intraday_by_date[date_str] and i < len(...)`) is False
#     -> tick_lpc never receives an entry for that ticker -> stamped
#     last_percent_change = None -> EXCLUDED from run_monte_carlo's
#     current_symphony_return sum entirely.
#
#     Both branches were independently verified correct BY CONSTRUCTION
#     (neither crashes, neither fabricates a value) — this test's job is
#     coverage of the previously-untested branch, not a bug fix. The plan's
#     own Edge Cases section names "missing/gapped bars (holiday, half-day)"
#     explicitly.
# ===========================================================================


def _build_gapped_bar_inputs(fx: dict) -> dict:
    """Build build_replay_day kwargs from the gapped-bar fixture's per-tick
    `{ticker: {c, vwap}}` bar-map shape -- distinct from
    _build_replay_day_inputs' single-ticker flat-list shape, since this
    fixture needs to express "ticker X has no bar this tick" per tick."""
    date_str = fx["date_str"]
    timestamps = [t["t"] for t in fx["ticks"]]
    intraday_by_date: dict = {date_str: {}}
    for h in fx["holdings"]:
        ticker = h["ticker"]
        bars_for_ticker = []
        for tick_spec in fx["ticks"]:
            if ticker in tick_spec["bars"]:
                bar = tick_spec["bars"][ticker]
                bars_for_ticker.append({"t": tick_spec["t"], "c": bar["c"], "vwap": bar["vwap"]})
            # else: no entry appended -- this ticker's bar list is SHORTER
            # than `timestamps`, exactly reproducing a genuine gap (the real
            # production shape: intraday_by_date[date][ticker] simply lacks
            # an element for a halted/gapped tick, so `i < len(...)` goes
            # False for that ticker at that tick_idx).
        intraday_by_date[date_str][ticker] = bars_for_ticker
    return {
        "sym_id": fx["sym_id"],
        "date_str": date_str,
        "holdings": [dict(h) for h in fx["holdings"]],
        "intraday_by_date": intraday_by_date,
        "timestamps": timestamps,
        "hist_data_up_to_yesterday": _build_history(fx["historical_data_spec"]),
        "yesterday_closes": fx["yesterday_closes"],
        "spy_today": fx["spy_today_return"],
    }


def test_gapped_intraday_bar_excludes_only_the_gapped_ticker_sibling_stays_real(mc_spy) -> None:
    """AC-1 golden (ADDENDUM 7): a genuine gapped intraday bar (a ticker with
    no bar entry for a given tick -- distinct from a missing prior close)
    must stamp last_percent_change=None for ONLY the gapped ticker at that
    tick -- excluded from run_monte_carlo's current_symphony_return sum,
    never a fabricated 0.0, never a crash/NaN -- while a SIBLING ticker that
    DOES have a bar the same tick still gets its real, finite per-tick lpc
    stamped and included.

    Fixture: AAA has a bar at both ticks; BBB has a bar only at tick 0 (a
    gap at tick 1). Verified live against the real build_replay_day before
    locking this fixture in (see the fixture's own "derivation" field) --
    not assumed, and not the same code path
    test_missing_prior_close_does_not_raise_or_produce_non_finite_lpc
    exercises (an earlier, incorrect closure claimed they were equivalent;
    r1-review falsified that claim empirically -- see ADDENDUM 7).
    """
    fx = _load_json(_GAPPED_BAR_FIXTURE)
    kwargs = _build_gapped_bar_inputs(fx)

    ticks = synthetic_history.build_replay_day(**kwargs)  # must not raise

    assert len(ticks) == len(fx["ticks"]), (
        f"Expected {len(fx['ticks'])} ticks back, got {len(ticks)}."
    )
    assert len(mc_spy.calls) == len(fx["ticks"]), (
        f"Expected one run_monte_carlo call per tick ({len(fx['ticks'])}), "
        f"observed {len(mc_spy.calls)}."
    )

    # Tick 0: both AAA and BBB have a bar -- both must be real, finite values
    # (at the prior close, so 0.0 for both -- a genuine zero-return tick, not
    # the gap case).
    tick0_holdings = {h["ticker"]: h for h in mc_spy.calls[0]["holdings"]}
    for ticker in ("AAA", "BBB"):
        lpc = tick0_holdings[ticker].get("last_percent_change")
        assert lpc is not None and math.isfinite(lpc), (
            f"Tick 0: {ticker} has a bar this tick and must get a real, finite "
            f"last_percent_change. Got {lpc!r}."
        )

    # Tick 1: AAA has a bar (c=101.0, prior close 100.0 -> lpc=0.01 exactly);
    # BBB has NO bar this tick -- the genuine gap -- and must be None.
    tick1_holdings = {h["ticker"]: h for h in mc_spy.calls[1]["holdings"]}
    aaa_lpc = tick1_holdings["AAA"].get("last_percent_change")
    assert aaa_lpc == pytest.approx(0.01, abs=1e-9), (
        # abs=1e-9: same-arithmetic float-representation tolerance only
        # (101.0 - 100.0) / 100.0 on exact decimal inputs, not a genuine
        # formula-mismatch allowance.
        f"Tick 1: AAA has a bar this tick (c=101.0, prior close=100.0) and must "
        f"get last_percent_change=0.01. Got {aaa_lpc!r}."
    )
    bbb_lpc = tick1_holdings["BBB"].get("last_percent_change")
    assert bbb_lpc is None, (
        f"Tick 1: BBB has NO bar this tick (a genuine gap -- trading halt / "
        f"data gap) and must be stamped last_percent_change=None (excluded "
        f"from run_monte_carlo's current_symphony_return sum via "
        f"math_engine.py:1162-1166's existing exclusion contract), never a "
        f"fabricated 0.0. Got {bbb_lpc!r}."
    )
    assert "ticker" in tick1_holdings["BBB"] and "allocation" in tick1_holdings["BBB"], (
        "BBB's holdings dict must still carry ticker/allocation even when "
        "excluded -- only last_percent_change is None, not the whole entry."
    )
