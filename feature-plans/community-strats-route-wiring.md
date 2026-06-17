# Feature: Community-Strats Route Wiring (HF-1)
Status: ready
Created: 2026-06-17

## Summary
The community-strategies engine path is fully built but **hollow in production** (AI Advisor closeout finding HF-1): the Strategy Builder route `POST /ai-advisor/strategy-builder/run` (`app.py:3437`) calls `propose_strategies(...)` WITHOUT the `community_candidates=` kwarg, so community strategies never enter a production proposal run (reachable only from tests). This cycle wires the existing, already-shipped engine pieces into that one route so community candidates are loaded (weekly-cached Atlas read), adapted, and fed into the single FDR-gate batch alongside the template candidates — with honest template-only degradation when the Atlas load is empty/unavailable. Advisory-only, off the live execution path, never-raising. (Supersedes the stale engine-layer plan `feature-plans/propose-strategies-community-wiring.md`, whose scope is already merged.)

## Acceptance Criteria
- [ ] AC-1 (community candidates reach the run): when `load_community_strategies` returns candidates, the route adapts them via `community_candidate_infos(result, max_candidates=MAX_COMMUNITY_CANDIDATES_PER_RUN)` and passes them as `propose_strategies(..., community_candidates=<list>)`. Assert (mocking the engine) that the `community_candidates` kwarg the route forwards equals the adapter output (membership/count), so they enter the FDR-gate batch.
- [ ] AC-2 (honest template-only degradation): when the Atlas load is unavailable or returns no candidates (`available=False` or empty), the route still completes a template-only run — `community_candidates` is `[]`/`None` and the route's response shape + status is identical to the current template-only behavior. Assert no error surfaced solely due to an empty community load.
- [ ] AC-3 (weekly-cache / bill-protection): the route uses the weekly-cached Atlas path (`force_refresh=False` / default) — NEVER a forced refresh per request. Assert the route does not pass `force_refresh=True` to `load_community_strategies`.
- [ ] AC-4 (never-raising / D-1): a `load_community_strategies` or `community_candidate_infos` failure does NOT break the route — community wiring is best-effort; the route degrades to template-only and, on any catastrophic exception, still returns `{"error": type(exc).__name__}` (no `str(exc)`, no key/path leakage). Assert a raised community-load does not 500 the route and does not leak detail.
- [ ] AC-5 (off-execution-path + boundary preserved): the new imports stay LAZY inside the route handler (not module-level), CSRF enforcement is unchanged, the route is NOT added to `_SETTINGS_WRITE_ALLOWLIST`, and there is no `LIVE_EXECUTION`/credential/Composer-write interaction. Assert imports are inside the handler and no allowlist/live-exec reference is introduced.
- [ ] AC-6 (no regression to the default path): with community wiring producing `[]` (empty Atlas), a template-only run's survivor/rejected/FDR output is byte-equivalent to the pre-change route for the same input. Assert against a template-only fixture run.

## Architecture
Edit `app.py` only — the `ai_advisor_strategy_builder_run()` handler (~`app.py:3413-3443`):
1. Extend the existing lazy import block (currently `Objective, ScreenConfig, propose_strategies` from `advisors.strategy_builder_engine`) to also import `community_candidate_infos` and `MAX_COMMUNITY_CANDIDATES_PER_RUN`; add a lazy import of `load_community_strategies` from `advisors.community_strats`. All lazy (inside the handler) to keep the engine off the live 1-minute execution path.
2. Before the `propose_strategies(...)` call, load + adapt community candidates, best-effort:
   ```python
   community_candidates = []
   try:
       _community = load_community_strategies(force_refresh=False)
       community_candidates = community_candidate_infos(
           _community, max_candidates=MAX_COMMUNITY_CANDIDATES_PER_RUN
       )
   except Exception as exc:
       _daemon_log.warning("community-strats load skipped: %s", type(exc).__name__)
       community_candidates = []
   ```
   (Both helpers are documented never-raising/D-1; the try/except is belt-and-suspenders and logs only the class name.)
3. Pass `community_candidates=community_candidates` into the existing `propose_strategies(...)` call. Everything downstream (single-batch FDR gate, screens, persistence, response building) is unchanged — `propose_strategies` already merges + caps community candidates into the full gated batch.
No new modules, no new routes, no signature changes, no template/JS change (the Strategy Builder tab already renders survivor/rejected/FDR JSON).

## Design-System Mapping
N/A — backend route wiring; no new UI components. The existing Strategy Builder SPA tab renders the same response JSON shape.

## Edge Cases
- Empty Atlas result / `available=False` → `community_candidate_infos` returns `[]` → template-only run (AC-2).
- More community candidates than the cap → `community_candidate_infos`/`propose_strategies` cap at `MAX_COMMUNITY_CANDIDATES_PER_RUN` (existing engine behavior; route just forwards).
- Atlas/cache raises → caught, logged by class, template-only (AC-4).
- No Composer key → unchanged: `propose_strategies` returns `ProposalRun(error=...)`, route surfaces `run.error` (existing path; community candidates are moot).
- Empty universe / unknown objective → unchanged existing handling.
- Concurrency: the weekly cache is shared; concurrent route calls read the cache (no per-call Atlas hit).

## Security Considerations
- **Injection/input:** no new user-supplied fields; community trees are validated by `community_strats` via `symphony_schema.validate_tree` before adaptation (existing). No query interpolation.
- **Bill-protection (abuse):** weekly-cached Atlas read (`force_refresh=False`) — a high-volume caller cannot weaponize the route into repeated Atlas pulls (AC-3). Operator bill-protection directive respected.
- **Data exposure / D-1:** route still returns only `type(exc).__name__` on failure; community-load failures log only the class name, never `str(exc)` (no MONGO_URI/key/path leakage).
- **Auth/boundary:** CSRF enforced (unchanged); advisory-only; not in `_SETTINGS_WRITE_ALLOWLIST`; no `LIVE_EXECUTION`/credential surface; off the execution path (lazy import).
- **SSRF:** Atlas endpoint is fixed/config-driven (existing `community_strats`/`atlas_cache`); no user-supplied URL.

## Testing Strategy
New `tests/ui/test_strategy_builder_community_wiring.py` (or extend the existing strategy-builder route test) — Flask test client, all collaborators mocked, NO live Atlas/Composer:
- Mock `advisors.community_strats.load_community_strategies` + `advisors.strategy_builder_engine.community_candidate_infos` + `propose_strategies` (capture the `community_candidates` kwarg the route forwards).
- AC-1: populated community load → assert the captured `community_candidates` equals the adapter output (membership/count) and that `propose_strategies` was called with it.
- AC-2/AC-6: empty/`available=False` load → `community_candidates=[]`, route returns the same template-only response shape; no error from emptiness.
- AC-3: assert the route calls `load_community_strategies` without `force_refresh=True`.
- AC-4: `load_community_strategies` raises → route does not 500, degrades template-only; a `propose_strategies` raise still yields `{"error": "<ClassName>"}` (no `str(exc)`).
- AC-5: assert imports are inside the handler (no new module-level import of strategy_builder_engine/community_strats); no `_SETTINGS_WRITE_ALLOWLIST`/`LIVE_EXECUTION` reference added.
- No hardcoded producer/metric values; assert shape/membership/kwarg-forwarding only.
- **Gate:** `-n0` scoped run on `tests/ui` + `tests/advisors` → genuine full-tree verifier vs base origin/main → `/review` → **PM LIVE functional test** (test-client POST `/ai-advisor/strategy-builder/run`: with the live weekly-cached community load present, the run includes community candidates in the gated batch; force an empty-load path → template-only; market-hours-safe via the Flask test client, NO 2nd trading daemon) → merge.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Wire at the route, not inside `propose_strategies` | Preserves the engine's caller-owns-the-Atlas-fetch design (DE-CS-001); the engine stays decoupled from the loader, mirroring the `live_returns` injection pattern. |
| Best-effort try/except around the community load | Defense-in-depth; the route must never fail a proposal run because community strategies were unavailable. Template-only is a valid run. |
| `force_refresh=False` (weekly cache) | Operator bill-protection directive — at most ~1 Atlas read/week/collection. |
| Lazy import inside the handler | Keeps `strategy_builder_engine`/`community_strats` off the live 1-minute execution path (existing CC-2 boundary). |

## Scope Boundaries
- **IN**: the `app.py` route wiring (lazy imports + load/adapt/pass community candidates + best-effort degradation) + tests + docs (DECISIONS entry + CLAUDE.md `community_strats`/`app.py` row updates reflecting the now-live production caller) + marking the stale `feature-plans/propose-strategies-community-wiring.md` superseded.
- **OUT**: any change to `strategy_builder_engine`/`community_strats`/`atlas_cache` (engine layer is already shipped); the FDR gate, screens, or persistence; the Strategy Builder template/JS; RF-1; F5; any execution-path/credential/allowlist change; any new Atlas collection or schema.
