"""
conftest.py for tests/ai_advisor/ — directory-scoped pytest fixtures.

Provides the ``stub_network_producers`` fixture: patches the four
live-network section producers in ai_advisor so that tests that call
``assemble_advisor_context`` perform no live network I/O.

Producers patched:
  - ai_advisor._build_sentiment_section    (GDELT)
  - ai_advisor._build_macro_section        (FRED)
  - ai_advisor._build_fundamentals_section (SEC EDGAR)
  - advisors.lens_technicals._get_bars     (Alpaca — proxy floor means
    _build_technicals_section ALWAYS fetches bars; mock the seam so the
    suite runs offline)

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
import requests.exceptions

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


@pytest.fixture(autouse=True)
def _stub_live_lens_seams():
    """Autouse: stub live-network seams for ALL tests in tests/ai_advisor/.

    Three seams are stubbed here because all are always reachable in
    assemble_advisor_context regardless of test setup:

    1. lens_technicals._get_bars — The _PROXY_UNIVERSE floor (DE-TECH-002)
       means _build_technicals_section ALWAYS fetches Alpaca bars (never
       an empty universe).  Stubbing _get_bars keeps tests offline.

    2. requests.get — assemble_advisor_context calls _build_sentiment_section
       (GDELT artlist + lens_gdelt._fetch_gdelt_sentiment tone), _build_macro
       _section (FRED), and _build_fundamentals_section (SEC EDGAR), each of
       which makes HTTPS GET calls.  Stubbing requests.get to raise
       ConnectionError causes all three to return available=False with no live
       network I/O.  With requests.get raising immediately, lens_gdelt catches
       the ConnectionError in its broad ``except Exception`` and returns
       available=False without reaching its inter-request sleep.

    3. ai_advisor.time.sleep — ai_advisor._fetch_with_backoff (used by the
       artlist, FRED, and EDGAR paths) sleeps between retries (exponential
       backoff, up to _FETCH_MAX_BACKOFF_TOTAL_WAIT_S = 8 s total).  Stubbing
       time.sleep on the ai_advisor module to a no-op makes backoff retries
       complete in microseconds.  Tests in test_backoff_termination.py patch
       ai_advisor.time.sleep themselves (same target) — their inner patch
       overrides this stub.  Note: lens_gdelt.py uses the module-level
       time.sleep directly, not ai_advisor.time.sleep, so the inter-request
       sleep in lens_gdelt is handled separately by the ConnectionError path
       terminating before that code is reached (tone fetch fails at the first
       requests.get call, returning _unavailable immediately).

    All stubs yield the honest available=False degraded form — same as before
    any producer was wired.  Tests exercising the real producer bodies must
    patch these seams themselves; their inner patch context takes precedence
    over this autouse outer patch.  In particular:

    - test_lens_gdelt.py and test_cycle2_lens_producers.py tests that need
      successful GDELT/FRED/EDGAR responses patch ``requests.get`` themselves
      (side_effect=[tone_resp, artlist_resp] etc.) — this inner patch
      temporarily overrides the autouse stub for the duration of the ``with``.
    - test_backoff_termination.py tests that need controlled time.sleep
      behaviour patch ``ai_advisor.time.sleep`` themselves, which overrides
      the autouse no-op for their duration.
    """
    from advisors import lens_technicals

    with (
        patch.object(lens_technicals, "_get_bars", return_value={}),
        # Fail all HTTP GETs instantly (GDELT artlist/tone, FRED, SEC EDGAR).
        # Inner patches on requests.get in producer-body tests override this.
        patch(
            "requests.get",
            side_effect=requests.exceptions.ConnectionError(
                "unit-test-stub — offline"
            ),
        ),
        # No-op backoff sleeps on ai_advisor._fetch_with_backoff so retries
        # exhaust in < 1 ms instead of sleeping up to 8 s total.
        patch("ai_advisor.time.sleep"),
    ):
        yield


@pytest.fixture()
def stub_network_producers():
    """Patches live-network section producers with honest stubs.

    Request this fixture in any test (or a module-scoped autouse fixture) that
    calls ``assemble_advisor_context`` and should not hit live GDELT/FRED/SEC
    endpoints.

    Note: lens_technicals._get_bars is already stubbed by the autouse
    ``_stub_technicals_get_bars`` fixture above — no need to re-patch it here.
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
