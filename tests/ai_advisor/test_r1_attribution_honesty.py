"""RED tests — AC-1/AC-2/AC-3 (F4, Gap D): attribution honesty.

Byte-exact current copy confirmed by reading templates/ai_advisor.html
directly before writing these tests (not guessed):
  - AC-1 target: templates/ai_advisor.html:987-989, the page-global
    `<p class="adv-subtitle">Claude-powered suggestion engine — operator must
    approve each change through safety gates</p>` rendered above all 6 tabs
    (`data-testid="advisor-header"`).
  - AC-2 target: templates/ai_advisor.html:1066-1273, the
    `data-testid="market-prism-block"` — the header at :1077-1082 has NO
    model/Claude/Opus mention anywhere in the block today.
  - AC-3 target: templates/ai_advisor.html:1717-1721 (SB run-controls-note,
    inside the strategy-builder tab panel) — current copy: "The advisor
    generates candidate symphonies from templates, backtests each one, and
    applies FDR/Yekutieli multiple-testing correction. Only candidates that
    clear the gate are surfaced as proposals." (git-blamed stale, describes
    the retired T1-T7 template stamper).

Each test asserts BYTE-ABSENCE of the specific stale claim (not merely
presence of new text — a lazy fix could append new copy while leaving the old
misleading claim in place, exactly the trap AC-1's own doc-reconciliation
draft calls out: "TRUE for 3 tabs... FALSE/MISLEADING for 3").
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


def _mock_ai_advisor_route_deps(monkeypatch) -> None:
    """Mirrors tests/ai_advisor/test_rf1_prose_render.py's _mock_route_deps."""
    import app as app_module
    import database

    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: None)
    monkeypatch.setattr(database, "get_advisor_observations_for_role", lambda *a, **kw: [])

    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    mock_analytics.compute_per_symphony_returns.return_value = ([], [], [])
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    fake_corr_mod = MagicMock()
    fake_corr_mod.compute_pairwise_correlations.return_value = []
    fake_corr_mod.CRISIS_CAVEAT = ""
    monkeypatch.setitem(sys.modules, "advisors.correlation_diagnostic", fake_corr_mod)


def _get_html(client, monkeypatch) -> str:
    _mock_ai_advisor_route_deps(monkeypatch)
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _extract_by_testid(html: str, testid: str, *, closing_tag: str) -> str:
    """Marker-based element-text extraction (mirrors the pattern established
    in test_r1_guardrail_honesty.py's _extract_guardrail_note) — scoped
    assertions, not whole-page substring checks, to avoid vacuous passes
    against unrelated markup elsewhere on the large page."""
    marker = f'data-testid="{testid}"'
    start = html.find(marker)
    assert start != -1, f'element with {marker} not found in rendered HTML'
    end = html.find(closing_tag, start)
    assert end != -1, f'element with {marker} has no {closing_tag} in rendered HTML'
    return html[start:end]


# ===========================================================================
# AC-1 — page-global subtitle replaced by per-tab attribution.
# ===========================================================================


def test_ac1_page_global_claude_powered_subtitle_is_removed(client, monkeypatch):
    """MUST FAIL pre-fix: the stale universal-Claude-power claim is still
    rendered above all 6 tabs today."""
    html = _get_html(client, monkeypatch)
    header = _extract_by_testid(html, "advisor-header", closing_tag="</header>")
    assert "Claude-powered suggestion engine" not in header, (
        "AC-1 GAP: the page-global 'Claude-powered suggestion engine' subtitle "
        "is still present in the advisor-header — it must be replaced by "
        "per-tab attribution."
    )


def test_ac1_logic_changes_tab_carries_deterministic_no_ai_label(client, monkeypatch):
    """MUST FAIL pre-fix: the Logic Changes tab nav button carries no
    deterministic/no-AI-reasoning indication today (only an overfitting-risk
    badge, `title='Higher overfitting risk'`)."""
    html = _get_html(client, monkeypatch)
    tab = _extract_by_testid(html, "tab-logic-changes", closing_tag="</button>")
    tab_lower = tab.lower()
    assert "deterministic" in tab_lower or "no ai reasoning" in tab_lower, (
        f"AC-1 GAP: tab-logic-changes does not carry a deterministic/no-AI-"
        f"reasoning label. Element: {tab}"
    )


def test_ac1_asset_swaps_tab_carries_deterministic_no_ai_label(client, monkeypatch):
    """MUST FAIL pre-fix — identical gap to the Logic Changes sibling test."""
    html = _get_html(client, monkeypatch)
    tab = _extract_by_testid(html, "tab-asset-swaps", closing_tag="</button>")
    tab_lower = tab.lower()
    assert "deterministic" in tab_lower or "no ai reasoning" in tab_lower, (
        f"AC-1 GAP: tab-asset-swaps does not carry a deterministic/no-AI-"
        f"reasoning label. Element: {tab}"
    )


def test_ac1_strategy_builder_tab_labeled_generation_model_plus_community_statistically_gated(client, monkeypatch):
    """MUST FAIL pre-fix: the SB tab nav badge today only says 'Highest
    overfitting risk — building from scratch', with no model/community/
    statistically-gated attribution.

    ATTRIBUTION-COHERENCE NOTE (AC-16 conflict with AC-1's literal plan text):
    AC-1's plan text quotes "Opus-generated" as the target SB label, but AC-16
    (operator directive, added mid-cycle) routes SB's build-plan generation
    (build_plan_generator.py) through model_config.get_advisor_suggestion_model()
    — default claude-fable-5, not Opus. AC-16's own text is explicit: "a Fable
    suggestion surface must never display 'Opus'". Since SB generation IS a
    suggestion-producing call site per AC-16's scope, a hardcoded "Opus" label
    would be WRONG the moment AC-16 lands (and is arguably wrong today, given
    AC-16 already exists in the committed plan when this test was written).
    Resolving in favor of the more specific, later directive: this test proves
    the label is ACCESSOR-DRIVEN (reflects whatever model_config resolves,
    monkeypatched to a marker value) rather than hardcoding either "Opus" or
    "Fable" as a literal string — flagged to team-lead as a real plan-text
    inconsistency, not silently resolved without a paper trail."""
    import model_config

    monkeypatch.setattr(model_config, "get_advisor_suggestion_model", lambda: "test-marker-sb-model")
    html = _get_html(client, monkeypatch)
    tab = _extract_by_testid(html, "strategy-builder-tab", closing_tag="</button>")
    assert "test-marker-sb-model" in tab, (
        f"AC-1 GAP: strategy-builder-tab does not reflect the accessor-resolved "
        f"generation model (patched model_config.get_advisor_suggestion_model "
        f"to a marker value and it did not appear) — the label must be "
        f"accessor-driven, never a hardcoded 'Opus'/'Fable' literal (AC-16 "
        f"attribution-coherence requirement). Element: {tab}"
    )
    tab_lower = tab.lower()
    assert "community" in tab_lower, f"AC-1 GAP: strategy-builder-tab does not mention community candidates. Element: {tab}"
    assert "statistically gated" in tab_lower or "statistical" in tab_lower, (
        f"AC-1 GAP: strategy-builder-tab does not mention statistical gating. Element: {tab}"
    )


def test_ac1_chat_and_run_advisor_retain_claude_attribution(client, monkeypatch):
    """Regression guard: the 3 genuinely real-LLM tabs (Chat, Run Advisor,
    Market Prism — Market Prism is covered by the AC-2 tests below) must NOT
    lose their correct Claude attribution while AC-1 fixes the deterministic
    tabs. Chat's badge already says 'Explain-only' with no Claude mention
    today; this pins that a per-tab-attribution pass adds real attribution
    here rather than only removing the page-global claim."""
    html = _get_html(client, monkeypatch)
    chat_tab = _extract_by_testid(html, "tab-chat", closing_tag="</button>")
    assert "claude" in chat_tab.lower() or "opus" in chat_tab.lower(), (
        f"AC-1 GAP: tab-chat carries no Claude/Opus attribution despite Chat "
        f"being a genuine real-LLM feature. Element: {chat_tab}"
    )


# ===========================================================================
# AC-2 — Market Prism block gains explicit model attribution.
# ===========================================================================


def test_ac2_market_prism_block_carries_model_attribution(client, monkeypatch):
    """MUST FAIL pre-fix: the market-prism-block header has zero model/Claude/
    Opus mention today — the ONE genuinely real, best-documented LLM pipeline
    in the suite is the LEAST attributed surface (audit F4's inverted-
    attribution finding)."""
    html = _get_html(client, monkeypatch)
    block = _extract_by_testid(html, "market-prism-block", closing_tag='data-testid="advisor-controls"')
    block_lower = block.lower()
    assert "opus" in block_lower or "claude" in block_lower, (
        f"AC-2 GAP: market-prism-block carries no model attribution anywhere "
        f"in its rendered output."
    )


def test_ac2_market_prism_attribution_reflects_resolved_model_accessor_not_hardcoded(client, monkeypatch):
    """Accessor-monkeypatch proof (mirrors the AC-16 technique): patching
    ai_advisor.resolve_advisor_model() must change the rendered attribution —
    proves the badge reads a live accessor value, not a hardcoded literal
    string baked into the template."""
    import ai_advisor

    monkeypatch.setattr(ai_advisor, "resolve_advisor_model", lambda: "test-marker-prism-model")
    html = _get_html(client, monkeypatch)
    block = _extract_by_testid(html, "market-prism-block", closing_tag='data-testid="advisor-controls"')
    assert "test-marker-prism-model" in block, (
        f"AC-2 GAP: patching resolve_advisor_model() did not change the "
        f"rendered Market Prism attribution — the badge is not accessor-driven."
    )


# ===========================================================================
# AC-3 — SB run-controls-note stale "from templates" copy corrected.
# ===========================================================================


def test_ac3_sb_run_controls_note_stale_from_templates_copy_removed(client, monkeypatch):
    """MUST FAIL pre-fix: byte-absence check on the exact stale string
    (git-blamed 7908d77b0, describes the retired T1-T7 template stamper)."""
    html = _get_html(client, monkeypatch)
    assert (
        "The advisor generates candidate symphonies from templates, backtests"
        not in html
    ), "AC-3 GAP: the stale 'generates candidate symphonies from templates' copy is still present verbatim."


def test_ac3_sb_run_controls_note_mentions_generation_model_and_community_and_significance_bar(client, monkeypatch):
    """Corrected copy per doc-reconciliation §1.3: generation-model attribution
    + Atlas community sourcing + FDR/Yekutieli-calibrated significance bar,
    with single-strongest-candidate winner-take-all framing (not plural
    'candidates ... are surfaced').

    Same AC-16 attribution-coherence resolution as the SB tab test above:
    doc-reconciliation's drafted copy says "via Claude Opus", but AC-16 routes
    SB generation through model_config.get_advisor_suggestion_model()
    (default claude-fable-5) — asserting a hardcoded "Opus" substring here
    would itself become a false claim the moment AC-16 lands. Accessor-driven
    check instead."""
    import model_config

    monkeypatch.setattr(model_config, "get_advisor_suggestion_model", lambda: "test-marker-sb-model")
    html = _get_html(client, monkeypatch)
    run_controls_marker = 'class="run-controls-note"'
    start = html.find(run_controls_marker)
    assert start != -1, "run-controls-note element not found in rendered HTML"
    end = html.find("</p>", start)
    assert end != -1
    note = html[start:end]
    assert "test-marker-sb-model" in note, (
        f"AC-3 GAP: run-controls-note does not reflect the accessor-resolved "
        f"generation model — must be accessor-driven, never a hardcoded model "
        f"name literal (AC-16 attribution-coherence requirement). Element text: {note}"
    )
    note_lower = note.lower()
    assert "community" in note_lower or "atlas" in note_lower, (
        f"AC-3 GAP: run-controls-note does not mention the Atlas community library. Element text: {note_lower}"
    )
    assert "single" in note_lower or "strongest" in note_lower, (
        f"AC-3 GAP: run-controls-note does not correct the plural 'candidates are "
        f"surfaced' framing to the actual single-winner-per-run semantics. Element text: {note_lower}"
    )
