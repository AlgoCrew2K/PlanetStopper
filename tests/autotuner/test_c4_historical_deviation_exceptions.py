"""
Cluster 4 — AC-7 (audit finding-13): calculate_historical_deviation's bare
`except` is replaced with a specific exception set and a logged WARNING naming
the offending file.

RED tests, written before the implementation.

Audit finding-13: the per-file loop in calculate_historical_deviation
(autotuner.py:~312) uses a bare `except:` that catches EVERYTHING — malformed
JSON, KeyError, even KeyboardInterrupt / MemoryError — and silently `continue`s.
A corrupt post_mortem_*.json is dropped with no log line. The deviation penalties
feed straight into the autotuner objective (`deviation_dict.get(reason, -0.20)`),
so a silently-dropped post-mortem changes the objective with zero operator
visibility.

AC-7 requires:
  - the bare `except:` replaced with a specific set:
    json.JSONDecodeError, OSError, KeyError, ValueError
  - a logged WARNING that NAMES the offending file
  - KeyboardInterrupt / MemoryError must NO LONGER be swallowed

These tests run calculate_historical_deviation against a temp working directory
containing a deliberately malformed post-mortem file. No live API calls.
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


@pytest.fixture
def deviation_workdir(tmp_path, monkeypatch):
    """Run calculate_historical_deviation against an isolated post-mortem dir.

    Post PERF-007, calculate_historical_deviation resolves its glob through
    the `POST_MORTEM_DIR` env override (or the absolute `_POST_MORTEM_DIR`
    default) — NOT the process CWD. This fixture writes post-mortems into
    `tmp_path` and points the env override there so the function consumes
    them. We also chdir so any incidental CWD-relative reads (logs, side
    files) stay inside the sandbox. Scope: function — each test gets a
    fresh, isolated directory; no shared module-level state.
    """
    monkeypatch.setenv("POST_MORTEM_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_post_mortem(directory: pathlib.Path, date_str: str, content: str) -> pathlib.Path:
    """Write a raw post_mortem_<date>.json file with the given literal content."""
    path = directory / f"post_mortem_{date_str}.json"
    path.write_text(content, encoding="utf-8")
    return path


# A well-formed post-mortem the loop should consume without complaint.
def _valid_post_mortem_json(reason: str, exit_return: float, attempted: float) -> str:
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


# ===========================================================================
# Source-level — the bare except is gone, the specific set is present
# ===========================================================================


def test_no_bare_except_in_calculate_historical_deviation():
    """
    AC-7: calculate_historical_deviation must contain NO bare `except:` clause.

    AST inspection of the function body — a bare except is an ast.ExceptHandler
    whose `type` is None. The outer broad `except Exception as e` that prints the
    "Deviation calculation failed" fallback is acceptable (it is not bare and
    names Exception); only a TYPE-LESS handler fails this test.
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

    bare_handlers = [
        h for h in ast.walk(func) if isinstance(h, ast.ExceptHandler) and h.type is None
    ]
    assert not bare_handlers, (
        f"calculate_historical_deviation still has {len(bare_handlers)} bare "
        f"`except:` handler(s) at line(s) "
        f"{[h.lineno for h in bare_handlers]}. AC-7 requires a specific exception "
        f"set (json.JSONDecodeError, OSError, KeyError, ValueError)."
    )


def test_per_file_except_names_the_specific_exception_set():
    """
    AC-7: the per-file loop's except clause must name the specific set
    {json.JSONDecodeError, OSError, KeyError, ValueError}.

    AST: find the ExceptHandler nested inside the per-file `for` loop and assert
    its caught types are exactly that set (order-insensitive). A handler catching
    the bare `Exception` is NOT acceptable for the per-file clause — Exception
    still swallows unforeseen bugs silently.
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
    assert func is not None, "calculate_historical_deviation not found."

    def _handler_caught_names(handler: ast.ExceptHandler) -> set[str]:
        """Return the set of dotted exception names a handler catches."""
        names: set[str] = set()
        node = handler.type
        if node is None:
            return names
        items = node.elts if isinstance(node, ast.Tuple) else [node]
        for item in items:
            if isinstance(item, ast.Name):
                names.add(item.id)
            elif isinstance(item, ast.Attribute):
                # e.g. json.JSONDecodeError -> "JSONDecodeError"
                names.add(item.attr)
        return names

    required = {"JSONDecodeError", "OSError", "KeyError", "ValueError"}

    # The per-file handler is the one whose caught set is a superset of `required`.
    matching = [
        h
        for h in ast.walk(func)
        if isinstance(h, ast.ExceptHandler) and required.issubset(_handler_caught_names(h))
    ]
    assert matching, (
        "calculate_historical_deviation has no except clause catching the "
        "specific set {json.JSONDecodeError, OSError, KeyError, ValueError}. "
        "AC-7 requires the per-file bare `except:` replaced with exactly that set."
    )

    # Defence-in-depth: the per-file handler must not catch KeyboardInterrupt /
    # MemoryError. Those are BaseException-only and must propagate.
    for h in matching:
        caught = _handler_caught_names(h)
        assert "KeyboardInterrupt" not in caught and "BaseException" not in caught, (
            "The per-file except clause must not catch KeyboardInterrupt / "
            "BaseException — AC-7 requires those to propagate, not be swallowed."
        )


# ===========================================================================
# Behavioural — a malformed file is skipped with a WARNING naming the file
# ===========================================================================


def test_malformed_post_mortem_is_skipped_and_logged(deviation_workdir, capsys):
    """
    AC-7: a corrupt post-mortem JSON file must be skipped AND must produce a
    logged WARNING that NAMES the offending file.

    Setup: one malformed post-mortem (invalid JSON) plus one valid post-mortem,
    both inside the dated lookback window. The function must:
      - not raise,
      - still process the valid file,
      - emit a WARNING-level log line containing the malformed file's name.

    The malformed filename is asserted by NAME — derived from the fixture-style
    file the test itself wrote, never a hardcoded producer value.
    """
    autotuner = _import_autotuner()

    current_date = "2026-05-10"
    # Both dates are inside the 45-calendar-day lookback and strictly before
    # current_date, so both files are in-window.
    malformed = _write_post_mortem(deviation_workdir, "2026-05-01", "{ this is not valid json ,,, ")
    _write_post_mortem(
        deviation_workdir,
        "2026-05-02",
        _valid_post_mortem_json("Trailing Stop", -1.0, -0.5),
    )

    # Must not raise on the malformed file.
    result = autotuner.calculate_historical_deviation(current_date)

    assert isinstance(result, dict), (
        "calculate_historical_deviation must return a deviation dict even when a "
        "post-mortem file is malformed."
    )

    captured = capsys.readouterr()
    log_text = captured.out + captured.err

    assert "WARNING" in log_text.upper(), (
        "AC-7: a malformed post-mortem file must produce a WARNING-level log "
        f"line. No WARNING found in output:\n{log_text!r}"
    )
    assert malformed.name in log_text, (
        f"AC-7: the WARNING must NAME the offending file ('{malformed.name}'). "
        f"A silent skip with no filename is exactly the finding-13 defect.\n"
        f"Output:\n{log_text!r}"
    )


def test_valid_post_mortem_still_processed_alongside_malformed(deviation_workdir):
    """
    AC-7: replacing the bare except must not change the happy path — a valid
    post-mortem in the window must still contribute to the deviation dict even
    when a sibling file is malformed.

    The valid file records a Trailing Stop trigger whose deviation
    (exit_return - attempted_trigger_level) the test computes itself from the
    values it wrote — no producer output is hardcoded.
    """
    autotuner = _import_autotuner()

    current_date = "2026-05-10"
    exit_return = -1.0
    attempted = -0.5
    expected_deviation = exit_return - attempted  # derived, not hardcoded

    _write_post_mortem(deviation_workdir, "2026-05-01", "{ corrupt ]]] not json")
    _write_post_mortem(
        deviation_workdir,
        "2026-05-03",
        _valid_post_mortem_json("Trailing Stop", exit_return, attempted),
    )

    result = autotuner.calculate_historical_deviation(current_date)

    assert "Trailing Stop" in result, (
        "calculate_historical_deviation must return a 'Trailing Stop' key."
    )
    # Single valid trigger -> the averaged deviation equals that trigger's
    # deviation. The function rounds to 3 decimals (existing behaviour).
    assert result["Trailing Stop"] == pytest.approx(expected_deviation, abs=1e-3), (
        f"The valid post-mortem's Trailing Stop deviation must survive a "
        f"malformed sibling file. Expected {expected_deviation} "
        f"(= exit_return {exit_return} - attempted {attempted}), "
        f"got {result['Trailing Stop']}."
    )


def test_keyboard_interrupt_is_not_swallowed(deviation_workdir, monkeypatch):
    """
    AC-7: replacing the bare `except:` with a specific set means a
    KeyboardInterrupt raised while reading a post-mortem must PROPAGATE — the old
    bare except swallowed it.

    We force the failure by patching the builtin `open` to raise KeyboardInterrupt
    when the per-file loop reads a post-mortem. With the bare except, the function
    would catch it and `continue`; with the AC-7 specific set, it propagates.
    """
    autotuner = _import_autotuner()

    _write_post_mortem(
        deviation_workdir,
        "2026-05-01",
        _valid_post_mortem_json("Trailing Stop", -1.0, -0.5),
    )

    real_open = open

    def interrupting_open(path, *args, **kwargs):
        # Only interrupt on the post-mortem read; leave any other open() intact.
        # Post PERF-007 the path is absolute (anchored to the resolved
        # POST_MORTEM_DIR), so match on the filename component, not a prefix.
        if isinstance(path, str) and pathlib.Path(path).name.startswith("post_mortem_"):
            raise KeyboardInterrupt("simulated operator interrupt during file read")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", interrupting_open)

    with pytest.raises(KeyboardInterrupt):
        autotuner.calculate_historical_deviation("2026-05-10")
