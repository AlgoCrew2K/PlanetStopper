"""Scope pins for the hygiene-waves-3-4 engine, math and regime edits (AC-10, S-10.1 to S-10.10).

The slice edits four protected files in listed, behavior-identical ways:

* ``alpha_bot_execution.py`` -- the ``env_flags`` import, the ``LIVE_EXECUTION`` assignment, and the
  dip-line ``print`` (AC-3, AC-5).
* ``math_engine.py`` -- ``filter_sortino_sentinels`` deleted, one shared ``_zscore`` added, the three
  nested ``_z`` closures and the six call statements replaced (AC-1, AC-3).
* ``autotuner.py`` -- ``_build_optuna_sampler_from_env`` and ``build_symphony_study_name`` deleted,
  the seed read through its named constant, ``_n_jobs_raw`` and its comment deleted (AC-1).
* ``regime_classifier.py`` -- the module docstring only (AC-9).

A full-file pin cannot tell "only the listed edits" from "something else too", so each file is pinned
here with its listed edit nodes CUT. The cut is by AST (the pattern of
``tests/engine/test_close_snapshot_builder_scope.py``): every node a selector names is removed by its
line span (a selector may also take the comment block directly above it), blank-line runs collapse
to one blank line (deleting a top-level function would otherwise change the runs around it), and the
text is hashed LF-normalized. The digests are the reference commit's (AlphaBotPM@7adc2c56): they were
computed from ``git show 7adc2c56:<path>`` and equal the digest of the worktree file at RED, before
any production edit. A GREEN diff that stays inside the listed edits leaves every digest unchanged; a
byte changed anywhere else moves one.

STOP and ask the PM before re-taking any digest here: a change means a file moved outside what the
slice may touch. The three FULL-file pins (``tests/test_mdd_window_truth_zero_diff_guard.py``,
``tests/engine/test_close_snapshot_builder_scope.py``, ``tests/test_regime_label_protected_files.py``)
are re-taken by the closer after GREEN only once these scope pins pass.

Also pinned: the replay (S-10.7), ``regime_classifier.py`` minus its docstring (S-10.8), the phase-1.5
M3 bundle surfaces of ``database.py`` (S-10.10), and the two files the regime pin keeps byte-identical
(S-10.3).
"""

from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------------------
# The cut
# --------------------------------------------------------------------------------------

Selector = Callable[[ast.Module], Iterable[tuple[ast.AST, bool]]]  # (node, take leading comments)


def _nodes(tree: ast.Module, kind, pred) -> list[ast.AST]:
    return [n for n in ast.walk(tree) if isinstance(n, kind) and pred(n)]


def _module_level_functions(*names: str) -> Selector:
    return lambda tree: [
        (n, False) for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names
    ]


def _nested_or_any_functions(*names: str) -> Selector:
    return lambda tree: [
        (n, False) for n in _nodes(tree, ast.FunctionDef, lambda f: f.name in names)
    ]


def _assigns_to(names: set[str], *, comments: bool = False, module_level: bool = False) -> Selector:
    def select(tree: ast.Module):
        pool = tree.body if module_level else ast.walk(tree)
        return [
            (n, comments)
            for n in pool
            if isinstance(n, ast.Assign)
            and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in names
        ]

    return select


def _module_import_of(module: str) -> Selector:
    return lambda tree: [
        (n, False)
        for n in tree.body
        if isinstance(n, ast.Import) and any(a.name == module for a in n.names)
    ]


def _print_statements_containing(fragment: str) -> Selector:
    def select(tree: ast.Module):
        found = []
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Name)
                and n.value.func.id == "print"
                and any(
                    isinstance(c, ast.Constant) and isinstance(c.value, str) and fragment in c.value
                    for c in ast.walk(n.value)
                )
            ):
                found.append((n, False))
        return found

    return select


def _module_docstring(tree: ast.Module):
    first = tree.body[0] if tree.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return [(first, False)]
    return []


def _union(*selectors: Selector) -> Selector:
    return lambda tree: [pair for s in selectors for pair in s(tree)]


def _ranges(text: str, selector: Selector) -> list[tuple[int, int]]:
    """0-based inclusive line ranges to cut, merged."""
    lines = text.split("\n")
    out = []
    for node, comments in selector(ast.parse(text)):
        start, end = node.lineno - 1, node.end_lineno - 1
        if comments:
            while start > 0 and lines[start - 1].strip().startswith("#"):
                start -= 1
        out.append((start, end))
    out.sort()
    merged: list[tuple[int, int]] = []
    for s, e in out:
        if merged and s <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def cut_text(text: str, selector: Selector) -> str:
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    drop = set()
    for s, e in _ranges(text, selector):
        drop.update(range(s, e + 1))
    kept = [ln for i, ln in enumerate(lines) if i not in drop]
    collapsed: list[str] = []
    for ln in kept:
        if ln.strip() == "" and collapsed and collapsed[-1].strip() == "":
            continue
        collapsed.append(ln)
    return "\n".join(collapsed).strip("\n") + "\n"


def scope_digest(text: str, selector: Selector) -> str:
    return hashlib.sha256(cut_text(text, selector).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------
# The listed edits per file
# --------------------------------------------------------------------------------------

_ENGINE_EDITS = _union(
    _module_import_of("env_flags"),
    _assigns_to({"LIVE_EXECUTION"}, module_level=True),
    _print_statements_containing("dipped below stop"),
)
_MATH_EDITS = _union(
    _module_level_functions("filter_sortino_sentinels", "_zscore"),
    _nested_or_any_functions("_z"),
    _assigns_to({"cand_returns_z", "cand_vols_z"}),
)
_AUTOTUNER_EDITS = _union(
    _module_level_functions("_build_optuna_sampler_from_env", "build_symphony_study_name"),
    _assigns_to({"_seed_raw", "_n_jobs_raw", "_n_jobs"}, comments=True),
)
_CLASSIFIER_EDITS: Selector = _module_docstring

_SCOPE = {
    "alpha_bot_execution.py": _ENGINE_EDITS,
    "math_engine.py": _MATH_EDITS,
    "autotuner.py": _AUTOTUNER_EDITS,
    "regime_classifier.py": _CLASSIFIER_EDITS,
}

# LF-normalized sha256 of each file with its listed edits cut (blank runs collapsed), computed from
# `git show 7adc2c56:<path>`; equal to the worktree file at RED.
_PINNED_SCOPE_DIGESTS = {
    "alpha_bot_execution.py": "2d555aacd952b4848483db6ba5fdbb44fe898442fa0b30d91009ca7c498e9a7e",
    "math_engine.py": "de998d4bb0f7b57c98c06b15b9f48850b92c5fd58426b0b98ed0141765b6ac3e",
    "autotuner.py": "64bccae77b35e8cfa3275e8258c15067e823659f6800b72a981b2b1466f5dd74",
    "regime_classifier.py": "7a5fc8dae2c3d1c88d4c84513b4f9b8a20b00ab133b29a75d0986340964e7693",
}


def _read(rel: str) -> str:
    return (_REPO / rel).read_bytes().decode("utf-8").replace("\r\n", "\n")


class TestScopePinsHoldOnlyTheListedEdits:
    @pytest.mark.parametrize("rel", sorted(_SCOPE))
    def test_file_outside_its_listed_edits_is_the_reference_text(self, rel):
        actual = scope_digest(_read(rel), _SCOPE[rel])
        assert actual == _PINNED_SCOPE_DIGESTS[rel], (
            f"{rel} changed outside the slice's listed edits (pinned "
            f"{_PINNED_SCOPE_DIGESTS[rel]}, now {actual}). STOP and ask the PM."
        )

    @pytest.mark.parametrize("rel", sorted(_SCOPE))
    def test_the_cut_removes_at_least_one_node_when_the_edit_exists_at_green_or_ref(self, rel):
        # Non-vacuity: at the reference the cut must remove something (the nodes the slice edits).
        assert _ranges(_read(rel), _SCOPE[rel]), f"the selector for {rel} matched nothing"


class TestTheCutIsSensitiveToTheRightThings:
    """S-10.2: a byte changed outside a listed edit moves the digest; one inside does not."""

    @pytest.mark.parametrize("rel", sorted(_SCOPE))
    def test_a_comment_planted_outside_every_cut_node_moves_the_digest(self, rel):
        text = _read(rel)
        base = scope_digest(text, _SCOPE[rel])
        assert scope_digest(text + "\n# planted outside the listed edits\n", _SCOPE[rel]) != base

    @pytest.mark.parametrize("rel", sorted(_SCOPE))
    def test_one_byte_changed_in_the_middle_of_the_file_outside_the_cuts_moves_the_digest(
        self, rel
    ):
        text = _read(rel)
        base = scope_digest(text, _SCOPE[rel])
        cuts = _ranges(text, _SCOPE[rel])
        lines = text.split("\n")
        inside = {i for s, e in cuts for i in range(s, e + 1)}
        target = next(
            i for i in range(len(lines) // 2, len(lines)) if i not in inside and lines[i].strip()
        )
        mutated = list(lines)
        # a comment line parses wherever it lands (inside a call, a string or a block)
        mutated.insert(target, "# one line planted outside every listed edit")
        assert scope_digest("\n".join(mutated), _SCOPE[rel]) != base

    @pytest.mark.parametrize("rel", sorted(_SCOPE))
    def test_a_change_inside_a_cut_node_does_not_move_the_digest(self, rel):
        text = _read(rel)
        base = scope_digest(text, _SCOPE[rel])
        s, e = _ranges(text, _SCOPE[rel])[0]
        lines = text.split("\n")
        mutated = list(lines)
        mutated[e] = mutated[e] + "  # changed inside the cut"
        assert scope_digest("\n".join(mutated), _SCOPE[rel]) == base

    def test_blank_line_runs_around_a_deleted_function_do_not_move_the_digest(self):
        src = "a = 1\n\n\ndef gone():\n    return 1\n\n\ndef kept():\n    return 2\n"
        without = "a = 1\n\n\ndef kept():\n    return 2\n"
        sel = _module_level_functions("gone")
        assert scope_digest(src, sel) == scope_digest(without, sel)

    def test_crlf_and_lf_checkouts_hash_the_same(self):
        src = "x = 1\n\n\ndef gone():\n    return 1\n\n\ny = 2\n"
        sel = _module_level_functions("gone")
        assert scope_digest(src.replace("\n", "\r\n"), sel) == scope_digest(src, sel)

    def test_a_second_statement_planted_beside_a_cut_node_moves_the_digest(self):
        src = "def main():\n    print('dipped below stop. N')\n    b = 2\n"
        planted = src.replace("    b = 2\n", "    b = 2\n    save()\n")
        sel = _print_statements_containing("dipped below stop")
        assert scope_digest(planted, sel) != scope_digest(src, sel)


# --------------------------------------------------------------------------------------
# S-10.7 the replay, S-10.8 the classifier, S-10.10 the M3 bundle, S-10.3 the two pinned files
# --------------------------------------------------------------------------------------


def _function_dump(rel: str, name: str) -> str:
    tree = ast.parse(_read(rel))
    (fn,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    return ast.dump(fn, include_attributes=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_REPLAY_DIGESTS = {
    "_collect_sim_returns": "52b01e29cfef4d169e17ff2bca52c2af365f8ebead1eb60b4082dcace6354db5",
    "_collect_sim_returns_dated": "9658fb2a100e969f152d15f02e90726dc5d55da24ca18df4f69c9468399d5e73",
    "run_simulation": "5597a3034402446ec53ba8fa590d0db4622108e78b778e3b3d4a7fe3ab5ae5d6",
}
_CLASSIFIER_WITHOUT_DOCSTRING = "eb99455e68ce97d2cda3df55159afaf01c6b1bcad988fc8e15d9cad06597a304"
_M3_DIGEST = "f7eb4d4f85f7d6510471cf7dcfe8db3a28b6b139f5f3c6255297d7e7e2179a7f"
_UNTOUCHED_FILE_DIGESTS = {
    "synthetic_history.py": "7a7df0162e0895649e42e8915c98653f6336118ca5a3379ca09d6d4561bb2883",
    "migrations/027_regime_label_cache.sql": (
        "46b63e476670db9eb57afcccb6a15e8e78b8a9ddc08d7ca50680d6c2a26fe8d2"
    ),
}


class TestTheReplayIsIdentical:
    @pytest.mark.parametrize("name", sorted(_REPLAY_DIGESTS))
    def test_replay_function_ast_equals_the_reference(self, name):
        assert _sha(_function_dump("autotuner.py", name)) == _REPLAY_DIGESTS[name]


class TestRegimeClassifierIsIdenticalOutsideItsDocstring:
    def test_module_ast_without_the_docstring_equals_the_reference(self):
        tree = ast.parse(_read("regime_classifier.py"))
        tree.body = tree.body[1:] if _module_docstring(tree) else tree.body
        assert _sha(ast.dump(tree, include_attributes=False)) == _CLASSIFIER_WITHOUT_DOCSTRING


def _m3_surface_dump() -> str:
    tree = ast.parse(_read("database.py"))
    parts = []
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, ast.FunctionDef):
            names = [node.name]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
        if any(
            n == "get_or_create_phase15_m3_bundle_id"
            or n == "_phase15_m3_bundle_id_cache"
            or n.startswith("_M3_R1_")
            or n.startswith("_M3_R2_")
            for n in names
        ):
            parts.append(ast.dump(node, include_attributes=False))
    return "\n".join(parts)


class TestPhase15M3BundleSurfaceIsIdentical:
    def test_the_m3_bundle_function_constants_and_cache_equal_the_reference(self):
        dump = _m3_surface_dump()
        assert dump, "no M3 bundle surface found in database.py"
        assert _sha(dump) == _M3_DIGEST


class TestTwoFilesStayByteIdentical:
    @pytest.mark.parametrize("rel", sorted(_UNTOUCHED_FILE_DIGESTS))
    def test_file_matches_its_reference_digest(self, rel):
        raw = (_REPO / rel).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(raw).hexdigest() == _UNTOUCHED_FILE_DIGESTS[rel]
