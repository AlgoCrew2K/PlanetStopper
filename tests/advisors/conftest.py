"""Conftest for tests/advisors/.

Clears the DEV_ADVISOR_FIXTURE env var that is loaded from .env via
synthetic_history/alpha_bot_execution load_dotenv() at import time.
Without this, the /ai-advisor/suggest route hits the early-return fixture
path before reaching the real ai_advisor.assemble_advisor_context call,
making it impossible to test the real exception-surfacing path.
"""

import pytest


@pytest.fixture(autouse=True)
def _clear_dev_advisor_fixture(monkeypatch):
    """Remove DEV_ADVISOR_FIXTURE from os.environ for all tests in this dir.

    The .env file has DEV_ADVISOR_FIXTURE=1 (dev convenience); imported modules
    call load_dotenv() at module load, populating os.environ.  Route tests that
    exercise the real code path need it absent so the early-return bypass doesn't
    fire before reaching the patched call site.
    """
    monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)
