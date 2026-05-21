# Cycle 4 · AI Advisor · Code review

## SHA preamble
- HEAD reviewed impl commit: 2edc0a1 (feat(ui): GREEN cycle-4 — Advisor parity)
- Ready-for-review marker: f70b904
- origin/main: 113e3d1cc654d8d26ac79d6351acdbc3ad8f730c (freshly fetched)
- merge-base: 113e3d1cc654d8d26ac79d6351acdbc3ad8f730c
- delta: 27 ahead, 0 behind
- Review scope: templates/ai_advisor.html, static/ai_advisor.js, ai_advisor.py (ConfigSuggestion extension), app.py (reformatting + active_route/meta passthrough)

---

## Verdict
APPROVED

---

## Math safety
PASS — zero diff lines in `alpha_bot_execution.py`, `math_engine.py`, `synthetic_history.py`, `autotuner.py`, `reporting.py`. Engine files confirmed untouched.

## Live-trade boundary
PASS — `ai_advisor_accept()` route (`app.py:1479`) writes config via `database.save_symphony_strategy()` only. No `is_live`, `submit_order`, `place_order`, `cancel_order`, or `liquidate` calls reachable from any new code path. The `onclick="getSuggestions()"` on the Run Advisor button (`ai_advisor.html:249`) is pre-existing. `acceptSuggestion()`/`rejectSuggestion()` JS functions POST to `/ai-advisor/accept` and `/ai-advisor/reject` respectively — config-write routes protected by C2 gates (allowlist, risk direction, OOS, locked-var), not live trade routes.

## Fixture provenance
PASS — no new test fixtures co-designed alongside the parser. `tests/ui/test_cycle_4_advisor.py` uses `patch.object` stubs against the API contract; no circular advisor+fixture co-design.

## Schema reversibility
N/A — no `database.py` changes. `ConfigSuggestion` Pydantic model gains three new fields with safe defaults (`oos_status="pending"`, `oos_reason=None`, `impact={"metric":"sharpe","delta":0.0}`). All existing `model_dump()` keys preserved; new fields are additive and backward-compatible.

## Secrets hygiene
PASS — zero API keys, webhook URLs, or raw account IDs in `ai_advisor.html` or `ai_advisor.js`. `app.py` changes are reformatting only; `_MASKED_SETTINGS_KEYS` frozenset unchanged.

## Engine constants
N/A — no `math_engine.py` changes.

## Logging redaction
PASS — three `print()` changes in `app.py` are pure Black/ruff reformatting of pre-existing lines. No new log statements echo Composer or Alpaca response bodies. No new `ai_advisor.py` log lines added.

## Dashboard side effects
PASS — `ai_advisor_tab()` route passes only `active_route="advisor"` and `meta=_build_meta({})` to `render_template()`. No engine mutations from any route added in this cycle.

---

## Open NITs (non-blocking)

- `templates/ai_advisor.html:14-19` — scrollbar thumb uses `var(--studio-scroll-thumb, #334155)` / `var(--studio-scroll-thumb-hover, #475569)` fallbacks. Same NIT pattern carried from cycle-1. Consistent; safe.
- `static/ai_advisor.js:130,135` — `acceptSuggestion` and `rejectSuggestion` buttons use `onclick=` injected via innerHTML. The symphony ID is run through `escHtml()` before interpolation which prevents XSS from user-supplied values. Safe as implemented; an event-delegation pattern would be cleaner but is not a security issue given the escaping.
- `ai_advisor.py:194` — `impact: dict = {"metric": "sharpe", "delta": 0.0}` uses a mutable default in a Pydantic model. Pydantic handles this safely (it copies the default), but if this were plain Python it would be a shared-state bug. No action required; noting for awareness.
