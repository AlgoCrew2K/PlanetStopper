"""The Asset Swaps panel, run for real under node, rendering what the route produced.

The panel script ``static/ai_advisor_asset_swaps.js`` is executed unmodified in a
``vm`` context with minimal ``document`` and ``fetch`` stubs; the test fires
``DOMContentLoaded`` and the evaluate click, and reads the results area's HTML.
The response body the stubbed ``fetch`` returns is what the real route produced
for the scenario (real SDK over the HTTP fake, tests/_asset_swap_llm_support.py),
never a hand-built body, except in the one escaping test that says so.

Contract under test:
- a result whose ``message`` carries any of the six generation reason codes says
  that no swap was generated this run, never that the gate rejected one;
- a zero-survivor result with no reason code keeps the gate wording;
- the model line reads ``model unavailable`` for an empty ``generation_model`` and
  names the model otherwise;
- the message is escaped in the page.
"""

from __future__ import annotations

import html
import json
import pathlib
import re
import shutil
import subprocess

import pytest

from tests import _asset_swap_llm_support as sup
from tests._anthropic_http_fake import AnthropicHttpFake, load_fixture

_PANEL_JS = pathlib.Path(__file__).resolve().parents[2] / "static" / "ai_advisor_asset_swaps.js"
_ENV = "ADVISOR_SUGGESTION_MODEL"
_UNSERVED_OVERRIDE = "operator-chosen-model"

_GENERATED_TITLE = "No swap was generated this run"
_GATE_TITLE = "No swap cleared the gate this run"
_UNAVAILABLE_LINE = "model unavailable"

# Runs the real panel script; reads the route's JSON body from stdin, prints the results HTML.
_DRIVER = r"""
const fs = require('fs');
const vm = require('vm');
const body = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(process.argv[1], 'utf8');

function element(id, props) {
  const handlers = {};
  return Object.assign({
    id, value: '', innerHTML: '', textContent: '', disabled: false, style: {},
    handlers,
    addEventListener(type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
  }, props || {});
}
const elements = {
  'swap-symphony-select': element('swap-symphony-select', { value: 'sym-test' }),
  'swap-from-ticker': element('swap-from-ticker'),
  'swap-to-ticker': element('swap-to-ticker'),
  'swap-evaluate-btn': element('swap-evaluate-btn'),
  'try-swap-error': element('try-swap-error'),
  'swap-results-area': element('swap-results-area'),
};
const documentHandlers = {};
const document = {
  getElementById(id) { return elements[id] || null; },
  addEventListener(type, fn) { (documentHandlers[type] = documentHandlers[type] || []).push(fn); },
};
const requests = [];
function fetch(url) {
  requests.push(url);
  if (url === '/api/csrf-token') {
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ csrf_token: 'token' }) });
  }
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve(body), text: () => Promise.resolve(''),
  });
}
const sandbox = {
  document, fetch, console, setTimeout,
  window: {}, location: {}, sessionStorage: { setItem() {}, getItem() { return null; } },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
(documentHandlers.DOMContentLoaded || []).forEach((fn) => fn());
(elements['swap-evaluate-btn'].handlers.click || []).forEach((fn) => fn());
setTimeout(() => {
  process.stdout.write(JSON.stringify({ html: elements['swap-results-area'].innerHTML, requests }));
}, 25);
"""


def _render(body: dict) -> str:
    """The results area HTML after the real panel script rendered ``body``."""
    assert shutil.which("node") is not None, "node is required to run the panel script"
    done = subprocess.run(
        ["node", "-e", _DRIVER, str(_PANEL_JS)],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert done.returncode == 0, f"node failed:\nstdout: {done.stdout}\nstderr: {done.stderr}"
    out = json.loads(done.stdout)
    assert "/ai-advisor/asset-swaps/evaluate" in out["requests"], (
        "the evaluate click must POST to the route, or the harness drove nothing"
    )
    return out["html"]


def _between(page: str, opening: str) -> str:
    match = re.search(re.escape(opening) + r"(.*?)</div>", page, re.S)
    assert match, f"{opening!r} not found in the rendered panel:\n{page}"
    return match.group(1)


def _title(page: str) -> str:
    return _between(page, '<div class="no-survivors-title">')


def _model_line(page: str) -> str:
    return _between(page, 'data-testid="as-live-generation-provenance">')


@pytest.fixture
def fake() -> AnthropicHttpFake:
    return AnthropicHttpFake.with_captured_page()


def _queue(fake, *names):
    for name in names:
        fake.queue_message(load_fixture(name))


def _produce_model_unavailable(client, fake, monkeypatch):
    fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))
    return sup.post_evaluate(client, fake, sup.objective_body())


def _produce_no_tool_call(client, fake, monkeypatch):
    _queue(fake, *["messages/text_only_with_tools.json"] * 3)
    return sup.post_evaluate(client, fake, sup.objective_body())


def _produce_refusal(client, fake, monkeypatch):
    _queue(fake, "messages/refusal_reasoning_extraction.json")
    return sup.post_evaluate(client, fake, sup.objective_body())


def _produce_max_tokens(client, fake, monkeypatch):
    _queue(fake, "messages/max_tokens.json")
    return sup.post_evaluate(client, fake, sup.objective_body())


def _produce_override_model_not_found(client, fake, monkeypatch):
    monkeypatch.setenv(_ENV, _UNSERVED_OVERRIDE)
    _queue(fake, "errors/404_not_found.json")
    return sup.post_evaluate(client, fake, sup.objective_body())


def _produce_override_request_rejected(client, fake, monkeypatch):
    monkeypatch.setenv(_ENV, _UNSERVED_OVERRIDE)
    _queue(fake, "errors/400_invalid_request.json")
    return sup.post_evaluate(client, fake, sup.objective_body())


_GENERATION_REASONS = {
    "model_unavailable": _produce_model_unavailable,
    "no_tool_call": _produce_no_tool_call,
    "refusal": _produce_refusal,
    "max_tokens": _produce_max_tokens,
    "override_model_not_found": _produce_override_model_not_found,
    "override_request_rejected": _produce_override_request_rejected,
}

_REASON_PARAMS = [pytest.param(code, id=code) for code in _GENERATION_REASONS]


@pytest.mark.usefixtures("unresolved_models")
class TestTheTitleSaysWhatHappened:
    @pytest.mark.parametrize("code", _REASON_PARAMS)
    def test_a_generation_reason_code_says_no_swap_was_generated_not_that_the_gate_rejected(
        self, client, fake, monkeypatch, code
    ):
        run = _GENERATION_REASONS[code](client, fake, monkeypatch)
        assert run.data["survivors"] == 0
        assert code in run.data["message"], "self-guard: the route must have produced this code"

        page = _render(run.data)

        assert _title(page) == _GENERATED_TITLE
        assert _GATE_TITLE not in page
        assert run.data["message"] in page, "the message with the reason stays visible in the body"

    def test_a_zero_survivor_run_without_a_reason_code_keeps_the_gate_title(
        self, client, fake, monkeypatch
    ):
        fake.queue_message(sup.swap_tool_reply())
        run = sup.post_evaluate(
            client, fake, sup.objective_body(), universe=frozenset({"SPY"})
        )  # the proposed candidate is not tradeable, so nothing reaches the gate
        assert run.data["survivors"] == 0
        assert not any(code in run.data["message"] for code in _GENERATION_REASONS), (
            "self-guard: this run must carry no generation reason code"
        )

        page = _render(run.data)

        assert _title(page) == _GATE_TITLE
        assert _GENERATED_TITLE not in page

    def test_an_evaluated_swap_renders_cards_and_no_empty_state_title(
        self, client, fake, monkeypatch
    ):
        fake.queue_message(sup.swap_tool_reply())
        run = sup.post_evaluate(client, fake, sup.objective_body())
        assert sup.evaluated_pairs(run.data) == [sup.captured_pair()], "self-guard"

        page = _render(run.data)

        assert _GENERATED_TITLE not in page
        assert sup.captured_pair()[1] in page, "the evaluated candidate must appear on a card"


@pytest.mark.usefixtures("unresolved_models")
class TestTheModelLine:
    def test_an_empty_generation_model_reads_model_unavailable(self, client, fake, monkeypatch):
        run = _produce_model_unavailable(client, fake, monkeypatch)
        assert run.data["provenance"]["generation_model"] == "", "self-guard"

        assert _model_line(_render(run.data)).count(_UNAVAILABLE_LINE) == 1

    def test_an_early_error_with_no_resolved_family_reads_model_unavailable(
        self, client, fake, monkeypatch
    ):
        run = sup.post_evaluate(client, fake, sup.objective_body(), composer_key=False)
        assert "error" in run.data and run.data["provenance"]["generation_model"] == "", (
            "self-guard"
        )

        page = _render(run.data)

        assert _model_line(page).count(_UNAVAILABLE_LINE) == 1
        assert 'data-testid="error-state"' in page

    def test_a_named_model_is_shown_and_never_labelled_unavailable(self, client, fake, monkeypatch):
        fake.queue_message(sup.swap_tool_reply())
        run = sup.post_evaluate(client, fake, sup.objective_body())
        model = run.data["provenance"]["generation_model"]
        assert model, "self-guard: a successful run names its model"

        line = _model_line(_render(run.data))

        assert model in line
        assert _UNAVAILABLE_LINE not in line

    def test_an_override_value_is_shown_as_the_model(self, client, fake, monkeypatch):
        run = _produce_override_model_not_found(client, fake, monkeypatch)

        line = _model_line(_render(run.data))

        assert _UNSERVED_OVERRIDE in line
        assert _UNAVAILABLE_LINE not in line


@pytest.mark.usefixtures("unresolved_models")
class TestSecurityNothingTheServerSaidIsRenderedAsMarkup:
    def test_a_message_carrying_markup_is_escaped_on_the_generation_title_path(
        self, client, fake, monkeypatch
    ):
        run = _produce_refusal(client, fake, monkeypatch)
        # The one hand-edited body in this file: a producer body whose message gains hostile text.
        hostile = dict(run.data, message='<img src=x onerror="alert(1)"> ' + run.data["message"])

        page = _render(hostile)

        assert "<img" not in page
        assert html.escape("<img", quote=True) in page
        assert _title(page) == _GENERATED_TITLE
