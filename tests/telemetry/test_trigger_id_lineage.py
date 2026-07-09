"""RED — exit-trigger lineage: shadow_history.trigger_id must actually populate.

THE BUG (VERDICT-droplet.md Finding 10, LOW-MEDIUM, approved into this cycle):
  0 of 25,218 post-trigger shadow_history rows link to their exit_triggers row.
  The data phase already READS ``bot_state[s_id].get("_last_trigger_id")``
  (alpha_bot_execution.py:908) and passes it to record_shadow_observation — but
  nothing in the non-test tree ever SETS ``_last_trigger_id``, so the column can
  never populate. Any historical repair (e.g. the Finding-2 $-saved regeneration)
  must join by (symphony, day, time) instead of the designed key — and the
  Finding-1 duplicate-trigger day makes that join ambiguous.

CONTRACT PINNED:
  1. database.record_exit_trigger RETURNS the inserted row id — without it the
     trigger site cannot learn the key it must stash (today it returns None).
  2. alpha_bot_execution.py contains a real ASSIGNMENT to ``_last_trigger_id``
     (AST-verified, not grep) — closing the write side of the read that already
     exists. The audit's falsification ("exactly one READ, zero writers") is
     automated here so the gap can never silently reopen.
  3. DB round-trip anchor: a trigger_id written via record_shadow_observation is
     returned by load_latest_shadow_row — the repair join works once wired.

FIXTURE PROVENANCE: tests/fixtures/prod_droplet/ (captured-from-producer, SHA
0bcbd1a — see capture_from_droplet.py). Trigger rows used for seeding are the
real production rows.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import database

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "prod_droplet"
_ENGINE_SOURCE = Path(__file__).parent.parent.parent / "alpha_bot_execution.py"


@pytest.fixture(scope="module")
def exit_trigger_rows() -> list[dict]:
    return json.loads((_FIXTURE_DIR / "exit_triggers.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def shadow_snapshot_rows() -> list[dict]:
    return json.loads((_FIXTURE_DIR / "shadow_snapshot_rows.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. The enabler: record_exit_trigger must return the inserted row id.
# ---------------------------------------------------------------------------


class TestRecordExitTriggerReturnsRowId:
    def test_record_exit_trigger_returns_the_inserted_row_id(self, exit_trigger_rows):
        """The trigger site can only stash _last_trigger_id if the recorder hands
        the key back. Seed a real production trigger row and assert the return
        value is the integer id of the row just written.

        RED today: record_exit_trigger returns None.
        """
        row = exit_trigger_rows[0]
        returned = database.record_exit_trigger(
            symphony_id=row["symphony_id"],
            account_id=row["account_id"],
            triggered_reason=row["triggered_reason"],
            at_return=row["at_return"],
            cycle_id=row["cycle_id"],
            ts_utc=row["ts_utc"],
            ts_et=row["ts_et"],
        )

        assert isinstance(returned, int), (
            f"record_exit_trigger must return the inserted row id; got {returned!r} — "
            "without it the engine cannot link shadow_history rows to their trigger"
        )
        import sqlite3

        conn = sqlite3.connect(database._db_file(), timeout=10.0)
        stored_id = conn.execute(
            "SELECT id FROM exit_triggers WHERE symphony_id = ? AND ts_utc = ?",
            (row["symphony_id"], row["ts_utc"]),
        ).fetchone()[0]
        conn.close()
        assert returned == stored_id

    def test_consecutive_triggers_return_distinct_ids(self, exit_trigger_rows):
        """Two real triggers recorded back-to-back must yield two distinct ids —
        the lineage key must disambiguate the Finding-1 duplicate-day scenario,
        which is exactly where the (symphony, day) join breaks.
        """
        first, second = exit_trigger_rows[0], exit_trigger_rows[1]
        id_a = database.record_exit_trigger(
            symphony_id=first["symphony_id"],
            account_id=first["account_id"],
            triggered_reason=first["triggered_reason"],
            at_return=first["at_return"],
            cycle_id=first["cycle_id"],
            ts_utc=first["ts_utc"],
            ts_et=first["ts_et"],
        )
        id_b = database.record_exit_trigger(
            symphony_id=second["symphony_id"],
            account_id=second["account_id"],
            triggered_reason=second["triggered_reason"],
            at_return=second["at_return"],
            cycle_id=second["cycle_id"],
            ts_utc=second["ts_utc"],
            ts_et=second["ts_et"],
        )
        assert isinstance(id_a, int) and isinstance(id_b, int)
        assert id_a != id_b


# ---------------------------------------------------------------------------
# 2. The wiring: the engine must contain a real _last_trigger_id assignment.
# ---------------------------------------------------------------------------


def _last_trigger_id_store_targets(tree: ast.AST) -> list[ast.AST]:
    """AST nodes where '_last_trigger_id' is a subscript STORE target
    (e.g. bot_state[sym_id]["_last_trigger_id"] = ...)."""
    stores = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "_last_trigger_id"
            ):
                stores.append(node)
    return stores


class TestEngineWiresLastTriggerId:
    def test_engine_assigns_last_trigger_id_at_a_trigger_site(self):
        """alpha_bot_execution.py must ASSIGN _last_trigger_id somewhere — the
        write side of the read at the shadow-observation site. AST-verified so a
        comment or string cannot satisfy it.

        RED today: the audit's re-verified finding — exactly one read, zero
        writers — means shadow_history.trigger_id can never populate.
        """
        tree = ast.parse(_ENGINE_SOURCE.read_text(encoding="utf-8"))
        stores = _last_trigger_id_store_targets(tree)
        assert stores, (
            "alpha_bot_execution.py never assigns _last_trigger_id: the read at the "
            "record_shadow_observation call site can only ever see None, so 0 of "
            "25,218 post-trigger rows link to their exit_triggers row"
        )

    def test_engine_still_reads_last_trigger_id_into_shadow_writes(self):
        """Anchor (passes today): the read side exists — bot_state's
        _last_trigger_id feeds record_shadow_observation's trigger_id. Keeps the
        chain closed from both ends once the writer lands.
        """
        source = _ENGINE_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        reads = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "_last_trigger_id"
        ]
        assert reads, (
            "the engine no longer reads _last_trigger_id — the lineage contract "
            "changed; re-point this suite deliberately, do not delete it"
        )


# ---------------------------------------------------------------------------
# 3. DB round-trip anchor: the column works end-to-end once fed.
# ---------------------------------------------------------------------------


class TestTriggerIdRoundTripsThroughShadowHistory:
    def test_shadow_row_written_with_trigger_id_returns_it(
        self, exit_trigger_rows, shadow_snapshot_rows
    ):
        """Anchor (passes today): record_shadow_observation persists trigger_id and
        load_latest_shadow_row returns it — proving the repair join is purely
        blocked on the missing engine write, not on the storage path.
        """
        trig = exit_trigger_rows[0]
        returned = database.record_exit_trigger(
            symphony_id=trig["symphony_id"],
            account_id=trig["account_id"],
            triggered_reason=trig["triggered_reason"],
            at_return=trig["at_return"],
            cycle_id=trig["cycle_id"],
            ts_utc=trig["ts_utc"],
            ts_et=trig["ts_et"],
        )
        # Until contract 1 lands, derive the id from the table so this anchor
        # stays independent of record_exit_trigger's return value.
        if not isinstance(returned, int):
            import sqlite3

            conn = sqlite3.connect(database._db_file(), timeout=10.0)
            returned = conn.execute("SELECT MAX(id) FROM exit_triggers").fetchone()[0]
            conn.close()

        shadow = next(r for r in shadow_snapshot_rows if r["symphony_id"] == trig["symphony_id"])
        database.record_shadow_observation(
            symphony_id=shadow["symphony_id"],
            account_id=shadow["account_id"],
            cycle_id=shadow["cycle_id"],
            ts_utc=shadow["ts_utc"],
            ts_et=shadow["ts_et"],
            trading_day=shadow["trading_day"],
            current_return=shadow["current_return"],
            shadow_return=shadow["shadow_return"],
            is_post_trigger=1,
            trigger_id=returned,
            position_epoch=shadow["position_epoch"],
        )

        row = database.load_latest_shadow_row(shadow["symphony_id"], shadow["trading_day"])
        assert row is not None
        assert row["trigger_id"] == returned, (
            f"trigger_id {returned} did not round-trip through shadow_history "
            f"(got {row['trigger_id']!r})"
        )
