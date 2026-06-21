# Planet Stopper — Open Backlog
**As of:** 2026-06-19
**Source of truth for shipped/obsolete history:** `.claude/backlog-reconciliation.md`

This file lists only work that is genuinely open. Plans renamed to `.completed.md` or `.obsolete.md`
are archived in this same directory; see the reconciliation doc for the full classification record.

---

## Blocked on operator action

### `security-review.md` — S-1 (operator action required)
S-1: Discord webhook credential has been in git history since `c0ec631`. Operator must
rotate the webhook URL and explicitly approve the force-push scrub needed to remove it
from history. No code change until operator provides the new credential + go-ahead.

---

## Ready to build now

### `tech-debt-cleanups.completed.md` — C3b + C3c
C3a is a confirmed no-op (stash empty). C3c shipped (chore/tech-debt-c3bc). Remaining:
- **C3b:** formal route self-skip closure — write the route-level RED test for the
  302-redirect behaviour (AC-4b/AC-5b never written).
Small; Tier 1.

### `security-review.md` — S-2 + DEP-1 (buildable now, independent of S-1)
- **S-2:** commit a `.env.example` template (no real credentials). **[DONE this sweep — `.env.example` committed]**
- **DEP-1:** tighten `anthropic~=0.85.0` and `feedparser>=6.0` to exact `==` pins in
  `requirements.txt` / `pyproject.toml`.

### `test-reload-leak-remediation.md` — endemic `importlib.reload`-per-test memory leak
Pre-existing C1 test-infra debt: `tests/advisors/` files call `importlib.reload(...)` per
test → orphans heavy modules (`pymongo`/`atlas_cache`) → unbounded growth that OOMs
single-process full-tree verification (`-p no:xdist`). Sites: `test_community_strats.py`
(35), `test_atlas_cache.py` (1), `test_community_strats_timeout.py` (1). The
`test_universe_provider.py` portion is already fixed (commit `e52e17c`, the reference
pattern). Dedicated remediation cycle — own branch/team; the `community_strats` reloads are
load-bearing for patch-visibility (real test-breakage risk). Discovered by the C5 full-tree
gate, 2026-06-21.

---

## Deployment follow-on

### Droplet wipe-and-collect
Full E2E validation of the production droplet: confirm daemon healthy, council timer
firing at 03:00, no two-daemon conflict, MARKET_PRISM rows arriving nightly, Overview
tab rendering council output. Requires operator access to the droplet.
