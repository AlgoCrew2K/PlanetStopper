# Feature: Per-Symphony Optimization Loop Exception Isolation + Batch Visibility (BL-2)
Status: ready
Created: 2026-08-04
Source: `docs/audit/TWO-WEEK-REVIEW-2026-08-04.md` §4 Finding T2, §6 Backlog BL-2 (commit `ca7f2beb`)

## Summary
`run_autotuner()`'s per-symphony optimization loop
(`for normalized_name in symphony_names:`, `autotuner.py:2558-3290`) has NO
surrounding try/except. The loop body — Optuna `study.optimize` (`:2762`-ish),
CPCV-fold generation, PBO computation, the OOS adoption cascade, and
`database.save_autotune_run` (`:3218`) — is entirely unguarded. Only the two TAIL
advisor-producer calls are isolated: Overfitting Conscience (`:3259-3272`) and
Divergence Explainer (`:3276-3288`), each wrapped in its own local try/except that
logs and continues. An uncaught exception ANYWHERE in the unguarded portion of one
symphony's processing propagates out of the whole `for` loop and aborts
`run_autotuner()` for every symphony not yet reached — with the only observable
symptom being a missing `autotune_runs` row for the un-processed symphonies (no
`aborted` marker; the existing `DE-AUTOTUNE-REPORTING-001` graceful-abort contract
only covers abort conditions detected BEFORE the loop starts — e.g. history
shortfall, empty synthetic history, `<2` WFA days — and would report `{"aborted":
True, ...}` for a 0/11 batch, not the 7/11 partial-batch case this defect produces).

Observed live batch counts (re-verified from the audit's snapshot):
07-10 = 11/11, 07-18 = 11/11, **07-24 = 7/11**, 07-31 = 11/11. The 4 missing
symphonies on 07-24 (`corporate chaos 2060`, `corporate chaos 5 ways`,
`planet lqd … waltanansi`, `planet of the paragons`) all return normally on 07-31,
consistent with a transient failure rather than a permanently-broken symphony.

**Root-cause note (explicitly flagged, not blocking):** 2026-07-24 is also the
single night the nightly Market Prism council was skipped across all 4 roles — two
independent scheduled jobs degraded the same calendar day (~14h apart: autotuner
EOD ~21:48 UTC vs. council overnight ~07:06-07:14 UTC), suggestive of an
environmental/droplet-health event rather than two unrelated code bugs, though the
data alone cannot fully distinguish an uncaught code exception from an OS-level
kill. **INV-1 (pulling 2026-07-24 droplet logs — `journalctl`, OOM/restart records,
absence of the "finished all symphonies" print at `autotuner.py:3290`) is a
separate, operator-driven diagnostic action** that should ideally run BEFORE or
alongside this fix's deployment to correctly attribute the 07-24 incident — but per
the audit's own framing, **the isolation fix below is correct defense-in-depth
regardless of what INV-1 finds**, and is not blocked on it.

## Acceptance Criteria
- [ ] **AC-1 — per-symphony exception isolation.** The per-symphony loop body
      (`autotuner.py:2558-3290`, everything from `strat_data = database.get_symphony_strategy(...)`
      through the existing OC/DE tail calls) is wrapped so that an uncaught exception
      raised ANYWHERE within one symphony's processing is caught, logged (mirroring
      the existing OC/DE isolation pattern — `logging.error(..., exc_info=True)`,
      never a bare `print`), and the loop `continue`s to the NEXT symphony — never
      propagating out of `run_autotuner()`. The existing inner OC/DE try/excepts
      (`:3259-3272`/`:3276-3288`) are UNCHANGED (nested try/except is fine — they
      still catch their own local failures first; the new outer guard is a safety
      net for everything else: Optuna, CPCV/PBO, the OOS cascade,
      `save_autotune_run` itself).
- [ ] **AC-2 — attempted-vs-completed visibility.** `run_autotuner()`'s return value
      gains counts distinguishing symphonies ATTEMPTED (entered the loop iteration)
      from symphonies COMPLETED (reached the end of the iteration without an
      isolated exception) — e.g. `optimization_results["_batch_summary"] =
      {"attempted": N, "completed": M}` or an equivalent top-level structure (exact
      shape is an implementer decision, but it MUST be consumable by
      `reporting.py`'s EOD Discord builder per AC-3). A batch with zero exceptions
      has `attempted == completed == len(symphony_names)`.
- [ ] **AC-3 — partial-batch surfaced in the EOD Discord report.** `reporting.py`'s
      EOD Discord embed builder (the same function `DE-AUTOTUNE-REPORTING-001`
      extended for the aborted-vs-no-change distinction) renders a visibly different
      message when `attempted != completed` — e.g. "Optimization completed for M of
      N symphonies (K failed — see logs)" — distinct from BOTH the existing
      "aborted" embed (0 symphonies, pre-loop abort) and the normal "N symphonies
      optimized" embed (full batch, no failures). A partial batch must never render
      identically to a full, healthy batch.
- [ ] **AC-4 — no rollback of already-completed work.** A symphony's isolated
      exception does NOT retroactively affect or re-run symphonies already
      processed earlier in the SAME loop iteration — their `autotune_runs` rows
      (already persisted via `save_autotune_run` inside their own iteration) and
      their OC/DE observations remain exactly as written. This matches the audit's
      own characterization of the defect: siblings NOT YET processed are dropped;
      already-processed ones are unaffected today and must stay unaffected after
      this fix.
- [ ] **AC-5 — zero-exception regression guard.** When no symphony's processing
      raises, `run_autotuner()`'s return shape, every `autotune_runs` row written,
      and every OC/DE call are byte-identical to today's behavior — the new
      `attempted`/`completed` counters are purely additive.

## Architecture
- **`autotuner.py`** — wrap the body of the `for normalized_name in symphony_names:`
  loop (`:2558-3290`) in a try/except at the loop's top level. Increment an
  `attempted` counter at loop-body entry (before any work); increment `completed`
  at the natural end of a successful iteration (after the existing DE call at
  `:3288`, mirroring where the loop currently falls through to its next iteration
  today). On a caught exception: log via `logging.error(..., exc_info=True)` with a
  message naming the symphony (`normalized_name`) and the isolation boundary (mirror
  the wording style already used at `:3267-3272`/`:3283-3288` for consistency), then
  `continue`. Attach the final counts to the function's return value per AC-2.
- **`reporting.py`** — extend the EOD Discord embed builder (the same function
  `DE-AUTOTUNE-REPORTING-001`/F-015 touched for the aborted-vs-no-change and
  shape-guarded per-symphony-changes-loop fixes) with a THIRD branch: partial batch
  (`attempted != completed`), alongside the existing "aborted" and "normal" branches.
- **`alpha_bot_execution.py`** — the abort-discriminator consumer at
  `:1166-1192` (per `DE-AUTOTUNE-REPORTING-001`) is unaffected — this fix's new
  counters are additive fields on a SUCCESSFUL (non-aborted) `run_autotuner()`
  return, a structurally different case from the pre-loop `{"aborted": True, ...}`
  early-return this discriminator already handles.

## Edge Cases
- Exception in the LAST symphony of the batch (no further symphonies to continue
  to) — `attempted` increments, `completed` does not, loop ends normally, function
  returns with the partial-batch marker set correctly (no special-case needed —
  this falls out of the general per-iteration counter logic).
- Exception during `save_autotune_run` itself (a DB write failure mid-iteration) —
  now caught by the SAME outer guard (previously entirely unguarded too); the
  symphony's `current_params`/`locked_vars` in the in-memory optimization run are
  simply not persisted for this attempt — no partial/corrupt row is left behind
  (the INSERT either fully succeeds before the exception or doesn't run at all).
- ALL symphonies fail — `attempted == N`, `completed == 0`; the EOD report renders
  the partial-batch (in this case, zero-of-N) message honestly; no crash.
- A symphony whose exception is itself non-deterministic/transient (e.g. the
  07-24 environmental-event hypothesis) — this fix does not attempt retry logic;
  a failed symphony is simply skipped for THIS run and picked up again on the NEXT
  weekly run — retry-within-run is explicitly out of scope (see Scope Boundaries).

## Security Considerations
- No new input surface — this is control-flow hardening around already-internal
  per-symphony processing. Logged exception messages follow the existing D-1-style
  convention used elsewhere in this file (symphony name + exception type/message via
  `logging.error(..., exc_info=True)`) — no new secret/credential exposure risk
  beyond what the existing OC/DE isolation blocks already log.

## Testing Strategy
- New test in `tests/autotuner/` — simulate one symphony's `study.optimize` (or
  another point inside the loop body) raising an exception in a multi-symphony
  batch fixture; assert: (a) the exception is caught and logged, not propagated;
  (b) the loop continues — the NEXT symphony's `autotune_runs` row IS persisted;
  (c) `attempted`/`completed` counts are correct (N attempted, N-1 completed); (d)
  already-processed prior symphonies' rows are unaffected.
- Regression test: the existing zero-exception `run_autotuner()` integration tests
  (grep `tests/autotuner/` for current full-batch tests) stay green with the new
  counters present and correctly reflecting a full/clean batch (AC-5).
- `reporting.py` test (extend the existing EOD-embed test suite touched by
  `DE-AUTOTUNE-REPORTING-001`/F-015 — locate via the `send_eod_discord_post` test
  file) — AC-3: assert the partial-batch embed is DISTINCT from both the
  "aborted" embed and the normal "N optimized" embed.
- Consumer-suite discovery (house lesson): grep the whole tree for existing tests
  that assert `run_autotuner()`'s exact return-dict SHAPE (keys present/absent) —
  a test hardcoding "only symphony-keyed entries, no `_batch_summary` key" would
  need updating alongside AC-2, not left as a stale conflicting assertion.
- N-run flake check (house lesson,
  `feedback_n_run_flake_check_before_blaming_cycle`): the new exception-injection
  test must be deterministic (mock the exact raise point), not timing-dependent.
- Both ruff gates green; full PR gate (CI, `/review`, PM's LIVE functional gate —
  verify a real or simulated partial-batch scenario renders the new Discord embed
  distinctly on a test/staging path before the next live weekly autotune run).

## Decisions
| Decision | Rationale |
|----------|-----------|
| Isolation fix ships independently of INV-1's root-cause finding | The audit explicitly frames the isolation fix as "correct defense-in-depth regardless" of whether 07-24's trigger was a code exception or an OS-level kill — waiting on droplet-log access would block a fix that is justified either way. |
| No in-run retry for a failed symphony | The audit's evidence doesn't establish retry would help (a transient droplet-health event would likely fail a retry too within the same run); the existing weekly cadence already re-attempts naturally next week. Adding retry logic is unrequested scope. |
| Attempted/completed counters, not a per-symphony status list | Keeps the fix minimal per the audit's own framing ("surface an attempted-vs-completed count") — a full per-symphony status list is a plausible future enhancement but not what BL-2 asks for. |

## Scope Boundaries
- **IN:** try/except isolation around the per-symphony loop body; attempted/completed
  counters on `run_autotuner()`'s return; a distinct partial-batch EOD Discord
  embed branch; regression + new isolation tests.
- **OUT:** INV-1 (pulling 2026-07-24 droplet logs to root-cause the specific
  incident) — a separate operator-driven diagnostic action, not a code change,
  tracked independently; INV-2 (T4's zero-triad mechanism disambiguation) — an
  unrelated open investigation; any change to Optuna trial logic, CPCV/PBO
  computation, or the OOS adoption cascade math; in-run retry logic for a failed
  symphony; any change to the pre-loop graceful-abort paths `DE-AUTOTUNE-REPORTING-001`
  already covers.
