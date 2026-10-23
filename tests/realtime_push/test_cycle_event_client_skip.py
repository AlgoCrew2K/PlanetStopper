"""RED tests: the dashboard's cycle-complete handler refetches only when data changed.

static/index.js registers one `cycle-complete` listener. It parses the event's JSON and skips
`loadState()` and `fetchGuardAlphaSummary(window)` only when the parsed `changed` is strictly
`false`; anything else (an older daemon's empty object, a missing key, text that is not JSON)
refetches, exactly as before the payload existed.

The real `handleCycleCompleteEvent` is extracted from static/index.js and run under node
against recording stubs, so the assertions are about what the handler calls, not what its
source contains. Payload strings come from a real CycleRecord.event_payload().
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.dashboard.test_account_basis_honesty_render import _extract_function_with_signature

_INDEX_JS = Path(__file__).resolve().parents[2] / "static" / "index.js"
_HANDLER = "handleCycleCompleteEvent"


def _handler_source() -> str:
    return _extract_function_with_signature(_INDEX_JS.read_text(encoding="utf-8"), _HANDLER)


def _calls_for(data, hero_window: str = "30d") -> list[list]:
    """Run the real handler under node with `evt.data` = data and return what it called."""
    assert shutil.which("node") is not None, "node is required to run the handler"
    harness = f"""
    var __calls = [];
    var _heroWindow = {json.dumps(hero_window)};
    function loadState() {{ __calls.push(['loadState']); }}
    function fetchGuardAlphaSummary(w) {{ __calls.push(['fetchGuardAlphaSummary', w]); }}
    {_handler_source()}
    var __evt = {{}};
    {"" if data is None else f"__evt.data = {json.dumps(data)};"}
    {_HANDLER}(__evt);
    console.log(JSON.stringify(__calls));
    """
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, f"the handler threw instead of refetching:\n{result.stderr}"
    return json.loads(result.stdout.strip())


_REFETCH = [["loadState"], ["fetchGuardAlphaSummary", "30d"]]


def _sorted(calls: list[list]) -> list[list]:
    return sorted(calls)


class TestHandlerIsWired:
    def test_the_listener_registers_the_named_handler(self):
        source = _INDEX_JS.read_text(encoding="utf-8")

        assert re.search(
            rf"addEventListener\(\s*['\"]cycle-complete['\"]\s*,\s*{_HANDLER}\s*\)", source
        ), f"the cycle-complete listener must be registered as `{_HANDLER}`"

    def test_the_handler_is_a_function_declaration_in_the_script(self):
        assert _handler_source().startswith(f"function {_HANDLER}(")


class TestChangedFalseIsSkipped:
    def test_an_unchanged_closed_market_event_triggers_no_fetch(self, make_cycle_record):
        """A closed-market minute: no /api/state and no /api/guard-alpha-summary request."""
        payload = make_cycle_record(outcome="closed", changed=False).event_payload()

        assert _calls_for(json.dumps(payload)) == []

    def test_a_skipped_minute_event_triggers_no_fetch(self, make_cycle_record):
        payload = make_cycle_record(outcome="skipped", changed=False).event_payload()

        assert _calls_for(json.dumps(payload)) == []


class TestChangedTrueRefetches:
    @pytest.mark.parametrize("hero_window", ["30d", "1y"])
    def test_a_changed_event_fetches_state_once_and_the_summary_for_the_live_window(
        self, hero_window, make_cycle_record
    ):
        """The summary request carries the picker's current window, read when the event fires."""
        payload = make_cycle_record(outcome="cycle", changed=True).event_payload()

        calls = _calls_for(json.dumps(payload), hero_window=hero_window)

        assert _sorted(calls) == _sorted([["loadState"], ["fetchGuardAlphaSummary", hero_window]])


class TestUnreadablePayloadRefetches:
    def test_text_that_is_not_json_refetches(self):
        assert _sorted(_calls_for("not json")) == _sorted(_REFETCH)

    def test_the_empty_object_an_older_daemon_sends_refetches(self):
        assert _sorted(_calls_for("{}")) == _sorted(_REFETCH)

    def test_a_payload_without_the_changed_key_refetches(self, make_cycle_record):
        payload = make_cycle_record(outcome="cycle", changed=True).event_payload()
        payload.pop("changed", None)

        assert _sorted(_calls_for(json.dumps(payload))) == _sorted(_REFETCH)

    @pytest.mark.parametrize(
        "changed",
        ["false", 0, None, "", [], {}],
        ids=["string-false", "zero", "null", "empty-string", "empty-list", "empty-object"],
    )
    def test_only_a_boolean_false_skips(self, changed, make_cycle_record):
        """`changed` is read strictly: a falsy value of another type is not a skip signal."""
        payload = make_cycle_record(outcome="closed", changed=False).event_payload()
        payload["changed"] = changed

        assert _sorted(_calls_for(json.dumps(payload))) == _sorted(_REFETCH)

    @pytest.mark.parametrize(
        "data",
        ["null", "[]", "42", '"false"', "true"],
        ids=["json-null", "json-array", "json-number", "json-string", "json-true"],
    )
    def test_json_that_is_not_an_object_refetches_without_throwing(self, data):
        assert _sorted(_calls_for(data)) == _sorted(_REFETCH)

    def test_an_event_with_no_data_refetches_without_throwing(self):
        assert _sorted(_calls_for(None)) == _sorted(_REFETCH)


def test_payload_fixture_really_carries_changed_false(make_cycle_record):
    """Guard for this module's own premise: the strings above come from a record whose
    payload names `changed`, so the skip cases are not vacuous."""
    payload = make_cycle_record(outcome="closed", changed=False).event_payload()

    assert payload.get("changed") is False
