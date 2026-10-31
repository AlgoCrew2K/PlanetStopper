"""RED: /settings survives a restart, shows a load failure and reads a small armed list.

``static/settings.js`` (with ``request_guard.js`` and ``chrome.js``) runs unmodified over the page the
real app renders. Bodies are the real routes' (``tests/ui/_settings_page_answers``); the 403 page and
the rotated token are the real hook's and route's (``tests/ui/_refusal_answers``).

CONTRACT PINNED
* Save: after a restart the header poll notes a new ``daemon_started_at``, the token refreshes, and the
  next Save click sends one POST with the new token. A Save refused 403 sends nothing more, the page
  says ``Not saved: the session check refused it after a restart. Click Save again.``, the pending
  edit and an enabled Save stay, and the next click sends one POST with the new token.
* A flush Confirm refused 403 says the flush was not sent and needs a new click; no second POST.
* The load keeps its one retry after 400 ms; when that also fails ``#settings-load-error`` (role alert)
  reads ``Settings could not be loaded`` with a ``Retry`` button, and no control is bound to empty
  data. Retry loads the page as a first-try load does. A 401 navigates to ``/login``.
* The flush confirm reads ``GET /api/armed-symphonies`` (never ``/api/state``) and lists each entry
  labelled live. The Confirm request is unchanged.
* A save in flight while an input is edited: the 200 does not discard what was typed.
"""

from __future__ import annotations

import pytest

from tests.ui import _refusal_answers as answers
from tests.ui import _settings_page_answers as page
from tests.ui._header_status_answers import et, header_answer, lapsed_session_answer, wall_of
from tests.ui._page_js_harness import DEFAULT_TOKEN, render_page, route, run_page
from tests.ui._shell_support import (  # noqa: F401
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
)

NOT_SAVED = "Not saved: the session check refused it after a restart. Click Save again."
SETTINGS = r"/api/settings$"
TOKEN_URL = r"/api/csrf-token$"
START_TIME = "#execution-start-time"


@pytest.fixture
def seeded():
    return page.seed_state(flags=True)


# The daemon restarted after the page loaded: from now on the token route answers the new token.
RESTART = "H.setToken('" + answers.TOKEN_B + "');\n"


def run(scenario, *routes_, wall=None):
    kw = {"wall": wall} if wall else {}
    return run_page(
        render_page("/settings"),
        "await H.boot();\n" + RESTART + scenario,
        routes=list(routes_),
        pathname="/settings",
        **kw,
    )


EDIT_AND_SAVE = f"""
  await H.set('{START_TIME}', '09:35');
  await H.click('#btn-save');
"""


class TestSaveAfterARestart:
    def test_a_poll_that_sees_the_restart_gives_the_next_save_the_new_token(self, seeded):
        at = et(10, 43, 5)
        out = run(
            f"""
            await H.set('{START_TIME}', '09:35');
            await H.advance(30000);
            const tokenGets = H.to('/api/csrf-token').length;
            await H.click('#btn-save');
            return tokenGets;
            """,
            route(SETTINGS, page.settings_get_answer(), method="GET"),
            route(SETTINGS, page.settings_save_answer(), method="POST"),
            route(
                r"/api/header-status$",
                header_answer(at, daemon_started_at="2026-10-22T09:00:00Z"),
                header_answer(at, daemon_started_at="2026-10-22T10:43:30Z"),
                method="GET",
            ),
            wall=wall_of(at),
        )
        assert out.result == 2, "the restart must fetch one new token before the click"
        (post,) = out.posts("/api/settings")
        assert post["headers"]["x-csrf-token"] == answers.TOKEN_B

    def test_a_refused_save_says_so_keeps_the_edit_and_the_next_click_sends_one_post(self, seeded):
        out = run(
            EDIT_AND_SAVE
            + f"""
            await H.advance(60000);
            const after = {{surfaced: H.surfaced(), saveDisabled: H.$('#btn-save').disabled,
                           start: H.value('{START_TIME}'), posts: H.posts('/api/settings').length}};
            await H.click('#btn-save');
            return after;
            """,
            route(SETTINGS, page.settings_get_answer(), method="GET"),
            route(
                SETTINGS,
                answers.csrf_refused(),
                page.settings_save_answer(),
                method="POST",
                strict=True,
            ),
        )
        after = out.result
        assert NOT_SAVED in after["surfaced"]
        assert after["posts"] == 1, "the refused save was sent again"
        assert after["saveDisabled"] is False, "Save must stay enabled with the pending edit"
        assert after["start"] == "09:35", "the pending edit was lost"
        first, second = out.posts("/api/settings")
        assert first["headers"]["x-csrf-token"] == DEFAULT_TOKEN
        assert second["headers"]["x-csrf-token"] == answers.TOKEN_B

    def test_a_save_in_flight_when_the_restart_is_seen_is_not_repeated(self, seeded):
        at = et(10, 43, 5)
        out = run(
            f"""
            await H.set('{START_TIME}', '09:35');
            await H.click('#btn-save');
            await H.advance(30000);
            return H.posts('/api/settings').length;
            """,
            route(SETTINGS, page.settings_get_answer(), method="GET"),
            route(SETTINGS, {"hold": "slow-save"}, method="POST"),
            route(
                r"/api/header-status$",
                header_answer(at, daemon_started_at="2026-10-22T09:00:00Z"),
                header_answer(at, daemon_started_at="2026-10-22T10:43:30Z"),
                method="GET",
            ),
            wall=wall_of(at),
        )
        assert out.result == 1


class TestEditsSurviveASave:
    def test_a_value_typed_while_the_save_is_in_flight_is_kept(self, seeded):
        out = run(
            f"""
            await H.set('{START_TIME}', '09:35');
            await H.click('#btn-save');
            await H.set('{START_TIME}', '09:40');
            await H.release('save-lands');
            return {{value: H.value('{START_TIME}'), saveDisabled: H.$('#btn-save').disabled}};
            """,
            route(SETTINGS, page.settings_get_answer(), method="GET"),
            route(SETTINGS, page.settings_save_answer() | {"hold": "save-lands"}, method="POST"),
        )
        assert out.result == {"value": "09:40", "saveDisabled": False}, (
            "the reload discarded a typed value"
        )


class TestFlush:
    OPEN_PANEL = """
      await H.click('#btn-flush-trigger');
    """

    def test_the_confirm_reads_the_small_armed_route_and_labels_each_entry_live(self, seeded):
        armed = page.armed_answer()
        assert armed["json"]["armed"], "fixture integrity: some symphonies are armed"
        out = run(
            self.OPEN_PANEL
            + "return {listed: H.text('#flush-armed-list'), shown: H.isVisible('#flush-confirm-body')};",
            route(SETTINGS, page.settings_get_answer(), method="GET"),
            route(r"/api/armed-symphonies$", armed, method="GET"),
        )
        assert out.to("/api/state") == [], "the flush confirm must not fetch the whole state"
        assert len(out.to("/api/armed-symphonies")) == 1
        listed = out.result["listed"]
        for entry in armed["json"]["armed"]:
            assert entry["name"] in listed, f"{entry['name']} is missing from the confirm list"
        assert listed.lower().count("live") >= len(armed["json"]["armed"]), (
            "each entry is labelled live"
        )

    def test_a_refused_flush_says_it_was_not_sent_and_sends_nothing_more(self, seeded):
        out = run(
            self.OPEN_PANEL
            + """
            await H.click('#btn-flush-confirm');
            await H.advance(60000);
            return H.surfaced();
            """,
            route(SETTINGS, page.settings_get_answer(), method="GET"),
            route(r"/api/armed-symphonies$", page.armed_answer(), method="GET"),
            route(r"/api/settings/flush-resync$", answers.csrf_refused(), method="POST"),
        )
        assert len(out.posts("flush-resync")) == 1
        text = out.result.lower()
        assert "flush" in text and "not sent" in text and "click" in text
        assert len(out.to("/api/csrf-token")) == 2

    def test_the_confirm_request_is_the_one_it_always_was(self, seeded):
        out = run(
            self.OPEN_PANEL + "await H.click('#btn-flush-confirm'); return 1;",
            route(SETTINGS, page.settings_get_answer(), method="GET"),
            route(r"/api/armed-symphonies$", page.armed_answer(), method="GET"),
            route(
                r"/api/settings/flush-resync$",
                {"json": {"status": "ok", "errors": []}},
                method="POST",
            ),
        )
        (post,) = out.posts("flush-resync")
        assert post["headers"] == {"x-csrf-token": DEFAULT_TOKEN} and post["body"] is None
        assert post["initKeys"] == ["headers", "method"]


class TestLoadFailure:
    DOWN = {"status": 500, "text": "<html>boom</html>"}

    def test_two_failures_show_a_visible_error_with_retry_and_bind_nothing(self, seeded):
        out = run(
            """
            await H.advance(500);
            const box = H.$('#settings-load-error');
            return {text: box ? box.textContent : null, role: box ? box.getAttribute('role') : null,
                    visible: H.isVisible('#settings-load-error'),
                    retry: box ? box.querySelectorAll('button').map((b) => b.textContent.trim()) : [],
                    symphonyButtons: H.$$('.sym-list-btn').length, saveDisabled: H.$('#btn-save').disabled,
                    loads: H.to('/api/settings', 'GET').length};
            """,
            route(SETTINGS, self.DOWN, self.DOWN, method="GET"),
        )
        r = out.result
        assert r["text"] and "Settings could not be loaded" in r["text"], r
        assert r["role"] == "alert" and r["visible"] is True
        assert "Retry" in r["retry"]
        assert r["symphonyButtons"] == 0 and r["saveDisabled"] is True
        assert r["loads"] == 2, "the load keeps exactly one retry"

    def test_retry_renders_the_page_as_a_first_try_load_does(self, seeded):
        good = page.settings_get_answer()
        compare = """
          const view = () => ({symphonies: H.$('#sym-list').innerHTML, pill: H.text('#live-pill'), start: H.value('#execution-start-time')});
        """
        first_try = run(compare + "return view();", route(SETTINGS, good, method="GET"))
        out = run(
            compare
            + """
            await H.advance(500);
            const retry = H.$$('#settings-load-error button').find((b) => /Retry/.test(b.textContent));
            if (!retry) throw new Error('there is no Retry button');
            retry.click();
            await H.settle();
            return {view: view(), errorVisible: H.isVisible('#settings-load-error')};
            """,
            route(SETTINGS, self.DOWN, self.DOWN, good, method="GET"),
        )
        assert out.result["view"] == first_try.result
        assert out.result["errorVisible"] is False

    def test_a_lapsed_session_goes_to_login(self, seeded):
        out = run(
            "return H.navigations;",
            route(SETTINGS, lapsed_session_answer("/api/settings"), method="GET"),
        )
        assert "assign:/login" in out.result
