# 01-c3 fixtures: provenance

Plan: server-state-truth, cycle 3 (dated stamps and the frozen trading day).

This folder holds no record files. Every record this cycle's tests use is derived at test time, in code (`tests/app/_refresh_timing_frozen_world.py`), from a signed-off record of `01-c2/` or from the captured Composer body, with only the fields named below changed. Nothing is typed from memory and no expected stamp is copied from a response: each expected string is built from the inputs the test passes in (the variant's day and capture time, or a seeded row's `ts_utc`).

## Derived records

| Derived record (builder) | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| Close snapshot variant (`snapshot_variant`) | R8 `01-c2/snapshot_two_symphonies.json` | `trading_day` set to the named day (the capture day, the session before it, a Friday, the Wednesday before Thanksgiving, the Friday before a Monday holiday, the Wednesday before a half day, the half day itself); `captured_at_et` set to "16:00:42 ET" (or "13:00:42 ET" on the half day) with `data_as_of` kept in step ("16:00 ET" / "13:00 ET"); or `trading_day` removed (a legacy snapshot) | the close snapshot of a chosen session, captured at a chosen second | S-3.1 to S-3.10, S-4.10, S-4.11 | pending: sign-off requested |
| Re-stamped engine cycle (`retime_rows`) | R6 `01-c2/shadow_rows_two_cycles.json` rows (and `01-c2/shadow_rows_cycle_m_moved.json`) | `trading_day`, `ts_et`, `ts_utc` (three seconds after `ts_et`, as the captured rows show) and `cycle_id` set for a chosen ET moment; every return, epoch, trigger and account field as captured | cycles at a mid-session moment (10:42 to 10:44), the last row of the previous day at 15:52, and a row at 23:59:30, instead of the captured 16:03 and 16:04 | S-4.1 to S-4.7, S-4.9, S-4.12 | pending: sign-off requested |
| Live state stamp (`save_state(..., stamp=...)`) | `01-c2/live_state_two_cycles.json` `last_successful_cycle_at` | replaced by the chosen cycle's stamp (the same second as that cycle's rows, sub-second part and offset as captured), by the text "not-a-timestamp", or removed | the engine stamps this even when a cycle recorded no observation | S-4.2 to S-4.4, S-4.6, S-4.7, S-4.12 | pending: sign-off requested |
| Close snapshot stamped after the seeded rows (`CLOSE_SNAPSHOT_CAPTURED_AT` in `tests/app/_refresh_timing_state_world.py`) | R8 | `captured_at_et` set to "16:05:00 ET" | the seeded closed worlds hold shadow rows stamped up to 16:04:01; the record's own 16:00:01 would put every seeded row after the capture, which the after-close rule excludes. Figures (not stamps) stay equal to the reference goldens | S-2.2, S-2.3, S-2.4, S-2.6, S-3.9 | pending: sign-off requested |
