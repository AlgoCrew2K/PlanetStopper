# Provenance of the backtest slippage calibration fixtures

## request_sequences.json

Class: captured behaviour of the unchanged code. For each of the eleven entry points that send a
Composer backtest (Strategy Builder route and weekly run, Asset Swaps route and weekly run, Logic
Changes route and weekly run, Frontrunner Builder route and weekly run, the incubation tick, the
tenure review route and tick), it records what the surface asked Composer and how its gate
decided, at the plan's ref `c3e12882141ae601f900fdc13e0c342462a739bb`.

- Producer: `tests/fixtures/backtest_slippage_calibration/capture_request_sequences.py`. It runs
  each runner of `tests/advisors/_slippage_surface_worlds.RUNNERS` under pytest with `-n0` and
  merges the entries. It refuses to run unless `git diff --name-only
  c3e12882141ae601f900fdc13e0c342462a739bb -- . ':(exclude)tests' ':(exclude)feature-plans'`
  prints nothing, and the same check runs inside every captured test.
- Captured on: `d8dbc16828deaa0fec34bf058b89a6785b97f88f` (batch B's RED commits; that commit
  changes tests only). The guard output was empty before the run: no file outside `tests/` and
  `feature-plans/` differed from the ref. No golden changes after the implementation lands.
- Per request: the URL, the body form (`json` or `data`), every body field except the tree and
  `slippage_percent` (capital, fee flags, broker, version, start and end dates), the tree's
  ticker set and the SHA-256 of the tree with its `id` values replaced by the order of their
  first appearance in the entry point's request sequence (the schema constructors mint uuid4 ids;
  the real symphony trees run to 100 KB, so the digest stands for the text). `slippage_percent`
  is deliberately absent: the golden must hold before and after the default moves.
- Per entry point, `outcome`: the gate decisions the run reached (route answers: candidate id,
  decision, rejection reason, counts; jobs: the stored advisory rows as role, verdict and count;
  the incubation tick: the candidate's status and days observed; the frontrunner runs: proposals
  queued). Run ids, timestamps and metrics are never recorded.
- Composer replies are captured producer output served by `ComposerRouter` at the `requests`
  boundary: the inline backtest capture `tests/fixtures/composer/backtest_inline_v1.json` for the
  Strategy Builder, Asset Swaps, Logic Changes and Frontrunner worlds (a long series, so the
  gates judge), and the router's default captured tenure record
  (`tests/fixtures/composer/tenure-review/case5_backtest_dated_compact_nOyb55RMGVCKPiYXv7TI.json`)
  for the incubation tick and the tenure review. Every backtest of a run gets the same reply, so
  a candidate and its SPY baseline carry identical returns; the gate outcomes are therefore the
  ones that follow from that, and nothing here asserts a figure.
- Anthropic replies are the captured Messages fixtures under `tests/fixtures/anthropic` with the
  plan list or the edit replaced by conforming ones (derived and schema-validated by the shared
  harnesses). The frontrunner incumbent and the incubation candidate are test-stated trees whose
  content no assertion reads.

Comparison: parsed JSON on both sides, so line endings cannot differ.
