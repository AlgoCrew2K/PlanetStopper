"""
RED tests — F-003: Panic-Stop Liquidation Confirmation.

``app.perform_account_liquidation`` (the ``POST /api/sell_account`` background
thread target) currently logs ``f"Liquidated {name} (HTTP {status})"``
UNCONDITIONALLY — a 429/4xx/5xx reject reads as a false success — and wraps
the ENTIRE per-symphony loop in one outer try/except, so one symphony's
failure silently abandons every remaining symphony. During a real emergency
the console/journal print is the operator's ONLY per-symphony ground truth.

This module pins the fix's contract:
  - AC-1: only ``status_code in (200, 201, 202)`` logs success; anything else
    logs an explicit ``"LIQUIDATION FAILED {name} - HTTP {code} - {text[:200]}"``
    line, never "Liquidated".
  - AC-2: each per-symphony attempt is isolated in its OWN try/except — a
    non-2xx status OR a raised exception (request-related or not) on one
    symphony must not abort the rest of the queue.
  - AC-3: the function returns a STRUCTURED per-symphony outcome (accepted
    shapes: ``{name: {"ok": bool, ...}}`` OR ``[{"name": ..., "ok": bool,
    ...}, ...]`` — the plan's ``name -> {ok, status/reason}`` notation does
    not pin one Python container, so both are accepted here).
  - AC-4: a mixed success/failure batch reports BOTH — no all-or-nothing.
  - AC-5 (HARD, no-behavior-change): the sell POST's URL/headers/json/timeout,
    the upstream GET, and the ``live_mode=False`` no-POST gate are all
    byte-unchanged. This cycle touches confirmation/logging/isolation ONLY.
  - AC-6: a golden all-success run logs every symphony exactly as before.

Mock boundary: ``app_module.requests.get`` / ``app_module.requests.post`` /
``app_module.time.sleep``. No live network, no live DB, no Flask test client
needed (the function is called directly, matching the existing direct-call
tests in ``tests/app/test_app_routes.py``). ``-n0`` only.

Blast-radius note: ``tests/app/test_app_routes.py`` has 4 existing tests that
touch this surface (``test_sell_account_live_mode_passes_true_to_liquidation``,
``test_perform_account_liquidation_skips_post_when_live_mode_false``,
``test_perform_account_liquidation_posts_when_live_mode_true``, plus the
route-level dry-run test). None assert on print content or a return value, so
they are expected to stay GREEN through this fix.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from unittest.mock import MagicMock

import pytest
import requests

import app as app_module

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_get_response(symphonies):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"symphonies": symphonies}
    return resp


def _make_sell_response(status_code, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def _run_liquidation(monkeypatch, symphonies, post_side_effect, *, live_mode=True):
    """
    Wire GET to return ``symphonies``, wire POST to ``post_side_effect``,
    no-op ``time.sleep``, call ``perform_account_liquidation`` capturing
    stdout, and return ``(result, captured_stdout, post_calls)`` where
    ``post_calls`` is a list of ``(url, kwargs)`` tuples in call order.
    """
    post_calls = []

    def fake_post(url, **kwargs):
        post_calls.append((url, kwargs))
        return post_side_effect(url, **kwargs)

    monkeypatch.setattr(
        app_module.requests, "get", lambda *_a, **_k: _make_get_response(symphonies)
    )
    monkeypatch.setattr(app_module.requests, "post", fake_post)
    monkeypatch.setattr(app_module.time, "sleep", lambda *_a, **_kw: None)

    buf = io.StringIO()
    with redirect_stdout(buf):
        result = app_module.perform_account_liquidation(
            "ACC1", "key", "secret", live_mode=live_mode
        )
    return result, buf.getvalue(), post_calls


def _outcome_for(result, name):
    """
    Look up the per-symphony outcome for ``name`` regardless of whether the
    implementer returned a dict-keyed-by-name or a list-of-dicts shape.
    Returns ``None`` if not found.
    """
    if result is None:
        return None
    if isinstance(result, dict):
        if name in result:
            return result[name]
        for value in result.values():
            if isinstance(value, dict) and value.get("name") == name:
                return value
        return None
    if isinstance(result, (list, tuple)):
        for entry in result:
            if isinstance(entry, dict) and entry.get("name") == name:
                return entry
    return None


# ---------------------------------------------------------------------------
# AC-1: only 200/201/202 logs success; everything else logs FAILED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [429, 400, 500])
def test_liquidation_logs_failed_line_not_liquidated_for_non_2xx(monkeypatch, status_code):
    """A 429/400/500 sell response must produce an explicit FAILED log line —
    never the unconditional 'Liquidated' success line."""
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}]

    def post_side_effect(url, **_kwargs):
        return _make_sell_response(status_code, text="rejected")

    _result, out, _calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert "LIQUIDATION FAILED" in out
    assert "Alpha" in out
    assert str(status_code) in out
    assert "Liquidated Alpha" not in out


def test_liquidation_failed_log_line_matches_ac1_format(monkeypatch):
    """Pins the AC-1 literal format:
    'LIQUIDATION FAILED {name} — HTTP {code} — {text[:200]}'."""
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}]

    def post_side_effect(url, **_kwargs):
        return _make_sell_response(429, text="Too Many Requests")

    _result, out, _calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert "LIQUIDATION FAILED Alpha — HTTP 429 — Too Many Requests" in out


@pytest.mark.parametrize("status_code", [200, 201, 202])
def test_liquidation_logs_success_line_for_each_2xx_success_code(monkeypatch, status_code):
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}]

    def post_side_effect(url, **_kwargs):
        return _make_sell_response(status_code, text="ok")

    _result, out, _calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert f"Liquidated Alpha (HTTP {status_code})" in out
    assert "LIQUIDATION FAILED" not in out


@pytest.mark.parametrize("status_code", [199, 302])
def test_liquidation_treats_non_success_status_near_boundary_as_failure(monkeypatch, status_code):
    """Only status_code in (200, 201, 202) may log success — a 199 or a 302
    redirect must NOT be silently treated as a liquidation."""
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}]

    def post_side_effect(url, **_kwargs):
        return _make_sell_response(status_code, text="")

    _result, out, _calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert "LIQUIDATION FAILED" in out
    assert f"Liquidated Alpha (HTTP {status_code})" not in out


# ---------------------------------------------------------------------------
# AC-2: per-symphony isolation — one failure never aborts the rest
# ---------------------------------------------------------------------------


def test_liquidation_request_exception_on_one_symphony_does_not_abort_the_rest(monkeypatch):
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}, {"symphony_id": "S2", "name": "Beta"}]

    def post_side_effect(url, **_kwargs):
        if "S1" in url:
            raise requests.exceptions.ConnectionError("timeout")
        return _make_sell_response(200, text="ok")

    _result, out, calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert len(calls) == 2, "both symphonies must be attempted even though the first raised"
    assert "Liquidated Beta (HTTP 200)" in out


def test_liquidation_non_2xx_on_one_symphony_does_not_abort_the_rest(monkeypatch):
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}, {"symphony_id": "S2", "name": "Beta"}]

    def post_side_effect(url, **_kwargs):
        if "S1" in url:
            return _make_sell_response(429, text="rate limited")
        return _make_sell_response(200, text="ok")

    _result, out, calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert len(calls) == 2
    assert "LIQUIDATION FAILED" in out and "Alpha" in out
    assert "Liquidated Beta (HTTP 200)" in out


@pytest.mark.parametrize("exc_type", [ValueError, KeyError, TypeError])
def test_liquidation_isolates_non_request_exception_types_per_symphony(monkeypatch, exc_type):
    """
    The per-symphony catch must be broadly typed as ``except Exception`` —
    never narrowly scoped to ``requests.RequestException`` — so a panic-stop
    cannot be stranded by a non-request fault on one symphony (e.g. a
    malformed sym dict raising KeyError/ValueError while building the sell
    URL/payload). (A truly bare ``except:`` is NOT required or desired here —
    that would also swallow ``SystemExit``/``KeyboardInterrupt``, which
    ``except Exception`` correctly does not.) The exception is injected at
    the POST call boundary using non-request exception TYPES as a stand-in
    for "any per-symphony fault"; the assertion is about the catch being
    ``Exception``-broad, not the exact origin of the fault.
    """
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}, {"symphony_id": "S2", "name": "Beta"}]

    def post_side_effect(url, **_kwargs):
        if "S1" in url:
            raise exc_type("simulated non-request fault")
        return _make_sell_response(200, text="ok")

    _result, out, calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert len(calls) == 2, f"a {exc_type.__name__} on symphony 1 must not abort symphony 2"
    assert "Liquidated Beta (HTTP 200)" in out


# ---------------------------------------------------------------------------
# AC-3: structured per-symphony outcome
# ---------------------------------------------------------------------------


def test_liquidation_returns_structured_outcome_marking_success(monkeypatch):
    """
    The function must return a structured per-symphony outcome the operator
    can inspect programmatically — today it returns None (nothing beyond the
    console print is inspectable). Shape is intentionally not over-pinned:
    either {name: {...}} or [{"name": ..., ...}, ...] is accepted.
    """
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}]

    def post_side_effect(url, **_kwargs):
        return _make_sell_response(200, text="ok")

    result, _out, _calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert result is not None, "perform_account_liquidation must return a structured outcome"
    outcome = _outcome_for(result, "Alpha")
    assert outcome is not None, "the returned structure must have an entry for symphony 'Alpha'"
    assert outcome.get("ok") is True


def test_liquidation_structured_outcome_marks_failure_as_not_ok(monkeypatch):
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}]

    def post_side_effect(url, **_kwargs):
        return _make_sell_response(500, text="server error")

    result, _out, _calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    outcome = _outcome_for(result, "Alpha")
    assert outcome is not None
    assert outcome.get("ok") is False
    # a status/reason field of SOME kind must survive for operator diagnosis
    status_fields = (outcome.get("status"), outcome.get("status_code"), outcome.get("reason"))
    assert any("500" in str(v) for v in status_fields if v is not None), (
        "failed outcome must carry a status/reason field identifying HTTP 500"
    )


# ---------------------------------------------------------------------------
# AC-4: mixed batch — both success and failure individually reported
# ---------------------------------------------------------------------------


def test_liquidation_mixed_success_and_failure_both_reported(monkeypatch):
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}, {"symphony_id": "S2", "name": "Beta"}]

    def post_side_effect(url, **_kwargs):
        if "S1" in url:
            return _make_sell_response(200, text="ok")
        return _make_sell_response(429, text="rate limited")

    result, out, calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert len(calls) == 2
    assert "Liquidated Alpha (HTTP 200)" in out
    assert "LIQUIDATION FAILED" in out and "Beta" in out
    assert _outcome_for(result, "Alpha").get("ok") is True
    assert _outcome_for(result, "Beta").get("ok") is False


# ---------------------------------------------------------------------------
# AC-5 (HARD): no-behavior-change — endpoint, payload, and gating unchanged
# ---------------------------------------------------------------------------


def test_liquidation_calls_post_exactly_once_per_symphony_even_under_failure(monkeypatch):
    symphonies = [
        {"symphony_id": "S1", "name": "Alpha"},
        {"symphony_id": "S2", "name": "Beta"},
        {"symphony_id": "S3", "name": "Gamma"},
    ]

    def post_side_effect(url, **_kwargs):
        if "S1" in url:
            return _make_sell_response(429, text="")
        if "S2" in url:
            raise requests.exceptions.Timeout("slow")
        return _make_sell_response(500, text="")

    _result, _out, calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert len(calls) == 3, "every symphony gets exactly one POST attempt regardless of outcome"


def test_liquidation_post_url_headers_and_payload_unchanged(monkeypatch):
    """HARD no-behavior-change pin: the sell POST's URL, headers, json body,
    and timeout must be byte-identical to the pre-fix contract — this cycle
    changes ONLY confirmation/logging/isolation, never the request itself."""
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}]

    def post_side_effect(url, **_kwargs):
        return _make_sell_response(200, text="ok")

    _result, _out, calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == f"{app_module.COMPOSER_BASE_URL}/deploy/accounts/ACC1/symphonies/S1/go-to-cash"
    assert kwargs["json"] == {}
    assert kwargs["timeout"] == 10
    assert kwargs["headers"] == {
        "x-api-key-id": "key",
        "authorization": "Bearer secret",
        "Content-Type": "application/json",
    }


def test_liquidation_get_symphony_stats_meta_call_unchanged(monkeypatch):
    """The upstream GET (account symphony list) is untouched by this fix."""
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}]
    get_calls = []

    def fake_get(url, **kwargs):
        get_calls.append((url, kwargs))
        return _make_get_response(symphonies)

    monkeypatch.setattr(app_module.requests, "get", fake_get)
    monkeypatch.setattr(app_module.requests, "post", lambda *_a, **_k: _make_sell_response(200))
    monkeypatch.setattr(app_module.time, "sleep", lambda *_a, **_kw: None)

    app_module.perform_account_liquidation("ACC1", "key", "secret", live_mode=True)

    assert len(get_calls) == 1
    url, kwargs = get_calls[0]
    assert url == f"{app_module.COMPOSER_BASE_URL}/portfolio/accounts/ACC1/symphony-stats-meta"
    assert kwargs["headers"] == {
        "x-api-key-id": "key",
        "authorization": "Bearer secret",
        "Content-Type": "application/json",
    }
    assert kwargs["timeout"] == 10


def test_liquidation_live_mode_false_still_issues_zero_posts(monkeypatch):
    """Reinforces existing coverage in test_app_routes.py — the second layer
    of defence behind LIVE_EXECUTION must survive the restructure."""
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}]

    def post_side_effect(url, **_kwargs):
        return _make_sell_response(200, text="ok")

    _result, _out, calls = _run_liquidation(
        monkeypatch, symphonies, post_side_effect, live_mode=False
    )

    assert calls == []


def test_liquidation_never_raises_for_any_failure_mix(monkeypatch):
    """The function must never let an exception escape — a crashed panic-stop
    thread with no output at all would be WORSE than a wrong log line."""
    symphonies = [
        {"symphony_id": "S1", "name": "Alpha"},
        {"symphony_id": "S2", "name": "Beta"},
        {"symphony_id": "S3", "name": "Gamma"},
    ]

    def post_side_effect(url, **_kwargs):
        if "S1" in url:
            raise requests.exceptions.ConnectionError("down")
        if "S2" in url:
            raise ValueError("malformed")
        return _make_sell_response(500, text="")

    # If perform_account_liquidation raises, this call itself fails the test.
    _result, _out, _calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)


# ---------------------------------------------------------------------------
# AC-6: golden all-success — regression pin
# ---------------------------------------------------------------------------


def test_liquidation_golden_all_success_logs_each_symphony(monkeypatch):
    symphonies = [
        {"symphony_id": "S1", "name": "Alpha"},
        {"id": "S2", "name": "Beta"},  # lazy fallback key, per existing coverage
    ]

    def post_side_effect(url, **_kwargs):
        return _make_sell_response(200, text="ok")

    result, out, calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert len(calls) == 2
    assert "Liquidated Alpha (HTTP 200)" in out
    assert "Liquidated Beta (HTTP 200)" in out
    assert "LIQUIDATION FAILED" not in out
    assert _outcome_for(result, "Alpha").get("ok") is True
    assert _outcome_for(result, "Beta").get("ok") is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_liquidation_empty_symphony_list_is_a_safe_no_op(monkeypatch):
    def post_side_effect(url, **_kwargs):
        raise AssertionError("POST must never be called for an empty symphony list")

    result, out, calls = _run_liquidation(monkeypatch, [], post_side_effect)

    assert calls == []
    assert out == ""
    # A structured outcome must exist (not None) even when empty.
    assert result is not None
    assert (isinstance(result, dict) and result == {}) or (
        isinstance(result, list) and result == []
    )


def test_liquidation_failure_text_truncated_to_200_chars_in_log(monkeypatch):
    long_text = "X" * 300
    symphonies = [{"symphony_id": "S1", "name": "Alpha"}]

    def post_side_effect(url, **_kwargs):
        return _make_sell_response(500, text=long_text)

    _result, out, _calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert long_text[:200] in out
    assert long_text not in out, "the full 300-char response body must never be logged unbounded"


# ---------------------------------------------------------------------------
# F-003 RESIDUAL (MUST-CLOSE-BEFORE-LIVE, confidence-program cycle):
#
# `name = sym.get(...)` and `sell_url = ...` (app.py:3462-3463) sit OUTSIDE
# the per-symphony try (app.py:3467) that #105/this-file's AC-2 established.
# A non-dict `sym` raises AttributeError at line 3462 BEFORE the try even
# starts — the exception is only caught by the OUTER try (app.py:3457-3486),
# which aborts the ENTIRE per-symphony loop, silently defeating the very
# isolation this file otherwise pins. AC-6: move the extraction INSIDE the
# per-symphony try; a malformed entry yields a per-symphony FAILED outcome
# and the queue continues. HARD GUARD: zero change to sell-request
# construction for valid entries (reuses the AC-5 pins above unchanged).
# ---------------------------------------------------------------------------


def _outcome_count(result) -> int:
    """Shape-tolerant outcome count — dict-keyed-by-name or list-of-dicts."""
    if result is None:
        return 0
    if isinstance(result, dict):
        return len(result)
    if isinstance(result, (list, tuple)):
        return len(result)
    return 0


def _unmatched_outcomes(result, known_names):
    """Outcomes NOT keyed/tagged by any of known_names — i.e. the malformed
    entry's own outcome record, whatever key/shape the implementer chose.
    """
    if isinstance(result, dict):
        return [
            (k, v)
            for k, v in result.items()
            if not (k in known_names or (isinstance(v, dict) and v.get("name") in known_names))
        ]
    if isinstance(result, (list, tuple)):
        return [
            (None, v)
            for v in result
            if not (isinstance(v, dict) and v.get("name") in known_names)
        ]
    return []


@pytest.mark.parametrize(
    "malformed_entry", ["not-a-dict", None, 42, ["nested", "list"]], ids=["str", "none", "int", "list"]
)
def test_malformed_symphony_entry_does_not_abort_the_queue(monkeypatch, malformed_entry):
    """AC-6: a malformed entry BEFORE a valid one must not prevent the valid
    one from being processed — the exact defect: extraction outside the
    per-symphony try aborts the whole loop via the OUTER except.
    """
    symphonies = [
        {"symphony_id": "S1", "name": "Alpha"},
        malformed_entry,
        {"symphony_id": "S2", "name": "Beta"},
    ]

    def post_side_effect(url, **_kwargs):
        return _make_sell_response(200, text="ok")

    result, _out, calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert len(calls) == 2, (
        f"both valid symphonies (Alpha, Beta — surrounding the malformed entry) must each "
        f"get exactly one POST attempt; got {len(calls)} call(s): {calls!r}. A queue-abort "
        "regression (the pre-fix bug) stops after the malformed entry, so Beta (which comes "
        "AFTER it) would never be POSTed."
    )
    assert _outcome_for(result, "Alpha").get("ok") is True
    assert _outcome_for(result, "Beta").get("ok") is True, (
        "Beta comes AFTER the malformed entry in the input list — its outcome being missing "
        "or not-ok proves the queue aborted instead of continuing past the malformed entry."
    )


def test_malformed_symphony_entry_produces_its_own_failed_outcome(monkeypatch):
    """AC-6: the malformed entry itself must yield a per-symphony FAILED
    outcome (not merely be silently skipped) — one outcome record per INPUT
    entry, including the malformed one.
    """
    symphonies = [
        {"symphony_id": "S1", "name": "Alpha"},
        "not-a-dict",
        {"symphony_id": "S2", "name": "Beta"},
    ]

    def post_side_effect(url, **_kwargs):
        return _make_sell_response(200, text="ok")

    result, _out, _calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert _outcome_count(result) == len(symphonies), (
        f"expected one outcome per input entry (2 valid + 1 malformed = {len(symphonies)}), "
        f"got {_outcome_count(result)} outcome(s): {result!r}. A malformed entry that is "
        "silently skipped (rather than recorded as FAILED) still fails AC-6's "
        "'produces a per-symphony FAILED outcome' requirement."
    )

    extras = _unmatched_outcomes(result, {"Alpha", "Beta"})
    assert extras, (
        f"no outcome record found for the malformed entry — result={result!r}. "
        "AC-6 requires the malformed entry to leave its own FAILED outcome, not just "
        "vanish from the queue."
    )
    for _key, outcome in extras:
        assert isinstance(outcome, dict) and outcome.get("ok") is False, (
            f"the malformed entry's outcome must be marked ok=False (a FAILED outcome, "
            f"not a silent pass): got {outcome!r}"
        )


def test_malformed_entry_does_not_change_valid_entries_sell_request(monkeypatch):
    """HARD GUARD (AC-6): a malformed entry anywhere in the queue must not
    alter the sell-request construction for the surrounding valid entries —
    same URL/json/timeout pin as test_liquidation_post_url_headers_and_payload_unchanged,
    now proven with a malformed neighbor present.
    """
    symphonies = [
        "not-a-dict",
        {"symphony_id": "S1", "name": "Alpha"},
    ]

    def post_side_effect(url, **_kwargs):
        return _make_sell_response(200, text="ok")

    _result, _out, calls = _run_liquidation(monkeypatch, symphonies, post_side_effect)

    assert len(calls) == 1, "only the one valid entry should ever reach requests.post"
    url, kwargs = calls[0]
    assert url == f"{app_module.COMPOSER_BASE_URL}/deploy/accounts/ACC1/symphonies/S1/go-to-cash"
    assert kwargs["json"] == {}
    assert kwargs["timeout"] == 10
