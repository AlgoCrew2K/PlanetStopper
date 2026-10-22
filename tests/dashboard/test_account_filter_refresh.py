"""Picking an account in the workspace switcher refreshes the dashboard for that account.

Contract under test:
  * Picking an account reloads the dashboard state through ``/api/state``'s ``account`` query
    parameter and shows only that account's symphony cards; the two section counts follow the cards.
  * Every later refresh (the 30 s poll, the cycle-complete event, the Force run refresh) keeps the
    filter, and the parameter is URL-encoded. With no account picked no ``account`` parameter is sent.
  * The hero chart and the portfolio strip keep covering every account and say so with the caption
    "All accounts" while an account is picked, never mixing filtered symphonies with whole-account totals.
  * After market hours the server's frozen branch ignores ``account``; the client narrows the cards.
  * An account with no symphonies shows no cards and zero counts, with no new copy.
  * Whatever order overlapping ``/api/state`` responses land in, the page shows the response to the
    most recently issued request: a slow response for an earlier pick, a poll issued before a pick,
    a response issued before the pick was cleared, and a poll overlapping a cycle-complete refresh
    never overwrite a newer one, and a stale failure never raises the "Connection Lost" cue.

Real call path: the real ``static/chrome.js`` and ``static/index.js`` run whole under node over the page
``GET /`` really renders, with ``/api/state`` bodies produced by the real route
(see ``_account_page_harness.py``). Fixture provenance: ``tests/fixtures/dashboard/account_filter/PROVENANCE.md``.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.dashboard import _account_page_harness as harness

pytestmark = pytest.mark.usefixtures("_isolated_env")


@pytest.fixture
def _isolated_env(monkeypatch, tmp_path):
    harness.isolate_environment(monkeypatch, tmp_path)


@pytest.fixture
def fx() -> dict:
    return harness.load_accounts_fixture()


def _state_body(client, **query) -> dict:
    response = client.get("/api/state", query_string=query)
    assert response.status_code == 200, f"/api/state {query} answered {response.status_code}"
    return response.get_json()


def _routes(fx: dict, monkeypatch, *, market_state: str = "open", bot_state: dict | None = None):
    """``/api/state`` bodies from the real route, for no account and for each account of the fixture."""
    routes = {}
    with harness.state_api(
        monkeypatch, bot_state or fx["bot_state"], market_state=market_state
    ) as client:
        routes["/api/state"] = _state_body(client)
        for account in fx["accounts"].values():
            routes[f"/api/state|{account}"] = _state_body(client, account=account)
    return routes


def _query(url: str) -> dict:
    parts = urlsplit(url)
    assert parts.path == "/api/state", f"not a state request: {url}"
    return parse_qs(parts.query, keep_blank_values=True)


def _counts(fx: dict, ids: list[str]) -> tuple[str, str]:
    entries = [fx["bot_state"][i] for i in ids]
    active = sum(1 for e in entries if harness.is_active(e))
    return str(active), str(len(entries) - active)


LOAD_PAGE = "H.fire('DOMContentLoaded'); await H.flush();"

COUNTS_JS = """
  const counts = function () {
    return [H.byTestid('active-section-count').textContent, H.byTestid('standby-section-count').textContent];
  };
"""


class TestPickingAnAccountReloadsTheCards:
    @pytest.mark.parametrize("which", ["first", "second"])
    def test_pick_requests_that_accounts_state_and_shows_only_its_cards(
        self, fx, monkeypatch, tmp_path, which
    ):
        account = fx["accounts"][which]
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + """
              const before = H.visibleCardIds();
              const m = H.mark();
              H.window.switchAccount(H.params.account);
              await H.flush();
              return { before: before, urls: H.stateRequests(m), visible: H.visibleCardIds(),
                       picked: H.window.activeAccount };
            }""",
            html=html,
            routes=_routes(fx, monkeypatch),
            params={"account": account},
        )
        result = out["result"]
        everything = sorted(harness.symphony_entries(fx["bot_state"]))
        assert sorted(result["before"]) == everything, "before any pick every account's card shows"
        assert len(result["urls"]) == 1, f"one state request follows a pick, got {result['urls']}"
        assert _query(result["urls"][0]) == {"account": [account]}
        assert sorted(result["visible"]) == sorted(
            harness.ids_for_account(fx["bot_state"], account)
        ), "only the picked account's cards stay visible"
        assert out["unrouted"] == []

    @pytest.mark.parametrize("which", ["first", "second"])
    def test_pick_sets_both_section_counts_to_that_accounts_cards(
        self, fx, monkeypatch, tmp_path, which
    ):
        account = fx["accounts"][which]
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + COUNTS_JS
            + """
              H.window.switchAccount(H.params.account);
              await H.flush();
              return { counts: counts() };
            }""",
            html=html,
            routes=_routes(fx, monkeypatch),
            params={"account": account},
        )
        expected = _counts(fx, harness.ids_for_account(fx["bot_state"], account))
        assert [str(c) for c in out["result"]["counts"]] == list(expected), (
            "active and standby counts must cover only the picked account's symphonies"
        )

    def test_choosing_all_accounts_again_restores_every_card_and_drops_the_parameter(
        self, fx, monkeypatch, tmp_path
    ):
        account = fx["accounts"]["second"]
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + COUNTS_JS
            + """
              H.window.switchAccount(H.params.account);
              await H.flush();
              const narrowed = H.visibleCardIds();
              const m = H.mark();
              H.window.switchAccount(null);
              await H.flush();
              return { narrowed: narrowed, urls: H.stateRequests(m), visible: H.visibleCardIds(),
                       counts: counts() };
            }""",
            html=html,
            routes=_routes(fx, monkeypatch),
            params={"account": account},
        )
        result = out["result"]
        everything = sorted(harness.symphony_entries(fx["bot_state"]))
        assert sorted(result["narrowed"]) == sorted(
            harness.ids_for_account(fx["bot_state"], account)
        ), "the pick must narrow the cards before the reset means anything"
        assert result["urls"] == ["/api/state"], "no account parameter once all accounts are chosen"
        assert sorted(result["visible"]) == everything
        assert [str(c) for c in result["counts"]] == list(_counts(fx, everything))

    def test_poll_and_cycle_event_keep_the_account_filter(self, fx, monkeypatch, tmp_path):
        account = fx["accounts"]["first"]
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + """
              H.window.switchAccount(H.params.account);
              await H.flush();
              const pickUrls = H.stateRequests(0).slice(-1);
              const m1 = H.mark();
              H.poll();
              await H.flush();
              const poll = H.stateRequests(m1);
              const m2 = H.mark();
              H.cycleComplete();
              await H.flush();
              return { pick: pickUrls, poll: poll, cycle: H.stateRequests(m2),
                       visible: H.visibleCardIds() };
            }""",
            html=html,
            routes=_routes(fx, monkeypatch),
            params={"account": account},
        )
        result = out["result"]
        assert [_query(u) for u in result["poll"]] == [{"account": [account]}], (
            "the 30 s poll must send the picked account"
        )
        assert [_query(u) for u in result["cycle"]] == [{"account": [account]}], (
            "the cycle-complete refresh must send the picked account"
        )
        assert [_query(u) for u in result["pick"]] == [{"account": [account]}]
        assert sorted(result["visible"]) == sorted(
            harness.ids_for_account(fx["bot_state"], account)
        )

    def test_force_run_refresh_keeps_the_account_filter(self, fx, monkeypatch, tmp_path):
        account = fx["accounts"]["first"]
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + """
              H.window.switchAccount(H.params.account);
              await H.flush();
              const button = H.byTestid('force-run-btn');
              const m = H.mark();
              if (button) { H.click(button); await H.flush(); }
              return { found: !!button, urls: H.stateRequests(m) };
            }""",
            html=html,
            routes=_routes(fx, monkeypatch),
            params={"account": account},
        )
        result = out["result"]
        assert result["found"], "the header's Force run button is rendered on the dashboard"
        assert [_query(u) for u in result["urls"]] == [{"account": [account]}], (
            "Force run refreshes the dashboard, and that refresh carries the picked account"
        )

    def test_loadstate_is_exposed_for_the_shared_header_script(self, fx, monkeypatch, tmp_path):
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + """
              return { kind: typeof H.window.loadState };
            }""",
            html=html,
            routes=_routes(fx, monkeypatch),
        )
        assert out["result"]["kind"] == "function", (
            "static/chrome.js calls window.loadState after a pick and after Force run; "
            "static/index.js must assign it"
        )


class TestAccountParameterRules:
    def test_account_with_no_symphonies_shows_no_cards_and_zero_counts_without_new_copy(
        self, fx, monkeypatch, tmp_path
    ):
        account = fx["accounts"]["without_symphonies"]
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + COUNTS_JS
            + """
              H.window.switchAccount(H.params.account);
              await H.flush();
              return { visible: H.visibleCardIds(), counts: counts(),
                       writes: H.textWrites.map(function (w) { return w.text; }) };
            }""",
            html=html,
            routes=_routes(fx, monkeypatch),
            params={"account": account},
        )
        result = out["result"]
        assert result["visible"] == []
        assert [str(c) for c in result["counts"]] == ["0", "0"]
        new_copy = [
            t
            for t in result["writes"]
            if re.search(r"\bno\b.*(symphon|card|strateg)|empty|nothing", t, re.I)
        ]
        assert new_copy == [], f"no explanatory copy is added for an empty account: {new_copy}"

    def test_account_id_is_url_encoded(self, fx, monkeypatch, tmp_path):
        account = fx["accounts"]["needs_url_encoding"]
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + """
              const m = H.mark();
              H.window.switchAccount(H.params.account);
              await H.flush();
              return { urls: H.stateRequests(m) };
            }""",
            html=html,
            routes=_routes(fx, monkeypatch),
            params={"account": account},
        )
        urls = out["result"]["urls"]
        assert len(urls) == 1, f"expected one state request, got {urls}"
        query = urlsplit(urls[0]).query
        assert parse_qs(query, keep_blank_values=True) == {"account": [account]}, (
            f"the id must survive a round trip through the query string: {query}"
        )
        assert "&q" not in query and "+" not in query.replace("%2B", ""), (
            f"raw reserved characters leaked into the query string: {query}"
        )
        assert out["unrouted"] == []

    @pytest.mark.parametrize("unset", [None, ""])
    def test_no_account_picked_sends_no_account_parameter(self, fx, monkeypatch, tmp_path, unset):
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + """
              H.window.activeAccount = H.params.unset;
              const m = H.mark();
              H.poll();
              await H.flush();
              return { first: H.stateRequests(0)[0], later: H.stateRequests(m) };
            }""",
            html=html,
            routes=_routes(fx, monkeypatch),
            params={"unset": unset},
        )
        assert out["result"]["first"] == "/api/state", "the first load sends no account parameter"
        assert out["result"]["later"] == ["/api/state"], "a poll with no account sends none either"

    def test_workspace_list_failure_keeps_text_and_state(self, fx, monkeypatch, tmp_path):
        html = harness.render_dashboard_html(fx["bot_state"])
        routes = _routes(fx, monkeypatch)
        routes["/api/accounts"] = harness.REJECT
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + """
              const dropdown = H.byId('ws-dropdown');
              const m = H.mark();
              H.window.toggleAccountDropdown({ stopPropagation: function () {} });
              await H.flush();
              return { present: !!dropdown, html: dropdown ? dropdown.innerHTML : '',
                       hidden: dropdown ? H.isHidden(dropdown) : null,
                       urls: H.stateRequests(m), picked: H.window.activeAccount,
                       visible: H.visibleCardIds() };
            }""",
            html=html,
            routes=routes,
        )
        result = out["result"]
        assert result["present"], "the header's account list container is rendered"
        assert "Failed to load accounts" in result["html"]
        assert result["hidden"] is False, "the failure text is shown"
        assert result["urls"] == [], "opening the switcher does not reload the dashboard"
        assert result["picked"] is None
        assert sorted(result["visible"]) == sorted(harness.symphony_entries(fx["bot_state"]))


class TestFrozenSnapshotStillNarrowsOnTheClient:
    def test_closed_market_response_with_an_account_equals_the_one_without(self, fx, monkeypatch):
        with harness.state_api(
            monkeypatch, harness.frozen_bot_state(fx), market_state="closed_frozen"
        ) as client:
            everything = _state_body(client)
            picked = _state_body(client, account=fx["accounts"]["first"])
        assert picked["market_state"] == "closed_frozen"
        assert sorted(harness.symphony_entries(picked["bot_state"])) == sorted(
            harness.symphony_entries(fx["bot_state"])
        ), "the server's frozen branch does not apply the account filter"
        for body in (everything, picked):
            body["meta"].pop("clock_et")  # wall-clock stamp, differs between two calls
        assert picked == everything, "the closed branch's response must not depend on `account`"

    def test_after_hours_pick_hides_the_other_accounts_cards_and_keeps_them_hidden(
        self, fx, monkeypatch, tmp_path
    ):
        account = fx["accounts"]["second"]
        html = harness.render_dashboard_html(fx["bot_state"])
        routes = _routes(
            fx, monkeypatch, market_state="closed_frozen", bot_state=harness.frozen_bot_state(fx)
        )
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + COUNTS_JS
            + """
              H.window.switchAccount(H.params.account);
              await H.flush();
              const picked = H.visibleCardIds();
              const pickedCounts = counts();
              H.poll();
              await H.flush();
              return { picked: picked, pickedCounts: pickedCounts, afterPoll: H.visibleCardIds() };
            }""",
            html=html,
            routes=routes,
            params={"account": account},
        )
        result = out["result"]
        expected = sorted(harness.ids_for_account(fx["bot_state"], account))
        assert sorted(result["picked"]) == expected
        assert sorted(result["afterPoll"]) == expected
        assert [str(c) for c in result["pickedCounts"]] == list(_counts(fx, expected))


class TestHeroAndStripCoverEveryAccount:
    def test_strip_with_an_account_equals_the_strip_without_one(self, fx, monkeypatch):
        first = fx["accounts"]["first"]
        with harness.state_api(monkeypatch, fx["bot_state"]) as client:
            everything = _state_body(client)
            picked = _state_body(client, account=first)
        entries = harness.symphony_entries(fx["bot_state"])
        first_ids = sorted(harness.ids_for_account(fx["bot_state"], first))
        # The filter must really narrow, or the comparison below proves nothing.
        assert sorted(harness.symphony_entries(picked["bot_state"])) == first_ids
        assert sorted(s["id"] for s in picked["symphonies"]) == first_ids
        assert sorted(s["id"] for s in everything["symphonies"]) == sorted(entries)
        assert picked["portfolio_strip"] == everything["portfolio_strip"], (
            "the strip must cover all accounts, not the picked account's symphonies mixed with account totals"
        )
        total_value = sum(e["current_value"] for e in entries.values())
        assert picked["portfolio_strip"]["account_value"] == pytest.approx(total_value)

    def test_hero_stats_with_an_account_equal_the_hero_stats_without_one(self, fx, monkeypatch):
        first = fx["accounts"]["first"]
        with harness.state_api(monkeypatch, fx["bot_state"]) as client:
            everything = _state_body(client)
            picked = _state_body(client, account=first)
        for key in ("tracked", "armed", "triggered", "portfolio", "triggers_today", "market_state"):
            assert picked["meta"][key] == everything["meta"][key], (
                f"meta.{key} feeds the hero and must cover all accounts"
            )
        assert everything["meta"]["tracked"] == len(harness.symphony_entries(fx["bot_state"]))

    def test_without_an_account_the_strip_covers_every_symphony(self, fx, monkeypatch):
        with harness.state_api(monkeypatch, fx["bot_state"]) as client:
            body = _state_body(client)
        entries = harness.symphony_entries(fx["bot_state"])
        assert sorted(harness.symphony_entries(body["bot_state"])) == sorted(entries)
        assert body["portfolio_strip"]["account_value"] == pytest.approx(
            sum(e["current_value"] for e in entries.values())
        )

    @pytest.mark.parametrize(
        ("caption_id", "container_testid"),
        [("hero-scope-caption", "hero-left"), ("strip-scope-caption", "hero-right")],
    )
    def test_scope_caption_is_rendered_hidden_inside_its_hero_column(
        self, fx, caption_id, container_testid
    ):
        elements = harness.parse_elements(harness.render_dashboard_html(fx["bot_state"]))
        found = [i for i, e in enumerate(elements) if e["attrs"].get("id") == caption_id]
        assert len(found) == 1, f"expected one #{caption_id}, found {len(found)}"
        caption = elements[found[0]]
        assert "hidden" in caption["attrs"], "no account is picked at first paint"
        assert caption["text"].strip() == "All accounts"
        assert "legend-as-of" in harness.class_list(caption), (
            "the caption adopts the existing legend caption primitive"
        )
        ancestors, parent = [], caption["parent"]
        while parent >= 0:
            ancestors.append(elements[parent]["attrs"].get("data-testid"))
            parent = elements[parent]["parent"]
        assert container_testid in ancestors, f"#{caption_id} belongs inside {container_testid}"

    def test_captions_show_while_an_account_is_picked_and_hide_again(
        self, fx, monkeypatch, tmp_path
    ):
        account = fx["accounts"]["first"]
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + """
              const read = function () {
                const out = {};
                ['hero-scope-caption', 'strip-scope-caption'].forEach(function (id) {
                  const el = H.byId(id);
                  out[id] = el ? { hidden: H.isHidden(el), text: el.textContent.trim() } : null;
                });
                return out;
              };
              const initial = read();
              H.window.switchAccount(H.params.account);
              await H.flush();
              const picked = read();
              H.window.switchAccount(null);
              await H.flush();
              return { initial: initial, picked: picked, cleared: read() };
            }""",
            html=html,
            routes=_routes(fx, monkeypatch),
            params={"account": account},
        )
        result = out["result"]
        for caption_id in ("hero-scope-caption", "strip-scope-caption"):
            assert result["initial"][caption_id] is not None, f"#{caption_id} is rendered"
            assert result["initial"][caption_id]["hidden"] is True
            assert result["picked"][caption_id]["hidden"] is False, (
                f"#{caption_id} shows while an account is picked"
            )
            assert result["picked"][caption_id]["text"] == "All accounts"
            assert result["cleared"][caption_id]["hidden"] is True


SNAPSHOT_JS = """
  const snap = function () {
    return {
      visible: H.visibleCardIds(),
      counts: [H.byTestid('active-section-count').textContent,
               H.byTestid('standby-section-count').textContent],
      captionsHidden: ['hero-scope-caption', 'strip-scope-caption'].map(function (id) {
        return H.isHidden(H.byId(id));
      })
    };
  };
"""


class TestNewestRequestWins:
    """The page always shows the response to the most recently issued ``/api/state`` request."""

    def _expected(self, fx: dict, account: str) -> dict:
        ids = harness.ids_for_account(fx["bot_state"], account)
        return {"visible": sorted(ids), "counts": list(_counts(fx, ids))}

    @pytest.mark.parametrize(("earlier", "later"), [("first", "second"), ("second", "first")])
    def test_slow_response_for_an_earlier_pick_does_not_replace_the_later_pick(
        self, fx, monkeypatch, tmp_path, earlier, later
    ):
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + SNAPSHOT_JS
            + """
              H.deferState(true);
              H.window.switchAccount(H.params.earlier);
              H.window.switchAccount(H.params.later);
              H.release(1);
              await H.flush();
              const afterNewer = snap();
              H.release(0);
              await H.flush();
              return { urls: H.deferredUrls(), afterNewer: afterNewer, afterOlder: snap(),
                       picked: H.window.activeAccount };
            }""",
            html=html,
            routes=_routes(fx, monkeypatch),
            params={"earlier": fx["accounts"][earlier], "later": fx["accounts"][later]},
        )
        result = out["result"]
        expected = self._expected(fx, fx["accounts"][later])
        assert [_query(u) for u in result["urls"]] == [
            {"account": [fx["accounts"][earlier]]},
            {"account": [fx["accounts"][later]]},
        ], "each pick issues its own filtered request"
        assert sorted(result["afterNewer"]["visible"]) == expected["visible"]
        assert result["afterNewer"]["counts"] == expected["counts"]
        assert sorted(result["afterOlder"]["visible"]) == expected["visible"], (
            "the earlier pick's response landing last must not hide the later pick's cards"
        )
        assert result["afterOlder"]["counts"] == expected["counts"], (
            "the earlier pick's response landing last must not change the counts"
        )
        assert result["afterOlder"]["captionsHidden"] == [False, False]

    def test_poll_issued_before_a_pick_does_not_overwrite_the_picked_account_response(
        self, fx, monkeypatch, tmp_path
    ):
        account = fx["accounts"]["first"]
        routes = _routes(fx, monkeypatch)
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + SNAPSHOT_JS
            + """
              // The two bodies differ in one hero figure so the older snapshot is recognisable.
              const body = function (key, armed) {
                const b = JSON.parse(JSON.stringify(H.params.routes[key]));
                b.meta.armed = armed;
                return b;
              };
              const armed = function () { return H.byId('hero-armed').textContent; };
              H.deferState(true);
              H.poll();
              H.window.switchAccount(H.params.account);
              H.release(1, body('/api/state|' + H.params.account, 5));
              await H.flush();
              const afterPick = Object.assign({ armed: armed() }, snap());
              H.release(0, body('/api/state', 2));
              await H.flush();
              return { urls: H.deferredUrls(), afterPick: afterPick,
                       afterPoll: Object.assign({ armed: armed() }, snap()) };
            }""",
            html=html,
            routes=routes,
            params={"account": account, "routes": routes},
        )
        result = out["result"]
        expected = self._expected(fx, account)
        assert result["urls"][0] == "/api/state", "the poll was issued before the pick"
        assert _query(result["urls"][1]) == {"account": [account]}
        assert sorted(result["afterPick"]["visible"]) == expected["visible"]
        assert str(result["afterPick"]["armed"]) == "5"
        assert str(result["afterPoll"]["armed"]) == "5", (
            "a poll issued before the pick and landing after it is the older snapshot and must "
            "not replace the picked account's response"
        )
        assert sorted(result["afterPoll"]["visible"]) == expected["visible"], (
            "a poll issued before the pick must not bring back every account's cards"
        )
        assert result["afterPoll"]["counts"] == expected["counts"]
        assert result["afterPoll"]["captionsHidden"] == [False, False]

    def test_request_issued_before_the_pick_was_cleared_does_not_narrow_the_page_again(
        self, fx, monkeypatch, tmp_path
    ):
        account = fx["accounts"]["second"]
        everything = sorted(harness.symphony_entries(fx["bot_state"]))
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + SNAPSHOT_JS
            + """
              H.deferState(true);
              H.window.switchAccount(H.params.account);
              H.window.switchAccount(null);
              H.release(1);
              await H.flush();
              const afterClear = snap();
              H.release(0);
              await H.flush();
              return { urls: H.deferredUrls(), afterClear: afterClear, afterFiltered: snap(),
                       picked: H.window.activeAccount };
            }""",
            html=html,
            routes=_routes(fx, monkeypatch),
            params={"account": account},
        )
        result = out["result"]
        assert [_query(u) for u in result["urls"][:1]] == [{"account": [account]}]
        assert result["urls"][1] == "/api/state", "clearing the pick issues an unfiltered request"
        assert result["picked"] is None
        assert sorted(result["afterClear"]["visible"]) == everything
        assert result["afterClear"]["captionsHidden"] == [True, True]
        assert sorted(result["afterFiltered"]["visible"]) == everything, (
            "the filtered response landing after the pick was cleared must not hide cards"
        )
        assert result["afterFiltered"]["counts"] == result["afterClear"]["counts"], (
            "the filtered response landing after the pick was cleared must not change the counts"
        )
        assert result["afterFiltered"]["captionsHidden"] == [True, True]

    def test_poll_overlapping_a_cycle_event_never_overwrites_the_newer_snapshot(
        self, fx, monkeypatch, tmp_path
    ):
        routes = _routes(fx, monkeypatch)
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + """
              const body = function (armed) {
                const b = JSON.parse(JSON.stringify(H.params.base));
                b.meta.armed = armed;
                return b;
              };
              const armed = function () { return H.byId('hero-armed').textContent; };
              H.deferState(true);
              // The poll is issued first and the cycle event second; the event's response lands first.
              H.poll();
              H.cycleComplete();
              H.release(1, body(5));
              await H.flush();
              const newerLanded = armed();
              H.release(0, body(2));
              await H.flush();
              const afterOlder = armed();
              // Control: responses landing in the order they were issued show the last one issued.
              H.poll();
              H.cycleComplete();
              H.release(2, body(3));
              await H.flush();
              H.release(3, body(8));
              await H.flush();
              return { urls: H.deferredUrls(), newerLanded: newerLanded, afterOlder: afterOlder,
                       inOrder: armed() };
            }""",
            html=html,
            routes=routes,
            params={"base": routes["/api/state"]},
        )
        result = out["result"]
        assert result["urls"] == ["/api/state"] * 4, "two poll requests and two cycle refreshes"
        assert str(result["newerLanded"]) == "5"
        assert str(result["afterOlder"]) == "5", (
            "the older snapshot landing last must not replace the newer one (armed went 5 then 2)"
        )
        assert str(result["inOrder"]) == "8", "responses landing in issue order show the newest"

    def test_stale_failure_after_a_newer_success_does_not_show_connection_lost(
        self, fx, monkeypatch, tmp_path
    ):
        routes = _routes(fx, monkeypatch)
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            "async function (H) {"
            + LOAD_PAGE
            + """
              const body = JSON.parse(JSON.stringify(H.params.base));
              body.meta.armed = 6;
              H.deferState(true);
              H.poll();
              H.cycleComplete();
              H.release(1, body);
              await H.flush();
              H.fail(0);
              await H.flush();
              return {
                lost: H.textWrites.filter(function (w) { return w.text === 'Connection Lost'; }).length,
                label: H.byId('engine-status-label').textContent,
                armed: H.byId('hero-armed').textContent
              };
            }""",
            html=html,
            routes=routes,
            params={"base": routes["/api/state"]},
        )
        result = out["result"]
        assert str(result["armed"]) == "6", "the newer response is on screen"
        assert result["lost"] == 0, (
            "a failure of a request that a newer one has replaced must not raise the cue"
        )
        assert result["label"] != "Connection Lost"
