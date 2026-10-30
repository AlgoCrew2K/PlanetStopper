# Provenance: records the data-truth tests build at run time

This folder holds no data files. The tests build their records at run time from the sources
below, and this file is where each derived record names its source, the fields changed and the
reason (SC-10 rule 4). PM sign-off of every derived record below is requested in the handoff.

## Sources used unchanged

| Source | Class | Used for |
|---|---|---|
| `tests/fixtures/prod_droplet/bot_state_positions.json` | captured from the droplet (`capture_from_droplet.py`) | live symphony names and Composer hashes; picked by property (trailing space, apostrophe, parentheses), never by typed name |
| `tests/fixtures/advisors/frontrunner/real_tree_07_iaSOOUsmnCJHiZvbrWfs.json` | captured Composer tree | Frontrunner proposal tree; Strategy Builder candidate tree |
| `tests/fixtures/autotuner/dsr_surfacing/autotune_run_legacy_nulls.json` | schema-derived, provenance header in the file | a legacy run with null score columns |
| `tests/fixtures/prism_verifier/verify_cited_numbers_mixed_classifications.json` | schema-derived (numeric values synthetic, shapes from the lens builders); the file says so | council claims and lens sections fed to the real `verify_cited_numbers` |

## Derived records

| Test | Source record | Fields changed | Reason |
|---|---|---|---|
| verification rows (`_producer_row`) | `verify_cited_numbers` output over the fixture above | `cited_numbers` limited to the claims the fixture records as pass, as unverifiable, or as flagged; or emptied | one real producer row per verdict the pill distinguishes |
| damaged verification summary | the pass-plus-unverifiable producer row | `n_pass` or `n_checks` deleted, `n_pass` set to null, `n_checks` set to the string `"32"` | the summary-field-missing and wrong-type edges the pill must survive |
| unclassified check | the producer's pass check | nine copies re-indexed `PASS_0..8`; one copy renamed `UNCLASSIFIED` with `classification` `unclassified`; summary and verdict recomputed with the producer's own `_summarize` and `_derive_verdict` | the plan's "one check of no recognised class" edge, which `_summarize` counts in `n_checks` and in no bucket |
| Strategy Builder cards | rows written by `_persist_survivor` and `_persist_rejected` from authored `CandidateInfo` inputs (objective, template, metrics are inputs, never asserted) | none | stored-card label cases |
| Strategy Builder row, key removed / null / unknown | the producer's row for a base candidate | `objective` or `template_id` deleted, set to null, or set to `legacy_mix` / `community`; `candidate_id` and `subject_id` renamed so the original row stays | the absent-and-unknown edges for "not recorded" |
| Strategy Builder failed-backtest card | the producer's rejected row | `backtest_error` added | no writer sets `backtest_error` on a Strategy Builder row at the Ref (the template branch keys on it), so the only way to render that card is a row with the key |
| autotune runs | rows written by `database.save_autotune_run` | authored scores and decisions (inputs, asserted only as formatted) | one run per label case |
| route row, verdict key deleted | the real `GET /api/autotune-runs` row | `frozen_eval_verdict` deleted | the missing-field edge |
| bot_state with metadata entries | `bot_state_positions.json` | `last_market_close_snapshot` and `post_mortem_run` added as dict entries with no `name` | the real engine writes these top-level non-symphony entries; name resolution must skip them |
| bot_state without the incumbent | `bot_state_positions.json` | one entry removed | the "incumbent no longer held" edge |

## Held

Scenario S-7.11 (live Strategy Builder run card) is held until the PM commits a
`POST /ai-advisor/strategy-builder/run` capture taken on the droplet after the model-unpin deploy.
No test stands in for it.
