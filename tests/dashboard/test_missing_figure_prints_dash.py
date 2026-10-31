"""A figure that is unknown prints a dash, never ``+0.00%`` or ``0``.

Two surfaces print a stand-in for a missing figure:

* The hero's Today and Cumulative rows, on the first paint (``GET /``): the server render is checked
  with the figures the real ``_build_meta`` reads from the strip (``today_change`` /
  ``cumulative_return``), one side unknown at a time.
* The symphony detail panel's live return (``writeDetailFigures`` in ``static/index.js``): the whole of
  the script runs under node (tests/dashboard/_index_js_harness.py) over the page ``GET /`` renders;
  the panel is opened the way a user opens it, and the figure is read after a poll that carries the
  entry.

A figure that is genuinely zero still prints as a number: the dash is for "not known", and 0.0 is a
known value (the two are different cases here).

Derived bodies change one named field of a real body: tests/fixtures/refresh_timing/03-c6/PROVENANCE.md.
"""

from __future__ import annotations

import copy
import re
from html import unescape
from unittest.mock import patch

import pytest

import app as app_module
from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup
from tests.ui.test_comprehensive_audit import _api_state_stub, _patch_db

DIGIT = re.compile(r"\d")
DASHES = ("--", "—")


def _is_dash(text: str) -> bool:
    return not DIGIT.search(text) and any(d in text for d in DASHES)


# ---------------------------------------------------------------------------
# The hero rows on the first paint
# ---------------------------------------------------------------------------


def _first_paint(**strip_overrides) -> str:
    """``GET /`` with the portfolio strip the page is built from replaced by a captured one.

    The strip is the real ``/api/state`` strip of the default world (a captured producer body) with
    the named fields changed; ``_build_meta`` and the template are the real ones.
    """
    strip = copy.deepcopy(hx.get_world("default")["state"]["portfolio_strip"])
    strip.update(strip_overrides)
    db_mock = _patch_db()
    with (
        patch.object(
            app_module, "get_api_state_dict", return_value=_api_state_stub(portfolio_strip=strip)
        ),
        patch.object(app_module, "_compute_portfolio_strip", return_value=strip),
        patch.object(app_module, "database", db_mock),
    ):
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            response = client.get("/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _span(html: str, testid: str) -> str:
    match = re.search(rf'data-testid="{testid}"[^>]*>(.*?)</span>', html, flags=re.S)
    assert match, f"the render holds no {testid}"
    return unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()


ROW_CASES = [
    # (row, the strip key, which side is unknown)
    ("today", "today_change", "dry_run"),
    ("today", "today_change", "if_held"),
    ("cumulative", "cumulative_return", "dry_run"),
    ("cumulative", "cumulative_return", "if_held"),
]


class TestHeroRowsOnTheFirstPaint:
    @pytest.mark.parametrize(("row", "key", "side"), ROW_CASES)
    def test_an_unknown_side_prints_a_dash_and_the_known_side_keeps_its_number(
        self, row, key, side
    ):
        known = "if_held" if side == "dry_run" else "dry_run"
        figures = {"dry_run": 1.15, "if_held": 2.3}
        figures[side] = None
        html = _first_paint(**{key: figures})

        unknown_testid = f"comp-{row}-{'bot' if side == 'dry_run' else 'held'}-text"
        known_testid = f"comp-{row}-{'held' if side == 'dry_run' else 'bot'}-text"
        assert _is_dash(_span(html, unknown_testid)), (
            f"{unknown_testid} prints {_span(html, unknown_testid)!r} for an unknown figure"
        )
        assert f"{figures[known]:.2f}" in _span(html, known_testid), (
            f"{known_testid} lost its known figure"
        )

    @pytest.mark.parametrize(("row", "key", "side"), ROW_CASES)
    def test_a_delta_that_needs_the_unknown_side_prints_a_dash(self, row, key, side):
        figures = {"dry_run": 1.15, "if_held": 2.3}
        figures[side] = None
        html = _first_paint(**{key: figures})
        delta = _span(html, f"comp-{row}-delta")
        assert _is_dash(delta), f"the {row} delta prints {delta!r} though one side is unknown"

    @pytest.mark.parametrize("row_key", ["today_change", "cumulative_return"])
    def test_a_genuine_zero_is_still_a_number(self, row_key):
        html = _first_paint(**{row_key: {"dry_run": 0.0, "if_held": 1.2}})
        row = "today" if row_key == "today_change" else "cumulative"
        bot = _span(html, f"comp-{row}-bot-text")
        assert "0.00" in bot, f"a known 0.0 printed {bot!r}"

    def test_both_sides_of_both_rows_unknown_prints_dashes_everywhere(self):
        html = _first_paint(
            today_change={"dry_run": None, "if_held": None},
            cumulative_return={"dry_run": None, "if_held": None},
        )
        for row in ("today", "cumulative"):
            for part in ("bot-text", "held-text", "delta"):
                text = _span(html, f"comp-{row}-{part}")
                assert _is_dash(text), f"comp-{row}-{part} prints {text!r}"


# ---------------------------------------------------------------------------
# The detail panel's live return
# ---------------------------------------------------------------------------

WORLD = "default"
UNTRIGGERED = hx.UNTRIGGERED_ID
TRIGGERED = hx.TRIGGERED_TP_ID

PANEL = r"""
const D = H.sandbox.document;
const T = function (id) { const e = D.getElementById(id); return e ? e.textContent.trim() : null; };
const open = async function (id) { return H.press('.sym-card[data-sym-id="' + id + '"]'); };
const poll = async function (body) {
  H.route('/api/state', { body: body });
  await H.tick(); await H.completeAnimations();
};
"""


def _world() -> dict:
    return hx.get_world(WORLD)


def _same() -> dict:
    world = _world()
    return sup.with_stamp(world["state"], sup.render_stamp(world))


def _live_returns(bodies: list[dict], symphony_id: str) -> list[str]:
    """The panel's live return after the panel was opened and after each poll body in turn."""
    scenario = (
        PANEL
        + r"""
    await H.boot({ extras: true });
    await open(__ID__);
    const seen = [];
    for (const key of Object.keys(H.bodies.sequence).sort()) {
      await poll(H.bodies.sequence[key]);
      seen.push(T('dp-live-ret'));
    }
    return seen;
    """.replace("__ID__", repr(symphony_id))
    )
    sequence = {f"{index:02d}": body for index, body in enumerate(bodies)}
    return hx.run_scenario(
        _world(),
        scenario,
        scripts=sup.CHROME_AND_INDEX,
        extra_bodies={"sequence": sequence},
        now_iso=hx.seeded_now_iso(_world()),
    )["result"]


KNOWN = 1.23
KNOWN_TEXT = "+1.23%"


class TestDetailPanelLiveReturn:
    @pytest.mark.parametrize(
        "unknown",
        [None, 999.0, -999.0],
        ids=["null", "the engine's upper not-set sentinel", "the engine's lower not-set sentinel"],
    )
    def test_a_poll_that_makes_the_live_return_unknown_replaces_the_figure_with_a_dash(
        self, unknown
    ):
        known = sup.with_entry(_same(), UNTRIGGERED, current_return=KNOWN)
        gone = sup.with_entry(_same(), UNTRIGGERED, current_return=unknown)
        seen = _live_returns([known, gone], UNTRIGGERED)
        assert seen[0] == KNOWN_TEXT, f"a known live return reads {seen[0]!r}"
        assert _is_dash(seen[1]), f"an unknown live return reads {seen[1]!r}"

    def test_a_live_return_the_entry_stops_carrying_is_a_dash(self):
        known = sup.with_entry(_same(), UNTRIGGERED, current_return=KNOWN)
        gone = sup.without_entry_key(_same(), UNTRIGGERED, "current_return")
        seen = _live_returns([known, gone], UNTRIGGERED)
        assert seen[0] == KNOWN_TEXT
        assert _is_dash(seen[1]), f"the live return reads {seen[1]!r}"

    def test_a_triggered_entry_without_its_exit_return_is_a_dash_not_the_live_return(self):
        # A triggered symphony's figure is its frozen exit return; the live return is a different
        # quantity and must not stand in for it.
        exit_known = sup.with_entry(
            _same(), TRIGGERED, triggered_at_return=-3.1, current_return=2.5
        )
        exit_gone = sup.with_entry(_same(), TRIGGERED, triggered_at_return=None, current_return=2.5)
        seen = _live_returns([exit_known, exit_gone], TRIGGERED)
        assert seen[0] == "-3.10%", f"a known exit return reads {seen[0]!r}"
        assert _is_dash(seen[1]), f"the exit return reads {seen[1]!r}"

    def test_a_genuine_zero_live_return_is_a_number(self):
        known = sup.with_entry(_same(), UNTRIGGERED, current_return=KNOWN)
        zero = sup.with_entry(_same(), UNTRIGGERED, current_return=0.0)
        seen = _live_returns([known, zero], UNTRIGGERED)
        assert seen == [KNOWN_TEXT, "+0.00%"], seen
