# TDD Handoff — fix/prism-followups

**Status:** RED committed — implementers read YOUR section only, then write minimal GREEN.

**Worktree:** `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-followups`
**Branch:** `fix/prism-followups`

---

## BACKEND — pf-impl-backend (advisors/prism_audit_write.py ONLY)

### Failing tests
`tests/advisors/test_prism_dotenv_hardening.py` — 3 tests

Run to verify RED:
```
python -m pytest "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-followups/tests/advisors/test_prism_dotenv_hardening.py" -v -n0
```

### Root cause
`advisors/prism_audit_write.py` does not call `load_dotenv()`.  The `import database`
is lazy (inside `_main()`), but `database._db_file()` reads `os.environ["DB_PATH"]` at
call time.  When the CLI is invoked from a non-primary cwd without `DB_PATH` in the
shell env, `_db_file()` resolves to the cwd-relative `alphabot_state.db` — a silent
split-brain write.

### Exact minimal change — ONE file only

**File:** `advisors/prism_audit_write.py`

Add `load_dotenv()` at module level, after `from __future__ import annotations` and
before the argparse imports.  This ensures `DB_PATH` from `.env` is in `os.environ`
before `_main()` lazily imports `database` and `_db_file()` fires.

```python
# Before (current lines 1-26):
from __future__ import annotations

import argparse
import sys

# After (add these two lines after the __future__ import):
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()  # populate DB_PATH (and other env vars) from .env before _db_file() resolves

import argparse
import sys
```

**Rules:**
- Do NOT change `database.py` resolution logic.
- Do NOT add `load_dotenv()` to any other file.
- Do NOT change the D-1 error contract (type(exc).__name__ only).
- The comment near "DB_PATH must be set" stays — it is now satisfied by load_dotenv().

### Expected GREEN
All 3 tests pass:
- `test_cli_honors_dotenv_db_path_when_not_in_shell_env` — exit 0, row in temp DB
- `test_cli_shell_env_wins_over_dotenv` — shell env wins over .env (load_dotenv default)
- `test_cli_missing_dotenv_does_not_crash` — no ImportError/AttributeError when no .env

### Report back
SendMessage `pf-test-writer` with: "BACKEND GREEN — <SHA> — test_prism_dotenv_hardening 3/3 pass"

---

## UI — pf-impl-ui (templates/ai_advisor.html chip mapping ONLY)

### Failing tests
`tests/ai_advisor/test_prism_chip_color_mapping.py` — 2 tests fail in RED state
(the bullish and bearish assertions); 5 pass as regression/meta guards.

Run to verify RED:
```
python -m pytest "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claire/worktrees/prism-followups/tests/ai_advisor/test_prism_chip_color_mapping.py" -v -n0
```

Correct path:
```
python -m pytest "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-followups/tests/ai_advisor/test_prism_chip_color_mapping.py" -v -n0
```

### Root cause
`templates/ai_advisor.html` lines 968-973 — the Jinja2 dict mapping `_sentiment`
to CSS modifier class:

```jinja2
{% set _chip_class = {
    'risk-on':        'prism-sentiment-chip--risk-on',
    'risk-off':       'prism-sentiment-chip--risk-off',
    'neutral':        'prism-sentiment-chip--neutral',
    'limited-inputs': 'prism-sentiment-chip--limited-inputs',
}.get(_sentiment, 'prism-sentiment-chip--neutral') %}
```

Missing keys: `bullish` and `bearish`.  The lens_pipeline synthesizer can produce
either canonical (`risk-on`/`risk-off`) or synonym (`bullish`/`bearish`) verdicts.
Both synonyms fall through to `--neutral` (wrong color).

### Exact minimal change — ONE file only

**File:** `templates/ai_advisor.html`, lines 968-973

Add two keys to the mapping dict:

```jinja2
{% set _chip_class = {
    'bullish':        'prism-sentiment-chip--risk-on',
    'risk-on':        'prism-sentiment-chip--risk-on',
    'bearish':        'prism-sentiment-chip--risk-off',
    'risk-off':       'prism-sentiment-chip--risk-off',
    'neutral':        'prism-sentiment-chip--neutral',
    'limited-inputs': 'prism-sentiment-chip--limited-inputs',
}.get(_sentiment, 'prism-sentiment-chip--neutral') %}
```

**Rules:**
- Do NOT change the verdict TEXT rendering (line 976: `{{ _sentiment | e }}`).
- Do NOT change the CSS class DEFINITIONS (lines 706-724 in the `<style>` block).
- Do NOT touch any JS files — chip mapping is template-only.
- Keep ALL existing keys intact (regression guard tests cover them).

### Expected GREEN
All 7 tests pass:
- `test_bullish_verdict_yields_risk_on_chip_class` — bullish -> --risk-on, NOT --neutral
- `test_bearish_verdict_yields_risk_off_chip_class` — bearish -> --risk-off, NOT --neutral
- `test_risk_on_verdict_yields_risk_on_chip_class` — regression guard
- `test_risk_off_verdict_yields_risk_off_chip_class` — regression guard
- `test_neutral_verdict_yields_neutral_chip_class` — regression guard
- `test_unknown_verdict_falls_back_to_neutral_chip_class` — safe default preserved
- `test_chip_color_assertions_are_class_based_not_rgb` — meta-guard

### Report back
SendMessage `pf-test-writer` with: "UI GREEN — <SHA> — test_prism_chip_color_mapping 7/7 pass"
