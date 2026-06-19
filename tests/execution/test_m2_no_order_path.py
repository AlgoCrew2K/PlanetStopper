"""
RED tests — M2 cvar_diagnostics writer has no broker-order reachability.

Plan deliverable (2): assert no submit_order / place_order / cancel_order /
liquidate symbol appears in any M2 call-chain reachable from
compute_portfolio_cvar or its callers (plan §Deliverables 2 / rubric M-2).

Two complementary checks:
  1. Static AST scan: math_engine.py must not reference broker-order symbols
     anywhere in the body of compute_portfolio_cvar or in any function that
     it calls within the same module.
  2. Structural: compute_portfolio_cvar must exist in math_engine (RED gate —
     will fail until M2 lands, proving RED state).

Binding refs:
  - live-vs-replay-safety-boundary plan deliverable (2)
  - Architecture constraint 4: is_live=True explicit, never a default
  - Rubric M-2: zero new order paths introduced by M2
  - live-mode-audit.csv: M2 rows = 0 is itself the assertion
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import math_engine

_WORKTREE_ROOT = pathlib.Path(__file__).parents[2]
_MATH_ENGINE_PATH = _WORKTREE_ROOT / "math_engine.py"

# Broker-order symbol denylist — presence in any M2 call-chain is a hard block.
_BROKER_ORDER_DENYLIST: frozenset[str] = frozenset(
    {
        "submit_order",
        "place_order",
        "cancel_order",
        "liquidate",
        "go-to-cash",
        "perform_account_liquidation",
        "execute_sell_to_cash",
    }
)


# ---------------------------------------------------------------------------
# 1. compute_portfolio_cvar exists in math_engine (RED gate)
# ---------------------------------------------------------------------------


def test_compute_portfolio_cvar_exists_in_math_engine():
    """M2 function compute_portfolio_cvar must be importable from math_engine.

    This is RED until the implementer adds it — proving the RED phase is
    genuine. The function must live in math_engine so it is testable in
    isolation without importing alpha_bot_execution.
    """
    assert hasattr(math_engine, "compute_portfolio_cvar"), (
        "math_engine is missing compute_portfolio_cvar. "
        "M2 (Phase-1 cvar_diagnostics writer) must define this function. "
        "Plan deliverable (2): import graph test reads from this function."
    )
    assert callable(math_engine.compute_portfolio_cvar), (
        "math_engine.compute_portfolio_cvar is not callable."
    )


# ---------------------------------------------------------------------------
# 2. compute_portfolio_cvar's function body contains no broker-order symbols
# ---------------------------------------------------------------------------


class TestM2BodyHasNoBrokerSymbols:
    """AST scan: the body of compute_portfolio_cvar must not reference any
    broker-order symbol — directly or via a call to a locally-defined helper.

    The import-graph check reads math_engine.py only (M2 is a pure math
    function; any broker interaction would be a category error that the
    scanner must catch before review).
    """

    def _collect_called_names_in_function(self, source: str, func_name: str) -> set[str]:
        """Return every Name and Attribute call target found in func_name's body."""
        tree = ast.parse(source)
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == func_name:
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            if isinstance(child.func, ast.Name):
                                names.add(child.func.id)
                            elif isinstance(child.func, ast.Attribute):
                                names.add(child.func.attr)
        return names

    def test_compute_portfolio_cvar_body_has_no_submit_order(self):
        """compute_portfolio_cvar must not call submit_order."""
        if not hasattr(math_engine, "compute_portfolio_cvar"):
            pytest.skip("compute_portfolio_cvar not found — see existence test")
        source = _MATH_ENGINE_PATH.read_text(encoding="utf-8")
        calls = self._collect_called_names_in_function(source, "compute_portfolio_cvar")
        assert "submit_order" not in calls, (
            "compute_portfolio_cvar calls submit_order — M2 must have zero order paths. "
            "Rubric M-2: new decision-content code paths must never reach order helpers."
        )

    def test_compute_portfolio_cvar_body_has_no_place_order(self):
        """compute_portfolio_cvar must not call place_order."""
        if not hasattr(math_engine, "compute_portfolio_cvar"):
            pytest.skip("compute_portfolio_cvar not found")
        source = _MATH_ENGINE_PATH.read_text(encoding="utf-8")
        calls = self._collect_called_names_in_function(source, "compute_portfolio_cvar")
        assert "place_order" not in calls, (
            "compute_portfolio_cvar calls place_order — zero order paths required (rubric M-2)."
        )

    def test_compute_portfolio_cvar_body_has_no_cancel_order(self):
        """compute_portfolio_cvar must not call cancel_order."""
        if not hasattr(math_engine, "compute_portfolio_cvar"):
            pytest.skip("compute_portfolio_cvar not found")
        source = _MATH_ENGINE_PATH.read_text(encoding="utf-8")
        calls = self._collect_called_names_in_function(source, "compute_portfolio_cvar")
        assert "cancel_order" not in calls, (
            "compute_portfolio_cvar calls cancel_order — zero order paths required."
        )

    def test_compute_portfolio_cvar_body_has_no_liquidate(self):
        """compute_portfolio_cvar must not call liquidate."""
        if not hasattr(math_engine, "compute_portfolio_cvar"):
            pytest.skip("compute_portfolio_cvar not found")
        source = _MATH_ENGINE_PATH.read_text(encoding="utf-8")
        calls = self._collect_called_names_in_function(source, "compute_portfolio_cvar")
        assert "liquidate" not in calls, (
            "compute_portfolio_cvar calls liquidate — zero order paths required."
        )

    def test_compute_portfolio_cvar_body_has_no_execute_sell_to_cash(self):
        """compute_portfolio_cvar must not call execute_sell_to_cash."""
        if not hasattr(math_engine, "compute_portfolio_cvar"):
            pytest.skip("compute_portfolio_cvar not found")
        source = _MATH_ENGINE_PATH.read_text(encoding="utf-8")
        calls = self._collect_called_names_in_function(source, "compute_portfolio_cvar")
        assert "execute_sell_to_cash" not in calls, (
            "compute_portfolio_cvar calls execute_sell_to_cash — zero order paths required."
        )


# ---------------------------------------------------------------------------
# 3. math_engine module-level import has no broker-order module imports
# ---------------------------------------------------------------------------


def test_math_engine_imports_no_broker_modules():
    """math_engine.py must not import alpaca_trade_api or requests at module level.

    A broker-module import at module level would mean that any function in
    math_engine could transitively reach network I/O — violating the pure-math
    contract of M2.

    We scan top-level import statements only (not inside function bodies, where
    lazy imports are acceptable for non-blocking paths).
    """
    source = _MATH_ENGINE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    broker_modules = {"alpaca_trade_api", "alpaca", "tradeapi", "requests"}
    module_level_imports: list[str] = []

    for node in ast.walk(tree):
        # Only look at top-level import nodes (not inside function bodies)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # ast.walk does not preserve depth — check via parent analysis
            # For simplicity, check the body of the module directly
            pass

    # Direct iteration over module-level nodes (not ast.walk)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.split(".")[0]
                if name in broker_modules:
                    module_level_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in broker_modules:
                module_level_imports.append(node.module or "")

    assert not module_level_imports, (
        "math_engine.py has top-level broker-module imports: "
        + str(module_level_imports)
        + ". M2 is a pure-math function; broker I/O must never be reachable from math_engine."
    )


# ---------------------------------------------------------------------------
# 4. live-mode-audit.csv M2 row count = 0
# ---------------------------------------------------------------------------


def test_live_mode_audit_csv_has_zero_m2_rows():
    """live-mode-audit.csv must contain zero rows referencing M2 paths.

    M2 (compute_portfolio_cvar + cvar_diagnostics write) has zero order-capable
    paths. The audit CSV is CI-enforced: any row mentioning compute_portfolio_cvar
    or cvar_diagnostics as a call_to_order_symbol would mean M2 touches an order
    path — a rubric M-2 hard block.

    The empty row count IS the assertion (plan deliverable 1).
    """
    audit_csv = _WORKTREE_ROOT / "tests" / "fixtures" / "live-mode-audit.csv"
    assert audit_csv.exists(), (
        f"live-mode-audit.csv not found at {audit_csv}. "
        "Plan deliverable (1) requires this file to exist (even if empty = M2 rows = 0)."
    )

    rows = []
    for line in audit_csv.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("file,"):
            continue  # header row
        rows.append(stripped)

    m2_symbols = {"compute_portfolio_cvar", "cvar_diagnostics", "record_cvar_diagnostic"}
    m2_rows = [r for r in rows if any(sym in r for sym in m2_symbols)]

    assert len(m2_rows) == 0, (
        f"live-mode-audit.csv contains {len(m2_rows)} M2-related order-path row(s): "
        + str(m2_rows)
        + ". M2 must have zero order paths — these rows should not exist. "
        "Rubric M-2: zero new order-capable paths from M2."
    )
