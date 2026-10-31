"""The Overview's summary chips, the lens stamp and the Symphony label, run on the real page.

Subject: ``static/ai_advisor.js`` over the markup ``GET /ai-advisor`` returns, with
``POST /ai-advisor/suggest`` answered by the body the real route produced (a real
``anthropic.Anthropic`` client on the in-process fake, answering with a captured reply that has
one named edit, the suggestions list; ``tests/_suggest_and_chat_routes.py``).

* The summary strip ("N suggestions", "N OOS passed", "N OOS rejected") is hidden until a
  suggestion run has returned, shows that run's counts, and shows the second run's counts after
  a second run. A run that fails leaves it hidden.
* The lens-cache stamp reads "market context as of" with the date and time in ET.
* The Symphony label's ``for`` names the select's id.

The count text is checked by its leading number; the counts are derived from the route body
(suggestions with ``oos_status`` ``passed`` and ``rejected``), never typed.
Fixture provenance: ``tests/fixtures/ai_advisor/ux_overhaul_02/PROVENANCE.md``.
"""

from __future__ import annotations

import json
import pathlib
import re
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from tests._ai_advisor_page_harness import run_advisor_page
from tests._anthropic_http_fake import AnthropicHttpFake
from tests._suggest_and_chat_routes import (
    SUGGEST_BOT_STATE,
    SUGGEST_CONTEXT,
    SUGGEST_NAME,
    SUGGESTION_ITEM,
    structured_reply,
    use_client,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
VALUES = json.loads(
    (REPO / "tests" / "fixtures" / "ai_advisor" / "ux_overhaul_02" / "time_values.json").read_text(
        encoding="utf-8"
    )
)

_WATCH = {
    "strip": '[data-testid="advisor-summary-strip"]',
    "total": "#chip-total",
    "passed": "#chip-passed",
    "rejected": "#chip-rejected",
    "lens": "#advisor-lens-as-of",
    "container": "#suggestions-container",
    "error": "#advisor-error",
}


@pytest.fixture
def flask_client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _item(key: str, status: str) -> dict:
    return {**SUGGESTION_ITEM, "config_key": key, "oos_status": status}


_RUN_ONE = [
    _item("MAX_SQUEEZE_FLOOR", "passed"),
    _item("TRIGGER_THRESHOLD_PCT", "rejected"),
    _item("VWAP_BLEED_MULTIPLIER", "rejected"),
]
_RUN_TWO = [_item("MAX_SQUEEZE_FLOOR", "passed"), _item("TRIGGER_THRESHOLD_PCT", "passed")]


def _route_bodies(monkeypatch, flask_client, runs, *, lens_as_of=None, lens_stale=None) -> list:
    """The JSON bodies the real suggest route answers, one per run in ``runs``."""
    fake = AnthropicHttpFake.with_captured_page()
    for items in runs:
        fake.queue_message(structured_reply(items))
    use_client(monkeypatch, fake)
    context = {**SUGGEST_CONTEXT, "lens_data_as_of": lens_as_of, "lens_data_stale": lens_stale}
    bodies = []
    for _ in runs:
        with ExitStack() as stack:
            stack.enter_context(patch("database.load_state", return_value=SUGGEST_BOT_STATE))
            stack.enter_context(patch("database.get_latest_autotune_run", return_value=None))
            stack.enter_context(patch("ai_advisor.assemble_advisor_context", return_value=context))
            stack.enter_context(patch("app._compute_suggestion_gates", return_value={}))
            stack.enter_context(patch("app._enrich_suggestion_impact", return_value={}))
            response = flask_client.post("/ai-advisor/suggest", json={"symphony_id": SUGGEST_NAME})
        assert response.status_code == 200
        body = response.get_json()
        assert "error" not in body, body
        bodies.append(body)
    return bodies


def _page(flask_client) -> str:
    response = flask_client.get("/ai-advisor")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _run_steps(tmp_path, html, bodies, *, runs: int | None = None):
    steps = [
        {"op": "set", "selector": "#symphony-id-input", "prop": "value", "value": SUGGEST_NAME}
    ]
    for i in range(runs if runs is not None else len(bodies)):
        steps.append({"op": "call", "fn": "getSuggestions", "label": f"run{i + 1}"})
    return run_advisor_page(
        html,
        tmp_path,
        steps=steps,
        routes={"/ai-advisor/suggest": [{"body": b} for b in bodies]},
        watch=_WATCH,
    )


def _lead_number(text: str) -> int:
    match = re.match(r"\s*(\d+)", text)
    assert match, f"a chip must lead with its count; it reads {text!r}"
    return int(match.group(1))


def _counts(body: dict) -> tuple[int, int, int]:
    suggestions = body["suggestions"]
    return (
        len(suggestions),
        sum(1 for s in suggestions if s["oos_status"] == "passed"),
        sum(1 for s in suggestions if s["oos_status"] == "rejected"),
    )


def _chips(obs: dict) -> tuple[int, int, int]:
    w = obs["watched"]
    return (
        _lead_number(w["total"][0]["text"]),
        _lead_number(w["passed"][0]["text"]),
        _lead_number(w["rejected"][0]["text"]),
    )


# ---------------------------------------------------------------------------
# Summary chips
# ---------------------------------------------------------------------------


class TestSummaryChips:
    def test_the_strip_is_hidden_before_any_run(self, flask_client, tmp_path):
        result = _run_steps(tmp_path, _page(flask_client), [], runs=0)
        strip = result["loaded"]["watched"]["strip"][0]
        assert strip["visible"] is False, "the strip must not show before a suggestion run returns"
        assert result["errors"] == []

    def test_a_run_shows_the_routes_counts(self, monkeypatch, flask_client, tmp_path):
        bodies = _route_bodies(monkeypatch, flask_client, [_RUN_ONE])
        result = _run_steps(tmp_path, _page(flask_client), bodies)
        obs = result["steps"][-1]["obs"]
        assert obs["watched"]["strip"][0]["visible"] is True
        assert _chips(obs) == _counts(bodies[0])
        assert _counts(bodies[0]) == (3, 1, 2), "the fixture run holds 1 passed and 2 rejected"
        assert result["errors"] == []

    def test_a_second_run_shows_the_second_runs_counts(self, monkeypatch, flask_client, tmp_path):
        bodies = _route_bodies(monkeypatch, flask_client, [_RUN_ONE, _RUN_TWO])
        result = _run_steps(tmp_path, _page(flask_client), bodies)
        first, second = result["steps"][1]["obs"], result["steps"][2]["obs"]
        assert _chips(first) == _counts(bodies[0])
        assert _chips(second) == _counts(bodies[1])
        assert _counts(bodies[0]) != _counts(bodies[1])
        assert second["watched"]["strip"][0]["visible"] is True

    def test_a_run_with_no_suggestions_shows_the_strip_with_zeros(
        self, monkeypatch, flask_client, tmp_path
    ):
        bodies = _route_bodies(monkeypatch, flask_client, [[]])
        result = _run_steps(tmp_path, _page(flask_client), bodies)
        obs = result["steps"][-1]["obs"]
        assert obs["watched"]["strip"][0]["visible"] is True
        assert _chips(obs) == (0, 0, 0)

    def test_a_failed_run_leaves_the_strip_hidden(self, flask_client, tmp_path):
        result = run_advisor_page(
            _page(flask_client),
            tmp_path,
            steps=[
                {"op": "set", "selector": "#symphony-id-input", "prop": "value", "value": "x"},
                {"op": "call", "fn": "getSuggestions"},
            ],
            routes={"/ai-advisor/suggest": {"body": {"error": "AdvisorUnavailable"}}},
            watch=_WATCH,
        )
        obs = result["steps"][-1]["obs"]
        assert obs["watched"]["strip"][0]["visible"] is False
        assert "AdvisorUnavailable" in obs["watched"]["error"][0]["text"]


# ---------------------------------------------------------------------------
# The lens-cache stamp
# ---------------------------------------------------------------------------


class TestLensStamp:
    def test_the_stamp_reads_the_date_and_time_in_et(self, monkeypatch, flask_client, tmp_path):
        bodies = _route_bodies(
            monkeypatch,
            flask_client,
            [_RUN_ONE],
            lens_as_of=VALUES["lens_cache_captured_at"],
            lens_stale=False,
        )
        assert bodies[0]["lens_data_as_of"] == VALUES["lens_cache_captured_at"]
        result = _run_steps(tmp_path, _page(flask_client), bodies)
        stamp = result["steps"][-1]["obs"]["watched"]["lens"][0]
        assert stamp["visible"] is True
        assert stamp["text"].lower().startswith("market context as of")
        # 18:50:25 UTC on 2026-10-22 is 14:50 in New York (summer time, UTC-4).
        assert "2026-10-22 14:50 ET" in stamp["text"]
        assert "123456" not in stamp["text"] and "+00:00" not in stamp["text"]
        assert "stale" not in stamp["text"].lower()

    def test_a_stale_bundle_says_so(self, monkeypatch, flask_client, tmp_path):
        bodies = _route_bodies(
            monkeypatch,
            flask_client,
            [_RUN_ONE],
            lens_as_of=VALUES["lens_cache_captured_at"],
            lens_stale=True,
        )
        result = _run_steps(tmp_path, _page(flask_client), bodies)
        stamp = result["steps"][-1]["obs"]["watched"]["lens"][0]
        assert "2026-10-22 14:50 ET" in stamp["text"]
        assert "stale" in stamp["text"].lower()

    def test_no_capture_time_hides_the_stamp(self, monkeypatch, flask_client, tmp_path):
        bodies = _route_bodies(monkeypatch, flask_client, [_RUN_ONE], lens_as_of=None)
        result = _run_steps(tmp_path, _page(flask_client), bodies)
        stamp = result["steps"][-1]["obs"]["watched"]["lens"][0]
        assert stamp["visible"] is False and stamp["text"] == ""


# ---------------------------------------------------------------------------
# The Symphony label
# ---------------------------------------------------------------------------


class TestSymphonyLabel:
    def test_the_label_names_the_selects_id(self, flask_client):
        html = _page(flask_client)
        start = re.search(r'<div[^>]*data-testid="advisor-controls"', html).start()
        controls = html[start:].split('data-testid="run-advisor-btn"', 1)[0]
        select = re.search(r'<select[^>]*data-testid="symphony-select"[^>]*>', controls)
        assert select, "the symphony select is missing from the controls bar"
        select_id = re.search(r'id="([^"]+)"', select.group(0)).group(1)
        labels = re.findall(r'<label[^>]*for="([^"]+)"[^>]*>\s*Symphony\s*</label>', controls)
        assert labels == [select_id], (labels, select_id)
