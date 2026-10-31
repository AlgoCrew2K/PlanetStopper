# Approval packet: Refresh timing: every page shows the newest data, dated, through every refresh

## What is being built

(the plan has no Summary)

## Acceptance criteria and scenarios

## Not Building

Non-goals:
- NB-1: Any repository other than PlanetStopper (excludes R-33; approved by the PM, [PM-ASSUMED]).
- NB-2: A visual redesign, new layouts, new tokens or new primitives; the UX overhaul suite and Claude Design own visuals, and every new cue in this suite uses an existing primitive (excludes R-35; approved by the PM, [PM-ASSUMED]).
- NB-3: New data sources: no new Composer, Alpaca or Anthropic endpoint and no new engine producer; every refreshed datum is one the server already stores, except one append-only `FRONTRUNNER_BUILD_SUMMARY` row in the existing `advisor_observations` table per Frontrunner on-demand build (plan 04 AC-5), which follows the `RETIREMENT_RUN_SUMMARY` precedent and needs no schema change (PM amendment, [PM-ASSUMED]) (excludes R-36; approved by the PM, [PM-ASSUMED]).
- NB-4: Replacing server-sent events with WebSockets or with a faster poll; the event stream is fixed in plans 02 and 03 (R-37; approved by the PM, [PM-ASSUMED]).

Behaviour that stays exactly as it is:
- NB-5: The engine's exit-decision path: `math_engine.py` carries zero diff, and `alpha_bot_execution.py` changes only inside the close-snapshot builder (`alpha_bot_execution.py:1115-1163`) (excludes R-38; approved by the PM, [PM-ASSUMED]).
- NB-6: Trade, liquidation and order request payloads: `POST /api/sell_account`, the Cash Now sell, the Managed Sleeves order path and the engine's own orders send the same URL, method, headers and body as at the ref (excludes R-39; approved by the PM, [PM-ASSUMED]).
- NB-7: `LIVE_EXECUTION`, each symphony's `live_mode` and the settings write allowlist (excludes R-40; approved by the PM, [PM-ASSUMED]).
- NB-8: The engine's once-a-minute spawn at every `:00`, around the clock. At the ref the engine's own lock skips a minute only while the running engine is under 60 s old (`alpha_bot_execution.py:611-613`, `database.py:299`); plan 02 AC-5 adds the skip for runs longer than that, and every other minute still spawns (excludes R-41; approved by the PM, [PM-ASSUMED]).
- NB-9: No blocking I/O is added to the 1-minute execution path (excludes R-42; approved by the PM, [PM-ASSUMED]).
- NB-10: The value each metric computes, except that one computation replaces two inside one `/api/state` response (TA-6, plan 01 AC-2), that a frozen figure reads its account totals as of its snapshot (plan 01 AC-5, Q-1), and that a guard alpha whose `current_return` is absent is left absent instead of computed against 0.0 (plan 01 S-6.4); the last two go beyond G-57's single exception and are recorded as SD-12 for the operator's ruling (excludes R-43; approved by the PM, [PM-ASSUMED]).
- NB-17: The per-poll re-aggregation of minute-level `shadow_history` into daily series (3.02 s of a 3.36 s `/api/state` in the audit's profile, part of TA-13); plan 01 AC-2 removes the duplicate computation (1.69 s), plan 05 S-6.1 measures what remains on the droplet, and caching the daily series is its own performance plan (excludes R-91; approved by the PM, [PM-ASSUMED]).
- NB-11: The database schema, except additive changes (excludes R-44; approved by the PM, [PM-ASSUMED]).

Deferred:
- NB-12: Findings the audit marks covered stay with their plans and are not re-specified: TA-11 (the newest-request guard on `loadState`, ux-overhaul 05 C3 round 2, RED `eeba10be` and GREEN `c20265db` on `ux-overhaul/05-c3`), TA-43 to TA-50 (ux-overhaul 05 AC-1 to AC-7), TA-51 (ux-overhaul 04 AC-8) and TA-52 (ux-overhaul 03 AC-2) (excludes R-45's re-specification; approved by the PM, [PM-ASSUMED]).
- NB-15: Real defects the audit found that are not timing: a silent Chart.js CDN failure (its TC-27; ux-overhaul 05 NB-2 keeps the tag) and the Strategy Builder's "Chat about this" hand-off writing one storage key while the chat reads another (its TC-32, `static/ai_advisor.js:742` against `static/ai_advisor_chat.js:296`); each opens its own plan (approved by the PM, [PM-ASSUMED]).
- NB-16: Found during this spec's investigation, outside the audit: post-mortem stage 1 counts every dict-valued `bot_state` key, the close snapshot included, in `total_monitored` (`reporting.py:51-55`), and the EOD divergence loop logs a false "coverage gap" ERROR for the snapshot key every close (`alpha_bot_execution.py:1066-1075`); both sit outside the close-snapshot builder the PM allowed (G-52), so each is a follow-up (approved by the PM, [PM-ASSUMED]).

Protected surfaces (consumed, never changed):
- NB-13: Composer, Alpaca and Anthropic APIs (excludes R-46; approved by the PM, [PM-ASSUMED]).
- NB-14: The no-trade boundary: `POST /ai-advisor/proposal/approve` (the only route that reaches Composer symphony creation, which stays undeployed), `POST /api/sell_account` behind Emergency Liquidate, the Managed Sleeves arming ladder, `LIVE_EXECUTION` with each symphony's `live_mode`, the settings write allowlist, and the retirement approve, reject and run routes; their boundary tests (`tests/security/test_frontrunner_no_trade_boundary.py`, `tests/security/test_retirement_action_no_trade_boundary.py`, `tests/security/test_retirement_recommender_no_trade_boundary.py`, `tests/app/test_sell_account_panic_confirm.py`, `tests/app/test_f003_liquidation_confirmation.py`, `tests/app/test_dashboard_no_order_path.py`) stay green on the assembled stack (approved by the PM, [PM-ASSUMED]).

## Narrowed mappings

R-10: The operator's selections (hero window, account, open detail panel) persist through every refresh, and Cash Now, Force run and Emergency Liquidate keep working through the day; maps to 03, 04; left out: Force run's reply and Emergency Liquidate's request, phrase gate and refusal messages are ux-overhaul 04 AC-4, AC-8 and AC-9 (NB-12); this suite adds the token refresh and the not-sent message
R-54: No renderer writes to a missing element, and the server stops computing what no client reads (TA-40); maps to 01; left out: the unread but documented `/api/state` fields (`html`, `accounts_map`, `shadow_divergence`, `port_state`, `exit_authority`, `execution_start_time`) stay (plan 01 Q-4)
R-56: The scheduled 09:30 start and the 15:53-15:59 blackout never read as a fault (TA-17); maps to 02; left out: the dated as-of before the first 09:30 cycle is R-52's rule, delivered by plan 01
R-59: On the UTC host every "today" the server uses is the ET trading date (TA-39); maps to 02; left out: the user-visible date consequences on `/api/chart` (TA-29) and `/api/history` (TA-28) are delivered by plans 03 and 04

## Open questions

none
