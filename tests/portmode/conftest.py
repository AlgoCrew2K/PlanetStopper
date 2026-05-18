"""
Portmode test suite conftest.

Root cause of ValueError: I/O operation on closed file during test runs:

1. alpha_bot_execution.py line 7 replaces sys.stdout at module level with a new
   TextIOWrapper wrapping sys.stdout.buffer. When symphony_logic.py imports from
   alpha_bot_execution (triggered by app.py -> ai_advisor.py -> symphony_logic.py),
   the replacement runs unconditionally, stealing the underlying buffer from pytest's
   FDCapture.tmpfile. When FDCapture.syscapture.suspend() later restores the old
   sys.stdout, the stolen new TextIOWrapper gets GC'd, closing the shared underlying
   _TemporaryFileWrapper. FDCapture.tmpfile.seek(0) then raises ValueError.

   Fix: alpha_bot_execution.py:7 is now guarded by `if __name__ == "__main__":`.

2. database.py:663 calls logging.error() during run_migrations(), which triggers
   logging.basicConfig() if the root logger has no handlers. basicConfig() adds a
   StreamHandler(sys.stderr) pointing to pytest's captured stderr (a NamedTemporaryFile).
   The NullHandler below prevents basicConfig() from installing StreamHandler by
   ensuring the root logger always has at least one handler.
"""

import logging


def pytest_configure(config):
    """Block logging.basicConfig() from adding StreamHandler(stderr) during tests."""
    logging.getLogger().addHandler(logging.NullHandler())
