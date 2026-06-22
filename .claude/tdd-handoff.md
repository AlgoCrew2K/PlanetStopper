# TDD Handoff — footprint-cap-hardening cycle

**Cycle:** test-footprint-and-cap-hardening
**Feature plan:** `feature-plans/test-footprint-and-cap-hardening.md`
**Branch:** `fix/footprint-cap-hardening`
**Worktree:** `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/footprint-cap`
**Phase:** GREEN + recurrence guard committed — awaiting w1b-review APPROVE + w1b-doc docs

---

## Current state (updated 2026-06-22 by w1b-lead after continuation dispatch)

All AC-1 through AC-6 are GREEN (implemented by w1b-impl in commits 62afd25, 85e9145, 7c0d5cf, 862fcc1).
Recurrence guard added in dd7daab (this commit).

### Branch HEAD: dd7daab

### Commit history (newest first)
| SHA | What |
|-----|------|
| `dd7daab` | test(meta): recurrence guard — every tests/**/*.py must parse |
| `862fcc1` | fix(test-infra): AC-1 remove dead empty husk classes + ruff format |
| `7c0d5cf` | fix(test-infra): AC-6 extract _assert_safe_worker_count seam |
| `85e9145` | fix(test-infra): AC-1/AC-2/AC-3 footprint refactors |
| `62afd25` | fix(test-infra): AC-4/AC-5/AC-6 cap hardening |
| `283e99a` | test(footprint-cap): RED tests for AC-1..AC-6 |
| `bf8bb4e` | docs(plan): test-footprint reduction + memory-cap hardening |

### AC coverage — all GREEN
| AC | Description | Test file(s) | Status |
|----|-------------|-------------|--------|
| AC-1 | node --check consolidated + empty husks removed | `tests/js_syntax/test_js_syntax.py`, `tests/js_syntax/test_js_syntax_consolidation.py`, `tests/meta/test_all_test_files_parse.py` (recurrence guard) | GREEN |
| AC-2 | subprocess --collect-only → importlib.import_module | `tests/execution/test_orphan_port_importlib_refactor.py` | GREEN |
| AC-3 | _init_db_at subprocess → in-process init_db | `tests/advisors/test_prism_dotenv_init_refactor.py` | GREEN |
| AC-4 | KILL_ON_JOB_CLOSE flag in LimitFlags | `tests/mem_cap/test_kill_on_job_close_flag.py` | GREEN |
| AC-5 | IsProcessInJob membership verify-or-fail-loud | `tests/mem_cap/test_cap_install_verify_or_fail_loud.py` | GREEN |
| AC-6 | xdist worker count guard + _assert_safe_worker_count seam | `tests/conftest_guard/test_xdist_worker_count_guard.py` | GREEN |
| AC-7 | cap regression — total-job semantics preserved | `tests/mem_cap/test_total_job_memory_cap.py` | GREEN |
| Recurrence guard | every tests/**/*.py must compile (empty class body prevention) | `tests/meta/test_all_test_files_parse.py` | GREEN (477 files) |

### Verified (w1b-lead, 2026-06-22, ALPHABOT_TEST_MEM_CAP_GB=8 -n0)
- `tests/meta/test_all_test_files_parse.py` — 477 passed
- `tests/app/test_guard_alpha_panel_ui.py`, `tests/dashboard/test_dashboard_render_consistency.py`, `tests/dashboard/test_window_picker_wiring.py` — all included in the 477, GREEN
- `tests/js_syntax/` + `tests/mem_cap/` + `tests/conftest_guard/` — 43 passed
- Combined (guard + 3 fixed husks + targeted dirs) — 513 passed / 0 failed / 0 errors
- `ruff format --check .` — 532 files already formatted (clean)
- `ruff check .` — All checks passed (clean)

### Unstaged remaining (NOT test files — w1b-doc owns these)
- `.claude/CLAUDE.md` — doc-gen update pending
- `DECISIONS.md` — doc-gen update pending

---

## For w1b-review

Review the diff on this branch vs origin/main (or vs 5597eb5, the pre-cycle base). Key files to review:
- `tests/_mem_cap.py` — AC-4 LimitFlags OR + AC-5 IsProcessInJob seam + membership guard
- `tests/conftest.py` — AC-6 _assert_safe_worker_count seam extraction
- `tests/js_syntax/test_js_syntax.py` — AC-1 parametrized node --check
- `tests/meta/test_all_test_files_parse.py` — recurrence guard (new, dd7daab)
- 19 donor test files — verify only node --check methods removed, no collateral deletions

Review focus: correctness of seam extraction, flag OR semantics, warning path, guard deselect-safety.

## For w1b-doc

Commit `.claude/CLAUDE.md` and `DECISIONS.md` updates onto this branch before signaling cycle-complete.
Key doc entries needed:
- `tests/meta/test_all_test_files_parse.py` — what it guards (empty class body recurrence), how it works
- `tests/_mem_cap.py` — KILL_ON_JOB_CLOSE flag + IsProcessInJob membership verification
- `tests/conftest.py` — _assert_safe_worker_count seam + xdist ceiling enforcement
- `tests/js_syntax/test_js_syntax.py` — consolidated JS syntax guard

---

## Original RED requirements (historical — all implemented)

*(kept below for audit trail; implementation is complete)*

### What was RED and what was made GREEN

### What is RED and what you must make GREEN

**File 1: `tests/mem_cap/test_kill_on_job_close_flag.py`** (AC-4)

- `test_kill_on_job_close_flag_defined_as_named_constant` — PASSES (constant already defined)
- `test_kill_on_job_close_flag_value_is_0x2000` — PASSES (constant value correct)
- `test_kill_on_job_close_flag_present_in_limit_flags_after_install` — **FAILS** (RED)
  - Root: `_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` is defined as a constant but NOT ORed into `LimitFlags` in `SetInformationJobObject`.
  - Fix: Add `| _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` to the OR-expression in `install_total_memory_cap` (line ~138 of `tests/_mem_cap.py`).

**File 2: `tests/mem_cap/test_cap_install_verify_or_fail_loud.py`** (AC-5)

- `test_cap_installed_true_and_membership_confirmed_on_success_path` — PASSES (happy path)
- `test_cap_install_is_noop_on_non_windows` — PASSES (no-op)
- `test_cap_install_uses_is_process_in_job_for_verification` — **FAILS** (RED)
  - Root: `_is_process_in_job_seam` does not exist. Add it as a module-level callable in `tests/_mem_cap.py`.
  - Seam signature: `_is_process_in_job_seam(cur_handle, job_handle) -> bool`
  - The seam wraps `IsProcessInJob(GetCurrentProcess(), job)` so tests can patch it.
  - After adding the seam, `install_total_memory_cap` must call it after `AssignProcessToJobObject` and only set `_CAP_INSTALLED=True` if it returns True; otherwise emit `warnings.warn(..., UserWarning)` and return without setting True.
- `test_cap_installed_false_and_warning_when_membership_not_confirmed` — XFAIL (becomes GREEN after seam added)
- `test_cap_installed_true_when_already_nested_and_membership_confirmed` — XFAIL (becomes GREEN after seam added)

**File 3: `tests/conftest_guard/test_xdist_worker_count_guard.py`** (AC-6 — REVISED, now 11 RED)

All 11 tests now **FAIL** (RED) — PM refinement requires extracting the inline guard into a
named helper `_assert_safe_worker_count(numprocesses)` exported from `tests/conftest.py`.

Root: The guard logic is inline inside `pytest_configure` and not exported as a seam.
Calling `pytest_configure` directly in tests triggers cap-install and env side-effects.

Fix: Extract the xdist guard block from `pytest_configure` into:

```python
def _assert_safe_worker_count(numprocesses) -> None:
    """Reject unsafe xdist worker counts (AC-6). Called EARLY in pytest_configure."""
    if numprocesses is None:
        return
    if numprocesses == "auto" or (isinstance(numprocesses, int) and numprocesses > 4):
        raise SystemExit(
            f"[mem-cap] Rejecting xdist numprocesses={numprocesses!r}: "
            "exceeds the safe ceiling of 4 on this host (67.8 GB RAM + 4 GB pf). "
            "Two Kernel-Power 41 hard-reboots were caused by -n auto fan-out. "
            "Use -n 0..4.  To opt out, set ALPHABOT_TEST_MEM_CAP_GB=0."
        )
```

Then in `pytest_configure`, replace the inline guard block with:
```python
_assert_safe_worker_count(getattr(config.option, "numprocesses", None))
```

This call must remain BEFORE the `ALPHABOT_MAX_JOBS`/`OPTUNA_N_JOBS` setdefaults and
BEFORE `install_from_env()` — the guard must fire before any side-effects.

**File 4: `tests/js_syntax/test_js_syntax_consolidation.py`** (AC-1)

- `test_js_syntax_consolidated_module_exists` — **FAILS** (RED) — create `tests/js_syntax/test_js_syntax.py`
- `test_js_syntax_consolidated_module_has_node_skip_guard` — SKIPPED (pending AC-1)
- `test_js_syntax_covers_all_static_js_files` — SKIPPED (pending AC-1)
- `test_js_syntax_consolidated_module_is_valid_python` — SKIPPED (pending AC-1)

**Implementation needed:**
1. Create `tests/js_syntax/test_js_syntax.py` with a parametrized `node --check` test that discovers `static/*.js` via glob. Must include `shutil.which("node") is None` skip guard.
2. Remove the ~14 individual `node --check` test method calls from these files (keep all other tests in those files):
   - `tests/ui/test_persymph_settings_modal.py`
   - `tests/ui/test_run_advisor_backtest_413_and_client_errors.py`
   - `tests/ui/test_dash_advisor_fixes.py`
   - `tests/ui/test_config_suggestion_card_fixes.py`
   - `tests/dashboard/test_symph_autoupdate.py`
   - `tests/dashboard/test_window_picker_wiring.py`
   - `tests/dashboard/test_render_basis_fix.py`
   - `tests/dashboard/test_cold_start_account_stat.py`
   - `tests/dashboard/test_dash_fixes.py`
   - `tests/dashboard/test_dashboard_render_consistency.py`
   - `tests/dashboard/test_cards_live_refresh.py`
   - `tests/dashboard/test_cards_live_updates.py`
   - `tests/dashboard/test_card_consistency_liveness.py`
   - `tests/app/test_strategy_builder_spa_port.py`
   - `tests/app/test_guard_alpha_panel_ui.py`
   - `tests/ai_advisor/test_cycle5_market_prism_surface.py`
   - `tests/ai_advisor/test_advisor_informative_output.py`
   - `tests/ai_advisor/test_advisor_inplace_tabs.py`
   - `tests/ai_advisor/test_advisor_chat_handoff.py`

**File 5: `tests/execution/test_orphan_port_importlib_refactor.py`** (AC-2 — REVISED)

- `test_collect_only_subprocess_calls_removed_from_orphan_test` — **FAILS** (RED, AST-scoped)
- `test_importlib_import_module_used_in_retained_portmode_class` — **FAILS** (RED, AST-scoped)
- `test_importlib_calls_cover_both_portmode_targets` — PASSES

The tests now use AST-scoped checks (not raw string search) to avoid false-passes on
comments/docstrings. Both `subprocess.run([..., '--collect-only', ...])` absence AND
`importlib.import_module()` presence are verified within the `TestRetainedPortmodeTestsStillCollect`
class body only.

**Implementation needed:**
Replace the two `subprocess.run(["python", "-m", "pytest", "--collect-only", ...])` calls at
lines 524 and 540 of `tests/execution/test_orphan_port_modules_removed.py` (inside
`TestRetainedPortmodeTestsStillCollect`) with in-process `importlib.import_module(...)` calls.
If the module doesn't import, ImportError is raised — equivalent coverage. Keep `target.exists()`.

**File 6: `tests/advisors/test_prism_dotenv_init_refactor.py`** (AC-3)

- `test_init_db_at_no_longer_spawns_subprocess` — **FAILS** (RED)
- `test_init_db_at_uses_in_process_init_db` — PASSES
- `test_essential_dotenv_subprocess_tests_preserved` — PASSES
- `test_prism_dotenv_hardening_file_is_valid_python` — PASSES

**Implementation needed:**
Replace the subprocess body of `_init_db_at` in `tests/advisors/test_prism_dotenv_hardening.py`
(lines 80-103) with a direct `os.environ["DB_PATH"] = str(db_path)` + `database.init_db()` call.
The essential dotenv-discovery subprocess tests (which call the real CLI via subprocess.run) are
UNCHANGED — only the helper function changes.

---

## Summary of RED → GREEN requirements

| File to change | Required implementation |
|---|---|
| `tests/_mem_cap.py` | (1) Add `_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` to LimitFlags OR-expression (AC-4); (2) Add `_is_process_in_job_seam` callable wrapping IsProcessInJob; (3) Call seam after AssignProcessToJobObject; (4) warn+return if unconfirmed (AC-5) |
| `tests/conftest.py` | Extract inline xdist guard into `_assert_safe_worker_count(numprocesses)` top-level helper; call it EARLY in `pytest_configure` before any other side-effect (AC-6) |
| `tests/js_syntax/test_js_syntax.py` | CREATE: parametrized `node --check` over `static/*.js` with `shutil.which` skip; must actually run `node --check` and assert exit 0 per file (AC-1) |
| 19 scattered test files | REMOVE per-file `node --check` test methods |
| `tests/execution/test_orphan_port_modules_removed.py:524,540` | REPLACE subprocess `--collect-only` with `importlib.import_module(...)` inside `TestRetainedPortmodeTestsStillCollect` |
| `tests/advisors/test_prism_dotenv_hardening.py:_init_db_at` | REPLACE subprocess body with direct `database.init_db()` call |

---

## Constraints

- All changes are ONLY in `tests/` — NO production code (app.py, math_engine.py, etc.)
- Confirm `git branch --show-current` is `fix/footprint-cap-hardening` before committing
- Verify ONLY with bounded per-file `-n0` runs using `pytest.main(... '--override-ini=addopts=')` from `C:/Windows/Temp`
- Do NOT merge and do NOT push
- After GREEN, SendMessage w1-test (test-writer) with the HEAD SHA

## Note on w1-impl working tree state

The implementer's working tree already contains partial changes to `tests/_mem_cap.py` and
`tests/conftest.py` (unstaged). Verify with `git diff tests/_mem_cap.py` before committing.
The conftest.py change (AC-6 guard) is correct and should be committed. The `_mem_cap.py`
changes implement AC-4 and AC-5 — verify they pass the RED tests above before committing.
