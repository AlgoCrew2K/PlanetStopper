"""RED tests — R2-3 AC-2: LLM proposes objective-directed swap PAIRS over the
operator's REAL holdings.

THE ADVERSARIAL FOCUS (mirrors R2-2's test_logic_change_engine_reasoned_generation.py
almost 1:1 — AC-2 is the load-bearing "genuinely reasons" guardrail for this port too):

  A generator that ignores the objective and returns the same pairs regardless
  is a FAIL. This file drives ``generate_reasoned_swap_candidates`` twice with
  DIFFERENT objectives against the SAME fixture tree, via a mock SDK client
  whose canned response is keyed off which objective string reaches the prompt
  — an implementation that never threads ``objective`` into the prompt text
  gets the client's "no match" fallback (empty) both times and fails the test
  for the right reason (not a coincidental equality).

  A second adversarial axis: the LLM's own claimed incumbent must never be
  trusted blind — an incumbent ticker that is NOT actually present in the
  operator's real tree must be dropped, never fabricated into a SwapCandidate
  that would later corrupt the tree via apply_ticker_swap. A candidate equal to
  its own incumbent (swap-into-self) must also be dropped.

Candidate-universe (tradeable-set) validation is covered in its OWN dedicated
file, test_asset_swap_engine_candidate_universe_validation.py — every test
here mocks get_tradeable_set() to a permissive superset so the universe axis
never confounds the assertions made here.

Contract pinned (test-writer's call, mirrors generate_reasoned_logic_candidates):
  ``generate_reasoned_swap_candidates(symphony_id, raw_value, objective, *,
  reasoning_context=None, correlation_data=None, available_assets=None,
  tradeable_universe=None, max_candidates=MAX_SUGGESTED_CANDIDATES) ->
  list[SwapCandidate]`` where ``SwapCandidate`` has ``incumbent_asset``,
  ``candidate_asset``, ``rationale`` fields. LLM tool-use output shape pinned:
  ``{"candidates": [{"incumbent_asset": str, "candidate_asset": str,
  "rationale": str}]}``.

No live network — _build_client is mocked throughout; get_tradeable_set is
mocked throughout (network-bound Alpaca call, must never fire in this file).
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest

from tests import _asset_swap_llm_support as sup
from tests._anthropic_http_fake import load_fixture
from tests.llm_request.conftest import newest_id

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_TREE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
)

# A real ticker present in the fixture tree (verified via extract_tickers at
# test-authoring time) — used as a valid incumbent throughout this file.
_REAL_INCUMBENT = "QQQ"
# Tickers NOT present in the fixture tree — safe non-holdings for candidate use.
_CANDIDATE_A = "IALT"
_CANDIDATE_B = "AGG"


@pytest.fixture(scope="module")
def ase():
    import advisors.asset_swap_engine as _ase  # noqa: PLC0415

    return _ase


@pytest.fixture(scope="module")
def fixture_tree():
    with open(_FIXTURE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture()
def _permissive_universe(ase, monkeypatch):
    """Mock get_tradeable_set() to a permissive superset covering every ticker
    used in this file's tests — isolates the objective/trust adversarial axes
    from the universe-membership axis (that axis has its own dedicated file)."""
    universe = frozenset({_CANDIDATE_A, _CANDIDATE_B, _REAL_INCUMBENT, "SPY", "GLD"})
    monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: universe)
    return universe


# ---------------------------------------------------------------------------
# Mock SDK client doubles
# ---------------------------------------------------------------------------


class _RecordingMockBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload


class _RecordingMockResponse:
    def __init__(self, candidates):
        self.stop_reason = "tool_use"
        self.content = [_RecordingMockBlock({"candidates": candidates})]


class _FixedMockClient:
    """Always returns the same canned candidate list, regardless of prompt content."""

    def __init__(self, candidates):
        self._candidates = candidates
        self.calls: list[dict] = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            return _RecordingMockResponse(self._outer._candidates)

    @property
    def messages(self):
        return self._Messages(self)


class _ObjectiveKeyedMockClient:
    """Returns a DIFFERENT canned candidate list depending on which
    objective-marker substring is found in the outgoing prompt text. An
    implementation that never embeds the objective in the prompt gets the "no
    marker matched" empty fallback on every call — the adversarial trap."""

    def __init__(self, responses_by_marker: dict):
        self._responses_by_marker = responses_by_marker
        self.calls: list[dict] = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            prompt = kwargs.get("messages", [{}])[0].get("content", "")
            for marker, candidates in self._outer._responses_by_marker.items():
                if marker in prompt:
                    return _RecordingMockResponse(candidates)
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
# AC-2 CORE ADVERSARIAL: two objectives, same tree, must produce different pairs.
# ===========================================================================


def test_two_objectives_produce_different_pairs_on_same_tree(
    ase, fixture_tree, _permissive_universe, monkeypatch
):
    """ADVERSARIAL: an objective-ignoring generator fails this test. Two distinct
    objectives against the identical tree must yield DIFFERENT swap-candidate
    sets — proven via a mock client keyed on objective text reaching the prompt."""
    pair_a = {
        "incumbent_asset": _REAL_INCUMBENT,
        "candidate_asset": _CANDIDATE_A,
        "rationale": "targets drawdown reduction",
    }
    pair_b = {
        "incumbent_asset": _REAL_INCUMBENT,
        "candidate_asset": _CANDIDATE_B,
        "rationale": "targets risk-adjusted lift",
    }
    client = _ObjectiveKeyedMockClient(
        {"reduce_drawdown": [pair_a], "lift_risk_adjusted": [pair_b]}
    )
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    obj_a = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result_a = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, obj_a)

    obj_b = ase.SwapObjective(
        objective_type="lift_risk_adjusted", target_pair=None, measured_value=0.0
    )
    result_b = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, obj_b)

    assert result_a, (
        "AC-2 precondition failed: reduce_drawdown produced zero candidates — the "
        "objective marker never reached the prompt (or parsing is broken)."
    )
    assert result_b, (
        "AC-2 precondition failed: lift_risk_adjusted produced zero candidates — the "
        "objective marker never reached the prompt (or parsing is broken)."
    )
    candidates_a = {c.candidate_asset for c in result_a}
    candidates_b = {c.candidate_asset for c in result_b}
    assert candidates_a != candidates_b, (
        f"AC-2 GAP (adversarial FAIL): two different objectives produced the SAME "
        f"swap candidates on the same tree — the generator is ignoring the objective. "
        f"reduce_drawdown={candidates_a!r}, lift_risk_adjusted={candidates_b!r}"
    )


# ===========================================================================
# Real-holding-only incumbents: never trust the LLM's claimed incumbent.
# ===========================================================================


def test_reasoned_pair_targeting_nonexistent_incumbent_is_dropped(
    ase, fixture_tree, _permissive_universe, monkeypatch
):
    """An LLM-proposed pair whose incumbent_asset is NOT actually held in
    raw_value must be dropped — never fabricated into a SwapCandidate."""
    bogus_pair = {
        "incumbent_asset": "NOT-A-REAL-HOLDING",
        "candidate_asset": _CANDIDATE_A,
        "rationale": "should be dropped",
    }
    client = _FixedMockClient(candidates=[bogus_pair])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, objective)

    assert result == [], (
        f"AC-2 GAP: a pair whose incumbent is not a real holding was not dropped. Got {result!r}"
    )


def test_reasoned_pair_swap_into_self_dropped(ase, fixture_tree, _permissive_universe, monkeypatch):
    """A pair proposing incumbent_asset == candidate_asset (swap into self, a
    no-op that wastes a backtest) must be dropped."""
    self_pair = {
        "incumbent_asset": _REAL_INCUMBENT,
        "candidate_asset": _REAL_INCUMBENT,
        "rationale": "no-op, should be dropped",
    }
    client = _FixedMockClient(candidates=[self_pair])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, objective)

    assert result == [], f"AC-2 GAP: a swap-into-self pair was not dropped. Got {result!r}"


def test_reasoned_generator_bounded_by_max_candidates(
    ase, fixture_tree, _permissive_universe, monkeypatch
):
    """The reasoned generator must never return more than max_candidates, even
    when the LLM proposes more."""
    pairs = [
        {"incumbent_asset": _REAL_INCUMBENT, "candidate_asset": t, "rationale": "x"}
        for t in (_CANDIDATE_A, _CANDIDATE_B, "SPY", "GLD")
    ]
    client = _FixedMockClient(candidates=pairs)
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.generate_reasoned_swap_candidates(
        "sym-1", fixture_tree, objective, max_candidates=2
    )
    assert len(result) <= 2, (
        f"AC-2 GAP: generator returned {len(result)} candidates, exceeding max_candidates=2."
    )


# ===========================================================================
# D-1: never raises, degrades cleanly on malformed/failed LLM output.
# ===========================================================================


def test_malformed_tool_output_returns_empty_list_no_raise(
    ase, fixture_tree, _permissive_universe, monkeypatch
):
    """A tool_use block whose input has no 'candidates' key (or a non-list
    value) must degrade to an empty list, never raise."""

    class _MalformedResponse:
        stop_reason = "tool_use"

        class _Block:
            type = "tool_use"
            input = {"not_candidates": "garbage"}

        content = [_Block()]

    class _MalformedClient:
        class _Messages:
            def create(self, **kwargs):
                return _MalformedResponse()

        @property
        def messages(self):
            return self._Messages()

    monkeypatch.setattr(ase, "_build_client", lambda: _MalformedClient())
    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, objective)
    assert result == [], f"D-1 GAP: malformed tool output should degrade to [], got {result!r}"


def test_llm_client_construction_or_call_failure_returns_empty_list_no_raise(
    ase, fixture_tree, _permissive_universe, monkeypatch
):
    """_build_client raising (e.g. missing ANTHROPIC_API_KEY) or the SDK call
    itself raising must both degrade to an empty candidate list, never raise
    out of generate_reasoned_swap_candidates (D-1)."""

    def _raise():
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    monkeypatch.setattr(ase, "_build_client", _raise)
    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, objective)
    assert result == [], (
        f"D-1 GAP: client construction failure should degrade to [], got {result!r}"
    )

    monkeypatch.setattr(ase, "_build_client", lambda: _RaisingMockClient())
    result2 = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, objective)
    assert result2 == [], f"D-1 GAP: SDK call failure should degrade to [], got {result2!r}"


# ===========================================================================
# Model resolution: the request the generator sends and how it reads the reply.
#
# The private generator ``_generate_reasoned_swap_candidates`` returns
# ``(candidates, reply)`` where ``reply`` is the last ``llm_request.Reply`` (None
# when no request was ever made or the generator hit an unexpected exception);
# ``generate_reasoned_swap_candidates`` stays the list-returning wrapper over it.
# Both entry points of the engine call the private one, so existing tests that
# stand in for the generator patch ``_generate_reasoned_swap_candidates`` and
# return ``(candidates, None)``.
# ===========================================================================

_ENV = "ADVISOR_SUGGESTION_MODEL"


def _generate(ase, tree=None, **kwargs):
    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    return ase._generate_reasoned_swap_candidates(
        "sym-1", sup.held_tree() if tree is None else tree, objective, **kwargs
    )


def _without_closure(tool: dict) -> dict:
    """A tool with the strict flag and every ``additionalProperties`` removed."""
    tool = copy.deepcopy(tool)
    tool.pop("strict", None)
    for node in sup.tool_nodes(tool):
        node.pop("additionalProperties", None)
    return tool


class TestGeneratorRequest:
    def test_one_request_with_the_effort_budget_timeout_and_no_rejected_parameter(
        self, ase, swap_fake
    ):
        swap_fake.queue_message(sup.swap_tool_reply())

        _generate(ase)

        (request,) = swap_fake.message_requests
        body = request.body
        assert body["model"] == newest_id("fable")
        assert body["output_config"] == {"effort": "high"}
        assert body["max_tokens"] == 16000
        assert request.timeout["read"] == 120.0
        for rejected in ("temperature", "top_p", "top_k", "thinking"):
            assert rejected not in body
        assert [m["role"] for m in body["messages"]] == ["user"], "no trailing assistant message"
        assert "emit_swap_candidates" in body["messages"][0]["content"], (
            "the prompt must tell the model to call the tool by name, since the call is not forced"
        )

    def test_the_tool_goes_out_strict_with_every_object_node_closed_and_nothing_else_changed(
        self, ase, swap_fake
    ):
        swap_fake.queue_message(sup.swap_tool_reply())

        _generate(ase)

        (sent,) = swap_fake.message_requests[0].body["tools"]
        assert sent["name"] == sup.SWAP_TOOL_NAME
        assert sent["strict"] is True
        nodes = sup.tool_nodes(sent)
        assert len(nodes) >= 2, "the tool holds an input object and an item object"
        assert all(node["additionalProperties"] is False for node in nodes)
        assert _without_closure(sent) == _without_closure(ase._EMIT_SWAP_CANDIDATES_TOOL), (
            "names, types, required lists and descriptions must be exactly the engine's own"
        )

    def test_the_call_is_not_forced_and_asks_for_the_fallback_model(self, ase, swap_fake):
        swap_fake.queue_message(sup.swap_tool_reply())

        _generate(ase)

        (request,) = swap_fake.message_requests
        assert request.body["tool_choice"] == {"type": "auto"}
        assert request.body["fallbacks"] == "default"
        assert request.headers["anthropic-beta"] == "server-side-fallback-2026-07-01"

    def test_an_override_is_the_model_named_and_nothing_is_listed(
        self, ase, swap_fake, unresolved_models, monkeypatch
    ):
        monkeypatch.setenv(_ENV, "operator-chosen-model")
        swap_fake.queue_message(sup.swap_tool_reply())

        _generate(ase)

        assert sup.request_models(swap_fake) == ["operator-chosen-model"]
        assert swap_fake.listing_requests == []


class TestGeneratorReadsTheToolCall:
    def test_a_thinking_then_tool_reply_is_parsed_into_the_swap_pair(self, ase, swap_fake):
        reply_body = sup.swap_tool_reply()
        swap_fake.queue_message(reply_body)
        incumbent, candidate = sup.captured_pair()
        rationale = next(b for b in reply_body["content"] if b["type"] == "tool_use")["input"][
            "candidates"
        ][0]["rationale"]

        candidates, reply = _generate(ase)

        assert [(c.incumbent_asset, c.candidate_asset, c.rationale) for c in candidates] == [
            (incumbent, candidate, rationale)
        ]
        assert reply.reason is None
        assert reply.model == reply_body["model"]

    def test_a_reply_that_names_an_asset_the_symphony_does_not_hold_is_dropped(
        self, ase, swap_fake
    ):
        incumbent, _ = sup.captured_pair()
        tree = sup.plain_tree()
        assert incumbent not in ase.extract_tickers(tree), "self-guard: the tree must not hold it"
        swap_fake.queue_message(sup.swap_tool_reply())

        candidates, reply = _generate(ase, tree)

        assert candidates == []
        assert reply.reason is None, (
            "an untrusted tool input is dropped, it is not a generation failure"
        )
        assert len(swap_fake.message_requests) == 1

    def test_the_public_generator_returns_exactly_the_private_candidates(self, ase, swap_fake):
        swap_fake.queue_message(sup.swap_tool_reply())
        swap_fake.queue_message(sup.swap_tool_reply())
        objective = ase.SwapObjective(
            objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
        )

        private_candidates, _ = ase._generate_reasoned_swap_candidates(
            "sym-1", sup.held_tree(), objective
        )
        public_candidates = ase.generate_reasoned_swap_candidates(
            "sym-1", sup.held_tree(), objective
        )

        assert isinstance(public_candidates, list)
        assert public_candidates == private_candidates
        assert public_candidates


_GENERATION_STOPS = [
    pytest.param(
        ["messages/refusal_reasoning_extraction.json"],
        "refusal",
        "refusal: reasoning_extraction",
        1,
        id="refusal",
    ),
    pytest.param(["messages/max_tokens.json"], "max_tokens", "max_tokens", 1, id="max-tokens"),
    pytest.param(
        ["messages/text_only_with_tools.json"] * 3,
        "no_tool_call",
        "no_tool_call",
        3,
        id="no-tool-call",
    ),
]


class TestGeneratorAttemptsAndStops:
    @pytest.mark.parametrize(("replies", "reason", "reason_text", "requests"), _GENERATION_STOPS)
    def test_stops_with_the_reason_and_the_exact_number_of_requests(
        self, ase, swap_fake, replies, reason, reason_text, requests
    ):
        for name in replies:
            swap_fake.queue_message(load_fixture(name))
        swap_fake.queue_message(sup.swap_tool_reply())  # must stay unused

        candidates, reply = _generate(ase)

        assert candidates == []
        assert reply.reason == reason
        assert reply.reason_text() == reason_text
        assert len(swap_fake.message_requests) == requests
        assert reply.model == load_fixture(replies[-1])["model"]

    def test_a_refusal_after_a_complete_tool_call_still_yields_no_candidate(self, ase, swap_fake):
        swap_fake.queue_message(load_fixture("messages/refusal_after_partial_tool.json"))

        candidates, reply = _generate(ase)

        assert candidates == []
        assert reply.reason == "refusal"

    def test_a_missing_family_sends_nothing_and_says_model_unavailable(
        self, ase, swap_fake, unresolved_models
    ):
        swap_fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        candidates, reply = _generate(ase)

        assert candidates == []
        assert reply.reason == "model_unavailable"
        assert swap_fake.message_requests == []

    @pytest.mark.parametrize(
        ("error_fixture", "reason"),
        [
            pytest.param("errors/404_not_found.json", "override_model_not_found", id="not-found"),
            pytest.param(
                "errors/400_invalid_request.json", "override_request_rejected", id="rejected"
            ),
        ],
    )
    def test_an_unserved_override_is_reported_with_its_variable_and_never_retried(
        self, ase, swap_fake, unresolved_models, monkeypatch, error_fixture, reason
    ):
        monkeypatch.setenv(_ENV, "operator-chosen-model")
        swap_fake.queue_message(load_fixture(error_fixture))
        swap_fake.queue_message(sup.swap_tool_reply())

        candidates, reply = _generate(ase)

        assert candidates == []
        assert reply.reason == reason
        assert reply.reason_text() == f"{reason}: {_ENV}"
        assert len(swap_fake.message_requests) == 1

    @pytest.mark.parametrize(
        "error_fixture", ["errors/500_api_error.json", "errors/400_invalid_request.json"]
    )
    def test_an_api_error_degrades_to_nothing_without_a_retry_and_never_raises(
        self, ase, swap_fake, error_fixture
    ):
        swap_fake.queue_message(load_fixture(error_fixture))
        swap_fake.queue_message(sup.swap_tool_reply())

        candidates, reply = _generate(ase)

        assert candidates == []
        assert reply is None
        assert len(swap_fake.message_requests) == 1

    def test_no_client_degrades_to_nothing_with_no_reply(self, ase, monkeypatch):
        def _no_key():
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

        monkeypatch.setattr(ase, "_build_client", _no_key)
        monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: sup.tradeable_universe())

        candidates, reply = _generate(ase)

        assert candidates == []
        assert reply is None
