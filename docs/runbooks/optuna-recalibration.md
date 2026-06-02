# Runbook: Optuna Recalibration After a Calibration-Shifting Change

**When to use:** A code change ships that alters the behavioral semantics of the signals or math that Optuna scores during walk-forward optimization. Existing `optuna_studies.db` trial history was scored against pre-change behavior and must be discarded before the next tune cycle produces valid parameters.

**Canonical trigger:** Task #24 (commits `cd79430`–`945d7d6`) — `synthetic_history.py` now emits `valid_vwap_weight` per tick and the autotuner simulation consumes it via `compute_vwap_breakdown_update`. Trial scores in any pre-task-#24 `optuna_studies.db` reflect a VWAP coverage model that no longer exists.

---

## Section 1 — When to Use This Runbook

This runbook is triggered when an operator becomes aware that a merged change shifts **how Optuna scores a trial** during `run_autotuner`. The symptom is not a crash or error — it is silent parameter staleness: the stored `symphony_strategies` parameters were tuned against a signal model that no longer matches live behavior.

Indicators that a review is warranted:
- A commit message references a change to `synthetic_history.py` tick output fields
- A commit message references a change to VWAP gating, MC threshold, exit signal logic, or the `run_simulation` inner loop in `autotuner.py`
- A PR review flags calibration drift (as quant-code-reviewer did on task #24)
- The autotuner has not been re-run since a math-layer change that is NOT byte-equivalent

---

## Section 2 — Decision Tree: Does My Change Require Recalibration?

Work through this tree before touching `optuna_studies.db`.

```
Is this change to a file that affects the autotuner's per-tick simulation?
  (synthetic_history.py tick fields, math_engine functions called in run_simulation,
   or the run_simulation loop itself in autotuner.py)
  |
  NO ──> No recalibration needed.
  |
  YES
  |
  Is it a pure refactor — byte-equivalent math, no change to signal values or
  coverage logic (e.g., extracting a function into math_engine with identical arithmetic)?
  |
  YES ──> No recalibration needed.
          Examples: math_engine extraction cycles 1-10 (vol-scaling, time-squeeze,
          parabolic-arm, breakeven) — these are all byte-equivalent swaps confirmed
          by golden-fixture tests.
  |
  NO (behavior changes: new tick fields, changed gate thresholds, new exit signals,
      modified scoring weights in run_simulation)
  |
  Is there a provenance audit on record that clears this specific change?
  |
  YES ──> No recalibration needed.
          Example: task #25 (Alpaca feed=iex pin) — cleared by
          docs/research/alpaca/optuna-provenance-audit.md. The audit proves
          synthetic_history.py always used IEX; the live-path fix never touched
          the optimization pipeline.
  |
  NO
  |
  ==> RECALIBRATION REQUIRED. Continue to Section 3.
```

### Concrete examples from this project

| Task | Change | Recalibration? | Reason |
|------|--------|----------------|--------|
| #24 (VWAP calibration drift) | `synthetic_history.py` emits `valid_vwap_weight` per tick; autotuner consumes it via `compute_vwap_breakdown_update` | **YES** | Changes VWAP coverage scoring in every trial |
| #25 (Alpaca feed=iex pin) | `alpha_bot_execution.py` Alpaca calls pinned to IEX | **NO** | Provenance-cleared: autotuner pipeline was always IEX-clean. See `docs/research/alpaca/optuna-provenance-audit.md` |
| Math-engine extraction cycles 1–10 | Inline math extracted to canonical `math_engine` functions | **NO** | Byte-equivalent — golden-fixture tests confirm identical output |
| MC threshold change (hypothetical) | `TRIGGER_THRESHOLD_PCT` range in `objective()` widened | **YES** | Changes the trial search space and all historical scores are against the old range |

---

## Section 3 — Pre-Conditions

Before proceeding, confirm all three conditions are met:

1. **Market is closed** — autotuner runs inside `alpha_bot_execution.py`'s EOD path and via `app.py`'s `/api/force_eod` endpoint. Running recalibration during market hours risks interleaving with a live cycle.

2. **`app.py` daemon is paused** — the scheduler in `app.py` spawns execution subprocesses at :00 every minute. It must not be running while you rename `optuna_studies.db`.

3. **`alphabot_state.db` backup taken** — `symphony_strategies` rows in `alphabot_state.db` hold the currently-active parameters. Back this up before recalibration in case the new tune produces a worse result and you need to roll back.

   ```bash
   cp alphabot_state.db alphabot_state.db.bak_$(date +%Y%m%d)
   ```

---

## Section 4 — Procedure

### Step 1 — Stop the `app.py` daemon

Use whatever process manager your deployment uses (systemd, supervisor, PM2, or a manual kill). The intent is: no Python process running `app.py` or `alpha_bot_execution.py` should be active.

Verify nothing holds the SQLite files:

```bash
# Linux/macOS
lsof alphabot_state.db optuna_studies.db 2>/dev/null

# Windows (PowerShell)
# No lsof equivalent; confirm via your process manager before proceeding
```

### Step 2 — Rename (do not delete) `optuna_studies.db`

Renaming preserves the trial history for forensics in case you need to compare old vs. new parameter distributions.

```bash
mv optuna_studies.db optuna_studies.db.bak_$(date +%Y%m%d)
```

On Windows (PowerShell):

```powershell
Rename-Item optuna_studies.db "optuna_studies.db.bak_$(Get-Date -Format yyyyMMdd)"
```

The file is `.gitignore`d and lives at the project root alongside `alphabot_state.db`. After renaming, no `optuna_studies.db` should exist in the project root.

### Step 3 — Invoke the autotuner

`run_autotuner` in `autotuner.py` has no standalone CLI entry point. It is invoked via the Flask dashboard's force-EOD endpoint. With `app.py` running (or restarted in a controlled way), trigger recalibration via:

```bash
curl -X POST http://localhost:5000/api/force_eod
```

Or, if you prefer to avoid restarting the daemon, write a minimal bootstrap script that loads state and calls `run_autotuner` directly:

```python
# recalibrate.py — run from the project root with the venv active
import database
import autotuner
from datetime import datetime, timedelta
from dotenv import dotenv_values

env = dotenv_values(".env")
account_uuids = [
    uid for uid in [
        env.get("ACCOUNT_INDIVIDUAL", ""),
        env.get("ACCOUNT_ROTH", ""),
        env.get("ACCOUNT_TRAD", ""),
    ] if uid
]

bot_state = database.load_state()
current_date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

print(f"Starting recalibration for {current_date_str}...")
results = autotuner.run_autotuner(bot_state, current_date_str, account_uuids, is_forced=True)
print("Recalibration complete.")
print(results)
```

```bash
python recalibrate.py
```

**About `is_forced` and the Friday/weekend gate.** The gate that skips the autotuner on non-Friday weekdays lives in `alpha_bot_execution.py` line 415 (the caller), not inside `autotuner.py`. `run_autotuner` itself runs unconditionally — it has no internal day-of-week gate. This means the bootstrap script above calls `run_autotuner` directly and it will always run regardless of day or the `is_forced` value. `is_forced=True` in the bootstrap script is only meaningful if you route through `alpha_bot_execution.py`'s caller-side gate — when using the bootstrap script directly, the parameter has no effect but is harmless to include. The Flask `/api/force_eod` endpoint routes through that caller path and passes `is_forced=True` to bypass the gate (see `app.py` line 183).

### Step 4 — Wait for completion

The autotuner runs one Optuna study per active symphony. Runtime per symphony:

- **Window:** 250 trading days of synthetic history, split 60/20/20 (60% train / 20% validation / 20% frozen-eval; `autotuner.py` constants `TRAIN_RATIO`, `VALIDATION_RATIO`, `FROZEN_EVAL_RATIO`). Approximately 150 raw train days; ~29 usable validation days after purge (20 days) and embargo (1 day).
- **Trials:** 500 per symphony with the TPE sampler; PHASE-3 PBO gate applied to the top-20 pre-BHY configs.
- **Parallelism:** `n_jobs` is sourced from the `OPTUNA_N_JOBS` environment variable via `_resolve_optuna_n_jobs_from_env()`; defaults to `1` (NOT `-1`) for SQLite RDBStorage safety. Increase `OPTUNA_N_JOBS` explicitly if parallelism is desired.
- **Per-trial cost:** Each trial replays a full intraday tick simulation (~390 ticks/day × 150 training days = ~58,500 ticks per trial). At ~50–200ms per trial, expect **2–8 minutes per symphony** at `n_jobs=1`.
- **Total:** Multiply by the number of active symphonies in `bot_state`. Three symphonies → approximately 3–15 minutes total. The exact elapsed time is printed to stdout per symphony: `Optimization completed in {elapsed:.2f}s` (`autotuner.py` line 367).

These are honest estimates. Actual timing depends on host hardware and symphony tick density. Let the process run to completion — do not interrupt.

Progress is logged to stdout:

```
-> Starting EOD Autotune (250-day WFA: 60% Train / 20% Validation / 20% Frozen-Eval per Symphony)...
   Optimizing Symphony: my_symphony_name
     OOS validation passed! OOS Guard Alpha: +2.34% (Average: 0.09%)
     Optimization completed in 87.42s. Train Alpha: +5.61% (Average: 0.06%)
-> Autotuner finished all symphonies.
```

### Step 5 — Verify new `symphony_strategies` rows

Confirm that each active symphony has an updated row in `alphabot_state.db`:

```bash
sqlite3 alphabot_state.db "SELECT symphony_name, parameters FROM symphony_strategies;"
```

Every symphony in your active `bot_state` should appear. Cross-check that the normalized symphony names in the output match the names reported during the autotuner run.

---

## Section 5 — Verification

### 5a — Spot-check parameters against the backup

Open both the new `alphabot_state.db` and the backup from Step 3 of Section 3:

```bash
sqlite3 alphabot_state.db.bak_YYYYMMDD "SELECT symphony_name, parameters FROM symphony_strategies;"
sqlite3 alphabot_state.db              "SELECT symphony_name, parameters FROM symphony_strategies;"
```

Confirm that at least one parameter differs for each symphony. If all parameters are identical, the recalibration either loaded a stale study (check that no `optuna_studies.db` existed before Step 2) or `run_autotuner` did not complete successfully.

### 5b — Confirm OOS validation outcome in stdout

Each symphony's stdout should report one of:

- `OOS validation passed!` — new parameters adopted
- `OOS validation failed ... Reverting to Fallback parameters` — pre-existing fallback parameters kept
- `OOS validation & Fallback failed. Resetting to Global Default` — global defaults applied

In all three cases the `symphony_strategies` row is written. Any outcome other than the first should be investigated before resuming live trading.

### 5c — Confirm new `optuna_studies.db` was created

```bash
ls -lh optuna_studies.db
```

A new file should exist at the project root, created during the run. This file holds the 500-trial study for each symphony calibrated against the post-fix tick model.

---

## Section 6 — Resume the Daemon

Once verification in Section 5 passes, restart `app.py` via your process manager. Confirm the startup log shows no errors and that the next scheduled :00 cycle runs cleanly.

---

## Section 7 — What NOT to Do

- **Do not recalibrate during market hours.** The autotuner's EOD path and the live execution path both write to `alphabot_state.db`. Interleaving them risks parameter overwrites mid-cycle.

- **Do not reuse study names.** Optuna study names in this project follow the pattern `{timestamp}__{normalized_name}` (set at `autotuner.py:2215-2218`) with `load_if_exists=False`. Each recalibration run creates a fresh study. If `optuna_studies.db.bak_*` is restored and reused under the same timestamp prefix, a naming collision is possible — start fresh with a new `optuna_studies.db` every recalibration. See the Known Gotchas in `.claude/CLAUDE.md`: "Walk-forward study names: `<timestamp>__<symphony>`; never reuse a study name."

- **Do not skip the provenance check.** If a change has a cleared provenance audit on record, recalibration is unnecessary and wastes 15–30 minutes of compute. Always run the decision tree in Section 2 first. Example: task #25's IEX feed-pin fix is explicitly cleared — see `docs/research/alpaca/optuna-provenance-audit.md`.

- **Do not delete `optuna_studies.db.bak_*` files immediately.** These files contain the pre-fix trial history needed to compare old vs. new parameter distributions and to diagnose unexpected behavior changes. Retain until the next quarterly cleanup.

- **Do not recalibrate for a byte-equivalent refactor.** Math extractions into `math_engine` that pass golden-fixture tests produce identical per-tick outputs. The study scores are unaffected. Running an unnecessary recalibration discards valid trial history.

---

## Walk-forward architecture notes (Phase 1/2/3 overhaul)

This runbook covers recalibration mechanics. For architecture context on the walk-forward phases introduced after the initial recalibration runbook was written:

- **Phase 1** (window expansion): `_WALK_FORWARD_TRADING_DAYS` increased from 125 to 250. Yields ~29 usable validation days vs. the prior ~4 days.
- **Phase 2** (CPCV): `_generate_cpcv_folds` assembles N=6 groups, k=2 test groups, C(6,2)=15 splits, φ=5 OOS backtest paths. Each trial scores across all 15 CPCV splits and persists `cscv_date_returns` in its user attributes.
- **Phase 3** (PBO gate): `compute_pbo` on the top-`_CSCV_TOP_K` (=20) pre-BHY configs. PBO > `PBO_REJECT_THRESHOLD` (=0.5) vetoes as STAGE-1 before BHY. This is a sample-robustness check orthogonal to the BHY multiplicity correction.
- **`locked_vars`**: Optuna search space gates every `suggest_*` call on `if KEY not in locked_vars`. Operator-locked parameters are excluded from the trial search.

All three phases are consistent with recalibration triggers in Section 2 (any change to the scoring model — tick fields, gate thresholds, or the fold structure — requires recalibration).

---

## Related Runbooks

- `docs/runbooks/composer-rejection-diagnostic.md` — Composer API rejection triage procedure

## Related Research

- `docs/research/alpaca/optuna-provenance-audit.md` — **The canonical counter-example.** This audit proves that the Alpaca feed=iex fix (task #25) did NOT require recalibration because the optimization pipeline was always IEX-clean. Read this before concluding that a data-source change requires recalibration — the architecture may already isolate the autotuner from the affected path.
