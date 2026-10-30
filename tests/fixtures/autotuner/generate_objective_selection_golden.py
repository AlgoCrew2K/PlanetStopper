"""Record the objective the Ref's inline rule selects for each facet case.

The inputs below are authored here; the outputs are never typed. Each output is the value
the rule inside ``run_autotuner`` at the Ref commit computes, obtained by extracting that
block from ``git show <ref>:autotuner.py`` and executing it over the case's facets.

Usage (from the repository root):
    python tests/fixtures/autotuner/generate_objective_selection_golden.py 37c8e983
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import textwrap

_START = '_utility_family = _facets_by_name.get("utility_family", "")'
_END = "# Apply optuna_001 archive migration"

# (case name, facets_by_name) -- one per distinction the rule draws.
CASES: list[tuple[str, dict]] = [
    ("objective_kind_crra_eu", {"objective_kind": "crra_eu"}),
    ("objective_kind_sortino_loss_aversion", {"objective_kind": "sortino_loss_aversion"}),
    (
        "objective_kind_wins_over_crra_family",
        {"objective_kind": "sortino_loss_aversion", "utility_family": "CRRA"},
    ),
    (
        "objective_kind_wins_over_other_family",
        {"objective_kind": "crra_eu", "utility_family": "CARA"},
    ),
    ("objective_kind_unrecognised_value_kept", {"objective_kind": "some_other_kind"}),
    ("family_upper_crra", {"utility_family": "CRRA"}),
    ("family_lower_crra", {"utility_family": "crra"}),
    ("family_mixed_case_crra", {"utility_family": "Crra"}),
    ("family_padded_crra_is_not_crra", {"utility_family": " CRRA "}),
    ("family_other", {"utility_family": "CARA"}),
    ("family_empty", {"utility_family": ""}),
    (
        "empty_objective_kind_falls_through_to_crra",
        {"objective_kind": "", "utility_family": "CRRA"},
    ),
    (
        "empty_objective_kind_falls_through_to_other",
        {"objective_kind": "", "utility_family": "CARA"},
    ),
    (
        "neither_facet_other_facets_present",
        {"gamma": "2.0", "wealth_argument": "compounded_return"},
    ),
    ("no_facets", {}),
]


def _inline_rule_source(ref: str) -> str:
    source = subprocess.run(
        ["git", "show", f"{ref}:autotuner.py"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    start = source.index(_START)
    end = source.index(_END, start)
    return textwrap.dedent("    " + source[start:end])


def main(ref: str) -> None:
    block = _inline_rule_source(ref)
    cases = []
    for name, facets in CASES:
        scope = {"_facets_by_name": dict(facets)}
        exec(block, {}, scope)  # noqa: S102 - executing the Ref's own rule is the point
        cases.append({"name": name, "facets": facets, "objective_kind": scope["_objective_kind"]})
    out = {
        "_provenance": (
            f"Recorded by executing the objective-selection block of run_autotuner at {ref} "
            "over each authored facet case. See tests/fixtures/autotuner/PROVENANCE.md."
        ),
        "ref": ref,
        "cases": cases,
    }
    target = pathlib.Path(__file__).with_name("objective_selection_golden.json")
    target.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target} ({len(cases)} cases)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "37c8e983")
