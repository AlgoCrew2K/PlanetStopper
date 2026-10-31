"""RED: after the token work every operator POST control still sends exactly the request it sent.

Each control of the census is clicked once with fixed inputs (``tests/ui/_post_controls``) over the
real scripts and templates and the one request it sends is compared with the record the same
drivers wrote over the plan's base files (``tests/fixtures/refresh_timing/04-c9/
post_requests_at_base.json``, ``capture_post_requests.py``, ``PROVENANCE.md``): URL, method, every
header except the token's value, body, ``keepalive`` and the keys of the init object. The money controls
(Emergency Liquidate, Cash Now, the settings saves, Go live) are in the census; their requests may not
change by a byte.

The page harness additionally fails any driver that sends a POST without a click or twice for one click.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.ui import _post_controls as controls
from tests.ui._shell_support import (  # noqa: F401
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
)

BASE = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "refresh_timing"
        / "04-c9"
        / "post_requests_at_base.json"
    ).read_text(encoding="utf-8")
)

MONEY_CONTROLS = [
    "emergency_liquidate",
    "cash_now",
    "settings_save",
    "modal_save",
    "modal_save_go_live",
]


def test_the_census_is_the_set_the_base_record_holds():
    assert set(controls.NAMES) == set(BASE["controls"]), (
        "a control was added or removed from the census"
    )


def test_the_money_controls_are_in_the_census():
    assert set(MONEY_CONTROLS) <= set(controls.NAMES)


@pytest.mark.parametrize("name", controls.NAMES)
def test_the_request_equals_the_base_request(name):
    now = controls.drive(name)
    base = BASE["controls"][name]
    assert now == base, f"{name}: the request changed.\nbase: {base}\nnow:  {now}"
