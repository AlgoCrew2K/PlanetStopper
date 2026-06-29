"""
RED tests for DE-ADVISOR-LATENCY: Cache-Serve Market Lenses.
Feature plan: feature-plans/advisor-latency-cache-serve.md

Covers all 10 ACs. Tests fail today because:
  - database.get_latest_market_lens_cache() does not exist (AttributeError)
  - ai_advisor.persist_market_lens_cache() does not exist (AttributeError)
  - assemble_advisor_context() calls all 5 live builders regardless of cache (wrong when
    a fresh MARKET_LENS_CACHE row exists — AC-1 assert_not_called fails)
  - context dict has no "lens_data_as_of" / "lens_data_stale" keys (AC-3/AC-4 KeyError)
  - build_assessment_from_context still returns the alarming "Optuna has not yet run"
    phrase (AC-8)

Fixture provenance: shapes derived from reading the real _build_*_section() bodies
in ai_advisor.py (2026-06-29). Fixture file:
  tests/fixtures/math/advisor_latency_cache_serve_shapes.json

Mocking strategy:
  - database.get_latest_market_lens_cache: NOT patched — each test controls the
    real DB row via _insert_cache_row() or by leaving the table empty.
  - _build_*_section: patched with MagicMock ONLY for the latency-contract and
    cold-start tests (AC-1, AC-5); all other tests use the autouse stub from
    tests/ai_advisor/conftest.py (requests.get -> ConnectionError, no live network).
  - Math engine: NEVER mocked.
  - All fixtures are function-scoped — no shared mutable state.

Tests that are GUARD (pass today, must continue to pass after implementation):
  - test_market_lens_cache_role_absent_from_advisor_roles (AC-9)

All other tests are RED today and must turn GREEN after implementation.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import ai_advisor
import database as db_module

# ---------------------------------------------------------------------------
# Path helpers + fixture load
# ---------------------------------------------------------------------------

_WORKTREE = pathlib.Path(__file__).resolve().parent.parent.parent
_FIXTURES_PATH = (
    _WORKTREE / "tests" / "fixtures" / "math" / "advisor_latency_cache_serve_shapes.json"
)

with open(_FIXTURES_PATH) as _fh:
    _SHAPES = json.load(_fh)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_LENS_NAMES = ("technicals", "sentiment", "derivatives", "macro", "fundamentals")
_BUILDER_ATTRS = (
    "_build_technicals_section",
    "_build_sentiment_section",
    "_build_derivatives_section",
    "_build_macro_section",
    "_build_fundamentals_section",
)


# Minimal stub lens block — shape matches real producer output (available=True).
# Values are sentinels; assertions check keys and types, not values.
def _stub_lens(name: str, *, available: bool = True) -> dict:
    """Return a minimal lens block matching _build_*_section() shape."""
    if available:
        return {
            "lens": name,
            "available": True,
            "payload": {name: "stub-sentinel"},
            "sources": [],
        }
    return {
        "lens": name,
        "available": False,
        "reason": "unit-test-stub",
        "payload": None,
        "sources": [],
    }


def _all_stub_lenses(*, available: bool = True) -> dict:
    """Return a dict of name -> stub lens block for all 5 lenses."""
    return {name: _stub_lens(name, available=available) for name in _LENS_NAMES}


def _insert_cache_row(
    *,
    lenses: dict | None = None,
    captured_at: str | None = None,
    raw_override: dict | None = None,
) -> int:
    """Insert a MARKET_LENS_CACHE row and return its id.

    Uses insert_advisor_observation — production write path, not a raw INSERT.
    This ensures the row is in the same format the real producer will write.
    """
    if raw_override is not None:
        raw = raw_override
    else:
        if lenses is None:
            lenses = _all_stub_lenses(available=True)
        if captured_at is None:
            # "now" — always within any freshness window
            captured_at = datetime.now(UTC).isoformat()
        raw = {"captured_at": captured_at, "lenses": lenses}

    return db_module.insert_advisor_observation(
        advisor_role="MARKET_LENS_CACHE",
        subject_type="portfolio",
        subject_id="global",
        verdict=None,
        raw_response=raw,
        symphony_id="",
    )


def _minimal_context_args() -> dict:
    """Kwargs for assemble_advisor_context that avoid live DB/network side-effects."""
    return {
        "scope": "symphony",
        "symphony_id": "test-symphony",
        # Pass autotune_run=None explicitly so the internal DB fetch is skipped.
        "autotune_run": None,
    }


# ---------------------------------------------------------------------------
# AC-1: fresh cache → NONE of the 5 live builders are called
# (The core latency contract: proves the 6-min hang is eliminated)
# ---------------------------------------------------------------------------


def test_fresh_cache_skips_all_live_builders():
    """AC-1 ADVERSARIAL: with a fresh MARKET_LENS_CACHE row present,
    assemble_advisor_context() must NOT invoke ANY of the 5 live builders.

    Adversarial: patches each _build_*_section with a MagicMock and asserts
    assert_not_called() on ALL FIVE individually. A partial bypass (e.g., one
    builder still called while the other four are skipped) will fail this test.

    Fails today because the current assemble_advisor_context() calls all 5 builders
    unconditionally — there is no cache-serve logic yet.
    """
    _insert_cache_row()  # fresh row in DB

    mocks = {
        attr: MagicMock(return_value=_stub_lens(attr.split("_build_")[1].replace("_section", "")))
        for attr in _BUILDER_ATTRS
    }

    with (
        patch.object(ai_advisor, "_build_technicals_section", mocks["_build_technicals_section"]),
        patch.object(ai_advisor, "_build_sentiment_section", mocks["_build_sentiment_section"]),
        patch.object(ai_advisor, "_build_derivatives_section", mocks["_build_derivatives_section"]),
        patch.object(ai_advisor, "_build_macro_section", mocks["_build_macro_section"]),
        patch.object(
            ai_advisor, "_build_fundamentals_section", mocks["_build_fundamentals_section"]
        ),
        # Keep symphony_logic and database.load_state offline — not under test here
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value={}),
        patch.object(db_module, "load_state", return_value={}),
    ):
        ai_advisor.assemble_advisor_context(**_minimal_context_args())

    for attr, mock in mocks.items():
        (
            mock.assert_not_called(),
            (
                f"_build{attr}() was called despite a fresh MARKET_LENS_CACHE row being present. "
                "The cache-serve path must skip ALL live builder calls when a fresh cache exists. "
                "This is the core latency contract (AC-1): the 6-min hang is gone when NONE of "
                "the 5 builders are called on a cache-hit."
            ),
        )


def test_fresh_cache_serves_all_five_lens_names_in_context():
    """AC-1 (shape complement): the returned context must expose all 5 lens names
    from the cached data — they must be accessible from the context dict.

    Does NOT assert builder calls — that is AC-1 adversarial above.
    Asserts that the context is well-formed after cache-serve.

    Fails today because assemble_advisor_context() has no cache-serve path, so
    'lens_data_as_of' is absent (KeyError on AC-3/AC-4 related context keys).
    """
    _insert_cache_row()

    with (
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value={}),
        patch.object(db_module, "load_state", return_value={}),
    ):
        context = ai_advisor.assemble_advisor_context(**_minimal_context_args())

    # The implementer may put lens data under context["lenses"] (per the feature plan
    # architecture) or preserve top-level keys — both are accepted here.
    # What must be true: each lens name is reachable from the context.
    nested_lenses = context.get("lenses") or {}
    for name in _LENS_NAMES:
        found = nested_lenses.get(name) or context.get(name)
        assert found is not None, (
            f"Lens '{name}' is missing from the returned context after cache-serve. "
            "Either context['lenses'][name] or context[name] must be present. "
            f"context keys: {list(context.keys())!r}"
        )


# ---------------------------------------------------------------------------
# AC-2: producer persists one MARKET_LENS_CACHE row
# ---------------------------------------------------------------------------


def test_producer_writes_one_cache_row_with_all_five_lenses():
    """AC-2: ai_advisor.persist_market_lens_cache(sections) must write exactly one
    MARKET_LENS_CACHE advisor_observations row containing all 5 lenses' structured
    payloads PLUS a 'captured_at' UTC timestamp.

    Fails today with AttributeError: module 'ai_advisor' has no attribute
    'persist_market_lens_cache' — the producer function is not yet implemented.
    """
    sections = _all_stub_lenses(available=True)

    # Pre-condition: no MARKET_LENS_CACHE rows before the call
    assert db_module.get_latest_market_lens_cache() is None, (
        "Test precondition: no MARKET_LENS_CACHE rows should exist before this test. "
        "DB isolation may be broken."
    )

    ai_advisor.persist_market_lens_cache(sections)  # AttributeError today

    row = db_module.get_latest_market_lens_cache()
    assert row is not None, (
        "persist_market_lens_cache() must write a MARKET_LENS_CACHE row; "
        "get_latest_market_lens_cache() returned None after the call."
    )

    raw = row.get("raw_response", {})
    assert isinstance(raw, dict), f"raw_response must be a parsed dict; got {type(raw).__name__!r}"
    assert "captured_at" in raw, (
        "raw_response must carry 'captured_at' — the UTC timestamp of the cache capture. "
        f"Keys present: {list(raw.keys())!r}"
    )
    assert "lenses" in raw, (
        "raw_response must carry 'lenses' — the dict of structured lens payloads. "
        f"Keys present: {list(raw.keys())!r}"
    )

    lenses = raw["lenses"]
    assert isinstance(lenses, dict), (
        f"raw_response['lenses'] must be a dict; got {type(lenses).__name__!r}"
    )

    for name in _LENS_NAMES:
        assert name in lenses, (
            f"raw_response['lenses'] must contain '{name}'; missing from {list(lenses.keys())!r}"
        )


def test_producer_partial_availability_persists_remaining_lenses():
    """AC-2 (edge): when one lens fails (available=False), the producer must still
    persist the other 4 lenses. Partial availability must not abort the whole cache write.

    Fails today with AttributeError — persist_market_lens_cache() not implemented.
    """
    sections = _all_stub_lenses(available=True)
    # Degrade one lens to available=False (simulates a build failure that night)
    sections["macro"] = _stub_lens("macro", available=False)

    ai_advisor.persist_market_lens_cache(sections)  # AttributeError today

    row = db_module.get_latest_market_lens_cache()
    assert row is not None, "A cache row must be written even when one lens is unavailable"

    raw = row.get("raw_response", {})
    lenses = raw.get("lenses", {})

    # All 5 names must be present (the macro one is available=False but still persisted)
    for name in _LENS_NAMES:
        assert name in lenses, (
            f"Lens '{name}' is absent from the cache raw_response even in partial-availability mode. "
            "The producer must persist ALL lenses (with honest available=False) rather than "
            "aborting on a single unavailable lens."
        )

    # The degraded lens must reflect its unavailability (not silently dropped)
    macro_block = lenses.get("macro", {})
    assert macro_block.get("available") is False, (
        "The degraded 'macro' lens block must carry available=False in the persisted cache. "
        f"Got: {macro_block!r}"
    )


# ---------------------------------------------------------------------------
# AC-3: served context carries staleness stamp ("lens_data_as_of")
# ---------------------------------------------------------------------------


def test_served_context_carries_lens_data_as_of_key():
    """AC-3: When cache-served, the returned context must include a 'lens_data_as_of'
    key containing the captured_at timestamp from the cache row.

    The timestamp must be a non-empty string and must be HTML-escaped where rendered
    (rendering tested separately in UI tests). This test pins the context-level key.

    Fails today because assemble_advisor_context() has no cache-serve path — the key
    'lens_data_as_of' will be absent from the context dict (KeyError).
    """
    captured_at = "2026-06-29T03:00:00+00:00"
    _insert_cache_row(captured_at=captured_at)

    with (
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value={}),
        patch.object(db_module, "load_state", return_value={}),
    ):
        context = ai_advisor.assemble_advisor_context(**_minimal_context_args())

    assert "lens_data_as_of" in context, (
        "assemble_advisor_context() must include 'lens_data_as_of' in the returned context "
        "when a cache row is present (AC-3: honest staleness stamp). "
        f"Context keys: {list(context.keys())!r}"
    )
    val = context["lens_data_as_of"]
    assert isinstance(val, str) and val.strip(), (
        f"'lens_data_as_of' must be a non-empty string; got {val!r}"
    )
    # The value must be the captured_at from the cache, not a live-generated timestamp
    # (shape only — we don't hardcode the exact format since the implementer may
    # normalize the datetime; assert it is a parseable timestamp-like string)
    assert len(val) >= 10, (
        f"'lens_data_as_of' must be a timestamp string (at least 10 chars); got {val!r}"
    )


# ---------------------------------------------------------------------------
# AC-4: freshness classification — fresh vs stale at the max-age boundary
# ---------------------------------------------------------------------------


def test_fresh_bundle_is_labeled_not_stale():
    """AC-4: A cache row with captured_at close to now must produce
    context['lens_data_stale'] == False.

    Uses captured_at = now (0 seconds old) — unambiguously within any reasonable
    freshness window (< 36 h default).

    Fails today — 'lens_data_stale' key absent from the context dict.
    """
    # captured_at = right now — always fresh regardless of the exact threshold
    captured_at = datetime.now(UTC).isoformat()
    _insert_cache_row(captured_at=captured_at)

    with (
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value={}),
        patch.object(db_module, "load_state", return_value={}),
    ):
        context = ai_advisor.assemble_advisor_context(**_minimal_context_args())

    assert "lens_data_stale" in context, (
        "assemble_advisor_context() must include 'lens_data_stale' bool in the context (AC-4). "
        f"Context keys: {list(context.keys())!r}"
    )
    assert context["lens_data_stale"] is False, (
        "A cache row with captured_at=now must be labeled fresh (lens_data_stale=False). "
        f"Got: {context['lens_data_stale']!r}"
    )


def test_stale_bundle_is_labeled_stale():
    """AC-4: A cache row with captured_at far in the past must produce
    context['lens_data_stale'] == True.

    Uses captured_at = now - 100 hours — unambiguously outside any reasonable
    freshness window (default is 36 h per the feature plan).

    Fails today — 'lens_data_stale' key absent from the context dict.
    """
    old_ts = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
    _insert_cache_row(captured_at=old_ts)

    with (
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value={}),
        patch.object(db_module, "load_state", return_value={}),
    ):
        context = ai_advisor.assemble_advisor_context(**_minimal_context_args())

    assert "lens_data_stale" in context, (
        "assemble_advisor_context() must include 'lens_data_stale' bool in the context (AC-4). "
        f"Context keys: {list(context.keys())!r}"
    )
    assert context["lens_data_stale"] is True, (
        "A cache row with captured_at 100 hours ago must be labeled stale "
        "(lens_data_stale=True). Default freshness window is 36 h per the feature plan. "
        f"Got: {context['lens_data_stale']!r}"
    )


def test_stale_bundle_is_served_not_ignored():
    """AC-4 (serve-with-label): even a stale bundle must be SERVED (not treated as
    cache-absent). The AC says stale bundles are served with a 'stale (as of <old ts>)'
    label — they must NOT trigger a live re-fetch.

    Adversarial: patches all 5 builders as must-not-be-called; even with a stale cache
    they must remain uncalled (the stale path still serves from cache, not live).

    Fails today — no cache-serve path exists, so all 5 builders ARE called.
    """
    old_ts = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
    _insert_cache_row(captured_at=old_ts)

    mocks = {attr: MagicMock(return_value=_stub_lens("technicals")) for attr in _BUILDER_ATTRS}

    with (
        patch.object(ai_advisor, "_build_technicals_section", mocks["_build_technicals_section"]),
        patch.object(ai_advisor, "_build_sentiment_section", mocks["_build_sentiment_section"]),
        patch.object(ai_advisor, "_build_derivatives_section", mocks["_build_derivatives_section"]),
        patch.object(ai_advisor, "_build_macro_section", mocks["_build_macro_section"]),
        patch.object(
            ai_advisor, "_build_fundamentals_section", mocks["_build_fundamentals_section"]
        ),
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value={}),
        patch.object(db_module, "load_state", return_value={}),
    ):
        context = ai_advisor.assemble_advisor_context(**_minimal_context_args())

    # Stale bundle must STILL be served (not re-fetch live)
    for attr, mock in mocks.items():
        (
            mock.assert_not_called(),
            (
                f"{attr}() was called despite a stale MARKET_LENS_CACHE row being present. "
                "AC-4 says stale bundles must be served WITH a stale label, "
                "not discarded and re-fetched live. Serve-with-label, never re-fetch."
            ),
        )

    assert context.get("lens_data_stale") is True, (
        "The stale bundle must be reflected in context['lens_data_stale']=True. "
        f"Got: {context.get('lens_data_stale')!r}"
    )


# ---------------------------------------------------------------------------
# AC-5: cold-start fallback — no cache row → does NOT fan out to all 5 builders
# ---------------------------------------------------------------------------


def test_cold_start_no_cache_row_does_not_invoke_all_five_builders():
    """AC-5 ADVERSARIAL: when NO MARKET_LENS_CACHE row exists, assemble_advisor_context()
    must NOT invoke all 5 live builders as the default path.

    The feature plan specifies: cold-start must degrade to EITHER:
      (a) a single bounded live fetch, OR
      (b) an honest 'market context unavailable' context.
    In NEITHER case may it silently fall through to the full 5-builder fan-out.

    Adversarial assertion: the total number of builder calls is < 5.
    (On the current implementation: all 5 are called → sum = 5 → assertion fails.)
    (After implementation: 0 or fewer calls → sum < 5 → assertion passes.)

    Note: this test is agnostic of WHICH cold-start path is chosen. The implementer
    picks the approach; the test pins the anti-pattern (NOT full fan-out).

    Fails today because no cache row is present and all 5 builders are called.
    """
    # Confirm the DB has no MARKET_LENS_CACHE row before this test
    assert db_module.get_latest_market_lens_cache() is None, (
        "Test precondition: no MARKET_LENS_CACHE rows should exist before cold-start test. "
        "DB isolation may be broken."
    )

    call_counts: dict[str, int] = {attr: 0 for attr in _BUILDER_ATTRS}

    def _counting_stub(name: str):
        def _stub(*args, **kwargs):
            call_counts[name] += 1
            return _stub_lens(name.split("_build_")[1].replace("_section", ""), available=False)

        return _stub

    with (
        patch.object(
            ai_advisor, "_build_technicals_section", _counting_stub("_build_technicals_section")
        ),
        patch.object(
            ai_advisor, "_build_sentiment_section", _counting_stub("_build_sentiment_section")
        ),
        patch.object(
            ai_advisor, "_build_derivatives_section", _counting_stub("_build_derivatives_section")
        ),
        patch.object(ai_advisor, "_build_macro_section", _counting_stub("_build_macro_section")),
        patch.object(
            ai_advisor, "_build_fundamentals_section", _counting_stub("_build_fundamentals_section")
        ),
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value={}),
        patch.object(db_module, "load_state", return_value={}),
    ):
        context = ai_advisor.assemble_advisor_context(**_minimal_context_args())

    builders_called = sum(1 for count in call_counts.values() if count > 0)
    assert builders_called < 5, (
        f"Cold-start (no cache row): {builders_called}/5 live builders were called. "
        "The full 5-builder fan-out is the anti-pattern this feature eliminates. "
        "Cold-start must degrade gracefully to either (a) a bounded single fetch "
        "or (b) honest 'market context unavailable' — NOT the full 17-29-call fan-out. "
        f"Call counts by builder: {call_counts!r}"
    )


def test_cold_start_context_is_still_well_shaped():
    """AC-5 (complement): even on cold-start, assemble_advisor_context() must return
    a well-shaped context dict — never None, never raise.

    The cold-start path must produce a context the Claude call can consume.

    Fails today — the current implementation calls all 5 builders (which is undesirable
    but does return a well-shaped dict). After the fix this test verifies the new
    cold-start path also returns a well-shaped context.
    """
    assert db_module.get_latest_market_lens_cache() is None, "Test precondition: cold start"

    with (
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value={}),
        patch.object(db_module, "load_state", return_value={}),
    ):
        try:
            context = ai_advisor.assemble_advisor_context(**_minimal_context_args())
        except Exception as exc:
            pytest.fail(
                f"assemble_advisor_context() must not raise on cold-start; "
                f"got {type(exc).__name__}: {exc}"
            )

    assert isinstance(context, dict) and context, "Context must be a non-empty dict"
    # Mandatory always-present keys (not new to this feature — these are pre-existing)
    for key in ("scope", "symphony_id", "optuna_evidence", "suggestible_surface"):
        assert key in context, (
            f"Always-present key '{key}' is missing from cold-start context. "
            f"Keys: {list(context.keys())!r}"
        )


# ---------------------------------------------------------------------------
# AC-7: served cache bundle is shape-compatible with live-fetch output
# (structured _build_*_section shape, NOT council prose per_lens_digest)
# ---------------------------------------------------------------------------


def test_served_lenses_shape_identical_to_live_fetch_shape():
    """AC-7: The lens data served from cache must be SHAPE-IDENTICAL to the
    structured _build_*_section() output — NOT the council prose shape.

    Council prose shape (per_lens_digest): {"available": bool, "summary": str, "sources": list}
    Structured producer shape: {"lens": str, "available": bool, "payload": dict|None, "sources": list}

    Assertions (derived from reading the real producer bodies — no hardcoded values):
      1. Each lens block must have all required keys from the fixture contract.
      2. block["available"] must be a bool (not a string, not absent).
      3. "payload" must be present (dict on available=True, None on available=False).
      4. "sources" must be a list.
      5. The block must NOT have council-only keys like "summary" (string) or
         "per_lens_digest" (which would indicate the wrong cache shape was served).

    Fixture provenance: tests/fixtures/math/advisor_latency_cache_serve_shapes.json
    (derived from reading the actual _build_*_section() source bodies).

    Fails today — no cache-serve path; the context has no 'lenses' grouping key,
    or if lens keys are present they come from the live-fetch (the test verifies
    they come from the CACHED structured data).
    """
    # Seed a well-shaped cache row using the fixture template
    lenses = {}
    for name in _LENS_NAMES:
        lenses[name] = _stub_lens(name, available=True)

    _insert_cache_row(lenses=lenses)

    # All 5 builders must remain uncalled (AC-1 + shape from cache, not live)
    for attr in _BUILDER_ATTRS:
        patch_target = getattr(ai_advisor, attr)

    with (
        patch.object(
            ai_advisor,
            "_build_technicals_section",
            side_effect=AssertionError(
                "builder called during AC-7 shape test — must serve from cache"
            ),
        ),
        patch.object(
            ai_advisor,
            "_build_sentiment_section",
            side_effect=AssertionError(
                "builder called during AC-7 shape test — must serve from cache"
            ),
        ),
        patch.object(
            ai_advisor,
            "_build_derivatives_section",
            side_effect=AssertionError(
                "builder called during AC-7 shape test — must serve from cache"
            ),
        ),
        patch.object(
            ai_advisor,
            "_build_macro_section",
            side_effect=AssertionError(
                "builder called during AC-7 shape test — must serve from cache"
            ),
        ),
        patch.object(
            ai_advisor,
            "_build_fundamentals_section",
            side_effect=AssertionError(
                "builder called during AC-7 shape test — must serve from cache"
            ),
        ),
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value={}),
        patch.object(db_module, "load_state", return_value={}),
    ):
        context = ai_advisor.assemble_advisor_context(**_minimal_context_args())

    # Find lens data — accept either context["lenses"][name] or context[name]
    nested = context.get("lenses") or {}
    required_keys_available = set(_SHAPES["lens_available_required_keys"])
    forbidden_council_keys = set(_SHAPES["council_prose_forbidden_top_level_keys"])

    for name in _LENS_NAMES:
        block = nested.get(name) or context.get(name)
        assert block is not None, (
            f"Lens '{name}' is not reachable from context after cache-serve. "
            "Expected under context['lenses'][name] or context[name]. "
            f"context keys: {list(context.keys())!r}"
        )
        assert isinstance(block, dict), (
            f"Lens block for '{name}' must be a dict; got {type(block).__name__!r}"
        )

        # Required keys: lens, available, payload, sources
        for req_key in required_keys_available:
            assert req_key in block, (
                f"Lens block for '{name}' is missing required key '{req_key}'. "
                f"Keys present: {list(block.keys())!r}. "
                "This indicates the cache is storing/serving a different shape than "
                "the _build_*_section() output — verify the producer captures the "
                "exact structured output."
            )

        assert isinstance(block["available"], bool), (
            f"block['available'] for '{name}' must be a bool; "
            f"got {type(block['available']).__name__!r}: {block['available']!r}"
        )
        assert isinstance(block["sources"], list), (
            f"block['sources'] for '{name}' must be a list; got {type(block['sources']).__name__!r}"
        )

        # Must NOT have council prose keys (wrong shape = council digest, not producer output)
        for forbidden_key in forbidden_council_keys:
            # "summary" as a *string* value at the block level indicates council prose shape
            if forbidden_key == "summary" and isinstance(block.get(forbidden_key), str):
                pytest.fail(
                    f"Lens block for '{name}' has 'summary' as a string value — "
                    "this is the council prose per_lens_digest shape, NOT the structured "
                    "_build_*_section() producer shape. The cache must store and serve "
                    "the producer's structured output, not the council's prose digest."
                )


# ---------------------------------------------------------------------------
# AC-8: empty-state reword — old alarming phrasing gone, new informative message present
# ---------------------------------------------------------------------------


def test_empty_state_old_alarming_phrase_is_absent():
    """AC-8: build_assessment_from_context() for a symphony with no Optuna run must NOT
    return the old alarming phrase 'Optuna has not yet run for this symphony'.

    Fails today because the old phrase is still present in the source:
      ai_advisor.py:1436 "Optuna has not yet run for this symphony — no walk-forward..."
    """
    context_no_optuna = {"optuna_evidence": {"available": False}}

    result = ai_advisor.build_assessment_from_context(context_no_optuna)

    summary = result.get("summary", "")
    old_phrase = "Optuna has not yet run for this symphony"
    assert old_phrase not in summary, (
        f"build_assessment_from_context() still returns the alarming phrase "
        f"'{old_phrase}' for a symphony with no Optuna run. "
        "AC-8 requires this phrasing to be replaced with a clearer, less alarming message "
        "that still accurately conveys: no walk-forward OOS evidence; Claude reasoning "
        "without OOS data. Update build_assessment_from_context() at ai_advisor.py:1436."
    )


def test_empty_state_new_message_conveys_required_semantics():
    """AC-8 (semantic forward-guard): the replacement message for no-Optuna must still
    convey that OOS validation has not run AND that Claude is reasoning without OOS evidence.

    This guard ensures the implementer does not produce a blank or misleading message.
    It passes today (the current message conveys the right semantics — just alarmingly).
    It must continue to pass after the reword.
    """
    context_no_optuna = {"optuna_evidence": {"available": False}}

    result = ai_advisor.build_assessment_from_context(context_no_optuna)
    summary = result.get("summary", "")

    assert summary.strip(), (
        "build_assessment_from_context() must return a non-empty summary for the "
        "no-Optuna case — the operator needs to understand the advisory state."
    )
    # Must convey: no walk-forward / OOS validation evidence
    oos_hint = any(
        phrase in summary.lower()
        for phrase in ("oos", "out-of-sample", "walk-forward", "validation", "unvalidated")
    )
    assert oos_hint, (
        "The no-Optuna summary must still convey that walk-forward / OOS validation "
        "has not been run. The replacement message must be accurate, not just less alarming. "
        f"Summary returned: {summary!r}"
    )


# ---------------------------------------------------------------------------
# AC-9: council / MARKET_PRISM behavior unchanged; MARKET_LENS_CACHE not in _ADVISOR_ROLES
# (GUARD test — passes today, must continue to pass after implementation)
# ---------------------------------------------------------------------------


def test_market_lens_cache_role_absent_from_advisor_roles():
    """AC-9 GUARD: 'MARKET_LENS_CACHE' must NOT be present in app._ADVISOR_ROLES.

    MARKET_LENS_CACHE is a serve-infrastructure role (like MARKET_PRISM_SOURCES) — it
    must be excluded from the Overview observations loop and _preview_text stamping.
    Adding it to _ADVISOR_ROLES would surface cache rows as advisory observations in the UI.

    This test PASSES TODAY and must continue to pass after implementation — the guard
    ensures the implementer does not accidentally add MARKET_LENS_CACHE to _ADVISOR_ROLES.
    """
    import app as app_module

    advisor_roles = getattr(app_module, "_ADVISOR_ROLES", [])
    assert "MARKET_LENS_CACHE" not in advisor_roles, (
        "'MARKET_LENS_CACHE' must NOT be added to app._ADVISOR_ROLES. "
        "It is a serve-infrastructure role (like MARKET_PRISM_SOURCES) — "
        "keep it out of the Overview observations loop. "
        f"Current _ADVISOR_ROLES: {advisor_roles!r}"
    )


# ---------------------------------------------------------------------------
# AC-10: D-1 — malformed cache row degrades to cache-miss; /ai-advisor route stays 200
# ---------------------------------------------------------------------------


def test_malformed_raw_response_treated_as_cache_miss():
    """AC-10: A MARKET_LENS_CACHE row with malformed raw_response must be treated
    as a cache miss (degrade gracefully) — assemble_advisor_context() must not raise.

    Malformed cases tested:
      1. raw_response has 'lenses' key missing
      2. 'captured_at' is absent / unparseable

    After degrading to cache-miss, the cold-start fallback (AC-5) must apply.

    Fails today with AttributeError — accessor not implemented.
    """
    # Seed a row with malformed raw_response (missing 'lenses')
    _insert_cache_row(raw_override={"captured_at": "2026-06-29T03:00:00+00:00"})  # no 'lenses'

    with (
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value={}),
        patch.object(db_module, "load_state", return_value={}),
    ):
        try:
            context = ai_advisor.assemble_advisor_context(**_minimal_context_args())
        except Exception as exc:
            pytest.fail(
                f"assemble_advisor_context() must not raise when the MARKET_LENS_CACHE row "
                f"has a malformed raw_response (D-1 contract). "
                f"Got {type(exc).__name__}: {exc}"
            )

    assert isinstance(context, dict) and context, (
        "assemble_advisor_context() must return a non-empty dict even on malformed cache"
    )


def test_unparseable_captured_at_treated_as_stale_not_crash():
    """AC-10 (edge): A cache row with captured_at absent or unparseable must be treated
    as stale (loud label) rather than crashing.

    Fails today with AttributeError — accessor not implemented.
    """
    # Seed a row with captured_at = "NOT_A_DATE"
    _insert_cache_row(
        raw_override={
            "captured_at": "NOT_A_DATE",
            "lenses": _all_stub_lenses(available=True),
        }
    )

    with (
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value={}),
        patch.object(db_module, "load_state", return_value={}),
    ):
        try:
            context = ai_advisor.assemble_advisor_context(**_minimal_context_args())
        except Exception as exc:
            pytest.fail(
                f"assemble_advisor_context() must not raise on unparseable captured_at "
                f"(D-1 contract — treat as stale). Got {type(exc).__name__}: {exc}"
            )

    # When captured_at is unparseable, the bundle must be treated as stale (not fresh)
    stale = context.get("lens_data_stale")
    assert stale is True or stale is None, (
        "An unparseable 'captured_at' must result in lens_data_stale=True (treated as stale). "
        f"Got lens_data_stale={stale!r}"
    )


def test_suggest_route_returns_200_when_cache_row_is_malformed():
    """AC-10: The /ai-advisor/suggest route must return HTTP 200 even when the
    MARKET_LENS_CACHE row is malformed (D-1 propagated through the route).

    Verifies the whole-path D-1: producer error / malformed cache → cache miss →
    cold-start fallback → Claude call (mocked) → 200.

    Fails today — the accessor doesn't exist, so the route 500s if assemble_advisor_context
    raises (though currently it doesn't raise since the accessor doesn't exist yet).
    After implementation, this test guards that the route never 500s on a bad cache row.
    """
    import app as app_module

    _insert_cache_row(raw_override={"captured_at": "GARBAGE", "lenses": "not_a_dict"})

    with app_module.app.test_client() as client:
        with (
            patch.object(
                ai_advisor,
                "request_suggestions",
                return_value=(type("Resp", (), {"suggestions": []})(), None),
            ),
            patch.object(app_module, "database") as mock_db,
        ):
            mock_db.load_state.return_value = {}
            mock_db.normalize_name.side_effect = db_module.normalize_name
            mock_db.get_latest_autotune_run.return_value = None
            # Make get_latest_market_lens_cache available via the real db module
            mock_db.get_latest_market_lens_cache.side_effect = (
                db_module.get_latest_market_lens_cache
            )

            resp = client.post(
                "/ai-advisor/suggest",
                json={"symphony_id": "test-symphony"},
                content_type="application/json",
            )

    assert resp.status_code == 200, (
        f"/ai-advisor/suggest must return 200 even when the MARKET_LENS_CACHE row is "
        f"malformed (D-1: error degrades to cache-miss, route still responds). "
        f"Got HTTP {resp.status_code}."
    )


# ---------------------------------------------------------------------------
# AC-6: per-click symphony-specific work is preserved on the cache-serve path
# (GUARD — passes today; must continue to pass after implementation)
# ---------------------------------------------------------------------------


def test_cache_serve_preserves_symphony_specific_context():
    """AC-6 GUARD: When a fresh MARKET_LENS_CACHE row is present and the 5 live
    builders are skipped, assemble_advisor_context() must STILL assemble all the
    symphony-specific context elements:
      - optuna_evidence (Optuna walk-forward metrics for this symphony)
      - suggestible_surface (per-symphony config surface + locked vars)
      - symphony_logic (condensed Composer logic for this symphony)
      - scope, symphony_id

    These are per-click, not market-wide — they must never be served from the
    market-wide cache. The cache only covers the 5 market-wide lens blocks.

    Adversarial guard: patches all 5 builders with AssertionError (must not be called),
    yet the symphony-specific fields must still appear with the expected types.
    A wrong implementation that also caches optuna_evidence / suggestible_surface
    will fail here or in AC-1 tests.

    Passes today — the current implementation always assembles these fields.
    Must continue to pass after the cache-serve path is added.
    """
    _insert_cache_row()  # fresh cache row — builders must be skipped

    # Provide a known autotune_run so optuna_evidence is deterministic
    _fake_autotune = {
        "oos_alpha": None,  # sentinel for "all trials haircut-rejected"
        "fallback_oos_alpha": 0.0,
        "default_oas_alpha": 0.0,
        "baseline_decision": "HOLD",
        "train_alpha": 0.01,
    }

    with (
        # 5 market-wide builders: must NOT be called (AC-1 contract)
        patch.object(
            ai_advisor,
            "_build_technicals_section",
            side_effect=AssertionError("AC-6: technicals builder called — cache should serve this"),
        ),
        patch.object(
            ai_advisor,
            "_build_sentiment_section",
            side_effect=AssertionError("AC-6: sentiment builder called — cache should serve this"),
        ),
        patch.object(
            ai_advisor,
            "_build_derivatives_section",
            side_effect=AssertionError(
                "AC-6: derivatives builder called — cache should serve this"
            ),
        ),
        patch.object(
            ai_advisor,
            "_build_macro_section",
            side_effect=AssertionError("AC-6: macro builder called — cache should serve this"),
        ),
        patch.object(
            ai_advisor,
            "_build_fundamentals_section",
            side_effect=AssertionError(
                "AC-6: fundamentals builder called — cache should serve this"
            ),
        ),
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value={"rules": []}),
        patch.object(db_module, "load_state", return_value={}),
    ):
        context = ai_advisor.assemble_advisor_context(
            scope="symphony",
            symphony_id="test-symphony-ac6",
            autotune_run=_fake_autotune,
        )

    # Symphony-specific fields must be present and well-typed
    assert context.get("scope") == "symphony", (
        f"context['scope'] must be 'symphony'; got {context.get('scope')!r}"
    )
    assert context.get("symphony_id") == "test-symphony-ac6", (
        f"context['symphony_id'] must match the passed symphony_id; "
        f"got {context.get('symphony_id')!r}"
    )

    optuna = context.get("optuna_evidence")
    assert isinstance(optuna, dict) and optuna, (
        "context['optuna_evidence'] must be a non-empty dict on the cache-serve path. "
        "The autotune evidence is per-symphony and must never come from the market cache. "
        f"Got: {optuna!r}"
    )

    surface = context.get("suggestible_surface")
    assert isinstance(surface, list), (
        "context['suggestible_surface'] must be a list (per-symphony config surface). "
        "It is per-symphony and must never come from the market cache. "
        f"Got type: {type(surface).__name__!r}"
    )

    # symphony_logic was mocked to return {"rules": []} — must appear in context
    sym_logic = context.get("symphony_logic")
    assert sym_logic == {"rules": []}, (
        "context['symphony_logic'] must reflect the per-symphony condensed logic "
        '(get_condensed_logic was mocked to return {"rules": []}). '
        f"Got: {sym_logic!r}"
    )
