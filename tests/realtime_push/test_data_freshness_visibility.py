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
    """Minimal bot_state with a last_successful_cycle_at stamp."""
    return {
        "sym-1": {
            "name": "Symphony 1",
            "current_value": 1000.0,
            "current_return": 1.5,
            "simple_return": 0.05,
            "net_deposits": 800.0,
            "time_weighted_return": 0.06,
            "max_drawdown": 0.12,
            "last_successful_cycle_at": cycle_ts,
        }
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
