"""RED tests — R2-1 AC-1/AC-2/AC-3/AC-9: the reasoning-context assembler.

Module under test: ``ai_advisor.build_reasoning_context`` (engine contract per
r2-engine, feature-plans/advisor-r2-1-context-provenance.md).

Contract pinned by these tests (test-writer defines it; r2-engine implements
to match — confirmed via SendMessage before this file was written):

    build_reasoning_context(symphony_id, objective, *, composer_symphony_id=None)
        -> tuple[str, dict]

    Returns (prompt_context, manifest):
      - prompt_context: str. "" when nothing is injectable (no symphony_id).
        Otherwise a bounded, human-readable text block built from
        symphony_schema.render_rules_text (never a raw JSON tree dump).
      - manifest: dict with keys
          "tree": "present" | "absent"
          "stats": "present" | "absent"   (mirrors context["optuna_evidence"]["available"]
                                            from assemble_advisor_context — the "live stats"
                                            AC-2 refers to)
          "technicals" / "sentiment" / "derivatives" / "macro" / "fundamentals":
              "available" | "stale" | "absent"
              (derived by combining each lens's assemble_advisor_context "available"
              flag with the bundle-wide staleness classifier — never a new classifier)
      - symphony_id falsy -> ("", _EMPTY_MANIFEST), ZERO I/O (no DB read, no
        Composer call) — AC-3's honest-degradation floor.
      - D-1: never raises, under any failure mode.

Fixture provenance (Gate-1): real captured Composer /score trees already in
the repo — tests/fixtures/symphony_logic/sample_score_small.json (132 KB,
57 real tickers incl. UVIX/TQQQ/BTAL, deeply nested if/filter/group). NOT
hand-invented. Reused verbatim.

Lens-cache seeding mirrors the exact idiom from
tests/ai_advisor/test_advisor_latency_cache_serve.py's ``_insert_cache_row``
(writes via database.insert_advisor_observation — the real producer write
path — never a raw INSERT).

No live network. LLM client seam is irrelevant to this file (build_reasoning_context
does not call the SDK) but the Composer-tree-fetch seam (symphony_logic.fetch_symphony_score)
and the DB are exercised directly against the isolated per-test SQLite DB.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

import ai_advisor
import database as db_module

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REAL_TREE_FIXTURE = (
    _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
)
_LENS_NAMES = ("technicals", "sentiment", "derivatives", "macro", "fundamentals")

# Two real tickers confirmed present in the fixture tree (asset-node leaves) — used as
# positive markers that the REAL tree (not a placeholder) was rendered into the prompt.
_KNOWN_TICKERS = ("UVIX", "TQQQ")


def _load_real_tree() -> dict:
    with open(_REAL_TREE_FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def _stub_lens(name: str, *, available: bool = True) -> dict:
    """Matches the real _build_*_section() producer shape (same shape the
    lens-cache-serve tests use) — {"lens", "available", "payload", "sources"}."""
    if available:
        return {"lens": name, "available": True, "payload": {name: "stub"}, "sources": []}
    return {
        "lens": name,
        "available": False,
        "reason": "stub-unavailable",
        "payload": None,
        "sources": [],
    }


def _all_stub_lenses(*, available: bool = True) -> dict:
    return {name: _stub_lens(name, available=available) for name in _LENS_NAMES}


def _insert_lens_cache_row(*, lenses: dict | None = None, captured_at: str | None = None) -> int:
    """Seed a MARKET_LENS_CACHE row via the real producer write path — mirrors
    tests/ai_advisor/test_advisor_latency_cache_serve.py::_insert_cache_row exactly."""
    if lenses is None:
        lenses = _all_stub_lenses(available=True)
    if captured_at is None:
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


def _brc():
    """Resolve build_reasoning_context — AttributeError today is the expected RED."""
    fn = getattr(ai_advisor, "build_reasoning_context", None)
    if fn is None:
        raise AttributeError(
            "ai_advisor.build_reasoning_context does not exist yet — "
            "expected RED until R2-1 GREEN lands"
        )
    return fn


# ===========================================================================
# AC-1: real tree injected (rendered, not a raw JSON dump).
# ===========================================================================


def test_real_tree_markers_present_when_symphony_scoped():
    """AC-1: a resolvable symphony_id -> the real fixture tree's tickers appear
    in the rendered prompt_context, and the manifest reports tree='present'."""
    brc = _brc()
    real_tree = _load_real_tree()

    with (
        patch.object(ai_advisor.symphony_logic, "fetch_symphony_score", return_value=real_tree),
        patch.object(db_module, "get_latest_autotune_run", return_value=None),
        patch.object(db_module, "load_state", return_value={}),
    ):
        prompt_context, manifest = brc("sym-test-001", "diversify", composer_symphony_id="hash-abc")

    assert manifest.get("tree") == "present", (
        f"AC-1 GAP: manifest['tree'] must be 'present' when fetch_symphony_score returns "
        f"a real tree. Got manifest={manifest!r}"
    )
    for ticker in _KNOWN_TICKERS:
        assert ticker in prompt_context, (
            f"AC-1 GAP: known real-tree ticker {ticker!r} is absent from prompt_context — "
            "the real fixture tree was not rendered into the injected context."
        )


def test_real_tree_rendered_via_render_rules_text_not_raw_json_dump():
    """AC-1 (ADVERSARIAL): the injected tree text must be the deterministic
    render_rules_text() output, never a raw json.dumps(tree) — a raw dump would
    both blow the token budget (AC-9) and expose internal Composer node ids/uuids
    that a human-readable render never emits."""
    brc = _brc()
    real_tree = _load_real_tree()

    with (
        patch.object(ai_advisor.symphony_logic, "fetch_symphony_score", return_value=real_tree),
        patch.object(db_module, "get_latest_autotune_run", return_value=None),
        patch.object(db_module, "load_state", return_value={}),
    ):
        prompt_context, _manifest = brc(
            "sym-test-001", "diversify", composer_symphony_id="hash-abc"
        )

    assert '"children":' not in prompt_context, (
        "AC-1 GAP: prompt_context contains a raw JSON 'children' key — the tree was "
        "dumped as JSON instead of rendered via symphony_schema.render_rules_text(). "
        "A raw dump is both a token-budget violation (AC-9) and an internal-id leak."
    )
    # A node-id (uuid4) from the fixture must never leak into the prompt either —
    # spot-check one real node id from the fixture tree.
    assert "60f18ffa-403a-471e-8049-8edb0fb34048" not in prompt_context, (
        "AC-1 GAP: a raw Composer node uuid leaked into prompt_context — proves a "
        "structural (not rendered) representation was injected."
    )


# ===========================================================================
# AC-2: live stats + the 5 lens blocks injected from the cache-serve path.
# ===========================================================================


def test_lens_and_stats_markers_present_when_cache_fresh_and_optuna_available():
    """AC-2: a fresh 5-lens cache bundle + an available optuna run ->
    manifest reports every lens 'available' and stats 'present'; the prompt
    text carries lens-derived content (shape/presence only — no producer-
    computed numeric value asserted, per project hard rule)."""
    brc = _brc()
    _insert_lens_cache_row()  # fresh, all 5 lenses available=True

    fake_autotune_run = {
        "run_timestamp": "2026-07-01T00:00:00+00:00",
        "train_alpha": 0.02,
        "oos_alpha": 0.01,
        "fallback_oos_alpha": 0.0,
        "default_oas_alpha": 0.0,
        "baseline_decision": "HOLD",
    }

    with (
        patch.object(ai_advisor.symphony_logic, "fetch_symphony_score", return_value={}),
        patch.object(db_module, "get_latest_autotune_run", return_value=fake_autotune_run),
        patch.object(db_module, "load_state", return_value={}),
    ):
        prompt_context, manifest = brc("sym-test-002", "diversify", composer_symphony_id="hash-abc")

    for name in _LENS_NAMES:
        assert manifest.get(name) == "available", (
            f"AC-2 GAP: manifest[{name!r}] must be 'available' with a fresh cache bundle. "
            f"Got manifest={manifest!r}"
        )
    assert manifest.get("stats") == "present", (
        f"AC-2 GAP: manifest['stats'] must be 'present' when an autotune run exists. "
        f"Got manifest={manifest!r}"
    )
    assert prompt_context.strip(), (
        "AC-2 GAP: prompt_context must be non-empty when stats/lenses are injected."
    )


# ===========================================================================
# AC-3: honest degradation — never fabricated context, D-1 never raises.
# ===========================================================================


def test_no_symphony_id_yields_empty_context_and_absent_manifest_with_zero_io():
    """AC-3: falsy symphony_id -> ("", manifest-all-absent), and ZERO I/O —
    no Composer fetch, no DB read. Proves the from-scratch path never even
    attempts to assemble reasoning context (matches AC-8's byte-preservation
    requirement one layer up)."""
    brc = _brc()

    with (
        patch.object(ai_advisor.symphony_logic, "fetch_symphony_score") as mock_fetch,
        patch.object(db_module, "get_latest_autotune_run") as mock_autotune,
        patch.object(db_module, "get_latest_market_lens_cache") as mock_lens_cache,
    ):
        prompt_context, manifest = brc("", "diversify")

    assert prompt_context == "", (
        f"AC-3 GAP: falsy symphony_id must yield prompt_context=='', got {prompt_context!r}"
    )
    assert manifest.get("tree") == "absent", f"AC-3 GAP: manifest={manifest!r}"
    mock_fetch.assert_not_called()
    mock_autotune.assert_not_called()
    mock_lens_cache.assert_not_called()


def test_tree_fetch_failure_degrades_to_absent_never_fabricates():
    """AC-3: fetch_symphony_score returning {} (its own D-1 contract on transport
    failure — see symphony_logic.py:35-58) -> manifest['tree']=='absent', and no
    placeholder/fabricated tree text is injected. Must not raise."""
    brc = _brc()

    with (
        patch.object(ai_advisor.symphony_logic, "fetch_symphony_score", return_value={}),
        patch.object(db_module, "get_latest_autotune_run", return_value=None),
        patch.object(db_module, "load_state", return_value={}),
    ):
        try:
            prompt_context, manifest = brc(
                "sym-test-003", "diversify", composer_symphony_id="hash-x"
            )
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"AC-3 GAP (D-1): build_reasoning_context must never raise on a tree-fetch "
                f"failure. Got {type(exc).__name__}: {exc}"
            )

    assert manifest.get("tree") == "absent", f"AC-3 GAP: manifest={manifest!r}"
    for known in _KNOWN_TICKERS:
        assert known not in prompt_context, (
            f"AC-3 GAP: fabricated/stale ticker {known!r} present in prompt_context despite "
            "a tree-fetch failure — no fabricated context is allowed."
        )


def test_tree_fetch_raises_degrades_to_absent_never_raises():
    """AC-3 (D-1, adversarial): fetch_symphony_score itself RAISING (not just
    returning {}) must still degrade cleanly — build_reasoning_context's own
    D-1 contract must not depend on its collaborator's D-1 contract holding."""
    brc = _brc()

    with (
        patch.object(
            ai_advisor.symphony_logic,
            "fetch_symphony_score",
            side_effect=RuntimeError("simulated transport failure"),
        ),
        patch.object(db_module, "get_latest_autotune_run", return_value=None),
        patch.object(db_module, "load_state", return_value={}),
    ):
        try:
            prompt_context, manifest = brc(
                "sym-test-004", "diversify", composer_symphony_id="hash-x"
            )
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"AC-3 GAP (D-1): build_reasoning_context must never raise even when its own "
                f"tree-fetch COLLABORATOR raises. Got {type(exc).__name__}: {exc}"
            )

    assert manifest.get("tree") == "absent", f"AC-3 GAP: manifest={manifest!r}"


@pytest.mark.parametrize(
    "captured_at_delta_hours,expected_state",
    [
        (0, "available"),
        (100, "stale"),
    ],
)
def test_lens_cache_freshness_reflected_per_lens_in_manifest(
    captured_at_delta_hours, expected_state
):
    """AC-3: a fresh bundle -> per-lens 'available'; a stale bundle (captured_at
    100h old, well past the existing 36h freshness window) -> per-lens 'stale'.
    Reuses assemble_advisor_context's EXISTING staleness classifier — no new
    one is invented."""
    brc = _brc()
    captured_at = (datetime.now(UTC) - timedelta(hours=captured_at_delta_hours)).isoformat()
    _insert_lens_cache_row(captured_at=captured_at)

    with (
        patch.object(ai_advisor.symphony_logic, "fetch_symphony_score", return_value={}),
        patch.object(db_module, "get_latest_autotune_run", return_value=None),
        patch.object(db_module, "load_state", return_value={}),
    ):
        _prompt_context, manifest = brc("sym-test-005", "diversify", composer_symphony_id="hash-x")

    for name in _LENS_NAMES:
        assert manifest.get(name) == expected_state, (
            f"AC-3 GAP: with captured_at {captured_at_delta_hours}h old, manifest[{name!r}] "
            f"must be {expected_state!r}. Got manifest={manifest!r}"
        )


def test_lens_cache_cold_start_reflected_as_absent_per_lens():
    """AC-3: no MARKET_LENS_CACHE row at all (cold start) -> every lens is
    'absent' in the manifest — never silently reported 'available'."""
    brc = _brc()
    assert db_module.get_latest_market_lens_cache() is None, (
        "Test precondition: no MARKET_LENS_CACHE row should exist (DB isolation check)."
    )

    with (
        patch.object(ai_advisor.symphony_logic, "fetch_symphony_score", return_value={}),
        patch.object(db_module, "get_latest_autotune_run", return_value=None),
        patch.object(db_module, "load_state", return_value={}),
    ):
        _prompt_context, manifest = brc("sym-test-006", "diversify", composer_symphony_id="hash-x")

    for name in _LENS_NAMES:
        assert manifest.get(name) == "absent", (
            f"AC-3 GAP: cold-start (no cache row) must yield manifest[{name!r}]=='absent'. "
            f"Got manifest={manifest!r}"
        )


def test_stats_absent_when_no_autotune_run():
    """AC-3: no Optuna run for this symphony -> manifest['stats']=='absent',
    never fabricated as present."""
    brc = _brc()

    with (
        patch.object(ai_advisor.symphony_logic, "fetch_symphony_score", return_value={}),
        patch.object(db_module, "get_latest_autotune_run", return_value=None),
        patch.object(db_module, "load_state", return_value={}),
    ):
        _prompt_context, manifest = brc("sym-test-007", "diversify", composer_symphony_id="hash-x")

    assert manifest.get("stats") == "absent", f"AC-3 GAP: manifest={manifest!r}"


# ===========================================================================
# AC-9: bounded prompt — a large real tree cannot blow the generation token budget.
# ===========================================================================


def test_oversized_real_tree_render_is_length_bounded():
    """AC-9: the real fixture tree renders to 70k+ chars unbounded — injecting
    that raw would alone exceed a reasonable share of MAX_OUTPUT_TOKENS=16384
    (~4 chars/token -> ~65k chars). build_reasoning_context must cap the
    injected tree text at a documented, discoverable bound (a named module
    constant — never a magic inline number, per project hard rule 'no magic
    numbers')."""
    brc = _brc()
    real_tree = _load_real_tree()

    with (
        patch.object(ai_advisor.symphony_logic, "fetch_symphony_score", return_value=real_tree),
        patch.object(db_module, "get_latest_autotune_run", return_value=None),
        patch.object(db_module, "load_state", return_value={}),
    ):
        prompt_context, manifest = brc("sym-test-008", "diversify", composer_symphony_id="hash-x")

    assert manifest.get("tree") == "present", f"AC-9 precondition: manifest={manifest!r}"

    bound = getattr(ai_advisor, "MAX_REASONING_TREE_RENDER_CHARS", None)
    assert bound is not None and isinstance(bound, int) and bound > 0, (
        "AC-9 GAP: no discoverable named bound constant found on ai_advisor "
        "(expected e.g. ai_advisor.MAX_REASONING_TREE_RENDER_CHARS). The render must be "
        "capped by a named constant, never an inline magic number."
    )
    assert len(prompt_context) <= bound + 2000, (
        f"AC-9 GAP: prompt_context is {len(prompt_context)} chars, which exceeds the "
        f"documented bound ({bound}) by more than a small structural-text allowance. "
        "The oversized real tree (70k+ chars unbounded) must be truncated/summarized."
    )
    # And meaningfully smaller than the raw unbounded render — proves truncation
    # actually happened, not just a generous bound that never bites.
    from advisors import symphony_schema

    unbounded_len = len(symphony_schema.render_rules_text(real_tree))
    assert len(prompt_context) < unbounded_len, (
        f"AC-9 GAP: prompt_context ({len(prompt_context)} chars) is not shorter than the "
        f"unbounded render ({unbounded_len} chars) — no truncation occurred for an "
        "oversized real tree."
    )
