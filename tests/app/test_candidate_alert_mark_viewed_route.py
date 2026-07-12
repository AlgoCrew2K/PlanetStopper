"""
RED tests — POST /api/candidate-alert/mark-viewed (AC-5, feature-plans/candidate-alert.md).

The route does not exist yet; every test in this file is expected to fail RED
until the implementer adds it.

Contract under test:
- CSRF-protected: same enforcement pattern as save_symphony_settings
  (app.py:3545, _validate_csrf()) and the strategy-builder /run route
  (tests/app/test_strategy_builder_route.py Group B) — missing/wrong token ->
  403; valid token -> 200.
- AC-5: advances the viewed-marker so previously-new survivors stop counting;
  idempotent (a second call never raises, never un-clears the badge); marker
  is server-computed only — the client cannot POST an arbitrary marker value
  (mirrors tests/database/test_033_candidate_alert_state.py::MV3).
- Advisory-only: NOT added to _SETTINGS_WRITE_ALLOWLIST (that allowlist gates
  a DIFFERENT route, /api/settings, for env-key writes only); no
  LIVE_EXECUTION interaction.
"""

from __future__ import annotations

import secrets
import time

import pytest

import app as app_module
import database as db_module

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _seed_observation(advisor_role: str, verdict: str | None, symphony_id: str = "sym-a") -> int:
    return db_module.insert_advisor_observation(
        advisor_role=advisor_role,
        subject_type="test_subject",
        subject_id=f"cand-{time.time_ns()}",
        verdict=verdict,
        raw_response={},
        symphony_id=symphony_id,
    )


def _assert_route_exists(resp, label: str):
    """A 404 means the route is not yet implemented — the RED signal for every
    test in this module. Every assertion below should be read as 'once the
    route exists, it must additionally satisfy...'."""
    assert resp.status_code != 404, f"{label} does not exist yet (404) — RED, not implemented."


# ---------------------------------------------------------------------------
# CSRF enforcement — mirrors tests/app/test_strategy_builder_route.py Group B
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def _reenable_csrf(monkeypatch):
    """Root tests/conftest.py disables CSRF globally; these tests need it live."""
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)


class TestMarkViewedCsrf:
    def test_post_without_csrf_token_returns_403(self, client, _reenable_csrf):
        resp = client.post("/api/candidate-alert/mark-viewed")
        _assert_route_exists(resp, "POST /api/candidate-alert/mark-viewed")
        assert resp.status_code == 403, (
            f"POST /api/candidate-alert/mark-viewed without X-CSRF-Token returned "
            f"{resp.status_code}, expected 403."
        )

    def test_post_with_wrong_csrf_token_returns_403(self, client, _reenable_csrf):
        fake_token = secrets.token_hex(32)
        resp = client.post(
            "/api/candidate-alert/mark-viewed",
            headers={"X-CSRF-Token": fake_token},
        )
        _assert_route_exists(resp, "POST /api/candidate-alert/mark-viewed")
        assert resp.status_code == 403, (
            f"POST /api/candidate-alert/mark-viewed with a fabricated X-CSRF-Token "
            f"returned {resp.status_code}, expected 403."
        )

    def test_post_with_valid_csrf_token_returns_200(self, client, _reenable_csrf):
        token_resp = client.get("/api/csrf-token")
        assert token_resp.status_code == 200
        token = token_resp.get_json().get("csrf_token")
        assert token, "GET /api/csrf-token must return a non-empty csrf_token"

        resp = client.post(
            "/api/candidate-alert/mark-viewed",
            headers={"X-CSRF-Token": token},
        )
        _assert_route_exists(resp, "POST /api/candidate-alert/mark-viewed")
        assert resp.status_code == 200, (
            f"POST /api/candidate-alert/mark-viewed with a valid X-CSRF-Token "
            f"returned {resp.status_code}, expected 200."
        )


# ---------------------------------------------------------------------------
# AC-5: marker advances, badge clears, idempotent
# (CSRF disabled by root conftest for the tests below)
# ---------------------------------------------------------------------------


class TestMarkViewedAdvancesMarker:
    def test_mark_viewed_clears_the_badge(self, client):
        """A survivor that counted before mark-viewed must not count after."""
        _seed_observation("ASSET_SWAP", "ADOPT_CANDIDATE")

        before = client.get("/api/candidate-alert").get_json()
        assert before["new_valid_count"] == 1, "Sanity: the seeded survivor must count first."

        post_resp = client.post("/api/candidate-alert/mark-viewed")
        _assert_route_exists(post_resp, "POST /api/candidate-alert/mark-viewed")
        assert post_resp.status_code == 200

        after = client.get("/api/candidate-alert").get_json()
        assert after["new_valid_count"] == 0, (
            "new_valid_count must drop to 0 after mark-viewed acknowledges the "
            "currently-visible survivors."
        )

    def test_mark_viewed_does_not_hide_a_later_survivor(self, client):
        """AC-5: a survivor persisted AFTER mark-viewed must still count (the
        marker advances to 'now', not forever into the future)."""
        _seed_observation("ASSET_SWAP", "ADOPT_CANDIDATE")
        client.post("/api/candidate-alert/mark-viewed")

        _seed_observation("LOGIC_CHANGE", "ADOPT_CANDIDATE")
        after = client.get("/api/candidate-alert").get_json()
        assert after["new_valid_count"] == 1, (
            "A new survivor persisted after mark-viewed must still increment the "
            "badge — the marker must not suppress future candidates."
        )

    def test_mark_viewed_is_idempotent(self, client):
        """Calling mark-viewed twice in a row must never raise and must not
        change the outcome the second time."""
        _seed_observation("ASSET_SWAP", "ADOPT_CANDIDATE")

        first = client.post("/api/candidate-alert/mark-viewed")
        _assert_route_exists(first, "POST /api/candidate-alert/mark-viewed")
        assert first.status_code == 200

        second = client.post("/api/candidate-alert/mark-viewed")
        assert second.status_code == 200, (
            f"A second mark-viewed call must not raise/error, got {second.status_code}."
        )

        after = client.get("/api/candidate-alert").get_json()
        assert after["new_valid_count"] == 0

    def test_mark_viewed_response_has_marker_value(self, client):
        _seed_observation("ASSET_SWAP", "ADOPT_CANDIDATE")
        resp = client.post("/api/candidate-alert/mark-viewed")
        _assert_route_exists(resp, "POST /api/candidate-alert/mark-viewed")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "last_viewed_observation_id" in data, (
            f"Response must report the resulting marker value, got keys: {list(data.keys())}"
        )
        assert isinstance(data["last_viewed_observation_id"], int)

    def test_mark_viewed_on_empty_table_does_not_raise(self, client):
        """No survivors ever -> mark-viewed must still succeed (0-row no-op)."""
        resp = client.post("/api/candidate-alert/mark-viewed")
        _assert_route_exists(resp, "POST /api/candidate-alert/mark-viewed")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Advisory-only guardrails
# ---------------------------------------------------------------------------


class TestMarkViewedAdvisoryOnly:
    def test_route_not_gated_by_settings_allowlist(self, client):
        """The mark-viewed write path is a dedicated route/accessor, not a channel
        into _SETTINGS_WRITE_ALLOWLIST — no allowlist key should reference the
        candidate-alert marker (that allowlist is exclusively for /api/settings
        env-key writes)."""
        allowlist = app_module._SETTINGS_WRITE_ALLOWLIST
        for key in allowlist:
            assert "candidate" not in key.lower() and "alert" not in key.lower(), (
                f"_SETTINGS_WRITE_ALLOWLIST must not gain a candidate-alert-related "
                f"key ({key!r}) — mark-viewed is a separate, dedicated write path."
            )

    def test_route_body_cannot_set_arbitrary_marker(self, client):
        """A malicious/buggy client POSTing an explicit observation id must not be
        able to set the marker to an arbitrary (e.g. far-future) value — the
        server computes the marker itself (mirrors
        tests/database/test_033_candidate_alert_state.py::MV3)."""
        _seed_observation("ASSET_SWAP", "ADOPT_CANDIDATE")

        resp = client.post(
            "/api/candidate-alert/mark-viewed",
            json={"last_viewed_observation_id": 999999},
        )
        _assert_route_exists(resp, "POST /api/candidate-alert/mark-viewed")
        assert resp.status_code == 200

        data = resp.get_json()
        assert data["last_viewed_observation_id"] != 999999, (
            "The route must ignore a client-supplied marker value and compute it "
            "server-side from the real observation rows."
        )
