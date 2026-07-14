"""Standalone, non-collected DUAL-SEAM execution-level leak detector.

Deliberately NOT named test_*.py — pytest's default python_files pattern
never auto-collects it, so `/run-tests`'s default sweep never picks it up.
Run it manually as part of a PM/reviewer re-gate:

    python tests/tools/execution_seam_detector.py [test_path ...]

(no args -> the R2-3 asset-swap test superset, see _R2_3_DEFAULT_TARGETS below)

Covers BOTH real-money network seams this reasoning port introduces or
touches (team-lead ruling, R2-3): the LLM seam (anthropic.Anthropic) AND the
Composer backtest seam (the requests.post call composer_backtest_client.py
wraps). Each is patched to record-then-raise on every reachable-unmocked
call, the given pytest target(s) are run IN-PROCESS from a neutral cwd
(mirrors this project's "pytest.main(), neutral dir" no-xdist gate technique
— never spawns a nested subprocess pytest, always -n0, per the host
memory-cap hard rule — CLAUDE.md's "NEVER run full suite uncapped or with
-n>4"), and every live call is reported. A clean run (pytest rc in {0, 1}
for genuine test failures unrelated to this detector, AND zero calls on
either seam) proves every test's own seam-mock (_build_client()-style for
Anthropic, run_backtest-style for Composer) is genuinely intercepting —
a leak hidden behind D-1 degradation, a test whose assertions happen to
still pass on a real response, or a test that mocks one local name-binding
but not the true network boundary, is caught here regardless of whether the
test itself reports green.

WHY THE COMPOSER SEAM MATTERS HERE (team-lead ruling): Composer's /backtest
endpoint does NOT enforce auth, so a credential-less-green test run can still
reach the real network if a test forgets to mock run_backtest at whichever
local name-binding the code under test actually calls (or mocks
composer_backtest_client.run_backtest at the SOURCE module while the caller
imported it into its own module namespace, e.g.
advisors.asset_swap_engine.run_backtest — a different binding). Patching at
the TRUE network boundary (requests.post, filtered to the Composer backtest
URL) is immune to which local name got missed — including the SPY-baseline
call inside _spy_returns_fn_for, which reuses the SAME run_backtest
reference as every candidate backtest, so no special-casing is needed beyond
this one guard. The R2-2 PM-gate verifier found exactly this class of leak
(2 pre-existing live Composer /backtest calls in test_builder_integration.py,
tracked separately) — this detector generalizes that finding into a
reusable, standing check.

KNOWN LIMITATION (Anthropic seam only): a test that deliberately constructs
a REAL anthropic.Anthropic(...) for SDK-surface sanity checking (e.g.
tests/ai_advisor/test_sdk_contract.py::test_messages_parse_method_exists_on_installed_sdk,
which passes a dummy, non-billing api_key purely to inspect
`client.messages.parse`'s existence) will trip a FALSE POSITIVE if included
in the target list — scope target lists to the reasoning-port's own test
files, never the whole tree.

R2-2 (DE-ADVISOR-R2-2-001) established the single-seam (Anthropic-only)
version of this check as an ad-hoc, uncommitted verification step run
independently by the test-writer and reviewer at the final gate. DECISIONS.md
explicitly notes it "for R2-3 (Asset Swaps) reuse" — this module captures it
as reusable, dual-seam code instead of re-deriving it by hand every cycle.
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


class LiveComposerCallError(AssertionError):
    """Raised the instant a real, unmocked Composer /backtest POST is
    attempted while the detector is active."""


def _is_composer_backtest_url(url: object) -> bool:
    """Return True iff `url` is (or targets) Composer's /backtest endpoint.

    Compared against the REAL COMPOSER_BASE_URL constant (not a guessed
    substring) — the same constant composer_backtest_client.py itself
    imports from alpha_bot_execution to build the request URL.
    """
    if not isinstance(url, str):
        return False
    try:
        from alpha_bot_execution import COMPOSER_BASE_URL  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - if the constant can't be resolved, fall back
        # to a conservative substring check so the detector still catches
        # obviously-Composer URLs rather than silently under-detecting.
        return "/backtest" in url and "composer" in url.lower()
    return url == f"{COMPOSER_BASE_URL}/backtest"


def _extract_url(args: tuple, kwargs: dict) -> object:
    """requests.post(url, ...) may pass url positionally or as a kwarg."""
    if args:
        return args[0]
    return kwargs.get("url")


def run_seam_detector(test_paths: list[str]) -> tuple[int, list[str], list[str]]:
    """Run `test_paths` under pytest with BOTH the Anthropic SDK constructor
    AND the Composer /backtest network call patched to record-then-raise.

    Returns:
        (pytest_exit_code, anthropic_call_sites, composer_call_sites) —
        each call-sites list holds one formatted stack trace per live-call
        attempt on that seam. A non-empty list on EITHER means at least one
        test left a real hole in its seam mocking, regardless of that test's
        own reported outcome.
    """
    import anthropic  # local import — keeps this tool importable even in an

    # environment without the SDK installed, since only __main__/callers that
    # actually invoke this function need it present.
    import requests

    anthropic_calls: list[str] = []
    composer_calls: list[str] = []

    def _record_then_raise_anthropic(self, *args, **kwargs):
        anthropic_calls.append("".join(traceback.format_stack(limit=8)))
        raise LiveClientConstructedError(
            "execution_seam_detector: a REAL anthropic.Anthropic(...) was constructed "
            "during this run — an LLM-seam mock is leaking. See the recorded call site "
            "in the returned construction list."
        )

    real_post = requests.post

    def _guarded_post(*args, **kwargs):
        url = _extract_url(args, kwargs)
        if _is_composer_backtest_url(url):
            composer_calls.append("".join(traceback.format_stack(limit=8)))
            raise LiveComposerCallError(
                f"execution_seam_detector: a REAL Composer /backtest POST to {url!r} was "
                "attempted during this run — a run_backtest/requests.post mock is leaking "
                "(Composer's /backtest endpoint does not enforce auth, so this can happen "
                "even credential-less). See the recorded call site in the returned list."
            )
        # Not a Composer /backtest call — pass through to the real requests.post
        # (individual tests may legitimately need other endpoints mocked their
        # own way; this guard is scoped to the one real-money endpoint).
        return real_post(*args, **kwargs)

    original_cwd = os.getcwd()
    os.chdir(str(_REPO_ROOT))  # neutral dir — matches the project's no-xdist gate technique
    try:
        with (
            patch.object(anthropic.Anthropic, "__init__", _record_then_raise_anthropic),
            patch("requests.post", side_effect=_guarded_post),
        ):
            import pytest  # local import, same reasoning as above

            rc = pytest.main([*test_paths, "-p", "no:cacheprovider", "-q", "-n0"])
    finally:
        os.chdir(original_cwd)

    return int(rc), anthropic_calls, composer_calls


# The R2-3 asset-swap reasoning-port test superset — default target when run
# standalone with no CLI arguments. Widened (r2-3-review finding, post-GREEN
# gate) from the original 13-file list to the FULL §5 handoff superset — the
# original list under-covered: test_advisor_liveness_gate.py,
# test_weekly_asset_swap_suggestions_loop.py, test_asset_swap_engine.py, and
# both cycle3 files all exercise suggest_swaps/the reasoned path too, so a
# casual no-args run was silently missing genuine R2-3 seam-leak surface.
# Deliberately excludes any file (e.g. test_sdk_contract.py) that
# legitimately constructs a real SDK client for non-LLM-call sanity checking
# — see the KNOWN LIMITATION note above.
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
    "tests/advisors/test_advisor_liveness_gate.py",
    "tests/advisors/test_weekly_asset_swap_suggestions_loop.py",
    "tests/ai_advisor/test_asset_swap_engine.py",
    "tests/ai_advisor/test_cycle3_lens_informed_swaps.py",
    "tests/ai_advisor/test_cycle3_lens_swaps_supplement.py",
    "tests/ai_advisor/test_lens_blend_efficacy.py",
]


def _report(label: str, sites: list[str]) -> None:
    print(f"{label}: {len(sites)} live call(s)")
    for i, site in enumerate(sites, 1):
        print(f"--- {label} call #{i} ---")
        print(site)


if __name__ == "__main__":
    targets = sys.argv[1:] or _R2_3_DEFAULT_TARGETS
    exit_code, anthropic_sites, composer_sites = run_seam_detector(targets)
    print(
        f"\nexecution_seam_detector: pytest rc={exit_code}, "
        f"anthropic calls={len(anthropic_sites)}, composer calls={len(composer_sites)}"
    )
    _report("ANTHROPIC", anthropic_sites)
    _report("COMPOSER", composer_sites)
    if anthropic_sites or composer_sites:
        sys.exit(1)
    sys.exit(0 if exit_code == 0 else exit_code)
