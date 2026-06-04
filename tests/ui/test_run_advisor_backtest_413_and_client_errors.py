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
        re.search(r"\.then\(\s*function\s*\(\s*resp\s*\)\s*\{\s*return\s+resp\.json\(\)\s*;?\s*\}\s*\)", src)
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
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"node --check failed on {js_path.name}:\n{result.stderr}"
    )


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
    err = (getattr(result, "error", "") or "")
    assert "413" in err, (
        f"The 413 must be reflected in the error string; got {err!r}. The operator "
        "needs to know the backtest was rejected as too large."
    )
