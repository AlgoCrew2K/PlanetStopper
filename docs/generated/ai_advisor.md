# ai_advisor

> Claude-backed config advisor: context assembly, structured-output Claude call, per-symphony assessment, safety gates (7-item allowlist, risk-direction cross-check, OOS re-validation), and multi-lens pipeline (technicals wired; sentiment wired; derivatives wired with freshness guard; fundamentals wired with portfolio fan-out; macro stub).

**Source:** `ai_advisor.py`
**Last updated:** 2026-06-17

## Overview

`ai_advisor.py` is the operator-assist config advisory surface. It is split into two cycles:

- **C1** — Context assembly + synchronous Claude call. `assemble_advisor_context` reads a curated 7-item allowlist of config values (6 Optuna search-space keys + `MAX_SQUEEZE_FLOOR`), never `os.environ`. `request_suggestions` calls Claude with structured output (`ConfigSuggestionsResponse`). `build_assessment_from_context` synthesises a per-symphony assessment from the assembled context so the UI can explain why no suggestion was made — the common case for symphonies with no validated edge. Never raises — every failure degrades to `(None, error_message)`.

- **C2** — Safety gates. Three independent defense-in-depth layers on top of C1's context allowlist: `enforce_suggestion_allowlist`, `check_risk_direction_agreement`, `revalidate_suggestion_oos`.

Real-money-critical input governance: `assemble_advisor_context` never includes credentials, account IDs, safety flags, or methodology knobs. The config surface is an allowlist, not a denylist.

**Liveness fix (2026-06-10):** `assemble_advisor_context` now accepts `composer_symphony_id` and `autotune_run` parameters. The route passes the Composer hash ID via `composer_symphony_id` so that `symphony_logic.get_condensed_logic` receives the hash the Composer `/score` API expects — passing the normalized name previously produced HTTP 400 and an empty logic struct. The `autotune_run` parameter is now fully honored — when a pre-fetched row is passed (non-`_SENTINEL`), the internal `database.get_latest_autotune_run` call is skipped, avoiding a redundant DB round-trip.

**Regime context fix (2026-06-10):** `_build_volatility_regime` sets `available: False` with an explicit `reason` when `vol/atr` keys are absent from the autotune run row, rather than fabricating `available: True` with all-null fields.

**Cycle-1 multi-lens scaffold (2026-06-10):** Five lens helpers added (`_build_technicals_section`, `_build_sentiment_section`, `_build_derivatives_section`, `_build_macro_section`, `_build_fundamentals_section`), each following the honest-availability pattern. A citation convention (`build_citation` / `validate_citation`) enforces well-formed sources. See [Multi-Lens Scaffold](#multi-lens-scaffold) below.

**Technicals lens wiring (2026-06-15):** `_build_technicals_section` (`ai_advisor.py:439-482`) replaced its Cycle-1 stub with a real producer. It lazy-imports `advisors.lens_technicals` (CC-2), derives the universe from `database.load_state()` holdings (tickers across all monitored symphonies), and calls `_fetch_technicals(universe)`. Returns `available=True` with MA posture, breadth, and momentum payload when bars are available; `available=False` with a named reason otherwise. See [advisors/lens_technicals](advisors_lens_technicals.md).

**Sentiment lens wiring (GDELT, 2026-06-15):** `_build_sentiment_section` lazy-imports `advisors.lens_gdelt` and calls `_fetch_gdelt_sentiment`. Honest-availability: `tone is None → available=False`. See [advisors/lens_gdelt](advisors_lens_gdelt.md).

**Derivatives lens wiring (FRED VIX/VXV, 2026-06-16):** `_build_derivatives_section` lazy-imports `advisors.lens_options_proxy` and calls `_fetch_options_proxy()`. Honest-availability now covers **staleness** as well as fetch failure: the freshness guard (`_OPTIONS_PROXY_MAX_STALENESS_DAYS = 10`) rejects observations older than 10 calendar days as `available=False, reason="stale_data"`. Prior stub behavior is superseded — the producer returns a real VIX level, term-structure, and risk read when `FRED_API_KEY` is set and data is fresh. See [advisors/lens_options_proxy](advisors-lens-options-proxy.md).

**Fundamentals lens portfolio fan-out (2026-06-16 — DE-FUND-001):** `_build_fundamentals_section()` (called with no ticker by both the 03:00 nightly pipeline and `assemble_advisor_context`) previously short-circuited to `available=False, reason="ticker symbol required..."` — a dead lens. Now fans out over a company-ticker universe (live `logic_holdings` ∪ `_FUNDAMENTALS_PROXY_UNIVERSE` floor of 8 large-cap company tickers — NOT ETFs, which have no SEC companyfacts). The single-ticker path (`ticker="AAPL"`) is preserved byte-for-byte via the extracted `_fetch_fundamentals_for_ticker(ticker)` helper. Per-ticker honest degradation; no invented composite ratios; bounded fan-out. See [DE-FUND-001 in DECISIONS.md](../../DECISIONS.md).

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

As of Cycle-1, the returned dict also includes five top-level lens keys:
`technicals`, `sentiment`, `derivatives`, `macro`, `fundamentals` — each a lens block dict (see [Multi-Lens Scaffold](#multi-lens-scaffold)).

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `scope` | `str` | `"symphony"` or `"global"` |
| `symphony_id` | `str \| None` | Required when `scope == "symphony"`; used as key for all state-DB lookups (autotune_runs, symphony_strategies) |
| `composer_symphony_id` | `str \| None` | Optional Composer hash ID; passed to `get_condensed_logic` when present (hash required by Composer /score API) |
| `autotune_run` | `dict \| None \| _SENTINEL` | Optional pre-fetched autotune run. When non-`_SENTINEL`, the value is used directly and `database.get_latest_autotune_run` is skipped (avoids a second DB round-trip). Pass `_SENTINEL` (the default) to have the function fetch from DB internally. Explicit `None` means "Optuna has not run" — also skips the fetch. |

**Returns:** Well-shaped context dict including all lens blocks. Never raises; degrades gracefully when Optuna has not run.

**Raises:** `ValueError` when `scope == "symphony"` and `symphony_id` is `None`.

---

#### `build_assessment_from_context(context: dict) → dict`

Builds a per-symphony assessment dict from the assembled context. Resolves the UI empty-state problem: `ConfigSuggestionsResponse` carries only a `suggestions` list — the route previously discarded the assembled context entirely, leaving the result box showing an identical generic message for every symphony regardless of tuning state.

The assessment is derived from `context["optuna_evidence"]` which carries `baseline_decision`, `oos_alpha`, `fallback_oos_alpha`, `default_oos_alpha`, and `available`. The `summary` string is differentiated per tuning state:

- No Optuna run: explains config is unvalidated.
- `oos_alpha is None` (all trials haircut-rejected by FDR gate): explains all trials failed the significance gate; quotes fallback and default alphas. This is the expected state for most symphonies — the FDR + PBO gates are intentionally strict.
- `oos_alpha` present: summarises the validated edge with numeric values.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `context` | `dict` | Output of `assemble_advisor_context` or any dict with `optuna_evidence` key |

**Returns:** `{"baseline_decision": str | None, "oas_alpha": float | None, "fallback_oas_alpha": float | None, "default_oas_alpha": float | None, "summary": str}`.

---

### Claude Client

#### `request_suggestions(context: dict) → tuple[ConfigSuggestionsResponse | None, str | None]`

Calls Claude's structured-output endpoint. Synchronous — blocks until the response returns or times out (30 seconds).

**Returns:** `(ConfigSuggestionsResponse, None)` on success; `(None, error_message)` on any failure. Never raises.

**Model:** read from `ADVISOR_SYNTHESIS_MODEL` env var (default `claude-opus-4-8`), `max_tokens=2048`. Set `ADVISOR_SYNTHESIS_MODEL=mock-model` in tests to prevent real API calls (AC-4).

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

All five are wired as top-level keys in the dict returned by `assemble_advisor_context`:

| Helper | Key in context | Status | Producer |
|--------|----------------|--------|----------|
| `_build_technicals_section()` (`ai_advisor.py:439-482`) | `"technicals"` | **Wired** (2026-06-15) | `advisors/lens_technicals.py` — MA posture, breadth, momentum |
| `_build_sentiment_section()` | `"sentiment"` | **Wired** (2026-06-15) | `advisors/lens_gdelt.py` — GDELT 2.0 tone + citations |
| `_build_derivatives_section()` | `"derivatives"` | **Wired** (2026-06-16) — freshness-guarded | `advisors/lens_options_proxy.py` — FRED VIXCLS/VXVCLS; VIX level, term-structure regime, risk read; staleness guard (`_OPTIONS_PROXY_MAX_STALENESS_DAYS=10`) |
| `_build_macro_section()` | `"macro"` | Stub — `available=False` | FRED / US Treasury XML (not yet connected) |
| `_build_fundamentals_section()` | `"fundamentals"` | **Wired** (2026-06-16) — portfolio fan-out | SEC EDGAR companyfacts — per-ticker key facts over live holdings ∪ `_FUNDAMENTALS_PROXY_UNIVERSE` (DE-FUND-001) |

Each accepts an optional `_data` argument (reserved for caller pre-injection; unused in current implementations) so future producers can be wired in without changing call sites in `assemble_advisor_context`.

### `_build_technicals_section(_data=None) → dict` (ai_advisor.py:439-482)

Wired (2026-06-15). Lazy-imports `advisors.lens_technicals` (CC-2) and calls `_fetch_technicals([])`. Returns the lens block with `available=True` and `payload={ma_posture, breadth, momentum}` when bars are available; `available=False` with a named reason otherwise. Defense-in-depth: wraps the import+call in `try/except` — any unexpected exception returns `available=False, reason=type(exc).__name__`.

See [advisors/lens_technicals](advisors_lens_technicals.md) for indicator definitions, constants, and retry protocol.

### `_build_fundamentals_section(_data=None, *, ticker=None) → dict`

Wired (2026-06-16 — DE-FUND-001). Two paths:

**Single-ticker path** (`ticker="AAPL"`): delegates immediately to `_fetch_fundamentals_for_ticker(ticker)` and wraps the result in the standard lens-block shape (adds the `"lens"` key). Per-symphony callers that pass a ticker are unaffected by the fan-out change — behavior is byte-for-byte preserved (AC-3).

**Portfolio fan-out path** (`ticker=None` — used by the 03:00 nightly pipeline and `assemble_advisor_context`):
1. Lazily calls `database.load_state()` (CC-2 — never at module import time) to collect `logic_holdings` tickers from all monitored symphonies.
2. Merges with `_FUNDAMENTALS_PROXY_UNIVERSE` (unconditional floor — guarantees a non-empty universe at 03:00 / flat markets / off-hours).
3. Fans out `_fetch_fundamentals_for_ticker` over each ticker; per-ticker failures degrade only that ticker.
4. Returns `available=True` with `payload={tickers: {AAPL: {...}, ...}, coverage: {available: N, universe: M}}` when at least one ticker resolves.
5. Returns `available=False` when the universe is empty or every ticker fails — with a real reason, no fabricated payload.

**`_FUNDAMENTALS_PROXY_UNIVERSE`:** Module-level `frozenset[str]` — 8 large-cap company tickers (AAPL, MSFT, GOOGL, AMZN, NVDA, JPM, XOM, JNJ). Individual companies only; ETFs excluded because ETFs have no SEC EDGAR `companyfacts` entries (every CIK lookup would fail). Source comment cites S&P 500 large-cap 2024 constituents.

**D-1:** all failure reasons are `type(exc).__name__` for caught exceptions, or named labels (`"no fundamentals universe..."`, `"no fundamentals available: all tickers failed..."`) for authoritative empty states.

**CC-2:** `database.load_state()` is called lazily inside the function body, never at module level.

### `_fetch_fundamentals_for_ticker(ticker: str) → dict`

Helper extracted from the original single-ticker body of `_build_fundamentals_section` to enable the portfolio fan-out path. Returns a per-ticker block without the top-level `"lens"` key — callers set `"lens"` on the outer block.

**Returned shape on success:** `{available: True, payload: {entity_name, cik, key_facts: {RevenueFromContractWithCustomerExcludingAssessedTax: ..., ...}}, sources: [{title, url, published, lens}]}`.

**Returned shape on failure:** `{available: False, reason: str, payload: None, sources: []}`.

**Flow:** CIK resolution via `_sec_ticker_to_cik` (SEC bulk tickers file) → `_fetch_with_backoff` for the companyfacts JSON → extraction of `_SEC_KEY_CONCEPTS` keys → citation assembly from accession numbers. All HTTP fetches use `_SEC_USER_AGENT` (mandatory per SEC EDGAR terms). D-1: `type(exc).__name__` on any caught exception; named labels for authoritative failures (CIK not found, no key facts).

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

### Module-Level Constants (Fundamentals Lens)

| Constant | Type | Value | Purpose |
|----------|------|-------|---------|
| `_FUNDAMENTALS_PROXY_UNIVERSE` | `frozenset[str]` | 8 company tickers | Unconditional floor for portfolio fan-out path; guarantees non-empty universe at 03:00 / flat markets. Individual companies only — ETFs excluded (no SEC companyfacts). |
| `_SEC_USER_AGENT` | `str` | `"Planet Stopper AlphaBot..."` | Mandatory SEC EDGAR User-Agent (missing UA is the primary cause of 403 responses). |
| `_SEC_KEY_CONCEPTS` | `dict[str, str]` | XBRL → human label map | Defines the set of recognized financial facts extracted from companyfacts responses; keys not in this dict are ignored. |

### Module-Level Constants (LLM Config)

| Constant | Type | Value | Purpose |
|----------|------|-------|---------|
| `_CLAUDE_MODEL` | `str` | `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")` | Model for all advisor LLM calls. Read at import time. Override via `ADVISOR_SYNTHESIS_MODEL` env var in tests. |
| `_MAX_TOKENS` | `int` | 2048 | Token budget for structured-output suggestions response. |
| `_REQUEST_TIMEOUT_SECONDS` | `float` | 30.0 | Client-side timeout; never relies on SDK/urllib3 default. |

## Internal Dependencies

- `database` — `get_latest_autotune_run`, `get_symphony_strategy`, `load_state`, `normalize_name`, `DEFAULT_STRATEGY`, `DEFAULT_LOCKED_VARS`
- `symphony_logic` — `get_condensed_logic` (called with Composer hash ID via `composer_symphony_id`, not normalized name)
- `autotuner` — `run_simulation`, `calculate_historical_deviation` (lazy import in `revalidate_suggestion_oos`)
- `synthetic_history` — `generate_synthetic_history` (lazy import in `revalidate_suggestion_oos`)
- `advisors.lens_technicals` — `_fetch_technicals` (lazy import in `_build_technicals_section`)
- `advisors.lens_gdelt` — `_fetch_gdelt_sentiment` (lazy import in `_build_sentiment_section`)
- `anthropic` SDK — `messages.parse` with structured output
- `pydantic` — `ConfigSuggestion`, `ConfigSuggestionsResponse`
- `requests` — SEC EDGAR HTTP fetches in `_fetch_fundamentals_for_ticker` / `_fetch_with_backoff` (direct import; no lazy boundary needed — SEC calls are off-execution-path advisory only)
