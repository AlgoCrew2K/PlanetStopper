"""RED tests — Workstream E / AC-E1: database.save_autotune_run gains s_count.

Migration 023 (023_autotune_runs_s_count.sql) added autotune_runs.s_count
INTEGER DEFAULT NULL, but the write path was never updated: save_autotune_run
(database.py:632-652) has no s_count parameter and the INSERT (database.py:
693-699) does not reference the column. Every row in production has left
s_count permanently NULL since migration 023 shipped — which is why
overfitting_conscience's Indicator-3 drift check (valid_prior_s, which drops
None entries) can never see >= 2 non-None priors on live data.

AC-E1 (feature-plans/advisor-weekly-suggestions.md):
  "database.save_autotune_run gains s_count: int | None = None; the INSERT
   writes it into the existing autotune_runs.s_count column. The table stays
   append-only — no UPDATE path introduced."

These tests are RED until save_autotune_run accepts and persists s_count.

DB isolation: the root tests/conftest.py autouse _isolate_db fixture points
DB_PATH at a fresh per-test SQLite file — no live-DB risk, no fixture setup
needed beyond calling the accessor.
"""

from __future__ import annotations

import inspect

import pytest


def _call_save_autotune_run(db_module, *, symphony_id: str, s_count=None, **overrides) -> int:
    """Call save_autotune_run with the minimal required positional/keyword args.

    Returns the inserted row id (save_autotune_run's documented contract,
    S3-AUDIT-001 — returns cursor.lastrowid).
    """
    kwargs = {
        "run_timestamp": "2026-07-12T12:00:00Z",
        "symphony_id": symphony_id,
        "oos_alpha": 0.5,
        "train_alpha": 1.0,
        "baseline_decision": "Adopted AI",
        "fallback_oos_alpha": 0.2,
        "default_oos_alpha": 0.1,
    }
    kwargs.update(overrides)
    return db_module.save_autotune_run(s_count=s_count, **kwargs)


class TestSaveAutotuneRunAcceptsSCount:
    """AC-E1: signature gains s_count: int | None = None."""

    def test_save_autotune_run_signature_has_s_count_parameter(self):
        import database as db_module

        sig = inspect.signature(db_module.save_autotune_run)
        assert "s_count" in sig.parameters, (
            "database.save_autotune_run must accept an 's_count' keyword parameter "
            "(AC-E1) — migration 023 added autotune_runs.s_count but the write path "
            "was never extended to populate it."
        )
        param = sig.parameters["s_count"]
        assert param.default is None, (
            f"s_count must default to None (AC-E1: 's_count: int | None = None'); "
            f"got default={param.default!r}"
        )

    def test_save_autotune_run_accepts_s_count_without_raising(self):
        import database as db_module

        # Must not raise TypeError for the new kwarg.
        row_id = _call_save_autotune_run(db_module, symphony_id="sc-accept-test", s_count=7)
        assert isinstance(row_id, int) and row_id > 0, (
            f"save_autotune_run must return a positive int row id; got {row_id!r}"
        )


class TestSaveAutotuneRunPersistsSCount:
    """AC-E1: the INSERT must write s_count into the existing column — not silently drop it."""

    def test_persisted_s_count_matches_supplied_value(self):
        import database as db_module

        _call_save_autotune_run(db_module, symphony_id="sc-persist-test", s_count=42)

        conn = db_module.get_connection()
        row = conn.execute(
            "SELECT s_count FROM autotune_runs WHERE symphony_id = 'sc-persist-test'"
        ).fetchone()
        conn.close()

        assert row is not None, "Expected one autotune_runs row for 'sc-persist-test'."
        assert row[0] == 42, (
            f"autotune_runs.s_count = {row[0]!r}, expected 42.\n"
            f"  AC-E1: the value passed to save_autotune_run(s_count=42) must be written "
            f"to the column, not silently dropped by an INSERT that omits it."
        )

    def test_s_count_zero_is_persisted_as_zero_not_null(self):
        """Zero is the honest NN1-compliant value (no BACKTEST_SELECTION evidence) and
        must be distinguishable from a legacy NULL row (AC-E2 downstream: the migration
        023 comment states 'DEFAULT NULL: S=0 in the NN1-honest case; NULL for legacy
        heuristic-path rows' — so an implementation that treats falsy 0 as 'omit the
        column' would corrupt this distinction)."""
        import database as db_module

        _call_save_autotune_run(db_module, symphony_id="sc-zero-test", s_count=0)

        conn = db_module.get_connection()
        row = conn.execute(
            "SELECT s_count FROM autotune_runs WHERE symphony_id = 'sc-zero-test'"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 0, (
            f"autotune_runs.s_count = {row[0]!r}, expected 0 (not NULL).\n"
            f"  An implementation using `if s_count:` (falsy check) instead of "
            f"`if s_count is not None:` would incorrectly write NULL for the honest "
            f"S=0 case, making it indistinguishable from a legacy pre-023 row."
        )

    def test_s_count_omitted_defaults_to_null(self):
        """Backward compatibility: callers that don't pass s_count must still get a
        NULL row (legacy behaviour preserved for callers that haven't been updated)."""
        import database as db_module

        db_module.save_autotune_run(
            run_timestamp="2026-07-12T12:00:00Z",
            symphony_id="sc-omitted-test",
            oos_alpha=0.5,
            train_alpha=1.0,
            baseline_decision="Adopted AI",
            fallback_oos_alpha=0.2,
            default_oos_alpha=0.1,
            # s_count intentionally omitted
        )

        conn = db_module.get_connection()
        row = conn.execute(
            "SELECT s_count FROM autotune_runs WHERE symphony_id = 'sc-omitted-test'"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] is None, (
            f"autotune_runs.s_count = {row[0]!r}; expected NULL when s_count is omitted "
            f"(backward-compat default per AC-E1 's_count: int | None = None')."
        )

    def test_append_only_no_update_path_introduced(self):
        """AC-E1: 'the table stays append-only — no UPDATE path introduced'. Two calls
        for the same symphony_id must produce TWO rows (an INSERT each time), never
        an UPDATE-in-place of the first row."""
        import database as db_module

        _call_save_autotune_run(db_module, symphony_id="sc-append-test", s_count=1)
        _call_save_autotune_run(db_module, symphony_id="sc-append-test", s_count=2)

        conn = db_module.get_connection()
        rows = conn.execute(
            "SELECT s_count FROM autotune_runs WHERE symphony_id = 'sc-append-test' ORDER BY id ASC"
        ).fetchall()
        conn.close()

        assert len(rows) == 2, (
            f"Expected 2 append-only rows for two save_autotune_run calls; got {len(rows)}. "
            f"s_count must never be written via an UPDATE to an existing row."
        )
        assert [r[0] for r in rows] == [1, 2], (
            f"Expected each row to carry its own call's s_count [1, 2]; got "
            f"{[r[0] for r in rows]!r} — a shared/UPDATEd row would show both as the same value."
        )


class TestSaveAutotuneRunSCountDoesNotAffectOtherColumns:
    """Guard: adding s_count must not perturb any existing column's value or the
    documented S3-AUDIT-001 return-id contract (regression pin)."""

    def test_existing_return_id_contract_unchanged(self):
        import database as db_module

        row_id = _call_save_autotune_run(db_module, symphony_id="sc-regression-test", s_count=5)

        conn = db_module.get_connection()
        row = conn.execute(
            "SELECT id, oos_alpha, train_alpha FROM autotune_runs WHERE id = ?", (row_id,)
        ).fetchone()
        conn.close()

        assert row is not None, "save_autotune_run must return the id of the row it just inserted."
        assert row[0] == row_id
        # pytest.approx: oos_alpha/train_alpha are floats round-tripped through SQLite —
        # exact equality is safe here (no arithmetic performed), but approx guards any
        # future float-storage-affinity surprise.
        assert row[1] == pytest.approx(0.5, abs=1e-9)
        assert row[2] == pytest.approx(1.0, abs=1e-9)
