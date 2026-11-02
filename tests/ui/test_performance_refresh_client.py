"""RED: /performance renders each panel on its own and shows its data age.

``static/performance.js`` runs unmodified over the page the real app renders; every body is the real
route's over the captured production records (``tests/ui/_performance_page``), the 401 is the real
auth hook's.

CONTRACT PINNED
* A panel's failure never blocks another: with ``Chart`` undefined the headline stats, banner and
  metrics table render exactly as they do when the chart draws.
* The exit-turnover panel keeps a last-good row per symphony: a symphony whose request fails keeps
  its previous row while the others redraw; when the network answers none of them the panel keeps all
  its rows and its text says ``connection lost`` (a read the server answered with an error is not a
  lost connection: tests/ui/test_exit_turnover_failure_shows_no_zeros.py).
* A 401 on the page's refresh navigates to ``/login``.
* ``#performance-data-as-of`` (class ``legend-as-of``) reads ``data as of <data_as_of>``.
* The 60-second tick sends the same data requests it sent before this work
  (``tests/fixtures/refresh_timing/04-c4/requests_per_tick_at_base.json``).
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

import pytest

import analytics
from tests.ui import _performance_page as perf
from tests.ui._header_status_answers import lapsed_session_answer
from tests.ui._page_js_harness import render_page, route
from tests.ui._shell_support import (  # noqa: F401
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
)

BASE_RECORD = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "refresh_timing"
        / "04-c4"
        / "requests_per_tick_at_base.json"
    ).read_text(encoding="utf-8")
)

PANELS = """
  const panels = () => ({
    metrics: H.$('#metrics-tbody').innerHTML,
    headline: H.$('[data-testid="headline-strip"]').textContent,
    banner: H.text('#insufficient-banner') + '|' + (H.$('#insufficient-banner').style.display || ''),
    caption: H.text('#obs-caption'),
    turnover: H.$$('#exit-turnover-tbody tr').map((tr) => tr.children.map((td) => td.textContent)),
    turnoverPanel: H.text('[data-testid="exit-turnover-panel"]'),
    stamp: H.text('#performance-data-as-of'),
  });
"""


@pytest.fixture
def world(monkeypatch):
    # analytics reads the shadow table through its own DB_FILE; point it at this test's database.
    monkeypatch.setattr(analytics, "DB_FILE", os.environ["DB_PATH"])
    return perf.seed_world()


def run(scenario, routes, **kw):
    return perf.run_performance(PANELS + scenario, routes, **kw)


class TestPanelsAreIndependent:
    def test_without_the_chart_library_the_other_panels_render_as_with_it(self, world):
        routes = perf.real_routes()
        before_data = run("return panels();", [route(r".", {"reject": True}, method="GET")]).result
        with_chart = run("return panels();", routes, chart="stub").result
        without_chart = run("return panels();", routes, chart="undefined").result
        assert with_chart["metrics"] != before_data["metrics"], (
            "fixture integrity: the metrics table must change when the data arrives"
        )
        for key in ("metrics", "headline", "banner", "caption"):
            assert without_chart[key] == with_chart[key], (
                f"{key} did not render when Chart is undefined"
            )

    def test_a_panel_that_cannot_draw_says_so_in_the_console(self, world):
        out = run("return panels();", perf.real_routes(), chart="undefined")
        assert out.errors, "a panel that cannot draw must say so in the console"


class TestExitTurnoverKeepsLastGoodRows:
    def test_one_failed_request_keeps_that_row_and_redraws_the_others(self, world):
        ids = perf.symphony_ids()
        failing, changing = ids[0], ids[1]
        good = {sid: perf.get_real(f"/api/exit-turnover?symphony_id={sid}") for sid in ids}
        moved = copy.deepcopy(good[changing])
        moved["json"]["windows"]["30"]["exit_count"] += 7
        out = run(
            """
            const first = panels().turnover;
            await H.advance(60000);
            return {first, second: panels().turnover};
            """,
            perf.real_routes(
                turnover_overrides={
                    failing: [good[failing], {"status": 500, "json": {"status": "error"}}],
                    changing: [good[changing], moved],
                }
            ),
        )
        first, second = out.result["first"], out.result["second"]
        assert len(first) == len(ids) == len(second), "a failed request dropped its row"
        assert second != first, "the symphony whose request succeeded was not redrawn"
        changed_rows = [i for i in range(len(first)) if first[i] != second[i]]
        assert len(changed_rows) == 1, "only the successful symphony's row may change"

    def test_every_request_failing_keeps_every_row_and_says_so(self, world):
        ids = perf.symphony_ids()
        down = [{"reject": True}]
        good = {sid: perf.get_real(f"/api/exit-turnover?symphony_id={sid}") for sid in ids}
        out = run(
            """
            const first = panels();
            await H.advance(60000);
            return {first, second: panels()};
            """,
            perf.real_routes(turnover_overrides={sid: [good[sid], *down] for sid in ids}),
        )
        first, second = out.result["first"], out.result["second"]
        assert first["turnover"] and second["turnover"] == first["turnover"]
        assert "connection lost" in second["turnoverPanel"].lower()
        assert "connection lost" not in first["turnoverPanel"].lower()

    def test_a_good_tick_after_a_failed_one_clears_the_cue(self, world):
        ids = perf.symphony_ids()
        good = {sid: perf.get_real(f"/api/exit-turnover?symphony_id={sid}") for sid in ids}
        flaky = {sid: [good[sid], {"reject": True}, good[sid]] for sid in ids}
        out = run(
            """
            await H.advance(60000);
            const lost = panels().turnoverPanel;
            await H.advance(60000);
            return {lost, recovered: panels().turnoverPanel};
            """,
            perf.real_routes(turnover_overrides=flaky),
        )
        assert "connection lost" in out.result["lost"].lower()
        assert "connection lost" not in out.result["recovered"].lower()


class TestSession:
    def test_a_lapsed_session_goes_to_login(self, world):
        routes = perf.real_routes()
        routes.insert(
            0,
            route(
                r"/api/performance\?",
                perf.get_real("/api/performance?scope=aggregate&days=60d"),
                lapsed_session_answer("/api/performance?scope=aggregate&days=60d"),
                method="GET",
            ),
        )
        out = run("await H.advance(60000); return H.navigations;", routes)
        assert "assign:/login" in out.result


class TestDataAge:
    def test_the_page_has_a_stamp_element_in_the_caption_class(self, world):
        html = render_page("/performance")
        match = re.search(r'<[^>]*id="performance-data-as-of"[^>]*>', html)
        assert match, "the page has no #performance-data-as-of element"
        assert "legend-as-of" in match.group(0), "the stamp must use the page's .legend-as-of style"

    def test_the_stamp_is_the_routes_data_as_of(self, world):
        body = perf.get_real("/api/performance?scope=aggregate&days=60d")["json"]
        assert "data_as_of" in body, "GET /api/performance carries no data_as_of"
        out = run("return panels().stamp;", perf.real_routes())
        assert out.result.startswith("data as of ")
        assert out.result.removeprefix("data as of ").strip() == body["data_as_of"]


class TestTickIsUnchanged:
    def test_the_sixty_second_tick_sends_the_requests_it_always_sent(self, world):
        assert perf.one_minute_tick_requests() == BASE_RECORD["tick_requests"]
