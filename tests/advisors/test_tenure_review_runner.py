"""The tenure review runner: Composer reads, shadow fallback, dated backtest, holdings, one run.

Module under test: advisors.tenure_review_runner (and, through it, advisors.composer_portfolio_client,
the definition read and advisors.composer_backtest_client.run_backtest).

Every Composer request is served by tests/advisors/_composer_http_router.py from the captured
records under tests/fixtures/composer/tenure-review/; the router also replaces every other
``requests`` verb and ``time.sleep``, so the request ledger shows any request that is not a read
or a backtest. Entries are produced by the real review compute module (advisors.tenure_review).

Contract pinned here (the implementer reads this file, not the plan):

- ``run_tenure_review(*, account_ids, threshold_raw, now_et, run_id, trigger="on_demand",
  account_labels=None, read_spacing_seconds=6.0)`` returns ``RunOutcome(completed, result,
  failure_reason)`` and never raises. ``account_labels`` maps an account id to the label the
  reasons use (``configured_account_labels(env)`` builds it from the configuration keys in the
  dashboard's words: ACCOUNT_INDIVIDUAL "Individual", ACCOUNT_ROTH "Roth IRA", ACCOUNT_TRAD
  "Trad. IRA"); an account without a label reads "account <n>", n being its 1-based position
  in ``account_ids``. A reason never carries a raw account id.
  ``read_spacing_seconds`` (keyword-only, default COMPOSER_READ_SPACING_SECONDS, 6.0) is the
  sleep after every Composer request of the run; a value that is not a finite number of zero
  or more (negative, NaN, infinity, None, a non-number) is replaced by the default with one
  WARNING line naming the parameter, and the run never raises on it. ``result`` is the
  ``ReviewResult`` of ``tenure_review.build_review`` (called as ``tenure_review.build_review(...)``
  so a patch of that module attribute holds). It writes nothing to any database.
- The roster is the union of the statistics responses of ``account_ids`` and nothing else. Any
  statistics failure (any failure class, any account) ends the run uncompleted with
  ``failure_reason == "Composer statistics unavailable"`` (an unreadable 200 reads
  "Composer statistics unreadable"); no account ends it with "no Composer account configured".
  An unexpected exception ends the run uncompleted with a reason naming the exception type only.
- Threshold and as-of day come from ``tenure_review.parse_threshold(threshold_raw)`` and
  ``tenure_review.as_of_trading_day(now_et)``, with ``now_et`` passed through as given.
- A display name is trimmed; a missing, null or blank name is the Composer id, everywhere the
  review shows a name.
- Every live symphony gets one series read against the account that listed it. A series failure
  becomes ``SymphonyInput.series_failure`` (the failure class string of the client) and each
  failure class reads its own reason in the entry. The fallback ``shadow_returns`` are read as
  ``analytics.get_symphony_bot_and_held_daily_returns(id, days=None)`` element [1] keyed by
  Composer id whenever the Composer series is not usable: when its read failed, and also when it
  was read but leaves fewer than 2 usable points between the invested-since date and the as-of
  day. A usable series becomes ``composer_values``.
- ``SymphonyInput.current_tickers`` are the keys of the symphony's own ``logic_holdings`` in the
  state returned by ``database.load_state()`` (called as ``database.load_state()`` so a patch of
  that module attribute holds); left ``None`` when the state has no entry for the Composer id,
  no ``logic_holdings`` or a null one, or when the state cannot be read.
- The window of a symphony is asked of ``tenure_review.qualifying_window`` (called as
  ``tenure_review.qualifying_window(symphony, as_of, threshold)`` so a patch of that module
  attribute holds); the runner uses no private name of ``advisors.tenure_review``.
- The run reads in phases: statistics, then every series, then for each symphony with a usable
  window and tenure at least the threshold one definition read and, when the definition is
  non-empty, one dated compact backtest (``run_backtest(..., start_date=<window first>,
  end_date=<window last>, compact_body=True, ...)``), then holdings. An empty definition sets
  ``definition_available=False`` and requests no backtest.
- Holdings are read for every account in client spans, each from the single earliest window
  first date over all symphonies (not each account's own) to the as-of day, merged across
  accounts by date and symphony id, keeping only NYSE trading days. When every read fails,
  ``HoldingsInput.failure`` is the first failed span's class. When some accounts could not be
  read in full (any failed span), the days that were read still feed the review, and the
  long-VIX item of a symphony names the accounts that are missing: its portfolio-wide shares
  (``peak_share_with``, ``peak_share_without``) read ``value`` None, and its own counts
  (``held_days``, ``co_held_days``) are computed from the days that were read. A symphony
  whose own account is missing and that has no holding day in what was read reads
  unavailable, never "no holdings for this symphony in the window". Every such reason keeps
  the failure class's own text (a 429 still reads "Composer rate limit", a 401 "Composer
  refused the request", whatever the other accounts did) and adds, for each missing account,
  "holdings partly read for <label>" when some of its spans were read, else "holdings not
  read for <label>" (an account never requested because an earlier request was answered 429
  is "not read" with the rate-limit class). The share reasons name every missing account and
  no readable one; an unavailable item names its own account. When no day at all was read
  the item reads the failure class's text alone. A holdings failure never fails the run.
  An account whose failed spans carry several classes is named once per distinct class, the
  classes in the order first seen, each clause being the class text then "holdings <state> for
  <label>" (so a 429 after a 500 on one account reads both); accounts follow in the order of
  ``account_ids``.
- Pacing: at least 6.0 seconds of sleep (``read_spacing_seconds``) separate any two
  consecutive Composer requests of the run, whatever their kinds and whether the earlier one
  was answered or raised, a retry of the backtest included. The first reply answered 429
  ends the run's Composer requests: nothing is requested after it (the backtest is not retried
  after a 429), and every item that would have needed a later request reads "Composer rate
  limit". The runner adds no request beyond statistics, series, holdings, definition and
  backtest; none of the five follows a redirect. A symphony id that is not a plain id segment
  is never requested: its series reads unreadable. An account id that is not a plain id
  (outside letters, digits, "_" and "-", so "." and ".." included) is refused before any
  request: the run reads that account's statistics as unreadable and ends uncompleted.
- An exception inside the definition read or the backtest call makes only that symphony's
  backtest item unavailable; the run still completes.
- Logs name exception types only: no log line at any level carries a credential header, a
  response body or text, or an exception message.
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
import math
import re
import sqlite3
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import requests

import advisors.tenure_review as tr
import alpha_bot_execution
import analytics
import database
from advisors import composer_backtest_client as cbc
from advisors import composer_portfolio_client as cpc
from advisors import tenure_review_runner as runner
from tests.advisors import _composer_http_router as h
from tests.advisors import _tenure_review_builders as b
from tests.advisors._retirement_recommender_reference import seed_state_db

REPO = Path(__file__).resolve().parents[2]

NOW = b.et(2026, 10, 20, 3, 45)  # a Tuesday before the open: the as-of day is Monday 2026-10-19
AS_OF = date(2026, 10, 19)  # the last point of every captured series
LONG_VIX = {"VIXY", "VIXM", "UVXY", "UVIX", "VXX"}
# 10 requests a minute: the engine reads statistics every minute and the dashboard twice, all
# against the 25 a minute Composer documents.
SPACING_SECONDS = 6.0
RATE_LIMIT_REASON = "Composer rate limit"
LEAK = "LEAKMARK-reply-body-5521"
SHORT_SERIES_REASON = "Composer series has fewer than 2 usable points in the tenure"
COMPOSER_BASIS = "realized: Composer account, deposit-adjusted"
SHADOW_BASIS = "PlanetStopper shadow history from the 2026-06-22 go-live floor"

_DROP = object()

# Labels as configured_account_labels names them: the dashboard's words for the account.
LABELS = {h.ACCOUNT_ID: "Individual", h.SECOND_ACCOUNT_ID: "Roth IRA"}
FIRST_LABEL, SECOND_LABEL = LABELS[h.ACCOUNT_ID], LABELS[h.SECOND_ACCOUNT_ID]
SHARE_FIGURES = ("peak_share_with", "peak_share_without")
WIDENED_SPACING_SECONDS = 7.5
# How the reasons name an account: by the configured label, or by position when none is given.
NAMING = [
    pytest.param({"account_labels": LABELS}, (FIRST_LABEL, SECOND_LABEL), id="configured_labels"),
    pytest.param({}, ("account 1", "account 2"), id="positions"),
]


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def router(monkeypatch):
    return h.ComposerRouter().install(monkeypatch)


@pytest.fixture(autouse=True)
def _no_shadow_history_by_default(tmp_path, monkeypatch):
    """No shadow rows unless a test seeds them: the shadow read finds no database file."""
    monkeypatch.setattr(analytics, "DB_FILE", str(tmp_path / "no_shadow_history.db"))


@pytest.fixture
def inputs_seen(monkeypatch):
    """Every ReviewInputs the runner hands to the real build_review, in call order."""
    seen: list[tr.ReviewInputs] = []
    real = tr.build_review

    def recording(inputs):
        seen.append(inputs)
        return real(inputs)

    monkeypatch.setattr(tr, "build_review", recording)
    return seen


@pytest.fixture
def engine_stats_calls(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        alpha_bot_execution, "fetch_symphony_stats", lambda *a, **k: calls.append(a) or []
    )
    return calls


def run(**overrides):
    args = {
        "account_ids": [h.ACCOUNT_ID],
        "threshold_raw": None,
        "now_et": NOW,
        "run_id": b.RUN_ID,
    }
    args.update(overrides)
    return runner.run_tenure_review(**args)


def completed_run(**overrides):
    outcome = run(**overrides)
    assert outcome.completed is True, outcome.failure_reason
    assert outcome.failure_reason is None
    assert outcome.result is not None
    return outcome


def entry(outcome, symphony_id):
    return b.entry_of(outcome.result, symphony_id)


def input_of(inputs_seen, symphony_id):
    assert len(inputs_seen) == 1, "build_review must be called exactly once per run"
    found = [s for s in inputs_seen[0].symphonies if s.symphony_id == symphony_id]
    assert len(found) == 1
    return found[0]


def edit_statistics(router, edits):
    """Serve the captured statistics record with per-symphony field edits (``_DROP`` removes a
    key): the missing-field and changed-value edges of one captured record."""
    body = h.statistics_body()
    for item in body["symphonies"]:
        for key, value in edits.get(item["id"], {}).items():
            if value is _DROP:
                del item[key]
            else:
                item[key] = value
    router.statistics_default = h.Reply(body=body)


def split_roster(router, first_count=None):
    """Two accounts: the first lists the first ``first_count`` captured symphonies (half of them
    by default), the second the rest (subsets of the one captured statistics record)."""
    entries = h.statistics_body()["symphonies"]
    first_count = len(entries) // 2 if first_count is None else first_count
    mine, theirs = entries[:first_count], entries[first_count:]
    router.statistics_by_account = {
        h.ACCOUNT_ID: h.Reply(body={"symphonies": mine}),
        h.SECOND_ACCOUNT_ID: h.Reply(body={"symphonies": theirs}),
    }
    return [e["id"] for e in mine], [e["id"] for e in theirs]


def seed_shadow(tmp_path, monkeypatch, symphony_id, count=140, *, roster_extra=()):
    """Shadow rows for one Composer id on the last ``count`` NYSE sessions to the as-of day.
    Bot (shadow_return) and held (current_return) differ on every day. Returns the bot map."""
    days = [d.isoformat() for d in b.last_n_nyse_days(AS_OF, count)]
    bot = b.sine_returns(count, 3, amp=0.4, drift=0.08)
    held = b.sine_returns(count, 5, amp=0.9, drift=-0.04)
    rows = {symphony_id: {d: (s, c) for d, s, c in zip(days, bot, held, strict=True)}}
    for extra in roster_extra:
        rows[extra] = {}
    db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=rows)
    monkeypatch.setattr(analytics, "DB_FILE", db_file)
    return dict(zip(days, bot, strict=True)), db_file


def shadow_window_days(bot_by_day, invested_since):
    """The day count of a shadow-basis window: the stored rows plus the session before the
    first row, which opens the window as a baseline when it falls on or after the
    invested-since date (otherwise the first row is the baseline and adds none)."""
    baseline = b.session_before(date.fromisoformat(min(bot_by_day)))
    start = date.fromisoformat(invested_since[:10]) if invested_since else date.min
    return len(bot_by_day) + (1 if baseline >= start else 0)


def seed_shadow_many(tmp_path, monkeypatch, symphony_ids, count=140):
    """The same shadow rows (bot and held differ on every day) for each listed Composer id."""
    days = [d.isoformat() for d in b.last_n_nyse_days(AS_OF, count)]
    bot = b.sine_returns(count, 3, amp=0.4, drift=0.08)
    held = b.sine_returns(count, 5, amp=0.9, drift=-0.04)
    rows = {
        sid: {d: (s, c) for d, s, c in zip(days, bot, held, strict=True)} for sid in symphony_ids
    }
    db_file = seed_state_db(tmp_path, monkeypatch, series_by_symphony=rows)
    monkeypatch.setattr(analytics, "DB_FILE", db_file)
    return dict(zip(days, bot, strict=True)), db_file


def seed_logic_holdings(tmp_path, monkeypatch, logic_holdings_by_key):
    """A state with one entry per key, each holding ``logic_holdings`` as given (None allowed)."""
    seed_state_db(
        tmp_path, monkeypatch, series_by_symphony={key: {} for key in logic_holdings_by_key}
    )
    state = database.load_state()
    for key, logic_holdings in logic_holdings_by_key.items():
        state[key]["logic_holdings"] = logic_holdings
    database.save_state(state)


def table_count(db_file, table):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def backtest_posts(router):
    return router.of_kind("backtest")


def captured_definition_for(router, *symphony_ids):
    """Serve the captured definition record as the definition of each listed symphony (its
    tree content is irrelevant to the read path and the request body)."""
    for sid in symphony_ids:
        router.definition_by_symphony[sid] = h.reply_from_record(
            "case4_definition_nOyb55RMGVCKPiYXv7TI.json"
        )


def trading_day_holdings(body):
    """The days of a holdings body that are NYSE sessions (weekend days dropped)."""
    days = h.holdings_days(body)
    sessions = {
        d.isoformat()
        for d in b.nyse_days(date.fromisoformat(min(days)), date.fromisoformat(max(days)))
    }
    return {day: by_symphony for day, by_symphony in days.items() if day in sessions}


def long_vix_counts(days, subject, live_ids):
    """(days the subject held a long-VIX product, of those days the ones another did too)."""
    held = co_held = 0
    for by_symphony in days.values():
        if subject in by_symphony and LONG_VIX & set(by_symphony[subject]):
            held += 1
            co_held += any(
                LONG_VIX & set(by_symphony[other])
                for other in live_ids
                if other != subject and other in by_symphony
            )
    return held, co_held


def long_vix_peak_shares(days, subject, live_ids):
    """(peak long-VIX percent of the portfolio with the subject, without it) over the days the
    subject has holdings; None for a side with no value on any day."""
    peaks = {"with": None, "without": None}
    for by_symphony in days.values():
        if subject not in by_symphony:
            continue
        for side, ids in (
            ("with", live_ids),
            ("without", [sid for sid in live_ids if sid != subject]),
        ):
            total = sum(sum(by_symphony[sid].values()) for sid in ids if sid in by_symphony)
            long_value = sum(
                v
                for sid in ids
                if sid in by_symphony
                for k, v in by_symphony[sid].items()
                if k in LONG_VIX
            )
            if total > 0:
                share = long_value / total * 100.0
                peaks[side] = share if peaks[side] is None else max(peaks[side], share)
    return peaks["with"], peaks["without"]


def assert_compact_dated_body(request, *, start, end, definition):
    body = request.body_json()
    assert body["start_date"] == start
    assert body["end_date"] == end
    assert body["symphony"]["raw_value"] == definition
    text = request.body_text()
    assert text is not None, "the compact body is sent as data=, not json="
    assert request.kwargs.get("json") is None
    assert text == json.dumps(json.loads(text), separators=(",", ":"), allow_nan=False)
    assert request.headers.get("Content-Type") == "application/json"


def available_long_vix(outcome):
    """Symphony id -> long-VIX item, for every entry whose item is available."""
    items = {}
    for e in outcome.result.entries:
        item = e.get("evidence", {}).get("long_vix")
        if item and item["status"] == "available":
            items[e["symphony_id"]] = item
    return items


def assert_reason_names_accounts(reason, class_reason, missing, *, readable=()):
    """The reason keeps the failure class's own text, says for each missing account (label ->
    "not read" or "partly read") how much of its holdings was read, names no readable
    account, and never carries a raw account id."""
    assert class_reason in reason, reason
    for label, state in missing.items():
        assert f"holdings {state} for {label}" in reason, reason
    for state in {"not read", "partly read"} - set(missing.values()):
        assert f"holdings {state}" not in reason, reason
    for label in readable:
        assert label not in reason, reason
    for raw_id in (h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID):
        assert raw_id not in reason, reason


HOLDINGS_CLAUSE = re.compile(r"holdings (?:partly read|not read) for")


def assert_class_clauses(reason, clauses, *, absent=()):
    """The reason is the clauses in order, one per (class text, state, label): the class text,
    then "holdings <state> for <label>", before the next clause's class text. A class text
    appears once per clause that carries it, so a class shared by several failed spans of an
    account is named once; no clause is added beyond the expected ones; a label in ``absent``
    and a raw account id appear nowhere."""
    assert len(HOLDINGS_CLAUSE.findall(reason)) == len(clauses), reason
    for text in {clause[0] for clause in clauses}:
        assert reason.count(text) == sum(1 for c in clauses if c[0] == text), reason
    cursor = 0
    for text, state, label in clauses:
        start = reason.find(text, cursor)
        assert start >= 0, f"{text!r} (after offset {cursor}) is missing from: {reason}"
        phrase = f"holdings {state} for {label}"
        at = reason.find(phrase, start + len(text))
        assert at >= 0, f"{phrase!r} does not follow {text!r} in: {reason}"
        cursor = at + len(phrase)
    for label in absent:
        assert label not in reason, reason
    for raw_id in (h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID):
        assert raw_id not in reason, reason


# One holdings span reply per token: "ok" is a read span with no days, an int is an HTTP status.
CLASS_TEXT_OF_TOKEN = {
    401: "Composer refused the request",
    403: "Composer refused the request",
    404: "Composer returned not found",
    422: "Composer rejected the request",
    429: RATE_LIMIT_REASON,
    500: "Composer unavailable",
    "timeout": "Composer unavailable",
    "garbled": "Composer response unreadable",
}


def span_reply(token):
    if token == "ok":
        return h.Reply(body=h.holdings_emptied_body())
    if token == "timeout":
        return h.raising(requests.Timeout("LEAKMARK"))
    if token == "garbled":
        return h.non_json_200()
    return h.error_reply(token)


def span_clauses(tokens, label):
    """The clauses an account whose spans answer ``tokens`` (the last token repeats for every
    later span) must read: each distinct class in the order first seen, all in one state."""
    ended = tokens[: tokens.index(429) + 1] if 429 in tokens else tokens
    texts = []
    for token in ended:
        if token != "ok" and CLASS_TEXT_OF_TOKEN[token] not in texts:
            texts.append(CLASS_TEXT_OF_TOKEN[token])
    state = "partly read" if "ok" in ended else "not read"
    return [(text, state, label) for text in texts]


def serve_split_holdings(router, mine, second_account_reply):
    """The first account is readable (its symphonies on its first span, nothing after); the
    second account answers ``second_account_reply(index of its request)``. Returns the body
    the first account serves."""
    readable = h.holdings_subset_body(mine)

    def handler(request, index):
        if request.match["account"] == h.SECOND_ACCOUNT_ID:
            return second_account_reply(index)
        return h.Reply(body=readable if index == 0 else h.holdings_emptied_body())

    router.holdings_handler = handler
    return readable


def imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


# ---------------------------------------------------------------------------
# Account configuration
# ---------------------------------------------------------------------------


def test_configured_account_ids_follow_the_engine_order_whatever_the_mapping_order():
    env = {"ACCOUNT_TRAD": "trad-id", "ACCOUNT_INDIVIDUAL": "ind-id", "ACCOUNT_ROTH": "roth-id"}

    assert runner.configured_account_ids(env) == ["ind-id", "roth-id", "trad-id"]


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, []),
        ({"ACCOUNT_ROTH": "roth-id"}, ["roth-id"]),
        ({"ACCOUNT_INDIVIDUAL": "", "ACCOUNT_ROTH": "roth-id", "ACCOUNT_TRAD": "  "}, ["roth-id"]),
        ({"ACCOUNT_INDIVIDUAL": "  ind-id \n", "ACCOUNT_TRAD": "trad-id"}, ["ind-id", "trad-id"]),
    ],
    ids=["none", "one", "blank_values_skipped", "values_stripped"],
)
def test_configured_account_ids_skip_blank_values_and_strip_the_rest(env, expected):
    assert runner.configured_account_ids(env) == expected


def test_configured_account_ids_read_only_the_mapping_they_are_given(monkeypatch):
    monkeypatch.setenv("ACCOUNT_ROTH", "from-the-process-environment")

    assert runner.configured_account_ids({}) == []


def test_configured_account_labels_use_the_dashboards_words_for_each_account():
    env = {"ACCOUNT_TRAD": "trad-id", "ACCOUNT_INDIVIDUAL": "ind-id", "ACCOUNT_ROTH": "roth-id"}

    labels = runner.configured_account_labels(env)

    # The words the dashboard shows for these accounts (app.py names them the same way).
    assert labels == {"ind-id": "Individual", "roth-id": "Roth IRA", "trad-id": "Trad. IRA"}
    assert list(labels) == runner.configured_account_ids(env), "the engine's account order"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"ACCOUNT_ROTH": "roth-id"},
        {"ACCOUNT_INDIVIDUAL": "", "ACCOUNT_ROTH": "roth-id", "ACCOUNT_TRAD": "  "},
        {"ACCOUNT_INDIVIDUAL": "  ind-id \n", "ACCOUNT_TRAD": "trad-id"},
    ],
    ids=["none", "one", "blank_values_skipped", "values_stripped"],
)
def test_configured_account_labels_cover_exactly_the_configured_account_ids(env):
    labels = runner.configured_account_labels(env)

    assert list(labels) == runner.configured_account_ids(env)
    for account_id, label in labels.items():
        assert account_id not in label, "a label never carries the id it stands for"


def test_configured_account_labels_read_only_the_mapping_they_are_given(monkeypatch):
    monkeypatch.setenv("ACCOUNT_ROTH", "from-the-process-environment")

    assert runner.configured_account_labels({}) == {}


def test_runner_imports_its_collaborators_and_never_the_execution_engine():
    imported = imported_modules(REPO / "advisors" / "tenure_review_runner.py")

    assert "alpha_bot_execution" not in imported
    for needed in (
        "advisors.composer_portfolio_client",
        "advisors.composer_backtest_client",
        "advisors.tenure_review",
    ):
        assert any(name == needed or name.startswith(f"{needed}.") for name in imported), needed


# ---------------------------------------------------------------------------
# One run: identity, threshold, as-of day
# ---------------------------------------------------------------------------


def test_a_completed_run_has_one_entry_per_symphony_in_the_statistics_response(router):
    outcome = completed_run()

    assert isinstance(outcome.result, tr.ReviewResult)
    ids = [e["symphony_id"] for e in outcome.result.entries]
    assert sorted(ids) == sorted(h.statistics_ids())
    assert len(ids) == len(set(ids)) == len(h.statistics_ids())
    by_id = {e["id"]: e for e in h.statistics_body()["symphonies"]}
    assert any(e["name"] != e["name"].strip() for e in by_id.values()), (
        "fixture sanity: a captured name carries surrounding whitespace"
    )
    for e in outcome.result.entries:
        assert e["display_name"] == by_id[e["symphony_id"]]["name"].strip()
        assert e["invested_since"] == by_id[e["symphony_id"]]["invested_since"]


@pytest.mark.parametrize("trigger", ["nightly", "on_demand"])
def test_the_run_carries_its_identity_trigger_start_and_as_of_day(router, trigger):
    outcome = completed_run(trigger=trigger, run_id="f" * 32)

    assert outcome.result.run_id == "f" * 32
    assert outcome.result.trigger == trigger
    assert outcome.result.started_at == NOW
    assert outcome.result.as_of == AS_OF


@pytest.mark.parametrize(
    ("raw", "value", "note"),
    [(None, 126, None), ("90", 90, None), ("abc", 126, tr.INVALID_THRESHOLD_NOTE)],
    ids=["absent", "valid", "invalid"],
)
def test_the_threshold_setting_is_parsed_by_the_review_module(router, raw, value, note):
    outcome = completed_run(threshold_raw=raw)

    assert outcome.result.threshold.value == value
    assert outcome.result.threshold.note == note


def test_the_threshold_decides_eligibility_on_the_same_captured_symphony(router):
    low = completed_run(threshold_raw="100")
    high = completed_run(threshold_raw="200")

    assert entry(low, h.BACKTEST_ID)["eligible"] is True
    assert entry(high, h.BACKTEST_ID)["eligible"] is False
    assert entry(high, h.OLDEST_ID)["eligible"] is True


def test_the_as_of_day_is_read_in_us_eastern_time_from_an_instant_in_another_zone(router):
    utc_instant = datetime(2026, 10, 21, 2, 0, tzinfo=UTC)  # 22:00 ET on Tuesday 2026-10-20

    outcome = completed_run(now_et=utc_instant)

    assert outcome.result.as_of == date(2026, 10, 20)
    tenure = entry(outcome, h.BACKTEST_ID)["tenure_trading_days"]
    assert tenure == b.oracle_count_trading_days(date(2026, 3, 19), date(2026, 10, 20))


def test_series_points_after_the_as_of_day_are_left_out_of_the_window(router):
    before_the_close = b.et(2026, 10, 19, 11, 0)  # Monday 11:00 ET: as-of is Friday 2026-10-16

    outcome = completed_run(now_et=before_the_close)

    window = entry(outcome, h.OLDEST_ID)["evidence"]["performance"]["window"]
    assert outcome.result.as_of == date(2026, 10, 16)
    assert window["last"] == "2026-10-16"
    assert window["days"] == sum(d <= "2026-10-16" for d in h.series_dates(h.OLDEST_ID))


# ---------------------------------------------------------------------------
# Statistics: the roster, and the fail-closed rule
# ---------------------------------------------------------------------------

STATISTICS_FAILURES = [
    pytest.param(
        lambda: h.reply_from_record("case7b_statistics_invalid_credentials_401.json"), id="401"
    ),
    pytest.param(lambda: h.error_reply(403), id="403"),
    pytest.param(lambda: h.error_reply(404), id="404"),
    pytest.param(lambda: h.error_reply(429), id="429"),
    pytest.param(lambda: h.error_reply(400), id="400"),
    pytest.param(lambda: h.error_reply(500), id="500"),
    pytest.param(lambda: h.error_reply(503), id="503"),
    pytest.param(lambda: h.raising(requests.Timeout("LEAKMARK")), id="Timeout"),
    pytest.param(lambda: h.raising(requests.ConnectionError("LEAKMARK")), id="ConnectionError"),
]


@pytest.mark.parametrize("make_reply", STATISTICS_FAILURES)
def test_a_statistics_failure_ends_the_run_uncompleted_before_any_other_read(router, make_reply):
    router.statistics_default = make_reply()

    outcome = run()

    assert outcome.completed is False
    assert outcome.result is None
    assert outcome.failure_reason == "Composer statistics unavailable"
    assert {r.kind for r in router.requests} == {"statistics"}


@pytest.mark.parametrize(
    "reply",
    [
        h.non_json_200(),
        h.Reply(body={"data": []}),
        h.Reply(body={"symphonies": "x"}),
    ],
    ids=["non_json_200", "no_symphonies_key", "symphonies_not_a_list"],
)
def test_an_unreadable_statistics_200_ends_the_run_uncompleted(router, reply):
    router.statistics_default = reply

    outcome = run()

    assert outcome.completed is False
    assert outcome.result is None
    assert outcome.failure_reason.startswith("Composer statistics")
    assert {r.kind for r in router.requests} == {"statistics"}


@pytest.mark.parametrize("failing_account", [h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])
def test_one_failing_account_ends_the_run_even_when_the_other_account_answered(
    router, failing_account
):
    split_roster(router)
    router.statistics_by_account[failing_account] = h.error_reply(503)

    outcome = run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])

    assert outcome.completed is False
    assert outcome.failure_reason == "Composer statistics unavailable"
    assert {r.kind for r in router.requests} == {"statistics"}


def test_no_configured_account_ends_the_run_without_a_single_request(router):
    outcome = run(account_ids=[])

    assert outcome.completed is False
    assert outcome.result is None
    assert outcome.failure_reason == "no Composer account configured"
    assert router.requests == []


def test_an_unexpected_error_ends_the_run_uncompleted_naming_only_the_exception_type(
    router, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)

    def boom(inputs):
        raise RuntimeError("boom-secret-7731")

    monkeypatch.setattr(tr, "build_review", boom)

    outcome = run()

    assert outcome.completed is False
    assert outcome.result is None
    assert "RuntimeError" in outcome.failure_reason
    assert "boom-secret-7731" not in outcome.failure_reason
    assert "RuntimeError" in caplog.text, "the failure is logged, by type"
    assert "boom-secret-7731" not in caplog.text


def test_the_roster_is_the_statistics_response_and_a_state_only_symphony_is_not_reviewed(
    router, tmp_path, monkeypatch
):
    state_only = "StateOnlyZ9Q3x7L2m5K8d1T4w6Y"
    _, db_file = seed_shadow(tmp_path, monkeypatch, h.OLDEST_ID, roster_extra=[state_only])
    import database  # noqa: PLC0415

    assert database.upsert_retirement_decision(state_only, approval_status="approved")
    decisions_before = database.get_retirement_decisions()
    observations_before = table_count(db_file, "advisor_observations")
    assert [d["candidate_id"] for d in decisions_before] == [state_only]

    outcome = completed_run()

    assert state_only not in [e["symphony_id"] for e in outcome.result.entries]
    assert database.get_retirement_decisions() == decisions_before
    assert table_count(db_file, "advisor_observations") == observations_before
    assert table_count(db_file, "retirement_decisions") == 1


def test_a_failed_run_writes_nothing(router, tmp_path, monkeypatch):
    _, db_file = seed_shadow(tmp_path, monkeypatch, h.OLDEST_ID)
    router.statistics_default = h.error_reply(500)
    before = {t: table_count(db_file, t) for t in ("advisor_observations", "retirement_decisions")}

    assert run().completed is False

    assert {t: table_count(db_file, t) for t in before} == before


def test_two_accounts_are_each_read_once_and_each_symphony_is_read_from_its_own_account(router):
    mine, theirs = split_roster(router)

    outcome = completed_run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])

    stats = router.of_kind("statistics")
    assert sorted(r.match["account"] for r in stats) == sorted([h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])
    owner = {sid: h.ACCOUNT_ID for sid in mine} | {sid: h.SECOND_ACCOUNT_ID for sid in theirs}
    assert len(owner) == len(h.statistics_ids())
    assert len(router.of_kind("series")) == len(h.statistics_ids())
    for r in router.of_kind("series"):
        assert r.match["account"] == owner[r.match["sid"]]
    assert len(outcome.result.entries) == len(h.statistics_ids())


def test_an_invested_since_date_composer_does_not_report_leaves_the_tenure_unknown(
    router, inputs_seen
):
    edit_statistics(
        router,
        {h.OLDEST_ID: {"invested_since": _DROP}, h.MID_ID: {"invested_since": None}},
    )

    outcome = completed_run()

    for sid in (h.OLDEST_ID, h.MID_ID):
        assert input_of(inputs_seen, sid).invested_since is None
        e = entry(outcome, sid)
        assert e["tenure_trading_days"] is None
        assert e["eligible"] is False
        assert e["eligibility_text"].startswith("tenure unavailable")
        assert e["verdict"] is None


def test_a_changed_name_and_start_date_in_the_statistics_response_are_what_the_entry_shows(router):
    edit_statistics(
        router,
        {h.BACKTEST_ID: {"name": "Renamed Test Symphony", "invested_since": "2026-05-04"}},
    )

    outcome = completed_run()

    e = entry(outcome, h.BACKTEST_ID)
    assert e["display_name"] == "Renamed Test Symphony"
    assert e["invested_since"] == "2026-05-04"
    assert e["tenure_trading_days"] == b.oracle_count_trading_days(date(2026, 5, 4), AS_OF)


# ---------------------------------------------------------------------------
# Series: the realized basis
# ---------------------------------------------------------------------------


def test_every_live_symphony_gets_exactly_one_series_read(router):
    completed_run()

    reads = [r.match["sid"] for r in router.of_kind("series")]
    assert sorted(reads) == sorted(h.statistics_ids())


def test_a_full_composer_series_is_the_realized_basis_over_the_whole_tenure(
    router, inputs_seen, tmp_path, monkeypatch
):
    seed_shadow(tmp_path, monkeypatch, h.OLDEST_ID)  # shadow rows exist but must not be used

    outcome = completed_run()

    sym = input_of(inputs_seen, h.OLDEST_ID)
    assert sym.series_failure is None
    assert dict(sym.composer_values) == h.series_points(h.OLDEST_ID)
    e = entry(outcome, h.OLDEST_ID)
    assert e["basis"]["kind"] == "composer"
    assert e["basis"]["label"] == COMPOSER_BASIS
    assert e["basis"]["fallback_reason"] is None
    assert e["basis"]["series_begins"] == e["basis"]["tenure_begins"] == "2024-07-12"
    perf = e["evidence"]["performance"]
    assert perf["status"] == "available"
    assert perf["label"] == COMPOSER_BASIS
    assert perf["window"] == {
        "first": "2024-07-12",
        "last": "2026-10-19",
        "days": len(h.series_dates(h.OLDEST_ID)),
    }


def test_realized_figures_come_from_the_deposit_adjusted_levels(router):
    outcome = completed_run()

    perf = entry(outcome, h.MID_ID)["evidence"]["performance"]
    expected = b.oracle_metrics(b.returns_from_values(h.series_points(h.MID_ID)))
    assert perf["figures"]["total_return"]["value"] == pytest.approx(
        expected["total_return"], rel=1e-9
    )
    assert perf["figures"]["max_drawdown"]["value"] == pytest.approx(
        expected["max_drawdown"], rel=1e-9
    )


# A weekend day, a weekday holiday and a Thanksgiving inside the window of the mid-tenure series.
CLOSED_DAYS = (date(2025, 11, 27), date(2026, 9, 7), date(2026, 10, 17))


def series_body_with_closed_day_points(symphony_id, closed_days):
    """The captured series with one extra point on each closed day, valued at half the session
    before it, so a counted closed day would move the day count and every figure."""
    body = h.record_body(h.series_record_name(symphony_id))
    points = list(
        zip(body["epoch_ms"], body["series"], body["deposit_adjusted_series"], strict=True)
    )
    for day in closed_days:
        epoch = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
        before = [pt for pt in points if pt[0] < epoch][-1]
        points.append((epoch, before[1] / 2, before[2] / 2))
    points.sort()
    body["epoch_ms"] = [pt[0] for pt in points]
    body["series"] = [pt[1] for pt in points]
    body["deposit_adjusted_series"] = [pt[2] for pt in points]
    return body


def test_a_series_point_on_a_closed_day_is_never_counted_by_the_review(router):
    sessions = {d for d in b.nyse_days(date(2025, 11, 1), AS_OF)}
    assert not sessions & set(CLOSED_DAYS), "fixture sanity: every added day is a closed day"
    body = series_body_with_closed_day_points(h.MID_ID, CLOSED_DAYS)
    assert len(body["epoch_ms"]) == len(h.series_dates(h.MID_ID)) + len(CLOSED_DAYS)
    router.set_series(h.MID_ID, h.Reply(body=body))

    outcome = completed_run()

    perf = entry(outcome, h.MID_ID)["evidence"]["performance"]
    assert perf["status"] == "available"
    assert perf["window"]["days"] == len(h.series_dates(h.MID_ID)), "no closed day is counted"
    expected = b.oracle_metrics(b.returns_from_values(h.series_points(h.MID_ID)))
    assert perf["figures"]["total_return"]["value"] == pytest.approx(
        expected["total_return"], rel=1e-9
    )
    assert perf["figures"]["max_drawdown"]["value"] == pytest.approx(
        expected["max_drawdown"], rel=1e-9
    )


# ---------------------------------------------------------------------------
# Series failures: the shadow-history fallback
# ---------------------------------------------------------------------------

SERIES_FALLBACKS = [
    pytest.param(
        lambda: h.reply_from_record("case7b_series_invalid_credentials_401.json"),
        "refused",
        "Composer refused the request",
        id="401",
    ),
    pytest.param(
        lambda: h.Reply(
            status=403, body=h.record_body("case7b_series_invalid_credentials_401.json")
        ),
        "refused",
        "Composer refused the request",
        id="403",
    ),
    pytest.param(
        lambda: h.reply_from_record("case7a_series_nonexistent_account_404.json"),
        "not_found",
        "Composer returned not found",
        id="404",
    ),
    pytest.param(lambda: h.error_reply(400), "rejected", "Composer rejected the request", id="400"),
    pytest.param(lambda: h.error_reply(409), "rejected", "Composer rejected the request", id="409"),
    pytest.param(lambda: h.error_reply(422), "rejected", "Composer rejected the request", id="422"),
    pytest.param(
        lambda: h.redirect_reply(302), "rejected", "Composer rejected the request", id="302"
    ),
    pytest.param(lambda: h.error_reply(429), "rate_limited", "Composer rate limit", id="429"),
    pytest.param(lambda: h.error_reply(500), "unavailable", "Composer unavailable", id="500"),
    pytest.param(lambda: h.error_reply(502), "unavailable", "Composer unavailable", id="502"),
    pytest.param(lambda: h.error_reply(503), "unavailable", "Composer unavailable", id="503"),
    pytest.param(lambda: h.error_reply(504), "unavailable", "Composer unavailable", id="504"),
    pytest.param(
        lambda: h.raising(requests.Timeout("LEAKMARK")),
        "unavailable",
        "Composer unavailable",
        id="Timeout",
    ),
    pytest.param(
        lambda: h.raising(requests.ConnectionError("LEAKMARK")),
        "unavailable",
        "Composer unavailable",
        id="ConnectionError",
    ),
    pytest.param(
        lambda: h.reply_from_record("case7a_series_nonexistent_symphony_200_empty.json"),
        "empty",
        "Composer series empty",
        id="empty_arrays",
    ),
    pytest.param(h.non_json_200, "unreadable", "Composer response unreadable", id="non_json_200"),
    pytest.param(
        lambda: h.Reply(
            body={
                k: v
                for k, v in h.record_body(h.series_record_name(h.OLDEST_ID)).items()
                if k != "deposit_adjusted_series"
            }
        ),
        "unreadable",
        "Composer response unreadable",
        id="deposit_adjusted_series_missing",
    ),
]


@pytest.mark.parametrize(("make_reply", "failure", "reason"), SERIES_FALLBACKS)
def test_a_series_failure_falls_back_to_shadow_history_labelled_with_its_reason(
    router, inputs_seen, tmp_path, monkeypatch, make_reply, failure, reason
):
    bot_by_day, _ = seed_shadow(tmp_path, monkeypatch, h.OLDEST_ID, count=140)
    router.set_series(h.OLDEST_ID, make_reply())

    outcome = completed_run()

    assert len(router.series_requests(h.OLDEST_ID)) == 1, "a failed read is never retried"
    sym = input_of(inputs_seen, h.OLDEST_ID)
    assert sym.series_failure == failure
    assert not sym.composer_values
    assert dict(sym.shadow_returns) == bot_by_day
    e = entry(outcome, h.OLDEST_ID)
    assert e["basis"]["kind"] == "shadow"
    assert e["basis"]["label"] == SHADOW_BASIS
    assert e["basis"]["fallback_reason"] == reason
    assert all(item["label"] != COMPOSER_BASIS for item in e["evidence"].values())
    perf = e["evidence"]["performance"]
    assert perf["label"] == SHADOW_BASIS
    assert perf["window"]["days"] == shadow_window_days(bot_by_day, sym.invested_since)
    assert perf["figures"]["total_return"]["value"] == pytest.approx(
        b.oracle_metrics(list(bot_by_day.values()))["total_return"], rel=1e-9
    ), "the shadow basis is the bot series (shadow_return), over all history"
    assert entry(outcome, h.MID_ID)["basis"]["kind"] == "composer"


SERIES_CLASS_CASES = [
    (lambda: h.error_reply(401), "refused", "Composer refused the request"),
    (lambda: h.error_reply(404), "not_found", "Composer returned not found"),
    (lambda: h.error_reply(503), "unavailable", "Composer unavailable"),
    (h.non_json_200, "unreadable", "Composer response unreadable"),
    (lambda: h.error_reply(422), "rejected", "Composer rejected the request"),
    (
        lambda: h.reply_from_record("case7a_series_nonexistent_symphony_200_empty.json"),
        "empty",
        "Composer series empty",
    ),
]


def test_each_series_failure_class_reads_its_own_reason_when_all_sit_side_by_side(
    router, inputs_seen
):
    """One run, one class per symphony: every class the client returns except rate_limited (a
    429 stops the run's requests, so it is exercised on its own), so a reason taken from another
    class's text cannot hide behind a shared default."""
    assigned = dict(zip(h.statistics_ids(), SERIES_CLASS_CASES, strict=False))
    assert len(assigned) == len(SERIES_CLASS_CASES), "the fixture roster covers every class"
    for sid, (make_reply, _, _) in assigned.items():
        router.set_series(sid, make_reply())

    outcome = completed_run()

    for sid, (_, failure, reason) in assigned.items():
        assert input_of(inputs_seen, sid).series_failure == failure, sid
        assert entry(outcome, sid)["basis"]["fallback_reason"] == reason, sid
    reasons = [entry(outcome, sid)["basis"]["fallback_reason"] for sid in assigned]
    assert len(set(reasons)) == len(reasons), "no two classes share a reason"


def test_no_series_and_no_shadow_rows_leaves_every_figure_unavailable_and_none(router):
    router.set_series(
        h.OLDEST_ID, h.reply_from_record("case7a_series_nonexistent_account_404.json")
    )

    outcome = completed_run()

    e = entry(outcome, h.OLDEST_ID)
    assert e["basis"]["kind"] == "none"
    assert e["basis"]["fallback_reason"] == "Composer returned not found"
    perf = e["evidence"]["performance"]
    assert perf["status"] == "unavailable"
    assert perf["reason"] == "no history"
    assert all(fig["value"] is None for fig in perf["figures"].values())


def test_shadow_rows_are_looked_up_by_the_composer_id_and_not_by_the_display_name(
    router, tmp_path, monkeypatch
):
    display_name = {e["id"]: e["name"] for e in h.statistics_body()["symphonies"]}[h.OLDEST_ID]
    seed_shadow(tmp_path, monkeypatch, display_name)  # rows under the name, none under the id
    router.set_series(h.OLDEST_ID, h.error_reply(500))

    outcome = completed_run()

    assert entry(outcome, h.OLDEST_ID)["basis"]["kind"] == "none"


# ---------------------------------------------------------------------------
# Definition and the dated compact backtest
# ---------------------------------------------------------------------------


def test_an_eligible_symphony_gets_one_definition_read_and_one_dated_compact_backtest(
    router, inputs_seen
):
    outcome = completed_run()

    assert [r.match["sid"] for r in router.of_kind("score")].count(h.BACKTEST_ID) == 1
    posts = backtest_posts(router)
    assert len(posts) == 1
    dates = h.series_dates(h.BACKTEST_ID)
    assert_compact_dated_body(
        posts[0], start=dates[0], end=dates[-1], definition=h.definition_body()
    )
    sym = input_of(inputs_seen, h.BACKTEST_ID)
    assert sym.definition_available is True
    assert sym.backtest is not None
    assert sym.backtest.error is None
    assert sym.backtest.first_day == h.day_integer_to_iso(h.backtest_success_body()["first_day"])
    assert entry(outcome, h.BACKTEST_ID)["eligible"] is True


def test_definitions_are_read_only_for_eligible_symphonies(router):
    outcome = completed_run()

    eligible = {e["symphony_id"] for e in outcome.result.entries if e["eligible"]}
    assert h.YOUNGEST_ID not in eligible
    requested = {r.match["sid"] for r in router.of_kind("score")}
    assert requested <= eligible
    assert h.BACKTEST_ID in requested


def test_a_backtest_whose_window_matches_the_realized_window_gives_both_legs_and_gaps(
    router, inputs_seen
):
    request_dates = h.load_record("case5_backtest_dated_compact_nOyb55RMGVCKPiYXv7TI.json")[
        "_request"
    ]["body_params"]
    start, end = request_dates["start_date"], request_dates["end_date"]
    edit_statistics(router, {h.BACKTEST_ID: {"invested_since": start}})

    outcome = completed_run(threshold_raw="60")

    posts = backtest_posts(router)
    assert len(posts) == 1
    assert_compact_dated_body(posts[0], start=start, end=end, definition=h.definition_body())
    item = entry(outcome, h.BACKTEST_ID)["evidence"]["backtest"]
    assert item["status"] == "available"
    assert item["window"]["first"] == start
    assert item["window"]["last"] == end
    levels = [
        value
        for day, value in sorted(
            h.backtest_success_body()["dvm_capital"][h.BACKTEST_ID].items(),
            key=lambda kv: int(kv[0]),
        )
        if start <= h.day_integer_to_iso(day) <= end
    ]
    assert item["figures"]["backtest_total_return"]["value"] == pytest.approx(
        (levels[-1] / levels[0] - 1.0) * 100.0, rel=1e-9
    )
    realized = {d: v for d, v in h.series_points(h.BACKTEST_ID).items() if start <= d <= end}
    expected_total = b.oracle_metrics(b.returns_from_values(realized))["total_return"]
    assert item["figures"]["realized_total_return"]["value"] == pytest.approx(
        expected_total, rel=1e-9
    )
    gap = item["figures"]["total_return_gap"]["value"]
    assert gap == pytest.approx(
        item["figures"]["realized_total_return"]["value"]
        - item["figures"]["backtest_total_return"]["value"],
        rel=1e-9,
        abs=1e-9,
    )


BACKTEST_FAILURES = [
    pytest.param(
        lambda: h.reply_from_record("case6_backtest_413_too_large.json"),
        "definition too large for Composer backtest (HTTP 413)",
        (1, 1),
        id="413",
    ),
    pytest.param(
        lambda: h.reply_from_record("case7c_backtest_malformed_structure_400.json"),
        "Composer rejected the backtest (HTTP 400)",
        (1, 1),
        id="400",
    ),
    pytest.param(
        lambda: h.Reply(status=422, body=h.marketcap_422_record()["response_body"]),
        "Composer rejected the backtest (HTTP 422)",
        (1, 1),
        id="422",
    ),
    pytest.param(lambda: h.error_reply(429), "Composer rate limit", (1, 1), id="429_not_retried"),
    pytest.param(
        lambda: h.reply_from_record("case7c_backtest_unknown_ticker_500.json"),
        "Composer unavailable",
        (2, 2),
        id="500_twice",
    ),
    pytest.param(lambda: h.error_reply(502), "Composer unavailable", (2, 2), id="502_twice"),
    pytest.param(lambda: h.error_reply(503), "Composer unavailable", (2, 2), id="503_twice"),
    pytest.param(lambda: h.error_reply(504), "Composer unavailable", (2, 2), id="504_twice"),
    pytest.param(
        lambda: h.raising(requests.Timeout("LEAKMARK")),
        "Composer unavailable",
        (1, 2),
        id="Timeout",
    ),
    pytest.param(
        lambda: h.raising(requests.ConnectionError("LEAKMARK")),
        "Composer unavailable",
        (1, 2),
        id="ConnectionError",
    ),
    pytest.param(h.non_json_200, "Composer response unreadable", (1, 1), id="non_json_200"),
]


@pytest.mark.parametrize(("make_reply", "reason", "post_range"), BACKTEST_FAILURES)
def test_a_backtest_failure_reads_unavailable_with_its_reason_and_the_run_still_completes(
    router, make_reply, reason, post_range
):
    router.set_backtest(make_reply())

    outcome = completed_run()

    posts = backtest_posts(router)
    assert post_range[0] <= len(posts) <= post_range[1], "at most one retry (max_retries=1)"
    dates = h.series_dates(h.BACKTEST_ID)
    assert_compact_dated_body(
        posts[0], start=dates[0], end=dates[-1], definition=h.definition_body()
    )
    e = entry(outcome, h.BACKTEST_ID)
    item = e["evidence"]["backtest"]
    assert item["status"] == "unavailable"
    assert item["reason"] == reason
    assert "backtest" in e["missing_items"]


DEFINITION_FAILURES = [
    pytest.param(lambda: h.error_reply(404), id="404"),
    pytest.param(lambda: h.error_reply(500), id="500"),
    pytest.param(lambda: h.raising(requests.Timeout("LEAKMARK")), id="Timeout"),
    pytest.param(lambda: h.raising(requests.ConnectionError("LEAKMARK")), id="ConnectionError"),
    pytest.param(h.non_json_200, id="non_json_200"),
    pytest.param(lambda: h.Reply(body={}), id="empty_object_200"),
]


@pytest.mark.parametrize("make_reply", DEFINITION_FAILURES)
def test_an_unavailable_definition_requests_no_backtest(router, inputs_seen, make_reply):
    router.definition_by_symphony[h.BACKTEST_ID] = make_reply()

    outcome = completed_run()

    assert backtest_posts(router) == []
    assert input_of(inputs_seen, h.BACKTEST_ID).definition_available is False
    item = entry(outcome, h.BACKTEST_ID)["evidence"]["backtest"]
    assert item["status"] == "unavailable"
    assert item["reason"] == "definition unavailable"


# ---------------------------------------------------------------------------
# Daily holdings
# ---------------------------------------------------------------------------


def _span_params(request):
    return (
        date.fromisoformat(request.query["start_date"]),
        date.fromisoformat(request.query["end_date"]),
    )


def test_holdings_are_read_in_contiguous_spans_from_the_earliest_window_to_the_as_of_day(router):
    completed_run()

    requests_ = router.holdings_requests(h.ACCOUNT_ID)
    spans = [_span_params(r) for r in requests_]
    first_day = date.fromisoformat(h.series_dates(h.OLDEST_ID)[0])
    assert spans[0][0] == first_day
    assert spans[-1][1] == AS_OF
    assert len(spans) == -(-((AS_OF - first_day).days + 1) // cpc.HOLDINGS_REQUEST_SPAN_DAYS)
    for first, last in spans:
        assert (last - first).days + 1 <= cpc.HOLDINGS_REQUEST_SPAN_DAYS
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:], strict=False):
        assert next_start == prev_end + timedelta(days=1)
    assert all(r.query["include_aggregate_account_holdings"] == "false" for r in requests_)


def test_holdings_reach_the_review_on_trading_days_only_and_feed_the_long_vix_item(
    router, inputs_seen
):
    recent = h.holdings_recent_body()
    outcome = completed_run()

    assert inputs_seen[0].holdings.failure is None
    expected = trading_day_holdings(recent)
    assert len(expected) < len(h.holdings_days(recent)), "fixture sanity: a weekend day is dropped"
    assert inputs_seen[0].holdings.days == expected
    item = entry(outcome, h.OLDEST_ID)["evidence"]["long_vix"]
    held, co_held = long_vix_counts(expected, h.OLDEST_ID, h.statistics_ids())
    peak_with, peak_without = long_vix_peak_shares(expected, h.OLDEST_ID, h.statistics_ids())
    assert held > 0, "the capture must hold a long-VIX product for this symphony"
    assert item["status"] == "available"
    assert item["window"]["days"] == sum(1 for by in expected.values() if h.OLDEST_ID in by)
    assert item["figures"]["held_days"]["value"] == held
    assert item["figures"]["co_held_days"]["value"] == co_held
    assert item["figures"]["peak_share_with"]["value"] == pytest.approx(peak_with, rel=1e-9)
    assert item["figures"]["peak_share_without"]["value"] == pytest.approx(peak_without, rel=1e-9)


def test_holdings_of_two_accounts_are_merged_by_date_and_symphony(router, inputs_seen):
    mine, theirs = split_roster(router)
    subsets = {
        h.ACCOUNT_ID: h.holdings_subset_body(mine),
        h.SECOND_ACCOUNT_ID: h.holdings_subset_body(theirs),
    }
    router.holdings_handler = lambda req, i: h.Reply(
        body=subsets[req.match["account"]] if i == 0 else h.holdings_emptied_body()
    )

    completed_run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])

    assert router.holdings_requests(h.ACCOUNT_ID) and router.holdings_requests(h.SECOND_ACCOUNT_ID)
    assert inputs_seen[0].holdings.days == trading_day_holdings(h.holdings_recent_body())


@pytest.mark.parametrize(("labels_kwargs", "names"), NAMING)
def test_a_partly_failed_holdings_read_feeds_the_days_it_got_and_names_the_account_for_the_shares(
    router, inputs_seen, labels_kwargs, names
):
    replies = {0: h.error_reply(500), 1: h.Reply(body=h.holdings_recent_body())}
    router.holdings_handler = lambda req, i: replies.get(i, h.Reply(body=h.holdings_emptied_body()))

    outcome = completed_run(**labels_kwargs)

    got = trading_day_holdings(h.holdings_recent_body())
    assert inputs_seen[0].holdings.failure is None
    assert inputs_seen[0].holdings.days == got
    item = entry(outcome, h.OLDEST_ID)["evidence"]["long_vix"]
    held, co_held = long_vix_counts(got, h.OLDEST_ID, h.statistics_ids())
    assert item["status"] == "available"
    assert item["figures"]["held_days"]["value"] == held
    assert item["figures"]["co_held_days"]["value"] == co_held
    for name in SHARE_FIGURES:
        figure = item["figures"][name]
        assert figure["value"] is None, "a share over an incomplete read is not shown"
        assert_reason_names_accounts(
            figure["reason"], "Composer unavailable", {names[0]: "partly read"}
        )


HOLDINGS_FAILURES = [
    pytest.param(lambda: h.error_reply(401), "refused", "Composer refused the request", id="401"),
    pytest.param(lambda: h.error_reply(403), "refused", "Composer refused the request", id="403"),
    pytest.param(lambda: h.error_reply(404), "not_found", "Composer returned not found", id="404"),
    pytest.param(lambda: h.error_reply(400), "rejected", "Composer rejected the request", id="400"),
    pytest.param(lambda: h.error_reply(409), "rejected", "Composer rejected the request", id="409"),
    pytest.param(lambda: h.error_reply(422), "rejected", "Composer rejected the request", id="422"),
    pytest.param(lambda: h.error_reply(429), "rate_limited", "Composer rate limit", id="429"),
    pytest.param(lambda: h.error_reply(500), "unavailable", "Composer unavailable", id="500"),
    pytest.param(
        lambda: h.raising(requests.Timeout("LEAKMARK")),
        "unavailable",
        "Composer unavailable",
        id="Timeout",
    ),
    pytest.param(h.non_json_200, "unreadable", "Composer response unreadable", id="non_json_200"),
]


@pytest.mark.parametrize(("make_reply", "failure", "reason"), HOLDINGS_FAILURES)
def test_holdings_that_cannot_be_read_make_the_long_vix_item_unavailable_not_zero(
    router, inputs_seen, make_reply, failure, reason
):
    router.set_all_holdings(make_reply())

    outcome = completed_run()

    assert inputs_seen[0].holdings.days == {}
    assert inputs_seen[0].holdings.failure == failure
    item = entry(outcome, h.OLDEST_ID)["evidence"]["long_vix"]
    assert item["status"] == "unavailable"
    assert item["reason"] == reason
    assert all(fig["value"] is None for fig in item["figures"].values())


# ---------------------------------------------------------------------------
# The request set, pacing and credentials
# ---------------------------------------------------------------------------

ALLOWED = {
    ("GET", "statistics"),
    ("GET", "series"),
    ("GET", "holdings"),
    ("GET", "score"),
    ("POST", "backtest"),
}
FORBIDDEN_FRAGMENTS = ("/deploy", "/go-to-cash", "/invest", "/sell", "/liquidate")


def test_a_complete_run_makes_only_reads_and_backtests_and_never_the_engine_stats_call(
    router, engine_stats_calls
):
    completed_run()

    assert router.violations == []
    for r in router.requests:
        assert (r.method, r.kind) in ALLOWED, f"{r.method} {r.path}"
        assert not any(fragment in r.path for fragment in FORBIDDEN_FRAGMENTS), r.path
        assert r.url.startswith(h.BASE_URL)
    assert {r.path for r in backtest_posts(router)} == {"/backtest"}
    assert len(router.of_kind("statistics")) == 1
    assert len(router.of_kind("series")) == len(h.statistics_ids())
    assert len(backtest_posts(router)) <= len(router.of_kind("score"))
    assert engine_stats_calls == []
    assert {r.kind for r in router.requests} == set(h.COMPOSER_KINDS), "fixture sanity"
    for r in router.requests:
        assert r.kwargs.get("allow_redirects") is False, f"{r.kind} must not follow redirects"


def test_every_request_carries_the_engine_credential_headers(router):
    completed_run()

    expected = alpha_bot_execution.get_composer_headers()
    assert router.requests
    for r in router.requests:
        assert r.headers["x-api-key-id"] == expected["x-api-key-id"]
        assert r.headers["authorization"] == expected["authorization"]


def test_every_statistics_series_and_holdings_read_is_followed_by_the_spacing_sleep(router):
    completed_run()

    h.assert_reads_paced(router, cpc.COMPOSER_READ_SPACING_SECONDS)
    assert cpc.COMPOSER_READ_SPACING_SECONDS == SPACING_SECONDS


def test_a_run_with_failures_still_makes_no_request_outside_the_allowlist(router):
    router.set_series(h.OLDEST_ID, h.error_reply(500))
    router.set_all_holdings(h.error_reply(500))
    router.set_backtest(h.error_reply(503))

    completed_run()

    assert router.violations == []
    assert all((r.method, r.kind) in ALLOWED for r in router.requests)


def tickers_of(inputs_seen, symphony_id):
    """The sorted tickers handed to the review for one symphony (asserted to be set)."""
    tickers = input_of(inputs_seen, symphony_id).current_tickers
    assert tickers is not None, f"{symphony_id} has logic_holdings in the state"
    return sorted(tickers)


def statistics_other_ids():
    """The roster symphonies without a captured series record."""
    return [sid for sid in h.statistics_ids() if sid not in h.CAPTURED_SERIES_IDS]


# ---------------------------------------------------------------------------
# Series read but not usable: the shadow fallback applies to it too
# ---------------------------------------------------------------------------

SERIES_SHAPES = ["all_null", "one_point", "after_as_of"]
_DAYS_PAST_THE_SERIES_MS = 900 * 86_400_000  # moves every captured point after the as-of day


def _reshaped_series(shape):
    """The captured oldest series with its levels or dates edited so that fewer than two
    points fall inside [invested-since, as-of] (derived edge forms of one captured record)."""
    body = h.record_body(h.series_record_name(h.OLDEST_ID))
    count = len(body["epoch_ms"])
    if shape == "all_null":
        body["deposit_adjusted_series"] = [None] * count
    elif shape == "one_point":
        body["deposit_adjusted_series"] = [None] * (count - 1) + [
            body["deposit_adjusted_series"][-1]
        ]
    elif shape == "after_as_of":
        body["epoch_ms"] = [ms + _DAYS_PAST_THE_SERIES_MS for ms in body["epoch_ms"]]
    else:
        raise AssertionError(shape)
    return h.Reply(body=body)


@pytest.mark.parametrize("shape", SERIES_SHAPES)
def test_a_series_read_that_leaves_fewer_than_two_usable_points_falls_back_to_shadow_history(
    router, inputs_seen, tmp_path, monkeypatch, shape
):
    bot_by_day, _ = seed_shadow(tmp_path, monkeypatch, h.OLDEST_ID, count=140)
    router.set_series(h.OLDEST_ID, _reshaped_series(shape))

    outcome = completed_run()

    sym = input_of(inputs_seen, h.OLDEST_ID)
    assert sym.shadow_returns is not None, "the shadow rows are read for an unusable series"
    assert dict(sym.shadow_returns) == bot_by_day
    e = entry(outcome, h.OLDEST_ID)
    assert e["basis"]["kind"] == "shadow"
    assert e["basis"]["label"] == SHADOW_BASIS
    assert e["basis"]["fallback_reason"] == SHORT_SERIES_REASON
    perf = e["evidence"]["performance"]
    assert perf["label"] == SHADOW_BASIS
    assert perf["window"]["days"] == shadow_window_days(bot_by_day, sym.invested_since)
    assert perf["figures"]["total_return"]["value"] == pytest.approx(
        b.oracle_metrics(list(bot_by_day.values()))["total_return"], rel=1e-9
    )


@pytest.mark.parametrize("shape", SERIES_SHAPES)
def test_a_series_read_that_leaves_fewer_than_two_usable_points_and_no_shadow_rows_has_no_basis(
    router, shape
):
    router.set_series(h.OLDEST_ID, _reshaped_series(shape))

    outcome = completed_run()

    e = entry(outcome, h.OLDEST_ID)
    assert e["basis"]["kind"] == "none"
    assert e["basis"]["fallback_reason"] == SHORT_SERIES_REASON
    perf = e["evidence"]["performance"]
    assert perf["status"] == "unavailable"
    assert perf["reason"] == "no history"


# ---------------------------------------------------------------------------
# Pacing and the number of requests
# ---------------------------------------------------------------------------

PHASE_OF = {"statistics": 0, "series": 1, "score": 2, "backtest": 2, "holdings": 3}


def _expected_request_counts(router, account_count, threshold=126):
    """The requests a run over the fixture roster calls for, derived from the fixtures: one
    statistics read per account; one series read per symphony; a definition read per symphony
    that has a captured series and a tenure of at least the threshold; a backtest per such
    symphony whose definition is served; and holdings spans from the earliest window start to
    the as-of day, once per account."""
    ids = h.statistics_ids()
    # a captured series has a point on every NYSE session from its first day, so its points up
    # to the as-of day are the trading-day tenure (see the fixture README)
    windowed = [
        sid
        for sid in ids
        if sid in h.CAPTURED_SERIES_IDS
        and sum(day <= AS_OF.isoformat() for day in h.series_dates(sid)) >= threshold
    ]
    assert windowed, "fixture sanity: some symphony qualifies"
    assert h.YOUNGEST_ID not in windowed
    with_definition = [
        sid
        for sid in windowed
        if sid in router.definition_by_symphony and router.definition_by_symphony[sid].status == 200
    ]
    earliest = min(date.fromisoformat(h.series_dates(sid)[0]) for sid in windowed)
    spans = -(-((AS_OF - earliest).days + 1) // cpc.HOLDINGS_REQUEST_SPAN_DAYS)
    return {
        "statistics": account_count,
        "series": len(ids),
        "score": len(windowed),
        "backtest": len(with_definition),
        "holdings": spans * account_count,
    }


@pytest.mark.parametrize(
    ("account_count", "extra_definitions"),
    [(1, ()), (2, (h.MID_ID, h.OLDEST_ID))],
    ids=["one_account_one_backtest", "two_accounts_three_backtests"],
)
def test_a_full_run_makes_exactly_the_requests_the_fixture_roster_calls_for(
    router, account_count, extra_definitions
):
    accounts = [h.ACCOUNT_ID]
    if account_count == 2:
        split_roster(router)
        accounts.append(h.SECOND_ACCOUNT_ID)
    captured_definition_for(router, *extra_definitions)
    expected = _expected_request_counts(router, account_count)

    completed_run(account_ids=accounts)

    assert Counter(r.kind for r in router.requests) == Counter(expected)
    assert len(router.requests) == sum(expected.values())
    assert router.violations == []


def test_requests_come_in_phases_statistics_then_series_then_definitions_and_backtests_then_holdings(
    router,
):
    captured_definition_for(router, h.MID_ID)

    completed_run()

    phases = [PHASE_OF[r.kind] for r in router.requests]
    assert phases == sorted(phases)
    assert len(router.of_kind("score")) >= 2 and len(backtest_posts(router)) >= 2


def test_at_least_the_spacing_separates_any_two_consecutive_requests_of_a_run(router):
    captured_definition_for(router, h.MID_ID)

    completed_run()

    assert len(router.of_kind("score")) >= 2 and len(backtest_posts(router)) >= 2
    h.assert_requests_spaced(router, SPACING_SECONDS)


BACKTEST_RETRY_TRIGGERS = [
    pytest.param(lambda: h.error_reply(500), id="500"),
    pytest.param(lambda: h.error_reply(503), id="503"),
    pytest.param(lambda: h.raising(requests.ConnectionError("LEAKMARK")), id="ConnectionError"),
]


@pytest.mark.parametrize("second_attempt_succeeds", [False, True], ids=["fails_twice", "recovers"])
@pytest.mark.parametrize("make_failure", BACKTEST_RETRY_TRIGGERS)
def test_the_spacing_also_separates_a_backtest_from_its_own_retry(
    router, inputs_seen, make_failure, second_attempt_succeeds
):
    failing = make_failure()
    success = h.reply_from_record("case5_backtest_dated_compact_nOyb55RMGVCKPiYXv7TI.json")
    router.backtest_handler = lambda req, i: (
        success if i == 1 and second_attempt_succeeds else failing
    )

    completed_run()

    assert len(backtest_posts(router)) == 2, "fixture sanity: the first failure is retried once"
    h.assert_requests_spaced(router, SPACING_SECONDS)
    recovered = input_of(inputs_seen, h.BACKTEST_ID).backtest.error is None
    assert recovered is second_attempt_succeeds


@pytest.mark.parametrize("make_exc", [lambda: ValueError(LEAK), lambda: RuntimeError(LEAK)])
def test_a_backtest_request_that_raises_is_still_followed_by_the_spacing_sleep(router, make_exc):
    captured_definition_for(router, h.MID_ID)
    router.set_backtest(h.raising(make_exc()))

    completed_run()

    assert len(backtest_posts(router)) == 2, "the next symphony is still backtested"
    h.assert_requests_spaced(router, SPACING_SECONDS)
    h.assert_reads_paced(router, SPACING_SECONDS)


def test_a_backtest_call_that_raises_after_posting_is_still_followed_by_the_spacing_sleep(
    router, monkeypatch
):
    captured_definition_for(router, h.MID_ID)
    real = cbc.run_backtest

    def posts_then_fails(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError(LEAK)

    monkeypatch.setattr(cbc, "run_backtest", posts_then_fails)

    completed_run()

    assert len(backtest_posts(router)) == 2
    h.assert_requests_spaced(router, SPACING_SECONDS)


# ---------------------------------------------------------------------------
# The spacing is a parameter of the run
# ---------------------------------------------------------------------------


def test_the_read_spacing_is_a_keyword_only_parameter_that_defaults_to_the_client_constant():
    parameters = inspect.signature(runner.run_tenure_review).parameters
    assert "read_spacing_seconds" in parameters
    parameter = parameters["read_spacing_seconds"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default == cpc.COMPOSER_READ_SPACING_SECONDS == SPACING_SECONDS


def test_a_default_run_sleeps_only_the_default_spacing(router):
    captured_definition_for(router, h.MID_ID)

    completed_run()

    assert len(backtest_posts(router)) >= 2
    assert set(router.sleeps) == {SPACING_SECONDS}


INVALID_SPACINGS = [
    pytest.param(-1.0, id="negative"),
    pytest.param(-0.001, id="slightly_negative"),
    pytest.param(math.nan, id="nan"),
    pytest.param(math.inf, id="infinity"),
    pytest.param(-math.inf, id="negative_infinity"),
    pytest.param(None, id="none"),
    pytest.param("6.0", id="numeric_string"),
    pytest.param(LEAK, id="text"),
    pytest.param([6.0], id="list"),
]


def spacing_warnings(caplog):
    return [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and "read_spacing_seconds" in r.getMessage()
    ]


@pytest.mark.parametrize("bad", INVALID_SPACINGS)
def test_a_read_spacing_that_is_not_a_finite_number_of_zero_or_more_falls_back_to_the_default(
    router, caplog, bad
):
    caplog.set_level(logging.DEBUG)
    captured_definition_for(router, h.MID_ID)

    completed_run(read_spacing_seconds=bad)

    assert router.sleeps
    assert all(slept == cpc.COMPOSER_READ_SPACING_SECONDS for slept in router.sleeps)
    h.assert_reads_paced(router, cpc.COMPOSER_READ_SPACING_SECONDS)
    h.assert_requests_spaced(router, cpc.COMPOSER_READ_SPACING_SECONDS)
    assert len(spacing_warnings(caplog)) == 1, "one WARNING line names the parameter"
    assert LEAK not in caplog.text, "the offending value is not echoed into a log line"


@pytest.mark.parametrize("good", [0, 0.0, 1, 2.5, WIDENED_SPACING_SECONDS])
def test_a_read_spacing_of_zero_or_more_is_used_exactly_as_given(router, caplog, good):
    caplog.set_level(logging.DEBUG)

    completed_run(read_spacing_seconds=good)

    assert set(router.sleeps) == {good}
    assert spacing_warnings(caplog) == []


SPACING_SCENARIOS = [
    pytest.param(lambda r: None, id="all_answered"),
    pytest.param(
        lambda r: r.set_series(h.OLDEST_ID, h.raising(requests.ConnectionError("LEAKMARK"))),
        id="series_read_raises",
    ),
    pytest.param(
        lambda r: r.definition_by_symphony.update(
            {h.BACKTEST_ID: h.raising(requests.Timeout("LEAKMARK"))}
        ),
        id="definition_read_raises",
    ),
    pytest.param(
        lambda r: r.set_all_holdings(h.raising(requests.Timeout("LEAKMARK"))),
        id="every_holdings_span_raises",
    ),
    pytest.param(lambda r: r.set_backtest(h.error_reply(500)), id="backtest_retried_after_500"),
    pytest.param(
        lambda r: r.set_backtest(h.raising(requests.ConnectionError("LEAKMARK"))),
        id="backtest_retried_after_transport_error",
    ),
    pytest.param(
        lambda r: r.set_backtest(h.raising(RuntimeError("LEAKMARK"))), id="backtest_raises"
    ),
]


@pytest.mark.parametrize("spacing", [None, WIDENED_SPACING_SECONDS], ids=["default", "widened"])
@pytest.mark.parametrize("arrange", SPACING_SCENARIOS)
def test_every_request_of_a_run_is_separated_by_the_read_spacing_whatever_the_one_before_did(
    router, arrange, spacing
):
    captured_definition_for(router, h.MID_ID)
    arrange(router)
    expected = SPACING_SECONDS if spacing is None else spacing

    completed_run(**({} if spacing is None else {"read_spacing_seconds": spacing}))

    assert len(router.requests) > 3, "fixture sanity: the run went on after the failure"
    h.assert_reads_paced(router, expected)
    h.assert_requests_spaced(router, expected)
    assert set(router.sleeps) == {expected}, "no other wait appears anywhere in the run"


# ---------------------------------------------------------------------------
# The first 429 ends the run's requests
# ---------------------------------------------------------------------------


def _assert_run_ended_at(router, limited_request):
    assert router.requests[-1] is limited_request, "nothing is requested after the 429"


def _assert_remaining_items_read_rate_limited(outcome, symphony_ids):
    for sid in symphony_ids:
        e = entry(outcome, sid)
        assert e["eligible"] is True, sid
        for item in ("backtest", "long_vix"):
            evidence = e["evidence"][item]
            assert evidence["status"] == "unavailable", (sid, item)
            assert evidence["reason"] == RATE_LIMIT_REASON, (sid, item)


def test_a_429_on_the_first_accounts_statistics_requests_nothing_further(router):
    split_roster(router)
    router.statistics_by_account[h.ACCOUNT_ID] = h.error_reply(429)

    outcome = run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])

    assert outcome.completed is False
    assert [r.match["account"] for r in router.requests] == [h.ACCOUNT_ID]


def test_a_429_on_a_series_read_ends_the_requests_and_every_later_item_reads_rate_limited(
    router, inputs_seen, tmp_path, monkeypatch
):
    ids = h.statistics_ids()
    position = ids.index(h.BACKTEST_ID)
    assert ids.index(h.MID_ID) < position < ids.index(h.OLDEST_ID), "fixture sanity: roster order"
    seed_shadow_many(tmp_path, monkeypatch, [h.BACKTEST_ID, h.OLDEST_ID])
    router.set_series(h.BACKTEST_ID, h.error_reply(429))

    outcome = completed_run()

    limited = router.series_requests(h.BACKTEST_ID)
    assert len(limited) == 1
    _assert_run_ended_at(router, limited[0])
    assert {r.match["sid"] for r in router.of_kind("series")} == set(ids[: position + 1])
    for sid in ids[position:]:
        assert input_of(inputs_seen, sid).series_failure == "rate_limited", sid
        assert entry(outcome, sid)["basis"]["fallback_reason"] == RATE_LIMIT_REASON, sid
    assert input_of(inputs_seen, h.MID_ID).series_failure is None
    assert entry(outcome, h.BACKTEST_ID)["basis"]["kind"] == "shadow"
    _assert_remaining_items_read_rate_limited(outcome, [h.MID_ID, h.BACKTEST_ID, h.OLDEST_ID])


def test_a_429_on_a_definition_read_ends_the_requests_and_every_later_item_reads_rate_limited(
    router,
):
    router.definition_by_symphony[h.MID_ID] = h.error_reply(429)

    outcome = completed_run()

    scores = router.of_kind("score")
    assert [r.match["sid"] for r in scores] == [h.MID_ID]
    _assert_run_ended_at(router, scores[0])
    assert backtest_posts(router) == []
    _assert_remaining_items_read_rate_limited(outcome, [h.MID_ID, h.BACKTEST_ID, h.OLDEST_ID])


def test_a_429_on_a_backtest_is_not_retried_and_ends_the_requests(router):
    captured_definition_for(router, h.MID_ID)
    router.set_backtest(h.error_reply(429))

    outcome = completed_run()

    posts = backtest_posts(router)
    assert len(posts) == 1
    _assert_run_ended_at(router, posts[0])
    assert [r.match["sid"] for r in router.of_kind("score")] == [h.MID_ID]
    _assert_remaining_items_read_rate_limited(outcome, [h.MID_ID, h.BACKTEST_ID, h.OLDEST_ID])


@pytest.mark.parametrize("two_accounts", [False, True], ids=["one_account", "two_accounts"])
def test_a_429_on_a_holdings_read_ends_the_requests_and_the_item_reads_rate_limited(
    router, two_accounts
):
    accounts = [h.ACCOUNT_ID]
    if two_accounts:
        split_roster(router)
        accounts.append(h.SECOND_ACCOUNT_ID)
    router.set_all_holdings(h.error_reply(429))

    outcome = completed_run(account_ids=accounts)

    holdings = router.of_kind("holdings")
    assert len(holdings) == 1, "the other spans and the other account are never requested"
    _assert_run_ended_at(router, holdings[0])
    for sid in (h.MID_ID, h.BACKTEST_ID, h.OLDEST_ID):
        item = entry(outcome, sid)["evidence"]["long_vix"]
        assert item["status"] == "unavailable"
        assert item["reason"] == RATE_LIMIT_REASON


# ---------------------------------------------------------------------------
# No request follows a redirect; a symphony id is never sent unless it is a plain id
# ---------------------------------------------------------------------------


NOT_PLAIN_ACCOUNT_IDS = [
    pytest.param(".", id="single_dot"),
    pytest.param("..", id="double_dot"),
    pytest.param("", id="empty"),
    pytest.param("ab/cd", id="slash"),
    pytest.param("ab?x=1", id="query"),
    pytest.param("ab#frag", id="fragment"),
    pytest.param("ab%2Fcd", id="percent_escape"),
    pytest.param("ab cd", id="space"),
]


@pytest.mark.parametrize("bad", NOT_PLAIN_ACCOUNT_IDS)
def test_an_account_id_that_is_not_a_plain_id_ends_the_run_before_any_request(router, bad):
    outcome = run(account_ids=[bad])

    assert outcome.completed is False
    assert outcome.failure_reason == "Composer statistics unreadable"
    assert router.requests == [], "a refused id builds no URL, so nothing is sent"
    assert router.violations == []


@pytest.mark.parametrize("bad", NOT_PLAIN_ACCOUNT_IDS)
def test_a_refused_account_after_a_readable_one_costs_only_the_readable_accounts_statistics(
    router, bad
):
    outcome = run(account_ids=[h.ACCOUNT_ID, bad])

    assert outcome.completed is False
    assert outcome.failure_reason == "Composer statistics unreadable"
    assert [(r.kind, r.match.get("account")) for r in router.requests] == [
        ("statistics", h.ACCOUNT_ID)
    ]


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_answer_to_the_backtest_is_not_followed_or_retried_and_reads_unavailable(
    router, status
):
    router.set_backtest(h.redirect_reply(status))

    outcome = completed_run()

    posts = backtest_posts(router)
    assert len(posts) == 1, "a redirect is not retried"
    assert posts[0].kwargs.get("allow_redirects") is False
    item = entry(outcome, h.BACKTEST_ID)["evidence"]["backtest"]
    assert item["status"] == "unavailable"
    assert item["reason"]
    assert "redirect.invalid" not in json.dumps(outcome.result.entries, default=str)


@pytest.mark.parametrize("bad_id", [".", "..", "ab/cd"])
def test_a_roster_symphony_whose_id_is_not_a_plain_id_segment_reads_unreadable_and_is_not_requested(
    router, inputs_seen, bad_id
):
    victim = statistics_other_ids()[0]
    body = h.statistics_body()
    for item in body["symphonies"]:
        if item["id"] == victim:
            item["id"] = bad_id
    router.statistics_default = h.Reply(body=body)

    outcome = completed_run()

    assert router.violations == []
    requested = {r.match["sid"] for r in router.of_kind("series") + router.of_kind("score")}
    assert requested <= set(h.statistics_ids()) - {victim}, "the refused id builds no URL"
    assert {r.match["sid"] for r in router.of_kind("series")} == set(h.statistics_ids()) - {victim}
    assert input_of(inputs_seen, bad_id).series_failure == "unreadable"
    assert entry(outcome, bad_id)["basis"]["fallback_reason"] == "Composer response unreadable"
    assert entry(outcome, h.OLDEST_ID)["basis"]["kind"] == "composer", "the others are read"


# ---------------------------------------------------------------------------
# Holdings: every account from the one earliest start; an unreadable account is named
# ---------------------------------------------------------------------------


def test_every_account_is_read_from_the_single_earliest_window_start(router):
    mine, theirs = split_roster(router)

    completed_run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])

    earliest = date.fromisoformat(h.series_dates(h.OLDEST_ID)[0])
    own_earliest = min(
        date.fromisoformat(h.series_dates(sid)[0]) for sid in mine if sid in h.CAPTURED_SERIES_IDS
    )
    assert h.OLDEST_ID in theirs and own_earliest > earliest, "fixture sanity"
    for account in (h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID):
        spans = [_span_params(r) for r in router.holdings_requests(account)]
        assert spans[0][0] == earliest, account
        assert spans[-1][1] == AS_OF, account


SECOND_ACCOUNT_FAILURES = [
    pytest.param(lambda: h.error_reply(401), "Composer refused the request", id="401"),
    pytest.param(lambda: h.error_reply(404), "Composer returned not found", id="404"),
    pytest.param(lambda: h.error_reply(422), "Composer rejected the request", id="422"),
    pytest.param(lambda: h.error_reply(429), "Composer rate limit", id="429"),
    pytest.param(lambda: h.error_reply(500), "Composer unavailable", id="500"),
    pytest.param(
        lambda: h.raising(requests.Timeout("LEAKMARK")), "Composer unavailable", id="Timeout"
    ),
    pytest.param(h.non_json_200, "Composer response unreadable", id="non_json_200"),
]


@pytest.mark.parametrize(("labels_kwargs", "names"), NAMING)
@pytest.mark.parametrize(("make_reply", "class_reason"), SECOND_ACCOUNT_FAILURES)
def test_an_account_whose_holdings_cannot_be_read_keeps_its_failure_class_and_is_named(
    router, inputs_seen, make_reply, class_reason, labels_kwargs, names
):
    first, second = names
    mine, theirs = split_roster(router)
    readable = serve_split_holdings(router, mine, lambda index: make_reply())

    outcome = completed_run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID], **labels_kwargs)

    got = trading_day_holdings(readable)
    assert inputs_seen[0].holdings.days == got
    held, co_held = long_vix_counts(got, h.MID_ID, h.statistics_ids())
    assert held > 0 and co_held > 0, "fixture sanity: the readable account has the cases"
    item = entry(outcome, h.MID_ID)["evidence"]["long_vix"]
    assert item["status"] == "available"
    assert item["figures"]["held_days"]["value"] == held
    assert item["figures"]["co_held_days"]["value"] == co_held
    for name in SHARE_FIGURES:
        figure = item["figures"][name]
        assert figure["value"] is None, name
        assert_reason_names_accounts(
            figure["reason"], class_reason, {second: "not read"}, readable=[first]
        )
    for sid in (h.BACKTEST_ID, h.OLDEST_ID):
        assert sid in theirs
        missing = entry(outcome, sid)["evidence"]["long_vix"]
        assert missing["status"] == "unavailable", sid
        assert "no holdings for this symphony" not in missing["reason"], sid
        assert_reason_names_accounts(
            missing["reason"], class_reason, {second: "not read"}, readable=[first]
        )
        assert all(fig["value"] is None for fig in missing["figures"].values()), sid


def test_a_429_on_the_second_accounts_holdings_ends_the_requests_after_that_one(router):
    mine, _ = split_roster(router)
    serve_split_holdings(router, mine, lambda index: h.error_reply(429))

    completed_run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])

    requested = router.holdings_requests(h.SECOND_ACCOUNT_ID)
    assert len(requested) == 1, "the other spans of the account are never requested"
    _assert_run_ended_at(router, requested[0])


@pytest.mark.parametrize(("labels_kwargs", "names"), NAMING)
def test_a_429_on_a_later_holdings_span_keeps_the_rate_limit_reason_and_says_partly_read(
    router, inputs_seen, labels_kwargs, names
):
    recent = h.holdings_recent_body()
    replies = {0: h.Reply(body=recent), 1: h.error_reply(429)}
    router.holdings_handler = lambda req, i: replies.get(i, h.Reply(body=h.holdings_emptied_body()))

    outcome = completed_run(**labels_kwargs)

    holdings = router.of_kind("holdings")
    assert len(holdings) == 2, "nothing is requested after the 429"
    _assert_run_ended_at(router, holdings[1])
    got = trading_day_holdings(recent)
    assert inputs_seen[0].holdings.days == got
    assert inputs_seen[0].holdings.failure is None, "days were read, so the read did not fail"
    items = available_long_vix(outcome)
    assert h.OLDEST_ID in items, "fixture sanity: the days read give this symphony an item"
    held, co_held = long_vix_counts(got, h.OLDEST_ID, h.statistics_ids())
    assert items[h.OLDEST_ID]["figures"]["held_days"]["value"] == held
    assert items[h.OLDEST_ID]["figures"]["co_held_days"]["value"] == co_held
    for sid, item in items.items():
        for name in SHARE_FIGURES:
            figure = item["figures"][name]
            assert figure["value"] is None, (sid, name)
            assert_reason_names_accounts(
                figure["reason"],
                RATE_LIMIT_REASON,
                {names[0]: "partly read"},
                readable=[names[1]],
            )


@pytest.mark.parametrize(("labels_kwargs", "names"), NAMING)
def test_a_429_after_the_first_accounts_first_span_names_both_accounts_by_what_was_read(
    router, inputs_seen, labels_kwargs, names
):
    first, second = names
    mine, theirs = split_roster(router)
    readable = h.holdings_subset_body(mine)
    replies = {0: h.Reply(body=readable), 1: h.error_reply(429)}
    router.holdings_handler = lambda req, i: replies.get(i, h.Reply(body=h.holdings_emptied_body()))

    outcome = completed_run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID], **labels_kwargs)

    holdings = router.of_kind("holdings")
    assert [r.match["account"] for r in holdings] == [h.ACCOUNT_ID] * 2
    _assert_run_ended_at(router, holdings[-1])
    assert inputs_seen[0].holdings.days == trading_day_holdings(readable)
    items = available_long_vix(outcome)
    assert h.MID_ID in items
    for sid, item in items.items():
        for name in SHARE_FIGURES:
            assert item["figures"][name]["value"] is None, (sid, name)
            assert_reason_names_accounts(
                item["figures"][name]["reason"],
                RATE_LIMIT_REASON,
                {first: "partly read", second: "not read"},
            )
    for sid in (h.BACKTEST_ID, h.OLDEST_ID):
        assert sid in theirs
        missing = entry(outcome, sid)["evidence"]["long_vix"]
        assert missing["status"] == "unavailable", sid
        assert "no holdings for this symphony" not in missing["reason"], sid
        assert RATE_LIMIT_REASON in missing["reason"], sid
        assert f"holdings not read for {second}" in missing["reason"], sid
        assert h.SECOND_ACCOUNT_ID not in missing["reason"], sid


# What the second account's spans answer, in order (the last token repeats for every later
# span). A 429 ends the run's requests, so the spans after it are never asked.
MIXED_SPAN_SEQUENCES = [
    pytest.param([500, 429], id="500_then_429"),
    pytest.param(["ok", 500, 429], id="read_then_500_then_429"),
    pytest.param([401, 500, 429], id="three_classes"),
    pytest.param([500, 401], id="500_then_401"),
    pytest.param([401, 403, 404], id="401_403_are_one_class_then_404"),
    pytest.param([500, "timeout", 401], id="500_and_timeout_are_one_class_then_401"),
    pytest.param([500, 500, 500, 429], id="a_repeated_class_is_named_once"),
    pytest.param(["ok", "garbled", 500], id="read_then_unreadable_then_500"),
    pytest.param([500, "ok"], id="one_class_partly_read"),
]


@pytest.mark.parametrize(("labels_kwargs", "names"), NAMING)
@pytest.mark.parametrize("tokens", MIXED_SPAN_SEQUENCES)
def test_an_account_whose_spans_fail_with_several_classes_names_each_class_in_the_order_first_seen(
    router, labels_kwargs, names, tokens
):
    first, second = names
    mine, theirs = split_roster(router)
    serve_split_holdings(
        router, mine, lambda index: span_reply(tokens[min(index, len(tokens) - 1)])
    )

    outcome = completed_run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID], **labels_kwargs)

    requested = router.holdings_requests(h.SECOND_ACCOUNT_ID)
    if 429 in tokens:
        assert len(requested) == tokens.index(429) + 1, "the 429 ends the account's spans"
        _assert_run_ended_at(router, requested[-1])
    expected = span_clauses(tokens, second)
    item = entry(outcome, h.MID_ID)["evidence"]["long_vix"]
    assert item["status"] == "available"
    for name in SHARE_FIGURES:
        assert item["figures"][name]["value"] is None, name
        assert_class_clauses(item["figures"][name]["reason"], expected, absent=[first])
    for sid in (h.BACKTEST_ID, h.OLDEST_ID):
        assert sid in theirs
        missing = entry(outcome, sid)["evidence"]["long_vix"]
        assert missing["status"] == "unavailable", sid
        assert "no holdings for this symphony" not in missing["reason"], sid
        assert_class_clauses(missing["reason"], expected, absent=[first])
        for figure in missing["figures"].values():
            assert_class_clauses(figure["reason"], expected, absent=[first])


@pytest.mark.parametrize(("labels_kwargs", "names"), NAMING)
def test_two_accounts_failing_with_different_classes_each_read_their_own_class_and_label(
    router, labels_kwargs, names
):
    first, second = names
    mine, theirs = split_roster(router)
    first_account_days = h.holdings_subset_body(mine)

    def handler(request, index):
        if request.match["account"] == h.ACCOUNT_ID:
            return h.Reply(body=first_account_days) if index == 0 else h.error_reply(500)
        return span_reply("ok") if index == 0 else h.error_reply(401)

    router.holdings_handler = handler

    outcome = completed_run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID], **labels_kwargs)

    item = entry(outcome, h.MID_ID)["evidence"]["long_vix"]
    assert item["status"] == "available"
    for name in SHARE_FIGURES:
        assert item["figures"][name]["value"] is None, name
        assert_class_clauses(
            item["figures"][name]["reason"],
            [
                ("Composer unavailable", "partly read", first),
                ("Composer refused the request", "partly read", second),
            ],
        )
    for sid in (h.BACKTEST_ID, h.OLDEST_ID):
        assert sid in theirs
        missing = entry(outcome, sid)["evidence"]["long_vix"]
        assert missing["status"] == "unavailable", sid
        assert_class_clauses(
            missing["reason"],
            [("Composer refused the request", "partly read", second)],
            absent=[first],
        )


@pytest.mark.parametrize(("labels_kwargs", "names"), NAMING)
def test_a_first_account_with_two_classes_and_an_account_the_429_kept_unrequested_name_each_clause(
    router, labels_kwargs, names
):
    first, second = names
    mine, theirs = split_roster(router)
    first_account_days = h.holdings_subset_body(mine)
    replies = {0: h.Reply(body=first_account_days), 1: h.error_reply(500), 2: h.error_reply(429)}
    router.holdings_handler = lambda request, index: replies[index]

    outcome = completed_run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID], **labels_kwargs)

    assert [r.match["account"] for r in router.of_kind("holdings")] == [h.ACCOUNT_ID] * 3
    item = entry(outcome, h.MID_ID)["evidence"]["long_vix"]
    assert item["status"] == "available"
    for name in SHARE_FIGURES:
        assert_class_clauses(
            item["figures"][name]["reason"],
            [
                ("Composer unavailable", "partly read", first),
                (RATE_LIMIT_REASON, "partly read", first),
                (RATE_LIMIT_REASON, "not read", second),
            ],
        )
    for sid in (h.BACKTEST_ID, h.OLDEST_ID):
        assert sid in theirs
        missing = entry(outcome, sid)["evidence"]["long_vix"]
        assert missing["status"] == "unavailable", sid
        assert_class_clauses(
            missing["reason"], [(RATE_LIMIT_REASON, "not read", second)], absent=[first]
        )


PARTIAL_LABELS = [
    pytest.param({h.ACCOUNT_ID: "Individual"}, ("Individual", "account 2"), id="second_unlabelled"),
    pytest.param(
        {h.SECOND_ACCOUNT_ID: "Roth IRA"}, ("account 1", "Roth IRA"), id="first_unlabelled"
    ),
]


@pytest.mark.parametrize(("labels", "names"), PARTIAL_LABELS)
def test_an_account_without_a_label_reads_its_position_while_a_labelled_one_keeps_its_label(
    router, labels, names
):
    first, second = names
    mine, _ = split_roster(router)
    replies = {0: h.Reply(body=h.holdings_subset_body(mine)), 1: h.error_reply(429)}
    router.holdings_handler = lambda request, index: replies[index]

    outcome = completed_run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID], account_labels=labels)

    item = entry(outcome, h.MID_ID)["evidence"]["long_vix"]
    assert item["status"] == "available"
    for name in SHARE_FIGURES:
        assert_class_clauses(
            item["figures"][name]["reason"],
            [(RATE_LIMIT_REASON, "partly read", first), (RATE_LIMIT_REASON, "not read", second)],
        )


def test_when_every_account_is_readable_the_shares_have_values(router):
    mine, theirs = split_roster(router)
    subsets = {
        h.ACCOUNT_ID: h.holdings_subset_body(mine),
        h.SECOND_ACCOUNT_ID: h.holdings_subset_body(theirs),
    }
    router.holdings_handler = lambda req, i: h.Reply(
        body=subsets[req.match["account"]] if i == 0 else h.holdings_emptied_body()
    )

    outcome = completed_run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])

    for sid in (h.MID_ID, h.BACKTEST_ID, h.OLDEST_ID):
        item = entry(outcome, sid)["evidence"]["long_vix"]
        assert item["status"] == "available", sid
        for name in ("peak_share_with", "peak_share_without"):
            assert item["figures"][name]["value"] is not None, (sid, name)


# ---------------------------------------------------------------------------
# Current tickers
# ---------------------------------------------------------------------------


def test_current_tickers_are_the_logic_holdings_keys_of_the_symphonys_own_state_entry(
    router, inputs_seen, tmp_path, monkeypatch
):
    seed_logic_holdings(
        tmp_path,
        monkeypatch,
        {
            h.OLDEST_ID: {"VIXY": 120.5, "SPY": 44.0},
            h.MID_ID: {"QQQ": 9.0},
            h.BACKTEST_ID: {},
            h.YOUNGEST_ID: None,
        },
    )

    completed_run()

    assert tickers_of(inputs_seen, h.OLDEST_ID) == ["SPY", "VIXY"]
    assert tickers_of(inputs_seen, h.MID_ID) == ["QQQ"]
    assert not input_of(inputs_seen, h.BACKTEST_ID).current_tickers
    for sid in (h.YOUNGEST_ID, *statistics_other_ids()):
        assert input_of(inputs_seen, sid).current_tickers is None, sid


def test_a_state_entry_kept_under_the_display_name_is_not_the_symphonys_holdings(
    router, inputs_seen, tmp_path, monkeypatch
):
    display_name = {e["id"]: e["name"] for e in h.statistics_body()["symphonies"]}[h.MID_ID]
    seed_logic_holdings(
        tmp_path,
        monkeypatch,
        {display_name.strip(): {"SPY": 3.0}, h.MID_ID: {"QQQ": 9.0}, h.OLDEST_ID: None},
    )

    completed_run()

    assert tickers_of(inputs_seen, h.MID_ID) == ["QQQ"]
    assert input_of(inputs_seen, h.OLDEST_ID).current_tickers is None


def test_a_state_that_cannot_be_read_leaves_every_ticker_set_unset_and_the_run_completes(
    router, inputs_seen, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)

    def unreadable():
        raise RuntimeError("LEAKMARK-state")

    monkeypatch.setattr(database, "load_state", unreadable)

    completed_run()

    assert all(sym.current_tickers is None for sym in inputs_seen[0].symphonies)
    assert "LEAKMARK" not in caplog.text


# ---------------------------------------------------------------------------
# Display names
# ---------------------------------------------------------------------------

_USE_ID = object()
NAME_CASES = [
    pytest.param("  Padded Name \t", "Padded Name", id="padded"),
    pytest.param(_DROP, _USE_ID, id="missing"),
    pytest.param(None, _USE_ID, id="null"),
    pytest.param("", _USE_ID, id="empty"),
    pytest.param(" \t\n ", _USE_ID, id="blank"),
]


@pytest.mark.parametrize(("raw", "shown"), NAME_CASES)
def test_a_display_name_is_trimmed_and_a_blank_one_is_the_composer_id_wherever_it_appears(
    router, inputs_seen, raw, shown
):
    expected = h.YOUNGEST_ID if shown is _USE_ID else shown
    edit_statistics(router, {h.YOUNGEST_ID: {"name": raw}})

    outcome = completed_run()

    assert input_of(inputs_seen, h.YOUNGEST_ID).display_name == expected
    assert entry(outcome, h.YOUNGEST_ID)["display_name"] == expected
    marginal = b.item_of(entry(outcome, h.OLDEST_ID), "marginal")
    assert marginal["status"] == "available", "fixture sanity: the comparison is made"
    assert expected in {record["display_name"] for record in marginal["excluded"]}


# ---------------------------------------------------------------------------
# The window comes from the review module's public function
# ---------------------------------------------------------------------------


def test_the_windows_come_from_the_review_modules_public_qualifying_window(router, monkeypatch):
    seen: list[tuple] = []
    real = tr.qualifying_window
    backtest_first, backtest_last = (
        h.series_dates(h.BACKTEST_ID)[10],
        h.series_dates(h.BACKTEST_ID)[-3],
    )
    oldest_first = h.series_dates(h.OLDEST_ID)[300]
    mid_first = h.series_dates(h.MID_ID)[0]
    assert oldest_first < mid_first, "fixture sanity: the patched start is the earliest"

    def patched(symphony, as_of, threshold):
        seen.append((symphony.symphony_id, as_of, threshold))
        if symphony.symphony_id == h.BACKTEST_ID:
            return {"first": backtest_first, "last": backtest_last, "days": 7}
        if symphony.symphony_id == h.OLDEST_ID:
            window = real(symphony, as_of, threshold)
            return {**window, "first": oldest_first}
        return real(symphony, as_of, threshold)

    monkeypatch.setattr(tr, "qualifying_window", patched)

    completed_run()

    assert {sid for sid, _, _ in seen} == set(h.statistics_ids())
    assert all(as_of == AS_OF and threshold == 126 for _, as_of, threshold in seen)
    posts = backtest_posts(router)
    assert len(posts) == 1
    assert posts[0].body_json()["start_date"] == backtest_first
    assert posts[0].body_json()["end_date"] == backtest_last
    holdings_start = _span_params(router.holdings_requests(h.ACCOUNT_ID)[0])[0]
    assert holdings_start == date.fromisoformat(oldest_first)


def test_the_runner_uses_no_private_name_of_another_module():
    tree = ast.parse((REPO / "advisors" / "tenure_review_runner.py").read_text("utf-8"))
    private: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("_")
            and not node.attr.startswith("__")
            and not (isinstance(node.value, ast.Name) and node.value.id in {"self", "cls"})
        ):
            private.append(node.attr)
        if isinstance(node, ast.ImportFrom):
            private += [
                alias.name
                for alias in node.names
                if alias.name.startswith("_") and not alias.name.startswith("__")
            ]
    assert private == []


# ---------------------------------------------------------------------------
# An exception in one symphony's definition or backtest never ends the run
# ---------------------------------------------------------------------------


def test_an_exception_inside_the_backtest_call_makes_only_that_symphonys_item_unavailable(
    router, inputs_seen, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    captured_definition_for(router, h.MID_ID)
    real = cbc.run_backtest
    calls: list = []

    def flaky(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError(LEAK)
        return real(*args, **kwargs)

    monkeypatch.setattr(cbc, "run_backtest", flaky)

    outcome = completed_run()

    assert len(calls) == 2, "the next symphony is still backtested"
    broken = entry(outcome, h.MID_ID)
    assert broken["evidence"]["backtest"]["status"] == "unavailable"
    assert broken["evidence"]["backtest"]["reason"]
    assert "backtest" in broken["missing_items"]
    sibling = input_of(inputs_seen, h.BACKTEST_ID).backtest
    assert sibling is not None and sibling.error is None and sibling.first_day
    assert LEAK not in caplog.text
    assert LEAK not in json.dumps(outcome.result.entries, default=str)


def test_an_unexpected_exception_from_the_backtest_request_is_isolated_to_each_symphony(
    router, caplog
):
    caplog.set_level(logging.DEBUG)
    captured_definition_for(router, h.MID_ID)
    router.set_backtest(h.raising(ValueError(LEAK)))

    outcome = completed_run()

    assert len(backtest_posts(router)) == 2
    for sid in (h.MID_ID, h.BACKTEST_ID):
        item = entry(outcome, sid)["evidence"]["backtest"]
        assert item["status"] == "unavailable", sid
        assert LEAK not in item["reason"], sid
    assert LEAK not in caplog.text


def test_an_unexpected_exception_from_a_definition_read_is_isolated_to_that_symphony(
    router, inputs_seen, caplog
):
    caplog.set_level(logging.DEBUG)
    router.definition_by_symphony[h.MID_ID] = h.raising(RuntimeError(LEAK))

    outcome = completed_run()

    item = entry(outcome, h.MID_ID)["evidence"]["backtest"]
    assert item["status"] == "unavailable"
    assert LEAK not in item["reason"]
    assert len(backtest_posts(router)) == 1
    assert input_of(inputs_seen, h.BACKTEST_ID).backtest is not None
    assert LEAK not in caplog.text


# ---------------------------------------------------------------------------
# Logs: no credential, response body or exception message
# ---------------------------------------------------------------------------


def _marked_backtest_400():
    body = h.record_body("case7c_backtest_malformed_structure_400.json")
    return h.Reply(status=400, body={"marker": LEAK, **body})


def _marked_backtest_413():
    text = h.record_body("case6_backtest_413_too_large.json")
    return h.Reply(status=413, body=f"<!-- {LEAK} -->{text}", content_type="text/html")


BACKTEST_LEAK_REPLIES = [
    pytest.param(
        lambda: h.reply_from_record("case5_backtest_dated_compact_nOyb55RMGVCKPiYXv7TI.json"),
        id="success",
    ),
    pytest.param(_marked_backtest_400, id="400"),
    pytest.param(_marked_backtest_413, id="413"),
    pytest.param(lambda: h.marked_error_reply(429, LEAK), id="429"),
    pytest.param(lambda: h.marked_error_reply(500, LEAK), id="500"),
    pytest.param(lambda: h.raising(requests.Timeout(LEAK)), id="Timeout"),
    pytest.param(lambda: h.raising(requests.ConnectionError(LEAK)), id="ConnectionError"),
    pytest.param(lambda: h.raising(ValueError(LEAK)), id="unexpected"),
    pytest.param(lambda: h.Reply(status=200, body=f"<html>{LEAK}</html>"), id="non_json_200"),
]


@pytest.mark.parametrize("make_reply", BACKTEST_LEAK_REPLIES)
def test_no_log_line_of_a_run_carries_a_credential_a_response_body_or_an_exception_message(
    router, caplog, make_reply
):
    caplog.set_level(logging.DEBUG)
    captured_definition_for(router, h.MID_ID)
    router.set_series(h.OLDEST_ID, h.marked_error_reply(500, LEAK))
    router.set_all_holdings(h.marked_error_reply(500, LEAK))
    router.set_backtest(make_reply())

    outcome = completed_run()

    assert backtest_posts(router), "fixture sanity: the backtest path ran"
    assert any(r.name.startswith("advisors.") for r in caplog.records), "the capture saw the run"
    assert "LEAKMARK" not in caplog.text
    for secret in (
        h.SERVICE_SECRET,
        h.SERVICE_KEY_ID,
        alpha_bot_execution.get_composer_headers()["authorization"],
    ):
        assert secret not in caplog.text
    assert "LEAKMARK" not in json.dumps(outcome.result.entries, default=str)


@pytest.mark.parametrize("status", [401, 404, 500])
def test_no_log_line_carries_a_read_reply_body_when_a_series_and_a_definition_fail(
    router, caplog, status
):
    caplog.set_level(logging.DEBUG)
    router.set_series(h.MID_ID, h.marked_error_reply(status, LEAK))
    router.definition_by_symphony[h.BACKTEST_ID] = h.marked_error_reply(status, LEAK)

    completed_run()

    assert "LEAKMARK" not in caplog.text
    assert h.SERVICE_SECRET not in caplog.text
    assert h.SERVICE_KEY_ID not in caplog.text
