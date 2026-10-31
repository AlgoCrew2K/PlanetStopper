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
