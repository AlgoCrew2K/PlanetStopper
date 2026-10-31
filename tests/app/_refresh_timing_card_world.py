"""Triggered-card worlds for the frozen-card and verdict-label tests.

Builds on the clocked worlds of `_refresh_timing_frozen_world` (same captured records, real
temp database, nothing mocked) and adds what a card test needs:

  * the close snapshot with the second symphony armed instead of triggered, so one list holds a
    triggered and an armed card (derived record, see `tests/fixtures/refresh_timing/01-c5/
    PROVENANCE.md`);
  * the captured exit row written through the real writer;
  * the same world with the triggered symphony's `current_return` removed;
  * the cards of a server-rendered page read back as the text an operator sees.

Expected strings are built from the response a test is comparing against or from the seeded
inputs, never copied from a producer run.
"""

from __future__ import annotations

import html
import re
from datetime import datetime

import database as database_module
from tests.app._refresh_timing_frozen_world import (
    CAPTURE_DAY,
    close_world,
    et,
    midday_world,
    save_state,
    store_snapshot,
    stored_snapshot,
)
from tests.app._refresh_timing_state_world import (
    TRIGGERED_ID,
    UNTRIGGERED_ID,
    World,
    load_record,
)

NEXT_DAY = "2026-10-22"

OPEN_INSTANT = et(CAPTURE_DAY, "10:44:00")
CLOSED_INSTANT = et(CAPTURE_DAY, "18:00:00")
PRE_MARKET_INSTANT = et(NEXT_DAY, "08:00:00")

DASH = "—"
LABEL_OPEN = "vs held now"
LABEL_CLOSED = "at close"


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def exit_row_record() -> dict:
    """The captured exit row (R7) of the triggered symphony."""
    return load_record("exit_trigger_row.json")


def seed_exit_row(at_return: float | None = None) -> float:
    """Write the captured exit row through the real writer; returns the `at_return` stored.

    `at_return` replaces the captured value (the verdict figure is that value minus the
    symphony's `current_return`, so a test that needs a figure of a chosen sign or zero sets it).
    """
    row = exit_row_record()
    value = row["at_return"] if at_return is None else at_return
    row_id = database_module.record_exit_trigger(
        symphony_id=TRIGGERED_ID,
        account_id=row["account_id"],
        triggered_reason=row["triggered_reason"],
        at_return=value,
        cycle_id=row["cycle_id"],
        ts_utc=row["ts_utc"],
        ts_et=row["ts_et"],
    )
    assert row_id is not None, "the real exit-trigger writer refused the captured row"
    return value


def _entries(snapshot: dict) -> dict[str, dict]:
    return {
        entry["id"]: entry
        for entries in snapshot["accounts_map"].values()
        for entry in entries
        if isinstance(entry, dict) and "id" in entry
    }


def card_snapshot(snapshot: dict, *, drop_current_return: bool = False) -> dict:
    """The stored snapshot with the untriggered symphony armed (it was triggered in the
    capture) and, when asked, the triggered symphony's `current_return` removed."""
    entries = _entries(snapshot)
    armed = entries[UNTRIGGERED_ID]
    armed["triggered"] = False
    armed["armed"] = True
    if drop_current_return:
        entries[TRIGGERED_ID].pop("current_return", None)
    return snapshot


def closed_cards_world(
    monkeypatch, tmp_path, instant: datetime = CLOSED_INSTANT, *, drop_current_return: bool = False
) -> World:
    """The state after the close: previous-day rows, the close cycle's rows before the capture,
    the close snapshot (one triggered card, one armed card) and live state moved on."""
    world, _rows = close_world(monkeypatch, tmp_path, instant)
    snapshot = card_snapshot(stored_snapshot(), drop_current_return=drop_current_return)
    store_snapshot(world, snapshot)
    return world


def open_cards_world(monkeypatch, tmp_path, *, drop_current_return: bool = False) -> World:
    """A market-open world (both cycles' rows, live state at the newer cycle)."""
    world, _seeded = midday_world(monkeypatch, tmp_path, OPEN_INSTANT)
    if drop_current_return:
        state = database_module.load_state()
        state[TRIGGERED_ID].pop("current_return", None)
        database_module.save_state(state)
    return world


def set_live_return(symphony_id: str, value: float) -> None:
    """Save live `bot_state` with one symphony's `current_return` replaced (a later cycle)."""
    state = database_module.load_state()
    state[symphony_id]["current_return"] = value
    database_module.save_state(state)


def live_entry(symphony_id: str) -> dict:
    return database_module.load_state()[symphony_id]


def snapshot_entry(symphony_id: str) -> dict:
    return _entries(stored_snapshot())[symphony_id]


def move_live_state_on(world: World) -> None:
    """Live state changes after the close: values and returns move, the triggered symphony
    reads as not triggered. The snapshot stays as stored."""
    save_state(
        world,
        "m",
        snapshot=stored_snapshot(),
        entry_overrides={
            TRIGGERED_ID: {"triggered": False, "armed": True, "current_return": 7.77},
            UNTRIGGERED_ID: {"triggered": True, "armed": False, "current_return": -6.66},
        },
    )


# ---------------------------------------------------------------------------
# Reading cards
# ---------------------------------------------------------------------------

_CARD_START = re.compile(r'<div data-testid="sym-card"[^>]*data-sym-id="([^"]*)"')
_TAG = re.compile(r"<[^>]+>")


def _text(segment: str, pattern: str) -> str | None:
    match = re.search(pattern, segment, flags=re.S)
    if match is None:
        return None
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", match.group(1)))).strip()


def rendered_cards(page: str) -> dict[str, dict]:
    """Every card of a server-rendered page, by symphony id, as the text it shows.

    `today_bot` is the large figure at the top of the card; `cum_bot`, `mdd_bot` and the held
    legs are the footer cells. `basis_label` is the text of the verdict's basis label and
    `basis_hidden` whether it carries the `hidden` attribute (None when the element is absent).
    """
    starts = [(m.start(), m.group(1)) for m in _CARD_START.finditer(page)]
    standby_at = page.find('data-testid="standby-section-count"')
    cards: dict[str, dict] = {}
    for index, (position, symphony_id) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(page)
        segment = page[position:end]
        label = re.search(
            r'data-testid="guard-alpha-(basis|at-exit)"([^>]*)>(.*?)</span>', segment, flags=re.S
        )
        cards[symphony_id] = {
            "section": "standby" if 0 <= standby_at < position else "active",
            "pill": _text(segment, r'data-testid="status-pill"[^>]*>(.*?)</span>'),
            "today_bot": _text(segment, r'data-field="tc-bot"[^>]*>(.*?)</span>'),
            "today_held": _text(segment, r'data-field="tc-held"[^>]*>(.*?)</span>'),
            "cum_bot": _text(segment, r'data-field="cr-bot"[^>]*>(.*?)</span>'),
            "cum_held": _text(segment, r'data-field="cr-held"[^>]*>(.*?)</span>'),
            "mdd_bot": _text(segment, r'data-field="mdd-bot"[^>]*>(.*?)</span>'),
            "mdd_held": _text(segment, r'data-field="mdd-held"[^>]*>(.*?)</span>'),
            "verdict_text": _text(segment, r'data-field="verdict-text"[^>]*>(.*?)</span>'),
            "basis_testid": None if label is None else f"guard-alpha-{label.group(1)}",
            "basis_label": None if label is None else _text(label.group(3), r"(.*)"),
            "basis_hidden": None if label is None else " hidden" in label.group(2),
        }
    return cards


def card_of(body: dict, symphony_id: str) -> dict:
    """One entry of a response's `symphonies` list; fails readably when it is missing."""
    cards = body.get("symphonies")
    assert isinstance(cards, list), "the response carries no `symphonies` list"
    found = [card for card in cards if card.get("id") == symphony_id]
    assert len(found) == 1, f"`symphonies` holds {len(found)} entries for {symphony_id}"
    return found[0]


def signed_percent(value) -> str:
    """How a Today or Cumulative leg reads on a card: sign, one decimal, percent; a dash if None."""
    return DASH if value is None else f"{value:+.1f}%"


def drawdown_percent(value) -> str:
    """How a Max DD leg reads on a card: magnitude, one decimal, percent; two hyphens if None."""
    return "--" if value is None else f"{abs(value):.1f}%"
