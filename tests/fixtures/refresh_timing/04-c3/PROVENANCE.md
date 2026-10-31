# 04-c3 fixtures: provenance

Plan: refresh-timing 04, cycle C3 (`/history`). This folder holds no record files.

## Real answers (producer output, `tests/ui/_history_answers.py`)

| Answer | Producer call | Scenarios |
|---|---|---|
| `/api/history/<days>` body | the real route over the six captured post-mortem days (`tests/ui/_shell_support.seed_history_exits`) with the host clock frozen at 2026-07-25 12:00 ET | S-3.1, S-3.2, S-3.5 to S-3.8 |
| 500 HTML | `GET /api/history/30` with `analytics.get_history_summary` raising | S-3.5 |
| 503 JSON, 401 JSON | `GET` with the auth gate on, no credential / no session | S-3.4, S-3.5 |

## Derived records

| Builder | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| `test_the_drilled_day_is_redrawn_from_the_new_payload` | the real 30-day body | the drilled day last exit removed from `daily_exits` | a poll whose payload differs for the drilled day | S-3.1 | requested |
| `test_the_stamp_is_set_through_text_not_markup` | the real 30-day body | `data_as_of` set to a string containing markup | markup must not be written as markup | S-3.6 | requested |
| `_copy_post_mortem_to` (in `tests/app/test_history_today_fallback_date_filter.py`) | the captured post-mortem file with the most triggers | copied to a file dated for the Eastern day; in one test every `exit_reason` set to a marker | the Eastern-date file and a different UTC-date file must be told apart | S-3.3 | requested |
