Spec audit: feature-plans/symphony-tenure-review.md (tier: feature) - RE-CHECK (round 2, the one permitted re-check)
Verified against: AlphaBotPM @ ed7f43ce5f9f ## main...origin/main (HEAD = origin/main = ed7f43ce5f9f5a6901eee3b030d3dced576df28b; working tree: `.claude/pr125-review-findings.json` modified plus untracked `.claude/` files, none of them cited by the spec)
Checklist: .claude/agents/_refs/spec-review-checklist.md (repo copy), plus the PlanetStopper bindings in .claude/agents/spec-auditor.md
Scripts: `plan-trace check --stage gate-1` clean; `plan-trace self-contained` 0 findings; the placeholder grep found nothing
Result: 3 CONFIRMED, 1 QUESTION (round 2)

Round-1 dispositions. Each was re-read at the cited lines in the revised spec.
- Resolved: 1 (S-8.14 now expects a redirect; S-9.8 depends on the path type), 2 (NB-14, S-8.9, T-2), 3 (Q-14 and the Earlier-criteria row), 4 (Q-15, S-9.10, S-9.17, polish AC-4 marked changed), 5 (Known-Unconfirmed, S-5.12), 6 (the AC-10 rule names the permitted imports; S-10.1 names each scan and its paths; S-10.7), 7 (common composite window, Composer basis only: S-4.6 to S-4.8, D-1), 8 (S-3.13), 9 (live defined, S-1.15, Q-18), 10 (D-4, S-1.20 to S-1.22), 11 (S-3.11, S-3.12), 12 (S-1.18, S-1.19, S-2.14, S-5.11, S-7.8), 14 (Security Considerations and Q-9 give the split; the header keeps the union and passes plan-trace), 15 (Q-16, Q-17), 16 (Migrations Required, S-10.5), 17 (TENURE_REVIEW_MIN_TRADING_DAYS: no other occurrence in the code, docs or .env.example at the ref; read at each run), 19 (the read-only queries are listed).
- Resolved except one item: 13. The 503 and the re-enabled auth and CSRF flags are in. The 413 claim for the run action is wrong; see finding 1.
- Unchanged and non-blocking: 18 (no workflow file), carried as finding 4.

A. Wrong or unverifiable claims

1. CONFIRMED - Fail-Closed bullet "A POST body over 64 KiB: 413 from the app-wide body cap", the matrix "POST body over 64 KiB" row, and S-9.16 - The cap is not automatic. Werkzeug enforces MAX_CONTENT_LENGTH only when the handler reads the body. Contract Changes says the run action has "no request body the review depends on", and the CSRF hook reads a JSON request's body only through the header channel, so the hook never triggers the check. Tested on the installed Flask 3.1.0 / Werkzeug 3.1.3 with MAX_CONTENT_LENGTH=65536: a POST of 70,000 bytes to a handler that never reads the body returned 202 and the handler ran. The same request to a handler that calls `request.get_json(silent=True)` returned the 413 HTML page. So S-8.19 holds, because the approve handler reads the body (app.py:7936), but S-9.16 holds only if the run action reads the body or checks the content length itself. The rule and Fail-Closed must state that the run action refuses an oversized body itself; otherwise drop S-9.16. The in-code comment the spec relies on says the opposite of what executes. - app.py:450-454 "Werkzeug enforces MAX_CONTENT_LENGTH at the WSGI layer (returns 413 automatically)"; app.py:364-372 (the CSRF channel touches `request.form` only for form-encoded content types).

B. Criteria missing or contradicting an enforced contract

2. CONFIRMED - AC-8 rule ("a pair passing both gates in which it has the lower composite"), S-3.9 and S-4.6 - Under the revised AC-4 a not-yet-eligible symphony has no composite (S-4.6). The existing selection returns no candidate when either pair member has no composite. So an eligible symphony whose only redundant sibling is not yet eligible can never be a retire-candidate from that pair, whichever of the two performs worse. S-3.9 says the pair is "evaluated and shown" but not that it can never flag. The consequence of S-4.7 for the fallback symphony's sibling is likewise unstated. A test-writer could encode either reading of a money verdict. State the outcome in S-3.9 (and for a pair with a fallback-basis member) and in the rule. - advisors/retirement_recommender.py:263-264 "if score_a is None or score_b is None: return None"; :277-278 "if comp_a is None or comp_b is None: return None".

3. CONFIRMED - S-4.8 ("every composite carries the same window first and last dates and the same day count") against S-2.8 and S-2.10 - S-2.8 allows an eligible symphony's Composer series to start after its invested-since date, and S-2.10 drops days with absent values. Either can leave an eligible symphony with fewer than threshold-many points inside the common composite window. S-4.8 then cannot hold, and no scenario says what that symphony's composite shows: excluded from the population, or computed on fewer days. This missing family member decides the normalization population, and the population decides the candidate (advisors/retirement_recommender.py:215-218 "ranges[k] = (min(finite_vals), max(finite_vals))").

C. Template sections absent or empty without reason

4. QUESTION - User Tasks workflow line (carried from round 1, non-blocking per the checklist) - The repository has no `.claude/workflows/` directory, so the panel's operator workflow is unconfirmed. It would be settled by the operator confirming a workflow file for the AI Advisor Overview tab.

D. HOW content that belongs at Gate 2
none

E. Closed-scope breaches
none

F. Correct and easily doubted
- S-8.19 holds as written: the approve handler reads the body through `request.get_json(silent=True)` (app.py:7936), and Werkzeug's 413 is raised through `silent=True` rather than swallowed (tested: 413 HTML returned).
- The revised as-of rule matches the Worked Example: 2027-01-01 is an NYSE holiday, so a 03:45 tick that day has as-of 2026-12-31, the same as-of as the 17:00 on-demand run on 2026-12-31 (XNYS calendar, pandas-market-calendars ~=5.4.0).
- The reworded S-8.14 (redirect for a POST that is neither under /api/ nor XHR) matches app.py:185-190 and :236-237 and the panel client's headers (static/ai_advisor.js:1127-1130).

Refusal enumeration (changes from round 1 only; every other row stands as covered):
| Input | Write path entry | Refusal (code / status) | Enforced at | Spec covers (AC-N / Fail-Closed / unreachable: reason / MISSING) |
|---|---|---|---|---|
| any request, auth misconfigured | global auth hook | 503 / 302 | app.py:205-228 | S-1.23, S-9.15 |
| POST body > 64 KiB, approve or reject | Werkzeug cap when the body is read (app.py:7936) | 413 HTML | app.py:454 | S-8.19 |
| POST body > 64 KiB, run action | none unless the handler reads the body | none (the run starts) | not enforced; see finding 1 | MISSING: S-9.16 is required but no current path enforces it |
| Composer statistics, one account failing or an unreadable 200 | new read | run not completed | new | S-1.18, S-1.19 |
| Composer series, backtest or holdings returning an unreadable 200 | new read / `run_backtest` | "invalid JSON in 200 response" | advisors/composer_backtest_client.py:333-336 | S-2.14, S-5.11, S-7.8 |
| review row insert without a subject id | advisor_observations NOT NULL | IntegrityError | migrations/017_advisor_observations.sql:14-15 | Known-Unconfirmed (a Gate-2 decision for S-9.11) |
