"""Validator for the Anthropic API fixtures in this directory.

Written from the public references only (the Models API list reference and the
Errors reference), never from the code under test and never from the SDK's
types, which accept shapes the API does not produce. A schema-derived fixture
is acceptable only when this validator passes on it, so a fixture and the
parser that reads it cannot agree with each other by construction.

Reference facts encoded here:
- Models list page: ``data`` (array), ``has_more`` (boolean), ``first_id`` and
  ``last_id`` (string or null). The first and last ids name the first and last
  record of ``data``.
- Model record: ``id``, ``display_name`` and ``created_at`` (RFC 3339) are
  strings, ``type`` is ``"model"``. ``line`` is a string or null,
  ``lifecycle`` is one of active, deprecated or retired, ``deprecated_at`` and
  ``retires_at`` are RFC 3339 strings or null.
- Error body: ``{"type": "error", "error": {"type", "message"}, "request_id"}``
  with the error type fixed by the HTTP status (400 invalid_request_error, 401
  authentication_error, 403 permission_error, 404 not_found_error, 413
  request_too_large, 429 rate_limit_error, 500 api_error, 529
  overloaded_error). 503 is not a documented status of the Messages API; a
  gateway answering it uses the generic server-side ``api_error`` type, which
  is what the 503 fixture carries.

Fixture files may carry two extra top-level shapes that are not part of the
API: ``{"pages": [...]}`` (a multi-page listing) and
``{"cases": {name: {"description", "page", "expect"}}}`` (one page per edge
case). ``expect`` names, per knob, the record id the case's scenario requires
(or null for none); each id must appear in that case's page.
"""

from __future__ import annotations

import json
import pathlib
import re

_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
_LIFECYCLES = {"active", "deprecated", "retired"}
_ERROR_TYPE_BY_STATUS = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    429: "rate_limit_error",
    500: "api_error",
    503: "api_error",
    529: "overloaded_error",
}
_REQUEST_ID = re.compile(r"^req_[A-Za-z0-9]{10,}$")


def _is_rfc3339(value) -> bool:
    return isinstance(value, str) and bool(_RFC3339.match(value))


def check_model_record(record, where="record") -> list[str]:
    problems: list[str] = []
    if not isinstance(record, dict):
        return [f"{where}: not an object"]
    for key in ("id", "display_name"):
        if not isinstance(record.get(key), str) or not record.get(key):
            problems.append(f"{where}: {key} must be a non-empty string")
    if not _is_rfc3339(record.get("created_at")):
        problems.append(f"{where}: created_at must be an RFC 3339 string")
    if record.get("type") != "model":
        problems.append(f"{where}: type must be 'model'")
    if "line" in record and record["line"] is not None and not isinstance(record["line"], str):
        problems.append(f"{where}: line must be a string or null")
    if "lifecycle" in record and record["lifecycle"] not in _LIFECYCLES:
        problems.append(f"{where}: lifecycle must be one of {sorted(_LIFECYCLES)}")
    for key in ("deprecated_at", "retires_at"):
        if key in record and record[key] is not None and not _is_rfc3339(record[key]):
            problems.append(f"{where}: {key} must be an RFC 3339 string or null")
    return problems


def check_models_page(page, where="page") -> list[str]:
    if not isinstance(page, dict):
        return [f"{where}: not an object"]
    problems: list[str] = []
    data = page.get("data")
    if not isinstance(data, list):
        return [f"{where}: data must be an array"]
    if not isinstance(page.get("has_more"), bool):
        problems.append(f"{where}: has_more must be a boolean")
    for key in ("first_id", "last_id"):
        if key not in page or not (page[key] is None or isinstance(page[key], str)):
            problems.append(f"{where}: {key} must be a string or null")
    for i, record in enumerate(data):
        problems.extend(check_model_record(record, f"{where}.data[{i}]"))
    ids = [r.get("id") for r in data if isinstance(r, dict)]
    if len(ids) != len(set(ids)):
        problems.append(f"{where}: duplicate ids in one page")
    if data and isinstance(data[0], dict) and page.get("first_id") != data[0].get("id"):
        problems.append(f"{where}: first_id must name the first record")
    if data and isinstance(data[-1], dict) and page.get("last_id") != data[-1].get("id"):
        problems.append(f"{where}: last_id must name the last record")
    return problems


def _check_expect(expect, page, where) -> list[str]:
    problems: list[str] = []
    if not isinstance(expect, dict) or set(expect) != {"suggestion", "synthesis"}:
        return [f"{where}: expect must have exactly the keys suggestion and synthesis"]
    ids = {r.get("id") for r in page.get("data", []) if isinstance(r, dict)}
    for knob, value in expect.items():
        if value is not None and value not in ids:
            problems.append(f"{where}: expect.{knob} names an id absent from the page")
    return problems


def check_models_file(obj, where="file") -> list[str]:
    if not isinstance(obj, dict):
        return [f"{where}: not an object"]
    problems: list[str] = []
    if "pages" in obj:
        pages = obj["pages"]
        if not isinstance(pages, list) or len(pages) < 2:
            return [f"{where}: pages must be an array of at least two pages"]
        for i, page in enumerate(pages):
            problems.extend(check_models_page(page, f"{where}.pages[{i}]"))
            if isinstance(page, dict) and page.get("has_more") is not (i < len(pages) - 1):
                problems.append(
                    f"{where}.pages[{i}]: has_more must be true on every page but the last"
                )
        return problems
    if "cases" in obj:
        cases = obj["cases"]
        if not isinstance(cases, dict) or not cases:
            return [f"{where}: cases must be a non-empty object"]
        for name, case in cases.items():
            if not isinstance(case, dict) or set(case) != {"description", "page", "expect"}:
                problems.append(f"{where}.cases.{name}: must have description, page and expect")
                continue
            problems.extend(check_models_page(case["page"], f"{where}.cases.{name}.page"))
            problems.extend(_check_expect(case["expect"], case["page"], f"{where}.cases.{name}"))
        return problems
    body = {k: v for k, v in obj.items() if k != "expect"}
    problems.extend(check_models_page(body, where))
    if "expect" in obj:
        problems.extend(_check_expect(obj["expect"], body, where))
    return problems


def check_error_file(obj, where="file") -> list[str]:
    if not isinstance(obj, dict) or set(obj) != {"status", "body"}:
        return [f"{where}: must be an object with exactly status and body"]
    problems: list[str] = []
    status, body = obj["status"], obj["body"]
    if status not in _ERROR_TYPE_BY_STATUS:
        problems.append(f"{where}: status {status!r} is not a documented error status")
    if not isinstance(body, dict) or body.get("type") != "error":
        return problems + [f"{where}: body.type must be 'error'"]
    error = body.get("error")
    if (
        not isinstance(error, dict)
        or not isinstance(error.get("message"), str)
        or not error.get("message")
    ):
        problems.append(f"{where}: body.error.message must be a non-empty string")
    elif error.get("type") != _ERROR_TYPE_BY_STATUS.get(status):
        problems.append(
            f"{where}: body.error.type must be {_ERROR_TYPE_BY_STATUS.get(status)!r} for status {status}"
        )
    if "request_id" in body and not (
        isinstance(body["request_id"], str) and _REQUEST_ID.match(body["request_id"])
    ):
        problems.append(f"{where}: request_id must look like req_<alphanumerics>")
    return problems


_STOP_REASONS = {"end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal"}
_MESSAGE_ID = re.compile(r"^msg_[A-Za-z0-9]{10,}$")
_TOOL_USE_ID = re.compile(r"^toolu_[A-Za-z0-9]{10,}$")


def _check_content_block(block, where) -> list[str]:
    """Block shapes from the Messages reference and the server-side fallback reference."""
    if not isinstance(block, dict) or not isinstance(block.get("type"), str):
        return [f"{where}: a content block is an object with a string type"]
    kind = block["type"]
    problems: list[str] = []
    if kind == "text" and not isinstance(block.get("text"), str):
        problems.append(f"{where}: a text block carries a string text")
    elif kind == "thinking":
        for key in ("thinking", "signature"):
            if not isinstance(block.get(key), str):
                problems.append(f"{where}: a thinking block carries a string {key}")
    elif kind == "tool_use":
        if not (isinstance(block.get("id"), str) and _TOOL_USE_ID.match(block["id"])):
            problems.append(f"{where}: a tool_use id looks like toolu_<alphanumerics>")
        if not isinstance(block.get("name"), str) or not block.get("name"):
            problems.append(f"{where}: a tool_use block carries a name")
        if not isinstance(block.get("input"), dict):
            problems.append(f"{where}: a tool_use input is an object")
    elif kind == "fallback":
        for side in ("from", "to"):
            ref = block.get(side)
            if not (isinstance(ref, dict) and isinstance(ref.get("model"), str) and ref["model"]):
                problems.append(f"{where}: a fallback block names {side}.model")
    return problems


def check_message_file(obj, where="file") -> list[str]:
    """One full Messages response body (captured, or derived from the references)."""
    if not isinstance(obj, dict):
        return [f"{where}: not an object"]
    problems: list[str] = []
    if not (isinstance(obj.get("id"), str) and _MESSAGE_ID.match(obj["id"])):
        problems.append(f"{where}: id must look like msg_<alphanumerics>")
    if obj.get("type") != "message":
        problems.append(f"{where}: type must be 'message'")
    if obj.get("role") != "assistant":
        problems.append(f"{where}: role must be 'assistant'")
    if not isinstance(obj.get("model"), str) or not obj.get("model"):
        problems.append(f"{where}: model must be a non-empty string")
    content = obj.get("content")
    if not isinstance(content, list):
        return problems + [f"{where}: content must be an array"]
    for i, block in enumerate(content):
        problems.extend(_check_content_block(block, f"{where}.content[{i}]"))
    kinds = [b.get("type") for b in content if isinstance(b, dict)]
    if "fallback" in kinds and any(k != "fallback" for k in kinds[: kinds.index("fallback")]):
        problems.append(f"{where}: a fallback block precedes the fallback model's own blocks")
    stop_reason = obj.get("stop_reason")
    if stop_reason not in _STOP_REASONS:
        problems.append(f"{where}: stop_reason must be one of {sorted(_STOP_REASONS)}")
    if stop_reason == "tool_use" and "tool_use" not in kinds:
        problems.append(f"{where}: stop_reason tool_use needs a tool_use block")
    details = obj.get("stop_details")
    if stop_reason == "refusal":
        if not isinstance(details, dict) or details.get("type") != "refusal":
            problems.append(f"{where}: a refusal carries stop_details of type 'refusal'")
        else:
            for key in ("category", "explanation"):
                if key not in details or not (
                    details[key] is None or isinstance(details[key], str)
                ):
                    problems.append(f"{where}: stop_details.{key} must be a string or null")
    elif details is not None:
        problems.append(f"{where}: stop_details is null unless stop_reason is 'refusal'")
    usage = obj.get("usage")
    if not isinstance(usage, dict) or not all(
        isinstance(usage.get(k), int) for k in ("input_tokens", "output_tokens")
    ):
        problems.append(f"{where}: usage carries integer input_tokens and output_tokens")
    elif "fallback" in kinds:
        types = [it.get("type") for it in usage.get("iterations", []) if isinstance(it, dict)]
        if "fallback_message" not in types:
            problems.append(f"{where}: a fallback reply has a fallback_message usage iteration")
    return problems


def validate_messages_directory(root: pathlib.Path | None = None) -> dict[str, list[str]]:
    """Validate every messages fixture; map relative path to problems."""
    root = root or pathlib.Path(__file__).parent
    return {
        f"messages/{path.name}": check_message_file(
            json.loads(path.read_text(encoding="utf-8")), path.name
        )
        for path in sorted((root / "messages").glob("*.json"))
    }


def validate_directory(root: pathlib.Path | None = None) -> dict[str, list[str]]:
    """Validate every models and errors fixture; map relative path to problems."""
    root = root or pathlib.Path(__file__).parent
    report: dict[str, list[str]] = {}
    for path in sorted((root / "models").glob("*.json")):
        report[f"models/{path.name}"] = check_models_file(
            json.loads(path.read_text(encoding="utf-8")), path.name
        )
    for path in sorted((root / "errors").glob("*.json")):
        report[f"errors/{path.name}"] = check_error_file(
            json.loads(path.read_text(encoding="utf-8")), path.name
        )
    return report


if __name__ == "__main__":
    failed = {
        name: probs
        for name, probs in {**validate_directory(), **validate_messages_directory()}.items()
        if probs
    }
    for name, probs in failed.items():
        print(name, *probs, sep="\n  ")
    raise SystemExit(1 if failed else 0)
