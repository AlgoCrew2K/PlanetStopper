"""
RED tests — Cluster 6 AC-3: the shadow-trajectory query must be scoped to the
CURRENT position epoch, so symphony_id reuse cannot splice two positions'
return series.

Audit provenance:
  risk-math__2026-05-21.md HIGH "analytics shadow-trajectory query has no
  position-lifecycle boundary; symphony_id reuse corrupts dry_run CR/MDD".

THE FINDING. `_get_shadow_cumulative_trajectory` (analytics.py:489) selects
EVERY shadow_history row for a symphony_id with no position boundary. A
Composer symphony_id is long-lived; AlphaBot opens, exits, and re-enters
positions under the same symphony_id across the 180-day retention window. The
query chain-links the prior position's daily shadow_return values into the new
position's series — get_symphony_cumulative_return and get_symphony_max_drawdown
then report a dry_run number that corresponds to no actual holding.

MECHANISM (team-lead D7 + risk-engine-specialist 2026-05-22, DETERMINISTIC —
not a heuristic):
  - Migration 015_shadow_history_position_epoch.sql adds a NULLable
    `position_epoch TEXT` column to shadow_history.
  - The engine stamps a fresh epoch per symphony at the wipe_transient_state
    position-lifecycle boundary (the AC-E2.5 new-position reset) and at the
    `symphony_id not in bot_state` fresh-entry branches.
  - record_shadow_observation gains a `position_epoch` kwarg, written per row.
  - `_get_shadow_cumulative_trajectory` self-selects the LATEST epoch for the
    symphony_id and filters the trajectory to it — the caller threads nothing.
  - The cache key (analytics.py:503) gains the resolved epoch.
  - NULL-epoch pre-migration rows form a single legacy segment.
  - Documented limitation: epoch granularity is the session; an intraday
    same-symphony re-entry shares one epoch (the engine does not record
    intraday position rotation — pre-existing, out of scope, documented).

These tests seed shadow_history exactly as the engine would write it (two
epochs under one symphony_id) and assert the trajectory reflects ONLY the
current epoch. Expected values are derived from the chain-link formula on the
fixtured per-day returns — no producer-captured numbers.

Tolerance: pytest.approx with rel=1e-9 for chain-link products of small
fixtured returns — these are exact arithmetic; the only error is float
representation.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import database as _db
from analytics import (
    _get_shadow_cumulative_trajectory,
    get_symphony_cumulative_return,
    get_symphony_max_drawdown,
)


# --- Fixtured per-day shadow_return series for two distinct position epochs ---
# Position A: a 5-day run. Position B: a later, separate 5-day run, same
# symphony_id. The two series are deliberately different so a spliced
# (epoch-blind) trajectory is observably wrong.
_EPOCH_A = "2026-01-05T14:30:00Z"   # earlier position-open timestamp
_EPOCH_B = "2026-03-02T14:30:00Z"   # later position-open timestamp (the CURRENT one)

_POSITION_A_RETURNS = [1.0, -2.0, 0.5, 1.5, -1.0]   # pct per day
_POSITION_B_RETURNS = [-0.5, 0.8, -1.2, 2.0, -0.3]  # pct per day, current epoch

_SYM_ID = "test-symphony-reused-id"


def _chain_link_pct(returns: list[float]) -> float:
    """From-zero chain-link of per-day pct returns -> total pct.
    product(1 + r/100) - 1, expressed in percent. This mirrors the analytics
    dry_run formula; expecteds are derived here, not captured from the producer.
    """
    product = 1.0
    for r in returns:
        product *= 1.0 + r / 100.0
    return (product - 1.0) * 100.0


def _peak_to_trough_mdd(returns: list[float]) -> float:
    """Peak-to-trough max drawdown of the cumulative series built from
    per-day returns — positive-magnitude convention, matching
    get_symphony_max_drawdown's dry_run computation."""
    cum: list[float] = []
    product = 1.0
    for r in returns:
        product *= 1.0 + r / 100.0
        cum.append((product - 1.0) * 100.0)
    peak = cum[0]
    max_dd = 0.0
    for val in cum:
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd
    return max_dd


# ---------------------------------------------------------------------------
# Shadow DB builders — write rows the way the engine does, WITH a
# position_epoch column. These RED tests require the column to exist; until the
# migration + write-path land, the schema/insert here drives the RED.
# ---------------------------------------------------------------------------

_SHADOW_SCHEMA_WITH_EPOCH = """
    CREATE TABLE shadow_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        ts_et TEXT,
        trading_day TEXT NOT NULL,
        symphony_id TEXT NOT NULL,
        account_id TEXT,
        cycle_id TEXT,
        current_return REAL NOT NULL,
        shadow_return REAL NOT NULL,
        is_post_trigger INTEGER NOT NULL DEFAULT 0,
        trigger_id INTEGER,
        position_epoch TEXT
    )
"""

_SHADOW_SCHEMA_LEGACY_NO_EPOCH = """
    CREATE TABLE shadow_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        ts_et TEXT,
        trading_day TEXT NOT NULL,
        symphony_id TEXT NOT NULL,
        account_id TEXT,
        cycle_id TEXT,
        current_return REAL NOT NULL,
        shadow_return REAL NOT NULL,
        is_post_trigger INTEGER NOT NULL DEFAULT 0,
        trigger_id INTEGER
    )
"""


def _seed_two_epoch_shadow_db(tmp_path: Path) -> str:
    """Seed one symphony_id with TWO position epochs: position A (days 1-5,
    epoch_A) then position B (days 20-25, epoch_B) — exactly the audit's
    reuse scenario. Returns the db file path."""
    db_file = str(tmp_path / "shadow_two_epoch.db")
    conn = sqlite3.connect(db_file)
    conn.execute(_SHADOW_SCHEMA_WITH_EPOCH)
    # Position A — earlier epoch, trading days 2026-01-05..09
    for i, ret in enumerate(_POSITION_A_RETURNS):
        day = f"2026-01-{5 + i:02d}"
        conn.execute(
            "INSERT INTO shadow_history (ts_utc, trading_day, symphony_id, "
            "current_return, shadow_return, position_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"{day}T19:00:00Z", day, _SYM_ID, ret, ret, _EPOCH_A),
        )
    # Position B — later epoch, trading days 2026-03-02..06 (the CURRENT position)
    for i, ret in enumerate(_POSITION_B_RETURNS):
        day = f"2026-03-{2 + i:02d}"
        conn.execute(
            "INSERT INTO shadow_history (ts_utc, trading_day, symphony_id, "
            "current_return, shadow_return, position_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"{day}T19:00:00Z", day, _SYM_ID, ret, ret, _EPOCH_B),
        )
    conn.commit()
    conn.close()
    return db_file


def _seed_legacy_no_epoch_shadow_db(tmp_path: Path) -> str:
    """Seed a single symphony_id with rows that have NO position_epoch column
    at all (pre-migration shape) — for the legacy-segment contract test."""
    db_file = str(tmp_path / "shadow_legacy.db")
    conn = sqlite3.connect(db_file)
    conn.execute(_SHADOW_SCHEMA_LEGACY_NO_EPOCH)
    for i, ret in enumerate(_POSITION_B_RETURNS):
        day = f"2026-03-{2 + i:02d}"
        conn.execute(
            "INSERT INTO shadow_history (ts_utc, trading_day, symphony_id, "
            "current_return, shadow_return) VALUES (?, ?, ?, ?, ?)",
            (f"{day}T19:00:00Z", day, _SYM_ID, ret, ret),
        )
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture(autouse=True)
def clear_shadow_cr_cache():
    """The trajectory function caches in database._shadow_cr_cache (a module
    global). Clear it before and after every test so cache state never bleeds
    across the AC-3 cases."""
    _db._shadow_cr_cache.clear()
    yield
    _db._shadow_cr_cache.clear()


# ---------------------------------------------------------------------------
# AC-3 core — the trajectory must reflect ONLY the current (latest) epoch.
# ---------------------------------------------------------------------------


class TestTrajectoryScopedToCurrentEpoch:
    """_get_shadow_cumulative_trajectory must return only the current position
    epoch's rows — never a splice of two positions under one symphony_id."""

    def test_trajectory_returns_only_current_epoch_rows(self, tmp_path):
        """With two epochs seeded, the trajectory must equal position B's
        returns (the latest epoch), NOT all 10 rows spliced together."""
        db_file = _seed_two_epoch_shadow_db(tmp_path)
        trajectory = _get_shadow_cumulative_trajectory(_SYM_ID, db_file)
        assert trajectory is not None, (
            "two-epoch DB has 5 current-epoch rows (>= 2) — trajectory must "
            "not be None"
        )
        assert trajectory == pytest.approx(_POSITION_B_RETURNS, rel=1e-9), (
            f"trajectory must contain ONLY the current epoch's ({_EPOCH_B}) "
            f"per-day returns {_POSITION_B_RETURNS}; got {trajectory}. "
            f"A spliced epoch-blind query would return all 10 rows "
            f"(positions A+B), which corresponds to no real holding."
        )

    def test_trajectory_length_is_current_epoch_only(self, tmp_path):
        """Property: the trajectory length equals the current epoch's row
        count (5), not the total row count (10)."""
        db_file = _seed_two_epoch_shadow_db(tmp_path)
        trajectory = _get_shadow_cumulative_trajectory(_SYM_ID, db_file)
        assert trajectory is not None
        assert len(trajectory) == len(_POSITION_B_RETURNS), (
            f"trajectory length must be {len(_POSITION_B_RETURNS)} (current "
            f"epoch only), not {len(_POSITION_A_RETURNS) + len(_POSITION_B_RETURNS)} "
            f"(spliced A+B); got {len(trajectory)}"
        )

    def test_trajectory_excludes_prior_epoch_returns(self, tmp_path):
        """Anti-splice: no value unique to position A may appear in the
        trajectory. Position A has -2.0 on day 2; position B never does."""
        db_file = _seed_two_epoch_shadow_db(tmp_path)
        trajectory = _get_shadow_cumulative_trajectory(_SYM_ID, db_file)
        assert trajectory is not None
        # -2.0 is a position-A-only return; its presence proves a splice.
        prior_only_return = -2.0
        assert prior_only_return not in _POSITION_B_RETURNS, "fixture integrity"
        assert prior_only_return not in trajectory, (
            "a position-A-only return leaked into the current-epoch trajectory "
            "— the query is splicing across the position boundary"
        )


# ---------------------------------------------------------------------------
# AC-3 consumers — dry_run CR and MDD must reflect only the current epoch.
# ---------------------------------------------------------------------------


class TestDryRunConsumersUseCurrentEpochOnly:
    """get_symphony_cumulative_return / get_symphony_max_drawdown dry_run must
    be computed from the current epoch's trajectory only."""

    def test_dry_run_cr_reflects_current_epoch_only(self, tmp_path):
        """dry_run CR = if_held + chain_link(current epoch). A spliced
        trajectory would chain-link 10 days and produce a different number."""
        db_file = _seed_two_epoch_shadow_db(tmp_path)
        sym_dict = {
            "id": _SYM_ID,
            "simple_return": 0.10,   # if_held baseline 10%
            "net_deposits": 1000.0,
            "time_weighted_return": 0.10,
        }
        if_held = 0.10 * 100.0  # 10.0 pct
        expected_dry_run = if_held + _chain_link_pct(_POSITION_B_RETURNS)
        spliced_dry_run = if_held + _chain_link_pct(
            _POSITION_A_RETURNS + _POSITION_B_RETURNS
        )
        # The two must differ, else the test cannot discriminate.
        assert abs(expected_dry_run - spliced_dry_run) > 1e-6, "fixture integrity"

        result = get_symphony_cumulative_return(
            sym_dict, bot_state_entry=None, db_path=db_file
        )
        assert result["dry_run"] == pytest.approx(expected_dry_run, rel=1e-9), (
            f"dry_run CR must chain-link ONLY the current epoch "
            f"(expected {expected_dry_run:.6f}); a spliced A+B trajectory would "
            f"yield {spliced_dry_run:.6f}; got {result['dry_run']}"
        )

    def test_dry_run_mdd_reflects_current_epoch_only(self, tmp_path):
        """dry_run MDD = peak-to-trough of the current epoch's cumulative
        series. A spliced series invents a discontinuity at the A->B join
        that is not a real intra-position drawdown."""
        db_file = _seed_two_epoch_shadow_db(tmp_path)
        sym_dict = {
            "id": _SYM_ID,
            "max_drawdown": 0.05,  # Composer if_held 5%
        }
        expected_mdd = _peak_to_trough_mdd(_POSITION_B_RETURNS)
        spliced_mdd = _peak_to_trough_mdd(
            _POSITION_A_RETURNS + _POSITION_B_RETURNS
        )
        assert abs(expected_mdd - spliced_mdd) > 1e-6, "fixture integrity"

        result = get_symphony_max_drawdown(
            sym_dict, bot_state_entry=None, db_path=db_file
        )
        assert result["dry_run"] == pytest.approx(expected_mdd, rel=1e-9), (
            f"dry_run MDD must be peak-to-trough of the CURRENT epoch only "
            f"(expected {expected_mdd:.6f}); a spliced A+B series would yield "
            f"{spliced_mdd:.6f}; got {result['dry_run']}"
        )


# ---------------------------------------------------------------------------
# AC-3 cache — the cache key must include the epoch.
# ---------------------------------------------------------------------------


class TestTrajectoryCacheKeyIncludesEpoch:
    """The analytics.py:503 cache key must include the resolved position epoch
    — else a trajectory cached for an earlier epoch is served for a later
    one (risk-engine-specialist's explicit watch-item)."""

    def test_cache_does_not_serve_stale_prior_epoch_trajectory(self, tmp_path):
        """Seed only position A, query (populates cache). Then ADD position B
        rows and query again. The second result must reflect epoch B, not the
        cached epoch-A trajectory."""
        db_file = str(tmp_path / "shadow_cache_epoch.db")
        conn = sqlite3.connect(db_file)
        conn.execute(_SHADOW_SCHEMA_WITH_EPOCH)
        for i, ret in enumerate(_POSITION_A_RETURNS):
            day = f"2026-01-{5 + i:02d}"
            conn.execute(
                "INSERT INTO shadow_history (ts_utc, trading_day, symphony_id, "
                "current_return, shadow_return, position_epoch) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"{day}T19:00:00Z", day, _SYM_ID, ret, ret, _EPOCH_A),
            )
        conn.commit()

        first = _get_shadow_cumulative_trajectory(_SYM_ID, db_file)
        assert first == pytest.approx(_POSITION_A_RETURNS, rel=1e-9), (
            "first query (only epoch A present) must return epoch A's rows"
        )

        # A new position B opens — append its rows under a NEW epoch.
        for i, ret in enumerate(_POSITION_B_RETURNS):
            day = f"2026-03-{2 + i:02d}"
            conn.execute(
                "INSERT INTO shadow_history (ts_utc, trading_day, symphony_id, "
                "current_return, shadow_return, position_epoch) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"{day}T19:00:00Z", day, _SYM_ID, ret, ret, _EPOCH_B),
            )
        conn.commit()
        conn.close()

        second = _get_shadow_cumulative_trajectory(_SYM_ID, db_file)
        assert second == pytest.approx(_POSITION_B_RETURNS, rel=1e-9), (
            "after position B opens, the trajectory must reflect epoch B — a "
            "cache keyed without the epoch would serve the stale epoch-A "
            "trajectory. The cache key must include the resolved position epoch."
        )


# ---------------------------------------------------------------------------
# AC-3 migration — 015 must apply cleanly, additive and NULLable.
# ---------------------------------------------------------------------------


class TestPositionEpochMigration:
    """Migration 015_shadow_history_position_epoch.sql must add a NULLable
    position_epoch column without a destructive step."""

    def test_migration_015_file_exists(self):
        migrations_dir = Path(__file__).parent.parent.parent / "migrations"
        mig = migrations_dir / "015_shadow_history_position_epoch.sql"
        assert mig.exists(), (
            "migration 015_shadow_history_position_epoch.sql must exist — it "
            "adds the position_epoch column to shadow_history"
        )

    def test_migration_015_registered_in_migration_files(self):
        """The migration must be registered in database._MIGRATION_FILES or it
        will never run."""
        assert "015_shadow_history_position_epoch.sql" in _db._MIGRATION_FILES, (
            "015_shadow_history_position_epoch.sql must be appended to "
            "database._MIGRATION_FILES"
        )

    def test_migration_015_adds_nullable_position_epoch_column(self, tmp_path):
        """Apply 008 (creates shadow_history) then 015; the position_epoch
        column must exist and be NULLable (pre-existing rows -> NULL)."""
        migrations_dir = Path(__file__).parent.parent.parent / "migrations"
        db_file = str(tmp_path / "migration_test.db")
        conn = sqlite3.connect(db_file)
        # Base table from 008.
        sql_008 = (migrations_dir / "008_shadow_history.sql").read_text(
            encoding="utf-8"
        )
        conn.executescript(sql_008)
        # Insert a legacy row BEFORE the migration.
        conn.execute(
            "INSERT INTO shadow_history (ts_utc, ts_et, trading_day, "
            "symphony_id, current_return, shadow_return) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("2026-01-01T19:00:00Z", "2026-01-01T15:00:00", "2026-01-01",
             _SYM_ID, 1.0, 1.0),
        )
        conn.commit()

        # Apply migration 015.
        mig = migrations_dir / "015_shadow_history_position_epoch.sql"
        assert mig.exists(), "015 migration file must exist for this test"
        conn.executescript(mig.read_text(encoding="utf-8"))
        conn.commit()

        # The column must now exist.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(shadow_history)")}
        assert "position_epoch" in cols, (
            "migration 015 must add a 'position_epoch' column to shadow_history"
        )

        # The pre-existing legacy row must have a NULL epoch (additive, NULLable,
        # no destructive backfill).
        legacy_epoch = conn.execute(
            "SELECT position_epoch FROM shadow_history WHERE symphony_id = ?",
            (_SYM_ID,),
        ).fetchone()[0]
        conn.close()
        assert legacy_epoch is None, (
            "a pre-migration row must have position_epoch = NULL after 015 — "
            "the migration is additive/NULLable with no destructive backfill"
        )


# ---------------------------------------------------------------------------
# AC-3 legacy contract — all-NULL-epoch rows form ONE legacy segment.
# ---------------------------------------------------------------------------


class TestNullEpochLegacySegment:
    """Pre-migration rows have position_epoch = NULL. A symphony whose rows are
    all NULL-epoch must be treated as a single legacy segment — the trajectory
    returns all the NULL-epoch rows (no splice, because there is only one
    legacy position recorded)."""

    def test_all_null_epoch_rows_form_one_segment(self, tmp_path):
        """Legacy DB shape (no position_epoch column at all): the trajectory
        must still return all rows as one segment, not crash."""
        db_file = _seed_legacy_no_epoch_shadow_db(tmp_path)
        trajectory = _get_shadow_cumulative_trajectory(_SYM_ID, db_file)
        assert trajectory is not None, (
            "a legacy (no-epoch) shadow_history must still yield a trajectory "
            "— the query must tolerate the absent/NULL epoch as one legacy "
            "segment, not raise"
        )
        assert trajectory == pytest.approx(_POSITION_B_RETURNS, rel=1e-9), (
            "all legacy NULL-epoch rows form one segment — the trajectory "
            "returns all of them"
        )
