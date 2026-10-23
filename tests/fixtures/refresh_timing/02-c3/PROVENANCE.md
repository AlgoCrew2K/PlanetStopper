# Provenance: refresh-timing plan 02, cycle C3 (account-basis grace, startup refresh)

One row per record. No derived record exists in this folder, so none awaits a sign-off.

| Record | Source | Case it covers | Changed fields |
|---|---|---|---|
| `bot_state_entry_iaSOOUsmnCJHiZvbrWfs.json` | `tests/fixtures/prod_droplet/bot_state_positions.json`, key `iaSOOUsmnCJHiZvbrWfs` (captured read-only from the production droplet database copy on 2026-07-09, `tests/fixtures/prod_droplet/capture_from_droplet.py:1-30`) | The one real per-symphony `bot_state` entry the route tests seed so the live strip, the frozen snapshot and the server render are built from producer-shaped data. Its `account` value is also the account id the Composer stand-in expects in the total-stats URL. | none (verbatim) |

## Read in place, not copied

| Record | Source | Case it covers |
|---|---|---|
| Composer account total-stats body | `tests/fixtures/composer/v3-audit/total_stats.json` (captured from the Composer producer on 2026-06-04) | The 200 body every successful refresh in the C3 tests serves through a `requests.get` stand-in. Expected strip values are recomputed from it (`todays_percent_change * 100`, `simple_return * 100`), never typed. |

## Test-time stand-ins (not records)

- A 503 reply is a bodyless stand-in that carries a status code only; `_refresh_account_totals` reads nothing else from a non-200 reply, so no captured body is altered.
- A `ReadTimeout`, a `ConnectionError` and a fault inside the cache write are raised by the test; no producer data is involved.
- The frozen close snapshot is a container built by the test around the captured symphony entry (the wrapper keys `trading_day`, `captured_at_et` and `accounts_map` are synthetic and carry no producer claim).
