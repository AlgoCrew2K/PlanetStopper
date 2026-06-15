"""
RED — dash-polish TASK 2: F821 in get_history_summary causes silent name-map failure.

THE BUG (static analysis + runtime verification):
  analytics.py get_history_summary() (line 1530) calls `database.load_state()` at
  line 1604 inside an inner try/except Exception: pass block.  The name `database`
  is NOT imported at module scope in analytics.py (only `import database as _db`
  exists inside two other functions at lines 528 and 586).

  At runtime, `database.load_state()` raises NameError: name 'database' is not defined.
  The except clause catches it silently, _name_map stays {}, and every todays_exits
  entry gets symphony_name = symphony_id (raw key, not the human display name).

  The /api/history/<days> route never 500s — it returns a silently-degraded response
  with an empty name map.  Unit tests that mock out the whole analytics module pass
  because the mock never runs the real function.

THE FIX (expected): add `import database` at module scope in analytics.py (or qualify
the call as `_db.load_state()` inside get_history_summary, consistent with the
`import database as _db` pattern used at lines 528 and 586).

ACCEPTANCE CRITERIA:

  AC-FM.1  analytics module has a `database` attribute (import exists at module scope).
           This is the existence check that prevents the NameError from hiding behind
           the except clause.

  AC-FM.2  GET /api/history/<days> with a real today's post_mortem file containing a
           trigger whose symphony_id maps to a human name in live state MUST populate
           todays_exits[*].symphony_name with the human name, NOT the raw ID.

  AC-FM.3  The route must return HTTP 200 AND todays_exits must be non-empty when a
           valid today's file exists.

  AC-FM.4  The inner exception swallow in get_history_summary must not mask a
           NameError on `database`.  If `database` is undefined, the test scaffolding
           that patches `analytics.database` will patch a non-existent name and Python
           will raise AttributeError — confirming the import is missing.

Test strategy (per feedback_route_level_red_for_mocked_analytics_fns):
  - Hit GET /api/history/<days> with the REAL analytics module (do NOT mock analytics).
  - Mock only DB/network: patch `analytics.database.load_state` to return a controlled
    state dict so no live SQLite is required.
  - Patch `analytics.database` to exist so the test does not fail for the wrong reason
    once the fix is applied — but AC-FM.1 asserts it must be reachable WITHOUT patching.
  - The fix must land in analytics.py (not in the test), so the test reflects what
    SHOULD be true: analytics.database is a real attribute, not a mock-injected one.
"""

from __future__ import annotations

import json
from datetime import date as _date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import analytics as analytics_module


# ---------------------------------------------------------------------------
# AC-FM.1 — analytics module must have a database attribute at module scope
# ---------------------------------------------------------------------------


def test_analytics_module_has_database_attribute():
    """
    AC-FM.1: analytics module must expose a `database` attribute so
    `analytics.database.load_state()` is reachable without a NameError.

    Current code: `import database` is absent at module scope in analytics.py.
    The only database imports are local `import database as _db` at lines 528, 586.

    This test is RED on current code: hasattr(analytics_module, 'database') == False.
    """
    assert hasattr(analytics_module, "database"), (
        "AC-FM.1 FAIL: analytics module has no 'database' attribute. "
        "`import database` is missing at module scope in analytics.py. "
        "get_history_summary() calls `database.load_state()` at line 1604 — "
        "without the import, a NameError is silently swallowed by the inner "
        "except clause, leaving the symphony name-map empty. "
        "Fix: add `import database` at module scope in analytics.py."
    )


# ---------------------------------------------------------------------------
# AC-FM.2/AC-FM.3 — route-level: name-map is populated with real analytics
# ---------------------------------------------------------------------------


@pytest.fixture
def client_real_analytics():
    """Flask test client with the REAL analytics module — only DB/network are mocked.

    This fixture intentionally does NOT mock analytics to catch the
    mocked-module-fn failure mode (feedback_route_level_red_for_mocked_analytics_fns):
    a test that mocks the whole analytics module passes even when get_history_summary
    raises a NameError, because the mock never runs the real function.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_history_route_populates_name_map_from_live_state(
    client_real_analytics, tmp_path, monkeypatch
):
    """
    AC-FM.2/AC-FM.3: GET /api/history/<days> must populate todays_exits[*].symphony_name
    with the human name from live state — not the raw symphony_id.

    Setup:
      - A today's post_mortem file with one trigger whose symphony_id = 'sym-abc'.
      - analytics.database.load_state returns {'sym-abc': {'id': 'sym-abc', 'name': 'My Symphony'}}.
      - Expectation: todays_exits[0].symphony_name == 'My Symphony'.

    Current code: database is undefined in analytics.py → NameError caught silently →
    _name_map stays {} → symphony_name = 'sym-abc' (the raw ID, not the human name).
    This test FAILS on current code because symphony_name != 'My Symphony'.
    """
    today_str = _date.today().isoformat()
    today_post_mortem = {
        "triggers": [
            {
                "symphony_id": "sym-abc",
                "symphony_name": "sym-abc",
                "exit_reason": "Stop",
                "saved_pct_guard_alpha": 0.5,
                "saved_dollars": 100.0,
            }
        ]
    }
    (tmp_path / f"post_mortem_{today_str}.json").write_text(json.dumps(today_post_mortem))

    # Pivot cwd so get_history_summary sees the fixture file.
    monkeypatch.chdir(tmp_path)

    # Stub database at the analytics module level — the REAL analytics code
    # must call analytics.database.load_state (not a global).
    # If analytics.database doesn't exist, patch.object will raise AttributeError
    # (which also fails this test, correctly — AC-FM.4).
    fake_db = MagicMock()
    fake_db.load_state.return_value = {"sym-abc": {"id": "sym-abc", "name": "My Symphony"}}

    with patch.object(analytics_module, "database", fake_db, create=False):
        resp = client_real_analytics.get("/api/history/30")

    assert resp.status_code == 200, (
        f"AC-FM.3 FAIL: /api/history/30 returned {resp.status_code}. "
        "Route must return 200 even when a today's post_mortem file exists."
    )
    body = resp.get_json()
    assert body is not None, "Response must be valid JSON"

    todays_exits = body.get("todays_exits", [])
    assert len(todays_exits) >= 1, (
        "AC-FM.3 FAIL: todays_exits must be non-empty when a valid today's "
        f"post_mortem file exists (date={today_str}). Got: {todays_exits!r}"
    )

    symphony_name = todays_exits[0].get("symphony_name")
    assert symphony_name == "My Symphony", (
        f"AC-FM.2 FAIL: todays_exits[0].symphony_name = {symphony_name!r}; "
        "expected 'My Symphony' (the human name from live state). "
        "Actual ID 'sym-abc' means the name-map lookup silently failed — "
        "the NameError on `database` was swallowed by the inner except clause."
    )


def test_history_route_name_map_fallback_to_id_when_state_empty(
    client_real_analytics, tmp_path, monkeypatch
):
    """
    When live state returns {} (no symphonies), todays_exits symphony_name must
    fall back to the raw symphony_id value from the post_mortem file — not None,
    not an empty string.

    This is a separate invariant from AC-FM.2: the fallback path must also work
    after the fix.
    """
    today_str = _date.today().isoformat()
    today_post_mortem = {
        "triggers": [
            {
                "symphony_id": "sym-xyz",
                "symphony_name": "sym-xyz",
                "exit_reason": "TP",
                "saved_pct_guard_alpha": 0.3,
                "saved_dollars": 60.0,
            }
        ]
    }
    (tmp_path / f"post_mortem_{today_str}.json").write_text(json.dumps(today_post_mortem))
    monkeypatch.chdir(tmp_path)

    fake_db = MagicMock()
    fake_db.load_state.return_value = {}  # empty — no symphonies in state

    with patch.object(analytics_module, "database", fake_db, create=False):
        resp = client_real_analytics.get("/api/history/30")

    assert resp.status_code == 200
    body = resp.get_json()
    todays_exits = body.get("todays_exits", [])
    assert len(todays_exits) >= 1, (
        "todays_exits must be non-empty when a valid today's post_mortem exists"
    )
    symphony_name = todays_exits[0].get("symphony_name")
    # When state is empty, name falls back to symphony_name or symphony_id from file.
    assert symphony_name is not None and symphony_name != "", (
        f"FALLBACK FAIL: todays_exits[0].symphony_name is {symphony_name!r}. "
        "When the name-map is empty, the fallback must be the raw ID/name from the "
        "post_mortem file, not None or empty string."
    )


# ---------------------------------------------------------------------------
# AC-FM.4 — patch.object raises AttributeError if attribute doesn't exist
#            (structural guard: test can't pass for wrong reason)
# ---------------------------------------------------------------------------


def test_analytics_database_attr_patchable_without_create():
    """
    AC-FM.4: patch.object(analytics_module, 'database', ..., create=False) must
    succeed (analytics.database exists) — not raise AttributeError.

    If this test PASSES, it confirms the module attribute is present.
    If this test raises AttributeError, the import is still missing.

    Note: create=False is the strict mode — patch.object refuses to inject a new
    attribute; it must already exist.  This prevents the other tests from passing
    with `create=True` while the real NameError persists in production.
    """
    try:
        fake_db = MagicMock()
        with patch.object(analytics_module, "database", fake_db, create=False):
            # Just confirm the patch applies and reverts cleanly.
            assert analytics_module.database is fake_db
        # After context exit, original is restored.
    except AttributeError as exc:
        pytest.fail(
            f"AC-FM.4 FAIL: patch.object raised AttributeError: {exc}. "
            "analytics.database does not exist as a module-scope attribute. "
            "Fix: add `import database` at module scope in analytics.py."
        )
