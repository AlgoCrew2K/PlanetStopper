"""
DE-PRISM-DOTENV — RED tests for load_dotenv() hardening in prism_audit_write CLI.

AC-1 (dotenv honored): with DB_PATH present only in a .env file (NOT in the
process/shell env), invoking the prism_audit_write CLI from an arbitrary cwd
writes the audit row to the .env's DB_PATH, not the relative-fallback DB.

Root cause (current state — RED):
    advisors/prism_audit_write.py imports only argparse and sys.  There is no
    load_dotenv() call.  When the CLI is invoked from a cwd that does not have
    DB_PATH in the shell env, database._db_file() falls back to the cwd-relative
    alphabot_state.db — a silent split-brain write to the wrong DB.

Fix (for pf-impl-backend):
    Add `from dotenv import load_dotenv; load_dotenv()` at module level in
    advisors/prism_audit_write.py, before the lazy `import database` inside
    _main().  This populates os.environ["DB_PATH"] from .env so _db_file()
    resolves to the correct path regardless of cwd.

Mocking strategy:
    - All tests use subprocess (not in-process import) so each run gets a fresh
      os.environ that we fully control.
    - The subprocess env is stripped of DB_PATH; the .env file is the only
      source of DB_PATH.
    - The temp DB basename is "prism_test_audit.db" — safe from the pytest
      sentinel guard (which only blocks "alphabot_state.db").
    - No live network, no live Alpaca, no live Composer.

Adversarial RED intent:
    - test_cli_honors_dotenv_db_path_when_not_in_shell_env: without load_dotenv(),
      the CLI either crashes (RuntimeError from _db_file or sqlite3.OperationalError
      for missing DB) OR writes to the wrong cwd-local DB.  The assertion checks
      both the exit code AND that the intended temp DB received the row.
    - test_cli_shell_env_wins_over_dotenv: load_dotenv(override=False) must not
      clobber a shell-env DB_PATH — existing behavior preserved.
    - test_cli_missing_dotenv_does_not_crash: no .env present → load_dotenv() is a
      no-op; the CLI should still start without AttributeError/ImportError from
      load_dotenv itself.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import subprocess
import sys
import textwrap

_WORKTREE = pathlib.Path(__file__).resolve().parent.parent.parent
_PYTHON = sys.executable


def _clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return a stripped copy of os.environ without DB_PATH.

    Removes DB_PATH (and the pytest-injected session value) so the subprocess
    starts without any pre-existing DB_PATH.

    PYTHONPATH is explicitly set to the worktree root so `python -m
    advisors.prism_audit_write` can locate the `advisors` package when the
    subprocess cwd is an arbitrary temp directory (not the repo root).  The
    existing production usage runs from the repo root (cwd auto-resolve), but
    AC-1 deliberately runs from a temp cwd to prove .env is honoured; PYTHONPATH
    is the correct mechanism to keep the module findable without changing cwd.
    """
    env = {k: v for k, v in os.environ.items() if k != "DB_PATH"}
    # Inject worktree root so `advisors` package is importable from any cwd.
    existing_pythonpath = env.get("PYTHONPATH", "")
    worktree_str = str(_WORKTREE)
    if existing_pythonpath:
        env["PYTHONPATH"] = f"{worktree_str}{os.pathsep}{existing_pythonpath}"
    else:
        env["PYTHONPATH"] = worktree_str
    if extra:
        env.update(extra)
    return env


def _init_db_at(db_path: pathlib.Path) -> None:
    """Initialise a minimal prism_audit_log table in a fresh SQLite file.

    AC-3: replaced the subprocess spawn (which started a new Python process and
    cost ~30 MB + startup overhead) with a direct in-process call.  The
    conftest.py _isolate_db autouse fixture + per-call DB_PATH override ensures
    the production DB is never touched.
    """
    import os  # noqa: PLC0415

    import database  # noqa: PLC0415

    os.environ["DB_PATH"] = str(db_path)
    database.init_db()


# ===========================================================================
# AC-1: CLI honors .env DB_PATH when not in shell env
# ===========================================================================


def test_cli_honors_dotenv_db_path_when_not_in_shell_env(tmp_path):
    """prism_audit_write CLI must read DB_PATH from .env when not in shell env.

    Setup:
    - A temp DB is initialised at <tmp_path>/prism_test_audit.db.
    - A .env file containing DB_PATH=<temp_db_path> is written to <tmp_path>.
    - The CLI is run from <tmp_path> as cwd.
    - DB_PATH is stripped from the subprocess env.

    Without load_dotenv() in prism_audit_write.py (RED state):
      database._db_file() either raises RuntimeError (if basename check fires)
      or resolves to a cwd-relative alphabot_state.db.  The CLI may exit non-zero
      or write to the wrong file.  Either way, the row does NOT appear in
      <tmp_path>/prism_test_audit.db.

    With load_dotenv() at module level (GREEN state):
      DB_PATH is populated before the lazy import database fires inside _main().
      The row lands in <tmp_path>/prism_test_audit.db.  We verify via direct
      sqlite3 read of that file.
    """
    db_path = tmp_path / "prism_test_audit.db"
    _init_db_at(db_path)

    # Write the .env into cwd so load_dotenv() finds it.
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(f"DB_PATH={db_path}\n", encoding="utf-8")

    env = _clean_env()  # no DB_PATH in env

    result = subprocess.run(
        [
            _PYTHON,
            "-m",
            "advisors.prism_audit_write",
            "--run-id",
            "dotenv-test-run-001",
            "--role",
            "test-agent",
            "--phase",
            "test-phase",
        ],
        input="test content from AC-1 dotenv hardening",
        capture_output=True,
        text=True,
        cwd=str(tmp_path),  # cwd = tmp_path, where .env lives
        env=env,
        timeout=30,
    )

    # Primary assertion: exit 0 and a row id on stdout.
    assert result.returncode == 0, (
        "prism_audit_write CLI must exit 0 when DB_PATH is provided only via .env. "
        f"Got exit code {result.returncode}. "
        f"stderr: {result.stderr!r}. "
        "Root cause: advisors/prism_audit_write.py does not call load_dotenv() "
        "so DB_PATH from .env is never loaded into os.environ before _db_file() runs."
    )

    row_id_text = result.stdout.strip()
    assert row_id_text.isdigit(), (
        f"prism_audit_write must print the inserted row id (an integer) on stdout. "
        f"Got: {row_id_text!r}. stderr: {result.stderr!r}"
    )

    # Secondary assertion: the row is in the correct DB (not some other file).
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT run_id, agent_role, phase FROM prism_audit_log WHERE run_id = ?",
            ("dotenv-test-run-001",),
        ).fetchall()
    finally:
        con.close()

    assert len(rows) == 1, (
        f"Expected 1 row in {db_path} for run_id='dotenv-test-run-001', "
        f"found {len(rows)}. "
        "The CLI must write to the DB_PATH from .env, not a cwd-relative fallback."
    )
    assert rows[0][1] == "test-agent", (
        f"Row agent_role mismatch: expected 'test-agent', got {rows[0][1]!r}"
    )
    assert rows[0][2] == "test-phase", (
        f"Row phase mismatch: expected 'test-phase', got {rows[0][2]!r}"
    )


def test_cli_shell_env_wins_over_dotenv(tmp_path):
    """Shell env DB_PATH must take precedence over .env DB_PATH (load_dotenv default).

    load_dotenv() by default does NOT override existing env vars (override=False).
    This test verifies that if DB_PATH is already in the shell env, the .env file's
    DB_PATH is ignored — preserving the existing behavior for production callers
    that set DB_PATH explicitly.

    Setup:
    - shell_db: initialised, DB_PATH in subprocess env points here
    - dotenv_db: initialised, .env in cwd points here
    - The CLI must write to shell_db, not dotenv_db.
    """
    shell_db = tmp_path / "shell_env_prism_test.db"
    dotenv_db = tmp_path / "dotenv_prism_test.db"
    _init_db_at(shell_db)
    _init_db_at(dotenv_db)

    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(f"DB_PATH={dotenv_db}\n", encoding="utf-8")

    # Shell env has DB_PATH pointing at shell_db.
    env = _clean_env(extra={"DB_PATH": str(shell_db)})

    result = subprocess.run(
        [
            _PYTHON,
            "-m",
            "advisors.prism_audit_write",
            "--run-id",
            "shell-env-wins-run-001",
            "--role",
            "test-shell-agent",
            "--phase",
            "test-shell-phase",
        ],
        input="shell-env-wins test content",
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, (
        "CLI must exit 0 when DB_PATH is set in shell env (and .env also exists). "
        f"Exit code: {result.returncode}. stderr: {result.stderr!r}"
    )

    # Row must be in shell_db, not dotenv_db.
    for db_file, expect_row, label in [
        (shell_db, True, "shell_db (shell-env DB_PATH)"),
        (dotenv_db, False, "dotenv_db (.env DB_PATH, must NOT receive the row)"),
    ]:
        con = sqlite3.connect(str(db_file))
        try:
            rows = con.execute(
                "SELECT run_id FROM prism_audit_log WHERE run_id = ?",
                ("shell-env-wins-run-001",),
            ).fetchall()
        finally:
            con.close()

        if expect_row:
            assert len(rows) == 1, (
                f"Expected 1 row in {label} for run_id='shell-env-wins-run-001', "
                f"found {len(rows)}. Shell-env DB_PATH must win over .env."
            )
        else:
            assert len(rows) == 0, (
                f"Expected 0 rows in {label} (the .env path must be ignored when "
                f"shell-env DB_PATH is already set). Found {len(rows)} rows. "
                "load_dotenv() must not override an existing shell-env DB_PATH."
            )


# ===========================================================================
# AC-1 edge case: missing .env
# ===========================================================================


def test_cli_missing_dotenv_does_not_crash(tmp_path):
    """load_dotenv() is a no-op when no .env file is present — must not crash.

    When the CLI is invoked from a directory with no .env file and DB_PATH is
    set in the shell env (normal production use), the call to load_dotenv() must
    not raise ImportError, AttributeError, or any other exception.

    This is an import-path test: if load_dotenv is imported but python-dotenv is
    not installed, the CLI would crash on import.  If load_dotenv() raises when
    no file exists, same problem.  Both manifest as a non-zero exit code with an
    error in stderr.

    Note: The CLI is expected to FAIL (non-zero exit) for missing DB_PATH in the
    DB resolution step — that is fine and expected (a different code path).  We
    only care that the failure is NOT an ImportError/AttributeError from
    load_dotenv itself.  We supply a valid DB_PATH so we can distinguish a
    load_dotenv crash from a DB-path crash.
    """
    # Supply a valid DB_PATH so any crash is attributable to load_dotenv, not DB.
    db_path = tmp_path / "nodotenv_prism_test.db"
    _init_db_at(db_path)

    # cwd with no .env file.
    no_dotenv_dir = tmp_path / "no_dotenv_subdir"
    no_dotenv_dir.mkdir()
    assert not (no_dotenv_dir / ".env").exists(), "Test setup: .env must not exist here"

    env = _clean_env(extra={"DB_PATH": str(db_path)})

    result = subprocess.run(
        [
            _PYTHON,
            "-m",
            "advisors.prism_audit_write",
            "--run-id",
            "nodotenv-test-run-001",
            "--role",
            "test-nodotenv",
            "--phase",
            "test-phase",
        ],
        input="no dotenv test content",
        capture_output=True,
        text=True,
        cwd=str(no_dotenv_dir),
        env=env,
        timeout=30,
    )

    # The CLI must not crash due to load_dotenv — either succeed (exit 0) or
    # fail for an unrelated reason (but NOT ImportError/AttributeError/ModuleNotFoundError
    # from load_dotenv being absent or raising on a missing .env file).
    stderr_lower = result.stderr.lower()
    assert "importerror" not in stderr_lower, (
        "CLI crashed with ImportError — python-dotenv may not be installed, "
        "or load_dotenv import failed. "
        f"stderr: {result.stderr!r}"
    )
    assert "attributeerror" not in stderr_lower, (
        "CLI crashed with AttributeError — likely load_dotenv() raised unexpectedly "
        "when no .env file exists. load_dotenv() must be a no-op when .env is absent. "
        f"stderr: {result.stderr!r}"
    )
    assert "modulenotfounderror" not in stderr_lower, (
        "CLI crashed with ModuleNotFoundError — python-dotenv is not installed in "
        "the test environment, or the import path is wrong. "
        f"stderr: {result.stderr!r}"
    )

    # If the CLI succeeded, verify the row landed correctly (belt-and-suspenders).
    if result.returncode == 0:
        con = sqlite3.connect(str(db_path))
        try:
            rows = con.execute(
                "SELECT run_id FROM prism_audit_log WHERE run_id = ?",
                ("nodotenv-test-run-001",),
            ).fetchall()
        finally:
            con.close()
        assert len(rows) == 1, (
            f"CLI exited 0 but row not found in DB. DB: {db_path}. stdout: {result.stdout!r}"
        )
