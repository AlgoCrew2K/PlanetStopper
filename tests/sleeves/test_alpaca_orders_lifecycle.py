"""
RED tests — sleeves/alpaca_orders.py: THE only order-capable module (AC-7, AC-8, AC-13, AC-14).

CONTRACT this file specifies for the GREEN implementer (sleeve-integration-impl):

    sleeves/alpaca_orders.py

    @dataclass(frozen=True)
    class OrderResult:
        order: dict | None   # raw Alpaca order object (or list, for get_positions)
        error: str | None    # None on success; never raises — matches
                              # advisors/composer_backtest_client.BacktestResult
                              # convention. On failure, `error` is built from
                              # `type(exc).__name__` (D-1: never echo raw
                              # exception text, which could carry request
                              # bodies / header values).

    def resolve_host(*, live_mode: bool, live_keys_present: bool) -> str:
        # THE single designated host-selection function (Config section of
        # the plan: "Host selection is derived from sleeve status + key
        # presence in exactly one function, unit-locked"). Returns
        # "https://paper-api.alpaca.markets" UNLESS live_mode is True AND
        # live_keys_present is True, in which case it returns
        # "https://api.alpaca.markets". AC-13/AC-14: paper is the floor;
        # live is structurally unreachable without both conditions.

    def submit_bracket_order(
        *, symbol: str, qty: float, side: str,
        take_profit_price: float, stop_loss_price: float,
        time_in_force: str = "day",
        live_mode: bool = False, live_keys_present: bool = False,
        max_retries: int = 4,
    ) -> OrderResult: ...

    def submit_trailing_stop_order(
        *, symbol: str, qty: float, side: str,
        trail_percent: float | None = None, trail_price: float | None = None,
        time_in_force: str = "day",
        live_mode: bool = False, live_keys_present: bool = False,
        max_retries: int = 4,
    ) -> OrderResult: ...

    def cancel_order(*, order_id: str, live_mode: bool = False, live_keys_present: bool = False) -> OrderResult: ...
    def get_order(*, order_id: str, live_mode: bool = False, live_keys_present: bool = False) -> OrderResult: ...
    def get_account(*, live_mode: bool = False, live_keys_present: bool = False) -> OrderResult: ...
    def get_positions(*, live_mode: bool = False, live_keys_present: bool = False) -> OrderResult: ...

Retry policy (mirrors advisors/composer_backtest_client.py exactly):
    - Exponential backoff across a bounded, explicit interval tuple.
    - 429 responses respect the `Retry-After` header when present.
    - Explicit per-request HTTP timeout (never an unbounded call).
    - No top-level network calls at import time.
    - Uses `requests.post`/`requests.get`/`requests.delete` directly — no
      `requests.Session` — so `patch("requests.post", ...)` intercepts
      cleanly (matches composer_backtest_client's own test convention).

Fixture provenance (Gate-1): order fixtures are schema-derived (see
tests/sleeves/_alpaca_fixtures.py), never captured live — the /api-fixture
skill refuses write-verb methods, so a live capture of an order response is
categorically unavailable this cycle.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.sleeves._alpaca_fixtures import (
    load_account_fixture,
    load_order_fixture,
    load_positions_fixture,
)

pytest.importorskip(
    "sleeves.alpaca_orders", reason="RED phase — sleeves.alpaca_orders not implemented yet"
)

import sleeves.alpaca_orders as alpaca_orders  # noqa: E402

_PAPER_HOST = "https://paper-api.alpaca.markets"
_LIVE_HOST = "https://api.alpaca.markets"


def _mock_response(status_code: int, json_body=None, headers: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body if json_body is not None else {}
    resp.headers = headers or {}
    return resp


# ---------------------------------------------------------------------------
# 1. Host resolution — the single gated function (AC-13, AC-14)
# ---------------------------------------------------------------------------


class TestResolveHost:
    def test_paper_mode_resolves_to_paper_host(self):
        assert alpaca_orders.resolve_host(live_mode=False, live_keys_present=False) == _PAPER_HOST

    def test_live_mode_without_live_keys_still_resolves_to_paper_host(self):
        # AC-14: live is impossible-by-construction without live keys present,
        # even if the caller claims live_mode=True.
        assert alpaca_orders.resolve_host(live_mode=True, live_keys_present=False) == _PAPER_HOST

    def test_live_mode_with_live_keys_resolves_to_live_host(self):
        assert alpaca_orders.resolve_host(live_mode=True, live_keys_present=True) == _LIVE_HOST

    def test_live_keys_present_without_live_mode_still_resolves_to_paper_host(self):
        # Presence of keys alone must not arm live — both gates required.
        assert alpaca_orders.resolve_host(live_mode=False, live_keys_present=True) == _PAPER_HOST

    @pytest.mark.parametrize(
        "live_mode,live_keys_present",
        [(False, False), (True, False), (False, True)],
    )
    def test_live_host_string_never_returned_unless_both_gates_true(
        self, live_mode, live_keys_present
    ):
        host = alpaca_orders.resolve_host(live_mode=live_mode, live_keys_present=live_keys_present)
        assert host != _LIVE_HOST, (
            f"resolve_host(live_mode={live_mode}, live_keys_present={live_keys_present}) "
            f"returned the LIVE host — both gates must be True for live to be reachable."
        )


# ---------------------------------------------------------------------------
# 2. submit_bracket_order — success paths against schema-derived fixtures
# ---------------------------------------------------------------------------


class TestSubmitBracketOrder:
    def test_submit_bracket_order_returns_order_on_200(self):
        fixture = load_order_fixture("bracket_new.json")
        with patch.object(
            alpaca_orders.requests, "post", return_value=_mock_response(200, fixture)
        ) as mock_post:
            result = alpaca_orders.submit_bracket_order(
                symbol="SPY",
                qty=10,
                side="buy",
                take_profit_price=512.0,
                stop_loss_price=495.0,
            )
        assert result.error is None
        assert result.order is not None
        assert result.order["order_class"] == "bracket"
        assert mock_post.called

    def test_submit_bracket_order_request_targets_paper_host_by_default(self):
        fixture = load_order_fixture("bracket_new.json")
        with patch.object(
            alpaca_orders.requests, "post", return_value=_mock_response(200, fixture)
        ) as mock_post:
            alpaca_orders.submit_bracket_order(
                symbol="SPY",
                qty=10,
                side="buy",
                take_profit_price=512.0,
                stop_loss_price=495.0,
            )
        call_args = mock_post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
        assert _PAPER_HOST in url, f"expected paper host in request URL, got: {url}"

    def test_submit_bracket_order_body_declares_order_class_bracket(self):
        fixture = load_order_fixture("bracket_new.json")
        with patch.object(
            alpaca_orders.requests, "post", return_value=_mock_response(200, fixture)
        ) as mock_post:
            alpaca_orders.submit_bracket_order(
                symbol="SPY",
                qty=10,
                side="buy",
                take_profit_price=512.0,
                stop_loss_price=495.0,
            )
        body = mock_post.call_args.kwargs.get("json") or {}
        assert body.get("order_class") == "bracket"
        assert "take_profit" in body or "take_profit_price" in str(body)
        assert "stop_loss" in body or "stop_loss_price" in str(body)

    def test_submit_bracket_order_sets_explicit_timeout(self):
        fixture = load_order_fixture("bracket_new.json")
        with patch.object(
            alpaca_orders.requests, "post", return_value=_mock_response(200, fixture)
        ) as mock_post:
            alpaca_orders.submit_bracket_order(
                symbol="SPY",
                qty=10,
                side="buy",
                take_profit_price=512.0,
                stop_loss_price=495.0,
            )
        timeout = mock_post.call_args.kwargs.get("timeout")
        assert timeout is not None, (
            "every Alpaca request must set an explicit timeout (never unbounded)"
        )
        assert timeout > 0

    def test_submit_bracket_order_rejected_response_returns_no_order_no_raise(self):
        fixture = load_order_fixture("bracket_rejected.json")
        with patch.object(
            alpaca_orders.requests, "post", return_value=_mock_response(422, fixture)
        ):
            result = alpaca_orders.submit_bracket_order(
                symbol="SPY",
                qty=10,
                side="buy",
                take_profit_price=512.0,
                stop_loss_price=495.0,
            )
        assert result.error is not None
        assert isinstance(result.error, str) and result.error


# ---------------------------------------------------------------------------
# 3. submit_trailing_stop_order
# ---------------------------------------------------------------------------


class TestSubmitTrailingStopOrder:
    def test_submit_trailing_stop_order_returns_order_on_200(self):
        fixture = load_order_fixture("trailing_stop_new.json")
        with patch.object(
            alpaca_orders.requests, "post", return_value=_mock_response(200, fixture)
        ):
            result = alpaca_orders.submit_trailing_stop_order(
                symbol="SPY",
                qty=10,
                side="sell",
                trail_percent=2.0,
            )
        assert result.error is None
        assert result.order["type"] == "trailing_stop"

    def test_submit_trailing_stop_order_body_carries_trail_percent(self):
        fixture = load_order_fixture("trailing_stop_new.json")
        with patch.object(
            alpaca_orders.requests, "post", return_value=_mock_response(200, fixture)
        ) as mock_post:
            alpaca_orders.submit_trailing_stop_order(
                symbol="SPY",
                qty=10,
                side="sell",
                trail_percent=2.0,
            )
        body = mock_post.call_args.kwargs.get("json") or {}
        assert body.get("trail_percent") == 2.0 or str(body.get("trail_percent")) == "2.0"
        assert body.get("type") == "trailing_stop"

    def test_stop_order_exists_at_broker_independent_of_process_state(self):
        """
        AC-8: a test proves the stop order exists at the broker (fixture)
        independent of our process state. Simulate the engine "restarting"
        by calling get_order fresh (no shared in-memory object) and confirm
        the trailing-stop order is still reported as live by the broker.
        """
        fixture = load_order_fixture("trailing_stop_new.json")
        with patch.object(alpaca_orders.requests, "get", return_value=_mock_response(200, fixture)):
            result = alpaca_orders.get_order(order_id=fixture["id"])
        assert result.error is None
        assert result.order["status"] in ("new", "accepted", "held")
        assert result.order["type"] == "trailing_stop"


# ---------------------------------------------------------------------------
# 4. Order lifecycle read-backs: partial fill, filled, canceled, rejected
# ---------------------------------------------------------------------------


class TestOrderLifecycleReadback:
    @pytest.mark.parametrize(
        "fixture_name,expected_status",
        [
            ("bracket_new.json", "new"),
            ("bracket_partial_fill.json", "partially_filled"),
            ("bracket_filled.json", "filled"),
            ("bracket_rejected.json", "rejected"),
            ("bracket_canceled.json", "canceled"),
        ],
    )
    def test_get_order_reports_fixture_status_verbatim(self, fixture_name, expected_status):
        fixture = load_order_fixture(fixture_name)
        with patch.object(alpaca_orders.requests, "get", return_value=_mock_response(200, fixture)):
            result = alpaca_orders.get_order(order_id=fixture["id"])
        assert result.error is None
        assert result.order["status"] == expected_status

    def test_partial_fill_filled_qty_is_less_than_requested_qty(self):
        fixture = load_order_fixture("bracket_partial_fill.json")
        with patch.object(alpaca_orders.requests, "get", return_value=_mock_response(200, fixture)):
            result = alpaca_orders.get_order(order_id=fixture["id"])
        filled_qty = float(result.order["filled_qty"])
        requested_qty = float(result.order["qty"])
        assert 0 < filled_qty < requested_qty

    def test_cancel_order_returns_order_result_without_raising(self):
        fixture = load_order_fixture("bracket_canceled.json")
        with patch.object(alpaca_orders.requests, "delete", return_value=_mock_response(204, None)):
            result = alpaca_orders.cancel_order(order_id=fixture["id"])
        assert result.error is None


# ---------------------------------------------------------------------------
# 5. get_account / get_positions
# ---------------------------------------------------------------------------


class TestAccountAndPositions:
    def test_get_account_returns_fixture_shape(self):
        fixture = load_account_fixture("account.json")
        with patch.object(alpaca_orders.requests, "get", return_value=_mock_response(200, fixture)):
            result = alpaca_orders.get_account()
        assert result.error is None
        assert result.order["cash"] == fixture["cash"]

    def test_get_positions_returns_fixture_list(self):
        fixture = load_positions_fixture("positions.json")
        with patch.object(alpaca_orders.requests, "get", return_value=_mock_response(200, fixture)):
            result = alpaca_orders.get_positions()
        assert result.error is None
        assert result.order == fixture


# ---------------------------------------------------------------------------
# 6. Bounded retry / backoff / 429 Retry-After (mirrors test_reliability_expansion.py
#    and composer_backtest_client conventions)
# ---------------------------------------------------------------------------


class TestBoundedRetryBackoff:
    def test_transient_500_is_retried_and_bounded_at_max_retries(self):
        call_count = {"n": 0}

        def _side_effect(*_a, **_kw):
            call_count["n"] += 1
            return _mock_response(500, {"message": "internal error"})

        with (
            patch.object(alpaca_orders.requests, "post", side_effect=_side_effect),
            patch.object(alpaca_orders.time, "sleep"),
        ):
            result = alpaca_orders.submit_bracket_order(
                symbol="SPY",
                qty=1,
                side="buy",
                take_profit_price=110.0,
                stop_loss_price=90.0,
                max_retries=3,
            )
        assert call_count["n"] == 3, (
            f"expected exactly max_retries=3 attempts on persistent HTTP 500; got {call_count['n']}"
        )
        assert result.error is not None

    def test_429_respects_retry_after_header(self):
        sleep_calls: list[float] = []

        def _fake_sleep(seconds):
            sleep_calls.append(seconds)

        fixture = load_order_fixture("bracket_new.json")
        responses = [
            _mock_response(429, {"message": "rate limited"}, headers={"Retry-After": "7"}),
            _mock_response(200, fixture),
        ]

        with (
            patch.object(alpaca_orders.requests, "post", side_effect=responses),
            patch.object(alpaca_orders.time, "sleep", side_effect=_fake_sleep),
        ):
            result = alpaca_orders.submit_bracket_order(
                symbol="SPY",
                qty=1,
                side="buy",
                take_profit_price=110.0,
                stop_loss_price=90.0,
                max_retries=4,
            )
        assert result.error is None, "a 429 followed by a 200 must ultimately succeed"
        assert sleep_calls, "429 must trigger a sleep before retrying"
        assert 7 in sleep_calls or any(s >= 7 for s in sleep_calls), (
            f"expected the Retry-After=7 header value to be respected; got sleep calls: {sleep_calls}"
        )

    def test_network_exception_never_propagates_out_of_submit(self):
        import requests as real_requests

        with (
            patch.object(
                alpaca_orders.requests, "post", side_effect=real_requests.ConnectionError("boom")
            ),
            patch.object(alpaca_orders.time, "sleep"),
        ):
            try:
                result = alpaca_orders.submit_bracket_order(
                    symbol="SPY",
                    qty=1,
                    side="buy",
                    take_profit_price=110.0,
                    stop_loss_price=90.0,
                    max_retries=2,
                )
            except Exception as exc:  # pragma: no cover — this branch IS the failure
                pytest.fail(
                    f"submit_bracket_order raised {type(exc).__name__} instead of returning "
                    f"an OrderResult with error set — violates the never-raises contract"
                )
        assert result.error is not None
        assert result.order is None

    def test_retry_has_an_explicit_bounded_interval_not_unbounded_loop(self):
        # Structural guard: the module must expose a bounded retry-interval
        # constant so a future change cannot accidentally introduce an
        # infinite retry loop (mirrors composer_backtest_client's
        # BACKTEST_MAX_RETRY_WAIT_SECONDS pattern).
        assert hasattr(alpaca_orders, "MAX_RETRY_WAIT_SECONDS") or hasattr(
            alpaca_orders, "_BACKOFF_INTERVALS"
        ), (
            "sleeves.alpaca_orders must expose a bounded retry-wait constant "
            "(e.g. MAX_RETRY_WAIT_SECONDS or _BACKOFF_INTERVALS) — an unbounded "
            "retry loop on a broker order endpoint could hang the engine tick."
        )


# ---------------------------------------------------------------------------
# 7. D-1 — no secrets in error/result objects
# ---------------------------------------------------------------------------


class TestNoSecretLeakage:
    def test_error_message_does_not_echo_raw_exception_text_containing_a_key(self):
        import requests as real_requests

        secret_bearing_message = (
            "Failed with header Authorization: Bearer sk-live-SUPERSECRETKEY123"
        )

        with (
            patch.object(
                alpaca_orders.requests,
                "post",
                side_effect=real_requests.RequestException(secret_bearing_message),
            ),
            patch.object(alpaca_orders.time, "sleep"),
        ):
            result = alpaca_orders.submit_bracket_order(
                symbol="SPY",
                qty=1,
                side="buy",
                take_profit_price=110.0,
                stop_loss_price=90.0,
                max_retries=1,
            )
        assert result.error is not None
        assert "SUPERSECRETKEY123" not in result.error, (
            f"error result leaked a secret-bearing exception string verbatim: {result.error!r}. "
            "D-1 contract: use type(exc).__name__, never str(exc), for transport errors."
        )

    def test_no_api_key_env_var_value_appears_in_any_order_result_field(self, monkeypatch):
        monkeypatch.setenv("ALPACA_KEY", "AKPAPERKEYVALUE00001")
        monkeypatch.setenv("ALPACA_SECRET", "supersecretalpacasecretvalue0001")

        fixture = load_order_fixture("bracket_new.json")
        with patch.object(
            alpaca_orders.requests, "post", return_value=_mock_response(200, fixture)
        ):
            result = alpaca_orders.submit_bracket_order(
                symbol="SPY",
                qty=10,
                side="buy",
                take_profit_price=512.0,
                stop_loss_price=495.0,
            )
        result_str = repr(result)
        assert "AKPAPERKEYVALUE00001" not in result_str
        assert "supersecretalpacasecretvalue0001" not in result_str
