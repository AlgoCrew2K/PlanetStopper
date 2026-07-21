"""
RED tests — F-020: History per-day drill-down (dates + per-day exits).

Source: docs/audit/confidence-program/running-register.md lines 126-131 —
"F-020 — DEFECT (LOW, observability/product gap): no UI affordance to view
an arbitrary past day's exits ... the History tab's daily bar chart
(static/history.js:renderDailyChart:90) takes only a daily_alpha numeric
array — no paired dates, no tooltips ... There is NO UI affordance anywhere
to drill into a specific historical day's exits ... REMEDIATION: add a
per-day drill-down (date-paired tooltips / a selectable historical day
showing that day's exits)."

Root cause confirmed at analytics.py:1893 — `stats["daily_alpha"] =
[daily_map[d] for d in sorted(daily_map)]` — the dates (daily_map's own
keys) are ALREADY computed, then discarded on flatten. This file pins:

  AC-3a: `daily_dates` — a parallel array to `daily_alpha` (same length,
         same sort order), sourced from the SAME already-computed
         `daily_map` keys — zero new math.
  AC-3b: `daily_exits` — {date_str: [exit dict, ...]}, grouped from the
         SAME already-parsed post_mortem trigger entries the multi-day loop
         already reads (exit_reason/saved_pct_guard_alpha/saved_dollars) —
         pure reshaping, no new computation. Entries carry AT LEAST
         symphony_id/reason/detail (a shape subset of the existing
         todays_exits per-entry convention — NOT full key-set equality,
         since the multi-day loop has zero DB dependency today and forcing
         a symphony_name lookup into it would be new I/O, not reshaping).
  Edge case (plan): exits missing optional fields render gracefully — a
  day with zero VALID triggers must not crash and must degrade honestly
  (omitted or empty list, never a fabricated entry).

This is a pure analytics.py unit test (no Flask route, no DB — the
multi-day get_history_summary loop reads only post_mortem_*.json files).
-n0 only.
"""

from __future__ import annotations

import json

import analytics


def _write_post_mortem(tmp_path, date_str: str, triggers: list[dict]) -> None:
    (tmp_path / f"post_mortem_{date_str}.json").write_text(
        json.dumps({"triggers": triggers}), encoding="utf-8"
    )


def _valid_trigger(
    *,
    symphony_id: str,
    reason: str = "Take-Profit",
    alpha: float = 0.42,
    dollars: float = 12.34,
) -> dict:
    """A minimally-valid post_mortem trigger entry — carries the
    if_held_source provenance stamp required by is_valid_post_mortem_entry
    (analytics.py:98-112), so get_history_summary's loop actually counts it.
    """
    return {
        "if_held_source": "shadow_history",
        "symphony_id": symphony_id,
        "exit_reason": reason,
        "saved_pct_guard_alpha": alpha,
        "saved_dollars": dollars,
        "detail": alpha,
        "time_triggered": "14:00:00",
    }


# ---------------------------------------------------------------------------
# AC-3a: daily_dates parallel array
# ---------------------------------------------------------------------------


def test_daily_dates_key_present_and_same_length_as_daily_alpha(tmp_path):
    _write_post_mortem(tmp_path, "2026-07-01", [_valid_trigger(symphony_id="S1")])
    _write_post_mortem(tmp_path, "2026-07-02", [_valid_trigger(symphony_id="S2")])
    _write_post_mortem(tmp_path, "2026-07-03", [_valid_trigger(symphony_id="S3")])

    stats = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

    assert "daily_dates" in stats, (
        f"get_history_summary must expose 'daily_dates'; got keys {sorted(stats.keys())}"
    )
    assert len(stats["daily_dates"]) == len(stats["daily_alpha"]), (
        f"daily_dates must be a PARALLEL array to daily_alpha (same length): "
        f"len(daily_dates)={len(stats['daily_dates'])}, len(daily_alpha)={len(stats['daily_alpha'])}"
    )


def test_daily_dates_values_match_fixture_dates_in_sorted_order(tmp_path):
    """daily_dates must echo back the SAME dates as the fixture files I wrote
    (my own test input, not a producer-computed value), in the SAME sorted
    order the existing daily_alpha flatten already uses (sorted(daily_map)).
    """
    fixture_dates = ["2026-07-03", "2026-07-01", "2026-07-02"]  # written out of order
    for i, d in enumerate(fixture_dates):
        _write_post_mortem(tmp_path, d, [_valid_trigger(symphony_id=f"S{i}")])

    stats = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

    assert stats["daily_dates"] == sorted(fixture_dates), (
        f"daily_dates must equal the fixture's own dates in sorted order "
        f"{sorted(fixture_dates)!r}, got {stats['daily_dates']!r}"
    )


def test_daily_dates_index_aligns_with_daily_alpha_index(tmp_path):
    """The i-th daily_dates entry must correspond to the i-th daily_alpha
    entry — proven by writing distinguishable alpha values per day and
    checking the date at each alpha's index matches that day's fixture date.
    """
    _write_post_mortem(
        tmp_path, "2026-07-01", [_valid_trigger(symphony_id="S1", alpha=1.0, dollars=10.0)]
    )
    _write_post_mortem(
        tmp_path, "2026-07-02", [_valid_trigger(symphony_id="S2", alpha=2.0, dollars=20.0)]
    )

    stats = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

    alpha_by_date = dict(zip(stats["daily_dates"], stats["daily_alpha"]))
    assert alpha_by_date.get("2026-07-01") == 1.0, f"got alpha_by_date={alpha_by_date!r}"
    assert alpha_by_date.get("2026-07-02") == 2.0, f"got alpha_by_date={alpha_by_date!r}"


# ---------------------------------------------------------------------------
# AC-3b: daily_exits per-day trigger grouping
# ---------------------------------------------------------------------------


def test_daily_exits_key_present_and_is_a_dict(tmp_path):
    _write_post_mortem(tmp_path, "2026-07-01", [_valid_trigger(symphony_id="S1")])

    stats = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

    assert "daily_exits" in stats, (
        f"get_history_summary must expose 'daily_exits'; got keys {sorted(stats.keys())}"
    )
    assert isinstance(stats["daily_exits"], dict), (
        f"daily_exits must be a dict keyed by date string, got {type(stats['daily_exits'])}"
    )


def test_daily_exits_grouped_under_the_correct_date_key(tmp_path):
    _write_post_mortem(
        tmp_path, "2026-07-01", [_valid_trigger(symphony_id="S1", reason="Trailing Stop")]
    )
    _write_post_mortem(
        tmp_path, "2026-07-02", [_valid_trigger(symphony_id="S2", reason="VWAP Breakdown")]
    )

    stats = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

    day1 = stats["daily_exits"].get("2026-07-01")
    assert day1 is not None and len(day1) == 1, f"expected 1 exit under 2026-07-01, got {day1!r}"
    assert day1[0].get("reason") == "Trailing Stop", f"got {day1[0]!r}"

    day2 = stats["daily_exits"].get("2026-07-02")
    assert day2 is not None and len(day2) == 1, f"expected 1 exit under 2026-07-02, got {day2!r}"
    assert day2[0].get("reason") == "VWAP Breakdown", f"got {day2[0]!r}"


def test_daily_exits_multiple_triggers_same_day_all_present(tmp_path):
    _write_post_mortem(
        tmp_path,
        "2026-07-01",
        [
            _valid_trigger(symphony_id="S1", reason="Take-Profit"),
            _valid_trigger(symphony_id="S2", reason="VWAP Bleed Cut"),
        ],
    )

    stats = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

    day1 = stats["daily_exits"].get("2026-07-01")
    assert day1 is not None and len(day1) == 2, (
        f"a day with 2 triggers must expose both in daily_exits — got {day1!r}"
    )
    reasons = {e.get("reason") for e in day1}
    assert reasons == {"Take-Profit", "VWAP Bleed Cut"}, f"got reasons={reasons!r}"


def test_daily_exits_entries_carry_a_ts_field_sourced_like_todays_exits(tmp_path):
    """RED — foc-rev finding #3: `time_triggered` is available on the SAME
    `t` dict, in the SAME loop iteration, as zero new I/O (unlike
    symphony_name, which genuinely needs database.load_state() and was
    correctly excluded per this file's own AC-3b docstring) — yet it was
    silently dropped from daily_exits. The `todays_exits` path 20 lines
    below already solved this EXACT class of gap ("Finding 11" comment,
    analytics.py ~2022-2024): `t.get("time_triggered") or t.get("timestamp")
    or t.get("ts", "")`. daily_exits entries must use the same sourcing so
    the drill-down's exit time isn't a silent blank (the client's
    renderDayDrilldown already reads `e.time_triggered || e.ts || ''`
    expecting exactly this).
    """
    _write_post_mortem(
        tmp_path,
        "2026-07-01",
        [_valid_trigger(symphony_id="S-known", reason="Take-Profit")],  # time_triggered="14:00:00"
    )

    stats = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

    entry = stats["daily_exits"]["2026-07-01"][0]
    ts_value = entry.get("time_triggered") or entry.get("ts")
    assert ts_value == "14:00:00", (
        f"daily_exits entry must carry the trigger's time_triggered/ts value (fixture "
        f"wrote time_triggered='14:00:00') — got entry={entry!r}. A drill-down row with "
        f"a blank exit time undercuts AC-3's 'individual exit's details are inspectable'."
    )


def test_daily_exits_entries_carry_symphony_id_reason_and_detail(tmp_path):
    """Shape subset of the existing todays_exits per-entry convention — no
    new computation, just re-exposing fields already present on the parsed
    trigger dict (symphony_id/exit_reason/detail-equivalent).
    """
    _write_post_mortem(
        tmp_path, "2026-07-01", [_valid_trigger(symphony_id="S-known", reason="Take-Profit")]
    )

    stats = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

    entry = stats["daily_exits"]["2026-07-01"][0]
    assert entry.get("symphony_id") == "S-known", f"got {entry!r}"
    assert entry.get("reason") == "Take-Profit", f"got {entry!r}"
    assert "detail" in entry, f"entry must carry a 'detail' field (guard-alpha), got {entry!r}"


# ---------------------------------------------------------------------------
# Edge case (plan): exits missing optional fields render gracefully.
# ---------------------------------------------------------------------------


def test_daily_exits_day_with_zero_valid_triggers_does_not_crash(tmp_path):
    """A file whose only trigger fails is_valid_post_mortem_entry (missing
    if_held_source) must not crash get_history_summary, and must not
    fabricate a daily_exits entry for that day.
    """
    (tmp_path / "post_mortem_2026-07-01.json").write_text(
        json.dumps({"triggers": [{"exit_reason": "Take-Profit"}]}),  # no if_held_source
        encoding="utf-8",
    )

    stats = analytics.get_history_summary(days=30, base_dir=str(tmp_path))  # must not raise

    day1 = stats["daily_exits"].get("2026-07-01")
    assert day1 in (None, []), (
        f"a day with zero VALID triggers must degrade to omitted or an empty list, "
        f"never a fabricated entry — got {day1!r}"
    )


def test_daily_exits_empty_history_is_an_empty_dict(tmp_path):
    stats = analytics.get_history_summary(days=30, base_dir=str(tmp_path))  # no files at all

    assert stats["daily_exits"] == {}, (
        f"no post_mortem files -> daily_exits must be {{}}, got {stats['daily_exits']!r}"
    )
    assert stats["daily_dates"] == [], (
        f"no post_mortem files -> daily_dates must be [], got {stats['daily_dates']!r}"
    )


# ---------------------------------------------------------------------------
# Regression guard: pre-existing keys untouched.
# ---------------------------------------------------------------------------


def test_existing_keys_still_present_alongside_new_fields(tmp_path):
    _write_post_mortem(tmp_path, "2026-07-01", [_valid_trigger(symphony_id="S1")])

    stats = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

    for key in ("total_alpha", "total_saved", "trigger_count", "wins", "by_reason", "daily_alpha"):
        assert key in stats, (
            f"pre-existing key {key!r} must remain present; got {sorted(stats.keys())}"
        )
