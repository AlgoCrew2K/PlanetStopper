# TDD Handoff — prism-council F-1/F-2/F-3 fixes

**From:** pc-test-writer
**To:** pc-implementer
**Branch:** feat/prism-council-5of5
**Worktree:** C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-council
**RED commit:** 23a4bac (supersedes 1c60a3d)
**Suite state:** 5 FAILED / 44 passed / 1 skipped

---

## Your mission: GREEN (minimum changes to pass the 5 RED tests)

You are the implementer. Read ONLY this handoff — not the feature plan or the
test file. Write the minimum production code to make the 5 failing tests pass.

Confirm RED first:

```
cd C:/Windows/Temp && python -m pytest \
  "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-council/tests/ai_advisor/test_prism_scheduling.py::TestRunIdUnification" \
  "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-council/tests/ai_advisor/test_prism_scheduling.py::TestSynthesizerWaitBarrierHardRule" \
  "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-council/tests/ai_advisor/test_prism_scheduling.py::TestSynthesizerWaitBarrierDeHollowed" \
  --override-ini="addopts=" -v
```

Expected: 5 FAILED, 2 passed, 1 skipped.

---

## Files to change

### 1. `prism_scheduler.py` — dynamic run_id injection (F-1)

**Root cause:** `PRISM_RUN_PROMPT` is a static string constant that instructs
the headless council to generate its own `run_id` via
`datetime.now(timezone.utc).strftime(...)`. But `main()` generates
`run_id = str(uuid.uuid4())` and passes it to `_persist_spend`. The two values
never match — the spend-attribution join is always broken.

**What tests require (TestRunIdUnification):**

- `test_run_id_threaded_into_prism_prompt_not_minted_by_council`: the prompt
  string passed as `cmd[-1]` to `subprocess.run` must NOT contain `'strftime'`
  or `'datetime.now'`, AND must contain a uuid4-format string (the scheduler's
  `run_id`).

- `test_persist_spend_run_id_matches_run_id_embedded_in_prompt`: the
  `run_id` argument passed to `database.insert_prism_audit_entry` (in
  `_persist_spend`) must equal the uuid4 found in the subprocess prompt.

**Minimum fix:**

Change `_run_prism(run_id)` to build the prompt dynamically by embedding
the `run_id` argument. The simplest approach:

```python
def _run_prism(run_id: str = "unknown") -> bool:
    prompt = (
        PRISM_RUN_PROMPT_TEMPLATE  # static preamble without strftime instructions
        + f" The run_id for this session is: {run_id}. "
        "Use this exact string as the run_id for ALL audit rows and the "
        "MARKET_PRISM observation. Do not generate a new run_id."
    )
    cmd = [
        "claude", "-p", "--dangerously-skip-permissions",
        "--model", "claude-opus-4-8",
        "--max-budget-usd", str(MAX_BUDGET_USD),
        "--output-format", "json",
        prompt,  # last element
    ]
    ...
```

OR rename `PRISM_RUN_PROMPT` to `_PRISM_RUN_PROMPT_TEMPLATE` and build the
actual prompt in `_run_prism` by appending the injected run_id. Either way:
- Remove the `datetime.now(timezone.utc).strftime(...)` snippet from the
  prompt template (the council must not mint its own run_id).
- Embed the scheduler's `run_id` in the final prompt string.
- Keep everything else in the prompt (the 6 agent names, spawn directives,
  completion guard — none of those change).

**Third test is GREEN (do not break it):**

`test_idempotency_guard_uses_created_at_not_run_id_format` — currently passing.
Do NOT change `_is_todays_row`. It must continue to key on `created_at` date only.

---

### 2. `.claude/agents/prism-synthesizer.md` — two new Hard Rules bullets (F-2/F-3)

Add exactly two new bullets to the `## Hard Rules` section. The existing 7
bullets stay; append these two at the end of the Hard Rules list.

**Bullet A** — wait-barrier prohibition
(satisfies `test_synthesizer_hard_rules_prohibit_synthesis_before_five_initial_reads`
AND `TestSynthesizerWaitBarrierDeHollowed.test_synthesizer_instructs_wait_barrier_before_synthesis`):

Required elements within ~300 chars: a prohibition marker (`never`/`do not`/
`must not`), the literal string `initial_read` (with underscore), and a
five-count token (`5`/`five`/`all five`).

Example:
```markdown
- **Never synthesize until 5 initial_read rows are confirmed in the audit DB
  for this run_id.** Query the DB directly; never rely on the SendMessage inbox
  alone. If fewer than 5 initial_read rows exist when the wait-barrier times out,
  synthesize with honest limited-inputs degradation naming the missing lenses.
```

**Bullet B** — false-attribution prohibition
(satisfies `test_synthesizer_hard_rules_prohibit_false_attribution`):

Must match one of these patterns:
- `(never|do not|must not)` + `(falsely|false...attribut)` + `(spawn|report|respond)`
- `spawned but` + `did not/didn't` near `report/file/respond`
- `never falsely` within 200 chars of `spawn`

Example:
```markdown
- **Never falsely attribute non-response to a lens that spawned.** A lens that
  spawned but did not report its initial_read is missing or late — not absent.
  Do not record it as "did not spawn". Mark it limited-inputs only after the
  wait-barrier times out.
```

---

## Scope boundary

- Touch ONLY: `prism_scheduler.py` and `.claude/agents/prism-synthesizer.md`
- Do NOT modify any test files
- Do NOT modify any other production code (app.py, database.py, etc.)
- Do NOT touch the 5 analyst .md files (those were addressed in the prior cycle)
- NEVER merge, push, or checkout main

---

## Verify GREEN

```
cd C:/Windows/Temp && python -m pytest \
  "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-council/tests/ai_advisor/test_prism_scheduling.py" \
  --override-ini="addopts=" -q
```

Expected: 0 FAILED, 49 passed (44 + 5 new), 1 skipped.

---

## After GREEN: commit and signal pc-test-writer

Branch check (must NOT be main):
```
git -C "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-council" branch --show-current
```

Stage path-scoped:
```
git -C "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-council" add \
  prism_scheduler.py \
  .claude/agents/prism-synthesizer.md
```

Commit prefix: `fix(prism-council):`

Then SendMessage to `pc-test-writer`:
"GREEN: N passed / 0 failed on feat/prism-council-5of5 HEAD=<sha>. Ready for R/G/R review."
