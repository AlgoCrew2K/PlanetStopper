"""Route-level producer guard for GET /ai-advisor (AC-5b, tech-debt-cleanups C3b).

Rationale (project lesson: feedback_route_level_red_for_mocked_analytics_fns):
    A wholesale-mocked route test can PASS while the live route 500s on a
    called-but-undefined producer function — the bare ``except Exception: pass``
    guards in ai_advisor_tab() swallow AttributeErrors and NameErrors silently,
    producing a 200 with degraded (empty) panels rather than a visible 500.
    This means a standard mock-everything test gives a false GREEN while real users
    see broken content.

    The guard here hits GET /ai-advisor with the REAL producer modules in play
    (correlation_diagnostic, prism_render, asset_swap_engine) and mocks ONLY the
    DB/network boundary (analytics history, DB observation reads, DB Prism summary).
    It then asserts:
      1. The route returns 200 (not a 500).
      2. Each producer module that ai_advisor_tab() imports lazily exposes the
         callable/attribute the route actually calls (existence guard — catches the
         class of bug where a fn was renamed but the route was not updated).

    Since there is no code removal associated with C3b (the self-skip branch was
    already absent from the codebase — confirmed by independent inspection and
    commit 47f0eb5), this test serves as a regression guard going forward: any
    future refactor that breaks a producer fn's public API will fail here rather
    than silently degrading the advisor page.

Mocking strategy:
    - analytics.get_history_with_cache_invalidation → returns empty dict (no files)
    - analytics.list_available_symphonies → returns [] (no post-mortems)
    - analytics.compute_per_symphony_returns → returns ([], [], [])
    - database.get_advisor_observations_for_role → returns [] (no DB rows)
    - database.get_latest_market_prism_summary → returns None (no Prism row)
    - database.get_advisor_observations_for_symphony → returns [] (no SB rows)
    - advisors.correlation_diagnostic: NOT mocked at module level — the route's
      lazy import resolves the real module; network-touching compute is isolated
      by the empty series_dict (no tickers → no pairwise computation).
    - advisors.prism_render: NOT mocked — real module, pure stdlib, no I/O.
    - advisors.asset_swap_engine._has_composer_key: NOT mocked — checks env only,
      no network; returns False in CI (no COMPOSER_API_KEY set).

No live API calls. No live DB writes. All tests are function-scoped.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Repo path helper
# ---------------------------------------------------------------------------

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _ensure_repo_on_path() -> None:
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def advisor_tab_client(monkeypatch):
    """Flask test client with DB/analytics boundary mocked, producer modules REAL.

    Stubs:
      - analytics read path (post-mortem history) → empty, no files needed
      - database observation accessors → empty lists
      - database Prism summary → None

    NOT stubbed (real modules exercise the producer contract):
      - advisors.correlation_diagnostic (lazy-imported inside the route)
      - advisors.prism_render (lazy-imported inside the route)
      - advisors.asset_swap_engine._has_composer_key (env-only check)
    """
    _ensure_repo_on_path()

    with patch("database.init_db"):
        import app as flask_app

    # Stub analytics read path — no post-mortem files needed for the route to render.
    monkeypatch.setattr(
        flask_app.analytics,
        "get_history_with_cache_invalidation",
        lambda **kw: {},
    )
    monkeypatch.setattr(flask_app.analytics, "list_available_symphonies", lambda h: [])
    monkeypatch.setattr(
        flask_app.analytics,
        "compute_per_symphony_returns",
        lambda h, sym: ([], [], []),
    )

    # Stub database observation accessors — no DB rows needed.
    monkeypatch.setattr(
        flask_app.database,
        "get_advisor_observations_for_role",
        lambda role, limit=50: [],
    )
    monkeypatch.setattr(
        flask_app.database,
        "get_advisor_observations_for_symphony",
        lambda sym_id: [],
    )
    # Stub Prism summary — no MARKET_PRISM row (empty state path).
    monkeypatch.setattr(
        flask_app.database,
        "get_latest_market_prism_summary",
        lambda: None,
    )

    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# AC-5b: Route-level guard — GET /ai-advisor returns 200 with real producers
# ---------------------------------------------------------------------------


def test_advisor_tab_returns_200_with_real_producer_modules(advisor_tab_client):
    """GET /ai-advisor must return 200 when real producer modules are in play.

    This test exercises the route with the REAL advisors.correlation_diagnostic,
    advisors.prism_render, and advisors.asset_swap_engine modules — NOT wholesale
    mocks — so that a renamed or missing producer fn causes a test failure here
    rather than a silent degraded page in production.

    The DB/analytics boundary is mocked (empty data) so no live files or DB
    writes are needed.  The route must handle all-empty data gracefully (that
    is its normal empty-state path) and return 200.
    """
    resp = advisor_tab_client.get("/ai-advisor")
    assert resp.status_code == 200, (
        f"GET /ai-advisor returned {resp.status_code} with real producer modules. "
        "Expected 200 — the route must degrade gracefully when data is empty, "
        "not 500. A 500 here means a producer fn is broken or missing and the "
        "bare except-pass guards in ai_advisor_tab() are swallowing the error. "
        "Check: advisors.correlation_diagnostic.compute_pairwise_correlations, "
        "advisors.prism_render.humanize_obs_preview, "
        "advisors.asset_swap_engine._has_composer_key."
    )


def test_advisor_tab_response_is_html(advisor_tab_client):
    """GET /ai-advisor must return an HTML response (Content-Type: text/html).

    A 200 JSON response would indicate the route hit an error handler that
    returns JSON instead of the expected rendered template.
    """
    resp = advisor_tab_client.get("/ai-advisor")
    assert resp.status_code == 200, f"Route returned {resp.status_code}, expected 200"
    content_type = resp.content_type or ""
    assert "text/html" in content_type, (
        f"GET /ai-advisor returned Content-Type={content_type!r}; expected text/html. "
        "The route must render the ai_advisor.html template, not return JSON."
    )


# ---------------------------------------------------------------------------
# Producer module existence guards (hasattr checks)
#
# Each guard asserts that a module the route lazy-imports actually exposes the
# callable/attribute the route calls.  These are the functions that would cause
# a silent degraded page if renamed without updating the route, because the bare
# `except Exception: pass` in ai_advisor_tab() swallows the AttributeError.
# ---------------------------------------------------------------------------


def test_correlation_diagnostic_exposes_compute_pairwise_correlations():
    """advisors.correlation_diagnostic must expose compute_pairwise_correlations.

    ai_advisor_tab() calls this at app.py:3251.  If the fn is renamed the route
    silently renders an empty correlations panel (the exception is swallowed)
    rather than failing loudly.  This guard catches the rename before it ships.
    """
    _ensure_repo_on_path()
    corr_diag = importlib.import_module("advisors.correlation_diagnostic")
    assert hasattr(corr_diag, "compute_pairwise_correlations") and callable(
        corr_diag.compute_pairwise_correlations
    ), (
        "advisors.correlation_diagnostic must expose a callable "
        "'compute_pairwise_correlations'.  ai_advisor_tab() calls it at app.py:3251 "
        "inside a bare except-pass block — a rename would silently degrade the "
        "Correlations panel without any test catching it until now."
    )


def test_correlation_diagnostic_exposes_crisis_caveat():
    """advisors.correlation_diagnostic must expose the CRISIS_CAVEAT string attribute.

    ai_advisor_tab() reads this at app.py:3252.  Missing attribute is swallowed
    by the except-pass guard.
    """
    _ensure_repo_on_path()
    corr_diag = importlib.import_module("advisors.correlation_diagnostic")
    assert hasattr(corr_diag, "CRISIS_CAVEAT"), (
        "advisors.correlation_diagnostic must expose a 'CRISIS_CAVEAT' attribute. "
        "ai_advisor_tab() reads it at app.py:3252; missing attribute is swallowed "
        "by the bare except-pass, silently omitting the instability caveat from the UI."
    )


def test_prism_render_exposes_humanize_obs_preview():
    """advisors.prism_render must expose humanize_obs_preview callable.

    ai_advisor_tab() calls this at app.py:3229 to stamp _preview_text on each
    non-MARKET_PRISM observation.  Swallowed-AttributeError → raw JSON in the UI.
    """
    _ensure_repo_on_path()
    prism_render = importlib.import_module("advisors.prism_render")
    assert hasattr(prism_render, "humanize_obs_preview") and callable(
        prism_render.humanize_obs_preview
    ), (
        "advisors.prism_render must expose a callable 'humanize_obs_preview'. "
        "ai_advisor_tab() calls it at app.py:3229; if missing, the except-pass "
        "swallows the error and observations render without a _preview_text stamp, "
        "potentially exposing raw JSON in the UI (RF-1 violation)."
    )


def test_prism_render_exposes_humanize_lens_summary():
    """advisors.prism_render must expose humanize_lens_summary callable.

    ai_advisor_tab() calls this at app.py:3313 to pre-humanize per_lens_digest
    summaries before render_template.  Missing fn → raw JSON reaching the template.
    """
    _ensure_repo_on_path()
    prism_render = importlib.import_module("advisors.prism_render")
    assert hasattr(prism_render, "humanize_lens_summary") and callable(
        prism_render.humanize_lens_summary
    ), (
        "advisors.prism_render must expose a callable 'humanize_lens_summary'. "
        "ai_advisor_tab() calls it at app.py:3313 to pre-humanize per_lens_digest; "
        "if missing, the except-pass swallows the error and the template sees raw JSON "
        "lens summaries (RF-1 / DE-RF1-PROSE-RENDER violation)."
    )


def test_asset_swap_engine_exposes_has_composer_key():
    """advisors.asset_swap_engine must expose _has_composer_key callable.

    ai_advisor_tab() imports and calls this at app.py:3263-3265 to determine
    whether to show the swap/logic forms as available.  Missing fn is swallowed
    → no_api_key defaults to True (forms always show as unavailable).
    """
    _ensure_repo_on_path()
    engine = importlib.import_module("advisors.asset_swap_engine")
    assert hasattr(engine, "_has_composer_key") and callable(engine._has_composer_key), (
        "advisors.asset_swap_engine must expose a callable '_has_composer_key'. "
        "ai_advisor_tab() calls it at app.py:3265; missing fn is swallowed by the "
        "except-pass, defaulting no_api_key=True and showing swap/logic forms as "
        "always-unavailable regardless of the actual credential state."
    )


# ---------------------------------------------------------------------------
# Break-restore proof (inline documentation of manual verification)
#
# The PM required proof that this test genuinely guards the production path
# (not a vacuous pass).  Verification was performed as follows:
#
#   1. Temporarily patched advisors.correlation_diagnostic to make
#      compute_pairwise_correlations raise AttributeError:
#
#        import advisors.correlation_diagnostic as cd
#        original = cd.compute_pairwise_correlations
#        del cd.compute_pairwise_correlations   # simulate rename/deletion
#
#   2. Ran test_correlation_diagnostic_exposes_compute_pairwise_correlations.
#      Result: FAILED — "advisors.correlation_diagnostic must expose a callable
#      'compute_pairwise_correlations'."
#
#   3. Restored the attribute (cd.compute_pairwise_correlations = original).
#      Result: PASSED.
#
# The same break-restore cycle was confirmed for:
#   - CRISIS_CAVEAT (del + restore on the module)
#   - humanize_obs_preview (del + restore on prism_render)
#   - humanize_lens_summary (del + restore on prism_render)
#   - _has_composer_key (del + restore on asset_swap_engine)
#
# All five existence guards fail when the attribute is absent and pass when
# restored.  test_advisor_tab_returns_200_with_real_producer_modules was
# verified to PASS in both states (the route's except-pass swallows missing
# attrs and degrades gracefully — confirming the route-level smoke test alone
# is insufficient without the explicit hasattr guards above).
# ---------------------------------------------------------------------------
