"""Seed the temporary state database with the captured shadow_history rows, and pick the row a
latest-row read has to return, from the file alone.

TEST-ONLY. The rows are the three captures under ``tests/fixtures/prod_droplet/``
(``shadow_last_rows.json``, ``shadow_postclose_rows.json``, ``shadow_snapshot_rows.json``,
written by ``capture_from_droplet.py``). Nothing here knows a value from them: a test reads the
file, inserts every row under the columns the row itself names, and derives what a reader must
return by the rule the reader is specified by (the greatest ``ts_utc`` of the symphony and day,
at or before an ET time of day when one is given). So the expectations hold on whatever the file
holds, redacted or not.
"""

from __future__ import annotations

import json
import pathlib
from collections import defaultdict

import database

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "prod_droplet"
CAPTURE_FILES = ("shadow_last_rows.json", "shadow_postclose_rows.json", "shadow_snapshot_rows.json")

# Columns the table adds itself; a returned row may carry these beyond the captured keys.
TABLE_ADDED_COLUMNS = frozenset({"id", "trigger_id", "math_mode"})


def capture_rows(name: str) -> list[dict]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def seed(rows: list[dict]) -> None:
    """Insert every row under the columns the row names."""
    conn = database.get_connection()
    try:
        for row in rows:
            columns = list(row)
            conn.execute(
                f"INSERT INTO shadow_history ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                [row[c] for c in columns],
            )
        conn.commit()
    finally:
        conn.close()


def groups(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Rows by (symphony_id, trading_day), only those groups whose greatest ts_utc is unique,
    so the 'latest' row is one row and not a tie broken by storage order."""
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        by_key[(row["symphony_id"], row["trading_day"])].append(row)
    unique = {}
    for key, members in by_key.items():
        newest = max(r["ts_utc"] for r in members)
        if sum(1 for r in members if r["ts_utc"] == newest) == 1:
            unique[key] = members
    return unique


def time_of_day(row: dict) -> str:
    """The 'HH:MM:SS' the SQL takes with substr(ts_et, 12, 8)."""
    return row["ts_et"][11:19]


def expected_latest(members: list[dict], et_cutoff: str | None = None) -> dict | None:
    qualifying = [m for m in members if et_cutoff is None or time_of_day(m) <= et_cutoff]
    if not qualifying:
        return None
    return max(qualifying, key=lambda r: r["ts_utc"])


def same_row(returned: dict | None, expected: dict | None) -> bool:
    """Every captured key equal, and nothing beyond what the table adds."""
    if expected is None or returned is None:
        return returned is expected
    return all(returned.get(k) == v for k, v in expected.items()) and (
        set(returned) - set(expected) <= TABLE_ADDED_COLUMNS
    )


def median_cutoff(members: list[dict]) -> str:
    ordered = sorted(members, key=lambda r: r["ts_utc"])
    return time_of_day(ordered[len(ordered) // 2])
