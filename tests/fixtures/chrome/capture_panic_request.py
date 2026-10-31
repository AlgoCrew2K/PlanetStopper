"""Record the request Emergency Liquidate sends, from the shipped ``static/chrome.js``.

Run on an UNMODIFIED ``static/chrome.js`` and ``templates/_chrome.html`` (the script refuses
otherwise) to write ``panic_request_at_base.json`` beside this file:

    DB_PATH=<scratch>/state.db ATLAS_CACHE_DB_PATH=<scratch>/atlas.db \
        python tests/fixtures/chrome/capture_panic_request.py

The producer is the real ``static/chrome.js`` run by ``tests/app/_chrome_node_harness.py`` against
the answers the real Flask app gives (``tests/app/_chrome_route_answers.py``): open the modal,
choose the account, type the phrase, click Confirm once; and, separately, click Confirm twice.
What is written is the request the page sent (URL, method, headers, body string, byte for byte),
the number of requests two Confirm clicks sent, the Confirm gate the markup carries on the phrase
input, and the inputs used (the token and account id), so a later run replays the same inputs.

No network is used and no ``.env`` is read. Provenance: ``PROVENANCE.md`` in this directory.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import uuid

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def main() -> None:
    watched = ["static/chrome.js", "templates/_chrome.html"]
    dirty = _git("status", "--porcelain", "--", *watched)
    if dirty:
        raise SystemExit(f"refusing to capture: the base files are modified:\n{dirty}")

    scratch = pathlib.Path(tempfile.mkdtemp(prefix="panic_request_base_"))
    os.environ.setdefault("DB_PATH", str(scratch / "state.db"))
    os.environ.setdefault("ATLAS_CACHE_DB_PATH", str(scratch / "atlas.db"))

    import database

    database.init_db()
    database.run_migrations()

    import app as app_module
    from tests.app import _chrome_node_harness as harness
    from tests.app import _chrome_route_answers as answers

    token = app_module._CSRF_TOKEN
    account_id = str(uuid.uuid4())
    token_answer = answers.csrf_token_answer()
    assert json.loads(token_answer["body"])["csrf_token"] == token
    accounts = answers.accounts_answer([account_id])
    accepted = answers.sell_answer(answers.sell_payload(account_id))
    gate = answers.chrome_oninput_gate()

    responses = {
        "/api/csrf-token": [token_answer],
        "/api/accounts": [accounts],
        "/api/sell_account": [accepted],
    }
    setup = [
        {"op": "dom_ready"},
        {"op": "open_panic"},
        {"op": "type_phrase", "value": "LIQUIDATE"},
    ]
    one = harness.run(
        setup + [{"op": "confirm"}, {"op": "flush"}], responses=responses, oninput=gate
    )
    two = harness.run(
        setup + [{"op": "confirm"}, {"op": "confirm"}, {"op": "flush"}],
        responses=responses,
        oninput=gate,
    )
    single = harness.calls_to(one, "/api/sell_account")
    if len(single) != 1:
        raise SystemExit(f"expected one /api/sell_account request, saw {len(single)}")
    request = single[0]["init"]
    if request is None or request.get("method") != "POST":
        raise SystemExit(f"the page sent no POST: {request!r}")

    record = {
        "base_sha": _git("rev-parse", "HEAD"),
        "captured_at_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": {"csrf_token": token, "account_id": account_id, "phrase": "LIQUIDATE"},
        "single_confirm_request": {
            "url": single[0]["url"],
            "method": request["method"],
            "headers": request["headers"],
            "body": request["body"],
        },
        "two_confirm_clicks_request_count": len(harness.calls_to(two, "/api/sell_account")),
        "confirm_gate_oninput": gate,
    }
    out = HERE / "panic_request_at_base.json"
    out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(
        f"wrote {out.name} at {record['base_sha']}: "
        f"{record['single_confirm_request']['method']} {record['single_confirm_request']['url']}, "
        f"{record['two_confirm_clicks_request_count']} requests for two Confirm clicks"
    )


if __name__ == "__main__":
    main()
