"""The shared boolean environment-flag definition and the two process-environment flag reads.

The shape of ``env_flags``.

Two sites read a flag from the process environment at import: ``alpha_bot_execution.LIVE_EXECUTION``
(``alpha_bot_execution.py:75``) and ``app.config["SESSION_COOKIE_SECURE"]`` (``app.py:175``). Each is
characterized in ONE fresh interpreter per module that loops over every spelling of
``tests/_env_flag_spellings.py``, dropping the module from ``sys.modules`` between values. The
interpreter replaces ``dotenv.load_dotenv`` with a no-op so the developer ``.env`` never decides a
value, and points ``DB_PATH`` at a temp file so no production state database is opened.

The expected value of every case is the reference expression copied verbatim
(``tests/_env_flag_spellings.ref_is_true``), never a literal copied from a run. These cases pass on
the reference code by design (characterization); ``TestEnvFlagsDefinition`` fails until the shared
module exists.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests._env_flag_spellings import ABSENT, SPELLINGS, ref_is_true

REPO_ROOT = Path(__file__).resolve().parents[1]

_RESULT_MARKER = "ENV_FLAG_RESULT::"

# One interpreter, one module, every spelling. argv: module name, flag variable, attribute probe.
_LOOP_SCRIPT = textwrap.dedent(
    """
    import importlib, json, os, sys

    sys.path.insert(0, {root!r})
    import dotenv

    dotenv.load_dotenv = lambda *a, **k: False
    module_name, variable, probe = sys.argv[1], sys.argv[2], sys.argv[3]
    cases = json.loads(sys.argv[4])  # [[value or null for absent], ...] as {{"v": ..., "absent": bool}}
    results = []
    for case in cases:
        sys.modules.pop(module_name, None)
        if case["absent"]:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = case["v"]
        module = importlib.import_module(module_name)
        if probe == "LIVE_EXECUTION":
            observed = module.LIVE_EXECUTION
        else:
            observed = module.app.config["SESSION_COOKIE_SECURE"]
        results.append(observed)
    print({marker!r} + json.dumps(results))
    """
)


def _run_loop(module_name: str, variable: str, probe: str, tmp_path: Path) -> list:
    cases = [
        {"v": None, "absent": True} if value is ABSENT else {"v": value, "absent": False}
        for value in (*SPELLINGS, ABSENT)
    ]
    env = {
        key: val
        for key, val in os.environ.items()
        if key not in ("LIVE_EXECUTION", "SESSION_COOKIE_SECURE")
    }
    env["DB_PATH"] = str(tmp_path / "isolated_state.db")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    script = tmp_path / "loop_script.py"
    script.write_text(_LOOP_SCRIPT.format(root=str(REPO_ROOT), marker=_RESULT_MARKER))
    completed = subprocess.run(
        [sys.executable, str(script), module_name, variable, probe, json.dumps(cases)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, (
        f"the fresh interpreter failed (exit {completed.returncode}): {completed.stderr[-800:]}"
    )
    lines = [ln for ln in completed.stdout.splitlines() if ln.startswith(_RESULT_MARKER)]
    assert len(lines) == 1, "the fresh interpreter printed no result line"
    return json.loads(lines[0][len(_RESULT_MARKER) :])


def _expected_for_cases(default: str) -> list[bool]:
    return [ref_is_true(default if v is ABSENT else v) for v in (*SPELLINGS, ABSENT)]


@pytest.fixture(scope="module")
def engine_flag_results(tmp_path_factory) -> list:
    return _run_loop(
        "alpha_bot_execution",
        "LIVE_EXECUTION",
        "LIVE_EXECUTION",
        tmp_path_factory.mktemp("engine_flag"),
    )


@pytest.fixture(scope="module")
def app_flag_results(tmp_path_factory) -> list:
    return _run_loop(
        "app",
        "SESSION_COOKIE_SECURE",
        "SESSION_COOKIE_SECURE",
        tmp_path_factory.mktemp("app_flag"),
    )


class TestEngineLiveExecutionFlag:
    """``alpha_bot_execution.LIVE_EXECUTION`` equals the reference for every spelling."""

    @pytest.mark.parametrize("index", range(len(SPELLINGS) + 1))
    def test_engine_live_execution_flag_matches_reference_expression(
        self, engine_flag_results, index
    ):
        cases = (*SPELLINGS, ABSENT)
        expected = _expected_for_cases("False")[index]
        assert engine_flag_results[index] is expected, (
            f"LIVE_EXECUTION={cases[index]!r} resolved to {engine_flag_results[index]!r}"
        )

    def test_engine_flag_family_has_both_outcomes_and_resolves_to_booleans(
        self, engine_flag_results
    ):
        assert all(isinstance(v, bool) for v in engine_flag_results)
        assert True in engine_flag_results and False in engine_flag_results


class TestSessionCookieSecureFlag:
    """``app.config["SESSION_COOKIE_SECURE"]``; the absent case reads "" and gives False."""

    @pytest.mark.parametrize("index", range(len(SPELLINGS) + 1))
    def test_session_cookie_secure_flag_matches_reference_expression(self, app_flag_results, index):
        cases = (*SPELLINGS, ABSENT)
        expected = _expected_for_cases("")[index]
        assert app_flag_results[index] is expected, (
            f"SESSION_COOKIE_SECURE={cases[index]!r} resolved to {app_flag_results[index]!r}"
        )

    def test_absent_session_cookie_secure_is_false(self, app_flag_results):
        assert app_flag_results[-1] is False

    def test_session_cookie_secure_family_has_both_outcomes(self, app_flag_results):
        assert True in app_flag_results and False in app_flag_results


class TestEnvFlagsDefinition:
    """The one shared definition: ``env_flags`` at the repository root, stdlib only."""

    def test_true_spellings_are_the_three_reference_spellings(self):
        import env_flags

        assert tuple(env_flags.TRUE_SPELLINGS) == ("true", "1", "yes")

    @pytest.mark.parametrize("raw", SPELLINGS)
    def test_is_true_equals_the_reference_expression_for_every_spelling(self, raw):
        import env_flags

        assert env_flags.is_true(raw) is ref_is_true(raw)

    def test_is_true_raises_attribute_error_on_none_as_the_inline_expression_did(self):
        import env_flags

        with pytest.raises(AttributeError):
            ref_is_true(None)  # the reference raises; the helper must raise the same type
        with pytest.raises(AttributeError):
            env_flags.is_true(None)

    def test_is_true_does_not_strip_whitespace(self):
        import env_flags

        assert env_flags.is_true(" true") is False
        assert env_flags.is_true("true ") is False

    def test_importing_env_flags_loads_no_first_party_module(self, tmp_path):
        """Stdlib only, so the engine, the app and the sleeve tick can all import it."""
        probe = (
            "import sys\n"
            f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
            "before = set(sys.modules)\n"
            "import env_flags\n"
            "first_party = ('alpha_bot_execution', 'app', 'database', 'analytics', 'sleeves',\n"
            "               'math_engine', 'autotuner')\n"
            "loaded = sorted(m for m in set(sys.modules) - before if m.split('.')[0] in first_party)\n"
            "print('LOADED::' + ','.join(loaded))\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr[-500:]
        assert completed.stdout.strip().endswith("LOADED::"), completed.stdout
