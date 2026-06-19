# Market Prism — Run Orchestration Runbook

**Status:** ready (Phase 2 deliverable)
**Created:** 2026-06-13
**Supersedes:** Cycle-4 `run_pipeline()` for the deliberation layer (data layer unchanged)

This runbook is the PM-driveable procedure for running a Market Prism nightly session. It covers the Phase-3 observed proof run (PM watches the run live) and serves as the template for the Phase-4 unattended headless schedule.

---

## Prerequisites

Before starting any run, verify:

1. **Phase 1 is on main.** Migration 032 (`prism_audit_log`) is applied. Verify:
   ```bash
   python -c "import database; print(database._MIGRATION_FILES[-1])"
   # Expected: 032_prism_audit_log.sql
   ```

2. **`ANTHROPIC_API_KEY` is set.** The synthesizer and all 5 analysts run Opus 4.8.
   ```bash
   python -c "import os; key=os.environ.get('ANTHROPIC_API_KEY',''); print('OK' if key.startswith('sk-') else 'MISSING')"
   ```

3. **`DB_PATH` points to the live state DB** (or a known temp path for dry runs).
   ```bash
   python -c "import os; print(os.environ.get('DB_PATH', 'alphabot_state.db (default)'))"
   ```

4. **Agent files are present** in `.claude/agents/`:
   ```
   prism-technicals-analyst.md
   prism-sentiment-analyst.md
   prism-derivatives-analyst.md
   prism-macro-analyst.md
   prism-fundamentals-analyst.md
   prism-synthesizer.md
   ```

5. **Cycle-4 lens data is fresh.** The Prism analysts pull from `advisors/lens_pipeline._call_lens_section()`. For a live run, the 03:00 pipeline should have already written data. For an observed proof run outside that window, the analysts will get `available=False` for most lenses and produce a `limited-inputs` report — that is acceptable for Phase 3.

---

## Run Protocol

### Step 1 — Spawn the Agent Team

The PM spawns the team with the synthesizer as lead. All analysts are spawned as teammates. They share a worktree on a short-lived session branch.

**Team composition:**
- Lead: `prism-synthesizer`
- Members: `prism-technicals-analyst`, `prism-sentiment-analyst`, `prism-derivatives-analyst`, `prism-macro-analyst`, `prism-fundamentals-analyst`
- Model: `opus` for all (operator directive 2026-06-13)
- Isolation: worktree on `run/prism-<date>` branch (for write access to state DB path)
- Worktree `.env` must include `ANTHROPIC_API_KEY` and `DB_PATH`

**Critical:** copy `.env` into the worktree before kickoff. Reference: `feedback_worktree_env_gap_env_sensitive_tests`.

### Step 2 — Synthesizer generates run_id and kicks off analysts

The synthesizer agent (per its role file):
1. Generates `run_id` as an ISO UTC timestamp
2. Confirms DB_PATH and repo root
3. Sends kickoff messages to all 5 analysts via SendMessage

The PM monitors via TaskList — all 5 analysts should be active within ~2 minutes.

### Step 3 — Analysts pull lens data and post initial reads

Each analyst:
1. Calls `_call_lens_section("<lens>")` from `advisors/lens_pipeline`
2. Forms their initial read
3. Writes `phase=initial_read` to `prism_audit_log` via `python -m advisors.prism_audit_write`
4. Sends their read to the synthesizer via SendMessage

**Expected duration:** 5–10 minutes (Opus 4.8 calls + lens data pull).

**PM check:** After 15 minutes, if fewer than 3 analysts have responded, check git status in the worktree and agent heartbeats. An analyst silent for 15+ minutes without a WIP commit may be stalled.

### Step 4 — Clarifying Q&A

The synthesizer facilitates cross-lens questions. Analysts message each other via SendMessage. This phase is unstructured and may last 5–20 minutes. PM monitors passively.

**Normal pattern:** macro and technicals often exchange. Derivatives may ask macro for rate context. Sentiment may ask fundamentals whether a sentiment spike is earnings-driven or macro-fear-driven.

**PM does not relay messages.** This is the team's autonomous coordination.

### Step 5 — Debate decision (synthesizer's call)

The synthesizer decides whether debate is needed. PM does not override this decision. If the synthesizer skips debate and the PM believes a genuine conflict was missed, note it in the Phase-3 review but do not interrupt the run.

### Step 6 — Synthesis and DB write

The synthesizer:
1. Writes `phase=synthesis` to `prism_audit_log`
2. Writes the `MARKET_PRISM` observation row to `advisor_observations`
3. Broadcasts a completion message

PM verifies the write:
```python
import database
row = database.get_latest_market_prism_summary()
print(row["verdict"], row["raw_response"]["run_id"])
```

Verify `run_id` matches the one the synthesizer generated.

### Step 7 — Verify the audit trail

```python
import database
run_id = "<run_id from synthesizer>"
trail = database.get_prism_audit_for_run(run_id)
print(f"Audit entries: {len(trail)}")
for entry in trail:
    print(f"  {entry['agent_role']:30s} {entry['phase']}")
```

**Expected minimum entries (no debate, all lenses available):**
- 5 × `initial_read` (one per analyst)
- ≥0 × `clarification` (any number)
- 1 × `synthesis` (synthesizer)

**Expected with debate:**
- 5 × `initial_read`
- ≥0 × `clarification`
- N × `debate_round_1..3` entries per participating analyst + synthesizer
- 1 × `synthesis`

**Failure signal:** fewer than 6 entries total (missing any analyst's initial_read or the synthesis) means a member did not log its output — that is a defect.

### Step 8 — Team teardown

Send shutdown_request to each team member once. Wait for terminations. Then remove the session worktree. Reference: `feedback_graceful_shutdown_before_teamdelete`.

```bash
git worktree remove "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/<session-branch>" --force
git branch -d run/prism-<date>
```

---

## Dry Run Mode

For testing the protocol without writing to the live DB:

1. Set `DB_PATH` to a temp path in the worktree `.env`:
   ```
   DB_PATH=C:/Windows/Temp/prism_dry_run.db
   ```
2. The synthesizer will write to the temp DB. Verify the MARKET_PRISM row and audit trail there.
3. Discard the temp DB after verification.

The Cycle-4 `run_pipeline()` dry_run parameter is separate and unrelated — it controls the Cycle-4 Haiku synthesis path, not the Phase-2 agent team.

---

## Phase-4 Headless Schedule (preview)

For Phase 4 (unattended), the run is initiated by a headless `claude` CLI invocation from a cron job or systemd timer:

```bash
claude --agent prism-synthesizer \
       --env ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
       --env DB_PATH="$DB_PATH" \
       "Run the nightly Market Prism: generate run_id, kick off all 5 analysts, coordinate, synthesize, write MARKET_PRISM row. Follow your role file exactly."
```

The synthesizer spawns the analyst team. The headless session exits after step 7 (completion broadcast). Phase 4 adds a graceful fallback wrapper (retry on error, Discord notification on failure) — that is out of scope for Phase 2.

---

## Known Constraints

| Constraint | Detail |
|------------|--------|
| Flask daemon cannot spawn Agent Teams | The Prism run is a Claude Code session, NOT a daemon Python job. Do not add Team spawning to `app.py`. |
| Fresh team per run | Marathon sessions accumulate compaction/husk fragility. Always a fresh session. |
| `.env` must be copied to worktree | `ANTHROPIC_API_KEY` and `DB_PATH` must be present or analysts cannot call the API or write to the DB. |
| SendMessage inbox lag | Teammates' inboxes lag. Verify repo state with git before acting on teammate claims. Per `feedback_verify_git_over_lagging_teammate_relays`. |
| Debate cap is 3 rounds | Synthesizer enforces this. PM does not intervene in debate closure. |
| One MARKET_PRISM row per run | The synthesizer checks for an existing row for the run_id before writing. If a row already exists (e.g. from a retry), it does not write a second one. |
