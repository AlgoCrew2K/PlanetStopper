"""advisor-fix cycle — RED: the AI Advisor shows IDENTICAL output for every symphony.

Root cause (forensically confirmed against the live alphabot_state.db, 2026-06-04):
  Overfitting Conscience is the ONLY genuinely per-symphony producer, but it wrote
  ZERO rows across 1678 autotune_runs.  Its persistence wrapper
  ``run_overfitting_conscience`` THROWS at runtime and the exception is SILENTLY
  swallowed at autotuner.py:2620-2622 (try/except -> logging.warning).  The throw is

      TypeError: '>' not supported between instances of 'NoneType' and 'int'

  raised at overfitting_conscience.py:105 (``s = max(s, s_count)``) because the live
  autotune_runs rows have ``s_count = None`` (the column was added by a later
  migration; the pre-existing rows are NULL).  ``compute_overfitting_conscience_observation``
  binds ``s_count: int = autotune_run["s_count"]`` (= None) then ``max(s, None)`` throws.

This file owns AC-1 (OC persists a genuine DISTINCT per-symphony observation; the
None-s_count input no longer throws) and AC-2 (the KILLER INVARIANT: two different
symphonies yield DIFFERENT advisor output).  The producer-failure surfacing contract
(AC-3 backend) lives in tests/autotuner/test_advisor_failure_not_silently_swallowed.py.

Provenance:
  tests/fixtures/math/advisor_identical_output_ground_truth.json — captured READ-ONLY
  from the live DB.  Carries identity/key data + the null-s_count SHAPE only; NO
  producer-computed verdicts/ratios/dollars (per feedback_no_hardcoded_test_values).
  Verdicts are asserted as ENUM membership and DISTINCTNESS, never against hardcoded
  values.

Mocking strategy:
  * The math/producer engine is NEVER mocked — we test it directly.
  * database.insert_advisor_observation is the only seam patched (to capture the
    persisted payload without a live write) in the persistence-contract tests.
  * No module-level mutable state; every fixture is function-scoped.
"""

from __future__ import annotations

import importlib
import json
import pathlib
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_VERDICT_ENUM = {"CLEAR", "WATCH", "BREACH"}


def _ground_truth() -> dict:
    """Return the live-captured identity/shape fixture for the advisor-fix cycle."""
    fixture_path = (
        pathlib.Path(__file__).parents[1]
        / "fixtures"
        / "math"
        / "advisor_identical_output_ground_truth.json"
    )
    return json.loads(fixture_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="function")
def ground_truth() -> dict:
    return _ground_truth()


@pytest.fixture(scope="function")
def oc():
    """Import the Overfitting Conscience producer fresh each test."""
    import advisors.overfitting_conscience as module
    return importlib.reload(module)


def _run_from_fixture(row: dict) -> dict:
    """Project the fixture's autotune_runs row into the dict OC consumes.

    Returns a NEW dict each call (never a shared mutable) so tests cannot leak
    state through it.
    """
    return {
        "id": row["id"],
        "symphony_id": row["symphony_id"],
        "spec_bundle_id": row["spec_bundle_id"],
        "n_effective": row["n_effective"],
        "s_count": row["s_count"],  # None on the live pre-migration rows
    }


# ===========================================================================
# AC-1 — OC pure compute must NOT throw on the live None-s_count input.
# ===========================================================================


def test_oc_compute_does_not_throw_on_null_s_count(oc, ground_truth):
    """compute_overfitting_conscience_observation MUST tolerate s_count=None.

    This is the exact swallowed exception: the live autotune_runs rows carry
    s_count=None, and ``s = max(s, s_count)`` raises
    ``TypeError: '>' not supported between instances of 'NoneType' and 'int'``.
    The fix is to coerce a missing/None s_count to 0 (no BACKTEST_SELECTION
    counted) at the source so the pure compute succeeds and a row can persist.

    Asserting on enum membership only — NO hardcoded verdict.  A naive
    implementation that hardcodes a verdict to dodge the throw would still have
    to satisfy the distinctness invariant in AC-2.
    """
    row = ground_truth["autotune_runs_with_null_s_count"][0]
    assert row["s_count"] is None, (
        "Fixture drift: the AC-1 ground-truth row must carry s_count=None — "
        "that is the live shape that triggers the swallowed TypeError."
    )

    # This call raises TypeError today (overfitting_conscience.py:105).
    obs = oc.compute_overfitting_conscience_observation(_run_from_fixture(row), [])

    assert obs["advisor_role"] == "OVERFITTING_CONSCIENCE"
    assert obs["subject_type"] == "autotune_run"
    assert obs["subject_id"] == str(row["id"]), (
        "subject_id must be the stringified autotune_runs.id from the input row, "
        "derived from the fixture — never a hardcoded sentinel."
    )
    assert obs["verdict"] in _VERDICT_ENUM, (
        f"verdict must be one of {_VERDICT_ENUM}; got {obs['verdict']!r}."
    )
    assert obs["is_advisory_only"] == 1


def test_oc_compute_treats_null_s_count_same_as_zero(oc, ground_truth):
    """A None s_count must behave identically to s_count=0 (no selection counted).

    Pins the SEMANTICS of the fix (coerce None -> 0), not just no-throw: the
    observation produced from a None-s_count row must equal the observation
    produced from the same row with s_count=0 (same id/symphony, no ledger rows).
    Prevents a fix that silently invents a non-zero S from None.
    """
    row = ground_truth["autotune_runs_with_null_s_count"][0]

    run_none = _run_from_fixture(row)            # s_count = None
    run_zero = _run_from_fixture(row)
    run_zero["s_count"] = 0                       # explicit zero

    obs_none = oc.compute_overfitting_conscience_observation(run_none, [])
    obs_zero = oc.compute_overfitting_conscience_observation(run_zero, [])

    assert obs_none["verdict"] == obs_zero["verdict"], (
        "None s_count must be treated as 0 (no BACKTEST_SELECTION). "
        f"Got None->{obs_none['verdict']!r} vs 0->{obs_zero['verdict']!r}."
    )
    assert obs_none["raw_response"] == obs_zero["raw_response"], (
        "None s_count and explicit 0 must yield byte-identical raw_response — "
        "the fix must coerce, not fabricate a different S."
    )


# ===========================================================================
# AC-1 — OC persistence wrapper must actually WRITE a row (no swallowed throw).
# ===========================================================================


def test_run_overfitting_conscience_persists_a_row_on_null_s_count(oc, ground_truth):
    """run_overfitting_conscience MUST persist (call insert_advisor_observation)
    even when the input row has s_count=None.

    Today it throws before reaching insert_advisor_observation, which is why the
    live DB has ZERO OVERFITTING_CONSCIENCE rows.  We patch insert_advisor_observation
    to capture the call WITHOUT a live write, and assert it WAS called with the
    canonical symphony_id (the normalized name from the input row).
    """
    row = ground_truth["autotune_runs_with_null_s_count"][0]
    run = _run_from_fixture(row)

    captured: dict = {"called": False, "kwargs": None}

    def _capture(**kwargs):
        captured["called"] = True
        captured["kwargs"] = kwargs
        return 4242  # arbitrary row id

    with patch("database.insert_advisor_observation", side_effect=_capture):
        returned_id = oc.run_overfitting_conscience(run, [])

    assert captured["called"], (
        "run_overfitting_conscience did NOT reach insert_advisor_observation — "
        "the None-s_count TypeError is still thrown before persistence. This is "
        "the root cause of the ZERO-OC-rows / identical-advisor bug."
    )
    assert returned_id == 4242
    kw = captured["kwargs"]
    assert kw["advisor_role"] == "OVERFITTING_CONSCIENCE"
    # AC-4 cross-cut: the persisted symphony_id MUST be the canonical normalized name.
    assert kw["symphony_id"] == row["symphony_id"], (
        f"Persisted symphony_id={kw['symphony_id']!r} must equal the canonical "
        f"normalized name {row['symphony_id']!r} (the key the modal route queries)."
    )


# ===========================================================================
# AC-2 — KILLER INVARIANT: two different symphonies => DIFFERENT advisor output.
# ===========================================================================


def test_two_symphonies_yield_distinct_oc_observations(oc, ground_truth):
    """Two DIFFERENT symphonies must produce DISTINCT OC observations.

    This is the invariant the shipped bug violated: every symphony showed
    identical output.  We feed two different live symphony rows (different id +
    different symphony_id) and assert the persisted/returned observation DIFFERS
    in its symphony-identifying content.

    We assert DISTINCTNESS, not specific values — a correct implementation makes
    these differ via subject_id/symphony_id at minimum; we additionally require
    the two observations not be byte-identical as whole dicts.
    """
    rows = ground_truth["autotune_runs_with_null_s_count"]
    assert len(rows) >= 2, "Need >=2 live symphony rows for the distinctness invariant."
    row_a, row_b = rows[0], rows[1]
    assert row_a["symphony_id"] != row_b["symphony_id"], (
        "Fixture drift: the two AC-2 rows must be different symphonies."
    )

    obs_a = oc.compute_overfitting_conscience_observation(_run_from_fixture(row_a), [])
    obs_b = oc.compute_overfitting_conscience_observation(_run_from_fixture(row_b), [])

    # subject_id is derived from the (distinct) run ids -> must differ.
    assert obs_a["subject_id"] != obs_b["subject_id"], (
        "Two different autotune_runs rows produced the SAME subject_id — the "
        "observations are not symphony-distinct. This is the identical-output bug."
    )
    # The full observation dicts must not be byte-identical.
    assert obs_a != obs_b, (
        "Two different symphonies produced byte-identical OC observations. "
        "AC-2 (the killer invariant) requires per-symphony distinctness."
    )


def test_two_symphonies_persist_distinct_symphony_ids(oc, ground_truth):
    """End-to-end persistence: two symphonies must persist under DISTINCT
    canonical symphony_ids (so the modal/route can tell them apart).

    Captures the symphony_id passed to insert_advisor_observation for each and
    asserts they differ and each equals the row's normalized name.
    """
    rows = ground_truth["autotune_runs_with_null_s_count"]
    row_a, row_b = rows[0], rows[1]

    seen: list[str] = []

    def _capture(**kwargs):
        seen.append(kwargs.get("symphony_id"))
        return len(seen)

    with patch("database.insert_advisor_observation", side_effect=_capture):
        oc.run_overfitting_conscience(_run_from_fixture(row_a), [])
        oc.run_overfitting_conscience(_run_from_fixture(row_b), [])

    assert seen == [row_a["symphony_id"], row_b["symphony_id"]], (
        f"Persisted symphony_ids {seen!r} must be the two distinct canonical "
        f"normalized names {[row_a['symphony_id'], row_b['symphony_id']]!r}."
    )
    assert seen[0] != seen[1], (
        "Both symphonies persisted under the SAME symphony_id — the modal route "
        "cannot disambiguate them. Identical-output bug at the key level."
    )
