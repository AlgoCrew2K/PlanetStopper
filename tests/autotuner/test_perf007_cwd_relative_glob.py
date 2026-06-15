"""
PERF-007 — math-reaudit LOW: calculate_historical_deviation's post-mortem glob
is CWD-relative, so an invocation from any directory other than the project
root silently finds zero files. The autotuner objective then runs with the
hard-coded default deviation penalties, with NO operator-visible warning that
the historical adjustment was skipped.

Source defect — autotuner.py (calculate_historical_deviation):

    files = glob.glob("post_mortem_*.json")

A CWD-relative glob has two failure modes:

  1. Different CWD (e.g. running from `tests/`, a daemon working dir, a
     systemd unit) silently returns []. The function then prints its
     "Historical Execution Deviation Penalties" line with the *defaults*
     and the operator has no signal that the file scan missed.
  2. The project's post-mortem directory is undocumented — there is no
     constant, no env override, no log line naming the resolved path —
     so an operator can't even tell where the search ran.

These RED tests demand:

  * The post-mortem search resolves to a STABLE, ABSOLUTE path that is
    independent of os.getcwd() — sourced from a module-level constant
    `_POST_MORTEM_DIR` (or `POST_MORTEM_DIR`) on `autotuner`. The default
    points at the project root (where `autotuner.py` lives — i.e.
    `Path(__file__).parent`).
  * An env override `POST_MORTEM_DIR` lets operators relocate the
    directory; when set, the function reads from THAT path.
  * When the resolved post-mortem directory is missing OR contains zero
    matching files, the function emits an operator-visible WARNING that
    names the resolved path. A silent empty result is the very defect.
  * Existing behaviour is preserved — when the post-mortem files live in
    the resolved directory and the dated lookback selects them, the
    function returns the same deviation values it always did.

These tests run against a temp directory; no live API calls.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"


def _import_autotuner():
    import autotuner

    return autotuner


def _valid_post_mortem_json(reason: str, exit_return: float, attempted: float) -> str:
    """Minimal post-mortem JSON the loop will happily consume."""
    return json.dumps(
        {
            "triggers": [
                {
                    "exit_reason": reason,
                    "exit_return": exit_return,
                    "attempted_trigger_level": attempted,
                }
            ]
        }
    )


def _write_post_mortem(directory: pathlib.Path, date_str: str, content: str) -> pathlib.Path:
    path = directory / f"post_mortem_{date_str}.json"
    path.write_text(content, encoding="utf-8")
    return path


def _resolve_post_mortem_dir_attr(autotuner_mod):
    """Return the module-level attribute that names the resolved post-mortem dir.

    Implementer may name it `_POST_MORTEM_DIR` (private) or `POST_MORTEM_DIR`
    (public). Either is acceptable as long as exactly one of them exists.
    """
    for name in ("_POST_MORTEM_DIR", "POST_MORTEM_DIR"):
        if hasattr(autotuner_mod, name):
            return name, getattr(autotuner_mod, name)
    return None, None


# ===========================================================================
# Source-level — the glob no longer uses a CWD-relative literal pattern
# ===========================================================================


def test_post_mortem_glob_is_not_cwd_relative_literal():
    """The bare `glob.glob("post_mortem_*.json")` literal — a CWD-relative
    pattern with no directory component — must NOT appear in
    calculate_historical_deviation. The directory anchor must be explicit.

    AST inspection: find every glob.glob(...) call inside
    calculate_historical_deviation and assert no argument is the bare
    literal string "post_mortem_*.json". A path-joined argument
    (os.path.join(...), str(Path(...) / ...), or an f-string interpolation
    of a directory constant) is fine.
    """
    tree = ast.parse(_AUTOTUNER_SRC.read_text(encoding="utf-8"))

    func = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "calculate_historical_deviation"
        ),
        None,
    )
    assert func is not None, "calculate_historical_deviation not found in autotuner.py."

    offenders: list[int] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        # Match glob.glob(...) and glob.iglob(...) — directly or via attribute access.
        fn = node.func
        is_glob = (
            isinstance(fn, ast.Attribute)
            and fn.attr in {"glob", "iglob"}
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "glob"
        )
        if not is_glob:
            continue
        if not node.args:
            continue
        arg0 = node.args[0]
        if isinstance(arg0, ast.Constant) and arg0.value == "post_mortem_*.json":
            offenders.append(node.lineno)

    assert not offenders, (
        f"PERF-007: calculate_historical_deviation still calls "
        f'`glob.glob("post_mortem_*.json")` at line(s) {offenders}. '
        f"A CWD-relative literal silently returns [] from any directory other "
        f"than the project root. Anchor the glob to a module-level "
        f"`_POST_MORTEM_DIR` constant (default `Path(__file__).parent`, "
        f"env-overridable via POST_MORTEM_DIR)."
    )


def test_post_mortem_dir_constant_exists_and_is_absolute():
    """A module-level constant must record the resolved post-mortem directory
    so operators (and tests) can introspect WHERE the search runs. The
    default value must be ABSOLUTE — a CWD-relative default re-introduces
    the very defect this ticket fixes."""
    autotuner_mod = _import_autotuner()
    name, value = _resolve_post_mortem_dir_attr(autotuner_mod)

    assert name is not None, (
        "PERF-007: autotuner must expose a module-level "
        "`_POST_MORTEM_DIR` (or `POST_MORTEM_DIR`) constant that names the "
        "resolved post-mortem search directory."
    )

    # Accept str or pathlib.Path; require absolute.
    resolved = pathlib.Path(value)
    assert resolved.is_absolute(), (
        f"PERF-007: autotuner.{name} = {value!r} must be an ABSOLUTE path. "
        f"A relative default re-creates the CWD-dependency this ticket fixes."
    )


# ===========================================================================
# Behavioural — finding files no longer depends on os.getcwd()
# ===========================================================================


@pytest.fixture
def isolated_cwd(tmp_path, monkeypatch):
    """Run the function from a directory that contains ZERO post-mortems.

    This is the core PERF-007 setup: the process CWD is deliberately
    NOT the project root. Pre-fix, glob.glob(\"post_mortem_*.json\") returns
    [] silently. Post-fix, the function reads from the explicit
    _POST_MORTEM_DIR (or POST_MORTEM_DIR env override) regardless of CWD.
    """
    sandbox_cwd = tmp_path / "unrelated_cwd"
    sandbox_cwd.mkdir()
    monkeypatch.chdir(sandbox_cwd)
    return sandbox_cwd


def test_finds_post_mortems_via_env_override_regardless_of_cwd(
    isolated_cwd,
    tmp_path,
    monkeypatch,
    capsys,
):
    """When POST_MORTEM_DIR points at a directory containing in-window
    post-mortems, the function MUST find them — even though the process CWD
    is a different, empty directory.

    Pre-fix this test fails because the literal glob runs against
    isolated_cwd (empty), returns [], and the function keeps its default
    deviation. Post-fix the env override anchors the glob to the real
    location.
    """
    autotuner_mod = _import_autotuner()

    pm_dir = tmp_path / "post_mortems"
    pm_dir.mkdir()

    current_date = "2026-05-10"
    exit_return = -1.0
    attempted = -0.5
    expected_deviation = exit_return - attempted  # derived, never hardcoded

    _write_post_mortem(
        pm_dir,
        "2026-05-03",
        _valid_post_mortem_json("Trailing Stop", exit_return, attempted),
    )

    monkeypatch.setenv("POST_MORTEM_DIR", str(pm_dir))

    result = autotuner_mod.calculate_historical_deviation(current_date)

    assert isinstance(result, dict)
    assert "Trailing Stop" in result, (
        "PERF-007: with POST_MORTEM_DIR set, the dated in-window post-mortem "
        "must contribute to the result. A missing 'Trailing Stop' key means "
        "the function still searched the (empty) CWD."
    )
    assert result["Trailing Stop"] == pytest.approx(expected_deviation, abs=1e-3), (
        f"PERF-007: with POST_MORTEM_DIR set, the dated post-mortem's "
        f"deviation must be returned. Expected {expected_deviation} "
        f"(= exit_return {exit_return} - attempted {attempted}), "
        f"got {result['Trailing Stop']}. "
        f"A default-equal result means the env override was ignored."
    )


def test_default_post_mortem_dir_is_project_root_not_cwd(
    isolated_cwd,
    tmp_path,
    monkeypatch,
):
    """Without POST_MORTEM_DIR set, the function must resolve the search to
    the project root (the directory containing autotuner.py) — NOT the
    process CWD. We assert this by checking the module constant resolves
    to the directory of autotuner.py.

    No env override is set; the empty isolated_cwd is the process CWD; the
    constant must STILL point at autotuner.py's directory.
    """
    autotuner_mod = _import_autotuner()
    monkeypatch.delenv("POST_MORTEM_DIR", raising=False)

    name, value = _resolve_post_mortem_dir_attr(autotuner_mod)
    assert name is not None, "PERF-007: autotuner must expose _POST_MORTEM_DIR / POST_MORTEM_DIR."

    expected = pathlib.Path(autotuner_mod.__file__).resolve().parent
    actual = pathlib.Path(value).resolve()

    assert actual == expected, (
        f"PERF-007: the default post-mortem directory must be the project "
        f"root (where autotuner.py lives) so the resolution is independent "
        f"of os.getcwd(). Expected {expected}, got {actual}. "
        f"Process CWD during this assertion is {os.getcwd()!r}."
    )


# ===========================================================================
# Behavioural — silent-empty is a defect; missing/empty dir must WARN
# ===========================================================================


def test_warns_when_resolved_dir_has_no_post_mortems(
    isolated_cwd,
    tmp_path,
    monkeypatch,
    capsys,
):
    """When the resolved post-mortem directory exists but contains zero
    matching files, the function must emit an operator-visible WARNING
    that names the resolved path. The pre-fix silent [] is the defect.
    """
    autotuner_mod = _import_autotuner()

    empty_pm_dir = tmp_path / "empty_post_mortems"
    empty_pm_dir.mkdir()
    monkeypatch.setenv("POST_MORTEM_DIR", str(empty_pm_dir))

    autotuner_mod.calculate_historical_deviation("2026-05-10")
    captured = capsys.readouterr()
    log_text = captured.out + captured.err

    assert "WARNING" in log_text.upper(), (
        "PERF-007: a resolved post-mortem directory with zero matching "
        "files must emit a WARNING-level log line. A silent zero-file "
        "result IS the defect. No WARNING found in output:\n"
        f"{log_text!r}"
    )
    assert str(empty_pm_dir) in log_text or empty_pm_dir.name in log_text, (
        f"PERF-007: the WARNING must NAME the resolved directory "
        f"({empty_pm_dir}) so the operator can see WHERE the function "
        f"looked. A WARNING with no path is just as opaque as silence.\n"
        f"Output:\n{log_text!r}"
    )


def test_warns_when_resolved_dir_is_missing(
    isolated_cwd,
    tmp_path,
    monkeypatch,
    capsys,
):
    """When the resolved post-mortem directory does NOT exist, the function
    must emit an operator-visible WARNING that names the missing path AND
    must not raise. A typoed POST_MORTEM_DIR env value is exactly the
    confusing failure mode this ticket eliminates.
    """
    autotuner_mod = _import_autotuner()

    nonexistent = tmp_path / "definitely_not_here"  # never created
    assert not nonexistent.exists()
    monkeypatch.setenv("POST_MORTEM_DIR", str(nonexistent))

    # Must not raise even though the dir is missing.
    result = autotuner_mod.calculate_historical_deviation("2026-05-10")
    assert isinstance(result, dict)

    captured = capsys.readouterr()
    log_text = captured.out + captured.err

    assert "WARNING" in log_text.upper(), (
        "PERF-007: a missing post-mortem directory must emit a WARNING. "
        f"No WARNING found:\n{log_text!r}"
    )
    assert str(nonexistent) in log_text or nonexistent.name in log_text, (
        f"PERF-007: the WARNING must NAME the missing directory "
        f"({nonexistent}) so the operator sees what to fix.\n"
        f"Output:\n{log_text!r}"
    )


# ===========================================================================
# Regression — existing happy-path behaviour preserved
# ===========================================================================


def test_existing_happy_path_preserved_when_files_in_resolved_dir(
    isolated_cwd,
    tmp_path,
    monkeypatch,
):
    """When the resolved post-mortem directory contains a valid, in-window
    post-mortem, the deviation it computes must equal exit_return -
    attempted_trigger_level (the existing behaviour). We assert via the
    derived value, never a hardcoded producer output.
    """
    autotuner_mod = _import_autotuner()

    pm_dir = tmp_path / "pms"
    pm_dir.mkdir()
    monkeypatch.setenv("POST_MORTEM_DIR", str(pm_dir))

    current_date = "2026-05-10"
    # Pick exit_return / attempted such that the derived deviation
    # (exit_return - attempted) is DISTINCT from every default in
    # deviation_dict — otherwise the test cannot distinguish "fix applied"
    # from "defaults returned unchanged".
    exit_return = -0.91
    attempted = -0.07
    expected = exit_return - attempted  # derived, not hardcoded

    # Sanity-check separation: assert the derived value is not equal to any
    # of the producer's default deviation values. If a future producer
    # change makes the defaults collide with this derived value, the test
    # must be re-tuned.
    derived_clashes_with_defaults = any(
        abs(expected - default) < 1e-3 for default in (0.0, -0.20, -0.40, -0.25)
    )
    assert not derived_clashes_with_defaults, (
        f"Test setup error: derived deviation {expected} collides with a "
        f"producer default — pick different exit_return/attempted values."
    )

    _write_post_mortem(
        pm_dir,
        "2026-05-02",
        _valid_post_mortem_json("VWAP Breakdown", exit_return, attempted),
    )

    result = autotuner_mod.calculate_historical_deviation(current_date)

    assert "VWAP Breakdown" in result
    assert result["VWAP Breakdown"] == pytest.approx(expected, abs=1e-3), (
        f"PERF-007 regression: a single in-window VWAP Breakdown post-mortem "
        f"must yield deviation = exit_return - attempted = {expected}. "
        f"Got {result['VWAP Breakdown']}."
    )


def test_out_of_window_files_in_resolved_dir_still_ignored(
    isolated_cwd,
    tmp_path,
    monkeypatch,
):
    """The 45-calendar-day dated-lookback filter must still apply against
    the resolved post-mortem directory. A file dated outside the window
    must NOT contribute, irrespective of how the directory is resolved.
    """
    autotuner_mod = _import_autotuner()

    pm_dir = tmp_path / "pms"
    pm_dir.mkdir()
    monkeypatch.setenv("POST_MORTEM_DIR", str(pm_dir))

    current_date = "2026-05-10"
    # Date well outside 45-calendar-day lookback (>45 days before current).
    _write_post_mortem(
        pm_dir,
        "2025-01-01",
        _valid_post_mortem_json("Trailing Stop", -1.0, -0.5),
    )

    result = autotuner_mod.calculate_historical_deviation(current_date)

    # The dict must come back with the DEFAULT Trailing Stop deviation —
    # i.e. the out-of-window file did not move the value. The default itself
    # is a producer constant, so assert by COMPARISON to a parallel call from
    # an empty directory rather than a hardcoded literal.
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.setenv("POST_MORTEM_DIR", str(empty_dir))
    baseline = autotuner_mod.calculate_historical_deviation(current_date)

    assert result["Trailing Stop"] == baseline["Trailing Stop"], (
        "PERF-007 regression: an out-of-window post-mortem must NOT shift "
        "the deviation away from the default. The dated lookback gate "
        "must still apply after the directory-anchor fix. "
        f"with-out-of-window={result['Trailing Stop']} "
        f"empty-dir-baseline={baseline['Trailing Stop']}."
    )
