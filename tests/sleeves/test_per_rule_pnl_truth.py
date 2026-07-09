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
    # filled_at derives from the test's own runtime (review gap G6): these
    # tests assert LIFETIME semantics, so the seeds must never accidentally
    # depend on a fixed calendar date if the digest ever becomes day-scoped.
    database.insert_sleeve_fill(
        order_id=order_pk,
        fill_price=price,
        filled_qty=qty,
        filled_at=datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    summing to it (both GREEN designs the PM ruling allows).

    Escape hatch closed (review gap G4): a truthful sleeve-level figure does
    NOT excuse per-rule entries that still render numeric $0.00 against a
    nonzero truth — per-rule numerics must be honest attributions (sum to
    truth) or explicit non-numeric n/a markers, never residual value claims.
    """
    rule_values = [
        r.get("realized_pnl_usd")
        for r in entry.get("rules", [])
        if isinstance(r.get("realized_pnl_usd"), (int, float))
    ]
    # abs 1e-6 dollars: float bookkeeping of short-decimal seeded values.
    rules_sum_to_truth = bool(rule_values) and sum(rule_values) == pytest.approx(truth, abs=1e-6)
    sleeve_level = entry.get("realized_pnl_usd")
    if isinstance(sleeve_level, (int, float)) and sleeve_level == pytest.approx(truth, abs=1e-6):
        return rules_sum_to_truth or not rule_values
    return rules_sum_to_truth


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


class TestAttributionFoldContract:
    """Ratified interface pins (sf-eng <-> sf-dash <-> sf-test convergence,
    2026-07-09; supersedes the sell-side proposal): ONE pure fold in
    sleeves.ledger — attribute_realized_fills(order_history) — with BUY-side
    attribution: each sell fill's realized delta lands on the rule that
    OPENED the lots being closed. Sell-side attribution would pin every
    entry-only rule at $0.00 forever (today's bug in mirror image) and break
    the AC-11 churn brake + AC-20-24 track record, both of which need net
    realized P&L per ENTRY rule. Panel, digest, and churn brake all consume
    this one fold."""

    def _fold(self):
        fold = getattr(ledger, "attribute_realized_fills", None)
        assert fold is not None, (
            "sleeves.ledger.attribute_realized_fills does not exist — the "
            "ratified shared fold (fill-level realized attribution records) "
            "is the single source panel/digest/churn-brake agreed to consume"
        )
        return fold

    def test_realized_delta_attributes_to_the_opening_entry_rule(self):
        sleeve_id, capital_usd, truth = _make_cross_rule_profitable_sleeve()
        entry_rule_id = next(
            r["id"]
            for r in database.get_sleeve_rules_for_sleeve(sleeve_id)
            if r["name"] == "entry-rule"
        )

        records = self._fold()(database.get_sleeve_order_history(sleeve_id))

        assert records, "the profitable round trip must yield attribution records"
        # abs 1e-6 dollars: float bookkeeping of short-decimal seeded values.
        assert sum(r.realized_delta_usd for r in records) == pytest.approx(truth, abs=1e-6), (
            "attribution records must sum exactly to the ledger's own "
            "realized_pnl_usd — the fold is an attribution of ledger truth, "
            "never a second P&L computation that can drift from it"
        )
        assert all(r.opening_rule_id == entry_rule_id for r in records), (
            "BUY-side law: the realized delta lands on the rule that OPENED "
            "the position, even though the closing sell order carries the "
            "defensive rule's rule_id"
        )

    def test_panel_shows_entry_rule_pnl_and_defensive_rule_zero(self):
        """Display consequence of the buy-side law, pinned deliberately: the
        defensive closer truthfully shows $0.00 realized (its value is loss
        avoidance), the entry rule shows the realized P&L."""
        sleeve_id, _capital, truth = _make_cross_rule_profitable_sleeve()

        rules_panel = {r["name"]: r for r in _panel_entry(sleeve_id)["rules"]}

        assert rules_panel["entry-rule"]["realized_pnl_usd"] == pytest.approx(truth, abs=1e-6), (
            "the ENTRY rule opened the lots — the realized P&L is its track "
            "record (feeds AC-11 churn math and the AC-20-24 culling record)"
        )
        assert rules_panel["defensive-rule"]["realized_pnl_usd"] == pytest.approx(0.0, abs=1e-6), (
            "the defensive closer realizes nothing of its own under buy-side "
            "attribution — a truthful $0.00, distinct from the swallowed "
            "$0.00 this cycle removed"
        )

    def test_pro_rata_sells_across_rule_buckets_then_full_flatten_folds_cleanly(self):
        """Ratified float-exactness pin (remainder allocation): naive
        per-bucket proration drifts a few ulps per sell, so a history ending
        in a FULL flatten could spuriously raise on its final sell — i.e.
        render 'n/a' on a history reconstruct_from_history folds cleanly.
        Two rule buckets, several partial sells, then a flatten of exactly
        the remaining qty: must fold without raising and still sum to ledger
        truth."""
        capital_usd = 50_000.0
        sleeve_id = database.create_sleeve(
            f"pnl-flatten-{uuid.uuid4().hex}", capital_usd, envelope_json="{}"
        )
        rule_a = database.create_sleeve_rule(sleeve_id, "entry-a", json_doc="{}")
        rule_b = database.create_sleeve_rule(sleeve_id, "entry-b", json_doc="{}")
        closer = database.create_sleeve_rule(sleeve_id, "closer", json_doc="{}")

        # Odd quantities/prices chosen so pro-rata shares are non-terminating
        # binary fractions (7/40ths etc.) — the ulp-drift shape.
        _seed_filled_order(sleeve_id, rule_a, side="buy", qty=10.0, price=101.37)
        _seed_filled_order(sleeve_id, rule_b, side="buy", qty=30.0, price=109.83)
        _seed_filled_order(sleeve_id, closer, side="sell", qty=7.0, price=111.11)
        _seed_filled_order(sleeve_id, closer, side="sell", qty=11.0, price=95.55)
        _seed_filled_order(sleeve_id, closer, side="sell", qty=22.0, price=105.01)  # full flatten

        history = database.get_sleeve_order_history(sleeve_id)
        truth = ledger.reconstruct_from_history(capital_usd, history).realized_pnl_usd

        records = self._fold()(history)  # must not raise on the exact flatten

        # abs 1e-6 dollars: the fold may order float operations differently
        # from reconstruct_from_history; only representation error is allowed.
        assert sum(r.realized_delta_usd for r in records) == pytest.approx(truth, abs=1e-6), (
            "after remainder-allocated sells and a full flatten, the "
            "attribution records must still sum to the ledger's realized "
            "P&L exactly"
        )
        assert {r.opening_rule_id for r in records} == {rule_a, rule_b}, (
            "both entry buckets were drawn down — both must appear in the "
            "attribution; the closer never does (buy-side law)"
        )

    def test_fold_failure_renders_na_never_zero_dollars(self):
        """A history the fold cannot honestly attribute (over-sell beyond
        held qty — mirrors apply_fill's InsufficientPositionError) must
        surface as an explicit n/a marker on panel AND digest, never the
        value claim $0.00."""
        capital_usd = 10_000.0
        sleeve_id = database.create_sleeve(
            f"pnl-oversell-{uuid.uuid4().hex}", capital_usd, envelope_json="{}"
        )
        rule_id = database.create_sleeve_rule(sleeve_id, "oversell-rule", json_doc="{}")
        _seed_filled_order(sleeve_id, rule_id, side="buy", qty=5.0, price=100.0)
        _seed_filled_order(sleeve_id, rule_id, side="sell", qty=9.0, price=110.0)  # > held

        panel_rules = _panel_entry(sleeve_id)["rules"]
        panel_value = next(r for r in panel_rules if r["id"] == rule_id).get("realized_pnl_usd")
        assert not isinstance(panel_value, (int, float)), (
            f"an unattributable history must render an explicit non-numeric "
            f"n/a marker; the panel shows {panel_value!r} — ANY numeric here "
            f"is fabricated (no truth is computable), and $0.00 specifically "
            f"is the exact swallow this cycle removed (audit #7)"
        )

        today_str = datetime.now(_ET).strftime("%Y-%m-%d")
        summaries = reporting._build_sleeve_digest_summaries(today_str)
        digest_entry = next(
            s for s in summaries if s["name"] == database.get_sleeve(sleeve_id)["name"]
        )
        digest_value = next(iter(digest_entry["rules"])).get("realized_pnl_usd")
        assert not isinstance(digest_value, (int, float)), (
            f"the digest must carry the same non-numeric n/a marker; got {digest_value!r}"
        )
