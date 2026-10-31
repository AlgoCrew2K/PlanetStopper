"""Derive the cycle-2 fixture records from the captured records R1 and R6.

Stdlib only. Run with `python -I derive_records.py` to (re)write the derived files, or
import `derive_all()` to get them in memory; a test re-derives and compares byte for byte,
so a derived record can never drift from the captures it names.

Every derived record changes only the fields named beside it in this folder's
`PROVENANCE.md`; nothing here invents a value that a capture does not already hold,
except the one named offset `CYCLE_MOVE_PCT`.
"""

from __future__ import annotations

import copy
import gzip
import json
import sys
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
R1_PATH = FOLDER.parent / "01-c1" / "nested_bot_state_row.json.gz"
R6_PATH = FOLDER / "shadow_rows_two_cycles.json"

TRIGGERED_ID = "nOyb55RMGVCKPiYXv7TI"
UNTRIGGERED_ID = "5XjzXjdGnjh99MIsdM97"
SYMPHONY_IDS = (TRIGGERED_ID, UNTRIGGERED_ID)

# R6 is the 2026-10-21 trading day; R1 is the 2026-10-22 close.
R6_TRADING_DAY = "2026-10-21"

# R6's two cycles carry identical return values (the captured cycles ran after the close,
# when nothing moves), so a test cannot tell which cycle a figure came from. The newer
# cycle's rows are therefore derived with this one named offset in percentage points.
CYCLE_MOVE_PCT = 0.35

# One decimal-place-safe rounding for the derived percent-scale values.
_ROUND_DP = 2


def load_r1() -> dict:
    return json.loads(gzip.open(R1_PATH).read().decode("utf-8"))


def load_r6() -> dict:
    return json.loads(R6_PATH.read_text(encoding="utf-8"))


def _real_account_key(accounts_map: dict) -> str:
    keys = [k for k in accounts_map if k != "Unknown"]
    if len(keys) != 1:
        raise ValueError(f"expected exactly one real account in R1, found {keys!r}")
    return keys[0]


def snapshot_two_symphonies(r1: dict) -> dict:
    """R8: R1's close snapshot narrowed to the two R6 symphonies, flat, on R6's day."""
    snap = r1["last_market_close_snapshot"]
    account = _real_account_key(snap["accounts_map"])
    by_id = {e["id"]: e for e in snap["accounts_map"][account]}
    entries = [copy.deepcopy(by_id[sym_id]) for sym_id in SYMPHONY_IDS]
    divergence = copy.deepcopy(snap["shadow_divergence"])
    divergence["by_symphony"] = {
        sym_id: divergence["by_symphony"][sym_id] for sym_id in SYMPHONY_IDS
    }
    return {
        "trading_day": R6_TRADING_DAY,
        "captured_at_et": snap["captured_at_et"],
        "data_as_of": snap["data_as_of"],
        "portfolio_strip": copy.deepcopy(snap["portfolio_strip"]),
        "shadow_divergence": divergence,
        "accounts_map": {account: entries},
    }


def _cycle_rows(r6: dict, cycle_id: str) -> dict[str, dict]:
    for cycle in r6["cycles"]:
        if cycle["cycle_id"] == cycle_id:
            return {row["symphony_id"]: row for row in cycle["rows"]}
    raise KeyError(cycle_id)


def shadow_rows_cycle_m_moved(r6: dict) -> dict:
    """The newer R6 cycle with its figures moved by CYCLE_MOVE_PCT.

    The untriggered symphony moves on both series; the triggered symphony's held series
    moves while its bot series stays at the frozen exit value, as the engine writes it.
    """
    newer = r6["cycles"][-1]
    rows = []
    for row in newer["rows"]:
        moved = copy.deepcopy(row)
        moved["current_return"] = round(row["current_return"] + CYCLE_MOVE_PCT, _ROUND_DP)
        if not row["is_post_trigger"]:
            moved["shadow_return"] = round(row["shadow_return"] + CYCLE_MOVE_PCT, _ROUND_DP)
        rows.append(moved)
    return {"cycle_id": newer["cycle_id"], "rows": rows}


def _stamp(r1: dict, ts_et: str) -> str:
    """The engine's `last_successful_cycle_at` for a cycle whose rows carry `ts_et`.

    The engine writes current_et.isoformat(); the rows carry the same instant truncated to
    whole seconds. The sub-second part and the offset are R1's own.
    """
    real = r1["last_successful_cycle_at"]
    sub_second = real[real.index(".") : real.index("-", real.index("."))]
    offset = real[real.index("-", real.index(".")) :]
    return f"{ts_et}{sub_second}{offset}"


def live_state_two_cycles(r1: dict, r6: dict, moved: dict) -> dict:
    """The live `bot_state` entries of the two symphonies as the engine commits them.

    Entries are R1's, with `current_return` set to the value the engine writes in the same
    cycle as the matching shadow row, and the untriggered symphony armed rather than
    triggered (R6's rows show it was not triggered on 2026-10-21).
    """
    older_rows = _cycle_rows(r6, r6["cycles"][0]["cycle_id"])
    newer_rows = {row["symphony_id"]: row for row in moved["rows"]}
    out = {}
    for label, rows in (("m_minus_1", older_rows), ("m", newer_rows)):
        entries = {}
        for sym_id in SYMPHONY_IDS:
            entry = copy.deepcopy(r1[sym_id])
            entry["current_return"] = rows[sym_id]["current_return"]
            if sym_id == UNTRIGGERED_ID:
                entry["triggered"] = False
                entry["armed"] = True
            entries[sym_id] = entry
        any_row = next(iter(rows.values()))
        out[label] = {
            "last_successful_cycle_at": _stamp(r1, any_row["ts_et"]),
            "entries": entries,
        }
    out["reserved"] = {
        "date": R6_TRADING_DAY,
        "last_execution_mode": r1["last_execution_mode"],
    }
    return out


def derive_all() -> dict[str, dict]:
    r1 = load_r1()
    r6 = load_r6()
    moved = shadow_rows_cycle_m_moved(r6)
    return {
        "snapshot_two_symphonies.json": snapshot_two_symphonies(r1),
        "live_state_two_cycles.json": live_state_two_cycles(r1, r6, moved),
        "shadow_rows_cycle_m_moved.json": moved,
    }


def serialize(record: dict) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def main() -> int:
    for name, record in derive_all().items():
        (FOLDER / name).write_text(serialize(record), encoding="utf-8", newline="\n")
        print(f"wrote {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
