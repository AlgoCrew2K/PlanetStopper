# Spec Review Checklist

Tier marks: F feature, T task, E epic overview; `always` and `conditional` follow the tier template. Each check names its evidence; a check whose evidence was not obtained is a QUESTION, never a pass. A conditional section reading `none: <reason>` passes only when the reason holds against the code.

## Header lines [F T E: always]
- Every key the tier template lists is a plain `Key: value` line above the first `##` heading: no bold, bullet or table. Evidence: the line. Reason: the unfilled-line grep matches only that shape.
- `Repos:` names every repo that a criterion, contract change or migration touches. Evidence: each repo named in the body appears in the header.
- `Base:` and `Depends on:` name a branch, plan file or PR that exists. Evidence: the ref, file or PR.
- F: `Type:` is one of the template's values; `Story:` is an id or `none: <reason>`.
- T: `Feature:` points at an existing feature spec. Evidence: the file.

## Trigger [F T: always]
- States what prompted the work and where the request is recorded. T: names the feature criterion or finding it serves, and that criterion exists in the feature file. Evidence: the record location or the feature's AC id.

## Summary [F: always]
- One sentence names the single thing made true, who uses it and through which surface; each capability bullet maps to at least one criterion. Evidence: bullet-to-AC map. Reason: a capability with no criterion is either unbuilt or built from inference.

## Acceptance Criteria (Building) [F T: always]
- Each AC-N names one surface (page, route, endpoint, job, command) and one observable result there. Evidence: the surface exists at a cited path, or the spec says it is created. Reason: a criterion without a surface and an observable cannot become one test row.
- The empty, invalid, not-found, denied and failure cases each have a criterion or are named in Not Building.
- No criterion contradicts a contract the code enforces: validation, uniqueness, refusal, constraint, authorization. Evidence: the enforcing `repo:path:line`.
- Every stated observable (status, refusal code, message, field, default, rounding) matches what the code returns, or the spec states that it changes. Evidence: the return site.
- A criterion states behavior; a file, function, class, library or algorithm it names is a Gate-2 finding unless that name is the observable contract (a route, a wire field).
- T: no criterion is absent from the feature. Reason: a new criterion means the task has outgrown its tier.

## Not Building [F T E: always]
- F: four labelled lists: non-goals, behavior that stays as it is, deferred items, protected surfaces. T: what is left to the feature or other tasks, and behavior that stays as it is. E: suite exclusions, unchanged behavior, protected surfaces.
- Each "stays as it is" item cites the current behavior, and no criterion changes it.
- No item is something the code forces the change to do: a constraint, trigger, cascade, required consumer update or migration. Evidence: the forcing path. Reason: an exclusion the code forces ships as unplanned work or a broken build.
- No criterion, contract change or migration touches a protected surface.

## Worked Example [F: always]
- One concrete input in the feature's terms and its exact observable result, then the main failure case likewise.
- Every value the example shows (defaults, rounding, identifiers, codes, messages) is produced by the cited code or required by a criterion, and no result contradicts one. Evidence: the line producing each value. Reason: the example is the statement of intent the test-writer copies first.

## Authorities Touched [F: conditional] / Authorities and Contracts Touched [T: conditional]
- Each value's authority is the source the code reads today, or the spec says it moves. Evidence: the read site.
- "What proves no second source" is a search for every other read and write site of the value. Evidence: the search and its hits.

## Fail-Closed Behaviour [F: conditional]
- Each new refusal names the input, the typed refusal (code and status) and the point in the path where it refuses.
- The section includes every refusal the existing write path returns for the inputs the feature sends, by code and cited line (see Refusal enumeration). `none` holds only when the feature sends no input to a write path.

## Contract Changes and Consumers [F: conditional]
- Each changed route or wire shape shows before, after and backward compatibility.
- Every consumer by repo is listed. Evidence: a caller search per repo for the route, type or field; a consumer the search finds that the spec omits is a finding.
- `none` holds only when no criterion changes a wire shape or route.

## Sibling Repos and Landing Order [F: conditional]
- Each repo in `Repos:` has a row when there are several; providers land before consumers; each "what breaks" traces to a consumer found above.

## Migrations Required [F: conditional]
- Every criterion needing a new column, constraint, index or data change has a migration here, numbered as the repo's existing files at the verified SHA, with idempotency and rollback stated. Evidence: the migrations directory listing.

## Configuration and Enablement [F: conditional]
- Each key has a default, environments and an owner; its name collides with no existing key; the code reads it where the spec says. Evidence: the config loader or schema.

## Breaking Changes [F: conditional]
- Every incompatible change in Contract Changes appears here; `none` holds only when every contract change is compatible.

## Security Considerations [F: always]
- Walks input validation and injection, authorization, data exposure, client-side rendering, and abuse or rate limits; each that applies names the criterion covering it.
- A claim about who may call a route cites the guard that protects it today. Evidence: the guard's line.

## Prerequisites [F: conditional]
- Each prerequisite states the refusal a user sees when it is missing, matching the code. Evidence: the refusal site.

## Design-System Mapping [F: conditional, UI only]
- Every visible element has a row; each primitive or token exists in the design system's exports, or is listed as one to create. Evidence: the export. `none` on a feature with a UI surface is a finding.

## Definition of Done (Verification) [F T: always]
- One row per AC, no AC missing and no row for an AC that does not exist; each row's assertion is its criterion's observable.
- Gates are listed; every fixture has a provenance (captured from the real producer, or a cited source). F: data evidence for pipeline changes; for UI changes, the screenshot-against-design comparison per changed screen at stated viewports and themes.

## Success Measures [F: conditional]
- Each measure names where it is read; that source exists.

## Known-Unconfirmed and Standing Risk [F T: conditional]
- Each item is unverifiable, not merely unchecked: an item you can settle from code becomes a finding. No criterion depends on an item listed here.

## Operator Questions [F: conditional]
- Each question has a default, applied in the body. A question the code already answers is a finding carrying the answer's line.

## Decisions [F T: conditional] / Shared Decisions [E: conditional]
- No decision contradicts a contract the code enforces. Evidence: that line.

## Docs to Update [F: conditional]
- Named docs exist; docs describing the changed behavior are listed. Evidence: a search of the docs tree for the behavior's names.

## Follow-Ups [F: conditional]
- Every deferred Not Building item has a row with its tracking location; no criterion needs a follow-up.

## Epic Overview [E: always]
- North Star: one paragraph on the end state and why it is split.
- Plans and Dependency Graph: columns `#`, Plan, Repos, Base, Contract exposed upward, Status, Depends on; one row per plan file and one plan file per row. Evidence: the suite directory listing. Reason: the overview is the epic's record.
- Base and Depends on form no cycle, and providers precede consumers.
- Stack and Merge Rules, Suite Gate, and Assembly and Close each state what their template line requires; a deviation carries its reason.
- Every plan passes the feature blocks on its own and does not restate the overview.

## New-Format Plans [F T E: a document with `Requirements as Given`]
`plan-trace check` has already judged identifier form, G and R citation and mapping, scenario and negative-coverage presence, matrix cell form, Definition of Done rows and question fields. These checks need judgment, and each is blocking:
- **Source whole.** `Requirements as Given` equals the source and answers in your own brief, sentence for sentence. A named `<plan-name>.source.md` is the source, that file read whole: an item it states that the inventory lacks blocks, whatever a summary says. Reason: a sentence the writer dropped passes the writer's copy.
- **Narrowing judged.** Each mapped criterion keeps its item's meaning; one that covers less than its item under a row not marked `narrowed: <what is left out>` blocks. Reason: a quietly narrowed mapping passes every id check.
- **Scenarios cover the rule.** Every case of a family (absent, null, empty; each state a threshold separates) has a row, and each `n/a` reason holds against the enforcing path: "no concurrent writes" fails when two routes write the record. Evidence: the enforcing line.
- **Assertable.** Each scenario names a surface, trigger and observable, and states a producer's value as a format or relation, never a sampled literal.
- **Matrix complete.** Every write route in the refusal enumeration is a column of each rule it enforces.
- **Sensitive.** A criterion on authentication, authorization, money, pricing, or personal or sensitive data needs a `Sensitive:` value naming it, agreeing with Security Considerations.
- **Evidence summaries.** Each carries its numbers and its public source.
- **Earlier criteria on the same surface.** May read `none: <reason>` only after this search. Search the plans root and its archive for plans whose criteria touch a screen, endpoint or record this plan changes. A plan with such surfaces and no `Earlier criteria on the same surface` section blocks, as does an earlier criterion the section omits or marks `kept` while a criterion here contradicts it. Reason: nothing else reconciles new criteria with delivered ones.
- **Workflow file.** Runs only when Design-System Mapping lists a screen. Per screen, read whole the file its User Tasks `Workflow (<screen>):` line names, `PROJECT/.claude/workflows/<screen>.md` (PROJECT: the directory holding the plans root's `feature-plans/`, else the repository root). The file counts only with a `Confirmed:` line with a date: an undated file is an agent's draft, not the operator's workflow. A screen reading `no workflow file:`, a named file that does not exist, a file with no `Confirmed:` line or none with a date, and a file that cannot be read each draw one QUESTION naming the screen: never blocking, never a pass. For a confirmed file each of these is CONFIRMED and blocking, citing the workflow file's line: an affected task (one that uses an input, action or panel a criterion adds, changes or removes) that is neither a User Task nor in Not Building; a User Task step whose action or expected result differs from the workflow's, quoting both; an input or action the workflow lists for an affected task that is in neither a criterion nor Not Building. Only a Not Building entry exempts; a task a User Task covers and a workflow task no criterion touches (not affected) draw nothing.
- **Classes and questions.** Each defect class's sibling search would find its siblings; no `Open Questions` row is one the code already answers.

## Cross-Section Checks [F T E]
- **Closed scope.** Every behavior described anywhere (Summary, Worked Example, Fail-Closed, Contract Changes) is a criterion or in Not Building; nothing is built for a Not Building item; every criterion has something built and a DoD row.
- **Negative claims at the enforcing path.** Every negative claim is traced as Verification Procedure step 5 requires. Evidence: the enforcing line, or the chain read with no guard found. Reason: only the enforcing path records what happens; vocabulary files, comments and docs record intent.
- **Refusal enumeration.** Every refusal is listed with code, status and line and covered, handled or named unreachable, as Verification Procedure step 6 requires. Reason: an unknown refusal ships as an unhandled error.
- **One value, one spelling.** A code, field, status or limit has the same name and value everywhere it appears.
