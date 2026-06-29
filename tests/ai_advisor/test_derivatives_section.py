"""
AC-9 — RED tests for derivatives section wiring (feat/derivatives-section-wiring).

Tests cover:
  AC-9a  Success path: proxy returns available=True → correct shape + payload + 1 source.
  AC-9b  Failure path: proxy returns available=False + reason → correct shape + propagation.
  AC-9c  build_citation called with exactly the correct args on success path.
  AC-9d  Lazy import: advisors.lens_options_proxy is NOT in sys.modules at module import time.
  AC-9e  Missing FRED_API_KEY: proxy returns available=False → section propagates without raising.

Additional:
  Stub marker: asserts _build_derivatives_section currently returns stub reason
               (flips GREEN after implementation — serves as before/after marker).
  Call-site guard: asserts assemble_advisor_context wraps _build_derivatives_section in
                   a try/except so a proxy failure cannot propagate to callers.

Mocking strategy
----------------
* Proxy calls: patch("advisors.lens_options_proxy._fetch_options_proxy")
* build_citation args test: patch("ai_advisor.build_citation")
* No live network calls.
* No DB writes (conftest._isolate_db handles isolation).
* Math engine and DB are never mocked — not used by _build_derivatives_section.

IMPORTANT: All tests in this file are RED (failing) because _build_derivatives_section
is still the cycle-1 stub. They will go GREEN only after the implementer wires the
real _fetch_options_proxy call.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

# Pre-import the proxy module so patch() can resolve it.
# The implemented _build_derivatives_section uses a lazy import inside its body;
# pre-importing here doesn't violate CC-2 — it just ensures the test-side patch
# target is resolvable before the function is called.
import advisors.lens_options_proxy  # noqa: F401

# ---------------------------------------------------------------------------
# Fixtures — proxy return shapes (from spec, never hardcoded computed values)
# ---------------------------------------------------------------------------


@pytest.fixture
def proxy_success_result() -> dict:
    """A well-formed _fetch_options_proxy success result matching the module docstring."""
    return {
        "available": True,
        "vix_level": 18.5,
        "vix_term_structure": {
            "spot": 18.5,
            "term_3m": 20.1,
            "ratio": 18.5 / 20.1,
            "spread": 18.5 - 20.1,
            "regime": "contango",
        },
        "risk_read": "neutral",
        "as_of_date": "2026-06-14",
        "source": "FRED (Federal Reserve Bank of St. Louis): VIXCLS and VXVCLS.",
    }


@pytest.fixture
def proxy_failure_result() -> dict:
    """A well-formed _fetch_options_proxy failure result matching the D-1 contract."""
    return {
        "available": False,
        "reason": "ConnectionError",
        "source": "FRED (Federal Reserve Bank of St. Louis): VIXCLS and VXVCLS.",
    }


@pytest.fixture
def proxy_key_missing_result() -> dict:
    """Proxy result for missing FRED_API_KEY — reason='KeyError' per proxy docstring."""
    return {
        "available": False,
        "reason": "KeyError",
        "source": "FRED (Federal Reserve Bank of St. Louis): VIXCLS and VXVCLS.",
    }


# ---------------------------------------------------------------------------
# AC-9a: Success path — shape, payload keys, one source
# ---------------------------------------------------------------------------


class TestDerivativesSectionSuccessPath:
    """AC-9a: proxy available=True → correct output shape + payload fields + 1 source."""

    def test_success_path_returns_dict(self, proxy_success_result):
        """_build_derivatives_section returns a dict on success.

        FAILS on the stub because the stub never calls _fetch_options_proxy.
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_success_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert isinstance(block, dict), "_build_derivatives_section must return a dict"

    def test_success_path_lens_key_is_derivatives(self, proxy_success_result):
        """block['lens'] == 'derivatives' on success path.

        FAILS on the stub (stub returns 'derivatives' but still fails because
        it never calls the proxy — so available is always False in stub).
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_success_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("lens") == "derivatives", (
            f"lens must be 'derivatives', got {block.get('lens')!r}"
        )

    def test_success_path_available_is_true(self, proxy_success_result):
        """block['available'] is True when proxy returns available=True.

        This is the primary RED gate — the stub always returns available=False.
        FAILS on the stub.
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_success_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is True, (
            "When proxy returns available=True, _build_derivatives_section must "
            "return available=True. "
            "FAILING because _build_derivatives_section is still the stub "
            "(returns available=False unconditionally)."
        )

    def test_success_path_payload_has_all_required_keys(self, proxy_success_result):
        """payload carries vix_level, vix_term_structure, risk_read, as_of_date.

        FAILS on the stub (available=False, no payload).
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_success_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is True, (
            "Precondition: available must be True for payload assertions"
        )
        payload = block.get("payload")
        assert isinstance(payload, dict), f"payload must be a dict, got {type(payload)}"
        for key in ("vix_level", "vix_term_structure", "risk_read", "as_of_date"):
            assert key in payload, (
                f"payload must carry key '{key}' (per AC-2 contract). "
                f"Got payload keys: {list(payload.keys())}"
            )

    def test_success_path_vix_level_is_positive_float(self, proxy_success_result):
        """payload.vix_level is a positive float — property assertion, not hardcoded.

        FAILS on the stub.
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_success_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is True, "precondition"
        vix_level = block["payload"].get("vix_level")
        assert isinstance(vix_level, (int, float)), (
            f"payload.vix_level must be a numeric type, got {type(vix_level)}"
        )
        assert vix_level > 0, f"payload.vix_level must be positive, got {vix_level!r}"

    def test_success_path_vix_term_structure_is_dict(self, proxy_success_result):
        """payload.vix_term_structure is a dict.

        FAILS on the stub.
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_success_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is True, "precondition"
        vts = block["payload"].get("vix_term_structure")
        assert isinstance(vts, dict), f"payload.vix_term_structure must be a dict, got {type(vts)}"

    def test_success_path_risk_read_is_string(self, proxy_success_result):
        """payload.risk_read is a non-empty string.

        FAILS on the stub.
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_success_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is True, "precondition"
        risk_read = block["payload"].get("risk_read")
        assert isinstance(risk_read, str) and risk_read.strip(), (
            f"payload.risk_read must be a non-empty string, got {risk_read!r}"
        )

    def test_success_path_sources_has_exactly_one_entry(self, proxy_success_result):
        """sources list has exactly one entry on success path (AC-3: one VIXCLS citation).

        FAILS on the stub.
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_success_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is True, "precondition"
        sources = block.get("sources", [])
        assert isinstance(sources, list), f"sources must be a list, got {type(sources)}"
        assert len(sources) == 1, (
            f"sources must have exactly 1 entry on success path (one VIXCLS citation), "
            f"got {len(sources)}: {sources!r}"
        )

    def test_success_path_source_entry_passes_build_citation(self, proxy_success_result):
        """The single source entry passes build_citation validation.

        FAILS on the stub.
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_success_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is True, "precondition"
        sources = block.get("sources", [])
        assert len(sources) == 1, "precondition: exactly 1 source"
        result = ai_advisor.build_citation(sources[0])
        assert result is not None, (
            f"The source entry failed build_citation validation: {sources[0]!r}. "
            f"Every source must carry title, url (http/https), published, lens."
        )

    def test_success_path_source_lens_is_derivatives(self, proxy_success_result):
        """The source citation carries lens='derivatives'.

        FAILS on the stub.
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_success_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is True, "precondition"
        sources = block.get("sources", [])
        assert len(sources) == 1, "precondition"
        assert sources[0].get("lens") == "derivatives", (
            f"source citation 'lens' must be 'derivatives', got {sources[0].get('lens')!r}"
        )

    def test_success_path_source_url_is_fred_vixcls(self, proxy_success_result):
        """The source URL is the FRED VIXCLS page URL (AC-3).

        FAILS on the stub.
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_success_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is True, "precondition"
        sources = block.get("sources", [])
        assert len(sources) == 1, "precondition"
        url = sources[0].get("url", "")
        assert "fred.stlouisfed.org" in url and "VIXCLS" in url, (
            f"source URL must be the FRED VIXCLS page, got {url!r}. "
            f"AC-3 requires: url='https://fred.stlouisfed.org/series/VIXCLS'"
        )

    def test_success_path_as_of_date_propagated_to_source_published(self, proxy_success_result):
        """The source 'published' field equals proxy as_of_date (AC-3).

        FAILS on the stub.
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_success_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is True, "precondition"
        sources = block.get("sources", [])
        assert len(sources) == 1, "precondition"
        as_of = proxy_success_result["as_of_date"]
        published = sources[0].get("published", "")
        assert published == as_of, (
            f"source 'published' must equal proxy as_of_date={as_of!r}, got {published!r}"
        )


# ---------------------------------------------------------------------------
# AC-9b: Failure path — reason propagation, payload=None, sources=[]
# ---------------------------------------------------------------------------


class TestDerivativesSectionFailurePath:
    """AC-9b: proxy available=False + reason → correct output shape with reason propagated."""

    def test_failure_path_available_is_false(self, proxy_failure_result):
        """block['available'] is False when proxy returns available=False.

        This test passes on the stub BUT is still part of the RED suite — the
        stub passes for the wrong reason (it ignores the proxy entirely).
        We pair it with reason-propagation tests that will fail on the stub.
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_failure_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is False, (
            "When proxy returns available=False, _build_derivatives_section must "
            "return available=False."
        )

    def test_failure_path_reason_is_propagated_from_proxy(self, proxy_failure_result):
        """The proxy's reason is propagated verbatim (AC-4 / D-1 ownership).

        FAILS on the stub: stub returns its own hardcoded reason, not the proxy's.
        The proxy already satisfies D-1; the section must not construct a new reason.
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_failure_result,
        ):
            block = ai_advisor._build_derivatives_section()

        proxy_reason = proxy_failure_result["reason"]
        block_reason = block.get("reason", "")
        assert block_reason == proxy_reason, (
            f"_build_derivatives_section must propagate the proxy's reason verbatim. "
            f"Expected reason={proxy_reason!r}, got {block_reason!r}. "
            f"FAILING because the stub returns its own reason, not the proxy's."
        )

    def test_failure_path_payload_is_none(self, proxy_failure_result):
        """payload is None on failure path (AC-4).

        FAILS on the stub if stub doesn't set payload=None (it does in this case,
        but the test is needed to guard against regressions).
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_failure_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("payload") is None, (
            f"On failure path, payload must be None. Got {block.get('payload')!r}"
        )

    def test_failure_path_sources_is_empty_list(self, proxy_failure_result):
        """sources is [] on failure path (AC-4 / CC-4).

        FAILS on the stub if stub doesn't set sources=[].
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_failure_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("sources") == [], (
            f"On failure path, sources must be []. Got {block.get('sources')!r}"
        )

    def test_failure_path_does_not_construct_reason_from_exception(self):
        """The section does not construct its own reason string from any exception.

        AC-5 / D-1: _build_derivatives_section must not call str(exc) or
        type(exc).__name__ in its own body. The proxy owns D-1.

        Tested by injecting a proxy result with a known reason and verifying
        the section returns that exact string, not a reconstructed version.
        FAILS on the stub because stub uses its own hardcoded reason.
        """
        import ai_advisor

        sentinel_reason = "SentinelExceptionTypeForAC5Test"
        proxy_result = {
            "available": False,
            "reason": sentinel_reason,
            "source": "FRED.",
        }

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_result,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("reason") == sentinel_reason, (
            f"_build_derivatives_section must propagate the proxy's reason exactly. "
            f"Expected {sentinel_reason!r}, got {block.get('reason')!r}. "
            f"AC-5: the section must not re-construct the reason from str(exc)."
        )


# ---------------------------------------------------------------------------
# AC-9c: build_citation called with correct args on success path
# ---------------------------------------------------------------------------


class TestDerivativesSectionBuildCitationArgs:
    """AC-9c: build_citation is called with exactly the right dict on success path."""

    def test_build_citation_called_with_correct_title(self, proxy_success_result):
        """build_citation receives title='VIXCLS / VXVCLS (CBOE Vol Index)'.

        FAILS on the stub (stub never calls build_citation).
        """
        import ai_advisor

        with (
            patch(
                "advisors.lens_options_proxy._fetch_options_proxy",
                return_value=proxy_success_result,
            ),
            patch("ai_advisor.build_citation", wraps=ai_advisor.build_citation) as mock_bc,
        ):
            ai_advisor._build_derivatives_section()

        assert mock_bc.called, (
            "_build_derivatives_section must call build_citation on success path. "
            "FAILING because stub never calls build_citation."
        )
        citation_arg = mock_bc.call_args[0][0]
        assert citation_arg.get("title") == "VIXCLS / VXVCLS (CBOE Vol Index)", (
            f"build_citation title must be 'VIXCLS / VXVCLS (CBOE Vol Index)', "
            f"got {citation_arg.get('title')!r}"
        )

    def test_build_citation_called_with_correct_url(self, proxy_success_result):
        """build_citation receives url='https://fred.stlouisfed.org/series/VIXCLS'.

        FAILS on the stub.
        """
        import ai_advisor

        with (
            patch(
                "advisors.lens_options_proxy._fetch_options_proxy",
                return_value=proxy_success_result,
            ),
            patch("ai_advisor.build_citation", wraps=ai_advisor.build_citation) as mock_bc,
        ):
            ai_advisor._build_derivatives_section()

        assert mock_bc.called, "precondition: build_citation must be called"
        citation_arg = mock_bc.call_args[0][0]
        assert citation_arg.get("url") == "https://fred.stlouisfed.org/series/VIXCLS", (
            f"build_citation url must be 'https://fred.stlouisfed.org/series/VIXCLS', "
            f"got {citation_arg.get('url')!r}"
        )

    def test_build_citation_called_with_correct_published(self, proxy_success_result):
        """build_citation receives published=proxy['as_of_date'].

        FAILS on the stub.
        """
        import ai_advisor

        with (
            patch(
                "advisors.lens_options_proxy._fetch_options_proxy",
                return_value=proxy_success_result,
            ),
            patch("ai_advisor.build_citation", wraps=ai_advisor.build_citation) as mock_bc,
        ):
            ai_advisor._build_derivatives_section()

        assert mock_bc.called, "precondition: build_citation must be called"
        citation_arg = mock_bc.call_args[0][0]
        expected_published = proxy_success_result["as_of_date"]
        assert citation_arg.get("published") == expected_published, (
            f"build_citation published must equal proxy as_of_date={expected_published!r}, "
            f"got {citation_arg.get('published')!r}"
        )

    def test_build_citation_called_with_correct_lens(self, proxy_success_result):
        """build_citation receives lens='derivatives'.

        FAILS on the stub.
        """
        import ai_advisor

        with (
            patch(
                "advisors.lens_options_proxy._fetch_options_proxy",
                return_value=proxy_success_result,
            ),
            patch("ai_advisor.build_citation", wraps=ai_advisor.build_citation) as mock_bc,
        ):
            ai_advisor._build_derivatives_section()

        assert mock_bc.called, "precondition: build_citation must be called"
        citation_arg = mock_bc.call_args[0][0]
        assert citation_arg.get("lens") == "derivatives", (
            f"build_citation lens must be 'derivatives', got {citation_arg.get('lens')!r}"
        )

    def test_build_citation_none_result_yields_empty_sources(self, proxy_success_result):
        """When build_citation returns None (malformed input), sources is [] (AC-3).

        FAILS on the stub (stub never calls build_citation, always returns sources=[]).
        The real implementation must return available=True with sources=[] when
        build_citation returns None — not downgrade to available=False.
        """
        import ai_advisor

        # Force build_citation to return None to simulate malformed proxy output
        with (
            patch(
                "advisors.lens_options_proxy._fetch_options_proxy",
                return_value=proxy_success_result,
            ),
            patch("ai_advisor.build_citation", return_value=None),
        ):
            block = ai_advisor._build_derivatives_section()

        # Section must still return available=True (the data was fetched OK)
        assert block.get("available") is True, (
            "When build_citation returns None, _build_derivatives_section must "
            "still return available=True (data was fetched). "
            "FAILING because stub returns available=False."
        )
        assert block.get("sources") == [], (
            f"When build_citation returns None, sources must be []. Got {block.get('sources')!r}"
        )


# ---------------------------------------------------------------------------
# AC-9d: Lazy import — advisors.lens_options_proxy NOT imported at module load
# ---------------------------------------------------------------------------


class TestDerivativesSectionLazyImport:
    """AC-9d: The lazy import does NOT fire at module import time."""

    def test_lens_options_proxy_not_in_sys_modules_after_ai_advisor_import(self):
        """Import ai_advisor without triggering advisors.lens_options_proxy import.

        CC-2: the proxy module must not be imported at ai_advisor module load time.
        It is only allowed to be imported lazily inside the function body.

        This test verifies the CC-2 boundary is respected at import time.
        FAILS if _build_derivatives_section has a module-level import statement.

        Note: this test checks the state BEFORE _build_derivatives_section is called.
        If the module was already imported by a prior test (or by the function having
        been called in this session), we patch sys.modules temporarily to simulate
        a fresh import environment.
        """
        # We need to check that importing ai_advisor does NOT cause
        # lens_options_proxy to be imported. We simulate this by removing the
        # module from sys.modules if present, reimporting ai_advisor, and
        # verifying lens_options_proxy is still absent.

        # Save current state
        proxy_key = "advisors.lens_options_proxy"
        ai_advisor_key = "ai_advisor"

        # Remove both from sys.modules to get a fresh slate
        ai_advisor_backup = sys.modules.pop(ai_advisor_key, None)
        proxy_backup = sys.modules.pop(proxy_key, None)

        try:
            # Import ai_advisor fresh — must NOT pull in lens_options_proxy
            import ai_advisor  # noqa: F401 (re-import)

            assert proxy_key not in sys.modules, (
                "advisors.lens_options_proxy was imported at ai_advisor module "
                "load time — CC-2 violation. The import must be lazy (inside "
                "_build_derivatives_section body only). "
                "FAILING if _build_derivatives_section has a module-level import."
            )
        finally:
            # Restore sys.modules to original state
            if ai_advisor_backup is not None:
                sys.modules[ai_advisor_key] = ai_advisor_backup
            if proxy_backup is not None:
                sys.modules[proxy_key] = proxy_backup

    def test_lazy_import_fires_inside_function_not_before_via_ast(self):
        """The lazy import of lens_options_proxy is INSIDE the function body (AST check).

        Verifies the CC-2 boundary structurally: the import statement for
        lens_options_proxy must be inside _build_derivatives_section, not at
        module level in ai_advisor.py.

        This is a structural companion to the runtime module-load test. It checks
        the AST of ai_advisor.py to verify the import is inside the function.

        FAILS on the stub because the stub has NO import of lens_options_proxy
        anywhere (it simply returns a hardcoded dict). After implementation,
        this test will verify the lazy import IS inside the function body.
        """
        import ast
        import pathlib

        ai_advisor_path = pathlib.Path(__file__).parents[2] / "ai_advisor.py"
        source = ai_advisor_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Find _build_derivatives_section function definition
        derivatives_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_build_derivatives_section":
                derivatives_fn = node
                break

        assert derivatives_fn is not None, "_build_derivatives_section not found in ai_advisor.py"

        # Look for an import of lens_options_proxy inside the function body
        def _has_lazy_import(fn_node: ast.FunctionDef) -> bool:
            """True if fn_node contains an import of lens_options_proxy."""
            for node in ast.walk(fn_node):
                if isinstance(node, ast.ImportFrom):
                    # from advisors import lens_options_proxy ...
                    if node.module == "advisors" and any(
                        alias.name == "lens_options_proxy" for alias in node.names
                    ):
                        return True
                elif isinstance(node, ast.Import):
                    # import advisors.lens_options_proxy
                    if any("lens_options_proxy" in alias.name for alias in node.names):
                        return True
            return False

        has_import = _has_lazy_import(derivatives_fn)
        assert has_import, (
            "_build_derivatives_section does not contain a lazy import of "
            "advisors.lens_options_proxy inside its function body. "
            "AC-1 requires: `from advisors import lens_options_proxy as _proxy_mod` "
            "inside the function body (CC-2 lazy import pattern). "
            "FAILING because the stub has no import — it returns a hardcoded dict."
        )

        # Also verify the import is NOT at module level in ai_advisor.py
        # (check top-level imports only, not inside functions)
        module_level_imports_lens_proxy = False
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                if node.module and "lens_options_proxy" in node.module:
                    module_level_imports_lens_proxy = True
            elif isinstance(node, ast.Import):
                if any("lens_options_proxy" in alias.name for alias in node.names):
                    module_level_imports_lens_proxy = True

        assert not module_level_imports_lens_proxy, (
            "lens_options_proxy is imported at module level in ai_advisor.py — "
            "this violates CC-2. The import must be lazy (inside function body only)."
        )


# ---------------------------------------------------------------------------
# AC-9e: Missing FRED_API_KEY — proxy returns available=False, section propagates
# ---------------------------------------------------------------------------


class TestDerivativesSectionMissingFredKey:
    """AC-9e: Missing FRED_API_KEY → proxy returns available=False → section propagates."""

    def test_missing_fred_key_propagated_without_raising(self, proxy_key_missing_result):
        """Proxy returns available=False, reason='KeyError' → section returns same.

        AC-6: _build_derivatives_section does NOT perform its own FRED_API_KEY env
        check — the proxy owns that detection. The section only propagates the result.
        This test verifies the propagation without raising.

        FAILS on the stub: stub propagates its own hardcoded reason, not 'KeyError'.
        """
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=proxy_key_missing_result,
        ):
            # Must not raise
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is False, (
            "When proxy signals FRED_API_KEY absent (available=False), "
            "_build_derivatives_section must return available=False."
        )
        assert block.get("reason") == "KeyError", (
            f"Proxy reason 'KeyError' must be propagated verbatim. "
            f"Got {block.get('reason')!r}. "
            f"AC-6: the section must not add its own env guard."
        )

    def test_missing_fred_key_does_not_raise_even_without_env_var(self):
        """Calling with no FRED_API_KEY in env does not raise.

        _build_derivatives_section must degrade gracefully via proxy.
        AC-6: no env check in the section itself.
        FAILS on the stub if the stub raises unexpectedly (it does not,
        so this test passes on the stub — pairing with reason test for RED coverage).
        """
        import os

        import ai_advisor

        env_without_fred = {k: v for k, v in os.environ.items() if k != "FRED_API_KEY"}

        key_missing_result = {
            "available": False,
            "reason": "KeyError",
            "source": "FRED.",
        }

        with (
            patch.dict(os.environ, env_without_fred, clear=True),
            patch(
                "advisors.lens_options_proxy._fetch_options_proxy",
                return_value=key_missing_result,
            ),
        ):
            # Must not raise
            block = ai_advisor._build_derivatives_section()

        assert isinstance(block, dict), "_build_derivatives_section must return a dict, not raise"
        assert block.get("available") is False, (
            "With FRED_API_KEY absent, section must return available=False"
        )

    def test_section_does_not_add_env_check_for_fred_key(self, proxy_success_result):
        """The section does NOT independently gate on FRED_API_KEY.

        AC-6: if proxy returns available=True, the section must use that result
        regardless of whether FRED_API_KEY is in the section's own env check.
        The proxy owns FRED_API_KEY detection.

        FAILS on the stub (returns available=False regardless of proxy result).
        """
        import os

        import ai_advisor

        # Simulate env without FRED_API_KEY but proxy says available=True
        # (e.g., the proxy used a cached result or a different mechanism).
        env_without_fred = {k: v for k, v in os.environ.items() if k != "FRED_API_KEY"}

        with (
            patch.dict(os.environ, env_without_fred, clear=True),
            patch(
                "advisors.lens_options_proxy._fetch_options_proxy",
                return_value=proxy_success_result,
            ),
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is True, (
            "When proxy returns available=True, _build_derivatives_section must "
            "trust the proxy result — it must NOT add its own FRED_API_KEY guard. "
            "FAILING because stub always returns available=False."
        )


# ---------------------------------------------------------------------------
# Edge case: empty as_of_date → build_citation returns None → sources=[]
# ---------------------------------------------------------------------------


class TestDerivativesSectionEdgeCases:
    """Edge cases from the feature plan."""

    def test_empty_as_of_date_yields_empty_sources_but_available_true(self, proxy_success_result):
        """When as_of_date is empty, build_citation returns None → sources=[].

        The section must still return available=True (data was fetched OK).
        build_citation requires 'published' non-empty — empty as_of_date → None.
        This is the edge case from the feature plan: AC-3 / build_citation None path.

        FAILS on the stub (available=False regardless).
        """
        import ai_advisor

        # Make a copy with empty as_of_date
        result_empty_date = dict(proxy_success_result, as_of_date="")

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=result_empty_date,
        ):
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is True, (
            "Empty as_of_date must not downgrade the section to available=False. "
            "Data was fetched successfully; only the citation is dropped. "
            "FAILING because stub returns available=False."
        )
        assert block.get("sources") == [], (
            f"When build_citation returns None (empty published), sources must be []. "
            f"Got {block.get('sources')!r}"
        )

    def test_missing_vix_term_structure_in_proxy_result_does_not_raise(self, proxy_success_result):
        """When vix_term_structure is missing from proxy result, payload carries None.

        Defensive: the section must not raise AttributeError or KeyError if the
        proxy omits vix_term_structure.
        FAILS on the stub (available=False, doesn't get to payload construction).
        """
        import ai_advisor

        result_no_vts = {k: v for k, v in proxy_success_result.items() if k != "vix_term_structure"}

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            return_value=result_no_vts,
        ):
            # Must not raise
            block = ai_advisor._build_derivatives_section()

        assert block.get("available") is True, "precondition"
        payload = block.get("payload", {})
        # vix_term_structure key should be present but value is None
        assert "vix_term_structure" in payload, (
            "payload must carry vix_term_structure key even when proxy omits it"
        )
        assert payload["vix_term_structure"] is None, (
            f"payload.vix_term_structure must be None when proxy omits the key, "
            f"got {payload['vix_term_structure']!r}"
        )


# ---------------------------------------------------------------------------
# Call-site guard: assemble_advisor_context wraps _build_derivatives_section
# ---------------------------------------------------------------------------


class TestAssembleAdvisorContextCallSiteGuard:
    """assemble_advisor_context must not propagate proxy exceptions to callers.

    DE-ADVISOR-LATENCY (AC-1): lenses are now served from the MARKET_LENS_CACHE
    bundle — assemble_advisor_context no longer calls _build_derivatives_section
    directly.  Exception isolation of the builder is covered by the producer path:
    test_patch_provenance_v2_d1_builder_raises_market_prism_unchanged (T6 in
    tests/prism_scheduler/test_patch_provenance.py) verifies that when
    _build_derivatives_section raises inside _patch_provenance the exception is
    caught and does not propagate.

    The structural AST guard on the old assemble_advisor_context call-site was
    removed (stale-by-intent) because the call-site itself was removed by this PR.
    The runtime guard test below remains: it verifies the context assembly path
    degrades gracefully if the proxy somehow fires.
    """

    def test_assemble_advisor_context_does_not_propagate_proxy_exception_at_runtime(
        self, tmp_path, monkeypatch
    ):
        """A proxy RuntimeError inside _build_derivatives_section must NOT propagate.

        Runtime companion to the AST-structural test above. After the guard is
        added (GREEN), this test verifies the guard works at runtime too.

        FAILS until both the stub is replaced AND the call-site guard is in place:
        - With stub: proxy never called → no RuntimeError → asserts on 'derivatives'
          key being available=False with stub reason (currently passes, but the stub
          reason test will pin the expected behavior).
        - With live implementation + no guard: RuntimeError propagates → pytest.fail().
        - With live implementation + guard: passes and verifies degradation.

        This test is a runtime RED gate for the guard requirement.
        """
        import ai_advisor
        import database

        db_path = str(tmp_path / "guard_rt_test.db")
        monkeypatch.setenv("DB_PATH", db_path)
        database.init_db()

        def _proxy_raises():
            raise RuntimeError("Simulated proxy failure — must not propagate to caller")

        # After implementation, the proxy IS called and raises RuntimeError.
        # Without a guard in assemble_advisor_context, the error propagates.
        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            side_effect=_proxy_raises,
        ):
            try:
                context = ai_advisor.assemble_advisor_context(scope="global")
                # Guard exists (GREEN) — verify degradation shape
                assert "derivatives" in context, (
                    "assemble_advisor_context must include 'derivatives' key even "
                    "when the proxy raises — the section must degrade, not disappear."
                )
            except RuntimeError as exc:
                pytest.fail(
                    f"assemble_advisor_context propagated a proxy RuntimeError to the "
                    f"caller: {exc!r}. "
                    f"The call-site at ai_advisor.py:1208 has no try/except guard. "
                    f"RED: fails until the implementer adds the exception guard."
                )


# ---------------------------------------------------------------------------
# Defense-in-depth: exception isolation — _build_derivatives_section caller guard
# ---------------------------------------------------------------------------

_FAKE_SECRET = "api_key=SECRET_TOKEN_12345"

_EXCEPTION_CLASSES = [
    RuntimeError,
    ValueError,
    OSError,
    ConnectionError,
]


def _make_raising_proxy(exc_class: type, secret: str = _FAKE_SECRET):
    """Return a callable that raises exc_class with a message containing secret."""

    def _raise():
        raise exc_class(f"Request failed: {secret}")

    return _raise


class TestDerivativesSectionExceptionIsolation:
    """_build_derivatives_section must degrade gracefully when proxy raises.

    These tests assert:

    1. When _fetch_options_proxy RAISES (monkeypatched), _build_derivatives_section
       returns available=False with an informative reason that contains ONLY
       type(exc).__name__ — never str(exc) and never any secret embedded in the
       exception message.

    2. The secret string embedded in the exception is NEVER present in the returned
       reason (D-1 hard rule: no credential leakage even from exception payloads).

    3. The returned dict has the full honest-unavailable shape:
       {lens, available, reason, payload, sources} matching the existing contract.

    4. The exception does NOT propagate — _build_derivatives_section catches it
       internally.

    No network. No live FRED calls. No DB.
    """

    @pytest.mark.parametrize("exc_class", _EXCEPTION_CLASSES)
    def test_returns_available_false_when_proxy_raises(self, exc_class):
        """available=False returned (not raised) when _fetch_options_proxy raises."""
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            side_effect=_make_raising_proxy(exc_class),
        ):
            result = ai_advisor._build_derivatives_section()

        assert isinstance(result, dict), (
            "_build_derivatives_section must return dict, never propagate"
        )
        assert result.get("available") is False, (
            f"available must be False when proxy raises, got {result.get('available')!r}"
        )

    @pytest.mark.parametrize("exc_class", _EXCEPTION_CLASSES)
    def test_reason_is_exc_type_name_only(self, exc_class):
        """reason contains ONLY type(exc).__name__ — not str(exc), not the secret."""
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            side_effect=_make_raising_proxy(exc_class),
        ):
            result = ai_advisor._build_derivatives_section()

        reason = result.get("reason", "")
        assert isinstance(reason, str) and reason, (
            f"reason must be a non-empty string, got {reason!r}"
        )
        # D-1: reason must be the bare class name
        assert reason == exc_class.__name__, (
            f"reason must equal type(exc).__name__ == {exc_class.__name__!r}, got {reason!r}"
        )

    @pytest.mark.parametrize("exc_class", _EXCEPTION_CLASSES)
    def test_reason_never_contains_secret(self, exc_class):
        """Secret embedded in str(exc) must NEVER appear in the returned reason."""
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            side_effect=_make_raising_proxy(exc_class, secret=_FAKE_SECRET),
        ):
            result = ai_advisor._build_derivatives_section()

        reason = result.get("reason", "")
        assert _FAKE_SECRET not in reason, f"Secret string leaked into reason: {reason!r}"

    @pytest.mark.parametrize("exc_class", _EXCEPTION_CLASSES)
    def test_returned_shape_matches_honest_unavailable_contract(self, exc_class):
        """Returned dict has all required keys in the honest-unavailable shape."""
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            side_effect=_make_raising_proxy(exc_class),
        ):
            result = ai_advisor._build_derivatives_section()

        assert result.get("lens") == "derivatives", (
            f"lens key must be 'derivatives', got {result.get('lens')!r}"
        )
        assert result.get("available") is False
        assert "reason" in result, "reason key must be present on available=False"
        assert result.get("payload") is None, (
            f"payload must be None when available=False, got {result.get('payload')!r}"
        )
        assert result.get("sources") == [], (
            f"sources must be [] when available=False, got {result.get('sources')!r}"
        )

    def test_exception_does_not_propagate(self):
        """Exception from proxy must not propagate out of _build_derivatives_section."""
        import ai_advisor

        with patch(
            "advisors.lens_options_proxy._fetch_options_proxy",
            side_effect=_make_raising_proxy(RuntimeError),
        ):
            # If this raises, the test fails — that's the assertion.
            try:
                ai_advisor._build_derivatives_section()
            except Exception as exc:  # noqa: BLE001
                pytest.fail(
                    f"_build_derivatives_section propagated an exception "
                    f"instead of degrading to available=False: {exc!r}"
                )
