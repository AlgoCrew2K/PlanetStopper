"""
RED tests — Cluster 6 AC-3: the shadow-trajectory query must be scoped to the
CURRENT position epoch, so symphony_id reuse cannot splice two positions'
return series.

Audit provenance:
  risk-math__2026-05-21.md HIGH "analytics shadow-trajectory query has no
  position-lifecycle boundary; symphony_id reuse corrupts dry_run CR/MDD".

THE FINDING. `_get_shadow_cumulative_trajectory` (analytics.py:489) selects
EVERY shadow_history row for a symphony_id with no position boundary. A
Composer symphony_id is long-lived; Planet Stopper opens, exits, and re-enters
positions under the same symphony_id across the 180-day retention window. The
query chain-links the prior position's daily shadow_return values into the new
position's series — get_symphony_cumulative_return and get_symphony_max_drawdown
then report a dry_run number that corresponds to no actual holding.

MECHANISM (team-lead D7 + risk-engine-specialist 2026-05-22, DETERMINISTIC —
not a heuristic):
  - Migration 015_shadow_history_position_epoch.sql adds a NULLable
    `position_epoch TEXT` column to shadow_history.
  - The engine stamps a fresh epoch per symphony at TWO sites:
      (i) first-ever entry creation (`symphony_id not in bot_state`), and
      (ii) inside wipe_transient_state, CONDITIONALLY — re-stamped ONLY when
           `s_data.get("triggered")` was True at wipe time (a triggered
           position rolled into a new session = a genuine new-position
           boundary). An untriggered still-open position is NOT re-stamped,
           so its epoch is STABLE across the per-day wipes it survives.
    The conditional is load-bearing: wipe_transient_state runs every trading
    day for every symphony, so an UNCONDITIONAL re-stamp would give a 5-day
    position 5 distinct epochs and the latest-epoch query would truncate the
    position's own trajectory to its last day.
  - record_shadow_observation gains a `position_epoch` kwarg, written per row.
  - `_get_shadow_cumulative_trajectory` self-selects the LATEST epoch for the
    symphony_id and filters the trajectory to it — the caller threads nothing.
  - The cache key (analytics.py:503) gains the resolved epoch.
  - NULL-epoch pre-migration rows form a single legacy segment; the query
    tolerates BOTH a missing position_epoch column (pre-migration DB) and a
    present-but-NULL value (post-migration legacy row).
  - Documented limitation (CASE 2): a re-entry following an UNTRIGGERED exit
    (Composer rebalances the symphony out, then back in) is NOT segmented —
    the engine records no untriggered-rotation event. Fixing it needs
    engine-execution position-rotation bookkeeping, tracked out-of-cluster.

The query-side tests seed shadow_history with pre-written epochs and assert
the trajectory reflects ONLY the current epoch. The stamping-stability tests
drive the ACTUAL stamp path (wipe_transient_state + record_shadow_observation)
and assert a multi-day position keeps ONE epoch — these catch the
fragmentation flaw a query-only suite cannot. Expected values are derived from
the chain-link formula on the fixtured per-day returns — no producer-captured
numbers.

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
        # CORRECTED: bot_CR = chain_link only (no if_held double-count).
        expected_dry_run = _chain_link_pct(_POSITION_B_RETURNS)
        spliced_dry_run = _chain_link_pct(
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


# ---------------------------------------------------------------------------
# AC-3 STAMPING STABILITY — the load-bearing adversarial gap.
#
# Every test above hand-writes position_epoch as already-distinct strings, so
# it tests the QUERY ("given two epochs, filter to the latest?") but NEVER the
# STAMP ("does one multi-day position get exactly ONE stable epoch?"). A broken
# UNCONDITIONAL-stamp implementation — re-stamping the epoch on every per-day
# wipe_transient_state call — passes every query-side test while silently
# fragmenting a 5-day position into 5 epochs, so the latest-epoch query then
# truncates the position's trajectory to its last day.
#
# These tests drive the ACTUAL stamp path (database.wipe_transient_state +
# database.record_shadow_observation) and assert epoch STABILITY. They are the
# tests that fail on the unconditional-stamp implementation.
# ---------------------------------------------------------------------------


# Post-migration shadow_history schema — record_shadow_observation must be able
# to write the position_epoch column, so the test DB carries it.
_SHADOW_SCHEMA_POST_MIGRATION = _SHADOW_SCHEMA_WITH_EPOCH


def _make_post_migration_shadow_db(tmp_path: Path) -> str:
    """An empty shadow_history table WITH the position_epoch column — the
    post-migration shape record_shadow_observation writes into."""
    db_file = str(tmp_path / "shadow_stamp_stability.db")
    conn = sqlite3.connect(db_file)
    conn.execute(_SHADOW_SCHEMA_POST_MIGRATION)
    conn.commit()
    conn.close()
    return db_file


def _open_position_bot_state(symphony_id: str) -> dict:
    """A minimal bot_state with one open, untriggered symphony position.

    Models the engine's fresh-entry path: the engine stamps a position_epoch at
    the `symphony_id not in bot_state` branch when a position first opens, so a
    just-opened entry already carries an epoch. The test stamps it the same way
    the engine does (database.mint_position_epoch) — a hand-built dict with NO
    epoch is not a state the engine ever produces.
    """
    return {
        symphony_id: {
            "high_water_mark": 0.0,
            "shadow_hwm": 0.0,
            "triggered": False,
            "armed": False,
            "current_return": 0.0,
            # The engine stamps the epoch at position open (fresh-entry branch).
            "position_epoch": _db.mint_position_epoch(),
        }
    }


def _record_day(symphony_id: str, bot_state: dict, day: str, ret: float) -> None:
    """Write one shadow_history row for `symphony_id`, sourcing position_epoch
    from bot_state exactly as the engine's record path does."""
    epoch = bot_state[symphony_id].get("position_epoch")
    _db.record_shadow_observation(
        symphony_id=symphony_id,
        account_id="acct-stamp-test",
        cycle_id=f"{day.replace('-', '')}_1500",
        ts_utc=f"{day}T19:00:00Z",
        ts_et=f"{day}T15:00:00",
        trading_day=day,
        current_return=ret,
        shadow_return=ret,
        is_post_trigger=int(bool(bot_state[symphony_id].get("triggered"))),
        trigger_id=None,
        position_epoch=epoch,
    )


class TestPositionEpochStampStability:
    """The epoch must be STABLE for one position's entire life and change ONLY
    at a post-trigger session-rollover boundary. A per-day-wipe unconditional
    re-stamp fragments a multi-day position — these tests catch that."""

    def test_five_day_untriggered_position_keeps_one_epoch(
        self, tmp_path, monkeypatch
    ):
        """ADVERSARIAL: a single 5-day untriggered position survives 4 new-day
        wipes (days 2-5). Every wipe must LEAVE the epoch unchanged — an
        untriggered open position is not a new-position boundary. All 5
        shadow_history rows must carry the SAME position_epoch.

        Under an unconditional-stamp implementation each wipe re-stamps, the 5
        rows get 5 distinct epochs, and this test fails."""
        db_file = _make_post_migration_shadow_db(tmp_path)
        monkeypatch.setattr(_db, "DB_FILE", db_file)

        symphony_id = "stamp-stability-sym"
        bot_state = _open_position_bot_state(symphony_id)

        days = ["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06"]
        for i, day in enumerate(days):
            if i > 0:
                # Day 2..5: the new-trading-day wipe runs. The position is
                # still open and untriggered — its epoch must NOT change.
                _db.wipe_transient_state(bot_state)
            # The engine writes a shadow row each day.
            _record_day(symphony_id, bot_state, day, ret=0.5)

        # All 5 rows must share ONE epoch.
        conn = sqlite3.connect(db_file)
        epochs = [
            row[0]
            for row in conn.execute(
                "SELECT position_epoch FROM shadow_history "
                "WHERE symphony_id = ? ORDER BY ts_utc ASC",
                (symphony_id,),
            ).fetchall()
        ]
        conn.close()
        assert len(epochs) == 5, "5 days recorded -> 5 rows expected"
        assert len(set(epochs)) == 1, (
            f"a single 5-day untriggered position must carry ONE stable "
            f"position_epoch across all rows; got {len(set(epochs))} distinct "
            f"epochs {set(epochs)}. An unconditional re-stamp in "
            f"wipe_transient_state fragments the position — the re-stamp must "
            f"be CONDITIONAL on s_data.get('triggered') being True at wipe time."
        )
        assert epochs[0] is not None, (
            "the epoch must be stamped (not None) on a position opened "
            "post-migration — the first-entry stamp site must fire"
        )

    def test_five_day_untriggered_position_trajectory_returns_all_days(
        self, tmp_path, monkeypatch
    ):
        """The fragmentation flaw's observable consequence: if the 5-day
        position fragments into 5 epochs, the latest-epoch query returns only
        day 5. The trajectory must return ALL 5 days."""
        db_file = _make_post_migration_shadow_db(tmp_path)
        monkeypatch.setattr(_db, "DB_FILE", db_file)

        symphony_id = "stamp-traj-sym"
        bot_state = _open_position_bot_state(symphony_id)
        returns = [0.5, -1.0, 0.8, 1.2, -0.3]
        days = ["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06"]
        for i, (day, ret) in enumerate(zip(days, returns)):
            if i > 0:
                _db.wipe_transient_state(bot_state)
            _record_day(symphony_id, bot_state, day, ret)

        _db._shadow_cr_cache.clear()
        trajectory = _get_shadow_cumulative_trajectory(symphony_id, db_file)
        assert trajectory is not None, (
            "a 5-day position must yield a trajectory (>= 2 rows)"
        )
        assert trajectory == pytest.approx(returns, rel=1e-9), (
            f"the trajectory must contain all 5 days of the single position "
            f"{returns}; got {trajectory}. If it returns only the last day, "
            f"the epoch fragmented across the per-day wipes."
        )

    def test_post_trigger_reentry_gets_a_new_epoch(self, tmp_path, monkeypatch):
        """The CONDITIONAL re-stamp: position A runs days 1-5 and is triggered
        on day 5; the day-6 wipe — running on a position whose triggered flag
        is True — IS a genuine new-position boundary and MUST re-stamp. Position
        B (days 6-10) must carry a DIFFERENT epoch from position A, and the
        trajectory must return only B."""
        db_file = _make_post_migration_shadow_db(tmp_path)
        monkeypatch.setattr(_db, "DB_FILE", db_file)

        symphony_id = "reentry-sym"
        bot_state = _open_position_bot_state(symphony_id)

        # Position A — days 1-5, untriggered until day 5.
        a_days = ["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06"]
        for i, day in enumerate(a_days):
            if i > 0:
                _db.wipe_transient_state(bot_state)
            _record_day(symphony_id, bot_state, day, ret=0.4)
        # On day 5 a trigger fires — mark the position triggered.
        bot_state[symphony_id]["triggered"] = True

        # Day 6: the new-day wipe runs on a TRIGGERED position -> new-position
        # boundary -> the epoch MUST be re-stamped here.
        _db.wipe_transient_state(bot_state)

        # Position B — days 6-10, the re-entered position.
        b_days = ["2026-02-09", "2026-02-10", "2026-02-11", "2026-02-12", "2026-02-13"]
        b_returns = [-0.5, 0.8, -1.2, 2.0, -0.3]
        for i, (day, ret) in enumerate(zip(b_days, b_returns)):
            if i > 0:
                _db.wipe_transient_state(bot_state)
            _record_day(symphony_id, bot_state, day, ret)

        conn = sqlite3.connect(db_file)
        rows = conn.execute(
            "SELECT trading_day, position_epoch FROM shadow_history "
            "WHERE symphony_id = ? ORDER BY ts_utc ASC",
            (symphony_id,),
        ).fetchall()
        conn.close()
        a_epochs = {ep for day, ep in rows if day in a_days}
        b_epochs = {ep for day, ep in rows if day in b_days}
        assert len(a_epochs) == 1 and len(b_epochs) == 1, (
            f"each of the two positions must have ONE stable epoch; "
            f"A={a_epochs}, B={b_epochs}"
        )
        assert a_epochs != b_epochs, (
            f"position B (post-trigger re-entry) must carry a DIFFERENT epoch "
            f"from position A — the day-6 wipe on a triggered position is a "
            f"new-position boundary that MUST re-stamp. A={a_epochs}, "
            f"B={b_epochs}"
        )

        _db._shadow_cr_cache.clear()
        trajectory = _get_shadow_cumulative_trajectory(symphony_id, db_file)
        assert trajectory == pytest.approx(b_returns, rel=1e-9), (
            f"after a post-trigger re-entry the trajectory must reflect ONLY "
            f"position B {b_returns}; got {trajectory}. A spliced query would "
            f"return all 10 days."
        )

    def test_wipe_does_not_restamp_untriggered_but_does_restamp_triggered(
        self, tmp_path, monkeypatch
    ):
        """Direct controlled contract test of the CONDITIONAL re-stamp. Both
        arms start from the SAME epoch and the SAME wipe call, differing ONLY
        in the `triggered` flag — so the test isolates the conditional to its
        one variable:
          - Arm A (triggered=False): wipe leaves position_epoch unchanged.
          - Arm B (triggered=True):  wipe assigns a fresh position_epoch.
        An unconditional re-stamp fails Arm A; a never-re-stamp fails Arm B.
        The pair pins the conditional precisely."""
        db_file = _make_post_migration_shadow_db(tmp_path)
        monkeypatch.setattr(_db, "DB_FILE", db_file)

        # The SAME starting epoch for both arms — the controlled constant.
        starting_epoch = _db.mint_position_epoch()

        # Arm A — triggered=False: a wipe on an untriggered still-open position
        # must NOT re-stamp the epoch.
        arm_a = {"sym-A": {"triggered": False, "position_epoch": starting_epoch}}
        _db.wipe_transient_state(arm_a)
        assert arm_a["sym-A"].get("position_epoch") == starting_epoch, (
            "a wipe on an UNTRIGGERED still-open position must NOT re-stamp the "
            f"epoch; it changed from {starting_epoch!r} to "
            f"{arm_a['sym-A'].get('position_epoch')!r}. An unconditional "
            f"re-stamp would fragment a multi-day position."
        )

        # Arm B — triggered=True: the ONLY difference from Arm A. A wipe on a
        # triggered position is a new-position boundary and MUST re-stamp.
        arm_b = {"sym-B": {"triggered": True, "position_epoch": starting_epoch}}
        _db.wipe_transient_state(arm_b)
        new_epoch = arm_b["sym-B"].get("position_epoch")
        assert new_epoch is not None and new_epoch != starting_epoch, (
            "a wipe on a TRIGGERED position is a new-position boundary and "
            f"MUST re-stamp the epoch; it stayed {starting_epoch!r}. A "
            f"never-re-stamp implementation would splice the re-entered "
            f"position onto the triggered one."
        )
