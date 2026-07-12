# advisors/logic_change_engine

> M4 Logic-Change proposal engine: operator-initiated (plain-text or explicit `LogicTweak`) and advisor-suggested (objective-directed) parameter-tweak proposals for a symphony's decision tree, gated through the shared M2 BHY/FDR batch gate; advise-only, never auto-applies.

**Source:** `advisors/logic_change_engine.py`
**Last updated:** 2026-07-12

## Overview

`advisors/logic_change_engine.py` implements the two Logic-Change modes from `feature-plans/ai-advisor.md` §M4 (AC-3.*):

1. **Operator-initiated** (`propose_operator_logic_change`) — the operator supplies either a `LogicTweak` (explicit `node_path` + `param_key` + `old_value` + `new_value`) or a plain-text `change_description` (e.g. `"Reduce window from 20d to 16d"`), which the engine parses into a `LogicTweak` via `_parse_change_description_to_tweak`. The tweak is applied to a deep copy of the score tree, backtested, gated as a single-element batch, and persisted.
2. **Advisor-suggested** (`suggest_logic_changes`) — given a `LogicChangeObjective`, `generate_objective_directed_candidates` produces a bounded, objective-directed set of `LogicTweak` candidates; all are backtested and submitted as ONE `evaluate_candidate_batch` call so the BHY/Yekutieli FDR correction applies across the FULL set (AC-3.2 — never gate candidates individually).

**Off-execution-path (AC-X2):** this module is not imported from `alpha_bot_execution.py`. It is an advise-only, offline, post-backtest decision layer.

**No write endpoints (AC-X1):** only `GET /score` and stateless `POST /api/v0.1/backtest` are called (via `advisors.composer_backtest_client.run_backtest`). No Composer write, mutate, or trade-placement call of any kind.

**Never auto-applies (AC-3.4):** every survivor's `apply_guidance` is a plain-text `ADVISE_ONLY_APPLY_TEMPLATE` instruction ("open {symphony} in Composer and manually adjust…") — never a button, never a write call.

**Verdict-agnostic persistence (RC-4):** every gated proposal — survivor, `KEEP_INCUMBENT`, or `REJECT_VETO_FAILED` — is persisted as an `advisor_observation` with `is_advisory_only=1`, so the operator sees the engine ran even on the common non-ADOPT path. A persistence failure is surfaced via `LogicChangeRunResult.persistence_error`, never swallowed to a warning (RC-5).

## Public Types

### `LogicTweak` (dataclass)

One concrete numeric parameter change to apply to a symphony logic tree.

| Field | Type | Description |
|-------|------|-------------|
| `node_path` | `list[str \| int]` | Navigation keys from root to the target node (e.g. `["children", 0, "children", 2]`); `[]` means the root node |
| `param_key` | `str` | The key within the target node whose value is being changed |
| `old_value` | `Any` | Current value at `param_key` — validated against the live tree before applying |
| `new_value` | `Any` | Proposed replacement value (numeric) |
| `node_description` | `str` | Human-readable node/param description for operator-facing apply guidance (default `""`) |

### `LogicChangeObjective` (dataclass)

Typed objective driving a logic-change search (Gate-1 Resolution #2 — every change must be objective-directed, never a vibe tweak).

| Field | Type | Description |
|-------|------|-------------|
| `objective_type` | `str` | One of `"reduce_drawdown"`, `"lift_risk_adjusted"`, `"reduce_turnover"`, `"improve_momentum_timing"`, `"reduce_whipsaw"`, or any other named objective |
| `measured_value` | `float` | The live backtest measurement driving this objective (e.g. current max-drawdown, current Sharpe) — never a hardcoded heuristic |
| `rationale` | `str` | Human-readable explanation surfaced alongside every survivor (AC-3.3); default `""` |

### `LogicChangeProposalResult` (dataclass) — alias `LogicProposalResult`

Result for one evaluated logic-change candidate.

| Field | Type | Description |
|-------|------|-------------|
| `candidate_id` | `str` | `"{symphony_id}:{param_key}@{path}:{old}->{new}"` traceability ID |
| `symphony_id` | `str` | Composer symphony UUID |
| `tweak` | `LogicTweak \| None` | `None` when the tweak is structurally invalid or unparseable |
| `objective` | `LogicChangeObjective` | The objective driving this proposal (AC-3.3) |
| `objective_rationale` | `str` | Human-readable explanation of how this change addresses the objective |
| `gate_result` | `CandidateGateResult \| None` | From `backtest_gate_engine.evaluate_candidate_batch`; `None` if backtest failed pre-gate |
| `baseline_stats` / `variant_stats` | `dict \| None` | Stats from the unchanged / changed tree backtests |
| `caveats` | `list[str]` | Propagated from `gate_result.caveats`; `SURVIVOR_OVERFITTING_CAVEAT` mandatory on every `ADOPT_CANDIDATE` |
| `apply_guidance` | `str` | Plain-text operator instruction (AC-X1 / AC-3.4) |
| `backtest_error` | `str \| None` | Descriptive failure string (AC-X5) — never aborts the batch |
| `data_warnings` | `list[str]` | Ticker-level data-availability warnings from the Composer API |

### `LogicChangeRunResult` (dataclass)

Top-level result of a `propose_operator_logic_change` or `suggest_logic_changes` run.

| Field | Type | Description |
|-------|------|-------------|
| `gate_batch` | `GatedBatch` | Always non-`None`, even for zero candidates — carries `n_candidates`/`fdr_q` audit trail |
| `proposals` | `list[LogicChangeProposalResult]` | All evaluated proposals (survivors + rejected + failed) |
| `survivors` | `list` | Subset with `gate_result.verdict.decision == "ADOPT_CANDIDATE"` |
| `rejected_candidates` | `list` | Subset gated-out or backtest-failed |
| `message` | `str` | Human-readable run summary; `NO_SURVIVORS_MESSAGE` on zero survivors |
| `objective` | `LogicChangeObjective \| None` | The objective that drove this run |
| `no_api_key` | `bool` | `True` when the Composer API key is absent — proposals empty (AC-X4) |
| `persistence_error` | `str \| None` | Non-`None` when the `advisor_observation` write failed (RC-5) |

## Constants

### Public

| Constant | Value | Description |
|----------|-------|-------------|
| `LOGIC_CHANGE_SURVIVOR_CAVEAT` | re-export of `backtest_gate_engine.SURVIVOR_OVERFITTING_CAVEAT` | Mandatory caveat on every survivor |
| `NO_SURVIVORS_MESSAGE` | `"no logic change cleared the gate this run"` | Zero-survivors is a valid, non-error outcome |
| `MAX_SUGGESTED_CANDIDATES` | `30` | Upper bound on advisor-suggested candidates per run — keeps the FDR correction effective (`feature-plans/ai-advisor.md` §Gate-1 Resolutions #4) |
| `ADVISE_ONLY_APPLY_TEMPLATE` | `"To apply: open {symphony_name} in Composer and manually adjust {node_description} from {old_value} to {new_value}."` | Operator-facing apply-manually template (AC-X1 / AC-3.4) |

### Objective-directed scaling factors (`generate_objective_directed_candidates`)

Every value is a named module-level constant with a source comment (project coding standard — no magic numbers).

| Constant | Value | Direction | Applies to objective |
|----------|-------|-----------|----------------------|
| `_REDUCE_DRAWDOWN_TIGHTEN_FACTOR` | `0.80` | tighten (−20%) | `reduce_drawdown` — shorter lookbacks respond faster to drawdown signals |
| `_LIFT_RISK_ADJUSTED_LOOSEN_FACTOR` | `1.25` | loosen (+25%) | `lift_risk_adjusted` — wider thresholds capture more trend signal |
| `_REDUCE_TURNOVER_LENGTHEN_FACTOR` | `1.50` | lengthen (+50%) | `reduce_turnover` — longer lookbacks reduce rebalancing frequency |
| `_IMPROVE_MOMENTUM_TIMING_SHORTEN_FACTOR` | `0.90` | shorten (−10%) | `improve_momentum_timing` — conservative tightening of the timing window |
| `_REDUCE_WHIPSAW_LENGTHEN_FACTOR` | `1.30` | lengthen (+30%) | `reduce_whipsaw` — damps high-frequency signal churn |
| `_CANDIDATE_WINDOW_FLOOR_DAYS` | `5` | — | Minimum parameter value eligible for a day-scale tweak (below this is likely a fractional threshold or flag, not a lookback window) |
| `_REDUCE_TURNOVER_FLOOR_DAYS` | `3` | — | Lower floor for `reduce_turnover` only — the objective lengthens windows, so a 3-day starting point is still meaningful |

These five factors were never affected by DE-LOGIC-CHANGE-DIRECTION-001 below — each already carries the objective-correct sign.

### Fallback direction factors (`_parse_change_description_to_tweak`, added by DE-LOGIC-CHANGE-DIRECTION-001)

| Constant | Value | Description |
|----------|-------|-------------|
| `_FALLBACK_INCREASE_FACTOR` | `1.20` | +20% — applied when the description contains an increase keyword, or no direction keyword at all (preserves prior behavior for direction-less descriptions) |
| `_FALLBACK_DECREASE_FACTOR` | `0.80` | −20% — applied when the description contains a reduce keyword |
| `_REDUCE_DIRECTION_KEYWORDS` | `("reduce", "lower", "decrease", "shrink")` | Scanned against the full description text |
| `_INCREASE_DIRECTION_KEYWORDS` | `("increase", "raise", "grow")` | Scanned against the full description text |

## API Reference

### `propose_operator_logic_change(symphony_id, score_tree, tweak=None, objective=None, *, change_description=None, incumbent_oos_alpha=None, default_oos_alpha=0.0) → LogicChangeRunResult`

Evaluate one operator-specified logic change (AC-3.1 operator-initiated mode). Exactly one of `tweak` or `change_description` must be supplied — when `change_description` is given, it is parsed into a `LogicTweak` via `_parse_change_description_to_tweak`. `objective` is required (raises `ValueError` if `None`).

Gates as a single-element batch (`evaluate_candidate_batch([bt_candidate], ...)`) — the BHY/FDR machinery runs even for N=1, satisfying AC-3.2 structurally. No Composer API key → `no_api_key=True`, writes nothing (AC-X4). Never raises on backtest or gate failure (AC-X5).

**Incumbent OOS alpha (H5/H6/RC-1):** when `incumbent_oos_alpha` is not supplied, it is derived from a fold-matched baseline backtest (`_fold_transform_single` on the unchanged tree) rather than a full-history sum — this avoids biasing the gate toward `KEEP_INCUMBENT`. An explicit `incumbent_oos_alpha=0.0` is honored; only `None` triggers the fallback.

### `suggest_logic_changes(symphony_id, score_tree, objective, *, incumbent_oos_alpha=None, default_oos_alpha=0.0, baseline_stats=None) → LogicChangeRunResult`

Evaluate advisor-suggested objective-directed candidates (AC-3.1 + AC-3.2). Generates candidates via `generate_objective_directed_candidates` (objective-directed, not brute force), backtests each independently (AC-X5 — one candidate's failure never aborts the batch), then submits ALL successfully-backtested candidates as ONE `evaluate_candidate_batch` call.

**AC-3.2 critical invariant:** never gate candidates individually — that silently disables the multiple-testing correction (raising N must raise the adjusted-p-value bar every candidate must clear). No Composer API key → `no_api_key=True` (AC-X4). Zero candidates or zero survivors are valid non-error outcomes.

### `generate_objective_directed_candidates(symphony_id, raw_value, objective, *, baseline_stats=None) → list[LogicTweak]`

The adversarially-testable objective-direction gate (Gate-1 Resolution #2 / AC-3.2). Dispatches on `objective.objective_type` to one of the five named scaling factors above; an **unknown objective_type returns an empty list** (refuses to produce unguided candidates — an objective-ignoring generator is the overfitting trap). Bounded by `MAX_SUGGESTED_CANDIDATES`.

### `generate_objective_directed_logic_candidates(symphony_id, score_tree, objective, *, baseline_stats=None) → list[dict]`

Higher-level wrapper around `generate_objective_directed_candidates` that annotates each `LogicTweak` with a human-readable `"change_description"` string (per-objective phrasing: tighten/loosen/lengthen/shorten/extend) for the operator/UI layer. Returns `[{"change_description": str, "tweak": LogicTweak}, ...]`.

### `apply_logic_tweak(raw_value, tweak) → dict | None`

Deep-copies `raw_value` and applies `tweak`. Returns `None` (invalid variant, never mutates the input) when the `node_path` cannot be navigated, the target node lacks `param_key`, or the current value does not match `tweak.old_value`.

### `extract_numeric_params(raw_value) → list[dict]`

Recursively collects every numeric parameter node in the tree as `{"node_path", "param_key", "value"}`. Booleans are excluded (flags, not tunable parameters — checked before the numeric branch since `bool` is an `int` subclass). A current value of `0` or `1` does NOT make a param a flag — only genuine booleans are excluded.

## Internal Helpers

### `_fallback_direction_factor(desc_lower) → float` (added by DE-LOGIC-CHANGE-DIRECTION-001)

Returns the scaling factor implied by direction words in a plain-text `change_description`. Scans the full description (not just the keyword mapped to a `param_key`) for `_REDUCE_DIRECTION_KEYWORDS` → `_FALLBACK_DECREASE_FACTOR` (0.80), else `_INCREASE_DIRECTION_KEYWORDS` → `_FALLBACK_INCREASE_FACTOR` (1.20), else defaults to `_FALLBACK_INCREASE_FACTOR` (matches the prior behavior for descriptions that state no direction at all). See the dedicated fix section below.

### `_parse_change_description_to_tweak(raw_value, change_description) → LogicTweak | None`

Parses a plain-text change description into a `LogicTweak` via four phases, tried in order:

1. **Phase 1** — regex-extract explicit `"from X to Y"` / `"X -> Y"` numeric values, matched against a `param_key` preferred by a keyword-to-key map (`lookback`/`window`/`threshold`/`period`/`momentum`/`regime`).
2. **Phase 2** — same explicit-value match, without requiring a preferred-key hit.
3. **Phase 3** — no explicit numbers; match by preferred `param_key` alone, apply `_fallback_direction_factor(desc_lower)`.
4. **Phase 4** — no preferred-key match either; fall back to the tree's first numeric parameter, apply `_fallback_direction_factor(desc_lower)`.

Phases 1–2 were never affected by the direction bug — they read the operator's exact stated `old`/`new` values, so direction is inherent to the input. Only Phases 3–4 (no explicit numbers — direction must be inferred from keywords) shared the buggy flat-`1.20` math before the fix.

### `_navigate_to_node(raw_value, node_path) → Any`

Walks `node_path` from the tree root; returns `None` on any invalid step (`KeyError`/`IndexError`/`TypeError`).

### `_build_objective_rationale(tweak, objective) → str`

Per-objective human-readable sentence explaining why the tweak addresses the stated objective (mirrors the phrasing in `generate_objective_directed_logic_candidates`).

### `_make_candidate_id(symphony_id, tweak) → str`

Builds `"{symphony_id}:{param_key}@{path}:{old}->{new}"` (`"root"` when `node_path` is empty).

### `_evaluate_single_variant(raw_value, symphony_id, tweak, objective, symphony_name="") → tuple`

Backtests one variant: applies the tweak to a deep copy, backtests both the baseline and the variant tree, converts log-returns → percent, and returns `(BacktestCandidate | None, LogicChangeProposalResult, baseline_stats | None)`. `candidate` is `None` when the tweak is structurally invalid or the variant backtest failed (AC-X5 — isolated to this candidate, never aborts the batch).

### `_backtest_returns_from_tree(raw_value, symphony_id) → list[float]`

Runs a backtest and returns the log-returns list; `[]` on failure.

### `_persist_observation(symphony_id, proposal, gate_result) → None`

Writes one `advisor_observation` row (`advisor_role="LOGIC_CHANGE"`, `observation_type="logic_change_proposal"`, `is_advisory_only=1`) carrying the ACTUAL gate verdict — regardless of ADOPT/KEEP/REJECT (RC-4). `raw_response` carries the tweak, objective, gate decision, validation days, OOS alpha, and caveats for audit.

### `_has_composer_key() → bool`

Local import of `alpha_bot_execution.COMPOSER_KEY_ID` / `COMPOSER_SECRET` (deferred import — not a module-level dependency on the execution engine, satisfying AC-X2). Returns `False` on any exception.

### `_empty_gate_batch() → GatedBatch`

Sentinel empty `GatedBatch` (zero candidates, zero survivors) for the no-API-key / empty-candidate paths.

## Bug Fix — Direction-Aware Fallback (DE-LOGIC-CHANGE-DIRECTION-001)

**The bug:** `_parse_change_description_to_tweak`'s Phase 3 (preferred-key fallback) and Phase 4 (first-numeric-parameter fallback) both applied a flat `old_val * 1.20` (unconditional +20% increase) whenever the operator's `change_description` had no explicit `"from X to Y"` numbers — regardless of what the description said. Live-verified regression: `"reduce the window size"` on `old_value=10` produced `new_value=12`, an **increase** despite the word "reduce". This reached the operator-initiated live path through `propose_operator_logic_change(change_description=...)` whenever the operator described a change in words without giving explicit numbers.

**The fix:** `_fallback_direction_factor(desc_lower)` scans the full description for reduce/lower/decrease/shrink → `_FALLBACK_DECREASE_FACTOR` (0.80) or increase/raise/grow → `_FALLBACK_INCREASE_FACTOR` (1.20), applied identically in both Phase 3 and Phase 4 (they share the same math). `old_value=10` with `"reduce the window size"` now yields `new_value=8` (`round(10 * 0.80)`), not `12`.

**Blast radius:** confined to the plain-text-description fallback paths. `generate_objective_directed_candidates` (the advisor-suggested candidate generator) was never affected — its five named scaling factors already carried objective-correct signs. Phases 1–2 of the parser (explicit `"from X to Y"` numbers) were never affected — direction is inherent to the operator's stated values.

**Why prior tests didn't catch it:** the existing test suite exercised Phases 1–2 (explicit numeric descriptions) and the objective-directed generator, but had no test forcing Phase 3/4 with a direction-only (no-numbers) description — the exact shape a real operator is likely to type. Added `TestPhase3FallbackDirectionRespected` (`tests/ai_advisor/test_logic_change_engine.py`) covers Phase 3, Phase 4, and the live `propose_operator_logic_change` path end-to-end.

## Design Invariants

| Code | Invariant |
|------|-----------|
| AC-3.1 | Both modes (operator-initiated tweak; advisor-suggested candidates) diagnose → propose → backtest → gate → surface survivors |
| AC-3.2 | N backtested candidates → ONE FDR/multiple-testing correction across the FULL set; per-candidate gating is a test FAIL |
| AC-3.3 | Every surfaced logic-change carries `SURVIVOR_OVERFITTING_CAVEAT` + post-correction gate verdict |
| AC-3.4 | Never auto-applies — `apply_guidance` is plain text; writes only an advisory observation |
| AC-X1 | No Composer write endpoint call — only `GET /score` + stateless `POST /api/v0.1/backtest` |
| AC-X2 | `alpha_bot_execution.py` does not import this module; this module does not module-level-import `alpha_bot_execution` (local import inside `_has_composer_key` only) |
| AC-X3 | Every surfaced recommendation persists as an `advisor_observation` with `is_advisory_only=1` + `symphony_id` |
| AC-X4 | No Composer API key → `no_api_key=True`, writes nothing, never an unhandled error |
| AC-X5 | Per-candidate backtest failure produces a failure marker; never aborts the batch |
| RC-4 | Persistence is verdict-agnostic — a `KEEP_INCUMBENT`/`REJECT_VETO_FAILED` proposal is still persisted |
| RC-5 | A persistence failure is surfaced via `LogicChangeRunResult.persistence_error`, never swallowed |

## Internal Dependencies

- `database` — `insert_advisor_observation`
- `advisors.backtest_gate_engine` — `HARVEY_LIU_FDR_Q`, `SURVIVOR_OVERFITTING_CAVEAT`, `BacktestCandidate`, `CandidateGateResult`, `GatedBatch`, `_fold_transform_single`, `evaluate_candidate_batch`
- `advisors.composer_backtest_client` — `run_backtest`
- `alpha_bot_execution` — `COMPOSER_KEY_ID`, `COMPOSER_SECRET` (local import inside `_has_composer_key`, AC-X2 boundary)

No import of `app`; off-execution-path; advisory-only.
