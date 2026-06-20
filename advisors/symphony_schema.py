"""Symphony decision-tree schema: constructors, validation, lint, and rendering.

Phase-1 Strategy Builder primitive. This module CONSTRUCTS synthetic Composer
``raw_value`` decision trees (for submission to ``POST /api/v0.1/backtest``) and
provides read-only inspection of arbitrary trees — both ones we build and real
``/score`` responses captured from Composer.

The node vocabulary, field shapes, and tolerance rules are pinned by
``feature-plans/strategy-builder-composer-grammar.md`` (the grammar doc) and the
binding contract amendments in
``feature-plans/strategy-builder-phase1-handoff.md``. Every constant below cites
its grammar-doc source.

Design contract (load-bearing):
  * ``validate_tree`` returns HARD errors only and NEVER raises — the caller can
    always safely inspect the returned list. Structural problems are hard
    errors; vocabulary drift (unknown indicator fns) and policy violations
    (weight sums, size caps) are NOT — those are ``lint_tree`` warnings.
  * Both golden fixtures (small: 866 nodes / depth 19; large: 8455 nodes /
    depth 230) must ``validate_tree() == []``. That is why size/depth caps and
    unknown-indicator drift are lint-only (handoff amendments 1 & 2).
  * All tree traversal is ITERATIVE (explicit stack) so a depth-230+ tree never
    raises ``RecursionError`` (handoff amendment 8).
  * Read-only functions (``validate_tree``, ``lint_tree``, ``extract_tickers``,
    ``render_rules_text``) never mutate their input.
  * Constructors emit fresh ``uuid.uuid4()`` ids and deep-copy their children so
    no two parents ever share a mutable subtree.

Pure stdlib only (uuid / copy / json-compatible plain dicts).
"""

from __future__ import annotations

import copy
import uuid

# ---------------------------------------------------------------------------
# Grammar-pinned vocabulary constants
#
# Every value here is VERIFIED-LOCAL (observed verbatim in repo fixtures or
# production code) per the grammar doc. UNVERIFIED / community-only values are
# deliberately excluded so constructed trees never emit an unconfirmed token.
# ---------------------------------------------------------------------------

# The 9 confirmed `step` values. Source: grammar doc §2.1 (all VERIFIED-LOCAL).
KNOWN_STEPS: frozenset[str] = frozenset(
    {
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
)

# The 13 confirmed indicator-fn strings (7 original VERIFIED-LOCAL + 6 new corpus-verified).
# Source: grammar doc §4.1. The canonical RSI token is the full
# "relative-strength-index"; the abbreviation "rsi" is intentionally absent
# (lint warns on it). AC-3 grammar-foundation: 6 new corpus-verified fns added:
#   exponential-moving-average-price (n≈45,816), standard-deviation-price
#   (n≈5,572, was lint-only before AC-3), percentage-price-oscillator,
#   percentage-price-oscillator-signal, upper-bollinger, lower-bollinger.
KNOWN_INDICATOR_FNS: frozenset[str] = frozenset(
    {
        # Original 7 VERIFIED-LOCAL
        "relative-strength-index",
        "cumulative-return",
        "max-drawdown",
        "current-price",
        "standard-deviation-return",
        "moving-average-price",
        "moving-average-return",
        # 6 new corpus-verified fns — AC-3 grammar-foundation
        "exponential-moving-average-price",
        "standard-deviation-price",
        "percentage-price-oscillator",
        "percentage-price-oscillator-signal",
        "upper-bollinger",
        "lower-bollinger",
    }
)

# Confirmed comparators. Source: grammar doc §8. "gt"/"lt" VERIFIED-LOCAL,
# "lte" VERIFIED-COMMUNITY. "gte" corpus-verified (n≈39,596 across 10,441 real
# symphonies — AC-1 grammar-foundation). "eq"/"neq" absent from corpus, excluded.
KNOWN_COMPARATORS: frozenset[str] = frozenset({"gt", "lt", "gte", "lte"})

# Confirmed rebalance cadences. Source: grammar doc §6. "daily" VERIFIED-LOCAL;
# "none"/"weekly"/"monthly" VERIFIED-COMMUNITY (2026-05-31 swagger enum).
# "quarterly" (n≈58) and "yearly" (n≈27) corpus-verified — AC-2 grammar-foundation.
KNOWN_REBALANCE: frozenset[str] = frozenset(
    {"daily", "none", "weekly", "monthly", "quarterly", "yearly"}
)

# Construction-side size ceilings. Source: grammar doc OQ-7 (conservative <500
# node bound for SYNTHETIC trees) + handoff amendment 1. These gate the
# constructors and surface as lint_tree warnings ONLY — they are NOT
# validate_tree hard errors, because real /score trees routinely exceed them
# (small=866 nodes/depth-19, large=8455 nodes/depth-230 both validate clean).
MAX_TOTAL_NODES: int = 500
# Depth ceiling chosen comfortably below Python's default recursion limit; a
# lint threshold only. Source: handoff amendment 1 (construction-side constant).
MAX_TREE_DEPTH: int = 100

# Compound condition-type values. Source: grammar doc §7 (corpus-verified).
# validate_tree checks these when a true-branch if-child carries a `condition`
# block; unknown condition-type strings are hard errors (AC-10).
_KNOWN_CONDITION_TYPES: frozenset[str] = frozenset({"binary", "binary-compound", "compound"})

# Compound condition operator values. Source: grammar doc §7.
# operator ∈ {any, all} only; "or"/"and"/"xor" are hard errors (AC-10).
_KNOWN_OPERATORS: frozenset[str] = frozenset({"any", "all"})

# Maximum depth when walking a compound condition block during validation.
# Prevents a pathologically deep (e.g. 5000-level) input from blowing the
# iterative stack budget. A depth-cap error is emitted when exceeded. Chosen
# to comfortably contain the real corpus depth-230 while bounding runaway
# inputs. Source: AC-10 grammar-foundation.
MAX_CONDITION_DEPTH: int = 400

# The standard weight denominator. Source: grammar doc §5.1 ("den is always the
# integer 100 in every observed instance"). Constructors emit the int form; the
# validator additionally tolerates the string "100" on read (amendment 4).
_WEIGHT_DEN: int = 100

# Sum that wt-cash-specified weight numerators are expected to total. Source:
# grammar doc §5.3 / OQ-4 — a lint warning when violated, never a hard error.
_EXPECTED_WEIGHT_SUM: int = 100

# Float comparison epsilon for the weight-sum lint check. Weight numerators are
# percentages (0–100 range) and may be numeric strings like "66.67", so the
# summed total is a float; 1e-9 is far below any meaningful percentage precision
# and avoids false "sum != 100" warnings from floating-point round-off.
_WEIGHT_SUM_EPSILON: float = 1e-9

# Indicator-fn-bearing keys scanned for lint (flat node-level form). Source:
# grammar doc §3.4 (if-child) and §3.5 (filter sort-by-fn).
_FLAT_FN_KEYS: tuple[str, ...] = ("lhs-fn", "rhs-fn", "sort-by-fn")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fresh_id() -> str:
    """Return a fresh UUID v4 string for a node id (grammar doc §9.1)."""
    return str(uuid.uuid4())


def _iter_children(node: dict) -> list:
    """Return a node's children list, normalised to a list (never None)."""
    children = node.get("children")
    if not isinstance(children, list):
        return []
    return children


def _is_numeric_weight_value(value) -> bool:
    """True if a weight num/den value is an int/float or a numeric string.

    Grammar doc §5.1: num may be an int or a numeric string ("66.67", "100").
    A bool is rejected (bools are ints in Python but are not valid weights).
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value)
            return True
        except ValueError:
            return False
    return False


# ---------------------------------------------------------------------------
# validate_tree — hard errors only, iterative, read-only, never raises
# ---------------------------------------------------------------------------


def validate_tree(tree) -> list[str]:
    """Return a list of HARD structural errors for a decision tree.

    Never raises: any garbage input (None, lists, scalars, malformed nesting)
    yields a list of error strings, possibly empty. Read-only: the input is
    never mutated. Traversal is iterative (explicit stack) so depth-230+ trees
    never trigger ``RecursionError``.

    HARD errors (per handoff amendments 1, 2):
      * top-level input is not a dict, or root step != "root"
      * unknown ``step`` value (not in KNOWN_STEPS)
      * unknown ``comparator`` on a true-branch if-child (not in KNOWN_COMPARATORS)
      * unknown ``rebalance`` on root (not in KNOWN_REBALANCE)
      * missing required fields: root(name/rebalance/children); asset(ticker);
        group(name); filter(select-fn/sort-by-fn); true-branch if-child(lhs-fn/
        comparator)
      * an ``if`` node missing a true branch or an else branch (or empty)
      * a non-asset leaf node (step != "asset" with no children)
      * duplicate node ids anywhere in the tree
      * malformed weight object (missing den, or non-numeric num/den)

    NOT hard errors (tolerated here; some are lint_tree warnings instead):
      * unknown indicator fns (amendment 2) — e.g. "standard-deviation-price"
      * depth/node-count over the construction caps (amendment 1)
      * cosmetic keys, flat *-window-days alongside fn-params, select-n as
        string, weight.den as "100", weight on any node type, compound
        condition blocks (amendments 3-7)
      * None elements inside children lists (skipped gracefully)
    """
    errors: list[str] = []

    if not isinstance(tree, dict):
        return [f"tree is not a dict (got {type(tree).__name__})"]
    if tree.get("step") != "root":
        errors.append(
            f"root node must have step='root' (id={tree.get('id')!r}, "
            f"got step={tree.get('step')!r})"
        )

    seen_ids: set[str] = set()

    # Explicit-stack DFS. Each entry is the node dict to examine.
    stack: list = [tree]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            # None / junk children are skipped gracefully (amendment robustness).
            continue

        node_id = node.get("id")
        step = node.get("step")

        # --- Duplicate-id detection ---------------------------------------
        if isinstance(node_id, str) and node_id:
            if node_id in seen_ids:
                errors.append(f"duplicate node id {node_id!r}")
            else:
                seen_ids.add(node_id)

        # --- Unknown step --------------------------------------------------
        if step not in KNOWN_STEPS:
            errors.append(f"unknown step {step!r} on node id={node_id!r}")
            # Still descend so we surface deeper problems / dup ids.
            for child in _iter_children(node):
                stack.append(child)
            continue

        # --- Per-step required-field checks --------------------------------
        errors.extend(_validate_node_fields(node, step, node_id))

        # --- Non-asset leaf check ------------------------------------------
        # Leaves in the allocation graph must be asset nodes. An asset has no
        # children; any other step with zero children is a structural dangler.
        children = [c for c in _iter_children(node) if isinstance(c, dict)]
        if step != "asset" and not children:
            errors.append(
                f"non-asset leaf node: step={step!r} has no asset children (id={node_id!r})"
            )

        for child in _iter_children(node):
            stack.append(child)

    return errors


def _validate_node_fields(node: dict, step: str, node_id) -> list[str]:
    """Return hard-error strings for a single node's required fields.

    Pure read-only helper; the caller owns traversal. Indicator-fn *values* are
    intentionally NOT checked here (amendment 2 makes them lint-only); only
    structural presence and the strictly-enumerated vocab (comparator /
    rebalance) are hard-gated.
    """
    errs: list[str] = []

    if step == "root":
        if "name" not in node:
            errs.append(f"root missing required field 'name' (id={node_id!r})")
        if "rebalance" not in node:
            errs.append(f"root missing required field 'rebalance' (id={node_id!r})")
        elif node.get("rebalance") not in KNOWN_REBALANCE:
            errs.append(
                f"unknown rebalance {node.get('rebalance')!r} on root "
                f"(id={node_id!r}); allowed: {sorted(KNOWN_REBALANCE)}"
            )
        if "children" not in node:
            errs.append(f"root missing required field 'children' (id={node_id!r})")

    elif step == "asset":
        if not node.get("ticker"):
            errs.append(f"asset missing required field 'ticker' (id={node_id!r})")

    elif step == "group":
        if not node.get("name"):
            errs.append(f"group missing required field 'name' (id={node_id!r})")

    elif step == "filter":
        if "select-fn" not in node:
            errs.append(f"filter missing required field 'select-fn' (id={node_id!r})")
        # select-n is required (grammar doc §3.5); it may be an int or a string
        # ("4" appears in fixtures, amendment 3), but it must be present.
        if "select-n" not in node:
            errs.append(f"filter missing required field 'select-n' (id={node_id!r})")
        if "sort-by-fn" not in node:
            errs.append(f"filter missing required field 'sort-by-fn' (id={node_id!r})")

    elif step == "if":
        errs.extend(_validate_if_branches(node, node_id))

    elif step == "if-child":
        errs.extend(_validate_if_child(node, node_id))

    # Weight objects may appear on any node type (amendment 4); validate shape
    # wherever present.
    if "weight" in node:
        errs.extend(_validate_weight(node, node_id))

    return errs


def _validate_if_branches(node: dict, node_id) -> list[str]:
    """An `if` must have at least one true-branch and one else-branch child."""
    kids = [c for c in _iter_children(node) if isinstance(c, dict)]
    has_else = any(c.get("is-else-condition?") for c in kids)
    has_true = any(not c.get("is-else-condition?") for c in kids)
    errs: list[str] = []
    if not kids:
        errs.append(f"if node has no children (id={node_id!r})")
    else:
        if not has_true:
            errs.append(f"if node missing true-branch child (id={node_id!r})")
        if not has_else:
            errs.append(f"if node missing else-branch child (id={node_id!r})")
    return errs


def _validate_if_child(node: dict, node_id) -> list[str]:
    """Validate a true-branch if-child's condition fields.

    Only the flat-condition true branch is hard-gated for lhs-fn presence and a
    known comparator. Else branches carry no condition. Compound `condition`
    blocks (amendment 6) are validated via _validate_condition_block (AC-10).
    """
    errs: list[str] = []
    if node.get("is-else-condition?"):
        return errs
    # A compound-condition if-child carries its predicate in a nested
    # `condition` block instead of flat lhs-fn/comparator (amendment 6).
    # Run the compound block validator (AC-10) and skip the flat-field checks.
    condition = node.get("condition")
    if isinstance(condition, dict):
        errs.extend(_validate_condition_block(condition, node_id))
        return errs
    if "lhs-fn" not in node:
        errs.append(f"true-branch if-child missing required field 'lhs-fn' (id={node_id!r})")
    if "comparator" not in node:
        errs.append(f"true-branch if-child missing required field 'comparator' (id={node_id!r})")
    elif node.get("comparator") not in KNOWN_COMPARATORS:
        errs.append(
            f"unknown comparator {node.get('comparator')!r} on if-child "
            f"(id={node_id!r}); allowed: {sorted(KNOWN_COMPARATORS)}"
        )
    # When rhs is a ticker comparison (rhs-fixed-value? explicitly False), rhs-fn
    # names the indicator applied to the rhs ticker and is required (grammar doc
    # §3.4). A fixed-value comparison (True, or the field absent) correctly omits
    # rhs-fn, so only the explicit-False case is gated here.
    if node.get("rhs-fixed-value?") is False and "rhs-fn" not in node:
        errs.append(
            f"true-branch if-child with rhs-fixed-value?=False missing required "
            f"field 'rhs-fn' (id={node_id!r})"
        )
    return errs


def _validate_condition_block(condition: dict, node_id) -> list[str]:
    """Validate a compound condition block embedded in a true-branch if-child.

    Iterative DFS bounded by MAX_CONDITION_DEPTH so a 5000-deep pathological
    input never blows the stack. Hard errors (AC-10):
      * unknown condition-type string (not in _KNOWN_CONDITION_TYPES)
      * bad operator (not in _KNOWN_OPERATORS) on compound / binary-compound blocks
      * compound block missing the 'conditions' key
      * binary-compound block missing the 'tickers' key
    Absent condition-type is tolerated (raw binary leaf — valid grammar); None
    elements in conditions[] are skipped gracefully.
    """
    errs: list[str] = []
    # Stack entries: (cond_dict, depth). Depth 0 = the top-level condition block.
    stack: list[tuple] = [(condition, 0)]
    while stack:
        cond, depth = stack.pop()
        if not isinstance(cond, dict):
            continue
        if depth > MAX_CONDITION_DEPTH:
            errs.append(
                f"condition block on if-child id={node_id!r} exceeds MAX_CONDITION_DEPTH "
                f"({MAX_CONDITION_DEPTH}); truncating validation"
            )
            # Stop descending this branch — do not recurse into deeper levels.
            continue
        cond_type = cond.get("condition-type")
        # Absent condition-type is a raw binary leaf: tolerated, skip checks.
        if cond_type is None:
            continue
        # Unknown condition-type is a hard error.
        if cond_type not in _KNOWN_CONDITION_TYPES:
            errs.append(
                f"unknown condition-type {cond_type!r} on if-child id={node_id!r}; "
                f"allowed: {sorted(_KNOWN_CONDITION_TYPES)}"
            )
            continue
        # compound and binary-compound both require a known operator.
        if cond_type in ("compound", "binary-compound"):
            operator = cond.get("operator")
            if operator not in _KNOWN_OPERATORS:
                errs.append(
                    f"condition block on if-child id={node_id!r} has bad operator "
                    f"{operator!r}; allowed: {sorted(_KNOWN_OPERATORS)}"
                )
        # compound requires a 'conditions' key.
        if cond_type == "compound":
            if "conditions" not in cond:
                errs.append(
                    f"compound condition block on if-child id={node_id!r} missing "
                    f"required key 'conditions'"
                )
            else:
                # Recurse into sub-conditions.
                for sub in cond.get("conditions") or []:
                    if isinstance(sub, dict):
                        stack.append((sub, depth + 1))
        # binary-compound requires a 'tickers' key.
        if cond_type == "binary-compound":
            if "tickers" not in cond:
                errs.append(
                    f"binary-compound condition block on if-child id={node_id!r} missing "
                    f"required key 'tickers'"
                )
    return errs


def _validate_weight(node: dict, node_id) -> list[str]:
    """Validate a weight object's shape (grammar doc §5.1; amendment 4)."""
    weight = node.get("weight")
    if not isinstance(weight, dict):
        return [f"weight must be an object on node id={node_id!r} (got {weight!r})"]
    errs: list[str] = []
    if "den" not in weight:
        errs.append(f"weight missing 'den' on node id={node_id!r}")
    elif not _is_numeric_weight_value(weight.get("den")):
        errs.append(f"weight 'den' is non-numeric ({weight.get('den')!r}) on node id={node_id!r}")
    if "num" not in weight:
        errs.append(f"weight missing 'num' on node id={node_id!r}")
    elif not _is_numeric_weight_value(weight.get("num")):
        errs.append(f"weight 'num' is non-numeric ({weight.get('num')!r}) on node id={node_id!r}")
    return errs


# ---------------------------------------------------------------------------
# lint_tree — warnings (not hard errors), iterative, read-only, never raises
# ---------------------------------------------------------------------------


def lint_tree(tree) -> list[str]:
    """Return a list of advisory warning strings for a decision tree.

    Warnings (never hard errors): weight numerators not summing to 100 under a
    wt-cash-specified node (OQ-4); total node count over MAX_TOTAL_NODES;
    tree depth over MAX_TREE_DEPTH; any indicator fn not in KNOWN_INDICATOR_FNS
    (amendment 2 — e.g. "rsi", "standard-deviation-price").

    Read-only and never raises; traversal is iterative.
    """
    warnings: list[str] = []
    if not isinstance(tree, dict):
        return warnings

    total_nodes = 0
    max_depth = 0
    unknown_fns: set[str] = set()

    # Stack entries carry (node, depth). Root is depth 0.
    stack: list[tuple] = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, dict):
            continue
        total_nodes += 1
        if depth > max_depth:
            max_depth = depth

        # Unknown indicator fns (flat keys + nested condition blocks).
        for key in _FLAT_FN_KEYS:
            fn = node.get(key)
            if isinstance(fn, str) and fn and fn not in KNOWN_INDICATOR_FNS:
                unknown_fns.add(fn)
        condition = node.get("condition")
        if isinstance(condition, dict):
            unknown_fns.update(_scan_condition_fns(condition))

        # Weight-sum check on wt-cash-specified containers.
        if node.get("step") == "wt-cash-specified":
            warnings.extend(_lint_weight_sum(node))

        for child in _iter_children(node):
            stack.append((child, depth + 1))

    if total_nodes > MAX_TOTAL_NODES:
        warnings.append(
            f"tree has {total_nodes} nodes, exceeding MAX_TOTAL_NODES ({MAX_TOTAL_NODES})"
        )
    if max_depth > MAX_TREE_DEPTH:
        warnings.append(f"tree depth {max_depth} exceeds MAX_TREE_DEPTH ({MAX_TREE_DEPTH})")
    for fn in sorted(unknown_fns):
        warnings.append(f"unverified indicator fn {fn!r} (not in KNOWN_INDICATOR_FNS)")

    return warnings


def _scan_condition_fns(condition: dict) -> set[str]:
    """Collect unknown indicator-fn strings from a compound condition block.

    Mirrors symphony_logic._scan_condition: predicate fns live in lhs.fn / rhs.fn
    and compound conditions nest under `conditions`. Iterative to stay
    recursion-safe. Returns only the fns NOT in KNOWN_INDICATOR_FNS.
    """
    unknown: set[str] = set()
    stack: list = [condition]
    while stack:
        cond = stack.pop()
        if not isinstance(cond, dict):
            continue
        for side in ("lhs", "rhs"):
            operand = cond.get(side)
            if isinstance(operand, dict):
                fn = operand.get("fn")
                if isinstance(fn, str) and fn and fn not in KNOWN_INDICATOR_FNS:
                    unknown.add(fn)
        for sub in cond.get("conditions") or []:
            stack.append(sub)
    return unknown


def _lint_weight_sum(node: dict) -> list[str]:
    """Warn about a wt-cash-specified node's weights.

    Two warnings: an "undefined allocation" warning when no child carries a
    weight (a specified-weight container with nothing to specify), and a
    "weights do not sum to 100" warning otherwise (OQ-4). A node with no
    children at all is handled by validate_tree's non-asset-leaf rule, so it is
    not warned about here.
    """
    children = [c for c in _iter_children(node) if isinstance(c, dict)]
    total = 0.0
    saw_weight = False
    for child in children:
        weight = child.get("weight")
        if isinstance(weight, dict) and _is_numeric_weight_value(weight.get("num")):
            saw_weight = True
            total += float(weight["num"])
    if children and not saw_weight:
        return [
            f"wt-cash-specified node has no weighted children; allocation split "
            f"is undefined (id={node.get('id')!r})"
        ]
    if saw_weight and abs(total - _EXPECTED_WEIGHT_SUM) > _WEIGHT_SUM_EPSILON:
        return [
            f"wt-cash-specified weights sum to {total:g}, not "
            f"{_EXPECTED_WEIGHT_SUM} (id={node.get('id')!r})"
        ]
    return []


# ---------------------------------------------------------------------------
# extract_tickers — iterative, read-only, never raises
# ---------------------------------------------------------------------------


def extract_tickers(tree) -> set[str]:
    """Return the set of all ticker strings from asset (or ticker-bearing) nodes.

    Iterative traversal; returns an empty set for a tree with no assets; never
    raises; read-only.

    AC-9: The binary-compound lhs emits ticker='%' as a grammar placeholder;
    '%' is not a real ticker and is excluded from the result. Real tickers in
    a binary-compound's top-level 'tickers' list are included.
    """
    tickers: set[str] = set()
    if not isinstance(tree, dict):
        return tickers
    stack: list = [tree]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        # Collect node-level ticker (skip the '%' binary-compound placeholder).
        ticker = node.get("ticker")
        if isinstance(ticker, str) and ticker and ticker != "%":
            tickers.add(ticker)
        # Walk condition block's tickers list for binary-compound nodes (AC-9).
        condition = node.get("condition")
        if isinstance(condition, dict):
            _collect_condition_tickers(condition, tickers)
        for child in _iter_children(node):
            stack.append(child)
    return tickers


def _collect_condition_tickers(condition: dict, tickers: set) -> None:
    """Walk a compound condition block and collect real tickers into ``tickers``.

    Skips the '%' placeholder. Handles binary (lhs/rhs operands), binary-compound
    (broadcast tickers list), and nested compound blocks. Iterative to avoid
    recursion depth limits.
    """
    stack: list = [condition]
    while stack:
        cond = stack.pop()
        if not isinstance(cond, dict):
            continue
        # binary: collect from lhs.ticker and rhs.ticker (ticker-comparison rhs).
        # The '%' placeholder used by binary_compound lhs is excluded.
        for operand_key in ("lhs", "rhs"):
            operand = cond.get(operand_key)
            if isinstance(operand, dict):
                t = operand.get("ticker")
                if isinstance(t, str) and t and t != "%":
                    tickers.add(t)
        # binary-compound: collect from the top-level tickers list (real tickers)
        # and skip the lhs ticker which is '%'.
        for t in cond.get("tickers") or []:
            if isinstance(t, str) and t and t != "%":
                tickers.add(t)
        # compound: recurse into sub-conditions.
        for sub in cond.get("conditions") or []:
            if isinstance(sub, dict):
                stack.append(sub)


# ---------------------------------------------------------------------------
# render_rules_text — deterministic, operator-readable, read-only
# ---------------------------------------------------------------------------


def render_rules_text(tree) -> str:
    """Render a decision tree as deterministic, operator-readable text.

    Walks the tree depth-first in child order (deterministic), emitting one
    indented line per node describing its role. Every ticker present in the tree
    appears in the output. Read-only; never raises.
    """
    if not isinstance(tree, dict):
        return ""

    lines: list[str] = []
    # Stack entries: (node, depth). Children are pushed in reverse so the output
    # preserves left-to-right child order deterministically.
    stack: list[tuple] = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, dict):
            continue
        lines.append(_render_node_line(node, depth))
        children = _iter_children(node)
        for child in reversed(children):
            stack.append((child, depth + 1))

    return "\n".join(lines)


def _render_node_line(node: dict, depth: int) -> str:
    """Render a single node to one indented, human-readable line."""
    indent = "  " * depth
    step = node.get("step")

    if step == "root":
        return f"{indent}STRATEGY {node.get('name', '')!r} (rebalance: {node.get('rebalance', '')})"
    if step == "asset":
        ticker = node.get("ticker", "")
        name = node.get("name") or ""
        suffix = f" — {name}" if name else ""
        return f"{indent}HOLD {ticker}{suffix}"
    if step == "group":
        return f"{indent}GROUP {node.get('name', '')!r}"
    if step == "wt-cash-equal":
        return f"{indent}WEIGHT equally across children"
    if step == "wt-cash-specified":
        return f"{indent}WEIGHT by specified percentages"
    if step == "wt-inverse-vol":
        return f"{indent}WEIGHT by inverse volatility"
    if step == "filter":
        return (
            f"{indent}FILTER {node.get('select-fn', '')} {node.get('select-n', '')} "
            f"by {node.get('sort-by-fn', '')}"
        )
    if step == "if":
        return f"{indent}IF"
    if step == "if-child":
        return _render_if_child_line(node, indent)
    return f"{indent}{step}"


def _render_if_child_line(node: dict, indent: str) -> str:
    """Render an if-child branch as a readable condition / else line.

    AC-9: when the if-child carries a compound 'condition' block (binary-compound
    or compound), renders the gate type (ANY/all) and the condition fn/tickers
    instead of the flat lhs-fn/comparator fields.
    """
    if node.get("is-else-condition?"):
        return f"{indent}ELSE"
    condition = node.get("condition")
    if isinstance(condition, dict):
        return _render_compound_condition_line(condition, indent)
    lhs_fn = node.get("lhs-fn", "")
    lhs_val = node.get("lhs-val", "")
    comparator = node.get("comparator", "")
    if node.get("rhs-fixed-value?"):
        rhs = node.get("rhs-val", "")
    else:
        rhs_fn = node.get("rhs-fn", "")
        rhs_val = node.get("rhs-val", "")
        rhs = f"{rhs_fn}({rhs_val})" if rhs_fn else rhs_val
    return f"{indent}WHEN {lhs_fn}({lhs_val}) {comparator} {rhs}"


def _render_compound_condition_line(condition: dict, indent: str) -> str:
    """Render a compound condition block as a human-readable WHEN line.

    Surfaces the gate type (ANY / all), the indicator fn, the tickers, and the
    comparator+rhs so an operator reading the output understands the semantics.
    """
    cond_type = condition.get("condition-type", "")
    operator = condition.get("operator", "")
    comparator = condition.get("comparator", "")
    # Render the rhs (constant or nested operand).
    rhs_obj = condition.get("rhs") or {}
    if isinstance(rhs_obj, dict) and "constant" in rhs_obj:
        rhs_str = str(rhs_obj["constant"])
    else:
        rhs_str = str(condition.get("rhs-val", ""))
    if cond_type == "binary-compound":
        lhs = condition.get("lhs") or {}
        fn = lhs.get("fn", "")
        tickers = condition.get("tickers") or []
        tickers_str = ", ".join(str(t) for t in tickers if t != "%")
        gate = operator.upper() if operator else "ANY"
        return f"{indent}WHEN {gate}([{tickers_str}]) {fn} {comparator} {rhs_str}"
    if cond_type == "compound":
        gate = operator.upper() if operator else "ANY"
        sub_conds = condition.get("conditions") or []
        # Collect all real tickers from nested binary-compound sub-conditions so
        # that render_rules_text mentions every ticker present in the tree (AC-9,
        # golden fixture contract). Use the same iterative walk as extract_tickers.
        all_tickers: set[str] = set()
        _collect_condition_tickers(condition, all_tickers)
        tickers_part = f" [{', '.join(sorted(all_tickers))}]" if all_tickers else ""
        return f"{indent}WHEN {gate}({len(sub_conds)} conditions){tickers_part}"
    # binary leaf or unknown: render minimally
    lhs = condition.get("lhs") or {}
    fn = lhs.get("fn", "")
    ticker = lhs.get("ticker", "")
    return f"{indent}WHEN {fn}({ticker}) {comparator} {rhs_str}"


# ---------------------------------------------------------------------------
# Constructors
#
# Each emits a plain dict with grammar-doc field shapes, a fresh uuid4 id, and
# DEEP-COPIED children so no two parents ever share a mutable subtree. None of
# the constructors validate — validate_tree is the validation gate.
# ---------------------------------------------------------------------------


def make_asset(ticker: str, *, name: str = "", exchange: str = "") -> dict:
    """Build an asset leaf node (grammar doc §3.9)."""
    return {
        "step": "asset",
        "ticker": ticker,
        "name": name,
        "exchange": exchange,
        "id": _fresh_id(),
    }


def make_root(name: str, rebalance: str, children: list) -> dict:
    """Build a root node (grammar doc §3.1)."""
    return {
        "step": "root",
        "name": name,
        "rebalance": rebalance,
        "description": "",  # required by live Composer /backtest API (was HTTP 400)
        "id": _fresh_id(),
        "children": [copy.deepcopy(child) for child in children],
    }


def make_weight_equal(children: list) -> dict:
    """Build a wt-cash-equal allocation node (grammar doc §3.6)."""
    return {
        "step": "wt-cash-equal",
        "id": _fresh_id(),
        "children": [copy.deepcopy(child) for child in children],
    }


def make_weight_specified(children_with_weights: list) -> dict:
    """Build a wt-cash-specified node, attaching a weight to each child.

    ``children_with_weights`` is a list of ``(child_dict, num)`` tuples. Each
    child is deep-copied and given ``weight={"num": num, "den": 100}`` (grammar
    doc §5.1 — den is always the integer 100; constructors emit the int form).
    """
    out_children: list[dict] = []
    for child, num in children_with_weights:
        new_child = copy.deepcopy(child)
        new_child["weight"] = {"num": num, "den": _WEIGHT_DEN}
        out_children.append(new_child)
    return {
        "step": "wt-cash-specified",
        "id": _fresh_id(),
        "children": out_children,
    }


def make_inverse_vol(children: list) -> dict:
    """Build a wt-inverse-vol allocation node (grammar doc §3.8)."""
    return {
        "step": "wt-inverse-vol",
        "window-days": 30,  # required by live Composer /backtest API (was HTTP 422)
        "id": _fresh_id(),
        "children": [copy.deepcopy(child) for child in children],
    }


def make_group(name: str, children: list) -> dict:
    """Build a named group container node (grammar doc §3.2)."""
    return {
        "step": "group",
        "name": name,
        "id": _fresh_id(),
        "children": [copy.deepcopy(child) for child in children],
    }


def make_filter(
    select_fn: str,
    select_n: int,
    sort_by_fn: str,
    children: list,
    *,
    window: int,
) -> dict:
    """Build a filter node (grammar doc §3.5).

    Emits the nested ``sort-by-fn-params: {"window": window}`` object form (the
    JSON-API serialization), never the legacy flat ``sort-by-window-days`` key.
    """
    return {
        "step": "filter",
        "select-fn": select_fn,
        "select-n": select_n,
        "sort-by-fn": sort_by_fn,
        "sort-by-fn-params": {"window": window},
        "id": _fresh_id(),
        "children": [copy.deepcopy(child) for child in children],
    }


def make_indicator(fn: str, ticker: str, *, window: int) -> dict:
    """Build an indicator descriptor for use as the lhs of make_condition.

    Returns the grammar §7 condition-operand shape:
    ``{"fn": fn, "fn-params": {"window": window}, "val": ticker}``. This is a
    descriptor — make_if extracts its fields into the flat if-child lhs-* keys.
    """
    return {
        "fn": fn,
        "fn-params": {"window": window},
        "val": ticker,
    }


def make_condition(
    lhs_indicator: dict,
    comparator: str,
    rhs,
    *,
    rhs_indicator: dict | None = None,
) -> dict:
    """Build a condition descriptor from an lhs indicator, comparator, and rhs.

    Two comparison shapes (grammar doc §3.4):

      * Fixed-value: ``rhs`` is numeric (int/float, non-bool). The condition is a
        fixed-value comparison — ``rhs-fixed-value? = True``, ``rhs-val`` is the
        numeric string. ``rhs_indicator`` must be omitted (there is no rhs fn).
      * Ticker comparison: ``rhs`` is a ticker string. The condition compares an
        indicator on the lhs ticker against the same/another indicator on the
        rhs ticker — ``rhs-fixed-value? = False``, and ``rhs_indicator`` (a
        ``make_indicator(...)`` descriptor) is REQUIRED so make_if can emit the
        mandatory ``rhs-fn`` / ``rhs-fn-params`` fields. Omitting it raises
        ValueError rather than silently emitting a tree that validate_tree would
        then reject.

    Whole-number numeric rhs is stringified without a trailing ``.0`` (e.g. 0.0
    -> ``"0"``, not ``"0.0"``) to match the fixture rhs-val form (grammar §3.4).

    The returned dict is consumed by make_if to populate an if-child node.
    """
    cond: dict = {
        "lhs": copy.deepcopy(lhs_indicator),
        "comparator": comparator,
    }
    # A bare bool is not a meaningful rhs; treat it as a fixed value so
    # validate_tree can still reason about it deterministically. (Checked first
    # because bool is a subclass of int.)
    if isinstance(rhs, bool):
        cond["rhs-fixed-value?"] = True
        cond["rhs-val"] = str(rhs)
    elif isinstance(rhs, (int, float)):
        cond["rhs-fixed-value?"] = True
        cond["rhs-val"] = _numeric_rhs_str(rhs)
        if rhs_indicator is not None:
            raise ValueError(
                "rhs_indicator must not be supplied for a fixed-value comparison "
                "(numeric rhs); it applies only to ticker comparisons."
            )
    else:
        # Ticker comparison: rhs-fn is mandatory (grammar §3.4), so the caller
        # must supply the rhs indicator. Guard against the silent-then-reject
        # footgun the domain review flagged.
        if not isinstance(rhs_indicator, dict):
            raise ValueError(
                "Cannot construct a ticker-comparison if-child: supply "
                "rhs_indicator=make_indicator(...) so rhs-fn / rhs-fn-params can "
                f"be emitted for rhs ticker {rhs!r}."
            )
        cond["rhs-fixed-value?"] = False
        cond["rhs-val"] = rhs
        cond["rhs"] = copy.deepcopy(rhs_indicator)
    return cond


def _numeric_rhs_str(rhs) -> str:
    """Stringify a numeric rhs in fixture form (whole floats drop the .0)."""
    if isinstance(rhs, float) and rhs == int(rhs):
        return str(int(rhs))
    return str(rhs)


def make_condition_operand(fn: str, ticker: str, *, window: int) -> dict:
    """Build a condition operand for use in compound condition constructors.

    Returns the grammar §7 compound-operand shape:
    ``{"fn": fn, "ticker": ticker, "params": {"window": window}}``.

    This is distinct from ``make_indicator`` (which emits the flat if-child
    shape ``fn-params`` / ``val``). Use ``make_condition_operand`` when building
    compound conditions via ``make_binary_condition`` / ``make_binary_compound_condition``.

    The ticker='%' placeholder is faithfully emitted when passed (used internally
    by ``make_binary_compound_condition`` for the broadcast lhs operand).
    Each call returns an independent dict; no shared mutable state.
    """
    return {
        "fn": fn,
        "ticker": ticker,
        "params": {"window": window},
    }


def make_constant_rhs(value) -> dict:
    """Build a constant rhs descriptor for use in compound condition constructors.

    Returns ``{"constant": value}``. The value is stored as-is (int, float, or
    any JSON-compatible scalar); no coercion is applied.
    """
    return {"constant": value}


def make_binary_condition(lhs_operand: dict, comparator: str, rhs) -> dict:
    """Build a binary leaf condition (grammar §7 condition-type='binary').

    Args:
        lhs_operand: A ``make_condition_operand(...)`` dict.
        comparator: One of the KNOWN_COMPARATORS strings.
        rhs: Either a ``make_constant_rhs(...)`` dict or another
            ``make_condition_operand(...)`` dict for ticker comparisons.

    Returns a ``{"condition-type": "binary", "lhs": ..., "comparator": ..., "rhs": ...}``
    dict with the lhs and rhs deep-copied so the caller's originals remain independent.
    """
    return {
        "condition-type": "binary",
        "lhs": copy.deepcopy(lhs_operand),
        "comparator": comparator,
        "rhs": copy.deepcopy(rhs),
    }


def make_binary_compound_condition(
    fn: str,
    tickers: list,
    comparator: str,
    rhs,
    *,
    window: int,
    operator: str = "any",
) -> dict:
    """Build a binary-compound condition (grammar §7 condition-type='binary-compound').

    The frontrunner primitive: broadcast one predicate (``fn(%) comparator rhs``)
    over a list of tickers with any/all semantics. The lhs ticker is the grammar
    §7 placeholder ``"%"``; the real watched tickers live in the top-level
    ``tickers`` list.

    Args:
        fn: Indicator fn string (e.g. ``"relative-strength-index"``).
        tickers: Non-empty list of ticker strings to broadcast over.
        comparator: One of the KNOWN_COMPARATORS strings.
        rhs: A ``make_constant_rhs(...)`` dict (or any rhs descriptor).
        window: Indicator window passed to the broadcast lhs operand.
        operator: Gate semantics — ``"any"`` (OR) or ``"all"`` (AND). Defaults
            to ``"any"``. Raises ValueError if not in ``{"any", "all"}``.

    Raises:
        ValueError: If ``operator`` is not in ``{"any", "all"}`` or if
            ``tickers`` is empty.
    """
    if operator not in _KNOWN_OPERATORS:
        raise ValueError(
            f"make_binary_compound_condition: operator must be one of "
            f"{sorted(_KNOWN_OPERATORS)!r}; got {operator!r}"
        )
    if not tickers:
        raise ValueError("make_binary_compound_condition: tickers must be a non-empty list")
    lhs = make_condition_operand(fn, "%", window=window)
    return {
        "condition-type": "binary-compound",
        "operator": operator,
        "lhs": lhs,
        "tickers": list(tickers),  # shallow copy — strings are immutable
        "comparator": comparator,
        "rhs": copy.deepcopy(rhs),
    }


def make_compound_condition(operator: str, conditions: list) -> dict:
    """Build a compound condition (grammar §7 condition-type='compound').

    Joins N sub-conditions with ``any`` (OR) or ``all`` (AND) semantics.
    Sub-conditions can be binary leaves, binary-compound blocks, or other
    compound blocks (nestable).

    Args:
        operator: ``"any"`` or ``"all"``. Raises ValueError if invalid.
        conditions: Non-empty list of condition dicts. Raises ValueError if empty.

    Each sub-condition is deep-copied into the result.
    """
    if operator not in _KNOWN_OPERATORS:
        raise ValueError(
            f"make_compound_condition: operator must be one of "
            f"{sorted(_KNOWN_OPERATORS)!r}; got {operator!r}"
        )
    if not conditions:
        raise ValueError("make_compound_condition: conditions must be a non-empty list")
    return {
        "condition-type": "compound",
        "operator": operator,
        "conditions": [copy.deepcopy(c) for c in conditions],
    }


def make_if_compound(
    condition_block: dict,
    *,
    then_children: list,
    else_children: list,
) -> dict:
    """Build an if node whose true-branch if-child carries a compound condition block.

    Unlike ``make_if`` (which extracts flat lhs-fn/comparator fields from a
    ``make_condition(...)`` descriptor), ``make_if_compound`` stores the
    authoritative ``condition`` block dict directly on the true-branch if-child.
    The validate_tree compound-block path (Amendment 6, AC-10) recognises this
    and skips the flat-field requirements.

    Args:
        condition_block: A compound condition dict (binary, binary-compound, or
            compound) built via the ``make_*_condition`` constructors.
        then_children: Allocation nodes for the true branch.
        else_children: Allocation nodes for the else branch.

    All inputs are deep-copied. Fresh uuid4 ids are assigned to the if node and
    both if-child nodes.
    """
    true_child: dict = {
        "step": "if-child",
        "is-else-condition?": False,
        "condition": copy.deepcopy(condition_block),
        "id": _fresh_id(),
        "children": [copy.deepcopy(child) for child in then_children],
    }
    else_child: dict = {
        "step": "if-child",
        "is-else-condition?": True,
        "id": _fresh_id(),
        "children": [copy.deepcopy(child) for child in else_children],
    }
    return {
        "step": "if",
        "id": _fresh_id(),
        "children": [true_child, else_child],
    }


def make_if(condition: dict, *, then_children: list, else_children: list) -> dict:
    """Build an if node with a true-branch and an else-branch if-child.

    The true-branch if-child carries the flat condition fields extracted from
    ``condition`` (lhs-fn / lhs-fn-params / lhs-val / comparator /
    rhs-fixed-value? / rhs-val) per grammar doc §3.4; the else-branch carries
    only ``is-else-condition? = True``. Children are deep-copied into their
    respective branches.
    """
    lhs = condition.get("lhs") or {}
    true_child: dict = {
        "step": "if-child",
        "is-else-condition?": False,
        "lhs-fn": lhs.get("fn"),
        "lhs-fn-params": copy.deepcopy(lhs.get("fn-params") or {}),
        "lhs-val": lhs.get("val"),
        "comparator": condition.get("comparator"),
        "rhs-fixed-value?": condition.get("rhs-fixed-value?", True),
        "rhs-val": condition.get("rhs-val"),
        "id": _fresh_id(),
        "children": [copy.deepcopy(child) for child in then_children],
    }
    # For a ticker comparison (rhs-fixed-value? False), rhs-fn / rhs-fn-params
    # are required (grammar §3.4); pull them from the rhs indicator descriptor
    # that make_condition attached. make_condition guarantees its presence in
    # this branch, so a missing descriptor here is a programming error.
    if condition.get("rhs-fixed-value?") is False:
        rhs = condition.get("rhs") or {}
        true_child["rhs-fn"] = rhs.get("fn")
        true_child["rhs-fn-params"] = copy.deepcopy(rhs.get("fn-params") or {})
    else_child: dict = {
        "step": "if-child",
        "is-else-condition?": True,
        "id": _fresh_id(),
        "children": [copy.deepcopy(child) for child in else_children],
    }
    return {
        "step": "if",
        "id": _fresh_id(),
        "children": [true_child, else_child],
    }
