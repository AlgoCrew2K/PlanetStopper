"""RED tests — AC-7 + AC-8: data freshness visibility (dashboard-realtime-push).

These two ACs were added from the live-dashboard-reality-audit (2026-06-23):

AC-7 (true data-age, not render clock):
  `data_as_of` in portfolio_strip is currently set to `datetime.now(_ET)` at
  app.py:1142 and app.py:1619. It always looks current regardless of engine state.
  Fix: derive `data_as_of` from `last_successful_cycle_at` (from bot_state) or
  MAX(shadow_history.ts_utc) — the real age of the data, not the server render clock.

AC-8 (visible staleness on fetch failure):
  static/index.js:1296 `.catch` is console-only. The Live/Stale badge and "data as of"
  keep their last-rendered values on a poll error — the page looks alive and current
  with frozen numbers. Fix: on SSE/poll failure, surface a visible staleness cue
  (flip the badge to a connection-lost/stale state) independently of a successful payload.

These tests fail until rt-impl:
  - Changes app.py:1142 and app.py:1619 to derive data_as_of from bot_state
    last_successful_cycle_at or MAX(shadow_history.ts_utc)
  - Changes static/index.js:1296 to surface a visible error cue (not just console.error)
    and/or tracks lastSuccessfulPollAt independently

No live engine, no real DB, no live API calls.
All bot_state data is injected via fixture/patch.
"""

from __future__ import annotations

import pathlib
import re
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"
_INDEX_JS = _STATIC_DIR / "index.js"
_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
_CHROME_HTML = _TEMPLATES_DIR / "_chrome.html"
_INDEX_HTML = _TEMPLATES_DIR / "index.html"


@pytest.fixture(autouse=True)
def _clear_account_totals_cache():
    """Clear _account_totals_cache before and after every test in this module.

    Prevents _StaleFlagDict state (stale flag or cached values) from bleeding
    into tests in other modules via the module-level cache object.
    """
    app_module._account_totals_cache.clear()
    yield
    app_module._account_totals_cache.clear()


@pytest.fixture(scope="module")
def index_js_source() -> str:
    """Read static/index.js once for the module."""
    assert _INDEX_JS.exists(), f"static/index.js not found at {_INDEX_JS}."
    return _INDEX_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-7 helpers
# ---------------------------------------------------------------------------

_FAKE_CYCLE_TS = "2026-06-23T10:09:00"  # last_successful_cycle_at from the audit
_FAKE_CYCLE_LABEL = "10:09"  # what the timestamp should produce (HH:MM)


def _bot_state_with_cycle_at(cycle_ts: str) -> dict:
    """Minimal bot_state with last_successful_cycle_at at the TOP LEVEL.

    The engine writes this key at the top level of bot_state, never inside a
    per-symphony sub-dict.  See alpha_bot_execution.py:948/1092/1878:
        bot_state["last_successful_cycle_at"] = current_et.isoformat()

    Placing the key at the top level makes _compute_portfolio_strip RED against
    the current per-sym-dict loop reader (app.py:1281-1287) and GREEN only after
    the loop is replaced with bot_state.get("last_successful_cycle_at").
    """
    return {
        "sym-1": {
            "name": "Symphony 1",
            "current_value": 1000.0,
            "current_return": 1.5,
            "simple_return": 0.05,
            "net_deposits": 800.0,
            "time_weighted_return": 0.06,
            "max_drawdown": 0.12,
            # NOT here — engine never writes this into per-sym dicts.
        },
        "last_successful_cycle_at": cycle_ts,  # top-level, production shape
    }


# ---------------------------------------------------------------------------
# AC-7: data_as_of derives from real data age
# ---------------------------------------------------------------------------


class TestDataAsOfReflectsRealDataAge:
    def test_compute_portfolio_strip_data_as_of_is_not_always_server_render_clock(self):
        """_compute_portfolio_strip must derive data_as_of from the real data age,
        NOT from datetime.now(_ET) at the moment of the request.

        Regression guard for app.py:1142. Currently: `data_as_of = datetime.now(_ET).strftime(...)`.
        After fix: should equal the HH:MM of the bot_state last_successful_cycle_at
        (or MAX shadow_history.ts_utc), NOT the server render time.

        This test injects a bot_state with a known last_successful_cycle_at and asserts
        that data_as_of does NOT simply equal the current server render time.
        If data_as_of ALWAYS equals now(), a wrong implementation would pass this test
        only by coincidence — we use a timestamp from the past to make the distinction clear.

        Fails until rt-impl derives data_as_of from the data timestamp, not now().
        """
        # The fake cycle timestamp is ~10:09 ET — clearly different from the current server time
        # unless this test happens to run exactly at 10:09 ET, which is astronomically unlikely.
        bot_state = _bot_state_with_cycle_at(_FAKE_CYCLE_TS)

        result = app_module._compute_portfolio_strip(bot_state)

        data_as_of = result.get("data_as_of", "")
        assert isinstance(data_as_of, str), (
            f"data_as_of must be a string; got {type(data_as_of).__name__}."
        )

        # The key assertion: data_as_of must NOT equal the current-server-render-clock HH:MM.
        # We capture "now" before and after the call and reject any value in that window.
        from datetime import datetime

        try:
            from zoneinfo import ZoneInfo

            _ET = ZoneInfo("America/New_York")
        except ImportError:
            import pytz

            _ET = pytz.timezone("America/New_York")

        now_hhmm = datetime.now(_ET).strftime("%H:%M")
        # If the render-clock HH:MM appears in data_as_of but the fake cycle label
        # does NOT appear, the implementation is using datetime.now() — flag it.
        # Note: now_hhmm is compared as a substring because data_as_of may include
        # the " ET" suffix, e.g. "12:48 ET".
        if now_hhmm in data_as_of and _FAKE_CYCLE_LABEL not in data_as_of:
            pytest.fail(
                f"data_as_of='{data_as_of}' encodes the server render clock ({now_hhmm} ET) "
                f"rather than the real data age (last_successful_cycle_at ~ {_FAKE_CYCLE_LABEL} ET). "
                "rt-impl: change app.py:1142 to derive data_as_of from "
                "bot_state last_successful_cycle_at or MAX(shadow_history.ts_utc), "
                "not datetime.now(_ET)."
            )

    def test_compute_portfolio_strip_data_as_of_reflects_last_successful_cycle_at(self):
        """When bot_state contains last_successful_cycle_at='2026-06-23T10:09:00',
        data_as_of must encode that timestamp — not the server render time.

        The format is flexible (HH:MM ET, ISO, etc.) but must encode 10:09,
        not the current clock time.

        Fails until rt-impl reads last_successful_cycle_at from bot_state.
        """
        bot_state = _bot_state_with_cycle_at(_FAKE_CYCLE_TS)

        result = app_module._compute_portfolio_strip(bot_state)

        data_as_of = result.get("data_as_of", "")

        # Must contain "10:09" (the cycle timestamp's HH:MM component)
        assert "10:09" in data_as_of, (
            f"data_as_of='{data_as_of}' does not encode the cycle timestamp "
            f"(last_successful_cycle_at={_FAKE_CYCLE_TS} → expected '10:09'). "
            "rt-impl: change app.py:1142 to parse last_successful_cycle_at and "
            "format it as 'HH:MM ET' (or similar), replacing datetime.now(_ET)."
        )

    def test_get_state_portfolio_strip_data_as_of_not_always_now(self, client):
        """/api/state portfolio_strip.data_as_of must NOT always equal the server render clock.

        End-to-end test that the route-level data_as_of (app.py:1619) also reflects
        real data age, not now().

        We inject a known last_successful_cycle_at via the bot_state stub and assert
        that data_as_of does NOT match the current wall-clock HH:MM.

        Fails until rt-impl fixes app.py:1619 (the second data_as_of = datetime.now(...)).
        """
        from datetime import datetime

        import database as db

        try:
            from zoneinfo import ZoneInfo

            _ET = ZoneInfo("America/New_York")
        except ImportError:
            import pytz

            _ET = pytz.timezone("America/New_York")

        # Write a known bot_state row so get_state() will pick it up.
        db.save_state(
            {
                "sym-test": {
                    "name": "Test Symphony",
                    "current_value": 1000.0,
                    "current_return": 1.0,
                    "simple_return": 0.01,
                    "net_deposits": 900.0,
                    "time_weighted_return": 0.02,
                    "max_drawdown": 0.05,
                    "last_successful_cycle_at": _FAKE_CYCLE_TS,
                }
            }
        )

        response = client.get("/api/state")
        assert response.status_code == 200, (
            f"GET /api/state returned {response.status_code}; expected 200."
        )

        data = response.get_json()
        if not isinstance(data, dict):
            return  # empty/error state — can't check data_as_of

        port_strip = data.get("portfolio_strip")
        if not isinstance(port_strip, dict):
            return  # no strip built — depends on DB state; skip shape check

        data_as_of = port_strip.get("data_as_of", "")
        if not data_as_of:
            return  # absent — fine, but no render-clock check needed

        now_hhmm = datetime.now(_ET).strftime("%H:%M")
        if data_as_of == now_hhmm and "10:09" not in data_as_of:
            pytest.fail(
                f"GET /api/state portfolio_strip.data_as_of='{data_as_of}' matches "
                f"server render clock ({now_hhmm} ET) not the data timestamp "
                f"(last_successful_cycle_at ~ 10:09 ET). "
                "rt-impl: fix app.py:1619 to derive data_as_of from the real data age."
            )

    def test_compute_portfolio_strip_ignores_per_sym_cycle_ts_no_top_level(self):
        """Negative guard: when last_successful_cycle_at is ONLY inside a per-symphony
        sub-dict (production-impossible shape) and absent from the top level, the reader
        must NOT use it — it must fall back to datetime.now().

        This closes the "defensive OR-fallback" regression class.  A future refactor
        that re-adds a per-sym fallback (e.g.
            `state.get("last_successful_cycle_at") or <loop over sym dicts>`)
        would re-honor the production-impossible shape and still pass the positive tests
        (which all supply the key at the correct top level).  This test would fail it.

        Shape injected here: last_successful_cycle_at inside "sym-1" ONLY, no top-level
        key — exactly the hollow-fixture shape we removed from the positive tests.

        Must be GREEN immediately (current fixed reader uses top-level .get, finds nothing,
        falls back to datetime.now()).  Would go RED if a per-sym fallback were re-introduced.
        """
        # Production-impossible shape: key buried in per-sym dict, absent at top level.
        bot_state_wrong_shape = {
            "sym-1": {
                "name": "Symphony 1",
                "current_value": 1000.0,
                "current_return": 1.5,
                "simple_return": 0.05,
                "net_deposits": 800.0,
                "time_weighted_return": 0.06,
                "max_drawdown": 0.12,
                "last_successful_cycle_at": _FAKE_CYCLE_TS,  # wrong location — per-sym
            }
            # Intentionally NO top-level "last_successful_cycle_at".
        }

        result = app_module._compute_portfolio_strip(bot_state_wrong_shape)

        data_as_of = result.get("data_as_of", "")
        assert isinstance(data_as_of, str), (
            f"data_as_of must be a string; got {type(data_as_of).__name__}."
        )

        # The key assertion: the per-sym key must NOT be honored.
        # The reader should fall back to datetime.now() because no top-level key exists.
        # We can't assert the exact now() value, but we CAN assert that the
        # production-impossible per-sym shape was ignored (10:09 must NOT appear).
        assert _FAKE_CYCLE_LABEL not in data_as_of, (
            f"data_as_of='{data_as_of}' encodes '{_FAKE_CYCLE_LABEL}' from a "
            f"per-symphony-dict last_successful_cycle_at — the reader is honoring a "
            "production-impossible shape.  The reader must use ONLY the top-level key "
            "bot_state.get('last_successful_cycle_at'), never a per-sym sub-dict lookup. "
            "Regression: a per-sym fallback was re-introduced at app.py _compute_portfolio_strip."
        )


# ---------------------------------------------------------------------------
# AC-8: visible staleness cue on poll/SSE failure
# ---------------------------------------------------------------------------


class TestVisibleStalenessCueOnFetchFailure:
    def test_index_js_does_not_have_console_only_catch_in_loadstate(self, index_js_source: str):
        """static/index.js loadState() must not have a catch that is ONLY console.error.

        The current implementation (app.py audit line 1296):
            .catch(function (err) { console.error('state load failed', err); });
        provides no visible staleness cue to the operator — the page looks alive
        with frozen numbers.

        After the fix, the catch must surface a visible cue (badge flip, text update,
        or lastSuccessfulPollAt tracking that drives an independent staleness timer).

        This test asserts that the catch block is NOT purely console-only by checking
        that there is something beyond `console.error` in the loadState error path.

        Fails until rt-impl adds a visible cue (badge update, DOM write, or tracker).
        """
        # Find the loadState function region in the source
        loadstate_idx = index_js_source.find("function loadState(")
        assert loadstate_idx >= 0, (
            "static/index.js must contain `function loadState(` — it is the poll loop function."
        )

        # Extract the loadState function body (up to the next top-level function definition)
        # by finding the closing brace pattern. We take a generous window.
        window = index_js_source[loadstate_idx : loadstate_idx + 600]

        # Assert the catch block does MORE than just console.error.
        # Acceptable patterns: a DOM write, a badge update call, a variable assignment,
        # showStaleIndicator, updateStaleState, lastSuccessfulPollAt, etc.
        # We reject the pattern where catch body is SOLELY `console.error(...)`.
        catch_idx = window.find(".catch(")
        assert catch_idx >= 0, (
            "static/index.js loadState() must have a .catch() handler (AC-5/AC-8)."
        )

        catch_body = window[catch_idx : catch_idx + 200]

        # Patterns that indicate a purely console-only catch (wrong):
        # .catch(function (err) { console.error(...); });
        # .catch(function(err){console.error(...)})
        is_console_only = bool(
            re.search(
                r"\.catch\s*\(\s*function\s*\([^)]*\)\s*\{\s*console\.error\([^}]*\)\s*;\s*\}",
                catch_body,
            )
        )
        assert not is_console_only, (
            "static/index.js loadState().catch() is console-only — no visible staleness cue. "
            "Audit evidence: index.js:1296. On a fetch error the badge, 'data as of', and "
            "hero numbers keep their last-rendered values with no cue for the operator. "
            "rt-impl: add a visible staleness indicator in the catch block, e.g.: "
            "call a showConnectionLost() function, or track lastSuccessfulPollAt "
            "and add an independent badge-staleness timer."
        )

    def test_index_js_has_visible_stale_indicator_on_poll_failure(self, index_js_source: str):
        """static/index.js must have a visible staleness indicator triggered on poll failure.

        Acceptable implementations:
        - A function like showConnectionLost(), showStaleIndicator(), markStale(), etc.
        - A DOM write (element.textContent, element.classList) in the catch path
        - A lastSuccessfulPollAt tracker combined with an independent setInterval staleness check
        - Any pattern that changes visible DOM state on fetch failure

        Fails until rt-impl adds visible-stale machinery to static/index.js.
        """
        # Look for any of the acceptable visible-stale patterns anywhere in the file.
        stale_patterns = [
            "showConnectionLost",
            "showStaleIndicator",
            "markStale",
            "connectionLost",
            "staleIndicator",
            "lastSuccessfulPollAt",
            "lastSuccessfulUpdate",
            # DOM manipulation in an error handler
            "stale-banner",
            "connection-lost",
            "data-stale",
        ]
        found = any(p in index_js_source for p in stale_patterns)
        assert found, (
            "static/index.js must implement a visible staleness cue for poll/SSE failure. "
            "None of the expected patterns were found: " + str(stale_patterns) + ". "
            "rt-impl: add e.g. `lastSuccessfulPollAt` tracking + an independent badge-staleness "
            "timer, OR a `showConnectionLost()` DOM-write called in the loadState .catch path."
        )

    def test_index_js_has_independent_staleness_timer_or_tracker(self, index_js_source: str):
        """static/index.js must track last-successful-update time independently of the poll.

        The badge must be able to go Stale even if every poll call fails (fetch error,
        OS-throttled background tab, daemon restart). This requires tracking the LAST
        successful update time (from either the SSE cycle-complete event or a successful
        poll) and checking it on an independent timer or on the next SSE event.

        Acceptable: any variable name containing 'lastSuccessful', 'lastUpdate', 'lastPoll',
        or 'lastEvent', or a function that reads a stored timestamp to drive staleness.

        Fails until rt-impl adds independent staleness tracking.
        """
        tracker_patterns = [
            "lastSuccessful",
            "lastUpdate",
            "lastPoll",
            "lastEvent",
            "lastFetch",
            "_lastOk",
            "lastOkAt",
        ]
        found = any(p in index_js_source for p in tracker_patterns)
        assert found, (
            "static/index.js must track the last-successful-update timestamp independently "
            "of the poll (e.g. `var lastSuccessfulPollAt = 0;` + `lastSuccessfulPollAt = Date.now()` "
            "inside the .then success path + stale check in the .catch or on a setInterval). "
            "None of the expected patterns found: " + str(tracker_patterns) + ". "
            "rt-impl: add this tracker so the badge can go Stale even when every poll errors."
        )

    def test_show_connection_lost_targets_real_dom_ids(self, index_js_source: str):
        """showConnectionLost() in static/index.js must reference element ids that
        actually exist in the rendered templates — bidirectional JS↔template contract.

        This test enforces BOTH directions of the contract:

        Direction 1 (JS → template): the ids used by showConnectionLost() must match
        the ids present in templates/_chrome.html and templates/index.html.

        Direction 2 (template → JS): the ids present in the templates must be
        referenced by showConnectionLost() so a template rename is caught immediately.

        DEFECT (index.js:1299-1310):
          - getElementById('engine-status-badge')  →  no such id exists
              Real ids in _chrome.html:51-53: 'engine-status', 'engine-status-dot',
              'engine-status-label'
          - querySelector('[data-testid="data-as-of"]') || querySelector('.data-as-of')
              →  no element with that testid or class exists
              Real element in templates/index.html:846: id='hero-data-as-of'

        After rt-impl fixes showConnectionLost() to use the real selectors, this test
        passes. A future template rename that breaks the contract will also be caught here.

        Fails until rt-impl changes index.js:1300/1305-1306 to target real ids.
        """
        # --- Extract showConnectionLost function body from index.js ---
        fn_start = index_js_source.find("function showConnectionLost(")
        assert fn_start >= 0, (
            "static/index.js must contain `function showConnectionLost(` — it is the "
            "AC-8 visible-staleness function."
        )
        # Take a generous window covering the function body (up to ~500 chars).
        fn_body = index_js_source[fn_start : fn_start + 500]

        # --- Read template source files (fail fast if missing) ---
        assert _CHROME_HTML.exists(), (
            f"templates/_chrome.html not found at {_CHROME_HTML}. "
            "Required for JS↔template contract check."
        )
        assert _INDEX_HTML.exists(), (
            f"templates/index.html not found at {_INDEX_HTML}. "
            "Required for JS↔template contract check."
        )
        chrome_src = _CHROME_HTML.read_text(encoding="utf-8")
        index_html_src = _INDEX_HTML.read_text(encoding="utf-8")

        # --- Contract check: badge element ids ---
        # The badge cluster in _chrome.html uses three ids.  showConnectionLost must
        # reference at least one of them — and that id must actually exist in the template.
        # The WRONG id ('engine-status-badge') must NOT be the only badge reference.
        badge_ids_in_template = []
        for bid in ("engine-status-dot", "engine-status-label", "engine-status"):
            if f'id="{bid}"' in chrome_src or f"id='{bid}'" in chrome_src:
                badge_ids_in_template.append(bid)

        assert badge_ids_in_template, (
            "templates/_chrome.html contains none of the expected badge element ids "
            "('engine-status-dot', 'engine-status-label', 'engine-status'). "
            "This test expects those ids to exist in the template. "
            "If _chrome.html was refactored, update this test and showConnectionLost together."
        )

        js_refs_a_real_badge_id = any(bid in fn_body for bid in badge_ids_in_template)
        wrong_badge_id = "engine-status-badge"
        js_only_has_wrong_id = (wrong_badge_id in fn_body) and not js_refs_a_real_badge_id

        assert js_refs_a_real_badge_id, (
            f"showConnectionLost() does not reference any real badge element id. "
            f"Real ids in templates/_chrome.html: {badge_ids_in_template}. "
            + (
                f"Found wrong id '{wrong_badge_id}' instead — this id does not exist in the template. "
                if js_only_has_wrong_id
                else ""
            )
            + "rt-impl: change index.js showConnectionLost() to target "
            "getElementById('engine-status-dot') and/or getElementById('engine-status-label') "
            "(matching _chrome.html:51-53)."
        )

        # --- Contract check: data-as-of element id ---
        # templates/index.html:846 uses id='hero-data-as-of'.  showConnectionLost must
        # reference that id.  The WRONG selectors ('[data-testid="data-as-of"]' and
        # '.data-as-of') must not be the only data-as-of references.
        dao_id = "hero-data-as-of"
        dao_id_in_template = (
            f'id="{dao_id}"' in index_html_src or f"id='{dao_id}'" in index_html_src
        )

        assert dao_id_in_template, (
            f"templates/index.html does not contain id='{dao_id}'. "
            "This test expects that id to exist (templates/index.html:846 in the audit). "
            "If index.html was refactored, update this test and showConnectionLost together."
        )

        js_refs_dao_id = dao_id in fn_body
        wrong_dao_patterns = ['data-testid="data-as-of"', "data-testid='data-as-of'", ".data-as-of"]
        js_only_has_wrong_dao = any(p in fn_body for p in wrong_dao_patterns) and not js_refs_dao_id

        assert js_refs_dao_id, (
            f"showConnectionLost() does not reference the real data-as-of element id "
            f"('{dao_id}'). "
            + (
                "Found wrong selectors ('[data-testid=\"data-as-of\"]' / '.data-as-of') instead — "
                "no element with that testid or class exists in the template. "
                if js_only_has_wrong_dao
                else ""
            )
            + "rt-impl: change index.js showConnectionLost() to target "
            f"getElementById('{dao_id}') (matching templates/index.html:846)."
        )


# ---------------------------------------------------------------------------
# AC-7 (BLOCK-B): snapshot branch data_as_of regression guard
# ---------------------------------------------------------------------------

_SNAP_DATA_AS_OF = "10:09 ET"
_SNAP_TRADING_DAY = "2026-06-23"
_SNAP_CAPTURED_AT_ET = "10:09:00 ET"


class TestSnapshotBranchDataAsOfUsesSnapshotTimestamp:
    """Regression guard: when /api/state serves a frozen market-close snapshot
    (market_state in ('closed_frozen', 'pre_market')), portfolio_strip.data_as_of
    must reflect the snapshot's own captured timestamp — not the server render clock.

    The snapshot branch at app.py lines ~1776 and ~1792 uses
    `snapshot.get('data_as_of')` (correct) rather than `datetime.now()` (wrong).
    This test exercises that path end-to-end so a regression back to `datetime.now()`
    is caught immediately.

    The test fails if data_as_of matches the current server clock instead of the
    known snapshot timestamp ('10:09 ET').
    """

    def test_snapshot_branch_data_as_of_matches_snapshot_not_render_clock(self, client):
        """/api/state in closed_frozen state must serve portfolio_strip.data_as_of
        from the snapshot's `data_as_of` field, not the server render clock.

        Scenario:
        1. Write a last_market_close_snapshot into the DB with data_as_of='10:09 ET'.
        2. Patch get_market_state to return 'closed_frozen' so the snapshot branch fires.
        3. GET /api/state.
        4. Assert portfolio_strip.data_as_of == '10:09 ET' (the snapshot's value).

        Would FAIL if the snapshot branch used datetime.now() — the current HH:MM
        would not match '10:09 ET' (unless the test happens to run at exactly 10:09,
        which is ~1/720 chance and caught by the clock-match guard below).

        Regression guard for app.py:1776 / app.py:1792 (`snapshot.get('data_as_of')`).
        """
        import database as db

        # Write a known snapshot into the per-test isolated DB.
        # The snapshot branch reads last_market_close_snapshot from load_state().
        snapshot = {
            "trading_day": _SNAP_TRADING_DAY,
            "captured_at_et": _SNAP_CAPTURED_AT_ET,
            "data_as_of": _SNAP_DATA_AS_OF,
            # Minimal accounts_map so the strip builder has something to iterate.
            "accounts_map": {
                "test-account": [
                    {
                        "id": "sym-snap-test",
                        "name": "Snap Symphony",
                        "current_return": 5.0,
                        "current_value": 10000.0,
                        "simple_return": 0.05,
                        "net_deposits": 9500.0,
                        "time_weighted_return": 0.06,
                        "max_drawdown": 0.03,
                    }
                ]
            },
            "shadow_divergence": {"by_symphony": {}, "portfolio": None},
            "portfolio_strip": None,
        }
        db.save_state({"last_market_close_snapshot": snapshot})

        # Force the snapshot branch: patch get_market_state in app's namespace.
        with patch("app.get_market_state", return_value="closed_frozen"):
            response = client.get("/api/state")

        assert response.status_code == 200, (
            f"GET /api/state returned {response.status_code}; expected 200."
        )

        data = response.get_json()
        assert isinstance(data, dict), (
            f"Expected dict response from /api/state, got {type(data)!r}."
        )

        port_strip = data.get("portfolio_strip")
        assert isinstance(port_strip, dict), (
            f"portfolio_strip missing or non-dict in /api/state response: {data!r}. "
            "The snapshot branch should produce a portfolio_strip from the snapshot data."
        )

        data_as_of = port_strip.get("data_as_of")
        assert data_as_of is not None, (
            "portfolio_strip.data_as_of is None in snapshot-branch response. "
            f"Expected '{_SNAP_DATA_AS_OF}' (from snapshot). "
            "rt-impl: snapshot branch must propagate snapshot['data_as_of'] "
            "not produce None."
        )

        # Guard: if test runs exactly at 10:09 ET, the clock-match would be a
        # false pass. Detect this edge case and skip rather than give a false green.
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            _ET = ZoneInfo("America/New_York")
        except ImportError:
            import pytz

            _ET = pytz.timezone("America/New_York")

        now_hhmm_et = datetime.now(_ET).strftime("%H:%M ET")
        if now_hhmm_et == _SNAP_DATA_AS_OF:
            import pytest

            pytest.skip(
                f"Test skipped: current ET time ({now_hhmm_et}) matches the snapshot "
                f"timestamp ({_SNAP_DATA_AS_OF}) — cannot distinguish snapshot vs clock source."
            )

        assert data_as_of == _SNAP_DATA_AS_OF, (
            f"portfolio_strip.data_as_of='{data_as_of}' does not match the snapshot "
            f"timestamp '{_SNAP_DATA_AS_OF}'. "
            "The snapshot branch must use snapshot.get('data_as_of') not datetime.now(). "
            "Regression target: app.py lines ~1776 and ~1792 in the closed_frozen branch."
        )


# ---------------------------------------------------------------------------
# AC-7: top-level data_as_of in /api/state live-market path (app.py ~2117/2230)
#
# Scope decision: IN-SCOPE (PM ratified 2026-06-23).
#   app.py:2117 `data_as_of` is the JS fallback hero freshness signal:
#   index.js:1168 reads `portfolio.data_as_of || data.data_as_of`.
#   When bot_state carries a `last_successful_cycle_at`, the top-level
#   data_as_of must derive from that timestamp, not the render clock.
#   Fixed at b215049: same last_successful_cycle_at pattern as
#   _compute_portfolio_strip (app.py:1281-1303). Also fixes the pre-existing
#   naive datetime.now() (no _ET timezone) bug.
#
#   OUT-OF-SCOPE (exception path):
#     app.py:1362  exception-fallback in _compute_portfolio_strip — entire strip
#     failed, no bot_state reachable; datetime.now(_ET) is the only value.
# ---------------------------------------------------------------------------


class TestTopLevelDataAsOfDerivesFromCycleTimestamp:
    """Regression guard: top-level data_as_of at app.py:2117 must derive from
    last_successful_cycle_at, not datetime.now() (render clock).

    The JS reads `portfolio.data_as_of || data.data_as_of` (index.js:1168) —
    the top-level field is the hero freshness fallback when portfolio.data_as_of
    is absent. Fixed at b215049 to mirror _compute_portfolio_strip (1281-1303).

    These tests go GREEN against b215049 and would FAIL if app.py:2117 regressed
    back to datetime.now() (render clock).
    """

    def test_get_state_top_level_data_as_of_not_always_render_clock(self, client):
        """GET /api/state top-level data_as_of must NOT always match the server render clock.

        Scenario:
        1. Write bot_state with last_successful_cycle_at='2026-06-23T10:09:00'.
        2. GET /api/state.
        3. Assert top-level data_as_of does NOT contain the current ET or naive clock HH:MM
           when that differs from the known cycle timestamp.

        Uses a past fixed timestamp (10:09 ET) — distinct from current time unless
        the test runs at exactly 10:09:XX ET (skipped if that happens).

        Would FAIL if app.py:2117 reverts to `datetime.now().strftime(...)`.
        """
        from datetime import datetime

        import database as db

        try:
            from zoneinfo import ZoneInfo

            _ET = ZoneInfo("America/New_York")
        except ImportError:
            import pytz

            _ET = pytz.timezone("America/New_York")

        # Production shape: last_successful_cycle_at is a TOP-LEVEL key, not per-sym.
        # Engine writes: bot_state["last_successful_cycle_at"] = current_et.isoformat()
        # (alpha_bot_execution.py:948/1092/1878).  The hollow test wrote it inside the
        # per-sym dict, where the buggy per-sym-dict reader at app.py:2125-2131 happened
        # to find it — producing a false GREEN.  Top-level placement makes it RED against
        # the per-sym-dict loop and GREEN only after state_data.get("last_successful_cycle_at").
        db.save_state(
            {
                "sym-tl-1": {
                    "name": "Top-Level Test Symphony",
                    "current_value": 2000.0,
                    "current_return": 3.0,
                    "simple_return": 0.03,
                    "net_deposits": 1900.0,
                    "time_weighted_return": 0.04,
                    "max_drawdown": 0.02,
                    # NOT here — engine never writes cycle_at into per-sym dicts.
                },
                "last_successful_cycle_at": _FAKE_CYCLE_TS,  # top-level, production shape
            }
        )

        response = client.get("/api/state")
        assert response.status_code == 200, (
            f"GET /api/state returned {response.status_code}; expected 200."
        )

        data = response.get_json()
        if not isinstance(data, dict):
            return

        top_level_dao = data.get("data_as_of", "")
        if not top_level_dao:
            return

        # Capture both ET-aware and naive now() to detect either render-clock flavor.
        now_hhmm_et = datetime.now(_ET).strftime("%H:%M")
        now_hhmm_naive = datetime.now().strftime("%H:%M")
        if _FAKE_CYCLE_LABEL in (now_hhmm_et, now_hhmm_naive):
            pytest.skip(
                f"Test skipped: server clock ({now_hhmm_et} ET / {now_hhmm_naive} local) "
                f"matches fake cycle label ({_FAKE_CYCLE_LABEL}) — cannot distinguish "
                "render-clock from cycle-ts at this exact minute."
            )

        is_et_clock = now_hhmm_et in top_level_dao
        is_naive_clock = now_hhmm_naive in top_level_dao
        has_cycle_label = _FAKE_CYCLE_LABEL in top_level_dao

        if (is_et_clock or is_naive_clock) and not has_cycle_label:
            clock_found = now_hhmm_et if is_et_clock else now_hhmm_naive
            pytest.fail(
                f"GET /api/state top-level data_as_of='{top_level_dao}' encodes the "
                f"server render clock ({clock_found}) rather than the data age "
                f"(last_successful_cycle_at ~ {_FAKE_CYCLE_LABEL} ET). "
                "Regression: app.py:2125-2131 still uses per-sym-dict loop — must "
                "replace with state_data.get('last_successful_cycle_at') (top-level)."
            )

    def test_get_state_top_level_data_as_of_encodes_cycle_timestamp(self, client):
        """GET /api/state top-level data_as_of must encode the cycle timestamp HH:MM.

        When bot_state contains last_successful_cycle_at='2026-06-23T10:09:00',
        the top-level data_as_of must contain '10:09' — the actual data age.

        Would FAIL if app.py:2117 regressed to datetime.now() (always renders
        current clock time, never the cycle timestamp).
        """
        import database as db

        # Production shape: last_successful_cycle_at at TOP LEVEL (not per-sym).
        db.save_state(
            {
                "sym-tl-2": {
                    "name": "Top-Level Cycle-TS Symphony",
                    "current_value": 3000.0,
                    "current_return": 2.5,
                    "simple_return": 0.025,
                    "net_deposits": 2800.0,
                    "time_weighted_return": 0.03,
                    "max_drawdown": 0.015,
                    # NOT here — engine never writes cycle_at into per-sym dicts.
                },
                "last_successful_cycle_at": _FAKE_CYCLE_TS,  # top-level, production shape
            }
        )

        response = client.get("/api/state")
        assert response.status_code == 200, (
            f"GET /api/state returned {response.status_code}; expected 200."
        )

        data = response.get_json()
        if not isinstance(data, dict):
            return

        top_level_dao = data.get("data_as_of", "")
        if not top_level_dao:
            return

        assert _FAKE_CYCLE_LABEL in top_level_dao, (
            f"GET /api/state top-level data_as_of='{top_level_dao}' does not encode "
            f"the cycle timestamp (last_successful_cycle_at={_FAKE_CYCLE_TS} → "
            f"expected '{_FAKE_CYCLE_LABEL}'). "
            "app.py:2125-2131 must use state_data.get('last_successful_cycle_at') "
            "(direct top-level get) not the per-sym-dict loop."
        )
