"""RED tests — DoF-ledger cross-subsystem isolation (team-lead ruling
2026-07-11, cff1264c: MUST-FIX for wave-1, not a follow-on).

BACKGROUND: advisors.frontrunner_builder._gate_and_accept_candidate writes a
researcher_dof_ledger row (AC-6 "search breadth recorded") on every candidate
evaluation, using evidence_source="BACKTEST_SELECTION" — the SAME literal
value the real autotuner walk-forward haircut (autotuner.py:_haircut_select,
via compute_n_effective) uses to identify ITS OWN P&L-toured facets. Every
autotuner-side consumer that aggregates across the ledger
(database.get_researcher_dof_ledger_for_run — the real production
N_effective feed at autotuner.py:2487 — and database.count_dof_backtest_selections)
filters on the literal string evidence_source='BACKTEST_SELECTION' with no
producer/subsystem distinction. Confirmed via a full consumer audit (frtest,
2026-07-11) reading every researcher_dof_ledger reader in database.py: a
frontrunner row currently inflates N_effective (and therefore the
BHY/Yekutieli FDR bar) for EVERY symphony's real autotune run, indefinitely
(the ledger is append-only, never pruned).

RATIFIED FIX (team-lead, cff1264c): the frontrunner builder's overlay search
is a structurally DIFFERENT search from the autotuner's parameter
optimization (separate search space; its own multiple-testing is handled
per-batch by evaluate_candidate_batch) — record it per AC-6, but ISOLATE it
from the autotuner's N_effective via a DISTINCT evidence_source value:
"OVERLAY_BACKTEST_SELECTION" (names the evidence KIND, matching the enum's
existing kind-not-producer convention — THEORY/MANDATE/STYLIZED_FACT/
CALIBRATION/BACKTEST_SELECTION/OOS; producer distinction stays in the
spec_bundle_id sentinel, which is KEPT as belt-and-suspenders). Purely
additive: one new member in database._VALID_DOF_EVIDENCE_SOURCES, no schema
or query change (every consumer keys on the literal 'BACKTEST_SELECTION'
string, never IN/!= — a distinct value is excluded by construction).

THESE TESTS PROVE ISOLATION AGAINST THE REAL, UNMOCKED PRODUCTION QUERIES —
not a mock of database.py's internals. If any consumer actually swept the
new evidence_source value in, these tests fail, which is the intended
escalation trigger to the producer-column/separate-store fallback
(team-lead, ae097937). Runs against the per-test isolated state DB
(tests/conftest.py's autouse DB_PATH sentinel + _isolate_db fixture) — never
the real alphabot_state.db.
"""

from __future__ import annotations

import database


def _insert_autotuner_row(*, n_configs_searched: int, spec_bundle_id: str, facet_name: str) -> int:
    """A ledger row shaped exactly like a real autotuner NN1-violation write
    (autotuner.py's own validate_nn1_compliance call site) — evidence_source
    is the REAL 'BACKTEST_SELECTION' value, never the frontrunner's."""
    return database.insert_dof_ledger_row(
        facet_name=facet_name,
        facet_category="specification",
        decision_type="SEARCHED",
        evidence_source="BACKTEST_SELECTION",
        n_configs_searched=n_configs_searched,
        spec_bundle_id=spec_bundle_id,
        justification="autotuner NN1 BACKTEST_SELECTION violation (test fixture)",
    )


def _insert_frontrunner_row(
    *, n_configs_searched: int, facet_name: str = "frontrunner_candidate_search"
) -> int:
    """A ledger row shaped exactly like the RATIFIED frontrunner_builder
    write — evidence_source="OVERLAY_BACKTEST_SELECTION" (not yet a valid
    enum member as of 21608d1 — this call raising ValueError IS this test
    file's RED signal until database._VALID_DOF_EVIDENCE_SOURCES is
    extended)."""
    return database.insert_dof_ledger_row(
        facet_name=facet_name,
        facet_category="specification",
        decision_type="SEARCHED",
        evidence_source="OVERLAY_BACKTEST_SELECTION",
        n_configs_searched=n_configs_searched,
        spec_bundle_id="frontrunner_builder",
        justification="frontrunner_builder candidate search (test fixture)",
    )


# ---------------------------------------------------------------------------
# Real-query isolation: count_dof_backtest_selections (the S accumulator).
# ---------------------------------------------------------------------------


def test_frontrunner_row_does_not_change_the_global_backtest_selection_count():
    """The strongest form of 'zero behavior change to the autotuner's own
    rows': insert a real autotuner BACKTEST_SELECTION row, record the global
    S accumulator, insert a frontrunner OVERLAY_BACKTEST_SELECTION row, and
    assert the accumulator is BYTE-IDENTICAL before and after — not just
    'still correct', but literally unmoved by the frontrunner insert. Calls
    the REAL database.count_dof_backtest_selections (no mocking)."""
    _insert_autotuner_row(
        n_configs_searched=3,
        spec_bundle_id="autotuner-real-bundle-hash-abc123",
        facet_name="gamma_backtest_selection_violation",
    )
    count_before = database.count_dof_backtest_selections()

    _insert_frontrunner_row(n_configs_searched=5)
    count_after = database.count_dof_backtest_selections()

    assert count_after == count_before, (
        f"inserting a frontrunner OVERLAY_BACKTEST_SELECTION row changed the "
        f"global count_dof_backtest_selections() result ({count_before} -> "
        f"{count_after}) — the frontrunner search is polluting the "
        f"autotuner's S accumulator, which feeds N_effective for EVERY "
        f"symphony's FDR bar"
    )
    assert count_before == 3, (
        f"sanity check: the autotuner row's own n_configs_searched=3 must "
        f"be the count BEFORE any frontrunner row exists, got {count_before}"
    )


def test_frontrunner_row_is_excluded_from_a_bundle_scoped_backtest_selection_count():
    """count_dof_backtest_selections(spec_bundle_id=<real bundle>) must never
    include the frontrunner row regardless of scope — belt-and-suspenders on
    top of the global-count test above."""
    _insert_autotuner_row(
        n_configs_searched=2,
        spec_bundle_id="autotuner-real-bundle-hash-xyz789",
        facet_name="wealth_argument_backtest_selection_violation",
    )
    _insert_frontrunner_row(n_configs_searched=7)

    scoped_count = database.count_dof_backtest_selections(
        spec_bundle_id="autotuner-real-bundle-hash-xyz789"
    )
    assert scoped_count == 2, (
        f"expected the bundle-scoped count to reflect ONLY the autotuner "
        f"row's n_configs_searched (2), got {scoped_count} — a frontrunner "
        f"row leaked into a scoped query it can never legitimately match"
    )


# ---------------------------------------------------------------------------
# Real-query isolation: get_researcher_dof_ledger_for_run (the real
# production N_effective feed, autotuner.py:2487).
# ---------------------------------------------------------------------------


def test_frontrunner_row_is_excluded_from_get_researcher_dof_ledger_for_run():
    """The REAL production N_effective feed must never return a frontrunner
    row, with no winning_spec_bundle_id supplied (the broadest, most
    permissive query shape) — proving isolation holds even in the widest
    case, not just when a winner happens to be excluded."""
    _insert_autotuner_row(
        n_configs_searched=1,
        spec_bundle_id="autotuner-real-bundle-hash-run1",
        facet_name="utility_family_backtest_selection_violation",
    )
    _insert_frontrunner_row(n_configs_searched=1, facet_name="unique-frontrunner-marker-run1")

    rows = database.get_researcher_dof_ledger_for_run("2026-07-11T00:00:00")

    facet_names = {r.get("facet_name") for r in rows}
    assert "unique-frontrunner-marker-run1" not in facet_names, (
        f"the frontrunner row (evidence_source=OVERLAY_BACKTEST_SELECTION) "
        f"was returned by get_researcher_dof_ledger_for_run — this is the "
        f"REAL production N_effective feed (autotuner.py:2487); its "
        f"inclusion inflates N_effective for every real symphony autotune "
        f"run. Returned facet_names: {facet_names!r}"
    )
    assert "utility_family_backtest_selection_violation" in facet_names, (
        "sanity check: the real autotuner row must still be returned — "
        "isolation must not silently swallow genuine autotuner rows too"
    )


def test_frontrunner_row_is_excluded_even_when_a_winning_bundle_is_supplied():
    """Same proof with winning_spec_bundle_id supplied (the normal
    production shape during a real symphony's autotune run) — the
    autotuner's OWN winning-bundle row is correctly excluded (pre-existing
    'winner already counted in n_optuna' behavior, UNCHANGED by this fix),
    and the frontrunner row is ALSO excluded (via evidence_source, not
    bundle-id coincidence)."""
    winning_bundle = "autotuner-real-bundle-hash-winner"
    _insert_autotuner_row(
        n_configs_searched=1,
        spec_bundle_id=winning_bundle,
        facet_name="winner-bundle-own-facet",
    )
    _insert_autotuner_row(
        n_configs_searched=4,
        spec_bundle_id="autotuner-real-bundle-hash-loser",
        facet_name="loser-bundle-facet",
    )
    _insert_frontrunner_row(n_configs_searched=1, facet_name="unique-frontrunner-marker-run2")

    rows = database.get_researcher_dof_ledger_for_run(
        "2026-07-11T00:00:00", winning_spec_bundle_id=winning_bundle
    )
    facet_names = {r.get("facet_name") for r in rows}

    assert "unique-frontrunner-marker-run2" not in facet_names, (
        f"frontrunner row leaked into a run-scoped query even with a "
        f"winning_spec_bundle_id supplied: {facet_names!r}"
    )
    assert "winner-bundle-own-facet" not in facet_names, (
        "the winning bundle's own row must still be excluded (pre-existing "
        "'winner already counted in n_optuna' behavior — this fix must not "
        "change it)"
    )
    assert "loser-bundle-facet" in facet_names, (
        "a genuine non-winning autotuner row must still be returned — "
        "isolation must not over-exclude real autotuner rows"
    )
