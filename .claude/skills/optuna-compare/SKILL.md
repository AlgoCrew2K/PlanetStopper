---
name: optuna-compare
description: Side-by-side diff of two Optuna walk-forward optimization runs — surfaces parameter shifts, objective deltas, and which trials drove the change.
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write
agent: general-purpose
---

# /optuna-compare

**Usage:** `/optuna-compare <run-id-a> <run-id-b> [<symphony-id>]`

- `run-id-a`, `run-id-b` — required; identifiers for the two runs to compare
- `symphony-id` — optional; restricts comparison to one symphony (default: all)

**Dynamic context:** `!`ls *.db 2>/dev/null``

---

## Steps

**1. Locate the optimization DB**
Glob for `*.db` in the project root. If none found, fail with: `No optimization DB found. Expected a *.db file in the project root.`

**2. Open DB read-only and confirm run-ids**
Query the DB with `sqlite3 <db> "SELECT DISTINCT run_id FROM ..."` (or the equivalent Optuna study/trial tables). If either run-id is missing, list all available run-ids and exit:
```
Run-id '<X>' not found. Available run-ids:
  run_2026_01
  run_2026_02
  ...
```

**3. Pull best-trial parameters per symphony**
For each symphony (filtered by `symphony-id` if provided), retrieve the best-trial parameters from both runs. Use Optuna's `trials` / `trial_params` tables directly via `sqlite3` Bash calls.

**4. Auto-detect objective metric**
Read study metadata (`studies` table or equivalent) to determine what metric the optimizer maximized (e.g., Sharpe, win-rate). Label columns in output accordingly.

**5. Compute deltas**
- Numeric params: `Δ = run_b − run_a`, `%Δ = (Δ / |run_a|) × 100` (guard divide-by-zero)
- Categorical params: show `A → B`; mark as changed or unchanged

**6. Render markdown comparison table**
One table per symphony:

```
### SYMPH-001 — Objective: sharpe_ratio
| Parameter         | run_2026_03 | run_2026_04 | Δ       | %Δ     |
|-------------------|-------------|-------------|---------|--------|
| lookback_window   | 20          | 25          | +5      | +25%** |
| stop_loss_pct     | 0.02        | 0.018       | −0.002  | −10%   |
| entry_signal      | momentum    | mean_revert | —       | A→B    |
| **Objective**     | 1.42        | 1.61        | +0.19   | +13.4% |
```

`**` marks params where `|%Δ| > 20%`.

**7. Summary block**
After all tables, emit a summary:
```
## Candidates for Further Investigation (|%Δ| > 20%)
- SYMPH-001 · lookback_window: 20 → 25 (+25%)
```

---

## Mental walkthrough: `/optuna-compare run_2026_03 run_2026_04 SYMPH-001`

1. Glob finds `alphabot_optuna.db`
2. Both run-ids confirmed present; scope restricted to `SYMPH-001`
3. Best-trial params fetched for both runs on SYMPH-001
4. Metadata shows objective = `sharpe_ratio`
5. Deltas computed; `lookback_window` flagged at +25%
6. Single markdown table rendered for SYMPH-001
7. Summary lists `lookback_window` as investigation candidate

---

## What You Must NOT Do

- **Never write to or modify the optimization DB.** Open with `sqlite3` in read-only mode (`?mode=ro` URI or equivalent).
- **Never re-run trials.** This skill is comparison-only; no Optuna `study.optimize()` calls.
- **Never invent run-ids.** If a requested run-id does not exist in the DB, fail loudly with the available-ids list and exit immediately.
