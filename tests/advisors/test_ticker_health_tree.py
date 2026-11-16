"""The dead-ticker check's tree walker: which tickers a live tree names, and how many nodes use each.

Module under test: advisors.ticker_health (``tree_ticker_uses``, ``TickerUse``,
``is_plain_ticker``, ``asset_class_covered``), plus the pin that the two existing ticker readers
(``symphony_schema.extract_tickers`` and ``asset_swap_engine.extract_tickers``) are unchanged.

Contract pinned here (the implementer reads this file, not the plan):

- ``tree_ticker_uses(tree) -> dict[str, TickerUse]``; ``TickerUse`` is a frozen dataclass with the
  ints ``asset_nodes`` and ``conditions``. The walk never recurses (a tree 5000 levels deep must
  not raise ``RecursionError``) and never raises on malformed input (a non-dict tree reads as
  ``{}``; a non-dict node, a non-list ``children`` and a non-string ticker are skipped).
- Asset node: a node with ``step == "asset"`` and a string ``ticker`` other than ``"%"`` adds 1 to
  that ticker's ``asset_nodes``.
- Condition node: a node that is not an else branch (``is-else-condition?`` not true) and that is
  an ``if-child`` or carries a dict ``condition``. Its operand set is
    * with a dict ``condition`` (compound or binary-compound block): the tickers of that block
      only, at any depth of ``conditions`` nesting: ``lhs.ticker`` and ``rhs.ticker`` of binary
      blocks, the ``tickers`` list of binary-compound blocks, never ``"%"`` and never a constant.
      The node's own ``lhs-val`` and ``rhs-val`` are display labels there and are not read;
    * without one (the legacy form): ``lhs-val`` when it is a string, and ``rhs-val`` when it is a
      string and ``rhs-fixed-value?`` is not ``True``.
  Each distinct ticker of one node's operand set adds 1 to that ticker's ``conditions``, so a node
  naming a ticker on both sides, or in two clauses, counts once.
- A string that is a ticker in form only (``BRK/B``, ``A B``, ``..``) is still returned: the runner
  reports it not checked. ``is_plain_ticker`` is the form test; ``asset_class_covered`` is false
  only when the top-level ``asset_class`` key is present and is not ``"EQUITIES"``.

Fixture provenance. Whole-tree tests run on captured producer output: the live score body
``tests/fixtures/composer/ticker-health/score_8FAXAnQmYi1INDubazeC.json``, the complete
``response_body`` of ``tests/fixtures/composer/tenure-review/case4_definition_nOyb55RMGVCKPiYXv7TI.json``
and the eleven trimmed real trees under ``tests/fixtures/advisors/frontrunner/`` (core tickers
replaced by placeholders, so they test operand coverage and never a verdict). Expected counts come
from an independent recursive reading in this file, never from a literal copied from a run. The
small trees below are DERIVED: each node has the keys of the captured node form it stands for
(an asset node, a legacy if-child with ``lhs-val``/``rhs-val``, a compound if-child with a
``condition`` block of ``binary``, ``binary-compound`` and ``compound`` clauses, as in the case4
capture), with invented tickers; they are labelled derived and prove one rule each.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from advisors import asset_swap_engine, symphony_schema
from advisors import ticker_health as th

REPO = Path(__file__).resolve().parents[2]
SCORE_PATH = "tests/fixtures/composer/ticker-health/score_8FAXAnQmYi1INDubazeC.json"
CASE4_PATH = (
    "tests/fixtures/composer/tenure-review/case4_definition_nOyb55RMGVCKPiYXv7TI.json#response_body"
)
FRONTRUNNER_PATHS = sorted(
    p.relative_to(REPO).as_posix()
    for p in (REPO / "tests/fixtures/advisors/frontrunner").glob("real_tree_*.json")
)
PINS_PATH = REPO / "tests/fixtures/advisors/ticker_health/extract_tickers_ref_pins.json"


def load_tree(path: str) -> dict:
    """A captured tree by repo-relative path; ``#response_body`` reads a capture's body."""
    file_part, _, pointer = path.partition("#")
    document = json.loads((REPO / file_part).read_text(encoding="utf-8"))
    return document[pointer] if pointer else document


def uses_of(tree) -> dict[str, tuple[int, int]]:
    """The walker's result as plain tuples (asset_nodes, conditions) per ticker."""
    result = th.tree_ticker_uses(tree)
    assert isinstance(result, dict)
    return {t: (u.asset_nodes, u.conditions) for t, u in result.items()}


# ---------------------------------------------------------------------------
# Derived node builders (shapes of the captured node forms, invented tickers)
# ---------------------------------------------------------------------------

_counter = iter(range(10**9))


def _id() -> str:
    return f"node-{next(_counter):08d}"


def asset(ticker):
    return {"step": "asset", "ticker": ticker, "id": _id(), "exchange": "ARCA", "name": "x"}


def root(*children):
    return {
        "step": "root",
        "id": _id(),
        "name": "t",
        "rebalance": "daily",
        "children": list(children),
    }


def group(*children):
    return {"step": "group", "id": _id(), "name": "g", "children": list(children)}


def if_node(*children):
    return {"step": "if", "id": _id(), "children": list(children)}


def legacy_if(lhs, rhs, *, fixed="absent", children=()):
    node = {
        "step": "if-child",
        "id": _id(),
        "is-else-condition?": False,
        "lhs-fn": "moving-average-return",
        "lhs-fn-params": {"window": 118},
        "lhs-val": lhs,
        "comparator": "gt",
        "rhs-val": rhs,
        "children": list(children),
    }
    if fixed != "absent":
        node["rhs-fixed-value?"] = fixed
    return node


def else_child(*children, **extra):
    node = {"step": "if-child", "id": _id(), "is-else-condition?": True, "children": list(children)}
    node.update(extra)
    return node


def compound_if(block, *, lhs_label=None, rhs_label=None, children=()):
    node = {
        "step": "if-child",
        "id": _id(),
        "is-else-condition?": False,
        "condition": block,
        "children": list(children),
    }
    if lhs_label is not None:
        node["lhs-val"] = lhs_label
    if rhs_label is not None:
        node["rhs-val"] = rhs_label
        node["rhs-fixed-value?"] = True
    return node


def binary_block(lhs_ticker, rhs=None):
    block = {
        "condition-type": "binary",
        "lhs": {"fn": "relative-strength-index", "ticker": lhs_ticker, "params": {"window": 10}},
        "comparator": "gt",
        "rhs": rhs if rhs is not None else {"constant": 72.0},
    }
    return block


def binary_compound_block(tickers, *, placeholder="%"):
    return {
        "condition-type": "binary-compound",
        "lhs": {"fn": "relative-strength-index", "ticker": placeholder, "params": {"window": 10}},
        "tickers": list(tickers),
        "comparator": "gt",
        "operator": "any",
        "rhs": {"constant": 80.0},
    }


def compound_block(*clauses, operator="any"):
    return {"condition-type": "compound", "conditions": list(clauses), "operator": operator}


# ---------------------------------------------------------------------------
# Rules, one derived tree each
# ---------------------------------------------------------------------------


class TestAssetNodes:
    def test_asset_only_ticker_counts_every_asset_node(self):
        tree = root(group(asset("QXGD"), asset("QXGD")), asset("QXGD"), asset("SPY"))
        assert uses_of(tree) == {"QXGD": (3, 0), "SPY": (1, 0)}

    def test_placeholder_percent_asset_ticker_is_never_returned(self):
        assert uses_of(root(asset("%"), asset("SPY"))) == {"SPY": (1, 0)}

    def test_a_non_asset_node_ticker_is_not_an_asset_use(self):
        # A ticker key on a node that is not an asset node (a filter's metadata) is no asset.
        tree = root({"step": "filter", "id": _id(), "ticker": "NOPE", "children": [asset("SPY")]})
        assert "NOPE" not in uses_of(tree)

    def test_a_ticker_in_form_only_is_returned_for_the_runner_to_report(self):
        tree = root(asset("BRK/B"), asset("A B"), asset(".."), asset("BRK.B"))
        assert set(uses_of(tree)) == {"BRK/B", "A B", "..", "BRK.B"}


class TestLegacyConditionOperands:
    def test_lhs_val_alone_is_a_condition_use_with_no_asset_node(self):
        tree = root(if_node(legacy_if("QXGD", "SPY"), else_child()))
        assert uses_of(tree)["QXGD"] == (0, 1)

    @pytest.mark.parametrize("fixed", ["absent", False])
    def test_rhs_val_is_judged_when_the_fixed_flag_is_absent_or_false(self, fixed):
        tree = root(if_node(legacy_if("SPY", "QXGD", fixed=fixed), else_child()))
        assert uses_of(tree) == {"SPY": (0, 1), "QXGD": (0, 1)}

    @pytest.mark.parametrize("rhs", ["9.25", "80", "ZZZ"])
    def test_a_fixed_rhs_val_is_never_judged_whatever_it_looks_like(self, rhs):
        tree = root(if_node(legacy_if("SPY", rhs, fixed=True), else_child()))
        assert uses_of(tree) == {"SPY": (0, 1)}

    def test_a_non_string_rhs_val_is_never_judged(self):
        tree = root(if_node(legacy_if("SPY", 9.25), else_child()))
        assert uses_of(tree) == {"SPY": (0, 1)}

    def test_an_else_branch_is_not_a_condition_even_if_it_carries_operand_keys(self):
        else_with_keys = else_child(**{"lhs-val": "ZZZ", "rhs-val": "YYY"})
        tree = root(if_node(legacy_if("SPY", "9", fixed=True), else_with_keys))
        assert uses_of(tree) == {"SPY": (0, 1)}

    def test_each_node_naming_the_ticker_counts_once_per_node(self):
        tree = root(
            if_node(legacy_if("QXGD", "SPY"), else_child()),
            if_node(legacy_if("SPY", "QXGD"), else_child()),
        )
        assert uses_of(tree) == {"QXGD": (0, 2), "SPY": (0, 2)}


class TestConditionBlocks:
    def test_binary_compound_tickers_are_judged_and_the_percent_placeholder_never(self):
        block = binary_compound_block(["QXGD", "PLRZ"])
        tree = root(if_node(compound_if(block), else_child()))
        assert uses_of(tree) == {"QXGD": (0, 1), "PLRZ": (0, 1)}

    def test_a_single_binary_compound_node_without_compound_wrapper(self):
        tree = root(if_node(compound_if(binary_compound_block(["QXGD"])), else_child()))
        assert "%" not in uses_of(tree)

    def test_binary_block_lhs_and_rhs_tickers_are_both_judged_and_a_constant_is_not(self):
        both = binary_block("QXGD", rhs={"fn": "relative-strength-index", "ticker": "PLRZ"})
        constant = binary_block("SPY", rhs={"constant": 72.0})
        tree = root(if_node(compound_if(both), compound_if(constant), else_child()))
        assert uses_of(tree) == {"QXGD": (0, 1), "PLRZ": (0, 1), "SPY": (0, 1)}

    def test_a_ticker_nested_two_compounds_deep_is_judged(self):
        inner = compound_block(binary_block("QXGD"), binary_block("SPY"))
        middle = compound_block(binary_block("PLRZ"), inner, operator="all")
        tree = root(if_node(compound_if(compound_block(middle)), else_child()))
        assert uses_of(tree) == {"QXGD": (0, 1), "SPY": (0, 1), "PLRZ": (0, 1)}

    def test_a_comma_joined_label_is_never_judged_and_its_member_tickers_are(self):
        # The label of a compound if-child is the comma-joined list of its block's tickers.
        block = compound_block(binary_compound_block(["QXGD", "PLRZ", "SPY"]))
        node = compound_if(block, lhs_label="QXGD,PLRZ,SPY")
        assert uses_of(root(if_node(node, else_child()))) == {
            "QXGD": (0, 1),
            "PLRZ": (0, 1),
            "SPY": (0, 1),
        }

    def test_labels_on_a_compound_node_are_display_text_and_never_read(self):
        block = compound_block(binary_block("QXGD"))
        node = compound_if(block, lhs_label="LABELONLY", rhs_label="ALSOLABEL")
        assert set(uses_of(root(if_node(node, else_child())))) == {"QXGD"}

    def test_a_ticker_named_in_two_clauses_of_one_block_counts_one_condition(self):
        block = compound_block(binary_block("QXGD"), binary_compound_block(["QXGD", "SPY"]))
        tree = root(if_node(compound_if(block), else_child()))
        assert uses_of(tree) == {"QXGD": (0, 1), "SPY": (0, 1)}

    def test_a_ticker_on_both_sides_of_one_binary_block_counts_one_condition(self):
        block = binary_block("QXGD", rhs={"fn": "relative-strength-index", "ticker": "QXGD"})
        assert uses_of(root(if_node(compound_if(block), else_child()))) == {"QXGD": (0, 1)}

    def test_an_else_branch_carrying_a_condition_block_is_not_a_condition(self):
        else_with_block = else_child(condition=binary_block("ZZZ"))
        tree = root(if_node(compound_if(binary_block("SPY")), else_with_block))
        assert uses_of(tree) == {"SPY": (0, 1)}


class TestMixedUse:
    def test_a_dead_ticker_bought_25_times_and_compared_with_itself_counts_once_as_condition(self):
        both_sides = legacy_if("QXGD", "QXGD", fixed=False)
        tree = root(
            *[group(asset("QXGD")) for _ in range(25)],
            if_node(both_sides, else_child()),
        )
        assert uses_of(tree) == {"QXGD": (25, 1)}

    def test_asset_and_condition_uses_are_counted_separately_for_one_ticker(self):
        tree = root(
            asset("QXGD"),
            asset("QXGD"),
            if_node(compound_if(binary_block("QXGD")), legacy_if("QXGD", "SPY"), else_child()),
        )
        assert uses_of(tree) == {"QXGD": (2, 2), "SPY": (0, 1)}

    def test_a_condition_only_ticker_has_no_asset_node(self):
        tree = root(asset("SPY"), if_node(legacy_if("QXGD", "5", fixed=True), else_child()))
        assert uses_of(tree)["QXGD"] == (0, 1)


class TestWalkSafety:
    def test_a_tree_5000_levels_deep_is_walked_without_recursion(self):
        node = asset("QXGD")
        for _ in range(5000):
            node = group(node)
        assert uses_of(root(node)) == {"QXGD": (1, 0)}

    def test_a_condition_block_3000_levels_deep_is_walked_without_recursion(self):
        block = binary_block("QXGD")
        for _ in range(3000):
            block = compound_block(block)
        assert uses_of(root(if_node(compound_if(block), else_child()))) == {"QXGD": (0, 1)}

    @pytest.mark.parametrize(
        "tree", [None, [], "text", 7, {}, {"children": None}, {"step": "root"}]
    )
    def test_a_tree_without_nodes_reads_empty(self, tree):
        assert th.tree_ticker_uses(tree) == {}

    def test_malformed_nodes_are_skipped_and_the_rest_is_still_read(self):
        tree = root(
            "not a node",
            None,
            {"step": "asset", "ticker": 7},
            {"step": "asset", "ticker": None},
            {"step": "group", "children": "not a list"},
            asset("SPY"),
        )
        assert uses_of(tree) == {"SPY": (1, 0)}

    def test_the_tree_is_not_modified(self):
        tree = root(asset("SPY"), if_node(compound_if(binary_block("QXGD")), else_child()))
        before = copy.deepcopy(tree)
        th.tree_ticker_uses(tree)
        assert tree == before

    def test_a_use_is_a_frozen_record(self):
        use = th.tree_ticker_uses(root(asset("SPY")))["SPY"]
        assert dataclasses.is_dataclass(use)
        with pytest.raises(dataclasses.FrozenInstanceError):
            use.asset_nodes = 99
        assert th.TickerUse(asset_nodes=1, conditions=0) == use


# ---------------------------------------------------------------------------
# Captured trees, read against an independent recursive walk
# ---------------------------------------------------------------------------


def independent_uses(tree) -> dict[str, tuple[int, int]]:
    """The same rules read recursively and separately from the module under test."""
    assets: Counter = Counter()
    conditions: Counter = Counter()

    def block_tickers(block) -> set[str]:
        found: set[str] = set()
        if not isinstance(block, dict):
            return found
        for side in ("lhs", "rhs"):
            operand = block.get(side)
            if isinstance(operand, dict):
                ticker = operand.get("ticker")
                if isinstance(ticker, str) and ticker != "%":
                    found.add(ticker)
        for ticker in block.get("tickers") or []:
            if isinstance(ticker, str) and ticker != "%":
                found.add(ticker)
        for sub in block.get("conditions") or []:
            found |= block_tickers(sub)
        return found

    def visit(node) -> None:
        if not isinstance(node, dict):
            return
        ticker = node.get("ticker")
        if node.get("step") == "asset" and isinstance(ticker, str) and ticker != "%":
            assets[ticker] += 1
        is_condition = not node.get("is-else-condition?") and (
            node.get("step") == "if-child" or isinstance(node.get("condition"), dict)
        )
        if is_condition:
            if isinstance(node.get("condition"), dict):
                operands = block_tickers(node["condition"])
            else:
                operands = set()
                if isinstance(node.get("lhs-val"), str):
                    operands.add(node["lhs-val"])
                if (
                    isinstance(node.get("rhs-val"), str)
                    and node.get("rhs-fixed-value?") is not True
                ):
                    operands.add(node["rhs-val"])
            for operand in operands:
                conditions[operand] += 1
        for child in node.get("children") or []:
            visit(child)

    visit(tree)
    return {t: (assets[t], conditions[t]) for t in set(assets) | set(conditions)}


CAPTURED_TREES = [SCORE_PATH, CASE4_PATH, *FRONTRUNNER_PATHS]


class TestCapturedTrees:
    def test_the_eleven_trimmed_real_trees_are_found(self):
        assert len(FRONTRUNNER_PATHS) == 11

    @pytest.mark.parametrize("path", CAPTURED_TREES)
    def test_every_ticker_and_both_counts_match_an_independent_reading(self, path):
        tree = load_tree(path)
        assert uses_of(tree) == independent_uses(tree)

    @pytest.mark.parametrize("path", CAPTURED_TREES)
    def test_every_captured_tree_has_tickers_only_a_condition_reads(self, path):
        # Non-vacuity: the reason for the walker is tickers no asset node holds.
        result = th.tree_ticker_uses(load_tree(path))
        assert any(u.asset_nodes == 0 and u.conditions > 0 for u in result.values())

    @pytest.mark.parametrize("path", CAPTURED_TREES)
    def test_no_placeholder_label_or_fixed_value_is_ever_judged(self, path):
        result = th.tree_ticker_uses(load_tree(path))
        assert "%" not in result
        assert not [t for t in result if "," in t]
        assert not [t for t in result if re.fullmatch(r"[0-9.]+", t)]

    def test_the_live_score_tree_reports_the_dead_ticker_of_the_incident_by_its_own_counts(self):
        tree = load_tree(SCORE_PATH)
        asset_nodes, conditions = independent_uses(tree)["SHNY"]
        assert asset_nodes > 0 and conditions > 0  # the capture names SHNY in both ways
        assert uses_of(tree)["SHNY"] == (asset_nodes, conditions)

    def test_a_condition_comparing_the_ticker_with_itself_is_one_condition_not_two(self):
        tree = load_tree(SCORE_PATH)
        comparing_itself = [
            n for n in _all_nodes(tree) if n.get("lhs-val") == "SHNY" and n.get("rhs-val") == "SHNY"
        ]
        assert comparing_itself, "the capture holds a SHNY-vs-SHNY condition"
        naming_it = [
            n
            for n in _all_nodes(tree)
            if n.get("step") == "if-child" and "SHNY" in (n.get("lhs-val"), n.get("rhs-val"))
        ]
        assert th.tree_ticker_uses(tree)["SHNY"].conditions == len(naming_it)

    def test_the_comma_joined_label_of_the_complete_capture_is_not_a_ticker(self):
        tree = load_tree(CASE4_PATH)
        labels = [n["lhs-val"] for n in _all_nodes(tree) if "," in str(n.get("lhs-val", ""))]
        assert labels, "the capture holds a comma-joined compound label"
        assert not set(labels) & set(th.tree_ticker_uses(tree))

    def test_every_ticker_of_the_live_score_tree_is_plain_in_form(self):
        assert all(th.is_plain_ticker(t) for t in th.tree_ticker_uses(load_tree(SCORE_PATH)))


def _all_nodes(tree):
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(node.get("children") or [])


# ---------------------------------------------------------------------------
# Form tests
# ---------------------------------------------------------------------------


class TestPlainTicker:
    @pytest.mark.parametrize("text", ["SPY", "A", "SHNY", "TQQQ", "BRK.B", "X1", "UGL", "BF.B"])
    def test_plain_symbols_are_accepted(self, text):
        assert th.is_plain_ticker(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "BRK/B",
            "A B",
            "..",
            "",
            "spy",
            "1ABC",
            "CORE_ASSET_0001",
            "BRK.",
            "A..B",
            "A.B.C",
            "SPY\n",
            " SPY",
            "SPY ",
            "%",
            "SPY​",
            "ＳＰＹ",
            "SPY?x=1",
            "../x",
            None,
            7,
            ["SPY"],
        ],
    )
    def test_anything_else_is_rejected_without_raising(self, text):
        assert th.is_plain_ticker(text) is False


class TestAssetClassCovered:
    @pytest.mark.parametrize(
        ("tree", "covered"),
        [
            ({"step": "root", "children": []}, True),
            ({"step": "root", "asset_class": "EQUITIES", "children": []}, True),
            ({"step": "root", "asset_class": "CRYPTO", "children": []}, False),
            ({"step": "root", "asset_class": "OPTIONS", "children": []}, False),
            ({"step": "root", "asset_class": "equities", "children": []}, False),
        ],
        ids=["absent", "equities", "crypto", "options", "wrong-case"],
    )
    def test_only_a_present_class_other_than_equities_is_not_covered(self, tree, covered):
        assert th.asset_class_covered(tree) is covered

    def test_the_captures_are_covered(self):
        assert th.asset_class_covered(load_tree(SCORE_PATH)) is True  # asset_class EQUITIES
        assert th.asset_class_covered(load_tree(CASE4_PATH)) is True  # no asset_class key


# ---------------------------------------------------------------------------
# The existing ticker readers keep their results (their other callers must not change)
# ---------------------------------------------------------------------------


def _digest(tickers: set) -> dict:
    return {
        "size": len(tickers),
        "sha256": hashlib.sha256("\n".join(sorted(tickers)).encode()).hexdigest(),
    }


class TestExistingTickerReadersUnchanged:
    """Behaviour present at the base: these pass before and after the change (a characterization)."""

    @pytest.mark.parametrize("path", CAPTURED_TREES)
    def test_both_existing_readers_return_what_they_returned_at_the_ref(self, path):
        pins = json.loads(PINS_PATH.read_text(encoding="utf-8"))["pins"]
        tree = load_tree(path)
        assert _digest(symphony_schema.extract_tickers(tree)) == pins[path]["symphony_schema"]
        assert _digest(asset_swap_engine.extract_tickers(tree)) == pins[path]["asset_swap_engine"]

    @pytest.mark.parametrize("path", [SCORE_PATH, CASE4_PATH])
    def test_the_existing_reader_still_misses_the_legacy_only_operands(self, path):
        # The check reads legacy operands itself; it must not "fix" extract_tickers for its
        # other callers, so the tickers that only a legacy operand names stay absent from it.
        tree = load_tree(path)
        legacy_only = {t for t, (a, c) in independent_uses(tree).items() if a == 0 and c > 0}
        assert legacy_only - symphony_schema.extract_tickers(tree)

    @pytest.mark.parametrize(
        "module",
        [
            "community_strats",
            "plan_tree_compiler",
            "weekly_suggestions_scheduler",
            "asset_swap_engine",
        ],
    )
    def test_the_existing_callers_do_not_read_the_checks_walker(self, module):
        source = (REPO / "advisors" / f"{module}.py").read_text(encoding="utf-8")
        assert "ticker_health" not in source
        assert "extract_tickers" in source
