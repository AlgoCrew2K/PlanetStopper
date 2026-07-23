"""Plan->Tree Compiler — Component 3 of the real Strategy Builder.

Deterministically compiles a build-plan DSL dict (the Component-2 generator output)
into a Composer ``raw_value`` decision tree using ONLY the existing
``advisors/symphony_schema.py`` constructors, then runs a bounded validate +
repair loop so only valid, tradeable trees reach the downstream pipeline.

Public surface
--------------
MAX_REPAIR_ATTEMPTS : int
    Named bound on the validate/tradeability repair loop (no magic number; never
    unbounded).

CompileResult : dataclass
    Container returned by compile_plan. Fields:
      .tree   (dict | None) — the compiled Composer raw_value tree, or None on
              a clean drop.
      .reason (str | None) — set on a drop; None on success.
              D-1: carries only type(exc).__name__ on an internal error.
      .tradeability_unverified (bool) — True only when the repair loop
              degraded on an infra/transport failure (see below): the tree
              IS returned (not None) but its tradeability was never checked
              against Composer. Default False.

compile_plan(plan, *, backtest_fn=None) -> CompileResult
    Compile a single build-plan into a Composer tree. The DSL NODE/CONDITION
    union dispatches 1:1 to symphony_schema constructors. validate_tree gates the
    result (a HARD-error tree NEVER reaches backtest). When backtest_fn is
    supplied, a bounded repair loop runs: a tradeability rejection (HTTP 400
    envelope) prunes the named ticker and retries; a grammar rejection (HTTP 422
    envelope) is NOT blind-ticker-pruned. An infra/transport failure (connection
    error, timeout, DNS failure, HTTP 5xx/429, or any other non-parseable /
    non-200 / non-400 result — Composer is unreachable, not rejecting) DEGRADES
    instead of dropping: the current validated tree is returned with
    reason="backtest_unavailable" and tradeability_unverified=True, so a
    Composer outage never silently zeroes the run. market_cap-scheme plans are
    a producer-deprecated drop (Composer retired market-cap weighting —
    captured 2026-06-20). Never raises (D-1).

Design constraints
------------------
- Compile via symphony_schema constructors ONLY (no hand-built node dicts).
- Determinism: same plan -> byte-identical tree modulo the fresh uuid `id` keys.
- D-1: never raises; failures degrade to (tree=None, reason set).
- Advisory-only: no LIVE_EXECUTION, no Composer write/deploy.
- The error-envelope split parses the composer_backtest_client format
  "HTTP {status}: {text}" (composer_backtest_client.py:360) by STATUS CODE,
  not by message text. A failure with no parseable "HTTP {N}:" prefix at all
  (timeout, transport/connection/DNS error, invalid-JSON-on-200, or the
  Retry-After-exhausted 429 message — none of these carry that prefix) is
  classified infra, same as a parsed 5xx/429.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from advisors import symphony_schema

# Bound on the validate/tradeability repair loop. Must be 1..10 (test-asserted
# range). Three attempts is sufficient: initial compile + two prune-and-retry
# cycles before declaring the plan unrepairable.
MAX_REPAIR_ATTEMPTS: int = 3


@dataclass
class CompileResult:
    """Result of compiling one build-plan. tree is None on a clean drop.

    tradeability_unverified is True only on the infra-degrade path: tree is
    NOT None (it's the last validated tree) but its tradeability against
    Composer was never confirmed because Composer was unreachable, not
    rejecting.
    """

    tree: dict | None = None
    reason: str | None = None
    tradeability_unverified: bool = False


# ---------------------------------------------------------------------------
# Ticker identification from a 400 error envelope.
# The envelope text names the offending ticker in patterns like:
#   "Asset SPY is not tradable"
#   "Ticker NOPRICE not tradable on this venue"
#   "node BADX references an untradable ticker"
#   "VENUE MARKET rejected order: BADX is not tradable"
#   "Comparing SPY: QQQ is not tradable / no pricing data"
#
# The prune target MUST be an in-tree ticker (one in the compiled tree's real
# ticker set). A leading non-ticker uppercase word (venue/market name) or an
# off-tree ticker in the text is not a valid prune target — pruning it would
# be a no-op that leaves the real offending ticker in place and wastes the
# repair budget.
#
# When multiple in-tree tickers appear in the text, the one flagged as
# untradable is the one immediately preceding the "not tradable" /
# "untradable" signal phrase, so we pick the in-tree candidate whose last
# position before the signal is closest.
# ---------------------------------------------------------------------------

_TICKER_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]{0,9}(?:\.[A-Z]{1,2})?)\b")

# Signal phrases that mark what immediately precedes them as the untradable asset.
_UNTRADABLE_SIGNALS = re.compile(
    r"\b(not\s+tradable|untradable|not\s+tradeable|untradeable|no\s+pricing)\b",
    re.IGNORECASE,
)

# Words we know appear in the standard envelope text that are NOT tickers.
_STOP_WORDS = frozenset(
    {"HTTP", "ASSET", "TICKER", "NOT", "NO", "ON", "THIS", "NODE", "REFERENCES", "AN"}
)


def _find_prune_target(text: str, tree_tickers: set[str]) -> str | None:
    """Return the in-tree ticker to prune from a 400 envelope text.

    Cross-references extracted uppercase candidates against ``tree_tickers``
    (the real ticker set from the current compiled tree). Returns None when no
    in-tree candidate is found — signalling the caller to drop cleanly without
    looping the repair budget on a no-op prune.

    When multiple in-tree tickers appear in the text, prefers the one closest
    and immediately before the first "not tradable" / "untradable" signal
    phrase (the one Composer flagged). Falls back to the first in-tree
    candidate found left-to-right when no signal phrase is present.
    """
    if not isinstance(text, str) or not tree_tickers:
        return None

    # Collect all uppercase candidates with their start positions.
    candidates: list[tuple[int, str]] = []
    for match in _TICKER_PATTERN.finditer(text):
        word = match.group(1)
        if word.upper() not in _STOP_WORDS and word in tree_tickers:
            candidates.append((match.start(), word))

    if not candidates:
        return None

    # Find the first untradable signal phrase position in the text.
    signal_match = _UNTRADABLE_SIGNALS.search(text)
    if signal_match is None:
        # No signal phrase found: return the first in-tree candidate left-to-right.
        return candidates[0][1]

    signal_pos = signal_match.start()

    # Among candidates that appear before the signal, pick the one closest
    # (highest start position still < signal_pos). This is the ticker
    # Composer named as untradable.
    before_signal = [(pos, word) for pos, word in candidates if pos < signal_pos]
    if before_signal:
        # The rightmost candidate before the signal is the flagged one.
        return max(before_signal, key=lambda pw: pw[0])[1]

    # All candidates appear after the signal (unusual): fall back to first.
    return candidates[0][1]


# ---------------------------------------------------------------------------
# market_cap containment check — iterative DFS over the DSL NODE tree.
# ---------------------------------------------------------------------------


def _has_market_cap(node) -> bool:
    """Return True if any node in the DSL tree uses scheme=='market_cap'."""
    if not isinstance(node, dict):
        return False
    stack = [node]
    while stack:
        n = stack.pop()
        if not isinstance(n, dict):
            continue
        if n.get("kind") == "weight" and n.get("scheme") == "market_cap":
            return True
        children = n.get("children")
        if isinstance(children, list):
            for child in children:
                # specified-weight children are dicts with a 'node' sub-key
                if isinstance(child, dict):
                    if "node" in child:
                        stack.append(child["node"])
                    else:
                        stack.append(child)
        # if/if_compound branches
        for branch_key in ("then", "else"):
            branch = n.get(branch_key)
            if isinstance(branch, list):
                stack.extend(branch)
    return False


# ---------------------------------------------------------------------------
# Ticker pruning from a compiled Composer raw_value tree.
# ---------------------------------------------------------------------------


def _prune_ticker_from_tree(tree: dict, ticker: str) -> dict | None:
    """Return a NEW tree with all asset nodes for ``ticker`` removed.

    Rebuilds the tree bottom-up (via deep-copy of structure minus the pruned
    ticker). If pruning degenerates a node (e.g. a weight container ends up
    with no children, or an if branch is emptied), returns None so the caller
    can detect degeneration.

    This operates on the COMPILED Composer tree (raw_value shape), not the DSL.
    """
    try:
        result = _prune_node(tree, ticker)
        return result
    except Exception:
        return None


def _prune_node(node, ticker: str):
    """Recursively rebuild a Composer node, omitting asset nodes for ``ticker``.

    Returns None when a node degenerates (empty children where children are
    required), allowing the caller to handle degeneration.
    """
    if not isinstance(node, dict):
        return node

    step = node.get("step")

    # Asset leaf: drop it if it matches the pruned ticker.
    if step == "asset":
        if node.get("ticker") == ticker:
            return None  # signal: drop this leaf
        return copy.deepcopy(node)

    # For nodes with children, rebuild children list with pruned nodes removed.
    children = node.get("children")
    if isinstance(children, list):
        new_children = []
        for child in children:
            rebuilt = _prune_node(child, ticker)
            if rebuilt is not None:
                new_children.append(rebuilt)

        # Nodes that need at least one child to be valid:
        # wt-cash-equal, wt-cash-specified, wt-inverse-vol, group, filter,
        # and the if-child branches. An empty children list degenerates these.
        if not new_children and step not in ("root",):
            # A root with zero children is technically degenerate too, but
            # validate_tree will catch it — return it and let the gate handle it.
            # For allocation nodes an empty children list is always invalid.
            return None

        new_node = {k: v for k, v in node.items() if k != "children"}
        new_node["children"] = new_children
        return new_node

    # Nodes with no children key (shouldn't happen in valid trees, but be safe).
    return copy.deepcopy(node)


# ---------------------------------------------------------------------------
# Infra-vs-rejection classification for a backtest_fn failure.
#
# A genuine Composer rejection (400 tradeability, 422 grammar, or any other
# status Composer actually returned) always carries a parsed HTTP status via
# _parse_envelope_status. An infra/transport failure — Composer unreachable
# or overloaded, NOT rejecting the content — either carries a 5xx/429 status
# (composer_backtest_client.py retries these internally with its own bounded
# backoff before giving up and returning the error) or carries no parseable
# "HTTP {N}:" prefix at all (timeout, connection/DNS error, invalid JSON on
# a 200, or the Retry-After-exhausted 429 message, which omits the colon).
# ---------------------------------------------------------------------------

# HTTP statuses that mean "Composer is overloaded/unreachable", not "Composer
# rejected this content". Matches composer_backtest_client._RETRYABLE_HTTP_STATUSES.
_INFRA_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _is_infra_failure(status: int | None) -> bool:
    """True when a backtest_fn failure is infra/transport, not a content rejection.

    ``status`` is the result of ``_parse_envelope_status`` on the failure
    envelope. None (no parseable "HTTP {N}:" prefix — timeout, transport
    error, invalid JSON, or an unparseable 429) and any parsed 5xx/429 status
    are both infra. Any other parsed status (400, 422, ...) is a genuine
    Composer rejection and is NOT infra.
    """
    return status is None or status in _INFRA_HTTP_STATUSES


# ---------------------------------------------------------------------------
# Condition DSL -> symphony_schema compound condition constructors.
# ---------------------------------------------------------------------------


def _compile_condition(cond: dict):
    """Compile a DSL CONDITION dict into a symphony_schema condition block.

    Handles three types:
      - binary: make_binary_condition
      - binary_compound: make_binary_compound_condition
      - compound: make_compound_condition (recursive)
    """
    cond_type = cond.get("type")

    if cond_type == "binary":
        # Canonical-flat encoding (binary-encoding-fix): reads the SAME field names
        # as the flat-if condition so both paths share one encoding.
        # lhs: lhs_fn / lhs_ticker / window (NOT nested cond["lhs"]["fn"]).
        # rhs constant: rhs["fixed"] (NOT rhs["const"]).
        # rhs ticker-comparison: rhs = {"fn", "ticker", "window"} (unchanged).
        lhs_op = symphony_schema.make_condition_operand(
            cond["lhs_fn"], cond["lhs_ticker"], window=cond["window"]
        )
        rhs = cond["rhs"]
        if "fixed" in rhs:
            rhs_val = symphony_schema.make_constant_rhs(rhs["fixed"])
        else:
            rhs_val = symphony_schema.make_condition_operand(
                rhs["fn"], rhs["ticker"], window=rhs["window"]
            )
        return symphony_schema.make_binary_condition(lhs_op, cond["comparator"], rhs_val)

    if cond_type == "binary_compound":
        rhs = cond["rhs"]
        rhs_val = symphony_schema.make_constant_rhs(rhs["const"])
        return symphony_schema.make_binary_compound_condition(
            cond["fn"],
            cond["tickers"],
            cond["comparator"],
            rhs_val,
            window=cond["window"],
            operator=cond.get("operator", "any"),
        )

    if cond_type == "compound":
        compiled_subs = [_compile_condition(sub) for sub in cond["conditions"]]
        return symphony_schema.make_compound_condition(cond["operator"], compiled_subs)

    raise ValueError(f"Unknown condition type: {cond_type!r}")


# ---------------------------------------------------------------------------
# DSL NODE -> symphony_schema constructor dispatch.
# ---------------------------------------------------------------------------


def _compile_node(node: dict) -> dict:
    """Compile one DSL NODE into a symphony_schema Composer node.

    Dispatches on node['kind'] (and node['scheme'] for weight nodes).
    Raises ValueError for unknown or unsupported kinds (e.g. market_cap).
    """
    kind = node.get("kind")

    if kind == "asset":
        return symphony_schema.make_asset(node["ticker"])

    if kind == "weight":
        scheme = node.get("scheme")

        if scheme == "equal":
            compiled_children = [_compile_node(c) for c in node["children"]]
            return symphony_schema.make_weight_equal(compiled_children)

        if scheme == "specified":
            pairs = [(_compile_node(c["node"]), c["pct"]) for c in node["children"]]
            return symphony_schema.make_weight_specified(pairs)

        if scheme == "inverse_vol":
            compiled_children = [_compile_node(c) for c in node["children"]]
            result = symphony_schema.make_inverse_vol(compiled_children)
            # Honor an explicit window_days from the DSL (default is 30 from constructor).
            if "window_days" in node:
                result["window-days"] = node["window_days"]
            return result

        if scheme == "market_cap":
            # Producer-deprecated: Composer returns HTTP 422 node-type-not-supported.
            # This is caught upstream by _has_market_cap before we even reach here,
            # but guard defensively so any nested call also fails loudly.
            raise ValueError("market_cap weighting is producer-deprecated (AC-17)")

        raise ValueError(f"Unknown weight scheme: {scheme!r}")

    if kind == "group":
        compiled_children = [_compile_node(c) for c in node["children"]]
        return symphony_schema.make_group(node["name"], compiled_children)

    if kind == "filter":
        compiled_children = [_compile_node(c) for c in node["children"]]
        return symphony_schema.make_filter(
            node["select_fn"],
            node["select_n"],
            node["sort_by_fn"],
            compiled_children,
            window=node["window"],
        )

    if kind == "if":
        cond_dsl = node["condition"]
        lhs_indicator = symphony_schema.make_indicator(
            cond_dsl["lhs_fn"], cond_dsl["lhs_ticker"], window=cond_dsl["window"]
        )
        rhs_dsl = cond_dsl["rhs"]
        if "fixed" in rhs_dsl:
            cond = symphony_schema.make_condition(
                lhs_indicator, cond_dsl["comparator"], rhs_dsl["fixed"]
            )
        else:
            rhs_indicator = symphony_schema.make_indicator(
                rhs_dsl["fn"], rhs_dsl["ticker"], window=rhs_dsl["window"]
            )
            cond = symphony_schema.make_condition(
                lhs_indicator,
                cond_dsl["comparator"],
                rhs_dsl["ticker"],
                rhs_indicator=rhs_indicator,
            )
        then_children = [_compile_node(c) for c in node["then"]]
        else_children = [_compile_node(c) for c in node["else"]]
        return symphony_schema.make_if(
            cond, then_children=then_children, else_children=else_children
        )

    if kind == "if_compound":
        compiled_condition = _compile_condition(node["condition"])
        then_children = [_compile_node(c) for c in node["then"]]
        else_children = [_compile_node(c) for c in node["else"]]
        return symphony_schema.make_if_compound(
            compiled_condition, then_children=then_children, else_children=else_children
        )

    raise ValueError(f"Unknown DSL node kind: {kind!r}")


# ---------------------------------------------------------------------------
# Public compile_plan — the only exported entry point.
# ---------------------------------------------------------------------------


def compile_plan(plan, *, backtest_fn=None) -> CompileResult:
    """Compile a build-plan DSL into a validated Composer tree.

    Parameters
    ----------
    plan:
        A build-plan dict as produced by Component 2 (build_plan_generator).
        Top-level keys: plan_id, objective, name, rebalance, root (NODE).
    backtest_fn:
        Optional callable: ``backtest_fn(raw_value) -> result`` where
        ``result.error`` is a non-empty envelope string on failure, None on
        success. When None, compilation + validate_tree gating run but the
        tradeability repair loop does not.

    Returns
    -------
    CompileResult
        .tree   — compiled tree on success or infra-degrade, None on any
                  clean drop.
        .reason — None on success; a string on any drop or degrade (D-1:
                  type(exc).__name__ on internal error; "backtest_unavailable"
                  on infra-degrade).
        .tradeability_unverified — True only on infra-degrade.

    Never raises (D-1). Any garbage input or internal error degrades cleanly.
    """
    try:
        # --- Basic shape guard -------------------------------------------
        if not isinstance(plan, dict):
            return CompileResult(tree=None, reason="InvalidPlan")

        root_node = plan.get("root")
        name = plan.get("name", "")
        rebalance = plan.get("rebalance", "daily")

        # --- AC-17: market_cap is producer-deprecated --------------------
        # A market_cap node anywhere in the plan is unrepairable. Check before
        # any compilation so backtest_fn is NEVER called.
        if _has_market_cap(root_node):
            return CompileResult(
                tree=None,
                reason="market_cap_scheme_deprecated",
            )

        # --- Compile DSL -> Composer tree --------------------------------
        try:
            compiled_root = _compile_node(root_node)
        except Exception as exc:
            return CompileResult(tree=None, reason=type(exc).__name__)

        tree = symphony_schema.make_root(name, rebalance, [compiled_root])

        # --- validate_tree gate ------------------------------------------
        # A HARD-error tree NEVER reaches backtest_fn.
        errors = symphony_schema.validate_tree(tree)
        if errors:
            return CompileResult(tree=None, reason="validate_tree_hard_error")

        # --- No backtest seam provided: compile-only path ----------------
        if backtest_fn is None:
            return CompileResult(tree=tree, reason=None)

        # --- Bounded repair loop -----------------------------------------
        # Attempt backtest; on HTTP 400 (tradeability) prune the named ticker
        # and retry; on HTTP 422 (grammar) drop immediately; on an infra/
        # transport failure, degrade (emit the current validated tree
        # unverified) rather than drop — Composer is unreachable, not
        # rejecting, and dropping would silently zero the run on an outage.
        # The loop is bounded by MAX_REPAIR_ATTEMPTS (the test asserts
        # <= MAX + 1 calls).
        attempts = 0
        current_tree = tree

        while True:
            result = backtest_fn(current_tree)
            attempts += 1

            if result.error is None:
                # Success.
                return CompileResult(tree=current_tree, reason=None)

            # Parse the envelope: "HTTP {status}: {text}"
            envelope = result.error
            status = _parse_envelope_status(envelope)

            if _is_infra_failure(status):
                # Composer unreachable/overloaded, not rejecting. No extra
                # retry here — backtest_fn (composer_backtest_client.run_backtest)
                # already ran its own bounded exponential backoff before
                # returning this error, so retrying again would silently
                # stack a second unbounded-feeling retry layer on top.
                # current_tree is the last validated tree (initial, or
                # partially pruned if earlier attempts pruned a genuine
                # tradeability rejection) — that's exactly what's emitted.
                return CompileResult(
                    tree=current_tree,
                    reason="backtest_unavailable",
                    tradeability_unverified=True,
                )

            if status == 400:
                # Tradeability rejection: prune named in-tree ticker and retry.
                if attempts > MAX_REPAIR_ATTEMPTS:
                    return CompileResult(tree=None, reason="max_repair_attempts_exceeded")

                text_after_status = _envelope_text(envelope)
                tree_tickers = symphony_schema.extract_tickers(current_tree)
                ticker_to_prune = _find_prune_target(text_after_status, tree_tickers)

                if ticker_to_prune is None:
                    # No in-tree ticker identified: no productive prune possible.
                    # Drop cleanly without burning the remaining repair budget on
                    # no-op prunes (the ticker named is absent from the tree).
                    return CompileResult(tree=None, reason="no_in_tree_ticker_in_400")

                pruned = _prune_ticker_from_tree(current_tree, ticker_to_prune)
                if pruned is None:
                    return CompileResult(tree=None, reason="prune_degenerated_tree")

                # Validate the pruned tree before retrying.
                prune_errors = symphony_schema.validate_tree(pruned)
                if prune_errors:
                    return CompileResult(tree=None, reason="pruned_tree_invalid")

                current_tree = pruned
                # Loop continues: retry with pruned tree.

            else:
                # HTTP 422 grammar rejection (or any other status): unrepairable.
                # Do NOT blind-prune a ticker.
                return CompileResult(tree=None, reason=f"grammar_reject_{status}")

    except Exception as exc:
        # D-1: any unexpected internal error degrades cleanly.
        return CompileResult(tree=None, reason=type(exc).__name__)


# ---------------------------------------------------------------------------
# Envelope parsing helpers.
# ---------------------------------------------------------------------------


def _parse_envelope_status(envelope: str) -> int | None:
    """Extract the HTTP status code from an envelope string "HTTP {N}: {text}".

    Returns None if the envelope does not match the expected format.
    """
    if not isinstance(envelope, str):
        return None
    match = re.match(r"^HTTP (\d+):", envelope)
    if match:
        return int(match.group(1))
    return None


def _envelope_text(envelope: str) -> str:
    """Return the text portion of an envelope "HTTP {N}: {text}"."""
    colon_idx = envelope.find(": ")
    if colon_idx >= 0:
        return envelope[colon_idx + 2 :]
    return envelope
