# 01-c4 fixtures: provenance

Plan: server-state-truth, cycle 4 (frozen figures fixed on the snapshot, window routes on the snapshot).

This folder holds no record files. Every record this cycle's tests use is derived at test time, in code (`tests/app/_refresh_timing_frozen_world.py`), from a signed-off record or the captured Composer body, with only the fields named below changed. Expected figures are recomputed from the same inputs (`composer_body(version)`), never copied from a response.

## Derived records

| Derived record (builder) | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| Composer total-stats body, versioned (`composer_body`) | `tests/fixtures/composer/v3-audit/total_stats.json` (captured) | `portfolio_value` + 10.0 x version, `simple_return` + 0.005 x version, `todays_percent_change` + 0.0005 x version; every other field as captured | a later account-totals read whose figures differ from the earlier one | S-5.1, S-5.5 to S-5.9, S-5.12 | pending: sign-off requested |
| Close cycle (`close_world`) | R6 earlier cycle `01-c2/shadow_rows_two_cycles.json` | cycle re-stamped to 16:00:30 ET (`retime_rows`: `trading_day`, `ts_et`, `ts_utc`, `cycle_id`) | a cycle whose rows are written before the snapshot's capture at 16:00:42 | all of AC-5 | pending: sign-off requested |
| Post-close cycles (`seed_after_close_cycles`, `engine_cycle_after_close`) | R6 cycles `20261021_1603` and the moved `20261021_1604`; the moved cycle re-stamped for later evening, overnight and pre-open moments | none for the first two (as captured, stamped 16:03 and 16:04, after the capture); `trading_day`, `ts_et`, `ts_utc`, `cycle_id` for the later ones | the engine keeps writing rows after the close | the after-close rule, S-5.2 to S-5.4 | pending: sign-off requested |
| Live state moved past the close (`drift_live_state`) | `01-c2/live_state_two_cycles.json` cycle `m` entries | `current_value` x 4 and `current_return` + 1.5 for the triggered symphony, `current_value` x 0.25 and `current_return` - 1.5 for the untriggered one | live `bot_state` the engine keeps saving after the close, differing from the snapshot's entries | S-5.2, S-5.3 | pending: sign-off requested |
| Next-session snapshot (`snapshot_variant`) | R8 | `trading_day` the next session, `captured_at_et` "16:00:30 ET" | the close step storing the next snapshot | S-5.9 | pending: sign-off requested |
