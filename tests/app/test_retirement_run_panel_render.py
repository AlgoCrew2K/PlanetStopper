"""RED tests -- Retirement panel "last run" block + Run-now button
(Cycle 2e AC-6, AC-7 UI; feature-plans/retirement-visibility.md).

Real Flask test-client render of ``GET /ai-advisor`` against a real temp DB
seeded through the production write accessor
(``database.insert_advisor_observation``) -- the template, the Overview-tab
prefetch in ``ai_advisor_tab()`` and the shared fetch helpers all run for real.

Pinned data-testids (.claude/tdd-handoff.md "templates/ai_advisor.html"):

  retirement-last-run                 the always-present block, ABOVE the cards
  retirement-last-run-time            run time (the run_at date appears)
  retirement-last-run-basis           source + basis_label
  retirement-last-run-symphonies      analysed vs roster counts
  retirement-last-run-depth           series_days_min / series_days_max
  retirement-last-run-pairs           pairs screened (+ the summary's screen_threshold)
  retirement-last-run-blocked         per-gate blocked counts (+ stress minimum when too_thin > 0)
  retirement-last-run-recommendations recommendation count
  retirement-last-run-excluded        excluded symphonies: display name + reason
  retirement-run-now-btn              the Run-now button (onclick="retirementRunNow(this)")
  retirement-run-now-status           status text element JS writes via textContent

Pinned copy (case-insensitive substrings; the surrounding words are free):
  never run          "Not run yet — runs nightly at 03:45, or use Run now."  (exact, plan AC-6)
  series_unavailable "series unavailable"
  insufficient_symphonies "insufficient symphonies"
  internal_error     "internal error" + the summary's error_type

Content is Jinja-autoescaped (no ``| safe``); JS writes status via textContent.
The JS contract tests at the bottom are source-contract checks, NOT node
--check tests (syntax is covered by tests/js_syntax/test_js_syntax.py).
"""

from __future__ import annotations

import html
import pathlib
import re

import pytest

import app as app_module
import database as db_module
from tests.app._retirement_run_summary_stub import make_summary

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_NEVER_RUN_TEXT = "Not run yet — runs nightly at 03:45, or use Run now."
_SUMMARY_ROLE = "RETIREMENT_RUN_SUMMARY"
_REC_ROLE = "RETIREMENT_RECOMMENDATION"

_SUB_TESTIDS = (
    "retirement-last-run-time",
    "retirement-last-run-basis",
    "retirement-last-run-symphonies",
    "retirement-last-run-depth",
    "retirement-last-run-pairs",
    "retirement-last-run-blocked",
    "retirement-last-run-recommendations",
    "retirement-last-run-excluded",
)


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_retirement_run_panel.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()
    db_module.run_migrations()
    return db_path


# ---------------------------------------------------------------------------
# Seeding + HTML extraction helpers
# ---------------------------------------------------------------------------


def _seed_roster(id_to_name: dict[str, str]) -> None:
    db_module.save_state({sid: {"name": n, "logic_holdings": {}} for sid, n in id_to_name.items()})


def _seed_summary(**overrides) -> dict:
    summary = make_summary(**overrides)
    db_module.insert_advisor_observation(
        advisor_role=_SUMMARY_ROLE,
        subject_type="fleet",
        subject_id="retirement",
        verdict=summary["status"],
        raw_response=summary,
    )
    return summary


def _seed_rec(run_id: str, candidate_id: str, sibling_id: str) -> None:
    db_module.insert_advisor_observation(
        advisor_role=_REC_ROLE,
        subject_type="symphony",
        subject_id=candidate_id,
        symphony_id=candidate_id,
        verdict="retire_candidate",
        raw_response={
            "candidate_id": candidate_id,
            "sibling_id": sibling_id,
            "correlation": 0.8,
            "ci_lower": 0.7,
            "ci_upper": 0.9,
            "n_obs": 250,
            "candidate_composite": 0.2,
            "sibling_composite": 0.7,
            "candidate_metrics": {},
            "sibling_metrics": {},
            "uncertainty_gate_passed": True,
            "structural_redundancy_gate_passed": True,
            "stressed_correlation": 0.8,
            "holdings_overlap": None,
            "basis_label": "realized daily returns (Composer, deposit-adjusted)",
            "explanation": "explanation text",
            "run_id": run_id,
        },
    )


def _render(client) -> str:
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _panel(body: str) -> str:
    start = body.index('data-testid="retirement-recommendations-panel"')
    end = body.index("</section>", start)
    return body[start:end]


_VOID_TAGS = frozenset(
    {
        "br",
        "hr",
        "img",
        "input",
        "meta",
        "link",
        "area",
        "base",
        "col",
        "embed",
        "source",
        "track",
        "wbr",
    }
)
_TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9-]*)\b[^>]*?(/?)>")


def _element_html(markup: str, testid: str) -> str:
    """Inner HTML of the element carrying ``data-testid="<testid>"`` (balanced
    tag scan over Jinja-rendered, well-formed markup). Raises ValueError when
    the testid is absent -- tests that expect absence check the raw substring."""
    opener = re.search(
        rf'<([A-Za-z][A-Za-z0-9-]*)\b[^>]*data-testid="{re.escape(testid)}"[^>]*>', markup
    )
    if opener is None:
        raise ValueError(f"data-testid={testid!r} not found")
    depth = 1
    pos = opener.end()
    for m in _TAG_RE.finditer(markup, pos):
        closing, name, selfclose = m.group(1), m.group(2).lower(), m.group(3)
        if name in _VOID_TAGS or selfclose:
            continue
        depth += -1 if closing else 1
        if depth == 0:
            return markup[pos : m.start()]
    raise ValueError(f"unbalanced markup after data-testid={testid!r}")


def _text(markup: str) -> str:
    """Visible text of an HTML fragment: tags stripped, entities decoded,
    whitespace collapsed."""
    no_tags = re.sub(r"<[^>]+>", " ", markup)
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()


def _block_html(body: str) -> str:
    return _element_html(_panel(body), "retirement-last-run")


def _block_text(body: str) -> str:
    return _text(_block_html(body))


def _sub_text(body: str, testid: str) -> str:
    """Text of a sub-element, which must be a DESCENDANT of the last-run block."""
    return _text(_element_html(_block_html(body), testid))


def _has_number(text: str, n: int | float) -> bool:
    """`n` appears as a standalone number (not part of a longer number or decimal)."""
    return re.search(rf"(?<![\d.]){re.escape(str(n))}(?!\d|\.\d)", text) is not None


_OK_SUMMARY = dict(
    run_id="run-render-ok",
    run_at="2026-10-21T07:45:03Z",
    source="composer",
    status="ok",
    roster_size=11,
    symphonies_with_series=8,
    symphonies_without_series=[
        {"symphony_id": "hash-nosym-1", "reason": "no_account"},
        {"symphony_id": "hash-nosym-2", "reason": "fetch_error: HTTP 503"},
        {"symphony_id": "hash-unknown-3", "reason": "series_too_short"},
    ],
    series_days_min=143,
    series_days_max=250,
    lookback_days=250,
    pairs_evaluated=28,
    pairs_screened=15,
    blocked_uncertainty=2,
    blocked_stress_too_thin=3,
    blocked_stress_below_threshold=4,
    blocked_no_candidate=5,
    recommendations=1,
    screen_threshold=0.72,
    stress_gate_min_aligned_days=173,
)
_OK_NAMES = {
    "hash-nosym-1": "Alpha Rotation",
    "hash-nosym-2": "Beta Momentum",
    # hash-unknown-3 is deliberately absent from bot_state -> hash fallback
    "cand-render": "Render Candidate",
    "sib-render": "Render Sibling",
}


# ===========================================================================
# AC-6: never-run state
# ===========================================================================


class TestNeverRunState:
    def test_panel_shows_the_not_run_yet_message_and_the_run_now_button(self, client, isolated_db):
        body = _render(client)
        assert _NEVER_RUN_TEXT in _block_text(body)
        assert 'data-testid="retirement-run-now-btn"' in _panel(body)

    def test_never_run_state_shows_no_run_statistics(self, client, isolated_db):
        panel = _panel(_render(client))
        assert 'data-testid="retirement-last-run"' in panel, "the block itself must render"
        for testid in _SUB_TESTIDS:
            assert f'data-testid="{testid}"' not in panel, testid

    def test_the_legacy_empty_message_is_still_shown_for_the_cards_area(self, client, isolated_db):
        panel = _panel(_render(client))
        assert 'data-testid="retirement-recommendations-empty"' in panel


# ===========================================================================
# AC-6: an OK run with blocked gates
# ===========================================================================


class TestOkRunRendersEveryFact:
    @pytest.fixture()
    def body(self, client, isolated_db):
        _seed_roster(_OK_NAMES)
        _seed_summary(**_OK_SUMMARY)
        return _render(client)

    def test_block_precedes_the_cards_region_and_every_subelement_is_present(self, body):
        block = _block_html(body)
        for testid in _SUB_TESTIDS:
            assert f'data-testid="{testid}"' in block, f"{testid} must be a descendant of the block"

    def test_run_time_shows_the_run_date(self, body):
        text = _sub_text(body, "retirement-last-run-time")
        assert "2026-10-21" in text

    def test_basis_shows_the_source_and_the_basis_label(self, body):
        text = _sub_text(body, "retirement-last-run-basis")
        assert "realized daily returns (Composer, deposit-adjusted)" in text
        assert "composer" in text.lower()

    def test_symphonies_shows_analysed_versus_roster(self, body):
        text = _sub_text(body, "retirement-last-run-symphonies")
        assert _has_number(text, 8), text
        assert _has_number(text, 11), text

    def test_depth_shows_the_series_day_range(self, body):
        text = _sub_text(body, "retirement-last-run-depth")
        assert _has_number(text, 143), text
        assert _has_number(text, 250), text

    def test_pairs_shows_the_screened_count_and_the_summarys_own_threshold(self, body):
        text = _sub_text(body, "retirement-last-run-pairs")
        assert _has_number(text, 15), text
        assert "0.72" in text, "the threshold must come from the run summary, not a literal"

    def test_blocked_shows_each_gates_count(self, body):
        text = _sub_text(body, "retirement-last-run-blocked")
        for count in (2, 3, 4, 5):
            assert _has_number(text, count), (count, text)

    def test_blocked_states_the_stress_minimum_from_the_summary_when_too_thin_is_nonzero(
        self, body
    ):
        text = _sub_text(body, "retirement-last-run-blocked")
        assert _has_number(text, 173), (
            "stress_gate_min_aligned_days (173 here, deliberately not the production 181) "
            f"must be read from the summary: {text!r}"
        )

    def test_recommendation_count_is_shown(self, body):
        text = _sub_text(body, "retirement-last-run-recommendations")
        assert _has_number(text, 1), text

    def test_excluded_symphonies_are_listed_by_display_name_with_their_reasons(self, body):
        text = _sub_text(body, "retirement-last-run-excluded")
        assert "Alpha Rotation" in text
        assert "no_account" in text
        assert "Beta Momentum" in text
        assert "HTTP 503" in text
        assert "series_too_short" in text

    def test_an_excluded_symphony_missing_from_bot_state_falls_back_to_its_hash(self, body):
        text = _sub_text(body, "retirement-last-run-excluded")
        assert "hash-unknown-3" in text

    def test_raw_hashes_of_resolvable_symphonies_are_not_the_primary_label(self, body):
        text = _sub_text(body, "retirement-last-run-excluded")
        assert "hash-nosym-1" not in text, (
            "a resolvable symphony shows its display name, not its hash"
        )


class TestStressMinimumOnlyWhenTooThin:
    def test_stress_minimum_is_not_stated_when_no_pair_was_too_thin(self, client, isolated_db):
        _seed_roster(_OK_NAMES)
        _seed_summary(**{**_OK_SUMMARY, "blocked_stress_too_thin": 0, "pairs_screened": 12})
        text = _sub_text(_render(client), "retirement-last-run-blocked")
        assert not _has_number(text, 173), text


class TestWorkerFallbackSummaryRendersHonestly:
    """When the producer dies the worker writes a MINIMAL summary row. The panel
    and API must render exactly that shape -- a template that formats a missing
    key would 500 the whole /ai-advisor page precisely when something failed."""

    _FALLBACK = {
        "run_id": "run-fallback",
        "run_at": "2026-10-22T03:45:00Z",
        "status": "internal_error",
        "error_type": "RuntimeError",
        "recommendations": 0,
    }

    def _seed_minimal_fallback(self) -> None:
        db_module.insert_advisor_observation(
            advisor_role=_SUMMARY_ROLE,
            subject_type="fleet",
            subject_id="retirement",
            verdict="internal_error",
            raw_response=dict(self._FALLBACK),
        )

    def test_the_minimal_five_key_fallback_row_renders_the_internal_error_state(
        self, client, isolated_db
    ):
        self._seed_minimal_fallback()
        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        text = _block_text(body)
        assert "internal error" in text.lower()
        assert "RuntimeError" in text
        assert _NEVER_RUN_TEXT not in text
        assert 'data-testid="retirement-run-now-btn"' in _panel(body)

    def test_a_fallback_row_after_an_ok_run_keeps_that_runs_cards_and_says_so(
        self, client, isolated_db
    ):
        """AC-5b (reverses the first AC-5 build): the fallback run persisted no cards,
        but cards are scoped to the latest SUCCESSFUL run, so the earlier ok run's
        card stays visible with a note naming that run."""
        _seed_roster({"cand-render": "Render Candidate", "sib-render": "Render Sibling"})
        _seed_rec("run-earlier", "cand-render", "sib-render")
        _seed_summary(run_id="run-earlier", recommendations=1, run_at="2026-10-18T03:45:00Z")
        self._seed_minimal_fallback()
        body = _render(client)
        panel = _panel(body)
        assert 'data-testid="retirement-recommendation-card"' in panel
        assert 'data-testid="retirement-recommendations-empty"' not in panel
        assert "2026-10-18" in _text(_element_html(panel, "retirement-cards-from-earlier-run"))
        assert "internal error" in _block_text(body).lower()

    def test_a_fallback_row_with_no_successful_run_falls_back_to_the_legacy_cards(
        self, client, isolated_db
    ):
        """No ok summary with a run_id exists, so the legacy latest-calendar-date filter
        applies (and there is no earlier-run note to show)."""
        _seed_roster({"cand-render": "Render Candidate", "sib-render": "Render Sibling"})
        _seed_rec("run-legacy", "cand-render", "sib-render")
        self._seed_minimal_fallback()
        panel = _panel(_render(client))
        assert 'data-testid="retirement-recommendation-card"' in panel
        assert 'data-testid="retirement-cards-from-earlier-run"' not in panel

    def test_a_producer_failure_end_to_end_renders_the_panel_and_the_api(
        self, client, isolated_db, monkeypatch
    ):
        """Real worker, producer raises -> persisted fallback -> panel + API."""
        import advisors.retirement_recommender as recommender_module  # noqa: PLC0415

        def _boom(**kwargs):
            raise RuntimeError("producer down")

        monkeypatch.setattr(recommender_module, "build_recommendations_with_summary", _boom)
        app_module._retirement_recommender_tick_worker()

        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        text = _block_text(resp.get_data(as_text=True))
        assert "internal error" in text.lower()
        assert "RuntimeError" in text

        api = client.get("/api/retirement-recommendations")
        assert api.status_code == 200
        payload = api.get_json()
        assert payload["last_run"]["status"] == "internal_error"
        assert payload["last_run"]["error_type"] == "RuntimeError"
        assert payload["recommendations"] == []


_FAILED_STATUS_MARKERS = {
    "series_unavailable": "series unavailable",
    "internal_error": "internal error",
    "insufficient_symphonies": "insufficient symphonies",
}
_NOTE_TESTID = "retirement-cards-from-earlier-run"
_OK_RUN_AT = "2026-10-18T03:45:00Z"
_FAILED_RUN_AT = "2026-10-21T03:45:00Z"


def _seed_failed_night(status: str) -> None:
    _seed_summary(
        run_id="run-failed",
        status=status,
        run_at=_FAILED_RUN_AT,
        recommendations=0,
        error_type="KeyError" if status == "internal_error" else None,
    )


class TestCardsFromEarlierRunNote:
    """AC-5b: a failed night never hides the latest successful run's cards (approved
    ones and their wind-down checklists included); the last-run block still shows the
    failure; a note names the successful run the cards come from."""

    _ROSTER = {
        "cand-old": "Old Candidate",
        "sib-old": "Old Sibling",
        "cand-new": "New Candidate",
        "sib-new": "New Sibling",
    }

    @pytest.mark.parametrize("status", sorted(_FAILED_STATUS_MARKERS))
    def test_a_failed_night_keeps_the_earlier_cards_with_a_note_and_the_failure_block(
        self, client, isolated_db, status
    ):
        _seed_roster(self._ROSTER)
        _seed_rec("run-ok", "cand-old", "sib-old")
        _seed_summary(run_id="run-ok", recommendations=1, run_at=_OK_RUN_AT)
        _seed_failed_night(status)

        body = _render(client)
        panel = _panel(body)

        assert 'data-testid="retirement-recommendation-card"' in panel
        assert 'data-testid="retirement-recommendations-empty"' not in panel
        note = _text(_element_html(panel, _NOTE_TESTID))
        assert "2026-10-18" in note, "the note names the successful run's run_at"
        assert "UTC" in note
        assert "2026-10-21" not in note, "it must not name the failed night"
        assert panel.index(f'data-testid="{_NOTE_TESTID}"') < panel.index(
            'data-testid="retirement-recommendation-card"'
        )
        # The last-run block still renders the latest summary of ANY status.
        assert _FAILED_STATUS_MARKERS[status] in _block_text(body).lower()

    def test_an_approved_card_and_its_checklist_survive_a_failed_night(self, client, isolated_db):
        """The finding that motivated AC-5b: an outage must not read as lost approvals."""
        _seed_roster(self._ROSTER)
        _seed_rec("run-ok", "cand-old", "sib-old")
        _seed_summary(run_id="run-ok", recommendations=1, run_at=_OK_RUN_AT)
        db_module.upsert_retirement_decision("cand-old", approval_status="approved")
        _seed_failed_night("series_unavailable")

        panel = _panel(_render(client))

        assert 'data-testid="retirement-recommendation-card"' in panel
        assert 'data-testid="retirement-checklist"' in panel
        assert 'data-testid="retirement-approve-btn"' not in panel, (
            "an approved card keeps no Approve button"
        )
        assert 'data-testid="retirement-rec-status"' in panel
        assert "approved" in _text(_element_html(panel, "retirement-rec-status")).lower()
        assert _NOTE_TESTID in panel

    def test_an_ok_run_after_an_ok_run_shows_only_the_newest_cards_and_no_note(
        self, client, isolated_db
    ):
        _seed_roster(self._ROSTER)
        _seed_rec("run-ok-1", "cand-old", "sib-old")
        _seed_summary(run_id="run-ok-1", recommendations=1, run_at=_OK_RUN_AT)
        _seed_rec("run-ok-2", "cand-new", "sib-new")
        _seed_summary(run_id="run-ok-2", recommendations=1, run_at=_FAILED_RUN_AT)

        panel = _panel(_render(client))

        assert panel.count('data-testid="retirement-recommendation-card"') == 1
        assert "New Candidate" in panel and "Old Candidate" not in panel
        assert _NOTE_TESTID not in panel

    def test_an_ok_run_that_found_nothing_hides_earlier_cards_without_a_note(
        self, client, isolated_db
    ):
        _seed_roster(self._ROSTER)
        _seed_rec("run-ok-1", "cand-old", "sib-old")
        _seed_summary(run_id="run-ok-1", recommendations=1, run_at=_OK_RUN_AT)
        _seed_summary(run_id="run-ok-2", recommendations=0, run_at=_FAILED_RUN_AT)

        panel = _panel(_render(client))

        assert 'data-testid="retirement-recommendation-card"' not in panel
        assert 'data-testid="retirement-recommendations-empty"' in panel
        assert _NOTE_TESTID not in panel

    def test_only_failed_nights_plus_legacy_rows_use_the_legacy_filter_without_a_note(
        self, client, isolated_db
    ):
        _seed_roster(self._ROSTER)
        _seed_rec("legacy", "cand-new", "sib-new")
        _seed_failed_night("series_unavailable")

        body = _render(client)
        panel = _panel(body)

        assert 'data-testid="retirement-recommendation-card"' in panel
        assert _NOTE_TESTID not in panel
        assert "series unavailable" in _block_text(body).lower()

    def test_no_note_when_nothing_has_run_or_the_latest_run_is_ok(self, client, isolated_db):
        assert _NOTE_TESTID not in _panel(_render(client))
        _seed_roster(self._ROSTER)
        _seed_rec("run-ok", "cand-old", "sib-old")
        _seed_summary(run_id="run-ok", recommendations=1, run_at=_OK_RUN_AT)
        assert _NOTE_TESTID not in _panel(_render(client))

    def test_the_note_is_autoescaped(self, client, isolated_db):
        _seed_roster(self._ROSTER)
        _seed_rec("run-ok", "cand-old", "sib-old")
        _seed_summary(run_id="run-ok", recommendations=1, run_at="2026-10-18T<b>03</b>:45:00Z")
        _seed_failed_night("series_unavailable")

        note_html = _element_html(_panel(_render(client)), _NOTE_TESTID)

        # Whether the template echoes the raw run_at or reformats it, markup must never
        # reach the page unescaped.
        assert "<b>" not in note_html


class TestExcludedListAbsentWhenNothingWasExcluded:
    def test_no_exclusions_means_no_excluded_entries(self, client, isolated_db):
        _seed_roster(_OK_NAMES)
        _seed_summary(**{**_OK_SUMMARY, "symphonies_without_series": [], "roster_size": 8})
        block = _block_html(_render(client))
        if 'data-testid="retirement-last-run-excluded"' in block:
            text = _text(_element_html(block, "retirement-last-run-excluded"))
            assert "Alpha Rotation" not in text and "no_account" not in text


# ===========================================================================
# AC-6: distinct honest states
# ===========================================================================


def _state_text(client, **summary_overrides) -> str:
    _seed_summary(**summary_overrides)
    return _block_text(_render(client))


class TestDistinctHonestStates:
    def test_series_unavailable_says_so_and_still_lists_the_excluded_symphonies(
        self, client, isolated_db
    ):
        _seed_roster({"h1": "Gamma Trend", "h2": "Delta Carry"})
        text = _state_text(
            client,
            status="series_unavailable",
            roster_size=2,
            symphonies_with_series=0,
            symphonies_without_series=[
                {"symphony_id": "h1", "reason": "fetch_error: HTTP 500"},
                {"symphony_id": "h2", "reason": "fetch_error: HTTP 500"},
            ],
            series_days_min=None,
            series_days_max=None,
            pairs_evaluated=0,
        )
        assert "series unavailable" in text.lower()
        assert "Gamma Trend" in text and "Delta Carry" in text
        assert "HTTP 500" in text

    def test_insufficient_symphonies_says_so(self, client, isolated_db):
        text = _state_text(
            client,
            status="insufficient_symphonies",
            roster_size=1,
            symphonies_with_series=0,
            series_days_min=None,
            series_days_max=None,
            pairs_evaluated=0,
        )
        assert "insufficient symphonies" in text.lower()

    def test_internal_error_says_so_and_names_the_error_type(self, client, isolated_db):
        text = _state_text(
            client,
            status="internal_error",
            error_type="ZeroDivisionError",
            series_days_min=None,
            series_days_max=None,
        )
        assert "internal error" in text.lower()
        assert "ZeroDivisionError" in text

    def test_each_failure_state_reads_differently_from_the_others_and_from_ok(
        self, client, isolated_db
    ):
        texts = {}
        for status, extra in (
            ("ok", {}),
            ("series_unavailable", {"symphonies_with_series": 0}),
            ("insufficient_symphonies", {"roster_size": 1, "symphonies_with_series": 0}),
            ("internal_error", {"error_type": "KeyError"}),
        ):
            db = db_module.get_connection()
            db.execute("DELETE FROM advisor_observations WHERE advisor_role = ?", (_SUMMARY_ROLE,))
            db.commit()
            db.close()
            texts[status] = _state_text(client, status=status, **extra)
        assert len(set(texts.values())) == 4, texts

    def test_a_failure_state_never_shows_the_never_run_message(self, client, isolated_db):
        text = _state_text(client, status="internal_error", error_type="KeyError")
        assert _NEVER_RUN_TEXT not in text

    def test_the_run_now_button_is_present_in_every_state(self, client, isolated_db):
        for status in ("ok", "series_unavailable", "insufficient_symphonies", "internal_error"):
            _seed_summary(
                status=status, error_type="KeyError" if status == "internal_error" else None
            )
            assert 'data-testid="retirement-run-now-btn"' in _panel(_render(client)), status


# ===========================================================================
# AC-5/AC-6 integration: the panel follows the LATEST run
# ===========================================================================


class TestPanelFollowsTheLatestRun:
    def test_a_zero_rec_latest_run_hides_earlier_cards_but_shows_the_run_block(
        self, client, isolated_db
    ):
        _seed_roster({"cand-render": "Render Candidate", "sib-render": "Render Sibling"})
        _seed_rec("run-morning", "cand-render", "sib-render")
        _seed_summary(run_id="run-morning", recommendations=1)
        _seed_summary(run_id="run-afternoon", recommendations=0, run_at="2026-10-21T18:00:00Z")

        body = _render(client)
        panel = _panel(body)
        assert 'data-testid="retirement-recommendation-card"' not in panel
        assert 'data-testid="retirement-recommendations-empty"' in panel
        assert "2026-10-21" in _block_text(body)

    def test_cards_of_the_latest_run_render_below_the_last_run_block(self, client, isolated_db):
        _seed_roster({"cand-render": "Render Candidate", "sib-render": "Render Sibling"})
        _seed_rec("run-now", "cand-render", "sib-render")
        _seed_summary(run_id="run-now", recommendations=1)

        panel = _panel(_render(client))
        assert panel.index('data-testid="retirement-last-run"') < panel.index(
            'data-testid="retirement-recommendation-card"'
        )

    def test_a_last_run_read_failure_degrades_to_the_never_run_block_without_a_500(
        self, client, isolated_db, monkeypatch
    ):
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(app_module, "_fetch_retirement_last_run", _boom)
        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        assert 'data-testid="retirement-last-run"' in _panel(resp.get_data(as_text=True))


# ===========================================================================
# Escaping
# ===========================================================================


class TestEscaping:
    _XSS_NAME = "<script>alert(1)</script>"
    _XSS_REASON = "<img src=x onerror=alert(2)>"
    _XSS_BASIS = "<b>bold-basis</b>"
    _XSS_ERROR = "<svg/onload=alert(3)>"

    def test_summary_text_is_html_escaped_everywhere_in_the_block(self, client, isolated_db):
        _seed_roster({"hash-x": self._XSS_NAME})
        _seed_summary(
            status="ok",
            basis_label=self._XSS_BASIS,
            symphonies_without_series=[{"symphony_id": "hash-x", "reason": self._XSS_REASON}],
            blocked_stress_too_thin=1,
        )
        body = _render(client)
        block = _block_html(body)

        for raw in (self._XSS_NAME, self._XSS_REASON, self._XSS_BASIS):
            assert raw not in block, f"unescaped markup leaked: {raw!r}"
        assert "&lt;script&gt;" in block
        assert "&lt;img" in block
        assert "&lt;b&gt;" in block

    def test_error_type_is_escaped_in_the_internal_error_state(self, client, isolated_db):
        _seed_summary(status="internal_error", error_type=self._XSS_ERROR)
        block = _block_html(_render(client))
        assert self._XSS_ERROR not in block
        assert "&lt;svg" in block

    def test_the_template_never_marks_last_run_fields_safe(self):
        template = (REPO_ROOT / "templates" / "ai_advisor.html").read_text(encoding="utf-8")
        # Scan the WHOLE panel (heading, last-run block, button, status, cards), not just
        # the last-run block: markup placed above that block must be covered too.
        start = template.index('data-testid="retirement-recommendations-panel"')
        end = template.index("</section>{# /retirement-recommendations-panel #}", start)
        assert "| safe" not in template[start:end] and "|safe" not in template[start:end]


# ===========================================================================
# JS / template wiring contract (source-level; syntax lives in js_syntax tests)
# ===========================================================================


def _function_body(source: str, name: str) -> str:
    """Source of ``function <name>(...) { ... }`` by brace matching."""
    start = source.index(f"function {name}(")
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


class TestRunNowMarketHoursHint:
    """AC-7b: a STATIC hint in the panel that Run now is available outside market
    hours (rendered in every state, whatever the market state at render time)."""

    def _hint_text(self, body: str) -> str:
        return _text(_element_html(_panel(body), "retirement-run-now-hint"))

    def test_the_hint_is_present_when_no_run_exists(self, client, isolated_db):
        body = _render(client)
        assert "outside market hours" in self._hint_text(body).lower()

    @pytest.mark.parametrize(
        "status", ["ok", "series_unavailable", "insufficient_symphonies", "internal_error"]
    )
    def test_the_hint_is_present_in_every_run_state(self, client, isolated_db, status):
        _seed_summary(status=status, error_type="KeyError" if status == "internal_error" else None)
        body = _render(client)
        assert "outside market hours" in self._hint_text(body).lower()

    @pytest.mark.parametrize("market_state", ["open", "pre_market", "closed_frozen"])
    def test_the_hint_is_static_regardless_of_the_market_state_at_render_time(
        self, client, isolated_db, monkeypatch, market_state
    ):
        monkeypatch.setattr(app_module, "get_market_state", lambda *a, **k: market_state)
        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        assert "outside market hours" in self._hint_text(resp.get_data(as_text=True)).lower()

    def test_the_hint_is_inside_the_retirement_panel_and_outside_the_cards(
        self, client, isolated_db
    ):
        _seed_roster({"cand-render": "Render Candidate", "sib-render": "Render Sibling"})
        _seed_rec("run-now", "cand-render", "sib-render")
        _seed_summary(run_id="run-now", recommendations=1)
        panel = _panel(_render(client))
        assert 'data-testid="retirement-run-now-hint"' in panel
        assert panel.index('data-testid="retirement-run-now-hint"') < panel.index(
            'data-testid="retirement-recommendation-card"'
        )

    def test_the_hint_text_is_autoescaped_markup_free(self, client, isolated_db):
        hint_html = _element_html(_panel(_render(client)), "retirement-run-now-hint")
        assert "<script" not in hint_html.lower()


@pytest.fixture(scope="module")
def js() -> str:
    return (REPO_ROOT / "static" / "ai_advisor.js").read_text(encoding="utf-8")


class TestRunNowWiring:
    def test_button_calls_retirement_run_now_and_a_status_element_exists(self, client, isolated_db):
        panel = _panel(_render(client))
        btn = re.search(r'<button[^>]*data-testid="retirement-run-now-btn"[^>]*>', panel)
        assert btn, "run-now button missing"
        assert 'onclick="retirementRunNow(this)"' in btn.group(0)
        assert 'data-testid="retirement-run-now-status"' in panel

    def test_js_defines_and_exposes_retirement_run_now(self, js):
        assert "function retirementRunNow(" in js
        assert "window.retirementRunNow = retirementRunNow" in js

    def test_js_posts_to_the_run_route_with_the_csrf_header(self, js):
        body = _function_body(js, "retirementRunNow")
        # The exact route (closing quote included): '/ai-advisor/retirement/runx' must not pass.
        assert re.search(r"""fetch\(\s*['"]/ai-advisor/retirement/run['"]""", body)
        assert "X-CSRF-Token" in body
        assert "POST" in body

    def test_js_polls_the_api_for_a_changed_last_run_id_then_reloads(self, js):
        body = _function_body(js, "retirementRunNow")
        assert "/api/retirement-recommendations" in body
        assert "last_run" in body and "run_id" in body
        assert "location.reload" in body

    def test_js_declares_the_poll_interval_and_timeout_as_named_constants(self, js):
        assert re.search(r"RETIREMENT_POLL_INTERVAL_MS\s*=\s*5000\b", js), "5 s poll interval"
        assert re.search(r"RETIREMENT_POLL_TIMEOUT_MS\s*=\s*180000\b", js), "3 min poll timeout"

    def test_js_handles_the_409_already_running_response(self, js):
        assert "already_running" in _function_body(js, "retirementRunNow")

    def test_js_writes_status_text_via_textcontent_never_innerhtml(self, js):
        body = _function_body(js, "retirementRunNow")
        assert "textContent" in body
        assert "retirement-run-now-status" in body
        assert "innerHTML" not in body

    def _market_open_branch(self, js: str) -> str:
        """Source of the `if (...market_open...) { ... }` block inside
        retirementRunNow (brace-matched)."""
        body = _function_body(js, "retirementRunNow")
        match = re.search(r"if\s*\([^)]*market_open[^)]*\)\s*\{", body)
        assert match, (
            "retirementRunNow must handle the 'market_open' status in its own "
            "`if (... market_open ...) { ... }` block"
        )
        depth = 0
        for i in range(match.end() - 1, len(body)):
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
                if depth == 0:
                    return body[match.end() - 1 : i + 1]
        raise AssertionError("unbalanced braces in the market_open branch")

    def test_js_checks_market_open_before_the_generic_409_already_running_branch(self, js):
        """The route answers 409 for BOTH market_open and already_running; a
        generic `status === 409` check placed first would swallow market_open."""
        body = _function_body(js, "retirementRunNow")
        assert "market_open" in body
        assert body.index("market_open") < body.index("already_running")

    def test_js_market_open_branch_explains_the_reason_via_textcontent(self, js):
        branch = self._market_open_branch(js)
        assert "textContent" in branch or "setStatus(" in branch, "status text must be written"
        literals = re.findall(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"", branch)
        messages = [a or b for a, b in literals]
        assert any(
            "outside market hours" in m.lower()
            and "composer" in m.lower()
            and "shared" in m.lower()
            for m in messages
        ), (
            "the market_open message must say Run now is available outside market hours "
            f"because the Composer API is shared with the live engine; literals: {messages}"
        )

    def test_js_market_open_branch_does_not_start_polling(self, js):
        branch = self._market_open_branch(js)
        assert "return" in branch, "the market_open branch must return before the polling loop"
        for polling_token in (
            "sleep(",
            "setTimeout",
            "setInterval",
            "while",
            "readLastRunId",
            "fetch(",
        ):
            assert polling_token not in branch, f"market_open must not poll ({polling_token!r})"
        assert "location.reload" not in branch

    def test_js_disables_the_button_while_a_run_is_in_flight(self, js):
        assert ".disabled = true" in _function_body(js, "retirementRunNow")
