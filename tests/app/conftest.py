"""
App test conftest.

Patches get_api_state_dict for every test in tests/app/ so that the additive
portmode-dev fields (port_state, exit_authority, daemon_started_at) are
JSON-serialisable. Historically (predates this cycle) get_api_state_dict
reached engine.exit_authority.get_exit_authority(), which returned a
MagicMock with no live DB/engine, causing Flask's jsonify to raise a 500 on
serialisation -- app.py has SINCE been refactored to compute exit_authority
via a plain `os.getenv("EXIT_AUTHORITY", "per_symphony")` (app.py:2229, no
engine dependency), but this stub is still needed so /api/state-touching
tests in tests/app/ don't reach real DB/engine state via the OTHER fields
this stub replaces.

Also stubs load_community_strategies for every test in tests/app/ so that the
HF-1 route wiring (app.py: lazy `from advisors.community_strats import
load_community_strategies`) never reaches real Atlas (MongoDB) during the
offline suite.  Without this stub every POST to /ai-advisor/strategy-builder/run
blocks on a live network connection and the suite hangs.  The stub returns the
honest-unavailable shape the route already degrades on (AC-4 / AC-6).

[Fixed, DE-PERF-WINDOW-TRUTH-001, 2026-09-04, team-lead root-cause via CI
diagnostic]: `exit_authority` below was `{}` -- a TYPE mismatch (dict where
the real get_api_state_dict always returns a str, "per_symphony" or
"port_level"). This stub is pre-existing/autouse for ALL of tests/app/, and
was never itself reachable outside that directory under normal pytest
conftest directory-scoping -- but a same-CI-worker sys.modules identity-leak
(a tests/app/ file's autouse patch.object + test_dismiss_executor_atexit.py's
sys.modules["app"] pop+reload, co-located with a tests/portmode/ victim
under `-n2 --dist loadfile`'s full-tree grouping) let this MagicMock-wrapped
stub reach a /api/state consumer OUTSIDE tests/app/, which then rendered the
type-mismatched `{}` instead of a valid exit_authority string. Fixed
robust-by-construction: `exit_authority` is now a REALISTIC value
("per_symphony", matching the real function's own default at app.py:2229)
so that even if this stub ever leaks again via the same or a similar
identity-leak mechanism, ANY consumer sees a genuinely valid value instead
of a type-mismatched placeholder -- the leak-TIMING dependency is removed as
a failure mode, not just this one occurrence.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

_API_STATE_STUB = {
    "bot_state": {},
    "is_locked": False,
    "port_state": {},
    "exit_authority": "per_symphony",
    "daemon_started_at": None,
}

# Offline community-strats stub: mimics the "Atlas unavailable" branch so the
# route degrades to a template-only run without touching the network.
_COMMUNITY_STRATS_OFFLINE_STUB = {
    "available": False,
    "candidates": [],
    "stats": {},
    "source": "test-stub",
}


@pytest.fixture(autouse=True)
def _stub_get_api_state_dict():
    import app as app_module

    with patch.object(app_module, "get_api_state_dict", return_value=_API_STATE_STUB):
        yield


@pytest.fixture(autouse=True)
def _stub_community_strats_offline():
    """Prevent load_community_strategies from reaching real Atlas in any tests/app/ test.

    Patched at the source module because the route handler uses a lazy
    `from advisors.community_strats import load_community_strategies` inside
    its function body — that re-fetches the name from the source module
    namespace on each call, so patching there is the correct seam.
    """
    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value=_COMMUNITY_STRATS_OFFLINE_STUB,
    ) as mock_load:
        yield mock_load


@pytest.fixture()
def client():
    """A Flask test client for tests that do not need a special one (a test file that defines
    its own ``client`` keeps using that)."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_review_run_state(monkeypatch):
    """Give every tests/app/ test a fresh, idle review run state with no remembered failed
    attempt.

    The state is one module-level object shared by the nightly tick, the on-demand route
    and the panel, so a test that takes it (or records a failed attempt) would otherwise
    change what the next test's page and API report. A run that is still holding the old
    object finishes against it and cannot touch the new one. Production code must reach the
    state as ``tenure_review.REVIEW_RUN_STATE`` at each use (never a name bound at import),
    which is what makes this replacement take effect everywhere.
    """
    import advisors.tenure_review as tenure_review

    monkeypatch.setattr(tenure_review, "REVIEW_RUN_STATE", type(tenure_review.REVIEW_RUN_STATE)())
    yield
