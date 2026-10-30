"""Failure records of the suggestion and chat sites, and the model an audit row names.

Two contracts:

* A swallowed failure keeps its full detail at DEBUG (traceback, API rejection text) and
  never reaches a WARNING-or-higher record except as the exception type name: the
  browser-facing and default-level logs carry no key, path or API message.
* The ``llm_suggestions`` audit row names the model that served the suggestion it
  records, which differs from the registry's current resolution when a fallback model
  answered or the family was relisted since. The serving model rides on the suggestion
  object the suggest route returns (``model``), which the panel posts back unchanged to
  accept and reject; a value that is not a plain model id is ignored and the row falls
  back to the registry's resolution.

Replies are captured or schema-derived fixtures served over the HTTP fake; model ids
come from the captured Models page.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import anthropic
import pytest

import ai_advisor
from tests._anthropic_http_fake import AnthropicHttpFake
from tests._suggest_and_chat_routes import (
    SUGGEST_CONTEXT,
    SUGGESTION_ITEM,
    error_outcome,
    fallback_reply,
    ids_newest_first,
    post_suggest,
    structured_reply,
    use_client,
)

CLIENT_SECRET = "sk-example-secret-value-9f3a"
API_REJECTION_TEXT = "`temperature` is deprecated for this model."
CHAT_ARTIFACT = {
    "artifact_type": "asset_swap_proposal",
    "artifact_id": "swap-001",
    "symphony_id": "sym-alpha",
    "gate_verdict": "ADOPT_CANDIDATE",
}


@pytest.fixture
def flask_client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client


def _call_suggestions():
    return ai_advisor.request_suggestions(SUGGEST_CONTEXT)


def _call_chat():
    from advisors import advisor_chat

    return advisor_chat.explain_artifact("Why did this pass?", CHAT_ARTIFACT)


SITES = [
    pytest.param("ai_advisor", _call_suggestions, id="suggestions"),
    pytest.param("advisors.advisor_chat", _call_chat, id="chat"),
]


def _site_records(caplog, logger_name):
    return [r for r in caplog.records if r.name == logger_name]


def _shown_records(records):
    """Records a default-level handler would emit."""
    return [r for r in records if r.levelno >= logging.WARNING]


def _assert_nothing_shown_beyond_the_type_name(records, *forbidden: str):
    for record in _shown_records(records):
        text = record.getMessage()
        for fragment in forbidden:
            assert fragment not in text, f"{fragment!r} reached a {record.levelname} record"
        assert record.exc_info is None, "a traceback is attached to a WARNING-or-higher record"
        assert not any(isinstance(arg, BaseException) for arg in (record.args or ())), (
            "an exception object is passed to a WARNING-or-higher record"
        )


def _debug_records_with_detail(records, exc_type):
    return [
        r
        for r in records
        if r.levelno == logging.DEBUG
        and r.exc_info
        and r.exc_info[1] is not None
        and isinstance(r.exc_info[1], exc_type)
    ]


class TestSwallowedFailuresKeepDetailAtDebugOnly:
    @pytest.mark.parametrize(("logger_name", "call"), SITES)
    def test_a_client_construction_failure_shows_only_the_type_at_warning(
        self, monkeypatch, caplog, logger_name, call
    ):
        def explode():
            raise RuntimeError(CLIENT_SECRET)

        monkeypatch.setattr(ai_advisor, "_build_client", explode)

        with caplog.at_level(logging.DEBUG):
            result = call()

        records = _site_records(caplog, logger_name)
        _assert_nothing_shown_beyond_the_type_name(records, CLIENT_SECRET)
        assert CLIENT_SECRET not in repr(result)
        assert _debug_records_with_detail(records, RuntimeError), (
            "the construction failure's detail is not kept at DEBUG"
        )

    @pytest.mark.parametrize(("logger_name", "call"), SITES)
    def test_an_api_rejection_of_a_resolved_model_shows_only_the_type_at_warning(
        self, monkeypatch, caplog, logger_name, call
    ):
        fake = AnthropicHttpFake.with_captured_page()
        fake.queue_message(error_outcome("400_invalid_request.json"))
        use_client(monkeypatch, fake)

        with caplog.at_level(logging.DEBUG):
            result = call()

        records = _site_records(caplog, logger_name)
        _assert_nothing_shown_beyond_the_type_name(records, API_REJECTION_TEXT, "temperature")
        assert any("BadRequestError" in r.getMessage() for r in _shown_records(records))
        assert API_REJECTION_TEXT not in repr(result)
        assert _debug_records_with_detail(records, anthropic.BadRequestError), (
            "the API's rejection text is not kept at DEBUG"
        )


# ---------------------------------------------------------------------------
# The audit row names the model that served the suggestion
# ---------------------------------------------------------------------------


def _suggestions_reply_text() -> str:
    return json.dumps({"suggestions": [SUGGESTION_ITEM]})


def _suggest_via_route(monkeypatch, flask_client, body):
    fake = AnthropicHttpFake.with_captured_page()
    fake.queue_message(body)
    use_client(monkeypatch, fake)
    response = post_suggest(flask_client)
    assert response.status_code == 200
    data = response.get_json()
    assert "error" not in data, data
    return data["suggestions"][0]


def _record_decision(flask_client, route, suggestion):
    """POST accept or reject with every gate and write stubbed; return the audit row's kwargs."""
    import database

    recorded: list[dict] = []
    with (
        patch.object(
            ai_advisor, "revalidate_suggestion_oos", return_value={"passed": True, "detail": "ok"}
        ),
        patch.object(
            database, "get_symphony_strategy", return_value={"params": {}, "locked_vars": []}
        ),
        patch.object(database, "save_symphony_strategy", return_value=None),
        patch.object(database, "normalize_name", side_effect=lambda x: x),
        patch.object(
            database, "record_llm_suggestion", side_effect=lambda **kw: recorded.append(kw)
        ),
        patch.object(ai_advisor, "check_risk_direction_agreement", return_value=None),
    ):
        response = flask_client.post(
            f"/ai-advisor/{route}",
            json={"symphony_id": "example-symphony-hash", "suggestion": suggestion},
        )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert len(recorded) == 1
    return recorded[0]


class TestAuditRowNamesTheServingModel:
    @pytest.mark.parametrize("route", ["accept", "reject"])
    def test_a_fallback_served_suggestion_is_recorded_under_the_fallback_model(
        self, monkeypatch, flask_client, route
    ):
        newest, served = ids_newest_first("fable")[:2]
        body = fallback_reply(_suggestions_reply_text(), declined_id=newest, served_id=served)
        suggestion = _suggest_via_route(monkeypatch, flask_client, body)

        assert suggestion["model"] == served
        row = _record_decision(flask_client, route, suggestion)

        assert row["model_id"] == served
        assert row["model_id"] != newest

    @pytest.mark.parametrize("route", ["accept", "reject"])
    def test_a_primary_served_suggestion_is_recorded_under_the_reply_model(
        self, monkeypatch, flask_client, route
    ):
        body = structured_reply([SUGGESTION_ITEM])
        suggestion = _suggest_via_route(monkeypatch, flask_client, body)

        assert suggestion["model"] == body["model"]
        row = _record_decision(flask_client, route, suggestion)

        assert row["model_id"] == body["model"]

    @pytest.mark.parametrize("route", ["accept", "reject"])
    @pytest.mark.parametrize(
        "forged",
        [
            pytest.param("x" * 500, id="oversized"),
            pytest.param("claude-fable-5-1\nINJECTED", id="control-characters"),
            pytest.param("<script>alert(1)</script>", id="markup"),
            pytest.param("", id="empty"),
            pytest.param(None, id="null"),
            pytest.param(12345, id="not-a-string"),
            pytest.param({"id": "claude-fable-5-1"}, id="object"),
        ],
    )
    def test_a_posted_model_that_is_not_a_plain_id_falls_back_to_the_registry_resolution(
        self, flask_client, route, forged
    ):
        import model_config

        suggestion = {**SUGGESTION_ITEM, "model": forged}

        row = _record_decision(flask_client, route, suggestion)

        assert row["model_id"] == model_config.peek(model_config.Knob.SUGGESTION).model_id

    @pytest.mark.parametrize("route", ["accept", "reject"])
    def test_a_suggestion_without_a_model_is_recorded_under_the_registry_resolution(
        self, flask_client, route
    ):
        import model_config

        row = _record_decision(flask_client, route, dict(SUGGESTION_ITEM))

        assert row["model_id"] == model_config.peek(model_config.Knob.SUGGESTION).model_id
        assert row["model_id"] != ""
