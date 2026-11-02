"""The Settings master section binds no control to empty data.

``static/settings.js`` (with the page's other scripts) runs unmodified over the page the real app
renders; the bodies are the real ``GET /api/settings`` route's (tests/ui/_settings_page_answers.py).

Before this the mode pill and the Execution start time were template literals (``DRY RUN`` and
``09:30``), so a failed load left the master control stating a mode the server never confirmed, and
typing in the time field marked the page dirty and enabled Save. On a LIVE daemon that state would
say DRY RUN beside the real-money switch.

Contract pinned
* First paint (the server render, before any answer): the mode pill is not a mode word and the time
  input is not editable (disabled, read-only, or inside a hidden section).
* After the load failed (the one retry also failed: a server error, a lost connection, or an answer
  that is not a settings body): no visible element of the section says DRY RUN or LIVE, the time input
  is not editable, and an attempted edit leaves Save and Discard disabled.
* After a good load: the pill names the mode the body carries (LIVE as well as DRY RUN, so a
  hard-coded word fails), the time input holds the body's time and is editable, and an edit enables
  Save.
* Retry after a failure ends in the same state as a first-try good load.
* The Live execution switch states no position once the load failed (hidden, or neutral: neither on nor
  off), whether the page never had a mode or lost the one a good load gave it, and cannot be operated;
  a later good load gives it back the position a first-try good load gives.
"""

from __future__ import annotations

import copy
import re

import pytest

from tests.ui import _settings_page_answers as page
from tests.ui._page_js_harness import render_page, route, run_page
from tests.ui._shell_support import (  # noqa: F401
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
)

SETTINGS = r"/api/settings$"
START = "#execution-start-time"
PILL = "#live-pill"
MODE_WORD = re.compile(r"\b(DRY RUN|LIVE)\b", re.I)
DOWN = {"status": 500, "text": "<html>boom</html>"}
LOST = {"reject": True}
NOT_SETTINGS = {"status": 200, "json": {"error": "no settings"}}

SNAPSHOT = f"""
const start = H.$('{START}');
const pill = H.$('{PILL}');
const editable = function () {{
  const el = H.$('{START}');
  return !!el && !el.hasAttribute('disabled') && !el.hasAttribute('readonly') && H.isVisible('{START}');
}};
const view = function () {{
  return {{
    pillText: H.isVisible('{PILL}') ? H.text('{PILL}').trim() : '',
    startValue: H.value('{START}'),
    editable: editable(),
    saveDisabled: H.$('#btn-save').disabled,
    discardDisabled: H.$('#btn-discard').disabled,
  }};
}};
const tryTyping = async function () {{
  if (editable()) await H.set('{START}', '10:15');
  return view();
}};
"""


@pytest.fixture
def seeded():
    return page.seed_state(flags=True)


def _run(scenario: str, *routes_):
    return run_page(
        render_page("/settings"),
        "await H.boot();\n" + SNAPSHOT + scenario,
        routes=list(routes_),
        pathname="/settings",
    )


def _body_with_live(live: bool) -> dict:
    good = page.settings_get_answer()
    out = copy.deepcopy(good)
    out["json"]["globals"]["LIVE_EXECUTION"] = "True" if live else "False"
    return out


def _server_render_of_the_master_section() -> dict:
    html = render_page("/settings")
    section = re.search(r'<section id="sec-master".*?</section>', html, flags=re.S)
    assert section, "the settings render has no #sec-master section"
    block = section.group(0)
    pill = re.search(r'<span[^>]*id="live-pill"[^>]*>(.*?)</span>', block, flags=re.S)
    field = re.search(r'<input[^>]*id="execution-start-time"[^>]*>', block)
    assert pill and field, "the master section lost its pill or its time input"
    return {
        "section_tag": re.search(r"<section[^>]*>", block).group(0),
        "pill_tag": re.search(r'<span[^>]*id="live-pill"[^>]*>', block).group(0),
        "pill_text": re.sub(r"<[^>]+>", "", pill.group(1)).strip(),
        "input_tag": field.group(0),
    }


class TestFirstPaint:
    def test_the_server_render_states_no_mode_before_any_answer(self):
        render = _server_render_of_the_master_section()
        hidden = " hidden" in render["section_tag"] or " hidden" in render["pill_tag"]
        assert hidden or not MODE_WORD.search(render["pill_text"]), (
            f"the first paint states a mode the server never confirmed: {render['pill_text']!r}"
        )

    def test_the_server_render_offers_no_editable_time_before_any_answer(self):
        render = _server_render_of_the_master_section()
        inert = (
            " disabled" in render["input_tag"]
            or " readonly" in render["input_tag"]
            or " hidden" in render["section_tag"]
        )
        assert inert, f"the first paint offers an editable time field: {render['input_tag']}"


FAILURES = [DOWN, LOST, NOT_SETTINGS]
FAILURE_IDS = ["server error", "connection lost", "answer that is not settings"]


class TestAfterTheLoadFailed:
    @pytest.mark.parametrize("failure", FAILURES, ids=FAILURE_IDS)
    def test_no_mode_word_is_shown_and_the_time_cannot_be_edited(self, seeded, failure):
        out = _run(
            """
            await H.advance(500);
            const failed = { errorVisible: H.isVisible('#settings-load-error'), view: view() };
            return { failed: failed, afterTyping: await tryTyping() };
            """,
            route(SETTINGS, failure, failure, method="GET"),
        )
        r = out.result
        assert r["failed"]["errorVisible"], "precondition: the failure is on screen"
        view = r["failed"]["view"]
        assert not MODE_WORD.search(view["pillText"]), (
            f"after a failed load the master control says {view['pillText']!r}"
        )
        assert not view["editable"], "after a failed load the Execution start time is editable"

    @pytest.mark.parametrize("failure", FAILURES, ids=FAILURE_IDS)
    def test_an_attempted_edit_cannot_mark_the_page_dirty_or_enable_save(self, seeded, failure):
        out = _run(
            """
            await H.advance(500);
            return await tryTyping();
            """,
            route(SETTINGS, failure, failure, method="GET"),
        )
        after = out.result
        assert after["saveDisabled"] and after["discardDisabled"], (
            f"an edit after a failed load reached Save: {after}"
        )


class TestAfterAGoodLoad:
    @pytest.mark.parametrize("live", [False, True], ids=["dry run", "live"])
    def test_the_pill_names_the_mode_the_body_carries(self, seeded, live):
        body = _body_with_live(live)
        out = _run("return view();", route(SETTINGS, body, method="GET"))
        want = "LIVE" if live else "DRY RUN"
        assert out.result["pillText"].upper() == want, out.result

    def test_the_time_input_holds_the_bodys_time_and_an_edit_enables_save(self, seeded):
        body = page.settings_get_answer()
        want = body["json"]["globals"]["EXECUTION_START_TIME"]
        out = _run(
            """
            const loaded = view();
            return { loaded: loaded, afterTyping: await tryTyping() };
            """,
            route(SETTINGS, body, method="GET"),
        )
        assert out.result["loaded"]["startValue"] == want
        assert out.result["loaded"]["editable"], "the loaded time field is not editable"
        assert out.result["afterTyping"]["saveDisabled"] is False, "an edit did not enable Save"


class TestRetry:
    def test_retry_after_a_failure_ends_as_a_first_try_load_does(self, seeded):
        good = _body_with_live(True)
        first_try = _run("return await tryTyping();", route(SETTINGS, good, method="GET"))
        retried = _run(
            """
            await H.advance(500);
            const retry = H.$$('#settings-load-error button').find((b) => /Retry/.test(b.textContent));
            if (!retry) throw new Error('there is no Retry button');
            retry.click();
            await H.settle();
            return await tryTyping();
            """,
            route(SETTINGS, DOWN, DOWN, good, method="GET"),
        )
        assert retried.result == first_try.result
        assert retried.result["pillText"].upper() == "LIVE"
        assert retried.result["saveDisabled"] is False


SAVE_ROUTE = r"/api/settings$"
PARAM = ".param-input"

RELOAD_SNAPSHOT = f"""
const postsToSave = function () {{
  return H.posts('{SAVE_ROUTE}').map(function (r) {{ return JSON.parse(r.body); }});
}};
const failedReload = async function () {{
  const retry = H.$('#btn-settings-retry');
  if (!retry) throw new Error('there is no Retry button');
  await H.click('#btn-settings-retry');
  await H.advance(500);
}};
"""


def _run_with_save(scenario: str, *routes_):
    return _run(
        RELOAD_SNAPSHOT + scenario,
        *routes_,
        route(SAVE_ROUTE, page.settings_save_answer(), method="POST"),
    )


class TestAfterAFailedReload:
    def test_a_discarded_start_time_edit_cannot_be_saved_after_the_reads_fail(self, seeded):
        good = page.settings_get_answer()
        out = _run_with_save(
            """
            await H.advance(500);
            await H.set('#execution-start-time', '10:00');
            const edited = view();
            await H.click('#btn-discard');
            await H.advance(500);
            const failed = { errorVisible: H.isVisible('#settings-load-error'), view: view() };
            await H.click('#btn-save');
            return { edited: edited, failed: failed, posts: postsToSave() };
            """,
            route(SETTINGS, good, DOWN, DOWN, method="GET"),
        )
        r = out.result
        assert r["edited"]["saveDisabled"] is False, "precondition: the edit enabled Save"
        assert r["failed"]["errorVisible"], "precondition: the reload failed on screen"
        assert r["failed"]["view"]["saveDisabled"], (
            "Save stays enabled after the reload that dropped the edit failed"
        )
        assert r["failed"]["view"]["discardDisabled"]
        assert r["posts"] == [], f"a click on Save after a failed reload posted {r['posts']}"

    @pytest.mark.parametrize("failure", FAILURES, ids=FAILURE_IDS)
    def test_editing_a_rendered_input_after_a_failed_reload_does_not_enable_save_or_queue_a_post(
        self, seeded, failure
    ):
        good = page.settings_get_answer()
        out = _run_with_save(
            """
            await H.advance(500);
            const rendered = H.$$('__PARAM__').length;
            await failedReload();
            const failed = { errorVisible: H.isVisible('#settings-load-error'), view: view() };
            await H.set('__PARAM__', '7');
            const edited = view();
            await H.click('#btn-save');
            return { rendered: rendered, failed: failed, edited: edited, posts: postsToSave() };
            """.replace("__PARAM__", PARAM),
            route(SETTINGS, good, failure, failure, method="GET"),
        )
        r = out.result
        assert r["rendered"] > 0, "precondition: the page rendered algorithm inputs"
        assert r["failed"]["errorVisible"], "precondition: the reload failed on screen"
        assert r["edited"]["saveDisabled"] and r["edited"]["discardDisabled"], (
            f"an edit of a rendered input after a failed reload reached Save: {r['edited']}"
        )
        assert r["posts"] == [], f"Save posted {r['posts']} after a failed reload"

    def test_a_later_good_load_restores_normal_saving_without_the_dropped_edits(self, seeded):
        good = page.settings_get_answer()
        out = _run_with_save(
            """
            await H.advance(500);
            const key = H.$('__PARAM__').dataset.key;
            await H.set('#execution-start-time', '10:00');
            await H.click('#btn-discard');
            await H.advance(500);
            const failed = view();
            await H.click('#btn-settings-retry');
            await H.advance(500);
            const reloaded = view();
            await H.set('__PARAM__', '7');
            const edited = view();
            await H.click('#btn-save');
            return { key: key, failed: failed, reloaded: reloaded, edited: edited, posts: postsToSave() };
            """.replace("__PARAM__", PARAM),
            route(SETTINGS, good, DOWN, DOWN, good, method="GET"),
        )
        r = out.result
        assert r["failed"]["saveDisabled"], "Save was enabled while the reads were failing"
        assert r["reloaded"]["saveDisabled"] and r["reloaded"]["editable"]
        assert r["edited"]["saveDisabled"] is False, "a good load did not restore saving"
        assert len(r["posts"]) == 1, r["posts"]
        sent = r["posts"][0]["globals"]
        assert sent.get(r["key"]) == "7"
        assert "EXECUTION_START_TIME" not in sent, f"the discarded start time was posted: {sent}"


# ---------------------------------------------------------------------------
# The Live execution switch states no position while the mode is unknown
# ---------------------------------------------------------------------------

TOGGLE = "#live-toggle"
TOGGLE_VIEW = f"""
const toggleView = function () {{
  const el = H.$('{TOGGLE}');
  const style = el.style || {{}};
  return {{
    hiddenFromView: !H.isVisible('{TOGGLE}') || style.visibility === 'hidden'
      || el.getAttribute('aria-hidden') === 'true' || el.hasAttribute('inert'),
    dataOn: el.getAttribute('data-on'),
    ariaChecked: el.getAttribute('aria-checked'),
    operable: !(el.hasAttribute('disabled') || el.disabled === true
      || el.getAttribute('aria-disabled') === 'true' || el.hasAttribute('inert')),
  }};
}};
"""


def _shows_a_position(view: dict) -> bool:
    """On or off is stated: the switch is in view and carries a boolean position."""
    if view["hiddenFromView"]:
        return False
    return view["dataOn"] in ("0", "1") or view["ariaChecked"] in ("true", "false")


def _run_toggle(scenario: str, *routes_):
    return _run(RELOAD_SNAPSHOT + TOGGLE_VIEW + scenario, *routes_).result


class TestTheLiveSwitchAfterAFailedLoad:
    """A failed read leaves the page not knowing the mode; a switch resting at off would say DRY RUN
    beside a daemon that may be live. Hiding it, or a neutral state that is neither on nor off, both
    pass; dimming it while it stays in the off position does not."""

    @pytest.mark.parametrize("failure", FAILURES, ids=FAILURE_IDS)
    def test_a_load_that_never_succeeded_leaves_the_switch_stating_no_position(
        self, seeded, failure
    ):
        r = _run_toggle(
            """
            await H.advance(500);
            return { errorVisible: H.isVisible('#settings-load-error'), toggle: toggleView() };
            """,
            route(SETTINGS, failure, failure, method="GET"),
        )
        assert r["errorVisible"], "precondition: the failure is on screen"
        assert not _shows_a_position(r["toggle"]), (
            f"the failed page still shows a position: {r['toggle']}"
        )
        assert not r["toggle"]["operable"], f"the switch can be operated: {r['toggle']}"

    @pytest.mark.parametrize("failure", FAILURES, ids=FAILURE_IDS)
    @pytest.mark.parametrize("live", [True, False], ids=["was live", "was dry run"])
    def test_a_failed_reload_takes_the_position_a_good_load_gave_it_away(
        self, seeded, failure, live
    ):
        r = _run_toggle(
            """
            await H.advance(500);
            const loaded = toggleView();
            await failedReload();
            return { errorVisible: H.isVisible('#settings-load-error'), loaded: loaded,
                     failed: toggleView() };
            """,
            route(SETTINGS, _body_with_live(live), failure, failure, method="GET"),
        )
        assert _shows_a_position(r["loaded"]), (
            f"precondition: the good load states a mode: {r['loaded']}"
        )
        assert r["errorVisible"], "precondition: the reload failed on screen"
        assert not _shows_a_position(r["failed"]), (
            f"after the reload failed the switch still states a position: {r['failed']}"
        )
        assert not r["failed"]["operable"], f"the switch can be operated: {r['failed']}"


class TestTheLiveSwitchAfterALaterGoodLoad:
    @pytest.mark.parametrize("live", [True, False], ids=["live", "dry run"])
    def test_a_later_good_load_restores_the_position_a_first_try_load_gives(self, seeded, live):
        good = _body_with_live(live)
        first_try = _run_toggle("return toggleView();", route(SETTINGS, good, method="GET"))
        restored = _run_toggle(
            """
            await H.advance(500);
            const failed = toggleView();
            await H.click('#btn-settings-retry');
            await H.advance(500);
            return { failed: failed, restored: toggleView() };
            """,
            route(SETTINGS, DOWN, DOWN, good, method="GET"),
        )
        assert _shows_a_position(first_try), (
            f"precondition: a first-try load states a mode: {first_try}"
        )
        assert not _shows_a_position(restored["failed"]), (
            f"precondition: the failed state states no position: {restored['failed']}"
        )
        assert restored["restored"] == first_try, (
            f"a later good load did not restore the switch: {restored['restored']} != {first_try}"
        )
        assert restored["restored"]["dataOn"] == ("1" if live else "0")
        assert restored["restored"]["ariaChecked"] == ("true" if live else "false")


class TestTheLiveSwitchAfterAGoodFirstLoad:
    @pytest.mark.parametrize("live", [True, False], ids=["live", "dry run"])
    def test_the_switch_states_the_bodys_mode_and_stays_read_only(self, seeded, live):
        view = _run_toggle(
            "return toggleView();", route(SETTINGS, _body_with_live(live), method="GET")
        )
        assert _shows_a_position(view), view
        assert view["dataOn"] == ("1" if live else "0")
        assert view["ariaChecked"] == ("true" if live else "false")
        assert not view["operable"], f"the switch became operable: {view}"
