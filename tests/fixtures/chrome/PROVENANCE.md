# Provenance: the request Emergency Liquidate sends, at the base

Used by `tests/app/test_chrome_header_honesty.py` and `tests/ui/test_panic_flow_browser.py`. One file of
data, `panic_request_at_base.json`; the script that wrote it, `capture_panic_request.py`, is committed
beside it.

## Record: `panic_request_at_base.json` (case: one Confirm click, two Confirm clicks, the Confirm gate)

- Source: the real `static/chrome.js`, evaluated unedited under `node` by `tests/app/_chrome_node_harness.py`
  against a stub document, with every response it was given taken from the real Flask app through its
  test client (`tests/app/_chrome_route_answers.py`): `GET /api/csrf-token`, `GET /api/accounts` over a
  temporary env file holding one account id, and `POST /api/sell_account` (the dry-run answer, with the
  Composer and Discord calls and any liquidation thread replaced, so nothing leaves the process).
- Producer call: `python tests/fixtures/chrome/capture_panic_request.py` with `DB_PATH` and
  `ATLAS_CACHE_DB_PATH` set to scratch files. The script refuses to run when `static/chrome.js` or
  `templates/_chrome.html` differs from `HEAD`.
- Base SHA: `f9cb367668872b16d22877e6b7490c0950a12cba` (branch `lane/ux-04` before any edit of the shell
  work; also written into the file as `base_sha`). Time: see `captured_at_utc` in the file.
- Contents: `inputs` (the app's real CSRF token, 64 hex characters, and an account id in UUID form
  generated for the run), `single_confirm_request` (URL, method, headers and body string exactly as
  `fetch` received them), `two_confirm_clicks_request_count`, `confirm_gate_oninput` (the `oninput` value
  read from the rendered chrome).
- Scenarios served: the request-unchanged rows (S-4.4, S-4.8, S-9.8) and the Confirm gate row (S-4.2).
  Tests replay the recorded inputs, substitute this run's real token for the recorded one, and compare.
- Edits: none. Nothing in the file was typed or changed after the script wrote it.
- Sign-off: not needed (no derived record).

## Derived at test time (not stored)

These are computed inside tests from a real answer of the app by deleting one named field; no derived
record is committed.

| Source answer | Changed field | Reason | Scenario | Sign-off |
|---|---|---|---|---|
| `POST /api/trigger` 200 body | `message` deleted | the button's fallback text when a 2xx answer carries no message | S-8.1 (fallback expression) | pending: the PM signs off |
| `POST /api/sell_account` 200 dry-run body | `message` deleted | the alert's fallback expression when a 2xx answer carries no message | S-9.7 (fallback expression) | pending: the PM signs off |
| `POST /api/sell_account` 200 live-start body | `message` deleted | the alert's fallback expression, live branch | S-9.7 (fallback expression) | pending: the PM signs off |

## Answers taken from the real app at test time (not stored)

The five 400 bodies, the 403 HTML page from the CSRF hook, the 401 and 503 JSON bodies from the auth hook,
the 500 page from Flask's own handler (route's env read made to raise, exceptions not propagated), the
200 dry-run and live-start bodies, and the `/api/trigger` bodies. The live-start body needs
`threading.Thread` replaced for the duration of the request; no thread runs.

The capture harness account `7c2607c5-4583-4c1b-ae55-e5a05bb49f0d` is invented; it is not a real account id.
