# advisors/composer_draft_client

> Shared Composer write client for the Frontrunner Builder and the `propose_strategies` retrofit: creates a NEW, UNDEPLOYED symphony via `POST /api/v0.1/symphonies` and verifies zero allocation before an approval may be marked "uploaded".

**Source:** `advisors/composer_draft_client.py`
**Last updated:** 2026-07-11 (Wave-1 backend, frreview-APPROVED, unchanged by the subsequent P2-1/P2-2 hardening — those touched `frontrunner_detector.py`/`frontrunner_builder.py` only)

## Overview

`advisors/composer_draft_client.py` is the single shared write path both the Frontrunner Builder and the `strategy_builder_engine.propose_strategies` retrofit use to persist an approved candidate back to the operator's Composer account (feature-plans/frontrunner-builder.md AC-9/AC-10). It exposes exactly two functions — `save_symphony` and `verify_undeployed` — and nothing else.

### No-auto-trade boundary (structural, not policy)

This module deliberately does **not implement, import, or reference `invest_in_symphony`** or the `/deploy/.../invest` endpoint anywhere in its source. Deploying (funding/trading) a symphony is a separate Composer call this feature must never construct. The boundary holds by omission here and is additionally enforced by an adversarial source-scan security suite (`tests/security/test_frontrunner_no_trade_boundary.py`) that fails if a future edit ever reintroduces an invest/deploy-shaped symbol or URL fragment — see that suite's docs in [`advisors/frontrunner_builder`](advisors_frontrunner_builder.md).

### Live/test separation

Tests patch `requests.post`/`requests.get` to inject fixture responses. The module does not build a `requests.Session` internally, so the standard `patch("advisors.composer_draft_client.requests.post", ...)` intercept works with no extra wiring. No top-level network calls happen at import time.

### Error handling contract

Mirrors `composer_backtest_client.py`. Never raises on API or transport errors — every failure mode returns a `DraftResult` (or `bool` for `verify_undeployed`) with an explicit error signal. A single candidate's failure never aborts the caller's batch or crashes the approval route.

## Named Constants

| Name | Value | Purpose |
|------|-------|---------|
| `_BACKOFF_INTERVALS` | `(1.0, 2.0, 4.0, 8.0)` | Exponential backoff schedule — identical to `composer_backtest_client` |
| `DRAFT_MAX_RETRY_WAIT_SECONDS` | `15` (`sum(_BACKOFF_INTERVALS)`) | Maximum cumulative wait across all retries |
| `_CREATE_REQUEST_TIMEOUT` | `30` (seconds) | Per-request timeout for `save_symphony`'s `POST` |
| `_VERIFY_REQUEST_TIMEOUT` | `30` (seconds) | Per-request timeout for `verify_undeployed`'s `GET` |
| `_RETRYABLE_HTTP_STATUSES` | `{429, 500, 502, 503, 504}` | Transient statuses that warrant a retry |
| `_DEFAULT_ASSET_CLASS` | `"EQUITIES"` | Default per the pinned create contract |

## Public Types

### `DraftResult` (dataclass)

Structured result from `save_symphony`. Never `None`.

| Field | Type | Description |
|-------|------|--------------|
| `success` | `bool` | `True` on a successful create (or idempotent no-op echo) |
| `symphony_id` | `str \| None` | Populated on success (from the response body, or echoed on idempotent no-op); falsy on failure |
| `version_id` | `str \| None` | Populated on success when present in the response body |
| `error` | `str \| None` | Non-empty failure-reason string; `None` on success |

## API Reference

### `save_symphony(*, name, description, color, hashtag, raw_value, asset_class=_DEFAULT_ASSET_CLASS, already_uploaded_symphony_id=None, max_retries=4) -> DraftResult`

Creates a new **UNDEPLOYED** symphony via `POST /api/v0.1/symphonies` — the VALIDATED create contract (feature plan §Architecture: two independent third-party `composer-trade-mcp` mirrors agree exactly + match the OpenAPI doc, Medium-High confidence).

**Request:**
- Headers: `get_composer_headers()` (existing Composer creds, already write-capable — the engine already POSTs `go-to-cash`; no new secret)
- Body: `{"name", "asset_class", "description", "color", "hashtag", "symphony": {"raw_value": raw_value}}`. `raw_value` (the full validated symphony tree) is passed through **unchanged** — this client never mutates or re-derives fields into it.

**Parameters:**

| Name | Type | Description |
|------|------|--------------|
| `name`, `description`, `color`, `hashtag` | `str` | Composer symphony metadata fields per the pinned create contract |
| `raw_value` | `dict` | The full validated symphony tree |
| `asset_class` | `str` | `"EQUITIES"` (default) or `"CRYPTO"` |
| `already_uploaded_symphony_id` | `str \| None` | **Idempotency seam.** When supplied non-empty, this call is a no-op — no `POST` issued — and a success-shaped `DraftResult` echoing that id is returned immediately. Callers (the approval route) pass a candidate's previously-recorded `symphony_id`, so a duplicate Approve click never creates a second Composer symphony |
| `max_retries` | `int` | Bounded retry count after transient failures (default `4`; `0` = no retries; never unbounded) |

**Returns:** `DraftResult`. Never raises.

**Retry/error behavior:**
- `200`/`201` → success; parses `symphony_id`/`version_id` from the JSON body. Non-JSON or non-dict body → failure (`"invalid JSON..."`/`"unexpected response body shape..."`).
- `429` → honors `Retry-After` header when present (falls back to the first backoff interval); retried up to `max_retries`.
- `500`/`502`/`503`/`504` → exponential backoff retry up to `max_retries`.
- Any other status (e.g. `400` malformed `raw_value`) or retries exhausted → non-retryable failure, `error` carries `f"HTTP {status}: {response.text[:200]}"`.
- `requests.Timeout` → immediate failure (not retried — a timeout is treated as decisively failed, not transient).
- `requests.RequestException` (transport error) → retried with backoff up to `max_retries`, then failure carrying `type(exc).__name__`.

---

### `verify_undeployed(symphony_id: str, *, max_retries: int = 2) -> bool`

Reads back a created symphony and confirms it holds **zero allocation** — the AC-9 belt-and-suspenders safety check the approval route must call before marking an upload "uploaded". Mirrors `composer_backtest_client`'s `dvm_capital` read pattern.

**Parameters:**

| Name | Type | Description |
|------|------|--------------|
| `symphony_id` | `str` | The Composer symphony id returned by `save_symphony` |
| `max_retries` | `int` | Bounded retry count for transient failures (default `2` — a fast safety read, not a heavy backtest) |

**Returns:** `bool` — `True` **only** when the response is well-formed AND shows zero allocation for `symphony_id`. `False` on ANY error, transport failure, malformed/unparseable response, or non-zero allocation — **fail-closed**. Never raises.

**Zero-allocation determination:**
1. `GET /symphonies/{id}/score`, `200` response parsed as JSON dict.
2. No `dvm_capital` key, or it's empty/non-dict → `True` (never traded — the safest reading).
3. `dvm_capital[symphony_id]` looked up; if absent and `dvm_capital` has exactly one entry, falls back to that single series (same single-series fallback semantics as `composer_backtest_client._extract_returns` — the response key may not match `symphony_id` exactly).
4. No per-day series found → `True` (undeployed).
5. Series present → parses every value to `float`; unparseable → `False` (fail-closed). Otherwise `True` only if **every** value is exactly `0`.
6. Any retryable HTTP status → backoff retry up to `max_retries`, then `False`.
7. Non-retryable HTTP status, or a `requests.RequestException` after retries exhausted → `False`.

## Testing

`tests/advisors/test_frontrunner_approval.py` (approval orchestration, 8 tests) exercises `save_symphony`/`verify_undeployed` through `approve_frontrunner_proposal`'s call sites (HTTP mocked). `tests/security/test_frontrunner_no_trade_boundary.py` (10 tests) source-scans this module for the absence of any invest/deploy-shaped symbol or URL fragment and confirms `verify_undeployed` is referenced before any 'uploaded' status write. **Not yet exercised against the real Composer API** — the operator-gated "task zero" live test (one real `save_symphony` create, `verify_undeployed` confirmation, then delete the throwaway symphony) is still pending operator go-ahead; see feature plan §Architecture "Build task ZERO".

## Internal Dependencies

- `alpha_bot_execution` — `COMPOSER_BASE_URL`, `get_composer_headers` (reused, not duplicated)
- `requests` — the only network dependency
- `dataclasses`, `logging`, `time` — stdlib

## Consumers

- `advisors/frontrunner_builder.py::approve_frontrunner_proposal` — the **only** call site for `save_symphony` anywhere in the codebase (module-scope import restricted to this function; never referenced from the build/run path — see [`advisors/frontrunner_builder`](advisors_frontrunner_builder.md)'s no-auto-trade boundary section).
