# Lens Data — Derivatives Producer

**Epic:** B — lens data completion · **Parent:** [lens-data-completion.md](lens-data-completion.md)
· **Feeds:** the `derivatives_analyst` (Market Prism Phase 2) · **Status:** 🔴 not started
(deferred until Epic A unblocks; not a hard blocker — analyst runs `limited-inputs` without it).

## Goal

Produce real options/vol/positioning signals (e.g. vol term structure, skew, put/call) for the
universe so the Prism `derivatives_analyst` reasons about actual positioning instead of an
empty lens.

## Acceptance criteria

1. **Source research FIRST** — identify a $0/mo derivatives/vol source (researcher task) and
   pin its contract before any client code. This is the highest-uncertainty producer; do not
   skip the recon.
2. A producer in the Cycle-4 lens data layer fetches the chosen signals and normalizes them to
   a documented shape the `derivatives_analyst` consumes.
3. Honest-availability empty-state when the source is unavailable — no fabricated values.
4. Fixture-testable (captured-from-producer or schema-derived + runtime validator). Tests
   assert shape/format/presence, never hardcoded producer values.
5. Off-execution-path; bounded retries; no blocking I/O on any execution path.

## Team / approach

Researcher (pin the free source + contract) → Toxic Pair TDD: test-writer + implementer +
`composer-alpaca-integration` (or fitting integration specialist) + doc-gen.

## Risk callout

A reliable $0/mo derivatives source may not exist; if recon finds none, this producer is
reframed (dropped or replaced with a proxy) rather than invented — adopt-existing-contracts,
never fabricate. Decide after recon.

## Dependencies

Sequenced after Epic A's observed proof. Independent of the other two lens producers — can run
as a parallel agent.
