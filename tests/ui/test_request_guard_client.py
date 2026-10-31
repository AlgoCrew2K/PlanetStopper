"""RED: ``static/request_guard.js`` (``window.PSRequest``), the one request helper every page shares.

CONTRACT PINNED (the page scripts and the controls build on exactly these):

* ``PSRequest.issue(key)`` returns a number that grows per key; ``PSRequest.isLatest(key, n)`` is true
  only for the newest ``issue(key)``; keys do not affect each other. Module memory only.
* ``PSRequest.getJson(url)`` reads ``performance.now()`` as ``sentAtMono`` just before the ``fetch``,
  and resolves ``{ok: true, status, body, timing}`` for a 2xx JSON body, where ``timing`` is
  ``window.receiptTiming(sentAtMono, response)`` (``{sentAtMono, receivedAtMono, serverNowMs}``);
  otherwise ``{ok: false, status, kind}`` with ``kind`` ``unauthorized`` (401), ``http`` (other
  non-2xx), ``not_json`` (a 2xx body that is not JSON) or ``network`` (the request failed).
  ``PSRequest.goLogin()`` navigates to ``/login``.
* ``PSRequest.csrfToken()`` is the tab's one token, fetched once at ``DOMContentLoaded``;
  ``PSRequest.refreshCsrf()`` re-fetches it, concurrent callers sharing one ``GET /api/csrf-token``,
  and resolves ``true`` when a token arrived, else ``false``.
* ``PSRequest.noteDaemonStart(iso)`` remembers the first ``daemon_started_at`` it is given and calls
  ``refreshCsrf()`` when a later, different value arrives. Never on a timer.
* ``PSRequest.postAction(url, init)`` calls ``fetch(url, init)`` exactly once with the caller's
  ``init`` unchanged and resolves ``{status, ok, body, refused, tokenRefreshed, networkError}``:
  on a 403 it awaits ``refreshCsrf()`` and resolves ``refused: 'csrf'`` with ``tokenRefreshed``
  telling whether a new token arrived; any other answer has ``refused: null`` and carries the
  server's own parsed body (``null`` when it is not JSON); a failed request has
  ``networkError: true``. It has no retry path: no second POST ever follows a refusal.
* The module keeps its state in memory only, so two tabs never share a token.
"""

from __future__ import annotations

from pathlib import Path

from tests.ui import _refusal_answers as answers
from tests.ui._page_js_harness import DEFAULT_TOKEN, render_page, route, run_page

GUARD_JS = Path(__file__).resolve().parents[2] / "static" / "request_guard.js"
POST_URL = "/api/trigger"
TOKEN_URL = r"/api/csrf-token$"


def on_history(scenario, *, routes=()):
    return run_page(render_page("/history"), "await H.boot();\n" + scenario, routes=list(routes))


def token_sequence():
    """The token route as a daemon restart leaves it: the old token at load, the new one after."""
    return route(
        TOKEN_URL, {"json": {"csrf_token": DEFAULT_TOKEN}}, answers.token_answer(), method="GET"
    )


PRESENT = "if (!window.PSRequest) throw new Error('window.PSRequest is not defined');\n"


class TestSurface:
    def test_exposes_the_helper_functions(self):
        out = on_history(
            PRESENT
            + "return ['issue','isLatest','getJson','goLogin','csrfToken','refreshCsrf','noteDaemonStart','postAction']"
            ".map(n => typeof PSRequest[n]);"
        )
        assert out.result == ["function"] * 8

    def test_keeps_no_state_outside_the_tab(self):
        assert GUARD_JS.exists(), "static/request_guard.js does not exist"
        source = GUARD_JS.read_text(encoding="utf-8")
        for shared in (
            "localStorage",
            "sessionStorage",
            "BroadcastChannel",
            "postMessage",
            "cookie",
        ):
            assert shared not in source, f"two tabs must stay independent: {shared} shares state"

    def test_only_this_module_reads_the_token_route(self):
        static = Path(__file__).resolve().parents[2] / "static"
        holders = [
            p.name
            for p in static.glob("*.js")
            if "/api/csrf-token" in p.read_text(encoding="utf-8")
        ]
        assert holders == ["request_guard.js"], f"per-file token holders remain: {holders}"


class TestNewestWins:
    def test_issue_counts_per_key_and_only_the_newest_is_latest(self):
        out = on_history(
            PRESENT
            + """
            const a1 = PSRequest.issue('a'), a2 = PSRequest.issue('a'), b1 = PSRequest.issue('b');
            return {grows: a2 > a1, old: PSRequest.isLatest('a', a1), new: PSRequest.isLatest('a', a2),
                    other: PSRequest.isLatest('b', b1), unissued: PSRequest.isLatest('never', 1)};
            """
        )
        assert out.result == {
            "grows": True,
            "old": False,
            "new": True,
            "other": True,
            "unissued": False,
        }


class TestGetJson:
    def test_good_answer_carries_the_receipt_record(self):
        out = on_history(
            PRESENT
            + """
            const before = performance.now();
            const res = await PSRequest.getJson('/api/probe');
            return {res, before};
            """,
            routes=[
                route(
                    r"/api/probe$", {"json": {"value": 7}, "date": 1_700_000_000_000, "delay_ms": 0}
                )
            ],
        )
        res = out.result["res"]
        assert res["ok"] is True and res["status"] == 200 and res["body"] == {"value": 7}
        timing = res["timing"]
        assert timing["sentAtMono"] == out.result["before"], (
            "sentAtMono is read just before the fetch"
        )
        assert timing["receivedAtMono"] >= timing["sentAtMono"]
        assert timing["serverNowMs"] == 1_700_000_000_000, "serverNowMs is the Date header"

    def test_failures_are_told_apart(self):
        out = on_history(
            PRESENT
            + """
            const kinds = {};
            for (const name of ['unauth', 'boom', 'html', 'down']) {
              const r = await PSRequest.getJson('/api/' + name);
              kinds[name] = {ok: r.ok, status: r.status, kind: r.kind, hasBody: 'body' in r};
            }
            return kinds;
            """,
            routes=[
                route(r"/api/unauth$", {"status": 401, "json": {"status": "error"}}),
                route(r"/api/boom$", {"status": 500, "json": {"status": "error"}}),
                route(r"/api/html$", {"status": 200, "text": "<html>proxy page</html>"}),
                route(r"/api/down$", {"reject": True}),
            ],
        )
        kinds = out.result
        assert kinds["unauth"]["kind"] == "unauthorized" and kinds["unauth"]["status"] == 401
        assert kinds["boom"]["kind"] == "http" and kinds["boom"]["status"] == 500
        assert kinds["html"]["kind"] == "not_json"
        assert kinds["down"]["kind"] == "network"
        assert all(k["ok"] is False and not k["hasBody"] for k in kinds.values()), (
            "an answer that is not data carries no body to draw"
        )

    def test_go_login_navigates(self):
        out = on_history(PRESENT + "PSRequest.goLogin(); return H.navigations;")
        assert out.result == ["assign:/login"]


class TestTokenHolder:
    def test_one_token_fetch_at_load_for_the_whole_page(self):
        out = on_history(
            PRESENT + "return {token: PSRequest.csrfToken(), gets: H.to('/api/csrf-token').length};"
        )
        assert out.result == {"token": DEFAULT_TOKEN, "gets": 1}

    def test_concurrent_refreshes_share_one_request(self):
        out = on_history(
            PRESENT
            + "const both = await Promise.all([PSRequest.refreshCsrf(), PSRequest.refreshCsrf()]);"
            "return {both, gets: H.to('/api/csrf-token').length, token: PSRequest.csrfToken()};",
            routes=[token_sequence()],
        )
        assert out.result["both"] == [True, True]
        assert out.result["gets"] == 2, "one fetch at load plus exactly one shared refresh"
        assert out.result["token"] == answers.TOKEN_B

    def test_a_failed_refresh_keeps_the_old_token_and_reports_it(self):
        out = on_history(
            PRESENT
            + "const ok = await PSRequest.refreshCsrf(); return {ok, token: PSRequest.csrfToken()};",
            routes=[
                route(
                    TOKEN_URL,
                    {"json": {"csrf_token": DEFAULT_TOKEN}},
                    {"reject": True},
                    method="GET",
                )
            ],
        )
        assert out.result == {"ok": False, "token": DEFAULT_TOKEN}


class TestPostAction:
    INIT = (
        "{method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest.csrfToken()},"
        " body: JSON.stringify({a: 1}), keepalive: true}"
    )

    def test_a_refused_post_refreshes_the_token_and_is_never_repeated(self):
        out = on_history(
            PRESENT
            + f"""
            let res;
            await H.act('click', async () => {{ res = await PSRequest.postAction('{POST_URL}', {self.INIT}); }});
            await H.advance(60000);
            return {{res, token: PSRequest.csrfToken(), posts: H.posts().length, gets: H.to('/api/csrf-token').length}};
            """,
            routes=[
                route(POST_URL + "$", answers.csrf_refused(), method="POST"),
                token_sequence(),
            ],
        )
        res = out.result["res"]
        assert res["refused"] == "csrf" and res["tokenRefreshed"] is True and res["ok"] is False
        assert out.result["posts"] == 1, "a refused POST was sent again"
        assert out.result["gets"] == 2
        assert out.result["token"] == answers.TOKEN_B

    def test_the_callers_request_goes_out_untouched(self):
        out = on_history(
            PRESENT
            + f"await H.act('click', async () => {{ await PSRequest.postAction('{POST_URL}', {self.INIT}); }}); return 1;",
            routes=[route(POST_URL + "$", {"json": {"status": "success"}}, method="POST")],
        )
        (post,) = out.posts(POST_URL)
        assert post["method"] == "POST" and post["keepalive"] is True
        assert post["headers"] == {
            "content-type": "application/json",
            "x-csrf-token": DEFAULT_TOKEN,
        }
        assert post["body"] == '{"a":1}'
        assert post["initKeys"] == ["body", "headers", "keepalive", "method"]

    def test_the_servers_own_answer_is_returned_for_any_other_status(self):
        out = on_history(
            PRESENT
            + f"""
            let res;
            await H.act('click', async () => {{ res = await PSRequest.postAction('{POST_URL}', {self.INIT}); }});
            return res;
            """,
            routes=[
                route(
                    POST_URL + "$",
                    {"status": 400, "json": {"message": "confirm_account_id is required"}},
                    method="POST",
                )
            ],
        )
        res = out.result
        assert res["status"] == 400 and res["ok"] is False and res["refused"] is None
        assert res["body"] == {"message": "confirm_account_id is required"}
        assert len(out.to("/api/csrf-token")) == 1, "a 400 is not a token problem"

    def test_a_good_answer_and_an_unreadable_one(self):
        out = on_history(
            PRESENT
            + f"""
            const seen = [];
            for (const url of ['/api/good', '/api/page']) {{
              await H.act('click', async () => {{ seen.push(await PSRequest.postAction(url, {self.INIT})); }});
            }}
            return seen;
            """,
            routes=[
                route(r"/api/good$", {"json": {"status": "ok"}}, method="POST"),
                route(
                    r"/api/page$", {"status": 200, "text": "<html>not json</html>"}, method="POST"
                ),
            ],
        )
        good, page = out.result
        assert good["ok"] is True and good["refused"] is None and good["body"] == {"status": "ok"}
        assert page["ok"] is True and page["body"] is None

    def test_a_failed_request_is_a_network_error_and_sends_nothing_more(self):
        out = on_history(
            PRESENT
            + f"""
            let res;
            await H.act('click', async () => {{ res = await PSRequest.postAction('{POST_URL}', {self.INIT}); }});
            await H.advance(60000);
            return {{res, posts: H.posts().length}};
            """,
            routes=[route(POST_URL + "$", {"reject": True}, method="POST")],
        )
        assert out.result["res"]["networkError"] is True and out.result["res"]["refused"] is None
        assert out.result["posts"] == 1

    def test_when_the_token_fetch_fails_the_result_says_so(self):
        out = on_history(
            PRESENT
            + f"""
            let res;
            await H.act('click', async () => {{ res = await PSRequest.postAction('{POST_URL}', {self.INIT}); }});
            await H.advance(60000);
            return {{res, posts: H.posts().length}};
            """,
            routes=[
                route(POST_URL + "$", answers.csrf_refused(), method="POST"),
                route(
                    TOKEN_URL,
                    {"json": {"csrf_token": DEFAULT_TOKEN}},
                    {"reject": True},
                    method="GET",
                ),
            ],
        )
        assert (
            out.result["res"]["refused"] == "csrf" and out.result["res"]["tokenRefreshed"] is False
        )
        assert out.result["posts"] == 1


class TestRestartDetection:
    def test_a_changed_start_stamp_refreshes_the_token_once_and_an_unchanged_one_never(self):
        out = on_history(
            PRESENT
            + """
            const gets = () => H.to('/api/csrf-token').length;
            PSRequest.noteDaemonStart('2026-10-22T14:00:00Z');
            await H.settle();
            const afterFirst = gets();
            PSRequest.noteDaemonStart('2026-10-22T14:00:00Z');
            await H.settle();
            const afterSame = gets();
            PSRequest.noteDaemonStart('2026-10-22T15:30:00Z');
            await H.settle();
            const afterChange = gets();
            await H.advance(10 * 60 * 1000);
            return {afterFirst, afterSame, afterChange, afterTenMinutes: gets(), token: PSRequest.csrfToken()};
            """,
            routes=[token_sequence()],
        )
        r = out.result
        assert (r["afterFirst"], r["afterSame"]) == (1, 1), (
            "an unchanged start stamp must fetch nothing"
        )
        assert r["afterChange"] == 2, "a changed start stamp fetches exactly one new token"
        assert r["afterTenMinutes"] == 2, "the token is never refreshed on a timer"
        assert r["token"] == answers.TOKEN_B
        assert out.posts() == []

    def test_the_header_poll_notes_the_start_stamp_of_each_read(self):
        from tests.ui._header_status_answers import et, header_answer, wall_of

        at = et(10, 43, 5)
        out = run_page(
            render_page("/history"),
            "await H.boot(); await H.advance(30000); return {token: PSRequest.csrfToken(), gets: H.to('/api/csrf-token').length};",
            routes=[
                route(
                    r"/api/header-status$",
                    header_answer(at, daemon_started_at="2026-10-22T09:00:00Z"),
                    header_answer(at, daemon_started_at="2026-10-22T10:43:30Z"),
                    method="GET",
                    strict=True,
                ),
                token_sequence(),
            ],
            wall=wall_of(at),
        )
        assert out.result == {"token": answers.TOKEN_B, "gets": 2}


class TestTabsAreIndependent:
    def test_one_tabs_refusal_leaves_the_other_tab_untouched(self):
        refused_tab = on_history(
            PRESENT
            + f"await H.act('click', async () => {{ await PSRequest.postAction('{POST_URL}', {TestPostAction.INIT}); }});"
            "return PSRequest.csrfToken();",
            routes=[
                route(POST_URL + "$", answers.csrf_refused(), method="POST"),
                token_sequence(),
            ],
        )
        other_tab = on_history(
            PRESENT
            + "await H.advance(120000); return {token: PSRequest.csrfToken(), gets: H.to('/api/csrf-token').length};",
        )
        assert refused_tab.result == answers.TOKEN_B
        assert other_tab.result == {"token": DEFAULT_TOKEN, "gets": 1}
