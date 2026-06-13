# Feature: Lens Data — Derivatives Producer
Status: ready
Created: 2026-06-13

## Summary

Produces real options/vol/positioning signals (vol term structure, skew, put/call ratio) for the portfolio universe so the Prism `derivatives_analyst` (Phase 2) reasons about actual market positioning instead of an empty lens. This is the highest-uncertainty producer: a reliable $0/month derivatives data source may not exist, so a source research phase is mandatory before any client code is written. If recon finds no viable free source, this producer is reframed (dropped or replaced with a proxy signal) rather than invented. Integrates into the Cycle-4 lens data layer following the existing producer pattern.

## Acceptance Criteria

- [ ] AC-1: **Source research first** — a researcher identifies and pins a $0/month derivatives/vol source (endpoint, contract, rate limits, field semantics) BEFORE any client code or fixtures are written. If no viable free source exists, the researcher delivers a reframe proposal (drop or proxy) for PM decision.
- [ ] AC-2: A new producer function in the Cycle-4 lens data layer fetches the chosen signals and normalizes them to a documented shape the `derivatives_analyst` consumes (field names, types, value semantics — e.g. vol term structure slope, skew, put/call ratio).
- [ ] AC-3: Honest-availability empty-state when the source is unavailable or returns no data — no fabricated values. Returns a clear unavailable marker.
- [ ] AC-4: Fixtures are captured-from-producer or schema-derived with a runtime validator — NOT parser+fixture co-design. Tests assert shape/format/presence; they never assert hardcoded producer-computed values.
- [ ] AC-5: Off-execution-path; bounded retries (finite `max_attempts`, exponential backoff cap); no blocking I/O on any execution path.

## Architecture

**Finalize after researcher deliverable** — the exact data source determines the client implementation. The following structure is assumed [PM-ASSUMED]:

- `advisors/lens_pipeline.py` — add `_fetch_derivatives(universe: list[str]) -> dict` following the per-lens exception-isolation pattern; wire into `run_pipeline` as the `derivatives` lens; cite the source in `build_citation`
- `advisors/lens_derivatives.py` (optional) — extract here if the producer grows complex; lazy import
- `tests/ai_advisor/test_lens_derivatives.py` (new) — unit + contract tests

**Data flow:**
1. `run_pipeline()` calls `_fetch_derivatives(universe)` in the derivatives lens pass.
2. Producer fetches vol/positioning data from the researcher-pinned source.
3. Returns a dict with the documented shape: `{"available": bool, "vol_term_structure": dict | None, "skew": float | None, "put_call_ratio": float | None, "source": str}` [PM-ASSUMED — confirm per researcher].
4. `build_citation` adds the source.
5. Phase-2 `derivatives_analyst` reads this dict.

**Integration points:** `advisors/lens_pipeline.py` per-lens isolation; `run_pipeline(dry_run=True)` for local testing.

## Design-System Mapping

N/A — backend feature, no UI surface. (All 10 are backend/infra; the Cycle-5 Market Prism Overview UI already shipped separately.)

## Edge Cases

- **No viable free source found at recon:** researcher delivers a reframe proposal (drop the producer or replace with a proxy signal like VIX term structure from FRED/Yahoo). PM decides before any implementation work begins. This is the highest-risk edge case for this feature.
- **Source unavailable / HTTP error:** producer catches the exception, returns unavailable marker. D-1: `type(exc).__name__` only. Never crashes `run_pipeline`.
- **Sparse options data for small-cap tickers:** per-ticker availability flags; aggregate signals computed only from tickers with coverage.
- **Rate limit (429):** hard-bounded retry with backoff. After max retries, returns unavailable.
- **Stale data:** researcher deliverable clarifies data freshness. If too stale for nightly use, treat as unavailable.
- **Reframe to proxy:** if the researcher recommends a proxy signal (e.g. VIX slope as a derivatives proxy), the producer documents the proxy choice clearly in the citation and in the `source` field — the `derivatives_analyst` knows it is reading a proxy, not direct options data.

## Security Considerations

- **Input validation / injection:** derivatives response data is external and untrusted. Producer parses only the documented fields (floats, ratios); never eval's or templates response content. Parameterized DB writes for any persistence.
- **Data exposure:** vol/positioning signals are advisory analysis stored in `advisor_observations` (local DB). D-1: `type(exc).__name__` only on errors. No raw response data stored.
- **Authz / advisory-only:** off-execution-path; never touches `LIVE_EXECUTION`. No new Flask route.
- **API key handling:** if the researcher-identified source requires a key, it must be stored in `.env` and never hardcoded. If the source is public (no key), document that explicitly.
- **Abuse / rate-limiting:** bounded retries. Runs off-hours (03:00); one fetch per nightly run.
- **Adopt-existing-contracts principle:** the producer adopts the provider's data contract; it never invents fields or infers missing data from parser behavior (fixture provenance hard rule).

## Testing Strategy

**Precondition:** researcher deliverable pins the derivatives source contract BEFORE any fixtures or client code are written. If the researcher recommends a reframe, the test strategy is revised at that point.

**New test file:** `tests/ai_advisor/test_lens_derivatives.py`
- `test_fetch_returns_valid_shape` — with a mocked HTTP response matching the researcher-pinned schema, assert required keys and field types (not specific values)
- `test_unavailable_on_http_error` — mock a network error; assert `{"available": False}` returned, no exception propagates
- `test_unavailable_on_429_after_max_retries` — mock persistent 429; assert bounded retry exhausts and returns unavailable
- `test_sparse_coverage` — mock response with data for only some universe tickers; assert partial results, no fabricated values for uncovered tickers
- `test_pipeline_integration` — `run_pipeline(dry_run=True)` with derivatives producer mocked; assert derivatives lens in output has correct shape

**Fixture provenance:** HTTP response fixtures captured from the real API once (researcher recon) or schema-derived. Runtime validator asserts fixture matches schema on each run.

**Run protocol:** `DB_PATH` set via `tests/conftest.py`; targeted: `pytest tests/ai_advisor -n0 -o addopts= -p no:xdist`. No real API calls in CI.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Researcher dispatched first (mandatory) | This is the highest-uncertainty producer; a $0/month source may not exist; adopt-existing-contracts means we cannot invent a schema |
| Reframe instead of invent if no free source | Adopt-existing-contracts principle: if no reliable free source, drop or proxy rather than fabricate; operator decides after researcher reframe proposal |
| Producer returns unavailable marker (not raises) | Consistent with Cycle-4 per-lens exception isolation; `run_pipeline` must never crash on a single lens failure |
| Bounded retries (finite `max_attempts`) | The persistent-429 infinite-loop was a PC-crash root cause |
| [PM-ASSUMED] Shape includes vol_term_structure / skew / put_call_ratio | Placeholder pending researcher deliverable; all shape assumptions are revised per actual source |

## Scope Boundaries

- **IN**: source researcher deliverable (endpoint + contract + rate limits); derivatives producer in `advisors/lens_pipeline.py` (or `advisors/lens_derivatives.py`); honest-availability empty-state; bounded retries; tests; doc-gen updates. If reframed: reframe proposal + PM decision + revised implementation.
- **OUT**: GDELT sentiment producer; Technicals producer (separate feature files); Phase-2 analyst agent files; Epic A Market Prism phases; paid data sources (unless operator approves after recon)

**Dependencies:** sequenced after Epic A's observed proof run. Independent of the other two Epic B lens producers (B1 GDELT, B2 Technicals) — can run as a parallel agent once unblocked.

**Team note:** Researcher (pin the free source + contract) → Toxic Pair TDD: test-writer + implementer + `composer-alpaca-integration` (or fitting integration specialist) + doc-gen. Risk callout: a reliable $0/month derivatives source may not exist; if recon finds none, reframe (drop or proxy) rather than invent.
