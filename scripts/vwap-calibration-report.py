"""VWAP calibration sweep report generator.

Advisory-only: reads study output rows produced by autotuner.run_calibration_sweep
and renders a per-symphony summary. MUST NOT write to the state DB, MUST NOT import
the live execution path, MUST NOT apply any constant to live settings.

Usage (CLI):
    python scripts/vwap-calibration-report.py --rows-json <path>
                                               [--out <path>]

Programmatic:
    from scripts.vwap_calibration_report import generate_report
    enriched_rows = generate_report(rows)

Each row is a dict produced by run_calibration_sweep with at minimum:
    symphony_id, param_name, current_value, proposed_value,
    expected_trigger_freq_change, pbo_veto_status, flag_for_operator_review,
    haircut_outcome, study_name
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any


def generate_report(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the sweep rows as an enriched advisory report.

    The rows are produced by autotuner.run_calibration_sweep. This function
    passes the rows through unchanged — it is the minimal advisory pass-through
    required by AC-2/AC-3. Callers may render the result via _format_markdown
    or consume the dicts directly.

    Advisory-only: no DB writes, no live-engine imports, no constant application.

    Args:
        rows: List of report row dicts from run_calibration_sweep.

    Returns:
        The same list of row dicts, unchanged.
    """
    if not rows:
        return []
    return list(rows)


def _format_markdown(rows: list[dict[str, Any]]) -> str:
    """Render the report rows as a Markdown document for human review.

    Groups rows by symphony_id. Surfaces pbo_veto_status and
    flag_for_operator_review prominently so the operator cannot miss them.
    """
    if not rows:
        return "# VWAP Calibration Report\n\n_No rows — sweep produced no output._\n"

    # Group by symphony
    by_sym: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        sym = row.get("symphony_id", "UNKNOWN")
        by_sym.setdefault(sym, []).append(row)

    lines: list[str] = ["# VWAP Calibration Report", ""]
    lines.append(f"Symphonies: {len(by_sym)} | Param rows: {len(rows)}")
    lines.append("")

    for sym_id, sym_rows in sorted(by_sym.items()):
        first = sym_rows[0]
        pbo_vetoed = first.get("pbo_veto_status", False)
        flagged = first.get("flag_for_operator_review", False)
        haircut = first.get("haircut_outcome", "unknown")
        study = first.get("study_name", "")

        lines.append(f"## {sym_id}")
        if pbo_vetoed:
            lines.append(
                "> **PBO VETO**: haircut found no qualified winner — proposal is informational only."
            )
        if flagged:
            lines.append(
                "> **OPERATOR REVIEW REQUIRED**: proposed trigger frequency is >2x current."
            )
        lines.append(f"- Study: `{study}`")
        lines.append(f"- Haircut outcome: `{haircut}`")
        lines.append("")
        lines.append("| param_name | current_value | proposed_value | trigger_freq_change |")
        lines.append("|---|---|---|---|")
        for row in sym_rows:
            lines.append(
                f"| {row.get('param_name', '')} "
                f"| {row.get('current_value', '')} "
                f"| {row.get('proposed_value', '')} "
                f"| {row.get('expected_trigger_freq_change', '')} |"
            )
        lines.append("")

    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> None:
    """CLI entry point: read rows from JSON, write markdown report."""
    parser = argparse.ArgumentParser(
        description="Render VWAP calibration sweep rows as a Markdown advisory report."
    )
    parser.add_argument(
        "--rows-json",
        required=True,
        help="Path to a JSON file containing the list of rows from run_calibration_sweep.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path for the Markdown report (default: stdout).",
    )
    args = parser.parse_args(argv)

    rows_path = pathlib.Path(args.rows_json)
    if not rows_path.exists():
        print(f"ERROR: --rows-json path not found: {rows_path}", file=sys.stderr)
        sys.exit(1)

    raw = json.loads(rows_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        print("ERROR: rows JSON must be a list of dicts.", file=sys.stderr)
        sys.exit(1)

    validated = generate_report(raw)
    md = _format_markdown(validated)

    if args.out:
        out_path = pathlib.Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"Report written to {out_path}")
    else:
        print(md)


if __name__ == "__main__":
    _main()
