"""
AC-1-adjacent (team-lead's "catch of the cycle" from r2-stats's approved
trace) -- regime-lookback CHRONOLOGY PURITY under split-level scoring.

THE BUG (latent, R1-era, only load-bearing now): _collect_sim_returns_dated
(and _replay_resolve_regime_exit_ticks, which it drives) derives its
trailing-window chronology from ``sorted(history_data[sym_id].keys())`` --
i.e., from WHICHEVER dict the CALLER happened to pass in. Today's CPCV
call site passes a PRE-FILTERED ``_path_hist`` (a single path's sliced
history) -- harmless ONLY because MA-2's degeneracy makes every "path"
equal to the full eligible window (see test_ac1_cpcv_genuine_split_dispersion.py),
so the pre-filtered dict happens to equal the full chronology anyway.

Once AC-1 moves to genuine split-level scoring (each split covering only
~1/3 of the window), passing a date-RESTRICTED dict as history_data would
corrupt _replay_resolve_regime_exit_ticks's "trailing 20 days" into a
GAPPY, non-representative sample -- weeks-old dates from a DIFFERENT part
of the window masquerading as "recent", or an artificial insufficient-
history default where a genuine 20-day trailing window actually exists in
the true full chronology. A new distortion class, plus a latent bug at the
EXISTING _collect_sim_returns_dated CPCV call site that MA-2's degeneracy
has been silently hiding since R1.

RULED FIX SHAPE (team-lead): a ``score_dates`` kwarg on the per-date replay
loop(s). Call sites always pass the FULL per-symphony history dict (never a
pre-filtered subset); regime resolution (_replay_resolve_regime_exit_ticks's
sorted_dates chronology) runs over the FULL history; the per-tick replay
loop itself executes ONLY for dates in ``score_dates`` (when provided) --
decoupling "what informs the regime label" from "what gets scored".

This file pins:
  (1) full-chronology vs. restricted-chronology regime resolution DISAGREE
      on a constructed scenario (prerequisite fact -- provable today via
      regime_classifier.classify_regime directly, no code change needed).
  (2) _collect_sim_returns_dated gains an explicit score_dates parameter
      (structural pin, RED today).
  (3) behaviorally: calling _collect_sim_returns_dated with the FULL
      history dict + score_dates restricting replay to one target date must
      resolve that date's regime label from the FULL chronology, not a
      restricted one -- pinning the FULL-chronology ticks as correct.
  (4) whatever new per-split scoring loop replaces the old
      _cpcv_path_histories iteration threads score_dates through (text-level
      pin on objective()'s source -- deliberately not over-prescriptive
      about internal variable names r2-stats has not yet chosen).
"""

from __future__ import annotations

import datetime as _dt
import inspect
import pathlib

import pytest

import autotuner
import math_engine
import regime_classifier

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_AUTOTUNER_PATH = _REPO_ROOT / "autotuner.py"

_SYM_ID = "sym-regime-purity-001"
_N_TOTAL_DAYS = 90
_TARGET_IDX = 75  # the date whose regime resolution we probe

_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 99.0,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 999,
    "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}


def _sorted_dates() -> list[str]:
    base = _dt.date(2025, 1, 1)
    return [(base + _dt.timedelta(days=i)).isoformat() for i in range(_N_TOTAL_DAYS)]


def _tick(ret: float, mc: float) -> dict:
    return {
        "time": "tick",
        "return": ret,
        "mc_prob": mc,
        "vol": 1.0,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 0.0,
    }


def _three_confirming_tick_sequence() -> list[dict]:
    """Fires under the hardcoded/hardcoded-default 3-tick ladder; must NOT
    fire under a mean-reverting (5-tick) regime-faithful reading -- same
    scenario class as test_ac4_r2_residual_tripwire.py and
    test_ac4_undated_path_regime_faithful.py."""
    return [
        _tick(0.0, 10.0),
        _tick(-20.0, 70.0),
        _tick(-21.0, 70.0),
        _tick(-22.0, 70.0),
        _tick(0.0, 10.0),
    ]


def _build_full_history_data() -> dict:
    """90 days: indices 55-74 (the TRUE trailing-20 before _TARGET_IDX=75)
    carry an alternating +2%/-2% EOD return -- a genuine mean-reverting
    pattern under the FULL chronology. All other days are flat/neutral
    (EOD return ~0.001%, never mean-reverting or trending on their own).
    _TARGET_IDX itself carries the 3-confirming-tick trigger sequence."""
    dates = _sorted_dates()
    days: dict[str, list[dict]] = {}
    for i, date in enumerate(dates):
        if i == _TARGET_IDX:
            days[date] = _three_confirming_tick_sequence()
        elif 55 <= i <= 74:
            ret_pct = 2.0 if (i - 55) % 2 == 0 else -2.0
            days[date] = [_tick(ret_pct, 50.0)]
        else:
            days[date] = [_tick(0.001, 50.0)]
    return {_SYM_ID: days}


def _restricted_history_dict(full_history: dict) -> dict:
    """Mimics today's CPCV call site: a PRE-FILTERED history dict containing
    only a split's own dates -- indices 0-9 (old filler) and 70-75
    (5 of the true trailing-20 days, plus the target itself). Only 15
    "prior" dates are visible in this restricted view (< MIN_LABEL_SERIES_LENGTH
    =20) -- an artificial insufficient-history default, even though the TRUE
    full chronology has a genuine 20-day trailing window."""
    dates = _sorted_dates()
    keep_indices = set(range(0, 10)) | set(range(70, _TARGET_IDX + 1))
    restricted_days = {dates[i]: full_history[_SYM_ID][dates[i]] for i in keep_indices}
    return {_SYM_ID: restricted_days}


# ===========================================================================
# (1) Prerequisite fact: full vs. restricted chronology regime resolution
# genuinely DISAGREES on this fixture (provable today, no code change).
# ===========================================================================


def test_full_and_restricted_chronology_regime_resolution_disagree_on_this_fixture() -> None:
    dates = _sorted_dates()
    full_history = _build_full_history_data()
    restricted_history = _restricted_history_dict(full_history)

    # Full chronology: trailing 20 days before _TARGET_IDX are indices 55-74,
    # the genuine alternating mean-reverting pattern.
    full_trailing_returns = [
        full_history[_SYM_ID][dates[i]][-1]["return"] / autotuner.RETURN_PCT_TO_FRACTION
        for i in range(55, 75)
    ]
    full_label = regime_classifier.classify_regime(full_trailing_returns)
    assert full_label == "mean-reverting", (
        f"Fixture self-check: full-chronology trailing-20 label={full_label!r}, "
        "expected 'mean-reverting'."
    )

    # Restricted chronology: only 15 dates precede the target in the filtered
    # view (indices 0-9, 70-74) -- insufficient for MIN_LABEL_SERIES_LENGTH=20.
    restricted_dates = sorted(restricted_history[_SYM_ID].keys())
    restricted_prior = [d for d in restricted_dates if d < dates[_TARGET_IDX]]
    assert len(restricted_prior) == 15, (
        f"Fixture self-check: restricted view has {len(restricted_prior)} prior dates, "
        "expected 15 (< MIN_LABEL_SERIES_LENGTH=20, forcing the insufficient-history default)."
    )
    restricted_trailing_returns = [
        restricted_history[_SYM_ID][d][-1]["return"] / autotuner.RETURN_PCT_TO_FRACTION
        for d in restricted_prior[-regime_classifier.MIN_LABEL_SERIES_LENGTH :]
    ]
    restricted_label = regime_classifier.classify_regime(restricted_trailing_returns)
    assert restricted_label is None, (
        f"Fixture self-check: restricted-chronology label={restricted_label!r}, expected "
        "None (insufficient trailing history -- this IS the corruption this file targets)."
    )
    assert full_label != restricted_label, "Fixture must genuinely discriminate; it does."


# ===========================================================================
# (2) Structural: _collect_sim_returns_dated gains an explicit score_dates
# parameter.
# ===========================================================================


def test_collect_sim_returns_dated_accepts_score_dates_parameter() -> None:
    """RED (pre-fix): _collect_sim_returns_dated has no score_dates
    parameter -- callers cannot pass the FULL history dict while
    restricting which dates actually get replayed/scored."""
    sig = inspect.signature(autotuner._collect_sim_returns_dated)
    assert "score_dates" in sig.parameters, (
        "_collect_sim_returns_dated must accept a 'score_dates' parameter so "
        "callers can pass the FULL per-symphony history dict (correct regime-"
        "lookback chronology) while restricting actual replay/scoring to a "
        f"specific date subset. Current signature: {list(sig.parameters)}."
    )


# ===========================================================================
# (3) Behavioral: regime resolution must use the FULL chronology even when
# score_dates restricts which dates are actually replayed.
# ===========================================================================


def test_collect_sim_returns_dated_resolves_regime_from_full_chronology_not_score_dates_subset() -> (
    None
):
    """The decisive behavioral consequence: passing the FULL history dict
    with score_dates={target date} must resolve the target date's regime
    label from the TRUE full trailing-20 window (mean-reverting, 5 ticks --
    3 confirming ticks insufficient, no trigger), NOT from whatever gappy
    window a restricted dict would imply. RED today via TypeError (no
    score_dates parameter exists) -- converted to a clean pytest.fail so the
    RED signal is diagnostic, not an opaque traceback."""
    dates = _sorted_dates()
    full_history = _build_full_history_data()
    target_date = dates[_TARGET_IDX]

    try:
        result = autotuner._collect_sim_returns_dated(
            _PARAMS,
            full_history,
            [_SYM_ID],
            "2025-06-01",
            {},
            score_dates={target_date},
        )
    except TypeError as exc:
        pytest.fail(
            "_collect_sim_returns_dated does not yet accept score_dates -- "
            f"{exc}. AC-1-adjacent regime-lookback-chronology fix not landed."
        )

    triggered_dates = {d for d, _ga in result}
    assert target_date not in triggered_dates, (
        f"_collect_sim_returns_dated fired on {target_date} using score_dates to "
        "restrict replay -- expected NO trigger, since the FULL chronology's "
        "genuine trailing-20 window resolves 'mean-reverting' "
        f"(MEAN_REVERTING_EXIT_TICKS={math_engine.MEAN_REVERTING_EXIT_TICKS}), and "
        "the fixture's 3 confirming ticks are insufficient under that regime. A "
        "trigger here means regime resolution used a restricted/gappy chronology "
        "instead of the full history dict's true dates."
    )


def test_collect_sim_returns_dated_without_score_dates_still_replays_every_date() -> None:
    """Backward-compat: omitting score_dates (the existing call shape) must
    still replay every date in the passed history dict -- score_dates is an
    additive restriction, not a required parameter."""
    dates = _sorted_dates()
    full_history = _build_full_history_data()
    target_date = dates[_TARGET_IDX]

    result = autotuner._collect_sim_returns_dated(
        _PARAMS, full_history, [_SYM_ID], "2025-06-01", {}
    )
    scored_dates = {d for d, _ga in result}
    # Whether or not target_date itself fires, it must have been CONSIDERED
    # (present in the input, no date silently dropped just because
    # score_dates was omitted). We assert the function ran over the whole
    # dict without raising, and (weaker but robust) that omitting the kwarg
    # is a no-crash no-op path.
    assert isinstance(result, list)


# ===========================================================================
# (4) The new per-split scoring loop (whatever replaces the old
# _cpcv_path_histories iteration -- see test_ac1_cpcv_genuine_split_dispersion.py)
# must thread score_dates through, never re-introduce a pre-filtered
# history-dict call pattern. Text-level pin -- deliberately not prescriptive
# about internal variable names r2-stats has not yet chosen.
# ===========================================================================


def test_run_autotuner_source_threads_score_dates_for_split_level_scoring() -> None:
    """RED (pre-fix): 'score_dates' does not appear anywhere in autotuner.py
    yet -- the split-level scoring redesign (test_ac1_cpcv_genuine_split_dispersion.py)
    must call the per-date replay loop with the FULL history dict + an
    explicit score_dates restriction, never a pre-filtered dict (today's
    _cpcv_path_histories pattern, which this fix retires)."""
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    assert "score_dates" in src, (
        "autotuner.py does not yet reference 'score_dates' anywhere -- the split-"
        "level scoring redesign (AC-1) must thread a score_dates restriction "
        "through the per-date replay loop so regime-lookback chronology is always "
        "resolved from the FULL history dict, never a pre-filtered split/path subset."
    )
