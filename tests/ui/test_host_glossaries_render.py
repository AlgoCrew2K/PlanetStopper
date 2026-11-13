"""The dashboard, its detail panel, Performance, History and Settings each end with one Glossary.

Server render only (the Flask client over the seeded worlds of ``tests/ui/_page_explainers.py``; the
script-built rows and the settings modal are read by ``tests/ui/test_host_glossaries_browser.py``
and ``tests/ui/test_page_copy_settings.py``). The expected lines come from
``tests/fixtures/page_explainers/copy.json`` through ``tests/ui/_page_copy.py`` and from nothing
else; a slot is filled here from the authority the page reads, in the Values table's format, and
the production formatter is never called.

What is pinned, per host (``/``, ``detail panel``, ``Performance``, ``History``, ``Settings``):

* one ``details`` marked ``data-terms="glossary"`` of the shared closed look (``hp-more``), named
  "Glossary", closed on load, carrying ``data-glossary-host`` with the host's name, and the LAST
  element of the host (the page content, the detail panel's body, the Settings content after its
  five sections), in every world, whichever sections render;
* one ``p data-term`` per line of Table G, in Table G's order, each with its exact text: a Table G2
  line reads its G2 text, every other line the reference row's text with its slots filled;
* no "What the terms mean" summary and no other ``data-terms`` disclosure on the page, and every
  ``data-term`` on the page is a Glossary line defined once;
* the page loads ``static/glossary.js`` once;
* every sentence a shortened or removed description drops, and each of its old texts, is in no
  section body (a sentence a Glossary line took over lives in the Glossary only);
* every label Table G3 names for the host carries ``data-glossary="<line key>"`` in the markup the
  server writes.
"""

from __future__ import annotations

import re

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _page_copy as copy
from tests.ui import _page_explainers_advisor as adv
from tests.ui._shell_support import (  # noqa: F401  (the overrides and the empty directory)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
)
from tests.ui.test_page_copy_advisor import _sentences
from tests.ui.test_page_explainers_cross_host import render_hosts
from tests.ui.test_page_explainers_dashboard_browser import _slots

# host name (the fixture's key and the macro's argument) -> (page, the element the Glossary ends)
HOSTS = {
    "/": ("/", ".page-wrap"),
    "detail panel": ("/", '[data-testid="detail-panel"] .detail-body'),
    "Performance": ("/performance", ".page-wrap"),
    "History": ("/history", ".page-wrap"),
    "Settings": ("/settings", "main.settings-content"),
}
PAGES = ("/", "/performance", "/history", "/settings")
WORLDS = ("empty", "full")
SUMMARY = "Glossary"


def _page(world: str, path: str, tmp_path, monkeypatch):
    return adv.soup(render_hosts(world, tmp_path, monkeypatch)[path])


def _glossary(page, host: str):
    found = page.select(f'details[data-terms="glossary"][data-glossary-host="{host}"]')
    assert len(found) == 1, f"{len(found)} Glossaries for {host!r}, not one"
    return found[0]


def _without_glossaries(page) -> str:
    clone = adv.soup(str(page))
    for dead in clone(["script", "style", "template"]):
        dead.decompose()
    for glossary in clone.select('details[data-terms="glossary"]'):
        glossary.decompose()
    return adv.norm(clone.get_text(" "))


def _line_text(line: dict) -> str:
    text = line["text"]
    for slot in line["slots"]:
        text = text.replace("{" + slot + "}", _slots()[slot])
    return adv.norm(text)


@pytest.mark.parametrize("world", WORLDS)
@pytest.mark.parametrize("host", sorted(HOSTS))
def test_a_host_ends_with_one_closed_glossary_holding_table_gs_lines_in_order(
    host, world, tmp_path, monkeypatch
):
    path, container_selector = HOSTS[host]
    page = _page(world, path, tmp_path, monkeypatch)
    glossary = _glossary(page, host)
    assert "hp-more" in glossary.get("class", []), "the Glossary is not the shared closed look"
    assert not glossary.has_attr("open"), "the Glossary is open on load"
    assert glossary.get("data-testid") == "glossary"
    summary = glossary.find("summary", recursive=False)
    assert summary is not None and adv.norm(summary.get_text(" ")) == SUMMARY
    assert glossary.find_all("summary") == [summary]
    container = page.select_one(container_selector)
    assert container is not None, f"{host}: no {container_selector} on {path}"
    assert glossary.parent is container, f"{host}: the Glossary is not a direct child of its host"
    assert container.find_all(recursive=False)[-1] is glossary, (
        f"{host}: the Glossary is not the last element of {container_selector}"
    )
    lines = [c for c in glossary.find_all(recursive=False) if c.name != "summary"]
    expected = copy.glossary(host)
    assert [c.name for c in lines] == ["p"] * len(expected)
    assert [c.get("data-term") for c in lines] == [line["key"] for line in expected]
    for node, line in zip(lines, expected, strict=True):
        assert adv.norm(node.get_text(" ")) == _line_text(line), line["key"]


def test_the_settings_glossary_sits_outside_every_section_so_it_shows_whichever_is_chosen(
    tmp_path, monkeypatch
):
    page = _page("full", "/settings", tmp_path, monkeypatch)
    glossary = _glossary(page, "Settings")
    assert not [p for p in glossary.parents if p.name == "section"], "inside a section"
    sections = page.select("main.settings-content > section.settings-section")
    assert len(sections) == 5
    assert all(s.sourceline is not None and s.sourceline < glossary.sourceline for s in sections), (
        "the Glossary is not after the five sections"
    )
    assert not glossary.has_attr("hidden") and not glossary.find_parent(attrs={"hidden": True})


@pytest.mark.parametrize("host", sorted(HOSTS))
def test_every_g2_line_reads_its_g2_text_and_every_other_line_the_reference_rows_text(
    host, tmp_path, monkeypatch
):
    path, _ = HOSTS[host]
    glossary = _glossary(_page("full", path, tmp_path, monkeypatch), host)
    lines = copy.glossary(host)
    assert any(line["source"] == "ref" for line in lines), f"premise: {host} has reference lines"
    for line in lines:
        node = glossary.select_one(f'p[data-term="{line["key"]}"]')
        assert node is not None, line["key"]
        if line["source"] == "G2":
            assert adv.norm(node.get_text(" ")) == _line_text(line), line["key"]
        else:
            reference = copy.row(line["key"], "B")["new_text"]
            for slot in line["slots"]:
                reference = reference.replace("{" + slot + "}", _slots()[slot])
            assert adv.norm(node.get_text(" ")) == adv.norm(reference), line["key"]


def test_the_g2_lines_exist_for_the_hosts_the_plan_names():
    named = {
        "/": ["D02", "D20t", "D50e"],
        "Performance": ["F15h", "F17a", "F17h"],
        "History": ["Y16"],
        "Settings": ["S14s", "S14p"],
    }
    for host, keys in named.items():
        got = [line["key"] for line in copy.glossary(host) if line["source"] == "G2"]
        assert got == keys, f"{host}: {got}"


@pytest.mark.parametrize("path", PAGES)
def test_no_page_carries_a_what_the_terms_mean_disclosure_or_a_definition_outside_the_glossary(
    path, tmp_path, monkeypatch
):
    page = _page("full", path, tmp_path, monkeypatch)
    assert not [
        s for s in page.select("summary") if adv.norm(s.get_text(" ")) == "What the terms mean"
    ]
    other = [d.get("data-terms") for d in page.select("[data-terms]:not([data-terms='glossary'])")]
    assert other == [], f"disclosures that are not a Glossary: {other}"
    for node in page.select("[data-term]"):
        assert node.find_parent("details", attrs={"data-terms": "glossary"}) is not None, (
            f"{node.get('data-term')} is defined outside a Glossary"
        )
    keys = [n.get("data-term") for n in page.select("[data-term]")]
    assert len(keys) == len(set(keys)), "a term is defined twice on one page"
    hosts = [h for h, (p, _) in HOSTS.items() if p == path]
    assert sorted(keys) == sorted(line["key"] for h in hosts for line in copy.glossary(h))


@pytest.mark.parametrize("path", PAGES)
def test_the_page_loads_the_glossary_script_once_beside_its_own_script(path, tmp_path, monkeypatch):
    page = _page("full", path, tmp_path, monkeypatch)
    sources = [s.get("src") or "" for s in page.select("script[src]")]
    assert sum(1 for s in sources if s.endswith("/static/glossary.js")) == 1, sources


# ---------------------------------------------------------------------------
# What the descriptions dropped is in no section body
# ---------------------------------------------------------------------------

_A_ROWS = [
    r
    for r in copy.a_rows()
    if copy.host_of(r) in ("/", "detail panel", "Performance", "History", "Settings")
    and r["kind"] in ("shortened", "removed", "moved")
]


def _page_of_row(record: dict) -> str:
    host = copy.host_of(record)
    return HOSTS[host][0]


@pytest.mark.parametrize("record", _A_ROWS, ids=[r["key"] for r in _A_ROWS])
def test_every_old_sentence_a_description_dropped_is_in_no_section_body(
    record, tmp_path, monkeypatch
):
    page = _page("full", _page_of_row(record), tmp_path, monkeypatch)
    body = _without_glossaries(page)
    old = adv.norm(copy.text(record["key"], "A"))
    new = copy.a_text(record["key"])
    kept = set(_sentences(new)) if new else set()
    assert old not in body, f"{record['key']}: the whole old text is on the page"
    for sentence in _sentences(old):
        if sentence not in kept:
            assert sentence not in body, (
                f"{record['key']}: old sentence {sentence!r} is on the page"
            )


def test_the_sentences_a_glossary_line_took_over_are_in_the_glossary_once(tmp_path, monkeypatch):
    """F17a takes the Replay and Shadow honesty sentence of F16 word for word; F15h the delta
    sentences of F13; S14s and S14p the storage and pinned-value sentences of S14."""
    moved = (
        ("Performance", "/performance", "F17a", "Replay = simulated if-held daily series"),
        (
            "Performance",
            "/performance",
            "F15h",
            "improved risk-adjusted outcomes vs holding through",
        ),
        ("Settings", "/settings", "S14s", "stored in alphabot_state.db"),
        ("Settings", "/settings", "S14p", "it will not follow a later global change"),
    )
    problems = []
    for host, path, key, words in moved:
        page = _page("full", path, tmp_path, monkeypatch)
        glossary = adv.norm(_glossary(page, host).get_text(" "))
        if words not in glossary:
            problems.append(f"{key}: {words!r} is not in the {host} Glossary")
        if words in _without_glossaries(page):
            problems.append(f"{key}: {words!r} is also in a section body")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# Labels that carry a Glossary line as their title (the markup the server writes)
# ---------------------------------------------------------------------------


def _texts(page, selector: str) -> dict[str, list]:
    found: dict[str, list] = {}
    for node in page.select(selector):
        found.setdefault(adv.norm(node.get_text(" ")), []).append(node)
    return found


def test_the_dashboard_labels_name_their_glossary_lines(tmp_path, monkeypatch):
    page = _page("full", "/", tmp_path, monkeypatch)
    glossary = _glossary(page, "/")
    missing = []

    def want(node, key: str, what: str) -> None:
        if node is None:
            missing.append(f"{what}: no element")
        elif node.get("data-glossary") != key:
            missing.append(f"{what}: data-glossary is {node.get('data-glossary')!r}, not {key!r}")
        elif glossary.select_one(f'p[data-term="{key}"]') is None:
            missing.append(f"{what}: {key} is not a Glossary line")

    want(page.select_one(".guard-alpha-label"), "D02", "the guard-effect label")
    rows = _texts(page, "#portfolio-strip .vs-row-label")
    want(next((v[0] for k, v in rows.items() if k.startswith("Max DD")), None), "D17", "Max DD")
    want(next((v[0] for k, v in rows.items() if k.startswith("Ann. vol")), None), "D18", "Ann. vol")
    stats = _texts(page, '[data-testid="mini-stat"] .mini-stat-label')
    for label in ("Tracked", "Armed", "Triggered"):
        want(stats.get(label, [None])[0], "D20t", label)
    want(page.select_one('[data-testid="mdd-lifetime-scalar"]'), "D19", "the lifetime Max DD line")
    account = page.select_one(".account-all-time-label")
    if account is not None:
        want(account, "D05", "Account all-time")
    assert not missing, "\n".join(missing)


def test_the_performance_labels_name_their_glossary_lines(tmp_path, monkeypatch):
    page = _page("full", "/performance", tmp_path, monkeypatch)
    glossary = _glossary(page, "Performance")
    labels = _texts(page, ".hp-fig-label")
    headers = _texts(page, "th")
    rows = _texts(page, "#metrics-tbody tr td:first-child")
    expected = [
        (labels, "Sharpe improvement", "F07"),
        (labels, "Max DD reduction", "F08"),
        (labels, "Sortino improvement", "F09"),
        (headers, "Delta (DD and vol in %)", "F15h"),
        (rows, "Max DD", "F15a"),
        (rows, "Calmar", "F15b"),
        (rows, "Annualized volatility", "F15c"),
        (rows, "Volatility reduction", "F15d"),
        (rows, "Total return", "F15e"),
        (rows, "Annualized return (CAGR)", "F15f"),
        (rows, "Up days", "F15g"),
        (headers, "Sample", "F17a"),
        (headers, "ρ", "F17b"),
        (headers, "95% CI", "F17c"),
        (headers, "Daily Sharpe", "F17d"),
        (headers, "N", "F17e"),
        (headers, "Est. annual trading-cost drag", "F24"),
    ]
    problems = []
    for pool, text, key in expected:
        nodes = pool.get(text)
        if not nodes:
            problems.append(f"{text!r}: no element")
        elif not all(n.get("data-glossary") == key for n in nodes):
            got = [n.get("data-glossary") for n in nodes]
            problems.append(f"{text!r}: data-glossary is {got}, not {key!r}")
        if glossary.select_one(f'p[data-term="{key}"]') is None:
            problems.append(f"{key} is not a Glossary line")
    assert not problems, "\n".join(problems)


def test_the_history_labels_name_their_glossary_lines(tmp_path, monkeypatch):
    page = _page("full", "/history", tmp_path, monkeypatch)
    glossary = _glossary(page, "History")
    labels = _texts(page, ".hp-fig-label")
    headers = _texts(page, "th")
    expected = [
        (labels, "Dollar result", "Y05"),
        (labels, "Win rate", "Y07"),
        (headers, "Detail", "Y16"),
        (headers, "Wins", "Y13"),
        (headers, "Avg per exit", "Y13"),
    ]
    problems = []
    for pool, text, key in expected:
        nodes = pool.get(text)
        if not nodes:
            problems.append(f"{text!r}: no element")
        elif not all(n.get("data-glossary") == key for n in nodes):
            got = [n.get("data-glossary") for n in nodes]
            problems.append(f"{text!r}: data-glossary is {got}, not {key!r}")
        if glossary.select_one(f'p[data-term="{key}"]') is None:
            problems.append(f"{key} is not a Glossary line")
    assert not problems, "\n".join(problems)


def test_every_g3_key_of_the_five_hosts_is_a_line_of_a_glossary_of_the_host_it_names():
    problems = []
    for row in copy.title_rows():
        if row["host"] not in HOSTS and row["host"] != "Settings":
            continue
        keys = {line["key"] for line in copy.glossary(row["host"])}
        for line_key in row["line_keys"]:
            if line_key not in keys:
                problems.append(f"{row['key']}: {line_key} is not in the {row['host']} Glossary")
    assert not problems, "\n".join(problems)
