# Provenance: refresh-timing plan 05, cycle C2 (Composer call latency lines)

No record file is copied into this folder. The 200 body is read in place; the 503 and the timeout are derived from it by changing only the status or the raised exception, applied by the test at run time. One row per AC-3 case.

## Read in place, not copied

| Record | Source | Case it covers |
|---|---|---|
| Composer account total-stats body | `tests/fixtures/composer/v3-audit/total_stats.json` (captured from the Composer producer on 2026-06-04) | The 200 answer behind scenario S-3.1, S-3.4 and S-3.6. The log line asserted reads only the status code and the test's own duration; no body field reaches an assertion. |
| Per-symphony `bot_state` entry | `tests/fixtures/refresh_timing/02-c3/bot_state_entry_iaSOOUsmnCJHiZvbrWfs.json` (verbatim, see its folder's `PROVENANCE.md`) | Supplies the real account id (a UUID) the total-stats URL carries, so the leak checks of S-3.4 look for an identifier in its real format. |

## Derived cases (test-time stand-ins applied to the record above)

| Case | Source record | Changed field | Reason | Scenario | PM sign-off |
|---|---|---|---|---|---|
| Composer answers 503 | the captured 200 answer | status code `200` replaced by `503`; no body (`_refresh_account_totals` reads nothing else from a non-200 answer) | the call's failure path logs `status=503` and its duration | S-3.3, S-3.4 | PM-5 of `feature-plans/refresh-timing/05-observability-transport.md` (PM-ASSUMED) |
| Composer times out | the captured 200 answer | no answer; `requests.exceptions.ReadTimeout` raised after the test advances the timing clock by 30 s (the code's `_ACCOUNT_TOTALS_HTTP_TIMEOUT_S`) | the call's timeout path logs `error=ReadTimeout` with about 30000 ms | S-3.2, S-3.4 | PM-5 of `feature-plans/refresh-timing/05-observability-transport.md` (PM-ASSUMED) |

## Test-time stand-ins (not records)

- A `ConnectionError` and a `ValueError` carrying the request URL in their message are raised by the test; no producer data is involved. The URL is the one the code built, so the message holds the account id the S-3.7 checks look for.
- The key id and secret of S-3.4 are typed in their real formats (a UUID and a 40-character token); they are credentials the test sets in the environment, not producer output.
