# Lens Data Completion (Cycle 2b) — Epic B Overview

**Status:** 🔴 deferred until Epic A (Market Prism) unblocks. Not a hard blocker for Prism
Phase 2 (analysts run `limited-inputs` where a producer is missing), but raises the quality of
the overnight read.

## Goal

Complete the off-hours lens data layer so each Prism analyst has real directional data to reason
about. Cycle 4 stood up the pipeline scaffold + some producers; three producers remain thin or
stubbed. Each is its own feature (separable data source → can be a parallel agent / its own
RED→GREEN cycle).

## Features (each has its own plan)

| # | Producer | Feeds analyst | File |
|---|----------|---------------|------|
| B1 | GDELT tone / sentiment | `sentiment_analyst` | [lens-data-gdelt-sentiment.md](lens-data-gdelt-sentiment.md) |
| B2 | Technicals (price/trend/breadth) | `technicals_analyst` | [lens-data-technicals.md](lens-data-technicals.md) |
| B3 | Derivatives (vol/skew/positioning) | `derivatives_analyst` | [lens-data-derivatives.md](lens-data-derivatives.md) |

## Cross-cutting rules (apply to all three)

- Honest-availability empty-state — NEVER fabricate data when a source is down.
- Provenance hard rule (Gate 1): fixtures captured-from-producer, schema-derived + runtime
  validator, or producer-owner signed off. Parser+fixture co-design is an automatic Gate-1 fail.
- Tests never hardcode producer-computed values (assert shape/format/presence).
- Off-execution-path; bounded retries (persistent-429 infinite-loop was a PC-crash root cause);
  no blocking I/O on any execution path.
- Each producer feeds the existing `advisors/lens_pipeline.py` data layer that the analysts pull.

## Dependencies

Sequenced AFTER Epic A's observed proof (exclusive-focus rule). The three producers are mutually
independent and can be dispatched in parallel once unblocked.
