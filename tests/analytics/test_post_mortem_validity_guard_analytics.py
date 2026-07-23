"""
RED tests — F-008 Post-Mortem Data Integrity, analytics.py consumers
(AC-5 load_post_mortem_history, AC-5b get_history_summary).

Companion to tests/app/test_post_mortem_validity_guard.py (app.py's
guard_alpha_summary). Same contract, applied to the History/Performance tab
data layer:

  AC-5:  analytics.load_post_mortem_history(days, base_dir) must exclude an
         entry lacking a recognized if_held_source from the returned
         internal-shape dict (feeds History rows 2.1/2.4 + Performance
         rows 3.x).
  AC-5b: analytics.get_history_summary(days, base_dir) — a SEPARATE,
         independent glob+sum (analytics.py:1785) that is the ACTUAL
         producer of the History tab's total_saved/total_alpha/win_rate/
         by_reason headline stats via GET /api/history/<days>
         (app.py:3085) — must apply the SAME guard. Added at plan-approval
         2026-07-20 after this consumer was found to have the identical
         defect but was not named by the original AC-5 wording; see
         feature-plans/fix-f008-data-integrity.md Decisions table.

Both functions must route through ONE shared helper
(analytics.is_valid_post_mortem_entry, per this file's direct unit tests
below) so the two live consumers cannot diverge — same requirement
app.py's guard_alpha_summary is held to.

Trusted if_held_source set (2026-07-20 plan-approval ruling — wider than
the plan's original "only shadow_history" wording): "shadow_history",
"shadow_history_post_cutoff", "bot_state_fallback" — reporting.py's own
3-value contract. Only a missing/unrecognized value is excluded.

FIXTURE PROVENANCE: same fixtures as test_post_mortem_validity_guard.py —
schema-derived valid/contaminated shapes under
tests/fixtures/app/post_mortem_validity_guard/, plus the REAL captured
tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-06-22.json
(verified unstamped by direct inspection).

All dollar/count assertions are derived from the fixture files at test
runtime — never hardcoded (feedback_no_hardcoded_test_values).
"""

from __future__ import annotations

import json
import pathlib
from datetime import date, timedelta

import pytest

import analytics

_VALID_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "app" / "post_mortem_validity_guard"
)
_REAL_DROPLET_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "prod_droplet" / "post_mortems"
)


def _recent_date_str(days_ago: int) -> str:
    """A date guaranteed inside get_history_summary's wall-clock window.

    get_history_summary computes its window from datetime.now() at call
    time, not from the fixture's baked-in "date" field (which is never read
    for filtering — only the FILENAME date is) — unlike load_post_mortem_
    history, which just keeps the N most-recent files by filename sort with
    no calendar arithmetic. Writing fixtures under a dynamically-recent
    filename (team-lead ruling 2026-07-20: mock/freeze the clock OR use
    dynamically-recent dates — this file uses the latter) keeps these tests
    deterministic regardless of which day the suite runs, rather than
    coupling them to a fixed historical date that only happens to be inside
    the window today.
    """
    return (date.today() - timedelta(days=days_ago)).isoformat()


_PM_VALID_SHADOW_HISTORY = json.loads(
    (_VALID_FIXTURE_DIR / "post_mortem_2026-02-02_valid_shadow_history.json").read_text()
)
_PM_VALID_POST_CUTOFF = json.loads(
    (_VALID_FIXTURE_DIR / "post_mortem_2026-02-03_valid_post_cutoff.json").read_text()
)
_PM_VALID_BOT_STATE_FALLBACK = json.loads(
    (_VALID_FIXTURE_DIR / "post_mortem_2026-02-04_valid_bot_state_fallback.json").read_text()
)
_PM_CONTAMINATED_MISSING_STAMP = json.loads(
    (_VALID_FIXTURE_DIR / "post_mortem_2026-07-09_contaminated_missing_stamp.json").read_text()
)
_PM_REAL_CONTAMINATED_06_22 = json.loads(
    (_REAL_DROPLET_DIR / "post_mortem_2026-06-22.json").read_text()
)


def _write_pm(base_dir: pathlib.Path, date_str: str, payload: dict) -> None:
    (base_dir / f"post_mortem_{date_str}.json").write_text(json.dumps(payload), encoding="utf-8")


def _sum_dollars(*pms: dict) -> float:
    return sum(t["saved_dollars"] for pm in pms for t in pm.get("triggers", []))


def _count_triggers(*pms: dict) -> int:
    return sum(len(pm.get("triggers", [])) for pm in pms)


# ===========================================================================
# 0. Direct unit tests of the shared helper contract
# ===========================================================================


class TestSharedValidityHelperContract:
    """Pins the shared helper's NAME and behavior so app.py, load_post_mortem_
    history, and get_history_summary cannot each invent their own logic and
    silently diverge. Name chosen here: analytics.is_valid_post_mortem_entry
    (public — consumed cross-module by app.py). See .claude/tdd-handoff.md if
    the implementer needs to negotiate a different name."""

    @pytest.mark.parametrize(
        "if_held_source",
        ["shadow_history", "shadow_history_post_cutoff", "bot_state_fallback"],
    )
    def test_recognized_values_are_valid(self, if_held_source):
        entry = {"if_held_source": if_held_source, "saved_dollars": 10.0}
        assert analytics.is_valid_post_mortem_entry(entry) is True, (
            f"if_held_source={if_held_source!r} is one of the producer's 3 recognized "
            "values and must be trusted"
        )

    def test_missing_if_held_source_is_invalid(self):
        entry = {"saved_dollars": 10.0}
        assert analytics.is_valid_post_mortem_entry(entry) is False

    @pytest.mark.parametrize("bad_value", ["", None, "made_up_source", "SHADOW_HISTORY", 0, 1])
    def test_unrecognized_or_wrong_type_if_held_source_is_invalid(self, bad_value):
        entry = {"if_held_source": bad_value, "saved_dollars": 10.0}
        assert analytics.is_valid_post_mortem_entry(entry) is False, (
            f"if_held_source={bad_value!r} is not one of the 3 recognized values and "
            "must be excluded"
        )

    def test_non_dict_entry_does_not_raise(self):
        """D-1-style defensiveness: a malformed entry (not a dict) must not crash
        the caller — treat as invalid rather than raising."""
        for bad_entry in (None, "not-a-dict", 42, []):
            assert analytics.is_valid_post_mortem_entry(bad_entry) is False

    @pytest.mark.parametrize(
        "unhashable_value",
        [["shadow_history"], {"nested": "dict"}, [1, 2, 3]],
        ids=["list", "dict", "list-of-ints"],
    )
    def test_unhashable_if_held_source_value_does_not_raise(self, unhashable_value):
        """REVIEW GAP (post-GREEN): a JSON array or object is a perfectly valid
        JSON value for if_held_source — a malformed/corrupted-but-syntactically-
        valid post-mortem file could have `"if_held_source": ["shadow_history"]`
        or `{}` instead of a plain string. `entry.get("if_held_source") in
        _TRUSTED_IF_HELD_SOURCES` (a frozenset membership test) raises
        `TypeError: unhashable type` for these — a real crash, not a graceful
        exclusion. Confirmed via direct probe of the GREEN implementation
        (500964ff) before writing this test. AC-4 requires 'never a crash';
        the plan's Security Considerations require the guard not to leak
        exception strings — an uncaught TypeError bubbling into a Flask 500
        violates both.
        """
        entry = {"if_held_source": unhashable_value, "saved_dollars": 10.0}
        assert analytics.is_valid_post_mortem_entry(entry) is False, (
            f"if_held_source={unhashable_value!r} (a valid JSON value, not a string) "
            "must be treated as invalid, not crash the caller"
        )


# ===========================================================================
# AC-5: load_post_mortem_history excludes/includes by provenance
# ===========================================================================


class TestLoadPostMortemHistoryValidityGuard:
    def test_missing_stamp_entry_absent_from_returned_history(self, tmp_path):
        _write_pm(tmp_path, "2026-07-09", _PM_CONTAMINATED_MISSING_STAMP)

        history = analytics.load_post_mortem_history(days=60, base_dir=str(tmp_path))

        # Either the date is absent entirely, or present with an empty/absent
        # entry for the contaminated symphony — either way, its data must not
        # be queryable via the DV1 internal shape.
        contaminated_name = _PM_CONTAMINATED_MISSING_STAMP["triggers"][0]["symphony_name"]
        day = history.get("2026-07-09", {})
        assert contaminated_name not in day, (
            f"{contaminated_name!r} (missing if_held_source) must not appear in "
            f"load_post_mortem_history's output; got day contents: {list(day.keys())}"
        )

    def test_valid_and_contaminated_mixed_only_valid_symphony_present(self, tmp_path):
        _write_pm(tmp_path, "2026-02-02", _PM_VALID_SHADOW_HISTORY)
        _write_pm(tmp_path, "2026-07-09", _PM_CONTAMINATED_MISSING_STAMP)

        history = analytics.load_post_mortem_history(days=60, base_dir=str(tmp_path))

        valid_names = {t["symphony_name"] for t in _PM_VALID_SHADOW_HISTORY["triggers"]}
        contaminated_name = _PM_CONTAMINATED_MISSING_STAMP["triggers"][0]["symphony_name"]

        assert "2026-02-02" in history, "the valid day must still load"
        for name in valid_names:
            assert name in history["2026-02-02"], (
                f"valid symphony {name!r} must be present in the valid day's history"
            )
        contaminated_day = history.get("2026-07-09", {})
        assert contaminated_name not in contaminated_day

    @pytest.mark.parametrize(
        "fixture",
        [_PM_VALID_SHADOW_HISTORY, _PM_VALID_POST_CUTOFF, _PM_VALID_BOT_STATE_FALLBACK],
        ids=["shadow_history", "shadow_history_post_cutoff", "bot_state_fallback"],
    )
    def test_each_recognized_value_included(self, tmp_path, fixture):
        date_str = fixture["date"]
        _write_pm(tmp_path, date_str, fixture)

        history = analytics.load_post_mortem_history(days=60, base_dir=str(tmp_path))

        assert date_str in history, (
            f"if_held_source={fixture['triggers'][0]['if_held_source']!r} must be trusted "
            f"and the day must load; got keys={list(history.keys())}"
        )
        for t in fixture["triggers"]:
            assert t["symphony_name"] in history[date_str], (
                f"{t['symphony_name']!r} must be present — its if_held_source is recognized"
            )

    def test_real_captured_06_22_symphonies_absent(self, tmp_path):
        """The real production contamination (11 unstamped triggers) must not
        surface via the History/Performance data layer either."""
        _write_pm(tmp_path, "2026-06-22", _PM_REAL_CONTAMINATED_06_22)

        history = analytics.load_post_mortem_history(days=60, base_dir=str(tmp_path))

        day = history.get("2026-06-22", {})
        real_names = {t["symphony_name"] for t in _PM_REAL_CONTAMINATED_06_22["triggers"]}
        leaked = real_names & set(day.keys())
        assert not leaked, (
            f"real 06-22 capture leaked {len(leaked)} unstamped symphonies into "
            f"load_post_mortem_history: {leaked}"
        )

    def test_malformed_json_still_skipped_not_double_handled(self, tmp_path):
        """Existing contract (test_analytics.py test 3) must survive the new
        guard unchanged: malformed JSON is skipped by the except-path, not
        evaluated for provenance at all."""
        _write_pm(tmp_path, "2026-02-02", _PM_VALID_SHADOW_HISTORY)
        (tmp_path / "post_mortem_2026-02-05.json").write_text("{not valid json", encoding="utf-8")

        history = analytics.load_post_mortem_history(days=60, base_dir=str(tmp_path))

        assert set(history.keys()) == {"2026-02-02"}, (
            f"malformed file must be silently skipped (not raise, not appear); "
            f"got keys={list(history.keys())}"
        )


# ===========================================================================
# AC-5b: get_history_summary (the THIRD consumer, History-tab headline)
# ===========================================================================


class TestGetHistorySummaryValidityGuard:
    """get_history_summary (analytics.py:1785) is the function GET
    /api/history/<days> (app.py:3085) actually calls to produce total_saved
    / total_alpha / win_rate / by_reason — a fully independent glob+sum from
    load_post_mortem_history, with the identical unguarded-sum defect. Added
    to scope at plan-approval 2026-07-20 (AC-5b)."""

    def test_missing_stamp_entry_excluded_from_total_saved(self, tmp_path):
        _write_pm(tmp_path, _recent_date_str(1), _PM_CONTAMINATED_MISSING_STAMP)

        result = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

        assert result["total_saved"] == pytest.approx(0.0, abs=1e-9), (
            f"total_saved={result['total_saved']} — the contaminated day's dollars leaked "
            "into get_history_summary's headline total"
        )
        assert result["trigger_count"] == 0, (
            f"trigger_count={result['trigger_count']} — the missing-stamp entry must not be counted"
        )

    def test_valid_and_contaminated_mixed_total_saved_matches_valid_only(self, tmp_path):
        _write_pm(tmp_path, _recent_date_str(1), _PM_VALID_SHADOW_HISTORY)
        _write_pm(tmp_path, _recent_date_str(2), _PM_CONTAMINATED_MISSING_STAMP)

        expected_saved = _sum_dollars(_PM_VALID_SHADOW_HISTORY)
        expected_count = _count_triggers(_PM_VALID_SHADOW_HISTORY)

        result = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

        assert result["total_saved"] == pytest.approx(expected_saved, abs=0.01), (
            f"total_saved={result['total_saved']} != valid-only {expected_saved}"
        )
        assert result["trigger_count"] == expected_count

    @pytest.mark.parametrize(
        "fixture",
        [_PM_VALID_SHADOW_HISTORY, _PM_VALID_POST_CUTOFF, _PM_VALID_BOT_STATE_FALLBACK],
        ids=["shadow_history", "shadow_history_post_cutoff", "bot_state_fallback"],
    )
    def test_each_recognized_value_included_in_total(self, tmp_path, fixture):
        # Dynamic filename date — the fixture's own baked-in "date" field is
        # never read by get_history_summary's window filter (filename-only).
        _write_pm(tmp_path, _recent_date_str(1), fixture)
        expected_saved = _sum_dollars(fixture)
        expected_count = _count_triggers(fixture)

        result = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

        assert result["total_saved"] == pytest.approx(expected_saved, abs=0.01), (
            f"if_held_source={fixture['triggers'][0]['if_held_source']!r} must be trusted"
        )
        assert result["trigger_count"] == expected_count

    def test_real_captured_06_22_excluded_from_history_tab_total(self, tmp_path):
        """The specific defect that motivated F-008: the History tab's headline
        total_saved must not include the real 06-22 capture's contamination."""
        _write_pm(tmp_path, _recent_date_str(1), _PM_VALID_SHADOW_HISTORY)
        _write_pm(tmp_path, _recent_date_str(2), _PM_REAL_CONTAMINATED_06_22)

        expected_saved = _sum_dollars(_PM_VALID_SHADOW_HISTORY)
        expected_count = _count_triggers(_PM_VALID_SHADOW_HISTORY)
        real_contaminated = _sum_dollars(_PM_REAL_CONTAMINATED_06_22)
        assert real_contaminated != 0.0, "fixture integrity: real 06-22 must be nonzero"

        result = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

        assert result["total_saved"] == pytest.approx(expected_saved, abs=0.01), (
            f"total_saved={result['total_saved']} != valid-only {expected_saved} — the "
            f"real 06-22 capture's ${real_contaminated:.2f} leaked into the History tab total"
        )
        assert result["trigger_count"] == expected_count

    def test_all_valid_golden_regression_matches_naive_sum(self, tmp_path):
        """AC-6 for the History-tab consumer: all-valid input is unaffected by
        the guard."""
        _write_pm(tmp_path, _recent_date_str(1), _PM_VALID_SHADOW_HISTORY)
        _write_pm(tmp_path, _recent_date_str(2), _PM_VALID_POST_CUTOFF)
        _write_pm(tmp_path, _recent_date_str(3), _PM_VALID_BOT_STATE_FALLBACK)

        naive_saved = _sum_dollars(
            _PM_VALID_SHADOW_HISTORY, _PM_VALID_POST_CUTOFF, _PM_VALID_BOT_STATE_FALLBACK
        )
        naive_count = _count_triggers(
            _PM_VALID_SHADOW_HISTORY, _PM_VALID_POST_CUTOFF, _PM_VALID_BOT_STATE_FALLBACK
        )

        result = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

        assert result["total_saved"] == pytest.approx(naive_saved, abs=1e-9), (
            f"guard altered a valid-only total: {result['total_saved']} != naive {naive_saved}"
        )
        assert result["trigger_count"] == naive_count

    def test_malformed_json_still_skipped_not_double_handled(self, tmp_path):
        _write_pm(tmp_path, _recent_date_str(1), _PM_VALID_SHADOW_HISTORY)
        (tmp_path / f"post_mortem_{_recent_date_str(2)}.json").write_text(
            "not json at all {{{", encoding="utf-8"
        )

        expected_saved = _sum_dollars(_PM_VALID_SHADOW_HISTORY)
        expected_count = _count_triggers(_PM_VALID_SHADOW_HISTORY)

        result = analytics.get_history_summary(days=30, base_dir=str(tmp_path))

        assert result["total_saved"] == pytest.approx(expected_saved, abs=0.01)
        assert result["trigger_count"] == expected_count
