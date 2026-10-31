"""RED: the header on /history, /performance, /ai-advisor and /settings follows the engine.

The page is the HTML the real app renders; the scripts are the ones that HTML loads, run unmodified
under node (``tests/ui/_page_js_harness.py``). Every header body is produced by the real
``GET /api/header-status`` at a server instant the test names (``tests/ui/_header_status_answers``),
so the scripts read the route's own bodies, never a typed one.

CONTRACT PINNED
* ``templates/_chrome.html`` loads ``/static/request_guard.js`` before ``/static/chrome.js`` on every
  page and puts ``data-header-poll="1"`` on the nav of every page except ``/``.
* ``chrome.js`` starts a poller where that attribute is present: one read at once at
  ``DOMContentLoaded``, then every 30 s, and one read on ``visibilitychange`` to visible, on
  ``pageshow`` with ``persisted`` and on ``online``, events within 1 s of each other sending one.
* A good read runs ``noteDataReceived(timing)`` and ``updateChromeTicker(body, timing)`` and then
  ``updateModeIndicators(body.meta)``; a failed one runs ``noteRefreshFailed()``; a 401 sends the page
  to ``/login``; a response that is not the newest read issued is dropped.
* ``#ws-label`` and ``#ws-uuid`` take ``meta.account`` only while ``window.activeAccount`` is null.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest

from tests.ui._header_status_answers import (
    ACCOUNT_ENV,
    chrome_text,
    chrome_text_map,
    et,
    header_answer,
    lapsed_session_answer,
    nav_markup,
    server_error_answer,
    wall_of,
)
from tests.ui._page_js_harness import render_page, route, run_page

BASE_MARKUP = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "refresh_timing"
    / "04-c1"
    / "header_markup_at_base.json"
)
PAGES = [
    ("/history", "history"),
    ("/performance", "performance"),
    ("/ai-advisor", "ai-advisor"),
    ("/settings", "settings"),
]
STATUS = r"/api/header-status$"
FOUR_PAGES = [path for path, _ in PAGES]
LABEL = "#engine-status-label"
TITLE_HOLDER = "#engine-status"
COUNTDOWN = "#next-run-countdown"
PILL = '[data-testid="mode-pill"]'


def scenario_label(js_after_boot: str) -> str:
    return "await H.boot();\n" + js_after_boot


def run(path, answers, scenario, *, wall, **kw):
    """Run ``scenario`` on ``path`` with the status route answering ``answers`` in order."""
    return run_page(
        render_page(path),
        scenario,
        routes=[route(STATUS, *answers, method="GET", strict=kw.pop("strict", False))],
        pathname=path,
        wall=wall,
        **kw,
    )


class TestMarkup:
    @pytest.mark.parametrize("path", FOUR_PAGES)
    def test_poll_attribute_and_guard_script_on_the_four_pages(self, path):
        html = render_page(path)
        assert 'data-header-poll="1"' in html
        guard = html.find("/static/request_guard.js")
        chrome = html.find("/static/chrome.js")
        assert guard != -1, "request_guard.js is not loaded by the page"
        assert guard < chrome, "request_guard.js must load before chrome.js"

    def test_dashboard_keeps_its_own_header_driver(self):
        html = render_page("/")
        assert 'data-header-poll="1"' not in html, "/ is driven by its /api/state poll"
        assert "/static/request_guard.js" in html, "request_guard.js is not loaded by the page"
        assert html.find("/static/request_guard.js") < html.find("/static/chrome.js")

    @pytest.mark.parametrize("path", [*FOUR_PAGES, "/"])
    def test_header_ids_the_poller_writes_are_still_there(self, path):
        html = render_page(path)
        for marker in (
            'id="engine-status"',
            'id="engine-status-dot"',
            'id="engine-status-label"',
            'id="next-run-countdown"',
            'id="ws-label"',
            'id="ws-uuid"',
            'data-testid="mode-pill"',
        ):
            assert marker in html, f"{marker} is gone from {path}"


class TestHeaderMarkupIsOnlyGivenTheAttribute:
    @pytest.mark.parametrize("path", [*FOUR_PAGES, "/"])
    def test_the_nav_is_the_one_at_the_base_except_for_the_poll_attribute(self, path):
        base = json.loads(BASE_MARKUP.read_text(encoding="utf-8"))["navs"][path]
        now = nav_markup(path)
        assert now.replace(' data-header-poll="1"', "") == base, (
            f"{path}: the header markup changed beyond the one attribute"
        )
        opening = now.split(">", 1)[0]
        assert ('data-header-poll="1"' in opening) is (path != "/"), (
            f"{path}: data-header-poll belongs on the nav of every page but the dashboard"
        )


class TestReadCadence:
    @pytest.mark.parametrize("path", FOUR_PAGES)
    def test_reads_at_once_and_then_every_thirty_seconds(self, path):
        at = et(10, 43, 5)
        answers = [header_answer(at)]
        out = run(
            path,
            answers,
            scenario_label(
                """
                const first = H.to('/api/header-status').length;
                await H.advance(29000);
                const before = H.to('/api/header-status').length;
                await H.advance(2000);
                const after = H.to('/api/header-status').length;
                return {first, before, after};
                """
            ),
            wall=wall_of(at),
        )
        assert out.result == {"first": 1, "before": 1, "after": 2}

    def test_return_events_each_send_one_read_and_coalesce_within_a_second(self):
        at = et(10, 43, 5)
        out = run(
            "/history",
            [header_answer(at)],
            scenario_label(
                """
                await H.advance(2000);
                const base = H.to('/api/header-status').length;
                await H.setVisibility('hidden');
                await H.setVisibility('visible');
                const afterVisible = H.to('/api/header-status').length;
                await H.advance(1500);
                await H.fireWindow('online');
                await H.fireWindow('pageshow', {persisted: true});
                const afterOnline = H.to('/api/header-status').length;
                await H.advance(1500);
                await H.fireWindow('pageshow', {persisted: false});
                const afterNotPersisted = H.to('/api/header-status').length;
                return {base, afterVisible, afterOnline, afterNotPersisted};
                """
            ),
            wall=wall_of(at),
        )
        r = out.result
        assert r["afterVisible"] == r["base"] + 1
        assert r["afterOnline"] == r["afterVisible"] + 1, (
            "online and pageshow within 1 s send one read"
        )
        assert r["afterNotPersisted"] == r["afterOnline"], (
            "a pageshow that is not persisted reads nothing"
        )

    def test_the_dashboard_page_sends_no_header_status_read(self):
        at = et(10, 43, 5)
        out = run_page(
            render_page("/"),
            "await H.boot(); await H.advance(61000); return H.to('/api/header-status').length;",
            routes=[route(STATUS, header_answer(at), method="GET")],
            pathname="/",
            wall=wall_of(at),
        )
        assert out.result == 0


class TestLabels:
    def test_closes_within_sixty_seconds_of_the_bell(self):
        before = et(15, 59, 30)
        out = run(
            "/history",
            [header_answer(before), header_answer(et(16, 0, 0)), header_answer(et(16, 0, 30))],
            scenario_label(
                """
                const first = H.text('#engine-status-label');
                await H.advance(60000);
                return {first, last: H.text('#engine-status-label'), countdown: H.$('#next-run-countdown').style.display};
                """
            ),
            wall=wall_of(before),
        )
        assert out.result["first"] == "Live"
        assert out.result["last"] == "Closed"
        assert out.result["countdown"] == "none"

    def test_opening_then_live_across_the_first_cycle(self):
        t0, t1, t2 = et(9, 25), et(9, 30, 20), et(9, 31, 10)
        out = run(
            "/ai-advisor",
            [
                header_answer(t0, cycle_age_s=60 * 60 * 17),
                header_answer(t1, cycle_age_s=60 * 60 * 17),
                header_answer(t2, cycle_age_s=15),
            ],
            scenario_label(
                """
                const seen = [H.text('#engine-status-label')];
                await H.advance(30000); seen.push(H.text('#engine-status-label'));
                await H.advance(30000); seen.push(H.text('#engine-status-label'));
                return seen;
                """
            ),
            wall=wall_of(t0),
        )
        assert out.result == ["Closed", "Opening", "Live"]

    def test_rebalance_pause_not_stale(self):
        at = et(15, 56)
        out = run(
            "/settings",
            [header_answer(at, cycle_age_s=240)],
            scenario_label("return {label: H.text('#engine-status-label')};"),
            wall=wall_of(at),
        )
        assert out.result["label"] == "Rebalance pause"

    def test_stale_by_the_servers_clock_not_the_browsers(self):
        at = et(11, 0)
        browser_ten_minutes_behind = wall_of(et(10, 50))
        out = run(
            "/history",
            [header_answer(at, cycle_age_s=180)],
            scenario_label(
                f"return {{label: H.text('#engine-status-label'), title: H.attr('{TITLE_HOLDER}', 'title')}};"
            ),
            wall=browser_ten_minutes_behind,
        )
        assert out.result["label"] == "Stale"
        assert out.result["title"] == chrome_text("STALE_TITLE")

    def test_connection_lost_on_a_failed_read_then_recovery(self):
        at = et(10, 43, 5)
        out = run(
            "/history",
            [
                header_answer(at),
                {"reject": True},
                server_error_answer(),
                header_answer(et(10, 44, 5)),
            ],
            scenario_label(
                """
                const log = [];
                const snap = () => ({label: H.text('#engine-status-label'), countdown: H.$('#next-run-countdown').style.display});
                log.push(snap());
                for (let i = 0; i < 3; i++) { await H.advance(30000); log.push(snap()); }
                return log;
                """
            ),
            wall=wall_of(at),
            strict=True,
        )
        live, lost_network, lost_500, recovered = out.result
        assert live["label"] == "Live"
        assert lost_network == {"label": "Connection Lost", "countdown": "none"}
        assert lost_500["label"] == "Connection Lost"
        assert recovered["label"] == "Live" and recovered["countdown"] != "none"

    def test_not_updating_after_ninety_seconds_without_a_good_read(self):
        at = et(10, 43, 5)
        out = run(
            "/performance",
            [
                header_answer(at),
                {"hold": "never-1"},
                {"hold": "never-2"},
                {"hold": "never-3"},
                {"hold": "never-4"},
            ],
            scenario_label(
                """
                await H.advance(60000);
                const early = H.text('#engine-status-label');
                await H.advance(40000);
                return {early, late: H.text('#engine-status-label')};
                """
            ),
            wall=wall_of(at),
        )
        assert out.result["early"] == "Live"
        assert out.result["late"] == "Not updating"

    def test_newest_read_wins_when_the_older_lands_last(self):
        t0, t1 = et(15, 59, 50), et(16, 0, 5)
        out = run(
            "/history",
            [header_answer(t0, delay_ms=0) | {"hold": "older"}, header_answer(t1)],
            scenario_label(
                """
                await H.advance(1500);
                await H.setVisibility('hidden');
                await H.setVisibility('visible');
                const afterNewer = H.text('#engine-status-label');
                await H.release('older');
                return {afterNewer, afterOlder: H.text('#engine-status-label')};
                """
            ),
            wall=wall_of(t0),
        )
        assert out.result["afterNewer"] == "Closed"
        assert out.result["afterOlder"] == "Closed", "the older read landed last and was drawn"

    def test_lapsed_session_goes_to_login(self):
        at = et(10, 43, 5)
        out = run(
            "/history",
            [header_answer(at), lapsed_session_answer()],
            scenario_label("await H.advance(30000); return H.navigations;"),
            wall=wall_of(at),
        )
        assert "assign:/login" in out.result


class TestDegraded:
    REASONS = chrome_text_map("DEGRADED_REASON_TEXT")

    @pytest.mark.parametrize(
        "reasons", [["engine_overrun"], ["scheduler_stalled"], ["engine_failing"]]
    )
    def test_each_reason_reads_its_fixed_sentence_never_the_key(self, reasons):
        at = et(10, 43, 5)
        out = run(
            "/history",
            [header_answer(at, reasons=reasons)],
            scenario_label(
                f"return {{label: H.text('#engine-status-label'), title: H.attr('{TITLE_HOLDER}', 'title')}};"
            ),
            wall=wall_of(at),
        )
        assert out.result["label"] == "Degraded"
        assert out.result["title"] == self.REASONS[reasons[0]]
        assert reasons[0] not in out.result["title"]

    def test_two_reasons_join_with_a_space_and_an_unknown_one_reads_the_generic_sentence(self):
        at = et(10, 43, 5)
        out = run(
            "/history",
            [header_answer(at, reasons=["scheduler_stalled", "engine_overrun", "from_the_future"])],
            scenario_label(f"return H.attr('{TITLE_HOLDER}', 'title');"),
            wall=wall_of(at),
        )
        assert out.result == " ".join(
            [
                self.REASONS["scheduler_stalled"],
                self.REASONS["engine_overrun"],
                chrome_text("DEGRADED_UNKNOWN_TEXT"),
            ]
        )

    def test_degraded_outranks_closed(self):
        at = et(20, 0)
        out = run(
            "/history",
            [header_answer(at, reasons=["engine_failing"])],
            scenario_label("return H.text('#engine-status-label');"),
            wall=wall_of(at),
        )
        assert out.result == "Degraded"

    def test_not_updating_outranks_degraded(self):
        at = et(10, 43, 5)
        out = run(
            "/history",
            [
                header_answer(at, reasons=["engine_overrun"]),
                {"hold": "a"},
                {"hold": "b"},
                {"hold": "c"},
                {"hold": "d"},
            ],
            scenario_label("await H.advance(95000); return H.text('#engine-status-label');"),
            wall=wall_of(at),
        )
        assert out.result == "Not updating"

    def test_degraded_clears_when_the_reasons_do(self):
        at = et(10, 43, 5)
        out = run(
            "/history",
            [
                header_answer(at, reasons=["engine_overrun"]),
                header_answer(et(10, 43, 35), reasons=[]),
            ],
            scenario_label(
                "const a = H.text('#engine-status-label'); await H.advance(30000); return [a, H.text('#engine-status-label')];"
            ),
            wall=wall_of(at),
        )
        assert out.result == ["Degraded", "Live"]


class TestCountdown:
    def test_counts_down_each_second_and_wraps_without_resting_on_zero(self):
        start = et(10, 43, 5)
        answers = [header_answer(start)] + [
            header_answer(start + timedelta(seconds=30 * k), delay_ms=4000) for k in range(1, 5)
        ]
        out = run(
            "/performance",
            answers,
            scenario_label(
                """
                const samples = [];
                for (let s = 1; s <= 125; s++) {
                  await H.advance(1000);
                  samples.push({s, text: H.text('#next-run-countdown'), wall: Date.now()});
                }
                return samples;
                """
            ),
            wall=wall_of(start),
        )
        zero_run = 0
        for sample in out.result:
            match = re.fullmatch(r"NEXT (\d\d):(\d\d)", sample["text"] or "")
            assert match, f"countdown text {sample['text']!r} is not NEXT MM:SS"
            shown = int(match.group(1)) * 60 + int(match.group(2))
            wall_seconds = (sample["wall"] / 1000.0) % 60
            expected = (60 - wall_seconds) % 60
            gap = min(abs(shown - expected), 60 - abs(shown - expected))
            assert gap <= 1.5, (
                f"at +{sample['s']}s the countdown shows {shown}, the next :00 is {expected:.1f}s away"
            )
            zero_run = zero_run + 1 if shown == 0 else 0
            assert zero_run <= 2, "the countdown rested on 00:00"
        assert len({s["text"] for s in out.result}) > 30, "the countdown did not move"


class TestModePillAndWorkspace:
    def test_pill_follows_the_live_flag_without_a_reload(self):
        at = et(10, 43, 5)
        out = run(
            "/history",
            [
                header_answer(at, live=False),
                header_answer(et(10, 43, 35), live=True),
                header_answer(et(10, 44, 5), live=False),
            ],
            scenario_label(
                f"""
                const read = () => ({{text: H.text('{PILL}'), live: H.attr('{PILL}', 'data-live')}});
                const seen = [read()];
                await H.advance(30000); seen.push(read());
                await H.advance(30000); seen.push(read());
                return seen;
                """
            ),
            wall=wall_of(at),
        )
        assert out.result == [
            {"text": "DRY RUN", "live": "false"},
            {"text": "LIVE", "live": "true"},
            {"text": "DRY RUN", "live": "false"},
        ]

    def test_workspace_label_follows_meta_until_the_operator_picks_an_account(self):
        at = et(10, 43, 5)
        out = run(
            "/history",
            [
                header_answer(at, accounts=ACCOUNT_ENV),
                header_answer(et(10, 43, 35), accounts=ACCOUNT_ENV),
            ],
            scenario_label(
                """
                const before = H.text('#ws-label');
                window.activeAccount = 'picked-uuid';
                H.$('#ws-label').textContent = 'Picked account';
                await H.advance(30000);
                return {before, after: H.text('#ws-label')};
                """
            ),
            wall=wall_of(at),
        )
        assert out.result["before"] == "Individual"
        assert out.result["after"] == "Picked account", "a poll overwrote the operator's pick"
