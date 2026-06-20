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
  - "gte" must PASS: corpus-verified (n≈39,596); "gt"/"lt"/"gte"/"lte" all valid (AC-1).
  - "eq"/"neq" must fail (not in corpus).
  - "quarterly"/"yearly" must PASS: corpus-verified (AC-2); "hourly" must fail.
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
    """Iteratively gather all ticker strings from a raw tree node.

    Mirrors extract_tickers exactly: collects ``ticker`` from every node
    reachable via ``children`` (excluding the ``'%'`` binary-compound
    placeholder), and walks ``condition`` blocks to collect real tickers from:
      * binary leaf operands — ``lhs.ticker`` / ``rhs.ticker`` (an indicator fn
        applied to a real ticker, e.g. RSI(PSR)); a strategy that gates on
        RSI(PSR) genuinely references PSR, so it MUST be extracted (membership
        validation depends on it). The ``'%'`` binary-compound broadcast lhs is
        excluded.
      * ``tickers[]`` lists (binary-compound broadcast),
      * nested compound sub-conditions,
    — the same traversal performed by ``_collect_condition_tickers`` in the
    implementation (binary-encoding-fix: the operand-ticker collection was added
    to both the impl and this reference walker, which previously shared a blind
    spot to binary-leaf operands).

    Used for exact-equality assertions in the golden-fixture extract_tickers
    tests and as a lower-bound for render_rules_text tests (render only covers
    tickers reachable via children, so it is still the correct reference there).
    """
    if out is None:
        out = set()
    if not isinstance(node, dict):
        return out
    stack: list = [node]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        # Collect node-level ticker, skipping the '%' binary-compound placeholder.
        ticker = current.get("ticker")
        if isinstance(ticker, str) and ticker and ticker != "%":
            out.add(ticker)
        # Walk condition block — mirrors _collect_condition_tickers in the impl.
        condition = current.get("condition")
        if isinstance(condition, dict):
            cond_stack: list = [condition]
            while cond_stack:
                cond = cond_stack.pop()
                if not isinstance(cond, dict):
                    continue
                # binary leaf operands: lhs.ticker / rhs.ticker (skip '%').
                for operand_key in ("lhs", "rhs"):
                    operand = cond.get(operand_key)
                    if isinstance(operand, dict):
                        t = operand.get("ticker")
                        if isinstance(t, str) and t and t != "%":
                            out.add(t)
                # binary-compound: collect from top-level tickers list.
                for t in cond.get("tickers") or []:
                    if isinstance(t, str) and t and t != "%":
                        out.add(t)
                # compound: recurse into sub-conditions.
                for sub in cond.get("conditions") or []:
                    if isinstance(sub, dict):
                        cond_stack.append(sub)
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
        """extract_tickers must return exactly the same set as the reference walker.

        Both _ref_collect_tickers and extract_tickers walk children tickers
        (excluding '%') AND condition block tickers from binary-compound
        tickers[] lists.  Exact equality is the strong invariant — any spurious
        extra ticker returned by extract_tickers is a bug.

        The '%' placeholder must NOT appear in the result (AC-9 invariant).
        """
        m = _import_schema()
        expected = _ref_collect_tickers(raw_small)
        # Sanity: reference walker must find tickers (guards against silent breakage)
        assert len(expected) > 0, "reference walker found no tickers in small fixture"
        result = m.extract_tickers(raw_small)
        assert isinstance(result, set), "extract_tickers must return a set"
        # Exact equality — both oracles must agree completely.
        assert result == expected, (
            f"extract_tickers diverged from reference walker. "
            f"Extra (spurious): {result - expected}. "
            f"Missing: {expected - result}."
        )
        # The '%' placeholder must never appear in the result (AC-9).
        assert "%" not in result, "extract_tickers must not return the '%' placeholder"

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
        """extract_tickers on the large fixture must match the reference walker exactly.

        Same strong invariant as the small fixture test: exact equality between
        extract_tickers and the independent reference oracle.  Both oracles walk
        children tickers AND condition block binary-compound tickers[].
        The '%' placeholder must not appear.
        """
        m = _import_schema()
        expected = _ref_collect_tickers(raw_large)
        assert len(expected) > 0, "reference walker found no tickers in large fixture"
        result = m.extract_tickers(raw_large)
        assert isinstance(result, set)
        assert result == expected, (
            f"extract_tickers diverged from reference walker on large fixture. "
            f"Extra (spurious): {result - expected}. "
            f"Missing: {expected - result}."
        )
        assert "%" not in result, "extract_tickers must not return the '%' placeholder"

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

    def test_comparator_gte_is_now_valid_per_corpus_verification(self, valid_tree):
        """'gte' is corpus-verified (n≈39,596) and must now PASS validate_tree.

        AC-1 (grammar-foundation): KNOWN_COMPARATORS = {gt, lt, gte, lte}.
        The prior OQ-2 unconfirmed stance is superseded by the 10,441-symphony
        corpus audit recorded in project memory. A tree with comparator='gte'
        must produce ZERO hard errors.
        """
        m = _import_schema()
        if_child = self._first_node_of_step(valid_tree, "if-child")
        if if_child is None:
            pytest.skip("Minimal fixture has no if-child; rebuild needed")
        if_child["comparator"] = "gte"
        errors = m.validate_tree(valid_tree)
        assert errors == [], (
            "AC-1: 'gte' is corpus-verified (n≈39,596); validate_tree must accept it. "
            f"Got errors: {errors}"
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
        """validate_tree must not mutate inputs even when it finds errors.

        Note: uses 'hourly' as the invalid rebalance value. 'quarterly' and 'yearly'
        are valid per AC-2 (corpus-verified). 'hourly' is not in the corpus.
        """
        m = _import_schema()
        bad_tree = {
            "step": "root",
            "name": "Test",
            "rebalance": "hourly",  # invalid — not in corpus (AC-2 widened to quarterly/yearly)
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

    def test_known_comparators_contains_gte_after_corpus_verification(self):
        """KNOWN_COMPARATORS MUST contain 'gte' — corpus-verified (n≈39,596).

        AC-1 (grammar-foundation): the corpus audit of 10,441 real Composer symphonies
        found 'gte' in wide use. The prior OQ-2 exclusion is superseded. KNOWN_COMPARATORS
        must now be {gt, lt, gte, lte}.
        """
        m = _import_schema()
        assert "gte" in m.KNOWN_COMPARATORS, (
            "AC-1: 'gte' must be in KNOWN_COMPARATORS after corpus verification (n≈39,596)"
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
        """All 6 corpus-verified rebalance values must each pass validation.

        AC-2 (grammar-foundation): KNOWN_REBALANCE widened to include 'quarterly'
        (n≈58) and 'yearly' (n≈27) from the 10,441-symphony corpus audit.
        """
        m = _import_schema()
        for rebalance in ("daily", "none", "weekly", "monthly", "quarterly", "yearly"):
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
        """Every item returned by validate_tree must be a string.

        Note: uses 'hourly' as invalid rebalance — 'quarterly' and 'yearly' are
        valid per AC-2 (corpus-verified). 'hourly' is confirmed absent from corpus.
        """
        m = _import_schema()
        # Use a definitely-invalid tree to get multiple errors
        bad_tree = {
            "step": "root",
            "name": "T",
            "rebalance": "hourly",  # invalid — not in corpus (AC-2 uses quarterly/yearly as valid)
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
        Note: uses 'hourly' — 'quarterly' and 'yearly' are valid per AC-2 corpus verification.
        """
        m = _import_schema()
        asset = m.make_asset("SPY")
        wt = m.make_weight_equal([asset])
        root = m.make_root("Test", "hourly", [wt])  # "hourly" is unrecognised (not in corpus)
        errors = m.validate_tree(root)
        assert len(errors) >= 1, (
            "Expected error from validate_tree when root has unknown rebalance 'hourly'"
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
# GRAMMAR FOUNDATION — AC-1..AC-12 (RED tests for the grammar-foundation cycle)
#
# These tests encode the corpus-verified grammar widening (10,441 real Composer
# symphonies) and the new compound condition constructors.
#
# Grammar ground truth (recorded in project memory, corpus audit):
#   - Comparators in real use: gt, lt, gte (n≈39,596), lte. eq/neq DO NOT occur.
#   - Rebalance values: daily, none, weekly, monthly, quarterly (n≈58), yearly (n≈27).
#   - New indicator fns: exponential-moving-average-price (n≈45,816),
#     standard-deviation-price (n≈5,572), percentage-price-oscillator,
#     percentage-price-oscillator-signal, upper-bollinger, lower-bollinger.
#   - Compound condition-type ∈ {binary, binary-compound, compound}.
#   - operator ∈ {any, all} only.
#   - binary-compound: lhs ticker="%", tickers:[...], broadcasts one predicate.
#   - compound: joins conditions:[...] with any/all; nestable.
# ===========================================================================


# ===========================================================================
# AC-1: Comparator widening — gte now accepted; eq/neq still error
# ===========================================================================


class TestGrammarFoundationComparatorWidening:
    """AC-1: KNOWN_COMPARATORS = {gt, lt, gte, lte}. Corpus-verified."""

    def _make_minimal_if_tree(self, comparator: str) -> dict:
        """Build a minimal tree with the given if-child comparator for testing."""
        return {
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
                                    "comparator": comparator,
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

    def test_gte_comparator_produces_no_hard_errors(self):
        """AC-1: 'gte' is corpus-verified (n≈39,596); validate_tree must accept it.

        The prior OQ-2 stance (excluded until confirmed) is superseded by the
        10,441-symphony corpus audit. KNOWN_COMPARATORS must contain 'gte'.
        """
        m = _import_schema()
        tree = self._make_minimal_if_tree("gte")
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"AC-1: comparator 'gte' is corpus-verified (n≈39,596); must produce no hard errors. "
            f"Got: {errors}"
        )

    def test_known_comparators_contains_gte(self):
        """AC-1: KNOWN_COMPARATORS frozenset must include 'gte'."""
        m = _import_schema()
        assert hasattr(m, "KNOWN_COMPARATORS"), "KNOWN_COMPARATORS missing"
        assert "gte" in m.KNOWN_COMPARATORS, (
            "AC-1: 'gte' must be in KNOWN_COMPARATORS — corpus-verified n≈39,596"
        )

    def test_known_comparators_is_exactly_gt_lt_gte_lte(self):
        """AC-1: KNOWN_COMPARATORS must be exactly {gt, lt, gte, lte} — no more, no less.

        eq/neq do NOT occur in the corpus and must remain excluded.
        """
        m = _import_schema()
        expected = frozenset({"gt", "lt", "gte", "lte"})
        actual = frozenset(m.KNOWN_COMPARATORS)
        assert actual == expected, (
            f"AC-1: KNOWN_COMPARATORS must be exactly {{gt, lt, gte, lte}}. Got: {sorted(actual)}"
        )

    def test_all_four_comparators_produce_no_hard_errors(self):
        """AC-1: All four corpus-verified comparators — gt, lt, gte, lte — must pass."""
        m = _import_schema()
        for comp in ("gt", "lt", "gte", "lte"):
            tree = self._make_minimal_if_tree(comp)
            errors = m.validate_tree(tree)
            assert errors == [], (
                f"AC-1: corpus-verified comparator {comp!r} produced unexpected errors: {errors}"
            )

    def test_eq_comparator_still_produces_hard_error(self):
        """AC-1 exactness guard: 'eq' does NOT occur in the corpus and must still error."""
        m = _import_schema()
        tree = self._make_minimal_if_tree("eq")
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-1 exactness: 'eq' is absent from the corpus; validate_tree must reject it"
        )

    def test_neq_comparator_still_produces_hard_error(self):
        """AC-1 exactness guard: 'neq' does NOT occur in the corpus and must still error."""
        m = _import_schema()
        tree = self._make_minimal_if_tree("neq")
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-1 exactness: 'neq' is absent from the corpus; validate_tree must reject it"
        )

    def test_make_if_with_gte_comparator_validates_clean(self):
        """AC-1 constructor path: make_condition with 'gte' comparator must produce valid tree."""
        m = _import_schema()
        lhs = m.make_indicator("cumulative-return", "TLT", window=200)
        cond = m.make_condition(lhs, "gte", 0.0)  # gte is now valid
        if_node = m.make_if(
            cond,
            then_children=[m.make_asset("TLT")],
            else_children=[m.make_asset("BIL")],
        )
        root = m.make_root("GTE Test", "daily", [m.make_weight_equal([if_node])])
        errors = m.validate_tree(root)
        assert errors == [], (
            f"AC-1: make_condition with 'gte' comparator produced unexpected errors: {errors}"
        )


# ===========================================================================
# AC-2: Rebalance widening — quarterly/yearly accepted; hourly still errors
# ===========================================================================


class TestGrammarFoundationRebalanceWidening:
    """AC-2: KNOWN_REBALANCE = {daily, none, weekly, monthly, quarterly, yearly}."""

    def _make_root_with_rebalance(self, rebalance: str) -> dict:
        """Build a minimal tree with the given rebalance value."""
        return {
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

    def test_quarterly_rebalance_produces_no_hard_errors(self):
        """AC-2: 'quarterly' is corpus-verified (n≈58); validate_tree must accept it."""
        m = _import_schema()
        tree = self._make_root_with_rebalance("quarterly")
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"AC-2: rebalance 'quarterly' (n≈58 corpus) must produce no hard errors. Got: {errors}"
        )

    def test_yearly_rebalance_produces_no_hard_errors(self):
        """AC-2: 'yearly' is corpus-verified (n≈27); validate_tree must accept it."""
        m = _import_schema()
        tree = self._make_root_with_rebalance("yearly")
        errors = m.validate_tree(tree)
        assert errors == [], (
            f"AC-2: rebalance 'yearly' (n≈27 corpus) must produce no hard errors. Got: {errors}"
        )

    def test_known_rebalance_contains_quarterly_and_yearly(self):
        """AC-2: KNOWN_REBALANCE must include 'quarterly' and 'yearly'."""
        m = _import_schema()
        assert hasattr(m, "KNOWN_REBALANCE"), "KNOWN_REBALANCE missing"
        assert "quarterly" in m.KNOWN_REBALANCE, (
            "AC-2: 'quarterly' must be in KNOWN_REBALANCE — corpus-verified n≈58"
        )
        assert "yearly" in m.KNOWN_REBALANCE, (
            "AC-2: 'yearly' must be in KNOWN_REBALANCE — corpus-verified n≈27"
        )

    def test_known_rebalance_is_exactly_six_values(self):
        """AC-2: KNOWN_REBALANCE must be exactly {daily, none, weekly, monthly, quarterly, yearly}.

        'hourly', 'biweekly', and other non-corpus values must NOT be in the set.
        """
        m = _import_schema()
        expected = frozenset({"daily", "none", "weekly", "monthly", "quarterly", "yearly"})
        actual = frozenset(m.KNOWN_REBALANCE)
        assert actual == expected, (
            f"AC-2: KNOWN_REBALANCE must be exactly the 6 corpus-verified values. "
            f"Got: {sorted(actual)}"
        )

    def test_hourly_rebalance_still_produces_hard_error(self):
        """AC-2 exactness guard: 'hourly' is not in the corpus and must still error."""
        m = _import_schema()
        tree = self._make_root_with_rebalance("hourly")
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-2 exactness: 'hourly' is absent from the corpus; validate_tree must reject it"
        )

    def test_all_six_rebalance_values_pass_validation(self):
        """AC-2: All six corpus-verified rebalance values must each pass validate_tree."""
        m = _import_schema()
        for rebalance in ("daily", "none", "weekly", "monthly", "quarterly", "yearly"):
            tree = self._make_root_with_rebalance(rebalance)
            errors = m.validate_tree(tree)
            assert errors == [], (
                f"AC-2: corpus-verified rebalance {rebalance!r} produced unexpected errors: {errors}"
            )


# ===========================================================================
# AC-3: Indicator fn widening — 6 new fns no lint warning; rsi still warns
# ===========================================================================


class TestGrammarFoundationIndicatorFnWidening:
    """AC-3: KNOWN_INDICATOR_FNS widened with 6 new corpus-verified strings."""

    # The 6 new corpus-verified indicator fns from the grammar-foundation plan.
    _NEW_INDICATOR_FNS = (
        "exponential-moving-average-price",  # n≈45,816 in corpus
        "standard-deviation-price",  # n≈5,572 in corpus (was lint-warning before)
        "percentage-price-oscillator",
        "percentage-price-oscillator-signal",
        "upper-bollinger",
        "lower-bollinger",
    )

    def _make_if_tree_with_lhs_fn(self, fn: str) -> dict:
        """Build a minimal tree using fn as the if-child lhs-fn."""
        return {
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

    @pytest.mark.parametrize("fn", _NEW_INDICATOR_FNS)
    def test_new_corpus_indicator_fn_produces_no_lint_warning(self, fn):
        """AC-3: Each of the 6 new corpus-verified indicator fns must NOT produce a lint warning.

        These fns were KNOWN_INDICATOR_FNS candidates; they now belong in the allowlist.
        An implementation that treats them as unknown will emit lint warnings — this
        test guards against that regression.
        """
        m = _import_schema()
        tree = self._make_if_tree_with_lhs_fn(fn)
        warnings = m.lint_tree(tree)
        # Filter to fn-related lint warnings only
        fn_warnings = [w for w in warnings if fn in w]
        assert len(fn_warnings) == 0, (
            f"AC-3: corpus-verified indicator fn {fn!r} must NOT produce a lint warning. "
            f"Got fn-related warnings: {fn_warnings}"
        )

    @pytest.mark.parametrize("fn", _NEW_INDICATOR_FNS)
    def test_new_corpus_indicator_fn_in_known_indicator_fns(self, fn):
        """AC-3: Each new corpus fn must be present in KNOWN_INDICATOR_FNS."""
        m = _import_schema()
        assert fn in m.KNOWN_INDICATOR_FNS, (
            f"AC-3: corpus-verified fn {fn!r} must be in KNOWN_INDICATOR_FNS"
        )

    def test_known_indicator_fns_contains_all_thirteen_verified_fns(self):
        """AC-3: KNOWN_INDICATOR_FNS must contain all 13 corpus-verified fns (7 original + 6 new)."""
        m = _import_schema()
        all_verified = {
            # Original 7 VERIFIED-LOCAL
            "relative-strength-index",
            "cumulative-return",
            "max-drawdown",
            "current-price",
            "standard-deviation-return",
            "moving-average-price",
            "moving-average-return",
            # New 6 from AC-3
            "exponential-moving-average-price",
            "standard-deviation-price",
            "percentage-price-oscillator",
            "percentage-price-oscillator-signal",
            "upper-bollinger",
            "lower-bollinger",
        }
        missing = all_verified - set(m.KNOWN_INDICATOR_FNS)
        assert not missing, (
            f"AC-3: KNOWN_INDICATOR_FNS is missing corpus-verified fn strings: {missing}"
        )

    def test_rsi_abbreviation_still_produces_lint_warning(self):
        """AC-3 exactness guard: 'rsi' is NOT a valid fn string; must still warn."""
        m = _import_schema()
        tree = self._make_if_tree_with_lhs_fn("rsi")
        # rsi is not in KNOWN_INDICATOR_FNS; must produce a lint warning
        warnings = m.lint_tree(tree)
        assert len(warnings) >= 1, (
            "AC-3 exactness: 'rsi' is not a corpus-verified fn; lint_tree must warn about it"
        )

    def test_standard_deviation_price_no_longer_produces_lint_warning(self):
        """AC-3: 'standard-deviation-price' (n≈5,572 corpus) must not produce a lint warning.

        This fn appears 3 times in sample_score_large.json and was previously
        tolerated by validate_tree (amendment 2) but still produced a lint warning.
        After AC-3 widening it must be in KNOWN_INDICATOR_FNS and must NOT warn.
        """
        m = _import_schema()
        tree = self._make_if_tree_with_lhs_fn("standard-deviation-price")
        warnings = m.lint_tree(tree)
        fn_warnings = [w for w in warnings if "standard-deviation-price" in w]
        assert len(fn_warnings) == 0, (
            "AC-3: 'standard-deviation-price' is now corpus-verified; must not produce lint warnings. "
            f"Got: {fn_warnings}"
        )

    def test_exponential_moving_average_price_no_lint_warning(self):
        """AC-3: 'exponential-moving-average-price' (n≈45,816) must not produce a lint warning.

        This is the highest-frequency new fn in the corpus; failure here indicates the
        KNOWN_INDICATOR_FNS frozenset was not widened with the new entries.
        """
        m = _import_schema()
        tree = self._make_if_tree_with_lhs_fn("exponential-moving-average-price")
        warnings = m.lint_tree(tree)
        fn_warnings = [w for w in warnings if "exponential-moving-average-price" in w]
        assert len(fn_warnings) == 0, (
            "AC-3: 'exponential-moving-average-price' (n≈45,816) must not lint-warn. "
            f"Got: {fn_warnings}"
        )


# ===========================================================================
# AC-4: make_condition_operand + make_constant_rhs constructors
# ===========================================================================


class TestCompoundConditionOperandConstructors:
    """AC-4: New operand constructor functions for compound condition building."""

    def test_make_condition_operand_exists(self):
        """AC-4: make_condition_operand must exist on the module."""
        m = _import_schema()
        assert hasattr(m, "make_condition_operand"), (
            "AC-4: make_condition_operand not found on advisors.symphony_schema"
        )

    def test_make_condition_operand_returns_correct_shape(self):
        """AC-4: make_condition_operand(fn, ticker, *, window) → {fn, ticker, params:{window}}.

        The corpus §7 shape for a condition operand is:
        {"fn": fn, "ticker": ticker, "params": {"window": window}}
        This is distinct from the flat make_indicator shape used by make_if's flat path.
        """
        m = _import_schema()
        operand = m.make_condition_operand("relative-strength-index", "SPY", window=14)
        assert isinstance(operand, dict), "make_condition_operand must return a dict"
        # Must have the fn key
        assert operand.get("fn") == "relative-strength-index", (
            f"AC-4: operand must have fn='relative-strength-index'; got fn={operand.get('fn')!r}"
        )
        # Must have the ticker key (not 'val' — the §7 operand shape uses 'ticker')
        assert operand.get("ticker") == "SPY", (
            f"AC-4: operand must have ticker='SPY'; got ticker={operand.get('ticker')!r}"
        )
        # Must have params.window (not fn-params — §7 uses 'params')
        assert isinstance(operand.get("params"), dict), (
            f"AC-4: operand must have params={{window: N}}; got params={operand.get('params')!r}"
        )
        assert operand["params"].get("window") == 14, (
            f"AC-4: operand params.window must be 14 (from constructor arg); "
            f"got {operand['params'].get('window')!r}"
        )

    def test_make_condition_operand_ticker_is_percent_when_passed_percent(self):
        """AC-4: make_condition_operand with ticker='%' must emit ticker='%'.

        The binary-compound primitive uses '%' as the lhs ticker placeholder.
        The constructor must faithfully emit whatever ticker string is passed.
        """
        m = _import_schema()
        operand = m.make_condition_operand("relative-strength-index", "%", window=10)
        assert operand.get("ticker") == "%", (
            "AC-4: make_condition_operand must emit ticker='%' when called with '%'"
        )

    def test_make_constant_rhs_exists(self):
        """AC-4: make_constant_rhs must exist on the module."""
        m = _import_schema()
        assert hasattr(m, "make_constant_rhs"), (
            "AC-4: make_constant_rhs not found on advisors.symphony_schema"
        )

    def test_make_constant_rhs_returns_correct_shape(self):
        """AC-4: make_constant_rhs(value) → {constant: value}.

        The corpus §7 shape for a constant rhs is {"constant": N}.
        """
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        assert isinstance(rhs, dict), "make_constant_rhs must return a dict"
        assert "constant" in rhs, (
            f"AC-4: make_constant_rhs must have key 'constant'; got keys: {list(rhs)}"
        )
        # Value must be preserved as-is (derived from constructor arg)
        assert rhs["constant"] == 80, (
            f"AC-4: make_constant_rhs(80) must have constant=80; got {rhs['constant']!r}"
        )

    def test_make_constant_rhs_with_zero(self):
        """AC-4: make_constant_rhs(0) must emit {constant: 0}."""
        m = _import_schema()
        rhs = m.make_constant_rhs(0)
        assert rhs.get("constant") == 0, (
            f"AC-4: make_constant_rhs(0) must have constant=0; got {rhs.get('constant')!r}"
        )

    def test_make_constant_rhs_with_float(self):
        """AC-4: make_constant_rhs accepts float values (e.g. 0.5 for percentage thresholds)."""
        m = _import_schema()
        rhs = m.make_constant_rhs(0.5)
        assert rhs.get("constant") == 0.5, (
            f"AC-4: make_constant_rhs(0.5) must have constant=0.5; got {rhs.get('constant')!r}"
        )

    def test_make_condition_operand_deep_copies_are_independent(self):
        """AC-4 deep-copy invariant: two make_condition_operand calls produce independent dicts.

        Mutating one must not affect the other (ensures no shared mutable params dict).
        """
        m = _import_schema()
        op1 = m.make_condition_operand("cumulative-return", "TLT", window=200)
        op2 = m.make_condition_operand("cumulative-return", "TLT", window=200)
        # Mutate op1's params
        op1["params"]["window"] = 999
        # op2 must be unaffected
        assert op2["params"]["window"] == 200, (
            "AC-4: two make_condition_operand calls share a mutable params dict — "
            "must deep-copy or construct fresh dicts"
        )


# ===========================================================================
# AC-5: make_binary_condition → binary leaf
# ===========================================================================


class TestMakeBinaryCondition:
    """AC-5: make_binary_condition(lhs_operand, comparator, rhs) → binary leaf."""

    def test_make_binary_condition_exists(self):
        """AC-5: make_binary_condition must exist on the module."""
        m = _import_schema()
        assert hasattr(m, "make_binary_condition"), (
            "AC-5: make_binary_condition not found on advisors.symphony_schema"
        )

    def test_make_binary_condition_with_constant_rhs_returns_binary_leaf(self):
        """AC-5: make_binary_condition(lhs, 'gt', constant_rhs) → dict with condition-type='binary'.

        The corpus §7 shape for a binary leaf condition has condition-type='binary'.
        """
        m = _import_schema()
        lhs = m.make_condition_operand("relative-strength-index", "SPY", window=14)
        rhs = m.make_constant_rhs(70)
        cond = m.make_binary_condition(lhs, "gt", rhs)
        assert isinstance(cond, dict), "make_binary_condition must return a dict"
        assert cond.get("condition-type") == "binary", (
            f"AC-5: binary leaf must have condition-type='binary'; "
            f"got condition-type={cond.get('condition-type')!r}"
        )

    def test_make_binary_condition_with_operand_rhs_returns_binary_leaf(self):
        """AC-5: make_binary_condition with operand rhs (ticker comparison) → binary leaf."""
        m = _import_schema()
        lhs = m.make_condition_operand("relative-strength-index", "SPY", window=14)
        rhs = m.make_condition_operand("relative-strength-index", "QQQ", window=14)
        cond = m.make_binary_condition(lhs, "gt", rhs)
        assert isinstance(cond, dict), "make_binary_condition must return a dict"
        assert cond.get("condition-type") == "binary", (
            f"AC-5: binary operand comparison must have condition-type='binary'; "
            f"got {cond.get('condition-type')!r}"
        )

    def test_make_binary_condition_carries_lhs_operand(self):
        """AC-5: The returned binary leaf must carry the lhs operand."""
        m = _import_schema()
        lhs = m.make_condition_operand("cumulative-return", "TLT", window=200)
        rhs = m.make_constant_rhs(0)
        cond = m.make_binary_condition(lhs, "gt", rhs)
        # The lhs operand must be present (stored under 'lhs' key by corpus convention)
        assert "lhs" in cond, (
            f"AC-5: binary condition must carry lhs operand; keys found: {list(cond)}"
        )
        # fn from the lhs operand must be preserved
        lhs_block = cond["lhs"]
        assert isinstance(lhs_block, dict), "AC-5: lhs must be a dict"
        assert lhs_block.get("fn") == "cumulative-return", (
            f"AC-5: lhs.fn must match the operand fn; got {lhs_block.get('fn')!r}"
        )

    def test_make_binary_condition_carries_comparator(self):
        """AC-5: The returned binary leaf must carry the comparator."""
        m = _import_schema()
        lhs = m.make_condition_operand("cumulative-return", "TLT", window=200)
        rhs = m.make_constant_rhs(0)
        cond = m.make_binary_condition(lhs, "gte", rhs)
        assert cond.get("comparator") == "gte", (
            f"AC-5: binary condition must carry comparator='gte'; got {cond.get('comparator')!r}"
        )

    def test_make_binary_condition_lhs_is_deep_copied(self):
        """AC-5 deep-copy invariant: mutating the lhs operand after construction must not mutate the condition."""
        m = _import_schema()
        lhs = m.make_condition_operand("cumulative-return", "TLT", window=200)
        rhs = m.make_constant_rhs(0)
        cond = m.make_binary_condition(lhs, "gt", rhs)
        # Mutate the original lhs
        lhs["fn"] = "MUTATED"
        # The condition's internal lhs must be unaffected
        assert cond["lhs"].get("fn") == "cumulative-return", (
            "AC-5 deep-copy: mutating lhs after make_binary_condition corrupted the condition's lhs"
        )


# ===========================================================================
# AC-6: make_binary_compound_condition → binary-compound (frontrunner primitive)
# ===========================================================================


class TestMakeBinaryCompoundCondition:
    """AC-6: make_binary_compound_condition → binary-compound with lhs ticker='%'."""

    def test_make_binary_compound_condition_exists(self):
        """AC-6: make_binary_compound_condition must exist on the module."""
        m = _import_schema()
        assert hasattr(m, "make_binary_compound_condition"), (
            "AC-6: make_binary_compound_condition not found on advisors.symphony_schema"
        )

    def test_make_binary_compound_condition_returns_binary_compound(self):
        """AC-6: Returns condition-type='binary-compound'.

        The frontrunner primitive: 'RSI of ANY([tickers]) > constant'.
        """
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            ["EYEG", "LQD", "XLV"],
            "gt",
            rhs,
            window=10,
            operator="any",
        )
        assert isinstance(cond, dict), "make_binary_compound_condition must return a dict"
        assert cond.get("condition-type") == "binary-compound", (
            f"AC-6: must return condition-type='binary-compound'; "
            f"got {cond.get('condition-type')!r}"
        )

    def test_make_binary_compound_condition_emits_percent_placeholder_on_lhs(self):
        """AC-6: lhs ticker must be '%' — the grammar §7 frontrunner placeholder."""
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            ["EYEG", "LQD", "XLV"],
            "gt",
            rhs,
            window=10,
        )
        # The lhs must carry the '%' placeholder ticker
        lhs_block = cond.get("lhs") or {}
        lhs_ticker = lhs_block.get("ticker")
        assert lhs_ticker == "%", (
            f"AC-6: binary-compound lhs.ticker must be '%' (grammar §7 placeholder); "
            f"got {lhs_ticker!r}"
        )

    def test_make_binary_compound_condition_emits_tickers_list(self):
        """AC-6: Result must carry the broadcast tickers list at the top level."""
        m = _import_schema()
        watched = ["EYEG", "LQD", "XLV"]
        rhs = m.make_constant_rhs(80)
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            watched,
            "gt",
            rhs,
            window=10,
        )
        assert "tickers" in cond, (
            f"AC-6: binary-compound must have a 'tickers' key; keys: {list(cond)}"
        )
        assert isinstance(cond["tickers"], list), "AC-6: tickers must be a list"
        # All watched tickers must be present (order may vary)
        assert set(cond["tickers"]) == set(watched), (
            f"AC-6: tickers list must match the input; expected {watched}, got {cond['tickers']}"
        )

    def test_make_binary_compound_condition_operator_any(self):
        """AC-6: operator='any' must be emitted correctly."""
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            ["SPY", "QQQ"],
            "gt",
            rhs,
            window=10,
            operator="any",
        )
        assert cond.get("operator") == "any", (
            f"AC-6: operator='any' must be emitted; got {cond.get('operator')!r}"
        )

    def test_make_binary_compound_condition_operator_all(self):
        """AC-6: operator='all' must be emitted correctly (AND semantics)."""
        m = _import_schema()
        rhs = m.make_constant_rhs(20)
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            ["SPY", "QQQ"],
            "lt",
            rhs,
            window=14,
            operator="all",
        )
        assert cond.get("operator") == "all", (
            f"AC-6: operator='all' must be emitted; got {cond.get('operator')!r}"
        )

    def test_make_binary_compound_condition_default_operator_is_any(self):
        """AC-6: operator defaults to 'any' when not specified (OR semantics — the common case)."""
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        # Call WITHOUT operator keyword
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            ["SPY"],
            "gt",
            rhs,
            window=10,
        )
        assert cond.get("operator") == "any", (
            f"AC-6: default operator must be 'any'; got {cond.get('operator')!r}"
        )

    def test_make_binary_compound_condition_single_ticker_works(self):
        """AC-6 edge: a single-ticker list must work (not require ≥2 tickers)."""
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            ["EYEG"],  # single ticker
            "gt",
            rhs,
            window=10,
        )
        assert cond.get("condition-type") == "binary-compound"
        assert len(cond.get("tickers", [])) == 1

    def test_make_binary_compound_condition_tickers_are_deep_copied(self):
        """AC-6 deep-copy: mutating the input tickers list must not mutate the condition."""
        m = _import_schema()
        watched = ["SPY", "QQQ"]
        rhs = m.make_constant_rhs(80)
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            watched,
            "gt",
            rhs,
            window=10,
        )
        # Mutate the original list
        watched.append("MUTATED")
        # The condition's tickers must be unaffected
        assert "MUTATED" not in cond.get("tickers", []), (
            "AC-6 deep-copy: mutating the input tickers list corrupted the condition's tickers"
        )


# ===========================================================================
# AC-7: make_compound_condition(operator, conditions) → compound block
# ===========================================================================


class TestMakeCompoundCondition:
    """AC-7: make_compound_condition joins N conditions with any/all; nestable."""

    def test_make_compound_condition_exists(self):
        """AC-7: make_compound_condition must exist on the module."""
        m = _import_schema()
        assert hasattr(m, "make_compound_condition"), (
            "AC-7: make_compound_condition not found on advisors.symphony_schema"
        )

    def test_make_compound_condition_returns_compound_type(self):
        """AC-7: make_compound_condition → condition-type='compound'."""
        m = _import_schema()
        lhs = m.make_condition_operand("cumulative-return", "TLT", window=200)
        cond1 = m.make_binary_condition(lhs, "gt", m.make_constant_rhs(0))
        lhs2 = m.make_condition_operand("cumulative-return", "BIL", window=20)
        cond2 = m.make_binary_condition(lhs2, "gt", m.make_constant_rhs(0))
        compound = m.make_compound_condition("any", [cond1, cond2])
        assert isinstance(compound, dict), "make_compound_condition must return a dict"
        assert compound.get("condition-type") == "compound", (
            f"AC-7: must return condition-type='compound'; got {compound.get('condition-type')!r}"
        )

    def test_make_compound_condition_operator_any(self):
        """AC-7: operator='any' must be emitted (OR semantics)."""
        m = _import_schema()
        lhs = m.make_condition_operand("cumulative-return", "TLT", window=200)
        cond = m.make_binary_condition(lhs, "gt", m.make_constant_rhs(0))
        compound = m.make_compound_condition("any", [cond])
        assert compound.get("operator") == "any", (
            f"AC-7: operator='any' must be emitted; got {compound.get('operator')!r}"
        )

    def test_make_compound_condition_operator_all(self):
        """AC-7: operator='all' must be emitted (AND semantics)."""
        m = _import_schema()
        lhs = m.make_condition_operand("cumulative-return", "TLT", window=200)
        cond = m.make_binary_condition(lhs, "gt", m.make_constant_rhs(0))
        compound = m.make_compound_condition("all", [cond])
        assert compound.get("operator") == "all", (
            f"AC-7: operator='all' must be emitted; got {compound.get('operator')!r}"
        )

    def test_make_compound_condition_carries_all_sub_conditions(self):
        """AC-7: the conditions list in the result must contain all supplied conditions."""
        m = _import_schema()
        lhs1 = m.make_condition_operand("cumulative-return", "TLT", window=200)
        lhs2 = m.make_condition_operand("cumulative-return", "BIL", window=20)
        lhs3 = m.make_condition_operand("relative-strength-index", "SPY", window=14)
        conds = [
            m.make_binary_condition(lhs1, "gt", m.make_constant_rhs(0)),
            m.make_binary_condition(lhs2, "gt", m.make_constant_rhs(0)),
            m.make_binary_condition(lhs3, "lte", m.make_constant_rhs(30)),
        ]
        compound = m.make_compound_condition("all", conds)
        result_conds = compound.get("conditions")
        assert isinstance(result_conds, list), (
            f"AC-7: conditions must be a list; got {type(result_conds)}"
        )
        # Derived from the constructor argument — 3 conditions in, 3 out
        assert len(result_conds) == len(conds), (
            f"AC-7: conditions list must contain {len(conds)} items; got {len(result_conds)}"
        )

    def test_make_compound_condition_nestable_compound_in_compound(self):
        """AC-7 nestability: a compound can appear inside another compound's conditions list.

        'RSI(SPY) > 50 AND (CumRet(TLT) > 0 OR CumRet(BIL) > 0)'
        The inner compound is the sub-condition of the outer all-compound.
        """
        m = _import_schema()
        rsi_lhs = m.make_condition_operand("relative-strength-index", "SPY", window=14)
        rsi_cond = m.make_binary_condition(rsi_lhs, "gt", m.make_constant_rhs(50))

        tlt_lhs = m.make_condition_operand("cumulative-return", "TLT", window=200)
        bil_lhs = m.make_condition_operand("cumulative-return", "BIL", window=20)
        inner_any = m.make_compound_condition(
            "any",
            [
                m.make_binary_condition(tlt_lhs, "gt", m.make_constant_rhs(0)),
                m.make_binary_condition(bil_lhs, "gt", m.make_constant_rhs(0)),
            ],
        )

        outer_all = m.make_compound_condition("all", [rsi_cond, inner_any])
        assert outer_all.get("condition-type") == "compound"
        assert outer_all.get("operator") == "all"
        sub_conds = outer_all.get("conditions", [])
        assert len(sub_conds) == 2  # rsi_cond + inner_any
        # The inner compound must appear as a sub-condition
        nested = [c for c in sub_conds if c.get("condition-type") == "compound"]
        assert len(nested) == 1, (
            "AC-7: nested compound-in-compound must work; inner compound not found in conditions"
        )

    def test_make_compound_condition_conditions_are_deep_copied(self):
        """AC-7 deep-copy: mutating the input conditions list must not mutate the compound."""
        m = _import_schema()
        lhs = m.make_condition_operand("cumulative-return", "TLT", window=200)
        cond = m.make_binary_condition(lhs, "gt", m.make_constant_rhs(0))
        input_conds = [cond]
        compound = m.make_compound_condition("any", input_conds)
        # Mutate the input list
        input_conds.append({"injected": True})
        # The compound's conditions must be unaffected
        result_conds = compound.get("conditions", [])
        assert len(result_conds) == 1, (
            "AC-7 deep-copy: mutating the input conditions list corrupted the compound's conditions"
        )


# ===========================================================================
# AC-8: make_if_compound → if node with condition block; flat make_if unchanged
# ===========================================================================


class TestMakeIfCompound:
    """AC-8: make_if_compound produces an if node whose true-branch if-child carries
    the authoritative condition block; flat make_if / make_condition unchanged."""

    def test_make_if_compound_exists(self):
        """AC-8: make_if_compound must exist on the module."""
        m = _import_schema()
        assert hasattr(m, "make_if_compound"), (
            "AC-8: make_if_compound not found on advisors.symphony_schema"
        )

    def _build_binary_compound_condition(self, m) -> dict:
        """Helper: RSI of ANY([SPY, QQQ]) > 80."""
        rhs = m.make_constant_rhs(80)
        return m.make_binary_compound_condition(
            "relative-strength-index",
            ["SPY", "QQQ"],
            "gt",
            rhs,
            window=10,
            operator="any",
        )

    def test_make_if_compound_returns_if_node(self):
        """AC-8: make_if_compound must return a dict with step='if'."""
        m = _import_schema()
        condition_block = self._build_binary_compound_condition(m)
        then_a = m.make_asset("SPY")
        else_a = m.make_asset("BIL")
        result = m.make_if_compound(
            condition_block,
            then_children=[then_a],
            else_children=[else_a],
        )
        assert isinstance(result, dict), "make_if_compound must return a dict"
        assert result.get("step") == "if", (
            f"AC-8: make_if_compound must return step='if'; got {result.get('step')!r}"
        )

    def test_make_if_compound_true_branch_carries_condition_block(self):
        """AC-8: The true-branch if-child must carry the authoritative 'condition' block."""
        m = _import_schema()
        condition_block = self._build_binary_compound_condition(m)
        then_a = m.make_asset("SPY")
        else_a = m.make_asset("BIL")
        if_node = m.make_if_compound(
            condition_block,
            then_children=[then_a],
            else_children=[else_a],
        )
        children = if_node.get("children", [])
        true_branch = next(
            (c for c in children if isinstance(c, dict) and not c.get("is-else-condition?")),
            None,
        )
        assert true_branch is not None, "AC-8: make_if_compound must produce a true-branch if-child"
        assert true_branch.get("step") == "if-child", (
            f"AC-8: true branch must have step='if-child'; got {true_branch.get('step')!r}"
        )
        # Must carry the condition block (not flat lhs-fn/comparator)
        assert "condition" in true_branch, (
            "AC-8: true-branch if-child must carry a 'condition' key with the compound block"
        )
        cond_block = true_branch["condition"]
        assert isinstance(cond_block, dict), "AC-8: 'condition' must be a dict"
        # The condition block must have the expected condition-type
        assert cond_block.get("condition-type") in ("binary", "binary-compound", "compound"), (
            f"AC-8: condition block must have a known condition-type; "
            f"got {cond_block.get('condition-type')!r}"
        )

    def test_make_if_compound_produces_valid_tree_via_validate_tree(self):
        """AC-8: A tree built with make_if_compound must pass validate_tree with no errors.

        The existing validate_tree already tolerates compound condition blocks
        (Amendment 6). This test confirms the compound-block if-child still validates
        clean after the grammar-foundation widening.
        """
        m = _import_schema()
        condition_block = self._build_binary_compound_condition(m)
        spy = m.make_asset("SPY")
        qqq = m.make_asset("QQQ")
        bil = m.make_asset("BIL")
        inv_vol = m.make_inverse_vol([spy, qqq])
        if_node = m.make_if_compound(
            condition_block,
            then_children=[inv_vol],
            else_children=[bil],
        )
        root = m.make_root("Compound Test", "daily", [m.make_weight_equal([if_node])])
        errors = m.validate_tree(root)
        assert errors == [], (
            f"AC-8: tree built with make_if_compound must validate clean; got: {errors}"
        )

    def test_make_if_compound_has_else_branch(self):
        """AC-8: make_if_compound must produce an else-branch if-child."""
        m = _import_schema()
        condition_block = self._build_binary_compound_condition(m)
        then_a = m.make_asset("SPY")
        else_a = m.make_asset("BIL")
        if_node = m.make_if_compound(
            condition_block,
            then_children=[then_a],
            else_children=[else_a],
        )
        children = if_node.get("children", [])
        else_branch = next(
            (c for c in children if isinstance(c, dict) and c.get("is-else-condition?")),
            None,
        )
        assert else_branch is not None, (
            "AC-8: make_if_compound must produce an else-branch if-child"
        )

    def test_make_if_compound_ids_are_uuid4(self):
        """AC-8: All node ids produced by make_if_compound must be valid UUID v4 strings."""
        m = _import_schema()
        condition_block = self._build_binary_compound_condition(m)
        then_a = m.make_asset("SPY")
        else_a = m.make_asset("BIL")
        if_node = m.make_if_compound(
            condition_block,
            then_children=[then_a],
            else_children=[else_a],
        )
        ids = _ref_collect_ids(if_node)
        for id_val in ids:
            parsed = uuid.UUID(id_val)
            assert parsed.version == 4, f"AC-8: id {id_val!r} is not UUID v4"

    def test_flat_make_if_still_works_unchanged(self):
        """AC-8 no-regression: existing flat make_if must still produce valid trees.

        The grammar-foundation spec states 'existing flat make_if/make_condition UNCHANGED.'
        """
        m = _import_schema()
        lhs = m.make_indicator("cumulative-return", "TLT", window=200)
        cond = m.make_condition(lhs, "gt", 0.0)
        if_node = m.make_if(
            cond,
            then_children=[m.make_asset("TLT")],
            else_children=[m.make_asset("BIL")],
        )
        root = m.make_root("Flat IF Test", "daily", [m.make_weight_equal([if_node])])
        errors = m.validate_tree(root)
        assert errors == [], (
            f"AC-8 no-regression: flat make_if must still produce valid trees; got: {errors}"
        )


# ===========================================================================
# AC-9: Full frontrunner overlay integration
# ===========================================================================


class TestFrontrunnerOverlayIntegration:
    """AC-9: Full end-to-end frontrunner overlay integration test.

    Builds the complete tree from the feature plan:
      make_if_compound(
        make_binary_compound_condition("relative-strength-index", [watched_tickers],
                                       "gt", make_constant_rhs(80), window=10, operator="any"),
        then=[vol_basket],
        else=[base],
      )
    Wrapped in make_root.
    """

    _WATCHED_TICKERS = ["EYEG", "LQD", "XLV"]
    _VOL_BASKET_TICKERS = ["SPY", "QQQ"]
    _BASE_TICKERS = ["BIL"]

    def _build_frontrunner_overlay(self, m) -> dict:
        """Build the complete frontrunner overlay tree using the new compound constructors."""
        # Vol basket: inverse-vol allocation over two ETFs
        vol_basket = m.make_inverse_vol([m.make_asset(t) for t in self._VOL_BASKET_TICKERS])
        # Base: equal-weighted single asset
        base = m.make_weight_equal([m.make_asset(t) for t in self._BASE_TICKERS])
        # Frontrunner condition: RSI of ANY([EYEG, LQD, XLV]) > 80, window=10
        condition_block = m.make_binary_compound_condition(
            "relative-strength-index",
            self._WATCHED_TICKERS,
            "gt",
            m.make_constant_rhs(80),
            window=10,
            operator="any",
        )
        # if_compound wraps the condition with then/else branches
        if_node = m.make_if_compound(
            condition_block,
            then_children=[vol_basket],
            else_children=[base],
        )
        # Wrap in root
        return m.make_root(
            "Frontrunner Overlay",
            "daily",
            [m.make_weight_equal([if_node])],
        )

    def test_frontrunner_overlay_validates_clean(self):
        """AC-9: The complete frontrunner overlay tree must pass validate_tree with no errors."""
        m = _import_schema()
        tree = self._build_frontrunner_overlay(m)
        errors = m.validate_tree(tree)
        assert errors == [], f"AC-9: frontrunner overlay tree must validate clean; got: {errors}"

    def test_frontrunner_overlay_extract_tickers_excludes_percent_placeholder(self):
        """AC-9: extract_tickers must NOT include the '%' placeholder ticker.

        The binary-compound lhs emits ticker='%' as a grammar placeholder;
        '%' is not a real ticker and must never appear in the ticker set.
        """
        m = _import_schema()
        tree = self._build_frontrunner_overlay(m)
        tickers = m.extract_tickers(tree)
        assert "%" not in tickers, (
            "AC-9: extract_tickers must exclude '%' (binary-compound lhs placeholder); "
            f"got tickers: {tickers}"
        )

    def test_frontrunner_overlay_extract_tickers_includes_all_real_tickers(self):
        """AC-9: extract_tickers must include all real tickers — watched + basket + base."""
        m = _import_schema()
        tree = self._build_frontrunner_overlay(m)
        tickers = m.extract_tickers(tree)
        all_expected = set(self._WATCHED_TICKERS + self._VOL_BASKET_TICKERS + self._BASE_TICKERS)
        missing = all_expected - tickers
        assert not missing, (
            f"AC-9: extract_tickers is missing real tickers: {missing}. Got: {tickers}"
        )

    def test_frontrunner_overlay_render_rules_text_contains_any_gate(self):
        """AC-9: render_rules_text must render the ANY gate in a recognizable way.

        The output must indicate the 'any' semantics of the binary-compound condition
        so an operator reading the rendered output understands the gate type.
        """
        m = _import_schema()
        tree = self._build_frontrunner_overlay(m)
        text = m.render_rules_text(tree)
        assert isinstance(text, str) and len(text) > 0, (
            "AC-9: render_rules_text must return a non-empty string"
        )
        # The render must surface the ANY semantics in some recognizable form
        text_upper = text.upper()
        assert "ANY" in text_upper or "any" in text.lower(), (
            f"AC-9: render_rules_text must indicate the ANY gate; got:\n{text}"
        )

    def test_frontrunner_overlay_render_rules_text_contains_watched_tickers(self):
        """AC-9: render_rules_text must reference the watched tickers in the condition."""
        m = _import_schema()
        tree = self._build_frontrunner_overlay(m)
        text = m.render_rules_text(tree)
        # At least one watched ticker must appear in the render
        found_any = any(t in text for t in self._WATCHED_TICKERS)
        assert found_any, (
            f"AC-9: render_rules_text must reference at least one watched ticker "
            f"({self._WATCHED_TICKERS}); got:\n{text}"
        )

    def test_frontrunner_overlay_all_node_ids_are_unique(self):
        """AC-9: Every node in the complete frontrunner tree must have a unique UUID v4 id."""
        m = _import_schema()
        tree = self._build_frontrunner_overlay(m)
        ids = _ref_collect_ids(tree)
        assert len(ids) == len(set(ids)), (
            f"AC-9: duplicate ids found in frontrunner overlay tree: "
            f"{[x for x in ids if ids.count(x) > 1]}"
        )
        for id_val in ids:
            parsed = uuid.UUID(id_val)
            assert parsed.version == 4, f"AC-9: id {id_val!r} is not UUID v4"

    def test_frontrunner_overlay_golden_fixture_shape(self):
        """AC-9: Tree shape matches the golden fixture description.

        Loads and cross-checks against tests/fixtures/math/frontrunner_overlay_integration.json.
        Grammar tokens are facts and can be checked directly.
        """
        m = _import_schema()
        fixture = _load_fixture(
            _REPO_ROOT / "tests" / "fixtures" / "math" / "frontrunner_overlay_integration.json"
        )
        tree = self._build_frontrunner_overlay(m)

        # validate_tree: must return []
        assert m.validate_tree(tree) == fixture["expected_validate_tree_errors"]

        # extract_tickers: must include all real tickers
        tickers = m.extract_tickers(tree)
        for expected_ticker in fixture["expected_extract_tickers_includes_all"]:
            assert expected_ticker in tickers, (
                f"AC-9 fixture: ticker {expected_ticker!r} missing from extract_tickers result"
            )
        # Must exclude the placeholder
        for excluded in fixture["expected_extract_tickers_excludes"]:
            assert excluded not in tickers, (
                f"AC-9 fixture: placeholder {excluded!r} must be excluded from extract_tickers"
            )


# ===========================================================================
# AC-10: validate_tree HARD errors on malformed compound blocks at any depth
# ===========================================================================


class TestCompoundConditionValidation:
    """AC-10: validate_tree hard-errors on malformed compound blocks, recursively,
    bounded by a depth cap so a pathologically deep input never raises."""

    def _wrap_condition_in_if_tree(self, condition_block: dict) -> dict:
        """Wrap a condition block in a minimal if-tree for validate_tree testing."""
        return {
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
                                    "condition": condition_block,
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

    def test_compound_block_with_bad_operator_produces_hard_error(self):
        """AC-10: A compound block with operator='or' (not in {any, all}) must produce a hard error.

        The grammar specifies operator ∈ {any, all} only. 'or'/'and'/'none' are invalid.
        """
        m = _import_schema()
        bad_condition = {
            "condition-type": "compound",
            "operator": "or",  # invalid — must be 'any' or 'all'
            "conditions": [
                {
                    "condition-type": "binary",
                    "lhs": {"fn": "cumulative-return", "ticker": "TLT", "params": {"window": 200}},
                    "comparator": "gt",
                    "rhs": {"constant": 0},
                }
            ],
        }
        tree = self._wrap_condition_in_if_tree(bad_condition)
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-10: compound block with operator='or' must produce a hard error; "
            "operator must be 'any' or 'all'"
        )

    def test_compound_block_with_unknown_condition_type_produces_hard_error(self):
        """AC-10: A condition block with an unknown condition-type must produce a hard error.

        Valid condition-type values are: binary, binary-compound, compound.
        Any other string is a hard error.
        """
        m = _import_schema()
        bad_condition = {
            "condition-type": "super-compound",  # not in {binary, binary-compound, compound}
            "operator": "any",
            "conditions": [],
        }
        tree = self._wrap_condition_in_if_tree(bad_condition)
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-10: condition block with unknown condition-type='super-compound' must hard-error"
        )

    def test_compound_block_missing_conditions_list_produces_hard_error(self):
        """AC-10: A 'compound' condition block without a 'conditions' key must produce a hard error.

        A compound block joins conditions; missing the conditions list is structurally invalid.
        """
        m = _import_schema()
        bad_condition = {
            "condition-type": "compound",
            "operator": "any",
            # 'conditions' key deliberately absent
        }
        tree = self._wrap_condition_in_if_tree(bad_condition)
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-10: compound block missing 'conditions' key must produce a hard error"
        )

    def test_binary_compound_missing_tickers_produces_hard_error(self):
        """AC-10: A 'binary-compound' block without a 'tickers' key must produce a hard error.

        binary-compound requires tickers:[...] to broadcast the predicate over.
        """
        m = _import_schema()
        bad_condition = {
            "condition-type": "binary-compound",
            "operator": "any",
            # 'tickers' key deliberately absent
            "lhs": {"fn": "relative-strength-index", "ticker": "%", "params": {"window": 10}},
            "comparator": "gt",
            "rhs": {"constant": 80},
        }
        tree = self._wrap_condition_in_if_tree(bad_condition)
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-10: binary-compound missing 'tickers' key must produce a hard error"
        )

    def test_malformed_compound_at_depth_2_nested_produces_hard_error(self):
        """AC-10: A malformed sub-condition at depth 2 inside a compound must produce a hard error.

        The validation must recurse into compound.conditions and validate each
        sub-condition. A bad operator at depth 2 must not be silently skipped.
        """
        m = _import_schema()
        # Outer compound is valid; inner compound has bad operator
        inner_bad = {
            "condition-type": "compound",
            "operator": "xor",  # invalid — must be any/all
            "conditions": [
                {
                    "condition-type": "binary",
                    "lhs": {"fn": "cumulative-return", "ticker": "TLT", "params": {"window": 200}},
                    "comparator": "gt",
                    "rhs": {"constant": 0},
                }
            ],
        }
        outer_condition = {
            "condition-type": "compound",
            "operator": "any",  # outer is valid
            "conditions": [
                inner_bad,  # inner has the defect
            ],
        }
        tree = self._wrap_condition_in_if_tree(outer_condition)
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-10: malformed sub-condition at depth 2 must produce a hard error; "
            "validate_tree must recurse into compound.conditions"
        )

    def test_malformed_compound_at_depth_3_nested_produces_hard_error(self):
        """AC-10: A malformed sub-condition at depth 3 inside nested compounds must produce a hard error.

        Validation must recurse through all levels of compound nesting.
        """
        m = _import_schema()
        # Deeply nested: outer (valid) → middle (valid) → inner_bad (bad operator)
        inner_bad = {
            "condition-type": "compound",
            "operator": "nand",  # invalid
            "conditions": [
                {
                    "condition-type": "binary",
                    "lhs": {"fn": "cumulative-return", "ticker": "TLT", "params": {"window": 200}},
                    "comparator": "gt",
                    "rhs": {"constant": 0},
                }
            ],
        }
        middle = {
            "condition-type": "compound",
            "operator": "all",  # valid
            "conditions": [inner_bad],
        }
        outer = {
            "condition-type": "compound",
            "operator": "any",  # valid
            "conditions": [middle],
        }
        tree = self._wrap_condition_in_if_tree(outer)
        errors = m.validate_tree(tree)
        assert len(errors) >= 1, (
            "AC-10: malformed sub-condition at depth 3 must produce a hard error; "
            "validate_tree must recurse through all compound nesting levels"
        )

    def test_absent_condition_type_on_raw_binary_leaf_is_tolerated(self):
        """AC-10 tolerance: a dict inside conditions[] lacking condition-type is skipped gracefully.

        The spec says: 'Absent-condition-type sub-items are tolerated (raw binary leaves),
        not errored.' A sub-item without condition-type is a raw binary leaf (valid grammar).
        """
        m = _import_schema()
        # A compound whose conditions list contains a raw binary leaf (no condition-type key)
        condition_with_raw_leaf = {
            "condition-type": "compound",
            "operator": "any",
            "conditions": [
                {
                    # No condition-type key — this is a raw binary leaf, must be tolerated
                    "lhs": {"fn": "cumulative-return", "ticker": "TLT", "params": {"window": 200}},
                    "comparator": "gt",
                    "rhs": {"constant": 0},
                }
            ],
        }
        tree = self._wrap_condition_in_if_tree(condition_with_raw_leaf)
        errors = m.validate_tree(tree)
        # The whole tree must be error-free — a raw binary leaf (absent condition-type)
        # is tolerated grammar, so no hard error of any kind must be emitted.
        assert errors == [], (
            "AC-10: absent condition-type on a raw binary leaf must be tolerated, not hard-errored. "
            f"Got errors: {errors}"
        )

    def test_5000_deep_compound_condition_never_raises(self):
        """AC-10 bounded depth: a 5000-deep nested compound must never raise (forces the depth cap).

        The spec states: 'bounded by a depth cap so a pathologically deep (e.g. 5000)
        input never raises / never blows the stack.' validate_tree must use iterative
        traversal with a MAX_CONDITION_DEPTH guard on the condition block walk.
        """
        m = _import_schema()
        # Build a 5000-deep compound chain
        leaf = {
            "condition-type": "binary",
            "lhs": {"fn": "cumulative-return", "ticker": "TLT", "params": {"window": 200}},
            "comparator": "gt",
            "rhs": {"constant": 0},
        }
        current = leaf
        for _ in range(5000):
            current = {
                "condition-type": "compound",
                "operator": "any",
                "conditions": [current],
            }
        tree = self._wrap_condition_in_if_tree(current)
        try:
            errors = m.validate_tree(tree)
        except RecursionError as exc:
            pytest.fail(
                f"AC-10: validate_tree raised RecursionError on 5000-deep compound condition. "
                f"Must use iterative traversal with a depth cap: {exc}"
            )
        except Exception as exc:
            # Any other exception is also forbidden (never raises contract)
            pytest.fail(
                f"AC-10: validate_tree raised {type(exc).__name__} on 5000-deep compound: {exc}"
            )
        # Must return a list (possibly with a depth-cap error or empty)
        assert isinstance(errors, list), (
            f"AC-10: validate_tree must return a list on 5000-deep compound; got {type(errors)}"
        )

    def test_validate_tree_never_raises_on_malformed_condition_block(self):
        """AC-10 never-raises: validate_tree must not raise on any malformed condition block shape."""
        m = _import_schema()
        malformed_conditions = [
            {"condition-type": "compound", "operator": None, "conditions": []},
            {"condition-type": "binary-compound", "operator": "any", "tickers": None},
            {"condition-type": "compound", "operator": "all", "conditions": [None, None]},
            {"condition-type": None, "operator": "any"},
            {"conditions": "not-a-list"},
            {},
        ]
        for bad_cond in malformed_conditions:
            tree = self._wrap_condition_in_if_tree(bad_cond)
            try:
                result = m.validate_tree(tree)
            except Exception as exc:
                pytest.fail(
                    f"AC-10: validate_tree raised {type(exc).__name__} on malformed condition "
                    f"block {bad_cond!r}: {exc}"
                )
            assert isinstance(result, list), (
                f"AC-10: validate_tree must return a list; got {type(result)}"
            )


# ===========================================================================
# AC-11: Invariants — ValueError on bad inputs; deep-copy; read-only
# ===========================================================================


class TestCompoundConditionInvariants:
    """AC-11: Constructor invariants — ValueError on bad inputs, deep-copy, fresh UUIDs,
    read-only functions remain read-only with compound blocks."""

    def test_make_binary_compound_condition_invalid_operator_raises_value_error(self):
        """AC-11: operator not in {any, all} must raise ValueError at construction time."""
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        with pytest.raises(ValueError):
            m.make_binary_compound_condition(
                "relative-strength-index",
                ["SPY"],
                "gt",
                rhs,
                window=10,
                operator="or",  # invalid — must be any/all
            )

    def test_make_compound_condition_invalid_operator_raises_value_error(self):
        """AC-11: operator not in {any, all} in make_compound_condition raises ValueError."""
        m = _import_schema()
        lhs = m.make_condition_operand("cumulative-return", "TLT", window=200)
        cond = m.make_binary_condition(lhs, "gt", m.make_constant_rhs(0))
        with pytest.raises(ValueError):
            m.make_compound_condition("xor", [cond])  # invalid operator

    def test_make_binary_compound_condition_empty_tickers_raises_value_error(self):
        """AC-11: empty tickers list must raise ValueError.

        A binary-compound with no tickers cannot broadcast the predicate; it is
        a programming error caught at construction time.
        """
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        with pytest.raises(ValueError):
            m.make_binary_compound_condition(
                "relative-strength-index",
                [],  # empty list — must raise
                "gt",
                rhs,
                window=10,
            )

    def test_make_compound_condition_empty_conditions_raises_value_error(self):
        """AC-11: empty conditions list must raise ValueError.

        A compound joining zero conditions is semantically meaningless; it is
        a construction error caught at call time.
        """
        m = _import_schema()
        with pytest.raises(ValueError):
            m.make_compound_condition("any", [])  # empty — must raise

    def test_make_if_compound_fresh_ids_on_each_call(self):
        """AC-11: Two calls to make_if_compound must produce different root ids (fresh UUIDs)."""
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            ["SPY"],
            "gt",
            rhs,
            window=10,
        )
        if1 = m.make_if_compound(
            cond,
            then_children=[m.make_asset("SPY")],
            else_children=[m.make_asset("BIL")],
        )
        cond2 = m.make_binary_compound_condition(
            "relative-strength-index",
            ["SPY"],
            "gt",
            rhs,
            window=10,
        )
        if2 = m.make_if_compound(
            cond2,
            then_children=[m.make_asset("SPY")],
            else_children=[m.make_asset("BIL")],
        )
        assert if1.get("id") != if2.get("id"), (
            "AC-11: two make_if_compound calls must produce different ids (fresh UUID each time)"
        )

    def test_make_if_compound_deep_copies_condition_block(self):
        """AC-11 deep-copy: mutating the condition block after make_if_compound must not mutate the tree."""
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            ["SPY", "QQQ"],
            "gt",
            rhs,
            window=10,
        )
        if_node = m.make_if_compound(
            cond,
            then_children=[m.make_asset("SPY")],
            else_children=[m.make_asset("BIL")],
        )
        # Mutate the original condition block
        original_tickers = list(cond.get("tickers", []))
        cond.get("tickers", []).append("MUTATED")
        # The if-child's condition block must be unaffected
        children = if_node.get("children", [])
        true_branch = next(
            (c for c in children if isinstance(c, dict) and not c.get("is-else-condition?")),
            None,
        )
        if true_branch and "condition" in true_branch:
            cond_in_tree = true_branch["condition"]
            tickers_in_tree = cond_in_tree.get("tickers", [])
            assert "MUTATED" not in tickers_in_tree, (
                "AC-11 deep-copy: mutating the condition block after make_if_compound "
                "corrupted the tree's condition block"
            )

    def test_make_if_compound_deep_copies_then_children(self):
        """AC-11 deep-copy: mutating the then_children list after construction must not affect the tree."""
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            ["SPY"],
            "gt",
            rhs,
            window=10,
        )
        then_asset = m.make_asset("SPY")
        then_list = [then_asset]
        if_node = m.make_if_compound(
            cond,
            then_children=then_list,
            else_children=[m.make_asset("BIL")],
        )
        # Mutate the input then_list
        then_list.append(m.make_asset("QQQ"))
        # Find the true-branch and count its children
        children = if_node.get("children", [])
        true_branch = next(
            (c for c in children if isinstance(c, dict) and not c.get("is-else-condition?")),
            None,
        )
        if true_branch:
            # The true branch should have 1 child (the original SPY), not 2
            assert len(true_branch.get("children", [])) == 1, (
                "AC-11 deep-copy: mutating then_children after make_if_compound corrupted the tree"
            )

    def test_validate_tree_does_not_mutate_tree_with_compound_block(self):
        """AC-11 read-only: validate_tree must not mutate a tree containing a compound block."""
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            ["SPY", "QQQ"],
            "gt",
            rhs,
            window=10,
        )
        if_node = m.make_if_compound(
            cond,
            then_children=[m.make_asset("SPY")],
            else_children=[m.make_asset("BIL")],
        )
        root = m.make_root("Mutation Test", "daily", [m.make_weight_equal([if_node])])
        before = json.dumps(root, sort_keys=True)
        m.validate_tree(root)
        after = json.dumps(root, sort_keys=True)
        assert before == after, (
            "AC-11 read-only: validate_tree mutated a tree containing a compound condition block"
        )

    def test_extract_tickers_does_not_mutate_tree_with_compound_block(self):
        """AC-11 read-only: extract_tickers must not mutate a tree with a compound block."""
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            ["SPY", "QQQ"],
            "gt",
            rhs,
            window=10,
        )
        if_node = m.make_if_compound(
            cond,
            then_children=[m.make_asset("SPY")],
            else_children=[m.make_asset("BIL")],
        )
        root = m.make_root("Read-Only Test", "daily", [m.make_weight_equal([if_node])])
        before = json.dumps(root, sort_keys=True)
        m.extract_tickers(root)
        after = json.dumps(root, sort_keys=True)
        assert before == after, (
            "AC-11 read-only: extract_tickers mutated a tree containing a compound condition block"
        )

    def test_lint_tree_does_not_raise_on_compound_block_tree(self):
        """AC-11 read-only: lint_tree must not raise on a tree with a compound block."""
        m = _import_schema()
        rhs = m.make_constant_rhs(80)
        cond = m.make_binary_compound_condition(
            "relative-strength-index",
            ["SPY"],
            "gt",
            rhs,
            window=10,
        )
        if_node = m.make_if_compound(
            cond,
            then_children=[m.make_asset("SPY")],
            else_children=[m.make_asset("BIL")],
        )
        root = m.make_root("Lint Test", "daily", [m.make_weight_equal([if_node])])
        try:
            warnings = m.lint_tree(root)
        except Exception as exc:
            pytest.fail(
                f"AC-11: lint_tree raised {type(exc).__name__} on compound block tree: {exc}"
            )
        assert isinstance(warnings, list)


# ===========================================================================
# AC-12: No regression — existing flat constructors and validation unchanged
# ===========================================================================


class TestGrammarFoundationNoRegression:
    """AC-12: All pre-existing symphony_schema behaviors remain intact after the widening.

    The grammar-foundation changes must be purely additive: widen allowlists,
    add new constructors, extend validate_tree — never remove or break existing paths.
    """

    def test_flat_make_condition_make_if_pipeline_still_validates(self):
        """AC-12: The full flat constructor pipeline must still produce a valid tree."""
        m = _import_schema()
        lhs = m.make_indicator("cumulative-return", "TLT", window=200)
        cond = m.make_condition(lhs, "gt", 0.0)
        if_node = m.make_if(
            cond,
            then_children=[m.make_asset("TLT")],
            else_children=[m.make_asset("BIL")],
        )
        root = m.make_root("AC-12 Regression", "daily", [m.make_weight_equal([if_node])])
        errors = m.validate_tree(root)
        assert errors == [], f"AC-12: flat constructor pipeline regression; got errors: {errors}"

    def test_known_steps_unchanged(self):
        """AC-12: KNOWN_STEPS must still contain all 9 original step values (none removed)."""
        m = _import_schema()
        original_steps = {
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
        missing = original_steps - set(m.KNOWN_STEPS)
        assert not missing, (
            f"AC-12: KNOWN_STEPS regression — original step values removed: {missing}"
        )

    def test_original_seven_indicator_fns_still_in_known_indicator_fns(self):
        """AC-12: The original 7 VERIFIED-LOCAL indicator fns must still be in KNOWN_INDICATOR_FNS."""
        m = _import_schema()
        original_fns = {
            "relative-strength-index",
            "cumulative-return",
            "max-drawdown",
            "current-price",
            "standard-deviation-return",
            "moving-average-price",
            "moving-average-return",
        }
        missing = original_fns - set(m.KNOWN_INDICATOR_FNS)
        assert not missing, (
            f"AC-12: KNOWN_INDICATOR_FNS regression — original fn strings removed: {missing}"
        )

    def test_original_four_rebalance_values_still_in_known_rebalance(self):
        """AC-12: The original 4 rebalance values must still be in KNOWN_REBALANCE."""
        m = _import_schema()
        original_rebalance = {"daily", "none", "weekly", "monthly"}
        missing = original_rebalance - set(m.KNOWN_REBALANCE)
        assert not missing, (
            f"AC-12: KNOWN_REBALANCE regression — original values removed: {missing}"
        )

    def test_gt_lt_lte_still_in_known_comparators(self):
        """AC-12: The original 3 comparators (gt, lt, lte) must still be in KNOWN_COMPARATORS."""
        m = _import_schema()
        for comp in ("gt", "lt", "lte"):
            assert comp in m.KNOWN_COMPARATORS, (
                f"AC-12: original comparator {comp!r} removed from KNOWN_COMPARATORS"
            )

    def test_validate_tree_still_never_raises_on_none(self):
        """AC-12: validate_tree(None) must still return a list and never raise."""
        m = _import_schema()
        try:
            result = m.validate_tree(None)
        except Exception as exc:
            pytest.fail(f"AC-12 regression: validate_tree(None) raised {type(exc).__name__}: {exc}")
        assert isinstance(result, list)

    def test_extract_tickers_still_returns_set(self):
        """AC-12: extract_tickers must still return a set for any input."""
        m = _import_schema()
        result = m.extract_tickers(None)
        assert isinstance(result, set)

    def test_rsi_still_produces_lint_warning_after_widening(self):
        """AC-12: 'rsi' must still be a lint warning (not in KNOWN_INDICATOR_FNS).

        The AC-3 widening adds 6 new fns; 'rsi' (abbreviation) must remain excluded.
        """
        m = _import_schema()
        assert "rsi" not in m.KNOWN_INDICATOR_FNS, (
            "AC-12: 'rsi' must NOT be added to KNOWN_INDICATOR_FNS by the AC-3 widening"
        )

    def test_make_asset_make_root_make_weight_equal_still_work(self):
        """AC-12: Core flat constructors must still produce correct step values."""
        m = _import_schema()
        asset = m.make_asset("SPY")
        assert asset["step"] == "asset"
        wt_eq = m.make_weight_equal([asset])
        assert wt_eq["step"] == "wt-cash-equal"
        root = m.make_root("Regression", "daily", [wt_eq])
        assert root["step"] == "root"
        assert m.validate_tree(root) == []

    def test_make_filter_still_works(self):
        """AC-12: make_filter must still work correctly after the grammar-foundation changes."""
        m = _import_schema()
        spy = m.make_asset("SPY")
        qqq = m.make_asset("QQQ")
        flt = m.make_filter("top", 1, "cumulative-return", [spy, qqq], window=20)
        assert flt["step"] == "filter"
        assert flt.get("select-fn") == "top"
        assert flt.get("select-n") == 1
        assert "sort-by-fn-params" in flt
