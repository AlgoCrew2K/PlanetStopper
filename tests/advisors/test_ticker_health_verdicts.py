"""The dead-ticker check's reading of one Alpaca asset answer.

Module under test: advisors.ticker_health (``classify_asset_answer``, ``classify_asset_failure``,
``TickerVerdict``).

Contract pinned here (the implementer reads this file, not the plan):

- ``classify_asset_answer(status, body) -> TickerVerdict``; ``status`` is the HTTP status integer
  and ``body`` the parsed JSON body (any type; ``None`` when the body was not JSON).
  ``TickerVerdict`` has ``state`` (one of the strings ``"dead"``, ``"healthy"``,
  ``"not_checked"``) and ``reason`` (the fixed text below; a healthy verdict's reason is not read).
- Status 200 with a dict body whose ``status`` is ``"active"`` or ``"inactive"`` and whose
  ``tradable`` is a bool:
    inactive and not tradable -> dead, "inactive and not tradable at Alpaca";
    active and not tradable   -> dead, "not tradable at Alpaca";
    inactive and tradable     -> dead, "inactive at Alpaca";
    active and tradable       -> healthy.
  ``exchange`` and every other field play no part.
- Any other 200 body -> not_checked, "Alpaca response unreadable".
- 404 -> dead, "not listed at Alpaca" (whatever the body).
- 401 and 403 -> not_checked, "Alpaca refused the credentials"; 429 -> not_checked,
  "Alpaca rate limit"; 5xx -> not_checked, "Alpaca unavailable"; every other status (a 3xx, any
  other 4xx, a 2xx other than 200, a 1xx) -> not_checked, "Alpaca rejected the request". The body
  of a non-200 answer never changes the verdict and never reaches the reason.
- ``classify_asset_failure()`` (a transport error: timeout, connection error) -> not_checked,
  "Alpaca unreachable".

Fixture provenance: the Alpaca records are captured producer output under
``tests/fixtures/alpaca/assets_captured/`` (PROVENANCE.md there): SHNY (inactive, not tradable),
AKAN (active, not tradable), SPY and UGL (active, tradable), ZZZZQQ (404), SPY with a wrong key
(401). The inactive-and-tradable case and every unreadable-body case are DERIVED from a captured
record by changing one field, and are labelled where they are made.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from advisors import ticker_health as th

CAPTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "alpaca" / "assets_captured"

DEAD_BOTH = "inactive and not tradable at Alpaca"
DEAD_NOT_TRADABLE = "not tradable at Alpaca"
DEAD_INACTIVE = "inactive at Alpaca"
DEAD_UNLISTED = "not listed at Alpaca"
UNREADABLE = "Alpaca response unreadable"
REFUSED = "Alpaca refused the credentials"
RATE_LIMIT = "Alpaca rate limit"
UNAVAILABLE = "Alpaca unavailable"
REJECTED = "Alpaca rejected the request"
UNREACHABLE = "Alpaca unreachable"


def capture(name: str) -> tuple[int, dict]:
    record = json.loads((CAPTURE_DIR / name).read_text(encoding="utf-8"))
    return record["status"], copy.deepcopy(record["body"])


def derived(name: str, **changes) -> tuple[int, dict]:
    """DERIVED: a captured 200 record with the named fields replaced (_REMOVE drops the key)."""
    status, body = capture(name)
    for key, value in changes.items():
        if value is _REMOVE:
            body.pop(key, None)
        else:
            body[key] = value
    return status, body


_REMOVE = object()


class TestStatusAndTradability:
    @pytest.mark.parametrize(
        ("status_body", "reason"),
        [
            (capture("asset_SHNY_inactive_not_tradable.json"), DEAD_BOTH),
            (capture("asset_AKAN_active_not_tradable.json"), DEAD_NOT_TRADABLE),
            (derived("asset_SHNY_inactive_not_tradable.json", tradable=True), DEAD_INACTIVE),
            (derived("asset_SPY_active_tradable.json", tradable=False), DEAD_NOT_TRADABLE),
            (derived("asset_SPY_active_tradable.json", status="inactive"), DEAD_INACTIVE),
            (
                derived("asset_SPY_active_tradable.json", status="inactive", tradable=False),
                DEAD_BOTH,
            ),
        ],
        ids=[
            "captured-inactive-not-tradable",
            "captured-active-not-tradable",
            "derived-inactive-tradable",
            "derived-spy-not-tradable",
            "derived-spy-inactive",
            "derived-spy-inactive-not-tradable",
        ],
    )
    def test_the_four_status_and_tradable_combinations_other_than_healthy_are_dead(
        self, status_body, reason
    ):
        verdict = th.classify_asset_answer(*status_body)
        assert (verdict.state, verdict.reason) == ("dead", reason)

    @pytest.mark.parametrize(
        "name", ["asset_SPY_active_tradable.json", "asset_UGL_active_tradable.json"]
    )
    def test_active_and_tradable_is_healthy(self, name):
        assert th.classify_asset_answer(*capture(name)).state == "healthy"

    @pytest.mark.parametrize("exchange", ["OTC", "NASDAQ", "", None, 7])
    def test_the_exchange_plays_no_part_in_the_verdict(self, exchange):
        # DERIVED: the captured SPY record with its exchange replaced.
        status, body = derived("asset_SPY_active_tradable.json", exchange=exchange)
        assert th.classify_asset_answer(status, body).state == "healthy"

    def test_a_missing_exchange_plays_no_part_either(self):
        status, body = derived("asset_SPY_active_tradable.json", exchange=_REMOVE)
        assert th.classify_asset_answer(status, body).state == "healthy"

    def test_a_dead_verdict_ignores_the_exchange_too(self):
        status, body = derived("asset_SHNY_inactive_not_tradable.json", exchange="NASDAQ")
        assert th.classify_asset_answer(status, body).reason == DEAD_BOTH

    def test_the_body_is_not_modified(self):
        status, body = capture("asset_SHNY_inactive_not_tradable.json")
        before = copy.deepcopy(body)
        th.classify_asset_answer(status, body)
        assert body == before


class TestUnreadableAnswer:
    @pytest.mark.parametrize(
        "body",
        [
            derived("asset_SPY_active_tradable.json", status=_REMOVE)[1],
            derived("asset_SPY_active_tradable.json", tradable=_REMOVE)[1],
            derived("asset_SPY_active_tradable.json", status=None)[1],
            derived("asset_SPY_active_tradable.json", tradable=None)[1],
            derived("asset_SPY_active_tradable.json", status="delisted")[1],
            derived("asset_SPY_active_tradable.json", status=1)[1],
            derived("asset_SPY_active_tradable.json", tradable="true")[1],
            derived("asset_SPY_active_tradable.json", tradable=1)[1],
            derived("asset_SPY_active_tradable.json", tradable=0)[1],
            {},
            [],
            "active",
            None,
            7,
        ],
        ids=[
            "status-missing",
            "tradable-missing",
            "status-null",
            "tradable-null",
            "status-unknown-word",
            "status-int",
            "tradable-text",
            "tradable-one",
            "tradable-zero",
            "empty-object",
            "list",
            "text",
            "null",
            "number",
        ],
    )
    def test_a_200_without_a_readable_status_and_tradable_is_not_checked_never_healthy_or_dead(
        self, body
    ):
        verdict = th.classify_asset_answer(200, body)
        assert (verdict.state, verdict.reason) == ("not_checked", UNREADABLE)

    def test_missing_status_beside_a_tradable_false_is_not_read_as_dead(self):
        # DERIVED: the dead record with its status removed. Half an answer is no answer.
        status, body = derived("asset_SHNY_inactive_not_tradable.json", status=_REMOVE)
        assert th.classify_asset_answer(status, body).state == "not_checked"

    def test_missing_tradable_beside_an_inactive_status_is_not_read_as_dead(self):
        status, body = derived("asset_SHNY_inactive_not_tradable.json", tradable=_REMOVE)
        assert th.classify_asset_answer(status, body).state == "not_checked"


class TestNotFound:
    def test_the_captured_404_reads_not_listed(self):
        verdict = th.classify_asset_answer(*capture("asset_ZZZZQQ_404_not_found.json"))
        assert (verdict.state, verdict.reason) == ("dead", DEAD_UNLISTED)

    @pytest.mark.parametrize("body", [None, "", {}, [], "Not Found", {"message": "no"}])
    def test_a_404_reads_not_listed_whatever_its_body(self, body):
        verdict = th.classify_asset_answer(404, body)
        assert (verdict.state, verdict.reason) == ("dead", DEAD_UNLISTED)


class TestEveryOtherStatus:
    @staticmethod
    def _expected(status: int) -> tuple[str, str | None]:
        if status == 200:
            return ("healthy", None)
        if status == 404:
            return ("dead", DEAD_UNLISTED)
        if status in (401, 403):
            return ("not_checked", REFUSED)
        if status == 429:
            return ("not_checked", RATE_LIMIT)
        if 500 <= status <= 599:
            return ("not_checked", UNAVAILABLE)
        return ("not_checked", REJECTED)

    def test_a_status_decides_the_verdict_even_when_the_body_says_healthy(self):
        # Every status from 100 to 599 gets a healthy record as its body: a reader that looks
        # at the body before the status would call all of them healthy.
        healthy_body = capture("asset_SPY_active_tradable.json")[1]
        wrong = {}
        for status in range(100, 600):
            verdict = th.classify_asset_answer(status, copy.deepcopy(healthy_body))
            state, reason = self._expected(status)
            if verdict.state != state or (reason is not None and verdict.reason != reason):
                wrong[status] = (verdict.state, verdict.reason)
        assert not wrong, f"statuses read wrongly (status: (state, reason)): {wrong}"

    def test_the_captured_401_is_a_refusal_not_a_verdict_on_the_ticker(self):
        verdict = th.classify_asset_answer(*capture("asset_SPY_401_wrong_key.json"))
        assert (verdict.state, verdict.reason) == ("not_checked", REFUSED)

    @pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503, 302, 400, 422, 200])
    def test_no_response_text_reaches_the_reason(self, status):
        marker = "LEAKMARK-alpaca-body-5c2e"
        verdict = th.classify_asset_answer(status, {"message": marker, "status": marker})
        assert marker not in (verdict.reason or "")


class TestTransportFailure:
    def test_a_transport_error_reads_unreachable_and_is_not_checked(self):
        verdict = th.classify_asset_failure()
        assert (verdict.state, verdict.reason) == ("not_checked", UNREACHABLE)


class TestVerdictRecord:
    def test_the_classifier_returns_a_frozen_verdict_record(self):
        import dataclasses

        verdict = th.classify_asset_answer(*capture("asset_SHNY_inactive_not_tradable.json"))
        assert isinstance(verdict, th.TickerVerdict)
        assert (verdict.state, verdict.reason) == ("dead", DEAD_BOTH)
        with pytest.raises(dataclasses.FrozenInstanceError):
            verdict.state = "healthy"
