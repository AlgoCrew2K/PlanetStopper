"""
RED tests for the MC parity-column reclassification and the RegimeMatchAssessment
__post_init__ runtime guard (cycle: mc-parity-postinit; PERF-005 sister fix).

WHY THIS TEST EXISTS
--------------------
Two non-blocking observations from rev-mc need RED tests to convert into binding
contract:

  Observation 1 — parity-column reclassification.
    Today `mc_regime_match_mean_dist2` and `mc_regime_match_suppressed` live in
    `database._PARITY_EXCLUDE_COLUMNS` (database.py:2397-2409). The suppression
    decision is currently only observable via downstream `cvar_5pct` parity, which
    obscures the "why did suppression flip?" answer the replay audit trail is
    supposed to give directly. Promoting both columns to
    `_PARITY_DECISION_COLUMNS` puts the suppression-flip signal on the replay
    parity surface where Gate-1 already enforces bit-identity.

  Observation 2 — __post_init__ runtime guard.
    `RegimeMatchAssessment` (math_engine.py:188-216) documents the fail-safe pair
    "is_unprecedented MUST be False when mean_sq_mahalanobis is None" but does
    NOT implement a `__post_init__` that raises on the illegal combination.
    `CVaRAssessment.__post_init__` (math_engine.py:160-185) sets the precedent —
    it raises `ValueError` on the symmetric illegal combination
    (`cvar_pct is None and breach is True`). Parity-align by adding the same
    runtime ValueError to `RegimeMatchAssessment`.

WHAT IS ASSERTED
----------------
1. _PARITY_DECISION_COLUMNS includes BOTH `mc_regime_match_mean_dist2` and
   `mc_regime_match_suppressed`.
2. _PARITY_EXCLUDE_COLUMNS does NOT include either of those two columns.
3. The two sets remain disjoint (a column cannot be both asserted and excluded;
   anti-overlap fence from the existing replay-determinism-anchor suite).
4. RegimeMatchAssessment.__post_init__ raises ValueError when
   mean_sq_mahalanobis is None AND is_unprecedented=True (illegal combination
   the docstring forbids).
5. RegimeMatchAssessment.__post_init__ permits the legal None+False sentinel —
   exactly the construction at math_engine.py:1646-1656 must remain valid.
6. RegimeMatchAssessment.__post_init__ permits a present-distance instance
   (the construction at math_engine.py:1707-1713 must remain valid for both
   is_unprecedented=True and is_unprecedented=False).
7. CVaRAssessment.__post_init__ behaviour is UNCHANGED — the precedent at
   math_engine.py:160-185 still raises on its three documented illegal
   combinations. This is the "no-regress" parity test for Observation 2.

The replay-audit Gate-1 round-trip fixture (test_gate1_parity_round_trip_via_accessor)
already exists in tests/database/test_replay_determinism_anchor.py and exercises
the five original parity columns only — it does not write the new MC columns, so
it continues to pass after the implementer adds the two columns to the decision
set. A round-trip assertion specifically for the two new columns lives below
(test_mc_parity_columns_round_trip_via_accessor) so implementer cannot satisfy
(1)-(2) by reclassification without also ensuring the read path returns the new
fields.

PA-18 compliance: no producer-computed floats are asserted. Distances and the
suppressed flag are caller-supplied integers/floats; the test only asserts
write-equals-read.

RED STATUS
----------
1-3: RED until implementer moves the two MC columns from _PARITY_EXCLUDE_COLUMNS
     to _PARITY_DECISION_COLUMNS in database.py.
4-6: RED until implementer adds a __post_init__ to RegimeMatchAssessment that
     raises ValueError on the illegal (None, True) combination.
7:   GREEN today — locked as a no-regress fence for the CVaRAssessment precedent.
"""

from __future__ import annotations

import pathlib
import sqlite3
from unittest.mock import patch

import pytest

import database as db
import math_engine


# ---------------------------------------------------------------------------
# isolated_db fixture (local copy — mirrors tests/database/test_replay_*.py)
# ---------------------------------------------------------------------------

_CVAR_DIAGNOSTICS_DDL_WITH_MC = """
CREATE TABLE IF NOT EXISTS cvar_diagnostics (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id                        TEXT    NOT NULL,
    ts_utc                          TEXT    NOT NULL DEFAULT (datetime('now')),
    symphony_id                     TEXT    NOT NULL,
    cvar_5pct                       REAL    DEFAULT NULL,
    cvar_5pct_stderr                REAL    DEFAULT NULL,
    cvar_n_tail                     INTEGER DEFAULT NULL,
    cvar_5pct_long                  REAL    DEFAULT NULL,
    cvar_n_tail_long                INTEGER DEFAULT NULL,
    mc_regime_match_mean_dist2      REAL    DEFAULT NULL,
    mc_regime_match_suppressed      INTEGER DEFAULT NULL
)
"""


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Isolated SQLite with the post-migration-026 cvar_diagnostics schema.

    We define the DDL inline here (rather than relying on init_db's migration
    runner) so the round-trip test below is decoupled from migration-027+
    additions: any future migration may add columns to cvar_diagnostics; this
    fixture pins the columns this test needs (the original 5 + the two MC
    parity columns) and lets _PARITY_EXCLUDE_COLUMNS / _PARITY_DECISION_COLUMNS
    drift over time without breaking RED here.
    """
    db_path = str(tmp_path / "mc_parity_test.db")
    monkeypatch.setenv("DB_PATH", db_path)
    with patch.object(db, "DB_FILE", db_path):
        db.init_db()
        # Drop and recreate the table with the columns this test exercises —
        # init_db may apply migrations that differ from the DDL above; we want
        # exactly the schema described in this test, including the two MC
        # columns that migration 026 adds.
        conn = sqlite3.connect(db_path)
        conn.execute("DROP TABLE IF EXISTS cvar_diagnostics")
        conn.execute(_CVAR_DIAGNOSTICS_DDL_WITH_MC)
        conn.commit()
        conn.close()
        yield db_path


# ---------------------------------------------------------------------------
# Observation 1 — parity-column reclassification
# ---------------------------------------------------------------------------


_MC_PARITY_COLUMNS = ("mc_regime_match_mean_dist2", "mc_regime_match_suppressed")


def test_mc_regime_match_columns_are_in_parity_decision_columns() -> None:
    """Both MC regime-match telemetry columns must be in _PARITY_DECISION_COLUMNS.

    Suppression is a decision-content signal (it flips run_monte_carlo's
    mc_prob to None, which then flows to the exit-confirm layer). The replay
    audit trail must directly assert bit-identity of the suppression flag and
    its driving distance score — otherwise a "why did suppression flip across
    two replays of the same cycle_id?" question can only be inferred from
    downstream cvar_5pct parity, which conflates suppression with every other
    upstream determinant of cvar_5pct.
    """
    decision = set(db._PARITY_DECISION_COLUMNS)
    for col in _MC_PARITY_COLUMNS:
        assert col in decision, (
            f"'{col}' is missing from _PARITY_DECISION_COLUMNS. "
            "Per rev-mc Observation 1, the regime-match telemetry columns "
            "must be on the Gate-1 replay parity surface so suppression flips "
            "are directly auditable across replays (database.py:2389-2395)."
        )


def test_mc_regime_match_columns_are_not_in_parity_exclude_columns() -> None:
    """Both MC regime-match telemetry columns must be ABSENT from _PARITY_EXCLUDE_COLUMNS.

    The current (pre-fix) classification at database.py:2402-2408 places these
    columns in the exclude set so the existing round-trip test doesn't KeyError.
    After reclassification, they are decision-content columns; leaving them in
    the exclude set would create a contradictory dual classification — a column
    cannot be both asserted and excluded by the parity audit.
    """
    exclude = set(db._PARITY_EXCLUDE_COLUMNS)
    for col in _MC_PARITY_COLUMNS:
        assert col not in exclude, (
            f"'{col}' is still in _PARITY_EXCLUDE_COLUMNS. "
            "Per rev-mc Observation 1, this column was reclassified as "
            "decision-content; it must NOT remain in the exclude set "
            "(database.py:2397-2409)."
        )


def test_parity_decision_and_exclude_sets_remain_disjoint() -> None:
    """The two parity sets must remain disjoint after reclassification.

    Mirrors the anti-overlap guard already enforced by
    tests/database/test_replay_determinism_anchor.py::
    test_parity_decision_and_exclude_columns_are_disjoint. Replicated here so
    the rev-mc reclassification fix has its own immediate fence: an implementer
    who promotes the MC columns by adding to decision WITHOUT removing from
    exclude is caught by this test in this file, not only by a sibling test
    elsewhere.
    """
    decision_set = set(db._PARITY_DECISION_COLUMNS)
    exclude_set = set(db._PARITY_EXCLUDE_COLUMNS)
    overlap = decision_set & exclude_set
    assert not overlap, (
        f"Columns appear in both _PARITY_DECISION_COLUMNS and "
        f"_PARITY_EXCLUDE_COLUMNS: {sorted(overlap)}. "
        "Reclassification must REMOVE from exclude when ADDING to decision; "
        "a column cannot be both asserted and excluded by Gate-1 parity."
    )


def test_mc_parity_columns_round_trip_via_accessor(isolated_db) -> None:
    """After reclassification, written MC parity column values must round-trip.

    Writes one cvar_diagnostics row with caller-supplied values for the two MC
    columns, then reads it back via read_cvar_diagnostic_for_cycle and asserts
    the values returned for those keys match the values written. This is the
    "decision-content" smoke for Observation 1: if the accessor cannot return
    these columns, Gate-1 parity cannot assert on them no matter what
    _PARITY_DECISION_COLUMNS says.

    PA-18: written values are test-controlled (not producer-derived).
    """
    cycle_id = "rev-mc-obs1-cycle"
    symphony_id = "rev-mc-obs1-symphony"

    # Test-controlled inputs — not producer-computed.
    written_mean_dist2 = 12.5
    written_suppressed = 1

    db.record_cvar_diagnostic(
        cycle_id=cycle_id,
        symphony_id=symphony_id,
        cvar_5pct=-0.04,
        cvar_5pct_stderr=0.007,
        cvar_n_tail=5,
        cvar_5pct_long=-0.05,
        cvar_n_tail_long=4,
        mode="replay",
        mc_regime_match_mean_dist2=written_mean_dist2,
        mc_regime_match_suppressed=written_suppressed,
    )

    row = db.read_cvar_diagnostic_for_cycle(cycle_id, symphony_id)

    assert row is not None, (
        f"read_cvar_diagnostic_for_cycle returned None after writing a row "
        f"for ('{cycle_id}', '{symphony_id}'). Accessor must return the "
        "stored row so Gate-1 can compare MC parity columns."
    )

    # Tolerance: rel=1e-9 is well below any meaningful precision for a squared
    # Mahalanobis-style distance (storage round-trip through IEEE 754 double
    # SQLite REAL); strict equality is acceptable but pytest.approx documents
    # the intent that this is a storage fidelity test, not a math invariant.
    assert row["mc_regime_match_mean_dist2"] == pytest.approx(written_mean_dist2, rel=1e-9), (
        f"mc_regime_match_mean_dist2 round-trip mismatch: "
        f"wrote {written_mean_dist2!r}, read back "
        f"{row['mc_regime_match_mean_dist2']!r}."
    )
    assert row["mc_regime_match_suppressed"] == written_suppressed, (
        f"mc_regime_match_suppressed round-trip mismatch: "
        f"wrote {written_suppressed!r}, read back "
        f"{row['mc_regime_match_suppressed']!r}."
    )


# ---------------------------------------------------------------------------
# Observation 2 — RegimeMatchAssessment.__post_init__ runtime guard
# ---------------------------------------------------------------------------


def test_regime_match_assessment_raises_when_none_distance_and_unprecedented() -> None:
    """RegimeMatchAssessment.__post_init__ must raise ValueError on the illegal
    combination (mean_sq_mahalanobis is None) AND (is_unprecedented is True).

    The docstring at math_engine.py:188-216 documents this as a fail-safe pair —
    an absent diagnostic must not be a suppression signal. Until __post_init__
    enforces the contract at construction time, a bug at a future call site can
    silently build an "is_unprecedented=True without a distance" instance and
    flip MC suppression on a cycle where the regime-match guard was supposed to
    abstain.

    CVaRAssessment's __post_init__ (math_engine.py:160-185) sets the precedent
    for raising on the symmetric (cvar_pct is None, breach is True) illegal
    combination. Parity with that precedent is the binding test here.
    """
    with pytest.raises(ValueError):
        math_engine.RegimeMatchAssessment(
            mean_sq_mahalanobis=None,
            is_unprecedented=True,  # illegal — None distance cannot drive suppression
            neighbor_k=8,
            threshold_used=9.21,
            insufficient_reason=None,
        )


def test_regime_match_assessment_permits_legal_none_distance_sentinel() -> None:
    """The legal insufficient-pool sentinel must continue to construct.

    The production call at math_engine.py:1646-1656 builds
    RegimeMatchAssessment(mean_sq_mahalanobis=None, is_unprecedented=False, ...)
    on the eligible-pool-too-short path. This is the fail-safe value; the new
    __post_init__ must NOT reject it. A test that catches "raises on legal
    construction" is essential because the runtime guard must distinguish the
    legal None+False case from the illegal None+True case.
    """
    assessment = math_engine.RegimeMatchAssessment(
        mean_sq_mahalanobis=None,
        is_unprecedented=False,  # legal — fail-safe: absent diagnostic is not suppression
        neighbor_k=8,
        threshold_used=9.21,
        insufficient_reason="eligible pool too short",
    )
    # Property assertions are deliberate (not just "no exception raised") so the
    # test fails informatively if a future change silently drops a field.
    assert assessment.mean_sq_mahalanobis is None
    assert assessment.is_unprecedented is False
    assert assessment.insufficient_reason == "eligible pool too short"


@pytest.mark.parametrize("is_unprecedented", [True, False])
def test_regime_match_assessment_permits_present_distance_both_directions(
    is_unprecedented: bool,
) -> None:
    """A present mean_sq_mahalanobis must construct regardless of is_unprecedented.

    Mirrors the production call at math_engine.py:1707-1713 which builds the
    assessment after computing mean_sq and is_unprecedented = (mean_sq >
    threshold). Both True and False must remain valid constructions — the
    runtime guard exists only to forbid the (None, True) combination, not to
    constrain the present-distance branch.
    """
    assessment = math_engine.RegimeMatchAssessment(
        mean_sq_mahalanobis=14.7,
        is_unprecedented=is_unprecedented,
        neighbor_k=8,
        threshold_used=9.21,
        insufficient_reason=None,
    )
    assert assessment.mean_sq_mahalanobis == pytest.approx(14.7, rel=1e-12)
    assert assessment.is_unprecedented is is_unprecedented
    assert assessment.insufficient_reason is None


# ---------------------------------------------------------------------------
# Observation 2 — CVaRAssessment.__post_init__ behaviour MUST NOT regress
# (precedent at math_engine.py:152-185)
# ---------------------------------------------------------------------------


def test_cvar_assessment_breach_true_with_none_cvar_still_raises() -> None:
    """CVaRAssessment.__post_init__ must continue to raise on (None, breach=True).

    This is the precedent that RegimeMatchAssessment is being parity-aligned
    to. The implementer fix for Observation 2 must not, in adding a sibling
    runtime guard to RegimeMatchAssessment, accidentally weaken the existing
    CVaRAssessment guard. This is the no-regress fence.
    """
    with pytest.raises(ValueError):
        math_engine.CVaRAssessment(
            cvar_pct=None,
            breach=True,  # illegal — absent CVaR cannot trigger a breach
            tail_obs_count=0,
            stderr=None,
            insufficient_reason="no tail observations",
        )


def test_cvar_assessment_present_estimate_requires_stderr() -> None:
    """CVaRAssessment.__post_init__ must continue to raise when stderr is missing.

    Locks the math_engine.py:181-185 precedent: every present estimate must
    carry its standard error. Catches a regression where the fix to add
    RegimeMatchAssessment's __post_init__ inadvertently deletes or weakens
    CVaRAssessment's stderr-pairing branch.
    """
    with pytest.raises(ValueError):
        math_engine.CVaRAssessment(
            cvar_pct=-0.03,
            breach=False,
            tail_obs_count=5,
            stderr=None,  # illegal — present estimate must carry stderr
            insufficient_reason=None,
        )


def test_cvar_assessment_legal_sentinel_still_constructs() -> None:
    """The legal CVaRAssessment None-sentinel must still construct (no regress).

    Pinned because the precedent guard at math_engine.py:160-185 deliberately
    permits cvar_pct=None alongside breach=False, tail_obs_count=0, stderr=None.
    Symmetric to the legal RegimeMatchAssessment None+False sentinel test above.
    """
    assessment = math_engine.CVaRAssessment(
        cvar_pct=None,
        breach=False,
        tail_obs_count=0,
        stderr=None,
        insufficient_reason="insufficient pool",
    )
    assert assessment.cvar_pct is None
    assert assessment.breach is False
    assert assessment.tail_obs_count == 0
    assert assessment.stderr is None
