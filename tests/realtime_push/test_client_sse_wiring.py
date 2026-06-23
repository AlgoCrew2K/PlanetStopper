"""RED tests — AC-3: client EventSource wiring contract.

These tests check that static/index.js contains the EventSource subscription
and cycle-complete handler without running any JS (Node not required for these
assertions — we inspect the source text).

The JS syntax validity is covered by the consolidated tests/js_syntax/test_js_syntax.py.
Do NOT add a duplicate `node --check` call here.

Fails until rt-impl adds EventSource wiring to static/index.js.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"
_INDEX_JS = _STATIC_DIR / "index.js"


@pytest.fixture(scope="module")
def index_js_source() -> str:
    """Read static/index.js once for the whole module."""
    assert _INDEX_JS.exists(), (
        f"static/index.js not found at {_INDEX_JS}. "
        "This fixture requires the file to exist."
    )
    return _INDEX_JS.read_text(encoding="utf-8")


class TestClientEventSourceWiring:
    def test_index_js_instantiates_eventsource(self, index_js_source: str):
        """static/index.js must create an EventSource instance.

        The primary update path is SSE — the client must subscribe to the server's
        event stream, not rely solely on the 30 s timer.

        Fails until rt-impl adds `new EventSource(...)` to static/index.js.
        """
        assert "EventSource" in index_js_source, (
            "static/index.js must contain 'EventSource' to subscribe to the SSE endpoint. "
            "rt-impl: add `new EventSource('/api/events')` inside the DOMContentLoaded handler."
        )

    def test_index_js_listens_for_cycle_complete_event(self, index_js_source: str):
        """static/index.js must register a listener for the 'cycle-complete' event name.

        The server emits `event: cycle-complete` (or `data:` with that value).
        The client must react to it — not just any SSE event.

        Fails until rt-impl adds `addEventListener('cycle-complete', ...)` or equivalent.
        """
        assert "cycle-complete" in index_js_source, (
            "static/index.js must listen for the 'cycle-complete' event from the SSE stream. "
            "rt-impl: add `_es.addEventListener('cycle-complete', function() { loadState(); })`."
        )

    def test_index_js_calls_loadstate_in_cycle_complete_handler(self, index_js_source: str):
        """The cycle-complete handler must call loadState() to fetch fresh /api/state.

        The handler must not just log or silently ignore the event — it must trigger
        an immediate dashboard refresh.

        Fails until rt-impl calls loadState() (or equivalent) inside the event handler.
        """
        # Look for loadState() called in proximity to 'cycle-complete'.
        # We search for a region that contains both the event name and loadState.
        # Allow a generous 300-char window around the event name occurrence.
        cycle_complete_idx = index_js_source.find("cycle-complete")
        assert cycle_complete_idx >= 0, (
            "static/index.js must contain 'cycle-complete'. See previous test."
        )

        # Check for loadState within ±300 chars of the event name
        window_start = max(0, cycle_complete_idx - 100)
        window_end = min(len(index_js_source), cycle_complete_idx + 300)
        window = index_js_source[window_start:window_end]

        assert "loadState" in window, (
            "The 'cycle-complete' event handler in static/index.js must call `loadState()` "
            "within 300 characters of the event name. "
            "rt-impl: `_es.addEventListener('cycle-complete', function() { loadState(); })`."
        )

    def test_index_js_retains_polling_fallback(self, index_js_source: str):
        """static/index.js must retain the setInterval(loadState, POLL_INTERVAL_MS) fallback.

        AC-5: if SSE drops or is unsupported the dashboard must never go blank.
        The existing poll is the resilience layer — it must NOT be removed.

        Fails if rt-impl removes setInterval when adding EventSource.
        """
        assert "setInterval" in index_js_source, (
            "static/index.js must retain the setInterval polling fallback (AC-5). "
            "The EventSource replaces the PRIMARY update path, not the resilience fallback. "
            "rt-impl: keep `setInterval(loadState, POLL_INTERVAL_MS)` when adding EventSource."
        )
        assert "POLL_INTERVAL_MS" in index_js_source, (
            "POLL_INTERVAL_MS must remain in static/index.js — "
            "it drives the fallback poll timer (AC-5)."
        )

    def test_index_js_guards_eventsource_availability(self, index_js_source: str):
        """static/index.js must guard EventSource usage with an availability check.

        AC-5: if EventSource is not supported (very old browser) the existing poll
        continues — no JS TypeError on construction.

        Fails until rt-impl adds `if (typeof EventSource !== 'undefined')` or similar.
        """
        # Accept: typeof EventSource !== 'undefined' or typeof EventSource != 'undefined'
        # or window.EventSource or EventSource in window
        guards = [
            "typeof EventSource",
            "window.EventSource",
            "EventSource in window",
        ]
        found = any(g in index_js_source for g in guards)
        assert found, (
            "static/index.js must guard EventSource usage with an availability check. "
            "Without this, the page throws on browsers without EventSource support. "
            "rt-impl: wrap EventSource instantiation in "
            "`if (typeof EventSource !== 'undefined') { ... }`."
        )
