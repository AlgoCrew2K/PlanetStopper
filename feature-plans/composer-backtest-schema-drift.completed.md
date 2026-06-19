# Research Report: Composer `POST /api/v0.1/backtest` Inline-Symphony Schema Drift

**Researcher:** composer-api-researcher
**Date:** 2026-06-18
**Confidence Summary:** HIGH that the fix is to send `symphony.encoding_type` + `symphony.encoded_value` instead of `symphony.raw_value`, and HIGH that the only allowed `encoding_type` value is `"transit_json"`. MEDIUM-LOW on the exact byte-level production of `encoded_value` from a `/score` `raw_value` tree — the published spec gives no example and Composer's source (MCP server, parser) is anonymously inaccessible, so the precise transit-json keying MUST be confirmed empirically by the fix team (ranked test plan in §4).

---

## Research Questions

1. Confirm or refute that the HTTP 400 is caused by a `raw_value` → `encoding_type` + `encoded_value` schema drift (vs. some other cause).
2. The exact new inline-backtest request body shape: valid `encoding_type` value(s) and how `encoded_value` is produced from a `raw_value` tree.
3. Citations/evidence, date-stamped.
4. If not fully determinable from research, the exact empirical test the fix team must run, ranked.

---

## Findings

### 1. Drift diagnosis: CONFIRMED (with one important nuance)

The PM diagnosis is **confirmed**: the live API now wants `symphony.encoding_type` + `symphony.encoded_value` and is rejecting `symphony.raw_value`.

The live malli 400 body is decisive:
```json
{"humanized": {"symphony": {
  "raw_value": {"description": ["missing required key"], "malli/error": ["disallowed key"]},
  "encoding_type": ["missing required key"],
  "encoded_value": ["missing required key"]}}}
```
Read literally against malli semantics:
- `encoding_type` / `encoded_value` → `"missing required key"` ⇒ the live schema **requires** these two keys.
- `raw_value` → `"disallowed key"` ⇒ the live schema **rejects** `raw_value` at the `symphony` level.
- The nested `raw_value.description: ["missing required key"]` is malli still trying the *old* `raw_value` branch and finding the posted tree itself now also lacks a required `description` key — a secondary signal the old branch is being deprecated, not just the wrapper.

This is **not** an auth error, not a rate-limit (429), not a transport error. It is a request-body validation failure (`malli/error`) on the `symphony` wrapper. The body the client sends today (`composer_backtest_client.py:287-295`) is `{"symphony": {"raw_value": <tree>}, ...}` — exactly the now-disallowed shape.

**IMPORTANT CONFLICT (must be surfaced, not hidden):** The *published* OpenAPI spec (`api.composer.trade/docs/swagger.json`, fetched 2026-06-18) still documents `raw_value` as a **valid and required** option for `/api/v0.1/backtest`. The spec models `symphony` as an `anyOf`:
- Option A (listed first): `{ raw_value: {...} }`, required `["raw_value"]`
- Option B: `{ encoded_value: string, encoding_type: enum["transit_json"] }`, required `["encoded_value","encoding_type"]`

So the spec says *either* works; the **live malli validator says only Option B works** (and actively disallows `raw_value`). When the published swagger disagrees with the deployed malli validator, **the live 400 is ground truth** — Composer's backend validates with malli and the swagger.json is a separately-generated artifact that has lagged the deployment. (This is consistent with the project's standing "assume drift" gotcha for Composer.) `[High]` on "live requires Option B"; the swagger's continued `raw_value` documentation is treated as **stale spec**, not a contradiction of the live evidence.

`[High]` — drift confirmed by the live malli 400 (Tier-1 observed-network, PM-captured) corroborated by the published anyOf schema that introduces the `encoded_value`/`encoding_type` branch (Tier-1 documented).

### 2. The new inline-backtest request body shape

**`encoding_type`** — the only allowed enum value is **`"transit_json"`** (underscore, not hyphen). Confirmed by three reads of the swagger via the jina reader proxy + the Redocly render; no `"json"`, `"edn"`, or hyphenated `"transit-json"` variant appears in the spec.
*(Source: api.composer.trade/docs/swagger.json via r.jina.ai, accessed 2026-06-18, Tier 1, observation: documented, Confidence: High)*

**New body shape (Option B):**
```python
body = {
    "symphony": {
        "encoding_type": "transit_json",
        "encoded_value": <transit-json STRING of the symphony tree>,
    },
    "capital": capital,                 # required
    "apply_reg_fee": apply_reg_fee,     # required
    "apply_taf_fee": apply_taf_fee,     # required
    "slippage_percent": slippage_percent,  # required (0.01 = 1%)
    "broker": broker,                   # required
    # backtest_version / start_date / end_date / benchmarks: optional, unchanged
}
```
Top-level `required` fields per spec: `["capital","apply_reg_fee","apply_taf_fee","slippage_percent","broker","symphony"]`. These are **unchanged** from the working 2026-05-31 schema — only the inside of `symphony` changed. So the client's existing top-level params (`capital`, fee flags, `slippage_percent`, `broker`, `backtest_version`) all stay as-is; the *only* change is `symphony: {"raw_value": tree}` → `symphony: {"encoding_type": "transit_json", "encoded_value": <string>}`.

**What is `transit_json` / how is `encoded_value` produced?** — this is the load-bearing detail and the one with residual uncertainty.

- Composer is a Clojure backend; symphonies are natively **EDN** (Extensible Data Notation, Clojure data with keyword keys like `:step`, `:children`, `:ticker`). The community parser repo describes downloaded symphonies as `"json formatted edn_encoded"` captured from backtest network traffic. `[Medium]` (Tier-3 community, corroborated by the malli/Clojure error shape — Tier-1).
- **Transit-json** (Cognitect Transit, JSON variant) is a JSON-based wire format that preserves richer types than plain JSON — notably **Clojure keywords**, which are encoded with a **`~:` prefix** (e.g. the map key `:step` becomes the JSON string `"~:step"`). Transit also uses a caching/`"^ "` map-marker convention. `[High]` on transit-json mechanics (Tier-1, cognitect docs + transit-python docs).
- **Therefore `encoded_value` is almost certainly NOT `json.dumps(raw_value)`.** The `/score` endpoint returns `raw_value` as plain JSON with **string** keys (`"step"`, `"children"`), having already projected the native keywords to strings. A faithful `transit_json` re-encoding would need those keys re-keywordized (`"step"` → `"~:step"`), which `json.dumps` will not do. This is the crux of the implementation risk. `[Medium — interpretation]`

**My interpretation (labeled):** producing a correct `encoded_value` requires running the symphony tree through a real transit-json *writer* with the map keys treated as keywords — not a naive `json.dumps`. Two concrete production paths for the fix team to try (ranked in §4):
1. A Transit writer (`transit-python2`, the maintained Py3 fork of the archived `cognitect/transit-python`) with keys wrapped as transit `Keyword` objects → yields `~:`-prefixed keys.
2. A plain `json.dumps(raw_value)` assigned to `encoded_value` with `encoding_type:"transit_json"` — only works IF the backend accepts string-keyed transit (cheap to try first; may well fail).

I could not find a single public example of a working `encoded_value` POST to confirm which path the live backend accepts. The published spec gives **no example string** for `encoded_value` (verified — asked the proxy specifically; none present).

### Auth / rate-limit / other params — UNCHANGED

Re-verified 2026-06-18: base URL `https://api.composer.trade/`, headers `x-api-key-id` + `authorization: Bearer`, standard 1 req/sec (the 500 req/sec exception is ONLY the saved-id `/symphonies/{id}/backtest` path, not the inline `/backtest` path). Help-center "Getting Started" article last-updated 2025-07-16. The client's auth (`get_composer_headers()`), retry policy, and 1-req/s spacing need **no change**.
*(Source: help.composer.trade/article/236, accessed 2026-06-18, Tier 1, Confidence: High)*

---

## Analysis

What the findings imply, if the project's assumptions hold:

- The fix is **narrow and localized** to `composer_backtest_client.run_backtest`, lines ~287-295: replace the `symphony` sub-dict. Nothing else in the request changes; the response-parsing path (`_parse_response`, `_extract_returns`, `dvm_capital`) is untouched by this drift (the 400 happens before any 200 body is produced, so response shape is not implicated by *this* break).
- The **response schema may have drifted too**, but we have no evidence of that yet — the request never reaches 200 today. The fix team should capture a fresh 200 fixture once the request is accepted and diff it against the 2026-05-31 response assumptions (`stats`, `dvm_capital`, `data_warnings`, `first_day`/`last_market_day`, `costs`). Flag as a follow-on, not part of this fix.
- The real risk is **`encoded_value` production**. A wrong guess (e.g. plain `json.dumps`) will burn a TDD cycle on a fixture that passes unit tests but 400s live — exactly the "tests-green but hollow" failure mode this project guards against. The fix MUST be validated with a **live POST** (not just a fixture) before the cycle is called done, per the project's live-functional-test merge gate.

---

## Recommendations (options + trade-offs — PM/cycle-pair decide)

**On where to get a ground-truth `encoded_value`:**
- **Option 1 — Capture the live wire format first (recommended).** Before writing the client fix, run the empirical probe in §4 to discover the exact `encoded_value` Composer accepts (and ideally capture one real 200 response). This makes the fixture provenance "captured-from-producer" (Gate-1 clean) rather than a parser+fixture co-design guess (Gate-1 fail per project rule). Trade-off: requires a Bash-capable agent with live Composer creds (the integrations agents, not this researcher).
- **Option 2 — Adopt a Transit library.** Add `transit-python2` as a dependency and encode via a real Transit writer. Trade-off: new dependency; must confirm the keyword-keying matches what Composer expects (still needs the §4 probe to confirm).
- **Option 3 — Mirror Composer's own MCP client.** The official `composer-trade-mcp` (PyPI v0.1.7, 2025-07-14) `backtest_symphony` tool is the canonical current implementation, but its source is anonymously inaccessible (GitHub repo + uithub mirror are auth-walled/404 in this environment). Trade-off: a Bash agent could `pip download composer-trade-mcp==0.1.7`, unpack the sdist, and read exactly how it builds the `/backtest` body — the single highest-value move to resolve the `encoded_value` question definitively. **Strongly recommend the fix team do this.**

**On the fix scope:** keep it to the `symphony` sub-dict in `run_backtest`. Do not invent new endpoints or change the top-level params — adopt the existing live contract (Option B) exactly.

---

## Open Questions (for the fix team — empirical, not researchable)

1. Does `encoded_value` need `~:`-prefixed (keyword) keys, or are plain string keys accepted? **(load-bearing — resolves the whole fix)**
2. Is `raw_value` now *fully* dead, or does the live malli still accept it under some other top-level flag? (The 400 says disallowed in the current call; treat as dead.)
3. Has the **response** shape (`dvm_capital`, `stats`) drifted since 2026-05-31? (Unknowable until a 200 is achieved.)
4. Does the inline tree still need an `id`/`step:"root"`/`description` when transit-encoded? (The nested `raw_value.description: ["missing required key"]` hints `description` may now be required inside the tree.)

---

## §4 — EXACT empirical test plan for the fix team (has Bash + live creds; this researcher does not)

**Step 0 (highest value, do first):** unpack Composer's own client and read the truth:
```bash
pip download composer-trade-mcp==0.1.7 --no-deps -d /tmp/ctm
tar xzf /tmp/ctm/composer_trade_mcp-0.1.7.tar.gz -C /tmp/ctm
grep -rn "encoded_value\|encoding_type\|transit\|/backtest\|raw_value" /tmp/ctm
```
This reveals exactly how `backtest_symphony` builds the body and produces `encoded_value`. If found, skip the guessing below.

**Otherwise, ranked live POST probes** (POST to `{COMPOSER_BASE_URL}/backtest` with real headers, a small known-good public symphony's `/score` `raw_value` as the tree, `capital=10000`, fees True, `slippage_percent=0.005`, `broker="alpaca"`; check for HTTP 200). Try in this order, stop at first 200:

1. **Transit writer, keyword keys** (most likely correct):
   ```python
   from transit.writer import Writer; from transit.transit_types import Keyword
   import io
   def kw(o):  # recursively re-key dict keys as Keywords
       if isinstance(o, dict):  return {Keyword(k): kw(v) for k,v in o.items()}
       if isinstance(o, list):  return [kw(v) for v in o]
       return o
   buf = io.StringIO(); Writer(buf, "json").write(kw(raw_value))
   encoded_value = buf.getvalue()
   body = {"symphony": {"encoding_type": "transit_json", "encoded_value": encoded_value}, ...}
   ```
2. **Transit writer, string keys** (no keywordizing):
   ```python
   buf = io.StringIO(); Writer(buf, "json").write(raw_value); encoded_value = buf.getvalue()
   ```
3. **Plain JSON string** (cheapest; likely 400 but rules out the simple case):
   ```python
   encoded_value = json.dumps(raw_value)
   ```
4. **anyOf Option A still alive?** Re-POST the *old* `{"symphony": {"raw_value": raw_value}}` to confirm it truly 400s today (sanity check that the drift is real and not intermittent), and try adding a top-level `"description"` inside the tree if the nested `description: missing` error persists.

For each probe, log the full status + first 300 chars of the response body. The first 200 wins; capture that exact request body + the 200 response as the canonical fixture (provenance = captured-from-producer). If ALL of 1-3 still 400 with a malli error, quote the new `humanized` error verbatim back to the PM — it will name the next missing/disallowed key and narrow the search further.

**Note (project rule):** the `/backtest` path uses live Composer credentials and the inline tree — this is advisory/off-execution-path (the client + its callers `strategy_builder_engine`, `asset_swap_engine`, `logic_change_engine` never touch `LIVE_EXECUTION`), so probing it is low-risk operationally, but it IS a live third-party call — space at 1 req/sec and keep probe count small.

---

## Sources

| # | URL / artifact | Access Date | Tier | Method | Description |
|---|---|---|---|---|---|
| 1 | Live malli 400 body (PM-captured) | 2026-06-18 | 1 | Observed network | Decisive: `raw_value` "disallowed key", `encoding_type`/`encoded_value` "missing required key" |
| 2 | https://api.composer.trade/docs/swagger.json (via r.jina.ai) | 2026-06-18 | 1 | Documented | `/backtest` symphony anyOf: raw_value OR (encoded_value+encoding_type); `encoding_type` enum = ["transit_json"] only; top-level required list; raw_value listed first; no encoded_value example present |
| 3 | https://api.composer.trade/docs/index.html (Redocly) | 2026-06-18 | 1 | Documented | API title "Welcome to the Composer API!", version 1.0.0; render still shows raw_value example (stale vs live malli) |
| 4 | https://help.composer.trade/article/236-getting-started-with-your-composer-api | 2026-06-18 | 1 | Documented | Auth headers, base URL, 1 req/s (500 exception = saved-id path only); last-updated 2025-07-16 — UNCHANGED |
| 5 | https://pypi.org/pypi/composer-trade-mcp/json | 2026-06-18 | 1 | Documented | Official MCP server v0.1.7, released 2025-07-14; sdist + wheel URLs; repo github.com/invest-composer/composer-trade-mcp (anon-inaccessible) |
| 6 | https://github.com/androslee/compose_symphony_parser | 2026-06-18 | 3 | Community | Symphonies are EDN ("json formatted edn_encoded"), captured from backtest network traffic; confirms Clojure/EDN native format |
| 7 | https://github.com/cognitect/transit-python + transit-python2 fork | 2026-06-18 | 1 | Documented | transit-json mechanics: keywords → `~:` prefix; `Writer(io,"json").write(v)`; original archived 2023-06-03, maintained fork = transit-python2 |
| 8 | composer_backtest_client.py:287-295 (this repo) | 2026-06-18 | 1 (internal) | Codebase | The exact failing body `{"symphony": {"raw_value": raw_value}, ...}` |
| 9 | feature-plans/ai-advisor-composer-api-research.md:65-99 (this repo) | 2026-06-18 | 1 (internal) | Documented | Prior (2026-05-31) raw_value schema — now drifted/stale |

---

### Schema diff (`as of` markers)

| Field | As of 2026-05-31 (working) | As of 2026-06-18 (live) |
|---|---|---|
| `symphony.raw_value` | required, valid | **disallowed key** (live malli) — still in published swagger anyOf (stale) |
| `symphony.encoding_type` | absent | **required**, enum `["transit_json"]` |
| `symphony.encoded_value` | absent | **required**, type string (transit-json encoding of the tree) |
| top-level required (`capital`, fees, `slippage_percent`, `broker`, `symphony`) | unchanged | unchanged |
| auth / rate-limit | unchanged | unchanged |
