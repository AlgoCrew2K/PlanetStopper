# Feature: Max-Drawdown Window Truth
Status: ready
Created: 2026-09-03

## Summary
Remediates the confirmed findings of `docs/audit/PERF-WINDOW-TRUTH-2026-09-03.md` (`DE-PERF-WINDOW-TRUTH-001`). The operator reported that switching the Performance tab from 60d to YTD changed nothing in the drawdown, and that the displays disclose no restriction on data length. The audit confirmed the Performance tab's arithmetic is correct but its coverage reporting is dishonest — and found two **more severe, previously unreported defects on the MAIN DASHBOARD**, where the Bot-vs-Held Max Drawdown comparison pairs two quantities that are not comparable in period, in subject, or (pending AC-0b) in units — then renders an `α` delta badge and a `winner` class on top of them.

**Measured impact: 98.7% of the rendered drawdown-reduction badge is basis artifact.** Rendered `if_held 23.4449` vs `dry_run 5.7875` (gap 17.66pp); honest same-window, same-weighting comparison is `held 10.5875` vs `bot 10.3622` (gap **0.23pp**).

Display/aggregation layer only. **`alpha_bot_execution.py` and `math_engine.py` MUST carry ZERO diff** — no change to any exit decision, trade, or `LIVE_EXECUTION` path.

## Acceptance Criteria

- [ ] **AC-0a (GATE — consumer enumeration, blocks AC-1):** Before redefining any shared quantity, enumerate EVERY consumer of `get_symphony_max_drawdown` / `get_portfolio_max_drawdown` / `dry_run` / `mdd_bot` / `mdd_if_held` / `mdd_alpha` across routes, templates, JS, post-mortems, Discord embeds, and advisors. Produce the list with `file:line`. **If any consumer depends on the current divergence-residual semantics, the fix introduces a NEW correctly-shaped value alongside rather than mutating the existing one in place.** The enumeration is a committed artifact, not a verbal claim.
- [ ] **AC-0b (GATE — units convention, blocks AC-1):** State explicitly, with `file:line`, whether the current `dry_run` is an un-normalized percentage-point peak-to-trough while Composer's `max_drawdown` is a normalized fraction. Declare ONE convention for the fixed metric and document it in the function docstring. (Evidence that they differ: `dry_run` is provably translation-invariant to `if_held`, which a normalized drawdown cannot be.)
- [ ] **AC-1 (the #1 defect — both legs recomputed, not merely re-based):** The Bot-vs-Held Max Drawdown comparison must compare two genuinely comparable quantities: **peak-to-trough of the compounded return path over ONE shared window**, held from `current_return` and bot from `shadow_return`, aggregated with the existing `_value_weighted_portfolio` `current_value` weighting. Re-basing the period ALONE is explicitly insufficient — `dry_run` currently measures the guard-alpha *divergence residual* (translation-invariant to `if_held`; empirically unchanged to 10 decimals across injected `max_drawdown` values `0.0`/`0.1805`/`9.99`/`−5.0`), which is structurally small at ANY history depth. Golden-fixture pinned against the audit's verified figures (`held 10.5875`, `bot 10.3622` on the 53-day window).
- [ ] **AC-2 (lifetime scalar kept, but never as a leg) — [AMENDED, team-lead ruling, 2026-09-03, see Decisions]:** Composer's lifetime `max_drawdown` scalar remains available and is rendered as its OWN clearly-labelled figure (~~naming its `invested_since` start~~ **naming that it covers full holding history since inception** — label text: "Lifetime Max Drawdown · since inception", no date this cycle), never as the counterpart in the Bot-vs-Held subtraction.
- [ ] **AC-3 (`/api/strip` window-blindness, F3):** `compute_windowed_portfolio_strip`'s `max_drawdown` must actually respond to `window` — thread it through the same seam `compute_windowed_symphony_guard_alpha` already uses. Currently identical across all 6 tokens while `cumulative_return`/`vol_bot`/`vol_held` in the same response vary correctly. (Dormant today — zero render consumers — so this is a contract fix, not a live-symptom fix; it must NOT be shipped without AC-1, or one leg becomes windowed while the other stays lifetime: responsive-looking and still incomparable.)
- [ ] **AC-4 (coverage disclosure — RENDERED, not a JSON field):** The Performance tab must state its ACTUAL coverage to the operator on screen, and must not silently present a window it cannot honor. Primary precedent is `guard_preconditions.verdict_copy()`/`INSUFFICIENT_DATA` — operator-facing PROSE — with `coverage_days`/`date_range` as the supporting data. **A response field with no render consumer does NOT satisfy this AC** (see `DE-AUDIT-BL4-001`, where honesty markers were computed correctly and rendered nowhere for months). Acceptance is stated in terms of what appears on screen.
- [ ] **AC-5 (`window_days` stops lying):** `/api/performance` (`app.py:4907`) and `/api/history/<days>` (`app.py:4628`) currently echo the REQUESTED window regardless of what was returned. Keep `window_days` as "requested" and add honest `actual_days`/`coverage_days` (the already-computed `observation_count`) plus a real `date_range`. A bolt-on field beside an unchanged, still-misleading `window_days` does NOT satisfy this AC.
- [ ] **AC-6 (`"ytd"` caption render bug):** `static/performance.js:444-458` renders the window label only when `typeof window_days === 'number'`, so the string `"ytd"` silently drops all window context — wrong at any data depth. Fix so every window token renders its label.
- [ ] **AC-7 (no-regression invariants):** `alpha_bot_execution.py` and `math_engine.py` carry ZERO diff. No new exec/trade/liquidation primitive. No schema migration. `/api/performance`'s own MaxDD arithmetic (confirmed CORRECT by the audit) is not "fixed" — only its coverage reporting changes.

## Architecture
- `analytics.py`: `get_symphony_max_drawdown` / `get_portfolio_max_drawdown` (AC-1, AC-2 — the metric redefinition + optional `window` param), `compute_windowed_portfolio_strip` (AC-3 threading).
- `app.py`: `/api/performance` + `/api/history/<days>` coverage fields (AC-5), `_compute_portfolio_strip`/`portfolio_meta` wiring (AC-1 render inputs).
- `templates/index.html`: hero vs-row `:885-909` and both per-symphony card blocks `:1233-1237`, `:1318-1322` (AC-1 render, AC-2 separate lifetime figure).
- `static/index.js`: `updateComparisonRows` `:1043-1057` (AC-1 live-poll overwrite path).
- `static/performance.js` + `templates/performance.html`: AC-4 disclosure, AC-6 caption.
- Byte-frozen: `alpha_bot_execution.py`, `math_engine.py`.

## Edge Cases
- Zero/one observation in window; all-positive series (no drawdown); a symphony absent from `shadow_history`; a symphony present in `bot_state` but with no Composer scalar; window shorter than available data (must genuinely subset); window longer than available data (must disclose, not silently collapse); the frozen/closed-market branch (`get_state`) as well as the live poll; NaN/None on either leg must degrade honestly, never render a fabricated 0.0.

## Security Considerations
- No new external input surface; no new write path; nothing added to `_SETTINGS_WRITE_ALLOWLIST`; no `LIVE_EXECUTION` reference. All rendered values stay `| e`-escaped, no `| safe`. Read-only DB access on every touched path.

## Testing Strategy
- RED-first. Golden fixtures pinning AC-1's corrected values against the audit's verified numbers; a translation-invariance REGRESSION test (inject differing `if_held` values and assert the bot leg NOW responds, i.e. the old invariance is gone); AC-3 asserts MaxDD differs across window tokens where data supports it; AC-4 asserts rendered copy (not just a payload key); AC-5 asserts `actual_days`/`date_range` present AND that `window_days` semantics are documented; AC-6 parametrized across every window token including `"ytd"`.
- Non-regression: full retirement + dashboard + analytics suites; `alpha_bot_execution.py`/`math_engine.py` zero-diff guard.
- `-n0` + scratch `DB_PATH` locally (NEVER bare/`-n>0`). Full-tree CI (`-n2 --dist loadfile`) on the exact SHA is the authoritative gate.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Fix BOTH the period mismatch and the metric definition | The partial fix moves the display 23.44 → 10.59 and still shows the bot winning by 4.80pp — ~21× the true 0.23pp — while LOOKING repaired. That launders the error rather than fixing it. |
| Disclosure is NOT sufficient for AC-1 | The row exists to answer "is the bot helping." A tooltip converts a confidently-wrong answer into an admittedly-useless one without answering it. This is the ONE finding in the audit where disclosure alone fails. |
| AC-3 must not ship without AC-1 | Threading `window` while one leg remains a lifetime scalar produces a responsive-looking, still-incomparable number — worse than the current visible breakage. |
| Consumer enumeration gates the redefinition | `dry_run` is a shared quantity; redefining it blind risks an engine-adjacent regression on an unexamined surface. |
| **[AMENDED AC-2, team-lead ruling, 2026-09-03]** AC-2's lifetime figure is labelled generically ("Lifetime Max Drawdown · since inception"), NOT with the actual `invested_since` date, this cycle | `invested_since` is not persisted anywhere in `bot_state` — the only site that could persist it, `alpha_bot_execution.py:267-275` (`_persist_composer_fields_to_bot_state`), sits inside AC-7's hard zero-diff freeze on `alpha_bot_execution.py`/`math_engine.py`. Team lead declined to relax AC-7 for this: "zero diff except one additive line" is a negotiation, not an invariant, and would weaken the guard test that currently verifies the freeze mechanically (`git diff` EMPTY). Exact-date labeling is a tracked follow-up, not silently dropped. **Explicitly ruled OUT:** fetching `invested_since` from Composer live at render time — a network dependency on the render path to win a cosmetic label, worse than the generic wording. |

## Scope Boundaries
- **IN:** AC-0a/0b through AC-7 above.
- **OUT:** the trading-day-vs-calendar-day token SEMANTIC UNIFICATION (Performance's 252/1260 vs History's 365/1825, and porting Performance onto the shared string-token seam) — a **product decision** with a visible number change, tracked separately, NOT a defect fix. Composer daily-series integration (3 endpoints found, reach unprobed) — separate cycle, and any use must provenance-tag realized-vs-backtest data per `DE-POSTMORTEM-INTEGRITY-001`. The dormant `analytics.load_post_mortem_history` positional slice — document only. `shadow_history` depth — a matter of elapsed time (181 aligned days ≈ 2027-03-10), not configuration.
