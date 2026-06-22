# Feature Plan — Test-Host Memory Cap + Daemon Memory Guard

Status: ready

## Summary
A full `python -m pytest` run (default `-n 2 --dist loadfile`) committed **~238 GB** of
virtual memory on the dev host (63.8 GB RAM + 4 GB pagefile = **67.8 GB commit ceiling**),
triggering a low-virtual-memory condition and a **hard reboot** (Windows Kernel-Power 41),
on 2026-06-21. Root cause is **process fan-out**, not a single allocation: `-n 2` xdist
workers + ~15 tests that spawn child interpreters via `subprocess.run([sys.executable, ...])`
+ the heavy scientific stack (numpy/scipy/pandas/optuna/matplotlib) each *reserve* multi-GB
of committed address space; on Windows the nested tree's commit is attributed to one PID
(the "238 GB process").

The Jun-13 `.design-handoff/memfix` cycle (MERGED) capped the two largest fan-out sites
(the nested-pytest meta test → forced single-process; `synthetic_history` joblib + optuna
n_jobs → env-bounded to 1 in tests). Those caps are necessary but **demonstrably
insufficient** — a full `-n 2` run still exceeds the host ceiling. An RSS-poll watchdog is
**not** a reliable guard: a max-single-process poll misses a fan-out of many medium
processes, and a fast reservation outruns the poll.

This cycle installs a **hard OS-level total-tree memory cap** so that ANY over-cap
allocation — regardless of which test/subprocess/fan-out causes it — fails with
`MemoryError` at the exact site instead of crashing the host. It also hardens the **droplet
daemon** with a systemd memory limit so a runaway can never take down the 4 GB droplet
(production parallelism is already core-bounded; this is belt-and-suspenders).

The proven mechanism is a Windows Job Object (validated 2026-06-21: a 2 GB cap fired
`MemoryError` at ~1 GiB, host never at risk). The existing `.design-handoff/memfix/
job_cap_harness.py` uses the **per-process** flag (`JOB_OBJECT_LIMIT_PROCESS_MEMORY`),
which does NOT bound a fan-out; this cycle upgrades to the **total-job** flag
(`JOB_OBJECT_LIMIT_JOB_MEMORY`) and installs it from `conftest.pytest_configure`.

## Acceptance Criteria
- **AC-1 — Total-tree cap installed at pytest startup.** `tests/conftest.py pytest_configure`
  installs a Windows Job-Object cap using `JOB_OBJECT_LIMIT_JOB_MEMORY` (total across the
  whole process tree) on the current process BEFORE xdist workers spawn, so the controller +
  all `-n` workers + any `subprocess`-spawned child interpreters are members of one capped
  job. Cap value is read from env `ALPHABOT_TEST_MEM_CAP_GB` (default a generous but safe
  value well under the host ceiling, e.g. 24 GB).
- **AC-2 — Over-cap fails with MemoryError, host survives.** A Windows-gated guard test
  proves that allocating past the cap raises `MemoryError` (not a host crash / not a silent
  pass). Reuse the validated self-test pattern (allocate-and-touch 1 GiB chunks).
- **AC-3 — Fan-out is bounded by the TOTAL job, not per-process.** A guard proves the
  total-job semantics: two child processes that each allocate under a per-process cap but
  whose SUM exceeds the job cap are bounded (the second over-the-total allocation fails).
  (This is the exact gap that crashed the host — must be covered, not just per-process.)
- **AC-4 — Linux/CI no-op, never breaks CI.** On non-Windows (the GitHub Actions Linux
  runner) the cap installer is a clean no-op (CI relies on the runner's own cgroup limits).
  The conftest hook must import and run with zero effect on Linux; the full suite must still
  collect+run on Linux exactly as before. No new hard dependency (`ctypes` is stdlib;
  Win32-only code paths guarded by `sys.platform`/`os.name`).
- **AC-5 — Sanctioned entrypoint + docs.** The `/run-tests` skill and `.design-handoff/
  memfix/FINDINGS.md` document that the cap is now automatic via conftest (no wrapper
  needed); the project CLAUDE.md "Known Gotchas" gets a row pointing at the cap + the 2026-
  06-21 RCA. `DECISIONS.md` records the design (DE-TEST-MEMCAP-001).
- **AC-6 — Droplet daemon hardened (prepared in-repo, applied by PM at gate).** The repo
  carries the systemd unit drop-in / documented change adding `MemoryMax=<N>G` +
  `Restart=on-failure` to the `planetstopper` daemon unit (and the council timer's service
  unit) so the daemon OOM-restarts instead of taking the droplet down. The PM applies +
  verifies it on the live droplet during the gate (not the team).
- **AC-7 — Cap does not false-trip a legitimate run.** The default cap is high enough that a
  normal capped full run does not spuriously `MemoryError` on a correctly-bounded suite; if
  the suite genuinely needs more than the cap, that is itself the signal that the residual
  footprint must be reduced (tracked follow-on; CI remains the cloud full-suite gate). The
  guard tests must not themselves balloon memory (use the cap, assert the error).

## Architecture
- **New module** `tests/_mem_cap.py` (or fold into conftest): `install_total_memory_cap(cap_bytes)`
  — ctypes Win32: `CreateJobObjectW` → `SetInformationJobObject(JobObjectExtendedLimitInformation)`
  with `LimitFlags = JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION |
  JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, `JobMemoryLimit = cap_bytes` → `AssignProcessToJobObject(
  GetCurrentProcess())`. Keep the handle alive process-lifetime. On non-Windows: return
  immediately (no-op). Lift the proven logic from `.design-handoff/memfix/job_cap_harness.py`
  but switch PER-PROCESS → TOTAL-JOB.
- **Wire into `tests/conftest.py pytest_configure`** — call the installer once, as early as
  possible (alongside the existing `DB_PATH` / `ALPHABOT_MAX_JOBS` setup), guarded by
  `os.name == "nt"`. Must run in BOTH the controller and each xdist worker process (xdist
  runs `pytest_configure` in every worker); the controller install happens before workers
  spawn so workers inherit job membership; each worker also self-installs defensively (a
  process can be assigned to a nested job on Win8+; assignment is idempotent/safe — verify).
- **Env knob** `ALPHABOT_TEST_MEM_CAP_GB` (default 24). `setdefault`-style so an operator can
  raise/lower it; a 0/empty/garbled value disables the cap (explicit opt-out) with a loud log.
- **Droplet (AC-6):** a `deploy/` systemd drop-in (e.g. `deploy/planetstopper.service.d/
  memory.conf` with `MemoryMax=3G` + `Restart=on-failure` + `RestartSec=`), documented in the
  deploy notes. PM applies via SSH at the gate.
- **Do NOT** change production parallelism (already bounded) and do NOT change the `-n 2`
  xdist setting in this cycle (footprint reduction is a separate follow-on). This cycle is
  purely additive safety.

## Edge Cases
- Process already in a job (Win8+ nested jobs) — assignment must succeed or degrade safely.
- xdist worker re-running `pytest_configure` — install must be idempotent / not error on a
  second assignment in the same process.
- `-n0` single-process runs — cap still installs and bounds the single process + any children.
- subprocess-spawned child interpreters (the prism/advisor tests) — confirm they inherit the
  job (spawned with default inherit flags) so their commit counts against the total cap.
- Linux CI — installer is a no-op; nothing imported that is Windows-only at module top level.
- Cap disabled (`ALPHABOT_TEST_MEM_CAP_GB=0`) — suite runs uncapped (explicit operator opt-out
  only); loud warning so it is never silent.
- Very low cap that a legit early test exceeds — surfaces as `MemoryError` in that test, not a
  host crash (acceptable; raise the cap).

## Security Considerations
- No secrets touched. ctypes Win32 calls only; no new external dependency. No change to the
  live-execution path, the dashboard write paths, or credentials. Droplet systemd change is
  ops-config (no app behavior change).

## Testing Strategy (adversarial — quant-test-writer)
- RED (Windows-gated, `@pytest.mark.skipif(os.name != "nt")`): allocate-past-cap raises
  `MemoryError` (AC-2); the installer is callable + idempotent (AC-1).
- RED: TOTAL-JOB semantics (AC-3) — a helper that fans out two children whose summed
  allocation exceeds the job cap is bounded (the over-total allocation fails). Construct so
  the test itself stays small (children do the allocating, capped).
- RED (cross-platform): on a simulated/forced non-Windows path the installer is a no-op and
  returns without error (AC-4) — monkeypatch `os.name`/`sys.platform` or guard via a seam.
- RED: conftest installs the cap (assert a sentinel set by the installer is present after
  `pytest_configure`), env knob honored (AC-1/AC-7), `=0` disables with a warning.
- Static guard: `ALPHABOT_TEST_MEM_CAP_GB` default present; the installer uses
  `JOB_OBJECT_LIMIT_JOB_MEMORY` (total), NOT the per-process flag (pin against regression to
  the original harness's weaker per-process cap).
- All guard tests must be bounded by the cap itself — never allocate toward the host ceiling.

## Scope Boundaries
- IN: the total-job OS cap module + conftest wiring + env knob + Windows guard tests + Linux
  no-op + `/run-tests` + docs + DECISIONS + the droplet systemd drop-in (in-repo); PM applies
  the droplet change at the gate.
- OUT (tracked follow-ons): deep per-test footprint reduction / identifying every residual
  fan-out site (the cap makes this SAFE to do later); changing `-n 2`; changing any production
  parallelism; the seed-cycle ship (paused behind this P0, its own tests already verified
  clean at 160 MB).
