"""The dead functions are gone from every tracked file, and the live names beside them stay.

Names with no production caller were deleted: a deleted name must not be defined, called,
patched, imported or named in a string anywhere in the repository, because a test that patches
a name the module no longer has fails at collection. The scan is an AST walk of every tracked
Python file (the pattern of tests/dashboard/test_table_partial_retired.py), so a comment or a
docstring that explains the removal is never a hit; only a Name, an Attribute, a definition, an
import alias, or a one-token string constant (the shape of a patch target or a ``getattr`` name)
is.

Three kinds of check:
  * structural (fail until the deletions land): no deleted name is referenced, the orphan
    fixtures are gone, and the two fixture keys only the deleted functions' tests read are gone;
  * call-path (characterization of the production tree): no non-Python tracked file (script,
    template, unit, config) names a deleted function, and no ``getattr``/``importlib`` string
    dispatch reaches one;
  * live siblings (characterization): each name a production path reaches still exists with
    the value or output it had.

Not scanned: this file, which has to name them, and the three files whose absence assertions name
them on purpose (tests/ai_advisor/test_logic_l_2_api_docstring.py,
tests/app/test_retirement_recommendations_route.py and
tests/database/test_bl10_dead_autotune_columns_documented.py). The files that pin which source nodes may
change in the same slice name them to cut them out, so they are listed too.
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

# Names no live module defines or uses. Each is defined in exactly one module at the base.
GLOBAL_DEAD_NAMES = (
    "build_symphony_study_name",
    "_build_optuna_sampler_from_env",
    "_n_jobs_raw",
    "record_autotune_run",
    "compute_v1_bootstrap_state",
    "get_eod_shadow_row",
    "get_advisor_observations_for_subject",
    "get_suggestions_for_symphony",
    "get_suggestions_for_session",
    "compute_shadow_hwm",
    # Names only the deleted readers used.
    "_parse_llm_suggestion_row",
    "_LLM_SUGGESTION_COLUMNS",
)
# autotuner.filter_sortino_sentinels is live and keeps the name; only the math_engine copy goes.
MATH_ENGINE_DEAD_NAME = "filter_sortino_sentinels"
MATH_ENGINE_MODULE = "math_engine"

ALLOWED_FILES = frozenset(
    {
        "tests/test_hygiene_dead_names_absent.py",
        "tests/ai_advisor/test_logic_l_2_api_docstring.py",
        "tests/app/test_retirement_recommendations_route.py",
        "tests/database/test_bl10_dead_autotune_columns_documented.py",
        "tests/test_hygiene_protected_surfaces.py",
        "tests/math_engine/test_zscore_shared_definition.py",
        # Holds the absence assertion that run_autotuner has no local _n_jobs_raw read.
        "tests/autotuner/test_optuna_sampler_pin_and_n_jobs.py",
    }
)
ALLOWED_PREFIXES = ("tests/fixtures/hygiene_waves_3_4/",)
EXCLUDED_PREFIXES = (".claude/",)

ORPHAN_FIXTURE_PATHS = (
    "tests/fixtures/autotuner/sortino_sentinel_trim",
    "tests/fixtures/shadow/v1_bootstrap_gate.json",
)
NON_PYTHON_SUFFIXES = (".js", ".html", ".json", ".toml", ".ps1", ".sh", ".service", ".timer")

_ONE_TOKEN = re.compile(r"[\w.]+")


def _git_ls_files(*patterns: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "--", *patterns],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _scanned(paths: list[str]) -> list[str]:
    return [
        p
        for p in paths
        if p not in ALLOWED_FILES
        and not p.startswith(ALLOWED_PREFIXES)
        and not p.startswith(EXCLUDED_PREFIXES)
    ]


def _dotted_tail(node: ast.AST) -> str:
    """'math_engine' for Name math_engine and for an Attribute chain ending in .math_engine."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _hits_in_tree(tree: ast.AST, rel_path: str) -> list[str]:
    """Every node that defines, reaches or names a dead name, as 'line: what'."""
    hits: list[str] = []
    is_math_engine_file = rel_path == f"{MATH_ENGINE_MODULE}.py"
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Name) and node.id in GLOBAL_DEAD_NAMES:
            hits.append(f"{line}: name {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in GLOBAL_DEAD_NAMES:
                hits.append(f"{line}: attribute .{node.attr}")
            elif (
                node.attr == MATH_ENGINE_DEAD_NAME
                and _dotted_tail(node.value) == MATH_ENGINE_MODULE
            ):
                hits.append(f"{line}: attribute {MATH_ENGINE_MODULE}.{node.attr}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in GLOBAL_DEAD_NAMES:
                hits.append(f"{line}: definition {node.name}")
            elif is_math_engine_file and node.name == MATH_ENGINE_DEAD_NAME:
                hits.append(f"{line}: definition {MATH_ENGINE_MODULE}.{node.name}")
        elif isinstance(node, ast.alias):
            if node.name in GLOBAL_DEAD_NAMES or node.asname in GLOBAL_DEAD_NAMES:
                hits.append(f"{line}: import alias {node.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == MATH_ENGINE_MODULE and any(
                a.name == MATH_ENGINE_DEAD_NAME for a in node.names
            ):
                hits.append(f"{line}: import {MATH_ENGINE_MODULE}.{MATH_ENGINE_DEAD_NAME}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if not _ONE_TOKEN.fullmatch(text):
                continue
            if (
                any(text == n or text.endswith("." + n) for n in GLOBAL_DEAD_NAMES)
                or text == f"{MATH_ENGINE_MODULE}.{MATH_ENGINE_DEAD_NAME}"
                or text.endswith(f".{MATH_ENGINE_MODULE}.{MATH_ENGINE_DEAD_NAME}")
            ):
                hits.append(f"{line}: string {text!r}")
    return hits


def _scan_python_files() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for rel in _scanned(_git_ls_files("*.py")):
        path = REPO / rel
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError):
            continue
        hits = _hits_in_tree(tree, rel)
        if hits:
            found[rel] = hits
    return found


class TestScanSeesWhatItClaimsToSee:
    """A scan that cannot hit proves nothing: each node kind it reports is planted and found."""

    @pytest.mark.parametrize(
        ("source", "needle"),
        [
            ("record_autotune_run()", "name record_autotune_run"),
            ("db.get_suggestions_for_session('x')", "attribute .get_suggestions_for_session"),
            ("def compute_shadow_hwm(): pass", "definition compute_shadow_hwm"),
            ("from database import get_eod_shadow_row", "import alias get_eod_shadow_row"),
            (
                "patch('database.compute_v1_bootstrap_state')",
                "string 'database.compute_v1_bootstrap_state'",
            ),
            ("getattr(m, '_n_jobs_raw')", "string '_n_jobs_raw'"),
            (
                "math_engine.filter_sortino_sentinels([])",
                "attribute math_engine.filter_sortino_sentinels",
            ),
            (
                "from math_engine import filter_sortino_sentinels",
                "import math_engine.filter_sortino_sentinels",
            ),
        ],
    )
    def test_each_node_kind_is_reported(self, source, needle):
        hits = _hits_in_tree(ast.parse(source), "planted.py")
        assert any(needle in h for h in hits), f"planted {source!r} not reported; hits: {hits}"

    @pytest.mark.parametrize(
        "source",
        [
            "autotuner.filter_sortino_sentinels([])",
            "from autotuner import filter_sortino_sentinels",
            '"""record_autotune_run was removed because nothing called it."""',
            "# record_autotune_run\nx = 1",
            "save_autotune_run()",
        ],
    )
    def test_live_names_and_prose_are_not_reported(self, source):
        assert _hits_in_tree(ast.parse(source), "planted.py") == []

    def test_the_math_engine_definition_is_reported_only_in_math_engine(self):
        source = "def filter_sortino_sentinels(values):\n    return values\n"
        assert _hits_in_tree(ast.parse(source), "math_engine.py")
        assert _hits_in_tree(ast.parse(source), "autotuner.py") == []


class TestDeletedNamesAreAbsent:
    def test_no_tracked_python_file_defines_or_references_a_deleted_name(self):
        found = _scan_python_files()
        report = [f"{path}:{hit}" for path, hits in sorted(found.items()) for hit in hits]
        assert not report, (
            "deleted names are still defined or referenced (AST nodes, not comments):\n"
            + "\n".join(report)
        )

    @pytest.mark.parametrize("path", ORPHAN_FIXTURE_PATHS)
    def test_orphan_fixture_is_not_tracked(self, path):
        assert _git_ls_files(path) == [], f"{path} is still tracked; nothing reads it"

    def test_the_fixture_keys_only_the_deleted_test_read_are_gone(self):
        import json

        data = json.loads(
            (REPO / "tests/fixtures/shadow/analytics_shadow_history.json").read_text(
                encoding="utf-8"
            )
        )

        def keys(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield k
                    yield from keys(v)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)

        assert "shadow_hwm_day1" not in set(keys(data)), (
            "analytics_shadow_history.json still carries shadow_hwm_day1, read only by the "
            "deleted high-water-mark function's test"
        )


class TestNoOtherPathReachesADeletedName:
    """Call-path proof, re-run at the head: dispatch by string, scripts, templates, units."""

    def test_no_non_python_tracked_file_names_a_deleted_function(self):
        pattern = re.compile(
            r"\b(?:"
            + "|".join(re.escape(n) for n in GLOBAL_DEAD_NAMES)
            + r")\b"
            + r"|\bmath_engine\.filter_sortino_sentinels\b"
        )
        offenders = []
        for rel in _scanned(_git_ls_files(*(f"*{s}" for s in NON_PYTHON_SUFFIXES))):
            path = REPO / rel
            if not path.is_file():
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                offenders.append(rel)
        assert not offenders, f"deleted names are named in non-Python files: {offenders}"

    def test_no_getattr_or_importlib_dispatch_names_a_deleted_function(self):
        offenders = []
        for rel in _scanned(_git_ls_files("*.py")):
            path = REPO / rel
            if not path.is_file():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                    continue
                if node.func.id not in {"getattr", "hasattr", "import_module", "__import__"}:
                    continue
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value in GLOBAL_DEAD_NAMES:
                        offenders.append(f"{rel}:{node.lineno}")
        assert not offenders, f"string dispatch reaches a deleted name: {offenders}"


class TestLiveSiblingsKeepTheirBehaviour:
    """Names a production path reaches stay, with the values and outputs they had."""

    def test_the_sortino_sentinel_constant_keeps_its_value(self):
        import math_engine

        assert math_engine._SORTINO_SENTINEL == 1e6

    def test_the_autotuner_sentinel_filter_still_drops_a_sentinel_trial(self):
        import autotuner
        import math_engine

        def trial(value):
            return type(
                "FakeTrial",
                (),
                {"value": value, "user_attrs": {"daily_returns": [0.5, 0.3, 0.4]}, "number": 0},
            )()

        kept = autotuner.filter_sortino_sentinels(
            [trial(math_engine._SORTINO_SENTINEL), trial(0.5), trial(None)]
        )

        assert [t.value for t in kept] == [0.5], "the live filter must keep dropping a sentinel"

    @pytest.mark.parametrize(
        ("module", "name"),
        [
            ("database", "save_autotune_run"),
            ("database", "load_latest_shadow_row"),
            ("database", "record_llm_suggestion"),
            ("database", "insert_advisor_observation"),
            ("database", "get_advisor_observations_for_role"),
            ("database", "get_advisor_observations_for_symphony"),
            ("analytics", "_load_latest_shadow_row_for_analytics"),
            ("analytics", "_V1_BOOTSTRAP_MIN_DAYS"),
            ("analytics", "_WINDOWED_VOL_MIN_DAYS"),
            ("autotuner", "filter_sortino_sentinels"),
            ("autotuner", "_resolve_optuna_n_jobs_from_env"),
            ("autotuner", "_OPTUNA_SAMPLER_SEED_ENV"),
            ("autotuner", "_OPTUNA_N_JOBS_ENV"),
        ],
    )
    def test_live_name_exists(self, module, name):
        import importlib

        assert hasattr(importlib.import_module(module), name), f"{module}.{name} must stay"

    def test_the_bootstrap_floor_and_the_windowed_floor_keep_their_value(self):
        import analytics

        assert analytics._V1_BOOTSTRAP_MIN_DAYS == 30
        assert analytics._WINDOWED_VOL_MIN_DAYS == analytics._V1_BOOTSTRAP_MIN_DAYS
