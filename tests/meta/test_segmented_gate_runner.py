"""
Unit tests for scripts/segmented_full_tree_gate.py.

WHY THIS FILE EXISTS
--------------------
The segmented gate runner exists to give a SAFE local full-tree verdict by
running tests/ in per-directory chunks, each in a fresh subprocess (see the
runner module's own docstring for the memory-crash rationale). These tests
cover ONLY the pure logic: discovery, chunk-planning/bin-packing, the --only
guard, summary-line parsing, node-id extraction, aggregation, verdict
computation, and the argv safety guardrails -- all against synthetic
fixtures or literal strings pulled from real prototype evidence logs.

These tests NEVER spawn a real full-tree (or even multi-chunk) pytest run.
The one function that touches subprocess/psutil (run_chunk) is exercised
only indirectly through its pure helpers; end-to-end proof that the runner
actually works against the real tree is a separate manual smoke step
(--dry-run, then --only tests/engine), not part of this file.
"""

from __future__ import annotations

import ast
import inspect
import random
import sys
from pathlib import Path

import pytest

import scripts.segmented_full_tree_gate as gate

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# Mirrors the real tests/ directory's file-count distribution (captured via
# `find tests/*/ -name test_*.py | wc -l` on 2026-08-25) closely enough to
# exercise realistic bin-packing behaviour, including the real substring
# collision between "tests/engine" and "tests/math_engine" that --only must
# handle correctly.
_REALISTIC_COUNTS = {
    "advisors": 99,
    "app": 99,
    "autotuner": 98,
    "ai_advisor": 76,
    "math_engine": 54,
    "database": 48,
    "execution": 39,
    "analytics": 27,
    "dashboard": 27,
    "sleeves": 24,
    "ui": 24,
    "engine": 11,
    "synthetic_history": 10,
    "loose": 10,
    "reporting": 8,
    "prism_scheduler": 6,
    "realtime_push": 6,
    "security": 6,
    "portmode": 5,
    "integration": 4,
    "meta": 4,
    "calibration": 2,
    "error_handling": 2,
    "guard_preconditions": 2,
    "holiday_calendar": 2,
    "js_syntax": 2,
    "symphony_logic": 2,
    "telemetry": 2,
    "tools": 2,
    "mem_cap": 3,
    "perf": 3,
    "acceptance_gate": 1,
    "alpaca": 1,
    "conftest_guard": 1,
    "scripts": 1,
    "shadow": 1,
}


def _units_from_counts(counts: dict[str, int]) -> list[gate.Unit]:
    return [
        gate.Unit(name=f"tests/{name}", paths=(f"tests/{name}",), file_count=n)
        for name, n in counts.items()
    ]


def _make_tree(tmp_path: Path, spec: dict[str, int]) -> Path:
    """Build a synthetic tests/ tree under tmp_path. `spec` maps dir name ->
    file count; a dir name of "" means loose top-level files."""
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    for name, count in spec.items():
        target_dir = tests_root if name == "" else tests_root / name
        target_dir.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            (target_dir / f"test_{name or 'loose'}_{i}.py").write_text("def test_x(): pass\n")
    return tests_root


# ---------------------------------------------------------------------------
# discover_units
# ---------------------------------------------------------------------------


def test_discover_units_finds_each_nonempty_subdir(tmp_path):
    tests_root = _make_tree(tmp_path, {"alpha": 3, "beta": 5})
    units = gate.discover_units(tests_root)
    counts = {u.name: u.file_count for u in units}
    assert counts.get("tests/alpha") == 3
    assert counts.get("tests/beta") == 5


def test_discover_units_excludes_empty_dir(tmp_path):
    tests_root = _make_tree(tmp_path, {"alpha": 3})
    (tests_root / "empty_dir").mkdir()
    units = gate.discover_units(tests_root)
    assert "tests/empty_dir" not in {u.name for u in units}


def test_discover_units_captures_loose_top_level_files(tmp_path):
    tests_root = _make_tree(tmp_path, {"alpha": 2, "": 4})
    units = gate.discover_units(tests_root)
    loose = [u for u in units if u.file_count == 4 and len(u.paths) == 4]
    assert loose, f"expected a loose-files unit, got {units}"
    assert all(p.endswith(".py") for p in loose[0].paths)


def test_discover_units_skips_hidden_and_pycache_dirs(tmp_path):
    tests_root = _make_tree(tmp_path, {"alpha": 2})
    pycache = tests_root / "__pycache__"
    pycache.mkdir()
    (pycache / "test_ghost.py").write_text("def test_x(): pass\n")
    hidden = tests_root / ".hidden"
    hidden.mkdir()
    (hidden / "test_ghost2.py").write_text("def test_x(): pass\n")
    units = gate.discover_units(tests_root)
    names = {u.name for u in units}
    assert "tests/__pycache__" not in names
    assert "tests/.hidden" not in names


def test_discover_units_paths_use_forward_slashes(tmp_path):
    tests_root = _make_tree(tmp_path, {"alpha": 1})
    units = gate.discover_units(tests_root)
    alpha = next(u for u in units if u.name == "tests/alpha")
    assert "\\" not in alpha.paths[0]


# ---------------------------------------------------------------------------
# plan_chunks
# ---------------------------------------------------------------------------


def test_plan_chunks_preserves_total_file_count():
    units = _units_from_counts(_REALISTIC_COUNTS)
    total_before = sum(u.file_count for u in units)
    chunks = gate.plan_chunks(units, target_chunks=6)
    assert sum(c.file_count for c in chunks) == total_before


def test_plan_chunks_never_splits_a_unit():
    units = _units_from_counts(_REALISTIC_COUNTS)
    chunks = gate.plan_chunks(units, target_chunks=6)
    seen_names = [u.name for c in chunks for u in c.units]
    assert sorted(seen_names) == sorted(u.name for u in units)
    assert len(seen_names) == len(set(seen_names))


def test_plan_chunks_respects_target_chunk_count():
    units = _units_from_counts(_REALISTIC_COUNTS)
    chunks = gate.plan_chunks(units, target_chunks=6)
    assert len(chunks) == 6


def test_plan_chunks_caps_at_unit_count_when_fewer_units_than_target():
    units = _units_from_counts({"alpha": 5, "beta": 3})
    chunks = gate.plan_chunks(units, target_chunks=6)
    assert len(chunks) == 2
    assert all(c.units for c in chunks)


def test_plan_chunks_balance_within_bound():
    units = _units_from_counts(_REALISTIC_COUNTS)
    chunks = gate.plan_chunks(units, target_chunks=6)
    sizes = [c.file_count for c in chunks]
    fair_share = sum(sizes) / len(sizes)
    # Structural balance check (LPT's known worst-case bound is loose, but on
    # this real-shaped distribution it should stay well clear of dominance)
    # -- never a hardcoded output size, just a bound on the spread.
    assert max(sizes) <= fair_share * 1.5
    assert min(sizes) >= fair_share * 0.5


def test_plan_chunks_deterministic_regardless_of_input_order():
    units = _units_from_counts(_REALISTIC_COUNTS)
    shuffled = units[:]
    random.Random(42).shuffle(shuffled)
    chunks_a = gate.plan_chunks(units, target_chunks=6)
    chunks_b = gate.plan_chunks(shuffled, target_chunks=6)
    assignment_a = {u.name: i for i, c in enumerate(chunks_a) for u in c.units}
    assignment_b = {u.name: i for i, c in enumerate(chunks_b) for u in c.units}
    assert assignment_a == assignment_b


def test_plan_chunks_empty_units_returns_empty_plan():
    assert gate.plan_chunks([], target_chunks=6) == []


# ---------------------------------------------------------------------------
# resolve_only_selection (the --only refusal guard)
# ---------------------------------------------------------------------------


def test_only_empty_substring_is_refused():
    units = _units_from_counts(_REALISTIC_COUNTS)
    with pytest.raises(gate.OnlyFilterRefused):
        gate.resolve_only_selection(units, "", target_chunks=6)


def test_only_whitespace_substring_is_refused():
    units = _units_from_counts(_REALISTIC_COUNTS)
    with pytest.raises(gate.OnlyFilterRefused):
        gate.resolve_only_selection(units, "   ", target_chunks=6)


def test_only_substring_matching_every_unit_is_refused():
    units = _units_from_counts(_REALISTIC_COUNTS)
    # Every synthetic unit name is "tests/<x>", so "tests" matches all of
    # them -- exactly the whole-tree-collapse hazard this guard stops.
    with pytest.raises(gate.OnlyFilterRefused):
        gate.resolve_only_selection(units, "tests", target_chunks=6)


def test_only_no_match_is_refused():
    units = _units_from_counts(_REALISTIC_COUNTS)
    with pytest.raises(gate.OnlyFilterRefused):
        gate.resolve_only_selection(units, "nonexistent_zzz", target_chunks=6)


def test_only_substring_matches_all_units_containing_it_not_just_exact_name():
    # "engine" is a substring of both "tests/engine" and "tests/math_engine"
    # -- --only matches by substring, so both are selected. This is real,
    # non-obvious behaviour: callers who want exactly one directory must
    # pass the fully-qualified name to avoid the collision.
    units = _units_from_counts(_REALISTIC_COUNTS)
    chunk = gate.resolve_only_selection(units, "engine", target_chunks=6)
    assert {u.name for u in chunk.units} == {"tests/engine", "tests/math_engine"}


def test_only_fully_qualified_name_matches_exactly_one_unit():
    units = _units_from_counts(_REALISTIC_COUNTS)
    chunk = gate.resolve_only_selection(units, "tests/engine", target_chunks=6)
    assert [u.name for u in chunk.units] == ["tests/engine"]


def test_only_broad_but_specific_match_within_ceiling_is_accepted():
    # "database" + "dashboard" would not match, but a genuinely large single
    # dir near-but-under the ceiling should still be accepted.
    units = _units_from_counts(_REALISTIC_COUNTS)
    chunk = gate.resolve_only_selection(units, "tests/database", target_chunks=6)
    assert [u.name for u in chunk.units] == ["tests/database"]


# ---------------------------------------------------------------------------
# parse_summary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected",
    [
        (
            "9 failed, 1928 passed, 12 skipped, 23 warnings in 515.20s (0:08:35)",
            {"failed": 9, "passed": 1928, "skipped": 12, "warnings": 23},
        ),
        (
            "2 failed, 3246 passed, 6 skipped, 4 deselected, 3 warnings in 876.95s (0:14:36)",
            {"failed": 2, "passed": 3246, "skipped": 6, "deselected": 4, "warnings": 3},
        ),
        (
            "2755 passed, 14 skipped, 7 deselected, 1 xfailed, 82 warnings in 731.53s (0:12:11)",
            {"passed": 2755, "skipped": 14, "deselected": 7, "xfailed": 1, "warnings": 82},
        ),
        (
            "708 passed, 10 skipped, 4 warnings in 167.84s (0:02:47)",
            {"passed": 708, "skipped": 10, "warnings": 4},
        ),
        ("2 passed in 3.14s", {"passed": 2}),
        ("3 errors in 12.40s", {"errors": 3}),
        ("1 failed, 2 errors in 5.00s", {"failed": 1, "errors": 2}),
    ],
)
def test_parse_summary_extracts_counts(line, expected):
    assert gate.parse_summary(line) == expected


def test_parse_summary_handles_equals_padding():
    padded = (
        "=================== 9 failed, 1928 passed, 12 skipped, 23 warnings "
        "in 515.20s (0:08:35) ==================="
    )
    assert gate.parse_summary(padded) == {
        "failed": 9,
        "passed": 1928,
        "skipped": 12,
        "warnings": 23,
    }


def test_parse_summary_uses_last_matching_line_in_multiline_output():
    text = "\n".join(
        [
            "collecting ... ",
            "some unrelated line mentioning 5 passed tests in a docstring",
            "=========== short test summary info ============",
            "FAILED tests/x/test_y.py::test_z",
            "1 failed, 3 passed in 4.00s",
        ]
    )
    assert gate.parse_summary(text) == {"failed": 1, "passed": 3}


def test_parse_summary_returns_none_on_collection_blowup_with_no_summary():
    text = "\n".join(
        [
            "ImportError while loading conftest '...conftest.py'.",
            "tests\\database\\conftest.py:16: in <module>",
            "    import database as db_module",
            "E   RuntimeError: test attempted to open the production DB",
        ]
    )
    assert gate.parse_summary(text) is None


def test_parse_summary_returns_none_on_no_tests_ran():
    assert gate.parse_summary("no tests ran in 0.01s") is None


# ---------------------------------------------------------------------------
# extract_node_ids
# ---------------------------------------------------------------------------


def test_extract_node_ids_from_mixed_failed_and_error_block():
    text = "\n".join(
        [
            "=========================== short test summary info ===========================",
            "SKIPPED [1] tests\\ai_advisor\\test_prism_scheduling.py:1605: some reason",
            "FAILED tests/app/test_held_basis_route_convergence.py::TestAC2::test_a",
            "ERROR tests/database/test_x.py::test_b - RuntimeError: boom",
            "FAILED tests/app/test_held_basis_route_convergence.py::TestAC3::test_c",
            "9 failed, 1928 passed, 12 skipped, 23 warnings in 515.20s (0:08:35)",
        ]
    )
    assert gate.extract_node_ids(text) == [
        "tests/app/test_held_basis_route_convergence.py::TestAC2::test_a",
        "tests/database/test_x.py::test_b",
        "tests/app/test_held_basis_route_convergence.py::TestAC3::test_c",
    ]


def test_extract_node_ids_ignores_skipped_lines():
    text = "SKIPPED [1] tests/foo/test_bar.py:10: reason\n1 skipped in 0.01s"
    assert gate.extract_node_ids(text) == []


def test_extract_node_ids_empty_when_no_failures():
    assert gate.extract_node_ids("708 passed, 10 skipped, 4 warnings in 167.84s (0:02:47)") == []


def test_extract_node_ids_preserves_spaces_in_parametrized_brackets_no_reason():
    # A parametrized node id can contain a literal space inside its brackets
    # (e.g. a string parameter "a b"). With no " - <reason>" suffix at all,
    # the full id -- space included -- must be captured, not truncated at
    # the first whitespace.
    text = "FAILED tests/x/test_z.py::test_foo[a b]\n1 failed in 1.00s"
    assert gate.extract_node_ids(text) == ["tests/x/test_z.py::test_foo[a b]"]


def test_extract_node_ids_preserves_spaces_in_parametrized_brackets_with_reason():
    text = "FAILED tests/x/test_z.py::test_foo[a b] - AssertionError: boom\n1 failed in 1.00s"
    assert gate.extract_node_ids(text) == ["tests/x/test_z.py::test_foo[a b]"]


# ---------------------------------------------------------------------------
# aggregate / verdict
# ---------------------------------------------------------------------------


def _chunk_result(index, verdict, counts=None, node_ids=None):
    chunk = gate.Chunk(
        index=index,
        units=[gate.Unit(name=f"tests/c{index}", paths=(f"tests/c{index}",), file_count=1)],
    )
    return gate.ChunkResult(
        chunk=chunk,
        returncode=0 if verdict == "PASS" else 1,
        counts=counts,
        node_ids=node_ids or [],
        duration_s=1.0,
        verdict=verdict,
    )


def test_aggregate_all_pass_yields_pass_verdict():
    results = [
        _chunk_result(0, "PASS", counts={"passed": 10}),
        _chunk_result(1, "PASS", counts={"passed": 5}),
    ]
    report = gate.aggregate(results)
    assert report.verdict == "PASS"
    assert report.totals == {"passed": 15}
    assert report.failing_node_ids == []


def test_aggregate_any_fail_yields_fail_verdict_and_unions_node_ids():
    results = [
        _chunk_result(0, "PASS", counts={"passed": 10}),
        _chunk_result(
            1,
            "FAIL",
            counts={"failed": 2, "passed": 8},
            node_ids=["tests/a/test_x.py::test_1", "tests/a/test_x.py::test_2"],
        ),
        _chunk_result(2, "TIMEOUT", counts=None),
    ]
    report = gate.aggregate(results)
    assert report.verdict == "FAIL"
    assert report.failing_node_ids == ["tests/a/test_x.py::test_1", "tests/a/test_x.py::test_2"]
    # The TIMEOUT chunk's missing counts must NOT be silently treated as
    # zero -- totals reflect only what was actually parsed.
    assert report.totals == {"passed": 18, "failed": 2}


def test_aggregate_deduplicates_node_ids_across_chunks():
    results = [
        _chunk_result(0, "FAIL", counts={"failed": 1}, node_ids=["tests/a/test_x.py::test_1"]),
        _chunk_result(1, "FAIL", counts={"failed": 1}, node_ids=["tests/a/test_x.py::test_1"]),
    ]
    report = gate.aggregate(results)
    assert report.failing_node_ids == ["tests/a/test_x.py::test_1"]


def test_aggregate_empty_results_is_not_pass():
    report = gate.aggregate([])
    assert report.verdict != "PASS"


def test_aggregate_passes_through_scoped_reason():
    report = gate.aggregate([], scoped_reason="--only 'tests/engine'")
    assert report.scoped_reason == "--only 'tests/engine'"


def test_aggregate_scoped_reason_defaults_to_none_for_a_full_run():
    report = gate.aggregate([_chunk_result(0, "PASS", counts={"passed": 1})])
    assert report.scoped_reason is None


def test_aggregate_no_tests_chunk_does_not_fail_the_grand_verdict():
    # A chunk whose directories were entirely deselected by the default
    # marker filter (-m 'not live and not slow and not perf') exits
    # pytest's own "no tests ran" code (5). That is NOT a test failure and
    # must not drag an otherwise clean tree down to a gate FAIL.
    results = [
        _chunk_result(0, "PASS", counts={"passed": 100}),
        _chunk_result(1, "NO_TESTS", counts={"deselected": 12}),
    ]
    report = gate.aggregate(results)
    assert report.verdict == "PASS"


def test_aggregate_all_no_tests_is_still_pass():
    results = [_chunk_result(0, "NO_TESTS", counts={"deselected": 3})]
    report = gate.aggregate(results)
    assert report.verdict == "PASS"


def test_aggregate_no_tests_does_not_mask_a_real_failure_elsewhere():
    results = [
        _chunk_result(0, "NO_TESTS", counts={"deselected": 3}),
        _chunk_result(1, "FAIL", counts={"failed": 1}, node_ids=["tests/a/test_x.py::test_1"]),
    ]
    report = gate.aggregate(results)
    assert report.verdict == "FAIL"


# ---------------------------------------------------------------------------
# _classify_chunk_outcome -- pure verdict classification extracted from
# run_chunk, so the exit-code-5 "no tests ran"/deselected-only case (and the
# rest of the PASS/FAIL/ERROR branching) is unit-testable without a real
# subprocess.
# ---------------------------------------------------------------------------


def test_classify_chunk_outcome_exit_5_is_no_tests_even_with_no_summary():
    # Literally zero items collected: no parseable summary line at all.
    assert gate._classify_chunk_outcome(5, None) == "NO_TESTS"


def test_classify_chunk_outcome_exit_5_is_no_tests_with_deselected_only_summary():
    # Every collected item deselected by the default marker filter: pytest
    # DOES print a parseable "N deselected in Xs" summary line here.
    assert gate._classify_chunk_outcome(5, {"deselected": 12}) == "NO_TESTS"


def test_classify_chunk_outcome_clean_pass():
    assert gate._classify_chunk_outcome(0, {"passed": 10}) == "PASS"


def test_classify_chunk_outcome_attributable_failure():
    assert gate._classify_chunk_outcome(1, {"failed": 2, "passed": 8}) == "FAIL"


def test_classify_chunk_outcome_no_summary_is_error():
    assert gate._classify_chunk_outcome(1, None) == "ERROR"


def test_classify_chunk_outcome_anomalous_returncode_is_error():
    # returncode 2 ("interrupted") with a summary present -- never PASS.
    assert gate._classify_chunk_outcome(2, {"passed": 5}) == "ERROR"


def test_classify_chunk_outcome_returncode_1_with_zero_failed_is_error():
    # Should not normally happen, but never silently call it PASS or FAIL.
    assert gate._classify_chunk_outcome(1, {"passed": 5}) == "ERROR"


# ---------------------------------------------------------------------------
# _attach_psutil_process -- must degrade to None, never propagate, so one
# bad chunk (dead pid by the time this runs) can't abort the whole gate.
# ---------------------------------------------------------------------------


class _FakePsutilError(Exception):
    pass


def test_attach_psutil_process_returns_none_when_psutil_unavailable(monkeypatch):
    monkeypatch.setattr(gate, "psutil", None)
    assert gate._attach_psutil_process(1234, "chunk 0") is None


def test_attach_psutil_process_degrades_when_process_raises(monkeypatch):
    def raising_process(pid):
        raise gate.psutil.NoSuchProcess(pid)

    monkeypatch.setattr(gate.psutil, "Process", raising_process)
    result = gate._attach_psutil_process(99999, "chunk 0")
    assert result is None


def test_attach_psutil_process_returns_handle_on_success(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(gate.psutil, "Process", lambda pid: sentinel)
    assert gate._attach_psutil_process(1234, "chunk 0") is sentinel


# ---------------------------------------------------------------------------
# render_report -- a scoped (--only) run must never read as a full-tree PASS
# ---------------------------------------------------------------------------


def test_render_report_marks_scoped_run_with_banner_and_verdict_suffix():
    report = gate.aggregate(
        [_chunk_result(0, "PASS", counts={"passed": 5})],
        scoped_reason="--only 'tests/engine'",
    )
    text = gate.render_report(report)
    lines = text.splitlines()
    assert lines[0].startswith("*** SCOPED RUN")
    assert "NOT a full-tree verdict" in lines[0]
    verdict_line = next(line for line in lines if line.startswith("VERDICT:"))
    # A wrapper/human grepping for the bare "VERDICT: PASS" string used by a
    # full-tree run must NOT get a false-positive match here.
    assert verdict_line != "VERDICT: PASS"
    assert "SCOPED" in verdict_line


def test_render_report_full_tree_run_has_no_scoped_marking():
    report = gate.aggregate([_chunk_result(0, "PASS", counts={"passed": 5})])
    text = gate.render_report(report)
    assert "SCOPED" not in text
    assert text.splitlines()[-1] == "VERDICT: PASS"


# ---------------------------------------------------------------------------
# build_argv safety guardrails
# ---------------------------------------------------------------------------


def _engine_chunk() -> gate.Chunk:
    return gate.Chunk(
        index=0, units=[gate.Unit(name="tests/engine", paths=("tests/engine",), file_count=11)]
    )


def test_build_argv_always_includes_explicit_n0():
    assert "-n0" in gate.build_argv(_engine_chunk())


def test_build_argv_never_passes_bare_tests_root():
    assert "tests" not in gate.build_argv(_engine_chunk())


def test_build_argv_includes_at_least_one_explicit_path():
    argv = gate.build_argv(_engine_chunk())
    path_tokens = [a for a in argv if not a.startswith("-") and a != sys.executable]
    assert any(a.startswith("tests/") for a in path_tokens)


def test_build_argv_includes_explicit_test_timeout():
    argv = gate.build_argv(_engine_chunk(), test_timeout=42)
    assert "--timeout=42" in argv


# ---------------------------------------------------------------------------
# _validate_safe_argv -- guardrails must be `raise`, never `assert`
#
# `assert` is compiled out entirely under `python -O` / PYTHONOPTIMIZE, which
# would silently disable a safety check. These tests prove BOTH a structural
# guarantee (no ast.Assert node exists in the function at all -- true
# regardless of how Python is invoked) and the resulting behavior (each
# violation raises RuntimeError, not AssertionError).
# ---------------------------------------------------------------------------


def test_validate_safe_argv_contains_no_assert_statements():
    source = inspect.getsource(gate._validate_safe_argv)
    tree = ast.parse(source)
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))


def test_validate_safe_argv_passes_for_a_safe_argv():
    gate._validate_safe_argv(gate.build_argv(_engine_chunk()))  # must not raise


def test_validate_safe_argv_raises_runtime_error_when_n0_missing():
    argv = [sys.executable, "-m", "pytest", "tests/engine", "-q", "--timeout=900"]
    with pytest.raises(RuntimeError):
        gate._validate_safe_argv(argv)


def test_validate_safe_argv_raises_runtime_error_on_bare_tests_root():
    argv = [sys.executable, "-m", "pytest", "tests", "-n0", "-q", "--timeout=900"]
    with pytest.raises(RuntimeError):
        gate._validate_safe_argv(argv)


def test_validate_safe_argv_raises_runtime_error_when_timeout_missing():
    argv = [sys.executable, "-m", "pytest", "tests/engine", "-n0", "-q"]
    with pytest.raises(RuntimeError):
        gate._validate_safe_argv(argv)


def test_validate_safe_argv_raises_runtime_error_when_no_path_token():
    argv = [sys.executable, "-m", "pytest", "-n0", "-q", "--timeout=900"]
    with pytest.raises(RuntimeError):
        gate._validate_safe_argv(argv)


def test_validate_safe_argv_never_raises_assertion_error():
    # Even if some future edit reintroduces an `assert`, this pins the
    # CONTRACT (RuntimeError, never AssertionError) independent of the
    # structural AST check above.
    argv = [sys.executable, "-m", "pytest", "-n0", "-q", "--timeout=900"]
    with pytest.raises(RuntimeError):
        gate._validate_safe_argv(argv)
    with pytest.raises(Exception) as exc_info:
        gate._validate_safe_argv(argv)
    assert not isinstance(exc_info.value, AssertionError)


# ---------------------------------------------------------------------------
# _other_pytest_running -- must match a console-script launch too, not only
# a "python" process name with "pytest" in its cmdline.
# ---------------------------------------------------------------------------


class _FakeProcHandle:
    """Minimal stand-in for a psutil.Process as returned by process_iter()."""

    def __init__(self, pid: int, name: str, cmdline: list[str]):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "cmdline": cmdline}


def test_other_pytest_running_matches_console_script_name(monkeypatch):
    # "pytest.exe" (or "py.test") has NO "python" in its process name at
    # all -- a name-requires-"python" check would miss this entirely.
    fake_procs = [_FakeProcHandle(pid=999, name="pytest.exe", cmdline=["pytest.exe", "tests/"])]
    monkeypatch.setattr(gate.psutil, "process_iter", lambda attrs: iter(fake_procs))
    found, desc = gate._other_pytest_running()
    assert found is True
    assert "999" in desc


def test_other_pytest_running_still_matches_python_dash_m_pytest(monkeypatch):
    fake_procs = [
        _FakeProcHandle(
            pid=888, name="python.exe", cmdline=["python.exe", "-m", "pytest", "tests/"]
        )
    ]
    monkeypatch.setattr(gate.psutil, "process_iter", lambda attrs: iter(fake_procs))
    found, _desc = gate._other_pytest_running()
    assert found is True


def test_other_pytest_running_false_when_no_match(monkeypatch):
    fake_procs = [_FakeProcHandle(pid=777, name="notepad.exe", cmdline=["notepad.exe"])]
    monkeypatch.setattr(gate.psutil, "process_iter", lambda attrs: iter(fake_procs))
    found, desc = gate._other_pytest_running()
    assert found is False
    assert desc == ""


def test_other_pytest_running_skips_its_own_pid(monkeypatch):
    my_pid = gate.os.getpid()
    fake_procs = [_FakeProcHandle(pid=my_pid, name="pytest.exe", cmdline=["pytest.exe"])]
    monkeypatch.setattr(gate.psutil, "process_iter", lambda attrs: iter(fake_procs))
    found, _desc = gate._other_pytest_running()
    assert found is False


def test_other_pytest_running_ignores_path_substring_false_positive(monkeypatch):
    # Regression: this very worktree is named "pytest-gate". A wrapping
    # shell (Git Bash's "bash.exe -c '<whole command>'") puts the ENTIRE
    # invoked command line -- including that worktree path -- into ONE
    # cmdline token. A blind "'pytest' in joined cmdline" substring search
    # would false-positive on that path and refuse to ever run on this
    # host. Token-precise matching must not.
    fake_procs = [
        _FakeProcHandle(
            pid=555,
            name="bash.exe",
            cmdline=[
                "bash.exe",
                "-c",
                "cd /c/Users/x/worktrees/pytest-gate && python scripts/segmented_full_tree_gate.py",
            ],
        )
    ]
    monkeypatch.setattr(gate.psutil, "process_iter", lambda attrs: iter(fake_procs))
    found, _desc = gate._other_pytest_running()
    assert found is False


@pytest.mark.parametrize(
    "name,cmdline,expected",
    [
        ("pytest.exe", ["pytest.exe", "tests/"], True),
        ("py.test", ["py.test", "tests/"], True),
        ("python.exe", ["python.exe", "-m", "pytest", "tests/"], True),
        ("python.exe", ["python.exe", "-m", "pytest"], True),
        ("python.exe", ["python.exe", "-m", "pip", "list"], False),
        ("bash.exe", ["bash.exe", "-c", "cd /c/worktrees/pytest-gate && echo hi"], False),
        ("notepad.exe", ["notepad.exe"], False),
        ("python.exe", ["python.exe", "scripts/segmented_full_tree_gate.py"], False),
    ],
)
def test_looks_like_pytest_invocation(name, cmdline, expected):
    assert gate._looks_like_pytest_invocation(name, cmdline) is expected


# ---------------------------------------------------------------------------
# _kill_process_tree -- a TIMEOUT (and defensively a normal completion) must
# kill the ENTIRE descendant tree, not just the immediate pytest process,
# because test bodies fork their own worker pools (joblib/optuna) even
# though the chunk's own pytest invocation is "-n0".
# ---------------------------------------------------------------------------


class _FakeTreeProc:
    """Minimal stand-in for a psutil.Process, tracking terminate()/kill()."""

    def __init__(self, label: str):
        self.label = label
        self.terminated = False
        self.killed = False
        self._children: list[_FakeTreeProc] = []

    def children(self, recursive=True):
        return self._children

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_kill_process_tree_terminates_parent_and_all_children(monkeypatch):
    parent = _FakeTreeProc("parent")
    child1 = _FakeTreeProc("child1")
    child2 = _FakeTreeProc("child2")
    parent._children = [child1, child2]

    def fake_wait_procs(procs, timeout=None):
        return list(procs), []  # everyone terminated cleanly within the timeout

    monkeypatch.setattr(gate.psutil, "wait_procs", fake_wait_procs)

    gate._kill_process_tree(parent)

    assert parent.terminated and not parent.killed
    assert child1.terminated and not child1.killed
    assert child2.terminated and not child2.killed


def test_kill_process_tree_kills_survivors_after_terminate_timeout(monkeypatch):
    parent = _FakeTreeProc("parent")
    child = _FakeTreeProc("child")
    parent._children = [child]

    def fake_wait_procs(procs, timeout=None):
        # Nothing exited within the terminate() grace period.
        return [], list(procs)

    monkeypatch.setattr(gate.psutil, "wait_procs", fake_wait_procs)

    gate._kill_process_tree(parent)

    assert parent.terminated and parent.killed
    assert child.terminated and child.killed


def test_kill_process_tree_noop_when_psutil_unavailable(monkeypatch):
    monkeypatch.setattr(gate, "psutil", None)
    parent = _FakeTreeProc("parent")
    gate._kill_process_tree(parent)  # must not raise
    assert not parent.terminated
    assert not parent.killed


# ---------------------------------------------------------------------------
# main() exit codes -- 0 is reserved for a genuine full-tree PASS, 1 for a
# genuine full-tree FAIL, and a --only scoped run ALWAYS returns 3 (its own
# outcome) regardless of PASS/FAIL, so a wrapper keying on exit code alone
# can never mistake a subset smoke run for a full-tree merge-gate verdict.
#
# run_chunk itself is monkeypatched out in every test here -- these tests
# exercise main()'s own wiring/exit-code logic, never a real subprocess.
# ---------------------------------------------------------------------------


def _patch_discovery_and_run_chunk(monkeypatch, unit_name, chunk_verdict, counts):
    fake_unit = gate.Unit(name=unit_name, paths=(unit_name,), file_count=1)
    monkeypatch.setattr(gate, "discover_units", lambda root: [fake_unit])
    monkeypatch.setattr(
        gate,
        "run_chunk",
        lambda chunk, test_timeout, chunk_timeout: _chunk_result(
            chunk.index, chunk_verdict, counts=counts
        ),
    )


def test_main_returns_0_for_full_tree_pass(monkeypatch):
    _patch_discovery_and_run_chunk(monkeypatch, "tests/fake", "PASS", {"passed": 1})
    assert gate.main(["--chunks", "1"]) == 0


def test_main_returns_1_for_full_tree_fail(monkeypatch):
    _patch_discovery_and_run_chunk(monkeypatch, "tests/fake", "FAIL", {"failed": 1})
    assert gate.main(["--chunks", "1"]) == 1


def test_main_returns_0_for_full_tree_all_no_tests(monkeypatch):
    # NO_TESTS chunks are PASS-equivalent for the grand verdict (see
    # aggregate()) -- a fully-deselected tree is not a gate FAIL.
    _patch_discovery_and_run_chunk(monkeypatch, "tests/fake", "NO_TESTS", {"deselected": 1})
    assert gate.main(["--chunks", "1"]) == 0


def test_main_returns_3_for_scoped_pass_never_0(monkeypatch):
    _patch_discovery_and_run_chunk(monkeypatch, "tests/engine", "PASS", {"passed": 1})
    assert gate.main(["--only", "tests/engine"]) == 3


def test_main_returns_3_for_scoped_fail_too_never_1(monkeypatch):
    # Even a scoped FAIL returns 3, not 1 -- the distinguishing signal is
    # "this was scoped", not "did it pass"; a wrapper must never see 0 or 1
    # from a --only invocation and mistake it for a full-tree result.
    _patch_discovery_and_run_chunk(monkeypatch, "tests/engine", "FAIL", {"failed": 1})
    assert gate.main(["--only", "tests/engine"]) == 3
