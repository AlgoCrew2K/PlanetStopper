# Planet Stopper — Active Backlog (scaffolded 2026-06-13)

Master index of everything currently on the plate, broken into individual features so each
can be handed to its own Agent Team without losing scope. Status verified against git at
scaffold time (main = `d636ce3`).

**Legend:** 🔴 not started · 🟡 in progress · 🟢 landed on main · ⛔ blocked · 💤 deferred

---

## Epic A — Market Prism: real collaborating agent team (EXCLUSIVE FOCUS)

The Market Prism (overnight "overall market sentiment" read) must be a **real collaborating
Claude Code Agent Team** — per-lens Opus analysts that genuinely clarify/debate, then a
synthesizer integrates one report. NOT the Cycle-4 data-fetch + single-Haiku synthesis (that
is the data layer only). Every member writes its own output to a DB audit log keyed to the
run, so each nightly report is fully auditable. **Prove it observed first** (real full report
+ per-agent logs shown to the operator) before any unattended schedule. Once this epic starts,
nothing else is worked on until the operator sees a real report + logs.

| # | Feature | File | Status |
|---|---------|------|--------|
| A0 | Prism epic overview / sequencing / hard rules | [market-prism-overview.md](market-prism-overview.md) | 🟡 |
| A1 | Phase 1 — audit-log DB foundation (migration 032 + accessors + CLI writer) | [market-prism-phase1-audit-log-foundation.md](market-prism-phase1-audit-log-foundation.md) | 🟡 team spawned, no commits |
| A2 | Phase 2 — collaborating analyst team + orchestration | [market-prism-phase2-collaborating-analyst-team.md](market-prism-phase2-collaborating-analyst-team.md) | 🔴 blocked by A1 |
| A3 | Phase 3 — observed proof run (real report + logs to operator) | [market-prism-phase3-observed-proof-run.md](market-prism-phase3-observed-proof-run.md) | 🔴 blocked by A2 |
| A4 | Phase 4 — unattended scheduling + graceful fallback | [market-prism-phase4-unattended-scheduling.md](market-prism-phase4-unattended-scheduling.md) | 🔴 blocked by A3 |

## Epic B — Lens data completion (feeds richer reads to the Prism analysts)

| # | Feature | File | Status |
|---|---------|------|--------|
| B0 | Epic overview / cross-cutting rules | [lens-data-completion.md](lens-data-completion.md) | 🔴 (deferred until A unblocks) |
| B1 | GDELT tone / sentiment producer → `sentiment_analyst` | [lens-data-gdelt-sentiment.md](lens-data-gdelt-sentiment.md) | 🔴 |
| B2 | Technicals producer → `technicals_analyst` | [lens-data-technicals.md](lens-data-technicals.md) | 🔴 |
| B3 | Derivatives producer → `derivatives_analyst` | [lens-data-derivatives.md](lens-data-derivatives.md) | 🔴 |

## Epic C — Platform polish / tech debt (independent, schedule around A)

| # | Feature | File | Status |
|---|---------|------|--------|
| C1 | Advisor synthesis model → configurable (Opus prod / cheap CI) | [advisor-synthesis-model-config.md](advisor-synthesis-model-config.md) | 🔴 |
| C2 | xdist test-isolation fix (`-n2` spurious failures) | [xdist-test-isolation-fix.md](xdist-test-isolation-fix.md) | 🔴 |
| C3 | Tech-debt cleanups (stash reconcile, route self-skip, dead param) | [tech-debt-cleanups.md](tech-debt-cleanups.md) | 🔴 |

## Future lanes (out of current scope — captured so we don't lose the vision)

- **public.com execution lane** — second execution backend alongside Composer. Explicitly
  deferred by the operator ("we can get to that"). See
  [project-vision-portfolio-command-center](../../.claude) memory. Not a current feature.

---

## Recently landed (context — do not re-scope)

| Work | SHA | Note |
|------|-----|------|
| Cycle 5 — Market Prism Overview tab (surface) | `d636ce3` | Renders latest MARKET_PRISM row; AC-1..AC-6 GREEN |
| Cycle 4 — off-hours lens pipeline + Market Prism always-on persistence | `1b15b3d` | The DATA layer the analysts will pull from |
| Synthesis JSON-extraction fix (Haiku fence-stripping) | `df2d19e` | Stand-in synthesis; superseded by Epic A |
| Strategy Builder SPA port (6th tab) + Cycle 3 lens swaps | merged | |
| PC-crash fix (pyproject `-n2`, backoff bound) | `a7f2bac`, `948fb02` | infra, not a feature |
| Main-merge protection rotated to PM-only `PM_VERIFIED_MERGE` token | hooks | agents can no longer merge to main |

## Hard pre-merge bar (applies to EVERY feature here)

1. PM-personal **`-n0` gate** (`-o addopts= -p no:xdist`; `-n2` gives spurious isolation
   failures here) — 0 new failures vs the fork-point baseline.
2. PM-personal **nightly live-functional test** for any feature with runtime/integration
   behavior — run BEFORE merge, not after.
3. Merge only via the private `PM_VERIFIED_MERGE` token. No force-push. Docs-only changes are
   exempt from the live gate but still get the `-n0` gate if they touch test-adjacent files.
