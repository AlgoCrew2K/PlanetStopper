"""
RED tests — dashboard render consistency (AC-4a stale alpha, AC-4d standby badge).

AC-4a (HIGH) — the user's 67.73-65.56=2.17 vs displayed 1.72 symptom.
  ``updateComparisonRows`` (static/index.js:857-889) updates comp-{row}-bot-text /
  held-text on every 30s poll but NEVER the alpha (.vs-delta) span for the today /
  cumulative / mdd rows (only the vol row's comp-vol-delta updates). The three
  .vs-delta spans (templates/index.html:880/903/929) have NO data-testid, so the JS
  cannot even address them. Result: Bot/Held tick live while alpha stays frozen at its
  server-side-render value -> displayed (Bot - Held) drifts away from the displayed alpha.

  The fix: (1) give the three delta spans data-testid="comp-{today,cumulative,mdd}-delta";
  (2) updateComparisonRows queries AND writes them each poll so the displayed delta == the
  displayed Bot - Held by construction. These tests pin both halves at the SOURCE level
  (the established dashboard-test pattern: read_text on the template + index.js + a
  `node --check` parse guard — per feedback_visual_gate_catches_js_break, a JS parse error
  passes every server/template test while the modal/poll silently never runs).

AC-4d (MEDIUM) — standby section badge inflates 11 -> 12.
  ``symphony_keys`` at app.py:1294 selects every top-level dict in bot_state with NO
  ``"name" in v`` guard (the SSR/meta paths at app.py:659 DO guard), so the phantom
  ``last_market_close_snapshot`` top-level key leaks into data.symphonies. The /api/state
  'symphonies' count must equal the name-guarded symphony count, excluding the phantom.

These intentionally FAIL on cdfa088. Render correctness is necessary but NOT sufficient —
the PM owns the final live rendered-page gate.
"""

from __future__ import annotations

import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

_JS_PATH = pathlib.Path(__file__).parent.parent.parent / "static" / "index.js"
_INDEX_HTML_PATH = pathlib.Path(__file__).parent.parent.parent / "templates" / "index.html"

_DELTA_ROWS = ["today", "cumulative", "mdd"]


# ---------------------------------------------------------------------------
# Fixtures / helpers (mirror tests/dashboard/test_cards_live_refresh.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_database():
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
        db_mock.read_fleet_alert.return_value = None
        db_mock.get_triggers.return_value = []
        db_mock.get_guard_alpha_by_symphony.return_value = {}
        # get_api_state_dict (app.py:897-911) calls database.read_port_state(acc_id) for each
        # account in bot_state. Unstubbed it returns a truthy MagicMock -> port_state carries a
        # non-JSON-serializable value -> /api/state 500s -> no "symphonies" key. None is the real
        # prod behavior (pre-migration-010) and keeps port_state empty + serializable. Without this
        # the test is order-dependent (passes only when an earlier test leaves read_port_state stubbed).
        db_mock.read_port_state.return_value = None
        yield db_mock


def _default_analytics_mock():
    m = MagicMock()
    m.get_portfolio_today_change.return_value = {"if_held": 1.0, "dry_run": 0.8}
    m.get_portfolio_cumulative_return.return_value = {"if_held": 10.0, "dry_run": 9.5}
    m.get_portfolio_max_drawdown.return_value = {"if_held": 0.05, "dry_run": 0.05}
    m.get_symphony_today_change.return_value = {"if_held": 1.2, "dry_run": 0.9}
    m.get_symphony_cumulative_return.return_value = {"if_held": 15.0, "dry_run": 14.0}
    m.get_symphony_max_drawdown.return_value = {"if_held": 0.08, "dry_run": 0.07}
    m.get_portfolio_daily_returns_from_shadow.return_value = None
    m.get_history_with_cache_invalidation.return_value = {}
    m.compute_aggregate_returns.return_value = ([], [], [])
    m._POST_MORTEMS_DIR = "/tmp/no-such-dir"
    return m


def _js_source() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


def _html_source() -> str:
    return _INDEX_HTML_PATH.read_text(encoding="utf-8")


# ===========================================================================
# AC-4a part 1 — the three delta spans must carry stable testids (template source)
# ===========================================================================


class TestDeltaSpansHaveTestids:
    """The today/cumulative/mdd alpha spans must be addressable by data-testid so the
    poll can update them. Today only the bot-text/held-text spans have testids.
    """

    @pytest.mark.parametrize("row", _DELTA_ROWS)
    def test_template_delta_span_has_testid(self, row):
        html = _html_source()
        testid = f'data-testid="comp-{row}-delta"'
        assert testid in html, (
            f"AC-4a FAIL: templates/index.html has no {testid} on the {row} alpha "
            "(.vs-delta) span. Without a stable testid the poll cannot address the "
            "delta, so alpha stays frozen at its server-side-render value while Bot/Held "
            "tick live (the 2.17-vs-1.72 symptom)."
        )

    def test_template_delta_spans_are_vs_delta_class(self):
        """Each comp-{row}-delta testid must sit on the .vs-delta alpha span (not a
        random element) — i.e. the testid and the vs-delta class co-occur on one tag.
        """
        html = _html_source()
        for row in _DELTA_ROWS:
            # A span tag carrying both the vs-delta class and the comp-{row}-delta testid.
            pattern = re.compile(
                r"<span[^>]*\bvs-delta\b[^>]*comp-" + row + r"-delta\b"
                r"|<span[^>]*comp-" + row + r"-delta\b[^>]*\bvs-delta\b"
            )
            assert pattern.search(html), (
                f"AC-4a FAIL: comp-{row}-delta testid must be on the .vs-delta alpha span."
            )


# ===========================================================================
# AC-4a part 2 — updateComparisonRows must WRITE the delta spans each poll (JS source)
# ===========================================================================


class TestPollUpdatesDeltaSpans:
    """The poll handler must address AND write each delta span so the displayed alpha
    refreshes together with Bot/Held — the displayed delta must equal displayed Bot-Held.
    """

    @pytest.mark.parametrize("row", _DELTA_ROWS)
    def test_index_js_references_each_delta_testid(self, row):
        js = _js_source()
        testid = f"comp-{row}-delta"
        assert testid in js, (
            f"AC-4a FAIL: static/index.js never references '{testid}'. "
            "updateComparisonRows updates comp-{row}-bot-text/held-text but not the alpha "
            "delta span, so the displayed alpha goes stale on every poll."
        )

    def test_update_comparison_rows_writes_a_delta_textcontent(self):
        """Within updateComparisonRows, each row's delta element must receive a
        textContent assignment (the poll must actually write the alpha, not just query it).

        Pins the contract structurally: for every row the function must (a) select a
        comp-{row}-delta element and (b) assign .textContent to a delta variable. We
        assert the function body references all three delta testids and performs a
        delta textContent write — the exact value is derived live from bot-held, never
        hardcoded.
        """
        js = _js_source()
        # Isolate the updateComparisonRows function body (best-effort brace-free slice:
        # from its declaration to the next top-level 'function ' at the same indent).
        #
        # DIAGNOSED 2026-07-21 (CI failure on PR #110, DE-DISPLAY-TRUTH-001): this used
        # to be a FIXED `js[start:start+4000]` window, on the assumption "the function
        # is well under this size" -- that assumption broke silently as later cycles
        # (F-014/F-016 among them) added explanatory inline comments earlier in the
        # function body, pushing the real `deltaEl.textContent = ...` write (still
        # present and correct, per AC-4a's contract) to offset ~4040 -- 40 chars past
        # the cutoff. Not a regression: the write survived, the test's brittle
        # fixed-length slice just stopped reaching it. Switched to the same
        # boundary-detection idiom this codebase already uses elsewhere (e.g.
        # tests/dashboard/test_f7_ac2_render_surfaces_js.py's `_slice_function`) so
        # future comment growth in this function can never silently blind this test
        # again -- matches what this docstring/comment already claimed the slice did.
        start = js.find("function updateComparisonRows")
        assert start != -1, "static/index.js must define updateComparisonRows"
        next_fn = js.find("\n    function ", start + 1)
        body = js[start:next_fn] if next_fn != -1 else js[start : start + 8000]

        for row in _DELTA_ROWS:
            assert f"comp-{row}-delta" in body, (
                f"AC-4a FAIL: updateComparisonRows body does not address comp-{row}-delta."
            )
        # A delta textContent write must occur for the today/cumulative/mdd rows — assert
        # the function assigns textContent to a delta element at least as many times as the
        # vol row already does. The vol delta write (comp-vol-delta) is in a DIFFERENT
        # function path; here we require the row loop itself to write a delta.
        # Heuristic contract: a 'Delta' or 'delta' textContent assignment inside the row loop.
        assert re.search(r"[Dd]elta[A-Za-z]*\.textContent\s*=", body), (
            "AC-4a FAIL: updateComparisonRows must assign textContent to a delta element "
            "for the today/cumulative/mdd rows (the alpha must refresh on poll)."
        )


# ===========================================================================
# AC-4d — standby/symphonies count excludes the phantom non-name top-level key
# ===========================================================================


class TestStandbyCountExcludesPhantomKey:
    """/api/state 'symphonies' must be name-guarded: a phantom top-level dict without a
    'name' (e.g. last_market_close_snapshot) must NOT inflate the count 11 -> 12.
    """

    def _state_with_phantom(self):
        return {
            "sym-a": {
                "name": "Alpha",
                "account": "ACC1",
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
                "current_return": 1.0,
                "current_value": 10000.0,
                "stop_trigger": -2.0,
                "mc_prob": 30.0,
            },
            "sym-b": {
                "name": "Bravo",
                "account": "ACC1",
                "armed": True,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
                "current_return": 0.5,
                "current_value": 9500.0,
                "stop_trigger": -1.5,
                "mc_prob": 40.0,
            },
            # Phantom: a top-level dict with NO 'name' — must be excluded from symphonies.
            "last_market_close_snapshot": {"ts": "2026-06-04T20:00:00Z", "value": 12945.18},
        }

    def test_symphonies_excludes_phantom_non_name_key(self, client, mock_database, monkeypatch):
        state = self._state_with_phantom()
        mock_database.load_state.return_value = state
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())
        # Force the ACTIVE path: when the market is "closed_frozen"/"pre_market" AND a
        # last_market_close_snapshot exists, get_state serves the FROZEN branch (app.py:1038)
        # which returns no "symphonies" key. Our phantom key IS last_market_close_snapshot, so
        # we must drive the open path to reach the name-guard at app.py:1386.
        monkeypatch.setattr(app_module, "get_market_state", lambda *_a, **_k: "open")

        resp = client.get("/api/state")
        assert resp.status_code == 200
        symphonies = resp.get_json()["symphonies"]

        # DERIVED: the expected count is the number of name-bearing top-level dicts.
        expected = sum(1 for v in state.values() if isinstance(v, dict) and "name" in v)
        assert len(symphonies) == expected, (
            f"AC-4d FAIL: 'symphonies' has {len(symphonies)} entries, expected {expected} "
            "name-guarded ones. The phantom 'last_market_close_snapshot' leaked in "
            '(app.py:1294 has no `"name" in v` guard), inflating the standby badge 11 -> 12.'
        )

    def test_no_symphony_is_the_phantom_key(self, client, mock_database, monkeypatch):
        state = self._state_with_phantom()
        mock_database.load_state.return_value = state
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())
        # Force the ACTIVE path (see sibling test) — the phantom key doubles as the frozen
        # snapshot key, so an "open" market state is required to reach the name-guard.
        monkeypatch.setattr(app_module, "get_market_state", lambda *_a, **_k: "open")

        resp = client.get("/api/state")
        symphonies = resp.get_json()["symphonies"]
        ids = {s.get("id") for s in symphonies}
        assert "last_market_close_snapshot" not in ids, (
            "AC-4d FAIL: the phantom 'last_market_close_snapshot' key surfaced as a "
            "symphony in /api/state — it has no 'name' and must be name-guarded out."
        )
