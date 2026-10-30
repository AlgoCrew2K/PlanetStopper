"""RED tests — R2-2 AC-2: LLM proposes objective-directed edits over the REAL tree.

THE ADVERSARIAL FOCUS (per team-lead's brief — AC-2 is the load-bearing
"genuinely reasons" guardrail):

  A generator that ignores the objective and returns the same edits regardless
  is a FAIL. This file drives ``generate_reasoned_logic_candidates`` twice with
  DIFFERENT objectives against the SAME fixture tree, via a mock SDK client
  whose canned response is keyed off which objective string reaches the prompt
  — an implementation that never threads ``objective`` into the prompt text
  gets the client's "no match" fallback (empty) both times and fails the test
  for the right reason (not a coincidental equality).

  A second adversarial axis: the LLM's OWN claimed ``old_value`` must never be
  trusted — the resulting ``LogicTweak.old_value`` must be read from the REAL
  tree at the claimed node_path/param_key. An LLM edit whose node_path/param_key
  does not resolve to a real node in ``raw_value`` must be dropped, never
  fabricated into a LogicTweak that would later corrupt the tree.

Contract (see test_logic_change_engine_reasoning_context.py's module docstring
for the full generate_reasoned_logic_candidates signature). LLM tool-use output
shape pinned here: ``{"edits": [{"node_path": [...], "param_key": str,
"new_value": <num>, ...}]}`` — additional keys (e.g. an LLM-claimed
``old_value``) are tolerated but MUST be ignored, never trusted.

No live network — _build_client is mocked throughout.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests.advisors import logic_change_http_support as support

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_TREE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
)


@pytest.fixture(scope="module")
def lce():
    import advisors.logic_change_engine as _lce  # noqa: PLC0415

    return _lce


@pytest.fixture(scope="module")
def fixture_tree():
    with open(_FIXTURE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def real_param(lce, fixture_tree):
    """A REAL numeric-param node_path/param_key/value from the fixture tree —
    used to build canned LLM edits that resolve to an actual node, never a
    hand-invented path (Gate-1 fixture provenance)."""
    params = lce.extract_numeric_params(fixture_tree)
    assert params, "self-guard: fixture tree has no numeric params — test setup is broken."
    return params[0]


# ---------------------------------------------------------------------------
# Mock SDK client doubles
# ---------------------------------------------------------------------------


class _RecordingMockBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload


class _RecordingMockResponse:
    def __init__(self, edits):
        self.stop_reason = "tool_use"
        self.content = [_RecordingMockBlock({"edits": edits})]


class _FixedMockClient:
    """Always returns the same canned edit list, regardless of prompt content."""

    def __init__(self, edits):
        self._edits = edits
        self.calls: list[dict] = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            return _RecordingMockResponse(self._outer._edits)

    @property
    def messages(self):
        return self._Messages(self)


class _ObjectiveKeyedMockClient:
    """Returns a DIFFERENT canned edit list depending on which objective-marker
    substring is found in the outgoing prompt text. An implementation that never
    embeds the objective in the prompt gets the "no marker matched" empty
    fallback on every call — the adversarial trap."""

    def __init__(self, responses_by_marker: dict):
        self._responses_by_marker = responses_by_marker
        self.calls: list[dict] = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            prompt = kwargs.get("messages", [{}])[0].get("content", "")
            for marker, edits in self._outer._responses_by_marker.items():
                if marker in prompt:
                    return _RecordingMockResponse(edits)
            return _RecordingMockResponse([])  # no marker matched -> empty

    @property
    def messages(self):
        return self._Messages(self)


class _RaisingMockClient:
    class _Messages:
        def create(self, **kwargs):
            raise RuntimeError("simulated SDK failure")

    @property
    def messages(self):
        return self._Messages()


# ===========================================================================
# AC-2 CORE ADVERSARIAL: two objectives, same tree, must produce different edits.
# ===========================================================================


def test_two_objectives_produce_different_edits_on_same_tree(
    lce, fixture_tree, real_param, monkeypatch
):
    """ADVERSARIAL: an objective-ignoring generator fails this test. Two distinct
    objectives against the identical tree must yield DIFFERENT LogicTweak sets —
    proven via a mock client keyed on objective text reaching the prompt."""
    edit_a = {
        "node_path": real_param["node_path"],
        "param_key": real_param["param_key"],
        "new_value": real_param["value"] * 0.5,
    }
    edit_b = {
        "node_path": real_param["node_path"],
        "param_key": real_param["param_key"],
        "new_value": real_param["value"] * 2.0,
    }
    client = _ObjectiveKeyedMockClient(
        {"reduce_drawdown": [edit_a], "lift_risk_adjusted": [edit_b]}
    )
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    obj_a = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result_a = lce.generate_reasoned_logic_candidates("sym-1", fixture_tree, obj_a)

    obj_b = lce.LogicChangeObjective(objective_type="lift_risk_adjusted", measured_value=0.0)
    result_b = lce.generate_reasoned_logic_candidates("sym-1", fixture_tree, obj_b)

    assert result_a, (
        "AC-2 precondition failed: reduce_drawdown produced zero candidates — the "
        "objective marker never reached the prompt (or parsing is broken)."
    )
    assert result_b, (
        "AC-2 precondition failed: lift_risk_adjusted produced zero candidates — the "
        "objective marker never reached the prompt (or parsing is broken)."
    )
    new_values_a = {t.new_value for t in result_a}
    new_values_b = {t.new_value for t in result_b}
    assert new_values_a != new_values_b, (
        f"AC-2 GAP (adversarial FAIL): two different objectives produced the SAME "
        f"edits on the same tree — the generator is ignoring the objective. "
        f"reduce_drawdown={new_values_a!r}, lift_risk_adjusted={new_values_b!r}"
    )


def test_change_description_reaches_operator_path_prompt(lce, fixture_tree, monkeypatch):
    """The operator's free-text change_description must reach the LLM prompt as a
    steering hint (PM-ASSUMED Q1: retained, not removed)."""
    client = _FixedMockClient(edits=[])
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    lce.generate_reasoned_logic_candidates(
        "sym-1",
        fixture_tree,
        objective,
        change_description="please widen the RSI threshold aggressively",
    )
    assert client.calls, "generate_reasoned_logic_candidates never called the SDK client."
    prompt = client.calls[0].get("messages", [{}])[0].get("content", "")
    assert "widen the RSI threshold aggressively" in prompt, (
        "AC-2 GAP: change_description text did not reach the LLM prompt."
    )


# ===========================================================================
# Real-node-only edits: never trust the LLM's own node_path or old_value claim.
# ===========================================================================


def test_reasoned_edit_targeting_nonexistent_node_path_is_dropped(lce, fixture_tree, monkeypatch):
    """An LLM edit whose node_path/param_key does not resolve to a real node in
    raw_value must be dropped — never fabricated into a LogicTweak."""
    bogus_edit = {
        "node_path": ["this", "path", "does", "not", "exist", 999],
        "param_key": "nonexistent_param",
        "new_value": 42,
    }
    client = _FixedMockClient(edits=[bogus_edit])
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.generate_reasoned_logic_candidates("sym-1", fixture_tree, objective)

    assert result == [], (
        f"AC-2 GAP: an edit targeting a nonexistent node_path was not dropped. Got {result!r}"
    )


def test_reasoned_edit_old_value_sourced_from_real_tree_never_from_llm(
    lce, fixture_tree, real_param, monkeypatch
):
    """SECURITY-ADVERSARIAL: the LLM's response includes a bogus 'old_value' key
    the parser must ignore — the resulting LogicTweak.old_value must equal the
    REAL current value read from the tree, never the LLM-supplied decoy (which
    could otherwise be used to bypass apply_logic_tweak's old-value verification
    downstream)."""
    decoy_old_value = "not-the-real-value-12345"
    edit = {
        "node_path": real_param["node_path"],
        "param_key": real_param["param_key"],
        "old_value": decoy_old_value,  # must be ignored
        "new_value": real_param["value"] + 1,
    }
    client = _FixedMockClient(edits=[edit])
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.generate_reasoned_logic_candidates("sym-1", fixture_tree, objective)

    assert result, "AC-2 precondition failed: a well-formed edit produced zero candidates."
    tweak = result[0]
    assert tweak.old_value == real_param["value"], (
        f"AC-2 SECURITY GAP: LogicTweak.old_value ({tweak.old_value!r}) does not match the "
        f"REAL tree value ({real_param['value']!r}) — the LLM's decoy old_value "
        f"({decoy_old_value!r}) was trusted instead of reading the actual tree."
    )


def test_reasoned_generator_bounded_by_max_candidates(lce, fixture_tree, monkeypatch):
    """The reasoned generator must never return more than max_candidates, even
    when the LLM proposes more."""
    params = lce.extract_numeric_params(fixture_tree)[:5]
    edits = [
        {"node_path": p["node_path"], "param_key": p["param_key"], "new_value": p["value"] + 1}
        for p in params
    ]
    client = _FixedMockClient(edits=edits)
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.generate_reasoned_logic_candidates(
        "sym-1", fixture_tree, objective, max_candidates=2
    )
    assert len(result) <= 2, (
        f"AC-2 GAP: generator returned {len(result)} candidates, exceeding max_candidates=2."
    )


# ===========================================================================
# D-1: never raises, degrades cleanly on malformed/failed LLM output.
# ===========================================================================


def test_malformed_tool_output_returns_empty_list_no_raise(lce, fixture_tree, monkeypatch):
    """A tool_use block whose input has no 'edits' key (or a non-list value)
    must degrade to an empty list, never raise."""

    class _MalformedResponse:
        stop_reason = "tool_use"

        class _Block:
            type = "tool_use"
            input = {"not_edits": "garbage"}

        content = [_Block()]

    class _MalformedClient:
        class _Messages:
            def create(self, **kwargs):
                return _MalformedResponse()

        @property
        def messages(self):
            return self._Messages()

    monkeypatch.setattr(lce, "_build_client", lambda: _MalformedClient())
    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.generate_reasoned_logic_candidates("sym-1", fixture_tree, objective)
    assert result == [], f"D-1 GAP: malformed tool output should degrade to [], got {result!r}"


def test_llm_client_construction_or_call_failure_returns_empty_list_no_raise(
    lce, fixture_tree, monkeypatch
):
    """_build_client raising (e.g. missing ANTHROPIC_API_KEY) or the SDK call
    itself raising must both degrade to an empty candidate list, never raise
    out of generate_reasoned_logic_candidates (D-1)."""

    def _raise():
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    monkeypatch.setattr(lce, "_build_client", _raise)
    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.generate_reasoned_logic_candidates("sym-1", fixture_tree, objective)
    assert result == [], (
        f"D-1 GAP: client construction failure should degrade to [], got {result!r}"
    )

    monkeypatch.setattr(lce, "_build_client", lambda: _RaisingMockClient())
    result2 = lce.generate_reasoned_logic_candidates("sym-1", fixture_tree, objective)
    assert result2 == [], f"D-1 GAP: SDK call failure should degrade to [], got {result2!r}"


# ===========================================================================
# The real route and the real engine against the Anthropic HTTP fake: what the
# generation request looks like on the wire, and how a thinking-then-tool reply
# and a fallback-served reply are read.
# ===========================================================================


@pytest.fixture
def generation(monkeypatch):
    """Run the route once against a fake that answers with the queued replies.

    Returns ``(response_json, fake, observation_write_spy)``.
    """

    def _run(*replies, page: str = support.CAPTURED_MODELS_PAGE):
        fake = support.new_fake(page)
        spy = support.install_route_world(monkeypatch, fake)
        for reply in replies:
            fake.queue_message(reply)
        data = support.evaluate_via_route()
        return data, fake, spy

    return _run


def _value_at(tree: dict, edit: dict):
    node = tree
    for step in edit["node_path"]:
        node = node[step]
    return node[edit["param_key"]]


def test_generation_request_carries_tool_choice_auto(generation):
    _data, fake, _spy = generation(support.reply_with_tool_edit(support.real_edit()))

    assert fake.message_requests[0].body["tool_choice"] == {"type": "auto"}, (
        "the tool must be offered, never forced: a forced tool call is rejected by models that think"
    )


def test_generation_request_sends_the_baseline_tool_made_strict(generation):
    _data, fake, _spy = generation(support.reply_with_tool_edit(support.real_edit()))

    assert fake.message_requests[0].body["tools"] == [support.expected_strict_tool()], (
        "the tool must equal the base definition plus strict and closed objects, nothing else changed"
    )


def test_generation_request_keeps_the_node_path_item_type_list(generation):
    _data, fake, _spy = generation(support.reply_with_tool_edit(support.real_edit()))

    schema = fake.message_requests[0].body["tools"][0]["input_schema"]
    node_path = schema["properties"]["edits"]["items"]["properties"]["node_path"]
    assert node_path["items"] == {"type": ["string", "integer"]}, (
        "a node path mixes dict keys and list indices; the item type list must reach the API as is"
    )


def test_generation_request_carries_the_server_side_fallback_setting(generation):
    _data, fake, _spy = generation(support.reply_with_tool_edit(support.real_edit()))

    request = fake.message_requests[0]
    assert request.body.get("fallbacks") == "default"
    assert "server-side-fallback-2026-07-01" in request.headers.get("anthropic-beta", "")


def test_generation_request_sets_effort_and_sends_no_sampling_or_thinking_parameters(generation):
    _data, fake, _spy = generation(support.reply_with_tool_edit(support.real_edit()))

    body = fake.message_requests[0].body
    assert body.get("output_config", {}).get("effort") == "high"
    sent_forbidden = [k for k in ("temperature", "top_p", "top_k", "thinking") if k in body]
    assert sent_forbidden == [], (
        f"models that always think reject these parameters: {sent_forbidden}"
    )
    assert body["messages"][-1]["role"] == "user", (
        "a trailing assistant message (prefill) is rejected"
    )


def test_generation_request_output_budget_fits_a_thinking_reply(generation):
    _data, fake, _spy = generation(support.reply_with_tool_edit(support.real_edit()))

    assert fake.message_requests[0].body["max_tokens"] >= 16000, (
        "thinking counts against max_tokens; the old 2048 budget cannot hold a reply at high effort"
    )


def test_generation_request_timeout_fits_a_thinking_reply(generation):
    _data, fake, _spy = generation(support.reply_with_tool_edit(support.real_edit()))

    assert fake.message_requests[0].timeout["read"] >= 120, (
        "a thinking reply at high effort does not finish inside the old 30 second timeout"
    )


def test_generation_prompt_lists_candidate_parameters_and_names_the_tool(
    generation, lce, fixture_tree
):
    _data, fake, _spy = generation(support.reply_with_tool_edit(support.real_edit()))

    content = fake.message_requests[0].body["messages"][0]["content"]
    assert isinstance(content, str)
    assert support.TOOL_NAME in content, "the prompt must tell the model to call the tool by name"
    first = lce.extract_numeric_params(fixture_tree)[0]
    assert f"param_key={first['param_key']!r}" in content
    assert f"node_path={first['node_path']!r}" in content, (
        "a prompt without the candidate parameters makes the model answer in text instead of "
        "calling the tool"
    )


def test_thinking_then_tool_reply_input_is_parsed(generation, fixture_tree):
    edit = support.real_edit()
    reply = support.reply_with_thinking_then_tool_edit(edit)
    assert [b["type"] for b in reply["content"]][:2] == ["thinking", "tool_use"], (
        "self-guard: the tool call must not be the first block"
    )

    data, _fake, spy = generation(reply)

    proposals = support.all_proposals(data)
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["tweak_param_key"] == edit["param_key"]
    assert proposal["tweak_node_path"] == edit["node_path"]
    assert proposal["tweak_new_value"] == edit["new_value"]
    assert proposal["tweak_old_value"] == _value_at(fixture_tree, edit), (
        "the old value comes from the real tree, never from the reply"
    )
    assert spy.call_count == 1, "the evaluated proposal is persisted once"


def test_primary_reply_is_used_and_provenance_names_the_primary_model(generation):
    reply = support.reply_with_tool_edit(support.real_edit())

    data, _fake, _spy = generation(reply)

    assert data["provenance"]["generation_model"] == reply["model"]


def test_fallback_served_reply_supplies_the_candidates_and_provenance_names_the_fallback_model(
    generation,
):
    edit = support.real_edit()
    reply = support.fallback_served_reply(edit)

    data, fake, _spy = generation(reply)

    requested = fake.message_requests[0].body["model"]
    assert reply["model"] != requested, "self-guard: a fallback reply names a different model"
    assert [p["tweak_param_key"] for p in support.all_proposals(data)] == [edit["param_key"]]
    assert data["provenance"]["generation_model"] == reply["model"], (
        f"the run was served by the fallback model and must say so: {data['provenance']!r}"
    )


# ---------------------------------------------------------------------------
# security: tool input is untrusted
# ---------------------------------------------------------------------------


def test_security_edit_naming_a_path_absent_from_the_tree_is_dropped(generation):
    edit = {**support.real_edit(), "node_path": ["children", 99, "children", 99]}

    data, _fake, spy = generation(support.reply_with_tool_edit(edit))

    assert support.all_proposals(data) == []
    assert data["survivors"] == 0
    assert spy.call_count == 0, "a fabricated edit must never reach persistence"


def test_security_old_value_claimed_by_the_reply_is_ignored(generation, fixture_tree):
    edit = support.real_edit()
    claimed = {**edit, "old_value": 987654.321}

    data, _fake, _spy = generation(support.reply_with_tool_edit(claimed))

    (proposal,) = support.all_proposals(data)
    assert proposal["tweak_old_value"] == _value_at(fixture_tree, edit)
    assert proposal["tweak_old_value"] != claimed["old_value"]
