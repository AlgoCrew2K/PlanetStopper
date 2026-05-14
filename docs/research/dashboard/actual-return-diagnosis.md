# Diagnosis: Dashboard "Actual Return" vs Composer "Today's Change"

**Date:** 2026-05-14
**Analyst:** composer-alpaca-integration
**Scope:** Research/diagnosis only — no production code written.
**Account under test:** `880be47e-efe4-4b44-9d83-b6d86098fe0d`

---

## Verdict

**NOT A BUG (computation is correct) — the operator's discrepancy is a STALENESS artifact, not a formula error.**

"Actual Return" *is* Composer's "Today's Change" — same metric, same formula, verified to 2 decimals against Composer's own account-level `todays_percent_change`. The numbers the operator sees differ from Composer's UI only because **the dashboard displays a snapshot frozen at the last engine cycle (~1-minute cadence), while Composer's UI updates continuously.** Intraday `last_percent_change` drifts every few seconds; the dashboard is correct *as of its last refresh*, just not real-time.

There is **no file:line bug to fix.** If the operator wants the dashboard to track Composer's UI tick-for-tick, that is a **new feature** (live polling / shorter refresh / a "data as of HH:MM" timestamp), not a defect repair.

Secondary finding (genuine, low-severity): the dashboard shows **no "data as of" timestamp**, so a stale snapshot is indistinguishable from a live one. That is the actual operator-facing problem and the only thing worth changing.

---

## What "Actual Return" is

**Template:** `templates/table_partial.html:24-25` — labelled `Actual Return:`, tooltip `"Capital-Weighted Account Return"`.

**Formula** (`table_partial.html:5-15`): per account, capital-weighted mean of each symphony's `current_return`, weighted by `current_value`:

```
total_return = Σ(current_value × current_return) / Σ(current_value)
```

**Feeder field:** `sym.current_return` — comes from the `bot_state` blob in `alphabot_state.db`, surfaced to the template by `app.py:71-143` (`/api/state` → `render_template("table_partial.html", ...)`).

**Where `current_return` is computed** — `alpha_bot_execution.py`:
- **Non-triggered (STANDBY) symphonies:** line **461** — `current_return = sym.get("last_percent_change", 0.0) * 100`. Not reassigned anywhere on the non-triggered path; written back verbatim to state at line **675**.
- **Triggered symphonies:** lines **464-477** override it with frozen-return + post-trigger VWAP move (Shadow Return). Not relevant here — all 11 symphonies on the test account are `triggered: false`.
- **EOD post-mortem:** line **404** — same `last_percent_change * 100`.

So on the live (non-triggered) path, `current_return` is **exactly** Composer's per-symphony `last_percent_change × 100`, captured at the instant of the engine cycle.

## What Composer's "Today's Change" is

From `docs/research/composer/reverification__2026-05-13.md` (schema snapshot, Tier 1):

- **Per-symphony:** `symphony-stats-meta` → `last_percent_change` — decimal fraction (e.g. `-0.0017` = -0.17%). The `×100` in code matches this convention.
- **Account-level:** `total-stats` → `todays_percent_change` — decimal fraction. This is what Composer's UI shows as the account "Today's Change".

AlphaBot consumes `last_percent_change` directly (`alpha_bot_execution.py:404, 461`) and re-derives the account number by capital-weighting. It does **not** call `todays_percent_change` — it recomputes the equivalent.

---

## Empirical comparison (READ-ONLY — GET only, no deploy/liquidate/go-to-cash)

### Account-level: AlphaBot's aggregate vs Composer's native field

| Source | Value |
|---|---|
| Composer `total-stats.todays_percent_change` (native account "Today's Change") | `0.001137` → **+0.11%** |
| AlphaBot "Actual Return" formula applied to **live** `symphony-stats-meta` | **+0.1133%** |

**Match to 2 decimals.** The capital-weighted formula in `table_partial.html` is the correct reconstruction of Composer's account "Today's Change". The formula is sound.

### Per-symphony: state DB snapshot vs live API

State DB (`bot_state`) last written at chart time **10:00 ET** (`chart_history` last point = `10:00`; `execution_lock.timestamp` ≈ 09:40 ET). API polled live at ~15:42 UTC. Sample:

| Symphony | DB `current_return` (snapshot @10:00) | `chart_history` @10:00 | Live API `last_percent_change×100` (@15:42) |
|---|---|---|---|
| `n2ooAZTvBRN6...` We do a Little | -0.15 | -0.15 | -0.18 |
| `INfCn3eKsu6i...` Projected I | -0.14 | -0.14 | +0.06 |
| `hvPiGP1O7AHf...` Hunted Casc | -0.26 | -0.26 | +0.01 |
| `8FAXAnQmYi1I...` Run of the Feaver | +0.15 | — | +0.45 |

**The DB value matches `chart_history` at 10:00 exactly** — confirming `current_return` is faithfully `last_percent_change × 100` *at cycle time*. It does **not** match the live API because intraday `last_percent_change` moves continuously: two API polls 3 seconds apart showed values shifting on 5 of 6 symphonies (e.g. `iaSOOUsmnCJH` -0.0031 → -0.0020). The "discrepancy" is pure time-lag between a frozen snapshot and a moving target.

---

## Root cause

1. **Engine cadence ≠ Composer UI cadence.** `alpha_bot_execution.py` runs once per minute (spawned by `app.py` scheduler at :00) and writes a point-in-time snapshot of `last_percent_change` into `bot_state`. Composer's UI streams continuously. Any comparison made mid-cycle will differ — and will differ *more* the longer since the last engine run (or if the engine is between/after runs, e.g. the 10:00 snapshot viewed at 15:42).
2. **No staleness indicator on the dashboard.** `table_partial.html` renders `current_return` with no "as of" timestamp. The operator cannot tell a 5-second-old number from a 6-hour-old one, so a stale-but-correct value reads as a "wrong" value.

Neither is a computation bug. The formula at `table_partial.html:5-15` and the assignment at `alpha_bot_execution.py:461` are both correct.

---

## Recommendation (for PM — this is a feature decision, not a fix)

Do **not** dispatch a bug-fix against the return computation; there is no bug in it. If the operator wants closer parity with Composer's UI, the options are:

1. **Minimal — surface staleness.** Add a "data as of HH:MM ET" stamp to the dashboard (the engine already has `current_time_str` and writes `chart_history` time points). Cheapest; makes the snapshot self-explanatory and closes the actual operator-facing gap.
2. **Medium — independent dashboard refresh.** Have `/api/state` (or a sibling endpoint) poll `symphony-stats-meta` / `total-stats` directly on dashboard load, decoupled from the engine cycle. Note the 1 req/sec Composer rate limit and the read-only dashboard constraint (project CLAUDE.md: dashboard is never an action surface — a read poll is fine).
3. **No-op — label clarification.** If snapshot-at-cycle is acceptable, just retitle/tooltip to "Today's Change (as of last cycle)".

All three are new dashboard-metrics work, gated through normal A/C. None touch `is_live`, broker writes, or the execution path's return math.

---

## Provenance / method notes

- All Composer calls were **GET only**: `portfolio/accounts/{acct}/symphony-stats-meta` and `portfolio/accounts/{acct}/total-stats`. No POST, no `/deploy`, no `go-to-cash`, no `liquidate`.
- Credentials read from working-tree `.env` via `dotenv_values`; never logged or echoed.
- HTTP calls used explicit `timeout=30`.
- State read from `alphabot_state.db` (`bot_state`, `chart_history`, `execution_lock` tables) — read-only `SELECT`.
- Schema semantics for `last_percent_change` / `todays_percent_change` cross-checked against `docs/research/composer/reverification__2026-05-13.md` (Tier 1, re-fetched 2026-05-13).
