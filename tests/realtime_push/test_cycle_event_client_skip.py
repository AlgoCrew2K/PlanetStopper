"""RED tests: the dashboard's cycle-complete handler refetches only when data changed.

static/index.js registers one `cycle-complete` listener. It parses the event's JSON and skips
`loadState()` and `fetchGuardAlphaSummary(window)` only when the parsed `changed` is strictly
`false`; anything else (an older daemon's empty object, a missing key, text that is not JSON)
refetches, exactly as before the payload existed.

The real `handleCycleCompleteEvent` is extracted from static/index.js and run under node
against recording stubs, so the assertions are about what the handler calls, not what its
source contains. Payload strings come from a real CycleRecord.event_payload().

Each test runs a whole family of events through one node process and reports every case that
refetched (or skipped) wrongly, because every test pays for a rebuilt test database.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from tests.dashboard.test_account_basis_honesty_render import _extract_function_with_signature

_INDEX_JS = Path(__file__).resolve().parents[2] / "static" / "index.js"
_HANDLER = "handleCycleCompleteEvent"
_NO_DATA = object()


def _handler_source() -> str:
    return _extract_function_with_signature(_INDEX_JS.read_text(encoding="utf-8"), _HANDLER)


def _run_events(events: list[tuple[str, object, str]]) -> dict:
    """Run the real handler under node once per (label, evt.data, hero window) and return,
    by label, the calls it made. `_NO_DATA` sends an event with no `data` property at all."""
    assert shutil.which("node") is not None, "node is required to run the handler"
    cases = [
        {
            "label": label,
            "window": window,
            "hasData": data is not _NO_DATA,
            "data": None if data is _NO_DATA else data,
        }
        for label, data, window in events
    ]
    harness = f"""
    var __calls = [];
    var _heroWindow = '30d';
    function loadState() {{ __calls.push(['loadState']); }}
    function fetchGuardAlphaSummary(w) {{ __calls.push(['fetchGuardAlphaSummary', w]); }}
    {_handler_source()}
    var __out = {{}};
    {json.dumps(cases)}.forEach(function (c) {{
        __calls = [];
        _heroWindow = c.window;
        var evt = {{}};
        if (c.hasData) {{ evt.data = c.data; }}
        try {{
            {_HANDLER}(evt);
            __out[c.label] = __calls;
        }} catch (err) {{
            __out[c.label] = 'THREW: ' + err;
        }}
    }});
    console.log(JSON.stringify(__out));
    """
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"the harness failed:\n{result.stderr}"
    return json.loads(result.stdout.strip())


def _refetch(window: str = "30d") -> list[list]:
    return [["loadState"], ["fetchGuardAlphaSummary", window]]


def _wrong(results: dict, expected: dict) -> list[str]:
    """Each label whose calls differ from `expected` (compared order-free)."""
    wrong = []
    for label, want in expected.items():
        got = results[label]
        if got != want and (not isinstance(got, list) or sorted(got) != sorted(want)):
            wrong.append(f"{label}: want {want!r}, got {got!r}")
    return wrong


class TestHandlerIsWired:
    def test_the_listener_registers_the_named_handler(self):
        source = _INDEX_JS.read_text(encoding="utf-8")

        assert re.search(
            rf"addEventListener\(\s*['\"]cycle-complete['\"]\s*,\s*{_HANDLER}\s*\)", source
        ), f"the cycle-complete listener must be registered as `{_HANDLER}`"

    def test_the_handler_is_a_function_declaration_in_the_script(self):
        assert _handler_source().startswith(f"function {_HANDLER}(")


class TestPayloadDecidesTheRefetch:
    def test_an_unchanged_event_triggers_no_fetch_for_any_outcome(self, make_cycle_record):
        """Closed-market and skipped minutes: no /api/state and no /api/guard-alpha-summary request."""
        events = [
            (
                outcome,
                json.dumps(make_cycle_record(outcome=outcome, changed=False).event_payload()),
                "30d",
            )
            for outcome in ("closed", "skipped", "blackout", "failed")
        ]

        results = _run_events(events)

        assert _wrong(results, {label: [] for label, _, _ in events}) == []

    def test_a_changed_event_fetches_state_once_and_the_summary_for_the_live_window(
        self, make_cycle_record
    ):
        """The summary request carries the picker's current window, read when the event fires."""
        payload = json.dumps(make_cycle_record(outcome="cycle", changed=True).event_payload())
        events = [("window-30d", payload, "30d"), ("window-1y", payload, "1y")]

        results = _run_events(events)

        assert _wrong(results, {"window-30d": _refetch("30d"), "window-1y": _refetch("1y")}) == []

    def test_a_payload_that_does_not_say_changed_is_false_refetches(self, make_cycle_record):
        """`changed` is read strictly: only a boolean false skips; every other value, a missing
        key, an older daemon's empty object and text that is not JSON refetch."""
        base = make_cycle_record(outcome="closed", changed=False).event_payload()
        assert base.get("changed") is False, "premise: the record's payload names `changed`"
        without_key = {k: v for k, v in base.items() if k != "changed"}
        events: list[tuple[str, object, str]] = [
            ("not-json", "not json", "30d"),
            ("empty-object", "{}", "30d"),
            ("missing-key", json.dumps(without_key), "30d"),
            ("no-data-property", _NO_DATA, "30d"),
        ]
        for label, value in (
            ("string-false", "false"),
            ("zero", 0),
            ("null", None),
            ("empty-string", ""),
            ("empty-list", []),
            ("empty-object-value", {}),
        ):
            events.append((f"changed-{label}", json.dumps({**base, "changed": value}), "30d"))
        for label, text in (
            ("json-null", "null"),
            ("json-array", "[]"),
            ("json-number", "42"),
            ("json-string", '"false"'),
            ("json-true", "true"),
        ):
            events.append((label, text, "30d"))

        results = _run_events(events)

        assert _wrong(results, {label: _refetch() for label, _, _ in events}) == []
