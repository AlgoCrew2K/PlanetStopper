# ai_advisor

> Claude-backed config advisor: context assembly, per-symphony assessment, structured-output Claude call via ADVISOR_SYNTHESIS_MODEL, safety gates (7-item allowlist, risk-direction check, OOS re-validation), market-wide lens cache-serve (nightly MARKET_LENS_CACHE bundle; no per-click live lens fetches for the 5 market-wide lens blocks), and the R2-1 `build_reasoning_context` operator-context assembler that feeds Strategy Builder generation (real tree + live stats + lens evidence, with an honest per-source manifest).

**Source:** `ai_advisor.py`
**Last updated:** 2026-07-13 (R2-1 -- new `build_reasoning_context` reasoning-context assembler + `_EMPTY_MANIFEST`/`_MAX_TREE_RENDER_CHARS` constants, `DE-ADVISOR-R2-1-001`; AC-9 wording reconciled per r2-review's gate finding -- see below; prior: advisor-suite-fixes AC-4: fundamentals selection loop no longer pre-filters to 10-K-only -- see below; prior: DE-TECH-SMA200-HISTORY-001 technicals lens line-range correction; prior: DE-ADVISOR-LATENCY MARKET_LENS_CACHE cache-serve path; persist_market_lens_cache producer; build_assessment_from_context empty-state reword; prior: DE-FUND-002 vintage-correct fundamentals)

## Overview

`ai_advisor.py` is the operator-assist config advisory surface. It is split into two cycles:

- **C1** — Context assembly + synchronous Claude call. `assemble_advisor_context` reads a curated 7-item allowlist of config values (6 Optuna search-space keys + `MAX_SQUEEZE_FLOOR`), never `os.environ`. `request_suggestions` calls Claude with structured output (`ConfigSuggestionsResponse`). `build_assessment_from_context` synthesises a per-symphony assessment from the assembled context so the UI can explain why no suggestion was made — the common case for symphonies with no validated edge. Never raises — every failure degrades to `(None, error_message)`.

- **C2** — Safety gates. Four independent defense-in-depth gates on the accept path (implemented in `app.py`): (1) `enforce_suggestion_allowlist` — structural rejection of non-allowlisted keys (blocks); (2) `check_risk_direction_agreement` — risk-polarity cross-check (logs only, non-blocking — disagreement is recorded but does not veto the suggestion); (3) `revalidate_suggestion_oos` — OOS re-gate via autotuner simulation (blocks); (4) `enforce_locked_var_gate` — rejects suggestions that touch a variable locked by the current theory bundle (NN1 spec-freeze enforcement) (blocks). The source comment at `ai_advisor.py:1738` enumerates all four gates. (C2-COMMENT-1 resolved 2026-06-17.)

Real-money-critical input governance: `assemble_advisor_context` never includes credentials, account IDs, safety flags, or methodology knobs. The config surface is an allowlist, not a denylist.

**Liveness fix (2026-06-10):** `assemble_advisor_context` now accepts `composer_symphony_id` and `autotune_run` parameters. The route passes the Composer hash ID via `composer_symphony_id` so that `symphony_logic.get_condensed_logic` receives the hash the Composer `/score` API expects — passing the normalized name previously produced HTTP 400 and an empty logic struct. The `autotune_run` parameter is now fully honored — when a pre-fetched row is passed (non-`_SENTINEL`), the internal `database.get_latest_autotune_run` call is skipped, avoiding a redundant DB round-trip.

**Regime context fix (2026-06-10):** `_build_volatility_regime` sets `available: False` with an explicit `reason` when `vol/atr` keys are absent from the autotune run row, rather than fabricating `available: True` with all-null fields.

**Cycle-1 multi-lens scaffold (2026-06-10):** Five lens helpers added (`_build_technicals_section`, `_build_sentiment_section`, `_build_derivatives_section`, `_build_macro_section`, `_build_fundamentals_section`), each following the honest-availability pattern. A citation convention (`build_citation` / `validate_citation`) enforces well-formed sources. See [Multi-Lens Scaffold](#multi-lens-scaffold) below.

**Technicals lens wiring (2026-06-15):** `_build_technicals_section` (`ai_advisor.py:439-482`) replaced its Cycle-1 stub with a real producer. It lazy-imports `advisors.lens_technicals` (CC-2), derives the universe from `database.load_state()` holdings (tickers across all monitored symphonies), and calls `_fetch_technicals(universe)`. Returns `available=True` with MA posture, breadth, and momentum payload when bars are available; `available=False` with a named reason otherwise. See [advisors/lens_technicals](advisors_lens_technicals.md).

**Sentiment lens wiring (GDELT, 2026-06-15):** `_build_sentiment_section` lazy-imports `advisors.lens_gdelt` and calls `_fetch_gdelt_sentiment`. Honest-availability: `tone is None → available=False`. See [advisors/lens_gdelt](advisors_lens_gdelt.md).

**Derivatives lens wiring (FRED VIX/VXV, 2026-06-16):** `_build_derivatives_section` lazy-imports `advisors.lens_options_proxy` and calls `_fetch_options_proxy()`. Honest-availability now covers **staleness** as well as fetch failure: the freshness guard (`_OPTIONS_PROXY_MAX_STALENESS_DAYS = 10`) rejects observations older than 10 calendar days as `available=False, reason="stale_data"`. Prior stub behavior is superseded — the producer returns a real VIX level, term-structure, and risk read when `FRED_API_KEY` is set and data is fresh. See [advisors/lens_options_proxy](advisors-lens-options-proxy.md).

**Fundamentals lens portfolio fan-out (2026-06-16 — DE-FUND-001):** `_build_fundamentals_section()` (called with no ticker by both the 03:00 nightly pipeline and `assemble_advisor_context`) previously short-circuited to `available=False, reason="ticker symbol required..."` — a dead lens. Now fans out over a company-ticker universe (live `logic_holdings` ∪ `_FUNDAMENTALS_PROXY_UNIVERSE` floor of 8 large-cap company tickers — NOT ETFs, which have no SEC EDGAR `companyfacts` entries). The single-ticker path (`ticker="AAPL"`) is preserved byte-for-byte via the extracted `_fetch_fundamentals_for_ticker(ticker)` helper. Per-ticker honest degradation; no invented composite ratios; bounded fan-out. See [DE-FUND-001 in DECISIONS.md](../../DECISIONS.md).

**Fundamentals lens vintage fix (2026-06-17 — DE-FUND-002):** Two concurrent vintage defects resolved. Mode A (XBRL concept deprecation): `_SEC_KEY_CONCEPTS` now maps each logical concept to `(label, ordered_candidate_tags)` — the Revenues concept unions three candidate tags (`RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`, `Revenues`) so migrated issuers are not frozen at a deprecated tag. Mode B (wrong sort key): the entry selection loop now sorts by `(end desc, filed desc)` across the unioned candidate-tag entries, selecting the entry with the most recent reporting-period end date. `key_facts` output keys are stable (logical keys unchanged — `Revenues`, `NetIncomeLoss`, etc.). See [DE-FUND-002 in DECISIONS.md](../../DECISIONS.md).

**Market-lens cache-serve (2026-06-29 — DE-ADVISOR-LATENCY):** The per-click 17-29 sequential external API call fan-out (6-minute hang) has been eliminated. `assemble_advisor_context` now serves the 5 market-wide lens blocks from a nightly `MARKET_LENS_CACHE` advisor_observations row instead of invoking the live `_build_*_section()` builders per request. The nightly producer (`persist_market_lens_cache`) is wired into `prism_scheduler._patch_provenance`, which already runs the 5 builders — the cache costs one additional DB write and zero extra network calls. Cold-start (no cache row yet): each lens block degrades honestly to `available=False, reason="lens_cache_unavailable"` — the live builders are never the silent default fallback. A stale bundle (older than `_LENS_CACHE_MAX_AGE_HOURS=36`) is served with an honest "stale" label rather than triggering a live re-fetch. Staleness metadata (`lens_data_as_of`, `lens_data_stale`) is surfaced in the suggest response JSON and the advisor SPA. See [DE-ADVISOR-LATENCY in DECISIONS.md](../../DECISIONS.md).

**Reasoning-context assembler (2026-07-13 — R2-1, `DE-ADVISOR-R2-1-001`):** `build_reasoning_context(symphony_id, objective, *, composer_symphony_id=None) -> tuple[str, dict]` assembles a bounded, human-readable operator-context block for Strategy Builder generation — the operator's real symphony tree (rendered via `symphony_schema.render_rules_text`, capped at `_MAX_TREE_RENDER_CHARS=6000`), live Optuna stats, and the 5 market-lens blocks (reusing `assemble_advisor_context`'s existing nightly cache-serve path — never a fresh live fan-out) — paired with an honest per-source manifest (`tree`/`stats`: present|absent; the 5 lenses: available|stale|absent) a caller can surface as provenance. Falsy `symphony_id` (the from-scratch, non-symphony-scoped path) returns immediately with zero I/O — no Composer fetch, no DB read — matching the byte-preservation floor one layer up in `build_plan_generator._build_generation_prompt`. D-1: never raises, even when a collaborator (e.g. `symphony_logic.fetch_symphony_score`) itself raises. See [advisors/build_plan_generator](advisors_build_plan_generator.md) for how the returned text is threaded into the SB generation prompt, and `DE-ADVISOR-R2-1-001` in `DECISIONS.md` for the full provenance contract — this assembler is the shared, cross-cutting enabler R2-2 (Logic Changes) and R2-3 (Asset Swaps) reuse, not a Strategy-Builder-only feature.

## API Reference

### Context Assembly

#### `assemble_advisor_context(scope: str, symphony_id: str | None = None, composer_symphony_id: str | None = None, autotune_run = _SENTINEL) → dict`

Assembles the prompt-ready context blob carrying all 9 must-have prompt elements plus role framing:

1. Per-param definition + risk polarity
2. Valid range of every suggestible param
3. Optuna OOS-vs-train delta + `baseline_decision`
4. Current live value of each param
5. `locked_vars`
6. Volatility regime context (honest availability — `available: False` when vol/atr columns absent from schema)
7. Data window + its limits
8. Risk invariants as hard constraints
9. Operator-assist role + task framing

The returned dict includes market-wide lens context from the nightly `MARKET_LENS_CACHE` bundle (DE-ADVISOR-LATENCY) rather than live fetches. New top-level keys:

- `lenses` — `dict[str, dict]` with keys `technicals`, `sentiment`, `derivatives`, `macro`, `fundamentals`; each value is the structured `_build_*_section()` payload from the most recent nightly cache.
- `lens_data_as_of` — `str | None` — ISO UTC timestamp of when the cache bundle was captured; `None` on cold-start.
- `lens_data_stale` — `bool` — `True` when the bundle age exceeds `_LENS_CACHE_MAX_AGE_HOURS`; conservatively defaults to `True` on cold-start.

Backward-compat aliases at the top level are preserved: `context["technicals"]`, `context["sentiment"]`, `context["derivatives"]`, `context["macro"]`, `context["fundamentals"]` — each reads from `_lenses_from_cache.get(name) or {}`. Existing consumers of these keys require no change.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `scope` | `str` | `"symphony"` or `"global"` |
| `symphony_id` | `str \| None` | Required when `scope == "symphony"`; used as key for all state-DB lookups (autotune_runs, symphony_strategies) |
| `composer_symphony_id` | `str \| None` | Optional Composer hash ID; passed to `get_condensed_logic` when present (hash required by Composer /score API) |
| `autotune_run` | `dict \| None \| _SENTINEL` | Optional pre-fetched autotune run. When non-`_SENTINEL`, the value is used directly and `database.get_latest_autotune_run` is skipped. Pass `_SENTINEL` (the default) to fetch from DB internally. Explicit `None` means "Optuna has not run" — also skips the fetch. |

**Returns:** Well-shaped context dict including `lenses`, `lens_data_as_of`, `lens_data_stale`, and backward-compat lens aliases. Never raises; degrades gracefully when Optuna has not run or when the market-lens cache is absent.

**Raises:** `ValueError` when `scope == "symphony"` and `symphony_id` is `None`.

**Cache-serve path (DE-ADVISOR-LATENCY):** Before any live lens call, invokes `database.get_latest_market_lens_cache()`. On a valid cache hit, extracts the 5 structured lens payloads, computes `age_hours = (now_utc - captured_at).total_seconds() / 3600`, and sets `lens_data_stale = age_hours > _LENS_CACHE_MAX_AGE_HOURS`. Skips ALL 5 live `_build_*_section()` calls. Cold-start or unparseable `captured_at` → honest `available=False, reason="lens_cache_unavailable"` for each lens — still no live builder calls.

---

#### `build_assessment_from_context(context: dict) → dict`

Builds a per-symphony assessment dict from the assembled context. Resolves the UI empty-state problem: `ConfigSuggestionsResponse` carries only a `suggestions` list — the route previously discarded the assembled context entirely, leaving the result box showing an identical generic message for every symphony regardless of tuning state.

The assessment is derived from `context["optuna_evidence"]` which carries `baseline_decision`, `oos_alpha`, `fallback_oos_alpha`, `default_oas_alpha`, and `available`. The `summary` string is differentiated per tuning state:

- No Optuna run: "Walk-forward optimization (Optuna) has not run for this symphony yet. No out-of-sample (OOS) validation evidence is available — the current config is unvalidated. Claude will reason without OOS data." (rewording of the prior "Optuna has not yet run…" text — DE-ADVISOR-LATENCY AC-8; semantics unchanged.)
- `oos_alpha is None` (all trials haircut-rejected by FDR gate): explains all trials failed the significance gate; quotes fallback and default alphas. This is the expected state for most symphonies — the FDR + PBO gates are intentionally strict.
- `oos_alpha` present: summarises the validated edge with numeric values.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `context` | `dict` | Output of `assemble_advisor_context` or any dict with `optuna_evidence` key |

**Returns:** `{"baseline_decision": str | None, "oas_alpha": float | None, "fallback_oas_alpha": float | None, "default_oas_alpha": float | None, "summary": str}`.

---

#### `persist_market_lens_cache(sections: dict) -> None`

Persists all 5 structured lens payloads as a `MARKET_LENS_CACHE` advisor_observations row. Called nightly from `prism_scheduler._patch_provenance` after the council builders have already run — adds one DB write at zero extra network cost.

`sections` is a `dict[str, dict]` keyed by lens name (`technicals`, `sentiment`, `derivatives`, `macro`, `fundamentals`); each value is the exact dict returned by the corresponding `_build_*_section()` call. An unavailable lens is stored with its honest `available=False` block rather than being omitted — the advisor serve path handles partial availability correctly.

`raw_response` shape stored:
```json
{
    "captured_at": "<ISO UTC timestamp>",
    "lenses": {
        "technicals": { "lens": "technicals", "available": ..., ... },
        "sentiment":  { ... },
        "derivatives":{ ... },
        "macro":      { ... },
        "fundamentals":{ ... }
    }
}
```

`captured_at` is `datetime.now(UTC).isoformat()` at call time. Append-only: the latest row wins on serve. `advisor_role` is `"MARKET_LENS_CACHE"`, `subject_type` is `"portfolio"`, `subject_id` is `"global"`, `symphony_id` is `""`.

**D-1 never-raises:** any exception is caught and logged as `logger.warning("persist_market_lens_cache failed: %s", type(exc).__name__)`. Never propagates.

**Source:** `ai_advisor.py:1470–1495`

---

### Reasoning-Context Assembly (R2-1)

#### `build_reasoning_context(symphony_id: str, objective, *, composer_symphony_id: str | None = None) → tuple[str, dict]`

Assembles operator-context text + a per-source honesty manifest for Strategy Builder generation (`DE-ADVISOR-R2-1-001`). Real-money-critical honesty contract: Strategy Builder should reason over the operator's ACTUAL symphony, not just an objective name — this function gathers {the real tree (rendered), live Optuna stats, the 5 market-lens blocks} into a bounded prose block ready to splice verbatim into a generation prompt, alongside an honest manifest a caller can surface as provenance.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `symphony_id` | `str` | The symphony to reason about. Falsy (`""`/`None`) means a from-scratch run — returns immediately with **zero I/O** (no Composer fetch, no DB read), matching the AC-8 byte-preservation floor in `build_plan_generator._build_generation_prompt`. |
| `objective` | (unused today) | Reserved for a future objective-conditioned rendering; every source is gathered unconditionally regardless of objective. |
| `composer_symphony_id` | `str \| None` | The Composer hash ID, when known — preferred for the tree-fetch/lens-cache-serve calls per the project's Composer hash rule; falls back to `symphony_id` when absent. |

**Returns:** `(prompt_context, manifest)`:
- `prompt_context: str` — `""` when nothing is injectable; otherwise a bounded, human-readable text block (never a raw JSON tree dump — a raw dump would both blow the token budget and leak internal Composer node ids/uuids).
- `manifest: dict` — an honest per-source record, never fabricated:
  - `"tree"`: `"present"` \| `"absent"`
  - `"stats"`: `"present"` \| `"absent"` (mirrors `context["optuna_evidence"]["available"]` from `assemble_advisor_context`)
  - `"technicals"` / `"sentiment"` / `"derivatives"` / `"macro"` / `"fundamentals"`: `"available"` \| `"stale"` \| `"absent"` — derived by combining each lens's own `assemble_advisor_context` `available` flag with the bundle-wide `lens_data_stale` classifier (the SAME classifier `assemble_advisor_context` already computes — never a second, hand-rolled one).

**The honest-degradation manifest is the honesty artifact this function exists to produce — not a footnote to the prose.** Every degraded source is reflected in the manifest exactly as it happened: a tree-fetch failure or a falsy `symphony_id` → `"tree": "absent"`; no Optuna run → `"stats": "absent"`; a cold lens cache → every lens `"absent"`; a bundle older than `_LENS_CACHE_MAX_AGE_HOURS` → every available lens `"stale"` instead of `"available"`. In every one of those cases the run PROCEEDS without that evidence — never a placeholder, never a fabricated "available". This is the concrete mechanism behind R2's thesis that reasoning must be OBSERVABLE, not merely present: an operator (or a future R2-2/R2-3 port) can always tell, from the manifest alone and independent of the prose, exactly what evidence a given generation run actually saw versus what it reasoned without.

**D-1: never raises**, under any failure mode — including when a collaborator itself raises (e.g. `symphony_logic.fetch_symphony_score` throwing `RuntimeError`). Each source (tree; stats+lenses) is gathered inside its own `try/except`, so one collaborator's failure can never take down the other or propagate to the caller.

**Behavior:**
- Real tree (AC-1): `symphony_logic.fetch_symphony_score(composer_symphony_id or symphony_id)` → `symphony_schema.render_rules_text(raw_tree)`, truncated to `_MAX_TREE_RENDER_CHARS` characters (with a `"... [truncated]"` marker appended) when the rendered text exceeds the bound — never a raw JSON dump.
- Live stats + the 5 lens blocks (AC-2): reuses `assemble_advisor_context`'s EXISTING nightly cache-serve path (never a fresh live fan-out on this per-click path) — `optuna_evidence` for stats; each available lens block's `payload` is re-encoded to JSON and passed through `advisors.prism_render.humanize_lens_summary` for prose (the same humanizer the Overview tab uses — no second hand-rolled renderer).
- Bound (AC-9): `_MAX_TREE_RENDER_CHARS = 6000` bounds INPUT-context growth/cost for the injected tree text specifically. Distinct from `build_plan_generator.MAX_OUTPUT_TOKENS` (a different, OUTPUT-side ceiling on the SDK's structured-tool-use response). Conservative, uncalibrated value (~1,500 tokens at a rough 4-chars/token estimate) — no measured worst-case exists yet, unlike `MAX_OUTPUT_TOKENS`'s calibrated figure.

**AC-9 wording reconciliation (r2-review gate finding, 2026-07-13):** the feature plan's AC-9 text reads "bounded so a large real tree can't blow `build_plan_generator.MAX_OUTPUT_TOKENS`" — that phrasing is loose and does not literally match the implementation. `_MAX_TREE_RENDER_CHARS` bounds THIS function's own INPUT-context contribution to the prompt; it has no runtime relationship to `MAX_OUTPUT_TOKENS`, which is a wholly separate module's OUTPUT-side ceiling on the SDK's structured-tool-use response (`advisors/build_plan_generator.py`). AC-9's actual intent — "the injected tree can't blow the generation call's cost/context budget" — is satisfied by capping the input side; the two constants have never been coupled in code, and the plan text should not be read as implying otherwise. Follow-up (non-blocking, logged 2026-07-13): tighten this wording in the R2-1 feature plan itself so a future reader isn't misled by the same loose phrasing.

**Source:** `ai_advisor.py:1700-1803`

**Constants:**
| Constant | Type | Value | Purpose |
|----------|------|-------|---------|
| `_EMPTY_MANIFEST` | `dict` | 7-key, all `"absent"` | Returned (a fresh `dict()` copy) whenever nothing is injectable — every key defaults to `"absent"`, never omitted, never fabricated as present. `ai_advisor.py:67-78`. |
| `_MAX_TREE_RENDER_CHARS` | `int` | `6000` | INPUT-context bound: caps the rendered real-tree text injected into the SB generation prompt (AC-9). NOT related to `build_plan_generator.MAX_OUTPUT_TOKENS` (see the wording-reconciliation note above). `ai_advisor.py:80-88`. |

**Called by:** `app.py`'s `ai_advisor_strategy_builder_run()` route — symphony-scoped runs only, never the from-scratch path (see [app](app.md)). Threaded into `strategy_builder_engine.propose_strategies(reasoning_context=, reasoning_manifest=)` → `build_plan_generator.generate_build_plans(reasoning_context=)` → `_build_generation_prompt(reasoning_context=)` (see [advisors/strategy_builder_engine](advisors_strategy_builder_engine.md) and [advisors/build_plan_generator](advisors_build_plan_generator.md)).

**Cross-cutting contract:** this assembler and its manifest shape are the shared enabler `DE-ADVISOR-R2-1-001` establishes for the whole R2 program — R2-2 (Logic Changes) and R2-3 (Asset Swaps) call the SAME `build_reasoning_context` and extend the SAME provenance surface to their own routes, rather than each port inventing its own context-assembly or manifest shape.

---

### Claude Client

#### `resolve_advisor_model() -> str`

Returns the configured advisor synthesis model ID. Reads `ADVISOR_SYNTHESIS_MODEL` at call time — no daemon restart required to pick up a config change.

**Returns:** `str` — the model ID to use for all advisor LLM calls. Default: `"claude-opus-4-8"`.

**Source:** `ai_advisor.py:63-69`

Used by:
- `request_suggestions` — structured-output Claude call (`ai_advisor.py`)
- `explain_artifact` — chat explanation Claude call (`advisors/advisor_chat.py`)
- `_synthesize_via_claude` — nightly Market Prism synthesis (`advisors/lens_pipeline.py`)
- `app.py:3748` and `app.py:3781` — accept/reject audit-trail `model_id` field

Override: set `ADVISOR_SYNTHESIS_MODEL` in the daemon environment before start. All three call sites pick up the change on the next call without a code deploy.

---


#### `request_suggestions(context: dict) → tuple[ConfigSuggestionsResponse | None, str | None]`

Calls Claude's structured-output endpoint. Synchronous — blocks until the response returns or times out (30 seconds).

**Returns:** `(ConfigSuggestionsResponse, None)` on success; `(None, error_message)` on any failure. Never raises.

**Model:** `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")`, `max_tokens=2048`. The model is read at call time — override via the `ADVISOR_SYNTHESIS_MODEL` env var (see DE-SYNTH-001 in DECISIONS.md). `_CLAUDE_MODEL` was removed at 46a6bc4 (dead-constant cleanup); inline `os.environ.get()` at the `model=` argument is the canonical pattern.

An empty `suggestions` list is a valid non-error response ("no edit is well-supported"). D-1 security contract: fully honored — all failure paths (`messages.parse` at `ai_advisor.py:631` and client construction) return only `type(exc).__name__` to the browser. Full exception detail is logged server-side via `exc_info=True`; no exception text reaches the JSON response.

---

### C2 Safety Gates

#### `enforce_suggestion_allowlist(suggestions: list[ConfigSuggestion]) → tuple[list[ConfigSuggestion], list[ConfigSuggestion]]`

Partitions suggestions into `(allowed, rejected)` by `config_key`. Any key not in the 7-item suggestible allowlist is routed to `rejected`. Defense-in-depth: even if Claude hallucinates a key or emits a credential, it can never reach a live config write.

**Returns:** `(allowed, rejected)` — order-preserving; every suggestion in exactly one partition.

#### `compute_risk_direction(config_key: str, current_value, suggested_value) → str`

Computes, code-side, whether a suggestion loosens or tightens risk. The engine never trusts Claude's self-reported `risk_direction`. Derives the direction from each param's documented risk polarity (`_RAISE_RISK_DIRECTION`).

**Returns:** `"loosens"` | `"tightens"` | `"neutral"`.

#### `check_risk_direction_agreement(suggestion: ConfigSuggestion) → dict`

Cross-checks Claude's self-reported `risk_direction` against the engine's code-computed direction.

**Returns:** `{"agrees": bool, "code_direction": str, "claimed_direction": str}`.

#### `revalidate_suggestion_oos(symphony_id: str, config_key: str, suggested_value, current_strategy: dict) → dict`

Re-validates an accepted suggestion through the autotuner's OOS gate. Calls `run_simulation` twice (baseline + patched strategy) over the same history window. Pass rule is strict `>` — a tie does not pass.

The `autotuner` import is lazy (inside the function body) to avoid import-collision with the Anthropic SDK under pytest.

**Returns:** `{"passed": bool, "oas_alpha": float, "baseline_oos_alpha": float, "detail": str}`.

---

## Multi-Lens Scaffold

### Lens-Block Contract

Every lens helper returns a dict conforming to this contract:

```python
{
    "lens": str,        # lens name, e.g. "technicals"
    "available": bool,  # True only when the source is connected and data is present
    "reason": str,      # non-empty; explains unavailability or data provenance
    "payload": ...,     # None when available=False; structured data when available=True
    "sources": list,    # list of citation dicts (see Citation Convention); [] when unavailable
}
```

**Honest-availability rule (CC-3):** a lens helper MUST NOT fabricate a payload when `available` is `False`. This mirrors the `_build_volatility_regime` pattern (`ai_advisor.py:218–270`).

### Lens Helpers — Current Status

All five are wired as top-level keys in the dict returned by `assemble_advisor_context` (via the backward-compat aliases) and as entries in `context["lenses"]`. **As of DE-ADVISOR-LATENCY (2026-06-29), these builders are called NIGHTLY by `prism_scheduler._patch_provenance` and cached as a `MARKET_LENS_CACHE` row — they are NOT called per-click on advisor requests.** Cold-start (no cache row) degrades to `available=False, reason="lens_cache_unavailable"` for each lens.

| Helper | Key in context | Status | Producer |
|--------|----------------|--------|----------|
| `_build_technicals_section()` (`ai_advisor.py:489-560`) | `"technicals"` | **Wired** (2026-06-15); served from nightly cache (2026-06-29) | `advisors/lens_technicals.py` — MA posture, breadth, momentum |
| `_build_sentiment_section()` | `"sentiment"` | **Wired** (2026-06-15); served from nightly cache (2026-06-29) | `advisors/lens_gdelt.py` — GDELT 2.0 tone + citations |
| `_build_derivatives_section()` | `"derivatives"` | **Wired** (2026-06-16) — freshness-guarded; served from nightly cache (2026-06-29) | `advisors/lens_options_proxy.py` — FRED VIXCLS/VXVCLS; VIX level, term-structure regime, risk read; staleness guard (`_OPTIONS_PROXY_MAX_STALENESS_DAYS=10`) |
| `_build_macro_section()` | `"macro"` | **Wired** — FRED producer (DGS10/UNRATE/CPIAUCSL/FEDFUNDS); served from nightly cache (2026-06-29) | FRED API — 10-Year Treasury, Unemployment Rate, CPI-U, Federal Funds Rate; per-series value+date + clickable fred.stlouisfed.org citation; degrades to `available=False` when `FRED_API_KEY` absent |
| `_build_fundamentals_section()` | `"fundamentals"` | **Wired** (2026-06-16) — portfolio fan-out; vintage-correct (DE-FUND-001, DE-FUND-002); served from nightly cache (2026-06-29) | SEC EDGAR companyfacts — per-ticker key facts over live holdings ∪ `_FUNDAMENTALS_PROXY_UNIVERSE`; vintage-correct selection: multi-tag union sorted by `(end desc, filed desc)` |

Each accepts an optional `_data` argument (reserved for caller pre-injection; unused in current implementations) so future producers can be wired in without changing call sites in `assemble_advisor_context`.

### `_build_technicals_section(_data=None) → dict` (ai_advisor.py:489-560)

Wired (2026-06-15). Lazy-imports `advisors.lens_technicals` (CC-2), derives the universe from the UNION of live `database.load_state()` `logic_holdings` and `lens_technicals._PROXY_UNIVERSE` (a named market-proxy floor basket -- DE-TECH-002, corrects a stale doc claim that this called `_fetch_technicals([])` with an empty universe), and calls `_fetch_technicals(universe)`. Returns the lens block with `available=True` and `payload={ma_posture, breadth, momentum}` when bars are available; `available=False` with a named reason otherwise. Defense-in-depth: wraps the import+call in `try/except` — any unexpected exception returns `available=False, reason=type(exc).__name__`. See DE-TECH-SMA200-HISTORY-001 (2026-07-12) for the `_HISTORY_DAYS` fix that made `above_sma200` computable on real fetches.

Called nightly by `prism_scheduler._patch_provenance` to populate the MARKET_LENS_CACHE bundle. NOT called per advisor click as of DE-ADVISOR-LATENCY.

See [advisors/lens_technicals](advisors_lens_technicals.md) for indicator definitions, constants, and retry protocol.

### `_build_fundamentals_section(_data=None, *, ticker=None) → dict` (ai_advisor.py:1111)

Wired (2026-06-16 — DE-FUND-001; vintage-corrected 2026-06-17 — DE-FUND-002). Two paths:

**Single-ticker path** (`ticker="AAPL"`): delegates immediately to `_fetch_fundamentals_for_ticker(ticker)` and wraps the result in the standard lens-block shape (adds the `"lens"` key). Per-symphony callers that pass a ticker are unaffected by any fan-out change — behavior is preserved (AC-6).

**Portfolio fan-out path** (`ticker=None` — used by the nightly `_patch_provenance` and formerly by `assemble_advisor_context`):
1. Lazily calls `database.load_state()` (CC-2) to collect `logic_holdings` tickers from all monitored symphonies.
2. Merges with `_FUNDAMENTALS_PROXY_UNIVERSE` (unconditional floor — guarantees a non-empty universe at 03:00 / flat markets / off-hours).
3. Fans out `_fetch_fundamentals_for_ticker` over each ticker; per-ticker failures degrade only that ticker.
4. Returns `available=True` with `payload={tickers: {AAPL: {...}, ...}, coverage: {available: N, universe: M}}` when at least one ticker resolves.
5. Returns `available=False` when the universe is empty or every ticker fails — with a real reason, no fabricated payload.

**`_FUNDAMENTALS_PROXY_UNIVERSE`:** Module-level `frozenset[str]` — 8 large-cap company tickers (AAPL, MSFT, GOOGL, AMZN, NVDA, JPM, XOM, JNJ). Individual companies only; ETFs excluded because ETFs have no SEC EDGAR `companyfacts` entries (every CIK lookup would fail). Source comment cites S&P 500 large-cap 2024 constituents.

**D-1:** all failure reasons are `type(exc).__name__` for caught exceptions, or named labels (`"no fundamentals universe..."`, `"no fundamentals available: all tickers failed..."`) for authoritative empty states.

**CC-2:** `database.load_state()` is called lazily inside the function body, never at module level.

### `_fetch_fundamentals_for_ticker(ticker: str) → dict` (ai_advisor.py:952)

Helper that performs CIK resolution, companyfacts fetch, and concept extraction for a single ticker. Returns a per-ticker block without the top-level `"lens"` key — callers set `"lens"` on the outer block.

**Vintage-correct selection (DE-FUND-002, `ai_advisor.py:1011-1073`; all-forms fix advisor-suite-fixes AC-4, 2026-07-13):** For each logical concept in `_SEC_KEY_CONCEPTS`, the helper:
1. Unions entries across ALL candidate tags present in the `us-gaap` namespace (e.g. for Revenues: `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`, `Revenues` — whichever tags exist are included).
2. Considers ALL forms (10-K, 10-Q, ...) — the prior 10-K-only pre-filter was removed (AC-4); the `(end desc, filed desc)` sort below now picks the freshest reporting period regardless of form, so a fresher 10-Q is never shadowed by a stale 10-K.
3. Sorts the union by `(end desc, filed desc)` — the most recently reported accounting period wins; `filed` is a secondary tiebreak for restatements sharing the same `end` date.
4. Selects `entries_sorted[0]` — the entry with the latest `end`.
5. Wraps the whole per-concept block in `try/except` (AC-7 — never raises on malformed XBRL).

This resolves Mode B (sort-by-filed selected the oldest comparative entry from a 10-K bundle) and Mode A (a single hardcoded tag never reached data under migrated GAAP concepts). **AC-4 (2026-07-13, operator-approved reversal of the prior 10-K-only scope-out):** the 10-K-only pre-filter (old step 2) discarded a fresher 10-Q whenever any 10-K existed for the same concept — live evidence was AAPL resolving to its 2025-09 10-K instead of the ~2026-03 10-Q. See `feature-plans/lens-fundamentals-vintage-fix.completed.md`'s append-only "Superseded" section and `DECISIONS.md` `DE-ADVISOR-SUITE-FIX-001`.

**Returned shape on success:** `{available: True, payload: {entity_name, cik, key_facts: {"Revenues": {label, value, unit, end, filed, form}, ...}}, sources: [{title, url, published, lens}]}`. The `key_facts` outer keys are the stable logical concept keys from `_SEC_KEY_CONCEPTS` (e.g. `"Revenues"`, `"NetIncomeLoss"`) — not the XBRL candidate-tag names.

**Returned shape on failure:** `{available: False, reason: str, payload: None, sources: []}`.

**Flow:** CIK resolution via `_sec_ticker_to_cik` (SEC bulk tickers file) → `_fetch_with_backoff` for the companyfacts JSON → vintage-correct extraction loop (`ai_advisor.py:1011-1073`) → citation assembly from accession numbers. All HTTP fetches use `_SEC_USER_AGENT` (mandatory per SEC EDGAR terms). D-1: `type(exc).__name__` on any caught exception; named labels for authoritative failures (CIK not found, no key facts).

**Never raises.** Per-ticker degradation: if CIK is not found, the companyfacts endpoint is unreachable, or no recognized key facts exist in the response, the function returns `available=False` — it does not raise.

### Citation Convention

#### `build_citation(citation: dict) → dict | None`

Validates and returns a structured citation. All four fields are required; the URL must be a well-formed `http://` or `https://` URL with a non-empty host.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `citation` | `dict` | Must have `title`, `url`, `published`, `lens` — all non-empty strings |

**Returns:** The citation dict unchanged if valid; `None` for any malformed input. Never raises.

**Alias:** `validate_citation = build_citation` (both names are importable).

**Citation-missing rule (CC-4):** a claim with no valid source (i.e., `build_citation` returns `None`) must be suppressed by the caller — never surfaced without a source.

A well-formed citation dict:

```python
{
    "title": "Fed holds rates steady",
    "url": "https://example.com/fed-rates",
    "published": "2026-06-10",
    "lens": "macro",
}
```

**Storage:** citations are stored in `advisor_observations.raw_response` (JSON). No schema migration is required — `raw_response` is an existing JSON column. See [DE-ML-001 in DECISIONS.md](../../DECISIONS.md).

## Types

### Pydantic Schemas

#### `ConfigSuggestion`

| Field | Type | Description |
|-------|------|-------------|
| `config_key` | `str` | One of the 7 suggestible keys |
| `current_value` | `float \| int \| str` | Current live value |
| `suggested_value` | `float \| int \| str` | Claude's proposed value |
| `rationale` | `str` | Claude's reasoning, citing supplied numbers |
| `risk_direction` | `str` | `"loosens"` \| `"tightens"` \| `"neutral"` (Claude self-classifies; engine cross-checks) |
| `confidence` | `str` | Claude's confidence level |
| `data_sufficiency` | `str` | `"sufficient"` \| `"insufficient"` |
| `oos_status` | `str` | `"pending"` \| `"passed"` \| `"rejected"` |
| `oos_reason` | `str \| None` | OOS re-validation detail |
| `impact` | `dict` | `{"metric": "sharpe", "delta": 0.0}` |

#### `ConfigSuggestionsResponse`

| Field | Type | Description |
|-------|------|-------------|
| `suggestions` | `list[ConfigSuggestion]` | Zero or more suggestions. Empty list = valid abstention |

### Suggestible Config Surface

The 7-item allowlist (6 Optuna search-space keys + `MAX_SQUEEZE_FLOOR`). Note: `TRIGGER_THRESHOLD_PCT` is the default locked variable (`database.DEFAULT_LOCKED_VARS`) and is NOT suggestible — any suggestion for it would be routed to `rejected` by `enforce_suggestion_allowlist`.

| Key | Optuna-tuned | Risk Polarity |
|-----|-------------|---------------|
| `TAKE_PROFIT_MC_PCT` | Yes | raising tightens risk (inverted) |
| `VWAP_CROSS_HWM_PCT` | Yes | raising loosens risk |
| `VWAP_BLEED_MULTIPLIER` | Yes | raising loosens risk |
| `VWAP_BLEED_TICKS` | Yes | raising loosens risk |
| `PARABOLIC_VELOCITY_THRESHOLD` | Yes | raising loosens risk |
| `MAX_PARABOLIC_SQUEEZE` | Yes | raising loosens risk |
| `MAX_SQUEEZE_FLOOR` | No (`_UNTUNED_SUGGESTIBLE_KEY`) | raising loosens risk |

### Module-Level Constants

| Constant | Type | Value | Purpose |
|----------|------|-------|---------|
| `_LENS_CACHE_MAX_AGE_HOURS` | `int` | `36` | Freshness window for the nightly MARKET_LENS_CACHE bundle. A bundle within 36 hours is considered fresh; older is served with a stale label. 36 hours covers a missed council night while allowing the next nightly run to refresh. `ai_advisor.py:61-63`. |
| `_FUNDAMENTALS_PROXY_UNIVERSE` | `frozenset[str]` | 8 company tickers | Unconditional floor for portfolio fan-out path; guarantees non-empty universe at 03:00 / flat markets. Individual companies only — ETFs excluded (no SEC companyfacts). |
| `_SEC_USER_AGENT` | `str` | `"Planet Stopper AlphaBot..."` | Mandatory SEC EDGAR User-Agent (missing UA is the primary cause of 403 responses). |
| `_SEC_KEY_CONCEPTS` | `dict[str, tuple[str, tuple[str, ...]]]` | logical concept → (display label, ordered candidate tags) | Maps each of the 5 recognized financial concepts to a display label and an ordered tuple of XBRL us-gaap candidate tags. All present candidate tags are unioned per concept; entry with most recent `end` wins. Outer logical keys are stable (`Revenues`, `NetIncomeLoss`, `Assets`, `Liabilities`, `StockholdersEquity`) — these are the `key_facts` output keys. `ai_advisor.py:361-374`. |
| `_REQUEST_TIMEOUT_SECONDS` | `float` | `30.0` | Explicit client-side timeout for all Anthropic SDK calls. Never rely on SDK/urllib3 default. |
| `_MAX_TOKENS` | `int` | `2048` | Max output tokens for the structured-output Claude call in `request_suggestions`. |
| `_EMPTY_MANIFEST` | `dict` | 7-key, all `"absent"` | R2-1: default honest manifest returned by `build_reasoning_context` when nothing is injectable. `ai_advisor.py:67-78`. |
| `_MAX_TREE_RENDER_CHARS` | `int` | `6000` | R2-1: INPUT-context bound — the rendered real-tree text `build_reasoning_context` injects into the SB generation prompt (AC-9). Independent of `build_plan_generator.MAX_OUTPUT_TOKENS`. `ai_advisor.py:80-88`. |

## Internal Dependencies

- `database` — `get_latest_autotune_run`, `get_symphony_strategy`, `load_state`, `normalize_name`, `DEFAULT_STRATEGY`, `DEFAULT_LOCKED_VARS`, **`get_latest_market_lens_cache`** (DE-ADVISOR-LATENCY cache-serve path), **`insert_advisor_observation`** (called by `persist_market_lens_cache`)
- `symphony_logic` — `get_condensed_logic` (called with Composer hash ID via `composer_symphony_id`, not normalized name); `fetch_symphony_score` (R2-1, `build_reasoning_context` real-tree source). **Transitive:** `symphony_logic.py` itself imports `alpha_bot_execution` (`COMPOSER_BASE_URL`, `get_composer_headers`) at module level — see [advisors/strategy_builder_engine](advisors_strategy_builder_engine.md)'s "Internal Dependencies" section for the accepted-precedent note this creates for R2-1's new `strategy_builder_engine` → `ai_advisor` import edge.
- `advisors.symphony_schema` — `render_rules_text` (R2-1, `build_reasoning_context` — renders the real tree into bounded prose, never a raw JSON dump)
- `advisors.prism_render` — `humanize_lens_summary` (R2-1, `build_reasoning_context` — reuses the Overview tab's humanizer for injected lens prose; no second hand-rolled renderer)
- `autotuner` — `run_simulation`, `calculate_historical_deviation` (lazy import in `revalidate_suggestion_oos`)
- `synthetic_history` — `generate_synthetic_history` (lazy import in `revalidate_suggestion_oos`)
- `advisors.lens_technicals` — `_fetch_technicals` (lazy import in `_build_technicals_section`; called nightly by prism_scheduler, not per advisor click)
- `advisors.lens_gdelt` — `_fetch_gdelt_sentiment` (lazy import in `_build_sentiment_section`; called nightly)
- `anthropic` SDK — `messages.parse` with structured output
- `pydantic` — `ConfigSuggestion`, `ConfigSuggestionsResponse`
- `requests` — SEC EDGAR HTTP fetches in `_fetch_fundamentals_for_ticker` / `_fetch_with_backoff` (direct import; no lazy boundary needed — SEC calls are off-execution-path advisory only)
