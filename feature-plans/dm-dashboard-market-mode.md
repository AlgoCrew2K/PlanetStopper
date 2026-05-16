# Feature: DM — Dashboard Market-Mode Rendering
Status: ready (queued behind M1F; small surface; no panel-validation required)
Created: 2026-05-16

## Summary

Make the dashboard market-state aware: render live during market hours (09:30-16:00 ET on trading days), freeze visuals at market close, and show the frozen snapshot until the next market open. The companion to M1F. Sidesteps the post-trigger Composer-API ambiguity for M1F.7's `shadow_hwm = max(current_return)` formula by removing operator confusion about post-close values — outside market hours, the dashboard explicitly displays a frozen snapshot rather than whatever Composer happens to be returning. The engine continues running 24/7 and continues writing `shadow_history` rows (per the existing data-vs-action split's data phase) — the freeze is purely a dashboard rendering concern.

## Acceptance Criteria

### DM.1 — Market-state detection

- **AC-DM.1.1**: New helper `get_market_state(current_et: datetime) -> str` returning one of `{"open", "closed_frozen", "pre_market"}` based on:
  - `"open"`: current ET time is a weekday AND `09:30 <= time < 16:00`
  - `"closed_frozen"`: weekday after 16:00, OR weekend, OR holiday (use existing US-equity calendar logic if present; otherwise weekday-only check + retain holiday gap as a known v1 limitation)
  - `"pre_market"`: weekday before 09:30 (treated as `closed_frozen` for v1 — same frozen-snapshot rendering)
- **AC-DM.1.2**: Named constants `REAL_MARKET_OPEN_TIME = time(9, 30)` and `MARKET_CLOSE_TIME = time(16, 0)` with source comments. NOT bare literals.
- **AC-DM.1.3**: Existing `EXECUTION_START_TIME` env var is separate from these — used for the action-phase gate (per engine-correctness E1's data-vs-action split), NOT the dashboard market-state determination. The dashboard shows live state from 09:30 ET regardless of when `EXECUTION_START_TIME` allows actions.

### DM.2 — Snapshot capture at market close

- **AC-DM.2.1**: New `bot_state["last_market_close_snapshot"]` field populated at the EOD post-mortem branch (`alpha_bot_execution.py` post-mortem) on each trading day. Schema: `{"trading_day": "YYYY-MM-DD", "captured_at_et": "HH:MM:SS ET", "data_as_of": <same content as /api/state.state at capture time>, "portfolio_strip": {...same shape as live...}, "shadow_divergence": {...same shape...}, "accounts_map": {...same shape...}}`.
- **AC-DM.2.2**: Snapshot is captured ONCE per trading day at EOD post-mortem fire. Subsequent cycles outside market hours do NOT overwrite the snapshot — the EOD value is the frozen authoritative state for that trading day.
- **AC-DM.2.3**: Snapshot is persisted in the existing state DB (`bot_state` JSON blob — additive field; no schema migration needed since `bot_state` is a JSON column).
- **AC-DM.2.4**: On daemon restart outside market hours, the dashboard reads from `last_market_close_snapshot` — does NOT trigger an immediate engine cycle to recompute state.

### DM.3 — Conditional dashboard rendering

- **AC-DM.3.1**: `/api/state` route augmented with a top-level `market_state` field (`"open"` | `"closed_frozen"` | `"pre_market"`) computed from `get_market_state(datetime.now(ET))` at request time. Also includes a top-level `frozen_at` field (None when market open; ISO timestamp when frozen) sourced from `last_market_close_snapshot.captured_at_et` + `trading_day`.
- **AC-DM.3.2**: When `market_state == "open"`: `/api/state` returns live state (current behavior — read from current `bot_state` snapshot at request time).
- **AC-DM.3.3**: When `market_state == "closed_frozen"` (or `pre_market`): `/api/state` returns the `last_market_close_snapshot` content for `state` / `portfolio_strip` / `shadow_divergence` / `accounts_map`. The `data_as_of` field shows the snapshot's `captured_at_et`, NOT current time.
- **AC-DM.3.4**: When the dashboard polls `/api/state` outside market hours and NO snapshot exists yet (fresh deploy before first market close), the route returns `market_state: "closed_frozen"` + `frozen_at: null` + an empty/null `state` payload with a clear `notice` field: `"No closing snapshot yet — waiting for first market close at 16:00 ET."`

### DM.4 — Dashboard banner indicator

- **AC-DM.4.1**: New banner (or header element) on `templates/index.html` showing:
  - `"Market Open"` (green/active styling) when `market_state == "open"`
  - `"Market Closed — frozen at HH:MM ET YYYY-MM-DD"` (muted/slate styling) when `market_state == "closed_frozen"` (with the snapshot's `frozen_at` rendered)
  - `"Pre-Market — frozen at HH:MM ET YYYY-MM-DD"` when `market_state == "pre_market"`
- **AC-DM.4.2**: Banner positioned in the established dashboard stack: header area, above the portfolio strip. Distinguishable from the cycleat-fix staleness badge (which is per-cycle freshness, not per-day) and the future V3 fleet banner.
- **AC-DM.4.3**: Banner clicking is not interactive in v1 (read-only).
- **AC-DM.4.4**: When `state` is empty and `notice` is present (first-deploy case), banner shows the notice text in its slate styling.

### DM.5 — Engine cycles continue running 24/7

- **AC-DM.5.1**: The engine's data phase continues to run every cycle 24/7 (per the data-vs-action split). DM does NOT change cycle cadence.
- **AC-DM.5.2**: `shadow_history` rows continue to be written outside market hours (per M1F.2 + PA-M1F-15). The dashboard ignores them when rendering frozen state, but they remain queryable via `/api/triggers` and `/api/state` historical endpoints.
- **AC-DM.5.3**: All other telemetry (H1 exit_triggers, post-mortem JSON) continues to fire outside market hours per existing semantics. DM is purely a dashboard rendering concern.

### DM.6 — Live verification post-merge

- **AC-DM.6.1**: After M1F + DM merge, operator can confirm:
  - During market hours: dashboard shows live values updating per polling cycle (current behavior preserved)
  - At 16:00 ET market close: snapshot captured; banner switches to "Market Closed — frozen at 16:00 ET YYYY-MM-DD"
  - 16:01 ET onwards (until 09:30 ET next trading day): dashboard values do NOT change visually; live engine cycles still run (verifiable via tail of `alphabot_daemon.log`)
  - Next 09:30 ET: banner switches back to "Market Open"; values resume updating live

## Architecture

| Surface | Files touched |
|---------|---------------|
| Market-state detection | `math_engine.py` or new `market_calendar.py` — `get_market_state` pure helper |
| Engine snapshot capture | `alpha_bot_execution.py` EOD post-mortem branch — populate `bot_state["last_market_close_snapshot"]` |
| State persistence | `database.py` — `bot_state` JSON blob already exists; additive field, no migration needed |
| Conditional API | `app.py /api/state` — branch on `market_state`; return live OR snapshot |
| Dashboard banner | `templates/index.html` — new market-state banner above portfolio strip |
| Tests | `tests/dashboard/test_market_mode.py` + `tests/fixtures/dashboard/market_mode/*.json` |

**Team composition**: Trio
- `quant-test-writer` (lead)
- `flask-dashboard-specialist` (route + banner + snapshot serialization)
- `quant-code-reviewer` (discipline gate)

(No risk-engine-specialist or sqlite-specialist needed: no math layer changes, no schema migration since `bot_state` is already a JSON blob.)

## Edge Cases

- **Daemon restart at exactly 16:00 ET**: race between EOD post-mortem fire and snapshot capture. EOD post-mortem is the single capture point; if the daemon was down at 16:00, the next post-mortem fire on the next trading day produces the snapshot for THAT day, leaving yesterday's frozen snapshot as the last available. Document.
- **Trading half-days** (Black Friday, day before Independence Day, etc.): v1 uses fixed `MARKET_CLOSE_TIME = 16:00`. Half-day handling is a v2 concern; v1 may show "Market Open" for ~3 hours after the actual half-day close. Document as a known v1 limitation.
- **Market holidays**: v1 weekday-only check. Treating holidays as "Market Open" until 16:00 is a v1 limitation; v2 integrates the US-equity calendar.
- **Daemon down across market close**: no snapshot captured for that trading day. The previous trading day's snapshot remains the dashboard display. Banner shows the OLD `frozen_at` timestamp — clear signal to the operator that EOD didn't fire.
- **Snapshot field absent on existing deployments**: backward compat — `bot_state.get("last_market_close_snapshot")` returns None; route returns `notice` instead of crashing.
- **Daylight Saving boundary**: `get_market_state` uses `zoneinfo` to handle ET correctly across DST transitions. Pin via fixture.

## Security Considerations

- No new external API surfaces.
- `/api/state` extension is read-only; no mutation paths.
- No XSS: market-state banner renders controlled strings (state literals + timestamps); Jinja autoescape applies.
- No new Composer/Alpaca/Anthropic auth surfaces.

## Testing Strategy

- **TDD via real Trio Agent Team** (project hard requirement).
- **PA-18 fixture provenance — strict**: fixtures in `tests/fixtures/dashboard/market_mode/` with provenance comments (e.g., DST transition day, half-day, weekend, fresh-deploy-no-snapshot).
- **ZERO live calls** in test tier.
- **Tests against the real /api/state response shape** (M2-class lesson).
- **PA-19 explicit reviewer APPROVE message via SendMessage required before merge**.
- **Live verification mandatory post-merge** — operator confirms the four scenarios in AC-DM.6.1.

## Decisions

| Decision | Resolution |
|----------|-----------|
| Sequenced after M1F | M1F + DM both touch `alpha_bot_execution.py` EOD branch + `app.py /api/state` + `templates/index.html`; M1F first because it's the engine instrumentation; DM layers rendering on top. |
| Snapshot storage | Additive field on the existing `bot_state` JSON blob — no schema migration. |
| Snapshot scope | Full state-relevant payload (state, portfolio_strip, shadow_divergence, accounts_map). Conservative — easier to render frozen state if everything's captured. |
| Half-day / holiday handling | v1 limitation; banner shows "Market Open" for ~3 hours past actual half-day close. v2 integrates US-equity calendar. |
| EXECUTION_START_TIME vs market_state | Separate concerns. EXECUTION_START_TIME gates exit-monitoring actions (10:30 default). Dashboard shows live state from 09:30 regardless — operator sees data starting at real market open. |
| Frozen state during fresh deploy | Returns a clear `notice` field rather than empty render or fall-back to live. |

## Scope Boundaries

**IN:**
- All 6 AC groups (DM.1 through DM.6).
- Market-state-aware `/api/state`.
- Snapshot capture at EOD post-mortem.
- Dashboard banner indicating market state.

**OUT:**
- Half-day / holiday calendar integration (v2).
- Time-zone selection for non-ET operators (v2; ET hardcoded).
- Replaying historical snapshots (operator can query `bot_state` JSON history if desired, but not surfaced).
- Per-account separate snapshots (single global snapshot; aggregates across accounts via existing portfolio strip semantics).
- Pre-market intraday rendering (treated identically to closed_frozen for v1).

## Dependencies

- M1F must merge first (shared `/api/state` extension + `alpha_bot_execution.py` EOD branch + dashboard templates).
- After DM merges, OD-4 (M1F.7 Composer-API ambiguity) is fully resolved — `shadow_hwm = max(current_return)` is defensible under any Composer post-trigger behavior because the dashboard freezes at close and the runtime alert is no longer needed.

## Hand-off

Plan saved. Sequenced AFTER M1F merges. Operator approves dispatch when ready.
