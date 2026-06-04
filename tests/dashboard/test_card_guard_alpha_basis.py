"""
RED/regression tests — AC-2 / AC-5 per-card guard-alpha basis consistency (S-1).

DATA-CORRECTNESS-AUDIT S-1: the per-card "saved +X%alpha at exit" verdict reads
``database.get_guard_alpha_by_symphony`` (exit_triggers.at_return - current_return) — a
THIRD basis unrelated to the hero divergence basis. On the same screen a card could show a
non-zero guard alpha while the hero shows 0.00%, so "nothing self-reconciles" (the user's
core complaint).

THE CONTRACT (AC-2/AC-5 — ONE reconciling basis per metric, exit-snapshot disambiguated):
  - The card's CUMULATIVE guard-alpha badge (cr-alpha-badge) must be cr_bot - cr_held, where
    cr_bot/cr_held are the SAME divergence-basis values the hero uses
    (analytics.get_symphony_cumulative_return). So the card cumulative alpha reconciles with
    the hero per-symphony alpha — no third basis for the cumulative number.
  - The exit-time snapshot (database.get_guard_alpha_by_symphony) is a DIFFERENT, legitimate
    metric (CR at the exit moment vs the shadow baseline). It must be clearly LABELED
    "at exit" (data-testid="guard-alpha-at-exit") so it is not read as the cumulative guard
    alpha — disambiguation, not a third unlabeled number.

These assert at the /api/state payload + template-source level (the established dashboard-test
pattern). The PM owns the LIVE card-vs-hero reconciliation gate.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

_INDEX_HTML_PATH = pathlib.Path(__file__).parent.parent.parent / "templates" / "index.html"


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
        # The exit-time snapshot source (a THIRD basis); the cumulative card alpha must
        # NOT come from here.
        db_mock.get_guard_alpha_by_symphony.return_value = {}
        yield db_mock


def _html() -> str:
    return _INDEX_HTML_PATH.read_text(encoding="utf-8")


def _state_one_symphony():
    return {
        "sym-x": {
            "name": "Symphony X", "account": "ACC1", "armed": True, "tp_armed": False,
            "para_armed": False, "triggered": False, "current_return": 1.5,
            "current_value": 10000.0, "stop_trigger": -2.0, "mc_prob": 40.0,
            "simple_return": 0.12, "net_deposits": 1000.0, "time_weighted_return": 0.12,
            "max_drawdown": 0.08,
        }
    }


def _analytics_with_divergence_cr(cr_bot, cr_held):
    """analytics mock whose per-symphony CR returns a KNOWN divergence-basis pair, so the
    card cr_bot/cr_held must echo it (proving the card reads the divergence basis)."""
    m = MagicMock()
    m.get_portfolio_today_change.return_value = {"if_held": 1.0, "dry_run": 0.8}
    m.get_portfolio_cumulative_return.return_value = {"if_held": cr_held, "dry_run": cr_bot}
    m.get_portfolio_max_drawdown.return_value = {"if_held": 0.05, "dry_run": 0.05}
    m.get_symphony_today_change.return_value = {"if_held": 1.2, "dry_run": 0.9}
    m.get_symphony_cumulative_return.return_value = {"if_held": cr_held, "dry_run": cr_bot}
    m.get_symphony_max_drawdown.return_value = {"if_held": 0.08, "dry_run": 0.07}
    m.get_portfolio_daily_returns_from_shadow.return_value = None
    m.get_history_with_cache_invalidation.return_value = {}
    m.compute_aggregate_returns.return_value = ([], [], [])
    m._POST_MORTEMS_DIR = "/tmp/no-such-dir"
    return m


# ---------------------------------------------------------------------------
# AC-2/AC-5 — the card cumulative alpha uses the divergence basis (cr_bot - cr_held)
# ---------------------------------------------------------------------------

class TestCardCumulativeAlphaUsesDivergenceBasis:
    def test_card_cr_fields_echo_divergence_basis_from_analytics(
        self, client, mock_database, monkeypatch
    ):
        """The card payload cr_bot/cr_held must be the analytics divergence-basis values
        (get_symphony_cumulative_return), so the card cumulative alpha (cr_bot - cr_held)
        reconciles with the hero per-symphony alpha — NOT a third basis."""
        cr_bot, cr_held = 14.0, 12.0  # KNOWN divergence-basis pair injected via the mock
        mock_database.load_state.return_value = _state_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(
            app_module, "analytics", _analytics_with_divergence_cr(cr_bot, cr_held)
        )

        resp = client.get("/api/state")
        assert resp.status_code == 200
        syms = resp.get_json()["symphonies"]
        assert syms, "expected one symphony in the card payload"
        card = syms[0]

        # DERIVED from the injected divergence-basis mock — the card must echo it.
        assert card["cr_bot"] == pytest.approx(cr_bot, abs=1e-9), (
            f"AC-2 FAIL: card cr_bot {card['cr_bot']} != divergence-basis dry_run {cr_bot}. "
            "The card cumulative alpha must use the SAME basis as the hero "
            "(analytics.get_symphony_cumulative_return), not a third source."
        )
        assert card["cr_held"] == pytest.approx(cr_held, abs=1e-9), (
            f"AC-2 FAIL: card cr_held {card['cr_held']} != divergence-basis if_held {cr_held}."
        )

    def test_card_cumulative_alpha_reconciles_with_divergence_gap(
        self, client, mock_database, monkeypatch
    ):
        """The card's cumulative guard alpha (cr_bot - cr_held) must equal the injected
        divergence gap — the value the cr-alpha-badge renders. Reconciles with the hero."""
        cr_bot, cr_held = 9.5, 10.0
        mock_database.load_state.return_value = _state_one_symphony()
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(
            app_module, "analytics", _analytics_with_divergence_cr(cr_bot, cr_held)
        )

        card = client.get("/api/state").get_json()["symphonies"][0]
        card_alpha = card["cr_bot"] - card["cr_held"]
        assert card_alpha == pytest.approx(cr_bot - cr_held, abs=1e-9), (
            f"AC-5 FAIL: card cumulative alpha {card_alpha} != divergence gap "
            f"{cr_bot - cr_held}. The card cumulative alpha must be the divergence basis."
        )


# ---------------------------------------------------------------------------
# AC-2 — the exit-time snapshot is disambiguated, not an unlabeled third number
# ---------------------------------------------------------------------------

class TestExitSnapshotIsLabeled:
    def test_template_labels_exit_snapshot_at_exit(self):
        """The exit-time guard_alpha (from exit_triggers.at_return — a different metric than
        the cumulative divergence) must carry the 'at exit' disambiguation testid so it is
        not read as the cumulative guard alpha that the hero shows."""
        html = _html()
        assert 'data-testid="guard-alpha-at-exit"' in html, (
            "AC-2 FAIL: the per-card exit-time guard-alpha verdict has no 'at exit' label "
            "(data-testid=guard-alpha-at-exit). An unlabeled exit-snapshot alpha sitting next "
            "to the cumulative cr-alpha-badge reproduces the S-1 'three guard-alpha numbers "
            "that don't reconcile' confusion."
        )

    def test_template_card_has_cr_alpha_badge_field(self):
        """The card must expose the cumulative divergence-basis alpha badge (cr-alpha-badge)
        — the hero-consistent number."""
        html = _html()
        assert 'data-field="cr-alpha-badge"' in html, (
            "AC-2 FAIL: the card is missing the cumulative cr-alpha-badge (divergence basis). "
            "The card must surface the hero-consistent cumulative guard alpha."
        )
