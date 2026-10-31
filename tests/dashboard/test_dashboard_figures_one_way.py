"""The dashboard reads one way: first paint, refresh and picked window say the same thing.

Plan 06 AC-1 (headline words and unit), AC-2 (Max DD text, absent alpha, Max DD and Ann. Vol follow the
picked window, lock badge, hero chart tooltip and hover) and AC-3 (a card opened before the first state
answer). Real call path: the page `GET /` renders, `static/index.js` run whole under node
(tests/dashboard/_index_js_harness.py), bodies from the real routes over a seeded database.

Worlds: `review-fixes` (the default world plus a standby entry with no row today, and the symphony the
droplet check found gone; the harness documents both), `review-fixes-live` (flag True), `onepoint` (one
history day: a window cannot supply drawdown or volatility). Expected text is recomputed from the strip
body the route served for the picked window with the client's own format, never copied from a capture.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

WORLD = "review-fixes"
REPO = Path(__file__).resolve().parents[2]

# AC-1 descriptor, from the audit's label table. "Simulated" is bound to the engine's flag.
DESCRIPTOR_SIMULATED = (
    "Simulated Bot return minus If-held return over this window: the gap between the chart's "
    "two lines. All symphonies at today's values, before exit friction."
)
DESCRIPTOR_LIVE = DESCRIPTOR_SIMULATED.replace("Simulated Bot", "Bot")
HEADLINE_SHAPE = re.compile(r"^[+-]\d+\.\d{2} pp$")
LEG_SHAPE = re.compile(r"^(Bot|Held) \d+\.\d{2}%$")


def _norm(text: str | None) -> str:
    return " ".join((text or "").replace(" ", " ").split())


def _bodies(world_name: str = WORLD) -> dict:
    world = hx.get_world(world_name)
    stamp = sup.render_stamp(world)
    return {
        "pollNew": sup.with_stamp(world["next"]["state"], sup.later_stamp(stamp))
        if "next" in world
        else sup.with_stamp(world["state"], sup.later_stamp(stamp)),
    }


def _run(scenario: str, *, world: str = WORLD, extra: dict | None = None):
    bodies = _bodies(world)
    bodies.update(extra or {})
    return sup.run(scenario, world=world, scripts=sup.INDEX_ONLY, extra_bodies=bodies)


def _text_of(tree: dict, predicate) -> list[str]:
    return [_norm(hx.tree_text(n)) for n in hx.find_all(tree, predicate)]


def _has_class(name: str):
    return lambda n: name in (n["a"].get("class") or "").split()


# ---------------------------------------------------------------------------
# AC-1: the headline's words, unit and descriptor
# ---------------------------------------------------------------------------


class TestHeadlineWordsAndUnit:
    def test_label_names_the_guard_effect_on_the_portfolio_and_the_window(self):
        tree = hx.parse_html(hx.get_world(WORLD)["html"])
        labels = _text_of(tree, _has_class("guard-alpha-label"))
        assert len(labels) == 1, f"expected one headline label, found {labels}"
        assert labels[0].startswith("Guard effect on portfolio"), labels[0]
        assert "30d" in labels[0], "the window is named in the label"
        assert "Guard alpha" not in labels[0], "the old name is still on the label"

    def test_first_paint_headline_is_the_strip_gap_in_percentage_points(self):
        world = hx.get_world(WORLD)
        tree = hx.parse_html(world["html"])
        shown = _norm(hx.tree_text(hx.find_by_id(tree, "guard-alpha-headline")))
        assert HEADLINE_SHAPE.match(shown), f"headline {shown!r} is not '<sign><n.nn> pp'"
        assert shown == sup.fmt_pp(world["strips"]["30d"]["guard_alpha"]), (
            "first paint and the 30d strip answer disagree"
        )

    def test_descriptor_states_the_basis_and_says_simulated_while_the_engine_is_simulated(self):
        tree = hx.parse_html(hx.get_world(WORLD)["html"])
        descriptors = _text_of(tree, _has_class("guard-alpha-descriptor"))
        assert descriptors == [DESCRIPTOR_SIMULATED], descriptors

    def test_descriptor_drops_simulated_when_the_engine_is_live(self):
        tree = hx.parse_html(hx.get_world("review-fixes-live")["html"])
        descriptors = _text_of(tree, _has_class("guard-alpha-descriptor"))
        assert descriptors == [DESCRIPTOR_LIVE], (
            f"the word Simulated must follow the engine's flag: {descriptors}"
        )

    def test_the_cumulative_row_says_why_it_leaves_a_symphony_out(self):
        html = hx.get_world(WORLD)["html"]
        marker = re.search(
            r'data-testid="cumulative-cashflow-footnote-marker"[^>]*title="([^"]*)"', html
        )
        assert marker, "the cumulative row's footnote marker is gone"
        assert "Excludes symphonies with no Composer cost basis." in marker.group(1)

    @pytest.mark.parametrize("token, label", [("1y", "1Y"), ("all", "All Time")])
    def test_a_picked_window_writes_the_unit_and_the_label_together(self, token, label):
        r = _run(
            """
        await H.boot({ extras: true });
        await H.click('[data-testid="window-__T__"]');
        const picked = H.guardAlpha();
        H.route('/api/state', { body: H.bodies.pollNew });
        await H.runTimers(30000);
        return { picked: picked, after: H.guardAlpha() };
        """.replace("__T__", token)
        )
        strip = hx.get_world(WORLD)["strips"][token]
        assert r["picked"] == {"headline": sup.fmt_pp(strip["guard_alpha"]), "label": label}
        assert HEADLINE_SHAPE.match(r["picked"]["headline"]) or r["picked"]["headline"] == "--"
        assert r["after"] == r["picked"], "a later poll moved the headline or its label apart"


# ---------------------------------------------------------------------------
# AC-2: Max DD
# ---------------------------------------------------------------------------

_MDD_READ = """
const mdd = function () {
  const label = H.el('[data-testid="mdd-insufficient-label"]');
  return { bot: H.text('[data-testid="comp-mdd-bot-text"]'), held: H.text('[data-testid="comp-mdd-held-text"]'),
           delta: H.text('[data-testid="comp-mdd-delta"]'), insufficientShown: label.exists && !label.hidden };
};
"""


def _leg(prefix: str, value) -> str | None:
    return None if value is None else f"{prefix} {abs(value):.2f}%"


def test_max_dd_prints_the_same_text_at_first_paint_and_after_a_refresh():
    r = _run(
        _MDD_READ
        + """
        await H.boot({ extras: true });
        const first = mdd();
        H.route('/api/state', { body: H.bodies.pollNew });
        await H.runTimers(30000);
        return { first: first, after: mdd() };
        """
    )
    for side in ("bot", "held"):
        assert r["first"][side] == r["after"][side], (
            f"Max DD {side}: first paint {r['first'][side]!r}, after a refresh {r['after'][side]!r}"
        )
        assert "+" not in r["after"][side] and "+" not in r["first"][side], (
            "a drawdown is a magnitude and carries no plus sign"
        )
        assert LEG_SHAPE.match(r["after"][side]) or not re.search(r"\d", r["after"][side])


@pytest.mark.parametrize("token", ["30d", "60d", "1y"])
def test_max_dd_follows_the_picked_window_and_matches_the_strip(token):
    world = hx.get_world(WORLD)
    strip = world["strips"][token]["max_drawdown"]
    r = _run(
        _MDD_READ
        + """
        await H.boot({ extras: true });
        await H.click('[data-testid="window-__T__"]');
        const picked = mdd();
        H.route('/api/state', { body: H.bodies.pollNew });
        await H.runTimers(30000);
        return { picked: picked, after: mdd() };
        """.replace("__T__", token)
    )
    assert strip["dry_run"] is not None, "the world must supply drawdown for this window"
    assert r["picked"]["bot"] == _leg("Bot", strip["dry_run"]), (
        f"{token}: the Max DD row does not show the picked window's bot figure"
    )
    assert r["picked"]["held"] == _leg("Held", strip["if_held"])
    assert r["after"] == r["picked"], f"{token}: a refresh put another window's figure in the row"


def test_max_dd_says_so_when_the_window_cannot_supply_it():
    world = hx.get_world("onepoint")
    strip = world["strips"]["30d"]["max_drawdown"]
    assert strip["dry_run"] is None and strip["if_held"] is None, "the world must not supply it"
    r = _run(
        _MDD_READ
        + """
        await H.boot({ extras: true });
        const first = mdd();
        H.route('/api/state', { body: H.bodies.pollNew });
        await H.runTimers(30000);
        return { first: first, after: mdd() };
        """,
        world="onepoint",
    )
    for state in ("first", "after"):
        for side in ("bot", "held"):
            assert not re.search(r"\d", r[state][side] or ""), (
                f"{state}: the row shows {r[state][side]!r} although the window supplies no drawdown"
            )
        assert r[state]["insufficientShown"] is True, (
            f"{state}: the row does not say it has no figure"
        )


# ---------------------------------------------------------------------------
# AC-2: Ann. Vol
# ---------------------------------------------------------------------------

_VOL_ROW = """
const volRow = function () {
  let e = H.sandbox.document.querySelector('[data-testid="comp-vol-bot-text"]');
  while (e && e.getAttribute('data-testid') !== 'vs-row') e = e.parentNode;
  if (!e) return { found: false };
  return { found: true, hidden: !!e.hidden || (e.style && e.style.display === 'none'), texts: H.vol() };
};
"""


def test_the_vol_row_shows_the_picked_windows_figures_matching_performance():
    world = hx.get_world(WORLD)
    strip = world["strips"]["1y"]
    assert strip["vol_bot"] is not None, "the world must supply volatility for 1y"
    r = _run(
        _VOL_ROW
        + """
        await H.boot({ extras: true });
        await H.click('[data-testid="window-1y"]');
        return volRow();
        """
    )
    assert r["found"] and r["hidden"] is False
    assert r["texts"] == sup.vol_texts(strip)


def test_the_vol_row_is_hidden_not_dashed_when_there_is_no_value():
    world = hx.get_world("onepoint")
    assert world["strips"]["30d"]["vol_bot"] is None
    r = _run(
        _VOL_ROW
        + """
        await H.boot({ extras: true });
        const first = volRow();
        H.route('/api/state', { body: H.bodies.pollNew });
        await H.runTimers(30000);
        return { first: first, after: volRow() };
        """,
        world="onepoint",
    )
    assert r["first"]["found"] and r["first"]["hidden"] is True, "first paint shows a dead vol row"
    assert r["after"]["hidden"] is True, "a refresh brought the dead vol row back"


# ---------------------------------------------------------------------------
# AC-2: standby card without a Bot figure
# ---------------------------------------------------------------------------

_STANDBY_READ = """
const D = H.sandbox.document;
const card = function () {
  const c = D.querySelector('.sym-card[data-sym-id="__ID__"]');
  if (!c) return { found: false };
  const texts = function (field) { return c.querySelectorAll('[data-field="' + field + '"]').map(function (e) { return e.textContent.trim(); }); };
  return { found: true, bot: texts('tc-bot'), alpha: texts('tc-alpha-badge') };
};
""".replace("__ID__", hx.STANDBY_NO_ROWS_ID)


def test_a_card_with_no_bot_figure_shows_the_same_dash_alpha_at_first_paint_and_after_a_refresh():
    r = _run(
        _STANDBY_READ
        + """
        await H.boot({ extras: true });
        const first = card();
        H.route('/api/state', { body: H.bodies.pollNew });
        await H.runTimers(30000);
        return { first: first, after: card() };
        """
    )
    assert r["first"]["found"] and r["after"]["found"], "the standby entry must have a card"
    assert r["first"]["bot"] and all(not re.search(r"\d", t) for t in r["first"]["bot"]), (
        f"the world must give this card no Bot figure: {r['first']['bot']}"
    )
    assert r["first"]["alpha"] == r["after"]["alpha"], (
        f"alpha at first paint {r['first']['alpha']} differs from after a refresh {r['after']['alpha']}"
    )
    assert all(not re.search(r"\d", t) for t in r["first"]["alpha"]), (
        f"an alpha {r['first']['alpha']} was invented for a card that has no Bot figure"
    )
    assert r["first"]["bot"] == r["after"]["bot"], "the absent Bot marker changed between renders"


# ---------------------------------------------------------------------------
# AC-2: lock badge, hero chart tooltip, window button hover
# ---------------------------------------------------------------------------


def test_the_lock_count_badge_is_labelled():
    import copy

    polled = copy.deepcopy(_bodies()["pollNew"])
    polled.setdefault("dashboard_panels", {})["vars_locked_count"] = 12
    r = _run(
        """
        await H.boot({ extras: true });
        H.route('/api/state', { body: H.bodies.locked });
        await H.runTimers(30000);
        return H.el('[data-testid="edit-vars-indicator"]');
        """,
        extra={"locked": polled},
    )
    assert r["exists"] and r["hidden"] is False
    assert _norm(r["text"]) == "12 locked", f"the badge reads {r['text']!r}, a bare number"


def test_the_first_paint_lock_badge_template_carries_the_label_too():
    html = (REPO / "templates" / "index.html").read_text(encoding="utf-8")
    start = html.index('data-testid="edit-vars-indicator"')
    span = html[start : html.index("</span>", start)]
    assert "locked" in span, "the server-rendered badge still prints a bare count"


def test_the_hero_chart_shows_a_tooltip_on_hover_like_performance():
    r = _run(
        """
        await H.boot({ extras: true });
        const o = H.hero().options;
        const cb = o.plugins && o.plugins.tooltip && o.plugins.tooltip.callbacks && o.plugins.tooltip.callbacks.label;
        return { interaction: o.interaction || null, tooltip: o.plugins && o.plugins.tooltip || null,
                 label: cb ? cb({ dataset: { label: 'Bot' }, parsed: { y: 1.2345 } }) : null };
        """
    )
    assert r["interaction"] == {"mode": "index", "intersect": False}, (
        "without index mode and no intersect the tooltip appears only on an exact point"
    )
    assert r["tooltip"] is not None and r["tooltip"].get("enabled") is not False
    assert r["label"] and "Bot" in r["label"] and "1.23%" in r["label"], r["label"]


def test_window_buttons_have_a_hover_state():
    css = "\n".join(
        [(REPO / "templates" / "index.html").read_text(encoding="utf-8")]
        + [p.read_text(encoding="utf-8") for p in (REPO / "static").glob("*.css")]
    )
    rule = re.search(r"\.window-selector\s+button:hover\s*\{([^}]*)\}", css)
    assert rule, "no :hover rule for the window selector buttons"
    assert re.search(r"background|color", rule.group(1)), "the hover rule changes nothing"


# ---------------------------------------------------------------------------
# AC-3: a card opened before the first state answer
# ---------------------------------------------------------------------------


def test_a_card_opened_before_the_first_state_shows_its_name_and_a_loading_state_then_fills():
    world = hx.get_world(WORLD)
    triggered = hx.UNTRIGGERED_ID
    r = _run(
        """
        const D = H.sandbox.document;
        await H.boot({ extras: true, statePending: true });
        await H.press('.sym-card[data-sym-id="__ID__"]');
        const nameOnCard = (D.querySelector('.sym-card[data-sym-id="__ID__"] .card-name') || {}).textContent;
        const loading = D.querySelector('[data-testid="detail-loading"]');
        const risk = D.getElementById('detail-risk-profile');
        const waiting = { title: D.getElementById('detail-panel-title').textContent.trim(),
                          card: (nameOnCard || '').trim(),
                          loadingShown: !!loading && !loading.hidden && loading.textContent.trim().length > 0,
                          riskText: risk ? risk.textContent : '' };
        await H.releasePending('/api/state', { body: H.bodies.pollNew });
        await H.completeAnimations();
        const filled = D.querySelector('[data-testid="detail-loading"]');
        return { waiting: waiting, filledTitle: D.getElementById('detail-panel-title').textContent.trim(),
                 loadingGone: !filled || !!filled.hidden };
        """.replace("__ID__", triggered)
    )
    assert r["waiting"]["title"] == " ".join(r["waiting"]["card"].split()), (
        f"the panel is titled {r['waiting']['title']!r}, the card {r['waiting']['card']!r}"
    )
    assert r["waiting"]["title"] != "Symphony Detail"
    assert r["waiting"]["loadingShown"] is True, (
        "no loading state while the first answer is awaited"
    )
    assert "Select a symphony" not in r["waiting"]["riskText"], (
        "the panel tells the operator to select a symphony that was just selected"
    )
    assert r["filledTitle"] == world["bot_state"][triggered]["name"], "the panel did not fill"
    assert r["loadingGone"] is True, "the loading state stayed after the answer"
