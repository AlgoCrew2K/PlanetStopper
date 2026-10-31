# 03-c2 fixtures: provenance

Plan: dashboard-refresh, cycle 2 (errors are never drawn as data; the event stream re-arms).

This folder holds no record files. Worlds and the cycle event payload are as in `03-c1/PROVENANCE.md`.

## Real error responses (producer output, `error_response` in `tests/dashboard/_refresh_timing_support.py`)

| Kind | Producer call | Status and body | Scenarios |
|---|---|---|---|
| `500` | `GET /api/state` with `app.get_api_state_dict` raising | 500, JSON `{"status": "error", "message": "An internal error occurred"}` | S-2.1, S-2.8, S-2.10 to S-2.15 |
| `401` | `GET /api/state` with the auth gate on, no session | 401, JSON `{"error": "unauthenticated"}` | S-2.2, S-3.5 |
| `503` | `GET /api/state` with the auth gate on and no secret key | 503, JSON `{"error": "misconfigured"}` | S-2.3 |
| `403-html` | `POST /api/trigger` with the CSRF gate on and no token | 403, Werkzeug's HTML page | S-2.4 |
| waiting body | `GET /api/state` on an empty state database with the market closed | 200, JSON with `status: "waiting"` and `notice` | S-2.9 |
| trigger body | `POST /api/trigger` | 200, JSON `{"status": "success", ...}` | S-2.14 |

## Derived records

| Builder | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| `200-not-json` (in `test_dashboard_poll_errors.py`) | the `403-html` response | status 200 instead of 403, text unchanged | a proxy answering 200 with an HTML page (plan S-2.7, FX-ERR-NOTJSON) | S-2.7 | requested |
| `200-without-data` | the `500` response | status 200 instead of 500, body unchanged | an error-shaped JSON body under a 200 status (the shape check, not the status, must reject it) | S-2.1 | requested |
| `_summaries` (in `test_dashboard_event_stream.py`) | `world["summaries"]["30d"]` and `["1y"]` through `summary_with` | `cumulative_saved_dollars` (+ net of friction, realized and its net), `guard_event_count`, `realized_coverage` set to 1234.56 / 3 events / 2 of 3 for 30d and -222.25 / 7 events / 4 of 5 for 1y | the seeded world holds no guard events, so both windows read "No guard events yet"; two windows must read differently to show which one the panel holds | S-3.3, S-3.4, S-3.8, S-3.9 | requested |
