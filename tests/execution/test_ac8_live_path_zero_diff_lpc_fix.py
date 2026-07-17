"""
AC-8 (no live-path regression) — the AC-1 lpc-stamping fix must produce
ZERO behavior change on the live execution path.

Ruled architecture (PM addendum @ debc9537, r1-engine's investigation): the
AC-1 fix lands ENTIRELY inside synthetic_history.build_replay_day; both
alpha_bot_execution.py and math_engine.py carry ZERO diff. This makes AC-8
STRUCTURAL — live import graph never reaches the changed code (synthetic_history.py
is never imported by alpha_bot_execution.py; confirmed by r1-engine's grep of
the whole repo's import graph and independently by test 1 below) — plus the
EMPIRICAL execution suite (this file's pins are targeted at the one seam
where a WRONG implementation could still leak: the two `current_holdings`
construction sites, which build_replay_day's fix must NOT touch).

WHY THIS SEAM MATTERS (not covered elsewhere): `bot_state[s_id]["current_holdings"]`
-- built ticker+allocation-only at alpha_bot_execution.py:888-894 and
:1557-1560 -- is read back on the LIVE path at :1191 (the "TRUE SHADOW
RETURN OVERRIDE" for an already-triggered symphony) into the LIVE
run_monte_carlo call at :1270 and into persisted bot_state[symphony_id]["mc_prob"].
A tempting-but-wrong AC-1 implementation could stamp last_percent_change
onto THIS shared cache (since it superficially looks like "the holdings
list" AC-1's plan text originally cited) instead of building a fresh,
replay-local copy inside build_replay_day. That would be a live-path
behavior diff (this cache's shape/consumers would change) AND wouldn't even
correctly satisfy AC-1 (a day-stale Composer snapshot value, not a real
per-tick one) -- see test_ma1_replay_per_tick_lpc_stamping.py's docstring
for the full architecture ruling this file enforces from the OTHER side.

Existing coverage this file does NOT duplicate:
  - tests/execution/test_h3_failopen_arming.py (36 tests, GREEN today) --
    the live path's H-3 fail-open arming behavior is already comprehensively
    pinned; if AC-1 stays confined to synthetic_history.py as ruled, this
    suite is untouched by construction and its continued GREEN status at
    cycle-complete IS the byte-identical-live-decisions regression check.
  - tests/execution/test_main_*.py, tests/execution/test_m4_*.py, etc. --
    the existing execution suite's PA-M4 comment
    ("prob_underperforming is computed against a fictional 0% baseline
    (exit already fired)") already documents that a triggered symphony's
    live mc_prob is a KNOWN-fictional, deliberately-quarantined value
    (excluded from mc_history, alpha_bot_execution.py:1349-1355) -- this
    file's job is to pin that it stays EXACTLY as fictional/unchanged as
    before, not to re-derive that it's fictional.

Per the alpha_bot_execution.py-touch rule (project CLAUDE.md), the FULL
tests/execution/ + engine suites must be run (by the PM/reviewer, not this
file re-running them) alongside this file at cycle-complete.
"""

from __future__ import annotations

import ast
import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_EXEC_PATH = _REPO_ROOT / "alpha_bot_execution.py"
_SYNTH_PATH = _REPO_ROOT / "synthetic_history.py"


def _exec_tree() -> ast.Module:
    return ast.parse(_EXEC_PATH.read_text(encoding="utf-8"), filename=str(_EXEC_PATH))


# ===========================================================================
# 1 — Structural: synthetic_history.py is never imported by
#     alpha_bot_execution.py (the import-graph half of "AC-8 is structural").
# ===========================================================================


def test_alpha_bot_execution_never_imports_synthetic_history() -> None:
    """r1-engine's investigation finding, re-verified independently: if
    synthetic_history (the module AC-1 patches) is never imported by
    alpha_bot_execution.py, the live path cannot reach build_replay_day's
    changed code by construction -- the strongest possible AC-8 guard.

    A future refactor that adds this import would re-open the exact blast
    radius this cycle's architecture ruling was designed to close.
    """
    tree = _exec_tree()
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".")[0])

    assert "synthetic_history" not in imported_modules, (
        "alpha_bot_execution.py now imports synthetic_history — this re-opens the "
        "exact live-path blast radius AC-8's architecture ruling (PM addendum @ "
        "debc9537) was designed to close. The AC-1 lpc-stamping fix must stay "
        "reachable ONLY from the replay path (autotuner.py + synthetic_history.py "
        "internals), never from the live 1-minute execution path."
    )


# ===========================================================================
# 2 — current_holdings construction sites: key-shape pin.
# ===========================================================================


def _find_dict_literals_in_list_comprehensions(tree: ast.Module) -> list[ast.Dict]:
    """Every `[{...} for h in ...]` list-comprehension dict literal in the
    module -- the shape the current_holdings construction sites use."""
    dicts: list[ast.Dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ListComp) and isinstance(node.elt, ast.Dict):
            dicts.append(node.elt)
    return dicts


def _dict_keys(node: ast.Dict) -> set[str]:
    keys: set[str] = set()
    for k in node.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys.add(k.value)
    return keys


def _find_current_holdings_assignments(tree: ast.Module) -> list[ast.ListComp]:
    """Locate every `bot_state[...]["current_holdings"] = [...]` assignment
    (the two construction sites at :888-894 and :1557-1560) via the
    assignment target's subscript key, not a hardcoded line number -- so
    this test survives reformatting/line-shift."""
    sites: list[ast.ListComp] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.ListComp):
            continue
        for tgt in node.targets:
            if not isinstance(tgt, ast.Subscript):
                continue
            # tgt is bot_state[s_id]["current_holdings"] -- Subscript(Subscript(...), "current_holdings")
            key_node = tgt.slice
            if isinstance(key_node, ast.Constant) and key_node.value == "current_holdings":
                sites.append(node.value)
    return sites


def test_current_holdings_construction_sites_exist() -> None:
    """Guard test: confirm both construction sites are still found via the
    subscript-key pattern. If this fires, the sites moved/were renamed and
    the key-shape test below has no target."""
    sites = _find_current_holdings_assignments(_exec_tree())
    assert len(sites) >= 2, (
        f"Expected >= 2 `bot_state[...]['current_holdings'] = [...]` construction "
        f"sites in alpha_bot_execution.py (the plan's :888-894 and :1557-1560), "
        f"found {len(sites)}. AC-8's key-shape pin cannot verify without them."
    )


def test_current_holdings_construction_sites_emit_ticker_allocation_only() -> None:
    """AC-8 core pin: both current_holdings construction sites must emit
    EXACTLY {"ticker", "allocation"} keys -- no "last_percent_change" or any
    other new key. current_holdings is read back on the LIVE path (:1191,
    triggered-symphony shadow override) into the live run_monte_carlo call
    (:1270); a new key here (e.g. a wrongly-placed lpc stamp) would be a
    live-path behavior diff.

    RED trigger: an AC-1 implementation that stamps last_percent_change (or
    any other field) onto these dicts instead of confining the fix to
    synthetic_history.build_replay_day's own fresh, replay-local copy.
    """
    sites = _find_current_holdings_assignments(_exec_tree())
    assert sites, "No current_holdings construction sites found — see the guard test above."

    expected_keys = {"ticker", "allocation"}
    for i, site in enumerate(sites):
        keys = _dict_keys(site.elt) if isinstance(site.elt, ast.Dict) else set()
        assert keys == expected_keys, (
            f"current_holdings construction site #{i} (line {getattr(site, 'lineno', '?')}) "
            f"emits keys {sorted(keys)}, expected exactly {sorted(expected_keys)}. "
            "A new key here means the shared live-path cache changed shape — the AC-1 "
            "lpc-stamping fix must be confined to synthetic_history.build_replay_day's "
            "own fresh holdings copy, never this construction site."
        )


# ===========================================================================
# 3 — Triggered-symphony live run_monte_carlo input: byte-identical shape.
# ===========================================================================


def test_live_run_monte_carlo_call_receives_holdings_variable_not_current_holdings_directly() -> None:
    """Documents and pins the exact data-flow AC-8 depends on: the live
    run_monte_carlo call (:1270) receives a local `holdings` variable that,
    for a triggered symphony, is REASSIGNED from
    bot_state[symphony_id]["current_holdings"] (:1191) -- so this file's
    key-shape pin (test 2 above) on current_holdings' CONSTRUCTION sites is
    exactly what transitively guards the live run_monte_carlo call's input
    shape for triggered symphonies too. This test pins that the reassignment
    line still exists (a future refactor removing the "TRUE SHADOW RETURN
    OVERRIDE" block would silently invalidate the transitive guarantee test
    2 relies on, without either test failing on its own).
    """
    src = _EXEC_PATH.read_text(encoding="utf-8")
    assert 'holdings = bot_state[symphony_id].get("current_holdings", [])' in src, (
        "The triggered-symphony 'TRUE SHADOW RETURN OVERRIDE' reassignment "
        "(holdings = bot_state[symphony_id].get(\"current_holdings\", [])) was not "
        "found verbatim in alpha_bot_execution.py. If this line moved or changed "
        "shape, test_current_holdings_construction_sites_emit_ticker_allocation_only "
        "no longer transitively guards the live run_monte_carlo call's input for "
        "triggered symphonies — re-verify the data-flow and update this test's "
        "docstring/assertion together."
    )


def test_fictional_mc_history_quarantine_comment_present() -> None:
    """Regression pin on the PA-M4 quarantine this file's docstring cites:
    a triggered symphony's live mc_prob (computed against 'a fictional 0%
    baseline') must stay excluded from mc_history. If this guard is ever
    removed, a day-stale/fictional value could pollute the pre-trigger
    arm/disarm signal buffer for OTHER symphonies -- unrelated to AC-1, but
    exactly the kind of live-path regression a careless AC-1 diff near this
    code could introduce.
    """
    src = _EXEC_PATH.read_text(encoding="utf-8")
    assert 'if mc_available and not bot_state[symphony_id]["triggered"]:' in src, (
        "The mc_history quarantine guard (mc_available and not triggered) was not "
        "found verbatim in alpha_bot_execution.py — a triggered symphony's "
        "fictional-baseline mc_prob could now pollute mc_history."
    )
