# Planet Stopper — Open Backlog
**As of:** 2026-06-21
**Source of truth for shipped/obsolete history:** `.claude/backlog-reconciliation.md`

This file lists only work that is genuinely open. Plans renamed to `.completed.md` or `.obsolete.md`
are archived in this same directory; see the reconciliation doc for the full classification record.

---

## Blocked on operator action

### `security-review.md` — S-1 (operator action required)
S-1: Discord webhook credential has been in git history since `c0ec631`. Operator must
rotate the webhook URL and explicitly approve the force-push scrub needed to remove it
from history. No code change until operator provides the new credential + go-ahead.

---

## Ready to build now

### `autotune_runs.pbo` never persisted — small defect (found 2026-07-07, culling-engine recon)
The production autotune call site (`autotuner.py:2826-2844`) does not pass `pbo=` to
`database.save_autotune_run`, so the computed `_pbo_value` (used for the in-run veto) is
never written — `autotune_runs.pbo` (migration 028) is `None` on every real row. Also no
dashboard/context path surfaces the numeric PBO anywhere. Fix: thread `_pbo_value` into the
save call; optionally surface it in `_build_optuna_section`. Tier 1; independent of sleeves.

### `tech-debt-cleanups.completed.md` — C3b + C3c
C3a is a confirmed no-op (stash empty). C3c shipped (chore/tech-debt-c3bc). Remaining:
- **C3b:** formal route self-skip closure — write the route-level RED test for the
  302-redirect behaviour (AC-4b/AC-5b never written).
Small; Tier 1.

### `security-review.md` — S-2 + DEP-1 (buildable now, independent of S-1)
- **S-2:** commit a `.env.example` template (no real credentials). **[DONE this sweep — `.env.example` committed]**
- **DEP-1:** tighten `anthropic~=0.85.0` and `feedparser>=6.0` to exact `==` pins in
  `requirements.txt` / `pyproject.toml`.

### `POST /ai-advisor/suggest` does not hash-resolve `composer_symphony_id` (found 2026-07-20, F-023 doc-audit)
`ai_advisor_suggest()` (`app.py:5796`) resolves the client's raw `symphony_id` to a canonical
normalized name for everything else, but passes it straight through unresolved as
`composer_symphony_id` -- which `assemble_advisor_context`'s Composer `/score` call
(`ai_advisor.py:1601-1604`) requires to be a HASH, not a name; a name silently degrades that
request's condensed-logic context to empty (D-1, never crashes). Pre-existing (predates F-023),
out of scope for that cycle. Fix candidate: reuse `resolved_id`'s existing hash-match loop.
Tier 1.

---

## Low priority / tracked follow-on

### Sleeves: mis-citing float-imprecision example in the price-rounding docstring — COSMETIC (found 2026-07-08, P3 smoke cycle)
The bracket price-rounding (`_round_to_equity_tick`, sleeves/alpaca_orders.py, task #35) cites
`495.00 / 0.01 == 49499.999999999993` as motivation, but that expression is exactly `49500.0` in
Python — the example doesn't reproduce. The Decimal-based decision is CORRECT (naive
`floor(price*100)/100` genuinely misrounds e.g. $0.29→$0.28); only the illustrative citation is
wrong. Now mirrored in 3 places (the source docstring, DECISIONS.md DE-SLEEVES-P3-001, docs/generated/sleeves.md).
Trivial one-line fix — swap in a real reproducing example. Not fixed inline to avoid re-gating a comment typo.

### tests/database/conftest.py init_db-before-guard footgun (found 2026-07-07, sleeves P1 cycle) — LOW
Bare top-level `import database` in tests/database/conftest.py triggers database.py's
module-level init_db() BEFORE tests/conftest.py's pytest_configure() DB_PATH guard fires,
when tests/database is passed as an explicit pytest CLI target (bare `tests` root, as CI
uses, is unaffected). Pre-existing on stock HEAD (confirmed via git stash by sleeve-db).
Workaround: pre-set DB_PATH in the shell env. Fix candidate: defer init_db out of import
time or make the database conftest set DB_PATH itself. Tier 1.

### Fundamentals lens `sources[].url` hardcodes `type=10-K` query param — COSMETIC (found 2026-07-13, advisor-suite live re-verify)
The AAPL fundamentals payload correctly selects the latest 10-Q (`end=2026-03-28`, `filed=2026-05-01`,
`form=10-Q` — DE-ADVISOR-SUITE-FIX-001 AC-4, proven live), and `sources[].title` reads
"Apple Inc. 10-Q (2026-05-01)", but the EDGAR browse URL still hardcodes `&type=10-K` in the query
string. Data + title are correct; only the source deep-link's form filter is wrong. Trivial fix —
thread the selected form into the URL builder (`ai_advisor.py` fundamentals section). Advisory-only.

### Standardize AI-Advisor slide-in panels on the transform pattern — DEFERRED FOLLOW-UP (found 2026-07-13, AC-3b saga)
The right-based chat/detail panel (`right:-440px` → `chat-panel--open`) has a latent paint
fragility that produced a stale-headless-browser artifact during AC-3b (the panel *does* open
instantly in a fresh browser — verified — so this was NOT a shipped bug and the transform fix was
reverted net-zero). Consider deliberately standardizing all AI-Advisor slide-panels on the proven
`#detail-panel` transform-translateX pattern as a hygiene follow-up, to eliminate the class of
paint fragility. Deferred, not a defect. See DECISIONS.md DE-ADVISOR-SUITE-FIX-001 AC-3b.

### `test_api_history` cross-test isolation gap — LOW (found 2026-07-13, advisor-suite gate)
`tests/.../test_api_history` reads the absolute `_POST_MORTEMS_DIR` (module-level absolute path)
which defeats `monkeypatch.chdir` — under a nested-path pytest invocation it reads the worktree's
real `post_mortems/` instead of an isolated temp dir, so it passes/fails depending on ambient
fixtures. Deterministic pre-existing isolation gap, NOT a production defect (the app path is
correct). Fix: make the post-mortems dir resolution test-overridable (env var or fixture) so the
test can point it at a temp dir. Deselected in bounded PM gate runs.

### Per-module test footprint (`tests/advisors/` single-process) — LOW PRIORITY
**Discovered:** 2026-06-21 (reload-leak remediation diagnostic). **Priority:** LOW — gates
nothing; xdist (CI/real test mode) bounds it per-worker (~270 MB). NOT a production/daemon leak.

**Symptom:** `pytest tests/advisors/ -p no:xdist` accumulates RSS cumulatively across test
files (the process never releases between modules). Clean serialized peak after reload removal
(SHA 470de98): **~6.9 GB**.

**Dominant growers** (per-file RSS diagnostic, `.claude/_perfile_diag.txt` on branch
`fix/test-reload-leak`):

| File | delta_GB |
|------|----------|
| `test_builder_scheduler.py` | +1.49 GB |
| `test_symphony_schema.py` | +0.69 GB |
| `test_community_strats_timeout.py` | +0.54 GB |
| `test_strategy_builder_engine.py` | +0.30 GB |

**Hypothesis:** heavy-object retention from quantstats/pandas/Optuna/anthropic imports + per-test
object footprint. Unclear whether the root cause is fixture-scope/accumulator patterns or simply
the cost of repeated heavy-import initialization — needs a targeted per-file diagnosis before
any fix.

**Classification:** single-process-ONLY. xdist shards across workers and bounds per-worker
footprint to ~270 MB. The strategy-builder scheduler runs as fresh weekly subprocesses in
production — no accumulation occurs in the daemon or live path.

**If pursued:** separate RED cycle — diagnose fixture-scope/accumulator vs heavy-imports per top
file, then targeted test-infra fix (e.g. session-scoped fixtures, gc.collect teardowns, or
test-file splitting). Do NOT address by weakening assertions or removing coverage — this is an
infrastructure concern, not a test correctness problem.

---

### `docs/generated/app.md` Strategy Builder route section -- stale `app.py:38xx` line citations -- COSMETIC (found 2026-07-13, advisor-outage-degrade doc pass)
The `POST /ai-advisor/strategy-builder/run` section of `docs/generated/app.md` cites several
`app.py:38xx` line numbers (e.g. `app.py:3759`, `:3800`, `:3807`, `:3813`, `:3826`, `:3840`,
`:3852-3879`) that were accurate when R1 wrote them but have drifted -- several routes
(candidate-alert, 2026-07-12) were added to `app.py` since, and the route decorator now lives
at `app.py:4739`. Flagged inline in the doc (a footnote noting the drift + the current line)
rather than corrected line-by-line, since a full citation sweep of this section is its own
small task, not scoped to the advisor-outage-degrade cycle that surfaced it. Fix candidate:
re-derive every `app.py:N` citation in this section (and audit the rest of `app.md` for the
same drift pattern -- this section is unlikely to be the only one). Tier 1; cosmetic, no
functional impact -- the doc content itself is accurate, only the line pointers are stale.

---

## Deployment follow-on

### Droplet wipe-and-collect
Full E2E validation of the production droplet: confirm daemon healthy, council timer
firing at 03:00, no two-daemon conflict, MARKET_PRISM rows arriving nightly, Overview
tab rendering council output. Requires operator access to the droplet.

---

## Shipped this cycle (2026-06-21)

### `test-reload-leak-remediation.md` — `importlib.reload` removal (commit 470de98)
All 37 per-test `importlib.reload` calls removed from `tests/advisors/` (35 in
`test_community_strats.py`, 1 each in `test_atlas_cache.py` and
`test_community_strats_timeout.py`). Replaced with module-attribute patching and env-var-only
isolation; per-file AST anti-recurrence guard added. Behavior-preserving: 722 passed / 4
skipped. NOTE: The original hypothesis (reloads = dominant OOM driver) was falsified — the
reloads contributed only ~1.1 GB of the ~8 GB single-process peak. The real driver is
cumulative heavy-lib footprint (quantstats/Optuna/anthropic), which is bounded under xdist (CI
mode). Single-process full-tree peak reduced from 8.1 GB to 6.9 GB. Residual tracked above
as LOW PRIORITY follow-on.
