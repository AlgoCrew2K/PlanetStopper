"""The Retirement tab's controls and the tab fragment, run by the real script on the real markup.

Subject: ``static/ai_advisor.js`` (unmodified) over the markup ``GET /ai-advisor`` returns for a
seeded world (``tests/app/_retirement_tab_world.py``), driven under node by
``tests/_ai_advisor_page_harness.py`` with ``fetch`` answered from a route table and timers on a
virtual clock.

* Run now: the requests, the poll cadence, the refusals and the reload, which opens on the
  Retirement tab.
* Approve and Reject: the request each sends, the status pill each leaves, and the fail-closed answers.
* The review's "Run review now" request, unchanged.
* The ``#<tab>`` fragment: ``tabNameFromHash`` over every rendered tab name and hostile input,
  the initial tab from the URL, a click that writes the fragment without a history entry, the
  ``hashchange`` listener, and the pointer, which issues no request.

Request shapes (``method``, URL, headers, body) are those of the script before this change; the
expected values are written here from that contract, not read from a run of the new script.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests._ai_advisor_page_harness import call_exports, run_advisor_page
from tests.advisors.test_retirement_recommender_composer_source import _series_cluster_scenario
from tests.app import _retirement_tab_world as w
from tests.app import _tenure_review_panel_builders as pb

REPO = pathlib.Path(__file__).resolve().parents[2]

CSRF = "csrf-token-for-the-harness"  # what the harness's /api/csrf-token answers
_RUN_BTN = '[data-testid="retirement-run-now-btn"]'
_WATCH = {
    "status": '[data-testid="retirement-run-now-status"]',
    "run_btn": _RUN_BTN,
    "pills": '[data-testid="retirement-rec-status"]',
    "approve": '[data-testid="retirement-approve-btn"]',
    "reject": '[data-testid="retirement-reject-btn"]',
    "tabs": '[role="tab"]',
    "panels": '[role="tabpanel"]',
    "review_btn": '[data-testid="tenure-review-run-btn"]',
    "review_msg": '[data-testid="tenure-review-run-message"]',
    "pointer": '[data-testid="retirement-pointer"]',
}
_POLL = "/api/retirement-recommendations"
_RUN = "/ai-advisor/retirement/run"


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    import database

    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "retirement_tab_client.db"))
    database.init_db()
    database.run_migrations()


@pytest.fixture()
def never_run_page(client, isolated_db):
    return w.get_page(client)


@pytest.fixture()
def cards_page(client, isolated_db, monkeypatch):
    w.run_tick_with_recommendations(monkeypatch, _series_cluster_scenario())
    return w.get_page(client), w.stored_recommendation_ids()


def _snapshot(run_id: str) -> dict:
    return {"body": {"last_run": {"run_id": run_id}, "recommendations": []}}


def _active_tab(obs: dict) -> list[str]:
    return [
        t["attrs"]["data-tab"]
        for t in obs["watched"]["tabs"]
        if t["attrs"].get("aria-selected") == "true"
    ]


def _active_panel(obs: dict) -> list[str]:
    return [
        p["attrs"]["data-tab"]
        for p in obs["watched"]["panels"]
        if "tab-panel--active" in p["classes"]
    ]


# ===========================================================================
# Run now
# ===========================================================================


class TestRunNow:
    def _run(self, html, tmp_path, routes, extra_steps=()):
        return run_advisor_page(
            html,
            tmp_path,
            steps=[
                {"op": "call", "fn": "retirementRunNow", "target": _RUN_BTN, "label": "run"},
                *extra_steps,
            ],
            routes=routes,
            watch=_WATCH,
        )

    def test_a_started_run_polls_every_five_seconds_then_reloads_on_the_retirement_tab(
        self, never_run_page, tmp_path
    ):
        result = self._run(
            never_run_page,
            tmp_path,
            {
                _POLL: [_snapshot("before"), _snapshot("before"), _snapshot("after")],
                _RUN: {"status": 202, "body": {"status": "started"}},
            },
        )
        step = result["steps"][0]
        sequence = [(r["method"], r["url"]) for r in step["requests"]]
        assert sequence == [
            ("GET", _POLL),
            ("POST", _RUN),
            ("GET", _POLL),
            ("GET", _POLL),
        ]
        assert step["requests"][0]["headers"] == {"Accept": "application/json"}
        start = step["requests"][0]["at"]
        assert [r["at"] - start for r in step["requests"][2:]] == [5000, 10000]
        post = step["requests"][1]
        assert post["headers"] == {"Content-Type": "application/json", "X-CSRF-Token": CSRF}
        assert post["body"] is None
        assert step["obs"]["reloads"] == 1
        assert result["steps"][0]["obs"]["hash"] == "#retirement"
        assert "reloading" in step["obs"]["watched"]["status"][0]["text"].lower()

    def test_the_reload_is_made_after_the_fragment_names_the_retirement_tab(
        self, never_run_page, tmp_path
    ):
        # The page reloads with the fragment already in the address: the reload opens that tab.
        result = self._run(
            never_run_page,
            tmp_path,
            {
                _POLL: [_snapshot("before"), _snapshot("after")],
                _RUN: {"status": 202, "body": {"status": "started"}},
            },
        )
        obs = result["steps"][0]["obs"]
        assert obs["reloads"] == 1 and obs["hash"] == "#retirement"

    def test_a_refusal_during_market_hours_shows_its_reason_and_polls_nothing(
        self, never_run_page, tmp_path
    ):
        result = self._run(
            never_run_page,
            tmp_path,
            {
                _POLL: _snapshot("before"),
                _RUN: {"status": 409, "body": {"status": "market_open"}},
            },
        )
        step = result["steps"][0]
        assert [(r["method"], r["url"]) for r in step["requests"]] == [
            ("GET", _POLL),
            ("POST", _RUN),
        ]
        status = step["obs"]["watched"]["status"][0]["text"]
        assert "outside market hours" in status
        assert step["obs"]["reloads"] == 0
        assert step["obs"]["watched"]["run_btn"][0]["disabled"] is False

    def test_a_run_already_in_flight_is_waited_for(self, never_run_page, tmp_path):
        result = self._run(
            never_run_page,
            tmp_path,
            {
                _POLL: [_snapshot("before"), _snapshot("before"), _snapshot("theirs")],
                _RUN: {"status": 409, "body": {"status": "already_running"}},
            },
        )
        step = result["steps"][0]
        assert [(r["method"], r["url"]) for r in step["requests"]] == [
            ("GET", _POLL),
            ("POST", _RUN),
            ("GET", _POLL),
            ("GET", _POLL),
        ]
        assert step["obs"]["reloads"] == 1 and step["obs"]["hash"] == "#retirement"

    def test_a_poll_that_keeps_failing_ends_with_the_still_going_text_and_no_reload(
        self, never_run_page, tmp_path
    ):
        result = self._run(
            never_run_page,
            tmp_path,
            {
                _POLL: [_snapshot("before"), {"status": 500, "body": {}}],
                _RUN: {"status": 202, "body": {"status": "started"}},
            },
        )
        step = result["steps"][0]
        polls = [r for r in step["requests"] if r["method"] == "GET"][1:]
        assert 35 <= len(polls) <= 37, "the poll runs for three minutes at five-second intervals"
        status = step["obs"]["watched"]["status"][0]["text"].lower()
        assert "still going" in status and "reload this page" in status
        assert step["obs"]["reloads"] == 0
        assert step["obs"]["watched"]["run_btn"][0]["disabled"] is False

    def test_a_missing_csrf_token_response_stops_before_the_post(self, never_run_page, tmp_path):
        result = run_advisor_page(
            never_run_page,
            tmp_path,
            steps=[{"op": "call", "fn": "retirementRunNow", "target": _RUN_BTN}],
            routes={
                _POLL: _snapshot("before"),
                "/api/csrf-token": {"status": 500, "body": {}},
            },
            watch=_WATCH,
        )
        step = result["steps"][0]
        assert not any(r["method"] == "POST" and r["url"] == _RUN for r in step["requests"])
        assert step["obs"]["reloads"] == 0

    def test_the_status_survives_a_tab_switch_and_the_reload_still_lands_on_retirement(
        self, never_run_page, tmp_path
    ):
        routes = {
            _POLL: [_snapshot("before"), _snapshot("before"), _snapshot("after")],
            _RUN: {"status": 202, "body": {"status": "started"}},
        }
        result = run_advisor_page(
            never_run_page,
            tmp_path,
            steps=[
                {"op": "call", "fn": "retirementRunNow", "target": _RUN_BTN, "hold": True},
                {"op": "advance", "ms": 2000, "hold": True, "label": "mid-wait"},
                {
                    "op": "click",
                    "selector": '[data-tab="correlations"]',
                    "hold": True,
                    "label": "away",
                },
                {"op": "advance", "ms": 4000, "hold": True, "label": "after the first poll"},
                {"op": "advance", "ms": 60000, "label": "finish"},
            ],
            routes=routes,
            watch=_WATCH,
        )
        mid, away, later, finish = (result["steps"][i]["obs"] for i in (1, 2, 3, 4))
        status_before = mid["watched"]["status"][0]["text"]
        assert "started" in status_before.lower()
        assert _active_tab(away) == ["correlations"]
        assert away["watched"]["status"][0]["text"] == status_before
        assert later["watched"]["status"][0]["text"] == status_before
        assert finish["reloads"] == 1
        assert finish["hash"] == "#retirement", "the reload must open the Retirement tab"


# ===========================================================================
# Approve and Reject
# ===========================================================================

_DECIDE = {
    "approve": ("/ai-advisor/retirement/approve", "Approved", "armed"),
    "reject": ("/ai-advisor/retirement/reject", "Rejected", "triggered"),
}


class TestDecisions:
    def _decide(self, html, tmp_path, action, body):
        route, _text, _cls = _DECIDE[action]
        return run_advisor_page(
            html,
            tmp_path,
            steps=[
                {
                    "op": "call",
                    "fn": "retDispatchDecision",
                    "target": f'[data-testid="retirement-{action}-btn"]',
                    "args": [action, "$target"],
                }
            ],
            routes={route: {"body": body}},
            watch=_WATCH,
        )

    @pytest.mark.parametrize("action", ["approve", "reject"])
    def test_the_request_is_the_one_the_script_always_sent_and_the_pill_changes(
        self, cards_page, tmp_path, action
    ):
        html, ids = cards_page
        route, text, pill_class = _DECIDE[action]
        result = self._decide(html, tmp_path, action, {"success": True})
        step = result["steps"][0]
        posts = [r for r in step["requests"] if r["method"] == "POST"]
        assert len(posts) == 1 and posts[0]["url"] == route
        assert posts[0]["headers"] == {"Content-Type": "application/json", "X-CSRF-Token": CSRF}
        first_card_id = (
            pb.parse_html(html).find(f"retirement-{action}-btn").attrs["data-candidate-id"]
        )
        assert first_card_id in ids
        assert json.loads(posts[0]["body"]) == {"candidate_id": first_card_id}
        first, second = step["obs"]["watched"]["pills"]
        assert first["text"] == text
        assert {"status-pill", pill_class} <= set(first["classes"])
        assert "standby" not in first["classes"]
        assert "retirement-status-chip" not in " ".join(first["classes"])
        # The other card is untouched.
        assert second["text"] == "Pending" and {"status-pill", "standby"} <= set(second["classes"])
        assert result["errors"] == []

    @pytest.mark.parametrize("action", ["approve", "reject"])
    def test_a_refusal_changes_nothing_and_says_so(self, cards_page, tmp_path, action):
        html, _ids = cards_page
        result = self._decide(
            html, tmp_path, action, {"success": False, "error": "invalid candidate_id"}
        )
        step = result["steps"][0]
        first = step["obs"]["watched"]["pills"][0]
        assert first["text"] == "Pending" and {"status-pill", "standby"} <= set(first["classes"])
        assert step["obs"]["alerts"] == [f"{action.capitalize()} failed: invalid candidate_id"]
        assert step["obs"]["watched"][action][0]["disabled"] is False

    def test_an_approval_does_not_reload_and_leaves_the_fragment_on_the_retirement_tab(
        self, cards_page, tmp_path
    ):
        html, _ids = cards_page
        result = run_advisor_page(
            html,
            tmp_path,
            steps=[
                {"op": "click", "selector": '[data-tab="retirement"]'},
                {
                    "op": "call",
                    "fn": "retDispatchDecision",
                    "target": '[data-testid="retirement-approve-btn"]',
                    "args": ["approve", "$target"],
                },
            ],
            routes={"/ai-advisor/retirement/approve": {"body": {"success": True}}},
            watch=_WATCH,
        )
        obs = result["steps"][-1]["obs"]
        assert obs["reloads"] == 0, "the operator reloads to see the checklist"
        assert obs["hash"] == "#retirement", "a reload now opens the Retirement tab"

    def test_the_review_entrys_lifecycle_swaps_its_pill_the_same_way(
        self, client, isolated_db, tmp_path
    ):
        pb.seed_bot_state()
        pb.store_full_run()
        html = w.get_page(client)
        result = run_advisor_page(
            html,
            tmp_path,
            steps=[
                {
                    "op": "call",
                    "fn": "retDispatchDecision",
                    "target": '[data-testid="retirement-approve-btn"]',
                    "args": ["approve", "$target"],
                }
            ],
            routes={"/ai-advisor/retirement/approve": {"body": {"success": True}}},
            watch=_WATCH,
        )
        pill = result["steps"][0]["obs"]["watched"]["pills"][0]
        assert pill["text"] == "Approved" and {"status-pill", "armed"} <= set(pill["classes"])
        post = [r for r in result["steps"][0]["requests"] if r["method"] == "POST"][0]
        assert json.loads(post["body"]) == {"candidate_id": pb.FULL_IDS["retire"]}

    def test_the_review_run_request_is_unchanged(self, client, isolated_db, tmp_path):
        html = w.get_page(client)
        result = run_advisor_page(
            html,
            tmp_path,
            steps=[
                {
                    "op": "call",
                    "fn": "retRunReview",
                    "target": '[data-testid="tenure-review-run-btn"]',
                }
            ],
            routes={"/ai-advisor/retirement/review/run": {"status": 202, "body": {}}},
            watch=_WATCH,
        )
        step = result["steps"][0]
        posts = [r for r in step["requests"] if r["method"] == "POST"]
        assert len(posts) == 1 and posts[0]["url"] == "/ai-advisor/retirement/review/run"
        assert posts[0]["headers"] == {"X-Requested-With": "XMLHttpRequest", "X-CSRF-Token": CSRF}
        assert posts[0]["body"] is None
        assert step["obs"]["watched"]["review_msg"][0]["text"] == (
            "Review started: reload this page when it finishes"
        )


# ===========================================================================
# The #<tab> fragment
# ===========================================================================

_RENDERED_TABS = [
    "overview",
    "correlations",
    "retirement",
    "asset-swaps",
    "logic-changes",
    "chat",
    "strategy-builder",
    "frontrunner-builder",
]
_HOSTILE = [
    "",
    "#",
    "#nope",
    "#Retirement",
    "#retirement/extra",
    "#retirement ",
    "#retirement%20",
    '#retirement"]',
    '#retirement"] , [data-tab=',
    "#a b",
    "#..",
    "#__proto__",
    "#constructor",
    "#toString",
    "#<script>alert(1)</script>",
    "retirement",
    None,
]


class TestFragment:
    def test_every_rendered_tab_name_selects_itself(self):
        calls = [
            {"fn": "tabNameFromHash", "args": [f"#{n}", _RENDERED_TABS]} for n in _RENDERED_TABS
        ]
        results = call_exports(calls)
        assert [r.get("value") for r in results] == _RENDERED_TABS

    @pytest.mark.parametrize("fragment", _HOSTILE)
    def test_anything_else_selects_the_overview(self, fragment):
        result = call_exports([{"fn": "tabNameFromHash", "args": [fragment, _RENDERED_TABS]}])[0]
        assert result.get("value") == "overview", (fragment, result)

    def test_a_name_that_is_not_a_rendered_tab_selects_the_overview(self):
        result = call_exports([{"fn": "tabNameFromHash", "args": ["#retirement", ["overview"]]}])[0]
        assert result.get("value") == "overview"

    def test_the_rendered_names_are_the_ones_the_page_has(self, never_run_page):
        assert w.tab_names(never_run_page) == _RENDERED_TABS

    @pytest.mark.parametrize("name", _RENDERED_TABS)
    def test_the_url_fragment_opens_that_tab_when_the_page_loads(
        self, never_run_page, tmp_path, name
    ):
        result = run_advisor_page(never_run_page, tmp_path, watch=_WATCH, initial_hash=f"#{name}")
        loaded = result["loaded"]
        assert _active_tab(loaded) == [name]
        assert _active_panel(loaded) == [name]
        assert result["errors"] == []

    @pytest.mark.parametrize("fragment", [f for f in _HOSTILE if f])
    def test_a_fragment_that_names_no_tab_opens_the_overview_without_error(
        self, never_run_page, tmp_path, fragment
    ):
        result = run_advisor_page(never_run_page, tmp_path, watch=_WATCH, initial_hash=fragment)
        assert _active_tab(result["loaded"]) == ["overview"]
        assert _active_panel(result["loaded"]) == ["overview"]
        assert result["errors"] == []

    def test_no_fragment_opens_the_overview(self, never_run_page, tmp_path):
        result = run_advisor_page(never_run_page, tmp_path, watch=_WATCH)
        assert _active_tab(result["loaded"]) == ["overview"]

    def test_a_click_selects_the_tab_and_writes_the_fragment_without_a_history_entry(
        self, never_run_page, tmp_path
    ):
        result = run_advisor_page(
            never_run_page,
            tmp_path,
            steps=[{"op": "click", "selector": '[data-tab="retirement"]'}],
            watch=_WATCH,
        )
        obs = result["steps"][0]["obs"]
        assert _active_tab(obs) == ["retirement"] and _active_panel(obs) == ["retirement"]
        assert obs["hash"] == "#retirement"
        assert len(obs["replaced"]) == 1, "one replaceState call, no history entry"
        assert (
            obs["replaced"][0]["url"].endswith("#retirement") and "push" not in obs["replaced"][0]
        )

    def test_a_hashchange_selects_the_named_tab(self, never_run_page, tmp_path):
        result = run_advisor_page(
            never_run_page,
            tmp_path,
            steps=[
                {"op": "hashchange", "hash": "#retirement"},
                {"op": "hashchange", "hash": "#chat"},
                {"op": "hashchange", "hash": "#nope"},
            ],
            watch=_WATCH,
        )
        seen = [_active_tab(s["obs"]) for s in result["steps"]]
        assert seen == [["retirement"], ["chat"], ["overview"]]
        assert [_active_panel(s["obs"]) for s in result["steps"]] == seen

    def test_the_overview_pointer_links_to_the_fragment_and_opens_the_tab(
        self, never_run_page, tmp_path
    ):
        result = run_advisor_page(
            never_run_page,
            tmp_path,
            steps=[
                {"op": "click", "selector": '[data-testid="retirement-pointer"]', "label": "click"},
                {"op": "hashchange", "hash": "#retirement", "label": "follow"},
            ],
            watch=_WATCH,
        )
        pointers = result["loaded"]["watched"]["pointer"]
        assert pointers, "the Overview has no Retirement pointer"
        pointer = pointers[0]
        assert pointer["attrs"]["href"] == "#retirement"
        click, follow = result["steps"]
        assert follow["obs"]["watched"]["tabs"] and _active_tab(follow["obs"]) == ["retirement"]
        assert _active_panel(follow["obs"]) == ["retirement"]
        # Selecting the pointer asks the server for nothing.
        assert click["requests"] == [] and follow["requests"] == []

    def test_the_tab_script_is_the_only_reader_of_the_fragment(self):
        readers = sorted(
            p.name
            for p in (REPO / "static").glob("*.js")
            if "location.hash" in p.read_text(encoding="utf-8")
        )
        assert readers == ["ai_advisor.js"]
