# Feature: Prism Follow-ups — audit-write dotenv hardening + Market Prism chip-color mapping
Status: ready
Created: 2026-06-17

## Summary
Two minor, independent follow-ons surfaced by the W3 Market Prism capstone run, fixed in one cycle:
1. **DE-PRISM-DOTENV** — `advisors/prism_audit_write.py` does NOT call `load_dotenv()`, so a CLI invocation (`python -m advisors.prism_audit_write ...`) from a non-primary cwd without `DB_PATH` in the shell env falls back to the relative `alphabot_state.db` → writes to the WRONG (cwd-local) DB (a silent split-brain; the capstone analysts hit this). Fix: load `.env` at module import so `DB_PATH` from `.env` is honored regardless of cwd.
2. **RF-1 (re-scoped)** — the Overview Market Prism sentiment chip renders the verdict TEXT correctly ("bullish") but its CSS modifier class is `prism-sentiment-chip--neutral` (verdict→modifier mapping defaults known verdicts to neutral) → a bullish read shows neutral-gray styling. Fix the verdict→chip-modifier mapping so the chip COLOR matches the verdict. (The original RF-1 "lens cards render raw JSON" premise does NOT reproduce — the cards render readable prose digests — so card rework is OUT of scope.)

## Acceptance Criteria
- [x] AC-1 (dotenv honored): with `DB_PATH` present only in a `.env` file (NOT in the process/shell env), invoking the `prism_audit_write` CLI from an arbitrary cwd writes the audit row to the `.env`'s `DB_PATH`, not the relative-fallback DB. Assert via a subprocess/CLI test with a temp `.env` + temp `DB_PATH` + a cwd that has a different local DB; the row lands in the temp `DB_PATH`. (Or an equivalent test that proves `load_dotenv()` runs before `database._db_file()` resolves.) No behavior change when `DB_PATH` is already in the shell env (shell env still wins / is honored).
- [x] AC-2 (chip color matches verdict): the Market Prism overall-sentiment chip's modifier class matches the verdict — `bullish`/`risk-on` → `prism-sentiment-chip--risk-on`; `bearish`/`risk-off` → `--risk-off`; `neutral`/limited → `--neutral`. Assert the mapping (template/JS logic or a render-path unit) for each verdict value; a `bullish` verdict must NOT yield `--neutral`. Assert the design-system contract (the semantic modifier class), never a specific RGB.
- [ ] AC-3 (scope guard): the 5 per-lens digest cards still render prose summaries (unchanged); the `obs-raw-preview` raw-JSON observations column is NOT touched (PM-ASSUMED intended diagnostic preview — if the team finds it is the ONLY render of lens data / a user-facing defect, file a finding, do not rework in this cycle).
- [ ] AC-4 (no regression): the 03:00 nightly `lens_pipeline` DB writes (daemon cwd = primary repo) and the existing Overview render are unaffected; existing prism/advisor tests still pass.

## Architecture
- **AC-1:** add `from dotenv import load_dotenv; load_dotenv()` at `advisors/prism_audit_write.py` module import (mirroring how other entrypoints load env). Confirm `database._db_file()` reads `os.environ["DB_PATH"]` AFTER load_dotenv populates it. Do NOT change `database.py` resolution logic; do NOT remove the existing `# DB_PATH must be set` comment's intent (now satisfied by load_dotenv). Single file change.
- **AC-2:** locate the verdict→chip-modifier mapping (likely `templates/ai_advisor.html` around the prism chip render, or a small helper in `app.py`/JS that picks the class from `overall_sentiment`). Fix the mapping so `bullish`/`risk-on`→`--risk-on`, `bearish`/`risk-off`→`--risk-off`, else `--neutral`. Keep the verdict TEXT rendering unchanged. Minimal template/logic edit.

## Design-System Mapping
The chip uses semantic modifier classes `prism-sentiment-chip--{risk-on,risk-off,neutral}` (existing design-system tokens). AC-2 asserts the correct semantic class per verdict — never a computed color value.

## Edge Cases
- Unknown/empty `overall_sentiment` → `--neutral` (safe default) + correct text.
- `risk-on`/`risk-off` synonyms of bullish/bearish must both map correctly (the stored verdict may be either "bullish" or "risk-on" depending on run — both seen in the DB).
- `DB_PATH` absent from both shell env AND `.env` → existing relative-fallback behavior preserved (no crash).
- `.env` missing entirely → `load_dotenv()` is a no-op, no raise.

## Security Considerations
- D-1 preserved: `prism_audit_write` error contract stays `type(exc).__name__` only; load_dotenv adds no secret exposure (it only populates env from the local `.env`).
- Advisory-only; no `LIVE_EXECUTION`, not in `_SETTINGS_WRITE_ALLOWLIST`. No new input surface.

## Testing Strategy
- AC-1: a hermetic CLI/subprocess test with a temp `.env` carrying `DB_PATH=<temp.db>`, run from a cwd WITHOUT `DB_PATH` in env → assert the audit row lands in `<temp.db>` (and a sentinel-guard-safe temp basename, not `alphabot_state.db`). NO writes to the real state DB (pytest sentinel + conftest isolation).
- AC-2: a render-path/template-logic test mapping each `overall_sentiment` value → expected modifier class (design-system contract assertion). `node --check` for any touched JS.
- AC-4: run tests/advisors + tests/app/ui offline; full-tree verifier vs base 375b010 at the gate.
- **PM gate (post-/review):** PM LIVE visual check — render the Overview with the bullish row 76 + READ the screenshot: the chip is risk-on-colored (not neutral-gray). + a live DE-PRISM-DOTENV proof (CLI from a temp cwd honors .env DB_PATH).

## Scope Boundaries
- **IN**: `advisors/prism_audit_write.py` (load_dotenv); the verdict→chip-modifier mapping (template/app/JS); tests; docs (DECISIONS entries for both).
- **OUT**: `database.py` resolution logic; the per-lens digest cards (already prose); the `obs-raw-preview` column; lens_pipeline/ai_advisor dotenv (separate if ever needed — daemon has env); any engine/trade-path change; the capstone row 76 (leave as-is).
