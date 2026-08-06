# INV-1 / INV-2 Findings — 2026-08-05

**Type:** PM diagnostic (live droplet investigation), follow-up to `docs/audit/TWO-WEEK-REVIEW-2026-08-04.md` §5 items 1-2 / §6 "Open investigations."
**Evidence gathered live from droplet 104.248.7.101** (journalctl + state DB + optuna DB + weekly synthetic-history cache bundles), 2026-08-05.
**Consumed by:** `DE-AUDIT-BL2-001` (see `DECISIONS.md`) — INV-1's root-cause finding is the honesty boundary that entry's fix explicitly does NOT claim to close.

---

Source of questions: `docs/audit/TWO-WEEK-REVIEW-2026-08-04.md` §5 items 1–2, §6 "Open investigations".
Evidence gathered live from droplet 104.248.7.101 (journalctl + state DB + optuna DB + weekly synthetic-history cache bundles), 2026-08-05.

## INV-1 — 2026-07-24 partial autotune batch (7/11) + skipped Prism council: ROOT-CAUSED

**Verdict: two independent, mundane causes. No OOM, no reboot, no code exception, no droplet-health event.**

1. **The 7/11 partial batch was caused by a deploy restart.** `systemd: Stopping planetstopper.service` at
   **2026-07-24 22:34:39 UTC** — a clean operator/PM-initiated `systemctl restart` during that day's shipping
   activity (2026-07-24 was the DE-EXIT-FRICTION-REALIZED-001 / preconditions-R3 deploy day). The batch started
   21:48:33 UTC; per-study Optuna timeline: land-of-feaverd 21:48 → crypto 21:51 → lqd+eyeg 21:54 →
   hunted-cascades 21:59 → erased-history 22:00 → reasonabilists 22:23 → golden-age 22:25 → **paragons 22:26,
   killed mid-study at 312/500 trials** (`20260724T222637952404Z__planet of the paragons` — the only <500-trial
   study in the DB). The remaining 3 (corporate chaos 2060, corporate chaos 5, planet lqd waltanansi) never
   started. Kernel journal shows zero OOM/kill events 07-23..07-26; single boot since Jun 19; daemon memory
   peak that day 319.4M (nowhere near the cgroup cap).
   - **Implication for BL-2:** the observed 07-24 incident was a SIGTERM — a try/except CANNOT catch it. BL-2
     still ships per its own "defense-in-depth regardless" decision (it guards the uncaught-exception class),
     but no one should claim it prevents a 07-24-style recurrence. The real preventive control for THIS class
     is operational: don't deploy-restart while the weekly autotune batch is in flight (batch window ≈ 21:48–23:00
     UTC on autotune Fridays).
2. **The skipped council was a Claude subscription weekly-limit 429.** All 3 attempts at 07:00–07:01 UTC
   returned `api_error_status:429, "You've hit your weekly limit · resets 9pm (UTC)"`; the scheduler retried
   MAX_ATTEMPTS=3 and exited loudly — worked exactly as designed. Root cause = subscription quota exhausted by
   that week's heavy interactive usage, not code.

## INV-2 — zero-triad autotune rows (11/40, now 11/51): MECHANISM IDENTIFIED (a NEW third mechanism)

**Verdict: BOTH audit candidate mechanisms are refuted. The real mechanism is weekly holdings rotation into
thin-history tickers truncating that week's per-symphony replay coverage.**

- **(b) "genuine zero-trigger week" REFUTED:** zero-triad studies ran 500 trials at Optuna-overhead speed
  (mean 0.09–0.16 s/trial, 451–500/500 zero-valued trials) vs 0.22–9.8 s/trial and 0 zero-valued trials for
  healthy studies. A genuine zero-trigger replay would still pay full replay cost per trial. No replay work
  occurred.
- **(a) "account-id-not-resolvable in bot_state" REFUTED:** all 11 affected names resolve against current
  `bot_state` via `database.normalize_name` (verified hash-by-hash), and the affected set varies week to week
  for the SAME symphony — inconsistent with a name-resolution failure.
- **Actual mechanism (evidence: weekly `cache/synthetic_history_v4_*.json` per-symphony day counts):** the
  weekly bundle is built from each symphony's CURRENT holdings; when a symphony rotates into a young/thin-history
  ticker, its replay day coverage truncates to that ticker's data availability. Per-week day-count vs zero-triad
  correlation is exact on 07-18 (corp-chaos-5 111d → the only triad), 07-24 (hunted-cascades 113d + golden-age
  190d → the only 2 triads among completed), and 07-31 (all six ≤190d symphonies triaded; all five ≥249d healthy;
  bundle shrank to 14.1 MB vs 84.8 MB on the all-healthy 06-30). The 07-10 case (corp-chaos-5 106d contiguous
  late-start block → healthy, vs 190d gap-in-the-tail symphonies → triad) proves it is gap POSITION relative to
  the walk-forward train/validation slices that matters, not raw count: an empty slice replays to 0.0 for every
  trial → `train==fallback==default==0.0`.
- **Follow-up candidate (NOT built this cycle, surfaced for the operator's backlog):** a minimum-per-slice
  coverage guard in `run_autotuner` that records an honest "insufficient replay coverage" outcome instead of
  persisting a fabricated-looking 0.0 triad row. Interacts with BL-8's never-adopted signal (zero-triad rows
  currently masquerade as real evaluations).
