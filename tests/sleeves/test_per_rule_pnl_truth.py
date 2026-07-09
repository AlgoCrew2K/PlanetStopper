"""RED — audit findings #7 and #8 (HIGH): realized P&L truth on the two
operator surfaces.

#7: app.py's per-rule panel fold filters the order history to ONE rule's
orders and reconstructs a ledger from just those — a sell attributed to a
DIFFERENT rule than the buy (the DESIGN case: a defensive/go_to_cash rule
selling an entry rule's position) raises InsufficientPositionError inside the
fold, which `except Exception: rule_realized_pnl = 0.0` swallows. Every rule
then renders $0.00 exactly when the defensive machinery works as intended.

#8: reporting.py's EOD digest hardcodes `"realized_pnl_usd": 0.0` (the
docstring calls it an honest placeholder — stale: the panel wires a fold) and
renders it as a real dollar figure (`realized $+0.00`), so dashboard and
digest disagree whenever any same-rule round trip exists, and both lie in the
cross-rule case.

Pinned contract:
  * the panel must expose the sleeve's TRUE realized P&L — either a
    sleeve-level realized figure equal to ledger truth, or per-rule numeric
    attributions that SUM to ledger truth (fill-level provenance). All-$0.00
    for a genuinely profitable sleeve is never acceptable ("$0.00" is a value
    claim, audit #7).
  * the digest derives realized P&L from the same data as the panel — never
    a constant — and the two surfaces agree.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import app as app_module
import database
import reporting
import sleeves.ledger as ledger

_ET = ZoneInfo("America/New_York")


def _seed_filled_order(
    sleeve_id: int,
    rule_id: int,
    *,
    side: str,
    qty: float,
    price: float,
) -> None:
    """One fully-filled order + its fill row, attributed to rule_id — the
    exact rows the armed engine writes (actions._place_order_with_reservation
    + poll_and_apply_fills)."""
    client_order_id = f"pnl-{uuid.uuid4().hex}"
    order_pk = database.insert_sleeve_order(
        client_order_id=client_order_id,
        sleeve_id=sleeve_id,
        rule_id=rule_id,
        symbol="SPY",
        side=side,
        qty=qty,
        status="filled",
        reserved_price=price if side == "buy" else None,
    )
    database.insert_sleeve_fill(
        order_id=order_pk,
        fill_price=price,
        filled_qty=qty,
        filled_at="2026-07-08T14:35:00Z",
    )


def _sleeve_truth_realized(sleeve_id: int, capital_usd: float) -> float:
    return ledger.reconstruct_from_history(
        capital_usd, database.get_sleeve_order_history(sleeve_id)
    ).realized_pnl_usd


def _panel_entry(sleeve_id: int) -> dict:
    panel = app_module._build_sleeves_panel_context()
    return next(s for s in panel if s["id"] == sleeve_id)


def _surface_exposes_truth(entry: dict, truth: float) -> bool:
    """True iff this panel/digest sleeve entry carries the sleeve's real
    realized P&L — as a sleeve-level figure, or as per-rule numeric values
    summing to it (both GREEN designs the PM ruling allows)."""
    sleeve_level = entry.get("realized_pnl_usd")
    if isinstance(sleeve_level, (int, float)) and sleeve_level == pytest.approx(truth, abs=1e-6):
        return True
    rule_values = [
        r.get("realized_pnl_usd")
        for r in entry.get("rules", [])
        if isinstance(r.get("realized_pnl_usd"), (int, float))
    ]
    # abs 1e-6 dollars: float bookkeeping of short-decimal seeded values.
    return bool(rule_values) and sum(rule_values) == pytest.approx(truth, abs=1e-6)


def _make_cross_rule_profitable_sleeve() -> tuple[int, float, float]:
    """The DESIGN case: entry rule buys, the defensive rule sells the same
    position higher. Returns (sleeve_id, capital, truth_realized)."""
    capital_usd = 10_000.0
    sleeve_id = database.create_sleeve(
        f"pnl-cross-{uuid.uuid4().hex}", capital_usd, envelope_json="{}"
    )
    entry_rule = database.create_sleeve_rule(sleeve_id, "entry-rule", json_doc="{}")
    defensive_rule = database.create_sleeve_rule(sleeve_id, "defensive-rule", json_doc="{}")
    _seed_filled_order(sleeve_id, entry_rule, side="buy", qty=10.0, price=100.0)
    _seed_filled_order(sleeve_id, defensive_rule, side="sell", qty=10.0, price=150.0)

    truth = _sleeve_truth_realized(sleeve_id, capital_usd)
    assert truth > 0, "fixture sanity: this sleeve genuinely made money"
    return sleeve_id, capital_usd, truth


class TestPanelPnlTruth:
    def test_cross_rule_exit_never_collapses_panel_pnl_to_all_zeros(self):
        sleeve_id, _capital, truth = _make_cross_rule_profitable_sleeve()

        entry = _panel_entry(sleeve_id)

        assert _surface_exposes_truth(entry, truth), (
            f"the sleeve realized ${truth:+.2f} (ledger truth), but the panel "
            f"exposes sleeve-level={entry.get('realized_pnl_usd')!r} / "
            f"per-rule={[r.get('realized_pnl_usd') for r in entry['rules']]} — "
            f"the per-rule fold swallows the cross-rule sell "
            f"(InsufficientPositionError -> except -> 0.0) exactly when the "
            f"defensive machinery works as designed (audit #7); $0.00 is a "
            f"value claim, not a degraded state"
        )


class TestDigestPnlTruth:
    def test_digest_realized_pnl_is_derived_from_fills_never_a_constant(self):
        sleeve_id, _capital, truth = _make_cross_rule_profitable_sleeve()
        sleeve_name = database.get_sleeve(sleeve_id)["name"]

        today_str = datetime.now(_ET).strftime("%Y-%m-%d")
        summaries = reporting._build_sleeve_digest_summaries(today_str)
        digest_entry = next(s for s in summaries if s["name"] == sleeve_name)

        assert _surface_exposes_truth(digest_entry, truth), (
            f"the EOD digest must report the sleeve's real realized P&L "
            f"(${truth:+.2f}); reporting.py hardcodes realized_pnl_usd=0.0 "
            f"for every rule, always (audit #8) — a fabricated dollar figure "
            f"in an operator report"
        )

    def test_panel_and_digest_agree_on_a_same_rule_round_trip(self):
        """The simplest honest case — buy and sell attributed to the SAME
        rule. The panel's fold already computes this correctly; the digest
        must show the SAME number (shared fold), not $0.00."""
        capital_usd = 10_000.0
        sleeve_id = database.create_sleeve(
            f"pnl-same-{uuid.uuid4().hex}", capital_usd, envelope_json="{}"
        )
        rule_id = database.create_sleeve_rule(sleeve_id, "round-trip-rule", json_doc="{}")
        _seed_filled_order(sleeve_id, rule_id, side="buy", qty=10.0, price=100.0)
        _seed_filled_order(sleeve_id, rule_id, side="sell", qty=10.0, price=150.0)
        truth = _sleeve_truth_realized(sleeve_id, capital_usd)
        assert truth > 0

        panel_rules = _panel_entry(sleeve_id)["rules"]
        panel_value = next(r for r in panel_rules if r["id"] == rule_id)["realized_pnl_usd"]
        assert panel_value == pytest.approx(truth, abs=1e-6), (
            "fixture sanity: the same-rule round trip is the case the panel "
            "fold already handles — if this fails, the panel regressed"
        )

        sleeve_name = database.get_sleeve(sleeve_id)["name"]
        today_str = datetime.now(_ET).strftime("%Y-%m-%d")
        summaries = reporting._build_sleeve_digest_summaries(today_str)
        digest_entry = next(s for s in summaries if s["name"] == sleeve_name)
        digest_values = [
            r.get("realized_pnl_usd")
            for r in digest_entry["rules"]
            if isinstance(r.get("realized_pnl_usd"), (int, float))
        ]

        assert digest_values and sum(digest_values) == pytest.approx(panel_value, abs=1e-6), (
            f"dashboard and digest disagree on the same quantity: panel says "
            f"${panel_value:+.2f}, digest rule values are {digest_values} — "
            f"the digest must share the panel's fold (audit #8), never print "
            f"a formatted dollar from a constant"
        )

    def test_rendered_digest_line_never_fabricates_plus_zero_dollars(self):
        """Rendering-level pin: the digest LINE for a profitable rule must not
        read 'realized $+0.00' — that string is the exact live symptom the
        audit reproduced."""
        capital_usd = 10_000.0
        sleeve_id = database.create_sleeve(
            f"pnl-render-{uuid.uuid4().hex}", capital_usd, envelope_json="{}"
        )
        rule_id = database.create_sleeve_rule(sleeve_id, "profitable-rule", json_doc="{}")
        _seed_filled_order(sleeve_id, rule_id, side="buy", qty=10.0, price=100.0)
        _seed_filled_order(sleeve_id, rule_id, side="sell", qty=10.0, price=150.0)
        assert _sleeve_truth_realized(sleeve_id, capital_usd) > 0

        today_str = datetime.now(_ET).strftime("%Y-%m-%d")
        section = reporting.build_sleeves_digest_section(
            reporting._build_sleeve_digest_summaries(today_str)
        )
        rule_line = next(line for line in section.splitlines() if "profitable-rule" in line)

        assert "$+0.00" not in rule_line, (
            f"the digest renders {rule_line!r} for a genuinely profitable "
            f"rule — 'realized $+0.00' from a hardcoded constant is a "
            f"fabricated operator-facing dollar figure (audit #8)"
        )
