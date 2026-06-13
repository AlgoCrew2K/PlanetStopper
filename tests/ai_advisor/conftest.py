"""
conftest.py for tests/ai_advisor/ — directory-scoped pytest fixtures.

Provides the ``stub_network_producers`` fixture: patches the three
live-network section producers in ai_advisor so that tests that call
``assemble_advisor_context`` perform no live network I/O.

Producers patched (all call _fetch_with_backoff internally):
  - ai_advisor._build_sentiment_section   (GDELT)
  - ai_advisor._build_macro_section       (FRED)
  - ai_advisor._build_fundamentals_section (SEC EDGAR)

Each stub returns an honest ``available=False`` lens block — the same shape
the real producers return on a degraded source — so context-assembly tests
see a structurally valid context dict.

This fixture is NOT autouse.  It is requested explicitly by
``test_ai_advisor.py``'s module-scoped ``_block_network_producers`` autouse
fixture so that tests in other files (test_cycle2_lens_producers.py,
test_backoff_termination.py) that need to exercise the real producer bodies
are unaffected.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Honest stub lens blocks.
# Shape matches the real producer contract (lens, available, reason, payload,
# sources) so assemble_advisor_context produces a structurally valid ctx.
# ---------------------------------------------------------------------------

_SENTINEL_SENTIMENT = {
    "lens": "sentiment",
    "available": False,
    "reason": "unit-test-stub — no live GDELT fetch in unit tests",
    "payload": None,
    "sources": [],
}

_SENTINEL_MACRO = {
    "lens": "macro",
    "available": False,
    "reason": "unit-test-stub — no live FRED fetch in unit tests",
    "payload": None,
    "sources": [],
}

_SENTINEL_FUNDAMENTALS = {
    "lens": "fundamentals",
    "available": False,
    "reason": "unit-test-stub — no live SEC EDGAR fetch in unit tests",
    "payload": None,
    "sources": [],
}


@pytest.fixture()
def stub_network_producers():
    """Patches the three live-network section producers with honest stubs.

    Request this fixture in any test (or a module-scoped autouse fixture) that
    calls ``assemble_advisor_context`` and should not hit live GDELT/FRED/SEC
    endpoints.
    """
    import ai_advisor

    with (
        patch.object(
            ai_advisor,
            "_build_sentiment_section",
            return_value=_SENTINEL_SENTIMENT,
        ),
        patch.object(
            ai_advisor,
            "_build_macro_section",
            return_value=_SENTINEL_MACRO,
        ),
        patch.object(
            ai_advisor,
            "_build_fundamentals_section",
            return_value=_SENTINEL_FUNDAMENTALS,
        ),
    ):
        yield
