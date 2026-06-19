# CI Test Harness

> GitHub Actions `tests` workflow: ruff format check, ruff lint, and pytest — all three steps pass on every push.

**Source:** `.github/workflows/tests.yml`, `pyproject.toml`, `requirements-dev.txt`, `tests/conftest.py`
**Last updated:** 2026-06-19

## Overview

The `tests` workflow was RED from its introduction at `1658b18` through PR #53 — every PR was merged via `--admin` bypass. The workflow runs three sequential steps that must all pass before a branch is considered green:

1. `ruff format --check .` — verifies all Python files are correctly formatted.
2. `ruff check .` — verifies zero lint violations under the `select=[E,F,I,B,UP,SIM]` ruleset.
3. `pytest` — runs the full test suite per `pyproject.toml` `[tool.pytest.ini_options]`.

The `ci-green` cycle (PR #54) made all three steps pass on the live GitHub Actions runner, behavior-neutrally (formatting, safe auto-fixes, documented ruleset ignores, ruff pin, credential-gated test skips — zero logic change).

## Running Locally

The local equivalents of the three CI steps, in order:

```bash
# Step 1 — format check (read-only; reports files that need reformatting)
ruff format --check .

# Step 2 — lint check (read-only)
ruff check .

# Step 3 — test suite (uses pyproject.toml addopts automatically)
pytest
```

To apply format and safe auto-fixes (what the cycle ran to green the branch):

```bash
ruff format .
ruff check --fix .   # safe fixes only; --unsafe-fixes is FORBIDDEN
```

The `/lint` skill wraps both format and check steps.

## Ruff Configuration

**Version pin:** `ruff==0.15.11` in `requirements-dev.txt`. The pin ensures CI and local environments target the same format/lint output. Never remove or loosen the pin without re-greening CI.

**Core settings (`pyproject.toml [tool.ruff]`):**
- `line-length = 100`
- `target-version = "py312"`
- `exclude = ["templates", "static", ".venv", "venv", "build", "dist"]`

**Lint ruleset (`[tool.ruff.lint]`):**
- `select = ["E", "F", "I", "B", "UP", "SIM"]`
- `ignore = []` — no global ignores; all silencing is via `per-file-ignores` with justifications.

**Per-file-ignores policy:** Every `per-file-ignores` entry carries an inline comment explaining *why* the rule is suppressed for that file. Silencing a real bug is forbidden; all suppressions are either stylistic deferrals or language-level constraints (e.g. ruff 0.15.11 `noqa` not effective inside triple-quoted docstrings — use `per-file-ignores` instead).

Key suppressions and their rationale:

| File / Glob | Rules | Rationale |
|---|---|---|
| `tests/**` | `SIM*`, `F401`, `F841`, `F811`, `B*`, `UP031`, `E741`, `E501` | Stylistic + intentional mock patterns in test code; deferred to a follow-on style cycle |
| `autotuner.py` | `B023` | Optuna trial-factory closures capture mutable loop variables; fixing requires an Optuna refactor (behavior-affecting) — **deferred, not masked**; tracked |
| `autotuner.py` | `F841` | `split_idx` and `raw_train_dates` are intentionally preserved; source-scan tests look for these names |
| `math_engine.py` | `E501` | Named-constant inline source citations are intentionally long (no-magic-numbers rule requires a citation on every constant; splitting harms the annotation) |
| `advisors/prism_audit_write.py` | `E402` | `load_dotenv()` must precede all imports by design (DE-PRISM-DOTENV) |
| Production modules | `B006`, `B007`, `B905`, `SIM108`, `E501` | Stylistic findings deferred; long Flask route docstrings, Discord embed strings, SQL fragments |

## Credential-Gated Tests

Tests that require live API credentials (`COMPOSER_KEY_ID`, `COMPOSER_SECRET`, `ANTHROPIC_API_KEY`, `ALPACA_*`) use `pytest.mark.skipif` on the absence of the relevant env var. They **skip** (not fail) on secret-less CI runners. The live secrets are never added to GitHub Actions — they remain operator-infra.

The `live`, `slow`, and `perf` markers are already excluded by `pyproject.toml addopts` (`-m 'not live and not slow and not perf'`), so those test files are always deselected on CI.

## Atlas Cache DB Isolation

`tests/conftest.py:pytest_configure()` routes `ATLAS_CACHE_DB_PATH` to a session-temp directory before any test collection. Without this, a stale `alphabot_atlas_cache.db` from a previous operator run with real credentials causes mock-timeout tests (e.g. `test_community_strats_timeout`) to return `available=True` (cache hit) instead of the expected `available=False` (timeout behavior). The temp path ensures every test run sees a cold cache. See DE-CIGREEN-001.

## Deferred Work

The `B023` finding in `autotuner.py` (mutable loop variable capture in Optuna trial factories) is **deferred, not masked**. Fixing it requires restructuring the Optuna trial-factory closures — a behavior-affecting refactor out of scope for the behavior-neutral CI-green cycle. It is tracked via the `per-file-ignores` entry; a future Optuna refactor cycle should resolve it.
