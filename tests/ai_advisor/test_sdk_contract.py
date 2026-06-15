"""
Cycle fix-live-claude — RED: SDK return-shape contract for `request_suggestions`.

The 3 live tests in ``test_live_claude_advisor.py`` ERROR under a real Claude
call with::

    Claude returned an unparseable response (no structured output).

That error originates in ``ai_advisor.request_suggestions`` where the code
reads ``sdk_response.parsed``. The anthropic Python SDK (version 0.85.0,
declared dependency) does NOT expose a top-level ``.parsed`` on the
``ParsedMessage`` returned by ``client.messages.parse(...)``. Instead, the
validated structured-output instance lives on each ``ParsedTextBlock`` inside
``response.content``::

    response.content[i].parsed_output : Optional[ConfigSuggestionsResponse]

Reading the wrong attribute silently degrades to ``getattr(..., 'parsed', None)
is None`` and yields the unparseable-response error path. The implementation
is reading from a phantom field that has never existed on this SDK version.

These tests pin the REAL SDK return shape — they cannot be satisfied by a
``MagicMock`` whose ``.parsed`` attribute is wired to a Pydantic instance.

Mocking strategy
----------------
We construct genuine ``anthropic.types.parsed_message.ParsedMessage`` /
``ParsedTextBlock`` instances (the same classes the live SDK returns) and
inject them through the ``_build_client`` factory seam. This makes the tests
adversarial: a stub that returns a bare-mock-with-``.parsed``-attribute will
FAIL these tests, because real ``ParsedMessage`` has no ``.parsed`` field.

The 3 live tests in ``test_live_claude_advisor.py`` are the Tier-2 runtime
validators for the same contract; this file is the Tier-1 adversarial
companion that fails locally (no network) the moment the SDK surface drifts
or the implementation reads the wrong field.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — build genuine SDK return objects, not bare mocks.
# ---------------------------------------------------------------------------


def _make_parsed_message(parsed_output_obj):
    """Construct a real ``ParsedMessage`` carrying ``parsed_output_obj``.

    Mirrors the live ``client.messages.parse(...)`` return shape on anthropic
    0.85.0: one ``ParsedTextBlock`` in ``content`` whose ``parsed_output``
    field holds the validated Pydantic instance.

    ``parsed_output_obj`` may be ``None`` to model the truly-malformed live
    response (Claude returns text but the SDK could not validate it against
    the supplied ``output_format``).
    """
    from anthropic.types.parsed_message import ParsedMessage, ParsedTextBlock
    from anthropic.types.usage import Usage

    # ``parsed_output`` is typed ``Optional[ResponseFormatT]`` where
    # ``ResponseFormatT`` is a TypeVar resolved at SDK call time. Direct
    # construction with a concrete Pydantic instance fails Pydantic's
    # ``Input should be None`` check on the unresolved TypeVar. ``model_construct``
    # bypasses validation — this is how the live SDK populates the field
    # internally after a successful parse.
    block = ParsedTextBlock.model_construct(
        type="text",
        text='{"suggestions": []}',
        parsed_output=parsed_output_obj,
        citations=None,
    )
    return ParsedMessage.model_construct(
        id="msg_test",
        type="message",
        role="assistant",
        model="claude-opus-4-7",
        content=[block],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def _empty_context() -> dict:
    """Minimal valid context for request_suggestions. Shape, not values."""
    return {
        "scope": "global",
        "param_definitions": {},
        "valid_ranges": {},
        "current_values": {},
        "locked_vars": [],
        "volatility_regime": {},
        "data_window": {},
        "risk_invariants": {},
        "symphony_logic": {},
        "autotune_signal": {},
    }


# ---------------------------------------------------------------------------
# Test 1 — The SDK return shape contract.
# request_suggestions must extract the structured output from
# response.content[i].parsed_output, NOT from a non-existent .parsed.
# ---------------------------------------------------------------------------


def test_request_suggestions_reads_parsed_output_from_content_block():
    """``request_suggestions`` must extract the validated Pydantic instance
    from ``sdk_response.content[i].parsed_output`` — the real anthropic 0.85.0
    surface for ``messages.parse``. Reading ``sdk_response.parsed`` is the
    documented defect (no such attribute exists on ``ParsedMessage``)."""
    import ai_advisor

    expected = ai_advisor.ConfigSuggestionsResponse(suggestions=[])
    sdk_response = _make_parsed_message(parsed_output_obj=expected)

    fake_client = MagicMock(name="fake_anthropic_client_real_shape")
    fake_client.messages.parse.return_value = sdk_response

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        response, error = ai_advisor.request_suggestions(_empty_context())

    assert error is None, (
        f"happy path with a real-shape ParsedMessage must return error=None; "
        f"got {error!r}. If error mentions 'unparseable response', "
        f"request_suggestions is still reading the phantom .parsed field "
        f"instead of content[i].parsed_output."
    )
    assert isinstance(response, ai_advisor.ConfigSuggestionsResponse), (
        "request_suggestions must yield a ConfigSuggestionsResponse extracted "
        "from content[i].parsed_output"
    )
    assert response.suggestions == [], (
        "the empty-suggestions ParsedMessage must round-trip as suggestions=[]"
    )


def test_request_suggestions_yields_populated_suggestions_from_content_block():
    """A populated parsed_output round-trips through request_suggestions —
    every ConfigSuggestion field reaches the caller intact."""
    import ai_advisor

    populated = ai_advisor.ConfigSuggestionsResponse(
        suggestions=[
            ai_advisor.ConfigSuggestion(
                config_key="MAX_SQUEEZE_FLOOR",
                current_value=0.20,
                suggested_value=0.18,
                rationale="placeholder rationale — shape, not value",
                risk_direction="tightens",
                confidence="medium",
                data_sufficiency="sufficient",
            )
        ]
    )
    sdk_response = _make_parsed_message(parsed_output_obj=populated)

    fake_client = MagicMock(name="fake_client_populated")
    fake_client.messages.parse.return_value = sdk_response

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        response, error = ai_advisor.request_suggestions(_empty_context())

    assert error is None
    assert isinstance(response, ai_advisor.ConfigSuggestionsResponse)
    assert len(response.suggestions) == 1
    s = response.suggestions[0]
    # Shape assertions, not value pins — per
    # [[feedback_no_hardcoded_test_values]].
    assert isinstance(s.config_key, str) and s.config_key
    assert isinstance(s.suggested_value, (float, int, str))
    assert s.risk_direction in {"loosens", "tightens", "neutral"}


# ---------------------------------------------------------------------------
# Test 2 — The truly-malformed real response degrades gracefully.
# Live Claude can return a ParsedMessage whose ParsedTextBlock has
# parsed_output=None (SDK could not validate against output_format). The
# graceful-degradation contract still applies on the REAL shape.
# ---------------------------------------------------------------------------


def test_parsed_output_none_on_real_shape_degrades_gracefully():
    """A genuine ParsedMessage whose ParsedTextBlock has ``parsed_output=None``
    is the real-world malformed response. ``request_suggestions`` must
    return ``(None, error_message)`` — never raise."""
    import ai_advisor

    sdk_response = _make_parsed_message(parsed_output_obj=None)

    fake_client = MagicMock(name="fake_client_malformed_real")
    fake_client.messages.parse.return_value = sdk_response

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        try:
            response, error = ai_advisor.request_suggestions(_empty_context())
        except Exception as exc:  # noqa: BLE001 - the whole point is no-raise
            pytest.fail(
                f"request_suggestions raised {type(exc).__name__} on a "
                f"parsed_output=None response — must degrade gracefully."
            )

    assert response is None
    assert isinstance(error, str) and error.strip(), (
        "a malformed real-shape response must yield a non-empty error message"
    )


def test_no_text_blocks_in_content_degrades_gracefully():
    """A ParsedMessage with empty ``content`` (no ParsedTextBlock at all —
    possible if Claude refuses or only emits tool_use blocks) must degrade
    gracefully, not crash on an IndexError or AttributeError."""
    from anthropic.types.parsed_message import ParsedMessage
    from anthropic.types.usage import Usage

    import ai_advisor

    sdk_response = ParsedMessage.model_construct(
        id="msg_empty",
        type="message",
        role="assistant",
        model="claude-opus-4-7",
        content=[],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=Usage(input_tokens=1, output_tokens=0),
    )

    fake_client = MagicMock(name="fake_client_empty_content")
    fake_client.messages.parse.return_value = sdk_response

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        try:
            response, error = ai_advisor.request_suggestions(_empty_context())
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"request_suggestions raised {type(exc).__name__} on empty "
                f"content list — must degrade gracefully."
            )

    assert response is None
    assert isinstance(error, str) and error.strip()


# ---------------------------------------------------------------------------
# Test 3 — The SDK call signature contract.
# request_suggestions must invoke client.messages.parse(...) with the keyword
# arguments documented for anthropic 0.85.0: model, max_tokens, messages, and
# output_format (the Pydantic class). Drift here (e.g. renaming output_format
# -> response_format prematurely) breaks the live integration.
# ---------------------------------------------------------------------------


def test_request_suggestions_calls_messages_parse_with_documented_kwargs():
    """The SDK call uses ``messages.parse`` with the documented kwargs.

    Pins the call surface so a refactor to ``messages.create`` + manual JSON
    parsing — or a rename of ``output_format`` — is caught here, not in the
    live test."""
    import ai_advisor

    sdk_response = _make_parsed_message(
        parsed_output_obj=ai_advisor.ConfigSuggestionsResponse(suggestions=[])
    )
    fake_client = MagicMock(name="fake_client_call_sig")
    fake_client.messages.parse.return_value = sdk_response

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        ai_advisor.request_suggestions(_empty_context())

    # The method we expect to be called.
    assert fake_client.messages.parse.call_count == 1, (
        "request_suggestions must invoke client.messages.parse exactly once"
    )
    _args, kwargs = fake_client.messages.parse.call_args

    # Documented kwargs for anthropic 0.85.0 messages.parse — all keyword-only
    # (the SDK signature is `*, max_tokens, messages, model, ...`).
    for required in ("model", "max_tokens", "messages", "output_format"):
        assert required in kwargs, (
            f"messages.parse call missing required kwarg {required!r}; got kwargs={list(kwargs)}"
        )

    # output_format must be the Pydantic schema class itself — passing an
    # instance, a dict, or a string here is the documented misuse.
    assert kwargs["output_format"] is ai_advisor.ConfigSuggestionsResponse, (
        "output_format must be the ConfigSuggestionsResponse class itself, "
        f"not {kwargs['output_format']!r}"
    )

    # messages must be a non-empty list of message-shaped dicts.
    msgs = kwargs["messages"]
    assert isinstance(msgs, list) and msgs, "messages kwarg must be a non-empty list"
    for m in msgs:
        assert isinstance(m, dict)
        assert "role" in m and "content" in m

    # model must be a non-empty string. We do NOT pin the literal value —
    # the model constant lives in ai_advisor and may change; we pin shape.
    assert isinstance(kwargs["model"], str) and kwargs["model"], (
        "model kwarg must be a non-empty string"
    )

    # max_tokens must be a positive int.
    assert isinstance(kwargs["max_tokens"], int) and kwargs["max_tokens"] > 0


def test_messages_parse_method_exists_on_installed_sdk():
    """Sanity check on the installed SDK: ``client.messages.parse`` exists.

    Guards against a future anthropic upgrade removing the method (in which
    case the integration must be reworked to ``messages.create`` + manual
    Pydantic validation, and this test is the early-warning signal)."""
    import anthropic

    client = anthropic.Anthropic(api_key="dummy-key-for-attr-check")
    assert hasattr(client.messages, "parse"), (
        "anthropic SDK no longer exposes client.messages.parse — "
        "request_suggestions needs reworking onto messages.create"
    )


def test_parsed_message_has_no_top_level_parsed_field():
    """Locks in the SDK shape that made the original integration silently
    fail: ``ParsedMessage`` does NOT expose a top-level ``.parsed`` attribute.

    If a future anthropic release adds ``.parsed``, this test fails loudly so
    the code review catches the new convenience attribute and we can decide
    whether to consume it — rather than the integration silently relying on
    it again."""
    from anthropic.types.parsed_message import ParsedMessage

    assert "parsed" not in ParsedMessage.model_fields, (
        "ParsedMessage now exposes a 'parsed' field — the historical "
        "implementation that read sdk_response.parsed may now work by "
        "accident. Re-audit ai_advisor.request_suggestions before relying on "
        "it: structured output should still be sourced from "
        "content[i].parsed_output (the per-block validated instance)."
    )
