# Research — Suggestible Config Surface + Optuna-as-Claude-Context

> **⚠️ SUPERSEDED BY IMPLEMENTATION (noted 2026-06-10).** This is the original 2026-05-14 design research; its body is preserved as a historical record. The shipped suggestible surface is **7 items** — the **6** Optuna search-space keys + `MAX_SQUEEZE_FLOOR` — **not 9**. `TRIGGER_THRESHOLD_PCT` is a **locked** variable (`database.DEFAULT_LOCKED_VARS`), not suggestible. For current truth see `docs/generated/ai_advisor.md` (`_SUGGESTIBLE_ALLOWLIST`, `ai_advisor.py:698`).

**Status:** Research only. No feature code. Owner surface: `autotuner.py` + optimization DB + `.env` + `symphony_strategies`.
**Date:** 2026-05-14
**Feature under scope:** Operator-triggered, on-demand "Claude suggests config edits" — Claude receives engine quant data + Optuna's current tuned config + symphony info, returns accept/reject config diffs with rationale.

---

## 0. TL;DR

- **Total suggestible config values: 9** — the 7 Optuna-tuned per-symphony params, plus `MAX_SQUEEZE_FLOOR` (in `symphony_strategies` / `DEFAULT_STRATEGY` but **not** in the Optuna search space — hand-set only), and the `locked_vars` list (meta-config: which params Optuna may touch).
- **Hard exclusions (never AI-suggestible): 8 values** — every credential/identifier/safety flag in `.env`: `LIVE_EXECUTION`, `EXECUTION_START_TIME`, `COMPOSER_KEY_ID`, `COMPOSER_SECRET`, `ACCOUNT_*` UUIDs, `ALPACA_KEY`, `ALPACA_SECRET`, `DISCORD_WEBHOOK_URL`. Plus the infra knobs `SIMULATION_PATHS` / `NEIGHBOR_K` (methodology, not strategy — see §2).
- **Optuna-overlap take:** see §5. Short version — Claude's value-add is *not* re-optimizing the 7 params (Optuna already did, with 500-trial walk-forward + OOS validation + a 3-tier baseline cascade). Claude's value-add is **reasoning about the things Optuna structurally cannot see**: `MAX_SQUEEZE_FLOOR` (untuned), the `locked_vars` decision, regime/symphony-composition context, and *flagging when Optuna's own output looks degenerate*. Letting Claude overwrite a params value that just passed OOS validation is second-guessing a statistically-validated result and should be heavily friction-gated.

---

## 1. The Suggestible Config Surface — full enumeration

Three layers, as the brief frames them. Each entry: what it is, valid range, current source.

### Layer A — Optuna search-space keys (per-symphony, `symphony_strategies.parameters`)

These 7 are defined twice in `autotuner.py`: as the `OPTUNA_SEARCH_SPACE_KEYS` frozenset (the validation contract) and as `trial.suggest_*` calls inside the `objective()` closure (the actual search space). The `suggest_*` ranges below are the **authoritative valid ranges** — they are what Optuna is allowed to explore.

| Key | What it is | Valid range (from `suggest_*`) | Type | Current source |
|-----|------------|-------------------------------|------|----------------|
| `TRIGGER_THRESHOLD_PCT` | MC-probability ceiling that arms the risk guard; also `*2` = disarm level. Higher = guard arms in a wider band. | `5.0 – 25.0` | float | Optuna-tuned. **But** also listed in `DEFAULT_LOCKED_VARS` — by default Optuna is told *not* to tune it (see §1.D). Also has a `.env` global default (`15.0`). |
| `TAKE_PROFIT_MC_PCT` | MC-probability floor below which TP-arming triggers (exceptional-gain exit). | `2.0 – 10.0` | float | Optuna-tuned. `.env` global default `5.0`. |
| `VWAP_CROSS_HWM_PCT` | HWM-relative band for the VWAP-breakdown state machine. | `0.5 – 2.5` | float | Optuna-tuned. `.env` global default `1.0`. |
| `VWAP_BLEED_MULTIPLIER` | Multiplier on 20-day vol → VWAP-bleed arming threshold. | `0.5 – 3.0` | float | Optuna-tuned. No `.env` global; hard-coded fallback `1.5` in `alpha_bot_execution.py` + `DEFAULT_STRATEGY`. |
| `VWAP_BLEED_TICKS` | Consecutive-tick count before a VWAP-bleed cut fires. | `3 – 30` | **int** | Optuna-tuned. Fallback `10`. |
| `PARABOLIC_VELOCITY_THRESHOLD` | Return-velocity threshold that arms the parabolic squeeze. | `1.0 – 4.0` | float | Optuna-tuned. `.env` global default `2.0`. |
| `MAX_PARABOLIC_SQUEEZE` | Cap on the parabolic-squeeze multiplier applied to the trailing stop. | `0.1 – 0.8` | float | Optuna-tuned. `.env` global default `0.50`. |

Notes:
- All 7 land in `symphony_strategies.parameters` (JSON blob) via `database.save_symphony_strategy()`. The post-autotune value is whatever the baseline cascade selected (Adopted AI / Reverted to Fallback / Reset to Global Default).
- The frozenset and the closure must stay in sync — `autotuner.py:11-23` documents this explicitly. Any suggestible-surface code that enumerates "what Claude can suggest" should read `OPTUNA_SEARCH_SPACE_KEYS`, **not** re-list the keys.

### Layer B — `.env` globals consumed by `alpha_bot_execution.py`

Read at module load (`alpha_bot_execution.py:30-61`). Two sub-classes: **strategy globals** (defensible to surface) and **infra/identity** (hard exclusions — see §2).

| `.env` key | What it is | Valid range | Suggestible? |
|------------|------------|-------------|--------------|
| `TRIGGER_THRESHOLD_PCT` | Global default for the per-symphony key above; used when a symphony's params lack the key. | match Layer A: `5.0 – 25.0` | **Yes, with care** — see §3 (global vs per-symphony targeting). |
| `TAKE_PROFIT_MC_PCT` | Global default for Layer A key. | `2.0 – 10.0` | Yes, with care. |
| `MAX_SQUEEZE_FLOOR` | **The odd one out.** In `DEFAULT_STRATEGY` and read from `.env` (default `0.20`), consumed per-symphony via `acc_MAX_SQUEEZE_FLOOR`, but **NOT in `OPTUNA_SEARCH_SPACE_KEYS` and NOT in any `suggest_*` call.** Optuna never tunes this. | No `suggest_*` range exists. `DEFAULT_STRATEGY` = `0.20`. Practical range should be researched before exposing — treat `0.05 – 0.50` as a placeholder pending `risk-engine-specialist` confirmation. | **Yes — and this is the highest-value target.** It is the one strategy param Optuna structurally ignores, so Claude is not second-guessing a validated result here. |
| `VWAP_CROSS_HWM_PCT` | Global default for Layer A key. | `0.5 – 2.5` | Yes, with care. |
| `PARABOLIC_VELOCITY_THRESHOLD` | Global default for Layer A key. | `1.0 – 4.0` | Yes, with care. |
| `MAX_PARABOLIC_SQUEEZE` | Global default for Layer A key. | `0.1 – 0.8` | Yes, with care. |
| `SIMULATION_PATHS` | Monte Carlo path count (`5000`). | int, infra | **No** — methodology knob, not strategy. See §2. |
| `NEIGHBOR_K` | kNN neighbor count for MC sampling (`150`). | int, infra | **No** — methodology knob. See §2. |
| `LIVE_EXECUTION` | Master safety flag. Real money on/off. | `True`/`False` | **NEVER.** See §2. |
| `EXECUTION_START_TIME` | When the engine begins acting (`09:30`). | `HH:MM` | **NEVER** (operational, not strategy). See §2. |
| `COMPOSER_KEY_ID`, `COMPOSER_SECRET`, `ACCOUNT_INDIVIDUAL/ROTH/TRAD`, `ALPACA_KEY`, `ALPACA_SECRET`, `DISCORD_WEBHOOK_URL` | Credentials + account identifiers + webhook. | secrets | **NEVER.** See §2. |

Important wiring detail: `.env` is read **once at module import**. There is no live-reload. A "write to `.env`" accept action does **not** take effect until the next `alpha_bot_execution.py` subprocess spawn (next minute tick) — and even then only because `app.py` spawns a fresh process each minute. This is actually convenient (no hot-reload hazard) but the feature must communicate "applies next cycle," not "applies now."

### Layer C — the `symphony_strategies` DB table

Schema (`database.py:41-47`): `symphony_name TEXT PRIMARY KEY`, `parameters TEXT` (JSON), `locked_vars TEXT` (JSON).

| Column | Suggestible content | Notes |
|--------|---------------------|-------|
| `symphony_name` | **No.** Primary key / identity. Renaming it orphans the row (cf. migration 001). | Hard exclusion. |
| `parameters` | The 7 Layer-A keys + `MAX_SQUEEZE_FLOOR` = the 8 strategy values. | This is the per-symphony write target (§3). |
| `locked_vars` | **Yes — meta-config.** The list of param names Optuna is forbidden to tune for this symphony. `DEFAULT_LOCKED_VARS = ["TRIGGER_THRESHOLD_PCT"]`. | Suggesting a change here is suggesting *what Optuna is allowed to do next run* — a genuinely different and valuable lever than suggesting a value. See §5. |

**Caveat on `locked_vars`:** there is a live inconsistency in the code. `database.get_symphony_strategy()` returns `locked_vars` and `run_autotuner()` reads it into `locked_vars` and passes it to `save_symphony_strategy()` — but the `objective()` closure **unconditionally** calls `suggest_*` for all 7 keys regardless of `locked_vars`. So today `locked_vars` is *persisted but not enforced* in the search space. Flag to PM: if Claude suggests editing `locked_vars`, the operator may reasonably expect it to constrain the next Optuna run — and right now it wouldn't. Either fix the enforcement gap or scope `locked_vars` out of the suggestible surface until it's wired.

### Surface count summary

- **Strategy values Claude could defensibly suggest: 8** — 7 Optuna keys + `MAX_SQUEEZE_FLOOR`.
- **Plus 1 meta-config:** `locked_vars` (caveated above).
- **Total suggestible surface: 9.**

---

## 2. Hard Exclusions — never AI-suggestible

| Config value | Layer | Why locked out |
|--------------|-------|----------------|
| `LIVE_EXECUTION` | `.env` | Master real-money safety flag. Project rule: "`is_live=True` is explicit, never a default." An AI suggestion path that can flip this — even as a reject-able diff — creates a one-click path to live trading. Must be **structurally impossible** for it to appear in a diff, not just "operator should say no." |
| `EXECUTION_START_TIME` | `.env` | Operational scheduling, not strategy. No quant rationale Claude could offer; out of scope of "tuned config." |
| `COMPOSER_KEY_ID` / `COMPOSER_SECRET` | `.env` | Credentials. Never in a prompt, never in a diff. Also a secrets-leak vector — Claude must not even *receive* these as context (see §4). |
| `ALPACA_KEY` / `ALPACA_SECRET` | `.env` | Credentials. Same as above. |
| `ACCOUNT_INDIVIDUAL` / `ACCOUNT_ROTH` / `ACCOUNT_TRAD` | `.env` | Account UUIDs — identity, not config. Editing them re-points the engine at a different brokerage account. |
| `DISCORD_WEBHOOK_URL` | `.env` | Endpoint identity + a secret-ish URL. Not strategy. |
| `SIMULATION_PATHS` | `.env` | Monte Carlo methodology knob. Changing it changes the *measurement instrument*, not the strategy. Per global autotuner rules, methodology changes go to a human, not an AI diff. |
| `NEIGHBOR_K` | `.env` | kNN methodology knob — same reasoning as `SIMULATION_PATHS`. |
| `symphony_strategies.symphony_name` | DB | Primary key / identity. Renaming orphans the row and breaks `normalize_name()` lookups (cf. migration 001). |

**Implementation guidance for the exclusion list:** make it an explicit allowlist, not a denylist. The suggestible surface is small and well-defined (9 items); enumerate what's *in*, and anything not in the allowlist is automatically excluded. A denylist silently admits any new `.env` key a future commit adds.

**Secrets must never reach the prompt.** The context-assembly step (§4) must read from a curated allowlist of keys, never `dict(os.environ)` or a raw `.env` dump. Credentials should not be in Claude's input at all — not "in the input but marked do-not-touch."

---

## 3. Per-symphony vs Global Targeting

Config genuinely lives in two places, and 6 of the 7 Optuna keys exist in *both* (`.env` global default **and** `symphony_strategies.parameters` per-symphony override). The read path in `alpha_bot_execution.py:448-456` is: `acc_params.get(KEY, GLOBAL_DEFAULT)` — per-symphony value wins, `.env` global is the fallback.

**Recommendation: Claude should target the per-symphony layer by default.**

Rationale:
- The feature is invoked *per symphony* ("operator clicks → Claude receives symphony info"). The natural write target is that symphony's `symphony_strategies` row.
- Per-symphony writes are reversible, isolated (one row), and don't affect other symphonies — consistent with the project's "parameter-isolated per symphony" principle.
- A `.env` global edit is blast-radius-wide: it changes the fallback for *every* symphony that lacks an explicit override, and it requires editing a file that also holds secrets. Higher risk, lower precision.

**Can Claude suggest both?** It *could*, but it shouldn't in v1. Proposed policy:
- **Per-symphony (`symphony_strategies.parameters` row):** default and primary target for all 8 strategy values.
- **Global (`.env`):** out of scope for v1. If a later version wants it, gate it behind a separate explicit operator mode ("edit global defaults") and a separate writer with file-level care (preserve comments, preserve secret lines, only touch the one `KEY=value` line). Mixing secret-bearing-file edits into an AI accept/reject flow in v1 is asking for trouble.
- `MAX_SQUEEZE_FLOOR` lives in `symphony_strategies.parameters` per-symphony (via `DEFAULT_STRATEGY`) — so it fits the per-symphony target cleanly even though it also has an `.env` global.

**Accept/reject → write mapping:**

| Operator action | Target | Write mechanism |
|-----------------|--------|-----------------|
| Accept a per-symphony param diff | `symphony_strategies.parameters` JSON for that `symphony_name` | Read row → `json.loads` → patch the one key → `json.dumps` → `save_symphony_strategy()`. **Must merge, not replace** — never write a partial params dict (cf. the "no Frankenstein merge" rule in autotuner — the inverse risk here is dropping keys). |
| Accept a `locked_vars` diff | `symphony_strategies.locked_vars` JSON | Same row, `locked_vars` column. (Blocked in v1 until the enforcement gap in §1.C is fixed.) |
| Reject | nothing | No write. Optionally log the rejected suggestion + rationale for audit. |
| Accept a global `.env` diff | — | Out of scope v1. |

**Concurrency hazard:** the autotuner writes `symphony_strategies` rows via `save_symphony_strategy()` (INSERT OR REPLACE — full-row overwrite). If an operator accepts a Claude diff while the autotuner is mid-run, last-writer-wins and one of the two silently loses. The accept-write path needs either (a) a check that no autotune is in progress (the `execution_lock` table exists for exactly this kind of guard), or (b) a documented "autotune overwrites manual edits on next run" expectation surfaced to the operator. Flag to PM.

---

## 4. Packaging Optuna's Output as Claude Context

The user wants Optuna's results to be **input** to Claude. The goal is a prompt-ready blob that says "here's what Optuna concluded and how confident it was." Below is what it should contain and how to assemble it.

### What Optuna actually produces today (and what's missing)

`run_autotuner()` computes a rich set of signals but **persists almost none of them.** What survives the run:
- `symphony_strategies.parameters` — the final cascade-selected values.
- `optimization_results[name]` — an in-memory dict with `_baseline_chosen` and per-key `{old, new}`. **Returned, then dropped** unless the caller (reporting) captures it. Not in any DB.
- The Optuna study itself in `optuna_studies.db` — has `best_value`, `best_params`, all 500 trials.

What's computed but **never persisted anywhere**: `best_alpha_train`, `oos_alpha`, `fallback_oos_alpha`, `default_oos_alpha`, `avg_train_alpha`, `avg_oos_alpha`, `train_days_count`, `test_days_count`, the `baseline_decision` string, `ai_proposal_invalid`. These are exactly the "how confident was Optuna" signals the context blob needs. **This is the single biggest gap for the feature** — see recommendation below.

### Proposed context blob structure

A per-symphony JSON object, assembled at feature-invocation time:

```
{
  "symphony": {
    "name": "<normalized name>",
    "current_holdings": [...],        // from bot_state
    "current_return_pct": <float>,
    "symphony_vol": <float>           // 20-day vol — drives most thresholds
  },
  "optuna": {
    "study_name": "<study_name>",
    "n_trials": 500,
    "n_complete_trials": <int>,        // completed vs pruned/failed — confidence signal
    "best_value_train_alpha": <float>,
    "search_space": {                 // typed ranges — lets Claude reason about headroom
      "TRIGGER_THRESHOLD_PCT": {"type": "float", "low": 5.0, "high": 25.0},
      ... all 7 ...
    },
    "best_params": { ...7 keys... },   // what Optuna's BO landed on
    "validation": {
      "oos_alpha": <float>,            // AI proposal OOS guard alpha
      "fallback_oos_alpha": <float>,
      "default_oos_alpha": <float>,
      "train_days": <int>, "test_days": <int>,
      "avg_oos_alpha": <float>,
      "ai_proposal_invalid": <bool>,   // schema-validation outcome
      "baseline_decision": "Adopted AI" | "Reverted to Fallback" | "Reset to Global Default"
    }
  },
  "current_config": {                  // what's LIVE right now — may differ from best_params
    "parameters": { ...8 strategy values from symphony_strategies... },
    "locked_vars": [...],
    "source_of_each": { "TRIGGER_THRESHOLD_PCT": "fallback", ... }  // optional: which cascade tier each value came from
  },
  "suggestible_surface": [ ...the 9 allowlisted keys with valid ranges... ]
}
```

### The critical fields and why

- **`baseline_decision` / `ai_proposal_invalid`** — these tell Claude *whether Optuna even trusted its own result this run.* If `baseline_decision == "Adopted AI"`, the live params are statistically validated and Claude should be conservative. If `"Reverted to Fallback"` or `"Reset to Global Default"`, Optuna's optimization *failed OOS* — and that is precisely the situation where Claude's reasoning has the most room (Optuna gave up; the live config is a last-known-good or a generic default, not an optimized result).
- **`oos_alpha` vs `fallback_oos_alpha` vs `default_oos_alpha`** — the margin between these is the confidence signal. AI beating fallback by 0.05% is a coin-flip; beating it by 3% is real. Claude should be told to weight its own suggestions against this margin.
- **`search_space` ranges** — lets Claude reason about *headroom*: "best_params put `TRIGGER_THRESHOLD_PCT` at 24.8, near the 25.0 ceiling — the optimizer may be range-constrained" is a genuinely useful observation Claude can make and Optuna cannot.
- **`current_config` vs `best_params`** — these can differ (cascade reverted to fallback). Claude must reason about the *live* config, not Optuna's raw best.
- **`n_complete_trials`** — pruned/failed trials reduce effective sample size. If only 60 of 500 completed, "500-trial study" is misleading.

### Assembly recommendation

The blob spans **both DBs** (state DB: `symphony_strategies`, `bot_state`; optimization DB: `optuna_studies.db`) — and the project hard rule is **never cross-join across DBs in app code; copy rows.** So the assembly function must read each DB independently and merge in Python, never a SQL join.

**Strongly recommended prerequisite:** persist the per-run validation metrics. Today they evaporate. Two clean options:
1. Optuna `study.set_user_attr()` for `oos_alpha`, `fallback_oos_alpha`, `default_oos_alpha`, `baseline_decision`, etc. — keeps them in the optimization DB next to the study, retrievable without re-running anything.
2. A small new `autotune_runs` metadata table in the optimization DB (additive, NULLable per project schema rules) — one row per symphony per run.

Option 1 is lower-friction and aligns with the autotuner rule "write a one-line summary into the optimization DB metadata table after every run." Without one of these, the context blob can only report `best_params` + `best_value` and the operator gets a much weaker "how confident was Optuna" picture. **This persistence work should be a Gate-1 dependency of the feature, not an afterthought.**

### Study-naming finding (tangential but must flag)

`autotuner.py:324` creates studies with `study_name=normalized_name` and `load_if_exists=True`. This **directly violates** the project's own rule (project CLAUDE.md "Walk-forward study names: use `<timestamp>__<symphony>`; never reuse a study name") and the global autotuner rule #3. Every autotune run appends 500 more trials to the *same* study — `study.best_params` is therefore drawn from an ever-growing pool spanning many different market regimes and many different `history_train` windows. For the Claude-context feature this matters: "n_trials: 500" is wrong (it's 500 × number-of-runs), and `best_params` may be a trial from weeks ago on stale data. **Recommend a separate task to fix study naming before the context-packaging feature ships** — otherwise the "Optuna confidence" blob is built on a corrupted study. Not in scope to fix here, but the feature is unsound without it.

---

## 5. The Overlap Tension — honest domain take

**The setup:** Optuna already runs a 500-trial, `n_jobs=-1` Bayesian walk-forward (80/20 train/OOS split over 125 trading days) on these exact 7 params, *and* validates the result OOS against two baselines, *and* falls back through a 3-tier cascade if the AI proposal doesn't beat them. That is a genuinely rigorous pipeline. Claude suggesting edits on top of it needs a clear-eyed answer to "why isn't this just second-guessing a statistically-validated number?"

**Where the value-add is real:**

1. **The untuned param.** `MAX_SQUEEZE_FLOOR` is in `DEFAULT_STRATEGY` and consumed live, but **Optuna never touches it.** It's hand-set at `0.20` and has presumably never been systematically examined. Claude reasoning about whether `0.20` is right for a given symphony's volatility profile is not second-guessing Optuna — Optuna abdicated this one entirely. **Highest-confidence value-add.**

2. **The `locked_vars` decision.** `TRIGGER_THRESHOLD_PCT` is locked by default, so Optuna is *told not to optimize it.* Claude suggesting "unlock `TRIGGER_THRESHOLD_PCT` for this symphony so the next autotune can tune it" is a meta-level suggestion Optuna cannot make about itself. (Caveat: enforcement gap in §1.C must be fixed first.)

3. **Reading Optuna's failure modes.** When `baseline_decision` is "Reverted to Fallback" or "Reset to Global Default," Optuna's optimization *did not produce a usable result.* The live config is then a stale last-known-good or a generic default — **not** an optimized number. Claude has real room here: it's not overruling a validated result, it's filling a vacuum the optimizer left.

4. **Range-constraint and regime observations.** Claude can look at `best_params` against the `search_space` bounds and the symphony's holdings/vol and say "the optimizer pinned three params at their range ceilings — the search space may be mis-specified" or "this symphony's composition changed materially; the 125-day window straddles two regimes." These are observations *about* the optimization, not competing numbers.

**Where the risk is real — and where to put friction:**

When `baseline_decision == "Adopted AI"` and `oos_alpha` beat both baselines by a meaningful margin, the live params for those 7 keys are a *statistically validated result on out-of-sample data.* Claude suggesting "nudge `TAKE_PROFIT_MC_PCT` from 4.8 to 5.3" in that situation is exactly the failure mode to fear: Claude has no out-of-sample test behind its number, Optuna does. A plausible-sounding LLM rationale ("5.3 better matches the symphony's recent momentum") will *feel* more convincing than Optuna's silent number — and be worse. This is the "second-guessing a statistically-validated result" trap, and it's the dangerous default if the feature treats all 9 surface items identically.

**Recommendation — tier the surface by Optuna-overlap, don't treat it flat:**

- **Tier 1 (Claude leads):** `MAX_SQUEEZE_FLOOR`, `locked_vars` — Optuna structurally doesn't optimize these. Claude's suggestions here stand on their own.
- **Tier 2 (Claude fills vacuum):** the 7 tuned params *when* `baseline_decision != "Adopted AI"` — Optuna failed OOS; Claude reasoning into the gap is legitimate.
- **Tier 3 (Claude challenges, with heavy friction):** the 7 tuned params *when* `baseline_decision == "Adopted AI"` and the OOS margin was material — Claude may still flag a concern, but the UI should show the operator the OOS evidence Claude is arguing against, and the diff should carry a "this overrides an OOS-validated value" warning. Default posture: Claude should mostly *defer* here and say so.

**One-paragraph bottom line:** The feature is sound if and only if it is built to *complement* Optuna's blind spots rather than re-litigate its validated outputs. The genuine value-add is the untuned param, the lock decision, the failure-mode vacuum, and meta-observations about the optimization itself — and packaging Optuna's confidence signals (§4) into the context is what makes that complementary framing possible: Claude can only defer-to vs fill-in-for Optuna if it's *told* how confident Optuna was. Built flat — Claude nudging all 9 values with equal authority regardless of whether Optuna just validated them OOS — it becomes a confident-sounding LLM overriding statistics, which on a real-money engine is a net negative. The §4 persistence work (surface `baseline_decision` + OOS margins) is therefore not optional polish; it's the load-bearing piece that keeps the feature on the right side of this line.

---

## Appendix — Key file references

- `autotuner.py:19-23` — `OPTUNA_SEARCH_SPACE_KEYS` frozenset (validation contract).
- `autotuner.py:302-308` — `objective()` closure `suggest_*` calls (authoritative search-space ranges).
- `autotuner.py:324-325` — study creation (`study_name` rule violation + `n_trials=500`, `n_jobs=-1`).
- `autotuner.py:329-407` — `best_params`, OOS validation, 3-tier baseline cascade, `baseline_decision`.
- `autotuner.py:410-417` — `optimization_results` assembly (returned, not persisted) + `save_symphony_strategy()`.
- `database.py:11-25` — `DEFAULT_STRATEGY` (8 keys incl. `MAX_SQUEEZE_FLOOR`) + `DEFAULT_LOCKED_VARS`.
- `database.py:41-47` — `symphony_strategies` schema.
- `database.py:172-195` — `get_symphony_strategy` / `save_symphony_strategy` (INSERT OR REPLACE full-row).
- `alpha_bot_execution.py:30-61` — all `.env` consumption (credentials, safety flags, strategy globals, infra knobs).
- `alpha_bot_execution.py:448-456` — `acc_params.get(KEY, GLOBAL_DEFAULT)` per-symphony-over-global resolution.
- `.env` — live config file: secrets + `LIVE_EXECUTION` + `EXECUTION_START_TIME` (no strategy globals currently set in the file; engine uses code defaults).
- `migrations/001_normalize_symphony_names.sql` — precedent for why `symphony_name` is identity, not config.
