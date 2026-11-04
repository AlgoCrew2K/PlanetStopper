"""The worlds the `GET /api/state` tests and the golden producer build, one named builder each.

Every builder composes the existing worlds instead of seeding anew: the refresh-timing world
(`seeded_world`, `build_world`), the stalled-morning world, the close-gap held view and the
no-longer-held entry. The records are the derived ones of `tests/fixtures/state_poll_no_table/`
(each one a captured record with one named change) and the captured ones they come from.

Fault worlds patch exactly one read the route makes:

  * `database.get_triggers`;
  * one `analytics.get_symphony_*` call, only when `app.get_state` is the caller and only for one
    symphony (any other caller, the portfolio computation for one, reads the real function);
  * `app._dashboard_panels_payload`;
  * `database.get_guard_alpha_by_symphony`.

Every builder fixes the route clock (`frozen_datetime_class` through `build_world`, or the virtual
host clock of `_closed_session_world.install_clock`), so `meta.clock_et`, `meta.next_run` and
`next_run_seconds` are equal between two reads by construction. `WORLDS` names each builder and
`GOLDEN_CASES` names each (world, request) pair that is compared with the reference commit's body,
so the golden producer and the tests build the same worlds through one implementation.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import analytics as analytics_module
import app as app_module
import database as database_module
from tests.app._refresh_timing_state_world import (
    CLOSE_SNAPSHOT_CAPTURED_AT,
    World,
    build_world,
    seeded_world,
)

FIXTURE_FOLDER = Path(__file__).resolve().parents[1] / "fixtures" / "state_poll_no_table"

# The keys of a body that describe the process and not the stored state: `daemon_started_at` is the
# start time of the test process, so a golden written by another process can never equal it.
PROCESS_KEYS = ("daemon_started_at",)

# An account id that no record holds, in the format of the captured account key.
UNKNOWN_ACCOUNT_ID = "c3f1d2a4-7b8e-4c59-9a06-1e2d3f4a5b6c"

# The captured symphonies the derived records hold (ids and names are the captured ones).
CORPORATE_ID = "nOyb55RMGVCKPiYXv7TI"
FEAVERD_ID = "5XjzXjdGnjh99MIsdM97"
REASONABILISTS_ID = "Gpaw3IhZghQPRE6AdEKx"
LQD_ID = "iaSOOUsmnCJHiZvbrWfs"

# The helpers the live branch calls once per symphony, in the order it calls them.
PER_SYMPHONY_HELPERS = (
    ("_tc", "get_symphony_today_change"),
    ("_cr", "get_symphony_cumulative_return"),
    ("_mdd", "get_symphony_max_drawdown"),
)
# The exception types the live branch catches around each helper.
CAUGHT_EXCEPTIONS = (KeyError, TypeError, ValueError)


EXIT_TRIGGER_RECORD = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "refresh_timing"
    / "01-c2"
    / "exit_trigger_row.json"
)


def seed_captured_exit_trigger() -> int:
    """Write the captured exit row of the triggered symphony through the real writer.

    The writer assigns a new row id; the record's own id is not written. Returns the new id.
    """
    record = json.loads(EXIT_TRIGGER_RECORD.read_text(encoding="utf-8"))
    row_id = database_module.record_exit_trigger(
        symphony_id=record["symphony_id"],
        account_id=record["account_id"],
        triggered_reason=record["triggered_reason"],
        at_return=record["at_return"],
        gate_state_json=record["gate_state_json"],
        cycle_id=record["cycle_id"],
        ts_utc=record["ts_utc"],
        ts_et=record["ts_et"],
        math_mode=record["math_mode"],
        port_trigger_id=record["port_trigger_id"],
        also_true=json.loads(record["also_true_json"]),
    )
    assert row_id is not None, "the exit row was not written"
    return row_id


def load_derived(name: str) -> dict:
    return json.loads((FIXTURE_FOLDER / name).read_text(encoding="utf-8"))


@dataclass
class StateWorld:
    """One built world: the Flask client over its database, plus the records it was seeded from."""

    name: str
    client: object
    inner: object
    record: dict | None = None
    notes: dict = field(default_factory=dict)

    def get(self, path: str = "/api/state"):
        return self.client.get(path)

    def body(self, path: str = "/api/state") -> dict:
        response = self.client.get(path)
        assert response.status_code == 200, (
            f"{path} answered {response.status_code}: {response.get_data(as_text=True)[:300]}"
        )
        return response.get_json()

    @property
    def first_account_id(self) -> str:
        """The account of the first symphony entry of the record the world was seeded from."""
        return self.notes["first_account_id"]


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _first_account(record: dict) -> str:
    if "entries" in record:
        entries = record["entries"].values()
    else:
        entries = [e for lst in record["accounts_map"].values() for e in lst]
    return next(iter(entries))["account"]


def _commit_live_record(live: dict, snapshot: dict | None = None) -> None:
    """Save `bot_state` as the engine does at the end of a cycle, from a derived live record."""
    state: dict = dict(live["reserved"])
    state.update(copy.deepcopy(live["entries"]))
    state["last_successful_cycle_at"] = live["last_successful_cycle_at"]
    if snapshot is not None:
        stored = copy.deepcopy(snapshot)
        stored["captured_at_et"] = CLOSE_SNAPSHOT_CAPTURED_AT
        state["last_market_close_snapshot"] = stored
    database_module.save_state(state)


def _seed_rows(world: World) -> None:
    world.seed_rows(world.previous_day_rows())
    world.seed_cycle("m_minus_1")
    world.seed_cycle("m")


def _live_world(
    monkeypatch, tmp_path, name: str, live_record: str, *, branch: str = "open"
) -> StateWorld:
    """The refresh-timing world's rows, with a derived live record committed as the state."""
    world = build_world(monkeypatch, tmp_path, branch)
    _seed_rows(world)
    live = load_derived(live_record)
    _commit_live_record(live)
    return StateWorld(name, world.client, world, live, {"first_account_id": _first_account(live)})


def _snapshot_world(
    monkeypatch, tmp_path, name: str, snapshot_record: str, *, branch: str = "closed_frozen"
) -> StateWorld:
    """The refresh-timing world's rows, with a derived close snapshot stored in the state."""
    world = build_world(monkeypatch, tmp_path, branch)
    _seed_rows(world)
    snapshot = load_derived(snapshot_record)
    # The live state beside the snapshot is the refresh-timing record's own, as `seeded_world`.
    world.commit_state("m")
    state = database_module.load_state()
    stored = copy.deepcopy(snapshot)
    stored["captured_at_et"] = CLOSE_SNAPSHOT_CAPTURED_AT
    state["last_market_close_snapshot"] = stored
    database_module.save_state(state)
    notes = {}
    if "accounts_map" in snapshot:
        notes["first_account_id"] = _first_account(snapshot)
    return StateWorld(name, world.client, world, snapshot, notes)


# ---------------------------------------------------------------------------
# Fault patches (exactly one read each)
# ---------------------------------------------------------------------------


def _raiser(exc_type: type[Exception]):
    def boom(*_args, **_kwargs):
        raise exc_type("planted fault")

    return boom


def fail_triggers(monkeypatch) -> None:
    monkeypatch.setattr(database_module, "get_triggers", _raiser(RuntimeError))


def fail_panels(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "_dashboard_panels_payload", _raiser(RuntimeError))


def fail_guard_alpha(monkeypatch) -> None:
    monkeypatch.setattr(database_module, "get_guard_alpha_by_symphony", _raiser(RuntimeError))


def fail_symphony_helpers(
    monkeypatch, exc_type: type[Exception], *, symphony_name: str, helpers: tuple[str, ...]
) -> None:
    """Make the named `analytics.get_symphony_*` helpers raise for one symphony, when `get_state`
    calls them. Every other caller and every other symphony reads the real function."""
    for helper in helpers:
        real = getattr(analytics_module, helper)

        def planted(*args, _real=real, **kwargs):
            entry = args[1] if len(args) > 1 else None
            caller = sys._getframe(1).f_code.co_name
            if (
                caller == "get_state"
                and isinstance(entry, dict)
                and entry.get("name") == symphony_name
            ):
                raise exc_type("planted fault")
            return _real(*args, **kwargs)

        monkeypatch.setattr(analytics_module, helper, planted)


# ---------------------------------------------------------------------------
# The named worlds
# ---------------------------------------------------------------------------


def open_two_accounts(monkeypatch, tmp_path) -> StateWorld:
    """Market open, a cycle committed today, two accounts, three symphonies."""
    return _live_world(monkeypatch, tmp_path, "open_two_accounts", "live_state_two_accounts.json")


def open_with_snapshot_key(monkeypatch, tmp_path) -> StateWorld:
    """Market open; the state also holds a stored close snapshot, an unnamed dict entry."""
    world = seeded_world(monkeypatch, tmp_path, "open")
    world.commit_state("m", with_snapshot=True)
    return StateWorld("open_with_snapshot_key", world.client, world)


def open_triggers_fail(monkeypatch, tmp_path) -> StateWorld:
    """An exit of the day is stored, and the trigger read fails."""
    built = open_two_accounts(monkeypatch, tmp_path)
    seed_captured_exit_trigger()
    fail_triggers(monkeypatch)
    built.name = "open_triggers_fail"
    return built


def open_analytics_fail(monkeypatch, tmp_path) -> StateWorld:
    """Every per-symphony analytics read raises KeyError for one symphony (Corporate Chaos)."""
    built = open_two_accounts(monkeypatch, tmp_path)
    fail_symphony_helpers(
        monkeypatch,
        KeyError,
        symphony_name=built.record["entries"][CORPORATE_ID]["name"],
        helpers=tuple(helper for _key, helper in PER_SYMPHONY_HELPERS),
    )
    built.name = "open_analytics_fail"
    built.notes["failing_id"] = CORPORATE_ID
    return built


def open_prev_return_cases(monkeypatch, tmp_path) -> StateWorld:
    return _live_world(
        monkeypatch, tmp_path, "open_prev_return_cases", "live_state_prev_return_cases.json"
    )


def open_panels_fail(monkeypatch, tmp_path) -> StateWorld:
    built = open_two_accounts(monkeypatch, tmp_path)
    fail_panels(monkeypatch)
    built.name = "open_panels_fail"
    return built


def closed_no_snapshot_two_accounts(monkeypatch, tmp_path) -> StateWorld:
    """Market closed, no snapshot stored, the open world's live state: the live branch serves."""
    return _live_world(
        monkeypatch,
        tmp_path,
        "closed_no_snapshot_two_accounts",
        "live_state_two_accounts.json",
        branch="closed_frozen",
    )


def closed_frozen_two_accounts(monkeypatch, tmp_path) -> StateWorld:
    return _snapshot_world(
        monkeypatch, tmp_path, "closed_frozen_two_accounts", "snapshot_two_accounts.json"
    )


def closed_missing_defaults(monkeypatch, tmp_path) -> StateWorld:
    return _snapshot_world(
        monkeypatch, tmp_path, "closed_missing_defaults", "snapshot_missing_defaults.json"
    )


def closed_null_defaults(monkeypatch, tmp_path) -> StateWorld:
    return _snapshot_world(
        monkeypatch, tmp_path, "closed_null_defaults", "snapshot_null_defaults.json"
    )


def closed_unnamed_entry(monkeypatch, tmp_path) -> StateWorld:
    return _snapshot_world(
        monkeypatch, tmp_path, "closed_unnamed_entry", "snapshot_unnamed_entry.json"
    )


def closed_no_accounts_map(monkeypatch, tmp_path) -> StateWorld:
    return _snapshot_world(
        monkeypatch, tmp_path, "closed_no_accounts_map", "snapshot_no_accounts_map.json"
    )


def closed_guard_alpha_fail(monkeypatch, tmp_path) -> StateWorld:
    """Both captured entries of the first account are triggered and an exit row of one is stored,
    so the read would find a figure; it fails instead."""
    built = closed_frozen_two_accounts(monkeypatch, tmp_path)
    seed_captured_exit_trigger()
    fail_guard_alpha(monkeypatch)
    built.name = "closed_guard_alpha_fail"
    return built


def pre_market(monkeypatch, tmp_path) -> StateWorld:
    world = seeded_world(monkeypatch, tmp_path, "pre_market")
    return StateWorld("pre_market", world.client, world)


def closed_no_longer_held(monkeypatch, tmp_path) -> StateWorld:
    from tests.app.test_no_longer_held_surfaces import _add_stale_entry, _seed_older_sessions

    world = seeded_world(monkeypatch, tmp_path, "closed_frozen")
    _seed_older_sessions(world)
    _add_stale_entry(world)
    return StateWorld("closed_no_longer_held", world.client, world)


def stalled_open(monkeypatch, tmp_path) -> StateWorld:
    """Market open, no cycle committed since today's open, Friday's close snapshot stored."""
    from tests.app.test_open_market_stalled_figures_are_the_last_sessions import (
        MONDAY,
        fresh_world,
    )

    closed = fresh_world(monkeypatch, tmp_path / "stalled_open", sleeve=False)
    closed.at(MONDAY, "09:40:00")
    return StateWorld("stalled_open", closed.world.client, closed)


def held_view(monkeypatch, tmp_path) -> StateWorld:
    """The market closed a minute ago and the session's snapshot is not stored yet."""
    from tests.app.test_close_gap_serves_session_that_closed import (
        MONDAY,
        monday_before_the_close,
    )

    closed = monday_before_the_close(monkeypatch, tmp_path)
    closed.at(MONDAY, "16:00:35")
    return StateWorld("held_view", closed.world.client, closed)


def no_state(monkeypatch, tmp_path) -> StateWorld:
    world = build_world(monkeypatch, tmp_path, "open")
    return StateWorld("no_state", world.client, world)


def no_state_closed(monkeypatch, tmp_path) -> StateWorld:
    world = build_world(monkeypatch, tmp_path, "closed_frozen")
    return StateWorld("no_state_closed", world.client, world)


WORLDS: dict[str, Callable[..., StateWorld]] = {
    "open_two_accounts": open_two_accounts,
    "open_with_snapshot_key": open_with_snapshot_key,
    "open_triggers_fail": open_triggers_fail,
    "open_analytics_fail": open_analytics_fail,
    "open_prev_return_cases": open_prev_return_cases,
    "open_panels_fail": open_panels_fail,
    "closed_no_snapshot_two_accounts": closed_no_snapshot_two_accounts,
    "closed_frozen_two_accounts": closed_frozen_two_accounts,
    "closed_missing_defaults": closed_missing_defaults,
    "closed_null_defaults": closed_null_defaults,
    "closed_unnamed_entry": closed_unnamed_entry,
    "closed_no_accounts_map": closed_no_accounts_map,
    "closed_guard_alpha_fail": closed_guard_alpha_fail,
    "closed_no_longer_held": closed_no_longer_held,
    "pre_market": pre_market,
    "stalled_open": stalled_open,
    "held_view": held_view,
    "no_state": no_state,
    "no_state_closed": no_state_closed,
}


def _bare(_world: StateWorld) -> str:
    return "/api/state"


def _first_account_query(world: StateWorld) -> str:
    return f"/api/state?account={world.first_account_id}"


def _unknown_account_query(_world: StateWorld) -> str:
    return f"/api/state?account={UNKNOWN_ACCOUNT_ID}"


# (world name, request builder) by golden case name. A golden is `golden_ref_<case>.json`.
GOLDEN_CASES: dict[str, tuple[str, Callable[[StateWorld], str]]] = {
    **{name: (name, _bare) for name in WORLDS},
    "open_two_accounts__account_first": ("open_two_accounts", _first_account_query),
    "open_two_accounts__account_none": ("open_two_accounts", _unknown_account_query),
}


def golden_path(case: str) -> Path:
    return FIXTURE_FOLDER / f"golden_ref_{case}.json"


def comparable(body: dict) -> dict:
    """A body without the keys that describe the test process, as a golden is compared."""
    return {key: value for key, value in body.items() if key not in PROCESS_KEYS}


REFERENCE_COMMIT = "758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7"
PRODUCTION_PATHS = (
    "app.py",
    "templates",
    "static",
    "analytics.py",
    "database.py",
    "market_calendar.py",
)


def refuse_unless_production_equals_the_reference() -> None:
    """Stop a producer that is not running on the reference commit's production code.

    A golden or a baseline written from any other tree would only prove that tree agrees with
    itself: the diff against the reference must be empty and the paths must have no uncommitted
    change.
    """
    repo = Path(__file__).resolve().parents[2]
    differs = subprocess.run(
        ["git", "-C", str(repo), "diff", "--quiet", REFERENCE_COMMIT, "--", *PRODUCTION_PATHS],
        check=False,
    ).returncode
    dirty = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--", *PRODUCTION_PATHS],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if differs or dirty:
        raise SystemExit(
            f"refusing to write: the production paths differ from {REFERENCE_COMMIT}"
            + (f" (uncommitted: {dirty})" if dirty else "")
        )
