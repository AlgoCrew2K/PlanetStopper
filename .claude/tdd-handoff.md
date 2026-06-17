# TDD Handoff
Plan: feature-plans/community-strats-atlas-timeout.md
Branch: fix/community-strats-atlas-timeout
Phase: red

## Summary
Bug fix: `load_community_strategies` live Atlas fetch hangs >50s on SRV/TXT DNS resolution.
`serverSelectionTimeoutMS`/`connectTimeoutMS` do NOT bound `mongodb+srv://` DNS.
Fix: wall-clock `ThreadPoolExecutor` timeout wrapper (`_bounded_fetch_fn`) with `shutdown(wait=False)`.
Constant: `_ATLAS_FETCH_TIMEOUT_S = 12.0` (in `advisors/community_strats.py`).
Result shape on timeout: `{available: False, reason: "AtlasFetchTimeout", candidates: [], stats: {...}, source: "captplanet"}`.
Only file changed: `advisors/community_strats.py`.

## Test Files
- `tests/advisors/test_community_strats_timeout.py` — 14 tests (all RED)

## A/C Coverage Matrix

| A/C ID | Description | Test File | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| AC-1 | Hang is bounded — returns available=False, reason=AtlasFetchTimeout, elapsed < bound+margin | test_community_strats_timeout.py | TestHangIsBounded::test_slow_fetch_returns_available_false_with_atlas_fetch_timeout_reason | RED |
| AC-2 | Route degrades template-only, no hang, status 200 within bound | test_community_strats_timeout.py | TestRouteDegradesTemplateOnly::test_post_strategy_builder_run_completes_within_bound_when_fetch_times_out, test_post_run_with_timeout_response_has_expected_shape | RED |
| AC-3 | No join-on-exit hang — worker thread still sleeping when call returns | test_community_strats_timeout.py | TestHangIsBounded::test_no_join_on_exit_hang_worker_still_sleeping | RED |
| AC-4 | Fast path unchanged — available=True with candidates | test_community_strats_timeout.py | TestFastPathUnchanged::test_fast_fetch_returns_available_true_with_candidates, TestFastPathUnchanged::test_fast_fetch_elapsed_is_well_below_bound | RED |
| AC-5 | Never-raising / D-1 — timeout/raise/malformed all return available=False, no secret leakage | test_community_strats_timeout.py | TestNeverRaisingAndD1::test_timeout_reason_is_fixed_string_not_class_name, TestNeverRaisingAndD1::test_raising_mongo_returns_available_false_no_raise_no_secret_leak, TestNeverRaisingAndD1::test_malformed_fetch_result_returns_available_false, TestNeverRaisingAndD1::test_function_never_raises_on_any_seam_failure | RED |
| AC-6 | Bill-protection preserved — cache hit bypasses live fetch; constant correct | test_community_strats_timeout.py | TestCachePathIntact::test_cache_hit_does_not_invoke_mongo_client, TestCachePathIntact::test_force_refresh_true_invokes_fetch_seam, TestCachePathIntact::test_atlas_fetch_timeout_constant_exists_and_is_positive_float_gt_10 | RED |

## Fetch Seam Design (CRITICAL — read before implementing)

`_fetch_fn` is a **nested def** inside `load_community_strategies` — NOT a module-level name.
Therefore `monkeypatch.setattr(community_strats, "_fetch_fn", ...)` will NOT work.

The correct test seam uses **two layers**:

**Layer 1 — force the live-fetch leg to execute:**
```python
patch("advisors.atlas_cache.cached_pull", side_effect=lambda col, fn, **kw: fn())
```
This makes `cached_pull` bypass its cache logic and directly call whatever `fetch_fn` it receives.
After implementation, `load_community_strategies` will pass `_bounded_fetch_fn` as `fetch_fn`.
`_bounded_fetch_fn` wraps `_fetch_fn` in a `ThreadPoolExecutor`.
`_fetch_fn` calls `pymongo.MongoClient(...)`.

**Layer 2 — control what the innermost call does:**
```python
patch("pymongo.MongoClient", side_effect=lambda *a, **kw: (time.sleep(seconds), MagicMock())[1])
```
Or for a raising seam:
```python
patch("pymongo.MongoClient", side_effect=RuntimeError("msg"))
```
Or for a fast-success seam — patch `pymongo.MongoClient` to return a mock whose chain
(`client["captplanet"]["strategies"].find({}, projection)`) returns an iterator of fixture docs.

**Why this works:**
- pre-implementation: `cached_pull` is patched to call `fn()` which is the original `_fetch_fn`
  (not `_bounded_fetch_fn`). `_fetch_fn` calls `MongoClient`. Tests fail because there is no
  `_ATLAS_FETCH_TIMEOUT_S`, no timeout wrapper, and timing assertions fail.
- post-implementation: `cached_pull` is patched to call `fn()` which is `_bounded_fetch_fn`.
  `_bounded_fetch_fn` submits `_fetch_fn` to a thread and waits `_ATLAS_FETCH_TIMEOUT_S`.
  `_fetch_fn` calls `MongoClient` (which is patched to sleep/raise/return docs).

**AC-3 discriminator timing:**
MongoClient sleeps `_BOUND * 5 = 60s`. We assert elapsed < `_BOUND + 3.0 = 15s`.
- Correct impl (`shutdown(wait=False)`): returns in ~12s, elapsed < 15s — PASS.
- Wrong impl (`with ThreadPoolExecutor() as ex:`): blocks ~60s — FAIL.

## Import Stubs Created
None — no new modules introduced. `_ATLAS_FETCH_TIMEOUT_S` does not exist yet;
tests that reference it will fail with `AttributeError` (correct RED signal for constant-check test).

## Implementer Instructions

**File to edit:** `advisors/community_strats.py` ONLY.

**Changes required:**

1. Add at module level (near other imports):
   ```python
   import concurrent.futures
   ```

2. Add module-level constant with source comment:
   ```python
   # Wall-clock bound for the live Atlas fetch leg. serverSelectionTimeoutMS /
   # connectTimeoutMS do NOT cover mongodb+srv:// SRV/TXT DNS resolution (confirmed:
   # hangs >50s with those set). Chosen > 10s serverSelectionTimeoutMS so a
   # reachable-but-slow Atlas still completes server selection.
   _ATLAS_FETCH_TIMEOUT_S: float = 12.0
   ```

3. Inside `load_community_strategies`, replace the `_fetch_fn` nested def + `atlas_cache.cached_pull(...)` call with:
   ```python
   def _fetch_fn() -> list:
       """Connect to Atlas and return the projected strategy documents."""
       import pymongo  # noqa: PLC0415
       mongo_client = pymongo.MongoClient(
           os.environ["MONGO_URI"],
           serverSelectionTimeoutMS=10_000,
           connectTimeoutMS=10_000,
       )
       collection = mongo_client["captplanet"]["strategies"]
       cursor = collection.find({}, _PROJECTION)
       return list(cursor)

   def _bounded_fetch_fn() -> list:
       """Wrap _fetch_fn with a wall-clock timeout.

       Uses ThreadPoolExecutor with shutdown(wait=False) so a hung worker thread
       (e.g., blocked on SRV/TXT DNS resolution) does not block the caller on exit.
       The orphan thread is allowed to linger; MongoClient eventually errors.
       """
       ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
       fut = ex.submit(_fetch_fn)
       try:
           return fut.result(timeout=_ATLAS_FETCH_TIMEOUT_S)
       except concurrent.futures.TimeoutError:
           raise  # re-raise so cached_pull's except block maps it to available=False
       finally:
           ex.shutdown(wait=False, cancel_futures=True)  # NEVER wait=True

   # Route through the weekly cache. The bounded wrapper is the live-fetch leg.
   raw_docs = atlas_cache.cached_pull(
       _COLLECTION_NAME,
       _bounded_fetch_fn,
       force_refresh=force_refresh,
   )
   ```

4. Map `concurrent.futures.TimeoutError` to `available=False, reason="AtlasFetchTimeout"`.
   Check how `cached_pull` handles exceptions from `fetch_fn`:
   - Looking at `atlas_cache.py`: `cached_pull` has `try: fetched_payload = fetch_fn(); fetch_succeeded = True`
     with `except Exception as exc: logger.warning(...)` → sets `fetch_succeeded = False` → returns stale or None.
   - So `concurrent.futures.TimeoutError` raised from `_bounded_fetch_fn` is caught by `cached_pull`
     and `cached_pull` returns `None` (no stale row) → `load_community_strategies` returns
     `{available: False, reason: "AtlasCacheUnavailable"}`.
   - BUT the plan requires `reason: "AtlasFetchTimeout"` (distinct from "AtlasCacheUnavailable").
   - Therefore: catch `concurrent.futures.TimeoutError` in `_bounded_fetch_fn` and re-raise as a
     named exception, OR catch it in the outer `load_community_strategies` try/except.
   - Simplest approach: wrap `atlas_cache.cached_pull(...)` call in a try/except that specifically
     catches the timeout and returns the AtlasFetchTimeout shape. But `cached_pull` swallows it.
   - CORRECT approach: do NOT re-raise from `_bounded_fetch_fn`. Instead, return a sentinel value
     (or raise a custom exception that `cached_pull` swallows → returns None), then check `raw_docs is None`
     and determine reason. But we can't distinguish AtlasFetchTimeout from AtlasCacheUnavailable at that point.
   - BEST approach: catch `concurrent.futures.TimeoutError` inside `_bounded_fetch_fn` and raise
     a custom exception class (e.g. `_AtlasFetchTimeoutError(Exception)`) defined in the module.
     Then in `load_community_strategies`'s outer `except Exception as exc:` block, it becomes
     `reason = type(exc).__name__` = `"_AtlasFetchTimeoutError"` — but that's not `"AtlasFetchTimeout"`.
   - SIMPLEST CORRECT approach: in `load_community_strategies`, wrap the `atlas_cache.cached_pull(...)` call
     with an explicit concurrent.futures.TimeoutError catch BEFORE it reaches cached_pull. But the timeout
     is raised INSIDE the thread, propagated via `fut.result(timeout=...)` which raises `TimeoutError` in
     the calling thread (inside `_bounded_fetch_fn`), which cached_pull catches.
   - The implementer must choose: either let `cached_pull` swallow and accept `reason="AtlasCacheUnavailable"`,
     OR ensure the timeout surfaces as `reason="AtlasFetchTimeout"`.
   - The plan and tests REQUIRE `reason="AtlasFetchTimeout"`. So the implementer needs to route around
     `cached_pull`'s exception-swallowing by either:
     (a) Catching TimeoutError in `_bounded_fetch_fn`, logging it, then returning a sentinel (but then
         raw_docs is a sentinel not None — the downstream code needs to handle it)
     (b) Not using `_bounded_fetch_fn` as the `cached_pull` fetch_fn, but instead wrapping the
         entire `cached_pull` call:
         ```python
         try:
             raw_docs = atlas_cache.cached_pull(_COLLECTION_NAME, _bounded_fetch_fn, force_refresh=force_refresh)
         except concurrent.futures.TimeoutError:
             return {available: False, reason: "AtlasFetchTimeout", ...}
         ```
         But `cached_pull` swallows it, so this won't work either.
     (c) Run `_bounded_fetch_fn` outside of `cached_pull` then pass result: complex.
     (d) Raise a unique exception subclass from `_bounded_fetch_fn`, catch it in `load_community_strategies`'s
         outer except BEFORE the generic except block catches it.

   The CLEANEST solution: wrap `raw_docs = atlas_cache.cached_pull(...)` in a try/except that catches
   `concurrent.futures.TimeoutError` specifically. But `cached_pull` catches it first.

   Actually, re-reading `atlas_cache.py` carefully: `cached_pull` catches ALL exceptions from `fetch_fn`.
   So `concurrent.futures.TimeoutError` is caught by `cached_pull` → `fetch_succeeded=False` → returns stale or None.
   The implementer needs a way to surface the timeout specifically.

   The plan says: "handle at whichever layer makes the timeout surface as `available=False, reason='AtlasFetchTimeout'`".

   **RECOMMENDED implementation (note for implementer):**
   Have `_bounded_fetch_fn` catch `concurrent.futures.TimeoutError` and re-raise as a new exception class:
   ```python
   class _AtlasFetchTimeout(Exception):
       """Raised when the bounded Atlas fetch exceeds _ATLAS_FETCH_TIMEOUT_S."""
   
   def _bounded_fetch_fn() -> list:
       ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
       fut = ex.submit(_fetch_fn)
       try:
           return fut.result(timeout=_ATLAS_FETCH_TIMEOUT_S)
       except concurrent.futures.TimeoutError:
           raise _AtlasFetchTimeout("Atlas SRV/DNS fetch timed out")
       finally:
           ex.shutdown(wait=False, cancel_futures=True)
   ```
   Then in `load_community_strategies`'s outer except block:
   ```python
   except _AtlasFetchTimeout:
       return {available: False, reason: "AtlasFetchTimeout", candidates: [], stats: dict(_EMPTY_STATS), source: "captplanet"}
   except Exception as exc:
       return {available: False, reason: type(exc).__name__, ...}
   ```
   This gives `reason="AtlasFetchTimeout"` (not `"_AtlasFetchTimeout"`) because it's a hardcoded string.

   Tests assert `reason == "AtlasFetchTimeout"` exactly. The implementer must use this string.

## How to Run Tests

```bash
cd /tmp && python -m pytest "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/atlas-timeout-team/tests/advisors/test_community_strats_timeout.py" -v -p no:xdist -n0
```

Target: all 14 tests fail on assertions (not import errors).

Regression suite after GREEN:
```bash
cd /tmp && python -m pytest "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/atlas-timeout-team/tests/advisors" "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/atlas-timeout-team/tests/app" -v -p no:xdist -n0
```

## After GREEN

1. Commit path-scoped (NEVER `git add -A`):
   ```bash
   git -C "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/atlas-timeout-team" add advisors/community_strats.py
   git -C "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/atlas-timeout-team" commit -m "fix(community-strats): wall-clock timeout for Atlas fetch — bounds HF-1 hang regression"
   ```

2. SendMessage `at-test-writer` with: "GREEN — N/N tests passing on <SHA>. Ready for review."

## Test File Issues (for test-writer to fix)
None yet.

## Disputed Tests
None.

## Status Log
- [2026-06-17] test-writer (at-test-writer): Starting RED phase — atlas-timeout fix
- [2026-06-17] test-writer (at-test-writer): RED complete — 14 tests written (all failing on assertions or AttributeError for constant-check), 0 stubs created
