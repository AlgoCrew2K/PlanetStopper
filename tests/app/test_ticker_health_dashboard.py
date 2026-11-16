"""The dashboard page `/` shows the dead-ticker check: warning lines, notices, clearing, escaping.

Every stored result in this file is written by the real runner
(``advisors.ticker_health_runner.run_ticker_health_check``) over the ticker check HTTP router
(``tests/advisors/_ticker_health_http_router.py``: captured Composer roster and score records,
captured Alpaca asset records), never a hand-written row. The page is then read through the Flask
test client, so a break anywhere between the stored row and the markup fails a test here.

Contract pinned here (the implementer reads this file and the handoff, not the plan)
------------------------------------------------------------------------------------
Reads. ``app._ticker_health_page_view()`` returns the dict ``{"warning": ..., "notices": [...]}``
of ``advisors.ticker_health.page_view(latest, latest_completed, now_utc)`` where ``latest`` and
``latest_completed`` come from ``advisors.ticker_health_runner.read_latest_runs()`` and ``now_utc``
is ``datetime.now(UTC)`` with ``datetime`` read as the module attribute of ``app`` (the tests freeze
``app.datetime``, the routes' own clock). The view is passed to the page template as the keyword
``ticker_health``. A failure to read returns ``{"warning": None, "notices": ["The ticker check
result could not be read."]}``. The page makes no Composer, Alpaca or Discord request.

Markup (both elements render only when they have content; all text through Jinja autoescape):

- ``<div data-testid="ticker-health-warning" class="ticker-health-warning">`` holding one ``<p>``
  that reads the lead sentence, then ``Checked {YYYY-MM-DD HH:MM ET}.``, then a ``<ul>`` with one
  ``<li>`` per warning line. A line from an earlier check ends with
  ``<span class="ticker-health-earlier">(from the check at {stamp})</span>`` and no other line has
  that span.
- ``<div data-testid="ticker-health-notice" class="ticker-health-notice">`` holding one ``<p>``
  per notice, in order.
- Both sit after the market-state banner and before the hero section. Neither holds a button, an
  input, a script, a section header or an inline ``style`` attribute.
- Style rules in the page's ``<style>`` block for ``.ticker-health-warning``,
  ``.ticker-health-notice`` and ``.ticker-health-earlier``: design tokens only, ``overflow-wrap:
  anywhere`` on the warning and the notice, ``text-align: left`` on both, ``font-size: 13px`` and
  ``font-weight: 500`` on the warning (the fleet banner's values).

Rulings pinned (PM): a run that did not complete, with no completed check ever, shows exactly one
notice and never "No ticker check has completed yet." (that text is for no run at all); a failed
run's notice reads "Ticker check failed on {stamp}." and ends " No completed check yet." when no
completed check exists, or " Last completed check {stamp}." when one does (this needs a small change
to ``advisors.ticker_health._run_notices``); after a run that did not complete the earlier completed
run's warnings show with their "(from the check at ...)" suffix and none of that earlier run's
not-checked notices show.

Fixture provenance: the roster, score tree and Alpaca asset records are the captured ones behind the
router (README and PROVENANCE.md beside them). Derived forms (a changed name, holdings, tree or
reply status) are built in the tests by editing a captured record and are labelled where used. The
expected stamps are computed here from the stored ``ended_at`` text with ``zoneinfo``, and the
expected counts from the independent tree walk ``t.oracle_tree_uses``.
"""

from __future__ import annotations

import inspect
import json
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import markupsafe
import pytest
import requests

import app as app_module
from tests.advisors import _composer_http_router as h
from tests.advisors import _ticker_health_http_router as t
from tests.advisors.test_ticker_health_runner import (
    DEAD_SHNY,
    HELD,
    HOLDING_UNKNOWN,
    LEAD,
    LEAK,
    NOT_HELD,
    NOT_LISTED,
    NOT_TRADABLE,
    X,
    Y,
    alpaca_status,
    name_of,
    night,
    record_of,
    roster,
    run_check,
    shny_counts,
)
from tests.app._refresh_timing_state_world import frozen_datetime_class
from tests.app.test_dashboard_auth import auth_client, auth_client_authed  # noqa: F401
from tests.dashboard._first_paint_dom import parse_markup

REPO = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")

# The page is read ten hours after the run that started the night, which is inside the 36 hour
# window the stale notice measures and in a closed-market hour of an ordinary trading day.
PAGE_DELAY = timedelta(hours=10)

# Monday 17:00 EST: closed, and 35 to 37 hours later is still a closed hour (04:00 to 06:00 ET).
CLOSED_AFTERNOON = datetime(2026, 11, 16, 22, 0, tzinfo=UTC)

NEVER_COMPLETED = "No ticker check has completed yet."
LINE_REASONS = {"SHNY": DEAD_SHNY, "ZZZZQQ": NOT_LISTED, "AKAN": NOT_TRADABLE}
CREDENTIAL_KEYS = (
    "COMPOSER_KEY_ID",
    "COMPOSER_SECRET",
    "ACCOUNT_ROTH",
    "ACCOUNT_INDIVIDUAL",
    "ACCOUNT_TRAD",
    "ALPACA_KEY",
    "ALPACA_SECRET",
    "DISCORD_WEBHOOK_URL",
)


# ---------------------------------------------------------------------------
# Reading the page
# ---------------------------------------------------------------------------


def walk(node):
    yield node
    for child in node.get("children", ()):
        yield from walk(child)


def elements(node, tag=None):
    if node is None:
        return []
    return [n for n in walk(node) if "tag" in n and (tag is None or n["tag"] == tag)]


def has_class(node, name) -> bool:
    return name in node["attrs"].get("class", "").split()


def text_of(node, *, skip=None) -> str:
    parts: list[str] = []

    def visit(n):
        if "text" in n:
            parts.append(n["text"])
        elif skip is None or not skip(n):
            for child in n["children"]:
                visit(child)

    visit(node)
    return " ".join("".join(parts).split())


def is_earlier_span(node) -> bool:
    return node.get("tag") == "span" and has_class(node, "ticker-health-earlier")


class Page:
    def __init__(self, markup: str) -> None:
        self.html = markup
        self.root = parse_markup(markup)

    def testid(self, value):
        found = [n for n in walk(self.root) if n.get("attrs", {}).get("data-testid") == value]
        return found[0] if found else None

    @property
    def warning(self):
        return self.testid("ticker-health-warning")

    @property
    def notice(self):
        return self.testid("ticker-health-notice")

    def lead(self) -> str:
        return text_of(elements(self.warning, "p")[0])

    def lines(self) -> list[tuple[str, str | None]]:
        """Each warning line as (its own text, the text of its earlier-check suffix or None)."""
        out = []
        for item in elements(self.warning, "li"):
            spans = [n for n in walk(item) if is_earlier_span(n)]
            assert len(spans) <= 1, "a line carries at most one earlier-check suffix"
            out.append((text_of(item, skip=is_earlier_span), text_of(spans[0]) if spans else None))
        return out

    def line_texts(self) -> list[str]:
        return [main for main, _ in self.lines()]

    def notices(self) -> list[str]:
        return [text_of(p) for p in elements(self.notice, "p")]

    def notice_text(self) -> str:
        return text_of(self.notice) if self.notice is not None else ""

    def feature_text(self) -> str:
        return " ".join(text_of(n) for n in (self.warning, self.notice) if n is not None)


def stamp(iso_text: str) -> str:
    """The ``YYYY-MM-DD HH:MM ET`` form of a stored instant, derived with zoneinfo."""
    return datetime.fromisoformat(iso_text).astimezone(ET).strftime("%Y-%m-%d %H:%M ET")


def ended_at(outcome) -> str:
    return record_of(outcome)["ended_at"]


def count_text(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def dead_line(name, ticker, reason, counts, held=NOT_HELD) -> str:
    assets, conditions = counts
    return (
        f"{name}: {ticker} is {reason}. Used by {count_text(assets, 'asset node')} "
        f"and {count_text(conditions, 'condition')}. {held}"
    )


def shny_line(symphony_id, held=NOT_HELD, name=None) -> str:
    return dead_line(
        name if name is not None else name_of(symphony_id), "SHNY", DEAD_SHNY, shny_counts(), held
    )


def earlier(stamp_text: str) -> str:
    return f"(from the check at {stamp_text})"


def holdings_of(*tickers) -> list[dict]:
    return [{"ticker": ticker, "allocation": 0.1} for ticker in tickers]


# ---------------------------------------------------------------------------
# The world: temp database, router, frozen page clock
# ---------------------------------------------------------------------------


class World:
    def __init__(self, router, client, monkeypatch) -> None:
        self.router, self.client, self.monkeypatch = router, client, monkeypatch
        self.last_start = t.NIGHT

    def run(self, start=t.NIGHT, *, status="completed", **overrides):
        outcome = run_check(self.router, start=start, **overrides)
        assert outcome.status == status, outcome
        self.last_start = start
        return outcome

    def page(self, at=None) -> Page:
        instant = at if at is not None else self.last_start + PAGE_DELAY
        self.monkeypatch.setattr(app_module, "datetime", frozen_datetime_class(instant))
        response = self.client.get("/")
        assert response.status_code == 200, f"GET / answered {response.status_code}"
        return Page(response.get_data(as_text=True))


@pytest.fixture
def world(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(env_file))
    for key in CREDENTIAL_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ATLAS_CACHE_DB_PATH", str(tmp_path / "atlas_cache.db"))
    router = t.TickerHealthRouter().install(monkeypatch)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield World(router, client, monkeypatch)


def assert_view_is_empty() -> None:
    """The view handed to the template is the empty one; the clock is the one the page just
    read (``world.page`` froze ``app.datetime`` first)."""
    view = getattr(app_module, "_ticker_health_page_view", None)
    assert view is not None, "app._ticker_health_page_view does not exist"
    assert view() == {"warning": None, "notices": []}


def only_the_warning(page: Page, expected_lines: list[str]) -> None:
    assert page.warning is not None, "the dead-ticker warning is not on the page"
    assert page.line_texts() == expected_lines


# ---------------------------------------------------------------------------
# The warning
# ---------------------------------------------------------------------------


class TestWarning:
    def test_the_warning_shows_the_lead_the_check_time_once_and_the_line(self, world):
        roster(world.router, X)
        outcome = world.run()
        page = world.page()
        assert page.warning is not None, "the dead-ticker warning is not on the page"
        assert page.lead() == f"{LEAD} Checked {stamp(ended_at(outcome))}."
        assert page.line_texts() == [shny_line(X)]
        assert page.lines()[0][1] is None
        assert text_of(page.warning).count(stamp(ended_at(outcome))) == 1
        assert page.notice is None

    def test_the_warning_sits_after_the_market_banner_and_before_the_hero_section(self, world):
        roster(world.router, X)
        world.run()
        page = world.page()
        order = [n.get("attrs", {}) for n in walk(page.root) if "tag" in n]
        positions = {
            "market": next(i for i, a in enumerate(order) if a.get("id") == "market-state-banner"),
            "warning": next(
                (i for i, a in enumerate(order) if a.get("data-testid") == "ticker-health-warning"),
                None,
            ),
            "hero": next(i for i, a in enumerate(order) if a.get("data-testid") == "hero-section"),
        }
        assert positions["warning"] is not None, "the dead-ticker warning is not on the page"
        assert positions["market"] < positions["warning"] < positions["hero"]

    def test_a_ticker_the_symphony_holds_now_reads_held(self, world):
        roster(world.router, X, **{X: {"holdings": holdings_of("BIL", "SHNY")}})
        world.run()
        only_the_warning(world.page(), [shny_line(X, HELD)])

    @pytest.mark.parametrize(
        "holdings",
        [
            pytest.param(t.DROP, id="field_absent"),
            pytest.param("BIL", id="not_a_list"),
            pytest.param(None, id="null"),
            pytest.param(["BIL"], id="entry_not_a_dict"),
        ],
    )
    def test_holdings_that_cannot_be_read_read_unknown_never_not_held(self, world, holdings):
        roster(world.router, X, **{X: {"holdings": holdings}})
        world.run()
        page = world.page()
        only_the_warning(page, [shny_line(X, HOLDING_UNKNOWN)])
        assert NOT_HELD not in page.feature_text()

    def test_each_count_follows_its_plural(self, world):
        asset_only = t.tree_with(assets=["SHNY"])
        condition_only = t.tree_with(conditions=[("SHNY", "SPY")])
        world.router.set_tree(X, asset_only)
        world.router.set_tree(Y, condition_only)
        roster(world.router, X, Y)
        world.run()
        page = world.page()
        assert page.warning is not None, "the dead-ticker warning is not on the page"
        by_name = dict(
            zip(
                (name_of(X), name_of(Y)),
                (
                    t.oracle_tree_uses(asset_only)["SHNY"],
                    t.oracle_tree_uses(condition_only)["SHNY"],
                ),
                strict=True,
            )
        )
        assert sorted(page.line_texts()) == sorted(
            dead_line(name, "SHNY", DEAD_SHNY, counts) for name, counts in by_name.items()
        )
        joined = " ".join(page.line_texts())
        assert "Used by 1 asset node and 0 conditions." in joined
        assert "Used by 0 asset nodes and 1 condition." in joined

    def test_lines_run_in_display_name_then_ticker_order(self, world):
        trees = {X: t.tree_with(assets=["ZZZZQQ"]), Y: t.tree_with(assets=["SHNY", "AKAN"])}
        for symphony_id, tree in trees.items():
            world.router.set_tree(symphony_id, tree)
        world.router.set_alpaca("ZZZZQQ", t.not_found_reply())
        world.router.set_alpaca("AKAN", t.active_not_tradable_reply())
        roster(world.router, X, Y)
        world.run()
        pairs = [(X, "ZZZZQQ"), (Y, "SHNY"), (Y, "AKAN")]
        ordered = sorted(pairs, key=lambda p: (name_of(p[0]).casefold(), p[0], p[1]))
        expected = [
            dead_line(
                name_of(sid),
                ticker,
                LINE_REASONS[ticker],
                t.oracle_tree_uses(trees[sid])[ticker],
            )
            for sid, ticker in ordered
        ]
        only_the_warning(world.page(), expected)

    @pytest.mark.parametrize("blank", ["", "   ", "\t "])
    def test_a_blank_roster_name_shows_the_composer_id(self, world, blank):
        roster(world.router, X, **{X: {"name": blank}})
        world.run()
        only_the_warning(world.page(), [shny_line(X, name=X)])

    def test_a_symphony_listed_by_two_accounts_is_one_line_held_when_either_holds_it(self, world):
        world.router.set_roster(
            t.roster_entry(X, name="Name in the first account", holdings=holdings_of("BIL")),
            account=h.ACCOUNT_ID,
        )
        world.router.set_roster(
            t.roster_entry(X, name="Name in the second account", holdings=holdings_of("SHNY")),
            account=h.SECOND_ACCOUNT_ID,
        )
        world.run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])
        only_the_warning(world.page(), [shny_line(X, HELD, name="Name in the first account")])

    def test_a_check_that_found_nothing_shows_neither_a_warning_nor_a_notice(self, world):
        world.router.set_alpaca("SHNY", t.healthy_reply("SHNY"))
        roster(world.router, X)
        outcome = world.run()
        stored = record_of(outcome)
        assert stored["warnings"] == [] and stored["not_checked"] == {
            "accounts": [],
            "symphonies": [],
            "tickers": [],
        }
        page = world.page()
        assert page.warning is None and page.notice is None
        assert LEAD not in page.html and "Ticker check" not in page.html
        assert "No ticker check has completed" not in page.html
        assert_view_is_empty()

    def test_a_roster_that_lists_no_symphony_shows_nothing_either(self, world):
        world.router.set_roster()
        world.run()
        page = world.page()
        assert page.warning is None and page.notice is None
        assert_view_is_empty()

    def test_the_page_reads_stored_results_and_makes_no_request(self, world):
        roster(world.router, X)
        world.run()
        before = (len(world.router.requests), len(world.router.events), len(world.router.sleeps))
        for _ in range(2):
            page = world.page()
            assert page.warning is not None, "the dead-ticker warning is not on the page"
        after = (len(world.router.requests), len(world.router.events), len(world.router.sleeps))
        assert after == before
        assert world.router.violations == []

    def test_a_run_in_progress_shows_nothing_of_itself(self, world):
        """The earlier completed run's result stays on the page while a newer run is held in the
        middle of its Composer reads; the held run would clear the warning, so it shows if the page
        read anything of it."""
        roster(world.router, X)
        world.run(night(0))
        world.router.set_alpaca("SHNY", t.healthy_reply("SHNY"))
        reached, release = threading.Event(), threading.Event()
        real_dispatch = world.router.dispatch

        def held_dispatch(method, url, kwargs):
            if world.router._classify(method, url, kwargs)[0] == "score":
                reached.set()
                release.wait(timeout=30)
            return real_dispatch(method, url, kwargs)

        world.monkeypatch.setattr(world.router, "dispatch", held_dispatch)
        box: dict = {}

        def second_run():
            try:
                box["outcome"] = run_check(world.router, start=night(1))
            except BaseException as exc:  # reported after the join
                box["error"] = exc

        worker = threading.Thread(target=second_run, name="held-ticker-check", daemon=True)
        worker.start()
        try:
            assert reached.wait(timeout=30), "the second run never reached its first score read"
            page = world.page(at=night(0) + PAGE_DELAY)
            only_the_warning(page, [shny_line(X)])
            assert page.notice is None
            assert page.lines()[0][1] is None
        finally:
            release.set()
            worker.join(timeout=60)
        assert "error" not in box, box.get("error")
        assert box["outcome"].status == "completed"
        assert world.page(at=night(1) + PAGE_DELAY).warning is None


# ---------------------------------------------------------------------------
# Clearing
# ---------------------------------------------------------------------------


class TestClearing:
    def test_a_ticker_that_trades_again_leaves_the_page(self, world):
        roster(world.router, X)
        world.run(night(0))
        only_the_warning(world.page(), [shny_line(X)])
        world.router.set_alpaca("SHNY", t.healthy_reply("SHNY"))
        world.run(night(1))
        page = world.page()
        assert page.warning is None and page.notice is None

    def test_a_ticker_removed_from_the_tree_leaves_the_page(self, world):
        roster(world.router, X)
        world.run(night(0))
        assert world.page().warning is not None, "the dead-ticker warning is not on the page"
        world.router.set_tree(X, t.tree_with(assets=["SPY", "QQQ"]))  # derived: no SHNY node left
        world.run(night(1))
        page = world.page()
        assert page.warning is None and page.notice is None

    def test_a_symphony_gone_from_every_roster_leaves_the_page(self, world):
        roster(world.router, X)
        world.run(night(0))
        assert world.page().warning is not None, "the dead-ticker warning is not on the page"
        world.router.set_tree(Y, t.tree_with(assets=["SPY"]))
        roster(world.router, Y)  # derived: the second roster no longer lists X
        world.run(night(1))
        page = world.page()
        assert page.warning is None and page.notice is None

    def test_removing_the_ticker_from_one_symphony_leaves_the_other_symphonys_line(self, world):
        roster(world.router, X, Y)
        world.run(night(0))
        assert len(world.page().lines()) == 2
        world.router.set_tree(X, t.tree_with(assets=["SPY"]))
        world.run(night(1))
        page = world.page()
        only_the_warning(page, [shny_line(Y)])
        assert page.lines()[0][1] is None

    def test_a_symphony_the_run_could_not_read_keeps_its_line_with_the_earlier_time(self, world):
        roster(world.router, X)
        first = world.run(night(0))
        world.router.set_tree(X, h.error_reply(503))
        world.run(night(1))
        page = world.page()
        only_the_warning(page, [shny_line(X)])
        assert page.lines()[0][1] == earlier(stamp(ended_at(first)))
        assert page.notices() == [f"Not checked: {name_of(X)} (Composer unavailable)."]

    def test_an_answer_that_failed_keeps_the_line_with_the_earlier_time(self, world):
        roster(world.router, X)
        first = world.run(night(0))
        world.router.set_alpaca("SHNY", alpaca_status(503))
        world.run(night(1))
        page = world.page()
        only_the_warning(page, [shny_line(X)])
        assert page.lines()[0][1] == earlier(stamp(ended_at(first)))
        assert page.notices() == [
            f"1 ticker in {name_of(X)} could not be checked (Alpaca unavailable)."
        ]
        assert len(world.router.discord_posts()) == 1, "nothing is posted for a carried line"


# ---------------------------------------------------------------------------
# Notices
# ---------------------------------------------------------------------------

REFUSED, REJECTED = "Composer refused the request", "Composer rejected the request"


def _unusable_id_setup(router):
    router.set_roster(t.roster_entry(X), t.roster_entry(Y, id="a.b/c"))
    return {}


def _score_failure(reply):
    def setup(router):
        roster(router, X, Y)
        router.set_tree(Y, reply())
        return {}

    return setup


def _roster_failure(reply):
    def setup(router):
        router.set_roster(t.roster_entry(Y), account=h.ACCOUNT_ID)
        router.statistics_by_account[h.SECOND_ACCOUNT_ID] = reply()
        return {"account_ids": [h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID]}

    return setup


def _unusable_account_setup(router):
    router.set_roster(t.roster_entry(Y), account=h.ACCOUNT_ID)
    return {
        "account_ids": ["../x", h.ACCOUNT_ID],
        "account_labels": {"../x": "Spare", h.ACCOUNT_ID: "Roth IRA"},
    }


def _rate_limited_second_roster(router):
    router.set_roster(t.roster_entry(X), t.roster_entry(Y), account=h.ACCOUNT_ID)
    router.statistics_by_account[h.SECOND_ACCOUNT_ID] = h.error_reply(429)
    return {"account_ids": [h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID]}


def _crypto_setup(router):
    roster(router, X)
    router.set_tree(X, t.tree_with(assets=["SHNY", "QQQ"], asset_class="CRYPTO"))
    return {}


def _bad_form_setup(bad):
    def setup(router):
        roster(router, X)
        router.set_tree(X, t.tree_with(assets=[bad, "SPY"], conditions=[("SPY", "SPY")]))
        return {}

    return setup


def _unanswered_setup(reply, count):
    def setup(router):
        roster(router, X)
        unanswered = ["IWM", "QQQ"][:count]
        router.set_tree(X, t.tree_with(assets=["SHNY", *unanswered]))
        for symbol in unanswered:
            router.set_alpaca(symbol, reply())
        return {}

    return setup


def _alpaca_unreachable_setup(router):
    roster(router, X)
    router.alpaca_by_symbol.clear()
    router.set_all_alpaca(h.raising(requests.ConnectionError("down")))
    return {}


def _every_roster_down_setup(router):
    router.statistics_default = h.error_reply(503)
    return {}


def _name(symphony_id: str) -> str:
    return name_of(symphony_id)


def _not_checked(symphony_id: str, reason: str) -> str:
    return f"Not checked: {_name(symphony_id)} ({reason})."


def _account_not_checked(label: str, reason: str) -> str:
    return f"Not checked: symphonies of the {label} account ({reason})."


class Case:
    def __init__(self, setup, notices, lines=(), status="completed", check=None):
        self.setup, self.notices, self.lines = setup, notices, list(lines)
        self.status, self.check = status, check


SHNY_X = shny_line(X)
SHNY_Y = shny_line(Y)
# The derived tree the unanswered-ticker cases give X: SHNY on asset nodes only.
SHNY_X_ASSET_ONLY = dead_line(
    name_of(X), "SHNY", DEAD_SHNY, t.oracle_tree_uses(t.tree_with(assets=["SHNY"]))["SHNY"]
)
UNAVAILABLE_NONE = "Ticker check unavailable: {reason}. No completed check yet."


def _no_request_for_the_unusable_id(router):
    assert "a.b/c" not in router.score_ids()
    assert router.violations == []


def _no_request_for_the_unusable_account(router):
    assert "../x" not in [r.match["account"] for r in router.of_kind("statistics")]


def _no_asset_request(router):
    assert router.asset_requests() == []


def _no_request_for_the_bad_form(router):
    assert not [s for s in router.asset_symbols() if s in ("BRK/B", "A B", "..")]


def _no_request_at_all(router):
    assert router.requests == []


NOTICE_CASES = {
    "score_5xx": Case(
        _score_failure(lambda: h.error_reply(503)),
        [_not_checked(Y, "Composer unavailable")],
        [SHNY_X],
    ),
    "score_timeout": Case(
        _score_failure(lambda: h.raising(requests.Timeout("slow"))),
        [_not_checked(Y, "Composer unavailable")],
        [SHNY_X],
    ),
    "score_404": Case(
        _score_failure(lambda: h.error_reply(404)),
        [_not_checked(Y, "definition not found")],
        [SHNY_X],
    ),
    "score_401": Case(
        _score_failure(lambda: h.error_reply(401)), [_not_checked(Y, REFUSED)], [SHNY_X]
    ),
    "score_403": Case(
        _score_failure(lambda: h.error_reply(403)), [_not_checked(Y, REFUSED)], [SHNY_X]
    ),
    "score_non_json": Case(
        _score_failure(h.non_json_200), [_not_checked(Y, "definition unreadable")], [SHNY_X]
    ),
    "score_json_list": Case(
        _score_failure(lambda: h.Reply(body=["not", "a", "tree"])),
        [_not_checked(Y, "definition unreadable")],
        [SHNY_X],
    ),
    "score_redirect": Case(
        _score_failure(lambda: h.redirect_reply(302)), [_not_checked(Y, REJECTED)], [SHNY_X]
    ),
    "score_400": Case(
        _score_failure(lambda: h.error_reply(400)), [_not_checked(Y, REJECTED)], [SHNY_X]
    ),
    "score_422": Case(
        _score_failure(lambda: h.error_reply(422)), [_not_checked(Y, REJECTED)], [SHNY_X]
    ),
    "symphony_id_not_usable": Case(
        _unusable_id_setup,
        [_not_checked(Y, "symphony id not usable")],
        [SHNY_X],
        check=_no_request_for_the_unusable_id,
    ),
    "roster_5xx": Case(
        _roster_failure(lambda: h.error_reply(503)),
        [_account_not_checked("Individual", "Composer unavailable")],
        [SHNY_Y],
    ),
    "roster_timeout": Case(
        _roster_failure(lambda: h.raising(requests.Timeout("slow"))),
        [_account_not_checked("Individual", "Composer unavailable")],
        [SHNY_Y],
    ),
    "roster_401": Case(
        _roster_failure(lambda: h.error_reply(401)),
        [_account_not_checked("Individual", REFUSED)],
        [SHNY_Y],
    ),
    "roster_403": Case(
        _roster_failure(lambda: h.error_reply(403)),
        [_account_not_checked("Individual", REFUSED)],
        [SHNY_Y],
    ),
    "roster_404": Case(
        _roster_failure(lambda: h.error_reply(404)),
        [_account_not_checked("Individual", "account not found")],
        [SHNY_Y],
    ),
    "roster_redirect": Case(
        _roster_failure(lambda: h.redirect_reply(302)),
        [_account_not_checked("Individual", REJECTED)],
        [SHNY_Y],
    ),
    "roster_400": Case(
        _roster_failure(lambda: h.error_reply(400)),
        [_account_not_checked("Individual", REJECTED)],
        [SHNY_Y],
    ),
    "roster_422": Case(
        _roster_failure(lambda: h.error_reply(422)),
        [_account_not_checked("Individual", REJECTED)],
        [SHNY_Y],
    ),
    "roster_non_json": Case(
        _roster_failure(h.non_json_200),
        [_account_not_checked("Individual", "Composer response unreadable")],
        [SHNY_Y],
    ),
    "roster_json_list": Case(
        _roster_failure(lambda: h.Reply(body=["not", "a", "roster"])),
        [_account_not_checked("Individual", "Composer response unreadable")],
        [SHNY_Y],
    ),
    "account_id_not_usable": Case(
        _unusable_account_setup,
        [_account_not_checked("Spare", "Composer response unreadable")],
        [SHNY_Y],
        check=_no_request_for_the_unusable_account,
    ),
    "rate_limit_after_a_roster": Case(
        _rate_limited_second_roster,
        [
            _account_not_checked("Individual", "Composer rate limit"),
            _not_checked(X, "Composer rate limit"),
            _not_checked(Y, "Composer rate limit"),
        ],
        [],
    ),
    "asset_class_not_covered": Case(
        _crypto_setup,
        [_not_checked(X, "asset class not covered by the check")],
        [],
        check=_no_asset_request,
    ),
    "ticker_form_slash": Case(
        _bad_form_setup("BRK/B"),
        [f"Not checked: BRK/B in {_name(X)} (ticker form not checked)."],
        [],
        check=_no_request_for_the_bad_form,
    ),
    "ticker_form_space": Case(
        _bad_form_setup("A B"),
        [f"Not checked: A B in {_name(X)} (ticker form not checked)."],
        [],
        check=_no_request_for_the_bad_form,
    ),
    "ticker_form_dots": Case(
        _bad_form_setup(".."),
        [f"Not checked: .. in {_name(X)} (ticker form not checked)."],
        [],
        check=_no_request_for_the_bad_form,
    ),
    "one_ticker_rate_limited": Case(
        _unanswered_setup(lambda: h.error_reply(429), 1),
        [f"1 ticker in {_name(X)} could not be checked (Alpaca rate limit)."],
        [SHNY_X_ASSET_ONLY],
    ),
    "two_tickers_rate_limited": Case(
        _unanswered_setup(lambda: h.error_reply(429), 2),
        [f"2 tickers in {_name(X)} could not be checked (Alpaca rate limit)."],
        [SHNY_X_ASSET_ONLY],
    ),
    "two_tickers_unavailable": Case(
        _unanswered_setup(lambda: alpaca_status(503), 2),
        [f"2 tickers in {_name(X)} could not be checked (Alpaca unavailable)."],
        [SHNY_X_ASSET_ONLY],
    ),
    "alpaca_unreachable": Case(
        _alpaca_unreachable_setup,
        [UNAVAILABLE_NONE.format(reason="Alpaca unreachable")],
        status="unavailable",
    ),
    "every_roster_down": Case(
        _every_roster_down_setup,
        [UNAVAILABLE_NONE.format(reason="Composer unavailable")],
        status="unavailable",
    ),
    "no_account_configured": Case(
        lambda router: {"account_ids": []},
        [UNAVAILABLE_NONE.format(reason="no Composer account configured")],
        status="unavailable",
        check=_no_request_at_all,
    ),
}


class TestNotices:
    @pytest.mark.parametrize("case_id", list(NOTICE_CASES))
    def test_what_the_check_could_not_judge_is_named_with_its_reason(self, world, case_id):
        case = NOTICE_CASES[case_id]
        overrides = case.setup(world.router)
        world.run(status=case.status, **overrides)
        page = world.page()
        assert page.notice is not None, "the check notice is not on the page"
        assert page.notices() == case.notices
        if case.lines:
            only_the_warning(page, case.lines)
        else:
            assert page.warning is None
        if case.check is not None:
            case.check(world.router)

    def test_a_run_that_never_completed_shows_that_one_notice_never_two(self, world):
        roster(world.router, X)
        world.router.alpaca_by_symbol.clear()
        world.router.set_all_alpaca(h.raising(requests.ConnectionError("down")))
        world.run(status="unavailable")
        page = world.page()
        assert len(page.notices()) == 1
        assert NEVER_COMPLETED not in page.html

    def test_no_run_at_all_says_no_check_has_completed(self, world):
        page = world.page(at=t.NIGHT + PAGE_DELAY)
        assert page.notices() == [NEVER_COMPLETED]
        assert page.warning is None

    def test_the_error_body_of_a_reply_is_never_on_the_page(self, world):
        roster(world.router, X, Y)
        world.router.set_tree(X, h.marked_error_reply(503, LEAK))
        world.router.statistics_by_account[h.SECOND_ACCOUNT_ID] = h.marked_error_reply(401, LEAK)
        world.router.set_alpaca("SPY", _marked_alpaca(alpaca_status(503)))
        world.run(account_ids=[h.ACCOUNT_ID, h.SECOND_ACCOUNT_ID])
        page = world.page()
        assert page.notice is not None, "the check notice is not on the page"
        assert LEAK not in page.html

    def test_an_alpaca_error_body_is_never_on_the_page(self, world):
        roster(world.router, X)
        world.router.set_tree(X, t.tree_with(assets=["SHNY", "SPY"]))
        world.router.set_alpaca("SPY", _marked_alpaca(alpaca_status(429)))
        world.run()
        page = world.page()
        assert page.notices() == [
            f"1 ticker in {name_of(X)} could not be checked (Alpaca rate limit)."
        ]
        assert LEAK not in page.html

    def test_a_refused_alpaca_key_shows_only_the_fixed_reason(self, world):
        roster(world.router, X)
        marked = _marked_alpaca(t.unauthorized_reply())
        world.router.set_all_alpaca(marked)
        world.router.alpaca_by_symbol.clear()
        world.run(status="unavailable")
        page = world.page()
        assert page.notices() == [UNAVAILABLE_NONE.format(reason="Alpaca refused the credentials")]
        assert LEAK not in page.html


def _marked_alpaca(reply):
    body = reply.body if isinstance(reply.body, dict) else {}
    reply.body = {**body, "message": f"{body.get('message', '')} {LEAK}"}
    return reply


# ---------------------------------------------------------------------------
# Earlier results, failed runs and staleness
# ---------------------------------------------------------------------------


class TestEarlierResultsAndStaleness:
    def test_a_symphony_the_latest_run_did_not_judge_keeps_its_line_with_the_earlier_time(
        self, world
    ):
        roster(world.router, X, Y)
        first = world.run(night(0))
        world.router.set_tree(Y, h.error_reply(503))
        world.run(night(1))
        page = world.page()
        assert sorted(page.lines()) == sorted(
            [
                (shny_line(X), None),
                (shny_line(Y), earlier(stamp(ended_at(first)))),
            ]
        )
        assert page.notices() == [_not_checked(Y, "Composer unavailable")]
        assert len(world.router.discord_posts()) == 1, "nothing is posted for a carried line"

    def test_a_run_that_could_not_judge_shows_the_unavailable_notice_and_the_earlier_lines(
        self, world
    ):
        roster(world.router, X)
        first = world.run(night(0))
        world.router.alpaca_by_symbol.clear()
        world.router.set_all_alpaca(h.raising(requests.ConnectionError("down")))
        world.run(night(1), status="unavailable")
        page = world.page()
        assert page.notices() == [
            f"Ticker check unavailable: Alpaca unreachable. "
            f"Last completed check {stamp(ended_at(first))}."
        ]
        only_the_warning(page, [shny_line(X)])
        assert page.lines()[0][1] == earlier(stamp(ended_at(first)))
        assert page.lead() == f"{LEAD} Checked {stamp(ended_at(first))}."

    def test_the_earlier_runs_not_checked_notices_are_not_repeated_under_a_newer_run(self, world):
        roster(world.router, X, Y)
        world.router.set_tree(Y, h.error_reply(503))
        world.run(night(0))
        assert page_has_not_checked(world.page())
        world.router.alpaca_by_symbol.clear()
        world.router.set_all_alpaca(h.raising(requests.ConnectionError("down")))
        world.run(night(1), status="unavailable")
        page = world.page()
        assert len(page.notices()) == 1
        assert "Not checked" not in page.notice_text()
        assert page.warning is not None, "the earlier dead-ticker lines are not on the page"

    def test_a_failed_run_says_when_it_failed_and_names_the_last_completed_check(
        self, world, monkeypatch
    ):
        roster(world.router, X)
        first = world.run(night(0))
        _make_the_walker_raise(monkeypatch)
        failed = world.run(night(1), status="failed")
        page = world.page()
        assert page.notices() == [
            f"Ticker check failed on {stamp(ended_at(failed))}. "
            f"Last completed check {stamp(ended_at(first))}."
        ]
        only_the_warning(page, [shny_line(X)])
        assert page.lines()[0][1] == earlier(stamp(ended_at(first)))
        assert "RuntimeError" not in page.html and WALKER_FAULT not in page.html

    def test_a_failed_run_with_no_completed_check_says_so_and_nothing_else(
        self, world, monkeypatch
    ):
        roster(world.router, X)
        _make_the_walker_raise(monkeypatch)
        failed = world.run(night(0), status="failed")
        page = world.page()
        assert page.notices() == [
            f"Ticker check failed on {stamp(ended_at(failed))}. No completed check yet."
        ]
        assert page.warning is None
        assert NEVER_COMPLETED not in page.html
        assert "RuntimeError" not in page.html and WALKER_FAULT not in page.html

    @pytest.mark.parametrize(
        ("hours", "stale"), [(35, False), (36, False), (37, True)], ids=["35h", "36h", "37h"]
    )
    def test_a_check_older_than_36_hours_is_stale_and_its_lines_still_show(
        self, world, hours, stale
    ):
        roster(world.router, X)
        outcome = world.run(CLOSED_AFTERNOON)
        page = world.page(at=datetime.fromisoformat(ended_at(outcome)) + timedelta(hours=hours))
        only_the_warning(page, [shny_line(X)])
        if stale:
            assert page.notices() == [
                f"No ticker check has completed since {stamp(ended_at(outcome))}."
            ]
        else:
            assert page.notice is None

    @pytest.mark.parametrize(
        ("start", "page_at"),
        [
            pytest.param(
                datetime(2026, 11, 15, 1, 30, tzinfo=UTC),
                datetime(2026, 11, 15, 17, 0, tzinfo=UTC),
                id="sunday_after_a_saturday_night_run",
            ),
            pytest.param(
                datetime(2026, 11, 16, 1, 30, tzinfo=UTC),
                datetime(2026, 11, 16, 12, 0, tzinfo=UTC),
                id="monday_morning_after_a_sunday_night_run",
            ),
            pytest.param(
                datetime(2026, 11, 26, 1, 30, tzinfo=UTC),
                datetime(2026, 11, 26, 17, 0, tzinfo=UTC),
                id="thanksgiving_holiday_after_the_night_before",
            ),
        ],
    )
    def test_a_weekend_or_holiday_page_after_the_last_night_run_has_no_stale_notice(
        self, world, start, page_at
    ):
        roster(world.router, X)
        for earlier_start in (start - timedelta(days=2), start - timedelta(days=1), start):
            world.run(earlier_start)
        page = world.page(at=page_at)
        only_the_warning(page, [shny_line(X)])
        assert page.notice is None


WALKER_FAULT = "walker fault marker 5521"


def _make_the_walker_raise(monkeypatch):
    def fail(_tree):
        raise RuntimeError(WALKER_FAULT)

    monkeypatch.setattr("advisors.ticker_health.tree_ticker_uses", fail)


def page_has_not_checked(page: Page) -> bool:
    return any(text.startswith("Not checked") for text in page.notices())


# ---------------------------------------------------------------------------
# A daemon stop in the middle of a run
# ---------------------------------------------------------------------------


class TestInterruptedRun:
    def test_a_run_the_daemon_stopped_leaves_the_previous_result_and_posts_nothing(self, world):
        roster(world.router, X)
        world.run(night(0))
        posts_before = len(world.router.discord_posts())
        world.router.set_tree(X, t.tree_with(assets=["SHNY", "ZZZZQQ"]))
        world.router.set_alpaca("ZZZZQQ", h.raising(SystemExit()))
        with pytest.raises(SystemExit):
            run_check(world.router, start=night(1))
        page = world.page(at=night(0) + timedelta(hours=26))
        only_the_warning(page, [shny_line(X)])
        assert page.lines()[0][1] is None, "the stopped run wrote nothing, so the line is current"
        assert page.notice is None
        assert len(world.router.discord_posts()) == posts_before


# ---------------------------------------------------------------------------
# The rest of the page is unchanged by a stored result
# ---------------------------------------------------------------------------

BANNER_IDS = ("fleet-correlation-banner", "market-state-banner", "engine-status")


class TestOtherSurfacesUnchanged:
    def _banners(self, page: Page) -> dict:
        found = {}
        for banner_id in BANNER_IDS:
            nodes = [n for n in walk(page.root) if n.get("attrs", {}).get("id") == banner_id]
            assert nodes, f"fixture integrity: #{banner_id} is not on the page"
            found[banner_id] = nodes[0]
        return found

    def test_the_fleet_banner_the_market_banner_and_the_engine_label_do_not_move(self, world):
        alert = {
            **_fleet_alert(),
            "dismissed_at_et": None,
        }
        at = t.NIGHT + PAGE_DELAY
        with patch.object(app_module.database, "read_fleet_alert", return_value=alert):
            baseline = self._banners(world.page(at=at))
            roster(world.router, X)
            world.run()
            page = world.page(at=at)
            assert page.warning is not None, "the dead-ticker warning is not on the page"
            assert self._banners(page) == baseline

    def test_dead_tickers_never_enter_the_health_or_header_status_reads(self, world):
        moment = datetime(2026, 11, 17, 10, 43, 12, tzinfo=ET)
        reads = {
            "health on box": ("/health", {}),
            "health remote": ("/health", {"REMOTE_ADDR": "203.0.113.9"}),
            "header status": ("/api/header-status", {}),
        }

        def snapshot():
            # The patches live only while the two reads are taken, so the page read between them
            # renders over the real state.
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(app_module, "_now_et", lambda: moment)
                mp.setattr(
                    app_module.database,
                    "load_state",
                    lambda: {
                        "last_successful_cycle_at": (moment - timedelta(seconds=20)).isoformat()
                    },
                )
                mp.setattr(
                    app_module._dotenv_module,
                    "dotenv_values",
                    lambda *_a, **_k: {
                        "LIVE_EXECUTION": "False",
                        "ACCOUNT_INDIVIDUAL": "0123abcd-1",
                    },
                )
                out = {}
                for label, (route, environ) in reads.items():
                    response = world.client.get(route, environ_overrides=environ)
                    assert response.status_code == 200, f"{route} answered {response.status_code}"
                    body = response.get_json()
                    out[label] = {
                        key: body.get(key)
                        for key in (
                            "status",
                            "degraded_reasons",
                            "market_state",
                            "engine_phase",
                            "meta",
                        )
                    }
                    lowered = response.get_data(as_text=True).lower()
                    assert "shny" not in lowered and "ticker" not in lowered
                return out

        baseline = snapshot()
        roster(world.router, X)
        world.run()
        assert world.page(at=t.NIGHT + PAGE_DELAY).warning is not None, (
            "the dead-ticker warning is not on the page"
        )
        assert snapshot() == baseline


def _fleet_alert() -> dict:
    shape = json.loads(
        (REPO / "tests/fixtures/engine/fleet_correlation/alert_state_shape.json").read_text(
            encoding="utf-8"
        )
    )
    return dict(shape["example"])


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

HOSTILE_NAMES = [
    pytest.param("<img src=x onerror=alert(1)>", id="image_with_onerror"),
    pytest.param("</li></ul></div><script>alert(1)</script>", id="closing_tags_and_script"),
    pytest.param('"><svg onload=alert(1)>', id="quote_breakout_and_svg"),
]


def no_element_made_from_the_text(page: Page) -> None:
    for node in walk(page.root):
        if "tag" not in node:
            continue
        attrs = node["attrs"]
        assert not (node["tag"] in ("img", "svg") and ("onerror" in attrs or "onload" in attrs))
        if node["tag"] == "script":
            assert "alert(1)" not in text_of(node), "markup in a name became a script"


def only_text_and_the_earlier_span(node) -> bool:
    return all("text" in child or is_earlier_span(child) for child in node["children"])


class TestSecurity:
    @pytest.mark.parametrize("hostile", HOSTILE_NAMES)
    def test_markup_in_a_symphony_name_shows_as_text_in_a_warning_line(self, world, hostile):
        roster(world.router, X, **{X: {"name": hostile}})
        world.run()
        page = world.page()
        only_the_warning(page, [shny_line(X, name=hostile)])
        no_element_made_from_the_text(page)
        assert all(only_text_and_the_earlier_span(li) for li in elements(page.warning, "li"))
        assert str(markupsafe.escape(hostile)) in page.html

    @pytest.mark.parametrize("hostile", HOSTILE_NAMES)
    def test_markup_in_a_symphony_name_shows_as_text_in_a_notice(self, world, hostile):
        roster(world.router, X, Y, **{Y: {"name": hostile}})
        world.router.set_tree(Y, h.error_reply(503))
        world.run()
        page = world.page()
        assert page.notices() == [f"Not checked: {hostile} (Composer unavailable)."]
        no_element_made_from_the_text(page)
        assert all("text" in child for p in elements(page.notice, "p") for child in p["children"])
        assert str(markupsafe.escape(hostile)) in page.html

    def test_markup_in_a_tree_ticker_shows_as_text_in_a_notice(self, world):
        bad = "<b>bold</b>"
        roster(world.router, X)
        world.router.set_tree(X, t.tree_with(assets=[bad, "SPY"]))
        world.run()
        page = world.page()
        assert page.notices() == [f"Not checked: {bad} in {name_of(X)} (ticker form not checked)."]
        assert all("text" in child for p in elements(page.notice, "p") for child in p["children"])
        assert str(markupsafe.escape(bad)) in page.html

    def test_no_session_means_a_redirect_to_login_and_no_check_result(self, world, auth_client):  # noqa: F811
        roster(world.router, X)
        world.run()
        assert world.page().warning is not None, "the dead-ticker warning is not on the page"
        response = auth_client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]
        body = response.get_data(as_text=True)
        for marker in (LEAD, "SHNY", name_of(X), "ticker-health", "Ticker check"):
            assert marker not in body

    def test_a_signed_in_session_sees_the_warning(self, world, auth_client_authed):  # noqa: F811
        roster(world.router, X)
        world.run()
        response = auth_client_authed.get("/")
        assert response.status_code == 200
        page = Page(response.get_data(as_text=True))
        assert page.warning is not None, "the dead-ticker warning is not on the page"
        assert page.line_texts() == [shny_line(X)]

    def test_no_stored_text_reaches_the_page_through_a_safe_filter(self):
        source = (REPO / "templates" / "index.html").read_text(encoding="utf-8")
        start_marker = 'data-testid="ticker-health-warning"'
        assert start_marker in source, "the dead-ticker warning is not in the page template"
        region = source[source.index(start_marker) : source.index('data-testid="hero-section"')]
        for forbidden in ("| safe", "|safe", "Markup(", "autoescape"):
            assert forbidden not in region
        view = getattr(app_module, "_ticker_health_page_view", None)
        assert view is not None, "app._ticker_health_page_view does not exist"
        code = inspect.getsource(view)
        for forbidden in ("Markup", "safe(", "escape("):
            assert forbidden not in code


# ---------------------------------------------------------------------------
# Design system styling
# ---------------------------------------------------------------------------

RAW_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")


def rule_bodies(css: str, selector: str) -> str:
    pattern = re.compile(r"([^{}]*" + re.escape(selector) + r"[^{}]*)\{([^}]*)\}")
    return "\n".join(body for _, body in pattern.findall(css))


class TestDesignSystemStyling:
    @pytest.fixture
    def css_and_page(self, world):
        roster(world.router, X, Y)
        world.router.set_tree(Y, h.error_reply(503))
        world.run()
        page = world.page()
        css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", page.html, re.DOTALL))
        return css, page

    def test_the_warning_uses_the_banner_tokens_and_wraps_long_text(self, css_and_page):
        css, _ = css_and_page
        body = rule_bodies(css, ".ticker-health-warning")
        assert body, "no style rule names .ticker-health-warning"
        for token in ("--studio-warn-tint", "--studio-warn-border", "--studio-ink"):
            assert f"var({token})" in body
        assert re.search(r"overflow-wrap:\s*anywhere", body)
        assert re.search(r"text-align:\s*left", body)
        assert re.search(r"font-size:\s*13px", body) and re.search(r"font-weight:\s*500", body)

    def test_the_notice_uses_the_muted_banner_tokens_and_wraps_long_text(self, css_and_page):
        css, _ = css_and_page
        body = rule_bodies(css, ".ticker-health-notice")
        assert body, "no style rule names .ticker-health-notice"
        for token in ("--studio-chip-bg", "--studio-rule", "--studio-ink-dim"):
            assert f"var({token})" in body
        assert re.search(r"overflow-wrap:\s*anywhere", body)
        assert re.search(r"text-align:\s*left", body)

    def test_the_earlier_check_suffix_is_dimmed_with_a_token(self, css_and_page):
        css, _ = css_and_page
        body = rule_bodies(css, ".ticker-health-earlier")
        assert body, "no style rule names .ticker-health-earlier"
        assert "var(--studio-ink-dim)" in body

    def test_no_rule_of_the_feature_holds_a_raw_colour(self, css_and_page):
        css, _ = css_and_page
        for selector in (
            ".ticker-health-warning",
            ".ticker-health-notice",
            ".ticker-health-earlier",
        ):
            assert not RAW_COLOUR.search(rule_bodies(css, selector)), selector

    def test_the_elements_carry_no_inline_style_and_no_control(self, css_and_page):
        _, page = css_and_page
        for host in (page.warning, page.notice):
            assert host is not None, "a check element is not on the page"
            assert "style" not in host["attrs"]
            for node in elements(host):
                assert "style" not in node["attrs"]
                assert node["tag"] not in ("button", "input", "script", "select", "textarea")
                assert not has_class(node, "section-header")
