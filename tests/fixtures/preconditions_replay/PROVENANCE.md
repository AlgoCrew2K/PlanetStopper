# Provenance of the preconditions replay fixtures

## cache_excerpt_2026-11-13.json

Class: captured producer output, read-only, from the droplet's `/opt/planetstopper` on
2026-11-15T17:51:39Z. The source is the autotune's cache file
`cache/synthetic_history_v4_2026-11-13_6c47627672bb88f5bc75964c5586676f.json` written by
`synthetic_history.generate_synthetic_history` (71,093,029 bytes; 12 symphonies; 121, 188 or 250
dates each; 1 to 456 ticks per date). The excerpt keeps the last 60 dates per symphony and the
last 2 ticks per date (the route reads only `ticks[-1]["return"]`), plus the bot state's
`current_holdings` and names for the 12 symphonies. Its own `provenance` block records that the
holdings' md5 equals the source file name's hash; `test_the_seeded_world_is_internally_consistent`
proves the same through the real key derivation.

## goldens/<state>.json

Class: captured route output of the unchanged route. Each file is the exact response body of
`GET /api/guard-alpha-preconditions` at the plan's base commit
`aafe476d62401f041aeae67c563742f884d8b4af` for the state of the same name, built by
`tests/app/_preconditions_replay_world.golden_world(<state>)`.

- Producer: `tests/fixtures/preconditions_replay/capture_goldens.py`. It refuses to run unless
  `git diff --name-only aafe476d -- app.py autotuner.py synthetic_history.py analytics.py
  database.py guard_preconditions.py` is empty, and it was run on a tree where that output was
  empty (the RED commit `455b51ea507305a544a17de5fcfe0ed5c44070a6` and the commit that adds these
  files change tests only).
- Clock: the ET instant is frozen at Sunday 2026-11-15 12:00 through `frozen_datetime_class`
  (`tests/app/_refresh_timing_state_world.py`) into `app.datetime` (the no-longer-held rule) and
  `autotuner.datetime` (the probe's start date). Both the capture and the tests use the same
  instant. `full_plus_60s` is the `full` world's body after the clock moves 60 seconds.
- Shadow rows: synthetic, labelled so. One end-of-day row per session in the real
  `shadow_history` schema, from `random.Random(20261115)`: the first 7 symphonies (sorted ids)
  carry 45 sessions (at or above `N_MIN_OBS`), the other 5 carry 12, all through the most recent
  completed session (2026-11-13).
- Bot-state metadata keys and the `symphony_added` symphony (`Kp4WqZr7TnB2xLm9Hc3D`, a copy of
  the first symphony's real records under an invented id): synthetic, labelled so.
- Comparison: the tests compare bytes after normalizing CRLF to LF in the golden (the repository
  has no `.gitattributes`, so `core.autocrlf` may rewrite a checkout). The body holds no CR.

States, with the stated edit each is derived by:

| Golden | State |
|---|---|
| `full` | the excerpt's 12 symphonies, the excerpt's history as the file at the real key for 2026-11-13 |
| `full_plus_60s` | `full`, clock +60 s |
| `no_file` | `full` without the file |
| `file_lacks_one` | the file lacks the last symphony |
| `empty_ticks` | the last symphony's every date has an empty tick list |
| `zero_variance` | the last symphony's last-tick returns are all 0.0 |
| `file_has_extra` | the bot state lacks the last symphony; the file at that smaller state's key still holds it |
| `no_shadow` | the second symphony has no `shadow_history` rows |
| `load_state_raises` | `database.load_state` raises |
| `load_state_empty` | `database.load_state` returns `{}` |
| `shadow_raises` | the shadow series read raises for the second symphony |
| `metadata_and_stale` | the five metadata keys; the second symphony's rows stop two sessions early (no longer held) |
| `symphony_added` | a 13th symphony with holdings and history, the file at the 13-symphony state's key |
| `corrupt_newest_valid_older` | a truncated file at the 2026-11-13 key, the valid history at the 2026-11-12 key |
| `only_corrupt` | a truncated file at the 2026-11-13 key only |

Never edited by hand and never recaptured from a later build: a change here is a change of the
contract the head is held to.
