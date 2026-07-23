"""
RED tests -- F-016 locus 2: per-card SSR "Today Bot/Held" null->false-zero coercion.

PM RULING (fdc-tw plan approval, 2026-07-21): F-016 locus 2 (per-card SSR Jinja) is
EXPLICITLY IN SCOPE for this cycle. The plan's Architecture section under-listed
templates/index.html for F-016, but the FINDING itself is "fleet-wide (portfolio
header + all 11 cards)" and the card-side falsity lives in the SSR expressions.
This is a PM-authorized scope clarification -- fdc-rev should not flag this file's
templates/index.html touch as creep.

ROOT CAUSE (traced through the real render path, not asserted from the audit alone):
  1. app.py:938-945 `_safe_analytics` -- the dashboard() route's per-symphony `_tc`
     enrichment coerces EVERY None value in the analytics result to 0.0 BEFORE it
     ever reaches the template: `{k: (v if v is not None else 0.0) for k, v in
     result.items()}`. A genuine "no data yet" result (the documented shape used
     elsewhere in this same file at app.py:1802/2266: `{"if_held": None, "dry_run":
     None}`) is silently rewritten to `{"if_held": 0.0, "dry_run": 0.0}`.
  2. templates/index.html's dv-value headline (:1080-1081, :1183-1184) and cfg-val
     footer (:1121-1122, :1198-1199) `{% set %}` blocks ALSO default a missing/None
     `dry_run`/`if_held` to 0 via `.get(key, 0)` / `(tc_h or 0)` -- with NO None-aware
     branch, unlike the MDD row's own precedent guard at :1125-1128 (`_mdd_bot_raw is
     not none`) which already solves this exact class of bug for a sibling field.
  Both layers must be considered: fixing only one may still leave a false "+0.0%"
  visible end-to-end, which is what this test actually exercises (real route render,
  not template-in-isolation).

CONTRACT (AC-3, per feature-plans/fix-display-cluster.md):
  - A genuinely null/missing Today value renders the codebase's honest empty-state,
    never a false "+0.0%".
  - A genuine 0.0 still renders "+0.0%" (both-sides regression pin).
  - Fleet-wide: both the dv-value headline AND the cfg-val footer, in BOTH the
    Active and Standby card sections (the two near-duplicate SSR blocks in
    templates/index.html).

Fixture/harness convention: mirrors tests/dashboard/test_mdd_honest_framing.py's
established `mock_database` + `_analytics_mock(...)` + `client.get("/")` real-route
render pattern (the sibling AC-4c cycle's precedent for this exact route).
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# fdc-rev review finding (2026-07-21): F-016 has a THIRD locus, not covered by
# the original RED battery -- see TestApiStateGenuineZeroTodayNotMisrenderedAsNull
# below.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Fixtures (mirrors test_mdd_honest_framing.py)
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
        yield db_mock


def _analytics_mock(*, symphony_today_change: dict) -> MagicMock:
    """A sufficient-history (>=30d) analytics stub so the MDD-insufficient framing
    does not interfere with the TC-row assertions this file scopes to. Only
    `get_symphony_today_change` varies per test -- everything else is a realistic,
    non-triggering baseline."""
    long_dates = [f"2026-05-{d:02d}" for d in range(1, 32)] + [
        f"2026-06-{d:02d}" for d in range(1, 10)
    ]
    m = MagicMock()
    m.get_portfolio_today_change.return_value = {"if_held": 0.0, "dry_run": 0.0}
    m.get_portfolio_cumulative_return.return_value = {"if_held": 10.0, "dry_run": 10.0}
    m.get_portfolio_max_drawdown.return_value = {"if_held": 5.0, "dry_run": 4.0}
    m.get_symphony_today_change.return_value = symphony_today_change
    m.get_symphony_cumulative_return.return_value = {"if_held": 12.0, "dry_run": 12.0}
    m.get_symphony_max_drawdown.return_value = {"if_held": 8.0, "dry_run": 6.0}
    m.get_portfolio_daily_returns_from_shadow.return_value = (long_dates, [0.0] * len(long_dates))
    m.compute_portfolio_annualized_vol.return_value = 0.1
    m.get_history_with_cache_invalidation.return_value = {}
    m.compute_aggregate_returns.return_value = (
        long_dates,
        [0.0] * len(long_dates),
        [0.0] * len(long_dates),
    )
    m._POST_MORTEMS_DIR = "/tmp/no-such-dir"
    return m


def _one_symphony_state(*, section: str) -> dict:
    """A single-symphony bot_state. section='active' sets armed=True (lands in the
    Active section markup); section='standby' sets all flags False (lands in the
    Standby section markup, a SEPARATE near-duplicate SSR block)."""
    is_active = section == "active"
    return {
        "sym-tc-probe": {
            "name": "TC Probe Symphony",
            "account": "ACC1",
            "armed": is_active,
            "tp_armed": False,
            "para_armed": False,
            "triggered": False,
            "current_return": 1.5,
            "current_value": 10000.0,
            "stop_trigger": -2.0,
            "mc_prob": 40.0,
            "simple_return": 0.12,
            "net_deposits": 1000.0,
            "time_weighted_return": 0.12,
            "max_drawdown": 0.08,
        }
    }


def _card_markup(html: str, sym_id: str) -> str:
    """Scope the rendered HTML to the single card for `sym_id` -- from its opening
    sym-card div to the next sym-card div (or a generous fallback window)."""
    anchor = html.find(f'data-sym-id="{sym_id}"')
    assert anchor != -1, f"rendered page must contain a card for {sym_id!r}"
    start = html.rfind('data-testid="sym-card"', 0, anchor)
    assert start != -1, "could not find the enclosing sym-card container"
    next_card = html.find('data-testid="sym-card"', anchor + 1)
    end = next_card if next_card != -1 else start + 4000
    return html[start:end]


def _field_spans(card_html: str, field: str) -> list[str]:
    """Every rendered <span data-field="{field}" ...>...</span> occurrence within a
    scoped card block (there are two per card: dv-value headline + cfg-val footer)."""
    spans = []
    for m in re.finditer(rf'data-field="{re.escape(field)}"[^>]*>([^<]*)<', card_html):
        spans.append(m.group(1))
    return spans


# ===========================================================================
# Null Today -> must NOT render a false "+0.0%" (either section, either locus)
# ===========================================================================


class TestNullTodayNeverRendersFalseZero:
    @pytest.mark.parametrize("section", ["active", "standby"])
    def test_null_today_bot_and_held_do_not_render_false_zero(
        self, client, mock_database, monkeypatch, section
    ):
        mock_database.load_state.return_value = _one_symphony_state(section=section)
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(
            app_module,
            "analytics",
            _analytics_mock(symphony_today_change={"if_held": None, "dry_run": None}),
        )

        resp = client.get("/")
        assert resp.status_code == 200, f"dashboard render failed: {resp.status_code}"
        html = resp.get_data(as_text=True)

        card = _card_markup(html, "sym-tc-probe")
        for field in ("tc-bot", "tc-held"):
            spans = _field_spans(card, field)
            assert spans, (
                f'F-016 FAIL: no data-field="{field}" span found in the {section} '
                f"card -- test scoping is stale, update the markers."
            )
            for text in spans:
                assert not re.match(r"^[+-]?0\.0%$", text.strip()), (
                    f"F-016 FAIL ({section} section, data-field={field!r}): rendered "
                    f"a false {text.strip()!r} for a genuinely None today-change value "
                    f"-- null must render the honest empty-state, never a fabricated "
                    f"'+0.0%' that reads as 'no change today' when the data is "
                    f"actually missing. Card markup: {card[:600]!r}"
                )


# ===========================================================================
# Genuine 0.0 -> regression pin, must STILL render "+0.0%" (both sides of AC-3)
# ===========================================================================


class TestGenuineZeroTodayStillRendersZero:
    @pytest.mark.parametrize("section", ["active", "standby"])
    def test_genuine_zero_today_bot_and_held_render_plus_zero(
        self, client, mock_database, monkeypatch, section
    ):
        mock_database.load_state.return_value = _one_symphony_state(section=section)
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(
            app_module,
            "analytics",
            _analytics_mock(symphony_today_change={"if_held": 0.0, "dry_run": 0.0}),
        )

        resp = client.get("/")
        assert resp.status_code == 200, f"dashboard render failed: {resp.status_code}"
        html = resp.get_data(as_text=True)

        card = _card_markup(html, "sym-tc-probe")
        for field in ("tc-bot", "tc-held"):
            spans = _field_spans(card, field)
            assert spans, (
                f'regression-pin FAIL: no data-field="{field}" span found in the '
                f"{section} card -- test scoping is stale, update the markers."
            )
            assert any(text.strip() == "+0.0%" for text in spans), (
                f"AC-3 regression FAIL ({section} section, data-field={field!r}): a "
                f"GENUINE 0.0 today-change must still render '+0.0%' -- the "
                f"honest-empty-state fix for null must not also suppress a real "
                f"zero. Rendered spans: {spans!r}"
            )


# ===========================================================================
# F-016 THIRD LOCUS (fdc-rev finding, 2026-07-21): the LIVE /api/state poll
# path silently converts a genuine 0.0 into null -- the OPPOSITE direction
# bug from loci 1/2 above, but squarely within AC-3's own text: "genuine 0.0
# still renders '+0.00%' ... Fleet-wide (portfolio header + per-card)".
# ===========================================================================


class TestApiStateGenuineZeroTodayNotMisrenderedAsNull:
    """ROOT CAUSE (pre-existing, untouched by this cycle's diff, confirmed via
    direct source read): app.py's `_tc_cr_mdd_floats` (~2396-2406, defined
    inside the `/api/state` route) computes:

        tc_bot  = (tc.get("dry_run") if isinstance(tc, dict) else tc) or None
        tc_held = (tc.get("if_held") if isinstance(tc, dict) else None) or None

    `0.0 or None` evaluates to `None` in Python (0.0 is falsy) -- a genuine
    "no change today" (dry_run=0.0/if_held=0.0, a fully plausible real value:
    a flat market, a fresh trading day, etc.) is silently converted to None
    BEFORE it ever reaches `_symphonies_for_cards` (app.py:2429-2430), which
    feeds `data.symphonies[].tc_bot`/`tc_held` in the REAL `/api/state` JSON
    response. static/index.js's `updateCards` (the "cards-live" feature,
    wired into `updateDashboard`'s per-poll pipeline -- reachable on every
    30s poll, not dead code) renders this via `_fmtSignedPct`, which
    correctly returns `'--'` for `null` -- so the net effect is: a genuine
    0.0% Today change gets silently misrendered as the empty-state em-dash
    on every live re-poll, on every card. This is the exact opposite of
    loci 1/2 (there: null -> false zero; here: genuine zero -> false null),
    but it is the SAME finding (F-016) and the SAME AC (AC-3's "genuine 0.0
    still renders '+0.00%' ... Fleet-wide") -- the initial SSR page-load is
    now correct (loci 1/2), but this bug re-corrupts the cards on the very
    next poll via a path neither the JS-source-pin battery nor the
    route-level test battery originally touched.
    """

    def test_api_state_symphony_tc_bot_and_held_are_zero_not_null_when_genuinely_zero(
        self, client, mock_database, monkeypatch
    ):
        mock_database.load_state.return_value = _one_symphony_state(section="active")
        mock_database.get_last_trigger_per_symphony.return_value = {}
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(
            app_module,
            "analytics",
            _analytics_mock(symphony_today_change={"if_held": 0.0, "dry_run": 0.0}),
        )
        monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open")

        resp = client.get("/api/state")
        assert resp.status_code == 200, f"/api/state returned {resp.status_code}"
        body = resp.get_json()
        symphonies = body.get("symphonies")
        assert isinstance(symphonies, list) and symphonies, (
            "fixture sanity: /api/state must return a non-empty 'symphonies' list "
            "for this test to be meaningful."
        )
        sym = next((s for s in symphonies if s.get("id") == "sym-tc-probe"), None)
        assert sym is not None, "fixture symphony 'sym-tc-probe' missing from the response."

        assert sym.get("tc_bot") == 0.0, (
            f"F-016 FAIL (3rd locus): /api/state symphonies[].tc_bot == "
            f"{sym.get('tc_bot')!r} for a GENUINE 0.0 today-change -- "
            f"_tc_cr_mdd_floats' `tc.get('dry_run') or None` pattern silently "
            f"converts a real 0.0 (falsy) into a fabricated null, which "
            f"static/index.js's updateCards then misrenders as the empty-state "
            f"'--' on every live poll instead of the honest '+0.0%'."
        )
        assert sym.get("tc_held") == 0.0, (
            f"F-016 FAIL (3rd locus): /api/state symphonies[].tc_held == "
            f"{sym.get('tc_held')!r} for a GENUINE 0.0 today-change (same bug as "
            f"tc_bot above, `tc.get('if_held') or None`)."
        )

    def test_api_state_symphony_tc_bot_and_held_are_still_null_when_genuinely_null(
        self, client, mock_database, monkeypatch
    ):
        """Regression pin (the other half of the same fix): a GENUINELY missing
        today-change (analytics returns {"if_held": None, "dry_run": None}, the
        real "no data yet" shape) must still surface as null in the JSON --
        the fix must not overcorrect into fabricating a false 0.0 for real
        missing data."""
        mock_database.load_state.return_value = _one_symphony_state(section="active")
        mock_database.get_last_trigger_per_symphony.return_value = {}
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(
            app_module,
            "analytics",
            _analytics_mock(symphony_today_change={"if_held": None, "dry_run": None}),
        )
        monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open")

        resp = client.get("/api/state")
        assert resp.status_code == 200, f"/api/state returned {resp.status_code}"
        body = resp.get_json()
        symphonies = body.get("symphonies")
        sym = next((s for s in (symphonies or []) if s.get("id") == "sym-tc-probe"), None)
        assert sym is not None, "fixture symphony 'sym-tc-probe' missing from the response."

        assert sym.get("tc_bot") is None, (
            f"regression FAIL: /api/state symphonies[].tc_bot == {sym.get('tc_bot')!r} "
            f"for a genuinely null today-change -- must stay None, not be coerced "
            f"to some other value by the F-016 3rd-locus fix."
        )
        assert sym.get("tc_held") is None, (
            f"regression FAIL: /api/state symphonies[].tc_held == "
            f"{sym.get('tc_held')!r} for a genuinely null today-change."
        )


# ===========================================================================
# PM RULING (2026-07-21, resolving fdc-rev's block on HEAD 02bca884): KEEP
# the 6-field fix in _tc_cr_mdd_floats; the cr/mdd half was untested by the
# original RED battery above (which only pinned tc_bot/tc_held) -- this class
# closes that gap. Same bug shape, same fix, same function as the Today pair:
# `0.0 or None` fabricates a null from a genuine 0.0 cumulative-return or MDD
# (a never-triggered symphony has a real 0.0 MDD -- not a rare edge case).
# Ruling explicitly distinguishes this from the earlier `_safe_analytics`
# Today-only precedent: that ruling narrowed a DIFFERENT-direction bug
# (None->0.0 un-coercion) to protect UNVERIFIED template consumers from
# receiving a raw None; this locus is the OPPOSITE direction (0.0->None
# fabrication) on a JSON path whose client consumers are VERIFIED null-safe
# (fdc-rev's own rerun) -- honesty-restoring with no unverified blast radius.
# ===========================================================================


class TestApiStateGenuineZeroCrAndMddNotMisrenderedAsNull:
    @pytest.mark.parametrize(
        "mock_attr, bot_key, held_key",
        [
            ("get_symphony_cumulative_return", "cr_bot", "cr_held"),
            ("get_symphony_max_drawdown", "mdd_bot", "mdd_held"),
        ],
    )
    def test_genuine_zero_renders_zero_not_null(
        self, client, mock_database, monkeypatch, mock_attr, bot_key, held_key
    ):
        mock_database.load_state.return_value = _one_symphony_state(section="active")
        mock_database.get_last_trigger_per_symphony.return_value = {}
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        analytics_mock = _analytics_mock(symphony_today_change={"if_held": 1.0, "dry_run": 1.0})
        getattr(analytics_mock, mock_attr).return_value = {"if_held": 0.0, "dry_run": 0.0}
        monkeypatch.setattr(app_module, "analytics", analytics_mock)
        monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open")

        resp = client.get("/api/state")
        assert resp.status_code == 200, f"/api/state returned {resp.status_code}"
        body = resp.get_json()
        sym = next(
            (s for s in (body.get("symphonies") or []) if s.get("id") == "sym-tc-probe"), None
        )
        assert sym is not None, "fixture symphony 'sym-tc-probe' missing from the response."

        assert sym.get(bot_key) == 0.0, (
            f"F-016 FAIL (3rd locus, cr/mdd extension): /api/state "
            f"symphonies[].{bot_key} == {sym.get(bot_key)!r} for a GENUINE 0.0 "
            f"value -- same `or None` fabrication bug as tc_bot/tc_held, same "
            f"fix, same function ({mock_attr} feeds this field)."
        )
        assert sym.get(held_key) == 0.0, (
            f"F-016 FAIL (3rd locus, cr/mdd extension): /api/state "
            f"symphonies[].{held_key} == {sym.get(held_key)!r} for a GENUINE 0.0 "
            f"value."
        )

    @pytest.mark.parametrize(
        "mock_attr, bot_key, held_key",
        [
            ("get_symphony_cumulative_return", "cr_bot", "cr_held"),
            ("get_symphony_max_drawdown", "mdd_bot", "mdd_held"),
        ],
    )
    def test_genuine_null_stays_null_regression(
        self, client, mock_database, monkeypatch, mock_attr, bot_key, held_key
    ):
        """Regression pin (the other half of the same fix, mirroring
        TestApiStateGenuineZeroTodayNotMisrenderedAsNull's null-stays-null
        case): a genuinely missing cumulative-return/MDD must still surface as
        null -- the fix must not overcorrect into fabricating a false 0.0."""
        mock_database.load_state.return_value = _one_symphony_state(section="active")
        mock_database.get_last_trigger_per_symphony.return_value = {}
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        analytics_mock = _analytics_mock(symphony_today_change={"if_held": 1.0, "dry_run": 1.0})
        getattr(analytics_mock, mock_attr).return_value = {"if_held": None, "dry_run": None}
        monkeypatch.setattr(app_module, "analytics", analytics_mock)
        monkeypatch.setattr(app_module, "get_market_state", lambda dt: "open")

        resp = client.get("/api/state")
        assert resp.status_code == 200, f"/api/state returned {resp.status_code}"
        body = resp.get_json()
        sym = next(
            (s for s in (body.get("symphonies") or []) if s.get("id") == "sym-tc-probe"), None
        )
        assert sym is not None, "fixture symphony 'sym-tc-probe' missing from the response."

        assert sym.get(bot_key) is None, (
            f"regression FAIL: /api/state symphonies[].{bot_key} == "
            f"{sym.get(bot_key)!r} for a genuinely null value -- must stay None."
        )
        assert sym.get(held_key) is None, (
            f"regression FAIL: /api/state symphonies[].{held_key} == "
            f"{sym.get(held_key)!r} for a genuinely null value."
        )
