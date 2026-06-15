"""
M3 spec_bundle provenance + intended_direction contract tests.

Scope: persistence of R1 (time_squeeze_decay_curve_v2) and R2
(vwap_system_a_hwm_gate_v2) facets to the spec_bundles / spec_facets schema
with freeze_discipline = THEORY and the prescribed intended_direction text.

This is the **NN1 structural enforcement** for M3: per the M3 plan §69 +
council synthesis §2.5, an M3 facet persisted with freeze_discipline =
BACKTEST_SELECTION (or any other haircut-evading discipline) turns the BHY
haircut into a lie by omission. The Phase-1 NN1 spec-freeze machinery
already classifies disciplines and rejects BACKTEST_SELECTION on Phase-1
facets; M3 adds two Phase-1.5 facets to the same surface.

Per the M3 plan §118 + literature-pass.md §3.4, the intended_direction
declaration must be in place BEFORE any post-M3 replay attribution can be
audited -- an after-the-fact direction declaration is circular. This test
file pins the declaration as a hard contract.

Scope EXCLUDES the S-1 Stage 2 per-cycle attribution-table validator -- that
piece needs the live-replay S-1 harness which is not yet in the worktree.
Per team-lead's go on option (b) of cycle fix-m3-provenance, the
attribution-table validator becomes a follow-up cycle. This test file pins
the spec_bundle preconditions that the future S-1 harness will consume.

Provenance (D-2 load-bearing): the fixture at
tests/fixtures/math/m3_provenance_facets.json is SCHEMA-DERIVED from
research-m3's literature pass + the M3 plan. It is NOT captured from any
persistence-code output. Required citation substrings, required intended-
direction substrings, and the freeze_discipline enum constraints all
trace to specific section anchors in the research note and the plan.

Persistence-entry-point flexibility: impl-m3 chooses HOW to persist the M3
facets -- either by extending get_or_create_phase1_theory_bundle_id to add
the two new facets, or by adding a sibling helper (e.g.
get_or_create_phase15_m3_bundle_id). The test asserts the CONTRACT only:
after the canonical entry point is called, both facets must be present
with the right discipline + content. The fixture's
persistence_contract.must_persist_via and must_be_introspectable_via
identify the underlying database functions.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import database


_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "math" / "m3_provenance_facets.json"
)


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _resolve_m3_bundle_hash() -> str:
    """
    Call the M3 bundle entry point and return the resulting bundle_hash.

    Discovery order, lowest-friction first:
      1. database.get_or_create_phase15_m3_bundle_id() -- if impl-m3 added
         a sibling helper, prefer it.
      2. database.get_or_create_phase1_theory_bundle_id() -- if impl-m3
         extended the existing helper to include M3 facets.

    A successful call must result in BOTH M3 facets being persisted to the
    spec_facets table under the returned bundle's hash. The persistence is
    asserted by the tests below.

    If neither entry point exists or the call does not produce the M3
    facets, the tests below will fail with a clear message naming the
    missing facet.
    """
    # First preference: a dedicated Phase-1.5 helper.
    phase15_fn = getattr(database, "get_or_create_phase15_m3_bundle_id", None)
    if phase15_fn is not None and callable(phase15_fn):
        bundle_id = phase15_fn()
        # Map bundle_id -> bundle_hash via the spec_bundles row.
        row = database.get_spec_bundle_by_id(bundle_id)
        if row is None:
            raise RuntimeError(
                "get_or_create_phase15_m3_bundle_id() returned id "
                f"{bundle_id} but no spec_bundles row was found. "
                "Persistence is incomplete."
            )
        return row["bundle_hash"]

    # Fallback: the Phase-1 helper has been extended to include M3 facets.
    fallback_fn = getattr(database, "get_or_create_phase1_theory_bundle_id", None)
    assert fallback_fn is not None, (
        "Neither database.get_or_create_phase15_m3_bundle_id nor "
        "database.get_or_create_phase1_theory_bundle_id exists. impl-m3 "
        "must provide an entry point that persists the M3 R1 + R2 facets."
    )
    bundle_id = fallback_fn()
    row = database.get_spec_bundle_by_id(bundle_id)
    if row is None:
        raise RuntimeError(f"Phase-1 bundle id {bundle_id} returned but no spec_bundles row found.")
    return row["bundle_hash"]


def _get_facet_by_name(bundle_hash: str, facet_name: str) -> "dict | None":
    facets = database.get_spec_facets_for_bundle(bundle_hash)
    for f in facets:
        if f["facet_name"] == facet_name:
            return f
    return None


def _facet_text_blob(facet: dict) -> str:
    """
    Concatenate the searchable text fields of a facet row. The
    insert_spec_bundle_facet API exposes facet_value + justification +
    calibration_evidence as the free-text fields; impl-m3 can store the
    citation / formula / intended_direction in whichever combination they
    choose. The contract test searches ACROSS all of them so impl-m3 isn't
    boxed into a single field.
    """
    parts = [
        facet.get("facet_value") or "",
        facet.get("justification") or "",
        facet.get("calibration_evidence") or "",
    ]
    return "\n".join(parts)


# --- Fixture structural integrity ------------------------------------------


def test_m3_provenance_fixture_is_structurally_complete() -> None:
    """
    The fixture must be loadable and contain all top-level keys M3 depends
    on. This is a precondition for the assertions below -- a malformed
    fixture is a test-author bug, not an impl-m3 bug.
    """
    fixture = _load_fixture()
    for key in (
        "r1_facet",
        "r2_facet",
        "freeze_discipline_constraints",
        "persistence_contract",
    ):
        assert key in fixture, f"M3 provenance fixture missing required top-level key: {key!r}"
    # The two facet specs each need name + discipline + value_must_reference.
    for facet_key in ("r1_facet", "r2_facet"):
        spec = fixture[facet_key]
        for required in (
            "facet_name",
            "required_freeze_discipline",
            "value_must_reference",
            "intended_direction_required_substrings",
        ):
            assert required in spec, f"M3 provenance fixture {facet_key} missing key: {required!r}"


# --- R1 facet persistence + content ----------------------------------------


def test_r1_facet_persisted_with_freeze_discipline_theory() -> None:
    """
    After the M3 entry point is called, a spec_facets row must exist with
    facet_name = 'time_squeeze_decay_curve_v2' and freeze_discipline =
    'THEORY'. Per literature-pass.md §1.3 + plan §69, THEORY is the
    correct discipline because f(t) = 1 - sqrt(1 - t) is derived from
    i.i.d.-returns square-root-of-time scaling -- zero free parameters,
    no calibration, no Optuna search on outcomes for the curve shape.
    """
    fixture = _load_fixture()
    spec = fixture["r1_facet"]

    bundle_hash = _resolve_m3_bundle_hash()
    facet = _get_facet_by_name(bundle_hash, spec["facet_name"])

    assert facet is not None, (
        f"R1 facet {spec['facet_name']!r} not found in spec_facets for "
        f"bundle_hash={bundle_hash!r} after calling the M3 entry point. "
        f"impl-m3 must persist this facet via insert_spec_bundle_facet."
    )
    assert facet["freeze_discipline"] == spec["required_freeze_discipline"], (
        f"R1 facet {spec['facet_name']!r} has freeze_discipline "
        f"{facet['freeze_discipline']!r}, expected "
        f"{spec['required_freeze_discipline']!r} (THEORY -- zero free "
        f"parameters under the Danielsson & Zigrand 2003 derivation). "
        f"Per M3 plan §69 + council synthesis §2.5, BACKTEST_SELECTION "
        f"is FORBIDDEN here."
    )


def test_r1_facet_value_cites_derivation() -> None:
    """
    The R1 facet's persisted text (facet_value + justification +
    calibration_evidence -- whichever fields impl-m3 used) must reference
    the closed-form formula AND the citing paper. This is the structural
    enforcement of D-2 (non-circular provenance): the facet itself encodes
    the derivation, not just a label.

    A future contributor reading spec_facets MUST be able to trace the R1
    curve back to Danielsson & Zigrand 2003 directly from the database row.
    """
    fixture = _load_fixture()
    spec = fixture["r1_facet"]

    bundle_hash = _resolve_m3_bundle_hash()
    facet = _get_facet_by_name(bundle_hash, spec["facet_name"])
    assert facet is not None, f"R1 facet {spec['facet_name']!r} not persisted; cannot check value."

    blob = _facet_text_blob(facet)
    for required in spec["value_must_reference"]:
        assert required in blob, (
            f"R1 facet text does not reference {required!r}. The facet "
            f"must encode the derivation citation so spec_facets is "
            f"self-documenting. Got blob:\n{blob}"
        )


def test_r1_facet_declares_intended_direction() -> None:
    """
    Per M3 plan §118 + literature-pass.md §1.5: the intended_direction must
    be declared in the spec_bundle BEFORE any post-M3 replay attribution
    can be reviewed -- an after-the-fact declaration is circular.

    The required substrings come from research-m3's literature pass §1.5:
      'the new R1 curve is less aggressive midday by up to ~0.45 in
       decay-weight, equivalent to ~0.45 pp wider stop at t=0.5, monotone-
       converging at endpoints'

    Substrings tested: 'concave', 'open-loaded', 'less aggressive midday',
    'monotone-converging at endpoints'. impl-m3 may add more context; the
    test only asserts the minimum set.
    """
    fixture = _load_fixture()
    spec = fixture["r1_facet"]

    bundle_hash = _resolve_m3_bundle_hash()
    facet = _get_facet_by_name(bundle_hash, spec["facet_name"])
    assert facet is not None, (
        f"R1 facet {spec['facet_name']!r} not persisted; cannot check intended_direction."
    )

    blob = _facet_text_blob(facet)
    for required in spec["intended_direction_required_substrings"]:
        assert required in blob, (
            f"R1 facet text missing intended_direction substring "
            f"{required!r}. Per M3 plan §118 the declaration must precede "
            f"any replay attribution. Got blob:\n{blob}"
        )


# --- R2 facet persistence + content ----------------------------------------


def test_r2_facet_persisted_with_freeze_discipline_theory() -> None:
    """
    After the M3 entry point is called, a spec_facets row must exist with
    facet_name = 'vwap_system_a_hwm_gate_v2' and freeze_discipline =
    'THEORY'. The gate STRUCTURE is justified from optimal-stopping
    theory (Leung-Zhang 2019, Peskir 1998 maximality principle); the
    threshold VALUE continues to live in the Optuna search space. THEORY
    is the right discipline because what's being frozen is the gate shape,
    not the tuned value.
    """
    fixture = _load_fixture()
    spec = fixture["r2_facet"]

    bundle_hash = _resolve_m3_bundle_hash()
    facet = _get_facet_by_name(bundle_hash, spec["facet_name"])

    assert facet is not None, (
        f"R2 facet {spec['facet_name']!r} not found in spec_facets for "
        f"bundle_hash={bundle_hash!r} after calling the M3 entry point."
    )
    assert facet["freeze_discipline"] == spec["required_freeze_discipline"], (
        f"R2 facet {spec['facet_name']!r} has freeze_discipline "
        f"{facet['freeze_discipline']!r}, expected "
        f"{spec['required_freeze_discipline']!r} (THEORY -- the gate "
        f"shape is justified from optimal-stopping theory). Per M3 plan "
        f"§69 BACKTEST_SELECTION is FORBIDDEN here."
    )


def test_r2_facet_value_cites_derivation() -> None:
    """
    The R2 facet's persisted text must reference Leung-Zhang 2019 + Peskir
    + the maximality principle, so a future contributor can trace the gate
    shape back to its optimal-stopping derivation directly from the
    database row.
    """
    fixture = _load_fixture()
    spec = fixture["r2_facet"]

    bundle_hash = _resolve_m3_bundle_hash()
    facet = _get_facet_by_name(bundle_hash, spec["facet_name"])
    assert facet is not None, f"R2 facet {spec['facet_name']!r} not persisted; cannot check value."

    blob = _facet_text_blob(facet)
    for required in spec["value_must_reference"]:
        assert required in blob, f"R2 facet text does not reference {required!r}. Got blob:\n{blob}"


def test_r2_facet_declares_intended_direction_byte_identical() -> None:
    """
    R2 is a provenance-only closure -- the runtime is byte-identical to
    pre-M3. The intended_direction declaration must encode that the
    behavior is unchanged ('byte-identical') AND that the structural
    framing is the regime-switch construction ('regime-switch'). Per
    literature-pass.md §2.4: 'Max-absolute-deviation: 0 (zero divergent
    cycles in the S-1 Stage 2 attribution table for R2).'
    """
    fixture = _load_fixture()
    spec = fixture["r2_facet"]

    bundle_hash = _resolve_m3_bundle_hash()
    facet = _get_facet_by_name(bundle_hash, spec["facet_name"])
    assert facet is not None, (
        f"R2 facet {spec['facet_name']!r} not persisted; cannot check intended_direction."
    )

    blob = _facet_text_blob(facet)
    for required in spec["intended_direction_required_substrings"]:
        assert required in blob, (
            f"R2 facet text missing intended_direction substring "
            f"{required!r}. R2's intended_direction must encode 'no "
            f"behavioral change' under the provenance-only closure. Got "
            f"blob:\n{blob}"
        )


# --- Negative assertions: forbidden disciplines must raise ------------------


def test_m3_facets_reject_backtest_selection_discipline() -> None:
    """
    NN1 hard rule: M3 facets persisted with freeze_discipline =
    BACKTEST_SELECTION turn the BHY haircut into a lie by omission. The
    underlying insert_spec_bundle_facet API ACCEPTS this discipline (it
    is enum-validated to a permitted set that includes BACKTEST_SELECTION
    for Phase-1 BACKTEST_SELECTION ledger entries) -- but no M3 facet
    must EVER be inserted with that value.

    This test is the structural enforcement: after the M3 entry point is
    called, neither the R1 nor R2 facet has BACKTEST_SELECTION. A future
    impl that 'just runs Optuna' on the R1 curve and stores the tuned
    facet with BACKTEST_SELECTION would fail here.

    BACKTEST_SELECTION on either M3 facet is an automatic NN1 violation
    per council synthesis §2.5.
    """
    fixture = _load_fixture()
    forbidden = set(fixture["freeze_discipline_constraints"]["forbidden"])
    facet_names = [
        fixture["r1_facet"]["facet_name"],
        fixture["r2_facet"]["facet_name"],
    ]

    bundle_hash = _resolve_m3_bundle_hash()
    for facet_name in facet_names:
        facet = _get_facet_by_name(bundle_hash, facet_name)
        if facet is None:
            # Facet not present means the positive-persistence test above
            # already failed; don't double-flag.
            continue
        assert facet["freeze_discipline"] not in forbidden, (
            f"M3 facet {facet_name!r} persisted with FORBIDDEN "
            f"freeze_discipline {facet['freeze_discipline']!r}. This is "
            f"an NN1 violation per M3 plan §69 + council synthesis §2.5; "
            f"the BHY haircut becomes a lie by omission."
        )


# --- Bundle-hash immutability invariant ------------------------------------


def test_m3_bundle_hash_matches_persisted_facets_json() -> None:
    """
    spec_bundles immutability invariant (Phase-1 binding): the bundle_hash
    must equal hash_facets_json(facets_json) -- i.e., the stored hash must
    be derivable from the stored canonical JSON. If impl-m3 forgets to
    update the bundle_hash when adding the M3 facets to facets_json, this
    detects the mismatch.

    Per database.py: bundle_hash = hash_facets_json(canonicalize_facets_json(...)).
    """
    bundle_hash = _resolve_m3_bundle_hash()
    bundle = database.get_spec_bundle(bundle_hash)
    assert bundle is not None, (
        f"spec_bundles row with hash {bundle_hash!r} not found after the M3 entry point was called."
    )
    facets_json = bundle.get("facets_json")
    assert facets_json is not None, (
        f"spec_bundles row {bundle_hash!r} has NULL facets_json. The bundle "
        f"must store the canonical facets JSON alongside the hash."
    )
    recomputed = database.hash_facets_json(facets_json)
    assert recomputed == bundle_hash, (
        f"spec_bundles immutability violated: stored bundle_hash "
        f"{bundle_hash!r} does not equal hash_facets_json(facets_json) = "
        f"{recomputed!r}. impl-m3 likely forgot to update facets_json when "
        f"adding the M3 facets, or wrote a stale hash."
    )


# --- Idempotency ------------------------------------------------------------


def test_m3_entry_point_is_idempotent() -> None:
    """
    The M3 persistence entry point must be idempotent -- calling it
    repeatedly within the same process must return the same bundle_hash
    and must NOT duplicate spec_facets rows (the UNIQUE(bundle_hash,
    facet_name) constraint from migration 024 enforces this at the DB
    level; this test confirms the application layer respects it).

    The Phase-1 helper get_or_create_phase1_theory_bundle_id is already
    idempotent (process-local cache + INSERT OR IGNORE); any M3 sibling
    helper must follow the same pattern.
    """
    bundle_hash_1 = _resolve_m3_bundle_hash()
    bundle_hash_2 = _resolve_m3_bundle_hash()
    assert bundle_hash_1 == bundle_hash_2, (
        f"M3 entry point not idempotent: first call returned "
        f"{bundle_hash_1!r}, second call returned {bundle_hash_2!r}. "
        f"The entry point must return the same bundle_hash across repeated "
        f"calls within the same process."
    )

    # Confirm no duplicate facets were created.
    facets = database.get_spec_facets_for_bundle(bundle_hash_1)
    facet_names = [f["facet_name"] for f in facets]
    duplicates = [n for n in set(facet_names) if facet_names.count(n) > 1]
    assert not duplicates, (
        f"M3 entry point created duplicate spec_facets rows: {duplicates}. "
        f"The UNIQUE(bundle_hash, facet_name) constraint should have "
        f"prevented this -- if it's still happening, migration 024 may not "
        f"be applied in this test DB."
    )
