# Provenance: autotuner fixtures added for the objective-selection rule

## objective_selection_golden.json

Class: recorded output of the Ref's own code over authored inputs.

- Inputs: the 15 facet cases in `generate_objective_selection_golden.py` (`CASES`). Each case is one
  distinction the rule draws (an `objective_kind` facet present, empty or unrecognised; a
  `utility_family` facet in upper, lower, mixed and padded case; neither facet; no facets).
- Outputs: never typed. `generate_objective_selection_golden.py` extracts the block that starts at
  `_utility_family = _facets_by_name.get("utility_family", "")` from `git show 37c8e983:autotuner.py`
  (the inline rule inside `run_autotuner` before it moved to `database.objective_kind_for_facets`)
  and executes it over each case; `objective_kind` is the value that block left in `_objective_kind`.
- Regenerate: `python tests/fixtures/autotuner/generate_objective_selection_golden.py 37c8e983`.
- Tolerance: exact string equality.
- Used by: `tests/autotuner/test_objective_selection_rule.py`.

A case records what the rule did at the Ref, including behaviour a reader might not choose
(`" CRRA "` with padding is not CRRA; an unrecognised `objective_kind` is kept as given). The rule
moved without changing; any change to a recorded output is a behaviour change and needs the PM.
