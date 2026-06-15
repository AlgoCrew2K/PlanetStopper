# TDD Handoff — derivatives-section-wiring
Plan: feature-plans/derivatives-section-wiring.md
Branch: feat/derivatives-section-wiring
Phase: red
HEAD SHA: 7ce9130

## Test Files
- tests/test_derivatives_section.py

## RED Run Result
```
============================= test session starts =============================
platform win32 -- Python 3.12.7, pytest-8.4.2, pluggy-1.6.0
2 workers [9 items]

PASSED  tests/test_derivatives_section.py::test_lazy_import_not_at_module_load
FAILED  tests/test_derivatives_section.py::test_success_path_shape
FAILED  tests/test_derivatives_section.py::test_success_path_payload_keys
FAILED  tests/test_derivatives_section.py::test_success_path_one_source
FAILED  tests/test_derivatives_section.py::test_success_path_citation_args
FAILED  tests/test_derivatives_section.py::test_failure_path_shape
FAILED  tests/test_derivatives_section.py::test_failure_path_reason_propagated
FAILED  tests/test_derivatives_section.py::test_missing_fred_key_propagated
FAILED  tests/test_derivatives_section.py::test_empty_as_of_date_yields_no_sources_but_still_available

8 failed, 1 passed
```

All 8 failures are against the stub in ai_advisor.py lines 522-556, which
unconditionally returns available=False without calling the proxy.
test_failure_path_shape now also fails correctly because it asserts
mock_proxy.called (the stub never invokes the proxy at all, even on the failure
path). test_lazy_import_not_at_module_load passes because the stub does not
import advisors.lens_options_proxy at module level — that invariant must
continue to hold after implementation.

The `_ensure_proxy_module` fixture is non-autouse (opt-in via parameter).
The CC-2 test does NOT request it so it sees the genuine sys.modules state.
All other tests request it to ensure the proxy module is resolvable regardless
of whether advisors/lens_options_proxy.py is on the branch.

## Call-site check (ai_advisor.py ~line 1208)
NO — `_build_derivatives_section()` is called at line 1186 inside the `context:
dict = {...}` dict literal within `assemble_advisor_context` (function body spans
lines 1087-1190). There is NO try/except wrapping the lens section calls. The
only guard in the function is a `raise ValueError` at lines 1134-1137 for missing
`symphony_id` when `scope == "symphony"`. The lens calls are bare dict values
with no exception isolation at the call site.

## What implementer must do
Replace _build_derivatives_section in ai_advisor.py (lines 522-556) with:

    def _build_derivatives_section(_data: object = None) -> dict:
        """Derivatives / volatility lens block — FRED VIXCLS + VXVCLS producer.

        Fetches VIX spot (VIXCLS) and 3-month VIX (VXVCLS) from FRED via the
        options-proxy lens producer.  Requires FRED_API_KEY in env; returns
        available=False with the proxy reason when the key is absent, FRED is
        down, or data is unavailable.

        Lazy-imports advisors.lens_options_proxy inside the function body (CC-2
        boundary) — never at module level.  D-1: reason is always
        type(exc).__name__ only (enforced by the proxy; this function propagates
        verbatim).

        Args:
            _data: unused; reserved for caller pre-injection.

        Returns:
            Lens block dict: {lens, available, payload, sources} on success;
            {lens, available, reason, payload, sources} on failure.
        """
        _lens = "derivatives"
        from advisors import lens_options_proxy as _proxy_mod  # CC-2: lazy import
        result = _proxy_mod._fetch_options_proxy()

        if not result.get("available"):
            return {
                "lens": _lens,
                "available": False,
                "reason": result.get("reason", "unavailable"),
                "payload": None,
                "sources": [],
            }

        as_of = result.get("as_of_date", "")
        citation = build_citation({
            "title": "VIXCLS / VXVCLS (CBOE Vol Index)",
            "url": "https://fred.stlouisfed.org/series/VIXCLS",
            "published": as_of,
            "lens": _lens,
        })
        sources = [citation] if citation is not None else []

        logger.info(
            "Derivatives lens: vix=%.2f regime=%s risk_read=%s as_of=%s",
            result.get("vix_level", 0),
            result.get("vix_term_structure", {}).get("regime", "unknown"),
            result.get("risk_read", "unknown"),
            as_of,
        )

        return {
            "lens": _lens,
            "available": True,
            "payload": {
                "vix_level": result.get("vix_level"),
                "vix_term_structure": result.get("vix_term_structure"),
                "risk_read": result.get("risk_read"),
                "as_of_date": as_of,
            },
            "sources": sources,
        }

## AC Coverage
- AC-1 (CC-2 lazy import): test_lazy_import_not_at_module_load
- AC-2 (success shape): test_success_path_shape, test_success_path_payload_keys
- AC-2 (one source): test_success_path_one_source
- AC-3 (citation args): test_success_path_citation_args
- AC-4 (failure shape + proxy called): test_failure_path_shape
- AC-4 (reason propagated): test_failure_path_reason_propagated
- AC-9e (KeyError propagated): test_missing_fred_key_propagated
- edge (empty as_of_date): test_empty_as_of_date_yields_no_sources_but_still_available
