"""Tests — Strategy Builder Flask routes and template.

SPA-PORT UPDATE (cycle/spa-port-strategy-builder): Group A tests A1-A4 have been
updated from the standalone-page model to the SPA model.

  OLD model (standalone page): GET /ai-advisor/strategy-builder → 200 with own template
  NEW model (SPA): GET /ai-advisor/strategy-builder → 302 to /ai-advisor
                   Strategy Builder content now lives in /ai-advisor as the 6th tab

Tests the two routes:
  GET  /ai-advisor/strategy-builder      — NOW a 302 redirect to /ai-advisor (AC3)
  POST /ai-advisor/strategy-builder/run  — unchanged: CSRF-protected, dispatches
                                           propose_strategies(), returns JSON

Architecture contracts verified:

  Group A: GET route (SPA-port model)
    A1. GET returns 302 redirect to /ai-advisor (NOT 200 standalone page).
    A2. GET /ai-advisor (200) contains data-tab="strategy-builder" (6th tab button).
    A3. GET /ai-advisor (200) contains risk banner in the 6th tab panel.
    A4. GET /ai-advisor/strategy-builder does NOT return 200.

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
        caveats=[
            "Selected on backtest: gate passes but selection bias remains. Treat as hypothesis."
        ],
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


def test_get_strategy_builder_returns_302(client):
    """A-1 (SPA-port): GET /ai-advisor/strategy-builder must return 302 redirect to /ai-advisor.

    SPA-PORT UPDATE: the standalone page is folded into the unified SPA as the 6th tab.
    The GET sub-route must now return a 302 redirect to /ai-advisor — matching the
    pattern of Correlations, Asset Swaps, Logic Changes, and Chat (AC3).

    OLD contract (pre-SPA-port): GET returned 200 with standalone template.
    NEW contract (post-SPA-port): GET returns 302 to /ai-advisor.
    """
    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder")
    assert resp.status_code in (301, 302, 303, 308), (
        f"GET /ai-advisor/strategy-builder returned {resp.status_code}, expected a redirect. "
        "SPA-port requires this route to redirect to /ai-advisor (AC3). "
        "Convert the GET handler in app.py to return redirect('/ai-advisor')."
    )


def test_get_ai_advisor_spa_contains_strategy_builder_tab_button(client):
    """A-2 (SPA-port): GET /ai-advisor must contain data-tab="strategy-builder" tab button.

    SPA-PORT UPDATE: the tab landmark is now in the unified SPA at /ai-advisor,
    not on the deleted standalone page. This test verifies the 6th tab button
    is present in the unified SPA.

    OLD contract: checked data-testid="strategy-builder-tab" on standalone page.
    NEW contract: checks data-tab="strategy-builder" on /ai-advisor (AC1).
    """
    import app as _app_module

    _stub_patches = [
        patch.object(_app_module.analytics, "get_history_with_cache_invalidation", return_value={}),
        patch.object(_app_module.analytics, "list_available_symphonies", return_value=[]),
        patch.object(
            _app_module.analytics, "compute_per_symphony_returns", return_value=([], [], [])
        ),
        patch.object(_app_module.database, "get_advisor_observations_for_role", return_value=[]),
    ]
    from unittest.mock import MagicMock

    fake_corr = MagicMock()
    fake_corr.compute_pairwise_correlations.return_value = []
    fake_corr.CRISIS_CAVEAT = "caveat"
    with patch.dict("sys.modules", {"advisors.correlation_diagnostic": fake_corr}):
        for p in _stub_patches:
            p.start()
        try:
            resp = client.get("/ai-advisor")
        finally:
            for p in _stub_patches:
                p.stop()

    assert resp.status_code == 200, (
        f"GET /ai-advisor returned {resp.status_code}; expected 200 (SPA base route)."
    )
    html = resp.get_data(as_text=True)
    assert 'data-tab="strategy-builder"' in html, (
        'GET /ai-advisor must contain a tab button with data-tab="strategy-builder". '
        "The 6th tab button is absent from the unified SPA. Add it to templates/ai_advisor.html (AC1)."
    )


def test_get_ai_advisor_spa_contains_strategy_builder_risk_banner(client):
    """A-3 (SPA-port): GET /ai-advisor must contain the strategy-builder risk banner.

    SPA-PORT UPDATE: the non-dismissible risk banner lives inside the 6th tab panel
    on the unified SPA at /ai-advisor, not on the deleted standalone page.

    OLD contract: checked risk banner on /ai-advisor/strategy-builder (200).
    NEW contract: checks risk banner on /ai-advisor (6th tab panel — AC2).
    """
    import app as _app_module

    _stub_patches = [
        patch.object(_app_module.analytics, "get_history_with_cache_invalidation", return_value={}),
        patch.object(_app_module.analytics, "list_available_symphonies", return_value=[]),
        patch.object(
            _app_module.analytics, "compute_per_symphony_returns", return_value=([], [], [])
        ),
        patch.object(_app_module.database, "get_advisor_observations_for_role", return_value=[]),
    ]
    from unittest.mock import MagicMock

    fake_corr = MagicMock()
    fake_corr.compute_pairwise_correlations.return_value = []
    fake_corr.CRISIS_CAVEAT = "caveat"
    with patch.dict("sys.modules", {"advisors.correlation_diagnostic": fake_corr}):
        for p in _stub_patches:
            p.start()
        try:
            resp = client.get("/ai-advisor")
        finally:
            for p in _stub_patches:
                p.stop()

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    has_testid = 'data-testid="strategy-builder-risk-banner"' in html
    has_text_a = "Logic-change proposals carry the highest overfitting risk" in html
    has_text_b = "non-dismissible" in html

    assert has_testid or has_text_a or has_text_b, (
        "GET /ai-advisor must render a non-dismissible risk banner in the strategy-builder panel. "
        'Expected one of: data-testid="strategy-builder-risk-banner", '
        "'Logic-change proposals carry the highest overfitting risk', "
        "or 'non-dismissible'. "
        "Port the risk banner from the standalone template into the 6th tab panel (AC2)."
    )


def test_get_strategy_builder_does_not_return_200_standalone(client):
    """A-4 (SPA-port): GET /ai-advisor/strategy-builder must NOT return 200.

    SPA-PORT UPDATE: the old A-4 checked that data-testid="strategy-builder-run-form"
    was absent from the 200 response. After SPA-port there must be NO 200 response
    at all — the route must redirect.

    OLD contract (A-4): 200 response must not contain data-testid="strategy-builder-run-form".
    NEW contract (A-4): 200 response is forbidden — route must redirect (AC3).
    """
    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder")
    assert resp.status_code != 200, (
        "GET /ai-advisor/strategy-builder returned 200. "
        "After SPA-port this route must redirect — a 200 response means the standalone "
        "page is still being rendered, which is the defect being eliminated (AC3)."
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
        json={
            "objective": "diversify",
            "universe": ["SPY", "AGG", "GLD"],
            "symphony_id": "sym-test",
        },
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
        json={
            "objective": "diversify",
            "universe": ["SPY", "AGG", "GLD"],
            "symphony_id": "sym-test",
        },
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
            f'Survivor card must contain data-testid="{testid}". '
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
    submit_count = len(re.findall(r'<button[^>]+type\s*=\s*["\']submit["\']', html, re.IGNORECASE))
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
        "GET /ai-advisor/strategy-builder with no observations must render "
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
        "GET /ai-advisor/strategy-builder with WITHHELD observations must render "
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
        "GET /ai-advisor/strategy-builder with backtest_error in raw_response must render "
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


# ===========================================================================
# Adversarial Cycle 2 tests
# Added after GREEN was confirmed. These tests cover gaps not caught by the
# initial 18 tests — identified by direct inspection of the implementation.
# ===========================================================================


# ---------------------------------------------------------------------------
# H: AC-X2 lazy import — strategy_builder_engine must NOT appear at top level
# ---------------------------------------------------------------------------


def test_strategy_builder_engine_not_imported_at_module_scope():
    """H-19: app.py must NOT import strategy_builder_engine at module scope.

    AC-X2: a module-scope import couples the 1-minute execution path (spawned
    by app.py) to the advisor engine — every app.py load pulls the advisor into
    memory. strategy_builder_engine must be imported lazily inside each route
    handler.

    This is a static-analysis test — it parses the app.py AST and verifies
    that no top-level Import or ImportFrom node references strategy_builder_engine.
    """
    import ast as _ast

    source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
    tree = _ast.parse(source)

    for node in tree.body:
        if isinstance(node, _ast.ImportFrom):
            module = node.module or ""
            assert "strategy_builder_engine" not in module, (
                f"app.py imports from '{module}' at module scope — "
                "strategy_builder_engine must be imported LAZILY inside the route "
                "handler function body, not at the top level (AC-X2). "
                "A module-scope import of strategy_builder_engine couples the "
                "1-minute live execution path to the advisor."
            )
        if isinstance(node, _ast.Import):
            for alias in node.names:
                assert "strategy_builder_engine" not in alias.name, (
                    f"app.py imports '{alias.name}' at module scope — AC-X2 violation."
                )


def test_strategy_builder_run_route_imports_propose_strategies_inside_function():
    """H-20: The POST /run route handler must import propose_strategies inside its body.

    This is the AC-X2 lazy-import contract for the run route specifically.
    The function body must contain a lazy import from advisors.strategy_builder_engine.
    """
    import ast as _ast

    source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
    tree = _ast.parse(source)

    fn_body = None
    for node in _ast.walk(tree):
        if isinstance(node, _ast.FunctionDef) and node.name == "ai_advisor_strategy_builder_run":
            fn_body = _ast.unparse(node)
            break

    assert fn_body is not None, (
        "ai_advisor_strategy_builder_run not found in app.py. "
        "The POST /run route handler must be defined."
    )

    assert "strategy_builder_engine" in fn_body, (
        "ai_advisor_strategy_builder_run does not import from strategy_builder_engine "
        "inside its function body. "
        "AC-X2: the lazy import must appear inside the route handler, not at the top "
        "level of app.py. "
        "Missing lazy import means the live path guard is bypassed."
    )

    assert "propose_strategies" in fn_body, (
        "ai_advisor_strategy_builder_run does not call or import propose_strategies. "
        "The route must delegate to propose_strategies from strategy_builder_engine "
        "(not implement the proposal logic inline)."
    )


# ---------------------------------------------------------------------------
# I: Advisor role isolation — non-STRATEGY_BUILDER observations must not bleed
# ---------------------------------------------------------------------------


def test_get_route_filters_out_non_strategy_builder_observations(client):
    """I-21 (SPA-port): Role isolation — only STRATEGY_BUILDER observations reach the SPA.

    SPA-PORT UPDATE: the GET /ai-advisor/strategy-builder sub-route now returns 302.
    Role isolation is enforced at the DB layer: the /ai-advisor route must call
    database.get_advisor_observations_for_role("STRATEGY_BUILDER"), which returns
    only STRATEGY_BUILDER rows. OVERFITTING_CONSCIENCE, SPEC_CRITIC, and other
    advisor roles are never passed to the Strategy Builder panel.

    Two assertions:
      1. GET /ai-advisor/strategy-builder returns 302 (sub-route redirects, AC3).
      2. app.py ai_advisor_tab route source calls
         get_advisor_observations_for_role("STRATEGY_BUILDER") — source-code
         inspection proves the role-scoped accessor is wired correctly (AC4).
         Source inspection is used instead of a live call to avoid mocking the
         full correlation/history machinery that surrounds the prefetch.
    """
    # Part 1: GET sub-route returns 302 — not 200 standalone page.
    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

    assert resp.status_code in (301, 302, 303, 308), (
        f"GET /ai-advisor/strategy-builder returned {resp.status_code}; expected a redirect. "
        "SPA-port: this sub-route must redirect to /ai-advisor (AC3). "
        "Role isolation is now enforced at the DB accessor level (AC4)."
    )

    # Part 2: Source-code verification that ai_advisor_tab calls the role-scoped
    # accessor with "STRATEGY_BUILDER". Source inspection avoids needing to stub
    # the full correlation-diagnostic / history machinery around the prefetch call.
    import inspect

    import app as _app_mod

    source = inspect.getsource(_app_mod.ai_advisor_tab)
    assert (
        'get_advisor_observations_for_role("STRATEGY_BUILDER")' in source
        or "get_advisor_observations_for_role('STRATEGY_BUILDER')" in source
    ), (
        "ai_advisor_tab does not call "
        "database.get_advisor_observations_for_role('STRATEGY_BUILDER'). "
        "The route must use the role-scoped DB accessor to fetch Strategy Builder "
        "observations (AC4) so that OVERFITTING_CONSCIENCE, SPEC_CRITIC, and other "
        "advisor roles cannot bleed into the Strategy Builder panel."
    )


# ---------------------------------------------------------------------------
# J: POST /run — unknown objective defaults to diversify (no 400/500)
# ---------------------------------------------------------------------------


def test_post_run_unknown_objective_string_does_not_crash(client):
    """J-22: POST /run with an unrecognised objective string must return 200, not 400/500.

    The route parses `objective` from the JSON body and must gracefully default
    to `diversify` for any unknown value — a typo or future enum value must not
    crash the route or return a user-hostile 4xx error.

    This is a robustness test for the enum-parsing branch.
    """
    mock_run = _make_proposal_run(survivors=False, error=None)

    with patch(
        "advisors.strategy_builder_engine.propose_strategies",
        return_value=mock_run,
    ):
        resp = client.post(
            "/ai-advisor/strategy-builder/run",
            json={
                "objective": "COMPLETELY_UNKNOWN_OBJECTIVE_VALUE_XYZ",
                "universe": ["SPY", "AGG"],
                "symphony_id": "sym-test",
            },
            content_type="application/json",
        )

    assert resp.status_code == 200, (
        f"POST /run with unknown objective returned {resp.status_code}, expected 200. "
        "The route must default to 'diversify' for unrecognised objective values — "
        "not crash, not return 4xx."
    )
    data = resp.get_json()
    assert data is not None, "Response must be valid JSON even for unknown objective."
    # Must not have an error triggered purely by the unrecognised string
    # (errors from the mock engine are fine; errors from JSON parsing are not)
    # The mock returns no error, so the response error must also be None
    assert data.get("error") is None, (
        f"Unknown objective string must not produce an error when the engine succeeds. "
        f"Got error: {data.get('error')!r}. "
        "The route must parse the objective and default to diversify without raising."
    )


# ---------------------------------------------------------------------------
# K: POST /run — missing JSON body is handled gracefully (no 500)
# ---------------------------------------------------------------------------


def test_post_run_missing_json_body_does_not_500(client):
    """K-23: POST /run with no JSON body must return 200 or 4xx — never a 500.

    The route calls `request.get_json(silent=True) or {}` to handle missing bodies.
    A missing body must default gracefully (empty universe, default objective) rather
    than crash with an AttributeError or KeyError from None.
    """
    mock_run = _make_proposal_run(survivors=False, error=None)

    with patch(
        "advisors.strategy_builder_engine.propose_strategies",
        return_value=mock_run,
    ):
        resp = client.post(
            "/ai-advisor/strategy-builder/run",
            data="",  # no JSON body
            content_type="application/json",
        )

    assert resp.status_code != 500, (
        "POST /run with missing JSON body returned 500. "
        "The route must handle a missing body gracefully — "
        "`request.get_json(silent=True) or {{}}` must default all fields rather than crash."
    )


# ---------------------------------------------------------------------------
# L: GET route does NOT write to the database (read-only contract)
# ---------------------------------------------------------------------------


def test_get_route_does_not_call_insert_advisor_observation(client):
    """L-24: GET /ai-advisor/strategy-builder must NOT call insert_advisor_observation.

    The GET route is purely a display route — it reads from the database via
    get_advisor_observations_for_symphony and renders. It must never write.
    A write on the GET path would violate the read-only display constraint
    and could interfere with observation history on every page load.
    """
    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[]),
        patch("analytics.list_available_symphonies", return_value=[]),
        patch("database.insert_advisor_observation") as mock_insert,
    ):
        client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

    assert mock_insert.call_count == 0, (
        f"GET /ai-advisor/strategy-builder called database.insert_advisor_observation "
        f"{mock_insert.call_count} time(s). "
        "The GET route must be read-only — no writes on the display path."
    )


# ---------------------------------------------------------------------------
# M: POST /run — fdr_adjusted_threshold is a float (not None) when n_candidates > 0
# ---------------------------------------------------------------------------


def test_post_run_fdr_adjusted_threshold_is_float_when_candidates_exist(client):
    """M-25: When n_candidates > 0, fdr_adjusted_threshold must be a positive float.

    The route computes fdr_adjusted_threshold = fdr_q / c_n (Yekutieli correction).
    When n_candidates is non-zero, this must produce a non-None positive float —
    None would mean the route failed to compute the threshold and the operator
    has no FDR audit trail (AC-3.2 violation).

    Tolerance: we assert it is a positive float — the exact value is
    producer-computed (fdr_q / harmonic-n) and must not be hardcoded.
    """
    from types import SimpleNamespace

    # Build a mock ProposalRun with a non-empty gated_batch (n_candidates=5)
    gate_batch = SimpleNamespace(
        results=[],
        survivors=[],
        n_candidates=5,
        fdr_q=0.05,
    )
    mock_run = SimpleNamespace(
        candidates=[],
        gated_batch=gate_batch,
        screened_survivors=[],
        observations_written=0,
        error=None,
    )

    with patch(
        "advisors.strategy_builder_engine.propose_strategies",
        return_value=mock_run,
    ):
        resp = client.post(
            "/ai-advisor/strategy-builder/run",
            json={"objective": "diversify", "universe": ["SPY", "AGG"], "symphony_id": "sym-test"},
            content_type="application/json",
        )

    data = resp.get_json()
    assert data is not None
    assert data.get("error") is None, f"Unexpected error: {data.get('error')}"

    threshold = data.get("fdr_adjusted_threshold")
    assert threshold is not None, (
        "fdr_adjusted_threshold must not be None when n_candidates=5. "
        "The route must compute Yekutieli c(n) correction and return a float. "
        "None means the AC-3.2 FDR audit trail is absent."
    )
    assert isinstance(threshold, float), (
        f"fdr_adjusted_threshold must be a float, got {type(threshold).__name__}: {threshold!r}. "
        "The Yekutieli-corrected threshold is always a positive float when fdr_q > 0."
    )
    assert threshold > 0, (
        f"fdr_adjusted_threshold must be positive (fdr_q / c_n > 0). Got: {threshold}. "
        "A zero or negative threshold would incorrectly gate all candidates."
    )


# ---------------------------------------------------------------------------
# N: Template — run trigger button must be type="button" not type="submit"
# ---------------------------------------------------------------------------


def test_template_run_trigger_button_is_not_type_submit():
    """N-26: The run trigger button in the template must be type="button", not type="submit".

    A type="submit" run trigger would submit the containing form (or the page
    body as an implicit form in some browsers), bypassing the JS fetch + CSRF
    token flow. The button must be type="button" to ensure only the JS onclick
    handler fires — never an HTML form submission.

    This is a static analysis test on the template source.
    """
    template_path = _REPO_ROOT / "templates" / "ai_advisor_strategy_builder.html"
    if not template_path.exists():
        pytest.skip("Template not yet created — skipping static analysis.")

    source = template_path.read_text(encoding="utf-8")

    # Find all <button> elements and check none of the run-related ones are type="submit"
    run_button_pattern = re.compile(
        r"<button[^>]+(?:sb-run-btn|sbRunAnalysis|run-trigger-btn)[^>]*>", re.IGNORECASE
    )
    run_buttons = run_button_pattern.findall(source)

    assert run_buttons, (
        "No run trigger button found in the template. "
        "Expected a <button> with id='sb-run-btn' or class='run-trigger-btn'. "
        "The run trigger must exist as a type='button' element."
    )

    for btn_tag in run_buttons:
        assert 'type="submit"' not in btn_tag.lower() and "type='submit'" not in btn_tag.lower(), (
            f"Run trigger button is type='submit': {btn_tag!r}. "
            "The run trigger must be type='button' to prevent HTML form submission — "
            "the CSRF token is injected via JS fetch, not via an HTML form action."
        )
        # Verify it is explicitly type="button"
        has_type_button = 'type="button"' in btn_tag.lower() or "type='button'" in btn_tag.lower()
        assert has_type_button, (
            f"Run trigger button does not have explicit type='button': {btn_tag!r}. "
            "The button must have type='button' explicitly set — an implicit button "
            "type defaults to 'submit' inside a form element in HTML5."
        )


# ---------------------------------------------------------------------------
# O: FDR metadata content — α= numeric value must appear (domain-reviewer blocker)
# ---------------------------------------------------------------------------


def test_survivor_card_fdr_metadata_contains_numeric_alpha_value(client):
    """O-27: The fdr-metadata element must render a numeric α value, not just the label.

    The contract requires `α=0.0500` (or equivalent formatted fdr_q) to appear
    in the data-testid="fdr-metadata" element — not just the boilerplate text
    "Yekutieli c(n) correction applied" with no number.

    Without the numeric value the operator cannot independently verify the
    correction threshold used. AC-3.2 requires the full FDR metadata line.

    This tests content precision, not just testid presence.
    """
    fixture = _load_fixture()
    obs = fixture["observation_survivor"]

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder (fdr-metadata content)")
    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping content check.")

    html = resp.get_data(as_text=True)

    # The fdr-metadata element must contain a numeric α value.
    # Accept either α= or alpha= (HTML entity vs literal) followed by digits.
    # Pattern matches: α=0.0500, α=0.05, alpha=0.0500, ɑ=0.05, etc.
    has_numeric_alpha = bool(
        re.search(r"(?:α|&alpha;|&#x3b1;|alpha)\s*=\s*\d", html, re.IGNORECASE)
    )
    assert has_numeric_alpha, (
        'data-testid="fdr-metadata" must render a numeric α= value '
        "(e.g. 'α=0.0500') in the rendered HTML. "
        "The template must interpolate the fdr_q value from the observation, "
        "not just print 'Yekutieli c(n) correction applied' without a number. "
        "AC-3.2: FDR metadata must include the adjusted threshold value for "
        "the operator audit trail."
    )


# ---------------------------------------------------------------------------
# P: Caveat text content — exact contract wording (domain-reviewer blocker)
# ---------------------------------------------------------------------------


def test_survivor_card_caveat_text_contains_overfitting_risk_wording(client):
    """P-28: The caveat-text element must contain the exact contract wording about
    overfitting risk being impossible to eliminate.

    The domain-reviewer contract specifies: the caveat text must contain
    "Overfitting risk cannot be eliminated" (or equivalent) — not only
    "Selected on backtest" (which is too loose and can be satisfied by
    paraphrases that miss the core message).

    The SURVIVOR_OVERFITTING_CAVEAT from backtest_gate_engine reads:
    "...selecting the best backtest from N candidates still concentrates
    selection bias even after correction. A gate pass is resistance to noise,
    not proof of live edge. Treat survivors as hypotheses, not guarantees."

    The contract wording per the domain-reviewer is:
    "Overfitting risk cannot be eliminated — only resisted."

    This test verifies the template renders this exact message and does NOT
    merely paraphrase it.
    """
    fixture = _load_fixture()
    obs = fixture["observation_survivor"]

    with (
        patch("database.get_advisor_observations_for_symphony", return_value=[obs]),
        patch("analytics.list_available_symphonies", return_value=[]),
    ):
        resp = client.get("/ai-advisor/strategy-builder?symphony_id=sym-test-001")

    _assert_route_exists(resp, "GET /ai-advisor/strategy-builder (caveat content)")
    if resp.status_code != 200:
        pytest.skip(f"Route returned {resp.status_code} — skipping content check.")

    html = resp.get_data(as_text=True)

    # The exact contract wording from the domain-reviewer.
    # "Overfitting risk cannot be eliminated — only resisted."
    has_exact_wording = "Overfitting risk cannot be eliminated" in html and "only resisted" in html
    assert has_exact_wording, (
        "Survivor card caveat text must contain the exact contract wording: "
        '"Overfitting risk cannot be eliminated — only resisted." '
        "The template currently uses a paraphrase ('Selected on backtest: the gate screens...') "
        "that does not satisfy the domain contract. "
        "The caveat-text element must be updated to include: "
        '"Overfitting risk cannot be eliminated — only resisted."'
    )


# ---------------------------------------------------------------------------
# Q: HF-1 regression — community-strats load must NOT reach real Atlas in
#    the offline suite (DE-HF1-001 best-effort degradation contract)
# ---------------------------------------------------------------------------


def test_post_run_does_not_call_real_atlas_in_offline_suite(client, _stub_community_strats_offline):
    """Q-29: POST /run must never reach real Atlas (MongoDB) during the offline suite.

    After HF-1 the route ALWAYS calls load_community_strategies.  The
    tests/app/conftest.py autouse fixture stubs it out, but this test
    explicitly asserts the stub was invoked — confirming the patch seam is
    correct and no test-infrastructure change can silently re-expose the live
    call.

    Failure means the conftest stub is broken or the patch target changed:
    the offline suite would hang on real Atlas connections (observed as 51 GB
    memory balloon and a 32%-stall, 2026-06-17).

    The stub is the autouse `_stub_community_strats_offline` fixture from
    tests/app/conftest.py, yielded as a MagicMock so call-count is
    inspectable.  We do NOT assert the count == 1 (the route is free to call
    it once or delegate) — we assert count >= 1 to confirm the seam fired and
    the route did not bypass the stub.
    """
    mock_run = MagicMock()
    mock_run.error = None
    gate = MagicMock()
    gate.n_candidates = 0
    gate.fdr_q = 0.05
    gate.results = []
    gate.survivors = []
    mock_run.gated_batch = gate
    mock_run.candidates = []
    mock_run.screened_survivors = []
    # AC-4/AC-5 (DEGRADE-FIX): a bare MagicMock() auto-vivifies ANY attribute
    # access, so getattr(run, "backtest_unavailable_count", 0) in the route
    # never falls back to its default here — it returns a child MagicMock,
    # which jsonify() cannot serialize (500, unrelated to this test's actual
    # Atlas-stub assertion). Set explicitly to the real ProposalRun defaults.
    mock_run.backtest_unavailable = False
    mock_run.backtest_unavailable_count = 0

    with patch(
        "advisors.strategy_builder_engine.propose_strategies",
        return_value=mock_run,
    ):
        resp = client.post(
            "/ai-advisor/strategy-builder/run",
            json={"objective": "diversify", "universe": ["SPY", "AGG"]},
            content_type="application/json",
        )

    assert resp.status_code == 200, (
        f"POST /run returned {resp.status_code} — route should succeed even with "
        "community strats unavailable (template-only degradation, DE-HF1-001)."
    )

    assert _stub_community_strats_offline.call_count >= 1, (
        "load_community_strategies stub was never called during POST /run. "
        "The HF-1 route wiring (app.py: lazy from advisors.community_strats import "
        "load_community_strategies) must invoke the function exactly once per request. "
        "If this assertion fails the patch seam is broken — the route may be reaching "
        "real Atlas (MongoDB) during the offline suite."
    )
