> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Fleet Banner Dismiss — UI Regression Escalation

**Branch:** hotfix/fleet-banner-and-quantstats
**Authored:** 2026-05-24
**Escalating to PM** — per hotfix worker mandate, CASE B: UI degradation confirmed.

## Expected user-facing behavior

Operator clicks "Dismiss" on the fleet correlation banner. The banner disappears and
stays gone until the next genuine fleet correlation event trips. The dismiss action
sets `dismissed_at_et` in `fleet_alert_state` so `/api/state` returns
`fleet_correlation_alert: null` on subsequent polls and the banner stays hidden.

## Actual current behavior after commit 7930b72

Operator clicks "Dismiss". The route returns `{"status": "ok"}` (200). The banner
disappears for this poll cycle because the JS hides it on a 200 response. On the
NEXT `/api/state` poll (next JS tick, typically 5–10 seconds), `dismissed_at_et` is
still NULL in `fleet_alert_state`, so `/api/state` returns the alert as non-null and
the banner reappears. The dismiss button has no durable effect.

**In practice:** the operator cannot clear the fleet banner. It reappears on every
poll until the auto-clear timer fires (FLEET_CORRELATION_CLEAR_MINUTES, default 30).

## Root cause — what broke

Commit **7930b72** ("feat(dashboard-side-effect-ban): GREEN — remove write paths
from Flask routes") removed the `database.write_fleet_alert()` call from
`fleet_alert_dismiss()` without adding a background-thread replacement. The commit
message explicitly notes: "fleet_alert_dismiss: remove database.write_fleet_alert()
call; route returns ok without writing alert state (engine-exclusive write)".

The side-effect-ban cycle's RED test suite (`test_fleet_alert_state_table.py`,
Section 6 `TestDismissRouteWritesOnlyToFleetAlertState`, line 883) asserts that
`write_fleet_alert` must NOT be called on the Flask request thread, and that the
write "must be dispatched to a background thread." The GREEN implementation removed
the synchronous write but never added the background thread dispatch. The structural
test (`TestDismissRouteSourceIsolation::test_dismiss_route_source_references_write_fleet_alert`)
passes spuriously because the word `write_fleet_alert` appears in a comment, not a
call.

## The failing test and why it was not patched

`tests/dashboard/test_fleet_banner.py::TestFleetAlertDismissRoute::test_dismiss_route_clears_alert_in_state`
asserts `database.write_fleet_alert()` is called when the operator posts to
`/api/fleet-alert/dismiss`. This assertion reflects the user-visible contract (alert
must be cleared). It SHOULD fail given the current handler. Patching it to match the
current no-op handler would mask the regression.

The `tests/engine/test_fleet_alert_state_table.py` suite's Section 6 asserts the
OPPOSITE: write_fleet_alert must NOT be called from the request thread. Both test
sets are asserting incompatible states because the design intent (background thread
dispatch) was never implemented.

## Diff that broke it

```python
# Before 7930b72 (the R1 contract — correct behavior):
@app.route("/api/fleet-alert/dismiss", methods=["POST"])
def fleet_alert_dismiss():
    try:
        existing_row = database.read_fleet_alert()
        if existing_row is not None:
            existing_row["dismissed_at_et"] = datetime.now().strftime(...)
            database.write_fleet_alert(existing_row)
        return jsonify({"status": "ok"})
    ...

# After 7930b72 (broken — no persist):
@app.route("/api/fleet-alert/dismiss", methods=["POST"])
def fleet_alert_dismiss():
    # Dashboard side-effect ban: write_fleet_alert is engine-exclusive.
    # The dismiss acknowledgement is read-only from the route; the engine owns alert state.
    try:
        return jsonify({"status": "ok"})
    ...
```

## Suggested fix path

The side-effect-ban cycle's intent was that writes happen in a background thread, not
that dismiss becomes a no-op. Two implementation options:

**Option A (background thread — matches cycle intent):**
```python
@app.route("/api/fleet-alert/dismiss", methods=["POST"])
def fleet_alert_dismiss():
    def _dismiss_async():
        row = database.read_fleet_alert()
        if row is not None:
            row["dismissed_at_et"] = datetime.now(ZoneInfo("America/New_York")).isoformat()
            database.write_fleet_alert(row)
    threading.Thread(target=_dismiss_async, daemon=True).start()
    return jsonify({"status": "ok"})
```
This satisfies both the R1 AC-6 contract (dismiss writes dismissed_at_et) and the
side-effect-ban test assertion (write not called on request thread).

**Option B (direct write — simpler, revisit the side-effect-ban scope):**
The side-effect-ban was intended to prevent the dashboard from running the engine or
mutating trade state. Setting `dismissed_at_et` on a UI acknowledgement field is
categorically different from write operations on `bot_state`. A direct synchronous
write to `fleet_alert_state` for a UI dismiss is architecturally defensible and does
not violate the spirit of arch constraint 2 (dashboard is not an action surface for
live trades). The `test_dismiss_route_does_not_call_write_fleet_alert_on_request_thread`
test assertion would need updating to match a revised scope decision.

**Which option requires a TDD Quad:** Option A is an existing codepath change (the
handler exists, the behavior changes). It touches the fleet_alert_state write path.
Option B involves revising a test and the handler — also a codepath change. Either
option requires a TDD Quad cycle per project CLAUDE.md rules, not a hotfix worker.

## Tests that need to change (once PM decides option)

Either option:
- `tests/dashboard/test_fleet_banner.py::TestFleetAlertDismissRoute::test_dismiss_route_clears_alert_in_state`
  — must be updated to match whichever contract is chosen.

Option A only:
- `tests/engine/test_fleet_alert_state_table.py::TestDismissRouteSourceIsolation::test_dismiss_route_source_references_write_fleet_alert`
  — currently passes spuriously (comment match); the background thread implementation
  will make it pass for the right reason.

Option B only:
- `tests/engine/test_fleet_alert_state_table.py::TestDismissRouteWritesOnlyToFleetAlertState::test_dismiss_route_does_not_call_write_fleet_alert_on_request_thread`
  — must be revised to accept a synchronous write given a scope refinement.
