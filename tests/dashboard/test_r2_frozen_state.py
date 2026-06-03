"""
RED tests for R2 — HIGH-2 frozen /api/state state-field UX fix.

Bug: app.py:239 sets "state": snapshot.get("data_as_of") — a date string like "16:00 ET".
JS at templates/index.html:910 does Object.keys(stateObj).filter(k => k !== 'date'),
which on a string "16:00 ET" returns ["0","1",...,"4"] (digit-string fake symphony keys),
producing "Tracked: 5 / Armed: 0 / Triggered: 0" with a blank symphony table on every
weekend / after-hours dashboard load.

Fix: restore "state" to a real dict keyed by symphony ID, derived from the snapshot's
accounts_map (flatten each account's symphony list by id) so that Object.keys(state)
yields actual symphony IDs, not digit strings from iterating a date string.

Acceptance criteria tested (from team-lead brief):
  AC-1  : closed_frozen + snapshot → state is a real OBJECT, not a string
  AC-2  : frozen_at ISO timestamp field present and non-null
  AC-3  : frozen state includes snapshot symphony roster as state keys/values
  AC-4  : Object.keys(state) yields symphony-ID-shaped strings, not digit strings
  AC-5a : closed_frozen + no snapshot → state returns {} (empty object, not null, not string)
  AC-5b : closed_frozen + no snapshot → notice field present
  AC-6  : pre_market path serves real state object (same as closed_frozen)
  AC-7  : pre_market + snapshot → state object with symphony IDs
  AC-8  : market_state == "open" preserves current live behavior (no regression)
  AC-9  : JS simulation — Object.keys on frozen response state yields symphony-ID strings
  AC-10 : EOD snapshot state_data subset is FULL — accounts_map contains real symphony dicts

Fixture provenance:
  tests/fixtures/dashboard/frozen_state/frozen_state_with_symphonies.json
  tests/fixtures/dashboard/frozen_state/frozen_state_no_snapshot.json
  tests/fixtures/dashboard/frozen_state/frozen_state_pre_market.json
  (Hand-authored, schema-derived from alpha_bot_execution.py:727-750 and app.py:404-418)

PA-18: all fixtures under tests/fixtures/dashboard/frozen_state/ with _provenance comments.
PA-19: explicit APPROVE from quant-code-reviewer required before merge.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "dashboard" / "frozen_state"
)


def _load(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Flask test-client fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def flask_client():
    """Flask test client with minimal stubs.  No live DB; no live network."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with (
        patch.object(app_module, "analytics") as mock_analytics,
        patch.object(app_module, "schedule"),
    ):
        mock_analytics.get_portfolio_today_change.return_value = None
        mock_analytics.get_portfolio_cumulative_return.return_value = None
        mock_analytics.get_portfolio_max_drawdown.return_value = None
        mock_analytics.get_symphony_today_change.return_value = {"if_held": None, "dry_run": None}
        mock_analytics.get_symphony_cumulative_return.return_value = {"if_held": None, "dry_run": None}
        mock_analytics.get_symphony_max_drawdown.return_value = {"if_held": None, "dry_run": None}
        with app_module.app.test_client() as client:
            yield client, app_module


def _patch_frozen_db(app_module, bot_state, monkeypatch, market_state="closed_frozen"):
    """Return a context-manager stack that patches DB + market_state for frozen paths."""
    mock_db = MagicMock()
    mock_db.load_state.return_value = bot_state
    mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
    mock_db.get_triggers.return_value = []
    mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
    mock_db.read_fleet_alert.return_value = None
    return mock_db


# ===========================================================================
# AC-1 + AC-2 + AC-3: closed_frozen with snapshot → state is object, frozen_at set
# ===========================================================================

class TestFrozenStateIsObjectNotString:
    """
    Core regression: app.py:239 must NOT set state to a date string.
    When a snapshot exists, state must be a dict keyed by symphony IDs.
    """

    def test_frozen_state_is_dict_not_string_when_snapshot_exists(
        self, flask_client, monkeypatch
    ):
        """AC-1: closed_frozen + snapshot → /api/state.state is a dict, not a string."""
        client, app_module = flask_client
        fx = _load("frozen_state_with_symphonies")

        bot_state = {"date": "2026-05-16", "last_market_close_snapshot": fx["snapshot_input"]}

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )

            resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()
        state = body.get("state")

        assert state is not None, (
            "/api/state.state is missing entirely from frozen response. "
            "Implementer must set 'state' to the symphony roster object."
        )
        assert isinstance(state, dict), (
            f"/api/state.state must be a dict (symphony roster), got {type(state).__name__!r}. "
            f"Current bug: app.py:239 returns snapshot.get('data_as_of') — a string like '16:00 ET'. "
            f"Actual value: {state!r}"
        )

    def test_frozen_at_is_nonnull_when_snapshot_exists(
        self, flask_client, monkeypatch
    ):
        """AC-2: frozen_at must be a non-null string (ISO timestamp) when snapshot exists."""
        client, app_module = flask_client
        fx = _load("frozen_state_with_symphonies")

        bot_state = {"date": "2026-05-16", "last_market_close_snapshot": fx["snapshot_input"]}

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )

            resp = client.get("/api/state")

        body = resp.get_json()
        frozen_at = body.get("frozen_at")
        assert frozen_at is not None, (
            "frozen_at must be non-null when serving a frozen snapshot. "
            f"Got frozen_at={frozen_at!r}"
        )
        assert isinstance(frozen_at, str), (
            f"frozen_at must be a string timestamp, got {type(frozen_at).__name__!r}"
        )

    def test_frozen_state_contains_snapshot_symphony_roster_as_keys(
        self, flask_client, monkeypatch
    ):
        """AC-3: state dict must contain the snapshot's symphony IDs as keys."""
        client, app_module = flask_client
        fx = _load("frozen_state_with_symphonies")

        bot_state = {"date": "2026-05-16", "last_market_close_snapshot": fx["snapshot_input"]}
        expected_ids = set(fx["expected"]["state_keys_are_symphony_ids"])

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )

            resp = client.get("/api/state")

        body = resp.get_json()
        state = body.get("state", {})

        assert isinstance(state, dict), (
            f"state must be a dict to have symphony ID keys, got {type(state).__name__!r}"
        )
        actual_sym_keys = {k for k in state.keys() if k != "date"}
        missing = expected_ids - actual_sym_keys
        assert not missing, (
            f"Frozen state is missing expected symphony IDs: {missing}. "
            f"state keys present (non-date): {actual_sym_keys}. "
            "Implementer must flatten accounts_map into a symphony-keyed dict."
        )


# ===========================================================================
# AC-4: JS simulation — Object.keys must yield symphony ID strings, not digits
# ===========================================================================

class TestObjectKeysYieldSymphonyIds:
    """
    AC-4 + AC-9: Python simulation of the JS Object.keys(state) pattern.
    When state is a date string, dict(enumerate("16:00 ET")) produces digit keys.
    After the fix, keys must be symphony-ID-shaped strings.
    """

    def test_object_keys_on_frozen_state_yields_symphony_id_strings_not_digit_strings(
        self, flask_client, monkeypatch
    ):
        """
        AC-4 + AC-9: Python analogue of JS `Object.keys(state).filter(k => k !== 'date')`.
        Result must be symphony-ID-shaped strings, NOT single-character digit strings
        from iterating a date/time string like '16:00 ET'.
        """
        client, app_module = flask_client
        fx = _load("frozen_state_with_symphonies")

        bot_state = {"date": "2026-05-16", "last_market_close_snapshot": fx["snapshot_input"]}

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )

            resp = client.get("/api/state")

        body = resp.get_json()
        state = body.get("state")

        assert isinstance(state, dict), (
            f"state must be a dict for Object.keys() to work correctly. Got {type(state).__name__!r}."
        )

        # Python analogue of JS: Object.keys(state).filter(k => k !== 'date')
        sym_keys = [k for k in state.keys() if k != "date"]

        # Every key must NOT be a single digit (the symptom of iterating a date string)
        digit_keys = [k for k in sym_keys if k.isdigit()]
        assert not digit_keys, (
            f"Object.keys(state) returned digit-string keys: {digit_keys}. "
            "This is the bug: state is a string being iterated by character index. "
            "Fix app.py:239 to return a real symphony-keyed dict."
        )

        # Every key must look like a symphony ID (non-empty string, not a single character)
        assert all(len(k) > 1 for k in sym_keys), (
            f"Some state keys are suspiciously short (single chars from string iteration): "
            f"{[k for k in sym_keys if len(k) <= 1]}. Expected multi-character symphony IDs."
        )

        # Must have at least the expected symphony IDs
        expected_ids = set(fx["expected"]["state_keys_are_symphony_ids"])
        actual_non_date = set(sym_keys)
        assert expected_ids.issubset(actual_non_date), (
            f"Expected symphony IDs {expected_ids} not found in Object.keys(state) result "
            f"{actual_non_date}."
        )


# ===========================================================================
# AC-5: closed_frozen + no snapshot → state={}, notice present
# ===========================================================================

class TestFrozenStateNoSnapshotReturnsEmptyObject:
    """
    AC-5: fresh deploy (no snapshot) must return state={} not null, not string.
    AC-5b: notice field must be present.
    """

    def test_fresh_deploy_state_is_empty_dict_not_null_not_string(
        self, flask_client, monkeypatch
    ):
        """AC-5a: no snapshot + closed_frozen → state is {} (empty dict)."""
        client, app_module = flask_client
        fx = _load("frozen_state_no_snapshot")

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = {}
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )

            resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()
        state = body.get("state", "MISSING")

        assert state != "MISSING", "/api/state response must include 'state' key even on fresh deploy"
        assert state is not None, (
            "/api/state.state must be {} (empty dict), not null/None, on fresh deploy. "
            f"Got: {state!r}"
        )
        assert isinstance(state, dict), (
            f"/api/state.state must be an empty dict on fresh deploy, got {type(state).__name__!r}. "
            f"Value: {state!r}"
        )
        assert state == fx["expected"]["state"], (
            f"/api/state.state must be empty dict {{}}, got {state!r}"
        )

    def test_fresh_deploy_notice_field_is_present(
        self, flask_client, monkeypatch
    ):
        """AC-5b: no snapshot + closed_frozen → notice field present with descriptive message."""
        client, app_module = flask_client
        fx = _load("frozen_state_no_snapshot")

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = {}
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )

            resp = client.get("/api/state")

        body = resp.get_json()
        notice = body.get("notice")

        assert notice is not None and notice != "", (
            "notice field must be a non-empty string on fresh deploy. "
            f"Got notice={notice!r}. AC-DM.3.4 requires this field."
        )
        assert fx["expected"]["notice_contains"] in notice, (
            f"notice must contain '{fx['expected']['notice_contains']}'. "
            f"Got: {notice!r}"
        )


# ===========================================================================
# AC-6 + AC-7: pre_market path behaves identically to closed_frozen
# ===========================================================================

class TestPreMarketServesFrozenStateObject:
    """
    AC-6 + AC-7: market_state='pre_market' with snapshot → same real-object behavior.
    """

    def test_pre_market_state_is_object_not_string(
        self, flask_client, monkeypatch
    ):
        """AC-6: pre_market + snapshot → state is a dict, not a string."""
        client, app_module = flask_client
        fx = _load("frozen_state_pre_market")

        bot_state = {"date": "2026-05-16", "last_market_close_snapshot": fx["snapshot_input"]}

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "pre_market", raising=False
            )

            resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()
        state = body.get("state")

        assert body.get("market_state") == "pre_market", (
            f"Expected market_state='pre_market', got {body.get('market_state')!r}"
        )
        assert isinstance(state, dict), (
            f"pre_market state must be a dict, got {type(state).__name__!r}. "
            f"Value: {state!r}. pre_market path shares the same fix as closed_frozen."
        )

    def test_pre_market_state_contains_expected_symphony_ids(
        self, flask_client, monkeypatch
    ):
        """AC-7: pre_market + snapshot → state keys are the snapshot's symphony IDs."""
        client, app_module = flask_client
        fx = _load("frozen_state_pre_market")

        bot_state = {"date": "2026-05-16", "last_market_close_snapshot": fx["snapshot_input"]}
        expected_ids = set(fx["expected"]["state_keys_are_symphony_ids"])

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "pre_market", raising=False
            )

            resp = client.get("/api/state")

        body = resp.get_json()
        state = body.get("state", {})
        assert isinstance(state, dict), (
            f"state must be a dict, got {type(state).__name__!r}"
        )
        actual_sym_keys = {k for k in state.keys() if k != "date"}
        missing = expected_ids - actual_sym_keys
        assert not missing, (
            f"pre_market frozen state missing expected symphony IDs: {missing}. "
            f"Got keys: {actual_sym_keys}"
        )


# ===========================================================================
# AC-8: market_state='open' preserves current live behavior (no regression)
# ===========================================================================

class TestOpenMarketPreservesLiveBehavior:
    """
    AC-8: The R2 fix must not break the live (open) path.
    state must still be the live bot_state dict when market is open.
    """

    def test_open_market_state_field_is_live_bot_state(
        self, flask_client, monkeypatch
    ):
        """AC-8: market_state='open' → state is live bot_state (with symphony dicts), frozen_at=null."""
        client, app_module = flask_client

        live_state = {
            "date": "2026-05-16",
            "sym-live": {
                "name": "Live Symphony",
                "account": "ACC-INDIVIDUAL",
                "current_return": 3.1,
                "armed": True,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
            },
        }

        with (
            patch.object(app_module, "database") as mock_db,
            patch.object(app_module, "dotenv_values", return_value={}),
            patch.object(app_module, "render_template", return_value=""),
        ):
            mock_db.load_state.return_value = dict(live_state)
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "open", raising=False
            )

            resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("market_state") == "open"
        assert body.get("frozen_at") is None, (
            "frozen_at must be null when market is open"
        )
        state = body.get("state")
        assert isinstance(state, dict), (
            f"Live path state must still be a dict. Got {type(state).__name__!r}"
        )
        assert "sym-live" in state, (
            f"Live path state must contain the live symphony key 'sym-live'. "
            f"Keys: {list(state.keys())}"
        )


# ===========================================================================
# AC-10: EOD snapshot accounts_map contains full symphony dicts (not just IDs)
# ===========================================================================

class TestSnapshotAccountsMapContainsFullSymphonyDicts:
    """
    AC-10: The snapshot's accounts_map must contain real symphony data dicts
    (with 'id', 'name', 'account', 'current_return', arm flags), not empty lists
    or stub records. If accounts_map is populated with full dicts, the frozen
    state reconstruction can derive the symphony roster.
    """

    def test_snapshot_accounts_map_entries_are_full_symphony_dicts(self):
        """
        AC-10: Each entry in snapshot.accounts_map[account_id] must be a dict
        with at least the 'id' key so the frozen state can be keyed by symphony ID.
        This verifies the fixture shape is correct and that the implementation
        contract is checkable.
        """
        fx = _load("frozen_state_with_symphonies")
        accounts_map = fx["snapshot_input"]["accounts_map"]

        assert accounts_map, "accounts_map must not be empty in a populated snapshot"

        all_entries = []
        for account_id, entries in accounts_map.items():
            assert isinstance(entries, list), (
                f"accounts_map[{account_id!r}] must be a list of symphony dicts, "
                f"got {type(entries).__name__!r}"
            )
            for entry in entries:
                assert isinstance(entry, dict), (
                    f"Each entry in accounts_map[{account_id!r}] must be a dict, "
                    f"got {type(entry).__name__!r}"
                )
                assert "id" in entry, (
                    f"Each symphony entry must have an 'id' key for state reconstruction. "
                    f"Entry: {entry}"
                )
            all_entries.extend(entries)

        # All expected symphony IDs must be present in the flattened entries
        expected_ids = set(fx["expected"]["state_keys_are_symphony_ids"])
        actual_ids = {e["id"] for e in all_entries}
        assert expected_ids == actual_ids, (
            f"Flattened accounts_map symphony IDs {actual_ids} do not match "
            f"expected IDs {expected_ids}. Snapshot must capture the full symphony roster."
        )

    def test_frozen_state_reconstruction_flattens_accounts_map_correctly(
        self, flask_client, monkeypatch
    ):
        """
        AC-10 (end-to-end): The /api/state frozen path must reconstruct state
        by flattening accounts_map — each symphony dict keyed by its 'id'.
        Verify that each value in state is a dict (not a string or number).
        """
        client, app_module = flask_client
        fx = _load("frozen_state_with_symphonies")

        bot_state = {"date": "2026-05-16", "last_market_close_snapshot": fx["snapshot_input"]}

        with patch.object(app_module, "database") as mock_db:
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None
            monkeypatch.setattr(
                app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
            )

            resp = client.get("/api/state")

        body = resp.get_json()
        state = body.get("state", {})

        assert isinstance(state, dict), (
            f"state must be a dict for reconstruction to have worked. "
            f"Got {type(state).__name__!r}: {state!r}"
        )

        sym_entries = {k: v for k, v in state.items() if k != "date"}
        assert sym_entries, (
            "Frozen state must have at least one symphony entry after reconstruction."
        )

        for sym_id, sym_data in sym_entries.items():
            assert isinstance(sym_data, dict), (
                f"state[{sym_id!r}] must be a symphony data dict, "
                f"got {type(sym_data).__name__!r}: {sym_data!r}. "
                "Reconstruction must flatten accounts_map[account][n] by 'id', "
                "not return string characters."
            )
