"""The Messages reply fixtures conform to the public API references.

Captured replies must pass the validator (so it is no stricter than the real
API) and every derived refusal or fallback reply must pass it too (so a
fixture and the helper that reads it cannot agree with each other by
construction). Each rule is exercised on a planted violation so the validator
is not vacuous.
"""

from __future__ import annotations

import copy
import importlib.util

import pytest

from tests._anthropic_http_fake import FIXTURES_DIR, load_fixture

DERIVED = [
    "refusal_reasoning_extraction.json",
    "refusal_null_category.json",
    "refusal_after_partial_text.json",
    "refusal_after_partial_tool.json",
    "fallback_served_text.json",
    "fallback_served_tool.json",
]


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "anthropic_fixture_schema_check_messages", FIXTURES_DIR / "schema_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schema_check = _load_validator()
CAPTURED_PAGE = load_fixture("models/page_captured.json")


def _names():
    return sorted(p.name for p in (FIXTURES_DIR / "messages").glob("*.json"))


@pytest.mark.parametrize("name", _names())
def test_every_message_fixture_passes_the_validator(name):
    assert schema_check.check_message_file(load_fixture(f"messages/{name}"), name) == []


def test_the_directory_walk_covers_every_message_fixture():
    report = schema_check.validate_messages_directory()

    assert set(report) == {f"messages/{n}" for n in _names()}
    assert not any(report.values())


def test_every_derived_message_exists_and_names_a_captured_model_id():
    captured_ids = {r["id"] for r in CAPTURED_PAGE["data"]}

    for name in DERIVED:
        assert load_fixture(f"messages/{name}")["model"] in captured_ids, name


def test_the_refusal_and_fallback_records_cover_the_cases_the_helper_distinguishes():
    refusals = [load_fixture(f"messages/{n}") for n in DERIVED if n.startswith("refusal")]
    categories = {r["stop_details"]["category"] for r in refusals}
    kinds = [[b["type"] for b in r["content"]] for r in refusals]

    assert None in categories
    assert len(categories - {None}) >= 1
    assert any("text" in k for k in kinds)
    assert any("tool_use" in k for k in kinds)
    assert all(r["stop_reason"] == "refusal" for r in refusals)
    for name in ("fallback_served_text.json", "fallback_served_tool.json"):
        body = load_fixture(f"messages/{name}")
        assert body["content"][0]["type"] == "fallback"
        assert body["content"][0]["to"]["model"] == body["model"]


def _mutations():
    refusal = load_fixture("messages/refusal_after_partial_tool.json")
    served = load_fixture("messages/fallback_served_tool.json")
    plain = load_fixture("messages/thinking_text.json")

    def edit(base, mutate):
        data = copy.deepcopy(base)
        mutate(data)
        return data

    return {
        "id not msg_": edit(plain, lambda d: d.update(id="x_123")),
        "type not message": edit(plain, lambda d: d.update(type="completion")),
        "role not assistant": edit(plain, lambda d: d.update(role="user")),
        "model empty": edit(plain, lambda d: d.update(model="")),
        "content not an array": edit(plain, lambda d: d.update(content={})),
        "unknown stop reason": edit(plain, lambda d: d.update(stop_reason="done")),
        "text block without text": edit(plain, lambda d: d["content"][1].pop("text")),
        "thinking without signature": edit(plain, lambda d: d["content"][0].pop("signature")),
        "tool_use input not an object": edit(
            refusal, lambda d: d["content"][0].update(input="not an object")
        ),
        "tool_use id malformed": edit(refusal, lambda d: d["content"][0].update(id="t1")),
        "stop reason tool_use without a tool block": edit(
            served, lambda d: d.update(content=[d["content"][0]])
        ),
        "refusal without stop_details": edit(refusal, lambda d: d.update(stop_details=None)),
        "refusal details wrong type": edit(
            refusal, lambda d: d["stop_details"].update(type="other")
        ),
        "refusal category not a string": edit(
            refusal, lambda d: d["stop_details"].update(category=3)
        ),
        "refusal explanation missing": edit(
            refusal, lambda d: d["stop_details"].pop("explanation")
        ),
        "stop_details on a normal reply": edit(
            plain,
            lambda d: d.update(
                stop_details={"type": "refusal", "category": None, "explanation": None}
            ),
        ),
        "usage missing": edit(plain, lambda d: d.pop("usage")),
        "fallback block without a target model": edit(served, lambda d: d["content"][0].pop("to")),
        "fallback block after the serving model's blocks": edit(
            served, lambda d: d["content"].append(d["content"].pop(0))
        ),
        "fallback reply without a fallback_message iteration": edit(
            served,
            lambda d: d["usage"].update(
                iterations=[dict(i, type="message") for i in d["usage"]["iterations"]]
            ),
        ),
    }


@pytest.mark.parametrize("label", list(_mutations()))
def test_the_validator_rejects_a_planted_violation(label):
    assert schema_check.check_message_file(_mutations()[label], label) != []
