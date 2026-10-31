"""RED: the per-symphony settings modal never saves one symphony with another's data.

``static/settings-modal.js`` runs unmodified (with the page's own ``request_guard.js`` and ``chrome.js``)
over the page header the real app renders. The two symphonies are seeded through the real writers
(A on live, B on dry run) and every GET body is the real ``/api/symphony-settings/<id>``'s; the 403
page and the rotated token are the real hook's and route's.

CONTRACT PINNED
* ``openSymphonySettings`` takes a per-open sequence number: a response that is not the newest open's,
  or that lands after the modal was closed, is discarded (nothing renders, nothing is saved).
* Save posts to the symphony the modal is open on and carries only that symphony's own loaded
  values and the operator's edits: ``{live_mode, locked_vars}``, plus ``confirm: true`` when the live
  flag turns on after the go-live confirmation.
* A save refused 403 sends nothing more; the modal says the save was not sent and needs a new click,
  keeps the operator's edits, and the next Save asks the go-live confirmation again before one POST
  with the new token.
* A restart noted while a save is in flight fetches one new token and sends nothing; the save's own
  answer is rendered.
* A dashboard poll while the modal holds edits leaves its symphony, values and edits as they were.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import app as app_module
import database
from tests.ui import _refusal_answers as answers
from tests.ui._page_js_harness import DEFAULT_TOKEN, page_scripts, render_page, route, run_page

A, B = "symphony_a", "symphony_b"
NAME_A, NAME_B = "Copper Ridge Rotation", "Granite Bay Momentum"
GET_A = rf"/api/symphony-settings/{A}$"
GET_B = rf"/api/symphony-settings/{B}$"
SAVE = "#sym-settings-save"
TOGGLE = "#sym-settings-live-toggle"
NOT_SENT = re.compile(r"not sent", re.I)


@pytest.fixture
def symphonies():
    database.save_state({A: {"name": NAME_A}, B: {"name": NAME_B}})
    database.set_symphony_live_mode(A, 1, "test")
    bodies = {}
    for sid in (A, B):
        with app_module.app.test_client() as client:
            response = client.get(f"/api/symphony-settings/{sid}")
        assert response.status_code == 200
        bodies[sid] = response.get_json()
    assert bodies[A]["live_mode"] is True and bodies[B]["live_mode"] is False
    return bodies


def spec(body, **extra):
    return {"status": 200, "json": body, **extra}


def run(scenario, *routes_):
    html = render_page("/history")
    scripts = [s for s in page_scripts(html) if s != "history.js"] + ["settings-modal.js"]
    return run_page(
        html,
        "await H.boot();\n" + RESTART + scenario,
        routes=list(routes_),
        scripts=scripts,
        pathname="/history",
    )


# The daemon restarted after the page loaded: from now on the token route answers the new token.
RESTART = "H.setToken('" + answers.TOKEN_B + "');\n"


OPEN_B = f"""
  await H.act('open B', async () => {{ window.openSymphonySettings(null, '{B}'); }});
"""


class TestResponseOrdering:
    def test_a_late_response_for_the_first_symphony_is_discarded(self, symphonies):
        out = run(
            f"""
            await H.act('open A', async () => {{ window.openSymphonySettings(null, '{A}'); }});
            await H.act('open B', async () => {{ window.openSymphonySettings(null, '{B}'); }});
            await H.release('a-late');
            const modal = H.text('#sym-settings-modal');
            await H.click('{SAVE}');
            return {{modal, posts: H.posts('/api/symphony-settings').map((p) => [p.url, p.body])}};
            """,
            route(GET_A, spec(symphonies[A], hold="a-late"), method="GET"),
            route(GET_B, spec(symphonies[B]), method="GET"),
            route(r"/api/symphony-settings/", {"json": {"status": "ok"}}, method="POST"),
        )
        assert NAME_B in out.result["modal"] and NAME_A not in out.result["modal"], (
            "the modal shows the symphony whose response arrived last"
        )
        ((url, body),) = out.result["posts"]
        assert url == f"/api/symphony-settings/{B}", "Save posted to the wrong symphony"
        assert json.loads(body) == {
            "live_mode": False,
            "locked_vars": symphonies[B]["locked_vars"],
        }, "Save carried values that are not B's own"

    def test_a_response_that_lands_after_the_modal_was_closed_renders_nothing(self, symphonies):
        out = run(
            f"""
            await H.act('open A', async () => {{ window.openSymphonySettings(null, '{A}'); }});
            await H.fireDocument('keydown', {{key: 'Escape'}});
            await H.release('a-late');
            return {{hasSave: !!H.$('{SAVE}'), display: H.$('#sym-settings-modal').style.display,
                     posts: H.posts('/api/symphony-settings').length}};
            """,
            route(GET_A, spec(symphonies[A], hold="a-late"), method="GET"),
        )
        assert out.result["hasSave"] is False, "a closed modal was filled by a late response"
        assert out.result["display"] == "none"
        assert out.result["posts"] == 0

    def test_a_failed_load_says_so_and_offers_no_save(self, symphonies):
        out = run(
            OPEN_B + f"return {{text: H.text('#sym-settings-modal'), hasSave: !!H.$('{SAVE}')}};",
            route(GET_B, {"reject": True}, method="GET"),
        )
        assert "Failed to load settings. Please try again." in out.result["text"]
        assert out.result["hasSave"] is False

    def test_a_failure_for_the_first_symphony_does_not_blank_the_second(self, symphonies):
        out = run(
            f"""
            await H.act('open A', async () => {{ window.openSymphonySettings(null, '{A}'); }});
            await H.act('open B', async () => {{ window.openSymphonySettings(null, '{B}'); }});
            await H.release('a-fails');
            const shown = H.text('#sym-settings-modal');
            return {{shown, error: shown.includes('Failed to load')}};
            """,
            route(GET_A, {"reject": True, "hold": "a-fails"}, method="GET"),
            route(GET_B, spec(symphonies[B]), method="GET"),
        )
        assert out.result["error"] is False and NAME_B in out.result["shown"]


class TestGoLiveStillAsksFirst:
    def test_turning_b_live_shows_the_confirmation_before_any_post(self, symphonies):
        out = run(
            OPEN_B
            + f"""
            await H.click('{TOGGLE}');
            await H.click('{SAVE}');
            return {{confirm: !!H.$('#sym-settings-confirm'), posts: H.posts('/api/symphony-settings').length}};
            """,
            route(GET_B, spec(symphonies[B]), method="GET"),
        )
        assert out.result == {"confirm": True, "posts": 0}

    def test_the_confirmed_save_carries_confirm_and_the_flag(self, symphonies):
        out = run(
            OPEN_B
            + f"await H.click('{TOGGLE}'); await H.click('{SAVE}'); await H.click('#sym-confirm-go-live'); return 1;",
            route(GET_B, spec(symphonies[B]), method="GET"),
            route(r"/api/symphony-settings/", {"json": {"status": "ok"}}, method="POST"),
        )
        (post,) = out.posts("/api/symphony-settings")
        body = json.loads(post["body"])
        assert body == {
            "live_mode": True,
            "locked_vars": symphonies[B]["locked_vars"],
            "confirm": True,
        }
        assert post["url"] == f"/api/symphony-settings/{B}" and post["method"] == "POST"
        assert post["headers"] == {
            "content-type": "application/json",
            "x-csrf-token": DEFAULT_TOKEN,
        }
        assert post["initKeys"] == ["body", "headers", "method"]

    def test_a_save_without_a_live_change_posts_the_bare_pair(self, symphonies):
        out = run(
            OPEN_B + f"await H.click('{SAVE}'); return 1;",
            route(GET_B, spec(symphonies[B]), method="GET"),
            route(r"/api/symphony-settings/", {"json": {"status": "ok"}}, method="POST"),
        )
        (post,) = out.posts("/api/symphony-settings")
        assert json.loads(post["body"]) == {
            "live_mode": False,
            "locked_vars": symphonies[B]["locked_vars"],
        }


class TestRefusedSave:
    def test_the_refusal_keeps_the_edit_and_asks_the_confirmation_again(self, symphonies):
        out = run(
            OPEN_B
            + f"""
            await H.click('{TOGGLE}');
            await H.click('{SAVE}');
            await H.click('#sym-confirm-go-live');
            await H.advance(60000);
            const after = {{surfaced: H.surfaced(), posts: H.posts('/api/symphony-settings').length,
                           pending: H.attr('{TOGGLE}', 'aria-checked'), saveDisabled: H.$('{SAVE}').disabled}};
            await H.click('{SAVE}');
            const asked = !!H.$('#sym-settings-confirm');
            const midPosts = H.posts('/api/symphony-settings').length;
            await H.click('#sym-confirm-go-live');
            return {{after, asked, midPosts}};
            """,
            route(GET_B, spec(symphonies[B]), method="GET"),
            route(
                r"/api/symphony-settings/",
                answers.csrf_refused(),
                {"json": {"status": "ok"}},
                method="POST",
                strict=True,
            ),
        )
        after = out.result["after"]
        assert after["posts"] == 1, "the refused save was sent again"
        assert NOT_SENT.search(after["surfaced"]) and "click" in after["surfaced"].lower()
        assert after["pending"] == "true", "the operator's live edit was lost"
        assert after["saveDisabled"] is False
        assert out.result["asked"] is True and out.result["midPosts"] == 1, (
            "the next Save must ask the go-live confirmation again before it sends"
        )
        first, second = out.posts("/api/symphony-settings")
        assert first["headers"]["x-csrf-token"] == DEFAULT_TOKEN
        assert second["headers"]["x-csrf-token"] == answers.TOKEN_B
        assert json.loads(second["body"])["confirm"] is True

    def test_a_restart_noted_while_a_save_is_in_flight_repeats_nothing(self, symphonies):
        out = run(
            OPEN_B
            + f"""
            await H.click('{SAVE}');
            PSRequest.noteDaemonStart('2026-10-22T09:00:00Z');
            PSRequest.noteDaemonStart('2026-10-22T10:43:30Z');
            await H.settle();
            const duringFlight = {{posts: H.posts('/api/symphony-settings').length, gets: H.to('/api/csrf-token').length}};
            await H.release('save-lands');
            return {{duringFlight, text: H.text('#sym-settings-modal')}};
            """,
            route(GET_B, spec(symphonies[B]), method="GET"),
            route(
                r"/api/symphony-settings/",
                {"json": {"status": "ok"}, "hold": "save-lands"},
                method="POST",
            ),
        )
        assert out.result["duringFlight"] == {"posts": 1, "gets": 2}
        assert "Couldn't save" not in out.result["text"], (
            "the save's own (good) answer was not rendered"
        )


class TestADashboardPollLeavesTheOpenModalAlone:
    """The real ``index.js`` and ``settings-modal.js`` together, in the whole-file harness."""

    def test_the_symphony_values_and_edits_survive_a_poll(self, symphonies):
        from tests.dashboard._index_js_harness import get_world, run_scenario

        static = Path(__file__).resolve().parents[2] / "static"
        scripts = tuple(
            f"static/{name}"
            for name in ("request_guard.js", "chrome.js", "index.js", "settings-modal.js")
            if (static / name).exists()
        )
        result = run_scenario(
            get_world("cards-open"),
            f"""
            await H.boot({{extras: true}});
            H.route('/api/symphony-settings/{B}', {{body: H.bodies.symB}});
            const doc = H.sandbox.document;
            H.sandbox.openSymphonySettings(null, '{B}');
            await H.settle();
            doc.getElementById('sym-settings-live-toggle').click();
            await H.settle();
            const view = () => ({{
              modal: doc.getElementById('sym-settings-modal').textContent,
              pending: doc.getElementById('sym-settings-live-toggle').getAttribute('aria-checked'),
            }});
            const before = view();
            await H.sandbox.loadState();
            await H.runTimers(30000);
            const after = view();
            doc.getElementById('sym-settings-save').click();
            await H.settle();
            return {{before, after, confirm: !!doc.getElementById('sym-settings-confirm'),
                    posts: H.fetchLog.filter((e) => e.method === 'POST').length}};
            """,
            extra_bodies={"symB": symphonies[B]},
            scripts=scripts,
        )["result"]
        assert result["after"] == result["before"], "a poll changed the open modal"
        assert NAME_B in result["after"]["modal"]
        assert result["after"]["pending"] == "true", "the operator's live edit was lost"
        assert result["confirm"] is True and result["posts"] == 0, (
            "the save after a poll must still ask the go-live confirmation first"
        )
