# TDD Handoff — lens-data-gdelt-sentiment
Plan: feature-plans/lens-data-gdelt-sentiment.md
Branch: feat/lens-gdelt-tone
Phase: red

## Test Files
- tests/ai_advisor/test_lens_gdelt.py (RED — comprehensive coverage)

## Fixture Files
- tests/fixtures/math/gdelt_timelinetone_response.json (schema-derived-with-validator, from gdelt-diagnosis.md real 200)
- tests/fixtures/math/gdelt_artlist_response.json (schema-derived-with-validator, from existing artlist shape)

## Behavioral Test Plan
N/A — backend-only feature, no UI surface (feature plan §Design-System Mapping: "N/A").

## A/C Coverage Matrix
| A/C ID | Description | Test File | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| AC-1 | `_fetch_gdelt_sentiment(universe)` exists in `advisors/lens_gdelt.py`; returns documented shape | test_lens_gdelt.py | `test_producer_function_exists_and_is_callable`, `test_returns_all_required_keys_on_success`, `test_per_ticker_is_always_none_in_v1`, `test_source_field_is_non_empty_string` | RED |
| AC-2 | Honest-availability: unavailable marker on fetch failure, no fabricated tone | test_lens_gdelt.py | `test_network_timeout_returns_unavailable_with_exc_class_reason`, `test_json_decode_error_returns_unavailable`, `test_empty_timeline_returns_no_tone_data_unavailable`, `test_empty_data_array_returns_no_tone_data_unavailable`, `test_no_numeric_values_in_data_returns_no_tone_data_unavailable`, `test_fabrication_forbidden_no_default_tone_on_empty`, `test_tone_none_implies_available_false_on_timeout`, `test_tone_none_implies_available_false_on_empty_data`, `test_available_true_implies_tone_is_float` | RED |
| AC-3 | Fixtures captured-from-producer or schema-derived-with-validator; tests assert shape/format, never hardcoded tone | test_lens_gdelt.py | `test_timelinetone_fixture_schema_is_valid`, `test_artlist_fixture_schema_is_valid`, `test_tone_normalized_in_minus1_to_1_range`, `test_tone_is_float_not_hardcoded_sentinel` | RED |
| AC-4 | Off-execution-path; bounded retry MAX_ATTEMPTS=3, BACKOFF_BASE_S>=5.0; no infinite loop | test_lens_gdelt.py | `test_bounded_retry_exhausts_after_max_attempts_on_429`, `test_retry_count_does_not_exceed_max_attempts`, `test_backoff_base_constant_is_at_least_five_seconds`, `test_retry_only_on_429_not_on_success_with_empty_data`, `test_backoff_cap_constant_exists_and_is_positive` | RED |
| AC-5 | GDELT API contract pinned (.claude/gdelt-contract.md exists with endpoint/field semantics) | test_lens_gdelt.py | `test_contract_document_exists_and_names_timelinetone_endpoint`, `test_tone_extracted_from_nested_data_field_not_series_wrapper` | RED |

## Import Stubs Created
- `advisors/lens_gdelt.py` — stub only; exports `_fetch_gdelt_sentiment` raising NotImplementedError and module-level constants at their specified values. Contains NO business logic.

## Questions for User
None at RED phase.

## RED Run Summary
- 35 failing (non-live), 8 passing, 2 deselected (live — @pytest.mark.live, excluded by default pyproject.toml addopts)
- All 35 failures are NotImplementedError from the stub — correct assertion failures
- 8 passing are structural (fixture schema validity, function existence, named constants at pinned values, contract doc existence) — these legitimately SHOULD pass on the stub
- No syntax errors, no import errors, no tautologies

## Status Log
- [2026-06-15] test-writer: Starting RED phase
- [2026-06-15] test-writer: RED complete — 35 failing (non-live) / 8 passing / 2 live-deselected. Stub at advisors/lens_gdelt.py. Fixtures at tests/fixtures/math/gdelt_timelinetone_response.json + gdelt_artlist_response.json.
