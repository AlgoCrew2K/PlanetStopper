"""Clocked worlds for the dated-stamp, close-snapshot and account-totals tests.

Builds on `_refresh_timing_state_world` (same fixture records, same real temp database) and
adds what the dated stamps and the after-close figures need:

  * a clock that moves between requests, with the real XNYS calendar deciding the market
    state (the state world forces it instead);
  * close snapshots derived from the signed-off R8 record (one named field changed per
    variant, see `tests/fixtures/refresh_timing/01-c3/PROVENANCE.md`);
  * engine cycles re-stamped to a chosen ET time (derived from the captured R6 rows: only
    `ts_et`, `ts_utc` and `cycle_id` change);
  * the Composer account-totals endpoint, driven through the real `_refresh_account_totals`
    job, so a latch or cache fault shows up exactly as it would in the daemon.

Nothing here mocks `analytics` or `database`. Expected strings are built from the inputs a
test passes in, never from a response.
"""

from __future__ import annotations

import copy
import html
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import analytics as analytics_module
import app as app_module
import database as database_module
import market_calendar
from tests.app._refresh_timing_state_world import (
    COMPOSER_TOTAL_STATS,
    ET,
    TRIGGERED_ID,
    UNTRIGGERED_ID,
    World,
    _pinned_window_cutoff,
    build_world,
    frozen_datetime_class,
)

UTC = ZoneInfo("UTC")

# The day the captured rows and the signed-off snapshot record belong to, and the session before.
CAPTURE_DAY = "2026-10-21"
PREVIOUS_DAY = "2026-10-20"

PREVIOUS_SESSION_SUFFIX = " · previous session"
NO_STAMP = "--"


def et(day: str, clock: str) -> datetime:
    """An aware ET instant from `YYYY-MM-DD` and `HH:MM[:SS[.ffffff]]`."""
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=ET)


def stamp_text(moment: datetime, *, seconds: bool = False) -> str:
    """The server stamp for an instant, spelled out independently of the code under test."""
    local = moment.astimezone(ET)
    return local.strftime("%Y-%m-%d %H:%M:%S ET" if seconds else "%Y-%m-%d %H:%M ET")


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


def set_clock(monkeypatch, instant: datetime, *, real_calendar: bool = True) -> None:
    """Move the routes' clock; `real_calendar` lets the XNYS calendar decide the market state."""
    monkeypatch.setattr(app_module, "datetime", frozen_datetime_class(instant))
    if real_calendar:
        monkeypatch.setattr(app_module, "get_market_state", market_calendar.get_market_state)
    monkeypatch.setattr(
        analytics_module, "_window_cutoff_date", _pinned_window_cutoff(instant.date())
    )


# ---------------------------------------------------------------------------
# Derived records (named in tests/fixtures/refresh_timing/01-c3/PROVENANCE.md)
# ---------------------------------------------------------------------------


def snapshot_variant(
    base: dict,
    *,
    trading_day: str | None = None,
    captured_at_et: str | None = None,
    drop_trading_day: bool = False,
) -> dict:
    """R8 with only the named fields changed.

    `captured_at_et` is "HH:MM:SS ET"; the record's own `data_as_of` is kept in step
    ("HH:MM ET") so the stored stamp cannot disagree with the stored capture time.
    """
    snapshot = copy.deepcopy(base)
    if trading_day is not None:
        snapshot["trading_day"] = trading_day
    if drop_trading_day:
        snapshot.pop("trading_day", None)
    if captured_at_et is not None:
        snapshot["captured_at_et"] = captured_at_et
        snapshot["data_as_of"] = captured_at_et.rsplit(":", 1)[0] + " ET"
    return snapshot


def retime_rows(rows: list[dict], *, day: str, clock: str, cycle_id: str) -> list[dict]:
    """Shadow rows with only `trading_day`, `ts_et`, `ts_utc` and `cycle_id` changed.

    The writer stamps `ts_utc` a few seconds after `ts_et` (the captured rows show 3 s);
    the same gap is kept so the two stamps stay a plausible pair.
    """
    moment = et(day, clock)
    written = moment + timedelta(seconds=3)
    out = []
    for row in rows:
        new = dict(row)
        new["trading_day"] = day
        new["ts_et"] = moment.strftime("%Y-%m-%dT%H:%M:%S")
        new["ts_utc"] = written.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        new["cycle_id"] = cycle_id
        out.append(new)
    return out


def cycle_stamp(day: str, clock: str) -> str:
    """The `last_successful_cycle_at` the engine saves for a cycle that ran at `day clock` ET."""
    return et(day, clock).replace(microsecond=413198).isoformat()


def observation_moment(row: dict) -> datetime:
    """The aware instant of a shadow row's `ts_utc`."""
    return datetime.fromisoformat(row["ts_utc"].replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_KEEP = object()


def save_state(
    world: World,
    label: str,
    *,
    snapshot: dict | None = None,
    stamp=_KEEP,
    entries_from: str | None = None,
    entry_overrides: dict[str, dict] | None = None,
) -> None:
    """Save `bot_state` as the engine does at the end of cycle `label`, optionally with a close
    snapshot, with the cycle stamp replaced (`stamp=None` drops it) and named fields of
    named symphony entries replaced (`entry_overrides`, for a live state that has moved on)."""
    live = world._records["live"]
    state: dict = dict(live["reserved"])
    state.update(json.loads(json.dumps(live[entries_from or label]["entries"])))
    for sym_id, fields in (entry_overrides or {}).items():
        state[sym_id].update(fields)
    if stamp is _KEEP:
        stamp = live[label]["last_successful_cycle_at"]
    if stamp is not None:
        state["last_successful_cycle_at"] = stamp
    if snapshot is not None:
        state["last_market_close_snapshot"] = copy.deepcopy(snapshot)
    database_module.save_state(state)


def base_snapshot(world: World) -> dict:
    return copy.deepcopy(world._records["snapshot"])


# ---------------------------------------------------------------------------
# Worlds
# ---------------------------------------------------------------------------


def clocked_world(monkeypatch, tmp_path, instant: datetime) -> World:
    """A bare world (no rows, no state) at `instant`, market state from the real calendar."""
    world = build_world(monkeypatch, tmp_path, "closed_frozen")
    set_clock(monkeypatch, instant)
    return world


def snapshot_world(
    monkeypatch,
    tmp_path,
    instant: datetime,
    *,
    trading_day: str = CAPTURE_DAY,
    captured_at_et: str = "16:00:42 ET",
    drop_trading_day: bool = False,
) -> World:
    """The seeded rows of both captured cycles, live state at the newer cycle, and a close
    snapshot of `trading_day` captured at `captured_at_et`, served at `instant`."""
    world = clocked_world(monkeypatch, tmp_path, instant)
    world.seed_rows(world.previous_day_rows())
    world.seed_cycle("m_minus_1")
    world.seed_cycle("m")
    snapshot = snapshot_variant(
        base_snapshot(world),
        trading_day=trading_day,
        captured_at_et=captured_at_et,
        drop_trading_day=drop_trading_day,
    )
    save_state(world, "m", snapshot=snapshot)
    return world


def midday_world(
    monkeypatch,
    tmp_path,
    instant: datetime,
    *,
    newest_cycle_clock: str = "10:43:05",
    state_stamp=_KEEP,
    cycles: tuple[str, ...] = ("m_minus_1", "m"),
    state_label: str = "m",
    previous_day_clock: str | None = None,
) -> tuple[World, dict[str, list[dict]]]:
    """Open-market world: the captured cycles re-stamped to run one minute apart ending at
    `newest_cycle_clock` on the capture day (default 10:43:05), live state at `state_label`.

    Returns the world and the seeded rows by cycle label (the stamps the test expects come
    from these rows). `cycles` selects which of the two cycles' rows reach the database.
    """
    world = clocked_world(monkeypatch, tmp_path, instant)
    newest = et(CAPTURE_DAY, newest_cycle_clock)
    stamps = {
        "m": newest,
        "m_minus_1": newest - timedelta(minutes=1),
    }
    seeded: dict[str, list[dict]] = {}
    previous = world.previous_day_rows()
    if previous_day_clock is not None:
        previous = retime_rows(
            previous, day=PREVIOUS_DAY, clock=previous_day_clock, cycle_id="20261020_1552"
        )
    world.seed_rows(previous)
    seeded["previous_day"] = previous
    for label in cycles:
        when = stamps[label]
        rows = retime_rows(
            world.cycle_rows(label),
            day=CAPTURE_DAY,
            clock=when.strftime("%H:%M:%S"),
            cycle_id=when.strftime("%Y%m%d_%H%M"),
        )
        world.seed_rows(rows)
        seeded[label] = rows
    if state_stamp is _KEEP:
        # The engine saves the stamp at the end of its cycle, in the second of the rows.
        state_stamp = cycle_stamp(CAPTURE_DAY, stamps[state_label].strftime("%H:%M:%S"))
    save_state(world, state_label, stamp=state_stamp)
    return world, seeded


# The cycle whose rows are written before the close snapshot, and the two cycles whose rows
# the engine writes after it (the captured 16:03 and 16:04 cycles).
CLOSE_CYCLE_CLOCK = "16:00:30"
CAPTURE_CLOCK = "16:00:42"


def close_world(
    monkeypatch,
    tmp_path,
    instant: datetime,
    *,
    captured_at_et: str = f"{CAPTURE_CLOCK} ET",
    trading_day: str = CAPTURE_DAY,
    drop_trading_day: bool = False,
    after_close_cycles: tuple[str, ...] = (),
) -> tuple[World, list[dict]]:
    """The state at the end of the trading day, as the droplet holds it after the close.

    Previous-day rows, the close cycle's rows (the captured earlier cycle re-stamped to
    16:00:30, before the capture), the close snapshot captured at `captured_at_et`, and live
    `bot_state` at the later captured cycle (stamp 16:04:01, as the engine keeps stamping
    after the close). `after_close_cycles` seeds the captured 16:03 (`m_minus_1`) and 16:04
    (`m`) cycles too; they are stamped after the capture. Returns the world and the close
    cycle's rows.
    """
    world = clocked_world(monkeypatch, tmp_path, instant)
    world.seed_rows(world.previous_day_rows())
    close_rows = retime_rows(
        world.cycle_rows("m_minus_1"),
        day=CAPTURE_DAY,
        clock=CLOSE_CYCLE_CLOCK,
        cycle_id="20261021_1600",
    )
    world.seed_rows(close_rows)
    for label in after_close_cycles:
        world.seed_cycle(label)
    snapshot = snapshot_variant(
        base_snapshot(world),
        trading_day=trading_day,
        captured_at_et=captured_at_et,
        drop_trading_day=drop_trading_day,
    )
    save_state(world, "m", snapshot=snapshot)
    return world, close_rows


def seed_after_close_cycles(world: World) -> list[dict]:
    """Write the engine's two post-close cycles (captured 16:03 and a moved 16:04) now."""
    rows = world.cycle_rows("m_minus_1") + world.cycle_rows("m")
    world.seed_rows(rows)
    return rows


# ---------------------------------------------------------------------------
# Composer account totals, driven through the real refresh job
# ---------------------------------------------------------------------------


def composer_body(version: int = 0) -> dict:
    """The captured total-stats body; `version` > 0 moves the figures a refresh reads."""
    body = json.loads(COMPOSER_TOTAL_STATS.read_text(encoding="utf-8"))
    body["portfolio_value"] = body["portfolio_value"] + 10.0 * version
    body["simple_return"] = body["simple_return"] + 0.005 * version
    body["todays_percent_change"] = body["todays_percent_change"] + 0.0005 * version
    return body


def expected_account_value(version: int) -> float:
    return round(composer_body(version)["portfolio_value"], 2)


def expected_all_time_cr(version: int) -> float:
    return composer_body(version)["simple_return"] * 100.0


class _Response:
    def __init__(self, status_code: int, body: dict | None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        assert self._body is not None
        return self._body


@dataclass
class Composer:
    """The total-stats endpoint: `mode` is ok, http_503, timeout or connection_error."""

    version: int = 0
    mode: str = "ok"
    calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, *_args, **_kwargs):
        import requests

        with self._lock:
            self.calls += 1
        if self.mode == "timeout":
            raise requests.exceptions.ReadTimeout("read timed out")
        if self.mode == "connection_error":
            raise requests.exceptions.ConnectionError("refused")
        if self.mode == "http_503":
            return _Response(503, None)
        return _Response(200, composer_body(self.version))


def install_composer(monkeypatch, *, version: int = 0, mode: str = "ok") -> Composer:
    composer = Composer(version=version, mode=mode)
    monkeypatch.setattr(app_module.requests, "get", composer.get)
    return composer


def clean_account_totals(monkeypatch) -> None:
    """Start with no account totals at all and no latched close totals: every figure the
    test sees comes from a refresh it drives."""
    app_module._account_totals_cache.clear()
    monkeypatch.setattr(app_module, "_account_totals_last_good", {})
    monkeypatch.setattr(app_module, "_account_totals_last_success_at", None)
    monkeypatch.setattr(app_module, "_account_totals_last_success_mono", None)
    # The close-totals latch is module state of the fix; absent before it exists.
    monkeypatch.setattr(app_module, "_frozen_account_totals", None, raising=False)


class Monotonic:
    """Controllable replacement for `app._monotonic` (the stale-chip grace age)."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def install_monotonic(monkeypatch) -> Monotonic:
    clock = Monotonic()
    monkeypatch.setattr(app_module, "_monotonic", clock)
    return clock


def refresh_at(monkeypatch, instant: datetime) -> None:
    """Run the real account-totals refresh job with the clock at `instant`."""
    set_clock(monkeypatch, instant)
    app_module._refresh_account_totals()


# ---------------------------------------------------------------------------
# Reading responses
# ---------------------------------------------------------------------------

ACCOUNT_FIGURE_KEYS = ("today_change", "cumulative_return", "account_value", "account_all_time_cr")


def account_figures(body: dict) -> dict:
    """The strip figures that rest on the account totals."""
    strip = body["portfolio_strip"]
    return {key: strip.get(key) for key in ACCOUNT_FIGURE_KEYS}


def all_stamps(body: dict) -> dict[str, object]:
    """Every freshness stamp of an `/api/state` response."""
    return {
        "data_as_of": body.get("data_as_of"),
        "meta.portfolio.data_as_of": ((body.get("meta") or {}).get("portfolio") or {}).get(
            "data_as_of"
        ),
        "portfolio_strip.data_as_of": (body.get("portfolio_strip") or {}).get("data_as_of"),
    }


def legs_of_entries(body: dict) -> dict:
    """The `_tc`, `_cr` and `_mdd` legs the response carries for each served symphony."""
    return {
        sym_id: {key: entry.get(key) for key in ("_tc", "_cr", "_mdd")}
        for sym_id, entry in (body.get("bot_state") or {}).items()
        if isinstance(entry, dict) and "name" in entry
    }


SYMPHONY_IDS = (TRIGGERED_ID, UNTRIGGERED_ID)


# ---------------------------------------------------------------------------
# Moving the stored state on, and comparing responses
# ---------------------------------------------------------------------------


def stored_snapshot() -> dict | None:
    snapshot = database_module.load_state().get("last_market_close_snapshot")
    return None if snapshot is None else copy.deepcopy(snapshot)


def drift_live_state(world: World) -> None:
    """Re-save live `bot_state` as the engine keeps writing it after the close: the close
    snapshot stays, while every symphony's value and return move to new numbers."""
    live_entries = world._records["live"]["m"]["entries"]
    overrides = {
        sym_id: {
            "current_value": live_entries[sym_id]["current_value"] * factor,
            "current_return": live_entries[sym_id]["current_return"] + shift,
        }
        for sym_id, factor, shift in ((TRIGGERED_ID, 4.0, 1.5), (UNTRIGGERED_ID, 0.25, -1.5))
    }
    save_state(world, "m", snapshot=stored_snapshot(), entry_overrides=overrides)


def store_snapshot(world: World, snapshot: dict) -> None:
    """The close step stores a (new) snapshot; live state is saved again with it."""
    save_state(world, "m", snapshot=snapshot)


def flatten(value, prefix: str = "") -> dict:
    out: dict = {}
    if isinstance(value, dict):
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            out.update(flatten(item, f"{prefix}[{index}]"))
    else:
        out[prefix] = value
    return out


def differences(before: dict, after: dict) -> dict[str, tuple]:
    """Every leaf path whose value differs between two responses, as (before, after)."""
    flat_before, flat_after = flatten(before), flatten(after)
    return {
        path: (flat_before.get(path), flat_after.get(path))
        for path in sorted(set(flat_before) | set(flat_after))
        if flat_before.get(path) != flat_after.get(path)
    }


def describe(changed: dict[str, tuple], limit: int = 12) -> str:
    shown = list(changed.items())[:limit]
    more = "" if len(changed) <= limit else f" (+{len(changed) - limit} more)"
    return "; ".join(f"{path}: {a!r} -> {b!r}" for path, (a, b) in shown) + more


_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b.*?</\1>", flags=re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def visible_text(page: str) -> str:
    """The words and numbers on a page: no markup, scripts or styles, spacing collapsed."""
    without_code = _SCRIPT_OR_STYLE.sub(" ", page)
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", without_code))).strip()


class StateReads:
    def __init__(self) -> None:
        self.count = 0


def count_state_reads(monkeypatch) -> StateReads:
    """Count calls to `database.load_state` (the real one still runs)."""
    reads = StateReads()
    real = database_module.load_state

    def counting(*args, **kwargs):
        reads.count += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(database_module, "load_state", counting)
    return reads


def engine_cycle_after_close(world: World, day: str, clock: str) -> None:
    """One more engine cycle at `day clock` ET: its rows are written, then live `bot_state` is
    saved with that cycle's stamp, the close snapshot untouched (as the engine does overnight)."""
    rows = retime_rows(
        world.cycle_rows("m"), day=day, clock=clock, cycle_id=f"{day.replace('-', '')}_{clock[:5]}"
    )
    world.seed_rows(rows)
    save_state(world, "m", snapshot=stored_snapshot(), stamp=cycle_stamp(day, clock))
