"""
RED tests — Cluster 6 AC-4: drawdown sign convention must be consistent on the
operator-facing surface, regardless of cache warmth.

Audit provenance:
  risk-math__2026-05-21.md MEDIUM "Two opposing max-drawdown sign conventions
  across the codebase" + invariant-coverage__2026-05-21.md MEDIUM-6, AND a
  load-bearing live sign-flip found by risk-engine-specialist during the
  Cluster 6 pre-review (not in the original audit).

THE FINDING. Three drawdown surfaces use different sign conventions:
  - port_aggregator._compute_max_drawdown_from_series  -> NEGATIVE float
    (internal; consumed only by aggregate_to_port).
  - analytics.compute_quantstats_metrics["max_drawdown"] -> NEGATIVE (<= 0)
    (internal quant-metrics function).
  - analytics.get_symphony_max_drawdown["if_held"/"dry_run"] -> POSITIVE
    magnitude (operator-facing).

THE LIVE DEFECT (risk-engine-specialist). app._compute_portfolio_strip emits
portfolio_strip["max_drawdown"]["if_held"] from TWO branches:
  - WARM cache: if_held = _account_totals_cache["portfolio_mdd"], which app.py
    sets from `float(compute_quantstats_metrics["max_drawdown"]) * 100` — the
    NEGATIVE convention.
  - COLD cache: if_held flows from analytics.get_portfolio_max_drawdown ->
    get_symphony_max_drawdown -> POSITIVE magnitude.
=> The SAME operator-facing dashboard field flips sign purely on cache warmth.

D8 RULING (team-lead 2026-05-22): positive magnitude is the canonical
convention for the dashboard / operator-facing drawdown surface. The fix is
option (a): the app.py warm-cache branch abs()-converts the quantstats negative
value AT THE BOUNDARY. compute_quantstats_metrics KEEPS its internal negative
convention (it is a standard quant-metrics function — convert at the consumer,
not the producer). port_aggregator stays negative internally. The canonical
convention is documented in code.

This suite:
  - pins the per-module conventions each surface documents (anti-flip guard);
  - DRIVES both the warm-cache and cold-cache app._compute_portfolio_strip
    branches and asserts they agree on POSITIVE magnitude — the D8 contract;
  - requires the canonical convention to be documented + cross-referenced.

All expecteds are derived from the documented conventions and a hand-built loss
scenario — no producer-captured magic numbers.

Tolerance: pytest.approx rel=1e-9 — the drawdown ratios are exact rational
arithmetic; floating-point representation is the only error source.
"""

from __future__ import annotations

import inspect
import re

import pytest

import analytics
import app as app_module
from analytics import compute_quantstats_metrics, get_symphony_max_drawdown

# A single equity decline of exactly 20%: peak 100 -> trough 80.
_PEAK = 100.0
_TROUGH = 80.0
_DRAWDOWN_MAGNITUDE = (_PEAK - _TROUGH) / _PEAK  # 0.20, derived not hardcoded


# ---------------------------------------------------------------------------
# Cache isolation — _compute_portfolio_strip reads a module-level cache.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_account_totals_cache():
    """Reset app._account_totals_cache before and after each test so cache
    warmth is fully controlled per test."""
    app_module._account_totals_cache.clear()
    yield
    app_module._account_totals_cache.clear()


def _minimal_bot_state_with_drawdown() -> dict:
    """A bot_state with two symphonies each carrying a real Composer
    max_drawdown so the cold-cache analytics path produces a non-zero MDD."""
    return {
        f"sym-{i}": {
            "name": f"Symphony {i}",
            "current_value": 1000.0,
            "current_return": 1.5,
            "simple_return": 0.05,
            "net_deposits": 800.0,
            "time_weighted_return": 0.06,
            "max_drawdown": _DRAWDOWN_MAGNITUDE,  # Composer positive decimal
        }
        for i in range(2)
    }


def _seed_shadow_history_for_bot_state(tmp_path, bot_state: dict) -> str:
    """[Added, DE-PERF-WINDOW-TRUTH-001]: post-AC-1, MDD if_held/dry_run are
    genuine windowed peak-to-trough values sourced from shadow_history, NOT
    bot_state['max_drawdown'] -- the D8 tests below need a real shadow_history
    DB backing each symphony to produce a non-None if_held at all."""
    import sqlite3
    from datetime import date, timedelta

    db_file = str(tmp_path / "d8_shadow.db")
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE shadow_history (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "symphony_id TEXT NOT NULL, ts_utc TEXT NOT NULL, trading_day TEXT NOT NULL, "
        "current_return REAL NOT NULL, shadow_return REAL NOT NULL, "
        "is_post_trigger INTEGER NOT NULL DEFAULT 0, position_epoch TEXT)"
    )
    day0 = (date.today() - timedelta(days=3)).isoformat()
    day1 = date.today().isoformat()
    for sym_id in bot_state:
        for trading_day, cr, sr in [(day0, 0.0, 0.0), (day1, -6.0, -6.0)]:
            conn.execute(
                "INSERT INTO shadow_history (symphony_id, ts_utc, trading_day, "
                "current_return, shadow_return, position_epoch) VALUES (?, ?, ?, ?, ?, ?)",
                (sym_id, trading_day + "T20:00:00Z", trading_day, cr, sr, "EPOCH_A"),
            )
    conn.commit()
    conn.close()
    return db_file


# ---------------------------------------------------------------------------
# Per-module convention pins (anti-flip guards).
# ---------------------------------------------------------------------------


class TestQuantstatsMetricsKeepsNegativeConventionInternally:
    """compute_quantstats_metrics keeps max_drawdown <= 0 — D8 explicitly says
    NOT to flip this producer; it is converted at the consumer boundary."""

    def test_quantstats_max_drawdown_is_non_positive_for_a_real_loss(self):
        pytest.importorskip("quantstats", reason="quantstats is an optional dep — skip when absent")
        returns_pct = [1.0, -25.0, 5.0, 3.0]
        metrics = compute_quantstats_metrics(returns_pct)
        assert metrics["max_drawdown"] is not None
        assert metrics["max_drawdown"] <= 0.0, (
            "compute_quantstats_metrics keeps the internal negative convention "
            "(D8: convert at the app.py consumer boundary, not the producer)"
        )


class TestGetSymphonyMaxDrawdownUsesPositiveMagnitude:
    """analytics.get_symphony_max_drawdown is operator-facing and uses the
    canonical POSITIVE magnitude convention (D8)."""

    def test_if_held_lifetime_drawdown_is_positive_magnitude(self):
        """[Updated, DE-PERF-WINDOW-TRUTH-001]: the D8 positive-magnitude
        convention now applies to BOTH if_held (a genuine windowed
        peak-to-trough, honestly None here with no shadow_history DB) and
        the separate 'if_held_lifetime' figure (AC-2) this test now pins --
        the Composer scalar-to-percent conversion this test was originally
        protecting."""
        sym_dict = {"id": "sym-sign", "max_drawdown": _DRAWDOWN_MAGNITUDE}
        result = get_symphony_max_drawdown(sym_dict, bot_state_entry=None)
        assert result["if_held"] is None, (
            "if_held must be the honest None no-data sentinel — no "
            "shadow_history DB backs this fixture-only symphony under the "
            f"AC-1 redefinition; got {result['if_held']!r}"
        )
        assert result["if_held_lifetime"] is not None
        assert result["if_held_lifetime"] > 0.0, (
            "get_symphony_max_drawdown's if_held_lifetime is operator-facing "
            "— D8 canonical is POSITIVE magnitude"
        )
        assert result["if_held_lifetime"] == pytest.approx(_DRAWDOWN_MAGNITUDE * 100.0, rel=1e-9)


# ---------------------------------------------------------------------------
# THE D8 CONTRACT — the operator-facing portfolio-strip MDD must be the SAME
# positive-magnitude sign regardless of cache warmth.
# ---------------------------------------------------------------------------


class TestPortfolioStripDrawdownSignIsCacheWarmthInvariant:
    """app._compute_portfolio_strip must emit portfolio_strip["max_drawdown"]
    ["if_held"] as a POSITIVE magnitude whether the account-totals cache is
    warm or cold. Currently the warm branch is negative (quantstats) and the
    cold branch is positive (analytics) — a live sign flip on one operator
    field."""

    def test_warm_cache_portfolio_mdd_if_held_is_positive_magnitude(self, tmp_path, monkeypatch):
        """WARM cache. [Updated, DE-PERF-WINDOW-TRUTH-001 AC-1]: the exact-
        value pin against the cached quantstats scalar is SUPERSEDED -- that
        pin encoded the audit's #1 defect (pairing a Composer/quantstats
        LIFETIME scalar as the if_held comparison leg). Post-fix, if_held
        must come from analytics.get_portfolio_max_drawdown's genuine
        windowed computation REGARDLESS of cache warmth -- this test now
        asserts the warm-cache scalar has NO EFFECT on if_held (mirrors the
        AC-1 translation-invariance-to-the-wrong-source-regression pattern in
        tests/analytics/test_mdd_window_truth.py, applied at the app.py
        boundary). The POSITIVE-magnitude sign convention itself (D8) is
        still asserted -- that part of the D8 ruling survives the redefinition.
        """
        bot_state = _minimal_bot_state_with_drawdown()
        db_file = _seed_shadow_history_for_bot_state(tmp_path, bot_state)
        monkeypatch.setattr(analytics, "DB_FILE", db_file)

        quantstats_negative_mdd_pct = -_DRAWDOWN_MAGNITUDE * 100.0  # -20.0
        app_module._account_totals_cache["portfolio_mdd"] = quantstats_negative_mdd_pct
        app_module._account_totals_cache["portfolio_value"] = 2000.0

        strip = app_module._compute_portfolio_strip(bot_state)
        mdd = strip.get("max_drawdown")
        assert mdd is not None and mdd.get("if_held") is not None, (
            "warm-cache portfolio strip must produce a max_drawdown.if_held "
            "(a real shadow_history DB now backs both fixture symphonies)"
        )
        assert mdd["if_held"] > 0.0, (
            "D8: the operator-facing portfolio MDD if_held must be a POSITIVE "
            "magnitude regardless of cache warmth."
        )
        assert mdd["if_held"] != pytest.approx(abs(quantstats_negative_mdd_pct), rel=1e-6), (
            f"AC-1 VIOLATION: the warm-cache scalar (abs={abs(quantstats_negative_mdd_pct)}) "
            f"still equals if_held ({mdd['if_held']}) -- per "
            f"docs/audit/MDD-CONSUMER-ENUMERATION-2026-09-03.md's confirmed "
            f"design, this cached scalar is REPURPOSED as AC-2's separate "
            f"portfolio-level lifetime figure (it may still appear in source, "
            f"just no longer assigned to the vs-row's if_held key); if_held "
            f"always comes from analytics.get_portfolio_max_drawdown's "
            f"genuine (redefined) value, regardless of cache warmth."
        )

    def test_cold_cache_portfolio_mdd_if_held_is_positive_magnitude(self, tmp_path, monkeypatch):
        """COLD cache: if_held flows through analytics.get_portfolio_max_drawdown
        which is already positive magnitude. Anti-regression — the D8 fix must
        not disturb the already-correct cold path. [Updated, DE-PERF-WINDOW-
        TRUTH-001]: now DB-backed so if_held is genuinely non-None under AC-1."""
        bot_state = _minimal_bot_state_with_drawdown()
        db_file = _seed_shadow_history_for_bot_state(tmp_path, bot_state)
        monkeypatch.setattr(analytics, "DB_FILE", db_file)
        # cache is empty (cleared by autouse fixture) -> cold path
        strip = app_module._compute_portfolio_strip(bot_state)
        mdd = strip.get("max_drawdown")
        assert mdd is not None and mdd.get("if_held") is not None, (
            "cold-cache portfolio strip must produce a max_drawdown.if_held"
        )
        assert mdd["if_held"] > 0.0, (
            "cold-cache portfolio MDD if_held is already positive magnitude — "
            "must remain so after the D8 fix"
        )

    def test_warm_and_cold_portfolio_mdd_if_held_are_identical(self, tmp_path, monkeypatch):
        """[Updated, DE-PERF-WINDOW-TRUTH-001, supersedes the D8 'agree on
        sign' framing]: post-AC-1, MDD if_held no longer has TWO distinct
        computation branches (warm-cache-scalar vs cold-cache-analytics) --
        it is ALWAYS analytics.get_portfolio_max_drawdown's genuine value.
        'Agreeing on sign' is now the WEAKER, subsumed half of a STRONGER
        invariant: warm and cold must produce the IDENTICAL if_held value
        (not merely the same sign), because cache warmth no longer feeds
        this computation at all."""
        bot_state = _minimal_bot_state_with_drawdown()
        db_file = _seed_shadow_history_for_bot_state(tmp_path, bot_state)
        monkeypatch.setattr(analytics, "DB_FILE", db_file)

        # Cold branch.
        app_module._account_totals_cache.clear()
        cold = app_module._compute_portfolio_strip(bot_state)["max_drawdown"]["if_held"]

        # Warm branch — cache holds an arbitrary scalar that must now be inert
        # for MDD purposes.
        app_module._account_totals_cache.clear()
        app_module._account_totals_cache["portfolio_mdd"] = -_DRAWDOWN_MAGNITUDE * 100.0
        app_module._account_totals_cache["portfolio_value"] = 2000.0
        warm = app_module._compute_portfolio_strip(bot_state)["max_drawdown"]["if_held"]

        assert cold is not None and warm is not None
        assert warm == pytest.approx(cold, abs=1e-9), (
            f"AC-1 VIOLATION: portfolio MDD if_held differs between cold "
            f"({cold}) and warm ({warm}) cache -- if_held must be IDENTICAL "
            f"regardless of _account_totals_cache['portfolio_mdd'] warmth; "
            f"that cache entry no longer feeds this computation at all."
        )
        # D8's positive-magnitude convention still holds.
        assert cold > 0.0 and warm > 0.0, (
            "D8 canonical convention is POSITIVE magnitude on both branches"
        )


# ---------------------------------------------------------------------------
# The canonical convention must be documented in code.
# ---------------------------------------------------------------------------


class TestDrawdownConventionIsDocumentedAndCrossReferenced:
    """AC-4 / D8: the canonical positive-magnitude convention for the
    operator-facing surface must be documented in code, and the opposing
    internal-negative producers must be cross-referenced so a future reader
    cannot miss the deliberate split."""

    def test_get_symphony_max_drawdown_docstring_states_positive_convention(self):
        doc = inspect.getdoc(analytics.get_symphony_max_drawdown) or ""
        assert "positive" in doc.lower() or "magnitude" in doc.lower(), (
            "get_symphony_max_drawdown docstring must state the canonical "
            "POSITIVE-magnitude convention"
        )

    def test_app_portfolio_strip_documents_the_canonical_convention(self):
        """[Updated, DE-PERF-WINDOW-TRUTH-001, aligned to the confirmed
        design in docs/audit/MDD-CONSUMER-ENUMERATION-2026-09-03.md AC-0a
        §C row 1]: the warm-cache `abs(_cached_mdd)` computation is
        REPURPOSED, not deleted -- it becomes AC-2's separate portfolio-level
        lifetime figure. What MUST change is its ROLE: it must no longer be
        assigned to the vs-row's `if_held` key (the exact defect the audit
        found). This test checks the PRECISE structural pattern -- the
        literal `"if_held": abs(_cached_mdd)` dict-key assignment -- rather
        than a blanket absence of `abs(_cached_mdd)` anywhere in source
        (which would incorrectly fail a correct implementation that keeps
        the expression under a new key name)."""
        src = inspect.getsource(app_module._compute_portfolio_strip)
        lowered = src.lower()
        assert "max_drawdown" in lowered, (
            "_compute_portfolio_strip must still assemble a max_drawdown "
            "entry on the portfolio strip"
        )
        assert "magnitude" in lowered or "convention" in lowered or "sign" in lowered, (
            "the canonical positive-magnitude drawdown convention must still "
            "be documented with a comment somewhere in _compute_portfolio_strip."
        )
        forbidden_assignment = re.search(r'"if_held"\s*:\s*abs\(\s*_cached_mdd\s*\)', src)
        assert forbidden_assignment is None, (
            "AC-1 VIOLATION: the literal `\"if_held\": abs(_cached_mdd)` "
            "dict-key assignment is still present -- the warm-cache Composer/"
            "quantstats lifetime scalar must no longer be assigned to the "
            "vs-row's if_held key (it may still be COMPUTED for AC-2's "
            "separate lifetime figure, just under a different key)."
        )
