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
