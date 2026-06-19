"""
App test conftest.

Patches get_api_state_dict for every test in tests/app/ so that the additive
portmode-dev fields (port_state, exit_authority, daemon_started_at) are
JSON-serialisable. Without this, engine.exit_authority.get_exit_authority()
is invoked with no live DB/engine, returns a MagicMock, and Flask's jsonify
raises a 500 on serialisation.

Also stubs load_community_strategies for every test in tests/app/ so that the
HF-1 route wiring (app.py: lazy `from advisors.community_strats import
load_community_strategies`) never reaches real Atlas (MongoDB) during the
offline suite.  Without this stub every POST to /ai-advisor/strategy-builder/run
blocks on a live network connection and the suite hangs.  The stub returns the
honest-unavailable shape the route already degrades on (AC-4 / AC-6).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

_API_STATE_STUB = {
    "bot_state": {},
    "is_locked": False,
    "port_state": {},
    "exit_authority": {},
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
