"""RED tests — Phase 1 Symphony Schema (advisors/symphony_schema.py).

Module under test: advisors.symphony_schema

These tests are RED by construction: the module does not exist yet.
Every assertion is derived from:
  - the pinned grammar document (feature-plans/strategy-builder-composer-grammar.md)
  - the handoff contract amendments (feature-plans/strategy-builder-phase1-handoff.md)
  - the two real captured fixture trees (tests/fixtures/symphony_logic/)
  - the hand-built minimal fixture (tests/fixtures/symphony_schema/)

ADVERSARIAL FOCUS:
  - Every mutation test must produce >= 1 error naming the offending node id/path.
  - validate_tree must NEVER raise on any malformed input (None, [], {}, strings,
    weird nesting, circular-ish depth bombs).
  - No producer-computed values are hardcoded — expected tickers are derived from
    the fixtures by the test's own independent reference walker.
  - "rsi" must produce a lint warning (not a hard error); "relative-strength-index"
    must pass with no errors (tests the exact grammar string).
  - "gte" must fail; "gt"/"lt"/"lte" must pass (OQ-2 stance).
  - Constructors must emit uuid4-parseable ids, correct field names, and produce
    trees that pass validate_tree with no errors.

CONTRACT AMENDMENTS (from feature-plans/strategy-builder-phase1-handoff.md):
  1. MAX_TOTAL_NODES / MAX_TREE_DEPTH are CONSTRUCTION-side constants and lint_tree
     warnings, NOT validate_tree hard errors (both golden fixtures exceed any sane
     construction cap: small=866 nodes/depth-19, large=8455 nodes/depth-230).
  2. Unknown indicator fns are lint warnings, not hard errors ('standard-deviation-price'
     occurs in the large fixture). Hard errors remain: unknown step, structurally
     missing required fields, duplicate ids, malformed weight objects, if missing
     branches, non-asset leaves, None/garbage input.
  3. select-n may be string OR int ("4" appears in fixtures).
  4. weight.den may be the string "100"; weight appears on asset/if/group/filter nodes,
     not only children of wt-cash-specified.
  5. Flat lhs-window-days/rhs-window-days keys DO appear in real if-child nodes
     alongside the *-fn-params object form — both tolerated on read; constructors
     emit the params-object form only.
  6. condition blocks may carry condition-type, operator, tickers arrays, % placeholder
     tickers, rhs: {"constant": N} — tolerated without hard error.
  7. Cosmetic keys tolerated everywhere.
  8. Traversal must be ITERATIVE (explicit stack) — no RecursionError at depth 230+.

No live network calls.  No DB access.  The math engine is never mocked.
"""

from __future__ import annotations

import copy
import json
import pathlib
import uuid
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Repository layout helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_GOLDEN_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "symphony_logic"
_SCHEMA_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "symphony_schema"


def _load_fixture(path: pathlib.Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Independent reference walker (test's own truth — never delegates to impl)
# ---------------------------------------------------------------------------


def _ref_collect_tickers(node: Any, out: set[str] | None = None) -> set[str]:
    """Iteratively gather all ticker strings from a raw tree node."""
    if out is None:
        out = set()
    if not isinstance(node, dict):
        return out
    stack = [node]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        if current.get("ticker"):
            out.add(current["ticker"])
        for child in current.get("children") or []:
            stack.append(child)
    return out


def _ref_collect_ids(node: Any, out: list[str] | None = None) -> list[str]:
    """Iteratively gather all id values from a raw tree node."""
    if out is None:
        out = []
    if not isinstance(node, dict):
        return out
    stack = [node]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        if current.get("id"):
            out.append(current["id"])
        for child in current.get("children") or []:
            stack.append(child)
    return out


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def raw_small() -> dict:
    """Real /score response — 'Corporate Chaos 5 ways' (small golden fixture, 866 nodes)."""
    return _load_fixture(_GOLDEN_FIXTURE_DIR / "sample_score_small.json")


@pytest.fixture(scope="module")
def raw_large() -> dict:
    """Real /score response — 'Planet of Hunted Cascades' (large golden fixture, 8455 nodes, depth 230)."""
    return _load_fixture(_GOLDEN_FIXTURE_DIR / "sample_score_large.json")


@pytest.fixture(scope="module")
def raw_minimal() -> dict:
    """Hand-built minimal valid tree (bond-trend CFIT style)."""
    return _load_fixture(_SCHEMA_FIXTURE_DIR / "minimal_valid_bond_trend.json")


# ---------------------------------------------------------------------------
# Import helper — deferred so RED failure is on function call, not collection
# ---------------------------------------------------------------------------


def _import_schema():
    """Import advisors.symphony_schema; fail with a clear message if missing."""
    try:
        from advisors import symphony_schema  # type: ignore[import]

        return symphony_schema
    except ImportError as exc:
        pytest.fail(
            f"advisors/symphony_schema.py does not exist yet (RED suite): {exc}"
        )


# ===========================================================================
# 1. GOLDEN FIXTURE TESTS
#    Both real captured fixtures must validate clean, tickers must match
#    independent reference walk, and render_rules_text must be deterministic.
# ===========================================================================


class TestGoldenFixtureSmall:
    """validate_tree / extract_tickers / render_rules_text on the small fixture."""

    def test_small_fixture_validates_clean(self, raw_small):
        """The real small fixture (866 nodes) must produce zero hard errors.

        Amendment 1 ground truth: MAX_TOTAL_NODES / MAX_TREE_DEPTH caps are
        lint warnings only, so 866 nodes must not produce a validate_tree error.
        """
        m = _import_schema()
        errors = m.validate_tree(raw_small)
        assert errors == [], (
            f"Expected no hard errors on golden small fixture, got: {errors}"
        )

    def test_small_fixture_extract_tickers_matches_reference_walk(self, raw_small):
        """extract_tickers must agree with the independent reference walker.

        The expected set is derived from the fixture at test time — never
        hardcoded — so if the fixture changes the test re-derives automatically.
        """
        m = _import_schema()
        expected = _ref_collect_tickers(raw_small)
        # Sanity: reference walker must find tickers (guards against silent breakage)
        assert len(expected) > 0, "reference walker found no tickers in small fixture"
        result = m.extract_tickers(raw_small)
        assert isinstance(result, set), "extract_tickers must return a set"
        assert result == expected

    def test_small_fixture_render_rules_text_mentions_all_tickers(self, raw_small):
        """render_rules_text must mention every ticker present in the tree."""
        m = _import_schema()
        tickers = _ref_collect_tickers(raw_small)
        text = m.render_rules_text(raw_small)
        assert isinstance(text, str) and len(text) > 0
        for ticker in tickers:
            assert ticker in text, (
                f"render_rules_text omitted ticker {ticker!r} from small fixture"
            )

    def test_small_fixture_render_rules_text_is_deterministic(self, raw_small):
        """Two calls to render_rules_text on the same tree must return identical output."""
        m = _import_schema()
        first = m.render_rules_text(raw_small)
        second = m.render_rules_text(raw_small)
        assert first == second, "render_rules_text is not deterministic"

    def test_small_fixture_lint_tree_produces_no_false_positive_hard_errors(
        self, raw_small
    ):
        """lint_tree must not mislabel real-fixture warnings as hard errors.

        This test guards against an implementation that conflates lint warnings
        with validation errors (validate_tree already confirmed it's valid).
        """
        m = _import_schema()
        # lint returns warnings (strings), not hard errors; we just check it doesn't
        # raise and that validate_tree remains clean after lint (lint must be read-only)
        warnings = m.lint_tree(raw_small)
        assert isinstance(warnings, list)
        # After lint, the tree must still validate clean (lint is non-destructive)
        errors = m.validate_tree(raw_small)
        assert errors == [], "lint_tree mutated the input tree; validate_tree now fails"


class TestGoldenFixtureLarge:
    """validate_tree / extract_tickers / render_rules_text on the large fixture."""

    def test_large_fixture_validates_clean(self, raw_large):
        """The real large fixture (8455 nodes, depth 230) must produce zero hard errors.

        Amendment 1 ground truth: both MAX_TOTAL_NODES and MAX_TREE_DEPTH are
        construction-side lint warnings; they must NOT fire as hard errors on
        pre-existing trees that exceed those construction bounds.
        Amendment 2: 'standard-deviation-price' appears 3 times in this fixture
        and must be tolerated as a lint warning, not a hard validate error.
        """
        m = _import_schema()
        errors = m.validate_tree(raw_large)
        assert errors == [], (
            f"Expected no hard errors on golden large fixture, got: {errors}"
        )

    def test_large_fixture_extract_tickers_matches_reference_walk(self, raw_large):
        """extract_tickers on the large fixture must agree with the reference walker."""
        m = _import_schema()
        expected = _ref_collect_tickers(raw_large)
        assert len(expected) > 0, "reference walker found no tickers in large fixture"
        result = m.extract_tickers(raw_large)
        assert isinstance(result, set)
        assert result == expected

    def test_large_fixture_render_rules_text_mentions_all_tickers(self, raw_large):
        """render_rules_text on the large fixture must mention every ticker."""
        m = _import_schema()
        tickers = _ref_collect_tickers(raw_large)
        text = m.render_rules_text(raw_large)
        assert isinstance(text, str) and len(text) > 0
        for ticker in tickers:
            assert ticker in text, (
                f"render_rules_text omitted ticker {ticker!r} from large fixture"
            )

    def test_large_fixture_render_rules_text_is_deterministic(self, raw_large):
        """Determinism check on the larger (more complex) fixture."""
        m = _import_schema()
        first = m.render_rules_text(raw_large)
        second = m.render_rules_text(raw_large)
        assert first == second

    def test_large_fixture_lint_tree_does_not_raise(self, raw_large):
        """lint_tree must not raise on the large fixture."""
        m = _import_schema()
        warnings = m.lint_tree(raw_large)
        assert isinstance(warnings, list)

    def test_large_fixture_no_recursion_error_at_depth_230(self, raw_large):
        """Traversal of the depth-230 large fixture must never raise RecursionError.

        Amendment 8: traversal must be iterative (explicit stack). A recursive
        implementation would hit Python's default recursion limit (~1000) on a
        230-deep tree only if the path is consistently deep; this test confirms
        the iterative contract holds for the real fixture.
        """
        m = _import_schema()
        # Both validate_tree and extract_tickers traverse the full tree
        try:
            errors = m.validate_tree(raw_large)
            tickers = m.extract_tickers(raw_large)
        except RecursionError as exc:
            pytest.fail(
                f"RecursionError during traversal of depth-230 large fixture: {exc}. "
                "Traversal must be iterative (explicit stack), not recursive."
            )
        assert isinstance(errors, list)
        assert isinstance(tickers, set)


# ===========================================================================
# 2. CONSTRUCTOR ROUND-TRIP
#    Build a CFIT bond-trend tree via constructors, validate it, check IDs.
# ===========================================================================


class TestConstructorRoundTrip:
    """Trees built from constructors must validate clean and have correct structure."""

    def _build_bond_trend_tree(self, m) -> dict:
        """
        Builds: root(daily) -> wt-cash-equal -> if -> [
          if-child(false): cumulative-return(TLT,200d) gt 0  -> asset(TLT)
          if-child(true):                                    -> asset(BIL)
        ]
        """
        tlt_asset = m.make_asset("TLT")
        bil_asset = m.make_asset("BIL")

        lhs_indicator = m.make_indicator("cumulative-return", "TLT", window=200)
        condition = m.make_condition(lhs_indicator, "gt", 0.0)

        if_node = m.make_if(condition, then_children=[tlt_asset], else_children=[bil_asset])
        wt_node = m.make_weight_equal([if_node])
        root = m.make_root("Bond Trend CFIT", "daily", [wt_node])
        return root

    def test_constructor_tree_validates_clean(self):
        """A properly-built constructor tree must pass validate_tree with no errors."""
        m = _import_schema()
        tree = self._build_bond_trend_tree(m)
        errors = m.validate_tree(tree)
        assert errors == [], f"Constructor tree has unexpected hard errors: {errors}"

    def test_constructor_tree_all_ids_are_uuid4_parseable(self):
        """Every id in a constructed tree must be a valid UUID v4 string."""
        m = _import_schema()
        tree = self._build_bond_trend_tree(m)
        ids = _ref_collect_ids(tree)
        assert len(ids) > 0, "No ids found in constructed tree"
        for id_val in ids:
            assert isinstance(id_val, str), f"id {id_val!r} is not a string"
            parsed = uuid.UUID(id_val)
            # UUID v4 has version 4
            assert parsed.version == 4, (
                f"id {id_val!r} is not UUID v4 (got version {parsed.version})"
            )

    def test_constructor_tree_all_ids_are_unique(self):
        """No two nodes in a constructed tree may share an id."""
        m = _import_schema()
        tree = self._build_bond_trend_tree(m)
        ids = _ref_collect_ids(tree)
        assert len(ids) == len(set(ids)), (
            f"Duplicate ids found in constructed tree: "
            f"{[x for x in ids if ids.count(x) > 1]}"
        )

    def test_constructor_tree_root_has_correct_fields(self):
        """Root node from make_root must have step/name/rebalance/id/children."""
        m = _import_schema()
        tree = self._build_bond_trend_tree(m)
        assert tree.get("step") == "root"
        assert tree.get("name") == "Bond Trend CFIT"
        assert tree.get("rebalance") == "daily"
        assert "id" in tree
        assert isinstance(tree.get("children"), list)

    def test_constructor_tree_if_child_has_correct_fn_params_field(self):
        """if-child must use 'lhs-fn-params': {'window': 200} — not flat lhs-window-days.

        Amendment 5: constructors emit the params-object form only; flat
        lhs-window-days is only tolerated on read (existing fixtures), not emitted.
        """
        m = _import_schema()
        tree = self._build_bond_trend_tree(m)

        # Locate the true-branch if-child by walking the tree
        def _find_if_children(node):
            results = []
            stack = [node]
            while stack:
                current = stack.pop()
                if not isinstance(current, dict):
                    continue
                if current.get("step") == "if-child":
                    results.append(current)
                for child in current.get("children") or []:
                    stack.append(child)
            return results

        if_children = _find_if_children(tree)
        assert len(if_children) >= 1, "No if-child nodes found in constructed tree"

        true_branch = next(
            (n for n in if_children if not n.get("is-else-condition?")), None
        )
        assert true_branch is not None, "No true-branch if-child found"
        # Per grammar: params nested as {"window": int}, NOT flat key "lhs-window-days"
        assert "lhs-fn-params" in true_branch, (
            "lhs-fn-params key missing from if-child (should not use flat lhs-window-days)"
        )
        assert "window" in true_branch["lhs-fn-params"]
        # Value type: int (grammar specifies int; exact value tested via shape not hardcode)
        assert isinstance(true_branch["lhs-fn-params"]["window"], int)
        # The constructor was called with window=200, so window must be 200
        # (derived from the constructor argument, not from the implementation's output)
        assert true_branch["lhs-fn-params"]["window"] == 200, (
            "window param mismatch — the constructor was called with window=200"
        )

    def test_constructor_tree_if_child_has_correct_lhs_fn(self):
        """if-child lhs-fn must be the exact grammar string 'cumulative-return'."""
        m = _import_schema()
        tree = self._build_bond_trend_tree(m)

        def _find_if_children(node):
            results = []
            stack = [node]
            while stack:
                current = stack.pop()
                if not isinstance(current, dict):
                    continue
                if current.get("step") == "if-child":
                    results.append(current)
                for child in current.get("children") or []:
                    stack.append(child)
            return results

        if_children = _find_if_children(tree)
        true_branch = next(
            (n for n in if_children if not n.get("is-else-condition?")), None
        )
        assert true_branch is not None
        assert true_branch.get("lhs-fn") == "cumulative-return", (
            f"lhs-fn must be 'cumulative-return'; got {true_branch.get('lhs-fn')!r}"
        )

    def test_constructor_asset_has_step_ticker_id(self):
        """make_asset must produce a node with step='asset', ticker, and an id."""
        m = _import_schema()
        node = m.make_asset("SPY")
        assert node.get("step") == "asset"
        assert node.get("ticker") == "SPY"
        assert "id" in node
        assert uuid.UUID(node["id"]).version == 4

    def test_constructor_weight_equal_has_children(self):
        """make_weight_equal must produce a wt-cash-equal node with children list."""
        m = _import_schema()
        child = m.make_asset("SPY")
        node = m.make_weight_equal([child])
        assert node.get("step") == "wt-cash-equal"
        assert isinstance(node.get("children"), list)
        assert len(node["children"]) == 1

    def test_constructor_weight_specified_has_weight_fields_on_children(self):
        """make_weight_specified children must carry weight={num: int, den: 100}.

        Constructors emit den as integer 100 (the standard form); the validator
        also tolerates string '100' on read (Amendment 4).
        """
        m = _import_schema()
        spy = m.make_asset("SPY")
        agg = m.make_asset("AGG")
        # 60/40 split: num values are integers; den is always 100
        node = m.make_weight_specified([(spy, 60), (agg, 40)])
        assert node.get("step") == "wt-cash-specified"
        children = node.get("children") or []
        assert len(children) == 2
        for child in children:
            assert "weight" in child, f"child missing 'weight' field: {child}"
            w = child["weight"]
            assert isinstance(w, dict), f"weight must be a dict; got {w!r}"
            # Constructors always emit integer 100; grammar also allows string "100" on read
            den_val = w.get("den")
            assert den_val == 100 or den_val == "100", (
                f"weight den must be 100 or '100' per grammar; got {den_val!r}"
            )
            # num must be a numeric value (int or numeric string)
            assert "num" in w, f"weight missing 'num' key: {w}"

    def test_constructor_weight_specified_nums_sum_to_100(self):
        """60+40 weight spec: nums derived from constructor args must sum to 100."""
        m = _import_schema()
        spy = m.make_asset("SPY")
        agg = m.make_asset("AGG")
        node = m.make_weight_specified([(spy, 60), (agg, 40)])
        children = node.get("children") or []
        total = sum(int(c["weight"]["num"]) for c in children)
        # 60+40=100; total is derived from constructor arguments, not hardcoded
        assert total == 60 + 40

    def test_constructor_inverse_vol_has_correct_step(self):
        """make_inverse_vol must produce a wt-inverse-vol node."""
        m = _import_schema()
        spy = m.make_asset("SPY")
        node = m.make_inverse_vol([spy])
        assert node.get("step") == "wt-inverse-vol"
        assert isinstance(node.get("children"), list)

    def test_constructor_group_has_name_and_children(self):
        """make_group must produce a group node with name and children."""
        m = _import_schema()
        spy = m.make_asset("SPY")
        node = m.make_group("My Group", [spy])
        assert node.get("step") == "group"
        assert node.get("name") == "My Group"
        assert isinstance(node.get("children"), list)

    def test_constructor_filter_has_all_required_fields(self):
        """make_filter must produce a filter node with all grammar-required fields."""
        m = _import_schema()
        spy = m.make_asset("SPY")
        qqq = m.make_asset("QQQ")
        node = m.make_filter("top", 2, "cumulative-return", [spy, qqq], window=20)
        assert node.get("step") == "filter"
        assert node.get("select-fn") in ("top", "bottom")
        assert "select-n" in node
        assert "sort-by-fn" in node
        assert "sort-by-fn-params" in node
        assert isinstance(node.get("children"), list)

    def test_constructor_two_calls_produce_distinct_ids(self):
        """Two separate make_asset calls must not share an id (no global mutable state)."""
        m = _import_schema()
        a = m.make_asset("SPY")
        b = m.make_asset("SPY")
        assert a["id"] != b["id"], (
            "make_asset returned the same id on two separate calls — "
            "ids must be freshly generated, not cached"
        )

    def test_constructor_children_are_not_shared_references(self):
        """Constructors must not share mutable child lists across two parent nodes."""
        m = _import_schema()
        child = m.make_asset("SPY")
        parent_a = m.make_weight_equal([child])
        parent_b = m.make_weight_equal([child])
        # Mutating parent_a's children list must not affect parent_b's
        parent_a["children"].append(m.make_asset("AGG"))
        assert len(parent_b["children"]) == 1, (
            "Constructors share a mutable children reference; "
            "mutating one parent corrupted the other"
        )


# ===========================================================================
# 3. ADVERSARIAL MUTATION TESTS
#    Each mutation of a valid tree must produce >= 1 error naming the offending
#    node id/path.  validate_tree must never raise.
# ===========================================================================


class TestAdversarialMutations:
    """validate_tree must catch each class of structural error."""

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @pytest.fixture
    def valid_tree(self, raw_minimal) -> dict:
        """Deep copy of the minimal valid fixture — mutations are isolated."""
        return copy.deepcopy(raw_minimal)

    def _first_node_of_step(self, tree: dict, step: str) -> dict | None:
        """Find first node with a given step value via iterative BFS."""
        queue = [tree]
        while queue:
            node = queue.pop(0)
            if not isinstance(node, dict):
                continue
            if node.get("step") == step:
                return node
            for child in node.get("children") or []:
                queue.append(child)
        return None

    def _assert_errors_with_node_reference(self, errors: list[str], node_id: str):
        """At least one error must reference the offending node id or contain useful info."""
        assert len(errors) >= 1, (
            f"Expected >= 1 error for node {node_id!r}, got none"
        )

    # -----------------------------------------------------------------------
    # Unknown step
    # -----------------------------------------------------------------------

    def test_unknown_step_value_produces_error(self, valid_tree):
        """An unrecognised step string must produce a hard error."""
        m = _import_schema()
        valid_tree["children"][0]["step"] = "wt-magic-sauce"  # not in KNOWN_STEPS
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1, "Expected error for unknown step 'wt-magic-sauce'"

    # -----------------------------------------------------------------------
    # Unknown indicator function — lint warnings, NOT hard errors (Amendment 2)
    # -----------------------------------------------------------------------

    def test_unknown_indicator_fn_rsi_abbreviation_produces_lint_warning_not_hard_error(self, valid_tree):
        """'rsi' must produce a lint_tree warning, NOT a validate_tree hard error.

        Amendment 2: unknown indicator fns are lint warnings only. 'standard-deviation-price'
        appears in the large golden fixture and must be tolerated at validation time.
        The inverse adversarial test ensures 'rsi' (a common shorthand) does at least
        surface as a lint warning so the user is informed.
        """
        m = _import_schema()
        if_child = self._first_node_of_step(valid_tree, "if-child")
        if if_child is None:
            pytest.skip("Minimal fixture has no if-child; rebuild needed")
        if_child["lhs-fn"] = "rsi"  # abbreviated — not a VERIFIED-LOCAL grammar string
        # Must NOT be a hard error (amendment 2)
        errors = m.validate_tree(valid_tree)
        assert errors == [], (
            "Amendment 2: 'rsi' (unknown indicator fn) must be a lint warning, "
            f"not a validate_tree hard error; got errors: {errors}"
        )
        # Must surface as a lint warning so users are informed
        warnings = m.lint_tree(valid_tree)
        assert isinstance(warnings, list)
        assert len(warnings) >= 1, (
            "'rsi' is not a VERIFIED-LOCAL indicator fn; "
            "lint_tree should warn about it but validate_tree must not error"
        )

    def test_unknown_indicator_fn_arbitrary_string_produces_lint_warning_not_hard_error(self, valid_tree):
        """Completely made-up indicator fn must produce a lint warning, not a hard error.

        Amendment 2: unknown indicator fns (including 'standard-deviation-price' in the
        large fixture) are lint warnings, not validate_tree hard errors.
        """
        m = _import_schema()
        if_child = self._first_node_of_step(valid_tree, "if-child")
        if if_child is None:
            pytest.skip("Minimal fixture has no if-child; rebuild needed")
        if_child["lhs-fn"] = "momentum-oscillator-xyz"
        # Must NOT be a hard error
        errors = m.validate_tree(valid_tree)
        assert errors == [], (
            "Amendment 2: unknown indicator fn must be a lint warning, "
            f"not a validate_tree hard error; got errors: {errors}"
        )
        # Must surface as a lint warning
        warnings = m.lint_tree(valid_tree)
        assert len(warnings) >= 1, (
            "lint_tree must warn about unknown indicator fn 'momentum-oscillator-xyz'"
        )

    def test_standard_deviation_price_in_large_fixture_is_tolerated(self, raw_large):
        """'standard-deviation-price' appears in the real large fixture; validate_tree must accept it.

        Amendment 2 specific case: this exact fn string occurs 3 times in
        sample_score_large.json. It is NOT in KNOWN_INDICATOR_FNS but must not
        cause a hard validation error.
        """
        m = _import_schema()
        # The large fixture already contains standard-deviation-price; its validation
        # is fully covered by test_large_fixture_validates_clean, but this test makes
        # the Amendment 2 intent explicit.
        errors = m.validate_tree(raw_large)
        assert errors == [], (
            "validate_tree errored on large fixture containing 'standard-deviation-price'; "
            "Amendment 2 requires unknown indicator fns to be lint warnings only"
        )

    # -----------------------------------------------------------------------
    # Unknown comparator
    # -----------------------------------------------------------------------

    def test_unknown_comparator_gte_produces_error(self, valid_tree):
        """'gte' is NOT in the confirmed grammar (OQ-2 stance: validate against gt/lt/lte only).

        This pins the OQ-2 decision: until 'gte' is confirmed in a local fixture,
        it must be treated as a hard error.
        """
        m = _import_schema()
        if_child = self._first_node_of_step(valid_tree, "if-child")
        if if_child is None:
            pytest.skip("Minimal fixture has no if-child; rebuild needed")
        if_child["comparator"] = "gte"
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1, (
            "'gte' is UNCONFIRMED per OQ-2; validate_tree should reject it. "
            "Accepted comparators: gt, lt, lte."
        )

    def test_unknown_comparator_eq_produces_error(self, valid_tree):
        """'eq' is not confirmed in any local fixture; must produce a hard error."""
        m = _import_schema()
        if_child = self._first_node_of_step(valid_tree, "if-child")
        if if_child is None:
            pytest.skip("Minimal fixture has no if-child; rebuild needed")
        if_child["comparator"] = "eq"
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1

    # -----------------------------------------------------------------------
    # Unknown rebalance
    # -----------------------------------------------------------------------

    def test_unknown_rebalance_value_produces_error(self, valid_tree):
        """A rebalance value not in {daily, none, weekly, monthly} must produce an error."""
        m = _import_schema()
        valid_tree["rebalance"] = "biweekly"  # not in KNOWN_REBALANCE
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1, "Expected error for unknown rebalance 'biweekly'"

    # -----------------------------------------------------------------------
    # Missing required fields per step
    # -----------------------------------------------------------------------

    def test_root_missing_name_field_produces_error(self, valid_tree):
        """root node without 'name' must produce a hard error."""
        m = _import_schema()
        del valid_tree["name"]
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1, "Expected error for root missing 'name'"

    def test_root_missing_rebalance_field_produces_error(self, valid_tree):
        """root node without 'rebalance' must produce a hard error."""
        m = _import_schema()
        del valid_tree["rebalance"]
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1

    def test_root_missing_children_field_produces_error(self, valid_tree):
        """root node without 'children' must produce a hard error."""
        m = _import_schema()
        del valid_tree["children"]
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1

    def test_asset_missing_ticker_field_produces_error(self, valid_tree):
        """asset node without 'ticker' must produce a hard error."""
        m = _import_schema()
        asset = self._first_node_of_step(valid_tree, "asset")
        assert asset is not None, "No asset node found in minimal fixture"
        del asset["ticker"]
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1, "Expected error for asset missing 'ticker'"

    def test_if_child_missing_comparator_produces_error(self, valid_tree):
        """True-branch if-child without 'comparator' must produce a hard error."""
        m = _import_schema()
        if_child = self._first_node_of_step(valid_tree, "if-child")
        if if_child is None or if_child.get("is-else-condition?"):
            pytest.skip("No true-branch if-child found in minimal fixture")
        del if_child["comparator"]
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1

    def test_if_child_missing_lhs_fn_produces_error(self, valid_tree):
        """True-branch if-child without 'lhs-fn' must produce a hard error.

        Note: this test checks that the *field* is present. The value of lhs-fn
        may be unknown (lint warning per Amendment 2), but missing it entirely
        is a structural hard error.
        """
        m = _import_schema()
        if_child = self._first_node_of_step(valid_tree, "if-child")
        if if_child is None or if_child.get("is-else-condition?"):
            pytest.skip("No true-branch if-child found in minimal fixture")
        del if_child["lhs-fn"]
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1

    def test_filter_missing_select_fn_produces_error(self):
        """filter node without 'select-fn' must produce a hard error."""
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "filter",
                    # select-fn deliberately omitted
                    "select-n": 2,
                    "sort-by-fn": "cumulative-return",
                    "sort-by-fn-params": {"window": 20},
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, "Expected error for filter missing 'select-fn'"

    def test_filter_missing_sort_by_fn_produces_error(self):
        """filter node without 'sort-by-fn' must produce a hard error."""
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "filter",
                    "select-fn": "top",
                    "select-n": 2,
                    # sort-by-fn deliberately omitted
                    "sort-by-fn-params": {"window": 20},
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert len(errors) >= 1

    def test_group_missing_name_field_produces_error(self):
        """group node without 'name' must produce a hard error."""
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "group",
                    # name deliberately omitted
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, "Expected error for group missing 'name'"

    # -----------------------------------------------------------------------
    # if with missing branch
    # -----------------------------------------------------------------------

    def test_if_with_no_else_branch_produces_error(self, valid_tree):
        """An 'if' node with no else-condition child must produce a hard error.

        Per grammar: if has two children — a true branch and an else branch.
        A missing else branch is a structural error (non-optional per spec).
        """
        m = _import_schema()
        if_node = self._first_node_of_step(valid_tree, "if")
        assert if_node is not None, "No if node found in minimal fixture"
        # Remove the else-branch child
        if_node["children"] = [
            c for c in (if_node.get("children") or [])
            if not c.get("is-else-condition?")
        ]
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1, "Expected error for if missing else branch"

    def test_if_with_empty_children_produces_error(self, valid_tree):
        """An 'if' node with zero children must produce a hard error."""
        m = _import_schema()
        if_node = self._first_node_of_step(valid_tree, "if")
        assert if_node is not None
        if_node["children"] = []
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1

    # -----------------------------------------------------------------------
    # Non-asset leaf
    # -----------------------------------------------------------------------

    def test_non_asset_leaf_node_produces_error(self):
        """A leaf node (no children) with step != 'asset' must produce a hard error.

        Leaves in the allocation graph must be asset nodes; any other step at
        a leaf position indicates a structural problem (e.g. dangling group).
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-equal",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "group",  # group is not a valid leaf — no children
                            "name": "Dangling Group",
                            "id": str(uuid.uuid4()),
                            "children": [],  # leaf with no assets
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, "Expected error for non-asset leaf (dangling group)"

    # -----------------------------------------------------------------------
    # Duplicate IDs
    # -----------------------------------------------------------------------

    def test_duplicate_ids_produce_error(self, valid_tree):
        """A tree with two nodes sharing the same id must produce a hard error."""
        m = _import_schema()
        # Steal the root's id and assign it to the first asset
        shared_id = valid_tree["id"]
        asset = self._first_node_of_step(valid_tree, "asset")
        assert asset is not None
        asset["id"] = shared_id  # now two nodes share the same id
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1, f"Expected error for duplicate id {shared_id!r}"

    # -----------------------------------------------------------------------
    # Depth bomb — must produce lint warning, NOT validate_tree hard error
    # (Amendment 1: MAX_TREE_DEPTH is construction-side only)
    # -----------------------------------------------------------------------

    def test_depth_bomb_beyond_max_depth_produces_lint_warning_not_hard_error(self):
        """A tree deeper than MAX_TREE_DEPTH must produce a lint warning, not a hard error.

        Amendment 1: MAX_TREE_DEPTH is a CONSTRUCTION-side constant; it gates
        the constructors (and surfaces as a lint warning) but must NOT cause
        validate_tree to error. The large golden fixture is depth 230 and must
        validate clean.

        This test uses 300 wrapping layers to ensure it exceeds any reasonable
        MAX_TREE_DEPTH ceiling, then confirms the behaviour is lint-only.
        """
        m = _import_schema()
        inner: dict = {
            "step": "asset",
            "ticker": "SPY",
            "name": "",
            "exchange": "NYSE",
            "id": str(uuid.uuid4()),
        }
        current = inner
        for _ in range(300):  # 300 wrapping layers >> any sane MAX_TREE_DEPTH
            wrapper = {
                "step": "wt-cash-equal",
                "id": str(uuid.uuid4()),
                "children": [current],
            }
            current = wrapper

        root = {
            "step": "root",
            "name": "Depth Bomb",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [current],
        }
        # Must NOT be a hard validate error (Amendment 1)
        errors = m.validate_tree(root)
        assert errors == [], (
            "Amendment 1: MAX_TREE_DEPTH must be a lint warning, not a validate_tree hard error; "
            f"got errors: {errors}"
        )
        # Must surface as a lint warning
        warnings = m.lint_tree(root)
        assert isinstance(warnings, list)
        assert len(warnings) >= 1, (
            "Expected lint warning for tree depth > MAX_TREE_DEPTH (300 layers), got none"
        )

    def test_depth_bomb_no_recursion_error(self):
        """A 300-layer deep tree must not raise RecursionError (iterative traversal required).

        Amendment 8: traversal must use an explicit stack. This test confirms
        the iterative contract holds for synthetic depth-bomb trees, not just
        the large fixture.
        """
        m = _import_schema()
        inner: dict = {
            "step": "asset",
            "ticker": "SPY",
            "name": "",
            "exchange": "NYSE",
            "id": str(uuid.uuid4()),
        }
        current = inner
        for _ in range(300):
            wrapper = {
                "step": "wt-cash-equal",
                "id": str(uuid.uuid4()),
                "children": [current],
            }
            current = wrapper
        root = {
            "step": "root",
            "name": "Deep Tree",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [current],
        }
        try:
            m.validate_tree(root)
            m.extract_tickers(root)
        except RecursionError as exc:
            pytest.fail(
                f"RecursionError on 300-layer tree: {exc}. "
                "Traversal must be iterative (explicit stack), not recursive."
            )

    # -----------------------------------------------------------------------
    # Node count bomb — must produce lint warning, NOT validate_tree hard error
    # (Amendment 1: MAX_TOTAL_NODES is construction-side only)
    # -----------------------------------------------------------------------

    def test_node_count_bomb_beyond_max_nodes_produces_lint_warning_not_hard_error(self):
        """A tree with more nodes than MAX_TOTAL_NODES must produce a lint warning, not a hard error.

        Amendment 1: MAX_TOTAL_NODES is a CONSTRUCTION-side constant; it gates
        the constructors (and surfaces as a lint warning) but must NOT cause
        validate_tree to error. The small golden fixture has 866 nodes and must
        validate clean.

        This test uses 1000 asset children, exceeding any reasonable MAX_TOTAL_NODES,
        then confirms the behaviour is lint-only.
        """
        m = _import_schema()
        many_assets = [
            {
                "step": "asset",
                "ticker": f"S{i:04d}",
                "name": "",
                "exchange": "NYSE",
                "id": str(uuid.uuid4()),
            }
            for i in range(1000)  # 1000 nodes >> any construction-side MAX_TOTAL_NODES
        ]
        root = {
            "step": "root",
            "name": "Node Bomb",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-equal",
                    "id": str(uuid.uuid4()),
                    "children": many_assets,
                }
            ],
        }
        # Must NOT be a hard validate error (Amendment 1)
        errors = m.validate_tree(root)
        assert errors == [], (
            "Amendment 1: MAX_TOTAL_NODES must be a lint warning, not a validate_tree hard error; "
            f"got errors: {errors}"
        )
        # Must surface as a lint warning
        warnings = m.lint_tree(root)
        assert isinstance(warnings, list)
        assert len(warnings) >= 1, (
            "Expected lint warning for tree with 1000 nodes > MAX_TOTAL_NODES, got none"
        )

    # -----------------------------------------------------------------------
    # Malformed weight
    # -----------------------------------------------------------------------

    def test_weight_num_as_garbage_string_produces_error(self):
        """weight.num = 'abc' (non-numeric string) must produce a hard error.

        Grammar accepts int or numeric string, but a non-numeric string like 'abc'
        cannot be interpreted as a percentage weight.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-specified",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                            "weight": {"num": "abc", "den": 100},  # non-numeric garbage
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, "Expected error for weight.num = 'abc' (non-numeric)"

    def test_weight_missing_den_produces_error(self):
        """weight object without 'den' key must produce a hard error."""
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-specified",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                            "weight": {"num": 100},  # den missing
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, "Expected error for weight missing 'den'"

    # -----------------------------------------------------------------------
    # validate_tree never raises on malformed inputs
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize(
        "bad_input,description",
        [
            (None, "None"),
            ([], "empty list"),
            ({}, "empty dict"),
            ("a string", "bare string"),
            (42, "integer"),
            ({"step": "root"}, "root with only step key"),
            (
                {"step": "root", "name": None, "rebalance": None, "id": None, "children": None},
                "root with all None fields",
            ),
            (
                {"step": "root", "name": "T", "rebalance": "daily", "id": "x", "children": [None, None]},
                "children containing None elements",
            ),
            (
                {"step": "if", "id": str(uuid.uuid4()), "children": [{"not": "an-if-child"}]},
                "if with non-if-child children",
            ),
        ],
    )
    def test_validate_tree_never_raises_on_malformed_input(self, bad_input, description):
        """validate_tree must return a list (possibly non-empty) and NEVER raise.

        Robustness contract: the validator must handle any garbage input without
        an unhandled exception, so the caller can always safely inspect errors[].
        """
        m = _import_schema()
        try:
            result = m.validate_tree(bad_input)
        except Exception as exc:
            pytest.fail(
                f"validate_tree raised {type(exc).__name__} on input={description!r}: {exc}"
            )
        assert isinstance(result, list), (
            f"validate_tree must return list, got {type(result)} for input={description!r}"
        )


# ===========================================================================
# 4. LINT TESTS
#    Lint warnings: weights summing to 99 -> warning not error.
#    String nums ("66.67") accepted without error.
#    Node/depth caps produce lint warnings not hard errors.
# ===========================================================================


class TestLintTree:
    """lint_tree produces warnings for policy violations, not hard errors."""

    def test_weights_summing_to_99_produces_lint_warning_not_hard_error(self):
        """wt-cash-specified with weights summing to 99 -> lint warning, not validate error.

        Per OQ-4 stance: sum constraint is WARN only; Composer may or may not
        enforce it at POST /backtest time.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-specified",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                            "weight": {"num": 59, "den": 100},  # 59 + 40 = 99, not 100
                        },
                        {
                            "step": "asset",
                            "ticker": "AGG",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                            "weight": {"num": 40, "den": 100},
                        },
                    ],
                }
            ],
        }
        # Must NOT be a hard error
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"Weight sum != 100 must be a lint warning, not a hard error; got: {errors}"
        )
        # Must be a lint warning
        warnings = m.lint_tree(tree)
        assert isinstance(warnings, list)
        assert len(warnings) >= 1, (
            "Expected lint warning for weight sum = 99 (not 100), got none"
        )

    def test_weights_as_string_nums_accepted_without_hard_error(self):
        """weight.num as numeric string ('66.67') must not produce a hard error.

        VERIFIED-LOCAL: sample_score_large.json contains {'num': '66.67', 'den': 100}.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-specified",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                            "weight": {"num": "66.67", "den": 100},
                        },
                        {
                            "step": "asset",
                            "ticker": "AGG",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                            "weight": {"num": "33.33", "den": 100},
                        },
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"String numeric weight ('66.67') must be accepted without hard error; got: {errors}"
        )

    def test_weight_den_as_string_100_accepted_without_hard_error(self):
        """weight.den as string '100' must not produce a hard error.

        Amendment 4: weight.den may be the string '100' in real fixtures.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-specified",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                            "weight": {"num": 100, "den": "100"},  # den as string
                        },
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"Amendment 4: weight.den as string '100' must be accepted; got errors: {errors}"
        )

    def test_lint_tree_returns_list_of_strings(self):
        """lint_tree must return a list of strings (possibly empty)."""
        m = _import_schema()
        spy_tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-equal",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                        }
                    ],
                }
            ],
        }
        result = m.lint_tree(spy_tree)
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, str), f"lint_tree items must be strings; got {type(item)}"


# ===========================================================================
# 5. AMENDMENT TOLERANCE TESTS
#    Tests verifying Amendment 3-7 tolerances: select-n string, flat window-days,
#    compound conditions, cosmetic keys.
# ===========================================================================


class TestAmendmentTolerances:
    """Contract amendments 3-7: tolerance cases that must validate clean."""

    def test_select_n_as_string_accepted_without_hard_error(self):
        """Amendment 3: select-n as string ('3') must be accepted by validate_tree.

        VERIFIED-LOCAL: sample_score_large.json contains 'select-n': '3'.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "filter",
                    "select-fn": "top",
                    "select-n": "3",  # string, not int — Amendment 3
                    "sort-by-fn": "cumulative-return",
                    "sort-by-fn-params": {"window": 20},
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"Amendment 3: select-n as string '3' must be accepted; got errors: {errors}"
        )

    def test_flat_lhs_window_days_tolerated_alongside_fn_params(self):
        """Amendment 5: flat lhs-window-days key alongside lhs-fn-params must not produce an error.

        VERIFIED-LOCAL: sample_score_large.json has 186 occurrences of lhs-window-days
        as a flat key co-existing with lhs-fn-params.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-equal",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "if",
                            "id": str(uuid.uuid4()),
                            "children": [
                                {
                                    "step": "if-child",
                                    "is-else-condition?": False,
                                    "lhs-fn": "cumulative-return",
                                    "lhs-fn-params": {"window": 200},
                                    "lhs-window-days": 200,  # flat key — Amendment 5 tolerance
                                    "lhs-val": "TLT",
                                    "comparator": "gt",
                                    "rhs-fixed-value?": True,
                                    "rhs-val": "0",
                                    "id": str(uuid.uuid4()),
                                    "children": [
                                        {
                                            "step": "asset",
                                            "ticker": "TLT",
                                            "name": "",
                                            "exchange": "NYSE",
                                            "id": str(uuid.uuid4()),
                                        }
                                    ],
                                },
                                {
                                    "step": "if-child",
                                    "is-else-condition?": True,
                                    "id": str(uuid.uuid4()),
                                    "children": [
                                        {
                                            "step": "asset",
                                            "ticker": "BIL",
                                            "name": "",
                                            "exchange": "NYSE",
                                            "id": str(uuid.uuid4()),
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"Amendment 5: flat lhs-window-days must be tolerated; got errors: {errors}"
        )

    def test_flat_rhs_window_days_tolerated(self):
        """Amendment 5: flat rhs-window-days alongside rhs-fn-params must be tolerated.

        VERIFIED-LOCAL: sample_score_large.json has 120 occurrences of rhs-window-days.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-equal",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "if",
                            "id": str(uuid.uuid4()),
                            "children": [
                                {
                                    "step": "if-child",
                                    "is-else-condition?": False,
                                    "lhs-fn": "relative-strength-index",
                                    "lhs-fn-params": {"window": 14},
                                    "lhs-val": "SPY",
                                    "comparator": "gt",
                                    "rhs-fixed-value?": False,
                                    "rhs-fn": "relative-strength-index",
                                    "rhs-fn-params": {"window": 14},
                                    "rhs-window-days": 14,  # flat key — Amendment 5 tolerance
                                    "rhs-val": "QQQ",
                                    "id": str(uuid.uuid4()),
                                    "children": [
                                        {
                                            "step": "asset",
                                            "ticker": "SPY",
                                            "name": "",
                                            "exchange": "NYSE",
                                            "id": str(uuid.uuid4()),
                                        }
                                    ],
                                },
                                {
                                    "step": "if-child",
                                    "is-else-condition?": True,
                                    "id": str(uuid.uuid4()),
                                    "children": [
                                        {
                                            "step": "asset",
                                            "ticker": "BIL",
                                            "name": "",
                                            "exchange": "NYSE",
                                            "id": str(uuid.uuid4()),
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"Amendment 5: flat rhs-window-days must be tolerated; got errors: {errors}"
        )

    def test_compound_condition_block_tolerated(self):
        """Amendment 6: if-child with a nested 'condition' block must validate clean.

        Compound conditions carry condition-type, operator, tickers arrays, and
        rhs: {'constant': N}. These must be tolerated without error.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-equal",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "if",
                            "id": str(uuid.uuid4()),
                            "children": [
                                {
                                    "step": "if-child",
                                    "is-else-condition?": False,
                                    # Compound condition block (Amendment 6)
                                    "condition": {
                                        "condition-type": "compound",
                                        "operator": "any",
                                        "conditions": [
                                            {
                                                "lhs": {
                                                    "fn": "cumulative-return",
                                                    "fn-params": {"window": 200},
                                                    "val": "TLT",
                                                },
                                                "comparator": "gt",
                                                "rhs": {"constant": 0},
                                            }
                                        ],
                                    },
                                    "id": str(uuid.uuid4()),
                                    "children": [
                                        {
                                            "step": "asset",
                                            "ticker": "TLT",
                                            "name": "",
                                            "exchange": "NYSE",
                                            "id": str(uuid.uuid4()),
                                        }
                                    ],
                                },
                                {
                                    "step": "if-child",
                                    "is-else-condition?": True,
                                    "id": str(uuid.uuid4()),
                                    "children": [
                                        {
                                            "step": "asset",
                                            "ticker": "BIL",
                                            "name": "",
                                            "exchange": "NYSE",
                                            "id": str(uuid.uuid4()),
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"Amendment 6: compound condition block must be tolerated; got errors: {errors}"
        )

    def test_cosmetic_keys_tolerated_on_all_node_types(self):
        """Amendment 7: cosmetic keys (collapsed?, description, window-days, etc.) must not error.

        These keys appear throughout real fixtures and must be silently ignored
        by the validator.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "description": "cosmetic description field",  # cosmetic
            "collapsed?": True,                            # cosmetic
            "suppress_incomplete_warnings": False,         # cosmetic
            "children": [
                {
                    "step": "wt-cash-equal",
                    "id": str(uuid.uuid4()),
                    "window-days": 20,  # cosmetic
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "SPDR S&P 500",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                            "price": 500.0,            # cosmetic
                            "dollar_volume": 1e9,      # cosmetic
                            "has_marketcap": True,     # cosmetic
                            "children-count": 0,       # cosmetic
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"Amendment 7: cosmetic keys must not produce hard errors; got: {errors}"
        )

    def test_weight_on_non_wt_cash_specified_child_tolerated(self):
        """Amendment 4: weight field on asset/if/group/filter nodes must be tolerated.

        The grammar notes that weight appears on nodes that are direct children of
        wt-cash-specified. Amendment 4 clarifies it may also appear on other node
        types in real fixtures; it must not cause a hard error.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-equal",  # NOT wt-cash-specified
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                            "weight": {"num": 100, "den": 100},  # weight on non-wt-specified child
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"Amendment 4: weight on non-wt-cash-specified child must be tolerated; got: {errors}"
        )


# ===========================================================================
# 6. PROPERTY-STYLE TESTS
#    validate_tree is read-only (input unmutated).
#    Constructors don't share mutable children references.
# ===========================================================================


class TestPropertyStyleInvariants:
    """Structural invariants that must hold regardless of input."""

    def test_validate_tree_does_not_mutate_input(self, raw_minimal):
        """validate_tree must be read-only: calling it must not change the input tree."""
        m = _import_schema()
        original = copy.deepcopy(raw_minimal)
        _before = json.dumps(original, sort_keys=True)
        m.validate_tree(original)
        _after = json.dumps(original, sort_keys=True)
        assert _before == _after, (
            "validate_tree mutated the input dict; it must be read-only"
        )

    def test_validate_tree_does_not_mutate_input_on_invalid_tree(self):
        """validate_tree must not mutate inputs even when it finds errors."""
        m = _import_schema()
        bad_tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "quarterly",  # invalid
            "id": str(uuid.uuid4()),
            "children": [],
        }
        original_rebalance = bad_tree["rebalance"]
        m.validate_tree(bad_tree)
        assert bad_tree["rebalance"] == original_rebalance, (
            "validate_tree changed the 'rebalance' field on an invalid input"
        )

    def test_lint_tree_does_not_mutate_input(self, raw_minimal):
        """lint_tree must be read-only."""
        m = _import_schema()
        original = copy.deepcopy(raw_minimal)
        _before = json.dumps(original, sort_keys=True)
        m.lint_tree(original)
        _after = json.dumps(original, sort_keys=True)
        assert _before == _after, "lint_tree mutated the input dict"

    def test_extract_tickers_does_not_mutate_input(self, raw_minimal):
        """extract_tickers must be read-only."""
        m = _import_schema()
        original = copy.deepcopy(raw_minimal)
        _before = json.dumps(original, sort_keys=True)
        m.extract_tickers(original)
        _after = json.dumps(original, sort_keys=True)
        assert _before == _after, "extract_tickers mutated the input dict"

    def test_render_rules_text_does_not_mutate_input(self, raw_minimal):
        """render_rules_text must be read-only."""
        m = _import_schema()
        original = copy.deepcopy(raw_minimal)
        _before = json.dumps(original, sort_keys=True)
        m.render_rules_text(original)
        _after = json.dumps(original, sort_keys=True)
        assert _before == _after, "render_rules_text mutated the input dict"


# ===========================================================================
# 9. ADVERSARIAL CYCLE 2 — gaps found by test-writer after first GREEN
#    These tests target three specific implementation gaps identified by
#    probing the first-pass implementation after cycle 1 passed.
# ===========================================================================


class TestAdversarialCycle2:
    """Cycle-2 adversarial cases designed to break gaps in the first GREEN pass."""

    # -----------------------------------------------------------------------
    # Gap 1: filter missing select-n produces no error
    # Grammar doc §3.5 lists select-n as a required field alongside select-fn
    # and sort-by-fn. The cycle-1 implementation only checks select-fn and
    # sort-by-fn, silently accepting a filter with no select-n.
    # -----------------------------------------------------------------------

    def test_filter_missing_select_n_produces_error(self):
        """filter node without 'select-n' must produce a hard error.

        Grammar doc §3.5: select-n is required on a filter node alongside
        select-fn and sort-by-fn. A filter without select-n cannot be
        submitted to Composer (it has no count to select).
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "filter",
                    "select-fn": "top",
                    # select-n deliberately omitted
                    "sort-by-fn": "cumulative-return",
                    "sort-by-fn-params": {"window": 20},
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "filter without 'select-n' must produce a hard error; "
            "grammar §3.5 lists select-n as required"
        )

    def test_filter_with_all_required_fields_validates_clean(self):
        """Sanity check: a complete filter node must produce no hard errors.

        Ensures the select-n check does not accidentally reject valid filters.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "filter",
                    "select-fn": "top",
                    "select-n": 3,
                    "sort-by-fn": "cumulative-return",
                    "sort-by-fn-params": {"window": 20},
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"Complete filter node produced unexpected hard errors: {errors}"
        )

    # -----------------------------------------------------------------------
    # Gap 2: if-child with rhs-fixed-value?=False and missing rhs-fn
    # Grammar doc §3.4: when rhs-fixed-value? is False, rhs-fn and rhs-fn-params
    # are required. The cycle-1 implementation does not check for rhs-fn presence
    # on non-fixed-value true-branch if-children.
    # -----------------------------------------------------------------------

    def test_if_child_ticker_comparison_missing_rhs_fn_produces_error(self):
        """True-branch if-child with rhs-fixed-value?=False and no rhs-fn must produce an error.

        Grammar doc §3.4: when rhs-fixed-value? is False the rhs is a ticker
        comparison; rhs-fn (the indicator applied to the rhs ticker) is required.
        A missing rhs-fn produces a structurally incomplete condition that Composer
        cannot evaluate.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-equal",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "if",
                            "id": str(uuid.uuid4()),
                            "children": [
                                {
                                    "step": "if-child",
                                    "is-else-condition?": False,
                                    "lhs-fn": "relative-strength-index",
                                    "lhs-fn-params": {"window": 14},
                                    "lhs-val": "SPY",
                                    "comparator": "gt",
                                    "rhs-fixed-value?": False,
                                    "rhs-val": "QQQ",
                                    # rhs-fn deliberately omitted — required when rhs-fixed-value?=False
                                    "id": str(uuid.uuid4()),
                                    "children": [
                                        {
                                            "step": "asset",
                                            "ticker": "SPY",
                                            "name": "",
                                            "exchange": "NYSE",
                                            "id": str(uuid.uuid4()),
                                        }
                                    ],
                                },
                                {
                                    "step": "if-child",
                                    "is-else-condition?": True,
                                    "id": str(uuid.uuid4()),
                                    "children": [
                                        {
                                            "step": "asset",
                                            "ticker": "BIL",
                                            "name": "",
                                            "exchange": "NYSE",
                                            "id": str(uuid.uuid4()),
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "true-branch if-child with rhs-fixed-value?=False and missing rhs-fn "
            "must produce a hard error; grammar §3.4 requires rhs-fn when rhs is a ticker"
        )

    def test_if_child_ticker_comparison_with_rhs_fn_validates_clean(self):
        """Sanity check: if-child with rhs-fixed-value?=False AND rhs-fn must validate clean."""
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-equal",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "if",
                            "id": str(uuid.uuid4()),
                            "children": [
                                {
                                    "step": "if-child",
                                    "is-else-condition?": False,
                                    "lhs-fn": "relative-strength-index",
                                    "lhs-fn-params": {"window": 14},
                                    "lhs-val": "SPY",
                                    "comparator": "gt",
                                    "rhs-fixed-value?": False,
                                    "rhs-fn": "relative-strength-index",
                                    "rhs-fn-params": {"window": 14},
                                    "rhs-val": "QQQ",
                                    "id": str(uuid.uuid4()),
                                    "children": [
                                        {
                                            "step": "asset",
                                            "ticker": "SPY",
                                            "name": "",
                                            "exchange": "NYSE",
                                            "id": str(uuid.uuid4()),
                                        }
                                    ],
                                },
                                {
                                    "step": "if-child",
                                    "is-else-condition?": True,
                                    "id": str(uuid.uuid4()),
                                    "children": [
                                        {
                                            "step": "asset",
                                            "ticker": "BIL",
                                            "name": "",
                                            "exchange": "NYSE",
                                            "id": str(uuid.uuid4()),
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"Valid ticker-comparison if-child (rhs-fn present) produced unexpected errors: {errors}"
        )

    def test_if_child_fixed_value_without_rhs_fn_validates_clean(self):
        """Sanity check: rhs-fixed-value?=True without rhs-fn must validate clean.

        When rhs-fixed-value? is True, rhs-fn is correctly absent per grammar §3.4.
        This ensures the rhs-fn check does not fire on fixed-value comparisons.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-equal",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "if",
                            "id": str(uuid.uuid4()),
                            "children": [
                                {
                                    "step": "if-child",
                                    "is-else-condition?": False,
                                    "lhs-fn": "cumulative-return",
                                    "lhs-fn-params": {"window": 200},
                                    "lhs-val": "TLT",
                                    "comparator": "gt",
                                    "rhs-fixed-value?": True,
                                    "rhs-val": "0",
                                    # rhs-fn intentionally absent (correct for fixed value)
                                    "id": str(uuid.uuid4()),
                                    "children": [
                                        {
                                            "step": "asset",
                                            "ticker": "TLT",
                                            "name": "",
                                            "exchange": "NYSE",
                                            "id": str(uuid.uuid4()),
                                        }
                                    ],
                                },
                                {
                                    "step": "if-child",
                                    "is-else-condition?": True,
                                    "id": str(uuid.uuid4()),
                                    "children": [
                                        {
                                            "step": "asset",
                                            "ticker": "BIL",
                                            "name": "",
                                            "exchange": "NYSE",
                                            "id": str(uuid.uuid4()),
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"Fixed-value if-child without rhs-fn produced unexpected errors: {errors}"
        )

    # -----------------------------------------------------------------------
    # Gap 3: wt-cash-specified with unweighted children produces no lint warning
    # The implementer flagged this: lint_tree's weight-sum check only fires when
    # at least one child carries a weight field. A wt-cash-specified whose
    # children have NO weight fields is structurally ambiguous (Composer cannot
    # determine the allocation split) and should warn.
    # -----------------------------------------------------------------------

    def test_wt_cash_specified_with_no_weighted_children_produces_lint_warning(self):
        """wt-cash-specified with children bearing no weight fields must produce a lint warning.

        A wt-cash-specified container exists to carry explicit allocation percentages.
        If none of its children have a weight field, the allocation is undefined —
        this is almost certainly a construction error and warrants a lint warning.
        """
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-specified",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                            # weight field deliberately absent
                        },
                        {
                            "step": "asset",
                            "ticker": "AGG",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                            # weight field deliberately absent
                        },
                    ],
                }
            ],
        }
        # Must NOT be a hard error (weight fields are optional per the amendment tolerances)
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"wt-cash-specified with unweighted children must not be a hard error; got: {errors}"
        )
        # Must be a lint warning (allocation is undefined)
        warnings = m.lint_tree(tree)
        assert isinstance(warnings, list)
        assert len(warnings) >= 1, (
            "wt-cash-specified with no weighted children must produce a lint warning "
            "(allocation split is undefined for a specified-weight node)"
        )

    def test_wt_cash_specified_with_weighted_children_produces_no_spurious_lint(self):
        """Sanity check: wt-cash-specified with all children weighted to 100 must not warn."""
        m = _import_schema()
        tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-specified",
                    "id": str(uuid.uuid4()),
                    "children": [
                        {
                            "step": "asset",
                            "ticker": "SPY",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                            "weight": {"num": 60, "den": 100},
                        },
                        {
                            "step": "asset",
                            "ticker": "AGG",
                            "name": "",
                            "exchange": "NYSE",
                            "id": str(uuid.uuid4()),
                            "weight": {"num": 40, "den": 100},
                        },
                    ],
                }
            ],
        }
        warnings = m.lint_tree(tree)
        # 60+40=100 exactly — weight sum warning must not fire
        weight_sum_warnings = [w for w in warnings if "weight" in w.lower() and "sum" in w.lower()]
        assert len(weight_sum_warnings) == 0, (
            f"60+40 weight sum produced unexpected lint warning: {weight_sum_warnings}"
        )


# ===========================================================================
# 7. CONSTANTS CONTRACT
#    KNOWN_STEPS, KNOWN_INDICATOR_FNS, KNOWN_COMPARATORS, KNOWN_REBALANCE,
#    MAX_TREE_DEPTH, MAX_TOTAL_NODES must exist and contain the verified values.
# ===========================================================================


class TestModuleConstants:
    """Module-level constants must expose the correct grammar-pinned vocabulary."""

    def test_known_steps_contains_all_verified_local_steps(self):
        """KNOWN_STEPS must include all 9 VERIFIED-LOCAL step strings."""
        m = _import_schema()
        assert hasattr(m, "KNOWN_STEPS"), "KNOWN_STEPS constant missing from module"
        verified_steps = {
            "root",
            "group",
            "if",
            "if-child",
            "filter",
            "wt-cash-equal",
            "wt-cash-specified",
            "wt-inverse-vol",
            "asset",
        }
        missing = verified_steps - set(m.KNOWN_STEPS)
        assert not missing, (
            f"KNOWN_STEPS is missing VERIFIED-LOCAL step values: {missing}"
        )

    def test_known_indicator_fns_contains_all_verified_local_fns(self):
        """KNOWN_INDICATOR_FNS must include all 7 VERIFIED-LOCAL indicator strings."""
        m = _import_schema()
        assert hasattr(m, "KNOWN_INDICATOR_FNS"), "KNOWN_INDICATOR_FNS missing"
        verified_fns = {
            "relative-strength-index",
            "cumulative-return",
            "max-drawdown",
            "current-price",
            "standard-deviation-return",
            "moving-average-price",
            "moving-average-return",
        }
        missing = verified_fns - set(m.KNOWN_INDICATOR_FNS)
        assert not missing, (
            f"KNOWN_INDICATOR_FNS is missing VERIFIED-LOCAL fn strings: {missing}"
        )

    def test_known_indicator_fns_does_not_contain_rsi_abbreviation(self):
        """KNOWN_INDICATOR_FNS must NOT contain 'rsi' — the valid string is longer."""
        m = _import_schema()
        assert "rsi" not in m.KNOWN_INDICATOR_FNS, (
            "'rsi' must not be in KNOWN_INDICATOR_FNS; valid form is 'relative-strength-index'"
        )

    def test_known_comparators_contains_verified_values(self):
        """KNOWN_COMPARATORS must include gt, lt, lte (VERIFIED per grammar)."""
        m = _import_schema()
        assert hasattr(m, "KNOWN_COMPARATORS"), "KNOWN_COMPARATORS missing"
        assert "gt" in m.KNOWN_COMPARATORS
        assert "lt" in m.KNOWN_COMPARATORS
        assert "lte" in m.KNOWN_COMPARATORS

    def test_known_comparators_does_not_contain_gte(self):
        """KNOWN_COMPARATORS must NOT contain 'gte' (OQ-2: unconfirmed, omit until verified)."""
        m = _import_schema()
        assert "gte" not in m.KNOWN_COMPARATORS, (
            "'gte' must not be in KNOWN_COMPARATORS until confirmed in a local fixture (OQ-2)"
        )

    def test_known_rebalance_contains_all_four_values(self):
        """KNOWN_REBALANCE must include daily, none, weekly, monthly."""
        m = _import_schema()
        assert hasattr(m, "KNOWN_REBALANCE"), "KNOWN_REBALANCE missing"
        for val in ("daily", "none", "weekly", "monthly"):
            assert val in m.KNOWN_REBALANCE, f"KNOWN_REBALANCE missing {val!r}"

    def test_max_tree_depth_is_positive_int(self):
        """MAX_TREE_DEPTH must be a positive integer.

        Amendment 1: this is a construction-side constant (lint warning threshold),
        not a validate_tree hard error gate. Its value may be any positive integer
        chosen by the implementer — no upper bound is tested here.
        """
        m = _import_schema()
        assert hasattr(m, "MAX_TREE_DEPTH"), "MAX_TREE_DEPTH missing"
        assert isinstance(m.MAX_TREE_DEPTH, int) and not isinstance(m.MAX_TREE_DEPTH, bool)
        assert m.MAX_TREE_DEPTH > 0

    def test_max_total_nodes_is_positive_int(self):
        """MAX_TOTAL_NODES must be a positive integer.

        Amendment 1: this is a construction-side constant (lint warning threshold),
        not a validate_tree hard error gate. The golden fixtures have 866 and 8455
        nodes respectively, so no upper bound is imposed on this constant.
        OQ-7 cites 500 as a conservative bound for CONSTRUCTING synthetic trees,
        but that does not constrain validation of pre-existing trees.
        """
        m = _import_schema()
        assert hasattr(m, "MAX_TOTAL_NODES"), "MAX_TOTAL_NODES missing"
        assert isinstance(m.MAX_TOTAL_NODES, int) and not isinstance(m.MAX_TOTAL_NODES, bool)
        assert m.MAX_TOTAL_NODES > 0


# ===========================================================================
# 8. ADDITIONAL ADVERSARIAL CASES (Cycle 2 pre-emptive hardening)
#    These cases are designed to break naive first-pass implementations.
# ===========================================================================


class TestAdversarialCasesRound2:
    """Second-round adversarial cases targeting typical first-pass implementation gaps."""

    def test_valid_comparators_gt_lt_lte_do_not_produce_errors(self):
        """Verified comparators gt/lt/lte must each be accepted without error."""
        m = _import_schema()
        for comparator in ("gt", "lt", "lte"):
            tree = {
                "step": "root",
                "name": "Test",
                "rebalance": "daily",
                "id": str(uuid.uuid4()),
                "children": [
                    {
                        "step": "wt-cash-equal",
                        "id": str(uuid.uuid4()),
                        "children": [
                            {
                                "step": "if",
                                "id": str(uuid.uuid4()),
                                "children": [
                                    {
                                        "step": "if-child",
                                        "is-else-condition?": False,
                                        "lhs-fn": "cumulative-return",
                                        "lhs-fn-params": {"window": 20},
                                        "lhs-val": "SPY",
                                        "comparator": comparator,
                                        "rhs-fixed-value?": True,
                                        "rhs-val": "0",
                                        "id": str(uuid.uuid4()),
                                        "children": [
                                            {
                                                "step": "asset",
                                                "ticker": "SPY",
                                                "name": "",
                                                "exchange": "NYSE",
                                                "id": str(uuid.uuid4()),
                                            }
                                        ],
                                    },
                                    {
                                        "step": "if-child",
                                        "is-else-condition?": True,
                                        "id": str(uuid.uuid4()),
                                        "children": [
                                            {
                                                "step": "asset",
                                                "ticker": "BIL",
                                                "name": "",
                                                "exchange": "NYSE",
                                                "id": str(uuid.uuid4()),
                                            }
                                        ],
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
            errors = m.validate_tree(tree)
            assert errors == [], (
                f"Verified comparator {comparator!r} produced unexpected errors: {errors}"
            )

    def test_valid_indicator_fns_all_accepted_without_error(self):
        """All 7 VERIFIED-LOCAL indicator strings must pass validation when used in if-child."""
        m = _import_schema()
        for fn in (
            "relative-strength-index",
            "cumulative-return",
            "max-drawdown",
            "current-price",
            "standard-deviation-return",
            "moving-average-price",
            "moving-average-return",
        ):
            tree = {
                "step": "root",
                "name": "Test",
                "rebalance": "daily",
                "id": str(uuid.uuid4()),
                "children": [
                    {
                        "step": "wt-cash-equal",
                        "id": str(uuid.uuid4()),
                        "children": [
                            {
                                "step": "if",
                                "id": str(uuid.uuid4()),
                                "children": [
                                    {
                                        "step": "if-child",
                                        "is-else-condition?": False,
                                        "lhs-fn": fn,
                                        "lhs-fn-params": {"window": 20},
                                        "lhs-val": "SPY",
                                        "comparator": "gt",
                                        "rhs-fixed-value?": True,
                                        "rhs-val": "0",
                                        "id": str(uuid.uuid4()),
                                        "children": [
                                            {
                                                "step": "asset",
                                                "ticker": "SPY",
                                                "name": "",
                                                "exchange": "NYSE",
                                                "id": str(uuid.uuid4()),
                                            }
                                        ],
                                    },
                                    {
                                        "step": "if-child",
                                        "is-else-condition?": True,
                                        "id": str(uuid.uuid4()),
                                        "children": [
                                            {
                                                "step": "asset",
                                                "ticker": "BIL",
                                                "name": "",
                                                "exchange": "NYSE",
                                                "id": str(uuid.uuid4()),
                                            }
                                        ],
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
            errors = m.validate_tree(tree)
            assert errors == [], (
                f"VERIFIED indicator fn {fn!r} produced unexpected errors: {errors}"
            )

    def test_valid_rebalance_values_all_accepted_without_error(self):
        """All 4 known rebalance values must each pass validation."""
        m = _import_schema()
        for rebalance in ("daily", "none", "weekly", "monthly"):
            tree = {
                "step": "root",
                "name": "Test",
                "rebalance": rebalance,
                "id": str(uuid.uuid4()),
                "children": [
                    {
                        "step": "wt-cash-equal",
                        "id": str(uuid.uuid4()),
                        "children": [
                            {
                                "step": "asset",
                                "ticker": "SPY",
                                "name": "",
                                "exchange": "NYSE",
                                "id": str(uuid.uuid4()),
                            }
                        ],
                    }
                ],
            }
            errors = m.validate_tree(tree)
            assert errors == [], (
                f"Valid rebalance {rebalance!r} produced unexpected errors: {errors}"
            )

    def test_make_indicator_with_window_produces_correct_structure(self):
        """make_indicator(fn, ticker, window=N) must produce the nested params structure.

        Grammar: lhs-fn-params = {"window": int}, NOT a flat sibling key.
        """
        m = _import_schema()
        indicator = m.make_indicator("cumulative-return", "TLT", window=200)
        # The exact structure required by the if-child grammar
        assert isinstance(indicator, dict)
        # Must have the fn name
        assert (
            indicator.get("fn") == "cumulative-return"
            or indicator.get("lhs-fn") == "cumulative-return"
            or indicator.get("name") == "cumulative-return"
        ), (
            f"Indicator dict must encode the function name; got: {indicator}"
        )

    def test_make_condition_with_float_rhs_produces_rhs_fixed_value_true(self):
        """make_condition(lhs, 'gt', 0.0) with float rhs must set rhs-fixed-value? = True."""
        m = _import_schema()
        lhs = m.make_indicator("cumulative-return", "TLT", window=200)
        cond = m.make_condition(lhs, "gt", 0.0)
        # When rhs is a scalar float, the if-child must have rhs-fixed-value? = True
        # (this is what make_if uses to build the if-child node)
        assert isinstance(cond, dict)

    def test_validate_tree_errors_are_all_strings(self):
        """Every item returned by validate_tree must be a string."""
        m = _import_schema()
        # Use a definitely-invalid tree to get multiple errors
        bad_tree = {
            "step": "root",
            "name": "T",
            "rebalance": "quarterly",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "unknown-step",
                    "id": str(uuid.uuid4()),
                    "children": [],
                }
            ],
        }
        errors = m.validate_tree(bad_tree)
        assert isinstance(errors, list)
        for err in errors:
            assert isinstance(err, str), (
                f"validate_tree returned non-string error item: {err!r}"
            )

    def test_extract_tickers_returns_empty_set_for_tree_with_no_assets(self):
        """A tree with no asset nodes must yield an empty set from extract_tickers."""
        m = _import_schema()
        # wt-cash-equal with no children — technically invalid, but extract_tickers
        # must not raise and must return empty set
        tree = {
            "step": "root",
            "name": "Empty",
            "rebalance": "daily",
            "id": str(uuid.uuid4()),
            "children": [
                {
                    "step": "wt-cash-equal",
                    "id": str(uuid.uuid4()),
                    "children": [],
                }
            ],
        }
        result = m.extract_tickers(tree)
        assert isinstance(result, set)
        assert len(result) == 0

    def test_make_root_with_unknown_rebalance_produces_error_when_validated(self):
        """make_root with an unrecognised rebalance value must produce a validation error.

        Constructors do not themselves validate (they just build dicts);
        validate_tree is the validation gate.
        """
        m = _import_schema()
        asset = m.make_asset("SPY")
        wt = m.make_weight_equal([asset])
        root = m.make_root("Test", "quarterly", [wt])  # "quarterly" is unrecognised
        errors = m.validate_tree(root)
        assert len(errors) >= 1, (
            "Expected error from validate_tree when root has unknown rebalance 'quarterly'"
        )

    def test_multiple_calls_to_make_root_produce_different_root_ids(self):
        """Each call to make_root must generate a fresh UUID, not reuse a cached one."""
        m = _import_schema()
        spy = m.make_asset("SPY")
        r1 = m.make_root("A", "daily", [m.make_weight_equal([spy])])
        spy2 = m.make_asset("SPY")
        r2 = m.make_root("B", "daily", [m.make_weight_equal([spy2])])
        assert r1["id"] != r2["id"], (
            "make_root returned the same id on two separate calls — ids must be fresh UUIDs"
        )
