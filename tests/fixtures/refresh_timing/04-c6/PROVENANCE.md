# 04-c6 fixtures: provenance

Plan: refresh-timing 04, cycle C6 (`/settings`). This folder holds no record files; the request base for S-6.5 is
`04-c9/post_requests_at_base.json` (control `flush_confirm`).

## Derived records

| Builder | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| `tests/ui/_settings_page_answers.seed_state(flags=True)` and the `state` fixture of `tests/app/test_armed_symphonies_route.py` | the captured production `bot_state` (`tests/fixtures/prod_droplet/bot_state_positions.json`) | `armed` on one symphony, `tp_armed` + `para_armed` on a second, `triggered` on a third, all four flags false on a fourth (the capture already carries `triggered=true` on 8 of its 11 symphonies, which the route lists as the flag it is) | the capture omits the engine arming flags but `triggered`; the flush confirm lists the armed ones | S-6.3, S-2.16 | requested |

## Real answers (producer output)

`GET /api/settings`, `POST /api/settings` (over a temporary `.env`), `GET /api/armed-symphonies`, the 500 page Flask gives when the settings read raises, the CSRF hook 403 page and the rotated token: `tests/ui/_settings_page_answers.py`, `tests/ui/_refusal_answers.py`.
