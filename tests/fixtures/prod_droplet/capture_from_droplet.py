"""Capture real production data from a droplet DB copy into committed test fixtures.

PROVENANCE (feedback_verify_backend_contract_before_fixtures — captured-from-producer):
    Source host:  root@104.248.7.101 (the PlanetStopper production droplet)
    Deployed SHA: 0bcbd1a36ae849bd198b18e1ea42d0dde53a511f (== origin/main at capture)
    Captured:     2026-07-09, via READ-ONLY scp of /tmp/prodfix_dbcopy.db (a copy of
                  alphabot_state.db) and /opt/planetstopper/post_mortems/*.json.
                  The live daemon/DB were never written to.

Usage (re-capture):
    python tests/fixtures/prod_droplet/capture_from_droplet.py <db_copy_path> <pm_dir>

Outputs (all into this directory):
    post_mortems/               verbatim copies of the production post-mortem files
    exit_triggers.json          every exit_triggers row (87 at capture — includes the
                                2026-07-09 int64-crash duplicate rows ids 80-83)
    shadow_last_rows.json       last shadow_history row per (symphony_id, trading_day)
                                — the exact row-set the canonical portfolio series uses
    shadow_snapshot_rows.json   for each (symphony, day) with an exit trigger on the
                                money-math audit days (2026-06-23 / 2026-06-24): the
                                last 3 rows at/before the 15:54 ET Stage-1 snapshot —
                                the DB state visible when the post-mortem is generated
    bot_state_positions.json    per-symphony position snapshot from the bot_state blob
                                (name, account, current_value, current_return, trigger
                                fields) — real key shapes for state-persistence tests

Tests derive ALL expected values from these files at runtime; nothing here is a
synthetic or producer-run-hardcoded number.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

_OUT_DIR = Path(__file__).parent

# Trading days audited for the $-saved money-math defect (VERDICT-droplet.md
# Finding 2). Snapshot rows are captured for every triggered symphony on them.
_MONEY_MATH_AUDIT_DAYS = ("2026-06-23", "2026-06-24")

# Stage-1 post-mortem generation time (ET) — rows after this minute do not yet
# exist when the producer runs, so the captured snapshot excludes them.
_STAGE1_CUTOFF_ET_TIME = "15:54:59"

# bot_state fields captured per symphony — the real key shapes the engine writes.
_POSITION_FIELDS = (
    "name",
    "account",
    "current_value",
    "current_return",
    "triggered",
    "triggered_reason",
    "triggered_at_return",
    "triggered_at_stop",
    "triggered_at_hwm",
    "triggered_at_time",
    "high_water_mark",
    "shadow_hwm",
    "symphony_vol",
    "mc_prob",
    "vwap_ticks",
    "vwap_bleed_ticks",
    "above_tp_count",
    "below_stop_count",
    "position_epoch",
    "current_holdings",
)


def capture(db_copy: str, pm_dir: str) -> None:
    conn = sqlite3.connect(f"file:{db_copy}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # --- post_mortems: verbatim copies -----------------------------------
    out_pm = _OUT_DIR / "post_mortems"
    out_pm.mkdir(exist_ok=True)
    for src in sorted(Path(pm_dir).glob("post_mortem_*.json")):
        shutil.copy(src, out_pm / src.name)

    # --- exit_triggers: every row -----------------------------------------
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT id, ts_utc, ts_et, symphony_id, account_id, triggered_reason, "
            "       at_return, cycle_id "
            "FROM exit_triggers ORDER BY id"
        )
    ]
    (_OUT_DIR / "exit_triggers.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")

    # --- shadow_history: last row per (symphony_id, trading_day) ----------
    last_rows = [
        dict(r)
        for r in conn.execute(
            "SELECT trading_day, symphony_id, account_id, cycle_id, ts_utc, ts_et, "
            "       current_return, shadow_return, is_post_trigger, position_epoch "
            "FROM shadow_history "
            "WHERE ts_utc = (SELECT MAX(ts_utc) FROM shadow_history s2 "
            "                WHERE s2.symphony_id = shadow_history.symphony_id "
            "                  AND s2.trading_day = shadow_history.trading_day) "
            "ORDER BY trading_day, symphony_id"
        )
    ]
    (_OUT_DIR / "shadow_last_rows.json").write_text(
        json.dumps(last_rows, indent=1), encoding="utf-8"
    )

    # --- shadow_history: pre-Stage-1 snapshot rows for audited exits ------
    snapshot_rows: list[dict] = []
    for day in _MONEY_MATH_AUDIT_DAYS:
        triggered_ids = [
            r["symphony_id"]
            for r in conn.execute(
                "SELECT DISTINCT symphony_id FROM exit_triggers WHERE substr(ts_et, 1, 10) = ?",
                (day,),
            )
        ]
        for sid in triggered_ids:
            snapshot_rows.extend(
                dict(r)
                for r in conn.execute(
                    "SELECT trading_day, symphony_id, account_id, cycle_id, ts_utc, "
                    "       ts_et, current_return, shadow_return, is_post_trigger, "
                    "       position_epoch "
                    "FROM shadow_history "
                    "WHERE symphony_id = ? AND trading_day = ? "
                    "  AND substr(ts_et, 12, 8) <= ? "
                    "ORDER BY ts_utc DESC LIMIT 3",
                    (sid, day, _STAGE1_CUTOFF_ET_TIME),
                )
            )
    (_OUT_DIR / "shadow_snapshot_rows.json").write_text(
        json.dumps(snapshot_rows, indent=1), encoding="utf-8"
    )

    # --- bot_state: per-symphony position snapshot -------------------------
    blob = json.loads(conn.execute("SELECT data FROM bot_state WHERE id = 1").fetchone()[0])
    positions = {
        sym_id: {k: entry[k] for k in _POSITION_FIELDS if k in entry}
        for sym_id, entry in blob.items()
        if isinstance(entry, dict) and entry.get("name")
    }
    (_OUT_DIR / "bot_state_positions.json").write_text(
        json.dumps(positions, indent=1), encoding="utf-8"
    )

    conn.close()
    print(
        f"captured: {len(rows)} exit_triggers, {len(last_rows)} shadow last-rows, "
        f"{len(snapshot_rows)} snapshot rows, {len(positions)} positions, "
        f"{len(list(out_pm.glob('*.json')))} post-mortem files"
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: capture_from_droplet.py <db_copy_path> <pm_dir>")
    capture(sys.argv[1], sys.argv[2])
