"""
RED meta-tests — Sprint 3 final close: zero skipped, zero xfailed tests.

WHY THIS FILE EXISTS
--------------------
User mandate (2026-05-27): the test suite at PM completion of Sprint 3 must
report zero ``skipped`` and zero ``xfailed`` entries (excluding the
``--include-live`` deselected files, which is project policy, not a skip).

This meta-suite pins the post-cleanup contract for ten specific test entries
that were previously skipped or xfailed. Each entry is one of:

  (a) A skip whose pre-condition can no longer be reproduced from the current
      code state (test scenario is obsolete) — assertion: the corresponding
      node id no longer collects under pytest --collect-only.
  (b) A skip parametrised over a deleted production module — assertion: the
      module filename is no longer present in the parametrize argument list,
      so the [engine/dual_altitude.py] parametrization is gone.
  (c) An xfail tombstone declaring a future deletion that is now complete —
      assertion: the xfail marker is gone and the test asserts the deletion
      is permanent via pytest.raises(ImportError) / hasattr() == False.
  (d) An xfail asserting Phase-2 behaviour that is out of scope per binding
      decisions (DECISIONS.md, feature-plans/decision-science/README.md §0) —
      assertion: the test no longer collects (the node id is absent).

WHAT IS ASSERTED
----------------
M1. tests/database/test_022_spec_bundles_add_id.py
    The three node ids gated on the unreproducible ``pre_existing_db`` scenario
    (T22 / T23 / T25) no longer collect. Implementation: delete the fixture
    and the three tests it gated.

M2. tests/integration/test_run_monte_carlo_consumers_enumerated.py
    The parametrized node id
    ``test_run_monte_carlo_call_count_matches_baseline[engine/dual_altitude.py]``
    no longer collects. Implementation: remove "engine/dual_altitude.py" from
    _PRODUCTION_FILES and _BASELINE_CALL_SITES.

M3. tests/math_engine/test_cvar_assessment_dataclass_contract.py
    The parametrized node id
    ``test_phase1_production_module_does_not_import_cvar_assessment[engine/dual_altitude.py]``
    no longer collects. Implementation: remove "engine/dual_altitude.py" from
    _PRODUCTION_MODULES.

M4. tests/execution/test_port_dispatch_removal.py
    The four ``TestPortmodeTestFilesToBeDeleted`` xfail tombstones have been
    converted to plain pass-tests that assert
    ``pytest.raises((ImportError, ModuleNotFoundError))`` (or AttributeError for
    the now-removed symbol on a surviving module). No ``@pytest.mark.xfail``
    decorator remains on this class.

M5. tests/math_engine/test_cvar_assessment_fail_safe_invariant.py
    The Phase-2 monotonicity placeholder
    ``test_property_deeper_tail_loss_never_has_fewer_observations_xfail_phase1``
    no longer collects. Implementation: delete the test entirely (Phase-2
    simulate_forward_paths is forbidden per the decision-science binding
    decisions; the placeholder is permanent dead weight).

M6. Whole-tree zero-skip / zero-xfail / zero-xpass guard.
    Running the full default pytest with -rN shows none of the ten removed
    node ids in the short-summary or collection output.

RED STATUS
----------
M1-M5 each go RED against the pre-cleanup tree (the node ids collect; the
xfail markers are present). They go GREEN once impl-final lands the
deletions and conversions.

M6 is the acceptance gate: it runs pytest --collect-only at runtime and
asserts that no node id from the ten-entry pre-cleanup list collects, and
that no ``xfail`` decorator survives on TestPortmodeTestFilesToBeDeleted.

NO PRODUCER-COMPUTED VALUES
---------------------------
All assertions are structural (node id presence, parametrize-list membership,
decorator presence). No floats, no hardcoded counts derived from math.

NO SKIP / XFAIL MARKERS IN THIS FILE
------------------------------------
By construction. If you find yourself wanting to ``pytest.skip`` here, you
are restating the bug that this file exists to forbid.
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import sys
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent

_FILE_022 = _PROJECT_ROOT / "tests" / "database" / "test_022_spec_bundles_add_id.py"
_FILE_MC_CONSUMERS = (
    _PROJECT_ROOT / "tests" / "integration" / "test_run_monte_carlo_consumers_enumerated.py"
)
_FILE_CVAR_CONTRACT = (
    _PROJECT_ROOT / "tests" / "math_engine" / "test_cvar_assessment_dataclass_contract.py"
)
_FILE_PORT_DISPATCH = (
    _PROJECT_ROOT / "tests" / "execution" / "test_port_dispatch_removal.py"
)
_FILE_CVAR_FAILSAFE = (
    _PROJECT_ROOT / "tests" / "math_engine" / "test_cvar_assessment_fail_safe_invariant.py"
)


def _ast(path: pathlib.Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _toplevel_function_names(tree: ast.AST) -> set[str]:
    return {
        node.name
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _class_method_names(tree: ast.AST, class_name: str) -> set[str]:
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                inner.name
                for inner in ast.iter_child_nodes(node)
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return set()


# ---------------------------------------------------------------------------
# M1 — three obsolete pre_existing_db tests deleted
# ---------------------------------------------------------------------------


_OBSOLETE_022_TESTS = (
    "test_022_materialises_id_column_on_preexisting_db",
    "test_022_backfills_id_for_existing_rows",
    "test_validate_nn1_compliance_does_not_raise_no_such_column_on_preexisting_db",
)


@pytest.mark.parametrize("func_name", _OBSOLETE_022_TESTS)
def test_obsolete_022_preexisting_db_test_is_deleted(func_name: str) -> None:
    """
    The three migration-022 tests gated on the ``pre_existing_db`` fixture
    are obsolete in the current code state — migration 016 creates the id
    column at CREATE TABLE time, so the fixture's "id absent after 016"
    pre-condition cannot be reproduced. The fixture pytest.skip()s and the
    three tests never execute. Delete them — coverage of the additive 022
    migration is retained via T20/T21/T24/T26/T27 in the same file.
    """
    tree = _ast(_FILE_022)
    names = _toplevel_function_names(tree)
    assert func_name not in names, (
        f"{_FILE_022.name} still defines {func_name!r}; the test is gated on the "
        f"unreproducible pre_existing_db scenario. Delete the function and the "
        f"pre_existing_db fixture entirely; the additive-migration contract is "
        f"already covered by the remaining T20/T21/T24/T26/T27 tests in this file."
    )


def test_022_file_no_pre_existing_db_fixture() -> None:
    """
    The ``pre_existing_db`` fixture itself must be deleted along with its three
    consumers. Leaving the fixture behind would invite re-introduction of the
    same skip.
    """
    tree = _ast(_FILE_022)
    names = _toplevel_function_names(tree)
    assert "pre_existing_db" not in names, (
        "pre_existing_db fixture remains in test_022_spec_bundles_add_id.py. "
        "Delete the fixture body — its three consumers are obsolete and the "
        "fixture itself contains the inline pytest.skip() that is the source of "
        "three of the ten skip markers this cycle exists to remove."
    )


def test_022_file_contains_no_pytest_skip_calls() -> None:
    """
    After the cleanup, no ``pytest.skip(`` call may remain in the 022 test
    file. The only call before the cleanup is inside the pre_existing_db
    fixture; once the fixture is deleted, the file must be skip-free.
    """
    source = _FILE_022.read_text(encoding="utf-8")
    assert "pytest.skip(" not in source, (
        f"{_FILE_022.name} still contains a pytest.skip() call. The cleanup must "
        f"remove every skip from this file (only the pre_existing_db fixture had one)."
    )


# ---------------------------------------------------------------------------
# M2 — engine/dual_altitude.py parametrization removed from MC-consumers test
# ---------------------------------------------------------------------------


def test_mc_consumers_production_files_excludes_dual_altitude() -> None:
    """
    The ``_PRODUCTION_FILES`` constant in the MC-consumers enumeration test
    must not include ``engine/dual_altitude.py``; the module is deleted, the
    inline ``pytest.skip(`` in ``_read_production`` fires unconditionally for
    it, and the parametrised node id has been skipped every run since SITE-C1.
    """
    source = _FILE_MC_CONSUMERS.read_text(encoding="utf-8")
    # Hit the constant declaration block (covers both _PRODUCTION_FILES and the
    # _BASELINE_CALL_SITES dict — the same string must not appear in either).
    assert "engine/dual_altitude.py" not in source, (
        f"{_FILE_MC_CONSUMERS.name} still references 'engine/dual_altitude.py'. "
        f"Remove the entry from both _PRODUCTION_FILES (list) and "
        f"_BASELINE_CALL_SITES (dict). The module is permanently deleted "
        f"(see tests/execution/test_port_engine_module_removal.py) and the "
        f"parametrized node id has only ever produced a SKIP."
    )


def test_mc_consumers_file_contains_no_pytest_skip_calls() -> None:
    """
    The remaining ``pytest.skip(`` call in ``_read_production`` was a defensive
    branch for the only-now-missing dual_altitude.py file. With that entry gone,
    the skip-emitting branch is dead and must be deleted so it cannot be
    reactivated by a future regression that re-adds a missing-file entry.
    """
    source = _FILE_MC_CONSUMERS.read_text(encoding="utf-8")
    assert "pytest.skip(" not in source, (
        f"{_FILE_MC_CONSUMERS.name} still contains a pytest.skip() call. "
        f"Delete the defensive branch in _read_production now that every "
        f"entry in _PRODUCTION_FILES is guaranteed to exist on disk."
    )


# ---------------------------------------------------------------------------
# M3 — engine/dual_altitude.py parametrization removed from CVaR contract test
# ---------------------------------------------------------------------------


def test_cvar_contract_production_modules_excludes_dual_altitude() -> None:
    """
    The ``_PRODUCTION_MODULES`` constant in the CVaR contract test must not
    include ``engine/dual_altitude.py``. The parametrized node id has been
    skipped every run since SITE-C1.
    """
    source = _FILE_CVAR_CONTRACT.read_text(encoding="utf-8")
    assert "engine/dual_altitude.py" not in source, (
        f"{_FILE_CVAR_CONTRACT.name} still references 'engine/dual_altitude.py' "
        f"in _PRODUCTION_MODULES. Remove it — the file is permanently deleted."
    )


def test_cvar_contract_file_contains_no_pytest_skip_calls() -> None:
    """
    Same dead-defensive-branch argument as M2 — remove the missing-file skip
    branch from the CVaR contract file.
    """
    source = _FILE_CVAR_CONTRACT.read_text(encoding="utf-8")
    assert "pytest.skip(" not in source, (
        f"{_FILE_CVAR_CONTRACT.name} still contains a pytest.skip() call. "
        f"Delete the defensive missing-file branch in "
        f"test_phase1_production_module_does_not_import_cvar_assessment."
    )


# ---------------------------------------------------------------------------
# M4 — TestPortmodeTestFilesToBeDeleted xfail tombstones converted to passing
#       assertions about the permanence of the deletions.
# ---------------------------------------------------------------------------


_TOMBSTONE_CLASS = "TestPortmodeTestFilesToBeDeleted"

_TOMBSTONE_METHODS = (
    "test_port_aggregator_import_fails_after_removal",
    "test_port_selector_import_fails_after_removal",
    "test_dual_altitude_initialize_import_fails_after_removal",
    "test_exit_authority_get_exit_authority_import_fails_after_removal",
)


@pytest.mark.parametrize("method_name", _TOMBSTONE_METHODS)
def test_port_dispatch_tombstone_method_no_longer_xfailed(method_name: str) -> None:
    """
    Each tombstone method in TestPortmodeTestFilesToBeDeleted must no longer
    carry an ``@pytest.mark.xfail`` decorator. The deletions are complete
    (SITE-A2 + SITE-C1 — confirmed by tests/execution/test_port_engine_module_removal.py
    and tests/execution/test_orphan_port_modules_removed.py); the tombstones
    should now be plain pass-tests asserting ImportError / ModuleNotFoundError
    via ``pytest.raises``.
    """
    tree = _ast(_FILE_PORT_DISPATCH)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and node.name == _TOMBSTONE_CLASS:
            for inner in ast.iter_child_nodes(node):
                if (
                    isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and inner.name == method_name
                ):
                    for deco in inner.decorator_list:
                        # Match ``@pytest.mark.xfail(...)`` regardless of args.
                        if isinstance(deco, ast.Call):
                            target = deco.func
                        else:
                            target = deco
                        # Walk the attribute chain to its base name(s).
                        attr_chain: list[str] = []
                        cur = target
                        while isinstance(cur, ast.Attribute):
                            attr_chain.append(cur.attr)
                            cur = cur.value
                        if isinstance(cur, ast.Name):
                            attr_chain.append(cur.id)
                        attr_chain.reverse()
                        if attr_chain[-1:] == ["xfail"]:
                            pytest.fail(
                                f"{_FILE_PORT_DISPATCH.name}::{_TOMBSTONE_CLASS}::"
                                f"{method_name} still carries an "
                                f"@pytest.mark.xfail decorator. Convert it to a "
                                f"plain assertion using pytest.raises("
                                f"(ImportError, ModuleNotFoundError)) — the "
                                f"deletion is permanent."
                            )
                    return
    pytest.fail(
        f"Could not locate {_TOMBSTONE_CLASS}::{method_name} in "
        f"{_FILE_PORT_DISPATCH.name}. If the class was renamed or restructured, "
        f"update this meta-test."
    )


def test_port_dispatch_tombstone_class_uses_pytest_raises() -> None:
    """
    After conversion, each tombstone method body must call ``pytest.raises``
    (with ImportError / ModuleNotFoundError / AttributeError) — otherwise the
    deletion-permanence assertion is missing and the test is content-free.
    """
    tree = _ast(_FILE_PORT_DISPATCH)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and node.name == _TOMBSTONE_CLASS:
            for inner in ast.iter_child_nodes(node):
                if (
                    isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and inner.name in _TOMBSTONE_METHODS
                ):
                    body_src = ast.unparse(inner)
                    assert "pytest.raises(" in body_src, (
                        f"{_TOMBSTONE_CLASS}::{inner.name} must assert the "
                        f"deletion via pytest.raises((ImportError, "
                        f"ModuleNotFoundError)). Current body: {body_src!r}"
                    )
            return
    pytest.fail(f"Class {_TOMBSTONE_CLASS} not found in {_FILE_PORT_DISPATCH.name}.")


# ---------------------------------------------------------------------------
# M5 — Phase-2 monotonicity placeholder deleted
# ---------------------------------------------------------------------------


def test_phase2_monotonicity_placeholder_deleted() -> None:
    """
    ``test_property_deeper_tail_loss_never_has_fewer_observations_xfail_phase1``
    is an xfail placeholder for ``simulate_forward_paths`` — a Phase-2
    deliverable that is FORBIDDEN per the decision-science council binding
    decisions (DECISIONS.md, feature-plans/decision-science/README.md §0).
    The placeholder will never go live in this build and must be deleted.
    """
    tree = _ast(_FILE_CVAR_FAILSAFE)
    names = _toplevel_function_names(tree)
    assert "test_property_deeper_tail_loss_never_has_fewer_observations_xfail_phase1" not in names, (
        f"{_FILE_CVAR_FAILSAFE.name} still defines the Phase-2 monotonicity "
        f"xfail placeholder. Phase 2 (simulate_forward_paths) is FORBIDDEN per "
        f"the decision-science binding decisions; the placeholder is permanent "
        f"dead weight. Delete the test."
    )


def test_failsafe_file_contains_no_xfail_markers() -> None:
    """
    After deletion, no ``@pytest.mark.xfail`` may remain in the property
    test file.
    """
    source = _FILE_CVAR_FAILSAFE.read_text(encoding="utf-8")
    assert "pytest.mark.xfail" not in source, (
        f"{_FILE_CVAR_FAILSAFE.name} still contains a pytest.mark.xfail "
        f"reference. The Phase-2 placeholder was the only xfail in this file; "
        f"deleting the test removes the marker."
    )


# ---------------------------------------------------------------------------
# M6 — acceptance gate: run pytest --collect-only and forbid the 10 node ids
# ---------------------------------------------------------------------------


_FORBIDDEN_NODE_IDS = (
    # Skip-1/2/3 — pre_existing_db scenario obsolete
    "tests/database/test_022_spec_bundles_add_id.py::test_022_materialises_id_column_on_preexisting_db",
    "tests/database/test_022_spec_bundles_add_id.py::test_022_backfills_id_for_existing_rows",
    "tests/database/test_022_spec_bundles_add_id.py::test_validate_nn1_compliance_does_not_raise_no_such_column_on_preexisting_db",
    # Skip-4 — parametrize entry on deleted module
    "tests/integration/test_run_monte_carlo_consumers_enumerated.py::test_run_monte_carlo_call_count_matches_baseline[engine/dual_altitude.py]",
    # Skip-5 — parametrize entry on deleted module
    "tests/math_engine/test_cvar_assessment_dataclass_contract.py::test_phase1_production_module_does_not_import_cvar_assessment[engine/dual_altitude.py]",
    # Xfail-5 — Phase-2 placeholder
    "tests/math_engine/test_cvar_assessment_fail_safe_invariant.py::test_property_deeper_tail_loss_never_has_fewer_observations_xfail_phase1",
    # Xfail-1/2/3/4 — these node IDs SURVIVE after conversion (they become PASS
    # tests rather than xfails). The acceptance check for them is the absence
    # of xfail markers (covered by test_port_dispatch_tombstone_method_no_longer_xfailed)
    # and not their disappearance from --collect-only.
)


def _collected_node_ids() -> list[str]:
    """
    Run ``pytest --collect-only -q`` in the project root and return the list
    of collected node ids (one per line). Excludes summary lines.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    # pytest --collect-only -q prints one node id per line, then a blank line
    # plus a summary like "1234 tests collected in 5.67s". Drop the summary.
    lines = []
    for ln in proc.stdout.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        # Skip summary / warnings lines.
        if re.match(r"^\d+\s+(tests?|errors?)\s+", ln):
            continue
        if ln.startswith("=") or ln.startswith("warnings summary"):
            continue
        lines.append(ln)
    return lines


def test_pytest_collect_only_does_not_emit_any_forbidden_node_id() -> None:
    """
    The headline acceptance gate. Run ``pytest --collect-only`` in the project
    root and assert none of the six "must-disappear" node ids from the
    ten-entry cleanup list are present. The four xfail-tombstone node ids in
    test_port_dispatch_removal.py survive intentionally (they convert from
    xfail to PASS); their cleanup is verified by M4 instead.
    """
    collected = _collected_node_ids()
    # Normalise path separators for cross-platform reliability.
    collected_norm = {ln.replace("\\", "/") for ln in collected}
    violations = sorted(nid for nid in _FORBIDDEN_NODE_IDS if nid in collected_norm)
    assert not violations, (
        "pytest --collect-only still emits forbidden node ids that should have "
        "been deleted as part of the Sprint 3 zero-skip/zero-xfail close:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_full_suite_reports_zero_skips_and_zero_xfails() -> None:
    """
    Run the default pytest invocation (no ``--include-live``, no other custom
    args) and assert the summary line reports zero ``skipped``, zero
    ``xfailed``, and zero ``xpassed`` outcomes.

    The acceptance criterion verbatim from the user mandate (2026-05-27).

    Subprocess timeout note
    -----------------------
    The full default suite at the time of writing runs ~1462s on the
    Sprint-3 worktree (impl-final measurement, 2026-05-27). We budget
    3000s (50 min) to leave headroom for suite growth and slower CI
    hosts. The timeout is intentionally generous because the acceptance
    gate is the single most important test in this file — a false
    timeout here would silently mask a real skip/xfail regression.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=no",
            "-p",
            "no:cacheprovider",
            # Exclude this meta-file from recursive invocation to keep the run
            # tractable and avoid recursion (subprocess shells the runner).
            "--deselect",
            "tests/meta/test_zero_skip_xfail_close.py::test_full_suite_reports_zero_skips_and_zero_xfails",
            "--deselect",
            "tests/meta/test_zero_skip_xfail_close.py::test_pytest_collect_only_does_not_emit_any_forbidden_node_id",
        ],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=3000,
    )
    stdout_tail = "\n".join(proc.stdout.splitlines()[-15:])
    # The summary line contains keywords like 'skipped', 'xfailed', 'xpassed'.
    # We look for them anywhere in the tail so we catch the short summary
    # ("=== N passed, M skipped, ... ===") regardless of the exact format.
    forbidden_keywords = ("skipped", "xfailed", "xpassed")
    found = [kw for kw in forbidden_keywords if kw in stdout_tail]
    assert not found, (
        f"Default pytest run reports outcomes that violate the zero-skip/zero-"
        f"xfail mandate: {found}. Last lines of pytest stdout:\n{stdout_tail}"
    )
