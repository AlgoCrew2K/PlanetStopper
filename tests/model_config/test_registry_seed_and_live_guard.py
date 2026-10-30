"""The test harness around model resolution.

Existing tests give their LLM calls MagicMock clients whose model listing is
empty, so the shared harness seeds a resolved model for both knobs through the
real resolver. A test that needs the unresolved state opts out, and a test that
forgets its HTTP seam can never reach the real Anthropic API.
"""

from __future__ import annotations

import os

import anthropic
import pytest

import model_config
from model_config import Knob
from tests import conftest as harness
from tests._anthropic_http_fake import FAKE_API_KEY, AnthropicHttpFake, load_fixture

CAPTURED = load_fixture("models/page_captured.json")


def _newest(line):
    records = [r for r in CAPTURED["data"] if r["line"] == line and r["lifecycle"] == "active"]
    return max(records, key=lambda r: r["created_at"])["id"]


@pytest.mark.parametrize(
    "knob,line",
    [(Knob.SUGGESTION, "fable"), (Knob.SYNTHESIS, "opus")],
    ids=["suggestion", "synthesis"],
)
def test_every_test_starts_with_both_knobs_resolved_from_the_captured_page(knob, line):
    choice = model_config.peek(knob)

    assert choice.source == "resolved"
    assert choice.model_id == _newest(line)


def test_every_test_starts_without_override_variables():
    assert "ADVISOR_SUGGESTION_MODEL" not in os.environ
    assert "ADVISOR_SYNTHESIS_MODEL" not in os.environ


def test_the_unresolved_models_fixture_starts_a_test_with_nothing_resolved(unresolved_models):
    fake = AnthropicHttpFake.with_captured_page()
    assert [model_config.peek(k).source for k in (Knob.SUGGESTION, Knob.SYNTHESIS)] == [
        "unavailable"
    ] * 2

    model_config.resolve(Knob.SUGGESTION, fake.client)

    assert fake.listing_sequences == 1, "the seed was discarded, so the first call lists"


@pytest.mark.parametrize(
    "expression,selects_live",
    [
        ("not live and not slow and not perf", False),
        ("not (live or perf)", False),
        ("slow", False),
        ("", True),
        ("live", True),
        ("live or slow", True),
        ("(((", False),
    ],
)
def test_the_live_guard_decision_follows_the_marker_expression(expression, selects_live):
    assert harness._live_anthropic_selected(expression) is selects_live


def test_the_default_endpoint_is_unroutable_exactly_when_the_run_deselects_live_tests(request):
    selects_live = harness._live_anthropic_selected(getattr(request.config.option, "markexpr", ""))
    forced = os.environ.get("ANTHROPIC_BASE_URL") == harness._UNROUTABLE_ANTHROPIC_URL

    assert forced is (not selects_live)

    if forced:
        assert harness._UNROUTABLE_ANTHROPIC_URL.startswith("http://127.0.0.1:"), (
            "loopback only, nothing external"
        )
        client = anthropic.Anthropic(api_key=FAKE_API_KEY, max_retries=0, timeout=5.0)
        with pytest.raises(anthropic.APIConnectionError):
            client.models.list()
