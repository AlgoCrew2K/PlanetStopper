"""
RED tests — AC-15: whole-repo containment invariant for the sleeves order path.

Two independent hazards, both scanned repo-wide (excluding tests/, docs/,
archives, and non-source dirs):

  1. Broker order-endpoint reachability: Alpaca order-placing symbols
     (submit_bracket_order, submit_trailing_stop_order, cancel_order,
     close_position, the literal `/v2/orders` endpoint path) may exist
     ONLY inside sleeves/alpaca_orders.py — THE single designated order
     module (plan Architecture section). No other production module —
     not sleeves/envelope.py, not sleeves/ledger.py, not app.py, not
     alpha_bot_execution.py — may define or call these.

  2. Live-host string denylist: the bare live trading host
     "api.alpaca.markets" (as opposed to "paper-api.alpaca.markets") must
     appear nowhere outside sleeves/alpaca_orders.py's single gated
     `resolve_host` function (or the module-level constant it reads).

Plus a regression canary: the EXISTING no-order-path tests
(tests/execution/test_m2_no_order_path.py, tests/app/test_dashboard_no_order_path.py)
and the port-removal guard tests must remain present and non-trivial —
this cycle's new sleeves/ package must never be the reason an old
containment guard gets weakened or deleted (Decisions: "Existing
m2/dashboard denylist tests stay untouched; containment via NEW whole-repo
invariant").

This file requires NO sleeves module to exist to run its negative checks
(a repo with zero sleeves/ files trivially has zero violations) — the
positive existence checks are what proves genuine RED at authoring time.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_WORKTREE_ROOT = Path(__file__).resolve().parents[2]
_ORDER_MODULE_PATH = _WORKTREE_ROOT / "sleeves" / "alpaca_orders.py"

_BROKER_ORDER_SYMBOLS: frozenset[str] = frozenset(
    {
        "submit_bracket_order",
        "submit_trailing_stop_order",
        "submit_order",
        "place_order",
        "cancel_order",
        "close_position",
        "liquidate_position",
    }
)

_ORDER_ENDPOINT_TOKEN = "/v2/orders"

_LIVE_HOST_RE = re.compile(r"(?<!paper-)api\.alpaca\.markets")

# Pre-existing prose-only mentions of the live host string, verified to be
# comment/docstring explanations (never a constructed URL / network call).
# advisors/universe_provider.py hardcodes ALPACA_TRADING_BASE_URL to the
# PAPER host and only mentions "api.alpaca.markets" in prose explaining why
# (paper keys 401 on the live host). Verified via grep before allowlisting —
# zero `requests.*` call sites in that file reference the live host.
_LIVE_HOST_MENTION_ALLOWLIST: frozenset[str] = frozenset({"advisors/universe_provider.py"})

# Directories excluded from the repo-wide scan: test code (which legitimately
# names these symbols in mocks/fixtures/assertions), docs/archives (prose
# examples), and non-source directories.
_SCAN_EXCLUDE_DIRS = {
    "tests",
    ".git",
    ".design-handoff",
    "docs",
    "doc-archive",
    "node_modules",
    "static",
    "templates",
    "migrations",
    ".claude",
    "scripts",
}


def _iter_python_files():
    for path in _WORKTREE_ROOT.rglob("*.py"):
        rel_parts = path.relative_to(_WORKTREE_ROOT).parts
        if any(part in _SCAN_EXCLUDE_DIRS for part in rel_parts):
            continue
        yield path


# ---------------------------------------------------------------------------
# 1. The designated order module must exist and actually place orders
#    (otherwise the containment scan below would be vacuously true)
# ---------------------------------------------------------------------------


class TestOrderModuleExistsAndIsGenuine:
    def test_alpaca_orders_module_exists(self):
        assert _ORDER_MODULE_PATH.exists(), (
            f"sleeves/alpaca_orders.py not found at {_ORDER_MODULE_PATH}. "
            "P1 requires THE single order-capable module to exist before the "
            "whole-repo containment invariant means anything."
        )

    def test_alpaca_orders_module_genuinely_places_orders(self):
        if not _ORDER_MODULE_PATH.exists():
            pytest.skip("alpaca_orders.py not found — see existence test")
        source = _ORDER_MODULE_PATH.read_text(encoding="utf-8")
        assert _ORDER_ENDPOINT_TOKEN in source, (
            "sleeves/alpaca_orders.py does not reference /v2/orders anywhere — "
            "the containment scan needs a genuine order-placing module to guard, "
            "not an empty stub."
        )
        tree = ast.parse(source)
        defined_names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert defined_names & _BROKER_ORDER_SYMBOLS, (
            f"sleeves/alpaca_orders.py defines none of the expected order symbols "
            f"{sorted(_BROKER_ORDER_SYMBOLS)}; found: {sorted(defined_names)}"
        )


# ---------------------------------------------------------------------------
# 2. No other production module may define or reference order-placing symbols
# ---------------------------------------------------------------------------


class TestNoOtherModuleReachesOrderEndpoints:
    def test_no_python_file_outside_alpaca_orders_references_v2_orders_endpoint(self):
        offenders = []
        for path in _iter_python_files():
            if path == _ORDER_MODULE_PATH:
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
            if _ORDER_ENDPOINT_TOKEN in source:
                offenders.append(str(path.relative_to(_WORKTREE_ROOT)))
        assert not offenders, (
            f"/v2/orders referenced outside sleeves/alpaca_orders.py in: {offenders}. "
            "THE single order-capable module invariant is violated (AC-15)."
        )

    def test_no_python_file_outside_alpaca_orders_defines_broker_order_functions(self):
        offenders = []
        for path in _iter_python_files():
            if path == _ORDER_MODULE_PATH:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name in _BROKER_ORDER_SYMBOLS
                ):
                    offenders.append(f"{path.relative_to(_WORKTREE_ROOT)}::{node.name}")
        assert not offenders, (
            f"broker order-placing function(s) defined outside sleeves/alpaca_orders.py: "
            f"{offenders}. Only sleeves/alpaca_orders.py may define these (AC-15)."
        )

    def test_sleeves_sibling_modules_do_not_define_order_functions(self):
        """
        Narrower, sharper version of the check above: explicitly probe the
        OTHER sleeves/ modules (envelope, sizing, ledger, reconciliation) —
        the most tempting place for a shortcut ("just call requests.post
        directly from envelope.py to save a function call") to leak in.
        """
        sibling_modules = [
            _WORKTREE_ROOT / "sleeves" / "envelope.py",
            _WORKTREE_ROOT / "sleeves" / "sizing.py",
            _WORKTREE_ROOT / "sleeves" / "ledger.py",
            _WORKTREE_ROOT / "sleeves" / "reconciliation.py",
        ]
        offenders = []
        for path in sibling_modules:
            if not path.exists():
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
            if _ORDER_ENDPOINT_TOKEN in source:
                offenders.append(str(path.relative_to(_WORKTREE_ROOT)))
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name in _BROKER_ORDER_SYMBOLS
                ):
                    offenders.append(f"{path.relative_to(_WORKTREE_ROOT)}::{node.name}")
        assert not offenders, f"sleeves sibling module(s) leak order-placing code: {offenders}"


# ---------------------------------------------------------------------------
# 3. Live-host string denylist
# ---------------------------------------------------------------------------


class TestLiveHostStringDenylist:
    def test_live_host_string_appears_nowhere_outside_alpaca_orders_module(self):
        offenders = []
        for path in _iter_python_files():
            if path == _ORDER_MODULE_PATH:
                continue
            rel = str(path.relative_to(_WORKTREE_ROOT)).replace("\\", "/")
            if rel in _LIVE_HOST_MENTION_ALLOWLIST:
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
            if _LIVE_HOST_RE.search(source):
                offenders.append(rel)
        assert not offenders, (
            f"live trading host string 'api.alpaca.markets' found outside "
            f"sleeves/alpaca_orders.py in: {offenders}. The live host must be "
            f"reachable only through the single gated resolve_host function (AC-15)."
        )

    def test_live_host_string_within_alpaca_orders_confined_to_resolve_host_or_constant(self):
        if not _ORDER_MODULE_PATH.exists():
            pytest.skip("alpaca_orders.py not found — see existence test")
        source = _ORDER_MODULE_PATH.read_text(encoding="utf-8")
        lines = source.splitlines()
        tree = ast.parse(source)

        resolve_host_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_host":
                end = getattr(node, "end_lineno", node.lineno)
                resolve_host_lines = set(range(node.lineno, end + 1))

        module_const_lines: set[int] = set()
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                module_const_lines.add(node.lineno)

        offending_lines = []
        for i, line in enumerate(lines, start=1):
            if _LIVE_HOST_RE.search(line):
                if i in resolve_host_lines or i in module_const_lines:
                    continue
                offending_lines.append((i, line.strip()))

        assert not offending_lines, (
            f"live host string appears outside resolve_host()/module constants in "
            f"sleeves/alpaca_orders.py at lines: {offending_lines}. AC-14/AC-15 require "
            f"the live host to be reachable through exactly one gated function."
        )

    def test_allowlisted_live_host_mentions_are_prose_only_never_a_network_call(self):
        """
        Keeps _LIVE_HOST_MENTION_ALLOWLIST honest: every allowlisted file must
        have zero lines that BOTH mention the live host token AND look like a
        network call construction (requests.*, a URL assignment, or an
        f-string interpolation). If a future edit turns a comment into a real
        call, this test must fail even though the bare-string scan above is
        silenced by the allowlist.
        """
        call_shape_re = re.compile(r"requests\.\w+\s*\(|=\s*[\"']https?://|f[\"']https?://")
        for rel in _LIVE_HOST_MENTION_ALLOWLIST:
            path = _WORKTREE_ROOT / rel
            if not path.exists():
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1
            ):
                if _LIVE_HOST_RE.search(line):
                    assert not call_shape_re.search(line), (
                        f"{rel}:{lineno} mentions the live host AND looks like a network "
                        f"call — the prose-only allowlist exception no longer holds: {line.strip()!r}"
                    )

    def test_resolve_host_function_exists_and_is_the_single_gate(self):
        if not _ORDER_MODULE_PATH.exists():
            pytest.skip("alpaca_orders.py not found — see existence test")
        tree = ast.parse(_ORDER_MODULE_PATH.read_text(encoding="utf-8"))
        function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assert "resolve_host" in function_names, (
            "sleeves/alpaca_orders.py must define resolve_host() — the single "
            "designated host-selection function (Config section of the plan)."
        )


# ---------------------------------------------------------------------------
# 4. Regression canary — existing containment guards must survive this cycle
# ---------------------------------------------------------------------------


class TestExistingContainmentGuardsUntouched:
    _CANARY_FILES = [
        "tests/execution/test_m2_no_order_path.py",
        "tests/app/test_dashboard_no_order_path.py",
        "tests/execution/test_orphan_port_modules_removed.py",
        "tests/execution/test_port_dispatch_removal.py",
        "tests/execution/test_port_engine_module_removal.py",
    ]

    @pytest.mark.parametrize("rel_path", _CANARY_FILES)
    def test_canary_file_still_exists(self, rel_path):
        path = _WORKTREE_ROOT / rel_path
        assert path.exists(), (
            f"{rel_path} was removed or renamed. Per the plan Decisions table, "
            f"'existing m2/dashboard denylist tests stay untouched; containment "
            f"via NEW whole-repo invariant' — this file must never be deleted "
            f"as part of adding the sleeves order path."
        )

    @pytest.mark.parametrize("rel_path", _CANARY_FILES)
    def test_canary_file_is_not_gutted(self, rel_path):
        path = _WORKTREE_ROOT / rel_path
        if not path.exists():
            pytest.skip("see existence test")
        source = path.read_text(encoding="utf-8", errors="ignore")
        test_function_count = source.count("def test_")
        assert test_function_count >= 3, (
            f"{rel_path} has only {test_function_count} test function(s) — "
            f"suspiciously thin for a containment guard file; verify it was not "
            f"gutted while adding the sleeves order path."
        )
