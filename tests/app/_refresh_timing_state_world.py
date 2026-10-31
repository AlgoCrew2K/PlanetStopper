"""Seeded world for the `/api/state` and `/` consistency tests.

Builds one deterministic world out of the committed fixture records under
`tests/fixtures/refresh_timing/01-c2/`: two real symphonies (one triggered, one not), the
shadow rows of two consecutive engine cycles seeded through the real writer, the live and
closed-snapshot state through the real `database.save_state`, warm Composer account totals
taken from the captured total-stats body, a frozen route clock, and a forced market state.

Nothing here mocks `analytics` or `database`: the routes run against a temp SQLite file
(`tests/conftest.py` isolates it), so a wiring fault between the routes and the readers
fails a test instead of hiding behind a stand-in.

Two seams are pinned only because they would otherwise read the real wall clock:
  * `app.datetime`: the routes' own clock.
  * `analytics._window_cutoff_date`: resolves "30d" against the real UTC date, which would
    age fixture rows out of the window as calendar time passes.
"""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import analytics as analytics_module
import app as app_module
import database as database_module

ET = ZoneInfo("America/New_York")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "refresh_timing" / "01-c2"
COMPOSER_TOTAL_STATS = (
    Path(__file__).resolve().parents[1] / "fixtures" / "composer" / "v3-audit" / "total_stats.json"
)

TRIGGERED_ID = "nOyb55RMGVCKPiYXv7TI"
UNTRIGGERED_ID = "5XjzXjdGnjh99MIsdM97"
SYMPHONY_IDS = (TRIGGERED_ID, UNTRIGGERED_ID)

BRANCHES = ("open", "closed_frozen", "pre_market")

# The real function, captured at import time: every tests/app and tests/dashboard conftest
# replaces `app.get_api_state_dict` with a stub for the whole directory, and these tests
# exist to prove the real pipeline.
_REAL_GET_API_STATE_DICT = app_module.get_api_state_dict

# Writer keywords of `database.record_shadow_observation`; a captured row also carries
# `id` and `math_mode`, which the writer does not take.
_SHADOW_WRITER_KEYS = (
    "symphony_id",
    "account_id",
    "cycle_id",
    "ts_utc",
    "ts_et",
    "trading_day",
    "current_return",
    "shadow_return",
    "is_post_trigger",
    "trigger_id",
    "position_epoch",
)

# Account-total drift: how far each cached figure moves per simulated background refresh.
_DRIFT_VALUE_STEP = 10.0
_DRIFT_RETURN_STEP_PCT = 0.5
_DRIFT_TODAY_STEP_PCT = 0.05

# Longest a drift-test thread waits for its peer before the test fails instead of hanging.
_RENDEZVOUS_TIMEOUT_S = 20.0


@dataclass(frozen=True)
class Moment:
    market_state: str
    instant: datetime

    @property
    def trading_day(self) -> str:
        return self.instant.strftime("%Y-%m-%d")


# The close snapshot of the seeded closed worlds is stamped after the newest seeded shadow row
# (16:04:01 ET), so every seeded row belongs to the close the snapshot describes. The record's
# own 16:00:01 would put the whole seeded day after the capture.
CLOSE_SNAPSHOT_CAPTURED_AT = "16:05:00 ET"

# Cycle M's rows are committed and its state write is not, one minute after M-1 finished.
MOMENTS = {
    "open": Moment("open", datetime(2026, 10, 21, 16, 4, 20, tzinfo=ET)),
    "closed_frozen": Moment("closed_frozen", datetime(2026, 10, 21, 18, 0, 0, tzinfo=ET)),
    "pre_market": Moment("pre_market", datetime(2026, 10, 22, 8, 0, 0, tzinfo=ET)),
}


def load_record(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def frozen_datetime_class(instant: datetime) -> type[datetime]:
    """A `datetime` subclass whose `now()` is `instant` (naive `now()` is its wall clock)."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return instant.astimezone(tz)
            return instant.replace(tzinfo=None)

    return _Frozen


def _pinned_window_cutoff(today: date):
    def _cutoff(window: object):
        if isinstance(window, int):
            return today - timedelta(days=window)
        token = str(window).lower()
        if token == "all":
            return None
        if token == "ytd":
            return date(today.year, 1, 1)
        days = analytics_module._WINDOW_TRAILING_DAYS.get(token)
        return None if days is None else today - timedelta(days=days)

    return _cutoff


# ---------------------------------------------------------------------------
# Account totals
# ---------------------------------------------------------------------------


def account_totals(version: int = 0) -> dict:
    """The cache keys `_refresh_account_totals` writes, from the captured Composer body.

    `version` > 0 moves every figure, as a background refresh landing between two reads.
    """
    stats = json.loads(COMPOSER_TOTAL_STATS.read_text(encoding="utf-8"))
    return {
        "portfolio_value": stats["portfolio_value"] + _DRIFT_VALUE_STEP * version,
        "portfolio_cr": stats["simple_return"] * 100.0 + _DRIFT_RETURN_STEP_PCT * version,
        "portfolio_tc": stats["todays_percent_change"] * 100.0 + _DRIFT_TODAY_STEP_PCT * version,
        "portfolio_mdd": float(stats["metrics"]["max_drawdown"]) * 100.0,
    }


def seed_account_totals(monkeypatch) -> None:
    cache = app_module._account_totals_cache
    cache.clear()
    for key, value in account_totals().items():
        cache[key] = value
    monkeypatch.setattr(app_module, "_account_totals_last_good", dict(cache))
    monkeypatch.setattr(app_module, "_account_totals_last_success_at", None)


def mask_account_totals(monkeypatch) -> None:
    """No usable account totals: cache masked as stale and no last-good snapshot."""
    app_module._account_totals_cache.mark_stale()
    monkeypatch.setattr(app_module, "_account_totals_last_good", {})


def install_account_total_drift(monkeypatch, *, rendezvous: threading.Barrier | None = None):
    """Move the cached account totals after every default-window strip computation.

    A computation reads the cache before it calls this analytics function and the move
    lands after it returns, so a response built from one computation is unaffected while a
    response that computes twice reads two different sets of figures. `rendezvous`, when
    given, holds each request thread's first computation until its peer arrives so that two
    requests are provably in flight together.
    """
    real = analytics_module.compute_windowed_portfolio_strip
    lock = threading.Lock()
    seen = threading.local()
    counter = {"calls": 0}

    def drifting(*args, **kwargs):
        if rendezvous is not None and not getattr(seen, "arrived", False):
            seen.arrived = True
            rendezvous.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        result = real(*args, **kwargs)
        with lock:
            counter["calls"] += 1
            for key, value in account_totals(counter["calls"]).items():
                app_module._account_totals_cache[key] = value
        return result

    monkeypatch.setattr(analytics_module, "compute_windowed_portfolio_strip", drifting)
    return counter


# ---------------------------------------------------------------------------
# Counting seams
# ---------------------------------------------------------------------------


class CallCounter:
    """Counts calls into one seam while running the real one."""

    def __init__(self) -> None:
        self.count = 0
        self._lock = threading.Lock()

    def bump(self) -> None:
        with self._lock:
            self.count += 1


def count_analytics_call(monkeypatch, name: str) -> CallCounter:
    counter = CallCounter()
    real = getattr(analytics_module, name)

    def counting(*args, **kwargs):
        counter.bump()
        return real(*args, **kwargs)

    monkeypatch.setattr(analytics_module, name, counting)
    return counter


def count_app_call(monkeypatch, name: str) -> CallCounter:
    """Count calls to an `app` function; a name the module lacks counts as zero calls."""
    counter = CallCounter()
    real = getattr(app_module, name, None)

    def counting(*args, **kwargs):
        counter.bump()
        if real is None:
            raise AssertionError(f"app.{name} does not exist")
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module, name, counting, raising=False)
    return counter


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------


class World:
    def __init__(self, client, moment: Moment) -> None:
        self.client = client
        self.moment = moment
        self._records = {
            "r6": load_record("shadow_rows_two_cycles.json"),
            "moved": load_record("shadow_rows_cycle_m_moved.json"),
            "live": load_record("live_state_two_cycles.json"),
            "snapshot": load_record("snapshot_two_symphonies.json"),
        }

    # -- shadow rows -------------------------------------------------------

    def cycle_rows(self, label: str) -> list[dict]:
        """`m_minus_1` is captured cycle 20261021_1603; `m` is the newer, moved cycle."""
        if label == "m_minus_1":
            return [dict(row) for row in self._records["r6"]["cycles"][0]["rows"]]
        if label == "m":
            return [dict(row) for row in self._records["moved"]["rows"]]
        raise KeyError(label)

    def previous_day_rows(self) -> list[dict]:
        sym = self._records["r6"]["symphonies"]
        return [dict(sym[key]["previous_day_last_row"]) for key in ("triggered", "untriggered")]

    def seed_rows(self, rows: list[dict]) -> None:
        """Write rows through the real writer and prove each one landed."""
        for row in rows:
            database_module.record_shadow_observation(**{k: row[k] for k in _SHADOW_WRITER_KEYS})
        conn = sqlite3.connect(os.environ["DB_PATH"])
        try:
            for row in rows:
                found = conn.execute(
                    "SELECT COUNT(*) FROM shadow_history "
                    "WHERE symphony_id = ? AND cycle_id = ? AND ts_et = ?",
                    (row["symphony_id"], row["cycle_id"], row["ts_et"]),
                ).fetchone()[0]
                assert found == 1, f"seeding failed for {row['symphony_id']} {row['cycle_id']}"
        finally:
            conn.close()

    def seed_cycle(self, label: str, *, only: str | None = None) -> None:
        rows = self.cycle_rows(label)
        self.seed_rows([r for r in rows if only is None or r["symphony_id"] == only])

    def row(self, label: str, symphony_id: str) -> dict:
        return next(r for r in self.cycle_rows(label) if r["symphony_id"] == symphony_id)

    # -- state -------------------------------------------------------------

    def commit_state(
        self,
        label: str,
        *,
        with_snapshot: bool = False,
        snapshot_captured_at: str | None = None,
    ) -> None:
        """Save `bot_state` as the engine does at the end of cycle `label`.

        `snapshot_captured_at` replaces the record's `captured_at_et` ("HH:MM:SS ET"): the
        world seeds shadow rows stamped up to 16:04, so a snapshot stamped earlier would
        leave every seeded row outside the close it describes.
        """
        live = self._records["live"]
        state: dict = dict(live["reserved"])
        state.update(json.loads(json.dumps(live[label]["entries"])))
        state["last_successful_cycle_at"] = live[label]["last_successful_cycle_at"]
        if with_snapshot:
            snapshot = json.loads(json.dumps(self._records["snapshot"]))
            if snapshot_captured_at is not None:
                snapshot["captured_at_et"] = snapshot_captured_at
            state["last_market_close_snapshot"] = snapshot
        database_module.save_state(state)

    def stamp(self, label: str) -> str:
        """The `last_successful_cycle_at` the engine saves at the end of cycle `label`."""
        return self._records["live"][label]["last_successful_cycle_at"]

    def live_entry(self, label: str, symphony_id: str) -> dict:
        return self._records["live"][label]["entries"][symphony_id]

    @property
    def snapshot_entries(self) -> dict[str, dict]:
        accounts = self._records["snapshot"]["accounts_map"]
        return {e["id"]: e for entries in accounts.values() for e in entries}

    # -- requests ----------------------------------------------------------

    def poll(self, path: str = "/api/state") -> dict:
        response = self.client.get(path)
        assert response.status_code == 200, (
            f"{path} answered {response.status_code}: {response.get_data(as_text=True)[:300]}"
        )
        return response.get_json()

    def page(self) -> str:
        response = self.client.get("/")
        assert response.status_code == 200, f"/ answered {response.status_code}"
        return response.get_data(as_text=True)


def build_world(monkeypatch, tmp_path, branch: str) -> World:
    """Pin the clocks and the market state, restore the real pipeline, seed account totals.

    No shadow rows and no state are written: each test seeds the cycle it is about.
    """
    moment = MOMENTS[branch]
    monkeypatch.setattr(app_module, "datetime", frozen_datetime_class(moment.instant))
    monkeypatch.setattr(app_module, "get_market_state", lambda _dt: moment.market_state)
    monkeypatch.setattr(analytics_module, "DB_FILE", os.environ["DB_PATH"])
    monkeypatch.setattr(
        analytics_module, "_window_cutoff_date", _pinned_window_cutoff(moment.instant.date())
    )
    # Bound directly, never through `monkeypatch`: monkeypatch would save the suite's stand-in as
    # the value to restore, and when its undo runs after the conftest patch's own exit the stand-in
    # is written back and stays on the module. The conftest patch saved the real function at its
    # entry and puts it back at its exit, so a direct binding is undone whatever the order is.
    app_module.get_api_state_dict = _REAL_GET_API_STATE_DICT

    # The developer .env holds real credentials: nothing here may read it.
    for var in ("LIVE_EXECUTION", "EXIT_AUTHORITY", "EXECUTION_START_TIME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(tmp_path / "absent.env"))
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
    monkeypatch.setattr(app_module._dotenv_module, "dotenv_values", lambda *_a, **_k: {})

    seed_account_totals(monkeypatch)
    app_module.app.config["TESTING"] = True
    return World(app_module.app.test_client(), moment)


def seeded_world(monkeypatch, tmp_path, branch: str, *, cycle: str = "m") -> World:
    """The world a finished cycle leaves: yesterday's rows and both cycles' rows committed,
    state saved for `cycle`, with the close snapshot in place for the two closed branches."""
    world = build_world(monkeypatch, tmp_path, branch)
    world.seed_rows(world.previous_day_rows())
    world.seed_cycle("m_minus_1")
    world.seed_cycle("m")
    world.commit_state(
        cycle,
        with_snapshot=branch != "open",
        snapshot_captured_at=CLOSE_SNAPSHOT_CAPTURED_AT,
    )
    return world


# ---------------------------------------------------------------------------
# Reading a response
# ---------------------------------------------------------------------------

_CARD_LEG_KEYS = ("tc_bot", "tc_held", "cr_bot", "cr_held", "mdd_bot", "mdd_held")


def card_legs(body: dict) -> dict[str, dict]:
    """Bot and Held legs of every card in a live response, by symphony id."""
    return {
        card["id"]: {key: card.get(key) for key in _CARD_LEG_KEYS} for card in body["symphonies"]
    }


def cycle_projection(body: dict) -> dict:
    """Every figure of a live response that one engine cycle determines.

    Excludes the fields that read the request clock or run outside the committed view
    (`meta.clock_et`, `meta.next_run`, `next_run_seconds`, `shadow_divergence`, `html`).
    """
    return {
        "cards": body["symphonies"],
        "portfolio_strip": body["portfolio_strip"],
        "meta_portfolio": body["meta"]["portfolio"],
        "entry_legs": {
            sym_id: {key: entry.get(key) for key in ("_tc", "_cr", "_mdd")}
            for sym_id, entry in body["bot_state"].items()
            if isinstance(entry, dict) and "name" in entry
        },
    }


def hero_last_points(body: dict) -> dict:
    """The last point of the hero series, from the strip and from `meta.portfolio`."""
    strip = body["portfolio_strip"]
    portfolio = body["meta"]["portfolio"]
    return {
        "strip_bot": (strip.get("hist_bot") or [None])[-1],
        "strip_held": (strip.get("hist_held") or [None])[-1],
        "meta_bot": (portfolio.get("hist_bot") or [None])[-1],
        "meta_held": (portfolio.get("hist_held") or [None])[-1],
    }


# -- the server-rendered page, as text the operator reads ---------------------

_ROW_TESTIDS = {
    "today": ("comp-today-bot-text", "comp-today-held-text"),
    "cumulative": ("comp-cumulative-bot-text", "comp-cumulative-held-text"),
    "mdd": ("comp-mdd-bot-text", "comp-mdd-held-text"),
}
_NUMBER = re.compile(r"[-+]?\d+\.\d{2}")


def _element_text(page: str, pattern: str) -> str | None:
    match = re.search(pattern, page, flags=re.S)
    if match is None:
        return None
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", match.group(1)))).strip()


def _testid_text(page: str, testid: str) -> str | None:
    return _element_text(page, rf'data-testid="{re.escape(testid)}"[^>]*>(.*?)</(?:span|div)>')


def number_in(text: str | None) -> str | None:
    """The two-decimal figure a row text shows, without its sign prefix; None for a dash."""
    if text is None:
        return None
    found = _NUMBER.search(text)
    return None if found is None else found.group(0).lstrip("+")


def format_figure(value) -> str | None:
    """How a figure reads on screen: two decimals, None for a missing value."""
    return None if value is None else f"{value:.2f}"


def window_mdd_figures(strip: dict) -> dict:
    """The Max DD row as the page prints it: the picked window's drawdown as two magnitudes.

    `strip` is a `/api/strip/<window>` body. The row is the window's and never the poll's
    lifetime figure, so a page that prints the poll's `max_drawdown` is wrong.
    """
    legs = strip.get("max_drawdown") or {}
    return {
        f"mdd_{side}": None if legs.get(key) is None else format_figure(abs(legs[key]))
        for side, key in (("bot", "dry_run"), ("held", "if_held"))
    }


def rendered_page_figures(page: str) -> dict:
    """The values `GET /` renders for the rows, headline, counts and as-of."""
    figures: dict = {}
    for row, (bot_id, held_id) in _ROW_TESTIDS.items():
        figures[f"{row}_bot"] = number_in(_testid_text(page, bot_id))
        figures[f"{row}_held"] = number_in(_testid_text(page, held_id))
    figures["guard_alpha"] = number_in(_testid_text(page, "guard-alpha-headline"))
    figures["account_all_time_cr"] = number_in(_testid_text(page, "account-all-time-cr"))
    for key, element in (
        ("tracked", "hero-tracked"),
        ("armed", "hero-armed"),
        ("triggered", "hero-triggered"),
    ):
        text = _element_text(page, rf'id="{element}"[^>]*>(.*?)</div>')
        figures[key] = None if text is None else int(text)
    as_of = _element_text(page, r'id="hero-data-as-of"[^>]*>(.*?)</span>')
    figures["data_as_of"] = None if as_of is None else as_of.removeprefix("data as of ")
    return figures


def polled_figures(body: dict) -> dict:
    """The same values, as the poll hands them to the dashboard script."""
    strip = body["portfolio_strip"]
    figures: dict = {}
    for row, key in (
        ("today", "today_change"),
        ("cumulative", "cumulative_return"),
        ("mdd", "max_drawdown"),
    ):
        legs = strip.get(key) or {}
        figures[f"{row}_bot"] = format_figure(legs.get("dry_run"))
        figures[f"{row}_held"] = format_figure(legs.get("if_held"))
    figures["guard_alpha"] = format_figure(strip.get("guard_alpha"))
    figures["account_all_time_cr"] = format_figure(strip.get("account_all_time_cr"))
    meta = body["meta"]
    figures["tracked"] = meta["tracked"]
    figures["armed"] = meta["armed"]
    figures["triggered"] = meta["triggered"]
    figures["data_as_of"] = (meta.get("portfolio") or {}).get("data_as_of") or body.get(
        "data_as_of"
    )
    return figures
