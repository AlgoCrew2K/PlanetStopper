"""RED tests -- AC-2: explainer runs at producer time, persisted.

feature-plans/retirement-approval-lifecycle.md AC-2: the 03:45 producer
(app._retirement_recommender_tick_worker) calls advisors.retirement_explainer.
explain_recommendation per recommendation AFTER build_recommendations() and
BEFORE persist_recommendations(), stamping `explanation` (str | None) into
each rec's raw_response dict. The LLM never runs on the render path or the
approve/checklist path. advisors/retirement_recommender.py stays LLM-free --
pinned via a golden sha256 hash (this cycle's diff to that file MUST be zero).

Lazy-import mocking note: _retirement_recommender_tick_worker does
`from advisors.retirement_recommender import (build_recommendations,
persist_recommendations)` INSIDE the function body (CC-2) -- patching the
MODULE-level attributes (advisors.retirement_recommender.build_recommendations
etc.) before calling the worker is picked up correctly, since a `from X
import Y` executes at call time and reads Y off X's current namespace. Same
pattern applies to the explainer import this cycle adds.

Expected state: RED until app.py's _retirement_recommender_tick_worker is
extended to call advisors.retirement_explainer.explain_recommendation.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib
from unittest.mock import patch

import pytest

import app as app_module

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Golden immutability pin (NOT a producer-computed value -- a file-integrity
# guard, same convention as this codebase's other "byte-preserved, pinned
# against a golden fixture" tests): advisors/retirement_recommender.py must
# carry ZERO diff for this cycle (AC-2: "stays LLM-free"). Computed against
# the file as it exists at RED-phase authoring time (Cycle-2a, already
# shipped) -- if this hash mismatches, retirement_recommender.py was touched
# this cycle, which AC-2 forbids.
#
# CI-caught false failure (2026-08-26, root-caused by team-lead): this used
# to hash RAW file bytes, which differ by line-ending convention alone --
# CRLF on a Windows checkout (this repo's default git config) vs LF on CI's
# Linux checkout. `git diff 1c449941 HEAD -- advisors/retirement_recommender.py`
# is genuinely empty (confirmed) -- the file IS byte-unchanged in the sense
# that matters (no real edit), but a raw-byte hash is not line-ending-stable
# across platforms. Fixed: hash the LF-NORMALIZED content instead (CRLF ->
# LF before hashing) via _lf_normalized_sha256, so the guard is platform-
# independent while still catching any REAL content modification (the
# normalization only collapses \r\n -> \n, it does not touch anything else).
_RETIREMENT_RECOMMENDER_GOLDEN_SHA256 = (
    "b8ffb354e584a42ffbfc84f3d4697f4e221dab48210dba0f67741f1ad878cad6"
)


def _lf_normalized_sha256(path: pathlib.Path) -> str:
    """sha256 of `path`'s content with CRLF normalized to LF first -- makes
    the golden-hash pin stable across a Windows (CRLF) checkout and a Linux
    CI (LF) checkout of the identical logical file content."""
    raw = path.read_bytes()
    normalized = raw.replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _sample_recs() -> list[dict]:
    return [
        {"candidate_id": "sym-a", "sibling_id": "sym-b", "correlation": 0.8},
        {"candidate_id": "sym-c", "sibling_id": "sym-d", "correlation": 0.75},
    ]


# ===========================================================================
# AC-2 core: retirement_recommender.py stays byte-unchanged (LLM-free)
# ===========================================================================


def test_retirement_recommender_module_is_byte_unchanged_this_cycle():
    """AC-2: 'advisors/retirement_recommender.py stays LLM-free (the
    explainer call lives in the producer orchestration, not in
    build_recommendations)' -- enforced as a hard zero-diff pin, not just a
    prose promise."""
    path = REPO_ROOT / "advisors" / "retirement_recommender.py"
    assert path.exists(), (
        "advisors/retirement_recommender.py must exist (Cycle-2a, already shipped)."
    )
    actual_hash = _lf_normalized_sha256(path)
    assert actual_hash == _RETIREMENT_RECOMMENDER_GOLDEN_SHA256, (
        "advisors/retirement_recommender.py was modified this cycle -- AC-2 requires "
        "it to stay byte-unchanged (LLM-free); the explainer wiring belongs in "
        "app.py's producer orchestration only, never inside build_recommendations."
    )


class TestLfNormalizedSha256GuardIntentPreserved:
    """The CRLF-normalization fix (2026-08-26) must only neutralize
    line-ending differences -- it must still catch a REAL content
    modification. Proven against tmp_path-created files, never the real
    production file (this test-writer's role never edits production code,
    not even transiently for a test)."""

    def test_two_files_differing_only_by_line_ending_hash_identically(self, tmp_path):
        content = "line one\nline two\nline three\n"
        crlf_path = tmp_path / "crlf.py"
        lf_path = tmp_path / "lf.py"
        crlf_path.write_bytes(content.replace("\n", "\r\n").encode("utf-8"))
        lf_path.write_bytes(content.encode("utf-8"))

        assert _lf_normalized_sha256(crlf_path) == _lf_normalized_sha256(lf_path), (
            "A CRLF and an LF copy of the SAME logical content must hash identically "
            "-- otherwise this guard still false-fails across a Windows/Linux "
            "checkout boundary, the exact CI-red bug being fixed."
        )

    def test_two_files_with_genuinely_different_content_hash_differently(self, tmp_path):
        original = tmp_path / "original.py"
        modified = tmp_path / "modified.py"
        original.write_bytes(b"def foo():\r\n    return 1\r\n")
        modified.write_bytes(b"def foo():\r\n    return 2\r\n")  # a real content change

        assert _lf_normalized_sha256(original) != _lf_normalized_sha256(modified), (
            "The LF-normalization fix must NOT weaken the guard's actual intent -- "
            "a genuine content modification (not just a line-ending difference) must "
            "still produce a different hash."
        )


def test_retirement_recommender_module_never_imports_ai_advisor():
    """Corroborating source-scan: the math module must never import the LLM
    seam module at all, regardless of the hash pin above."""
    path = REPO_ROOT / "advisors" / "retirement_recommender.py"
    source = path.read_text(encoding="utf-8")
    assert "ai_advisor" not in source, (
        "advisors/retirement_recommender.py must never import ai_advisor -- "
        "it is a deterministic math module, LLM-free by AC-2."
    )


# ===========================================================================
# AC-2 wiring: explainer called per-rec, ordering, stamping
# ===========================================================================


def test_worker_calls_explainer_once_per_recommendation():
    recs = _sample_recs()
    with (
        patch("advisors.retirement_recommender.build_recommendations", return_value=recs),
        patch("advisors.retirement_recommender.persist_recommendations", return_value=len(recs)),
        patch(
            "advisors.retirement_explainer.explain_recommendation", return_value="explanation text"
        ) as mock_explain,
    ):
        app_module._retirement_recommender_tick_worker()

    assert mock_explain.call_count == len(recs), (
        f"Expected explain_recommendation to be called once per recommendation "
        f"({len(recs)}), got {mock_explain.call_count} calls."
    )


def test_worker_stamps_explanation_into_each_recs_raw_response_before_persist():
    recs = _sample_recs()
    captured_persist_arg: list[dict] = []

    def _fake_persist(recs_arg, **kwargs):
        captured_persist_arg.extend(recs_arg)
        return len(recs_arg)

    with (
        patch("advisors.retirement_recommender.build_recommendations", return_value=recs),
        patch("advisors.retirement_recommender.persist_recommendations", side_effect=_fake_persist),
        patch(
            "advisors.retirement_explainer.explain_recommendation",
            return_value="stamped explanation",
        ),
    ):
        app_module._retirement_recommender_tick_worker()

    assert len(captured_persist_arg) == len(recs), (
        "persist_recommendations must be called with all recommendations, stamped."
    )
    for rec in captured_persist_arg:
        assert rec.get("explanation") == "stamped explanation", (
            f"Expected every rec passed to persist_recommendations to carry the "
            f"explainer's output under the 'explanation' key, got {rec!r}."
        )


def test_explainer_runs_after_build_and_before_persist_ordering():
    """Explicit call-order assertion (not just call-count) -- explain must
    happen strictly between build and persist, never before build, never
    after persist."""
    events: list[str] = []

    def _fake_build(**kwargs):
        events.append("build")
        return _sample_recs()

    def _fake_explain(rec):
        events.append(f"explain:{rec['candidate_id']}")
        return "x"

    def _fake_persist(recs_arg, **kwargs):
        events.append("persist")
        return len(recs_arg)

    with (
        patch("advisors.retirement_recommender.build_recommendations", side_effect=_fake_build),
        patch("advisors.retirement_recommender.persist_recommendations", side_effect=_fake_persist),
        patch("advisors.retirement_explainer.explain_recommendation", side_effect=_fake_explain),
    ):
        app_module._retirement_recommender_tick_worker()

    assert events[0] == "build", f"build_recommendations must run first, got order {events}."
    assert events[-1] == "persist", f"persist_recommendations must run last, got order {events}."
    explain_events = [e for e in events if e.startswith("explain:")]
    assert len(explain_events) == 2, f"Expected 2 explain events, got {events}."
    # Every explain event must sit strictly between the build and persist markers.
    build_idx = events.index("build")
    persist_idx = events.index("persist")
    for e in explain_events:
        idx = events.index(e)
        assert build_idx < idx < persist_idx, (
            f"explain event {e!r} did not occur strictly between build and persist: {events}"
        )


def test_explainer_failure_none_does_not_block_persistence():
    """AC-9: the explainer never gates persistence -- a None explanation
    (e.g. the LLM is down) still lets persist_recommendations run with every
    recommendation, explanation stamped as None."""
    recs = _sample_recs()
    captured: list[dict] = []

    def _fake_persist(recs_arg, **kwargs):
        captured.extend(recs_arg)
        return len(recs_arg)

    with (
        patch("advisors.retirement_recommender.build_recommendations", return_value=recs),
        patch("advisors.retirement_recommender.persist_recommendations", side_effect=_fake_persist),
        patch("advisors.retirement_explainer.explain_recommendation", return_value=None),
    ):
        app_module._retirement_recommender_tick_worker()

    assert len(captured) == len(recs), "A None explanation must never block persistence."
    for rec in captured:
        assert rec.get("explanation") is None


def test_empty_recommendations_never_calls_explainer_or_persist_with_anything(caplog):
    """AC-9: no recommendations tonight -> the explainer is never invoked at
    all (nothing to explain), persist_recommendations is still called
    (honoring the existing Cycle-2a contract) with an empty list."""
    with (
        patch("advisors.retirement_recommender.build_recommendations", return_value=[]),
        patch(
            "advisors.retirement_recommender.persist_recommendations", return_value=0
        ) as mock_persist,
        patch("advisors.retirement_explainer.explain_recommendation") as mock_explain,
    ):
        app_module._retirement_recommender_tick_worker()

    mock_explain.assert_not_called()
    mock_persist.assert_called_once()
    call_args = mock_persist.call_args[0]
    assert call_args[0] == [], (
        f"Expected persist_recommendations([]) on an empty night, got {call_args}."
    )


def test_explainer_unexpected_exception_is_logged_and_does_not_crash_the_worker(caplog):
    """Defense-in-depth: explain_recommendation is D-1/never-raises by its
    own contract, but if it somehow did raise (a contract violation), the
    worker must catch it, log type(exc).__name__ only, and continue --
    matching this file's existing outer try/except D-1 pattern (mirrors
    _retirement_recommender_tick_worker's existing build/persist guard)."""
    recs = _sample_recs()

    with (
        patch("advisors.retirement_recommender.build_recommendations", return_value=recs),
        patch("advisors.retirement_recommender.persist_recommendations", return_value=len(recs)),
        patch(
            "advisors.retirement_explainer.explain_recommendation",
            side_effect=RuntimeError("secret leak should never surface: token=abc123"),
        ),
        caplog.at_level(logging.WARNING),
    ):
        try:
            app_module._retirement_recommender_tick_worker()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"_retirement_recommender_tick_worker must never propagate an "
                f"explainer exception: {type(exc).__name__}: {exc}"
            )

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "token=abc123" not in joined, (
        "D-1 security contract: the raw exception message must never reach logs."
    )
