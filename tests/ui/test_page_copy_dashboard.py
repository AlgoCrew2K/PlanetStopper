"""The dashboard, its detail panel and its header say what they are, in the approved words.

Rendered through the Flask test client over the seeded worlds of ``tests/ui/_page_explainers.py``
(captured records written through the real writers; the app clock and the CSRF token fixed). The
expected text of every row is read from ``tests/fixtures/page_explainers/copy.json`` through
``tests/ui/_page_copy.py`` and nowhere else; a slot is filled here, from the authority the test
patched, in the format the Values table states, and the production formatter is never called.

What is read, all from the server's markup (the scripts are not run here, so a text the page shows
only after a script ran cannot pass these):

* every description row of Copy table A for the host sits in an element marked ``data-explainer``,
  once, with the plan's exact text, in the place the copy table names; the two rows the plan removes
  (D01, the page line, and D20, the Tracked, Armed, Triggered line) are on no page, with their old
  words;
* every term definition is a line of the host's one Glossary (``tests/ui/test_host_glossaries_render.py``
  reads the Glossaries; this module reads the lines it still depends on through their keys);
* a configured value (the shadow-history retention, the exit trading cost) prints the value its
  authority holds, and the whole phrase that holds it is replaced when the authority cannot be read;
* every reworded label shows its new text and its old text is gone;
* the three controls no script handled are gone and the close control stays.

The browser readings (visibility, opening a disclosure, the poll rewriting the same words) are in
``tests/ui/test_page_explainers_dashboard_browser.py``.
"""

from __future__ import annotations

import re

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _page_copy as copy
from tests.ui import _page_explainers as harness
from tests.ui._shell_support import (  # noqa: F401  (the overrides and the empty directory)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
)

# The page line (D01) and the Tracked, Armed, Triggered line (D20) are removed: their keys are in
# REMOVED_DESCRIPTIONS and every other row of the hosts reads Copy table A's new text.
REMOVED_DESCRIPTIONS = ("D01", "D20")
DASHBOARD_DESCRIPTIONS = (
    "dash.hero",
    "dash.rows",
    "D21",
    "D28",
    "D40",
    "D41",
    "D47",
    "D48",
)
DETAIL_DESCRIPTIONS = ("dp.lead", "D57", "dp.events", "D59", "D66", "D67")
ALL_DESCRIPTIONS = DASHBOARD_DESCRIPTIONS + DETAIL_DESCRIPTIONS
WORDS_FOR_RELABEL_ROWS = (
    "D30a",
    "D30b",
    "D30c",
    "D29v",
    "D68",
    "D03r",
    "D06c",
    "NM1",
    "NM2",
    "NM3",
    "NM4",
    "NM5",
    "NM6",
)
# Containers a script rewrites with innerHTML: nothing the page adds may sit inside one.
SCRIPT_REWRITTEN = "#detail-risk-profile, #events-timeline, #vars-section"


def _words(node) -> str:
    return " ".join(node.get_text().split())


def _without_glossaries(soup) -> str:
    """What a reader sees outside every Glossary: no script, style, template or comment."""
    from bs4 import BeautifulSoup, Comment

    clone = BeautifulSoup(str(soup), "html.parser")
    for dead in clone(["script", "style", "template"]):
        dead.decompose()
    for glossary in clone.select('details[data-terms="glossary"]'):
        glossary.decompose()
    for comment in clone.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    return " ".join(clone.get_text(" ").split())


def _plain(value: float) -> str:
    """A number with no trailing zeros, written here so the production formatter is never the oracle."""
    return f"{value:.6f}".rstrip("0").rstrip(".")


@pytest.fixture
def render(tmp_path, monkeypatch):
    """``render(world, env="", **patches)`` returns the page's ``(status, soup)``; pages are cached.

    ``env`` is the text of the env file the page reads; ``patches`` are attributes set on the
    ``autotuner`` module for the render. The seeded world is rebuilt for every distinct request.
    """
    from bs4 import BeautifulSoup

    import app as app_module

    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )
    env_path = tmp_path / "page.env"
    cache: dict = {}

    def make(world: str, env: str = "", market: str | None = None, **autotuner_patches):
        import autotuner

        key = (world, env, market, tuple(sorted(autotuner_patches.items(), key=lambda kv: kv[0])))
        if key not in cache:
            harness.seed_world(world, tmp_path, monkeypatch.setattr)
            env_path.write_text(env, encoding="utf-8")
            for name, value in autotuner_patches.items():
                monkeypatch.setattr(autotuner, name, value)
            if market is not None:
                monkeypatch.setattr(app_module, "get_market_state", lambda *_a, **_k: market)
            response = app_module.app.test_client().get("/")
            assert response.status_code == 200
            cache[key] = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        return cache[key]

    return make


def _position(element) -> tuple[int, int]:
    return (element.sourceline, element.sourcepos)


def _one(soup, selector: str):
    found = soup.select(selector)
    assert len(found) == 1, f"{len(found)} elements match {selector!r}, expected exactly one"
    return found[0]


def _explainer(soup, key: str):
    return _one(soup, f'[data-explainer="{key}"]')


# ---------------------------------------------------------------------------
# The rows the tests walk are the rows the host has
# ---------------------------------------------------------------------------


def test_the_description_rows_walked_here_are_every_description_row_of_the_two_hosts():
    rows = [r for r in copy.a_rows() if copy.host_of(r) in ("/", "detail panel")]
    shown = {r["key"] for r in rows if r["kind"] not in ("removed", "moved")}
    gone = {r["key"] for r in rows if r["kind"] in ("removed", "moved")}
    assert shown == set(ALL_DESCRIPTIONS)
    assert gone == set(REMOVED_DESCRIPTIONS)
    for key in REMOVED_DESCRIPTIONS:
        assert copy.a_text(key) is None, key


def test_the_relabel_rows_walked_here_are_every_relabel_row_of_the_dashboard_hosts():
    wanted = {
        r["key"]
        for r in copy.rows("C")
        if copy.host_of(r) in ("/", "detail panel")
        or r["key"] in ("D30b", "D30c")  # the verdict rows of the same card, listed beside D30a
    }
    assert wanted <= set(WORDS_FOR_RELABEL_ROWS), sorted(wanted - set(WORDS_FOR_RELABEL_ROWS))
    for key in WORDS_FOR_RELABEL_ROWS:
        assert copy.row(key, "C")["new_text"], key


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------


class TestDescriptions:
    @pytest.mark.parametrize("key", [k for k in ALL_DESCRIPTIONS])
    def test_a_description_shows_its_exact_text_once_in_an_element_marked_for_it(self, render, key):
        soup = render("dashboard_full")
        assert _words(_explainer(soup, key)) == copy.a_text(key)

    @pytest.mark.parametrize("world", ["empty", "dashboard_sleeves"])
    @pytest.mark.parametrize("key", [k for k in ALL_DESCRIPTIONS if k != "D48"])
    def test_every_section_that_renders_shows_its_description_in_every_state(
        self, render, world, key
    ):
        soup = render(world)
        assert _words(_explainer(soup, key)) == copy.a_text(key)

    def test_the_sleeves_empty_line_is_the_element_that_carries_its_row(self, render):
        soup = render("dashboard_full")
        assert _explainer(soup, "D48") is _one(soup, '[data-testid="sleeves-empty"]')
        assert not _explainer(soup, "D48").has_attr("hidden")

    def test_the_sleeves_empty_line_stays_hidden_while_a_sleeve_exists(self, render):
        soup = render("dashboard_sleeves")
        line = _explainer(soup, "D48")
        assert line.has_attr("hidden")
        assert _words(line) == copy.a_text("D48")

    @pytest.mark.parametrize("world", ["empty", "dashboard_full", "dashboard_sleeves"])
    @pytest.mark.parametrize("key", REMOVED_DESCRIPTIONS)
    def test_a_removed_description_is_on_no_page_and_its_old_words_are_gone(
        self, render, world, key
    ):
        soup = render(world)
        assert not soup.select(f'[data-explainer="{key}"]'), f"{key} is still on the page"
        # A Glossary line may carry the old words (D20t holds D20's sentences word for word), so
        # the page is read outside every Glossary.
        assert copy.text(key, "A") not in _without_glossaries(soup), (
            f"the old text of {key} is on the page"
        )

    @pytest.mark.parametrize("key", ["dash.hero", "dash.rows", "D21", "D28", "D40", "D41", "D47"])
    def test_a_shortened_description_drops_the_sentences_its_new_text_does_not_keep(
        self, render, key
    ):
        soup = render("dashboard_full")
        old = copy.text(key, "A")
        kept = {s for s in re.split(r"(?<=[.?!])\s+", copy.a_text(key)) if s}
        body = _without_glossaries(soup)
        for sentence in (s for s in re.split(r"(?<=[.?!])\s+", old) if s):
            if sentence not in kept:
                assert sentence not in body, f"{key}: the old sentence {sentence!r} is on the page"

    def test_the_cvar_description_sits_inside_the_panel_and_the_panel_is_hidden_without_a_diagnostic(
        self, render
    ):
        soup = render("empty")
        panel = _one(soup, "#cvar-diagnostic-panel")
        assert panel.has_attr("hidden")
        assert panel.select('[data-explainer="D41"]'), "the description is not inside the panel"
        shown = render("dashboard_full")
        assert not _one(shown, "#cvar-diagnostic-panel").has_attr("hidden")

    def test_the_two_cvar_notes_the_page_already_carried_are_still_the_only_other_notes(
        self, render
    ):
        soup = render("dashboard_full")
        notes = soup.select("#cvar-diagnostic-panel .dash-panel-note:not([data-explainer])")
        assert [_words(n) for n in notes] == [
            "diagnostic, not a signal — do not trade on this",
            "known-low-biased LOWER BOUND on tail severity, not a point estimate",
        ]

    @pytest.mark.parametrize(
        ("key", "inside", "after", "before"),
        [
            (
                "dash.hero",
                '[data-testid="hero-left"]',
                ".guard-alpha-label",
                '[data-testid="guard-alpha-descriptor"]',
            ),
            ("dash.rows", '[data-testid="hero-right"]', None, '[data-testid="vs-row"]'),
            (
                "D21",
                '[data-testid="dollar-saved-panel"]',
                None,
                '[data-testid="dollar-saved-headline"]',
            ),
            (
                "D28",
                '[data-testid="active-section"]',
                ".section-header",
                ".cards-grid",
            ),
            (
                "D40",
                '[data-testid="standby-section"]',
                ".section-header",
                ".cards-grid",
            ),
            ("D41", "#cvar-diagnostic-panel", ".dash-panel-title", ".cvar-figures"),
            (
                "D47",
                '[data-testid="sleeves-panel"]',
                ".dash-panel-head",
                '[data-testid="sleeves-empty"]',
            ),
            (
                "dp.lead",
                '[data-testid="detail-panel"]',
                "#detail-panel-title",
                '[data-testid="detail-stats-row"]',
            ),
            (
                "D57",
                '[data-testid="detail-panel"]',
                ".detail-section-sub",
                '[data-testid="intraday-chart"]',
            ),
            (
                "dp.events",
                '[data-testid="detail-panel"]',
                '[data-field="events-heading"]',
                "#events-timeline",
            ),
            (
                "D59",
                '[data-testid="detail-panel"]',
                None,
                "#dp-rm-mc-row",
            ),
            (
                "D66",
                '[data-testid="detail-panel"]',
                ".section-label",
                "#detail-risk-profile",
            ),
            ("D67", '[data-testid="detail-panel"]', None, "#vars-section"),
        ],
    )
    def test_a_description_sits_under_its_heading_and_above_what_it_describes(
        self, render, key, inside, after, before
    ):
        soup = render("dashboard_full")
        element = _explainer(soup, key)
        container = soup.select_one(inside)
        assert container is not None and container in element.parents, (
            f"{key} is not inside {inside}"
        )
        later = container.select_one(before)
        assert later is not None and _position(element) < _position(later), (
            f"{key} is not above {before}"
        )
        if after is not None:
            earlier = [e for e in container.select(after) if _position(e) < _position(element)]
            assert earlier, f"{key} is not below {after}"

    def test_the_detail_panel_headings_the_descriptions_sit_under_still_read_as_before(
        self, render
    ):
        soup = render("dashboard_full")
        panel = _one(soup, '[data-testid="detail-panel"]')
        assert _words(panel.select_one("#detail-panel-title")) == "Symphony detail"
        titles = [_words(t) for t in panel.select(".detail-section-title")]
        assert "Intraday tape" in titles and "Risk math" in titles
        assert "Symphony variables" in titles

    def test_no_description_or_disclosure_sits_inside_a_container_a_script_rewrites(self, render):
        soup = render("dashboard_full")
        for container in soup.select(SCRIPT_REWRITTEN):
            assert not container.select("[data-explainer], [data-terms], [data-term]"), (
                f"a script rewrites #{container.get('id')}, so nothing may be placed in it"
            )

    def test_a_description_is_page_text_and_not_only_an_attribute(self, render):
        soup = render("dashboard_full")
        for key in ALL_DESCRIPTIONS:
            element = _explainer(soup, key)
            assert _words(element) == copy.a_text(key)
            assert element.get("title") != copy.a_text(key)
            assert element.get("aria-label") != copy.a_text(key)


# ---------------------------------------------------------------------------
# Term definitions
# ---------------------------------------------------------------------------


GLOSSARY_HOSTS = ("/", "detail panel")


class TestTermDefinitions:
    """Every definition of the two hosts is a Glossary line, none a disclosure of its section."""

    def test_no_section_carries_a_disclosure_of_its_own(self, render):
        soup = render("dashboard_full")
        assert not soup.select("[data-terms]:not([data-terms='glossary'])")
        assert not [s for s in soup.select("summary") if _words(s) == "What the terms mean"]

    @pytest.mark.parametrize("host", GLOSSARY_HOSTS)
    def test_every_definition_of_the_host_is_a_line_of_its_glossary_defined_once(
        self, render, host
    ):
        soup = render("dashboard_full")
        glossary = _one(soup, f'details[data-terms="glossary"][data-glossary-host="{host}"]')
        got = [p.get("data-term") for p in glossary.select("p[data-term]")]
        assert got == [line["key"] for line in copy.glossary(host)]
        for key in got:
            assert len(soup.select(f'[data-term="{key}"]')) == 1, f"{key} is defined twice"

    def test_a_header_term_is_not_a_glossary_line(self, render):
        soup = render("dashboard_full")
        for record in copy.rows("B"):
            if record["key"].startswith("H"):
                assert not soup.select(f'[data-term="{record["key"]}"]'), record["key"]

    def test_a_removed_definition_row_has_no_line(self, render):
        soup = render("dashboard_full")
        assert not soup.select('[data-term="D40t"]'), "the Standby description says what it is"

    def test_the_cvar_and_sleeves_panels_carry_no_definitions_of_their_own(self, render):
        soup = render("empty")
        assert not _one(soup, "#cvar-diagnostic-panel").select("[data-term], [data-terms]")
        assert not _one(soup, '[data-testid="sleeves-panel"]').select("[data-term], [data-terms]")


# ---------------------------------------------------------------------------
# Configured values
# ---------------------------------------------------------------------------


def _term_text(soup, key: str) -> str:
    return _words(_one(soup, f'[data-term="{key}"]'))


def _with_fallback(key: str, slot: str, copy_row: str) -> str:
    """The row's text with the phrase that holds ``slot`` replaced whole by its fallback."""
    values = copy.slot_row(slot, copy_row)
    text = copy.row(key, "B")["new_text"]
    assert values["phrase"] in text, f"the Values phrase for {{{slot}}} is not in the row"
    return text.replace(values["phrase"], values["fallback"])


class TestConfiguredValues:
    @pytest.mark.parametrize("retention", ["90", "180", "365", "7"])
    def test_the_retention_is_the_one_in_the_env_file(self, render, retention):
        soup = render("dashboard_full", env=f"SHADOW_HISTORY_RETENTION_DAYS={retention}\n")
        assert _term_text(soup, "D07") == copy.fill(
            copy.row("D07", "B")["new_text"], shadow_retention_days=retention
        )

    def test_a_missing_retention_prints_the_default_the_prune_uses(self, render):
        soup = render("dashboard_full", env="")
        assert _term_text(soup, "D07") == copy.fill(
            copy.row("D07", "B")["new_text"], shadow_retention_days="180"
        )

    @pytest.mark.parametrize("bad", ["abc", "", "12.5", "ninety"])
    def test_an_unreadable_retention_replaces_the_whole_phrase_and_prints_no_number(
        self, render, bad
    ):
        soup = render("dashboard_full", env=f"SHADOW_HISTORY_RETENTION_DAYS={bad}\n")
        text = _term_text(soup, "D07")
        assert text == _with_fallback("D07", "shadow_retention_days", "D07")
        assert not any(ch.isdigit() for ch in text)
        # The rest of the page and the sibling slot are untouched.
        assert _term_text(soup, "D25") == copy.fill(
            copy.row("D25", "B")["new_text"], friction_pct=_plain(0.5)
        )

    @pytest.mark.parametrize("pct", [0.25, 0.5, 1.0, 0.125])
    def test_the_trading_cost_is_the_one_the_optimizer_assumes(self, render, pct):
        soup = render("dashboard_full", SIM_EXIT_FRICTION_PCT=pct)
        assert _term_text(soup, "D25") == copy.fill(
            copy.row("D25", "B")["new_text"], friction_pct=_plain(pct)
        )

    @pytest.mark.parametrize(
        "unreadable", [None, True, float("nan"), float("inf"), "0.5", object()]
    )
    def test_an_unreadable_trading_cost_replaces_the_whole_phrase_and_prints_no_number(
        self, render, unreadable
    ):
        soup = render("dashboard_full", SIM_EXIT_FRICTION_PCT=unreadable)
        text = _term_text(soup, "D25")
        assert text == _with_fallback("D25", "friction_pct", "D25")
        assert not any(ch.isdigit() for ch in text)
        # The sibling slot and the rest of the page are untouched.
        assert _term_text(soup, "D07") == copy.fill(
            copy.row("D07", "B")["new_text"], shadow_retention_days="180"
        )

    def test_both_slots_failing_together_still_renders_the_whole_page(self, render):
        soup = render(
            "dashboard_full", env="SHADOW_HISTORY_RETENTION_DAYS=abc\n", SIM_EXIT_FRICTION_PCT=None
        )
        for key in ALL_DESCRIPTIONS:
            assert _words(_explainer(soup, key)) == copy.a_text(key)
        assert _term_text(soup, "D07") == _with_fallback("D07", "shadow_retention_days", "D07")
        assert _term_text(soup, "D25") == _with_fallback("D25", "friction_pct", "D25")

    def test_no_slot_value_is_typed_into_the_template_or_the_script_next_to_the_copy(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2]
        template = (root / "templates" / "index.html").read_text(encoding="utf-8")
        script = (root / "static" / "index.js").read_text(encoding="utf-8")
        for needle in (
            "kept for the last 180",
            "kept for the last 90",
            "percentage points off every exit",
        ):
            for source in (template, script):
                for line in source.splitlines():
                    if needle in line:
                        assert "{{" in line or "copy_values" in line, (
                            f"a configured value is typed beside the copy: {line.strip()[:160]}"
                        )


# ---------------------------------------------------------------------------
# Reworded labels
# ---------------------------------------------------------------------------

OLD_FRAGMENTS = {
    key: copy.row(key, "C")["old_text"].split("{")[0].strip()
    for key in ("D30a", "D30b", "D06c", "NM1", "NM2", "NM4", "NM5", "NM6", "D68", "D03r")
}


def _page_words(soup) -> str:
    """The text a reader sees: no script, style or comment, and the cached soup left as it was."""
    from bs4 import Comment

    pieces = [
        t
        for t in soup.find_all(string=True)
        if not isinstance(t, Comment) and t.parent.name not in ("script", "style")
    ]
    return " ".join(" ".join(pieces).split())


class TestRelabelledRows:
    def test_the_hero_descriptor_ends_in_the_new_sentence(self, render):
        soup = render("dashboard_full")
        descriptor = _words(_one(soup, '[data-testid="guard-alpha-descriptor"]'))
        assert descriptor.endswith(copy.text("D03r", "C"))
        assert OLD_FRAGMENTS["D03r"] not in descriptor

    @pytest.mark.parametrize(("mode", "word"), [("LIVE_EXECUTION=True\n", "live"), ("", "dry-run")])
    def test_the_hero_legend_names_the_bot_series_with_the_mode(self, render, mode, word):
        soup = render("dashboard_full", env=mode)
        legend = _words(soup.select_one('[data-testid="chart-legend"] .legend-item'))
        assert legend == copy.fill(copy.text("D06c", "C"), mode=word)

    def test_the_lifetime_drawdown_labels_read_the_new_text_on_the_hero_and_every_card(
        self, render
    ):
        soup = render("dashboard_full")
        assert _words(_one(soup, '[data-testid="mdd-lifetime-scalar"]')).startswith(
            copy.text("NM1", "C")
        )
        cells = soup.select('[data-field="mdd-lifetime-cell"]')
        assert cells, "premise: the seeded dashboard renders cards"
        for cell in cells:
            assert _words(cell).startswith(copy.text("NM2", "C"))

    def test_the_detail_panel_stat_labels_and_legend_read_the_new_text(self, render):
        soup = render("dashboard_full")
        panel = _one(soup, '[data-testid="detail-panel"]')

        def label_of(value_id: str) -> str:
            stat = panel.select_one(f"#{value_id}").find_parent(class_="detail-stat")
            return _words(stat.select_one(".detail-stat-label"))

        assert label_of("dp-alpha") == copy.text("NM3", "C")
        assert label_of("dp-shadow-peak") == copy.text("NM4", "C")
        legend = [_words(k) for k in panel.select(".detail-legend .legend-key")]
        assert copy.text("NM5", "C") in legend
        assert OLD_FRAGMENTS["NM5"] not in legend

    def test_an_open_market_names_the_held_line_with_the_new_words(self, render):
        soup = render("dashboard_full", market="open")
        legend = _words(_one(soup, '[data-field="if-held-legend"]'))
        assert legend == copy.text("NM6", "C")

    def test_the_detail_panel_footer_reads_the_new_sentence(self, render):
        soup = render("dashboard_full")
        footer = _one(soup, "#detail-panel-footer")
        assert copy.text("D68", "C") in [_words(s) for s in footer.find_all("span")]

    def test_a_vwap_breakdown_pill_reads_the_new_text_and_the_old_one_is_gone(self, render):
        soup = render("dashboard_full")
        pills = [
            _words(p)
            for p in soup.select('[data-testid="status-pill"]')
            if "vwap" in p.get("class", [])
        ]
        assert pills, "premise: the captured state holds a symphony exited on a VWAP breakdown"
        assert set(pills) == {copy.text("D29v", "C")}

    def test_every_triggered_verdict_reads_one_of_the_three_sentences_or_a_dash(self, render):
        soup = render("dashboard_full")
        shown = [
            _words(holder.select_one('[data-field="verdict-text"]'))
            for holder in soup.select('[data-testid="triggered-verdict"]')
            if "display:none" not in (holder.get("style") or "").replace(" ", "")
        ]
        assert shown, "premise: the captured state holds triggered symphonies"
        lead_a, lead_b = (copy.text(k, "C").split("{m}")[0] for k in ("D30a", "D30b"))
        matched = copy.text("D30c", "C")
        for text in shown:
            assert text.startswith((lead_a, lead_b)) or text in (matched, "—"), text

    def test_no_old_wording_is_left_in_the_page_text(self, render):
        text = _page_words(render("dashboard_full", market="open"))
        for key, old in OLD_FRAGMENTS.items():
            assert old and old not in text, f"the old text of {key} ({old!r}) is still on the page"


# ---------------------------------------------------------------------------
# The three controls no script handled
# ---------------------------------------------------------------------------


class TestControlsWithoutAHandler:
    def test_the_detail_panel_no_longer_renders_the_three_inert_buttons(self, render):
        soup = render("dashboard_full")
        panel = _one(soup, '[data-testid="detail-panel"]')
        texts = [_words(b) for b in panel.find_all("button")]
        for removed in harness.REMOVED_CONTROL_TEXTS:
            assert not any(t.startswith(removed) for t in texts), removed
        assert not soup.select("#dp-view-logs-btn, #dp-go-cash-btn")

    def test_the_close_control_and_the_overlay_toggles_stay(self, render):
        soup = render("dashboard_full")
        panel = _one(soup, '[data-testid="detail-panel"]')
        assert panel.select_one('[data-testid="detail-close"]')
        toggles = [t.get("data-testid") for t in panel.select(".overlay-toggle")]
        assert toggles == [
            "overlay-toggle-stop",
            "overlay-toggle-breakeven",
            "overlay-toggle-vwap",
            "overlay-toggle-mc",
        ]
        assert soup.select_one("#detail-scrim")
