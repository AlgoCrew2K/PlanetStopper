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
**[SHIPPED 2026-07-21 -- `DE-OPS-CLUSTER-001`, `fix-ops-cluster` cycle. See DECISIONS.md.]**
`ai_advisor_suggest()` (`app.py:5796`) resolves the client's raw `symphony_id` to a canonical
normalized name for everything else, but passes it straight through unresolved as
`composer_symphony_id` -- which `assemble_advisor_context`'s Composer `/score` call
(`ai_advisor.py:1601-1604`) requires to be a HASH, not a name; a name silently degrades that
request's condensed-logic context to empty (D-1, never crashes). Pre-existing (predates F-023),
out of scope for that cycle. Fix candidate: reuse `resolved_id`'s existing hash-match loop.
Tier 1.

### Test-infra hardening: `test_live_*.py` needs a second opt-in gate beyond the `live` marker (found 2026-07-20, F-013 doc-verification incident)
`tests/*/test_live_*.py` files rely solely on `pytestmark = pytest.mark.live` + pyproject.toml's
default `-m 'not live and not slow and not perf'` addopts filter to stay excluded from normal
runs. That `-m` filter is a single point of failure: any invocation that overrides addopts
(e.g. `pytest ... -o addopts=""`, attempted as a workaround for an unrelated xdist issue) silently
strips it, and if `ANTHROPIC_API_KEY` (or the equivalent live credential) is present in the
environment, the test's own self-skip guard does not fire either — the live test runs for real.
Confirmed 2026-07-20: an `-o addopts=""` invocation during F-013 doc-verification caused one
unsanctioned real Anthropic API call via `tests/ai_advisor/test_live_claude_advisor.py`'s
module-scoped fixture (advisor-context content, no trade action, cost trivial; disclosed and
accepted as a low-impact process incident, DE-ADVISOR-GATE3-DIRECTION-001 doc-cycle). Fix
candidate: require an explicit opt-in env var (e.g. `ALPHABOT_RUN_LIVE_TESTS=1`) in addition to
the `live` marker, so a stripped `-m` filter alone can never let a live test execute. Tier 1.

---

## Low priority / tracked follow-on

### Performance-tab double "+" glyph on pp-kind delta rows -- ACCEPTED-COSMETIC (F-024, register LOW, closed out 2026-07-21, `fix-ops-cluster`/`DE-OPS-CLUSTER-001`)
`performance.js:77` and `:90` both self-sign their pp-kind delta rows (prepend `'+'` for a
non-negative value) on top of an already-signed formatted string, producing a doubled glyph
(e.g. "↑ ++0.64pp"). The underlying VALUE is correct -- only the glyph repeats. Explicitly kept
OUT of scope by two prior cycles (`fix-f023-perf-view.md`, `fix-display-cluster.md`) and again by
this FINAL confidence-program cycle's plan (`feature-plans/fix-ops-cluster.md` Scope Boundaries:
"F-024 glyph (stays deferred cosmetic -- record as ACCEPTED-COSMETIC in the close-out)"). This is
that close-out record: the finding is real, LOW severity, cosmetic-only, and deliberately not
fixed across three consecutive cycles that all touched adjacent code (`fix-f023-perf-view`,
`fix-display-cluster`, `fix-ops-cluster`) -- a standing decision, not an oversight. Trivial one-line fix (drop the redundant `'+'` prepend) if ever prioritized.

### F-1 frozen-branch connection close isn't `try/finally`-wrapped -- LOW, accepted residual (found + accepted 2026-07-21, `fix-ops-cluster`/`DE-OPS-CLUSTER-001`)
`get_state()`'s `closed_frozen`/`pre_market` branch (`app.py`, F-1 frozen-branch fix) opens a
shared `_frozen_shadow_conn` and closes it with a plain statement after the per-symphony-loop-
through-portfolio-calls span converges, NOT a `try/finally` wrapping that whole span (unlike the
live branch's fix, which does use `try/finally`). An exception raised in the per-symphony loop
itself, outside the narrow `(KeyError, TypeError, ValueError)` catch already there (e.g. a
non-numeric `current_return` TypeErroring on `/100.0`), would propagate past the `close()` and
leak the connection. Both foc-tw and foc-rev independently traced this during review and agreed:
LOW severity (a read-only SQLite connection with no pending transaction, GC-recovered, no
data-integrity risk; requires already-malformed snapshot data that would 500 the route
regardless). Not fixed inline given cycle-velocity pressure and the severity gap. Optional cheap
follow-up available (~4 lines: harden the two numeric coercions with try/except, closing both the
leak AND the underlying pre-existing TypeError risk) if ever prioritized -- not blocking, PM's
call. See `DE-OPS-CLUSTER-001` in `DECISIONS.md` for the full trace.

### `database._shadow_cr_cache` needs a column discriminator before a second column-selecting accessor can share it -- LOW, accepted residual (found 2026-07-23, guard-alpha-preconditions cycle)
`database._shadow_cr_cache` (`database.py:2967`) is keyed `(symphony_id, today, db_file,
resolved_epoch)` regardless of WHICH `shadow_history` column's series is cached under that key.
`analytics._get_shadow_cumulative_trajectory` (`analytics.py:612-706`) already writes
`shadow_return` series under that exact key shape. This cycle's new
`analytics.get_shadow_current_return_daily_series` (guard-alpha-preconditions feature, commit
`327cd6d2`) selects `current_return` instead of `shadow_return` for the same symphony -- reusing
the shared cache under the identical key shape would risk one accessor silently serving the
other's cached series for the same symphony/day/epoch (a cross-column collision, not merely a
stale-value bug). Interim mitigation, already shipped: the new accessor deliberately does NOT
cache at all (rationale documented in-source at `analytics.py:723-730`) -- correct but
suboptimal, a perf/DB-load tradeoff, not a correctness bug. Fix candidate: add a column
discriminator to the cache key shape (or split into a separate cache namespace per column) so
both accessors can safely share the cache. Source: ga-impl, guard-alpha-preconditions cycle,
2026-07-23.

Separately (same variable, independently findable, not introduced or fixed this cycle):
the declared type hint has drifted from actual usage. `database.py:2967` declares
`_shadow_cr_cache: dict[tuple[str, str], float] = {}` with a comment describing a
`(symphony_id, trading_day) -> cumulative shadow return` shape, but the real production
consumer (`analytics._get_shadow_cumulative_trajectory`, `analytics.py:659-665`) keys it
with a 4-tuple `(symphony_id, today, db_file, resolved_epoch)` and stores a `list[float]`,
not a bare `float`. Fix candidate: correct the declared type hint (and the stale comment)
to match actual usage in the same pass as the column-discriminator fix above, since both
touch the same declaration line.

### `tests/test_scope_guard_f7.py::test_math_engine_not_in_diff` was a permanent tripwire, not an F7-scoped guard -- FIXED this cycle (found + fixed 2026-07-24, exit-friction-realized-savings cycle, ga2-tw)
**[FIXED `bb731525`, same cycle.]** Kept as the record of the defect and its fix. The test
enforced "`math_engine.py` has zero diff since the F7 RED anchor commit" -- correct and useful
DURING the F7 cycle (`feature-plans/math-f7.md` AC-5: `math_engine.py` out of scope for that
cycle's display/diagnostic-only fix), but the anchor was git-derived from a FIXED historical
commit (`git log --follow` on `tests/execution/test_f7_ac1_persist_guard.py`, resolving to
`7752bb00`) with an unbounded anchor-to-CURRENT-HEAD diff window -- it never stopped enforcing
"F7 scope" once F7 itself shipped. Any LATER cycle touching `math_engine.py` (`6f38b86e` MA-11,
`43a458f8` MA-4, both already-shipped math-remediation cycles) tripped it forever afterward,
for every subsequent branch, regardless of relevance to F7 -- reproduced and independently
re-verified on this branch (anchor SHA, both offending commits, their ancestry to the fork
point `ccda9abe`, clean working tree, and the live test failure).

**Second, independent root cause found while fixing it:** CI had been passing this test
VACUOUSLY, not correctly. `.github/workflows/tests.yml`'s `actions/checkout@v4` has no
`fetch-depth` override (shallow, depth 1 by default), which breaks `git log --follow`'s
ability to walk back past the shallow boundary -- in that shallow clone the "anchor" silently
resolved to the shallow tip commit itself, collapsing the diff-since-anchor to a commit diffed
against itself (always empty). CI was never actually exercising the assertion.

**Fix:** F7 is a shipped, closed cycle (PR #99, merged `bd2c8d5d`) -- its scope claim is a
fixed historical fact, not a live-forever invariant. Rebound the diff to F7's own two fixed
endpoints (`7752bb00..bd2c8d5d`) instead of `<anchor>..HEAD` -- permanently correct (verified
empty diff on `math_engine.py` in that exact range) and shallow-clone-safe (a shallow clone
missing those specific commits makes the diff command itself fail loudly, rc=128, routing to
the existing skip path, rather than silently resolving to a wrong anchor). Verified GREEN: 2/2
in the file.

**Sibling `tests/test_scope_guard.py` (DE-EOD-BASIS-001) has the IDENTICAL design flaw and is
CURRENTLY FAILING right now -- NOT fixed by `bb731525` (different AC/cycle, out of this
cycle's scope). Tracked as its own entry immediately below.**

### `tests/test_scope_guard.py` (DE-EOD-BASIS-001) has the same permanent-tripwire design as the F7 sibling above -- CURRENTLY FAILING, tracked (found 2026-07-24, exit-friction-realized-savings cycle, ga2-tw flagged the design match; independently run + fully re-verified by this doc-writer, not taken on report alone -- ga2-tw explicitly had not run it)
Structurally identical to the F7 scope guard fixed above: dynamically resolves its anchor via
`git log --follow -- tests/dashboard/test_eod_account_basis.py` (resolves to `848acf94`, the
DE-EOD-BASIS-001 PR #89 commit), then diffs `<anchor>..HEAD` forever, checking two forbidden
files (`alpha_bot_execution.py`, `math_engine.py`). Both defects independently confirmed:

1. **Already tripped, not merely at future risk.** `pytest tests/test_scope_guard.py -n0`
   FAILS 2/2 (`test_alpha_bot_execution_not_in_diff` AND `test_math_engine_not_in_diff`) on the
   current tree. `git log 848acf94..HEAD -- alpha_bot_execution.py` shows 5 offending commits
   (`0c5d3e86` Managed Sleeves, `6f38b86e` MA-11, `43a458f8` MA-4, `ed194259` F7 AC-1/AC-4,
   `ba331a30` non-finite persistence policy); `git log 848acf94..HEAD -- math_engine.py` shows
   2 (`6f38b86e`, `43a458f8` -- same pair as the F7 sibling). `git merge-base --is-ancestor`
   confirms ALL FIVE are ancestors of this branch's fork point (`ccda9abe`) -- pre-existing,
   not introduced by exit-friction-realized-savings. `git status --short alpha_bot_execution.py
   math_engine.py` is clean on the current working tree.
2. **Same CI shallow-clone masking risk.** `.github/workflows/tests.yml`'s
   `actions/checkout@v4` has no `fetch-depth` override -- verified directly (no `fetch-depth`
   key anywhere in the checkout step) -- the identical condition that made the F7 sibling pass
   vacuously in CI rather than genuinely.

**Fix-pattern nuance vs. the F7 sibling:** DE-EOD-BASIS-001's PR #89 was squash-merged into a
SINGLE commit (`848acf94`) containing both the RED tests and the GREEN implementation --
unlike F7, which had a separate RED-anchor commit (`7752bb00`) and a later, distinct merge
commit (`bd2c8d5d`) to rebind between. There is no two-endpoint "cycle range" to rebind to
here; a fix would need either (a) retire the test now that DE-EOD-BASIS-001 (2026-07-02) is
long shipped, or (b) rebind to `848acf94^..848acf94` (that single commit's own diff against
its parent) if the "these files stay frozen" intent still matters -- a narrower, single-commit
variant of the F7 fix, not a direct copy.

Not fixed in this pass -- flagged per house rule (no pre-existing failures carried silently).
PM should decide whether this gets the same immediate-fix treatment `bb731525` gave the F7
sibling (it is failing on every full-suite run right now, same as F7 was) or stays
BACKLOG-tracked for a dedicated remediation pass.

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
time, or move the seed fixture's `database` import inside the fixture function so it is
no longer module-scope. Tier 1.

**Independently reproduced a second time** (found 2026-07-24, exit-friction-realized-savings
cycle, ga2-tw): identical RuntimeError, identical trigger condition, this time on an
already-shipped, presumably-GREEN file (`tests/database/test_029_exit_triggers_also_true.py`)
— reproduced BEFORE the cycle's own new `tests/database/test_exit_turnover_stats.py` was ever
touched, ruling out a cycle-introduced regression. Confirms this is a recurring footgun for
anyone invoking pytest against `tests/database/` files directly as a CLI target, not a one-off
from the original 2026-07-07 report. Same workaround applies (`export DB_PATH=<any writable
temp path>`); not needed for full-suite runs via `testpaths`/no-args or `/run-tests`.

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
