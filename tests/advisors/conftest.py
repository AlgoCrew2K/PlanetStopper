"""Conftest for tests/advisors/.

Clears the DEV_ADVISOR_FIXTURE env var that is loaded from .env via
synthetic_history/alpha_bot_execution load_dotenv() at import time.
Without this, the /ai-advisor/suggest route hits the early-return fixture
path before reaching the real ai_advisor.assemble_advisor_context call,
making it impossible to test the real exception-surfacing path.
"""

import pytest


@pytest.fixture(autouse=True)
def _clear_dev_advisor_fixture(monkeypatch):
    """Remove DEV_ADVISOR_FIXTURE from os.environ for all tests in this dir.

    The .env file has DEV_ADVISOR_FIXTURE=1 (dev convenience); imported modules
    call load_dotenv() at module load, populating os.environ.  Route tests that
    exercise the real code path need it absent so the early-return bypass doesn't
    fire before reaching the patched call site.
    """
    monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)


@pytest.fixture(autouse=True)
def _no_live_builder_seams(monkeypatch):
    """Mock the LIVE builder seams (C1 Alpaca + C2 Opus SDK) for ALL advisors tests.

    C4 (commit 5ae6c8c) swapped the propose_strategies body to drive the REAL builder:
    _generate_candidate_trees now calls universe_provider.get_tradeable_set (a live Alpaca
    /v2/assets fetch) and build_plan_generator.generate_build_plans (a live Anthropic Opus
    SDK call). The ~10 PRE-EXISTING propose_strategies tests mock ONLY run_backtest — so
    without this guard they would make a LIVE Opus call (+ Alpaca) per test → HANG + real
    API spend (the integration blast-radius the merge gate caught).

    This autouse fixture installs SAFE DEFAULTS (empty membership set + empty generator
    result) so any test that drives propose_strategies degrades to zero candidates WITHOUT
    a network call — preserving each pre-existing test's propose_strategies-behavior intent
    (the body swap intentionally added the live seams; expanding the mock surface is a
    stale-by-intent re-point, NOT a weakening). Tests that need SPECIFIC builder behaviour
    (test_builder_integration.py, the route tests) override these seams with their own
    monkeypatch INSIDE the test body, which takes precedence over this autouse default.

    Patches the SOURCE modules (c4-impl used lazy function-scope imports in
    _generate_candidate_trees, so the source-module name is the live binding)."""
    try:
        import advisors.build_plan_generator as _gen  # noqa: PLC0415
        import advisors.universe_provider as _up  # noqa: PLC0415
    except Exception:
        # If the builder modules are not importable in some narrow context, there is
        # nothing to guard — let the test proceed (it will not reach the live seam).
        return

    monkeypatch.setattr(_up, "get_tradeable_set", lambda *a, **k: frozenset(), raising=False)
    monkeypatch.setattr(
        _gen,
        "generate_build_plans",
        lambda *a, **k: _gen.GeneratorResult(plans=[], reason="MockedNoLiveOpusInTests"),
        raising=False,
    )
