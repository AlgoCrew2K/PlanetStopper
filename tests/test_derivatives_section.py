"""RED tests for _build_derivatives_section wiring (AC-9).

All tests except test_lazy_import_not_at_module_load should FAIL against
the current stub (which returns available=False unconditionally).

Dependency note
---------------
advisors/lens_options_proxy.py does not yet exist on this branch (it lives on
origin/feat/options-proxy, not yet merged to main).  The autouse fixture
``_ensure_proxy_module`` registers a minimal stub into sys.modules when the
real module is absent so that patching works correctly.  Once the real module
lands on the branch, the stub is bypassed automatically (the fixture checks
before registering).
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, call, patch

import pytest

_PROXY_MODULE_KEY = "advisors.lens_options_proxy"


@pytest.fixture()
def _ensure_proxy_module():
    """Register a minimal stub lens_options_proxy module if not already present.

    When the real advisors/lens_options_proxy.py has not yet been merged onto
    this branch, import attempts inside tests would raise ModuleNotFoundError.
    This fixture silently registers a MagicMock module so the tests can
    exercise the patch() path.  The stub does NOT satisfy any assertions on its
    own — the tests still patch _fetch_options_proxy with controlled return
    values; the stub just makes the module resolvable.

    When the real module exists (post-merge), this fixture is a no-op.

    Not autouse: test_lazy_import_not_at_module_load must NOT use this fixture
    so it can verify the genuine sys.modules state after ai_advisor import.
    """
    if _PROXY_MODULE_KEY not in sys.modules:
        stub = types.ModuleType(_PROXY_MODULE_KEY)
        stub._fetch_options_proxy = MagicMock(return_value={"available": False, "reason": "stub"})  # type: ignore[attr-defined]
        sys.modules[_PROXY_MODULE_KEY] = stub
        # Also hang it off the advisors package so `from advisors import
        # lens_options_proxy` resolves correctly.
        import advisors as _advisors_pkg
        setattr(_advisors_pkg, "lens_options_proxy", stub)
        injected = True
    else:
        injected = False

    yield

    if injected:
        sys.modules.pop(_PROXY_MODULE_KEY, None)
        try:
            import advisors as _advisors_pkg
            delattr(_advisors_pkg, "lens_options_proxy")
        except (AttributeError, ImportError):
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROXY_SUCCESS = {
    "available": True,
    "vix_level": 18.5,
    "vix_term_structure": {
        "spot": 18.5,
        "term_3m": 20.0,
        "ratio": 0.925,
        "spread": -1.5,
        "regime": "contango",
    },
    "risk_read": "neutral",
    "as_of_date": "2026-06-14",
    "source": "FRED (Federal Reserve Bank of St. Louis): VIXCLS and VXVCLS.",
}

_PROXY_FAIL_CONN = {
    "available": False,
    "reason": "ConnectionError",
    "source": "FRED (Federal Reserve Bank of St. Louis): VIXCLS and VXVCLS.",
}

_PROXY_FAIL_KEY = {
    "available": False,
    "reason": "KeyError",
    "source": "FRED (Federal Reserve Bank of St. Louis): VIXCLS and VXVCLS.",
}


# ---------------------------------------------------------------------------
# AC-1 / CC-2 — lazy import regression guard
# ---------------------------------------------------------------------------


def test_lazy_import_not_at_module_load() -> None:
    """lens_options_proxy must NOT be imported at ai_advisor module load time (CC-2).

    This test passes against the stub and must continue to pass after implementation.
    """
    # Ensure ai_advisor is imported (it will be, since we use it in other tests too).
    import ai_advisor  # noqa: F401

    assert "advisors.lens_options_proxy" not in sys.modules, (
        "advisors.lens_options_proxy was imported at module level in ai_advisor — "
        "violates CC-2 boundary (must be lazy-imported inside _build_derivatives_section)"
    )


# ---------------------------------------------------------------------------
# AC-2 — success path shape
# ---------------------------------------------------------------------------


def test_success_path_shape(_ensure_proxy_module) -> None:
    """On available=True proxy result, section returns correct top-level shape."""
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_SUCCESS,
    ):
        result = ai_advisor._build_derivatives_section()

    assert result["lens"] == "derivatives"
    assert result["available"] is True, (
        f"Expected available=True from proxy success; stub returns False. result={result!r}"
    )
    assert isinstance(result["payload"], dict)
    assert isinstance(result["sources"], list)


def test_success_path_payload_keys(_ensure_proxy_module) -> None:
    """Payload contains all four required keys on success."""
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_SUCCESS,
    ):
        result = ai_advisor._build_derivatives_section()

    payload = result["payload"]
    assert payload is not None, "payload is None on success path (stub returns stub dict)"
    assert "vix_level" in payload
    assert "vix_term_structure" in payload
    assert "risk_read" in payload
    assert "as_of_date" in payload


def test_success_path_one_source(_ensure_proxy_module) -> None:
    """Exactly one citation source on success with valid as_of_date."""
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_SUCCESS,
    ):
        result = ai_advisor._build_derivatives_section()

    assert len(result["sources"]) == 1, (
        f"Expected 1 source; stub returns 0 (never calls proxy/build_citation). "
        f"sources={result['sources']!r}"
    )


# ---------------------------------------------------------------------------
# AC-3 — citation args
# ---------------------------------------------------------------------------


def test_success_path_citation_args(_ensure_proxy_module) -> None:
    """build_citation is called with the correct structured args on success."""
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_SUCCESS,
    ), patch("ai_advisor.build_citation", wraps=ai_advisor.build_citation) as mock_bc:
        ai_advisor._build_derivatives_section()

    assert mock_bc.called, "build_citation was never called (stub bypasses proxy entirely)"
    args, _ = mock_bc.call_args
    citation_dict = args[0]
    assert citation_dict["title"] == "VIXCLS / VXVCLS (CBOE Vol Index)"
    assert citation_dict["url"] == "https://fred.stlouisfed.org/series/VIXCLS"
    # published must equal the as_of_date from the proxy result fixture.
    assert citation_dict["published"] == _PROXY_SUCCESS["as_of_date"]
    assert citation_dict["lens"] == "derivatives"


# ---------------------------------------------------------------------------
# AC-4 — failure path
# ---------------------------------------------------------------------------


def test_failure_path_shape(_ensure_proxy_module) -> None:
    """On available=False proxy result, section returns correct failure shape.

    Also asserts the mock was called — distinguishes the live implementation
    (which calls the proxy and propagates its result) from the stub (which
    hard-codes available=False without ever calling the proxy).
    """
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_FAIL_CONN,
    ) as mock_proxy:
        result = ai_advisor._build_derivatives_section()

    # The proxy mock must have been invoked — stub never calls it.
    assert mock_proxy.called, (
        "_fetch_options_proxy was never called — stub bypasses the proxy entirely. "
        "The implementation must call the proxy and propagate its result."
    )
    assert result["lens"] == "derivatives"
    assert result["available"] is False
    assert result["payload"] is None
    assert result["sources"] == []


def test_failure_path_reason_propagated(_ensure_proxy_module) -> None:
    """Proxy reason is propagated verbatim — not overwritten by the section."""
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_FAIL_CONN,
    ):
        result = ai_advisor._build_derivatives_section()

    assert result["reason"] == "ConnectionError", (
        f"Expected 'ConnectionError' (proxy value); "
        f"got {result.get('reason')!r} (stub hard-codes its own string)."
    )


# ---------------------------------------------------------------------------
# AC-6 / AC-9e — missing FRED_API_KEY handled by proxy (KeyError propagated)
# ---------------------------------------------------------------------------


def test_missing_fred_key_propagated(_ensure_proxy_module) -> None:
    """FRED_API_KEY absent → proxy returns KeyError reason → section propagates it."""
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_FAIL_KEY,
    ):
        result = ai_advisor._build_derivatives_section()

    assert result["available"] is False
    assert result["reason"] == "KeyError", (
        f"Expected 'KeyError' propagated from proxy; got {result.get('reason')!r}."
    )


# ---------------------------------------------------------------------------
# Edge case — empty as_of_date → no citation, but still available=True
# ---------------------------------------------------------------------------


def test_empty_as_of_date_yields_no_sources_but_still_available(_ensure_proxy_module) -> None:
    """Empty as_of_date → build_citation returns None → sources=[], available=True."""
    import ai_advisor

    proxy_result = dict(_PROXY_SUCCESS)
    proxy_result["as_of_date"] = ""

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=proxy_result,
    ):
        result = ai_advisor._build_derivatives_section()

    assert result["available"] is True, (
        "Stub returns available=False regardless; implementation must call proxy "
        "and set available=True when proxy returns available=True."
    )
    assert result["sources"] == [], (
        f"Empty as_of_date → build_citation(published='') returns None → sources must be []; "
        f"got {result['sources']!r}."
    )
