"""Shared harness for Strategy Builder generation tests that run over the Anthropic HTTP fake.

Build-plan replies come from the captured Messages fixtures. The captured
``emit_build_plans`` reply carries one plan that matches no objective's
structural signature (an equal-weight pair of assets is no sleeve pair, regime
gate, inverse-vol weight or momentum filter), so a test that needs an admitted
plan derives its reply from the captured envelope with ``content[*].input``
replaced by conforming plans; the derived body is checked by the same
validator that checks every other message fixture, so it cannot drift from the
public reply shape. Model IDs are read from the captured Models page, never
typed.

Patch targets and forms production must keep:
- ``advisors.build_plan_generator._build_client`` is the client seam
  (``generate_build_plans`` calls it once per run, as a module attribute).
- ``advisors.universe_provider.get_tradeable_set`` is the Alpaca universe seam
  (lazy-imported inside ``strategy_builder_engine._generate_candidate_trees``).
- ``advisors.composer_backtest_client.requests.post`` is the Composer network
  boundary.
- ``advisors.build_plan_generator.load_atlas_candidates`` is the Atlas seam the
  route lazy-imports, so no Mongo read is ever reachable.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from contextlib import ExitStack
from unittest.mock import patch

from tests._anthropic_http_fake import FIXTURES_DIR, AnthropicHttpFake, load_fixture

CAPTURED_MODELS = load_fixture("models/page_captured.json")
RELISTED_NEWER_FABLE = load_fixture("models/relisted_newer_fable.json")
COMPOSER_BACKTEST_CAPTURE = json.loads(
    (FIXTURES_DIR.parent / "composer" / "backtest_inline_v1.json").read_text(encoding="utf-8")
)

# The universe every generation test offers; the conforming plans below use only these tickers.
MEMBERSHIP = frozenset({"SPY", "QQQ", "TLT", "GLD", "AGG"})
SUGGESTION_ENV_VAR = "ADVISOR_SUGGESTION_MODEL"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def newest_active_record(line: str, page: dict) -> dict:
    """Oracle: the newest active record of a line on a Models page, by release date."""
    eligible = [
        r
        for r in page["data"]
        if r.get("line") == line and r.get("lifecycle", "active") == "active"
    ]
    return max(eligible, key=lambda r: r["created_at"])


def _message_validator():
    spec = importlib.util.spec_from_file_location(
        "anthropic_schema_check", FIXTURES_DIR / "schema_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_valid_message(body: dict, where: str) -> dict:
    problems = _message_validator().check_message_file(body, where)
    assert not problems, f"derived reply does not conform to the Messages reference: {problems}"
    return body


# ---------------------------------------------------------------------------
# Plans (generator input shapes; the DSL is the module's own contract)
# ---------------------------------------------------------------------------


def _asset(ticker: str) -> dict:
    return {"kind": "asset", "ticker": ticker}


def _weight(scheme: str, children: list, **extra) -> dict:
    return {"kind": "weight", "scheme": scheme, "children": children, **extra}


def _plan(plan_id: str, root: dict) -> dict:
    return {
        "plan_id": plan_id,
        "objective": "diversify",
        "name": f"Plan {plan_id}",
        "rebalance": "daily",
        "root": root,
    }


def conforming_diversify_plans() -> list[dict]:
    """Two structurally distinct plans, each a pair of sleeves (the diversify signature)."""
    return [
        _plan(
            "div-a",
            {
                "kind": "group",
                "name": "sleeves",
                "children": [
                    _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                    _weight("equal", [_asset("TLT"), _asset("GLD")]),
                ],
            },
        ),
        _plan(
            "div-b",
            {
                "kind": "group",
                "name": "sleeves",
                "children": [
                    _weight("equal", [_asset("SPY"), _asset("QQQ")]),
                    _weight("inverse_vol", [_asset("TLT"), _asset("AGG")], window_days=30),
                ],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------


def _replace_tool_input(body: dict, plans: list[dict]) -> dict:
    blocks = [b for b in body["content"] if b["type"] == "tool_use"]
    assert len(blocks) == 1, "the base reply must carry exactly one tool_use block"
    blocks[0]["name"] = "emit_build_plans"
    blocks[0]["input"] = {"plans": copy.deepcopy(plans)}
    return body


def tool_reply_with_plans(plans: list[dict]) -> dict:
    """Captured emit_build_plans reply with its plans replaced (derived, validated)."""
    body = _replace_tool_input(load_fixture("messages/thinking_tool_emit_build_plans.json"), plans)
    return assert_valid_message(body, "tool_reply_with_plans")


def fallback_reply_with_plans(plans: list[dict]) -> dict:
    """Fallback-served reply (fallback block, thinking, tool_use) carrying build plans."""
    body = _replace_tool_input(load_fixture("messages/fallback_served_tool.json"), plans)
    return assert_valid_message(body, "fallback_reply_with_plans")


def refusal_after_partial_plans(plans: list[dict]) -> dict:
    """Refusal that arrives after a complete-looking emit_build_plans tool_use block."""
    body = _replace_tool_input(load_fixture("messages/refusal_after_partial_tool.json"), plans)
    return assert_valid_message(body, "refusal_after_partial_plans")


def text_only_reply() -> dict:
    return load_fixture("messages/text_only_with_tools.json")


def max_tokens_reply() -> dict:
    return load_fixture("messages/max_tokens.json")


def refusal_reply(name: str = "refusal_reasoning_extraction.json") -> dict:
    return load_fixture(f"messages/{name}")


def error_outcome(name: str) -> dict:
    return load_fixture(f"errors/{name}")


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------


def walk(node):
    """Yield every dict in a JSON value, depth first."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def fake_with_captured_page() -> AnthropicHttpFake:
    return AnthropicHttpFake.with_captured_page()


def fake_with_newer_fable_listing() -> AnthropicHttpFake:
    return AnthropicHttpFake(pages=[RELISTED_NEWER_FABLE])


def fake_with_failing_listing() -> AnthropicHttpFake:
    """A fake whose every Models listing answers 503 (the unresolved-family case)."""
    fake = AnthropicHttpFake()
    for _ in range(8):
        fake.queue_listing_outcome(error_outcome("503_unavailable.json"))
    return fake


def install_generator_seam(monkeypatch, fake: AnthropicHttpFake) -> None:
    import advisors.build_plan_generator as gen

    monkeypatch.setattr(gen, "_build_client", lambda: fake.client)


# ---------------------------------------------------------------------------
# Full route / engine chain (Alpaca, Composer and Atlas replaced at their boundaries)
# ---------------------------------------------------------------------------


class _ComposerBacktestResponse:
    status_code = 200
    headers: dict = {}
    text = ""

    def json(self):
        return copy.deepcopy(COMPOSER_BACKTEST_CAPTURE["response"])


def atlas_candidate(candidate_id: str = "atlas-candidate"):
    """One community candidate shaped as ``load_atlas_candidates`` returns it."""
    from advisors import strategy_builder_engine as sbe
    from advisors import symphony_schema

    tree = symphony_schema.make_root(
        "Community pick",
        "daily",
        [symphony_schema.make_weight_equal([symphony_schema.make_asset("SPY")])],
    )
    return sbe.CandidateInfo(
        candidate_id=candidate_id,
        tree=tree,
        template_id="atlas-suggested",
        params={
            "plan_id": candidate_id,
            "name": "Community pick",
            "objective": "diversify",
            "provenance": "atlas-suggested",
        },
    )


def install_chain_seams(monkeypatch, fake: AnthropicHttpFake, atlas=None) -> ExitStack:
    """Wire generation to ``fake`` and stub every other live seam of the Strategy Builder run.

    Returns the ExitStack holding the Composer ``requests.post`` patch; the caller
    keeps it open for the request (use ``with`` or ``stack.close()``).
    """
    import advisors.build_plan_generator as gen
    import advisors.composer_backtest_client as composer
    import advisors.strategy_builder_engine as sbe
    import advisors.universe_provider as universe

    install_generator_seam(monkeypatch, fake)
    monkeypatch.setattr(universe, "get_tradeable_set", lambda *a, **k: MEMBERSHIP)
    monkeypatch.setattr(sbe, "_has_composer_key", lambda: True)
    atlas_list = list(atlas or [])
    monkeypatch.setattr(gen, "load_atlas_candidates", lambda *a, **k: list(atlas_list))
    stack = ExitStack()
    stack.enter_context(
        patch.object(composer.requests, "post", return_value=_ComposerBacktestResponse())
    )
    return stack


def post_strategy_builder_run(client, body: dict | None = None):
    return client.post(
        "/ai-advisor/strategy-builder/run",
        json=body if body is not None else {"objective": "diversify"},
    )
