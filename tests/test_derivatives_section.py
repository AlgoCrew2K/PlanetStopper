"""RED tests for _build_derivatives_section wiring (AC-9).

All tests except test_lazy_import_not_at_module_load should FAIL against
the current stub (which returns available=False unconditionally).
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, call, patch

import pytest


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


def test_success_path_shape() -> None:
    """On available=True proxy result, section returns correct top-level shape."""
    import advisors.lens_options_proxy  # ensure module importable before patching
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_SUCCESS,
    ):
        result = ai_advisor._build_derivatives_section()

    assert result["lens"] == "derivatives"
    assert result["available"] is True
    assert isinstance(result["payload"], dict)
    assert isinstance(result["sources"], list)


def test_success_path_payload_keys() -> None:
    """Payload contains all four required keys on success."""
    import advisors.lens_options_proxy
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_SUCCESS,
    ):
        result = ai_advisor._build_derivatives_section()

    payload = result["payload"]
    assert "vix_level" in payload
    assert "vix_term_structure" in payload
    assert "risk_read" in payload
    assert "as_of_date" in payload


def test_success_path_one_source() -> None:
    """Exactly one citation source on success with valid as_of_date."""
    import advisors.lens_options_proxy
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_SUCCESS,
    ):
        result = ai_advisor._build_derivatives_section()

    assert len(result["sources"]) == 1


# ---------------------------------------------------------------------------
# AC-3 — citation args
# ---------------------------------------------------------------------------


def test_success_path_citation_args() -> None:
    """build_citation is called with the correct structured args on success."""
    import advisors.lens_options_proxy
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_SUCCESS,
    ), patch("ai_advisor.build_citation", wraps=ai_advisor.build_citation) as mock_bc:
        ai_advisor._build_derivatives_section()

    assert mock_bc.called, "build_citation was never called"
    args, _ = mock_bc.call_args
    citation_dict = args[0]
    assert citation_dict["title"] == "VIXCLS / VXVCLS (CBOE Vol Index)"
    assert citation_dict["url"] == "https://fred.stlouisfed.org/series/VIXCLS"
    assert citation_dict["published"] == "2026-06-14"
    assert citation_dict["lens"] == "derivatives"


# ---------------------------------------------------------------------------
# AC-4 — failure path
# ---------------------------------------------------------------------------


def test_failure_path_shape() -> None:
    """On available=False proxy result, section returns correct failure shape."""
    import advisors.lens_options_proxy
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_FAIL_CONN,
    ):
        result = ai_advisor._build_derivatives_section()

    assert result["lens"] == "derivatives"
    assert result["available"] is False
    assert result["payload"] is None
    assert result["sources"] == []


def test_failure_path_reason_propagated() -> None:
    """Proxy reason is propagated verbatim — not overwritten by the section."""
    import advisors.lens_options_proxy
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_FAIL_CONN,
    ):
        result = ai_advisor._build_derivatives_section()

    assert result["reason"] == "ConnectionError"


# ---------------------------------------------------------------------------
# AC-6 / AC-9e — missing FRED_API_KEY handled by proxy (KeyError propagated)
# ---------------------------------------------------------------------------


def test_missing_fred_key_propagated() -> None:
    """FRED_API_KEY absent → proxy returns KeyError reason → section propagates it."""
    import advisors.lens_options_proxy
    import ai_advisor

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=_PROXY_FAIL_KEY,
    ):
        result = ai_advisor._build_derivatives_section()

    assert result["available"] is False
    assert result["reason"] == "KeyError"


# ---------------------------------------------------------------------------
# Edge case — empty as_of_date → no citation, but still available=True
# ---------------------------------------------------------------------------


def test_empty_as_of_date_yields_no_sources_but_still_available() -> None:
    """Empty as_of_date → build_citation returns None → sources=[], available=True."""
    import advisors.lens_options_proxy
    import ai_advisor

    proxy_result = dict(_PROXY_SUCCESS)
    proxy_result["as_of_date"] = ""

    with patch(
        "advisors.lens_options_proxy._fetch_options_proxy",
        return_value=proxy_result,
    ):
        result = ai_advisor._build_derivatives_section()

    assert result["available"] is True
    assert result["sources"] == []
