"""Agent-callable CLI writer for prism_audit_log.

Usage
-----
    python -m advisors.prism_audit_write \\
        --run-id  <run_id> \\
        --role    <agent_role> \\
        --phase   <phase>

Content is read from STDIN (not a positional argument) to avoid shell
argument-length limits on long/multiline analyst output.

On success, prints the new row id (integer) to STDOUT and exits 0.

Error handling (D-1 contract)
------------------------------
All errors write only ``type(exc).__name__`` to STDERR — never the full
exception message or a Python traceback.  The process exits with a non-zero
status code so callers can detect failure reliably.
"""

from __future__ import annotations

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))  # populate DB_PATH (and other env vars) from .env before _db_file() resolves

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prism_audit_write",
        description="Write one prism_audit_log entry; content read from STDIN.",
        # Suppress the default error traceback from argparse — we handle it
        # in _main() via the try/except around parse_known_args.
        add_help=True,
    )
    parser.add_argument("--run-id", required=True, help="Nightly run identifier.")
    parser.add_argument("--role", required=True, help="Agent role (e.g. 'synthesizer').")
    parser.add_argument("--phase", required=True, help="Deliberation phase (e.g. 'synthesis').")
    return parser


def _main(argv: list[str] | None = None) -> int:
    """Entry point — returns exit code.  Never raises uncaught exceptions."""
    parser = _build_parser()

    # parse_args() calls sys.exit(2) on missing required args, which bubbles
    # through argparse's internal error() method.  We catch SystemExit so we
    # can emit a D-1-compliant message before exiting.
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        # argparse already printed its usage/error to stderr; just exit non-zero.
        # We do NOT re-raise or print a traceback (D-1).
        return 2
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}\n")
        return 2

    # Read content from STDIN (never from an argument — content can be very long).
    try:
        content = sys.stdin.read()
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}\n")
        return 1

    # Insert into the state DB via the canonical accessor.
    try:
        import database  # lazy import — not on the hot path; DB_PATH resolved by load_dotenv above

        row_id = database.insert_prism_audit_entry(
            run_id=args.run_id,
            agent_role=args.role,
            phase=args.phase,
            content=content,
        )
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}\n")
        return 1

    # Print the new row id to STDOUT so callers can reference it.
    sys.stdout.write(f"{row_id}\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
