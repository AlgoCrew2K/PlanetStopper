"""
RED tests — A-1 (HIGH): POST /api/settings writes ANY key to .env with no allowlist.

Security audit finding A-1 confirmed that `save_settings` iterates
`globals_payload.items()` and calls `set_key(ENV_FILE_PATH, key, str(val))`
for every key without restriction.  This means any unauthenticated caller can:

  - Set LIVE_EXECUTION=True (arms real-money execution)
  - Overwrite credentials (COMPOSER_SECRET, ALPACA_SECRET, DISCORD_WEBHOOK_URL)
  - Inject arbitrary keys not part of the operator UI contract

Required fix: enforce an allowlist in `save_settings` so that only
operator-configurable settings pass through; credential keys may be written
(the operator legitimately needs to update them), but LIVE_EXECUTION MUST NOT
be settable to True via the dashboard (it is an arming flag with real financial
consequences that requires a deliberate out-of-band action).

The tests in this file are adversarial — they attempt every bypass vector
noted in the audit: casing variants, whitespace padding, alias names, and
Unicode look-alike characters.  A weak allowlist check (e.g., `if key.upper()
== "LIVE_EXECUTION"`) passes all of these; only a strict equality check on
the exact key name, applied BEFORE set_key, is sufficient.

Expected state: RED (all tests fail) until the allowlist is implemented.
"""

from __future__ import annotations

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def captured_set_key_calls(monkeypatch):
    """Replace dotenv set_key with a recorder; returns the list of (key, val) tuples."""
    calls: list[tuple[str, str]] = []

    def _fake_set_key(path, key, val, *_a, **_kw):
        calls.append((key, val))
        return True, key, val

    monkeypatch.setattr(app_module, "set_key", _fake_set_key)
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", "/fake/.env")
    return calls


@pytest.fixture
def _stub_database(monkeypatch):
    """Minimal database stub so save_settings can call save_symphony_strategy."""
    from unittest.mock import MagicMock

    db_mock = MagicMock()
    db_mock.save_symphony_strategy.return_value = None
    monkeypatch.setattr(app_module, "database", db_mock)
    return db_mock


# ---------------------------------------------------------------------------
# A-1 RED-1: LIVE_EXECUTION=True must be REJECTED outright
# ---------------------------------------------------------------------------


def test_post_settings_rejects_live_execution_true(client, captured_set_key_calls, _stub_database):
    """Attempting to set LIVE_EXECUTION=True via POST /api/settings must be
    rejected.  The route must return a non-200 status OR return status=error;
    the key must NOT be passed to set_key with value 'True'.

    LIVE_EXECUTION arms real-money execution.  The dashboard is explicitly
    documented as a read-only operator surface.  Enabling live execution
    from the dashboard would bypass the deliberate out-of-band arming step.

    Adversarial input: the straightforward case — exact key, 'True' value.
    """
    payload = {
        "globals": {"LIVE_EXECUTION": "True"},
        "symphonies": {},
    }
    resp = client.post("/api/settings", json=payload)

    # The route must signal rejection — either HTTP 400/403/422 or
    # a 200 with status=error.  HTTP 500 is NOT acceptable (that means the
    # code crashed, not that it rejected the input deliberately).
    body = resp.get_json()
    is_rejected_status = resp.status_code in (400, 403, 422)
    is_error_body = (
        isinstance(body, dict) and body.get("status") == "error" and resp.status_code != 500
    )
    assert is_rejected_status or is_error_body, (
        f"POST /api/settings with LIVE_EXECUTION=True must be rejected; "
        f"got HTTP {resp.status_code} body={body!r}. "
        f"A 500 means the code crashed rather than deliberately rejecting — also unacceptable."
    )

    # Belt-and-suspenders: even if the route returned an error body, the key
    # must NOT have reached set_key.
    live_exec_writes = [
        (k, v)
        for (k, v) in captured_set_key_calls
        if k == "LIVE_EXECUTION" and v.strip().lower() in ("true", "1", "yes")
    ]
    assert not live_exec_writes, (
        "set_key was called with LIVE_EXECUTION=True despite rejection. "
        "The allowlist check must happen BEFORE the set_key loop. "
        f"Captured calls that reached set_key: {captured_set_key_calls}"
    )


# ---------------------------------------------------------------------------
# A-1 RED-2: LIVE_EXECUTION=False must ALSO be blocked (no partial write)
# ---------------------------------------------------------------------------


def test_post_settings_rejects_live_execution_false_too(
    client, captured_set_key_calls, _stub_database
):
    """LIVE_EXECUTION (even set to False) must not be writable via the dashboard.

    Rationale: if the route allows writing LIVE_EXECUTION=False, an attacker
    who can observe the request can also try =True.  The allowlist must
    structurally exclude LIVE_EXECUTION regardless of the value provided —
    not merely check the value.  A value-based guard is insufficient:
    a second request with =True would pass.
    """
    payload = {
        "globals": {"LIVE_EXECUTION": "False"},
        "symphonies": {},
    }
    resp = client.post("/api/settings", json=payload)

    # Any write of LIVE_EXECUTION via this route is a violation.
    live_exec_writes = [k for (k, _v) in captured_set_key_calls if k == "LIVE_EXECUTION"]
    assert not live_exec_writes, (
        "LIVE_EXECUTION must not be writable via POST /api/settings regardless of "
        "value.  The allowlist must exclude the key entirely, not filter by value. "
        f"set_key was called with LIVE_EXECUTION. HTTP {resp.status_code}, body={resp.get_json()!r}"
    )


# ---------------------------------------------------------------------------
# A-1 RED-3 through RED-7: casing / whitespace bypass attempts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bypass_key",
    [
        "live_execution",  # lower-case
        "Live_Execution",  # mixed-case
        "LIVE_EXECUTION ",  # trailing space
        " LIVE_EXECUTION",  # leading space
        "\tLIVE_EXECUTION",  # tab prefix
        "LIVE_EXECUTION\n",  # newline suffix
    ],
)
def test_post_settings_rejects_live_execution_casing_and_whitespace_bypasses(
    bypass_key, client, captured_set_key_calls, _stub_database
):
    """Casing and whitespace variants of LIVE_EXECUTION must be rejected.

    dotenv's set_key preserves the exact key name written to the file.
    The Python daemon reads LIVE_EXECUTION via os.getenv("LIVE_EXECUTION"),
    which is case-sensitive on Linux/macOS and case-insensitive on Windows.
    A weak filter that only matches the exact string "LIVE_EXECUTION" would
    pass "live_execution" or "LIVE_EXECUTION " on a Windows deployment.

    The allowlist implementation must normalise (strip + upper) the incoming
    key before checking it against the blocklist, or perform a case-insensitive
    match — either approach closes this bypass.
    """
    payload = {
        "globals": {bypass_key: "True"},
        "symphonies": {},
    }
    resp = client.post("/api/settings", json=payload)
    body = resp.get_json()

    # The bypass key must not reach set_key at all.
    dangerous_writes = [
        (k, v)
        for (k, v) in captured_set_key_calls
        if k.strip().upper() == "LIVE_EXECUTION" and v.strip().lower() in ("true", "1", "yes")
    ]
    assert not dangerous_writes, (
        f"Bypass key {bypass_key!r} reached set_key with a truthy value. "
        f"The allowlist check must normalise (strip/upper) the key before "
        f"comparison.  HTTP {resp.status_code}, body={body!r}, "
        f"captured calls: {captured_set_key_calls}"
    )


# ---------------------------------------------------------------------------
# A-1 RED-8: credential keys must be EXCLUDED from the allowlist (not writable via dashboard)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "credential_key",
    [
        "COMPOSER_KEY_ID",
        "COMPOSER_SECRET",
        "ALPACA_KEY",
        "ALPACA_SECRET",
        "DISCORD_WEBHOOK_URL",
    ],
)
def test_post_settings_rejects_credential_key_writes(
    credential_key, client, captured_set_key_calls, _stub_database
):
    """Credential keys must NOT be writable via POST /api/settings.

    The implementation deliberately excludes credential keys from
    _SETTINGS_WRITE_ALLOWLIST — credentials must be rotated directly in .env,
    never through the unauthenticated dashboard.  This is a more restrictive
    design than merely blocking LIVE_EXECUTION: it removes the entire credential
    write surface from an unauthenticated endpoint.

    This test pins that credential keys are NOT accepted even when submitted
    by an operator — the error message should guide them to edit .env directly.
    """
    payload = {
        "globals": {credential_key: "new-test-placeholder-value"},
        "symphonies": {},
    }
    resp = client.post("/api/settings", json=payload)
    body = resp.get_json()

    # The route must reject credential keys — HTTP 400 or status=error.
    is_rejected_status = resp.status_code == 400
    is_error_body = (
        isinstance(body, dict) and body.get("status") == "error" and resp.status_code != 500
    )
    assert is_rejected_status or is_error_body, (
        f"POST /api/settings with credential key {credential_key!r} must be rejected. "
        f"Credential rotation must happen via .env, not the unauthenticated dashboard. "
        f"Got HTTP {resp.status_code} body={body!r}"
    )

    # The credential must not have reached set_key.
    credential_writes = [k for (k, _v) in captured_set_key_calls if k == credential_key]
    assert not credential_writes, (
        f"Credential key {credential_key!r} reached set_key despite rejection. "
        f"The allowlist check must run BEFORE the set_key loop. "
        f"Captured calls: {captured_set_key_calls}"
    )


# ---------------------------------------------------------------------------
# A-1 RED-9: arbitrary non-allowlisted keys must be rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arbitrary_key",
    [
        "ARBITRARY_UNKNOWN_KEY",
        "INJECTED_KEY",
        "PYTHONPATH",
        "PATH",
        "FLASK_ENV",
        "DEBUG",
        "SECRET_KEY",
        "_exit_authority_changed_at",  # internal timestamp key — not operator-settable
    ],
)
def test_post_settings_rejects_arbitrary_non_allowlisted_keys(
    arbitrary_key, client, captured_set_key_calls, _stub_database
):
    """Arbitrary keys that are not in the allowlist must be rejected.

    The current code writes every key in the payload without restriction.
    This allows an attacker to inject any environment variable — e.g.,
    PYTHONPATH to redirect imports, PATH to hijack executables, or
    FLASK_ENV=development to enable the debugger.

    The fix must be a strict allowlist.  If a key is not in the allowlist,
    the write must be silently dropped (preferred — avoids leaking the
    allowlist structure in error messages) or explicitly rejected.

    _exit_authority_changed_at is an internal timestamp that save_settings
    writes programmatically when EXIT_AUTHORITY changes.  It must NOT be
    directly settable by a client payload.
    """
    payload = {
        "globals": {arbitrary_key: "injected-value"},
        "symphonies": {},
    }
    resp = client.post("/api/settings", json=payload)

    arbitrary_writes = [(k, v) for (k, v) in captured_set_key_calls if k == arbitrary_key]
    assert not arbitrary_writes, (
        f"Arbitrary key {arbitrary_key!r} reached set_key — no allowlist is enforced. "
        f"POST /api/settings must only write keys in the explicit allowlist. "
        f"HTTP {resp.status_code}, captured calls: {captured_set_key_calls}"
    )


# ---------------------------------------------------------------------------
# A-1 RED-10: allowlist constant must exist as a module-level symbol
# ---------------------------------------------------------------------------


def test_settings_allowlist_exists_as_module_symbol():
    """The allowlist must be declared as a named module-level constant in app.py.

    A constant named _SETTINGS_GLOBALS_ALLOWLIST (or similar) provides a
    single source of truth that can be audited, tested, and updated in one
    place.  An inline set/dict literal inside save_settings is not sufficient
    — it is invisible to other tests and easy to accidentally widen.

    Acceptable names: any module-level frozenset/set/dict attribute whose
    name contains "ALLOWLIST" and "SETTINGS" (case-insensitive).
    """
    allowlist_attrs = [
        name
        for name in dir(app_module)
        if "allowlist" in name.lower() and "setting" in name.lower()
    ]
    assert allowlist_attrs, (
        "No settings allowlist constant found in app.py. "
        "Expected a module-level frozenset/set/dict whose name contains "
        "both 'ALLOWLIST' and 'SETTINGS' (e.g. _SETTINGS_GLOBALS_ALLOWLIST). "
        "An inline literal inside save_settings is not sufficient — it must "
        "be a named, auditable constant. "
        f"Searched dir(app_module) for matching names; found none. "
        f"All module attrs starting with '_': "
        f"{[n for n in dir(app_module) if n.startswith('_') and not n.startswith('__')][:30]}"
    )


# ---------------------------------------------------------------------------
# A-1 RED-11: the allowlist must include the algo param keys from _ALGO_PARAM_META
# ---------------------------------------------------------------------------


def test_settings_allowlist_covers_all_algo_param_keys():
    """The settings allowlist must include every key in _ALGO_PARAM_META.

    _ALGO_PARAM_META defines the algorithm parameter knobs that operators
    legitimately tune (TRIGGER_THRESHOLD_PCT, MAX_SQUEEZE_FLOOR, etc.).
    If the allowlist forgets any of these, saving a tuned parameter silently
    fails — the operator believes they saved their change but it was dropped.

    This test pins that the allowlist is a superset of _ALGO_PARAM_META.
    """
    allowlist_attrs = [
        name
        for name in dir(app_module)
        if "allowlist" in name.lower() and "setting" in name.lower()
    ]
    assert allowlist_attrs, (
        "Cannot check allowlist coverage — no allowlist constant found. "
        "See test_settings_allowlist_exists_as_module_symbol."
    )

    allowlist = getattr(app_module, allowlist_attrs[0])
    for key in app_module._ALGO_PARAM_META:
        assert key in allowlist, (
            f"Algo param key {key!r} is in _ALGO_PARAM_META but NOT in the "
            f"settings allowlist {allowlist_attrs[0]!r}. Saving this parameter "
            f"via the Settings UI would be silently dropped. "
            f"The allowlist must be a superset of _ALGO_PARAM_META."
        )


# ---------------------------------------------------------------------------
# A-1 RED-12: EXIT_AUTHORITY and EXECUTION_START_TIME must be in the allowlist
# ---------------------------------------------------------------------------


def test_settings_allowlist_covers_operator_configurable_globals():
    """Non-credential globals that operators set via the UI must be in the allowlist.

    EXIT_AUTHORITY and EXECUTION_START_TIME are legitimate operator settings
    written from the dashboard.  If the allowlist excludes them, the Settings
    page silently loses functionality.
    """
    allowlist_attrs = [
        name
        for name in dir(app_module)
        if "allowlist" in name.lower() and "setting" in name.lower()
    ]
    assert allowlist_attrs, "Cannot check — no allowlist found. See RED-10."

    allowlist = getattr(app_module, allowlist_attrs[0])
    for required_key in ("EXIT_AUTHORITY", "EXECUTION_START_TIME"):
        assert required_key in allowlist, (
            f"{required_key!r} must be in the settings globals allowlist. "
            f"It is legitimately set by the operator from the dashboard. "
            f"Current allowlist: {sorted(allowlist) if hasattr(allowlist, '__iter__') else allowlist!r}"
        )
