# Feature: Advisor Latency — Cache-Serve Market Lenses (DE-ADVISOR-LATENCY)
Status: ready
Created: 2026-06-29

## Summary
The AI Advisor hangs 6+ minutes when an operator selects a symphony because `ai_advisor.assemble_advisor_context` (`ai_advisor.py:1469`) makes 5 BLOCKING live lens fetches on every `/ai-advisor/suggest` request (technicals/sentiment/derivatives/macro/fundamentals, `ai_advisor.py:1545-1581`) — 17-29 sequential external API calls (SEC EDGAR companyfacts fan-out, ~8 RSS feeds, GDELT, FRED, Alpaca bars). These five lenses are **market-wide context** (sentiment/derivatives/macro have zero symphony coupling; technicals/fundamentals fold in live holdings but have proxy floors), so they do not need per-click freshness. This feature adds a **nightly producer that persists all 5 STRUCTURED lens payloads to a serve-able cache**, and changes `assemble_advisor_context` to **serve the market-wide lenses from that cache** (with an honest "market context as of <ts>" staleness stamp) instead of re-fetching live. Per-click work shrinks to symphony-specific logic + the autotune-evidence read + the Claude call. The "Optuna has not yet run" empty-state message is also reworded. Advisory-only, off the engine execution path.

**Verified premises (live droplet, read-only, 2026-06-29):** there is NO complete structured nightly cache today. `lens_warehouse.lens_snapshots` persists only `macro` + `sentiment` (incidental, from live requests — technicals/derivatives/fundamentals are never warehoused). The nightly `MARKET_PRISM.per_lens_digest` (council, id=11) is PROSE (`{available, summary:str, sources}`, `payload=None`) — NOT the structured `_build_*_section` payload the advisor prompt consumes. So the cache layer must be built; a naive "read existing cache" swap is not viable.

## Acceptance Criteria
- [ ] AC-1: When a fresh market-lens cache bundle exists, `assemble_advisor_context(scope="symphony", ...)` serves the 5 market-wide lenses from the cache and does NOT invoke ANY of the 5 live `_build_<lens>_section()` fetches. (Adversarial: assert each builder is NOT called when a fresh bundle is present.)
- [ ] AC-2: A nightly producer persists ONE cache record per run containing all 5 lenses' STRUCTURED payloads (the exact dict each `_build_<lens>_section()` returns) plus a `captured_at` UTC timestamp.
- [ ] AC-3: The served context carries an honest staleness indicator ("market context as of <captured_at>") that is surfaced to the advisor output/UI; the timestamp is HTML-escaped where rendered.
- [ ] AC-4: Freshness classification — a bundle within the freshness window (`_LENS_CACHE_MAX_AGE_HOURS`, default 36h) is labeled fresh; an older bundle is served WITH a clear "stale (as of <old ts>)" label (never silently presented as current).
- [ ] AC-5: Cold-start fallback — when NO cache record exists at all, the request degrades gracefully (either a single bounded live fetch OR an honest "market context unavailable" context) and NEVER silently hangs on the full 17-29-call fan-out as the default path. Decision recorded in the Decisions table; the test pins whichever is chosen.
- [ ] AC-6: Per-click symphony-specific work is preserved — symphony logic resolution, `database.get_latest_autotune_run` evidence, and the Claude `request_suggestions` call still run live and unchanged.
- [ ] AC-7: The served cache bundle is SHAPE-COMPATIBLE with what `request_suggestions` / the Claude prompt assembly consumes (structured `_build_*_section` shape), NOT the council's prose `per_lens_digest`. (Test pins the shape round-trip: producer output → cache → served context == live-fetch context shape.)
- [ ] AC-8: The "Optuna has not yet run for this symphony…" empty-state (`ai_advisor.py:1436`, `build_assessment_from_context`) is reworded to be clearer / less alarming while remaining accurate (still conveys: no walk-forward OOS evidence; Claude reasoning without OOS data).
- [ ] AC-9: No behavior change to the nightly council / Overview `MARKET_PRISM` producer or the `MARKET_PRISM_SOURCES` provenance row. The cache producer is additive.
- [ ] AC-10: Advisory-only / off the engine execution path; D-1 never-raises on the producer + accessor + serve path (a cache miss or producer error degrades honestly, never 500s `/ai-advisor`).

## Architecture
**New cache record (no schema migration):** persist ONE `advisor_observations` row with `advisor_role="MARKET_LENS_CACHE"` per nightly run. `raw_response` = `{"captured_at": <ISO UTC>, "lenses": {"technicals": <structured>, "sentiment": <structured>, "derivatives": <structured>, "macro": <structured>, "fundamentals": <structured>}}` where each `<structured>` is the exact dict `_build_<lens>_section()` returns. Reuses the existing append-only `advisor_observations` infra (freshness via `created_at` / the embedded `captured_at`). `MARKET_LENS_CACHE` is deliberately NOT added to `app.py` `_ADVISOR_ROLES` (keep it out of the Overview observations loop, exactly like `MARKET_PRISM_SOURCES`).

**New DB accessor (`database.py`):** `get_latest_market_lens_cache() -> dict | None` — `SELECT ... WHERE advisor_role='MARKET_LENS_CACHE' ORDER BY id DESC LIMIT 1`, deserialized; returns None when absent. Parameterized; read-only on the serve path.

**Nightly producer:** reuse the EXISTING nightly council path (`prism_scheduler`) where the 5 `_build_*_section()` builders ALREADY run inside `_patch_provenance` — capture those exact structured outputs and write the `MARKET_LENS_CACHE` row (one extra DB write, NO extra network, minimal compute). Implement as a small dedicated function (e.g. `advisors/lens_cache.py::refresh_market_lens_cache(sections: dict)` or `ai_advisor.persist_market_lens_cache(...)`) invoked from `prism_scheduler.main()`/`_patch_provenance` with the already-built sections. The producer is idempotent-safe (append-only; latest wins). NO new systemd timer (reuses `prism-council.timer`).

**Serve path (`ai_advisor.assemble_advisor_context`):** before the live-fetch block (lines 1545-1581), call `database.get_latest_market_lens_cache()`. If a record exists: build `context["lenses"]` from the cached structured payloads, compute `age = now - captured_at`, set `context["lens_data_as_of"] = captured_at` + a `lens_data_stale` bool (`age > _LENS_CACHE_MAX_AGE_HOURS`). Skip ALL 5 live `_build_*_section()` calls. If NO record: cold-start fallback per AC-5. Symphony-specific assembly (autotune evidence, etc.) is unchanged below.

**Empty-state reword (`ai_advisor.py:1436`):** replace the alarming "Optuna has not yet run…" string with a clearer, accurate message.

**Staleness surfacing:** thread `lens_data_as_of` / `lens_data_stale` into the suggest response JSON (`app.py:4145 ai_advisor_suggest`) and/or the advisor context so the UI can render "market context as of <ts>". Where rendered in a template, escape with `| e`.

## Design-System Mapping
Minimal UI surface — this is primarily a backend latency fix. The only new visible element is the "market context as of <ts>" staleness stamp on the AI Advisor suggest panel. If surfaced in the advisor SPA, it reuses the existing studio tokens / the same muted-meta styling used by the Overview "As of <ts>" stamp (`prism-as-of` pattern) — no raw hex, `var(--studio-ink-dim)` for the muted timestamp text. No new primitives required.

## Edge Cases
- No `MARKET_LENS_CACHE` row yet (cold start, before the first nightly run) → AC-5 fallback, never the silent 6-min hang.
- Stale bundle (missed nightly run / council failed) → serve with "stale as of <old ts>" label (AC-4), not silently.
- A single lens unavailable in the bundle (e.g. fundamentals degraded that night) → serve the available lenses; the missing one shows its honest-availability empty state, exactly as the live path does today.
- Malformed / unparseable cached `raw_response` → D-1: treat as cache miss, degrade per AC-5, never 500.
- `captured_at` missing or unparseable in an otherwise-present row → treat as stale (loud label), never crash.
- Timezone correctness — `captured_at` is UTC; age math and the "as of" stamp must be UTC-consistent (no naive/local drift).
- Concurrency: nightly producer append + request-time read are on separate processes (council vs Flask app); append-only + latest-wins avoids a write/read race.
- Producer runs but a builder raises → the producer persists the lenses that succeeded (honest-availability), never aborts the whole bundle.

## Security Considerations
- **Data exposure / injection:** cache content is internally produced (no user input); the read accessor is parameterized SQL. No new injection surface.
- **XSS:** the only client-rendered new value is the `captured_at` timestamp (server-generated). Escape with `| e` where templated; never `| safe`.
- **Auth:** `/ai-advisor/suggest` is already behind the global auth gate — no new unauthenticated surface.
- **DoS / abuse:** the change REDUCES external-call volume per request (cache-serve), shrinking the existing fan-out attack/abuse surface. Cold-start fallback must be bounded (AC-5) so a cache-absent state can't be weaponized into repeated 6-min fan-outs.
- **Resource:** removes the live multi-MB SEC EDGAR parse from the request path → memory-lighter. No memory-cap changes anywhere.

## Testing Strategy
- **Unit (`tests/ai_advisor/` + `tests/sqlite/` or equivalent):**
  - `database.get_latest_market_lens_cache()` returns the latest `MARKET_LENS_CACHE` row deserialized; None when absent; ignores other advisor roles.
  - Producer (`refresh_market_lens_cache` / `persist_market_lens_cache`) writes ONE row with all 5 structured payloads + `captured_at`; partial-availability (one lens degraded) still persists the rest (AC-2, edge).
  - **AC-1 adversarial (the core latency contract):** with a fresh cache present, `assemble_advisor_context` serves from cache and NONE of `_build_technicals_section/_build_sentiment_section/_build_derivatives_section/_build_macro_section/_build_fundamentals_section` are invoked (patch each with a fail-if-called spy / MagicMock assert_not_called).
  - AC-4 freshness classification (fresh vs stale boundary at `_LENS_CACHE_MAX_AGE_HOURS`).
  - AC-5 cold-start fallback (no row → the chosen degrade path; assert it does NOT run the full 5-builder fan-out as default).
  - AC-7 shape round-trip: producer output → cache → served `context["lenses"]` is shape-identical to the live-fetch `context["lenses"]` (NOT council prose). Assert structure/keys, not producer-computed values (no hardcoded values).
  - AC-8 empty-state reword: assert the new string is returned by `build_assessment_from_context` for the no-autotune case; assert the old alarming phrasing is gone.
  - AC-10 D-1: malformed `raw_response` / accessor error → cache miss, no exception escapes; `/ai-advisor/suggest` still 200.
- **Behavioral / live (PM, post-deploy on droplet):** measure the `/ai-advisor/suggest` wall-clock BEFORE (live-fetch, ~6 min) vs AFTER (cache-serve, seconds) on the real droplet via the test-client harness against a `/tmp` DB copy seeded with a `MARKET_LENS_CACHE` row; confirm the served context carries the real nightly lens data + an honest "as of" stamp; magnitude-check the latency drop. Confirm the nightly council run still produces MARKET_PRISM + SOURCES unchanged (AC-9) and additionally writes a MARKET_LENS_CACHE row.
- **No design-system computed-style tests** (no meaningful new UI beyond the reused meta-stamp).
- **Bounded `-n0` mem-capped local runs only; full cloud CI is the merge gate.**

## Decisions
| Decision | Rationale |
|----------|-----------|
| Reuse the existing nightly council path (no new systemd timer) | The 5 builders already run in `prism_scheduler._patch_provenance`; capturing their output adds one DB write, zero network, zero new ops/deploy risk. A dedicated `lens-cache.timer` was REJECTED (added droplet ops + can't be CI-tested). |
| `MARKET_LENS_CACHE` advisor_observations role (no schema migration) | Reuses proven append-only infra + freshness via `created_at`; consistent with how `MARKET_PRISM_SOURCES` is stored; avoids destructive/irreversible migration risk. |
| Cache the structured `_build_*_section()` output, NOT the council prose `per_lens_digest` | Verified on droplet: council digest is prose (`payload=None`); the advisor prompt consumes structured payload. Serving prose would silently degrade/break the advisor. |
| Serve-always-with-honest-age-stamp; live-fetch ONLY on total cache absence | Never silently hang (the operator's complaint); never silently present stale as current. |
| Keep market-wide (proxy-floor) lens context; do NOT personalize per-symphony holdings | The operator-confirmed framing is market-WIDE nightly context; the cached proxy-floor universe is the correct "market context as of <ts>". Per-symphony coupling stays out (scope). |
| Do NOT raise any memory cap; advisory-only off execution path | Project hard rule; the fix is memory-LIGHTER (removes live SEC parse). |

## Scope Boundaries
- **IN:** nightly `MARKET_LENS_CACHE` producer (reusing council builders' output); `database.get_latest_market_lens_cache()` accessor; `assemble_advisor_context` cache-serve for the 5 market-wide lenses with freshness/staleness handling + cold-start fallback; "market context as of <ts>" staleness surfacing; reword of the "Optuna not run" empty-state; D-1 never-raises on all new paths.
- **OUT:** any change to the council/Overview `MARKET_PRISM` / `MARKET_PRISM_SOURCES` producer behavior; per-symphony lens personalization (stays market-wide proxy-floor); the autotune/Optuna OOM fix (separate cycle DE-AUTOTUNE-OOM); parallelizing live fetches (explicitly rejected approach); any new systemd timer / schema migration / memory-cap change; engine-execution-path changes.
