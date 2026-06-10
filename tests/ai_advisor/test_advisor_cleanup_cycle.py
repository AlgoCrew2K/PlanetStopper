"""
advisor-cleanup cycle — RED tests for AC1-AC5.

AC1 — D-1 security leak in request_suggestions messages.parse failure path.
AC2 — assemble_advisor_context must HONOR a non-_SENTINEL autotune_run param.
AC3 — Source-comment count pins: 6 Optuna keys, 7-item allowlist, TRIGGER excluded.
AC4 — 4 orphaned advisor templates must not exist; SPA /ai-advisor still renders.
AC5 — No regression: full suite passes, CSRF intact, D-1 holds all advisor error paths.

Mocking strategy
----------------
* Anthropic SDK client        — patched at the ai_advisor module boundary.
* database calls              — patched via ``patch.object`` on the app_module.database
                                reference (for route tests) or via patch("database.*")
                                for direct ai_advisor.assemble_advisor_context calls.
* symphony_logic              — patched to return a minimal condensed_logic fixture.
* No live API calls. No live DB writes.

All tests are function-scoped. No shared module-level mutables.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest

import ai_advisor
import app as app_module

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_MINIMAL_AUTOTUNE_RUN = {
    "symphony_id": "test-symphony",
    "train_alpha": 0.05,
    "oos_alpha": 0.03,
    "baseline_decision": "accepted",
    "pbo": 0.2,
}

_MINIMAL_CONDENSED_LOGIC: dict = {"assets": [], "conditions": []}


@pytest.fixture(scope="module")
def flask_client():
    """Module-scoped Flask test client — creates the app once, reused per test.

    CSRF is disabled globally in conftest._disable_csrf_for_tests (autouse),
    so no token injection is required in POST tests within this module.
    """
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# AC1 — D-1 security leak: messages.parse failure path must NOT echo str(exc).
#
# Bug: ai_advisor.py:622-631 embeds the full `{exc}` in the returned
# error_message when messages.parse raises. That message is directly
# forwarded to the browser by app.py:3319 (`return jsonify({"error": error_msg})`).
# A "could not connect to proxy host=secret.internal.host" exception or an
# API key embedded in an SDK error message would therefore reach the browser
# verbatim, violating D-1.
#
# Fix target: ai_advisor.py:622-631 — mirror the client-construction failure
# path (line 604) which correctly uses only type(exc).__name__.
# ---------------------------------------------------------------------------

_SECRET_SENTINEL = "sk-ant-secret-api-key-SENTINEL-VALUE"


@pytest.fixture
def parse_raises_with_secret():
    """Patch _build_client to return a client whose messages.parse raises with
    a sentinel secret-like string. Used to verify D-1: the secret must NOT
    appear in the returned error_message string.

    Patch target: ai_advisor._build_client (the lazy-import factory) — the
    established pattern in this test suite (see test_ai_advisor.py:724).
    """
    mock_client = MagicMock()
    mock_client.messages.parse.side_effect = RuntimeError(
        f"Connection failed: key={_SECRET_SENTINEL} proxy=secret.internal.host"
    )
    with patch.object(ai_advisor, "_build_client", return_value=mock_client):
        yield mock_client


def test_d1_parse_failure_error_message_omits_exception_text(parse_raises_with_secret):
    """The error_message returned by request_suggestions when messages.parse
    raises must NOT contain any part of str(exc) — specifically the secret
    sentinel embedded in the exception message must not appear.

    This is the D-1 contract: exception text may contain API keys, internal
    paths, or other secrets and must never reach the browser-facing layer.
    """
    context = {"scope": "global", "symphony_id": None}

    _, error_msg = ai_advisor.request_suggestions(context)

    assert error_msg is not None, "A parse failure must return a non-None error_message"
    assert _SECRET_SENTINEL not in error_msg, (
        f"D-1 violation: the secret sentinel string appears in the error_message "
        f"returned to the browser. error_message={error_msg!r}. "
        "The messages.parse failure path must use type(exc).__name__ only, "
        "never str(exc) or f-string embedding {exc}."
    )


def test_d1_parse_failure_error_message_contains_class_name(parse_raises_with_secret):
    """The error_message must contain the exception CLASS name so the operator
    can identify the failure type without receiving any secret content.

    Companion to test_d1_parse_failure_error_message_omits_exception_text:
    omitting secrets is necessary; surfacing the class name is sufficient for
    operator triage and must be present.
    """
    context = {"scope": "global", "symphony_id": None}

    _, error_msg = ai_advisor.request_suggestions(context)

    assert error_msg is not None
    assert "RuntimeError" in error_msg, (
        f"The error_message must contain the exception class name ('RuntimeError') "
        f"so the operator can triage the failure type. Got: {error_msg!r}"
    )


def test_d1_route_level_parse_failure_does_not_leak_secret(flask_client):
    """Route-level RED: POST /ai-advisor/suggest must not echo the secret
    sentinel when the ai_advisor parse path raises.

    This test uses the REAL ai_advisor module (mock only the Anthropic client
    and DB/state calls). The route's own D-1 handler at app.py:3337-3343 is a
    separate guard, but this test isolates the ai_advisor.request_suggestions
    path — the error_msg it returns must already be sanitized BEFORE the route
    forwards it via jsonify.
    """

    exploding_client = MagicMock()
    exploding_client.messages.parse.side_effect = ValueError(
        f"API auth failed: token={_SECRET_SENTINEL}"
    )

    with (
        patch.object(ai_advisor, "_build_client", return_value=exploding_client),
        patch.object(app_module, "database") as mock_db,
        patch("ai_advisor.symphony_logic") as mock_logic,
        patch("ai_advisor.database") as mock_ai_db,
    ):
        mock_db.load_state.return_value = {
            "test-symphony": {"name": "Test Symphony"}
        }
        mock_db.get_latest_autotune_run.return_value = None
        mock_ai_db.get_latest_autotune_run.return_value = None
        mock_logic.get_condensed_logic.return_value = _MINIMAL_CONDENSED_LOGIC

        resp = flask_client.post(
            "/ai-advisor/suggest",
            json={"symphony_id": "test-symphony"},
        )

    data = resp.get_json()
    assert data is not None, "Route must return JSON"
    error_text = str(data.get("error", ""))
    assert _SECRET_SENTINEL not in error_text, (
        f"D-1 violation at route level: secret sentinel leaked in JSON response. "
        f"response JSON={data!r}. The messages.parse failure path must be "
        "sanitized before the error_msg reaches jsonify."
    )


# ---------------------------------------------------------------------------
# AC2 — assemble_advisor_context must honor a passed autotune_run and not
# double-fetch from DB.
#
# Bug: ai_advisor.py:497 unconditionally rebinds the param to None and line
# 501 always calls database.get_latest_autotune_run — the _SENTINEL/passed-value
# guard is present in the docstring but absent in the implementation.
# ---------------------------------------------------------------------------


def test_assemble_context_uses_passed_autotune_run_without_db_fetch():
    """When a non-_SENTINEL autotune_run is passed, assemble_advisor_context
    must use it directly and NOT call database.get_latest_autotune_run.

    Scenario: the suggest route pre-fetches autotune_run (one DB round-trip)
    and passes it to assemble_advisor_context to avoid a second fetch. The
    current implementation ignores the param (line 497) and always re-fetches,
    wasting a DB call and breaking the route-level DB mock coverage.
    """
    with (
        patch("ai_advisor.database") as mock_db,
        patch("ai_advisor.symphony_logic") as mock_logic,
    ):
        mock_db.get_latest_autotune_run.return_value = {
            "symphony_id": "should-not-see-this",
            "train_alpha": 0.99,
        }
        mock_logic.get_condensed_logic.return_value = _MINIMAL_CONDENSED_LOGIC

        ai_advisor.assemble_advisor_context(
            scope="symphony",
            symphony_id="test-symphony",
            autotune_run=_MINIMAL_AUTOTUNE_RUN,  # non-_SENTINEL: must be honored
        )

        # The DB call must NOT have been made — the param was supplied.
        mock_db.get_latest_autotune_run.assert_not_called(), (
            "assemble_advisor_context called database.get_latest_autotune_run even "
            "though a pre-fetched autotune_run was passed. The param is a no-op "
            "(ai_advisor.py:497 overwrites it unconditionally). Fix: only fetch when "
            "autotune_run is _SENTINEL."
        )


def test_assemble_context_with_sentinel_fetches_from_db():
    """When autotune_run is _SENTINEL (the default), assemble_advisor_context
    must call database.get_latest_autotune_run exactly once for the symphony.

    This is the normal path — caller did not pre-fetch — and the function must
    still work correctly by fetching from DB itself.
    """
    with (
        patch("ai_advisor.database") as mock_db,
        patch("ai_advisor.symphony_logic") as mock_logic,
    ):
        mock_db.get_latest_autotune_run.return_value = _MINIMAL_AUTOTUNE_RUN
        mock_logic.get_condensed_logic.return_value = _MINIMAL_CONDENSED_LOGIC

        # Call without passing autotune_run — _SENTINEL is the default.
        ai_advisor.assemble_advisor_context(
            scope="symphony",
            symphony_id="test-symphony",
        )

        mock_db.get_latest_autotune_run.assert_called_once_with("test-symphony"), (
            "assemble_advisor_context must call database.get_latest_autotune_run "
            "exactly once when autotune_run is _SENTINEL (default). "
            "Got call_count=%d" % mock_db.get_latest_autotune_run.call_count
        )


def test_assemble_context_uses_passed_none_autotune_run_without_db_fetch():
    """Explicit None is a valid non-_SENTINEL value meaning 'Optuna has not run'.

    When the caller explicitly passes autotune_run=None, that means 'I know
    there is no autotune run — skip the DB fetch.' The function must honor this
    and NOT call get_latest_autotune_run, even though None is falsy.

    This distinguishes 'not provided' (_SENTINEL, must fetch) from 'explicitly
    provided as None' (must skip fetch), a subtlety the bug ignores entirely.
    """
    with (
        patch("ai_advisor.database") as mock_db,
        patch("ai_advisor.symphony_logic") as mock_logic,
    ):
        mock_db.get_latest_autotune_run.return_value = _MINIMAL_AUTOTUNE_RUN
        mock_logic.get_condensed_logic.return_value = _MINIMAL_CONDENSED_LOGIC

        ai_advisor.assemble_advisor_context(
            scope="symphony",
            symphony_id="test-symphony",
            autotune_run=None,  # explicitly None: no Optuna run, skip fetch
        )

        mock_db.get_latest_autotune_run.assert_not_called(), (
            "assemble_advisor_context called database.get_latest_autotune_run even "
            "though autotune_run=None was explicitly passed. None means 'caller "
            "knows Optuna has not run'; _SENTINEL means 'caller did not provide'. "
            "The function must not conflate them."
        )


def test_route_suggest_causes_only_one_db_fetch_for_autotune_run(flask_client):
    """Route /ai-advisor/suggest must trigger exactly ONE get_latest_autotune_run
    call total — the route pre-fetches and passes it to assemble_advisor_context,
    which must then skip its own internal fetch.

    This is the double-fetch bug: with the current no-op param, app.py:3304 calls
    get_latest_autotune_run, then assemble_advisor_context ignores the passed
    value and calls it again internally (ai_advisor.database is a separate import
    so the app-level mock doesn't cover it). The fix requires both:
      1. The route passes the pre-fetched value.
      2. assemble_advisor_context honors the passed value.

    We mock BOTH database references (app_module.database and ai_advisor.database)
    and assert the combined call count is 1.
    """
    with (
        patch.object(app_module, "database") as mock_app_db,
        patch("ai_advisor.database") as mock_ai_db,
        patch("ai_advisor.symphony_logic") as mock_logic,
    ):
        mock_app_db.load_state.return_value = {
            "test-symphony": {"name": "Test Symphony"}
        }
        mock_app_db.get_latest_autotune_run.return_value = _MINIMAL_AUTOTUNE_RUN
        mock_ai_db.get_latest_autotune_run.return_value = _MINIMAL_AUTOTUNE_RUN

        mock_logic.get_condensed_logic.return_value = _MINIMAL_CONDENSED_LOGIC

        from ai_advisor import ConfigSuggestionsResponse

        # Short-circuit the Claude call so the route completes without a client.
        with (
            patch(
                "ai_advisor.request_suggestions",
                return_value=(ConfigSuggestionsResponse(suggestions=[]), None),
            ),
            patch("ai_advisor.build_assessment_from_context", return_value={}),
        ):
            flask_client.post(
                "/ai-advisor/suggest",
                json={"symphony_id": "test-symphony"},
            )

        ai_advisor_fetch_count = mock_ai_db.get_latest_autotune_run.call_count
        assert ai_advisor_fetch_count == 0, (
            f"ai_advisor.database.get_latest_autotune_run was called "
            f"{ai_advisor_fetch_count} time(s) — it must NOT be called because "
            "the route pre-fetches autotune_run and passes it to "
            "assemble_advisor_context, which must honor the passed value."
        )


# ---------------------------------------------------------------------------
# AC3 — Source-comment pin: 6 Optuna keys, 7-item allowlist, TRIGGER excluded.
#
# The comments in ai_advisor.py are inconsistent:
#   line 30: "6 Optuna search-space keys" — CORRECT
#   line 62: "9-item ALLOWLIST" — WRONG (it's 7: 6 Optuna + MAX_SQUEEZE_FLOOR)
#   line 64: "7 Optuna search-space keys" — WRONG (there are 6)
#   line 125: "7 Optuna keys" — WRONG (there are 6)
#   line 695: "9-item suggestible allowlist" — WRONG (it's 7)
#   line 731: "9-item" — WRONG
#   Multiple places reference TRIGGER_THRESHOLD_PCT as if it were in the
#   allowlist when it is NOT (it is NOT in _OPTUNA_SEARCH_SPACE_KEYS).
#
# These three tests pin the runtime constants so drift from the corrected
# comments causes immediate test failure.
# ---------------------------------------------------------------------------


def test_optuna_search_space_keys_count_is_six():
    """_OPTUNA_SEARCH_SPACE_KEYS must have exactly 6 keys.

    Source comments say "6 Optuna search-space keys" at line 30 (correct) and
    "7 Optuna search-space keys" at lines 64 and 125 (wrong). The runtime
    constant is the truth. Pins the count so a comment correction that
    accidentally deletes or adds a key fails here.
    """
    count = len(ai_advisor._OPTUNA_SEARCH_SPACE_KEYS)
    assert count == 6, (
        f"_OPTUNA_SEARCH_SPACE_KEYS has {count} entries, expected 6. "
        "The corrected comments must say '6 Optuna search-space keys', "
        "not '7'. Current keys: %s" % sorted(ai_advisor._OPTUNA_SEARCH_SPACE_KEYS)
    )


def test_suggestible_allowlist_count_is_seven():
    """_SUGGESTIBLE_ALLOWLIST must have exactly 7 items: 6 Optuna keys + MAX_SQUEEZE_FLOOR.

    Source comments variously say '9-item allowlist' (wrong — there is no
    TRIGGER_THRESHOLD_PCT in the allowlist; the actual count is 6+1=7).
    Pins the runtime count so corrected comments that accidentally alter the
    set fail here.
    """
    count = len(ai_advisor._SUGGESTIBLE_ALLOWLIST)
    assert count == 7, (
        f"_SUGGESTIBLE_ALLOWLIST has {count} entries, expected 7 "
        "(6 Optuna search-space keys + MAX_SQUEEZE_FLOOR). "
        "Corrected source comments must say '7-item allowlist' everywhere. "
        "Current keys: %s" % sorted(ai_advisor._SUGGESTIBLE_ALLOWLIST)
    )


def test_trigger_threshold_pct_not_in_suggestible_allowlist():
    """TRIGGER_THRESHOLD_PCT must NOT be in _SUGGESTIBLE_ALLOWLIST.

    This key appears in _PARAM_DEFINITIONS (element 1 context only) but is
    NOT in _OPTUNA_SEARCH_SPACE_KEYS and therefore must not be in the allowlist.
    Comments that mention 'TRIGGER_THRESHOLD_PCT' as suggestible are misleading
    and must be corrected — this test pins that TRIGGER is context-only,
    never suggestible.
    """
    assert "TRIGGER_THRESHOLD_PCT" not in ai_advisor._SUGGESTIBLE_ALLOWLIST, (
        "TRIGGER_THRESHOLD_PCT appears in _SUGGESTIBLE_ALLOWLIST but it is NOT "
        "an Optuna search-space key and must NOT be suggestible. "
        "It belongs in _PARAM_DEFINITIONS for context only. "
        "Check that no comment correction accidentally added it."
    )


# ---------------------------------------------------------------------------
# AC4 — 4 orphaned advisor templates must not exist.
#
# Templates that 302-redirect to /ai-advisor are dead code since the in-place
# SPA migration. They should be deleted. These guard tests assert they are gone.
# The SPA itself (/ai-advisor) must still render (smoke test).
# ---------------------------------------------------------------------------

_WORKTREE = pathlib.Path(__file__).resolve().parents[2]  # worktree root
_TEMPLATES_DIR = _WORKTREE / "templates"

_ORPHANED_TEMPLATES = [
    "ai_advisor_correlations.html",
    "ai_advisor_asset_swaps.html",
    "ai_advisor_logic_changes.html",
    "ai_advisor_chat.html",
]


@pytest.mark.parametrize("template_name", _ORPHANED_TEMPLATES)
def test_orphaned_advisor_template_does_not_exist(template_name):
    """Each of the 4 orphaned advisor templates must NOT exist in templates/.

    These files are dead code — their routes 302-redirect to /ai-advisor.
    Keeping them is misleading (they look like render_template targets) and
    creates confusion about which template is canonical.
    """
    template_path = _TEMPLATES_DIR / template_name
    assert not template_path.exists(), (
        f"Orphaned template {template_name!r} still exists at {template_path}. "
        "Delete it — its route 302-redirects to /ai-advisor; "
        "it is never passed to render_template()."
    )


def test_no_render_template_calls_for_orphaned_templates():
    """app.py must not contain render_template("<name>") calls for any of the
    4 orphaned templates — those routes must only 302-redirect.

    Source-inspection guard: search for the template filename inside the source
    text near a render_template call. The orphaned templates' ROUTE FUNCTIONS
    may still exist (they 302-redirect) but must not render the dead templates.
    """
    import re as _re

    app_source = (_WORKTREE / "app.py").read_text(encoding="utf-8")
    for template_name in _ORPHANED_TEMPLATES:
        # A render_template call using the orphaned template would look like:
        # render_template("ai_advisor_correlations.html", ...)
        pattern = rf'render_template\s*\(\s*["\']' + _re.escape(template_name)
        assert not _re.search(pattern, app_source), (
            f"app.py contains render_template({template_name!r}) — "
            "this orphaned template must never be rendered; "
            "its route must 302-redirect to /ai-advisor only."
        )


def test_spa_ai_advisor_still_renders(flask_client):
    """The unified SPA at GET /ai-advisor must still render (200 OK) after
    the orphaned templates are deleted.

    This is the regression guard for AC4: deleting the dead templates must not
    break the live SPA route.
    """
    with (
        patch.object(app_module, "database") as mock_db,
    ):
        mock_db.load_state.return_value = {}
        mock_db.get_market_state.return_value = "open"
        resp = flask_client.get("/ai-advisor")

    assert resp.status_code == 200, (
        f"GET /ai-advisor returned {resp.status_code}, expected 200. "
        "Deleting the orphaned templates must not break the unified SPA route."
    )


# ---------------------------------------------------------------------------
# AC5 — Regression guards: CSRF preserved, D-1 holds on ALL advisor error paths.
# ---------------------------------------------------------------------------


def test_csrf_preserved_on_advisor_suggest_post(flask_client):
    """POST /ai-advisor/suggest must be CSRF-protected.

    The conftest._disable_csrf_for_tests autouse fixture disables the CSRF check
    globally for the test suite. This test explicitly re-enables it and verifies
    that a request WITHOUT the CSRF token is rejected (403 or 400).

    D-1 and the CSRF gate are orthogonal — AC1 must not remove the CSRF guard.
    """
    import app as app_module

    # Temporarily re-enable the CSRF check.
    original = app_module._csrf_check_enabled
    app_module._csrf_check_enabled = True
    try:
        resp = flask_client.post(
            "/ai-advisor/suggest",
            json={"symphony_id": "test-symphony"},
            # No X-CSRF-Token header.
        )
        # CSRF rejection must be 400 or 403.
        assert resp.status_code in (400, 403), (
            f"Expected a CSRF rejection (400 or 403) when no token is provided, "
            f"got {resp.status_code}. The CSRF guard on POST /ai-advisor/suggest "
            "must not have been removed by the D-1 fix."
        )
    finally:
        app_module._csrf_check_enabled = original


def test_d1_client_construction_failure_does_not_leak_secrets():
    """D-1 regression: the CLIENT CONSTRUCTION failure path (ai_advisor.py:600-606)
    must also not echo str(exc).

    This path already uses type(exc).__name__ (it is correct). This test pins
    it as a regression guard so AC1's fix doesn't accidentally break this path.

    Patch target: ai_advisor._build_client (the lazy-import factory that
    anthropic.Anthropic() is called inside).
    """
    with patch.object(
        ai_advisor,
        "_build_client",
        side_effect=ConnectionError(f"proxy refused: secret={_SECRET_SENTINEL}"),
    ):
        _, error_msg = ai_advisor.request_suggestions({"scope": "global"})

    assert error_msg is not None
    assert _SECRET_SENTINEL not in error_msg, (
        f"D-1 regression: secret leaked in client-construction error path. "
        f"error_msg={error_msg!r}"
    )
    assert "ConnectionError" in error_msg, (
        "Client-construction error_msg must contain the exception class name."
    )


# ---------------------------------------------------------------------------
# AC5-B — D-1 violations in app.py advisor routes: asset-swaps/evaluate and
# logic-changes/evaluate still embed str(exc) in their JSON error response
# (app.py:2949 and app.py:3086). These were pre-existing at fork-point and are
# in AC5 scope ("D-1 holds on ALL advisor error paths").
#
# A third violation exists at app.py:3029 — the ImportError handler for
# logic_change_engine. All three must be fixed.
#
# Fix target: change `f"{type(exc).__name__}: {exc}"` to `type(exc).__name__`
# only in each jsonify({"error": ...}) return, mirroring app.py:3337-3343.
#
# Mocking strategy: the exceptions fire inside the `try` block that wraps the
# engine call (propose_operator_swap / propose_operator_logic_change). We patch
# those engine functions to raise with a secret-containing message. DB and
# symphony_logic are patched to let the route reach the engine call.
# ---------------------------------------------------------------------------


def test_d1_asset_swaps_evaluate_does_not_leak_exception_text(flask_client):
    """POST /ai-advisor/asset-swaps/evaluate must NOT embed str(exc) in the
    JSON error response when propose_operator_swap raises.

    Bug: app.py:2949 returns `jsonify({"error": f"{type(exc).__name__}: {exc}"})`.
    The `{exc}` embeds the full exception message — violating D-1.

    Fix: return only `type(exc).__name__` (no str(exc)).
    """
    with (
        patch.object(app_module, "database") as mock_db,
        # fetch_symphony_score is lazily imported inside the route body:
        # `from symphony_logic import fetch_symphony_score`
        # Patch target is the source module so the local binding picks it up.
        patch("symphony_logic.fetch_symphony_score", return_value={"type": "root"}),
        # propose_operator_swap is lazily imported: `from advisors.asset_swap_engine import ...`
        patch(
            "advisors.asset_swap_engine.propose_operator_swap",
            side_effect=RuntimeError(
                f"internal-error: db_key={_SECRET_SENTINEL}"
            ),
        ),
    ):
        mock_db.load_state.return_value = {
            "hash-abc123": {"name": "Test Symphony"}
        }
        mock_db.normalize_name.side_effect = lambda x: x.lower().replace(" ", "-")

        resp = flask_client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={
                "symphony_id": "Test Symphony",
                "from_ticker": "SPY",
                "to_ticker": "QQQ",
            },
        )

    data = resp.get_json()
    assert data is not None, "Route must return JSON"
    error_text = str(data.get("error", ""))
    assert _SECRET_SENTINEL not in error_text, (
        f"D-1 violation at app.py:2949: secret sentinel leaked in "
        f"/ai-advisor/asset-swaps/evaluate JSON response. "
        f"response JSON={data!r}. Fix: return only type(exc).__name__, "
        "not f\"{type(exc).__name__}: {exc}\"."
    )
    assert "RuntimeError" in error_text, (
        f"The error field must contain the exception class name ('RuntimeError') "
        f"for operator triage. Got: {error_text!r}"
    )


def test_d1_logic_changes_evaluate_does_not_leak_exception_text(flask_client):
    """POST /ai-advisor/logic-changes/evaluate must NOT embed str(exc) in the
    JSON error response when propose_operator_logic_change raises.

    Bug: app.py:3086 returns `jsonify({"error": f"{type(exc).__name__}: {exc}"})`.

    Fix: return only `type(exc).__name__` (no str(exc)).
    """
    with (
        patch.object(app_module, "database") as mock_db,
        # fetch_symphony_score is lazily imported inside the route body.
        patch("symphony_logic.fetch_symphony_score", return_value={"type": "root"}),
        # propose_operator_logic_change is lazily imported from advisors.logic_change_engine.
        patch(
            "advisors.logic_change_engine.propose_operator_logic_change",
            side_effect=ValueError(
                f"parse-failure: token={_SECRET_SENTINEL}"
            ),
        ),
    ):
        mock_db.load_state.return_value = {
            "hash-abc123": {"name": "Test Symphony"}
        }
        mock_db.normalize_name.side_effect = lambda x: x.lower().replace(" ", "-")

        resp = flask_client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={
                "symphony_id": "Test Symphony",
                "change_description": "change momentum from 20 to 10",
            },
        )

    data = resp.get_json()
    assert data is not None, "Route must return JSON"
    error_text = str(data.get("error", ""))
    assert _SECRET_SENTINEL not in error_text, (
        f"D-1 violation at app.py:3086: secret sentinel leaked in "
        f"/ai-advisor/logic-changes/evaluate JSON response. "
        f"response JSON={data!r}. Fix: return only type(exc).__name__, "
        "not f\"{type(exc).__name__}: {exc}\"."
    )
    assert "ValueError" in error_text, (
        f"The error field must contain the exception class name ('ValueError') "
        f"for operator triage. Got: {error_text!r}"
    )


def test_d1_logic_changes_evaluate_import_error_does_not_leak_exception_text(flask_client):
    """POST /ai-advisor/logic-changes/evaluate must NOT embed str(_ie) in the
    JSON error response when the lazy import of logic_change_engine fails.

    Bug: app.py:3029 returns
        `jsonify({"error": f"advisor unavailable: {type(_ie).__name__}: {_ie}"})`.
    The `{_ie}` embeds the full ImportError message — violating D-1.

    Fix: return `f"advisor unavailable: {type(_ie).__name__}"` only.
    """
    import builtins
    import importlib

    original_import = builtins.__import__

    def _blocking_import(name, *args, **kwargs):
        if name == "advisors.logic_change_engine":
            raise ImportError(
                f"cannot import: missing_dep={_SECRET_SENTINEL}"
            )
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_blocking_import):
        resp = flask_client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={
                "symphony_id": "Test Symphony",
                "change_description": "change momentum from 20 to 10",
            },
        )

    data = resp.get_json()
    assert data is not None, "Route must return JSON"
    error_text = str(data.get("error", ""))
    assert _SECRET_SENTINEL not in error_text, (
        f"D-1 violation at app.py:3029: secret sentinel leaked in ImportError "
        f"handler of /ai-advisor/logic-changes/evaluate. "
        f"response JSON={data!r}. Fix: return only the error class name, "
        "not f\"advisor unavailable: {type(_ie).__name__}: {_ie}\"."
    )
    assert "ImportError" in error_text, (
        f"The error field must contain 'ImportError' for operator triage. "
        f"Got: {error_text!r}"
    )
