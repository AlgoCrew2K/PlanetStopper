# AlphaBot v3 — Metadata Verification Report
**Date:** 2026-05-13
**HEAD:** 7586985 (main)
**Scope:** .claude/CLAUDE.md file map · auto-memory · .env git risk · .gitignore · pyproject.toml / requirements.txt

---

## 1. `.claude/CLAUDE.md` — File Map Accuracy

### 1.1 `app.py`

**Claim:** Flask dashboard + minute-by-minute scheduler (spawns `alpha_bot_execution.py` at :00)

**Verdict: CURRENT**

The description is accurate. `app.py` runs a `schedule.every().minute.at(":00").do(threaded_trigger)` loop, spawns `alpha_bot_execution.py` via `subprocess.run`, and serves dashboard routes (`/`, `/api/state`, `/api/logs/<id>`, `/api/chart/<id>`, `/api/settings`, etc.). The dashboard also exposes `/api/force_eod` and `/api/resend_discord` operator buttons — these are safe (they call `reporting` and `autotuner` via background threads, not live Composer sells). The "read-only" framing in Architecture Constraint 2 is still correct for trade execution; the operator triggers are post-mortem/reporting actions.

**No action required.**

---

### 1.2 `alpha_bot_execution.py`

**Claim:** Core engine — per-cycle execution

**Verdict: CURRENT — but description is thin; no CLAUDE.md action needed**

The file contains API connectors (Composer stats fetch, Alpaca history fetch, intraday VWAP fetch), the main execution loop, the full symphony state machine (arming, parabolic, time-squeeze, breakeven, exit confirmation, VWAP breakdown), the execution queue + rate-limit chunking, EOD post-mortem trigger, and autotuner trigger. "Core engine" is accurate if terse.

**No action required.**

---

### 1.3 `math_engine.py`

**Claim:** Risk math: volatility scaling, log time squeeze, parabolic ratchet, MC gating, VWAP, breakeven, exit confirm

**Verdict: CURRENT**

All listed subsystems are present and implemented as named pure functions. The module also contains `calculate_14d_atr_pct` (not listed), which is a supplementary vol calculation used only by `synthetic_history.py` as a `base_atr_pct` input for tick generation. This is a minor omission but does not misrepresent the file.

**Optional action:** Add `ATR` to the CLAUDE.md description. Low priority.

---

### 1.4 `autotuner.py`

**Claim:** Optuna walk-forward (125 trading days, 500 trials per symphony)

**Verdict: CURRENT — one nuance to note**

The 500-trial figure is confirmed (`study.optimize(objective, n_trials=500, n_jobs=-1)`). The 125-day figure maps to `synthetic_history.intraday_dates[-125:]`. However, the study uses `load_if_exists=True` without resetting the study between runs, so trial counts accumulate across Friday runs — the 500 is trials-this-run, not trials-total. The description is not wrong but could mislead. The Known Gotchas entry for study names (`<timestamp>__<symphony>`) contradicts the code, which uses bare `normalized_name` as the study name — study names are **not** timestamped in code (line 307: `study_name=normalized_name`).

**Action items:**
- **A1.4a (MINOR):** CLAUDE.md "Known Gotchas" says study names use `<timestamp>__<symphony>`. Code uses bare `normalized_name`. Fix the Gotcha to reflect actual behavior.
- **A1.4b (MINOR):** Clarify "500 trials" is per-run, not per-study-total (studies accumulate).

---

### 1.5 `database.py`

**Claim:** Dual-SQLite state + optimization

**Verdict: OUTDATED-MINOR**

`database.py` manages only the **state DB** (`alphabot_state.db`). The optimization DB (`optuna_studies.db`) is opened directly by `autotuner.py` via `optuna.storages.RDBStorage`. `database.py` does not import optuna, does not touch `optuna_studies.db`, and has no optimization-related code. The "Dual-SQLite" framing implies `database.py` manages both DBs — it does not. It manages one.

**Action item:**
- **A1.5 (MINOR):** Update description to: `database.py` | State SQLite only (bot_state, chart_history/archive, symphony_strategies, execution_lock). Optimization DB (optuna_studies.db) is managed directly by autotuner.py.

---

### 1.6 `reporting.py`

**Claim:** Discord webhooks + QuickChart embeds

**Verdict: CURRENT**

The file generates two-stage EOD snapshots (`generate_eod_snapshot`), sends EOD Discord posts with QuickChart API charts (`send_eod_discord_post`), and sends per-exit intraday alerts (`send_discord_alert`). All described surfaces are present.

**No action required.**

---

### 1.7 `synthetic_history.py`

**Claim:** Fixture/replay data generation

**Verdict: OUTDATED-MINOR — "fixture" framing is misleading**

`synthetic_history.py` generates real live data from Alpaca — fetching actual 1Day and 1Min bars for the past 125 trading days for autotuner input. It is not a fixture in the test-data sense (static pre-captured data). It caches results in `cache/synthetic_history_v2_<date>_<hash>.json`. The file uses `joblib.Parallel` for multi-day parallel processing.

**Action item:**
- **A1.7 (MINOR):** Update description to: `synthetic_history.py` | Walk-forward intraday history generator — fetches 125d of Alpaca 1Min bars + 20d vol; parallel-processed; cached per date+holdings hash. Feeds autotuner.

---

### 1.8 Unlisted `.py` Files at Repo Root

**Files at root not in CLAUDE.md table:**

None. The five production `.py` files (`app.py`, `alpha_bot_execution.py`, `math_engine.py`, `autotuner.py`, `database.py`, `reporting.py`, `synthetic_history.py`) are all listed. No root-level `.py` files are unaccounted for.

**No action required.**

---

### 1.9 "Known Gotchas" — Optuna Trial Floor

**Claim:** "Default Optuna trial floor | 100 trials (statistical stability)"

**Verdict: OUTDATED-MAJOR**

`autotuner.py` line 308: `study.optimize(objective, n_trials=500, n_jobs=-1)`. The floor is **500 trials**, not 100. The 100 figure does not appear anywhere in the codebase. This is actively wrong and would cause a worker to underrun the tuner by 5x if consulting this table.

**Action item:**
- **A1.9 (MAJOR):** Update Known Gotchas table from "100 trials (statistical stability)" to "500 trials (n_trials=500 per run; studies accumulate across runs via load_if_exists=True)."

---

## 2. Auto-Memory Directory

Location: `C:\Users\paulm\.claude\projects\C--Users-paulm-Documents-Projects-POC-AlphaBotPM\memory\`

Two files exist: `MEMORY.md` (index) and `feedback_researcher_tools_must_include_write.md`.

---

### 2.1 `feedback_researcher_tools_must_include_write.md`

**Claim:** Project-local researcher agents must include `Write, Edit` in `tools:` frontmatter; AlphaBot setup originally omitted them.

**Verdict: STILL-VALID-BUT-WORTH-RETIRING**

The fix was confirmed applied (commit `e187933`, 2026-05-12 per the note). The current agent files should carry the fix. The rule itself is now encoded as a universal Promoted Rule in `~/.claude/CLAUDE.md` ("Agent files encode timeless rules, not incidents") and the specific fix has been applied. The feedback file remains accurate but the incident context is stale — the project has moved past it. The underlying principle (charter and tools frontmatter must agree) is already in global config.

**Action item:**
- **A2.1 (RETIRE):** Consider retiring this feedback file since the fix is applied and the principle is universally encoded. No urgent action, but it adds noise to future reads.

---

### 2.2 MEMORY.md Index

**Claim:** References `project_math_engine_remaining_magic_numbers.md` under "Project" section.

**Verdict: OUTDATED-MAJOR — linked file does not exist**

MEMORY.md line 8 references:
```
- [math_engine.py remaining magic numbers](project_math_engine_remaining_magic_numbers.md)
```
This file does **not exist** in the memory directory. Additionally, the underlying claim — that `run_monte_carlo` and `calculate_14d_atr_pct` retain inline literals — is **no longer true**. Commits `d5e72cd` (MC-gating), `23a8fce` (calculate_14d_atr_pct), and `1310a3d` (calculate_20d_vol) have extracted all magic numbers to named module-level constants. The math_engine.py file is fully compliant with the no-magic-numbers coding standard.

**Action items:**
- **A2.2a (MAJOR):** Remove the broken link from MEMORY.md (`project_math_engine_remaining_magic_numbers.md` is referenced but does not exist).
- **A2.2b (INFO):** Magic-number remediation in `math_engine.py` is complete. No pending work remains.

---

## 3. `.env` in Git — Risk Assessment

**Verdict: LOW RISK — Confirmed placeholder-only template**

`.env` is tracked in git (added in commit `c0ec631` "Add files via upload"). `.gitignore` has a comment "Your keys: .env" but does NOT contain `.env` as an ignore rule — meaning the tracking is intentional.

**Content audit:**
```
COMPOSER_KEY_ID='key'
COMPOSER_SECRET='secret'
ACCOUNT_UUIDS='act1,act2,act3'
ALPACA_KEY='key'
ALPACA_SECRET='secret'
DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'
LIVE_EXECUTION='False'
EXECUTION_START_TIME=09:30
```

All values are clearly placeholder literals. No real credentials, no real account UUIDs (no UUID4 shape), no real webhook URL (terminates in `...`). The `.gitignore` comment "Your keys: .env" suggests this was originally intended to be ignored, but the file was committed as a template — that is a reasonable pattern for a personal project.

**One issue found:** The `.env` template uses `ACCOUNT_UUIDS='act1,act2,act3'` as a single comma-separated variable. The actual code (`alpha_bot_execution.py` lines 32-35, `app.py` lines 81-87, 171-174, 308-310) reads three **separate** vars: `ACCOUNT_INDIVIDUAL`, `ACCOUNT_ROTH`, `ACCOUNT_TRAD`. The template variable `ACCOUNT_UUIDS` does not exist in the code. A new user following the `.env` template would set the wrong key and get empty account UUIDs with no error message.

**Action items:**
- **A3.1 (MAJOR):** Fix `.env` template to use `ACCOUNT_INDIVIDUAL`, `ACCOUNT_ROTH`, `ACCOUNT_TRAD` instead of `ACCOUNT_UUIDS`. The current template will silently fail for any new deployer.
- **A3.2 (INFO):** The intentional tracking of `.env` as a template is acceptable given all values are placeholders. Document this rationale in a comment inside `.env` (e.g., `# This file is a template — tracked intentionally. Copy and fill in real values locally.`).

---

## 4. `.gitignore` Correctness

**Verdict: CURRENT with one minor note**

Entry-by-entry review:

| Entry | Still relevant? | Notes |
|-------|----------------|-------|
| `.env` comment header | N/A | Comment only; `.env` is NOT ignored (tracked) |
| `alphabot_state.db` | YES | Runtime state DB, per-user |
| `optuna_studies.db` | YES | Optuna optimization DB, per-user |
| `__pycache__/` | YES | Standard Python |
| `cache/` | YES | `synthetic_history.py` writes here |
| `history_cache.json` | YES | `alpha_bot_execution.py` writes here |
| `symphony_logs.json` | YES | `database.py` writes here |
| `post_mortem_*.json` | YES | `reporting.py` writes here |
| `.coverage` | YES | pytest-cov artifact |
| `.pytest_cache/` | YES | pytest artifact |
| `.ruff_cache/` | YES | ruff artifact |
| `htmlcov/` | YES | pytest-cov HTML report |
| `*.db-journal`, `*.db-wal`, `*.db-shm` | YES | SQLite WAL/journal files |

**Minor note:** The `.gitignore` comment "Your keys: .env" (line 1-2) is the section header but `.env` is not listed as an ignore pattern beneath it — the actual tracking of `.env` is confirmed intentional (see Section 3). The comment is slightly misleading to a future reader who would expect `.env` to be ignored.

**No blocking action required. The minor note on the comment is cosmetic.**

---

## 5. `pyproject.toml` / `requirements.txt`

### 5.1 Runtime Dependencies (`requirements.txt`)

Declared: `requests`, `numpy`, `pandas`, `python-dotenv`, `Flask`, `schedule`, `optuna`, `joblib`

**Imports found in production code vs declared:**

| Module | Imported in code? | In requirements.txt? | Verdict |
|--------|------------------|---------------------|---------|
| `requests` | YES (all 5 py files) | YES | OK |
| `numpy` | YES (`math_engine.py`, `alpha_bot_execution.py`, `synthetic_history.py`) | YES | OK |
| `pandas` | YES (`alpha_bot_execution.py`, `synthetic_history.py`) | YES | OK |
| `python-dotenv` (`dotenv`) | YES (`app.py`, `alpha_bot_execution.py`, `synthetic_history.py`) | YES | OK |
| `Flask` | YES (`app.py`) | YES | OK |
| `schedule` | YES (`app.py`) | YES | OK |
| `optuna` | YES (`autotuner.py`) | YES | OK |
| `joblib` | YES (`synthetic_history.py`) | YES | OK |
| `math` (stdlib) | YES (`math_engine.py`, `autotuner.py`) | N/A | stdlib |
| `sqlite3` (stdlib) | YES (`database.py`) | N/A | stdlib |
| `hashlib` (stdlib) | YES (`synthetic_history.py`) | N/A | stdlib |
| `zoneinfo` (stdlib) | YES (`alpha_bot_execution.py`) | N/A | stdlib (3.9+) |

**Verdict: CURRENT — no unused or undeclared third-party dependencies.**

---

### 5.2 Dev Dependencies (`requirements-dev.txt`)

Declared: `ruff`, `pytest`, `pytest-cov`, `hypothesis`

**Audit:**
- `ruff`: Used in `/lint` skill. YES.
- `pytest`: Used in `/run-tests` skill, `pyproject.toml` has `[tool.pytest.ini_options]`. YES.
- `pytest-cov`: Used for coverage. YES.
- `hypothesis`: Not found in any test file in the current test suite.

**Check for hypothesis usage:**

`hypothesis` is declared in `requirements-dev.txt` but no test file currently imports it. All test files use `pytest` fixtures, parametrize, and custom fixtures only.

**Action item:**
- **A5.1 (MINOR):** `hypothesis` is declared in `requirements-dev.txt` but unused in any test file. Either add property-based tests (the original intent) or remove it to keep the dev manifest clean.

---

### 5.3 Version Pins

No version pins exist in either `requirements.txt` or `requirements-dev.txt` — all entries are bare package names. This is a risk for production reproducibility (an `optuna` or `numpy` breaking release would silently break the engine), but this is outside the scope of a metadata accuracy audit.

**No action required for this audit. Flag as a separate operational risk if a `requirements.lock` or pinned versions task is desired.**

---

## Summary

### Action Item Count by Target

| Target | Total | Major | Minor | Retire/Info |
|--------|-------|-------|-------|-------------|
| 1. CLAUDE.md file map | 5 | 1 (A1.9 trial floor) | 4 (A1.4a, A1.4b, A1.5, A1.7) | 0 |
| 2. Auto-memory | 3 | 1 (A2.2a broken link) | 0 | 2 (A2.1 retire, A2.2b info) |
| 3. .env risk | 2 | 1 (A3.1 template wrong keys) | 0 | 1 (A3.2 info) |
| 4. .gitignore | 0 | 0 | 0 | 0 |
| 5. pyproject/requirements | 1 | 0 | 1 (A5.1 hypothesis) | 0 |
| **Total** | **11** | **3** | **5** | **3** |

---

### Overall Verdict: MINOR DRIFT

Three major issues require prompt attention before the next worker dispatch cycle:
1. **A1.9** — CLAUDE.md states "100 trial floor"; code enforces 500. Workers consulting this table will mis-scope autotuner work.
2. **A2.2a** — MEMORY.md references a non-existent file (`project_math_engine_remaining_magic_numbers.md`). A worker following this link will fail silently.
3. **A3.1** — `.env` template uses `ACCOUNT_UUIDS` instead of the three separate `ACCOUNT_INDIVIDUAL/ROTH/TRAD` keys the code actually reads. Silently breaks new deployments.

No architecture drift or missing files found. The codebase is structurally consistent with the file map; drift is limited to description accuracy and one critical gotcha table entry.
