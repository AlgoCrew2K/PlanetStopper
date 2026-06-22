# TDD Handoff — footprint-cap-hardening cycle

**Cycle:** test-footprint-and-cap-hardening
**Feature plan:** `feature-plans/test-footprint-and-cap-hardening.md`
**Branch:** `fix/footprint-cap-hardening`
**Worktree:** `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/footprint-cap`

---

## RED tests committed — implementer reads THIS FILE, NOT the feature plan

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

**File 3: `tests/conftest_guard/test_xdist_worker_count_guard.py`** (AC-6)

All 10 tests PASS — AC-6 guard is already implemented in `tests/conftest.py`. These are regression guards. No implementation needed for AC-6.

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

**File 5: `tests/execution/test_orphan_port_importlib_refactor.py`** (AC-2)

- `test_collect_only_subprocess_calls_removed_from_orphan_test` — **FAILS** (RED)
- `test_importlib_import_used_instead` — PASSES
- `test_orphan_test_still_asserts_target_files_exist` — PASSES

**Implementation needed:**
Replace the two `subprocess.run(["python", "-m", "pytest", "--collect-only", ...])` calls at
lines 524 and 540 of `tests/execution/test_orphan_port_modules_removed.py` with in-process
`importlib.import_module(...)` calls. If the module doesn't import, ImportError is raised —
equivalent coverage to `--collect-only`. Keep the `target.exists()` assertion.

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
| `tests/js_syntax/test_js_syntax.py` | CREATE: parametrized `node --check` over `static/*.js` with `shutil.which` skip |
| 19 scattered test files | REMOVE per-file `node --check` test methods |
| `tests/execution/test_orphan_port_modules_removed.py:524,540` | REPLACE subprocess `--collect-only` with `importlib.import_module(...)` |
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
