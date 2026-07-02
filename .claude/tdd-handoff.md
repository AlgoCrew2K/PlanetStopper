# TDD Handoff — DE-EOD-BASIS-001 (EOD Account-Basis Unification)

Plan: feature-plans/eod-today-change-account-basis.md
Branch: fix/eod-today-change-account-basis
Worktree: C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\.claude\worktrees\eod-basis
Phase: green
RED commit: 95055bb
RED result: 17 FAIL / 13 PASS / 3 SKIP
GREEN result: 33 PASS / 0 FAIL / 0 SKIP

## Test Files

- tests/dashboard/test_eod_account_basis.py   — AC-1, AC-2, AC-3, AC-6, AC-8, AC-9
- tests/app/test_eod_account_basis_refresh.py  — AC-4, AC-10
- tests/test_scope_guard.py                    — AC-5 (activates post-commit)

Fixture: tests/fixtures/dashboard/frozen_portfolio_strip/eod_account_basis_parity.json

## What is broken

Two bugs in the frozen/EOD branch of /api/state (app.py:1750-1834):

Bug 1 — today_change uses raw VW (app.py:1815-1817):
  "today_change": analytics.get_portfolio_today_change(...)
  No account-basis wrap. Denominator = invested capital (cash excluded).

Bug 2 — cumulative_return is half-converted (app.py:1807-1812):
  if_held = _snap_cached_cr  (account basis — correct)
  dry_run = _snap_cr.get("dry_run")  (VW basis — wrong)
  Mixed-basis dict; guard_alpha is a phantom artefact.

Root cause: the live path calls account-basis helpers at app.py:1183-1212, but
the frozen branch was never wired through them.

## What GREEN must add to app.py

### 1. Three new module-level variables (near the existing _account_totals_cache block, ~line 527)

  _account_totals_last_good: dict = {}            # plain dict, NOT _StaleFlagDict
  _account_totals_last_success_at: str | None = None
  _ACCOUNT_TOTALS_HTTP_TIMEOUT_S = 10             # promotes timeout=10 literal at line 769

_account_totals_last_good MUST be a plain dict. It must survive mark_stale() calls
on _account_totals_cache (that is the entire point of last-good retention).

### 2. In _refresh_account_totals (~line 769)

  - Replace `timeout=10` with `timeout=_ACCOUNT_TOTALS_HTTP_TIMEOUT_S`.
  - On successful 200 response (inside the `if resp.status_code == 200:` block):
      _account_totals_last_good.clear()
      _account_totals_last_good.update(<the values being written to cache>)
      _account_totals_last_success_at = <ET timestamp string>
    The timestamp can be any string (e.g., "2026-07-02 09:15:33 ET").
    Do NOT update these on a failed / non-200 response.

### 3. Stale-cache two-tier fallback (applies to BOTH frozen and live paths)

When _account_totals_cache.get(key) returns None (stale), the render path must:

  Tier 1 — last-good present (_account_totals_last_good is non-empty):
    Use _account_totals_last_good.get(key).
    Stamp on portfolio_strip:
      portfolio_strip["account_basis_stale"] = True
      portfolio_strip["account_basis_as_of"] = _account_totals_last_success_at

  Tier 2 — no last-good (fresh restart, dict empty):
    Emit portfolio_strip["basis"] = "value_weighted"
    OR set today_change["if_held"] = None.
    Never return an unlabelled VW value (operator cannot tell it from account basis).

### 4. Frozen branch fix (app.py:1750-1834) — primary fix

After resolving account totals (using warm cache OR last-good fallback):

  # today_change: replace the raw VW call with the account-basis wrap
  _snap_vw_tc = analytics.get_portfolio_today_change(_snap_symphonies_list, ...)
  "today_change": analytics.get_portfolio_cumulative_return_account_basis(
      _snap_vw_tc,
      _snap_portfolio_tc,     # from cache or last-good
      _snap_account_value,
      _snap_symphony_value_sum,
  ),

  # cumulative_return: replace the half-conversion with the account-basis wrap
  _snap_vw_cr = analytics.get_portfolio_cumulative_return(...)
  "cumulative_return": analytics.get_portfolio_cumulative_return_account_basis(
      _snap_vw_cr,
      _snap_portfolio_cr,     # from cache or last-good
      _snap_account_value,
      _snap_symphony_value_sum,
  ),

  _snap_symphony_value_sum = sum(s.get("current_value", 0.0) or 0.0
                                  for s in _snap_symphonies_list)

### 5. Live path fix (app.py:1202-1216) — AC-10 (same bug pattern)

The live path (_compute_portfolio_strip) also silently flips to VW when
_account_totals_cache.get("portfolio_tc") returns None (stale).
Apply the identical two-tier fallback there too.

## Analytics helper signatures (already correct — do not modify these)

  # analytics.py:1083-1157
  def get_portfolio_today_change_account_basis(
      vw_tc: dict,                    # {"if_held": float, "dry_run": float}
      account_if_held_tc: float | None,
      account_value: float,
      symphony_value_sum: float,
  ) -> dict:  # {"if_held": float, "dry_run": float}

  # analytics.py:1024-1080
  def get_portfolio_cumulative_return_account_basis(
      vw_cr: dict,                    # {"if_held": float, "dry_run": float}
      account_if_held: float,
      account_value: float,
      symphony_value_sum: float,
  ) -> dict:  # {"if_held": float, "dry_run": float}

Both have a division guard: return vw_* unchanged when account_value <= 0 or
symphony_value_sum <= 0. Do not add extra guards around these calls.

## Running the tests (run ONLY these 3 files, with -n0)

  cd C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\.claude\worktrees\eod-basis
  python -m pytest tests/dashboard/test_eod_account_basis.py tests/app/test_eod_account_basis_refresh.py tests/test_scope_guard.py -n0 -v

Do NOT run the full test suite. The PM owns the full-tree gate.
Target: 0 FAIL / >=30 PASS / 3 SKIP (scope guard skips already counted in the 3).

## Scope boundaries (AC-5)

DO NOT touch:
  alpha_bot_execution.py
  math_engine.py

The scope guard activates post-commit and FAILS if those files appear in EOD-cycle commits.
If the scope guard FAILS after your GREEN commit: stop, revert the offending file, recommit.

Permitted files:
  app.py           — primary fix location
  analytics.py     — only if a bug is found in the existing helpers (unlikely)

## Hard rules

- NEVER merge to main. NEVER run `git merge`, `git checkout main`, or any command
  that lands cycle work on the main branch. The PM owns the merge gate.
- Verify branch before every commit: `git -C <worktree-path> branch --show-current`
  must print fix/eod-today-change-account-basis, never main.
- Commit prefix: fix(dashboard):
- After GREEN: SendMessage to PM (main) with HEAD SHA and counts. Do NOT push or merge.

## Implementation Notes

- Change 1: Added `_account_totals_last_good: dict = {}`, `_account_totals_last_success_at: str | None = None`,
  `_ACCOUNT_TOTALS_HTTP_TIMEOUT_S = 10` after `_account_totals_cache_lock` at module level (app.py ~530).
- Change 2: `_refresh_account_totals` — replaced `timeout=10` with constant; added last-good snapshot
  (`_account_totals_last_good.clear(); .update(_account_totals_cache)`) inside the lock after
  `refresh_written()`; advanced `_account_totals_last_success_at` (with `global`) after the lock block
  completes without exception (genuine success only, per PM revision).
- Change 3: Frozen branch (~1860-1930 after offsets) — two-tier stale fallback; compute
  `_snap_symphony_value_sum`; wrap both TC and CR through account-basis helpers; single clean
  `_snap_tc_final`/`_snap_cr_final` assignment (no double-assignment, per PM feedback);
  `basis="value_weighted"` + `today_change.if_held=None` on Tier 2; `account_basis_stale=True` +
  `account_basis_as_of` (with `datetime.now()` fallback when `_account_totals_last_success_at` is None)
  on Tier 1.
- Change 4: Live path `_compute_portfolio_strip` — added `_live_basis_stale = False` flag; Tier-1
  last-good fallback on both `_cached_cr` and `_cached_tc` else branches; `basis="value_weighted"`
  on Tier-2; `account_basis_stale`/`account_basis_as_of` stamped on `_strip` when stale.
- `analytics.py` untouched. `alpha_bot_execution.py` untouched. `math_engine.py` untouched.
- One minor scope needed beyond the handoff: `_account_totals_last_success_at` fallback to
  `datetime.now(_ET)` when None (test manually populates `_account_totals_last_good` without
  a prior successful refresh, so the timestamp variable is None at that point).

## Test File Issues (for test-writer to fix)

None. All 33 tests pass cleanly. No test bugs found.

## Status log

- [2026-07-02] quant-test-writer: RED committed (95055bb). 17 FAIL / 13 PASS / 3 SKIP.
  All failures for the right reason. Handing off to eodimpl.
- [2026-07-02] eodimpl: GREEN complete — 33/33 tests passing, 0 test bugs. Lint ✓ Format ✓.
  Handing back to eodtest for sufficiency review (Red/Green/Revise).
