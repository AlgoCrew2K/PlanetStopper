"""
RED tests for prism_scheduler.main() wiring to the numeric verifier
(DE-PRISM-NUMERIC-VERIFY-001, AC-8).

Plan contract (Architecture section): main() already calls _patch_provenance(run_id, row)
after F-4 row-verification (prism_scheduler.py:521). This adds a call to
advisors.prism_numeric_verifier.verify_cited_numbers(...) immediately after
_patch_provenance, threading through the same lens_sections payloads (AC-4 — one fetch
feeds both SOURCES and VERIFICATION), then persists via persist_verification(...). The
verifier call must be D-1 (never raises) and must NEVER gate sys.exit(0) — a verifier
failure never fails the nightly run (AC-8).

Import strategy: patch("advisors.prism_numeric_verifier.<fn>", ..., create=True) targets
the not-yet-existing module. Pre-GREEN this raises ModuleNotFoundError inside the `with`
block for every test here — a clean, expected RED-for-the-right-reason (the module is new;
main() cannot call something that does not exist).

Mirrors the existing _import_scheduler() reload pattern from
tests/ai_advisor/test_prism_scheduling.py (module-object identity preserved via
importlib.reload rather than del+reimport, so patch() targets in other test files stay
valid).

DB isolation: conftest._isolate_db autouse fixture. Run protocol: targeted -n0 only.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

_RUN_ID = "wiring-test-run-id"

_SAMPLE_ROW = {
    "id": 10,
    "advisor_role": "MARKET_PRISM",
    "subject_id": "",
    "verdict": "neutral",
    "raw_response": {
        "run_id": _RUN_ID,
        "run_ts": _RUN_ID,
        "overall_sentiment": "neutral",
        "sentiment_rationale": "Wiring test row.",
        "per_lens_digest": {},
    },
}

_SAMPLE_VERIFY_RESULT = {
    "checks": [],
    "summary": {"n_checks": 0, "n_pass": 0, "n_flagged": 0, "n_overridden": 0, "n_unverifiable": 0},
    "verdict": "no-numeric-claims",
}


def _import_scheduler():
    """Import (or reimport) prism_scheduler fresh — module identity preserved via reload.

    Mirrors tests/ai_advisor/test_prism_scheduling.py::_import_scheduler exactly: del+
    reimport would create a new module object, corrupting patch() targets held by other
    test files that reference prism_scheduler at collection time.
    """
    import importlib
    import os

    worktree = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if worktree not in sys.path:
        sys.path.insert(0, worktree)
    if "prism_scheduler" in sys.modules:
        importlib.reload(sys.modules["prism_scheduler"])
    else:
        import prism_scheduler  # noqa: PLC0415
    return sys.modules["prism_scheduler"]


def _mock_subprocess_result() -> MagicMock:
    result = MagicMock()
    result.returncode = 0
    result.stdout = '{"total_cost_usd": 5.0}'
    result.stderr = ""
    return result


# ===========================================================================
# AC-8: main() calls verify_cited_numbers after _patch_provenance
# ===========================================================================


class TestVerifierWiring:
    def test_main_calls_verify_cited_numbers_after_patch_provenance(self):
        """AC-8: verify_cited_numbers must be invoked, and strictly AFTER _patch_provenance
        in the same main() run (order-checked via a shared call-order list)."""
        mod = _import_scheduler()
        call_order: list[str] = []

        def _patch_prov_side_effect(*_a, **_kw):
            call_order.append("patch_provenance")
            return True

        def _verify_side_effect(*_a, **_kw):
            call_order.append("verify_cited_numbers")
            return _SAMPLE_VERIFY_RESULT

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=_mock_subprocess_result()),
            patch.object(mod, "_get_market_prism_row_for_run", return_value=_SAMPLE_ROW, create=True),
            patch.object(mod, "_patch_provenance", side_effect=_patch_prov_side_effect),
            patch(
                "advisors.prism_numeric_verifier.verify_cited_numbers",
                side_effect=_verify_side_effect,
                create=True,
            ) as mock_verify,
            patch(
                "advisors.prism_numeric_verifier.persist_verification",
                return_value=1,
                create=True,
            ),
            patch.object(mod, "_persist_spend"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                mod.main()

        assert exc_info.value.code == 0, (
            f"main() must still exit 0 on the happy path; got {exc_info.value.code}"
        )
        mock_verify.assert_called_once(), "AC-8: verify_cited_numbers must be called exactly once"
        assert call_order == ["patch_provenance", "verify_cited_numbers"], (
            f"AC-8: verify_cited_numbers must be called AFTER _patch_provenance in the "
            f"same main() run; observed call order: {call_order!r}"
        )

    def test_verifier_called_with_populated_lens_sections(self):
        """AC-4: the verifier must be called with a non-None, non-empty lens_sections
        payload — proving the shared patch-time fetch is threaded through, not left to
        the verifier's own internal-refetch fallback on every nightly run."""
        mod = _import_scheduler()

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=_mock_subprocess_result()),
            patch.object(mod, "_get_market_prism_row_for_run", return_value=_SAMPLE_ROW, create=True),
            patch.object(mod, "_patch_provenance", return_value=True),
            patch(
                "advisors.prism_numeric_verifier.verify_cited_numbers",
                return_value=_SAMPLE_VERIFY_RESULT,
                create=True,
            ) as mock_verify,
            patch(
                "advisors.prism_numeric_verifier.persist_verification",
                return_value=1,
                create=True,
            ),
            patch.object(mod, "_persist_spend"),
        ):
            with pytest.raises(SystemExit):
                mod.main()

        mock_verify.assert_called_once()
        _call = mock_verify.call_args
        lens_sections_arg = _call.kwargs.get("lens_sections")
        if lens_sections_arg is None and len(_call.args) >= 3:
            lens_sections_arg = _call.args[2]
        assert lens_sections_arg, (
            "AC-4: main() must call verify_cited_numbers with a populated lens_sections "
            "payload (reusing the same builder fetch _patch_provenance performs) — "
            f"got lens_sections={lens_sections_arg!r} from call {_call!r}"
        )

    def test_verifier_exception_does_not_change_exit_code(self):
        """AC-8: a verifier exception must never change sys.exit(0) — the nightly run
        must still succeed even when the verifier itself blows up."""
        mod = _import_scheduler()

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=_mock_subprocess_result()),
            patch.object(mod, "_get_market_prism_row_for_run", return_value=_SAMPLE_ROW, create=True),
            patch.object(mod, "_patch_provenance", return_value=True),
            patch(
                "advisors.prism_numeric_verifier.verify_cited_numbers",
                side_effect=RuntimeError("verifier blew up"),
                create=True,
            ),
            patch.object(mod, "_persist_spend"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                mod.main()

        assert exc_info.value.code == 0, (
            f"AC-8: a verifier exception must NEVER gate sys.exit(0) — the nightly run "
            f"succeeded (council wrote its row) regardless of verifier health. "
            f"Got exit code {exc_info.value.code}."
        )

    def test_verifier_not_invoked_when_no_market_prism_row_found(self):
        """The verifier must only run when a MARKET_PRISM row was actually confirmed —
        never invoked on the rc==0-but-no-row retry-exhausted failure path."""
        mod = _import_scheduler()

        with (
            patch.object(mod, "_get_summary", return_value=None),
            patch("subprocess.run", return_value=_mock_subprocess_result()),
            patch.object(mod, "_get_market_prism_row_for_run", return_value=None, create=True),
            patch(
                "advisors.prism_numeric_verifier.verify_cited_numbers",
                create=True,
            ) as mock_verify,
            patch("time.sleep", return_value=None),
        ):
            with pytest.raises(SystemExit) as exc_info:
                mod.main()

        assert exc_info.value.code != 0, (
            "Precondition: no MARKET_PRISM row ever found -> main() must exit non-zero "
            "(F-4 contract)"
        )
        assert not mock_verify.called, (
            "The verifier must never be invoked when no MARKET_PRISM row was confirmed "
            f"for the run — got {mock_verify.call_count} call(s)."
        )
