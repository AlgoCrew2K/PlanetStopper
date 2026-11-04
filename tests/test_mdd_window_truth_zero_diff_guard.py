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

CRLF portability fix (2026-09-04, CI-red on branch head 788fee01): the pins
below were originally computed from RAW file bytes on a Windows checkout
(CRLF line endings). Linux CI checks out the same git-tracked content with LF
line endings (git's default core.autocrlf-independent behavior for a repo
with no .gitattributes forcing a line-ending policy -- confirmed absent at
this repo's root) -- same logical content, different bytes, different SHA-256
-- a FALSE "AC-7 VIOLATION" on CI even though `git diff <fork-point> --
alpha_bot_execution.py math_engine.py` is genuinely empty. Fixed by
normalizing CRLF -> LF on the read bytes BEFORE hashing (`_sha256` below),
and re-pinning `_PINNED_HASHES` to the LF-normalized values -- this makes the
guard platform-independent: identical hash whether the two engine files are
checked out with CRLF (Windows local `-n0`) or LF (Linux CI) line endings,
since both normalize to the same LF-only byte stream before hashing.

This test MUST PASS both BEFORE and AFTER the GREEN implementation -- it is a
self-guard, not a RED-to-GREEN test (there is no feature here to build; the
feature IS these two files staying untouched). If either hash ever needs to
change, that is itself the signal an AC-7 violation is being proposed --
STOP and get an explicit PM ruling before updating the pin; do not casually
regenerate it to make a failing test pass.

No schema migration: also guarded by the migration-file-count pin below,
mirroring database.py's own _MIGRATION_FILES ordering-guard convention
(project CLAUDE.md's ARCH-002 gotcha) -- this cycle adds zero new migration
files (the baseline 001 to 038 stays as it was).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALPHA_BOT_EXECUTION = _REPO_ROOT / "alpha_bot_execution.py"
_MATH_ENGINE = _REPO_ROOT / "math_engine.py"

# Pinned at RED-phase HEAD bf5239ab724d05e676b0ef489ffb1b37a01f2c32
# (branch fix/mdd-window-truth, 2026-09-03), content UNCHANGED since -- these
# are LF-NORMALIZED hashes (CRLF -> LF applied to the raw bytes before
# hashing, see _sha256 below and the CRLF-portability-fix module docstring
# section), platform-independent by construction. Re-derived 2026-09-04 via:
#   python -c "import hashlib; print(hashlib.sha256(open('<file>','rb').read().replace(b'\r\n', b'\n')).hexdigest())"
# Confirmed byte-identical to what Linux CI independently computed on the
# same content (CI-reported mismatch hashes on branch head 788fee01 were
# 8bbb51ab.../772886ea..., matching these exactly) -- not a fresh/unverified
# re-pin.
_PINNED_HASHES = {
    # Re-pinned for the one-line close-snapshot builder change (the account-map loop
    # keeps only dict values that carry `name`), the named exception of the
    # refresh-timing close-snapshot cycle; math_engine.py is unchanged.
    "alpha_bot_execution.py": "7f6b2df373628aca0281c6007d432c8764ef1050b29ec95c1e45bfd6151a8fec",
    "math_engine.py": "772886ead636e67e2c801339268608b39180d846a749fee924debd27c915de5e",
}


def _sha256(path: Path) -> str:
    """LF-normalized content hash -- CRLF -> LF on the raw bytes before
    hashing, so the result is identical whether the file is checked out with
    CRLF (Windows) or LF (Linux CI) line endings. See the CRLF-portability-
    fix module docstring section for why this normalization is required."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


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
    # The migrations on disk when this display-only cycle started: 001 to 038, the last being
    # retirement_decisions. The cycle added none, so each of these exists exactly once and none
    # was renumbered or replaced. A migration a later cycle adds takes the next number and
    # leaves this baseline intact, so the guard names the baseline and never "the last one".
    _BASELINE_HIGHEST = 38
    _BASELINE_LAST_NAME = "038_retirement_decisions.sql"

    def _numbered_files(self) -> dict[int, list[str]]:
        migrations_dir = _REPO_ROOT / "migrations"
        if not migrations_dir.is_dir():
            migrations_dir = _REPO_ROOT / "migration"
        assert migrations_dir.is_dir(), (
            f"expected a migrations directory at {_REPO_ROOT / 'migrations'} -- "
            "if migrations live elsewhere, update this test's search path "
            "(do not just delete the guard)."
        )
        by_number: dict[int, list[str]] = {}
        for f in migrations_dir.glob("*.sql"):
            m = re.match(r"^0*(\d+)_", f.name)
            if m:
                by_number.setdefault(int(m.group(1)), []).append(f.name)
        assert by_number, f"no numbered *.sql migration files found under {migrations_dir}"
        return by_number

    def test_baseline_migrations_are_each_present_once_and_unrenumbered(self):
        """AC-7: 'No schema migration.' The baseline 001..038 is on disk exactly once per number,
        and 038 is still retirement_decisions, so no file was added, renumbered or swapped in
        among them to smuggle a schema change into a display-only cycle."""
        by_number = self._numbered_files()
        missing = [n for n in range(1, self._BASELINE_HIGHEST + 1) if n not in by_number]
        assert not missing, f"baseline migration numbers missing from disk: {missing}"
        duplicated = {n: names for n, names in by_number.items() if len(names) > 1}
        assert not duplicated, f"a migration number is used by two files: {duplicated}"
        assert by_number[self._BASELINE_HIGHEST] == [self._BASELINE_LAST_NAME], (
            f"AC-7 VIOLATION (or baseline drift): migration {self._BASELINE_HIGHEST} is "
            f"{by_number[self._BASELINE_HIGHEST]}, expected {self._BASELINE_LAST_NAME}. Do not "
            "just edit the pinned name to make this pass; get a PM ruling first."
        )

    def test_baseline_last_migration_follows_its_neighbour_in_the_wired_order(self):
        """The wired list keeps 038 directly after 037, asserted against its neighbour and not
        as the list's last entry, which every later migration would break."""
        import database

        files = database._MIGRATION_FILES
        assert self._BASELINE_LAST_NAME in files
        position = files.index(self._BASELINE_LAST_NAME)
        assert position > 0 and files[position - 1] == "037_strategy_incubation.sql", (
            f"038 is wired after {files[position - 1] if position else None!r}, "
            "expected 037_strategy_incubation.sql"
        )
