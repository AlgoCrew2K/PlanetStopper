# advisors/retirement_recommender

> Advisory, read-only math core that flags a live symphony as a *retirement candidate* when it is both redundant (highly correlated with a live sibling) and the weaker performer of the pair -- gated by two conservative, fail-closed regime checks so a calm-market correlation point estimate never over-prunes crash-diversification. No trade, order, liquidation, or `LIVE_EXECUTION` primitive of any kind.

**Source:** `advisors/retirement_recommender.py`
**Last updated:** 2026-08-26 (new module, Phase 2 Cycle 2a, `DE-RETIRE-CORE-001` + Revise round: full-fleet composite normalization + the 03:45 scheduler-tick producer + Revise round 2: fail-closed candidate selection, downside stress-window, logged internal failures, F2/F6 accepted limitations)

## Overview

`advisors/retirement_recommender.py` implements the deterministic math core of the Retirement Recommender: three stages, each conservative and fail-closed.

1. **Screen** (`screen_correlated_pairs`) -- pairwise Pearson correlation over each live symphony's CONTINUOUS actual-traded (bot) daily return series, a thin wrapper over the existing `advisors.correlation_diagnostic.compute_pairwise_correlations`.
2. **Composite rank** (`compute_composite_scores` / `select_retirement_candidate`) -- a CAGR-dominant, fleet-normalized performance score identifies which member of a flagged pair is the weaker performer (the retirement candidate).
3. **Gates** (`evaluate_uncertainty_gate` / `evaluate_structural_redundancy_gate`) -- a bare correlation point estimate over-prunes crash-diversification (this is the Phase-1 audit's "option C" finding, cited verbatim in the module docstring): a recommendation only survives if the correlation estimate is statistically robust (uncertainty gate) AND the redundancy holds across regimes, not just in calm markets (structural-redundancy gate).

`build_recommendations()` orchestrates all three stages against the live roster and returns a flat list of evidence dicts; `persist_recommendations()` writes each one as an append-only `advisor_observations` row. **This module never moves money and never writes settings** -- structurally enforced by `tests/security/test_retirement_recommender_no_trade_boundary.py` (adversarial source-scan, mirrors `tests/security/test_frontrunner_no_trade_boundary.py`).

Cycle 2a ships ONLY this math core + advisory persistence + the read-only route/panel (see [app](app.md)). The LLM explainer, the operator approval/reject lifecycle, and the Composer liquidation checklist are explicitly Cycle 2b -- out of scope here, and this module contains no seam for any of them.

## The basis rule -- read this before touching the module

**Both the screen (stage 1) and the composite metrics (stage 2) are computed over the SAME series: element `[1]` (bot / actual-traded) of `analytics.get_symphony_bot_and_held_daily_returns(symphony_id, days=RETIREMENT_LOOKBACK_DAYS)`.** This is deliberate and load-bearing, not an implementation detail:

- **Never `analytics.compute_per_symphony_returns`'s trigger-day series.** That series is sparse and selection-biased -- it contains ONLY the days a symphony actually recorded a trigger, silently omitting every day it just held. `analytics.py:1698-1707` documents the concrete failure mode this produces: a 4-trigger sample averaging ~0.45%/day annualizes to an absurd ~209.8% CAGR when treated as consecutive trading days. A retirement decision is a capital decision; it must never rest on that kind of statistic.
- **Never element `[2]` (the if-held / counterfactual series).** Retirement is about how the symphony ACTUALLY performed under Planet Stopper's own exit/guard-alpha logic, not the counterfactual of never having a stop at all. `/api/performance`'s own JSON labels are the opposite of what they sound like (`live_metrics` there means the *counterfactual*) -- a documented historical inversion trap in this codebase. `build_recommendations` reads the tuple as `dates, bot_returns, _held_returns = result` and the held leg is discarded with a leading underscore, never touched again.
- **One coherent basis throughout.** Using the same continuous bot series for both the correlation screen and the composite ranking avoids introducing a second, independently selection-biased source and keeps the screen's redundancy claim consistent with the ranking that decides which sibling is "weaker."

`raw_response["basis_label"]` (`_BASIS_LABEL = "actual-traded (bot) daily returns"`) is stamped onto every persisted recommendation and every route/panel response, verbatim, so this basis is visible to the operator alongside the recommendation itself -- never assumed, always disclosed.

## Named constants -- real shipped values (not the plan's placeholders)

Every constant is source-commented in `advisors/retirement_recommender.py` (`:35-119`) with its rationale. Values as actually shipped at HEAD `f9819274`:

| Constant | Value | Meaning / why this value |
|----------|-------|---------------------------|
| `CORRELATION_SCREEN_THRESHOLD` | `0.65` | Screen bar (AC-1). Operator/Phase-1-audit ruling -- a spec-pinned literal, not tuned. |
| `MIN_OBS_FLOOR` | `30` | Minimum overlapping observations for the uncertainty gate (AC-5). **Reused, not reinvented:** `= correlation_diagnostic.THIN_DATA_THRESHOLD`, the same Bailey/de Prado (2014) interpretability floor already established elsewhere in this codebase. |
| `UNCERTAINTY_CI_CONFIDENCE` | `0.95` | Two-sided confidence level for the Fisher-z CI on the correlation estimate (AC-5). |
| `_Z_95` | `1.96` | Critical value for the 0.95 CI. Matches this repo's own house convention (`tests/guard_preconditions/_reference_stats.py`'s `Z_95`), deliberately NOT `scipy.stats.norm.ppf(0.975)`'s more precise `1.959964`. |
| `STRESS_REDUNDANCY_THRESHOLD` | `0.65` | Stressed-sub-window correlation bar (AC-6). Numerically equal to `CORRELATION_SCREEN_THRESHOLD` (redundancy must hold under stress at the same bar it was flagged at under calm conditions) but kept as a SEPARATE named constant, deliberately, so the two can be tuned independently later without an implicit coupling. |
| `STRESS_MIN_OBS` | `10` | Minimum aligned observations inside the stressed sub-window for its correlation to be estimable at all (AC-6). Independent of, and much smaller than, `MIN_OBS_FLOOR` -- a genuine stress window is a small minority of the full history by construction. |
| `STRESS_WINDOW_FRACTION` | `0.05` | Fraction of aligned trading days -- ranked by MOST-NEGATIVE combined return (downside, not magnitude; see Revise round 2 below) ascending -- treated as "stressed" (AC-6). Mirrors the standard 95%-confidence VaR tail convention (the worst/most-extreme 5% of days). |
| `RETIREMENT_LOOKBACK_DAYS` | `250` | Default lookback for the continuous per-symphony bot series (AC-2 / Architecture). ~one full trading year -- the same walk-forward window length already used elsewhere in this codebase's optimizer (`autotuner.py`). |
| `W_CAGR` | `0.40` | Composite weight, CAGR (AC-3). Strictly dominant over the other four (operator ruling). |
| `W_SHARPE` | `0.20` | Composite weight, Sharpe. |
| `W_SORTINO` | `0.15` | Composite weight, Sortino. |
| `W_MAXDD` | `0.15` | Composite weight, Max Drawdown. |
| `W_CALMAR` | `0.10` | Composite weight, Calmar. |

The five weights sum to `1.0` so the composite stays in the same rough `[0, 1]` numeric range as any one normalized input metric. `_METRIC_KEYS` (`annualized_return`, `sharpe`, `sortino`, `max_drawdown`, `calmar`) and `_METRIC_WEIGHTS` are the `compute_quantstats_metrics` key names reused verbatim -- no renaming/translation layer between the metrics producer and this module's weighting table.

## `screen_correlated_pairs(series_by_symphony) -> list[PairResult]`

Thin wrapper over `correlation_diagnostic.compute_pairwise_correlations`, filtering to pairs where `correlation is not None and correlation >= CORRELATION_SCREEN_THRESHOLD`. A pair whose `PairResult.correlation is None` (zero-variance, or fewer than 2 aligned observations -- `correlation_diagnostic`'s own contract) is never a screen hit (AC-1). Never raises; fewer than 2 symphonies yields an empty list via the underlying function's own contract.

## `compute_composite_scores(metrics_by_symphony) -> dict[str, CompositeScore]`

Fleet-normalized, CAGR-dominant composite (AC-3). Each of the 5 metrics is min-max normalized **across the current fleet's ELIGIBLE symphonies only** -- an ineligible symphony's partial values must never distort the range its eligible peers are scored against. All 5 raw metrics already use a "higher = better" convention (`max_drawdown` is `<= 0`, so a shallower/less-negative value is already numerically higher), so no per-metric sign inversion is applied; normalizing the raw values directly preserves that ordering. A degenerate fleet range (a single eligible symphony, or every eligible symphony tied on one metric) normalizes that metric to a neutral `0.5` midpoint -- there is no relative-ranking information to extract.

**"Fleet" means the FULL live roster, not the flagged pair (Revise round, `quant-code-reviewer` Finding 1, fixed at `882aac2f`).** The function itself has always min-max-normalized over whatever `metrics_by_symphony` dict it is handed -- the defect was entirely in the orchestrator's INPUT to it. The original `build_recommendations` computed `metrics_by_symphony` only for `involved` (the symphonies appearing in at least one screened/flagged pair -- 2 members in the common single-pair case), not every live symphony with a usable AC-2 series. Min-max normalization over exactly 2 points is mathematically degenerate: every non-tied metric collapses to a winner-take-all `{0.0, 1.0}`, discarding all magnitude information -- and review proved this flips which symphony is selected as the retirement candidate purely as a function of whether an unrelated THIRD live symphony happens to exist in the roster (a 3-symphony fixture where the identical A-B pair picks the opposite candidate depending on whether uncorrelated symphony C is included in the normalization population; see `tests/advisors/test_retirement_recommender_composite.py::TestFleetNormalizationScopeAtOrchestratorLevel`). Fixed in the orchestrator: `build_recommendations` now builds `metrics_by_symphony` over every key in `series_by_symphony` (every live symphony with a usable AC-2 series), not the pair-only subset. The five composite weights (`W_CAGR=0.40` etc.) are unchanged -- this was a normalization-POPULATION fix, not a weighting fix.

`CompositeScore.eligible` is `False` iff ANY of the 5 metrics is `None` (AC-11) -- that symphony's `composite` is `None` and it can never be returned as a retirement candidate (see `select_retirement_candidate` below). **As of the Revise round 2 fix (below), it can no longer be the "keep" (sibling) member of a pair either** -- eligibility is symphony-level (computed once, shared across every pair that symphony appears in), so an ineligible symphony now drops out of the recommendation system entirely: never a candidate, never a sibling, in any pair. This corrects the original cycle's AC-11 wording ("may still be the keep member of a pair"), which described the pre-fix fail-open behavior.

## `select_retirement_candidate(sym_a, sym_b, scores) -> str | None`

The candidate is the **lower-composite** member of a pair (AC-4). Deterministic tiebreak: (a) lower composite; (b) tie -> lower `metrics['annualized_return']`; (c) tie -> lexically smaller `symphony_id`. Symmetric in `(sym_a, sym_b)` -- the result identifies an entity, not a call-order-dependent position, so the same pair always resolves to the same candidate regardless of argument order.

**An ineligible symphony is NEVER returned as the candidate.** A symphony missing from `scores` entirely degrades the same way (never a `KeyError`).

**Fail-closed on EITHER side ineligible (Revise round 2, PR-level `/code-review` Finding 1, fixed at `f9819274`).** The original cycle's rule was `if comp_a is None and comp_b is None: return None` — BOTH sides had to be ineligible before the pair yielded no candidate. If exactly ONE side was ineligible (`composite is None`), the original code skipped the composite-comparison branch entirely and unconditionally nominated the OTHER, eligible side as the candidate — meaning the ineligible symphony was ALWAYS implicitly treated as the "keep" side whenever paired with an eligible one, regardless of which one actually looked worse. PM ruling: `composite=None` can hide a catastrophic or unmeasurable loss (an undefined metric, e.g. `max_drawdown`), so automatically keeping the unscoreable member while retiring the well-characterized one is backwards for a capital decision — the direction of the original bug was the opposite of conservative. Fixed to `if comp_a is None or comp_b is None: return None` — the pair yields NO candidate if EITHER side is ineligible, full stop. Because eligibility is symphony-level (shared across every pair via the same `scores` dict), this means an ineligible symphony now yields `None` in EVERY pair it appears in — it can never be nominated as candidate, and it can never be left standing as an implicit "keep" either. See the `CompositeScore.eligible` note above for the corresponding correction to AC-11's original wording.

## `evaluate_uncertainty_gate(pair) -> GateVerdict`

Passes iff the Fisher-z 95% CI **lower bound** on the pair's correlation is also `>= CORRELATION_SCREEN_THRESHOLD` AND `n_obs >= MIN_OBS_FLOOR` (AC-5). `GateVerdict.ci_lower`/`ci_upper` are populated here and threaded verbatim into `raw_response` by the orchestrator.

```python
z = math.atanh(r)
se = 1.0 / math.sqrt(n - 3)
ci_lower = math.tanh(z - _Z_95 * se)
ci_upper = math.tanh(z + _Z_95 * se)
```

Fails closed (never raises) when `correlation is None`, `n_obs < MIN_OBS_FLOOR`, or the Fisher-z formula is otherwise undefined (`n <= 3` or `|r| >= 1.0`). `MIN_OBS_FLOOR` (30) already exceeds the `n <= 3` boundary, making that branch unreachable once the floor check passes -- it is still guarded explicitly rather than relying on that implication.

## `evaluate_structural_redundancy_gate(pair, stressed_corr, holdings_overlap) -> GateVerdict`

Passes iff `stressed_corr is not None AND stressed_corr >= STRESS_REDUNDANCY_THRESHOLD` (AC-6). `stressed_corr=None` is the single fail-closed signal for BOTH "the stress sub-window has fewer than `STRESS_MIN_OBS` aligned days" and "the stress-window Pearson r is itself undefined (zero variance)" -- mirroring `PairResult.correlation`'s own None convention (one signal, two causes, both fail-closed identically).

`holdings_overlap` is **corroborating evidence only** -- recorded into `raw_response` by the orchestrator but never consumed by this gate's pass/fail logic (the parameter is accepted, then explicitly discarded with `del holdings_overlap` and a comment explaining why, for a symmetric call signature and possible future use). A calm-only pair -- high full-window correlation, low stressed-window correlation -- correctly yields NO recommendation: the sibling provides crash-diversification exactly the way the Phase-1 audit's option-C finding said a bare point estimate would miss.

### `_compute_stressed_correlation(vals_a, vals_b) -> float | None`

The stressed sub-window: the top `ceil(STRESS_WINDOW_FRACTION * n)` aligned days, ranked by **most-negative combined return** (`(return_a[i] + return_b[i]) / 2`, ascending) -- the deepest joint-drawdown subset of the aligned history, targeting genuine crash/stress days specifically. Returns `None` (fail-closed) when the selected subset is thinner than `STRESS_MIN_OBS`, or when the Pearson r over it is itself undefined. Reuses `correlation_diagnostic._pearson_r` directly (module-qualified access to the private helper, a deliberate architectural choice per the RED handoff) so the stress-window statistic shares the EXACT SAME formula and None-convention as the full-window screen -- never a second, independently-drifting correlation implementation.

**Downside selection, not magnitude selection (Revise round 2, PR-level `/code-review` Finding 3, fixed at `f9819274`).** The original cycle ranked by `max(|return_a|, |return_b|)` descending -- the highest-MAGNITUDE days, regardless of sign. PM ruling: magnitude selection admits big RALLY (up) days alongside genuine crash (down) days into the "stressed" sample. A pair that co-moves nicely on rallies but DIVERGES on drawdowns -- exactly the crash-diversification case this gate exists to protect, per the Phase-1 audit's "option C" finding -- could have its magnitude-selected "stressed" window filled entirely with well-correlated rally days, never sampling the divergent crash days at all, and silently PASS the gate for a pair that should have been withheld. Fixed to select the most-negative `(return_a[i] + return_b[i]) / 2` days (ascending), which targets the crash days specifically rather than any large-magnitude day of either sign. This also required a fixture fix, `99e33097` (test-only, no production diff): 7 pre-existing tests shared a `sin()`-based synthetic-return fixture whose near-zero-derivative region at its trough made downside selection collapse onto fixed-magnitude noise rather than true signal there (0.9999 full-series correlation → 0.39 downside-selected, on data that is genuinely near-perfectly linearly related); replaced with a linear-ramp fixture (constant, non-zero derivative everywhere) that stays robust (0.958-1.0 downside-selected correlation) across every `n`/`k` combination the suite uses. The same commit also caught and fixed two PROPERTY tests (`TestPropertyUncorrelatedSymphonyNeverCreatesRecommendation`, `TestPropertyDeterminismUnderReordering`) that had been passing VACUOUSLY under the vulnerable fixture (both sides trivially empty, never exercising a real recommendation) -- both now genuinely exercise a non-empty recommendation set.

### `_compute_holdings_overlap(holdings_a, holdings_b) -> float | None`

Jaccard overlap (`|intersection| / |union|`) of two symphonies' held ticker sets. `None` when either side's `logic_holdings` is empty (off-hours / flat market) -- must never crash, and must never be silently treated as zero overlap, which would fabricate a signal that doesn't exist.

## `build_recommendations(*, db_file=None, days=RETIREMENT_LOOKBACK_DAYS) -> list[dict]`

Orchestrator. Discovers the live roster via `_live_symphony_roster(bot_state)` -- a structural discriminator (`isinstance(entry, dict) and "name" in entry`) matching the house convention used at 7+ other call sites for distinguishing a real symphony entry from `bot_state`'s top-level portfolio metadata keys (`date`, `last_execution_mode`, etc.). Fewer than 2 live symphonies (or fewer than 2 with a usable AC-2 series) returns `[]` (AC-11).

After screening, `metrics_by_symphony` is built over **every key in `series_by_symphony`** -- the full live roster with a usable AC-2 series -- and fed to `compute_composite_scores` (Revise round fix, see above; NOT `involved`/the pair-only subset the original cycle used).

Pipeline per surviving pair: screen -> pull the AC-2 aligned values via `correlation_diagnostic._extract_aligned_pairs` -> compute the stressed correlation + holdings overlap -> uncertainty gate -> structural-redundancy gate -> `select_retirement_candidate` (against the full-fleet-normalized scores) -> assemble the `raw_response` evidence dict. A pair failing either gate, or yielding no valid candidate, contributes nothing and the loop continues to the next pair (no early abort).

**Dedup on cluster hits.** The same symphony can be flagged against multiple siblings (a correlation cluster). `_evidence_strength(raw_response)` (currently just `raw_response["correlation"]`, the raw full-window value) ranks competing recommendations for the same candidate; only the single strongest-evidence recommendation per candidate survives into the returned list, keyed by `candidate_id` and returned sorted by id for determinism.

**Never raises, and now logs internal failures (Revise round 2, PR-level `/code-review` Finding 5, fixed at `f9819274`).** The entire body is wrapped in one `try/except Exception: return []` -- an unexpected failure anywhere in the pipeline degrades to an honest empty result rather than propagating (D-1 honest-degradation contract, matching every sibling advisors module). The original cycle's `except` block was silent -- an honest "no recommendations tonight" empty result and a genuine internal crash both looked identical from the outside (a WARNING-worthy distinction the nightly 03:45 tick had no way to make). Fixed: the `except` clause now logs `logger.warning("build_recommendations: internal failure: %s", type(exc).__name__)` before returning `[]` -- `type(exc).__name__` only, never `str(exc)` (D-1: an exception message could carry a file path, DB row content, or other internal detail that shouldn't reach logs at WARNING+). Behavior (the empty-list return) is unchanged; this is observability-only.

### `raw_response` shape (the authoritative schema — AC-8)

Every dict `build_recommendations()` returns, and every row `persist_recommendations()` writes, carries exactly these keys:

| Key | Meaning |
|-----|---------|
| `candidate_id` | The weaker-performer symphony_id (AC-4) — the retirement candidate. |
| `sibling_id` | The other, stronger-composite member of the pair (the "keep"). |
| `correlation` | Full-window Pearson r for the pair. |
| `ci_lower` / `ci_upper` | Fisher-z 95% CI bounds on `correlation` (AC-5). |
| `n_obs` | Overlapping finite observations the correlation was computed from. |
| `candidate_composite` / `sibling_composite` | Each member's fleet-normalized composite score. |
| `candidate_metrics` / `sibling_metrics` | Each member's raw 5-metric dict (`annualized_return`, `sharpe`, `sortino`, `max_drawdown`, `calmar`). |
| `uncertainty_gate_passed` / `structural_redundancy_gate_passed` | Both always `True` by construction — a pair whose gate failed never reaches `raw_response` assembly at all; the keys exist so a persisted/rendered row is self-describing without requiring the reader to infer the gate outcome from presence alone. |
| `stressed_correlation` | The AC-6 stress-sub-window correlation that passed the gate. |
| `holdings_overlap` | Jaccard overlap, or `None` off-hours/flat-market (corroborating only). |
| `basis_label` | `"actual-traded (bot) daily returns"` — see "The basis rule" above. |

## `persist_recommendations(recs, *, db_file=None) -> int`

One `database.insert_advisor_observation(advisor_role="RETIREMENT_RECOMMENDATION", subject_type="symphony", subject_id=<candidate_id>, symphony_id=<candidate_id>, verdict="retire_candidate", raw_response=<the dict above>)` call per recommendation; returns the count persisted (AC-8). `db_file` is accepted purely for interface symmetry with `build_recommendations` — `database.insert_advisor_observation` has no `db_file` override of its own (it always writes through `database.DB_FILE`), so the parameter is discarded (`del db_file`) rather than threaded further; documented inline so the asymmetry isn't mistaken for a bug.

**Advisory-only is structurally guaranteed, not merely intended.** `insert_advisor_observation` stores `is_advisory_only=1` unconditionally regardless of any caller-supplied value (`database.py:1175-1177`) — this module could not accidentally write a non-advisory row even if it tried.

## `RETIREMENT_RECOMMENDATION` — deliberately NOT in `_ADVISOR_ROLES`

`app.py`'s `_ADVISOR_ROLES` list (the roles the AI-Advisor-tab Overview observations loop iterates) is `[OVERFITTING_CONSCIENCE, SPEC_CRITIC, NARRATOR, MARKET_PRISM, ADD_CANDIDATE, ASSET_SWAP, LOGIC_CHANGE]`. `RETIREMENT_RECOMMENDATION` is not, and must not be, added to it — the same convention already established for `MARKET_PRISM_SOURCES` and `MARKET_LENS_CACHE` (advisor-role rows that exist purely to back a dedicated route/panel, kept out of the general Overview observations loop so they don't render twice or out of context there). No schema migration is needed either way: `advisor_role` is a free-text column, not an enum. See `GET /api/retirement-recommendations` and the Overview-tab panel in [app](app.md).

## Known limitations (accepted, not fixed this cycle)

Two PR-level `/code-review` findings on the Revise round 2 diff were ruled ACCEPT/DOCUMENT-only by the PM — real, correctly-identified behavior, but neither a correctness defect nor in-scope to fix now.

**F2 — the structural-redundancy gate is unsatisfiable below ~181 aligned days.** `_compute_stressed_correlation` (`:366`) requires `k = ceil(STRESS_WINDOW_FRACTION * n) >= STRESS_MIN_OBS` (`:388`) — with `STRESS_WINDOW_FRACTION=0.05` and `STRESS_MIN_OBS=10`, this needs `n >= 181` (`ceil(0.05*180)=9 < 10` fails; `ceil(0.05*181)=10` passes — independently re-derived and confirmed by this doc-writer, not merely relayed). Below that floor, `evaluate_structural_redundancy_gate` always returns `passed=False, reason="stressed-window correlation undefined or too thin"` regardless of how genuinely redundant the pair is — a thin-history symphony can never be recommended for retirement, no matter how strong its correlation. **This is the SAME conservative retention-interaction pattern already documented for `guard_preconditions.py`** (whose `N_MIN_OBS=40` similarly renders verdicts `INSUFFICIENT_DATA` in production until the droplet's retention window is extended) — fail-closed-empty is the CORRECT, intended behavior for thin data, not a bug: a retirement (capital) decision must never fire on a statistically unreliable stress-window estimate. **Tracked follow-up (out of scope this cycle):** a future enhancement could distinguish an honest `"insufficient data"` verdict from a genuine `"not redundant under stress"` verdict in the gate's `reason` field or a dedicated evidence key, so the operator can tell "we don't know yet" apart from "this pair genuinely decorrelates under stress" — today both read identically as "no recommendation."

**F6 — `correlation_diagnostic._extract_aligned_pairs` runs twice per screened pair.** `screen_correlated_pairs` → `correlation_diagnostic.compute_pairwise_correlations` internally calls `_extract_aligned_pairs` once per pair to compute the full-window Pearson r (AC-1). `build_recommendations`'s per-pair loop (`:508`) then calls `_extract_aligned_pairs` AGAIN, explicitly, to obtain the raw aligned value lists `_compute_stressed_correlation` needs for the AC-6 stress-window statistic. This is a genuine duplicate computation, not a correctness issue — both calls produce identical output for the identical pair (same alignment algorithm, same inputs), and the whole pipeline runs off-hours (03:45, not the 1-minute execution path), so the negligible extra CPU cost is not a live-cadence concern. **Tracked follow-up (out of scope this cycle):** `screen_correlated_pairs`/`compute_pairwise_correlations` could be extended to optionally return the aligned value lists alongside each `PairResult`, letting the orchestrator reuse them for the stress-window calculation instead of re-deriving them.

## Internal Dependencies

- `analytics` — `get_symphony_bot_and_held_daily_returns` (the AC-2 basis series), `compute_quantstats_metrics` (the 5 composite metrics). See [analytics](analytics.md).
- `database` — `load_state` (live roster + `logic_holdings`), `insert_advisor_observation` (AC-8 persistence, forces `is_advisory_only=1`). See [database](database.md).
- `advisors.correlation_diagnostic` — `compute_pairwise_correlations` (AC-1 screen), `THIN_DATA_THRESHOLD` (→ `MIN_OBS_FLOOR`), `_extract_aligned_pairs` and `_pearson_r` (module-qualified reuse for the AC-6 stress-window statistic — the exact same alignment/Pearson-r implementation as the full-window screen, never forked).
- No import of `alpha_bot_execution`, `math_engine`, or any order/liquidation/deploy primitive anywhere in this module — structurally enforced by `tests/security/test_retirement_recommender_no_trade_boundary.py` (AC-7).

**Callers (Revise round — producer added, `cac04985`):** `app.py`'s `_run_retirement_recommender_tick()` -> `_retirement_recommender_tick_worker()` is the sole production caller of BOTH `build_recommendations()` and `persist_recommendations()` — a daily off-hours scheduler tick registered at **03:45** in `run_scheduler()` (`schedule.every().day.at("03:45").do(_run_retirement_recommender_tick)`), staggered 15 minutes after the Strategy Incubation Gate's existing 03:30 slot so the three off-hours jobs never contend for the same minute. `advisors.retirement_recommender` is lazily imported inside the worker function (CC-2 — never a module-level import from `app.py`), and the tick spawns its own daemon thread so `run_scheduler()`'s loop returns immediately (the tick itself never blocks the 1-minute execution path, Architecture Constraint 1). D-1 error contract: a producer failure is caught and logged as `type(exc).__name__` only — it can never crash the scheduler thread.

`app.py`'s `_fetch_retirement_recommendations()` (the route/panel's read path) is unaffected by this addition — it still only ever reads PERSISTED rows, never calls the producer functions itself; the tick is what populates the ledger those reads now find. **Revise round 2 (PR-level `/code-review` Finding 4, fixed at `0d9af079`):** because the 03:45 tick persists every night and `advisor_observations` is append-only, `_fetch_retirement_recommendations()` now filters to only the LATEST calendar-night's batch (not every historical row) before returning — see [app](app.md) for the full fix description.

**Original-cycle gap, closed by the Revise round.** The original ship of this module had a route and panel fully wired to read a ledger nothing wrote — no scheduler tick existed, so both would have rendered an honest empty state indefinitely in production. This was found and flagged (not hidden) during doc review, and the PM ruled it in-scope to close within this cycle rather than deferring to 2b — see `DE-RETIRE-CORE-001`'s "Revise round" section in `DECISIONS.md` for the full record of both this and the composite-normalization fix above.

See `DE-RETIRE-CORE-001` in `DECISIONS.md` for the full Gate-1/Gate-2 design record.
