# Frontrunner detector fixtures — provenance notes

`real_tree_NN_<symphony_id>.json` are trimmed derivatives of the operator's
11 real live Composer `/score` trees. Trimming replaces core-strategy asset
tickers with synthetic `CORE_ASSET_NNNN` placeholders while preserving the
real tree's structure (node ids, node count, if-node shape) byte-for-byte.

## Known drift: real_tree_01 / 03 / 04 / 06 (2026-07-16)

These 4 fixtures do **not** have exact node-id correspondence with a fresh
`/score` pull of the same symphonies (captured to
`.claude/fr-signals-inputs/fresh-trees-0716/` during the frontrunner-signals
cycle's falsification work). The fresh pull has FEWER total nodes than the
fixture in all 4 cases, and hundreds to thousands of fixture-only node ids
carry real, non-anonymized tickers absent from the fresh capture entirely
(e.g. real_tree_03's fixture-only ids include UDOW/UYM/XLY).

This is **not** a trimming bug specific to these 4 trees — it is most likely
Composer's own filter/select-driven leaves resolving differently across two
separate `/score` fetches taken on different days (a materialized `/score`
tree for a filter/select-based symphony is not guaranteed reproducible
fetch-to-fetch). Ruling (team-lead, 2026-07-16): **leave these 4 fixtures
untouched.** A fixture is a frozen regression tree, not a live mirror — it
stays valid as long as it is internally consistent, which the full test
suite passing against it proves. An id-diff patch against the fresh capture
would be unsafe here (no reliable node correspondence to patch against), and
a from-scratch regen would invalidate every hardcoded node-id assertion this
project's test files carry for these 4 trees (e.g. the LQD/XLV crossover
node `0d98c2bb...` in real_tree_06, referenced directly in
`test_frontrunner_extraction_walk.py`).

**Practical consequence:** node-id assertions in
`tests/advisors/test_frontrunner_extraction_walk.py`,
`tests/advisors/test_frontrunner_detector_ac3_rebuild.py`, and elsewhere
that target real_tree_01/03/04/06 anchor to THIS fixture's structure, not to
today's live symphony. They remain correct and stable for regression
purposes; they are not a live cross-check against the operator's current
tree shape.

**Follow-up (next cycle, not this one):** full fixture re-capture from fresh
`/score` pulls for all 11 trees, with a corresponding migration of every
hardcoded node-id assertion across the test suite. Tracked as a residual,
not scheduled here.

## Fixtures with confirmed, safely-patched hedge-ticker restoration

`real_tree_02/05/07/08/09/10/11` DID have exact node-id correspondence with
the fresh capture and were safely patched (commit `aad7c49b`, 2026-07-16):
some nodes had a genuine hedge/VIX-family ticker (e.g. UVIX, VXX) replaced
with a `CORE_ASSET_` placeholder by the trimming tool — a real trimming
defect, confirmed via node-level side-by-side comparison against the fresh
capture before any byte was changed. Restored in place; every other node
(including genuine core-strategy tickers correctly anonymized) is untouched.

## Confirmed-real, still-open production gap (not a fixture issue)

A separate, unrelated finding: the original `real_tree_04` BND-vs-SH leak
(a non-qualifying ticker-crossover if-node whose verbatim-copied sibling can
carry unrelated tickers into what the detector reports as fire-basket
content) was cross-checked directly against the fresh tree and confirmed
**real** — not a trimming artifact (see `DE-FR-SIGNALS-001`). The production
fix that landed (`42ffe560`) closes this only for the `CORE_ASSET_`-prefixed
case (fixture domain); real, non-prefixed core tickers can still leak the
same way in production. Tracked as a documented, low-severity, next-cycle
limitation — see the `xfail(strict=False)` tripwire test in
`tests/advisors/test_frontrunner_detector.py`
(`test_real_looking_core_tickers_do_not_leak_into_watched_tickers`), which
reproduces the exact mechanism with a synthetic tree and will XPASS the
moment a marker-free fix lands.
