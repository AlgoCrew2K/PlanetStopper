"""The open detail panel's Risk Math rows tell one story, whichever path writes them.

Real call path: ``static/chrome.js`` and ``static/index.js`` run whole under node
(tests/dashboard/_index_js_harness.py) over the page ``GET /`` renders. The panel is opened by a press
on the card, the chart answer is the ``/api/chart/<id>`` response, and a poll is the page's own
``/api/state`` fetch, so both writers of the rows (the chart answer and the state poll) run for real.

Contract pinned here, with the market closed (no number may move on a poll that carries the same entry):

* every row, its hint and the MC bar read the same after the chart answer as after any number of
  following polls with the same entry;
* Vol is the state value when it is a usable number, else the chart's last row; MC is no reading for a
  triggered symphony on every path, else the state value when usable, else the chart's backward scan;
* no reading is one dash on every path, and a locked breakeven is one text on every path;
* the VWAP row stays the chart's, and an open panel still follows a poll whose entry truly changed;
* a panel opened on another symphony shows nothing of the previous symphony's chart reading.

Derived bodies change named fields of the real captured ``/api/state`` and ``/api/chart/<id>`` bodies
(tests/dashboard/_refresh_timing_support.py ``with_entry``, ``without_entry_key``); the chart builder
below sets only ``vol``, ``mc_prob``, ``vwap_diff`` and ``stop`` on rows of the captured tape. Expected
texts are recomputed from the fixture values with the client's ``toFixed``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from functools import lru_cache

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup
from tests.dashboard._first_paint_dom import js_to_fixed

UNTRIGGERED = hx.UNTRIGGERED_ID
TRIGGERED = hx.TRIGGERED_TP_ID
OTHER = hx.TRIGGERED_VWAP_ID
DASHES = {"—", "--"}

STATE_VOL = 1.593
CHART_VOL = 4.010
STATE_MC = 33.3
CHART_MC = 61.1
NEW_STATE_VOL = 2.489
NEW_STATE_MC = 72.4

ROW_IDS = (
    "dp-rm-mc",
    "dp-rm-stop",
    "dp-rm-vol",
    "dp-rm-para",
    "dp-rm-be",
    "dp-rm-vwap",
    "dp-rm-para-hint",
    "dp-rm-be-hint",
    "dp-rm-vwap-hint",
)

PRELUDE = r"""
const D = H.sandbox.document;
const T = function (id) { const e = D.getElementById(id); return e ? e.textContent.trim() : null; };
const ROW_IDS = __ROW_IDS__;
const rows = function () {
  const out = {};
  ROW_IDS.forEach(function (id) { out[id] = T(id); });
  const bar = D.getElementById('dp-rm-mc-bar');
  out.bar = bar ? bar.style.width : null;
  return out;
};
const open = async function (id) { return H.press('.sym-card[data-sym-id="' + id + '"]'); };
const poll = async function (body) {
  H.route('/api/state', { body: body });
  await H.tick(); await H.completeAnimations();
};
const closePanel = function () {
  D.getElementById('detail-panel').classList.remove('open');
  D.getElementById('detail-scrim').classList.remove('open');
};
""".replace("__ROW_IDS__", repr(list(ROW_IDS)))


def _world() -> dict:
    return hx.get_world("default")


def _same() -> dict:
    built = _world()
    return sup.with_stamp(built["state"], sup.render_stamp(built))


def _state(symphony_id: str, fields: dict, drop: tuple[str, ...] = ()) -> dict:
    body = sup.with_entry(_same(), symphony_id, **fields)
    for key in drop:
        body = sup.without_entry_key(body, symphony_id, key)
    return body


def _chart(symphony_id: str, *, vol=None, mc=None, mc_gap: int = 0, vwap_diff=0.37, stop=-1.5):
    """The captured tape of a symphony with the last row's readings set.

    ``mc`` sits ``mc_gap`` rows before the last row and every other row has no MC reading, so the
    backward scan is the only way to find it. ``vwap_diff`` is non-zero so a chart answer is visible
    on the VWAP row.
    """
    chart = copy.deepcopy(_world()["charts"][symphony_id])
    tape = chart["data"]
    for row in tape:
        row["mc_prob"] = None
    if mc is not None:
        tape[len(tape) - 1 - mc_gap]["mc_prob"] = mc
    tape[-1]["vol"] = vol
    tape[-1]["vwap_diff"] = vwap_diff
    tape[-1]["stop"] = stop
    return chart


def _usable(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) < 900


@dataclass(frozen=True)
class Case:
    name: str
    symphony: str = UNTRIGGERED
    state: dict = field(default_factory=dict)
    drop: tuple[str, ...] = ()
    chart: dict = field(default_factory=dict)

    @property
    def triggered(self) -> bool:
        return bool(self.state.get("triggered", self.symphony != UNTRIGGERED))

    def state_body(self) -> dict:
        return _state(self.symphony, self.state, self.drop)

    def chart_body(self) -> dict:
        return _chart(self.symphony, **self.chart)

    def expected(self) -> dict:
        """Vol text, MC text and bar width by the contract's rule; ``None`` means no reading."""
        sv = self.state.get("symphony_vol")
        cv = self.chart.get("vol")
        vol = sv if _usable(sv) else (cv if _usable(cv) else None)
        sm = self.state.get("mc_prob")
        cm = self.chart.get("mc")
        if self.triggered:
            mc = None
        else:
            mc = sm if _usable(sm) else (cm if _usable(cm) else None)
        return {
            "vol": None if vol is None else js_to_fixed([vol], 3)[0],
            "mc": None if mc is None else js_to_fixed([mc], 1)[0] + "%",
            "bar": "0%" if mc is None else f"{max(0, min(100, mc))}%",
        }


CASES = [
    Case(
        "state_and_chart_disagree",
        state={"symphony_vol": STATE_VOL, "mc_prob": STATE_MC},
        chart={"vol": CHART_VOL, "mc": CHART_MC},
    ),
    Case(
        "state_and_chart_agree",
        state={"symphony_vol": STATE_VOL, "mc_prob": STATE_MC},
        chart={"vol": STATE_VOL, "mc": STATE_MC},
    ),
    Case(
        "state_keys_absent",
        drop=("symphony_vol", "mc_prob"),
        chart={"vol": CHART_VOL, "mc": CHART_MC},
    ),
    Case(
        "state_values_null",
        state={"symphony_vol": None, "mc_prob": None},
        chart={"vol": CHART_VOL, "mc": CHART_MC},
    ),
    Case(
        "state_values_are_the_engine_sentinel",
        state={"symphony_vol": 999.0, "mc_prob": -999.0},
        chart={"vol": CHART_VOL, "mc": CHART_MC},
    ),
    Case(
        "state_values_are_text",
        state={"symphony_vol": "1.593", "mc_prob": "33.3"},
        chart={"vol": CHART_VOL, "mc": CHART_MC},
    ),
    Case(
        "chart_has_no_reading",
        state={"symphony_vol": STATE_VOL, "mc_prob": STATE_MC},
        chart={"vol": None, "mc": None},
    ),
    Case(
        "neither_has_a_reading",
        state={"symphony_vol": None, "mc_prob": None},
        chart={"vol": None, "mc": None},
    ),
    Case(
        "chart_mc_found_by_backward_scan",
        drop=("symphony_vol", "mc_prob"),
        chart={"vol": CHART_VOL, "mc": CHART_MC, "mc_gap": 6},
    ),
    Case(
        "triggered_with_a_stale_state_mc",
        symphony=TRIGGERED,
        state={"triggered": True, "symphony_vol": STATE_VOL, "mc_prob": STATE_MC},
        chart={"vol": CHART_VOL, "mc": CHART_MC, "mc_gap": 6},
    ),
    Case(
        "triggered_with_a_null_state_mc",
        symphony=TRIGGERED,
        state={"triggered": True, "symphony_vol": None, "mc_prob": None},
        chart={"vol": CHART_VOL, "mc": CHART_MC, "mc_gap": 6},
    ),
    Case(
        "breakeven_locked_with_ratchet",
        state={
            "breakeven_locked": True,
            "para_velocity": 0.75,
            "symphony_vol": STATE_VOL,
            "mc_prob": STATE_MC,
        },
        chart={"vol": CHART_VOL, "mc": CHART_MC},
    ),
    Case(
        "breakeven_off_without_ratchet",
        state={"breakeven_locked": False, "para_velocity": None},
        drop=("symphony_vol", "mc_prob"),
        chart={"vol": CHART_VOL, "mc": CHART_MC},
    ),
    Case(
        "stop_distance_from_state_peak_and_chart_stop",
        state={"shadow_hwm": 3.5},
        drop=("stop_trigger", "symphony_vol", "mc_prob"),
        chart={"vol": CHART_VOL, "mc": CHART_MC, "stop": -1.25},
    ),
    Case(
        "stop_distance_from_state_alone",
        state={"shadow_hwm": 3.5, "stop_trigger": -1.25},
        drop=("symphony_vol", "mc_prob"),
        chart={"vol": CHART_VOL, "mc": CHART_MC, "stop": -2.0},
    ),
]
BY_NAME = {case.name: case for case in CASES}


def _run(scenario: str, **bodies) -> dict:
    built = _world()
    return hx.run_scenario(
        built,
        PRELUDE + scenario,
        scripts=sup.CHROME_AND_INDEX,
        extra_bodies=bodies,
        now_iso=hx.seeded_now_iso(built),
    )["result"]


FLOW = r"""
await H.boot({ extras: true, state: H.bodies.S,
               overrides: [['/api/chart/' + __ID__, __CHART__]] });
await open(__ID__);
await H.completeAnimations();
const opened = rows();
const polls = [];
for (let i = 0; i < 3; i++) { await poll(H.bodies.S); polls.push(rows()); }
return { opened: opened, polls: polls };
"""


@lru_cache(maxsize=None)
def _answered(name: str) -> dict:
    """Open on a symphony, let the chart answer, then poll three times with the same entry."""
    case = BY_NAME[name]
    return _run(
        FLOW.replace("__ID__", repr(case.symphony)).replace("__CHART__", "{ body: H.bodies.C }"),
        S=case.state_body(),
        C=case.chart_body(),
    )


@lru_cache(maxsize=None)
def _poll_only(name: str) -> dict:
    """The same open and polls with the chart request never answered: the state path alone."""
    case = BY_NAME[name]
    return _run(
        FLOW.replace("__ID__", repr(case.symphony)).replace("__CHART__", "{ pending: true }"),
        S=case.state_body(),
    )


def _text(rows: dict, key: str) -> str:
    value = rows[key]
    assert value is not None, f"the page has no element for {key}"
    return value


def _assert_reading(rows: dict, expected: dict, where: str) -> None:
    for key, row_id in (("vol", "dp-rm-vol"), ("mc", "dp-rm-mc")):
        if expected[key] is None:
            assert _text(rows, row_id) in DASHES, (
                f"{where}: {row_id} must be a dash when there is no reading, got {rows[row_id]!r}"
            )
        else:
            assert rows[row_id] == expected[key], (
                f"{where}: {row_id} is {rows[row_id]!r}, the contract's source rule gives {expected[key]!r}"
            )
    assert rows["bar"] == expected["bar"], f"{where}: the MC bar does not follow the MC row"


# ---------------------------------------------------------------------------
# Contract 1: the chart answer and any following poll tell one story
# ---------------------------------------------------------------------------


class TestNoRowMovesOnAPollThatCarriesTheSameEntry:
    @pytest.mark.parametrize("name", [case.name for case in CASES])
    def test_every_row_hint_and_bar_reads_the_same_after_each_poll(self, name):
        r = _answered(name)
        assert _text(r["opened"], "dp-rm-vwap") != "—" and "Above" in r["opened"]["dp-rm-vwap"], (
            "the chart answer did not reach the panel; the scenario proves nothing"
        )
        for index, after in enumerate(r["polls"], start=1):
            moved = {k: (r["opened"][k], after[k]) for k in r["opened"] if r["opened"][k] != after[k]}
            assert not moved, f"poll {index} moved rows the market being closed gave no reason to move: {moved}"

    @pytest.mark.parametrize("name", [case.name for case in CASES])
    def test_the_rows_follow_the_contracts_source_rule_after_the_chart_and_after_polls(self, name):
        case = BY_NAME[name]
        r = _answered(name)
        expected = case.expected()
        _assert_reading(r["opened"], expected, "after the chart answer")
        for index, after in enumerate(r["polls"], start=1):
            _assert_reading(after, expected, f"after poll {index}")

    def test_the_vwap_row_and_its_hint_stay_the_charts_through_polls(self):
        r = _answered("state_and_chart_disagree")
        for after in r["polls"]:
            assert after["dp-rm-vwap"] == r["opened"]["dp-rm-vwap"] != "--"
            assert after["dp-rm-vwap-hint"] == r["opened"]["dp-rm-vwap-hint"]


# ---------------------------------------------------------------------------
# Contract 2 and 3: one rule and one text, whichever path writes the row
# ---------------------------------------------------------------------------


class TestTheStatePathAndTheChartPathWriteTheSameText:
    STATE_ONLY_ROWS = ("dp-rm-mc", "dp-rm-stop", "dp-rm-vol", "dp-rm-para", "dp-rm-be", "bar")

    def test_a_symphony_whose_state_has_every_value_reads_the_same_with_or_without_the_chart(self):
        name = "breakeven_locked_with_ratchet"
        with_chart = _answered(name)["polls"][0]
        state_only = _poll_only(name)["polls"][0]
        differing = {
            k: (with_chart[k], state_only[k])
            for k in self.STATE_ONLY_ROWS
            if with_chart[k] != state_only[k]
        }
        assert not differing, f"chart path vs state path wrote different text: {differing}"

    def test_a_locked_breakeven_is_one_text_on_every_path(self):
        name = "breakeven_locked_with_ratchet"
        texts = {
            _answered(name)["opened"]["dp-rm-be"],
            _answered(name)["polls"][0]["dp-rm-be"],
            _poll_only(name)["opened"]["dp-rm-be"],
            _poll_only(name)["polls"][0]["dp-rm-be"],
        }
        assert len(texts) == 1, f"a locked breakeven reads {sorted(texts)} depending on the path"
        assert next(iter(texts)) not in DASHES | {"Off"}

    def test_an_unlocked_breakeven_is_one_text_on_every_path(self):
        name = "breakeven_off_without_ratchet"
        texts = {
            _answered(name)["opened"]["dp-rm-be"],
            _answered(name)["polls"][0]["dp-rm-be"],
            _poll_only(name)["opened"]["dp-rm-be"],
            _poll_only(name)["polls"][0]["dp-rm-be"],
        }
        assert len(texts) == 1, f"an unlocked breakeven reads {sorted(texts)} depending on the path"

    def test_the_ratchet_row_is_one_text_on_every_path(self):
        for name in ("breakeven_locked_with_ratchet", "breakeven_off_without_ratchet"):
            texts = {
                _answered(name)["opened"]["dp-rm-para"],
                _answered(name)["polls"][0]["dp-rm-para"],
                _poll_only(name)["opened"]["dp-rm-para"],
                _poll_only(name)["polls"][0]["dp-rm-para"],
            }
            assert len(texts) == 1, f"{name}: the ratchet row reads {sorted(texts)} by path"

    @pytest.mark.parametrize("name", ["neither_has_a_reading", "triggered_with_a_null_state_mc"])
    def test_no_reading_is_the_same_dash_on_the_chart_path_and_the_state_path(self, name):
        answered = _answered(name)
        state_only = _poll_only(name)
        seen = {
            where: _text(rows, "dp-rm-mc")
            for where, rows in {
                "chart answer": answered["opened"],
                "poll after chart": answered["polls"][0],
                "state path at open": state_only["opened"],
                "state path poll": state_only["polls"][0],
            }.items()
        }
        assert set(seen.values()) <= DASHES, f"a no-reading MC row must be a dash: {seen}"
        assert len(set(seen.values())) == 1, f"no reading is written more than one way: {seen}"

    def test_the_value_rows_with_no_reading_share_one_dash(self):
        name = "neither_has_a_reading"
        panel = _answered(name)["polls"][0]
        dashes = {panel[k] for k in ("dp-rm-mc", "dp-rm-vol", "dp-rm-stop", "dp-rm-para")}
        assert dashes <= DASHES and len(dashes) == 1, f"value rows show different dashes: {panel}"


# ---------------------------------------------------------------------------
# Contract 4: the open panel still follows a poll whose entry truly changed
# ---------------------------------------------------------------------------


class TestAPollThatChangesTheEntryStillMovesTheRows:
    def test_new_state_values_replace_the_rows_and_a_state_without_values_falls_back_to_the_chart(self):
        base = BY_NAME["state_and_chart_disagree"]
        changed = _state(
            base.symphony,
            {"symphony_vol": NEW_STATE_VOL, "mc_prob": NEW_STATE_MC, "breakeven_locked": True},
        )
        valueless = _state(
            base.symphony,
            {"breakeven_locked": True},
            drop=("symphony_vol", "mc_prob"),
        )
        r = _run(
            FLOW.split("const opened")[0].replace("__ID__", repr(base.symphony)).replace(
                "__CHART__", "{ body: H.bodies.C }"
            )
            + r"""
        const opened = rows();
        await poll(H.bodies.changed);
        const changed = rows();
        await poll(H.bodies.changed);
        const changedAgain = rows();
        await poll(H.bodies.valueless);
        const valueless = rows();
        await poll(H.bodies.S);
        const back = rows();
        return { opened: opened, changed: changed, changedAgain: changedAgain,
                 valueless: valueless, back: back };
        """,
            S=base.state_body(),
            C=base.chart_body(),
            changed=changed,
            valueless=valueless,
        )
        fmt = js_to_fixed
        assert r["changed"]["dp-rm-vol"] == fmt([NEW_STATE_VOL], 3)[0], "an open-market vol change froze"
        assert r["changed"]["dp-rm-mc"] == fmt([NEW_STATE_MC], 1)[0] + "%", "an open-market MC change froze"
        assert r["changed"]["bar"] == f"{NEW_STATE_MC}%"
        assert r["changed"]["dp-rm-be"] != r["opened"]["dp-rm-be"], "a breakeven that locked did not show"
        assert r["changedAgain"] == r["changed"], "a repeated poll moved a row"
        assert r["valueless"]["dp-rm-vol"] == fmt([CHART_VOL], 3)[0], (
            "a poll whose entry has no state value blanked or replaced the chart's reading"
        )
        assert r["valueless"]["dp-rm-mc"] == fmt([CHART_MC], 1)[0] + "%"
        assert r["valueless"]["bar"] == f"{CHART_MC}%"
        assert r["back"]["dp-rm-vol"] == fmt([STATE_VOL], 3)[0]
        assert r["back"]["dp-rm-mc"] == fmt([STATE_MC], 1)[0] + "%"

    @pytest.mark.parametrize("mc_after_exit", [STATE_MC, None])
    def test_an_exit_seen_on_a_poll_withdraws_the_mc_reading(self, mc_after_exit):
        base = BY_NAME["state_and_chart_disagree"]
        exited = _state(
            base.symphony,
            {
                "triggered": True,
                "status": "triggered",
                "triggered_at_return": -1.5,
                "symphony_vol": STATE_VOL,
                "mc_prob": mc_after_exit,
            },
        )
        r = _run(
            FLOW.split("const opened")[0].replace("__ID__", repr(base.symphony)).replace(
                "__CHART__", "{ body: H.bodies.C }"
            )
            + r"""
        const opened = rows();
        await poll(H.bodies.exited);
        const exited = rows();
        await poll(H.bodies.exited);
        return { opened: opened, exited: exited, again: rows() };
        """,
            S=base.state_body(),
            C=base.chart_body(),
            exited=exited,
        )
        assert r["opened"]["dp-rm-mc"] == js_to_fixed([STATE_MC], 1)[0] + "%"
        for key in ("exited", "again"):
            assert _text(r[key], "dp-rm-mc") in DASHES, (
                f"{key}: a triggered symphony still shows an MC reading {r[key]['dp-rm-mc']!r}"
            )
            assert r[key]["bar"] == "0%"


# ---------------------------------------------------------------------------
# Contract 1 again, across a close and reopen
# ---------------------------------------------------------------------------


class TestReopeningThePanelRepeatsTheSameRows:
    def test_close_and_reopen_gives_the_same_rows_before_and_after_a_poll(self):
        base = BY_NAME["state_and_chart_disagree"]
        r = _run(
            FLOW.split("const opened")[0].replace("__ID__", repr(base.symphony)).replace(
                "__CHART__", "{ body: H.bodies.C }"
            )
            + r"""
        const first = rows();
        await poll(H.bodies.S);
        const polled = rows();
        closePanel();
        await open(__ID__);
        await H.completeAnimations();
        const reopened = rows();
        await poll(H.bodies.S);
        return { first: first, polled: polled, reopened: reopened, reopenedPolled: rows() };
        """.replace("__ID__", repr(base.symphony)),
            S=base.state_body(),
            C=base.chart_body(),
        )
        for key in ("polled", "reopened", "reopenedPolled"):
            moved = {k: (r["first"][k], r[key][k]) for k in r["first"] if r["first"][k] != r[key][k]}
            assert not moved, f"{key} differs from the first view of the same panel: {moved}"


# ---------------------------------------------------------------------------
# Contract 5: another symphony never shows the previous symphony's chart reading
# ---------------------------------------------------------------------------


class TestSwitchingSymphonyCarriesNothingOver:
    def test_the_new_panel_shows_no_trace_of_the_previous_symphonys_chart_reading(self):
        a_state = {
            "symphony_vol": None,
            "mc_prob": None,
            "para_velocity": 0.75,
            "breakeven_locked": True,
        }
        body = _state(UNTRIGGERED, a_state, drop=("symphony_vol", "mc_prob"))
        body = sup.with_entry(body, OTHER, triggered=False, status="armed", para_velocity=None,
                              breakeven_locked=False)
        for key in ("symphony_vol", "mc_prob"):
            body = sup.without_entry_key(body, OTHER, key)
        a_chart = _chart(UNTRIGGERED, vol=CHART_VOL, mc=CHART_MC, vwap_diff=0.37)
        b_chart = _chart(OTHER, vol=NEW_STATE_VOL, mc=NEW_STATE_MC, vwap_diff=None)
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.S, overrides: [
          ['/api/chart/' + __A__, { body: H.bodies.CA }],
          ['/api/chart/' + __B__, { pending: true }]] });
        await open(__A__);
        await H.completeAnimations();
        const a = rows();
        await open(__B__);
        await H.completeAnimations();
        const bOpened = rows();
        await poll(H.bodies.S);
        const bPolled = rows();
        await H.releasePending('/api/chart/' + __B__, { body: H.bodies.CB });
        await H.completeAnimations();
        return { a: a, bOpened: bOpened, bPolled: bPolled, bFinal: rows() };
        """.replace("__A__", repr(UNTRIGGERED)).replace("__B__", repr(OTHER)),
            S=body,
            CA=a_chart,
            CB=b_chart,
        )
        a = r["a"]
        assert a["dp-rm-vol"] == js_to_fixed([CHART_VOL], 3)[0]
        assert a["dp-rm-mc"] == js_to_fixed([CHART_MC], 1)[0] + "%"
        for stage in ("bOpened", "bPolled"):
            b = r[stage]
            for key in ("dp-rm-vol", "dp-rm-mc", "dp-rm-vwap", "bar", "dp-rm-para-hint",
                        "dp-rm-be-hint", "dp-rm-vwap-hint"):
                assert b[key] != a[key], f"{stage}: {key} still shows the previous symphony's {a[key]!r}"
            assert b["dp-rm-vol"] in DASHES and b["dp-rm-mc"] in DASHES
            assert b["bar"] == "0%"
        final = r["bFinal"]
        assert final["dp-rm-vol"] == js_to_fixed([NEW_STATE_VOL], 3)[0]
        assert final["dp-rm-mc"] == js_to_fixed([NEW_STATE_MC], 1)[0] + "%"
