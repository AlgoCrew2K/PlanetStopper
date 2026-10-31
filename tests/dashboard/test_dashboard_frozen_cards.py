"""After the close and before the open, every card, verdict and section badge on ``/`` matches the poll.

Real call path: ``static/chrome.js`` and ``static/index.js`` run whole under node
(tests/dashboard/_index_js_harness.py) over the page ``GET /`` renders, with the bodies the real
``/api/state`` route returns after the close and before the next open (the card worlds of
tests/app/_refresh_timing_card_world.py: one triggered and one armed symphony). The page is read from
the DOM: section, status pill, the Today, Cumulative and Max DD texts, the verdict text and its basis
label, the two section badges.

The verdict wording is the server's: ``app._first_paint_verdict`` is the server render's rule for
the same guard alpha, so a poll that disagrees with it is a flicker. Derived bodies change only the
named fields of one real body: tests/fixtures/refresh_timing/03-c4/PROVENANCE.md.
"""

from __future__ import annotations

import copy

import pytest

import app as app_module
from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup
from tests.dashboard._first_paint_dom import js_to_fixed

OPEN_WORLD = "cards-open"
FROZEN_WORLD = "cards-frozen"
PREMARKET_WORLD = "cards-premarket"
TRIGGERED = hx.CARD_TRIGGERED_ID
ARMED = hx.CARD_ARMED_ID
DASH = "—"
JS_TO_FIXED_BOUND = 1e21  # the server render's bound: from here toFixed prints exponent text


@pytest.fixture(autouse=True)
def _worlds_built_before_a_failure_is_staged():
    """Worlds are captured through the app's own routes; a staged 500 must not be what builds them."""
    for name in (OPEN_WORLD, FROZEN_WORLD, PREMARKET_WORLD):
        hx.get_world(name)


CARDS = r"""
const sectionOf = function (c) {
  return c.closest('[data-testid="active-section"]') ? 'active'
       : (c.closest('[data-testid="standby-section"]') ? 'standby' : null);
};
const cards = function () {
  const out = {};
  H.sandbox.document.querySelectorAll('.sym-card[data-sym-id]').forEach(function (c) {
    const text = function (sel) { const e = c.querySelector(sel); return e ? e.textContent.trim() : null; };
    const field = function (f) { return text('[data-field="' + f + '"]'); };
    const v = c.querySelector('[data-testid="triggered-verdict"]');
    const basis = c.querySelector('[data-testid="guard-alpha-basis"]');
    out[c.getAttribute('data-sym-id')] = {
      section: sectionOf(c), hidden: c.hidden || c.style.display === 'none',
      pill: text('[data-testid="status-pill"]'),
      tcBot: field('tc-bot'), crBot: field('cr-bot'), mddBot: field('mdd-bot'),
      tcHeld: field('tc-held'), crHeld: field('cr-held'), mddHeld: field('mdd-held'),
      verdictShown: v ? v.style.display !== 'none' : null,
      verdictText: field('verdict-text'),
      basisHidden: basis ? basis.hasAttribute('hidden') : null
    };
  });
  return out;
};
const order = function (section) {
  return Array.prototype.map.call(
    H.sandbox.document.querySelectorAll('[data-testid="' + section + '-section"] .sym-card'),
    function (c) { return c.getAttribute('data-sym-id'); });
};
const badges = function () {
  return { active: H.text('[data-testid="active-section-count"]'),
           standby: H.text('[data-testid="standby-section-count"]') };
};
const visibleIn = function (section) {
  return Array.prototype.filter.call(
    H.sandbox.document.querySelectorAll('[data-testid="' + section + '-section"] .cards-grid .sym-card'),
    function (c) { return !c.hidden && c.style.display !== 'none'; }).length;
};
const poll = async function (body) {
  H.route('/api/state', { body: body });
  await H.tick(); await H.completeAnimations();
};
"""


def _world(name: str = FROZEN_WORLD) -> dict:
    return hx.get_world(name)


def _run(scenario: str, world: str = FROZEN_WORLD, **bodies) -> dict:
    return sup.run(CARDS + scenario, world=world, scripts=sup.CHROME_AND_INDEX, extra_bodies=bodies)


def _server_card(world: str, symphony_id: str) -> dict:
    """What the server render of a world shows on one card (verdict text, basis hidden, pill)."""
    tree = hx.parse_html(_world(world)["html"])
    card = hx.find_all(
        tree,
        lambda n: (
            n["a"].get("data-testid") == "sym-card" and n["a"].get("data-sym-id") == symphony_id
        ),
    )[0]

    def first(testid=None, field=None):
        hits = hx.find_all(
            card,
            lambda n: (
                (testid and n["a"].get("data-testid") == testid)
                or (field and n["a"].get("data-field") == field)
            ),
        )
        return hits[0] if hits else None

    verdict_text = first(field="verdict-text")
    basis = first(testid="guard-alpha-basis")
    return {
        "verdict": " ".join(hx.tree_text(verdict_text).split()) if verdict_text else None,
        "basis_hidden": "hidden" in basis["a"] if basis else None,
        "pill": " ".join(hx.tree_text(first(testid="status-pill")).split()),
    }


def _frozen() -> dict:
    return _world(FROZEN_WORLD)["state"]


def _guard_alpha(state: dict, symphony_id: str):
    return sup.card_entry(state, symphony_id).get("guard_alpha")


def _verdict_text(guard_alpha: float) -> str:
    """The server render's verdict text for a guard alpha (the rule the client must equal)."""
    return app_module._first_paint_verdict({"guard_alpha": guard_alpha})["_verdict_text"]


# ---------------------------------------------------------------------------
# S-5.1, S-5.2, S-5.8: the first frozen poll and the ones after it
# ---------------------------------------------------------------------------


class TestFrozenPollsKeepTheRenderedCards:
    def test_the_first_frozen_poll_after_an_open_page_writes_the_triggered_verdict(self):
        frozen = _frozen()
        ga = _guard_alpha(frozen, TRIGGERED)
        assert isinstance(ga, (int, float)), (
            "the frozen response must carry the triggered guard alpha"
        )
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.frozen);
        return { cards: cards() };
        """,
            world=OPEN_WORLD,
            frozen=frozen,
        )
        triggered = r["cards"][TRIGGERED]
        assert triggered["verdictShown"] is True
        assert triggered["verdictText"] == _verdict_text(ga), "never the dash for a real figure"
        assert triggered["basisHidden"] is False
        assert r["cards"][ARMED]["verdictShown"] is False

    def test_three_frozen_polls_leave_every_card_as_the_server_rendered_it(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        const rendered = cards();
        const after = [];
        for (let i = 0; i < 3; i++) { await poll(H.bodies.frozen); after.push(cards()); }
        return { rendered: rendered, after: after };
        """,
            frozen=_frozen(),
        )
        for i, after in enumerate(r["after"], start=1):
            assert after == r["rendered"], f"poll {i} changed a card the server had rendered"
        for symphony_id in (TRIGGERED, ARMED):
            server = _server_card(FROZEN_WORLD, symphony_id)
            assert r["rendered"][symphony_id]["verdictText"] == server["verdict"]
            assert r["rendered"][symphony_id]["pill"] == server["pill"]

    def test_a_failed_frozen_poll_leaves_every_card_alone(self, monkeypatch):
        refusal = sup.as_route_spec(sup.error_response("500", monkeypatch))
        r = _run(
            r"""
        await H.boot({ extras: true });
        const before = cards(); const beforeBadges = badges();
        H.route('/api/state', H.bodies.refusal);
        await H.tick(); await H.completeAnimations();
        return { before: before, after: cards(), beforeBadges: beforeBadges, afterBadges: badges() };
        """,
            refusal=refusal,
        )
        assert r["after"] == r["before"]
        assert r["afterBadges"] == r["beforeBadges"]


# ---------------------------------------------------------------------------
# S-5.3, S-5.6: the badges equal the cards in their sections
# ---------------------------------------------------------------------------


class TestBadgesEqualTheCardsInTheirSections:
    def test_the_first_frozen_poll_leaves_each_badge_at_the_number_of_cards_in_its_section(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.frozen);
        return { badges: badges(), active: visibleIn('active'), standby: visibleIn('standby') };
        """,
            frozen=_frozen(),
        )
        assert r["badges"] == {"active": str(r["active"]), "standby": str(r["standby"])}

    def test_a_state_map_that_disagrees_with_the_card_list_does_not_move_the_badges_off_the_cards(
        self,
    ):
        """The badges count the cards on the page, which the card list places; the state map's own
        flags are not what a card's section follows."""
        disagreeing = sup.with_state_only(
            _frozen(), ARMED, armed=False, tp_armed=False, para_armed=False, triggered=False
        )
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.disagreeing);
        return { badges: badges(), active: visibleIn('active'), standby: visibleIn('standby'),
                 sections: order('active').concat(order('standby')) };
        """,
            disagreeing=disagreeing,
        )
        assert r["badges"] == {"active": str(r["active"]), "standby": str(r["standby"])}, (
            "a badge counted something other than the cards in its section"
        )
        assert sorted(r["sections"]) == sorted([TRIGGERED, ARMED])

    def test_a_frozen_body_with_no_card_list_leaves_the_badges_at_the_number_of_cards(self):
        """The closed branch once carried no card list, and the badges read 0 on every poll."""
        listless = copy.deepcopy(_frozen())
        del listless["symphonies"]
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.listless);
        return { badges: badges(), active: visibleIn('active'), standby: visibleIn('standby') };
        """,
            listless=listless,
        )
        assert r["badges"] == {"active": str(r["active"]), "standby": str(r["standby"])}
        assert r["active"] + r["standby"] == 2, "the render's cards must still be on the page"

    def test_a_scalar_or_a_nameless_entry_in_the_state_map_is_not_counted_as_a_card(self):
        padded = copy.deepcopy(_frozen())
        for key in ("bot_state", "state"):
            if isinstance(padded.get(key), dict):
                padded[key]["last_successful_cycle_at"] = "2026-10-21T16:00:41-04:00"
                padded[key]["post_mortem_run"] = {"ran": True}
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.padded);
        return { badges: badges(), active: visibleIn('active'), standby: visibleIn('standby') };
        """,
            padded=padded,
        )
        assert r["badges"] == {"active": str(r["active"]), "standby": str(r["standby"])}

    def test_after_picking_an_account_the_badges_count_only_the_cards_shown(self):
        other = "a6c0f6a2-1b7e-4a3d-9f58-3d2c7e5b9a10"  # a second account's id, not a real one
        two_accounts = sup.with_entry(_frozen(), ARMED, account=other)
        picked_body = sup.for_account(
            two_accounts, sup.card_entry(two_accounts, TRIGGERED)["account"]
        )
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.two);
        H.route(/^\/api\/state/, { body: H.bodies.picked });
        H.sandbox.window.switchAccount(H.bodies.account);
        await H.settle(); await H.completeAnimations();
        const picked = { cards: cards(), badges: badges() };
        await H.tick(); await H.completeAnimations(); await H.tick(); await H.completeAnimations();
        return { picked: picked, later: { cards: cards(), badges: badges() },
                 active: visibleIn('active'), standby: visibleIn('standby') };
        """,
            two=two_accounts,
            picked=picked_body,
            account=sup.card_entry(two_accounts, TRIGGERED)["account"],
        )
        for where in ("picked", "later"):
            shown = {i for i, c in r[where]["cards"].items() if not c["hidden"]}
            assert shown == {TRIGGERED}, f"{where}: other accounts' cards must stay hidden"
        assert r["later"]["badges"] == {"active": str(r["active"]), "standby": str(r["standby"])}


# ---------------------------------------------------------------------------
# S-5.4: before the open the card values are the poll's
# ---------------------------------------------------------------------------


class TestPreMarketCardsMatchThePoll:
    def test_each_cards_pill_and_values_equal_its_entry_formatted_as_the_client_formats_them(self):
        state = _world(PREMARKET_WORLD)["state"]
        assert state["market_state"] == "pre_market"
        r = _run(
            r"""
        await H.boot({ extras: true });
        await poll(H.bodies.premarket);
        return { cards: cards() };
        """,
            world=PREMARKET_WORLD,
            premarket=state,
        )
        legs = {
            "tcBot": "tc_bot",
            "tcHeld": "tc_held",
            "crBot": "cr_bot",
            "crHeld": "cr_held",
        }
        for entry in state["symphonies"]:
            card = r["cards"][entry["id"]]
            for field, key in legs.items():
                value = entry.get(key)
                if value is None:
                    assert card[field] == "--", (entry["id"], field)
                    continue
                shown = js_to_fixed([value], 1)[0]
                assert card[field] == ("+" if value >= 0 else "") + shown + "%", (
                    entry["id"],
                    field,
                )
            for field, key in (("mddBot", "mdd_bot"), ("mddHeld", "mdd_held")):
                value = entry.get(key)
                if value is None:
                    assert card[field] == "--", (entry["id"], field)
                else:
                    assert card[field] == js_to_fixed([abs(value)], 1)[0] + "%", (
                        entry["id"],
                        field,
                    )
            assert card["pill"] == _server_card(PREMARKET_WORLD, entry["id"])["pill"], (
                entry["id"],
                "the poll's status must be the status the server rendered",
            )


# ---------------------------------------------------------------------------
# S-5.1, S-5.5, S-5.9, S-5.10: the verdict rule
# ---------------------------------------------------------------------------


def _verdict_after_poll_with(guard_alpha) -> dict:
    """The triggered card's verdict after a frozen poll whose entry carries ``guard_alpha``."""
    body = sup.with_entry(_frozen(), TRIGGERED, guard_alpha=guard_alpha)
    return _run(
        r"""
    await H.boot({ extras: true });
    await poll(H.bodies.edited);
    return { card: cards()[__ID__] };
    """.replace("__ID__", repr(TRIGGERED)),
        edited=body,
    )["card"]


class TestTheVerdictRule:
    @pytest.mark.parametrize("guard_alpha", [1e21, -1e21, 1e30, -1e30], ids=str)
    def test_a_figure_the_server_render_cannot_print_reads_the_dash_with_its_label_hidden(
        self, guard_alpha
    ):
        assert _verdict_text(guard_alpha) == DASH, "the server render's own rule for this value"
        card = _verdict_after_poll_with(guard_alpha)
        assert card["verdictText"] == DASH
        assert card["basisHidden"] is True, "a label is shown beside no value"

    @pytest.mark.parametrize("guard_alpha", [9.99e20, -9.99e20, 0.25, -0.25, 0.04, 0.0], ids=str)
    def test_a_figure_below_the_bound_reads_the_text_the_server_render_gives(self, guard_alpha):
        card = _verdict_after_poll_with(guard_alpha)
        assert card["verdictText"] == _verdict_text(guard_alpha)
        assert card["basisHidden"] is False

    @pytest.mark.parametrize("kind", ["absent", "null"])
    def test_a_triggered_entry_with_no_guard_alpha_reads_the_dash_after_a_poll_that_showed_a_value(
        self, kind
    ):
        frozen = _frozen()
        edited = (
            sup.without_entry_key(frozen, TRIGGERED, "guard_alpha")
            if kind == "absent"
            else sup.with_entry(frozen, TRIGGERED, guard_alpha=None)
        )
        r = _run(
            r"""
        await H.boot({ extras: true });
        const shown = cards()[__ID__];
        await poll(H.bodies.edited);
        return { shown: shown, card: cards()[__ID__] };
        """.replace("__ID__", repr(TRIGGERED)),
            edited=edited,
        )
        assert r["shown"]["verdictText"] != DASH, "the render must have shown a figure to replace"
        assert r["card"]["verdictText"] == DASH, "the old figure stayed over a missing one"
        assert r["card"]["basisHidden"] is True

    def test_a_non_number_guard_alpha_reads_the_dash(self):
        card = _verdict_after_poll_with("n/a")
        assert card["verdictText"] == DASH
        assert card["basisHidden"] is True


# ---------------------------------------------------------------------------
# S-5.7: a card that changes status moves to its section's place in the card list's order
# ---------------------------------------------------------------------------


class TestACardChangingStatusTakesItsPlaceInTheListsOrder:
    def test_it_moves_to_the_place_the_card_list_gives_it_and_keeps_its_chart(self):
        base = _world(OPEN_WORLD)["state"]
        ids = [s["id"] for s in base["symphonies"]]
        assert len(ids) == 2
        to_standby = sup.with_entry(
            base, ARMED, armed=False, tp_armed=False, para_armed=False, status="standby"
        )
        back_to_active_first = sup.with_card_order(base, [ARMED, TRIGGERED])
        r = _run(
            r"""
        await H.boot({ extras: true });
        const key = 'spark:' + __ARMED__;
        const built = H.summary()[key];
        await poll(H.bodies.standby);
        const moved = { active: order('active'), standby: order('standby') };
        await poll(H.bodies.activeFirst);
        return { moved: moved, active: order('active'), standby: order('standby'),
                 built: built.constructs, now: H.summary()[key].constructs,
                 live: H.summary()[key].live };
        """.replace("__ARMED__", repr(ARMED)),
            world=OPEN_WORLD,
            standby=to_standby,
            activeFirst=back_to_active_first,
        )
        assert r["moved"] == {"active": [TRIGGERED], "standby": [ARMED]}
        assert r["active"] == [ARMED, TRIGGERED], "the card must sit where the list's order puts it"
        assert r["standby"] == []
        assert r["now"] == r["built"] and r["live"], "moving a card rebuilt or lost its chart"
