"""Standalone, non-collected execution-level Anthropic-SDK seam detector.

Deliberately NOT named test_*.py — pytest's default python_files pattern
never auto-collects it, so `/run-tests`'s default sweep never picks it up.
Run it manually as part of a PM/reviewer re-gate:

    python tests/tools/execution_seam_detector.py [test_path ...]

(no args -> the R2-3 asset-swap test superset, see _R2_3_DEFAULT_TARGETS below)

Patches anthropic.Anthropic.__init__ to record-then-raise on EVERY
construction, runs the given pytest target(s) IN-PROCESS from a neutral cwd
(mirrors this project's "pytest.main(), neutral dir" no-xdist gate technique
— never spawns a nested subprocess pytest, always -n0, per the host
memory-cap hard rule — CLAUDE.md's "NEVER run full suite uncapped or with
-n>4"), and reports every live client construction. A clean run (pytest
rc in {0, 1} for genuine test failures unrelated to this detector, AND
zero constructions) proves every test's own _build_client()-style mock is
genuinely intercepting the LLM seam — a leak hidden behind D-1 degradation,
or a test whose assertions happen to still pass on a real response, is
caught here regardless of whether the test itself reports green.

KNOWN LIMITATION: a test that deliberately constructs a REAL
anthropic.Anthropic(...) for SDK-surface sanity checking (e.g.
tests/ai_advisor/test_sdk_contract.py::test_messages_parse_method_exists_on_installed_sdk,
which passes a dummy, non-billing api_key purely to inspect
`client.messages.parse`'s existence) will trip a FALSE POSITIVE if included
in the target list — scope target lists to the reasoning-port's own test
files, never the whole tree.

R2-2 (DE-ADVISOR-R2-2-001) established this exact check as an ad-hoc,
uncommitted verification step run independently by the test-writer and
reviewer at the final gate ("the execution-level Anthropic-seam detector —
the definitive tool, now the standing final seam check"). DECISIONS.md
explicitly notes it "for R2-3 (Asset Swaps) reuse" — this module captures it
as reusable code instead of re-deriving it by hand every cycle.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[2]


class LiveClientConstructedError(AssertionError):
    """Raised the instant a real anthropic.Anthropic(...) is constructed
    while the detector is active."""


def run_seam_detector(test_paths: list[str]) -> tuple[int, list[str]]:
    """Run `test_paths` under pytest with the Anthropic SDK constructor
    patched to record-then-raise.

    Returns:
        (pytest_exit_code, construction_call_sites) — construction_call_sites
        is a list of formatted stack traces, one per live-construction
        attempt. A non-empty list means at least one test left a real hole in
        its LLM-seam mocking, regardless of that test's own reported outcome.
    """
    import anthropic  # local import — keeps this tool importable even in an
    # environment without the SDK installed, since only __main__/callers that
    # actually invoke this function need it present.

    constructions: list[str] = []

    def _record_then_raise(self, *args, **kwargs):
        constructions.append("".join(traceback.format_stack(limit=8)))
        raise LiveClientConstructedError(
            "execution_seam_detector: a REAL anthropic.Anthropic(...) was constructed "
            "during this run — an LLM-seam mock is leaking. See the recorded call site "
            "in the returned construction list."
        )

    original_cwd = os.getcwd()
    os.chdir(str(_REPO_ROOT))  # neutral dir — matches the project's no-xdist gate technique
    try:
        with patch.object(anthropic.Anthropic, "__init__", _record_then_raise):
            import pytest  # local import, same reasoning as above

            rc = pytest.main([*test_paths, "-p", "no:cacheprovider", "-q", "-n0"])
    finally:
        os.chdir(original_cwd)

    return int(rc), constructions


# The R2-3 asset-swap reasoning-port test superset — default target when run
# standalone with no CLI arguments. Deliberately excludes any file (e.g.
# test_sdk_contract.py) that legitimately constructs a real SDK client for
# non-LLM-call sanity checking — see the KNOWN LIMITATION note above.
_R2_3_DEFAULT_TARGETS: list[str] = [
    "tests/advisors/test_asset_swap_engine_reasoned_generation.py",
    "tests/advisors/test_asset_swap_engine_reasoning_context.py",
    "tests/advisors/test_asset_swap_engine_candidate_universe_validation.py",
    "tests/advisors/test_asset_swap_engine_validate_tree_guard.py",
    "tests/advisors/test_asset_swap_engine_gate_batch_characterization.py",
    "tests/advisors/test_asset_swap_engine_provenance.py",
    "tests/advisors/test_asset_swap_engine_honest_degradation.py",
    "tests/advisors/test_asset_swap_engine_credentialless_bounded_prompt.py",
    "tests/advisors/test_asset_swap_engine_explicit_pair_preserved.py",
    "tests/advisors/test_asset_swap_production_wiring.py",
    "tests/ui/test_asset_swap_route_reasoning_provenance.py",
    "tests/ui/test_asset_swap_routes.py",
    "tests/ai_advisor/test_as_live_generation_provenance_render.py",
]


if __name__ == "__main__":
    targets = sys.argv[1:] or _R2_3_DEFAULT_TARGETS
    exit_code, sites = run_seam_detector(targets)
    print(f"\nexecution_seam_detector: pytest rc={exit_code}, live constructions={len(sites)}")
    if sites:
        print("LIVE CLIENT CONSTRUCTION CALL SITES:")
        for i, site in enumerate(sites, 1):
            print(f"--- construction #{i} ---")
            print(site)
        sys.exit(1)
    sys.exit(0 if exit_code == 0 else exit_code)
