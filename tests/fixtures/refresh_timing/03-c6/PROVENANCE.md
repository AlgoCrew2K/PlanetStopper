# 03-c6 fixtures: provenance

Plan: dashboard-refresh, cycle 6 (Cash Now's enabled state; the detail panel follows polls).

This folder holds no record files. Worlds are producer output (`default` and `mixed` of `tests/dashboard/_index_js_harness.py`); a derived body changes named fields of one symphony's entry in a real body.

## Derived records

| Builder | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| `_triggered_body` / `_untriggered_body` | `default` poll, equal stamp | one entry's `triggered`, `armed`, `status` (and `triggered_reason` for an exit) | a symphony exiting or not exiting between two polls | S-7.1 to S-7.6 | requested |
| `with_entry(state, id, current_return=5.55)` and the named fields below | `default` poll | `current_return` 5.55 or 7.5; `stop_trigger` -1.25 or -0.75; `shadow_hwm` 3.5; `mc_prob` 44 or 51; `symphony_vol` 0.123; for an exit `triggered`, `armed`, `status`, `triggered_at_return` -3.21, `guard_alpha` 0.8; `current_return` 4.25 | figures the detail panel must follow | S-8.1, S-8.2 | requested |
| `without_symphony` | `default` poll | one symphony removed from the card list and the state map | what a poll looks like after an account pick | S-8.6 | requested |
| detail answers | `default` `GET /api/chart/<id>` (first symphony's tape shortened by five points) | `marker` key added; logs, settings and performance bodies written by the tests in the shape the panel reads (`events-timeline`, `vars-section`, `detail-risk-profile`) | two symphonies' four answers that tell apart | S-8.4 | requested |
| `/api/sell_account` answers | none (the route's success shape is unreachable at the base: it refuses a request without `confirm_account_id`) | status 200 with `{}` or `{"status": "success"}`; status 400; pending | harness route specs; no sell is sent by anything here | S-7.3 to S-7.6 | requested |

The request equality in S-7.4 is the literal the page has always sent: URL `/api/sell_account`, method POST, headers `Content-Type` and `X-CSRF-Token`, body `{"symphony_id": <id>}` as compact JSON.
