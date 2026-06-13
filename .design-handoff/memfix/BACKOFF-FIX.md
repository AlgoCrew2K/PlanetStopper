# BACKOFF-FIX — _fetch_with_backoff Infinite-Loop / OOM Fix

## Bug Summary

`ai_advisor._fetch_with_backoff` (lines 354-441 pre-fix) contained an infinite
loop that activated once the retry budget was exhausted while the server
continued returning HTTP 429 (or raising ConnectionError/Timeout).

Root cause: `delay` collapses to `0.0` via `min(delay*2, 8.0 - total_waited)`
once `total_waited == 8.0`.  The original retry guard used `<= 8.0`, which
became `8.0 + 0.0 <= 8.0 = True` permanently.  Result: `time.sleep(0)` +
`requests.get()` spinning with no bound, leaking a `requests.Response` object
per iteration until the process committed ~100 GB of memory.  The
ConnectionError/Timeout path had the same defect: `total_waited + delay > 8.0`
became `8.0 > 8.0 = False`, so the `raise` was never reached.

## Diff Summary

### `ai_advisor.py`

- **New constant** `_FETCH_MAX_ATTEMPTS: int = 6` added at line 292 (adjacent
  to `_FETCH_MAX_BACKOFF_TOTAL_WAIT_S`).  Source comment explains the 8.0s
  budget spends in ~4-5 attempts at 1s→2x; 6 is a defense-in-depth ceiling.

- **`_fetch_with_backoff` rewritten** to compute `can_retry` as a single
  boolean expression before the `try` block (not inline in two separate guards):

  ```python
  can_retry = (
      attempt < _FETCH_MAX_ATTEMPTS       # condition 1: hard ceiling
      and delay > 0.0                     # condition 2: delay not collapsed
      and total_waited + delay <= _FETCH_MAX_BACKOFF_TOTAL_WAIT_S  # condition 3
  )
  ```

  - 429 path: `if resp.status_code == 429 and can_retry:` — returns response
    when `can_retry` is False (callers run `raise_for_status()`; the existing
    contract is preserved).
  - ConnectionError/Timeout path: `if not can_retry: raise` — raises the last
    exception when budget is spent.

- Public signature, 8.0s budget constant, 15s request timeout, and all normal/
  bounded-retry behavior are unchanged.

### `tests/ai_advisor/test_backoff_termination.py` (NEW FILE)

Ten regression tests for the fixed backoff, all patching `ai_advisor.requests.get`
and `ai_advisor.time.sleep` so no live network I/O occurs:

| Test | Assertion |
|------|-----------|
| `test_backoff_429_terminates_within_max_attempts` | call count <= `_FETCH_MAX_ATTEMPTS`; returns 429 response |
| `test_backoff_429_call_count_is_strictly_positive` | at least 1 GET made |
| `test_backoff_connection_error_terminates_within_max_attempts` | raises within cap |
| `test_backoff_timeout_error_terminates_within_max_attempts` | raises within cap |
| `test_backoff_success_on_first_attempt_makes_exactly_one_call` | exactly 1 call, no sleep |
| `test_backoff_non_429_error_code_is_returned_immediately` | 500 returned, no retry |
| `test_backoff_429_then_200_retries_and_succeeds` | 2 calls, 1 sleep, 200 returned |
| `test_backoff_headers_and_params_forwarded` | kwargs forwarded; explicit timeout present |
| `test_fetch_max_attempts_constant_exists_and_is_positive_int` | constant shape |
| `test_fetch_max_backoff_total_wait_constant_exists` | pre-existing constant unchanged |

### `tests/ai_advisor/conftest.py` (NEW FILE)

Provides `stub_network_producers` fixture (NOT autouse): patches
`_build_sentiment_section`, `_build_macro_section`, `_build_fundamentals_section`
with honest `available=False` stubs.  Only consumed by `test_ai_advisor.py`
via its module-scoped autouse fixture.

### `tests/ai_advisor/test_ai_advisor.py`

Added `_block_network_producers` autouse fixture (module scope) that requests
`stub_network_producers` from conftest.  All 35 existing tests in this file now
run without hitting live GDELT/FRED/SEC endpoints.  No test signatures changed.

### `tests/ai_advisor/test_cycle2_lens_producers.py`

Added `deadline=None` and `patch("ai_advisor.time.sleep")` to the hypothesis
test `test_available_false_payload_always_empty`.  The test was previously
exploiting the broken zero-sleep spin (completing in <200ms); with the fix, the
real backoff sleep runs (up to 8s) and hypothesis reported `DeadlineExceeded`.
`deadline=None` is correct: the test asserts lens-block shape, not timing.

## RED → GREEN Evidence

### Regression test call counts (backoff termination)

All tests patch `time.sleep` to avoid real waits.

| Scenario | requests.get calls | Expected (max) |
|----------|--------------------|----------------|
| Always 429 | 6 | <= 6 (_FETCH_MAX_ATTEMPTS) |
| Always ConnectionError | 6 | <= 6 |
| Always Timeout | 6 | <= 6 |
| 200 on first | 1 | 1 (no retry) |
| 500 on first | 1 | 1 (no retry) |
| 429 then 200 | 2 | 2 |

### Test runs

```
tests/ai_advisor/test_backoff_termination.py   10 passed
tests/ai_advisor/test_ai_advisor.py            35 passed
tests/ai_advisor/test_cycle2_lens_producers.py  (hypothesis fixed) 1 passed
```

The hypothesis test `test_available_false_payload_always_empty` was a confirmed
pre-fix OOM trigger: running it against the unpatched code caused `_fetch_with_backoff`
to spin indefinitely in the git stash verification step (bash process had to be
killed).

## ai_advisor Context Tests — Hermeticity Confirmation

Before this fix, `assemble_advisor_context` called the three live-network
producers directly, meaning every call from `test_ai_advisor.py` fixtures
attempted live GDELT/FRED/SEC EDGAR requests.  A persistent 429 from any of
those endpoints would have triggered the infinite-loop bug INSIDE a unit test.

After this fix:
- `tests/ai_advisor/conftest.py` provides `stub_network_producers`.
- `test_ai_advisor.py` declares `_block_network_producers(autouse=True)` which
  patches all three producers for every test in that module.
- No test in `test_ai_advisor.py` touches live network.
- `test_cycle2_lens_producers.py` and `test_backoff_termination.py` are
  unaffected — they mock at the `requests.get` level and call real producer
  bodies as required by their test contracts.

## Retry Config

- Backoff formula: `delay = min(delay * 2, _FETCH_MAX_BACKOFF_TOTAL_WAIT_S - total_waited)`
  starting from `delay = 1.0`.
- Max total wait: `_FETCH_MAX_BACKOFF_TOTAL_WAIT_S = 8.0` seconds.
- Max attempts: `_FETCH_MAX_ATTEMPTS = 6` (hard ceiling).
- Idempotency: all callers are read-only GET requests; no write idempotency key
  required (no state mutation on retry).

## is_live Propagation

This fix touches only `_fetch_with_backoff` (read-only GET helper for free
public APIs: GDELT, FRED, SEC EDGAR).  None of these calls are gated by
`is_live` — they are advisor-context enrichment, not broker writes.  The
`is_live` guard applies to Composer liquidations and Alpaca order writes, which
are on separate code paths in `alpha_bot_execution.py` and `app.py`.

## Commit SHA

`948fb02` on branch `fix/test-memory-blowup`
