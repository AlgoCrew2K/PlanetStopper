"""
RED tests — README CVaR-wire-up status reflects the post-fix reality.

SCOPE — item 6 of the vision-audit Critical Rec #1 dispatch brief
-----------------------------------------------------------------
After the CVaR per-cycle wire-up is fixed, the README's pre-fix language
becomes incorrect. The doc-writer's claims that the test scans for and
rejects:

  §3.2 "CVaR is staged, not live"
  §3.2 "the per-cycle live path writes all-`None` sentinels to the
        `cvar_diagnostic` table ([`alpha_bot_execution.py:1417-1426`]...)"
  §3.2 "the wire from the per-cycle path to the function is intentionally
        deferred to Phase 1.5"
  §3.2 "The dashboard's CVaR Diagnostic panel therefore shows the framing
        labels (...) with empty numeric cells today"
  §4.4 "deferred to Phase 1.5"
  §4.4 "On the current branch (`a0591b0`), the per-cycle live path writes
        all-`None` sentinels into the `cvar_diagnostic` table"
  §4.4 "the numeric cells are empty pending the live wire-up"
  §5   "writing `None` sentinels today; the live wire-up is Phase 1.5"
  "AlphaBot does not currently compute CVaR in the live path"
  "the per-cycle live wire-up is staged for Phase 1.5"

Post-fix, every one of those sentences is FALSE.

The vision-audit recommendation flags these as required edits when the
wire-up lands. This test pins the contract: the README cannot ship with
language that contradicts the runtime behaviour.

Mocking philosophy
------------------
Pure source-scan. No runtime imports beyond pathlib.
"""

from __future__ import annotations

import pathlib

import pytest


_README_PATH = pathlib.Path(__file__).parents[2] / "README.md"


# Phrases that are FALSE once the wire-up fix lands. Each entry is a
# substring match against the README source.
#
# When the impl-cvar agent updates the README, every entry on this list
# should be either deleted or rephrased to past tense / "previously the
# per-cycle write used None sentinels — fixed in cycle/fix-cvar-wireup".
_STALE_PHRASES_THAT_MUST_GO = [
    "CVaR is staged, not live",
    "the per-cycle live path writes all-`None` sentinels",
    "intentionally deferred to Phase 1.5",
    "deferred to Phase 1.5",
    "empty numeric cells today",
    "empty pending the live wire-up",
    "writing `None` sentinels today",
    "the live wire-up is Phase 1.5",
    "AlphaBot does not currently compute CVaR in the live path",
    "the per-cycle live wire-up is staged for Phase 1.5",
    "the wire from the per-cycle path to the function is intentionally\ndeferred",
]


@pytest.mark.parametrize("phrase", _STALE_PHRASES_THAT_MUST_GO)
def test_readme_no_longer_claims_cvar_is_unwired(phrase):
    """Each pre-fix README phrase that describes CVaR as unwired must be
    removed or rephrased once the wire-up lands.

    RED today: the phrase exists in the README.
    GREEN after impl-cvar updates the README sections §3.2, §4.4, and §5.
    """
    source = _README_PATH.read_text(encoding="utf-8")
    assert phrase not in source, (
        f"README.md still contains pre-fix phrase: {phrase!r}. The CVaR per-cycle "
        "wire-up has been fixed in cycle/fix-cvar-wireup — this language is now "
        "incorrect. Update README §3.2, §4.4, and §5 to describe CVaR as a live "
        "diagnostic (still diagnostic-only, never a trigger — see §4.4 + "
        "[[project_eut_cvar_migration_council_verdict]])."
    )


def test_readme_still_states_cvar_remains_diagnostic_only():
    """The wire-up fix changes CVaR from 'unwired and diagnostic-only' to
    'wired and diagnostic-only'. The diagnostic-only constraint MUST
    survive — the council verdict explicitly rejected the EUT+CVaR
    migration that would have made CVaR a trigger (project memory
    ``project_eut_cvar_migration_council_verdict``).

    The README should still carry the diagnostic-only language.
    """
    source = _README_PATH.read_text(encoding="utf-8")
    # At least one of these anchor phrases must remain.
    anchor_phrases = [
        "CVaR is **never** a live trigger",
        "diagnostic-only",
        "operator diagnostic",
        "diagnostic, not a signal",
    ]
    hits = [p for p in anchor_phrases if p in source]
    assert hits, (
        "README.md no longer carries a diagnostic-only anchor phrase. At least "
        "one of {anchor_phrases!r} must remain — the wire-up fix changes the "
        "operational status, not the architectural constraint. CVaR remains "
        "diagnostic-only post-fix (project memory "
        "[[project_eut_cvar_migration_council_verdict]])."
    )
