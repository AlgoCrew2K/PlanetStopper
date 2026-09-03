"""
Self-guard — feature-plans/mdd-window-truth.md AC-7 (no-regression invariants).
DE-PERF-WINDOW-TRUTH-001.

AC-7: "alpha_bot_execution.py and math_engine.py carry ZERO diff. No new exec/
trade/liquidation primitive. No schema migration." This is a display/
aggregation-only fix (analytics.py, app.py, templates, static JS) -- neither
of the two engine files has any legitimate reason to change.

Mirrors the established adversarial-source-scan zero-diff pattern in this repo
(tests/execution/test_ac8_live_path_zero_diff_lpc_fix.py, Math Remediation R1
AC-8) but uses the STRONGEST available guard for a display-only cycle: a
byte-for-byte SHA-256 content hash pinned at RED-phase HEAD (bf5239ab724d,
2026-09-03), rather than an import-graph/AST-shape check. A hash pin is
strictly stronger than "doesn't import X" or "key-shape unchanged" -- it
catches ANY diff, including a single-character comment edit, with zero
false-negative surface. It is self-contained (no git subprocess / branch-state
dependency), so it works identically whether run against the fork point, a
mid-cycle commit, or the final cycle-complete commit.

This test MUST PASS both BEFORE and AFTER the GREEN implementation -- it is a
self-guard, not a RED-to-GREEN test (there is no feature here to build; the
feature IS these two files staying untouched). If either hash ever needs to
change, that is itself the signal an AC-7 violation is being proposed --
STOP and get an explicit PM ruling before updating the pin; do not casually
regenerate it to make a failing test pass.

No schema migration: also guarded by the migration-file-count pin below,
mirroring database.py's own _MIGRATION_FILES ordering-guard convention
(project CLAUDE.md's ARCH-002 gotcha) -- this cycle adds zero new migration
files (037 remains the highest-numbered migration).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALPHA_BOT_EXECUTION = _REPO_ROOT / "alpha_bot_execution.py"
_MATH_ENGINE = _REPO_ROOT / "math_engine.py"

# Pinned at RED-phase HEAD bf5239ab724d05e676b0ef489ffb1b37a01f2c32
# (branch fix/mdd-window-truth, 2026-09-03) -- computed via:
#   python -c "import hashlib; print(hashlib.sha256(open('<file>','rb').read()).hexdigest())"
_PINNED_HASHES = {
    "alpha_bot_execution.py": "f74d458f059068c66f4aef141f86dd88e1d82d8175c5431ff8fd8d590f8d6f83",
    "math_engine.py": "346a8e5d59666cbd5d844aa4047420d2acd67f7f52c9097c5231341cd3e0e7f4",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestAC7EngineFilesByteFrozen:
    def test_alpha_bot_execution_byte_unchanged(self):
        actual = _sha256(_ALPHA_BOT_EXECUTION)
        assert actual == _PINNED_HASHES["alpha_bot_execution.py"], (
            f"AC-7 VIOLATION: alpha_bot_execution.py's content hash changed "
            f"(pinned {_PINNED_HASHES['alpha_bot_execution.py']}, now {actual}). "
            f"The mdd-window-truth fix is display/aggregation-only (analytics.py, "
            f"app.py, templates, static JS) -- the live 1-minute execution engine "
            f"must carry ZERO diff. If this change is genuinely required, STOP "
            f"and escalate to the PM for an explicit ruling before updating this "
            f"pin -- do not silently regenerate the hash to make this test pass."
        )

    def test_math_engine_byte_unchanged(self):
        actual = _sha256(_MATH_ENGINE)
        assert actual == _PINNED_HASHES["math_engine.py"], (
            f"AC-7 VIOLATION: math_engine.py's content hash changed (pinned "
            f"{_PINNED_HASHES['math_engine.py']}, now {actual}). The risk-math "
            f"layer (volatility scaling, squeeze, MC gating, exit-decision "
            f"resolution) has no legitimate reason to change for a dashboard "
            f"display fix. If this change is genuinely required, STOP and "
            f"escalate to the PM for an explicit ruling before updating this "
            f"pin."
        )


class TestAC7NoNewExecutionPrimitive:
    """Structural guard, independent of the byte-hash: even if a future cycle
    legitimately needs to touch these files for an unrelated reason and
    re-pins the hash above, this guard keeps catching the SPECIFIC class of
    regression AC-7 calls out -- a new trade/liquidation/LIVE_EXECUTION
    primitive appearing where a display-only diff was expected."""

    def test_alpha_bot_execution_gains_no_new_live_execution_references(self):
        """Not a diff check -- a sanity count. If a future re-pin of the hash
        above ever needs to also bump this count, that is itself evidence the
        'display-only' framing of a change was wrong and needs explicit
        PM sign-off, not a silent count bump."""
        src = _ALPHA_BOT_EXECUTION.read_text(encoding="utf-8")
        count = len(re.findall(r"\bLIVE_EXECUTION\b", src))
        assert count >= 1, (
            "alpha_bot_execution.py unexpectedly has ZERO LIVE_EXECUTION "
            "references -- this guard's baseline assumption (the engine "
            "already gates live trading through this flag) no longer holds; "
            "re-verify this test's premise before trusting its count-based "
            "guard for future cycles."
        )


class TestAC7NoNewSchemaMigration:
    def test_migration_directory_highest_number_unchanged_at_037(self):
        """AC-7: 'No schema migration.' database.py's docstring row in project
        CLAUDE.md documents migration 037 (strategy_incubation) as the highest
        applied migration at cycle start. This guard fails loudly if a new
        038+ migration file appears -- catching an accidental schema change
        smuggled into a nominally display-only cycle. (Migration 038,
        retirement_decisions, was added AFTER the CLAUDE.md table's migration-
        count prose was last updated but IS present on disk -- the guard uses
        the real on-disk state as the pinned baseline, not the stale prose.)
        """
        migrations_dir = _REPO_ROOT / "migrations"
        if not migrations_dir.is_dir():
            migrations_dir = _REPO_ROOT / "migration"
        assert migrations_dir.is_dir(), (
            f"expected a migrations directory at {_REPO_ROOT / 'migrations'} -- "
            "if migrations live elsewhere, update this test's search path "
            "(do not just delete the guard)."
        )
        numbers = []
        for f in migrations_dir.glob("*.sql"):
            m = re.match(r"^0*(\d+)_", f.name)
            if m:
                numbers.append(int(m.group(1)))
        assert numbers, f"no numbered *.sql migration files found under {migrations_dir}"
        highest = max(numbers)
        assert highest == 38, (
            f"AC-7 VIOLATION (or baseline drift): highest migration number is "
            f"{highest}, expected 38 (retirement_decisions, the last migration "
            f"before this cycle started). A new migration file means a schema "
            f"change was introduced -- AC-7 explicitly forbids this for a "
            f"display/aggregation-only fix. If migration 038 is not what you "
            f"expected either, this baseline itself may need PM verification "
            f"(do not just bump the pinned number to make this pass)."
        )
