"""Settings and the settings modal in a real browser: what an operator can see, open and type.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_page_explainers_settings_browser.py``).
Firefox headless through Playwright, imported inside a fixture; a missing Playwright or Firefox
fails with a message naming it, never a skip.

Every expected text is a row of ``tests/fixtures/page_explainers/copy.json``
(``tests/ui/_page_copy.py``). The server is the app in this process on the per-test database,
seeded by the real writers (``tests/ui/_page_explainers.py`` ``seed_world("dashboard_full")``, the
captured production ``bot_state``); the developer ``.env`` is never read (the credential-free
environment points ``app.ENV_FILE_PATH`` at an empty temporary file). Outbound requests are refused.
The one edit of a real answer is a named-field one: ``symphonies_no_longer_held`` is added to
``GET /api/settings`` in the browser, in the shape ``app.get_settings`` returns, so the "No longer
held" line has a symphony to describe.

* S-1.5, S-2.5: each side-nav section shows its description, visible and exact (this plan's Copy
  table A); the page's one Glossary stays visible whichever section is chosen, opens by a click on
  its summary and shows each line's exact text.
* S-1.6, S-2.5, S-3.14, S-4.13, S-4.14, S-7.12: the same for the settings modal opened on a
  symphony: its descriptions, its one Glossary, a pending live toggle with the Glossary opened and
  closed, and the error state with no Glossary.
* S-1.17: at 375 by 812 every description shows in full, and the page does not scroll sideways.
* S-1.22, S-2.16, S-5.12: an unsaved edit survives a section switch; a pending live toggle in the
  modal keeps its descriptions, and Save with it asks for confirmation and sends nothing first.
* S-3.16: typing into a credential field shows the new status and no text says it will be saved.
* the Settings document is no wider than the viewport at 1440 and at 1240.
"""

from __future__ import annotations

import re
from contextlib import contextmanager

import pytest

import database
from tests._ux_truth_support import load_captured_bot_state
from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _page_copy as copy
from tests.ui import _page_explainers as harness
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    NARROW,
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    assert_not_clipped_by_its_containers,
    firefox,
    open_page,
    open_probe,
    wait_until,
)

pytestmark = pytest.mark.browser

SUMMARY_TEXT = "Glossary"
GONE_NAME = "Review Gone Symphony"
GONE_ID = "review-gone-symphony"
TYPED_PLACEHOLDER = "typed-placeholder-not-a-credential"
MODAL_SAVE = "#sym-settings-save"
MODAL_TOGGLE = "#sym-settings-live-toggle"

SECTIONS = ("master", "algorithm", "credentials", "symphonies", "maintenance")
SECTION_ROWS = {
    "master": ["S03"],
    "algorithm": ["S08"],
    "credentials": ["S11"],
    "symphonies": ["S14"],
    "maintenance": ["S17"],
}

FACTS_JS = r"""
(selector) => [...document.querySelectorAll(selector)].map((el) => {
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  let closed = false;
  for (let a = el.parentElement; a; a = a.parentElement) {
    if (a.tagName === 'DETAILS' && !a.open) closed = true;
  }
  return {
    key: el.getAttribute('data-explainer') || el.getAttribute('data-term') ||
         el.getAttribute('data-terms') || '',
    text: el.textContent.replace(/\s+/g, ' ').trim(),
    shown: el.getClientRects().length > 0 && r.width > 0 && r.height > 0 &&
           cs.visibility !== 'hidden' && !el.closest('[hidden]'),
    closed,
    open: el.tagName === 'DETAILS' ? el.open : null,
    textOverflow: cs.textOverflow,
    clamp: cs.webkitLineClamp,
    scrollW: el.scrollWidth, clientW: el.clientWidth,
    scrollH: el.scrollHeight, clientH: el.clientHeight,
    left: r.left, right: r.right, viewport: innerWidth,
  };
})
"""


def _norm(text: str) -> str:
    return " ".join(text.split())


def _facts(probe, selector: str) -> list[dict]:
    return probe.page.evaluate(FACTS_JS, selector)


def _only(probe, selector: str) -> dict:
    found = _facts(probe, selector)
    assert len(found) == 1, f"{len(found)} elements match {selector}"
    return found[0]


@pytest.fixture
def world(tmp_path, monkeypatch):
    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )
    harness.seed_world("dashboard_full", tmp_path, monkeypatch.setattr)


def _add_no_longer_held(route) -> None:
    """GET /api/settings as the app answers it, plus one no-longer-held entry."""
    if route.request.method != "GET":
        route.continue_()
        return
    response = route.fetch()
    body = response.json()
    body["symphonies_no_longer_held"] = [{"id": GONE_ID, "name": GONE_NAME}]
    route.fulfill(response=response, json=body)


@contextmanager
def settings_open(firefox, server, *, size=WIDE):  # noqa: F811
    with open_probe(firefox, server, width=size[0], height=size[1]) as probe:
        probe.page.route("**/api/settings", _add_no_longer_held)
        record_writes(probe, server)
        open_page(probe, "/settings")
        probe.page.wait_for_selector("#sym-list .sym-list-btn", state="attached")
        yield probe
        assert probe.page_errors == [], probe.page_errors


def record_writes(probe, server) -> None:
    """``probe.posts``: every request the page sends to the server that is not a GET."""
    probe.posts = []

    def on_request(request) -> None:
        if request.method != "GET" and request.url.startswith(server.base_url):
            probe.posts.append((request.method, request.url, request.post_data))

    probe.page.on("request", on_request)


def go_to(probe, section: str) -> None:
    probe.page.click(f'.sn-btn[data-section="{section}"]')
    probe.page.wait_for_selector(f"#sec-{section}", state="visible")


def _assert_row_visible(probe, key: str) -> None:
    found = _only(probe, f'[data-explainer="{key}"]')
    assert found["shown"], f"{key}: not visible"
    assert found["closed"] is False, f"{key}: inside a closed disclosure"
    assert found["text"] == _norm(copy.a_text(key)), f"{key}: shows {found['text']!r}"


GLOSSARY = 'details[data-terms="glossary"][data-glossary-host="Settings"]'


def _open_and_read_glossary(probe) -> None:
    details = _only(probe, GLOSSARY)
    assert details["open"] is False, "the Glossary is not closed on load"
    assert details["shown"], "the Glossary is hidden"
    assert _only(probe, f"{GLOSSARY} > summary")["text"] == SUMMARY_TEXT
    probe.page.click(f"{GLOSSARY} > summary")
    assert _only(probe, GLOSSARY)["open"] is True
    got = _facts(probe, f"{GLOSSARY} > p[data-term]")
    want = copy.glossary("Settings")
    assert [g["key"] for g in got] == [x["key"] for x in want]
    for line, expected in zip(got, want, strict=True):
        assert line["shown"], f"{line['key']}: not visible once the Glossary is open"
        assert line["text"] == _norm(expected["text"]), line["key"]
    probe.page.click(f"{GLOSSARY} > summary")
    assert _only(probe, GLOSSARY)["open"] is False


# ---------------------------------------------------------------------------
# S-1.5, S-2.5: every section
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("section", SECTIONS)
def test_each_section_shows_its_description_and_the_one_glossary_stays_visible_and_opens(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,
    section,
):
    with settings_open(firefox, advisor_server) as probe:
        go_to(probe, section)
        _assert_row_visible(probe, "S01")
        for key in SECTION_ROWS[section]:
            _assert_row_visible(probe, key)
        assert _facts(probe, "[data-terms]:not([data-terms='glossary'])") == []
        _open_and_read_glossary(probe)


def test_only_the_chosen_section_shows_its_description(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,
):
    with settings_open(firefox, advisor_server) as probe:
        for section in SECTIONS:
            go_to(probe, section)
            shown = {
                key
                for keys in SECTION_ROWS.values()
                for key in keys
                if _only(probe, f'[data-explainer="{key}"]')["shown"]
            }
            assert shown == set(SECTION_ROWS[section]), f"{section}: shows {sorted(shown)}"


# ---------------------------------------------------------------------------
# S-1.17: at 375 nothing is cut off
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("section", SECTIONS)
def test_at_375_every_description_shows_in_full_and_the_page_does_not_scroll_sideways(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,
    section,
):
    with settings_open(firefox, advisor_server, size=NARROW) as probe:
        go_to(probe, section)
        keys = ["S01", *SECTION_ROWS[section]]
        probe.page.click(f"{GLOSSARY} > summary")
        keys += [x["key"] for x in copy.glossary("Settings")]
        for key in keys:
            selector = f'[data-explainer="{key}"], [data-term="{key}"]'
            found = _only(probe, selector)
            assert found["shown"], f"{key}: not visible at 375"
            expected = (
                copy.a_text(key)
                if key in ("S01", *SECTION_ROWS[section])
                else next(x["text"] for x in copy.glossary("Settings") if x["key"] == key)
            )
            assert found["text"] == _norm(expected)
            assert found["textOverflow"] != "ellipsis", f"{key}: ellipsed"
            assert found["clamp"] in ("none", ""), f"{key}: line-clamped"
            assert found["scrollW"] <= found["clientW"] + 1, f"{key}: cut off sideways"
            assert found["scrollH"] <= found["clientH"] + 1, f"{key}: cut off at the bottom"
            assert found["left"] >= -0.5 and found["right"] <= found["viewport"] + 0.5, key
            assert_not_clipped_by_its_containers(probe.page.locator(selector), key)
        width, viewport = probe.page.evaluate(
            "() => [document.documentElement.scrollWidth, innerWidth]"
        )
        assert width <= viewport, f"the page scrolls sideways ({width} > {viewport})"


# ---------------------------------------------------------------------------
# S-1.22: an unsaved edit and the descriptions
# ---------------------------------------------------------------------------


def test_an_unsaved_edit_survives_a_section_switch_with_the_descriptions_unchanged(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,
):
    with settings_open(firefox, advisor_server) as probe:
        before = {k: _only(probe, f'[data-explainer="{k}"]')["text"] for k in ("S01", "S03")}
        probe.page.fill("#execution-start-time", "09:35")
        assert probe.page.is_enabled("#btn-save")
        for section in ("algorithm", "credentials", "symphonies", "maintenance", "master"):
            go_to(probe, section)
        assert probe.page.input_value("#execution-start-time") == "09:35", "the edit was dropped"
        assert probe.page.is_enabled("#btn-save") and probe.page.is_enabled("#btn-discard")
        after = {k: _only(probe, f'[data-explainer="{k}"]')["text"] for k in ("S01", "S03")}
        assert after == before
        assert probe.posts == [], "switching section must send nothing"


# ---------------------------------------------------------------------------
# S-3.16: typing a credential
# ---------------------------------------------------------------------------

SAVE_WORDS = re.compile(r"(writes?|written|saved?|saving)[^.]{0,80}\.env", re.I)


def test_typing_a_credential_shows_the_new_status_and_nothing_promises_a_save(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,
):
    with settings_open(firefox, advisor_server) as probe:
        go_to(probe, "credentials")
        _assert_row_visible(probe, "S11")
        warning = _only(probe, ".cred-warn")
        assert warning["shown"] and warning["text"] == _norm(copy.text("S12r", "C"))
        probe.page.fill('.cred-input[data-key="ALPACA_KEY"]', TYPED_PLACEHOLDER)
        status = probe.page.locator(
            '.cred-input[data-key="ALPACA_KEY"] >> xpath=ancestor::*[contains(@class,"cred-field-wrap")]'
            " >> .cred-status"
        )
        assert status.is_visible()
        assert _norm(status.inner_text()) == copy.text("S12s", "C")
        body = _norm(probe.page.inner_text('[data-testid="settings-page"]'))
        assert copy.row("S12s", "C")["old_text"] not in body
        for key in ("S11", "S12r", "S12s", "S13a", "S13b", "S13c"):
            body = body.replace(_norm(copy.text(key)), " ")
        assert not SAVE_WORDS.search(body), SAVE_WORDS.search(body).group(0)
        assert probe.posts == [], "typing is not saving"


# ---------------------------------------------------------------------------
# The Settings document fits the viewport
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [WIDE, (1240, 900)], ids=["1440", "1240"])
def test_the_settings_document_is_no_wider_than_the_viewport(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,
    size,
):
    with settings_open(firefox, advisor_server, size=size) as probe:
        go_to(probe, "master")
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        width, viewport = probe.page.evaluate(
            "() => [document.documentElement.scrollWidth, innerWidth]"
        )
        assert width <= viewport, f"the document is wider than the viewport ({width} > {viewport})"


# ---------------------------------------------------------------------------
# The settings modal (S-1.6, S-2.5, S-1.22, S-2.16, S-5.12)
# ---------------------------------------------------------------------------


def _first_symphony_id() -> str:
    return next(
        key
        for key, record in load_captured_bot_state().items()
        if isinstance(record, dict) and "name" in record
    )


@contextmanager
def modal_open(firefox, server):  # noqa: F811
    with open_probe(firefox, server, width=WIDE[0], height=WIDE[1]) as probe:
        record_writes(probe, server)
        open_page(probe, "/")
        probe.page.evaluate("(id) => window.openSymphonySettings(null, id)", _first_symphony_id())
        wait_until(
            probe,
            "() => !!document.getElementById('sym-settings-save')",
            "the modal never finished loading",
        )
        yield probe
        assert probe.page_errors == [], probe.page_errors


MODAL_GLOSSARY = '#sym-settings-modal details[data-terms="glossary"]'


def _assert_modal_rows_visible(probe) -> None:
    for row in copy.a_rows("settings modal"):
        found = _only(probe, f'#sym-settings-modal [data-explainer="{row["key"]}"]')
        assert found["shown"], f"{row['key']}: not visible in the modal"
        assert found["text"] == _norm(copy.a_text(row["key"])), row["key"]


def test_the_modal_shows_each_description_and_opens_its_one_glossary(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,
):
    with modal_open(firefox, advisor_server) as probe:
        _assert_modal_rows_visible(probe)
        assert _facts(probe, "#sym-settings-modal [data-terms]:not([data-terms='glossary'])") == []
        assert _only(probe, MODAL_GLOSSARY)["open"] is False
        assert _only(probe, MODAL_GLOSSARY + " > summary")["text"] == "Glossary"
        probe.page.click(MODAL_GLOSSARY + " > summary")
        assert _only(probe, MODAL_GLOSSARY)["open"] is True
        got = _facts(probe, MODAL_GLOSSARY + " > p[data-term]")
        assert [g["key"] for g in got] == [x["key"] for x in copy.glossary("settings modal")]
        for line, expected in zip(got, copy.glossary("settings modal"), strict=True):
            assert line["shown"] and line["text"] == _norm(expected["text"]), line["key"]
        probe.page.click(MODAL_GLOSSARY + " > summary")
        assert _only(probe, MODAL_GLOSSARY)["open"] is False


@pytest.mark.parametrize("keystroke", ["Enter", "Space"])
def test_the_modal_glossary_opens_and_closes_from_the_keyboard_and_is_named_glossary(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,
    keystroke,
):
    with modal_open(firefox, advisor_server) as probe:
        summary = probe.page.locator(MODAL_GLOSSARY + " > summary")
        summary.focus()
        probe.page.keyboard.press(keystroke)
        assert _only(probe, MODAL_GLOSSARY)["open"] is True, f"{keystroke} did not open it"
        probe.page.keyboard.press(keystroke)
        assert _only(probe, MODAL_GLOSSARY)["open"] is False, f"{keystroke} did not close it"


def test_a_pending_live_toggle_stays_pending_while_the_modal_glossary_opens_and_closes(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,
):
    """S-4.13 and S-7.12: the Glossary changes nothing about the pending toggle or its confirmation."""
    with modal_open(firefox, advisor_server) as probe:
        probe.page.click(MODAL_TOGGLE)
        before = probe.page.evaluate(
            "(s) => document.querySelector(s).getAttribute('aria-checked')", MODAL_TOGGLE
        )
        probe.page.click(MODAL_GLOSSARY + " > summary")
        probe.page.click(MODAL_GLOSSARY + " > summary")
        after = probe.page.evaluate(
            "(s) => document.querySelector(s).getAttribute('aria-checked')", MODAL_TOGGLE
        )
        assert after == before, "opening the Glossary changed the pending toggle"
        probe.page.click(MODAL_SAVE)
        dialog = probe.page.locator("#sym-settings-confirm")
        assert dialog.is_visible()
        assert dialog.get_attribute("aria-label") == "Confirm live trading"
        assert [p for p in probe.posts if "/api/symphony-settings" in p[1]] == [], (
            "nothing may be sent before the confirmation"
        )


def test_a_failed_modal_fetch_shows_its_error_text_and_no_glossary(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,
):
    """S-4.14: the error state is the reference's and holds no Glossary."""
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        probe.page.route(
            "**/api/symphony-settings/*",
            lambda route: (
                route.fulfill(status=500, content_type="text/plain", body="boom")
                if route.request.method == "GET"
                else route.continue_()
            ),
        )
        open_page(probe, "/")
        probe.page.evaluate("(id) => window.openSymphonySettings(null, id)", _first_symphony_id())
        wait_until(
            probe,
            "() => /Failed to load settings/.test(document.getElementById('sym-settings-modal').textContent)",
            "the modal never showed its error text",
        )
        assert _facts(probe, "#sym-settings-modal [data-testid='glossary']") == []
        assert _facts(probe, "#sym-settings-modal [data-term]") == []
        assert _only(probe, "#sym-settings-modal .ssm-state--err")["shown"]


def test_a_pending_live_toggle_keeps_the_descriptions_and_save_asks_before_sending(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,
):
    with modal_open(firefox, advisor_server) as probe:
        probe.page.click(MODAL_TOGGLE)
        _assert_modal_rows_visible(probe)
        assert len(_facts(probe, "#sym-settings-modal [data-explainer]")) == 4
        probe.page.click(MODAL_SAVE)
        dialog = probe.page.locator("#sym-settings-confirm")
        assert dialog.is_visible()
        assert dialog.get_attribute("aria-label") == "Confirm live trading"
        assert [p for p in probe.posts if "/api/symphony-settings" in p[1]] == [], (
            "nothing may be sent before the confirmation"
        )
        probe.page.click("#sym-confirm-cancel")
        assert probe.page.locator("#sym-settings-confirm").count() == 0
        assert [p for p in probe.posts if "/api/symphony-settings" in p[1]] == []
        _assert_modal_rows_visible(probe)
