"""Characterization of the app's boolean environment-flag sites.

Sites covered, each through its real route:

* page meta (``_build_meta``, ``app.py:3639``) for ``GET /``, ``/history``, ``/performance``,
         ``/settings`` and ``GET /api/header-status`` (the API route that reaches the same site).
* ``GET /api/state`` live and frozen (``app.py:5465``, ``:5546``, ``:5642``).
* ``POST /api/sell_account`` (``app.py:8826``).
* ``GET /api/symphony-settings/<name>`` (``app.py:9625``).
* a ``.env`` key with no ``=`` (the mapping holds ``None``) at every unguarded site.
* ``POST /api/sell_account`` refusals that precede the edited line.
* ``POST /api/sleeves/<id>/arm-live`` (``app.py:9096``).

Expected values: the reference expression copied verbatim (``tests/_env_flag_spellings``), the
canonical spelling's own response for the same oracle value, and the outcomes the bare key had at
the reference commit, recorded by ``tests/fixtures/hygiene_waves_3_4/engine_math`` (see its
PROVENANCE.md). These cases pass on the reference code by design; they fail only if a site's
result changes for some spelling.

No test reaches a network: the HTTP layer refuses every call that is not a recorded fake, and
every database write lands in the per-test database that ``tests/conftest.py`` isolates.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import app as app_module
import database
from tests._env_flag_spellings import (
    ABSENT,
    ALL_CASES,
    BARE_KEY,
    case_id,
    mapping_with,
    ref_flag,
)
from tests.app._env_flag_sites import (
    BARE_KEY_SITES,
    arm_live_drive,
    bare_key_outcome,
    capture_pages,
    install_env,
    outcome_of,
    refuse_network,
    sell_account_drive,
)
from tests.app._state_poll_worlds import WORLDS, comparable

BARE_RECORD = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "hygiene_waves_3_4"
    / "engine_math"
    / "env_flag_bare_key_outcomes.json"
)

ET = ZoneInfo("America/New_York")


def _canonical(oracle: bool) -> str:
    return "True" if oracle else "False"


def _bare_record() -> dict:
    return json.loads(BARE_RECORD.read_text(encoding="utf-8"))


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def no_network(monkeypatch):
    return refuse_network(monkeypatch)


# ---------------------------------------------------------------------------
# page meta
# ---------------------------------------------------------------------------


def _meta_mode(oracle: bool) -> str:
    return "LIVE" if oracle else "DRY RUN"


@pytest.fixture
def header_clock(monkeypatch):
    now = datetime(2026, 10, 22, 10, 43, 12, tzinfo=ET)
    monkeypatch.setattr(app_module, "_now_et", lambda: now)
    monkeypatch.setattr(
        app_module.database,
        "load_state",
        lambda: {"last_successful_cycle_at": (now - timedelta(seconds=20)).isoformat()},
    )
    return now


class TestPageMetaFlagParse:
    @pytest.mark.parametrize("value", ALL_CASES, ids=case_id)
    @pytest.mark.parametrize("path", ["/", "/history", "/performance", "/settings"])
    def test_page_meta_mode_matches_reference_for_every_spelling(
        self, monkeypatch, client, no_network, path, value
    ):
        install_env(monkeypatch, mapping_with("LIVE_EXECUTION", value))
        seen = capture_pages(monkeypatch)
        response = client.get(path)
        assert response.status_code == 200
        assert len(seen) == 1, "the page rendered no template or more than one"
        (meta,) = seen.values()
        assert meta["mode"] == _meta_mode(ref_flag(value, "False"))

    @pytest.mark.parametrize("value", ALL_CASES, ids=case_id)
    def test_header_status_meta_mode_matches_reference_for_every_spelling(
        self, monkeypatch, client, header_clock, no_network, value
    ):
        install_env(monkeypatch, mapping_with("LIVE_EXECUTION", value))
        response = client.get("/api/header-status")
        assert response.status_code == 200
        assert response.get_json()["meta"]["mode"] == _meta_mode(ref_flag(value, "False"))


# ---------------------------------------------------------------------------
# GET /api/state, live and frozen
# ---------------------------------------------------------------------------


class TestApiStateFlagParse:
    @pytest.mark.parametrize("world_name", ["open_two_accounts", "closed_frozen_two_accounts"])
    def test_api_state_body_equals_canonical_spelling_body_for_every_spelling(
        self, monkeypatch, tmp_path, world_name
    ):
        world = WORLDS[world_name](monkeypatch, tmp_path)
        bodies = {}
        for value in ALL_CASES:
            install_env(monkeypatch, mapping_with("LIVE_EXECUTION", value))
            bodies[case_id(value)] = comparable(world.body())
        for value in ALL_CASES:
            oracle = ref_flag(value, "False")
            install_env(monkeypatch, mapping_with("LIVE_EXECUTION", _canonical(oracle)))
            canonical = comparable(world.body())
            assert bodies[case_id(value)] == canonical, (
                f"{world_name}: spelling {value!r} gave a body unlike the canonical "
                f"{_canonical(oracle)!r} body"
            )

    @pytest.mark.parametrize("world_name", ["open_two_accounts", "closed_frozen_two_accounts"])
    @pytest.mark.parametrize("oracle", [True, False])
    def test_api_state_reports_live_mode_and_meta_mode_of_the_oracle(
        self, monkeypatch, tmp_path, world_name, oracle
    ):
        world = WORLDS[world_name](monkeypatch, tmp_path)
        install_env(monkeypatch, mapping_with("LIVE_EXECUTION", _canonical(oracle)))
        body = world.body()
        assert body["live_mode"] is oracle
        assert body["meta"]["mode"] == _meta_mode(oracle)


# ---------------------------------------------------------------------------
# POST /api/sell_account
# ---------------------------------------------------------------------------


def _sell_env(value) -> dict:
    return {
        **mapping_with("LIVE_EXECUTION", value),
        "COMPOSER_KEY_ID": "test-key-id",
        "COMPOSER_SECRET": "test-secret",
        "DISCORD_WEBHOOK_URL": "https://discord.test/webhook",
    }


class TestSellAccountFlagParse:
    @pytest.mark.parametrize("value", ALL_CASES, ids=case_id)
    def test_sell_account_matches_canonical_spelling_for_every_spelling(
        self, monkeypatch, client, no_network, value
    ):
        oracle = ref_flag(value, "False")
        got = sell_account_drive(monkeypatch, client, _sell_env(value))
        want = sell_account_drive(monkeypatch, client, _sell_env(_canonical(oracle)))
        assert got == want
        assert got["status"] == 200
        assert (got["body"].get("executed") is True) is oracle
        assert (got["body"].get("dry_run") is True) is (not oracle)
        assert len(got["threads"]) == (1 if oracle else 0)
        # URLs, payloads: the only outgoing request is the Discord audit post, live or not.
        assert [p["url"] for p in got["posts"]] == ["https://discord.test/webhook"]
        if oracle:
            assert got["threads"][0]["args"][3] is True

    def test_wrong_confirm_phrase_refuses_before_the_flag_is_read_and_sends_nothing(
        self, monkeypatch, client, no_network
    ):
        install_env(monkeypatch, _sell_env("True"))
        posts: list = []
        monkeypatch.setattr(app_module.requests, "post", lambda *a, **k: posts.append(a))
        response = client.post(
            "/api/sell_account",
            json={
                "account_id": "ACC-LIVE-001",
                "confirm_account_id": "ACC-LIVE-001",
                "confirm_phrase": "liquidate",
            },
        )
        assert response.status_code == 400
        assert posts == []

    def test_missing_credentials_refuse_and_send_no_request(self, monkeypatch, client, no_network):
        mapping = _sell_env("True")
        del mapping["COMPOSER_KEY_ID"]
        install_env(monkeypatch, mapping)
        posts: list = []
        monkeypatch.setattr(app_module.requests, "post", lambda *a, **k: posts.append(a))
        response = client.post(
            "/api/sell_account",
            json={
                "account_id": "ACC-LIVE-001",
                "confirm_account_id": "ACC-LIVE-001",
                "confirm_phrase": "LIQUIDATE",
            },
        )
        assert response.status_code == 400
        assert posts == []


# ---------------------------------------------------------------------------
# GET /api/symphony-settings/<name>
# ---------------------------------------------------------------------------


class TestSymphonySettingsFlagParse:
    @pytest.mark.parametrize("value", ALL_CASES, ids=case_id)
    def test_symphony_settings_global_live_matches_reference_for_every_spelling(
        self, monkeypatch, client, no_network, value
    ):
        database.save_symphony_strategy("Alpha Grove", {"STOP_TRIGGER_LEVEL": 5.0}, [])
        install_env(monkeypatch, mapping_with("LIVE_EXECUTION", value))
        response = client.get("/api/symphony-settings/alpha-grove")
        assert response.status_code == 200
        body = response.get_json()
        assert body["global_live"] is ref_flag(value, "False")
        install_env(
            monkeypatch, mapping_with("LIVE_EXECUTION", _canonical(ref_flag(value, "False")))
        )
        assert client.get("/api/symphony-settings/alpha-grove").get_json() == body


# ---------------------------------------------------------------------------
# POST /api/sleeves/<id>/arm-live
# ---------------------------------------------------------------------------


def _arm_env(value) -> dict:
    return {
        "ALPACA_LIVE_KEY": "test-live-key",
        "ALPACA_LIVE_SECRET": "test-live-secret",
        **mapping_with("SLEEVE_LIVE_EXECUTION", value),
    }


class TestArmLiveFlagParse:
    @pytest.mark.parametrize("value", ALL_CASES + (BARE_KEY,), ids=case_id)
    def test_arm_live_result_and_stored_status_match_reference(
        self, monkeypatch, client, no_network, value
    ):
        # the site reads (value or "False"): a bare key falls to the default, so it is false.
        oracle = ref_flag("False" if value is BARE_KEY else value, "False")
        got = arm_live_drive(monkeypatch, client, _arm_env(value), f"arm-flag-{case_id(value)}")
        assert got["before"] != "LIVE"
        if oracle:
            assert got["status"] == 200
            assert got["after"] == "LIVE"
        else:
            assert got["status"] == 400
            assert "SLEEVE_LIVE_EXECUTION is not enabled" in (got["message"] or "")
            assert got["after"] == got["before"]


# ---------------------------------------------------------------------------
# a bare key (the mapping holds None)
# ---------------------------------------------------------------------------


class TestBareKeyOutcomes:
    """Each unguarded site answers as it did at the reference (a raise stays a raise)."""

    @pytest.mark.parametrize("site", BARE_KEY_SITES)
    def test_bare_key_outcome_equals_reference(
        self, monkeypatch, client, tmp_path, no_network, site
    ):
        got = bare_key_outcome(site, monkeypatch, client, tmp_path)
        assert got == _bare_record()[site]

    def test_every_bare_key_site_has_a_recorded_outcome(self):
        assert set(_bare_record()) == set(BARE_KEY_SITES)
