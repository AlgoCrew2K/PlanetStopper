# Feature: Lens Data — Technicals Producer
Status: ready
Created: 2026-06-13

## Summary

Produces real price/trend/breadth technicals for the portfolio universe so the Prism `technicals_analyst` (Phase 2) reasons about actual market structure — moving-average posture, breadth, momentum — instead of an empty lens. Integrates into the Cycle-4 lens data layer following the existing producer pattern. Where possible, reuses the existing Alpaca historical fetch / `synthetic_history.py` 250-day window rather than adding a new price source. Technicals are math-layer work: every constant must be named + source-commented (per the `math_engine.py` coding standard), and computed values must be verified with a golden-fixture test.

## Acceptance Criteria

- [ ] AC-1: A new producer function in the Cycle-4 lens data layer computes documented technicals (e.g. MA posture: price relative to 50-day and 200-day SMA; breadth: % of universe above 50-day SMA; momentum: 20-day return) for the universe and normalizes them to a documented shape the `technicals_analyst` consumes (field names, types, value semantics).
- [ ] AC-2: Honest-availability empty-state when source data (Alpaca bars) is missing or the fetch fails — no fabricated values. Returns a clear unavailable marker.
- [ ] AC-3: Fixtures are captured-from-producer or schema-derived with a runtime validator — NOT parser+fixture co-design. Tests assert shape/format/presence; computed technicals in tests derive from fixture bar data (not hardcoded expected values).
- [ ] AC-4: Off-execution-path; bounded retries on any Alpaca fetch (finite `max_attempts`); no blocking I/O on any execution path.
- [ ] AC-5: Source confirmation at recon: the implementer confirms whether the existing `synthetic_history.py` 250-day window provides the needed bars before adding any new price source. If it does, reuse it.
- [ ] AC-6: Every technical indicator constant (window sizes, thresholds) is named (not a magic number) with a source comment in the producer code, consistent with the `math_engine.py` naming standard.

## Architecture

**Files changed:**
- `advisors/lens_pipeline.py` — add `_fetch_technicals(universe: list[str]) -> dict` following the per-lens exception-isolation pattern; wire into `run_pipeline` as the `technicals` lens; cite the source in `build_citation`
- `advisors/lens_technicals.py` (optional) — extract here if the producer grows complex; lazy import
- `synthetic_history.py` — read-only dependency (existing 250-day bar fetcher + file cache); no changes expected unless the needed bars are outside the existing window
- `tests/ai_advisor/test_lens_technicals.py` (new) — unit + contract tests

**Data flow:**
1. `run_pipeline()` calls `_fetch_technicals(universe)` in the technicals lens pass.
2. Producer fetches or reuses cached Alpaca bars via `synthetic_history.py`.
3. Computes the documented indicators (named constants, source comments).
4. Returns a dict with the documented shape: `{"available": bool, "ma_posture": dict | None, "breadth": float | None, "momentum": dict | None, "source": str}` [PM-ASSUMED shape — confirm at recon].
5. `build_citation` adds the source.
6. Phase-2 `technicals_analyst` reads this dict.

**Integration points:** `advisors/lens_pipeline.py` existing per-lens isolation; `synthetic_history.py` (parallel + file cache, already tested); `run_pipeline(dry_run=True)` for local testing.

## Design-System Mapping

N/A — backend feature, no UI surface. (All 10 are backend/infra; the Cycle-5 Market Prism Overview UI already shipped separately.)

## Edge Cases

- **Alpaca fetch fails:** producer catches the exception, returns unavailable marker. Never crashes `run_pipeline`. D-1: `type(exc).__name__` only.
- **Insufficient bar history (< 200 days available):** indicators that require 200 days (e.g. 200-day SMA) are marked unavailable individually; shorter indicators can still be computed. Producer handles partial data without fabricating values.
- **Universe tickers with no Alpaca data:** per-ticker availability flags; overall indicators computed only from covered tickers.
- **`synthetic_history.py` file cache stale:** the existing cache TTL applies; producer does not bypass it. If cache is too stale to be useful, return unavailable rather than fabricate.
- **Alpaca 429 / rate limit:** hard-bounded retry with backoff (finite `max_attempts`). After exhaustion, returns unavailable marker.
- **Zero-bar edge case:** if a ticker returns an empty bar list, skip it from indicator computation — do not divide by zero.

## Security Considerations

- **Input validation / injection:** Alpaca bar data is fetched via the existing `synthetic_history.py` client (already in production use). Computed technicals are floats derived from bar data — not user-controlled strings. No injection risk.
- **Data exposure:** technicals are advisory analysis stored in `advisor_observations` (local DB). No raw bar data is stored beyond what `synthetic_history.py` already caches. D-1: `type(exc).__name__` only on errors.
- **Authz / advisory-only:** off-execution-path; never touches `LIVE_EXECUTION`. No new Flask route.
- **API key handling:** Alpaca API key (`ALPACA_KEY`, `ALPACA_SECRET`) is already used by `synthetic_history.py`. This producer reuses the same credentials — no new key handling required. Keys must not be hardcoded.
- **Abuse / rate-limiting:** bounded retries. Runs off-hours (03:00); one fetch per nightly run using the existing bar cache where possible.
- **Math correctness:** every constant named + source comment (per `math_engine.py` standard). Golden-fixture test verifies indicator computations on known bar data.

## Testing Strategy

**New test file:** `tests/ai_advisor/test_lens_technicals.py`
- `test_fetch_returns_valid_shape` — with a fixture of synthetic Alpaca bars (schema-derived), assert the returned dict has required keys and correct types
- `test_ma_posture_from_fixture` — provide fixture bars where price is above/below MA; assert `ma_posture` reflects the fixture state (not a hardcoded expected float — derive from the fixture)
- `test_unavailable_on_alpaca_error` — mock `synthetic_history.py` to raise; assert `{"available": False}` returned, no exception propagates
- `test_insufficient_history` — provide < 200 bars; assert 200-day indicators are individually marked unavailable; shorter indicators still compute
- `test_zero_bar_edge_case` — ticker with empty bar list; assert no ZeroDivisionError; ticker excluded from aggregation
- `test_bounded_retry_on_429` — mock Alpaca 429; assert retry exhausts (finite) and returns unavailable
- `test_pipeline_integration` — `run_pipeline(dry_run=True)` with technicals producer mocked; assert technicals lens in output has correct shape

**Golden-fixture test (math-layer rule):** a separate `test_technicals_golden.py` computes each indicator on a known fixture bar set and asserts the result matches the fixture-derived expected value (not a hardcoded literal — derive the expected from the fixture via the same formula).

**Fixture provenance:** Alpaca bar fixtures are schema-derived from the Alpaca API response schema (field names, types). Runtime validator asserts fixture matches schema on each run.

**Run protocol:** `DB_PATH` set via `tests/conftest.py`; targeted: `pytest tests/ai_advisor -n0 -o addopts= -p no:xdist`. No real Alpaca calls in CI — all mocked via fixtures.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Reuse `synthetic_history.py` bars where possible | Avoids adding a new price source; the existing 250-day window already covers common technical windows; confirm at recon |
| Every constant named + source comment | `math_engine.py` coding standard: no magic numbers; technicals are math-layer work |
| Golden-fixture test for each indicator | Every change to math layers requires a golden-fixture test per project coding standards |
| Producer returns unavailable marker (not raises) | Consistent with Cycle-4 per-lens exception isolation; `run_pipeline` must never crash on a single lens failure |
| Bounded retries (finite `max_attempts`) | The persistent-429 infinite-loop was a PC-crash root cause |

## Scope Boundaries

- **IN**: technicals producer in `advisors/lens_pipeline.py` (or `advisors/lens_technicals.py`); honest-availability empty-state; bounded retries; named constants + source comments; golden-fixture tests; doc-gen updates
- **OUT**: GDELT sentiment producer; derivatives producer (separate feature files); Phase-2 analyst agent files; Epic A Market Prism phases; changes to `math_engine.py`; changes to `synthetic_history.py` (read-only dependency)

**Dependencies:** sequenced after Epic A's observed proof run. Independent of the other two Epic B lens producers (B1 GDELT, B3 Derivatives) — can run as a parallel agent once unblocked.

**Team note:** Toxic Pair TDD — quant-test-writer (adversarial, math focus) + implementer + `risk-engine-specialist` (technicals are math — named constants + golden-fixture per math-layer rule) + doc-gen.
