Spec audit: feature-plans/ux-overhaul/ (00-overview.md, 01-data-truth.md, 02-advisor-overview.md, 03-correlations-realized.md, 04-app-shell.md, 05-dashboard-charts.md) (tier: epic)
Verified against: PlanetStopper @ 37c8e983b64c ## main...origin/main (only tracked change: .claude/pr125-review-findings.json; app.py, static/, templates/, advisors/, analytics.py, database.py, alpha_bot_execution.py unmodified at the ref)
Checklist: .claude/agents/_refs/spec-review-checklist.md (project copy)
Templates: ~/.claude/skills/scaffold/templates/epic.md, feature.md
Source checked: 00-overview.source.md read whole, plus the verbatim source and PM answers in my brief; Requirements as Given matches both sentence for sentence (G-1 to G-37), and every G-17 to G-21 item has an R row.
Scripts: `plan-trace check --stage gate-1` clean and `plan-trace self-contained` 0 findings on all six files; angle-bracket grep empty.
Result: 24 CONFIRMED, 3 QUESTION

Blocking for re-check: 1, 2, 3, 6, 9, 12, 13, 14, 16, 18, 24 (the rest are corrections a re-check will verify too).

A. Wrong or unverifiable claims

1. CONFIRMED - 01 AC-1 rule, S-1.1, DEC-1, NB-2; 02 AC-3 rule ("naive Sharpe values"); overview R-22 - The specs label the autotune card's `naive_sharpe` value "Naive Sharpe" and say no Sortino field exists. The code shows the value is not a Sharpe ratio. It is the Optuna study's best objective value: the CRRA expected-utility objective whenever the run's spec bundle has utility family CRRA (the Phase-1 bundle), otherwise a Sortino ratio. The audit (ux-audit-report.md:145 "Sortino shows the naive Sharpe value") went by the column name, which is the document-over-code defect this audit exists to catch. Relabelling it "Naive Sharpe" swaps one false label for another. The route also returns `sortino_sentinel_pct` (database.py:865), so NB-2's "carries no Sortino field" is wrong as written. G-17 states the same mistake, so this belongs to the operator as an Open Question with this evidence, not a criterion that builds the false label.
   - autotuner.py:2776 "naive_sharpe_value = study.best_value"
   - autotuner.py:2695-2700 split score = "math_engine.compute_crra_eu_objective(...) if _objective_kind == "crra_eu" else compute_sortino_ratio(_split_returns_pct)"
   - autotuner.py:2434-2436 "_objective_kind = ("crra_eu" if _utility_family.upper() == "CRRA" else "sortino_loss_aversion")"
   - database.py:750 "naive_sharpe:    Raw Optuna best trial Sortino before the selection haircut."
2. CONFIRMED - 02 AC-1 (one status line per producer, with its "observation count") against 02 NB-5 (collection unchanged) - The route collects up to 50 rows per role, in `_ADVISOR_ROLES` order, then cuts the list to 50. OVERFITTING_CONSCIENCE is first. When it has 50 or more rows (the audit copy had 50 identical rows), every later producer (SPEC_CRITIC, MARKET_PRISM, ASSET_SWAP, LOGIC_CHANGE) is dropped before the template sees it. AC-1 would then silently omit producers that have observations, and each count would be a capped sample shown as a total. S-1.1 (48 plus 2 out of 50 stored) cannot detect either problem.
   - app.py:6640-6643 "for role in _ADVISOR_ROLES: observations.extend(database.get_advisor_observations_for_role(role, limit=_ADVISOR_OBSERVATIONS_PAGE_LIMIT))"
   - app.py:6660 "observations = deduped_obs[:_ADVISOR_OBSERVATIONS_PAGE_LIMIT]"
   - app.py:8862-8863 `_ADVISOR_ROLES` lists "OVERFITTING_CONSCIENCE" first
3. CONFIRMED - 02 S-1.9 - "The observations query fails → the Overview shows the empty text and every other tab renders." At the ref nothing guards the query. The accessor raises on a database error, so the whole page returns 500. The scenario therefore adds a behaviour without saying so. It also renders a failed read as "No observations from advisors at this time.", presenting a failure as an empty result.
   - app.py:6640-6643 (no try around the loop)
   - database.py:1249-1257 "conn = get_ro_connection() ... cursor.execute(...)" with no exception handling
4. CONFIRMED - 03 AC-8 third clause and S-8.3 - "The latest successful run analysed only one symphony → the insufficient-data state." No `ok` run can have fewer than two symphonies with series. Fewer than two live symphonies sets `insufficient_symphonies`, and fewer than two series sets `series_unavailable`. The clause cannot be reached, and its fixture cannot be captured. The real gap is uncovered: the latest run is `series_unavailable` or `insufficient_symphonies` and no earlier `ok` run exists. S-8.1 says "no retirement run has completed", but a run did complete, just not `ok`.
   - advisors/retirement_recommender.py:640-641 "if len(symphony_ids) < 2: summary["status"] = STATUS_INSUFFICIENT_SYMPHONIES"
   - advisors/retirement_recommender.py:659-660 "if len(series_by_symphony) < 2: summary["status"] = STATUS_SERIES_UNAVAILABLE"
5. CONFIRMED - 01 S-10.8, and a gap in AC-10's family:
   - S-10.8's Given cannot occur. The producer never emits verdict `clean` with `n_pass` 0: when `n_checks` > 0 and `n_pass` == 0 it returns `no-verifiable-claims`.
   - AC-10's rule specifies "at least one unverifiable" and "every check passes", but leaves out a third state the producer can emit: verdict `clean` with `n_unverifiable` 0 and `n_pass` < `n_checks`. A check whose classification is not one of the four counts in `n_checks` only.
   - Evidence: advisors/prism_numeric_verifier.py:288-293 "if summary["n_checks"] > 0 and summary["n_pass"] == 0: ... return "no-verifiable-claims""; :273-280 "summary["n_checks"] = len(checks) ... if classification in ("pass", "flagged", "overridden", "unverifiable"): summary[f"n_{classification}"] += 1"
6. CONFIRMED - 05 Known-Unconfirmed item 4 and Q-2 - The spec lists as unverified whether the hero series and the portfolio strip follow the `account` filter. The code settles it:
   - The hero series is computed portfolio-wide with no account input.
   - The strip is computed from the filtered state, but its account value and account-basis figures come from unfiltered account-level totals. After a pick, the strip mixes a filtered set of symphonies with whole-account totals (mixed basis in one figure).
   - Q-2's default caption, "the hero and strip cover all accounts", would be false for the strip. Q-2 should be rewritten on these facts, and AC-6 must say what the strip shows after a pick.
   - Evidence: app.py:3146-3153 (filter on `state_data`); app.py:3351 "portfolio_strip = _compute_portfolio_strip(state_data, ...)"; app.py:2099 "_cached_portfolio_value = _account_totals_cache.get("portfolio_value")"; app.py:2269 "_shadow_result = analytics.get_portfolio_daily_returns_from_shadow()" and :2280 "_bh = analytics.get_portfolio_bot_and_held_daily_returns()" (no account argument)
7. CONFIRMED - Scenario inputs in a format the producer never writes (test-design contract: producer-computed fields use the producer's real format):
   - 01 S-6.1 uses trigger times "11:07:00, 11:33:00, 10:12:00". The producer writes `HH:MM`. alpha_bot_execution.py:717 "current_time_str = current_et.strftime("%H:%M")" → reporting.py:131 "time_triggered": sym.get("triggered_at_time", "")
   - 02 S-8.1 uses Prism `created_at` "2026-10-22T11:10:06.123456Z". `advisor_observations.created_at` is SQLite's default, UTC with no zone marker and no fraction (migrations/017_advisor_observations.sql:12 "created_at TEXT NOT NULL DEFAULT (datetime('now'))"; the insert omits the column, database.py:1195-1197). Observation times are the same. Read literally, S-8.4 ("a time with no zone → date only") would turn every Prism and observation stamp into a date. The spec must state that these zone-less stamps are UTC.
   - 02 S-7.2 uses `fetch_error: 429` and 03 S-5.2 uses `fetch_error: 503`. The producer writes "fetch_error: HTTP 429": advisors/composer_series_client.py:292 "last_error = f"HTTP {status}"" and advisors/retirement_recommender.py:611 "f"{_REASON_FETCH_ERROR_PREFIX}{result.error}"".
8. CONFIRMED - Citations that moved, and settleable items marked unconfirmed:
   - static/ai_advisor.js:531 is now :529 ("var symLabel = escHtml(r.symphony_name || r.symphony_id || '')").
   - templates/ai_advisor.html:2632 is now :2634 ("<span class="card-candidate-id">{{ p.symphony_id }}</span>"); :2676 is now :2679 (`p._incumbent_display_name`); :2949 is now :2942 (obs Symphony cell); 02's :2948 is now :2939 ("<td>{{ obs.created_at | e }}</td>").
   - 01 Known-Unconfirmed item 2 (whether any autotune row carries `symphony_name`) is settled by the code: the select has no such column, so `r.symphony_name` is always undefined. database.py:860-866 "SELECT id, run_timestamp, symphony_id, oos_alpha, ...".
   - 02 Known-Unconfirmed: `advisor-observations-section` is referenced by 2 test files, not 1 (grep -rl over tests/).
   - 01 Trigger item 8: a key with spaces only does not throw a SyntaxError. It builds a valid descendant selector, `querySelector` returns null, and `.addEventListener` throws a TypeError (static/settings.js:457). Parentheses do throw a SyntaxError. S-8.2 should list spaces-only as its own case.
9. CONFIRMED - 05 Trigger item 8 and AC-7 (S-7.1, S-7.3) - "Polls never update the banners" understates the code. Both banners are `hidden` in the server render, and `updateBanners` is their only un-hider and is never called. So neither banner has ever appeared on `/`, and the fleet Dismiss button is unreachable at the ref. AC-7 will put on the reference page two banners the operator has never seen. The market-state banner then shows on every poll in both branches (templates/index.html:1815-1822), so it becomes a permanent strip. The spec must state this against NB-11, R-7 and G-5, and must say whether the banners show at first paint. S-7.3's "a fleet alert at load" describes a state the page never has.
   - templates/index.html:766 "<div id="fleet-correlation-banner" hidden"
   - templates/index.html:800 "<div id="market-state-banner" hidden"
   - templates/index.html:1782 "function updateBanners(data) {" (sole definition; a grep of templates/ and static/ finds no call)
10. CONFIRMED - overview Stack and Merge Rules bullet 2, plan 05's Depends on, and R-71 maps:
   - The overview says 05 and 01 are built concurrently "because their files do not overlap". Both edit app.py:
     - 01 Contract Changes changes `/api/performance`, `/api/symphony-settings/` and `/api/history/`, and 01 Docs lists docs/generated/app.md.
     - 05 Contract Changes changes the server render of `/` (`dashboard()`), and 05 Docs lists docs/generated/app.md.
   - G-37 says model-unpin edits app.py and lands first, and the overview applies that to 01 only. Plan 05 (Base main, "Depends on: none") can branch before model-unpin merges, and R-71 maps only to 01, 02 and 03.
   - Evidence: feature-plans/ux-overhaul/05-dashboard-charts.md:286 "The server render of `/` additionally carries the hero series ... and each triggered card's guard alpha"; feature-plans/model-unpin.md:13 names "the display map at `app.py:6527-6534`".
11. CONFIRMED - overview Stack rule 6, 01 and 02 Depends on, 02 Q-1 - These cite `feature-plans/model-unpin.md` and `feature-plans/symphony-tenure-review.md`. Neither is tracked at the ref (`git ls-files` returns nothing for either; `git status` shows "?? feature-plans/model-unpin.md" and "?? feature-plans/symphony-tenure-review.md"). Under the template's rule an untracked plan is replaced by a summary; otherwise both must be committed before this suite is approved. Base and Depends on must name something that exists in the repo.

B. Criteria missing or contradicting an enforced contract

12. CONFIRMED - 01 AC-4, Contract Changes, S-4.9, S-4.10, and the math-r0 row in Earlier criteria - The spec leaves the wire contract of `/api/performance` undecided, and either choice breaks a scenario:
   - If the route reinterprets integer `days` as calendar days, the dashboard detail panel's `days=60` request changes, contradicting S-4.9.
   - If integer `days` keeps its meaning, the page must send new tokens. The route refuses anything that is not an integer or "ytd" with 400.
   - The spec must state the request values the page sends and what the route accepts.
   The Earlier-criteria row also marks `math-r0.md` AC-5 "kept", but the shipped decision records the numeric buttons as a deliberate trading-day contract. AC-4 contradicts that decision, so the disposition is "contradicted", with D-9 as the reason.
   - app.py:5226-5234 "days = int(raw_days) except (TypeError, ValueError): ... "message": "days must be an integer"" (400)
   - static/index.js:493 "...&days=60')"
   - DECISIONS.md:5825-5827 "`/api/performance`'s `days` query param keeps its DELIBERATE dual contract: the six numeric buttons (30/60/90/125/252/1260) stay trading-day counts by design"
13. CONFIRMED - 01 AC-9 S-9.4, the Worked Example and S-8.4 - "uses globals" is a claim about what the engine runs, and the code contradicts it. A symphony whose 8 stored values equal the globals still pins them:
   - The engine reads each stored parameter first and falls back to the global only for a missing key.
   - Every strategy row is created holding all 8 default values.
   - So after a global edit on `/settings`, that symphony keeps the old values while its entry reads "uses globals".
   S-9.4 (8 stored values, all equal) and S-8.4 (empty params after Reset) give one label to two different engine states, defect class DC-3 (one label, two meanings). D-11 governs the count; the label still needs to tell the two states apart.
   - alpha_bot_execution.py:1251-1252 "acc_TRIGGER_THRESHOLD_PCT = acc_params.get("TRIGGER_THRESHOLD_PCT", TRIGGER_THRESHOLD_PCT)"
   - database.py:554 "save_symphony_strategy(symphony_name, DEFAULT_STRATEGY, DEFAULT_LOCKED_VARS)" (runs on the first read of a row)
14. CONFIRMED - 01 Fail-Closed Behaviour ("the one refusal ... is the existing CSRF refusal") and S-8.4, S-8.5, S-8.6 - `POST /api/settings` has two more refusals the Reset and Save path can reach:
   - 400 "Rejected keys not in settings allowlist". The same page's Master LIVE toggle adds `LIVE_EXECUTION` to the save payload, so any Save after touching it refuses everything, including a pending Reset. static/settings.js:525 "_pendingGlobals['LIVE_EXECUTION'] = nowOn ? 'True' : 'False';"; app.py:6139-6147
   - 500 "An internal error occurred" when a pending override is not numeric. The client sends the raw string and the server calls `float(v)` after it has already written the globals (a partial write); S-8.5's "Save sends both" can hit it. static/settings.js:547-548 "numParams[k] = isNaN(n) ? v : n;"; app.py:6150 "set_key(ENV_FILE_PATH, key, str(val))" before app.py:6160 "params = {k: float(v) for k, v in ...}"
   - S-8.6's observable, "the existing 403 refusal shows", is not what the operator sees. `abort(403)` returns HTML, `r.json()` rejects, and the alert reads "Save failed: network error". app.py:379 "abort(403, description="CSRF token missing or invalid")"; static/settings.js:574 "alert('Save failed: network error');"
15. CONFIRMED - 02 S-4.1 and its Earlier-criteria row for `retirement-visibility.md` AC-6 - That criterion requires "pairs screened at the threshold; blocked counts per gate". S-4.1 shows "Pairs screened 10 of 55" without the threshold and a single "Blocked 10". That tile is a sum of four counts the summary stores separately, and the spec never states it as a sum or says what it shows when one count is missing. The row says only that "every state keeps its own copy". The spec must say whether the per-gate counts and the 0.65 threshold stay; if they go, the disposition is "contradicted".
   - feature-plans/retirement-visibility.md:105-108 "pairs screened at the threshold; blocked counts per gate"
   - advisors/retirement_recommender.py:535-538 "blocked_uncertainty", "blocked_stress_too_thin", "blocked_stress_below_threshold", "blocked_no_candidate"
16. CONFIRMED - 01 AC-4 rule ("YTD from 1 January" on both pages), with no History YTD scenario or matrix cell - At the ref, History's YTD always starts on or before 31 December of the previous year. The page sends the rounded-up count of local days since 1 January, and the route subtracts that count from today's UTC date. On 2 January this sends 2, giving a cutoff of 31 December. The rule needs a History YTD scenario (01 S-4.7 is Performance only) and a matrix cell.
   - static/history.js:389-394 "return Math.ceil((now - startOfYear) / 86400000);"
   - analytics.py:2003-2004 "if isinstance(window, int): return today - _td(days=window)"
17. CONFIRMED - 01 S-6.5 - "A day with no exits ... shows no rows." At the ref the drilldown reuses `renderTriggers`, which writes a placeholder row, and a day whose triggers all fail validation still gets a bar. Either the scenario keeps the existing placeholder row, or the spec states the change.
   - static/history.js:322-325 "No exit records for this window."
   - analytics.py:2352 "daily_map[date_part] = daily_map.get(date_part, 0.0) + day_alpha" (set even when `day_exits` is empty)
18. CONFIRMED - Earlier criteria on the same surface, omitted (blocking under the new-format check):
   - Plan 05 AC-7 changes the fleet banner but its table lists neither feature-plans/engine-correctness-remediation.completed.md:77 AC-V3.1 ("surfaces a high-visibility dashboard banner") nor feature-plans/vwap-remediation.completed.md:40 AC-4.1, which fixes the banner copy "Fleet-correlated trigger event detected: <count> symphonies fired <reason> within <M> minutes — review before acting". The live copy at templates/index.html:1795-1798 is "Concentrated risk — N of M symphonies exited on ...". AC-7 S-7.2 needs a disposition against that copy.
   - Plan 01 AC-6 changes the History drilldown but omits feature-plans/fix-ops-cluster.md:10 F-020 ("an expandable per-exit detail row ... sourcing EXISTING fields from the already-loaded history payload").
19. CONFIRMED - 04 S-2.6 - The scenario asserts resolved padding values ("padding 28 by 32 px for the panel, 18 by 20 px for the card"), which conflicts with the test-design contract (visual tests assert design-system contract, never a computed size) and with plan 04's own DoD row for AC-2 ("no computed-value assertion"). The quoted value is also incomplete: templates/index.html:30 "padding: 28px 32px 24px;".
20. CONFIRMED - 02 AC-4, AC-5 and Design-System Mapping; 03 AC-2, AC-7 and Design-System Mapping, against 04 DEC-1 and the overview stack order - 02 and 03 must render `.mini-stats`, `.mini-stat`, `.sym-card`, `.status-pill`, `.legend-as-of` and `.section-header` on `/ai-advisor`. At the ref those classes exist only in `templates/index.html`'s own style block. Making them shared is plan 04's DEC-1, and 04 lands after 02 and 03 and after 05, which owns `templates/index.html`. 02 calls the sharing "the HOW", but 02 and 03 can only use the classes in one of two ways:
   - copy the definitions, creating the second source that 04's DC-1 forbids; or
   - extract them from `templates/index.html`, breaking the stated file separation from 05.
   The suite must say which plan makes the primitives available to 02 and 03. templates/index.html:27 ".hero-section {" and :277-305 (`.mini-stats`), inside index.html's `<style>`; plan 04 Trigger item 3 (`static/layout.css` "shares only the page inset").
21. CONFIRMED - 01 DC-6 sibling search - S-3.8 removes the "Upside/Downside capture vs SPY" rows. The DC-6 query (`252|1260|Logic-change proposals carry|...`) does not find the tests that pin those rows, so the class's own search misses its siblings: `upside_capture`, `data-unavail` and `TIER2` appear in tests/dashboard/test_risk_adjusted_display.py and tests/app/test_risk_adjusted_performance_api.py (grep -rln "upside_capture\|data-unavail" tests). static/performance.js:65-72 "upside_capture ... TIER2_PLACEHOLDER_KEYS".

C. Template sections absent or empty without reason

22. CONFIRMED - 02 and 04 have no Fail-Closed Behaviour section. The checklist allows omitting it only when the feature sends no input to a write path, and both plans send to write paths:
   - Plan 02: Run now, Approve and Reject to `POST /ai-advisor/retirement/run` and `/approve|/reject`. The refusals are 409 `market_open`, 409 `already_running`, 500 on thread start, 400 invalid `candidate_id`, 500 accessor error, plus CSRF 403 and auth.
   - Plan 04: Force run to `POST /api/trigger` and Emergency Liquidate to `POST /api/sell_account`. The refusals are five 400s and CSRF 403.
   Each refusal is unchanged and its scenario exists (02 S-4.4, S-4.5, S-4.15; 04 S-4.5, S-8.2), but the section must list them by code and line. app.py:8348-8360 ("error": "invalid candidate_id" / `type(exc).__name__`); app.py:8431-8434 (409 `market_open`, 409 `already_running`); app.py:5452-5472 (`/api/sell_account` 400s).

D. HOW content that belongs at Gate 2

23. CONFIRMED - 01 AC-4 rule and 05 AC-4 rule name the private function `analytics._window_cutoff_date` as the contract. The observable contract is the calendar span: N calendar days back from today inclusive, YTD from 1 January, 1Y = 365. The function name belongs in Authorities or at Gate 2. analytics.py:1989 "def _window_cutoff_date(window: object) -> date | None:". (01 Authorities Touched may keep it.)

E. Closed-scope breaches (builds something named in Not Building, or builds nothing for a criterion)

24. CONFIRMED - R-6 / G-8 ("every control on it works") - The `/settings` Master LIVE toggle can never be saved, and no criterion, plan or NB entry covers it. Clicking it adds `LIVE_EXECUTION` to the save payload, and the server refuses that key with 400 by design. The toggle is a dead control that also blocks every other pending setting (see 14). Plan 01's R-6 narrowing lists only the window buttons and Reset. The suite needs a criterion (for example the toggle shown read-only with its reason) or an NB entry.
   - static/settings.js:510-528 `initLiveToggle` → :525 "_pendingGlobals['LIVE_EXECUTION'] = ..."
   - app.py:6113-6124 "LIVE_EXECUTION is deliberately excluded" from `_SETTINGS_WRITE_ALLOWLIST`

QUESTIONS

25. QUESTION - Workflow file, every screen in every plan - There is no `.claude/workflows/` directory at the ref (`ls .claude/workflows` fails), so each plan's "no workflow file" line is accurate and these screens have no operator-confirmed workflow to check User Tasks against: History, Performance, Settings, the settings modal, the AI Advisor Overview, Retirement, Correlations, Chat, Strategy Builder and Frontrunner Builder tabs, the shared header, login, and the dashboard `/`. This does not block. It settles only if the operator confirms workflow files.
26. QUESTION - 03 Fixture provenance - Run summaries that carry pairs cannot be captured from production:
   - Production rows predate the change.
   - The Composer series a pair is computed from is never stored.
   - The plan computes pairs with `compute_pairwise_correlations` over "the captured series", which in practice means `tests/fixtures/composer/symphony_daily_series_v1.json`, whose provenance is synthetic-from-docs (CLAUDE.md, `advisors/composer_series_client.py` row).
   That is neither captured from the real producer nor schema-validated. It settles with a real recapture (the open `/api-fixture` follow-up) or the producer owner's sign-off recorded in the plan.
27. QUESTION - 04 AC-4 and G-14 - A CSRF 403 on `POST /api/sell_account` fails silently today: the response body is HTML, `r.json()` rejects, there is no `.catch`, and the modal stays open with no message. G-14 forbids changing this behaviour in 04, so S-4.5 correctly covers only the JSON 400s. Should the overview record the silent 403 as a follow-up (a sensitive plan of its own) so it is not lost? static/chrome.js:189-196 "fetch('/api/sell_account', {...}).then(function (r) { return r.json(); }).then(function (d) { closePanicModal(); alert(...) });" (no `.catch`).

F. Correct and easily doubted

- Writer item 1 / overview NB-14 / 01 NB-9 and Q-2 - Confirmed. The modal reads and writes the strategy row under the lowercased Composer hash, while the engine and the post-mortem read it under the lowercased display name. One more fact for that plan: the modal's GET writes a default row under the hash on first open, although its docstring says "Never writes". app.py:6177 "symphony_name = database.normalize_name(symphony_name)" (the hash, templates/index.html:1215); alpha_bot_execution.py:1242-1243 "normalized_name = database.normalize_name(symphony_name) ... get_symphony_strategy(normalized_name)"; database.py:554.
- Writer item 2 (04 AC-8) - `/api/trigger` always answers 200 with "Manual trigger disabled — use the scheduler." (app.py:4932-4935), and chrome.js shows "Triggered!" on any `r.ok` (static/chrome.js:20).
- Writer item 3 (05 AC-7) - The Dismiss fetch sends no `X-CSRF-Token` and hides the banner in `.then`, which fetch resolves on a 403 too (templates/index.html:773); the global hook refuses every POST without the token (app.py:490-493). Item 9 above adds that the button is unreachable today.
- Writer item 4 (05 AC-6) - `window.loadState` is read at static/chrome.js:25 and :63 and assigned nowhere under static/ or templates/.
- Writer item 5 (03 Trigger item 1) - The matrix basis is post-mortem `live_ret` aligned by date, not shadow history (app.py:6683-6696, analytics.py:1570 default `days=60`). G-12's "from shadow history" wording is the PM's; the plan states the correction and keeps the ruled move.
- Writer item 6 (01 Q-3) - At 30 calendar days the insufficient-history banner shows on every default load: app.py:5322 "insufficient_history = observation_count < _PERFORMANCE_MIN_HISTORY_DAYS" with the constant at 30 (app.py:44). It only shows the banner; metrics are still computed (app.py:5328-5329).
- Writer item 7 (01 Q-1, money) - Justified. `/settings` keys symphonies by the lowercased display name (app.py:6083-6099), the same key the engine reads, so Save after Reset writes `params: {}` that the engine reads next cycle.
- Writer item 8 (02 Q-1) - feature-plans/symphony-tenure-review.md exists, reads "Status: ready", and targets the Overview Retirement panel. It is untracked (see 11).
- 05 NB-2 and DEC-2 - Chart.js is unpinned at the ref on both pages (templates/index.html:11, templates/performance.html:617 "https://cdn.jsdelivr.net/npm/chart.js"), so keeping it unpinned changes nothing.
- 03 constants - The 30 and 181 minimums, the 0.65 screen and the basis label are read from the producer, as the plan says: advisors/retirement_recommender.py:57 (0.65), :65 (MIN_OBS_FLOOR = 30), :180 (STRESS_GATE_MIN_ALIGNED_DAYS, derived), :145 (label). `PairResult` already carries `window` (advisors/correlation_diagnostic.py:77, :236-245).

Refusal enumeration:
| Input | Write path entry | Refusal (code / status) | Enforced at | Spec covers |
|---|---|---|---|---|
| any POST | every route below | 302 HTML / 401 JSON / 503 misconfigured (auth hook) | app.py `_auth_before_request` | unreachable in-session: every page is behind the same hook; all plans N4 |
| any POST | every route below | 413 body over 64 KiB | app.py:454 `MAX_CONTENT_LENGTH` | unreachable: every payload these plans send is under 1 KiB |
| CSRF token | POST /api/settings | 403 HTML | app.py:379 | 01 S-8.6 (observable wrong, item 14) |
| globals keys | POST /api/settings | 400 "Rejected keys not in settings allowlist" | app.py:6139-6147 | MISSING (item 14; source is settings.js:525) |
| non-numeric override | POST /api/settings | 500 "An internal error occurred", after a partial write | app.py:6150, :6160, :6165-6167 | MISSING (item 14) |
| live_mode type / confirm | POST /api/symphony-settings/{id} | 400 x3, 500 | app.py:6241-6285 | unreachable for this suite: 01 S-5.13 sends the request unchanged |
| CSRF token | POST /api/symphony-settings/{id} | 403 HTML | app.py:6231 `_validate_csrf()` | unchanged (01 S-5.13) |
| market state | POST /ai-advisor/retirement/run | 409 {"status":"market_open"} | app.py:8431 | 02 S-4.4; Fail-Closed section absent (item 22) |
| run in flight | POST /ai-advisor/retirement/run | 409 {"status":"already_running"} | app.py:8434 | 02 S-4.5; section absent (item 22) |
| thread start fails | POST /ai-advisor/retirement/run | 500 | app.py `ai_advisor_retirement_run` | 02 S-4.15 (unchanged); section absent |
| candidate_id | POST /ai-advisor/retirement/approve, /reject | 400 "invalid candidate_id"; 500 `type(exc).__name__` | app.py:8348-8360 | 02 S-4.15 (unchanged); section absent |
| CSRF token | POST /ai-advisor/retirement/* | 403 HTML ("Could not start the run.") | app.py:379, static/ai_advisor.js:1259-1262 | 02 S-4.15 (unchanged) |
| confirm fields | POST /api/sell_account | 400 x5 (account id required/mismatch, phrase required, phrase not LIQUIDATE, missing credentials) | app.py:5452-5472 | 04 S-4.5; section absent (item 22) |
| CSRF token | POST /api/sell_account | 403 HTML, silent in the client | app.py:379, static/chrome.js:189-196 | unchanged by G-14 (QUESTION 27) |
| none | POST /api/trigger | 200 "Manual trigger disabled — use the scheduler." | app.py:4932-4935 | 04 S-8.1 |
| CSRF token | POST /api/trigger | 403 HTML | app.py:379 | 04 S-8.2 |
| CSRF token | POST /api/fleet-alert/dismiss | 403 HTML | app.py:490-493 | 05 S-7.5, Fail-Closed |
| days | GET /api/performance (read) | 400 "days must be an integer" | app.py:5226-5234 | 01 S-4.10 (wire form undecided, item 12) |
| scope / symphony_id | GET /api/performance (read) | 400 invalid scope; 400 symphony_id required | app.py:5205-5213, :5237-5244 | unreachable: the page sends a valid scope and an id from its own picker |

---

Re-check (the one allowed): same folder, revised in place
Verified against: PlanetStopper @ 37c8e983b64c (HEAD unchanged; tracked tree unchanged)
Scripts: `plan-trace check --stage gate-1` clean and `self-contained` 0 findings on all six files; angle-bracket grep 0 on all six.
Result: 4 CONFIRMED (1 blocking), 1 QUESTION carried

Disposition of the first-pass findings:
- Resolved: 1 to 24, and QUESTION 26 (03 Prerequisites) and QUESTION 27 (overview NB-16).
- Checked at the cited lines: 01 AC-1, AC-4, AC-6, AC-8, AC-9 and AC-10, Fail-Closed and Contract Changes; 02 AC-1, S-4.1, S-4.13, AC-8 and Fail-Closed; 03 AC-8 and Prerequisites; 04 S-2.6 and Fail-Closed; 05 Trigger 8, AC-6, AC-7, Contract Changes and the earlier-criteria rows; the overview Stack rules, SD-6, SD-7, R-22 and R-71.
- Correction to my own first-pass refusal table: `/ai-advisor/retirement/approve` and `/reject` answer 200 with "error": "invalid candidate_id", not 400 (app.py:8348-8350 "), 200"). Plan 02's Fail-Closed section states this correctly.
- Still open: QUESTION 25 (no `.claude/workflows/` directory). The writer is carrying it to main.

A. Wrong or unverifiable claims
R1. CONFIRMED (non-blocking) - 01 Trigger item 1 ("the row does not record which objective scored it") and 01 Known-Unconfirmed item 1 - Each autotune row stores the spec bundle it ran under, and the autotuner derives the objective from that bundle's facets. So which objective scored a row can be recovered, though `/api/autotune-runs` does not currently select the column. The tooltip naming both objectives is honest either way, so this does not block. But the Known-Unconfirmed item can be settled from the code, and Q-8 could name the actual objective for each row.
   - autotuner.py:3244 "spec_bundle_id=stored_hash,"
   - autotuner.py:2431-2436 (objective kind from `utility_family` / `objective_kind` facets)
   - migrations/020_autotune_runs_eut.sql:27 "ALTER TABLE autotune_runs ADD COLUMN spec_bundle_id TEXT DEFAULT NULL;"

E. Closed-scope breaches
R2. CONFIRMED (blocking) - 02 Design-System Mapping, overview SD-6 and the Stack rules - Plan 02 now moves the dashboard primitives out of `templates/index.html`, so 02's PR changes the reference page `/`. Plan 02 has no criterion or scenario for that change, and no check that `/` is unchanged:
   - Its screenshot list covers only the Overview and Retirement tabs (02 line 361).
   - The only pins on `/`, 04 S-1.7 and S-2.6, belong to a later PR. 04's Known-Unconfirmed line 328 still assigns the extraction to 04.
   The overview requires each PR to be verified against its own base with its own screenshot comparison, so PR 02 would ship a refactor of the protected reference page (G-5, overview NB-11) with no test of it. 02 needs an N7 scenario and a screenshot row proving `/` renders unchanged with the shared definitions, and 04 line 328 needs correcting.

B. Minor, non-blocking
R3. CONFIRMED - 03 Prerequisites against 03 Fixture provenance - The two contradict each other on whether pairs may be computed from the synthetic fixture:
   - Prerequisites allows the synthetic fixture with the producer owner's sign-off when no real capture can be made.
   - Fixture provenance says pair records are "never computed from the synthetic-from-docs fixture".
   The spec needs one rule.
R4. CONFIRMED - 04 S-2.6 ("its screenshots match the audit's dashboard reference captures") - By the time 04 lands, plan 05 has deliberately changed `/` (first-paint verdicts and dials, the fleet banner) and plan 02 has refactored its styles. Some of the audit's dashboard captures are also mid-population frames: the diagnosis, section 2 item 3, describes D-1-1440-light-full.png as "a mid-population frame". The comparison should be against the post-05 stack's own `/` capture, using a readiness signal, not the audit's captures.

Verdict: 1 blocking finding remains (R2), so per the one-re-check cap this goes to main for the PM.
