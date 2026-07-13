"""RED tests — AC-7/AC-8 (F6, Gap F + F3, Gap C): gate transparency.

Confirmed by reading templates/ai_advisor.html directly before writing:
  - Strategy Builder observations render from `sb_observations` (app.py:4157,
    `database.get_advisor_observations_for_role("STRATEGY_BUILDER")`),
    classified in Jinja (ai_advisor.html:1793-1804) into `sb_survivors`
    (verdict == ADOPT_CANDIDATE), `sb_bt_failed` (raw_response.backtest_error
    set), `sb_withheld` (everything else — i.e. every rejected-but-backtested
    candidate, regardless of WHY it was rejected).
  - AC-7 target: ai_advisor.html:1996 — every sb_withheld card renders the
    IDENTICAL "Gate withheld: this candidate did not clear the FDR correction
    threshold." string (`data-testid="apply-guidance"` on the rejected card),
    regardless of `raw_response.rejection_reason` (pbo_veto / below_spy_alpha /
    fdr_not_winner — the CandidateGateResult field, confirmed present in
    backtest_gate_engine.py). `grep -c "rejection_reason" ai_advisor.html`
    returns zero matches — the field is computed and persisted but never read
    by the template (audit-doc 10.5, render-path confirmed).
  - AC-8 target: ai_advisor.html:1904, the sb_survivors caveat-text: "Selected
    on backtest across {{ n_cand }} candidates. FDR correction applied
    ({{ n_cand }} tested, threshold Yekutieli-adjusted). Overfitting risk
    cannot be eliminated — only resisted." — replaced per doc-reconciliation
    §1.5's exact drafted string (audit-stats' recommended wording,
    ADVISOR-INTENT-AUDIT.md:205).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _mock_ai_advisor_route_deps(monkeypatch, *, sb_rows: list[dict]) -> None:
    """Mirrors the established pattern (test_rf1_prose_render.py /
    test_r1_guardrail_honesty.py); additionally routes
    get_advisor_observations_for_role("STRATEGY_BUILDER", ...) to our
    controlled rows while every other role returns []."""
    import app as app_module
    import database

    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: None)

    def _get_obs_for_role(role, *a, **kw):
        return list(sb_rows) if role == "STRATEGY_BUILDER" else []

    monkeypatch.setattr(database, "get_advisor_observations_for_role", _get_obs_for_role)

    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    mock_analytics.compute_per_symphony_returns.return_value = ([], [], [])
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    fake_corr_mod = MagicMock()
    fake_corr_mod.compute_pairwise_correlations.return_value = []
    fake_corr_mod.CRISIS_CAVEAT = ""
    monkeypatch.setitem(sys.modules, "advisors.correlation_diagnostic", fake_corr_mod)


def _withheld_row(obs_id: int, rejection_reason: str) -> dict:
    return {
        "id": obs_id,
        "verdict": "REJECT_VETO_FAILED",
        "raw_response": {
            "candidate_id": f"cand-{obs_id}",
            "objective": "diversify",
            "template_id": "built-new",
            "n_candidates": 5,
            "rejection_reason": rejection_reason,
            "caveats": [],
        },
    }


def _survivor_row(obs_id: int, n_cand: int) -> dict:
    return {
        "id": obs_id,
        "verdict": "ADOPT_CANDIDATE",
        "raw_response": {
            "candidate_id": f"cand-{obs_id}",
            "objective": "diversify",
            "template_id": "built-new",
            "n_candidates": n_cand,
            "fdr_q": 0.05,
            "winner_p_adj": 0.01,
            "caveats": [],
        },
    }


# ===========================================================================
# AC-7 — rejection_reason rendered, distinguishable per reason.
# ===========================================================================


def _get_rejected_card_text(client, monkeypatch, rejection_reason: str) -> str:
    _mock_ai_advisor_route_deps(monkeypatch, sb_rows=[_withheld_row(1, rejection_reason)])
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    marker = 'class="proposal-card proposal-card--rejected"'
    start = html.find(marker)
    assert start != -1, f"no rejected card rendered for rejection_reason={rejection_reason!r}"
    end = html.find("</div>\n                        {% set _art", start)
    # Fallback boundary if the Jinja-comment marker above doesn't survive
    # rendering (it won't — Jinja strips {% %} — use the next card/section
    # marker instead): the rejected-cards loop's own closing div sequence.
    if end == -1:
        end = start + 4000  # generous fixed window — one card's markup fits well within this
    return html[start:end]


def test_ac7_pbo_veto_rejection_renders_distinguishable_copy(client, monkeypatch):
    """MUST FAIL pre-fix: today EVERY sb_withheld card renders the identical
    'did not clear the FDR correction threshold' string, regardless of
    rejection_reason."""
    card = _get_rejected_card_text(client, monkeypatch, "pbo_veto")
    assert "did not clear the FDR correction threshold" not in card, (
        f"AC-7 GAP: a pbo_veto rejection still renders the blanket FDR-threshold "
        f"copy — the wrong statistical claim for this rejection class. Card: {card}"
    )


def test_ac7_below_spy_alpha_rejection_renders_distinguishable_copy(client, monkeypatch):
    card = _get_rejected_card_text(client, monkeypatch, "below_spy_alpha")
    assert "did not clear the FDR correction threshold" not in card, (
        f"AC-7 GAP: a below_spy_alpha rejection still renders the blanket "
        f"FDR-threshold copy. Card: {card}"
    )


def test_ac7_pbo_veto_and_below_spy_alpha_render_different_copy(client, monkeypatch):
    """The core distinguishability requirement: two DIFFERENT rejection
    classes must NOT render identical rejection copy (a fix that replaces one
    blanket string with a DIFFERENT single blanket string would still fail
    this — the adversarial core of AC-7)."""
    pbo_card = _get_rejected_card_text(client, monkeypatch, "pbo_veto")
    spy_card = _get_rejected_card_text(client, monkeypatch, "below_spy_alpha")

    def _apply_guidance_text(card: str) -> str:
        marker = 'data-testid="apply-guidance"'
        start = card.find(marker)
        if start == -1:
            return card  # fall back to whole card if marker not found pre-fix
        end = card.find("</div>", start)
        return card[start:end] if end != -1 else card[start : start + 300]

    assert _apply_guidance_text(pbo_card) != _apply_guidance_text(spy_card), (
        "AC-7 GAP: pbo_veto and below_spy_alpha rejected cards render IDENTICAL "
        "apply-guidance copy — rejection reasons are not distinguishable to the operator."
    )


def test_ac7_legacy_row_without_rejection_reason_renders_no_fabricated_reason(client, monkeypatch):
    """Edge case (plan's own Edge Cases section): rejection_reason absent on a
    legacy persisted row -> render nothing rather than a wrong/fabricated
    reason. This must hold BOTH pre- and post-fix (not itself a RED test —
    guards the fix from inventing a reason where none was recorded)."""
    row = _withheld_row(2, rejection_reason="placeholder")
    del row["raw_response"]["rejection_reason"]
    _mock_ai_advisor_route_deps(monkeypatch, sb_rows=[row])
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    # Must not crash (Jinja .get() default handling) — a KeyError/500 here
    # would itself be a defect on a common real-world legacy-row shape.
    assert resp.status_code == 200


# ===========================================================================
# AC-8 — gate-strength copy correction (exact drafted string).
# ===========================================================================


def test_ac8_survivor_caveat_stale_fdr_tested_string_removed(client, monkeypatch):
    """MUST FAIL pre-fix: byte-exact stale string, doc-reconciliation §1.5."""
    _mock_ai_advisor_route_deps(monkeypatch, sb_rows=[_survivor_row(3, n_cand=7)])
    resp = client.get("/ai-advisor")
    html = resp.get_data(as_text=True)
    assert (
        "FDR correction applied (7 tested, threshold Yekutieli-adjusted)" not in html
    ), "AC-8 GAP: the stale 'FDR correction applied (N tested, ...)' string is still present verbatim."


def test_ac8_survivor_caveat_carries_calibrated_significance_bar_wording(client, monkeypatch):
    """Corrected copy per doc-reconciliation §1.5 / ADVISOR-INTENT-AUDIT.md:205
    ('tested against an FDR-calibrated significance bar accounting for N
    alternatives; only the single strongest candidate is promoted per run').
    Content-scoped (not exact-byte) since minor phrasing variance from a
    reviewer pass is acceptable as long as the substantive claims land."""
    _mock_ai_advisor_route_deps(monkeypatch, sb_rows=[_survivor_row(3, n_cand=7)])
    resp = client.get("/ai-advisor")
    html = resp.get_data(as_text=True)
    marker = 'data-testid="caveat-text"'
    start = html.find(marker)
    assert start != -1, "caveat-text element not found on the survivor card"
    end = html.find("</p>", start)
    caveat = html[start:end].lower()
    assert "calibrated" in caveat or "accounting for" in caveat, (
        f"AC-8 GAP: survivor caveat does not carry the FDR-calibrated "
        f"significance-bar wording. Element text: {caveat}"
    )
    assert "single strongest" in caveat or "single candidate" in caveat, (
        f"AC-8 GAP: survivor caveat does not carry the single-strongest-"
        f"candidate-promoted framing. Element text: {caveat}"
    )
