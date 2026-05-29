"""
Autotuner test-suite conftest.

Provides shared helpers for the NN1 spec-freeze blast-radius fix:
run_autotuner now requires an explicit spec_bundle_id (Phase-1 strict).
Pre-existing tests that called run_autotuner(bot_state, date, accounts)
need a valid all-THEORY bundle id to satisfy the guard without weakening it.

All helpers are deterministic — the same canonical facets dict always
produces the same bundle_hash, so multiple helpers calling this within
the same test's isolated DB are idempotent (INSERT OR IGNORE).
"""
from __future__ import annotations

import pytest


def make_phase1_theory_bundle() -> int:
    """Insert a minimal all-THEORY Phase-1 spec bundle and return its integer id.

    Idempotent: INSERT OR IGNORE means repeated calls within the same isolated
    DB return the same bundle_id. Safe to call from multiple helpers or fixtures.
    """
    import database as _db

    canon_facets = {
        "gamma": "2.0",
        "utility_family": "CRRA",
        "wealth_argument": "compounded_return",
    }
    canonical_json = _db.canonicalize_facets_json(canon_facets)
    bundle_hash = _db.hash_facets_json(canonical_json)
    _db.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    conn = _db.get_connection()
    bundle_id = conn.execute(
        "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
    ).fetchone()[0]
    conn.close()

    # Insert facets if not already present for this bundle.
    existing = _db.get_spec_facets_for_bundle(bundle_hash)
    existing_names = {r["facet_name"] for r in existing}
    for name, value in canon_facets.items():
        if name not in existing_names:
            _db.insert_spec_bundle_facet(
                bundle_hash=bundle_hash,
                facet_name=name,
                facet_value=value,
                freeze_discipline="THEORY",
                justification="Phase-1 test conftest — all-THEORY bundle",
            )

    return bundle_id


@pytest.fixture
def default_spec_bundle_id():
    """Return a valid all-THEORY spec bundle id for use in run_autotuner calls.

    Relies on the suite-wide _isolate_db autouse fixture (tests/conftest.py) to
    provide a clean per-test DB. Call this fixture in any test that calls
    run_autotuner directly and needs to satisfy the NN1 spec_bundle_id guard.
    """
    return make_phase1_theory_bundle()
