# Tracked Debt — `importlib.reload`-per-test memory-leak remediation

**Status:** ready (dedicated post-feature remediation cycle — NOT a Strategy-Builder-Real task)
**Type:** pre-existing test-infrastructure debt
**Discovered:** 2026-06-21, by the Strategy-Builder-Real (C5) single-process full-tree gate
**Classification:** PRE-EXISTING C1-era test-debt (git-proven — see "Provenance" below). NOT a C5 defect.
**Owner:** TBD (own branch + own team for the remediation cycle)

---

## Summary

Several `tests/advisors/` test files call `importlib.reload(...)` once (or many times) **per
test** to "pick up a new env var" or "re-bind to a patched dependency." `importlib.reload`
is a memory-leak anti-pattern: it re-executes the module into a **new** module object while
other already-imported modules keep references to the **old** one, so each reload orphans a
whole module's worth of objects (plus its heavy transitive imports — `pymongo`,
`advisors.atlas_cache`, `pandas`/`requests` state). Across a multi-file run the orphaned
modules accumulate **unbounded**, which **OOMs single-process full-tree verification**
(`pytest -p no:xdist`). Under the project's default/CI test mode (`pytest -n auto`, xdist) the
accumulation is sharded across worker processes, so it stays bounded — which is why this only
surfaces under the operator-mandated single-process pre-merge gate.

This is **partially fixed already**: the `test_universe_provider.py` portion was removed on the
C5 feature branch (commit `e52e17c`) to unblock that feature's full-tree gate. The remaining
sites below are the dedicated remediation deliverable.

## Affected files + site counts (remaining, as of `e52e17c`)

| File | `importlib.reload` sites | Notes |
|------|--------------------------|-------|
| `tests/advisors/test_community_strats.py` | **35** | DOMINANT leaker. A `module_under_test` fixture reloads `advisors.community_strats` per test, AND ~all tests reload AGAIN inside the test body to re-bind to a patched dependency. `community_strats` pulls in `atlas_cache` + `pymongo` → each reload orphans a heavy module. |
| `tests/advisors/test_atlas_cache.py` | 1 | `isolated_atlas_cache_db`-style fixture reload. |
| `tests/advisors/test_community_strats_timeout.py` | 1 | Single reload of `advisors.community_strats`. |
| `tests/advisors/test_universe_provider.py` | 0 (FIXED, `e52e17c`) | Reference implementation of the fix — see below. |

## Mechanism (why it leaks, why interaction-dependent)

- `importlib.reload(M)` runs `M`'s top-level code again and updates `sys.modules["M"]` in place,
  but any object that already did `from M import name` or holds `M.func` keeps pointing at the
  PRE-reload definitions. The pre-reload module object (and everything it imported) cannot be
  garbage-collected while those references live. Each per-test reload therefore leaks ~one
  module graph.
- **Standalone-clean, multi-file-leaky:** run ALONE, the leaking file has few other holders of
  the reloaded module, so the orphan count is small and bounded (`test_universe_provider.py`
  alone peaked 0.16 GB). In a full-`tests/advisors/` run, MANY other test modules import
  `community_strats`/`atlas_cache` first, so the per-test reload orphans references all of them
  hold → the accumulation compounds (observed: full `tests/advisors/` climbing 1.2 → 5.5 GB+
  single-process before fix; still 3.6 GB+ with only the universe_provider portion fixed).

## The LOAD-BEARING risk (why this is NOT a mechanical sweep)

`test_community_strats.py`'s reloads are **load-bearing for patch visibility**, not just env
isolation: tests do `patch("advisors.atlas_cache.cached_pull", ...)` then
`importlib.reload(community_strats)` so the reloaded `community_strats` re-binds to the patched
dependency. Naively deleting these reloads can BREAK pre-existing tests if `community_strats`
binds its dependency at import time (`from advisors.atlas_cache import cached_pull`) rather than
accessing it as a module attribute at call time (`atlas_cache.cached_pull(...)`). Each site must
be verified before removal. This is why it was NOT swept inside C5 (high-risk, unrelated to the
feature).

## Candidate fixes (per site, choose by what each test actually needs)

1. **Env-var-only isolation (the universe_provider fix):** if the reload exists merely to "pick
   up a new env var," delete it — `atlas_cache._db_path()` reads `ATLAS_CACHE_DB_PATH` from
   `os.environ` at CALL time, so `monkeypatch.setenv(...)` + `tmp_path` already isolate. Proven
   sufficient in `test_universe_provider.py` (`e52e17c`).
2. **Re-point the patch target:** if the reload exists for patch visibility, patch the symbol
   where it is USED (e.g. `patch("advisors.community_strats.cached_pull")` if `community_strats`
   imported it by name, or `patch("advisors.atlas_cache.cached_pull")` + ensure `community_strats`
   calls it via module attribute). This removes the need to reload for re-binding.
3. **Fixture-teardown reset (last resort, if a reload is genuinely unavoidable):** in a fixture
   `yield` teardown, `sys.modules.pop("advisors.community_strats", None)` + `gc.collect()` to
   force the orphan free per test (bounds the accumulation even if reload stays).

## Acceptance criteria for the remediation cycle

- **AC-1:** Zero `importlib.reload(...)` calls remain in `tests/advisors/` (enforce with an
  AST guard like `test_universe_provider.py::test_no_importlib_reload_in_this_test_module`,
  generalized to the whole dir or replicated per file).
- **AC-2:** Every affected file's tests stay GREEN with the same assertions (no weakening; this
  is isolation-mechanism change only, not a behavior change).
- **AC-3 (the proof):** `pytest tests/advisors/ -m "not live and not slow and not perf"
  -p no:xdist -o addopts=` completes BOUNDED (~base sub-GB peak) with 0 failed / 0 errors.
  Bounded single-process completion IS the proof.

## Provenance (git evidence the leak is pre-existing, not C5)

- C5 (`4de25d8..6d20d77`) did NOT touch `tests/advisors/test_community_strats.py`,
  `test_atlas_cache.py`, `test_community_strats_timeout.py`, `test_universe_provider.py`,
  `tests/advisors/conftest.py`, or production `advisors/community_strats.py` /
  `advisors/atlas_cache.py` / `advisors/universe_provider.py` (all `git diff --stat` empty).
- The leaking surface is byte-identical to the pre-C5 commit `2a1787e`. Identical code +
  identical inputs ⇒ identical leak. The mechanism predates C5; C5's larger footprint (more
  importers of `community_strats`/`atlas_cache` collected before the leaking files) merely
  pushed the single-process orphan count past the OOM threshold, exposing it at the C5 gate.

## Reference implementation

`tests/advisors/test_universe_provider.py` @ `e52e17c` — the env-var-only isolation pattern +
the AST anti-recurrence guard. Use it as the template for the remediation cycle.
