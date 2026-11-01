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
