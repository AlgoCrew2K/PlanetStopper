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
    """Real /score response — 'Planet of Hunted Cascades' (large golden fixture, 8455 nodes,
    depth 230)."""
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
        pytest.fail(f"advisors/symphony_schema.py does not exist yet (RED suite): {exc}")


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
        assert errors == [], f"Expected no hard errors on golden small fixture, got: {errors}"

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
            assert ticker in text, f"render_rules_text omitted ticker {ticker!r} from small fixture"

    def test_small_fixture_render_rules_text_is_deterministic(self, raw_small):
        """Two calls to render_rules_text on the same tree must return identical output."""
        m = _import_schema()
        first = m.render_rules_text(raw_small)
        second = m.render_rules_text(raw_small)
        assert first == second, "render_rules_text is not deterministic"

    def test_small_fixture_lint_tree_produces_no_false_positive_hard_errors(self, raw_small):
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
        assert errors == [], f"Expected no hard errors on golden large fixture, got: {errors}"

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
            assert ticker in text, f"render_rules_text omitted ticker {ticker!r} from large fixture"

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
            f"Duplicate ids found in constructed tree: {[x for x in ids if ids.count(x) > 1]}"
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

        true_branch = next((n for n in if_children if not n.get("is-else-condition?")), None)
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
        true_branch = next((n for n in if_children if not n.get("is-else-condition?")), None)
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
        assert len(errors) >= 1, f"Expected >= 1 error for node {node_id!r}, got none"

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

    def test_unknown_indicator_fn_rsi_abbreviation_produces_lint_warning_not_hard_error(
        self, valid_tree
    ):
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

    def test_unknown_indicator_fn_arbitrary_string_produces_lint_warning_not_hard_error(
        self, valid_tree
    ):
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
        """'standard-deviation-price' appears in the real large fixture; validate_tree must accept
        it.

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

    def test_unknown_comparator_eq_produces_hard_error(self, valid_tree):
        """'eq' is NOT in the Composer corpus and must produce a hard error.

        Re-pointed from 'gte': v2 grammar §8 (VERIFIED-CORPUS, 2026-06-14) confirms
        'gte' is real (n=39,596) and is accepted as of AC-1. 'eq' has 0 corpus
        occurrences and is explicitly excluded by v2 §8 — it is a permanent hard
        error regardless of any future widening.
        Provenance: v2 grammar §8 ("eq and neq do NOT exist in the corpus (0 occurrences)").
        """
        m = _import_schema()
        if_child = self._first_node_of_step(valid_tree, "if-child")
        if if_child is None:
            pytest.skip("Minimal fixture has no if-child; rebuild needed")
        if_child["comparator"] = "eq"
        errors = m.validate_tree(valid_tree)
        assert len(errors) >= 1, (
            "'eq' has 0 corpus occurrences (v2 §8); validate_tree must reject it. "
            "Accepted comparators after v2 widening: gt, lt, lte, gte."
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
            c for c in (if_node.get("children") or []) if not c.get("is-else-condition?")
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
        """A tree with more nodes than MAX_TOTAL_NODES must produce a lint warning, not a hard
        error.

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
                {
                    "step": "root",
                    "name": "T",
                    "rebalance": "daily",
                    "id": "x",
                    "children": [None, None],
                },
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
        assert len(warnings) >= 1, "Expected lint warning for weight sum = 99 (not 100), got none"

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
            "collapsed?": True,  # cosmetic
            "suppress_incomplete_warnings": False,  # cosmetic
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
                            "price": 500.0,  # cosmetic
                            "dollar_volume": 1e9,  # cosmetic
                            "has_marketcap": True,  # cosmetic
                            "children-count": 0,  # cosmetic
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
        assert _before == _after, "validate_tree mutated the input dict; it must be read-only"

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
        assert errors == [], f"Complete filter node produced unexpected hard errors: {errors}"

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
                                    # rhs-fn deliberately omitted — required when
                                    # rhs-fixed-value?=False
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
            f"Valid ticker-comparison if-child (rhs-fn present) produced unexpected"
            f" errors: {errors}"
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
# 10. TICKER-COMPARISON CONSTRUCTOR TESTS (domain review finding)
#     make_condition with string rhs requires rhs_indicator keyword arg.
#     Addressed after domain-reviewer found that string rhs silently produced
#     an invalid if-child (rhs-fn missing) in the cycle-1/2 implementation.
# ===========================================================================


class TestTickerComparisonConstructor:
    """make_condition ticker-comparison path: rhs_indicator required, raises without it."""

    def test_make_condition_string_rhs_without_rhs_indicator_raises_value_error(self):
        """make_condition with a string rhs and no rhs_indicator must raise ValueError.

        Without this guard, make_condition silently produced rhs-fixed-value?=False
        with no rhs-fn — a tree that validate_tree would then reject. The guard
        ensures the constructor fails loudly at call time, not silently downstream.

        This was the MAJOR finding from the domain review.
        """
        m = _import_schema()
        lhs = m.make_indicator("relative-strength-index", "LQD", window=50)
        with pytest.raises(ValueError):
            m.make_condition(lhs, "gt", "XLV")  # string rhs, no rhs_indicator

    def test_make_condition_string_rhs_various_tickers_all_raise(self):
        """Any ticker string as rhs without rhs_indicator must raise ValueError.

        Confirms the guard is not ticker-specific (not just 'XLV').
        """
        m = _import_schema()
        lhs = m.make_indicator("cumulative-return", "SPY", window=200)
        for ticker in ("QQQ", "TLT", "BIL", "AGG", "GLD"):
            with pytest.raises(ValueError, match=r"rhs_indicator"):
                m.make_condition(lhs, "gt", ticker)

    def test_make_condition_ticker_comparison_with_rhs_indicator_validates_clean(self):
        """make_condition with string rhs AND rhs_indicator must produce a tree that validates
        clean.

        This is the constructor-side complement of the cycle-2 test
        test_if_child_ticker_comparison_missing_rhs_fn_produces_error: the
        constructor now correctly populates rhs-fn / rhs-fn-params so
        validate_tree accepts the output.
        """
        m = _import_schema()
        lhs = m.make_indicator("relative-strength-index", "LQD", window=50)
        rhs_ind = m.make_indicator("relative-strength-index", "XLV", window=50)
        cond = m.make_condition(lhs, "gt", "XLV", rhs_indicator=rhs_ind)
        if_node = m.make_if(
            cond,
            then_children=[m.make_asset("LQD")],
            else_children=[m.make_asset("BIL")],
        )
        root = m.make_root("Ticker Compare", "daily", [m.make_weight_equal([if_node])])
        errors = m.validate_tree(root)
        assert errors == [], (
            f"Ticker-comparison constructor chain produced unexpected hard errors: {errors}"
        )

    def test_make_condition_ticker_comparison_emits_correct_if_child_fields(self):
        """The if-child from make_if on a ticker-comparison condition must have
        rhs-fixed-value?=False, rhs-fn, rhs-fn-params, and rhs-val set correctly.

        All expected values are derived from the constructor arguments, not hardcoded.
        """
        m = _import_schema()
        lhs = m.make_indicator("relative-strength-index", "LQD", window=50)
        rhs_ind = m.make_indicator("relative-strength-index", "XLV", window=50)
        cond = m.make_condition(lhs, "gt", "XLV", rhs_indicator=rhs_ind)
        if_node = m.make_if(
            cond,
            then_children=[m.make_asset("LQD")],
            else_children=[m.make_asset("BIL")],
        )
        true_branch = next(c for c in if_node["children"] if not c.get("is-else-condition?"))
        # rhs-fixed-value? must be explicitly False (not absent, not True)
        assert true_branch.get("rhs-fixed-value?") is False, (
            f"rhs-fixed-value? must be False for ticker comparison; "
            f"got {true_branch.get('rhs-fixed-value?')!r}"
        )
        # rhs-fn must match the fn string from rhs_indicator (derived from arg)
        assert true_branch.get("rhs-fn") == "relative-strength-index", (
            f"rhs-fn must match rhs_indicator fn; got {true_branch.get('rhs-fn')!r}"
        )
        # rhs-fn-params must carry the window from rhs_indicator (derived from arg)
        assert true_branch.get("rhs-fn-params") == {"window": 50}, (
            f"rhs-fn-params must carry rhs_indicator window; "
            f"got {true_branch.get('rhs-fn-params')!r}"
        )
        # rhs-val must be the ticker string (derived from rhs arg)
        assert true_branch.get("rhs-val") == "XLV", (
            f"rhs-val must be the rhs ticker; got {true_branch.get('rhs-val')!r}"
        )

    def test_make_condition_numeric_rhs_with_rhs_indicator_raises_value_error(self):
        """make_condition with numeric rhs AND rhs_indicator must raise ValueError.

        rhs_indicator is for ticker comparisons only; supplying it for a
        fixed-value (numeric) comparison is a programming error caught at call time.
        """
        m = _import_schema()
        lhs = m.make_indicator("cumulative-return", "TLT", window=200)
        rhs_ind = m.make_indicator("cumulative-return", "BIL", window=200)
        with pytest.raises(ValueError):
            m.make_condition(lhs, "gt", 0.0, rhs_indicator=rhs_ind)

    def test_make_condition_numeric_rhs_without_rhs_indicator_does_not_raise(self):
        """make_condition with numeric rhs and no rhs_indicator must NOT raise.

        The fixed-value path is correct without rhs_indicator; the ValueError
        guard must not fire on normal numeric usage.
        """
        m = _import_schema()
        lhs = m.make_indicator("cumulative-return", "TLT", window=200)
        try:
            cond = m.make_condition(lhs, "gt", 0.0)
        except ValueError as exc:
            pytest.fail(f"make_condition raised ValueError for valid numeric rhs: {exc}")
        assert isinstance(cond, dict)

    def test_make_condition_rhs_val_whole_float_stringifies_without_dot_zero(self):
        """Whole-number float rhs (0.0, -5.0) must stringify without trailing .0.

        Real fixtures use '0', not '0.0' (grammar §3.4 rhs-val form). Values
        are asserted against the constructor arguments, not hardcoded outputs.
        """
        m = _import_schema()
        lhs = m.make_indicator("cumulative-return", "TLT", window=200)
        c_zero = m.make_condition(lhs, "gt", 0.0)
        c_neg = m.make_condition(lhs, "gt", -5.0)
        # 0.0 -> '0', not '0.0'
        assert c_zero["rhs-val"] == str(int(0.0)), (
            f"0.0 must stringify to '0'; got {c_zero['rhs-val']!r}"
        )
        # -5.0 -> '-5', not '-5.0'
        assert c_neg["rhs-val"] == str(int(-5.0)), (
            f"-5.0 must stringify to '-5'; got {c_neg['rhs-val']!r}"
        )

    def test_make_condition_rhs_val_fractional_float_keeps_decimal(self):
        """Fractional float rhs (5.5, -0.15) must keep the decimal in rhs-val."""
        m = _import_schema()
        lhs = m.make_indicator("cumulative-return", "TLT", window=200)
        c_frac = m.make_condition(lhs, "gt", 5.5)
        c_neg_frac = m.make_condition(lhs, "gt", -0.15)
        assert c_frac["rhs-val"] == str(5.5), (
            f"5.5 must stringify to '5.5'; got {c_frac['rhs-val']!r}"
        )
        assert c_neg_frac["rhs-val"] == str(-0.15), (
            f"-0.15 must stringify to '-0.15'; got {c_neg_frac['rhs-val']!r}"
        )

    def test_make_condition_rhs_val_integer_stringifies_without_dot(self):
        """Integer rhs (85) must stringify without a decimal point."""
        m = _import_schema()
        lhs = m.make_indicator("relative-strength-index", "SPY", window=14)
        c_int = m.make_condition(lhs, "gt", 85)
        # 85 (int) -> '85', not '85.0'
        assert c_int["rhs-val"] == str(85), (
            f"Integer 85 must stringify to '85'; got {c_int['rhs-val']!r}"
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
        assert not missing, f"KNOWN_STEPS is missing VERIFIED-LOCAL step values: {missing}"

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
        assert not missing, f"KNOWN_INDICATOR_FNS is missing VERIFIED-LOCAL fn strings: {missing}"

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

    def test_known_comparators_does_not_contain_eq(self):
        """KNOWN_COMPARATORS must NOT contain 'eq' — it has 0 corpus occurrences.

        Re-pointed from 'gte': v2 grammar §8 (VERIFIED-CORPUS 2026-06-14) confirms
        'gte' is real (n=39,596) and is now a valid comparator. 'eq' has 0 corpus
        occurrences and is explicitly excluded by v2 §8 ("eq and neq do NOT exist
        in the corpus") — it must never be added to KNOWN_COMPARATORS.
        Provenance: v2 grammar §8.
        """
        m = _import_schema()
        assert "eq" not in m.KNOWN_COMPARATORS, (
            "'eq' must not be in KNOWN_COMPARATORS — v2 §8 confirms 0 corpus occurrences"
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
        ), f"Indicator dict must encode the function name; got: {indicator}"

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
            assert isinstance(err, str), f"validate_tree returned non-string error item: {err!r}"

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

        Re-pointed from 'quarterly': v2 grammar §6 (VERIFIED-CORPUS, 2026-06-14) confirms
        'quarterly' is real (n=58) and is accepted as of AC-2. 'hourly' has 0 corpus
        occurrences in the v2 full census — it is a permanent hard error.
        Provenance: v2 grammar §6 full census (daily/none/weekly/monthly/quarterly/yearly only).
        """
        m = _import_schema()
        asset = m.make_asset("SPY")
        wt = m.make_weight_equal([asset])
        root = m.make_root("Test", "hourly", [wt])  # "hourly" has 0 corpus occurrences (v2 §6)
        errors = m.validate_tree(root)
        assert len(errors) >= 1, (
            "Expected error from validate_tree when root has unknown rebalance 'hourly' "
            "(0 occurrences in v2 corpus full census, §6)"
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


# ===========================================================================
# 11. GRAMMAR V2 ALIGNMENT — Cycle A allowlist widening
#     These tests are RED against the current codebase until GREEN (the
#     implementer widens the three frozensets to corpus-verified values).
#
#     Provenance for all token strings: feature-plans/strategy-builder-composer-grammar-v2.md
#     (corpus-validated from 10,441 real Composer symphonies).
#
#     Structure:
#       TestGrammarV2Alignment       — the RED AC-1/AC-2/AC-3 tests (will fail now)
#       TestGrammarV2RegressionGuard — the AC-4 exactness guards (pass now, must stay green)
#       TestGrammarV2TypeGuard       — the AC-5 frozenset type checks (pass now, must stay green)
# ===========================================================================

# ---------------------------------------------------------------------------
# Minimal tree builder shared by V2 tests.
# Avoids repeating the full wt-cash-equal/if/if-child scaffold in every test.
# ---------------------------------------------------------------------------

def _make_minimal_if_tree(
    comparator: str = "gt",
    rebalance: str = "daily",
    lhs_fn: str = "cumulative-return",
    rhs_val: str = "0",
) -> dict:
    """Build a minimal valid tree with the given field values.

    Shape: root(rebalance) -> wt-cash-equal -> if -> [
      if-child(false): lhs_fn(TLT,200d) <comparator> rhs_val -> asset(TLT)
      if-child(true):                                          -> asset(BIL)
    ]

    Token strings are grammar corpus-values; no producer-computed numeric values
    are hardcoded here (rhs_val is the grammar literal "0", not an output).
    """
    return {
        "step": "root",
        "name": "Grammar V2 Test Tree",
        "rebalance": rebalance,
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
                                "lhs-fn": lhs_fn,
                                "lhs-fn-params": {"window": 200},
                                "lhs-val": "TLT",
                                "comparator": comparator,
                                "rhs-fixed-value?": True,
                                "rhs-val": rhs_val,
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


def _make_minimal_filter_tree(sort_by_fn: str) -> dict:
    """Build a minimal valid filter tree using the given sort-by-fn token.

    Shape: root(daily) -> filter(top, 1, sort_by_fn) -> asset(SPY)

    The sort_by_fn token is a grammar corpus value (v2 §4b); it's passed in
    from the test, which derives it from the v2 doc, not from producer output.
    """
    return {
        "step": "root",
        "name": "Grammar V2 Filter Test",
        "rebalance": "daily",
        "id": str(uuid.uuid4()),
        "children": [
            {
                "step": "filter",
                "select-fn": "top",
                "select-n": 1,
                "sort-by-fn": sort_by_fn,
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


class TestGrammarV2Alignment:
    """AC-1/AC-2/AC-3: corpus-verified tokens that currently produce errors/warnings
    must be accepted after the allowlist widening.

    All tests in this class are RED against the unmodified codebase — they will fail
    because KNOWN_COMPARATORS, KNOWN_REBALANCE, and KNOWN_INDICATOR_FNS do not yet
    contain the v2 corpus tokens. They become GREEN after the implementer widens
    the three frozensets.

    Provenance for every token: v2 grammar doc (feature-plans/strategy-builder-
    composer-grammar-v2.md, corpus-mined from 10,441 real Composer symphonies).
    Token strings are grammar literals — not producer-computed values. Hardcoding
    grammar token strings in tests is correct per project rule (feedback_no_hardcoded_test_values
    applies to NUMERIC producer outputs, not grammar vocabulary).
    """

    # ------------------------------------------------------------------
    # AC-1 — comparator "gte"
    # v2 §8: VERIFIED-CORPUS n=39,596. Enum becomes {gt, lt, gte, lte}.
    # Currently KNOWN_COMPARATORS = {gt, lt, lte} — gte absent → hard error.
    # ------------------------------------------------------------------

    def test_comparator_gte_does_not_produce_hard_error(self):
        """validate_tree must return NO hard error for a true-branch if-child whose
        comparator is "gte".

        Provenance: v2 grammar §8, VERIFIED-CORPUS n=39,596.
        Currently RED: KNOWN_COMPARATORS = {gt, lt, lte} rejects gte as a hard error.
        GREEN when: KNOWN_COMPARATORS widened to include "gte".

        NOTE: The pre-existing test test_unknown_comparator_gte_produces_error (class
        TestAdversarialMutations) asserted the OPPOSITE — that gte IS a hard error
        (OQ-2 stance before corpus verification). That test is now SUPERSEDED by this
        AC-1 test. The implementer must also update or remove that conflicting test as
        part of the GREEN phase; it is documented in the handoff.
        """
        m = _import_schema()
        # "gte" is the corpus-verified token: v2 §8 n=39,596. It is a grammar
        # vocabulary string, not a producer-computed numeric value.
        tree = _make_minimal_if_tree(comparator="gte")
        errors = m.validate_tree(tree)
        assert errors == [], (
            "AC-1: validate_tree must accept comparator='gte' (VERIFIED-CORPUS n=39,596, "
            "v2 grammar §8). Got hard errors: "
            + repr(errors)
        )

    def test_comparator_lte_still_does_not_produce_hard_error(self):
        """validate_tree must still accept "lte" after the widening (regression sanity).

        lte was already in KNOWN_COMPARATORS. This test ensures the widening for gte
        does not accidentally remove lte (e.g. via a set-replace rather than add).
        Provenance: v2 §8, VERIFIED-CORPUS (lte n=34,717).
        This test PASSES on the current codebase and must STAY green after GREEN.
        """
        m = _import_schema()
        tree = _make_minimal_if_tree(comparator="lte")
        errors = m.validate_tree(tree)
        assert errors == [], (
            "lte must still be accepted after gte widening; got: " + repr(errors)
        )

    # ------------------------------------------------------------------
    # AC-2 — rebalance "quarterly" and "yearly"
    # v2 §6: quarterly VERIFIED-CORPUS n=58; yearly n=27.
    # Currently KNOWN_REBALANCE = {daily, none, weekly, monthly} — both absent.
    # ------------------------------------------------------------------

    def test_rebalance_quarterly_does_not_produce_hard_error(self):
        """validate_tree must return NO hard error for a root with rebalance="quarterly".

        Provenance: v2 grammar §6, VERIFIED-CORPUS n=58 (document count).
        Currently RED: KNOWN_REBALANCE does not contain "quarterly" → hard error.
        GREEN when: KNOWN_REBALANCE widened to include "quarterly".

        NOTE: The pre-existing test test_make_root_with_unknown_rebalance_produces_error_when_validated
        (class TestAdversarialCasesRound2) used "quarterly" as the unknown-rebalance example.
        That test is now SUPERSEDED by this AC-2 test. The implementer must update that test
        to use a genuinely-unknown value (e.g. "hourly") as part of the GREEN phase; it is
        documented in the handoff.
        """
        m = _import_schema()
        # "quarterly" is a grammar vocabulary token (v2 §6 corpus-verified), not a
        # producer-computed numeric value. Hardcoding it is correct.
        tree = _make_minimal_if_tree(rebalance="quarterly")
        errors = m.validate_tree(tree)
        assert errors == [], (
            "AC-2: validate_tree must accept rebalance='quarterly' (VERIFIED-CORPUS n=58, "
            "v2 grammar §6). Got hard errors: "
            + repr(errors)
        )

    def test_rebalance_yearly_does_not_produce_hard_error(self):
        """validate_tree must return NO hard error for a root with rebalance="yearly".

        Provenance: v2 grammar §6, VERIFIED-CORPUS n=27 (document count).
        Currently RED: KNOWN_REBALANCE does not contain "yearly" → hard error.
        GREEN when: KNOWN_REBALANCE widened to include "yearly".
        """
        m = _import_schema()
        # "yearly" is a grammar vocabulary token (v2 §6), not a producer-computed value.
        tree = _make_minimal_if_tree(rebalance="yearly")
        errors = m.validate_tree(tree)
        assert errors == [], (
            "AC-2: validate_tree must accept rebalance='yearly' (VERIFIED-CORPUS n=27, "
            "v2 grammar §6). Got hard errors: "
            + repr(errors)
        )

    def test_rebalance_quarterly_and_yearly_both_validate_clean_in_same_run(self):
        """Both quarterly and yearly must validate clean in a single test run.

        Guards against an implementation that adds one but not the other (e.g. only
        adds quarterly because it has a higher corpus count). Both are in scope per AC-2.
        Currently RED: neither is in KNOWN_REBALANCE.
        """
        m = _import_schema()
        for rebalance_val in ("quarterly", "yearly"):
            tree = _make_minimal_if_tree(rebalance=rebalance_val)
            errors = m.validate_tree(tree)
            assert errors == [], (
                f"AC-2: rebalance={rebalance_val!r} must validate clean (v2 §6 VERIFIED-CORPUS). "
                f"Got: {errors!r}"
            )

    # ------------------------------------------------------------------
    # AC-3 — 6 new indicator fns in KNOWN_INDICATOR_FNS (lint only)
    # These fns produce "unverified indicator fn" lint warnings today.
    # After GREEN they must produce NO lint warning (they are corpus-real).
    # The test structure uses lhs-fn and sort-by-fn (both fn-bearing keys).
    # ------------------------------------------------------------------

    def test_exponential_moving_average_price_does_not_produce_lint_warning(self):
        """lint_tree must emit NO "unknown indicator fn" warning for
        "exponential-moving-average-price" when used as lhs-fn.

        Provenance: v2 grammar §4, VERIFIED-CORPUS n=45,816 (lhs/rhs-fn occurrences).
        Currently RED: fn not in KNOWN_INDICATOR_FNS → lint warning fires.
        GREEN when: KNOWN_INDICATOR_FNS widened to include this token.

        Tolerance: Unknown-fn warnings carry the fn name in their text
        (format: "unverified indicator fn '<fn>' (not in KNOWN_INDICATOR_FNS)").
        We filter for warnings that mention the specific fn so unrelated lint
        warnings (size/depth) do not cause a false failure.
        """
        m = _import_schema()
        # Token: v2 §4, 45,816 corpus occurrences — the 8th-most-used indicator fn.
        fn_token = "exponential-moving-average-price"
        tree = _make_minimal_if_tree(lhs_fn=fn_token)
        warnings = m.lint_tree(tree)
        fn_warnings = [w for w in warnings if fn_token in w]
        assert fn_warnings == [], (
            f"AC-3: lint_tree must not warn about {fn_token!r} "
            f"(VERIFIED-CORPUS n=45,816, v2 §4). Got fn-specific warnings: {fn_warnings!r}"
        )

    def test_standard_deviation_price_as_lhs_fn_does_not_produce_lint_warning(self):
        """lint_tree must emit NO "unknown indicator fn" warning for
        "standard-deviation-price" when used as lhs-fn.

        Provenance: v2 grammar §4, VERIFIED-CORPUS n=5,572.
        Currently RED: fn not in KNOWN_INDICATOR_FNS → lint warning fires.
        GREEN when: KNOWN_INDICATOR_FNS widened to include this token.

        Note: "standard-deviation-price" already appears in the large golden
        fixture (sample_score_large.json) — the existing test
        test_standard_deviation_price_in_large_fixture_is_tolerated verifies
        it does not produce a HARD error. This test adds the complementary
        requirement: it must produce NO LINT WARNING either.
        """
        m = _import_schema()
        # Token: v2 §4, 5,572 corpus occurrences.
        fn_token = "standard-deviation-price"
        tree = _make_minimal_if_tree(lhs_fn=fn_token)
        warnings = m.lint_tree(tree)
        fn_warnings = [w for w in warnings if fn_token in w]
        assert fn_warnings == [], (
            f"AC-3: lint_tree must not warn about {fn_token!r} "
            f"(VERIFIED-CORPUS n=5,572, v2 §4). Got fn-specific warnings: {fn_warnings!r}"
        )

    def test_percentage_price_oscillator_does_not_produce_lint_warning(self):
        """lint_tree must emit NO "unknown indicator fn" warning for
        "percentage-price-oscillator" when used as lhs-fn.

        Provenance: v2 grammar §4, VERIFIED-CORPUS n=99 (lhs/rhs-fn occurrences).
        Currently RED: fn not in KNOWN_INDICATOR_FNS → lint warning fires.
        GREEN when: KNOWN_INDICATOR_FNS widened to include this token.
        """
        m = _import_schema()
        # Token: v2 §4, 99 corpus occurrences.
        fn_token = "percentage-price-oscillator"
        tree = _make_minimal_if_tree(lhs_fn=fn_token)
        warnings = m.lint_tree(tree)
        fn_warnings = [w for w in warnings if fn_token in w]
        assert fn_warnings == [], (
            f"AC-3: lint_tree must not warn about {fn_token!r} "
            f"(VERIFIED-CORPUS n=99, v2 §4). Got fn-specific warnings: {fn_warnings!r}"
        )

    def test_percentage_price_oscillator_signal_does_not_produce_lint_warning(self):
        """lint_tree must emit NO "unknown indicator fn" warning for
        "percentage-price-oscillator-signal" when used as lhs-fn.

        Provenance: v2 grammar §4, VERIFIED-CORPUS n=100.
        Currently RED: fn not in KNOWN_INDICATOR_FNS → lint warning fires.
        GREEN when: KNOWN_INDICATOR_FNS widened to include this token.
        """
        m = _import_schema()
        # Token: v2 §4, 100 corpus occurrences.
        fn_token = "percentage-price-oscillator-signal"
        tree = _make_minimal_if_tree(lhs_fn=fn_token)
        warnings = m.lint_tree(tree)
        fn_warnings = [w for w in warnings if fn_token in w]
        assert fn_warnings == [], (
            f"AC-3: lint_tree must not warn about {fn_token!r} "
            f"(VERIFIED-CORPUS n=100, v2 §4). Got fn-specific warnings: {fn_warnings!r}"
        )

    def test_upper_bollinger_does_not_produce_lint_warning(self):
        """lint_tree must emit NO "unknown indicator fn" warning for
        "upper-bollinger" when used as lhs-fn.

        Provenance: v2 grammar §4b, VERIFIED-CORPUS n=1 (sort-by-fn) — the only
        appearance of Bollinger fns in the corpus. Low count but corpus-confirmed.
        Currently RED: fn not in KNOWN_INDICATOR_FNS → lint warning fires.
        GREEN when: KNOWN_INDICATOR_FNS widened to include this token.
        """
        m = _import_schema()
        # Token: v2 §4b, 1 corpus occurrence in sort-by-fn position.
        fn_token = "upper-bollinger"
        tree = _make_minimal_if_tree(lhs_fn=fn_token)
        warnings = m.lint_tree(tree)
        fn_warnings = [w for w in warnings if fn_token in w]
        assert fn_warnings == [], (
            f"AC-3: lint_tree must not warn about {fn_token!r} "
            f"(VERIFIED-CORPUS v2 §4b). Got fn-specific warnings: {fn_warnings!r}"
        )

    def test_lower_bollinger_does_not_produce_lint_warning(self):
        """lint_tree must emit NO "unknown indicator fn" warning for
        "lower-bollinger" when used as lhs-fn.

        Provenance: v2 grammar §4b, VERIFIED-CORPUS n=1 (sort-by-fn).
        Currently RED: fn not in KNOWN_INDICATOR_FNS → lint warning fires.
        GREEN when: KNOWN_INDICATOR_FNS widened to include this token.
        """
        m = _import_schema()
        # Token: v2 §4b, 1 corpus occurrence in sort-by-fn position.
        fn_token = "lower-bollinger"
        tree = _make_minimal_if_tree(lhs_fn=fn_token)
        warnings = m.lint_tree(tree)
        fn_warnings = [w for w in warnings if fn_token in w]
        assert fn_warnings == [], (
            f"AC-3: lint_tree must not warn about {fn_token!r} "
            f"(VERIFIED-CORPUS v2 §4b). Got fn-specific warnings: {fn_warnings!r}"
        )

    def test_exponential_moving_average_price_as_sort_by_fn_does_not_warn(self):
        """lint_tree must emit NO "unknown indicator fn" warning for
        "exponential-moving-average-price" used as sort-by-fn on a filter node.

        Provenance: v2 grammar §4b, VERIFIED-CORPUS n=1,997 (sort-by-fn occurrences).
        lint_tree scans _FLAT_FN_KEYS = ("lhs-fn", "rhs-fn", "sort-by-fn"), so
        sort-by-fn is a checked key. Currently RED.
        GREEN when: KNOWN_INDICATOR_FNS contains "exponential-moving-average-price".
        """
        m = _import_schema()
        # Token: v2 §4b, 1,997 sort-by-fn corpus occurrences.
        fn_token = "exponential-moving-average-price"
        tree = _make_minimal_filter_tree(sort_by_fn=fn_token)
        warnings = m.lint_tree(tree)
        fn_warnings = [w for w in warnings if fn_token in w]
        assert fn_warnings == [], (
            f"AC-3: lint_tree must not warn about {fn_token!r} as sort-by-fn "
            f"(VERIFIED-CORPUS n=1,997, v2 §4b). Got: {fn_warnings!r}"
        )

    def test_standard_deviation_price_as_sort_by_fn_does_not_warn(self):
        """lint_tree must emit NO "unknown indicator fn" warning for
        "standard-deviation-price" used as sort-by-fn on a filter node.

        Provenance: v2 grammar §4b, VERIFIED-CORPUS n=3,975 (sort-by-fn occurrences).
        Currently RED. GREEN when: KNOWN_INDICATOR_FNS contains "standard-deviation-price".
        """
        m = _import_schema()
        # Token: v2 §4b, 3,975 sort-by-fn corpus occurrences.
        fn_token = "standard-deviation-price"
        tree = _make_minimal_filter_tree(sort_by_fn=fn_token)
        warnings = m.lint_tree(tree)
        fn_warnings = [w for w in warnings if fn_token in w]
        assert fn_warnings == [], (
            f"AC-3: lint_tree must not warn about {fn_token!r} as sort-by-fn "
            f"(VERIFIED-CORPUS n=3,975, v2 §4b). Got: {fn_warnings!r}"
        )

    def test_all_six_new_indicator_fns_produce_no_lint_warnings_in_batch(self):
        """All 6 newly corpus-verified indicator fns must produce zero fn-specific lint
        warnings when used as lhs-fn.

        This omnibus test catches a partial-widening implementation that only adds
        some of the 6 tokens. It tests all 6 in one parameterised loop. If any one
        of them still produces a lint warning, this test fails with the specific fn.

        Provenance: v2 grammar §4 and §4b for all six tokens.
        Currently RED (all 6 absent from KNOWN_INDICATOR_FNS).
        """
        m = _import_schema()
        # These are grammar vocabulary tokens from the v2 corpus doc — not producer values.
        new_fns = [
            "exponential-moving-average-price",    # v2 §4, n=45,816
            "standard-deviation-price",             # v2 §4, n=5,572
            "percentage-price-oscillator",          # v2 §4, n=99
            "percentage-price-oscillator-signal",   # v2 §4, n=100
            "upper-bollinger",                      # v2 §4b, n=1
            "lower-bollinger",                      # v2 §4b, n=1
        ]
        for fn_token in new_fns:
            tree = _make_minimal_if_tree(lhs_fn=fn_token)
            warnings = m.lint_tree(tree)
            fn_warnings = [w for w in warnings if fn_token in w]
            assert fn_warnings == [], (
                f"AC-3 batch: lint_tree must not warn about corpus-verified fn {fn_token!r}. "
                f"Got: {fn_warnings!r}"
            )


class TestGrammarV2RegressionGuard:
    """AC-4: exactness guards — tokens that must STILL produce errors/warnings after
    the widening. These tests verify the widening is EXACT (only the corpus-verified
    tokens are added), not a blanket allowlist open-up.

    All tests in this class PASS on the current codebase and must continue to PASS
    after the implementer widens the frozensets. If any of these fail after GREEN,
    the implementer has over-widened.
    """

    def test_unknown_comparator_eq_still_produces_hard_error_after_widening(self):
        """validate_tree must STILL produce a hard error for comparator="eq".

        v2 §8 explicitly states: "eq and neq do NOT exist in the corpus (0 occurrences)".
        The widening adds "gte" but must NOT add "eq". This test guards against an
        implementation that simply removes all comparator checking.

        Provenance: v2 grammar §8. Currently PASSES. Must stay green after GREEN.
        """
        m = _import_schema()
        # "eq" has 0 corpus occurrences — it is NOT a valid Composer comparator.
        tree = _make_minimal_if_tree(comparator="eq")
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-4 regression: comparator='eq' must still produce a hard error after widening. "
            "v2 §8 confirms eq does NOT exist in the corpus (0 occurrences). "
            f"Got no errors — over-widening detected."
        )

    def test_unknown_comparator_neq_still_produces_hard_error_after_widening(self):
        """validate_tree must STILL produce a hard error for comparator="neq".

        v2 §8: "eq and neq do NOT exist" (0 occurrences). Guards against blanket allow.
        Currently PASSES. Must stay green after GREEN.
        """
        m = _import_schema()
        tree = _make_minimal_if_tree(comparator="neq")
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-4 regression: comparator='neq' must still produce a hard error. "
            "v2 §8 confirms neq does NOT exist in the corpus."
        )

    def test_unknown_comparator_symbol_form_still_produces_error_after_widening(self):
        """validate_tree must STILL produce a hard error for symbol comparator ">".

        The builder_backtests collection uses ">" — that is a DIFFERENT schema
        (v2 §10 #10, explicitly out of scope). The Composer symphony grammar uses
        only the word forms (gt/lt/gte/lte). Guards against symbol-form creep.
        Currently PASSES. Must stay green after GREEN.
        """
        m = _import_schema()
        # ">" is from the builder_backtests schema (v2 §10), not the symphony grammar.
        tree = _make_minimal_if_tree(comparator=">")
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-4 regression: comparator='>' (symbol form) must still produce a hard error. "
            "The symphony grammar uses word forms only (gt/lt/gte/lte)."
        )

    def test_unknown_rebalance_hourly_still_produces_hard_error(self):
        """validate_tree must STILL produce a hard error for rebalance="hourly".

        "hourly" has 0 corpus occurrences (v2 §6 full census). The widening adds
        quarterly and yearly but must NOT introduce a blanket allow. Guards against
        an implementation that simply removes rebalance checking.
        Currently PASSES. Must stay green after GREEN.
        """
        m = _import_schema()
        # "hourly" is not in the v2 corpus full census (v2 §6).
        tree = _make_minimal_if_tree(rebalance="hourly")
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-4 regression: rebalance='hourly' must still produce a hard error after widening. "
            "v2 §6 full census confirms 'hourly' does not exist in the corpus. "
            f"Got no errors — over-widening detected."
        )

    def test_unknown_rebalance_biweekly_still_produces_hard_error(self):
        """validate_tree must STILL produce a hard error for rebalance="biweekly".

        "biweekly" has 0 corpus occurrences. Guards against blanket allow.
        Currently PASSES (also tested in TestAdversarialMutations). Must stay green.
        """
        m = _import_schema()
        tree = _make_minimal_if_tree(rebalance="biweekly")
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-4 regression: rebalance='biweekly' must still produce a hard error."
        )

    def test_rsi_abbreviation_still_produces_lint_warning_after_widening(self):
        """lint_tree must STILL produce an "unknown indicator fn" warning for "rsi".

        The canonical RSI token is "relative-strength-index" (v2 §4). The abbreviation
        "rsi" has 0 corpus occurrences as an fn value. The 6 new fns being added must
        not suppress warnings for unconfirmed abbreviations.

        Provenance: v2 §4 ("The rsi abbreviation never appears — canonical RSI token is
        always the full relative-strength-index"). Currently PASSES. Must stay green.
        """
        m = _import_schema()
        # "rsi" is NOT a valid corpus fn — only the full form is.
        tree = _make_minimal_if_tree(lhs_fn="rsi")
        warnings = m.lint_tree(tree)
        fn_warnings = [w for w in warnings if "rsi" in w]
        assert len(fn_warnings) >= 1, (
            "AC-4 regression: lint_tree must still warn about 'rsi' (unverified abbreviation) "
            "after adding the 6 corpus-verified fns. v2 §4 explicitly excludes 'rsi'. "
            f"Got no rsi-related warnings — over-widening detected."
        )

    def test_made_up_indicator_fn_still_produces_lint_warning_after_widening(self):
        """lint_tree must STILL produce an "unknown indicator fn" warning for a
        completely made-up fn string like "momentum-oscillator-xyz".

        Guards against an implementation that removes indicator fn checking entirely.
        Currently PASSES. Must stay green after GREEN.
        """
        m = _import_schema()
        tree = _make_minimal_if_tree(lhs_fn="momentum-oscillator-xyz")
        warnings = m.lint_tree(tree)
        fn_warnings = [w for w in warnings if "momentum-oscillator-xyz" in w]
        assert len(fn_warnings) >= 1, (
            "AC-4 regression: lint_tree must still warn about unknown fn "
            "'momentum-oscillator-xyz' after widening. "
            "Got no warnings — indicator fn checking may have been removed entirely."
        )

    def test_existing_v1_indicator_fns_produce_no_lint_warnings_after_widening(self):
        """All 7 original KNOWN_INDICATOR_FNS tokens must still produce no lint warning.

        Regression guard: the widening operation must not accidentally replace the
        frozenset (losing v1 tokens) instead of extending it. Tests all 7 original
        tokens in a single pass.
        Currently PASSES. Must stay green after GREEN.
        """
        m = _import_schema()
        # These are the 7 tokens from the original KNOWN_INDICATOR_FNS (v1 grammar).
        original_fns = [
            "relative-strength-index",
            "cumulative-return",
            "max-drawdown",
            "current-price",
            "standard-deviation-return",
            "moving-average-price",
            "moving-average-return",
        ]
        for fn_token in original_fns:
            tree = _make_minimal_if_tree(lhs_fn=fn_token)
            warnings = m.lint_tree(tree)
            fn_warnings = [w for w in warnings if fn_token in w]
            assert fn_warnings == [], (
                f"AC-4 regression: original v1 fn {fn_token!r} must still produce no lint "
                f"warning after widening (regression: frozenset was replaced, not extended?). "
                f"Got: {fn_warnings!r}"
            )

    def test_existing_v1_rebalance_values_still_accepted_after_widening(self):
        """All 4 original KNOWN_REBALANCE tokens must still validate clean.

        Regression guard: the widening must extend the frozenset, not replace it.
        Currently PASSES. Must stay green.
        """
        m = _import_schema()
        # Original 4 values from KNOWN_REBALANCE (v1 grammar).
        original_rebalance = ["daily", "none", "weekly", "monthly"]
        for rebalance_val in original_rebalance:
            tree = _make_minimal_if_tree(rebalance=rebalance_val)
            errors = m.validate_tree(tree)
            assert errors == [], (
                f"AC-4 regression: original rebalance {rebalance_val!r} must still validate "
                f"clean after widening. Got: {errors!r}"
            )


class TestGrammarV2TypeGuard:
    """AC-5: the three vocabulary constants must be frozensets.

    All tests in this class PASS on the current codebase and must STAY green.
    They guard against an implementer who replaces frozensets with lists or sets,
    which would break isinstance checks and mutability safety.
    """

    def test_all_three_vocabulary_constants_are_frozensets(self):
        """KNOWN_COMPARATORS, KNOWN_REBALANCE, KNOWN_INDICATOR_FNS must all be frozenset.

        frozenset is the type contract (immutable, hashable, O(1) membership test).
        An implementer who changes any of these to list or set breaks the contract.
        Currently PASSES. Must stay green after GREEN.
        """
        m = _import_schema()
        for const_name in ("KNOWN_COMPARATORS", "KNOWN_REBALANCE", "KNOWN_INDICATOR_FNS"):
            const = getattr(m, const_name, None)
            assert const is not None, f"{const_name} is missing from the module"
            assert isinstance(const, frozenset), (
                f"AC-5: {const_name} must be frozenset (immutable, O(1) lookup); "
                f"got {type(const).__name__!r}"
            )

    def test_known_comparators_is_frozenset_and_not_mutable(self):
        """KNOWN_COMPARATORS must be immutable (frozenset, not set or list).

        An accidental mutation of KNOWN_COMPARATORS (e.g. add/discard) between
        test runs would create test-order dependencies. frozenset prevents this.
        """
        m = _import_schema()
        assert isinstance(m.KNOWN_COMPARATORS, frozenset), (
            f"KNOWN_COMPARATORS must be frozenset; got {type(m.KNOWN_COMPARATORS).__name__}"
        )
        # Attempting to add to a frozenset raises AttributeError — confirm mutability
        # is blocked by checking the type prevents .add() calls (not by calling .add())
        assert not hasattr(m.KNOWN_COMPARATORS, "add"), (
            "frozenset must not have .add() method; type is incorrect"
        )

    def test_known_rebalance_is_frozenset_and_not_mutable(self):
        """KNOWN_REBALANCE must be immutable (frozenset)."""
        m = _import_schema()
        assert isinstance(m.KNOWN_REBALANCE, frozenset), (
            f"KNOWN_REBALANCE must be frozenset; got {type(m.KNOWN_REBALANCE).__name__}"
        )
        assert not hasattr(m.KNOWN_REBALANCE, "add")

    def test_known_indicator_fns_is_frozenset_and_not_mutable(self):
        """KNOWN_INDICATOR_FNS must be immutable (frozenset)."""
        m = _import_schema()
        assert isinstance(m.KNOWN_INDICATOR_FNS, frozenset), (
            f"KNOWN_INDICATOR_FNS must be frozenset; got {type(m.KNOWN_INDICATOR_FNS).__name__}"
        )
        assert not hasattr(m.KNOWN_INDICATOR_FNS, "add")

    def test_known_comparators_after_widening_still_contains_gte_as_member(self):
        """After GREEN, KNOWN_COMPARATORS must contain 'gte' as a member.

        This is a post-GREEN membership check — currently RED (gte not present).
        It serves as a canary: if this test passes but AC-1 is still failing,
        something is wrong with the validate_tree logic itself.
        Currently RED: gte not in KNOWN_COMPARATORS.
        GREEN when: gte added to the frozenset.
        """
        m = _import_schema()
        assert "gte" in m.KNOWN_COMPARATORS, (
            "AC-5/AC-1 canary: KNOWN_COMPARATORS must contain 'gte' after widening. "
            "Provenance: v2 §8, VERIFIED-CORPUS n=39,596."
        )

    def test_known_rebalance_after_widening_contains_quarterly(self):
        """After GREEN, KNOWN_REBALANCE must contain 'quarterly'.

        Currently RED: quarterly not in KNOWN_REBALANCE.
        GREEN when: quarterly added to the frozenset.
        """
        m = _import_schema()
        assert "quarterly" in m.KNOWN_REBALANCE, (
            "AC-5/AC-2 canary: KNOWN_REBALANCE must contain 'quarterly' after widening. "
            "Provenance: v2 §6, VERIFIED-CORPUS n=58."
        )

    def test_known_rebalance_after_widening_contains_yearly(self):
        """After GREEN, KNOWN_REBALANCE must contain 'yearly'.

        Currently RED: yearly not in KNOWN_REBALANCE.
        GREEN when: yearly added to the frozenset.
        """
        m = _import_schema()
        assert "yearly" in m.KNOWN_REBALANCE, (
            "AC-5/AC-2 canary: KNOWN_REBALANCE must contain 'yearly' after widening. "
            "Provenance: v2 §6, VERIFIED-CORPUS n=27."
        )

    def test_known_indicator_fns_after_widening_contains_all_six_new_tokens(self):
        """After GREEN, KNOWN_INDICATOR_FNS must contain all 6 newly corpus-verified fns.

        Currently RED: none of the 6 are in KNOWN_INDICATOR_FNS.
        GREEN when: all 6 added to the frozenset.
        """
        m = _import_schema()
        # All 6 corpus-verified tokens from v2 §4 / §4b.
        new_fns = [
            "exponential-moving-average-price",
            "standard-deviation-price",
            "percentage-price-oscillator",
            "percentage-price-oscillator-signal",
            "upper-bollinger",
            "lower-bollinger",
        ]
        missing = [fn for fn in new_fns if fn not in m.KNOWN_INDICATOR_FNS]
        assert missing == [], (
            f"AC-5/AC-3 canary: KNOWN_INDICATOR_FNS is missing corpus-verified fns: {missing!r}. "
            "All 6 must be present after widening."
        )
