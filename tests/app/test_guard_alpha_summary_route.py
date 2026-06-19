"""
RED tests — GET /api/guard-alpha-summary (AC-1/AC-4/AC-5/AC-6/AC-8).

The route does not exist yet; every test in this file is expected to fail RED
until the implementer adds the route.

Contract under test:
- AC-1: route aggregates cumulative dollar-saved (Σ saved_dollars across
  post_mortems/*.json) + guard-event count + date range, with a basis_label.
- AC-4: read-only (SQLite mode=ro pattern); no DB writes; not in the settings
  allowlist; no LIVE_EXECUTION interaction.
- AC-5: empty-state (no post_mortem files) → 200, $0 / 0 events, no NaN/None.
- AC-6: malformed/corrupt post_mortem_*.json → skipped + logged, route still 200.
- AC-8: route sits behind the existing auth gate (DE-AUTH-001).

All dollar/count assertions are DERIVED from the fixture files loaded in-test,
never hardcoded to producer-computed values (feedback_no_hardcoded_test_values).
"""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Shared fixture data — loaded once so tests derive assertions from fixture
# ---------------------------------------------------------------------------

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "app" / "guard_alpha_summary"

_PM_2026_06_10 = json.loads((_FIXTURE_DIR / "post_mortem_2026-06-10.json").read_text())
_PM_2026_06_11 = json.loads((_FIXTURE_DIR / "post_mortem_2026-06-11.json").read_text())

# Expected aggregate derived purely from the two valid fixture files
_EXPECTED_SAVED_DOLLARS = sum(
    t["saved_dollars"] for pm in [_PM_2026_06_10, _PM_2026_06_11] for t in pm.get("triggers", [])
)
_EXPECTED_EVENT_COUNT = sum(len(pm.get("triggers", [])) for pm in [_PM_2026_06_10, _PM_2026_06_11])
_EXPECTED_EARLIEST_DATE = "2026-06-10"
_EXPECTED_LATEST_DATE = "2026-06-11"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Flask test client — no port bound."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def pm_dir_with_two_valid_files(tmp_path, monkeypatch):
    """Copy both valid post_mortem fixtures into a temp dir and patch analytics."""
    import analytics as analytics_module

    pm_dir = tmp_path / "post_mortems"
    pm_dir.mkdir()
    shutil.copy(_FIXTURE_DIR / "post_mortem_2026-06-10.json", pm_dir)
    shutil.copy(_FIXTURE_DIR / "post_mortem_2026-06-11.json", pm_dir)
    monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(pm_dir))
    return pm_dir


@pytest.fixture
def pm_dir_empty(tmp_path, monkeypatch):
    """Empty post_mortems dir — exercises empty-state branch."""
    import analytics as analytics_module

    pm_dir = tmp_path / "post_mortems"
    pm_dir.mkdir()
    monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(pm_dir))
    return pm_dir


@pytest.fixture
def pm_dir_with_one_valid_one_corrupt(tmp_path, monkeypatch):
    """One valid + one corrupt post_mortem — exercises resilience branch."""
    import analytics as analytics_module

    pm_dir = tmp_path / "post_mortems"
    pm_dir.mkdir()
    shutil.copy(_FIXTURE_DIR / "post_mortem_2026-06-11.json", pm_dir)
    shutil.copy(_FIXTURE_DIR / "post_mortem_corrupt.json", pm_dir / "post_mortem_2026-06-09.json")
    monkeypatch.setattr(analytics_module, "_POST_MORTEMS_DIR", str(pm_dir))
    return pm_dir


# ---------------------------------------------------------------------------
# AC-1/AC-4: Route exists, returns 200, correct JSON shape
# ---------------------------------------------------------------------------


class TestGuardAlphaSummaryRouteShape:
    def test_guard_alpha_summary_returns_200(self, client, pm_dir_with_two_valid_files):
        """AC-1/AC-4: GET /api/guard-alpha-summary → 200 with expected JSON keys.

        Fails RED because the route does not exist (404).
        """
        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200, (
            f"Expected 200 from /api/guard-alpha-summary, got {resp.status_code}. "
            "Route is not yet implemented — RED."
        )

    def test_guard_alpha_summary_response_has_required_keys(
        self, client, pm_dir_with_two_valid_files
    ):
        """AC-1: JSON response contains the four required top-level keys."""
        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None, "Response must be valid JSON"
        for key in ("cumulative_saved_dollars", "guard_event_count", "date_range", "basis_label"):
            assert key in data, (
                f"Missing required key '{key}' in /api/guard-alpha-summary response. "
                f"Got keys: {list(data.keys())}"
            )

    def test_guard_alpha_summary_content_type_is_json(self, client, pm_dir_with_two_valid_files):
        """AC-4: Route returns JSON content-type (GET, advisory, no HTML)."""
        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        assert "application/json" in resp.content_type, (
            f"Expected JSON content-type, got '{resp.content_type}'"
        )


# ---------------------------------------------------------------------------
# AC-1: Aggregate values derived from fixture data (never hardcoded)
# ---------------------------------------------------------------------------


class TestGuardAlphaSummaryAggregation:
    def test_cumulative_saved_dollars_matches_fixture_sum(
        self, client, pm_dir_with_two_valid_files
    ):
        """AC-1: cumulative_saved_dollars == Σ saved_dollars across all fixture triggers.

        The expected value is computed by summing the fixture files in this test,
        not by hardcoding a dollar amount (feedback_no_hardcoded_test_values).
        """
        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(
            _EXPECTED_SAVED_DOLLARS, abs=0.01
        ), (
            # abs=0.01 tolerance: dollar amounts use round(x, 2) in reporting.py:100;
            # floating-point summation of two-decimal values can drift < 1 cent.
            f"cumulative_saved_dollars {data['cumulative_saved_dollars']} != "
            f"fixture-derived sum {_EXPECTED_SAVED_DOLLARS}"
        )

    def test_guard_event_count_matches_fixture_trigger_count(
        self, client, pm_dir_with_two_valid_files
    ):
        """AC-1: guard_event_count == total number of triggers across all fixture files."""
        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["guard_event_count"] == _EXPECTED_EVENT_COUNT, (
            f"guard_event_count {data['guard_event_count']} != "
            f"fixture-derived count {_EXPECTED_EVENT_COUNT}"
        )

    def test_date_range_covers_fixture_dates(self, client, pm_dir_with_two_valid_files):
        """AC-1: date_range.earliest and date_range.latest bracket the fixture file dates."""
        from datetime import date

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        dr = data.get("date_range", {})
        assert "earliest" in dr and "latest" in dr, (
            f"date_range must have 'earliest' and 'latest' keys, got: {dr}"
        )
        # Both must be parseable ISO date strings
        earliest = date.fromisoformat(dr["earliest"])
        latest = date.fromisoformat(dr["latest"])
        assert earliest <= latest, (
            f"date_range.earliest {dr['earliest']} must be <= latest {dr['latest']}"
        )
        assert dr["earliest"] == _EXPECTED_EARLIEST_DATE, (
            f"Expected earliest date {_EXPECTED_EARLIEST_DATE}, got {dr['earliest']}"
        )
        assert dr["latest"] == _EXPECTED_LATEST_DATE, (
            f"Expected latest date {_EXPECTED_LATEST_DATE}, got {dr['latest']}"
        )

    def test_basis_label_is_nonempty_string(self, client, pm_dir_with_two_valid_files):
        """AC-1: basis_label is present and non-empty (snapshot-time labeling contract)."""
        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        label = data.get("basis_label", "")
        assert isinstance(label, str) and label.strip(), (
            f"basis_label must be a non-empty string, got: {label!r}"
        )


# ---------------------------------------------------------------------------
# AC-5: Empty-state (no post_mortem files)
# ---------------------------------------------------------------------------


class TestGuardAlphaSummaryEmptyState:
    def test_empty_state_returns_200(self, client, pm_dir_empty):
        """AC-5: no post_mortem files → route still returns 200."""
        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200, f"Empty-state must return 200, got {resp.status_code}"

    def test_empty_state_zero_dollars_zero_events(self, client, pm_dir_empty):
        """AC-5: no files → cumulative_saved_dollars==0.0, guard_event_count==0."""
        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(0.0, abs=1e-9), (
            f"Empty state: cumulative_saved_dollars must be 0.0, got {data['cumulative_saved_dollars']}"
        )
        assert data["guard_event_count"] == 0, (
            f"Empty state: guard_event_count must be 0, got {data['guard_event_count']}"
        )

    def test_empty_state_no_nan_or_none_in_numeric_fields(self, client, pm_dir_empty):
        """AC-5: numeric response fields must never be NaN or None (no crash, no leak)."""
        import math

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        for field in ("cumulative_saved_dollars", "guard_event_count"):
            val = data.get(field)
            assert val is not None, f"Field '{field}' must not be None in empty state"
            if isinstance(val, float):
                assert not math.isnan(val), f"Field '{field}' must not be NaN in empty state"


# ---------------------------------------------------------------------------
# AC-6: Malformed/corrupt post_mortem file resilience
# ---------------------------------------------------------------------------


class TestGuardAlphaSummaryCorruptFileResilience:
    def test_corrupt_file_is_skipped_route_still_200(
        self, client, pm_dir_with_one_valid_one_corrupt
    ):
        """AC-6: one corrupt file + one valid file → route 200, no crash."""
        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200, (
            f"Corrupt file must be skipped — route must return 200, got {resp.status_code}"
        )

    def test_corrupt_file_does_not_contribute_to_aggregate(
        self, client, pm_dir_with_one_valid_one_corrupt
    ):
        """AC-6: only the valid fixture file contributes to the aggregate.

        Expected values are derived from the single valid fixture (2026-06-11 only).
        """
        valid_only_dollars = sum(t["saved_dollars"] for t in _PM_2026_06_11.get("triggers", []))
        valid_only_count = len(_PM_2026_06_11.get("triggers", []))

        resp = client.get("/api/guard-alpha-summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cumulative_saved_dollars"] == pytest.approx(valid_only_dollars, abs=0.01), (
            # abs=0.01: same float-summation tolerance as the full-aggregate test.
            f"With one corrupt file, cumulative_saved_dollars {data['cumulative_saved_dollars']} "
            f"should equal the valid-only sum {valid_only_dollars}"
        )
        assert data["guard_event_count"] == valid_only_count, (
            f"With one corrupt file, guard_event_count {data['guard_event_count']} "
            f"should equal the valid-only count {valid_only_count}"
        )


# ---------------------------------------------------------------------------
# AC-4: No DB writes on the read path
# ---------------------------------------------------------------------------


class TestGuardAlphaSummaryReadOnly:
    def test_route_makes_no_database_writes(self, client, pm_dir_with_two_valid_files, monkeypatch):
        """AC-4: route is read-only — no INSERT/UPDATE/DELETE calls to the database.

        Patches the database module on app_module to intercept any write call.
        The route should only read post_mortem JSON files (and optionally read
        exit_triggers count); it must never write.
        """
        from unittest.mock import MagicMock, patch

        write_calls: list[str] = []

        def _record_write(method_name):
            def _fn(*args, **kwargs):
                write_calls.append(method_name)
                return MagicMock()

            return _fn

        write_methods = [
            "record_cvar_diagnostic",
            "insert_advisor_observation",
            "set_symphony_live_mode",
            "save_regime_label",
            "insert_prism_audit_entry",
        ]

        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = {}
            db_mock.get_triggers.return_value = []
            db_mock.get_recent_exit_triggers.return_value = []
            for method in write_methods:
                setattr(db_mock, method, _record_write(method))

            resp = client.get("/api/guard-alpha-summary")
            # Route may not exist yet (RED); if it does, assert no writes.
            if resp.status_code == 200:
                assert not write_calls, f"AC-4 FAIL: route made DB write calls: {write_calls}"


# ---------------------------------------------------------------------------
# AC-8: Auth gate (route sits behind DE-AUTH-001)
# ---------------------------------------------------------------------------

_TEST_PASSWORD = "test-pw-guard-alpha"
_TEST_SECRET_KEY = "test-secret-guard-alpha"


@pytest.fixture
def auth_client_no_session(monkeypatch):
    """Flask test client with auth gate ENABLED but no active session."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestGuardAlphaSummaryAuthGate:
    def test_unauthenticated_xhr_returns_401(
        self, auth_client_no_session, pm_dir_empty, monkeypatch
    ):
        """AC-8: unauthenticated XHR GET → 401 (route is behind the auth gate).

        Fails RED because the route does not exist (404 rather than 401).
        Once the route exists, this will pass when the auth gate is correctly
        wired — and fail if the route is accidentally left unprotected.
        """
        resp = auth_client_no_session.get(
            "/api/guard-alpha-summary",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 401, (
            f"AC-8: unauthenticated XHR to /api/guard-alpha-summary must return 401, "
            f"got {resp.status_code}. "
            "If 404: route not yet implemented (RED). "
            "If 200/302: route is missing the auth gate."
        )
