"""Shared harness for the request-helper tests.

``fake`` is a fresh in-process Anthropic API serving the captured Models page.
The suite-wide registry seed has already resolved both knobs against a
different fake, so a test that expects a listing says so by making the
resolution stale or by asking for ``unresolved_models``.
"""

from __future__ import annotations

import types

import pytest

import model_config
from tests._anthropic_http_fake import AnthropicHttpFake, load_fixture

CAPTURED = load_fixture("models/page_captured.json")
USER_MESSAGES = [{"role": "user", "content": "Explain a trailing stop in one sentence."}]


def newest_id(line: str, records=None) -> str:
    """Oracle: the newest active record of a line, by release date."""
    records = CAPTURED["data"] if records is None else records
    eligible = [
        r for r in records if r.get("line") == line and r.get("lifecycle", "active") == "active"
    ]
    return max(eligible, key=lambda r: r["created_at"])["id"]


def message_body(name: str) -> dict:
    return load_fixture(f"messages/{name}")


def error_outcome(name: str) -> dict:
    return load_fixture(f"errors/{name}")


class Clock:
    """Replacement for ``model_config.time``: the only clock the cache age reads."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.fixture
def fake() -> AnthropicHttpFake:
    return AnthropicHttpFake.with_captured_page()


@pytest.fixture
def clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(model_config, "time", types.SimpleNamespace(monotonic=clock.monotonic))
    return clock
