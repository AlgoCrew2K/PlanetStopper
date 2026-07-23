# Dashboard Per-Symphony Table Audit

> **Superseded claim, 2026-07-18 (`DE-MATH-F7-001`):** this dated audit (2026-05-14) describes the MC Prob column's semantic as "probability the symphony beats SPY" (below, and in the proposed hover-text) and its proposed tooltip repeats that framing. Math Remediation F7 corrected this: the header tooltip now reads "Monte Carlo probability this symphony underperforms its own regime-matched historical baseline" -- SPY only selects the regime-matching historical analog days the kNN pool draws from; it is never the compared benchmark. This banner corrects the record; the audit body below is preserved unedited as a historical snapshot of the M2-era table design, not a rewrite. See `DE-MATH-F7-001` in `DECISIONS.md` for the full ruling (including why no directional tooltip sentence ships either).

Read-only audit for the M2 dashboard expansion. No code changed.

Sources read:
- `templates/table_partial.html`
- `templates/index.html`
- `app.py` — `/api/state` route (lines ~71-152) + `dashboard()`
- `analytics.py` — `compute_aggregate_returns`, `compute_per_symphony_returns`
- `alpha_bot_execution.py` — `bot_state` field writes (lines ~440-740)

---

## 1. Current per-symphony table inventory

The table has **7 columns**. Each `<tr>` is one symphony. Several columns are
**compound** — they render a stacked two-line cell whose second line is a
distinct metric with its own tooltip.

| # | Display label | Source field(s) (`bot_state` / sym) | What it means | Has tooltip? |
|---|---------------|--------------------------------------|---------------|--------------|
| 1 | **Symphony Name** | `sym.name` / `sym.id` | Composer symphony name. Cell also has log + intraday-chart icon buttons. | Only `title={{name}}` on the truncated text (full name on hover). No explanatory tooltip. |
| 2 | **MC Prob** | `sym.mc_prob` | Monte Carlo probability (%) that the symphony **beats SPY** over the simulation horizon — the core arm/trigger gating signal. Colored amber when `< 15%`, fuchsia when TP-armed. | **No.** Header has no `title`. |
| 3 | **Status** | `sym.triggered`, `triggered_reason`, `para_armed`, `tp_armed`, `armed`, `below_stop_count`, `above_tp_count` | State badge: STANDBY / ARMED / TP-ARMED / PARA-ARMED / TRIGGERED (+ reason: VWAP Breakdown, VWAP Bleed Cut, Take-Profit, etc.). "(1/2 Ticks)" sub-label shows multi-tick confirmation progress. | **Partial.** Badge `title` carries the trigger reason / "Parabolic Squeeze Active" / "Smart Take-Profit is Armed" — but only on the *triggered/armed* states, and the header itself has none. STANDBY has no tooltip. |
| 4 | **Stop Level** | `sym.stop_trigger` (live) or `sym.triggered_at_stop` (frozen if triggered); 🔒 icon from `sym.breakeven_locked` | The trailing-stop trigger level (%) — the return threshold at which the bot exits. 🔒 = vol-scaled breakeven lock active. When triggered, shows the frozen stop level at exit. | **Partial.** The 🔒 icon has `title="Vol-Scaled Breakeven Lock Active"`. The header + the number itself have no tooltip. |
| 5 | **Exit Return** | live: `sym.current_return`; triggered: `sym.triggered_at_return` + `sym.triggered_at_time` | The symphony's return (%). When *not* triggered this is just the current live return. When *triggered* it is the **frozen return at the moment the bot exited** + a second line with a 🔒 lock icon and the execution time (ET). | **Partial.** When triggered: inner spans have `title="Frozen Exit Return"` and `title="Time of Execution"`. When not triggered: no tooltip. Header: none. Label "Exit Return" is misleading pre-trigger (it's just current return then). |
| 6 | **High Water Mark** | `sym.shadow_hwm` (NOTE: column is *labelled* HWM but renders `shadow_hwm`, not `high_water_mark`) | The highest return the **shadow (if-held) model** has reached this session. Used as the reference peak for trailing-stop distance. | **No.** Header has no `title`. Also a latent bug-smell: header sort key is `high_water_mark` but the rendered value is `shadow_hwm`, and the cell color var `hwm_color` is computed from the *triggered-branch* `f_hwm` / non-triggered `high_water_mark`, not from `shadow_hwm`. |
| 7 | **If Held (Shadow)** | live: `sym.current_return`; when triggered: `sym.current_return` (live theoretical) + computed `diff = f_ret - live_ret` ("Guard Alpha α") | "What you'd have if the bot had NOT triggered" — the buy-and-hold / Composer-model return. When triggered, second line shows **Guard Alpha**: how many % the bot saved (+) or cost (−) you by exiting. | **Yes** (the only well-tooltipped column). Header `title="The current live return of the symphony model..."`; inner spans `title="Live theoretical return..."` and `title="Guard Alpha (How much % you saved or lost by triggering)"`. |

### Account-group header (rendered once per account, above the table)
| Field | Source | Meaning | Tooltip? |
|-------|--------|---------|----------|
| Account label + masked id | `account_labels` + `acc_id[:8]` | Individual / Roth IRA / Trad. IRA + truncated UUID | `title={{acc_id}}` (full id) |
| "N Symphonies" | `syms|length` | count | No |
| **Actual Return** | capital-weighted mean of `sym.current_value * sym.current_return` computed in-template | Capital-weighted account return = Composer's "Today's Change" for the account (per `actual-return-diagnosis.md`) | `title="Capital-Weighted Account Return"` |
| GO TO CASH button | — | liquidation action | — |

**Effective metric count per row:** 7 columns but **10 distinct data points** when triggered (Exit Return splits into frozen-return + exec-time; If-Held splits into live-return + Guard-Alpha; Stop Level carries the lock flag).

### Cryptic columns (count: 5 of 7 need work)
- **MC Prob** — acronym, no tooltip, no expansion anywhere.
- **Status** — badge vocabulary (STANDBY/ARMED/TP-ARMED/PARA-ARMED) is insider jargon; only some states self-describe via title.
- **Stop Level** — ambiguous: trailing-stop *trigger* level, not a price.
- **High Water Mark** — acronym; *and* mislabeled vs the field it renders (`shadow_hwm`).
- **Exit Return** — label only correct post-trigger; pre-trigger it silently means "current return".

Self-explanatory enough: **Symphony Name**, **If Held (Shadow)** (already well tooltipped, though "Shadow" itself is jargon).

---

## 2. Clarity assessment + proposed wording

For each flagged column — proposed clearer label and/or hover-over copy. (Operator
asked for "better row names or informational hover-overs"; recommend **keep short
labels, add hover-overs** rather than long labels that blow out column width —
see layout section.)

| Column | Keep label? | Proposed label | Proposed hover-over |
|--------|-------------|----------------|---------------------|
| **MC Prob** | rename | **Beat-SPY Prob** (or keep "MC Prob" + tooltip) | "Monte Carlo probability this symphony beats SPY over the simulation horizon. This is the core signal that arms and triggers the trailing stop — low values mean the model expects underperformance." |
| **Status** | keep | **Status** | "Guard state for this symphony: STANDBY (watching) → ARMED (stop conditions building) → TP-ARMED (take-profit watch) / PARA-ARMED (parabolic squeeze) → TRIGGERED (bot has exited to cash). '(1/2 Ticks)' = waiting on a second confirmation cycle." |
| **Stop Level** | rename | **Trailing Stop** | "The return level (%) at which the bot exits this symphony to cash. Trails the high-water mark as the position rises. 🔒 = breakeven lock active (stop raised to lock in gains)." |
| **Exit Return** | rename | **Return** (pre-trigger) — but it's dual-purpose | "The symphony's return today. Once the bot triggers, this freezes at the return captured at the moment of exit (🔒 + time shown)." |
| **High Water Mark** | rename | **Shadow Peak** | "The highest return the if-held (shadow) model has reached today. The trailing stop is measured as a drawdown from this peak." (Also: fix the column to be internally consistent — see §4 note.) |
| **If Held (Shadow)** | rename | **If Held** | (mostly fine) "What this symphony would be returning if the bot had NOT intervened — i.e. buy-and-hold the Composer model. When triggered, the second line is Guard Alpha: % saved (+) or given up (−) by exiting." |

"Shadow" / "dry-run" terminology: the operator's M2 spec uses **"dry-run" vs "if-held"**.
Recommend the table adopt that same vocabulary so the new portfolio strip and the
per-symphony rows read consistently. "Shadow" = "if-held"; "AlphaBot/Guard" = "dry-run".

---

## 3. Current layout structure

### `index.html` skeleton
- `<body class="... p-8">` → single `<div class="max-w-7xl mx-auto">` wrapper.
- **Header banner**: `<header class="flex justify-between items-start mb-8 bg-slate-800 p-6 rounded-2xl ...">` — three columns (branding+clocks / center stats / right controls). Fixed-ish height ~140-160px.
- `#notification` — hidden banner slot, `mb-6`.
- **Table card**: `<div class="bg-slate-800 p-6 rounded-2xl ... min-h-[750px]">` containing an `<h2>` row ("Symphony Status by Account" + Rows-Displayed input + Last Updated) and `<div id="accounts-container" class="space-y-8">` — populated by JS injecting `table_partial.html` output.

### `table_partial.html` dimensions
- Each account is `<div class="bg-slate-900 rounded-xl border ...">`.
- Scroll viewport: `<div class="overflow-x-auto overflow-y-auto" style="max-height: calc(48px + (var(--max-rows,10) * 49px));">` — height is **operator-controlled** via the "Rows Displayed" number input (`--max-rows` CSS var, default 10 → ~538px).
- `<table class="w-full text-left text-sm whitespace-nowrap">` — `whitespace-nowrap` means **columns never wrap**; wide content forces horizontal scroll.
- Cells: `px-3 py-2.5`, headers `text-xs uppercase`. Sticky `<thead>`.

### Is the table cramped?
**It has room horizontally — for now.** 7 columns inside a `max-w-7xl` (1280px) container is comfortable; `overflow-x-auto` exists as a safety valve but is unlikely to be triggered at 7 columns. The `min-h-[750px]` card + the `--max-rows` scroll var give vertical breathing room.

### Re-proportioning to insert a top strip
Straightforward in the current structure:
- Insert a new `<div>` (the 6-metric portfolio strip) **between `</header>` and `#notification`** (or between `#notification` and the table card).
- The page is a simple vertical stack with no fixed heights except `min-h-[750px]` on the table card and the JS-driven `--max-rows` scroll height — nothing needs recomputation. Adding a ~100-120px strip just pushes the table card down; `max-w-7xl mx-auto` keeps everything aligned.
- Only real consideration: total vertical budget on a laptop screen (header ~150px + new strip ~110px + table h2 row ~60px + 10-row table ~538px ≈ 860px + padding). Operator may want to lower the default `--max-rows` or make the strip compact. **Not a blocker — a tuning decision.**

**Verdict: ROOM, not cramped — for the top strip. The per-symphony metric *addition* (§4) is where it gets tight.**

---

## 4. Impact analysis — per-symphony TC / CR / MDD vs if-held

### (a) Is per-symphony dry-run vs if-held data available?

**Today's Change (TC) — partially.**
- `bot_state[sym]["current_return"]` = today's live return (`last_percent_change * 100`). This is the **if-held** today's change per symphony — available live, every cycle.
- The **dry-run (AlphaBot)** today's change per symphony: when triggered, the frozen `triggered_at_return` + post-trigger drift IS effectively AlphaBot's realized today's change; when not triggered, dry-run == if-held (bot did nothing). So **TC dry-run-vs-if-held is derivable from live `bot_state`** with no new data source — it's the same `current_return` vs `triggered_at_return` relationship the "If Held (Shadow)" column already renders. No `shadow_today` field is needed; the diff already exists as "Guard Alpha α".

**Cumulative Return (CR) and Max Drawdown (MDD) — NOT in `bot_state`.**
- `bot_state` is **single-session** — it holds today's live state only. There is no cumulative or historical series in it.
- The historical series lives in the **post-mortem JSON files** consumed by `analytics.py`. `compute_per_symphony_returns(history, symphony_id)` already returns `(dates, live_returns, shadow_returns)` per symphony, and `compute_quantstats_metrics()` produces `total_return` and `max_drawdown`. So **CR and MDD per symphony are computable today** — but only via the `analytics.py` post-mortem path, **not** from `bot_state`.
- **Critical constraint:** `/api/state` currently does NOT touch `analytics.py`. It renders purely from `bot_state`. Wiring per-symphony CR/MDD into the table partial means `/api/state` (or a new sibling route) must also load post-mortem history. That is read-only file I/O — acceptable per the dashboard rules — but it's a new data dependency on the hot polling route. Better: a **separate `/api/state/metrics` route** the table partial pulls once, decoupled from the per-second `/api/state` poll, or fold it into `/api/state` accepting the added latency (post-mortem load is cached via `get_history_with_cache_invalidation`).
- **Also:** post-mortem `triggers[]` only contains symphonies that **triggered** that day. A symphony that never triggered has no entry → `compute_per_symphony_returns` omits those days. So per-symphony CR/MDD from this source is **only meaningful for symphonies with trigger history**; non-triggered symphonies would show empty/insufficient. This is a real data-completeness gap the operator must be told about.

### (b) Structural change to `table_partial.html`

To add TC / CR / MDD each as dry-run vs if-held = **6 new data points per row**. Options:

1. **6 new flat columns** → table goes from 7 → 13 columns. With `whitespace-nowrap` + `px-3` this *will* overflow `max-w-7xl` and force horizontal scroll. Poor operator UX for a primary surface.
2. **3 new compound columns** (TC, CR, MDD), each a stacked two-line cell "dry-run / if-held" — mirrors the existing "If Held (Shadow)" and "Exit Return" compound-cell pattern. Table goes 7 → 10 columns. Tight but viable in 1280px; consistent with existing design language.
3. **Expandable sub-row / drawer per symphony** — click a row to reveal a TC/CR/MDD dry-run-vs-if-held panel. Keeps the main table at 7 columns; richest but most JS work.
4. **Group the 3 new metrics under a single colspan'd "Performance" header** with 3 mini-columns — visually bundles them.

### (c) Column count after the addition
- Flat: **13 columns** — not viable in current layout without horizontal scroll.
- Compound (option 2): **10 columns** — viable but tight; would benefit from reducing `px-3`→`px-2` and dropping `whitespace-nowrap` on numeric cells, or widening the container past `max-w-7xl`.
- Sub-row (option 3): stays **7 columns** — fully viable, no re-proportioning of the table itself.

---

## 5. Recommendation

### Realistic M2 scope
The three planned changes are **not equal in cost**:

1. **Portfolio 6-metric strip** — clean, self-contained, low risk. Data: aggregate TC from `bot_state` capital-weighting (already done for "Actual Return"); aggregate CR/MDD from `analytics.compute_aggregate_returns` + `compute_quantstats_metrics` (already built). Mostly a new template section + one route touch. **Ship this in M2.**

2. **Per-symphony label clarity + hover-overs** — low risk, no new data, pure template edit. **Ship this in M2** (do it alongside the strip — small, high operator value).

3. **Per-symphony TC/CR/MDD vs if-held** — **highest cost and has a real data gap.** CR/MDD per symphony only exist via the post-mortem path and only for symphonies that have triggered; it forces a new data dependency onto/around the `/api/state` poll, and it forces a layout decision (10 vs 13 columns vs sub-row). Recommend **splitting this into its own cycle (M3)** or descoping CR/MDD to the expandable sub-row.

**Proposed M2 = strip + clarity pass. Per-symphony metrics = M3** (or M2-stretch only if the operator accepts the sub-row approach).

### Design fork to put back to the operator
**Per-symphony metric layout — the operator must choose before we build #3:**

- **Fork A — 3 compound columns** (10-col table): all metrics always visible, tight fit, mild horizontal pressure. Matches existing design.
- **Fork B — expandable sub-row/drawer**: main table stays 7 columns, metrics on demand, more JS, richest. Recommended if the operator wants the table to stay scannable.
- **Fork C — flat 13 columns**: rejected — forces horizontal scroll on the primary surface.

**Second question for the operator:** per-symphony CR/MDD are only available for symphonies that have **trigger history** in the post-mortem files. Non-triggered symphonies will show "—". Is that acceptable, or does M3 need a new per-symphony daily-return persistence path (a `sqlite-specialist` / engine-specialist task — out of dashboard scope)?
