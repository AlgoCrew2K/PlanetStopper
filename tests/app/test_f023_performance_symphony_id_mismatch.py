"""
RED tests for F-023 — Performance-tab per-symphony ID/name mismatch.

Root cause (feature-plans/fix-f023-perf-view.md): GET /api/performance/symphonies
returns human-readable NAMES used as both the picker's label AND its value. The
picked NAME is then sent as symphony_id into analytics.get_symphony_bot_and_held_
daily_returns -> "SELECT ... FROM shadow_history WHERE symphony_id = ?", but that
column stores only HASH ids -- so the query matches zero rows every time, and a
whole operator capability masquerades as an honest "Insufficient history" empty
state for every one of the 11 symphonies.

Fix under test:
  AC-1: GET /api/performance/symphonies returns [{id, name}] objects -- id is the
        hash key from database.load_state() (bot_state), name is the display label.
  AC-2: static/performance.js's picker uses id as the option value, name as label.
  AC-3: the SAME id the picker endpoint returns actually yields non-zero
        observations when fed into GET /api/performance?scope=symphony&symphony_id=.
  AC-4: a genuine no-data symphony (real hash, <threshold rows) is distinguished
        from a totally unrecognized symphony_id via a new `symphony_id_recognized`
        boolean field -- and static/performance.js's banner must surface that
        distinction visibly (never masquerade as "Insufficient history").
  AC-5: static/ai_advisor.js's picker (the OTHER consumer) gets the same {id,name}
        update.
  AC-6: scope=aggregate stays byte-unchanged; symphony_id_recognized never leaks
        into aggregate responses; analytics.get_symphony_bot_and_held_daily_returns
        itself is untouched (proven indirectly -- its parameterized WHERE clause
        already matches by hash, see the AC-3 end-to-end test below).

No producer-computed values are hardcoded -- every expected value derives from a
fixture this test module builds itself (project rule feedback_no_hardcoded_test_values).
Real (small, on-disk, per-test) shadow_history DBs are seeded via the documented
analytics.DB_FILE test-time override seam (analytics.py:551-560) so AC-3/AC-4 prove
the actual SQL matching behavior, not just route plumbing. -n0 only; no live API,
no live Discord, no production DB (per-test tmp_path files only).
"""

from __future__ import annotations

import pathlib
import re
import sqlite3

import pytest

import app as app_module

_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"

# Mirrors migrations/008_shadow_history.sql -- the live schema shadow_history rows
# are read against. Kept minimal (no indexes needed for a tiny per-test fixture DB).
_SHADOW_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT    NOT NULL,
    ts_et           TEXT    NOT NULL,
    trading_day     TEXT    NOT NULL,
    symphony_id     TEXT    NOT NULL,
    account_id      TEXT,
    cycle_id        TEXT,
    current_return  REAL    NOT NULL,
    shadow_return   REAL    NOT NULL,
    is_post_trigger INTEGER NOT NULL DEFAULT 0,
    trigger_id      INTEGER,
    math_mode       TEXT    NOT NULL DEFAULT 'per_symphony'
)
"""


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Flask test client bound to the in-process app instance (no port bound)."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _get_perf(client, **params):
    """GET /api/performance with dict-encoded query params (safe URL-encoding
    for values containing spaces/quotes/etc, needed by the AC-3 negative-control
    and security tests below)."""
    return client.get("/api/performance", query_string=params)


def _seed_shadow_history_db(path: pathlib.Path, symphony_id: str, n_rows: int) -> None:
    """Create a small on-disk shadow_history DB with `n_rows` rows for
    `symphony_id`, one per (fixture-controlled) trading day. `n_rows=0` creates
    a schema-only (genuinely empty) table -- used for the AC-4 "real hash, zero
    rows" and security-payload tests, where `symphony_id` is unused.
    """
    conn = sqlite3.connect(str(path))
    conn.execute(_SHADOW_HISTORY_SCHEMA)
    for i in range(n_rows):
        trading_day = f"2026-05-{i + 1:02d}"
        conn.execute(
            "INSERT INTO shadow_history "
            "(ts_utc, ts_et, trading_day, symphony_id, current_return, shadow_return) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"{trading_day}T20:00:00Z",
                f"{trading_day}T16:00:00-04:00",
                trading_day,
                symphony_id,
                0.001 * (i + 1),
                0.0015 * (i + 1),
            ),
        )
    conn.commit()
    conn.close()


def _extract_function_body(src: str, func_signature: str, end_marker: str, file_label: str) -> str:
    """Slice a JS source string from `func_signature` to the next `end_marker`
    landmark. Simple substring-based scoping (not brace-balancing) -- robust
    enough for these files' flat top-level function layout and gives a clear,
    named failure if the surrounding structure changes.
    """
    start = src.find(func_signature)
    assert start != -1, f"{func_signature!r} not found in {file_label}"
    end = src.find(end_marker, start)
    assert end != -1, f"{end_marker!r} not found after {func_signature!r} in {file_label}"
    return src[start:end]


# ---------------------------------------------------------------------------
# AC-1 -- GET /api/performance/symphonies returns [{id, name}], not bare strings
# ---------------------------------------------------------------------------


def test_symphonies_endpoint_returns_id_and_name_objects_not_bare_strings(client, monkeypatch):
    fake_state = {
        "a1b2c3-hash-alpha": {"name": "Sym Alpha", "current_value": 1000.0},
        "d4e5f6-hash-beta": {"name": "Sym Beta", "current_value": 2000.0},
    }
    monkeypatch.setattr(app_module.database, "load_state", lambda: fake_state)

    resp = client.get("/api/performance/symphonies")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "symphonies" in body
    symphonies = body["symphonies"]
    assert isinstance(symphonies, list)
    assert len(symphonies) == len(fake_state), (
        f"expected one entry per bot_state symphony ({len(fake_state)}), got {len(symphonies)}"
    )
    for entry in symphonies:
        assert isinstance(entry, dict), (
            f"expected an {{id, name}} object, got a bare value {entry!r} -- "
            "this is the F-023 defect: the endpoint must not return raw strings"
        )
        assert "id" in entry and "name" in entry

    ids = {e["id"] for e in symphonies}
    names = {e["name"] for e in symphonies}
    assert ids == set(fake_state.keys()), "id values must be the bot_state hash keys"
    assert names == {v["name"] for v in fake_state.values()}, "name values must be the bot_state display names"


def test_symphonies_endpoint_id_is_the_state_hash_key_name_is_the_display_label(client, monkeypatch):
    fake_state = {
        "a1b2c3-hash-alpha": {"name": "Sym Alpha"},
        "d4e5f6-hash-beta": {"name": "Sym Beta"},
    }
    monkeypatch.setattr(app_module.database, "load_state", lambda: fake_state)

    resp = client.get("/api/performance/symphonies")
    symphonies = resp.get_json()["symphonies"]
    # Non-emptiness asserted explicitly first so this test cannot pass vacuously
    # (an empty `symphonies` list would make every assertion in the loop below
    # trivially true without actually proving anything).
    assert len(symphonies) == len(fake_state), (
        f"expected {len(fake_state)} entries from bot_state, got {len(symphonies)}: {symphonies!r}"
    )
    for entry in symphonies:
        assert entry["id"] != entry["name"], (
            "id must be the hash key, not a duplicate of the display name "
            "(today's bug: the endpoint returns the name for BOTH)"
        )
        assert entry["id"] in fake_state, f"id {entry['id']!r} is not a real bot_state hash key"
        assert fake_state[entry["id"]]["name"] == entry["name"], (
            f"entry id {entry['id']!r} must map back to its OWN bot_state name, "
            f"got name={entry['name']!r}"
        )


def test_symphonies_endpoint_empty_bot_state_returns_empty_list_no_crash(client, monkeypatch):
    monkeypatch.setattr(app_module.database, "load_state", lambda: {})

    resp = client.get("/api/performance/symphonies")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["symphonies"] == []


def test_symphonies_endpoint_skips_malformed_bot_state_entries_without_crashing(client, monkeypatch):
    """Sufficiency-review addition (Red/Green/Revise): the new {id,name} list
    comprehension reads data["name"] per bot_state entry -- a malformed entry
    (non-dict value, or a dict missing "name") must be silently skipped, not
    crash the whole picker for every OTHER symphony in bot_state."""
    fake_state = {
        "hash-good": {"name": "Sym Good"},
        "hash-missing-name": {"current_value": 500.0},  # no "name" key
        "hash-not-a-dict": "not-a-dict-value",
    }
    monkeypatch.setattr(app_module.database, "load_state", lambda: fake_state)

    resp = client.get("/api/performance/symphonies")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["symphonies"] == [{"id": "hash-good", "name": "Sym Good"}], (
        f"malformed bot_state entries must be silently skipped, not crash or "
        f"leak into the picker list -- got {body['symphonies']!r}"
    )


def test_symphonies_endpoint_list_sorted_by_name(client, monkeypatch):
    fake_state = {
        "hash-zzz": {"name": "Zebra Strategy"},
        "hash-aaa": {"name": "Alpha Strategy"},
        "hash-mmm": {"name": "Midway Strategy"},
    }
    monkeypatch.setattr(app_module.database, "load_state", lambda: fake_state)

    resp = client.get("/api/performance/symphonies")
    names_in_order = [e["name"] for e in resp.get_json()["symphonies"]]
    assert names_in_order == sorted(names_in_order)
    assert names_in_order == ["Alpha Strategy", "Midway Strategy", "Zebra Strategy"]


# ---------------------------------------------------------------------------
# AC-2 -- static/performance.js picker: option value=id, label=name
# ---------------------------------------------------------------------------


def test_performance_js_symphony_picker_uses_id_as_value_and_name_as_label():
    src = (_STATIC_DIR / "performance.js").read_text(encoding="utf-8")
    body = _extract_function_body(src, "function loadSymphonies", "function wireSegControl", "performance.js")

    assert "sym.id" in body, (
        "loadSymphonies() must set the option value from sym.id (the new "
        "{id,name} shape) -- today it assigns the bare `sym` (a display NAME) "
        "to option.value, which is exactly the F-023 bug."
    )
    assert "sym.name" in body, "loadSymphonies() must set the option label/textContent from sym.name."
    assert not re.search(r"opt\.value\s*=\s*sym\s*;", body), (
        "loadSymphonies() still assigns the bare `sym` value to option.value -- must use sym.id."
    )
    assert not re.search(r"opt\.textContent\s*=\s*sym\s*;", body), (
        "loadSymphonies() still assigns the bare `sym` value to option.textContent -- must use sym.name."
    )


# ---------------------------------------------------------------------------
# AC-3 -- the picker endpoint's own id round-trips into a working query
# ---------------------------------------------------------------------------


def test_performance_symphony_id_from_picker_yields_nonzero_observations(client, monkeypatch, tmp_path):
    real_hash = "a1b2c3-real-hash-alpha"
    display_name = "Sym Real Alpha"
    n_rows = 15

    monkeypatch.setattr(app_module.database, "load_state", lambda: {real_hash: {"name": display_name}})

    shadow_db = tmp_path / "f023_shadow.db"
    _seed_shadow_history_db(shadow_db, real_hash, n_rows)
    monkeypatch.setattr(app_module.analytics, "DB_FILE", str(shadow_db))

    # Step 1: the picker endpoint returns the {id,name} pairs.
    picker_resp = client.get("/api/performance/symphonies")
    assert picker_resp.status_code == 200
    symphonies = picker_resp.get_json()["symphonies"]
    assert len(symphonies) == 1
    picker_id = symphonies[0]["id"]

    # Step 2: feeding that SAME id (not the name) into scope=symphony must
    # actually surface the seeded shadow_history rows -- this is the whole
    # F-023 capability, proven end-to-end.
    perf_resp = _get_perf(client, scope="symphony", symphony_id=picker_id)
    assert perf_resp.status_code == 200
    body = perf_resp.get_json()
    assert body["scope"] == "symphony"
    assert body["observation_count"] == n_rows, (
        f"the id the picker endpoint returned ({picker_id!r}) did not yield the "
        f"seeded {n_rows} shadow_history rows -- this is the F-023 defect: the "
        "picker endpoint's own output value doesn't work when fed back into the "
        "per-symphony query."
    )


def test_performance_symphony_name_instead_of_id_yields_zero_observations(client, monkeypatch, tmp_path):
    """Negative-control regression guard -- documents WHY the bug happened.
    Passes today AND after the fix: a display NAME (not a hash) legitimately
    matches zero shadow_history rows, because the column stores only hashes."""
    real_hash = "a1b2c3-real-hash-alpha"
    display_name = "Sym Real Alpha"

    monkeypatch.setattr(app_module.database, "load_state", lambda: {real_hash: {"name": display_name}})

    shadow_db = tmp_path / "f023_shadow_name_control.db"
    _seed_shadow_history_db(shadow_db, real_hash, 15)
    monkeypatch.setattr(app_module.analytics, "DB_FILE", str(shadow_db))

    resp = _get_perf(client, scope="symphony", symphony_id=display_name)
    assert resp.status_code == 200
    assert resp.get_json()["observation_count"] == 0


# ---------------------------------------------------------------------------
# AC-4 -- known-hash-zero-rows vs totally-unknown id are distinguished
# ---------------------------------------------------------------------------


def test_performance_symphony_known_hash_zero_rows_is_recognized_true(client, monkeypatch, tmp_path):
    known_hash = "known-hash-no-data-xyz"
    monkeypatch.setattr(app_module.database, "load_state", lambda: {known_hash: {"name": "Sym No Data"}})

    shadow_db = tmp_path / "f023_empty_shadow.db"
    _seed_shadow_history_db(shadow_db, known_hash, 0)
    monkeypatch.setattr(app_module.analytics, "DB_FILE", str(shadow_db))

    resp = _get_perf(client, scope="symphony", symphony_id=known_hash)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["observation_count"] == 0
    assert body["insufficient_history"] is True
    assert body["symphony_id_recognized"] is True, (
        "a genuine no-data symphony with a REAL known hash must be "
        "recognized=True -- this is the honest 'insufficient history' case, "
        "distinct from an unrecognized id (AC-4)"
    )


def test_performance_symphony_unknown_id_is_recognized_false(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module.database, "load_state", lambda: {"some-other-hash": {"name": "Sym Other"}})

    shadow_db = tmp_path / "f023_empty_shadow2.db"
    _seed_shadow_history_db(shadow_db, "unused", 0)
    monkeypatch.setattr(app_module.analytics, "DB_FILE", str(shadow_db))

    resp = _get_perf(client, scope="symphony", symphony_id="totally-unknown-id-not-in-state")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["observation_count"] == 0
    assert body["symphony_id_recognized"] is False, (
        "an id that is not a key in bot_state must be recognized=False, "
        "distinguishing it from a genuine no-data symphony (AC-4)"
    )


def test_performance_js_render_banner_branches_on_symphony_id_recognized():
    """AC-4 (UI half) -- source-presence check only (no jsdom in this suite).
    PM live-render-harness verifies the actual visual behavior; see the
    'Questions for User' section of .claude/tdd-handoff.md."""
    src = (_STATIC_DIR / "performance.js").read_text(encoding="utf-8")
    body = _extract_function_body(src, "function renderBanner", "function setHeadlineStat", "performance.js")

    assert "symphony_id_recognized" in body, (
        "renderBanner() must branch on payload.symphony_id_recognized so an "
        "unrecognized symphony_id surfaces as a DISTINCT message, never "
        "masquerading as the generic Insufficient-history empty state (AC-4). "
        "Today the banner text is static Jinja HTML and renderBanner() only "
        "toggles display -- it must now also set the banner's text dynamically."
    )
    assert "banner.innerHTML" in body or "banner.textContent" in body, (
        "renderBanner() must set the banner's text dynamically (innerHTML or "
        "textContent) to show a distinct message when symphony_id_recognized "
        "is false -- the existing static Jinja-baked text can't differ per state."
    )


# ---------------------------------------------------------------------------
# AC-5 -- static/ai_advisor.js picker gets the same {id,name} update
# ---------------------------------------------------------------------------


def test_ai_advisor_js_symphony_picker_uses_id_as_value_and_name_as_label():
    src = (_STATIC_DIR / "ai_advisor.js").read_text(encoding="utf-8")
    body = _extract_function_body(
        src, "function loadSymphonies", "document.addEventListener('DOMContentLoaded'", "ai_advisor.js"
    )

    assert "sym.id" in body, (
        "ai_advisor.js loadSymphonies() must set the option value from sym.id "
        "(the new {id,name} shape) -- today it assigns the bare `sym` (a "
        "display NAME) to option.value, the same F-023 bug as performance.js."
    )
    assert "sym.name" in body, "ai_advisor.js loadSymphonies() must set the option label from sym.name."
    assert not re.search(r"opt\.value\s*=\s*sym\s*;", body), (
        "ai_advisor.js loadSymphonies() still assigns the bare `sym` value to option.value."
    )
    assert not re.search(r"opt\.textContent\s*=\s*sym\s*;", body), (
        "ai_advisor.js loadSymphonies() still assigns the bare `sym` value to option.textContent."
    )


# ---------------------------------------------------------------------------
# AC-6 -- scope=aggregate stays byte-unchanged; no symphony_id_recognized leak
# ---------------------------------------------------------------------------


def test_performance_aggregate_scope_response_shape_unchanged(client, monkeypatch):
    dates = [f"2026-05-{i + 1:02d}" for i in range(10)]
    bot = [0.001 * i for i in range(10)]
    held = [0.0008 * i for i in range(10)]
    monkeypatch.setattr(
        app_module.analytics,
        "get_portfolio_bot_and_held_daily_returns",
        lambda *a, **kw: (dates, bot, held),
    )

    resp = client.get("/api/performance?scope=aggregate&days=60")
    assert resp.status_code == 200
    body = resp.get_json()
    expected_keys = {
        "scope",
        "dates",
        "live_returns",
        "shadow_returns",
        "live_metrics",
        "shadow_metrics",
        "observation_count",
        "insufficient_history",
        "window_days",
    }
    assert set(body.keys()) == expected_keys, (
        f"aggregate scope response shape changed -- AC-6 requires it stay "
        f"byte-unchanged. Got keys: {sorted(body.keys())}"
    )


def test_performance_aggregate_scope_never_has_symphony_id_recognized_key(client, monkeypatch):
    dates = [f"2026-05-{i + 1:02d}" for i in range(10)]
    bot = [0.001 * i for i in range(10)]
    held = [0.0008 * i for i in range(10)]
    monkeypatch.setattr(
        app_module.analytics,
        "get_portfolio_bot_and_held_daily_returns",
        lambda *a, **kw: (dates, bot, held),
    )

    resp = client.get("/api/performance?scope=aggregate&days=60")
    body = resp.get_json()
    assert "symphony_id_recognized" not in body, (
        "symphony_id_recognized is an AC-4 discriminator scoped to "
        "scope=symphony responses only -- it must never leak into aggregate "
        "responses"
    )


# ---------------------------------------------------------------------------
# Security -- symphony_id is server-provided but the route must still be safe
# against malformed values (defense in depth; see plan Security Considerations)
# ---------------------------------------------------------------------------


def test_symphony_id_sql_injection_payload_does_not_crash_or_leak_500(client, monkeypatch, tmp_path):
    shadow_db = tmp_path / "f023_injection_shadow.db"
    _seed_shadow_history_db(shadow_db, "unused", 0)
    monkeypatch.setattr(app_module.analytics, "DB_FILE", str(shadow_db))

    injection_payload = "x'; DROP TABLE shadow_history; --"
    resp = _get_perf(client, scope="symphony", symphony_id=injection_payload)
    assert resp.status_code == 200, (
        "a SQL-injection-shaped symphony_id must be handled safely by the "
        "parameterized query, not crash the route"
    )
    body = resp.get_json()
    assert body["observation_count"] == 0
    raw_text = resp.get_data(as_text=True)
    assert "Traceback" not in raw_text
    assert "sqlite3" not in raw_text.lower()


def test_symphony_id_oversized_input_handled_gracefully(client, monkeypatch, tmp_path):
    shadow_db = tmp_path / "f023_oversized_shadow.db"
    _seed_shadow_history_db(shadow_db, "unused", 0)
    monkeypatch.setattr(app_module.analytics, "DB_FILE", str(shadow_db))

    oversized_id = "x" * 10000
    resp = _get_perf(client, scope="symphony", symphony_id=oversized_id)
    assert resp.status_code == 200
    assert resp.get_json()["observation_count"] == 0
