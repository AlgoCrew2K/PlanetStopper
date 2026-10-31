"""Real answers for the refusal tests: the CSRF hook's 403 page and the rotated token.

TEST-ONLY. The 403 is the page Werkzeug gives a POST the CSRF hook refuses, produced by the real
app with the check re-enabled (``tests/conftest.py`` turns it off for every test). The rotated token
is what ``GET /api/csrf-token`` answers after ``app._CSRF_TOKEN`` is replaced, as it is by a daemon
restart. The route specs are for ``tests/ui/_page_js_harness``.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import app as app_module
from tests.app import _chrome_route_answers as real

TOKEN_B = "token-B-1111111111111111111111111111111111111111"


def csrf_refused() -> dict:
    """The 403 HTML page the CSRF hook gives a POST that carries a stale token."""
    answer = real.trigger_answer_csrf_refused()
    assert answer["status"] == 403, f"the hook answered {answer['status']}, not 403"
    assert "text/html" in answer["content_type"], "the 403 must be the hook's HTML page"
    return {"status": 403, "text": answer["body"]}


def token_answer(token: str = TOKEN_B) -> dict:
    """What ``GET /api/csrf-token`` answers once the daemon holds ``token``."""
    with patch.object(app_module, "_CSRF_TOKEN", token):
        answer = real.csrf_token_answer()
    assert json.loads(answer["body"])["csrf_token"] == token
    return {"json": json.loads(answer["body"])}


def accepted_sell(account_id: str) -> dict:
    """The dry-run 200 the sell route gives a valid Emergency Liquidate request."""
    answer = real.sell_answer(real.sell_payload(account_id))
    assert answer["status"] == 200
    return {"status": 200, "json": json.loads(answer["body"])}


def accounts(account_ids: list[str]) -> dict:
    answer = real.accounts_answer(account_ids)
    return {"status": answer["status"], "json": json.loads(answer["body"])}


def spec_of(answer: dict) -> dict:
    """A ``tests/app/_chrome_route_answers`` answer as a harness route spec."""
    if "json" in answer["content_type"]:
        return {"status": answer["status"], "json": json.loads(answer["body"])}
    return {"status": answer["status"], "text": answer["body"]}
