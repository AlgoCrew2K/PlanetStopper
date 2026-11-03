"""A gap between two returns is shown in %, never in pp, on the Dashboard.

Contract pinned (the operator reads "pp" as jargon; a gap between two returns is a % on a dashboard):
* The guard headline and the Today, Cumulative, Max DD and Ann. Vol deltas beside it print their
  figure with a ``%`` unit and never ``pp``, on the server render (first paint) and on the text the
  client writes after a poll or a window pick.
* A value that rounds to zero at the shown decimals prints plain ``0.00%`` (``0.0%`` for the Ann. Vol
  delta): no minus sign and no plus sign, whichever side of zero the value is on.
* The first paint and the poll print the same text for the same figure.

Real call paths: ``GET /`` through the Flask test client over a stubbed meta (the server render), and
the whole of ``static/index.js`` under node (tests/dashboard/_index_js_harness.py) over the page that
render returns (the poll and the pick). Expected texts come from the rule above, written out here as
``expected_gap``, not from the producer.
"""

from __future__ import annotations

import copy
import html as html_lib
import re
from unittest.mock import patch

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

WORLD = "default"

# (value, decimals) -> the text the rule above prints.
GAP_CASES = [
    pytest.param(-0.73, "-0.73%", id="negative"),
    pytest.param(0.5, "+0.50%", id="positive"),
    pytest.param(-0.004, "0.00%", id="negative-rounds-to-zero"),
    pytest.param(0.004, "0.00%", id="positive-rounds-to-zero"),
    pytest.param(0.0, "0.00%", id="exactly-zero"),
]
VOL_CASES = [
    pytest.param(1.6, "+1.6%", id="calmer"),
    pytest.param(-2.4, "-2.4%", id="choppier"),
    pytest.param(-0.004, "0.0%", id="negative-rounds-to-zero"),
    pytest.param(0.004, "0.0%", id="positive-rounds-to-zero"),
]

TEXT_NODE_WITH_PP = re.compile(r">[^<>]*\d\s?pp\b")


def expected_gap(value: float, decimals: int = 2) -> str:
    """The unit rule: signed, fixed decimals, then ``%``; a value that prints as zero has no sign."""
    shown = f"{value:.{decimals}f}"
    if float(shown) == 0:
        return f"{abs(float(shown)):.{decimals}f}%"
    return ("+" if value > 0 else "") + shown + "%"


# ---------------------------------------------------------------------------
# The server render (first paint)
# ---------------------------------------------------------------------------


def _meta(*, guard_alpha, tc, tc_held, cr, cr_held, mdd, mdd_held, vol_bot, vol_held) -> dict:
    return {
        "mode": "DRY RUN",
        "system_online": True,
        "tracked": 0,
        "armed": 0,
        "triggered": 0,
        "next_run": "00:00",
        "account": {"label": "Test Account", "uuid_short": "ab12cd34"},
        "market_state": "closed",
        "market_state_label": "Market closed",
        "clock_et": "09:30 ET",
        "portfolio": {
            "guard_alpha": guard_alpha,
            "tc": tc,
            "tc_if_held": tc_held,
            "cr": cr,
            "cr_if_held": cr_held,
            "mdd": mdd,
            "mdd_if_held": mdd_held,
            "vol_bot": vol_bot,
            "vol_held": vol_held,
            "hist_dates": ["2026-01-01"] * 35,
            "hist_bot": [0.0] * 35,
            "hist_held": [0.0] * 35,
            "hist_source": "shadow_history",
            "data_as_of": "09:30 ET",
            "account_value": 100000.0,
            "insufficient_history": False,
            "history_days": 35,
        },
    }


def _render(meta: dict) -> str:
    import app as app_module

    app_module.app.config["TESTING"] = True
    api_state = {
        "bot_state": {},
        "is_locked": False,
        "port_state": {},
        "exit_authority": {},
        "daemon_started_at": None,
    }
    with app_module.app.test_client() as client:
        with patch.object(app_module, "get_api_state_dict", return_value=api_state):
            with patch.object(app_module, "_build_meta", return_value=meta):
                response = client.get("/")
    assert response.status_code == 200
    return response.data.decode("utf-8")


def _element_text(html: str, testid: str) -> str:
    match = re.search(rf'data-testid="{testid}"[^>]*>(.*?)</', html, flags=re.S)
    assert match, f"the render holds no {testid}"
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", "", match.group(1))).split())


class TestServerRenderPrintsPercent:
    @pytest.mark.parametrize("gap, want", GAP_CASES)
    def test_the_headline_is_the_gap_in_percent(self, gap, want):
        html = _render(
            _meta(
                guard_alpha=gap,
                tc=0.3,
                tc_held=0.25,
                cr=8.0,
                cr_held=7.0,
                mdd=2.0,
                mdd_held=2.8,
                vol_bot=0.031,
                vol_held=0.047,
            )
        )
        assert _element_text(html, "guard-alpha-headline") == want

    @pytest.mark.parametrize("gap, want", GAP_CASES)
    def test_the_today_cumulative_and_max_dd_deltas_are_gaps_in_percent(self, gap, want):
        base = 10.0
        html = _render(
            _meta(
                guard_alpha=0.1,
                tc=base + gap,
                tc_held=base,
                cr=base + gap,
                cr_held=base,
                mdd=-(5.0 + gap),
                mdd_held=-5.0,
                vol_bot=0.031,
                vol_held=0.047,
            )
        )
        assert _element_text(html, "comp-today-delta") == f"α {want}"
        assert _element_text(html, "comp-cumulative-delta") == f"α {want}"
        # The Max DD delta is the held drawdown's magnitude minus the bot's: the bot drawing down
        # `gap` deeper than held reads as the gap's negative.
        mdd_want = expected_gap(-gap)
        assert _element_text(html, "comp-mdd-delta") == f"α {mdd_want}"

    @pytest.mark.parametrize("gap, want", VOL_CASES)
    def test_the_ann_vol_delta_is_a_gap_in_percent(self, gap, want):
        vol_bot = 0.15
        html = _render(
            _meta(
                guard_alpha=0.1,
                tc=0.3,
                tc_held=0.25,
                cr=8.0,
                cr_held=7.0,
                mdd=2.0,
                mdd_held=2.8,
                vol_bot=vol_bot,
                vol_held=vol_bot + gap / 100,
            )
        )
        assert _element_text(html, "comp-vol-delta") == f"α {want}"

    def test_no_text_of_the_page_states_a_figure_in_pp(self):
        html = _render(
            _meta(
                guard_alpha=-0.73,
                tc=0.8,
                tc_held=0.3,
                cr=8.5,
                cr_held=8.0,
                mdd=-2.0,
                mdd_held=-2.5,
                vol_bot=0.031,
                vol_held=0.047,
            )
        )
        stripped = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S | re.I)
        stripped = re.sub(r"<!--.*?-->", "", stripped, flags=re.S)
        found = TEXT_NODE_WITH_PP.findall(stripped)
        assert not found, f"a figure of the rendered page still ends in pp: {found}"
        titles = re.findall(r'title="([^"]*)"', stripped)
        assert not [t for t in titles if re.search(r"\dpp\b|\d pp\b", t)], titles


# ---------------------------------------------------------------------------
# The text the client writes
# ---------------------------------------------------------------------------

SNAPSHOT = r"""
const D = H.sandbox.document;
const text = function (testid) {
  const el = D.querySelector('[data-testid="' + testid + '"]');
  return el ? el.textContent.trim() : null;
};
const snap = function () {
  return {
    headline: (D.getElementById('guard-alpha-headline') || { textContent: null }).textContent.trim(),
    today: text('comp-today-delta'),
    cumulative: text('comp-cumulative-delta'),
    mdd: text('comp-mdd-delta'),
    vol: text('comp-vol-delta'),
  };
};
"""


def _run(scenario: str, *, extra: dict):
    world = hx.get_world(WORLD)
    stamp = sup.render_stamp(world)
    bodies = {"pollNew": sup.with_stamp(world["next"]["state"], sup.later_stamp(stamp))}
    bodies.update(extra)
    return sup.run(SNAPSHOT + scenario, world=WORLD, scripts=sup.INDEX_ONLY, extra_bodies=bodies)


def _picked_strip(*, guard_alpha, mdd_gap, vol_gap) -> dict:
    """The 1y strip with a known guard effect, a Max DD pair ``mdd_gap`` apart (the bot's drawdown
    ``mdd_gap`` deeper than held's) and an Ann. Vol pair ``vol_gap`` apart (held minus bot)."""
    strip = copy.deepcopy(hx.get_world(WORLD)["strips"]["1y"])
    strip["guard_alpha"] = guard_alpha
    strip["max_drawdown"] = {**strip["max_drawdown"], "dry_run": -(5.0 + mdd_gap), "if_held": -5.0}
    strip["vol_bot"] = 0.15
    strip["vol_held"] = 0.15 + vol_gap / 100
    strip.pop("insufficient_history", None)
    return strip


class TestPollAndPickPrintPercent:
    @pytest.mark.parametrize("gap, want", GAP_CASES)
    def test_a_pick_writes_the_headline_and_the_max_dd_delta_in_percent(self, gap, want):
        strip = _picked_strip(guard_alpha=gap, mdd_gap=gap, vol_gap=1.6)
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route(/^\/api\/strip\/1y$/, { body: H.bodies.picked });
        await H.click('[data-testid="window-1y"]');
        return { shown: snap() };
        """,
            extra={"picked": strip},
        )
        assert r["shown"]["headline"] == want
        assert r["shown"]["mdd"] == f"α {expected_gap(-gap)}"

    @pytest.mark.parametrize("gap, want", VOL_CASES)
    def test_a_pick_writes_the_ann_vol_delta_in_percent(self, gap, want):
        strip = _picked_strip(guard_alpha=0.5, mdd_gap=0.5, vol_gap=gap)
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route(/^\/api\/strip\/1y$/, { body: H.bodies.picked });
        await H.click('[data-testid="window-1y"]');
        return { shown: snap() };
        """,
            extra={"picked": strip},
        )
        assert r["shown"]["vol"] == f"α {want}"

    @pytest.mark.parametrize("gap, want", GAP_CASES)
    def test_a_poll_writes_the_today_and_cumulative_deltas_in_percent(self, gap, want):
        world = hx.get_world(WORLD)
        stamp = sup.render_stamp(world)
        state = sup.with_stamp(world["next"]["state"], sup.later_stamp(stamp))
        state = copy.deepcopy(state)
        strip = state["portfolio_strip"]
        strip["today_change"] = {
            **strip.get("today_change", {}),
            "dry_run": 10.0 + gap,
            "if_held": 10.0,
        }
        strip["cumulative_return"] = {
            **strip.get("cumulative_return", {}),
            "dry_run": 10.0 + gap,
            "if_held": 10.0,
        }
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route('/api/state', { body: H.bodies.gapState });
        await H.runTimers(30000);
        return { shown: snap() };
        """,
            extra={"gapState": state},
        )
        assert r["shown"]["today"] == f"α {want}"
        assert r["shown"]["cumulative"] == f"α {want}"

    @pytest.mark.parametrize("gap, want", GAP_CASES)
    def test_the_first_paint_and_the_poll_print_the_same_text(self, gap, want):
        html = _render(
            _meta(
                guard_alpha=gap,
                tc=0.3,
                tc_held=0.25,
                cr=8.0,
                cr_held=7.0,
                mdd=2.0,
                mdd_held=2.8,
                vol_bot=0.031,
                vol_held=0.047,
            )
        )
        strip = _picked_strip(guard_alpha=gap, mdd_gap=0.5, vol_gap=1.6)
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route(/^\/api\/strip\/1y$/, { body: H.bodies.picked });
        await H.click('[data-testid="window-1y"]');
        return { shown: snap() };
        """,
            extra={"picked": strip},
        )
        assert _element_text(html, "guard-alpha-headline") == r["shown"]["headline"] == want


def test_the_compact_number_format_keeps_the_percent_unit():
    world = hx.get_world(WORLD)
    stamp = sup.render_stamp(world)
    strip = sup.strip_with(world["strips"]["30d"], guard_alpha=1234.5)
    r = hx.run_scenario(
        world,
        r"""
        await H.boot({ extras: true });
        H.sandbox.document.documentElement.dataset.numFormat = 'compact';
        H.route('/api/state', { body: H.bodies.later });
        await H.tick(); await H.completeAnimations();
        return { headline: H.text('#guard-alpha-headline') };
        """,
        scripts=sup.CHROME_AND_INDEX,
        extra_bodies={
            "strips": {**world["strips"], "30d": strip},
            "later": sup.with_stamp(world["next"]["state"], sup.later_stamp(stamp)),
        },
        now_iso=hx.seeded_now_iso(world),
    )["result"]
    assert r["headline"] == "1.2k%"
