# TDD Handoff
Plan: feature-plans/lens-derivatives-vix-freshness-fix.md
Branch: fix/derivatives-vix-freshness
Phase: red

## Test Files
- tests/ai_advisor/test_lens_options_proxy_freshness.py  (AC-1 through AC-6)
- tests/fixtures/math/fred_observations_fresh.json       (golden fixture — fresh-window FRED shape)
- tests/fixtures/math/fred_observations_stale.json       (golden fixture — stale/2020-batch FRED shape)

## Behavioral Test Plan
N/A — backend producer, no UI surface (plan §Design-System Mapping).

## Implementation Contract for df-implementer

These are the exact symbols the tests import/monkeypatch from `advisors.lens_options_proxy`.
Coordinate on them BEFORE writing production code:

1. `_OPTIONS_PROXY_MAX_STALENESS_DAYS: int` — named module-level constant, value ~10
   (PM-ASSUMED). MUST have a source comment explaining the threshold choice.

2. `_OPTIONS_PROXY_LOOKBACK_DAYS: int` — named module-level constant, value ~90
   (PM-ASSUMED). Governs rolling recent-window start date passed to FRED.

3. `_today() -> datetime.date` — module-level callable returning today's date.
   Tests monkeypatch via:
     `monkeypatch.setattr(advisors.lens_options_proxy, "_today", lambda: datetime.date(...))`.
   Implementer MUST expose this exact symbol at module scope.

4. `_fetch_fred_series` request params MUST NOT hardcode `"observation_start":
   "2020-01-01"` with `"sort_order": "asc"` + `"limit": 100`. The AC-1 test asserts
   the MOST-RECENT valid observation is selected (not the oldest-from-2020).

5. Freshness guard: when `_parse_latest_observation` returns `(value, date_str)` and
   `datetime.date.fromisoformat(date_str) < _today() - timedelta(days=_OPTIONS_PROXY_MAX_STALENESS_DAYS)`,
   `_fetch_options_proxy` MUST return:
     `{"available": False, "reason": "stale_data", "source": <_SOURCE_CITATION>}`
   — NO `vix_level`, NO `vix_term_structure`, NO `risk_read`, NO `as_of_date`.

6. Fresh data: `as_of_date` in the returned dict equals the date string of the selected
   observation — not today's date.

7. Existing paths (fetch-failure / 429 / no-valid-observations) remain unchanged.

## A/C Coverage Matrix

| A/C ID | Description | Test Class / Test Name | Status |
|--------|-------------|------------------------|--------|
| AC-1 | Recent-window: most-recent valid obs selected from fixture | TestRecentWindowFetch | RED |
| AC-1 | Recent-window: request does NOT use the stale 2020-01-01 start | test_fetch_does_not_use_stale_2020_start | RED |
| AC-2 | Stale obs → available=False, reason="stale_data" | TestFreshnessGuard.test_stale_latest_observation_available_false | RED |
| AC-2 | Stale obs → no fabricated vix_level | TestFreshnessGuard.test_stale_obs_returns_no_vix_level | RED |
| AC-2 | Stale obs → no fabricated vix_term_structure | TestFreshnessGuard.test_stale_obs_returns_no_vix_term_structure | RED |
| AC-2 | Stale obs → no fabricated risk_read | TestFreshnessGuard.test_stale_obs_returns_no_risk_read | RED |
| AC-2 | Fresh obs → available=True with real values | TestFreshnessGuard.test_fresh_latest_observation_available_true | RED |
| AC-2 | Freshness constant is named module-level symbol | test_named_staleness_constant_exists | RED |
| AC-2 | Lookback constant is named module-level symbol | test_named_lookback_constant_exists | RED |
| AC-3 | as_of_date equals selected observation's real date (not today) | TestAsOfDateTruthful | RED |
| AC-4 | Fetch-failure path still returns available=False, reason=exc type | TestHonestAvailabilityPreserved.test_fetch_failure_returns_unavailable | RED |
| AC-4 | 429-exhausted path still returns available=False | TestHonestAvailabilityPreserved.test_429_exhausted_returns_unavailable | RED |
| AC-4 | No-valid-observations still returns available=False | TestHonestAvailabilityPreserved.test_no_valid_observations_returns_unavailable | RED |
| AC-4 | Regime classification unchanged when data is fresh | TestHonestAvailabilityPreserved.test_regime_classification_preserved_when_fresh | RED |
| AC-5 | _today() is monkeypatchable at module scope | TestRunDateInjectable.test_today_is_injectable | RED |
| AC-5 | Freshness comparison uses _today() not datetime.date.today() | TestRunDateInjectable.test_freshness_uses_injectable_today | RED |
| AC-6 | Bounded retry max-attempts constant still present | TestBoundedRetryPreserved.test_max_attempts_constant_present | RED |
| AC-6 | _fetch_options_proxy never raises (off-path) | TestBoundedRetryPreserved.test_fetch_options_proxy_never_raises | RED |
| Weekend | Latest obs 3-4 days old → still available (guard not false-positive) | TestWeekendHolidayEdgeCase | RED |

## Import Stubs Created
None needed — `advisors/lens_options_proxy.py` already exists.

New symbols (`_OPTIONS_PROXY_MAX_STALENESS_DAYS`, `_OPTIONS_PROXY_LOOKBACK_DAYS`, `_today`)
do NOT yet exist in the module — tests will fail with `AttributeError` on those names until
the implementer adds them. That is the correct RED failure mode for AC-2 and AC-5.

## Questions for User / PM
None — all design decisions covered by plan or [PM-ASSUMED] annotations.

## Status Log
- [2026-06-16] df-test-writer (quant-test-writer, LEAD): Starting RED phase for derivatives-vix-freshness fix
