"""RED — BL-12 (audit #118 D7, docs/audit/TWO-WEEK-REVIEW-2026-08-04.md §4/§6):
undisclosed cash-flow-sensitive basis on the dashboard's "Cumulative ·
lifetime" comparison row.

THE BUG: ``_account_totals_cache["portfolio_cr"] = data["simple_return"] *
100.0`` (app.py:844, deliberately matching Composer's own displayed "Total
return" per the existing code comment at :840-843 — cash-flow-sensitive,
diverges from time-weighted-return by ~5pp when cash flows exist). The
dashboard's "Cumulative · lifetime" label (templates/index.html:952,
class="vs-row-label") does not disclose this basis, so an operator
benchmarking against a TWR figure elsewhere could be misled.

THE FIX (AC-20): the label gains a tooltip/disclosure stating the figure is
Composer's own cash-flow-sensitive "Total return" convention (simple_return),
not a time-weighted return — matching the existing code comment's own
language ("~5 pp when cash flows exist").

AC-21: display/disclosure-only — zero change to the underlying portfolio_cr
VALUE computation (app.py:844).

Disambiguation from BL-4 (DE-AUDIT-BL4-001, same-day prior cycle): that cycle
added a `title` to the ADJACENT `comp-cumulative-bot-text` VALUE span
(explaining the Bot column is a shadow-history simulation, not realized
P&L) — a DIFFERENT element with a DIFFERENT message. AC-20's target is the
ROW LABEL (`.vs-row-label`), never that value span; this file's assertions
are keyed on the label element specifically so the two additions can never
collide or be mistaken for one another.
"""

from __future__ import annotations

import re

import pytest

import app as app_module

_CASH_FLOW_KEYWORDS = ("cash flow", "total return")


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _vs_row_label_blocks(html: str) -> list[str]:
    """Each `<span class="vs-row-label">...</span>` block, INCLUDING any
    sibling disclosure markup immediately following it up to the next
    `vs-val`/`vs-delta` span — a generous-but-bounded window so a title
    attribute on the label itself OR on an adjacent inline disclosure
    element (e.g. an info-icon span, matching the existing
    today-held-footnote-marker pattern at templates/index.html:920) is
    captured either way, without slurping the whole row (which would also
    catch the value spans' OWN unrelated title attributes, e.g. BL-4's
    comp-cumulative-bot-text title)."""
    return re.findall(
        r'<span class="vs-row-label">.*?</span>(?:\s*<span[^>]*>.*?</span>)?',
        html,
        flags=re.DOTALL,
    )


class TestCumulativeLifetimeRowDisclosesCashFlowBasis:
    def test_cumulative_row_label_carries_cash_flow_disclosure(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        blocks = _vs_row_label_blocks(html)
        cumulative_blocks = [b for b in blocks if "Cumulative" in b and "lifetime" in b]
        assert cumulative_blocks, (
            "fixture integrity: no 'Cumulative · lifetime' vs-row-label block found "
            "on the rendered dashboard — templates/index.html:952 may have moved"
        )
        block = cumulative_blocks[0]
        lowered = block.lower()
        assert any(kw in lowered for kw in _CASH_FLOW_KEYWORDS), (
            f"the 'Cumulative · lifetime' row label must disclose that the figure "
            f"is Composer's cash-flow-sensitive Total-return convention "
            f"(simple_return), not a time-weighted return — got block:\n{block}"
        )

    def test_other_row_labels_do_not_carry_this_specific_disclosure(self, client):
        """Scoping proof: the disclosure is specific to Cumulative · lifetime
        (the only row whose value is sourced from Composer's cash-flow-
        sensitive simple_return) — Today/Max DD/Ann. Vol must NOT carry the
        same cash-flow-basis language, proving AC-20 didn't land as an
        unscoped global tooltip."""
        resp = client.get("/")
        html = resp.get_data(as_text=True)

        blocks = _vs_row_label_blocks(html)
        other_blocks = [b for b in blocks if not ("Cumulative" in b and "lifetime" in b)]
        assert other_blocks, (
            "fixture integrity: expected other vs-row-label rows (Today/Max DD/Ann. Vol)"
        )

        leaked = [b for b in other_blocks if any(kw in b.lower() for kw in _CASH_FLOW_KEYWORDS)]
        assert not leaked, (
            f"the cash-flow-basis disclosure leaked onto a non-Cumulative row — "
            f"it must be scoped to the 'Cumulative · lifetime' label only:\n{leaked}"
        )
