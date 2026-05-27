"""
Sprint 3 Stream B — RED: Advisor Observations UI surface.

Covers the new advisor_observations display section on GET /ai-advisor and
the supporting /api/advisor-observations route.  The existing Claude
config-suggestion section must remain UNCHANGED.

Binding contract these tests pin:

  Route:   GET /ai-advisor         (renders ai_advisor.html)
  Route:   GET /api/advisor-observations  (JSON; supports ?symphony_id=)
  No-ops:  POST/PUT/DELETE to /api/advisor-observations → 405

Template contract:
  - data-testid="advisor-observations-section" present in rendered HTML
  - data-testid="advisor-suggestions-section"  present (smoke: existing section untouched)
  - Verdict cells carry CSS class or data attribute tied to studio tokens:
      CLEAR   → --studio-pos   (or token class "verdict-clear")
      WATCH   → --studio-warn  (or token class "verdict-watch")
      BREACH  → --studio-neg   (or token class "verdict-breach")
    No raw hex (#rrggbb / rgb(…)) in the rendered verdict markup.
  - is_advisory_only badge present on every observation row
  - Empty state: "No observations from advisors at this time" (no stack trace)
  - raw_response content is HTML-escaped (XSS guard)

Symphony filter:
  GET /api/advisor-observations?symphony_id=<id>
  → only rows whose subject_id == symphony_id are returned

Read-only enforcement:
  POST/PUT/DELETE /api/advisor-observations → 405

Hostile edges:
  - 1000+ row table: response is not a 500 and is paginated/limited
  - XSS: raw_response with <script> tag must be escaped in rendered HTML
  - Malformed legacy row (missing optional fields): no crash, graceful render

Fixture provenance:
  tests/fixtures/ai_advisor/advisor_observations_shape.json
  Schema-derived from migrations/017_advisor_observations.sql and
  database._ADVISOR_OBSERVATION_COLUMNS.  Values are test sentinels.

Mocking strategy:
  - database.get_advisor_observations_for_subject patched for route tests
  - database.get_advisor_observations_for_role patched where relevant
  - No live DB connections; no live network calls
  - math engine is NOT touched — this is a UI surface test
  - module-level mutable state: NONE; every fixture is function-scoped
"""

from __future__ import annotations

import html
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "ai_advisor"
)


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Shared test-sentinel observation rows (derived from fixture schema, never
# hardcoded producer values — these are structural sentinels only)
# ---------------------------------------------------------------------------

def _make_observation(
    *,
    obs_id: int = 1,
    advisor_role: str = "OVERFITTING_CONSCIENCE",
    subject_type: str = "autotune_run",
    subject_id: str = "run-test-001",
    verdict: str = "CLEAR",
    raw_response: dict | None = None,
    is_advisory_only: int = 1,
    spec_bundle_id: str | None = "bundle-test",
) -> dict:
    """Build a single observation dict matching the DB accessor contract.

    All default values are test sentinels chosen to be individually recognizable
    — NOT producer-computed rates/hours/dollars/percents.
    """
    return {
        "id": obs_id,
        "created_at": "2026-05-20T10:00:00",
        "advisor_role": advisor_role,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "verdict": verdict,
        "raw_response": raw_response if raw_response is not None else {"test": "sentinel"},
        "is_advisory_only": is_advisory_only,
        "spec_bundle_id": spec_bundle_id,
    }


# ---------------------------------------------------------------------------
# Flask test-client fixture — mirrors the dashboard conftest pattern
# (patch init_db so the import side-effect does not open the live DB)
# ---------------------------------------------------------------------------

@pytest.fixture()
def flask_client():
    """Flask test client with minimal stubs. No live DB; no live network."""
    with patch("database.init_db"):
        import app as flask_app

    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# Helper: render /ai-advisor with a patched observations accessor
# ---------------------------------------------------------------------------

def _get_ai_advisor_page(flask_client, observations: list[dict]) -> object:
    """GET /ai-advisor with database.get_advisor_observations_for_role patched
    to return the given observations.  Returns the test response object.
    """
    with (
        patch("database.get_advisor_observations_for_role", return_value=observations),
        patch("database.get_advisor_observations_for_subject", return_value=observations),
        patch("market_calendar.get_market_state", return_value="open"),
    ):
        return flask_client.get("/ai-advisor")


# ===========================================================================
# Section 1 — Route contract: GET /ai-advisor returns 200
# ===========================================================================

class TestAiAdvisorRouteReturns200:
    """GET /ai-advisor must return 200 regardless of observation count."""

    def test_route_returns_200_with_observations(self, flask_client):
        """Standard case: one observation per verdict type in the DB."""
        observations = [
            _make_observation(obs_id=1, verdict="CLEAR"),
            _make_observation(obs_id=2, verdict="WATCH"),
            _make_observation(obs_id=3, verdict="BREACH"),
        ]
        resp = _get_ai_advisor_page(flask_client, observations)
        assert resp.status_code == 200, (
            f"GET /ai-advisor with observations must return 200; got {resp.status_code}"
        )

    def test_route_returns_200_with_empty_observations(self, flask_client):
        """No observations in DB: route must still return 200, not 500."""
        resp = _get_ai_advisor_page(flask_client, [])
        assert resp.status_code == 200, (
            f"GET /ai-advisor with empty observations must return 200; got {resp.status_code}"
        )


# ===========================================================================
# Section 2 — Template structure: both sections present
# ===========================================================================

class TestTemplateSectionsCoexist:
    """Both the new observations section AND the existing suggestions section
    must be present in the rendered HTML — additive, not a replacement."""

    def test_observations_section_present_in_rendered_html(self, flask_client):
        """data-testid='advisor-observations-section' must be in the rendered output.
        This is the new section added by the AI Advisor UI cycle.
        """
        observations = [_make_observation()]
        resp = _get_ai_advisor_page(flask_client, observations)
        body = resp.get_data(as_text=True)
        assert "advisor-observations-section" in body, (
            "Rendered /ai-advisor HTML must contain data-testid='advisor-observations-section'. "
            "Implementer: add this section to templates/ai_advisor.html — it is the new "
            "advisor_observations display block."
        )

    def test_existing_suggestions_section_untouched(self, flask_client):
        """The existing Claude config-suggestion section (advisor-page, advisor-header,
        suggestions-column) must remain in the rendered output — no regression.
        """
        resp = _get_ai_advisor_page(flask_client, [])
        body = resp.get_data(as_text=True)
        assert "advisor-page" in body, (
            "data-testid='advisor-page' must still be present — the existing Claude "
            "config-suggestion section must not be removed or replaced."
        )
        assert "suggestions-column" in body or "suggestions-container" in body, (
            "The existing suggestions column/container must still be present in the "
            "rendered HTML — observations section is additive, not a replacement."
        )
        assert "advisor-header" in body, (
            "data-testid='advisor-header' must still be present — existing header untouched."
        )

    def test_both_sections_coexist_with_observations(self, flask_client):
        """When observations exist, both the observations section AND the existing
        suggestions section are present — the page is additive."""
        observations = [_make_observation()]
        resp = _get_ai_advisor_page(flask_client, observations)
        body = resp.get_data(as_text=True)
        assert "advisor-observations-section" in body, (
            "observations section must be present when observations exist"
        )
        assert "advisor-page" in body, (
            "existing page wrapper must still be present when observations exist"
        )


# ===========================================================================
# Section 3 — Verdict color tokens: no raw hex in rendered markup
# ===========================================================================

class TestVerdictColorTokensNoRawHex:
    """Verdict CLEAR/WATCH/BREACH must be color-coded using studio design-system
    tokens, never raw hex literals.  Architecture constraint: no bare hex
    in selectors or rule blocks outside tokens.css :root."""

    @pytest.mark.parametrize("verdict", ["CLEAR", "WATCH", "BREACH"])
    def test_verdict_row_rendered_without_raw_hex_color(self, flask_client, verdict):
        """The rendered HTML for a verdict row must not contain a raw hex color
        literal (e.g. #1f7a4d, #b07419, #b43a2a).  Color must come from
        --studio-pos, --studio-warn, --studio-neg tokens or a verdict CSS class.
        """
        obs = [_make_observation(verdict=verdict)]
        resp = _get_ai_advisor_page(flask_client, obs)
        body = resp.get_data(as_text=True)

        # Find only the observations section so we don't false-positive on tokens.css
        # loaded via <link> or on the existing suggestions section.
        # Isolate from the observations section marker to the next major section.
        # If the section is not present the test will fail on the section-present test;
        # here we only check for raw hex inside rendered style attributes or inline styles.
        import re
        # Raw hex: #rrggbb or #rrr  — 3 or 6 hex digit sequences after '#'
        inline_hex_pattern = re.compile(
            r'style\s*=\s*["\'][^"\']*#[0-9a-fA-F]{3,8}[^"\']*["\']'
        )
        matches = inline_hex_pattern.findall(body)
        # Filter to only matches that appear to be coloring verdict rows
        # (contain "color" or "background")
        color_matches = [m for m in matches if "color" in m.lower() or "background" in m.lower()]
        assert not color_matches, (
            f"Raw hex color found in inline style for verdict={verdict!r}: {color_matches}. "
            f"Use --studio-pos/--studio-warn/--studio-neg tokens or a verdict CSS class "
            f"(verdict-clear/verdict-watch/verdict-breach) — no raw hex in templates."
        )

    def test_verdict_clear_uses_pos_token_or_class(self, flask_client):
        """CLEAR verdict must use --studio-pos token or a 'verdict-clear' CSS class,
        not the hex value of --studio-pos directly."""
        obs = [_make_observation(verdict="CLEAR")]
        resp = _get_ai_advisor_page(flask_client, obs)
        body = resp.get_data(as_text=True)
        assert "verdict-clear" in body or "studio-pos" in body, (
            "CLEAR verdict must reference --studio-pos token or verdict-clear CSS class "
            "in the rendered observations section."
        )

    def test_verdict_watch_uses_warn_token_or_class(self, flask_client):
        """WATCH verdict must use --studio-warn token or 'verdict-watch' CSS class."""
        obs = [_make_observation(verdict="WATCH")]
        resp = _get_ai_advisor_page(flask_client, obs)
        body = resp.get_data(as_text=True)
        assert "verdict-watch" in body or "studio-warn" in body, (
            "WATCH verdict must reference --studio-warn token or verdict-watch CSS class."
        )

    def test_verdict_breach_uses_neg_token_or_class(self, flask_client):
        """BREACH verdict must use --studio-neg token or 'verdict-breach' CSS class."""
        obs = [_make_observation(verdict="BREACH")]
        resp = _get_ai_advisor_page(flask_client, obs)
        body = resp.get_data(as_text=True)
        assert "verdict-breach" in body or "studio-neg" in body, (
            "BREACH verdict must reference --studio-neg token or verdict-breach CSS class."
        )


# ===========================================================================
# Section 4 — is_advisory_only badge present on every observation row
# ===========================================================================

class TestIsAdvisoryOnlyBadge:
    """Every rendered observation row must carry the is_advisory_only badge —
    the structural declaration that the Advisor never moves money.
    This is a non-negotiable UX requirement (user mandate)."""

    def test_is_advisory_only_badge_present_for_single_row(self, flask_client):
        """A single observation row must have the is_advisory_only badge."""
        obs = [_make_observation(is_advisory_only=1)]
        resp = _get_ai_advisor_page(flask_client, obs)
        body = resp.get_data(as_text=True)
        # Badge can be rendered as data-testid or a CSS class or text content
        assert (
            "advisory-only" in body
            or "is-advisory-only" in body
            or "advisory only" in body.lower()
        ), (
            "Every observation row must render an is_advisory_only badge. "
            "Use data-testid='advisory-only-badge' or class 'advisory-only' — "
            "never implies the Advisor moves money."
        )

    def test_is_advisory_only_badge_present_on_all_rows(self, flask_client):
        """When multiple observations are rendered, each row must carry the badge.
        Count badge occurrences >= observation count.
        """
        obs = [
            _make_observation(obs_id=1, verdict="CLEAR"),
            _make_observation(obs_id=2, verdict="WATCH"),
            _make_observation(obs_id=3, verdict="BREACH"),
        ]
        resp = _get_ai_advisor_page(flask_client, obs)
        body = resp.get_data(as_text=True)
        # Count how many times the badge marker appears
        badge_count = body.lower().count("advisory-only") + body.lower().count("advisory only")
        assert badge_count >= len(obs), (
            f"Expected at least {len(obs)} advisory-only badge occurrences for "
            f"{len(obs)} observation rows; found {badge_count}. "
            "Every row must carry the badge — check template loop."
        )


# ===========================================================================
# Section 5 — Empty state: no stack trace, friendly message
# ===========================================================================

class TestEmptyStateRendering:
    """When advisor_observations is empty, the section must show a friendly
    empty-state message, not a Python stack trace or blank space."""

    def test_empty_state_shows_friendly_message(self, flask_client):
        """No observations: the observations section must render the
        'No observations from advisors at this time' message."""
        resp = _get_ai_advisor_page(flask_client, [])
        body = resp.get_data(as_text=True)
        assert "no observations from advisors" in body.lower() or \
               "no advisor observations" in body.lower(), (
            "Empty advisor_observations must render a friendly empty-state message "
            "('No observations from advisors at this time') — not a blank section "
            "and not a Python stack trace."
        )

    def test_empty_state_is_not_a_stack_trace(self, flask_client):
        """No observations: the response must not contain a Python traceback."""
        resp = _get_ai_advisor_page(flask_client, [])
        body = resp.get_data(as_text=True)
        assert "Traceback" not in body, (
            "GET /ai-advisor with empty observations must not render a Python "
            "Traceback in the response — empty-list is a valid state."
        )
        assert "Internal Server Error" not in body, (
            "GET /ai-advisor with empty observations must not render 'Internal "
            "Server Error' — empty-list is a valid state."
        )


# ===========================================================================
# Section 6 — /api/advisor-observations JSON route
# ===========================================================================

class TestAdvisorObservationsApiRoute:
    """GET /api/advisor-observations returns 200 + a JSON list with the
    correct data shape.  Every row in the response must have the required keys."""

    @pytest.fixture()
    def observations_fixture(self) -> list[dict]:
        """Three observations covering all three verdict values — schema-derived
        from advisor_observations_shape.json; values are sentinels."""
        return _load_fixture("advisor_observations_shape.json")["example_rows"]

    def test_api_route_returns_200(self, flask_client, observations_fixture):
        """GET /api/advisor-observations must return 200."""
        with (
            patch("database.get_advisor_observations_for_role", return_value=observations_fixture),
            patch("database.get_advisor_observations_for_subject", return_value=observations_fixture),
        ):
            resp = flask_client.get("/api/advisor-observations")
        assert resp.status_code == 200, (
            f"GET /api/advisor-observations must return 200; got {resp.status_code}. "
            "Implementer: add this route to app.py."
        )

    def test_api_route_returns_json_list(self, flask_client, observations_fixture):
        """Response must be a JSON array (list)."""
        with (
            patch("database.get_advisor_observations_for_role", return_value=observations_fixture),
            patch("database.get_advisor_observations_for_subject", return_value=observations_fixture),
        ):
            resp = flask_client.get("/api/advisor-observations")
        data = resp.get_json()
        assert isinstance(data, list), (
            f"GET /api/advisor-observations must return a JSON array; got {type(data).__name__}"
        )

    def test_api_route_response_rows_have_required_keys(self, flask_client, observations_fixture):
        """Each row in the response must have the required keys:
        advisor_role, subject_type, subject_id, verdict, is_advisory_only,
        created_at.  These are the fields the template renders."""
        required_keys = {"advisor_role", "subject_type", "subject_id",
                         "verdict", "is_advisory_only", "created_at"}
        with (
            patch("database.get_advisor_observations_for_role", return_value=observations_fixture),
            patch("database.get_advisor_observations_for_subject", return_value=observations_fixture),
        ):
            resp = flask_client.get("/api/advisor-observations")
        data = resp.get_json()
        assert data, "response must not be empty for non-empty fixture"
        for row in data:
            missing = required_keys - set(row.keys())
            assert not missing, (
                f"API response row missing required keys: {missing}. "
                f"Row keys present: {set(row.keys())}"
            )

    def test_api_route_is_advisory_only_always_1(self, flask_client, observations_fixture):
        """Every row in the response must have is_advisory_only == 1 —
        structural declaration that the Advisor never moves money."""
        with (
            patch("database.get_advisor_observations_for_role", return_value=observations_fixture),
            patch("database.get_advisor_observations_for_subject", return_value=observations_fixture),
        ):
            resp = flask_client.get("/api/advisor-observations")
        data = resp.get_json()
        for row in data:
            assert row.get("is_advisory_only") == 1, (
                f"is_advisory_only must always be 1; got {row.get('is_advisory_only')!r} "
                f"for row id={row.get('id')}"
            )


# ===========================================================================
# Section 7 — Symphony filter: ?symphony_id= parameter
# ===========================================================================

class TestSymphonyIdFilter:
    """GET /api/advisor-observations?symphony_id=<id> must filter observations
    via the single-query symphony accessor backed by the new
    advisor_observations.symphony_id column (migration 025, S3-AUDIT-004).

    Prior to the audit-fix cycle, this test class asserted that the route
    used `get_advisor_observations_for_subject` with a 3x fan-out across
    subject_types — that path was structurally broken because the user-supplied
    symphony_id never matched producer-persisted subject_ids (numeric PKs and
    bundle hashes).  The new contract: route calls
    `get_advisor_observations_for_symphony(symphony_id)` exactly once.
    """

    def test_symphony_filter_returns_only_matching_rows(self, flask_client):
        """When ?symphony_id=sym-A is given, the route returns every row
        emitted by the new single-query accessor for that symphony."""
        rows_a = [_make_observation(obs_id=1, subject_id="run-7")]
        with patch(
            "database.get_advisor_observations_for_symphony", return_value=rows_a
        ):
            resp = flask_client.get("/api/advisor-observations?symphony_id=sym-A")

        assert resp.status_code == 200, (
            f"Filtered GET /api/advisor-observations?symphony_id=sym-A must return 200; "
            f"got {resp.status_code}"
        )
        data = resp.get_json()
        assert isinstance(data, list), "filtered response must be a JSON array"
        assert len(data) == len(rows_a), (
            f"Route must surface every row returned by the single-query accessor; "
            f"expected {len(rows_a)} rows, got {len(data)}."
        )

    def test_symphony_filter_calls_correct_accessor(self, flask_client):
        """?symphony_id= must cause the route to call the new single-query
        accessor `get_advisor_observations_for_symphony` — not the legacy
        subject-type fan-out.  S3-AUDIT-004 + S3-AUDIT-010.
        """
        with (
            patch(
                "database.get_advisor_observations_for_symphony", return_value=[]
            ) as mock_symphony,
            patch(
                "database.get_advisor_observations_for_subject", return_value=[]
            ) as mock_subject,
            patch(
                "database.get_advisor_observations_for_role", return_value=[]
            ) as mock_role,
        ):
            flask_client.get("/api/advisor-observations?symphony_id=sym-B")

        assert mock_symphony.called, (
            "GET /api/advisor-observations?symphony_id=sym-B must call "
            "database.get_advisor_observations_for_symphony (S3-AUDIT-004 fix)."
        )
        assert not mock_subject.called, (
            "Route must NOT call the legacy get_advisor_observations_for_subject "
            "fan-out for symphony filtering (S3-AUDIT-010: single-query path)."
        )

    def test_symphony_filter_empty_result_returns_empty_list(self, flask_client):
        """?symphony_id= with no matching rows must return [] (empty list),
        not 404 or a stack trace."""
        with patch(
            "database.get_advisor_observations_for_symphony", return_value=[]
        ):
            resp = flask_client.get("/api/advisor-observations?symphony_id=nonexistent-sym")
        assert resp.status_code == 200, (
            "Filtered request with no matching rows must return 200 + empty list, not 404."
        )
        data = resp.get_json()
        assert data == [], (
            f"No matching symphony_id must yield an empty list; got {data!r}"
        )


# ===========================================================================
# Section 8 — Read-only enforcement: POST/PUT/DELETE return 405
# ===========================================================================

class TestReadOnlyEnforcement:
    """The dashboard is a read-only surface (architecture constraint 2).
    POST, PUT, DELETE to advisor-observation routes must return 405 Method Not Allowed.
    Only GET is permitted."""

    @pytest.mark.parametrize("method", ["post", "put", "delete"])
    def test_write_methods_return_405(self, flask_client, method):
        """POST/PUT/DELETE /api/advisor-observations must return 405."""
        resp = getattr(flask_client, method)("/api/advisor-observations")
        assert resp.status_code == 405, (
            f"{method.upper()} /api/advisor-observations must return 405 Method Not Allowed; "
            f"got {resp.status_code}. The dashboard surface is read-only "
            f"(architecture constraint 2) — only GET is permitted."
        )

    @pytest.mark.parametrize("method", ["post", "put", "delete"])
    def test_write_methods_to_page_route_return_405(self, flask_client, method):
        """POST/PUT/DELETE /ai-advisor must return 405 — the page route is GET-only."""
        resp = getattr(flask_client, method)("/ai-advisor")
        assert resp.status_code == 405, (
            f"{method.upper()} /ai-advisor must return 405; got {resp.status_code}. "
            "Route is GET-only (read-only dashboard surface)."
        )


# ===========================================================================
# Section 9 — Hostile edges
# ===========================================================================

class TestHostileEdges:
    """Adversarial edge cases: large tables, XSS, malformed legacy rows."""

    def test_large_observation_table_does_not_return_500(self, flask_client):
        """1000+ rows in advisor_observations must not cause a 500.
        The route must paginate or limit — UI must not block."""
        # 1000 sentinel rows — all structurally valid, values are sentinels
        large_batch = [
            _make_observation(obs_id=i, verdict="CLEAR" if i % 3 == 0 else "WATCH")
            for i in range(1, 1001)
        ]
        with (
            patch("database.get_advisor_observations_for_role", return_value=large_batch),
            patch("database.get_advisor_observations_for_subject", return_value=large_batch),
            patch("market_calendar.get_market_state", return_value="open"),
        ):
            resp = flask_client.get("/api/advisor-observations")
        assert resp.status_code == 200, (
            f"1000-row advisor_observations must return 200; got {resp.status_code}. "
            "Add pagination or a LIMIT — the UI must not block on large tables."
        )
        data = resp.get_json()
        # Response must be a list and must be bounded (paginated/limited)
        assert isinstance(data, list), "response must be a JSON array"
        assert len(data) <= 200, (
            f"Response with 1000 rows must be paginated or limited to ≤200; "
            f"got {len(data)}. Unbounded responses block the UI."
        )

    def test_raw_response_with_xss_payload_is_html_escaped(self, flask_client):
        """raw_response containing a <script> tag must be HTML-escaped in the
        rendered template — user-controllable strings from the DB must never
        execute as JavaScript in the browser."""
        xss_payload = "<script>alert('XSS')</script>"
        obs = [_make_observation(raw_response={"narrative": xss_payload})]
        resp = _get_ai_advisor_page(flask_client, obs)
        body = resp.get_data(as_text=True)
        # The raw <script> tag must NOT appear literally in the response body
        assert "<script>alert" not in body, (
            "raw_response with <script> tag must be HTML-escaped in the template. "
            "Use Jinja2's auto-escaping ({{ value | e }} or auto_escape=True) — "
            "never render raw_response as |safe."
        )
        # The escaped form IS acceptable: &lt;script&gt;
        escaped_form = html.escape(xss_payload)
        # We don't assert the escaped form is present because the collapsible/tooltip
        # may not render it at all in a collapsed state — we only assert the raw
        # form is absent (necessary condition for XSS safety).

    def test_malformed_legacy_row_does_not_cause_500(self, flask_client):
        """A legacy observation row missing optional fields (spec_bundle_id=None,
        verdict=None) must not cause a 500 — graceful degradation is required."""
        legacy_row = {
            "id": 99,
            "created_at": "2026-01-01T00:00:00",
            "advisor_role": "OVERFITTING_CONSCIENCE",
            "subject_type": "autotune_run",
            "subject_id": "legacy-run-001",
            "verdict": None,       # legacy rows may have NULL verdict
            "raw_response": {},    # empty raw_response
            "is_advisory_only": 1,
            "spec_bundle_id": None,  # NULL — before spec_bundle_id was added
        }
        with (
            patch("database.get_advisor_observations_for_role", return_value=[legacy_row]),
            patch("database.get_advisor_observations_for_subject", return_value=[legacy_row]),
            patch("market_calendar.get_market_state", return_value="open"),
        ):
            resp = flask_client.get("/ai-advisor")
        assert resp.status_code == 200, (
            f"Malformed legacy observation row (NULL verdict/spec_bundle_id) must not "
            f"cause a 500; got {resp.status_code}. Template must handle None gracefully."
        )
        body = resp.get_data(as_text=True)
        assert "Traceback" not in body, (
            "Malformed legacy row must not produce a Python Traceback in the response."
        )

    def test_raw_response_with_nested_html_is_escaped_in_template(self, flask_client):
        """A raw_response with nested HTML tags in string values must be escaped.
        This is the adversarial second pass: nested angle brackets, not just <script>."""
        nested_html = {"text": '<img src=x onerror="alert(1)">'}
        obs = [_make_observation(raw_response=nested_html)]
        resp = _get_ai_advisor_page(flask_client, obs)
        body = resp.get_data(as_text=True)
        # The raw onerror= attribute must not appear unescaped in the rendered HTML
        assert 'onerror="alert' not in body, (
            "raw_response with onerror= HTML injection must be escaped in the template. "
            "Use Jinja2 auto-escaping — never use |safe on raw_response content."
        )


# ===========================================================================
# Section 10 — Advisor role human-friendly labels
# ===========================================================================

class TestAdvisorRoleHumanFriendlyLabels:
    """The template must display human-friendly labels for advisor roles,
    not the raw internal enum strings (OVERFITTING_CONSCIENCE is not
    operator-friendly)."""

    @pytest.mark.parametrize("role,expected_label_fragment", [
        ("OVERFITTING_CONSCIENCE", "Overfitting"),
        ("SPEC_CRITIC", "Spec"),
        ("DIVERGENCE_EXPLAINER", "Divergence"),
    ])
    def test_advisor_role_displayed_with_human_friendly_label(
        self, flask_client, role, expected_label_fragment
    ):
        """Each advisor role must be rendered with a human-friendly label
        that a non-developer operator can understand."""
        obs = [_make_observation(advisor_role=role)]
        resp = _get_ai_advisor_page(flask_client, obs)
        body = resp.get_data(as_text=True)
        assert expected_label_fragment in body, (
            f"Advisor role {role!r} must be displayed with a human-friendly label "
            f"containing {expected_label_fragment!r}. "
            "Implementer: add a role-to-label mapping in the template or route handler."
        )


# ===========================================================================
# Section 11 — Fixture self-check
# ===========================================================================

class TestFixtureSelfCheck:
    """Validate the fixture file itself covers the required schema fields and
    verdict enum values — a wrong fixture weakens every upstream test."""

    def test_fixture_covers_all_valid_verdicts(self):
        """The example_rows in the fixture must cover all three verdict values:
        CLEAR, WATCH, BREACH."""
        fixture = _load_fixture("advisor_observations_shape.json")
        verdicts_in_fixture = {row["verdict"] for row in fixture["example_rows"]}
        required_verdicts = {"CLEAR", "WATCH", "BREACH"}
        assert required_verdicts <= verdicts_in_fixture, (
            f"Fixture must cover all valid verdict values {required_verdicts}; "
            f"fixture only has {verdicts_in_fixture}."
        )

    def test_fixture_is_advisory_only_always_1(self):
        """Every example row in the fixture must have is_advisory_only == 1 —
        the fixture must reflect the DB-level guarantee."""
        fixture = _load_fixture("advisor_observations_shape.json")
        for row in fixture["example_rows"]:
            assert row["is_advisory_only"] == 1, (
                f"Fixture row id={row['id']!r} has is_advisory_only={row['is_advisory_only']!r}; "
                "must be 1 — the Advisor never moves money."
            )

    def test_fixture_rows_have_required_columns(self):
        """Every example row must have all columns listed in _ADVISOR_OBSERVATION_COLUMNS."""
        fixture = _load_fixture("advisor_observations_shape.json")
        required_cols = set(fixture["_columns"])
        for row in fixture["example_rows"]:
            missing = required_cols - set(row.keys())
            assert not missing, (
                f"Fixture row id={row.get('id')!r} is missing columns: {missing}"
            )

    def test_fixture_valid_verdicts_list_matches_schema(self):
        """The fixture's valid_verdicts list must match the schema-documented
        values: CLEAR, WATCH, BREACH."""
        fixture = _load_fixture("advisor_observations_shape.json")
        assert set(fixture["valid_verdicts"]) == {"CLEAR", "WATCH", "BREACH"}, (
            "fixture valid_verdicts must be exactly {CLEAR, WATCH, BREACH}"
        )
