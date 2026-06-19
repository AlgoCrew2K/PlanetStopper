# Feature Plan: Green the CI `tests` harness

Status: ready

## Summary
The GitHub Actions `tests` workflow (`.github/workflows/tests.yml`, added in `1658b18`) has been **RED since inception** and gates nothing — PRs #50–#53 all `--admin`'d past it. Root cause: the workflow runs `ruff format --check .` → `ruff check .` → `pytest` as three blocking steps; origin/main carries 34 un-ruff-formatted files + 6929 lint violations of the (already-sane) `select=[E,F,I,B,UP,SIM]` ruleset, so the format step fails and pytest never runs. Additionally `ruff` is **unpinned** in `requirements-dev.txt`, so CI and local can disagree on the format/lint target.

This cycle makes the CI harness actually pass GREEN, **behavior-neutrally** (formatting + safe lint auto-fixes + ruleset/config + test-skip markers ONLY — zero logic/behavior change). The PM full-tree verifier staying 0-fail before==after is the behavior-neutral proof.

## Acceptance Criteria
- **AC-1:** `ruff format .` applied repo-wide; `ruff format --check .` reports 0 files to reformat at the branch tip.
- **AC-2:** `ruff check .` reports 0 errors at the branch tip. Method: run `ruff format` FIRST (wraps long lines → kills most E501), then `ruff check --fix` for SAFE auto-fixes only (NEVER `--unsafe-fixes`), then for the residual either (a) fix genuine F/B correctness findings, or (b) add NARROWLY-scoped, documented `[tool.ruff.lint]` per-file-ignores / `noqa` ONLY for un-wrappable or genuinely-noisy-stylistic cases (e.g. long URLs/strings, intentional patterns). Do NOT broaden `ignore` to silence real bugs. Every ignore added must carry an inline justification.
- **AC-3:** `ruff` pinned in `requirements-dev.txt` to the exact version used this cycle (deterministic CI==local). State the version.
- **AC-4:** The CI `pytest` step passes on GitHub runners that have NO API secrets. Tests that hard-require live credentials (COMPOSER_KEY_ID / COMPOSER_SECRET, ANTHROPIC_API_KEY, ALPACA_*) must `pytest.mark.skipif` on the absence of the env var, with a clear skip reason — they SKIP (not fail) when keys are absent. `live`/`slow`/`perf` are already excluded by pyproject addopts. Identify the offending tests empirically by running the suite with the relevant env vars UNSET.
- **AC-5 (PM-gated):** The full local test suite remains 0-fail before==after (behavior-neutral). Count may shift only by newly-skipped env-gated tests; ZERO new failures vs base `56ec9ce`.
- **AC-6:** `.claude/tdd-handoff.md` is gitignored (`git rm --cached` + `.gitignore` entry) so it stops riding into merges.
- **AC-7:** The actual GitHub Actions `tests` run on the PR goes GREEN (all three steps pass) — verified on the live PR, not just locally.

## Architecture
- `pyproject.toml` `[tool.ruff.lint]` — add documented per-file-ignores only as justified by AC-2.
- `requirements-dev.txt` — pin `ruff==<version>`.
- `.github/workflows/tests.yml` — edit ONLY if needed (e.g., to keep the pytest invocation aligned with pyproject addopts). Prefer no change.
- Repo-wide `*.py` — `ruff format` + safe `ruff check --fix` output. Mechanical.
- Specific test files — `skipif` markers for credential-gated tests (AC-4).
- `.gitignore` — add `.claude/tdd-handoff.md`; `git rm --cached` it.

## Edge Cases
- **`ruff --unsafe-fixes` is FORBIDDEN** — it can alter semantics. Safe `--fix` only.
- `math_engine.py` (no-magic-numbers + golden-fixture rules): formatting/whitespace only — every named constant and all logic byte-identical except wrapping. The reviewer verifies this file specifically.
- `ruff format` may leave un-wrappable E501 (long URLs, embedded strings) → targeted `# noqa: E501` with reason, NOT a blanket E501 ignore.
- Run `ruff format` BEFORE `ruff check --fix` (order matters — format resolves most E501 so the lint residual is small).

## Security Considerations
None — pure tooling/config/test-infra. No new external input, no execution-path code, no secrets added (env-gated tests SKIP on secret-less CI rather than CI gaining secrets — secrets are operator-infra, out of scope).

## Testing Strategy
- After each phase: `ruff format --check .` (AC-1) + `ruff check .` (AC-2) clean.
- PM full-tree verifier at the tip, base `56ec9ce`: 0 new failures (AC-5).
- Reproduce the secret-less CI condition locally (unset the API env vars) to confirm AC-4 (the gated tests SKIP, suite passes).
- The PR's live CI run GREEN (AC-7).

## Scope Boundaries
- IN: ruff format, safe lint auto-fixes + documented targeted ignores, ruff pin, credential-gated test skipif, gitignore tdd-handoff, the CI yaml only if required.
- OUT: ANY logic/behavior change; new features; `--unsafe-fixes`; adding GitHub secrets; touching the live execution path; broadening `ignore` to mask real bugs; reformatting `templates/`/`static/` (already excluded in `[tool.ruff].exclude`).
