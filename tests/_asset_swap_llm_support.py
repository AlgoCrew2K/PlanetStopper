"""Shared harness for the asset swap model-resolution tests.

Drives the asset swap code through a real ``anthropic.Anthropic`` over the
in-process HTTP fake (``tests/_anthropic_http_fake.py``), so every request fact
is read off the outgoing HTTP request and the SDK's own reply parsing runs.

Provenance of the data used here:
- the swap pair (incumbent, candidate) is read from the captured
  ``messages/thinking_tool_emit_swap_candidates.json`` reply, never typed;
- the held tree is the repository's captured symphony score fixture with one
  held ticker renamed to the captured incumbent (synthetic composition, a test
  setup edit, not a captured API body);
- every model reply and error body is a fixture under ``tests/fixtures/anthropic``.

Patch targets and forms production must keep (the tests rely on them):
- ``advisors.asset_swap_engine._build_client`` is the client seam, called with no
  arguments, looked up as a module global at call time;
- ``advisors.asset_swap_engine.get_tradeable_set``, ``run_backtest`` and
  ``_has_composer_key`` are module-scope names (patched on the engine module);
- ``advisors.asset_swap_engine.database.insert_advisor_observation`` is the only
  persistence call of a run.
"""

from __future__ import annotations

import copy
import json
import pathlib
from contextlib import ExitStack
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from tests._anthropic_http_fake import load_fixture

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TREE_PATH = _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
_RENAMED_TICKER = "SHY"

SYMPHONY_NAME = "sym-test"
COMPOSER_HASH = "hash-abc-123"
SWAP_TOOL_NAME = "emit_swap_candidates"
EVALUATE_URL = "/ai-advisor/asset-swaps/evaluate"


def swap_tool_reply() -> dict:
    """The captured tool reply every successful generation in these tests serves."""
    return load_fixture("messages/thinking_tool_emit_swap_candidates.json")


def captured_pair() -> tuple[str, str]:
    """(incumbent, candidate) the captured reply proposes."""
    block = next(b for b in swap_tool_reply()["content"] if b["type"] == "tool_use")
    pair = block["input"]["candidates"][0]
    return pair["incumbent_asset"], pair["candidate_asset"]


def plain_tree() -> dict:
    """The captured score fixture as it is: it does not hold the captured incumbent."""
    return json.loads(_TREE_PATH.read_text(encoding="utf-8"))


def held_tree() -> dict:
    """The score fixture with its ``SHY`` holding renamed to the captured incumbent."""
    incumbent, _ = captured_pair()
    text = json.dumps(plain_tree())
    marker = f'"ticker": "{_RENAMED_TICKER}"'
    assert marker in text, "support self-guard: the fixture no longer holds the renamed ticker"
    return json.loads(text.replace(marker, f'"ticker": "{incumbent}"'))


def tradeable_universe() -> frozenset:
    incumbent, candidate = captured_pair()
    return frozenset({incumbent, candidate, "SPY", "AGG", "GLD"})


def fake_backtest_result():
    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    day = date(2022, 1, 1)
    for i in range(100):
        returns[day.isoformat()] = 0.001 * (1 + (i % 5) * 0.1 - 0.2)
        day += timedelta(days=1)
    return BacktestResult(stats={"sharpe": 0.5}, data_warnings=[], daily_returns=returns)


def fail_every_listing(fake) -> None:
    """Make every listing a run could start fail, so a retry shows up as extra listings.

    One failure per attempt the generator is allowed: a run that retried a missing
    family would otherwise see a healthy page on its second try and send a request,
    which a zero-request assertion alone would catch only by accident.
    """
    import llm_request

    for _ in range(llm_request.NO_TOOL_CALL_ATTEMPTS + 1):
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))


def request_models(fake) -> list[str]:
    return [r.body["model"] for r in fake.message_requests]


def objective_body(**extra) -> dict:
    return {"symphony_id": SYMPHONY_NAME, **extra}


def explicit_pair_body() -> dict:
    incumbent, candidate = captured_pair()
    return {"symphony_id": SYMPHONY_NAME, "from_ticker": incumbent, "to_ticker": candidate}


def evaluated_pairs(data: dict) -> list[tuple[str, str]]:
    """(incumbent, candidate) of every proposal the route reports, survivors and rejected."""
    detail = (data.get("survivors_detail") or []) + (data.get("rejected_detail") or [])
    return [(p["incumbent_asset"], p["candidate_asset"]) for p in detail]


class Run:
    """What one evaluate request returned and what it persisted."""

    def __init__(self, response, inserts):
        self.status = response.status_code
        self.data = response.get_json()
        self.inserts = inserts


def post_evaluate(client, fake, body, *, tree=None, composer_key=True, universe=None) -> Run:
    """POST the evaluate route with every non-model seam stubbed and the model seam on ``fake``."""
    import advisors.asset_swap_engine as ase
    import ai_advisor

    inserts = MagicMock(return_value=1)
    tree = copy.deepcopy(held_tree() if tree is None else tree)
    universe = tradeable_universe() if universe is None else universe
    with ExitStack() as stack:
        stack.enter_context(patch.object(ase, "_has_composer_key", return_value=composer_key))
        stack.enter_context(patch.object(ase, "_build_client", return_value=fake.client))
        stack.enter_context(patch.object(ase, "get_tradeable_set", return_value=universe))
        stack.enter_context(
            patch.object(ase, "run_backtest", side_effect=lambda *a, **k: fake_backtest_result())
        )
        stack.enter_context(patch.object(ase.database, "insert_advisor_observation", inserts))
        stack.enter_context(
            patch("database.load_state", return_value={COMPOSER_HASH: {"name": SYMPHONY_NAME}})
        )
        stack.enter_context(patch("symphony_logic.fetch_symphony_score", return_value=tree))
        stack.enter_context(
            patch(
                "ai_advisor.build_reasoning_context",
                return_value=("", copy.deepcopy(ai_advisor._EMPTY_MANIFEST)),
            )
        )
        response = client.post(EVALUATE_URL, json=body)
    return Run(response, inserts)


def tool_nodes(schema, found=None) -> list[dict]:
    """Every dict node under ``schema`` whose ``type`` is ``object``."""
    found = [] if found is None else found
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            found.append(schema)
        for value in schema.values():
            tool_nodes(value, found)
    elif isinstance(schema, list):
        for item in schema:
            tool_nodes(item, found)
    return found
