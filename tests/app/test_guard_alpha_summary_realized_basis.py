"""
RED -- AC-6/AC-7/AC-9 (exit-friction-realized-savings): GET /api/guard-alpha-summary
dual-basis response (snapshot + realized).

Contract under test (frozen in .claude/tdd-handoff.md v1):
- AC-6: response gains ``saved_dollars_realized`` (float, sum of valid
  entries' saved_dollars_realized) and ``realized_coverage``
  ({"with_data": int, "total": int}), ADDITIVE to the existing
  cumulative_saved_dollars/guard_event_count/date_range/basis_label keys
  (unchanged math -- DE-GUARD-ALPHA-SAVED-001 semantics preserved).
- AC-7: an entry missing saved_dollars_realized is EXCLUDED from the
  realized sum, COUNTED in realized_coverage.total, NOT counted in
  realized_coverage.with_data -- never substituted with the snapshot value.
- AC-9: old-format files (no realized fields at all) parse cleanly and
  contribute normally to the snapshot-basis aggregate while contributing
  0 to the realized aggregate and to realized_coverage.total (they ARE
  valid triggered entries, just without realized data).

All dollar/count assertions are DERIVED from the fixture files loaded
in-test, never hardcoded to producer-computed values (house test rule).
"""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest

import app as app_module

_REALIZED_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "app" / "guard_alpha_summary_realized"
)
_OLD_FORMAT_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "app" / "guard_alpha_summary"
)

_PM_2026_07_01 = json.loads((_REALIZED_FIXTURE_DIR / "post_mortem_2026-07-01.json").read_text())
_PM_2026_07_02 = json.loads((_REALIZED_FIXTURE_DIR / "post_mortem_2026-07-02.json").read_text())
_PM_OLD_FORMAT = json.loads((_OLD_FORMAT_FIXTURE_DIR / "post_mortem_2026-06-10.json").read_text())


def _valid_triggers(*post_mortems):
    import analytics as analytics_module

    out = []
    for pm in post_mortems:
        out.extend(
            t for t in pm.get("triggers", []) if analytics_module.is_valid_post_mortem_entry(t)
        )
    return out


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def pm_dir_mixed_realized_coverage(tmp_path, monkeypatch):
    """Two new-format files (mixed realized coverage) -- one entry has no
    realized fields at all (Symphony Zeta in 2026-07-02)."""
    import analytics as analytics_module

    pm_dir = tmp_path / "post_mortems"
    pm_dir.mkdir()
    shutil.copy(_REALIZED_FIXTURE_DIR / "post_mortem_2026-07-01.json", pm_dir)
    shutil.copy(_REALIZED_FIXTURE_DIR / "post_mortem_2026-07-02.json", pm_dir)
    monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(pm_dir))
    return pm_dir


@pytest.fixture
def pm_dir_old_and_new_mixed(tmp_path, monkeypatch):
    """AC-9: one OLD-FORMAT file (no realized fields anywhere) alongside a
    NEW-FORMAT file -- both must parse and contribute correctly."""
    import analytics as analytics_module

    pm_dir = tmp_path / "post_mortems"
    pm_dir.mkdir()
    shutil.copy(_OLD_FORMAT_FIXTURE_DIR / "post_mortem_2026-06-10.json", pm_dir)
    shutil.copy(_REALIZED_FIXTURE_DIR / "post_mortem_2026-07-01.json", pm_dir)
    monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(pm_dir))
    return pm_dir


# ---------------------------------------------------------------------------
# AC-6: response schema gains saved_dollars_realized + realized_coverage
# ---------------------------------------------------------------------------


class TestDualBasisResponseSchema:
    def test_response_has_saved_dollars_realized_key(self, client, pm_dir_mixed_realized_coverage):
        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "saved_dollars_realized" in body, (
            "AC-6: /api/guard-alpha-summary must return 'saved_dollars_realized' "
            f"additively. Got keys: {sorted(body.keys())}"
        )

    def test_response_has_realized_coverage_key_with_with_data_and_total(
        self, client, pm_dir_mixed_realized_coverage
    ):
        resp = client.get("/api/guard-alpha-summary")
        body = resp.get_json()
        assert "realized_coverage" in body, "AC-6: response must include 'realized_coverage'."
        coverage = body["realized_coverage"]
        assert isinstance(coverage, dict)
        assert "with_data" in coverage and "total" in coverage, (
            f"realized_coverage must have 'with_data' and 'total' keys, got {coverage!r}"
        )

    def test_existing_snapshot_basis_keys_unchanged(self, client, pm_dir_mixed_realized_coverage):
        """AC-6: existing keys (cumulative_saved_dollars, guard_event_count,
        date_range, basis_label) stay present and mathematically UNCHANGED --
        the realized basis is additive, never a replacement."""
        resp = client.get("/api/guard-alpha-summary")
        body = resp.get_json()
        for key in ("cumulative_saved_dollars", "guard_event_count", "date_range", "basis_label"):
            assert key in body, f"Existing key {key!r} must still be present (AC-6 additive-only)."

        valid = _valid_triggers(_PM_2026_07_01, _PM_2026_07_02)
        expected_snapshot_sum = sum(t["saved_dollars"] for t in valid)
        assert body["cumulative_saved_dollars"] == pytest.approx(expected_snapshot_sum, abs=0.01), (
            "cumulative_saved_dollars (snapshot basis) must be unaffected by the "
            "realized-basis addition -- DE-GUARD-ALPHA-SAVED-001 semantics preserved."
        )
        assert body["guard_event_count"] == len(valid)


class TestSavedDollarsRealizedAggregate:
    def test_saved_dollars_realized_sums_only_entries_with_realized_data(
        self, client, pm_dir_mixed_realized_coverage
    ):
        resp = client.get("/api/guard-alpha-summary")
        body = resp.get_json()

        valid = _valid_triggers(_PM_2026_07_01, _PM_2026_07_02)
        with_realized = [t for t in valid if "saved_dollars_realized" in t]
        # Fixture integrity: at least one valid entry (Symphony Zeta) must be
        # WITHOUT realized data, and at least one WITH, or this test can't
        # distinguish the aggregate from a naive sum-everything implementation.
        assert 0 < len(with_realized) < len(valid), (
            "fixture integrity: need a mix of with/without realized-data entries"
        )
        expected = sum(t["saved_dollars_realized"] for t in with_realized)

        assert body["saved_dollars_realized"] == pytest.approx(expected, abs=0.01), (
            f"saved_dollars_realized must sum ONLY entries carrying "
            f"saved_dollars_realized (AC-7 -- never substitute the snapshot "
            f"value for a missing realized entry). expected={expected}, "
            f"got={body['saved_dollars_realized']}"
        )


class TestRealizedCoverageAccounting:
    def test_with_data_counts_only_entries_carrying_the_field(
        self, client, pm_dir_mixed_realized_coverage
    ):
        resp = client.get("/api/guard-alpha-summary")
        body = resp.get_json()

        valid = _valid_triggers(_PM_2026_07_01, _PM_2026_07_02)
        expected_with_data = sum(1 for t in valid if "saved_dollars_realized" in t)
        expected_total = len(valid)

        coverage = body["realized_coverage"]
        assert coverage["with_data"] == expected_with_data, (
            f"realized_coverage.with_data must count only entries carrying "
            f"saved_dollars_realized. expected={expected_with_data}, "
            f"got={coverage['with_data']}"
        )
        assert coverage["total"] == expected_total, (
            f"realized_coverage.total must count ALL valid triggered entries "
            f"(same denominator as guard_event_count) regardless of realized "
            f"data presence. expected={expected_total}, got={coverage['total']}"
        )
        # AC-7: an entry without realized data must be counted in total but
        # NOT in with_data -- the denominator must exceed the numerator here.
        assert coverage["total"] > coverage["with_data"], (
            "fixture integrity/AC-7 regression: at least one valid entry has "
            "no realized data, so total must strictly exceed with_data."
        )


# ---------------------------------------------------------------------------
# AC-9: old-format files parse cleanly and contribute honestly
# ---------------------------------------------------------------------------


class TestBackwardCompatOldFormatFiles:
    def test_old_format_file_contributes_zero_realized_and_counts_toward_total(
        self, client, pm_dir_old_and_new_mixed
    ):
        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200, (
            "AC-9: an old-format post-mortem (no realized fields) must not "
            "break the route -- 200 expected."
        )
        body = resp.get_json()

        old_valid = _valid_triggers(_PM_OLD_FORMAT)
        new_valid = _valid_triggers(_PM_2026_07_01)
        all_valid = old_valid + new_valid

        expected_total_coverage = len(all_valid)
        expected_with_data = sum(1 for t in new_valid if "saved_dollars_realized" in t)
        expected_realized_sum = sum(
            t["saved_dollars_realized"] for t in new_valid if "saved_dollars_realized" in t
        )
        expected_snapshot_sum = sum(t["saved_dollars"] for t in all_valid)

        assert body["realized_coverage"]["total"] == expected_total_coverage, (
            "Old-format entries must count toward realized_coverage.total (they "
            "ARE valid triggered entries, just without realized data)."
        )
        assert body["realized_coverage"]["with_data"] == expected_with_data, (
            "Old-format entries must contribute 0 to realized_coverage.with_data."
        )
        assert body["saved_dollars_realized"] == pytest.approx(expected_realized_sum, abs=0.01), (
            "Old-format entries must contribute nothing to saved_dollars_realized."
        )
        # And the pre-existing snapshot-basis aggregate is untouched by mixing
        # in an old-format file -- both old and new entries contribute their
        # (unchanged) saved_dollars.
        assert body["cumulative_saved_dollars"] == pytest.approx(expected_snapshot_sum, abs=0.01)
