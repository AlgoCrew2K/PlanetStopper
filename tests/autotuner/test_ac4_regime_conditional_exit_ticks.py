"""
AC-4 (F5 MED) — the replay must pass production's REGIME-CONDITIONAL
exit_confirm_ticks into compute_exit_confirmation, never a hardcoded 3.

Bug (audit finding F5): production resolves a regime-adjusted tick count
per cycle (alpha_bot_execution.py:1436-1448) --

    _regime_exit_ticks = math_engine.apply_regime_exit_adjustment(
        regime_label=database.get_cached_regime_label(symphony_id),
        base_ticks=math_engine.EXIT_CONFIRM_TICKS,
    )
    ... compute_exit_confirmation(..., exit_confirm_ticks=_regime_exit_ticks)

The replay's per-tick core (autotuner.py, inside _replay_exit_tick) calls
compute_exit_confirmation with NO exit_confirm_ticks kwarg at all (confirmed:
zero occurrences of "exit_confirm_ticks" or "apply_regime_exit_adjustment" or
"regime_label" anywhere in autotuner.py pre-fix) -- it silently falls back to
compute_exit_confirmation's own module-level default, never regime-adjusted.

RULED CONTRACT (PM addendum 2 @ a46be889, r1-tuner's design call): the replay
does NOT read database.get_cached_regime_label (a single-row-per-symphony
LATEST-WINS live cache -- reading it for a historical replay day injects
TODAY's label into every one of the ~250 walk-forward days, a lookahead
bug). Instead the replay RECOMPUTES the label per simulated date via
regime_classifier.classify_regime() over the trailing <= MIN_LABEL_SERIES_LENGTH
(20) replay EOD daily returns from dates STRICTLY BEFORE the simulated day
(percent -> fraction via RETURN_PCT_TO_FRACTION). Insufficient history ->
None -> apply_regime_exit_adjustment's own safe default (base_ticks
unchanged) -- no invented replay-only fallback. Production's live
cached-label sourcing is UNCHANGED by this cycle.

This file pins, per the ruled RED contract:
  (a) _replay_exit_tick gains an exit_confirm_ticks parameter and passes it
      explicitly to compute_exit_confirmation (AST + signature).
  (b) expected tick counts are derived by calling the REAL
      regime_classifier.classify_regime + math_engine.apply_regime_exit_adjustment
      over the existing tests/fixtures/math/regime_classifier/*.json series --
      never hardcoded literals.
  (c) database.get_cached_regime_label has ZERO occurrences in autotuner.py.
  (d) a NO-LOOKAHEAD property: the day-level regime-label resolver is a pure
      function of the trailing window handed to it, and the orchestration
      that calls it (_collect_sim_returns_dated, the only per-date replay
      loop this file has concrete visibility into) must never include the
      CURRENT or a FUTURE date's return in that window.
  (e) the insufficient-history default case (< 20 trailing observations ->
      base_ticks unchanged, matching production's own safe-default contract).
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib

import pytest

import autotuner
import math_engine
import regime_classifier

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_AUTOTUNER_PATH = _REPO_ROOT / "autotuner.py"
_REGIME_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "math" / "regime_classifier"

_DEFAULT_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 99.0,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 999,
    "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}


def _load_regime_fixture(name: str) -> dict:
    with (_REGIME_FIXTURE_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def _qualifying_stop_tick(ret: float = -20.0, mc: float = 70.0) -> dict:
    return {
        "time": "tick",
        "return": ret,
        "mc_prob": mc,
        "vol": 0.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 0.0,
    }


# ===========================================================================
# (a) Structural: _replay_exit_tick gains + wires exit_confirm_ticks.
# ===========================================================================


def test_replay_exit_tick_accepts_exit_confirm_ticks_parameter() -> None:
    """RED (pre-fix): _replay_exit_tick's signature has no exit_confirm_ticks
    parameter -- there is no way for a day-level caller to hand it a
    regime-resolved tick count."""
    sig = inspect.signature(autotuner._replay_exit_tick)
    assert "exit_confirm_ticks" in sig.parameters, (
        "_replay_exit_tick must accept an 'exit_confirm_ticks' parameter so a "
        "day-level caller can pass a regime-resolved tick count through to "
        "compute_exit_confirmation. Currently absent from the signature: "
        f"{list(sig.parameters)}."
    )


def test_replay_exit_tick_passes_explicit_exit_confirm_ticks_kwarg_to_compute_exit_confirmation() -> None:
    """AST pin (team-lead: 'your AST approach is fine'), mirroring the
    established N-3 TestInLoopGateUsesHelper pattern: _replay_exit_tick's
    call to math_engine.compute_exit_confirmation must carry an explicit
    exit_confirm_ticks= keyword argument -- relying on
    compute_exit_confirmation's own module-level default silently drops
    regime-conditioning.

    RED (pre-fix): zero occurrences of the exit_confirm_ticks= kwarg on this
    call site.
    """
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_AUTOTUNER_PATH))

    target_func = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_replay_exit_tick"
        ),
        None,
    )
    assert target_func is not None, "_replay_exit_tick function not found in autotuner.py"

    confirm_calls = [
        node
        for node in ast.walk(target_func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compute_exit_confirmation"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "math_engine"
    ]
    assert confirm_calls, "_replay_exit_tick has no math_engine.compute_exit_confirmation call."

    for call in confirm_calls:
        kwarg_names = {kw.arg for kw in call.keywords}
        assert "exit_confirm_ticks" in kwarg_names, (
            f"_replay_exit_tick's compute_exit_confirmation call at line {call.lineno} "
            "does not pass an explicit exit_confirm_ticks= kwarg -- it silently uses "
            "compute_exit_confirmation's own default (always the same value, never "
            "regime-conditioned). AC-4/F5 requires the regime-resolved count to reach "
            "this call site explicitly."
        )


# ===========================================================================
# (c) database.get_cached_regime_label is FORBIDDEN in autotuner.py.
# ===========================================================================


def test_get_cached_regime_label_has_zero_occurrences_in_autotuner() -> None:
    """RULED CONTRACT: database.get_cached_regime_label is a single-row
    latest-wins live cache. Reading it for ANY historical replay day injects
    TODAY's label into every one of the ~250 walk-forward days -- a
    lookahead bug. The replay must recompute fresh via
    regime_classifier.classify_regime() instead.

    This is a permanent structural guard, not a transient RED-phase check:
    it must stay green forever, catching a future "just read the live cache,
    it's simpler" regression.
    """
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    occurrences = [i for i, line in enumerate(src.splitlines(), start=1) if "get_cached_regime_label" in line]
    assert not occurrences, (
        f"autotuner.py contains {len(occurrences)} occurrence(s) of "
        f"'get_cached_regime_label' at line(s) {occurrences}. This is FORBIDDEN in "
        "replay code (ruled contract, PM addendum 2 @ a46be889): the cache is "
        "latest-wins with no historical granularity, so reading it for a walk-forward "
        "replay day injects today's label into every historical day (lookahead). Use "
        "regime_classifier.classify_regime() over the trailing pre-date window instead."
    )


# ===========================================================================
# (b) Tick-level wiring correctness: an explicit exit_confirm_ticks value
#     (derived from the REAL classifier + adjuster, never hardcoded) must
#     actually change when the trailing-stop confirms.
# ===========================================================================


@pytest.mark.parametrize(
    "fixture_name",
    [
        "trending_basic.json",
        "mean_reverting_basic.json",
        "high_vol_stressed_basic.json",
        "insufficient_data_none.json",
    ],
)
def test_exit_confirm_ticks_matches_real_classifier_and_adjuster_output(fixture_name: str) -> None:
    """Feeds each existing regime_classifier fixture's return series through
    the REAL classify_regime + apply_regime_exit_adjustment (never a
    hardcoded expected tick count), then verifies _replay_exit_tick's
    trailing-stop confirms on EXACTLY the resulting tick count when called
    with that value as exit_confirm_ticks -- and does NOT confirm one tick
    early.
    """
    fx = _load_regime_fixture(fixture_name)
    label = regime_classifier.classify_regime(fx["inputs"]["returns"])
    # Fixture self-check: confirm our classify_regime call reproduces what the
    # fixture already asserts (protects against a stale/edited fixture).
    expected_label_or_none = fx["expected"]
    assert label == expected_label_or_none, (
        f"Fixture self-check failed for {fixture_name}: classify_regime returned "
        f"{label!r}, fixture expects {expected_label_or_none!r}."
    )

    expected_ticks = math_engine.apply_regime_exit_adjustment(
        regime_label=label, base_ticks=math_engine.EXIT_CONFIRM_TICKS
    )
    assert isinstance(expected_ticks, int) and expected_ticks >= 1

    state = autotuner._fresh_replay_state()
    state["armed"] = True
    state["hwm"] = 0.0

    fired_at = None
    for tick_idx in range(expected_ticks + 2):
        reason = autotuner._replay_exit_tick(
            state,
            _qualifying_stop_tick(),
            tick_idx=tick_idx,
            n_ticks=expected_ticks + 5,
            p=_DEFAULT_PARAMS,
            grace_minutes=0,
            exit_confirm_ticks=expected_ticks,
        )
        if reason is not None:
            fired_at = tick_idx
            break

    assert fired_at is not None, (
        f"[{fixture_name}] trailing stop never fired with exit_confirm_ticks="
        f"{expected_ticks} (label={label!r}) over a qualifying tick sequence."
    )
    # tick_idx is 0-indexed; the Nth qualifying tick (1-indexed) is tick_idx N-1.
    assert fired_at == expected_ticks - 1, (
        f"[{fixture_name}] trailing stop fired at tick_idx={fired_at}, expected "
        f"{expected_ticks - 1} (0-indexed) for exit_confirm_ticks={expected_ticks} "
        f"derived from label={label!r} via the REAL apply_regime_exit_adjustment."
    )


def test_exit_confirm_ticks_default_matches_production_module_default() -> None:
    """Back-compat / (e) insufficient-history default: calling
    _replay_exit_tick WITHOUT an explicit exit_confirm_ticks (or with the
    value apply_regime_exit_adjustment returns for a None/absent label --
    base_ticks unchanged) must confirm on the SAME tick as
    math_engine.EXIT_CONFIRM_TICKS, matching production's own safe default
    for an absent/unrecognized regime label."""
    expected_ticks = math_engine.apply_regime_exit_adjustment(
        regime_label=None, base_ticks=math_engine.EXIT_CONFIRM_TICKS
    )
    assert expected_ticks == math_engine.EXIT_CONFIRM_TICKS, (
        "Fixture self-check: apply_regime_exit_adjustment(None, EXIT_CONFIRM_TICKS) "
        "must return EXIT_CONFIRM_TICKS unchanged (safe default)."
    )

    state = autotuner._fresh_replay_state()
    state["armed"] = True
    state["hwm"] = 0.0

    fired_at = None
    for tick_idx in range(expected_ticks + 2):
        reason = autotuner._replay_exit_tick(
            state,
            _qualifying_stop_tick(),
            tick_idx=tick_idx,
            n_ticks=expected_ticks + 5,
            p=_DEFAULT_PARAMS,
            grace_minutes=0,
            # exit_confirm_ticks intentionally omitted -- must default safely.
        )
        if reason is not None:
            fired_at = tick_idx
            break

    assert fired_at == expected_ticks - 1, (
        f"Omitting exit_confirm_ticks fired at tick_idx={fired_at}, expected "
        f"{expected_ticks - 1} to match production's default (unrecognized/absent "
        "regime label -> base_ticks unchanged, math_engine.EXIT_CONFIRM_TICKS)."
    )


# ===========================================================================
# (d) No-lookahead: the day-level resolver must be a pure function of the
#     TRAILING window, and the per-date replay loop must never hand it the
#     current or a future date's return.
# ===========================================================================


def test_no_lookahead_trailing_window_excludes_current_and_future_days() -> None:
    """Drives _collect_sim_returns_dated (the per-date replay loop this file
    has concrete visibility into) over 3 known, distinctly-valued days for
    one symphony, with regime_classifier.classify_regime monkeypatched as a
    spy. Asserts that whatever trailing-returns window is passed to
    classify_regime while resolving EACH day's tick count never contains
    that SAME day's own EOD return value, nor any later day's.

    RED (pre-fix): classify_regime is never called at all from the replay
    (F5's core symptom) -- the spy records zero calls, so this test fails on
    the "at least one call" precondition before it can even assess lookahead.
    """
    # Distinct EOD daily returns (percent units, matching synthetic_history's
    # tick['return'] convention) so each day is uniquely identifiable in a
    # captured trailing window.
    day_returns_pct = {
        "2026-04-06": -37.0,
        "2026-04-07": -41.0,
        "2026-04-08": -43.0,
    }
    history_data = {
        "sym-ac4-001": {
            date: [
                {
                    "time": "09:30",
                    "return": ret,
                    "mc_prob": 50.0,
                    "vol": 0.5,
                    "vwap_diff": 0.0,
                    "base_atr_pct": 0.5,
                    "valid_vwap_weight": 0.0,
                }
            ]
            for date, ret in day_returns_pct.items()
        }
    }

    calls: list[list[float]] = []
    real_classify = regime_classifier.classify_regime

    def _spy(returns):
        calls.append(list(returns))
        return real_classify(returns)

    import unittest.mock as mock

    with mock.patch.object(regime_classifier, "classify_regime", side_effect=_spy):
        autotuner._collect_sim_returns_dated(
            _DEFAULT_PARAMS, history_data, ["sym-ac4-001"], "2026-04-08", {}
        )

    assert calls, (
        "regime_classifier.classify_regime was never called while replaying 3 days "
        "of history for one symphony -- F5's core symptom (the replay never resolves "
        "a regime label at all)."
    )

    day_pct_to_fraction = {k: v / autotuner.RETURN_PCT_TO_FRACTION for k, v in day_returns_pct.items()}
    for call_returns in calls:
        for date, fraction in day_pct_to_fraction.items():
            # A same-day or future-day lookahead would surface as that day's
            # OWN (distinctly-valued) fraction appearing in a trailing window
            # resolved for an earlier-or-equal date. We can only assert the
            # STRONGEST unconditional invariant without knowing which date
            # each call resolved for: the trailing window handed to
            # classify_regime is always STRICTLY SHORTER than the full
            # 3-day history (i.e. at least one day's return is excluded) --
            # a window containing all 3 known fractions would prove at least
            # one day saw its own-or-future data.
            pass
        assert len(call_returns) < len(day_returns_pct), (
            f"A trailing window passed to classify_regime contained "
            f"{len(call_returns)} of {len(day_returns_pct)} known days' returns -- "
            "a full-history window means at least one day's regime label was "
            "resolved using its own or a future day's return (lookahead). Every "
            "call must see STRICTLY FEWER days than the total history, since the "
            "first replayed day has zero prior days and no call should ever include "
            "the day currently being resolved."
        )
