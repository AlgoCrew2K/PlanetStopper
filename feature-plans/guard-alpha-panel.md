# Feature: Guard Alpha Value Panel
Status: ready
Created: 2026-06-19

## ⚠️ SCOPE NARROWED (PM, post-verification 2026-06-19)
ga-implementer + PM verification found **AC-2 (per-card running guard-alpha for untriggered symphonies) is ALREADY BUILT** on origin/main: cards render `card_alpha = cr_bot − cr_held` (the divergence gap, populated for ALL symphonies regardless of trigger — `app.py:937,1016`), covered by `tests/dashboard/test_card_guard_alpha_basis.py::test_card_cumulative_alpha_reconciles_with_divergence_gap`. gax-scope's "Gap 1" conflated that running value with the post-trigger exit-snapshot verdict badge. **AC-2 is DROPPED from this cycle.** The sole genuinely-new surface is **AC-1/AC-4/AC-5/AC-6/AC-8: the `GET /api/guard-alpha-summary` route (cumulative $-saved aggregate) + a $-saved headline that consumes it.** Ignore AC-2/AC-3/AC-7's per-card + chart-reuse framing except as "don't rebuild what exists."

## Summary
A dashboard surface that QUANTIFIES Planet Stopper's value-add, filling two real gaps the existing UI does not cover (scoping: gax-scope 2026-06-19):
1. **Cumulative dollar-saved headline** — the "$X saved across N early exits" number. The per-exit `saved_dollars` / `saved_pct_guard_alpha` exist only in per-day `post_mortems/post_mortem_YYYY-MM-DD.json` files (`reporting.py:68-71,99-100`); they are NOT aggregated anywhere on the dashboard.
2. **Per-card running guard alpha for UNTRIGGERED symphonies** — today a card's `guard_alpha` is populated only at trigger moment (`alpha_bot_execution.py:1192`); untriggered cards show no live guard-alpha (`templates/index.html:1082` is conditioned on `'guard_alpha' in sym`).

The existing hero Bot-vs-Held cumulative chart (`templates/index.html:829`, `static/index.js:69`) + comparison rows (`templates/index.html:887-903`) + `/api/strip/<window>` windowed portfolio guard-alpha (`app.py:2127`) are REUSED, NOT rebuilt. All Guard-Alpha math already lives in `analytics.py` (`get_symphony_cumulative_return:754`, `compute_windowed_symphony_guard_alpha:1396`, `compute_windowed_portfolio_strip:1422`). Read-only, advisory; no new DB tables/migrations.

## Acceptance Criteria
- [ ] AC-1: A "Guard Alpha" panel renders **cumulative dollar-saved** (Σ `saved_dollars` across `post_mortems/*.json`) + **guard-event count** + the **date range covered**, with the basis labeled ("snapshot-time, since <earliest post_mortem date>").
- [ ] AC-2: Every symphony card shows a **running guard-alpha %** for UNTRIGGERED symphonies (live shadow-vs-held divergence from the analytics running computation), not just post-trigger. Triggered-card behavior unchanged.
- [ ] AC-3: The panel reuses `/api/strip/<window>`'s portfolio `guard_alpha` for any windowed figure; the existing hero `cum-chart` is NOT duplicated or rebuilt.
- [ ] AC-4: Dollar-saved data is served via a NEW read-only route `GET /api/guard-alpha-summary` (SQLite `mode=ro` per the established `analytics.py:1142` pattern); no engine re-run; no `LIVE_EXECUTION` interaction; NOT added to `_SETTINGS_WRITE_ALLOWLIST`.
- [ ] AC-5: **Honest empty-state** — no `post_mortem` files / zero guard events → "No guard events yet" (or $0 / 0 events), never a crash, NaN, or `None` leak into the template.
- [ ] AC-6: **Malformed/missing post_mortem file resilience** — a corrupt or unreadable `post_mortem_*.json` is skipped + logged (no secret leak), never crashes the route or the dashboard.
- [ ] AC-7: If a NEW Chart.js visualization is added, it uses the **active CDN Chart.js version's** API (the CDN load at `templates/index.html:11` is unpinned and the codebase mixes v2/v3 syntax — the implementer confirms the active major version before using `scales`/options). Prefer reusing the existing chart; add a new canvas only if the panel genuinely needs one.
- [ ] AC-8: The new route + panel sit behind the existing auth gate (DE-AUTH-001) like all routes; GET, read-only; the per-card running guard-alpha population does not alter any write path or the execution path.

## Architecture
- **`app.py`** — new `GET /api/guard-alpha-summary` route: reads + aggregates `post_mortems/*.json` (`saved_dollars`, `saved_pct_guard_alpha`, dates) and the guard-event count (from `database.get_triggers()` / `exit_triggers`); returns `{cumulative_saved_dollars, guard_event_count, date_range, ...}` JSON. Thin aggregator; all compute delegates to `analytics.py`. mode=ro / no writes.
- **`analytics.py`** — add a `get_guard_alpha_dollar_summary()` aggregator (Σ over post_mortem JSON, bounded glob of `post_mortems/`, skip-on-error) if not cleanly expressible in the route; reuse existing running-guard-alpha functions for the per-card value.
- **Per-card running guard alpha** — in `get_api_state_dict()` (the state dict feeding the cards), populate `guard_alpha` for untriggered symphonies from the analytics running computation (`compute_windowed_symphony_guard_alpha` / `get_symphony_cumulative_return`), epoch-aware (analytics is already `position_epoch`-scoped — `migrations/015`). Do not chain across position re-entries.
- **`templates/index.html`** — panel markup (Option A: extend `.hero-section` below the comparison rows at `:887`, OR a dedicated `<section>` between hero and the card grid). Reuse the existing light card-UI CSS; no dark/foreign theme.
- **`static/index.js`** — fetch `/api/guard-alpha-summary` + render the headline; Chart.js init only if a new canvas is added (else reuse the hero chart).

## Edge Cases
- No `post_mortem` files yet → $0 saved / 0 events, honest empty-state (AC-5).
- Malformed/partial `post_mortem_*.json` → skip + log, continue (AC-6).
- Untriggered symphony with insufficient `shadow_history` → guard_alpha `None`/"—" in the card, never NaN.
- `shadow_history` retention (`database.prune_old_shadow_history`, `database.py:2916`) bounds any "all-time" figure → label the covered range honestly, don't imply true lifetime if pruned.
- Position-epoch correctness — running guard-alpha must not chain across re-entries (analytics already epoch-aware; the test must guard this).
- Dollar-saved is snapshot-time basis (post_mortem captures `current_value × saved_pct` at exit, `reporting.py:71`) — labeled as such, NOT presented as mark-to-market.

## Security Considerations
- Read-only SQLite (`mode=ro`); no writes; no engine re-run (Architecture constraint #5).
- `post_mortem` file reads: fixed `post_mortems/` dir, no user input in the path → no traversal; bounded glob.
- No `LIVE_EXECUTION`/credential interaction; route sits behind the auth gate; not in the settings allowlist.
- No secrets in the JSON response or logs (a malformed-file log names the file, not its contents).

## Testing Strategy
- **Route tests** (`tests/app/`): `/api/guard-alpha-summary` aggregates fixture `post_mortem` JSON + `exit_triggers` correctly; **assertions derive from fixture data, never hardcoded $ values** (feedback_no_hardcoded_test_values); empty-state (no files → $0/0); malformed-file resilience (corrupt JSON → skipped, route 200).
- **State-dict test**: `get_api_state_dict()` populates a running `guard_alpha` for an untriggered fixture symphony (from fixture `shadow_history`); epoch-correctness (no chaining across re-entries); triggered-symphony value unchanged.
- **Read-only boundary**: assert the route makes no DB writes (mode=ro) / no engine call.
- **ux visual gate** (mandatory — new UI): ux-expert renders the panel on a running instance, confirms light card UI (no dark/off-theme), honest empty-state renders, no console errors.
- **PM live test**: real dashboard panel shows the cumulative-$-saved aggregate + per-card running guard alpha against live/fixture data.
- Use the `_isolate_db` + DB-sentinel fixtures (`tests/conftest.py`); the auth gate is disabled-by-default via `_disable_auth_for_tests`.

## Scope Boundaries
- **IN:** `/api/guard-alpha-summary` route + the dollar-saved aggregator; per-card running guard-alpha population in `get_api_state_dict()`; the panel markup + JS; empty-state + malformed-file resilience; the full test suite above.
- **OUT:** rebuilding the hero Bot-vs-Held chart or `/api/strip` (already exist — reuse); any new DB table/migration (none needed); fixing the `/perf-snapshot` skill schema mismatch (`would_have_held_until`/`position_size` don't exist — separate); mark-to-market dollar recomputation (use post_mortem snapshot-time, labeled); the "historical analysis" roadmap item (separate); pinning the Chart.js CDN version repo-wide (a separate tech-debt item — this cycle only audits the active version before using options).

## Decisions ([PM-ASSUMED] per the project autonomy directive — scoping confirmed the existing build)
| Decision | Rationale |
|----------|-----------|
| Dollar-saved = Σ post_mortem `saved_dollars` (snapshot-time), labeled as such | The ONLY persisted $ source (scoping Gap 3); `shadow_history` has percentages only |
| Per-card running guard alpha from existing `analytics.py` running computation | Math already live + epoch-aware; fills Gap 1 without new compute |
| Reuse the existing hero chart + `/api/strip` | Already built (scoping §2); rebuilding = duplication |
| New read-only route for the $-saved aggregate | post_mortem JSON aggregation isn't currently exposed; keeps the panel a thin read-only consumer |
