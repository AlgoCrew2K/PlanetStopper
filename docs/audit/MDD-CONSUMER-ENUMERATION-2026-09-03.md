# AC-0a / AC-0b — MDD Consumer Enumeration & Units-Convention Declaration

**Branch / SHA:** `fix/mdd-window-truth` @ `bf5239ab` (base)
**Author:** `mdd-metric` (analytics.py owner, `mdd-truth` Toxic-Pair team)
**Status:** GATE artifact — blocks AC-1/AC-2/AC-3 per `feature-plans/mdd-window-truth.md`
**Companion:** `docs/audit/PERF-WINDOW-TRUTH-2026-09-03.md` (the defect audit this remediates)

This document satisfies AC-0a (consumer enumeration) and AC-0b (units-convention
declaration) as committed artifacts. It also records the return-contract redesign
proposal derived from the enumeration, which was reported to the PM (`main`) for
confirmation before AC-1 implementation began, per the kickoff directive.

---

## AC-0a — Consumer Enumeration

### A. Production `analytics.py` surface (the functions being redefined)

| Symbol | Location |
|---|---|
| `get_symphony_max_drawdown` | `analytics.py:1008-1082` |
| `get_portfolio_max_drawdown` | `analytics.py:1251-1272` (thin wrapper over `_value_weighted_portfolio`) |
| `_get_shadow_divergence_trajectory` | `analytics.py:823-927` (shared with CR; NOT itself redefined — MDD stops calling it) |
| `compute_windowed_portfolio_strip`'s `max_drawdown` key | `analytics.py:1974-2090`, MDD computed at `analytics.py:2016` |

### B. Production call sites of `get_symphony_max_drawdown` (per-symphony)

| Call site | Consumer | Depends on divergence-residual SHAPE? |
|---|---|---|
| `app.py:1556-1559` (`_safe_analytics(analytics.get_symphony_max_drawdown, ...)`) | Live `/api/state` per-symphony `_mdd` build, feeds `_symphonies_for_cards` (`mdd_bot`/`mdd_held`, `app.py:3222-3223/3251-3252`) and dashboard SSR card render (`templates/index.html:1220-1257` active cards, `:1306-1341` standby cards) | NO — only renders whatever `dry_run`/`if_held` numerically are, as "Bot"/"Held" labels. This IS the defect surface, not a dependency on the old formula. |
| `app.py:2555-2562` | Frozen/closed-market snapshot per-symphony `_mdd` build (mirrors B above for the closed-market branch) | NO — same as above |
| `app.py:3108-3112` | `dashboard()` SSR per-symphony `_mdd` build (feeds `table_partial.html` + `_symphonies_for_cards`) | NO — same as above |

### C. Production call sites of `get_portfolio_max_drawdown` (portfolio-level)

| Call site | Consumer | Depends on divergence-residual SHAPE? |
|---|---|---|
| `app.py:2011-2018` (`_compute_portfolio_strip`, live branch) | Two sub-branches: warm-cache (`_cached_mdd` present) uses `analytics.get_portfolio_max_drawdown(...).get("dry_run")` as the Bot leg paired against `abs(_cached_mdd)` (Composer **account-level** lifetime scalar, cached at `app.py:854-855` from `_metrics["max_drawdown"]`) as the Held leg; cold-cache uses the whole dict directly. Feeds `portfolio_meta["mdd"]`/`["mdd_if_held"]` (`app.py:1704-1709`) → hero vs-row (`templates/index.html:885-909`) | NO for `dry_run` (same defect surface). The warm-cache branch's Held leg (`_cached_mdd`) is a **different, already-lifetime, already-correct** Composer figure that is NOT sourced from `get_portfolio_max_drawdown` at all — it is untouched by this fix and remains the natural source for AC-2's separate lifetime figure at the portfolio level. |
| `app.py:2741-2746` | Frozen/closed-market portfolio `_portfolio_strip["max_drawdown"]` build | NO — same as B/warm-cache above, mirrors the live branch |
| `alpha_bot_execution.py:1142-1144` | EOD snapshot writer (`_persist ... last_market_close_snapshot.portfolio_strip.max_drawdown`), called with the SAME positional/keyword signature (`symphonies, bot_state, trading_day=...`) the live/frozen branches use | NO — pure pass-through storage; the frozen branch (row above) is what actually renders it. **AC-7 constraint:** this call site's signature must not change — confirmed unnecessary, since the fix changes internal computation only, not the function signature. |
| `analytics.py:2016` (`compute_windowed_portfolio_strip`) | Internal call, currently unwindowed (`AC-3`'s defect) | NO — AC-3 replaces this call entirely with a dedicated windowed computation (see Design section) |

### D. Render-layer consumers (templates / JS) of the `if_held`/`dry_run` (aka `mdd_bot`/`mdd_held`/`mdd_alpha`) values

| File:line | Role |
|---|---|
| `templates/index.html:885-909` | Hero vs-row: `mdd_bot`/`mdd_held` set from `meta.portfolio.mdd`/`.mdd_if_held`; computes `mdd_alpha`, `mdd_bot_wins`, renders the α badge + winner/loser bar classes. **This is the exact defect render site the audit flagged (#1 severity).** |
| `templates/index.html:1232-1237` (active cards), `:1317-1322` (standby cards) | Per-symphony card footer: `_mdd_bot_raw`/`mdd_bot` has a `is not none` guard (renders `--` on None); `mdd_held` does **NOT** have an equivalent guard (`(mdd_d.get("if_held", 0) ...) | float` — coerces None to 0.0). **Gap found by this enumeration:** under the redefinition, `if_held` becomes potentially `None` (see AC-0b design section) exactly like `dry_run` already is — this line will fabricate a false "0.0%" Held figure instead of `--` unless mdd-ui adds the same none-guard here. Flagged to mdd-ui. |
| `static/index.js:1217, 1251-1252` | `updateComparisonRows`'s per-card live-poll refresh: reads `sym.mdd_bot`/`sym.mdd_held` directly from `/api/state` JSON, recomputes `mddAlpha = abs(mdd_held) - abs(mdd_bot)` client-side | NO dependency on the old formula's shape — same "just render whatever's there" pattern. Same None-handling gap as the template guard above; mdd-ui should audit this call. |
| `app.py:3222-3224, 3251-3252` | `/api/state`'s `_tc_cr_mdd_floats` unpacks `mdd.get("dry_run")`/`mdd.get("if_held")` into `mdd_bot`/`mdd_held` and ships them in the `symphonies` JSON array (live-poll payload for `static/index.js`) | NO |
| `app.py:1704-1709` | `portfolio_meta` dict (`"mdd"`, `"mdd_if_held"`) — feeds `templates/index.html:885-886` | NO |

### E. NOT consumers (checked, confirmed clean — zero hits)

- `reporting.py` — **zero** references to `max_drawdown`/`mdd` (grep confirmed). No Discord embed, no post-mortem field, touches this metric.
- `advisors/*.py` — **zero** references to `get_symphony_max_drawdown`/`get_portfolio_max_drawdown`/`mdd_bot`/`mdd_if_held`/`mdd_alpha` (grep confirmed). No advisor reasoning depends on this metric.
- `static/performance.js` / `templates/performance.html` — **zero** calls to either function. The Performance tab's `max_drawdown` is computed independently by `compute_quantstats_metrics`'s `qs_stats.max_drawdown()` call (`analytics.py:443`) over a route-selected returns series — a wholly separate code path, confirmed correct by the audit, untouched by AC-1/AC-2/AC-3 (AC-7).
- `/api/strip/<window>`'s response `max_drawdown` field (`analytics.py:2084`) — reaches the JSON response and is fetched by `static/index.js:1492-1505` (`fetchWindowedStrip`), but `updateComparisonRows` is explicitly gated (per its own in-file comment) to skip re-rendering any of the three vs-rows — including MDD — from a windowed-strip payload; only the headline guard-alpha % re-renders from it. **Confirmed dormant** (matches the audit's D-8/AC-3 characterization) at both call sites that build it (`app.py:2140-2157` live, `app.py:2790-2804` frozen) — both extract only `guard_alpha`/`window`/`cumulative_return` from the returned dict, never `max_drawdown`.
- `invested_since` — **NOT currently captured anywhere.** Per `docs/research/dashboard/composer-per-symphony-stats.md:93` it exists in the Composer `symphony-stats-meta` per-symphony payload, but `alpha_bot_execution.py:267-275` (`_persist_composer_fields_to_bot_state`, the ONLY place Composer fields are written into `bot_state`) persists exactly 4 fields — `simple_return`, `net_deposits`, `time_weighted_return`, `max_drawdown` — and `invested_since` is not one of them. See the AC-2/AC-7 tension flagged in the Design section below.

### F. Test suite (rewritten by `mdd-test`, not enumerated line-by-line here)

~150 references across `tests/analytics/`, `tests/app/`, `tests/dashboard/`, `tests/ui/`, `tests/shadow/`, `tests/engine/` (full grep list available in scratchpad, not reproduced here to keep this artifact readable). Two categories:

1. **Mocked-return-value tests** (`m.get_symphony_max_drawdown.return_value = {...}`) — assert on template/JS wiring given an arbitrary `{if_held, dry_run}` dict. **Unaffected** by the redefinition; these don't encode the formula.
2. **Golden-formula tests that assert the CURRENT divergence-residual semantics as correct**, most notably `tests/analytics/test_shadow_divergence_golden.py:333-531` (`TestMddOnBotEquityPath`, e.g. "MDD ANCHOR INVARIANT: never-triggered symphony → bot shadow-window MDD == 0" and the golden peak-to-trough-on-divergence-series value). **These are the tests being fixed** — they pin the bug as a feature and must be rewritten by `mdd-test` to assert the new normalized, comparable-window formula. This is expected TDD churn (RED→GREEN on the corrected contract), not a "consumer that must be preserved" in the AC-0a sense — no *production* behavior depends on it.

**AC-0a verdict:** every production consumer of `if_held`/`dry_run` from these two functions is a pure pass-through render (template, JS, JSON payload, EOD snapshot storage) with no arithmetic dependent on the OLD formula's translation-invariant shape. **No production consumer requires the divergence-residual semantics to be preserved.** The one genuine shape dependency found is structural, not semantic: **the Composer lifetime scalar (currently `if_held`) is still needed by AC-2 as a separate figure**, so it cannot simply be deleted — it must be exposed under a **new** key rather than silently dropped when `if_held`/`dry_run` are redefined to the windowed comparable pair. See Design section.

---

## AC-0b — Units-Convention Declaration

**Finding: they differ, and the difference is NOT (only) percentage-point-vs-fraction scale — it is ADDITIVE (un-normalized) vs. RELATIVE (normalized) drawdown.**

- **Composer's `max_drawdown`** (`sym_dict["max_drawdown"]`, read at `analytics.py:1040-1042`): a **normalized fraction** — peak-to-trough as a fraction of peak NAV, i.e. `(peak_NAV − trough_NAV) / peak_NAV`. Documented at `docs/research/dashboard/composer-per-symphony-stats.md:93`: *"positive number, not signed... since invested_since (full holding history)."* Multiplied by `100.0` at the read site to express as a percentage (e.g. `0.2552 → 25.52`), but the underlying quantity is still the NORMALIZED (peak-relative) drawdown.
- **The current `dry_run`** (`analytics.py:1058-1080`): builds `bot_equity[t] = if_held + epoch_start_alpha + (product_shadow − product_current) * 100.0`, then computes `max_dd = peak − val` by direct SUBTRACTION of two points on that **percentage-point LEVEL series**. This is an **ADDITIVE (un-normalized) peak-to-trough** — it never divides by the peak level. This is the exact reason it is translation-invariant to `if_held` (proven empirically: unchanged to 10 decimals across injected `max_drawdown` values `0.0`/`0.1805`/`9.99`/`−5.0`) — subtracting two points on a curve is invariant to a constant additive shift of the whole curve; a genuinely NORMALIZED (divide-by-peak) drawdown cannot be.
- **The codebase's own established, audit-confirmed-correct convention for this exact metric** already exists and is used elsewhere: `compute_quantstats_metrics` (`analytics.py:348-456`) calls `qs_stats.max_drawdown(series)` (`analytics.py:443`) on a **fraction-scale return series**, which quantstats compounds into a NAV index internally and returns the genuine **normalized, peak-relative** drawdown (≤ 0, drawdown convention). This is the function backing the Performance tab, which the audit explicitly confirmed has **no computation bug** (`PERF-WINDOW-TRUTH-2026-09-03.md` §4, D2/E-4/E-5) — it is the correct reference implementation already living in this file.

**Declared convention for the fixed metric:** adopt the **NORMALIZED (peak-relative, quantstats-style) drawdown**, computed by compounding the percent-scale `shadow_return`/`current_return` daily series into a NAV-index path and taking genuine peak-to-trough as a fraction of the running peak — i.e. reuse `compute_quantstats_metrics(...)["max_drawdown"]` directly (do not hand-roll a second peak-to-trough loop; AC-7 forbids re-deriving math already audited as correct without a golden-fixture equivalence check, and reuse is the only way to get that check for free). Expressed as a **positive percentage magnitude** at the function boundary (`abs(...) * 100.0`), preserving the existing D8 canonical convention documented at `analytics.py:1018` and pinned by `tests/analytics/test_drawdown_sign_convention.py`.

This will be stated verbatim in the redefined functions' docstrings (AC-0b requirement) once implemented.

---

## Design Section — Contract Redesign (reported to `main` for confirmation before AC-1 proceeded)

Derived directly from AC-0a's finding that the Composer lifetime scalar must survive as a **separate** value (AC-2) while `if_held`/`dry_run` are redefined to a genuinely comparable pair (AC-1):

1. **`get_symphony_max_drawdown` / `get_portfolio_max_drawdown` signatures are UNCHANGED** (no new required params) — every one of the 7 production call sites (B/C above) passes the same positional/keyword args today and after the fix. This is what keeps `alpha_bot_execution.py:1142-1144` at **zero diff** (AC-7) — the call site doesn't need to change; only the function body does.
2. **Return-dict shape gains one additive key, `if_held_lifetime`**, carrying the OLD `if_held` value (Composer's own scalar, unchanged computation) — satisfies AC-2 without deleting data any consumer might still want. `if_held`/`dry_run` are redefined in place to the new normalized, same-window comparable pair (held from `current_return`, bot from `shadow_return`, both via `get_symphony_bot_and_held_daily_returns`/`get_portfolio_bot_and_held_daily_returns` — already-existing, already-tested continuous series builders that use the SAME `bot_state.current_value` weighting `_value_weighted_portfolio` uses, satisfying AC-1's weighting clause for free).
3. **Both legs can now independently be `None`** (when `get_..._bot_and_held_daily_returns` returns `None` for <2 distinct trading days) — this is a NEW possibility for `if_held` specifically (it was previously always-available from Composer whenever `max_drawdown` was non-null). **Render-layer gap found:** `templates/index.html:1234`/`:1319` coerce `if_held` via `(mdd_d.get("if_held", 0) ...) | float` with no none-guard, unlike `dry_run`'s existing `is not none` guard at `:1232-1233`/`:1317-1318`. This will fabricate a false `0.0%` Held figure post-fix unless mdd-ui adds the same none-guard symmetrically. Flagged to mdd-ui/mdd-test.
4. **AC-3 (`compute_windowed_portfolio_strip`) does NOT call the redefined `get_portfolio_max_drawdown`** (which stays a lifetime/all-available-history computation, matching its unchanged signature). Instead it computes MDD directly from the series it **already fetches and slices** for `vol_bot`/`vol_held` (`analytics.py:2065-2077`) — reusing the identical cutoff-sliced `bot_pct`/`held_pct` arrays to also produce `compute_quantstats_metrics(...)["max_drawdown"]` for each leg, gated by the same `_WINDOWED_VOL_MIN_DAYS` sufficiency floor already in place. This avoids a second SQL round-trip and keeps AC-3 consistent with the existing W1 slice-then-regroup windowing pattern the vol computation already uses.
5. **Portfolio-level `if_held_lifetime`** is the VW aggregate (via the existing `_value_weighted_portfolio` machinery) of each symphony's own Composer scalar — i.e., structurally the SAME computation `get_portfolio_max_drawdown`'s `if_held` performed before this fix, just renamed/relocated to the new key. Distinct from (and NOT a replacement for) the account-level `_account_totals_cache["portfolio_mdd"]` warm-cache figure (`app.py:854-855`, `app.py:2007`) — that field is untouched by this fix and remains available to `mdd-ui` as the preferred AC-2 portfolio-level lifetime figure when warm, exactly as it always has been.
6. **`invested_since` (AC-2's "naming its start" clause) is NOT reachable without touching `alpha_bot_execution.py`**, per AC-0a section E — it is not persisted into `bot_state` anywhere today, and the only place that could persist it (`_persist_composer_fields_to_bot_state`, `alpha_bot_execution.py:267-275`) is inside the file AC-7 hard-freezes at zero diff. **This is a genuine tension between AC-2's literal wording and AC-7's hard invariant, reported to `main` for a ruling** — proposed resolution (pending confirmation): label the separate lifetime figure generically ("Lifetime Max Drawdown · since inception") without the specific `invested_since` date this cycle, and track exact-date labeling as a follow-up that touches `alpha_bot_execution.py` under its own AC-7 exception or a dedicated small cycle.

**Golden-fixture note:** AC-1's target figures (`held 10.5875`, `bot 10.3622` on the 53-day window) are computable via `get_portfolio_bot_and_held_daily_returns(db_file, days=None)` → `compute_quantstats_metrics(...)["max_drawdown"]` on each leg, `abs()*100`. `mdd-test` should construct the golden fixture's `shadow_history` rows to reproduce this exact pair via that path (not by hand-deriving a different peak-to-trough formula) — this is the concrete way to verify the redefinition change is complete and correctly reused, not partially reused.
