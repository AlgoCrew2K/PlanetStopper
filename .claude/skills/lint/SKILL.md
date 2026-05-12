---
name: lint
description: Run ruff format check + lint across the repo, or auto-fix safe issues.
allowed-tools: Read, Glob, Bash
---

## Dynamic Context

```
!`ruff --version 2>/dev/null || echo "ruff not installed"`
!`grep -A1 "tool.ruff" pyproject.toml 2>/dev/null | head -5 || echo "no pyproject.toml ruff section"`
```

## Args

- No args → check mode (read-only; reports issues, exits non-zero if any found)
- `--fix` → apply safe autofixes (format + lint `--fix`)
- `<path>` → restrict all checks to that path

## Steps

1. **Check ruff** — run `ruff --version`. If missing, print:
   > ruff not found. Install with: `pip install ruff`
   Then stop. Do NOT install anything.

2. **Check config** — run `grep -A1 "tool.ruff" pyproject.toml 2>/dev/null`. If absent, print:
   > No [tool.ruff] section found in pyproject.toml — running with ruff defaults.
   > Consider adding a [tool.ruff] section to pin rules and line length.
   Continue regardless.

3. **Determine target** — default is `.`; if `<path>` was provided, use that path instead.

4. **Check mode** (no `--fix`):
   ```
   ruff format --check <target>
   ruff check <target>
   ```
   Collect combined output.

5. **Fix mode** (`--fix`):
   ```
   ruff format <target>
   ruff check --fix <target>
   ```
   Collect combined output.

6. **Summarize results**:
   - Fix mode: report count of files reformatted and issues auto-fixed.
   - Check mode: report total issue count; list the top-3 rule codes by frequency (e.g. `E501 ×12, F401 ×7, W291 ×3`).
   - Check mode + clean (no issues, format already compliant): print:
     > [CLEAN] No issues found.

## What You Must NOT Do

- Never run `--unsafe-fixes` — it changes code semantics
- Never commit lint changes — that is the user's responsibility
- Never modify `pyproject.toml` or any ruff config file
- Never install packages (`pip install`, etc.)

## Examples

**`/lint`** — check mode, whole repo:
```
ruff format --check .
ruff check .
```
Output: total issue count + top-3 rule violations, or `[CLEAN] No issues found.`

**`/lint --fix`** — auto-fix safe issues, whole repo:
```
ruff format .
ruff check --fix .
```
Output: files reformatted + issues fixed.

**`/lint math_engine.py`** — check mode, single file:
```
ruff format --check math_engine.py
ruff check math_engine.py
```
Output: issues in that file only, or `[CLEAN] No issues found.`
