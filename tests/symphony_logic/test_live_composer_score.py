"""
Tier 2 — LIVE contract test for the Composer `/score` endpoint.

This is the DRIFT SENSOR. It hits the REAL Composer API and asserts the
actual response still has the structural keys our fixtures (and therefore
our condense logic) depend on. If Composer changes the `/score` response
shape, this test fails loudly and tells us the committed fixtures have gone
stale.

It is NOT run in normal CI:
  * every test here is marked `@pytest.mark.live`
  * the `run-tests` skill / pytest default deselects `-m 'not live'`
  * opt in explicitly with  `pytest -m live --include-live`  (or the project
    `/run-tests --include-live` wrapper)

It also self-skips if Composer credentials are absent from the environment,
so an accidental `-m live` run on a machine with no creds skips cleanly
rather than erroring.
"""

import os

import pytest

pytestmark = pytest.mark.live


# Top-level keys confirmed present on real /score responses by the validation
# research (composer-symphony-logic-endpoint.md) AND by both committed
# fixtures. `name`, `step`, `children`, `id`, `rebalance` appear on every
# real response; `asset_class` appears on the large fixture. The condense
# logic depends on these existing.
_REQUIRED_TOP_LEVEL_KEYS = {"name", "step", "children", "id", "rebalance"}

# Node-level structural vocabulary the condense walk relies on.
_EXPECTED_STEP_VALUES = {
    "root",
    "group",
    "if",
    "if-child",
    "asset",
    "filter",
    "wt-cash-equal",
}


def _composer_creds_present() -> bool:
    return bool(os.environ.get("COMPOSER_KEY_ID")) and bool(os.environ.get("COMPOSER_SECRET"))


def _live_symphony_id() -> str:
    """A real symphony id to probe.

    Prefer an env override (LIVE_SYMPHONY_ID) so the test isn't pinned to one
    account's holdings. Falls back to the id captured in the validation
    research doc ('Planet of Hunted Cascades').
    """
    return os.environ.get("LIVE_SYMPHONY_ID", "hvPiGP1O7AHfutHE3Fjy")


@pytest.fixture(scope="module")
def live_score_response():
    """GET the real /score response once for the module."""
    if not _composer_creds_present():
        pytest.skip("Composer credentials not in environment — skipping live test")

    import symphony_logic

    raw = symphony_logic.fetch_symphony_score(_live_symphony_id())
    assert isinstance(raw, dict), "live /score response should parse to a dict"
    return raw


def test_live_score_response_has_required_top_level_keys(live_score_response):
    """The real /score response still carries the keys condense depends on."""
    missing = _REQUIRED_TOP_LEVEL_KEYS - set(live_score_response.keys())
    assert not missing, (
        f"Composer /score response is missing keys {missing} — the committed "
        f"fixtures are STALE; recapture via /api-fixture"
    )


def test_live_score_root_step_is_root(live_score_response):
    """The tree root still self-identifies with step == 'root'."""
    assert live_score_response.get("step") == "root"


def test_live_score_children_is_a_nonempty_list(live_score_response):
    """The decision tree still lives under a populated `children` list."""
    children = live_score_response.get("children")
    assert isinstance(children, list)
    assert len(children) > 0, "live symphony has no decision-tree children"


def test_live_score_tree_uses_expected_step_vocabulary(live_score_response):
    """Walking the real tree, the `step` node types overlap our known
    vocabulary — if Composer renamed step types, condense would misparse."""
    seen_steps = set()

    def walk(node):
        if not isinstance(node, dict):
            return
        if node.get("step"):
            seen_steps.add(node["step"])
        for child in node.get("children", []) or []:
            walk(child)

    walk(live_score_response)
    assert seen_steps, "no step-typed nodes found in live tree"
    # At minimum the core branching/leaf vocabulary must still be present.
    overlap = seen_steps & _EXPECTED_STEP_VALUES
    assert overlap, (
        f"live tree step vocabulary {seen_steps} shares nothing with the "
        f"expected set {_EXPECTED_STEP_VALUES} — Composer schema drift"
    )


def test_live_score_has_asset_nodes_with_tickers(live_score_response):
    """At least one `asset` node with a `ticker` exists — the condense
    `asset_universe` extraction depends on this node/field shape."""
    found = []

    def walk(node):
        if not isinstance(node, dict):
            return
        if node.get("step") == "asset" and node.get("ticker"):
            found.append(node["ticker"])
        for child in node.get("children", []) or []:
            walk(child)

    walk(live_score_response)
    assert found, "no asset nodes with tickers in live /score response"


def test_live_score_condenses_without_error(live_score_response):
    """End-to-end: the real response condenses to a prompt-sized summary.

    This is the ultimate drift check — not just keys present, but the full
    condense pipeline survives a real payload.
    """
    import json

    import symphony_logic

    summary = symphony_logic.condense_symphony_logic(live_score_response)
    assert isinstance(summary, dict)
    assert summary["name"], "condensed summary lost the symphony name"
    assert len(summary["asset_universe"]) > 0
    # Same prompt-size contract as the fixture tests.
    assert len(json.dumps(summary).encode("utf-8")) < 8192
