"""The engine file changes only inside the close-snapshot builder.

The full-file content pin in ``tests/test_mdd_window_truth_zero_diff_guard.py`` moves
whenever the builder legitimately changes. This module pins the same file with the
builder block cut out, so "changes only inside the builder" stays checked after that
pin is re-taken. The builder block is the ``if`` statement inside ``main()`` whose
test reads ``last_market_close_snapshot`` (it spans the whole snapshot capture: the
account-map loop, the analytics calls and the snapshot dict).

STOP and ask the PM before re-pinning ``_PINNED_HASH_WITHOUT_BUILDER``: a change to it
means the engine changed outside the builder.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENGINE = _REPO_ROOT / "alpha_bot_execution.py"
_APP = _REPO_ROOT / "app.py"
_SNAPSHOT_KEY = "last_market_close_snapshot"

# LF-normalized sha256 of alpha_bot_execution.py with the builder block's lines removed.
# The pin follows the `session_date` keyword change at the regime read (commit 44b178d6) and the
# hygiene-waves-3-4 engine edits (env-flag import, LIVE_EXECUTION parse, dip line count).
# Command: python -I -c "import tests.engine.test_close_snapshot_builder_scope as m; print(m.hash_without_builder())"
_PINNED_HASH_WITHOUT_BUILDER = "f3c66bc496fb61b1313ab6eb4d320c108cb91b95085e289e2c3969352a8f6431"


def _builder_block(tree: ast.Module) -> ast.If:
    main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    candidates = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.If)
        and any(
            isinstance(leaf, ast.Constant) and leaf.value == _SNAPSHOT_KEY
            for leaf in ast.walk(node.test)
        )
    ]
    assert len(candidates) == 1, (
        f"expected exactly one if-statement in main() whose test reads {_SNAPSHOT_KEY!r}, "
        f"found {len(candidates)}"
    )
    return candidates[0]


def hash_without_builder(path: Path = _ENGINE) -> str:
    text = path.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
    block = _builder_block(ast.parse(text))
    lines = text.split("\n")
    remaining = lines[: block.lineno - 1] + lines[block.end_lineno :]
    return hashlib.sha256("\n".join(remaining).encode("utf-8")).hexdigest()


class TestEngineChangesOnlyInsideTheBuilder:
    def test_engine_outside_the_builder_block_is_byte_unchanged(self):
        actual = hash_without_builder()
        assert actual == _PINNED_HASH_WITHOUT_BUILDER, (
            f"alpha_bot_execution.py changed outside the close-snapshot builder block "
            f"(pinned {_PINNED_HASH_WITHOUT_BUILDER}, now {actual}). Only the builder's "
            f"`if` statement inside main() may change; the 1-minute exit path must not."
        )

    def test_builder_block_is_found_and_non_trivial(self):
        block = _builder_block(ast.parse(_ENGINE.read_text(encoding="utf-8")))
        assert block.end_lineno - block.lineno > 20

    def test_engine_never_calls_the_state_repair(self):
        assert "repair_close_snapshot_chain" not in _ENGINE.read_text(encoding="utf-8"), (
            "the repair runs once at daemon start, never on the engine's 1-minute path"
        )

    def test_only_the_daemon_start_block_calls_the_state_repair(self):
        tree = ast.parse(_APP.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "repair_close_snapshot_chain"
        ]
        assert len(calls) == 1, f"expected one reference in app.py, found {len(calls)}"
