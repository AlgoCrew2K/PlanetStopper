"""
Dashboard test conftest.

Patches get_api_state_dict for every dashboard test so that the additive
portmode-dev fields (port_state, exit_authority, daemon_started_at) are
JSON-serialisable. Without this, engine.exit_authority.get_exit_authority()
is invoked with no live DB/engine, returns a MagicMock, and Flask's jsonify
raises a 500 on serialisation.
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


@pytest.fixture(autouse=True)
def _stub_get_api_state_dict():
    import app as app_module

    with patch.object(app_module, "get_api_state_dict", return_value=_API_STATE_STUB):
        yield
