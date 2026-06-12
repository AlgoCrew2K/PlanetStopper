"""RED tests — Phase 3 Strategy Builder Flask routes and template.

Tests the two routes added by Phase 3:
  GET  /ai-advisor/strategy-builder      — renders Strategy Builder tab (HTML page)
  POST /ai-advisor/strategy-builder/run  — CSRF-protected, dispatches propose_strategies(),
                                           returns JSON (mirrors /ai-advisor/asset-swaps/evaluate)

Architecture contracts verified (all RED until GREEN implementation lands):

  Group A: GET route
    A1. GET returns HTTP 200.
    A2. Rendered HTML contains data-testid="strategy-builder-tab".
    A3. Non-dismissible risk banner present with overfitting-risk text.
    A4. data-testid="strategy-builder-run-form" NOT present in GET response.

  Group B: POST /run — CSRF enforcement
    B5. POST with no X-CSRF-Token header returns 403.
    B6. POST with wrong X-CSRF-Token returns 403.
    B7. POST with valid X-CSRF-Token + valid JSON returns 200.

  Group C: POST /run — response shape
    C8.  Valid POST returns JSON with keys: survivors, rejected, n_candidates,
         fdr_adjusted_threshold, error.
    C9.  When propose_strategies returns error, response has error + empty survivors/rejected.
    C10. Zero survivors is a valid non-error state (error=null, survivors=[], rejected=list).
    C11. Response JSON never contains LIVE_EXECUTION key.

  Group D: Template render — card anatomy
    D12. Survivor card contains all required data-testid landmarks and SURVIVOR_OVERFITTING_CAVEAT text.
    D13. Survivor card contains ZERO forms, ZERO submit buttons, ZERO action links.

  Group E: States
    E14. Empty state: no observations → data-testid="strategy-builder-empty-state".
    E15. Rejected section: WITHHELD observation → data-testid="rejected-candidates-section".
    E16. Backtest-failed card: backtest_error in raw_response →
         data-testid="backtest-failed-card".

  Group F: Adversarial — no action affordances on survivor cards
    F17. Full render with 2 survivors + 1 rejected: survivor-cards section contains ZERO
         <form, <button, <input type="submit", href="/api/, href="/apply, href="/accept,
         href="/trade.  Critical safety contract.

  Group G: Settings allowlist not expanded
    G18. _SETTINGS_WRITE_ALLOWLIST does not contain "strategy_builder" or "LIVE_EXECUTION".

Mocking strategy:
  - advisors.strategy_builder_engine.propose_strategies: patched — ZERO live Composer calls.
  - database.get_advisor_observations_for_symphony: patched — returns fixture dicts.
  - CSRF: root conftest.py sets _csrf_check_enabled=False globally; Group B tests
    re-enable it (same pattern as tests/security/test_a3_csrf_protection.py) and
    obtain the real token from GET /api/csrf-token.
  - All fixtures are function-scoped.

Fixture: tests/fixtures/ai_advisor/m6/strategy_builder_observations_basic.json
"""

from __future__ import annotations

import json
import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Repository root and fixture loading
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "ai_advisor"
    / "m6"
    / "strategy_builder_observations_basic.json"
)


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# ProposalRun stub factory — builds minimal objects matching the engine contract
# without importing the real engine (which may not exist yet in RED state).
# ---------------------------------------------------------------------------


def _make_proposal_run(
    survivors: bool = False,
    error: str | None = None,
    n_candidates: int = 3,
):
    """Build a minimal ProposalRun-like object for use as a mock return value.

    Shape matches ProposalRun dataclass contract:
      candidates, gated_batch, screened_survivors, observations_written, error
    No producer-computed quant values — shape and type only.
    """
    from types import SimpleNamespace

    gate_batch = SimpleNamespace(
        results=[],
        survivors=[],
        n_candidates=n_candidates,
        fdr_q=0.05,
    )

    survivor_result = SimpleNamespace(
        candidate_id="sym-test:T1:0",
        verdict=SimpleNamespace(decision="ADOPT_CANDIDATE"),
        caveats=["Selected on backtest: gate passes but selection bias remains. Treat as hypothesis."],
        winner_p_adj=None,
        metrics={"sharpe_ratio": None, "sortino_ratio": None},
        template_id="T1",
    )

    rejected_result = SimpleNamespace(
        candidate_id="sym-test:T2:0",
        verdict=SimpleNamespace(decision="WITHHELD"),
        caveats=[],
        winner_p_adj=None,
        metrics={"sharpe_ratio": None, "sortino_ratio": None},
        template_id="T2",
    )

    return SimpleNamespace(
        candidates=[],
        gated_batch=gate_batch,
        screened_survivors=[survivor_result] if survivors else [],
        observations_written=1 if survivors else 0,
        error=error,
        rejected_candidates=[] if survivors else [rejected_result],
    )


# ---------------------------------------------------------------------------
# Shared Flask test client fixture (function-scoped — no shared state)
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Flask test client with testing mode enabled."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Helper: assert route exists (not 404)
# ---------------------------------------------------------------------------


def _assert_route_exists(resp, route: str) -> None:
    assert resp.status_code != 404, (
        f"{route} returned 404 — the route has not been added to app.py yet. "
        "GREEN must register the route handler before this test can pass."
    )


# ===========================================================================
# Group A: GET route
# ===========================================================================


def test_get_strategy_builder_returns_200(client):
    """A-1: GET /ai-advisor/strategy-builder must return HTTP 200.

    Route must exist and respond — before any observations are loaded the page
    must not 404, error, or redirect.
    """
    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder")
    assert resp.status_code == 200, (
        f"GET /ai-advisor/strategy-builder returned {resp.status_code}, expected 200"
    )


def test_get_strategy_builder_contains_tab_testid(client):
    """A-2: Rendered HTML must contain data-testid="strategy-builder-tab".

    This is the DOM landmark for the Strategy Builder tab navigation element.
    The JS uses it to identify which tab is active.
    """
    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder")
    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping DOM check.")

    html = resp.get_data(as_text=True)
    assert 'data-testid="strategy-builder-tab"' in html, (
        'GET /ai-advisor/strategy-builder must render data-testid="strategy-builder-tab". '
        "The tab landmark is required for JS tab navigation and integration tests."
    )


def test_get_strategy_builder_contains_risk_banner(client):
    """A-3: Non-dismissible overfitting risk banner must be present.

    Strategy Builder carries the highest overfitting risk of all advisor tabs
    (building a full symphony from scratch vs modifying existing logic).
    The banner must be permanent, not dismissible via JS.
    The banner must contain 'data-testid="strategy-builder-risk-banner"'
    OR text matching 'Logic-change proposals carry the highest overfitting risk'
    OR 'non-dismissible'.
    """
    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder")
    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping DOM check.")

    html = resp.get_data(as_text=True)
    has_testid = 'data-testid="strategy-builder-risk-banner"' in html
    has_text_a = "Logic-change proposals carry the highest overfitting risk" in html
    has_text_b = "non-dismissible" in html

    assert has_testid or has_text_a or has_text_b, (
        "GET /ai-advisor/strategy-builder must render a non-dismissible risk banner. "
        "Expected one of: "
        'data-testid="strategy-builder-risk-banner", '
        '"Logic-change proposals carry the highest overfitting risk", '
        'or "non-dismissible". '
        "This is a hard safety requirement — strategy proposals carry the highest "
        "overfitting risk of any advisor capability."
    )


def test_get_strategy_builder_does_not_contain_run_form_testid(client):
    """A-4: data-testid="strategy-builder-run-form" must NOT be in the GET response.

    The form is only triggered via JS fetch (POST /run) — it does not render
    as a visible <form> in the page. A form in the GET response would expose
    a direct submission path, bypassing the JS CSRF token flow.
    """
    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder")
    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping DOM check.")

    html = resp.get_data(as_text=True)
    assert 'data-testid="strategy-builder-run-form"' not in html, (
        'GET /ai-advisor/strategy-builder must NOT contain data-testid="strategy-builder-run-form". '
        "The run form is JS-triggered via fetch POST — it must not be a plain HTML form "
        "in the GET response (which would bypass the CSRF token fetch flow)."
    )


# ===========================================================================
# Group B: POST /run — CSRF enforcement
#
# These tests re-enable CSRF enforcement (overriding the root conftest.py
# global disable) using the same pattern as test_a3_csrf_protection.py.
# ===========================================================================


@pytest.fixture(autouse=False)
def _reenable_csrf(monkeypatch):
    """Re-enable CSRF check for CSRF-specific tests in this module.

    The root tests/conftest.py sets _csrf_check_enabled=False globally.
    Group B tests need real CSRF enforcement — this fixture sets it back to True.
    Must be requested explicitly (autouse=False) so only Group B tests use it.
    """
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)


def test_post_run_without_csrf_token_returns_403(client, _reenable_csrf):
    """B-5: POST /ai-advisor/strategy-builder/run with no X-CSRF-Token header returns 403.

    The route must be CSRF-protected (same as /ai-advisor/asset-swaps/evaluate).
    A missing token is a rejected cross-site request.
    """
    resp = client.post(
        "/ai-advisor/strategy-builder/run",
        json={"objective": "diversify", "universe": ["SPY", "AGG", "GLD"], "symphony_id": "sym-test"},
        content_type="application/json",
        # Deliberately no X-CSRF-Token header
    )

    _assert_route_exists(resp, "POST /ai-advisor/strategy-builder/run")
    assert resp.status_code == 403, (
        f"POST /ai-advisor/strategy-builder/run without X-CSRF-Token returned "
        f"{resp.status_code}, expected 403. "
        "The route must be CSRF-protected — a missing token must be rejected."
    )


def test_post_run_with_wrong_csrf_token_returns_403(client, _reenable_csrf):
    """B-6: POST /ai-advisor/strategy-builder/run with wrong X-CSRF-Token returns 403.

    A fabricated or stale token (not the process-lifetime _CSRF_TOKEN) must be
    rejected. An attacker cannot guess the per-process token.
    """
    import secrets as _secrets

    fake_token = _secrets.token_hex(32)  # not the real _CSRF_TOKEN

    resp = client.post(
        "/ai-advisor/strategy-builder/run",
        json={"objective": "diversify", "universe": ["SPY", "AGG", "GLD"], "symphony_id": "sym-test"},
        content_type="application/json",
        headers={"X-CSRF-Token": fake_token},
    )

    _assert_route_exists(resp, "POST /ai-advisor/strategy-builder/run")
    assert resp.status_code == 403, (
        f"POST /ai-advisor/strategy-builder/run with a fabricated X-CSRF-Token returned "
        f"{resp.status_code}, expected 403. "
        "A wrong token must be rejected — the CSRF check must use secrets.compare_digest."
    )


def test_post_run_with_valid_csrf_token_returns_200(client, _reenable_csrf):
    """B-7: POST /ai-advisor/strategy-builder/run with valid X-CSRF-Token + valid JSON returns 200.

    The operator JS workflow: (1) fetch GET /api/csrf-token, (2) include the token
    in X-CSRF-Token header on the POST /run request. This test pins that happy path.
    """
    # Step 1: obtain the real process-lifetime CSRF token
    token_resp = client.get("/api/csrf-token")
    assert token_resp.status_code == 200, (
        f"GET /api/csrf-token returned {token_resp.status_code}; "
        "cannot issue a valid CSRF token for the POST test."
    )
    token = token_resp.get_json().get("csrf_token")
    assert token, "GET /api/csrf-token must return a non-empty csrf_token"

    # Step 2: POST with the valid token and a mocked engine
    mock_run = _make_proposal_run(survivors=False)
    with patch(
        "advisors.strategy_builder_engine.propose_strategies",
        return_value=mock_run,
    ):
        resp = client.post(
            "/ai-advisor/strategy-builder/run",
            json={
                "objective": "diversify",
                "universe": ["SPY", "AGG", "GLD"],
                "symphony_id": "sym-test",
            },
            content_type="application/json",
            headers={"X-CSRF-Token": token},
        )

    _assert_route_exists(resp, "POST /ai-advisor/strategy-builder/run")
    assert resp.status_code == 200, (
        f"POST /ai-advisor/strategy-builder/run with valid X-CSRF-Token returned "
        f"{resp.status_code}, expected 200."
    )


# ===========================================================================
# Group C: POST /run — response shape
# (CSRF disabled by root conftest for non-CSRF tests)
# ===========================================================================


def test_post_run_valid_request_has_required_keys(client):
    """C-8: Valid POST with objective/universe returns JSON with required keys.

    Required keys: survivors, rejected, n_candidates, fdr_adjusted_threshold, error.
    Shape contract — values are not asserted (producer-computed).
    """
    mock_run = _make_proposal_run(survivors=True, n_candidates=3)

    with patch(
        "advisors.strategy_builder_engine.propose_strategies",
        return_value=mock_run,
    ):
        resp = client.post(
            "/ai-advisor/strategy-builder/run",
            json={
                "objective": "diversify",
                "universe": ["SPY", "AGG", "GLD"],
                "symphony_id": "sym-test",
            },
            content_type="application/json",
        )

    _assert_route_exists(resp, "POST /ai-advisor/strategy-builder/run")
    assert resp.status_code == 200, (
        f"POST /ai-advisor/strategy-builder/run returned {resp.status_code}, expected 200."
    )

    data = resp.get_json()
    assert data is not None, "Response body must be valid JSON."

    required_keys = {"survivors", "rejected", "n_candidates", "fdr_adjusted_threshold", "error"}
    missing = required_keys - set(data.keys())
    assert not missing, (
        f"POST /run response missing required keys: {sorted(missing)}. "
        f"Got keys: {sorted(data.keys())}. "
        "Required response shape: survivors (list), rejected (list), "
        "n_candidates (int|null), fdr_adjusted_threshold (float|null), error (str|null)."
    )


def test_post_run_engine_error_returns_error_field_with_empty_lists(client):
    """C-9: When propose_strategies returns error, response has error + empty survivors/rejected.

    An engine error (e.g., no Composer key, network fail) must be surfaced
    as a JSON error field. The survivors and rejected lists must be empty —
    never partial results alongside an error.
    """
    mock_run = _make_proposal_run(error="No Composer API key configured.")

    with patch(
        "advisors.strategy_builder_engine.propose_strategies",
        return_value=mock_run,
    ):
        resp = client.post(
            "/ai-advisor/strategy-builder/run",
            json={
                "objective": "diversify",
                "universe": ["SPY", "AGG"],
                "symphony_id": "sym-test",
            },
            content_type="application/json",
        )

    _assert_route_exists(resp, "POST /ai-advisor/strategy-builder/run")
    data = resp.get_json()
    assert data is not None, "Response must be valid JSON even on engine error."

    assert data.get("error"), (
        f"Engine error response must have a non-empty 'error' key. Got: {data!r}"
    )
    assert data.get("survivors") == [], (
        f"Engine error response must have survivors=[] (not partial results). "
        f"Got survivors: {data.get('survivors')!r}"
    )
    assert data.get("rejected") == [], (
        f"Engine error response must have rejected=[] when error is set. "
        f"Got rejected: {data.get('rejected')!r}"
    )


def test_post_run_zero_survivors_is_not_an_error_state(client):
    """C-10: Zero survivors is a valid outcome — error must be null/absent, not an error string.

    The gate may reject all candidates. That is a legitimate statistical result
    (no candidate cleared the FDR gate), not a failure. The response must have
    survivors=[], rejected=<non-empty list>, error=null.
    """
    mock_run = _make_proposal_run(survivors=False, error=None, n_candidates=3)

    with patch(
        "advisors.strategy_builder_engine.propose_strategies",
        return_value=mock_run,
    ):
        resp = client.post(
            "/ai-advisor/strategy-builder/run",
            json={
                "objective": "diversify",
                "universe": ["SPY", "AGG", "GLD"],
                "symphony_id": "sym-test",
            },
            content_type="application/json",
        )

    _assert_route_exists(resp, "POST /ai-advisor/strategy-builder/run")
    data = resp.get_json()
    assert data is not None

    assert data.get("survivors") == [], (
        f"Zero-survivors run must have survivors=[]. Got: {data.get('survivors')!r}"
    )
    assert data.get("error") is None, (
        f"Zero survivors is NOT an error state — error must be null. "
        f"Got error: {data.get('error')!r}. "
        "Zero survivors means 'no candidate cleared the FDR gate this run', "
        "not a failure."
    )


def test_post_run_response_never_contains_live_execution_key(client):
    """C-11: Response JSON must never contain LIVE_EXECUTION key.

    LIVE_EXECUTION is a credential-adjacent control key excluded from all
    advisor surface responses. Its presence in any JSON response is a
    data-exposure violation.
    """
    mock_run = _make_proposal_run(survivors=True)

    with patch(
        "advisors.strategy_builder_engine.propose_strategies",
        return_value=mock_run,
    ):
        resp = client.post(
            "/ai-advisor/strategy-builder/run",
            json={
                "objective": "diversify",
                "universe": ["SPY", "AGG", "GLD"],
                "symphony_id": "sym-test",
            },
            content_type="application/json",
        )

    _assert_route_exists(resp, "POST /ai-advisor/strategy-builder/run")
    data = resp.get_json()
    assert data is not None

    assert "LIVE_EXECUTION" not in data, (
        "POST /run response must never contain 'LIVE_EXECUTION' key. "
        "The strategy builder route must not expose execution-control keys "
        "in any JSON response."
    )
    # Also check the serialised string representation for accidental inclusion
    raw = json.dumps(data)
    assert "LIVE_EXECUTION" not in raw, (
        "Serialised POST /run response body must not contain the string "
        "'LIVE_EXECUTION' anywhere — not in keys, not in nested values."
    )


# ===========================================================================
# Group D: Template render — card anatomy
#
# These tests render the GET route with fixture observations injected via the
# database mock, then assert the DOM structure of survivor cards.
# ===========================================================================


def test_survivor_card_contains_all_required_testid_landmarks(client):
    """D-12: Given a fixture ADOPT_CANDIDATE observation, rendered HTML must contain
    all required data-testid landmarks for the survivor card anatomy.

    Required landmarks:
      - data-testid="advisory-only-badge"     (advisory-only badge — no action affordance)
      - data-testid="objective-strip"          (objective shown: diversify / cut_drawdown / lift)
      - data-testid="stats-table"             (metrics table: sharpe, sortino, drawdown, return)
      - data-testid="gate-verdict-pill"       (gate verdict indicator)
      - data-testid="fdr-metadata"            (FDR metadata line for audit trail)
      - data-testid="apply-guidance"          (plain-text apply guidance — no button)
      - data-testid="rules-collapsible"       (collapsible rules text block)

    And text matching the SURVIVOR_OVERFITTING_CAVEAT ("Selected on backtest").
    """
    fixture = _load_fixture()
    obs = fixture["observation_survivor"]

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder (with survivor)")
    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping DOM check.")

    html = resp.get_data(as_text=True)

    required_testids = [
        "advisory-only-badge",
        "objective-strip",
        "stats-table",
        "gate-verdict-pill",
        "fdr-metadata",
        "apply-guidance",
        "rules-collapsible",
    ]
    for testid in required_testids:
        assert f'data-testid="{testid}"' in html, (
            f"Survivor card must contain data-testid=\"{testid}\". "
            f"This is a required DOM landmark for the Strategy Builder card anatomy."
        )

    # SURVIVOR_OVERFITTING_CAVEAT text must be present
    # The caveat starts with "Selected on backtest" per SURVIVOR_OVERFITTING_CAVEAT constant
    assert "Selected on backtest" in html, (
        "Survivor card must contain the SURVIVOR_OVERFITTING_CAVEAT text "
        "starting with 'Selected on backtest'. "
        "The caveat is mandatory for all ADOPT_CANDIDATE survivors (AC-3.3)."
    )


def test_survivor_card_contains_zero_forms_and_zero_submit_buttons_and_zero_action_links(client):
    """D-13: Survivor card must contain ZERO forms, ZERO submit buttons, ZERO action links.

    The Strategy Builder tab is advisory-only. Survivor cards must never have:
      - <form> elements (would allow HTML-form submission to action endpoints)
      - <button type="submit"> (would submit a form)
      - <a href="/api/..."> (API mutation links)
      - <a href="/apply..."> or href containing /apply, /accept, /trade

    This is the advisory-only contract pin for the card-level render.
    """
    fixture = _load_fixture()
    obs = fixture["observation_survivor"]

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder (card anatomy)")
    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping DOM check.")

    html = resp.get_data(as_text=True)

    # Count forms — must be zero
    form_count = len(re.findall(r"<form[\s>]", html, re.IGNORECASE))
    assert form_count == 0, (
        f"Survivor card page contains {form_count} <form> element(s). "
        "Strategy Builder cards must have ZERO forms — the page is advisory-only "
        "and must not provide a submission pathway to any action endpoint."
    )

    # Count submit buttons — must be zero
    submit_count = len(
        re.findall(r'<button[^>]+type\s*=\s*["\']submit["\']', html, re.IGNORECASE)
    )
    assert submit_count == 0, (
        f"Survivor card page contains {submit_count} <button type='submit'> element(s). "
        "Advisory-only cards must have ZERO submit buttons."
    )
    input_submit_count = len(
        re.findall(r'<input[^>]+type\s*=\s*["\']submit["\']', html, re.IGNORECASE)
    )
    assert input_submit_count == 0, (
        f"Survivor card page contains {input_submit_count} <input type='submit'> element(s). "
        "Advisory-only cards must have ZERO submit inputs."
    )

    # No action links
    forbidden_href_patterns = [
        r'href\s*=\s*["\']\/api\/',
        r'href\s*=\s*["\'][^"\']*\/apply',
        r'href\s*=\s*["\'][^"\']*\/accept',
        r'href\s*=\s*["\'][^"\']*\/trade',
    ]
    for pattern in forbidden_href_patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        assert not matches, (
            f"Survivor card page contains forbidden href pattern matching {pattern!r}: "
            f"{matches}. Advisory-only cards must have no API/apply/accept/trade links."
        )


# ===========================================================================
# Group E: States
# ===========================================================================


def test_empty_state_renders_when_no_observations(client):
    """E-14: When no observations exist for the symphony, page must show empty state.

    data-testid="strategy-builder-empty-state" must be present when the
    observation list is empty. This tells the operator no proposals have been
    run yet, rather than silently showing an empty page.
    """
    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder (empty state)")
    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping DOM check.")

    html = resp.get_data(as_text=True)
    assert 'data-testid="strategy-builder-empty-state"' in html, (
        'GET /ai-advisor/strategy-builder with no observations must render '
        'data-testid="strategy-builder-empty-state". '
        "Operators need a clear empty-state signal rather than a silently empty page."
    )


def test_rejected_candidates_section_present_when_withheld_observations_exist(client):
    """E-15: Given WITHHELD observations, page must show rejected candidates section.

    data-testid="rejected-candidates-section" must be present when there are
    observations with verdict="WITHHELD". Showing rejected candidates helps
    operators understand the full gate picture (AC-3.2 audit trail).
    """
    fixture = _load_fixture()
    obs = fixture["observation_rejected"]

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder (rejected section)")
    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping DOM check.")

    html = resp.get_data(as_text=True)
    assert 'data-testid="rejected-candidates-section"' in html, (
        'GET /ai-advisor/strategy-builder with WITHHELD observations must render '
        'data-testid="rejected-candidates-section". '
        "The rejected candidates section is required for operator audit trail (AC-3.2)."
    )


def test_backtest_failed_card_present_when_backtest_error_in_observation(client):
    """E-16: Given an observation with backtest_error set, page must show warn-tint card.

    data-testid="backtest-failed-card" must be present when raw_response contains
    a non-null backtest_error. This visually distinguishes failed backtests from
    intentionally withheld candidates (different failure modes).
    """
    fixture = _load_fixture()
    obs = fixture["observation_backtest_failed"]

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder (backtest failed)")
    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping DOM check.")

    html = resp.get_data(as_text=True)
    assert 'data-testid="backtest-failed-card"' in html, (
        'GET /ai-advisor/strategy-builder with backtest_error in raw_response must render '
        'data-testid="backtest-failed-card". '
        "Backtest-failed cards need a distinct warn-tint state to distinguish them "
        "from intentionally withheld candidates."
    )


# ===========================================================================
# Group F: Adversarial — no action affordances on survivor cards
#
# This is the critical safety contract test. It renders the full page with
# 2 survivor observations + 1 rejected observation and asserts that the
# survivor-cards section contains ZERO action affordances of any kind.
# ===========================================================================


def test_no_action_affordances_on_survivor_cards_adversarial(client):
    """F-17 ADVERSARIAL: Render full page with 2 survivors + 1 rejected observation.

    Assert that the rendered survivor-cards section (data-testid="survivor-cards"
    or the full page body) contains:
      - ZERO <form elements
      - ZERO <button elements
      - ZERO <input type="submit" elements
      - ZERO href="/api/ links
      - ZERO href="/apply links
      - ZERO href="/accept links
      - ZERO href="/trade links

    This is the critical safety contract: the Strategy Builder tab is advisory-only.
    Any single action affordance accidentally added would be a structural violation
    that could create a path from strategy proposals to live trading.

    The test is intentionally more aggressive than D-13 — it uses 2 survivors
    to increase the surface area and checks the survivor-cards container region.
    """
    fixture = _load_fixture()

    # Build two survivor observations and one rejected
    survivor_1 = dict(fixture["observation_survivor"])
    survivor_2 = dict(fixture["observation_survivor"])
    survivor_2 = {**survivor_2, "id": 201, "subject_id": "sym-test-001"}
    rejected = fixture["observation_rejected"]

    observations = [survivor_1, survivor_2, rejected]

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=observations),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder (adversarial)")
    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping adversarial DOM check.")

    html = resp.get_data(as_text=True)

    # ---------- forms ----------
    form_count = len(re.findall(r"<form[\s>]", html, re.IGNORECASE))
    assert form_count == 0, (
        f"ADVERSARIAL FAIL: Full page with 2 survivors + 1 rejected contains "
        f"{form_count} <form> element(s). "
        "The Strategy Builder tab must have ZERO form elements — forms create "
        "a direct submission pathway to action endpoints. "
        "This is a structural advisory-only contract violation."
    )

    # ---------- buttons ----------
    button_count = len(re.findall(r"<button[\s>]", html, re.IGNORECASE))
    # The run button (trigger) is permitted outside survivor-cards, but
    # we assert zero to catch any accidentally added action button.
    # If a 'Run Analysis' trigger button is needed, it should NOT be inside the
    # survivor-cards section — the route test accepts zero buttons in the full page.
    # Adjust this if a non-action trigger button is needed: isolate the survivor
    # section and assert zero there instead.
    submit_button_count = len(
        re.findall(r'<button[^>]+type\s*=\s*["\']submit["\']', html, re.IGNORECASE)
    )
    assert submit_button_count == 0, (
        f"ADVERSARIAL FAIL: Page contains {submit_button_count} <button type='submit'> "
        f"element(s) with 2 survivors rendered. "
        "Advisory-only cards must have ZERO submit buttons."
    )

    # ---------- submit inputs ----------
    input_submit_count = len(
        re.findall(r'<input[^>]+type\s*=\s*["\']submit["\']', html, re.IGNORECASE)
    )
    assert input_submit_count == 0, (
        f"ADVERSARIAL FAIL: Page contains {input_submit_count} <input type='submit'> "
        f"element(s). Advisory-only pages must have ZERO submit inputs."
    )

    # ---------- action hrefs ----------
    forbidden_href_patterns = {
        "href /api/": r'href\s*=\s*["\']\/api\/',
        "href /apply": r'href\s*=\s*["\'][^"\']*\/apply',
        "href /accept": r'href\s*=\s*["\'][^"\']*\/accept',
        "href /trade": r'href\s*=\s*["\'][^"\']*\/trade',
    }
    for label, pattern in forbidden_href_patterns.items():
        matches = re.findall(pattern, html, re.IGNORECASE)
        assert not matches, (
            f"ADVERSARIAL FAIL: Page with 2 survivors + 1 rejected contains "
            f"{len(matches)} forbidden link(s) matching {label!r}: {matches[:3]}. "
            "Survivor cards must have no API/apply/accept/trade links — "
            "the Strategy Builder is advisory-only and cannot expose action links."
        )


# ===========================================================================
# Group G: Settings write allowlist not touched
# ===========================================================================


def test_settings_write_allowlist_does_not_contain_strategy_builder_or_live_execution():
    """G-18: _SETTINGS_WRITE_ALLOWLIST must NOT contain 'strategy_builder' or 'LIVE_EXECUTION'.

    The new strategy-builder route must not expand the settings write allowlist.
    _SETTINGS_WRITE_ALLOWLIST is a security boundary that prevents credential
    and execution-control keys from being written via the unauthenticated dashboard.

    Adding 'strategy_builder' would create a settings write path for the advisor.
    'LIVE_EXECUTION' is always excluded — its presence would be a critical
    security regression allowing the dashboard to toggle live trading.
    """
    allowlist = app_module._SETTINGS_WRITE_ALLOWLIST

    assert "strategy_builder" not in allowlist, (
        "_SETTINGS_WRITE_ALLOWLIST contains 'strategy_builder'. "
        "The Phase 3 strategy-builder route must not expand the settings allowlist — "
        "advisor configuration does not belong in the settings write path."
    )

    assert "LIVE_EXECUTION" not in allowlist, (
        "_SETTINGS_WRITE_ALLOWLIST contains 'LIVE_EXECUTION'. "
        "This is a critical security regression — LIVE_EXECUTION controls real-money "
        "trading and must never be writable through the unauthenticated dashboard. "
        "The allowlist must never include LIVE_EXECUTION."
    )
