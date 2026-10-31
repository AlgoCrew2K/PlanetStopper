"""A /api/state poll the server answers with an error is a failed read on the page, not data.

Contract under test:
  * When the shadow history database cannot be opened, ``/api/state`` answers 500 with
    ``{"status": "error", ...}`` and no figures: the same answer as a failed state read, because
    both fail inside the route's one error handler.
  * The page treats that answer as a failed poll: the engine badge and the data-as-of stamp say
    "Connection Lost" / "connection lost", the figures last drawn stay on screen (nothing is blanked
    to zero), and no successful-poll time is recorded for it.
  * The next poll the server answers with data draws it again and clears the cue: an outage that
    ends leaves no page that stays blank or flagged.

Real call path: the real ``static/chrome.js`` and ``static/index.js`` run whole under node over the
page ``GET /`` really renders; the 200 body and the 500 body both come from the real ``/api/state``
route (the 500 by making the committed-read connection fail to open the file, the way an absent
shadow database does). Fixture provenance: ``tests/fixtures/dashboard/account_filter/PROVENANCE.md``.
"""

from __future__ import annotations

import sqlite3

import pytest

from tests.dashboard import _account_page_harness as harness

pytestmark = pytest.mark.usefixtures("_isolated_env")

LOAD_PAGE = "H.fire('DOMContentLoaded'); await H.flush();"


@pytest.fixture
def _isolated_env(monkeypatch, tmp_path):
    harness.isolate_environment(monkeypatch, tmp_path)


@pytest.fixture
def fx() -> dict:
    return harness.load_accounts_fixture()


@pytest.fixture
def bodies(fx, monkeypatch) -> dict:
    """The real route's 200 body, and its 500 body when the shadow database cannot be opened."""
    import app as app_module

    with harness.state_api(monkeypatch, fx["bot_state"]) as client:
        ok = client.get("/api/state")
        assert ok.status_code == 200, f"/api/state answered {ok.status_code}"

        def cannot_open(_state):
            raise sqlite3.OperationalError("unable to open database file")

        with monkeypatch.context() as unopenable:
            unopenable.setattr(app_module, "_committed_shadow_conn", cannot_open)
            failed = client.get("/api/state")
    assert failed.status_code == 500, "an unopenable shadow database must fail the whole answer"
    failure = failed.get_json()
    assert failure["status"] == "error", "the failed answer must say so, not carry figures"
    return {"ok": ok.get_json(), "error": failure}


def _run(tmp_path, fx, bodies, scenario: str) -> dict:
    html = harness.render_dashboard_html(fx["bot_state"])
    out = harness.run_page(
        tmp_path,
        "async function (H) {"
        + LOAD_PAGE.replace("H.fire", "H.deferState(true); H.fire")
        + """
          const shown = function () {
            const figures = {};
            ['hero-tracked', 'hero-armed', 'hero-triggered'].forEach(function (id) {
              figures[id] = H.byId(id).textContent;
            });
            return { figures: figures, cards: H.cardIds().length,
                     label: H.byId('engine-status-label').textContent,
                     asOf: H.byId('hero-data-as-of').textContent };
          };
          const answer = async function (body, status) {
            H.poll();
            const index = H.deferredUrls().length - 1;
            H.release(index, body, status);
            await H.flush();
            return shown();
          };
          H.release(H.deferredUrls().length - 1, H.params.ok);
          await H.flush();
          """
        + scenario
        + "}",
        html=html,
        routes={"/api/state": bodies["ok"]},
        params={"ok": bodies["ok"], "error": bodies["error"]},
    )
    return out["result"]


class TestAFailedPollIsAFailedRead:
    def test_the_connection_lost_cue_is_raised(self, fx, bodies, tmp_path):
        result = _run(
            tmp_path,
            fx,
            bodies,
            """
            const failed = await answer(H.params.error, 500);
            return { failed: failed };
            """,
        )["failed"]
        assert result["label"] == "Connection Lost", (
            "a 500 answer from /api/state must raise the same cue as a poll that never arrived"
        )
        assert result["asOf"] == "connection lost"

    def test_the_figures_last_drawn_are_not_blanked(self, fx, bodies, tmp_path):
        result = _run(
            tmp_path,
            fx,
            bodies,
            """
            const before = shown();
            const failed = await answer(H.params.error, 500);
            return { before: before, failed: failed };
            """,
        )
        assert result["failed"]["figures"] == result["before"]["figures"], (
            "an error body carries no figures; drawing it as data replaced the hero counts"
        )
        assert result["failed"]["cards"] == result["before"]["cards"] > 0, (
            "the symphony cards must stay while the read is failing"
        )

    def test_a_status_error_body_is_a_failed_read_whatever_the_status_code(
        self, fx, bodies, tmp_path
    ):
        result = _run(
            tmp_path,
            fx,
            bodies,
            """
            const failed = await answer(H.params.error, 200);
            return { failed: failed };
            """,
        )["failed"]
        assert result["label"] == "Connection Lost", (
            "the route marks its failure with status 'error' in the body; a proxy that rewrites "
            "the code must not turn that body into figures"
        )

    def test_the_next_answer_with_data_draws_it_and_clears_the_cue(self, fx, bodies, tmp_path):
        result = _run(
            tmp_path,
            fx,
            bodies,
            """
            const before = shown();
            await answer(H.params.error, 500);
            const recovered = await answer(H.params.ok, 200);
            return { before: before, recovered: recovered };
            """,
        )
        assert result["recovered"]["label"] != "Connection Lost", (
            "an outage that ended must not leave the page flagged"
        )
        assert result["recovered"]["figures"] == result["before"]["figures"]
        assert result["recovered"]["cards"] == result["before"]["cards"]
