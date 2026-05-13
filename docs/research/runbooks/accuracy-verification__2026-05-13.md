# Runbook Accuracy Verification — 2026-05-13

**Scope:** Three operator runbooks verified against HEAD (`7586985`, branch `main`).
**Method:** Read each runbook, extract every code citation and behavioral claim, open the cited file at the cited location, compare claim to actual code.
**Constraints:** Read-only for runbooks + code.

---

## 1. `docs/runbooks/composer-rejection-diagnostic.md`

### Citations extracted

| # | Runbook claim | Cited location |
|---|---|---|
| C1 | `[COMPOSER REJECTED]: HTTP {code}` originates from `fetch_symphony_stats` at **line ~88** | `alpha_bot_execution.py:~88` |
| C2 | `execute_sell_to_cash` emits `[COMPOSER REJECTED]` / `[COMPOSER ERROR]` at **lines ~115/120** | `alpha_bot_execution.py:~115, ~120` |
| C3 | HTTP 429 — AlphaBot handles automatically using **60s default `Retry-After` fallback** | `execute_sell_to_cash` retry logic |
| C4 | HTTP 500 — **auto-retries with backoff (1s, 2s, 4s, 10s)** | `execute_sell_to_cash` backoff logic |
| C5 | Log format defined at `alpha_bot_execution.py:89, :115, :120` | Same |
| C6 | Auth header construction at `alpha_bot_execution.py:73-78` | `get_composer_headers()` |

### Per-citation verdicts

**C1 — `fetch_symphony_stats` at line ~88, emitting `[COMPOSER REJECTED]`**

Code at line 93 (the error print inside `fetch_symphony_stats`):
```python
print(f"Error fetching account {account_id}: HTTP {response.status_code}")
```
The actual log message is `Error fetching account {account_id}: HTTP {status_code}` — NOT `[COMPOSER REJECTED]: HTTP {code}`. The `[COMPOSER REJECTED]` log format only exists inside `execute_sell_to_cash` (line 124). `fetch_symphony_stats` uses a different, non-bracketed format.

The runbook's Step 1 table states the `[COMPOSER REJECTED]: HTTP {code}` log can originate from `fetch_symphony_stats`. That is incorrect. The triggering log format stated in the runbook's opening ("When to use: `[COMPOSER REJECTED]: HTTP {status_code}`") only fires from `execute_sell_to_cash`.

Verdict: **INCORRECT** — `[COMPOSER REJECTED]` is not emitted by `fetch_symphony_stats`. Its format is `Error fetching account {account_id}: HTTP {status_code}`.

Line number drift: `fetch_symphony_stats` error print is at line 93, not ~88 (the `~88` annotation refers loosely to the function start). Minor drift only — the substantive error is the wrong log format claim.

**C2 — `execute_sell_to_cash` emits `[COMPOSER REJECTED]` at lines ~115/120**

Code at line 124:
```python
print(f"     !!! [COMPOSER REJECTED]: HTTP {response.status_code}")
```
Code at line 119:
```python
print(f"     !!! [COMPOSER ERROR HTTP {response.status_code}]")
```
Both formats exist and fire from `execute_sell_to_cash`. Lines are 119 and 124, vs the runbook's "~115/120". The function begins at line 98.

Verdict: **DRIFT** — formats and behavioral assignment are correct; actual lines are 119 and 124, not ~115/120.

**C3 — HTTP 429 uses 60s default `Retry-After` fallback**

Code at lines 112–114:
```python
if response.status_code == 429:
    retry_after = int(response.headers.get("Retry-After", 60))
    print(f"     !!! [RATE LIMIT HIT 429] Sleeping for {retry_after}s...")
```
Default fallback is 60s. Claim is accurate.

Verdict: **VERIFIED**

**C4 — HTTP 500 auto-retries with backoff (1s, 2s, 4s, 10s)**

Code at line 100:
```python
backoff_intervals = [1, 2, 4, 10]
```
Used at line 117–121 for `status_code >= 500`. Sequence matches exactly.

Verdict: **VERIFIED**

**C5 — Log format defined at `alpha_bot_execution.py:89, :115, :120`**

`[COMPOSER REJECTED]` is at line 124. `[COMPOSER ERROR HTTP]` is at line 119. `fetch_symphony_stats` error print is at line 93. None of the cited line numbers (89, 115, 120) are exact matches.

Verdict: **DRIFT** — behavioral content is accurate; all three line numbers are off by 4–9 lines.

**C6 — Auth header construction at `alpha_bot_execution.py:73-78`**

`get_composer_headers()` spans lines 72–77:
```python
def get_composer_headers(key=None, secret=None):
    return {
        "x-api-key-id": key or COMPOSER_KEY_ID,
        "authorization": f"Bearer {secret or COMPOSER_SECRET}",
        "Content-Type": "application/json",
    }
```
Range cited is 73-78; actual is 72-77. One line off — trivial drift.

Verdict: **DRIFT**

### Runbook 1 totals

| VERIFIED | DRIFT | INCORRECT | AMBIGUOUS |
|---|---|---|---|
| 2 | 3 | 1 | 0 |

### Action items for runbook 1

1. **Fix Step 1 table** — `fetch_symphony_stats` does NOT emit `[COMPOSER REJECTED]: HTTP {code}`. Its error log is `Error fetching account {account_id}: HTTP {status_code}`. Update the "Call site" table to reflect the actual log format for each function, or clarify that the "When to use" trigger (`[COMPOSER REJECTED]`) is specific to `execute_sell_to_cash`.
2. **Update line references** — Correct all cited line numbers:
   - `fetch_symphony_stats` error print: line 93 (not ~88)
   - `[COMPOSER ERROR HTTP]`: line 119 (not ~115)
   - `[COMPOSER REJECTED]`: line 124 (not ~120)
   - Related code references section: update `:89, :115, :120` to `:93, :119, :124`
   - Auth header: update `73-78` to `72-77`

---

## 2. `docs/runbooks/tzdata-missing-on-host.md`

### Citations extracted

| # | Runbook claim | Cited location |
|---|---|---|
| T1 | `ZoneInfoNotFoundError` / `KeyError: 'America/New_York'` surfaces inside `get_current_et()` at **lines 262–270** | `alpha_bot_execution.py:262–270` |
| T2 | `get_current_et()` catches `(ImportError, KeyError)` and falls back to hardcoded UTC-offset; **does not crash, does not skip tick, does not log a warning** | `get_current_et()` except branch |
| T3 | Fallback: months 3–11 → UTC−4 (EDT); months 12–2 → UTC−5 (EST) | `get_current_et()` except branch |
| T4 | Pre-cycle #28, catch clause was `except Exception`; cycle #28 narrowed it to `except (ImportError, KeyError)` | Code history claim — code state only |
| T5 | Runbook instructs adding temporary `print` inside `except` branch at lines **267–270** | `alpha_bot_execution.py:267–270` |
| T6 | `get_current_et()` fallback affects only `main()`'s `is_weekday` check and market-hours gate against `EXECUTION_START_TIME` / `EXECUTION_END_TIME` | `main()` usage of `get_current_et()` |

### Per-citation verdicts

**T1 — `get_current_et()` at lines 262–270**

Code at lines 262–270:
```python
def get_current_et():
    utc_now = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except (ImportError, KeyError):
        if 3 <= utc_now.month <= 11:
            return utc_now - timedelta(hours=4)
        return utc_now - timedelta(hours=5)
```
Lines 262–270 are exact. Function definition through the end of the except branch lands precisely in that range.

Verdict: **VERIFIED**

**T2 — Silent fallback: no crash, no skipped tick, no log warning**

The `except (ImportError, KeyError)` branch contains zero `print` or `logging` calls. It silently returns an offset-based datetime. The runbook's claim that AlphaBot "does not log a warning" is accurate.

Verdict: **VERIFIED**

**T3 — Offset math: months 3–11 → UTC−4; otherwise UTC−5**

Code:
```python
if 3 <= utc_now.month <= 11:
    return utc_now - timedelta(hours=4)
return utc_now - timedelta(hours=5)
```
Matches exactly, including the boundary (month 3 = March, month 11 = November inclusive).

Verdict: **VERIFIED**

**T4 — Pre-cycle #28 used `except Exception`; narrowed to `except (ImportError, KeyError)` in cycle #28**

Current code shows `except (ImportError, KeyError)`. Git history is not examined here (read-only scope is code, not full commit log), but the current state matches the "post-cycle #28" claim. Whether the pre-change form was `except Exception` cannot be confirmed from code alone.

Verdict: **AMBIGUOUS** — current code matches the "after" state; pre-change form unverifiable without reading git history.

**T5 — Lines 267–270 as the except branch for adding a debug print**

Lines 267–270:
```
267:    except (ImportError, KeyError):
268:        if 3 <= utc_now.month <= 11:
269:            return utc_now - timedelta(hours=4)
270:        return utc_now - timedelta(hours=5)
```
Exactly the except block. Instruction is accurate.

Verdict: **VERIFIED**

**T6 — Fallback affects only `is_weekday` and market-hours window in `main()`**

`main()` uses `get_current_et()` at line 287:
```python
current_et = get_current_et()
```
Then uses it for:
- `is_weekday = current_et.weekday() < 5` (line 289)
- `current_time = current_et.time()` (line 290) — compared against `market_open` and `post_mortem_cutoff`
- Various downstream uses throughout the function (EOD path gating, rebalance blackout, post-mortem detection)

The runbook says only `is_weekday` and the `EXECUTION_START_TIME`/`EXECUTION_END_TIME` gate are affected. The actual usage is broader — `current_et` also drives the rebalance blackout window check (line 308), EOD path gating (line 389), weekday check for the autotuner (line 415), and the chart history key (line 334). The runbook's operational impact section simplifies this to just two effects.

However, the runbook's operational impact section (Section 6) does explicitly say "The downstream effect is limited to `main()` in `alpha_bot_execution.py`, which uses the returned time to: 1. Check `is_weekday` ... 2. Compare `current_time` against `EXECUTION_START_TIME` and `EXECUTION_END_TIME`." The rebalance blackout and EOD-path gating are also `current_time`-based gates that would be affected during DST transitions.

The runbook's conclusion ("No positions are affected mid-cycle; no Guard Alpha logic is bypassed; no API calls are skipped") is accurate for mid-cycle execution. The drift risk is around start/end-of-day timing, which the runbook correctly identifies. The simplification in the bulleted list is an incomplete enumeration of `current_et` usages, but the operational conclusion is correct.

Verdict: **AMBIGUOUS** — the conclusion is correct; the enumerated list of affected gates is incomplete (omits rebalance blackout and EOD path).

### Runbook 2 totals

| VERIFIED | DRIFT | INCORRECT | AMBIGUOUS |
|---|---|---|---|
| 4 | 0 | 0 | 2 |

### Action items for runbook 2

1. **Section 6 — expand the list of gates affected by `get_current_et()` fallback.** The current bullet list cites only `is_weekday` and the market-open/close window. Also affected (all timing-sensitive): (a) rebalance blackout check (`rebalance_blackout <= current_time < market_close` at line 308), (b) EOD post-mortem path entry (`market_close <= current_time <= post_mortem_cutoff` at line 389), (c) autotuner weekday gate (`current_et.weekday() >= 4` at line 415). None of these change the runbook's core operational conclusion (no mid-cycle Guard Alpha impact, no position effects), but the section should not claim only two gates are affected.
2. **T4 pre-change form** — if the runbook is intended to document the reason for the current form (not just the current state), the historical claim that the old clause was `except Exception` should be cited against the specific commit (`d74e7d3`) rather than stated as fact here. Low priority.

---

## 3. `docs/runbooks/optuna-recalibration.md`

### Citations extracted

| # | Runbook claim | Cited location |
|---|---|---|
| O1 | `run_autotuner` has no standalone CLI; invoked via Flask `/api/force_eod`; endpoint always passes `is_forced=True` at **`app.py` line 183** | `app.py:183` |
| O2 | Outside Friday/weekend, `alpha_bot_execution.py` only runs autotuner when `is_forced=True`; gated at **`autotuner.py` line 415** | `alpha_bot_execution.py:415` (note: runbook says `autotuner.py line 415` — this is likely a typo for `alpha_bot_execution.py`) |
| O3 | 80/20 split: training ~100 days, OOS ~25 days; split at `autotuner.py` **line 98** | `autotuner.py:98` |
| O4 | **500 trials** per symphony; `n_jobs=-1`; both at `autotuner.py` **line 308** | `autotuner.py:308` |
| O5 | Study name = `normalized_symphony_name`; `load_if_exists=True`; both at `autotuner.py` **line 307** | `autotuner.py:307` |
| O6 | `Optimization completed in {elapsed:.2f}s` log at `autotuner.py` **line 367** | `autotuner.py:367` |
| O7 | WFA window: **125 trading days** noted at `autotuner.py` **line 69** | `autotuner.py:69` |
| O8 | Train/test split at `autotuner.py` **lines 69, 98** | `autotuner.py:69, 98` |
| O9 | Bootstrap script in runbook calls `autotuner.run_autotuner(bot_state, current_date_str, account_uuids, is_forced=True)` | Runbook example code |
| O10 | Study name note in "What NOT to Do": study names follow `<normalized_symphony_name>` pattern via `database.normalize_name()` at `autotuner.py` **line 307** | `autotuner.py:307` |

### Per-citation verdicts

**O1 — `/api/force_eod` always passes `is_forced=True`, at `app.py` line 183**

Code at `app.py:183`:
```python
autotuner_changes = autotuner.run_autotuner(bot_state, prev_date_str, account_uuids, is_forced=True)
```
Exact line match, `is_forced=True` confirmed.

Verdict: **VERIFIED**

**O2 — Autotuner gate "only runs on Fridays/weekends" outside `is_forced=True`, attributed to `autotuner.py` line 415**

The runbook text says "`autotuner.py` line 415". The actual gating logic is in `alpha_bot_execution.py` at line 415:
```python
if current_et.weekday() >= 4 or force_run: # 4=Fri, 5=Sat, 6=Sun
```
`autotuner.py` has no gating logic at line 415 (line 415+ is just the closing print and return of `run_autotuner`). The behavioral claim is correct — the gate exists and does what the runbook says. The file attribution is wrong.

Note: `force_run` in `alpha_bot_execution.py` is derived from `"--force" in sys.argv` (line 286), NOT from the `is_forced` parameter of `run_autotuner`. The Flask endpoint uses `is_forced=True` when calling `run_autotuner` directly. In the subprocess path from `alpha_bot_execution.py`, the gate is `force_run` (the `--force` CLI flag). These are two separate invocation paths; both work correctly.

Verdict: **INCORRECT** — file attribution is wrong (says `autotuner.py` line 415, should be `alpha_bot_execution.py` line 415).

**O3 — 80/20 split at `autotuner.py` line 98**

Code at line 98:
```python
split_idx = int(total_days * 0.8)
```
Exact line match.

Verdict: **VERIFIED**

**O4 — 500 trials, `n_jobs=-1`, both at `autotuner.py` line 308**

Code at line 308:
```python
study.optimize(objective, n_trials=500, n_jobs=-1)
```
Both values confirmed at line 308.

Verdict: **VERIFIED**

**O5 — Study name = `normalized_symphony_name`, `load_if_exists=True`, at `autotuner.py` line 307**

Code at line 307:
```python
study = optuna.create_study(study_name=normalized_name, storage=storage, load_if_exists=True, direction="maximize")
```
Both claims confirmed at line 307.

Verdict: **VERIFIED**

However, note a discrepancy between runbook and CLAUDE.md: Section 7 of the runbook ("What NOT to Do") states study names follow `<normalized_symphony_name>` (matching the code), while `.claude/CLAUDE.md`'s Known Gotchas says `<timestamp>__<symphony>`. The code at line 307 uses `normalized_name` (no timestamp). The runbook is correct; CLAUDE.md's gotcha entry is stale or describes a planned future format. This is not an error in the runbook itself.

**O6 — `Optimization completed in {elapsed:.2f}s` at `autotuner.py` line 367**

Code at line 367:
```python
print(f"       Optimization completed in {elapsed:.2f}s. Train Alpha: {best_alpha_train:+.2f}% (Average: {avg_train_alpha:.2f}%)")
```
Line 367, exact match.

Verdict: **VERIFIED**

**O7 — WFA window = 125 trading days at `autotuner.py` line 69**

Code at line 69:
```python
print(f"  -> Starting EOD Autotune (125-day WFA: 80% Train / 20% OOS per Symphony)...")
```
Line 69 confirms the 125-day figure as the documented window. The actual data comes from `synthetic_history.generate_synthetic_history()` (line 81) — the 125-day window is defined there, not enforced numerically at line 69 itself. The runbook's citation of line 69 as the source for this figure is marginally imprecise (it's a print statement, not a constant), but the claim is accurate.

Verdict: **VERIFIED**

**O8 — Train/test split at `autotuner.py` lines 69, 98**

Line 69 is the print. Line 98 is `split_idx = int(total_days * 0.8)`. Both cited correctly.

Verdict: **VERIFIED**

**O9 — Bootstrap script passes `is_forced=True`**

The runbook's example `recalibrate.py` script calls:
```python
results = autotuner.run_autotuner(bot_state, current_date_str, account_uuids, is_forced=True)
```
The `run_autotuner` function signature at line 64 is:
```python
def run_autotuner(bot_state, current_date_str, account_uuids, is_forced=False):
```
The parameter exists. The `is_forced` parameter is accepted but — importantly — `run_autotuner` itself does NOT gate on `is_forced` internally. Looking at the function body, there is no `if is_forced:` guard inside `run_autotuner`. The gating on Friday/force is done by the CALLER (`alpha_bot_execution.py` line 415) before calling `run_autotuner`. So calling `run_autotuner` directly (as the bootstrap script does) will always run regardless of `is_forced`.

The runbook's warning "`is_forced=True` is required. Without it, `alpha_bot_execution.py` only runs the autotuner on Fridays/weekends" is technically correct when invoking through `alpha_bot_execution.py` subprocess path — but when calling `run_autotuner` directly (as the bootstrap does), `is_forced` has no effect inside the function. The bootstrap would run fine with or without `is_forced=True`.

Verdict: **AMBIGUOUS** — the claim is correct for the `alpha_bot_execution.py` code path; misleading (though harmless) for the direct-call bootstrap path where `is_forced` is accepted but not consulted inside `run_autotuner`.

**O10 — "What NOT to Do" note about study names and `database.normalize_name()` at `autotuner.py` line 307**

Already confirmed in O5. Line 307 uses `normalized_name` which comes from `database.normalize_name(data["name"])` (line 113). Accurate.

Verdict: **VERIFIED**

### Runbook 3 totals

| VERIFIED | DRIFT | INCORRECT | AMBIGUOUS |
|---|---|---|---|
| 8 | 0 | 1 | 1 |

### Action items for runbook 3

1. **Fix file attribution in Section 3 Pre-Conditions and Section 4 Step 3.** The claim "`autotuner.py` line 415" should read "`alpha_bot_execution.py` line 415". The Friday/weekend gate is in the execution script, not the autotuner module.
2. **Clarify the `is_forced=True` explanation in the bootstrap script comment.** The current note says "Without it, `alpha_bot_execution.py` only runs the autotuner on Fridays/weekends (`autotuner.py` line 415)." This is misleading for operators using the bootstrap path. Clarify: (a) `is_forced=True` is only enforced by `alpha_bot_execution.py`'s caller gate; (b) when calling `run_autotuner` directly, the function always runs regardless of `is_forced`.
3. **Side note for PM:** CLAUDE.md Known Gotchas says study names follow `<timestamp>__<symphony>`. Code at `autotuner.py:307` uses only `normalized_symphony_name` (no timestamp). The CLAUDE.md entry is stale. Correct it separately from the runbook.

---

## Summary across all three runbooks

| Runbook | VERIFIED | DRIFT | INCORRECT | AMBIGUOUS | Total |
|---|---|---|---|---|---|
| composer-rejection-diagnostic.md | 2 | 3 | 1 | 0 | 6 |
| tzdata-missing-on-host.md | 4 | 0 | 0 | 2 | 6 |
| optuna-recalibration.md | 8 | 0 | 1 | 1 | 10 |
| **Totals** | **14** | **3** | **2** | **3** | **22** |

**Overall verdict:** INCORRECT CLAIMS FOUND (2), DRIFT FOUND (3)

### Critical fixes (operator-safety impact)

1. **composer-rejection-diagnostic.md — C1 (INCORRECT):** The runbook tells operators to look for `[COMPOSER REJECTED]: HTTP {code}` as a symptom from `fetch_symphony_stats`. This format is never emitted there; operators following the diagnostic using this log trigger will find it never fires from the polling path. Fix: distinguish the two log formats per call site.

2. **optuna-recalibration.md — O2 (INCORRECT):** File attribution `autotuner.py line 415` should be `alpha_bot_execution.py line 415`. An operator looking for the gate in the wrong file will not find it.

### Non-critical fixes (line number drift)

composer-rejection-diagnostic.md C2, C5, C6: All line numbers are off by 4–9 lines. Behavior described is correct; only navigation aids are stale.
