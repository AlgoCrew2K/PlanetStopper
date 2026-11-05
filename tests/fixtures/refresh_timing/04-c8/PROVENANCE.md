# 04-c8 fixtures: provenance

Plan: refresh-timing 04, cycle C8 (the dashboard controls and the dismiss route). This folder holds no record files.

## Real answers (producer output)

| Answer | Producer call | Scenarios |
|---|---|---|
| 400 for the Cash Now body | `POST /api/sell_account` with `{"symphony_id": ...}` over a patched `.env` reader (`tests/app/_chrome_route_answers.sell_answer`) | S-2.5 |
| 403 HTML, rotated token | the CSRF hook with the check on; `GET /api/csrf-token` after `app._CSRF_TOKEN` is replaced | S-2.5, S-2.8, S-2.15 |
| the `cards-open` world | `tests/dashboard/_index_js_harness.get_world("cards-open")` | all |

## Derived records

| Builder | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| `test_a_poll_with_a_new_start_stamp_fetches_one_new_token` | the world `/api/state` body | `daemon_started_at` set to two chosen stamps | a restart seen between two polls | S-2.14 (dashboard part) | requested |

## Revision: the Cash Now rows no longer have a consumer

The "400 for the Cash Now body" answer was consumed only by `cash_now_400()` and `TestCashNow` in `tests/ui/test_dashboard_controls_refusal_client.py`, both deleted with the control (security-and-controls C2, GREEN `5b0ea52ac3011fd370f3c71b2221849cec7387f8`, RED `ee38b5d6fd47b8c6324d3dcfb4dacce58ee9aca9`). The same route answer for a symphony-only body is now asserted by `tests/dashboard/test_cash_now_removed.py`, taken from the real route at test time. The other rows and the `cards-open` world are unchanged and still used by the Fleet Dismiss and Managed Sleeves tests of that file.
