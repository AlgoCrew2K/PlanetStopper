"""Single request helper for every Anthropic Messages call that names a resolved model.

``send`` resolves the knob's model through ``model_config``, builds the request, recovers
from a retired model (404), and classifies the reply into a ``Reply`` carrying a reason
code instead of raising. The caller owns retries for a missing tool call, bounded by
``NO_TOOL_CALL_ATTEMPTS``. ``anthropic`` is imported lazily so importing this module
stays free of the SDK.
"""

from __future__ import annotations

import copy

import model_config

NO_TOOL_CALL_ATTEMPTS = 3  # attempts a site may make when a reply carries no tool call

REASON_MODEL_UNAVAILABLE = "model_unavailable"
REASON_OVERRIDE_MODEL_NOT_FOUND = "override_model_not_found"
REASON_OVERRIDE_REQUEST_REJECTED = "override_request_rejected"
REASON_REFUSAL = "refusal"
REASON_MAX_TOKENS = "max_tokens"
REASON_NO_TOOL_CALL = "no_tool_call"

_OVERRIDE_REASONS = frozenset({REASON_OVERRIDE_MODEL_NOT_FOUND, REASON_OVERRIDE_REQUEST_REJECTED})
_FALLBACK_BETA_HEADER = "server-side-fallback-2026-07-01"


def _field(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


class Reply:
    """A classified reply; ``reason`` is None on success."""

    def __init__(self, reason=None, model="", *, blocks=(), env_var="", category=None):
        self.reason = reason
        self.model = model
        self._blocks = tuple(blocks)
        self._env_var = env_var
        self._category = category

    def text(self) -> str:
        if self.reason is not None:
            return ""
        parts = []
        for block in self._blocks:
            text = _field(block, "text")
            if _field(block, "type") == "text" and isinstance(text, str):
                parts.append(text)
        return "".join(parts)

    def tool_input(self):
        if self.reason is not None:
            return None
        for block in self._blocks:
            if _field(block, "type") == "tool_use":
                return _field(block, "input")
        return None

    def reason_text(self) -> str:
        if self.reason is None:
            return ""
        if self.reason == REASON_REFUSAL and self._category:
            return f"{REASON_REFUSAL}: {self._category}"
        if self.reason in _OVERRIDE_REASONS and self._env_var:
            return f"{self.reason}: {self._env_var}"
        return self.reason


def _close_objects(node):
    if isinstance(node, dict):
        for value in node.values():
            _close_objects(value)
        if node.get("type") == "object":
            node["additionalProperties"] = False
    elif isinstance(node, list):
        for item in node:
            _close_objects(item)


def strict_tool(tool: dict) -> dict:
    """Deep copy of ``tool`` in strict mode with every object node closed."""
    closed = copy.deepcopy(tool)
    closed["strict"] = True
    _close_objects(closed)
    return closed


def _create(client, model_id, max_tokens, messages, system, tools, effort, output_schema, timeout):
    output_config = {"effort": effort}
    if output_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": output_schema}
    kwargs = {
        "model": model_id,
        "max_tokens": max_tokens,
        "messages": messages,
        "output_config": output_config,
        "extra_body": {"fallbacks": "default"},
        "extra_headers": {"anthropic-beta": _FALLBACK_BETA_HEADER},
    }
    if system is not None:
        kwargs["system"] = system
    if tools is not None:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = {"type": "auto"}
    if timeout is not None:
        kwargs["timeout"] = timeout
    return client.messages.create(**kwargs)


def _classify(response, requested_id, tools) -> Reply:
    top_model = getattr(response, "model", None)
    model = top_model if isinstance(top_model, str) and top_model else requested_id
    blocks = getattr(response, "content", None) or ()
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        category = _field(getattr(response, "stop_details", None), "category")
        return Reply(
            REASON_REFUSAL,
            model,
            category=category if isinstance(category, str) and category else None,
        )
    if stop_reason == "max_tokens":
        return Reply(REASON_MAX_TOKENS, model)
    if tools and not any(_field(b, "type") == "tool_use" for b in blocks):
        return Reply(REASON_NO_TOOL_CALL, model)
    return Reply(model=model, blocks=blocks)


def send(
    client,
    knob,
    *,
    effort,
    max_tokens,
    messages,
    system=None,
    tools=None,
    output_schema=None,
    timeout=None,
) -> Reply:
    """Send one request on the knob's model and return the classified reply."""
    import anthropic

    choice = model_config.resolve(knob, client)
    if not choice.model_id:
        return Reply(REASON_MODEL_UNAVAILABLE)
    is_override = choice.source == "override"

    def attempt(model_id):
        return _create(
            client, model_id, max_tokens, messages, system, tools, effort, output_schema, timeout
        )

    try:
        response = attempt(choice.model_id)
    except anthropic.NotFoundError:
        if is_override:
            return Reply(REASON_OVERRIDE_MODEL_NOT_FOUND, choice.model_id, env_var=choice.env_var)
        newer = model_config.invalidate(knob, choice.model_id, client)
        if not newer.model_id or newer.model_id == choice.model_id:
            return Reply(REASON_MODEL_UNAVAILABLE)
        try:
            response = attempt(newer.model_id)
        except anthropic.NotFoundError:
            return Reply(REASON_MODEL_UNAVAILABLE)
        return _classify(response, newer.model_id, tools)
    except anthropic.BadRequestError:
        if is_override:
            return Reply(REASON_OVERRIDE_REQUEST_REJECTED, choice.model_id, env_var=choice.env_var)
        raise
    return _classify(response, choice.model_id, tools)
