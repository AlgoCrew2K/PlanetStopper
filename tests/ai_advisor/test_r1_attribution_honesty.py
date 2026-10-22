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
    assert start != -1, f"element with {marker} not found in rendered HTML"
    end = html.find(closing_tag, start)
    assert end != -1, f"element with {marker} has no {closing_tag} in rendered HTML"
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


# R2-2 (2026-07-14): the sibling Logic Changes assertion this file used to
# carry here (test_ac1_logic_changes_tab_carries_deterministic_no_ai_label)
# was RETIRED, not merely rephrased — R2-2 made the Logic Changes tab
# genuinely LLM-reasoned and intentionally removed its "Deterministic — no AI
# reasoning" label (see tests/ui/test_logic_change_route_reasoning_provenance.py
# ::test_attribution_label_flips_for_logic_changes_tab_only, which asserts the
# OPPOSITE — the label's ABSENCE — and is the current binding contract for
# that tab). This R1-era test asserted the PRE-R2-2 state, which is now
# obsolete by design, not a regression.
#
# R2-3 (2026-07-14): the SAME retirement now applies to Asset Swaps —
# test_ac1_asset_swaps_tab_carries_deterministic_no_ai_label (formerly here)
# asserted the PRE-R2-3 state. R2-3 made Asset Swaps genuinely LLM-reasoned
# too and intentionally removed its "Deterministic — no AI reasoning" label
# (found by the PM's independent full-tree gate on the R2-3 cycle, which
# caught this exact stale-test-pins-retired-behavior class the touch-set-only
# CYCLE-COMPLETE run had missed). The binding contract for Asset Swaps' label
# is now tests/ui/test_asset_swap_route_reasoning_provenance.py::
# test_attribution_label_flips_for_asset_swaps_tab_only, which asserts the
# label's ABSENCE plus the new reasoned-path copy's presence. Strategy
# Builder/Chat are untouched by either R2-2 or R2-3 and keep their own
# genuine-attribution assertions above/below.


def test_ac1_strategy_builder_tab_labeled_generation_model_plus_community_statistically_gated(
    client, monkeypatch
):
    """MUST FAIL pre-fix: the SB tab nav badge today only says 'Highest
    overfitting risk — building from scratch', with no model/community/
    statistically-gated attribution.

    Attribution coherence: Strategy Builder generation is a suggestion-producing
    call, so its label follows the suggestion knob (the Fable family), never a
    hardcoded "Opus" or "Fable" literal. Setting the knob's override to a marker
    value proves the label is read from the model authority at render time."""
    monkeypatch.setenv("ADVISOR_SUGGESTION_MODEL", "test-marker-sb-model")
    html = _get_html(client, monkeypatch)
    tab = _extract_by_testid(html, "strategy-builder-tab", closing_tag="</button>")
    assert "test-marker-sb-model" in tab, (
        f"AC-1 GAP: strategy-builder-tab does not reflect the configured "
        f"generation model (set ADVISOR_SUGGESTION_MODEL to a marker value and it "
        f"did not appear) — the label must be read from the model authority, "
        f"never a hardcoded 'Opus'/'Fable' literal. Element: {tab}"
    )
    tab_lower = tab.lower()
    assert "community" in tab_lower, (
        f"AC-1 GAP: strategy-builder-tab does not mention community candidates. Element: {tab}"
    )
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
    block = _extract_by_testid(
        html, "market-prism-block", closing_tag='data-testid="advisor-controls"'
    )
    block_lower = block.lower()
    assert "opus" in block_lower or "claude" in block_lower, (
        "AC-2 GAP: market-prism-block carries no model attribution anywhere in its rendered output."
    )


def test_ac2_market_prism_attribution_reflects_resolved_model_accessor_not_hardcoded(
    client, monkeypatch
):
    """Override proof: setting ADVISOR_SYNTHESIS_MODEL must change the rendered
    attribution, which proves the badge reads the model authority at render time
    and not a literal baked into the template."""
    monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", "test-marker-prism-model")
    html = _get_html(client, monkeypatch)
    block = _extract_by_testid(
        html, "market-prism-block", closing_tag='data-testid="advisor-controls"'
    )
    assert "test-marker-prism-model" in block, (
        "AC-2 GAP: setting ADVISOR_SYNTHESIS_MODEL did not change the "
        "rendered Market Prism attribution — the badge is not read at render time."
    )


# ===========================================================================
# AC-3 — SB run-controls-note stale "from templates" copy corrected.
# ===========================================================================


def test_ac3_sb_run_controls_note_stale_from_templates_copy_removed(client, monkeypatch):
    """MUST FAIL pre-fix: byte-absence check on the exact stale string
    (git-blamed 7908d77b0, describes the retired T1-T7 template stamper)."""
    html = _get_html(client, monkeypatch)
    assert "The advisor generates candidate symphonies from templates, backtests" not in html, (
        "AC-3 GAP: the stale 'generates candidate symphonies from templates' copy is still present verbatim."
    )


def test_ac3_sb_run_controls_note_mentions_generation_model_and_community_and_significance_bar(
    client, monkeypatch
):
    """Corrected copy per doc-reconciliation §1.3: generation-model attribution
    + Atlas community sourcing + FDR/Yekutieli-calibrated significance bar,
    with single-strongest-candidate winner-take-all framing (not plural
    'candidates ... are surfaced').

    The model name is read from the suggestion knob at render time (override
    marker below), never a hardcoded model-name literal.

    CI credential-less fix: the run-controls-note this test targets lives
    inside the /ai-advisor route's `{% if no_api_key %}...{% else %}...{%
    endif %}` branch (templates/ai_advisor.html) — no_api_key = not
    _has_composer_key() (app.py, lazily imported from
    advisors.asset_swap_engine). Without a credentials mock, this test
    silently depended on real local .env Composer credentials to reach the
    else-branch; credential-less (CI), no_api_key=True and the whole
    run-controls-panel — including this element — is honestly omitted in
    favor of a "Composer API key not configured" notice, which is correct
    production behavior, not a bug. Mocking _has_composer_key makes the
    assertion deterministic and independent of environment credentials,
    matching the pattern already used for the same seam elsewhere this
    cycle (e.g. test_r1_power_caveat.py)."""
    import advisors.asset_swap_engine as ase

    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)
    monkeypatch.setenv("ADVISOR_SUGGESTION_MODEL", "test-marker-sb-model")
    html = _get_html(client, monkeypatch)
    run_controls_marker = 'class="run-controls-note"'
    start = html.find(run_controls_marker)
    assert start != -1, "run-controls-note element not found in rendered HTML"
    end = html.find("</p>", start)
    assert end != -1
    note = html[start:end]
    assert "test-marker-sb-model" in note, (
        f"AC-3 GAP: run-controls-note does not reflect the configured "
        f"generation model — it must be read at render time, never a hardcoded "
        f"model name literal. Element text: {note}"
    )
    note_lower = note.lower()
    assert "community" in note_lower or "atlas" in note_lower, (
        f"AC-3 GAP: run-controls-note does not mention the Atlas community library. Element text: {note_lower}"
    )
    assert "single" in note_lower or "strongest" in note_lower, (
        f"AC-3 GAP: run-controls-note does not correct the plural 'candidates are "
        f"surfaced' framing to the actual single-winner-per-run semantics. Element text: {note_lower}"
    )


# ===========================================================================
# Model badges read the resolved model of each family (Models API display_name)
#
# Badge families: the suggestion family (Fable line) names the Asset Swaps,
# Logic Changes and Strategy Builder tabs and the Strategy Builder run note;
# the synthesis family (Opus line) names the Chat tab and the Market Prism
# badge. Expected names come from the captured Models API page and its
# re-listing pages, never from a literal.
# ===========================================================================

import ast  # noqa: E402
import copy  # noqa: E402
import html as html_lib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import pathlib  # noqa: E402
import re  # noqa: E402
import threading  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import httpx  # noqa: E402

from tests._anthropic_http_fake import AnthropicHttpFake, load_fixture  # noqa: E402
from tests.model_config.test_fixture_schemas import schema_check  # noqa: E402

_UNAVAILABLE = "model unavailable"
_CAPTURED_PAGE = load_fixture("models/page_captured.json")
_SUGGESTION_BADGES = ("asset_swaps", "logic_changes", "strategy_builder", "run_note")
_SYNTHESIS_BADGES = ("chat", "prism")
_SECONDS_PER_DAY = 24 * 60 * 60
_THREAD_JOIN_SECONDS = 15.0
_LINE_OF_FAMILY = {"suggestion": "fable", "synthesis": "opus"}


def _newest_active_record(page: dict, line: str) -> dict:
    """The newest active record of a model line, chosen independently of the code under test."""
    candidates = [
        r
        for r in page["data"]
        if r.get("line") == line and r.get("lifecycle", "active") == "active"
    ]
    assert candidates, f"fixture page holds no active {line}-line record"
    return max(candidates, key=lambda r: r["created_at"])


def _expected_names(page: dict) -> dict[str, str]:
    return {
        family: _newest_active_record(page, line)["display_name"]
        for family, line in _LINE_OF_FAMILY.items()
    }


def _page_without_line(line: str) -> dict:
    page = copy.deepcopy(_CAPTURED_PAGE)
    page["data"] = [r for r in page["data"] if r.get("line") != line]
    page["first_id"] = page["data"][0]["id"]
    page["last_id"] = page["data"][-1]["id"]
    return page


def _resolve_both_through(fake: AnthropicHttpFake) -> None:
    """Resolve both families the way a generation call does, through the real resolver."""
    import model_config

    for knob in (model_config.Knob.SUGGESTION, model_config.Knob.SYNTHESIS):
        model_config.resolve(knob, fake.client)


def _seed_registry_from(fake: AnthropicHttpFake) -> None:
    import model_config

    model_config._reset_registry()
    _resolve_both_through(fake)


def _advance_model_clock(monkeypatch, seconds: float) -> None:
    """Move the registry's clock forward without touching the process-wide clock."""
    import time

    import model_config

    real_monotonic = time.monotonic
    monkeypatch.setattr(
        model_config, "time", SimpleNamespace(monotonic=lambda: real_monotonic() + seconds)
    )


def _element_after(html: str, marker: str, closing_tag: str) -> str:
    start = html.find(marker)
    assert start != -1, f"{marker} not found in rendered HTML"
    end = html.find(closing_tag, start)
    assert end != -1, f"{marker} has no {closing_tag} in rendered HTML"
    return html[start:end]


def _badges(html: str) -> dict[str, str]:
    """The rendered (still HTML-escaped) text of every model badge on the page."""
    badges: dict[str, str] = {}
    for key, testid in (
        ("asset_swaps", "tab-asset-swaps"),
        ("logic_changes", "tab-logic-changes"),
        ("chat", "tab-chat"),
        ("strategy_builder", "strategy-builder-tab"),
    ):
        tab = _extract_by_testid(html, testid, closing_tag="</button>")
        match = re.search(r'<span class="cap-tab-attribution">(.*?)</span>', tab, re.S)
        assert match, f"{testid} carries no cap-tab-attribution badge"
        badges[key] = match.group(1)
    badges["prism"] = _element_after(html, 'data-testid="prism-model-badge"', "</span>")
    badges["run_note"] = _element_after(html, 'class="run-controls-note"', "</p>")
    return badges


def _get_badges(client, monkeypatch) -> dict[str, str]:
    import advisors.asset_swap_engine as ase

    # The run note sits in the branch rendered only when a Composer key exists.
    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)
    return _badges(_get_html(client, monkeypatch))


def _names(text: str, name: str) -> bool:
    """Whether ``name`` appears as a whole model name: ``Claude Opus 5`` is not found in ``Claude Opus 5.5``."""
    return re.search(r"(?<![\w.])" + re.escape(name) + r"(?!\w|\.\d)", text) is not None


def _assert_family_badges(badges, keys, expected_text, other_text, family):
    for key in keys:
        text = html_lib.unescape(badges[key])
        assert _names(text, expected_text), (
            f"{key} badge should name the {family} family's model {expected_text!r}; got {text!r}"
        )
        assert not _names(text, other_text), (
            f"{key} badge names the other family's model {other_text!r}: {text!r}"
        )


def _assert_family_unavailable(badges, keys, family):
    for key in keys:
        text = badges[key]
        assert _UNAVAILABLE in text, f"{key} badge of the unresolved {family} family reads {text!r}"
        assert "Claude" not in text, (
            f"{key} badge shows a model name for an unresolved family: {text!r}"
        )


def _assert_both_families(badges, names):
    _assert_family_badges(
        badges, _SUGGESTION_BADGES, names["suggestion"], names["synthesis"], "suggestion"
    )
    _assert_family_badges(
        badges, _SYNTHESIS_BADGES, names["synthesis"], names["suggestion"], "synthesis"
    )


def _assert_both_unavailable(badges):
    _assert_family_unavailable(badges, _SUGGESTION_BADGES, "suggestion")
    _assert_family_unavailable(badges, _SYNTHESIS_BADGES, "synthesis")


def test_each_badge_shows_the_display_name_of_its_familys_newest_resolved_model(
    client, monkeypatch
):
    _assert_both_families(_get_badges(client, monkeypatch), _expected_names(_CAPTURED_PAGE))


@pytest.mark.parametrize(
    "relisted_fixture",
    ["models/relisted_newer_opus.json", "models/relisted_newer_fable.json"],
)
def test_badges_follow_a_relisted_newer_model_with_no_code_change(
    client, monkeypatch, relisted_fixture
):
    relisted_page = load_fixture(relisted_fixture)
    before, after = _expected_names(_CAPTURED_PAGE), _expected_names(relisted_page)
    assert before != after, "fixture guard: the re-listing must change one family's newest model"
    fake = AnthropicHttpFake.with_captured_page()
    _seed_registry_from(fake)

    first = _get_badges(client, monkeypatch)
    fake.set_pages([relisted_page])
    _advance_model_clock(monkeypatch, 25 * 3600)
    _resolve_both_through(fake)
    second = _get_badges(client, monkeypatch)

    _assert_both_families(first, before)
    _assert_both_families(second, after)


_NEW_MEMBERS_FIXTURE = "models/relisted_new_family_members.json"
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_badges_follow_a_relisting_that_adds_models_the_captured_page_never_listed(
    client, monkeypatch
):
    """The new records' ids and display names occur nowhere in the captured page, so a badge
    that maps today's ids to names, rebuilds a name from an id, or caches the first listing
    cannot show them: only reading the re-listed record's own ``display_name`` can."""
    page = load_fixture(_NEW_MEMBERS_FIXTURE)
    assert schema_check.check_models_file(page, _NEW_MEMBERS_FIXTURE) == []
    captured_text = json.dumps(_CAPTURED_PAGE)
    before, after = _expected_names(_CAPTURED_PAGE), _expected_names(page)
    for family in ("suggestion", "synthesis"):
        newest = _newest_active_record(page, _LINE_OF_FAMILY[family])
        assert newest["id"] not in captured_text, "fixture guard: the new id must be unseen"
        assert after[family] not in captured_text, "fixture guard: the new name must be unseen"
        assert after[family] != before[family]
    fake = AnthropicHttpFake.with_captured_page()
    _seed_registry_from(fake)

    first = _get_badges(client, monkeypatch)
    fake.set_pages([page])
    _advance_model_clock(monkeypatch, 25 * 3600)
    _resolve_both_through(fake)
    listings_after_relisting = len(fake.listing_requests)
    second = _get_badges(client, monkeypatch)

    _assert_both_families(first, before)
    _assert_both_families(second, after)
    assert len(fake.listing_requests) == listings_after_relisting, "the render listed models"


@pytest.mark.parametrize("relative_path", ["app.py", "templates/ai_advisor.html"])
def test_the_page_sources_hold_no_quoted_model_id_literal(relative_path):
    source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")

    literals = re.findall(r"""["']claude-(?:fable|opus|sonnet|haiku)-\d[^"']*["']""", source)

    assert literals == [], f"{relative_path} holds a model id literal: {literals}"


def test_the_app_holds_no_model_id_to_display_name_map():
    import app as app_module

    source = pathlib.Path(app_module.__file__).read_text(encoding="utf-8")

    for name in ("_MODEL_DISPLAY_NAMES", "_humanize_model_name"):
        assert not hasattr(app_module, name), f"app still defines {name}"
        assert name not in source, f"app.py still mentions {name}"


def test_badges_of_both_families_read_model_unavailable_when_nothing_is_resolved(
    client, monkeypatch, unresolved_models
):
    _assert_both_unavailable(_get_badges(client, monkeypatch))


@pytest.mark.parametrize(
    ("missing_family", "unavailable_keys", "resolved_keys"),
    [
        ("suggestion", _SUGGESTION_BADGES, _SYNTHESIS_BADGES),
        ("synthesis", _SYNTHESIS_BADGES, _SUGGESTION_BADGES),
    ],
)
def test_only_the_unresolved_familys_badges_read_model_unavailable(
    client, monkeypatch, missing_family, unavailable_keys, resolved_keys
):
    resolved_family = "synthesis" if missing_family == "suggestion" else "suggestion"
    names = _expected_names(_CAPTURED_PAGE)
    _seed_registry_from(
        AnthropicHttpFake(pages=[_page_without_line(_LINE_OF_FAMILY[missing_family])])
    )

    badges = _get_badges(client, monkeypatch)

    _assert_family_unavailable(badges, unavailable_keys, missing_family)
    _assert_family_badges(
        badges, resolved_keys, names[resolved_family], names[missing_family], resolved_family
    )


def test_page_render_never_lists_models_never_builds_a_client_and_never_waits(
    client, monkeypatch, unresolved_models
):
    import ai_advisor
    import model_config

    fake = AnthropicHttpFake.with_captured_page()
    fake.hold_listings()  # a render that waits on the listing blocks until the gate times out
    built, resolutions = [], []
    monkeypatch.setattr(ai_advisor, "_build_client", lambda: built.append(1) or fake.client)

    def _forbidden(name):
        def call(*args, **kwargs):
            resolutions.append(name)
            raise RuntimeError("the page render must read the cached resolution only")

        return call

    for name in ("resolve", "invalidate"):
        monkeypatch.setattr(model_config, name, _forbidden(name))

    try:
        badges = _get_badges(client, monkeypatch)
    finally:
        fake.release_listings()

    assert fake.listing_requests == [], "the render sent a Models API listing request"
    assert built == [], "the render built an Anthropic client"
    assert resolutions == [], f"the render called the resolver: {resolutions}"
    _assert_both_unavailable(badges)


def test_a_stale_resolution_still_shows_its_last_resolved_name_and_triggers_no_listing(
    client, monkeypatch
):
    fake = AnthropicHttpFake.with_captured_page()
    _seed_registry_from(fake)
    listings_before = len(fake.listing_requests)
    _advance_model_clock(monkeypatch, 30 * _SECONDS_PER_DAY)
    fake.hold_listings()

    try:
        badges = _get_badges(client, monkeypatch)
    finally:
        fake.release_listings()

    assert len(fake.listing_requests) == listings_before, "the render re-listed a stale resolution"
    _assert_both_families(badges, _expected_names(_CAPTURED_PAGE))


@pytest.mark.parametrize(
    ("env_var", "overridden_family", "override_keys", "resolved_keys"),
    [
        ("ADVISOR_SUGGESTION_MODEL", "suggestion", _SUGGESTION_BADGES, _SYNTHESIS_BADGES),
        ("ADVISOR_SYNTHESIS_MODEL", "synthesis", _SYNTHESIS_BADGES, _SUGGESTION_BADGES),
    ],
)
def test_an_override_absent_from_the_listing_shows_its_raw_value_on_its_own_badges_only(
    client, monkeypatch, env_var, overridden_family, override_keys, resolved_keys
):
    override = "operator-chosen-model-id"
    assert override not in {r["id"] for r in _CAPTURED_PAGE["data"]}
    monkeypatch.setenv(env_var, override)
    names = _expected_names(_CAPTURED_PAGE)
    resolved_family = "synthesis" if overridden_family == "suggestion" else "suggestion"

    badges = _get_badges(client, monkeypatch)

    _assert_family_badges(
        badges, override_keys, override, names[overridden_family], overridden_family
    )
    _assert_family_badges(badges, resolved_keys, names[resolved_family], override, resolved_family)


@pytest.mark.parametrize("blank", ["", "   ", "\t "])
def test_a_blank_override_counts_as_absent_and_the_resolved_names_show(client, monkeypatch, blank):
    for env_var in ("ADVISOR_SUGGESTION_MODEL", "ADVISOR_SYNTHESIS_MODEL"):
        monkeypatch.setenv(env_var, blank)

    _assert_both_families(_get_badges(client, monkeypatch), _expected_names(_CAPTURED_PAGE))


def test_a_display_name_with_html_special_characters_renders_as_escaped_text(client, monkeypatch):
    """Only ``display_name`` of the two newest captured records is edited, in memory, to hold
    ``<``, ``&`` and ``"``; every other field is the captured record."""
    page = copy.deepcopy(_CAPTURED_PAGE)
    hostile = {
        "suggestion": 'Fable <b>bold</b> & "quoted"',
        "synthesis": 'Opus <i>slanted</i> & "quoted"',
    }
    for family, name in hostile.items():
        _newest_active_record(page, _LINE_OF_FAMILY[family])["display_name"] = name
    _seed_registry_from(AnthropicHttpFake(pages=[page]))
    import advisors.asset_swap_engine as ase

    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)
    html = _get_html(client, monkeypatch)
    badges = _badges(html)

    for markup in ("<b>bold</b>", "<i>slanted</i>"):
        assert markup not in html, f"display_name markup {markup!r} reached the page unescaped"
    for key in _SUGGESTION_BADGES:
        assert hostile["suggestion"] in html_lib.unescape(badges[key]), key
    for key in _SYNTHESIS_BADGES:
        assert hostile["synthesis"] in html_lib.unescape(badges[key]), key


# ===========================================================================
# Startup warm-up: both badges resolve after a restart with no operator action.
# ===========================================================================


class _ThreadSpy:
    """Record every thread created while it is installed; threads still run for real."""

    def __init__(self, monkeypatch):
        self.created: list[threading.Thread] = []
        self.targets: list = []
        created, targets, real_thread = self.created, self.targets, threading.Thread

        class _Recording(real_thread):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created.append(self)
                targets.append(kwargs.get("target"))

        monkeypatch.setattr(threading, "Thread", _Recording)

    def join_all(self) -> None:
        for thread in self.created:
            thread.join(_THREAD_JOIN_SECONDS)
            assert not thread.is_alive(), f"thread {thread.name!r} did not finish"


def test_startup_warmup_resolves_both_families_in_the_background_so_the_page_shows_names(
    client, monkeypatch, unresolved_models
):
    import ai_advisor
    import app as app_module

    fake = AnthropicHttpFake.with_captured_page()
    monkeypatch.setattr(ai_advisor, "_build_client", lambda: fake.client)
    spy = _ThreadSpy(monkeypatch)
    fake.hold_listings()

    app_module._start_model_warmup()  # must return while the listing is still in flight

    assert fake.listing_started.wait(_THREAD_JOIN_SECONDS), (
        "the warm-up never requested the listing"
    )
    assert spy.created, "the warm-up must run on its own thread"
    assert all(t.daemon for t in spy.created), "the warm-up thread must not block process exit"
    assert any(t.is_alive() for t in spy.created), (
        "the call returned only after the listing finished"
    )
    _assert_both_unavailable(_get_badges(client, monkeypatch))

    fake.release_listings()
    spy.join_all()
    listings_after_warmup = len(fake.listing_requests)
    badges = _get_badges(client, monkeypatch)

    _assert_both_families(badges, _expected_names(_CAPTURED_PAGE))
    assert fake.listing_sequences == 1, "one listing resolves both families"
    assert len(fake.listing_requests) == listings_after_warmup, "the page render listed again"
    assert all(r.path == "/v1/models" for r in fake.requests), "the warm-up sent a Messages request"
    assert fake.misuse == []


_WARMUP_SECRET = "secret-marker-3f9c1d"


def _raise_from_client_factory():
    raise RuntimeError(f"ANTHROPIC_API_KEY {_WARMUP_SECRET} is not usable")


@pytest.mark.parametrize("failure", ["client_construction", "listing_503", "connection_error"])
def test_warmup_worker_swallows_every_failure_logs_no_message_and_leaves_badges_unavailable(
    client, monkeypatch, unresolved_models, caplog, failure
):
    import ai_advisor
    import app as app_module

    fake = AnthropicHttpFake.with_captured_page()
    if failure == "client_construction":
        monkeypatch.setattr(ai_advisor, "_build_client", _raise_from_client_factory)
    else:
        monkeypatch.setattr(ai_advisor, "_build_client", lambda: fake.client)
        fake.queue_listing_outcome(
            load_fixture("errors/503_unavailable.json")
            if failure == "listing_503"
            else httpx.ConnectError(f"cannot reach {_WARMUP_SECRET}")
        )
    caplog.set_level(logging.WARNING)

    app_module._model_warmup_worker()  # a daemon thread's body: any raise would be lost

    assert _WARMUP_SECRET not in caplog.text, "an exception message reached a WARNING+ log record"
    _assert_both_unavailable(_get_badges(client, monkeypatch))


def _parse_app() -> ast.Module:
    import app as app_module

    return ast.parse(pathlib.Path(app_module.__file__).read_text(encoding="utf-8"))


def _daemon_entrypoint(tree: ast.Module) -> ast.If:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    )


def test_daemon_entrypoint_starts_the_warmup_after_the_scheduler_thread_and_before_serving():
    entry = _daemon_entrypoint(_parse_app())
    scheduler_line = warmup_line = serve_line = None
    for node in ast.walk(entry):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "Thread":
            targets = [
                k.value.id
                for k in node.keywords
                if k.arg == "target" and isinstance(k.value, ast.Name)
            ]
            if "run_scheduler" in targets:
                scheduler_line = node.lineno
        elif isinstance(func, ast.Name) and func.id == "_start_model_warmup":
            warmup_line = node.lineno
        elif (
            isinstance(func, ast.Attribute)
            and func.attr == "run"
            and getattr(func.value, "id", "") == "app"
        ):
            serve_line = node.lineno

    assert warmup_line is not None, "the daemon entrypoint never calls _start_model_warmup()"
    assert scheduler_line is not None and serve_line is not None, "entrypoint shape changed"
    assert scheduler_line < warmup_line < serve_line, (scheduler_line, warmup_line, serve_line)


# ===========================================================================
# Warm-up worker: knob overrides, failed listings, diagnostics, wording.
# ===========================================================================

_OVERRIDE_VALUE = "operator-chosen-model-id"
_KNOB_ENV = {
    "suggestion": ("ADVISOR_SUGGESTION_MODEL", _SUGGESTION_BADGES),
    "synthesis": ("ADVISOR_SYNTHESIS_MODEL", _SYNTHESIS_BADGES),
}


@pytest.mark.parametrize(
    ("overridden", "listings_expected"),
    [(("suggestion",), 1), (("synthesis",), 1), (("suggestion", "synthesis"), 0)],
    ids=["suggestion-overridden", "synthesis-overridden", "both-overridden"],
)
def test_warmup_resolves_the_family_that_is_not_overridden_and_lists_only_when_one_is_left(
    client, monkeypatch, unresolved_models, overridden, listings_expected
):
    """An override answers its own knob without a listing, so whichever family is left must
    still be resolved by the worker itself: with the suggestion knob overridden the synthesis
    resolve is the only call that lists models."""
    import ai_advisor
    import app as app_module

    fake = AnthropicHttpFake.with_captured_page()
    monkeypatch.setattr(ai_advisor, "_build_client", lambda: fake.client)
    for family in overridden:
        monkeypatch.setenv(_KNOB_ENV[family][0], _OVERRIDE_VALUE)
    names = _expected_names(_CAPTURED_PAGE)

    app_module._model_warmup_worker()
    badges = _get_badges(client, monkeypatch)

    assert fake.listing_sequences == listings_expected
    assert fake.misuse == []
    for family, (_, keys) in _KNOB_ENV.items():
        if family in overridden:
            _assert_family_badges(badges, keys, _OVERRIDE_VALUE, names[family], family)
        else:
            _assert_family_badges(badges, keys, names[family], _OVERRIDE_VALUE, family)


def test_warmup_names_the_family_the_listing_holds_when_the_other_family_is_absent(
    client, monkeypatch, unresolved_models
):
    import ai_advisor
    import app as app_module

    page = _page_without_line("fable")
    fake = AnthropicHttpFake(pages=[page])
    monkeypatch.setattr(ai_advisor, "_build_client", lambda: fake.client)

    app_module._model_warmup_worker()
    badges = _get_badges(client, monkeypatch)

    _assert_family_unavailable(badges, _SUGGESTION_BADGES, "suggestion")
    _assert_family_badges(
        badges,
        _SYNTHESIS_BADGES,
        _newest_active_record(page, "opus")["display_name"],
        _newest_active_record(_CAPTURED_PAGE, "fable")["display_name"],
        "synthesis",
    )
    assert fake.listing_sequences == 1


def test_warmup_sends_one_listing_request_when_the_listing_fails(monkeypatch, unresolved_models):
    """A failed listing leaves the registry unfresh, so a second resolve would send a second request."""
    import ai_advisor
    import app as app_module

    fake = AnthropicHttpFake.with_captured_page()
    fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))
    monkeypatch.setattr(ai_advisor, "_build_client", lambda: fake.client)

    app_module._model_warmup_worker()

    assert fake.listing_sequences == 1, "the worker retried a failed listing"


def test_a_failed_warmup_leaves_a_debug_record_with_the_traceback_beside_a_type_only_warning(
    monkeypatch, unresolved_models, caplog
):
    import ai_advisor
    import app as app_module

    monkeypatch.setattr(ai_advisor, "_build_client", _raise_from_client_factory)
    caplog.set_level(logging.DEBUG, logger="alphabot")

    app_module._model_warmup_worker()

    records = [r for r in caplog.records if r.name == "alphabot"]
    warnings = [r for r in records if r.levelno >= logging.WARNING]
    with_traceback = [r for r in records if r.levelno == logging.DEBUG and r.exc_info]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "RuntimeError" in warnings[0].getMessage()
    assert _WARMUP_SECRET not in warnings[0].getMessage()
    assert warnings[0].exc_info is None, "the WARNING record must not carry the exception text"
    assert any(
        r.exc_info[0] is RuntimeError and _WARMUP_SECRET in str(r.exc_info[1])
        for r in with_traceback
    ), "no DEBUG record carries the exception for diagnosis"


_PLAN_ID = re.compile(r"\b(?:CC|AC|DE|FD|CR|QA|E)-\d+\b|\bS-\d+\.\d+\b")
_WARMUP_HELPERS = ("_start_model_warmup", "_model_warmup_worker")


@pytest.mark.parametrize("helper", _WARMUP_HELPERS)
def test_warmup_helpers_cite_no_plan_id_and_claim_no_lazy_import_the_module_does_not_have(helper):
    import app as app_module

    source = pathlib.Path(app_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == helper)
    text = ast.get_source_segment(source, fn)
    module_level_imports = {
        (alias.asname or alias.name).split(".")[0]
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    imported_here = {"ai_advisor", "model_config", "threading"} & module_level_imports

    assert not _PLAN_ID.findall(text), f"{helper} cites a plan identifier: {_PLAN_ID.findall(text)}"
    if re.search(r"\blazy\b|\blazily\b", text, re.IGNORECASE):
        assert not imported_here, (
            f"{helper} says it imports lazily but app.py imports {sorted(imported_here)} "
            "at module level"
        )


# ===========================================================================
# One call site, no double listing, and the daily re-run through the scheduler.
# ===========================================================================


def test_the_warmup_starter_has_exactly_one_call_site_inside_the_daemon_entrypoint():
    tree = _parse_app()
    entry_nodes = {id(n) for n in ast.walk(_daemon_entrypoint(tree))}

    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "_start_model_warmup")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "_start_model_warmup")
        )
    ]

    assert len(calls) == 1, f"_start_model_warmup is called at lines {[c.lineno for c in calls]}"
    assert id(calls[0]) in entry_nodes, "the only call is outside the __main__ block"


def test_the_warmup_worker_is_referenced_only_as_the_thread_target_inside_the_starter():
    tree = _parse_app()
    starter = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_start_model_warmup"
    )
    targets = {
        id(kw.value)
        for call in ast.walk(starter)
        if isinstance(call, ast.Call)
        for kw in call.keywords
        if kw.arg == "target"
    }

    references = [
        n
        for n in ast.walk(tree)
        if (isinstance(n, ast.Name) and n.id == "_model_warmup_worker")
        or (isinstance(n, ast.Attribute) and n.attr == "_model_warmup_worker")
    ]

    assert len(references) == 1, f"worker referenced at lines {[r.lineno for r in references]}"
    assert id(references[0]) in targets, (
        "the worker is referenced outside the starter Thread target"
    )


def test_two_overlapping_warmup_starts_send_one_listing_and_both_families_show_names(
    client, monkeypatch, unresolved_models
):
    import ai_advisor
    import app as app_module

    fake = AnthropicHttpFake.with_captured_page()
    monkeypatch.setattr(ai_advisor, "_build_client", lambda: fake.client)
    spy = _ThreadSpy(monkeypatch)
    fake.hold_listings()

    app_module._start_model_warmup()
    assert fake.listing_started.wait(_THREAD_JOIN_SECONDS), "the first warm-up never listed"
    app_module._start_model_warmup()
    fake.release_listings()
    spy.join_all()

    assert len(spy.created) == 2
    assert fake.listing_sequences == 1, "a second start listed again"
    assert fake.max_concurrent_listings == 1
    _assert_both_families(_get_badges(client, monkeypatch), _expected_names(_CAPTURED_PAGE))


class _StopScheduler(Exception):
    """Raised by the stand-in scheduler to leave the endless loop of run_scheduler."""


def _scheduled_jobs(monkeypatch) -> list:
    """The jobs run_scheduler registers, read from a real scheduler that never runs any."""
    import schedule

    import app as app_module

    class _RegisterOnly(schedule.Scheduler):
        def run_pending(self):
            raise _StopScheduler

    scheduler = _RegisterOnly()
    monkeypatch.setattr(
        app_module,
        "schedule",
        SimpleNamespace(every=scheduler.every, run_pending=scheduler.run_pending),
    )
    with pytest.raises(_StopScheduler):
        app_module.run_scheduler()
    return scheduler.jobs


def _warmup_slots_among(jobs: list) -> list:
    import app as app_module

    return [
        job for job in jobs if getattr(job.job_func, "func", None) is app_module._start_model_warmup
    ]


def _the_warmup_slot(monkeypatch):
    slots = _warmup_slots_among(_scheduled_jobs(monkeypatch))
    assert len(slots) == 1, f"run_scheduler registers {len(slots)} warm-up slots, expected one"
    return slots[0]


def _minutes(job) -> int:
    return job.at_time.hour * 60 + job.at_time.minute


_PRE_MARKET_START_MINUTES = 8 * 60  # 04:00 US Eastern is 08:00 UTC, the earliest in summer time
_AFTER_RETIREMENT_TICK_MINUTES = 4 * 60  # the last existing off-hours slot is 03:45
_MIN_SLOT_GAP_MINUTES = 15


def test_the_scheduler_registers_the_warmup_once_a_day_off_hours_clear_of_every_other_slot(
    monkeypatch,
):
    jobs = _scheduled_jobs(monkeypatch)
    slots = _warmup_slots_among(jobs)

    assert len(slots) == 1, "run_scheduler must register _start_model_warmup exactly once"
    (slot,) = slots
    assert (slot.unit, slot.interval) == ("days", 1), "the warm-up must run once a day"
    assert slot.at_time is not None, "the daily warm-up needs a fixed time of day"
    assert _AFTER_RETIREMENT_TICK_MINUTES <= _minutes(slot) < _PRE_MARKET_START_MINUTES, (
        f"slot {slot.at_time} is outside the off-hours window (the daemon clock is UTC)"
    )
    for other in jobs:
        if other is not slot and other.unit == "days" and other.at_time is not None:
            gap = abs(_minutes(other) - _minutes(slot))
            assert gap >= _MIN_SLOT_GAP_MINUTES, (
                f"warm-up slot {slot.at_time} is {gap} min from the slot at {other.at_time}"
            )


def test_the_scheduled_slot_only_spawns_the_daemon_thread_warmup_and_returns(
    monkeypatch, unresolved_models
):
    import ai_advisor
    import app as app_module

    fake = AnthropicHttpFake.with_captured_page()
    monkeypatch.setattr(ai_advisor, "_build_client", lambda: fake.client)
    slot = _the_warmup_slot(monkeypatch)
    spy = _ThreadSpy(monkeypatch)
    fake.hold_listings()

    slot.run()  # the scheduler thread must come back while the listing is still in flight

    assert fake.listing_started.wait(_THREAD_JOIN_SECONDS), "the slot never requested the listing"
    assert len(spy.created) == 1, "the slot must spawn exactly one thread"
    assert spy.created[0].daemon, "the warm-up thread must not block process exit"
    assert spy.targets == [app_module._model_warmup_worker]
    assert spy.created[0].is_alive(), "the slot returned only after the listing finished"
    fake.release_listings()
    spy.join_all()
    assert fake.listing_sequences == 1
    assert all(r.path == "/v1/models" for r in fake.requests), "the slot sent a Messages request"


def test_a_model_released_after_boot_reaches_the_badges_through_the_daily_slot_alone(
    client, monkeypatch
):
    """No advisor action and no restart: the registry resolved at boot goes stale, new models
    are listed, the daily slot runs, and the next page render shows their display names
    without the render itself listing anything."""
    import ai_advisor

    page = load_fixture(_NEW_MEMBERS_FIXTURE)
    fake = AnthropicHttpFake.with_captured_page()
    _seed_registry_from(fake)
    monkeypatch.setattr(ai_advisor, "_build_client", lambda: fake.client)
    slot = _the_warmup_slot(monkeypatch)
    spy = _ThreadSpy(monkeypatch)
    _assert_both_families(_get_badges(client, monkeypatch), _expected_names(_CAPTURED_PAGE))
    fake.set_pages([page])
    _advance_model_clock(monkeypatch, 25 * 3600)

    slot.run()
    spy.join_all()
    listings_after_slot = len(fake.listing_requests)
    badges = _get_badges(client, monkeypatch)

    _assert_both_families(badges, _expected_names(page))
    assert len(fake.listing_requests) == listings_after_slot, "the render listed models"
    assert fake.listing_sequences == 2, "one listing at boot and one by the slot"


def test_the_daily_slot_recovers_badges_left_unavailable_by_a_failed_boot_listing(
    client, monkeypatch, unresolved_models
):
    import ai_advisor
    import app as app_module

    fake = AnthropicHttpFake.with_captured_page()
    fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))
    monkeypatch.setattr(ai_advisor, "_build_client", lambda: fake.client)
    slot = _the_warmup_slot(monkeypatch)
    spy = _ThreadSpy(monkeypatch)
    app_module._start_model_warmup()
    spy.join_all()
    _assert_both_unavailable(_get_badges(client, monkeypatch))

    slot.run()
    spy.join_all()

    _assert_both_families(_get_badges(client, monkeypatch), _expected_names(_CAPTURED_PAGE))
