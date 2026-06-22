# Feature Plan: Live Dashboard Metrics

**Status:** ready
**Branch:** fix/live-dashboard-metrics
**Diagnosis sources:** `.claude/live-dashboard-render-sweep.md`, `.claude/live-dashboard-diagnosis.md`

---

## Summary

The live droplet dashboard shows blank/zero/stale values on six surfaces. Root causes
are: (a) post-mortem-gated routes that cannot show data until EOD files exist, (b) a
missing `base_dir` argument on the history route, (c) an `None`→`0.0%` template coercion
on the MDD bot column, and (d) a broken sources-block template that reads a non-existent
top-level key. This plan wires every affected surface to its LIVE data source
(`exit_triggers`, `shadow_history`, `bot_state`) so the dashboard is meaningful on day one.

---

## Acceptance Criteria

### AC-1 — Guard-Alpha $-Saved Panel (`/api/guard-alpha-summary`)

The route CURRENTLY reads `post_mortem_*.json` files. Fix: add a live intraday path:

- `guard_event_count` reads `exit_triggers` table (row count) — updates as exits are recorded.
- `cumulative_saved_dollars` is computed from `exit_triggers` × `shadow_history`: for each
  exit trigger, `saved_dollars = (if_held_return_at_eod - shadow_return_at_trigger) * position_basis`.
  Because EOD return is unavailable intraday, use the CURRENT `shadow_history.current_return`
  vs `exit_triggers.at_return` × `bot_state.position_value` as an intraday snapshot.
  Formula: `saved = (current_return - at_return) / 100 * position_value` where
  `current_return` is the latest `shadow_history` row's `current_return` for that symphony,
  `at_return` is `exit_triggers.at_return`, and `position_value` comes from `bot_state`.
- Result is labeled "intraday estimate — updates live", not "snapshot-time basis".
- When no exit_triggers exist: returns `guard_event_count=0`, `cumulative_saved_dollars=0.0`,
  `basis_label="no guard events yet"` (honest empty-state, existing test still passes).
- Golden-fixture test pins the math: edge cases include negative divergence (held moves
  against the exit), zero position_value, missing symphony in shadow_history.
- Post-mortem files (when they exist EOD) continue to take precedence for
  `cumulative_saved_dollars` (add a `source` field: `"exit_triggers_intraday"` or
  `"post_mortem_eod"` so callers know the basis).

### AC-2 — Performance Tab (`/api/performance` + `/api/performance/symphonies`)

- Routes currently return empty when `post_mortems/` is missing.
- Fix: add a `shadow_history` fallback path. When `get_history_with_cache_invalidation`
  returns empty (0 files), compute `live_returns` and `shadow_returns` directly from
  `shadow_history` via the existing `get_portfolio_bot_and_held_daily_returns` function.
- With 1 day of data: `observation_count >= 1`, `insufficient_history=True` (still honest
  about insufficient history for quantstats), `dates/live_returns/shadow_returns` non-empty.
- No quantstats metrics computed for <2 days (existing floor preserved).
- The JS template gracefully handles `insufficient_history=True` by showing the available
  data points without the metrics table. Confirm this behavior without regression.

### AC-3 — History Tab (`/api/history/<days>`)

- **Bug:** `app.py:2452` calls `analytics.get_history_summary(days=days)` without
  `base_dir=analytics._POST_MORTEMS_DIR`. The function defaults to `base_dir="."`,
  finding no files in the process CWD.
- **Fix:** `analytics.get_history_summary(days=days, base_dir=analytics._POST_MORTEMS_DIR)`.
- This is a one-line fix. The AST-based `test_history_base_dir_pinned.py` only covers
  `get_history_with_cache_invalidation`, not `get_history_summary`. A new AST test must
  assert that every call to `analytics.get_history_summary` in `app.py` passes
  `base_dir=analytics._POST_MORTEMS_DIR`.
- `todays_exits` in the response: source from `exit_triggers` table (already in DB on the
  droplet) so today's exits appear even before EOD post-mortem is written.

### AC-4 — Hero Guard-Alpha Strip (`/api/strip/<window>`)

- Currently returns `guard_alpha=0.0` and `insufficient_history=True` when
  `shadow_history` has only 1 distinct trading_day (because
  `_get_windowed_divergence_trajectory` requires >= 2 rows for a trajectory).
- Fix: when the window has >= 1 row but < 2 distinct trading_days, compute a
  SINGLE-DAY intraday guard alpha for each triggered symphony using the intraday
  formula: `intraday_alpha = at_return - current_return` (from `exit_triggers` +
  latest `shadow_history`), then value-weight across triggered symphonies.
- Report this as `guard_alpha=<value>`, `insufficient_history=True` (still honest that
  the windowed trajectory cannot be computed), `intraday_only=True` (new field, additive).
- When `insufficient_history=True` AND `intraday_only=True`, the JS should show
  "Today only" label instead of "+0.00%". Template/JS change required.
- Non-triggered symphonies: excluded from intraday guard-alpha (no exit, no divergence).

### AC-5 — AI Advisor Overview News Sources (`ai_advisor.html:954`)

The template reads `_raw.get('sources', [])` — no top-level `sources` key exists in the
MARKET_PRISM `raw_response`. Sources are stored in `per_lens_digest[lens]['sources']` as
plain strings (data-provider citations), not article-object dicts.

Two sub-fixes:

**AC-5a — Template fix:** Replace the broken `_sources = _raw.get('sources', [])` block
with a per-lens source aggregation:

```
{% set _all_sources = [] %}
{% for lens_name, lens_entry in _per_lens.items() %}
  {% for src_str in (lens_entry.get('sources', []) if lens_entry is mapping else []) %}
    {% set _ = _all_sources.append({'citation': src_str, 'lens': lens_name}) %}
  {% endfor %}
{% endfor %}
```

Render each `_all_sources` item as a `<li>` with the citation text + lens tag. No
`href` links (citations are data-provider names, not URLs). Drop the `.get('url')` /
`.get('title')` calls entirely — they raise `AttributeError` on strings.

**AC-5b — Article corpus persistence:** The `news_corpus.build_news_corpus()` function
produces `TOP_K=25` ranked articles with `{url, title, published, topics, score}`. These
are currently consumed by `ai_advisor._build_sentiment_section` but NOT persisted into
the MARKET_PRISM `raw_response` (only the GDELT tone score is stored). Fix: persist the
top-25 article corpus into the sentiment lens's `raw_response` field (in
`ai_advisor._build_sentiment_section`) so the Prism synthesizer can include it. Then the
council's `raw_response` can carry `article_corpus` in the `per_lens_digest.sentiment`
entry. Template reads this from `per_lens_digest.sentiment.article_corpus` when present
and renders clickable article links for the sentiment lens.

Note: article corpus persistence is additive to `per_lens_digest.sentiment` — existing
string `sources` are preserved; `article_corpus` is a new optional key. The template
shows the article corpus when present, falls back to per-lens string citations when not.

### AC-6 — MDD Bot Column (`index.html:1121`)

- `_mdd.dry_run` is `None` when `shadow_history` has < 2 distinct trading days
  (trajectory function returns `None`).
- The template coerces `None` → `0.0` via `| float`, rendering "Bot 0.0%" — misleading.
- Fix: pass a `mdd_bot_available` flag from the route context or change the template
  to render `--` when `mdd_d.get("dry_run")` is `None`.
- Template fix only (no logic change in `analytics.py`): `{% if mdd_d.get("dry_run") is not none %}{{ "%+.1f"|format(mdd_bot) }}%{% else %}--{% endif %}`.
- The "insufficient history (<30d)" alpha-badge suppression (already working) is preserved.

### AC-7 — Accuracy + Freshness (cross-cutting property test)

- Property test: `shadow_history` data is the freshest available (last row within
  last 5 minutes during market hours).
- Property test: no surface that touches `shadow_history` returns `None` or `0.0`
  when there is at least 1 row for a triggered symphony.
- No regression: surfaces that already work (main cards today-change, guard-alpha
  banners, Prism chip/rationale, Asset Swaps/Logic/Chat/Strategy Builder) have
  their GREEN tests protected by this plan (no test modifications that weaken them).

### AC-8 — Render-Verified (visual gate)

- `ld-ux` confirms each surface against a copy of the live droplet DB (auth-disabled
  local instance) — tests-green is necessary, not sufficient.
- This AC is satisfied by `ld-ux` sign-off AFTER `ld-impl` goes GREEN.

---

## Architecture

### Data Sources (per surface)

| Surface | Current (broken) source | Fixed source |
|---------|------------------------|--------------|
| $-saved panel | `post_mortem_*.json` | `exit_triggers` × `shadow_history` (intraday); post-mortem when present (EOD) |
| Performance tab series | `post_mortem_*.json` | `shadow_history` via `get_portfolio_bot_and_held_daily_returns` fallback |
| History tab | `get_history_summary` with `base_dir="."` | same function with `base_dir=analytics._POST_MORTEMS_DIR` |
| History tab todays_exits | missing | `exit_triggers` table |
| Hero strip | trajectory requires ≥2 days | intraday single-day formula when 1 day only |
| News sources | `_raw.get('sources', [])` → empty | `per_lens_digest[lens]['sources']` aggregation |
| MDD bot column | `None | float` → `0.0%` | explicit `None` guard → `--` |

### Files Changed (by `ld-impl`)

- `app.py:2452` — add `base_dir=analytics._POST_MORTEMS_DIR`
- `app.py:2177` `guard_alpha_summary()` — add live `exit_triggers` path
- `app.py:2485` `api_performance()` — add `shadow_history` fallback series
- `app.py:2583` `api_performance_symphonies()` — same fallback
- `app.py` strip route — add single-day intraday guard_alpha path
- `templates/index.html:1121` — `None` guard for MDD bot
- `templates/ai_advisor.html:954` — per-lens source aggregation block
- `ai_advisor._build_sentiment_section` — persist article corpus into raw_response (AC-5b)

### Files Changed (by `ld-doc`)

- `docs/generated/` — update dashboard routes doc
- `DECISIONS.md` — record live-source data decisions
- `CLAUDE.md` key-files — update `app.py` entry with new route behaviors

---

## Edge Cases

- `exit_triggers` has rows but `shadow_history` has no rows for a given symphony: use
  `at_return` vs `at_return` = 0 divergence (conservative, not negative).
- `bot_state` has no `position_value` for a symphony: skip dollar calculation for that
  symphony; report `cumulative_saved_dollars` from what is available.
- `per_lens_digest` lens entry is a string (prose, not dict): `article_corpus` and
  `sources` fallback to empty list — no crash.
- `shadow_history` 1-day strip: only triggered symphonies participate in intraday
  guard-alpha; the `insufficient_history=True` flag propagates correctly.
- Performance tab with 0 shadow_history rows (daemon just started): `observation_count=0`,
  `insufficient_history=True`, empty series — identical behavior to current.

---

## Security Considerations

- All new routes remain read-only; no new keys added to `_SETTINGS_WRITE_ALLOWLIST`.
- No new network I/O on the execution path.
- Article corpus URLs in the template are rendered with `| e` (escaped); `rel="noopener noreferrer"` on outbound links.

---

## Testing Strategy

Tests written by `ld` (quant-test-writer) using `/tdd` skill:

1. **Golden-fixture tests** (`tests/fixtures/math/guard_alpha_intraday_saved.json`):
   pin the `(current_return - at_return) / 100 * position_value` math — adversarial
   edge cases (negative divergence, zero basis, missing symphony).

2. **AST guard** (`tests/app/test_history_base_dir_get_history_summary.py`):
   asserts every `analytics.get_history_summary` call in `app.py` passes
   `base_dir=analytics._POST_MORTEMS_DIR`.

3. **Route data-source tests** (`tests/app/test_live_dashboard_metrics.py`):
   - AC-1: `guard_alpha_summary` with mocked `exit_triggers` + `shadow_history` returns
     non-zero `guard_event_count` and positive `cumulative_saved_dollars`.
   - AC-2: `api_performance` with mocked `get_history_with_cache_invalidation` returning
     `{}` AND mocked `get_portfolio_bot_and_held_daily_returns` returning 1-day series
     → `observation_count >= 1`, non-empty `dates`.
   - AC-4: strip route with 1-day shadow_history → `intraday_only=True`,
     `guard_alpha` is a float (not 0.0 when exits exist), `insufficient_history=True`.
   - AC-6: MDD bot column template: when `_mdd.dry_run` is `None`, the rendered HTML
     contains `--` not `0.0%`.

4. **Template render tests** (`tests/app/test_news_sources_render.py`):
   - AC-5a: `ai_advisor` template with a MARKET_PRISM row whose `per_lens_digest`
     carries per-lens `sources` strings → rendered HTML contains the source citation
     text; does NOT contain raw JSON.

5. **Regression guard tests**: existing GREEN tests in `test_guard_alpha_summary_route.py`,
   `test_performance_routes.py`, `test_history_base_dir_pinned.py` must remain GREEN.

---

## Scope Boundaries

IN SCOPE:
- The six broken surfaces listed in the diagnosis docs.
- One-line `base_dir` fix for `get_history`.
- Template-only MDD fix (`--` vs `0.0%`).
- Per-lens sources aggregation in template.
- Intraday `exit_triggers` path for `guard_alpha_summary`.
- Single-day fallback for strip.
- `shadow_history` series fallback for performance tab.
- Article corpus persistence in `_build_sentiment_section` → `per_lens_digest.sentiment.article_corpus`.

OUT OF SCOPE:
- Rewriting the EOD post-mortem pipeline.
- Changing `analytics._get_windowed_divergence_trajectory` (existing logic preserved).
- Historical post-mortem backfill / deployment.
- Composer or Alpaca API changes.
- Any change to `alpha_bot_execution.py` (execution-path freeze).
- Autotuner or math_engine changes.
