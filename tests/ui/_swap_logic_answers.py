"""The answers of the Evaluate swap and Evaluate logic change routes, as the real routes give them.

TEST-ONLY. A page that shows a swap or a logic-change result is read over the body the real route
returned, so the page script is never fed a body built to match its parser. Each function runs the
real route through the Flask test client with the engine's own seams stubbed the way the repository's
engine and route suites already stub them (``tests/_asset_swap_llm_support.py`` and
``tests/advisors/logic_change_http_support.py``): the model side is the real SDK over the in-process
HTTP fake, and only the Composer backtest is a stand-in.

Provenance of the figures a result shows (they are the only numbers the cards print):

* ``stats`` of the baseline are the ``stats`` block of ``tests/fixtures/composer/
  backtest_response_v1.json``, captured from the real Composer backtest endpoint, verbatim.
* ``stats`` of the variant are that same block with the three figure fields named in
  ``VARIANT_EDITS`` replaced (a named edit, so a baseline and a variant differ in value and in
  printed width, which a right-edge rule needs to be able to fail).
* The daily return series a backtest result carries is the synthetic 100-day series the engine
  suites use (the captured responses hold ten days of returns at most); it is not printed.
"""

from __future__ import annotations

import copy
import itertools
import json
import pathlib
from datetime import date, timedelta

REPO = pathlib.Path(__file__).resolve().parents[2]
CAPTURE = REPO / "tests" / "fixtures" / "composer" / "backtest_response_v1.json"

# The edit that makes a variant's figures differ from the baseline's, in value and in width.
VARIANT_EDITS = {
    "sharpe_ratio": -0.41283,
    "sortino_ratio": 12.84021,
    "max_drawdown": 0.07312,
}


def captured_stats() -> dict:
    return json.loads(CAPTURE.read_text(encoding="utf-8"))["stats"]


def _daily_returns() -> dict[str, float]:
    returns: dict[str, float] = {}
    day = date(2022, 1, 1)
    for i in range(100):
        returns[day.isoformat()] = 0.001 * (1 + (i % 5) * 0.1 - 0.2)
        day += timedelta(days=1)
    return returns


def backtest_stand_in():
    """A ``run_backtest`` replacement: captured stats for the first call, the variant's after it."""
    from advisors.composer_backtest_client import BacktestResult

    calls = itertools.count()

    def run(*args, **kwargs):
        stats = captured_stats()
        if next(calls) > 0:
            stats = {**stats, **VARIANT_EDITS}
        return BacktestResult(stats=stats, data_warnings=[], daily_returns=_daily_returns())

    return run


def swap_response(monkeypatch):
    """``POST /ai-advisor/asset-swaps/evaluate`` for the explicit pair the captured reply names."""
    import advisors.asset_swap_engine as ase
    import ai_advisor
    import app as app_module
    from tests import _asset_swap_llm_support as sup

    def no_model():
        raise AssertionError("an explicit pair sends no model request")

    with monkeypatch.context() as mp:
        mp.setattr(ase, "_has_composer_key", lambda: True)
        mp.setattr(ase, "_build_client", no_model)
        mp.setattr(ase, "get_tradeable_set", lambda *a, **k: sup.tradeable_universe())
        mp.setattr(ase, "run_backtest", backtest_stand_in())
        mp.setattr(ase.database, "insert_advisor_observation", lambda *a, **k: 1)
        mp.setattr(
            "database.load_state",
            lambda: {sup.COMPOSER_HASH: {"name": sup.SYMPHONY_NAME}},
        )
        mp.setattr("symphony_logic.fetch_symphony_score", lambda *a, **k: sup.held_tree())
        mp.setattr(
            "ai_advisor.build_reasoning_context",
            lambda *a, **k: ("", copy.deepcopy(ai_advisor._EMPTY_MANIFEST)),
        )
        with app_module.app.test_client() as client:
            response = client.post(sup.EVALUATE_URL, json=sup.explicit_pair_body())
    assert response.status_code == 200, response.get_data(as_text=True)
    return response


def logic_response(monkeypatch):
    """``POST /ai-advisor/logic-changes/evaluate`` over the captured model reply with one real edit."""
    import advisors.logic_change_engine as lce
    import app as app_module
    from tests.advisors import logic_change_http_support as sup

    fake = sup.new_fake()
    fake.queue_message(sup.reply_with_tool_edit(sup.real_edit()))
    with monkeypatch.context() as mp:
        sup.install_route_world(mp, fake)
        mp.setattr(lce, "run_backtest", backtest_stand_in())
        with app_module.app.test_client() as client:
            response = client.post(
                "/ai-advisor/logic-changes/evaluate",
                json={
                    "symphony_id": sup.ROUTE_SYMPHONY_NAME,
                    "change_description": sup.ROUTE_CHANGE_DESCRIPTION,
                },
            )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response
