"""
SITE-B1 — port-mode autotuner removal RED tests.

Sprint 3 / port-autotuner-removal cycle.

Target: autotuner.py:217-284 — PORT_TRAIN_RATIO, PORT_VALIDATION_RATIO,
PORT_FROZEN_EVAL_RATIO, MODE_SPECIFIC_PARAMS, MODE_INVARIANT_PARAMS (port-mode
copies), build_port_study_name, get_port_mode_search_space,
validate_port_mode_params_available, warn_port_mode_replay_blind_spot.

Every test in this file MUST FAIL against the pre-removal autotuner.py
(the target names still exist).  Each test PASSES once the implementer
removes the identified dead code.

Keep rules:
  * No assertions against producer-computed values — tests assert structural
    shape (present / absent) and static AST facts.
  * Symphony-level functions are PRESENT; port-mode functions are ABSENT.
  * Migration 012 columns persist in the schema (additive-first mandate).
  * Tests targeting only the removed code are deleted; the test file that
    pins the blind-spot guard (test_c3_port_level_replay_guard.py) is gone
    once warn_port_mode_replay_blind_spot is removed.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pathlib

import pytest

# ---------------------------------------------------------------------------
# Module-level helpers — parse autotuner.py once per collection run.
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = pathlib.Path(__file__).resolve().parents[2]
_AUTOTUNER_PATH = _WORKTREE_ROOT / "autotuner.py"
_AUTOTUNER_SRC = _AUTOTUNER_PATH.read_text(encoding="utf-8")
_AUTOTUNER_TREE = ast.parse(_AUTOTUNER_SRC, filename=str(_AUTOTUNER_PATH))


def _autotuner_top_level_names() -> set[str]:
    """Return the set of names defined at module scope in autotuner.py.

    Captures: function defs, async function defs, class defs, and all
    assignment targets (simple Name nodes only — the port-mode constants
    are simple assignments like PORT_TRAIN_RATIO = 0.50).
    """
    names: set[str] = set()
    for node in ast.walk(_AUTOTUNER_TREE):
        if not isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign),
        ):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


# ===========================================================================
# 1 — Static absence: port-mode constants must not exist in autotuner.py
# ===========================================================================

_PORT_MODE_CONSTANTS = (
    "PORT_TRAIN_RATIO",
    "PORT_VALIDATION_RATIO",
    "PORT_FROZEN_EVAL_RATIO",
)


@pytest.mark.parametrize("constant_name", _PORT_MODE_CONSTANTS)
def test_port_mode_ratio_constant_absent_from_autotuner(constant_name: str) -> None:
    """SITE-B1: port-mode split constants are dead code once the port-level
    dispatch (SITE-A2) is removed.  They must not appear as top-level names
    in autotuner.py.

    RED: all three constants exist in the pre-removal file.
    """
    defined = _autotuner_top_level_names()
    assert constant_name not in defined, (
        f"autotuner.py still defines `{constant_name}`. "
        f"PORT_TRAIN_RATIO / PORT_VALIDATION_RATIO / PORT_FROZEN_EVAL_RATIO exist "
        f"solely to parameterise port-mode Optuna splits (Amendment F2). With the "
        f"port-level dispatch path removed (SITE-A2), these constants are dead code "
        f"and must be deleted."
    )


def test_mode_specific_params_absent_from_autotuner() -> None:
    """SITE-B1: MODE_SPECIFIC_PARAMS in autotuner.py is a port-mode-only
    parameter classification (Amendment F4).  The authoritative copy used by
    live production code lived in engine/params.py (also orphaned, pending
    SITE-C1); the autotuner copy is dead once port-mode tuning is removed.

    RED: MODE_SPECIFIC_PARAMS is defined in the pre-removal file.
    """
    defined = _autotuner_top_level_names()
    assert "MODE_SPECIFIC_PARAMS" not in defined, (
        "autotuner.py still defines `MODE_SPECIFIC_PARAMS`. "
        "This is a port-mode parameter classification (Amendment F4) whose only "
        "use is get_port_mode_search_space — itself being removed.  Delete it."
    )


def test_mode_invariant_params_absent_from_autotuner() -> None:
    """SITE-B1: MODE_INVARIANT_PARAMS in autotuner.py is the symmetric
    port-mode classification constant.  Same rationale as MODE_SPECIFIC_PARAMS.

    RED: MODE_INVARIANT_PARAMS is defined in the pre-removal file.
    """
    defined = _autotuner_top_level_names()
    assert "MODE_INVARIANT_PARAMS" not in defined, (
        "autotuner.py still defines `MODE_INVARIANT_PARAMS`. "
        "Remove alongside MODE_SPECIFIC_PARAMS — both exist only for port-mode "
        "parameter routing (Amendment F4) which is removed."
    )


# ===========================================================================
# 2 — Static absence: port-mode functions must not exist in autotuner.py
# ===========================================================================

_PORT_MODE_FUNCTIONS = (
    "build_port_study_name",
    "get_port_mode_search_space",
    "validate_port_mode_params_available",
    "warn_port_mode_replay_blind_spot",
)


@pytest.mark.parametrize("fn_name", _PORT_MODE_FUNCTIONS)
def test_port_mode_function_absent_from_autotuner(fn_name: str) -> None:
    """SITE-B1: port-mode autotuner functions are dead code once the port-level
    execution path (SITE-A2) is removed.  None of them are called from the
    per-symphony trial loop or any other live path.

    build_port_study_name    — names port-mode Optuna studies (no callers)
    get_port_mode_search_space — returns port-mode bounds (no callers)
    validate_port_mode_params_available — checks port-level DB rows (no callers)
    warn_port_mode_replay_blind_spot — called only by get_port_mode_search_space

    RED: all four functions exist in the pre-removal file.
    """
    defined = _autotuner_top_level_names()
    assert fn_name not in defined, (
        f"autotuner.py still defines `{fn_name}`. "
        f"SITE-B1 removal requires deleting all port-mode autotuner functions: "
        f"build_port_study_name, get_port_mode_search_space, "
        f"validate_port_mode_params_available, warn_port_mode_replay_blind_spot."
    )


# ===========================================================================
# 3 — Static presence: symphony-level functions must survive removal intact
# ===========================================================================

_SYMPHONY_LEVEL_FUNCTIONS = (
    "validate_search_space_nn1",
    "validate_nn1_compliance",
    "_haircut_select",
    "compute_crra_eu_tstat",
    "compute_n_effective",
    "run_autotuner",
)


@pytest.mark.parametrize("fn_name", _SYMPHONY_LEVEL_FUNCTIONS)
def test_symphony_level_function_still_present_in_autotuner(fn_name: str) -> None:
    """SITE-B1 keep-list: symphony-level autotuner functions are untouched by
    the port-mode removal.  An overly-broad deletion that removes these would
    break the live production path.

    GREEN condition: these must all remain present after removal.
    RED condition: they ARE present now (test trivially passes against
    pre-removal source).  This test catches accidental over-deletion by
    the implementer; it is here to enforce the keep-list, not to signal RED.

    Note: test_symphony_level_function_still_present_in_autotuner is
    intentionally written to PASS on pre-removal code (the functions exist)
    and continue to pass post-removal.  It is a regression guard — if the
    implementer accidentally deletes one it will immediately fail.
    """
    defined = _autotuner_top_level_names()
    assert fn_name in defined, (
        f"autotuner.py is missing `{fn_name}`. "
        f"SITE-B1 removal must NOT touch symphony-level functions. "
        f"Review the deletion range — it expanded beyond the port-mode block."
    )


# ===========================================================================
# 4 — Symphony-level function signatures are unchanged
#
# Signatures are verified via inspect.signature on the live module import,
# which is more reliable than AST parameter counting for default-heavy sigs.
# We assert shape (parameter names present) rather than exact defaults so the
# test is robust to innocuous future parameter additions.
# ===========================================================================

_EXPECTED_PARAMS: dict[str, tuple[str, ...]] = {
    # run_autotuner positional + keyword params that must survive.
    "run_autotuner": ("bot_state", "current_date_str", "account_uuids"),
    # validate_search_space_nn1 takes no positional args.
    "validate_search_space_nn1": (),
    # validate_nn1_compliance(spec_bundle_id).
    "validate_nn1_compliance": ("spec_bundle_id",),
}


@pytest.mark.parametrize("fn_name,expected_params", _EXPECTED_PARAMS.items())
def test_symphony_level_function_signature_shape_preserved(
    fn_name: str, expected_params: tuple[str, ...]
) -> None:
    """SITE-B1 keep-list: critical function signatures must retain their
    expected parameters after the port-mode removal.

    Asserts that each expected param name is present in the live function's
    signature — NOT that the signature is identical, so harmless additions
    are tolerated.  Fails if the removal corrupted or collapsed the function.

    RED: trivially passes pre-removal (functions exist with these params).
    Failure post-removal would indicate accidental scope damage.
    """
    import autotuner as _at  # noqa: PLC0415 — intentional deferred import

    fn = getattr(_at, fn_name, None)
    assert fn is not None, (
        f"autotuner.{fn_name} is not accessible — see "
        f"test_symphony_level_function_still_present_in_autotuner."
    )
    sig = inspect.signature(fn)
    actual_params = set(sig.parameters)
    for param in expected_params:
        assert param in actual_params, (
            f"autotuner.{fn_name} is missing parameter `{param}`. "
            f"The port-mode removal must not alter symphony-level function "
            f"signatures.  Expected params {expected_params!r}, "
            f"got {sorted(actual_params)!r}."
        )


# ===========================================================================
# 5 — Migration 012 schema columns persist (additive-first mandate)
# ===========================================================================

_MIGRATION_012_COLUMNS = ("math_mode", "account_id", "sortino_sentinel_pct")


@pytest.mark.parametrize("column_name", _MIGRATION_012_COLUMNS)
def test_migration_012_column_present_in_autotune_runs(column_name: str) -> None:
    """SITE-B1 additive-first mandate: migration 012 added three NULLable
    columns to autotune_runs.  Removing the port-mode code that WRITES them
    is correct; removing the columns themselves is NOT — tables are never
    destructively altered in a single step.

    Asserts the column still exists in the schema that database.py applies
    to a fresh in-memory DB.  Uses the same migration path the daemon uses.

    RED: columns exist pre-removal (test passes now).  Failure post-removal
    would mean the implementer incorrectly deleted migration 012 or issued a
    DROP COLUMN — a schema regression.
    """
    import database as _db  # noqa: PLC0415

    conn = _db.get_connection()
    try:
        cursor = conn.execute("PRAGMA table_info(autotune_runs)")
        column_names = {row[1] for row in cursor.fetchall()}
    finally:
        conn.close()

    assert column_name in column_names, (
        f"autotune_runs.{column_name} is missing from the schema. "
        f"Migration 012 added math_mode / account_id / sortino_sentinel_pct "
        f"as NULLable columns (additive-first, AC-P2.11.1).  Removing the "
        f"port-mode writer does NOT authorise dropping these columns."
    )


# ===========================================================================
# 6 — Dead test file for removed blind-spot guard is deleted
#
# test_c3_port_level_replay_guard.py tests warn_port_mode_replay_blind_spot
# and get_port_mode_search_space exclusively.  Once those functions are removed
# the test file has no valid targets and must be deleted; leaving it would
# cause ImportError at collection time when it tries to access the deleted
# attributes.
# ===========================================================================


def test_port_level_replay_guard_test_file_is_deleted() -> None:
    """SITE-B1: tests/autotuner/test_c3_port_level_replay_guard.py tests only
    warn_port_mode_replay_blind_spot (and transitively get_port_mode_search_space).
    Once those functions are removed the file will crash pytest collection with
    AttributeError.  It must be deleted in the same commit as the source removal.

    RED: the file still exists in the pre-removal worktree.
    """
    guard_test = _WORKTREE_ROOT / "tests" / "autotuner" / "test_c3_port_level_replay_guard.py"
    assert not guard_test.exists(), (
        "tests/autotuner/test_c3_port_level_replay_guard.py still exists. "
        "This file tests warn_port_mode_replay_blind_spot and "
        "get_port_mode_search_space exclusively.  Delete it together with the "
        "source functions — a live import of deleted attributes crashes "
        "pytest collection for the entire autotuner suite."
    )


# ===========================================================================
# 7 — Hostile import: autotuner module loads without errors post-removal
#
# Any module that imports the removed names at module level will raise
# ImportError / AttributeError on collection.  Catching this at test time
# makes the breakage explicit rather than silent.
# ===========================================================================


def test_autotuner_module_importable_without_port_mode_names() -> None:
    """SITE-B1 import-chain guard: after removal, `import autotuner` must
    succeed and must NOT expose the port-mode names as module attributes.

    A fresh importlib.reload is used so the test is not satisfied by a
    cached pre-removal import (important when tests run in the same process
    as the module was already imported by a keep-list test above).

    RED: the module currently exposes the port-mode names; the attribute
    assertions below will fail once the module is reloaded post-removal
    because the names will be present.  Wait — actually RED means the
    *import* itself must succeed but the *attribute checks* must FAIL
    (names exist).  Written correctly: we import, then assert each name
    ABSENT; pre-removal those assertions fail (names ARE present) = RED.
    """
    import autotuner as _at

    _at = importlib.reload(_at)

    for name in (
        "PORT_TRAIN_RATIO",
        "PORT_VALIDATION_RATIO",
        "PORT_FROZEN_EVAL_RATIO",
        "MODE_SPECIFIC_PARAMS",
        "MODE_INVARIANT_PARAMS",
        "build_port_study_name",
        "get_port_mode_search_space",
        "validate_port_mode_params_available",
        "warn_port_mode_replay_blind_spot",
    ):
        assert not hasattr(_at, name), (
            f"autotuner.{name} still accessible after module reload. "
            f"The port-mode removal must delete this name from autotuner.py "
            f"so it is not importable by any caller."
        )


# ===========================================================================
# 8 — No other module imports the removed port-mode names
#
# If any .py file outside autotuner.py imports a removed name, it will crash
# at module load time.  Scan the worktree for such imports.
# ===========================================================================

_REMOVED_SYMBOLS = frozenset(
    {
        "PORT_TRAIN_RATIO",
        "PORT_VALIDATION_RATIO",
        "PORT_FROZEN_EVAL_RATIO",
        "MODE_SPECIFIC_PARAMS",
        "MODE_INVARIANT_PARAMS",
        "build_port_study_name",
        "get_port_mode_search_space",
        "validate_port_mode_params_available",
        "warn_port_mode_replay_blind_spot",
    }
)


def _file_imports_autotuner_symbol(src: str, symbol: str) -> bool:
    """Return True when `src` imports `symbol` from autotuner or accesses it
    as `autotuner.<symbol>`.

    Covers:
      - `from autotuner import symbol`
      - `from autotuner import symbol as ...`
      - `autotuner.symbol`

    Explicitly excludes files that merely define a same-named local binding
    (e.g. `_MODE_SPECIFIC_PARAMS = frozenset(...)`) — those are not consumers
    of the removed autotuner names.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        # Unparseable file — skip; a separate lint pass will catch it.
        return False

    for node in ast.walk(tree):
        # `from autotuner import X` or `from autotuner import X as Y`
        if isinstance(node, ast.ImportFrom):
            if node.module == "autotuner" and node.names:
                for alias in node.names:
                    if alias.name == symbol:
                        return True
        # `autotuner.X` attribute access
        if isinstance(node, ast.Attribute):
            if (
                node.attr == symbol
                and isinstance(node.value, ast.Name)
                and node.value.id == "autotuner"
            ):
                return True
        # `getattr(autotuner, "X", ...)` — e.g. in test_c3_port_level_replay_guard.py
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "getattr" and len(node.args) >= 2:
                first = node.args[0]
                second = node.args[1]
                if (
                    isinstance(first, ast.Name)
                    and first.id == "autotuner"
                    and isinstance(second, ast.Constant)
                    and second.value == symbol
                ):
                    return True

    return False


def test_no_other_module_imports_removed_port_mode_symbols() -> None:
    """SITE-B1 blast-radius: scan all .py files in the worktree (excluding
    autotuner.py itself and this test file) for import statements or attribute
    accesses that consume the removed autotuner port-mode symbols from autotuner.

    A surviving import/access would cause ImportError or AttributeError at
    module load time after the names are deleted from autotuner.py.

    Detection uses AST — files that define their own same-named local constants
    (e.g. engine/params.py defines _MODE_SPECIFIC_PARAMS) are NOT flagged;
    only callers that import from or getattr on the `autotuner` module are caught.

    RED: test_c3_port_level_replay_guard.py calls
    `getattr(autotuner, "warn_port_mode_replay_blind_spot", None)`.  Pre-removal
    this test fails because the guard-test file still exists.  Post-removal, once
    both the source file and guard-test are deleted, the test passes.
    """
    violations: list[str] = []

    _this_file = pathlib.Path(__file__).resolve()
    # Exclude `.claude/` to skip git worktree + audit-worktree stale snapshots from
    # prior sessions — see project memory project_blast_radius_scanner_worktree_interaction.
    py_files = [
        p
        for p in _WORKTREE_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
        and ".claude" not in p.parts
        and p.resolve() != _AUTOTUNER_PATH.resolve()
        and p.resolve() != _this_file
    ]

    for py_file in py_files:
        try:
            src = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for symbol in _REMOVED_SYMBOLS:
            if _file_imports_autotuner_symbol(src, symbol):
                rel = py_file.relative_to(_WORKTREE_ROOT)
                violations.append(f"  {rel}: imports/accesses autotuner.{symbol}")
                break  # one violation per file is enough for the report

    assert not violations, (
        "The following files import or access port-mode symbols that will be "
        "deleted from autotuner.py.  Update or delete them before the SITE-B1 "
        "removal to avoid ImportError / AttributeError at collection time:\n"
        + "\n".join(violations)
    )
