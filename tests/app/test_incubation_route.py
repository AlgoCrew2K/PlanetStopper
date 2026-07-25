"""
RED tests -- GET /api/incubation (AC-7).

The route does not exist yet; every test in this file is expected to fail RED
(404) until the implementer adds it.

Contract under test (per .claude/tdd-handoff.md "app.py -- scheduling + route"):
- Read-only; global auth hook applies; NOT in _SETTINGS_WRITE_ALLOWLIST; no
  LIVE_EXECUTION interaction.
- Calls database.get_incubation_overview(); each row gets a status-derived
  badge label ("Incubating — day N of 63" / "Promoted — recommended" /
  "Failed incubation (...)" / "Expired (...)").
- Strict-JSON safe: NaN/Infinity sanitized at the boundary.
- Empty ledger -> {"incubating": []}, never a 500.
- Ledger read failure -> per-section degrade, never a 500.
- tree_json is NEVER returned by the route (no tree exfil).

All numeric/label assertions are derived from what each test itself seeds via
the real database accessors, never a hardcoded producer literal
(feedback_no_hardcoded_test_values).
"""

from __future__ import annotations

import math

import pytest

import app as app_module
import database as db_module
from database import init_db, run_migrations


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_incubation_route.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


def _admit(candidate_hash: str, backtest_mdd_pct: float = 8.0):
    db_module.register_incubation_candidate(
        candidate_hash=candidate_hash,
        tree_json="{}",
        objective="cut_drawdown",
        provenance="built-new",
        backtest_mdd_pct=backtest_mdd_pct,
    )


# ---------------------------------------------------------------------------
# Route shape
# ---------------------------------------------------------------------------


class TestIncubationRouteShape:
    def test_returns_200(self, client, isolated_db):
        resp = client.get("/api/incubation")
        assert resp.status_code == 200, (
            f"Expected 200 from GET /api/incubation, got {resp.status_code}. "
            "Route is not yet implemented -- RED."
        )

    def test_content_type_is_json(self, client, isolated_db):
        resp = client.get("/api/incubation")
        assert resp.status_code == 200
        assert "application/json" in resp.content_type

    def test_response_has_incubating_key(self, client, isolated_db):
        resp = client.get("/api/incubation")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert "incubating" in data, f"Missing 'incubating' key. Got keys: {list(data.keys())}"
        assert isinstance(data["incubating"], list)


# ---------------------------------------------------------------------------
# Empty / degraded states
# ---------------------------------------------------------------------------


class TestIncubationRouteEmptyState:
    def test_empty_ledger_returns_empty_list_not_error(self, client, isolated_db):
        resp = client.get("/api/incubation")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["incubating"] == []

    def test_ledger_read_failure_degrades_never_500(self, client, isolated_db, monkeypatch):
        monkeypatch.setattr(
            db_module,
            "get_incubation_overview",
            lambda: (_ for _ in ()).throw(RuntimeError("simulated DB failure")),
        )
        resp = client.get("/api/incubation")
        assert resp.status_code != 500, (
            "A ledger read failure must degrade honestly, never 500 the whole route."
        )


# ---------------------------------------------------------------------------
# Per-row content: badges, day counts, no tree exfil
# ---------------------------------------------------------------------------


class TestIncubationRouteNoStaleCaching:
    def test_status_change_between_two_requests_is_reflected_on_the_second(
        self, client, isolated_db
    ):
        """AC-5 (ga3-rev caching upgrade, promoted by the PM 2026-07-25): no path
        may cache/serve status from a frozen snapshot. Two GET requests, with a
        real status transition in between (via the same production accessor the
        tick uses), must return DIFFERENT status values -- proving the route has
        no response cache, no memoized ledger read, and no reliance on a frozen
        field anywhere in its path. A route that cached the first response (or
        read a frozen raw_response field instead of the live ledger) would return
        the SAME stale status on both requests."""
        _admit("hash-nocache-1")

        first = client.get("/api/incubation")
        first_row = next(
            r for r in first.get_json()["incubating"] if r.get("candidate_hash") == "hash-nocache-1"
        )
        assert first_row.get("status") == "INCUBATING"

        db_module.set_incubation_status("hash-nocache-1", "PROMOTED")

        second = client.get("/api/incubation")
        second_row = next(
            r
            for r in second.get_json()["incubating"]
            if r.get("candidate_hash") == "hash-nocache-1"
        )
        assert second_row.get("status") == "PROMOTED", (
            f"Expected the second request to reflect the real status transition to "
            f"PROMOTED, got {second_row.get('status')!r}. The route must read the "
            "live ledger on every request -- no caching, no frozen snapshot."
        )


class TestIncubationRowContent:
    def test_incubating_row_has_day_count_badge(self, client, isolated_db):
        _admit("hash-route-1")
        db_module.append_incubation_day("hash-route-1", "2026-01-05", 0.1, 0.05)

        resp = client.get("/api/incubation")
        data = resp.get_json()
        row = next(r for r in data["incubating"] if r.get("candidate_hash") == "hash-route-1")
        assert row.get("status") == "INCUBATING"
        assert row.get("days_observed") == 1

    def test_promoted_row_status_reflects_live_ledger_state(self, client, isolated_db):
        _admit("hash-route-2")
        db_module.set_incubation_status("hash-route-2", "PROMOTED")

        resp = client.get("/api/incubation")
        data = resp.get_json()
        row = next(r for r in data["incubating"] if r.get("candidate_hash") == "hash-route-2")
        assert row.get("status") == "PROMOTED"

    def test_failed_row_carries_status_reason(self, client, isolated_db):
        _admit("hash-route-3")
        db_module.set_incubation_status("hash-route-3", "FAILED", "mdd_breach")

        resp = client.get("/api/incubation")
        data = resp.get_json()
        row = next(r for r in data["incubating"] if r.get("candidate_hash") == "hash-route-3")
        assert row.get("status") == "FAILED"
        assert row.get("status_reason") == "mdd_breach"

    def test_response_never_includes_raw_tree_json(self, client, isolated_db):
        """Security: tree_json is stored server-side only -- the route returns
        derived stats/status, never the raw tree (no tree exfil via the API)."""
        _admit("hash-route-4")

        resp = client.get("/api/incubation")
        data = resp.get_json()
        row = next(r for r in data["incubating"] if r.get("candidate_hash") == "hash-route-4")
        assert "tree_json" not in row, (
            "GET /api/incubation must never return the raw tree_json field "
            "(security: no tree exfil via the API)."
        )


# ---------------------------------------------------------------------------
# Strict-JSON safety (NaN/Infinity)
# ---------------------------------------------------------------------------


class TestIncubationRouteStrictJson:
    def test_no_nan_or_infinity_in_numeric_fields(self, client, isolated_db, monkeypatch):
        """A row with a NaN/Inf-producing overview entry must be sanitized at the
        JSON boundary -- json.dumps would otherwise emit the bare NaN/Infinity
        tokens, which a real browser's response.json() rejects for the WHOLE
        response (the shipped _json_safe_float pattern)."""
        _admit("hash-route-nan")

        real_overview = db_module.get_incubation_overview

        def _poisoned_overview():
            rows = real_overview()
            for row in rows:
                if row.get("candidate_hash") == "hash-route-nan":
                    row["days_observed"] = float("nan")
            return rows

        monkeypatch.setattr(db_module, "get_incubation_overview", _poisoned_overview)

        resp = client.get("/api/incubation")
        assert resp.status_code == 200
        raw_body = resp.get_data(as_text=True)
        assert "NaN" not in raw_body and "Infinity" not in raw_body, (
            f"Response body contains a bare NaN/Infinity token (invalid JSON, RFC "
            f"8259) -- must be sanitized to null at the boundary. Body: {raw_body[:300]}"
        )
        data = resp.get_json()
        row = next(r for r in data["incubating"] if r.get("candidate_hash") == "hash-route-nan")
        val = row.get("days_observed")
        if isinstance(val, float):
            assert not math.isnan(val)


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

_TEST_PASSWORD = "test-pw-incubation"
_TEST_SECRET_KEY = "test-secret-incubation"


@pytest.fixture()
def auth_client_no_session(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestIncubationRouteAuthGate:
    def test_unauthenticated_xhr_returns_401(self, auth_client_no_session, isolated_db):
        resp = auth_client_no_session.get(
            "/api/incubation", headers={"X-Requested-With": "XMLHttpRequest"}
        )
        assert resp.status_code == 401, (
            f"Unauthenticated XHR to /api/incubation must return 401, got "
            f"{resp.status_code}. If 404: route not yet implemented (RED). If "
            "200/302: route is missing the auth gate."
        )


# ---------------------------------------------------------------------------
# Not a settings-write surface
# ---------------------------------------------------------------------------


class TestIncubationRouteNotWriteSurface:
    def test_route_path_not_in_settings_write_allowlist(self):
        allowlist = getattr(app_module, "_SETTINGS_WRITE_ALLOWLIST", None)
        if allowlist is None:
            pytest.skip("_SETTINGS_WRITE_ALLOWLIST not found on app module")
        assert "/api/incubation" not in allowlist, (
            "/api/incubation is a read-only advisory surface -- it must never appear "
            "in _SETTINGS_WRITE_ALLOWLIST."
        )
