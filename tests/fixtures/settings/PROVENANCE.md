# tests/fixtures/settings: provenance

Tests for the Settings page and the Settings modal read the captured records in
`tests/fixtures/prod_droplet/bot_state_positions.json` and
`tests/fixtures/app/settings_env_snapshot.json` directly; both are captures that
carry their own provenance (`tests/fixtures/prod_droplet/capture_from_droplet.py`
and the commit that added the snapshot). This directory holds only the derived
records below, which change one field of one real record.

## derived_name_records.json

Three derived records (one real record with named fields changed). Each is the captured record
`iaSOOUsmnCJHiZvbrWfs` of `tests/fixtures/prod_droplet/bot_state_positions.json`
(last changed at 7bb3a6d979e6dbf842120f12a2f93f60bb3f8432, name
"(INVEST) LQD + EYEG 5 ways Full Market") with every field verbatim except `name`.
The record is placed under its own Composer key (`source_key`) by the test that
uses it; the key is not changed.

| record | source | changed field | new value | reason | scenario served |
|---|---|---|---|---|---|
| `hash_sign_name` | `bot_state_positions.json` key `iaSOOUsmnCJHiZvbrWfs` | `name` | a name containing a hash sign | No captured name carries a hash sign, and the sign is the character that starts an id selector, so the Settings page's Reset binding must work for a key that contains it | Settings: Reset to globals for a key with a hash sign |
| `non_ascii_name` | `bot_state_positions.json` key `iaSOOUsmnCJHiZvbrWfs` | `name` | a name containing accented Latin letters and CJK characters | No captured name carries a non-ASCII letter; `normalize_name` lower-cases the name into the strategy key, and the page must bind, count and reset it correctly | Settings: Reset to globals for a key with non-ASCII letters |
| `markup_name` | `bot_state_positions.json` key `iaSOOUsmnCJHiZvbrWfs` | `name` | a name containing an HTML bold tag pair and an ampersand | No captured name carries markup characters; every surface that prints a symphony's display name must show them literally and never interpret them | Display-name escaping sweep: the settings modal, both Performance panels, the Frontrunner card, the autotune card, the observations row and the Settings detail header |

Sign-off: the data-truth approach document
(`feature-plans/ux-overhaul/approach/01-data-truth.md`, Per-Cycle Test Plan, Fixtures
paragraph) records the PM's approval of changed-name records for the hash-sign and
non-ASCII reset cases and tells the test-writer to record it here. The PM's own
confirmation of these two entries is requested in the RED hand-off.

The data-truth approach document (`feature-plans/ux-overhaul/approach/01-data-truth.md`,
cycle C8, scenario S-5.10) records the PM's approval of the `markup_name` record; the PM's
own confirmation is requested in the RED hand-off.
