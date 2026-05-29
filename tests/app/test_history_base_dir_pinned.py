"""
Regression guard for the 2026-05-29 advisor-dropdown bug.

Bug: three call sites in app.py called
    analytics.get_history_with_cache_invalidation()
without the `base_dir` keyword argument, so the function defaulted to the
process working directory ('.') and found no post-mortem files.  All three
were fixed at app.py:760, 1754, 1796 on commit c19a541.

These tests use AST analysis so they fail at source-review time, not at
runtime, making it impossible to silently regress by omitting the kwarg.
"""

import ast
import pathlib

import pytest

# Resolve app.py relative to this test file — absolute path discipline so
# the test works regardless of the working directory pytest is invoked from.
_APP_PY = pathlib.Path(__file__).parent.parent.parent / "app.py"

_TARGET_FUNC = "get_history_with_cache_invalidation"
_EXPECTED_KWARG = "base_dir"
# The kwarg value must be the attribute reference analytics._POST_MORTEMS_DIR.
# We assert on the symbolic reference, not the string it resolves to, so the
# test survives a future rename of the post_mortems directory.
_EXPECTED_KWARG_OBJECT = "analytics"
_EXPECTED_KWARG_ATTR = "_POST_MORTEMS_DIR"


def _parse_app() -> ast.Module:
    """Return the parsed AST of app.py."""
    assert _APP_PY.exists(), f"app.py not found at {_APP_PY}"
    return ast.parse(_APP_PY.read_text(encoding="utf-8"))


def _collect_target_calls(tree: ast.Module) -> list[tuple[ast.Call, int]]:
    """
    Walk the AST and return every Call node whose function is one of:
      - analytics.get_history_with_cache_invalidation  (Attribute form)
      - get_history_with_cache_invalidation             (direct Name form, if ever imported by name)

    Returns a list of (call_node, line_number) pairs.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            # analytics.get_history_with_cache_invalidation
            if (
                func.attr == _TARGET_FUNC
                and isinstance(func.value, ast.Name)
                and func.value.id == "analytics"
            ):
                found.append((node, node.lineno))
        elif isinstance(func, ast.Name):
            # bare get_history_with_cache_invalidation (imported directly)
            if func.id == _TARGET_FUNC:
                found.append((node, node.lineno))
    return found


def _kwarg_value_is_correct(keyword: ast.keyword) -> bool:
    """
    Return True iff the keyword's value is the AST node for
    analytics._POST_MORTEMS_DIR — i.e., an Attribute node with
    value=Name('analytics') and attr='_POST_MORTEMS_DIR'.
    """
    v = keyword.value
    return (
        isinstance(v, ast.Attribute)
        and isinstance(v.value, ast.Name)
        and v.value.id == _EXPECTED_KWARG_OBJECT
        and v.attr == _EXPECTED_KWARG_ATTR
    )


@pytest.fixture(scope="module")
def app_ast():
    return _parse_app()


@pytest.fixture(scope="module")
def target_calls(app_ast):
    return _collect_target_calls(app_ast)


def test_all_calls_have_base_dir_kwarg(target_calls):
    """
    Every call to analytics.get_history_with_cache_invalidation must supply
    base_dir=analytics._POST_MORTEMS_DIR.

    Missing or wrong kwarg means the function silently falls back to '.', finds
    no post-mortem files, and returns empty history — the 2026-05-29 bug.
    """
    violations = []
    for call_node, lineno in target_calls:
        # Find a keyword named base_dir in this call.
        base_dir_kwargs = [kw for kw in call_node.keywords if kw.arg == _EXPECTED_KWARG]

        if not base_dir_kwargs:
            violations.append(
                f"app.py:{lineno} — call is missing `base_dir` keyword argument"
            )
            continue

        kw = base_dir_kwargs[0]
        if not _kwarg_value_is_correct(kw):
            # Show what value was actually passed for diagnosis.
            actual = ast.unparse(kw.value)
            violations.append(
                f"app.py:{lineno} — `base_dir` is present but value is `{actual}`; "
                f"expected `analytics._POST_MORTEMS_DIR`"
            )

    assert not violations, (
        "The following call sites pass a wrong or missing `base_dir` kwarg "
        "(regression of 2026-05-29 advisor-dropdown bug):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_at_least_three_call_sites_exist(target_calls):
    """
    Guard against a future refactor that silently eliminates all calls to
    analytics.get_history_with_cache_invalidation without a replacement.

    The fix commit (c19a541) landed 3 call sites; asserting >= 3 allows new
    call sites to be added while ensuring none are quietly removed.
    """
    count = len(target_calls)
    line_numbers = [lineno for _, lineno in target_calls]
    assert count >= 3, (
        f"Expected >= 3 call sites for `{_TARGET_FUNC}` in app.py, "
        f"found {count} (at lines {line_numbers}).  "
        "If the function was intentionally removed or renamed, update this test."
    )
