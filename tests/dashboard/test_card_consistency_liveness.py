"""
RED tests — card-consistency: every displayed value updates on each 30s poll tick.

CONTRACT (bugs being fixed):
  CC-1  /api/state 'symphonies' entries carry guard_alpha for triggered symphonies
        so the outcome banner text can be updated client-side on each poll.
  CC-2  /api/state 'symphonies' entries carry stop_trigger (trailing-stop value)
        so the stop readout on each card can be updated on each poll.
  CC-3  static/index.js updateCards uses querySelectorAll('[data-field="tc-bot"]')
        (not querySelector) so BOTH the dual-value-headline span AND the footer
        cfg-val span are updated — the single-match querySelector bug produces
        headline/footer divergence (+0.3% vs +0.2%).
  CC-4  The same querySelectorAll guard applies to tc-held, cr-bot, cr-held,
        mdd-bot, and mdd-held — any other duplicated data-field must also use
        querySelectorAll.
  CC-5  static/index.js updateCards shows/hides the triggered-verdict banner element
        for each card from sym.triggered on each poll. The template must ALWAYS
        render the element (with display:none when not triggered) so JS can show/hide
        it without needing to create DOM nodes on demand.
  CC-6  When a symphony is triggered, updateCards sets the outcome banner text
        from sym.guard_alpha (carried in the symphonies payload), not from stale
        server-rendered content.
  CC-7  Hero mini-stats (hero-tracked, hero-armed, hero-triggered) update on
        every poll via updateMiniStats(meta) — already wired; regression guard.
  CC-8  Market-state dot + strip chips update on every poll — already wired;
        regression guard that updateStatusStrip and updateMarketDot are still
        called from updateDashboard.
  CC-9  node --check static/index.js passes (no syntax error).
  CC-10 REGRESSION: no sym-table-container; cards-grid/sym-card/card-spark
        structure preserved; data-field spans addressed in-place only.
  CC-11 The alpha-badge spans (α delta for Today/Cum/Max DD) carry data-field
        attributes so updateCards can refresh them on each poll without innerHTML
        replacement.
  CC-12 Section count badges (active-section-count, standby-section-count) and
        "data as of" hero legend update from poll data via updateSectionMeta.

Fixture provenance:
  tests/fixtures/dashboard/cards_live/card_consistency_bot_state.json
  Hand-authored; bot_state entries carry _tc/_cr/_mdd enrichment dicts matching
  the app.py analytics-enrichment shape (dry_run / if_held keys).
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WORKTREE = pathlib.Path(__file__).parent.parent.parent
_STATIC_JS = _WORKTREE / "static" / "index.js"
_INDEX_HTML = _WORKTREE / "templates" / "index.html"
_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "dashboard"
    / "cards_live"
    / "card_consistency_bot_state.json"
)

# Size of the function body window used for JS text searches.
# The updateCards function body is ~130 lines; 7000 chars covers it with margin.
_UPDATE_CARDS_WINDOW = 7000
# updateDashboard body is shorter; 2000 chars covers it.
_UPDATE_DASHBOARD_WINDOW = 2000


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _js() -> str:
    return _STATIC_JS.read_text(encoding="utf-8")


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def _update_cards_body(src: str) -> str:
    """Extract the updateCards function body (from signature through closing brace)."""
    start = src.find("function updateCards(")
    assert start != -1, "function updateCards not found in static/index.js"
    return src[start : start + _UPDATE_CARDS_WINDOW]


def _update_dashboard_body(src: str) -> str:
    """Extract the updateDashboard function body."""
    start = src.find("function updateDashboard(")
    assert start != -1, "function updateDashboard not found in static/index.js"
    return src[start : start + _UPDATE_DASHBOARD_WINDOW]


# ---------------------------------------------------------------------------
# Flask test-client fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def flask_client():
    """Flask test client — no live DB, no live network, analytics fully mocked."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    bot_state = _load_fixture()
    # Remove the provenance key so it's a clean bot_state dict.
    bot_state.pop("_provenance", None)

    analytics_mock = MagicMock()
    analytics_mock.get_portfolio_today_change.return_value = None
    analytics_mock.get_portfolio_cumulative_return.return_value = None
    analytics_mock.get_portfolio_max_drawdown.return_value = None
    analytics_mock.get_portfolio_daily_returns_from_shadow.return_value = None
    analytics_mock.get_history_with_cache_invalidation.return_value = {}
    analytics_mock.compute_aggregate_returns.return_value = ([], [], [])
    analytics_mock._POST_MORTEMS_DIR = "/tmp/no-such-dir"

    # Per-symphony analytics return values matching the fixture enrichment fields.
    def _sym_tc(sym_id, *args, **kwargs):
        raw = bot_state.get(sym_id, {}).get("_tc") or {}
        return raw if raw else {"if_held": 0.0, "dry_run": 0.0}

    def _sym_cr(sym_id, *args, **kwargs):
        raw = bot_state.get(sym_id, {}).get("_cr") or {}
        return raw if raw else {"if_held": 0.0, "dry_run": 0.0}

    def _sym_mdd(sym_id, *args, **kwargs):
        raw = bot_state.get(sym_id, {}).get("_mdd") or {}
        return raw if raw else {"if_held": 0.0, "dry_run": 0.0}

    analytics_mock.get_symphony_today_change.side_effect = _sym_tc
    analytics_mock.get_symphony_cumulative_return.side_effect = _sym_cr
    analytics_mock.get_symphony_max_drawdown.side_effect = _sym_mdd

    with patch.object(app_module, "analytics", analytics_mock):
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = bot_state
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {},
                "portfolio_today": None,
            }
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_last_trigger_per_symphony.return_value = {}
            # guard_alpha is pre-baked into bot_state fixture entries.
            db_mock.get_guard_alpha_by_symphony.return_value = {}
            db_mock.get_symphony_live_mode.return_value = False
            db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
            db_mock.get_triggers.return_value = []

            with patch.object(app_module, "dotenv_values", return_value={}):
                with patch.object(app_module, "render_template", return_value=""):
                    with app_module.app.test_client() as client:
                        yield client, app_module, bot_state


def _get_symphonies(client) -> dict:
    """Call /api/state and return sym_id → sym_dict mapping from 'symphonies'."""
    resp = client.get("/api/state")
    assert resp.status_code == 200, f"/api/state returned {resp.status_code}"
    body = resp.get_json()
    assert "symphonies" in body, "'/api/state' missing 'symphonies' key"
    syms = body["symphonies"]
    if isinstance(syms, list):
        return {s["id"]: s for s in syms if isinstance(s, dict) and "id" in s}
    return syms


# ---------------------------------------------------------------------------
# CC-1  'symphonies' entries carry guard_alpha for triggered symphonies
# ---------------------------------------------------------------------------


class TestGuardAlphaInSymphoniesPayload:
    """
    CC-1: The symphonies payload must carry guard_alpha for triggered symphonies
    so the client-side outcome banner text can be updated without a hard refresh.

    The prior implementation passed guard_alpha only in bot_state (which updateVerdicts
    already reads), but updateVerdicts only updates text in pre-existing DOM elements.
    A symphony that becomes triggered after page load has no triggered-verdict element
    in the DOM; updateCards must show/update it using guard_alpha from symphonies.
    """

    def test_triggered_symphony_carries_guard_alpha_in_symphonies(self, flask_client):
        """
        CC-1a: a symphony with triggered=True and a guard_alpha value in bot_state
        must have that guard_alpha present in the 'symphonies' payload.
        """
        client, _app, bot_state = flask_client
        sym_map = _get_symphonies(client)

        # sym_exiting is triggered=True and has guard_alpha=1.30 in the fixture.
        exiting = sym_map.get("sym_exiting")
        assert exiting is not None, "CC-1a FAIL: sym_exiting not found in symphonies payload"
        assert exiting.get("triggered") is True, (
            "CC-1a FAIL: sym_exiting must show triggered=True in symphonies"
        )
        assert "guard_alpha" in exiting, (
            "CC-1a FAIL: triggered symphony 'sym_exiting' is missing 'guard_alpha' "
            "in the symphonies payload. updateCards needs guard_alpha to render the "
            "outcome banner on the first poll after an exit — without it the banner "
            "text stays stale or blank until a hard refresh."
        )
        # The value must be numeric (positive = good call, negative = early exit).
        ga = exiting["guard_alpha"]
        assert isinstance(ga, (int, float)), (
            f"CC-1a FAIL: guard_alpha must be numeric; got {type(ga).__name__}: {ga!r}"
        )

    def test_non_triggered_symphony_guard_alpha_absent_or_none(self, flask_client):
        """
        CC-1b: a non-triggered symphony must NOT carry a meaningful guard_alpha
        (must be absent or None) — a non-zero guard_alpha on an armed/standby card
        would incorrectly render an outcome banner before the symphony has exited.
        """
        client, _app, _bot_state = flask_client
        sym_map = _get_symphonies(client)

        active = sym_map.get("sym_active")
        assert active is not None, "CC-1b FAIL: sym_active not found in symphonies"
        assert not active.get("triggered"), (
            "CC-1b FAIL: sym_active should not be triggered in this fixture"
        )
        # guard_alpha absent or None are both acceptable for non-triggered symphonies.
        ga = active.get("guard_alpha")
        assert ga is None or ga == 0, (
            f"CC-1b FAIL: non-triggered symphony 'sym_active' carries guard_alpha={ga!r}. "
            "This would cause updateCards to show a spurious outcome banner on an armed card."
        )

    def test_symphonies_payload_guard_alpha_matches_bot_state_value(self, flask_client):
        """
        CC-1c: the guard_alpha in symphonies must come from bot_state, not be
        independently computed. It must match the fixture value (guard_alpha=1.30)
        within floating-point tolerance.

        Tolerance 1e-6: guard_alpha is a stored float; a single float→JSON→float
        roundtrip should be exact within machine epsilon; 1e-6 gives headroom.
        """
        client, _app, _bot_state = flask_client
        sym_map = _get_symphonies(client)

        exiting = sym_map.get("sym_exiting")
        assert exiting is not None
        assert "guard_alpha" in exiting, "CC-1c FAIL: guard_alpha missing (see CC-1a)"
        assert exiting["guard_alpha"] == pytest.approx(1.30, abs=1e-6), (
            f"CC-1c FAIL: guard_alpha={exiting['guard_alpha']!r} does not match "
            "fixture value 1.30. The value must be passed through from bot_state, "
            "not re-computed."
        )


# ---------------------------------------------------------------------------
# CC-2  'symphonies' entries carry stop_trigger
# ---------------------------------------------------------------------------


class TestStopTriggerInSymphoniesPayload:
    """
    CC-2: Each symphony entry in 'symphonies' must carry stop_trigger so the
    trailing-stop readout on the card can be updated on each poll.
    """

    def test_armed_symphony_carries_stop_trigger(self, flask_client):
        """
        CC-2a: an armed symphony with a non-None stop_trigger must carry that
        value in the symphonies payload.
        """
        client, _app, bot_state = flask_client
        sym_map = _get_symphonies(client)

        active = sym_map.get("sym_active")
        assert active is not None, "CC-2a FAIL: sym_active not found in symphonies"
        assert "stop_trigger" in active, (
            "CC-2a FAIL: sym_active is missing 'stop_trigger' in the symphonies payload. "
            "The trailing-stop readout on the card updates from this field on each poll."
        )
        # Value must be numeric (the fixture has stop_trigger=3.1).
        st = active["stop_trigger"]
        assert isinstance(st, (int, float)), (
            f"CC-2a FAIL: stop_trigger must be numeric; got {type(st).__name__}: {st!r}"
        )

    def test_standby_symphony_carries_stop_trigger_none_or_absent(self, flask_client):
        """
        CC-2b: a standby symphony with stop_trigger=null in the fixture must
        carry None (or be absent) in the symphonies payload — not a default 0
        that would render a spurious stop readout.
        """
        client, _app, _bot_state = flask_client
        sym_map = _get_symphonies(client)

        standby = sym_map.get("sym_standby")
        assert standby is not None, "CC-2b FAIL: sym_standby not found in symphonies"
        st = standby.get("stop_trigger")
        assert st is None or st == 0, (
            f"CC-2b FAIL: standby symphony 'sym_standby' has stop_trigger={st!r}. "
            "A null stop_trigger in bot_state must not be coerced to a non-zero value "
            "in the symphonies payload — that would display a spurious stop level."
        )


# ---------------------------------------------------------------------------
# CC-3 / CC-4  querySelectorAll for all duplicated data-fields in updateCards
# ---------------------------------------------------------------------------


class TestQuerySelectorAllForDuplicatedDataFields:
    """
    CC-3 / CC-4: index.js updateCards must use querySelectorAll (not querySelector)
    when iterating any data-field attribute within a card.

    Each sym-card in index.html has TWO spans with data-field="tc-bot":
      - One in .dual-value-headline (the big headline number)
      - One in .card-footer-grid (the small footer cell)

    querySelector returns only the FIRST match, so the headline shows +0.3% and
    the footer stays at its server-rendered +0.2% — the bug the user reported.
    querySelectorAll updates both spans from the same source value.
    """

    def test_update_cards_uses_querySelectorAll_for_tc_bot(self):
        """
        CC-3: static/index.js must call querySelectorAll (not querySelector alone)
        for the tc-bot data-field within the updateCards function body.
        """
        src = _js()
        body = _update_cards_body(src)

        # querySelectorAll for data-field tc-bot must appear inside updateCards.
        assert re.search(r"querySelectorAll\(.*?data-field.*?tc-bot", body), (
            "CC-3 FAIL: static/index.js updateCards does not use querySelectorAll "
            "for '[data-field=\"tc-bot\"]'. Using querySelector only updates the FIRST "
            "matching span — the headline shows the new value but the footer stays stale. "
            "Use querySelectorAll and iterate both spans."
        )

    def test_update_cards_does_not_use_querySelector_alone_for_tc_bot(self):
        """
        CC-3 guard: static/index.js updateCards must not use a bare querySelector
        (singular) for tc-bot — only querySelectorAll is acceptable, to avoid the
        single-match stale-footer bug.
        """
        src = _js()
        body = _update_cards_body(src)

        # Any singular querySelector for tc-bot (not querySelectorAll) is a failure.
        singular_matches = re.findall(
            r"(?<![A-Za-z])querySelector(?!All)\(.*?data-field.*?tc-bot",
            body,
        )
        assert not singular_matches, (
            "CC-3 FAIL: static/index.js updateCards uses the singular querySelector "
            "for '[data-field=\"tc-bot\"]': %r. This matches only the first span in the "
            "card, leaving the footer cell stale. Replace with querySelectorAll and "
            "forEach to update both the headline and footer spans." % singular_matches
        )

    def test_update_cards_uses_querySelectorAll_for_tc_held(self):
        """
        CC-4a: same querySelectorAll requirement for tc-held — both the headline
        and the footer held span must be updated on each poll.
        """
        src = _js()
        body = _update_cards_body(src)

        assert re.search(r"querySelectorAll\(.*?data-field.*?tc-held", body), (
            "CC-4a FAIL: static/index.js updateCards does not use querySelectorAll "
            "for '[data-field=\"tc-held\"]'. Both the headline held span and the footer "
            "held span must be updated from the same source on each poll."
        )

    def test_update_cards_uses_querySelectorAll_for_cr_bot(self):
        """CC-4b: querySelectorAll required for cr-bot."""
        src = _js()
        body = _update_cards_body(src)

        assert re.search(r"querySelectorAll\(.*?data-field.*?cr-bot", body), (
            "CC-4b FAIL: static/index.js updateCards does not use querySelectorAll "
            "for '[data-field=\"cr-bot\"]'. Use querySelectorAll to ensure all matching "
            "spans in the card are updated."
        )

    def test_update_cards_uses_querySelectorAll_for_mdd_bot(self):
        """CC-4c: querySelectorAll required for mdd-bot."""
        src = _js()
        body = _update_cards_body(src)

        assert re.search(r"querySelectorAll\(.*?data-field.*?mdd-bot", body), (
            "CC-4c FAIL: static/index.js updateCards does not use querySelectorAll "
            "for '[data-field=\"mdd-bot\"]'. Use querySelectorAll to ensure all matching "
            "spans in the card are updated."
        )


# ---------------------------------------------------------------------------
# CC-5  The triggered-verdict element is always rendered (display:none when not
#        triggered) so updateCards can show/hide it without creating DOM nodes
# ---------------------------------------------------------------------------


class TestTriggeredVerdictAlwaysRendered:
    """
    CC-5: The triggered-verdict element must be present in the DOM for every
    symphony card on page load, rendered with display:none when sym.triggered
    is false. This allows updateCards to toggle visibility via style.display
    on each poll without needing to create DOM nodes.

    The prior implementation used {% if sym.get("triggered") %} — the element
    was absent from the DOM for non-triggered symphonies. An exit that fires
    after page load produced no banner because there was nothing to show.
    """

    def test_triggered_verdict_always_rendered_not_conditionally(self):
        """
        CC-5a: templates/index.html must render the triggered-verdict element
        unconditionally — it must NOT be the DIRECT content of a
        {% if sym.get("triggered") %} block.

        The element must always be present so that JS can toggle its display.
        Acceptable: the element has an inline display:none condition (handled by
        CC-5b). Unacceptable: the element itself is inside a block that is only
        rendered when triggered=True.

        Detection: look for a pattern where {% if ...triggered... %} is immediately
        followed (within a small window, no intervening card-level structure) by the
        triggered-verdict div start tag. This is tighter than a broad DOTALL search
        that would catch the status-pill conditional which legitimately uses triggered.
        """
        html = _html()
        # The specific anti-pattern: {% if sym.get("triggered") %} directly followed
        # by data-testid="triggered-verdict" with no other block-level element between.
        # We look for the pattern within 200 chars to avoid catching the status-pill
        # conditional that is legitimately separate.
        tight_conditional_wrap = re.search(
            r'\{%\s*if\s+sym\.get\(["\']triggered["\']\)\s*%\}\s*\{#[^#]*#\}\s*'
            r'<div[^>]*?data-testid=["\']triggered-verdict["\']',
            html,
        )
        # Also check: the element open tag itself is preceded directly (within 5 lines)
        # by a raw {% if ... triggered ... %} with no intervening rendered content.
        # More robust: find every occurrence of data-testid="triggered-verdict" and
        # check the 300 chars preceding it for a bare {% if ... triggered ... %} block
        # with no closing tag between the if and the div.
        for m in re.finditer(r'data-testid=["\']triggered-verdict["\']', html):
            preceding = html[max(0, m.start() - 300) : m.start()]
            # Does the preceding window contain an unclosed {% if ...triggered... %} block?
            # Count if-blocks vs endif-blocks in the window — if there's an unmatched if
            # for triggered, the element IS inside that conditional.
            if_triggered = len(re.findall(r'\{%\s*if\s+sym\.get\(["\']triggered["\']\)', preceding))
            endif_count = len(re.findall(r"\{%\s*endif\s*%\}", preceding))
            # If there are more if-triggered blocks than endifs in the window, the
            # triggered-verdict div is inside an unclosed if block — fail.
            if if_triggered > endif_count:
                assert False, (
                    "CC-5a FAIL: templates/index.html triggered-verdict div is inside "
                    "a {% if sym.get('triggered') %} block that has no closing {% endif %} "
                    "before the div. The element must be rendered unconditionally so JS "
                    "can show/hide it via style.display — not absent from the DOM.\n"
                    f"Context: ...{preceding[-150:].strip()}...<div data-testid='triggered-verdict'...>"
                )

    def test_triggered_verdict_rendered_with_display_none_when_not_triggered(self):
        """
        CC-5b: the triggered-verdict element must carry inline display:none when
        sym.triggered is false, so it is hidden at page load for non-triggered cards.

        Acceptable: style="display:none" or style="{% if not ... %}display:none;{% endif %}"
        """
        html = _html()
        # Find the triggered-verdict div. It should have a style attribute that
        # sets display:none conditionally when not triggered.
        verdict_pattern = re.search(
            r'data-testid=["\']triggered-verdict["\'][^>]*?style=["\']([^"\']*?)["\']',
            html,
        )
        # Also allow the style to be in a Jinja inline expression.
        jinja_style_pattern = re.search(
            r'data-testid=["\']triggered-verdict["\'][^>]*?style=.*?display:none',
            html,
            re.DOTALL,
        )
        assert jinja_style_pattern is not None, (
            "CC-5b FAIL: templates/index.html triggered-verdict element does not have "
            "'display:none' in its style attribute when sym.triggered is false. "
            "Without this, all symphony cards will show a blank verdict banner on page load "
            "because the element is always rendered but its content is empty for non-triggered "
            "symphonies. The element must start hidden and be revealed only by JS."
        )

    def test_update_cards_toggles_triggered_verdict_display(self):
        """
        CC-5c: static/index.js updateCards must toggle the triggered-verdict element's
        display from sym.triggered — show it (style.display='') when triggered,
        hide it (style.display='none') when not triggered.
        """
        src = _js()
        body = _update_cards_body(src)

        # Pattern: verdict.style.display = '' / '' or 'none' based on sym.triggered
        has_display_toggle = bool(
            re.search(r"verdict.*?style\.display", body, re.DOTALL)
            or re.search(r"style\.display.*?verdict", body, re.DOTALL)
        )
        assert has_display_toggle, (
            "CC-5c FAIL: static/index.js updateCards does not toggle "
            "triggered-verdict element display via style.display. "
            "The banner must be shown (display='') when sym.triggered is true "
            "and hidden (display='none') when sym.triggered is false so that cards "
            "that exit after page load correctly show the outcome banner on the next poll."
        )

    def test_update_cards_branches_on_sym_triggered(self):
        """
        CC-5d: updateCards must branch on sym.triggered within the card update loop.
        Both the true and false branches must exist (show and hide paths).
        """
        src = _js()
        body = _update_cards_body(src)

        assert re.search(r"sym\.triggered\b", body), (
            "CC-5d FAIL: static/index.js updateCards does not reference sym.triggered. "
            "The verdict banner show/hide toggle requires reading sym.triggered from "
            "the poll response on every tick."
        )


# ---------------------------------------------------------------------------
# CC-6  Outcome banner text set from sym.guard_alpha in updateCards
# ---------------------------------------------------------------------------


class TestOutcomeBannerTextFromGuardAlpha:
    """
    CC-6: When updateCards shows the triggered-verdict banner, the text must be
    derived from sym.guard_alpha in the symphonies payload.

    The server-rendered text is stale from page load. The symphonies payload
    carries the current guard_alpha, so the banner text must be set from that.
    """

    def test_update_cards_reads_guard_alpha_from_sym_for_banner_text(self):
        """
        CC-6: static/index.js updateCards must read sym.guard_alpha and use it
        to compose the outcome banner text.
        """
        src = _js()
        body = _update_cards_body(src)

        has_guard_alpha_read = "sym.guard_alpha" in body or 'sym["guard_alpha"]' in body
        assert has_guard_alpha_read, (
            "CC-6 FAIL: static/index.js updateCards does not read sym.guard_alpha. "
            "The outcome banner text ('Good call · saved +X%α' / 'Early exit · gave "
            "up X%α') must be set from sym.guard_alpha in the poll response — not from "
            "server-rendered content that is frozen at page load."
        )

    def test_api_state_symphonies_guard_alpha_is_numeric_for_triggered(self, flask_client):
        """
        CC-6b: the guard_alpha value in the symphonies payload must be a number,
        not a string or None, for a triggered symphony — the JS formats it with
        toFixed(1) which requires a numeric operand.
        """
        client, _app, _bot_state = flask_client
        sym_map = _get_symphonies(client)

        exiting = sym_map.get("sym_exiting")
        assert exiting is not None
        assert exiting.get("triggered") is True
        ga = exiting.get("guard_alpha")
        assert ga is not None, (
            "CC-6b FAIL: guard_alpha is None/absent for triggered sym_exiting. "
            "The JS uses toFixed(1) on this value — None causes a JS TypeError."
        )
        assert isinstance(ga, (int, float)), (
            f"CC-6b FAIL: guard_alpha={ga!r} is type {type(ga).__name__}, not numeric. "
            "JS toFixed(1) requires a number."
        )


# ---------------------------------------------------------------------------
# CC-7  Hero mini-stats wired to updateMiniStats (regression guard)
# ---------------------------------------------------------------------------


class TestHeroMiniStatsLiveUpdate:
    """
    CC-7: updateMiniStats(meta) must be called from updateDashboard on every poll
    and must update hero-tracked, hero-armed, hero-triggered from meta.
    Already wired in the prior cycle — regression guard that it survives this cycle.
    """

    def test_update_dashboard_calls_update_mini_stats(self):
        """CC-7a: updateDashboard must call updateMiniStats."""
        src = _js()
        body = _update_dashboard_body(src)

        assert "updateMiniStats" in body, (
            "CC-7a FAIL: updateDashboard does not call updateMiniStats. "
            "hero-tracked / hero-armed / hero-triggered will not update on the poll."
        )

    def test_update_mini_stats_reads_meta_tracked(self):
        """CC-7b: updateMiniStats must read meta.tracked and set hero-tracked."""
        src = _js()
        start = src.find("function updateMiniStats(")
        assert start != -1, "function updateMiniStats not found in static/index.js"
        body = src[start : start + 500]

        assert "hero-tracked" in body, (
            "CC-7b FAIL: updateMiniStats does not reference 'hero-tracked'. "
            "The total-tracked count in the hero strip goes stale without this."
        )
        assert re.search(r"meta\.tracked\b", body), (
            "CC-7b FAIL: updateMiniStats does not read meta.tracked."
        )

    def test_update_mini_stats_reads_meta_armed_and_triggered(self):
        """CC-7c: updateMiniStats must read meta.armed and meta.triggered."""
        src = _js()
        start = src.find("function updateMiniStats(")
        assert start != -1
        body = src[start : start + 500]

        assert "meta.armed" in body, "CC-7c FAIL: updateMiniStats does not read meta.armed."
        assert "meta.triggered" in body, "CC-7c FAIL: updateMiniStats does not read meta.triggered."

    def test_api_state_meta_carries_tracked_armed_triggered(self, flask_client):
        """
        CC-7d: /api/state must return a 'meta' object with tracked, armed, triggered
        counts so updateMiniStats can update the hero strip.
        """
        client, _app, _bot_state = flask_client
        resp = client.get("/api/state")
        assert resp.status_code == 200
        body = resp.get_json()

        assert "meta" in body, "CC-7d FAIL: /api/state missing 'meta' key"
        meta = body["meta"]
        for field in ("tracked", "armed", "triggered"):
            assert field in meta, (
                f"CC-7d FAIL: meta missing '{field}' — hero strip cannot update "
                f"hero-{field} count without it."
            )
            assert isinstance(meta[field], (int, float)), (
                f"CC-7d FAIL: meta.{field}={meta[field]!r} must be numeric."
            )


# ---------------------------------------------------------------------------
# CC-8  Market-state dot and strip chips regression guard
# ---------------------------------------------------------------------------


class TestMarketStateAndStripChipsUpdate:
    """
    CC-8: updateStatusStrip and updateMarketDot must still be called from
    updateDashboard after this cycle's changes — regression guard.
    """

    def test_update_dashboard_calls_update_status_strip(self):
        """CC-8a: updateDashboard must call updateStatusStrip."""
        src = _js()
        body = _update_dashboard_body(src)

        assert "updateStatusStrip" in body, (
            "CC-8a FAIL: updateDashboard does not call updateStatusStrip. "
            "The market label + trigger-count chips will not update on the poll."
        )

    def test_update_dashboard_calls_update_market_dot(self):
        """CC-8b: updateDashboard must call updateMarketDot."""
        src = _js()
        body = _update_dashboard_body(src)

        assert "updateMarketDot" in body, (
            "CC-8b FAIL: updateDashboard does not call updateMarketDot. "
            "The open/closed dot indicator will not update on the poll."
        )

    def test_update_status_strip_reads_meta_market_state_label(self):
        """CC-8c: updateStatusStrip must read meta.market_state_label."""
        src = _js()
        start = src.find("function updateStatusStrip(")
        assert start != -1, "function updateStatusStrip not found in static/index.js"
        body = src[start : start + 500]

        assert "market_state_label" in body, (
            "CC-8c FAIL: updateStatusStrip does not read meta.market_state_label."
        )




# ---------------------------------------------------------------------------
# CC-10  Regression guards (sym-table-container; structure preserved)
# ---------------------------------------------------------------------------


class TestRegressionGuards:
    """
    CC-10: Regression guards from the prior broken cycle — must survive this cycle.
    """

    def test_no_sym_table_container_in_html(self):
        """CC-10a: templates/index.html must not contain 'sym-table-container'."""
        assert "sym-table-container" not in _html(), (
            "CC-10a FAIL: templates/index.html contains 'sym-table-container'. "
            "This was the injection point for the prior broken dark-table pattern."
        )

    def test_no_sym_table_container_in_js(self):
        """CC-10b: static/index.js must not reference 'sym-table-container'."""
        assert "sym-table-container" not in _js(), (
            "CC-10b FAIL: static/index.js references 'sym-table-container'."
        )

    def test_no_html_field_injection_in_js(self):
        """CC-10c: static/index.js must not assign data.html to innerHTML."""
        src = _js()
        bad = [
            re.compile(r"innerHTML\s*=\s*data\.html\b", re.MULTILINE),
            re.compile(r"""innerHTML\s*=\s*data\[['"]html['"]\]""", re.MULTILINE),
        ]
        for pat in bad:
            m = pat.search(src)
            assert m is None, (
                "CC-10c FAIL: static/index.js assigns data.html to innerHTML — "
                "the prior broken pattern that injected the dark table. "
                f"Found: {src[max(0, m.start() - 40) : m.end() + 40]!r}"
            )

    @pytest.mark.parametrize(
        "marker",
        [
            'class="cards-grid"',
            'data-testid="sym-card"',
            'data-testid="card-spark"',
        ],
    )
    def test_html_preserves_card_markup_structure(self, marker: str):
        """
        CC-10d: core card markup markers must survive the implementer's changes.
        cards-grid, sym-card, and card-spark are structural anchors.
        """
        assert marker in _html(), (
            f"CC-10d FAIL: templates/index.html is missing required marker {marker!r}. "
            "Card markup must be updated in-place — not replaced or renamed."
        )

    def test_js_does_not_replace_sym_card_inner_html(self):
        """
        CC-10e: static/index.js must not assign innerHTML on a sym-card element.
        Whole-card innerHTML replacement destroys sparklines, settings buttons,
        and other in-card elements that are not part of the value update.
        """
        src = _js()
        # Match: (sym|symCard|card|cardEl).innerHTML = ... where the left side
        # refers to a card-level variable, not a targeted child span.
        bad = re.findall(
            r'(?:sym|symCard|card|cardEl)\b.*?\.innerHTML\s*=\s*(?!null|\'\'|"")',
            src,
        )
        assigns = [m for m in bad if "innerHTML =" in m or "innerHTML=" in m]
        assert not assigns, (
            "CC-10e FAIL: static/index.js assigns innerHTML on a card-level variable. "
            "This replaces the entire card and destroys sparklines, buttons, etc. "
            "Update only the targeted data-field child spans."
        )


# ---------------------------------------------------------------------------
# CC-11  Alpha-badge spans carry data-field for live update
# ---------------------------------------------------------------------------


class TestAlphaBadgeDataField:
    """
    CC-11: The α-delta badge spans in .card-footer-grid must carry data-field
    attributes (tc-alpha-badge, cr-alpha-badge, mdd-alpha-badge) so that
    updateCards can refresh the displayed α number on each poll.

    Without data-field hooks, the α delta shows the page-load computed value
    even as tc_bot/tc_held diverge from their server-rendered state.
    """

    def test_tc_alpha_badge_has_data_field(self):
        """CC-11a: the Today α badge must have data-field='tc-alpha-badge'."""
        html = _html()
        assert re.search(r'data-field=["\']tc-alpha-badge["\']', html), (
            "CC-11a FAIL: templates/index.html Today α badge missing "
            "data-field='tc-alpha-badge'. updateCards cannot refresh the α delta "
            "without this hook."
        )

    def test_cr_alpha_badge_has_data_field(self):
        """CC-11b: the Cum α badge must have data-field='cr-alpha-badge'."""
        html = _html()
        assert re.search(r'data-field=["\']cr-alpha-badge["\']', html), (
            "CC-11b FAIL: templates/index.html Cum α badge missing data-field='cr-alpha-badge'."
        )

    def test_mdd_alpha_badge_has_data_field(self):
        """CC-11c: the Max DD α badge must have data-field='mdd-alpha-badge'."""
        html = _html()
        assert re.search(r'data-field=["\']mdd-alpha-badge["\']', html), (
            "CC-11c FAIL: templates/index.html Max DD α badge missing data-field='mdd-alpha-badge'."
        )

    def test_update_cards_updates_tc_alpha_badge(self):
        """CC-11d: updateCards must update the tc-alpha-badge span on each poll."""
        src = _js()
        body = _update_cards_body(src)

        assert "tc-alpha-badge" in body, (
            "CC-11d FAIL: static/index.js updateCards does not reference 'tc-alpha-badge'. "
            "The Today α delta badge will show stale server-rendered α until a hard refresh."
        )

    def test_update_cards_updates_cr_alpha_badge(self):
        """CC-11e: updateCards must update the cr-alpha-badge span on each poll."""
        src = _js()
        body = _update_cards_body(src)

        assert "cr-alpha-badge" in body, (
            "CC-11e FAIL: static/index.js updateCards does not reference 'cr-alpha-badge'."
        )

    def test_update_cards_updates_mdd_alpha_badge(self):
        """CC-11f: updateCards must update the mdd-alpha-badge span on each poll."""
        src = _js()
        body = _update_cards_body(src)

        assert "mdd-alpha-badge" in body, (
            "CC-11f FAIL: static/index.js updateCards does not reference 'mdd-alpha-badge'."
        )


# ---------------------------------------------------------------------------
# CC-12  Section count badges and hero data-as-of update from poll data
# ---------------------------------------------------------------------------


class TestSectionMetaLiveUpdate:
    """
    CC-12: The section count badges (active-section-count, standby-section-count)
    and the hero chart legend "data as of" text must update from the poll response
    on each tick, not just on page load.

    These values change during market hours as symphonies arm/trigger/reset.
    Server-rendered counts go stale immediately after the first engine cycle.
    """

    def test_update_section_meta_updates_active_section_count(self):
        """
        CC-12a: the active-section-count badge must be updated from the poll response.
        Either updateSectionMeta (or an equivalent) must reference active-section-count.
        """
        src = _js()
        assert "active-section-count" in src, (
            "CC-12a FAIL: static/index.js does not reference 'active-section-count'. "
            "The active-symphonies section header badge will show a stale count until "
            "a hard refresh. The JS update path must target this element by testid."
        )

    def test_update_section_meta_updates_standby_section_count(self):
        """CC-12b: the standby-section-count badge must be updated from the poll response."""
        src = _js()
        assert "standby-section-count" in src, (
            "CC-12b FAIL: static/index.js does not reference 'standby-section-count'. "
            "The standby-symphonies section header badge will show a stale count."
        )

    def test_update_dashboard_calls_update_section_meta(self):
        """
        CC-12c: updateDashboard must call updateSectionMeta (or equivalent inline code)
        to keep section counts current on each poll.
        """
        src = _js()
        body = _update_dashboard_body(src)

        has_section_meta_update = (
            "updateSectionMeta" in body
            or "active-section-count" in body
            or "standby-section-count" in body
        )
        assert has_section_meta_update, (
            "CC-12c FAIL: updateDashboard does not call updateSectionMeta or update "
            "active-section-count / standby-section-count inline. These section count "
            "badges go stale after the first engine cycle unless refreshed on each poll."
        )

    def test_hero_data_as_of_element_has_id_for_js_update(self):
        """
        CC-12d: the hero chart legend 'data as of' span must have id='hero-data-as-of'
        so that updateSectionMeta (or equivalent) can update it from meta.portfolio.data_as_of.
        """
        html = _html()
        assert 'id="hero-data-as-of"' in html, (
            "CC-12d FAIL: templates/index.html hero chart legend 'data as of' span "
            "is missing id='hero-data-as-of'. Without this id the JS cannot update "
            "the data-as-of timestamp on each poll — it stays at the page-load value."
        )
