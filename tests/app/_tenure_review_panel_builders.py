"""TEST-ONLY builders for the tenure review read surfaces (API route, Overview panel).

Nothing here is imported by production code.

Every run record is produced by the real review compute: a roster of constructed
symphonies goes through ``advisors.tenure_review.build_review`` and ``run_record``,
so a stored run has exactly the shape the producer writes (provenance: output of the
real producer function over constructed inputs with invented names, never a record
shaped by hand to suit the renderer). A few tests then set fields a later pipeline
stage adds to an entry (the explanation and its written date) or poison one field to
prove escaping; those edits are named at the call site.

Three groups live here:

1. Rosters and run records (``full_record``, ``small_record``, ``edge_record``,
   ``quiet_record``, ``gap_record``, ``lone_record``, ``solo_record``, ``apart_record``,
   ``shadow_start_record`` and ``state_record``, one record per redundancy state).
2. Storage helpers that write rows exactly as the pipeline does
   (``store_run``, ``store_recommendation``, ``seed_bot_state``).
3. A tolerant stdlib HTML tree (``parse_html``) so panel tests can scope assertions to
   one entry instead of grepping a page that holds six tabs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import string
from datetime import date
from functools import cache, lru_cache
from html.parser import HTMLParser

import advisors.composer_backtest_client as backtest_client
import advisors.tenure_review as tr
import database as db_module
from tests.advisors import _tenure_review_builders as b

RUN_ID_FIRST = "1" * 32
RUN_ID_SECOND = "2" * 32
RUN_ID_THIRD = "3" * 32

# Invented Composer-hash-shaped ids and display names. The names are invented; the
# ids keep the real shape (twenty alphanumerics) so no fixture value looks synthetic.
FULL_IDS = {
    "retire": "Xe7Nq2Lt9Wz4Kp1Rv8Ys",
    "watch": "Yb3Hc8Jd5Fg2Lk9Mn4Pq",
    "keep": "Yt6Ty1Ui7Op3As5Df8Gh",
    "young": "Ys4Rm9Cd2Vb7Nx5Zq1Wk",
    "dateless": "Dl8Pw3Eh6Tj1Qa9Ux4Fn",
    "silent": "Sh2Kc7Vm5Bz8Ld3Yr6Gt",
    "shadow": "Sw5Jn1Xa9Fe4Ho7Uc2Pv",
}
FULL_NAMES = {
    "retire": "Xenon Tail",
    "watch": "Yankee Tail One",
    "keep": "Yankee Tail Two",
    "young": "Young Sprout",
    "dateless": "Dateless Drift",
    "silent": "Silent Harbor",
    "shadow": "Shadow Harbor",
}

SMALL_IDS = {
    "first": "Cf4Rn8Lm2Wt7Kp3Vx9Ys",
    "second": "Qb1Hc6Jd4Fg8Lk2Mn5Pr",
    "young": "Wy9Ty3Ui5Op7As1Df6Gk",
}
SMALL_NAMES = {
    "first": "Cedar Fold Rotation",
    "second": "Quartz Bay Momentum",
    "young": "Willow Gate Hedge",
}

EDGE_IDS = {
    "steady": "Rs1Aa2Bb3Cc4Dd5Ee6Ff",
    "twin_one": "Wv1Aa2Bb3Cc4Dd5Ee6Gg",
    "twin_two": "Wv2Aa2Bb3Cc4Dd5Ee6Hh",
    "late": "Lt1Aa2Bb3Cc4Dd5Ee6Ii",
}
EDGE_NAMES = {
    "steady": "Steady Climb",
    "twin_one": "Twin Wave One",
    "twin_two": "Twin Wave Two",
    "late": "Late Series",
}

QUIET_IDS = {
    "first": "Qa1Bb2Cc3Dd4Ee5Ff6Gg",
    "second": "Qh1Ii2Jj3Kk4Ll5Mm6Nn",
    "third": "Qo1Pp2Qq3Rr4Ss5Tt6Uu",
}
QUIET_NAMES = {
    "first": "Quiet Meadow Rotation",
    "second": "Quiet Harbor Carry",
    "third": "Quiet Ridge Hedge",
}
# Aligned days enough for "no redundant sibling" to be evidence: more than the aligned days
# the structural gate needs (the larger of the recommender's two floors), with the series
# built from mutually orthogonal waves.
QUIET_MARGIN_DAYS = 19
QUIET_DAYS = b.days_needed_for_structural_gate() + QUIET_MARGIN_DAYS

SMALL_THRESHOLD = 30
TICKERS = {
    FULL_IDS["retire"]: {"AAPL": 0.6, "MSFT": 0.4},
    FULL_IDS["watch"]: {"NVDA": 1.0},
    FULL_IDS["keep"]: {"GLD": 1.0},
    FULL_IDS["young"]: {"TLT": 1.0},
}


def _strong_backtest(days):
    values = {d.isoformat(): 100.0 * 1.01**i for i, d in enumerate(days)}
    return backtest_client.BacktestResult(
        stats={},
        data_warnings=[],
        daily_portfolio_values=values,
        first_day=days[0].isoformat(),
        last_market_day=days[-1].isoformat(),
    )


@lru_cache(maxsize=1)
def _full_base_record() -> dict:
    """Seven live symphonies, threshold 250, as of 2026-12-31:

    * retire: the weaker member of a pair that passes both redundancy gates
    * watch: its realized result trails a strong backtest by far more than the margin
    * keep: neither condition
    * young: Composer basis but not yet eligible
    * dateless: Composer did not report an invested-since date
    * silent: eligible, but its series could not be read and there is no shadow history
    * shadow: eligible on the shadow-history fallback basis
    """
    roster, threshold = b.roster_with_two_redundant_candidates()
    ids = FULL_IDS
    names = FULL_NAMES
    remap = {"tail-x": "retire", "tail-y1": "watch", "tail-y2": "keep"}
    days = b.last_n_nyse_days(b.AS_OF, 266)
    symphonies = []
    for sym in roster:
        key = remap[sym.symphony_id]
        sym.symphony_id = ids[key]
        sym.display_name = names[key]
        if key == "watch":
            sym.backtest = _strong_backtest(days)
        symphonies.append(sym)
    young_days = b.last_n_nyse_days(b.AS_OF, 40)
    symphonies.append(
        b.composer_symphony(
            ids["young"], names["young"], young_days, b.wave_returns(39, amp=0.3, freq=0.71)
        )
    )
    symphonies.append(
        b.make_symphony(
            ids["dateless"],
            names["dateless"],
            None,
            composer_values=b.values_from_returns(days[-30:], b.wave_returns(29, freq=0.53)),
        )
    )
    symphonies.append(
        b.make_symphony(
            ids["silent"], names["silent"], days[0].isoformat(), series_failure="unavailable"
        )
    )
    shadow_returns = {
        d.isoformat(): r
        for d, r in zip(days[1:], b.wave_returns(265, amp=0.2, freq=0.29), strict=True)
    }
    symphonies.append(
        b.make_symphony(
            ids["shadow"],
            names["shadow"],
            days[0].isoformat(),
            series_failure="unavailable",
            shadow_returns=shadow_returns,
        )
    )
    result = b.review(symphonies, as_of=b.AS_OF, threshold=threshold)
    record = tr.run_record(result, b.et(2026, 12, 31, 17, 5))
    verdicts = {e["verdict"] for e in record["entries"]}
    assert {"retire_candidate", "watch", "keep", None} <= verdicts, (
        f"the roster must exercise every verdict the panel renders, got {verdicts}"
    )
    assert any(e["basis"]["kind"] == "shadow" for e in record["entries"])
    return record


@lru_cache(maxsize=1)
def _small_base_record() -> dict:
    """Two eligible Composer-basis symphonies and one young one, threshold 30: the
    eligible pair has an available with/without comparison and the young one is
    listed as excluded from it."""
    days = b.last_n_nyse_days(b.AS_OF, 31)
    first = b.composer_symphony(
        SMALL_IDS["first"],
        SMALL_NAMES["first"],
        days,
        b.wave_returns(30, amp=0.4, drift=0.05),
    )
    second = b.composer_symphony(
        SMALL_IDS["second"], SMALL_NAMES["second"], days, b.wave_returns(30, amp=0.6, freq=1.3)
    )
    young = b.composer_symphony(
        SMALL_IDS["young"],
        SMALL_NAMES["young"],
        b.last_n_nyse_days(b.AS_OF, 10),
        b.wave_returns(9, amp=0.3, freq=0.8),
    )
    result = b.review([first, second, young], as_of=b.AS_OF, threshold=SMALL_THRESHOLD)
    record = tr.run_record(result, b.et(2026, 12, 31, 17, 5))
    marginal = _entry(record, SMALL_IDS["first"])["evidence"]["marginal"]
    assert marginal["status"] == "available", "the small roster must yield a with/without item"
    return record


@lru_cache(maxsize=1)
def _edge_base_record() -> dict:
    """Four live symphonies, threshold 30, as of 2026-12-31, chosen for the figures a
    renderer is tempted to drop:

    * steady: only rising days, so Sortino and Calmar are undefined and the composite
      cannot be scored; no peer reaches the screen with it, but the peers share too few
      aligned days for "no redundant sibling" to be evidence, so its redundancy item is
      unavailable; long-VIX holdings are available (whole-number day counts)
    * twin_one, twin_two: identical return series, so the correlation is exactly 1,
      the interval and the stressed-window correlation are not computed, the holdings
      overlap is unavailable, and the two gates carry different texts
    * late: its Composer series begins well after its invested-since date
    """
    days = b.last_n_nyse_days(b.AS_OF, 31)
    iso = [d.isoformat() for d in days]
    ids = EDGE_IDS
    names = EDGE_NAMES
    steady = b.composer_symphony(
        ids["steady"], names["steady"], days, [0.10 + 0.02 * (i % 5) for i in range(30)]
    )
    wave = b.wave_returns(30, amp=0.5, freq=0.37)
    twin_one = b.composer_symphony(ids["twin_one"], names["twin_one"], days, wave)
    twin_two = b.composer_symphony(ids["twin_two"], names["twin_two"], days, list(wave))
    late = b.make_symphony(
        ids["late"],
        names["late"],
        iso[0],
        composer_values=b.values_from_returns(days[-20:], b.wave_returns(19, amp=0.4, freq=0.91)),
    )
    holdings = tr.HoldingsInput(
        days={
            iso[-5 + i]: {
                ids["steady"]: {"VIXY": 10.0 if i < 3 else 0.0, "SPY": 90.0},
                ids["twin_one"]: {"SPY": 100.0},
                ids["twin_two"]: {"VIXY": 20.0, "SPY": 80.0},
                ids["late"]: {"SPY": 100.0},
            }
            for i in range(5)
        }
    )
    result = b.review(
        [steady, twin_one, twin_two, late], as_of=b.AS_OF, threshold=30, holdings=holdings
    )
    record = tr.run_record(result, b.et(2026, 12, 31, 17, 5))
    steady_entry = _entry(record, ids["steady"])
    perf = steady_entry["evidence"]["performance"]["figures"]
    assert perf["calmar"]["value"] is None and perf["sortino"]["value"] is None
    assert steady_entry["evidence"]["redundancy"]["pair"] is None
    assert steady_entry["evidence"]["long_vix"]["status"] == "available"
    pair = _entry(record, ids["twin_one"])["evidence"]["redundancy"]["pair"]
    assert pair["ci_lower"]["value"] is None and pair["stressed_correlation"]["value"] is None
    late_basis = _entry(record, ids["late"])["basis"]
    assert late_basis["series_begins"] != late_basis["tenure_begins"]
    return record


@lru_cache(maxsize=1)
def _quiet_base_record() -> dict:
    """Three live symphonies on the Composer basis, threshold 20, whose series are
    mutually orthogonal over more aligned days than the structural gate needs: no pair
    reaches the screen and every comparison is long enough, so each redundancy item is
    available with no pair and says "no redundant sibling", and none of them lists
    redundancy as missing."""
    days = b.last_n_nyse_days(b.AS_OF, QUIET_DAYS + 1)
    symphonies = [
        b.composer_symphony(
            QUIET_IDS[key], QUIET_NAMES[key], days, b.sine_returns(QUIET_DAYS, number)
        )
        for number, key in enumerate(("first", "second", "third"), start=1)
    ]
    result = b.review(symphonies, as_of=b.AS_OF, threshold=20)
    record = tr.run_record(result, b.et(2026, 12, 31, 17, 5))
    for stored in record["entries"]:
        item = stored["evidence"]["redundancy"]
        assert item["status"] == "available" and item["pair"] is None, stored["display_name"]
        assert item["summary"], "the quiet roster must yield the no-sibling statement"
        assert "redundancy" not in stored["missing_items"], stored["display_name"]
    return record


# ---------------------------------------------------------------------------
# Further records: day counts against sessions, the comparison portfolio, the shadow
# start and one record per redundancy state. The rosters come from the review compute's
# own test builders; only the ids and names are replaced, by invented names and
# Composer-hash-shaped ids, so a stored run has the shape the producer writes.
# ---------------------------------------------------------------------------

_ID_ALPHABET = string.ascii_letters + string.digits
_ID_LENGTH = 20
_NAME_POOL = (
    "Amber Ledger Rotation",
    "Birch Canal Carry",
    "Cobalt Dune Hedge",
    "Dusk Orchard Tilt",
    "Ember Pier Spread",
    "Fennel Quay Drift",
    "Garnet Loft Swing",
)


def composer_hash_like(kind: str, index: int) -> str:
    """A deterministic id of the real shape: twenty letters and digits."""
    digest = hashlib.sha256(f"{kind}/{index}".encode()).digest()
    return "".join(_ID_ALPHABET[byte % len(_ID_ALPHABET)] for byte in digest[:_ID_LENGTH])


def relabel(roster: list, kind: str) -> list:
    """Replace the id and display name of every symphony of a roster, in roster order."""
    assert len(roster) <= len(_NAME_POOL), "the name pool is shorter than the roster"
    for index, sym in enumerate(roster):
        sym.symphony_id = composer_hash_like(kind, index)
        sym.display_name = _NAME_POOL[index]
    return roster


def _record_of(roster, *, threshold, holdings=None) -> dict:
    result = b.review(roster, as_of=b.AS_OF, threshold=threshold, holdings=holdings)
    return tr.run_record(result, b.et(2026, 12, 31, 17, 5))


def _fresh(base: dict, run_id: str, trigger: str) -> dict:
    record = copy.deepcopy(base)
    record["run_id"] = run_id
    record["trigger"] = trigger
    return record


GAP_RETURNS = 60
GAP_SESSIONS_WITHOUT_A_VALUE = 2
GAP_SESSIONS_WITHOUT_HOLDINGS = 3
# The sessions without holdings are early in the first symphony's window, and the second
# symphony's series begins after them, so its long-VIX days are every session of its window.
GAP_HOLDINGS_GAP_START = 3
GAP_PEER_SERIES_STARTS_AT = 8


@lru_cache(maxsize=1)
def _gap_base_record() -> dict:
    """Two correlated Composer symphonies, threshold 20. The first has no value on two
    interior sessions, so its performance window uses fewer days than its span holds; the
    holdings are missing on three sessions of the window, so the long-VIX days are fewer
    than the sessions; a backtest over its window gives every item a window. The second
    has a value on every session of a shorter series that begins after the missing holdings,
    so its long-VIX days are every session of its window."""
    roster, threshold = b.composite_pair(GAP_RETURNS, 0.9)
    lead, peer = relabel(roster, "gap")
    keys = sorted(lead.composer_values)
    peer.composer_values = {
        k: v for k, v in peer.composer_values.items() if k >= keys[GAP_PEER_SERIES_STARTS_AT]
    }
    for key in keys[10 : 10 + GAP_SESSIONS_WITHOUT_A_VALUE]:
        lead.composer_values[key] = None
    window = tr.qualifying_window(lead, b.AS_OF, threshold)
    first, last = date.fromisoformat(window["first"]), date.fromisoformat(window["last"])
    lead.backtest = b.replica_backtest(
        first, last, b.wave_returns(len(b.nyse_days(first, last)) - 1, amp=0.2)
    )
    held = {lead.symphony_id: {"VIXY": 10.0, "SPY": 90.0}, peer.symphony_id: {"SPY": 100.0}}
    without = set(
        keys[GAP_HOLDINGS_GAP_START : GAP_HOLDINGS_GAP_START + GAP_SESSIONS_WITHOUT_HOLDINGS]
    )
    holdings = tr.HoldingsInput(days={key: held for key in keys if key not in without})
    record = _record_of(roster, threshold=threshold, holdings=holdings)
    short = {
        name
        for e in record["entries"]
        for name in ("performance", "long_vix")
        if (e["evidence"][name]["window"] or {}).get("days", 0)
        < (e["evidence"][name]["window"] or {}).get("sessions", 0)
    }
    assert short == {"performance", "long_vix"}, short
    assert any(
        e["evidence"]["performance"]["window"]["days"]
        == e["evidence"]["performance"]["window"]["sessions"]
        for e in record["entries"]
    ), "the gap record must also hold a window that uses every session"
    return record


@lru_cache(maxsize=1)
def _lone_base_record() -> dict:
    """One eligible Composer symphony beside one that is not yet eligible and one with no
    history: no other symphony is eligible, so its comparison item is not applicable, no
    same-basis peer has a composite, and no backtest was run, so it has no verdict."""
    days = b.last_n_nyse_days(b.AS_OF, 41)
    roster = relabel(
        [
            b.composer_symphony("s", "s", days, b.wave_returns(40, amp=0.4, drift=0.05)),
            b.composer_symphony(
                "y", "y", b.last_n_nyse_days(b.AS_OF, 10), b.wave_returns(9, amp=0.3, freq=0.8)
            ),
            b.no_history_symphony("n", "n", days[0].isoformat()),
        ],
        "lone",
    )
    record = _record_of(roster, threshold=30)
    subject = _entry(record, roster[0].symphony_id)
    assert subject["evidence"]["marginal"]["status"] == "not_applicable"
    assert subject["verdict"] is None and "marginal" in subject["missing_items"]
    return record


@lru_cache(maxsize=1)
def _solo_base_record() -> dict:
    """A single live symphony: nothing to compare it with, so its redundancy and comparison
    items are both not applicable."""
    days = b.last_n_nyse_days(b.AS_OF, 41)
    roster = relabel(
        [b.composer_symphony("s", "s", days, b.wave_returns(40, amp=0.4, drift=0.05))], "solo"
    )
    record = _record_of(roster, threshold=30)
    subject = record["entries"][0]
    assert subject["evidence"]["redundancy"]["status"] == "not_applicable"
    assert subject["evidence"]["marginal"]["status"] == "not_applicable"
    return record


@lru_cache(maxsize=1)
def _apart_base_record() -> dict:
    """One eligible Composer symphony and two eligible symphonies on the shadow-history
    fallback basis: the Composer one has no same-basis peer, so its comparison item is
    not applicable and lists both as excluded for their basis; each fallback one has the
    other on its basis and still has no comparison for want of position values."""
    days = b.last_n_nyse_days(b.AS_OF, 41)
    roster = relabel(
        [
            b.composer_symphony("c", "c", days, b.wave_returns(40, amp=0.4, drift=0.05)),
            b.shadow_symphony(
                "f1", "f1", days[0].isoformat(), b.by_date(days[1:], b.wave_returns(40, amp=0.3))
            ),
            b.shadow_symphony(
                "f2",
                "f2",
                days[0].isoformat(),
                b.by_date(days[1:], b.wave_returns(40, amp=0.5, freq=1.3)),
            ),
        ],
        "apart",
    )
    record = _record_of(roster, threshold=30)
    composer = _entry(record, roster[0].symphony_id)
    marginal = composer["evidence"]["marginal"]
    assert marginal["status"] == "not_applicable"
    assert [x["reason"] for x in marginal["excluded"]] == ["excluded: different basis"] * 2
    assert _entry(record, roster[1].symphony_id)["evidence"]["marginal"]["status"] == "unavailable"
    return record


SHADOW_START_THRESHOLD = 1
SHADOW_START_FIRST_ROW = date(2026, 12, 22)


@lru_cache(maxsize=1)
def _shadow_start_base_record() -> dict:
    """Two shadow-basis symphonies. The first has its first stored row on its invested-since
    date, so that row is its own baseline and the first shadow return is the next row's.
    The second has a single stored row on its invested-since date, so no return exists to
    date and the basis states no first-return date."""
    sessions = list(b.nyse_days(SHADOW_START_FIRST_ROW, b.AS_OF))
    only = b.AS_OF
    roster = relabel(
        [
            b.shadow_symphony(
                "a",
                "a",
                sessions[0].isoformat(),
                b.by_date(sessions, b.wave_returns(len(sessions), amp=2.0)),
            ),
            b.shadow_symphony("b", "b", only.isoformat(), {only.isoformat(): 1.5}),
        ],
        "start",
    )
    record = _record_of(roster, threshold=SHADOW_START_THRESHOLD)
    first = _entry(record, roster[0].symphony_id)
    assert first["basis"]["series_begins"] == sessions[1].isoformat()
    assert first["evidence"]["performance"]["window"]["first"] == sessions[0].isoformat()
    assert _entry(record, roster[1].symphony_id)["basis"]["series_begins"] is None
    return record


# One roster per state of the redundancy item that the plan names. Each builder returns
# (roster in order, threshold); the roster's symphonies take their ids and names from
# ``relabel`` in that order.
_NOTE_DAYS = b.NOTE_RETURNS + 1
_FLOOR_DAYS = b.days_needed_for_structural_gate()
_ENOUGH_DAYS = _FLOOR_DAYS + QUIET_MARGIN_DAYS
_SHORT_PEER_RETURNS = 150
_STRESS_TAIL_DAYS = 10
_BELOW_SCREEN_RHO = 0.64
_ABOVE_SCREEN_RHO = 0.66
_SHORT_HIT_RETURNS = 100
_DIVERGING_LONG_RETURNS = 265


def _pair_roster(first, second):
    return b.two_composer_symphonies(first, second), b.COMPOSITE_PAIR_THRESHOLD


def _roster_peer_without_composite_on_another_basis():
    days = b.last_n_nyse_days(b.AS_OF, _NOTE_DAYS)
    subject_returns, _ = b.orthogonal_pair(b.NOTE_RETURNS, 0.4, k=26)
    return [
        b.composer_symphony("subject", "subject", days, subject_returns),
        b.composer_symphony("sibling", "sibling", days[-10:], b.wave_returns(9)),
        b.shadow_symphony(
            "extra",
            "extra",
            days[0].isoformat(),
            b.by_date(days[1:], b.wave_returns(b.NOTE_RETURNS, freq=1.1)),
        ),
    ], b.NOTE_THRESHOLD


def _roster_compared_peer_beside_one_peer_of_each_kind_without_a_composite():
    days = b.last_n_nyse_days(b.AS_OF, _ENOUGH_DAYS + 1)
    lead, steady = b.orthogonal_pair(_ENOUGH_DAYS, 0.4, k=_ENOUGH_DAYS // 10, scale=0.3)
    thin = b.composer_symphony("thin", "thin", days[-40:], [-r for r in lead[-39:]])
    thin.composer_values[days[-5].isoformat()] = None
    return [
        b.composer_symphony("lead", "lead", days, lead),
        b.composer_symphony("steady", "steady", days, steady),
        b.composer_symphony("young", "young", days[-10:], [-r for r in lead[-9:]]),
        b.shadow_symphony(
            "fallback",
            "fallback",
            days[-31].isoformat(),
            b.by_date(days[-30:], b.wave_returns(30, freq=1.1)),
        ),
        thin,
        b.composer_symphony("flat", "flat", days[-40:], [0.05] * 39),
    ], b.COMPOSITE_PAIR_THRESHOLD


def _roster_flagged_candidate_beside_a_short_peer():
    roster = b.roster_with_a_candidate_in_two_passing_pairs(stronger_sorts_first=True)
    days = b.last_n_nyse_days(b.AS_OF, _NOTE_DAYS)
    roster.append(
        b.composer_symphony(
            "short",
            "short",
            days[-(_SHORT_PEER_RETURNS + 1) :],
            b.sine_returns(_SHORT_PEER_RETURNS, 4, amp=0.4),
        )
    )
    return roster, b.NOTE_THRESHOLD


def _roster_stressed_window_without_variance():
    calm_a, calm_b = b.calm_block(_FLOOR_DAYS - 1)
    first = calm_a + [-50.0] * _STRESS_TAIL_DAYS
    second = calm_b + [-50.0 + 0.05 * math.sin(0.9 * i) for i in range(_STRESS_TAIL_DAYS)]
    assert b.oracle_stressed_correlation(first, second) is None
    return _pair_roster(first, second)


STATE_ROSTERS = {
    "thin_coverage": lambda: (b.note_family_roster("thin_coverage"), b.NOTE_THRESHOLD),
    "flat_window": lambda: (b.note_family_roster("flat_window"), b.NOTE_THRESHOLD),
    "no_peer_composite": _roster_peer_without_composite_on_another_basis,
    "below_floor_no_hit": lambda: b.composite_pair(_FLOOR_DAYS - 1, _BELOW_SCREEN_RHO),
    "below_floor_hit": lambda: b.composite_pair(_SHORT_HIT_RETURNS, _ABOVE_SCREEN_RHO),
    "at_floor": lambda: b.composite_pair(_FLOOR_DAYS, _BELOW_SCREEN_RHO),
    "peers_without_composite": (
        _roster_compared_peer_beside_one_peer_of_each_kind_without_a_composite
    ),
    "flagged_beside_short_peer": _roster_flagged_candidate_beside_a_short_peer,
    "structural_undefined": _roster_stressed_window_without_variance,
    "interval_not_met": lambda: _pair_roster(*b.block_pair(_FLOOR_DAYS, "diverging")),
    "structural_not_met": lambda: _pair_roster(*b.block_pair(_DIVERGING_LONG_RETURNS, "diverging")),
}


@cache
def _state_base_record(kind: str) -> dict:
    roster, threshold = STATE_ROSTERS[kind]()
    relabel(roster, f"state-{kind}")
    return _record_of(roster, threshold=threshold)


def state_id(kind: str, index: int) -> str:
    """The id of the symphony at ``index`` of the roster the state ``kind`` is built from."""
    return composer_hash_like(f"state-{kind}", index)


def _entry(record: dict, symphony_id: str) -> dict:
    matches = [e for e in record["entries"] if e["symphony_id"] == symphony_id]
    assert len(matches) == 1, f"expected one entry for {symphony_id}"
    return matches[0]


def full_record(
    run_id: str = RUN_ID_FIRST,
    *,
    trigger: str = "nightly",
    completed_at: str | None = None,
    threshold_note: str | None = None,
) -> dict:
    """A fresh copy of the full run record with the given identity."""
    record = copy.deepcopy(_full_base_record())
    record["run_id"] = run_id
    record["trigger"] = trigger
    if completed_at is not None:
        record["completed_at"] = completed_at
    record["threshold_note"] = threshold_note
    return record


def small_record(run_id: str = RUN_ID_FIRST, *, trigger: str = "nightly") -> dict:
    record = copy.deepcopy(_small_base_record())
    record["run_id"] = run_id
    record["trigger"] = trigger
    return record


def edge_record(run_id: str = RUN_ID_FIRST, *, trigger: str = "nightly") -> dict:
    """A fresh copy of the record whose figures are null, undefined or unpaired."""
    record = copy.deepcopy(_edge_base_record())
    record["run_id"] = run_id
    record["trigger"] = trigger
    return record


def quiet_record(run_id: str = RUN_ID_FIRST, *, trigger: str = "nightly") -> dict:
    """A fresh copy of the record whose redundancy items all say "no redundant sibling"."""
    record = copy.deepcopy(_quiet_base_record())
    record["run_id"] = run_id
    record["trigger"] = trigger
    return record


def gap_record(run_id: str = RUN_ID_FIRST, *, trigger: str = "nightly") -> dict:
    """A fresh copy of the record whose windows use fewer days than their spans hold."""
    return _fresh(_gap_base_record(), run_id, trigger)


def lone_record(run_id: str = RUN_ID_FIRST, *, trigger: str = "nightly") -> dict:
    """A fresh copy of the record whose one eligible symphony has no eligible peer."""
    return _fresh(_lone_base_record(), run_id, trigger)


def solo_record(run_id: str = RUN_ID_FIRST, *, trigger: str = "nightly") -> dict:
    """A fresh copy of the record of a single live symphony."""
    return _fresh(_solo_base_record(), run_id, trigger)


def apart_record(run_id: str = RUN_ID_FIRST, *, trigger: str = "nightly") -> dict:
    """A fresh copy of the record of one Composer-basis and two fallback-basis symphonies."""
    return _fresh(_apart_base_record(), run_id, trigger)


def shadow_start_record(run_id: str = RUN_ID_FIRST, *, trigger: str = "nightly") -> dict:
    """A fresh copy of the record whose shadow baselines sit on the tenure's first day."""
    return _fresh(_shadow_start_base_record(), run_id, trigger)


def state_record(kind: str, run_id: str = RUN_ID_FIRST, *, trigger: str = "nightly") -> dict:
    """A fresh copy of the record built from the roster ``STATE_ROSTERS[kind]``."""
    return _fresh(_state_base_record(kind), run_id, trigger)


def empty_record(run_id: str = RUN_ID_FIRST) -> dict:
    """A run that completed with zero live symphonies."""
    record = full_record(run_id)
    record["entries"] = []
    return record


def entry_of(record: dict, symphony_id: str) -> dict:
    return _entry(record, symphony_id)


def entry_with_verdict(record: dict, verdict) -> dict:
    matches = [e for e in record["entries"] if e["verdict"] == verdict]
    assert matches, f"no entry with verdict {verdict!r}"
    return matches[0]


def set_explanation(record: dict, symphony_id: str, text: str | None, written_on: str | None):
    """Set the two fields the run pipeline adds to a retire-candidate entry after the
    review compute: the explanation and the day it was written."""
    entry = _entry(record, symphony_id)
    entry["explanation"] = text
    entry["explanation_written_on"] = written_on
    return entry


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def store_run(record: dict) -> int:
    """Write one completed run row exactly as the run pipeline does."""
    return db_module.insert_advisor_observation(
        advisor_role=tr.TENURE_REVIEW_RUN_ROLE,
        subject_type="tenure_review_run",
        subject_id=record["run_id"],
        verdict="completed",
        raw_response=record,
    )


def recommendation_for(record: dict, entry: dict, *, explanation=None, written_on=None) -> dict:
    """The recommendation row body for a retire-candidate entry: the fields the
    existing recommender persists, plus the run link and window keys the run
    pipeline adds. Every number is read from the record, none is invented."""
    pair = entry["evidence"]["redundancy"]["pair"]
    sibling = next(e for e in record["entries"] if e["symphony_id"] == pair["sibling_id"])
    perf = entry["evidence"]["performance"]["figures"]
    sib_perf = sibling["evidence"]["performance"]["figures"]
    comp = entry["evidence"]["composite"]["figures"]["composite"]["value"]
    sib_comp = sibling["evidence"]["composite"]["figures"]["composite"]["value"]

    def metrics(figs):
        return {
            "annualized_return": figs["annualized_return"]["value"],
            "sharpe": figs["sharpe"]["value"],
            "sortino": figs["sortino"]["value"],
            "max_drawdown": figs["max_drawdown"]["value"],
            "calmar": figs["calmar"]["value"],
        }

    return {
        "candidate_id": entry["symphony_id"],
        "sibling_id": pair["sibling_id"],
        "correlation": pair["correlation"]["value"],
        "ci_lower": pair["ci_lower"]["value"],
        "ci_upper": pair["ci_upper"]["value"],
        "n_obs": pair["n_obs"],
        "candidate_composite": comp,
        "sibling_composite": sib_comp,
        "candidate_metrics": metrics(perf),
        "sibling_metrics": metrics(sib_perf),
        "uncertainty_gate_passed": pair["uncertainty_gate"]["state"] == "passed",
        "structural_redundancy_gate_passed": pair["structural_gate"]["state"] == "passed",
        "stressed_correlation": pair["stressed_correlation"]["value"],
        "holdings_overlap": None,
        "basis_label": entry["basis"]["label"],
        "explanation": explanation,
        "explanation_written_on": written_on,
        "review_run_id": record["run_id"],
        "as_of_trading_day": record["as_of_trading_day"],
    }


def store_recommendation(body: dict) -> int:
    return db_module.insert_advisor_observation(
        advisor_role="RETIREMENT_RECOMMENDATION",
        subject_type="symphony",
        subject_id=body["candidate_id"],
        symphony_id=body["candidate_id"],
        verdict="retire_candidate",
        raw_response=body,
    )


def store_full_run(
    run_id: str = RUN_ID_FIRST,
    *,
    explanation: str | None = "This symphony is redundant with its stronger sibling.",
    written_on: str | None = "2026-12-31",
    **record_kwargs,
) -> dict:
    """Store the full run plus the recommendation row of its retire-candidate and
    return the stored record."""
    record = full_record(run_id, **record_kwargs)
    candidate = entry_with_verdict(record, "retire_candidate")
    set_explanation(record, candidate["symphony_id"], explanation, written_on)
    store_recommendation(
        recommendation_for(record, candidate, explanation=explanation, written_on=written_on)
    )
    store_run(record)
    return record


def seed_bot_state(include_small: bool = False) -> None:
    """Write the live roster the checklist and the name lookup read: display names and
    distinct holdings per symphony, so a checklist can be tied to its own symphony."""
    state = {}
    for key, sid in FULL_IDS.items():
        state[sid] = {
            "name": FULL_NAMES[key],
            "logic_holdings": TICKERS.get(sid, {}),
        }
    if include_small:
        for key, sid in SMALL_IDS.items():
            state[sid] = {"name": SMALL_NAMES[key], "logic_holdings": {"QQQ": 1.0}}
    db_module.save_state(state)


def seed_edge_bot_state() -> None:
    """The live roster of the edge record: display names and holdings per symphony."""
    db_module.save_state(
        {
            sid: {"name": EDGE_NAMES[key], "logic_holdings": {"QQQ": 1.0}}
            for key, sid in EDGE_IDS.items()
        }
    )


def seed_quiet_bot_state() -> None:
    """The live roster of the quiet record: display names and holdings per symphony."""
    db_module.save_state(
        {
            sid: {"name": QUIET_NAMES[key], "logic_holdings": {"QQQ": 1.0}}
            for key, sid in QUIET_IDS.items()
        }
    )


def seed_roster_state(record: dict) -> None:
    """The live roster of any record: each entry's display name with one holding."""
    db_module.save_state(
        {
            e["symphony_id"]: {"name": e["display_name"], "logic_holdings": {"QQQ": 1.0}}
            for e in record["entries"]
        }
    )


def decisions_by_id() -> dict:
    return {row["candidate_id"]: row for row in db_module.get_retirement_decisions()}


def reset_review_run_state() -> None:
    state = tr.REVIEW_RUN_STATE
    state.release()
    if state.try_start("nightly"):
        state.finish_success()


def observation_count(role: str | None = None) -> int:
    conn = db_module.get_connection()
    try:
        if role is None:
            row = conn.execute("SELECT COUNT(*) FROM advisor_observations").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM advisor_observations WHERE advisor_role = ?", (role,)
            ).fetchone()
        return row[0]
    finally:
        conn.close()


def forbid_network(monkeypatch) -> list:
    """Make any outbound HTTP attempt fail the test loudly and record it.

    Patches the public requests entry points, the session every one of them goes
    through, urllib and the connection class under urllib3, so a request made through
    any of them is caught. Returns the list of recorded attempts (empty on success).
    """
    import http.client
    import urllib.request

    import requests
    import requests.sessions

    attempts: list = []

    def refuse(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("an outbound HTTP request was attempted on a read path")

    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "post", refuse)
    monkeypatch.setattr(requests, "request", refuse)
    monkeypatch.setattr(requests.sessions.Session, "request", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)
    return attempts


# ---------------------------------------------------------------------------
# HTML tree
# ---------------------------------------------------------------------------

_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)


class Node:
    def __init__(self, tag: str, attrs: dict, parent: Node | None):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list = []

    @property
    def testid(self) -> str | None:
        return self.attrs.get("data-testid")

    def text(self) -> str:
        parts = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.text())
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def everything(self) -> str:
        """Visible text plus every attribute value below this node, for assertions
        that accept a label shown as text or as a tooltip."""
        parts = [self.text()]
        for node in self.walk():
            parts.extend(str(v) for v in node.attrs.values() if v)
        return " ".join(parts)

    def walk(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()

    def find_all(self, testid: str) -> list[Node]:
        return [n for n in self.walk() if n.testid == testid]

    def find(self, testid: str) -> Node | None:
        found = self.find_all(testid)
        return found[0] if found else None

    def ancestor(self, testid: str) -> Node | None:
        node = self.parent
        while node is not None:
            if node.testid == testid:
                return node
            node = node.parent
        return None

    def is_inside(self, other: Node) -> bool:
        node = self.parent
        while node is not None:
            if node is other:
                return True
            node = node.parent
        return False

    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("[document]", {}, None)
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        self._stack[-1].children.append(data)


def parse_html(body: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(body)
    builder.close()
    return builder.root


def panel_of(body: str) -> Node:
    """The Retirement Recommendations panel of a rendered AI Advisor page."""
    root = parse_html(body)
    panel = root.find("retirement-recommendations-panel")
    assert panel is not None, "the page has no retirement-recommendations-panel"
    return panel


def entry_node(panel: Node, symphony_id: str) -> Node:
    """The review entry of one symphony, found by its Composer hash."""
    nodes = [
        n
        for n in panel.find_all("tenure-review-entry")
        if n.attrs.get("data-symphony-id") == symphony_id
    ]
    assert len(nodes) == 1, f"expected exactly one review entry for {symphony_id}, got {len(nodes)}"
    return nodes[0]


def strict_json(response) -> dict:
    """Parse a response body refusing the non-standard NaN and Infinity tokens a
    browser's response.json() would reject."""

    def refuse(token):
        raise AssertionError(f"response body carries the non-JSON token {token}")

    return json.loads(response.get_data(as_text=True), parse_constant=refuse)


def leaf_nodes_containing(node: Node, needle: str) -> list[Node]:
    """The innermost elements below ``node`` whose own visible text contains ``needle``
    (case-insensitive): a figure's name and its value sit in one such element, so a
    claim about "next to" is a claim about one of these."""
    needle = needle.lower()
    found = []
    for candidate in node.walk():
        if needle not in candidate.text().lower():
            continue
        if any(
            isinstance(child, Node) and needle in child.text().lower()
            for child in candidate.children
        ):
            continue
        found.append(candidate)
    return found
