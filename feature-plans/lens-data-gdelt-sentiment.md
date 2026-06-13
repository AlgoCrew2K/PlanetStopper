# Feature: Lens Data — GDELT Tone / Sentiment Producer
Status: ready
Created: 2026-06-13

## Summary

Produces a real news-sentiment directional signal from GDELT tone for the portfolio universe, so the Prism `sentiment_analyst` (Phase 2) reasons about actual market sentiment instead of an empty lens. Uses the public GDELT API at $0/month. Integrates into the Cycle-4 lens data layer (`advisors/lens_pipeline.py`) following the existing producer pattern: per-lens exception isolation, honest-availability empty-state when GDELT is unreachable, and bounded retries. The `sentiment_analyst` is unblocked to run on real data once this producer ships; until then it operates in `limited-inputs` mode.

## Acceptance Criteria

- [ ] AC-1: A new producer function in the Cycle-4 lens data layer (in `advisors/lens_pipeline.py` or a new `advisors/lens_gdelt.py`) fetches GDELT tone for the configured universe and normalizes it to a documented directional score shape that the `sentiment_analyst` consumes. The shape is documented (field names, types, value semantics — e.g. normalized tone in [-1, 1]).
- [ ] AC-2: Honest-availability empty-state: when GDELT is unreachable or returns empty results, the producer returns a clear unavailable marker (e.g. `{"available": False, "reason": "gdelt_fetch_failed"}`) — NEVER fabricated tone values.
- [ ] AC-3: Fixtures are captured-from-producer or schema-derived with a runtime validator — NOT parser+fixture co-design (automatic Gate-1 fail). Tests assert shape/format/presence of the returned dict; they never assert specific hardcoded tone values (those are producer-computed).
- [ ] AC-4: The producer is off-execution-path; uses bounded retries (finite `max_attempts`, exponential backoff cap — the persistent-429 infinite-loop was a PC-crash root cause); no blocking I/O on any execution path.
- [ ] AC-5: The GDELT API contract (endpoint URL, request shape, rate limits, tone field semantics) is pinned in a researcher deliverable before any client code is written.

## Architecture

**Files changed:**
- `advisors/lens_pipeline.py` — add a `_fetch_gdelt_sentiment(universe: list[str]) -> dict` function following the existing per-lens exception-isolation pattern (try/except at the producer boundary, returns unavailable marker on any exception); wire into `run_pipeline` as the `sentiment` lens; cite the source in `build_citation`
- `advisors/lens_gdelt.py` (optional) — if the producer grows complex, extract it here; `lens_pipeline.py` imports it lazily (CC-2 boundary: no top-level import that blocks daemon startup)
- `tests/ai_advisor/test_lens_gdelt.py` (new) — unit + contract tests

**Data flow:**
1. `run_pipeline()` calls `_fetch_gdelt_sentiment(universe)` in the sentiment lens pass.
2. The producer fetches GDELT tone data (HTTP GET to the GDELT API endpoint, pinned by researcher).
3. Returns a dict with the documented shape: `{"available": bool, "tone": float | None, "per_ticker": dict | None, "source": str}` [PM-ASSUMED shape — confirm at researcher dispatch].
4. `build_citation` adds the source URL.
5. The Phase-2 `sentiment_analyst` reads this dict from the data layer.

**Integration points:** `advisors/lens_pipeline.py` existing per-lens isolation structure; `run_pipeline(dry_run=True)` for local testing without writing to DB.

## Design-System Mapping

N/A — backend feature, no UI surface. (All 10 are backend/infra; the Cycle-5 Market Prism Overview UI already shipped separately.)

## Edge Cases

- **GDELT unreachable / HTTP error:** producer catches the exception, returns `{"available": False, "reason": type(exc).__name__}`. Never crashes `run_pipeline`.
- **GDELT returns empty results:** treated as unavailable — no fabricated tone. Logged in the lens output.
- **Rate limit (429):** hard-bounded retry with backoff (finite `max_attempts` constant). After exhaustion, returns unavailable marker.
- **Universe tickers with no GDELT coverage:** producer returns per-ticker availability flags; overall tone computed only from covered tickers. If none are covered, returns unavailable.
- **Stale GDELT data (older than N hours):** [PM-ASSUMED] researcher deliverable clarifies GDELT data freshness guarantees; producer may include a `data_age_hours` field. If too stale, treat as unavailable.
- **GDELT API contract drift:** the API is public and may change. The researcher pins the contract at dispatch time. Future drift is a maintenance concern, not a Phase-1 bug.

## Security Considerations

- **Input validation / injection:** GDELT response data is external and untrusted. Producer parses only the documented fields (tone float, article counts); does not eval or template response content. Parameterized DB writes apply for any persistence.
- **Data exposure:** tone scores are advisory analysis stored in `advisor_observations` (local DB). No raw GDELT article text is stored or echoed to the UI. D-1: `type(exc).__name__` only on errors.
- **Authz / advisory-only:** off-execution-path; never touches `LIVE_EXECUTION`. No new Flask route.
- **API key handling:** GDELT is a public API ($0/month, no key required per current documentation). If a key is needed (researcher may find otherwise), it must be stored in `.env` and never hardcoded.
- **Abuse / rate-limiting:** bounded retries prevent hammering GDELT on persistent failure. The producer runs off-hours (03:00 slot) — one call per nightly run, not intraday.
- **Prompt injection:** tone scores (floats) and availability flags (bools) enter analyst prompts. These are structured numeric values, not free text — low injection risk. Researcher confirms field types.

## Testing Strategy

**Precondition:** researcher deliverable pins the GDELT API contract (endpoint, tone field, rate limits) BEFORE any test fixtures or client code are written (fixture provenance hard rule).

**New test file:** `tests/ai_advisor/test_lens_gdelt.py`
- `test_fetch_returns_valid_shape` — with a mocked HTTP response matching the researcher-pinned schema, assert the returned dict has the required keys and field types (not specific tone values)
- `test_unavailable_on_http_error` — mock a network error; assert `{"available": False}` is returned and no exception propagates
- `test_unavailable_on_429_after_max_retries` — mock persistent 429; assert bounded retry exhausts and returns unavailable (not infinite loop)
- `test_empty_response_returns_unavailable` — mock empty GDELT result; assert unavailable marker, no fabricated tone
- `test_pipeline_integration` — call `run_pipeline(dry_run=True)` with GDELT producer mocked; assert the sentiment lens in the output dict has the correct shape

**Fixture provenance:** HTTP response fixtures are captured from the real GDELT API once (during researcher recon) and stored as JSON in `tests/fixtures/gdelt_*.json`. The runtime validator asserts the fixture matches the documented schema on each test run.

**Run protocol:** `DB_PATH` set via `tests/conftest.py`; targeted: `pytest tests/ai_advisor -n0 -o addopts= -p no:xdist`. No real GDELT calls in CI — all mocked. Real API calls are the researcher's recon, not CI.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Researcher dispatched first to pin GDELT contract | Fixture provenance hard rule: no parser+fixture co-design; must know the real API shape before writing client code |
| Producer returns unavailable marker (not raises) | Consistent with Cycle-4 per-lens exception isolation; `run_pipeline` must never crash on a single lens failure |
| Bounded retries (finite `max_attempts`) | The persistent-429 infinite-loop was a PC-crash root cause; any retry must be hard-bounded |
| Tone normalized to a documented directional score | The `sentiment_analyst` needs a stable contract to reason against; raw GDELT tone values are normalized to a predictable shape |
| [PM-ASSUMED] $0/month GDELT public API | Researcher confirms this; if a paid source is the only reliable option, escalate to operator |

## Scope Boundaries

- **IN**: GDELT sentiment producer in `advisors/lens_pipeline.py` (or `advisors/lens_gdelt.py`); honest-availability empty-state; bounded retries; tests; fixture provenance via researcher; doc-gen updates
- **OUT**: FRED macro producer; SEC fundamentals producer; Technicals producer (separate feature files); Phase-2 analyst agent files; Epic A Market Prism phases; changes to `advisor_observations` schema

**Dependencies:** sequenced after Epic A's observed proof run (exclusive-focus rule). Independent of the other two Epic B lens producers (B2 Technicals, B3 Derivatives) — can run as a parallel agent once unblocked.

**Team note:** Toxic Pair TDD — test-writer + implementer + `composer-alpaca-integration` (or fitting integration specialist) + doc-gen. Precede with a researcher to pin the GDELT contract.
