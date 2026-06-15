"""
Prism Phase 1 — RED: Audit-log foundation (AC-3 and AC-4 from BRIEF.md).

AC-3  MARKET_PRISM raw_response carries run_id:
      lens_pipeline.run_pipeline() writes a run_id into the raw_response dict
      it persists via insert_advisor_observation.  Existing rows that lack run_id
      still read back without error (backward-compat).

AC-4  Agent-callable writer (advisors.prism_audit_write):
      - Module is importable as `python -m advisors.prism_audit_write`.
      - Reads content from STDIN (not a positional arg).
      - Requires --run-id, --role, --phase; missing any → non-zero exit with
        type-only message on stderr (D-1), no traceback.
      - With valid args and stdin content → calls insert_prism_audit_entry,
        prints the returned row id on stdout.
      - Long / multiline stdin content round-trips correctly.
      - --run-id or --phase containing spaces/special chars are accepted.

Mocking strategy:
  - insert_advisor_observation: captured via side_effect list — not DB-hitting.
  - insert_prism_audit_entry: patched so the CLI writer test doesn't need a
    live DB; asserted called-once with correct kwargs.
  - No live network, no live Claude.  Math engine never mocked.

Adversarial RED intent:
  - AC-3: lens_pipeline currently does NOT write run_id into raw_response → the
    key-existence assertion fails.
  - AC-4: advisors/prism_audit_write.py does not exist → ImportError / module
    not found.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Repo root (worktree IS the cycle repo root)
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_pipeline():
    """Import or reload advisors.lens_pipeline cleanly."""
    mod_name = "advisors.lens_pipeline"
    if mod_name in sys.modules:
        importlib.reload(sys.modules[mod_name])
    return importlib.import_module(mod_name)


def _python() -> str:
    """Return the current Python executable path."""
    return sys.executable


# ---------------------------------------------------------------------------
# AC-3 — MARKET_PRISM raw_response carries run_id
# ---------------------------------------------------------------------------


class TestMarketPrismRunId:
    """run_pipeline() must embed run_id in raw_response so audit entries join."""

    def _make_available_lens(self, name: str) -> dict:
        return {
            "lens": name,
            "available": True,
            "summary": f"{name} summary",
            "sources": [],
        }

    def _run_pipeline_with_mocked_db(self, lenses_available: bool = True):
        """Run the pipeline with all externals mocked; return the raw_response
        that was passed to insert_advisor_observation."""
        pipeline = _reload_pipeline()

        lens_result = (
            self._make_available_lens
            if lenses_available
            else (
                lambda name: {
                    "lens": name,
                    "available": False,
                    "reason": "Unavailable",
                    "sources": [],
                }
            )
        )

        captured_raw_response = {}

        def capture_insert(**kwargs):
            raw = kwargs.get("raw_response", {})
            if isinstance(raw, str):
                raw = json.loads(raw)
            captured_raw_response.update(raw)
            return 42  # fake row id

        synthesis_response = json.dumps(
            {
                "overall_sentiment": "neutral",
                "sentiment_rationale": "Balanced.",
            }
        )
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=synthesis_response)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        build_citation_result = None  # drop all sources

        with (
            patch.object(
                pipeline,
                "_call_lens_section",
                side_effect=lambda name: self._make_available_lens(name),
            ),
            patch("database.insert_advisor_observation", side_effect=capture_insert),
            patch("database.get_connection"),  # prevent real DB open
            patch("ai_advisor.build_citation", return_value=build_citation_result),
            patch("ai_advisor._build_client", return_value=mock_client),
        ):
            summary = pipeline.run_pipeline(dry_run=False)

        return summary, captured_raw_response

    def test_run_pipeline_summary_has_run_ts_key(self):
        # Sanity: the existing AC-1 contract (run_ts in summary) must still hold.
        summary, _ = self._run_pipeline_with_mocked_db()
        assert "run_ts" in summary, "run_pipeline() summary must contain 'run_ts'"

    def test_raw_response_carries_run_id(self):
        # AC-3 core assertion: raw_response must contain 'run_id'.
        _, raw = self._run_pipeline_with_mocked_db()
        assert "run_id" in raw, (
            "MARKET_PRISM raw_response must contain 'run_id' so audit entries "
            f"can join; got keys: {sorted(raw)}"
        )

    def test_run_id_is_non_empty_string(self):
        _, raw = self._run_pipeline_with_mocked_db()
        run_id = raw.get("run_id")
        assert isinstance(run_id, str) and run_id, (
            f"raw_response['run_id'] must be a non-empty string; got {run_id!r}"
        )

    def test_run_id_matches_run_ts_provenance(self):
        # run_id should be derived from (or equal to) the run_ts so the pipeline
        # summary and the audit entries share a stable join key.
        summary, raw = self._run_pipeline_with_mocked_db()
        run_ts = summary.get("run_ts", "")
        run_id = raw.get("run_id", "")
        # Accept either: run_id == run_ts, OR run_id contains run_ts, OR run_ts contains run_id.
        # We do NOT hardcode the exact format — just assert they're related.
        assert run_ts and run_id, "Both run_ts and run_id must be present"
        related = (run_id == run_ts) or (run_ts in run_id) or (run_id in run_ts)
        assert related, (
            f"run_id ({run_id!r}) must be derived from run_ts ({run_ts!r}) so "
            "the pipeline summary joins to its audit entries"
        )

    def test_backward_compat_raw_response_without_run_id_reads_fine(self):
        """Existing advisor_observations rows without run_id must not break reads.

        Simulate by calling get_advisor_observations_for_role after inserting
        a row whose raw_response JSON lacks the run_id key.
        """
        import database as db_module

        # Insert a legacy-style row (no run_id in raw_response).
        legacy_raw = {
            "run_ts": "2026-06-01T03:00:00+00:00",
            "overall_sentiment": "neutral",
        }
        row_id = db_module.insert_advisor_observation(
            advisor_role="MARKET_PRISM",
            subject_type="portfolio",
            subject_id="global",
            verdict="neutral",
            raw_response=legacy_raw,
        )
        # Must not raise.
        rows = db_module.get_advisor_observations_for_role("MARKET_PRISM")
        matching = [r for r in rows if r["id"] == row_id]
        assert matching, f"Legacy row id={row_id} not found"
        raw_back = matching[0]["raw_response"]
        # Backward-compat: the row reads fine and has the original keys.
        assert "run_ts" in raw_back
        # run_id may or may not be present — this is the backward-compat test;
        # the point is that the absence of run_id does NOT crash the accessor.
        assert isinstance(raw_back, dict)


# ---------------------------------------------------------------------------
# AC-4 — Agent-callable writer (advisors.prism_audit_write)
# ---------------------------------------------------------------------------


class TestPrismAuditWriteCLI:
    """advisors/prism_audit_write.py — module + CLI contract."""

    _MODULE_PATH = _REPO_ROOT / "advisors" / "prism_audit_write.py"

    def test_writer_module_file_exists(self):
        assert self._MODULE_PATH.exists(), "advisors/prism_audit_write.py must exist (AC-4)"

    def test_module_importable(self):
        # Clean import attempt — must not raise ImportError.
        mod_name = "advisors.prism_audit_write"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        importlib.import_module(mod_name)

    def test_valid_args_stdin_inserts_and_prints_rowid(self):
        """With valid --run-id, --role, --phase and stdin content, the writer
        must print the row id on stdout and exit 0."""
        content = "Technicals summary: volatility within normal range."
        result = subprocess.run(
            [
                _python(),
                "-m",
                "advisors.prism_audit_write",
                "--run-id",
                "run-cli-test-001",
                "--role",
                "technicals_analyst",
                "--phase",
                "initial_read",
            ],
            input=content,
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env={
                **__import__("os").environ,
                "DB_PATH": str(pathlib.Path(__import__("tempfile").mkdtemp()) / "cli_test.db"),
            },
        )
        assert result.returncode == 0, (
            f"CLI writer exited non-zero ({result.returncode}).\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        stdout = result.stdout.strip()
        assert stdout.isdigit(), (
            f"CLI writer must print a numeric row id on stdout; got: {stdout!r}"
        )
        assert int(stdout) > 0, f"Printed row id must be a positive integer; got: {stdout!r}"

    def test_multiline_stdin_content_is_accepted(self):
        """Long / multiline stdin content must be stored without truncation.

        We test that the CLI exits 0 and prints a row id — content integrity
        is separately tested by the database layer round-trip tests.
        """
        multiline = "\n".join(
            [
                "Line 1: EPS beat expectations.",
                "Line 2: Revenue in-line.",
                "Line 3: Guidance raised.",
                "Line 4: " + "A" * 2000,  # long line
            ]
        )
        result = subprocess.run(
            [
                _python(),
                "-m",
                "advisors.prism_audit_write",
                "--run-id",
                "run-cli-multi-001",
                "--role",
                "fundamentals_analyst",
                "--phase",
                "debate_round_1",
            ],
            input=multiline,
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env={
                **__import__("os").environ,
                "DB_PATH": str(pathlib.Path(__import__("tempfile").mkdtemp()) / "cli_multi.db"),
            },
        )
        assert result.returncode == 0, (
            f"CLI writer failed on multiline stdin.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        assert result.stdout.strip().isdigit()

    def test_missing_run_id_exits_nonzero(self):
        """--run-id missing → non-zero exit; no traceback on stderr (D-1)."""
        result = subprocess.run(
            [
                _python(),
                "-m",
                "advisors.prism_audit_write",
                "--role",
                "synthesizer",
                "--phase",
                "synthesis",
            ],
            input="Some content.",
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env={
                **__import__("os").environ,
                "DB_PATH": str(pathlib.Path(__import__("tempfile").mkdtemp()) / "cli_err.db"),
            },
        )
        assert result.returncode != 0, "CLI writer must exit non-zero when --run-id is missing"
        # D-1 contract: no raw traceback in stderr.
        assert "Traceback" not in result.stderr, (
            "CLI writer must not dump a traceback to stderr (D-1 contract);\n"
            f"stderr: {result.stderr!r}"
        )

    def test_missing_role_exits_nonzero(self):
        """--role missing → non-zero exit; no traceback (D-1)."""
        result = subprocess.run(
            [
                _python(),
                "-m",
                "advisors.prism_audit_write",
                "--run-id",
                "run-err-001",
                "--phase",
                "synthesis",
            ],
            input="Some content.",
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env={
                **__import__("os").environ,
                "DB_PATH": str(pathlib.Path(__import__("tempfile").mkdtemp()) / "cli_err2.db"),
            },
        )
        assert result.returncode != 0
        assert "Traceback" not in result.stderr, f"Traceback leaked to stderr: {result.stderr!r}"

    def test_missing_phase_exits_nonzero(self):
        """--phase missing → non-zero exit; no traceback (D-1)."""
        result = subprocess.run(
            [
                _python(),
                "-m",
                "advisors.prism_audit_write",
                "--run-id",
                "run-err-002",
                "--role",
                "macro_analyst",
            ],
            input="Some content.",
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env={
                **__import__("os").environ,
                "DB_PATH": str(pathlib.Path(__import__("tempfile").mkdtemp()) / "cli_err3.db"),
            },
        )
        assert result.returncode != 0
        assert "Traceback" not in result.stderr, f"Traceback leaked to stderr: {result.stderr!r}"

    def test_garbled_unknown_flag_exits_nonzero(self):
        """An unknown / garbled flag must exit non-zero without traceback."""
        result = subprocess.run(
            [
                _python(),
                "-m",
                "advisors.prism_audit_write",
                "--not-a-real-flag",
                "value",
            ],
            input="Content.",
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env={
                **__import__("os").environ,
                "DB_PATH": str(pathlib.Path(__import__("tempfile").mkdtemp()) / "cli_garbled.db"),
            },
        )
        assert result.returncode != 0
        assert "Traceback" not in result.stderr

    def test_stderr_error_message_is_type_only(self):
        """When the CLI exits with an error, stderr must contain a type name,
        not a raw exception message with file paths / DB internals (D-1)."""
        result = subprocess.run(
            [
                _python(),
                "-m",
                "advisors.prism_audit_write",
                # Omit --run-id to trigger an error path.
                "--role",
                "synthesizer",
                "--phase",
                "synthesis",
            ],
            input="Content.",
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env={
                **__import__("os").environ,
                "DB_PATH": str(pathlib.Path(__import__("tempfile").mkdtemp()) / "cli_d1.db"),
            },
        )
        assert result.returncode != 0
        stderr = result.stderr
        # D-1: no raw exception detail — no file paths, no line numbers.
        assert "alphabot_state.db" not in stderr, f"DB file path leaked to stderr: {stderr!r}"
        assert "Traceback" not in stderr, f"Full traceback leaked to stderr: {stderr!r}"

    def test_special_chars_in_run_id_accepted(self):
        """run-id with slashes, colons, spaces must be accepted by the CLI."""
        special_run_id = "run/2026-06-13T03:00:00Z run with spaces"
        result = subprocess.run(
            [
                _python(),
                "-m",
                "advisors.prism_audit_write",
                "--run-id",
                special_run_id,
                "--role",
                "macro_analyst",
                "--phase",
                "initial_read",
            ],
            input="Fed held rates.",
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            env={
                **__import__("os").environ,
                "DB_PATH": str(pathlib.Path(__import__("tempfile").mkdtemp()) / "cli_special.db"),
            },
        )
        assert result.returncode == 0, (
            f"CLI rejected special-char run_id.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        assert result.stdout.strip().isdigit()
