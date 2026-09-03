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

**UPDATE (2026-09-03, post-team-lead relay):** the audit team independently inspected the
same code and answered AC-0b authoritatively before this section's initial draft was
finalized. The finding below is that authoritative answer, **verified** (not re-derived)
against the source at the cited lines, plus the implementation-mechanism decision that
verification surfaced (direct formula vs. reusing `compute_quantstats_metrics`).

**Finding: they differ, and the difference is NOT (only) percentage-point-vs-fraction scale — it is ADDITIVE (un-normalized) vs. RELATIVE (normalized) drawdown. This is a THIRD stacked defect, distinct from the period mismatch (lifetime vs. 53-day window) and the subject mismatch (divergence residual vs. portfolio drawdown) — all three push the same direction (all make `dry_run` artificially small relative to `if_held`), which is why the rendered gap is 17.66pp and the true one is 0.23pp.**

- **`if_held`** (`sym_dict["max_drawdown"]`, read at `analytics.py:1042`): `float(sym_dict["max_drawdown"]) * 100.0` — Composer's **NORMALIZED** max drawdown, the industry-standard `(peak − trough) / peak` convention (matches quantstats' own convention too). Documented at `docs/research/dashboard/composer-per-symphony-stats.md:93`: *"positive number, not signed... since invested_since (full holding history)."*
- **`dry_run`** (`analytics.py:1073-1079`): the loop is literally `dd = peak - val` with **no `/ peak` anywhere** — a **RAW, UN-NORMALIZED** percentage-point difference, confirmed by direct inspection. This is the exact reason it is translation-invariant to `if_held` (proven empirically: unchanged to 10 decimals across injected `max_drawdown` values `0.0`/`0.1805`/`9.99`/`−5.0`) — a normalized ratio CANNOT be shift-invariant (its denominator moves with the shift), so the earlier empirical invariance result is itself independent proof of the un-normalized form. The two findings corroborate each other.

**Declared convention for the fixed metric: NORMALIZED `(peak − value) / peak`, expressed ×100.** Rationale: matches Composer's own convention, matches quantstats' convention (`analytics.py:443`, `compute_quantstats_metrics`'s already-audited, already-correct Performance-tab formula — confirmed no computation bug by `PERF-WINDOW-TRUTH-2026-09-03.md` §4 D2/E-4/E-5), and — decisively — **the audit's own reference figures (`held = 10.5875`, `bot = 10.3622`) were computed with exactly `dd = (peak − equity) / peak * 100.0`** applied directly to the compounded `shadow_return`/`current_return` NAV path. Golden fixtures and the implementation are therefore already in the same convention as long as the implementation uses this exact formula.

**Implementation-mechanism decision (mine, made verifying the above): do NOT route this through `compute_quantstats_metrics`/pandas/quantstats.** Two reasons: (1) it is algebraically equivalent for a plain compounded series with no day-0 anchor ambiguity (`quantstats.stats.max_drawdown`'s `(prices / prices.expanding().max()).min() - 1` reduces to the identical `(peak−price)/peak` at each point, with the running peak beginning at the first REAL compounded value — no synthetic pre-day-1 anchor — so a hand loop starting `peak = equity[0]` matches it exactly), so reuse buys no additional correctness guarantee over implementing the pinned formula directly; (2) `get_symphony_max_drawdown` is called per-symphony on every live poll (`app.py:1558/2555/3108`) — a pandas+quantstats round-trip per symphony per poll is unjustified overhead when the pinned formula is a trivial, three-line, easily golden-fixture-verified loop. **Implement the direct formula as a small named helper**, documented per the no-magic-numbers/source-comment standard, with the formula stated verbatim in the docstring (AC-0b requirement) — not a `compute_quantstats_metrics` call.

**New anchor invariant (supersedes the old "never-triggered → MDD == 0" test):** under this convention, when `shadow_return == current_return` on every day (guard never triggered), the bot and held NAV paths are IDENTICAL, so `dry_run == if_held` exactly — NOT necessarily `0.0` (that would only hold if the market itself had zero drawdown). The old `TestMddOnBotEquityPath` golden tests in `tests/analytics/test_shadow_divergence_golden.py:333-531` assert the OLD (now-superseded) `dry_run == 0` invariant and must be rewritten by `mdd-test` to assert `dry_run == if_held` for the never-triggered case instead — flagged in the AC-0a §F test-suite note above, restated here because it follows directly from the units fix.

**Scope ruling relayed from `main` (2026-09-03): keep AC-1 self-contained to `shadow_history`.** Composer separately exposes a real per-symphony daily series (538 points, reaching `invested_since` 2024-07-12, zero truncation) that would let `if_held` be windowed to arbitrary depth — **explicitly NOT in this cycle.** A new external data dependency (provenance tagging, caching, rate limits, error handling) is its own cycle; this fix ships now using only `shadow_history`-derived series via the existing `get_symphony_bot_and_held_daily_returns`/`get_portfolio_bot_and_held_daily_returns`, exactly as originally proposed below. **This also resolves the `invested_since`/AC-7 tension flagged earlier in this document**: fetching `invested_since` would itself require either a new Composer call (out of scope per this ruling) or touching `alpha_bot_execution.py` (forbidden by AC-7) — so AC-2's separate lifetime figure ships this cycle with a generic label ("Lifetime Max Drawdown · since inception"), not the exact `invested_since` date. The exact-date labeling is a tracked follow-up, not a blocker.

---

## Design Section — Contract Redesign (reported to `main` for confirmation before AC-1 proceeded)

Derived directly from AC-0a's finding that the Composer lifetime scalar must survive as a **separate** value (AC-2) while `if_held`/`dry_run` are redefined to a genuinely comparable pair (AC-1):

1. **`get_symphony_max_drawdown` / `get_portfolio_max_drawdown` signatures are UNCHANGED** (no new required params) — every one of the 7 production call sites (B/C above) passes the same positional/keyword args today and after the fix. This is what keeps `alpha_bot_execution.py:1142-1144` at **zero diff** (AC-7) — the call site doesn't need to change; only the function body does.
2. **Return-dict shape gains one additive key, `if_held_lifetime`**, carrying the OLD `if_held` value (Composer's own scalar, unchanged computation) — satisfies AC-2 without deleting data any consumer might still want. `if_held`/`dry_run` are redefined in place to the new normalized, same-window comparable pair (held from `current_return`, bot from `shadow_return`, both via `get_symphony_bot_and_held_daily_returns`/`get_portfolio_bot_and_held_daily_returns` — already-existing, already-tested continuous series builders that use the SAME `bot_state.current_value` weighting `_value_weighted_portfolio` uses, satisfying AC-1's weighting clause for free).
3. **Both legs can now independently be `None`** (when `get_..._bot_and_held_daily_returns` returns `None` for <2 distinct trading days) — this is a NEW possibility for `if_held` specifically (it was previously always-available from Composer whenever `max_drawdown` was non-null). **Render-layer gap found:** `templates/index.html:1234`/`:1319` coerce `if_held` via `(mdd_d.get("if_held", 0) ...) | float` with no none-guard, unlike `dry_run`'s existing `is not none` guard at `:1232-1233`/`:1317-1318`. This will fabricate a false `0.0%` Held figure post-fix unless mdd-ui adds the same none-guard symmetrically. Flagged to mdd-ui/mdd-test.
4. **AC-3 (`compute_windowed_portfolio_strip`) does NOT call the redefined `get_portfolio_max_drawdown`** (which stays a lifetime/all-available-history computation, matching its unchanged signature). Instead it computes MDD directly from the series it **already fetches and slices** for `vol_bot`/`vol_held` (`analytics.py:2065-2077`) — reusing the identical cutoff-sliced `bot_pct`/`held_pct` arrays to also run the SAME pinned-formula helper (peak-relative `(peak-value)/peak*100`, see AC-0b) for each leg, gated by the same `_WINDOWED_VOL_MIN_DAYS` sufficiency floor already in place. This avoids a second SQL round-trip and keeps AC-3 consistent with the existing W1 slice-then-regroup windowing pattern the vol computation already uses.
5. **Portfolio-level `if_held_lifetime`** is the VW aggregate (via the existing `_value_weighted_portfolio` machinery) of each symphony's own Composer scalar — i.e., structurally the SAME computation `get_portfolio_max_drawdown`'s `if_held` performed before this fix, just renamed/relocated to the new key. Distinct from (and NOT a replacement for) the account-level `_account_totals_cache["portfolio_mdd"]` warm-cache figure (`app.py:854-855`, `app.py:2007`) — that field is untouched by this fix and remains available to `mdd-ui` as the preferred AC-2 portfolio-level lifetime figure when warm, exactly as it always has been.
6. **`invested_since` (AC-2's "naming its start" clause) is NOT reachable without touching `alpha_bot_execution.py`**, per AC-0a section E — it is not persisted into `bot_state` anywhere today, and the only place that could persist it (`_persist_composer_fields_to_bot_state`, `alpha_bot_execution.py:267-275`) is inside the file AC-7 hard-freezes at zero diff. **RESOLVED by the scope ruling above** (no new Composer fetch this cycle): the separate lifetime figure ships with a generic label ("Lifetime Max Drawdown · since inception") without the exact `invested_since` date this cycle; exact-date labeling is a tracked follow-up.

**Golden-fixture note (updated to the pinned formula):** AC-1's target figures (`held 10.5875`, `bot 10.3622` on the 53-day window) are computable via `get_portfolio_bot_and_held_daily_returns(db_file, days=None)` → compound each leg's percent-return series into a NAV index (`level *= 1 + r/100`, starting `level=1.0`, no synthetic pre-day-1 anchor) → running-peak `(peak-value)/peak` max, `*100`. `mdd-test` should construct the golden fixture's `shadow_history` rows to reproduce this exact pair via that path and formula (not `compute_quantstats_metrics`, not a re-derived variant) — this is the concrete way to verify the redefinition is complete and uses the one pinned convention, matching AC-0b's implementation-mechanism decision above.

**Analytics-side implementation checklist derived from this document (for my own GREEN pass once mdd-test's handoff publishes):**
- New pure helper (name TBD at implementation time, e.g. `_peak_relative_drawdown(pct_returns) -> float | None`) implementing the pinned formula, `None` on `< 2` observations, positive-percentage-magnitude return (D8 convention). Named constants only — no magic numbers; if a sufficiency floor is needed it reuses an existing named constant (e.g. `_WINDOWED_VOL_MIN_DAYS`/`_V1_BOOTSTRAP_MIN_DAYS`) rather than inventing a new one.
- `get_symphony_max_drawdown`: `if_held`/`dry_run` computed from `get_symphony_bot_and_held_daily_returns(symphony_id, db_file, days=None)` (held/bot legs respectively) through the new helper; `if_held_lifetime` = old computation (`float(sym_dict["max_drawdown"]) * 100.0`) preserved verbatim under the new key. Signature unchanged.
- `get_portfolio_max_drawdown`: same shape at the portfolio level via `get_portfolio_bot_and_held_daily_returns(db_file, days=None)`; `if_held_lifetime` = `_value_weighted_portfolio` VW aggregate of each symphony's raw Composer scalar (the exact computation `if_held` used to perform). Signature unchanged.
- `compute_windowed_portfolio_strip`: replace the unwindowed `mdd = get_portfolio_max_drawdown(...)` call with a windowed computation over the already-sliced `bot_pct`/`held_pct` arrays via the same new helper, gated by `_WINDOWED_VOL_MIN_DAYS`.
- Docstrings on both redefined functions state the formula verbatim (AC-0b requirement) and the D8 positive-magnitude convention.
- No touch to `alpha_bot_execution.py`/`math_engine.py` (AC-7) — verified no call-site changes needed anywhere in that file.
