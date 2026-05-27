> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Cycle 2 · Dashboard Rewrite · Code review @ 989073f

## SHA preamble
- HEAD reviewed: 989073f (feat(ui): GREEN cycle-2 — Dashboard rewrite + meta.portfolio)
- origin/main: 113e3d1cc654d8d26ac79d6351acdbc3ad8f730c (fresh-fetched 2026-05-19)
- merge-base: 113e3d1cc654d8d26ac79d6351acdbc3ad8f730c
- delta: 19 ahead, 0 behind

---

## Math safety
PASS — `math_engine.py`, `alpha_bot_execution.py`, `synthetic_history.py`, `autotuner.py` are untouched between merge-base and 989073f. No golden-fixture diff required.

## Live-trade boundary
PASS — No path in the cycle-2 diff reaches `liquidate`, `submit_order`, `place_order`, or `cancel_order` without a `live_mode` guard. The `cash-now-btn` in `templates/index.html` is a visual stub with no click handler attached in this cycle — documented in the test comment at `tests/ui/test_cycle_2_dashboard.py:294` ("Button is present but 0-click (no wiring in cycle 2)"). The `live_mode` flag check already gates `perform_account_liquidation` in the untouched `sell_account` route.

## Fixture provenance
PASS — `tests/ui/test_cycle_2_dashboard.py` fixtures (`_ACTIVE_SYM`, `_TRIGGERED_SYM`, `_STANDBY_SYM`, `_API_STATE_ACTIVE`) are injected via `patch.object(app_module, "get_api_state_dict", ...)`. The dashboard route is a renderer, not a parser of these fixtures. No circular parser+fixture co-design. All numeric assertions are structural (key presence, list lengths, element counts) — no hardcoded producer-computed financial values asserted as exact outputs.

## Schema reversibility
PASS — `database.py` is untouched in this diff. No migration required.

## Secrets hygiene
PASS — No hardcoded API keys, webhook URLs, or account UUIDs found in the diff. Webhook URL and credentials are read from `env_vars.get(...)` at runtime. Account UUIDs reach the HTML only as `uuid_short` (8-character prefix via `first_uuid[:8]` in `_build_meta`). `_MASKED_SETTINGS_KEYS` frozenset is preserved intact and correctly covers all credential fields.

## Engine constants
PASS — The `-999.0` sort sentinel literals in `app.py` `get_state()` pre-existed before this cycle (they appear in context lines, not added lines, in the sort-lambda diff). `_build_meta()` introduces no numeric literals; all values are derived from the passed `state_data` and `portfolio_strip` dicts. The `math_engine.py` gate is not triggered.

## Logging redaction
PASS — No new `logging.*` or `print()` calls echo Composer/Alpaca response bodies. The only new `print()` statements are continuation-line reformats of pre-existing f-strings (EOD trigger log messages). No verbatim API response bodies are logged.

## Dashboard side effects
PASS — `dashboard()` route calls: `get_api_state_dict()` (read-only aggregation), `database.load_state()` (read-only), `database.normalize_name()` (pure function), `database.get_symphony_strategy()` (read-only), `_build_meta()` (pure function). No engine function that mutates state is invoked. `templates/index.html` contains no DB reads, no Flask imports, no engine calls — pure Jinja template rendering from server-supplied variables.

## Cycle-2-specific checks

**API additive only:** PASS — All existing `/api/state` keys preserved across both response branches (frozen: `status`, `market_state`, `frozen_at`, `data_as_of`, `state`, `portfolio_strip`, `shadow_divergence`, `accounts_map`, `fleet_correlation_alert`, `html`, `_additive`; live: adds `next_run_seconds`, `execution_start_time`, `last_successful_cycle_at`). New additive keys in cycle-2: `bot_state` (mirrors `state` — safe duplicate), `live_mode` (frozen branch only — pre-existed in live branch), `meta.portfolio` sub-object. No existing keys removed or types changed.

**Token hygiene in index.html:** PASS — `grep -En "#[0-9a-fA-F]{3,8}"` on the cycle-2 GREEN `templates/index.html` returns no matches. All colors reference `var(--studio-*)` tokens. No Tailwind CDN (`cdn.tailwindcss.com` absent). `tokens.css` and `tweaks.js` loaded via `url_for()`.

**CDN note (NIT):** `templates/index.html:10` loads Chart.js from `cdn.jsdelivr.net`. This was present in the old dashboard and is a runtime dependency, not a design-system token. Does not violate the no-bare-hex constraint. NIT only — does not block.

**No raw account UUIDs in HTML:** PASS — `_build_meta` passes only `first_uuid[:8]` to templates as `meta.account.uuid_short`. No full UUIDs reach rendered HTML.

**meta.portfolio field provenance:** PASS — `portfolio_strip` dict is produced by existing `analytics.*` calls inside `get_state()` (read path, not execution path). `_build_meta` reads 10 sub-fields (`tc`, `tc_if_held`, `cr`, `cr_if_held`, `mdd`, `mdd_if_held`, `hist_dates`, `hist_bot`, `hist_held`, `data_as_of`) from the passed `portfolio_strip` dict with `.get()` defaults. All field names are additive to the existing `meta` object.

## NITs (non-blocking)
1. `app.py:_build_meta` reads `_dotenv_module.dotenv_values(ENV_FILE_PATH)` on every HTTP call — no caching. Pre-existing NIT from cycle-1, not introduced in cycle-2.
2. `templates/index.html:10` — Chart.js loaded from CDN (`cdn.jsdelivr.net/npm/chart.js`). Not a token hygiene violation but ideally vendored. Pre-existed in the old dashboard.
3. `templates/_chrome.html` — nav links use hardcoded paths (`/`, `/performance`, etc.) not `url_for()`. Pre-existing NIT from cycle-1.

## Verdict (APPROVE)
All 8 gates pass. Zero BLOCKs. Cycle-2 Dashboard @ 989073f is **APPROVED**.
