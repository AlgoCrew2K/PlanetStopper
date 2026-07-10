"""RED tests — advisors/composer_draft_client.py (NEW — does not exist yet).

Module under test: advisors.composer_draft_client. The ImportError on the
fixture below is the first RED signal.

CONTRACT SOURCE (feature-plans/frontrunner-builder.md §Architecture, "VALIDATED
create contract"):
  POST https://api.composer.trade/api/v0.1/symphonies
  Headers: get_composer_headers() (x-api-key-id, authorization: Bearer ..., which
    alpha_bot_execution.get_composer_headers already returns — reused, not
    reinvented).
  Body: {name, asset_class, description, color, hashtag,
         symphony: {raw_value: <tree>}}
  Response (attested by task-zero guarded live test — not yet run here; these
  tests pin the SHAPE the client must handle, degrading gracefully if a real
  field is absent): {symphony_id, version_id} on success.

Post-create safety read: verify_undeployed(symphony_id) — GET
  /api/v0.1/symphonies/{id}/score (mirrors composer_backtest_client's dvm_capital
  read pattern) and confirms the symphony holds ZERO allocation before the
  caller may mark an approval "uploaded".

ADVERSARIAL FOCUS (assert SHAPE / STRUCTURE / CALL-ARGS only — never a
hardcoded producer value; HTTP is fully mocked, no live network):
  - exact method/path/headers/body per the pinned contract
  - verify_undeployed reads the right endpoint and returns a boolean-like
    "is undeployed" signal derived from the response, not a hardcoded literal
  - 4xx/5xx are surfaced on the result (never swallowed into a silent None/success)
  - a second save_symphony call for an already-uploaded candidate is a no-op
    (idempotency — no duplicate POST)
  - the client module exposes NO invest/deploy method whatsoever (security
    boundary; also covered independently and more thoroughly in
    tests/security/test_frontrunner_no_trade_boundary.py)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-under-test import guard — RED until advisors/composer_draft_client.py exists.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cdc():
    """Import and return the composer_draft_client module (RED until it exists)."""
    import advisors.composer_draft_client as _cdc  # noqa: PLC0415

    return _cdc


# ---------------------------------------------------------------------------
# Shared fixture data — a minimal valid Composer raw_value tree (shape-only,
# no producer-computed values). Mirrors the root shape asserted valid by
# symphony_schema.validate_tree in the existing test suite (step="root").
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_raw_value() -> dict:
    from advisors import symphony_schema  # noqa: PLC0415

    assets = [symphony_schema.make_asset("SPY")]
    wt = symphony_schema.make_weight_equal(assets)
    return symphony_schema.make_root("Test Frontrunner Splice", "daily", [wt])


def _mock_response(status_code: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or ""
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("no JSON body")
    return resp


# ---------------------------------------------------------------------------
# save_symphony — exact contract (method / path / headers / body)
# ---------------------------------------------------------------------------


def test_save_symphony_posts_to_the_pinned_create_endpoint(cdc, minimal_raw_value):
    """save_symphony issues exactly one POST to /api/v0.1/symphonies."""
    with patch("advisors.composer_draft_client.requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            200, {"symphony_id": "new-sid-123", "version_id": "v1"}
        )
        cdc.save_symphony(
            name="Test Frontrunner Splice",
            description="candidate",
            color="#112233",
            hashtag="#frontrunner",
            raw_value=minimal_raw_value,
        )

    assert mock_post.call_count == 1
    call = mock_post.call_args
    url = call.args[0] if call.args else call.kwargs.get("url")
    assert url.rstrip("/").endswith("/symphonies")
    assert url.startswith("https://api.composer.trade/api/v0.1")


def test_save_symphony_sends_the_authenticated_composer_headers(cdc, minimal_raw_value):
    """Headers must be exactly what get_composer_headers() returns — reused, not
    hand-rolled — so a single source of truth governs authentication."""
    from alpha_bot_execution import get_composer_headers

    expected_headers = get_composer_headers()

    with patch("advisors.composer_draft_client.requests.post") as mock_post:
        mock_post.return_value = _mock_response(200, {"symphony_id": "sid", "version_id": "v1"})
        cdc.save_symphony(
            name="n",
            description="d",
            color="#000000",
            hashtag="#h",
            raw_value=minimal_raw_value,
        )

    sent_headers = mock_post.call_args.kwargs.get("headers")
    assert sent_headers is not None
    for key, value in expected_headers.items():
        assert sent_headers.get(key) == value


def test_save_symphony_body_matches_the_pinned_contract_shape(cdc, minimal_raw_value):
    """Body carries exactly: name, asset_class, description, color, hashtag,
    symphony.raw_value — the raw_value passed through UNCHANGED (byte-identical
    dict, not a re-derived copy the client invents fields into)."""
    with patch("advisors.composer_draft_client.requests.post") as mock_post:
        mock_post.return_value = _mock_response(200, {"symphony_id": "sid", "version_id": "v1"})
        cdc.save_symphony(
            name="My Candidate",
            description="a spliced frontrunner overlay",
            color="#abcdef",
            hashtag="#fr",
            raw_value=minimal_raw_value,
            asset_class="EQUITIES",
        )

    sent_body = mock_post.call_args.kwargs.get("json")
    assert sent_body is not None
    assert sent_body["name"] == "My Candidate"
    assert sent_body["description"] == "a spliced frontrunner overlay"
    assert sent_body["color"] == "#abcdef"
    assert sent_body["hashtag"] == "#fr"
    assert sent_body["asset_class"] == "EQUITIES"
    assert sent_body["symphony"]["raw_value"] == minimal_raw_value


def test_save_symphony_asset_class_defaults_to_equities(cdc, minimal_raw_value):
    """AC: asset_class default is EQUITIES per the pinned contract when the
    caller omits it — never silently None or absent."""
    with patch("advisors.composer_draft_client.requests.post") as mock_post:
        mock_post.return_value = _mock_response(200, {"symphony_id": "sid", "version_id": "v1"})
        cdc.save_symphony(
            name="n", description="d", color="#000000", hashtag="#h", raw_value=minimal_raw_value
        )

    sent_body = mock_post.call_args.kwargs.get("json")
    assert sent_body["asset_class"] == "EQUITIES"


# ---------------------------------------------------------------------------
# 4xx/5xx surfaced, not swallowed
# ---------------------------------------------------------------------------


def test_save_symphony_surfaces_a_4xx_error_rather_than_returning_silent_success(
    cdc, minimal_raw_value
):
    """A malformed-raw_value 400 must be visible on the result — the caller
    (approval route) needs to render the error, never mark the item uploaded."""
    with patch("advisors.composer_draft_client.requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            400, text="raw_value failed schema validation"
        )
        result = cdc.save_symphony(
            name="n", description="d", color="#000000", hashtag="#h", raw_value=minimal_raw_value
        )

    # Whatever the result type, an error condition must be detectable and must
    # NOT present a symphony_id as if the create succeeded.
    assert getattr(result, "error", None) or getattr(result, "success", True) is False
    assert not getattr(result, "symphony_id", None)


def test_save_symphony_surfaces_a_5xx_error_rather_than_returning_silent_success(
    cdc, minimal_raw_value
):
    with patch("advisors.composer_draft_client.requests.post") as mock_post:
        mock_post.return_value = _mock_response(503, text="service unavailable")
        result = cdc.save_symphony(
            name="n", description="d", color="#000000", hashtag="#h", raw_value=minimal_raw_value
        )

    assert getattr(result, "error", None) or getattr(result, "success", True) is False
    assert not getattr(result, "symphony_id", None)


def test_save_symphony_never_raises_on_a_transport_error(cdc, minimal_raw_value):
    """Mirrors the D-1 never-raises contract used across composer_backtest_client
    and the rest of the advisors package — a network blip must not crash the
    approval route."""
    import requests

    with patch("advisors.composer_draft_client.requests.post") as mock_post:
        mock_post.side_effect = requests.ConnectionError("boom")
        result = cdc.save_symphony(
            name="n", description="d", color="#000000", hashtag="#h", raw_value=minimal_raw_value
        )

    assert getattr(result, "error", None) or getattr(result, "success", True) is False


# ---------------------------------------------------------------------------
# verify_undeployed — post-create zero-allocation safety read
# ---------------------------------------------------------------------------


def test_verify_undeployed_reads_the_score_endpoint_for_the_created_symphony(cdc):
    with patch("advisors.composer_draft_client.requests.get") as mock_get:
        mock_get.return_value = _mock_response(200, {"legacy_id": "sid-1", "dvm_capital": {}})
        cdc.verify_undeployed("sid-1")

    assert mock_get.call_count == 1
    call = mock_get.call_args
    url = call.args[0] if call.args else call.kwargs.get("url")
    assert "sid-1" in url
    assert "/symphonies/" in url


def test_verify_undeployed_true_when_the_symphony_holds_zero_allocation(cdc):
    """A freshly created (never-deployed) symphony reads back with no capital
    deployed — the client must recognise this as the safe state, derived from
    the response body (never a hardcoded True)."""
    with patch("advisors.composer_draft_client.requests.get") as mock_get:
        # An undeployed symphony's dvm_capital carries no entries for it (or an
        # explicit zero-value series) — shape mirrors composer_backtest_client's
        # dvm_capital contract (composer_backtest_client.py:147).
        mock_get.return_value = _mock_response(200, {"dvm_capital": {}, "legacy_id": "sid-1"})
        is_undeployed = cdc.verify_undeployed("sid-1")

    assert is_undeployed is True


def test_verify_undeployed_false_when_the_symphony_shows_nonzero_capital(cdc):
    """If the read-back ever shows a nonzero position, the client must say so —
    this is the safety backstop the approval route relies on before marking
    'uploaded'."""
    with patch("advisors.composer_draft_client.requests.get") as mock_get:
        mock_get.return_value = _mock_response(
            200, {"dvm_capital": {"sid-1": {"19000": 10000.0}}, "legacy_id": "sid-1"}
        )
        is_undeployed = cdc.verify_undeployed("sid-1")

    assert is_undeployed is False


def test_verify_undeployed_never_raises_on_transport_or_http_error(cdc):
    with patch("advisors.composer_draft_client.requests.get") as mock_get:
        mock_get.return_value = _mock_response(500, text="server error")
        # Must not raise; the safe default on an inconclusive read is "not
        # verified undeployed" (fail-closed — never assume safety on error).
        result = cdc.verify_undeployed("sid-1")

    assert result is False


# ---------------------------------------------------------------------------
# Idempotency — approving the same candidate twice must not double-create
# ---------------------------------------------------------------------------


def test_save_symphony_is_a_noop_when_an_idempotency_key_was_already_used(cdc, minimal_raw_value):
    """The client (or its documented idempotency seam) must refuse to issue a
    second POST for a candidate that already has a recorded symphony_id.

    This test drives the module's own idempotency guard if it exposes one
    (e.g. an already_uploaded_symphony_id kwarg / a dedicated helper) — RED
    until fb-eng picks a concrete idempotency mechanism; the shape asserted
    here is "a second call with the same idempotency marker does not POST
    again", not a specific function name, so it survives whichever concrete
    mechanism fb-eng lands on AS LONG AS it is exercised through save_symphony's
    public keyword surface.
    """
    with patch("advisors.composer_draft_client.requests.post") as mock_post:
        mock_post.return_value = _mock_response(200, {"symphony_id": "sid-1", "version_id": "v1"})
        first = cdc.save_symphony(
            name="n",
            description="d",
            color="#000000",
            hashtag="#h",
            raw_value=minimal_raw_value,
        )
        # Re-invoking with the already-created symphony_id recorded must be a
        # no-op — no second POST.
        cdc.save_symphony(
            name="n",
            description="d",
            color="#000000",
            hashtag="#h",
            raw_value=minimal_raw_value,
            already_uploaded_symphony_id=getattr(first, "symphony_id", None),
        )

    assert mock_post.call_count == 1


# ---------------------------------------------------------------------------
# Structural no-trade guard (module-level; the exhaustive version lives in
# tests/security/test_frontrunner_no_trade_boundary.py)
# ---------------------------------------------------------------------------


def test_module_exposes_no_invest_or_deploy_callable(cdc):
    """composer_draft_client must not implement invest/deploy at all — the
    no-auto-trade boundary is structural, not merely unused."""
    public_names = [n for n in dir(cdc) if not n.startswith("_")]
    for name in public_names:
        lowered = name.lower()
        assert "invest" not in lowered, f"found an invest-shaped symbol: {name}"
        assert "deploy" not in lowered, f"found a deploy-shaped symbol: {name}"
