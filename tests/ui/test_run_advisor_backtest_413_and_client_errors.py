"""advisor-fix cycle — RED: AC-9 the Run Advisor backtest step fails the live gate.

LIVE FAILURES (team-lead, 2026-06-04, on the deployed cdfa088 /ai-advisor page):
  * "(INVEST) Planet of Hunted Cascades": "backtest failed: HTTP 413 Request Entity
    Too Large" (nginx) — the modified symphony tree POST to Composer's backtest
    endpoint is oversized.
  * "(INVEST) LQD + EYEG 5 ways": client "Request failed: SyntaxError: JSON.parse:
    unexpected character at line 1 column 1" — the client called resp.json() on a
    NON-JSON (HTML 413) response and crashed.

So AC-8 fetch works + candidate-gen works, but the BACKTEST POST is oversized -> 413
-> no gate completion -> no proposal.  This is the mocked-tests-passed-but-live-failed
pattern AGAIN (the AC-8 tests mocked the Composer backtest, so the 413 never ran).

Two contracts this file pins:
  AC-9a (CLIENT): the logic-changes + asset-swaps evaluate fetch handlers must NOT
    call resp.json() unconditionally — they must guard on the response (resp.ok /
    status / content-type) so a non-JSON error (413 HTML) renders a graceful message
    instead of throwing SyntaxError: JSON.parse.  Source-scan + node --check.
  AC-9b (BACKTEST CLIENT): run_backtest must convert a 413 into a graceful
    BacktestResult(stats=None, error="HTTP 413 ...") — never raise, never crash.
    (This already holds at composer_backtest_client.py:344-346; pinned as a
    regression guard so a refactor can't reintroduce a crash on the oversized path.)

NOTE: whether the 413 is REDUCIBLE (trim/compact/gzip the payload so the backtest
COMPLETES) vs a HARD Composer/nginx limit is composer-alpaca-integration's diagnosis;
the payload-reduction RED is held until they report.  These two contracts are
unconditional regardless of that outcome (the client must not crash; the client
must surface a clean error).

No live network: requests.post is patched; the JS contracts are source/syntax checks.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_STATIC = _REPO_ROOT / "static"

_EVALUATE_JS = [
    _STATIC / "ai_advisor_logic_changes.js",
    _STATIC / "ai_advisor_asset_swaps.js",
]


# ===========================================================================
# AC-9a — client must not call resp.json() unconditionally (non-JSON guard).
# ===========================================================================


@pytest.mark.parametrize("js_path", _EVALUATE_JS, ids=lambda p: p.name)
def test_evaluate_fetch_handler_guards_before_json_parse(js_path):
    """The evaluate fetch handler must guard the response before parsing JSON.

    The live crash: `.then(function (resp) { return resp.json(); })` on a non-JSON
    413 HTML response throws "SyntaxError: JSON.parse: unexpected character".  The
    handler must instead check resp.ok / resp.status / content-type (or parse text
    defensively) BEFORE resp.json(), so a non-JSON error renders a clean message.

    Heuristic that fails on the buggy pattern: the file contains a fetch to the
    evaluate endpoint AND an unguarded `.then(function (resp) { return resp.json(); })`
    with no nearby resp.ok / resp.status / content-type / catch-of-parse guard.
    """
    assert js_path.is_file(), f"{js_path} not found"
    src = js_path.read_text(encoding="utf-8")

    assert "/evaluate" in src, (
        f"{js_path.name} does not POST to an /evaluate endpoint — fixture drift."
    )

    # The buggy pattern: an unconditional resp.json() immediately after fetch, with
    # no response-status/ok/content-type guard anywhere in the file.
    has_unconditional_json = bool(
        re.search(
            r"\.then\(\s*function\s*\(\s*resp\s*\)\s*\{\s*return\s+resp\.json\(\)\s*;?\s*\}\s*\)",
            src,
        )
    )
    has_response_guard = bool(
        re.search(r"resp\.ok", src)
        or re.search(r"resp\.status", src)
        or re.search(r"content-type", src, re.IGNORECASE)
        or re.search(r"resp\.text\(\)", src)  # defensive: read text then try/parse
    )

    assert has_response_guard and not has_unconditional_json, (
        f"{js_path.name}: the evaluate fetch handler calls resp.json() without a "
        "response guard (resp.ok / resp.status / content-type / resp.text fallback). "
        "AC-9a: a non-JSON 413 response must render a clean error, not throw "
        "SyntaxError: JSON.parse. Guard the response before parsing."
    )


@pytest.mark.parametrize("js_path", _EVALUATE_JS, ids=lambda p: p.name)
def test_evaluate_js_is_syntactically_valid(js_path):
    """node --check must pass on the evaluate JS (no parse error from the fix).

    Guards the hand-written client JS against a syntax break introduced by the
    AC-9a change (a parse error makes the whole modal dead — caught by node --check,
    not by any server/template test). Skips only if node is unavailable.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — cannot run --check syntax gate")
    assert js_path.is_file(), f"{js_path} not found"
    result = subprocess.run(
        [node, "--check", str(js_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"node --check failed on {js_path.name}:\n{result.stderr}"


# ===========================================================================
# AC-9b — run_backtest converts a 413 into a graceful error result (no crash).
# ===========================================================================


def test_run_backtest_413_returns_error_result_not_raise():
    """A 413 from Composer's /backtest must return a BacktestResult(error=...),
    never raise — so a single oversized candidate cannot crash the batch.

    Regression guard for composer_backtest_client.py:344-346 (the non-retryable
    branch). Pinned so a refactor of the payload/retry logic can't reintroduce a
    crash on the oversized path that the live gate hit.
    """
    import advisors.composer_backtest_client as bc

    resp = MagicMock()
    resp.status_code = 413
    resp.text = "<html><body><h1>413 Request Entity Too Large</h1></body></html>"

    with patch("advisors.composer_backtest_client.requests.post", return_value=resp):
        result = bc.run_backtest(
            symphony_id="hvPiGP1O7AHfutHE3Fjy",
            raw_value={"id": "x", "children": []},
            max_retries=0,
        )

    # Must be a graceful error result — stats absent, error set, no exception.
    assert getattr(result, "stats", "missing") is None, (
        "run_backtest on a 413 must return stats=None (graceful error), not raise "
        "or fabricate stats."
    )
    err = getattr(result, "error", "") or ""
    assert "413" in err, (
        f"The 413 must be reflected in the error string; got {err!r}. The operator "
        "needs to know the backtest was rejected as too large."
    )


# ===========================================================================
# AC-9c — the evaluate route translates a 413 backtest_error into a clean,
# human-readable "symphony too large" operator message (not raw nginx HTML).
#
# composer's diagnosis (live-measured): the 413 is a HARD limit — symphony trees
# > ~1 MB exceed nginx's body-size limit (Hunted Cascades = 1.1 MB / 7730 nodes),
# no client-side reduction works (gzip 400; no by-reference; encoded_value still
# needs raw_value).  So the fix is an HONEST operator message, not a bypass.
# ===========================================================================


_RAW_413 = (
    "HTTP 413: <html>\r\n<head><title>413 Request Entity Too Large</title></head>\r\n"
    "<body>\r\n<center><h1>413 Request Entity Too Large</h1></center>\r\n"
    "<hr><center>nginx/1.28.1</center>\r\n</body>\r\n</html>"
)


def _flask_client():
    with patch("database.init_db"):
        import app as flask_app
    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()


def test_logic_changes_evaluate_413_renders_clean_operator_message():
    """When the backtest fails with a 413 (oversized symphony), the route response
    must carry a CLEAN, human-readable "too large to backtest" message — NOT the
    raw nginx 413 HTML, and NOT a bare "HTTP 413".

    The proposal's backtest_error arrives as the raw 413 string from
    composer_backtest_client; the route must translate it so the operator sees why
    (symphony too large / exceeds Composer's inline-backtest limit), not nginx HTML.
    """
    import sys

    if "." not in sys.path:
        sys.path.insert(0, ".")
    # Build a run_result whose single proposal carries the raw 413 backtest_error.
    sys.path.insert(0, str(_REPO_ROOT))
    from tests.ui.test_logic_change_routes import _make_logic_change_run_result

    run_result = _make_logic_change_run_result(backtest_error=_RAW_413)

    client = _flask_client()
    with (
        patch("advisors.logic_change_engine._has_composer_key", return_value=True),
        patch(
            "symphony_logic.fetch_symphony_score",
            return_value={"id": "x", "name": "X", "children": []},
        ),
        patch("database.load_state", return_value={"x": {"name": "X", "account_uuid": "acc-1"}}),
        patch(
            "advisors.logic_change_engine.propose_operator_logic_change", return_value=run_result
        ),
    ):
        resp = client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={"symphony_id": "X", "change_description": "change window 20 to 16"},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body is not None
    # The full serialized response must NOT leak the raw nginx HTML to the operator.
    blob = str(body).lower()
    assert "<html>" not in blob and "nginx" not in blob, (
        "The raw nginx 413 HTML leaked into the evaluate response — the operator "
        f"would see a wall of HTML. Body: {body!r}. AC-9c: translate the 413 to a "
        "clean message."
    )
    # The translated message must explain WHY (too large / size limit), in plain words.
    assert re.search(r"too large|size limit|exceeds .* limit|too big", blob), (
        "The 413 was not translated to a human-readable 'symphony too large to "
        f"backtest' message. Body: {body!r}. AC-9c: the operator must understand "
        "the symphony exceeds Composer's inline-backtest size limit."
    )


def test_asset_swaps_evaluate_413_renders_clean_operator_message():
    """The SAME 413 translation must apply to the asset-swaps evaluate route — it
    surfaces backtest_error from the same _translate_backtest_error path (app.py).

    Locks the asset-swaps route as a regression guard so a future change can't
    translate only logic-changes and leak the raw 413 HTML on the swap route.
    """
    import sys

    if "." not in sys.path:
        sys.path.insert(0, ".")
    sys.path.insert(0, str(_REPO_ROOT))
    from tests.ui.test_asset_swap_routes import _make_swap_run_result

    run_result = _make_swap_run_result()
    # Set the raw 413 on the proposal the route serialises.
    run_result.proposals[0].backtest_error = _RAW_413

    client = _flask_client()
    with (
        patch("advisors.asset_swap_engine._has_composer_key", return_value=True),
        patch(
            "symphony_logic.fetch_symphony_score",
            return_value={"id": "x", "name": "X", "children": []},
        ),
        patch("database.load_state", return_value={"x": {"name": "X", "account_uuid": "acc-1"}}),
        patch("advisors.asset_swap_engine.propose_operator_swap", return_value=run_result),
    ):
        resp = client.post(
            "/ai-advisor/asset-swaps/evaluate",
            json={"symphony_id": "X", "from_ticker": "SPY", "to_ticker": "QQQ"},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body is not None
    blob = str(body).lower()
    assert "<html>" not in blob and "nginx" not in blob, (
        "The asset-swaps route leaked the raw nginx 413 HTML — the translation was "
        f"applied only to logic-changes. Body: {body!r}. AC-9c covers BOTH routes."
    )
    assert re.search(r"too large|size limit|exceeds .* limit|too big", blob), (
        "The asset-swaps route did not translate the 413 to a clean message. "
        f"Body: {body!r}. AC-9c: both evaluate routes translate the 413."
    )


# ===========================================================================
# AC-9d — COMPLETION: a backtest-able symphony's evaluate returns a REAL result
# (populated backtest stats), NOT a graceful-fail / "too large" / empty.
#
# team-lead's load-bearing check (verified live by composer): 10/11 of the user's
# symphonies are < 1 MB and backtest-able; LQD+EYEG + Corporate Chaos 5 complete
# end-to-end with REAL populated baseline/variant stats and an honest gate verdict
# (a gated rejection WITH numbers IS a real result).  This regression-guards that
# the COMPLETION path renders the real stats — so a future change that turns
# completion into a silent graceful-fail (the crash-guard masking a real failure)
# fails loudly.  "Crash fixed" != "Run Advisor completes."
# ===========================================================================


def test_logic_changes_evaluate_completion_surfaces_real_backtest_stats():
    """A backtest-able symphony that completes must surface its REAL baseline/variant
    backtest stats in the response — backtest_error must be falsy and the stats must
    be populated (not the too-large message, not all-None).

    Guards against the crash-fix MASKING a non-completion: a graceful-fail or a
    silently-empty result would fail this, distinguishing "completed with numbers"
    from "failed gracefully".
    """
    import sys

    if "." not in sys.path:
        sys.path.insert(0, ".")
    sys.path.insert(0, str(_REPO_ROOT))
    from tests.ui.test_logic_change_routes import _make_logic_change_run_result

    # A real completion: no backtest_error, and populated baseline/variant stats
    # (shape per composer's live evidence — sharpe/max_drawdown present).
    run_result = _make_logic_change_run_result(backtest_error=None)
    proposal = run_result.proposals[0]
    proposal.baseline_stats = {"sharpe_ratio": 2.227, "max_drawdown": 0.243}
    proposal.variant_stats = {"sharpe_ratio": 2.180, "max_drawdown": 0.251}

    client = _flask_client()
    with (
        patch("advisors.logic_change_engine._has_composer_key", return_value=True),
        patch(
            "symphony_logic.fetch_symphony_score",
            return_value={"id": "x", "name": "X", "children": []},
        ),
        patch("database.load_state", return_value={"x": {"name": "X", "account_uuid": "acc-1"}}),
        patch(
            "advisors.logic_change_engine.propose_operator_logic_change", return_value=run_result
        ),
    ):
        resp = client.post(
            "/ai-advisor/logic-changes/evaluate",
            json={"symphony_id": "X", "change_description": "change window 20 to 16"},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body is not None
    blob = str(body).lower()
    # A completed result must NOT show the too-large/backtest-unavailable message.
    assert "too large" not in blob and "backtest unavailable" not in blob, (
        f"A backtest-able symphony's completion rendered a failure message. Body: {body!r}."
    )
    # backtest_error must be falsy on a real completion.
    assert not body.get("backtest_error"), (
        f"backtest_error should be falsy on a real completion; got {body.get('backtest_error')!r}."
    )
    # The real stats must round-trip into the response — proving a populated result
    # rendered (not a silently-empty graceful-fail).
    detail = (body.get("survivors_detail") or []) + (body.get("rejected_detail") or [])
    assert detail, (
        "A completed evaluate must surface a proposal in survivors_detail or "
        f"rejected_detail; got neither. Body keys: {sorted(body.keys())}."
    )
    first = detail[0]
    assert first.get("baseline_stats") and first.get("variant_stats"), (
        "The completed proposal's baseline_stats / variant_stats are missing from "
        f"the response — the completion path did not surface the real backtest "
        f"numbers. proposal: {first!r}."
    )
    assert first["baseline_stats"].get("sharpe_ratio") is not None, (
        "baseline_stats.sharpe_ratio is None — the response carried an empty/"
        "graceful-fail result rather than a real completion with numbers."
    )
