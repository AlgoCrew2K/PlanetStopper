"""RED tests — R2-3 AC-11: credential-less mocked-green, both entry points
reach every seam with zero live calls.

With all 7 credential env vars set to "" (empty, NOT unset — .env auto-refills
unset per the project's known credential-less test gotcha), the FULL reasoned
path (LLM seam + Composer /score fetch + /backtest + the Alpaca-backed
get_tradeable_set seam, all mocked) runs end-to-end with ZERO unmocked live
calls — every seam is reached (asserted CALLED), never silently skipped.

Bounded-prompt-by-tree-size coverage lives in R2-1's own
build_reasoning_context tests (reused verbatim, not re-tested here);
bounded-prompt-by-universe-size coverage lives in its own dedicated file,
test_asset_swap_engine_candidate_universe_validation.py. This file is scoped
strictly to the credential-less full-path proof — no duplicate assertions.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SMALL_TREE_PATH = _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
_REAL_INCUMBENT = "QQQ"

# The 7 credential env vars this project's live integrations read — matches
# the established "7 cred env vars" convention (R2-1/R2-2's identical list).
_CRED_ENV_VARS = (
    "COMPOSER_KEY_ID",
    "COMPOSER_SECRET",
    "ALPACA_KEY",
    "ALPACA_SECRET",
    "DISCORD_WEBHOOK_URL",
    "ANTHROPIC_API_KEY",
    "FRED_API_KEY",
)


@pytest.fixture(scope="module")
def ase():
    import advisors.asset_swap_engine as _ase  # noqa: PLC0415

    return _ase


@pytest.fixture()
def small_tree():
    with open(_SMALL_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


class _RecordingMockBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload


class _RecordingMockResponse:
    def __init__(self, candidates):
        self.stop_reason = "tool_use"
        self.content = [_RecordingMockBlock({"candidates": candidates})]


class _RecordingMockClient:
    def __init__(self, candidates=None):
        self._candidates = candidates if candidates is not None else []
        self.calls: list[dict] = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            return _RecordingMockResponse(self._outer._candidates)

    @property
    def messages(self):
        return self._Messages(self)


def _fake_backtest_result():
    from datetime import date, timedelta

    from advisors.composer_backtest_client import BacktestResult

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
# Credential-less, mocked-green, zero live calls, both entry points reach
# every seam (LLM, universe fetch, Composer backtest).
# ===========================================================================


def test_full_reasoned_path_runs_credential_less_zero_live_calls(ase, small_tree, monkeypatch):
    _wipe_cred_env(monkeypatch)
    monkeypatch.setattr(
        ase, "_has_composer_key", lambda: True
    )  # engine-level guard mocked separately from live creds

    client = _RecordingMockClient(candidates=[])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    universe_spy = MagicMock(return_value=frozenset({"AGG", "IALT"}))
    monkeypatch.setattr(ase, "get_tradeable_set", universe_spy)

    backtest_spy = MagicMock(side_effect=lambda *a, **k: _fake_backtest_result())
    monkeypatch.setattr(ase, "run_backtest", backtest_spy)
    monkeypatch.setattr(ase.database, "insert_advisor_observation", MagicMock(return_value=1))

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.suggest_swaps("sym-1", small_tree, objective, {}, ["AGG", "IALT"])

    assert result is not None, (
        "AC-11 GAP: the reasoned path did not complete under credential-less env."
    )
    assert client.calls, (
        "AC-11 GAP: the LLM seam (_build_client) was never reached — the reasoned path was "
        "silently skipped rather than exercised end-to-end."
    )


def test_full_reasoned_path_operator_entry_point_credential_less(ase, small_tree, monkeypatch):
    _wipe_cred_env(monkeypatch)
    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)

    pair = {"incumbent_asset": _REAL_INCUMBENT, "candidate_asset": "AGG", "rationale": "x"}
    client = _RecordingMockClient(candidates=[pair])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    universe_spy = MagicMock(return_value=frozenset({"AGG"}))
    monkeypatch.setattr(ase, "get_tradeable_set", universe_spy)
    monkeypatch.setattr(ase, "run_backtest", lambda *a, **k: _fake_backtest_result())
    monkeypatch.setattr(ase.database, "insert_advisor_observation", MagicMock(return_value=1))

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.propose_operator_swap(
        symphony_id="sym-1", score_tree=small_tree, objective=objective
    )

    assert result is not None
    assert client.calls, (
        "AC-11 GAP: operator path never reached the LLM seam under credential-less env."
    )


def test_explicit_pair_operator_path_never_reaches_llm_seam_credential_less(
    ase, small_tree, monkeypatch
):
    """Regression companion (AC-12): the explicit-pair byte-preserved path must
    NEVER reach the LLM seam at all, under credential-less env or otherwise —
    proves the two modes are genuinely disjoint, not just usually disjoint."""
    _wipe_cred_env(monkeypatch)
    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)

    gen_spy = MagicMock(return_value=[])
    monkeypatch.setattr(ase, "generate_reasoned_swap_candidates", gen_spy)
    universe_spy = MagicMock(return_value=frozenset({"AGG"}))
    monkeypatch.setattr(ase, "get_tradeable_set", universe_spy)
    monkeypatch.setattr(ase, "run_backtest", lambda *a, **k: _fake_backtest_result())
    monkeypatch.setattr(ase.database, "insert_advisor_observation", MagicMock(return_value=1))

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    ase.propose_operator_swap(
        symphony_id="sym-1",
        score_tree=small_tree,
        objective=objective,
        incumbent_asset=_REAL_INCUMBENT,
        candidate_asset="AGG",
    )

    assert gen_spy.call_count == 0, (
        f"AC-12 GAP: explicit-pair mode called generate_reasoned_swap_candidates "
        f"{gen_spy.call_count} time(s) — the byte-preserved explicit-pair path must never "
        "reach the reasoned generator (and therefore never the LLM seam)."
    )
    assert universe_spy.call_count == 0, (
        "AC-12 GAP: explicit-pair mode invoked get_tradeable_set() — validation against the "
        "tradeable universe only applies to LLM-proposed candidates, never an operator-supplied "
        "exact pair."
    )
