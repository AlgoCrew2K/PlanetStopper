"""Cross-host sweeps of the page-explainers change, read through the Flask client and node.

The merged head holds every host at once; these checks read all of them in one pass, where each
host's own cycle pinned its own page. The expected text of every row comes from
``tests/fixtures/page_explainers/copy.json`` through ``tests/ui/_page_copy.py``; a configured value
is filled from the authority the test patched, never by the production formatter.

Render and source (no browser): S-2.11 (a term is defined once, in the section its row names),
S-2.13 (a title that defined a term stays beside the visible line), S-2.15 (no section, no
disclosure), S-3.7 (every attribute, id, class and contract name of the reference is still there),
S-3.12 and S-3.15 (new names present, old names gone, on every host), S-4.7 and S-4.9 (a configured
value is the patched authority's, its fallback phrase replaces the whole phrase when the authority
fails), S-4.10 (no configured value is typed beside the copy).

Node (the real scripts over the real render): S-1.20 (the four data routes answering 500 leave every
description as it was), S-1.21 (polls and tab choices never duplicate a description) and S-3.10
(no poll or refresh brings an old label back).

Recorded against the host cycles' own tests, not repeated here: S-2.12/S-2.14/S-1.21 (dashboard),
S-1.17/S-6.14/S-6.6/S-6.10 (Settings) and the Performance and History empty-rectangle checks sit in
``test_page_explainers_*_browser.py``; the browser sweeps over every host and world are in
``test_page_explainers_cross_host_browser.py``.

Fixture provenance: ``ref_token_inventory.json`` is written by ``produce_ref_token_inventory.py``
from ``git show 1988ce5f:<path>`` (no git runs in these checks, so a depth-one clone passes).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re

import pytest

import app as app_module
from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _page_copy as copy
from tests.ui import _page_explainers as harness
from tests.ui import _page_explainers_advisor as adv
from tests.ui._page_js_harness import answer, render_page, route, run_page
from tests.ui._shell_support import (  # noqa: F401  (the two overrides and the empty directory)
    PAGES,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
INVENTORY = REPO / "tests" / "fixtures" / "page_explainers" / "ref_token_inventory.json"
PRODUCER = REPO / "tests" / "fixtures" / "page_explainers" / "produce_ref_token_inventory.py"

# Y06 renames the History cell label "Triggers" to "Exits"; the label's only use is visible text
# (the plan's ruled exception), so that one pair is the only token besides the removed controls
# that may be missing.
RULED_RENAMES = ("data:data-label=Triggers",)

# Tokens the plan removes with the element that carried them, each named by what it was:
# the Advisor's page caption beside the tab bar and its disclosure; the Overview's tab line and the
# term disclosures the Overview's descriptions carried (their lines are Glossary lines now); the
# guardrail paragraph; the pointer on the Overview toolbar; and the autotune shared line with the
# column-lift modifiers. A later cycle adds the tokens of its own host.
RULED_REMOVALS = (
    "testid:advisor-caption",
    "class:adv-caption",
    "data:data-explainer=A01",
    "data:data-terms=A01",
    "data:data-explainer=O01",
    "data:data-terms=O03",
    "data:data-terms=O08",
    "data:data-terms=O15",
    "data:data-terms=O27",
    "testid:guardrail-uniform-note",
    "class:obs-framing-note",
    "class:adv-pointer",
    "testid:autotune-shared-line",
    "class:autotune-shared-line",
    "class:liftedClasses",
    # The Retirement tab: its two term disclosures became lines of its one Glossary, and the
    # modifier that put its two subsections side by side is gone with the script that set it.
    "data:data-terms=R02",
    "data:data-terms=R22",
    "class:retirement-pair--never-run",
    # The other tabs: their term disclosures became lines of each tab's Glossary, the two notes
    # the plan removes (the Strategy Builder run note and the Frontrunner pending-approval line)
    # went with their descriptions, and the flex wrap that stretched a last row of Frontrunner
    # cards is gone with its modifier class.
    "data:data-terms=C01",
    "data:data-terms=W01",
    "data:data-terms=L01",
    "data:data-terms=B03",
    "data:data-terms=B07",
    "data:data-terms=X05",
    "data:data-explainer=B03",
    "data:data-explainer=X05",
    "class:proposal-cards--fill",
    # The fix round on the Advisor tabs: the Chat default title (T02 says how a conversation
    # opens), the Frontrunner "On approval" line (the risk banner and the Identity line say it)
    # and the X11 Glossary line, split into one line per term (X11a to X11c).
    "class:chat-default-title",
    "class:fr-explainer-line",
    "testid:fr-explainer-line",
    "data:data-term=X11",
    # The dashboard and its detail panel: the page line and the Tracked, Armed, Triggered line are
    # gone, and every term disclosure became a line of the dashboard's or the panel's Glossary.
    "data:data-explainer=D01",
    "data:data-explainer=D20",
    "data:data-terms=dash.hero",
    "data:data-terms=dash.rows",
    "data:data-terms=D21",
    "data:data-terms=D28",
    "data:data-terms=D41",
    "data:data-terms=D47",
    "data:data-terms=dp.lead",
    "data:data-terms=D57",
    "data:data-terms=D59",
    "data:data-terms=D67",
    # Performance and History: the repeated header cells of the risk table are deleted with the
    # two-group layout, and each term disclosure became a line of the page's Glossary.
    "class:metrics-th-repeat",
    "data:data-terms=F01",
    "data:data-terms=F13",
    "data:data-terms=F16",
    "data:data-terms=F21",
    "data:data-terms=Y01",
    "data:data-terms=Y12",
    # Settings: its two section disclosures and the one the page script writes for the symphony
    # overrides became lines of the Settings Glossary.
    "data:data-terms=S11",
    "data:data-terms=S17",
    "data:data-terms=S14",
)

# A token one file loses with the element or function it came from while another file keeps it:
# the script that measured the Retirement pair carried the pair's test id and those of its two
# subsections, the template's elements keep them.
RULED_FILE_REMOVALS = frozenset(
    {
        ("static/ai_advisor.js", "testid:retirement-pair"),
        ("static/ai_advisor.js", "testid:retirement-recommender-section"),
        ("static/ai_advisor.js", "testid:tenure-review-section"),
        # The closed-disclosure look of a Glossary comes from the shared macro; History keeps the
        # class on its own reason-descriptions disclosure.
        ("templates/index.html", "class:hp-more"),
        ("templates/performance.html", "class:hp-more"),
        ("templates/settings.html", "class:hp-more"),
        # The Advisor template's last rules for the class went with the disclosures they placed
        # (the stylesheet check requires it); the shared Glossary macro keeps the class.
        ("templates/ai_advisor.html", "class:hp-more"),
    }
)

_CACHE: dict = {}


def render_hosts(
    world, tmp_path, monkeypatch, *, key: str = "", setup=None, hosts=PAGES
) -> dict[str, str]:
    """``{host: html}`` for ``world``, after ``setup(monkeypatch, tmp_path)`` when one is given.

    The world is seeded once per call; the default render of the same world is read first and kept
    (so a variant can be compared with it), then ``setup`` changes an authority and the pages are
    read again. The strings are cached per (world, key, hosts).
    """
    cache_key = (world, key, tuple(hosts))
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    harness.credential_free_environment(
        tmp_path / "page.env",
        monkeypatch.setattr,
        lambda name: monkeypatch.delenv(name, raising=False),
    )
    harness.seed_world(world, tmp_path, monkeypatch.setattr)
    client = app_module.app.test_client()

    def read() -> dict[str, str]:
        pages = {}
        for host in hosts:
            response = client.get(host)
            assert response.status_code == 200, f"GET {host} answered {response.status_code}"
            pages[host] = response.get_data(as_text=True)
        return pages

    _CACHE.setdefault((world, "", tuple(hosts)), read())
    if setup is not None:
        setup(monkeypatch, tmp_path)
        _CACHE[cache_key] = read()
    return _CACHE[cache_key]


def tidy(text: str) -> str:
    """Words joined by single spaces, with no space left before closing punctuation by inline tags."""
    return re.sub(r"\s+([,.;:!?)])", lambda m: m.group(1), adv.norm(text))


def visible_text(html: str) -> str:
    page = adv.soup(html)
    for dead in page(["script", "style", "template"]):
        dead.decompose()
    return tidy(page.get_text(" "))


ADVISOR_HOSTS = (
    "advisor",
    "overview",
    "correlations",
    "retirement",
    "asset swaps",
    "logic changes",
    "chat",
    "strategy builder",
    "frontrunner",
)


def page_of(place: str) -> str | None:
    """The page a copy row's host words name; None for a row every page carries or no page holds."""
    lowered = copy.host_of({"host": place}).lower()
    if lowered == "/" or lowered.startswith("detail panel"):
        return "/"
    for name, path in (("performance", "/performance"), ("history", "/history")):
        if lowered.startswith(name):
            return path
    if lowered.startswith("settings"):
        return "/settings"
    if lowered.startswith(ADVISOR_HOSTS):
        return "/ai-advisor"
    return None


def slotted(record: dict) -> bool:
    return bool(record.get("slots"))


def template_rows(table: str) -> list[dict]:
    return [
        r
        for r in copy.rows(table)
        if r["builder"] == "template" and not slotted(r) and "new_resolved" not in r
    ]


# ---------------------------------------------------------------------------
# S-2.11: a term is defined once, in the section its row names
# ---------------------------------------------------------------------------

# The Overview's term disclosures became its one Glossary: those lines are pinned by the Overview's
# own Glossary tests, so a definition row whose key is a Glossary line, or whose disclosure the plan
# removed, is not read here as a disclosure of its section.
OVERVIEW_GLOSSARY_KEYS = {line["key"] for line in copy.glossary("Overview")}
RETIREMENT_GLOSSARY_KEYS = {line["key"] for line in copy.glossary("Retirement")}
OTHER_TAB_GLOSSARY_KEYS = {
    line["key"] for host in adv.OTHER_TAB_GLOSSARY_HOSTS for line in copy.glossary(host)
}
REMOVED_OVERVIEW_DISCLOSURES = {
    "A01",
    "O01",
    "O03",
    "O08",
    "O15",
    "O27",
    "R02",
    "R22",
    "C01",
    "W01",
    "L01",
    "B03",
    "B07",
    "X05",
    # The dashboard, its detail panel, Performance, History, Settings and the settings modal.
    "dash.hero",
    "dash.rows",
    "D21",
    "D28",
    "D40",
    "D41",
    "D47",
    "dp.lead",
    "D57",
    "D59",
    "D67",
    "F01",
    "F13",
    "F16",
    "F21",
    "Y01",
    "Y12",
    "S11",
    "S14",
    "S17",
    "M04",
    "M07",
}
# The hosts of the dashboard cycle: every definition they hold is a line of the host's Glossary.
PAGE_GLOSSARY_HOSTS = ("/", "detail panel", "Performance", "History", "Settings")
PAGE_GLOSSARY_KEYS = {line["key"] for host in PAGE_GLOSSARY_HOSTS for line in copy.glossary(host)}

DEFINITION_ROWS = [
    r
    for r in template_rows("B")
    if r.get("placement_kind") == "D"
    and page_of(r["host"])
    and r["key"] not in OVERVIEW_GLOSSARY_KEYS
    and r["key"] not in RETIREMENT_GLOSSARY_KEYS
    and r["key"] not in OTHER_TAB_GLOSSARY_KEYS
    and r["key"] not in PAGE_GLOSSARY_KEYS
    and r.get("placement_target") not in REMOVED_OVERVIEW_DISCLOSURES
]


def test_every_definition_row_is_a_line_of_some_hosts_glossary_or_names_a_removed_disclosure():
    """Every host's definitions are Glossary lines now, read by the Glossary tests below and in
    the host modules: no definition row is left to be a disclosure of its own section."""
    assert DEFINITION_ROWS == [], [r["key"] for r in DEFINITION_ROWS]


@pytest.mark.parametrize("world", ["empty", "full"])
def test_every_line_of_the_dashboard_performance_history_and_settings_glossaries_is_defined_once_inside_it(
    world, tmp_path, monkeypatch
):
    pages = render_hosts(world, tmp_path, monkeypatch)
    problems = []
    for host in PAGE_GLOSSARY_HOSTS:
        page = adv.soup(pages[page_of(host)])
        glossary = page.select_one(f'details[data-terms="glossary"][data-glossary-host="{host}"]')
        if glossary is None:
            problems.append(f"{host}: no Glossary")
            continue
        for line in copy.glossary(host):
            nodes = page.select(f'[data-term="{line["key"]}"]')
            if len(nodes) != 1:
                problems.append(f"{host}: {line['key']} is defined {len(nodes)} times on its page")
            elif glossary not in nodes[0].parents:
                problems.append(f"{host}: {line['key']} is outside the host's Glossary")
    assert not problems, "\n".join(problems)


def test_no_definition_on_any_page_sits_outside_a_glossary(tmp_path, monkeypatch):
    pages = render_hosts("full", tmp_path, monkeypatch)
    problems = []
    for host, html in pages.items():
        page = adv.soup(html)
        for node in page.select("[data-term]"):
            if node.find_parent("details", attrs={"data-terms": "glossary"}) is None:
                problems.append(f"{host}: {node.get('data-term')} is outside every Glossary")
        for node in page.select("[data-terms]:not([data-terms='glossary'])"):
            problems.append(f"{host}: a disclosure marked {node.get('data-terms')}")
        for summary in page.select("summary"):
            if adv.norm(summary.get_text(" ")) == "What the terms mean":
                problems.append(f"{host}: a 'What the terms mean' summary")
    assert not problems, "\n".join(problems)


def test_every_moved_overview_line_is_defined_once_inside_the_glossary(tmp_path, monkeypatch):
    page = adv.soup(render_hosts("full", tmp_path, monkeypatch)["/ai-advisor"])
    problems = []
    for key in sorted(OVERVIEW_GLOSSARY_KEYS):
        nodes = page.select(f'#tab-panel-overview [data-term="{key}"]')
        if len(nodes) != 1:
            problems.append(f"{key} is defined {len(nodes)} times on the Overview")
        elif nodes[0].find_parent("details", attrs={"data-terms": "glossary"}) is None:
            problems.append(f"{key} is outside the Overview Glossary")
    assert not problems, "; ".join(problems)


def test_every_moved_retirement_line_is_defined_once_inside_the_glossary(tmp_path, monkeypatch):
    page = adv.soup(render_hosts("full", tmp_path, monkeypatch)["/ai-advisor"])
    problems = []
    for key in sorted(RETIREMENT_GLOSSARY_KEYS):
        nodes = page.select(f'#tab-panel-retirement [data-term="{key}"]')
        if len(nodes) != 1:
            problems.append(f"{key} is defined {len(nodes)} times on the Retirement tab")
        elif nodes[0].find_parent("details", attrs={"data-terms": "glossary"}) is None:
            problems.append(f"{key} is outside the Retirement Glossary")
    assert not problems, "; ".join(problems)


@pytest.mark.parametrize("host", adv.OTHER_TAB_GLOSSARY_HOSTS)
def test_every_moved_line_of_the_other_tabs_is_defined_once_inside_its_glossary(
    host, tmp_path, monkeypatch
):
    page = adv.soup(render_hosts("full", tmp_path, monkeypatch)["/ai-advisor"])
    tab = adv.TAB_OF_GLOSSARY_HOST[host]
    problems = []
    for line in copy.glossary(host):
        nodes = page.select(f'#tab-panel-{tab} [data-term="{line["key"]}"]')
        if len(nodes) != 1:
            problems.append(f"{line['key']} is defined {len(nodes)} times on the {host} tab")
        elif nodes[0].find_parent("details", attrs={"data-terms": "glossary"}) is None:
            problems.append(f"{line['key']} is outside the {host} Glossary")
    assert not problems, "; ".join(problems)


# ---------------------------------------------------------------------------
# S-2.13: a title that defined a term at the reference stays, and the meaning is also visible
# ---------------------------------------------------------------------------

# R12 is no longer here: the plan rewrites its Glossary line, which the Retirement tests read.
TITLED_TERMS = ("D10", "D16", "O09a", "O09b", "O09c", "S16", "A03")


def test_a_title_the_reference_carried_stays_and_the_meaning_is_also_a_visible_line(
    tmp_path, monkeypatch
):
    pages = render_hosts("full", tmp_path, monkeypatch)
    everything = " ".join(visible_text(html) for html in pages.values())
    record = json.loads(INVENTORY.read_text(encoding="utf-8"))
    titles_of = _token_module().titles_of
    reworded = [alt for r in RELABEL_ROWS for alt in old_alternatives(r)]
    gone = []
    carried = 0
    for path, titles in record["titles"].items():
        head = set(titles_of((REPO / path).read_text(encoding="utf-8")))
        for title in titles:
            carried += 1
            if title not in head and not any(alt in title for alt in reworded):
                gone.append(f"{path}: title {title!r}")
    assert carried >= 10, f"premise: the reference carried titles ({carried})"
    assert not gone, "titles the reference carried are gone:\n" + "\n".join(gone)
    # A term a script builds (S16) is read from the script's own source.
    scripts = " ".join(
        adv.norm(p.read_text(encoding="utf-8")) for p in (REPO / "static").glob("*.js")
    )
    missing = [
        key
        for key in TITLED_TERMS
        if not slotted(copy.row(key, "B"))
        and adv.norm(copy.text(key, "B")) not in (everything + " " + scripts)
    ]
    assert not missing, f"meanings of titled terms with no visible or disclosure line: {missing}"


# ---------------------------------------------------------------------------
# S-2.15 and S-1.15: no section, no disclosure
# ---------------------------------------------------------------------------

# X05 is removed by the plan; the terms of B07 and B10 are lines of the Strategy Builder Glossary,
# which a page with no such section still holds.
ABSENT_WITH_SECTION = {
    "/ai-advisor": ("B07", "B10"),
}


def _is_hidden(node) -> bool:
    for ancestor in [node, *node.parents]:
        style = (ancestor.get("style") or "").replace(" ", "").lower() if ancestor.attrs else ""
        if "display:none" in style or (ancestor.attrs and ancestor.has_attr("hidden")):
            return True
    return False


def test_with_no_diagnostic_the_cvar_panel_is_hidden_and_holds_its_description_while_its_terms_stay_in_the_glossary(
    tmp_path, monkeypatch
):
    page = adv.soup(render_hosts("empty", tmp_path, monkeypatch)["/"])
    panel = page.select_one("#cvar-diagnostic-panel")
    assert panel is not None and _is_hidden(panel), "the CVaR panel must stay hidden"
    description = page.select_one('[data-explainer="D41"]')
    assert description is not None and panel in description.parents
    assert not page.select('[data-terms="D41"]'), "the panel keeps a disclosure of its own"
    glossary = page.select_one('details[data-terms="glossary"][data-glossary-host="/"]')
    assert glossary is not None
    for key in ("D42", "D43", "D44", "D46"):
        node = page.select_one(f'[data-term="{key}"]')
        assert node is not None and glossary in node.parents, f"{key} is not a Glossary line"
        assert panel not in node.parents, f"{key} is inside the hidden CVaR panel"


@pytest.mark.parametrize("host", sorted(ABSENT_WITH_SECTION))
def test_with_no_proposals_no_observations_the_sections_description_and_disclosure_are_absent(
    host, tmp_path, monkeypatch
):
    page = adv.soup(render_hosts("empty", tmp_path, monkeypatch)[host])
    problems = []
    for a_key in ABSENT_WITH_SECTION[host]:
        if page.select(f'[data-explainer="{a_key}"]'):
            problems.append(f"description {a_key} is shown with no section")
        if page.select(f'[data-terms="{a_key}"]'):
            problems.append(f"disclosure {a_key} is shown with no section")
    assert not problems, "\n".join(problems)


def _plan_moved(record: dict) -> bool:
    """A description row the current plan removes, moves or rewords: its own host's tests pin the
    new state, so the reference's wording is not demanded here."""
    if record["table"] != "A":
        return False
    try:
        now = copy.a_text(record["key"])
    except KeyError:
        return False
    return now != copy.text(record["key"], "A")


def test_every_section_that_renders_in_the_empty_state_shows_its_description(tmp_path, monkeypatch):
    pages = render_hosts("empty", tmp_path, monkeypatch)
    problems = []
    for record in copy.rows("A"):
        host = page_of(record["host_place"])
        if host is None or record["builder"] != "template" or record["kind"] == "kept":
            continue
        if _plan_moved(record):
            continue
        page = adv.soup(pages[host])
        if page.select(f'[data-explainer="{record["key"]}"]'):
            continue
        absent_ok = record["key"] in ABSENT_WITH_SECTION.get(host, ())
        if not absent_ok:
            problems.append(f"{host}: no description {record['key']} in the empty state")
    assert not problems, "\n".join(problems)


def test_every_section_of_the_other_hosts_shows_its_new_description_in_the_empty_state(
    tmp_path, monkeypatch
):
    """Copy table A for the dashboard, its detail panel, Performance, History and Settings: each row
    that is neither removed nor moved reads its new text, with nothing stored and no slot filled (a
    row with a slot reads its fallback phrase, which its own host module pins)."""
    pages = render_hosts("empty", tmp_path, monkeypatch)
    problems = []
    for record in copy.a_rows():
        host = page_of(record["host_place"])
        if host not in ("/", "/performance", "/history", "/settings"):
            continue
        if copy.host_of(record) == "settings modal":
            continue  # the modal's descriptions are written by static/settings-modal.js on `/`
        if record["kind"] in ("removed", "moved") or record["slots"]:
            continue
        found = adv.soup(pages[host]).select(f'[data-explainer="{record["key"]}"]')
        if len(found) != 1:
            problems.append(f"{host}: {len(found)} elements carry {record['key']}")
        elif adv.norm(found[0].get_text(" ")) != adv.norm(copy.a_text(record["key"])):
            problems.append(f"{host}: {record['key']} reads {adv.norm(found[0].get_text(' '))!r}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("world", ["empty", "full"])
def test_the_two_removed_dashboard_lines_are_on_no_page(world, tmp_path, monkeypatch):
    pages = render_hosts(world, tmp_path, monkeypatch)
    page = adv.soup(pages["/"])
    for key in ("D01", "D20"):
        assert copy.a_row(key)["kind"] in ("removed", "moved")
        assert not page.select(f'[data-explainer="{key}"]'), f"{key} is still on the dashboard"
        old = adv.norm(copy.text(key, "A"))
        # A Glossary line may carry the old words (D20t holds D20's sentences word for word).
        for glossary in page.select('details[data-terms="glossary"]'):
            glossary.decompose()
        body = visible_text(str(page))
        assert old not in body, f"the old text of {key} is still on the page outside a Glossary"


# ---------------------------------------------------------------------------
# S-3.7: nothing the plan did not name is lost from the markup
# ---------------------------------------------------------------------------


def _token_module():
    spec = importlib.util.spec_from_file_location("ref_token_producer", PRODUCER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _token_reader():
    return _token_module().tokens_of


def test_every_attribute_id_class_and_contract_name_of_the_reference_is_still_in_its_file():
    record = json.loads(INVENTORY.read_text(encoding="utf-8"))
    tokens_of = _token_reader()
    excluded = set(record["removed_control_tokens"]) | set(RULED_RENAMES) | set(RULED_REMOVALS)
    problems = []
    for path, tokens in record["files"].items():
        head = tokens_of((REPO / path).read_text(encoding="utf-8"))
        problems += [
            f"{path}: {t}"
            for t in tokens
            if t not in head and t not in excluded and (path, t) not in RULED_FILE_REMOVALS
        ]
    assert not problems, "tokens the reference carried are gone:\n" + "\n".join(problems)


def test_the_inventory_is_the_reference_commits_and_holds_none_of_the_removed_controls():
    """The three controls an earlier change removed are not in the reference's markup, so none of
    their tokens is recorded and none is among the tokens a page must keep."""
    record = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert record["reference_commit"] == harness.REFERENCE_COMMIT
    assert record["removed_control_tokens"] == []
    every_token = {t for tokens in record["files"].values() for t in tokens}
    assert {"id:dp-view-logs-btn", "id:dp-go-cash-btn"}.isdisjoint(every_token)
    assert RULED_RENAMES[0] not in record["files"]["templates/history.html"]


def test_every_ruled_removal_is_a_token_the_reference_carried_and_the_head_no_longer_does():
    """The list names real tokens (a typo would excuse nothing and hide a lost token), and none
    of them has come back."""
    record = json.loads(INVENTORY.read_text(encoding="utf-8"))
    tokens_of = _token_reader()
    carried = {t for tokens in record["files"].values() for t in tokens}
    assert set(RULED_REMOVALS) <= carried, sorted(set(RULED_REMOVALS) - carried)
    back = []
    for path, tokens in record["files"].items():
        head = tokens_of((REPO / path).read_text(encoding="utf-8"))
        back += [f"{path}: {t}" for t in tokens if t in RULED_REMOVALS and t in head]
    assert not back, "removed tokens are back:\n" + "\n".join(back)


def test_the_token_comparison_reports_a_missing_testid_and_class():
    tokens_of = _token_reader()
    reference = tokens_of('<p data-testid="kept" class="a b" id="x">t</p>')
    head = tokens_of('<p data-testid="kept" class="a" id="x">t</p>')
    assert reference - head == {"class:b"}
    assert tokens_of('<p data-testid="gone"></p>') - head == {"testid:gone"}


# ---------------------------------------------------------------------------
# S-3.12 and S-3.15: new names show, old names are gone, on every host
# ---------------------------------------------------------------------------

MIN_LITERAL = 4
SHORT_OLD_TEXT = 30


def old_alternatives(record: dict) -> list[str]:
    """The old wordings to search for: each ``; `` alternative that the new wording does not contain."""
    old = record["old_text"]
    if " at `" in old or old.startswith("the ") or "with A05" in old:
        return []
    found = []
    for alternative in (a.strip() for a in old.split("; ")):
        literal = re.sub(r"\{[a-z_]+\}", "", alternative).strip()
        if (
            len(literal) >= MIN_LITERAL
            and "{" not in alternative
            and alternative not in new_text(record)
        ):
            found.append(alternative)
    return found


def new_text(record: dict) -> str:
    return record.get("new_text") or copy.text(record["key"], record["table"])


def new_alternatives(record: dict) -> list[str]:
    return [tidy(part) for part in new_text(record).split("; ") if part.strip()]


def leaf_texts(html: str) -> list[str]:
    page = adv.soup(html)
    for dead in page(["script", "style", "template"]):
        dead.decompose()
    return [
        tidy(el.get_text(" "))
        for el in page.find_all(True)
        if not el.find(True) and tidy(el.get_text(" "))
    ]


def scope_of(record: dict, pages: dict[str, str]) -> list[str]:
    """The pages a row's old wording must be gone from: its own page when its host words name one."""
    place = record.get("host_place") or record.get("host") or ""
    own = page_of(place)
    return [own] if own in pages else list(pages)


def old_text_hits(pages: dict[str, str], records: list[dict]) -> list[str]:
    leaves = {host: leaf_texts(html) for host, html in pages.items()}
    texts = {host: visible_text(html) for host, html in pages.items()}
    hits = []
    for record in records:
        for old in old_alternatives(record):
            for host in scope_of(record, pages):
                if len(old) < SHORT_OLD_TEXT:
                    found = old in leaves[host]
                else:
                    found = old in texts[host]
                if found:
                    hits.append(f"{record['key']}: {old!r} still on {host}")
    return hits


RELABEL_ROWS = [
    r for r in (*copy.rows("C"), *(x for x in copy.rows("A") if x["kind"] == "reworded"))
]


TEMPLATE_RELABEL_ROWS = [r for r in RELABEL_ROWS if r["builder"] == "template"]


def test_every_template_built_new_label_shows_on_some_host_in_the_full_world(tmp_path, monkeypatch):
    pages = render_hosts("full", tmp_path, monkeypatch)
    everything = " ".join(visible_text(html) for html in pages.values())
    missing = [
        r["key"]
        for r in RELABEL_ROWS
        if r["builder"] == "template"
        and not slotted(r)
        and "new_resolved" not in r
        and not _plan_moved(r)
        # Y02's reference wording is shortened by CX-8, which the History tests pin.
        and r["key"] != "Y02"
        and not all(part in everything for part in new_alternatives(r))
    ]
    assert not missing, f"new texts of reworded rows absent from every host: {missing}"
    assert copy.x_row("CX-8")["new_text"] in everything


@pytest.mark.parametrize("world", ["empty", "full"])
def test_no_old_name_of_a_reworded_row_is_on_any_host(world, tmp_path, monkeypatch):
    pages = render_hosts(world, tmp_path, monkeypatch)
    hits = old_text_hits(pages, TEMPLATE_RELABEL_ROWS)
    assert not hits, "\n".join(hits)


def test_the_old_text_scan_finds_an_old_name_it_is_given():
    record = {"key": "T", "old_text": "Triggers", "new_text": "Exits", "table": "C"}
    page = {"/x": "<table><tr><th>Triggers</th></tr></table>"}
    assert old_text_hits(page, [record]) == ["T: 'Triggers' still on /x"]
    assert old_text_hits({"/x": "<th>Exits</th>"}, [record]) == []


# ---------------------------------------------------------------------------
# S-4.7 and S-4.9: a configured value is its authority's, or the whole fallback phrase
# ---------------------------------------------------------------------------


def test_the_trading_cost_phrases_of_both_pages_read_the_patched_authority(tmp_path, monkeypatch):
    import autotuner

    def patch(mp, _tmp):
        mp.setattr(autotuner, "SIM_EXIT_FRICTION_PCT", 0.25)

    pages = render_hosts(
        "full", tmp_path, monkeypatch, key="friction-025", setup=patch, hosts=("/", "/performance")
    )
    for host, key in (("/", "D25"), ("/performance", "F24")):
        node = adv.soup(pages[host]).select_one(f'[data-term="{key}"]')
        assert node is not None, f"{key} is not on {host}"
        want = adv.norm(copy.fill(copy.text(key, "B"), friction_pct="0.25"))
        assert adv.norm(node.get_text(" ")) == want
        assert "0.25 percentage points" in adv.norm(node.get_text(" "))


# (slot, copy row, table, host, resolver function in app)
SLOT_CASES = [
    ("tenure_threshold", "R22", "A", "/ai-advisor", "_copy_tenure_threshold"),
    ("screen_threshold", "R02", "A", "/ai-advisor", "_copy_screen_threshold"),
    ("stress_fraction_pct", "R19b", "B", "/ai-advisor", "_copy_stress_fraction_pct"),
    ("watch_trail_pp", "R27b", "B", "/ai-advisor", "_copy_watch_trail_pp"),
    ("friction_pct", "D25", "B", "/", "_copy_friction_pct"),
    ("friction_pct", "F24", "B", "/performance", "_copy_friction_pct"),
    ("shadow_retention_days", "D07", "B", "/", "_copy_shadow_retention_days"),
]


def _raises(*_args, **_kwargs):
    raise RuntimeError("authority unavailable")


def _yields_none(*_args, **_kwargs):
    return None


def _element_texts(html: str) -> dict[str, str]:
    page = adv.soup(html)
    return {
        node.get("data-explainer") or node.get("data-term"): adv.norm(node.get_text(" "))
        for node in page.select("[data-explainer], [data-term]")
    }


@pytest.mark.parametrize("failure", ["raises", "yields_none"])
@pytest.mark.parametrize(
    ("slot", "row", "table", "host", "resolver"), SLOT_CASES, ids=[c[1] for c in SLOT_CASES]
)
def test_a_slot_whose_authority_fails_prints_its_whole_fallback_phrase_and_nothing_else_changes(
    slot, row, table, host, resolver, failure, tmp_path, monkeypatch
):
    replacement = _raises if failure == "raises" else _yields_none

    def patch(mp, _tmp):
        mp.setattr(app_module, resolver, replacement)

    pages = render_hosts(
        "full", tmp_path, monkeypatch, key=f"{resolver}-{failure}", setup=patch, hosts=(host,)
    )
    patched = _element_texts(pages[host])
    baseline = _element_texts(render_hosts("full", tmp_path, monkeypatch, hosts=(host,))[host])
    # A description this plan shortened reads its Copy table A text and its Values row.
    text = copy.a_text(row) if row in adv.RETIREMENT_A_KEYS else copy.text(row, table)
    want = adv.norm(adv.with_slots(text, row, {slot: None}))
    assert patched[row] == want, f"{row} did not print the fallback phrase"
    changed = {k for k in baseline if patched.get(k) != baseline[k]}
    siblings = {r["copy_row"] for r in copy.slot_rows() if r["slot"] == slot} & set(baseline)
    assert changed <= siblings, f"a failing {slot} changed other rows: {sorted(changed - siblings)}"
    assert not re.search(r"\b(None|NaN|undefined)\b", patched[row])


def test_a_retention_the_env_file_cannot_read_prints_the_fallback_phrase_on_the_dashboard(
    tmp_path, monkeypatch
):
    def patch(mp, tmp):
        env = tmp / "retention.env"
        env.write_text("SHADOW_HISTORY_RETENTION_DAYS=abc\n", encoding="utf-8")
        mp.setattr(app_module, "ENV_FILE_PATH", str(env))

    pages = render_hosts(
        "full", tmp_path, monkeypatch, key="retention-abc", setup=patch, hosts=("/",)
    )
    got = _element_texts(pages["/"])["D07"]
    assert got == adv.norm(
        adv.with_slots(copy.text("D07", "B"), "D07", {"shadow_retention_days": None})
    )


def test_the_history_minimum_the_banner_prints_is_the_authoritys_or_its_fallback_phrase(
    tmp_path, monkeypatch
):
    def patch(mp, _tmp):
        mp.setattr(app_module, "_PERFORMANCE_MIN_HISTORY_DAYS", None)

    pages = render_hosts(
        "full", tmp_path, monkeypatch, key="min-history-none", setup=patch, hosts=("/performance",)
    )
    banner = adv.soup(pages["/performance"]).select_one('[data-testid="insufficient-banner"]')
    want = adv.norm(adv.with_slots(copy.text("F10", "C"), "F10", {"min_history_days": None}))
    text = adv.norm(banner.get_text(" "))
    assert text.endswith(want), text
    assert "None" not in text


# ---------------------------------------------------------------------------
# S-4.10: no configured value is typed beside the copy
# ---------------------------------------------------------------------------

MIN_CONTEXT = 15
SOURCES = sorted([*(REPO / "templates").glob("*.html"), *(REPO / "static").glob("*.js")])


def _flat(path: pathlib.Path) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8", errors="replace"))


@pytest.mark.parametrize(
    "values",
    copy.new_slot_rows(),
    ids=[f"{v['slot']}-{v['copy_row']}" for v in copy.new_slot_rows()],
)
def test_no_source_types_the_value_a_slot_phrase_holds(values):
    whole_before, whole_after = values["phrase"].split("{" + values["slot"] + "}")
    # A side shorter than a phrase ("at ", " trading days,") appears beside other numbers legitimately.
    before = whole_before if len(whole_before.strip()) >= MIN_CONTEXT else ""
    after = whole_after if len(whole_after.strip()) >= MIN_CONTEXT else ""
    flats = {path: _flat(path) for path in SOURCES}
    seen = 0
    problems = []
    if not before.strip() and not after.strip():
        # Both sides are short ("at {x} or more"): the phrase as a whole is the context, so a
        # typed value is the whole phrase with a number in the slot, and the page reads the
        # slot through its name.
        typed = re.compile(re.escape(whole_before) + r"-?\d[\d.]*" + re.escape(whole_after))
        for path, flat in flats.items():
            if "page_copy." + values["slot"] in flat:
                seen += 1
            problems += [f"{path.name}: {m.group(0)!r} is typed" for m in typed.finditer(flat)]
    for path, flat in flats.items():
        if before.strip() and before in flat:
            seen += 1
            for match in re.finditer(re.escape(before) + r"\s*(.)", flat):
                if match.group(1).isdigit():
                    problems.append(f"{path.name}: a number follows {before.strip()!r}")
        if after.strip() and after in flat:
            seen += 1
            for match in re.finditer(r"(.)\s*" + re.escape(after), flat):
                if match.group(1).isdigit():
                    problems.append(f"{path.name}: a number precedes {after.strip()!r}")
    assert seen, f"premise: no source holds {before!r}; the scan would pass on nothing"
    assert not problems, "\n".join(problems)


def test_the_literal_scan_catches_a_typed_value():
    flat = "kept for the last 180 days."
    before, after = "kept for the last ", " days."
    assert any(
        m.group(1).isdigit() for m in re.finditer(re.escape(before) + r"\s*(.)", flat)
    ) and any(m.group(1).isdigit() for m in re.finditer(r"(.)\s*" + re.escape(after), flat))


# ---------------------------------------------------------------------------
# Node: the real scripts over the real render
# ---------------------------------------------------------------------------

SNAPSHOT = """
  const snapshot = () => ({
    explainers: H.$$('[data-explainer]').map((e) => [e.getAttribute('data-explainer'),
        e.textContent.replace(/\\s+/g, ' ').trim()]),
    disclosures: H.$$('[data-terms]').map((d) => [d.getAttribute('data-terms') === 'glossary'
        ? 'glossary:' + d.getAttribute('data-glossary-host') : d.getAttribute('data-terms'),
        d.hasAttribute('open'), d.querySelectorAll('[data-term]').length]),
  });
"""

FAILING_ROUTES = {
    "/": r"/api/state(\?|$)",
    "/performance": r"/api/performance(/symphonies)?(\?|$)",
    "/history": r"/api/history/",
    "/ai-advisor": r"/api/retirement-recommendations$",
}
SERVER_ERROR = {"status": 500, "text": "Internal Server Error"}
# The Advisor reads /api/retirement-recommendations only while a Run-now request is being waited
# for, so its refresh is started the way an operator starts it.
REFRESH_STEPS = {
    "/ai-advisor": "await H.click('[data-testid=\"retirement-run-now-btn\"]');",
}
EXTRA_ROUTES = {
    "/ai-advisor": [
        route(
            r"/ai-advisor/retirement/run$",
            {"status": 202, "json": {"status": "started"}},
            method="POST",
        )
    ],
}


def _prepared(world, tmp_path, monkeypatch):
    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )
    harness.seed_world(world, tmp_path, monkeypatch.setattr)
    return app_module.app.test_client()


@pytest.mark.parametrize("host", sorted(FAILING_ROUTES))
def test_a_data_route_answering_500_leaves_every_description_and_disclosure_as_rendered(
    host, tmp_path, monkeypatch
):
    _prepared("full", tmp_path, monkeypatch)
    pattern = FAILING_ROUTES[host]
    out = run_page(
        render_page(host),
        SNAPSHOT
        + """
        const before = snapshot();
        await H.boot();
        %s
        await H.advance(65000);
        return { before, after: snapshot(), asked: H.to(%s).length };
        """
        % (REFRESH_STEPS.get(host, ""), json.dumps(pattern)),
        routes=[route(pattern, SERVER_ERROR, method="GET"), *EXTRA_ROUTES.get(host, [])],
        pathname=host,
    )
    result = out.result
    assert result["before"]["explainers"], f"premise: {host} renders descriptions"
    assert result["asked"] >= 1, f"premise: the page never asked {pattern}"
    assert result["after"] == result["before"], f"{host} changed after a 500"


def test_three_dashboard_polls_never_duplicate_a_description_or_a_disclosure(tmp_path, monkeypatch):
    client = _prepared("dashboard_full", tmp_path, monkeypatch)
    body = answer(client.get("/api/state"))
    out = run_page(
        render_page("/"),
        SNAPSHOT
        + """
        const before = snapshot();
        await H.boot();
        for (let i = 0; i < 3; i++) await H.advance(31000);
        const after = snapshot();
        const count = (rows) => rows.reduce((m, r) => (m[r[0]] = (m[r[0]] || 0) + 1, m), {});
        return { before: count(before.explainers), after: count(after.explainers),
                 dBefore: count(before.disclosures), dAfter: count(after.disclosures),
                 polls: H.to('/api/state(\\\\?|$)').length };
        """,
        routes=[route(r"/api/state(\?|$)", body, method="GET")],
        pathname="/",
    )
    result = out.result
    assert result["polls"] >= 3
    assert result["after"] == result["before"]
    assert result["dAfter"] == result["dBefore"]
    assert set(result["after"].values()) == {1}, "a description is shown more than once"


def test_choosing_a_tab_leaving_and_choosing_it_again_shows_each_description_once(
    tmp_path, monkeypatch
):
    _prepared("full", tmp_path, monkeypatch)
    out = run_page(
        render_page("/ai-advisor"),
        SNAPSHOT
        + """
        await H.boot();
        const tabs = H.$$('[role="tab"]').map((t) => t.getAttribute('data-tab'));
        for (let round = 0; round < 2; round++) {
          for (const tab of tabs) await H.click('[role="tab"][data-tab="' + tab + '"]');
        }
        const after = snapshot();
        const count = (rows) => rows.reduce((m, r) => (m[r[0]] = (m[r[0]] || 0) + 1, m), {});
        return { tabs, counts: count(after.explainers), dCounts: count(after.disclosures) };
        """,
        routes=[],
        pathname="/ai-advisor",
    )
    result = out.result
    assert len(result["tabs"]) == 8
    assert set(result["counts"].values()) == {1}, result["counts"]
    assert set(result["dCounts"].values()) == {1}, result["dCounts"]


LEAVES = """
  const leaves = () => H.$$('*').filter((e) => e.children.length === 0)
      .map((e) => e.textContent.replace(/\\s+/g, ' ').trim()).filter(Boolean);
"""


def _old_texts_in(texts: list[str]) -> list[str]:
    out = []
    for record in RELABEL_ROWS:
        for old in old_alternatives(record):
            for text in texts:
                if (old == text) if len(old) < SHORT_OLD_TEXT else (old in text):
                    out.append(f"{record['key']}: {old!r}")
    return sorted(set(out))


def test_five_dashboard_polls_never_bring_an_old_label_back(tmp_path, monkeypatch):
    client = _prepared("dashboard_full", tmp_path, monkeypatch)
    body = answer(client.get("/api/state"))
    out = run_page(
        render_page("/"),
        LEAVES
        + """
        const first = leaves();
        await H.boot();
        const seen = [];
        for (let i = 0; i < 5; i++) { await H.advance(31000); seen.push(leaves()); }
        return { first, seen };
        """,
        routes=[route(r"/api/state(\?|$)", body, method="GET")],
        pathname="/",
    )
    assert out.result["first"], "premise: the page has text"
    rendered = set(_old_texts_in(out.result["first"]))
    for poll, texts in enumerate(out.result["seen"], 1):
        assert set(_old_texts_in(texts)) <= rendered, f"poll {poll} brought an old label back"


def test_five_overview_refreshes_never_bring_an_old_label_back(tmp_path, monkeypatch):
    client = _prepared("full", tmp_path, monkeypatch)
    runs = answer(client.get("/api/autotune-runs"))
    out = run_page(
        render_page("/ai-advisor"),
        LEAVES
        + """
        const first = leaves();
        await H.boot();
        const seen = [];
        for (let i = 0; i < 5; i++) { await H.advance(15500); seen.push(leaves()); }
        return { first, seen, asked: H.to('/api/autotune-runs').length };
        """,
        routes=[route(r"/api/autotune-runs$", runs, method="GET")],
        pathname="/ai-advisor",
    )
    assert out.result["asked"] >= 5, "premise: the Overview refreshed its runs five times"
    rendered = set(_old_texts_in(out.result["first"]))
    for refresh, texts in enumerate(out.result["seen"], 1):
        assert set(_old_texts_in(texts)) <= rendered, f"refresh {refresh} brought an old label back"
