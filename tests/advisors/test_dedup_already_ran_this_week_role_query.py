"""RED tests — Workstream A / AC-A1: strategy_builder_scheduler dedup TypeError bug.

strategy_builder_scheduler._already_ran_this_week() (strategy_builder_scheduler.py:59-63)
calls:

    database.get_advisor_observations_for_symphony(
        symphony_id="",
        advisor_role="STRATEGY_BUILDER",
        limit=50,
    )

but the REAL signature (database.py:1156) is:

    def get_advisor_observations_for_symphony(symphony_id: str) -> list[dict]:

a single positional parameter — no advisor_role, no limit. Every call raises
TypeError("get_advisor_observations_for_symphony() got an unexpected keyword
argument 'advisor_role'"), which is silently caught by the OUTER
`except Exception as exc:` at line 89-94 and degrades to `return False`
(logged at DEBUG, invisible in production). The dedup guard therefore NEVER
fires -- a same-ISO-week duplicate run is never detected, no matter how many
STRATEGY_BUILDER rows already exist this week.

AC-A1: "Same-ISO-week dedup returns True when a STRATEGY_BUILDER row exists
this week; NO swallowed TypeError."

These tests call the REAL _already_ran_this_week() (no monkeypatching of the
function itself, unlike tests/advisors/test_builder_scheduler.py which patches
this exact seam to drive orchestration tests in isolation) against a real
per-test-isolated DB, so the TypeError swallow is genuinely exercised.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _insert_observation_with_created_at(db_module, *, advisor_role: str, created_at: str) -> int:
    """Insert an advisor_observations row with an EXPLICIT created_at timestamp.

    database.insert_advisor_observation() does not accept created_at (it is a
    DB-default column) -- direct SQL is required to backdate a row for the
    "different ISO week" test below. Mirrors the real column set from
    migrations/017_advisor_observations.sql.
    """
    conn = db_module.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO advisor_observations "
        "(advisor_role, subject_type, subject_id, verdict, raw_response, "
        " is_advisory_only, symphony_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        (advisor_role, "strategy", "sb-dedup-test", None, "{}", "", created_at),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


class TestAlreadyRanThisWeekRealQuery:
    """AC-A1: exercise the REAL (unmocked) _already_ran_this_week() implementation."""

    def test_returns_true_when_strategy_builder_row_exists_this_week(self):
        import database as db_module
        from advisors import strategy_builder_scheduler as sched

        now_iso = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        _insert_observation_with_created_at(
            db_module, advisor_role="STRATEGY_BUILDER", created_at=now_iso
        )

        result = sched._already_ran_this_week()

        assert result is True, (
            "_already_ran_this_week() must return True when a STRATEGY_BUILDER "
            "advisor_observations row exists from THIS ISO week.\n"
            "  AC-A1: the current implementation calls "
            "get_advisor_observations_for_symphony(symphony_id='', advisor_role=..., "
            "limit=...) which raises TypeError (the real signature takes only "
            "symphony_id) -- silently caught by the outer except, always returning "
            "False. Fix: call database.get_advisor_observations_for_role("
            "'STRATEGY_BUILDER', limit=...) instead."
        )

    def test_returns_false_when_no_strategy_builder_row_exists(self):
        """Regression: an empty (fresh-week) DB must still return False -- the fix
        must not flip the guard to 'always True'."""
        from advisors import strategy_builder_scheduler as sched

        result = sched._already_ran_this_week()

        assert result is False, (
            f"_already_ran_this_week() must return False with no STRATEGY_BUILDER rows "
            f"in the DB at all; got {result!r}."
        )

    def test_returns_false_when_strategy_builder_row_is_from_a_different_iso_week(self):
        """A STRATEGY_BUILDER row from 3 weeks ago must NOT count as 'already ran
        this week' -- the ISO-week boundary check must still discriminate correctly
        once the TypeError swallow is fixed."""
        import database as db_module
        from advisors import strategy_builder_scheduler as sched

        old_dt = datetime.now(tz=UTC) - timedelta(days=21)
        old_iso = old_dt.strftime("%Y-%m-%d %H:%M:%S")
        _insert_observation_with_created_at(
            db_module, advisor_role="STRATEGY_BUILDER", created_at=old_iso
        )

        result = sched._already_ran_this_week()

        assert result is False, (
            f"_already_ran_this_week() must return False for a STRATEGY_BUILDER row "
            f"dated 3 weeks ago (different ISO week); got {result!r}."
        )

    def test_does_not_false_positive_on_other_advisor_roles(self):
        """A same-week row for a DIFFERENT advisor_role (e.g. OVERFITTING_CONSCIENCE)
        must not trigger the STRATEGY_BUILDER dedup guard -- the role filter must be
        genuinely applied, not accidentally matching everything once fixed."""
        import database as db_module
        from advisors import strategy_builder_scheduler as sched

        now_iso = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        _insert_observation_with_created_at(
            db_module, advisor_role="OVERFITTING_CONSCIENCE", created_at=now_iso
        )

        result = sched._already_ran_this_week()

        assert result is False, (
            f"_already_ran_this_week() must return False when only a same-week "
            f"OVERFITTING_CONSCIENCE row exists (not STRATEGY_BUILDER); got {result!r}. "
            f"The role filter must be genuinely applied via get_advisor_observations_for_role."
        )

    def test_never_raises_typeerror(self):
        """Direct guard against regressing back to the swallowed-TypeError bug: call
        the real function and assert it returns a plain bool, never propagating an
        exception (even though the outer try/except already catches it -- this locks
        the intended call NOT raising the wrong-signature TypeError at all)."""
        from advisors import strategy_builder_scheduler as sched

        result = sched._already_ran_this_week()

        assert isinstance(result, bool), (
            f"_already_ran_this_week() must return a plain bool; got {type(result).__name__}."
        )
