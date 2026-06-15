# TDD Handoff — derivatives-section-wiring
Plan: feature-plans/derivatives-section-wiring.md
Branch: feat/derivatives-section-wiring
Phase: red
HEAD SHA: (update after final RED commit)

## Test Files
- tests/test_derivatives_section.py (root-level; 9 tests, 8 RED — prior test-writer)
- tests/ai_advisor/test_derivatives_section.py (32 tests, 23 RED — comprehensive coverage)

## RED Run Summary
Both test files together: 31 failing tests, 10 passing.

Canonical RED test run (tests/test_derivatives_section.py):
  8 failed, 1 passed — against the stub in ai_advisor.py ~lines 544-556
  which unconditionally returns available=False without calling the proxy.

Extended RED test run (tests/ai_advisor/test_derivatives_section.py):
  23 failed, 9 passed

All failures are against the stub. Tests that pass on the stub:
- Shape checks (lens=derivatives, available=False on failure path) — correct stub behavior
- CC-2 module-load check (stub does not import lens_options_proxy at module level)
- Stub marker test (intentional pass, flips RED after implementation until guard added)

## Call-site check (ai_advisor.py ~line 1208)
NO — `_build_derivatives_section()` is called inside the `context: dict = {...}` dict
literal within `assemble_advisor_context` (function body spans lines ~1087-1212).
There is NO try/except wrapping the lens section calls. The only guard is a `raise
ValueError` for missing `symphony_id` when `scope == "symphony"`.

The AST structural test `test_derivatives_call_site_is_wrapped_in_try_except` is RED
and will stay RED until the implementer adds a try/except around the lens block.

## What implementer must do

### 1. Replace _build_derivatives_section body (ai_advisor.py lines 544-556)

```python
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
```

### 2. Add try/except guard in assemble_advisor_context

The lens-section calls starting at line ~1206 (technicals, sentiment, derivatives, macro,
fundamentals) are inside a bare dict literal with no exception isolation. The test
`test_derivatives_call_site_is_wrapped_in_try_except` will FAIL until each lens call
is wrapped. Minimum change: wrap the lens-section calls in try/except that returns
a degraded lens block on failure.

### 3. No other files to change

- advisors/lens_options_proxy.py: already on branch, DO NOT change
- CLAUDE.md key-files row for lens_options_proxy.py: doc-writer's job (AC-8)
- lens_pipeline.py, lens_warehouse.py, app.py, templates: OUT OF SCOPE

## AC Coverage
- AC-1 (CC-2 lazy import): test_lazy_import_not_at_module_load (root), test_lens_options_proxy_not_in_sys_modules_after_ai_advisor_import (ai_advisor/), test_lazy_import_fires_inside_function_not_before_via_ast (ai_advisor/)
- AC-2 (success shape + payload keys): test_success_path_shape, test_success_path_payload_keys, test_success_path_* (ai_advisor/)
- AC-2 (one source): test_success_path_one_source, test_success_path_sources_has_exactly_one_entry
- AC-3 (citation args): test_success_path_citation_args, TestDerivativesSectionBuildCitationArgs/*
- AC-4 (failure shape + proxy called): test_failure_path_shape
- AC-4 (reason propagated): test_failure_path_reason_propagated, test_failure_path_reason_is_propagated_from_proxy
- AC-5 (D-1 no str(exc) in section): test_failure_path_does_not_construct_reason_from_exception
- AC-6 / AC-9e (KeyError propagated): test_missing_fred_key_propagated, TestDerivativesSectionMissingFredKey/*
- AC-9 (call-site guard): test_derivatives_call_site_is_wrapped_in_try_except (AST)
- edge (empty as_of_date): test_empty_as_of_date_yields_no_sources_but_still_available, test_empty_as_of_date_yields_empty_sources_but_available_true
- edge (missing vix_term_structure): test_missing_vix_term_structure_in_proxy_result_does_not_raise
