# Tracked Debt — `importlib.reload`-per-test memory-leak remediation

**Status:** shipped (commit 470de98, branch fix/test-reload-leak)
**Type:** pre-existing test-infrastructure debt
**Discovered:** 2026-06-21, by the Strategy-Builder-Real (C5) single-process full-tree gate
**Classification:** PRE-EXISTING C1-era test-debt (git-proven — see "Provenance" below). NOT a C5 defect.
**Shipped:** 2026-06-21

---

## Summary

Several `tests/advisors/` test files called `importlib.reload(...)` once (or many times) **per
test** to "pick up a new env var" or "re-bind to a patched dependency." The initial hypothesis
was that these reloads were the dominant memory driver causing single-process full-tree
verification to OOM — but this was falsified by controlled measurement (see "Red Herring"
below). The reloads were nonetheless dead weight and were removed: behavior-preserving
refactor, patch-visibility maintained via module-attribute patching and env-var-only isolation.

## What shipped (commit 470de98)

- All 37 per-test `importlib.reload(...)` calls removed from 3 `tests/advisors/` files:
  - `test_community_strats.py` — 35 sites
  - `test_community_strats_timeout.py` — 1 site
  - `test_atlas_cache.py` — 1 site
- Replacement strategy (per site, per access-pattern analysis — see below):
  - Module-attribute patching (`patch("advisors.community_strats.cached_pull")`) where
    `community_strats` accesses the symbol via module attribute at call time
  - Env-var-only isolation (`monkeypatch.setenv` + `tmp_path`) where the reload existed
    only to pick up a new `ATLAS_CACHE_DB_PATH`
- Per-file AST anti-recurrence guard added to each file:
  `test_no_importlib_reload_in_this_test_module` — enforces zero future reloads per file
- **Behavior-preserving:** 722 passed / 4 skipped on full `tests/advisors/` `-n0` run
  (+3 tests = the new AST guards). No production code changed.

## Why the fix is correct (access-pattern analysis)

`community_strats.py:25` does `from advisors import atlas_cache` (imports the MODULE OBJECT,
not a name from it). Line 195 calls `atlas_cache.cached_pull(...)` — a call-time module-attribute
lookup. This means `patch("advisors.atlas_cache.cached_pull")` patches the attribute on the live
module object, which `community_strats` will see at call time WITHOUT any reload. The reload was
adding no patch-visibility — it was dead weight.

Similarly: `pymongo` is lazy-imported inside the fetch closure; `ThreadPoolExecutor` is patched
at call time. `atlas_cache` resolves `ATLAS_CACHE_DB_PATH` from `os.environ` at call time, so
`monkeypatch.setenv` + `tmp_path` fully isolates without any module reload.

## The red herring — original hypothesis falsified (document honestly)

The plan's central hypothesis was: "each `importlib.reload` orphans a module graph → unbounded
balloon → ~14 GB single-process peak." This was **falsified by controlled before/after
measurement:**

| Condition | Peak RSS |
|-----------|----------|
| BEFORE (37 reloads present) | **8.067 GB** (`.claude/_before_clean.txt`) |
| AFTER (reloads removed) | **6.932 GB** (`.claude/_after_clean.txt`) |
| **Reduction** | **~1.1 GB** |

The reloads contributed ~1.1 GB, NOT the originally hypothesized multi-GB balloon. The fix is
correct and worth keeping (dead weight removed, patch-visibility explicitly verified, AST guard
added) — but it does NOT bring single-process `tests/advisors/` into a sub-1 GB footprint. The
dominant driver is elsewhere.

**The earlier 14.3 / 13.5 GB readings** that prompted the original OOM diagnosis were
**measurement contamination** — concurrent overlapping `pytest` processes running at the same
time inflated the apparent single-process peak. The true isolated single-process peak was always
~8 GB before the fix.

## The real driver — cumulative heavy-lib footprint

Per-file RSS diagnostic (`.claude/_perfile_diag.txt`, run on the fixed branch, 726 tests, final RSS 4.10 GB):

| File | delta_GB | Notes |
|------|----------|-------|
| `test_builder_scheduler.py` | **+1.49 GB** | Dominant grower — imports quantstats/Optuna/anthropic |
| `test_symphony_schema.py` | +0.69 GB | |
| `test_community_strats_timeout.py` | +0.54 GB | |
| `test_strategy_builder_engine.py` | +0.30 GB | |
| All others | < 0.22 GB each | |

This is **cumulative heavy-library / feature-test object footprint** (quantstats, pandas, Optuna,
anthropic SDK) that accumulates across test files in a single process. It is NOT module-orphaning
from reloads.

**Why this is single-process-ONLY:** xdist (the CI and real-world test mode) bounds it per-worker
(~270 MB per worker). This is not a production or daemon leak — the strategy-builder scheduler
runs as fresh weekly subprocesses in prod, so no accumulation occurs.

## AC status

| AC | Description | Status |
|----|-------------|--------|
| AC-1 | Zero `importlib.reload(...)` in `tests/advisors/`; AST guard per file | **DONE** |
| AC-2 | All affected files GREEN with same assertions; behavior-preserving | **DONE** — 722 passed / 4 skipped |
| AC-3 | `tests/advisors/` `-p no:xdist` completes bounded (sub-GB peak) | **NOT ACHIEVED** — residual 6.9 GB peak is multi-cause heavy-lib footprint, not module-orphaning. xdist-bounded in CI (~270 MB/worker); not a prod leak. Tracked LOW PRIORITY as a separate concern. |

## What AC-3 not achieved means in practice

- CI (`pytest -n auto`, xdist) is UNAFFECTED — always bounded per-worker.
- The daemon/production path is UNAFFECTED — no accumulation.
- Single-process full-tree verification still requires xdist exclusion for `tests/advisors/` or
  a large-RAM host. This is a known constraint tracked separately; it does not block this PR.

## Provenance (git evidence the leak is pre-existing, not C5)

- C5 (`4de25d8..6d20d77`) did NOT touch `tests/advisors/test_community_strats.py`,
  `test_atlas_cache.py`, `test_community_strats_timeout.py`, or the production modules they test.
- The reload pattern is byte-identical to the pre-C5 commit `2a1787e`. C5's larger footprint
  (more importers of `community_strats`/`atlas_cache` collected before the leaking files) merely
  raised the single-process accumulation, exposing it at the C5 gate — it did not introduce it.

## Reference implementation

`tests/advisors/test_universe_provider.py` @ `e52e17c` — the env-var-only isolation pattern +
the AST anti-recurrence guard. Used as the template for this remediation cycle.
