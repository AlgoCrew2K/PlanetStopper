"""Advisor-page readings and seeds shared by the AI Advisor page-copy and explainer modules.

TEST-ONLY. The expected text of every row comes from ``tests/ui/_page_copy.py`` (the committed
fixture); a slot is filled from the value the test patched into its authority, in the format the
Values table states, and never by calling the production formatter. Nothing here types a row.

Seeds are written through the real writers:

* ``seed_strategy_builder``: survivor rows are what ``advisors.strategy_builder_engine`` writes
  (its ``_persist_survivor``/``_persist_rejected`` raw_response keys, copied from the producer's
  own function body); a backtest-failure row is that same record with ``backtest_error`` added.
  No producer stores a ``backtest_error`` on an observation row today (the engine only keeps it on
  the in-memory candidate), but the template's "Backtest failures" section reads that key, so the
  row is labelled synthetic here.
* ``seed_frontrunner_proposals``: ``database.insert_frontrunner_proposal`` over the captured
  incumbent tree; ids are the writer's.
* ``seed_long_tenure_review`` and ``seed_recommendation_cards``: the stored run record of
  ``tests/app/_tenure_review_panel_builders.py`` (captured shapes), extended with copies of its own
  entries under new ids, labelled synthetic.
"""

from __future__ import annotations

import copy
import json
import pathlib
import re

import database
from tests.app import _retirement_tab_world as w
from tests.app import _tenure_review_panel_builders as pb
from tests.ui import _page_copy as copy_fixture
from tests.ui._shell_support import _FR_INCUMBENT_HASH, _FR_METRICS, _REAL_INCUMBENT_TREE

# The tab each Copy table A key sits on; None is the caption above the tab bar.
TAB_OF_A_KEY = {
    "A01": None,
    "O01": "overview",
    "O03": "overview",
    "O08": "overview",
    "O15": "overview",
    "O27": "overview",
    "C01": "correlations",
    "R01": "retirement",
    "R02": "retirement",
    "R22": "retirement",
    "W01": "asset-swaps",
    "L01": "logic-changes",
    "T02": "chat",
    "B00": "strategy-builder",
    "B03": "strategy-builder",
    "B07": "strategy-builder",
    "B10": "strategy-builder",
    "X01": "frontrunner-builder",
    "X03": "frontrunner-builder",
    "X05": "frontrunner-builder",
}

# The keys the Overview's cycle changed: their descriptions are read from this plan's Copy table A
# (``_page_copy.a_text``) and their terms from the Overview Glossary, never from the reference rows.
# A01 and O01 are removed; O03, O08, O15 and O27 are shortened. A later cycle moves its own keys here.
RE_PINNED_A_KEYS = ("A01", "O01", "O03", "O08", "O15", "O27")
OVERVIEW_A_KEYS = ("O03", "O08", "O15", "O27")
REMOVED_A_KEYS = ("A01", "O01")
OWNED_A_KEYS = tuple(k for k in TAB_OF_A_KEY if k not in RE_PINNED_A_KEYS)

# The hosts whose terms now sit in a Glossary (this plan's Table G): their reference term rows are
# read from the Glossary, not from the disclosures the reference had. A later cycle adds its host.
GLOSSARY_HOSTS_DONE = ("Overview",)

# The heading each description sits directly under (None: a tab or panel line with no heading).
HEADING_OF_A_KEY = {
    "O08": "Recent autotune runs",
    "O15": "Market Prism",
    "O27": "Advisor observations",
    "R01": "Retirement recommendations",
    "R02": "Correlated pairs",
    "R22": "Symphony tenure review",
    "B07": "Strategy proposals",
    "B10": "Backtest failures",
    "X05": "Pending approval",
}

# Rows whose text is the ref text of a kept element; the model slot they hold is derived below.
KEPT_A_KEYS = ("B03", "X03")

# The tab dot titles at the reference, which must stay (S-1.23).
TAB_DOT_TITLES = {
    "overview": "Summary & autotune runs",
    "correlations": "Low risk — pure measurement",
    "asset-swaps": "Low risk when gated",
    "logic-changes": "Higher overfitting risk",
    "chat": "Explain-only",
    "strategy-builder": "Highest overfitting risk — building from scratch",
    "frontrunner-builder": "Real Composer write — approval required",
}

# The tab each Copy table B host belongs to; "Advisor, every tab" sits under the caption.
TAB_OF_B_HOST = {
    "Advisor, every tab": None,
    "Overview": "overview",
    "Correlations": "correlations",
    "Retirement": "retirement",
    "Asset Swaps": "asset-swaps",
    "Logic Changes": "logic-changes",
    "Strategy Builder": "strategy-builder",
    "Frontrunner": "frontrunner-builder",
}

DISCLOSURE_SUMMARY = "What the terms mean"


def soup(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


def norm(text: str) -> str:
    return " ".join(text.split())


def b_rows_for(a_key: str) -> list[dict]:
    """The Copy table B rows that sit in the disclosure under Copy table A row ``a_key``."""
    return [
        r
        for r in copy_fixture.rows("B")
        if r["host"] in TAB_OF_B_HOST and r.get("placement_target") == a_key
    ]


def moved_term_keys() -> set[str]:
    """The reference term keys that sit in a finished host's Glossary (and in no disclosure)."""
    return {line["key"] for host in GLOSSARY_HOSTS_DONE for line in copy_fixture.glossary(host)}


def b_rows_of_tab(tab: str | None) -> list[dict]:
    return [r for r in copy_fixture.rows("B") if TAB_OF_B_HOST.get(r["host"], "?") == tab]


def format_number(value: float, decimals: int) -> str:
    """Independent of the production helper: round, then drop trailing zeros and a bare point."""
    text = f"{round(float(value), decimals):.{decimals}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def phrase_of(slot: str, copy_row: str) -> tuple[str, str]:
    """(phrase holding the slot, whole fallback phrase) of one Values row."""
    row = copy_fixture.slot_row(slot, copy_row)
    return row["phrase"], row["fallback"]


def with_slots(text: str, copy_row: str, values: dict[str, str | None]) -> str:
    """``text`` with each slot phrase filled, or replaced whole by its fallback when the value is None."""
    out = text
    for slot, value in values.items():
        phrase, fallback = phrase_of(slot, copy_row)
        assert phrase in out, f"{copy_row}: the Values phrase {phrase!r} is not in the copy"
        out = out.replace(
            phrase, copy_fixture.fill(phrase, **{slot: value}) if value is not None else fallback
        )
    return out


def slot_names(record: dict) -> list[str]:
    return list(record.get("slots") or [])


def expected_row(key: str, table: str = "A", **values: str | None) -> str:
    """The text a row must show, its slots filled from ``values`` (a missing slot is a failure)."""
    record = copy_fixture.row(key, table)
    text = copy_fixture.text(key, table)
    missing = [s for s in slot_names(record) if s not in values]
    assert not missing, f"{key}: no value given for slot(s) {missing}"
    return with_slots(text, key, {s: values[s] for s in slot_names(record)})


def kept_model_note(key: str, model: str | None) -> str:
    """The kept Strategy Builder note (B03) with its model slot in the state the page is in.

    ``copy.json`` holds the note as captured with no model (it reads "model unavailable"); MU5 gives
    the unresolved wording and the note otherwise stays. ``model`` None is the unresolved state.
    """
    assert key == "B03"
    mu5 = copy_fixture.row("MU5", "C")
    ref_text = copy_fixture.text("B03", "A")
    head_old = mu5["old_text"].split(" (the rest")[0]
    head_unavailable = head_old.replace("{model}", "model unavailable")
    assert head_unavailable in ref_text, "the captured note no longer opens as MU5's old text says"
    if model is None:
        head_new = mu5["new_unresolved"].split(" (the rest")[0]
    else:
        head_new = head_old.replace("{model}", model)
    return ref_text.replace(head_unavailable, head_new)


def explainer_nodes(page, key: str) -> list:
    return page.select(f'[data-explainer="{key}"]')


def disclosure_nodes(page, key: str) -> list:
    return page.select(f'details[data-terms="{key}"]')


def visible_in_markup(node) -> bool:
    """Not hidden by the markup: no ``hidden`` attribute, inline display none, or closed ``details``."""
    cursor = node
    while cursor is not None and getattr(cursor, "name", None):
        if cursor.has_attr("hidden"):
            return False
        style = (cursor.get("style") or "").replace(" ", "").lower()
        if "display:none" in style or "visibility:hidden" in style:
            return False
        if cursor.name == "details" and not cursor.has_attr("open") and cursor is not node:
            # A node inside a closed details is visible only through its summary.
            if node.name != "summary" or node.parent is not cursor:
                return False
        cursor = cursor.parent
    return True


def panel_of(page, tab: str | None):
    """The tab panel element, or the page root for the caption above the tab bar."""
    if tab is None:
        return page
    found = page.select_one(f'[data-testid="tab-panel-{tab}"]')
    assert found is not None, f"the page has no tab-panel-{tab}"
    return found


def next_element(node):
    cursor = node.next_sibling
    while cursor is not None and not getattr(cursor, "name", None):
        cursor = cursor.next_sibling
    return cursor


def nearest_heading_before(panel, node) -> str:
    """The text of the last ``.section-header`` or ``.adv-heading`` before ``node`` in the panel."""
    last = ""
    for el in panel.select(".section-header, .adv-heading, [data-explainer]"):
        if el is node:
            return last
        if el.has_attr("data-explainer"):
            continue
        last = norm(el.get_text())
    return last


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------

_SB_RAW = {
    "objective": "diversify",
    "template_id": "built-new",
    "params": {"objective": "diversify"},
    "rules_text": "weight-equal over SPY, QQQ",
    "metrics": {"annualized_return": 0.12, "sharpe": 1.1, "calmar": 0.9, "max_drawdown": -0.14},
    "cagr": 0.12,
    "sharpe": 1.1,
    "calmar": 0.9,
    "max_drawdown": -0.14,
    "correlation_vs_live": 0.4,
    "blended_drawdown": -0.12,
    "n_survivors": 1,
    "gate_decision": "ADOPT_CANDIDATE",
    "winner_p_adj": 0.01,
    "n_candidates": 4,
    "fdr_q": 0.05,
    "fdr_adjusted_threshold": 0.024,
    "caveats": [],
    "run_id": "page-explainers-run",
    "evidence_injected": {},
    "invocation_source": "http-route:/ai-advisor/strategy-builder/run",
    "candidate_hash": None,
}


def seed_strategy_builder(*, survivors: int = 1, backtest_failures: int = 1) -> None:
    for index in range(survivors):
        raw = dict(copy.deepcopy(_SB_RAW), candidate_id=f"survivor-{index}")
        database.insert_advisor_observation(
            advisor_role="STRATEGY_BUILDER",
            subject_type="symphony",
            subject_id=f"sb-survivor-{index}",
            verdict="ADOPT_CANDIDATE",
            raw_response=raw,
            symphony_id="",
        )
    for index in range(backtest_failures):
        raw = dict(
            copy.deepcopy(_SB_RAW),
            candidate_id=f"failed-{index}",
            gate_decision="WITHHELD_FDR",
            backtest_error="Composer rejected the backtest (HTTP 422)",
        )
        database.insert_advisor_observation(
            advisor_role="STRATEGY_BUILDER",
            subject_type="symphony",
            subject_id=f"sb-failed-{index}",
            verdict="WITHHELD_FDR",
            raw_response=raw,
            symphony_id="",
        )


def seed_frontrunner_proposals(count: int, *, incumbent: str = _FR_INCUMBENT_HASH) -> list[int]:
    """``count`` pending proposals for one incumbent through the writer; the writer's ids."""
    tree = json.loads(_REAL_INCUMBENT_TREE.read_text(encoding="utf-8"))
    return [
        database.insert_frontrunner_proposal(
            symphony_id=incumbent,
            proposal_source="frontrunner_builder",
            candidate_tree=copy.deepcopy(tree),
            metrics_json=dict(_FR_METRICS),
        )
        for _ in range(count)
    ]


def long_tenure_record(entries: int = 11) -> dict:
    """The captured full review record extended to ``entries`` entries (copies under new ids)."""
    record = pb.full_record()
    base = record["entries"]
    extended = list(base)
    index = 0
    while len(extended) < entries:
        clone = copy.deepcopy(base[index % len(base)])
        clone["symphony_id"] = f"synthetic-tenure-entry-{len(extended):02d}"
        clone["display_name"] = f"Synthetic tenure entry {len(extended):02d}"
        # A clone is never a retire candidate: its pair would name a sibling that is not in the run.
        if clone.get("verdict") == "retire_candidate":
            clone["verdict"] = "watch"
        extended.append(clone)
        index += 1
    record["entries"] = extended[:entries]
    return record


def seed_long_tenure_review(entries: int = 11) -> dict:
    record = long_tenure_record(entries)
    pb.store_run(record)
    return record


def seed_recommendation_cards(count: int = 3, run_id: str = "page-explainers-cards-run") -> None:
    """A stored ok run and ``count`` recommendation cards stamped with its run id."""
    summary = w.derived_summary(run_id=run_id, recommendations=count)
    w.store_raw_summary(summary)
    record = pb.full_record()
    candidate = pb.entry_with_verdict(record, "retire_candidate")
    base = pb.recommendation_for(record, candidate)
    for index in range(count):
        body = dict(copy.deepcopy(base), run_id=run_id)
        body["candidate_id"] = f"synthetic-candidate-{index:02d}"
        pb.store_recommendation(body)


def tab_dot_titles(page) -> dict[str, str]:
    out = {}
    for button in page.select('[role="tab"][data-tab]'):
        badge = button.select_one(".cap-tab-badge")
        if badge is not None and badge.has_attr("title"):
            out[button["data-tab"]] = badge["title"]
    return out


def unescape_text(html: str) -> str:
    return re.sub(r"\s+", " ", soup(html).get_text(" "))


# ---------------------------------------------------------------------------
# Values and expectations shared by the render and browser modules
# ---------------------------------------------------------------------------


def full_world_values() -> dict[str, str]:
    """Each slot as the full world must print it: from the stored review, the stored run and constants."""
    from advisors import retirement_recommender as recommender
    from advisors import tenure_review

    return {
        "tenure_threshold": str(pb.full_record()["threshold_in_effect"]),
        "screen_threshold": str(json.loads(w.CAPTURE_ROW["raw_response"])["screen_threshold"]),
        "stress_fraction_pct": format_number(recommender.STRESS_WINDOW_FRACTION * 100, 1),
        "watch_trail_pp": format_number(tenure_review.BACKTEST_TRAIL_WATCH_PP, 6),
    }


def expected_a(key: str, values: dict[str, str], *, model: str | None = None) -> str:
    if key in OVERVIEW_A_KEYS:
        # This plan's text: the Overview rows carry no slot.
        text = copy_fixture.a_text(key)
        assert text is not None, f"{key} reads no description"
        return text
    if key == "B03":
        return kept_model_note("B03", model)
    return expected_row(key, "A", **values)


# ---------------------------------------------------------------------------
# Autotune runs (the table of the Overview), as GET /api/autotune-runs answers them
# ---------------------------------------------------------------------------

_FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "ai_advisor"
SHARED_LINE_PREFIX = "Same for every run: "


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _symphony_keys(count: int) -> list[str]:
    from tests._ux_truth_support import captured_symphonies

    keys = [database.normalize_name(name) for _, name in captured_symphonies()][:count]
    assert len(keys) == count, f"the captured state holds {len(keys)} symphonies, not {count}"
    return keys


def save_run_like(
    kind: str,
    key: str,
    stamp: str,
    *,
    naive_sharpe: float | None = None,
    selection_tstat: float | None = None,
    baseline_decision: str | None = None,
) -> None:
    """One run through ``database.save_autotune_run`` with the values of a fixture.

    ``kind`` is ``reverted`` (``autotune_run_reverted_to_fallback.json``) or ``adopted``
    (``autotune_run_adopted_ai.json``). The score and the t-stat are never stored unless asked
    for, so those two cells read N/A in a run of either kind: ``naive_sharpe`` and
    ``selection_tstat`` store them (named edits of the fixture), and ``baseline_decision`` replaces
    the stored outcome word
    (for example the producer's own "Reset to Global Default", autotuner.py, or a word the page
    has no mapping for).
    """
    source = _fixture(
        {
            "reverted": "autotune_run_reverted_to_fallback.json",
            "adopted": "autotune_run_adopted_ai.json",
        }[kind]
    )
    extra = (
        {
            "validation_sharpe": source["validation_sharpe"],
            "frozen_eval_sharpe": source["frozen_eval_sharpe"],
            "pbo": source["pbo"],
        }
        if kind == "adopted"
        else {}
    )
    database.save_autotune_run(
        stamp,
        key,
        source["oos_alpha"],
        source["train_alpha"],
        baseline_decision if baseline_decision is not None else source["baseline_decision"],
        source["fallback_oos_alpha"],
        source["default_oos_alpha"],
        selection_tstat=selection_tstat,
        naive_sharpe=naive_sharpe,
        **extra,
    )


def seed_autotune_runs(
    count: int, *, adopted_at: int | None = None, older_for: int = 0
) -> list[str]:
    """One run per captured symphony (``count`` of them); returns the symphony keys.

    Every run is of kind ``reverted``, except run ``adopted_at`` (kind ``adopted``), so that one
    Outcome differs and nothing else does. Run times differ by a day per symphony, so no run-time
    cell repeats. The first ``older_for`` symphonies also get a second, older ``reverted`` run (an
    older-runs row each).
    """
    keys = _symphony_keys(count)
    for index, key in enumerate(keys):
        save_run_like(
            "adopted" if adopted_at == index else "reverted",
            key,
            f"2026-10-{10 + index:02d}T04:00:00",
        )
    for index in range(older_for):
        save_run_like("reverted", keys[index], f"2026-09-{1 + index:02d}T04:00:00")
    return keys


def route_rows() -> list[dict]:
    """``GET /api/autotune-runs`` over what is stored: the body the page's script reads."""
    from tests.ui import _advisor_page_answers as answers

    return answers.autotune_runs_answer()["json"]
