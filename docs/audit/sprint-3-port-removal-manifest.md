# Sprint 3 — Port-Level Decision-Math Removal Manifest

## Metadata
- Auditor: code-auditor (solo read-only diagnostic)
- Run date: 2026-05-26
- Repo commit SHA: `ca32dc7e23f7345aed230b82bcdcf02ae0584d53`
- Branch: `cycle/sprint3-port-audit`
- `git status -sb`: clean (no staged or unstaged changes)
- Scope: `alpha_bot_execution.py`, `autotuner.py`, `math_engine.py`, `app.py`, `database.py`, `templates/`, `tests/portmode/`, `engine/`, `port_aggregator.py`, `port_selector.py`
- Mandate: Remove DECISION MATH operating at portfolio-aggregate level. Retain dashboard observability surfaces (NAV, portfolio strip, port-level chart, fleet alert banner, MANUAL `perform_account_liquidation` button).

---

## §0 Summary Table

| Classification      | Count | Files                                                              |
|---------------------|-------|--------------------------------------------------------------------|
| **REMOVE**          | 6     | `alpha_bot_execution.py` (4), `autotuner.py` (1), `engine/` (1)  |
| **KEEP-DISPLAY**    | 9     | `app.py` (5), `database.py` (3), `templates/` (1)                |
| **KEEP-MANUAL**     | 2     | `app.py` (2), `templates/_chrome.html` (1 — same surface)        |
| **AMBIGUOUS**       | 2     | `app.py` `save_settings` EXIT_AUTHORITY write (1), `engine/exit_authority.py` (1) |

Total sites classified: **19**

Top 3 highest-risk REMOVE sites:
1. `alpha_bot_execution.py:1540–1664` — the full PORT-LEVEL DISPATCH block: reads `port_state`, calls `aggregate_to_port`, `build_port_signal`, `select_symphony_with_mc_gate`, and fires `execute_sell_to_cash` autonomously. This is the apex execution path.
2. `alpha_bot_execution.py:1000` + `:1018–1028` — `get_exit_authority()` read at cycle start; `initialize_port_state_if_absent` called for every account in the per-symphony loop; both exist solely to service the port-level dispatch.
3. `autotuner.py:253–282` — `get_port_mode_search_space()` + `validate_port_mode_params_available()` + port-mode split constants (`PORT_TRAIN_RATIO`, `PORT_VALIDATION_RATIO`, `PORT_FROZEN_EVAL_RATIO`, `MODE_SPECIFIC_PARAMS`); the port-mode autotuner has no replay validation, is explicitly flagged as untrustworthy, and exists to tune parameters for the decision path being removed.

---

## §1 `alpha_bot_execution.py` — Decision Paths

### SITE-A1 — `get_exit_authority()` call at cycle start

- **File:** `alpha_bot_execution.py:1000`
- **Classification:** REMOVE
- **Autonomous trigger condition:** Reads `EXIT_AUTHORITY` env var and assigns the result to `exit_authority`, which gates both the port-level dispatch block (line 1540) and the per-symphony execution queue (line 1673). When `exit_authority="port_level"`, the per-symphony queue is suppressed and port-level dispatch runs instead. This is the top-level decision fork.
- **Current pattern (verbatim):**
  ```python
  exit_authority = get_exit_authority()
  ```
- **What replaces it:** Nothing. After port-level math is removed, `EXIT_AUTHORITY` is always `per_symphony` by definition. The `is_authoritative` guard on line 1673 (per-symphony queue) reduces to a constant `True`. The import of `get_exit_authority` and `is_authoritative` from `engine.exit_authority` can be deleted.
- **Test coverage:** `tests/portmode/test_exit_authority.py` — all tests in this file exercise the removed decision path. They will need to be deleted or converted to characterization tests confirming the toggle no longer exists.
- **Suggested cycle:** `port-execution-dispatch-removal`
- **Prerequisites:** SITE-A2 (port dispatch block must be removed in the same pass)

---

### SITE-A2 — PORT-LEVEL DISPATCH block

- **File:** `alpha_bot_execution.py:1535–1664` (comment header at 1535, `if is_authoritative(...)` at 1540, block ends before line 1666 comment)
- **Classification:** REMOVE
- **Autonomous trigger condition:** `is_authoritative(altitude="port_level", exit_authority=exit_authority)` returns True when `EXIT_AUTHORITY=port_level`. On True: reads `port_state`, detects composition changes, calls `aggregate_to_port` + `build_port_signal`, calls `select_symphony_with_mc_gate`, and — when `_port_signal["triggered"]` and MC gate passes — calls `execute_sell_to_cash` autonomously on the selected symphony. This is the complete port-level autonomous exit path.
- **Current pattern (verbatim, key lines):**
  ```python
  if is_authoritative(altitude="port_level", exit_authority=exit_authority):
      ...
      _port_agg = aggregate_to_port(symphonies=_sym_snapshots, ...)
      _port_signal = build_port_signal(_port_agg, cycle_id=cycle_id_str)
      if not _port_signal.get("triggered"):
          continue
      _selection = select_symphony_with_mc_gate(...)
      ...
      _port_success = execute_sell_to_cash(_sym_actual, _account)
  ```
- **What replaces it:** Nothing. The per-symphony exit queue (line 1673+) already handles symphony-level exits and is preserved.
- **Test coverage:** `tests/portmode/test_port_signal.py`, `tests/portmode/test_port_aggregator.py`, `tests/portmode/test_port_selector.py`, `tests/portmode/test_mc_sanity_gate.py`, `tests/portmode/test_port_telemetry.py`, `tests/portmode/test_composition_change_reset.py`, `tests/portmode/test_per_account_params.py` — all exercise this dispatch block. These tests will need deletion or annotation as dead coverage when the block is removed.
- **Suggested cycle:** `port-execution-dispatch-removal`
- **Prerequisites:** None (this is the apex site)

---

### SITE-A3 — `initialize_port_state_if_absent` call in per-symphony loop

- **File:** `alpha_bot_execution.py:1024–1028`
- **Classification:** REMOVE
- **Autonomous trigger condition:** Called on every cycle for every account, writing `port_state` rows to the DB. This only exists to seed state that the port-level dispatch (SITE-A2) reads. With SITE-A2 removed there is no consumer of `port_state` on the execution path.
- **Current pattern (verbatim):**
  ```python
  # AC-P2.1.3: initialize port_state on first cycle for this account.
  current_account_value = sum(
      sym.get("current_value", sym.get("value", 0.0)) for sym in symphonies
  )
  initialize_port_state_if_absent(account, current_port_value=current_account_value)
  ```
- **What replaces it:** Nothing. The `current_account_value` accumulation can also be removed unless it is used downstream for another purpose (verify before deletion — it appears only here and in the call above).
- **Test coverage:** `tests/portmode/test_dual_altitude_state.py` — tests `initialize_port_state_if_absent`; will need updating.
- **Suggested cycle:** `port-execution-dispatch-removal` (same pass as SITE-A2 for atomicity)
- **Prerequisites:** SITE-A2

---

### SITE-A4 — Port snapshot collection: `port_symphony_snapshots` dict

- **File:** `alpha_bot_execution.py:1018–1021` (declaration) + all sites where `port_symphony_snapshots[account]` is populated within the per-symphony loop (search `port_symphony_snapshots` — populated at multiple points in the symphony loop before line 1535)
- **Classification:** REMOVE
- **Autonomous trigger condition:** `port_symphony_snapshots` is populated within the per-symphony loop solely to feed SITE-A2 (the port dispatch block). With SITE-A2 removed this dict has no consumer.
- **Current pattern (verbatim):**
  ```python
  # AC-P2.8: collect symphony snapshots per account for port-level aggregation.
  # Populated during the per-symphony loop; consumed after it.
  port_symphony_snapshots: dict[str, list[dict]] = {}
  ```
- **What replaces it:** Nothing.
- **Test coverage:** indirectly covered by the same portmode tests as SITE-A2.
- **Suggested cycle:** `port-execution-dispatch-removal` (same pass as SITE-A2)
- **Prerequisites:** SITE-A2

---

### SITE-A5 — `is_authoritative` guard on per-symphony execution queue

- **File:** `alpha_bot_execution.py:1673–1675`
- **Classification:** REMOVE (the guard expression only; the queue itself is KEEP)
- **Autonomous trigger condition:** `is_authoritative(altitude="per_symphony", exit_authority=exit_authority)` currently gates the per-symphony execution queue. When `exit_authority="port_level"` this evaluates to `False` and the queue is suppressed. After port-level removal, this condition is always `True` by construction and the guard can be replaced with the unconditional `if execution_queue:` branch.
- **Current pattern (verbatim):**
  ```python
  if execution_queue and is_authoritative(
      altitude="per_symphony", exit_authority=exit_authority
  ):
  ```
- **What replaces it:** `if execution_queue:` — a direct unconditional test. The `is_authoritative` import can be removed.
- **Test coverage:** `tests/portmode/test_exit_authority.py` partially; no dedicated test for the queue guard itself — existing execution tests cover the queue behavior.
- **Suggested cycle:** `port-execution-dispatch-removal` (same pass for consistency)
- **Prerequisites:** SITE-A1, SITE-A2

---

## §2 `autotuner.py` — Port-Mode Autotuner

### SITE-B1 — Port-mode autotuner constants and functions

- **File:** `autotuner.py:215–282` (constants at 215–240, functions at 243–282)
- **Classification:** REMOVE
- **Autonomous trigger condition:** `build_port_study_name`, `get_port_mode_search_space`, `validate_port_mode_params_available`, `PORT_TRAIN_RATIO`, `PORT_VALIDATION_RATIO`, `PORT_FROZEN_EVAL_RATIO`, `MODE_SPECIFIC_PARAMS`, `MODE_INVARIANT_PARAMS` — these exist exclusively to service a port-level Optuna study that tunes parameters for the port-level exit decision path (SITE-A2). The autotuner's own documentation flags this path as non-replay-validated (`warn_port_mode_replay_blind_spot`), and no production call site invokes `get_port_mode_search_space` from outside tests. Removing the port decision math makes these stubs dead code.
- **Current pattern (verbatim, representative):**
  ```python
  def get_port_mode_search_space() -> dict:
      warn_port_mode_replay_blind_spot()
      return {
          "PARABOLIC_VELOCITY_THRESHOLD": (...),
          "VWAP_CROSS_HWM_PCT": (...),
      }
  ```
- **What replaces it:** Nothing. `warn_port_mode_replay_blind_spot()` (line 630–643) can also be removed as its only callers are within the port-mode functions above.
- **Test coverage:** `tests/portmode/test_autotuner_portmode.py` — the entire file exercises these functions. Deletion of the test file is required.
- **Suggested cycle:** `port-autotuner-removal`
- **Prerequisites:** SITE-A2 (confirm execution path is gone before removing tuning infrastructure)

---

## §3 `engine/` — Dual-Altitude Resolver

### SITE-C1 — `engine/dual_altitude.py` module (full module) and `engine/exit_authority.py` module (decision-path functions)

- **File:** `engine/dual_altitude.py:1–109` (entire module), `engine/exit_authority.py:25–87` (functions `get_exit_authority`, `validate_exit_authority`, `is_authoritative`, `write_exit_authority_to_env`)
- **Classification:** REMOVE (decision-path functions); AMBIGUOUS for display helper (see §5)
- **Autonomous trigger condition:** `compute_for_altitude`, `initialize_port_state_if_absent` (dual_altitude.py) — both exist only to service the port-level dispatch path. `get_exit_authority`, `is_authoritative`, `validate_exit_authority` (exit_authority.py) — read/validate the toggle that controls whether the port-level or per-symphony path is authoritative.
- **`write_exit_authority_to_env` note:** this function writes the `EXIT_AUTHORITY` env var from the settings UI. With port-level math removed, the toggle itself is dead. See AMBIGUOUS item AX-1 in §5 for the settings-route interaction.
- **What replaces it:** Nothing for the decision path. The `get_exit_authority_badge_context` and `build_restart_notice_context` display helpers in `exit_authority.py` need separate classification — see AMBIGUOUS item AX-2 in §5.
- **Test coverage:** `tests/portmode/test_exit_authority.py`, `tests/portmode/test_dual_altitude_state.py`, `tests/portmode/test_settings_exit_authority_route.py`, `tests/portmode/test_settings_restart_notice.py`, `tests/portmode/test_dual_altitude_dashboard.py` — all test the removed decision path and will need deletion or major rework.
- **Suggested cycle:** `port-engine-module-removal`
- **Prerequisites:** SITE-A1, SITE-A2, SITE-A5 (execution path fully cleaned before removing the engine modules)

---

## §4 KEEP-DISPLAY Sites

### SITE-D1 — `app.py:675–696` — `port_state` read in `get_api_state_dict`

- **File:** `app.py:675–696`
- **Classification:** KEEP-DISPLAY
- **Rationale:** Reads `port_state` for all accounts and includes it in the `/api/state` response. This powers dashboard observability (operator can see port-level state in the UI). No decision is made from this read; the result is serialised into the JSON response and consumed only by templates for display.
- **Consumer:** Frontend template / JS reads `port_state` from `/api/state` for the observability panel. Retained per mandate.

---

### SITE-D2 — `app.py:714–715` — `port_state` and `exit_authority` in `/api/state` response

- **File:** `app.py:714–715`
- **Classification:** KEEP-DISPLAY
- **Rationale:** The additive fields `port_state` and `exit_authority` are injected into the `/api/state` response. These are read-only display values consumed by the dashboard. No engine action flows from them.

---

### SITE-D3 — `app.py:489–513` — `portfolio_strip` / `portfolio_meta` build for Hero chart

- **File:** `app.py:489–513`
- **Classification:** KEEP-DISPLAY
- **Rationale:** Computes NAV, today's change, cumulative return, max drawdown from `portfolio_strip` (sourced from `_compute_portfolio_strip`). These are display values for the dashboard Hero chart (AC-C2). No autonomous action is triggered.

---

### SITE-D4 — `database.py:1789–1857` — `read_port_state`, `write_port_state`, `clear_port_state`, `get_all_port_states`

- **File:** `database.py:1789–1857`
- **Classification:** KEEP-DISPLAY (read helpers) / AMBIGUOUS for write helpers
- **Rationale:** `read_port_state` and `get_all_port_states` are pure reads consumed by `app.py` display routes. **However:** `write_port_state` and `clear_port_state` are also called from the decision path (SITE-A2 and SITE-A3). Once the decision path is removed the write helpers become dead code unless another caller exists. Verify with grep after SITE-A2 removal.
- **Note:** The `port_state` table itself (migration 010) stays per the additive-first mandate — tables are never dropped in a single step.

---

### SITE-D5 — `database.py:1860–1908` — `new_day_reset_port_state`, `rebase_port_state_on_composition_change`

- **File:** `database.py:1860–1908`
- **Classification:** AMBIGUOUS (see §5 AX-3)
- **Rationale:** `new_day_reset_port_state` resets transient port_state fields at new-day boundary — it is a lifecycle helper that only matters if port_state is being written by the engine. `rebase_port_state_on_composition_change` is called exclusively from SITE-A2. Once the decision path is removed, both functions have no live caller. However, because `new_day_reset_port_state` writes to the same `port_state` table being kept for display, whether it should be retained or removed requires a PM decision (see AX-3).

---

### SITE-D6 — `templates/_chrome.html:88–130` — Emergency Liquidate button + panic modal

- **File:** `templates/_chrome.html:88–130`
- **Classification:** KEEP-MANUAL
- **Rationale:** This is the operator-triggered manual liquidation UI (button → modal → POST `/api/sell_account`). It is explicitly called out in the mandate as a retained surface. It is never triggered autonomously.

---

### SITE-D7 — `app.py:1801–1903` — `perform_account_liquidation` + `/api/sell_account` route

- **File:** `app.py:1801–1903`
- **Classification:** KEEP-MANUAL
- **UI trigger:** `POST /api/sell_account` from the Emergency Liquidate modal (SITE-D6). The route requires: `confirm_account_id` matching `account_id`, `confirm_phrase == "LIQUIDATE"`, and LIVE_EXECUTION mode. A background thread runs `perform_account_liquidation`. This is an operator action path — no autonomous condition triggers it.
- **Why this is operator action:** Four confirmation gates before execution; explicit human intent required (modal + typed phrase).

---

### SITE-D8 — `app.py:1984–1991` — `EXIT_AUTHORITY` read in `get_settings`

- **File:** `app.py:1984–1991`
- **Classification:** KEEP-DISPLAY (for now; see AX-1)
- **Rationale:** Returns the current `EXIT_AUTHORITY` value in the settings API response, so the UI can render the toggle state. This is a read-only display of the env var.

---

### SITE-D9 — `database.py:1957–1997` — `record_exit_trigger` with `math_mode` / `port_trigger_id` columns

- **File:** `database.py:1957–1997`
- **Classification:** KEEP-DISPLAY
- **Rationale:** The `math_mode` and `port_trigger_id` columns (added in migration 011) are written by SITE-A2 (REMOVE) but the `record_exit_trigger` function also writes per-symphony rows (SITE-A on the per-symphony execution queue, line 1771–1785) with `math_mode=None`. The function itself and the columns are retained for telemetry forensics. After SITE-A2 removal, `math_mode="port_level"` rows will no longer be written, but existing rows remain queryable. The `get_recent_exit_triggers` reader is unambiguously display-only.

---

## §5 AMBIGUOUS Sites — Require PM Decision

### AX-1 — `app.py:2038–2053` — `save_settings` writes `EXIT_AUTHORITY` to `.env`

- **File:** `app.py:2049–2053`
- **Classification:** AMBIGUOUS
- **Why ambiguous:** `save_settings` writes `EXIT_AUTHORITY` to the env file when the settings UI changes it. This is operator-triggered (POST to `/api/settings`). But with port-level math removed, the `EXIT_AUTHORITY` toggle is meaningless — there is no port-level path for it to enable. The question is whether:
  - (a) the toggle should be **removed from settings entirely** (UI, API read/write, env var), or
  - (b) the env var write should be **left as a harmless dead key** (simplest short-term option).
- **PM Decision Required:** Option (a) is cleaner but requires coordinating template + API + env changes. Option (b) is safe but leaves dead settings UI. This choice also drives what happens to `get_exit_authority_badge_context` (AX-2).

---

### AX-2 — `engine/exit_authority.py:122–181` — `get_exit_authority_badge_context` and `build_restart_notice_context`

- **File:** `engine/exit_authority.py:122–181`
- **Classification:** AMBIGUOUS
- **Why ambiguous:** These two functions produce badge context for the dashboard UI (the EXIT_AUTHORITY mode indicator badge and restart notice). They are display-only (no side effects). However, their existence depends on whether the EXIT_AUTHORITY toggle remains in the UI. If AX-1 resolves to option (a) (remove the toggle entirely), these functions are removed. If AX-1 resolves to option (b), they may be kept as display helpers (reclassified KEEP-DISPLAY). The badge is currently rendered in the settings template.
- **PM Decision Required:** Depends on AX-1 resolution.

---

### AX-3 — `database.py:1860–1908` — `new_day_reset_port_state` + `rebase_port_state_on_composition_change`

- **File:** `database.py:1860–1908`
- **Classification:** AMBIGUOUS
- **Why ambiguous:** `rebase_port_state_on_composition_change` is called only from SITE-A2 (REMOVE) — it is clearly dead once the dispatch block is gone. `new_day_reset_port_state` is called nowhere in production code (confirmed via grep: zero hits in `alpha_bot_execution.py` and `app.py`). It is either a planned hook not yet wired in, or an orphaned helper. In either case: once SITE-A2 and SITE-A3 are removed, `port_state` writes stop — but the table rows persist (display-read path SITE-D4 still reads them). Whether to retain `new_day_reset_port_state` as a maintenance helper or remove it as dead code is a PM judgement call.
- **PM Decision Required:** `rebase_port_state_on_composition_change` is unambiguous REMOVE (no live caller after SITE-A2). `new_day_reset_port_state` — PM to decide.

---

## §6 Test Coverage — `tests/portmode/`

| Test File | Status After Removal |
|-----------|---------------------|
| `test_autotuner_portmode.py` | DELETE — tests SITE-B1 exclusively |
| `test_dual_altitude_state.py` | DELETE — tests `compute_for_altitude` and `initialize_port_state_if_absent` (SITE-C1, SITE-A3) |
| `test_exit_authority.py` | DELETE (mostly) — tests decision-path toggle; only `build_restart_notice_context` tests may survive if AX-2 resolves to KEEP |
| `test_port_aggregator.py` | DELETE — tests `aggregate_to_port` which feeds SITE-A2 exclusively |
| `test_port_selector.py` | DELETE — tests `select_symphony`, `select_symphony_with_mc_gate` which are SITE-A2 consumers |
| `test_port_signal.py` | DELETE — tests `build_port_signal` and `build_port_signal_with_authority`, both SITE-A2 consumers |
| `test_mc_sanity_gate.py` | DELETE — tests MC sanity gate in port-mode selection (SITE-A2 consumer) |
| `test_port_state_exit_lifecycle.py` | DELETE — tests lifecycle driven by SITE-A2 and SITE-A3 |
| `test_composition_change_reset.py` | DELETE — tests `rebase_port_state_on_composition_change` (AX-3 REMOVE) |
| `test_per_account_params.py` | DELETE — tests port-mode per-account params used in SITE-A2 |
| `test_port_telemetry.py` | DELETE — tests telemetry written by SITE-A2 |
| `test_settings_exit_authority_route.py` | AMBIGUOUS — depends on AX-1/AX-2 resolution |
| `test_settings_restart_notice.py` | AMBIGUOUS — depends on AX-2 resolution |
| `test_port_state_schema.py` | KEEP (partially) — schema tests for `port_state` table are valid display-layer tests; lifecycle write tests within the file will need pruning |
| `test_dual_altitude_dashboard.py` | KEEP (partially) — the `port_state` display in `/api/state` (SITE-D1/D2) is retained; test sections driving decision-path behavior need pruning |
| `test_api_state_route_additive_fields.py` | KEEP — tests additive fields on `/api/state` which includes `port_state` display (SITE-D1/D2); review for EXIT_AUTHORITY toggle assertions if AX-1 option (a) |
| `test_multi_cycle_convergence.py` | REVIEW — may test observable convergence behavior without exercising the removed execution path; needs inspection before deletion |
| `test_derive_target_reduction_unknown_reason.py` | DELETE — tests `_derive_target_reduction` (part of `port_aggregator.py`, feeds SITE-A2) |
| `test_drawdown_degenerate_inputs.py` | KEEP — tests `_compute_max_drawdown_from_series` (pure math, potentially useful for display-layer portfolio metrics) |
| `test_tie_epsilon_docstring.py` | DELETE — tests tie-epsilon in `port_selector.py` (SITE-A2 consumer) |

---

## §7 Scope Coverage

### Files read in full:
- `alpha_bot_execution.py` (lines 1–1810+, with full coverage of port-related sections)
- `autotuner.py` (lines 210–643, port-mode sections)
- `math_engine.py` — confirmed: **zero port-aggregate math**; no port-level VaR, CVaR, or aggregate signal functions present
- `app.py` (full grep + targeted reads)
- `database.py` (port-related sections: 520–598, 1760–1908, 1950–2020)
- `templates/_chrome.html` (full)
- `templates/index.html`, `settings.html`, `performance.html`, `history.html`, `ai_advisor.html` — grep-confirmed: no port-state, port-level, or exit-authority tokens in templates (except `settings.html` line 888 re: live-mode liquidation warning — display-only)
- `engine/dual_altitude.py` (full)
- `engine/exit_authority.py` (full)
- `port_aggregator.py` (full)
- `port_selector.py` (full)
- `tests/portmode/` (all 22 test files — full survey, selected reads)
- `migrations/010_port_state.sql`, `011_exit_triggers_port.sql`, `012_autotune_runs_portmode.sql` (full)

### Files confirmed out-of-scope (no port-aggregate decision math):
- `math_engine.py` — no `port_level`, `port_state`, or aggregate-math functions found
- `reporting.py` — not read (no port dispatch references found in grep sweep)
- `synthetic_history.py` — not read (no port-mode call sites found in grep sweep)
- `analytics.py` — not read (display/analytics layer; out-of-scope per mandate)
- `engine/params.py`, `engine/multi_cycle.py` — not read (no port-mode dispatch references in grep sweep)

---

## §8 Suggested Cycle Assignment

| Cycle Name | Sites | Dependency |
|------------|-------|------------|
| `port-execution-dispatch-removal` | SITE-A1, SITE-A2, SITE-A3, SITE-A4, SITE-A5 | Apex — no prerequisites within this manifest |
| `port-autotuner-removal` | SITE-B1 | After `port-execution-dispatch-removal` |
| `port-engine-module-removal` | SITE-C1 (decision-path functions) | After `port-execution-dispatch-removal` |
| `port-settings-toggle-cleanup` | AX-1, AX-2 (pending PM decision) | After PM resolves AX-1 |
| `port-db-write-helpers-cleanup` | AX-3 (`rebase_port_state_on_composition_change`) | After `port-execution-dispatch-removal`; `new_day_reset_port_state` pending PM decision |
| `port-test-cleanup` | All DELETE entries in §6 | After respective source cycles complete |
