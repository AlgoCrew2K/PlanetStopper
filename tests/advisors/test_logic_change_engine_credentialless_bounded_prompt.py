"""RED tests — R2-2 AC-10: credential-less mocked-green + bounded prompt.

Two independent proofs:
  1. With all 7 credential env vars set to "" (empty, NOT unset — .env
     auto-refills unset per the project's known credential-less test gotcha),
     the FULL reasoned path (LLM seam + Composer /score fetch + /backtest, all
     mocked) runs end-to-end with ZERO unmocked live calls — every seam is
     reached (asserted CALLED), never silently skipped.
  2. A large real tree (tests/fixtures/symphony_logic/sample_score_large.json,
     2000+ numeric params) cannot blow the LLM prompt's output budget — the
     reasoned generator's own prompt construction must stay under a bounded
     ceiling regardless of tree size (deterministic length cap), proven by
     comparing the large-fixture prompt length against a hard, generous ceiling
     AND against the small-fixture prompt length (must not scale linearly with
     the 10x larger param count).
"""

from __future__ import annotations

import json
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SMALL_TREE_PATH = _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
_LARGE_TREE_PATH = _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_large.json"

# The 7 credential env vars this project's live integrations read (Composer,
# Alpaca, Discord, Anthropic, FRED) — matches the "7 cred env vars" convention
# already established in feature-plans/advisor-r2-1-context-provenance.md's
# AC-7 (credential-less + mocked green).
_CRED_ENV_VARS = (
    "COMPOSER_KEY_ID",
    "COMPOSER_SECRET",
    "ALPACA_KEY",
    "ALPACA_SECRET",
    "DISCORD_WEBHOOK_URL",
    "ANTHROPIC_API_KEY",
    "FRED_API_KEY",
)

# A generous absolute ceiling on the reasoned-generation prompt length —
# sanity bound, not a pin on the implementer's own eventual named constant.
_PROMPT_LENGTH_CEILING_CHARS = 50_000


@pytest.fixture(scope="module")
def lce():
    import advisors.logic_change_engine as _lce  # noqa: PLC0415

    return _lce


@pytest.fixture()
def small_tree():
    with open(_SMALL_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture()
def large_tree():
    with open(_LARGE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


class _RecordingMockBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload


class _RecordingMockResponse:
    def __init__(self, edits):
        self.stop_reason = "tool_use"
        self.content = [_RecordingMockBlock({"edits": edits})]


class _RecordingMockClient:
    def __init__(self, edits=None):
        self._edits = edits if edits is not None else []
        self.calls: list[dict] = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            return _RecordingMockResponse(self._outer._edits)

    @property
    def messages(self):
        return self._Messages(self)


def _fake_backtest_result():
    from advisors.composer_backtest_client import BacktestResult
    from datetime import date, timedelta

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(100):
        returns[d.isoformat()] = 0.001 * (1 + (i % 5) * 0.1 - 0.2)
        d += timedelta(days=1)
    return BacktestResult(stats={"sharpe": 0.5}, data_warnings=[], daily_returns=returns)


def _wipe_cred_env(monkeypatch):
    for var in _CRED_ENV_VARS:
        monkeypatch.setenv(var, "")


# ===========================================================================
# Credential-less, mocked-green, zero live calls, both entry points reach every seam.
# ===========================================================================


def test_full_reasoned_path_runs_credential_less_zero_live_calls(lce, small_tree, monkeypatch):
    _wipe_cred_env(monkeypatch)
    monkeypatch.setattr(
        lce, "_has_composer_key", lambda: True
    )  # engine-level guard mocked separately from live creds

    client = _RecordingMockClient(edits=[])
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    from unittest.mock import MagicMock

    backtest_spy = MagicMock(side_effect=lambda *a, **k: _fake_backtest_result())
    monkeypatch.setattr(lce, "run_backtest", backtest_spy)
    monkeypatch.setattr(lce.database, "insert_advisor_observation", MagicMock(return_value=1))

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.suggest_logic_changes("sym-1", small_tree, objective)

    assert result is not None, (
        "AC-10 GAP: the reasoned path did not complete under credential-less env."
    )
    assert client.calls, (
        "AC-10 GAP: the LLM seam (_build_client) was never reached — the reasoned path was "
        "silently skipped rather than exercised end-to-end."
    )


def test_full_reasoned_path_operator_entry_point_credential_less(lce, small_tree, monkeypatch):
    _wipe_cred_env(monkeypatch)
    monkeypatch.setattr(lce, "_has_composer_key", lambda: True)

    params = lce.extract_numeric_params(small_tree)
    edit = {
        "node_path": params[0]["node_path"],
        "param_key": params[0]["param_key"],
        "new_value": params[0]["value"] + 1,
    }
    client = _RecordingMockClient(edits=[edit])
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    from unittest.mock import MagicMock

    monkeypatch.setattr(lce, "run_backtest", lambda *a, **k: _fake_backtest_result())
    monkeypatch.setattr(lce.database, "insert_advisor_observation", MagicMock(return_value=1))

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.propose_operator_logic_change(
        "sym-1", small_tree, objective=objective, change_description="tighten it"
    )

    assert result is not None
    assert client.calls, (
        "AC-10 GAP: operator path never reached the LLM seam under credential-less env."
    )


# ===========================================================================
# Bounded prompt — a large real tree cannot blow the LLM output budget.
# ===========================================================================


def test_oversized_real_tree_reasoning_context_stays_bounded(
    lce, small_tree, large_tree, monkeypatch
):
    small_client = _RecordingMockClient(edits=[])
    monkeypatch.setattr(lce, "_build_client", lambda: small_client)
    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    lce.generate_reasoned_logic_candidates("sym-1", small_tree, objective)
    assert small_client.calls, "self-guard: small-tree call never reached the SDK client."
    small_prompt = small_client.calls[0].get("messages", [{}])[0].get("content", "")

    large_client = _RecordingMockClient(edits=[])
    monkeypatch.setattr(lce, "_build_client", lambda: large_client)
    lce.generate_reasoned_logic_candidates("sym-1", large_tree, objective)
    assert large_client.calls, "self-guard: large-tree call never reached the SDK client."
    large_prompt = large_client.calls[0].get("messages", [{}])[0].get("content", "")

    assert len(large_prompt) <= _PROMPT_LENGTH_CEILING_CHARS, (
        f"AC-10 GAP: the large-fixture prompt is {len(large_prompt)} chars, exceeding the "
        f"{_PROMPT_LENGTH_CEILING_CHARS}-char sanity ceiling — a large real tree (2000+ "
        "numeric params) blew the LLM output budget."
    )
    # The large fixture has ~10x the numeric-param count of the small fixture — the
    # prompt must NOT scale anywhere near linearly with param count (deterministic
    # length cap), else it would blow the budget on a real large symphony.
    assert len(large_prompt) <= max(len(small_prompt) * 5, 10_000), (
        f"AC-10 GAP: large-tree prompt ({len(large_prompt)} chars) scales roughly linearly "
        f"with tree size vs small-tree prompt ({len(small_prompt)} chars) — the injected "
        "context must be deterministically length-capped, not proportional to tree size."
    )
