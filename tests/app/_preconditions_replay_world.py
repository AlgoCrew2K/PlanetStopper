"""Shared world for the preconditions replay load-once tests.

``GET /api/guard-alpha-preconditions`` answers each held symphony's replay row from one
portfolio-wide synthetic-history cache file. This module builds the states those tests (and the
golden capture script) drive: the bot state, the ``shadow_history`` rows, the cache files under a
per-test ``cache`` directory, the frozen clock, a decode counter and the real threaded server.

Fixture provenance (one class per piece):

* captured producer output: ``tests/fixtures/preconditions_replay/cache_excerpt_2026-11-13.json``
  (the droplet's autotune cache file for 2026-11-13, excerpted, PM read-only capture). The bot
  state's holdings and names and every replay history here come from it; the cache file name is
  never typed, it comes from ``synthetic_history._resolve_history_cache_key``.
* synthetic and labelled so: the ``shadow_history`` rows (a seeded sequence in the table's real
  schema), the five bot-state metadata keys, the added symphony of ``symphony_added`` and the
  production-sized files (the excerpt's real tick records replicated to the recorded shape of
  12 symphonies, 250 dates, 1 to 456 ticks per date, written at test time and never committed).
* goldens: ``tests/fixtures/preconditions_replay/goldens/<state>.json``, the unchanged base
  route's body for the state of the same name (see ``PROVENANCE.md`` beside them).
"""

from __future__ import annotations

import contextlib
import copy
import http.client
import json
import math
import os
import random
import shutil
import sqlite3
import statistics
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import analytics
import app as app_module
import autotuner
import database
import guard_preconditions as gp
import market_calendar
import synthetic_history
from tests.app._refresh_timing_state_world import frozen_datetime_class
from tests.ui._browser_server import running_server

ET = ZoneInfo("America/New_York")
ROUTE = "/api/guard-alpha-preconditions"

# The real state reader, captured at import (collection), before any test's stand-in.
_REAL_LOAD_STATE = database.load_state

# Sunday 2026-11-15 noon ET: the most recent completed session is Friday 2026-11-13, the day the
# excerpt's cache file is dated, so the file sits one trading day back from the probe's start.
FROZEN_INSTANT = datetime(2026, 11, 15, 12, 0, tzinfo=ET)
FILE_DATE = "2026-11-13"
OLDER_DATE = "2026-11-12"
# A Sunday: the probe's first candidate, a date no session is dated, so a file there is "newer".
NEWEST_DATE = "2026-11-15"

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "preconditions_replay"
EXCERPT = FIXTURES / "cache_excerpt_2026-11-13.json"
GOLDENS = FIXTURES / "goldens"

CACHE_MARKER = "synthetic_history_v4_"
MISS_WORDING = "synthetic_history: cache miss for"
WARNING_TEMPLATE = "synthetic_history: corrupt or unreadable cache file %s: %s"

# An invented id for the symphony the "added" state introduces.
ADDED_ID = "Kp4WqZr7TnB2xLm9Hc3D"
ADDED_HOLDINGS = [{"allocation": 1.0, "ticker": "SPY"}]

METADATA_KEYS = (
    "date",
    "last_execution_mode",
    "last_market_close_snapshot",
    "last_successful_cycle_at",
    "post_mortem_run",
)

SHADOW_SEED = 20261115
RICH_SYMPHONIES = 7  # of the 12, in sorted id order: shadow history at or above N_MIN_OBS
RICH_DAYS = 45
THIN_DAYS = 12

GATE_TIMEOUT_SECONDS = 20.0
SETTLE_SECONDS = 0.5
JOIN_SECONDS = 30.0
MEGABYTE = 1_000_000


# ---------------------------------------------------------------------------
# The fixture
# ---------------------------------------------------------------------------


def excerpt() -> dict:
    return json.loads(EXCERPT.read_text(encoding="utf-8"))


def symphony_ids() -> list[str]:
    return sorted(excerpt()["bot_state_current_holdings"])


def metadata_entries() -> dict:
    """The five non-symphony top-level keys of a real bot state (none carries a name)."""
    return {
        "date": FILE_DATE,
        "last_execution_mode": "dry_run",
        "last_market_close_snapshot": {"captured_at_et": "16:05:00 ET", "accounts_map": {}},
        "last_successful_cycle_at": "2026-11-13T21:04:00+00:00",
        "post_mortem_run": FILE_DATE,
    }


def bot_state(
    ids: list[str] | None = None,
    *,
    metadata: bool = False,
    holdings: dict[str, list] | None = None,
    without_holdings: bool = False,
) -> dict:
    """A bot state of the excerpt's symphonies (or ``ids``), holdings from the excerpt."""
    ex = excerpt()
    all_holdings = ex["bot_state_current_holdings"]
    names = ex["bot_state_names"]
    chosen = sorted(all_holdings) if ids is None else list(ids)
    state: dict = {}
    for sid in chosen:
        entry: dict = {
            "name": names.get(sid, "Invented symphony for the added state"),
            "current_return": 0.5,
            "current_value": 1000.0,
        }
        if not without_holdings:
            source = (holdings or {}).get(sid) or all_holdings.get(sid) or ADDED_HOLDINGS
            entry["current_holdings"] = copy.deepcopy(source)
        state[sid] = entry
    if metadata:
        state.update(metadata_entries())
    return state


def history_of(ids: list[str] | None = None) -> dict:
    """The excerpt's replay history (symphony -> date -> ticks) for ``ids``."""
    history = excerpt()["history"]
    if ids is None:
        return history
    return {sid: history[sid] for sid in ids if sid in history}


# --- stated edits -----------------------------------------------------------------------------


def without_symphony(history: dict, sid: str) -> dict:
    return {key: value for key, value in history.items() if key != sid}


def with_empty_ticks(history: dict, sid: str) -> dict:
    edited = copy.deepcopy(history)
    edited[sid] = {day: [] for day in edited[sid]}
    return edited


def with_zero_returns(history: dict, sid: str) -> dict:
    edited = copy.deepcopy(history)
    for ticks in edited[sid].values():
        ticks[-1]["return"] = 0.0
    return edited


def with_one_return_changed(history: dict, sid: str | None = None, delta: float = 0.5) -> dict:
    """The last tick of the newest date of one symphony moved: the replay row of that symphony
    changes, every other symphony's does not."""
    edited = copy.deepcopy(history)
    target = sid if sid is not None else sorted(edited)[0]
    newest = sorted(edited[target])[-1]
    edited[target][newest][-1]["return"] += delta
    return edited


def with_same_length_return_change(history: dict, sid: str | None = None) -> dict:
    """One return changed in its last digit so the file's text keeps its length exactly."""
    edited = copy.deepcopy(history)
    target = sid if sid is not None else sorted(edited)[0]
    tick = edited[target][sorted(edited[target])[-1]][-1]
    text = repr(tick["return"])
    for digit in "123456789":
        candidate = text[:-1] + digit
        if (
            digit != text[-1]
            and float(candidate) != tick["return"]
            and len(repr(float(candidate))) == len(text)
        ):
            tick["return"] = float(candidate)
            return edited
    raise AssertionError(f"no same-length variant of {text}")


def with_dates_trimmed(history: dict, sid: str | None = None, keep: int = 50) -> dict:
    edited = copy.deepcopy(history)
    target = sid if sid is not None else sorted(edited)[0]
    kept = sorted(edited[target])[-keep:]
    edited[target] = {day: edited[target][day] for day in kept}
    return edited


def with_added_symphony(history: dict, new_id: str = ADDED_ID, like: str | None = None) -> dict:
    """The history plus one more symphony: a copy of the real records of ``like``."""
    edited = copy.deepcopy(history)
    edited[new_id] = copy.deepcopy(edited[like if like is not None else sorted(edited)[0]])
    return edited


# ---------------------------------------------------------------------------
# Shadow rows
# ---------------------------------------------------------------------------


def sessions_back(last_day: date, count: int) -> list[str]:
    """``count`` NYSE session dates ending at ``last_day`` (inclusive), newest first."""
    days: list[str] = []
    cursor = last_day
    while len(days) < count:
        if market_calendar.is_trading_day(cursor):
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return days


def _shadow_series() -> dict[str, list[float]]:
    """Per symphony a seeded, lightly persistent daily return list (newest first)."""
    rng = random.Random(SHADOW_SEED)
    out: dict[str, list[float]] = {}
    for sid in symphony_ids():
        previous = 0.0
        values = []
        for _ in range(RICH_DAYS):
            previous = round(0.3 * previous + rng.gauss(0.0, 1.0), 6)
            values.append(previous)
        out[sid] = values
    return out


def seed_shadow(
    *,
    ids: list[str] | None = None,
    stale: tuple[str, ...] = (),
    absent: tuple[str, ...] = (),
    last_session: str = FILE_DATE,
) -> None:
    """Write one end-of-day ``shadow_history`` row per session for each symphony.

    The first ``RICH_SYMPHONIES`` of the sorted ids get ``RICH_DAYS`` sessions (at or above
    ``N_MIN_OBS``), the rest ``THIN_DAYS``. ``stale`` ids stop two sessions before the newest, so
    the shared no-longer-held rule leaves them out; ``absent`` ids get no rows at all.
    """
    assert RICH_DAYS >= gp.N_MIN_OBS > THIN_DAYS
    all_ids = symphony_ids()
    chosen = all_ids if ids is None else [sid for sid in ids if sid in all_ids]
    series = _shadow_series()
    sessions = sessions_back(date.fromisoformat(last_session), RICH_DAYS + 2)
    rows = []
    for sid in chosen:
        if sid in absent:
            continue
        count = RICH_DAYS if all_ids.index(sid) < RICH_SYMPHONIES else THIN_DAYS
        start = 2 if sid in stale else 0
        for offset, day in enumerate(sessions[start : start + count]):
            value = series[sid][offset]
            rows.append(
                (
                    f"{day}T21:00:00+00:00",
                    f"{day} 16:00:00",
                    day,
                    sid,
                    "acct-prl",
                    f"cycle-{day}",
                    value,
                    round(value * 0.9, 6),
                    0,
                    None,
                    None,
                )
            )
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        conn.executemany(
            "INSERT INTO shadow_history "
            "(ts_utc, ts_et, trading_day, symphony_id, account_id, cycle_id, "
            " current_return, shadow_return, is_post_trigger, trigger_id, position_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def delete_recent_shadow(sid: str, after_day: str) -> None:
    """Remove ``sid``'s shadow rows newer than ``after_day`` (it becomes no longer held)."""
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        conn.execute(
            "DELETE FROM shadow_history WHERE symphony_id = ? AND trading_day > ?",
            (sid, after_day),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The clock, the world and its cache files
# ---------------------------------------------------------------------------


def freeze(monkeypatch, instant: datetime = FROZEN_INSTANT) -> None:
    """Pin the ET instant in the two modules that read the clock on this path: ``app`` (the
    no-longer-held rule) and ``autotuner`` (the probe's start date). Calling it again moves the
    clock. ``analytics`` reads the per-test database for the shadow rows."""
    frozen = frozen_datetime_class(instant)
    monkeypatch.setattr(app_module, "datetime", frozen)
    monkeypatch.setattr(autotuner, "datetime", frozen)
    monkeypatch.setattr(analytics, "DB_FILE", os.environ["DB_PATH"])


def nth_trading_day_after(day: str, count: int, hour: int = 20) -> datetime:
    """The ET instant at ``hour``:00 of the ``count``-th NYSE session after ``day``."""
    cursor = date.fromisoformat(day)
    seen = 0
    while seen < count:
        cursor += timedelta(days=1)
        if market_calendar.is_trading_day(cursor):
            seen += 1
    return datetime(cursor.year, cursor.month, cursor.day, hour, 0, tzinfo=ET)


def cache_path(state: dict, day: str) -> str:
    """Where the real key derivation puts the cache file of ``state`` for ``day``."""
    _, _, path = synthetic_history._resolve_history_cache_key(state, day)
    assert path is not None, "the state holds no holdings, so no cache file name resolves"
    return path


def write_history(path: str, history: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(history, handle)


def write_truncated(path: str, history: dict) -> None:
    """The first half of a valid file's bytes: the state the in-place writer leaves mid-write."""
    data = json.dumps(history).encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(data[: len(data) // 2])


def bump_mtime(path: str, seconds: float = 2.0) -> None:
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + int(seconds * 1_000_000_000)))


def link_or_copy(source: str | Path, target: str | Path) -> None:
    """Put ``source`` at ``target`` without copying its bytes where the filesystem allows."""
    with contextlib.suppress(FileNotFoundError):
        os.unlink(target)
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def truncate_copy(source: str | Path, target: str | Path) -> None:
    size = os.path.getsize(source)
    remaining = size // 2
    with open(source, "rb") as src, open(target, "wb") as dst:
        while remaining > 0:
            chunk = src.read(min(remaining, 4 * MEGABYTE))
            if not chunk:
                break
            dst.write(chunk)
            remaining -= len(chunk)


@dataclass
class World:
    monkeypatch: object
    client: object
    state: dict
    history: dict
    now: datetime = FROZEN_INSTANT
    files: dict[str, str] = field(default_factory=dict)

    # -- state -----------------------------------------------------------------
    def save_state(self, state: dict | None = None) -> None:
        if state is not None:
            self.state = state
        database.save_state(self.state)

    def loaded_state(self) -> dict:
        return database.load_state()

    # -- cache files -----------------------------------------------------------
    def path_for(self, day: str, state: dict | None = None) -> str:
        return cache_path(self.loaded_state() if state is None else state, day)

    def write_file(self, day: str, history: dict | None = None, *, state: dict | None = None):
        path = self.path_for(day, state)
        write_history(path, self.history if history is None else history)
        self.files[day] = os.path.abspath(path)
        return self.files[day]

    def write_corrupt(self, day: str, history: dict | None = None, *, state: dict | None = None):
        path = self.path_for(day, state)
        write_truncated(path, self.history if history is None else history)
        self.files[day] = os.path.abspath(path)
        return self.files[day]

    def place_file(self, day: str, source: str | Path, *, state: dict | None = None) -> str:
        path = self.path_for(day, state)
        link_or_copy(source, path)
        self.files[day] = os.path.abspath(path)
        return self.files[day]

    def rewrite_in_place(self, day: str, history: dict, *, keep_mtime_bump: bool = True) -> None:
        path = self.files[day]
        write_history(path, history)
        if keep_mtime_bump:
            bump_mtime(path)

    def delete_file(self, day: str) -> None:
        os.unlink(self.files.pop(day))

    # -- the clock ---------------------------------------------------------------
    def set_clock(self, instant: datetime) -> None:
        self.now = instant
        freeze(self.monkeypatch, instant)

    def advance(self, seconds: float = 60.0) -> None:
        self.set_clock(self.now + timedelta(seconds=seconds))

    # -- requests ----------------------------------------------------------------
    def get(self):
        return self.client.get(ROUTE)

    def body(self) -> dict:
        resp = self.get()
        assert resp.status_code == 200, f"{ROUTE} answered {resp.status_code}"
        return resp.get_json()


def standard(
    monkeypatch,
    *,
    ids: list[str] | None = None,
    metadata: bool = False,
    stale: tuple[str, ...] = (),
    absent: tuple[str, ...] = (),
    file: bool = True,
    file_history: dict | None = None,
    instant: datetime = FROZEN_INSTANT,
) -> World:
    """The excerpt world: the clock frozen, the state saved, the shadow rows seeded and, unless
    ``file`` is false, the excerpt's history written at the real key for ``FILE_DATE``."""
    freeze(monkeypatch, instant)
    app_module.app.config["TESTING"] = True
    # tests/ui stubs database.load_state for every test; this world reads the real one. Bound
    # directly, never through monkeypatch: monkeypatch would save the stub as the value to
    # restore and write it back after the stub's own undo, leaving it on the module.
    database.load_state = _REAL_LOAD_STATE
    state = bot_state(ids, metadata=metadata)
    history = history_of(ids) if file_history is None else file_history
    world = World(monkeypatch, app_module.app.test_client(), state, history, now=instant)
    world.save_state()
    seed_shadow(ids=ids, stale=stale, absent=absent)
    if file:
        world.write_file(FILE_DATE)
    return world


@pytest.fixture
def chdir_to_tmp(tmp_path, monkeypatch):
    """A per-test ``cache`` directory: the cache dir is relative to the working directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def restart_daemon() -> None:
    """Forget everything the route kept in this process, as a daemon restart does."""
    store = getattr(autotuner, "_IF_HELD_REPLAY_STORE", None)
    if store is not None:
        store.clear()


# ---------------------------------------------------------------------------
# Expected rows, computed from the history with the math layer (never typed)
# ---------------------------------------------------------------------------


def _json_safe(value):
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return value


def degraded_row(source: str) -> dict:
    return {
        "rho": None,
        "rho_ci": None,
        "sharpe_daily": None,
        "n_obs": 0,
        "verdict": "INSUFFICIENT_DATA",
        "sample_source": source,
    }


def replay_rows(history: dict, ids) -> dict[str, dict]:
    """The replay row each id gets from a cache file holding ``history``."""
    rows = {}
    for sid in ids:
        series = autotuner.collect_if_held_daily_returns(history, sid)
        if not series:
            rows[sid] = degraded_row("if_held_replay")
            continue
        stats = gp.compute_persistence_stats(series)
        rows[sid] = {
            "rho": _json_safe(stats.rho),
            "rho_ci": _json_safe(stats.rho_ci),
            "sharpe_daily": _json_safe(stats.sharpe_daily),
            "n_obs": stats.n_obs,
            "verdict": gp.classify_stop_justification(stats),
            "sample_source": "if_held_replay",
        }
    return rows


def replay_of(body: dict) -> dict[str, dict]:
    return {sid: entry["replay"] for sid, entry in body["symphonies"].items()}


def shadow_of(body: dict) -> dict[str, dict]:
    return {sid: entry["shadow"] for sid, entry in body["symphonies"].items()}


def all_degraded(body: dict) -> bool:
    return all(row == degraded_row("if_held_replay") for row in replay_of(body).values())


# ---------------------------------------------------------------------------
# Goldens
# ---------------------------------------------------------------------------


def normalize_newlines(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def golden(name: str) -> bytes:
    """The base route's body for state ``name``; ``core.autocrlf`` may have rewritten the file."""
    return normalize_newlines((GOLDENS / f"{name}.json").read_bytes())


def _raise_load_state():
    raise RuntimeError("database.load_state failed (a state of the golden set)")


def golden_world(name: str, monkeypatch) -> World:
    """The world of golden ``name`` (the capture script and the tests build it the same way)."""
    ids = symphony_ids()
    last, second = ids[-1], ids[1]
    if name in ("full", "full_plus_60s"):
        return standard(monkeypatch)
    if name == "no_file":
        return standard(monkeypatch, file=False)
    if name == "file_lacks_one":
        return standard(monkeypatch, file_history=without_symphony(history_of(), last))
    if name == "empty_ticks":
        return standard(monkeypatch, file_history=with_empty_ticks(history_of(), last))
    if name == "zero_variance":
        return standard(monkeypatch, file_history=with_zero_returns(history_of(), last))
    if name == "file_has_extra":
        # The state lacks the last symphony; the file at that smaller state's key still holds it.
        world = standard(monkeypatch, ids=ids[:-1], file=False, file_history=history_of())
        seed_shadow(ids=[last])
        world.write_file(FILE_DATE)
        return world
    if name == "no_shadow":
        return standard(monkeypatch, absent=(second,))
    if name == "load_state_raises":
        world = standard(monkeypatch)
        monkeypatch.setattr(database, "load_state", _raise_load_state)
        return world
    if name == "load_state_empty":
        world = standard(monkeypatch)
        monkeypatch.setattr(database, "load_state", lambda: {})
        return world
    if name == "shadow_raises":
        world = standard(monkeypatch)
        real = analytics.get_shadow_current_return_daily_series

        def failing(symphony_id, db_file):
            if symphony_id == second:
                raise RuntimeError("shadow read failed (a state of the golden set)")
            return real(symphony_id, db_file)

        monkeypatch.setattr(analytics, "get_shadow_current_return_daily_series", failing)
        return world
    if name == "metadata_and_stale":
        return standard(monkeypatch, metadata=True, stale=(second,))
    if name == "symphony_added":
        world = standard(monkeypatch, file=False)
        grown = bot_state([*ids, ADDED_ID], holdings={ADDED_ID: ADDED_HOLDINGS})
        world.save_state(grown)
        world.history = with_added_symphony(history_of())
        world.write_file(FILE_DATE)
        return world
    if name == "corrupt_newest_valid_older":
        world = standard(monkeypatch, file=False)
        world.write_file(OLDER_DATE)
        world.write_corrupt(FILE_DATE)
        return world
    if name == "only_corrupt":
        world = standard(monkeypatch, file=False)
        world.write_corrupt(FILE_DATE)
        return world
    raise KeyError(name)


GOLDEN_NAMES = (
    "full",
    "full_plus_60s",
    "no_file",
    "file_lacks_one",
    "empty_ticks",
    "zero_variance",
    "file_has_extra",
    "no_shadow",
    "load_state_raises",
    "load_state_empty",
    "shadow_raises",
    "metadata_and_stale",
    "symphony_added",
    "corrupt_newest_valid_older",
    "only_corrupt",
)


# ---------------------------------------------------------------------------
# Counting decodes, and holding or failing one
# ---------------------------------------------------------------------------


class Gate:
    """Holds one decode until the test releases it; ``timed_out`` records a release never given."""

    def __init__(
        self, *, after_read: bool = False, fail: bool | type[BaseException] = False
    ) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.after_read = after_read
        # True raises RuntimeError after the release; an exception class raises that instead.
        self.fail = fail
        self.owner: int | None = None
        self.timed_out = False

    def hold(self) -> None:
        self.owner = threading.get_ident()
        self.entered.set()
        if not self.release.wait(GATE_TIMEOUT_SECONDS):
            self.timed_out = True


class DecodeCounter:
    """Counts every read of a cache file.

    ``reads`` counts ``json.load`` calls on a cache file (a read that bypassed the loader would
    still show here); ``loader_calls`` counts ``synthetic_history.load_cached_history`` calls.
    Both hold absolute paths. The wrappers call the real functions.
    """

    def __init__(self, real_loader, real_json_load) -> None:
        self._real_loader = real_loader
        self._real_json_load = real_json_load
        self._lock = threading.Lock()
        self.reads: list[str] = []
        self.loader_calls: list[str] = []
        self._gate: Gate | None = None

    # -- the wrappers --------------------------------------------------------------
    def json_load(self, fp, *args, **kwargs):
        name = getattr(fp, "name", None)
        if isinstance(name, str) and CACHE_MARKER in name:
            with self._lock:
                self.reads.append(os.path.abspath(name))
        return self._real_json_load(fp, *args, **kwargs)

    def loader(self, cache_file, *args, **kwargs):
        with self._lock:
            self.loader_calls.append(os.path.abspath(cache_file))
            gate, self._gate = self._gate, None
        if gate is not None and not gate.after_read:
            gate.hold()
        if gate is not None and gate.fail:
            fault = gate.fail if isinstance(gate.fail, type) else RuntimeError
            raise fault("simulated fault inside the decode")
        result = self._real_loader(cache_file, *args, **kwargs)
        if gate is not None and gate.after_read:
            gate.hold()
        return result

    # -- the test's view -----------------------------------------------------------
    def hold_first_decode(
        self, *, after_read: bool = False, fail: bool | type[BaseException] = False
    ) -> Gate:
        gate = Gate(after_read=after_read, fail=fail)
        with self._lock:
            self._gate = gate
        return gate

    def mark(self) -> tuple[int, int]:
        with self._lock:
            return len(self.reads), len(self.loader_calls)

    def reads_since(self, mark: tuple[int, int]) -> int:
        return len(self.reads) - mark[0]

    def loader_since(self, mark: tuple[int, int]) -> int:
        return len(self.loader_calls) - mark[1]

    def reads_of(self, path: str, mark: tuple[int, int] = (0, 0)) -> int:
        return Counter(self.reads[mark[0] :])[os.path.abspath(path)]

    def read_paths_since(self, mark: tuple[int, int]) -> Counter:
        return Counter(self.reads[mark[0] :])


def install_counter(monkeypatch) -> DecodeCounter:
    counter = DecodeCounter(synthetic_history.load_cached_history, json.load)
    monkeypatch.setattr(synthetic_history, "load_cached_history", counter.loader)
    monkeypatch.setattr(json, "load", counter.json_load)
    return counter


def assert_decodes(counter: DecodeCounter, mark: tuple[int, int], expected: int, what: str) -> None:
    """Both counters agree with ``expected``: the loader seam and the raw ``json.load`` seam."""
    reads, loads = counter.reads_since(mark), counter.loader_since(mark)
    start = len(CACHE_MARKER)  # the file's date: the part of its name that tells two files apart
    paths = {
        os.path.basename(p)[start : start + 10]: n
        for p, n in counter.read_paths_since(mark).items()
    }
    assert reads == expected, (
        f"{what}: {reads} cache-file decodes (json.load), expected {expected}: {paths}"
    )
    assert loads == expected, f"{what}: {loads} load_cached_history calls, expected {expected}"


class Arrivals:
    """Which threads have reached a cache file's existence check (the probe's unchanged step)."""

    def __init__(self, real_exists) -> None:
        self._real_exists = real_exists
        self._lock = threading.Lock()
        self.threads: set[int] = set()

    def exists(self, path):
        if isinstance(path, (str, os.PathLike)) and CACHE_MARKER in os.fspath(path):
            with self._lock:
                self.threads.add(threading.get_ident())
        return self._real_exists(path)

    def reset(self) -> None:
        with self._lock:
            self.threads.clear()

    def wait_for_threads_besides(self, owner: int | None, count: int, timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                others = self.threads - {owner}
            if len(others) >= count:
                return True
            time.sleep(0.01)
        return False


def watch_arrivals(monkeypatch) -> Arrivals:
    arrivals = Arrivals(os.path.exists)
    monkeypatch.setattr(os.path, "exists", arrivals.exists)
    return arrivals


class NetworkSpy:
    """Calls that could reach Alpaca: both fetchers of ``synthetic_history`` and any socket
    connect. Each records and then raises, so a call fails the request it came from."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def install(self, monkeypatch) -> None:
        import socket

        def record(name):
            def spy(*args, **kwargs):
                self.calls.append(name)
                raise RuntimeError(f"{name} called: a replay read must never fetch")

            return spy

        monkeypatch.setattr(synthetic_history, "fetch_bars", record("fetch_bars"))
        monkeypatch.setattr(
            synthetic_history, "generate_synthetic_history", record("generate_synthetic_history")
        )
        real_connect = socket.socket.connect

        def connect(sock, address, *args, **kwargs):
            host = address[0] if isinstance(address, tuple) else str(address)
            if host not in ("127.0.0.1", "localhost", "::1"):
                self.calls.append(f"socket.connect({host})")
                raise OSError("outbound connect refused by the test")
            return real_connect(sock, address, *args, **kwargs)

        monkeypatch.setattr(socket.socket, "connect", connect)


# ---------------------------------------------------------------------------
# The real threaded server
# ---------------------------------------------------------------------------


@pytest.fixture
def server():
    """The app on a real threaded loopback server, proved to answer before a test uses it."""
    with running_server() as running:
        host = running.base_url.removeprefix("http://")
        conn = http.client.HTTPConnection(host, timeout=30)
        try:
            conn.request("GET", "/api/csrf-token")
            assert conn.getresponse().status == 200, "the test server does not answer"
        finally:
            conn.close()
        yield running


@dataclass
class Fetched:
    status: int
    data: bytes
    started: float
    finished: float

    @property
    def seconds(self) -> float:
        return self.finished - self.started


def fetch(server, path: str = ROUTE, timeout: float = 120.0) -> Fetched:
    host = server.base_url.removeprefix("http://")
    conn = http.client.HTTPConnection(host, timeout=timeout)
    started = time.perf_counter()
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        data = resp.read()
        status = resp.status
    finally:
        conn.close()
    return Fetched(status, data, started, time.perf_counter())


class Request(threading.Thread):
    """One GET on its own thread; ``wait_to_start`` lines several up behind a barrier."""

    def __init__(self, server, path: str = ROUTE, barrier: threading.Barrier | None = None):
        super().__init__(daemon=True)
        self._server, self._path, self._barrier = server, path, barrier
        self.fetched: Fetched | None = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            if self._barrier is not None:
                self._barrier.wait(timeout=GATE_TIMEOUT_SECONDS)
            self.fetched = fetch(self._server, self._path)
        except BaseException as exc:  # noqa: BLE001 -- the test reads it from `error`
            self.error = exc

    @property
    def finished(self) -> bool:
        return self.fetched is not None or self.error is not None


class ClientRequest(threading.Thread):
    """One GET through the Flask test client on its own thread. Unlike a server thread it hands
    back any exception, a ``BaseException`` included, in ``error``."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.response = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.response = app_module.app.test_client().get(ROUTE)
        except BaseException as exc:  # noqa: BLE001 -- the test reads it from `error`
            self.error = exc

    @property
    def finished(self) -> bool:
        return self.response is not None or self.error is not None


def join_all(requests: list, timeout: float = JOIN_SECONDS, *, allow_errors: bool = False) -> None:
    deadline = time.monotonic() + timeout
    for request in requests:
        request.join(max(0.0, deadline - time.monotonic()))
    hung = [r for r in requests if r.is_alive()]
    assert not hung, f"{len(hung)} request(s) still running after {timeout} s: a hang"
    if not allow_errors:
        for request in requests:
            assert request.error is None, f"a request raised: {request.error!r}"


class _FailsOnFirstRead:
    """A file object whose first ``read`` raises ``OSError`` (an I/O error partway through)."""

    def __init__(self, handle) -> None:
        self._handle = handle
        self._failed = False

    def read(self, *args, **kwargs):
        if not self._failed:
            self._failed = True
            raise OSError(5, "Input/output error")
        return self._handle.read(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._handle, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return self._handle.__exit__(*exc_info)

    def __iter__(self):
        return iter(self._handle)


class Unreadable:
    """One file that cannot be read: an ``OSError`` on read-mode opens of its path, whatever the
    platform (a chmod does not make a file unreadable on Windows). ``at="open"`` raises
    ``PermissionError`` when the file is opened, ``at="read"`` opens it and raises an I/O error
    on the first read. ``times`` limits how many opens fail (None: every one, until
    ``make_readable``); an open after the budget is spent is an ordinary one, so the error has
    "cleared" for any later attempt. The file's size and modification time never change."""

    def __init__(self, path: str, real_open, *, at: str = "open", times: int | None = None):
        self._target = os.path.abspath(path)
        self._real_open = real_open
        self._at = at
        self._remaining = times
        self.active = True
        self.attempts = 0

    def __call__(self, file, mode="r", *args, **kwargs):
        if self.active and isinstance(file, (str, os.PathLike)) and "r" in mode:
            budget_left = self._remaining is None or self._remaining > 0
            if budget_left and os.path.abspath(os.fspath(file)) == self._target:
                self.attempts += 1
                if self._remaining is not None:
                    self._remaining -= 1
                if self._at == "open":
                    raise PermissionError(13, "Permission denied", os.fspath(file))
                return _FailsOnFirstRead(self._real_open(file, mode, *args, **kwargs))
        return self._real_open(file, mode, *args, **kwargs)

    def make_readable(self) -> None:
        self.active = False


def make_unreadable(
    monkeypatch, path: str, *, at: str = "open", times: int | None = None
) -> Unreadable:
    import builtins

    unreadable = Unreadable(path, builtins.open, at=at, times=times)
    monkeypatch.setattr(builtins, "open", unreadable)
    return unreadable


def run_together(server, paths: list[str]) -> list[Request]:
    """Start one GET per path behind a barrier and wait for all of them."""
    barrier = threading.Barrier(len(paths))
    requests = [Request(server, path, barrier) for path in paths]
    for request in requests:
        request.start()
    join_all(requests)
    return requests


# ---------------------------------------------------------------------------
# Timing and size helpers
# ---------------------------------------------------------------------------


def median_of(count: int, run) -> float:
    return statistics.median(run() for _ in range(count))


def time_decode(path: str | Path) -> float:
    """One decode of ``path`` alone: open and ``json.load``, a failed parse included."""
    started = time.perf_counter()
    try:
        with open(path, encoding="utf-8") as handle:
            json.load(handle)
    except ValueError:
        pass
    return time.perf_counter() - started


def generate_file(path: str | Path, *, target_bytes: int | None = None, ticks_per_date: int = 10):
    """Write a cache file of the excerpt's real tick records, replicated to the recorded shape.

    12 symphonies, 250 dates each (a date's record is the excerpt's real record of the same
    symphony, taken in turn, so the last tick, the one the route reads, is always real). Padding
    ticks repeat the record's first tick. ``target_bytes`` picks the tick count per date that
    reaches that size (re-written with a corrected count until it does). The file is streamed,
    so the parsed history is never built here.
    """
    if target_bytes is None:
        return _write_generated(path, ticks_per_date)
    ticks_per_date = 2
    for _ in range(4):
        size = _write_generated(path, ticks_per_date)
        if size >= target_bytes:
            assert ticks_per_date <= 456, "beyond the recorded 1 to 456 ticks per date"
            return size
        scaled = math.ceil(ticks_per_date * target_bytes / size * 1.02)
        ticks_per_date = max(ticks_per_date + 1, scaled)
    raise AssertionError(f"no tick count reached {target_bytes} bytes (last: {ticks_per_date})")


def _write_generated(path: str | Path, ticks_per_date: int) -> int:
    hist = excerpt()["history"]
    sessions = sessions_back(date.fromisoformat(FILE_DATE), 250)[::-1]
    sids = sorted(hist)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{")
        for i, sid in enumerate(sids):
            real_days = sorted(hist[sid])
            handle.write(("" if i == 0 else ", ") + json.dumps(sid) + ": {")
            for j, day in enumerate(sessions):
                ticks = hist[sid][real_days[j % len(real_days)]]
                padding = [json.dumps(ticks[0])] * (ticks_per_date - 1)
                tick_list = "[" + ", ".join([*padding, json.dumps(ticks[-1])]) + "]"
                handle.write(("" if j == 0 else ", ") + json.dumps(day) + ": " + tick_list)
            handle.write("}")
        handle.write("}")
    return os.path.getsize(path)
