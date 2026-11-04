"""Content pins for the regime-label-producer slice (AC-6, S-6.4).

The slice changes one statement in the engine (the cached-label read) and the
cache accessor in ``database.py``. Everything else that decides an exit, labels
a regime, replays history or defines the cache table must stay byte-identical to
the reference commit AlphaBotPM@ff161d72:

* ``math_engine.py`` -- the 2/5/3 table, ``EXIT_CONFIRM_TICKS`` and every exit layer
* ``regime_classifier.py`` -- ``classify_regime``, ``MIN_LABEL_SERIES_LENGTH``, the 0.04 threshold
* ``autotuner.py`` -- the replay and ``_replay_resolve_regime_exit_ticks``
* ``synthetic_history.py`` -- the replay's basis
* ``migrations/027_regime_label_cache.sql`` -- the cache schema
* ``alpha_bot_execution.py`` with the ``_regime_exit_ticks`` assignment cut out

Hashes are LF-normalized (CRLF -> LF on the raw bytes before hashing) so they are
identical on Windows and Linux checkouts, as in
``tests/test_mdd_window_truth_zero_diff_guard.py``.

Provenance: every digest was computed from ``git show ff161d72:<path>`` through
``_sha256_bytes`` below and equals the digest of the same file in the worktree at
RED. The engine digest comes from ``hash_without_regime_read`` applied to the
engine at ff161d72. STOP and ask the PM before re-taking any of them: a change
means a file moved outside what this slice may touch.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ENGINE = _REPO_ROOT / "alpha_bot_execution.py"

# math_engine.py, regime_classifier.py and autotuner.py follow the hygiene-waves-3-4 edits.
_PINNED_HASHES = {
    "math_engine.py": "343d303c29ee9aa4c9f390791603a12f3130eec456fe589b3959732cd4477f79",
    "regime_classifier.py": "04f8e1aa174c1802b8a4981522ce57d0fe9b801f7782323c298b719e3d9c1d52",
    "autotuner.py": "9f2fe9455fe13521061308fbe932e5b1b62551f0d2e8e5a12ced01fe47ed9a4f",
    "synthetic_history.py": "7a7df0162e0895649e42e8915c98653f6336118ca5a3379ca09d6d4561bb2883",
    "migrations/027_regime_label_cache.sql": (
        "46b63e476670db9eb57afcccb6a15e8e78b8a9ddc08d7ca50680d6c2a26fe8d2"
    ),
}

# alpha_bot_execution.py with the _regime_exit_ticks assignment cut; follows the
# hygiene-waves-3-4 engine edits outside that statement.
_PINNED_ENGINE_WITHOUT_REGIME_READ = (
    "3d865b08ac6e2f977e48fc4078db59e3910e9be21158b2431a9e4e273295ad21"
)


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def _regime_read_statement(tree: ast.Module) -> ast.Assign:
    """The single ``_regime_exit_ticks = ...`` assignment in ``main()``."""
    main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    found = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_regime_exit_ticks" for t in node.targets)
    ]
    assert len(found) == 1, (
        f"expected one _regime_exit_ticks assignment in main(), found {len(found)}"
    )
    return found[0]


def hash_without_regime_read(text: str) -> str:
    """LF-normalized sha256 of ``text`` with the ``_regime_exit_ticks`` assignment's
    lines removed (the cut-by-AST pattern of
    tests/engine/test_close_snapshot_builder_scope.py::hash_without_builder)."""
    text = text.replace("\r\n", "\n")
    stmt = _regime_read_statement(ast.parse(text))
    lines = text.split("\n")
    remaining = lines[: stmt.lineno - 1] + lines[stmt.end_lineno :]
    return hashlib.sha256("\n".join(remaining).encode("utf-8")).hexdigest()


class TestProtectedFilesAreByteUnchanged:
    def test_each_protected_file_matches_its_reference_hash(self):
        mismatches = {}
        for rel, pinned in _PINNED_HASHES.items():
            actual = _sha256_bytes((_REPO_ROOT / rel).read_bytes())
            if actual != pinned:
                mismatches[rel] = actual
        assert not mismatches, (
            "protected files changed from the reference commit ff161d72 "
            f"(path -> current hash): {mismatches}"
        )


class TestEngineChangesOnlyAtTheRegimeRead:
    def test_engine_outside_the_regime_read_statement_is_byte_unchanged(self):
        actual = hash_without_regime_read(_ENGINE.read_text(encoding="utf-8"))
        assert actual == _PINNED_ENGINE_WITHOUT_REGIME_READ, (
            "alpha_bot_execution.py changed outside the _regime_exit_ticks assignment "
            f"(pinned {_PINNED_ENGINE_WITHOUT_REGIME_READ}, now {actual}); only the "
            "cached-label read may change in this slice"
        )

    def test_the_statement_still_applies_the_adjustment_to_the_cached_read(self):
        stmt = _regime_read_statement(ast.parse(_ENGINE.read_text(encoding="utf-8")))
        names = {
            n.attr if isinstance(n, ast.Attribute) else n.id
            for n in ast.walk(stmt.value)
            if isinstance(n, (ast.Attribute, ast.Name))
        }
        assert "apply_regime_exit_adjustment" in names
        assert "get_cached_regime_label" in names
        assert "EXIT_CONFIRM_TICKS" in names

    def test_the_cache_has_exactly_one_engine_reader(self):
        tree = ast.parse(_ENGINE.read_text(encoding="utf-8"))
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get_cached_regime_label"
        ]
        assert len(calls) == 1, f"expected one engine read of the cache, found {len(calls)}"

    def test_the_engine_never_writes_the_cache(self):
        assert "save_regime_label" not in _ENGINE.read_text(encoding="utf-8")


class TestTheCutIsSensitiveToTheRightThings:
    """The pin must ignore a change inside the statement and fail on one outside."""

    _SRC = "def main():\n    a = 1\n    _regime_exit_ticks = f(\n        x=1,\n    )\n    b = 2\n"

    def test_changing_the_statement_does_not_move_the_hash(self):
        changed = self._SRC.replace("x=1,", "x=1,\n        session_date=d,")
        assert hash_without_regime_read(changed) == hash_without_regime_read(self._SRC)

    def test_changing_any_other_line_moves_the_hash(self):
        base = hash_without_regime_read(self._SRC)
        assert hash_without_regime_read(self._SRC.replace("b = 2", "b = 3")) != base
        assert hash_without_regime_read(self._SRC + "    c = 3\n") != base

    def test_a_second_statement_next_to_the_read_moves_the_hash(self):
        planted = self._SRC.replace("    b = 2\n", "    b = 2\n    save()\n")
        assert hash_without_regime_read(planted) != hash_without_regime_read(self._SRC)

    def test_crlf_and_lf_checkouts_hash_the_same(self):
        assert hash_without_regime_read(self._SRC.replace("\n", "\r\n")) == (
            hash_without_regime_read(self._SRC)
        )
