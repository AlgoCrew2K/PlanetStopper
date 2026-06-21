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

### `tech-debt-cleanups.completed.md` — C3b + C3c
C3a is a confirmed no-op (stash empty). C3c shipped (chore/tech-debt-c3bc). Remaining:
- **C3b:** formal route self-skip closure — write the route-level RED test for the
  302-redirect behaviour (AC-4b/AC-5b never written).
Small; Tier 1.

### `security-review.md` — S-2 + DEP-1 (buildable now, independent of S-1)
- **S-2:** commit a `.env.example` template (no real credentials). **[DONE this sweep — `.env.example` committed]**
- **DEP-1:** tighten `anthropic~=0.85.0` and `feedparser>=6.0` to exact `==` pins in
  `requirements.txt` / `pyproject.toml`.

---

## Low priority / tracked follow-on

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
