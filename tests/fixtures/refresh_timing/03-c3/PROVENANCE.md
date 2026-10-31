# 03-c3 fixtures: provenance

Plan: dashboard-refresh, cycle 3 (age cue, catch-up, true countdown) and the overnight scenarios.

This folder holds no record files. Worlds are as in `03-c1/PROVENANCE.md`; an HTTP `Date` header is the harness page clock when the response is made (a server on the same clock) unless a scenario skews it, as a real server's `Date` is its own clock (the suite approach's RC-3).

## Derived records (`poll_body` in `tests/dashboard/test_dashboard_liveness.py`, over `open_body`, `closed_body`, `with_cycle_at`, `with_next_run`)

| Case | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| open or closed poll | `world["state"]` | `market_state`, `meta.market_state`, `meta.system_online`, `engine_phase` | the world is an open-market capture; the closed, blackout and opening bodies are the same body with the phase fields plan 02 serves | S-4.1 to S-4.15, overnight | requested |
| cycle age | the poll | `last_successful_cycle_at` set to the page clock minus a chosen age, in the engine's format (aware, Eastern offset) | a cycle 10 s, 30 s, 100 s or 4 min old, or none | S-4.1, S-4.10 to S-4.14 | requested |
| countdown | the poll | `next_run_seconds` set to 40, 10, or the seconds to the next :00 of the chosen instant | the server reckons it early in the request (plan Trigger 4) | S-4.7 to S-4.10 | requested |
| degraded | the poll | `degraded_reasons` set to a list of the reasons plan 02's `_health_status` can report, one unknown reason, or the empty list | the plan's S-4.15 | S-4.15 | requested |

## Clock skews and instants chosen by the tests

| Instant or skew | Meaning | Scenarios |
|---|---|---|
| 2026-10-30T14:00:20Z | 10:00:20 EDT, 40 s before the next engine minute | S-4.x |
| a browser clock 187 s ahead or 600 s behind the server's | "three minutes fast" and a slow clock | S-4.10, S-4.12 |
| Sunday 2026-11-01T22:00:07Z to Monday 2026-11-02T14:31Z | overnight across the end of daylight saving (clocks went back at 06:00Z on Sunday): 09:30 EST is 14:30Z | overnight (added: proposed S-4.16 to S-4.19) |
| Friday 2026-10-30T20:30:07Z to Monday 2026-11-02T14:31:07Z | a laptop asleep for the weekend | overnight (proposed S-4.17, S-4.18) |
