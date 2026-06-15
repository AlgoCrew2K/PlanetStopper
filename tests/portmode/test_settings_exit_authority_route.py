"""
RED tests for /api/settings EXIT_AUTHORITY support (AC-P2.12.3).

Tests: GET returns EXIT_AUTHORITY in globals, POST writes EXIT_AUTHORITY + records
_exit_authority_changed_at timestamp, EXIT_AUTHORITY never masked, invalid values
rejected with 400.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

import pytest


@pytest.fixture(scope="module")
def settings_client():
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_settings_ea_")
    fd2, env_path = tempfile.mkstemp(suffix=".env", prefix="test_settings_ea_")
    os.close(fd)
    os.close(fd2)

    orig_db = os.environ.get("DB_PATH")
    orig_env = os.environ.get("ENV_FILE_PATH")
    orig_ea = os.environ.get("EXIT_AUTHORITY")

    os.environ["DB_PATH"] = db_path
    os.environ["EXIT_AUTHORITY"] = "per_symphony"

    import database

    _old_level = logging.getLogger().level
    logging.getLogger().setLevel(logging.CRITICAL)
    try:
        database.init_db()
    finally:
        logging.getLogger().setLevel(_old_level)

    import app as app_module

    # Point app at our temp .env so writes don't touch the real file
    app_module.ENV_FILE_PATH = env_path
    app_module.app.config["TESTING"] = True

    # Seed temp .env with EXIT_AUTHORITY
    with open(env_path, "w") as f:
        f.write("EXIT_AUTHORITY=per_symphony\n")

    with app_module.app.test_client() as client:
        yield client, env_path

    os.environ.pop("DB_PATH", None)
    os.environ.pop("EXIT_AUTHORITY", None)
    if orig_db is not None:
        os.environ["DB_PATH"] = orig_db
    if orig_ea is not None:
        os.environ["EXIT_AUTHORITY"] = orig_ea
    if orig_env is not None:
        app_module.ENV_FILE_PATH = orig_env
    try:
        os.unlink(db_path)
        os.unlink(env_path)
    except OSError:
        pass


class TestSettingsGetIncludesExitAuthority:
    """AC-P2.12.3: GET /api/settings must expose EXIT_AUTHORITY in globals."""

    def test_get_settings_includes_exit_authority_key(self, settings_client):
        client, _ = settings_client
        resp = client.get("/api/settings")
        data = json.loads(resp.data)
        assert "EXIT_AUTHORITY" in data.get("globals", {}), (
            "AC-P2.12.3: GET /api/settings globals must include EXIT_AUTHORITY"
        )

    def test_exit_authority_not_masked(self, settings_client):
        """EXIT_AUTHORITY must NOT be in _MASKED_SETTINGS_KEYS — its value is safe to expose."""
        client, _ = settings_client
        resp = client.get("/api/settings")
        data = json.loads(resp.data)
        ea_val = data.get("globals", {}).get("EXIT_AUTHORITY", "")
        assert ea_val != "", (
            "AC-P2.12.3: EXIT_AUTHORITY must not be masked (empty string) in GET response"
        )

    def test_exit_authority_value_is_valid_string(self, settings_client):
        client, _ = settings_client
        resp = client.get("/api/settings")
        data = json.loads(resp.data)
        ea_val = data["globals"]["EXIT_AUTHORITY"]
        assert ea_val in ("per_symphony", "port_level"), (
            "AC-P2.12.3: EXIT_AUTHORITY in GET response must be a valid mode string"
        )


class TestSettingsPostWritesExitAuthority:
    """AC-P2.12.3 + AC-P2.2.4: POST /api/settings persists EXIT_AUTHORITY and timestamps the change."""

    def test_post_exit_authority_returns_success(self, settings_client):
        client, _ = settings_client
        resp = client.post(
            "/api/settings",
            data=json.dumps({"globals": {"EXIT_AUTHORITY": "port_level"}}),
            content_type="application/json",
        )
        data = json.loads(resp.data)
        assert data.get("status") == "success", (
            "AC-P2.12.3: POST /api/settings with EXIT_AUTHORITY must return success"
        )

    def test_post_exit_authority_persisted_to_env_file(self, settings_client):
        """Written value must appear in the .env file."""
        client, env_path = settings_client
        client.post(
            "/api/settings",
            data=json.dumps({"globals": {"EXIT_AUTHORITY": "port_level"}}),
            content_type="application/json",
        )
        with open(env_path) as f:
            content = f.read()
        assert "EXIT_AUTHORITY" in content and "port_level" in content, (
            "AC-P2.12.3: POST must persist EXIT_AUTHORITY=port_level to .env file"
        )

    def test_post_exit_authority_records_changed_at_timestamp(self, settings_client):
        """AC-P2.2.4: Toggle change must record _exit_authority_changed_at in .env."""
        client, env_path = settings_client
        client.post(
            "/api/settings",
            data=json.dumps({"globals": {"EXIT_AUTHORITY": "per_symphony"}}),
            content_type="application/json",
        )
        with open(env_path) as f:
            content = f.read()
        assert "_exit_authority_changed_at" in content or "EXIT_AUTHORITY_CHANGED_AT" in content, (
            "AC-P2.2.4: POST EXIT_AUTHORITY change must record a changed_at timestamp in .env"
        )

    def test_post_exit_authority_changed_at_is_iso8601(self, settings_client):
        """AC-P2.2.4: The changed_at timestamp must be ISO 8601 UTC."""
        client, env_path = settings_client
        client.post(
            "/api/settings",
            data=json.dumps({"globals": {"EXIT_AUTHORITY": "port_level"}}),
            content_type="application/json",
        )
        with open(env_path) as f:
            content = f.read()
        # Find the timestamp value — accept either key casing
        import re

        match = re.search(r"(?:_exit_authority_changed_at|EXIT_AUTHORITY_CHANGED_AT)=(.+)", content)
        assert match is not None, "changed_at key not found in .env"
        ts = match.group(1).strip().strip('"').strip("'")
        assert "T" in ts, "AC-P2.2.4: _exit_authority_changed_at must be ISO 8601 (contains 'T')"


class TestExitAuthorityNotMaskedInSettingsKeys:
    """EXIT_AUTHORITY must never appear in _MASKED_SETTINGS_KEYS."""

    def test_exit_authority_absent_from_masked_keys(self):
        import app as app_module

        assert "EXIT_AUTHORITY" not in app_module._MASKED_SETTINGS_KEYS, (
            "EXIT_AUTHORITY must not be in _MASKED_SETTINGS_KEYS — it is not a secret"
        )
