Spec audit (re-check, the one allowed): feature-plans/refresh-timing/ (00-overview.md, 01 to 05) (tier: epic, with five feature plans)
Verified against: PlanetStopper @ 37c8e983b64c `## main...origin/main` (product files clean). HEAD equals the brief's ref.
Checklist: .claude/agents/_refs/spec-review-checklist.md (project copy)
Scripts on the revised files: `plan-trace check --stage gate-1` clean and `plan-trace self-contained` 0 findings on all six; the placeholder grep is empty.
Result: 1 CONFIRMED (not blocking), 0 QUESTION. Verdict: READY. All 25 first-round findings are closed (table below).

Closure of the first-round findings (each checked in the revised text and, where it cites code, at the ref):
| # | Finding | Closed by | Checked |
|---|---|---|---|
| 1 | NB-8's claim about the lock | overview NB-8 reworded | database.py:299 matches the new wording |
| 2 | AC-4 stamp against a mid-cycle response | 01 AC-4 rule ("of the completed cycle whose values the response shows") and S-4.12 | alpha_bot_execution.py:953 and :2048 |
| 3 | 03 S-2.1 self-contradiction | S-2.1 rewritten: values keep, label reads "Connection Lost" | static/index.js:1464 |
| 4 | value changes beyond G-57 | SD-12, overview NB-10 and 01 NB-4 name AC-5 and S-6.4 for the operator's ruling | app.py:3416 |
| 5 | `frozen_at` form against SD-1 | SD-1 now exempts `frozen_at` (seconds kept); the 01 row reworded | - |
| 6 | `triggers_today` consumers | 01 Contract Changes list matches `git grep -ln triggers_today 37c8e983 -- tests` | three tests plus the fixture |
| 7 | "taken from the snapshot" | 01 Trigger item 2 now says it is recomputed with the live totals | app.py:2831 |
| 8 | merge order | Stack: 02 before 05; 01 only after ux-overhaul 05 is on `main`; 03 after 01 and 02; 04 last; no PR carries unmerged commits | - |
| 9 | overview record and overlaps | 04 row depends on 03; overlap notes for 02 on chrome.js, 01 AC-8 and AC-9, 03 AC-10; Status column reads draft | - |
| 10 | Cash Now re-enabled after success | 03 AC-7 rule and S-7.6 keep it disabled, reading "In cash" | static/index.js:415-416 |
| 11 | POST controls missing from AC-2 | 04 S-2.15 to S-2.19 and a second matrix table | static/index.js:1679, :1703, :1721; settings.js:297; chrome.js:341-342; ai_advisor_chat.js:173; settings-modal.js:501 |
| 12 | `changed` on a failed run | 02 S-1.6 narrowed; S-1.11 (EOD saves, then raises) gives failed with `changed` true | alpha_bot_execution.py:1166-1171 |
| 13 | TA-13 chart fan-out and recompute | R-90 to 03 AC-9 (S-9.9, S-9.10); R-91 to NB-17 with its reason | static/index.js:349-352 |
| 14 | browser-clock "Stale" | 03 AC-4 rule, S-4.12; R-62 widened | static/chrome.js:226 |
| 15 | header label precedence | SD-13; 03 S-4.13 and S-4.14; 04 AC-1 rule cites it | - |
| 16 | all-time stat drifting after the close | 01 AC-5 rule adds `account_all_time_cr`; S-5.12 | app.py:3001 |
| 17 | `/history` and `/performance` stamps | 04 Contract Changes add `data_as_of` to both routes | app.py:5063 |
| 18 | Composer callers narrowed silently | 05 R-30 narrowed, naming the excluded callers; NB-8 | app.py:5406, :5424 |
| 19 | section 8 rows missing | 05 S-6.11 to S-6.13; Trigger item 3 | - |
| 20 | no-trade scan for new reads | DC-8 in 03 and 04: extend Group E's transitive walk, with a mutation test | Group E exists in tests/security/test_retirement_action_no_trade_boundary.py |
| 21 | auth and CSRF flags off in tests | DoD rows in 02, 04 and 05 re-enable the flags | tests/conftest.py:280, :297 |
| 22 | AC-6 N5 reason | 03 S-6.11 | - |
| 23 | earlier criteria omitted | 01 rows for ux05 NB-5 and NB-7, eod AC-5, DE-CLOSED-BOUNCE-001; 03 rows for ux04 AC-4 S-4.6 and NB-5, ux05 NB-5 (`/api/chart`), DE-CLOSED-BOUNCE-001's client leg | - |
| 24 | workflow files | 03 Q-5 and 04 Q-4 ask the PM to accept "no workflow file" | no `.claude/workflows/` at the ref |
| 25 | default-window vol row | 03 AC-1 rule ("30d when none was picked") and S-1.13 | app.py:2254-2260 |

A. Wrong or unverifiable claims
none

B. Criteria missing or contradicting an enforced contract
26. CONFIRMED (not blocking) - plan 03 Gates (03-dashboard-refresh.md:438) - DC-8 extends `tests/security/test_retirement_action_no_trade_boundary.py` to cover AC-6's new Sleeves and CVaR read and mutation-tests it, but plan 03's scoped pytest run (`tests/dashboard`, `tests/app`, `tests/realtime_push`, `tests/ui`, `tests/js_syntax`) leaves out `tests/security` and does not mention the mutation test. Plan 04's gate does both (04-pages-and-header.md:357). CI's full run still executes the scan, so this does not block. Fix: add `tests/security` and "DC-8's extended no-trade scan, mutation-tested once" to plan 03's Gates line.

C. Template sections absent or empty without reason
none

D. HOW content that belongs at Gate 2
none

E. Closed-scope breaches
none

F. Correct and easily doubted
- The first round's section F stands at the ref, unchanged.
- S-2.18's chat send goes to `/ai-advisor/chat/send` (static/ai_advisor_chat.js:173), a non-`/api` path. A stale token is refused 403 by the CSRF hook, because the session survives a restart (SECRET_KEY is stable), so the scenario's refusal is reachable as written.
- The SD-13 rule that the server reckons "Stale" needs no new field: the client can take the server's time from the response it already receives. How it does so is a Gate-2 choice, not a contract gap.

Refusal enumeration: unchanged from the first round apart from these rows, which are now covered:
| Input | Write path entry | Refusal (code / status) | Enforced at | Spec covers |
|---|---|---|---|---|
| Sleeves arm, disarm and delete; flush-resync; mark-viewed; chat send | their POST routes | 403 HTML (CSRF) | app.py:373-379 | 04 S-2.15 to S-2.18 |
| Per-symphony Save with a stale token | `POST /api/symphony-settings/<symphony_name>` | 403 HTML (CSRF) | app.py:373-379 | 04 S-2.19 |
