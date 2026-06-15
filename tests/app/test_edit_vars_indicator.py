"""
RED tests for Edit Variables populated-indicator.

Bug: Operator reports "'Edit Vars' isn't showing that I have anything
populated. I'm not saying it should show the keys, but it should populate
with bubbles or something at least so I know I DID put something there."

Root cause: The "Edit Variables" button in the header (templates/index.html
line ~169) has no visual indicator of populated state. The Strategy Variables
modal is JS-rendered from /api/settings, but the button itself is static HTML
with no server-rendered badge.

Fix:
  1. app.py index route queries total locked_vars count across all symphonies
     and passes `vars_locked_count` to render_template.
  2. templates/index.html renders a small badge/bubble next to "Edit Variables"
     when vars_locked_count > 0, showing the operator that vars are populated.
  3. Indicator is server-rendered on initial page load (no JS needed for first
     render — satisfies AC-VARS.3).

Acceptance criteria:
  AC-VARS.1 : When N≥1 vars are locked across any symphony, GET / renders
              an indicator element near the "Edit Variables" button (id or
              data-testid "edit-vars-indicator" present and non-empty).
  AC-VARS.2 : When 0 vars are locked, the indicator element is absent or
              hidden (empty state preserved — no phantom badge).
  AC-VARS.3 : Indicator is present in the initial HTML response (server-side
              rendered) — not gated on a subsequent JS fetch.

Fixture provenance:
  tests/fixtures/app/edit_vars_indicator.json
  Hand-authored. Schema-derived from database.get_symphony_strategy return
  shape and database.DEFAULT_LOCKED_VARS. PA-18.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "app" / "edit_vars_indicator.json"
)


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Flask client fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def flask_client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with (
        patch.object(app_module, "schedule"),
        patch.object(
            app_module,
            "get_api_state_dict",
            return_value={
                "bot_state": {},
                "is_locked": False,
                "port_state": {},
                "exit_authority": {},
                "daemon_started_at": None,
            },
        ),
    ):
        with app_module.app.test_client() as client:
            yield client, app_module


def _make_db_mock_with_strategies(symphonies: dict) -> MagicMock:
    """Build a database mock whose get_symphony_strategy returns per-fixture data."""
    mock_db = MagicMock()
    mock_db.load_state.return_value = {
        "date": "2026-05-16",
        **{sym_id: {"name": sym_id, "account": "ACC-INDIVIDUAL"} for sym_id in symphonies},
    }
    mock_db.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")

    def _get_strategy(name):
        normed = name.lower().replace(" ", "_")
        return symphonies.get(normed)

    mock_db.get_symphony_strategy.side_effect = _get_strategy
    mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
    mock_db.get_triggers.return_value = []
    mock_db.read_fleet_alert.return_value = None
    return mock_db


# ===========================================================================
# AC-VARS.1: N≥1 locked vars → indicator element present in rendered HTML
# ===========================================================================


class TestEditVarsIndicatorPresent:
    """
    AC-VARS.1: When at least one symphony has non-empty locked_vars, the
    rendered index page must include an element with id="edit-vars-indicator"
    (or data-testid="edit-vars-indicator") near the Edit Variables button.
    The element must be visible (not hidden via CSS class).
    """

    def test_indicator_present_when_locked_vars_populated(self, flask_client, monkeypatch):
        """AC-VARS.1: locked_vars populated → indicator element in page HTML."""
        client, app_module = flask_client
        fx = _load_fixture()
        symphonies = fx["scenario_populated"]["symphonies"]

        with patch.object(app_module, "database", _make_db_mock_with_strategies(symphonies)):
            resp = client.get("/")

        assert resp.status_code == 200, f"Index route must return 200. Got {resp.status_code}."
        html = resp.get_data(as_text=True)

        assert 'id="edit-vars-indicator"' in html or 'data-testid="edit-vars-indicator"' in html, (
            "Rendered index HTML must contain an element with id='edit-vars-indicator' "
            "or data-testid='edit-vars-indicator' when locked_vars are populated. "
            "Operator needs a visual signal that vars are set without seeing the values. "
            f"Searched HTML length: {len(html)} chars."
        )

    def test_indicator_shows_nonzero_count(self, flask_client, monkeypatch):
        """AC-VARS.1: indicator element must contain a non-zero count or non-empty text."""
        client, app_module = flask_client
        fx = _load_fixture()
        symphonies = fx["scenario_populated"]["symphonies"]

        with patch.object(app_module, "database", _make_db_mock_with_strategies(symphonies)):
            resp = client.get("/")

        html = resp.get_data(as_text=True)

        # Find the indicator and verify it's non-empty (has content between tags)
        import re

        indicator_pattern = re.compile(
            r'(?:id|data-testid)="edit-vars-indicator"[^>]*>(.*?)<',
            re.DOTALL,
        )
        match = indicator_pattern.search(html)
        assert match is not None, (
            "edit-vars-indicator element not found in HTML. "
            "Must be server-rendered near the 'Edit Variables' button."
        )
        content = match.group(1).strip()
        assert content, (
            f"edit-vars-indicator element must have non-empty content (count/badge text). "
            f"Found element but content was empty: {match.group(0)!r}"
        )

    def test_indicator_count_matches_fixture_total(self, flask_client, monkeypatch):
        """AC-VARS.1: badge content must equal the fixture's expected_total_locked_count.

        Pins that the count is correct, not just non-empty.  The fixture has 2 locked
        vars in scenario_populated (alpha-momentum: TRIGGER_THRESHOLD_PCT,
        TAKE_PROFIT_MC_PCT).  An implementation that always renders "1" or renders
        sum(all_params) would fail this test.
        """
        client, app_module = flask_client
        fx = _load_fixture()
        symphonies = fx["scenario_populated"]["symphonies"]
        expected_count = fx["scenario_populated"]["expected_total_locked_count"]

        with patch.object(app_module, "database", _make_db_mock_with_strategies(symphonies)):
            resp = client.get("/")

        html = resp.get_data(as_text=True)

        import re

        indicator_pattern = re.compile(
            r'(?:id|data-testid)="edit-vars-indicator"[^>]*>(.*?)<',
            re.DOTALL,
        )
        match = indicator_pattern.search(html)
        assert match is not None, "edit-vars-indicator element not found in HTML."
        content = match.group(1).strip()
        assert content == str(expected_count), (
            f"edit-vars-indicator badge must show {expected_count} (fixture expected_total_locked_count). "
            f"Got {content!r}. "
            "Count must reflect only locked_vars entries, not params keys or any other measure."
        )


# ===========================================================================
# AC-VARS.2: 0 locked vars → no indicator (empty state preserved)
# ===========================================================================


class TestEditVarsIndicatorAbsentWhenEmpty:
    """
    AC-VARS.2: When no symphony has any locked vars, the indicator element
    must be absent or hidden. No phantom badge when nothing is populated.
    """

    def test_no_indicator_when_no_locked_vars(self, flask_client, monkeypatch):
        """AC-VARS.2: zero locked_vars across all symphonies → no visible indicator."""
        client, app_module = flask_client
        fx = _load_fixture()
        symphonies = fx["scenario_empty"]["symphonies"]

        with patch.object(app_module, "database", _make_db_mock_with_strategies(symphonies)):
            resp = client.get("/")

        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        import re

        indicator_pattern = re.compile(
            r'(?:id|data-testid)="edit-vars-indicator"[^>]*>(.*?)<',
            re.DOTALL,
        )
        match = indicator_pattern.search(html)
        if match:
            content = match.group(1).strip()
            assert not content, (
                f"edit-vars-indicator must be empty or absent when locked_vars is empty. "
                f"Found indicator with content: {content!r}. "
                "Phantom badge confuses operator about whether vars are actually set."
            )


# ===========================================================================
# AC-VARS.3: Indicator is in initial server-rendered HTML (no JS required)
# ===========================================================================


class TestEditVarsIndicatorServerRendered:
    """
    AC-VARS.3: The indicator must be present in the initial HTTP response
    body — not injected by a subsequent JS fetch. Operator must see the
    indicator on page load without waiting for /api/settings to resolve.
    """

    def test_indicator_in_initial_response_not_gated_on_js(self, flask_client, monkeypatch):
        """AC-VARS.3: GET / HTML contains indicator without any JS execution."""
        client, app_module = flask_client
        fx = _load_fixture()
        symphonies = fx["scenario_populated"]["symphonies"]

        # Deliberately do NOT patch render_template — we need the real template
        # rendering to confirm the indicator is in the static HTML, not injected
        # by a subsequent fetch call.
        with patch.object(app_module, "database", _make_db_mock_with_strategies(symphonies)):
            resp = client.get("/")

        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        # The indicator must appear in the HTML, not only referenced in JS fetch callbacks
        assert "edit-vars-indicator" in html, (
            "edit-vars-indicator must be present in the server-rendered HTML "
            "(i.e. output of render_template, not injected by JS). "
            "The indicator must be visible on initial page load — AC-VARS.3."
        )

        # Verify it is NOT exclusively inside a JS string literal
        # (a simple heuristic: the element should appear outside of <script> tags)
        import re

        # Strip script blocks
        no_scripts = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        assert "edit-vars-indicator" in no_scripts, (
            "edit-vars-indicator must appear in the HTML markup (outside <script> tags). "
            "If it only appears inside a JS string, it's not server-rendered."
        )
