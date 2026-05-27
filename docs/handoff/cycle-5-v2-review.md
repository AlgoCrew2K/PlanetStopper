> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Cycle-5 Guard Alpha History v2 — Code Review

**Reviewed commit:** e77c579
**Incremental delta from:** 7309367 (v1, previously APPROVED)
**Merge-base (origin/main HEAD):** 113e3d1cc654d8d26ac79d6351acdbc3ad8f730c
**Branch position:** 46 ahead, 0 behind origin/main
**Review date:** 2026-05-19

Surfaces changed in this delta: `static/history.js`, `app.py` (`get_history` only).

---

## Math safety

PASS — `math_engine.py` and `alpha_bot_execution.py` untouched. No engine arithmetic changed.

## Live-trade boundary

PASS — zero `is_live`, `liquidate`, `submit_order`, `place_order`, `cancel_order` references
in the v2 delta. `get_history` remains a pure read path.

## Fixture provenance

N/A — no new fixture/parser pairs introduced.

## Schema reversibility

PASS — `database.py` untouched. `todays_exits` is a new additive JSON field in the
`/api/history/<days>` response. No existing keys removed or type-changed. No migration
needed (file-based data source, not SQLite).

## Secrets hygiene

PASS — no hardcoded credentials, webhook URLs, account IDs, or API keys in the v2 delta.

## Engine constants

N/A — no changes to `math_engine.py`.

## Logging redaction

PASS — no new log lines in the v2 delta. The `todays_exits` block uses a typed
`except (FileNotFoundError, KeyError, json.JSONDecodeError): pass` — silent but scoped.

## Dashboard side effects

PASS — `get_history` (app.py:1103) reads post-mortem JSON files only. The `todays_exits`
extension adds a single `open()` + `json.load()` read of today's post-mortem file. No
database writes, no engine calls, no state mutation.

---

## NIT carry-forward (non-blocking)

- **NIT-1 (open):** `static/history.js:249` — `86400000` magic literal (ms/day) in
  `windowDays()`. Not addressed in v2. Still non-blocking (not in math_engine).
- **NIT-2 (partially addressed):** `app.py` — bare `except: continue` at lines 1140
  and 1165 (the two file-scan loops) unchanged. The new `todays_exits` block correctly
  uses typed exceptions. Original loops still have bare `except:`. Still non-blocking.

---

## Verdict

**APPROVED at e77c579**

All 8 gates PASS. Two NITs carried forward from v1 (NIT-1: magic literal, NIT-2: bare
`except:` in original loops). Neither blocks. All three UX BLOCKs resolved: hero sign
coloring, `todays_exits` individual records, reason card anatomy with strip/badge/mini-grid.
