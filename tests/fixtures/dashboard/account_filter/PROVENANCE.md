# Provenance: two_accounts_bot_state.json

Class: captured producer output, with one recorded derived edit per record (the account), plus synthetic entries labelled below.

Source: four verbatim records of `tests/fixtures/prod_droplet/bot_state_positions.json`, written by
`tests/fixtures/prod_droplet/capture_from_droplet.py` from the droplet capture of 2026-07-09 (the committed
file records the deployed SHA and capture time). Composer ids (map keys) and symphony names are kept as captured.

Cases, one record each (the dashboard distinguishes active from standby, and the filter distinguishes accounts):
| role (`ids`)    | captured key           | case                                   |
|-----------------|------------------------|----------------------------------------|
| first_standby   | iaSOOUsmnCJHiZvbrWfs   | untriggered symphony, first account    |
| first_active    | lW4ZzWuqR8tEO2DhXbil   | triggered (exited) symphony, first account |
| second_standby  | nOyb55RMGVCKPiYXv7TI   | untriggered symphony, second account   |
| second_active   | qF5ZU7ALjrlhxrGEwsyJ   | triggered (exited) symphony, second account |

Derived edit (the only change to a captured record): `account` is replaced by an invented UUID of the real format
(`accounts.first`, `accounts.second`); no account id from real data is committed. Every other field is as captured.

Synthetic entries, labelled so:
- `accounts.without_symphonies`: an invented UUID that no record carries (the account with no symphonies).
- `accounts.needs_url_encoding`: an invented string holding `+`, `/`, a space, `&` and `=`, to prove query-string encoding.
- `bot_state.date` and `bot_state.last_successful_cycle_at`: top-level metadata keys in the shapes the engine writes
  (`last_successful_cycle_at` is a timezone-aware ISO string), so the freshness stamp is fixed and the metadata-key path of the filter is exercised.
- `ids`: a lookup from role to captured key, for readable test code.

Derived records made inside the tests (named fields changed on one fixture record), for the detail panel title:
- display name replaced by "Copper Ridge Rotation" (a name with no punctuation);
- `name` removed, set to the empty string, or removed while the poll's lowercase `normalized_name` stays;
- display name replaced by "Copper <b>Ridge</b> Rotation" (markup in a name).

Sign-off: the invented account UUIDs, the URL-encoded id and the three detail-title names above are derived records that the PM
signed off, by message to the C3 RED author during round 1 RED (2026-10-22).

The closed-market scenario builds its snapshot from `tests/fixtures/dashboard/frozen_state/frozen_state_with_symphonies.json`
(hand-authored, schema-derived) with its `accounts_map` replaced by the four records above.

Tests derive every expected count and id from this file at run time.
