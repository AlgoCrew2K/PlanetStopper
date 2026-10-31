# 04-c7 fixtures: provenance

Plan: refresh-timing 04, cycle C7 (the settings modal). This folder holds no record files; the request base for S-7.4 is
`04-c9/post_requests_at_base.json` (controls `modal_save` and `modal_save_go_live`).

## Derived records

| Builder | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| the `symphonies` fixture of `tests/ui/test_settings_modal_ordering_client.py` | the real `GET /api/symphony-settings/<id>` for two symphonies written through `database.save_state` and `database.set_symphony_live_mode` | two invented display names; symphony A on live, B on dry run | the capture holds no second symphony in the other live state | S-7.1 to S-7.6, S-2.13, S-2.19 | requested |
