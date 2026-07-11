"""RED tests — wave-2 Flask routes for the Frontrunner Builder Advisor tab
(feature-plans/frontrunner-builder.md AC-8/AC-9-route/AC-10-UX).

Team-lead-approved plan (2026-07-11) mirrors the established
tests/app/test_strategy_builder_route.py pattern closely; the endpoint
shape for approve/reject was RULED on (not left to guesswork): a single
opaque ``proposal_id`` — no ``source`` disambiguation param — because both
the Frontrunner Builder's own candidates AND the propose_strategies
retrofit's survivors already land in the SAME ``frontrunner_proposals``
table (migration 033), distinguished only by the ``proposal_source`` column
(verified directly: database.get_pending_frontrunner_proposals has no
proposal_source filter; strategy_builder_engine.py:702 already queues
retrofit survivors into the same table with
proposal_source="strategy_builder_retrofit"; approve_frontrunner_proposal
is already keyed purely by the row id, source-agnostic).

Three routes under test:
  POST /ai-advisor/frontrunner-builder/run   — trigger a build over all (or
                                                one) live symphonies.
  POST /ai-advisor/proposal/approve          — {"proposal_id": <int>} —
                                                generic, serves BOTH sources.
  POST /ai-advisor/proposal/reject           — {"proposal_id": <int>} —
                                                status-only, never touches
                                                composer_draft_client.

MOCKING STRATEGY ("route-level RED for mocked fns" — project memory:
feedback_route_level_red_for_mocked_analytics_fns): the /run tests hit the
REAL advisors.frontrunner_builder module (never patch the whole module) and
mock ONLY the network/DB leaves it calls at their ORIGIN
(symphony_logic.fetch_symphony_score, advisors.composer_backtest_client.
run_backtest, advisors.community_strats.load_community_strategies,
database.load_state, database.insert_frontrunner_proposal,
database.insert_advisor_observation, database.insert_dof_ledger_row,
advisors.frontrunner_builder._build_client for Fable) — a route calling an
undefined function on the real module would 500 live while a
whole-module-mocked test stays green; this pattern makes that impossible.

approve/reject tests mock ONLY composer_draft_client.save_symphony /
verify_undeployed (never a live Composer call — task-zero is
operator-gated) while exercising the REAL approve_frontrunner_proposal
function, same rationale.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Flask test client with testing mode enabled."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture(autouse=False)
def _reenable_csrf(monkeypatch):
    """Re-enable real CSRF enforcement for CSRF-specific tests in this
    module (root conftest.py disables it globally for the rest of the
    suite) — same pattern as tests/app/test_strategy_builder_route.py."""
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)


@pytest.fixture(autouse=False)
def auth_client(monkeypatch):
    """Flask test client with the auth gate ENABLED (overriding the autouse
    _disable_auth_for_tests fixture root conftest.py sets globally to
    protect the existing ~7000-route suite), unauthenticated.

    A minimal local equivalent of tests/app/test_dashboard_auth.py's own
    auth_client fixture — NOT imported cross-file (pytest fixtures defined
    in a sibling test module, as opposed to a conftest.py, are not
    automatically shared across test files), so this mirrors that fixture's
    essential setup (synthetic credential env vars + _auth_check_enabled)
    rather than duplicating its full docstring/rationale, which lives at
    test_dashboard_auth.py:86-109.
    """
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-pass-frontrunner-route-abc123")
    monkeypatch.setenv("SECRET_KEY", "test-secret-frontrunner-route-xyz789")
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _assert_route_exists(resp, route: str) -> None:
    assert resp.status_code != 404, (
        f"{route} returned 404 — the route has not been added to app.py yet."
    )


def _get_csrf_token(client) -> str:
    token_resp = client.get("/api/csrf-token")
    assert token_resp.status_code == 200, (
        f"GET /api/csrf-token returned {token_resp.status_code}; cannot obtain a "
        f"valid CSRF token."
    )
    token = token_resp.get_json().get("csrf_token")
    assert token, "GET /api/csrf-token must return a non-empty csrf_token"
    return token


# ===========================================================================
# Group A: POST /ai-advisor/frontrunner-builder/run — CSRF enforcement
# ===========================================================================


def test_run_without_csrf_token_returns_403(client, _reenable_csrf):
    resp = client.post(
        "/ai-advisor/frontrunner-builder/run",
        json={},
        content_type="application/json",
    )
    _assert_route_exists(resp, "POST /ai-advisor/frontrunner-builder/run")
    assert resp.status_code == 403


def test_run_with_wrong_csrf_token_returns_403(client, _reenable_csrf):
    import secrets

    resp = client.post(
        "/ai-advisor/frontrunner-builder/run",
        json={},
        content_type="application/json",
        headers={"X-CSRF-Token": secrets.token_hex(32)},
    )
    _assert_route_exists(resp, "POST /ai-advisor/frontrunner-builder/run")
    assert resp.status_code == 403


def test_run_with_valid_csrf_token_returns_202(client, _reenable_csrf):
    """RULING (team-lead, 2026-07-11): /run is ASYNC — a dedicated
    _FRONTRUNNER_BUILD_EXECUTOR (max_workers=1, atexit-registered) accepts
    the dispatch; the route returns 202 immediately, never blocking the
    request thread on run_frontrunner_build's genuinely multi-minute
    full-roster work (up to MAX_CASCADES_PER_SYMPHONY_RUN cascades x every
    live symphony x rate-limited Fable/Composer calls). Results persist to
    SQLite; the operator 'polls' by reloading /ai-advisor for the
    server-rendered pending-proposal cards — there is no synchronous result
    body to return."""
    token = _get_csrf_token(client)
    with (
        patch("os.environ.get", side_effect=lambda k, d=None: "fake-key" if k == "ANTHROPIC_API_KEY" else d),
        patch("app._FRONTRUNNER_BUILD_EXECUTOR") as mock_executor,
    ):
        resp = client.post(
            "/ai-advisor/frontrunner-builder/run",
            json={},
            content_type="application/json",
            headers={"X-CSRF-Token": token},
        )
    _assert_route_exists(resp, "POST /ai-advisor/frontrunner-builder/run")
    assert resp.status_code == 202, (
        f"expected 202 (accepted, background-dispatched) per the async ruling, "
        f"got {resp.status_code}"
    )
    data = resp.get_json()
    assert data is not None and data.get("status") == "started", (
        f"expected {{'status': 'started'}}, got {data!r}"
    )
    assert mock_executor.submit.called, (
        "the route did not dispatch to _FRONTRUNNER_BUILD_EXECUTOR.submit — "
        "the build must be fired into the background executor, not run inline"
    )


# ===========================================================================
# Group B: POST /ai-advisor/frontrunner-builder/run — auth enforcement
# ===========================================================================


def test_run_requires_authentication(auth_client):
    """Unauthenticated POST to the run route must not dispatch the build —
    mirrors the dashboard auth gate (DE-AUTH-001) already enforced on every
    other route via the global _auth_before_request hook."""
    with patch("app._FRONTRUNNER_BUILD_EXECUTOR") as mock_executor:
        resp = auth_client.post(
            "/ai-advisor/frontrunner-builder/run",
            json={},
            content_type="application/json",
        )
    _assert_route_exists(resp, "POST /ai-advisor/frontrunner-builder/run")
    assert resp.status_code in (302, 401), (
        f"unauthenticated POST to the run route returned {resp.status_code}, "
        f"expected a 302 redirect (HTML) or 401 (XHR/API) per the auth gate contract"
    )
    mock_executor.submit.assert_not_called()


# ===========================================================================
# Group C: POST /ai-advisor/frontrunner-builder/run — dispatch contract + D-1
# ===========================================================================


def test_run_never_calls_run_frontrunner_build_synchronously_in_request(client):
    """The load-bearing async assertion: run_frontrunner_build itself must
    NEVER be invoked directly on the request thread — only ever handed to
    the executor's .submit(). A regression back to a synchronous call would
    block a Flask worker for potentially minutes on the operator's full
    live-symphony roster."""
    with (
        patch("app._FRONTRUNNER_BUILD_EXECUTOR"),
        patch("advisors.frontrunner_builder.run_frontrunner_build") as mock_run_direct,
    ):
        client.post("/ai-advisor/frontrunner-builder/run", json={}, content_type="application/json")

    mock_run_direct.assert_not_called()


def test_run_dispatches_the_real_run_frontrunner_build_function_to_the_executor(client):
    """Route-level RED for mocked fns, adapted for the async model: since
    the actual multi-minute work is no longer exercised synchronously in a
    request/response cycle, the meaningful route-level proof shifts to
    'did the route correctly import and reference the REAL
    run_frontrunner_build callable when submitting it' — an
    AttributeError/ImportError-shaped bug here would otherwise only
    surface asynchronously, inside the executor thread, invisible to the
    operator's request."""
    with patch("app._FRONTRUNNER_BUILD_EXECUTOR") as mock_executor:
        client.post("/ai-advisor/frontrunner-builder/run", json={}, content_type="application/json")

    assert mock_executor.submit.called
    submitted_args = mock_executor.submit.call_args.args
    assert submitted_args, "executor.submit() was called with no arguments"

    from advisors.frontrunner_builder import run_frontrunner_build as real_run_frontrunner_build

    assert submitted_args[0] is real_run_frontrunner_build, (
        f"the route must submit the REAL advisors.frontrunner_builder."
        f"run_frontrunner_build function object to the executor, got "
        f"{submitted_args[0]!r} — an undefined/wrong reference here would "
        f"only fail inside the background thread, invisible to the operator's "
        f"request/response cycle"
    )


def test_run_response_never_contains_live_execution_key(client):
    with patch("app._FRONTRUNNER_BUILD_EXECUTOR"):
        resp = client.post("/ai-advisor/frontrunner-builder/run", json={}, content_type="application/json")

    _assert_route_exists(resp, "POST /ai-advisor/frontrunner-builder/run")
    data = resp.get_json()
    assert data is not None
    payload_str = str(data)
    assert "LIVE_EXECUTION" not in payload_str, (
        f"the run route's response leaked LIVE_EXECUTION — this is an advisory-only "
        f"route, never a settings/execution surface. Got: {data!r}"
    )


def test_run_submission_exception_returns_static_error_token_never_str_exc(client):
    """D-1 security contract: if the executor SUBMISSION itself raises (the
    only synchronous failure mode left in the async model —
    run_frontrunner_build's own internal exceptions are D-1/never-raise and
    happen off-thread, invisible to this response), the route's own
    except-clause must never echo str(exc) — only type(exc).__name__, same
    pattern as ai_advisor_strategy_builder_run's own outer except (app.py,
    ~line 4565)."""
    secret_bearing_message = "executor pool exhausted: internal_path=/etc/secrets/x"

    mock_executor = MagicMock()
    mock_executor.submit.side_effect = RuntimeError(secret_bearing_message)
    with (
        patch("os.environ.get", side_effect=lambda k, d=None: "fake-key" if k == "ANTHROPIC_API_KEY" else d),
        patch("app._FRONTRUNNER_BUILD_EXECUTOR", mock_executor),
    ):
        resp = client.post("/ai-advisor/frontrunner-builder/run", json={}, content_type="application/json")

    _assert_route_exists(resp, "POST /ai-advisor/frontrunner-builder/run")
    data = resp.get_json()
    assert data is not None
    payload_str = str(data)
    assert "internal_path" not in payload_str and "/etc/secrets/x" not in payload_str, (
        f"the route echoed the raw exception message instead of a static "
        f"type(exc).__name__ token: {data!r}"
    )
    assert data.get("error") == "RuntimeError", (
        f"expected error='RuntimeError' (type(exc).__name__ only), got {data.get('error')!r}"
    )


def test_run_accepts_optional_symphony_ids_for_a_scoped_run(client):
    """AC-1: 'Also invokable on demand via a route' — scoped to specific
    symphonies when symphony_ids is supplied in the body, threaded through
    to the submitted run_frontrunner_build call."""
    with patch("app._FRONTRUNNER_BUILD_EXECUTOR") as mock_executor:
        resp = client.post(
            "/ai-advisor/frontrunner-builder/run",
            json={"symphony_ids": ["sym-test-123"]},
            content_type="application/json",
        )

    _assert_route_exists(resp, "POST /ai-advisor/frontrunner-builder/run")
    assert resp.status_code == 202
    assert mock_executor.submit.called
    call_args = mock_executor.submit.call_args.args
    call_kwargs = mock_executor.submit.call_args.kwargs
    scoped_ids = call_kwargs.get("symphony_ids") or (call_args[1] if len(call_args) > 1 else None)
    assert scoped_ids == ["sym-test-123"], (
        f"symphony_ids in the request body must be threaded through to the "
        f"submitted run_frontrunner_build call, got call_args={call_args!r} "
        f"call_kwargs={call_kwargs!r}"
    )


def test_run_missing_api_key_returns_200_error_and_does_not_submit(client):
    """The fast pre-check (frdash's design decision-1): a missing
    ANTHROPIC_API_KEY is detected BEFORE dispatch — the route must fail fast
    with a 200 error response (matching ai_advisor_strategy_builder_run's
    own no-key-check-delegated-to-the-engine convention, surfaced here at
    the route since the engine call never even happens) and must NEVER
    submit a doomed job to the executor."""
    with (
        patch("os.environ.get", side_effect=lambda k, d=None: d if k == "ANTHROPIC_API_KEY" else d),
        patch("app._FRONTRUNNER_BUILD_EXECUTOR") as mock_executor,
    ):
        resp = client.post("/ai-advisor/frontrunner-builder/run", json={}, content_type="application/json")

    _assert_route_exists(resp, "POST /ai-advisor/frontrunner-builder/run")
    assert resp.status_code == 200, (
        f"a missing ANTHROPIC_API_KEY must fail fast with 200+error (never a "
        f"202 promising background work that cannot succeed), got {resp.status_code}"
    )
    data = resp.get_json()
    assert data is not None and data.get("error"), (
        f"expected a non-empty error field on the no-key fast-fail response, got {data!r}"
    )
    mock_executor.submit.assert_not_called()


# ===========================================================================
# Group D: GET /ai-advisor/frontrunner-builder — redirect stub
# ===========================================================================


def test_get_frontrunner_builder_returns_302(client):
    """Mirrors every other Advisor sub-route: the frontrunner-builder tab
    lives inside the unified /ai-advisor SPA, never as its own standalone
    200 page."""
    resp = client.get("/ai-advisor/frontrunner-builder")
    _assert_route_exists(resp, "GET /ai-advisor/frontrunner-builder")
    assert resp.status_code == 302, (
        f"GET /ai-advisor/frontrunner-builder returned {resp.status_code}, expected "
        f"a 302 redirect to /ai-advisor (SPA model — no standalone page)"
    )


# ===========================================================================
# Group E: POST /ai-advisor/proposal/approve — CSRF enforcement
# ===========================================================================


def test_approve_without_csrf_token_returns_403(client, _reenable_csrf):
    resp = client.post(
        "/ai-advisor/proposal/approve",
        json={"proposal_id": 1},
        content_type="application/json",
    )
    _assert_route_exists(resp, "POST /ai-advisor/proposal/approve")
    assert resp.status_code == 403


def test_approve_with_wrong_csrf_token_returns_403(client, _reenable_csrf):
    import secrets

    resp = client.post(
        "/ai-advisor/proposal/approve",
        json={"proposal_id": 1},
        content_type="application/json",
        headers={"X-CSRF-Token": secrets.token_hex(32)},
    )
    _assert_route_exists(resp, "POST /ai-advisor/proposal/approve")
    assert resp.status_code == 403


def test_approve_with_valid_csrf_token_returns_200(client, _reenable_csrf):
    token = _get_csrf_token(client)
    from advisors.frontrunner_builder import ApprovalResult

    with patch(
        "advisors.frontrunner_builder.approve_frontrunner_proposal",
        return_value=ApprovalResult(success=True, symphony_id="new-sym-1"),
    ):
        resp = client.post(
            "/ai-advisor/proposal/approve",
            json={"proposal_id": 1},
            content_type="application/json",
            headers={"X-CSRF-Token": token},
        )
    _assert_route_exists(resp, "POST /ai-advisor/proposal/approve")
    assert resp.status_code == 200


# ===========================================================================
# Group F: POST /ai-advisor/proposal/approve — auth + response shape + D-1
# ===========================================================================


def test_approve_requires_authentication(auth_client):
    with patch("advisors.frontrunner_builder.approve_frontrunner_proposal") as mock_approve:
        resp = auth_client.post(
            "/ai-advisor/proposal/approve",
            json={"proposal_id": 1},
            content_type="application/json",
        )
    _assert_route_exists(resp, "POST /ai-advisor/proposal/approve")
    assert resp.status_code in (302, 401)
    mock_approve.assert_not_called()


def test_approve_is_source_agnostic_generic_proposal_id_no_source_param_required(client):
    """The RULED contract: the route takes ONLY proposal_id — it must work
    identically for a frontrunner-sourced row and a
    strategy_builder_retrofit-sourced row, since approve_frontrunner_proposal
    itself never inspects proposal_source (both are the same DB row shape,
    frontrunner_proposals table)."""
    from advisors.frontrunner_builder import ApprovalResult

    with patch(
        "advisors.frontrunner_builder.approve_frontrunner_proposal",
        return_value=ApprovalResult(success=True, symphony_id="new-sym-2"),
    ) as mock_approve:
        resp = client.post(
            "/ai-advisor/proposal/approve",
            # Deliberately NO "source" key — the route must not require one.
            json={"proposal_id": 42},
            content_type="application/json",
        )

    _assert_route_exists(resp, "POST /ai-advisor/proposal/approve")
    assert resp.status_code == 200
    mock_approve.assert_called_once_with(42)


def test_approve_engine_error_returns_static_error_token_never_str_exc(client):
    secret_bearing_message = "Composer auth failed: secret=sk-abc123"

    with patch(
        "advisors.frontrunner_builder.approve_frontrunner_proposal",
        side_effect=RuntimeError(secret_bearing_message),
    ):
        resp = client.post(
            "/ai-advisor/proposal/approve",
            json={"proposal_id": 1},
            content_type="application/json",
        )

    _assert_route_exists(resp, "POST /ai-advisor/proposal/approve")
    data = resp.get_json()
    assert data is not None
    assert "sk-abc123" not in str(data)
    assert data.get("error") == "RuntimeError"


def test_approve_response_never_contains_live_execution_key(client):
    from advisors.frontrunner_builder import ApprovalResult

    with patch(
        "advisors.frontrunner_builder.approve_frontrunner_proposal",
        return_value=ApprovalResult(success=True, symphony_id="s1"),
    ):
        resp = client.post(
            "/ai-advisor/proposal/approve",
            json={"proposal_id": 1},
            content_type="application/json",
        )
    _assert_route_exists(resp, "POST /ai-advisor/proposal/approve")
    assert "LIVE_EXECUTION" not in str(resp.get_json())


def test_approve_never_calls_composer_draft_client_directly_only_via_the_function(client):
    """Structural no-trade-boundary check at the ROUTE layer: the route must
    delegate ALL Composer interaction to approve_frontrunner_proposal — it
    must never import/call composer_draft_client.save_symphony itself."""
    from advisors.frontrunner_builder import ApprovalResult

    with (
        patch(
            "advisors.frontrunner_builder.approve_frontrunner_proposal",
            return_value=ApprovalResult(success=True, symphony_id="s1"),
        ),
        patch("advisors.composer_draft_client.save_symphony") as mock_save,
    ):
        client.post(
            "/ai-advisor/proposal/approve",
            json={"proposal_id": 1},
            content_type="application/json",
        )

    mock_save.assert_not_called()


def test_approve_missing_proposal_id_returns_invalid_error_without_calling_the_engine(client):
    """Input validation: a missing/malformed proposal_id must fail fast with
    a shaped error response and must NEVER reach
    approve_frontrunner_proposal (which would raise or misbehave on a
    non-int id)."""
    with patch("advisors.frontrunner_builder.approve_frontrunner_proposal") as mock_approve:
        resp = client.post(
            "/ai-advisor/proposal/approve",
            json={},  # no proposal_id at all
            content_type="application/json",
        )

    _assert_route_exists(resp, "POST /ai-advisor/proposal/approve")
    assert resp.status_code == 200, (
        f"a missing proposal_id must fail fast with 200+error, got {resp.status_code}"
    )
    data = resp.get_json()
    assert data is not None
    assert data.get("success") is False, f"expected success=False, got {data!r}"
    assert data.get("error"), f"expected a non-empty error field, got {data!r}"
    mock_approve.assert_not_called()


# ===========================================================================
# Group G: POST /ai-advisor/proposal/reject
# ===========================================================================


def test_reject_without_csrf_token_returns_403(client, _reenable_csrf):
    resp = client.post(
        "/ai-advisor/proposal/reject",
        json={"proposal_id": 1},
        content_type="application/json",
    )
    _assert_route_exists(resp, "POST /ai-advisor/proposal/reject")
    assert resp.status_code == 403


def test_reject_with_valid_csrf_token_returns_200(client, _reenable_csrf):
    token = _get_csrf_token(client)
    with patch("database.update_frontrunner_proposal_status", return_value=True) as mock_update:
        resp = client.post(
            "/ai-advisor/proposal/reject",
            json={"proposal_id": 1},
            content_type="application/json",
            headers={"X-CSRF-Token": token},
        )
    _assert_route_exists(resp, "POST /ai-advisor/proposal/reject")
    assert resp.status_code == 200
    mock_update.assert_called_once()
    _, call_kwargs = mock_update.call_args
    assert call_kwargs.get("approval_status") == "rejected"


def test_reject_requires_authentication(auth_client):
    with patch("database.update_frontrunner_proposal_status") as mock_update:
        resp = auth_client.post(
            "/ai-advisor/proposal/reject",
            json={"proposal_id": 1},
            content_type="application/json",
        )
    _assert_route_exists(resp, "POST /ai-advisor/proposal/reject")
    assert resp.status_code in (302, 401)
    mock_update.assert_not_called()


def test_reject_never_touches_composer_draft_client(client):
    """AC-11/security: rejecting a proposal is a status-only DB write — it
    must never construct a Composer save_symphony/verify_undeployed call."""
    with (
        patch("database.update_frontrunner_proposal_status", return_value=True),
        patch("advisors.composer_draft_client.save_symphony") as mock_save,
        patch("advisors.composer_draft_client.verify_undeployed") as mock_verify,
    ):
        client.post(
            "/ai-advisor/proposal/reject",
            json={"proposal_id": 1},
            content_type="application/json",
        )

    mock_save.assert_not_called()
    mock_verify.assert_not_called()


def test_reject_engine_error_returns_static_error_token_never_str_exc(client):
    with patch(
        "database.update_frontrunner_proposal_status",
        side_effect=RuntimeError("db path leaked: C:\\Users\\operator\\secret.db"),
    ):
        resp = client.post(
            "/ai-advisor/proposal/reject",
            json={"proposal_id": 1},
            content_type="application/json",
        )

    _assert_route_exists(resp, "POST /ai-advisor/proposal/reject")
    data = resp.get_json()
    assert data is not None
    assert "secret.db" not in str(data)
    assert data.get("error") == "RuntimeError"


def test_reject_missing_proposal_id_returns_invalid_error_without_touching_the_db(client):
    """Input validation: a missing/malformed proposal_id must fail fast and
    must NEVER reach database.update_frontrunner_proposal_status."""
    with patch("database.update_frontrunner_proposal_status") as mock_update:
        resp = client.post(
            "/ai-advisor/proposal/reject",
            json={},  # no proposal_id at all
            content_type="application/json",
        )

    _assert_route_exists(resp, "POST /ai-advisor/proposal/reject")
    assert resp.status_code == 200, (
        f"a missing proposal_id must fail fast with 200+error, got {resp.status_code}"
    )
    data = resp.get_json()
    assert data is not None
    assert data.get("success") is False, f"expected success=False, got {data!r}"
    assert data.get("error"), f"expected a non-empty error field, got {data!r}"
    mock_update.assert_not_called()


# ===========================================================================
# Group H: settings-write-allowlist + CC-2 lazy-import
# ===========================================================================


def test_settings_write_allowlist_does_not_contain_frontrunner_or_live_execution():
    allowlist = app_module._SETTINGS_WRITE_ALLOWLIST
    forbidden = {"frontrunner", "frontrunner_builder", "LIVE_EXECUTION", "proposal_id"}
    hit = forbidden & set(allowlist)
    assert not hit, (
        f"_SETTINGS_WRITE_ALLOWLIST must not contain frontrunner-related or "
        f"LIVE_EXECUTION keys — these are advisory-only routes, never a "
        f"settings-write surface. Found: {hit}"
    )


def test_frontrunner_builder_not_imported_at_module_scope():
    """CC-2: advisors.frontrunner_builder must stay off app.py's module-scope
    import graph (lazy-imported inside the route function only), matching
    the strategy_builder_engine precedent — keeps it off any accidental
    import chain toward the 1-minute live execution path."""
    import ast

    app_source = pathlib.Path(app_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(app_source)
    module_level_imports: set[str] = set()
    for node in tree.body:  # top-level only — not nested inside function defs
        if isinstance(node, ast.ImportFrom) and node.module:
            module_level_imports.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module_level_imports.add(alias.name)

    assert "advisors.frontrunner_builder" not in module_level_imports, (
        "advisors.frontrunner_builder must be imported INSIDE the route "
        "function (CC-2 lazy-import), not at app.py module scope"
    )
