# Lens Data — GDELT Tone / Sentiment Producer

**Epic:** B — lens data completion · **Parent:** [lens-data-completion.md](lens-data-completion.md)
· **Feeds:** the `sentiment_analyst` (Market Prism Phase 2) · **Status:** 🔴 not started
(deferred until Epic A unblocks; not a hard blocker — analyst runs `limited-inputs` without it).

## Goal

Produce a real news-sentiment directional signal from GDELT tone for the relevant universe, so
the Prism `sentiment_analyst` reasons about actual sentiment instead of an empty lens. $0/mo
source.

## Acceptance criteria

1. A producer in the Cycle-4 lens data layer (`advisors/lens_pipeline.py` style) fetches GDELT
   tone for the universe and normalizes it to a documented directional score shape the
   `sentiment_analyst` consumes.
2. Honest-availability empty-state: when GDELT is unreachable/empty, return a clear unavailable
   marker — NEVER fabricated tone.
3. Fixture-testable with captured-from-producer or schema-derived + runtime-validated fixtures
   (NOT parser+fixture co-design — Gate-1 fail). Tests assert shape/format/presence, never
   hardcoded tone values.
4. Off-execution-path; bounded retries (recall the persistent-429 infinite-loop crash); no
   blocking I/O on any execution path.

## Team / approach

Toxic Pair TDD (new codepath): test-writer + implementer + `composer-alpaca-integration` (or a
fitting integration specialist) + doc-gen. Precede client work with a researcher to pin the
GDELT contract (endpoint shape, rate limits, tone field semantics).

## Dependencies

Sequenced after Epic A's observed proof (exclusive-focus). Independent of the other two lens
producers — can run as a parallel agent.
