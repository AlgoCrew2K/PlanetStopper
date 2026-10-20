"""The tenure review runner: Composer reads, shadow fallback, dated backtest, holdings, one run.

Module under test: advisors.tenure_review_runner (and, through it, advisors.composer_portfolio_client,
symphony_logic.fetch_symphony_score and advisors.composer_backtest_client.run_backtest).

Every Composer request is served by tests/advisors/_composer_http_router.py from the captured
records under tests/fixtures/composer/tenure-review/; the router also replaces every other
``requests`` verb and ``time.sleep``, so the request ledger shows any request that is not a read
or a backtest. Entries are produced by the real review compute module (advisors.tenure_review).

Contract pinned here (the implementer reads this file, not the plan):

- ``run_tenure_review(*, account_ids, threshold_raw, now_et, run_id, trigger="on_demand")``
  returns ``RunOutcome(completed, result, failure_reason)`` and never raises. ``result`` is the
  ``ReviewResult`` of ``tenure_review.build_review`` (called as ``tenure_review.build_review(...)``
  so a patch of that module attribute holds). It writes nothing to any database.
- The roster is the union of the statistics responses of ``account_ids`` and nothing else. Any
  statistics failure (any failure class, any account) ends the run uncompleted with
  ``failure_reason == "Composer statistics unavailable"`` (an unreadable 200 reads
  "Composer statistics unreadable"); no account ends it with "no Composer account configured".
  An unexpected exception ends the run uncompleted with a reason naming the exception type only.
- Threshold and as-of day come from ``tenure_review.parse_threshold(threshold_raw)`` and
  ``tenure_review.as_of_trading_day(now_et)``, with ``now_et`` passed through as given.
- Every live symphony gets one series read against the account that listed it. A series failure
  becomes ``SymphonyInput.series_failure`` (the failure class string of the client) with the
  fallback ``shadow_returns`` read as ``analytics.get_symphony_bot_and_held_daily_returns(id,
  days=None)`` element [1] keyed by Composer id; a usable series becomes ``composer_values``.
- Only a symphony with a usable realized window and tenure at least the threshold gets a
  definition read (``symphony_logic.fetch_symphony_score``, one GET) and, when the definition is
  non-empty, one dated compact backtest (``run_backtest(..., start_date=<window first>,
  end_date=<window last>, compact_body=True, max_retries=1)``). An empty definition sets
  ``definition_available=False`` and requests no backtest.
- Holdings are read per account in client spans from the earliest realized window first date
  among symphonies in that account to the as-of day, merged across accounts by date and
  symphony id. All spans failing sets ``HoldingsInput.failure`` to the first failed span's class;
  a partial read leaves it ``None``. A holdings failure never fails the run.
- Reads from the client are followed by the client's spacing sleep; the runner adds no request
  beyond statistics, series, holdings, definition and backtest.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import requests

import advisors.tenure_review as tr
import alpha_bot_execution
import analytics
from advisors import composer_portfolio_client as cpc
from advisors import tenure_review_runner as runner
from tests.advisors import _composer_http_router as h
from tests.advisors import _tenure_review_builders as b
from tests.advisors._retirement_recommender_reference import seed_state_db

REPO = Path(__file__).resolve().parents[2]

NOW = b.et(2026, 10, 20, 3, 45)  # a Tuesday before the open: the as-of day is Monday 2026-10-19
AS_OF = date(2026, 10, 19)  # the last point of every captured series
LONG_VIX = {"VIXY", "VIXM", "UVXY", "UVIX", "VXX"}
COMPOSER_BASIS = "realized: Composer account, deposit-adjusted"
SHADOW_BASIS = "PlanetStopper shadow history from the 2026-06-22 go-live floor"

_DROP = object()


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


def split_roster(router, first_count=5):
    """Two accounts: the first lists the first ``first_count`` captured symphonies, the second
    the rest (subsets of the one captured statistics record)."""
    entries = h.statistics_body()["symphonies"]
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


def table_count(db_file, table):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def backtest_posts(router):
    return router.of_kind("backtest")


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


def test_runner_imports_its_collaborators_and_never_the_execution_engine():
    imported = imported_modules(REPO / "advisors" / "tenure_review_runner.py")

    assert "alpha_bot_execution" not in imported
    for needed in (
        "advisors.composer_portfolio_client",
        "advisors.composer_backtest_client",
        "advisors.tenure_review",
        "symphony_logic",
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
    assert len(ids) == len(set(ids)) == 11
    by_id = {e["id"]: e for e in h.statistics_body()["symphonies"]}
    for e in outcome.result.entries:
        assert e["display_name"] == by_id[e["symphony_id"]]["name"]
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
    router, monkeypatch
):
    def boom(inputs):
        raise RuntimeError("boom-secret-7731")

    monkeypatch.setattr(tr, "build_review", boom)

    outcome = run()

    assert outcome.completed is False
    assert outcome.result is None
    assert "RuntimeError" in outcome.failure_reason
    assert "boom-secret-7731" not in outcome.failure_reason


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
    assert len(router.of_kind("series")) == 11
    for r in router.of_kind("series"):
        assert r.match["account"] == owner[r.match["sid"]]
    assert len(outcome.result.entries) == 11


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


def test_every_series_date_handed_to_the_review_is_an_nyse_trading_day(router, inputs_seen):
    completed_run()

    for sym in inputs_seen[0].symphonies:
        if not sym.composer_values:
            continue
        days = sorted(sym.composer_values)
        sessions = {d.isoformat() for d in b.nyse_days(date.fromisoformat(days[0]), AS_OF)}
        assert set(days) <= sessions, sym.symphony_id


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
        "Composer has no series for this symphony",
        id="404",
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
    assert perf["window"]["days"] == 140
    assert perf["figures"]["total_return"]["value"] == pytest.approx(
        b.oracle_metrics(list(bot_by_day.values()))["total_return"], rel=1e-9
    ), "the shadow basis is the bot series (shadow_return), over all history"
    assert entry(outcome, h.MID_ID)["basis"]["kind"] == "composer"


@pytest.mark.parametrize("status", [400, 422])
def test_a_rejected_series_request_also_falls_back_to_shadow_history(
    router, inputs_seen, tmp_path, monkeypatch, status
):
    seed_shadow(tmp_path, monkeypatch, h.OLDEST_ID)
    router.set_series(h.OLDEST_ID, h.error_reply(status))

    outcome = completed_run()

    assert input_of(inputs_seen, h.OLDEST_ID).series_failure == "rejected"
    assert entry(outcome, h.OLDEST_ID)["basis"]["kind"] == "shadow"


def test_no_series_and_no_shadow_rows_leaves_every_figure_unavailable_and_none(router):
    router.set_series(
        h.OLDEST_ID, h.reply_from_record("case7a_series_nonexistent_account_404.json")
    )

    outcome = completed_run()

    e = entry(outcome, h.OLDEST_ID)
    assert e["basis"]["kind"] == "none"
    assert e["basis"]["fallback_reason"] == "Composer has no series for this symphony"
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
    pytest.param(lambda: h.error_reply(429), "Composer rate limit", (2, 2), id="429_twice"),
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


def test_holdings_reach_the_review_and_feed_the_long_vix_item(router, inputs_seen):
    recent = h.holdings_recent_body()
    outcome = completed_run()

    assert inputs_seen[0].holdings.failure is None
    expected = h.holdings_days(recent)
    assert inputs_seen[0].holdings.days == expected
    item = entry(outcome, h.OLDEST_ID)["evidence"]["long_vix"]
    held = sum(
        1 for by in expected.values() if h.OLDEST_ID in by and LONG_VIX & set(by[h.OLDEST_ID])
    )
    others = set(h.statistics_ids()) - {h.OLDEST_ID}
    co_held = sum(
        1
        for by in expected.values()
        if h.OLDEST_ID in by
        and LONG_VIX & set(by[h.OLDEST_ID])
        and any(LONG_VIX & set(by[o]) for o in others if o in by)
    )
    assert held > 0, "the capture must hold a long-VIX product for this symphony"
    assert item["status"] == "available"
    assert item["window"]["days"] == sum(1 for by in expected.values() if h.OLDEST_ID in by)
    assert item["figures"]["held_days"]["value"] == held
    assert item["figures"]["co_held_days"]["value"] == co_held


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
    assert inputs_seen[0].holdings.days == h.holdings_days(h.holdings_recent_body())


def test_a_partly_failed_holdings_read_still_feeds_the_days_it_got(router, inputs_seen):
    replies = {0: h.error_reply(500), 1: h.Reply(body=h.holdings_recent_body())}
    router.holdings_handler = lambda req, i: replies.get(i, h.Reply(body=h.holdings_emptied_body()))

    outcome = completed_run()

    assert inputs_seen[0].holdings.failure is None
    assert inputs_seen[0].holdings.days == h.holdings_days(h.holdings_recent_body())
    assert entry(outcome, h.OLDEST_ID)["evidence"]["long_vix"]["status"] == "available"


HOLDINGS_FAILURES = [
    pytest.param(lambda: h.error_reply(403), "refused", "Composer refused the request", id="403"),
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
    assert len(router.of_kind("series")) == 11
    assert len(backtest_posts(router)) <= len(router.of_kind("score"))
    assert engine_stats_calls == []


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


def test_a_run_with_failures_still_makes_no_request_outside_the_allowlist(router):
    router.set_series(h.OLDEST_ID, h.error_reply(429))
    router.set_all_holdings(h.error_reply(500))
    router.set_backtest(h.error_reply(503))

    completed_run()

    assert router.violations == []
    assert all((r.method, r.kind) in ALLOWED for r in router.requests)
