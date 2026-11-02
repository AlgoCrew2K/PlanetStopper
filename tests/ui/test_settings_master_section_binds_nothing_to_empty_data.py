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
* In a real browser (the stub DOM above cannot see a class rule), after a failed load the switch has no
  box on screen at desktop and phone width in the light and dark theme, and the pill shows the dash; a
  later good load shows it again at the server's position. Every element the page's script hides through
  the ``hidden`` property has no box on screen, whatever its class rule sets: after a failed load,
  after a good load on each section, and in each state of the flush confirm panel.
"""

from __future__ import annotations

import copy
import json
import re

import pytest

from tests.ui import _settings_page_answers as page
from tests.ui._page_js_harness import render_page, route, run_page
from tests.ui._shell_support import (  # noqa: F401
    NARROW,
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_page,
    open_probe,
    wait_until,
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


# ---------------------------------------------------------------------------
# A real browser: an element the script hides leaves the screen
# ---------------------------------------------------------------------------
#
# The stub DOM above reads the ``hidden`` property and the inline display, never a class rule, so it
# passes while a rule such as ``.toggle-btn { display: flex }`` keeps a "hidden" element on screen.
# Only a browser lays the page out. Browser-only (marker ``browser``, deselected by default; run with
# ``python -m pytest -m browser -n 0 -p no:cacheprovider <this file>``). Requests the page sends to
# ``/api/settings`` are answered in the browser; nothing is written.

REMOVED_FROM_LAYOUT = 0
DASH = re.compile(r"^(—|–|-|--)$")

# Records every element the page's script sets ``hidden`` on, so the sweep covers what the script
# hides without a list kept beside the script.
_RECORD_SCRIPTED_HIDES = """
(() => {
  const own = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'hidden');
  window.__scriptedHides = new Set();
  Object.defineProperty(HTMLElement.prototype, 'hidden', {
    configurable: true,
    enumerable: own.enumerable,
    get() { return own.get.call(this); },
    set(value) { if (value) window.__scriptedHides.add(this); own.set.call(this, value); },
  });
})();
"""

_SCRIPTED_HIDES_STILL_ON_SCREEN = """
() => {
  const hiding = [...window.__scriptedHides].filter((e) => e.isConnected && e.hasAttribute('hidden'));
  return {
    hiding: hiding.length,
    onScreen: hiding
      .filter((e) => e.getClientRects().length > 0)
      .map((e) => e.id || e.tagName + '.' + e.className),
  };
}
"""

_LOAD_ERROR_SHOWN = "() => document.getElementById('settings-load-error').hidden === false"

_SWITCH_FACTS = """
() => {
  const toggle = document.getElementById('live-toggle');
  return {
    boxes: toggle.getClientRects().length,
    ariaChecked: toggle.getAttribute('aria-checked'),
    dataOn: toggle.getAttribute('data-on'),
    pill: document.getElementById('live-pill').textContent.trim(),
    bannerBoxes: document.getElementById('live-banner').getClientRects().length,
  };
}
"""


def _settings_answers(mode: dict):
    """The browser's answer to ``GET /api/settings``: a server error while ``mode["fail"]``, else the
    real route's body with ``LIVE_EXECUTION`` set to ``mode["live"]`` when that is given."""

    def handler(route_):
        if route_.request.method != "GET":
            route_.abort()
            return
        if mode["fail"]:
            route_.fulfill(status=500, content_type="text/html", body="<html>boom</html>")
            return
        body = route_.fetch().json()
        if mode.get("live") is not None:
            body["globals"]["LIVE_EXECUTION"] = "True" if mode["live"] else "False"
        route_.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    return handler


def _open_settings_with_the_load_failing(probe) -> dict:
    mode = {"fail": True, "live": None}
    probe.context.add_init_script(_RECORD_SCRIPTED_HIDES)
    probe.page.route("**/api/settings", _settings_answers(mode))
    probe.page.goto(probe.server.base_url + "/settings")
    wait_until(probe, _LOAD_ERROR_SHOWN, "the settings page never said the load failed")
    return mode


def _assert_nothing_hidden_by_the_script_is_on_screen(probe, step: str) -> None:
    facts = probe.page.evaluate(_SCRIPTED_HIDES_STILL_ON_SCREEN)
    assert facts["hiding"] > 0, f"{step}: the script hid nothing, so this check proved nothing"
    assert facts["onScreen"] == [], (
        f"{step}: elements the script hid still have a box on screen: {facts['onScreen']}"
    )


@pytest.mark.browser
@pytest.mark.parametrize("theme", [None, "dark"], ids=["light", "dark"])
@pytest.mark.parametrize("width, height", [WIDE, NARROW], ids=["1440", "375"])
def test_after_a_failed_load_the_live_switch_is_not_on_screen(
    firefox, advisor_server, width, height, theme
):
    with open_probe(firefox, advisor_server, width=width, height=height, theme=theme) as probe:
        _open_settings_with_the_load_failing(probe)
        facts = probe.page.evaluate(_SWITCH_FACTS)
        assert facts["boxes"] == REMOVED_FROM_LAYOUT, (
            "the Live execution switch still has a box on screen after the load failed "
            f"(it rests at data-on={facts['dataOn']!r}, beside the pill {facts['pill']!r})"
        )
        assert not probe.page.locator("#live-toggle").is_visible()
        assert DASH.match(facts["pill"]), f"the pill shows {facts['pill']!r}, not the dash"
        assert facts["bannerBoxes"] == REMOVED_FROM_LAYOUT


@pytest.mark.browser
@pytest.mark.parametrize("live", [False, True], ids=["dry run", "live"])
def test_a_later_good_load_shows_the_live_switch_again_at_the_servers_position(
    firefox, advisor_server, live
):
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        mode = _open_settings_with_the_load_failing(probe)
        mode.update(fail=False, live=live)
        probe.page.click("#btn-settings-retry")
        wait_until(
            probe,
            "() => document.getElementById('settings-load-error').hidden === true"
            " && document.getElementById('live-toggle').getClientRects().length > 0",
            "a good load after a failed one never showed the Live execution switch again",
        )
        facts = probe.page.evaluate(_SWITCH_FACTS)
        assert facts["ariaChecked"] == str(live).lower()
        assert facts["dataOn"] == ("1" if live else "0")
        assert facts["pill"] == ("LIVE" if live else "DRY RUN")
        assert (facts["bannerBoxes"] > 0) is live


@pytest.mark.browser
@pytest.mark.parametrize("width, height", [WIDE, NARROW], ids=["1440", "375"])
def test_after_a_failed_load_everything_the_script_hid_is_off_screen(
    firefox, advisor_server, width, height
):
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        _open_settings_with_the_load_failing(probe)
        _assert_nothing_hidden_by_the_script_is_on_screen(probe, "after the failed load")


@pytest.mark.browser
@pytest.mark.parametrize("width, height", [WIDE, NARROW], ids=["1440", "375"])
def test_after_a_good_load_every_section_the_script_hid_is_off_screen(
    firefox, advisor_server, width, height
):
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        probe.context.add_init_script(_RECORD_SCRIPTED_HIDES)
        open_page(probe, "/settings")
        _assert_nothing_hidden_by_the_script_is_on_screen(probe, "after the good load")
        for section in ("algorithm", "credentials", "symphonies", "maintenance", "master"):
            probe.page.click(f'.sn-btn[data-section="{section}"]')
            probe.page.wait_for_function(
                "(id) => document.getElementById('sec-' + id).hidden === false", arg=section
            )
            _assert_nothing_hidden_by_the_script_is_on_screen(probe, f"on the {section} section")


@pytest.mark.browser
@pytest.mark.parametrize("width, height", [WIDE, NARROW], ids=["1440", "375"])
def test_the_flush_confirm_panel_leaves_the_screen_in_every_state_the_script_hides_it(
    firefox, advisor_server, width, height
):
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        probe.context.add_init_script(_RECORD_SCRIPTED_HIDES)
        open_page(probe, "/settings")
        probe.page.click('.sn-btn[data-section="maintenance"]')
        probe.page.wait_for_function(
            "() => document.getElementById('sec-maintenance').hidden === false"
        )
        # The trigger's own handler sets the loading state before its request is answered; measure
        # in the same task so the answer cannot replace that state first.
        loading = probe.page.evaluate(
            """
            () => {
              document.getElementById('btn-flush-trigger').click();
              const hiding = [...window.__scriptedHides]
                .filter((e) => e.isConnected && e.hasAttribute('hidden'));
              return hiding.filter((e) => e.getClientRects().length > 0)
                .map((e) => e.id || e.tagName + '.' + e.className);
            }
            """
        )
        assert loading == [], (
            f"while the armed list loads, hidden elements are on screen: {loading}"
        )
        wait_until(
            probe,
            "() => document.getElementById('flush-confirm-body').hidden === false",
            "the confirm panel never showed its body",
        )
        _assert_nothing_hidden_by_the_script_is_on_screen(probe, "with the confirm body shown")
        probe.page.click("#btn-flush-cancel")
        probe.page.wait_for_function(
            "() => document.getElementById('flush-confirm-panel').hidden === true"
        )
        _assert_nothing_hidden_by_the_script_is_on_screen(probe, "after cancelling the flush")
