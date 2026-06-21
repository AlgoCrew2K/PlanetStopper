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


# Test modules that exercise the REAL propose_strategies → _generate_candidate_trees path
# and therefore need the live C1/C2 builder seams stubbed. A module that tests the builder
# collaborators DIRECTLY (build_plan_generator, universe_provider, plan_tree_compiler) — or
# that already mocks propose_strategies / _build_client itself — is NOT in this set and must
# NOT have its function-under-test clobbered.
# Test modules that drive the REAL propose_strategies → _generate_candidate_trees path
# WITHOUT supplying their own builder-seam mocks, and so would make a live Opus/Alpaca call
# per test post-C4. A module that tests the builder collaborators DIRECTLY
# (build_plan_generator, universe_provider, plan_tree_compiler) — or that mocks
# propose_strategies / _build_client itself — is NOT in this set.
_MODULES_NEEDING_BUILDER_SEAM_GUARD = frozenset(
    {
        "test_strategy_builder_engine",  # the ~10 pre-existing propose_strategies tests
        "test_community_strats_wiring",  # calls real propose_strategies w/ community cands
    }
)


@pytest.fixture(autouse=True)
def _no_live_builder_seams(request, monkeypatch):
    """Neutralise the LIVE Opus + Alpaca builder seams ONLY for the modules that drive the
    REAL propose_strategies path without their own builder mocks.

    C4 (commit 5ae6c8c) swapped the propose_strategies body to drive the REAL builder:
    _generate_candidate_trees calls universe_provider.get_tradeable_set (live Alpaca
    /v2/assets) + build_plan_generator.generate_build_plans (which builds an Anthropic SDK
    client via _build_client and makes a LIVE Opus call — .env has ANTHROPIC_API_KEY, so the
    call really fires). Pre-existing propose_strategies tests mock ONLY run_backtest → post-
    swap they HANG on the live Opus call + spend real money (the integration blast-radius
    the merge gate caught).

    WHAT WE MOCK (the true live seams, NOT the function under test):
      - build_plan_generator._build_client → raise RuntimeError. generate_build_plans is
        never-raising (D-1): it catches this and returns an empty GeneratorResult. So the
        REAL generate_build_plans logic still runs (no live SDK), degrading to empty plans —
        a guarded test that needs built-new candidates overrides generate_build_plans
        per-test (its monkeypatch wins over this default).
      - universe_provider.get_tradeable_set → empty frozenset (no live Alpaca fetch).

    SCOPING: applied ONLY to _MODULES_NEEDING_BUILDER_SEAM_GUARD. NOT applied to
    test_build_plan_generator*.py (they test generate_build_plans directly, mocking
    _build_client themselves), test_universe_provider.py (tests get_tradeable_set directly),
    test_builder_integration.py / route tests (override the seams per-test). A blanket
    directory-wide stub of generate_build_plans broke those (clobbered the function under
    test) — scoping by module + mocking the inner _build_client seam fixes it.

    Stale-by-intent re-point (test-writer lane; NOT a weakening — the body swap intentionally
    added the live seams)."""
    module_stem = request.module.__name__.rsplit(".", 1)[-1]
    if module_stem not in _MODULES_NEEDING_BUILDER_SEAM_GUARD:
        return

    try:
        import advisors.build_plan_generator as _gen  # noqa: PLC0415
        import advisors.universe_provider as _up  # noqa: PLC0415
    except Exception:
        return

    def _no_live_client(*_a, **_k):
        raise RuntimeError("live Opus SDK disabled in tests (conftest _no_live_builder_seams)")

    monkeypatch.setattr(_gen, "_build_client", _no_live_client, raising=False)
    monkeypatch.setattr(_up, "get_tradeable_set", lambda *a, **k: frozenset(), raising=False)
