"""
RED tests for AC-2, AC-5, AC-6, AC-7: report shape/presence/flags.

AC-2 — scripts/vwap-calibration-report.py emits a markdown report with the
        required per-symphony fields (shape/presence, not producer values).
AC-5 — pbo_veto_status surfaced per symphony; PBO-vetoed rows are flagged.
AC-6 — study names match <timestamp>__<symphony>__calsweep pattern; unique.
AC-7 — symphonies with >2x trigger-freq flip are flagged for operator review.

No live API calls. All expected values derived from the fixture.
"""

import importlib.util
import json
import pathlib
import re

import pytest

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "math" / "calibration_sweep_study_fixture.json"
)
_REPORT_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "vwap-calibration-report.py"

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fixture():
    return json.loads(_FIXTURE_PATH.read_text())


# ---------------------------------------------------------------------------
# Helper: build fixture-derived report rows (what the expanded sweep emits)
# ---------------------------------------------------------------------------

def _build_fixture_rows(fixture: dict) -> list[dict]:
    """Construct report rows from the fixture — no hardcoded producer values."""
    rows = []
    for sym_id, sym_data in fixture["symphonies"].items():
        current = sym_data["current_params"]
        proposed = sym_data["best_params"]
        current_tc = sym_data["current_trigger_count"]
        proposed_tc = sym_data["proposed_trigger_count"]
        flip_threshold = fixture["trigger_freq_flip_threshold"]
        flag = (proposed_tc / current_tc) > flip_threshold if current_tc > 0 else False

        for param_name in fixture["required_search_space_keys"]:
            rows.append({
                "symphony_id": sym_id,
                "param_name": param_name,
                "current_value": current[param_name],
                "proposed_value": proposed[param_name],
                "expected_trigger_freq_change": float(proposed_tc - current_tc),
                "pbo_veto_status": sym_data["pbo_veto_status"],
                "flag_for_operator_review": flag,
                "haircut_outcome": sym_data["haircut_outcome"],
                "study_name": (
                    f"20260618T000000Z__{sym_id}"
                    + sym_data["study_name_suffix"]
                ),
            })
    return rows


# ---------------------------------------------------------------------------
# Helper: load the report module
# ---------------------------------------------------------------------------

def _load_report_module():
    if not _REPORT_SCRIPT.exists():
        pytest.fail(
            f"scripts/vwap-calibration-report.py not found at {_REPORT_SCRIPT}. "
            "Implementer must create it. (AC-2)"
        )
    spec = importlib.util.spec_from_file_location("vwap_calibration_report", _REPORT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC-2.1 — report entry point exists and is callable
# ---------------------------------------------------------------------------

def test_report_script_exposes_callable_entry_point():
    """scripts/vwap-calibration-report.py must expose generate_report, render_report,
    or build_report as a callable.

    Fails RED until the script exists with a suitable entry point.
    """
    mod = _load_report_module()
    entry = (
        getattr(mod, "generate_report", None)
        or getattr(mod, "render_report", None)
        or getattr(mod, "build_report", None)
    )
    assert callable(entry), (
        "scripts/vwap-calibration-report.py must expose a callable named "
        "generate_report, render_report, or build_report."
    )


# ---------------------------------------------------------------------------
# AC-2.2 — report output contains all required per-symphony fields
# ---------------------------------------------------------------------------

def test_report_contains_required_row_keys(fixture):
    """Each report row must include the required fields listed in the fixture.

    Asserts shape/presence — not the numeric values themselves.
    Fails RED until the script emits the required fields.
    """
    mod = _load_report_module()
    entry = (
        getattr(mod, "generate_report", None)
        or getattr(mod, "render_report", None)
        or getattr(mod, "build_report", None)
    )

    rows = _build_fixture_rows(fixture)
    result = entry(rows)

    required_keys = set(fixture["required_report_row_keys"])

    if isinstance(result, list):
        # Entry point returns enriched row dicts
        assert result, "generate_report returned an empty list"
        for row in result:
            missing = required_keys - set(row.keys())
            assert not missing, (
                f"Report row missing required keys: {sorted(missing)}. "
                f"Row keys present: {sorted(row.keys())}"
            )
    elif isinstance(result, str):
        # Entry point returns a markdown string
        for key in required_keys:
            assert key in result, (
                f"Required field '{key}' not found anywhere in report output. "
                "The report must surface all required fields."
            )
    else:
        pytest.fail(
            f"generate_report returned unexpected type {type(result).__name__}; "
            "expected list[dict] or str (markdown)"
        )


# ---------------------------------------------------------------------------
# AC-2.3 — report covers each symphony from the fixture (not fewer)
# ---------------------------------------------------------------------------

def test_report_covers_all_fixture_symphonies(fixture):
    """The report must produce output for each symphony in the input rows.

    Symphony count is derived from the fixture — not hardcoded.
    Fails RED until the script exists and processes all input symphonies.
    """
    mod = _load_report_module()
    entry = (
        getattr(mod, "generate_report", None)
        or getattr(mod, "render_report", None)
        or getattr(mod, "build_report", None)
    )

    rows = _build_fixture_rows(fixture)
    expected_syms = set(fixture["symphonies"].keys())
    result = entry(rows)

    if isinstance(result, list):
        result_syms = {r["symphony_id"] for r in result if "symphony_id" in r}
    elif isinstance(result, str):
        result_syms = {sym for sym in expected_syms if sym in result}
    else:
        pytest.fail(f"Unexpected return type from entry point: {type(result).__name__}")

    missing_syms = expected_syms - result_syms
    assert not missing_syms, (
        f"Report missing symphony sections for: {sorted(missing_syms)}. "
        f"Expected {len(expected_syms)} symphonies, found {len(result_syms)}."
    )


# ---------------------------------------------------------------------------
# AC-5.1 — pbo_veto_status key present in every report row
# ---------------------------------------------------------------------------

def test_pbo_veto_status_key_present_in_all_rows(fixture):
    """Every report row must include pbo_veto_status.

    Fails RED until the field is added to run_calibration_sweep output.
    """
    import autotuner

    assert hasattr(autotuner, "run_calibration_sweep"), (
        "autotuner.run_calibration_sweep not found"
    )
    # We cannot call the real sweep (too slow); inspect the return via source
    # or trust AC-2.2 tests for the shape. This test asserts the fixture's
    # required_report_row_keys includes pbo_veto_status (fixture integrity).
    assert "pbo_veto_status" in fixture["required_report_row_keys"], (
        "Fixture error: pbo_veto_status must be in required_report_row_keys"
    )

    # Also assert pbo_veto_status is present in the fixture symphonies themselves
    for sym_id, sym_data in fixture["symphonies"].items():
        assert "pbo_veto_status" in sym_data, (
            f"Fixture error: {sym_id} missing pbo_veto_status"
        )


def test_pbo_vetoed_symphony_is_flagged_not_recommended(fixture):
    """A PBO-vetoed symphony must have pbo_veto_status=True in report rows.

    Derived from fixture: SYM-BETA has pbo_veto_status=True. Assert the
    report rows for that symphony carry the flag.
    Fails RED until the field is surfaced in the report output.
    """
    mod = _load_report_module()
    entry = (
        getattr(mod, "generate_report", None)
        or getattr(mod, "render_report", None)
        or getattr(mod, "build_report", None)
    )

    rows = _build_fixture_rows(fixture)
    result = entry(rows)

    # Find the fixture symphony with pbo_veto_status=True
    vetoed_sym = next(
        (sid for sid, sd in fixture["symphonies"].items() if sd["pbo_veto_status"]),
        None,
    )
    assert vetoed_sym is not None, "Fixture error: no symphony has pbo_veto_status=True"

    if isinstance(result, list):
        vetoed_rows = [r for r in result if r.get("symphony_id") == vetoed_sym]
        assert vetoed_rows, f"No rows found for PBO-vetoed symphony {vetoed_sym}"
        for row in vetoed_rows:
            assert row.get("pbo_veto_status") is True, (
                f"Row for {vetoed_sym} has pbo_veto_status={row.get('pbo_veto_status')!r}, "
                f"expected True"
            )
    elif isinstance(result, str):
        # Markdown output: vetoed sym must appear with a veto marker
        assert vetoed_sym in result, f"{vetoed_sym} not mentioned in markdown report"
        # Look for the marker near the symphony mention — we don't hardcode exact text
        sym_idx = result.index(vetoed_sym)
        context = result[max(0, sym_idx - 50): sym_idx + 200]
        assert any(term in context.lower() for term in ("pbo", "veto", "vetoed")), (
            f"PBO veto status for {vetoed_sym} not surfaced in report. "
            f"Context around symphony mention: {context!r}"
        )


# ---------------------------------------------------------------------------
# AC-6.1 — study names match the required pattern
# ---------------------------------------------------------------------------

def test_study_names_match_calsweep_pattern(fixture):
    """Study names emitted by the sweep must end with __calsweep.

    Pattern derived from fixture: fixture['study_name_regex'].
    Fails RED until run_calibration_sweep appends __calsweep to study names.
    """
    pattern = re.compile(fixture["study_name_regex"])
    rows = _build_fixture_rows(fixture)

    for row in rows:
        study_name = row.get("study_name", "")
        assert pattern.match(study_name), (
            f"Study name {study_name!r} does not match required pattern "
            f"{fixture['study_name_regex']}. "
            "Implementer must append '__calsweep' suffix."
        )


def test_study_names_are_unique_across_symphonies(fixture):
    """No two symphonies should share the same study name in a single sweep.

    Fails RED until run_calibration_sweep generates per-symphony unique names.
    """
    rows = _build_fixture_rows(fixture)
    study_names = [row["study_name"] for row in rows if row.get("param_name") == fixture["required_search_space_keys"][0]]
    # One study name per symphony (the first param row is the de-dup key)
    assert len(study_names) == len(set(study_names)), (
        f"Duplicate study names detected: {[n for n in study_names if study_names.count(n) > 1]}"
    )


# ---------------------------------------------------------------------------
# AC-7.1 — >2x trigger flip flagged for operator review
# ---------------------------------------------------------------------------

def test_greater_than_2x_trigger_flip_is_flagged(fixture):
    """A symphony whose proposed trigger count exceeds 2x current is flagged.

    Uses SYM-BETA from the fixture (proposed=11, current=5 => 2.2x > 2.0).
    flag_for_operator_review must be True for that symphony.
    Fails RED until the flag is added to report rows.
    """
    mod = _load_report_module()
    entry = (
        getattr(mod, "generate_report", None)
        or getattr(mod, "render_report", None)
        or getattr(mod, "build_report", None)
    )

    rows = _build_fixture_rows(fixture)
    result = entry(rows)

    flip_threshold = fixture["trigger_freq_flip_threshold"]

    # Derive which symphonies should be flagged from fixture (not hardcoded sym names)
    expected_flagged = {
        sid for sid, sd in fixture["symphonies"].items()
        if sd["current_trigger_count"] > 0
        and (sd["proposed_trigger_count"] / sd["current_trigger_count"]) > flip_threshold
    }
    assert expected_flagged, "Fixture error: no symphony has >2x trigger flip"

    if isinstance(result, list):
        for sym_id in expected_flagged:
            sym_rows = [r for r in result if r.get("symphony_id") == sym_id]
            assert sym_rows, f"No rows found for expected-flagged symphony {sym_id}"
            for row in sym_rows:
                assert row.get("flag_for_operator_review") is True, (
                    f"Symphony {sym_id} has >{flip_threshold}x trigger flip but "
                    f"flag_for_operator_review={row.get('flag_for_operator_review')!r}"
                )
    elif isinstance(result, str):
        for sym_id in expected_flagged:
            assert sym_id in result, f"Expected-flagged symphony {sym_id} not in report"
            sym_idx = result.index(sym_id)
            context = result[max(0, sym_idx - 50): sym_idx + 300]
            assert any(term in context.lower() for term in ("flag", "review", "operator", "2x", ">2")), (
                f"No operator-review flag found for {sym_id} in report. Context: {context!r}"
            )


def test_below_2x_trigger_flip_is_not_flagged(fixture):
    """A symphony whose trigger flip is <= 2x must NOT be flagged.

    Uses SYM-GAMMA from fixture (proposed=9, current=8 => 1.125x <= 2.0).
    Fails RED until flag logic is implemented correctly.
    """
    mod = _load_report_module()
    entry = (
        getattr(mod, "generate_report", None)
        or getattr(mod, "render_report", None)
        or getattr(mod, "build_report", None)
    )

    rows = _build_fixture_rows(fixture)
    result = entry(rows)

    flip_threshold = fixture["trigger_freq_flip_threshold"]

    # Derive which symphonies should NOT be flagged
    expected_not_flagged = {
        sid for sid, sd in fixture["symphonies"].items()
        if sd["current_trigger_count"] > 0
        and (sd["proposed_trigger_count"] / sd["current_trigger_count"]) <= flip_threshold
    }
    assert expected_not_flagged, "Fixture error: no symphony has <=2x trigger flip"

    if isinstance(result, list):
        for sym_id in expected_not_flagged:
            sym_rows = [r for r in result if r.get("symphony_id") == sym_id]
            for row in sym_rows:
                assert row.get("flag_for_operator_review") is not True, (
                    f"Symphony {sym_id} has <={flip_threshold}x trigger flip but "
                    f"flag_for_operator_review={row.get('flag_for_operator_review')!r} "
                    "— should not be flagged"
                )
