"""RED tests — AC-5: builder generation gating on signal data (in-memory) +
the crossover/vs display-key format behavior.

Module under test: advisors.frontrunner_builder. Implementer: fr-engine.

CONTRACT SOURCE (feature-plans/frontrunner-signals.md AC-5; crossover-format
reversal 2026-07-16):

AC-5 core: "Candidate frontrunner generation consumes the signal data:
proposed checks must have a matching fr_key with positive edge (cagr > 0 and
sharpe > 0); any candidate containing a Tier-1 remove key is rejected before
backtest spend; generation prompt/context receives the edge stat lines for
the signals it may use. Provenance on each candidate records the signal keys
+ their edge stats."

DE-PRODUCTIZATION (2026-07-16, ADDENDUM 2 AC-R2, operator directive): the
classification-persistence layer this file used to also cover
(`persist_classification_run` + the `resolve_signals_unavailable_marker`
run-marker helper, whose ONLY caller was that persist call) was REMOVED — it
productized a one-time PM cull-analysis deliverable, not a product ask. The
builder still computes classification in memory every run
(extract_fr_checks + classify_fr_checks); it no longer persists a marker or
a row. This file now covers ONLY the in-memory gating/formatting functions:
positive-edge proposal gating, the Tier-1 pre-spend veto, provenance
attachment, and the crossover/vs display-key format functions below
(team-lead, 2026-07-16, broadcast ruling): crossover/ticker-vs-ticker
FRChecks (fr_key=None) are FORMATTED to a display-form key by fr-engine's 3
canonical functions:
    genuine:   passthrough of FRCheck.fr_key
    crossover: f"{ticker}:{window}:xover({rhs_fn},{rhs_val})"
    vs:        f"{ticker}:{window}:vs({rhs_ticker})"
Every crossover/vs row's classification is always "no_edge_data", every edge
stat is None, and such a row must NEVER appear in anything the builder
proposes from.

This file mocks the signal/classification seams (classify_fr_checks,
extract_fr_checks) rather than re-driving the full existing generation
pipeline (SDK calls, plan_tree_compiler, backtest_gate_engine) — those are
covered by the existing frontrunner-builder test suite (PR #96) and are
unaffected by this cycle's scope.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def mod():
    from advisors import frontrunner_builder

    return frontrunner_builder


def _classification_row(fr_key, classification, cagr=None, sharpe=None):
    return {
        "fr_key": fr_key,
        "fn": "relative-strength-index",
        "comparator": "gt",
        "branch_path": ["true"],
        "rsi_live": 55.0 if classification != "no_edge_data" else None,
        "rsi_live_at": "2026-07-16T07:01:00Z" if classification != "no_edge_data" else None,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": None,
        "calmar": None,
        "max_drawdown": None,
        "classification": classification,
        "signal_fetch_ts": "2026-07-16T15:00:00Z" if classification != "no_edge_data" else None,
    }


# ---------------------------------------------------------------------------
# AC-5(a/b): positive-edge-only proposal gating, Tier-1 remove pre-spend veto
# ---------------------------------------------------------------------------


def test_candidate_generation_only_proposes_from_positive_edge_signal_keys(mod):
    """AC-5: 'proposed checks must have a matching fr_key with positive edge
    (cagr > 0 and sharpe > 0)'. Mocks the classification seam with a mix of
    keep/prune/remove rows; asserts the signal_context handed to generation
    (or the gating function's own filtered output) contains ONLY the
    positive-edge (keep) key."""
    classification_rows = [
        _classification_row("SPY:10:80", "keep", cagr=0.06, sharpe=1.0),
        _classification_row("DUST:10:80", "prune", cagr=0.03, sharpe=0.25),
        _classification_row("SPY:10:31", "remove", cagr=-0.37, sharpe=-0.42),
    ]

    positive_edge_keys = mod.filter_positive_edge_signal_keys(classification_rows)
    assert positive_edge_keys == ["SPY:10:80"], (
        f"expected only the positive-edge (keep) key, got {positive_edge_keys}"
    )


def test_candidate_containing_a_tier1_remove_key_is_rejected_before_backtest_spend(mod):
    """AC-5: 'any candidate containing a Tier-1 remove key is rejected before
    backtest spend'. A candidate whose watched-ticker set includes a known
    remove-tier fr_key must be rejected WITHOUT reaching the backtest seam —
    verified by patching composer_backtest_client.run_backtest to raise if
    called, proving the veto fires before spend."""
    classification_rows = [_classification_row("SPY:10:31", "remove", cagr=-0.37, sharpe=-0.42)]
    candidate_watching_removed_key = {"fr_key": "SPY:10:31", "ticker": "SPY"}

    with patch(
        "advisors.composer_backtest_client.run_backtest",
        side_effect=AssertionError(
            "backtest called on a Tier-1-remove candidate — veto did not fire pre-spend"
        ),
    ):
        rejected = mod.candidate_contains_tier1_remove_key(
            candidate_watching_removed_key, classification_rows
        )

    assert rejected is True, "a candidate watching a Tier-1 remove key must be rejected"


def test_candidate_with_only_keep_tier_keys_is_not_rejected(mod):
    classification_rows = [_classification_row("SPY:10:80", "keep", cagr=0.06, sharpe=1.0)]
    candidate = {"fr_key": "SPY:10:80", "ticker": "SPY"}

    rejected = mod.candidate_contains_tier1_remove_key(candidate, classification_rows)
    assert rejected is False


# ---------------------------------------------------------------------------
# AC-5(c): provenance carries signal keys + edge stats
# ---------------------------------------------------------------------------


def test_candidate_provenance_records_signal_keys_and_their_edge_stats(mod):
    """AC-5: 'Provenance on each candidate records the signal keys + their
    edge stats'."""
    classification_rows = [_classification_row("SPY:10:80", "keep", cagr=0.06, sharpe=1.0)]

    provenance = mod.build_signal_provenance(["SPY:10:80"], classification_rows)
    assert "SPY:10:80" in provenance
    entry = provenance["SPY:10:80"]
    assert entry["cagr"] == 0.06
    assert entry["sharpe"] == 1.0
    assert entry["classification"] == "keep"


# ---------------------------------------------------------------------------
# PM-ruling extension: crossover/vs persist-format at the builder's call site
# ---------------------------------------------------------------------------


def test_crossover_fr_check_is_formatted_to_xover_display_key_before_persist(mod):
    """fr-engine's canonical format function: f"{ticker}:{window}:xover({rhs_fn},{rhs_val})".
    Reproducible byte-for-byte given the same node — the Paragons example."""
    formatted = mod.format_crossover_fr_key(
        ticker="SPY", window=10, rhs_fn="moving-average-return", rhs_val="31"
    )
    assert formatted == "SPY:10:xover(moving-average-return,31)"


def test_vs_ticker_comparison_is_formatted_to_vs_display_key_before_persist(mod):
    """fr-engine's shown implementation is a plain f-string
    (f"{ticker}:{window}:vs({rhs_ticker})") — a None window renders literally
    as the string "None" by Python's own str(None) behavior, no special
    casing. Asserting the exact byte-for-byte value per that source, not a
    hedge — the collision-freedom property (letters+parens never appear in a
    genuine numeric segment) holds regardless and is tested separately."""
    formatted = mod.format_vs_fr_key(ticker="LQD", window=None, rhs_ticker="XLV")
    assert formatted == "LQD:None:vs(XLV)", (
        f"expected the literal f-string rendering, got {formatted!r}"
    )

    formatted_with_window = mod.format_vs_fr_key(ticker="LQD", window=10, rhs_ticker="XLV")
    assert formatted_with_window == "LQD:10:vs(XLV)"


def test_display_key_formats_never_collide_with_a_genuine_atlas_key_shape(mod):
    """Non-collision invariant (fr-engine): a genuine key's third segment is
    always a plain number; both display forms always contain a letter and a
    parenthesis, which can never appear in a genuine numeric segment —
    structurally disjoint string spaces."""
    xover_key = mod.format_crossover_fr_key(
        ticker="SPY", window=10, rhs_fn="moving-average-return", rhs_val="31"
    )
    vs_key = mod.format_vs_fr_key(ticker="LQD", window=10, rhs_ticker="XLV")

    for key in (xover_key, vs_key):
        third_segment = key.split(":", 2)[-1]
        with pytest.raises(ValueError):
            float(third_segment)  # a genuine key's third segment always parses as a number


def test_crossover_row_persisted_with_no_edge_data_and_null_edge_stats(mod):
    """The persisted classification row for a crossover FRCheck must have
    classification='no_edge_data' and every edge stat None — never joined,
    since there is nothing to join a crossover key to."""
    row = mod.build_classification_row_for_crossover(
        ticker="SPY",
        window=10,
        rhs_fn="moving-average-return",
        rhs_val="31",
        branch_path=["false", "true"],
    )
    assert row["fr_key"] == "SPY:10:xover(moving-average-return,31)"
    assert row["classification"] == "no_edge_data"
    assert row["cagr"] is None
    assert row["sharpe"] is None
    assert row["rsi_live"] is None


def test_crossover_row_never_appears_in_positive_edge_filtered_keys(mod):
    """AC-5(d): a crossover/vs row must never appear in anything the builder
    proposes from — proven by running it through the SAME positive-edge
    filter used for genuine proposal gating and confirming it's excluded."""
    rows = [
        _classification_row("SPY:10:80", "keep", cagr=0.06, sharpe=1.0),
        mod.build_classification_row_for_crossover(
            ticker="SPY",
            window=10,
            rhs_fn="moving-average-return",
            rhs_val="31",
            branch_path=["false", "true"],
        ),
    ]
    positive_edge_keys = mod.filter_positive_edge_signal_keys(rows)
    assert "SPY:10:xover(moving-average-return,31)" not in positive_edge_keys
    assert positive_edge_keys == ["SPY:10:80"]
