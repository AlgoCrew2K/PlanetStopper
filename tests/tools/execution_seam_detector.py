"""Standalone, non-collected DUAL-SEAM execution-level leak detector.

Deliberately NOT named test_*.py — pytest's default python_files pattern
never auto-collects it, so `/run-tests`'s default sweep never picks it up.
Run it manually as part of a PM/reviewer re-gate:

    python tests/tools/execution_seam_detector.py [test_path ...]

(no args -> the R2-3 asset-swap test superset, see _R2_3_DEFAULT_TARGETS below)

Covers THREE real-money/real-network seams this project introduces or
touches: the LLM seam (anthropic.Anthropic), the Composer backtest seam (the
requests.post call composer_backtest_client.py wraps) — both R2-3 — and the
Atlas/Mongo seam (pymongo.MongoClient) added for the frontrunner-signals
cycle (fr-review finding, team-lead-ratified hard AC-8 scope, 2026-07-16):
AC-1 introduces the first live-Mongo read path outside community_strats.py's
own tests, and per standing project lesson, credential-less-pass alone is
not a sufficient no-live-API detector. Each seam is patched to
record-then-raise on every reachable-unmocked call, the given pytest
target(s) are run IN-PROCESS from a neutral cwd
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


class LiveMongoClientConstructedError(AssertionError):
    """Raised the instant a real pymongo.MongoClient(...) is constructed
    while the detector is active (frontrunner-signals cycle, AC-8)."""


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


def make_mongo_guard() -> tuple[list[str], object]:
    """Return (call_sites, guard_fn) for the Mongo/Atlas seam — extracted as
    its own factory (not inlined in run_seam_detector) so the raise mechanism
    is independently testable WITHOUT going through a nested pytest.main()
    sub-run.

    WHY THIS MATTERS (nested-session shadowing): run_seam_detector's own
    patch is applied in the OUTER process, then pytest.main() launches a
    FRESH nested pytest session — tests/conftest.py's own session-autouse
    `_no_live_mongo_atlas_connections` fixture re-establishes ITS OWN
    `patch("pymongo.MongoClient", ...)` inside that nested session, which
    (being the innermost/most-recently-applied patch for the session's
    duration) shadows whatever the OUTER caller patched beforehand. A test
    that calls `run_seam_detector([...])` and then asserts on `mongo_calls`
    would actually be proving conftest's guard fires, not this tool's own —
    a false-positive self-test. Calling `make_mongo_guard()` directly, inside
    an ALREADY-RUNNING test (no nested pytest.main() involved), sidesteps
    this entirely.

    WHY guard_fn TAKES NO `self` PARAM (a real bug this file shipped with
    briefly, caught by the FIRST actual pytest run of this file — RCA below):
    the earlier version returned a `patch.object(pymongo.MongoClient,
    "__init__", guard_fn)`-shaped function (signature `(self, *args,
    **kwargs)`). That form only works when `pymongo.MongoClient` is genuinely
    the real class at patch-application time. Under pytest, tests/conftest.py's
    OWN session-autouse fixture has ALREADY replaced the `pymongo.MongoClient`
    NAME with a MagicMock before ANY test in the outer session runs (session
    scope = established once, wraps the whole session) — so by the time a
    test in THIS file executes, `pymongo.MongoClient` already IS a Mock, and
    `patch.object(<a Mock instance>, "__init__", ...)` raises "Attempting to
    set unsupported magic method '__init__'" (unittest.mock disallows setting
    dunder attributes on a Mock this way). The unittest.mock nesting
    guarantee ("a test's own local patch overrides the outer session
    fixture") holds for NAME-REPLACEMENT-style patches
    (`patch("target", ...)`, which simply reassigns whatever is currently
    there) but NOT for `patch.object`-style patches (which need to mutate an
    attribute ON the current target object, and fail when that object is
    already a Mock with restricted dunder handling). Fix: guard_fn is now a
    plain side_effect-compatible callable (no `self`), applied via
    `patch("pymongo.MongoClient", side_effect=guard_fn)` everywhere it's
    used — both here and in run_seam_detector below — which works
    identically whether the current `pymongo.MongoClient` binding is the
    real class (run_seam_detector's outer-process, pre-pytest application)
    or an already-active Mock (this file's tests, running inside the outer
    pytest session).
    """
    calls: list[str] = []

    def _record_then_raise_mongo(*args, **kwargs):
        calls.append("".join(traceback.format_stack(limit=8)))
        raise LiveMongoClientConstructedError(
            "execution_seam_detector: a REAL pymongo.MongoClient(...) was constructed "
            "during this run — an Atlas-seam mock is leaking. See the recorded call site "
            "in the returned construction list."
        )

    return calls, _record_then_raise_mongo


def run_seam_detector(test_paths: list[str]) -> tuple[int, list[str], list[str], list[str]]:
    """Run `test_paths` under pytest with the Anthropic SDK constructor, the
    Composer /backtest network call, AND pymongo.MongoClient construction
    all patched to record-then-raise.

    Returns:
        (pytest_exit_code, anthropic_call_sites, composer_call_sites,
        mongo_call_sites) — each call-sites list holds one formatted stack
        trace per live-call attempt on that seam. A non-empty list on ANY of
        the three means at least one test left a real hole in its seam
        mocking, regardless of that test's own reported outcome.
    """
    import anthropic  # local import — keeps this tool importable even in an

    # environment without the SDK installed, since only __main__/callers that
    # actually invoke this function need it present. pymongo is NOT imported
    # here directly — patch("pymongo.MongoClient", ...) below resolves the
    # string target itself (unittest.mock does its own import), and doing so
    # is what makes it work correctly whether pymongo.MongoClient is still
    # the real class or already patched by an outer/nested session guard —
    # see make_mongo_guard()'s docstring for the full RCA on why the
    # patch.object(..., "__init__", ...) style this used to use broke.
    import requests

    anthropic_calls: list[str] = []
    composer_calls: list[str] = []
    mongo_calls, _record_then_raise_mongo = make_mongo_guard()

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
            patch("pymongo.MongoClient", side_effect=_record_then_raise_mongo),
        ):
            import pytest  # local import, same reasoning as above

            rc = pytest.main([*test_paths, "-p", "no:cacheprovider", "-q", "-n0"])
    finally:
        os.chdir(original_cwd)

    return int(rc), anthropic_calls, composer_calls, mongo_calls


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


# The frontrunner-signals cycle's own test surface — default target for a
# no-args run scoped to THIS cycle (distinct from _R2_3_DEFAULT_TARGETS,
# which covers the asset-swap reasoning port). AC-1 introduces the first
# live-Mongo read path outside community_strats.py's own tests, so this list
# exists specifically to exercise the new Mongo seam guard end-to-end against
# this cycle's real test files, not just the pre-existing Anthropic/Composer
# seams. fr-review-requested, 2026-07-16.
_FR_SIGNALS_DEFAULT_TARGETS: list[str] = [
    "tests/advisors/test_frontrunner_signals_ingest.py",
    "tests/advisors/test_frontrunner_signals_warehouse.py",
    "tests/advisors/test_frontrunner_signals_classification.py",
    "tests/advisors/test_frontrunner_extraction_walk.py",
    "tests/advisors/test_frontrunner_detector_ac3_rebuild.py",
    "tests/advisors/test_frontrunner_builder_signal_gating.py",
    "tests/advisors/test_frontrunner_signals_no_live_api.py",
    "tests/app/test_frontrunner_signals_tab_render.py",
]


def _report(label: str, sites: list[str]) -> None:
    print(f"{label}: {len(sites)} live call(s)")
    for i, site in enumerate(sites, 1):
        print(f"--- {label} call #{i} ---")
        print(site)


if __name__ == "__main__":
    targets = sys.argv[1:] or _R2_3_DEFAULT_TARGETS
    exit_code, anthropic_sites, composer_sites, mongo_sites = run_seam_detector(targets)
    print(
        f"\nexecution_seam_detector: pytest rc={exit_code}, "
        f"anthropic calls={len(anthropic_sites)}, composer calls={len(composer_sites)}, "
        f"mongo calls={len(mongo_sites)}"
    )
    _report("ANTHROPIC", anthropic_sites)
    _report("COMPOSER", composer_sites)
    _report("MONGO", mongo_sites)
    if anthropic_sites or composer_sites or mongo_sites:
        sys.exit(1)
    sys.exit(0 if exit_code == 0 else exit_code)
