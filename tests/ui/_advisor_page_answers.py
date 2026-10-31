"""Real answers for the /ai-advisor page tests: the runs list and a Frontrunner build's outcome.

TEST-ONLY. ``autotune_runs_answer`` is ``GET /api/autotune-runs`` over rows written by
``database.save_autotune_run``. ``build_outcome`` drives the real run route with the build's leaves
patched (the same leaves ``tests/app/test_frontrunner_build_outcome_route`` patches) and returns the
202 body together with what ``GET /api/frontrunner-build/last-run`` answers afterwards, so the page
reads the route's own bodies for a started build and for its summary.
"""

from __future__ import annotations

import concurrent.futures
import os
from contextlib import ExitStack
from unittest.mock import patch

import app as app_module
import database
from advisors import frontrunner_builder
from tests.ui._page_js_harness import answer

RUN = "/ai-advisor/frontrunner-builder/run"
LAST = "/api/frontrunner-build/last-run"
SYMPHONY_KEYS = ("alpha rotation", "beta momentum", "gamma carry")


class _Inline:
    def submit(self, fn, *args, **kwargs):
        future = concurrent.futures.Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future


def seed_runs(count: int = 3) -> None:
    for index in range(count):
        database.save_autotune_run(
            f"2026-10-{10 + index:02d}T04:00:00",
            SYMPHONY_KEYS[index % len(SYMPHONY_KEYS)],
            0.5,
            1.0,
            "Reverted to Fallback",
            0.4,
            0.3,
            selection_tstat=2.0 + index * 0.01,
            naive_sharpe=1.0 + index * 0.1,
        )


def autotune_runs_answer() -> dict:
    with app_module.app.test_client() as client:
        response = client.get("/api/autotune-runs")
    assert response.status_code == 200, f"GET /api/autotune-runs answered {response.status_code}"
    assert isinstance(response.get_json(), list)
    return answer(response)


def last_run_answer() -> dict:
    with app_module.app.test_client() as client:
        response = client.get(LAST)
    assert response.status_code != 404, f"GET {LAST} is not registered"
    assert response.status_code == 200, f"GET {LAST} answered {response.status_code}"
    return answer(response)


def build_outcome(*, built=(2, 1), roster=("sym-a", "sym-b")) -> dict:
    """Start a build, let it finish, and return ``{started, last_run}`` route specs for it.

    ``built`` is what each symphony's build inserts; an exception instance in it makes that
    symphony's build raise.
    """
    results = list(built)

    def per_symphony(symphony_id, *_a, **_k):
        value = results.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"ANTHROPIC_API_KEY": "placeholder-not-used"}))
        stack.enter_context(patch.object(app_module, "_FRONTRUNNER_BUILD_EXECUTOR", _Inline()))
        stack.enter_context(
            patch.object(frontrunner_builder, "_resolve_live_symphony_roster", lambda: list(roster))
        )
        stack.enter_context(
            patch.object(frontrunner_builder, "_run_build_for_symphony", per_symphony)
        )
        with app_module.app.test_client() as client:
            response = client.post(RUN, json={})
    assert response.status_code == 202, response.get_data(as_text=True)
    return {"started": answer(response), "last_run": last_run_answer()}
