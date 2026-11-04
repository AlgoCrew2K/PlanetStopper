"""Derive the state-poll records from the captured records of `refresh_timing/01-c2` and
`prod_droplet`.

Stdlib only. Run with `python -I derive_records.py` to (re)write the derived files, or import
`derive_all()` to get them in memory; a test re-derives and compares byte for byte, so a derived
record can never drift from the captures it names.

Every derived record changes only the fields named beside it in this folder's `PROVENANCE.md`;
nothing here invents a value a capture does not already hold, except the one named second account
id (`SECOND_ACCOUNT_ID`, a lower-case UUID v4 string in the format of the captured account key).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
FIXTURES = FOLDER.parent
R8_PATH = FIXTURES / "refresh_timing" / "01-c2" / "snapshot_two_symphonies.json"
LIVE_PATH = FIXTURES / "refresh_timing" / "01-c2" / "live_state_two_cycles.json"
POSITIONS_PATH = FIXTURES / "prod_droplet" / "bot_state_positions.json"

# Captured symphonies (ids and display names are the captured ones).
CORPORATE_ID = "nOyb55RMGVCKPiYXv7TI"
FEAVERD_ID = "5XjzXjdGnjh99MIsdM97"
REASONABILISTS_ID = "Gpaw3IhZghQPRE6AdEKx"
LQD_ID = "iaSOOUsmnCJHiZvbrWfs"

# A second account id, in the format of the captured account key (a lower-case UUID v4 string).
SECOND_ACCOUNT_ID = "9a735bfb-ef62-4705-b9de-52f6de25363f"

# The 13 fields `app._FROZEN_SYM_DEFAULTS` defaults (a test compares this tuple with the module's).
FROZEN_DEFAULT_KEYS = (
    "mc_prob",
    "shadow_hwm",
    "stop_trigger",
    "current_return",
    "current_value",
    "breakeven_locked",
    "armed",
    "tp_armed",
    "para_armed",
    "triggered",
    "above_tp_count",
    "below_stop_count",
    "last_trigger",
)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _second_account_entry(positions: dict, symphony_id: str, *, with_id: bool) -> dict:
    entry = copy.deepcopy(positions[symphony_id])
    entry["account"] = SECOND_ACCOUNT_ID
    if with_id:
        entry["id"] = symphony_id
    return entry


def snapshot_two_accounts(r8: dict, positions: dict) -> dict:
    """R8 with its account list in reverse name order, plus a second account of two captured entries.

    The first account lists Land of Feaver'd then Corporate Chaos (name order is the reverse). The
    second lists Planet of the Reasonabilists then (INVEST) LQD (name order is the reverse).
    """
    snap = copy.deepcopy(r8)
    (first_account,) = snap["accounts_map"]
    snap["accounts_map"][first_account].reverse()
    snap["accounts_map"][SECOND_ACCOUNT_ID] = [
        _second_account_entry(positions, REASONABILISTS_ID, with_id=True),
        _second_account_entry(positions, LQD_ID, with_id=True),
    ]
    return snap


def _entry(snapshot: dict, symphony_id: str) -> dict:
    for entries in snapshot["accounts_map"].values():
        for entry in entries:
            if entry["id"] == symphony_id:
                return entry
    raise KeyError(symphony_id)


def snapshot_missing_defaults(two_accounts: dict) -> dict:
    snap = copy.deepcopy(two_accounts)
    entry = _entry(snap, FEAVERD_ID)
    for key in FROZEN_DEFAULT_KEYS:
        entry.pop(key, None)
    return snap


def snapshot_null_defaults(two_accounts: dict) -> dict:
    snap = copy.deepcopy(two_accounts)
    entry = _entry(snap, FEAVERD_ID)
    for key in FROZEN_DEFAULT_KEYS:
        entry[key] = None
    return snap


def snapshot_unnamed_entry(two_accounts: dict) -> dict:
    snap = copy.deepcopy(two_accounts)
    del _entry(snap, REASONABILISTS_ID)["name"]
    return snap


def snapshot_no_accounts_map(r8: dict) -> dict:
    snap = copy.deepcopy(r8)
    del snap["accounts_map"]
    return snap


def live_state_two_accounts(live: dict, positions: dict) -> dict:
    """Cycle `m` of the two-cycle live state plus the captured Reasonabilists entry, second account."""
    cycle = live["m"]
    entries = copy.deepcopy(cycle["entries"])
    entries[REASONABILISTS_ID] = _second_account_entry(positions, REASONABILISTS_ID, with_id=False)
    return {
        "entries": entries,
        "last_successful_cycle_at": cycle["last_successful_cycle_at"],
        "reserved": copy.deepcopy(live["reserved"]),
    }


def live_state_prev_return_cases(two_accounts: dict) -> dict:
    """Three entries, one per case: no `prev_return` (the captured entry has none), no
    `current_return` (removed here), and both (Corporate Chaos)."""
    rec = copy.deepcopy(two_accounts)
    del rec["entries"][FEAVERD_ID]["current_return"]
    return rec


def derive_all() -> dict[str, dict]:
    r8 = load(R8_PATH)
    live = load(LIVE_PATH)
    positions = load(POSITIONS_PATH)
    two_accounts = snapshot_two_accounts(r8, positions)
    live_two = live_state_two_accounts(live, positions)
    return {
        "snapshot_two_accounts.json": two_accounts,
        "live_state_two_accounts.json": live_two,
        "snapshot_missing_defaults.json": snapshot_missing_defaults(two_accounts),
        "snapshot_null_defaults.json": snapshot_null_defaults(two_accounts),
        "snapshot_unnamed_entry.json": snapshot_unnamed_entry(two_accounts),
        "snapshot_no_accounts_map.json": snapshot_no_accounts_map(r8),
        "live_state_prev_return_cases.json": live_state_prev_return_cases(live_two),
    }


def _sibling_serializer():
    """The serializer of the record derivation this one follows (one rule for the byte form)."""
    path = FIXTURES / "refresh_timing" / "01-c2" / "derive_records.py"
    spec = importlib.util.spec_from_file_location("refresh_timing_derive_records", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.serialize


serialize = _sibling_serializer()


def main() -> int:
    for name, record in derive_all().items():
        (FOLDER / name).write_text(serialize(record), encoding="utf-8", newline="\n")
        print(f"wrote {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
