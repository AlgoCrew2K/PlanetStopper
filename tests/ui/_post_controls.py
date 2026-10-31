"""Drive every operator POST control once and return the request it sends.

TEST-ONLY. One driver per control in the census (the dashboard's Cash Now, fleet Dismiss and
Managed Sleeves buttons in the whole-file harness of ``tests/dashboard/_index_js_harness``; every
other control in ``tests/ui/_page_js_harness`` over the page that carries it). Each driver clicks the
control with fixed inputs, answers the POST with a plain success, and returns the one request the
page sent as ``{url, method, headers, body, keepalive, initKeys}`` with the CSRF token's value
replaced by ``<token>``. ``tests/fixtures/refresh_timing/04-c9/capture_post_requests.py`` runs the
drivers over the plan's base files and writes ``post_requests_at_base.json``;
``tests/ui/test_post_controls_request_bytes.py`` runs them over the files under test and compares.

Nothing here sends a request to a running server: every answer is a route spec.
"""

from __future__ import annotations

from pathlib import Path

from tests.dashboard._index_js_harness import get_world, run_scenario
from tests.ui._page_js_harness import DEFAULT_TOKEN, page_scripts, render_page, route, run_page
from tests.ui._refusal_answers import accounts
from tests.ui.test_advisor_controls_refusal_client import CASES as ADVISOR_CASES
from tests.ui.test_advisor_controls_refusal_client import TestChat

PLACEHOLDER = "<token>"
STATIC = Path(__file__).resolve().parents[2] / "static"
ACCOUNT = "7c2607c5-4583-4c1b-ae55-e5a05bb49f0d"
OK = {"status": 200, "json": {"status": "ok", "message": "ok"}}
INDEX_SCRIPTS = tuple(
    f"static/{name}"
    for name in ("request_guard.js", "chrome.js", "index.js")
    if (STATIC / name).exists()
)

# name -> (page, scenario after boot, POST url pattern, extra routes)
PAGE_CONTROLS: dict[str, dict] = {
    "force_run": {
        "page": "/history",
        "scenario": "await H.click('[data-testid=\"force-run-btn\"]');",
        "url": r"/api/trigger$",
    },
    "emergency_liquidate": {
        "page": "/history",
        "scenario": "await H.act('open', async () => { window.openPanicModal(); });"
        " await H.set('#panic-phrase', 'LIQUIDATE'); await H.click('#panic-confirm-btn');",
        "url": r"/api/sell_account$",
        "extra": lambda: [route(r"/api/accounts$", accounts([ACCOUNT]), method="GET")],
    },
    "mark_viewed": {
        "page": "/history",
        "scenario": "await H.click('#candidate-alert-indicator');",
        "url": r"/api/candidate-alert/mark-viewed$",
    },
    "settings_save": {
        "page": "/settings",
        "scenario": "await H.set('#execution-start-time', '09:35'); await H.click('#btn-save');",
        "url": r"/api/settings$",
        "extra": lambda: _settings_routes(),
    },
    "flush_confirm": {
        "page": "/settings",
        "scenario": "await H.click('#btn-flush-trigger'); await H.click('#btn-flush-confirm');",
        "url": r"/api/settings/flush-resync$",
        "extra": lambda: (
            _settings_routes()
            + [
                route(r"/api/state$", {"json": {"bot_state": {}}}, method="GET"),
                route(r"/api/armed-symphonies$", {"json": {"armed": []}}, method="GET"),
            ]
        ),
    },
    "chat_send": {
        "page": "/ai-advisor",
        "scenario": TestChat.SETUP + TestChat.SEND,
        "url": r"/ai-advisor/chat/send$",
    },
}
for _name, _case in ADVISOR_CASES.items():
    PAGE_CONTROLS[f"advisor_{_name}"] = {
        "page": "/ai-advisor",
        "scenario": _case["setup"] + "\n" + _case["invoke"],
        "url": _case["url"],
    }

MODAL_CONTROLS = {
    "modal_save": "await H.click('#sym-settings-save');",
    "modal_save_go_live": "await H.click('#sym-settings-live-toggle'); await H.click('#sym-settings-save');"
    " await H.click('#sym-confirm-go-live');",
}

INDEX_CONTROLS = {
    "cash_now": """
        H.route('/api/sell_account', {status: 200, body: {status: 'success'}});
        const btn = H.sandbox.document.querySelectorAll('[data-testid="cash-now-btn"]').find((b) => !b.hasAttribute('disabled'));
        const card = btn.closest('[data-testid="sym-card"]');
        H.sandbox.cashNow({stopPropagation() {}, currentTarget: btn}, card.getAttribute('data-sym-id') || 'sym-from-card');
        await H.settle();
        return H.fetchLog.filter((e) => e.method === 'POST');
    """,
    "fleet_dismiss": """
        H.route('/api/fleet-alert/dismiss', {status: 200, body: {status: 'ok'}});
        H.sandbox._lastFleetAlert = {tripped_at_et: '2026-10-22T10:00:00-04:00'};
        const b = H.sandbox.document.createElement('button'); H.sandbox.document.body.appendChild(b);
        H.sandbox.dismissFleetAlert(b);
        await H.settle();
        return H.fetchLog.filter((e) => e.method === 'POST');
    """,
    "sleeve_arm": "ARM",
    "sleeve_disarm": "DISARM",
    "sleeve_delete": "DELETE",
}
_SLEEVE_CALLS = {
    "ARM": "H.sandbox.armSleeveRuleToPaper('s1', 'r1', b)",
    "DISARM": "H.sandbox.disarmSleeve('s1', b)",
    "DELETE": "H.sandbox.deleteSleeve('s1', b)",
}

NAMES = sorted([*PAGE_CONTROLS, *MODAL_CONTROLS, *INDEX_CONTROLS])


def _settings_routes() -> list[dict]:
    from tests.ui import _settings_page_answers as page

    page.seed_state()
    return [
        route(r"/api/settings$", page.settings_get_answer(), method="GET"),
        route(r"/api/settings$", page.settings_save_answer(), method="POST"),
        route(
            r"/api/settings/flush-resync$", {"json": {"status": "ok", "errors": []}}, method="POST"
        ),
    ]


def _normalise(request: dict, token: str) -> dict:
    headers = {k: (PLACEHOLDER if v == token else v) for k, v in request["headers"].items()}
    return {
        "url": request["url"],
        "method": request["method"],
        "headers": dict(sorted((k.lower(), v) for k, v in headers.items())),
        "body": request["body"],
        "keepalive": request.get("keepalive", False),
        "initKeys": request.get("initKeys"),
    }


def _drive_page(name: str) -> dict:
    control = PAGE_CONTROLS[name]
    html = render_page(control["page"])
    routes = list(control.get("extra", lambda: [])())
    routes.append(route(control["url"], OK, method="POST"))
    out = run_page(
        html,
        "await H.boot();\n" + control["scenario"] + "\nreturn 1;",
        routes=routes,
        pathname=control["page"],
    )
    (post,) = out.posts(control["url"].removesuffix("$"))
    return _normalise(post, DEFAULT_TOKEN)


def _drive_modal(name: str) -> dict:
    import app as app_module
    import database

    database.save_state({"symphony_b": {"name": "Granite Bay Momentum"}})
    with app_module.app.test_client() as client:
        body = client.get("/api/symphony-settings/symphony_b").get_json()
    html = render_page("/history")
    scripts = [s for s in page_scripts(html) if s != "history.js"] + ["settings-modal.js"]
    out = run_page(
        html,
        "await H.boot();\n"
        "await H.act('open', async () => { window.openSymphonySettings(null, 'symphony_b'); });\n"
        + MODAL_CONTROLS[name]
        + "\nreturn 1;",
        routes=[
            route(r"/api/symphony-settings/symphony_b$", {"json": body}, method="GET"),
            route(r"/api/symphony-settings/", OK, method="POST"),
        ],
        scripts=scripts,
        pathname="/history",
    )
    (post,) = out.posts("/api/symphony-settings")
    return _normalise(post, DEFAULT_TOKEN)


def _drive_index(name: str) -> dict:
    scenario = INDEX_CONTROLS[name]
    if scenario in _SLEEVE_CALLS:
        scenario = (
            "H.route(/\\/api\\/sleeves\\//, {status: 200, body: {status: 'ok'}});\n"
            "const b = H.sandbox.document.createElement('button'); H.sandbox.document.body.appendChild(b);\n"
            f"{_SLEEVE_CALLS[scenario]};\nawait H.settle();\n"
            "return H.fetchLog.filter((e) => e.method === 'POST');"
        )
    out = run_scenario(
        get_world("cards-open"),
        "await H.boot({extras: true});\n" + scenario,
        scripts=INDEX_SCRIPTS,
    )
    posts = out["result"]
    assert len(posts) == 1, f"{name} sent {len(posts)} POSTs for one click"
    post = posts[0]
    return _normalise(
        {
            "url": post["url"],
            "method": post["method"],
            "headers": post.get("headers") or {},
            "body": post.get("body"),
            "keepalive": False,
            "initKeys": None,
        },
        "tok",
    )


def drive(name: str) -> dict:
    """The one request control ``name`` sends for a click with the fixed inputs."""
    if name in PAGE_CONTROLS:
        return _drive_page(name)
    if name in MODAL_CONTROLS:
        return _drive_modal(name)
    return _drive_index(name)


def drive_all() -> dict[str, dict]:
    return {name: drive(name) for name in NAMES}
