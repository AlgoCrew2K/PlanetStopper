"""Apply suggestion and Dismiss on a symphony with an awkward name, in a real browser (marker ``browser``).

Run with ``python -m pytest -m browser -n 0 tests/ui/test_suggestion_buttons_browser.py``. Firefox
headless through Playwright; the app in this process serves the real ``GET /ai-advisor`` page
(tests/ui/_advisor_browser_support.py: outside hosts refused, the developer ``.env`` never read).

Nothing is written and nothing reaches a model: ``/ai-advisor/suggest`` is answered here with one
suggestion in the shape the structured-output schema asks for (tests/_suggest_and_chat_routes.py),
and ``/ai-advisor/accept`` and ``/ai-advisor/reject`` are answered here and their requests recorded,
so a press never reaches the app's routes.

Contract pinned (the node-harness twin is tests/ui/test_suggestion_buttons_with_awkward_names.py)
* Pressing Apply sends exactly one accept request whose ``symphony_id`` is the picked name, character
  for character (two captured Composer names hold an apostrophe), and Dismiss one reject request.
* A name built to close an inline handler string runs nothing.
"""

from __future__ import annotations

import json

import pytest

from tests._suggest_and_chat_routes import SUGGESTION_ITEM
from tests.ui._advisor_browser_support import (  # noqa: F401
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_probe,
    advisor_server,
    fail,
    firefox,
    open_advisor,
)
from tests.ui.test_suggestion_buttons_with_awkward_names import (
    CONSTRUCTED_NAMES,
    NAMES_WITH_AN_APOSTROPHE,
)

pytestmark = pytest.mark.browser

NAMES = NAMES_WITH_AN_APOSTROPHE + [CONSTRUCTED_NAMES["closes the string and runs code"]]
ENDPOINTS = {"Apply suggestion": "accept", "Dismiss": "reject"}


def _run_advisor_on(probe, name: str):
    """Pick ``name`` and run the advisor against a stubbed suggest route; the recorded presses."""
    sent = {"accept": [], "reject": []}

    def answer(kind, body):
        def handle(route):
            sent[kind].append(json.loads(route.request.post_data or "{}"))
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

        return handle

    probe.page.route(
        "**/ai-advisor/suggest",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"suggestions": [SUGGESTION_ITEM]}),
        ),
    )
    probe.page.route("**/ai-advisor/accept", answer("accept", {"status": "accepted"}))
    probe.page.route("**/ai-advisor/reject", answer("reject", {"status": "rejected"}))
    open_advisor(probe)
    # One page task picks the symphony and presses Run, so a later refill of the list cannot unpick it.
    probe.page.evaluate(
        """(name) => {
            const select = document.getElementById('symphony-id-input');
            const option = document.createElement('option');
            option.value = name;
            option.textContent = name;
            select.appendChild(option);
            select.value = name;
            select.dispatchEvent(new Event('change', { bubbles: true }));
            document.getElementById('get-suggestions-btn').click();
        }""",
        name,
    )
    return sent


@pytest.mark.parametrize("label", sorted(ENDPOINTS))
@pytest.mark.parametrize("name", NAMES)
def test_the_press_sends_one_request_naming_the_picked_symphony(advisor_probe, name, label):
    sent = _run_advisor_on(advisor_probe, name)
    button = advisor_probe.page.get_by_role("button", name=label)
    button.first.wait_for(state="visible")

    button.first.click()

    kind = ENDPOINTS[label]
    for _ in range(50):
        if sent[kind]:
            break
        advisor_probe.page.wait_for_timeout(100)
    advisor_probe.page.wait_for_timeout(300)  # a second request would have landed by now
    if len(sent[kind]) != 1:
        fail(advisor_probe, f"pressing {label} sent {len(sent[kind])} {kind} requests")
    assert sent[kind][0]["symphony_id"] == name
    assert sent[kind][0]["suggestion"] == SUGGESTION_ITEM
    assert advisor_probe.page.evaluate("window.__ran === undefined"), "a name ran as script"
