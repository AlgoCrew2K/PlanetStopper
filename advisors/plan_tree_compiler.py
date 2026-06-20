"""Plan->Tree Compiler — Component 3 of the real Strategy Builder (INERT RED STUB).

Deterministically compiles a build-plan DSL dict (the Component-2 generator output)
into a Composer ``raw_value`` decision tree using ONLY the existing
``advisors/symphony_schema.py`` constructors, then runs a bounded validate +
repair loop so only valid, tradeable trees reach the downstream pipeline.

This is the RED-phase INERT STUB: the public surface exists (so the tests collect
and import cleanly), but every behavioural function raises ``NotImplementedError``.
The implementer (sb3-impl) replaces the bodies in the GREEN phase, driven by the
RED tests — NOT by this docstring.

Public surface (pinned by the RED tests — the tests are authoritative)
---------------------------------------------------------------------
MAX_REPAIR_ATTEMPTS : int
    Named bound on the validate/tradeability repair loop (no magic number; never
    unbounded).

CompileResult : dataclass
    Container returned by compile_plan. Fields: .tree (dict | None — the compiled
    Composer raw_value tree, or None on a clean drop) and .reason (str | None — set
    on a drop, None on success). D-1: reason carries only type(exc).__name__ on an
    internal error.

compile_plan(plan, *, backtest_fn=None) -> CompileResult
    Compile a single build-plan into a Composer tree. The DSL NODE/CONDITION union
    dispatches 1:1 to symphony_schema constructors. validate_tree gates the result
    (a HARD-error tree NEVER reaches backtest). When backtest_fn is supplied, a
    bounded repair loop runs: a tradeability rejection (HTTP 400 envelope) prunes
    the named ticker and retries; a grammar rejection (HTTP 422 envelope) is NOT
    blind-ticker-pruned. market_cap-scheme plans are a producer-deprecated drop
    (Composer retired market-cap weighting — captured 2026-06-20). Never raises.

Design constraints (RED contract — for the implementer)
-------------------------------------------------------
- Compile via symphony_schema constructors ONLY (no hand-built node dicts).
- Determinism: same plan -> byte-identical tree modulo the fresh uuid `id` keys.
- D-1: never raises; failures degrade to (tree=None, reason set).
- Advisory-only: no LIVE_EXECUTION, no Composer write/deploy, no allowlist mutation.
- The error-envelope split parses the composer_backtest_client format
  "HTTP {status}: {text}" (composer_backtest_client.py:360) by STATUS CODE.
"""

from __future__ import annotations

from dataclasses import dataclass

# Bound on the validate/tradeability repair loop. RED stub value; the implementer
# confirms/adjusts within the test's asserted 1..10 range.
MAX_REPAIR_ATTEMPTS: int = 3


@dataclass
class CompileResult:
    """Result of compiling one build-plan. tree is None on a clean drop."""

    tree: dict | None = None
    reason: str | None = None


def compile_plan(plan, *, backtest_fn=None) -> CompileResult:
    """Compile a build-plan into a validated Composer tree (RED stub — raises)."""
    raise NotImplementedError("plan_tree_compiler.compile_plan is not implemented yet (RED phase).")
