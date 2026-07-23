"""RED tests — AC-4: edge scoring + keep/cull classification.

Module under test: advisors.frontrunner_signals.classify_fr_checks (NEW,
NOT-STARTED as of this commit — implementer is fr-engine per the PM ruling
committed at 101df72a, point 4: "fr-data = DDL + accessors; fr-engine =
compute + persist call sites (extends AC-3/4/5)"; the module FILE is
frontrunner_signals.py, but the classifier logic is fr-engine's, not
fr-data's).

CONTRACT SOURCE (feature-plans/frontrunner-signals.md AC-4, ratified by
team-lead 2026-07-16): join extracted FR-checks to signal rows by EXACT
fr_key AND live-side comparator=="gt" (only gt RSI checks are edge-comparable
to the gt-only Atlas collection). Classification:
  remove (Tier 1)  when cagr < 0 OR sharpe < 0
  prune  (Tier 2)  when sharpe < FR_WEAK_SHARPE_THRESHOLD (0.38, named
                    constant — p10 of the 152 matched live FR-check Sharpe
                    distribution, 2026-07-16 join)
  keep             otherwise
  no_edge_data     when the check is not edge-comparable at all (lt
                    comparator, fn mismatch, ticker/window/threshold not in
                    the collection) — NEVER scored against a mismatched
                    backtest.

FIXTURE PROVENANCE:
- tests/fixtures/advisors/frontrunner/atlas_raw_docs_signals.json — 10 raw
  Atlas docs pulled VERBATIM from the worktree's alphabot_atlas_cache.db
  dev cache (captured-from-producer; every cagr/sharpe/etc. value asserted
  in this file traces to one of these real docs, never invented).
- tests/fixtures/advisors/frontrunner/live_fr_checks_known_table.json —
  extraction-SHAPE input tuples (fr_key/ticker/fn/comparator/window/
  threshold/branch_path) derived from docs/fr-signals-inputs/joined.json's
  non-backtest fields, plus two synthetic negative-control entries
  (SPY:1:0 fn-mismatch trap, ZZZZ:10:50 missing-from-collection). These are
  INPUT-SHAPE decoupling fixtures (team-lead-ratified 2026-07-16: "synthetic
  INPUT tuples for classifier decoupling is fine — they're extraction-shape
  inputs, and every asserted VALUE comes from the real Atlas docs") — they
  let this file test classify_fr_checks without depending on AC-3's
  extract_fr_checks GREEN implementation.

THE MIS-JOIN TRAP (SPY:10:30): the raw Atlas doc for fr_key "SPY:10:30" has
comparator="gt" (the collection is genuinely gt-only) and real (bad)
backtest stats. A LIVE symphony's extracted FR-check for the SAME ticker/
window/threshold is comparator="lt" (an oversold gate, not overbought) — see
live_fr_checks_known_table.json's SPY:10:30 entry. A join on fr_key STRING
ALONE would spuriously match the live lt-check to the Atlas gt-doc's stats.
The classifier must guard on the LIVE check's own comparator=="gt" before
ever treating a fr_key string match as edge-comparable.

Boundary matrix source: FR_WEAK_SHARPE_THRESHOLD is asserted via the named
constant imported from the module under test, never a bare "0.38" literal,
so this file stays correct if the constant's calibration ever changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "advisors" / "frontrunner"


@pytest.fixture(scope="module")
def frontrunner_signals():
    import advisors.frontrunner_signals as _mod  # noqa: PLC0415

    return _mod


@pytest.fixture(scope="module")
def raw_atlas_docs() -> list[dict]:
    return json.loads((_FIXTURES_DIR / "atlas_raw_docs_signals.json").read_text())


@pytest.fixture(scope="module")
def live_fr_checks() -> list[dict]:
    return json.loads((_FIXTURES_DIR / "live_fr_checks_known_table.json").read_text())


def _signal_row_from_raw_doc(raw_doc: dict) -> dict:
    """Flatten one raw Atlas doc (nested backtest.summary shape) into the
    flat signal_row dict shape fr-data's warehouse accessor returns
    (fr-data's posted AC-1/AC-2 contract, 2026-07-16) — fr_key, ticker,
    window, threshold, comparator, rsi_live, rsi_live_at, cagr, sharpe,
    sortino, calmar, max_drawdown, n_points, vix_destination_json,
    total_strategy_count, true_ticker, false_ticker, fetch_ts.

    This mirrors what load_frontrunner_signals's own normalization does —
    kept local to this test file (not imported from production) so the test
    doesn't depend on that normalization's GREEN implementation existing yet.
    """
    summary = raw_doc.get("backtest", {}).get("summary", {})
    return {
        "fr_key": raw_doc["fr_key"],
        "ticker": raw_doc["ticker"],
        "window": raw_doc["window"],
        "threshold": raw_doc["threshold"],
        "comparator": raw_doc["comparator"],
        "rsi_live": raw_doc.get("rsi_live"),
        "rsi_live_at": raw_doc.get("rsi_live_at"),
        "cagr": summary.get("cagr"),
        "sharpe": summary.get("sharpe"),
        "sortino": summary.get("sortino"),
        "calmar": summary.get("calmar"),
        "max_drawdown": summary.get("max_drawdown"),
        "n_points": summary.get("n_points"),
        "vix_destination_json": json.dumps(raw_doc.get("vix_destination_summary") or {}),
        "total_strategy_count": raw_doc.get("total_strategy_count"),
        "true_ticker": raw_doc.get("backtest", {}).get("true_ticker"),
        "false_ticker": raw_doc.get("backtest", {}).get("false_ticker"),
        "fetch_ts": "2026-07-16T15:00:00Z",
    }


@pytest.fixture(scope="module")
def signal_rows(raw_atlas_docs) -> list[dict]:
    return [_signal_row_from_raw_doc(d) for d in raw_atlas_docs]


def _fr_check(live_fr_checks: list[dict], fr_key: str) -> dict:
    match = next(c for c in live_fr_checks if c["fr_key"] == fr_key)
    return dict(match)


def _classification_for(result: list[dict], fr_key: str) -> dict:
    matches = [row for row in result if row["fr_key"] == fr_key]
    assert len(matches) == 1, (
        f"expected exactly one classification row for {fr_key!r}, got {len(matches)}: {matches}"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# Tier 1 — remove (cagr < 0 OR sharpe < 0)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fr_key",
    ["SPY:21:30", "SPY:10:31", "PBE:10:79", "DBE:10:77"],
)
def test_tier1_remove_when_cagr_or_sharpe_negative(
    frontrunner_signals, live_fr_checks, signal_rows, fr_key
):
    """Known table: all four of these gt-RSI checks have cagr<0 or sharpe<0
    on the real Atlas doc (atlas_raw_docs_signals.json, captured-from-producer
    — the Atlas-side backtest stats are genuine regardless of the note below).
    DBE:10:77 specifically covers the OR-branch where cagr<0 but sharpe>=0
    (cagr=-0.0007, sharpe=+0.055) — a naive AND-based classifier would wrongly
    keep/prune it instead of removing it.

    PROVENANCE NOTE (2026-07-16 rhs-fn discriminator sweep, team-lead-requested,
    fr-doc-flagged): SPY:10:31/PBE:10:79/DBE:10:77 all have >=1 GENUINE
    fixed-threshold occurrence in the 11 real fixture trees (rhs-fn absent).
    SPY:21:30 does NOT — its only real-tree occurrence (real_tree_09,
    n2ooAZTvBRN6ZzpMmWmU) is itself an rhs-fn crossover
    (RSI(SPY,21) gt moving-average-return(30) — "30" is the RHS indicator's
    window via `lhs-window-days`/`rhs-fn`, not a fixed threshold; a correct
    extractor would never emit fr_key="SPY:21:30" from any of the 11 trees).
    This test still exercises classify_fr_checks's own arithmetic against
    SPY:21:30's real (and clean) Atlas-side stats — it does NOT claim a real
    extractor would produce this exact FRCheck as input from these trees."""
    fr_check = _fr_check(live_fr_checks, fr_key)
    result = frontrunner_signals.classify_fr_checks([fr_check], signal_rows)
    row = _classification_for(result, fr_key)
    assert row["classification"] == "remove", (
        f"{fr_key}: expected remove (real Atlas cagr/sharpe are negative or "
        f"OR-triggered), got {row['classification']!r}"
    )


# ---------------------------------------------------------------------------
# Tier 2 — prune (sharpe < FR_WEAK_SHARPE_THRESHOLD, cagr/sharpe both >= 0)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fr_key", ["DUST:10:80", "RINF:10:80", "ZSL:10:84"])
def test_tier2_prune_when_sharpe_below_weak_threshold(
    frontrunner_signals, live_fr_checks, signal_rows, fr_key
):
    """Known table: all three have positive cagr AND positive sharpe, but
    sharpe sits below FR_WEAK_SHARPE_THRESHOLD (real values 0.25-0.37,
    threshold 0.38) — must classify prune, not remove (neither cagr nor
    sharpe is negative) and not keep (sharpe fails the strength floor)."""
    fr_check = _fr_check(live_fr_checks, fr_key)
    result = frontrunner_signals.classify_fr_checks([fr_check], signal_rows)
    row = _classification_for(result, fr_key)
    assert row["classification"] == "prune", (
        f"{fr_key}: expected prune (positive but weak edge), got {row['classification']!r}"
    )


def test_keep_when_edge_is_strong(frontrunner_signals, live_fr_checks, signal_rows):
    """SPY:10:80 real Atlas stats: cagr=0.0625, sharpe=1.009 — well clear of
    both the remove floor (0) and the prune floor (FR_WEAK_SHARPE_THRESHOLD).
    Must classify keep."""
    fr_check = _fr_check(live_fr_checks, "SPY:10:80")
    result = frontrunner_signals.classify_fr_checks([fr_check], signal_rows)
    row = _classification_for(result, "SPY:10:80")
    assert row["classification"] == "keep", (
        f"expected keep for a strong-edge check, got {row['classification']!r}"
    )


# ---------------------------------------------------------------------------
# no_edge_data — the mis-join traps (AC-4's hardest requirement)
# ---------------------------------------------------------------------------


def test_comparator_mismatch_is_no_edge_data_despite_fr_key_string_match(
    frontrunner_signals, live_fr_checks, signal_rows
):
    """THE canonical mis-join trap. The live check's fr_key is 'SPY:10:30'
    with comparator='lt' (an oversold gate). The Atlas collection's doc for
    the SAME fr_key string has comparator='gt' (the collection's own
    invariant: always gt) and carries real (bad) backtest stats. A join on
    fr_key alone would spuriously score the live lt-check against the gt
    doc's backtest. classify_fr_checks must refuse this — the live check's
    OWN comparator must be 'gt' before its fr_key is treated as
    edge-comparable at all."""
    fr_check = _fr_check(live_fr_checks, "SPY:10:30")
    assert fr_check["comparator"] == "lt", "fixture sanity: this check must be the lt-side trap"
    result = frontrunner_signals.classify_fr_checks([fr_check], signal_rows)
    row = _classification_for(result, "SPY:10:30")
    assert row["classification"] == "no_edge_data", (
        "SPY:10:30 lt-check must never be scored against the gt-only Atlas "
        f"doc's stats — got {row['classification']!r}"
    )
    # The mis-join guard means no borrowed edge stats leak through either —
    # a no_edge_data row must not carry the (mismatched) Atlas cagr/sharpe.
    assert row.get("cagr") is None, (
        f"no_edge_data row leaked the mismatched Atlas doc's cagr: {row.get('cagr')!r}"
    )
    assert row.get("sharpe") is None, (
        f"no_edge_data row leaked the mismatched Atlas doc's sharpe: {row.get('sharpe')!r}"
    )


def test_fn_mismatch_is_no_edge_data_even_with_numeric_key_coincidence(
    frontrunner_signals, live_fr_checks, signal_rows
):
    """A non-RSI check (cumulative-return) whose ticker/window/threshold
    happen to numerically resemble an RSI key must never be treated as
    edge-comparable — the collection is RSI-only; fn must match too, not
    just the numeric fr_key string."""
    fr_check = _fr_check(live_fr_checks, "SPY:1:0")
    assert fr_check["fn"] == "cumulative-return"
    result = frontrunner_signals.classify_fr_checks([fr_check], signal_rows)
    row = _classification_for(result, "SPY:1:0")
    assert row["classification"] == "no_edge_data"


def test_missing_from_collection_is_no_edge_data_never_invented(
    frontrunner_signals, live_fr_checks, signal_rows
):
    """A ticker genuinely absent from the signal collection must classify
    no_edge_data honestly — never a fabricated/interpolated classification."""
    fr_check = _fr_check(live_fr_checks, "ZZZZ:10:50")
    result = frontrunner_signals.classify_fr_checks([fr_check], signal_rows)
    row = _classification_for(result, "ZZZZ:10:50")
    assert row["classification"] == "no_edge_data"
    assert row.get("cagr") is None
    assert row.get("sharpe") is None


# ---------------------------------------------------------------------------
# FR_WEAK_SHARPE_THRESHOLD boundary matrix — strict-< semantics
# ---------------------------------------------------------------------------


def test_sharpe_exactly_at_weak_threshold_is_not_pruned(frontrunner_signals, signal_rows):
    """Boundary: sharpe == FR_WEAK_SHARPE_THRESHOLD exactly (not strictly
    below it) must NOT prune — the contract is 'sharpe < threshold', strict.
    Synthetic input constructed directly against the named constant so this
    test tracks the real threshold even if its calibration changes."""
    threshold = frontrunner_signals.FR_WEAK_SHARPE_THRESHOLD
    boundary_key = "BOUND:10:50"
    fr_check = {
        "fr_key": boundary_key,
        "ticker": "BOUND",
        "fn": "relative-strength-index",
        "comparator": "gt",
        "window": 10,
        "threshold": 50.0,
        "branch_path": ["cond-true"],
    }
    signal_row = {
        "fr_key": boundary_key,
        "ticker": "BOUND",
        "window": 10,
        "threshold": 50.0,
        "comparator": "gt",
        "rsi_live": 55.0,
        "rsi_live_at": "2026-07-16T07:01:00Z",
        "cagr": 0.05,
        "sharpe": threshold,
        "sortino": 1.0,
        "calmar": 0.5,
        "max_drawdown": 0.1,
        "n_points": 5000,
        "vix_destination_json": "{}",
        "total_strategy_count": 10,
        "true_ticker": "VIXY",
        "false_ticker": "BIL",
        "fetch_ts": "2026-07-16T15:00:00Z",
    }
    result = frontrunner_signals.classify_fr_checks([fr_check], [signal_row])
    row = _classification_for(result, boundary_key)
    assert row["classification"] == "keep", (
        f"sharpe==threshold exactly must be keep (strict < for prune), got {row['classification']!r}"
    )


def test_sharpe_just_below_weak_threshold_is_pruned(frontrunner_signals, signal_rows):
    """Boundary: sharpe infinitesimally below FR_WEAK_SHARPE_THRESHOLD must prune."""
    threshold = frontrunner_signals.FR_WEAK_SHARPE_THRESHOLD
    boundary_key = "BOUND:10:51"
    fr_check = {
        "fr_key": boundary_key,
        "ticker": "BOUND",
        "fn": "relative-strength-index",
        "comparator": "gt",
        "window": 10,
        "threshold": 51.0,
        "branch_path": ["cond-true"],
    }
    signal_row = {
        "fr_key": boundary_key,
        "ticker": "BOUND",
        "window": 10,
        "threshold": 51.0,
        "comparator": "gt",
        "rsi_live": 55.0,
        "rsi_live_at": "2026-07-16T07:01:00Z",
        "cagr": 0.05,
        "sharpe": threshold - 0.000001,
        "sortino": 1.0,
        "calmar": 0.5,
        "max_drawdown": 0.1,
        "n_points": 5000,
        "vix_destination_json": "{}",
        "total_strategy_count": 10,
        "true_ticker": "VIXY",
        "false_ticker": "BIL",
        "fetch_ts": "2026-07-16T15:00:00Z",
    }
    result = frontrunner_signals.classify_fr_checks([fr_check], [signal_row])
    row = _classification_for(result, boundary_key)
    assert row["classification"] == "prune", (
        f"sharpe just below threshold must prune, got {row['classification']!r}"
    )


def test_negative_sharpe_dominates_over_a_great_cagr(frontrunner_signals):
    """OR-logic hostility: a spectacular cagr must never rescue a negative
    sharpe — remove fires on EITHER condition, not both."""
    boundary_key = "BOUND:10:52"
    fr_check = {
        "fr_key": boundary_key,
        "ticker": "BOUND",
        "fn": "relative-strength-index",
        "comparator": "gt",
        "window": 10,
        "threshold": 52.0,
        "branch_path": ["cond-true"],
    }
    signal_row = {
        "fr_key": boundary_key,
        "ticker": "BOUND",
        "window": 10,
        "threshold": 52.0,
        "comparator": "gt",
        "rsi_live": 55.0,
        "rsi_live_at": "2026-07-16T07:01:00Z",
        "cagr": 5.0,
        "sharpe": -0.0001,
        "sortino": -0.01,
        "calmar": -0.01,
        "max_drawdown": 0.1,
        "n_points": 5000,
        "vix_destination_json": "{}",
        "total_strategy_count": 10,
        "true_ticker": "VIXY",
        "false_ticker": "BIL",
        "fetch_ts": "2026-07-16T15:00:00Z",
    }
    result = frontrunner_signals.classify_fr_checks([fr_check], [signal_row])
    row = _classification_for(result, boundary_key)
    assert row["classification"] == "remove", (
        f"negative sharpe must trigger remove regardless of cagr, got {row['classification']!r}"
    )


def test_negative_cagr_dominates_over_a_great_sharpe(frontrunner_signals):
    """OR-logic hostility, mirrored: a spectacular sharpe must never rescue a
    negative cagr."""
    boundary_key = "BOUND:10:53"
    fr_check = {
        "fr_key": boundary_key,
        "ticker": "BOUND",
        "fn": "relative-strength-index",
        "comparator": "gt",
        "window": 10,
        "threshold": 53.0,
        "branch_path": ["cond-true"],
    }
    signal_row = {
        "fr_key": boundary_key,
        "ticker": "BOUND",
        "window": 10,
        "threshold": 53.0,
        "comparator": "gt",
        "rsi_live": 55.0,
        "rsi_live_at": "2026-07-16T07:01:00Z",
        "cagr": -0.0001,
        "sharpe": 5.0,
        "sortino": 5.0,
        "calmar": 5.0,
        "max_drawdown": 0.01,
        "n_points": 5000,
        "vix_destination_json": "{}",
        "total_strategy_count": 10,
        "true_ticker": "VIXY",
        "false_ticker": "BIL",
        "fetch_ts": "2026-07-16T15:00:00Z",
    }
    result = frontrunner_signals.classify_fr_checks([fr_check], [signal_row])
    row = _classification_for(result, boundary_key)
    assert row["classification"] == "remove", (
        f"negative cagr must trigger remove regardless of sharpe, got {row['classification']!r}"
    )


# ---------------------------------------------------------------------------
# Shape / never-raises hygiene
# ---------------------------------------------------------------------------


def test_classify_fr_checks_returns_one_row_per_input_check(
    frontrunner_signals, live_fr_checks, signal_rows
):
    """Structural: output has exactly one classification row per input
    fr_check, never dropping or duplicating entries."""
    checks = [_fr_check(live_fr_checks, k) for k in ("SPY:10:31", "SPY:10:80", "SPY:10:30")]
    result = frontrunner_signals.classify_fr_checks(checks, signal_rows)
    assert len(result) == 3, f"expected 3 classification rows, got {len(result)}: {result}"
    assert {row["fr_key"] for row in result} == {"SPY:10:31", "SPY:10:80", "SPY:10:30"}


def test_classify_fr_checks_never_raises_on_empty_inputs(frontrunner_signals):
    """Pure function hygiene: empty fr_checks or empty signal_rows must
    degrade to an empty result, never raise."""
    assert frontrunner_signals.classify_fr_checks([], []) == []
    assert frontrunner_signals.classify_fr_checks([], [{"fr_key": "X:1:1"}]) == []
