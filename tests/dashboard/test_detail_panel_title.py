"""The detail panel's title is the symphony's display name.

Contract under test:
  * Clicking a card opens the panel and its title reads the symphony's display name, never the
    lowercase internal key that the state poll stamps next to it.
  * An entry without a usable name gives the default title "Symphony Detail", never the internal key;
    opening a symphony the state does not hold also resets the title instead of keeping the last one.
  * The name is written as text, so markup in a name shows literally.
  * Everything else the panel shows for the symphony is unchanged by the title fix.

Real call path: a card's own ``onclick`` attribute, taken from the HTML ``GET /`` renders, runs the real
``openDetailPanel`` from the whole ``static/index.js`` after the real ``/api/state`` body was applied
(see ``_account_page_harness.py``). Fixture provenance: ``tests/fixtures/dashboard/account_filter/PROVENANCE.md``;
the markup and missing-name variants are recorded edits made in the tests below.
"""

from __future__ import annotations

import pytest

from tests.dashboard import _account_page_harness as harness

DEFAULT_TITLE = "Symphony Detail"
UNKNOWN_ID = "zzzzzzzzzzzzzzzzzzzz"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    harness.isolate_environment(monkeypatch, tmp_path)


@pytest.fixture
def fx() -> dict:
    return harness.load_accounts_fixture()


@pytest.fixture
def poll_body(fx, monkeypatch) -> dict:
    """The real /api/state body for the whole fixture (taken after the page HTML is rendered)."""
    with harness.state_api(monkeypatch, fx["bot_state"]) as client:
        response = client.get("/api/state")
    assert response.status_code == 200
    return response.get_json()


def _open_title(tmp_path, fx, body, symphony_id, *, via_card=True, first=None) -> dict:
    """Load the page over ``body``, optionally open ``first``, open ``symphony_id``; return the title state."""
    html = harness.render_dashboard_html(fx["bot_state"])
    opener = "H.clickCard(H.params.id);" if via_card else "H.window.openDetailPanel(H.params.id);"
    before = "H.window.openDetailPanel(H.params.first); await H.flush();" if first else ""
    out = harness.run_page(
        tmp_path,
        "async function (H) {"
        "H.fire('DOMContentLoaded'); await H.flush();"
        + before
        + opener
        + """
          await H.flush();
          const title = H.byId('detail-panel-title');
          return { title: title.textContent, htmlWrites: title.innerHTMLWrites };
        }""",
        html=html,
        routes={"/api/state": body},
        params={"id": symphony_id, "first": first},
    )
    return out["result"]


class TestTitleIsTheDisplayName:
    @pytest.mark.parametrize(
        "role", ["first_standby", "first_active", "second_standby", "second_active"]
    )
    def test_title_reads_the_display_name_not_the_internal_key(self, fx, poll_body, tmp_path, role):
        symphony_id = fx["ids"][role]
        name = fx["bot_state"][symphony_id]["name"]
        key = poll_body["bot_state"][symphony_id].get("normalized_name")
        assert key and key != name, "the poll must carry a lowercase key different from the name"
        result = _open_title(tmp_path, fx, poll_body, symphony_id)
        assert result["title"] == name

    def test_title_reads_a_plain_display_name_on_a_card_whose_key_differs(
        self, fx, poll_body, tmp_path
    ):
        symphony_id = fx["ids"]["first_standby"]
        name = "Copper Ridge Rotation"
        poll_body["bot_state"][symphony_id]["name"] = name
        assert poll_body["bot_state"][symphony_id]["normalized_name"] != name
        result = _open_title(tmp_path, fx, poll_body, symphony_id)
        assert result["title"] == name

    @pytest.mark.parametrize(
        "variant",
        ["no_name_no_key", "internal_key_only", "empty_name_with_key"],
    )
    def test_entry_without_a_usable_name_gives_the_default_title(
        self, fx, poll_body, tmp_path, variant
    ):
        symphony_id = fx["ids"]["first_active"]
        entry = poll_body["bot_state"][symphony_id]
        key = entry["normalized_name"]
        entry.pop("name", None)
        if variant == "no_name_no_key":
            entry.pop("normalized_name")
        elif variant == "empty_name_with_key":
            entry["name"] = ""
        assert entry.get("normalized_name", "") in ("", key)
        result = _open_title(tmp_path, fx, poll_body, symphony_id)
        assert result["title"] == DEFAULT_TITLE, (
            f"a missing name must not fall back to the internal key {key!r}"
        )

    def test_opening_a_symphony_the_state_lacks_resets_the_title(self, fx, poll_body, tmp_path):
        known = fx["ids"]["second_active"]
        result = _open_title(tmp_path, fx, poll_body, UNKNOWN_ID, via_card=False, first=known)
        assert result["title"] == DEFAULT_TITLE, (
            "the title must not keep the previously opened symphony's name"
        )

    def test_markup_in_a_name_shows_literally(self, fx, poll_body, tmp_path):
        symphony_id = fx["ids"]["first_standby"]
        name = "Copper <b>Ridge</b> Rotation"
        poll_body["bot_state"][symphony_id]["name"] = name
        result = _open_title(tmp_path, fx, poll_body, symphony_id)
        assert result["title"] == name
        assert result["htmlWrites"] == [], "the title is written as text, never as HTML"


def _number(entry: dict, key: str):
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(value) >= 900:
        return None
    return value


def _pct(value) -> str:
    return "--" if value is None else f"{'+' if value >= 0 else ''}{value:.2f}%"


class TestRestOfThePanelIsUnchanged:
    @pytest.mark.parametrize("role", ["first_standby", "second_active"])
    def test_panel_values_requests_and_open_state_follow_the_entry(
        self, fx, poll_body, tmp_path, role
    ):
        symphony_id = fx["ids"][role]
        entry = poll_body["bot_state"][symphony_id]
        html = harness.render_dashboard_html(fx["bot_state"])
        out = harness.run_page(
            tmp_path,
            """async function (H) {
              H.fire('DOMContentLoaded'); await H.flush();
              const m = H.mark();
              H.clickCard(H.params.id);
              await H.flush();
              const read = function (id) { const e = H.byId(id); return e ? e.textContent : null; };
              const ids = ['dp-live-ret', 'dp-live-ret-sub', 'dp-stop-level', 'dp-stop-sub',
                           'dp-shadow-peak', 'dp-alpha', 'dp-alpha-sub'];
              const texts = {};
              ids.forEach(function (id) { texts[id] = read(id); });
              return { texts: texts, urls: H.requests.slice(m).map(function (r) { return r.url; }),
                       panelOpen: H.byId('detail-panel').classList.contains('open'),
                       scrimOpen: H.byId('detail-scrim').classList.contains('open') };
            }""",
            html=html,
            routes={"/api/state": poll_body},
            params={"id": symphony_id},
        )
        result = out["result"]
        triggered = bool(entry.get("triggered"))
        live = _number(entry, "triggered_at_return" if triggered else "current_return") or 0
        guard_alpha = _number(entry, "guard_alpha")
        expected = {
            "dp-live-ret": _pct(live),
            "dp-live-ret-sub": "frozen at exit" if triggered else "real-time",
            "dp-stop-level": _pct(_number(entry, "stop_trigger")),
            "dp-stop-sub": "◆ breakeven lock" if entry.get("breakeven_locked") else "vol-scaled",
            "dp-shadow-peak": _pct(_number(entry, "shadow_hwm")),
            "dp-alpha": _pct(guard_alpha),
            "dp-alpha-sub": (
                "not exited"
                if guard_alpha is None
                else ("saved vs hold" if guard_alpha >= 0 else "gave up vs hold")
            ),
        }
        assert result["texts"] == expected
        assert result["panelOpen"] and result["scrimOpen"]
        assert f"/api/chart/{symphony_id}" in result["urls"]
        assert (
            f"/api/performance?scope=symphony&symphony_id={symphony_id}&days=60" in result["urls"]
        )
