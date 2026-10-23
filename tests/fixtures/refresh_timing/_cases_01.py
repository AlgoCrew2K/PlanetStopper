"""Capture cases for the server-state-truth plan.

PROVENANCE (captured-from-producer): each case reads the production state database
through `capture.open_readonly` and writes verbatim rows. Every case runs its
queries inside one read transaction, so one case sees one database state.

Cases (output under `--out`, mirroring the folders the records are committed to):
    01-c1-bot-state-row   01-c1/nested_bot_state_row.json.gz
                          `SELECT data FROM bot_state WHERE id = 1`, verbatim bytes, gzipped.
    01-c2-shadow-rows     01-c2/shadow_rows_two_cycles.json
                          one triggered and one untriggered symphony: each one's last
                          `shadow_history` row before the capture day, and every row of
                          the two newest engine cycles of the capture day.
    01-c2-exit-trigger    01-c2/exit_trigger_row.json
                          the newest `exit_triggers` row of the triggered symphony.

Run 01-c2-shadow-rows before 01-c2-exit-trigger with the same `--out`: the exit-trigger
case then reads the symphony the shadow capture chose instead of choosing again, so both
records describe one symphony even though the live database moves between the runs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

BOT_STATE_ROW_ID = 1
SNAPSHOT_KEY = "last_market_close_snapshot"
SHADOW_CYCLES_WANTED = 2
C1_DIR = "01-c1"
C2_DIR = "01-c2"
BOT_STATE_RECORD = "nested_bot_state_row.json.gz"
SHADOW_RECORD = "shadow_rows_two_cycles.json"
EXIT_RECORD = "exit_trigger_row.json"

TIER_POST_TRIGGER = (
    "newest exit_triggers row among symphonies with is_post_trigger rows in the two cycles"
)
TIER_NEWEST_EXIT = "newest exit_triggers row among symphonies present in both cycles"


def register(h):
    return {
        "01-c1-bot-state-row": h.Case(
            help="the production bot_state row (id = 1), verbatim, gzipped",
            run=lambda args: _run_bot_state_row(h, args),
        ),
        "01-c2-shadow-rows": h.Case(
            help="shadow_history rows of two engine cycles for one triggered and one untriggered symphony",
            run=lambda args: _run_shadow_rows(h, args),
            add_arguments=_add_selection_arguments,
        ),
        "01-c2-exit-trigger": h.Case(
            help="the newest exit_triggers row of the triggered symphony",
            run=lambda args: _run_exit_trigger(h, args),
            add_arguments=_add_selection_arguments,
        ),
    }


def _add_selection_arguments(parser):
    parser.add_argument(
        "--day", help="capture day YYYY-MM-DD (default: newest trading_day in shadow_history)"
    )
    parser.add_argument("--triggered-id", help="symphony id to use as the triggered symphony")
    parser.add_argument("--untriggered-id", help="symphony id to use as the untriggered symphony")


# ---------------------------------------------------------------------------
# 01-c1-bot-state-row
# ---------------------------------------------------------------------------


def _walk_stats(parsed, key):
    """Iterative walk (the stored chain nests deeply): occurrences of `key` and max depth."""
    occurrences = 0
    max_depth = 0
    stack = [(parsed, 1)]
    while stack:
        node, depth = stack.pop()
        max_depth = max(max_depth, depth)
        if isinstance(node, dict):
            if key in node:
                occurrences += 1
            children = node.values()
        elif isinstance(node, list):
            children = node
        else:
            continue
        stack.extend((child, depth + 1) for child in children if isinstance(child, (dict, list)))
    return occurrences, max_depth


def describe_state_bytes(raw: bytes) -> dict:
    """Facts about the stored text that the record's consumers check before using it."""
    facts: dict = {"raw_bytes": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest()}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        facts["utf8_decodable"] = False
        return facts
    facts["utf8_decodable"] = True
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError) as exc:
        facts["parse_error"] = type(exc).__name__
        return facts
    try:
        facts["json_round_trip_identical"] = json.dumps(parsed) == text
    except RecursionError:
        facts["json_round_trip_identical"] = None
        facts["round_trip_error"] = "RecursionError"
    facts["top_level_type"] = type(parsed).__name__
    if isinstance(parsed, dict):
        symphony_like = sorted(k for k, v in parsed.items() if isinstance(v, dict) and "name" in v)
        facts["top_level_key_count"] = len(parsed)
        facts["symphony_entry_count"] = len(symphony_like)
        facts["non_symphony_top_level_keys"] = sorted(k for k in parsed if k not in symphony_like)
        snapshot = parsed.get(SNAPSHOT_KEY)
        if isinstance(snapshot, dict):
            facts["top_level_snapshot_keys"] = sorted(snapshot)
    occurrences, max_depth = _walk_stats(parsed, SNAPSHOT_KEY)
    facts[f"{SNAPSHOT_KEY}_occurrences"] = occurrences
    facts["max_json_depth"] = max_depth
    return facts


def _run_bot_state_row(h, args):
    conn = h.open_readonly(args.db)
    try:
        with h.read_snapshot(conn):
            row = conn.execute(
                "SELECT typeof(data) AS storage_class, CAST(data AS BLOB) AS raw "
                "FROM bot_state WHERE id = ?",
                (BOT_STATE_ROW_ID,),
            ).fetchone()
    finally:
        conn.close()
    if row is None or row["raw"] is None:
        raise h.CaptureError(f"bot_state has no data for id = {BOT_STATE_ROW_ID}")
    raw = bytes(row["raw"])

    out = Path(args.out) / C1_DIR
    record = h.write_gzip(out / BOT_STATE_RECORD, raw, overwrite=args.force)
    facts = describe_state_bytes(raw)
    facts["storage_class"] = row["storage_class"]
    facts["gzip_bytes"] = record.stat().st_size
    h.write_sidecar(
        record,
        case="01-c1-bot-state-row",
        db_path=args.db,
        source_table="bot_state",
        row_key=f"id = {BOT_STATE_ROW_ID}",
        facts=facts,
        overwrite=args.force,
    )
    print(record)


# ---------------------------------------------------------------------------
# Symphony and cycle selection (shared by the two 01-c2 cases)
# ---------------------------------------------------------------------------


def select_shadow_scope(h, conn, args) -> dict:
    """Choose the capture day, its two newest cycles and the two symphonies.

    The triggered symphony is the newest `exit_triggers` row's symphony among those
    present in both cycles, preferring one that also has `is_post_trigger` rows in
    them. The untriggered symphony is the lowest id present in both cycles with no
    `is_post_trigger` row that day and no exit that ET day.
    """
    day = args.day
    if not day:
        day = conn.execute("SELECT MAX(trading_day) FROM shadow_history").fetchone()[0]
    if not day:
        raise h.CaptureError("shadow_history has no rows")

    cycle_rows = conn.execute(
        "SELECT cycle_id, MIN(ts_utc) AS first_ts_utc, MAX(ts_utc) AS last_ts_utc, "
        "COUNT(*) AS rows_all_symphonies, COUNT(DISTINCT symphony_id) AS symphonies_all "
        "FROM shadow_history WHERE trading_day = ? AND cycle_id IS NOT NULL "
        "GROUP BY cycle_id ORDER BY MAX(ts_utc) DESC, cycle_id DESC LIMIT ?",
        (day, SHADOW_CYCLES_WANTED),
    ).fetchall()
    if len(cycle_rows) < SHADOW_CYCLES_WANTED:
        raise h.CaptureError(
            f"trading_day {day} has {len(cycle_rows)} cycle(s) with a cycle_id; "
            f"need {SHADOW_CYCLES_WANTED} (use --day to pick another)"
        )
    cycles = [h.row_to_dict(r) for r in cycle_rows][::-1]  # oldest first
    cycle_ids = [c["cycle_id"] for c in cycles]

    per_cycle = []
    post_trigger_in_cycles: set = set()
    for cycle_id in cycle_ids:
        symphonies = set()
        for r in conn.execute(
            "SELECT symphony_id, MAX(is_post_trigger) AS post_trigger FROM shadow_history "
            "WHERE trading_day = ? AND cycle_id = ? GROUP BY symphony_id",
            (day, cycle_id),
        ):
            symphonies.add(r["symphony_id"])
            if r["post_trigger"]:
                post_trigger_in_cycles.add(r["symphony_id"])
        per_cycle.append(symphonies)
    candidates = set.intersection(*per_cycle)

    day_post_trigger = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT symphony_id FROM shadow_history WHERE trading_day = ? AND is_post_trigger = 1",
            (day,),
        )
    }
    exit_that_day = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT symphony_id FROM exit_triggers WHERE substr(ts_et, 1, 10) = ?", (day,)
        )
    }
    newest_exits = [
        h.row_to_dict(r)
        for r in conn.execute(
            "SELECT id, symphony_id, ts_utc FROM exit_triggers ORDER BY ts_utc DESC, id DESC"
        )
    ]

    tier = None
    triggered = args.triggered_id
    if triggered:
        tier = "override (--triggered-id)"
    else:
        for exit_row in newest_exits:
            if (
                exit_row["symphony_id"] in candidates
                and exit_row["symphony_id"] in post_trigger_in_cycles
            ):
                triggered, tier = exit_row["symphony_id"], TIER_POST_TRIGGER
                break
        if triggered is None:
            for exit_row in newest_exits:
                if exit_row["symphony_id"] in candidates:
                    triggered, tier = exit_row["symphony_id"], TIER_NEWEST_EXIT
                    break
    if triggered is None:
        raise h.CaptureError(
            "no exit_triggers row belongs to a symphony present in both cycles; pass --triggered-id"
        )

    untriggered = args.untriggered_id
    untriggered_basis = "override (--untriggered-id)"
    if not untriggered:
        eligible = sorted(candidates - day_post_trigger - exit_that_day - {triggered})
        if not eligible:
            raise h.CaptureError(
                "no untriggered symphony is present in both cycles; pass --untriggered-id"
            )
        untriggered = eligible[0]
        untriggered_basis = (
            "lowest id present in both cycles with no is_post_trigger row and no exit that day"
        )

    return {
        "day": day,
        "cycles": cycles,
        "triggered_symphony_id": triggered,
        "triggered_selection": tier,
        "untriggered_symphony_id": untriggered,
        "untriggered_selection": untriggered_basis,
        "candidates_in_both_cycles": len(candidates),
        "triggered_has_post_trigger_rows_in_cycles": triggered in post_trigger_in_cycles,
        "triggered_in_both_cycles": triggered in candidates,
        "untriggered_in_both_cycles": untriggered in candidates,
    }


# ---------------------------------------------------------------------------
# 01-c2-shadow-rows
# ---------------------------------------------------------------------------


def _run_shadow_rows(h, args):
    conn = h.open_readonly(args.db)
    try:
        with h.read_snapshot(conn):
            scope = select_shadow_scope(h, conn, args)
            day = scope["day"]
            pair = (scope["triggered_symphony_id"], scope["untriggered_symphony_id"])

            symphonies = {}
            for role, symphony_id in zip(("triggered", "untriggered"), pair):
                previous = conn.execute(
                    "SELECT * FROM shadow_history WHERE symphony_id = ? AND trading_day < ? "
                    "ORDER BY trading_day DESC, ts_utc DESC, id DESC LIMIT 1",
                    (symphony_id, day),
                ).fetchone()
                symphonies[role] = {
                    "symphony_id": symphony_id,
                    "previous_day_last_row": h.row_to_dict(previous)
                    if previous is not None
                    else None,
                }

            cycles_out = []
            cycle_facts = []
            for cycle in scope["cycles"]:
                rows = [
                    h.row_to_dict(r)
                    for r in conn.execute(
                        "SELECT * FROM shadow_history WHERE trading_day = ? AND cycle_id = ? "
                        "AND symphony_id IN (?, ?) ORDER BY ts_utc, id",
                        (day, cycle["cycle_id"], *pair),
                    )
                ]
                cycles_out.append({"cycle_id": cycle["cycle_id"], "rows": rows})
                cycle_facts.append({**cycle, "rows_for_the_two_symphonies": len(rows)})

            null_cycle_rows = conn.execute(
                "SELECT COUNT(*) FROM shadow_history WHERE trading_day = ? AND cycle_id IS NULL",
                (day,),
            ).fetchone()[0]
            newest_ts = conn.execute("SELECT MAX(ts_utc) FROM shadow_history").fetchone()[0]
            columns = [r["name"] for r in conn.execute("PRAGMA table_info(shadow_history)")]
    finally:
        conn.close()

    record_body = {
        "capture_day": day,
        "symphonies": symphonies,
        "cycles": cycles_out,
    }
    record = h.write_json(
        Path(args.out) / C2_DIR / SHADOW_RECORD, record_body, overwrite=args.force
    )
    h.write_sidecar(
        record,
        case="01-c2-shadow-rows",
        db_path=args.db,
        source_table="shadow_history",
        row_key=f"trading_day = {day}; the two newest cycle_id values by MAX(ts_utc); "
        "symphony_id in (triggered, untriggered); plus each symphony's newest earlier row",
        facts={
            "selection": {
                "triggered_symphony_id": scope["triggered_symphony_id"],
                "triggered_selection": scope["triggered_selection"],
                "untriggered_symphony_id": scope["untriggered_symphony_id"],
                "untriggered_selection": scope["untriggered_selection"],
                "triggered_in_both_cycles": scope["triggered_in_both_cycles"],
                "untriggered_in_both_cycles": scope["untriggered_in_both_cycles"],
                "triggered_has_post_trigger_rows_in_cycles": scope[
                    "triggered_has_post_trigger_rows_in_cycles"
                ],
                "candidates_in_both_cycles": scope["candidates_in_both_cycles"],
            },
            "cycles_oldest_first": cycle_facts,
            "rows_on_day_without_cycle_id": null_cycle_rows,
            "newest_shadow_history_ts_utc_at_capture": newest_ts,
            "columns": columns,
        },
        overwrite=args.force,
    )
    print(record)


# ---------------------------------------------------------------------------
# 01-c2-exit-trigger
# ---------------------------------------------------------------------------


def _triggered_from_shadow_capture(h, out_dir: Path):
    sidecar = h.sidecar_path(out_dir / C2_DIR / SHADOW_RECORD)
    if not sidecar.exists():
        return None
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))["facts"]["selection"][
            "triggered_symphony_id"
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise h.CaptureError(
            f"cannot read the triggered symphony from {sidecar}: {type(exc).__name__}"
        ) from exc


def _run_exit_trigger(h, args):
    conn = h.open_readonly(args.db)
    try:
        with h.read_snapshot(conn):
            symphony_id = args.triggered_id
            source = "override (--triggered-id)"
            if not symphony_id:
                symphony_id = _triggered_from_shadow_capture(h, Path(args.out))
                source = "shadow-rows capture sidecar in --out"
            if not symphony_id:
                symphony_id = select_shadow_scope(h, conn, args)["triggered_symphony_id"]
                source = "selected afresh (no shadow-rows capture in --out)"

            row = conn.execute(
                "SELECT * FROM exit_triggers WHERE symphony_id = ? ORDER BY ts_utc DESC, id DESC LIMIT 1",
                (symphony_id,),
            ).fetchone()
            if row is None:
                raise h.CaptureError(f"exit_triggers has no row for symphony {symphony_id}")
            count = conn.execute(
                "SELECT COUNT(*) FROM exit_triggers WHERE symphony_id = ?", (symphony_id,)
            ).fetchone()[0]
            columns = [r["name"] for r in conn.execute("PRAGMA table_info(exit_triggers)")]
            row_dict = h.row_to_dict(row)
    finally:
        conn.close()

    record = h.write_json(Path(args.out) / C2_DIR / EXIT_RECORD, row_dict, overwrite=args.force)
    h.write_sidecar(
        record,
        case="01-c2-exit-trigger",
        db_path=args.db,
        source_table="exit_triggers",
        row_key=f"symphony_id = {symphony_id}; newest by ts_utc DESC, id DESC (id {row_dict['id']})",
        facts={
            "symphony_id": symphony_id,
            "symphony_source": source,
            "exit_triggers_rows_for_symphony": count,
            "columns": columns,
        },
        overwrite=args.force,
    )
    print(record)
