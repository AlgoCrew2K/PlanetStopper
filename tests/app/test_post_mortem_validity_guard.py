"""
RED tests — F-008 Post-Mortem Data Integrity, app.py consumer (AC-2/AC-3/AC-4/AC-6).

THE BUG: two historical post-mortem days (2026-06-22, 2026-07-09-style) carry
wrong/sign-flipped saved_dollars and are served LIVE into the operator's
$-saved headline because app.py's guard_alpha_summary() (app.py:2712-2735)
globs EVERY post_mortem_*.json and sums saved_dollars unconditionally — no
read-time defense against semantically-wrong-but-syntactically-valid JSON.

THE FIX UNDER TEST: a read-time validity guard keyed on the trigger entry's
`if_held_source` provenance field. reporting.py:generate_eod_snapshot stamps
one of 3 RECOGNIZED values on every trigger since DE-GUARD-ALPHA-SAVED-001:
    "shadow_history"            - on-basis shadow_history row (normal case)
    "shadow_history_post_cutoff" - off-schedule run, still real shadow data
    "bot_state_fallback"        - the SANCTIONED row-less degradation (the
                                   only case bot_state.current_return may be
                                   trusted; see test_postmortem_if_held_
                                   shadow_history_source.py)
An entry with a MISSING or unrecognized if_held_source is excluded from the
aggregate. This is intentionally WIDER than "only shadow_history trusted"
(the plan's original wording) — see feature-plans/fix-f008-data-integrity.md
Decisions table for the 2026-07-20 plan-approval ruling and the concrete
regression this avoids (test_flush_resync.py::test_post_mortem_path_round_trip,
which triggers "bot_state_fallback" via the real producer with no shadow rows
seeded).

CONTRACT PINNED (this file):
  1. AC-2: an entry lacking a recognized if_held_source contributes NEITHER
     to cumulative_saved_dollars NOR to guard_event_count.
  2. AC-3: an entry carrying ANY of the 3 recognized if_held_source values
     aggregates exactly as before the guard existed (no regression).
  3. AC-4: syntactically-malformed JSON (existing except-path) and
     semantically-invalid entries (new guard) are handled by two DISTINCT,
     non-interacting mechanisms — neither double-counts nor double-skips.
  4. AC-6: with an all-valid post-mortem set, the guard changes nothing
     (byte-identical totals to the pre-guard "sum everything" result).
  5. Edge case: mixed valid+invalid surfaces an honest excluded count (field
     name `excluded_invalid_count` — chosen here; see .claude/tdd-handoff.md)
     so an all-invalid day reads as "N excluded", never a bare "$0.00" that
     could be misread as "genuinely nothing happened".
  6. Security (plan's Security Considerations: never leak raw file contents /
     exception strings into an operator-facing string): an excluded entry's
     own field values never appear anywhere in the JSON response.

FIXTURE PROVENANCE:
  - tests/fixtures/app/post_mortem_validity_guard/*_valid_*.json — schema-
    derived from reporting.py:generate_eod_snapshot's real trigger schema,
    one file per recognized if_held_source value.
  - tests/fixtures/app/post_mortem_validity_guard/post_mortem_2026-07-09_
    contaminated_missing_stamp.json — SCHEMA-DERIVED SYNTHETIC. No real
    post_mortem_2026-07-09.json exists in this repo (the earliest real
    capture in tests/fixtures/prod_droplet/post_mortems/ is 06-22, captured
    2026-07-09 itself, before that day's own file could be captured).
    Reproduces the F-008-documented signature: if_held_source absent
    entirely (the field did not exist in production before DE-GUARD-ALPHA-
    SAVED-001 / PR #80).
  - tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-06-22.json —
    the REAL captured droplet file (captured 2026-07-09, deployed SHA
    0bcbd1a). Verified by direct inspection: no trigger in this file carries
    an if_held_source key at all — it is genuinely representative of the
    contamination signature, not a fabrication.

All dollar/count assertions are DERIVED from the fixture files loaded in
this module, never hardcoded to producer-computed values
(feedback_no_hardcoded_test_values).
"""

from __future__ import annotations

import json
import pathlib

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_VALID_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "app" / "post_mortem_validity_guard"
)
_REAL_DROPLET_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "prod_droplet" / "post_mortems"
)

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

# Sanity guard on the fixture itself: if a future capture/edit adds an
# if_held_source to this real file, this test file's premise (it is an
# UNSTAMPED real contaminated day) would silently stop being tested. Fail
# loudly instead of silently testing nothing.
assert all("if_held_source" not in t for t in _PM_REAL_CONTAMINATED_06_22["triggers"]), (
    "fixture integrity: post_mortem_2026-06-22.json (real droplet capture) was "
    "expected to have NO if_held_source on any trigger — if this now fails, the "
    "fixture changed and this file's exclusion tests need re-deriving."
)


def _sum_dollars(*pms: dict) -> float:
    return sum(t["saved_dollars"] for pm in pms for t in pm.get("triggers", []))


def _count_triggers(*pms: dict) -> int:
    return sum(len(pm.get("triggers", [])) for pm in pms)


# ---------------------------------------------------------------------------
# Flask client + pm_dir fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _write_pm(pm_dir: pathlib.Path, date_str: str, payload: dict) -> None:
    (pm_dir / f"post_mortem_{date_str}.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def pm_dir(tmp_path, monkeypatch):
    """Empty post_mortems dir, patched onto analytics._POST_MORTEMS_DIR.

    Individual tests populate it via _write_pm before hitting the route.
    """
    import analytics as analytics_module

    d = tmp_path / "post_mortems"
    d.mkdir()
    monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(d))
    return d


# ===========================================================================
# AC-2 / AC-4: missing-provenance entries are EXCLUDED
# ===========================================================================


class TestGuardAlphaSummaryExcludesMissingProvenance:
    def test_only_contaminated_day_present_yields_zero_not_its_dollars(self, client, pm_dir):
        """A dir containing ONLY the missing-stamp contaminated fixture must NOT
        contribute its (wrong) dollars to the sum — the sum must be 0.0, not the
        fixture's saved_dollars total."""
        _write_pm(pm_dir, "2026-07-09", _PM_CONTAMINATED_MISSING_STAMP)

        # Sanity: the fixture itself carries real nonzero saved_dollars — if the
        # route were unguarded it WOULD sum to this contaminated value.
        contaminated_dollars = _sum_dollars(_PM_CONTAMINATED_MISSING_STAMP)
        assert contaminated_dollars != 0.0, "fixture integrity: contaminated day must be nonzero"

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(0.0, abs=1e-9), (
            f"cumulative_saved_dollars={data['cumulative_saved_dollars']} — the missing-stamp "
            f"entry's ${contaminated_dollars:.2f} leaked into the sum despite lacking a "
            "recognized if_held_source."
        )
        assert data["guard_event_count"] == 0, (
            f"guard_event_count={data['guard_event_count']} — the missing-stamp entry must not "
            "be counted as a guard event."
        )

    def test_contaminated_day_excluded_when_mixed_with_valid(self, client, pm_dir):
        """A valid day + the contaminated day together: only the valid day's
        numbers reach the aggregate."""
        _write_pm(pm_dir, "2026-02-02", _PM_VALID_SHADOW_HISTORY)
        _write_pm(pm_dir, "2026-07-09", _PM_CONTAMINATED_MISSING_STAMP)

        expected_dollars = _sum_dollars(_PM_VALID_SHADOW_HISTORY)
        expected_count = _count_triggers(_PM_VALID_SHADOW_HISTORY)

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(expected_dollars, abs=0.01), (
            # abs=0.01: same float-summation tolerance used throughout this route's
            # existing tests (2dp-rounded producer values).
            f"cumulative_saved_dollars={data['cumulative_saved_dollars']} != valid-only "
            f"{expected_dollars} — the contaminated day's dollars leaked in."
        )
        assert data["guard_event_count"] == expected_count, (
            f"guard_event_count={data['guard_event_count']} != valid-only {expected_count}"
        )

    def test_real_captured_06_22_contamination_excluded(self, client, pm_dir):
        """The EXACT real production file that caused F-008 (post_mortem_2026-06-22.json,
        11 triggers, no if_held_source anywhere) must not contribute when mixed with a
        valid day — proves the guard against real, not just synthetic, contamination."""
        _write_pm(pm_dir, "2026-02-02", _PM_VALID_SHADOW_HISTORY)
        _write_pm(pm_dir, "2026-06-22", _PM_REAL_CONTAMINATED_06_22)

        expected_dollars = _sum_dollars(_PM_VALID_SHADOW_HISTORY)
        expected_count = _count_triggers(_PM_VALID_SHADOW_HISTORY)
        real_contaminated_dollars = _sum_dollars(_PM_REAL_CONTAMINATED_06_22)
        assert real_contaminated_dollars != 0.0, (
            "fixture integrity: real 06-22 file must be nonzero"
        )

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(expected_dollars, abs=0.01), (
            f"cumulative_saved_dollars={data['cumulative_saved_dollars']} != valid-only "
            f"{expected_dollars} — the real 06-22 capture's ${real_contaminated_dollars:.2f} "
            "(11 triggers) leaked into the live headline."
        )
        assert data["guard_event_count"] == expected_count, (
            f"guard_event_count={data['guard_event_count']} != valid-only {expected_count} — "
            f"the real 06-22 capture's 11 triggers leaked in."
        )


# ===========================================================================
# AC-2 (wider trusted set) / AC-3: all 3 recognized values are INCLUDED
# ===========================================================================


class TestGuardAlphaSummaryIncludesAllRecognizedProvenance:
    @pytest.mark.parametrize(
        "fixture,date_str",
        [
            (_PM_VALID_SHADOW_HISTORY, "2026-02-02"),
            (_PM_VALID_POST_CUTOFF, "2026-02-03"),
            (_PM_VALID_BOT_STATE_FALLBACK, "2026-02-04"),
        ],
        ids=["shadow_history", "shadow_history_post_cutoff", "bot_state_fallback"],
    )
    def test_each_recognized_value_included_alone(self, client, pm_dir, fixture, date_str):
        """Each of the 3 recognized if_held_source values, alone, must fully
        contribute to the aggregate — none is treated as invalid."""
        _write_pm(pm_dir, date_str, fixture)
        expected_dollars = _sum_dollars(fixture)
        expected_count = _count_triggers(fixture)

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(expected_dollars, abs=0.01), (
            f"if_held_source={fixture['triggers'][0]['if_held_source']!r} must be trusted; "
            f"got {data['cumulative_saved_dollars']}, expected {expected_dollars}"
        )
        assert data["guard_event_count"] == expected_count

    def test_all_three_recognized_values_together(self, client, pm_dir):
        """All 3 recognized values across 3 files sum together with nothing excluded."""
        _write_pm(pm_dir, "2026-02-02", _PM_VALID_SHADOW_HISTORY)
        _write_pm(pm_dir, "2026-02-03", _PM_VALID_POST_CUTOFF)
        _write_pm(pm_dir, "2026-02-04", _PM_VALID_BOT_STATE_FALLBACK)

        expected_dollars = _sum_dollars(
            _PM_VALID_SHADOW_HISTORY, _PM_VALID_POST_CUTOFF, _PM_VALID_BOT_STATE_FALLBACK
        )
        expected_count = _count_triggers(
            _PM_VALID_SHADOW_HISTORY, _PM_VALID_POST_CUTOFF, _PM_VALID_BOT_STATE_FALLBACK
        )

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(expected_dollars, abs=0.01)
        assert data["guard_event_count"] == expected_count


# ===========================================================================
# AC-4: malformed JSON and semantic-invalidity are distinct, non-interacting
# ===========================================================================


class TestGuardAlphaSummaryMalformedNotDoubleHandled:
    def test_malformed_json_skipped_route_still_200_valid_only_counted(self, client, pm_dir):
        """A syntactically-malformed file alongside a valid one: still 200, and
        the aggregate reflects ONLY the valid file (the malformed file contributes
        neither dollars, nor a guard event, nor an excluded-count increment —
        it never reaches entry-level parsing at all)."""
        _write_pm(pm_dir, "2026-02-02", _PM_VALID_SHADOW_HISTORY)
        (pm_dir / "post_mortem_2026-02-05.json").write_text(
            "{not valid json at all", encoding="utf-8"
        )

        expected_dollars = _sum_dollars(_PM_VALID_SHADOW_HISTORY)
        expected_count = _count_triggers(_PM_VALID_SHADOW_HISTORY)

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200, "malformed JSON must not crash the route"
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(expected_dollars, abs=0.01)
        assert data["guard_event_count"] == expected_count
        # The malformed file must not be miscounted as a semantically-excluded
        # entry — it never got far enough to be evaluated for provenance.
        if "excluded_invalid_count" in data:
            assert data["excluded_invalid_count"] == 0, (
                f"malformed-file-skip and semantic-invalidity-exclusion must not overlap; "
                f"got excluded_invalid_count={data['excluded_invalid_count']} with no "
                "semantically-invalid entries present"
            )

    def test_missing_stamp_entry_excluded_alongside_malformed_file(self, client, pm_dir):
        """Three files at once: valid, malformed, and missing-stamp — each handled
        by its own mechanism, aggregate reflects only the valid one."""
        _write_pm(pm_dir, "2026-02-02", _PM_VALID_SHADOW_HISTORY)
        _write_pm(pm_dir, "2026-07-09", _PM_CONTAMINATED_MISSING_STAMP)
        (pm_dir / "post_mortem_2026-02-05.json").write_text("{{{not json", encoding="utf-8")

        expected_dollars = _sum_dollars(_PM_VALID_SHADOW_HISTORY)
        expected_count = _count_triggers(_PM_VALID_SHADOW_HISTORY)

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(expected_dollars, abs=0.01)
        assert data["guard_event_count"] == expected_count


# ===========================================================================
# AC-6: golden regression — all-valid set is byte-identical to the naive sum
# ===========================================================================


class TestGuardAlphaSummaryAllValidGoldenRegression:
    def test_all_valid_headline_equals_naive_unguarded_sum(self, client, pm_dir):
        """With every file valid (any recognized if_held_source), the guarded
        aggregate must exactly equal what a naive "sum every trigger" would have
        produced — proving the guard is purely a filter, never an alteration."""
        _write_pm(pm_dir, "2026-02-02", _PM_VALID_SHADOW_HISTORY)
        _write_pm(pm_dir, "2026-02-03", _PM_VALID_POST_CUTOFF)
        _write_pm(pm_dir, "2026-02-04", _PM_VALID_BOT_STATE_FALLBACK)

        naive_dollars = _sum_dollars(
            _PM_VALID_SHADOW_HISTORY, _PM_VALID_POST_CUTOFF, _PM_VALID_BOT_STATE_FALLBACK
        )
        naive_count = _count_triggers(
            _PM_VALID_SHADOW_HISTORY, _PM_VALID_POST_CUTOFF, _PM_VALID_BOT_STATE_FALLBACK
        )

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(naive_dollars, abs=1e-9), (
            # abs=1e-9: this is a regression-identity check (guard vs naive sum on
            # the SAME in-memory fixture data), not a cross-formula comparison —
            # any drift beyond float noise means the guard altered a valid value.
            f"guard altered a valid-only aggregate: {data['cumulative_saved_dollars']} != "
            f"naive sum {naive_dollars}"
        )
        assert data["guard_event_count"] == naive_count


# ===========================================================================
# Edge case: excluded count is surfaced honestly (never a bare misleading $0)
# ===========================================================================


class TestGuardAlphaSummaryExcludedCountSurfaced:
    def test_all_invalid_day_surfaces_nonzero_excluded_count(self, client, pm_dir):
        """A dir with ONLY the contaminated day must be distinguishable, via
        excluded_invalid_count, from a genuinely-empty dir — both show $0.00 but
        only one has something to report as excluded."""
        _write_pm(pm_dir, "2026-07-09", _PM_CONTAMINATED_MISSING_STAMP)

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "excluded_invalid_count" in data, (
            "response must surface an excluded-entry count so an all-invalid day "
            "reads as 'N excluded', not an unqualified $0.00 that could be misread "
            "as 'genuinely nothing happened' (plan Edge Cases)"
        )
        expected_excluded = _count_triggers(_PM_CONTAMINATED_MISSING_STAMP)
        assert data["excluded_invalid_count"] == expected_excluded, (
            f"excluded_invalid_count={data['excluded_invalid_count']} != "
            f"{expected_excluded} (the contaminated fixture's trigger count)"
        )

    def test_all_valid_day_has_zero_excluded_count(self, client, pm_dir):
        _write_pm(pm_dir, "2026-02-02", _PM_VALID_SHADOW_HISTORY)

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("excluded_invalid_count", 0) == 0, (
            f"an all-valid day must report zero excluded entries; "
            f"got {data.get('excluded_invalid_count')}"
        )

    def test_mixed_valid_invalid_excluded_count_matches_invalid_subset(self, client, pm_dir):
        _write_pm(pm_dir, "2026-02-02", _PM_VALID_SHADOW_HISTORY)
        _write_pm(pm_dir, "2026-07-09", _PM_CONTAMINATED_MISSING_STAMP)

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        expected_excluded = _count_triggers(_PM_CONTAMINATED_MISSING_STAMP)
        assert data.get("excluded_invalid_count") == expected_excluded, (
            f"excluded_invalid_count={data.get('excluded_invalid_count')!r} != "
            f"{expected_excluded} (the contaminated fixture's trigger count)"
        )


# ===========================================================================
# Security (plan's Security Considerations): no leak of excluded raw data
# ===========================================================================


class TestGuardAlphaSummaryNoDataLeakOnExclusion:
    def test_excluded_entry_symphony_name_not_present_in_response(self, client, pm_dir):
        """The excluded entry's own identifying data must never surface anywhere
        in the JSON response — the guard filters silently, it doesn't echo back
        what it dropped (that would itself be an operator-facing leak of a raw
        record from an unverified file)."""
        _write_pm(pm_dir, "2026-02-02", _PM_VALID_SHADOW_HISTORY)
        _write_pm(pm_dir, "2026-07-09", _PM_CONTAMINATED_MISSING_STAMP)

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        raw_body = resp.get_data(as_text=True)
        excluded_name = _PM_CONTAMINATED_MISSING_STAMP["triggers"][0]["symphony_name"]
        assert excluded_name not in raw_body, (
            f"excluded entry's symphony_name {excluded_name!r} leaked into the response body "
            "— the guard must filter silently, not echo dropped-record contents"
        )
