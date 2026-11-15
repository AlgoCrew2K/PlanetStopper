"""Scope pin for the preconditions replay read: two protected files, only the listed edits.

The slice edits two files that other pins hold byte for byte:

* ``autotuner.py`` -- ``build_if_held_replay_series`` (it answers from the request scope), the
  store, request-scope and shared-decode symbols (``_SharedDecode``, ``_IfHeldReplayStore``,
  ``_derive_if_held_series``, ``_IF_HELD_REPLAY_STORE``, ``_IF_HELD_REPLAY_REQUEST``,
  ``if_held_replay_request``, ``_scoped_if_held_replay_probe``) and the imports they need.
* ``synthetic_history.py`` -- ``get_cached_synthetic_history_only``, ``load_cached_history``
  (a keyword-only ``on_error`` that reports the caught exception; its warning and its return
  are unchanged) and the one import their annotations need (``from collections.abc import
  Callable``).

A full-file pin cannot tell "only the listed edits" from "something else too", so each file is
pinned with its listed edit nodes CUT, by AST, with the cut helpers of
``tests/test_hygiene_protected_surfaces.py`` (imported, not copied): the text without the listed
nodes must equal the reference commit's text with the same cut. The digests are the reference
commit's (``aafe476d``, the plan's base): each was computed from ``git show aafe476d:<path>``,
LF-normalized, cut with the selectors below. A change that stays inside the listed edits leaves
both digests unchanged; a byte changed anywhere else (the replay functions, the writer, the key
derivation) moves one.

STOP and ask the PM before re-taking either digest: a change means a file moved outside what the
slice may touch. The full-file pins of these two files are re-taken only once these pass.
"""

from __future__ import annotations

import ast

import pytest

from tests.test_hygiene_protected_surfaces import (
    Selector,
    _module_import_of,
    _module_level_functions,
    _read,
    _union,
    cut_text,
    scope_digest,
)

_AUTOTUNER_FUNCTIONS = (
    "build_if_held_replay_series",
    "_derive_if_held_series",
    "if_held_replay_request",
    "_scoped_if_held_replay_probe",
)
_AUTOTUNER_CLASSES = ("_SharedDecode", "_IfHeldReplayStore")
_AUTOTUNER_ASSIGNMENTS = ("_IF_HELD_REPLAY_STORE", "_IF_HELD_REPLAY_REQUEST")
_AUTOTUNER_IMPORTS = ("contextlib", "contextvars", "threading")


def _module_classes(*names: str) -> Selector:
    return lambda tree: [
        (n, False) for n in tree.body if isinstance(n, ast.ClassDef) and n.name in names
    ]


def _module_assignments_with_comments(*names: str) -> Selector:
    """Module-level ``NAME = ...`` and ``NAME: type = ...``, with the comment block above."""

    def target_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return node.target.id
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            return target.id if isinstance(target, ast.Name) else None
        return None

    return lambda tree: [(n, True) for n in tree.body if target_name(n) in names]


def _module_from_import(module: str, name: str) -> Selector:
    return lambda tree: [
        (n, False)
        for n in tree.body
        if isinstance(n, ast.ImportFrom)
        and n.module == module
        and any(alias.name == name for alias in n.names)
    ]


def _decorators_of(*names: str) -> Selector:
    """The decorator lines of the named module-level functions (a function node starts at its
    ``def`` line, so its decorators would otherwise stay behind in the cut text)."""
    return lambda tree: [
        (decorator, False)
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in names
        for decorator in n.decorator_list
    ]


_AUTOTUNER_EDITS = _union(
    _module_level_functions(*_AUTOTUNER_FUNCTIONS),
    _decorators_of(*_AUTOTUNER_FUNCTIONS),
    _module_classes(*_AUTOTUNER_CLASSES),
    _module_assignments_with_comments(*_AUTOTUNER_ASSIGNMENTS),
    *(_module_import_of(module) for module in _AUTOTUNER_IMPORTS),
    _module_from_import("collections.abc", "Iterator"),
)
_SYNTHETIC_HISTORY_EDITS = _union(
    _module_level_functions("get_cached_synthetic_history_only", "load_cached_history"),
    _module_from_import("collections.abc", "Callable"),
)

_SCOPE = {
    "autotuner.py": _AUTOTUNER_EDITS,
    "synthetic_history.py": _SYNTHETIC_HISTORY_EDITS,
}

# LF-normalized sha256 of each file with its listed edits cut (blank-line runs collapsed),
# computed from `git show aafe476d:<path>`; equal to the file at the slice head.
_PINNED_SCOPE_DIGESTS = {
    "autotuner.py": "19f7be1a52a6d0f7f7e017754b9609f220cc97bea6e9abe0f3bda17b36e2735a",
    "synthetic_history.py": "1b284b587a208f354732fb6d5836c46dbd99fc7530a8c8946db047b3a957d6ad",
}


class TestTheFilesHoldOnlyTheListedEdits:
    @pytest.mark.parametrize("rel", sorted(_SCOPE))
    def test_file_outside_its_listed_edits_is_the_reference_text(self, rel):
        actual = scope_digest(_read(rel), _SCOPE[rel])
        assert actual == _PINNED_SCOPE_DIGESTS[rel], (
            f"{rel} changed outside the slice's listed edits (pinned "
            f"{_PINNED_SCOPE_DIGESTS[rel]}, now {actual}). STOP and ask the PM."
        )

    def test_every_listed_autotuner_symbol_is_defined_in_the_file(self):
        """A listed name that no node carries would cut nothing and widen the pin silently."""
        tree = ast.parse(_read("autotuner.py"))
        defined = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                defined.add(node.target.id)
            elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                defined.add(node.targets[0].id)
            elif isinstance(node, ast.Import):
                defined.update(alias.name for alias in node.names)
        listed = {
            *_AUTOTUNER_FUNCTIONS,
            *_AUTOTUNER_CLASSES,
            *_AUTOTUNER_ASSIGNMENTS,
            *_AUTOTUNER_IMPORTS,
        }
        assert listed <= defined, f"listed but not defined: {sorted(listed - defined)}"

    def test_the_replay_and_the_writer_are_not_among_the_listed_edits(self):
        autotuner = _read("autotuner.py")
        synthetic = _read("synthetic_history.py")
        for name in ("_collect_sim_returns", "_collect_sim_returns_dated", "run_simulation"):
            assert f"def {name}(" in cut_text(autotuner, _AUTOTUNER_EDITS)
        for name in (
            "generate_synthetic_history",
            "_resolve_history_cache_key",
        ):
            assert f"def {name}(" in cut_text(synthetic, _SYNTHETIC_HISTORY_EDITS)


class TestThePinIsSensitiveToTheRightThings:
    """The pin is only worth its digests if a stray edit moves one: each case plants a change in
    the real file's text and checks the digest, with no file written."""

    def test_a_byte_changed_in_the_replay_moves_the_autotuner_digest(self):
        text = _read("autotuner.py")
        planted = text.replace("def _collect_sim_returns(", "def _collect_sim_returns (", 1)
        assert planted != text
        assert scope_digest(planted, _AUTOTUNER_EDITS) != _PINNED_SCOPE_DIGESTS["autotuner.py"]

    def test_a_byte_changed_in_the_writer_moves_the_synthetic_history_digest(self):
        text = _read("synthetic_history.py")
        planted = text.replace(
            "def generate_synthetic_history(", "def generate_synthetic_history (", 1
        )
        assert planted != text
        assert (
            scope_digest(planted, _SYNTHETIC_HISTORY_EDITS)
            != _PINNED_SCOPE_DIGESTS["synthetic_history.py"]
        )

    def test_a_comment_planted_between_cut_nodes_moves_the_digest(self):
        text = _read("autotuner.py")
        planted = text.replace(
            "def _collect_sim_returns(", "# planted\ndef _collect_sim_returns(", 1
        )
        assert scope_digest(planted, _AUTOTUNER_EDITS) != _PINNED_SCOPE_DIGESTS["autotuner.py"]

    def test_a_second_statement_planted_beside_a_cut_node_moves_the_digest(self):
        text = _read("autotuner.py")
        planted = text.replace(
            "_IF_HELD_REPLAY_STORE = _IfHeldReplayStore()",
            "_IF_HELD_REPLAY_STORE = _IfHeldReplayStore()\n_PLANTED = 1",
            1,
        )
        assert planted != text
        assert scope_digest(planted, _AUTOTUNER_EDITS) != _PINNED_SCOPE_DIGESTS["autotuner.py"]

    def test_a_change_inside_a_listed_node_does_not_move_the_digest(self):
        text = _read("autotuner.py")
        planted = text.replace(
            "def _derive_if_held_series(", "def _derive_if_held_series(  # planted\n   ", 1
        )
        assert planted != text
        assert scope_digest(planted, _AUTOTUNER_EDITS) == _PINNED_SCOPE_DIGESTS["autotuner.py"]

    def test_crlf_and_lf_checkouts_hash_the_same(self):
        text = _read("synthetic_history.py")
        assert scope_digest(text.replace("\n", "\r\n"), _SYNTHETIC_HISTORY_EDITS) == scope_digest(
            text, _SYNTHETIC_HISTORY_EDITS
        )
