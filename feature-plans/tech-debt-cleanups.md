# Tech-Debt Cleanups (grouped small items)

**Epic:** C — platform polish · **Status:** 🔴 not started. Grouped because each is small; each
section is independently dispatchable and can be its own micro-cycle.

---

## C3a — Reconcile `stash@{0}` cycle2-SEC WIP

- `git stash list` → `stash@{0}: On feature/multilens-advisor: cycle2 SEC two-call test WIP
  (pre-session; reconcile onto integrated branch later)`.
- This is uncommitted WIP for the SEC lens (two-call test) from before the integration. It must
  be reconciled onto the current integrated main (`d636ce3`) or explicitly dropped if
  superseded.
- **Action:** inspect `git stash show -p stash@{0}`, determine if the SEC two-call work is still
  needed (vs. what landed). If needed → apply onto a cycle branch, finish RED→GREEN, gate,
  merge. If superseded → drop with a note in DECISIONS.md. Do NOT leave it dangling.
- **Caution:** never `git stash` in a shared worktree (captures teammates' WIP). This stash
  predates the team work — handle it from the main worktree only.

## C3b — Route self-skip cleanup (`route.py`)

- Carried deferred item: an advisor route contains a self-skip branch that should be removed /
  simplified now that the unified SPA + advisor routes are settled.
- **Action:** locate the self-skip in the AI Advisor route layer (`app.py` advisor routes),
  confirm it is dead/redundant against the current SPA routing, remove it with a route-level
  RED test guarding the live behavior (mocked-module route tests miss live 500s — hit the route
  with the real producer module).

## C3c — Dead `higher_is_better` param in `_apply_lens_blend`

- `advisors/asset_swap_engine.py::_apply_lens_blend` carries a `higher_is_better` param flagged
  dead by the Cycle-3 reviewer (deferred cleanup).
- **Action:** confirm no caller passes it meaningfully, remove the param + dead branch, update
  callers, keep tests green. Behavior-preserving refactor (covered by existing tests → no new
  Toxic Pair required, but run the asset-swap suite).
- **Naming rule:** ensure no change-history naming creeps in.

---

## Approach

These can be a single "cleanup" cycle (one branch, sequential small commits) or three
micro-dispatches. Each is behavior-preserving except C3a (which may finish real WIP). doc-gen
notes any user-visible change in DECISIONS.md.

## Dependencies

None hard. Lowest priority — schedule around Epic A.
