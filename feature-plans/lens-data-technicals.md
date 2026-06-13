# Lens Data — Technicals Producer

**Epic:** B — lens data completion · **Parent:** [lens-data-completion.md](lens-data-completion.md)
· **Feeds:** the `technicals_analyst` (Market Prism Phase 2) · **Status:** 🔴 not started
(deferred until Epic A unblocks; not a hard blocker — analyst runs `limited-inputs` without it).

## Goal

Produce real price/trend/breadth technicals for the universe so the Prism `technicals_analyst`
reasons about actual market structure (moving-average posture, breadth, momentum) instead of an
empty lens.

## Acceptance criteria

1. A producer in the Cycle-4 lens data layer computes documented technicals (e.g. MA posture,
   breadth, momentum) for the universe, normalized to a shape the `technicals_analyst` consumes.
2. Honest-availability empty-state when source data is missing — no fabricated values.
3. Fixture-testable (captured-from-producer or schema-derived + runtime validator). Tests
   assert shape/format/presence; computed technicals derive from fixture, never hardcoded.
4. Off-execution-path; bounded retries; no blocking I/O on any execution path.
5. Source: reuse the existing Alpaca historical fetch / `synthetic_history.py` window where it
   already provides the needed bars, rather than adding a new price source — confirm at recon.

## Team / approach

Toxic Pair TDD: test-writer + implementer + `risk-engine-specialist` (technicals are math —
named constants + golden-fixture per the math-layer rule) + doc-gen.

## Dependencies

Sequenced after Epic A's observed proof. Independent of the other two lens producers — can run
as a parallel agent.
