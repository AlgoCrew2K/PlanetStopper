"""
RED tests — MED-8: /api/settings GET must mask Composer + Alpaca + Discord secrets.

Masking pattern: same as existing ANTHROPIC_API_KEY — return "" regardless of
whether the value is set. Secrets are never echoed to the browser.

Fixture provenance: tests/fixtures/app/settings_env_snapshot.json
  — placeholder strings only; no real credentials.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import app as app_module


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "app"

@pytest.fixture(scope="module")
def env_snapshot():
    """Load the env-var fixture snapshot (placeholder credentials only)."""
    with open(_FIXTURES_DIR / "settings_env_snapshot.json") as f:
        return json.load(f)


SECRET_KEYS = [
    "COMPOSER_KEY_ID",
    "COMPOSER_SECRET",
    "ALPACA_KEY",
    "ALPACA_SECRET",
    "DISCORD_WEBHOOK_URL",
]

NON_SECRET_KEYS = [
    "LIVE_EXECUTION",
    "EXECUTION_START_TIME",
    "ACCOUNT_INDIVIDUAL",
    "ACCOUNT_ROTH",
    "ACCOUNT_TRAD",
]


# ---------------------------------------------------------------------------
# Shared test client
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_database(monkeypatch):
    from unittest.mock import patch
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
        yield db_mock


# ---------------------------------------------------------------------------
# RED 1: masked secrets are "" in GET response when set to non-empty values
# ---------------------------------------------------------------------------

def test_get_settings_masks_all_five_secrets_when_set(
    client, mock_database, env_snapshot, monkeypatch
):
    """
    GET /api/settings must return "" for every secret key even when the
    env contains non-empty placeholder values. Pins that NO raw secret
    leaks to the browser.
    """
    full_env = {**env_snapshot["secrets"], **env_snapshot["non_secrets"]}
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: full_env)

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    globals_out = resp.get_json()["globals"]

    for key in SECRET_KEYS:
        assert globals_out[key] == "", (
            f"{key} must be masked to '' in GET response, got: {globals_out[key]!r}"
        )


# ---------------------------------------------------------------------------
# RED 2: raw secret value must NOT appear anywhere in the response body
# ---------------------------------------------------------------------------

def test_get_settings_raw_secret_value_absent_from_response_body(
    client, mock_database, env_snapshot, monkeypatch
):
    """
    The raw placeholder strings from the fixture must be absent from the
    entire serialised response body. Asserts on format/absence, not value.
    """
    full_env = {**env_snapshot["secrets"], **env_snapshot["non_secrets"]}
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: full_env)

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body_text = resp.get_data(as_text=True)

    for key, raw_value in env_snapshot["secrets"].items():
        assert raw_value not in body_text, (
            f"Raw value for {key} must not appear in response body"
        )


# ---------------------------------------------------------------------------
# RED 3: missing/None secrets render as "" — no crash, no spurious mask chars
# ---------------------------------------------------------------------------

def test_get_settings_missing_secrets_render_as_empty_string(
    client, mock_database, monkeypatch
):
    """
    When secret env vars are absent (None / missing), GET must return ""
    for each — no KeyError, no "***None***", no crash.
    """
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {
        "LIVE_EXECUTION": "False",
        "EXECUTION_START_TIME": "09:30",
    })

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    globals_out = resp.get_json()["globals"]

    for key in SECRET_KEYS:
        assert key in globals_out, f"{key} must be present in globals even when unset"
        val = globals_out[key]
        assert val == "" or val is None, (
            f"{key} with no env value must be '' or null, got: {val!r}"
        )
        # No spurious mask artifact — value must not contain asterisks.
        if val:
            assert "*" not in str(val), (
                f"{key} with empty source must not contain mask characters, got: {val!r}"
            )


# ---------------------------------------------------------------------------
# RED 4: non-secret settings pass through unchanged
# ---------------------------------------------------------------------------

def test_get_settings_non_secret_fields_pass_through_unchanged(
    client, mock_database, env_snapshot, monkeypatch
):
    """
    LIVE_EXECUTION, EXECUTION_START_TIME, and ACCOUNT_* must round-trip
    exactly. Masking must not touch them.
    """
    full_env = {**env_snapshot["secrets"], **env_snapshot["non_secrets"]}
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: full_env)

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    globals_out = resp.get_json()["globals"]

    for key in NON_SECRET_KEYS:
        assert key in globals_out, f"Non-secret key {key} must be present in response"
        assert globals_out[key] == env_snapshot["non_secrets"][key], (
            f"{key} must round-trip unchanged; "
            f"got {globals_out[key]!r}, expected {env_snapshot['non_secrets'][key]!r}"
        )


# ---------------------------------------------------------------------------
# RED 5: ANTHROPIC_API_KEY masking pattern is consistent with new secrets
# ---------------------------------------------------------------------------

def test_get_settings_anthropic_key_also_masked_for_consistency(
    client, mock_database, monkeypatch
):
    """
    Pre-existing ANTHROPIC_API_KEY mask must still return "" (regression guard).
    All 6 secrets (including Anthropic) use the same pattern.
    """
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {
        "ANTHROPIC_API_KEY": "sk-ant-test-placeholder-value",
        "COMPOSER_KEY_ID": "test-ck",
        "COMPOSER_SECRET": "test-cs",
    })

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    globals_out = resp.get_json()["globals"]

    assert globals_out.get("ANTHROPIC_API_KEY") == "", (
        "ANTHROPIC_API_KEY must still be masked as '' (regression guard)"
    )


# ---------------------------------------------------------------------------
# RED 6: POST /api/settings write path is unaffected — secrets still saveable
# ---------------------------------------------------------------------------

def test_post_settings_can_still_write_secret_keys(
    client, mock_database, monkeypatch
):
    """
    POST /api/settings must accept and persist secret keys. The masking is
    display-only (GET). Writing a new COMPOSER_KEY_ID value must succeed.
    """
    set_key_calls = []

    def fake_set_key(path, key, val, *_a, **_kw):
        set_key_calls.append((key, val))
        return True, key, val

    monkeypatch.setattr(app_module, "set_key", fake_set_key)
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", "/fake/.env")

    payload = {
        "globals": {
            "COMPOSER_KEY_ID": "new-test-key-id",
            "COMPOSER_SECRET": "new-test-secret",
            "ALPACA_KEY": "new-alpaca-key",
            "ALPACA_SECRET": "new-alpaca-secret",
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/111/new-placeholder",
        },
        "symphonies": {},
    }
    resp = client.post("/api/settings", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success", f"POST must succeed; got: {body}"

    written_keys = {k for (k, _v) in set_key_calls}
    for key in SECRET_KEYS:
        assert key in written_keys, (
            f"POST must write {key} to .env; masking must NOT block the write path"
        )


# ---------------------------------------------------------------------------
# RED 7: single masking implementation — no duplicated per-secret logic
#         (structural: all 5 secrets share one helper or one uniform pattern)
# ---------------------------------------------------------------------------

def test_get_settings_masking_is_uniform_not_per_secret_branches(
    client, mock_database, env_snapshot, monkeypatch
):
    """
    Structural guard: all 5 secrets must produce identical masked output
    format ("") even when set to distinct non-empty placeholder values of
    different lengths. If masking were per-secret branching, one key could
    silently differ.
    """
    full_env = {**env_snapshot["secrets"], **env_snapshot["non_secrets"]}
    # Ensure all secrets have distinct non-empty values (fixture guarantees this).
    for key in SECRET_KEYS:
        assert full_env[key], f"Fixture must have non-empty value for {key}"

    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: full_env)

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    globals_out = resp.get_json()["globals"]

    masked_values = [globals_out[k] for k in SECRET_KEYS]
    # All must be identical (all "") — uniform masking, not branching.
    assert len(set(masked_values)) == 1, (
        f"All secret keys must produce identical masked output; got: "
        f"{dict(zip(SECRET_KEYS, masked_values))}"
    )
    assert masked_values[0] == "", "Uniform masked value must be empty string"


# ---------------------------------------------------------------------------
# RED 8: _MASKED_SETTINGS_KEYS is the authoritative list — all its members
#         must be masked in the GET response (frozenset drives the contract)
# ---------------------------------------------------------------------------

def test_every_key_in_masked_settings_frozenset_is_suppressed_in_response(
    client, mock_database, monkeypatch
):
    """
    Contract: every key declared in _MASKED_SETTINGS_KEYS that is present
    in the env must be suppressed to "" in GET /api/settings globals.

    This test iterates the frozenset itself — if a developer adds a new secret
    to _MASKED_SETTINGS_KEYS but forgets to add the corresponding _mask_secret()
    call in the get_settings() dict literal, this test will catch it.

    Also guards the inverse: if _MASKED_SETTINGS_KEYS is empty or shrunken,
    the test will fail on the coverage assertion.
    """
    # Build an env that has a non-empty placeholder value for every declared secret.
    env = {key: f"test-placeholder-{key.lower()}" for key in app_module._MASKED_SETTINGS_KEYS}
    env["LIVE_EXECUTION"] = "False"
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: env)

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    globals_out = resp.get_json()["globals"]
    body_text = resp.get_data(as_text=True)

    # The frozenset must cover at least the 6 known secrets (5 + ANTHROPIC).
    assert len(app_module._MASKED_SETTINGS_KEYS) >= 6, (
        "_MASKED_SETTINGS_KEYS must declare at least 6 secrets; it may have been accidentally shrunk"
    )

    for key in app_module._MASKED_SETTINGS_KEYS:
        # If the key surfaces in globals at all, it must be masked.
        if key in globals_out:
            assert globals_out[key] == "", (
                f"{key} is in _MASKED_SETTINGS_KEYS but NOT masked in GET response — "
                f"got: {globals_out[key]!r}. The frozenset must drive the masking, not be dead code."
            )
        # The raw placeholder value must never appear in the response body.
        raw = env[key]
        assert raw not in body_text, (
            f"Raw value for {key} leaked into response body — masking failed for a declared secret key"
        )
