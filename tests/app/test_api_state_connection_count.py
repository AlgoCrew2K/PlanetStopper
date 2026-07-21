"""
RED tests — F-1: GET /api/state opens O(symphonies) SQLite connections.

Source: docs/audit/confidence-program/INSTITUTIONAL-READINESS-REPORT.md /
feature-plans/fix-ops-cluster.md — "F-1 (efficiency, PM-REPRODUCED 157 SQLite
connects / 1.4s per /api/state poll): the dashboard route's per-symphony
enrichment opens a fresh SQLite connection at ~8 connect-sites + 3
per-symphony metric functions inside the loop. Fix: ONE shared read-only
connection per request (or equivalent batching) ... Success = connection
count per poll drops from ~157 to a small constant (test pins the
MECHANISM, a counting seam; no wall-clock assertions)."

Confirmed mechanism: the live branch of app.get_state() (app.py ~2288) loops
`for k in symphony_keys:` calling analytics.get_symphony_today_change /
get_symphony_cumulative_return / get_symphony_max_drawdown per symphony —
each of which independently opens a fresh sqlite3.connect() to read
shadow_history (analytics.py:570 _load_latest_shadow_row_for_analytics, plus
the cumulative-return/max-drawdown trajectory helpers).

AC-1: connection count is a small constant, NOT O(symphonies) — pinned via a
counting-passthrough spy on the real sqlite3.connect (both database.py and
analytics.py call the bare stdlib function; grep-confirmed no
`from sqlite3 import connect` anywhere, so patching the module attribute
intercepts every call site). Response payload must stay correct (byte-
equivalent computed values) — proven via independent recompute against the
same fixture DB (this project's established recompute-vs-frozen-snapshot
pattern), never a hardcoded literal.

DB seam: tests/conftest.py's autouse `_isolate_db` fixture already provides
a fresh, fully-initialised per-test SQLite DB at `DB_PATH`. bot_state is
seeded via the real `database.save_state()`; shadow_history rows are
inserted via raw sqlite3 (no public writer accessor exists — the engine
writes it directly in alpha_bot_execution.py). `analytics.DB_FILE` is
monkeypatched to the same DB_PATH (analytics.py's documented test seam:
"Patched by tests to redirect DB queries to tmp_path databases") since the
live route calls the analytics functions WITHOUT a db_path kwarg.

-n0 only. No live network (get_market_state forced to "open").
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import analytics as analytics_module
import app as app_module
import database as database_module

_ET = ZoneInfo("America/New_York")


def _today_et() -> str:
    return datetime.now(_ET).strftime("%Y-%m-%d")


def _seed_symphonies(n: int, *, start_index: int = 0) -> list[str]:
    """Seed bot_state with n symphonies (via the real database.save_state)
    plus one shadow_history row each for today's trading_day, so every one
    of the 3 per-symphony analytics functions has real data to query
    (maximizing the current code's connection count — a conservative,
    RED-favoring fixture, never a hardcoded expected-count literal).
    Returns the list of symphony ids seeded.
    """
    today = _today_et()
    state = database_module.load_state() or {}
    state["last_successful_cycle_at"] = datetime.now(_ET).isoformat()
    ids = []
    for i in range(start_index, start_index + n):
        sym_id = f"sym-hash-{i}"
        ids.append(sym_id)
        state[sym_id] = {
            "name": f"Symphony {i}",
            "account": "ACC1",
            "current_return": 1.5,
            "current_value": 10000.0,
            "simple_return": 0.02,
            "net_deposits": 100.0,
            "time_weighted_return": 0.021,
            "max_drawdown": 0.05,
            "prev_return": 1.2,
            "armed": False,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
        }
    database_module.save_state(state)

    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    for sym_id in ids:
        conn.execute(
            "INSERT INTO shadow_history "
            "(ts_utc, ts_et, trading_day, symphony_id, current_return, shadow_return) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.now(_ET).isoformat(),
                datetime.now(_ET).isoformat(),
                today,
                sym_id,
                1.5,
                1.5,
            ),
        )
    conn.commit()
    conn.close()
    return ids


class _CountingConnect:
    """Passthrough spy: counts every real sqlite3.connect() call while still
    delegating to the real function — never mocks the DB away.
    """

    def __init__(self, real_connect):
        self._real_connect = real_connect
        self.count = 0

    def __call__(self, *args, **kwargs):
        self.count += 1
        return self._real_connect(*args, **kwargs)


@pytest.fixture()
def client(monkeypatch):
    # analytics.py's documented test seam — the live route calls the 3
    # per-symphony functions without a db_path kwarg, so they fall back to
    # this module-level override (or database.DB_FILE, which is frozen at
    # import time and would NOT reflect this per-test DB_PATH).
    monkeypatch.setattr(analytics_module, "DB_FILE", os.environ["DB_PATH"])
    # Force the live (non-frozen) branch — precedent: tests/app/test_f011_state_field_availability.py.
    monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# AC-1: connection count does not scale with symphony count.
# ---------------------------------------------------------------------------


def test_connection_count_does_not_scale_with_symphony_count(client, monkeypatch):
    real_connect = sqlite3.connect
    spy = _CountingConnect(real_connect)
    monkeypatch.setattr(sqlite3, "connect", spy)

    _seed_symphonies(2)
    spy.count = 0  # reset — only count connections opened DURING the request
    resp_2 = client.get("/api/state")
    assert resp_2.status_code == 200, (
        f"unexpected status: {resp_2.status_code} {resp_2.get_data()!r}"
    )
    count_2 = spy.count

    _seed_symphonies(4, start_index=2)  # now 6 symphonies total in the same DB
    spy.count = 0
    resp_6 = client.get("/api/state")
    assert resp_6.status_code == 200, (
        f"unexpected status: {resp_6.status_code} {resp_6.get_data()!r}"
    )
    count_6 = spy.count

    delta = count_6 - count_2
    assert delta <= 2, (
        f"AC-1 GAP: adding 4 more symphonies (2 -> 6) increased the per-request SQLite "
        f"connection count by {delta} ({count_2} -> {count_6}) — connection count must be "
        f"a SMALL CONSTANT, not O(symphonies). Each per-symphony analytics function "
        f"opening its own connection would add ~1-3 connects PER extra symphony "
        f"(here: 4 extra symphonies), which this bound catches."
    )


def test_connection_count_is_a_small_constant_for_a_single_symphony(client, monkeypatch):
    """A weaker, absolute-ceiling companion to the scaling test — catches a
    fix that shares connections within the analytics loop but still opens a
    large constant number of connections elsewhere in the request.
    """
    real_connect = sqlite3.connect
    spy = _CountingConnect(real_connect)
    monkeypatch.setattr(sqlite3, "connect", spy)

    _seed_symphonies(1)
    spy.count = 0
    resp = client.get("/api/state")
    assert resp.status_code == 200
    assert spy.count <= 15, (
        f"AC-1 GAP: a single-symphony /api/state request opened {spy.count} SQLite "
        f"connections — expected a small constant (<=15, generously covering the "
        f"non-per-symphony connect sites named in the plan)."
    )


# ---------------------------------------------------------------------------
# AC-1: response correctness unchanged — recompute vs the route's output,
# never a hardcoded literal (this project's established recompute-vs-
# frozen-snapshot pattern).
# ---------------------------------------------------------------------------


def test_response_tc_cr_mdd_match_independent_recompute(client, monkeypatch):
    """The route flattens _tc/_cr/_mdd (each {if_held, dry_run}) into
    tc_bot/tc_held/cr_bot/cr_held/mdd_bot/mdd_held on each entry of the
    top-level "symphonies" list (app.py:2406-2441, _tc_cr_mdd_floats) —
    dry_run -> *_bot, if_held -> *_held. Compared against an independent
    recompute of the same 3 analytics functions against the same fixture DB.
    """
    ids = _seed_symphonies(2)
    resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.get_json()
    symphonies_list = data.get("symphonies")
    assert isinstance(symphonies_list, list) and symphonies_list, (
        f"expected a non-empty 'symphonies' list in the /api/state response; "
        f"got {data.get('symphonies')!r}"
    )
    by_id = {s.get("id"): s for s in symphonies_list if isinstance(s, dict)}

    today = _today_et()
    for sym_id in ids:
        assert sym_id in by_id, (
            f"expected {sym_id!r} among the 'symphonies' list ids; got {sorted(by_id.keys())!r}"
        )
        sym_entry = by_id[sym_id]
        sym_dict = {
            "id": sym_id,
            "value": 10000.0,
            "last_percent_change": 1.5 / 100.0,
            "simple_return": 0.02,
            "net_deposits": 100.0,
            "time_weighted_return": 0.021,
            "max_drawdown": 0.05,
            "trading_day": today,
        }
        # bot_state_entry passed to the analytics fns is the raw per-symphony
        # dict — reconstruct it the same way _seed_symphonies wrote it.
        bot_state_entry = {
            "name": f"Symphony {sym_id.rsplit('-', 1)[-1]}",
            "current_return": 1.5,
        }
        expected_tc = analytics_module.get_symphony_today_change(
            sym_dict, bot_state_entry, trading_day=today
        )
        expected_cr = analytics_module.get_symphony_cumulative_return(
            sym_dict, bot_state_entry, trading_day=today
        )
        expected_mdd = analytics_module.get_symphony_max_drawdown(
            sym_dict, bot_state_entry, trading_day=today
        )
        assert sym_entry.get("tc_bot") == expected_tc.get("dry_run"), (
            f"{sym_id}: route's tc_bot {sym_entry.get('tc_bot')!r} != independently "
            f"recomputed dry_run {expected_tc.get('dry_run')!r} — the connection-sharing "
            f"refactor must not change computed values, only the connection mechanism."
        )
        assert sym_entry.get("tc_held") == expected_tc.get("if_held"), (
            f"{sym_id}: route's tc_held {sym_entry.get('tc_held')!r} != independently "
            f"recomputed if_held {expected_tc.get('if_held')!r}"
        )
        assert sym_entry.get("cr_bot") == expected_cr.get("dry_run"), (
            f"{sym_id}: route's cr_bot {sym_entry.get('cr_bot')!r} != independently "
            f"recomputed dry_run {expected_cr.get('dry_run')!r}"
        )
        assert sym_entry.get("cr_held") == expected_cr.get("if_held"), (
            f"{sym_id}: route's cr_held {sym_entry.get('cr_held')!r} != independently "
            f"recomputed if_held {expected_cr.get('if_held')!r}"
        )
        assert sym_entry.get("mdd_bot") == expected_mdd.get("dry_run"), (
            f"{sym_id}: route's mdd_bot {sym_entry.get('mdd_bot')!r} != independently "
            f"recomputed dry_run {expected_mdd.get('dry_run')!r}"
        )
        assert sym_entry.get("mdd_held") == expected_mdd.get("if_held"), (
            f"{sym_id}: route's mdd_held {sym_entry.get('mdd_held')!r} != independently "
            f"recomputed if_held {expected_mdd.get('if_held')!r}"
        )


# ---------------------------------------------------------------------------
# Edge case (plan): concurrent requests must not share a connection unsafely
# — per-request scope, not module-global. A crude same-process regression
# guard: two sequential requests must each succeed (a module-global
# connection reused across requests without re-open would still pass this,
# but a connection incorrectly CLOSED after the first request and never
# reopened would fail the second — catches the most common unsafe-sharing bug).
# ---------------------------------------------------------------------------


def test_two_sequential_requests_both_succeed(client):
    _seed_symphonies(2)
    resp_1 = client.get("/api/state")
    resp_2 = client.get("/api/state")
    assert resp_1.status_code == 200
    assert resp_2.status_code == 200
