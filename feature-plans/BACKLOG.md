# Planet Stopper — Open Backlog
**As of:** 2026-06-19
**Source of truth for shipped/obsolete history:** `.claude/backlog-reconciliation.md`

This file lists only work that is genuinely open. Plans renamed to `.completed.md` or `.obsolete.md`
are archived in this same directory; see the reconciliation doc for the full classification record.

---

## Blocked on operator action

### `market-prism-phase4-unattended-scheduling.md`
Code is shipped (PR #49, `ab03263`). Blocked on two operator actions:
- Register `prism_scheduler.py` as a nightly Windows Task Scheduler job
  (requires one-time `Run-As-Administrator` setup via `schedule_prism.ps1`).
- Disable the daemon 03:00 `lens_pipeline` slot once the Task Scheduler job is active
  to avoid double-runs.

### `security-review.md` — S-1 (operator action required)
S-1: Discord webhook credential has been in git history since `c0ec631`. Operator must
rotate the webhook URL and explicitly approve the force-push scrub needed to remove it
from history. No code change until operator provides the new credential + go-ahead.

---

## Ready to build now

### `vwap-remediation.merged.md` — W2 calibration sweep
H1/V2/V3 shipped. Remaining: write `scripts/vwap-calibration-report.py` and produce
`docs/research/dashboard/vwap-calibration-report.md` with per-symphony VWAP window
recommendations. May be consolidated with V1 below into a single sweep cycle.

### `engine-correctness-remediation.merged.md` — V1 calibration sweep
E1/E2/H1–H3/O1–O5/V2/V3 shipped. Remaining: per-symphony calibration sweep for PARA +
VWAP parameters now that the methodology fixes are in. Shares the W2 gap; PM may elect
a combined sweep cycle.

### `tech-debt-cleanups.md` — C3b + C3c
C3a is a confirmed no-op (stash empty). Remaining:
- **C3b:** formal route self-skip closure — write the route-level RED test for the
  302-redirect behaviour (AC-4b/AC-5b never written).
- **C3c:** remove the unused `higher_is_better: bool` parameter from
  `advisors/asset_swap_engine.py:375` and its four call sites (:548/:572/:597/:606).
Small; Tier 1.

### `security-review.md` — S-2 + DEP-1 (buildable now, independent of S-1)
- **S-2:** commit a `.env.example` template (no real credentials).
- **DEP-1:** tighten `anthropic~=0.85.0` and `feedparser>=6.0` to exact `==` pins in
  `requirements.txt` / `pyproject.toml`.
