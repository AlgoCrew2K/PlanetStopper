"""Shared setup for logic change tests that run the real engine against the Anthropic HTTP fake.

Every reply a test queues is a captured or derived record from ``tests/fixtures/anthropic``:

- ``reply_with_tool_edit`` copies the captured thinking-then-tool reply and replaces only the
  ``edits`` list inside its ``tool_use`` input with an edit that resolves against the sample tree
  used by the engine tests (the captured edit addresses a path of the capture's own prompt). The
  captured ``model``, ``id``, ``usage`` and rationale text are kept.
- ``fallback_served_reply`` and ``refusal_after_partial_tool_reply`` do the same to the derived
  fallback-served and refusal-after-partial-tool records, and set the ``tool_use`` block's ``name``
  to the logic tool (the derived records were written for the swap tool).
- Text-only, refusal and ``max_tokens`` replies are used verbatim.

The Composer backtest is outside this contract, so the engine's ``run_backtest`` binding is a
stand-in that returns a fixed, valid return series; the Anthropic side is the real SDK over the
HTTP fake, and the engine's own client seam is the only name patched.
"""

from __future__ import annotations

import copy
import json
import pathlib
from datetime import date, timedelta
from unittest.mock import MagicMock

from tests._anthropic_http_fake import AnthropicHttpFake, load_fixture

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TREE_PATH = REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"

CAPTURED_MODELS_PAGE = "models/page_captured.json"
RELISTED_MODELS_PAGE = "models/relisted_newer_fable.json"

TOOL_REPLY = "messages/thinking_tool_emit_logic_edits.json"
TEXT_ONLY_REPLY = "messages/text_instead_of_tool_emit_logic_edits.json"
FALLBACK_REPLY = "messages/fallback_served_tool.json"
REFUSAL_PARTIAL_TOOL_REPLY = "messages/refusal_after_partial_tool.json"
REFUSAL_REASONING_EXTRACTION_REPLY = "messages/refusal_reasoning_extraction.json"
REFUSAL_NULL_CATEGORY_REPLY = "messages/refusal_null_category.json"
MAX_TOKENS_REPLY = "messages/max_tokens_in_thinking.json"

ROUTE_SYMPHONY_NAME = "sym-test"
ROUTE_COMPOSER_HASH = "hash-abc-123"
ROUTE_CHANGE_DESCRIPTION = "lengthen the RSI window to cut drawdown"
TOOL_NAME = "emit_logic_edits"

# The tool exactly as the base ref defines it (advisors/logic_change_engine.py), written out
# here so a changed production schema cannot move its own baseline.
BASELINE_TOOL = {
    "name": "emit_logic_edits",
    "description": (
        "Emit a bounded list of objective-directed numeric parameter edits to "
        "the operator's real symphony logic tree. Each edit MUST target an "
        "EXISTING numeric parameter via its exact node_path and param_key as "
        "listed in the candidate parameters below \u2014 never invent a new path."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "description": "List of proposed parameter edits.",
                "items": {
                    "type": "object",
                    "properties": {
                        "node_path": {
                            "type": "array",
                            "items": {"type": ["string", "integer"]},
                            "description": (
                                "Navigation path from the tree root, copied EXACTLY "
                                "from a listed candidate parameter."
                            ),
                        },
                        "param_key": {"type": "string"},
                        "new_value": {"type": "number"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["node_path", "param_key", "new_value"],
                },
            }
        },
        "required": ["edits"],
    },
}


def expected_strict_tool() -> dict:
    """The baseline tool plus strictness: ``strict`` and closed objects, nothing else changed."""
    tool = copy.deepcopy(BASELINE_TOOL)
    tool["strict"] = True
    tool["input_schema"]["additionalProperties"] = False
    tool["input_schema"]["properties"]["edits"]["items"]["additionalProperties"] = False
    return tool


def load_tree() -> dict:
    return json.loads(TREE_PATH.read_text(encoding="utf-8"))


def newest_model_id(page: dict, line: str) -> str:
    """The id of the newest active record of ``line`` by ``created_at`` (an ISO Z timestamp)."""
    records = [
        r
        for r in page["data"]
        if r.get("line") == line and r.get("lifecycle", "active") == "active"
    ]
    return max(records, key=lambda r: r["created_at"])["id"]


def models_page(path: str = CAPTURED_MODELS_PAGE) -> dict:
    return load_fixture(path)


def real_edit() -> dict:
    """One edit that resolves against the sample tree and leaves it structurally valid."""
    import advisors.logic_change_engine as lce
    from advisors import symphony_schema

    tree = load_tree()
    captured = load_fixture(TOOL_REPLY)
    rationale = next(b for b in captured["content"] if b["type"] == "tool_use")["input"]["edits"][
        0
    ]["rationale"]
    for param in lce.extract_numeric_params(tree):
        if param["param_key"] != "window":
            continue
        new_value = param["value"] + 4
        variant = lce.apply_logic_tweak(
            tree,
            lce.LogicTweak(
                node_path=param["node_path"],
                param_key=param["param_key"],
                old_value=param["value"],
                new_value=new_value,
            ),
        )
        if variant is not None and not symphony_schema.validate_tree(variant):
            return {
                "node_path": param["node_path"],
                "param_key": param["param_key"],
                "new_value": new_value,
                "rationale": rationale,
            }
    raise AssertionError(
        "self-guard: the sample tree has no window parameter a valid edit can move"
    )


def _with_tool_edit(body: dict, edit: dict) -> dict:
    body = copy.deepcopy(body)
    for block in body["content"]:
        if block["type"] == "tool_use":
            block["name"] = TOOL_NAME
            block["input"] = {"edits": [edit]}
    return body


def reply_with_tool_edit(edit: dict) -> dict:
    return _with_tool_edit(load_fixture(TOOL_REPLY), edit)


def reply_with_thinking_then_tool_edit(edit: dict) -> dict:
    """The captured tool reply preceded by the captured thinking block of the thinking-text reply."""
    body = reply_with_tool_edit(edit)
    thinking = next(
        b for b in load_fixture("messages/thinking_text.json")["content"] if b["type"] == "thinking"
    )
    body["content"] = [copy.deepcopy(thinking), *body["content"]]
    return body


def fallback_served_reply(edit: dict) -> dict:
    return _with_tool_edit(load_fixture(FALLBACK_REPLY), edit)


def refusal_after_partial_tool_reply(edit: dict) -> dict:
    return _with_tool_edit(load_fixture(REFUSAL_PARTIAL_TOOL_REPLY), edit)


def new_fake(page: str = CAPTURED_MODELS_PAGE) -> AnthropicHttpFake:
    return AnthropicHttpFake(pages=[load_fixture(page)])


def queue_failing_listings(fake: AnthropicHttpFake, count: int = 6) -> None:
    """Make every model listing the code under test attempts answer 503."""
    for _ in range(count):
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))


def fake_backtest_result():
    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    day = date(2022, 1, 1)
    for i in range(100):
        returns[day.isoformat()] = 0.001 * (1 + (i % 5) * 0.1 - 0.2)
        day += timedelta(days=1)
    return BacktestResult(stats={"sharpe": 0.5}, data_warnings=[], daily_returns=returns)


def install_engine_world(monkeypatch, fake: AnthropicHttpFake) -> MagicMock:
    """Point the engine's client seam at the fake; return the observation-write spy."""
    import advisors.logic_change_engine as lce

    monkeypatch.setattr(lce, "_build_client", lambda: fake.client)
    monkeypatch.setattr(lce, "_has_composer_key", lambda: True)
    monkeypatch.setattr(lce, "run_backtest", lambda *a, **k: fake_backtest_result())
    spy = MagicMock(return_value=1)
    monkeypatch.setattr(lce.database, "insert_advisor_observation", spy)
    return spy


def install_route_world(monkeypatch, fake: AnthropicHttpFake) -> MagicMock:
    """Engine world plus the route's collaborators: symphony lookup, tree fetch, reasoning context."""
    import ai_advisor

    spy = install_engine_world(monkeypatch, fake)
    monkeypatch.setattr(
        "database.load_state", lambda: {ROUTE_COMPOSER_HASH: {"name": ROUTE_SYMPHONY_NAME}}
    )
    monkeypatch.setattr("symphony_logic.fetch_symphony_score", lambda *a, **k: load_tree())
    monkeypatch.setattr(
        "ai_advisor.build_reasoning_context",
        lambda *a, **k: ("", dict(ai_advisor._EMPTY_MANIFEST)),
    )
    return spy


def post_evaluate(payload: dict | None = None, client=None):
    """POST the route (default payload: the route symphony and change description)."""
    import app as app_module

    body = payload or {
        "symphony_id": ROUTE_SYMPHONY_NAME,
        "change_description": ROUTE_CHANGE_DESCRIPTION,
    }
    if client is not None:
        return client.post("/ai-advisor/logic-changes/evaluate", json=body)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as own_client:
        return own_client.post("/ai-advisor/logic-changes/evaluate", json=body)


def evaluate_via_route(client=None) -> dict:
    resp = post_evaluate(client=client)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data is not None
    return data


def all_proposals(data: dict) -> list[dict]:
    return list(data.get("survivors_detail") or []) + list(data.get("rejected_detail") or [])


def run_operator_entry(fake: AnthropicHttpFake, monkeypatch):
    import advisors.logic_change_engine as lce

    install_engine_world(monkeypatch, fake)
    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    return lce.propose_operator_logic_change(
        "sym-1",
        load_tree(),
        objective=objective,
        change_description=ROUTE_CHANGE_DESCRIPTION,
    )


def run_suggest_entry(fake: AnthropicHttpFake, monkeypatch):
    import advisors.logic_change_engine as lce

    install_engine_world(monkeypatch, fake)
    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    return lce.suggest_logic_changes("sym-1", load_tree(), objective)


ENTRY_RUNNERS = {"operator": run_operator_entry, "suggest": run_suggest_entry}
