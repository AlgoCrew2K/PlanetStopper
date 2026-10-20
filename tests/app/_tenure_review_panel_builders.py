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

1. Rosters and run records (``full_record``, ``small_record``, ``edge_record``).
2. Storage helpers that write rows exactly as the pipeline does
   (``store_run``, ``store_recommendation``, ``seed_bot_state``).
3. A tolerant stdlib HTML tree (``parse_html``) so panel tests can scope assertions to
   one entry instead of grepping a page that holds six tabs.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
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
      cannot be scored; no other symphony is correlated with it ("no redundant
      sibling"); long-VIX holdings are available (whole-number day counts)
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
