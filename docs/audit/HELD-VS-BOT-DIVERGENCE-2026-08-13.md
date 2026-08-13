# Held-vs-Bot Divergence on an "Armed-Only" Day — Integration Audit

**Date:** 2026-08-13 (Wednesday, US market open)
**Auditor:** integration-auditor (read-only diagnosis)
**Scope:** every UI surface rendering a Bot-vs-Held comparison that can show a TODAY delta
**Repo:** `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM` — `main` @ `dbe5994e715d3754e20211f594f26b67ee781edc`, clean vs `origin/main`
**Droplet:** `/opt/planetstopper` @ `dbe5994e715d3754e20211f594f26b67ee781edc` (identical SHA), unit `planetstopper` **active**, `ExecMainStartTimestamp=2026-08-06 06:09:28 UTC`
**DB access:** `sqlite3.connect("file:/opt/planetstopper/alphabot_state.db?mode=ro", uri=True)` — strictly read-only, no writes, no restarts, no settings changes, no pytest.

---

## 0. Headline: the operator's premise is falsified by the data

> OPERATOR REPORT: "All monitored symphonies are merely *Armed* today — zero exits have triggered."

**An exit DID fire today.** `exit_triggers` row id **250**:

```
id=250  ts_et=2026-08-13T11:08:10  symphony_id=n2ooAZTvBRN6ZzpMmWmU
triggered_reason='VWAP Breakdown'  at_return=0.67
cycle_id='2026-08-13T11:07:01.335905-04:00'
gate_state_json={"high_water_mark": 1.0999999999999999, "vwap_ticks": 3, "mc_prob": 56.36, ...}
```

Live `bot_state` flags (read-only, capture 17:31 UTC / 13:31 ET):

| Symphony | armed | tp_armed | triggered | reason |
|---|---|---|---|---|
| (INVEST) LQD + EYEG 5 ways Full Market | False | False | False | — |
| Planet of the Paragons: EYEG of the LQD | False | False | False | — |
| Corporate Chaos 5 ways | False | False | False | — |
| Planet of the Golden Age (Buy Copy) | **True** | False | False | — |
| Planet LQD: Run of the Feaver'd WaltAnansi | False | False | False | — |
| Land of Feaver'd Allocations: Intelligent Novas | False | False | False | — |
| (INVEST) Planet of Hunted Cascades | False | False | False | — |
| (INVEST) Planet of Projected Inflation | **True** | False | False | — |
| Planet of the Reasonabilists | **True** | False | False | — |
| Planet of Erased History: Nova's Feaver'd | False | False | False | — |
| **(INVEST:CRYPTO) We do a Little Trolling Planet's Mix v1.4** | False | False | **True** | **VWAP Breakdown** |

**ARMED count = 3, TRIGGERED count = 1.** The daemon's own console tail agrees:

```
-> (INVEST:CRYPTO) We do a Little Trol: Ret: 0.42% | HWM: -999.00% | Stop Dist: 1.98% | ArmProb: Exited
```

So the day is **not** a zero-exit day, and a nonzero Held-vs-Bot delta is *expected*. The audit question therefore becomes: **is the rendered delta the right size?** It is not — roughly half of it is a basis artifact (§5, FINDING-1).

The UI has working render paths for this state — `templates/index.html:1152-1156` emits a `status-pill vwap` "VWAP" pill when `sym.get("triggered")`, `static/index.js:1141-1156` refreshes that pill on every poll, and `app.py:1459` (`"triggered": triggered`, counted at the `sum(1 for s in symphony_entries if s.get("triggered"))` site) feeds the `hero-triggered` mini-stat (`static/index.js:1404-1405`, `templates/index.html:1071`). I could not log in to the dashboard to confirm the pixels (see ASSUMPTION-01), so **whether the operator's screen actually showed "Exited"/"VWAP" is unverified-live**; the state, the code paths, and the engine log all say it should have.

---

## 1. Surfaces enumerated

| # | Surface | Producer | Held source | Bot source | Can show TODAY delta? |
|---|---|---|---|---|---|
| S1 | Dashboard **Today's Change** comparison row | `_compute_portfolio_strip` (`app.py:1480`) → `/api/state` | `bot_state.current_return` | `shadow_history.shadow_return` | **YES — today-scoped** |
| S2 | Dashboard **Cumulative · lifetime** row | same | Composer `simple_return` (account) | Held + lifetime epoch-additive divergence | Yes, but **lifetime**, not a today statement |
| S3 | Dashboard **Max DD** row | same | Composer `metrics.max_drawdown` | divergence-equity peak-to-trough | Lifetime |
| S4 | Per-symphony **card** Today's Change (`_tc`) | `app.py:2675` / `app.py:2169` | `bot_state.current_return` | `shadow_history.shadow_return` | **YES — today-scoped** |
| S5 | **Hero chart** dashed "If held" line (`hist_bot`/`hist_held`) | `analytics.get_portfolio_bot_and_held_daily_returns:1471` | `shadow_history.current_return` | `shadow_history.shadow_return` | YES (today = last point) |
| S6 | **Performance tab** `scope=symphony` | `analytics.get_symphony_bot_and_held_daily_returns:1576` | `shadow_history.current_return` | `shadow_history.shadow_return` | YES |
| S7 | Hero **GUARD ALPHA** headline (windowed 30d) | `analytics.compute_windowed_portfolio_strip:1861` via `app.py:1773` | windowed epoch-additive divergence | same | Windowed, not today-only |
| S8 | **$-saved** headline (`/api/guard-alpha-summary`) | `post_mortem_*.json` (written by `reporting.py` EOD) | `shadow_history.current_return` | frozen exit | No — EOD-only, no row for today yet |

**The load-bearing observation:** S1/S4 take Held from `bot_state.current_return`; S5/S6/S8 take Held from `shadow_history.current_return`. Those two are the *same number for an untriggered symphony* and **different numbers for a triggered one**.

---

## 2. Dual call-path trace

### S1 — Dashboard Today's Change row

**Held (`if_held`) side:**

1. `app.py:1504` — `symphony_keys = [k for k, v in bot_state.items() if isinstance(v, dict) and "name" in v]`
2. `app.py:1508` — `cr = s.get("current_return") or 0.0` ← **reads `bot_state[k]["current_return"]`**
3. `app.py:1514` — `"last_percent_change": cr / 100.0` (synthesised into the sym_dict)
4. `app.py:1601` — `_vw_tc = analytics.get_portfolio_today_change(symphonies_list, bot_state, trading_day=..., conn=conn)`
5. `analytics.py:1128` → `_value_weighted_portfolio(..., get_symphony_today_change, ...)`
6. `analytics.py:1085` — `per = per_sym_fn(sym, entry, **kwargs)`
7. `analytics.py:536` — `if_held = float(sym_dict["last_percent_change"]) * 100.0` ← **round-trips straight back to `bot_state.current_return`**
8. `analytics.py:1095/1109` — `if_held_wsum += per["if_held"] * w` … `if_held_wsum / total_weight` (weight `w` = `bot_state.current_value`, `app.py:1509`)
9. `app.py:1604` — `analytics.get_portfolio_today_change_account_basis(_vw_tc, _cached_tc, account_value, _symphony_value_sum)`
10. `analytics.py:1324` — returns `{"if_held": account_if_held_tc, ...}` — the rendered Held is **Composer's account `todays_percent_change`**, and the VW `if_held` above survives *only* inside `guard_delta_vw`.

**Bot (`dry_run`) side:**

1. `analytics.py:554-555` — `row = _load_latest_shadow_row_for_analytics(symphony_id, _trading_day, _db_file, conn=conn)`
2. `analytics.py:597-602` — `SELECT * FROM shadow_history WHERE symphony_id = ? AND trading_day = ? ORDER BY ts_utc DESC LIMIT 1`
3. `analytics.py:557` — `dry_run = float(row["shadow_return"])` ← **frozen-at-exit column** (`row["current_return"]` sits unread in the same row — see FINDING-1)
4. `analytics.py:1098-1100` — `if per["dry_run"] is not None: dry_run_wsum += ...; dry_run_weight += w`
5. `analytics.py:1107` — `dry_run_wsum / dry_run_weight` — **an independent denominator** (see FINDING-2)
6. `analytics.py:1321-1323` — `guard_delta_vw = dry_run - if_held`; `dry_run_account = account_if_held_tc + guard_delta_vw * invested_frac`

**Rendered delta identity.** Because `if_held` is *replaced* by the account figure at `analytics.py:1324` while `dry_run` is that same figure *plus* the scaled delta:

```
Today-row delta = dry_run − if_held = guard_delta_vw × invested_frac
                  invested_frac = min(symphony_value_sum / account_value, 1.0)   [analytics.py:1318]
```

The rendered delta is **independent of Composer's account today-change value** — which is why §5 can attribute it exactly without reading the in-process account cache.

**Render:** `static/index.js:1001` (`values: ps.today_change`) → `:1013-1014` (`bot`/`held` reads of `dry_run`/`if_held`) → `:1030/1038` (`'Bot ' + fmtPct(bot)` / `'Held ' + fmtPct(held)`).

### Engine write sites (why the two Held columns diverge)

| Site | What it writes | Marker |
|---|---|---|
| `alpha_bot_execution.py:891` | data phase: `bot_state[s_id]["current_return"] = current_return` (raw Composer `last_percent_change*100`) | `:897` sets `current_return_is_reconstructed = False` |
| `alpha_bot_execution.py:942` | pre-trigger `shadow_return = current_return` | — |
| `alpha_bot_execution.py:937-939` | post-trigger `shadow_return = bot_state[s_id]["triggered_at_return"]` (**frozen**) | — |
| `alpha_bot_execution.py:953-966` | `record_shadow_observation(..., current_return=current_return, shadow_return=shadow_return, ...)` — **both columns, same cycle** | — |
| `alpha_bot_execution.py:1264-1274` | **TRUE SHADOW RETURN OVERRIDE** — for a triggered symphony, rebuilds `current_return` from frozen `trigger_prices` + **live VWAPs** | — |
| `alpha_bot_execution.py:1660` | action phase: `bot_state[symphony_id]["current_return"] = current_return` ← **overwrites :891 with the reconstruction** | `:1671` sets `current_return_is_reconstructed = is_triggered_now` |
| `alpha_bot_execution.py:1048-1057` | EOD pass: overwrites back to the raw per-tick figure, marker `False` | — |

So `bot_state.current_return` is **raw Composer for an untriggered symphony and a VWAP-based basket reconstruction for a triggered one**, while `shadow_history.current_return` is *always* the raw Composer figure. `_compute_portfolio_strip` treats the field as uniformly "Composer `last_percent_change`" (`app.py:2618` states that assumption in a comment: *"last_percent_change from current_return/100"*).

`current_return_is_reconstructed` (BL-9, `DE-AUDIT-BL9-001`) exists precisely to make this distinguishable and is documented as having **zero consumers**. S1/S4 are exactly the consumers that need it.

### S5/S6 — hero chart + Performance tab (the contrasting, correct basis)

- `analytics.py:1486` docstring: *"Held = value-weighted `current_return` (the if-held baseline — kept holding)"*
- `analytics.py:1515` — `SELECT trading_day, symphony_id, shadow_return, current_return FROM shadow_history ...`
- `analytics.py:1563-1564` — `bot_wsum += sym_ret * w` / `held_wsum += sym_cr * w`
- `analytics.py:1559` — `w = value_weights.get(sym_id, 0.0)` ← `_load_position_value_weights()` (`analytics.py:1366`, `bot_state.current_value`) — **the same weights S1 uses**, so the two surfaces are directly comparable and differ *only* in the Held column.

### S2 — Cumulative row

- `analytics.py:936` — `if_held = simple_return * 100.0` (Composer)
- `analytics.py:944-960` — lifetime **epoch-additive** divergence: `for epoch_pairs in trajectory: ... lifetime_divergence += (product_shadow - product_current) * 100.0`; `dry_run = if_held + lifetime_divergence`
- `analytics.py:932-934` — `_twr_fallback = True` when `simple_return == 0.0 and net_deposits == 0.0`; `analytics.py:1093-1094` excludes those symphonies from the VW aggregate entirely
- `app.py:1564-1574` — VW CR → `get_portfolio_cumulative_return_account_basis` → `analytics.py:1246` `dry_run_account = account_if_held + guard_delta_vw * invested_frac`
- Render: `static/index.js:1006` — note the F-014 comment there pins this row to the **lifetime** `cumulative_return`, deliberately *not* the windowed value.

---

## 3. Live capture — one atomic engine cycle

All figures below come from a **single** read at `CAPTURE_AT_UTC=2026-08-13T17:31:01`, engine cycle `last_successful_cycle_at=2026-08-13T13:30:02.282270-04:00`, all shadow rows `ts_et=2026-08-13T13:30:02` (one consistent tick — no cross-cycle mixing).

Coverage is complete: **all 11 symphonies have 202 shadow rows each today**, `first=09:30:01`, `last=13:30:02`. No symphony is missing a row, so the FINDING-2 denominator hazard is **latent, not firing today**.

### Per-symphony Today's Change inputs

| Symphony | weight ($) | Held = `bot_state.current_return` | Bot = `shadow_return` | `shadow_history.current_return` |
|---|---|---|---|---|
| (INVEST) LQD + EYEG 5 ways | 1020.63 | −1.27 | −1.27 | −1.27 |
| Planet of the Paragons | 765.23 | −1.17 | −1.17 | −1.17 |
| Corporate Chaos 5 ways | 853.31 | −0.09 | −0.09 | −0.09 |
| Planet of the Golden Age | 1760.90 | +1.21 | +1.21 | +1.21 |
| Planet LQD: Feaver'd WaltAnansi | 1576.06 | −0.73 | −0.73 | −0.73 |
| Land of Feaver'd Allocations | 1050.78 | +0.24 | +0.24 | +0.24 |
| (INVEST) Hunted Cascades | 1261.39 | +0.22 | +0.22 | +0.22 |
| (INVEST) Projected Inflation | 1071.05 | +0.91 | +0.91 | +0.91 |
| Planet of the Reasonabilists | 1750.42 | +1.11 | +1.11 | +1.11 |
| Planet of Erased History | 1025.53 | −0.13 | −0.13 | −0.13 |
| **(INVEST:CRYPTO) Little Trolling** ← TRIGGERED | 1757.54 | **+0.39571** | **+0.67000** | **+0.53000** |

Ten of eleven symphonies have `shadow_return == bot_state.current_return` **exactly**. All divergence is one symphony.

### Portfolio aggregates (`symphony_value_sum = 13892.28`)

```
[1] VW Held  (bot_state.current_return)      = +0.192476 pp   <- what the Today ROW renders as Held
[2] VW Bot   (shadow_history.shadow_return)  = +0.227176 pp   <- what the Today ROW renders as Bot
[3] VW Held  (shadow_history.current_return) = +0.209465 pp   <- what the hero chart / Perf tab call Held

Today-row delta   [2]-[1] = +0.034700 pp   (× invested_frac)
Hero/Perf  delta  [2]-[3] = +0.017712 pp
ARTIFACT          [3]-[1] = +0.016989 pp   = 49.0% of the Today-row delta
```

### Cumulative row (captured one cycle later; lifetime figures are stable)

```
VW CR if_held = +47.87612 pp | VW CR dry_run = +49.51654 pp
guard_delta_vw (CR) = +1.64042 pp   (× invested_frac)
```

Cross-check against the frozen `last_market_close_snapshot` (trading_day 2026-08-12, captured_at_et 16:00:01 ET), which is already account-basis-scaled:

```
today_change      = {"if_held": 0.11348020634669793, "dry_run": 0.15601187886187476}   delta +0.04253
cumulative_return = {"if_held": 47.063847534043305, "dry_run": 48.731331501926306}     delta +1.66748
max_drawdown      = {"if_held": 22.940409576114703, "dry_run": 5.608072367980429}
```

Yesterday's *rendered* cumulative delta (+1.66748, post-`invested_frac`) vs today's *pre-scaling* VW delta (+1.64042) implies **`invested_frac` ≈ 1** (the account is near fully deployed, little idle cash). That is an inference, not a direct read — see ASSUMPTION-02.

### Account-basis staleness markers — chip is NOT showing

- `_ACCOUNT_TOTALS_HTTP_TIMEOUT_S = 30` on the droplet (`/opt/planetstopper/app.py:579`) — the BL-4 value, so the deployed code matches `main`.
- `grep -c "_refresh_account_totals failed"` = **0** over the last 200,000 log lines of `alphabot_daemon.log` (478,912 lines total; the last failure is at line 274,695 and reports `read timeout=10`, i.e. pre-BL-4 code, before the 2026-08-06 06:09 UTC restart).
- Therefore `_account_totals_cache` is **warm**: `app.py:1600` takes the `_cached_tc is not None` branch, `_live_basis_stale` stays `False` (`app.py:1554`), neither `basis="value_weighted"` (`app.py:1750`) nor `account_basis_stale` (`app.py:1755`) is stamped.
- Consequence at the render layer (`static/index.js:948-957`): both branches are skipped, `chip.hidden = true`. **The BL-4 honesty chip is correctly absent** — the dashboard is on a genuine, fresh account basis. *It is disclosing nothing because there is nothing of that kind to disclose.* The divergence in §5 is **not** a staleness artifact.

---

## 4. Zero-exit-day divergence causes — hypothesis dispositions

| ID | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| H1 | Basis mismatch by design (account vs VW) | **REFUTED as a delta source** | `analytics.py:1321-1323` — the account rebasis applies to *both* sides; the rendered delta collapses to `guard_delta_vw × invested_frac`. Untriggered ⇒ `guard_delta_vw = 0` ⇒ delta 0 regardless of cash. Explicit invariant at `analytics.py:1286-1287`. |
| H2 | Timing/staleness skew (cached Composer fetch vs per-minute quotes) | **REFUTED today** | Zero `_refresh_account_totals` failures in 200k log lines; cache warm; no stale/floor marker. Also structurally: the account TC cancels out of the delta. |
| H3 | Coverage mismatch (cash / unmonitored holdings) | **REFUTED today; LATENT defect** | All 11 symphonies have 202 shadow rows today, so `dry_run_weight == total_weight`. But `analytics.py:1107` divides `dry_run` by `dry_run_weight` and `if_held` by `total_weight` — **different denominators**. See FINDING-2. |
| H4 | Reference-point mismatch (prev-close vs Composer day-change; cash-flow sensitivity) | **NOT a Today-row source; REAL for the Cumulative row** | Same cancellation as H1 for TC. For CR, `if_held` is Composer `simple_return` (`analytics.py:936`) — the cash-flow-sensitive basis BL-12 discloses in `templates/index.html`. |
| H5 | Genuine defect | **CONFIRMED** | Held side reads the **reconstructed** `bot_state.current_return` (`app.py:1508` ← `alpha_bot_execution.py:1660`/`:1264-1274`) instead of the raw `shadow_history.current_return` sitting unread in the very row already fetched at `analytics.py:555`. See FINDING-1. |

Additional confirmed non-cause: **no epoch boundary today** — `bot_state` `position_epoch` for the triggered symphony (`031141be…`) equals the `position_epoch` on its shadow rows for 2026-08-13.

---

## 5. Delta attribution

### Today's Change row: `+0.0347 pp × invested_frac` (≈ **+0.035 pp ≈ 3.5 bp**, ≈ **$4.82** on $13,892 invested)

**100% of it originates from the single triggered symphony.** Its weight share is `1757.54 / 13892.28 = 12.651%`, and its per-symphony delta is `0.67000 − 0.39571 = +0.27429 pp`:

```
0.27429 pp × 0.12651 = +0.034700 pp   ✓ exactly the whole portfolio guard_delta_vw
```

That per-symphony delta decomposes into two components:

| Component | Per-symphony | Portfolio | Share | Classification |
|---|---|---|---|---|
| **Genuine guard effect** — frozen exit `0.67000` vs raw if-held `0.53000` | +0.14000 pp | +0.017712 pp | **51.0%** | **REAL.** The exit fired at 11:08 ET at +0.67%; the position has since fallen to +0.53%. The guard is genuinely ahead. |
| **Reconstruction artifact** — raw if-held `0.53000` vs reconstructed `0.39571` | +0.13429 pp | +0.016989 pp | **49.0%** | **DEFECT (FINDING-1).** The VWAP-based basket reconstruction lags Composer's own `last_percent_change`, understating Held and inflating the apparent guard alpha. |

**The Today row therefore overstates today's guard alpha by ~96% (nearly 2×): +0.0347 pp rendered vs +0.0177 pp real.**

Same-day, same-weights cross-surface disagreement — the crispest evidence:

| Surface | Held rendered | Bot rendered | Delta |
|---|---|---|---|
| Dashboard **Today's Change** row (S1) | **+0.192476** | +0.227176 | **+0.0347** |
| Hero chart last point / Performance tab (S5/S6) | **+0.209465** | +0.227176 | **+0.0177** |

Identical day, identical weight vector, identical Bot column — **two different Held numbers**.

### Cumulative · lifetime row: `+1.64042 pp × invested_frac` — **BY-DESIGN-BASIS**

Lifetime epoch-additive guard alpha accumulated across every past exit (`analytics.py:946-960`). Non-zero on a zero-exit day is *correct by construction*; the operator's "identical holdings today ⇒ identical returns" intuition does not apply to a lifetime row. Per-symphony lifetime divergences today range `−8.64 pp` (Golden Age) to `+11.59 pp` (Paragons).

Two by-design subtleties worth naming:
- The triggered crypto symphony is **excluded** from this row entirely — `simple_return == 0.0 and net_deposits == 0.0` ⇒ `_twr_fallback` (`analytics.py:932-934`) ⇒ dropped at `analytics.py:1093-1094`, despite carrying `time_weighted_return = 3.15786` (+315.7%). So the same symphony is **in** the Today row and **out** of the Cumulative row.
- Held here is Composer `simple_return`, the cash-flow-sensitive basis already disclosed by BL-12's info-icon in `templates/index.html`.

### Max DD row — **BY-DESIGN-BASIS**

`if_held` = Composer `metrics.max_drawdown` (`app.py:1643`, `abs()`-converted); `dry_run` = peak-to-trough of the divergence-equity series (`analytics.py:976-985`). Lifetime, different constructions on both sides by design.

---

## 6. Findings

### FINDING-1 — Today's Change Held column reads the reconstructed `current_return` (DEFECT)

- **Severity:** MEDIUM (display/aggregation only — zero exit-decision impact; but it is the operator's primary honesty surface and it **overstates guard alpha**, the metric the product exists to prove)
- **Layer:** Data-sourcing seam between engine state and the analytics display layer
- **Current:** For a triggered symphony, `if_held` resolves to `bot_state.current_return`, which `alpha_bot_execution.py:1660` overwrote with the TRUE SHADOW RETURN OVERRIDE's basket reconstruction (frozen `trigger_prices` + live VWAPs, `:1264-1274`).
- **Expected:** Held should be the raw Composer if-held trajectory, `shadow_history.current_return` — the basis `reporting.py` was migrated to under `DE-GUARD-ALPHA-SAVED-001` and the basis S5/S6/S8 already use (`analytics.py:1486`).
- **Evidence:** `app.py:1508` + `app.py:2624` (`cr = s.get("current_return") or 0.0`) → `analytics.py:536`; contrast `analytics.py:1486`/`1564`. Live: reconstructed `+0.39571` vs raw `+0.53000` on the same symphony in the same cycle.
- **Why it recurs:** this is the **same defect class** as `DE-GUARD-ALPHA-SAVED-001` (a reconstructed basket used as an if-held proxy). That fix was applied to `reporting.py` only; the dashboard TC path was never migrated. Direction differs (that one *understated* saved, this one *overstates* alpha) — the common root is that a reconstruction is not a faithful if-held series.
- **Confidence:** HIGH — arithmetic reproduced end-to-end from live rows.
- **Minimal blast radius:** `analytics.get_symphony_today_change` (`analytics.py:513-559`). The shadow row fetched at `:555` **already contains `current_return`**; the fix is to prefer `row["current_return"]` for `if_held` when the row exists. Empirically safe: all 10 untriggered symphonies today have `row["current_return"] == bot_state.current_return` exactly (both descend from the same `symphony_data_cache` entry within one cycle — `alpha_bot_execution.py:891` and `:960` vs `:1262`).
  **Caution — two caller shapes:** the sym_dict is synthesised from `bot_state` at `app.py:1508-1514` and `app.py:2624-2630`, but `get_symphony_today_change` is also reachable from `app.py:1274` and `app.py:2169`; a fix must not regress a caller whose `last_percent_change` is a genuine live Composer field. Also fixes S4 (per-symphony card `_tc`) via the same seam.
- **Rollback risk:** LOW — one function, display-only; `alpha_bot_execution.py` / `math_engine.py` untouched.

### FINDING-2 — `_value_weighted_portfolio` uses different denominators for Held and Bot (LATENT DEFECT)

- **Severity:** LOW today (not firing), MEDIUM when it fires
- **Evidence:** `analytics.py:1109` divides `if_held_wsum` by `total_weight`; `analytics.py:1107` divides `dry_run_wsum` by `dry_run_weight`, accumulated only for symphonies whose `dry_run is not None` (`:1098-1100`). A symphony with no shadow row today (`analytics.py:556-557` leaves `dry_run = None`) is dropped from Bot but retained in Held, so the two averages are taken over **different symphony sets**.
- **Consequence:** a phantom `guard_delta_vw` on a genuinely zero-exit day — exactly the operator's suspicion — with magnitude set by how far the missing symphony's return sits from the portfolio mean.
- **Not firing today:** all 11 symphonies have 202 rows each (`09:30:01`→`13:30:02`).
- **When it would fire:** engine downtime mid-session, a newly added symphony, a Composer fetch failure skipping `record_shadow_observation`, or a symphony present in `bot_state` but absent from the day's fetch. `alpha_bot_execution.py:1071-1075` already logs `"EOD divergence: no shadow_history rows for %s on %s — coverage gap"`, so the coverage gap is a known real mode.
- **Confidence:** HIGH on the code path; the "would fire" magnitude is unexercised today.

### FINDING-3 — BL-9's `current_return_is_reconstructed` marker has the consumer it needs, and it is ignored (OBSERVATION)

`DE-AUDIT-BL9-001` added the marker for discoverability and documents "zero consumers read this key". `bot_state.current_return_is_reconstructed = true` is set on exactly the symphony causing 100% of today's Today-row delta. FINDING-1's seam is the intended consumer. No behavioural change is implied by this finding on its own; it is the cheapest available discriminator if a fix prefers an explicit guard over "always read the shadow column".

---

## 7. Routing verification

- `_compute_portfolio_strip` (`app.py:1480`) is reached from both live render paths: `app.py:1865` (`get_api_state_dict`, Jinja SSR) and `app.py:2701` (`get_state`, `/api/state` JSON poll).
- `updateComparisonRows` (`static/index.js:977`) has two production callers — `static/index.js:1375` (state poll) and `:1482` (windowed-strip wrapper) — per its own BL-4 comment at `:979-991`.
- Status pill live: `templates/index.html:1152-1156` (SSR) + `static/index.js:1141-1156` (poll refresh).
- Daemon **active**, engine ticking (`last_successful_cycle_at = 13:30:02 ET`, 202 shadow rows/symphony today).
- **Status: CONFIRMED LIVE** for S1–S6.

---

## 8. Open Questions

- **[ASSUMPTION-01] (Non-blocking)** — I did not authenticate to the dashboard, so no rendered pixels were captured. Logging in requires `POST /login`, which the read-only scope and the auditor's no-mutation rule exclude. All render claims are code-path + state derived, not screenshot-verified. Whether the operator's screen showed the "VWAP"/"Exited" pill for the crypto symphony is therefore **unverified-live** — though `bot_state.triggered=true`, `templates/index.html:1152`, `static/index.js:1141` and the daemon's `ArmProb: Exited` line all indicate it should have.
- **[ASSUMPTION-02] (Non-blocking)** — `invested_frac` is not directly readable: `account_value` lives in the Flask process's in-memory `_account_totals_cache`, not the DB. It is inferred ≈ 1 from yesterday's account-basis-scaled snapshot delta (+1.66748) vs today's pre-scaling VW CR delta (+1.64042). Since `invested_frac ≤ 1` (`analytics.py:1318`), every quoted delta is an **upper bound**; the 51%/49% split is unaffected (it is a ratio, and the scaling is common to both components).
- **[QUESTION-01] (Non-blocking)** — Should the reconstruction survive anywhere as a *displayed* Held value? A defensible alternative to FINDING-1's fix is to keep it but label it; the cross-surface inconsistency with S5/S6/S8 argues for converging on `shadow_history.current_return` everywhere. PM/operator call.
- **[QUESTION-02] (Non-blocking)** — The triggered symphony is included in the Today row but excluded from the Cumulative row (`_twr_fallback`, `analytics.py:932-934`) despite a `time_weighted_return` of +315.7%. Intended asymmetry, or should the two rows share a membership rule?

---

## 9. Evidence Appendix

**Live commands (all read-only).** SSH `ssh -i ~/.ssh/id_ed25519_tvr root@104.248.7.101`; every DB open used `sqlite3.connect("file:/opt/planetstopper/alphabot_state.db?mode=ro", uri=True)` via `python3` reading a heredoc from stdin (no files written to the droplet). `sqlite3` CLI is not installed there.

**Key file citations.**

| Claim | Citation |
|---|---|
| Held ← bot_state.current_return (strip path) | `app.py:1508`, `app.py:1514` |
| Held ← bot_state.current_return (poll path) | `app.py:2624`, `app.py:2630` |
| `if_held` from `last_percent_change` | `analytics.py:536` |
| `dry_run` from frozen `shadow_return` | `analytics.py:557` |
| Unread `current_return` in the same row | `analytics.py:597-603` |
| Independent VW denominators | `analytics.py:1107` vs `:1109` |
| Delta = `guard_delta_vw × invested_frac` | `analytics.py:1318-1323` |
| Zero-delta-when-untriggered invariant | `analytics.py:1286-1287` |
| Hero/Perf Held ← shadow current_return | `analytics.py:1486`, `:1515`, `:1564` |
| Shared weight source | `analytics.py:1366-1390` |
| Lifetime epoch-additive CR | `analytics.py:946-960` |
| `_twr_fallback` exclusion | `analytics.py:932-934`, `:1093-1094` |
| Data-phase raw write | `alpha_bot_execution.py:891-897` |
| Shadow row write (both columns) | `alpha_bot_execution.py:937-966` |
| TRUE SHADOW RETURN OVERRIDE | `alpha_bot_execution.py:1264-1274` |
| Action-phase overwrite + BL-9 marker | `alpha_bot_execution.py:1660-1671` |
| Coverage-gap logging | `alpha_bot_execution.py:1071-1075` |
| BL-4 chip render + gate | `static/index.js:945-975`, `:992-996` |
| Comparison-row render | `static/index.js:1001-1014`, `:1030`, `:1038` |
| Cumulative row pinned to lifetime | `static/index.js:1002-1006` |
| Triggered status pill | `templates/index.html:1152-1156` |
| Deployed timeout constant | `/opt/planetstopper/app.py:579` (`= 30`) |
