"""RED tests — feature-plans/frontrunner-proposal-identity.md (AC-4/5/6-render/7-render/9).

Route + template coverage for the Frontrunner Builder honest-identity cycle.
Pure-helper / persist-site / approve-path coverage lives in
tests/advisors/test_frontrunner_proposal_identity.py.

Mirrors tests/app/test_frontrunner_builder_template.py's exact established
pattern (_make_pending_row / _render_advisor_page / _panel_source helpers,
duplicated locally per that file's own documented convention rather than
cross-imported) so this file's fixtures, mocking strategy, and data-testid
discipline are consistent with the rest of the Frontrunner Builder suite.

HANDOFF CONTRACT (Section B, fpi-impl-dashboard — app.py + templates/ai_advisor.html ONLY):

  1. app.py `ai_advisor_tab()`'s existing frontrunner_proposals prefetch loop
     (the block starting "Frontrunner Builder panel: prefetch pending
     frontrunner_proposals rows", ~app.py:5900-5928) gains, per row `_fr_p`:

     a. Incumbent display-name resolution — scan `database.load_state()`
        for a sym_dict whose KEY == `_fr_p["symphony_id"]`, take
        `sym_dict["name"]`; fall back to `_fr_p["symphony_id"]` itself when
        no match (mirrors the SAME resolution pattern used in
        advisors.frontrunner_builder.approve_frontrunner_proposal — do the
        `database.load_state()` call ONCE outside the per-row loop, not
        once per row). Stamp the result onto `_fr_p["_incumbent_display_name"]`.
        Template renders it via `{{ p._incumbent_display_name | e }}` — never
        `| safe` (AC-9).

     b. Overlay-tree preview — mirrors the EXISTING candidate_tree_preview
        transform exactly (same json.dumps(indent=2) + truncate pattern),
        but reads from `metrics_json["overlay_tree"]` (do NOT pop it from
        `p["metrics_json"]` — the template still needs `m.replaced_node_id`/
        `m.overlay_summary` accessible via the existing `{% set m =
        p.metrics_json or {} %}` pattern already in the template). New local
        bound constant `_FR_OVERLAY_PREVIEW_MAX_CHARS = 4000` (mirrors the
        existing `_FR_TREE_PREVIEW_MAX_CHARS`). Stamp the bounded string onto
        `_fr_p["overlay_tree_preview"]`.

     c. Defensive non-dict-metrics_json guard: if `_fr_p["metrics_json"]` is
        not a dict (e.g. a parsed JSON list — AC-6 edge case), stamp
        `_fr_p["metrics_json"] = {}` before the template ever sees it, so
        the template's `{% set m = p.metrics_json or {} %}` and every
        `m.xxx` access stays safe — the whole route must still return 200,
        never a 500.

  2. templates/ai_advisor.html, frontrunner_builder-source card body (inside
     the existing `{% if is_fr %}` node-count-delta block area, ~:2299-2303):

     a. Identity line (new testid `data-testid="fr-incumbent-name"`):
        "Full standalone copy of {{ p._incumbent_display_name | e }} with one
        new frontrunner overlay" (AC-4).

     b. Explainer line: "Approve creates a NEW undeployed Composer symphony —
        the incumbent is never modified." (AC-4). No new testid required
        (plain text assertion is sufficient) but may share the identity
        line's container.

     c. Overlay summary line (new testid `data-testid="fr-overlay-summary"`):
        renders `m.overlay_summary` when present, else the exact honest
        fallback text "overlay not recorded for this proposal" (AC-6).

     d. NEW second collapsible `<details data-testid="fr-overlay-preview">`
        (same collapsed-by-default, no `open` attribute pattern as the
        existing `fr-raw-preview` block — mirrors
        TestCollapsedRawPreview in test_frontrunner_builder_template.py
        exactly), wrapping `{{ p.overlay_tree_preview or '(unavailable)' }}`.
        MUST be a DISTINCT element from the existing `fr-raw-preview`
        `<details>` (AC-5 — "rendered DISTINCT from the existing full-
        candidate preview").

     e. The EXISTING `fr-raw-preview` `<details><summary>` LABEL TEXT (not
        the testid — that stays `fr-raw-preview` on BOTH source branches)
        must BRANCH by `proposal_source` (team-lead finding, mid-cycle):
          - `is_fr` (frontrunner_builder-source): "Full spliced symphony
            (preview)"
          - non-`is_fr` (strategy_builder_retrofit-source): "Proposed
            symphony (preview)" — and the word "spliced" must NEVER appear
            anywhere on a retrofit card. A retrofit candidate is FROM-SCRATCH,
            not spliced from the incumbent — labeling its preview "spliced"
            would be the same category of dishonesty this feature exists to
            fix.

  3. templates/ai_advisor.html, strategy_builder_retrofit-source card body
     (the `{% else %}`/non-`is_fr` branch):

     a. Identity line: "From-scratch symphony proposed by Strategy Builder —
        does not contain the incumbent's logic." (AC-7). Must NEVER render
        the frontrunner "Full standalone copy of" wording inside a retrofit
        card.

     b. The `fr-raw-preview` `<details>` on THIS branch uses the "Proposed
        symphony (preview)" label per 2(e) above — do not reuse the
        `is_fr`-branch's "Full spliced symphony (preview)" text here.

  4. All new fields (`_incumbent_display_name`, `overlay_tree_preview`,
     `overlay_summary` via `m.overlay_summary`) render through Jinja's
     default autoescape — NEVER `| safe`, matching the existing
     `test_frontrunner_panel_source_contains_no_safe_filter` static scan
     (which already covers the WHOLE `tab-panel-frontrunner-builder` block,
     so new markup inherits that guard automatically — re-run it as a
     regression check, do not duplicate it here).

TEST EXECUTION: python -m pytest tests/app/test_frontrunner_proposal_identity_template.py -n0 -q
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Shared stub helpers — verbatim mirror of test_frontrunner_builder_template.py
# ---------------------------------------------------------------------------


def _stub_analytics(monkeypatch):
    monkeypatch.setattr(
        app_module.analytics, "get_history_with_cache_invalidation", lambda **kw: {}
    )
    monkeypatch.setattr(app_module.analytics, "list_available_symphonies", lambda h: [])
    monkeypatch.setattr(
        app_module.analytics, "compute_per_symphony_returns", lambda h, sym: ([], [], [])
    )


def _stub_db_observations(monkeypatch):
    monkeypatch.setattr(
        app_module.database, "get_advisor_observations_for_role", lambda role, limit=50: []
    )


_FR_METRICS_WITH_OVERLAY = {
    "incumbent_cagr": 0.08,
    "incumbent_max_drawdown": -0.22,
    "incumbent_calmar": 0.36,
    "candidate_cagr": 0.15,
    "candidate_max_drawdown": -0.11,
    "candidate_calmar": 1.36,
    "candidate_sharpe": 1.42,
    "candidate_volatility": 0.14,
    "node_count_delta": -6,
    "overlay_tree": {
        "kind": "if",
        "condition": {
            "lhs_fn": "relative-strength-index",
            "lhs_ticker": "SPY",
            "window": 10,
            "comparator": "gt",
            "rhs": {"fixed": 80},
        },
        "then": [{"kind": "asset", "ticker": "UVXY"}],
        "else": [{"kind": "asset", "ticker": "CANDIDATE_PLACEHOLDER_ASSET"}],
    },
    "replaced_node_id": "cascade-node-id-abc",
    "overlay_summary": "RSI(SPY,10) > 80 hedges into UVXY",
}

_FR_METRICS_LEGACY_NO_OVERLAY = {
    "incumbent_cagr": 0.08,
    "incumbent_max_drawdown": -0.22,
    "incumbent_calmar": 0.36,
    "candidate_cagr": 0.15,
    "candidate_max_drawdown": -0.11,
    "candidate_calmar": 1.36,
    "candidate_sharpe": 1.42,
    "candidate_volatility": 0.14,
    "node_count_delta": -6,
}

_RETROFIT_METRICS = {
    "cagr": 0.19,
    "sharpe": 1.55,
    "calmar": 1.9,
    "max_drawdown": -0.10,
}


def _make_pending_row(
    *,
    row_id: int = 1,
    symphony_id: str = "sym-abc123",
    proposal_source: str = "frontrunner_builder",
    candidate_tree: dict | None = None,
    metrics_json=None,
    error_message: str | None = None,
) -> dict:
    """Local copy of test_frontrunner_builder_template.py's own row factory,
    shaped per database._FRONTRUNNER_PROPOSAL_COLUMNS."""
    if metrics_json is None:
        metrics_json = (
            dict(_FR_METRICS_WITH_OVERLAY)
            if proposal_source == "frontrunner_builder"
            else dict(_RETROFIT_METRICS)
        )
    return {
        "id": row_id,
        "created_at": "2026-08-20T12:00:00Z",
        "updated_at": "2026-08-20T12:00:00Z",
        "symphony_id": symphony_id,
        "proposal_source": proposal_source,
        "approval_status": "pending",
        "candidate_tree": candidate_tree if candidate_tree is not None else {"step": "root"},
        "metrics_json": metrics_json,
        "created_symphony_id": None,
        "error_message": error_message,
    }


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _render_advisor_page(
    client,
    monkeypatch,
    *,
    pending_rows: list[dict] | None = None,
    bot_state: dict | None = None,
) -> str:
    """GET /ai-advisor with the frontrunner accessor + database.load_state
    controlled; returns decoded HTML."""
    _stub_analytics(monkeypatch)
    _stub_db_observations(monkeypatch)

    fake_corr = MagicMock()
    fake_corr.compute_pairwise_correlations.return_value = []
    fake_corr.CRISIS_CAVEAT = "Test caveat"

    with (
        patch.object(
            app_module.database,
            "get_pending_frontrunner_proposals",
            return_value=pending_rows if pending_rows is not None else [],
        ),
        patch.object(
            app_module.database,
            "load_state",
            return_value=bot_state if bot_state is not None else {},
        ),
        patch.dict("sys.modules", {"advisors.correlation_diagnostic": fake_corr}),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200, (
        f"GET /ai-advisor returned {resp.status_code}; expected 200."
    )
    return resp.data.decode("utf-8", errors="replace")


def _card_source(html: str, row_id: int) -> str:
    """Scope a slice of HTML to one card by id="fr-card-{row_id}", bounded at
    the next card's opening marker or a generous fixed window (mirrors
    test_frontrunner_builder_template.py's own TestCardAnatomyRetrofitSource
    scoping idiom)."""
    start = html.find(f'id="fr-card-{row_id}"')
    if start == -1:
        return ""
    return html[start : start + 6000]


# ===========================================================================
# Group A: incumbent display-name resolution reaches the rendered card (AC-4)
# ===========================================================================


class TestIncumbentDisplayNameResolution:
    def test_resolved_display_name_appears_in_the_card(self, client, monkeypatch):
        row = _make_pending_row(row_id=1, symphony_id="hash-abc123")
        bot_state = {"hash-abc123": {"name": "My Real Symphony"}}
        html = _render_advisor_page(client, monkeypatch, pending_rows=[row], bot_state=bot_state)
        card = _card_source(html, 1)
        assert "My Real Symphony" in card, (
            f"resolved incumbent display name did not reach the rendered card — "
            f"card slice: {card[:500]!r}"
        )

    def test_unresolvable_hash_falls_back_to_the_hash_itself(self, client, monkeypatch):
        """Scoped specifically to the NEW fr-incumbent-name identity-line testid,
        not just 'anywhere in the card' — p.symphony_id already renders raw
        elsewhere in the card (the pre-existing .card-candidate-id span), so an
        unscoped substring check would pass vacuously regardless of whether the
        NEW fallback logic exists at all."""
        row = _make_pending_row(row_id=2, symphony_id="unresolvable-hash-xyz")
        html = _render_advisor_page(client, monkeypatch, pending_rows=[row], bot_state={})
        card = _card_source(html, 2)
        idx = card.find('data-testid="fr-incumbent-name"')
        assert idx != -1, 'data-testid="fr-incumbent-name" not found (AC-4).'
        nearby = card[idx : idx + 300]
        assert "unresolvable-hash-xyz" in nearby, (
            f"an unresolvable incumbent hash must fall back to rendering the hash "
            f"itself IN the identity line, never a blank identity line — found nearby: "
            f"{nearby!r}"
        )


# ===========================================================================
# Group B: frontrunner_builder-source card identity + explainer + overlay
# summary + overlay preview (AC-4, AC-5, AC-6)
# ===========================================================================


class TestFrontrunnerSourceCardOverlayIdentity:
    @pytest.fixture
    def card(self, client, monkeypatch):
        row = _make_pending_row(row_id=10, symphony_id="hash-1", proposal_source="frontrunner_builder")
        bot_state = {"hash-1": {"name": "My Symphony"}}
        html = _render_advisor_page(client, monkeypatch, pending_rows=[row], bot_state=bot_state)
        return _card_source(html, 10)

    def test_identity_line_states_full_standalone_copy(self, card):
        assert "Full standalone copy of" in card, (
            f"frontrunner_builder-source card must render the AC-4 identity line — "
            f"card slice: {card[:800]!r}"
        )
        assert "My Symphony" in card

    def test_identity_line_has_testid(self, card):
        assert 'data-testid="fr-incumbent-name"' in card

    def test_explainer_line_present(self, card):
        assert "Approve creates a NEW undeployed Composer symphony" in card
        assert "the incumbent is never modified" in card

    def test_overlay_summary_line_present_with_testid(self, card):
        idx = card.find('data-testid="fr-overlay-summary"')
        assert idx != -1, 'data-testid="fr-overlay-summary" not found (AC-5).'
        nearby = card[idx : idx + 300]
        # The fixture text contains a raw '>' — Jinja's default autoescape (AC-9)
        # correctly renders it as '&gt;'; assert the ESCAPED form, matching real
        # rendered behavior, not the pre-escape Python string.
        assert "RSI(SPY,10) &gt; 80 hedges into UVXY" in nearby, (
            f"overlay_summary value from metrics_json did not render (escaped) nearby "
            f"its testid — found: {nearby!r}"
        )

    def test_overlay_preview_details_present_distinct_from_raw_preview(self, card):
        assert 'data-testid="fr-overlay-preview"' in card, (
            "a NEW collapsible overlay-preview <details> must be present, distinct "
            "from the existing fr-raw-preview block (AC-5)."
        )
        assert 'data-testid="fr-raw-preview"' in card, (
            "the EXISTING raw-candidate preview must still be present (relabeled, "
            "not removed)."
        )

    def test_overlay_preview_details_not_open_by_default(self, card):
        import re

        idx = card.find('data-testid="fr-overlay-preview"')
        assert idx != -1
        before = card[max(0, idx - 300) : idx]
        details_open = before.rfind("<details")
        assert details_open != -1, (
            'data-testid="fr-overlay-preview" was not found inside a <details> element.'
        )
        opening_tag_end = card.find(">", idx)
        opening_tag = card[max(0, idx - 300) + details_open : opening_tag_end + 1]
        assert not re.search(r"\bopen\b", opening_tag), (
            f"the fr-overlay-preview <details> must be collapsed by default — got "
            f"{opening_tag!r}"
        )

    def test_raw_preview_relabeled_full_spliced_symphony(self, card):
        assert "Full spliced symphony (preview)" in card, (
            f"the existing raw-candidate preview must be relabeled per AC-5 — card "
            f"slice: {card[:2000]!r}"
        )


# ===========================================================================
# Group C: AC-6 legacy row (no overlay keys) honest empty-state
# ===========================================================================


class TestLegacyRowHonestEmptyState:
    def test_missing_overlay_keys_render_honest_fallback_text(self, client, monkeypatch):
        row = _make_pending_row(
            row_id=20,
            symphony_id="hash-legacy",
            proposal_source="frontrunner_builder",
            metrics_json=dict(_FR_METRICS_LEGACY_NO_OVERLAY),
        )
        html = _render_advisor_page(
            client, monkeypatch, pending_rows=[row], bot_state={"hash-legacy": {"name": "Legacy"}}
        )
        card = _card_source(html, 20)
        assert "overlay not recorded for this proposal" in card, (
            f"a legacy row missing overlay_tree/replaced_node_id/overlay_summary must "
            f"render an honest empty-state, never crash or fabricate — card slice: "
            f"{card[:1500]!r}"
        )

    def test_non_dict_metrics_json_does_not_crash_the_render(self, client, monkeypatch):
        row = _make_pending_row(
            row_id=21,
            symphony_id="hash-weird",
            proposal_source="frontrunner_builder",
            metrics_json=[1, 2, 3],  # a JSON array — non-dict, per AC-6 edge case
        )
        # _render_advisor_page itself already asserts resp.status_code == 200 —
        # reaching this line without an exception IS the assertion.
        html = _render_advisor_page(client, monkeypatch, pending_rows=[row], bot_state={})
        assert 'id="fr-card-21"' in html, (
            "a non-dict metrics_json must still render the card (degraded, never a 500)"
        )


# ===========================================================================
# Group D: strategy_builder_retrofit-source card identity (AC-7)
# ===========================================================================


class TestRetrofitSourceCardIdentity:
    @pytest.fixture
    def card(self, client, monkeypatch):
        row = _make_pending_row(
            row_id=30, symphony_id="hash-retro", proposal_source="strategy_builder_retrofit"
        )
        html = _render_advisor_page(
            client, monkeypatch, pending_rows=[row], bot_state={"hash-retro": {"name": "Retro Sym"}}
        )
        return _card_source(html, 30)

    def test_from_scratch_identity_line_present(self, card):
        assert "From-scratch symphony proposed by Strategy Builder" in card
        assert "does not contain the incumbent's logic" in card

    def test_frontrunner_full_copy_wording_absent(self, card):
        assert "Full standalone copy of" not in card, (
            "a strategy_builder_retrofit-source card must NEVER render the frontrunner "
            "full-copy identity wording (AC-7)."
        )

    def test_preview_label_reads_proposed_symphony_not_full_spliced_symphony(self, card):
        """The existing raw-candidate preview label must BRANCH by proposal_source
        (team-lead finding, mid-cycle): a strategy_builder_retrofit candidate is
        FROM-SCRATCH, not spliced from the incumbent — labeling its preview 'Full
        spliced symphony (preview)' (the frontrunner_builder-source label) would be
        the same category of dishonesty this feature exists to fix."""
        assert "Proposed symphony (preview)" in card, (
            f"a strategy_builder_retrofit-source card's raw-candidate preview must be "
            f"labeled 'Proposed symphony (preview)', not the frontrunner-source label — "
            f"card slice: {card[:2500]!r}"
        )

    def test_word_spliced_absent_from_retrofit_card(self, card):
        assert "spliced" not in card.lower(), (
            "the word 'spliced' must never appear anywhere on a strategy_builder_retrofit "
            "-source card — a retrofit candidate is a from-scratch proposal, not a splice "
            "of the incumbent, and the frontrunner-source-only 'Full spliced symphony "
            f"(preview)' label leaking onto a retrofit card is exactly the dishonesty this "
            f"feature exists to fix. card slice: {card[:2500]!r}"
        )


# ===========================================================================
# Group E: AC-9 XSS escaping
# ===========================================================================


class TestNoSafeFilterOnNewFields:
    def test_incumbent_display_name_xss_payload_is_escaped(self, client, monkeypatch):
        payload_name = "<script>alert(1)</script>"
        row = _make_pending_row(row_id=40, symphony_id="hash-xss")
        html = _render_advisor_page(
            client, monkeypatch, pending_rows=[row], bot_state={"hash-xss": {"name": payload_name}}
        )
        assert "<script>alert(1)</script>" not in html, (
            "an unescaped <script> tag from a resolved incumbent display name reached "
            "the rendered page — stored-XSS vector (AC-9)."
        )
        assert "&lt;script&gt;" in html, (
            "the XSS payload was not found HTML-escaped anywhere in the page either — "
            "expected Jinja's default autoescape to render '&lt;script&gt;'."
        )

    def test_overlay_summary_xss_payload_is_escaped(self, client, monkeypatch):
        payload = "<img src=x onerror=alert(1)>"
        metrics = dict(_FR_METRICS_WITH_OVERLAY)
        metrics["overlay_summary"] = payload
        row = _make_pending_row(row_id=41, symphony_id="hash-xss2", metrics_json=metrics)
        html = _render_advisor_page(
            client, monkeypatch, pending_rows=[row], bot_state={"hash-xss2": {"name": "S"}}
        )
        assert "<img src=x onerror=alert(1)>" not in html, (
            "an unescaped <img onerror=...> payload from overlay_summary reached the "
            "rendered page — stored-XSS vector (AC-9)."
        )
        assert "&lt;img src=x onerror=alert(1)&gt;" in html, (
            "the overlay_summary XSS payload was not found HTML-escaped anywhere in the "
            "page either — if the raw marker is entirely absent, overlay_summary never "
            "reached the template at all (a wiring gap, not an escaping pass); expected "
            "Jinja's default autoescape to render the escaped form."
        )
