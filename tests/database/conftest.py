"""
Database-level conftest: seeds spec_bundles and spec_facets rows whenever the
migrated_db fixture is active (plan §Deliverables #3 — at least one bundle + one
facet in a freshly migrated fixture DB).

The migrated_db fixture is defined at the test-module level in test_spec_bundles.py
and takes precedence over a conftest override.  We inject seed data via a
function-scoped autouse fixture that depends on migrated_db; pytest will request
migrated_db on our behalf and we seed into whatever DB path it returns.
"""

import json

import pytest

import database as db_module


@pytest.fixture(autouse=True)
def _seed_spec_bundle_rows(request):
    """Seed one spec_bundle + one spec_facet row into migrated_db when present.

    Only fires for tests that request the migrated_db fixture.  Other tests are
    unaffected (fixture is a no-op when migrated_db is absent from the test's
    fixture set).
    """
    migrated_db_path = request.getfixturevalue("migrated_db") if "migrated_db" in request.fixturenames else None
    if migrated_db_path is None:
        return

    # Seed via application accessors — idempotent (INSERT OR IGNORE).
    seed_facets = {"generator_family": "crra-seed", "horizon_bars": 63}
    canonical_json = db_module.canonicalize_facets_json(seed_facets)
    seed_hash = db_module.hash_facets_json(canonical_json)

    db_module.insert_spec_bundle(
        bundle_hash=seed_hash,
        facets_json=canonical_json,
        horizon_bars=63,
        cvar_alpha=0.05,
        generator_family="crra-seed",
    )
    db_module.insert_spec_bundle_facet(
        bundle_hash=seed_hash,
        facet_name="generator_family",
        facet_value=json.dumps("crra-seed"),
        freeze_discipline="THEORY",
        justification="Seed row for fixture DB — theoretically motivated family.",
    )
