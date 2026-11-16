"""The ticker check runner: Composer reads, Alpaca asset reads, one stored run, Discord posting.

Module under test: advisors.ticker_health_runner, driven at the ``requests`` boundary by
tests/advisors/_ticker_health_http_router.py (captured Composer roster and score records,
captured Alpaca asset records; see the PROVENANCE.md files beside the fixtures). The router
replaces every ``requests`` verb and ``time.sleep``, so nothing reaches a network and no test
waits out a pacing interval. The unit logic (tree walker, verdict classifier, carry-forward,
page text, message text) is tested in the sibling ``test_ticker_health_*`` files; this file
pins what only a whole run shows. The implementer reads this file and the handoff, not the plan.

Contract pinned here
--------------------
Entry points (module ``advisors.ticker_health_runner``)

- ``run_ticker_health_check(*, account_ids, account_labels, alpaca_key, alpaca_secret,
  webhook_url, trigger, other_reader_running, clock=None) -> RunOutcome`` with ``run_id`` (a
  fresh string per run), ``status`` (``completed``, ``unavailable``, ``skipped`` or ``failed``)
  and ``completed`` (True exactly when status is ``completed``). ``clock`` is a zero-argument
  callable returning an aware UTC datetime and is the only source of every stored time and of
  the market test; ``other_reader_running`` is a zero-argument callable. It never raises an
  ``Exception``; a ``BaseException`` (a daemon stop, simulated here with ``SystemExit``)
  propagates and leaves no run row.
- ``read_latest_runs(*, scan_limit=365) -> LatestRuns`` with ``latest`` and ``latest_completed``,
  each the stored run record (the ``raw_response`` dict of the row) or None. It reads the newest
  ``TICKER_HEALTH_RUN`` row first with ``limit=1``; only when that row is not completed does it
  read ``scan_limit`` rows and take the newest completed one. It raises on a database error.

Stored rows (through ``database.insert_advisor_observation``, called as that module attribute
with keyword arguments, and read through ``database.get_advisor_observations_for_role``)

- One ``TICKER_HEALTH_RUN`` row per run that ends, written once, last (``subject_type``
  ``ticker_health_run``, ``subject_id`` the run id, ``verdict`` the status). Its ``raw_response``
  holds ``schema`` 1, ``run_id``, ``trigger``, ``started_at`` and ``ended_at`` (aware UTC ISO
  text from ``clock``), ``status``, ``completed`` (bool), ``reason`` for ``unavailable`` and
  ``skipped``, ``error_type`` (the exception class name only) for ``failed``, and, on completed
  runs only, ``warnings``, ``not_checked`` and ``counts``.
- A warning is exactly ``symphony_id``, ``display_name``, ``ticker``, ``reason``, ``asset_nodes``,
  ``conditions``, ``held`` (``held``, ``not_held`` or ``unknown``), ``found_at`` (the
  ``ended_at`` of the run that last judged the pair dead) and ``announced`` (bool).
- ``not_checked`` is exactly ``accounts`` (``label``, ``reason``), ``symphonies`` (``symphony_id``,
  ``display_name``, ``reason``) and ``tickers``: a named entry (``symphony_id``,
  ``display_name``, ``ticker``, ``reason``) for a ticker not in plain form, or a count entry
  (``symphony_id``, ``display_name``, ``reason``, ``count``) per symphony and reason for tickers
  Alpaca left unanswered.
- ``counts`` holds ``accounts_read``, ``symphonies_listed``, ``symphonies_judged``,
  ``tickers_asked``, ``tickers_answered``, ``dead_pairs``, ``composer_requests`` and
  ``alpaca_requests`` (every attempt).
- One ``TICKER_HEALTH_ANNOUNCEMENT`` row per Discord message Discord accepted (2xx):
  ``subject_id`` the run id, ``verdict`` ``announced``, ``raw_response`` ``{"run_id", "pairs":
  [[symphony_id, ticker], ...]}`` naming the pairs of that message. The run row is written
  before any post; a post is followed by its announcement row.

Requests

- Composer: ``composer_portfolio_client.read_symphony_statistics(account, None)`` per account
  in order, then ``read_symphony_definition(id, None)`` per distinct symphony id in roster
  order (the client's own 6.0 second sleep after every request is the pacing). Symphonies listed
  by several accounts merge by id: the first account's name, every account's holdings.
- Alpaca: ``requests.get("<paper host>/v2/assets/<SYMBOL>", headers={"APCA-API-KEY-ID",
  "APCA-API-SECRET-KEY"}, timeout=10, allow_redirects=False)``, the tickers sorted, each asked
  once per run, a 0.5 second ``time.sleep`` after every attempt, at most 2 attempts on 429, 5xx
  and a transport error (the wait between attempts is an integer ``Retry-After`` clamped to
  0..30 else 2), no retry on 401, 403, 404 or another status. The first 401 or 403 stops every
  further Alpaca request. Five consecutive tickers ending unreachable or unavailable (429 does
  not count) stop the rest, which read the last reason. One ``timing_log.emit("alpaca", "GET",
  "/v2/assets", status=..., ms=...)`` per attempt, never carrying the symbol.
- Discord: ``requests.post(webhook_url, json={"content": text, "allowed_mentions": {"parse":
  []}}, timeout=10, allow_redirects=False)``, ``time.sleep(1.5)`` between messages, only after a
  completed run and only for fresh dead pairs not yet announced; any non-2xx status or exception
  logs the status or class name and ends the run's posting (the run stays stored as it is).
- The Composer gate runs before every Composer request: a market that reads ``open`` through
  ``market_calendar.get_market_state(clock())`` stops Composer requests ("market opened" for
  symphonies not yet read; before the first request the run ends ``skipped`` with reason
  "market open"); while ``other_reader_running()`` is true the run sleeps 15 seconds and asks
  again, until 2700 seconds after ``started_at`` (before the first request the run then ends
  ``skipped`` with reason "another Composer job was still running", later the symphonies not
  yet read read "another Composer job was running"); the first 429 stops every further Composer
  request ("Composer rate limit").

Patch targets and call forms that must hold: ``time.sleep`` called as the module attribute;
``requests.get`` and ``requests.post`` called as module attributes; ``database.
insert_advisor_observation`` and ``database.get_advisor_observations_for_role`` called as module
attributes; ``advisors.ticker_health.tree_ticker_uses`` called as that module attribute (the
failed-run tests patch it). The runner reads no environment variable and never opens
``universe_provider`` or ``atlas_cache``.

Tests that mirror a scenario of the app cycle ("runner leg") pin the runner's half of it so the
dashboard cycle starts from a runner already proven here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests

import database
from advisors import ticker_health_runner as runner
from tests.advisors import _composer_http_router as h
from tests.advisors import _ticker_health_http_router as t

REPO = Path(__file__).resolve().parents[2]

RUN_ROLE = "TICKER_HEALTH_RUN"
ANNOUNCEMENT_ROLE = "TICKER_HEALTH_ANNOUNCEMENT"

# The lead of every Discord message, the AC-3 wording of the plan.
LEAD = "These live symphonies reference tickers that can no longer be traded; fix each tree in Composer."
DEAD_SHNY = "inactive and not tradable at Alpaca"
NOT_LISTED = "not listed at Alpaca"
NOT_TRADABLE = "not tradable at Alpaca"
NOT_HELD, HELD, HOLDING_UNKNOWN = "Not held now.", "Held now.", "Holding unknown."

COMPOSER_SPACING = 6.0
ALPACA_SPACING = 0.5
ALPACA_ATTEMPTS = 2
ALPACA_STREAK_LIMIT = 5
RETRY_BACKOFF = 2
RETRY_CAP = 30
POLL_SECONDS = 15
OTHER_READER_LIMIT_SECONDS = 2700
DISCORD_SPACING = 1.5
DISCORD_LIMIT = 2000
REQUEST_TIMEOUT = 10
LEAK = "LEAKMARK-reply-body-8841"

X, Y = t.PLANET_LQD_ID, t.TROLLING_ID
IN_SESSION = datetime(2026, 11, 17, 16, 0, tzinfo=UTC)  # Tuesday 11:00 EST: the market is open
JUST_BEFORE_OPEN = datetime(2026, 11, 17, 14, 29, 30, tzinfo=UTC)  # 09:29:30 EST
JUST_AFTER_OPEN = datetime(2026, 11, 17, 14, 30, 1, tzinfo=UTC)  # 09:30:01 EST
LABELS = {h.ACCOUNT_ID: "Roth IRA", h.SECOND_ACCOUNT_ID: "Individual"}

FORBIDDEN_SECRETS = (
    t.ALPACA_KEY,
    t.ALPACA_SECRET,
    h.SERVICE_KEY_ID,
    h.SERVICE_SECRET,
    t.WEBHOOK_URL,
    t.WEBHOOK_TOKEN,
    t.WEBHOOK_ID,
)

# reporting.py at the plan's ref, LF-normalised sha256: the exit alert and end-of-day senders
# are consumed unchanged. Stop and ask before re-pinning.
REPORTING_SHA256 = "a8d97d0a01f2ecb618d0bfb8256cbdb16ac205806662e6dc16f696082151f07d"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def router(monkeypatch):
    return t.TickerHealthRouter().install(monkeypatch)


@pytest.fixture(autouse=True)
def _isolated_atlas_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_CACHE_DB_PATH", str(tmp_path / "test_atlas_cache.db"))


def run_check(router, *, start=t.NIGHT, **overrides):
    args = {
        "account_ids": [h.ACCOUNT_ID],
        "account_labels": dict(LABELS),
        "alpaca_key": t.ALPACA_KEY,
        "alpaca_secret": t.ALPACA_SECRET,
        "webhook_url": t.WEBHOOK_URL,
        "trigger": "nightly",
        "other_reader_running": lambda: False,
        "clock": t.SleepClock(router, start),
    }
    args.update(overrides)
    return runner.run_ticker_health_check(**args)


def night(n: int) -> datetime:
    return t.NIGHT + timedelta(days=n)


def rows(role):
    return database.get_advisor_observations_for_role(role, limit=500)


def record_of(outcome) -> dict:
    found = [r for r in rows(RUN_ROLE) if r["subject_id"] == outcome.run_id]
    assert len(found) == 1, f"expected one run row for {outcome.run_id!r}, got {len(found)}"
    return found[0]["raw_response"]


def announcements() -> list[dict]:
    return [r["raw_response"] for r in rows(ANNOUNCEMENT_ROLE)]


def announced_pairs() -> set[tuple[str, str]]:
    return {tuple(pair) for rec in announcements() for pair in rec["pairs"]}


def completed_run(router, **kwargs):
    outcome = run_check(router, **kwargs)
    assert (outcome.status, outcome.completed) == ("completed", True), outcome
    return outcome


def name_of(symphony_id: str) -> str:
    return t.roster_entry(symphony_id)["name"].strip()


def roster(router, *ids, **edits_by_id):
    router.set_roster(*(t.roster_entry(i, **edits_by_id.get(i, {})) for i in ids))


def discord_escape(text: str) -> str:
    """The characters the plan names, each given a backslash: independent of the product."""
    return re.sub(r"([\\*_~`|<>#\-\[\]()@:])", r"\\\1", text)


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def pair_line(name, symphony_id, ticker, reason, counts, held_text) -> str:
    assets, conditions = counts
    return (
        f"{discord_escape(name)} ({symphony_id}): {ticker} is {reason}. "
        f"Used by {_count(assets, 'asset node')} and {_count(conditions, 'condition')}. {held_text}"
    )


def shny_counts() -> tuple[int, int]:
    return t.oracle_tree_uses(t.captured_tree())["SHNY"]


def shny_line(symphony_id, held_text=NOT_HELD, name=None) -> str:
    return pair_line(
        name if name is not None else name_of(symphony_id),
        symphony_id,
        "SHNY",
        DEAD_SHNY,
        shny_counts(),
        held_text,
    )


def lines_of(content: str) -> list[str]:
    return [line for line in content.splitlines() if line.strip()]


def warning_pairs(rec) -> set[tuple[str, str]]:
    return {(w["symphony_id"], w["ticker"]) for w in rec["warnings"]}


def not_checked_symphony(symphony_id, reason) -> dict:
    return {"symphony_id": symphony_id, "display_name": name_of(symphony_id), "reason": reason}


def tickers_of(tree) -> list[str]:
    return sorted(t.oracle_tree_uses(tree))


def transport_error(exc=requests.ConnectionError):
    return h.raising(exc(f"transport failed {t.ALPACA_KEY} {t.ALPACA_SECRET}"))


def alpaca_status(status: int, **headers) -> h.Reply:
    """A derived non-asset Alpaca reply: status decides, the body is a short JSON message."""
    return h.Reply(status=status, body={"message": "derived reply"}, headers=headers)


def spy_observation_reads(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    real = database.get_advisor_observations_for_role

    def spy(advisor_role, limit=50, *args, **kwargs):
        calls.append((advisor_role, limit))
        return real(advisor_role, limit, *args, **kwargs)

    monkeypatch.setattr(database, "get_advisor_observations_for_role", spy)
    return calls


def forbidden_in(text: str) -> list[str]:
    return [secret for secret in FORBIDDEN_SECRETS if secret in text]


# ---------------------------------------------------------------------------
# The stored run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trigger", ["nightly", "catch_up"])
def test_completed_run_is_stored_once_with_its_times_and_counts(router, trigger):
    roster(router, X)
    outcome = completed_run(router, webhook_url=None, trigger=trigger)
    assert outcome.run_id
    stored = rows(RUN_ROLE)
    assert len(stored) == 1
    row = stored[0]
    assert (row["advisor_role"], row["subject_type"]) == (RUN_ROLE, "ticker_health_run")
    assert (row["subject_id"], row["verdict"]) == (outcome.run_id, "completed")
    assert row["is_advisory_only"] == 1
    rec = row["raw_response"]
    assert (rec["schema"], rec["run_id"], rec["trigger"]) == (1, outcome.run_id, trigger)
    assert (rec["status"], rec["completed"]) == ("completed", True)
    assert datetime.fromisoformat(rec["started_at"]) == t.NIGHT
    ended = t.NIGHT + timedelta(seconds=sum(router.sleeps))
    assert datetime.fromisoformat(rec["ended_at"]) == ended
    assert set(rec["not_checked"]) == {"accounts", "symphonies", "tickers"}
    assert set(rec["counts"]) == {
        "accounts_read",
        "symphonies_listed",
        "symphonies_judged",
        "tickers_asked",
        "tickers_answered",
        "dead_pairs",
        "composer_requests",
        "alpaca_requests",
    }


def test_each_run_has_its_own_id_and_row(router):
    roster(router, X)
    first = completed_run(router, webhook_url=None)
    second = completed_run(router, webhook_url=None, start=night(1))
    assert first.run_id != second.run_id
    assert len(rows(RUN_ROLE)) == 2


def test_warning_record_has_exactly_the_documented_fields(router):
    roster(router, X)
    outcome = completed_run(router, webhook_url=None)
    rec = record_of(outcome)
    assert len(rec["warnings"]) == 1
    warning = rec["warnings"][0]
    assert set(warning) == {
        "symphony_id",
        "display_name",
        "ticker",
        "reason",
        "asset_nodes",
        "conditions",
        "held",
        "found_at",
        "announced",
    }
    assert warning["found_at"] == rec["ended_at"]
    assert warning["announced"] is False
    assert warning["display_name"] == name_of(X)


def test_counts_agree_with_the_request_ledger(router):
    roster(router, X, Y)
    outcome = completed_run(router, webhook_url=None)
    counts = record_of(outcome)["counts"]
    asked = tickers_of(t.captured_tree())
    assert counts["accounts_read"] == 1
    assert counts["symphonies_listed"] == counts["symphonies_judged"] == 2
    assert counts["tickers_asked"] == counts["tickers_answered"] == len(asked)
    assert counts["dead_pairs"] == 2
    assert counts["composer_requests"] == len(router.composer_requests())
    assert counts["alpaca_requests"] == len(router.asset_requests())


def test_every_request_of_a_full_run_is_a_read_or_the_webhook_post(router):
    """S-8.4: only Composer roster and score GETs, Alpaca asset GETs and the Discord POST."""
    roster(router, *t.roster_ids())
    completed_run(router)
    assert router.violations == []
    kinds = {r.kind for r in router.requests}
    assert kinds == {"statistics", "score", "alpaca_asset", "discord"}
    for request in router.requests:
        if request.kind == "discord":
            assert request.method == "POST" and request.url == t.WEBHOOK_URL
        else:
            assert request.method == "GET", f"{request.method} {request.path}"
        assert request.kwargs.get("allow_redirects") is False, request.path
    for request in router.of_kind("statistics"):
        assert request.path == f"/portfolio/accounts/{h.ACCOUNT_ID}/symphony-stats-meta"
    for request in router.of_kind("alpaca_asset"):
        assert request.url == f"{t.ALPACA_HOST}/v2/assets/{request.match['symbol']}"
        assert request.headers.get("APCA-API-KEY-ID") == t.ALPACA_KEY
        assert request.headers.get("APCA-API-SECRET-KEY") == t.ALPACA_SECRET
        assert request.timeout == REQUEST_TIMEOUT


def test_bars_are_never_requested_for_a_tradable_ticker(router):
    """S-2.7: a listed, tradable ticker is healthy whatever its price history; no bars read."""
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SPY"]))
    outcome = completed_run(router)
    assert router.asset_symbols() == ["SPY"]
    assert router.violations == []
    assert record_of(outcome)["warnings"] == []
    assert router.of_kind("discord") == []


# ---------------------------------------------------------------------------
# What a run asks about (AC-1)
# ---------------------------------------------------------------------------


def test_dead_ticker_counts_come_from_the_tree_and_are_asked_once(router):
    """S-1.8 runner leg: 25 asset nodes and one condition naming the ticker on both sides."""
    roster(router, X)
    outcome = completed_run(router, webhook_url=None)
    rec = record_of(outcome)
    uses = t.oracle_tree_uses(t.captured_tree())
    assert uses["SHNY"][1] == 1  # the captured condition names SHNY on both sides, counted once
    assert len(rec["warnings"]) == 1
    warning = rec["warnings"][0]
    assert (warning["symphony_id"], warning["ticker"], warning["reason"]) == (X, "SHNY", DEAD_SHNY)
    assert (warning["asset_nodes"], warning["conditions"]) == uses["SHNY"]
    assert warning["held"] == "not_held"
    assert router.asset_symbols().count("SHNY") == 1


def test_every_asset_and_operand_ticker_is_asked_once_in_sorted_order(router):
    """S-1.8 and S-2.15: legacy operand tickers are asked, fixed numeric values are not, and a
    ticker two symphonies name is asked once while each symphony keeps its own warning."""
    roster(router, X, Y)
    outcome = completed_run(router, webhook_url=None)
    tree = t.captured_tree()
    uses = t.oracle_tree_uses(tree)
    operand_only = {ticker for ticker, (assets, _) in uses.items() if assets == 0}
    assert operand_only, "the captured tree must name tickers only as condition operands"
    assert router.asset_symbols() == sorted(uses)
    assert not any(
        symbol.lstrip("-").replace(".", "").isdigit() for symbol in router.asset_symbols()
    )
    rec = record_of(outcome)
    assert warning_pairs(rec) == {(X, "SHNY"), (Y, "SHNY")}
    assert len(router.of_kind("score")) == 2


def test_tree_with_no_asset_and_no_condition_is_counted_checked(router):
    """S-1.9: nothing to ask, nothing to warn, and the symphony counts as judged."""
    roster(router, X)
    router.set_tree(X, t.tree_with(empty=True))
    outcome = completed_run(router)
    rec = record_of(outcome)
    assert rec["warnings"] == []
    assert rec["not_checked"] == {"accounts": [], "symphonies": [], "tickers": []}
    assert rec["counts"]["symphonies_judged"] == 1
    assert router.asset_requests() == []
    assert router.of_kind("discord") == []


def test_tree_of_another_asset_class_is_not_checked_and_never_asked(router):
    """S-1.10: a present top-level asset class that is not EQUITIES is reported, not asked."""
    roster(router, X, Y)
    router.set_tree(X, t.tree_with(assets=["SHNY", "QQQ"], asset_class="CRYPTO"))
    router.set_tree(Y, t.tree_with(assets=["SPY"]))
    outcome = completed_run(router)
    rec = record_of(outcome)
    assert router.asset_symbols() == ["SPY"]
    assert rec["not_checked"]["symphonies"] == [
        not_checked_symphony(X, "asset class not covered by the check")
    ]
    assert rec["warnings"] == []


def test_run_where_every_tree_is_of_another_asset_class_still_completes(router):
    roster(router, X, Y)
    router.set_tree(X, t.tree_with(assets=["SHNY"], asset_class="CRYPTO"))
    router.set_tree(Y, t.tree_with(assets=["QQQ"], asset_class="OPTIONS"))
    outcome = completed_run(router)
    rec = record_of(outcome)
    assert router.asset_requests() == []
    assert [e["reason"] for e in rec["not_checked"]["symphonies"]] == [
        "asset class not covered by the check"
    ] * 2
    assert router.of_kind("discord") == []


def test_tree_without_a_top_level_asset_class_is_covered(router):
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY"], asset_class=t.DROP))
    outcome = completed_run(router, webhook_url=None)
    rec = record_of(outcome)
    assert rec["not_checked"]["symphonies"] == []
    assert warning_pairs(rec) == {(X, "SHNY")}


@pytest.mark.parametrize("bad", ["BRK/B", "A B", "..", "../x"])
def test_ticker_not_in_plain_form_is_named_not_checked_and_never_requested(router, bad):
    """S-9.4: no request target is formed from it; the notice names it in its symphony."""
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=[bad, "SPY"], conditions=[("X/Y", "SPY")]))
    outcome = completed_run(router)
    rec = record_of(outcome)
    assert router.asset_symbols() == ["SPY"]
    assert router.violations == []
    assert all(t.PLAIN_SYMBOL.fullmatch(s) for s in router.asset_symbols())
    named = {e["ticker"]: e for e in rec["not_checked"]["tickers"] if "ticker" in e}
    assert set(named) == {bad, "X/Y"}
    for ticker, entry in named.items():
        assert entry == {
            "symphony_id": X,
            "display_name": name_of(X),
            "ticker": ticker,
            "reason": "ticker form not checked",
        }


@pytest.mark.parametrize(
    ("symphony_id", "edit", "expected"),
    [
        pytest.param(X, {"name": f"  {name_of(X)}  "}, name_of(X), id="trimmed"),
        pytest.param(Y, {"name": "   "}, Y, id="blank"),
        pytest.param(t.CORPORATE_CHAOS_ID, {"name": t.DROP}, t.CORPORATE_CHAOS_ID, id="missing"),
        pytest.param(t.CORPORATE_CHAOS_ID, {"name": None}, t.CORPORATE_CHAOS_ID, id="null"),
    ],
)
def test_display_name_is_trimmed_and_blank_or_missing_falls_back_to_the_id(
    router, symphony_id, edit, expected
):
    """S-3.6 runner leg."""
    roster(router, symphony_id, **{symphony_id: edit})
    outcome = completed_run(router, webhook_url=None)
    assert record_of(outcome)["warnings"][0]["display_name"] == expected


# ---------------------------------------------------------------------------
# Holding state (runner leg of the dashboard line)
# ---------------------------------------------------------------------------


def _holdings(*tickers, extra=None):
    out = [{"ticker": ticker, "allocation": 0.1} for ticker in tickers]
    if extra:
        out[0] = {**out[0], **extra}
    return out


@pytest.mark.parametrize(
    ("holdings", "expected"),
    [
        pytest.param(_holdings("BIL", "SHNY"), "held", id="ticker_key"),
        pytest.param(
            _holdings("BIL", extra={"working_ticker": "SHNY"}), "held", id="working_ticker"
        ),
        pytest.param(_holdings("BIL", "GLD"), "not_held", id="listed_without_it"),
        pytest.param([], "not_held", id="empty_list"),
        pytest.param(t.DROP, "unknown", id="field_absent"),
        pytest.param({"BIL": 0.1}, "unknown", id="not_a_list"),
        pytest.param(["BIL"], "unknown", id="entry_not_a_dict"),
        pytest.param([{"allocation": 0.1}], "unknown", id="entry_without_ticker"),
    ],
)
def test_held_state_comes_from_the_roster_holdings(router, holdings, expected):
    roster(router, X, **{X: {"holdings": holdings}})
    outcome = completed_run(router, webhook_url=None)
    assert record_of(outcome)["warnings"][0]["held"] == expected


def test_symphony_listed_by_two_accounts_is_one_symphony_with_both_holdings(router):
    """S-3.7: one score read, one warning, the first account's name, held in either account."""
    first = t.roster_entry(X, name="Name in the first account", holdings=_holdings("BIL"))
    second = t.roster_entry(X, name="Name in the second account", holdings=_holdings("SHNY"))
    router.set_roster(first, account=h.ACCOUNT_ID)
    router.set_roster(second, account=h.SECOND_ACCOUNT_ID)
    outcome = completed_run(
        router, webhook_url=None, account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID]
    )
    rec = record_of(outcome)
    assert router.score_ids() == [X]
    assert len(rec["warnings"]) == 1
    assert rec["warnings"][0]["display_name"] == "Name in the first account"
    assert rec["warnings"][0]["held"] == "held"


def test_one_account_without_readable_holdings_leaves_the_state_unknown(router):
    first = t.roster_entry(X, holdings=_holdings("BIL"))
    second = t.roster_entry(X, holdings=t.DROP)
    router.set_roster(first, account=h.ACCOUNT_ID)
    router.set_roster(second, account=h.SECOND_ACCOUNT_ID)
    outcome = completed_run(
        router, webhook_url=None, account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID]
    )
    assert record_of(outcome)["warnings"][0]["held"] == "unknown"


# ---------------------------------------------------------------------------
# What Alpaca's answer decides (AC-2)
# ---------------------------------------------------------------------------


def _seed_universe_cache(tmp_path, symbols):
    db = tmp_path / "test_atlas_cache.db"
    fetched_at = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS atlas_cache "
            "(collection TEXT PRIMARY KEY, fetched_at TEXT, payload TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO atlas_cache VALUES (?, ?, ?)",
            ("universe_provider", fetched_at, json.dumps(symbols)),
        )
        conn.commit()
    finally:
        conn.close()
    return db


def _cache_row(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT collection, fetched_at, payload FROM atlas_cache ORDER BY collection"
        ).fetchall()
    finally:
        conn.close()


def test_verdict_comes_from_tonights_asset_read_not_the_weekly_tradeable_set(
    router, tmp_path, monkeypatch
):
    """S-2.8 and S-2.13: the weekly set still lists SHNY; the dead verdict stands and the cache
    row (content and timestamp) is never read through its provider, nor refreshed."""
    from advisors import atlas_cache, universe_provider

    db = _seed_universe_cache(tmp_path, ["SHNY", "SPY"])
    before = _cache_row(db)
    touched: list[str] = []
    for module, name in (
        (universe_provider, "get_tradeable_set"),
        (universe_provider, "fetch_universe"),
        (universe_provider, "is_tradeable"),
        (atlas_cache, "cached_pull"),
    ):
        monkeypatch.setattr(
            module, name, lambda *a, _n=name, **k: touched.append(_n) or frozenset()
        )
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY"]))
    outcome = completed_run(router, webhook_url=None)
    assert warning_pairs(record_of(outcome)) == {(X, "SHNY")}
    assert touched == []
    assert _cache_row(db) == before
    assert router.violations == []


@pytest.mark.parametrize("exc", [requests.ConnectionError, requests.Timeout])
def test_alpaca_unreachable_ends_the_run_unavailable_with_no_verdict_and_no_post(router, exc):
    """S-2.9: nothing is dead or healthy from a run Alpaca never answered."""
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY"]))
    router.alpaca_by_symbol.clear()
    router.alpaca_default = lambda symbol: transport_error(exc)
    outcome = run_check(router)
    assert (outcome.status, outcome.completed) == ("unavailable", False)
    rec = record_of(outcome)
    assert (rec["status"], rec["completed"], rec["reason"]) == (
        "unavailable",
        False,
        "Alpaca unreachable",
    )
    assert "warnings" not in rec
    assert Counter(router.asset_symbols()) == {"SHNY": ALPACA_ATTEMPTS, "SPY": ALPACA_ATTEMPTS}
    assert router.of_kind("discord") == []


def test_unavailable_run_leaves_the_earlier_completed_result_in_place(router):
    """S-2.9 and S-4.6 runner leg: only completed runs post and carry the shown result."""
    roster(router, X)
    first = completed_run(router, webhook_url=None)
    router.alpaca_by_symbol.clear()
    router.alpaca_default = lambda symbol: transport_error()
    second = run_check(router, start=night(1))
    assert second.status == "unavailable"
    latest = runner.read_latest_runs()
    assert latest.latest == record_of(second)
    assert latest.latest_completed == record_of(first)
    assert warning_pairs(latest.latest_completed) == {(X, "SHNY")}
    assert router.of_kind("discord") == []


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        pytest.param(lambda s: transport_error(), "Alpaca unreachable", id="unreachable"),
        pytest.param(lambda s: alpaca_status(503), "Alpaca unavailable", id="unavailable"),
    ],
)
def test_five_consecutive_failed_tickers_stop_the_rest_of_the_alpaca_reads(router, script, reason):
    """S-2.9: an outage costs five tickers of attempts, not the whole ticker list."""
    roster(router, X)
    router.alpaca_by_symbol.clear()
    router.alpaca_default = script
    outcome = run_check(router)
    assert outcome.status == "unavailable"
    assert record_of(outcome)["reason"] == reason
    asked = tickers_of(t.captured_tree())
    assert len(asked) > ALPACA_STREAK_LIMIT
    assert router.asset_symbols() == [
        symbol for symbol in asked[:ALPACA_STREAK_LIMIT] for _ in range(ALPACA_ATTEMPTS)
    ]


def test_a_rate_limited_answer_does_not_count_toward_the_failure_streak(router):
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=[f"T{i}" for i in range(7)]))
    router.alpaca_by_symbol.clear()
    router.alpaca_default = lambda symbol: alpaca_status(429)
    outcome = run_check(router)
    assert outcome.status == "unavailable"
    assert record_of(outcome)["reason"] == "Alpaca rate limit"
    assert Counter(router.asset_symbols()) == {f"T{i}": ALPACA_ATTEMPTS for i in range(7)}


def test_an_answer_between_failures_resets_the_streak(router):
    roster(router, X)
    asked = tickers_of(t.captured_tree())
    router.alpaca_by_symbol.clear()
    scripted = {
        symbol: (
            t.healthy_reply(symbol)
            if i % ALPACA_STREAK_LIMIT == ALPACA_STREAK_LIMIT - 1
            else transport_error()
        )
        for i, symbol in enumerate(asked)
    }
    router.alpaca_default = lambda symbol: scripted[symbol]
    outcome = completed_run(router, webhook_url=None)
    failed = [s for i, s in enumerate(asked) if i % ALPACA_STREAK_LIMIT != ALPACA_STREAK_LIMIT - 1]
    assert set(router.asset_symbols()) == set(asked)
    assert Counter(router.asset_symbols())[failed[0]] == ALPACA_ATTEMPTS
    assert record_of(outcome)["not_checked"]["tickers"] == [
        {
            "symphony_id": X,
            "display_name": name_of(X),
            "reason": "Alpaca unreachable",
            "count": len(failed),
        }
    ]


@pytest.mark.parametrize("status", [401, 403])
def test_refused_credentials_stop_every_alpaca_read_and_end_the_run_unavailable(router, status):
    """S-2.10: no retry, no further ticker, no verdict."""
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY"]))
    router.alpaca_by_symbol.clear()
    refused = t.unauthorized_reply() if status == 401 else alpaca_status(403)
    router.alpaca_default = lambda symbol: refused
    outcome = run_check(router)
    assert outcome.status == "unavailable"
    rec = record_of(outcome)
    assert rec["reason"] == "Alpaca refused the credentials"
    assert "warnings" not in rec
    assert router.asset_symbols() == ["SHNY"]
    assert router.of_kind("discord") == []


def test_credentials_refused_after_some_answers_leaves_the_rest_not_checked(router):
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY"]))
    router.set_alpaca("SPY", t.unauthorized_reply())
    outcome = completed_run(router, webhook_url=None)
    rec = record_of(outcome)
    assert router.asset_symbols() == ["SHNY", "SPY"]
    assert warning_pairs(rec) == {(X, "SHNY")}
    assert rec["not_checked"]["tickers"] == [
        {
            "symphony_id": X,
            "display_name": name_of(X),
            "reason": "Alpaca refused the credentials",
            "count": 1,
        }
    ]


def test_unanswered_tickers_are_counted_per_symphony_and_reason_and_never_judged(router):
    """S-2.11: 429 and 5xx leave their tickers not checked; the answered ones are judged."""
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY", "QQQ"]))
    router.set_alpaca("QQQ", alpaca_status(429))
    router.set_alpaca("SPY", alpaca_status(503))
    outcome = completed_run(router)
    rec = record_of(outcome)
    assert warning_pairs(rec) == {(X, "SHNY")}
    counted = sorted(rec["not_checked"]["tickers"], key=lambda e: e["reason"])
    assert counted == [
        {"symphony_id": X, "display_name": name_of(X), "reason": "Alpaca rate limit", "count": 1},
        {"symphony_id": X, "display_name": name_of(X), "reason": "Alpaca unavailable", "count": 1},
    ]
    assert Counter(router.asset_symbols()) == {
        "SHNY": 1,
        "QQQ": ALPACA_ATTEMPTS,
        "SPY": ALPACA_ATTEMPTS,
    }


def test_run_where_every_answer_is_rate_limited_reads_unavailable_with_that_reason(router):
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY"]))
    router.alpaca_by_symbol.clear()
    router.alpaca_default = lambda symbol: alpaca_status(429)
    outcome = run_check(router)
    assert outcome.status == "unavailable"
    assert record_of(outcome)["reason"] == "Alpaca rate limit"


@pytest.mark.parametrize(
    ("first", "second", "reason"),
    [
        pytest.param(
            lambda: alpaca_status(429),
            lambda: alpaca_status(503),
            "Alpaca rate limit",
            id="rate_limit_then_unavailable",
        ),
        pytest.param(
            lambda: alpaca_status(503),
            lambda: transport_error(),
            "Alpaca unavailable",
            id="unavailable_then_unreachable",
        ),
        pytest.param(
            lambda: transport_error(),
            lambda: alpaca_status(503),
            "Alpaca unreachable",
            id="unreachable_then_unavailable",
        ),
        pytest.param(
            lambda: alpaca_status(503),
            t.unauthorized_reply,
            "Alpaca refused the credentials",
            id="any_refusal_wins",
        ),
    ],
)
def test_run_with_no_answer_reads_the_first_failed_tickers_reason_unless_one_was_refused(
    router, first, second, reason
):
    """Only transport errors read unreachable, any refusal reads refused, else the first
    failed ticker's reason (tickers are asked in sorted order, AAA before BBB)."""
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["AAA", "BBB"]))
    router.alpaca_by_symbol.clear()
    router.alpaca_by_symbol["AAA"] = first()
    router.alpaca_by_symbol["BBB"] = second()
    outcome = run_check(router)
    assert (outcome.status, outcome.completed) == ("unavailable", False)
    assert record_of(outcome)["reason"] == reason
    assert router.asset_symbols()[0] == "AAA"
    assert router.of_kind("discord") == []


@pytest.mark.parametrize(
    ("retry_after", "waited"),
    [("7", 7), ("999", RETRY_CAP), (None, RETRY_BACKOFF), ("soon", RETRY_BACKOFF)],
)
def test_one_retry_waits_the_clamped_retry_after_and_judges_the_second_answer(
    router, retry_after, waited
):
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY"]))
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    router.set_alpaca("SHNY", [alpaca_status(429, **headers), t.shny_reply()])
    outcome = completed_run(router, webhook_url=None)
    assert warning_pairs(record_of(outcome)) == {(X, "SHNY")}
    assert router.asset_symbols() == ["SHNY", "SHNY"]
    assert float(waited) in router.sleeps


def test_alpaca_reads_are_paced_after_every_attempt(router):
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY"]))
    router.set_alpaca("SPY", [alpaca_status(503), t.healthy_reply("SPY")])
    completed_run(router, webhook_url=None)
    seen = 0
    for i, event in enumerate(router.events):
        if event[0] == "request" and event[1].kind == "alpaca_asset":
            seen += 1
            sleeps = []
            for later in router.events[i + 1 :]:
                if later[0] == "request":
                    break
                sleeps.append(later[1])
            assert ALPACA_SPACING in sleeps, f"no {ALPACA_SPACING} s sleep after {event[1].path}"
    assert seen == 3


@pytest.mark.parametrize(
    ("status", "body"),
    [
        pytest.param(302, "", id="redirect_not_followed"),
        pytest.param(400, {"message": "derived"}, id="bad_request"),
        pytest.param(422, {"message": "derived"}, id="unprocessable"),
    ],
)
def test_other_statuses_leave_the_ticker_not_checked_and_never_dead(router, status, body):
    """S-2.16 runner leg: never read as not listed, never retried."""
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY"]))
    router.set_alpaca(
        "SPY", h.Reply(status=status, body=body, headers={"Location": "https://x.invalid/"})
    )
    outcome = completed_run(router, webhook_url=None)
    rec = record_of(outcome)
    assert warning_pairs(rec) == {(X, "SHNY")}
    assert rec["not_checked"]["tickers"] == [
        {
            "symphony_id": X,
            "display_name": name_of(X),
            "reason": "Alpaca rejected the request",
            "count": 1,
        }
    ]
    assert router.asset_symbols().count("SPY") == 1


def test_asset_not_found_and_not_tradable_are_dead_pairs_with_their_own_reason(router):
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SPY", "ZZZZQQ", "AKAN"]))
    router.set_alpaca("ZZZZQQ", t.not_found_reply())
    router.set_alpaca("AKAN", t.active_not_tradable_reply())
    outcome = completed_run(router, webhook_url=None)
    reasons = {w["ticker"]: w["reason"] for w in record_of(outcome)["warnings"]}
    assert reasons == {"ZZZZQQ": NOT_LISTED, "AKAN": NOT_TRADABLE}


@pytest.mark.parametrize(
    ("key", "secret"),
    [
        ("", t.ALPACA_SECRET),
        (t.ALPACA_KEY, ""),
        ("", ""),
        (None, t.ALPACA_SECRET),
        (t.ALPACA_KEY, None),
    ],
)
def test_missing_alpaca_credentials_end_the_run_unavailable_before_any_request(router, key, secret):
    """S-2.14."""
    roster(router, X)
    outcome = run_check(router, alpaca_key=key, alpaca_secret=secret)
    assert (outcome.status, outcome.completed) == ("unavailable", False)
    assert record_of(outcome)["reason"] == "Alpaca credentials not configured"
    assert router.requests == []


def test_no_account_ends_the_run_unavailable_before_any_request_and_before_the_key_check(router):
    """S-4.11 runner leg, and the account check comes first."""
    outcome = run_check(router, account_ids=[], alpaca_key="", alpaca_secret="")
    assert (outcome.status, outcome.completed) == ("unavailable", False)
    assert record_of(outcome)["reason"] == "no Composer account configured"
    assert router.requests == []


# ---------------------------------------------------------------------------
# Not checked, with the reason (AC-4 runner legs)
# ---------------------------------------------------------------------------

SCORE_FAILURES = [
    pytest.param(lambda: h.error_reply(503), "Composer unavailable", id="5xx"),
    pytest.param(lambda: h.raising(requests.Timeout("slow")), "Composer unavailable", id="timeout"),
    pytest.param(lambda: h.error_reply(404), "definition not found", id="404"),
    pytest.param(lambda: h.error_reply(401), "Composer refused the request", id="401"),
    pytest.param(
        lambda: h.Reply(status=403, body=h.error_body_for(401)),
        "Composer refused the request",
        id="403",
    ),
    pytest.param(h.non_json_200, "definition unreadable", id="non_json_body"),
    pytest.param(lambda: h.Reply(body=[]), "definition unreadable", id="json_list"),
    pytest.param(lambda: h.redirect_reply(302), "Composer rejected the request", id="redirect"),
    pytest.param(lambda: h.error_reply(400), "Composer rejected the request", id="400"),
    pytest.param(lambda: h.error_reply(422), "Composer rejected the request", id="422"),
]


@pytest.mark.parametrize(("failure", "reason"), SCORE_FAILURES)
def test_unreadable_tree_is_one_symphony_not_checked_and_the_others_are_judged(
    router, failure, reason
):
    roster(router, X, Y)
    router.set_tree(X, failure())
    outcome = completed_run(router, webhook_url=None)
    rec = record_of(outcome)
    assert rec["not_checked"]["symphonies"] == [not_checked_symphony(X, reason)]
    assert warning_pairs(rec) == {(Y, "SHNY")}
    assert rec["counts"]["symphonies_judged"] == 1


def test_symphony_id_that_is_not_plain_is_never_requested(router):
    """S-4.16 runner leg."""
    bad_id = "a.b/c"
    router.set_roster(t.roster_entry(X, id=bad_id), t.roster_entry(Y))
    outcome = completed_run(router, webhook_url=None)
    rec = record_of(outcome)
    assert router.score_ids() == [Y]
    assert router.violations == []
    assert rec["not_checked"]["symphonies"] == [
        {"symphony_id": bad_id, "display_name": name_of(X), "reason": "symphony id not usable"}
    ]


ROSTER_FAILURES = [
    pytest.param(lambda: h.error_reply(503), "Composer unavailable", id="5xx"),
    pytest.param(
        lambda: h.raising(requests.ConnectionError("down")), "Composer unavailable", id="net"
    ),
    pytest.param(lambda: h.error_reply(401), "Composer refused the request", id="401"),
    pytest.param(
        lambda: h.Reply(status=403, body=h.error_body_for(401)),
        "Composer refused the request",
        id="403",
    ),
    pytest.param(lambda: h.error_reply(404), "account not found", id="404"),
    pytest.param(lambda: h.redirect_reply(302), "Composer rejected the request", id="redirect"),
    pytest.param(lambda: h.error_reply(400), "Composer rejected the request", id="400"),
    pytest.param(lambda: h.error_reply(422), "Composer rejected the request", id="422"),
    pytest.param(
        lambda: h.Reply(body={"not": "a roster"}), "Composer response unreadable", id="shape"
    ),
    pytest.param(h.non_json_200, "Composer response unreadable", id="non_json"),
]


@pytest.mark.parametrize(("failure", "reason"), ROSTER_FAILURES)
def test_account_whose_roster_fails_is_named_and_the_other_accounts_are_judged(
    router, failure, reason
):
    router.set_roster(t.roster_entry(Y), account=h.ACCOUNT_ID)
    router.statistics_by_account[h.SECOND_ACCOUNT_ID] = failure()
    outcome = completed_run(
        router, webhook_url=None, account_ids=[h.SECOND_ACCOUNT_ID, h.ACCOUNT_ID]
    )
    rec = record_of(outcome)
    assert rec["not_checked"]["accounts"] == [
        {"label": LABELS[h.SECOND_ACCOUNT_ID], "reason": reason}
    ]
    assert warning_pairs(rec) == {(Y, "SHNY")}
    assert rec["counts"]["accounts_read"] == 1


def test_account_id_that_is_not_plain_is_refused_without_a_request(router):
    router.set_roster(t.roster_entry(Y), account=h.ACCOUNT_ID)
    labels = {"../x": "Individual", h.ACCOUNT_ID: "Roth IRA"}
    outcome = completed_run(
        router, webhook_url=None, account_ids=["../x", h.ACCOUNT_ID], account_labels=labels
    )
    assert record_of(outcome)["not_checked"]["accounts"] == [
        {"label": "Individual", "reason": "Composer response unreadable"}
    ]
    assert router.violations == []
    assert [r.match["account"] for r in router.of_kind("statistics")] == [h.ACCOUNT_ID]


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        pytest.param(lambda: h.error_reply(503), "Composer unavailable", id="one_class"),
        pytest.param(lambda: h.error_reply(401), "Composer refused the request", id="refused"),
    ],
)
def test_run_with_no_readable_roster_is_unavailable_with_the_shared_reason(router, failure, reason):
    router.statistics_default = failure()
    outcome = run_check(router)
    assert (outcome.status, outcome.completed) == ("unavailable", False)
    rec = record_of(outcome)
    assert rec["reason"] == reason
    assert "warnings" not in rec
    assert router.asset_requests() == []


def test_run_where_accounts_fail_for_different_reasons_gets_the_generic_reason(router):
    router.statistics_by_account[h.ACCOUNT_ID] = h.error_reply(503)
    router.statistics_by_account[h.SECOND_ACCOUNT_ID] = h.error_reply(404)
    outcome = run_check(router, account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])
    assert outcome.status == "unavailable"
    assert record_of(outcome)["reason"] == "no account's symphony list could be read"


def test_roster_that_lists_no_symphony_completes_with_nothing_to_show(router):
    """S-4.12 runner leg."""
    router.set_roster()
    outcome = completed_run(router)
    rec = record_of(outcome)
    assert rec["warnings"] == []
    assert rec["not_checked"] == {"accounts": [], "symphonies": [], "tickers": []}
    assert router.score_ids() == [] and router.asset_requests() == []
    assert router.of_kind("discord") == []


def test_run_where_every_tree_read_fails_still_completes_naming_every_symphony(router):
    """A run that reached every symphony, each with a cause, completes."""
    roster(router, X, Y, t.CORPORATE_CHAOS_ID)
    router.definition_default = h.error_reply(503)
    outcome = completed_run(router)
    rec = record_of(outcome)
    assert [e["symphony_id"] for e in rec["not_checked"]["symphonies"]] == [
        X,
        Y,
        t.CORPORATE_CHAOS_ID,
    ]
    assert {e["reason"] for e in rec["not_checked"]["symphonies"]} == {"Composer unavailable"}
    assert rec["counts"]["symphonies_judged"] == 0
    assert router.asset_requests() == []


# ---------------------------------------------------------------------------
# Discord posting and once-only announcements (AC-5)
# ---------------------------------------------------------------------------


def test_new_dead_pair_is_posted_once_in_the_wording_of_the_dashboard_line(router):
    """S-5.1."""
    roster(router, X)
    outcome = completed_run(router)
    requests_made = router.of_kind("discord")
    assert len(requests_made) == 1
    request = requests_made[0]
    assert request.url == t.WEBHOOK_URL
    assert request.timeout == REQUEST_TIMEOUT
    assert request.kwargs.get("allow_redirects") is False
    body = request.body_json()
    assert set(body) == {"content", "allowed_mentions"}
    assert body["allowed_mentions"] == {"parse": []}
    assert lines_of(body["content"]) == [LEAD, shny_line(X)]
    assert announcements() == [{"run_id": outcome.run_id, "pairs": [[X, "SHNY"]]}]
    row = rows(ANNOUNCEMENT_ROLE)[0]
    assert (row["subject_id"], row["verdict"]) == (outcome.run_id, "announced")
    assert row["is_advisory_only"] == 1


def test_run_row_is_written_before_the_first_post(router, monkeypatch):
    roster(router, X)
    order: list[tuple[str, int]] = []
    real = database.insert_advisor_observation

    def recording(**kwargs):
        order.append((kwargs["advisor_role"], len(router.of_kind("discord"))))
        return real(**kwargs)

    monkeypatch.setattr(database, "insert_advisor_observation", recording)
    completed_run(router)
    # (role written, posts made so far): the run row first, the announcement after the post.
    assert order == [(RUN_ROLE, 0), (ANNOUNCEMENT_ROLE, 1)]


def test_pair_announced_on_an_earlier_night_is_not_posted_again(router):
    """S-5.2: the next run still finds it dead, carries it as announced, and posts nothing."""
    roster(router, X)
    completed_run(router)
    assert len(router.discord_posts()) == 1
    second = completed_run(router, start=night(1))
    assert len(router.discord_posts()) == 1
    warning = record_of(second)["warnings"][0]
    assert (warning["symphony_id"], warning["ticker"], warning["announced"]) == (X, "SHNY", True)
    assert warning["found_at"] == record_of(second)["ended_at"]
    third = completed_run(router, start=night(2))
    assert len(router.discord_posts()) == 1
    assert record_of(third)["warnings"][0]["announced"] is True


def test_two_new_pairs_of_one_run_are_named_in_one_message_and_announced_once(router):
    """S-5.3."""
    roster(router, X, Y)
    outcome = completed_run(router)
    posts = router.discord_posts()
    assert len(posts) == 1
    content_lines = lines_of(posts[0]["content"])
    assert content_lines[0] == LEAD
    assert sorted(content_lines[1:]) == sorted([shny_line(X), shny_line(Y)])
    assert [rec["run_id"] for rec in announcements()] == [outcome.run_id]
    assert announced_pairs() == {(X, "SHNY"), (Y, "SHNY")}
    completed_run(router, start=night(1))
    assert len(router.discord_posts()) == 1


def test_only_the_new_pair_is_named_when_an_older_one_was_announced(router):
    """S-5.4."""
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY"]))
    completed_run(router)
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY", "ZZZZQQ"]))
    router.set_alpaca("ZZZZQQ", t.not_found_reply())
    second = completed_run(router, start=night(1))
    posts = router.discord_posts()
    assert len(posts) == 2
    new_lines = lines_of(posts[1]["content"])
    assert new_lines[0] == LEAD
    assert len(new_lines) == 2
    assert f"({X}): ZZZZQQ is {NOT_LISTED}." in new_lines[1]
    assert "SHNY" not in posts[1]["content"]
    assert announcements()[0] == {"run_id": second.run_id, "pairs": [[X, "ZZZZQQ"]]}


def test_pair_that_cleared_and_died_again_is_announced_again(router):
    """S-5.5: clearing resets the once-only record, with no message for the clearing (S-6.7)."""
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY"]))
    completed_run(router)
    assert len(router.discord_posts()) == 1
    router.set_alpaca("SHNY", t.healthy_reply("SHNY"))
    cleared = completed_run(router, start=night(1))
    assert record_of(cleared)["warnings"] == []
    assert len(router.discord_posts()) == 1
    router.set_alpaca("SHNY", t.shny_reply())
    completed_run(router, start=night(2))
    posts = router.discord_posts()
    assert len(posts) == 2
    assert f"({X}): SHNY is {DEAD_SHNY}." in posts[1]["content"]


def test_announcement_from_before_a_clearing_does_not_suppress_a_later_post(router):
    """The pair was announced, cleared, found dead again and its post failed; the next run
    must post it: only announcement rows of the run whose warnings are carried count, never
    an older row that predates the clearing."""
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY"]))
    first = completed_run(router)
    assert [rec["run_id"] for rec in announcements()] == [first.run_id]
    router.set_alpaca("SHNY", t.healthy_reply("SHNY"))
    assert record_of(completed_run(router, start=night(1)))["warnings"] == []
    router.set_alpaca("SHNY", t.shny_reply())
    router.set_discord(h.Reply(status=204, body=None), h.Reply(status=500, body={"message": "x"}))
    third = completed_run(router, start=night(2))
    assert warning_pairs(record_of(third)) == {(X, "SHNY")}
    assert len(router.of_kind("discord")) == 2
    assert [rec["run_id"] for rec in announcements()] == [first.run_id]
    router.discord_replies.clear()
    fourth = completed_run(router, start=night(3))
    assert len(router.of_kind("discord")) == 3
    assert f"({X}): SHNY is {DEAD_SHNY}." in router.discord_posts()[2]["content"]
    assert [rec["run_id"] for rec in announcements()][0] == fourth.run_id


def test_announcement_of_the_previous_completed_run_still_counts_across_a_run_that_did_not_complete(
    router,
):
    """A guard for the rule above from the other side: a run that ended unavailable between two
    completed ones is not the run whose announcements count; the previous completed run is."""
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY"]))
    completed_run(router)
    assert len(router.discord_posts()) == 1
    router.statistics_default = h.error_reply(503)
    assert run_check(router, start=night(1)).status == "unavailable"
    roster(router, X)
    third = completed_run(router, start=night(2))
    assert len(router.discord_posts()) == 1
    assert warning_pairs(record_of(third)) == {(X, "SHNY")}
    assert record_of(third)["warnings"][0]["announced"] is True


def test_a_carried_unannounced_pair_of_a_symphony_the_run_could_not_judge_waits_for_the_run_that_judges_it(
    router,
):
    """Only pairs found dead in this run and not yet announced are posted. The first run finds
    both pairs dead and its post fails. The second cannot read Y's tree: Y's carried pair keeps
    the earlier time and is not posted; X's is. The third judges Y dead and posts it."""
    ids = [X, Y]
    roster(router, *ids)
    router.set_discord(h.Reply(status=500, body={"message": "x"}))
    first = completed_run(router)
    assert warning_pairs(record_of(first)) == {(X, "SHNY"), (Y, "SHNY")}
    assert announcements() == []
    router.discord_replies.clear()
    router.set_tree(Y, h.error_reply(503))
    second = completed_run(router, start=night(1))
    rec = record_of(second)
    carried = next(w for w in rec["warnings"] if w["symphony_id"] == Y)
    assert carried["found_at"] == record_of(first)["ended_at"]
    assert carried["announced"] is False
    posts = router.discord_posts()
    assert len(posts) == 2
    assert lines_of(posts[1]["content"]) == [LEAD, shny_line(X)]
    assert Y not in posts[1]["content"]
    assert announcements() == [{"run_id": second.run_id, "pairs": [[X, "SHNY"]]}]
    router.set_tree(Y, h.Reply(body=t.captured_tree()))
    third = completed_run(router, start=night(2))
    posts = router.discord_posts()
    assert len(posts) == 3
    assert lines_of(posts[2]["content"]) == [LEAD, shny_line(Y)]
    assert announced_pairs() == {(X, "SHNY"), (Y, "SHNY")}
    assert announcements()[0] == {"run_id": third.run_id, "pairs": [[Y, "SHNY"]]}


def test_clearing_by_removing_the_ticker_from_the_tree_posts_nothing(router):
    """S-6.7."""
    roster(router, X)
    completed_run(router)
    router.set_tree(X, t.tree_with(assets=["SPY"]))
    cleared = completed_run(router, start=night(1))
    assert record_of(cleared)["warnings"] == []
    assert len(router.discord_posts()) == 1


@pytest.mark.parametrize(
    ("reply", "logged"),
    [
        pytest.param(h.Reply(status=500, body={"message": "derived"}), "500", id="server_error"),
        pytest.param(
            h.Reply(
                status=429,
                body={
                    "message": "You are being rate limited.",
                    "retry_after": 0.5,
                    "global": False,
                },
            ),
            "429",
            id="rate_limited",
        ),
        pytest.param(h.raising(requests.Timeout(f"slow {t.WEBHOOK_URL}")), "Timeout", id="timeout"),
    ],
)
def test_failed_post_is_not_recorded_and_the_next_completed_run_posts_the_pair(
    router, caplog, reply, logged
):
    """S-5.6: the log names the status or exception class only."""
    roster(router, X)
    router.set_discord(reply)
    with caplog.at_level(logging.DEBUG):
        first = completed_run(router)
    assert len(router.of_kind("discord")) == 1
    assert announcements() == []
    assert any(r.levelno >= logging.WARNING and logged in r.getMessage() for r in caplog.records), (
        "the failed post is logged with its status or exception class"
    )
    assert forbidden_in(caplog.text) == []
    assert record_of(first)["status"] == "completed"
    second = completed_run(router, start=night(1))
    assert len(router.of_kind("discord")) == 2
    assert announcements() == [{"run_id": second.run_id, "pairs": [[X, "SHNY"]]}]


def test_no_webhook_posts_nothing_records_nothing_and_a_later_webhook_posts_the_pair(router):
    """S-5.7."""
    roster(router, X)
    for missing in (None, ""):
        completed_run(router, webhook_url=missing, start=night(len(rows(RUN_ROLE))))
    assert router.of_kind("discord") == []
    assert announcements() == []
    third = completed_run(router, start=night(5))
    assert len(router.discord_posts()) == 1
    assert announcements() == [{"run_id": third.run_id, "pairs": [[X, "SHNY"]]}]


def test_message_names_only_what_the_run_judged(router):
    """S-5.8: a symphony the run could not read is not mentioned."""
    roster(router, X, Y)
    router.set_tree(Y, h.error_reply(503))
    outcome = completed_run(router)
    posts = router.discord_posts()
    assert len(posts) == 1
    assert lines_of(posts[0]["content"]) == [LEAD, shny_line(X)]
    assert Y not in posts[0]["content"] and "Trolling" not in posts[0]["content"]
    assert announced_pairs() == {(X, "SHNY")}
    assert record_of(outcome)["not_checked"]["symphonies"] == [
        not_checked_symphony(Y, "Composer unavailable")
    ]


def test_accepted_post_whose_announcement_row_was_lost_is_posted_once_more(router, monkeypatch):
    """S-5.9: Discord accepted the message and the daemon stopped (simulated with SystemExit)
    before the announcement was stored; the next completed run posts the pair again, once."""
    roster(router, X)
    real = database.insert_advisor_observation
    armed = {"on": True}

    def stop_before_the_announcement(**kwargs):
        if kwargs["advisor_role"] == ANNOUNCEMENT_ROLE and armed["on"]:
            armed["on"] = False
            raise SystemExit(1)
        return real(**kwargs)

    monkeypatch.setattr(database, "insert_advisor_observation", stop_before_the_announcement)
    with pytest.raises(SystemExit):
        run_check(router)
    assert len(router.discord_posts()) == 1
    assert announcements() == []
    second = completed_run(router, start=night(1))
    assert len(router.discord_posts()) == 2
    assert announcements() == [{"run_id": second.run_id, "pairs": [[X, "SHNY"]]}]
    completed_run(router, start=night(2))
    assert len(router.discord_posts()) == 2


def test_renamed_symphony_is_not_announced_again(router):
    """S-5.10: the pair is keyed by the Composer id."""
    roster(router, X)
    completed_run(router)
    roster(router, X, **{X: {"name": "A new name in Composer"}})
    second = completed_run(router, start=night(1))
    assert len(router.discord_posts()) == 1
    assert record_of(second)["warnings"][0]["display_name"] == "A new name in Composer"


def _many_dead_pairs(router, symphonies=11):
    ids = t.roster_ids()[:symphonies]
    roster(router, *ids)
    tree = t.tree_with(assets=["SHNY", "ZZZZQQ", "AKAN", "SPY"])
    for symphony_id in ids:
        router.set_tree(symphony_id, tree)
    router.set_alpaca("ZZZZQQ", t.not_found_reply())
    router.set_alpaca("AKAN", t.active_not_tradable_reply())
    expected = set()
    for symphony_id in ids:
        for ticker, reason in (("SHNY", DEAD_SHNY), ("ZZZZQQ", NOT_LISTED), ("AKAN", NOT_TRADABLE)):
            expected.add(
                pair_line(name_of(symphony_id), symphony_id, ticker, reason, (1, 0), NOT_HELD)
            )
    return ids, expected


def test_many_pairs_are_split_across_messages_each_within_the_limit(router):
    """S-5.1 with a roster-sized backlog: every pair named once, one announcement per message."""
    ids, expected = _many_dead_pairs(router)
    outcome = completed_run(router)
    posts = router.discord_posts()
    assert len(posts) >= 2
    named = []
    for post in posts:
        assert len(post["content"]) <= DISCORD_LIMIT
        assert post["allowed_mentions"] == {"parse": []}
        body_lines = lines_of(post["content"])
        assert body_lines[0] == LEAD
        named.extend(body_lines[1:])
    assert Counter(named) == Counter(expected)
    rows_written = announcements()
    assert len(rows_written) == len(posts)
    assert all(rec["run_id"] == outcome.run_id for rec in rows_written)
    announced = Counter(tuple(pair) for rec in rows_written for pair in rec["pairs"])
    assert set(announced) == {(i, tk) for i in ids for tk in ("SHNY", "ZZZZQQ", "AKAN")}
    assert set(announced.values()) == {1}


def test_posts_of_one_run_are_spaced(router):
    _many_dead_pairs(router)
    completed_run(router)
    posts = [i for i, e in enumerate(router.events) if e[0] == "request" and e[1].kind == "discord"]
    assert len(posts) >= 2
    for earlier, later in zip(posts, posts[1:], strict=False):
        between = sum(e[1] for e in router.events[earlier + 1 : later] if e[0] == "sleep")
        assert between >= DISCORD_SPACING


def test_first_failed_post_ends_the_posting_and_only_the_accepted_messages_are_recorded(router):
    """S-5.6 for several messages: the second message fails, nothing later is attempted, and the
    next run posts exactly the pairs that were not accepted."""
    ids, expected = _many_dead_pairs(router)
    router.set_discord(h.Reply(status=204, body=None), h.Reply(status=500, body={"message": "x"}))
    first = completed_run(router)
    assert len(router.of_kind("discord")) == 2
    accepted_lines = lines_of(router.discord_posts()[0]["content"])[1:]
    assert [rec["run_id"] for rec in announcements()] == [first.run_id]
    assert len(announced_pairs()) == len(accepted_lines)
    router.discord_replies.clear()
    before = len(router.of_kind("discord"))
    completed_run(router, start=night(1))
    later_lines = [
        line for post in router.discord_posts()[before:] for line in lines_of(post["content"])[1:]
    ]
    assert Counter(later_lines) == Counter(expected) - Counter(accepted_lines)
    assert len(announced_pairs()) == len(expected)


def test_names_and_tickers_reach_discord_only_as_literal_text(router):
    """S-9.3 runner leg: no mention can fire and the stored name stays unescaped."""
    hostile = "Alpha @everyone <@123456789012345678> *b* _i_ ~s~ `c` | # - [l](u) (p): x\\y"
    roster(router, X, **{X: {"name": hostile}})
    outcome = completed_run(router)
    post = router.discord_posts()[0]
    assert post["allowed_mentions"] == {"parse": []}
    assert lines_of(post["content"]) == [LEAD, shny_line(X, name=hostile)]
    assert not re.search(r"(?<!\\)[@<>]", post["content"])
    assert record_of(outcome)["warnings"][0]["display_name"] == hostile


# ---------------------------------------------------------------------------
# Warnings carried across runs (AC-4 and AC-6 runner legs)
# ---------------------------------------------------------------------------


def _first_run_with_a_warning(router, *ids):
    roster(router, *ids)
    first = completed_run(router)
    assert warning_pairs(record_of(first)) == {(i, "SHNY") for i in ids}
    return first


def test_symphony_the_run_could_not_read_keeps_its_warning_from_the_earlier_run(router):
    """S-6.3 and S-4.6: carried with the earlier time, no repeated message."""
    first = _first_run_with_a_warning(router, X)
    router.set_tree(X, h.error_reply(503))
    second = completed_run(router, start=night(1))
    rec = record_of(second)
    warning = rec["warnings"][0]
    assert (warning["symphony_id"], warning["ticker"]) == (X, "SHNY")
    assert warning["found_at"] == record_of(first)["ended_at"]
    assert warning["announced"] is True
    assert rec["not_checked"]["symphonies"] == [not_checked_symphony(X, "Composer unavailable")]
    assert len(router.discord_posts()) == 1


def test_ticker_the_run_could_not_ask_about_keeps_its_warning(router):
    """S-6.4."""
    first = _first_run_with_a_warning(router, X)
    router.set_alpaca("SHNY", alpaca_status(503))
    second = completed_run(router, start=night(1))
    warning = record_of(second)["warnings"][0]
    assert (warning["symphony_id"], warning["ticker"]) == (X, "SHNY")
    assert warning["found_at"] == record_of(first)["ended_at"]
    assert len(router.discord_posts()) == 1


def test_healthy_answer_clears_the_warning(router):
    """S-6.1."""
    _first_run_with_a_warning(router, X)
    router.set_alpaca("SHNY", t.healthy_reply("SHNY"))
    second = completed_run(router, start=night(1))
    assert record_of(second)["warnings"] == []


def test_ticker_removed_from_the_tree_clears_the_warning_only_for_that_symphony(router):
    """S-6.2 and S-6.6."""
    _first_run_with_a_warning(router, X, Y)
    router.set_tree(X, t.tree_with(assets=["SPY"]))
    second = completed_run(router, start=night(1))
    assert warning_pairs(record_of(second)) == {(Y, "SHNY")}


def test_symphony_gone_from_every_roster_read_loses_its_warning(router):
    """S-6.5."""
    _first_run_with_a_warning(router, X, Y)
    roster(router, Y)
    second = completed_run(router, start=night(1))
    assert warning_pairs(record_of(second)) == {(Y, "SHNY")}


def test_symphony_absent_while_a_roster_failed_keeps_its_warning(router):
    first_roster = [t.roster_entry(X)]
    router.set_roster(*first_roster, account=h.ACCOUNT_ID)
    router.set_roster(t.roster_entry(Y), account=h.SECOND_ACCOUNT_ID)
    ids = [h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID]
    completed_run(router, account_ids=ids)
    router.statistics_by_account[h.ACCOUNT_ID] = h.error_reply(503)
    second = completed_run(router, account_ids=ids, start=night(1))
    rec = record_of(second)
    assert warning_pairs(rec) == {(X, "SHNY"), (Y, "SHNY")}
    assert rec["not_checked"]["accounts"] == [
        {"label": LABELS[h.ACCOUNT_ID], "reason": "Composer unavailable"}
    ]


# ---------------------------------------------------------------------------
# The Composer gate, single stop conditions and the request budget (AC-7)
# ---------------------------------------------------------------------------


def test_run_started_while_the_market_is_open_is_skipped_before_any_request(router):
    """S-7.2 runner leg: the previous completed result stays the shown one."""
    roster(router, X)
    first = completed_run(router, webhook_url=None)
    before = len(router.requests)
    outcome = run_check(router, start=IN_SESSION)
    assert (outcome.status, outcome.completed) == ("skipped", False)
    rec = record_of(outcome)
    assert rec["reason"] == "market open"
    assert "warnings" not in rec
    assert len(router.requests) == before
    latest = runner.read_latest_runs()
    assert latest.latest == rec
    assert latest.latest_completed == record_of(first)
    assert router.of_kind("discord") == []


def test_market_that_opens_mid_run_stops_composer_reads_and_names_the_symphonies_left(router):
    """S-7.3: no Composer request after the open; judged symphonies still count; Alpaca goes on."""
    ids = [X, Y, t.CORPORATE_CHAOS_ID]
    roster(router, *ids)
    clock = t.FlippingClock(router, t.NIGHT, JUST_AFTER_OPEN, after_requests=3)
    outcome = completed_run(router, clock=clock)
    rec = record_of(outcome)
    assert len(router.composer_requests()) == 3
    assert router.score_ids() == ids[:2]
    assert rec["not_checked"]["symphonies"] == [not_checked_symphony(ids[2], "market opened")]
    assert warning_pairs(rec) == {(X, "SHNY"), (Y, "SHNY")}
    assert router.asset_symbols() == tickers_of(t.captured_tree())


def test_market_that_opens_while_waiting_for_another_job_skips_the_run(router):
    roster(router, X)
    outcome = run_check(router, start=JUST_BEFORE_OPEN, other_reader_running=lambda: True)
    assert (outcome.status, record_of(outcome)["reason"]) == ("skipped", "market open")
    assert router.requests == []


def test_run_waits_for_another_composer_job_in_poll_steps_then_reads(router):
    """S-7.5 runner leg."""
    roster(router, X)

    def busy():  # three 15 second polls of recorded sleep, then idle
        return sum(router.sleeps) < 40

    completed_run(router, other_reader_running=busy, webhook_url=None)
    assert router.sleeps_before_first_request() == [POLL_SECONDS] * 3


def test_run_still_blocked_after_the_wait_limit_is_skipped_before_any_request(router):
    roster(router, X)
    outcome = run_check(router, other_reader_running=lambda: True)
    assert (outcome.status, outcome.completed) == ("skipped", False)
    assert record_of(outcome)["reason"] == "another Composer job was still running"
    assert router.requests == []
    assert set(router.sleeps) == {POLL_SECONDS}
    assert (
        OTHER_READER_LIMIT_SECONDS
        <= sum(router.sleeps)
        <= OTHER_READER_LIMIT_SECONDS + POLL_SECONDS
    )


def test_other_job_that_starts_mid_run_stops_the_reads_after_the_wait_limit(router):
    ids = [X, Y, t.CORPORATE_CHAOS_ID]
    roster(router, *ids)
    outcome = completed_run(
        router, other_reader_running=lambda: bool(router.of_kind("score")), webhook_url=None
    )
    rec = record_of(outcome)
    assert router.score_ids() == ids[:1]
    assert rec["not_checked"]["symphonies"] == [
        not_checked_symphony(i, "another Composer job was running") for i in ids[1:]
    ]
    assert warning_pairs(rec) == {(X, "SHNY")}
    assert (
        sum(s for s in router.sleeps if s == POLL_SECONDS)
        >= OTHER_READER_LIMIT_SECONDS - 3 * COMPOSER_SPACING
    )


def test_rate_limited_roster_read_stops_every_further_composer_request(router):
    """S-7.6 and S-4.21: the first 429 on a roster ends Composer reads."""
    router.statistics_by_account[h.ACCOUNT_ID] = h.error_reply(429)
    outcome = run_check(router, account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])
    assert outcome.status == "unavailable"
    assert record_of(outcome)["reason"] == "Composer rate limit"
    assert len(router.composer_requests()) == 1


def test_rate_limit_on_the_second_account_leaves_the_first_accounts_symphonies_unread(router):
    router.set_roster(t.roster_entry(X), t.roster_entry(Y), account=h.ACCOUNT_ID)
    router.statistics_by_account[h.SECOND_ACCOUNT_ID] = h.error_reply(429)
    outcome = completed_run(
        router, webhook_url=None, account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID]
    )
    rec = record_of(outcome)
    assert len(router.composer_requests()) == 2
    assert rec["not_checked"]["accounts"] == [
        {"label": LABELS[h.SECOND_ACCOUNT_ID], "reason": "Composer rate limit"}
    ]
    assert rec["not_checked"]["symphonies"] == [
        not_checked_symphony(X, "Composer rate limit"),
        not_checked_symphony(Y, "Composer rate limit"),
    ]
    assert router.asset_requests() == []


@pytest.mark.parametrize(
    ("stop", "reason"),
    [
        pytest.param("market", "market opened", id="market_opens_after_the_first_roster"),
        pytest.param(
            "other_job", "another Composer job was running", id="other_job_after_the_first_roster"
        ),
    ],
)
def test_account_the_gate_stops_after_the_first_request_is_named_with_the_gate_reason(
    router, stop, reason
):
    """A gate stop that comes after a first Composer request is no skipped run: the account whose
    roster was never asked is recorded under accounts with the gate's own reason, and the
    symphonies of the roster that was read carry the same reason."""
    roster(router, X, Y)
    options = {"webhook_url": None, "account_ids": [h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID]}
    if stop == "market":
        options["clock"] = t.FlippingClock(router, t.NIGHT, JUST_AFTER_OPEN, after_requests=1)
    else:
        options["other_reader_running"] = lambda: bool(router.of_kind("statistics"))
    outcome = completed_run(router, **options)
    rec = record_of(outcome)
    assert len(router.composer_requests()) == 1
    assert rec["not_checked"]["accounts"] == [
        {"label": LABELS[h.SECOND_ACCOUNT_ID], "reason": reason}
    ]
    assert rec["not_checked"]["symphonies"] == [
        not_checked_symphony(X, reason),
        not_checked_symphony(Y, reason),
    ]
    assert rec["counts"]["accounts_read"] == 1
    assert rec["warnings"] == []
    assert router.asset_requests() == []


def test_rate_limited_score_read_stops_the_run_s_composer_requests(router):
    """S-7.6: the symphony answered 429 and every later one read Composer rate limit."""
    ids = [X, Y, t.CORPORATE_CHAOS_ID]
    roster(router, *ids)
    router.set_tree(Y, h.error_reply(429))
    outcome = completed_run(router, webhook_url=None)
    rec = record_of(outcome)
    assert len(router.composer_requests()) == 3
    assert rec["not_checked"]["symphonies"] == [
        not_checked_symphony(Y, "Composer rate limit"),
        not_checked_symphony(ids[2], "Composer rate limit"),
    ]
    assert warning_pairs(rec) == {(X, "SHNY")}


def test_a_run_of_eleven_trees_stays_within_ten_composer_requests_a_minute(router):
    """S-7.11: half-open 60 second windows over the recorded pacing sleeps."""
    roster(router, *t.roster_ids())
    completed_run(router, webhook_url=None)
    assert len(router.of_kind("score")) == len(t.roster_ids())
    h.assert_requests_spaced(router, COMPOSER_SPACING)
    times = t.request_times(router)
    assert len(times) == 1 + len(t.roster_ids())
    for start in times:
        in_window = [x for x in times if start <= x < start + 60]
        assert len(in_window) <= 10, (
            f"{len(in_window)} Composer requests in the minute from {start}"
        )


def test_interrupted_run_leaves_no_row_no_post_and_the_earlier_result_in_place(router):
    """S-7.7 runner leg, a stop in the Alpaca phase (simulated with SystemExit)."""
    roster(router, X)
    first = completed_run(router, webhook_url=None)
    router.set_alpaca("SHNY", t.shny_reply())
    router.set_alpaca("SPY", h.raising(SystemExit(1)))
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY"]))
    with pytest.raises(SystemExit):
        run_check(router, start=night(1))
    assert len(rows(RUN_ROLE)) == 1
    assert runner.read_latest_runs().latest == record_of(first)
    assert router.of_kind("discord") == []
    assert announcements() == []


def test_run_stopped_during_a_tree_read_leaves_no_row(router):
    roster(router, X, Y)
    router.set_tree(Y, h.raising(SystemExit(1)))
    with pytest.raises(SystemExit):
        run_check(router)
    assert rows(RUN_ROLE) == []
    assert router.of_kind("discord") == []


def test_unexpected_error_is_stored_as_a_failed_run_naming_the_exception_class_only(
    router, caplog, monkeypatch
):
    """S-7.10 runner leg: the call does not raise and the message text is never stored."""
    roster(router, X)
    first = completed_run(router, webhook_url=None)

    def explode(*args, **kwargs):
        raise RuntimeError(f"explosion text {LEAK}")

    monkeypatch.setattr("advisors.ticker_health.tree_ticker_uses", explode, raising=False)
    with caplog.at_level(logging.DEBUG):
        outcome = run_check(router, start=night(1))
    assert (outcome.status, outcome.completed) == ("failed", False)
    rec = record_of(outcome)
    assert (rec["status"], rec["completed"], rec["error_type"]) == (
        "failed",
        False,
        "RuntimeError",
    )
    assert "warnings" not in rec
    assert LEAK not in json.dumps(rec) and LEAK not in caplog.text
    assert runner.read_latest_runs().latest_completed == record_of(first)
    assert len(router.discord_posts()) == 0


def test_database_error_before_the_first_request_ends_the_run_failed(router, monkeypatch):
    roster(router, X)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError(f"database is locked {LEAK}")

    with monkeypatch.context() as patch:
        patch.setattr(database, "get_advisor_observations_for_role", broken)
        outcome = run_check(router)
    assert (outcome.status, outcome.completed) == ("failed", False)
    rec = record_of(outcome)
    assert rec["error_type"] == "OperationalError"
    assert LEAK not in json.dumps(rec)
    assert router.requests == []


# ---------------------------------------------------------------------------
# Reading the latest runs back
# ---------------------------------------------------------------------------


def test_latest_runs_of_an_empty_table_are_none_until_a_run_is_stored(router):
    empty = runner.read_latest_runs()
    assert empty.latest is None and empty.latest_completed is None
    stored = run_check(router, start=IN_SESSION)
    assert stored.status == "skipped"
    latest = runner.read_latest_runs()
    assert latest.latest == record_of(stored)
    assert latest.latest_completed is None


def test_newest_completed_run_is_read_alone(router, monkeypatch):
    roster(router, X)
    first = completed_run(router, webhook_url=None)
    calls = spy_observation_reads(monkeypatch)
    latest = runner.read_latest_runs()
    run_calls = [c for c in calls if c[0] == RUN_ROLE]
    assert latest.latest == latest.latest_completed == record_of(first)
    assert run_calls == [(RUN_ROLE, 1)]


def test_newest_run_that_did_not_complete_sends_the_read_to_the_scan(router, monkeypatch):
    roster(router, X)
    completed = completed_run(router, webhook_url=None)
    skipped = run_check(router, start=IN_SESSION)
    unavailable = run_check(router, start=night(2), alpaca_key="")
    assert (skipped.status, unavailable.status) == ("skipped", "unavailable")
    calls = spy_observation_reads(monkeypatch)
    latest = runner.read_latest_runs()
    default_calls = [c for c in calls if c[0] == RUN_ROLE]
    assert latest.latest == record_of(unavailable)
    assert latest.latest_completed == record_of(completed)
    assert default_calls == [(RUN_ROLE, 1), (RUN_ROLE, 365)]
    calls.clear()
    runner.read_latest_runs(scan_limit=2)
    assert [c for c in calls if c[0] == RUN_ROLE] == [(RUN_ROLE, 1), (RUN_ROLE, 2)]
    assert runner.read_latest_runs(scan_limit=2).latest_completed is None
    assert runner.read_latest_runs(scan_limit=3).latest_completed == record_of(completed)


def test_a_run_reads_the_two_hundred_newest_announcements_at_its_start(router, monkeypatch):
    roster(router, X)
    calls = spy_observation_reads(monkeypatch)
    completed_run(router, webhook_url=None)
    assert (ANNOUNCEMENT_ROLE, 200) in calls
    assert (RUN_ROLE, 1) in calls


def test_reading_the_latest_runs_raises_on_a_database_error(monkeypatch):
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(database, "get_advisor_observations_for_role", broken)
    with pytest.raises(sqlite3.OperationalError):
        runner.read_latest_runs()


# ---------------------------------------------------------------------------
# Logs and the timing line (AC-9)
# ---------------------------------------------------------------------------


def test_logs_carry_status_codes_and_class_names_never_credentials_or_names(router, caplog):
    """S-9.1: a failed post and a failed Alpaca read, and still no secret, name or ticker."""
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY"]))
    router.set_alpaca(
        "SPY",
        h.raising(
            requests.ConnectionError(
                f"net down {t.ALPACA_KEY} {t.ALPACA_SECRET} {t.WEBHOOK_URL} {h.SERVICE_SECRET}"
            )
        ),
    )
    router.set_discord(h.raising(requests.Timeout(f"slow {t.WEBHOOK_URL}")))
    with caplog.at_level(logging.DEBUG):
        outcome = completed_run(router)
    assert forbidden_in(caplog.text) == []
    own = [r for r in caplog.records if r.name.startswith("alphabot.ticker_health")]
    assert own, "the run logs through a child of the alphabot logger named alphabot.ticker_health"
    assert any(r.levelno >= logging.WARNING and "Timeout" in r.getMessage() for r in own)
    text = " ".join(r.getMessage() for r in own)
    for private in ("SHNY", "SPY", "Planet", X, h.ACCOUNT_ID, "LQD"):
        assert private not in text, private
    info = [r for r in own if r.levelno == logging.INFO]
    assert len(info) == 2
    assert "nightly" in info[0].getMessage() and outcome.run_id in info[0].getMessage()
    assert "completed" in info[1].getMessage()


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(t.unauthorized_reply, id="401"),
        pytest.param(lambda: alpaca_status(429), id="429"),
        pytest.param(lambda: alpaca_status(500), id="500"),
        pytest.param(lambda: alpaca_status(400), id="400"),
        pytest.param(
            lambda: h.Reply(status=200, body="not json", content_type="text/plain"), id="200_text"
        ),
    ],
)
def test_an_alpaca_error_body_reaches_no_log_row_or_message(router, caplog, reply):
    """S-9.5: the body text of any Alpaca answer is never carried anywhere."""
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY"]))
    marked = reply()
    if isinstance(marked.body, dict):
        marked.body = {**marked.body, "message": f"{marked.body.get('message', '')} {LEAK}"}
    else:
        marked.body = f"{marked.body} {LEAK}"
    router.set_alpaca("SPY", marked)
    with caplog.at_level(logging.DEBUG):
        outcome = completed_run(router)  # SHNY sorts first and is answered before SPY fails
    record_of(outcome)
    stored = json.dumps([r["raw_response"] for r in rows(RUN_ROLE) + rows(ANNOUNCEMENT_ROLE)])
    assert LEAK not in stored
    assert LEAK not in caplog.text
    assert LEAK not in json.dumps(router.discord_posts())


def test_composer_error_bodies_reach_no_log_row_or_message(router, caplog):
    roster(router, X, Y)
    router.set_tree(X, h.marked_error_reply(503, LEAK))
    router.statistics_by_account[h.SECOND_ACCOUNT_ID] = h.marked_error_reply(401, LEAK)
    with caplog.at_level(logging.DEBUG):
        outcome = completed_run(router, account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])
    assert record_of(outcome)["not_checked"]["symphonies"]
    stored = json.dumps([r["raw_response"] for r in rows(RUN_ROLE) + rows(ANNOUNCEMENT_ROLE)])
    assert LEAK not in stored and LEAK not in caplog.text
    assert LEAK not in json.dumps(router.discord_posts())


def test_one_timing_line_per_alpaca_attempt_without_the_symbol(router, timing_records):
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY", "SPY"]))
    router.set_alpaca("SHNY", [alpaca_status(503), t.shny_reply()])
    completed_run(router, webhook_url=None)
    lines = timing_records.of("alpaca")
    assert [line.fields.get("status") for line in lines] == ["503", "200", "200"]
    for line in lines:
        assert line.words == ("GET", "/v2/assets")
        assert line.fields["ms"].isdigit()
    assert not any("SHNY" in text or "SPY" in text for text in timing_records.lines)


# ---------------------------------------------------------------------------
# Protected surfaces
# ---------------------------------------------------------------------------


def test_the_exit_alert_and_end_of_day_senders_are_unchanged():
    """S-5.12: ``reporting.py`` keeps the bytes it had when the plan was written."""
    raw = (REPO / "reporting.py").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == REPORTING_SHA256, (
        "reporting.py changed; stop and ask before re-pinning"
    )
