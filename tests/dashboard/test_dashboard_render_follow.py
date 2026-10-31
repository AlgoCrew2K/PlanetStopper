"""Elements the server fixes at render follow the poll on ``/``: the mode pill and the hero legend, the
comparison bars' winner and loser, the Max DD labels, each card's lifetime Max DD, the Edit Vars badge,
and the Sleeves and CVaR panels.

Real call path: ``static/chrome.js`` and ``static/index.js`` run whole under node
(tests/dashboard/_index_js_harness.py) over the page ``GET /`` renders; the 30 s poll is the page's own
interval. The panels are judged by *cross-render*: what the poll leaves on the page equals what a fresh
server render of the same data prints (so the render and the poll print one text, whatever it is),
read by one extraction script run over both pages. The data changes through the real ``database``
writers (the ``cards-panels`` world); the failure shapes and the single-field edits are derived bodies:
tests/fixtures/refresh_timing/03-c5/PROVENANCE.md.

The element contract the tests address (the names are the handoff's, and the server render and the
poll write the same ones):

* mode: ``[data-testid="mode-pill"]`` (text, ``data-live``, background and colour tokens) and
  ``[data-field="legend-mode"]`` inside the hero legend's "Planet Stopper (...)";
* Max DD labels: ``[data-testid="mdd-insufficient-label"]`` with ``[data-testid="comp-mdd-delta"]`` in the
  hero row and ``[data-testid="card-mdd-insufficient-label"]`` with ``[data-field="mdd-alpha-badge"]`` on a
  card, one shown at a time; ``[data-field="mdd-lifetime"]`` on a card;
* Sleeves: ``[data-testid="sleeve-card"][data-sleeve-id]`` holding ``[data-field]`` ``sleeve-name``,
  ``sleeve-capital``, ``sleeve-ledger-cash``, ``sleeve-realized`` and ``[data-testid="sleeve-status-badge"]``;
  ``[data-testid="sleeve-rule-row"][data-rule-id]`` holding ``rule-name``, ``rule-mode``, ``rule-today``,
  ``rule-lifetime``, ``rule-realized`` and, in a SHADOW rule's row, ``[data-testid="sleeve-arm-control"]``;
  ``[data-testid="sleeves-empty"]`` for "No sleeves configured yet."; templates ``#sleeve-card-template``
  and ``#sleeve-rule-row-template``;
* CVaR: ``#cvar-diagnostic-panel`` (``hidden`` without a diagnostic) holding ``cvar-5pct``, ``cvar-stderr``,
  ``cvar-n-tail``; Edit Vars: ``[data-testid="edit-vars-indicator"]``.
"""

from __future__ import annotations

import copy
import re

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup
from tests.dashboard._first_paint_dom import js_to_fixed

WORLD = "default"
PANELS_WORLD = "cards-panels"
MDD_ROW = "mdd"
LIVE_LEGEND = "Planet Stopper (live)"
DRY_LEGEND = "Planet Stopper (dry-run)"
NEG, POS_WHITE, CHIP_BG, INK_DIM = (
    "var(--studio-neg)",
    "var(--studio-white)",
    "var(--studio-chip-bg)",
    "var(--studio-ink-dim)",
)
DASH = "—"

PRELUDE = r"""
const D = H.sandbox.document;
const hiddenOf = function (e) { return !e || e.hasAttribute('hidden') || e.style.display === 'none'; };
const txt = function (r, f) { const e = r.querySelector('[data-field="' + f + '"]'); return e ? e.textContent.trim() : null; };
const cls = function (e) { return e ? e.className.split(/\s+/).filter(Boolean).sort().join(' ') : null; };
const hasClass = function (e, c) { return !!e && e.className.split(/\s+/).indexOf(c) >= 0; };
const poll = async function (body) {
  H.route('/api/state', { body: body });
  await H.tick(); await H.completeAnimations();
};
const barRow = function (row, botId, heldId) {
  const bot = D.querySelector('[data-testid="' + botId + '"][data-row="' + row + '"]');
  const held = D.querySelector('[data-testid="' + heldId + '"][data-row="' + row + '"]');
  if (!bot || !held) return null;
  const labels = bot.closest('.vs-row').querySelectorAll('.vs-bar-label');
  const fill = function (e) { return ['winner', 'loser'].filter(function (c) { return hasClass(e, c); }).join(','); };
  return { botFill: fill(bot), heldFill: fill(held), botLabel: hasClass(labels[0], 'winner'),
           heldLabel: hasClass(labels[1], 'winner'), botLoserLabel: hasClass(labels[0], 'loser'),
           heldLoserLabel: hasClass(labels[1], 'loser') };
};
const bars = function () {
  return { today: barRow('today', 'comp-bar-bot', 'comp-bar-held'),
           cumulative: barRow('cumulative', 'comp-bar-bot', 'comp-bar-held'),
           mdd: barRow('mdd', 'comp-bar-bot', 'comp-bar-held'),
           vol: barRow('vol', 'comp-bar-vol-bot', 'comp-bar-vol-held') };
};
const mode = function () {
  const pill = D.querySelector('[data-testid="mode-pill"]');
  const word = D.querySelector('[data-field="legend-mode"]');
  return { text: pill ? pill.textContent.trim() : null, live: pill ? pill.getAttribute('data-live') : null,
           background: pill ? (pill.style.background || pill.style.backgroundColor || null) : null,
           color: pill ? (pill.style.color || null) : null,
           word: word ? word.textContent.trim() : null,
           legend: word && word.parentNode ? word.parentNode.textContent.replace(/\s+/g, ' ').trim() : null };
};
const mddLabels = function () {
  const card = D.querySelector('.sym-card[data-sym-id]');
  return { heroLabel: D.querySelector('[data-testid="mdd-insufficient-label"]') ? !hiddenOf(D.querySelector('[data-testid="mdd-insufficient-label"]')) : null,
           heroDelta: D.querySelector('[data-testid="comp-mdd-delta"]') ? !hiddenOf(D.querySelector('[data-testid="comp-mdd-delta"]')) : null,
           cardLabel: card.querySelector('[data-testid="card-mdd-insufficient-label"]') ? !hiddenOf(card.querySelector('[data-testid="card-mdd-insufficient-label"]')) : null,
           cardBadge: card.querySelector('[data-field="mdd-alpha-badge"]') ? !hiddenOf(card.querySelector('[data-field="mdd-alpha-badge"]')) : null };
};
const lifetimes = function () {
  const out = {};
  D.querySelectorAll('.sym-card[data-sym-id]').forEach(function (c) { out[c.getAttribute('data-sym-id')] = txt(c, 'mdd-lifetime'); });
  return out;
};
const panels = function () {
  const sleeves = Array.prototype.map.call(D.querySelectorAll('[data-testid="sleeve-card"]'), function (c) {
    const badge = c.querySelector('[data-testid="sleeve-status-badge"]');
    const disarm = c.querySelector('[data-testid="sleeve-disarm-control"]');
    return {
      id: c.getAttribute('data-sleeve-id'), name: txt(c, 'sleeve-name'), capital: txt(c, 'sleeve-capital'),
      ledger: txt(c, 'sleeve-ledger-cash'), realized: txt(c, 'sleeve-realized'),
      status: badge ? badge.textContent.trim() : null, badgeClass: cls(badge),
      disarmDisabled: disarm ? disarm.hasAttribute('disabled') : null,
      rules: Array.prototype.map.call(c.querySelectorAll('[data-testid="sleeve-rule-row"]'), function (r) {
        return { id: r.getAttribute('data-rule-id'), name: txt(r, 'rule-name'), mode: txt(r, 'rule-mode'),
                 today: txt(r, 'rule-today'), lifetime: txt(r, 'rule-lifetime'), realized: txt(r, 'rule-realized'),
                 hasArm: !!r.querySelector('[data-testid="sleeve-arm-control"]') };
      })
    };
  });
  const empty = D.querySelector('[data-testid="sleeves-empty"]');
  const cv = D.getElementById('cvar-diagnostic-panel');
  const vars = D.querySelector('[data-testid="edit-vars-indicator"]');
  return {
    sleeves: sleeves, emptyShown: empty ? !hiddenOf(empty) : null,
    cvar: cv ? { hidden: hiddenOf(cv), five: txt(cv, 'cvar-5pct'), stderr: txt(cv, 'cvar-stderr'), nTail: txt(cv, 'cvar-n-tail') } : null,
    vars: vars ? { hidden: hiddenOf(vars), text: vars.textContent.trim() } : null,
    lifetimes: lifetimes()
  };
};
const signature = function (e) {
  const data = {};
  e._attrs.forEach(function (v, k) { if (k.indexOf('data-') === 0) data[k] = v; });
  return { tag: e.localName, cls: cls(e), data: data, hidden: e.hasAttribute('hidden'),
           disabled: e.hasAttribute('disabled'), type: e.getAttribute('type'),
           children: e.children.map(signature) };
};
"""


def _world(name: str = WORLD) -> dict:
    return hx.get_world(name)


def _stamp(world: dict) -> str:
    return sup.render_stamp(world)


def _same_stamp(world: dict) -> dict:
    return sup.with_stamp(world["state"], _stamp(world))


def _run(scenario: str, world_name: str = WORLD, *, world: dict | None = None, **bodies) -> dict:
    built = world or _world(world_name)
    return hx.run_scenario(
        built,
        PRELUDE + scenario,
        scripts=sup.CHROME_AND_INDEX,
        extra_bodies=bodies,
        now_iso=hx.seeded_now_iso(built),
    )["result"]


def _with_meta(state: dict, **fields) -> dict:
    out = copy.deepcopy(state)
    out["meta"].update(fields)
    return out


def _with_portfolio_meta(state: dict, **fields) -> dict:
    out = copy.deepcopy(state)
    out["meta"]["portfolio"].update(fields)
    return out


def _with_strip_legs(state: dict, row: str, bot, held) -> dict:
    out = copy.deepcopy(state)
    out["portfolio_strip"][row] = {**out["portfolio_strip"][row], "dry_run": bot, "if_held": held}
    return out


def _with_panels(state: dict, **parts) -> dict:
    base = {"sleeves": [], "cvar": {"ok": True, "diagnostic": None}, "vars_locked_count": 0}
    out = copy.deepcopy(state)
    out["dashboard_panels"] = {**base, **parts}
    return out


# ---------------------------------------------------------------------------
# S-6.1: the mode pill and the hero legend
# ---------------------------------------------------------------------------


class TestTheModeFollowsThePoll:
    def test_a_poll_that_reports_live_turns_the_pill_and_the_legend_live(self):
        state = _with_meta(_same_stamp(_world()), mode="LIVE")
        r = _run(
            r"""
        await H.boot({ extras: true });
        const rendered = mode();
        await poll(H.bodies.live);
        return { rendered: rendered, after: mode() };
        """,
            live=state,
        )
        assert r["rendered"]["text"] == "DRY RUN" and r["rendered"]["live"] == "false"
        after = r["after"]
        assert after["text"] == "LIVE" and after["live"] == "true"
        assert after["background"] == NEG and after["color"] == POS_WHITE
        assert after["word"] == "live" and after["legend"] == LIVE_LEGEND

    def test_a_poll_that_reports_dry_run_turns_a_live_page_back(self):
        live = _with_meta(_same_stamp(_world()), mode="LIVE")
        dry = _same_stamp(_world())
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.live);
        await poll(H.bodies.dry);
        return { after: mode() };
        """,
            live=live,
            dry=dry,
        )
        after = r["after"]
        assert after["text"] == "DRY RUN" and after["live"] == "false"
        assert after["background"] == CHIP_BG and after["color"] == INK_DIM
        assert after["word"] == "dry-run" and after["legend"] == DRY_LEGEND

    @pytest.mark.parametrize("shape", ["no_mode", "no_meta", "unknown_mode"])
    def test_a_poll_with_no_usable_mode_writes_nothing(self, shape):
        state = copy.deepcopy(_same_stamp(_world()))
        if shape == "no_mode":
            del state["meta"]["mode"]
        elif shape == "unknown_mode":
            state["meta"]["mode"] = None
        else:
            del state["meta"]
        r = _run(
            r"""
        await H.boot({ extras: true });
        const before = mode();
        await poll(H.bodies.edited);
        return { before: before, after: mode() };
        """,
            edited=state,
        )
        assert r["after"] == r["before"]

    def test_the_pill_writer_is_defined_for_every_page_and_needs_no_legend(self):
        built = _world()
        r = hx.run_scenario(
            built,
            PRELUDE
            + r"""
        const word = D.querySelector('[data-field="legend-mode"]');
        if (word) word.parentNode.removeChild(word);
        const present = typeof H.sandbox.window.updateModeIndicators;
        let threw = null;
        try { H.sandbox.window.updateModeIndicators({ mode: 'LIVE' }); } catch (e) { threw = String(e); }
        const pill = D.querySelector('[data-testid="mode-pill"]');
        return { present: present, threw: threw, text: pill.textContent.trim(), live: pill.getAttribute('data-live') };
        """,
            scripts=("static/chrome.js",),
            now_iso=hx.seeded_now_iso(built),
        )["result"]
        assert r["present"] == "function", "chrome.js (on every page) defines no mode writer"
        assert r["threw"] is None
        assert r["text"] == "LIVE" and r["live"] == "true"


# ---------------------------------------------------------------------------
# S-6.2 to S-6.5: the winner and loser classes
# ---------------------------------------------------------------------------


def _wins(row: str, bot, held) -> bool | None:
    """Whether the bot side wins a row by the rule the server render applies to it (a missing leg of
    Today, Cumulative and Max DD reads as 0, as the template does); None when no side may win."""
    if row == "vol":
        return None if bot is None or held is None else bot <= held
    b, h = (bot or 0.0), (held or 0.0)
    return abs(b) <= abs(h) if row == MDD_ROW else b >= h


def _expect_classes(bot_wins: bool | None) -> dict:
    if bot_wins is None:
        return {
            "botFill": "",
            "heldFill": "",
            "botLabel": False,
            "heldLabel": False,
            "botLoserLabel": False,
            "heldLoserLabel": False,
        }
    return {
        "botFill": "winner" if bot_wins else "loser",
        "heldFill": "loser" if bot_wins else "winner",
        "botLabel": bot_wins,
        "heldLabel": not bot_wins,
        "botLoserLabel": False,
        "heldLoserLabel": False,
    }


LEG_CASES = [
    pytest.param(2.0, 1.0, id="bot-ahead"),
    pytest.param(1.0, 2.0, id="held-ahead"),
    pytest.param(1.5, 1.5, id="equal"),
    pytest.param(None, 1.5, id="bot-missing"),
    pytest.param(None, None, id="both-missing"),
]


class TestTheComparisonBarsFollowThePoll:
    @pytest.mark.parametrize("row", ["today_change", "cumulative_return"])
    @pytest.mark.parametrize(("bot", "held"), LEG_CASES)
    def test_today_and_cumulative_follow_the_legs_a_poll_carries(self, row, bot, held):
        state = _with_strip_legs(_same_stamp(_world()), row, bot, held)
        key = {"today_change": "today", "cumulative_return": "cumulative"}[row]
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.edited);
        return { bars: bars() };
        """,
            edited=state,
        )
        assert r["bars"][key] == _expect_classes(_wins(key, bot, held))

    @pytest.mark.parametrize(("bot", "held"), LEG_CASES)
    def test_max_drawdown_follows_the_legs_when_the_history_is_long_enough(self, bot, held):
        state = _with_strip_legs(_same_stamp(_world()), "max_drawdown", bot, held)
        state = _with_portfolio_meta(state, insufficient_history=False)
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.edited);
        return { bars: bars() };
        """,
            edited=state,
        )
        assert r["bars"][MDD_ROW] == _expect_classes(_wins(MDD_ROW, bot, held))

    def test_with_too_little_history_neither_max_drawdown_bar_carries_a_class(self):
        state = _with_strip_legs(_same_stamp(_world()), "max_drawdown", 0.5, 2.0)
        state = _with_portfolio_meta(state, insufficient_history=True)
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.edited);
        return { bars: bars() };
        """,
            edited=state,
        )
        assert r["bars"][MDD_ROW] == _expect_classes(None)

    @pytest.mark.parametrize(
        ("bot", "held"),
        [
            pytest.param(0.10, 0.20, id="bot-calmer"),
            pytest.param(0.20, 0.10, id="held-calmer"),
            pytest.param(0.15, 0.15, id="equal"),
            pytest.param(None, 0.15, id="bot-missing"),
            pytest.param(None, None, id="both-missing"),
        ],
    )
    def test_ann_vol_prefers_the_lower_vol_and_marks_no_side_without_both(self, bot, held):
        world = _world()
        strip = sup.strip_with(world["strips"]["30d"], vol_bot=bot, vol_held=held)
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.same });
        await poll(H.bodies.later);
        return { bars: bars() };
        """,
            strips={**world["strips"], "30d": strip},
            same=_same_stamp(world),
            later=sup.with_stamp(world["next"]["state"], sup.later_stamp(_stamp(world))),
        )
        assert r["bars"]["vol"] == _expect_classes(_wins("vol", bot, held))

    def test_a_poll_that_swaps_the_leaders_swaps_the_classes_it_wrote_before(self):
        world = _world()
        ahead = _with_strip_legs(_same_stamp(world), "today_change", 2.0, 1.0)
        behind = _with_strip_legs(_same_stamp(world), "today_change", 1.0, 2.0)
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.ahead);
        const first = bars().today;
        await poll(H.bodies.behind);
        return { first: first, second: bars().today };
        """,
            ahead=ahead,
            behind=behind,
        )
        assert r["first"] == _expect_classes(True)
        assert r["second"] == _expect_classes(False)

    def test_a_poll_with_the_data_the_page_rendered_leaves_the_classes_the_server_wrote(self):
        """Cross-render: the rule the client applies is the rule the template applies."""
        world = _world()
        r = _run(
            r"""
        await H.boot({ extras: true });
        const rendered = bars();
        await poll(H.bodies.same);
        return { rendered: rendered, after: bars() };
        """,
            same=_same_stamp(world),
        )
        for key in ("today", "cumulative", "mdd"):
            assert r["after"][key] == r["rendered"][key], f"the {key} row's classes moved"


# ---------------------------------------------------------------------------
# S-6.4, S-6.8: the Max DD labels and each card's lifetime figure
# ---------------------------------------------------------------------------


class TestTheMaxDrawdownLabelsFollowThePoll:
    def test_a_poll_with_too_little_history_shows_the_label_and_hides_the_alpha(self):
        world = _world()
        short = _with_portfolio_meta(_same_stamp(world), insufficient_history=True)
        long = _with_portfolio_meta(_same_stamp(world), insufficient_history=False)
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.short);
        const short = mddLabels();
        await poll(H.bodies.long);
        return { short: short, long: mddLabels() };
        """,
            short=short,
            long=long,
        )
        assert r["short"] == {
            "heroLabel": True,
            "heroDelta": False,
            "cardLabel": True,
            "cardBadge": False,
        }
        assert r["long"] == {
            "heroLabel": False,
            "heroDelta": True,
            "cardLabel": False,
            "cardBadge": True,
        }


class TestEachCardsLifetimeMaxDrawdownFollowsThePoll:
    @pytest.mark.parametrize(
        "value", [12.34, -12.34, 0.0, None], ids=["pos", "neg", "zero", "none"]
    )
    def test_the_figure_a_poll_carries_is_the_one_printed(self, value):
        world = _world()
        symphony_id = world["ids"][0]
        state = sup.with_card_only(_same_stamp(world), symphony_id, mdd_lifetime=value)
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.edited);
        return { shown: lifetimes() };
        """,
            edited=state,
        )
        expected = "--" if value is None else js_to_fixed([abs(value)], 1)[0] + "%"
        assert r["shown"][symphony_id] == expected

    def test_a_figure_that_changes_between_polls_changes_on_the_card(self):
        world = _world()
        symphony_id = world["ids"][0]
        first = sup.with_card_only(_same_stamp(world), symphony_id, mdd_lifetime=5.0)
        second = sup.with_card_only(_same_stamp(world), symphony_id, mdd_lifetime=9.0)
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.first);
        const a = lifetimes();
        await poll(H.bodies.second);
        return { a: a, b: lifetimes() };
        """,
            first=first,
            second=second,
        )
        assert r["a"][symphony_id] == "5.0%" and r["b"][symphony_id] == "9.0%"


# ---------------------------------------------------------------------------
# S-6.6, S-6.7, S-6.9, S-6.11, S-6.12: the Sleeves and CVaR panels, the Edit Vars badge
# ---------------------------------------------------------------------------


def _panels_world() -> dict:
    world = hx.get_world(PANELS_WORLD)
    assert "dashboard_panels" in world["state"], (
        "the poll carries no dashboard_panels (first state)"
    )
    assert "dashboard_panels" in world["next"]["state"], (
        "the poll carries no dashboard_panels (later)"
    )
    return world


def _server_panels() -> tuple[dict, dict]:
    """The panels the server render prints for the first and for the later data, read by the same
    extraction script that reads the live page."""
    world = _panels_world()
    out = []
    for html in (world["html"], world["next"]["html"]):
        read = hx.run_scenario(
            {**world, "html": html},
            PRELUDE + "return panels();",
            scripts=sup.CHROME_AND_INDEX,
            now_iso=hx.seeded_now_iso(world),
        )["result"]
        out.append(read)
    return out[0], out[1]


class TestThePanelsFollowThePoll:
    def test_the_page_before_any_poll_prints_the_panels_the_poll_will_print(self):
        """The extraction script reads the render; the first poll must leave it as it is."""
        world = _panels_world()
        first, _later = _server_panels()
        assert first["sleeves"], "the seeded world renders no sleeve"
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.state);
        return panels();
        """,
            world=world,
            state=world["state"],
        )
        assert r == first

    def test_after_the_data_changes_the_poll_leaves_the_panels_a_fresh_render_prints(self):
        world = _panels_world()
        _first, later = _server_panels()
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.stateNext);
        return panels();
        """,
            world=world,
        )
        assert r == later, (
            "the panels after the poll differ from a fresh server render of the same data"
        )

    def test_a_rules_fire_count_for_today_goes_up_by_the_fire_that_was_written(self):
        world = _panels_world()
        r = _run(
            r"""
        await H.boot({ extras: true });
        const before = panels();
        await poll(H.bodies.stateNext);
        return { before: before, after: panels() };
        """,
            world=world,
        )
        rule_id = str(world["panel_ids"]["r1"])

        def today(read: dict) -> int:
            rows = [x for s in read["sleeves"] for x in s["rules"] if x["id"] == rule_id]
            assert rows, f"rule {rule_id} has no row"
            return int(re.search(r"\d+", rows[0]["today"]).group(0))

        assert today(r["after"]) == today(r["before"]) + 1

    def test_the_cvar_values_change_and_a_hidden_panel_is_shown_by_a_poll(self):
        world = _panels_world()
        diagnostic = world["next"]["state"]["dashboard_panels"]["cvar"]
        assert diagnostic["diagnostic"], "the later world carries a diagnostic"
        bare = _world("cards-open")
        r = _run(
            r"""
        await H.boot({ extras: true });
        const before = panels().cvar;
        await poll(H.bodies.withDiagnostic);
        return { before: before, after: panels().cvar };
        """,
            world=bare,
            withDiagnostic=_with_panels(_same_stamp(bare), cvar=diagnostic),
        )
        assert r["before"] is not None and r["before"]["hidden"] is True
        assert r["after"]["hidden"] is False
        _first, later = _server_panels()
        assert r["after"]["five"] == later["cvar"]["five"]
        assert r["after"]["nTail"] == later["cvar"]["nTail"]

    def test_a_poll_with_no_diagnostic_hides_a_panel_that_was_shown(self):
        world = _panels_world()
        r = _run(
            r"""
        await H.boot({ extras: true });
        const before = panels().cvar;
        await poll(H.bodies.none);
        return { before: before, after: panels().cvar };
        """,
            world=world,
            none=_with_panels(world["state"], cvar={"ok": True, "diagnostic": None}),
        )
        assert r["before"]["hidden"] is False
        assert r["after"]["hidden"] is True

    def test_the_edit_vars_badge_shows_the_count_and_hides_at_zero(self):
        world = _world("cards-open")
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.three);
        const three = panels().vars;
        await poll(H.bodies.zero);
        return { three: three, zero: panels().vars };
        """,
            world=world,
            three=_with_panels(_same_stamp(world), vars_locked_count=3),
            zero=_with_panels(_same_stamp(world), vars_locked_count=0),
        )
        assert r["three"] == {"hidden": False, "text": "3"}
        assert r["zero"]["hidden"] is True and r["zero"]["text"] == "", (
            "a badge hidden at zero must also be empty (the server render's empty state)"
        )

    def test_a_part_whose_read_failed_keeps_what_the_page_shows(self):
        world = hx.get_world(PANELS_WORLD)
        failed = _with_panels(
            world["state"],
            sleeves=None,
            cvar={"ok": False, "diagnostic": None},
            vars_locked_count=None,
        )
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.state);
        const before = panels();
        await poll(H.bodies.failed);
        return { before: before, after: panels() };
        """,
            world=world,
            state=world["state"],
            failed=failed,
        )
        assert r["after"] == r["before"]

    def test_a_poll_without_the_panels_key_leaves_them_alone(self):
        world = hx.get_world(PANELS_WORLD)
        bare = copy.deepcopy(world["state"])
        bare.pop("dashboard_panels", None)
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.state);
        const before = panels();
        await poll(H.bodies.bare);
        return { before: before, after: panels() };
        """,
            world=world,
            state=world["state"],
            bare=bare,
        )
        assert r["after"] == r["before"]

    def test_two_polls_answering_out_of_order_leave_the_newer_ones_panels(self):
        world = _panels_world()
        _first, later = _server_panels()
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route('/api/state', { pending: true });
        await H.tick(); await H.tick();
        await H.releaseNth('/api/state', 1, { body: H.bodies.stateNext });  // the newer answers first
        await H.releaseNth('/api/state', 0, { body: H.bodies.state });      // the older lands last
        await H.completeAnimations();
        return panels();
        """,
            world=world,
        )
        assert r == later, "the older poll's panels replaced the newer one's"

    def test_a_rule_added_and_another_removed_appear_and_disappear_without_a_reload(self):
        world = _panels_world()
        ids = world["panel_ids"]
        r = _run(
            r"""
        await H.boot({ extras: true });
        D.querySelectorAll('[data-testid="sleeve-card"], [data-testid="sleeve-rule-row"]').forEach(function (e) { e.__kept = true; });
        await poll(H.bodies.stateNext);
        const survivors = Array.prototype.map.call(
          D.querySelectorAll('[data-testid="sleeve-card"], [data-testid="sleeve-rule-row"]'),
          function (e) { return { id: e.getAttribute('data-rule-id') || e.getAttribute('data-sleeve-id'), kept: !!e.__kept }; });
        return { survivors: survivors, reloads: H.reloads, panels: panels() };
        """,
            world=world,
        )
        sleeve_rows = {s["id"]: [x["id"] for x in s["rules"]] for s in r["panels"]["sleeves"]}
        assert str(ids["r4"]) in sleeve_rows[str(ids["s1"])], "the added rule has no row"
        assert str(ids["r2"]) not in sleeve_rows[str(ids["s1"])], "the removed rule's row stayed"
        assert str(ids["s3"]) in sleeve_rows, "the added sleeve has no card"
        assert str(ids["s2"]) not in sleeve_rows, "the deleted sleeve's card stayed"
        kept = {x["id"]: x["kept"] for x in r["survivors"]}
        assert kept[str(ids["s1"])] and kept[str(ids["r1"])], "an unchanged element was rebuilt"
        assert r["reloads"] == 0

    def test_a_row_added_by_the_poll_has_the_markup_of_a_row_the_server_renders(self):
        world = _panels_world()
        ids = world["panel_ids"]
        scenario_sig = r"""
        const rule = D.querySelector('[data-testid="sleeve-rule-row"][data-rule-id="__RULE__"]');
        const sleeve = D.querySelector('[data-testid="sleeve-card"][data-sleeve-id="__SLEEVE__"]');
        return { rule: rule ? signature(rule) : null, sleeve: sleeve ? signature(sleeve) : null };
        """.replace("__RULE__", str(ids["r4"])).replace("__SLEEVE__", str(ids["s3"]))
        server = hx.run_scenario(
            {**world, "html": world["next"]["html"]},
            PRELUDE + scenario_sig,
            scripts=sup.CHROME_AND_INDEX,
            now_iso=hx.seeded_now_iso(world),
        )["result"]
        assert server["rule"] and server["sleeve"], "the later server render lacks the added rows"
        client = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.stateNext);
        """
            + scenario_sig,
            world=world,
        )
        assert client["rule"] == server["rule"], "a cloned rule row differs from the rendered one"
        assert client["sleeve"] == server["sleeve"], (
            "a cloned sleeve card differs from the rendered one"
        )

    def test_the_buttons_of_a_row_added_by_the_poll_call_the_routes_the_rendered_ones_call(self):
        world = _panels_world()
        ids = world["panel_ids"]
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.stateNext);
        H.route(/^\/api\/sleeves\//, { body: {} });
        const rule = D.querySelector('[data-testid="sleeve-rule-row"][data-rule-id="__R5__"]');
        const card = D.querySelector('[data-testid="sleeve-card"][data-sleeve-id="__S3__"]');
        const arm = rule.querySelector('[data-testid="sleeve-arm-control"]');
        const disarm = card.querySelector('[data-testid="sleeve-disarm-control"]');
        const del = card.querySelector('[data-testid="sleeve-delete-control"]');
        const posts = function () { return H.fetchLog.filter(function (e) { return e.method === 'POST'; }); };
        const n0 = posts().length;
        arm.click(); await H.settle();
        const afterArm = posts().slice(n0).map(function (e) { return { url: e.url, body: e.body, csrf: e.headers['X-CSRF-Token'] }; });
        const reloadsAfterArm = H.reloads;
        del.click(); await H.settle();
        const afterDelete = posts().slice(n0 + afterArm.length).map(function (e) { return { url: e.url }; });
        return { hasArm: !!arm, disarmDisabled: disarm.hasAttribute('disabled'), afterArm: afterArm,
                 reloadsAfterArm: reloadsAfterArm, afterDelete: afterDelete };
        """.replace("__R5__", str(ids["r5"])).replace("__S3__", str(ids["s3"])),
            world=world,
        )
        assert r["hasArm"], "a SHADOW rule's row has no Arm Paper button"
        assert r["disarmDisabled"], "a SHADOW sleeve's Disarm button must be disabled, as rendered"
        assert [a["url"] for a in r["afterArm"]] == [
            f"/api/sleeves/{ids['s3']}/rules/{ids['r5']}/arm"
        ]
        assert r["afterArm"][0]["body"] == '{"target_mode":"PAPER"}'
        assert r["afterArm"][0]["csrf"], (
            "the request must carry the CSRF token the rendered buttons send"
        )
        assert r["reloadsAfterArm"] == 1
        assert [a["url"] for a in r["afterDelete"]] == [f"/api/sleeves/{ids['s3']}/delete"]

    def test_no_panel_write_goes_through_inner_html(self):
        world = _panels_world()
        r = _run(
            r"""
        await H.boot({ extras: true });
        const n0 = H.htmlWrites.length;
        await poll(H.bodies.stateNext);
        return { writes: H.htmlWrites.slice(n0).filter(function (w) {
          return w.within.indexOf('sleeves-panel') >= 0 || w.testid === 'sleeves-panel' || w.id === 'cvar-diagnostic-panel'
            || w.within.indexOf('sym-card') >= 0; }).length };
        """,
            world=world,
        )
        assert r["writes"] == 0

    def test_hostile_text_in_a_sleeve_or_rule_name_shows_literally(self):
        world = _panels_world()
        hostile = "<img src=x onerror=alert(1)>"
        state = copy.deepcopy(world["next"]["state"])
        panels = state["dashboard_panels"]
        panels["sleeves"][0]["name"] = hostile
        panels["sleeves"][0]["rules"][0]["name"] = hostile
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.hostile);
        const read = panels();
        return { name: read.sleeves[0].name, rule: read.sleeves[0].rules[0].name,
                 images: D.querySelectorAll('img').length };
        """,
            world=world,
            hostile=state,
        )
        assert r["name"] == hostile and r["rule"] == hostile
        assert r["images"] == 0


class TestTheSleevesButtonsBehaveAsTheyDid:
    @pytest.mark.parametrize(
        ("call", "url"),
        [
            ("disarmSleeve({s}, btn)", "/api/sleeves/{s}/disarm"),
            ("deleteSleeve({s}, btn)", "/api/sleeves/{s}/delete"),
        ],
    )
    def test_the_rendered_buttons_still_call_their_routes_and_reload(self, call, url):
        world = hx.get_world(PANELS_WORLD)
        sid = world["panel_ids"]["s1"]
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.state);
        H.route(/^\/api\/sleeves\//, { body: {} });
        const btn = D.querySelector('[data-testid="sleeve-card"][data-sleeve-id="__S__"] button');
        const n0 = H.fetchLog.length;
        H.sandbox.window.__CALL__;
        await H.settle();
        return { posts: H.fetchLog.slice(n0).filter(function (e) { return e.method === 'POST'; }).map(function (e) { return e.url; }),
                 reloads: H.reloads };
        """.replace("__S__", str(sid)).replace("__CALL__", call.format(s=sid)),
            world=world,
            state=world["state"],
        )
        assert r["posts"] == [url.format(s=sid)]
        assert r["reloads"] == 1

    def test_a_poll_leaves_the_rendered_buttons_as_they_were(self):
        world = hx.get_world(PANELS_WORLD)
        r = _run(
            r"""
        await H.boot({ extras: true });
        const removedRule = '__R2__';
        const ofSurvivingRow = function (b) { const row = b.closest('[data-testid="sleeve-rule-row"]'); return !row || row.getAttribute('data-rule-id') !== removedRule; };
        const buttons = Array.prototype.slice.call(D.querySelectorAll('[data-testid="sleeve-card"][data-sleeve-id="__S1__"] button')).filter(ofSurvivingRow);
        const attrs = buttons.map(function (b) { return b.getAttribute('onclick'); });
        buttons.forEach(function (b) { b.__kept = true; });
        await poll(H.bodies.stateNext);
        const after = Array.prototype.slice.call(D.querySelectorAll('[data-testid="sleeve-card"][data-sleeve-id="__S1__"] button'));
        return { attrs: attrs, afterAttrs: after.filter(function (b) { return b.__kept; }).map(function (b) { return b.getAttribute('onclick'); }),
                 kept: after.filter(function (b) { return b.__kept; }).length, count: buttons.length };
        """.replace("__S1__", str(world["panel_ids"]["s1"])).replace(
                "__R2__", str(world["panel_ids"]["r2"])
            ),
            world=world,
        )
        assert r["kept"] == r["count"] > 0, "a poll rebuilt a button the server rendered"
        assert r["afterAttrs"] == r["attrs"]
