# Feature: Community-Strats Atlas-Fetch Wall-Clock Timeout (HF-1 hang fix)
Status: ready
Created: 2026-06-17

## Summary
Bug fix for a regression HF-1 (#44) introduced: the Strategy Builder route `POST /ai-advisor/strategy-builder/run` now calls `community_strats.load_community_strategies`, whose live Atlas fetch (`community_strats._fetch_fn`: `pymongo.MongoClient(mongodb+srv://...)`) **HANGS >50s on SRV/TXT DNS resolution** when Atlas is slow/unreachable — `serverSelectionTimeoutMS=10_000`/`connectTimeoutMS=10_000` do NOT bound `mongodb+srv://` DNS resolution. The route's best-effort `try/except` cannot catch a hang, so the request hangs (confirmed live by an unbuffered timed probe: cache read errors → falls through to the live fetch → hang). This bounds the live fetch with a HARD wall-clock timeout so a hang degrades to `available=False` → the route degrades template-only (the existing HF-1 AC-2 path) instead of hanging. Advisory-only / off-execution-path / never-raising. `community_strats.py` only.

## Acceptance Criteria
- [ ] AC-1 (hang is bounded): when the live Atlas fetch (`_fetch_fn`) takes longer than the timeout, `load_community_strategies(force_refresh=False)` RETURNS within ~`_ATLAS_FETCH_TIMEOUT_S` + a small margin with `available=False` and a distinct `reason` (e.g. `"AtlasFetchTimeout"`) — it does NOT hang. Assert with a mocked `_fetch_fn`/seam that sleeps > the bound; assert the call returns `available=False` AND elapsed < bound + margin (timing assertion — the decisive guard).
- [ ] AC-2 (route degrades, no hang): with the fetch timing out, `POST /ai-advisor/strategy-builder/run` completes template-only (`community_candidates=[]`) and returns its normal JSON shape — it does not hang or 500. Assert via the Flask test client with the fetch-seam mocked to time out (the tests/app autouse stub already degrades; add a timing/non-hang assertion).
- [ ] AC-3 (no join-on-exit hang): the timeout wrapper must NOT block waiting for the hung worker thread on teardown. Do NOT use `with ThreadPoolExecutor() as ex:` (its `__exit__` calls `shutdown(wait=True)` → re-introduces the hang). Use explicit `submit` + `future.result(timeout=...)` + `ex.shutdown(wait=False)` (or `cancel_futures`) on timeout; the orphan worker thread is allowed to linger (it dies when the MongoClient eventually errors). Assert the bounded call returns within the bound EVEN THOUGH the worker is still sleeping (a sleep longer than the test's own wait).
- [ ] AC-4 (fast path unchanged): a fast/successful `_fetch_fn` returns its docs normally and `load_community_strategies` returns `available=True` with candidates — the timeout only fires on a genuine hang; no regression to the happy path. Assert with a fast mocked fetch returning fixture docs.
- [ ] AC-5 (never-raising / D-1): a timeout, a raised fetch, or a malformed result all yield `available=False` (never raise); reasons are class-name-ish / fixed strings, no `str(exc)` secret leakage (MONGO_URI etc. never surfaced). Assert several failure modes.
- [ ] AC-6 (bill-protection preserved): the weekly-cache path is unchanged — `force_refresh=False` still routes through `atlas_cache.cached_pull`; a successful pull still caches; the timeout wraps only the live-fetch leg. Assert the cache call path is intact (no extra Atlas reads introduced).

## Architecture
Edit `advisors/community_strats.py` only:
1. Add a named constant `_ATLAS_FETCH_TIMEOUT_S: float = 12.0` (source comment: bounds the live Atlas leg because `serverSelectionTimeoutMS` does not cover `mongodb+srv://` SRV/TXT DNS resolution; chosen > the 10s serverSelectionTimeoutMS so a reachable-but-slow Atlas still completes server selection, < a request-patience ceiling).
2. Wrap the live fetch in a hard wall-clock timeout. Keep `_fetch_fn` as-is (the real Mongo work); introduce a bounded wrapper passed to `cached_pull` (or wrap the `cached_pull` live-fetch leg):
   ```python
   import concurrent.futures  # module-level or local
   def _bounded_fetch_fn():
       ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
       fut = ex.submit(_fetch_fn)
       try:
           return fut.result(timeout=_ATLAS_FETCH_TIMEOUT_S)
       finally:
           ex.shutdown(wait=False, cancel_futures=True)   # NEVER wait=True — would block on the hung thread
   ```
   Pass `_bounded_fetch_fn` to `atlas_cache.cached_pull(...)`. On `concurrent.futures.TimeoutError`, map to the existing fetch-failure / never-raising path so `load_community_strategies` returns `available=False, reason="AtlasFetchTimeout"` (verify whether `cached_pull` swallows the exception → None, or whether `community_strats` must catch it and return the unavailable shape; handle at whichever layer makes the timeout surface as `available=False`).
3. No change to `atlas_cache.py`, the route, the engine, the cache schema, or the happy-path return shape.

## Design-System Mapping
N/A — backend producer fix; no UI.

## Edge Cases
- Live fetch hangs forever (SRV DNS) → bounded at `_ATLAS_FETCH_TIMEOUT_S` → available=False (AC-1/AC-3).
- Live fetch raises (auth/connection error) before the bound → existing never-raising path → available=False (AC-5).
- Cache HIT (data present) → no live fetch, no timeout overhead (AC-6).
- Fast successful fetch → normal data (AC-4).
- Orphan worker thread after a timeout → allowed to linger; must not accumulate unbounded in practice (route is on-demand; MongoClient eventually errors and the thread exits). Note in the plan; not a blocker.

## Security Considerations
- D-1: timeout/exception reasons are fixed strings / class names — never `str(exc)` (no MONGO_URI/credential leakage). The route already returns `{"error": type(exc).__name__}` on catastrophic failure.
- No new input surface; no new network calls (bounds an existing one). Advisory-only; not in `_SETTINGS_WRITE_ALLOWLIST`; no LIVE_EXECUTION.
- Availability: the fix REMOVES a hang (a DoS-on-self vector — repeated route POSTs could exhaust Flask threads on the unbounded hang).

## Testing Strategy
Unit tests (extend `tests/advisors/test_community_strats_wiring.py` or a new `tests/advisors/test_community_strats_timeout.py`) — NO live Atlas:
- AC-1/AC-3: mock the fetch seam (`_fetch_fn` or the MongoClient) to `time.sleep(bound*3)`; assert `load_community_strategies` returns `available=False, reason="AtlasFetchTimeout"` AND wall-clock elapsed < bound + ~3s (proves the bound fires + no join-on-exit hang). This is the decisive guard.
- AC-4: mock fetch to return fixture docs fast → `available=True` + candidates.
- AC-5: mock fetch to raise → available=False, no raise, no `str(exc)` leak.
- AC-6: assert `cached_pull` is still the path (force_refresh forwarded; no extra reads).
- AC-2 (route): Flask test client POST with the fetch seam timing out → route returns 200 template-only within the bound (no hang). (The tests/app autouse community stub already prevents real Atlas; add the timing/non-hang assertion.)
- **PM LIVE PROOF (the gate's decisive functional test):** with a real `_fetch_fn` that sleeps longer than the bound (or against the genuinely-unreachable Atlas), `load_community_strategies` returns `available=False` within the bound — no hang. Then redeploy + a real `POST /ai-advisor/strategy-builder/run` returns template-only fast (degrades, not hangs). Market-hours-safe (test client / direct call).
- Gate: scoped `-n0` on `tests/advisors` + `tests/app` → full-tree verifier vs base `31fb74c` (`--ignore=meta`, PM-owned bg, MONITOR RAM).

## Decisions
| Decision | Rationale |
|----------|-----------|
| Wall-clock `ThreadPoolExecutor` timeout, not a pymongo kwarg | `serverSelectionTimeoutMS`/`connectTimeoutMS` do NOT bound `mongodb+srv://` SRV/TXT DNS resolution (confirmed: hung >50s with those set). A wall-clock wrapper is the only reliable bound. |
| `shutdown(wait=False, cancel_futures=True)`, NEVER `with ... as ex:` | The context-manager `__exit__` joins (`wait=True`) → would block on the hung worker → re-introduce the hang. The orphan thread must be allowed to linger. |
| Fix-forward (not revert HF-1) | HF-1 is advisory/on-demand + the nightly is unaffected; this preserves HF-1's value + removes the hang. |
| `_ATLAS_FETCH_TIMEOUT_S = 12.0` | > 10s serverSelectionTimeoutMS (reachable-but-slow Atlas still completes), bounded request patience. Named constant, source-commented. |

## Scope Boundaries
- **IN**: `community_strats.py` wall-clock timeout wrapper + named constant + the `available=False, reason="AtlasFetchTimeout"` degradation + unit/route tests + docs (DECISIONS entry; CLAUDE.md community_strats row note that the Atlas fetch is wall-clock-bounded).
- **OUT**: `atlas_cache.py` changes; the route/engine/template; making Atlas reachable (infra/operator question); the cache schema; any orphan-thread reaping machinery (lingering thread accepted); RF-1; the W3 capstone.
