"""Settings and the per-symphony settings modal say what they are, define their terms, and say the
dashboard does not save credentials.

Every expected text is a row of ``tests/fixtures/page_explainers/copy.json`` read through
``tests/ui/_page_copy.py``; no sentence of the plan is typed here. The rows owned here are the Copy
table A rows S01, S03, S08, S11, S14, S17 and M04 to M07, the Copy table B rows S13a to S13c, S15,
S16, S18, M01 and M08, and the Copy table C rows S02, S05, S07, S12r and S12s.

Three layers, none of which needs a browser (the browser module is
``tests/ui/test_page_explainers_settings_browser.py``):

* the server render of ``GET /settings`` (the template-built rows, with no script run, which is also
  what the page holds when its script fails to load);
* the real ``static/settings.js`` and ``static/settings-modal.js`` run unmodified over that page in
  the node page harness, for the rows a script builds and for what a script re-render must not
  duplicate or lose;
* the real ``POST /api/settings`` for the refusal of a typed credential.

Fixture provenance. ``GET /api/settings`` and ``GET /api/symphony-settings/<id>`` answers are the real
routes over the captured production ``bot_state`` (``database.save_state``), as in
``tests/ui/_settings_page_answers.py`` and ``tests/ui/test_settings_modal_ordering_client.py``. The
one edit is a named-field one: ``symphonies_no_longer_held`` is set to one entry of the shape
``app.get_settings`` returns, because producing a real one needs several sessions of shadow rows
(``tests/app/test_no_longer_held_surfaces.py`` does that and is not repeated here). The credential
value typed is a labelled placeholder, never a credential. The ``title`` text of the "No longer
held" row is the string ``static/settings.js`` carries at the reference commit, copied verbatim.
"""

from __future__ import annotations

import json
import re

import pytest
from bs4 import BeautifulSoup

import app as app_module
import database
from tests.ui import _page_copy as copy
from tests.ui._page_js_harness import (
    answer,
    page_scripts,
    render_page,
    route,
    run_page,
)
from tests.ui._settings_page_answers import ENV_TEXT, seed_state, settings_get_answer
from tests.ui._shell_support import (  # noqa: F401  (autouse overrides: the page needs the real state)
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
)
from tests.ui.test_settings_modal_ordering_client import (  # noqa: F401  (fixture: two symphonies)
    GET_A,
    GET_B,
    SAVE,
    TOGGLE,
    A,
    B,
    spec,
    symphonies,
)

TYPED_PLACEHOLDER = "typed-placeholder-not-a-credential"
SUMMARY_TEXT = "What the terms mean"
GONE_NAME = "Review Gone Symphony"
GONE_ID = "review-gone-symphony"
# Verbatim from static/settings.js at the reference commit (a ``title`` that stays, S-2.13).
GONE_ROW_TITLE = (
    "Still recorded, but Composer no longer reports it and the engine writes it no rows. "
    "It returns here by itself if its rows resume."
)

SETTINGS_SECTION = {
    "S03": "master",
    "S08": "algorithm",
    "S11": "credentials",
    "S14": "symphonies",
    "S17": "maintenance",
}


def _norm(text: str) -> str:
    return " ".join(text.split())


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _text(element) -> str:
    return _norm(element.get_text())


def _page_text(soup: BeautifulSoup) -> str:
    """The text of the page body: the markup between the header and the scripts, never a style rule."""
    return _text(soup.select_one('[data-testid="settings-page"]'))


def _rows(host: str, table: str) -> list[dict]:
    """The rows of ``table`` whose host is ``host`` or names a place on it ("Settings Live execution body")."""
    return [
        r
        for r in copy.rows(table)
        if copy.host_of(r) == host or copy.host_of(r).startswith(host + " ")
    ]


SETTINGS_A = _rows("Settings", "A")
MODAL_A = _rows("settings modal", "A")
SETTINGS_B = _rows("Settings", "B")
MODAL_B = _rows("settings modal", "B")
SETTINGS_C = _rows("Settings", "C")
TEMPLATE_B = [r for r in SETTINGS_B if r["builder"] == "template"]
SCRIPT_B = [r for r in SETTINGS_B if r["builder"] == "static/settings.js"]


def _targets(rows: list[dict]) -> list[str]:
    seen: list[str] = []
    for r in rows:
        if r["placement_target"] not in seen:
            seen.append(r["placement_target"])
    return seen


def _keys_of_target(rows: list[dict], target: str) -> list[str]:
    return [r["key"] for r in rows if r["placement_target"] == target]


def _is_literal_old_text(old: str) -> bool:
    """An old text the plan gives as words, not as a pointer to a source line."""
    return bool(old) and old != "none" and " at `" not in old and not old.startswith("the ")


@pytest.fixture
def settings_html() -> str:
    return render_page("/settings")


@pytest.fixture
def settings_page(settings_html):
    return _soup(settings_html)


# ---------------------------------------------------------------------------
# The rows this cycle owns, as the fixture lists them
# ---------------------------------------------------------------------------


def test_the_fixture_lists_the_rows_this_cycle_owns():
    assert [r["key"] for r in SETTINGS_A] == ["S01", "S03", "S08", "S11", "S14", "S17"]
    assert [r["key"] for r in MODAL_A] == ["M04", "M05", "M06", "M07"]
    assert [r["key"] for r in SETTINGS_B] == ["S13a", "S13b", "S13c", "S15", "S16", "S18"]
    assert [r["key"] for r in MODAL_B] == ["M01", "M08"]
    assert [r["key"] for r in SETTINGS_C] == ["S02", "S05", "S07", "S12r", "S12s"]
    assert _targets(TEMPLATE_B) == ["S11", "S17"]
    assert _targets(SCRIPT_B) == ["S14"]
    assert _targets(MODAL_B) == ["M04", "M07"]


# ---------------------------------------------------------------------------
# The server render: Copy table A (S-1.5, S-1.19)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("row", SETTINGS_A, ids=[r["key"] for r in SETTINGS_A])
def test_each_settings_description_shows_its_exact_text_in_its_own_element(row, settings_page):
    key = row["key"]
    found = settings_page.select(f'[data-explainer="{key}"]')
    assert len(found) == 1, f"{key}: {len(found)} elements carry data-explainer={key!r}"
    element = found[0]
    assert _text(element) == _norm(copy.text(key)), f"{key}: the element's own text differs"
    assert not element.has_attr("hidden")
    assert element.find_parent("details") is None, f"{key} sits inside a disclosure"


@pytest.mark.parametrize(
    ("key", "section"), sorted(SETTINGS_SECTION.items()), ids=sorted(SETTINGS_SECTION)
)
def test_a_section_description_sits_directly_under_its_heading(key, section, settings_page):
    element = settings_page.select_one(f'[data-explainer="{key}"]')
    holder = settings_page.select_one(f"#sec-{section}")
    assert element is not None and holder is not None
    assert element in holder.descendants, f"{key} is not inside #sec-{section}"
    heading = element.find_previous_sibling()
    assert heading is not None and "section-header" in heading.get("class", []), (
        f"{key}: the element before it is {heading.name if heading else None}, not the heading"
    )


def test_the_page_caption_row_s01_carries_its_text_and_keeps_its_testid(settings_page):
    element = settings_page.select_one('[data-explainer="S01"]')
    assert element is not None and element.get("data-testid") == "settings-caption"


@pytest.mark.parametrize(
    "row",
    [r for r in SETTINGS_A if r["kind"] == "reworded" and _is_literal_old_text(r["old_text"])],
    ids=lambda r: r["key"],
)
def test_a_reworded_description_leaves_no_trace_of_its_old_text(row, settings_html, settings_page):
    assert _norm(row["old_text"]) not in _page_text(settings_page)


# ---------------------------------------------------------------------------
# The server render: Copy table B, template-built disclosures (S-2.5, S-2.11, S-2.18)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", _targets(TEMPLATE_B))
def test_a_template_built_disclosure_is_closed_named_and_lists_its_terms_in_order(
    target, settings_page
):
    keys = _keys_of_target(TEMPLATE_B, target)
    found = settings_page.select(f'details[data-terms="{target}"]')
    assert len(found) == 1, f"{len(found)} disclosures for {target}"
    details = found[0]
    assert "hp-more" in details.get("class", [])
    assert not details.has_attr("open"), "a disclosure is closed on load"
    summary = details.find("summary", recursive=False)
    assert summary is not None and _text(summary) == SUMMARY_TEXT
    got = [(p.get("data-term"), _text(p)) for p in details.find_all("p", recursive=False)]
    assert got == [(k, _norm(copy.text(k))) for k in keys], f"{target}: terms or text differ"
    explainer = settings_page.select_one(f'[data-explainer="{target}"]')
    assert explainer.find_next_sibling() is details, (
        f"the {target} disclosure is not directly after its description"
    )


@pytest.mark.parametrize("row", TEMPLATE_B, ids=[r["key"] for r in TEMPLATE_B])
def test_a_term_is_defined_once_on_the_page(row, settings_page):
    assert len(settings_page.select(f'[data-term="{row["key"]}"]')) == 1


# ---------------------------------------------------------------------------
# The server render: Copy table C (S-3.11, S-3.12)
# ---------------------------------------------------------------------------

TEMPLATE_C_SELECTORS = {
    "S02": ".sn-footnote",
    "S05": ".live-desc",
    "S07": ".setting-row .field-hint",
    "S12r": ".cred-warn",
}


@pytest.mark.parametrize("key", sorted(TEMPLATE_C_SELECTORS))
def test_a_template_built_relabel_shows_its_new_text_in_the_same_element(key, settings_page):
    expected = _norm(copy.text(key, "C"))
    texts = [_text(e) for e in settings_page.select(TEMPLATE_C_SELECTORS[key])]
    if key == "S05":
        assert any(t.startswith(expected) for t in texts), (
            f"S05: no .live-desc starts with {expected!r}"
        )
    else:
        assert expected in texts, (
            f"{key}: no {TEMPLATE_C_SELECTORS[key]} element reads {expected!r}"
        )


def test_the_live_description_keeps_its_dry_run_sentence(settings_page):
    desc = _text(settings_page.select_one(".live-desc"))
    assert "Dry run tracks identical math but never sends an order" in desc


@pytest.mark.parametrize("row", SETTINGS_C, ids=[r["key"] for r in SETTINGS_C])
def test_no_relabel_leaves_its_old_text_on_the_page(row, settings_page):
    old = _norm(row["old_text"])
    assert _is_literal_old_text(row["old_text"]), row["key"]
    assert old not in _page_text(settings_page), f"{row['key']}: old text still on the page"


def test_the_old_credential_status_word_is_gone_from_the_page_script():
    from tests.ui._page_js_harness import STATIC

    source = (STATIC / "settings.js").read_text(encoding="utf-8")
    assert copy.row("S12s", "C")["old_text"] not in source


# ---------------------------------------------------------------------------
# Credentials: no text says a credential is saved from here (S-3.16, S-3.17)
# ---------------------------------------------------------------------------

SAVE_WORDS = re.compile(r"(writes?|written|saved?|saving)[^.]{0,80}\.env", re.I)
ANY_SAVE_WORD = re.compile(r"\b(saved?|saving|writes?|written)\b", re.I)
_ALLOWED_CREDENTIAL_ROWS = ("S11", "S12r", "S12s", "S13a", "S13b", "S13c")


def _without_the_allowed_rows(text: str) -> str:
    for key in _ALLOWED_CREDENTIAL_ROWS:
        text = text.replace(_norm(copy.text(key)), " ")
    return text


def test_no_text_on_the_page_says_a_credential_will_be_saved_or_written_to_env(settings_page):
    page = _without_the_allowed_rows(_page_text(settings_page))
    assert not SAVE_WORDS.search(page), SAVE_WORDS.search(page).group(0)


def test_the_credentials_section_says_nothing_of_saving_beyond_its_three_rows(settings_page):
    section = _without_the_allowed_rows(_text(settings_page.select_one("#sec-credentials")))
    assert not ANY_SAVE_WORD.search(section), ANY_SAVE_WORD.search(section).group(0)


def test_the_credentials_description_says_the_dashboard_does_not_save_them(settings_page):
    element = settings_page.select_one('[data-explainer="S11"]')
    assert element is not None, "the Credentials description element is missing"
    text = _text(element)
    assert "does not save credentials" in text and ".env" in text and "host" in text


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / "refusal.env"
    path.write_text(ENV_TEXT, encoding="utf-8")
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(path))
    return path


def _credential_keys(settings_page) -> list[str]:
    return [i["data-key"] for i in settings_page.select("input.cred-input[data-key]")]


def test_the_page_lists_nine_credential_fields(settings_page):
    assert len(_credential_keys(settings_page)) == 9


def test_every_credential_field_is_refused_by_the_save_route_and_nothing_is_written(
    settings_page, env_file
):
    before = env_file.read_bytes()
    for key in _credential_keys(settings_page):
        with app_module.app.test_client() as client:
            response = client.post(
                "/api/settings", json={"globals": {key: TYPED_PLACEHOLDER}, "symphonies": {}}
            )
        assert response.status_code == 400, f"{key}: answered {response.status_code}"
        body = response.get_json()
        assert body["status"] == "error" and "allowlist" in body["message"], key
        assert TYPED_PLACEHOLDER not in json.dumps(body), f"{key}: the answer echoes the value"
        assert env_file.read_bytes() == before, f"{key}: the env file changed"


def test_a_save_that_carries_a_credential_with_a_valid_change_writes_neither(
    settings_page, env_file
):
    before = env_file.read_bytes()
    with app_module.app.test_client() as client:
        response = client.post(
            "/api/settings",
            json={
                "globals": {"ALPACA_KEY": TYPED_PLACEHOLDER, "EXECUTION_START_TIME": "09:35"},
                "symphonies": {},
            },
        )
    assert response.status_code == 400
    assert env_file.read_bytes() == before, "the valid change was written beside a refused key"


# ---------------------------------------------------------------------------
# The real settings.js over the page
# ---------------------------------------------------------------------------


@pytest.fixture
def roster(settings_html):
    """The real ``GET /api/settings`` over the captured state, plus one no-longer-held entry."""
    seed_state()
    real = settings_get_answer()
    body = dict(real["json"])
    body["symphonies_no_longer_held"] = [{"id": GONE_ID, "name": GONE_NAME}]
    assert body["symphonies_list"], "the captured state has no symphonies"
    return {**real, "json": body}


def run_settings(html: str, scenario: str, roster_answer: dict, *routes_):
    return run_page(
        html,
        "await H.boot();\n" + scenario,
        routes=[route(r"/api/settings$", roster_answer, method="GET"), *routes_],
        pathname="/settings",
    )


READ_CONTENT = (
    "return {content: H.$('.settings-content').innerHTML, page: H.text('.settings-body')};"
)


def test_the_script_builds_one_closed_disclosure_for_the_symphony_overrides(settings_html, roster):
    out = run_settings(settings_html, READ_CONTENT, roster)
    section = _soup(out.result["content"]).select_one("#sec-symphonies")
    found = section.select('details[data-terms="S14"]')
    assert len(found) == 1, f"{len(found)} S14 disclosures after one load"
    details = found[0]
    assert "hp-more" in details.get("class", []) and not details.has_attr("open")
    summary = details.find("summary", recursive=False)
    assert summary is not None and _text(summary) == SUMMARY_TEXT
    got = [(p.get("data-term"), _text(p)) for p in details.find_all("p", recursive=False)]
    assert got == [(r["key"], _norm(copy.text(r["key"]))) for r in SCRIPT_B], got
    explainer = section.select_one('[data-explainer="S14"]')
    assert explainer.find_next_sibling() is details, "not directly after the S14 description"
    assert section.select_one(".sym-split") in details.find_next_siblings(), (
        "the disclosure must sit outside the containers the script rewrites"
    )
    assert details.find_parent(id="sym-list") is None
    assert details.find_parent(id="sym-detail") is None


def test_the_no_longer_held_row_keeps_its_title_and_the_meaning_is_also_in_a_line(
    settings_html, roster
):
    out = run_settings(settings_html, READ_CONTENT, roster)
    soup = _soup(out.result["content"])
    row = soup.select_one(".sym-gone-row")
    assert row is not None and _text(row) == GONE_NAME
    assert row.get("title") == GONE_ROW_TITLE, "the title must stay as it was"
    line = soup.select_one('[data-term="S16"]')
    assert line is not None and _text(line) == _norm(copy.text("S16"))
    assert "No longer held" in _text(line)


DISCARD_THREE_TIMES = """
const before = H.$$('[data-explainer]').map((e) => e.textContent);
const disclosure = H.$('details[data-terms="S14"]');
if (disclosure) disclosure.setAttribute('open', '');
for (let i = 0; i < 3; i += 1) {
  await H.set('#execution-start-time', '09:3' + (i + 1));
  await H.click('#btn-discard');
}
const last = H.$$('.sym-list-btn').pop();
await H.click('.sym-list-btn[data-sym-id="' + last.dataset.symId + '"]');
const after = H.$$('[data-explainer]').map((e) => e.textContent);
return {before, after, content: H.$('.settings-content').innerHTML,
        open: !!H.$('details[data-terms="S14"]') && H.$('details[data-terms="S14"]').hasAttribute('open')};
"""


def test_re_rendering_the_roster_never_duplicates_or_closes_a_description(settings_html, roster):
    out = run_settings(settings_html, DISCARD_THREE_TIMES, roster)
    assert out.result["after"] == out.result["before"]
    soup = _soup(out.result["content"])
    for key in [r["key"] for r in SCRIPT_B]:
        assert len(soup.select(f'[data-term="{key}"]')) == 1, f"{key} was duplicated"
    assert len(soup.select('details[data-terms="S14"]')) == 1
    assert len(soup.select('[data-explainer="S14"]')) == 1
    assert out.result["open"] is True, "a re-render rebuilt the disclosure and closed it"


SWITCH_SECTIONS_WITH_AN_EDIT = """
const read = () => H.$$('[data-explainer]').map((e) => e.textContent);
const before = read();
await H.set('#execution-start-time', '09:35');
for (const s of ['algorithm', 'credentials', 'symphonies', 'maintenance', 'master']) {
  await H.click('.sn-btn[data-section="' + s + '"]');
}
return {before, after: read(), value: H.value('#execution-start-time'),
        saveDisabled: H.$('#btn-save').disabled === true,
        discardDisabled: H.$('#btn-discard').disabled === true};
"""


def test_switching_section_with_an_unsaved_edit_keeps_the_edit_and_the_descriptions(
    settings_html, roster
):
    out = run_settings(settings_html, SWITCH_SECTIONS_WITH_AN_EDIT, roster)
    result = out.result
    assert result["before"], "the page shows no description to keep"
    assert result["after"] == result["before"]
    assert result["value"] == "09:35", "the unsaved edit was dropped"
    assert result["saveDisabled"] is False and result["discardDisabled"] is False


# --- the credential status (Copy table C, S12s) -----------------------------------------------

TYPE_A_CREDENTIAL = f"""
const sel = '.cred-input[data-key="ALPACA_KEY"]';
const status = () => H.$(sel).closest('.cred-field-wrap').querySelector('.cred-status');
const idle = status().textContent;
await H.set(sel, '{TYPED_PLACEHOLDER}');
const typed = {{text: status().textContent, ready: status().getAttribute('data-ready')}};
await H.set(sel, '');
const cleared = {{text: status().textContent, ready: status().getAttribute('data-ready')}};
await H.set(sel, '{TYPED_PLACEHOLDER}');
return {{idle, typed, cleared, page: H.text('.settings-body'),
        statuses: H.$$('.cred-status').map((s) => s.textContent),
        content: H.$('.settings-content').innerHTML}};
"""


def test_a_typed_credential_shows_the_new_status_and_a_cleared_one_shows_nothing(
    settings_html, roster
):
    out = run_settings(settings_html, TYPE_A_CREDENTIAL, roster)
    result = out.result
    status_text = copy.text("S12s", "C")
    assert result["idle"] == ""
    assert result["typed"] == {"text": status_text, "ready": "1"}
    assert result["cleared"] == {"text": "", "ready": "0"}
    assert [s for s in result["statuses"] if s] == [status_text], "only the typed field speaks"
    assert copy.row("S12s", "C")["old_text"] not in _norm(result["page"])


def test_with_a_credential_typed_no_text_says_it_will_be_saved_or_written_to_env(
    settings_html, roster
):
    out = run_settings(settings_html, TYPE_A_CREDENTIAL, roster)
    soup = _soup(out.result["content"])
    page = _norm(out.result["page"])
    for key in (*_ALLOWED_CREDENTIAL_ROWS,):
        page = page.replace(_norm(copy.text(key)), " ")
    assert not SAVE_WORDS.search(page), SAVE_WORDS.search(page).group(0)
    section = _without_the_allowed_rows(_text(soup.select_one("#sec-credentials")))
    assert not ANY_SAVE_WORD.search(section), ANY_SAVE_WORD.search(section).group(0)


def test_saving_with_a_typed_credential_sends_one_request_and_shows_the_refusal_text_as_before(
    settings_html, roster, env_file
):
    with app_module.app.test_client() as client:
        refusal = client.post(
            "/api/settings",
            json={"globals": {"ALPACA_KEY": TYPED_PLACEHOLDER}, "symphonies": {}},
        )
    assert refusal.status_code == 400
    refused = answer(refusal)
    out = run_settings(
        settings_html,
        TYPE_A_CREDENTIAL.replace("return {", "await H.click('#btn-save');\nreturn {", 1),
        roster,
        route(r"/api/settings$", refused, method="POST"),
    )
    posts = out.posts("/api/settings")
    assert len(posts) == 1, "Save must send exactly one request"
    assert json.loads(posts[0]["body"])["globals"] == {"ALPACA_KEY": TYPED_PLACEHOLDER}
    assert out.alerts == ["Save failed: " + refusal.get_json()["message"]], (
        "the page's error text for a refused save must be unchanged"
    )


# ---------------------------------------------------------------------------
# The real settings-modal.js
# ---------------------------------------------------------------------------


def run_modal(scenario: str, *routes_):
    from tests.ui import _refusal_answers as answers

    html = render_page("/history")
    scripts = [s for s in page_scripts(html) if s != "history.js"] + ["settings-modal.js"]
    return run_page(
        html,
        "await H.boot();\nH.setToken('" + answers.TOKEN_B + "');\n" + scenario,
        routes=list(routes_),
        scripts=scripts,
        pathname="/history",
    )


def OPEN(sid: str) -> str:  # noqa: N802  (a scenario fragment, used like a constant)
    return f"await H.act('open', async () => {{ window.openSymphonySettings(null, '{sid}'); }});\n"


READ_MODAL = "H.$('#sym-settings-modal').innerHTML"


def _modal_soup(out) -> BeautifulSoup:
    return _soup(out.result["modal"])


def _assert_modal_copy(soup: BeautifulSoup, *, a_disclosure_may_be_open: bool = False) -> None:
    for row in MODAL_A:
        found = soup.select(f'[data-explainer="{row["key"]}"]')
        assert len(found) == 1, f"{row['key']}: {len(found)} elements in the modal"
        assert _text(found[0]) == _norm(copy.text(row["key"])), row["key"]
    for target in _targets(MODAL_B):
        details = soup.select(f'details[data-terms="{target}"]')
        assert len(details) == 1, f"{len(details)} modal disclosures for {target}"
        d = details[0]
        assert "hp-more" in d.get("class", [])
        assert a_disclosure_may_be_open or not d.has_attr("open"), f"{target} is not closed"
        summary = d.find("summary", recursive=False)
        assert summary is not None and _text(summary) == SUMMARY_TEXT
        got = [(p.get("data-term"), _text(p)) for p in d.find_all("p", recursive=False)]
        want = [(k, _norm(copy.text(k))) for k in _keys_of_target(MODAL_B, target)]
        assert got == want, f"{target}: {got}"
        explainer = soup.select_one(f'[data-explainer="{target}"]')
        assert id(d) in {
            id(x)
            for x in (
                explainer.find_next_sibling(),
                (explainer.find_parent(class_="ssm-sec") or explainer).find_next_sibling(),
            )
        }, f"the {target} disclosure is not directly after its description"


def test_the_modal_shows_each_description_and_each_closed_disclosure_in_order(symphonies):
    out = run_modal(
        OPEN(A) + f"return {{modal: {READ_MODAL}}};",
        route(GET_A, spec(symphonies[A]), method="GET"),
    )
    _assert_modal_copy(_modal_soup(out))


def test_the_modal_leaves_no_trace_of_the_old_sub_lines(symphonies):
    out = run_modal(
        OPEN(A) + f"return {{modal: {READ_MODAL}}};",
        route(GET_A, spec(symphonies[A]), method="GET"),
    )
    text = _text(_modal_soup(out))
    for row in MODAL_A:
        if _is_literal_old_text(row["old_text"]):
            assert _norm(row["old_text"]) not in text, row["key"]


PENDING_TOGGLE_THEN_SAVE = f"""
await H.click('{TOGGLE}');
const afterToggle = {READ_MODAL};
await H.click('{SAVE}');
const confirmEl = H.$('#sym-settings-confirm');
const dialog = {{present: !!confirmEl, label: confirmEl ? confirmEl.getAttribute('aria-label') : null,
                text: confirmEl ? confirmEl.textContent : ''}};
const postsBeforeConfirm = H.posts('/api/symphony-settings').length;
await H.click('#sym-confirm-cancel');
const afterCancel = {{confirmGone: !H.$('#sym-settings-confirm'),
                     posts: H.posts('/api/symphony-settings').length, modal: {READ_MODAL}}};
await H.click('{SAVE}');
await H.click('#sym-confirm-go-live');
return {{afterToggle, dialog, postsBeforeConfirm, afterCancel,
        posts: H.posts('/api/symphony-settings').map((p) => [p.url, p.body])}};
"""


def test_a_pending_live_toggle_keeps_the_descriptions_and_the_confirm_path_is_as_before(symphonies):
    out = run_modal(
        OPEN(B) + PENDING_TOGGLE_THEN_SAVE,
        route(GET_B, spec(symphonies[B]), method="GET"),
        route(r"/api/symphony-settings/", {"json": {"status": "ok"}}, method="POST"),
    )
    result = out.result
    _assert_modal_copy(_soup(result["afterToggle"]))
    assert result["dialog"]["present"] is True, "Save with a pending live flag must ask first"
    assert result["dialog"]["label"] == "Confirm live trading"
    assert "enable real-money live trading" in result["dialog"]["text"]
    assert result["postsBeforeConfirm"] == 0, "nothing may be sent before the confirmation"
    assert result["afterCancel"]["confirmGone"] and result["afterCancel"]["posts"] == 0
    _assert_modal_copy(_soup(result["afterCancel"]["modal"]))
    ((url, body),) = result["posts"]
    assert url == f"/api/symphony-settings/{B}"
    assert json.loads(body) == {
        "live_mode": True,
        "locked_vars": symphonies[B]["locked_vars"],
        "confirm": True,
    }


OPEN_AND_CLOSE_THE_DISCLOSURE_WITH_A_PENDING_TOGGLE = f"""
await H.click('{TOGGLE}');
const details = () => H.$('details[data-terms="M04"]');
const present = !!details();
const beforeOpen = {READ_MODAL};
if (present) details().setAttribute('open', '');
const whileOpen = {{open: present && details().hasAttribute('open'), modal: {READ_MODAL}}};
if (present) details().removeAttribute('open');
const afterClose = {READ_MODAL};
await H.click('{SAVE}');
const confirmEl = H.$('#sym-settings-confirm');
const postsBeforeConfirm = H.posts('/api/symphony-settings').length;
await H.click('#sym-confirm-go-live');
return {{present, beforeOpen, whileOpen, dialog: confirmEl ? confirmEl.getAttribute('aria-label') : null,
        postsBeforeConfirm, posts: H.posts('/api/symphony-settings').map((p) => p.body)}};
"""


def test_opening_and_closing_a_modal_disclosure_leaves_a_pending_live_toggle_and_its_confirm_as_before(
    symphonies,
):
    out = run_modal(
        OPEN(B) + OPEN_AND_CLOSE_THE_DISCLOSURE_WITH_A_PENDING_TOGGLE,
        route(GET_B, spec(symphonies[B]), method="GET"),
        route(r"/api/symphony-settings/", {"json": {"status": "ok"}}, method="POST"),
    )
    result = out.result
    assert result["present"] is True, "the modal has no closed-by-default disclosure to open"
    _assert_modal_copy(_soup(result["beforeOpen"]))
    assert result["whileOpen"]["open"] is True
    _assert_modal_copy(_soup(result["whileOpen"]["modal"]), a_disclosure_may_be_open=True)
    assert result["dialog"] == "Confirm live trading"
    assert result["postsBeforeConfirm"] == 0, "nothing may be sent before the confirmation"
    (body,) = result["posts"]
    assert json.loads(body) == {
        "live_mode": True,
        "locked_vars": symphonies[B]["locked_vars"],
        "confirm": True,
    }


RE_RENDER_THE_MODAL = f"""
await H.click('{TOGGLE}');
await H.click('{TOGGLE}');
await H.click('.sym-settings-locked-var-btn');
await H.click('.sym-settings-locked-var-btn');
return {{modal: {READ_MODAL}}};
"""


def test_re_rendering_the_modal_never_duplicates_a_description(symphonies):
    out = run_modal(OPEN(A) + RE_RENDER_THE_MODAL, route(GET_A, spec(symphonies[A]), method="GET"))
    _assert_modal_copy(_modal_soup(out))


def test_a_failed_settings_fetch_shows_the_error_and_no_definitions(symphonies):
    out = run_modal(
        OPEN(B) + f"return {{modal: {READ_MODAL}}};", route(GET_B, {"reject": True}, method="GET")
    )
    soup = _modal_soup(out)
    assert "Failed to load settings. Please try again." in _text(soup)
    assert not soup.select("[data-explainer], [data-terms], [data-term]")


def test_while_the_settings_load_the_modal_shows_no_definitions(symphonies):
    out = run_modal(
        f"""
        await H.act('open', async () => {{ window.openSymphonySettings(null, '{A}'); }});
        const loading = {READ_MODAL};
        await H.release('late');
        return {{modal: loading}};
        """,
        route(GET_A, spec(symphonies[A], hold="late"), method="GET"),
    )
    assert not _modal_soup(out).select("[data-explainer], [data-terms], [data-term]")
