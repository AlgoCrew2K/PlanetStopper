# scripts/vwap-calibration-report

> Advisory report renderer for the VWAP calibration sweep: reads rows produced by `autotuner.run_calibration_sweep` and emits a per-symphony Markdown summary with PBO-veto and operator-review flags surfaced prominently.

**Source:** `scripts/vwap-calibration-report.py`
**Last updated:** 2026-06-19

## Overview

`scripts/vwap-calibration-report.py` is a standalone advisory utility. It has no dependency on the Flask daemon, the state DB, or the live execution path. Its role is to take the raw list of report dicts produced by `autotuner.run_calibration_sweep` and render them into a human-readable Markdown document grouped by symphony.

The script enforces three invariants by design:
- **No DB writes.** It never imports `database` or any live-engine module.
- **No parameter application.** It passes rows through unchanged — the operator decides whether to act.
- **Honest operator flags.** PBO-veto status and the trigger-frequency flag (AC-5, AC-7) are rendered as blockquote banners at the top of each symphony section so the operator cannot miss them.

## API Reference

### `generate_report(rows: list[dict[str, Any]]) → list[dict[str, Any]]`

The programmatic entry point. Accepts the raw row list from `run_calibration_sweep` and returns it unchanged. This is the minimal advisory pass-through required by AC-2/AC-3.

Advisory-only: no DB writes, no live-engine imports, no constant application.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `rows` | `list[dict]` | Row dicts from `autotuner.run_calibration_sweep` |

**Returns:** The same list of row dicts, unchanged. Returns `[]` for empty input.

**Example:**
```python
from scripts.vwap_calibration_report import generate_report
enriched_rows = generate_report(rows)
```

---

### `_format_markdown(rows: list[dict[str, Any]]) → str`

Renders the report rows as a Markdown document for human review. Groups rows by `symphony_id`. Surfaces `pbo_veto_status` and `flag_for_operator_review` prominently as blockquote banners at the top of each symphony section.

**Output format per symphony:**
```
## {symphony_id}
> **PBO VETO**: haircut found no qualified winner — proposal is informational only.  [if pbo_veto_status]
> **OPERATOR REVIEW REQUIRED**: proposed trigger frequency is >2x current.            [if flag_for_operator_review]
- Study: `{study_name}`
- Haircut outcome: `{haircut_outcome}`

| param_name | current_value | proposed_value | trigger_freq_change |
```

Returns `"# VWAP Calibration Report\n\n_No rows — sweep produced no output._\n"` for empty input.

---

### `_main(argv=None) → None`

CLI entry point. Reads rows from a JSON file, renders the Markdown report, and writes it to stdout or an output path.

**CLI usage:**
```
python scripts/vwap-calibration-report.py --rows-json <path> [--out <path>]
```

**Arguments:**
| Flag | Required | Description |
|------|----------|-------------|
| `--rows-json` | Yes | Path to a JSON file containing the list of rows from `run_calibration_sweep` |
| `--out` | No | Output path for the Markdown report; defaults to stdout |

Exits with code 1 if `--rows-json` path does not exist or the file content is not a JSON list.

## Row Dict Contract

Each dict in the `rows` input must carry at minimum these fields (all produced by `autotuner.run_calibration_sweep`):

| Field | Type | Description |
|-------|------|-------------|
| `symphony_id` | `str` | Symphony identifier |
| `param_name` | `str` | `"PARABOLIC_VELOCITY_THRESHOLD"` or `"VWAP_CROSS_HWM_PCT"` |
| `current_value` | `float` | Current live value |
| `proposed_value` | `float` | Best haircut-selected (or naive) value |
| `expected_trigger_freq_change` | `float` | Proposed minus current trigger count on validation fold |
| `pbo_veto_status` | `bool` | True when haircut found no statistically qualified winner (AC-5) |
| `flag_for_operator_review` | `bool` | True when proposed trigger frequency >2x current (AC-7) |
| `haircut_outcome` | `str` | `"cleared"`, `"no_trial_cleared"`, `"not_run"`, or `"no_completed_trials"` |
| `study_name` | `str` | `{timestamp}__{symphony_id}__calsweep` (AC-6) |

## Internal Dependencies

- `argparse`, `json`, `pathlib`, `sys` — stdlib only; no project imports
- `autotuner.run_calibration_sweep` — the upstream row producer (not imported here; rows arrive as JSON)
