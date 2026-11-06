"""History and Performance keep the polish-and-density contract of the unslop pass.

Source-level counterpart of ``tests/ui/test_history_performance_density_browser.py`` (browser
modules do not run in CI). Subject: ``templates/history.html``, ``templates/performance.html``,
``static/history.js`` and ``static/performance.js``. The rules these two pages' own CSS and scripts
follow:

* no upper-case tracked labels, no italics, weights 400 and 500 only (figures included);
* no pill: a state is a dot and one word, and nothing sets a pill radius;
* no text below 11 px;
* the headline figures are one row of figures (not boxed tiles), numbers in tabular figures;
* the by-reason figures are one ledger row per reason under one header row, with the
  definitions behind a disclosure that starts collapsed;
* a stop-justification verdict is a status dot (drawn by the class) and one word in sentence case,
  and the risk-metrics table draws figures alone (no per-row mini bars);
* the return gap stays in ``%`` on Performance and History's sum of exit alpha stays in ``pts``.

Class, token and text usage are asserted, never a computed colour or size; a rendered page is judged
in the browser module and on the screenshot gate.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.ui import _page_copy as page_copy

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "templates"
STATIC = REPO / "static"

PAGE_FILES = {
    "history": (TEMPLATES / "history.html", STATIC / "history.js"),
    "performance": (TEMPLATES / "performance.html", STATIC / "performance.js"),
}
PAGE_IDS = list(PAGE_FILES)

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_JS_LINE_COMMENT = re.compile(r"(?m)^[ \t]*//.*$")


def _template(page: str) -> str:
    return _HTML_COMMENT.sub("", PAGE_FILES[page][0].read_text(encoding="utf-8"))


def _css(page: str) -> str:
    blocks = re.findall(r"<style\b[^>]*>(.*?)</style>", _template(page), flags=re.I | re.S)
    return _COMMENT.sub("", "\n".join(blocks))


def _script(page: str) -> str:
    text = PAGE_FILES[page][1].read_text(encoding="utf-8")
    return _JS_LINE_COMMENT.sub("", _COMMENT.sub("", text))


def _rule_bodies(css: str, selector: str) -> list[str]:
    """The declaration text of every rule (media-nested ones too) that lists exactly ``selector``."""
    return [
        " ".join(body.split())
        for names, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
        if selector in [name.strip() for name in names.split(",")]
    ]


# ---------------------------------------------------------------------------
# Type: sentence case, weights 400 and 500, nothing under 11 px, no italics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", PAGE_IDS)
def test_no_rule_upper_cases_or_tracks_a_label(page):
    css = _css(page)
    assert not re.search(r"text-transform\s*:\s*uppercase", css, re.I), (
        f"{page}: an upper-case label is back"
    )
    tracking = [v.strip() for v in re.findall(r"letter-spacing\s*:\s*([^;}]+)", css)]
    positive_tracking = [v for v in tracking if v not in {"0", "0px", "0em", "normal"}]
    assert positive_tracking == [], f"{page}: tracked text: {positive_tracking}"
    script = _script(page)
    assert "text-transform" not in script and "letter-spacing" not in script, (
        f"{page}: the script writes an upper-case or tracked style inline"
    )


@pytest.mark.parametrize("page", PAGE_IDS)
def test_no_italics_and_only_weights_400_and_500(page):
    for where, text in (("css", _css(page)), ("script", _script(page))):
        assert not re.search(r"font-style\s*:\s*italic", text, re.I), f"{page} {where}: italic"
        weights = set(re.findall(r"font-weight\s*:\s*(\d{3}|bold|bolder)", text, re.I))
        assert weights <= {"400", "500"}, f"{page} {where}: weights {sorted(weights)}"
    # The canvas labels of the cumulative chart are drawn from a font shorthand, not a rule.
    for shorthand in re.findall(r"""ctx\.font\s*=\s*['"](\d{3})\b""", _script(page)):
        assert shorthand in {"400", "500"}, f"{page}: chart label weight {shorthand}"


@pytest.mark.parametrize("page", PAGE_IDS)
def test_no_text_below_11_px(page):
    for where, text in (("css", _css(page)), ("script", _script(page))):
        sizes = [float(n) for n in re.findall(r"font-size\s*:\s*(\d+(?:\.\d+)?)px", text)]
        assert all(size >= 11 for size in sizes), f"{page} {where}: {sorted(set(sizes))}"
        rems = [float(n) for n in re.findall(r"font-size\s*:\s*(\d*\.?\d+)rem", text)]
        assert all(size >= 0.6875 for size in rems), f"{page} {where}: {sorted(set(rems))}"
    chart_sizes = [
        int(n) for n in re.findall(r"""ctx\.font\s*=\s*['"]\d{3} (\d+)px""", _script(page))
    ]
    assert all(size >= 11 for size in chart_sizes), f"{page}: chart label sizes {chart_sizes}"


# ---------------------------------------------------------------------------
# Signature: a state is a dot and one word, never a pill
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", PAGE_IDS)
def test_no_pill_radius_and_no_status_pill(page):
    css = _css(page)
    assert not re.search(r"border-radius\s*:\s*(999|9999|20|24)\d*px", css), (
        f"{page}: a pill radius"
    )
    assert "status-pill" not in _template(page) and "status-pill" not in _script(page), (
        f"{page}: a status pill is drawn"
    )


def test_a_precondition_verdict_is_a_dot_and_a_word():
    css = _css("performance")
    chip = " ".join(_rule_bodies(css, ".precond-verdict-chip"))
    dot = " ".join(_rule_bodies(css, ".precond-verdict-chip::before"))
    assert "border-radius: 50%" in dot and "width: 8px" in dot and "height: 8px" in dot
    for declaration in ("border:", "border-radius", "background", "padding"):
        assert declaration not in chip, f"the verdict chip still draws a pill: {declaration}"
    for modifier in ("sufficient-met", "sufficient-likely", "not-met", "negative-edge"):
        assert f".precond-verdict-chip--{modifier}" in css
    assert ".precond-verdict-chip--insufficient-data" in css


def test_the_verdict_word_is_sentence_case():
    script = _script("performance")
    body = script[script.index("function preconditionChipEl") :]
    body = body[: body.index("function preconditionNumCell")]
    assert ".toLowerCase()" in body and "toUpperCase()" in body and "charAt(0)" in body
    assert "verdict.replace(/_/g, ' ')" in body


_STACKED_TABLES = {
    # testid: (modifier class, the header texts after "Symphony", in order)
    "guard-alpha-preconditions-table": (
        "metrics-tbl--ledger-pre",
        ["Sample", "\u03c1", "95% CI", "Daily Sharpe", "N", "Verdict"],
    ),
    "exit-turnover-table": (
        "metrics-tbl--ledger-turn",
        ["30d", "90d", "365d", page_copy.text("F22r", "C").split("; ")[1]],
    ),
}


def _phone_css() -> str:
    css = _css("performance")
    start = css.index("@media (max-width: 700px)")
    return css[start:]


@pytest.mark.parametrize("testid", list(_STACKED_TABLES))
def test_a_per_symphony_ledger_is_a_name_line_and_a_figure_line_on_a_phone(testid):
    """Too many columns for a phone: each row is the symphony on its own line with one line of
    figures under it, under one header row of column labels stated once at the table's top."""
    modifier, columns = _STACKED_TABLES[testid]
    template = _template("performance")
    assert re.search(
        rf'<table class="metrics-tbl metrics-tbl--stack {modifier}"[^>]*data-testid="{testid}"',
        template,
    ), f"{testid} does not take the phone ledger layout"
    css = _phone_css()
    header = " ".join(_rule_bodies(css, ".metrics-tbl--stack thead tr"))
    assert "display: flex" in header, "the header row is not kept as one row"
    assert "display: none" not in " ".join(_rule_bodies(css, ".metrics-tbl--stack thead"))
    assert "display: none" in " ".join(_rule_bodies(css, ".metrics-tbl--stack th:first-child"))
    table = re.search(rf'<table[^>]*data-testid="{testid}".*?</thead>', template, re.S).group(0)
    headers = [
        re.sub(r"<[^>]+>", "", cell).replace("&rho;", "\u03c1").strip()
        for cell in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)
    ]
    assert headers[1:] == columns, "the header row changed"
    sizes = [int(n) for n in re.findall(r"font-size:\s*(\d+)px", css)]
    assert sizes and min(sizes) >= 11, "text under 11 px on the phone ledgers"


def test_the_turnover_figure_line_is_four_columns_and_a_count_reads_n_in_days():
    css = _phone_css()
    columns = " ".join(_rule_bodies(css, ".metrics-tbl--ledger-turn tbody tr"))
    assert "display: grid" in columns
    assert "repeat(3, minmax(0, 1fr)) minmax(0, 0.8fr)" in columns, "not four figure columns"
    first = " ".join(_rule_bodies(css, ".metrics-tbl--ledger-turn tbody td:first-child"))
    assert "grid-column: 1 / -1" in first, "the symphony does not take its own line"
    after = " ".join(_rule_bodies(css, ".metrics-tbl--ledger-turn td[data-days]::after"))
    assert 'content: " in " attr(data-days) "d"' in after
    longer = " ".join(_rule_bodies(css, ".metrics-tbl--ledger-turn td .hp-muted"))
    assert "clip: rect(0 0 0 0)" in longer, "the retained words must stay for screen readers"
    script = _script("performance")
    cell = script[script.index("function turnoverWindowCell") :].split("\n    }\n")[0]
    assert "setAttribute('data-days', String(windowStats.coverage_days))" in cell
    assert "' (' + windowStats.coverage_days + 'd retained)'" in cell, "the cell text changed"


def test_the_precondition_verdict_sits_on_the_name_line_and_the_figures_under_it():
    css = _phone_css()
    name = " ".join(_rule_bodies(css, ".metrics-tbl--ledger-pre tbody td:first-child"))
    verdict = " ".join(_rule_bodies(css, ".metrics-tbl--ledger-pre tbody td:nth-child(7)"))
    figures = " ".join(
        _rule_bodies(css, ".metrics-tbl--ledger-pre td:nth-child(n+2):nth-child(-n+6)")
    )
    assert "order: 0" in name and "flex: 1 1 0" in name
    assert "order: 0" in verdict and "flex: none" in verdict
    assert "order: 2" in figures, "the figures do not follow the name line"
    line_break = " ".join(_rule_bodies(css, ".metrics-tbl--ledger-pre tbody tr::after"))
    assert "flex: 0 0 100%" in line_break and "order: 1" in line_break
    assert "display: none" in " ".join(
        _rule_bodies(css, ".metrics-tbl--ledger-pre th:nth-child(7)")
    )


def test_a_reason_is_a_coloured_dot_and_its_name_in_both_tables():
    css = _css("history")
    for key, token in (
        ("tp", "--studio-plum"),
        ("stop", "--studio-neg"),
        ("vwap", "--studio-cyan"),
        ("bleed", "--studio-warn"),
    ):
        assert any(token in body for body in _rule_bodies(css, f".hp-dot--{key}")), key
    script = _script("history")
    exits = script[script.index("function renderTriggers") :]
    exits = exits[: exits.index("// Per-day drill-down") if "// Per-day" in exits else None]
    assert "reasonDotHtml(canonicalReason)" in exits, "the exits table reason has no dot"


# ---------------------------------------------------------------------------
# Density: figures in one row, a ledger instead of cards, detail behind a disclosure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", PAGE_IDS)
def test_the_headline_figures_are_one_row_with_tabular_figures(page):
    css = _css(page)
    row = " ".join(_rule_bodies(css, ".hp-figs"))
    assert "display: grid" in row and "repeat(4" in row, f"{page}: not one row of four figures"
    figure = " ".join(_rule_bodies(css, ".hp-fig"))
    assert "background" not in figure and "border-radius" not in figure, f"{page}: boxed figures"
    value = " ".join(_rule_bodies(css, ".hp-fig-value"))
    assert "font-variant-numeric: tabular-nums" in value and "font-weight: 500" in value
    assert "mini-stat" not in _template(page), f"{page}: boxed stat tiles are back"


def test_history_by_reason_is_one_ledger_with_the_definitions_collapsed():
    html = _template("history")
    section = html[html.index('data-testid="by-reason-section"') :]
    section = section[: section.index('data-testid="recent-triggers"')]
    assert section.count("<table") == 1 and section.count("<thead>") == 1
    assert section.count('data-testid="reason-card"') == 4
    assert "<details" in section and "reason-descriptions" in section
    assert re.search(r"<details[^>]*\bopen\b", section) is None, "the definitions start open"
    script = _script("history")
    assert "renderReasonDescriptions(" in script
    assert "REASON_DESCRIPTIONS[reason]" in script


def test_history_reason_ledger_stacks_on_a_phone_with_its_own_labels():
    css = _css("history")
    assert "attr(data-label)" in css, "the stacked ledger rows do not name their figures"
    assert re.search(r"\.hp-reasons thead\s*\{\s*display:\s*none", css)
    script = _script("history")
    for label in (
        "Sum of exit alpha",
        "Dollar result",
        page_copy.text("Y06", "C"),
        "Wins",
        "Win rate",
        "Avg per exit",
    ):
        assert f'data-label="{label}"' in script, label
    assert 'data-label="Triggers"' not in script


def test_the_risk_metrics_table_draws_figures_alone():
    assert "metric-bar" not in _template("performance")
    script = _script("performance")
    body = script[script.index("function renderMetrics") :]
    body = body[: body.index("function renderBanner")]
    assert "<svg" not in body


def test_the_pick_a_symphony_state_has_no_decorative_glyph():
    script = _script("performance")
    state = script[script.index("function showPickSymphonyState") :]
    state = state[: state.index("function hidePickSymphonyState")]
    assert "&#9835;" not in state and "font-size:2rem" not in state
    assert "Pick a symphony" in state


# ---------------------------------------------------------------------------
# Units and honesty text stay
# ---------------------------------------------------------------------------


def test_history_sum_stays_in_points_and_keeps_its_basis_sentence():
    script = _script("history")
    assert "' pts'" in script and "pts" in _template("history")
    html = _template("history")
    assert "Not a portfolio return." in html and "15:54 ET" in html
    assert 'id="total-alpha-descriptor"' in html


def test_performance_gap_stays_in_percent_and_its_caption_names_the_window():
    script = _script("performance")
    assert "'%'" in script and "' pp'" not in script
    caption = script[script.index("function renderGuardEffectCaption") :]
    caption = caption[: caption.index("function renderHeadlineStats")]
    assert "Guard effect on" in caption and "windowName" in caption
    assert "Same figure as the Dashboard at" in caption
