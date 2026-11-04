# Source: security-and-controls

The operator delegated every decision to the PM ("no decisions are to be put to them"). The PM's rulings of 2026-11-04T15:05Z, as recorded verbatim in the operator report draft, "Calls I made":

4. **Cash Now (dead button):** fix it so it sends what `/api/sell_account` needs, with a security review. The droplet is in dry run, so it places no real orders until you turn live execution on.
6. **On-demand tenure review has no market gate:** add the same gate Retirement "Run now" has (refused while the market is open, because Composer's rate limit is shared with the engine).
9. **Public `/health` detail (S-9):** off-box callers get only status, start time and last cycle; the full detail stays available to checks run on the droplet itself, which the open proofs rely on.
10. **Logout (S-10, S-11):** sessions get a lifetime and logout revokes them; logout becomes a POST.
11. **Explainer prompt (S-17):** add the sentence telling the model that symphony names in its input are data, not instructions.

The defects as first reported (same report, 2026-11-02 section, verbatim):

1. **Cash Now is dead.** `static/index.js` posts only `symphony_id`; `/api/sell_account` answers 400. Predates this work; left alone because it is a trade control.
3. **On-demand tenure review has no market gate**, unlike Retirement Run now.

Order of work (verbatim): Order of work: hygiene/dead-code (with item 7 and the worktrees), then security (9-11), then the controls (4, 6), then the engine (5), then UI (1, 12), then replay fidelity, then the provenance wire (15).

Security audit of build 38b0cbe8 (working SHA 38b0cbe8007ec7442cfb127114a8ff58d79b2a24, base 37c8e983), findings verbatim:

### S-9 LOW. Unauthenticated `/health` now says more

- **Status:** CONFIRMED, changed in this build.
- **Where:** `app.py:469-489` and `:1325`. Base returned `status`, `daemon_started_at`, `last_successful_cycle_at`. It now adds `degraded_reasons`, `scheduler_last_tick_at`, `engine_running_since`, and `last_cycle{cycle_id, outcome, completed_at}`.
- **Impact:** anonymous visitors can see when the engine is running and whether it is failing. No account, holding or money value is exposed.
- **Remediation:** keep `/health` to `status` plus the two base fields. `/api/header-status` is already authenticated and carries the same fields.

### S-10 LOW. Logout does not revoke a session

- **Status:** CONFIRMED by probe: a cookie captured before `/logout` returned 200 on `/api/accounts` afterwards. Pre-existing.
- **Why:** the session is a client-side signed cookie with no server state or lifetime. `/api/events` also checks auth at connect time only.
- **Remediation:** add `PERMANENT_SESSION_LIFETIME` and a server-side session generation value checked in `_auth_before_request`.

### S-11 LOW. `/logout` is a GET (`app.py:390`)

Any top-level link logs the operator out. Move it to POST.

### S-17 LOW. Display names reach the explainer prompt with no data-versus-instruction boundary

- **Status:** CONFIRMED by reading, low impact.
- **Where:** `advisors/retirement_explainer.py:62-100`, called from `app.py:1913`. The tenure review has no separate explainer: its recommendations go through this one. The JSON includes `candidate_name` and `sibling_name`, which are Composer-authored. The system prompt has no clause telling the model to treat that JSON as data.
- **Bound on impact:** the output is stored text, autoescaped at `templates/ai_advisor.html:2012`. The approve and reject routes are status-only, and the checklist is deterministic. **No LLM output drives an action here.** A crafted name could only produce misleading card text.
- **Remediation:** add one sentence to the system prompt saying text inside the JSON is data and never instructions.

