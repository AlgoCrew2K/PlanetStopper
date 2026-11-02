# static/request_guard.js

> The one request helper every page shares (`window.PSRequest`): newest-request-wins for GETs, and a POST path that refreshes the CSRF token after a restart but never sends a POST a second time.

**Source:** `static/request_guard.js`
**Last updated:** 2026-11-02

## Overview

`templates/_chrome.html` loads this file just before `static/chrome.js`, so `window.PSRequest` exists before any page script runs. State lives in the tab's memory only, so two tabs never share a token or a request counter.

## API Reference (`window.PSRequest`)

| Member | Behaviour |
|--------|-----------|
| `issue(key)`, `isLatest(key, n)` | each request issued under a key gets a number; a page draws an answer only while `isLatest` holds for it, so the newest issued request wins everywhere, not only in `loadState` |
| `getJson(url)` | one GET; a 2xx JSON body answers `{ok: true, status, body, timing}`; anything else answers `{ok: false, status, kind}` with no body, so a page cannot draw an error as data. A 401 is reported and `goLogin()` sends the page to `/login` |
| `csrfToken()`, `refreshCsrf()` | this tab's token, read once at load from `/api/csrf-token`; concurrent refreshes share one request |
| `noteDaemonStart(iso)` | a changed daemon start stamp (from `/api/state` or `/api/header-status`) means a restart, so a new token is read |
| `postAction(url, init)` | sends the caller's request exactly once. A 403 refreshes the token and is reported as refused; nothing in this module sends a POST a second time, so only the operator's next click can |
| `notSentText(res, clickAgain)` | the sentence a control shows for a refused POST, telling the operator the action was not sent and needs a new click |

The POST controls in the static scripts (Emergency Liquidate, Cash Now, settings saves, flush, approvals, the advisor, retirement and frontrunner actions) call `postAction`; `tests/ui/test_post_call_sites_census.py` is the census that pins this (not run for this document). This is the safety rule of the refresh-timing program: a liquidation or a settings write is never re-sent automatically after a token refresh.

## Consumed By
`postAction`: `static/chrome.js`, `static/index.js`, `static/ai_advisor*.js`, `static/settings.js`, `static/settings-modal.js`. `getJson` and the newest-wins counters: also `static/history.js` and `static/performance.js`, which have no POST control.

## Internal Dependencies
`/api/csrf-token`, `/login`, and `window.receiptTiming` from `static/chrome.js` (receipt timing of a response, measured on `performance.now()` and the response's `Date` header, never the browser's wall clock).
