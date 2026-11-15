# Approval packet: The preconditions route reads its replay cache file once

## What is being built

Out of hours, when a replay cache file resolves (weekends and after the Friday autotune), the Performance page's stop-justification preconditions panel answers from at most one decode of that file per request, and from none when the file has not changed, with the same JSON as today, so the operator's headline figures and other dashboard requests are no longer held up behind it.
- Each cache file is decoded at most once per request (AC-1).
- A repeat request reuses an unchanged file's derived series and never a changed one's, and a corrupt file is read and logged once per identity (AC-2).
- The route's answer is byte-identical to base for the same state and cache file (AC-3).
- Concurrent requests share one decode (AC-4).
- A measured latency bound holds on a production-sized fixture (AC-5).
- Concurrent `/api/performance`, `/api/header-status` and `/api/candidate-alert` requests are not held up (AC-6).
- Memory stays bounded between and during requests (AC-7).
- The cache-hit-only contract, the probe window, the log lines, auth and the protected files stay as they are (AC-8).

## Acceptance criteria and scenarios

### AC-1: Each cache file is decoded at most once per request
Rule: When `GET /api/guard-alpha-preconditions` resolves its replay samples, the route shall decode each cache file at most once for the whole request, however many held symphonies it reports.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | the fixture's 12 held symphonies with holdings; one valid cache file for those holdings dated inside the backward window; no request since the daemon started | `GET /api/guard-alpha-preconditions` | 200; that file is decoded exactly once; the body holds 12 entries, each with a `replay` and a `shadow` object |
| S-1.2 | N1 | one held symphony; a valid file for its holdings | GET | the file is decoded exactly once; the body holds 1 entry |
| S-1.3 | N1 | the bot state holds only the five metadata keys and no symphony | GET | 200 `{"symphonies": {}}`; no cache file is decoded |
| S-1.4 | N1 | held symphonies, none carrying `current_holdings` (no cache key resolves) | GET | no cache file is decoded; every entry's `replay` is the degraded row |
| S-1.5 | N3 | held symphonies with holdings; no file for those holdings anywhere in the window | GET | no cache file is decoded; every `replay` is the degraded row; every `shadow` is as at base |
| S-1.6 | N6 | the newest file in the window is corrupt (exists, invalid JSON); an older valid file is in the window | GET | the corrupt file is read once and the older file decoded once for the whole request; every `replay` comes from the older file |
| S-1.7 | N6 | the only file in the window is corrupt | GET | the corrupt file is read once for the whole request; every `replay` is the degraded row |
| S-1.8 | N7 | the fixture's 12 symphonies, one of them no longer held (its `shadow_history` rows stop before the second most recent completed session) | GET | the file is decoded exactly once; the no-longer-held symphony has no entry |
| S-1.9 | N5 | as S-1.1, with the page left open | three 60 s polls of the same GET | each poll decodes F at most once (none, once S-1.1 has decoded it unchanged) |
| S-1.10 | N5 | as S-1.1 | two GETs start together | no request decodes F more than once (once in total, AC-4) |

Negative coverage: N1 S-1.2, S-1.3, S-1.4 | N2 n/a: the route takes no parameters and refuses nothing | N3 S-1.5 | N4 covered by AC-8 (S-8.5) | N5 S-1.9, S-1.10 | N6 S-1.6, S-1.7 | N7 S-1.8 | N8 covered by AC-2 (S-2.12)

### AC-2: A repeat request reuses an unchanged file and never a changed one
Rule: When a request's backward probe passes only missing candidates or remembered failures and resolves the held identity, the route shall answer without reading any cache file; if the file it reaches differs in path, size or modification time from both the held identity and every remembered failure, or no file resolves, then the route shall answer from the newly reached file or the degraded row, never from series derived from the held identity.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | a request has decoded file F; F is unchanged; an engine cycle has since rewritten the bot state's per-symphony return and value fields without changing any holdings; `shadow_history` is unchanged | a second GET | no decode; the body is byte-identical to the first |
| S-2.2 | N5 | as S-2.1 | three more GETs 60 s apart (the page poll) | no decode across the three; each body is byte-identical to the first |
| S-2.3 | N1 | F is inside the window; the ET date rolls to the next trading day and F is still inside it | GET | no decode; the replay rows are unchanged |
| S-2.4 | N1 | F is exactly `AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS` trading days back | GET | no decode; the replay rows come from F |
| S-2.5 | N1 | the date rolls so F is one trading day beyond the window; no newer file | GET | F is not decoded or used; every `replay` is the degraded row; one INFO miss summary is logged in the existing wording (base logs one per held symphony) |
| S-2.6 | N1 | the autotune has written F2 for the same holdings at a newer date inside the window | GET | F2 is decoded exactly once; the replay rows come from F2 |
| S-2.7 | N1 | F is rewritten in place with a different size | GET | F is decoded once again; the replay rows come from the new content |
| S-2.8 | N1 | F is rewritten in place with the same size and a different modification time | GET | F is decoded once again; the replay rows come from the new content |
| S-2.9 | N1 | a held symphony's holdings change, so the current holdings resolve a different key and no file exists for it | GET | F is not decoded or used; every `replay` is the degraded row |
| S-2.10 | N3 | F is deleted and no other file is in the window | GET | no decode; every `replay` is the degraded row; one INFO miss summary; `synthetic_history.fetch_bars` and `generate_synthetic_history` are not called |
| S-2.11 | N6 | F is replaced in place by corrupt content (its size or modification time changed); an older valid file G is in the window | GET | one corrupt-file WARNING naming F; G is decoded once; the replay rows come from G |
| S-2.12 | N8 | the autotune is part-way through writing F2 (newer date, same holdings) | GET | the body equals base's for a corrupt F2 (replay rows from F or the degraded row) and one WARNING names F2; the next GET after the write completes decodes F2 once and answers from it (the completed file's size and modification time differ from the remembered partial one) |
| S-2.13 | N7 | as S-2.1, and one symphony has become no longer held between the two requests | a second GET | no decode; that symphony's entry is gone; every other entry is byte-identical to the first body's |
| S-2.14 | N1 | the daemon restarted after S-2.1 | GET | F is decoded exactly once (nothing derived from F survives a restart, and nothing derived is written to disk) |
| S-2.15 | N5 | as S-2.1 | two GETs start together | no decode; both bodies are byte-identical to the first |
| S-2.16 | N1 | F was decoded; the holdings then changed so F2 resolved and was decoded; the holdings then changed back, so F resolves again, unchanged | GET | F is decoded once again (the held identity is F2's, so the request is cold; warmth is not claimed across a return to an earlier file); the replay rows come from F |
| S-2.17 | N1 | F2 (newer date, same holdings) was decoded after F; F2 is then deleted, so F resolves again | GET | F is decoded once again; the replay rows come from F |
| S-2.18 | N6 | a request read corrupt F (newer) and logged its WARNING, then decoded the older valid G; F and G are unchanged | the next GET, and three 60 s polls after it | no cache file is read; no WARNING is logged; every body is byte-identical to the first request's (replay rows from G) |
| S-2.19 | N6 | as S-2.18, then F is rewritten with valid content (its size or modification time changes) | GET | F is decoded once; the replay rows come from F |
| S-2.20 | N6 | the only file in the window, F, is corrupt; a request has read it and logged its WARNING; F is unchanged | the next GET | F is not read; no WARNING; every `replay` is the degraded row, as at base |

Negative coverage: N1 S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.14, S-2.16, S-2.17 | N2 n/a: the route takes no parameters and refuses nothing | N3 S-2.10 | N4 covered by AC-8 (S-8.5) | N5 S-2.2, S-2.15 | N6 S-2.11, S-2.18, S-2.19, S-2.20 | N7 S-2.13 | N8 S-2.12

### AC-3: The answer is byte-identical to base
Rule: When `GET /api/guard-alpha-preconditions` is served for a given bot state, `shadow_history` and cache directory, the route shall return the status and JSON body bytes that base returns for them, on a cold request and on a warm one.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | the fixture's 12 held symphonies; the excerpt's cache file holding all 12; `shadow_history` rows for each | a cold GET, then a warm GET | both bodies are byte-equal to the base body captured for the same state |
| S-3.2 | N3 | no file in the window | GET | the body is byte-equal to base: every `replay` is the degraded row |
| S-3.3 | N3 | the file lacks one held symphony | GET | that entry's `replay` is the degraded row; the body is byte-equal to base |
| S-3.4 | N3 | the file holds a symphony whose every date has an empty tick list | GET | that entry's `replay` is the degraded row; byte-equal to base |
| S-3.5 | N1 | a symphony's replay returns are all equal and exactly representable (for example all 0.0), so the variance sum is exactly zero (`guard_preconditions.py:74-86`) | GET | that entry's `rho` and `sharpe_daily` are null and its `rho_ci` is the number 1.96 over the square root of `n_obs` (`guard_preconditions.py:98`), as at base; byte-equal to base |
| S-3.6 | N1 | the file holds a symphony that is not in the bot state | GET | no entry for it; byte-equal to base |
| S-3.7 | N3 | a held symphony has no `shadow_history` rows | GET | its `shadow` is the degraded row and its `replay` is real; byte-equal to base |
| S-3.8 | N6 | `database.load_state` raises | GET | 200 `{"symphonies": {}}`; no file is decoded; byte-equal to base |
| S-3.9 | N1 | `database.load_state` returns `{}` | GET | 200 `{"symphonies": {}}`; byte-equal to base |
| S-3.10 | N6 | the shadow series read raises for one symphony | GET | that entry's `shadow` is the degraded row; every other entry is real; byte-equal to base |
| S-3.11 | N7 | the five metadata keys and a no-longer-held symphony are in the bot state | GET | neither appears; byte-equal to base |
| S-3.12 | N7 | the market is closed and nothing is written between two GETs 60 s apart | two GETs | the two bodies are byte-identical to each other and to base |
| S-3.13 | N1 | a symphony with holdings is added to the bot state (so the key changes) and a file for the new holdings is in the window | GET | the entries follow the bot state and the new symphony's `replay` comes from that file; byte-equal to base |
| S-3.14 | N5 | the S-3.1 state, no earlier decode | two GETs start together | both bodies are byte-equal to the base body for that state |

Negative coverage: N1 S-3.5, S-3.6, S-3.9, S-3.13 | N2 n/a: the route takes no parameters and refuses nothing | N3 S-3.2, S-3.3, S-3.4, S-3.7 | N4 covered by AC-8 (S-8.5) | N5 S-3.14 | N6 S-3.8, S-3.10 | N7 S-3.11, S-3.12 | N8 covered by AC-2 (S-2.12)

### AC-4: Concurrent requests share one decode
Rule: While one request is decoding a cache file, a second request that reaches the same file identity shall wait for that decode and answer from its result instead of decoding the file again; if that decode fails, then the waiting request shall not read the file again and shall get base's answer for that file unreadable: on an error `load_cached_history` catches (`OSError`, `JSONDecodeError`, `ValueError`, `UnicodeDecodeError`, `synthetic_history.py:274`) the probe moves on to the next older candidate, with the degraded replay row when none is left; on any other exception the replay row is the degraded row, as the route gives a raising replay seam (`app.py:6610-6618`).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | no earlier decode of F | two GETs start together | F is decoded exactly once in total; both bodies are byte-equal to the single-request body of S-3.1 |
| S-4.2 | N5 | as S-4.1 | three GETs start while the first is decoding | F is decoded once in total; all bodies are byte-equal |
| S-4.3 | N6 | F is corrupt; an older valid file G is in the window | two GETs start together | both 200; F is read once in total (the waiting request does not re-read it) and one WARNING names it; both probes move on to G, which is decoded once in total; both bodies equal base's for that state (replay rows from G); neither request waits after the first finishes; a later GET does not read F while its identity is unchanged (S-2.18) |
| S-4.4 | N6 | request A's decode of valid F raises once with an error other than a decode error (a simulated transient fault); no older file | A and B start together, B waiting on A's decode | both 200; neither hangs; B does not read F; every `replay` in both bodies is the degraded row, the route's answer for a raising replay seam (`app.py:6610-6618`); the failure is not remembered (it is not an error `load_cached_history` catches), so the next GET decodes F once and answers from it. At base the same fault reaches only the symphony call that hit it; the shared decode applies that per-symphony rule to every symphony of the requests it serves (Known-Unconfirmed item 5) |
| S-4.5 | N1 | request A is decoding F; F is then rewritten with a different size | request B starts | B does not answer from A's result for the old F; B's replay rows come from the new content |
| S-4.6 | N7 | a cold preconditions GET is decoding | a concurrent `GET /api/performance?scope=aggregate&days=60d` completes | its body equals its body when run alone |
| S-4.7 | N3 | no file in the window | two GETs start together | no decode; both bodies are the degraded-replay body, byte-equal to base; no fetch is made |

Negative coverage: N1 S-4.5 | N2 n/a: the route takes no parameters and refuses nothing | N3 S-4.7 | N4 covered by AC-8 (S-8.5) | N5 S-4.1, S-4.2 | N6 S-4.3, S-4.4 | N7 S-4.6 | N8 covered by AC-2 (S-2.12)

### AC-5: A measured latency bound holds on a production-sized fixture
Rule: When the route runs on a production-sized fixture (at least 11 held symphonies and a cache file of at least 70 MB), a warm request shall decode nothing and complete in under 0.5 s, and a cold request shall decode at most one file and complete within the time of one decode of that fixture file, measured in the same run, plus 1.0 s.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the fixture; one cold GET already served | a warm GET, timed as the median of 5 | no decode; under 0.5 s |
| S-5.2 | N1 | the fixture; no earlier decode | a cold GET, timed | exactly one decode; within one decode of the fixture file (timed alone in the same run) plus 1.0 s |
| S-5.3 | N1 | the fixture with a 147 MB cache file (the largest recent droplet file, G-9) | a warm GET, timed | no decode; under 0.5 s |
| S-5.4 | N3 | the fixture with no file in the window | a GET, timed | no decode; under 0.5 s |
| S-5.5 | N5 | the fixture, warm | 5 warm GETs back to back | no decode; each under 0.5 s |
| S-5.6 | N6 | the fixture with a corrupt newest file F and a valid older file G, no earlier read | a GET, timed | F read once and G decoded once; within one read of F plus one decode of G (each timed alone in the same run) plus 1.0 s |
| S-5.7 | N6 | as S-5.6 after its GET: G held, F's failure remembered, both unchanged (for example a failed write left F truncated, `synthetic_history.py:841-851`) | a GET, timed | no file read; under 0.5 s |

Negative coverage: N1 S-5.2, S-5.3 | N2 n/a: the route takes no parameters and refuses nothing | N3 S-5.4 | N4 n/a: a 401 is answered by the global hook before the route runs, unchanged (S-8.5) | N5 S-5.5 | N6 S-5.6, S-5.7 | N7 n/a: a timing criterion; what must not change is AC-3 and AC-8 | N8 n/a: no edit is held open; a write in progress is S-2.12

### AC-6: Other requests are not held up
Rule: While a warm `GET /api/guard-alpha-preconditions` is in flight, a concurrent `GET /api/performance`, `/api/header-status` or `/api/candidate-alert` shall complete no more than 0.5 s later than the same request run alone.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the AC-5 fixture, warm, served by the real threaded server | `GET /api/performance?scope=aggregate&days=60d` starts together with a warm preconditions GET (median of 5) | it completes within its alone time, measured in the same run, plus 0.5 s |
| S-6.2 | N5 | as S-6.1 | `GET /api/header-status` starts together with a warm preconditions GET | it completes within its alone time plus 0.5 s |
| S-6.3 | N5 | as S-6.1 | `GET /api/candidate-alert` starts together with a warm preconditions GET | it completes within its alone time plus 0.5 s |
| S-6.4 | N1 | the fixture, cold | `GET /api/performance?scope=aggregate&days=60d` starts together with the cold preconditions GET | it completes within its alone time plus the measured time of one decode of the fixture file plus 0.5 s |
| S-6.5 | B | the fixture, warm; `/performance` opened in a browser | the page loads | the guard effect, Sharpe, Max DD and Sortino headline figures and the chart show values, not "--", within the `/api/performance` alone time plus 0.5 s after the symphony list loads; the preconditions panel shows the same rows and verdicts as at base |
| S-6.6 | N7 | `/performance` open; nothing written between polls | two 60 s polls run | no text in the headline or the preconditions panel changes |
| S-6.7 | N3 | the fixture with no file in the window | `/api/performance` starts together with a preconditions GET | it completes within its alone time plus 0.5 s |
| S-6.8 | N6 | the fixture with a corrupt newest file and a valid older file, cold | `/api/performance` starts together with the preconditions GET | it completes within its alone time plus the measured time of one read of the corrupt file and one decode of the older file plus 0.5 s |
| S-6.9 | N6 | the state of S-5.7 (G held, F's failure remembered) | `/api/performance` starts together with the preconditions GET | it completes within its alone time plus 0.5 s |
| S-6.10 | N1 | the fixture, cold | `GET /api/header-status` starts together with the cold preconditions GET | it completes within its alone time plus the measured time of one decode of the fixture file plus 0.5 s |
| S-6.11 | N1 | the fixture, cold | `GET /api/candidate-alert` starts together with the cold preconditions GET | it completes within its alone time plus the measured time of one decode of the fixture file plus 0.5 s |

Negative coverage: N1 S-6.4, S-6.10, S-6.11 | N2 n/a: the routes take no new input and refuse nothing new | N3 S-6.7 | N4 n/a: auth is the unchanged global hook (S-8.5) | N5 S-6.2, S-6.3 | N6 S-6.8, S-6.9 | N7 S-6.6 | N8 n/a: no edit is held open on these pages

### AC-7: Memory stays bounded
Rule: While no preconditions request is in flight, the daemon shall hold none of a cache file's decoded content for this route, only per-symphony daily return lists for the held identity and the identities (path, size, modification time) of remembered failures.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | a production-shaped state and file | a cold GET completes and garbage is collected | the traced memory retained against before the request is under 5% of the traced peak during the request |
| S-7.2 | N1 | three different files decoded by successive requests (F replaced twice) | the third request completes and garbage is collected | the retained memory is still under 5% of one request's traced peak |
| S-7.3 | N5 | no earlier decode | two cold GETs run together | the traced peak during the pair is at most 1.25 times the traced peak of one cold GET alone |
| S-7.4 | N3 | no file in the window | a GET completes and garbage is collected | the retained memory is under the S-7.1 bound |
| S-7.5 | N6 | the decode fails (corrupt file) | a GET completes and garbage is collected | nothing from the failed decode is retained beyond the file's identity (the S-7.1 bound holds) |
| S-7.6 | N1 | warm | 20 warm GETs complete and garbage is collected | the retained memory does not grow beyond the S-7.1 bound across the 20 |
| S-7.7 | N7 | any of the states above | a GET completes | the cache directory and the repository hold no new file (nothing derived is written to disk) |

Negative coverage: N1 S-7.2, S-7.6 | N2 n/a: the route takes no parameters and refuses nothing | N3 S-7.4 | N4 n/a: a refused request never reaches the route | N5 S-7.3 | N6 S-7.5 | N7 S-7.7 | N8 n/a: no edit is held open

### AC-8: Contracts and protected surfaces stay unchanged
Rule: When the route resolves its replay samples, it shall read only the existing cache files, probe the same `AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS` window, log the existing INFO miss summary wording at most once per request and the existing corrupt-file WARNING wording once per corrupt file identity, keep its auth and read-only behaviour, and leave the protected files unchanged.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | no file in the window; `synthetic_history.fetch_bars` and `generate_synthetic_history` instrumented | GET | neither is called; no network request leaves the process; every `replay` is the degraded row |
| S-8.2 | N3 | all 11 window candidates are missing | GET | exactly one INFO record per request with the existing "cache miss for 11 candidate date(s)" wording (`synthetic_history.py:566-574`); no WARNING |
| S-8.3 | N6 | a corrupt file in the window, not read before | GET, then two 60 s polls with the file unchanged | the first GET logs exactly one WARNING, byte-identical in wording to base's "synthetic_history: corrupt or unreadable cache file %s: %s" line naming that file (`synthetic_history.py:276-280`), and the walk continues to older dates as at base; the polls log no repeat WARNING for that unchanged file (narrowed: base logs one per held symphony on every poll) |
| S-8.4 | N1 | the current date's candidate is a valid file | GET | no log record from the probe |
| S-8.5 | N4 | auth on, no session, an XHR request | GET | 401 JSON, as at base |
| S-8.6 | N2 | any state | `POST /api/guard-alpha-preconditions` | 405, as at base (the route is GET only) |
| S-8.7 | N7 | the slice head against aafe476d | diff | `alpha_bot_execution.py`, `math_engine.py`, `guard_preconditions.py`, `static/performance.js`, `templates/performance.html` and `migrations/` are unchanged; `generate_synthetic_history`, `database.load_state` and `analytics.get_shadow_current_return_daily_series` are unchanged |
| S-8.8 | N7 | an existing valid file for the autotune's state | `generate_synthetic_history` is called | it returns the decoded dict and makes no fetch, as at base |
| S-8.9 | N7 | the route's source | read | no SQL write and no `LIVE_EXECUTION` reference (the existing read-only tests stay green) |
| S-8.10 | N5 | all window candidates missing | two GETs run together | at most one INFO miss summary per request |
| S-8.11 | N3 | a request has decoded F; F is then deleted and no other file is in the window; fetches instrumented as in S-8.1 | the next 60 s poll GET | `synthetic_history.fetch_bars` and `generate_synthetic_history` are not called; every `replay` is the degraded row |
| S-8.12 | N5 | no file in the window; fetches instrumented as in S-8.1 | two GETs start together | neither function is called by either request |
| S-8.13 | N1 | a request has decoded F; the date then rolls so every window candidate is missing | the next 60 s poll GET | exactly one INFO record with the existing miss wording; no WARNING |

Negative coverage: N1 S-8.4, S-8.13 | N2 S-8.6 | N3 S-8.2, S-8.11 | N4 S-8.5 | N5 S-8.10, S-8.12 | N6 S-8.3 | N7 S-8.7, S-8.8, S-8.9 | N8 covered by AC-2 (S-2.12)

## Rule-by-entry-point matrix

| Rule | Page load (first GET after the symphony list) | 60 s poll (repeat GET) | Concurrent GETs (two tabs, or a load overlapping a poll) | Autotune run (`generate_synthetic_history` on the same file) |
|---|---|---|---|---|
| AC-1 at most one decode per request | S-1.1 | S-1.9 | S-1.10 | n/a: the autotune decodes its own file once per run, unchanged (AC-8) |
| AC-2 reuse an unchanged file, never a changed one | S-2.14 | S-2.1, S-2.6 | S-2.15 | n/a: the autotune reads its file per run, unchanged (AC-8) |
| AC-3 byte-identical answer | S-3.1 | S-3.12 | S-3.14 | n/a: the autotune returns no route answer |
| AC-4 concurrent requests share one decode | n/a: a lone page load has no concurrent request | n/a: a lone poll has no concurrent request | S-4.1 | n/a: an autotune is not a route request: the scheduled one runs in the engine subprocess and the `POST /api/force_eod` one on a daemon thread (`app.py:8570-8622`), and neither waits on or shares a route decode; its write in progress is S-2.12 and its memory is Known-Unconfirmed item 12 |
| AC-8 cache-hit-only | S-8.1 | S-8.11 | S-8.12 | n/a: the autotune fetches on a miss by design (`synthetic_history.py:664-712`; NB-4), unchanged (S-8.8) |
| AC-8 log wording (INFO once per request, WARNING once per corrupt identity) | S-8.2 | S-8.13 | S-8.10 | n/a: its own lines are unchanged (S-8.8) |

## Not Building

Non-goals:
- NB-1: No change to the preconditions statistics, verdicts, thresholds (`N_MIN_OBS`) or the shadow sample (R-27; approved [PM-ASSUMED]).
- NB-2: No change to the 60 s poll in `static/performance.js` (`static/performance.js:1093-1097`) (R-28; approved [PM-ASSUMED]).
- NB-3: No speed-up of `/api/performance` itself (R-29; approved [PM-ASSUMED]).
- NB-4: No change to the cache file format or its writer `generate_synthetic_history`, including its fetch on a miss (R-30; approved [PM-ASSUMED]).
- NB-5: No change to the autotuner's walk-forward replay (R-31; approved [PM-ASSUMED]).
- NB-6: No wire, schema, migration or config change; no new environment key (R-24; approved [PM-ASSUMED]).
- NB-7: No workflow file is created for `/performance` (R-23; approved [PM-ASSUMED]).
- NB-8: The exhaustive UI/UX review and the droplet deploy are the PM's deploy gate, not criteria of this plan (narrows R-1; approved [PM-ASSUMED]).

Behavior that stays exactly as it is:
- NB-9: The route's JSON for the same state and cache file, its cache-hit-only read, the backward probe up to `AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS`, the INFO miss summary and corrupt-file WARNING wording, and the degraded-row copy (tested by AC-3 and AC-8; approved [PM-ASSUMED]).
- NB-10: The structural symphony filter (`app.py:6499-6515`) and the shared no-longer-held rule (`app.py:6604`) (tested by S-1.3, S-1.8, S-3.11; approved [PM-ASSUMED]).
- NB-11: The Performance panel's rendering, copy, display names and cold-replay note (`static/performance.js:874-924`) (approved [PM-ASSUMED]).

Deferred:
- NB-12: GIL contention from any other slow route (R-33; approved [PM-ASSUMED]). The source names "the NB-14 backlog"; no entry at the ref names it (Known-Unconfirmed item 9), so the follow-up line is recorded at ship (Q-8).
- NB-17: The replay sample is unavailable on weekdays because the cache key hashes the drifting `allocation` floats (Trigger fact 10); the key derivation and that weekday unavailability are not changed here, a pre-existing gap deferred to the NB-14 backlog alongside NB-12 (Q-8) (excludes R-35, which rested on the corrected market-hours clause; approved [PM-ASSUMED]).

Protected surfaces this consumes but does not change:
- NB-13: `alpha_bot_execution.py` and `math_engine.py`, the 1-minute engine (R-13; approved [PM-ASSUMED]; S-8.7).
- NB-14: The synthetic_history cache files and `generate_synthetic_history`, `database.load_state`, `guard_preconditions.py`, `analytics.get_shadow_current_return_daily_series`, `static/performance.js` and `templates/performance.html` (R-34; approved [PM-ASSUMED]; S-8.7).
- NB-15: Dashboard sessions: no logout generation bump, `SECRET_KEY` stable, migrations 040/041 and the session lifetime untouched, so no deploy of this change forces a re-login (R-2; approved [PM-ASSUMED]).
- NB-16: No other repository changes and nothing must land first (R-25; approved [PM-ASSUMED]).

## Narrowed mappings

R-1: The droplet is updated only after exhaustive UI/UX review and proof of no timing or refresh issues: closed-market figures never move and the morning refresh starts; maps to AC-3, NB-8; left out: the review and the deploy are the PM's gate (NB-8); this plan asserts only that its route's closed-market answer does not move (S-3.12) and that it changes no poll or refresh path (NB-2)
R-8: Other dashboard requests (header-status, candidate-alert) are not held up; maps to AC-6; left out: within 0.5 s of their alone time during a warm preconditions request; during a cold one (after each restart, a new autotune file, a holdings change, or the first read of a corrupt file) they may be delayed by the decode time that request spends (S-6.4, S-6.8, S-6.10, S-6.11)
R-9: A measured latency bound on a production-sized fixture (at least 11 symphonies, a cache file of at least 70 MB): a warm request under 0.5 s with zero parses, a cold request with at most one parse and a wall-clock time stated as a ratio against today's; maps to AC-5; left out: decode counts are the primary assertion (cold at most 1, warm 0); the cold wall-clock bound is one decode of the fixture file plus 1.0 s, looser than the source's "1/5 of the current per-symphony cost" (one fifth of one decode), which no cold path that decodes the whole file once at today's decode speed can meet (Q-3)
R-12: The cache-miss, backward-probe and log behaviour of get_cached_synthetic_history_only is unchanged; maps to AC-8; left out: the probe window, the miss handling and both log wordings are unchanged; the route logs one reader pass's lines per request instead of one per held symphony, and repeat WARNING lines for the same unchanged corrupt file are not re-logged (PM ruling, S-8.3)
R-16: A corrupt or unreadable file gives the same WARNING log and degraded row as today; maps to AC-1, AC-2, AC-8; left out: the replay rows are as at base and the first WARNING's wording is byte-identical to base; repeat WARNING lines for the same unchanged corrupt file are not re-logged, where base logs one per held symphony on every poll (PM ruling, S-2.18, S-8.3)
R-22: On /performance the headline figures (guard effect, Sharpe, Max DD, Sortino) and chart fill as fast as /api/performance alone allows (~1-2 s on the droplet), the panel shows the same rows and verdicts as today, and each 60 s refresh changes nothing visible when nothing changed; maps to AC-3, AC-6; left out: the headline bound holds while the preconditions request is warm (S-6.5); on the first page load after a restart or a file change the headline may also wait for one decode (S-6.4)
R-32: Stays exactly: the backward probe up to AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS, the cache-miss INFO summary, the corrupt-file WARNING and the degraded-row copy; maps to AC-3, AC-8; left out: repeat corrupt-file WARNING lines for the same unchanged file are not re-logged (PM ruling, S-8.3); the probe window, the INFO summary, the first WARNING's wording and the degraded-row copy are unchanged
R-35: The route no longer competes with the daemon's minute scheduler thread in the same process during market hours; maps to NB-17; left out: nothing is built for it; the PM's ruling corrects the clause (Trigger facts 10 and 11, D-1): no file is decoded during market hours, so no criterion rests on it, and the weekday unavailability it reveals is deferred (NB-17)
R-36: The Performance headline no longer shows '--' for ~20 s while the preconditions request runs; maps to AC-6; left out: during a warm preconditions request the '--' lasts no longer than `/api/performance` alone plus 0.5 s (S-6.5); during a cold one, up to one decode longer (S-6.4)

## User tasks

Workflow (/performance): no workflow file: `.claude/workflows/` is absent at the ref (G-16)

### T-1: Read the Performance page while it refreshes
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/performance` | none | the guard effect, Sharpe, Max DD and Sortino headline figures and the chart show values within the time `/api/performance` alone takes plus 0.5 s | S-6.5 |
| 2 | Read the Stop-justification preconditions panel | none | the same rows and verdicts as at base for the same state and cache file | S-3.1, S-6.5 |
| 3 | Leave the page open across two 60 s refreshes with nothing changed | none | no text in the headline or the panel changes | S-6.6, S-2.2 |

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | May two concurrent requests both parse the file? (G-25) | AC-4 | No. Concurrent requests for the same file share one decode; a second request waits for the first instead of parsing again (G-25, G-26) | default approved [PM-ASSUMED] |
| Q-2 | What identifies an unchanged cache file? (G-27) | AC-2 | The resolved file path plus its size and modification time, re-checked on every request with no time-based expiry (G-27) | default approved [PM-ASSUMED] |
| Q-3 | What is the latency bound? (G-28 to G-30) | AC-5, AC-6 | Decode counts are the primary assertion: cold at most 1 decode per request, warm 0. Wall-clock bounds are secondary and generous enough for CI, on a fixture of at least 11 symphonies and a file of at least 70 MB: a warm request under 0.5 s; a cold request within one decode of the fixture file plus 1.0 s; a concurrent `/api/performance`, `/api/header-status` or `/api/candidate-alert` during a warm request within 0.5 s of its alone time. The source's "1/5 of the current per-symphony cost" means one fifth of one decode (about 0.4 s on the droplet), which no cold path that decodes the whole file once at today's decode speed can meet; the default bound is looser and R-9 is marked narrowed (PM ruling). Choosing the literal bound would require a faster decode or no whole-file decode, and would change the approach | default approved [PM-ASSUMED] |
| Q-4 | What may be kept in memory? (G-31) | AC-7 | Between requests only the derived per-symphony daily return lists for one file identity, plus the identities (no content) of remembered corrupt files, never the parsed history (G-31) | default approved [PM-ASSUMED]; AC-7 is measured on a production-shaped 6.3 MB generated file, within "production-shaped" (approach raised item b) |
| Q-5 | What does "unchanged state" mean for the no-re-parse rule (G-7)? | AC-2 | The state matters only through which file the backward probe resolves (holdings and date); a write that changes returns or values without changing holdings still reuses the held identity. During market hours the holdings' allocations drift, so no file resolves and nothing is decoded, at base and after alike (Trigger fact 10) | default approved [PM-ASSUMED] |
| Q-6 | How many probe log lines does a request emit, and does a corrupt candidate get read again? | AC-1, AC-2, AC-5, AC-6, AC-8 | At most one INFO miss summary per request, in the existing wording, instead of one per held symphony. A corrupt or unreadable file is not re-decoded on every poll: its failure is remembered under its file identity until that identity changes, and its WARNING is logged once, on the first failed read, byte-identical in wording to base's; repeat WARNING lines for the same unchanged file stop, where base logs one per held symphony on every poll, so R-12, R-16 and R-32 are narrowed (PM ruling). A transient error that base does not classify as corrupt or unreadable is not remembered (S-4.4) | default approved [PM-ASSUMED]; the S-4.4 narrowing is accepted: a shared decode's non-decode fault degrades every replay row of the requests it serves and is not remembered, so the next request retries (Known-Unconfirmed item 5) |
| Q-7 | Are the wall-clock bounds asserted in CI? | AC-5, AC-6 | Yes, secondary to the decode counts: they run in the default suite and CI with bounds generous enough for a shared runner (PM ruling), with the measured numbers recorded; the PM's live checks add the droplet numbers | default approved [PM-ASSUMED]; S-6.5 and S-6.6 are carried by a browser-marked test the PM runs at the merge gate plus the PM's live check, with no capturer (approach raised item a) |
| Q-8 | Where are the deferred items NB-12 (GIL contention from other routes) and NB-17 (weekday replay unavailability) tracked? | plan | The slice's doc-gen adds one `feature-plans/BACKLOG.md` line for each at ship, since no NB-14 entry at the ref names either | default approved [PM-ASSUMED]: doc-gen adds the `feature-plans/BACKLOG.md` lines for NB-12 (GIL contention from other routes) and NB-17 (weekday replay-sample unavailability, the cache key hashes drifting allocations) |
