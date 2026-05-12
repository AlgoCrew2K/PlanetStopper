---
name: run-tests
description: Run the pytest suite with sensible defaults. Reports summary, surfaces failures, and offers diff-only mode for fast feedback.
allowed-tools: Read, Glob, Bash
---

## Dynamic Context

```
!`python -m pytest --version 2>/dev/null || echo "pytest not installed"`
!`ls tests/ 2>/dev/null || echo "no tests dir"`
```

## Steps

1. **Check pytest** — run `python -m pytest --version`. If missing, print:
   > pytest not found. Install with: `pip install pytest pytest-cov`
   Then stop. Do NOT install anything.

2. **Check tests/** — if `tests/` does not exist, print:
   > No tests/ directory yet. Create one and add test_*.py files under tests/.
   Then stop.

3. **Build the command** — start with `python -m pytest`.
   - No args → full suite (exclude `test_live_*.py` unless `--include-live` is passed).
   - `<path>` arg → append the path.
   - `-k <expr>` → append `-k <expr>`.
   - `--fast` → append `-x --ff`.
   - Always append `-v --tb=short` for readable output.
   - Always exclude live tests: append `--ignore=tests` patterns or use `--deselect` for any `test_live_*.py` unless `--include-live` was passed.

4. **Run** — execute the built command via Bash.

5. **Parse results** — extract the summary line (e.g. `5 passed`, `2 failed`).
   - On failure: surface the first 3 failed test IDs and their assertion messages.
   - Suggest `/run-tests --fast` if any tests failed and `--fast` was not already used.

## What You Must NOT Do

- Never install packages (`pip install`, `poetry add`, etc.)
- Never modify any test file
- Never run `test_live_*.py` files unless the user explicitly passes `--include-live`
- Never run tests against live external APIs without `--include-live`

## Examples

**`/run-tests`** — full suite, no live tests:
```
python -m pytest -v --tb=short <exclude live files>
```
Output: summary line + up to 3 failure details if any failed.

**`/run-tests tests/test_math_engine.py`** — single file:
```
python -m pytest tests/test_math_engine.py -v --tb=short
```

**`/run-tests -k volatility --fast`** — keyword filter, stop on first fail:
```
python -m pytest -k volatility -x --ff -v --tb=short
```
