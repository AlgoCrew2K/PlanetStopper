"""Performance and History say what each section is, define their terms and name things alike.

Subject: ``GET /performance`` and ``GET /history`` (the server render) and ``static/performance.js``
and ``static/history.js`` (the script-built labels). Every expected text is read from
``tests/fixtures/page_explainers/copy.json`` through ``tests/ui/_page_copy.py``; no test types a row,
and a slot is filled here from the authority the test patched, in the Values table's format.

CONTRACT PINNED
* Each description row of Copy table A for the two hosts is one element carrying
  ``data-explainer="<key>"`` with this plan's exact text, directly under its section heading, in the
  host's note class (``.metrics-caption`` on Performance, ``.hp-note`` on History), outside every
  container a script rewrites.
* Every definition is a line of the page's one Glossary (``tests/ui/test_host_glossaries_render.py``
  reads the lines); no section carries a disclosure of its own, and the one carried row, Y16, is a
  Glossary line now.
* The configured trading-cost value reaches the F24 definition from ``autotuner.SIM_EXIT_FRICTION_PCT``
  as a plain number; a value that is not a finite number, or an authority that raises, replaces the
  whole phrase (never the slot alone) and the page still renders.
* Each Copy table C row shows its new text where the server renders it and where a script writes it,
  the old text appears nowhere on the host (text, ``data-label``, ``title`` and ``aria-label``
  included), and every ``data-testid``, id, ``data-label`` other than the one History exits label,
  and JSON key survives the rewording.
* History reason names come from a display map; the stored and API reason strings stay as sent, an
  unmapped reason is shown as sent, and each of the four reasons carries its own definition as a line
  of the History Glossary (no "What the exit reasons mean" disclosure remains).
* History's ``.hp-more`` rule lives in the shared stylesheet, not in the History template.
"""

from __future__ import annotations

import copy
import json
import math
import os
import pathlib
import re

import pytest
from bs4 import BeautifulSoup

import analytics
import app as app_module
from tests.ui import _history_answers as hist
from tests.ui import _page_copy as pc
from tests.ui import _performance_page as perf
from tests.ui._page_js_harness import page_scripts, render_page, route, run_page
from tests.ui._shell_support import (  # noqa: F401  (fixtures: autouse overrides)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    seed_history_exits,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
STATIC = REPO / "static"

DESCRIPTION_KEYS = {
    "/performance": ("F01", "F11", "F13", "F16", "F21"),
    "/history": ("Y01", "Y10", "Y12", "Y15"),
}
HOST_OF_PATH = {"/performance": "Performance", "/history": "History"}
NOTE_CLASS = {"/performance": "metrics-caption", "/history": "hp-note"}
RELABEL_KEYS = {
    "/performance": ("F04", "F10", "F12a", "F12b", "F14a", "F14b", "F22r", "NM7"),
    "/history": ("Y02", "Y06", "Y14a"),
}
SUMMARY_TEXT = "What the terms mean"

# The section each description sits in, and how it sits: ("heading", the element just above it) or
# ("panel top", no heading: it precedes the first control row).
PLACEMENT = {
    "F01": ('[data-testid="perf-controls-panel"]', "panel top", ".hp-toolbar"),
    "F11": ('[data-testid="perf-chart-block"]', "heading", ".hp-head"),
    "F13": ('[data-testid="metrics-table"]', "heading", "h2"),
    "F16": ('[data-testid="guard-alpha-preconditions-panel"]', "heading", "h2"),
    "F21": ('[data-testid="exit-turnover-panel"]', "heading", "h2"),
    "Y01": ('[data-testid="history-controls"]', "panel top", '[data-testid="history-caption"]'),
    "Y10": ('[data-testid="daily-strip"]', "heading", ".hp-head"),
    "Y12": ('[data-testid="by-reason-section"]', "heading", '[data-testid="by-reason-header"]'),
    "Y15": ('[data-testid="recent-triggers"]', "heading", ".hp-head"),
}
# Containers a script rewrites with innerHTML: no description or disclosure may sit inside one.
REWRITTEN = ("tbody", "#reason-cards", "#triggers-tbody", ".hp-scroll")


def norm(text: str) -> str:
    return " ".join(text.split())


def soup_of(client, path: str) -> BeautifulSoup:
    response = client.get(path)
    assert response.status_code == 200, f"GET {path} answered {response.status_code}"
    return BeautifulSoup(response.get_data(as_text=True), "html.parser")


def filled(key: str, table: str, **slots: str) -> str:
    """The expected text of a row: this plan's text for a description (table A), the reference
    row's for a definition or a relabelled row."""
    text = pc.a_text(key) if table == "A" else pc.text(key, table)
    return norm(pc.fill(text, **slots))


def all_visible_strings(soup: BeautifulSoup) -> list[str]:
    """Every text a reader sees or a tool reads: text nodes plus label-like attributes."""
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    strings = [norm(s) for s in soup.stripped_strings]
    for tag in soup.find_all(True):
        for attribute in ("data-label", "title", "aria-label", "aria-description", "alt"):
            if tag.has_attr(attribute):
                strings.append(norm(str(tag[attribute])))
    return strings


def old_pieces(record: dict) -> list[str]:
    """The old texts a Copy table C row names (a row may name several, joined by "; " or ", ")."""
    old = pc.fill(
        record["old_text"], min_history_days=str(app_module._PERFORMANCE_MIN_HISTORY_DAYS)
    )
    separator = ", " if record["key"] == "Y14a" else "; "
    return [piece.strip() for piece in old.split(separator) if piece.strip()]


def new_pieces(record: dict) -> list[str]:
    separator = ", " if record["key"] == "Y14a" else "; "
    return [piece.strip() for piece in record["new_text"].split(separator) if piece.strip()]


# ---------------------------------------------------------------------------
# The fixture says what these tests assume of it
# ---------------------------------------------------------------------------


class TestFixturePremises:
    @pytest.mark.parametrize("path", sorted(DESCRIPTION_KEYS))
    def test_every_description_row_is_in_table_a_for_its_host(self, path):
        rows = [r for r in pc.a_rows(HOST_OF_PATH[path]) if r["kind"] not in ("removed", "moved")]
        assert {r["key"] for r in rows} == set(DESCRIPTION_KEYS[path])
        for key in DESCRIPTION_KEYS[path]:
            assert pc.host_of(pc.a_row(key)) == HOST_OF_PATH[path], key

    @pytest.mark.parametrize("path", sorted(RELABEL_KEYS))
    def test_every_relabel_row_is_in_table_c_for_its_host(self, path):
        for key in RELABEL_KEYS[path]:
            record = pc.row(key, "C")
            assert (record.get("host_place") or "").startswith(HOST_OF_PATH[path]), key

    @pytest.mark.parametrize("path", sorted(DESCRIPTION_KEYS))
    def test_the_host_has_a_glossary_and_each_of_its_lines_has_a_key_of_its_own(self, path):
        lines = pc.glossary(HOST_OF_PATH[path])
        assert lines, "premise: the host has Glossary lines"
        keys = [line["key"] for line in lines]
        assert len(keys) == len(set(keys))

    def test_the_carried_row_y16_is_a_g2_glossary_line_of_its_own(self):
        line = next(x for x in pc.glossary("History") if x["key"] == "Y16")
        assert line["source"] == "G2"
        assert line["text"].startswith("Detail: ")


# ---------------------------------------------------------------------------
# Copy table A: descriptions
# ---------------------------------------------------------------------------


CASES_A = [(p, k) for p, keys in sorted(DESCRIPTION_KEYS.items()) for k in keys]


@pytest.mark.parametrize(("path", "key"), CASES_A, ids=[f"{p}-{k}" for p, k in CASES_A])
class TestDescriptions:
    def test_the_description_is_one_element_with_the_rows_exact_text(self, client, path, key):
        soup = soup_of(client, path)
        found = soup.select(f'[data-explainer="{key}"]')
        assert len(found) == 1, f"{len(found)} elements carry data-explainer={key}"
        assert norm(found[0].get_text()) == filled(key, "A")

    def test_it_is_a_paragraph_or_block_in_the_hosts_note_class(self, client, path, key):
        element = soup_of(client, path).select_one(f'[data-explainer="{key}"]')
        assert element is not None and element.name in ("p", "div")
        assert NOTE_CLASS[path] in element.get("class", [])

    def test_it_sits_in_its_section_directly_under_its_heading(self, client, path, key):
        soup = soup_of(client, path)
        element = soup.select_one(f'[data-explainer="{key}"]')
        assert element is not None
        panel_selector, how, anchor = PLACEMENT[key]
        panel = soup.select_one(panel_selector)
        assert panel is not None
        assert element in panel.descendants, f"{key} is not inside {panel_selector}"
        if how == "heading":
            above = element.find_previous_sibling()
            assert above is not None and above in panel.select(anchor), (
                f"{key} is not directly under {anchor}: it follows {above.name if above else None}"
            )
        else:
            anchor_element = panel.select_one(anchor)
            assert anchor_element is not None
            order = list(panel.descendants)
            assert order.index(element) < order.index(anchor_element), (
                f"{key} must come before {anchor}"
            )

    def test_it_is_outside_every_container_a_script_rewrites(self, client, path, key):
        element = soup_of(client, path).select_one(f'[data-explainer="{key}"]')
        assert element is not None
        for selector in REWRITTEN:
            assert not _inside(element, selector), f"{key} sits inside {selector}"


def _inside(element, selector: str) -> bool:
    """True when ``element`` has an ancestor matching ``selector`` (same parse tree)."""
    root = element
    while root.parent is not None:
        root = root.parent
    return any(element in candidate.descendants for candidate in root.select(selector))


# ---------------------------------------------------------------------------
# Copy table B: definitions
# ---------------------------------------------------------------------------


def friction_text(row_key: str, value: str | None) -> str:
    record = pc.row(row_key, "B")
    base = pc.text(row_key, "B")
    if value is None:
        slot = pc.slot_row("friction_pct", row_key)
        return norm(base.replace(slot["phrase"], slot["fallback"]))
    return norm(pc.fill(base, friction_pct=value))


@pytest.fixture
def friction(monkeypatch):
    """A trading cost the page cannot have hard-coded (the production constant is another number)."""
    import autotuner

    value = 0.25
    assert value != autotuner.SIM_EXIT_FRICTION_PCT, (
        "premise: the test value differs from the constant"
    )
    monkeypatch.setattr(autotuner, "SIM_EXIT_FRICTION_PCT", value)
    return "0.25"


class TestDefinitions:
    """Each definition is a line of the page's Glossary, with the trading cost filled from its
    authority; the Glossary's placement and order are read by test_host_glossaries_render."""

    @pytest.mark.parametrize("path", sorted(DESCRIPTION_KEYS))
    def test_no_section_carries_a_disclosure_of_its_own(self, client, friction, path):
        soup = soup_of(client, path)
        assert not soup.select("[data-terms]:not([data-terms='glossary'])")
        assert not [s for s in soup.select("summary") if norm(s.get_text()) == SUMMARY_TEXT]
        for selector in REWRITTEN:
            for container in soup.select(selector):
                assert not container.select("[data-explainer], [data-terms], [data-term]"), (
                    f"{path}: a script rewrites {selector}, so nothing may be placed in it"
                )

    @pytest.mark.parametrize("path", sorted(DESCRIPTION_KEYS))
    def test_a_definition_appears_once_on_its_host_in_the_glossary(self, client, friction, path):
        soup = soup_of(client, path)
        host = HOST_OF_PATH[path]
        glossary = soup.select_one(f'details[data-terms="glossary"][data-glossary-host="{host}"]')
        assert glossary is not None, f"{host} has no Glossary"
        keys = [t.get("data-term") for t in soup.select("[data-term]")]
        assert len(keys) == len(set(keys)), "a term is defined twice on one page"
        assert keys == [t.get("data-term") for t in glossary.select("[data-term]")]
        assert keys == [line["key"] for line in pc.glossary(host)]

    @pytest.mark.parametrize("path", sorted(DESCRIPTION_KEYS))
    def test_a_slot_line_reads_the_patched_trading_cost(self, client, friction, path):
        soup = soup_of(client, path)
        for line in pc.glossary(HOST_OF_PATH[path]):
            if "friction_pct" in line["slots"]:
                node = soup.select_one(f'[data-term="{line["key"]}"]')
                assert node is not None, line["key"]
                assert norm(node.get_text()) == norm(
                    pc.fill(line["text"], friction_pct=friction)
                ), line["key"]

    def test_the_history_detail_line_is_a_glossary_line_and_the_exits_description_stays_short(
        self, client
    ):
        soup = soup_of(client, "/history")
        line = next(x for x in pc.glossary("History") if x["key"] == "Y16")
        assert norm(soup.select_one('[data-term="Y16"]').get_text()) == norm(line["text"])
        assert norm(soup.select_one('[data-explainer="Y15"]').get_text()) == norm(pc.a_text("Y15"))


# ---------------------------------------------------------------------------
# The trading-cost slot
# ---------------------------------------------------------------------------


def f24_text(client) -> str:
    term = soup_of(client, "/performance").select_one('[data-term="F24"]')
    assert term is not None, "the F24 definition is not on the page"
    return norm(term.get_text())


class TestTradingCostSlot:
    @pytest.mark.parametrize(
        ("authority", "printed"),
        [(0.25, "0.25"), (2.0, "2"), (0.5, "0.5"), (1, "1")],
        ids=["fraction", "whole float", "half", "int"],
    )
    def test_a_finite_number_prints_plain(self, client, monkeypatch, authority, printed):
        import autotuner

        monkeypatch.setattr(autotuner, "SIM_EXIT_FRICTION_PCT", authority)
        assert f24_text(client) == friction_text("F24", printed)

    @pytest.mark.parametrize(
        "authority",
        [math.nan, math.inf, -math.inf, "0.5", None, True],
        ids=["nan", "inf", "-inf", "string", "none", "bool"],
    )
    def test_a_value_that_is_not_a_finite_number_replaces_the_whole_phrase(
        self, client, monkeypatch, authority
    ):
        import autotuner

        monkeypatch.setattr(autotuner, "SIM_EXIT_FRICTION_PCT", authority)
        text = f24_text(client)
        assert text == friction_text("F24", None)
        assert "{" not in text and "None" not in text and "nan" not in text.lower()

    def test_an_authority_that_raises_replaces_the_phrase_and_the_page_still_renders(
        self, client, monkeypatch
    ):
        def boom():
            raise RuntimeError("authority unavailable")

        monkeypatch.setattr(app_module, "_copy_friction_pct", boom)
        assert client.get("/performance").status_code == 200
        assert f24_text(client) == friction_text("F24", None)

    def test_a_failed_slot_changes_no_other_definition(self, client, monkeypatch):
        baseline = {
            t.get("data-term"): norm(t.get_text())
            for t in soup_of(client, "/performance").select("[data-term]")
        }
        monkeypatch.setattr(app_module, "_copy_friction_pct", lambda: None)
        after = {
            t.get("data-term"): norm(t.get_text())
            for t in soup_of(client, "/performance").select("[data-term]")
        }
        assert "F24" in baseline, "premise: the F24 definition is on the page"
        changed = {k for k in baseline if baseline[k] != after[k]}
        assert changed == {"F24"}, changed

    def test_the_value_is_not_hard_coded_into_the_template(self, client, monkeypatch):
        import autotuner

        monkeypatch.setattr(autotuner, "SIM_EXIT_FRICTION_PCT", 0.75)
        assert "0.75" in f24_text(client)
        monkeypatch.setattr(autotuner, "SIM_EXIT_FRICTION_PCT", 0.3)
        text = f24_text(client)
        assert "0.3" in text and "0.75" not in text

    def test_history_defines_no_slot(self, client):
        for term in soup_of(client, "/history").select("[data-term]"):
            assert "{" not in term.get_text()


# ---------------------------------------------------------------------------
# Copy table C, server render
# ---------------------------------------------------------------------------


class TestPerformanceRelabelsServerRendered:
    def test_basis_caption_names_the_series_as_the_tables_row_words_it(self, client):
        soup = soup_of(client, "/performance")
        captions = [norm(p.get_text()) for p in soup.select(".legend-as-of")]
        assert norm(pc.text("F04", "C")) in captions

    def test_chart_legend_names_the_two_series(self, client):
        soup = soup_of(client, "/performance")
        labels = [
            norm(s.get_text())
            for s in soup.select('[data-testid="perf-chart-legend"] .legend-item span')
        ]
        assert labels == [norm(pc.text("F12a", "C")), norm(pc.text("F12b", "C"))]

    def test_metrics_table_headers_name_the_two_columns(self, client):
        soup = soup_of(client, "/performance")
        heads = [
            norm(th.get_text()) for th in soup.select('[data-testid="metrics-table"] thead th')
        ]
        assert heads[1] == norm(pc.text("F14a", "C"))
        assert heads[2] == norm(pc.text("F14b", "C"))
        assert heads[0] == "Metric" and heads[3].startswith("Delta")

    def test_the_last_placeholder_row_is_up_days(self, client):
        soup = soup_of(client, "/performance")
        rows = soup.select('#metrics-tbody tr[data-testid="metric-row"]')
        assert len(rows) == 10
        assert norm(rows[-1].find("td").get_text()) == norm(pc.text("NM7", "C"))

    def test_turnover_heading_and_header_say_trading_cost(self, client):
        soup = soup_of(client, "/performance")
        panel = soup.select_one('[data-testid="exit-turnover-panel"]')
        heading, header = new_pieces(pc.row("F22r", "C"))
        assert norm(panel.find("h2").get_text()) == heading
        heads = [norm(th.get_text()) for th in panel.select("thead th")]
        assert heads[-1] == header

    def test_the_banner_names_risk_metrics_and_prints_the_context_number(self, client):
        soup = soup_of(client, "/performance")
        banner = soup.select_one('[data-testid="insufficient-banner"]')
        expected = norm(
            pc.fill(
                pc.text("F10", "C"), min_history_days=str(app_module._PERFORMANCE_MIN_HISTORY_DAYS)
            )
        )
        text = norm(banner.get_text())
        assert text.startswith("Insufficient history.")
        assert text.endswith(expected), text

    def test_the_banner_prints_the_number_the_route_passes_not_a_literal(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "_PERFORMANCE_MIN_HISTORY_DAYS", 47)
        banner = soup_of(client, "/performance").select_one('[data-testid="insufficient-banner"]')
        assert norm(banner.get_text()).endswith(
            norm(pc.fill(pc.text("F10", "C"), min_history_days="47"))
        )


class TestHistoryRelabelsServerRendered:
    def test_the_caption_reads_totals_from_daily_exit_records(self, client):
        soup = soup_of(client, "/history")
        caption = soup.select_one('[data-testid="history-caption"]')
        assert norm(caption.get_text()) == norm(pc.x_row("CX-8")["new_text"])
        assert norm(pc.text("Y02", "C")) not in norm(soup.get_text()), "the longer caption is back"

    def test_the_exits_count_is_named_exits_in_the_hero_the_header_and_every_cell_label(
        self, client
    ):
        soup = soup_of(client, "/history")
        new = norm(pc.text("Y06", "C"))
        assert (
            norm(soup.select_one('[data-testid="stat-trigger-count"] .hp-fig-label').get_text())
            == new
        )
        heads = [norm(th.get_text()) for th in soup.select(".hp-reasons thead th")]
        assert heads[3] == new
        labels = [
            td.get("data-label") for td in soup.select("#reason-cards tr")[0].find_all("td")[1:]
        ]
        assert labels[2] == new
        for row in soup.select("#reason-cards tr"):
            assert row.find_all("td")[3].get("data-label") == new

    def test_the_server_render_carries_no_reason_definitions_disclosure(self, client):
        """No disclosure on History reads "What the exit reasons mean": the four definitions are
        Glossary lines, so the page's only disclosure is its Glossary."""
        soup = soup_of(client, "/history")
        summaries = [norm(s.get_text()) for s in soup.select("details > summary")]
        assert REASON_DISCLOSURE_SUMMARY not in summaries, summaries
        assert REASON_DISCLOSURE_SUMMARY not in norm(soup.get_text())
        assert summaries == ["Glossary"], summaries

    def test_the_four_placeholder_reasons_carry_their_display_names_in_order(self, client):
        soup = soup_of(client, "/history")
        names = [norm(row.find("td").get_text()) for row in soup.select("#reason-cards tr")]
        assert names == new_pieces(pc.row("Y14a", "C"))


@pytest.mark.parametrize("path", sorted(RELABEL_KEYS))
def test_no_old_text_of_the_host_remains_in_the_render(client, path):
    soup = soup_of(client, path)
    strings = all_visible_strings(soup)
    for key in RELABEL_KEYS[path]:
        for old in old_pieces(pc.row(key, "C")):
            hits = [s for s in strings if old in s]
            assert not hits, f"{key}: old text {old!r} remains in {hits[:2]}"


# ---------------------------------------------------------------------------
# Contract names survive the rewording
# ---------------------------------------------------------------------------


class TestContractNamesSurvive:
    def test_history_keeps_its_testids_ids_and_data_labels_but_the_one_renamed_label(self, client):
        soup = soup_of(client, "/history")
        assert soup.select_one('[data-testid="stat-trigger-count"]') is not None
        assert soup.select_one("#val-trigger-count") is not None
        assert len(soup.select('#reason-cards tr[data-testid="reason-card"]')) == 4
        first = soup.select("#reason-cards tr")[0]
        labels = [td.get("data-label") for td in first.find_all("td")[1:]]
        assert labels == [
            "Sum of exit alpha",
            "Dollar result",
            norm(pc.text("Y06", "C")),
            "Wins",
            "Win rate",
            "Avg per exit",
        ]
        assert soup.select_one("#reason-descriptions") is None, "the reasons disclosure is back"
        assert soup.select_one('[data-testid="reason-descriptions"]') is None

    def test_performance_keeps_ten_metric_rows_and_its_testids(self, client):
        soup = soup_of(client, "/performance")
        assert len(soup.select('#metrics-tbody [data-testid="metric-row"]')) == 10
        for testid in (
            "metrics-table",
            "perf-chart-legend",
            "insufficient-banner",
            "exit-turnover-table",
            "guard-alpha-preconditions-table",
            "headline-strip",
        ):
            assert soup.select_one(f'[data-testid="{testid}"]') is not None, testid

    def test_the_metric_key_for_the_renamed_row_is_unchanged_in_the_script(self):
        script = (STATIC / "performance.js").read_text(encoding="utf-8")
        assert "'win_rate'" in script, "the win_rate key is a contract name and stays"


class TestSharedDisclosureRule:
    def test_history_defines_no_hp_more_rule_of_its_own(self, client):
        for path in ("/history", "/performance"):
            styles = "\n".join(s.get_text() for s in soup_of(client, path).find_all("style"))
            assert ".hp-more" not in styles, f"{path} still carries a page-local .hp-more rule"

    def test_the_shared_sheet_defines_the_rule_and_both_pages_link_it(self, client):
        css = (STATIC / "layout.css").read_text(encoding="utf-8")
        assert re.search(r"\.hp-more\b", css), "layout.css has no .hp-more rule"
        for path in ("/history", "/performance"):
            hrefs = [
                link.get("href", "")
                for link in soup_of(client, path).find_all("link", rel="stylesheet")
            ]
            assert any("layout.css" in href for href in hrefs), path


# ---------------------------------------------------------------------------
# Script-built labels (node harness over the page and the real routes' bodies)
# ---------------------------------------------------------------------------


def warm_market_calendar() -> None:
    """Build the market calendar under the real clock: ``perf.get_real`` answers under a patched
    ``datetime`` class, which the calendar's first build cannot use."""
    with app_module.app.test_client() as warm:
        assert warm.get("/performance").status_code == 200


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(analytics, "DB_FILE", os.environ["DB_PATH"])
    state = perf.seed_world()
    warm_market_calendar()
    return state


PERFORMANCE_STATE = """
  const state = () => ({
    metricLabels: H.$$('#metrics-tbody tr').map((tr) => tr.children[0].textContent.trim()),
    headers: H.$$('[data-testid="metrics-table"] thead th').map((th) => th.textContent.trim()),
    legend: H.$$('[data-testid="perf-chart-legend"] .legend-item span').map((s) => s.textContent.trim()),
    page: H.$('.page-wrap').textContent,
    rowTestids: H.$$('#metrics-tbody tr').map((tr) => tr.getAttribute('data-testid')),
    charts: H.charts().map((c) => c.data.datasets.map((d) => ({label: d.label, dashed: !!d.borderDash}))),
  });
"""


class TestPerformanceScriptBuilt:
    def run(self):
        # The stub Chart class stands in for the vendored library, which would otherwise be loaded
        # over it and refuse the stub DOM's canvas.
        html = render_page(perf.PERFORMANCE_PATH)
        scripts = [name for name in page_scripts(html) if "chart.js" not in name]
        return run_page(
            html,
            "await H.boot();\n" + PERFORMANCE_STATE + "return state();",
            routes=perf.real_routes(),
            scripts=scripts,
            pathname=perf.PERFORMANCE_PATH,
            chart="stub",
        ).result

    def test_the_chart_datasets_name_if_held_and_the_bot_shadow_record(self, world):
        datasets = self.run()["charts"][0]
        by_style = {d["dashed"]: d["label"] for d in datasets}
        assert by_style[True] == norm(pc.text("F12b", "C")), "the dashed series is the if-held path"
        assert by_style[False] == norm(pc.text("F12a", "C")), (
            "the solid series is the bot's shadow record"
        )
        assert "(shadow)" in by_style[False]

    def test_script_and_server_legends_say_the_same_thing(self, world):
        out = self.run()
        assert out["legend"] == [norm(pc.text("F12a", "C")), norm(pc.text("F12b", "C"))]
        assert out["headers"][1:3] == [norm(pc.text("F14a", "C")), norm(pc.text("F14b", "C"))]

    def test_the_script_written_metric_rows_end_with_up_days_and_keep_their_marker(self, world):
        out = self.run()
        assert out["metricLabels"][-1] == norm(pc.text("NM7", "C"))
        assert len(out["metricLabels"]) == 10
        assert set(out["rowTestids"]) == {"metric-row"}

    def test_no_old_performance_text_is_on_the_page_after_the_script_ran(self, world):
        out = self.run()
        page = norm(out["page"])
        for key in RELABEL_KEYS["/performance"]:
            for old in old_pieces(pc.row(key, "C")):
                assert old not in page, f"{key}: {old!r} is still on the page"
        for old in ("Win rate", "Planet Stopper"):
            assert old not in page
        for dataset in out["charts"][0]:
            assert "Planet Stopper" not in dataset["label"] and "Live" not in dataset["label"]

    def test_the_script_source_holds_no_old_series_or_metric_name(self):
        script = (STATIC / "performance.js").read_text(encoding="utf-8")
        for old in ("Live (if held)", "Planet Stopper", "'Win rate'", "friction drag"):
            assert old not in script, old


def seed_history_with_all_four_reasons(tmp_path, monkeypatch) -> str:
    """The captured post-mortem days, one captured Take-Profit exit of 2026-06-23 relabelled.

    Provenance: ``tests/ui/_shell_support.seed_history_exits`` writes the captured days; none of
    them holds a Trailing Stop exit, so the one field ``exit_reason`` of one captured trigger is
    changed to the stored value ``Trailing Stop``. The route body the page reads is the real
    route's over those files. Returns the day that then holds all four reasons.
    """
    warm_market_calendar()
    seed_history_exits(tmp_path, monkeypatch.setattr)
    day = "2026-06-23"
    path = pathlib.Path(analytics._POST_MORTEMS_DIR) / f"post_mortem_{day}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    target = next(t for t in record["triggers"] if t["exit_reason"] == "Take-Profit")
    target["exit_reason"] = "Trailing Stop"
    path.write_text(json.dumps(record), encoding="utf-8")
    return day


HISTORY_ROUTE = r"/api/history/(\d+|ytd)$"
HISTORY_STATE = """
  const cell = (td) => ({text: td.textContent.trim(), label: td.getAttribute('data-label')});
  const state = () => ({
    reasonRows: H.$$('#reason-cards tr').map((tr) => tr.children.map(cell)),
    hero: H.text('[data-testid="stat-trigger-count"]'),
    disclosures: H.$$('details').map((e) => (e.querySelector('summary') || e).textContent.trim()),
    reasonDisclosure: H.$$('#reason-descriptions, [data-testid="reason-descriptions"]').length,
    glossary: H.$$('details[data-terms="glossary"] p').map((e) => e.textContent.trim()),
    exits: H.$$('#triggers-tbody tr').map((tr) => tr.children.map((td) => td.textContent.trim())),
    section: H.$('[data-testid="by-reason-section"]').innerHTML,
    page: H.$('.page-wrap').textContent,
  });
"""


# The reasons in the page's order: Take-profit, Trailing stop, VWAP breakdown, VWAP bleed.
REASON_GLOSSARY_KEYS = ("D29b", "D29a", "D29c", "D29d")
REASON_DISCLOSURE_SUMMARY = "What the exit reasons mean"


def history_glossary_texts() -> list[str]:
    """The History Glossary's lines in order, from the copy fixture."""
    return [norm(line["text"]) for line in pc.glossary("History")]


class TestHistoryScriptBuilt:
    def run(self, tmp_path, monkeypatch, scenario_tail: str, *, edit=None):
        day = seed_history_with_all_four_reasons(tmp_path, monkeypatch)
        body = hist.history_answer(90)
        if edit:
            edit(body["json"], day)
        scenario = "await H.boot();\n" + HISTORY_STATE + scenario_tail.replace("__DAY__", day)
        return run_page(
            render_page("/history"),
            scenario,
            routes=[route(HISTORY_ROUTE, body, method="GET", strict=False)],
        ).result

    def test_script_rows_carry_the_display_names_and_the_exits_label(self, tmp_path, monkeypatch):
        out = self.run(tmp_path, monkeypatch, "return state();")
        names = [row[0]["text"] for row in out["reasonRows"]]
        assert names == new_pieces(pc.row("Y14a", "C")), "all four reasons, by display name"
        new_label = norm(pc.text("Y06", "C"))
        for row in out["reasonRows"]:
            assert [c["label"] for c in row[1:]] == [
                "Sum of exit alpha",
                "Dollar result",
                new_label,
                "Wins",
                "Win rate",
                "Avg per exit",
            ]

    def test_the_hero_label_is_the_same_word_the_script_rows_use(self, tmp_path, monkeypatch):
        out = self.run(tmp_path, monkeypatch, "return state();")
        assert out["hero"].startswith(norm(pc.text("Y06", "C")))

    def test_the_script_built_page_carries_no_reason_definitions_disclosure(
        self, tmp_path, monkeypatch
    ):
        """The page script no longer fills a "What the exit reasons mean" disclosure: neither the
        element nor its summary text is on the page after a window with all four reasons draws."""
        out = self.run(tmp_path, monkeypatch, "return state();")
        assert out["reasonDisclosure"] == 0, "the reasons disclosure element is on the page"
        assert REASON_DISCLOSURE_SUMMARY not in out["disclosures"], out["disclosures"]
        assert REASON_DISCLOSURE_SUMMARY not in norm(out["page"]) + " " + out["section"]

    def test_the_four_reason_definitions_are_glossary_lines_led_by_the_reason_names(
        self, tmp_path, monkeypatch
    ):
        """S-4.16: the History Glossary holds D29b, D29a, D29c and D29d in that order, each reading
        its reference definition word for word with the reason's display name as the lead term."""
        out = self.run(tmp_path, monkeypatch, "return state();")
        lines = pc.glossary("History")
        assert [line["key"] for line in lines if line["key"] in REASON_GLOSSARY_KEYS] == list(
            REASON_GLOSSARY_KEYS
        )
        reason_lines = [line for line in lines if line["key"] in REASON_GLOSSARY_KEYS]
        for line in reason_lines:
            assert line["text"] == pc.text(line["key"], "B"), (
                f"{line['key']} is not the reference definition word for word"
            )
        names = [row[0]["text"] for row in out["reasonRows"]]
        assert sorted(line["lead_term"] for line in reason_lines) == sorted(names), (
            "each reason's display name leads exactly one Glossary line"
        )
        assert out["glossary"] == history_glossary_texts(), (
            "the rendered History Glossary is not the copy table's lines in order"
        )
        assert len(set(line["text"] for line in reason_lines)) == 4, (
            "each reason has a definition of its own"
        )

    def test_the_exits_table_shows_display_names_for_every_stored_reason(
        self, tmp_path, monkeypatch
    ):
        scenario = "await H.click('#daily-chart-svg rect[data-date=\"__DAY__\"]'); return state();"
        out = self.run(tmp_path, monkeypatch, scenario)
        reasons = {row[2] for row in out["exits"]}
        assert reasons == set(new_pieces(pc.row("Y14a", "C"))), reasons

    def test_a_reason_the_map_does_not_know_is_shown_as_sent(self, tmp_path, monkeypatch):
        def edit(body, day):
            body["daily_exits"][day][0]["reason"] = "Manual override"

        scenario = "await H.click('#daily-chart-svg rect[data-date=\"__DAY__\"]'); return state();"
        out = self.run(tmp_path, monkeypatch, scenario, edit=edit)
        assert "Manual override" in {row[2] for row in out["exits"]}

    def test_no_old_history_text_is_in_the_script_written_page(self, tmp_path, monkeypatch):
        scenario = "await H.click('#daily-chart-svg rect[data-date=\"__DAY__\"]'); return state();"
        out = self.run(tmp_path, monkeypatch, scenario)
        haystack = norm(out["page"]) + " " + out["section"]
        for key in RELABEL_KEYS["/history"]:
            for old in old_pieces(pc.row(key, "C")):
                assert old not in haystack, f"{key}: {old!r} is still shown"
        assert 'data-label="Triggers"' not in out["section"]

    def test_contract_markers_of_the_script_rows_survive(self, tmp_path, monkeypatch):
        out = self.run(tmp_path, monkeypatch, "return state();")
        assert out["section"].count('data-testid="reason-card"') == 4
        assert out["section"].count('data-testid="avg-per-exit"') == 4
        assert out["section"].count('data-testid="reason-bar"') == 4


def test_the_api_history_keys_are_unchanged(tmp_path, monkeypatch):
    """The reasons stay as stored in the route body: ``by_reason`` keys and each exit's ``reason``."""
    day = seed_history_with_all_four_reasons(tmp_path, monkeypatch)
    body = copy.deepcopy(hist.history_body(90))
    assert set(body["by_reason"]) == {
        "Take-Profit",
        "Trailing Stop",
        "VWAP Breakdown",
        "VWAP Bleed Cut",
    }
    assert {e["reason"] for e in body["daily_exits"][day]} <= set(body["by_reason"])
    for key in ("daily_dates", "daily_alpha", "daily_exits", "trigger_count", "by_reason"):
        assert key in body, key
