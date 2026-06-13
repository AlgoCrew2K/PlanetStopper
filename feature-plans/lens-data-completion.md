# Lens Data Completion (Cycle 2b)

**Epic:** B — feeds richer reads to the Prism analysts · **Status:** 🔴 deferred until Epic A
(Market Prism) unblocks. Not a hard blocker for Phase 2 (analysts run `limited-inputs` where a
producer is missing), but raises the quality of the overnight read.

## Goal

Complete the off-hours lens data layer so each Prism analyst has real directional data to
reason about. Cycle 4 stood up the pipeline scaffold + some producers; three producers remain
thin or stubbed.

## Sub-deliverables (each can be a parallel agent / its own RED→GREEN)

### B1a — GDELT tone fetch (sentiment lens)
- Pull GDELT tone / news-sentiment signal for the relevant universe; normalize to a directional
  score the `sentiment_analyst` can consume. $0/mo source.
- Producer feeds the existing `advisors/lens_pipeline.py` data layer.

### B1b — Technicals producer (technicals lens)
- Price/trend/breadth technicals (e.g. moving-average posture, breadth, momentum) for the
  universe, normalized for the `technicals_analyst`.

### B1c — Derivatives producer (derivatives lens)
- Options/vol/positioning signals (e.g. vol term structure, skew, put/call) for the
  `derivatives_analyst`. Identify a $0/mo source first (researcher task).

## Acceptance criteria

1. Each producer returns a normalized, documented signal shape the corresponding analyst can
   pull, with an honest-availability empty-state (no fabricated data when the source is down).
2. Producers are fixture-testable (captured-from-producer or schema-derived + runtime
   validator — NOT parser+fixture co-design, which is a Gate-1 fail).
3. No hardcoded producer values in tests — assert shape/format/presence.
4. Off-execution-path; bounded retries; no blocking I/O on any execution path.
5. Tests never hardcode producer-computed values (rates/tone/percents derive from fixture or
   assert shape).

## Team / approach

Toxic Pair TDD (new codepaths): test-writer + implementer + `composer-alpaca-integration` (or
the relevant integration specialist) + doc-gen. Precede client work for any new source with the
matching researcher (`composer-api-researcher` style) to pin the contract.

## Provenance hard rule (Gate 1)

Fixtures must be captured-from-producer, schema-derived with a runtime validator, or
producer-owner signed off. Parser+fixture co-design is an automatic Gate-1 fail.

## Dependencies

Sequenced AFTER Epic A's observed proof (exclusive-focus rule). Pulls into the Phase-2 data
layer the analysts already read from.
