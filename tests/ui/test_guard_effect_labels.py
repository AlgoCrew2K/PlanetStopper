"""History and Performance say what their guard numbers are (plan 06 AC-1, AC-7).

Real call path for each test: the page the Flask client returns, the real script run over it under
node (tests/ui/test_static_js_page_harness.py), the fetches answered with the real routes' bodies
over the captured production rows (tests/fixtures/prod_droplet, stamped as recorded in
tests/fixtures/ux_data_truth/PROVENANCE.md). Expected figures come from the body the route served.

* History's total is a sum of per-exit points, not a portfolio return: unit `pts`, the label
  "Sum of exit alpha", a descriptor stating 15:54 ET, no size weighting and not a portfolio return;
  the strip and the by-reason cards use the same words.
* Performance's tile is the same quantity as the dashboard headline: "Guard effect on portfolio",
  unit `%` (a gap between two returns, never `pp`), the window named, the two compounded returns stated. Its caption makes no claim about
  the Dashboard that is false at the window picked: it names that window, and says nothing about the
  Dashboard at a window the Dashboard has no button for or at symphony scope.
* The Performance risk table gives its lower-is-better rows one delta convention (bot minus
  baseline, in %, stated in the header) and leaves no meaningless if-held value on reduction rows.
* The preconditions table has no Replay row for a symphony without a replay sample, and one note
  names how many lack one.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.ui.test_history_page_data_truth import (  # noqa: F401  (client, pm_dir_factory: fixtures)
    _ago,
    _pick,
    _stamped_day,
    client,
    pm_dir_factory,
)
from tests.ui.test_history_page_data_truth import _history as _history_body
from tests.ui.test_history_page_data_truth import _run as _history_run
from tests.ui.test_performance_page_data_truth import (  # noqa: F401  (autouse fixtures)
    _analytics_shadow_db_seam,
    _panel_scenario,
    _perf_page,
    _precondition_world,
    _routes,
    _seed_captured_state_and_shadow_rows,
    _stub_database_load_state_for_advisor_routes,
)
from tests.ui.test_static_js_page_harness import captured_json, route

HISTORY_LABEL = "Sum of exit alpha"
HISTORY_STRIP_HEADER = "Daily sum of exit alpha (pts, unweighted)"
PTS_SHAPE = re.compile(r"^[+-]\d+\.\d{2} pts$")


def _history_descriptor(exits: int) -> str:
    return (
        "Each exit's return minus its if-held return at 15:54 ET, added across "
        f"{exits} exits with no size weighting. Not a portfolio return."
    )


_HISTORY_READ = """
await H.boot();
await H.click(%r);
return {
  value: H.text('#val-total-alpha'),
  label: H.text('[data-testid="stat-total-alpha"] .mini-stat-label'),
  tile: H.text('[data-testid="stat-total-alpha"]'),
  strip: H.text('[data-testid="daily-strip-header"]'),
  cards: H.$$('[data-testid="reason-card"]').map(function (e) { return e.textContent.replace(/\\s+/g, ' ').trim(); }),
};
"""


def _history_page(client, pm_dir_factory, window="30"):
    pm_dir_factory({_ago(5): _stamped_day("2026-06-22"), _ago(4): _stamped_day("2026-06-23")})
    payload = _history_body(client, int(window))
    out = _history_run(
        client,
        [route(f"/api/history/{window}", payload)],
        _HISTORY_READ % _pick(window),
    )
    return payload, out


def test_history_total_reads_sum_of_exit_alpha_in_points(client, pm_dir_factory):
    payload, out = _history_page(client, pm_dir_factory)
    expected = f"{'+' if payload['total_alpha'] >= 0 else ''}{payload['total_alpha']:.2f} pts"
    assert out["value"] == expected, f"value {out['value']!r}, expected {expected!r}"
    assert PTS_SHAPE.match(out["value"])
    assert "%" not in out["value"], "a sum of points is not a percentage"
    assert out["label"] == HISTORY_LABEL, out["label"]
    assert "Total guard" not in out["tile"]


def test_history_keeps_pts_and_does_not_take_the_gap_unit_of_the_other_pages(
    client, pm_dir_factory
):
    """The Dashboard and Performance show a gap between two returns in %. History's total is an
    unweighted sum across exits, not a return: it stays in "pts", never "%" and never "pp"."""
    _payload, out = _history_page(client, pm_dir_factory)
    assert out["value"].endswith(" pts"), out["value"]
    assert out["strip"].startswith("Daily sum of exit alpha (pts"), out["strip"]
    assert out["cards"], "the captured days must give at least one reason card"
    assert not out["value"].endswith("%"), out["value"]
    for text in [out["value"], out["strip"], *out["cards"]]:
        assert not re.search(r"\d\s?pp\b", text), f"History states a figure in pp: {text!r}"
    for card in out["cards"]:
        assert re.search(r"[+-]\d+\.\d{2} pts", card), f"reason card has no pts unit: {card!r}"
        assert not re.search(r"[+-]\d+\.\d{2}\s?%", card), (
            f"reason card states its sum of exit alpha in %: {card!r}"
        )


def test_history_total_carries_the_descriptor(client, pm_dir_factory):
    payload, out = _history_page(client, pm_dir_factory)
    tile = " ".join(out["tile"].split())
    assert _history_descriptor(payload["trigger_count"]) in tile, (
        f"the tile must state its basis: {tile!r}"
    )


def test_history_strip_and_reason_cards_use_the_same_words(client, pm_dir_factory):
    payload, out = _history_page(client, pm_dir_factory)
    assert out["strip"] == HISTORY_STRIP_HEADER, out["strip"]
    assert out["cards"], "the captured days must give at least one reason card"
    for card in out["cards"]:
        assert HISTORY_LABEL in card, f"reason card still names the old quantity: {card!r}"
        assert re.search(r"[+-]\d+\.\d{2} pts", card), (
            f"reason card value has no pts unit: {card!r}"
        )
        assert "cumulative" not in card.lower()


# ---------------------------------------------------------------------------
# Performance tile
# ---------------------------------------------------------------------------

_TILE_READ = """
await H.boot();
const read = function () {
  return { value: H.text('#guard-alpha-value'), tile: H.text('[data-testid="guard-alpha-stat"]'),
           label: H.text('[data-testid="guard-alpha-stat"] .mini-stat-label') };
};
const first = read();
await H.click('[data-testid="window-btn-30"]');
return { first: first, after: read() };
"""


def test_performance_tile_is_the_guard_effect_on_portfolio_in_percent(client):
    _seed_captured_state_and_shadow_rows()
    payload = client.get("/api/performance?scope=aggregate&days=60").get_json()
    result = _perf_page(client, _routes(client, payload, with_panels=False), _TILE_READ)
    bot = payload["shadow_metrics"]["total_return"] * 100
    held = payload["live_metrics"]["total_return"] * 100
    gap = bot - held
    first = result["first"]
    assert first["value"] == f"{'+' if gap >= 0 else ''}{gap:.2f}%", first["value"]
    assert "pp" not in first["value"], first["value"]
    assert first["label"].startswith("Guard effect on portfolio"), first["label"]
    assert "60d" in first["label"], "the window must be named on the tile"
    assert "30d" in result["after"]["label"], "the label must follow the picked window"
    stated = re.search(
        r"(-?\d+\.\d{2})% Bot vs (-?\d+\.\d{2})% If held, compounded\.",
        " ".join(first["tile"].split()),
    )
    assert stated, f"the tile does not state the two compounded returns: {first['tile']!r}"
    assert float(stated.group(1)) == round(bot, 2) or abs(float(stated.group(1)) - bot) < 0.006
    assert abs(float(stated.group(2)) - held) < 0.006


# ---------------------------------------------------------------------------
# Performance caption: true at every window
# ---------------------------------------------------------------------------

_CAPTION_READ = """
await H.boot();
const read = function () {
  return { label: H.text('#guard-alpha-label'), basis: H.text('#guard-alpha-basis') };
};
const buttons = H.$$('#days-picker button').map(function (b) {
  return { value: b.getAttribute('data-value'), testid: b.getAttribute('data-testid') };
});
const first = read();
const byWindow = {};
for (const b of buttons) {
  await H.click('[data-testid="' + b.testid + '"]');
  byWindow[b.value] = read();
}
return { first: first, byWindow: byWindow };
"""

_SYMPHONY_CAPTION_READ = _CAPTION_READ.replace(
    "const first = read();",
    "await H.click('[data-testid=\"scope-btn-symphony\"]');\nconst first = read();",
)

_DASHBOARD_WINDOW_ATTRIBUTE = re.compile(r'<button[^>]*data-window="([^"]+)"')


def _dashboard_window_tokens() -> set[str]:
    """The windows the Dashboard has a button for, read from the markup that renders them."""
    source = (Path(__file__).resolve().parents[2] / "templates" / "index.html").read_text(
        encoding="utf-8"
    )
    selector = re.search(r'data-testid="window-selector".*?</div>', source, flags=re.S)
    assert selector, "the Dashboard's window selector is gone from the template"
    return set(_DASHBOARD_WINDOW_ATTRIBUTE.findall(selector.group(0)))


def _window_named_by(label: str) -> str:
    """The window the tile's own label names (` · 60d`, ` · YTD`)."""
    named = re.search(r" · (\S+)$", " ".join(label.split()))
    assert named, f"the tile's label names no window: {label!r}"
    return named.group(1)


def _names(window: str, text: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(window)}(?!\w)", text))


def _sentence_naming_the_dashboard(basis: str) -> str:
    return next(x for x in re.split(r"(?<=[.!?])\s+", basis) if "Dashboard" in x)


def _caption_world(client, *, with_panels=False):
    _seed_captured_state_and_shadow_rows()
    payload = client.get("/api/performance?scope=aggregate&days=60").get_json()
    return _routes(client, payload, with_panels=with_panels)


def test_precondition_the_two_pages_do_not_share_every_window(client):
    """The caption's claim depends on the window only because the pages differ: the Performance
    page opens on a window the Dashboard does not open on, and has a window the Dashboard lacks."""
    dashboard = _dashboard_window_tokens()
    assert "30d" in dashboard and "5y" not in dashboard, dashboard
    result = _perf_page(client, _caption_world(client), _CAPTION_READ)
    assert "5y" in result["byWindow"], "the Performance picker lost its 5Y window"
    assert _window_named_by(result["first"]["label"]) != "30d", (
        "the Performance page opens on the window the Dashboard opens on"
    )


def test_the_caption_never_claims_the_dashboard_unconditionally_at_any_window(client):
    result = _perf_page(client, _caption_world(client), _CAPTION_READ)
    shown = {"(opening)": result["first"], **result["byWindow"]}
    for window, tile in shown.items():
        basis = " ".join(tile["basis"].split())
        assert re.search(r"-?\d+\.\d{2}% Bot vs -?\d+\.\d{2}% If held, compounded\.", basis), (
            f"{window}: the caption lost the two compounded returns: {basis!r}"
        )
        assert not re.search(r"Dashboard\s*\.", basis), (
            f"{window}: the caption says 'the Dashboard' with no window, which is false whenever "
            f"the Dashboard is on another window: {basis!r}"
        )


def test_a_caption_that_mentions_the_dashboard_names_the_window_it_is_stated_at(client):
    result = _perf_page(client, _caption_world(client), _CAPTION_READ)
    shown = {"(opening)": result["first"], **result["byWindow"]}
    for window, tile in shown.items():
        basis = " ".join(tile["basis"].split())
        if "Dashboard" not in basis:
            continue
        named = _window_named_by(tile["label"])
        mention = _sentence_naming_the_dashboard(basis)
        assert _names(named, mention), (
            f"{window}: the caption mentions the Dashboard without the window the tile is on "
            f"({named!r}): {basis!r}"
        )


def test_the_caption_follows_the_window_picked_and_never_keeps_another_windows_name(client):
    result = _perf_page(client, _caption_world(client), _CAPTION_READ)
    names = {w: _window_named_by(t["label"]) for w, t in result["byWindow"].items()}
    assert len(set(names.values())) == len(names), f"fixture: windows share a name: {names}"
    for window, tile in result["byWindow"].items():
        basis = " ".join(tile["basis"].split())
        if "Dashboard" not in basis:
            continue
        mention = _sentence_naming_the_dashboard(basis)
        stale = [n for w, n in names.items() if w != window and _names(n, mention)]
        assert not stale, f"{window}: the caption names {stale} after {names[window]!r}: {basis!r}"


def test_a_window_the_dashboard_has_no_button_for_makes_no_dashboard_claim(client):
    result = _perf_page(client, _caption_world(client), _CAPTION_READ)
    lacking = set(result["byWindow"]) - _dashboard_window_tokens()
    assert lacking, "fixture: the Performance picker has no window the Dashboard lacks"
    for window in lacking:
        basis = result["byWindow"][window]["basis"]
        assert "Dashboard" not in basis, f"{window}: no Dashboard figure exists here: {basis!r}"


def test_symphony_scope_keeps_no_dashboard_claim_at_any_window(client):
    result = _perf_page(client, _caption_world(client, with_panels=True), _SYMPHONY_CAPTION_READ)
    shown = {"(opening)": result["first"], **result["byWindow"]}
    assert all("symphony" in t["label"] for t in shown.values()), (
        f"fixture: the tile is not at symphony scope: {[t['label'] for t in shown.values()]}"
    )
    for window, tile in shown.items():
        assert tile["basis"].strip(), f"{window}: the symphony caption is empty"
        assert "Dashboard" not in tile["basis"], (
            f"{window}: the Dashboard has no per-symphony figure: {tile['basis']!r}"
        )


# ---------------------------------------------------------------------------
# Performance risk table (UXR-27)
# ---------------------------------------------------------------------------

_RISK_READ = """
await H.boot();
return { rows: H.rows('#metrics-tbody'),
         header: H.$$('.metrics-tbl thead th').map(function (e) { return e.textContent.replace(/\\s+/g, ' ').trim(); }) };
"""


def _number(text: str) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace("−", "-"))
    assert match, f"no number in {text!r}"
    return float(match.group(0))


def test_lower_is_better_rows_share_one_delta_convention_and_unit(client):
    _seed_captured_state_and_shadow_rows()
    payload = client.get("/api/performance?scope=aggregate&days=60").get_json()
    result = _perf_page(client, _routes(client, payload, with_panels=False), _RISK_READ)
    header = result["header"]
    assert header[3].endswith("in %)"), (
        f"the delta column header must state its unit: {header[3]!r}"
    )
    assert "pp" not in header[3], f"the delta column header still says pp: {header[3]!r}"
    rows = {r[0]: r for r in result["rows"]}
    for label in ("Max DD", "Annualized volatility"):
        cells = rows[label]
        held, bot = _number(cells[1]), _number(cells[2])
        assert cells[3].endswith("%"), f"{label}: delta {cells[3]!r} is not a gap in %"
        assert "pp" not in cells[3], f"{label}: delta {cells[3]!r} still says pp"
        assert abs(_number(cells[3]) - (bot - held)) <= 0.011, (
            f"{label}: delta {cells[3]!r} is not bot minus baseline of the legs {cells[1:3]}"
        )


def test_reduction_rows_carry_no_if_held_value(client):
    _seed_captured_state_and_shadow_rows()
    payload = client.get("/api/performance?scope=aggregate&days=60").get_json()
    result = _perf_page(client, _routes(client, payload, with_panels=False), _RISK_READ)
    reduction = [r for r in result["rows"] if "reduction" in r[0].lower()]
    for cells in reduction:
        assert not re.search(r"[+-]?0\.00\s*(pp|%)", cells[1]), (
            f"{cells[0]}: a meaningless if-held value {cells[1]!r}"
        )
        assert not re.search(r"\d", cells[1]), (
            f"{cells[0]}: if-held cell {cells[1]!r} holds a number"
        )


# ---------------------------------------------------------------------------
# Preconditions table (UXR-22)
# ---------------------------------------------------------------------------


def test_a_symphony_without_a_replay_sample_has_no_replay_row(client, monkeypatch):
    state = captured_json("bot_state_positions.json")
    with_replay = next(iter(state))
    ids, body = _precondition_world(
        client, monkeypatch, replay_for=[with_replay], shadow_for=list(state)
    )
    cold = [
        s for s, e in body["symphonies"].items() if e["replay"]["verdict"] == "INSUFFICIENT_DATA"
    ]
    assert cold and with_replay not in cold, "the world needs both kinds of symphony"
    result = _perf_page(
        client, _routes(client, {"live_metrics": {}, "shadow_metrics": {}}), _panel_scenario()
    )
    replay_rows = [r for r in result["rows"] if r["cells"][1] == "Replay"]
    assert len(replay_rows) == len(body["symphonies"]) - len(cold), (
        f"{len(replay_rows)} Replay rows for {len(body['symphonies']) - len(cold)} symphonies "
        "that have a replay sample"
    )
    assert result["note"]["visible"] and result["note"]["count"] == 1
    assert re.search(rf"\b{len(cold)}\b", result["note"]["text"]), result["note"]["text"]
