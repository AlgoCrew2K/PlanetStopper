"""``database.get_autotune_run_objectives``: which objective each stored autotune run optimised.

A run stores the hash of the spec bundle it ran under; the bundle's facets decide the objective
through ``database.objective_kind_for_facets``. The accessor answers for many runs in one call:
one read of the run rows and one facet read per distinct bundle, so a list of fifty runs under
two bundles costs two facet reads, not fifty.

Fallback contract: a run with no bundle, a bundle that was never stored, or a bundle with no
facet rows has no readable objective, and the answer for that run is ``None`` (the card then
says "Best objective score"). That is distinct from a stored bundle whose facets select the
fall-through objective, which is a real answer.
"""

from __future__ import annotations

import pytest

import database
from tests._ux_truth_support import phase1_facets, register_bundle, save_run

_PHASE1_FACETS = phase1_facets()


def test_crra_bundle_reads_as_crra_eu():
    run_id = save_run("a", register_bundle(_PHASE1_FACETS))
    assert database.get_autotune_run_objectives([run_id]) == {run_id: "crra_eu"}


def test_sortino_bundle_reads_as_sortino_loss_aversion():
    bundle = register_bundle({**_PHASE1_FACETS, "objective_kind": "sortino_loss_aversion"})
    run_id = save_run("a", bundle)
    assert database.get_autotune_run_objectives([run_id]) == {run_id: "sortino_loss_aversion"}


def test_answer_matches_the_rule_for_the_bundles_own_facets():
    facets = {"gamma": "2.0", "utility_family": "CARA"}
    run_id = save_run("a", register_bundle(facets))
    assert database.get_autotune_run_objectives([run_id])[run_id] == (
        database.objective_kind_for_facets(facets)
    )


def test_run_with_no_bundle_reads_as_none():
    run_id = save_run("a", None)
    assert database.get_autotune_run_objectives([run_id]) == {run_id: None}


def test_run_whose_bundle_was_never_stored_reads_as_none():
    run_id = save_run("a", "0" * 64)
    assert database.get_autotune_run_objectives([run_id]) == {run_id: None}


def test_stored_bundle_without_facet_rows_reads_as_none_not_as_the_fall_through():
    canonical = database.canonicalize_facets_json(_PHASE1_FACETS)
    bundle_hash = database.hash_facets_json(canonical)
    database.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical)
    run_id = save_run("a", bundle_hash)
    assert database.get_spec_facets_for_bundle(bundle_hash) == [], "self-guard: no facet rows"
    assert database.get_autotune_run_objectives([run_id]) == {run_id: None}


def test_mixed_runs_each_get_their_own_answer():
    crra = save_run("a", register_bundle(_PHASE1_FACETS))
    sortino = save_run(
        "b", register_bundle({**_PHASE1_FACETS, "objective_kind": "sortino_loss_aversion"})
    )
    legacy = save_run("c", None)
    assert database.get_autotune_run_objectives([crra, sortino, legacy]) == {
        crra: "crra_eu",
        sortino: "sortino_loss_aversion",
        legacy: None,
    }


def test_empty_request_returns_an_empty_mapping():
    assert database.get_autotune_run_objectives([]) == {}


def test_facets_are_read_once_per_distinct_bundle(monkeypatch):
    shared = register_bundle(_PHASE1_FACETS)
    other = register_bundle({**_PHASE1_FACETS, "objective_kind": "sortino_loss_aversion"})
    ids = [
        save_run("a", shared, "2026-10-26T04:00:00"),
        save_run("b", shared, "2026-10-26T04:01:00"),
        save_run("c", shared, "2026-10-26T04:02:00"),
        save_run("d", other, "2026-10-26T04:03:00"),
        save_run("e", None, "2026-10-26T04:04:00"),
    ]
    real = database.get_spec_facets_for_bundle
    reads: list[str] = []

    def counting(bundle_hash):
        reads.append(bundle_hash)
        return real(bundle_hash)

    monkeypatch.setattr(database, "get_spec_facets_for_bundle", counting)
    answers = database.get_autotune_run_objectives(ids)

    assert sorted(reads) == sorted([shared, other]), (
        "one facet read per distinct bundle, none for a run without a bundle"
    )
    assert set(answers) == set(ids)


def test_accessor_leaves_the_engine_facing_run_projection_alone():
    """The run rows other readers use do not gain the bundle hash."""
    save_run("proj", register_bundle(_PHASE1_FACETS))
    row = database.get_latest_autotune_run("proj", math_mode=None)
    assert row is not None
    assert "spec_bundle_id" not in row
    assert all("spec_bundle_id" not in r for r in database.get_all_autotune_runs(limit=5))


@pytest.mark.parametrize("bad", ["", "not-a-hash"])
def test_unreadable_bundle_values_degrade_to_none(bad):
    run_id = save_run("bad", bad)
    assert database.get_autotune_run_objectives([run_id]) == {run_id: None}
