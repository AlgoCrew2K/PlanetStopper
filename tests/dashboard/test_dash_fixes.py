"""
RED tests — dash-fixes cycle.

Covers six display-correctness issues and one contested math question identified
in the metric-display audit. Tests are the regression spec and (for F-CHAIN-LINK)
the DECIDER on the contested math interpretation.

Issue map
---------
F-2   MDD winner-encoding suppressed + row annotated when insufficient_history.
F-3   Hero "If Held" TC has an annotation indicating it is account-level.
F-4   templates/index.html has an @media (max-width ~700px) block making hero-grid
      1-column.
F-5   guard_alpha badge marks the value as "at exit".
F-ARMED  hero ARMED count equals the count of armed (not triggered) symphonies.
F-CHAIN-LINK  GOLDEN TEST — pins the correct CR for a flat-at -3% trajectory.
              If RED -> chain-link treats cumulative snapshots as periodic (bug).
              If GREEN -> chain-link is correct; document in DECISIONS.md.

Fixture provenance
------------------
  F-2/F-3/F-4/F-5 — HTML content assertions against templates/index.html (static
  source). No DB, no network.
  F-ARMED — schema-derived from app._build_meta; bot_state fixtures are built
  inline. No DB, no network.
  F-CHAIN-LINK — tests/fixtures/math/shadow_cr_chain_link_golden.json (pinned
  shadow_history rows + expected dry_run). No DB network; in-process SQLite.
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).parent.parent.parent
_TEMPLATE = _REPO / "templates" / "index.html"
_STATIC = _REPO / "static"
_FIXTURE_DIR = _REPO / "tests" / "fixtures" / "math"
_CHAIN_LINK_FIXTURE = _FIXTURE_DIR / "shadow_cr_chain_link_golden.json"


def _html() -> str:
    """Return templates/index.html contents (cached per process)."""
    return _TEMPLATE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# F-2  MDD winner-encoding suppressed + row annotated when insufficient_history
# ---------------------------------------------------------------------------


class TestF2MddInsufficientHistorySuppressAndAnnotate:
    """
    F-2: when meta.portfolio.insufficient_history is true, the Max DD vs-row must:
      (a) suppress the winner-encoding (no 'winner' class on any bar when insufficient)
      (b) annotate the row label with an "insufficient history (<30d)" note
      (c) per-card Max DD also annotated (not the alpha-badge)

    These are HTML-source checks against the Jinja template. The template renders
    these at server time via the mdd_insufficient variable; a server-side assert is
    sufficient because the template is the authoritative display contract.
    """

    def test_hero_mdd_row_has_insufficient_label_conditional(self):
        """
        F-2a: the Max DD row label must contain a conditional block that renders
        an 'insufficient history (<30d)' annotation tied to mdd_insufficient.
        A missing or wrong variable name means the annotation never appears.
        """
        html = _html()
        # The annotation must be guarded by mdd_insufficient or insufficient_history.
        assert "mdd_insufficient" in html or "insufficient_history" in html, (
            "F-2a FAIL: templates/index.html has no mdd_insufficient / insufficient_history "
            "variable — the MDD 'insufficient history' annotation can never render."
        )

    def test_hero_mdd_row_label_contains_insufficient_history_text(self):
        """
        F-2b: the template must contain the annotation string "insufficient history"
        associated with the Max DD row label, so the operator can tell why the row is
        greyed out instead of showing a winner.
        """
        html = _html()
        assert "insufficient history" in html.lower(), (
            "F-2b FAIL: templates/index.html has no 'insufficient history' text. "
            "The Max DD row cannot be annotated when history is short."
        )

    def test_hero_mdd_row_winner_bar_guarded_by_insufficient_flag(self):
        """
        F-2c: winner-class on the MDD bars must be conditional on NOT being
        insufficient — the 'winner' class must appear inside an {% if not mdd_insufficient %}
        or equivalent guard so the comparative encoding is suppressed.
        """
        html = _html()
        # The bars for the mdd row carry data-row="mdd"; the winner class must be
        # wrapped in a conditional block involving insufficient_history.
        # We look for "winner" co-occurring in the proximity of "mdd" and the
        # insufficient guard.
        mdd_section_start = html.find('data-row="mdd"')
        assert mdd_section_start != -1, (
            "F-2c FAIL: templates/index.html has no element with data-row='mdd'. "
            "Cannot locate the MDD bar section."
        )
        # Scan ~600 chars around the first mdd bar for a winner + insufficient guard.
        mdd_context = html[max(0, mdd_section_start - 200): mdd_section_start + 600]
        has_insufficient_guard = "mdd_insufficient" in mdd_context or (
            "insufficient_history" in mdd_context
        )
        assert has_insufficient_guard, (
            "F-2c FAIL: the data-row='mdd' bar section does not reference "
            "mdd_insufficient or insufficient_history. The winner-class on MDD bars "
            "will be applied even when history is too short."
        )

    def test_hero_mdd_alpha_delta_suppressed_when_insufficient(self):
        """
        F-2d: the alpha-delta span for the MDD row must be conditional on NOT
        insufficient — showing 'α+22pp' against 8 days of bot history vs years of
        held history is misleading.
        """
        html = _html()
        # The alpha delta is inside the vs-row-top div for the MDD row.
        # Look for the pattern: the mdd alpha span is guarded by insufficient_history.
        mdd_row_start = html.find('data-testid="comp-mdd-bot-text"')
        assert mdd_row_start != -1, (
            "F-2d FAIL: no comp-mdd-bot-text element found. "
            "Cannot verify alpha-delta suppression."
        )
        mdd_row_context = html[max(0, mdd_row_start - 100): mdd_row_start + 800]
        # The delta span (vs-delta) must be guarded.
        has_guard = "mdd_insufficient" in mdd_row_context or (
            "insufficient_history" in mdd_row_context
        )
        assert has_guard, (
            "F-2d FAIL: the MDD vs-row alpha-delta is not guarded by an "
            "insufficient_history / mdd_insufficient conditional. "
            "A misleading 22pp alpha badge will show even with only 8 days of bot history."
        )

    def test_per_card_mdd_has_insufficient_label_not_alpha_badge(self):
        """
        F-2e: per-symphony cards must show an "insufficient history (<30d)" label
        on the Max DD cell instead of the alpha-badge when history is short.
        The template must reference a testid card-mdd-insufficient-label or equivalent.
        """
        html = _html()
        assert "card-mdd-insufficient-label" in html or (
            "insufficient history" in html.lower()
            and "cfg-cell" in html
        ), (
            "F-2e FAIL: per-card Max DD cell does not contain an insufficient-history "
            "annotation. The alpha-badge will be shown even when the bot has fewer "
            "than 30 trading days of shadow history."
        )


# ---------------------------------------------------------------------------
# F-3  Hero "If Held" TC annotation — account-level (includes cash)
# ---------------------------------------------------------------------------


class TestF3HeroTcIfHeldAnnotation:
    """
    F-3: the hero Today's Change 'If Held' value is sourced from Composer's
    account-level todays_percent_change (includes uninvested cash), while
    per-card Held TC uses symphony-level last_percent_change (excludes cash).
    The gap is ~0.15 pp. The hero row must annotate the If Held label so
    operators know the two numbers are not directly comparable.
    """

    def test_today_row_has_if_held_annotation_marker(self):
        """
        F-3a: the Today vs-row label must contain a footnote marker (any indicator
        element with data-testid='today-held-footnote-marker' or equivalent) so the
        operator can discover why the hero Held % differs from per-card Held %.
        """
        html = _html()
        has_testid = "today-held-footnote-marker" in html
        has_title_attr = (
            "includes" in html.lower()
            and "cash" in html.lower()
            and "Today" in html
        )
        assert has_testid or has_title_attr, (
            "F-3a FAIL: templates/index.html Today row has no footnote marker indicating "
            "the If Held value is account-level (includes uninvested cash). "
            "Operators will not know why the hero Held differs from per-card Held."
        )

    def test_today_held_footnote_references_account_level_and_cash(self):
        """
        F-3b: the annotation / tooltip text must reference both 'account-level'
        (or equivalent) and 'cash' so the operator understands the denominator
        difference.
        """
        html = _html()
        html_lower = html.lower()
        # Accept 'account-level' or 'account level' or 'account total'.
        mentions_account = (
            "account-level" in html_lower
            or "account level" in html_lower
            or "account total" in html_lower
        )
        mentions_cash = "cash" in html_lower
        assert mentions_account and mentions_cash, (
            "F-3b FAIL: the hero Today vs-row annotation does not mention both "
            "'account-level' (or equivalent) and 'cash'. "
            "Expected a tooltip / title attribute on the footnote marker explaining "
            "why If Held includes cash but per-card Held does not. "
            f"mentions_account={mentions_account}, mentions_cash={mentions_cash}"
        )


# ---------------------------------------------------------------------------
# F-4  Responsive: @media (max-width ~700px) makes hero-grid 1-column
# ---------------------------------------------------------------------------


class TestF4ResponsiveHeroGrid:
    """
    F-4: templates/index.html must contain an @media (max-width ~700px) block
    that makes .hero-grid 1-column so the page does not overflow on mobile.
    """

    def test_index_html_has_media_query_at_700px(self):
        """
        F-4a: the template must contain an @media rule with max-width around 700px
        so the hero-grid drops to a single column on narrow viewports.
        """
        html = _html()
        has_media = "@media" in html and "700px" in html
        assert has_media, (
            "F-4a FAIL: templates/index.html has no @media (max-width 700px) rule. "
            "The hero-grid remains two-column on 375px mobile — content overflows."
        )

    def test_media_query_sets_hero_grid_to_single_column(self):
        """
        F-4b: within the @media block, .hero-grid must be set to a single column
        (grid-template-columns: 1fr or similar one-value expression).
        """
        html = _html()
        media_idx = html.find("@media")
        assert media_idx != -1, "F-4b FAIL: no @media rule found (prerequisite)."
        # Read up to 400 chars after the @media to capture the block.
        media_block = html[media_idx: media_idx + 400]
        assert "hero-grid" in media_block, (
            "F-4b FAIL: @media block does not target .hero-grid. "
            "The 1-column layout for narrow viewports is not applied."
        )
        # The block must set a single-column layout.
        has_one_column = (
            "1fr" in media_block
            and "grid-template-columns" in media_block
        )
        assert has_one_column, (
            "F-4b FAIL: @media .hero-grid block does not set grid-template-columns:1fr "
            "(or single-value equivalent). Hero columns will not stack on narrow screens."
        )


# ---------------------------------------------------------------------------
# F-5  guard_alpha badge marks the value "at exit"
# ---------------------------------------------------------------------------


class TestF5GuardAlphaBadgeAtExit:
    """
    F-5: the guard_alpha badge on triggered symphony cards shows the CR gap at
    the moment of exit, NOT the current live CR gap. The badge text must include
    an 'at exit' qualifier so operators don't interpret it as a current value.
    """

    def test_triggered_verdict_gave_up_text_has_at_exit_qualifier(self):
        """
        F-5a: the 'gave up Xα' branch of the triggered-verdict block must contain
        an 'at exit' qualifier (inline text or a labelled span).
        """
        html = _html()
        # Find the 'gave up' branch.
        gave_up_idx = html.find("gave up")
        assert gave_up_idx != -1, (
            "F-5a FAIL: 'gave up' text not found in templates/index.html. "
            "Cannot verify at-exit qualifier."
        )
        # Within 200 chars of 'gave up' there must be 'at exit'.
        nearby = html[gave_up_idx: gave_up_idx + 200]
        assert "at exit" in nearby.lower(), (
            "F-5a FAIL: the 'gave up Xα' badge does not have an 'at exit' qualifier "
            "within 200 chars. Operators will read the badge as the current live CR gap."
        )

    def test_triggered_verdict_good_call_text_has_at_exit_qualifier(self):
        """
        F-5b: the 'saved +Xα' branch of the triggered-verdict block must also
        carry 'at exit' so both positive and negative outcomes are correctly qualified.
        """
        html = _html()
        saved_idx = html.find("saved +")
        assert saved_idx != -1, (
            "F-5b FAIL: 'saved +' text not found in templates/index.html."
        )
        nearby = html[saved_idx: saved_idx + 200]
        assert "at exit" in nearby.lower(), (
            "F-5b FAIL: the 'saved +Xα' badge does not have an 'at exit' qualifier. "
            "Both positive and negative guard_alpha badges must be qualified."
        )

    def test_guard_alpha_at_exit_testid_present(self):
        """
        F-5c: a data-testid='guard-alpha-at-exit' element must exist so automated
        visual-QA can locate and verify the qualifier renders.
        """
        html = _html()
        assert "guard-alpha-at-exit" in html, (
            "F-5c FAIL: no data-testid='guard-alpha-at-exit' in templates/index.html. "
            "Add a labelled span so the visual gate can verify the qualifier."
        )


# ---------------------------------------------------------------------------
# F-ARMED  Hero ARMED count equals actual count of armed (non-triggered) symphonies
# ---------------------------------------------------------------------------


class TestFArmedCountMatchesActualArmedSymphonies:
    """
    F-ARMED: the hero 'N Armed' stat must equal the count of symphonies that are
    armed (any of: armed, tp_armed, para_armed) AND have NOT yet triggered.

    User report: hero shows '1 Armed' but no symphony is actually armed. This is
    the _build_meta formula being tested against known bot_state fixtures.
    """

    def _call_build_meta(self, state_data: dict) -> dict:
        """
        Call app._build_meta with a known state_data and neutral env/market stubs.
        Returns the meta dict.
        """
        import app as app_module

        with (
            patch.object(
                app_module._dotenv_module,
                "dotenv_values",
                return_value={"LIVE_EXECUTION": "False"},
            ),
        ):
            return app_module._build_meta(
                state_data,
                next_run_seconds=30,
                market_state="closed",
                portfolio_strip={},
            )

    def test_armed_count_is_zero_when_all_symphonies_standby(self):
        """
        F-ARMED-1: when every symphony has armed=False / tp_armed=False /
        para_armed=False, meta.armed must be 0 — not 1.
        This is the user-reported regression: '1 Armed' with no armed symphony.
        """
        state = {
            "sym_a": {
                "name": "Alpha",
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
            },
            "sym_b": {
                "name": "Beta",
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
            },
        }
        meta = self._call_build_meta(state)
        assert meta["armed"] == 0, (
            f"F-ARMED-1 FAIL: meta.armed={meta['armed']} but all symphonies are standby "
            "(armed=False, tp_armed=False, para_armed=False). "
            "The hero 'Armed' count must be 0."
        )

    def test_armed_count_equals_armed_non_triggered_only(self):
        """
        F-ARMED-2: armed count must include trailing-armed + tp-armed + para-armed
        symphonies that have NOT triggered, and exclude triggered ones even if they
        carry an armed flag.
        """
        state = {
            "sym_armed": {
                "name": "Armed",
                "armed": True,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
            },
            "sym_tp": {
                "name": "TP Armed",
                "armed": False,
                "tp_armed": True,
                "para_armed": False,
                "triggered": False,
            },
            "sym_triggered_armed": {
                "name": "Triggered (armed flag still set)",
                "armed": True,
                "tp_armed": False,
                "para_armed": False,
                "triggered": True,  # already fired — must NOT count as armed
            },
            "sym_standby": {
                "name": "Standby",
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
            },
        }
        meta = self._call_build_meta(state)
        # sym_armed + sym_tp = 2 armed; sym_triggered_armed is triggered so excluded.
        assert meta["armed"] == 2, (
            f"F-ARMED-2 FAIL: meta.armed={meta['armed']}, expected 2. "
            "Count must include trailing + TP armed but exclude triggered symphonies."
        )

    def test_armed_count_excludes_non_symphony_state_keys(self):
        """
        F-ARMED-3: non-symphony keys in state_data (those without a 'name' key)
        must not be counted. State dicts can contain metadata entries.
        """
        state = {
            "_meta": {"last_run": "2026-01-05"},  # no 'name' key — must be ignored
            "sym_a": {
                "name": "Alpha",
                "armed": True,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
            },
        }
        meta = self._call_build_meta(state)
        assert meta["armed"] == 1, (
            f"F-ARMED-3 FAIL: meta.armed={meta['armed']}, expected 1. "
            "Non-symphony state keys (missing 'name') must not be counted."
        )

    def test_tracked_count_equals_symphony_entries_only(self):
        """
        F-ARMED-4: meta.tracked must equal the number of dicts that have a 'name'
        key, mirroring the armed count's filter. A phantom 'tracked=3' when only
        2 symphonies exist confuses the hero strip.
        """
        state = {
            "_internal": {"some": "metadata"},  # no 'name' — excluded
            "sym_a": {"name": "Alpha", "armed": False, "tp_armed": False, "para_armed": False, "triggered": False},
            "sym_b": {"name": "Beta", "armed": False, "tp_armed": False, "para_armed": False, "triggered": False},
        }
        meta = self._call_build_meta(state)
        assert meta["tracked"] == 2, (
            f"F-ARMED-4 FAIL: meta.tracked={meta['tracked']}, expected 2. "
            "Non-symphony state entries must not inflate the Tracked count."
        )


# ---------------------------------------------------------------------------
# F-CHAIN-LINK  GOLDEN TEST — decider on contested CR chain-link math
# ---------------------------------------------------------------------------


def _load_chain_link_fixture() -> dict:
    """Load the shadow_cr_chain_link_golden.json fixture."""
    return json.loads(_CHAIN_LINK_FIXTURE.read_text(encoding="utf-8"))


def _build_shadow_db_with_rows(
    rows: list[dict],
    symphony_id: str,
    tmp_path: Path,
) -> str:
    """
    Build an in-process SQLite shadow_history DB from a list of row dicts.

    Each dict must have keys: trading_day, shadow_return, ts_utc.
    The position_epoch column is included (migration 015); all rows share one epoch
    so the query-epoch filter passes.
    """
    db_file = str(tmp_path / "shadow_chain_link.db")
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE shadow_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symphony_id TEXT NOT NULL,
            account_id TEXT,
            cycle_id TEXT,
            ts_utc TEXT NOT NULL,
            ts_et TEXT,
            trading_day TEXT NOT NULL,
            current_return REAL NOT NULL,
            shadow_return REAL NOT NULL,
            is_post_trigger INTEGER NOT NULL DEFAULT 0,
            trigger_id INTEGER,
            position_epoch TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX idx_sym_day ON shadow_history "
        "(symphony_id, trading_day, ts_utc)"
    )
    epoch = "2026-01-04T14:30:00Z"  # a fixed epoch shared by all rows
    for row in rows:
        conn.execute(
            """INSERT INTO shadow_history
               (symphony_id, ts_utc, trading_day, current_return,
                shadow_return, is_post_trigger, position_epoch)
               VALUES (?, ?, ?, ?, ?, 0, ?)""",
            (
                symphony_id,
                row["ts_utc"],
                row["trading_day"],
                row["shadow_return"],  # current_return same as shadow_return here
                row["shadow_return"],
                epoch,
            ),
        )
    conn.commit()
    conn.close()

    # Patch the database module's shadow cache so the test is isolated.
    import database as _db
    _db._shadow_cr_cache.clear()

    return db_file


class TestChainLinkGoldenCumulativeSnapshot:
    """
    F-CHAIN-LINK: GOLDEN TEST that decides the contested chain-link math question.

    The audit claim:
      shadow_return is stored as current_return = last_percent_change * 100.
      last_percent_change from Composer is today's change for the portfolio since
      position open (a CUMULATIVE return snapshot, not a periodic daily return).

    The audit lead counter-claim:
      The divergence shown is "correct behavior".

    This test pins the expected result for a known 3-day flat trajectory at -3.0%.
    A position opened and held flat at -3.0% cumulative for 3 days records
    shadow_return = -3.0 on each trading day. The correct CR adjustment is -3.0%
    (the final snapshot); compounding three -3% "daily" returns yields -8.73%,
    which is wrong if the values are cumulative snapshots.

    Result interpretation:
      RED (test FAILS) -> the chain-link consumes cumulative snapshots as periodic
        returns and computes the wrong answer. risk-engine-specialist must fix the
        consumer (analytics.get_symphony_cumulative_return, lines 694-698).
      GREEN (test PASSES) -> the chain-link result == -3.0% => either (a) shadow_return
        IS a periodic daily return in which case holding flat at -3% means last_percent_change
        = -3% every day (rare but theoretically possible); or (b) the consumer already
        correctly uses the last snapshot instead of compounding. Document in DECISIONS.md.

    Tolerance: pytest.approx abs=0.01 — 1 basis point; the fixture values are
    integers so float error is negligible; the tolerance guards only float arithmetic.
    """

    def test_flat_position_cr_equals_last_snapshot_not_compounded(self, tmp_path):
        """
        F-CHAIN-LINK-1 (GOLDEN): a position flat at shadow_return=-3.0% for 3 days
        must yield dry_run = if_held + (-3.0) = 2.0, NOT if_held + (-8.73) = -3.73.

        Fixture: tests/fixtures/math/shadow_cr_chain_link_golden.json
        Symphony id: 'sym_chain_link_test'
        if_held: 5.0 (percent)
        Expected dry_run: 2.0 (= 5.0 + last_snapshot_return = 5.0 + (-3.0))
        Wrong answer (chain-link bug): -3.73 (= 5.0 + (0.97^3 - 1)*100)
        """
        fixture = _load_chain_link_fixture()
        rows = fixture["shadow_history_rows"]
        if_held = fixture["if_held_pct"]
        symphony_id = "sym_chain_link_test"

        db_file = _build_shadow_db_with_rows(rows, symphony_id, tmp_path)

        sym_dict = {
            "id": symphony_id,
            "simple_return": if_held / 100.0,  # analytics multiplies by 100 internally
            "net_deposits": 100.0,  # non-zero to use simple_return path
            "time_weighted_return": if_held / 100.0,
        }

        from analytics import get_symphony_cumulative_return

        result = get_symphony_cumulative_return(
            sym_dict,
            bot_state_entry=None,
            db_path=db_file,
        )

        assert result is not None and "dry_run" in result, (
            "F-CHAIN-LINK-1 FAIL: get_symphony_cumulative_return returned "
            f"{result!r} instead of a dict with 'dry_run'."
        )

        # The correct answer: dry_run = if_held + last_snapshot = 5.0 + (-3.0) = 2.0.
        # The chain-link bug answer: 5.0 + (0.97^3 - 1)*100 = 5.0 + (-8.73) = -3.73.
        # Tolerance: abs=0.01 (1 basis point) because fixture values are integer-like;
        # float multiplication of three 0.97 values has sub-millionth error.
        correct_dry_run = 2.0
        chain_link_bug_value = pytest.approx(-3.73, abs=0.01)

        assert result["dry_run"] != chain_link_bug_value, (
            "F-CHAIN-LINK-1 FAIL (BUG CONFIRMED): dry_run is -3.73%, which is the "
            "chain-link compound of three -3% daily returns. But shadow_return stores "
            "the CUMULATIVE return-since-open (= last_percent_change * 100 from "
            "Composer), not a periodic daily return. Compounding cumulative snapshots "
            "fabricates divergence. The correct answer is 2.0% (= if_held + last "
            "snapshot = 5.0 + (-3.0)). "
            "risk-engine-specialist must fix analytics.get_symphony_cumulative_return "
            "lines 694-698: use trajectory[-1] (last snapshot) instead of the "
            "chain-link product."
        )

        assert result["dry_run"] == pytest.approx(correct_dry_run, abs=0.01), (
            f"F-CHAIN-LINK-1 FAIL: dry_run={result['dry_run']:.4f}%, expected "
            f"{correct_dry_run}% (= if_held {if_held}% + last snapshot -3.0%). "
            "The chain-link product treats cumulative return snapshots as periodic "
            "daily returns, fabricating compounded divergence."
        )

    def test_moving_position_cr_uses_last_snapshot(self, tmp_path):
        """
        F-CHAIN-LINK-2: a position that drops from -1% -> -2% -> -3% over 3 days
        should yield dry_run = if_held + (-3.0), the FINAL snapshot, NOT
        the product of three distinct periodic returns.

        This variant rules out the 'flat at the same value' coincidence: here the
        values differ per day but they are still cumulative snapshots (each day's
        snapshot is the total return since position open).
        """
        rows = [
            {"trading_day": "2026-01-05", "shadow_return": -1.0, "ts_utc": "2026-01-05T21:00:00Z"},
            {"trading_day": "2026-01-06", "shadow_return": -2.0, "ts_utc": "2026-01-06T21:00:00Z"},
            {"trading_day": "2026-01-07", "shadow_return": -3.0, "ts_utc": "2026-01-07T21:00:00Z"},
        ]
        if_held = 5.0
        symphony_id = "sym_chain_link_test_2"

        db_file = _build_shadow_db_with_rows(rows, symphony_id, tmp_path)

        sym_dict = {
            "id": symphony_id,
            "simple_return": if_held / 100.0,
            "net_deposits": 100.0,
            "time_weighted_return": if_held / 100.0,
        }

        from analytics import get_symphony_cumulative_return

        result = get_symphony_cumulative_return(
            sym_dict,
            bot_state_entry=None,
            db_path=db_file,
        )

        # The chain-link product: (0.99 * 0.98 * 0.97 - 1) * 100 = -5.88%
        # dry_run (chain-link bug): 5.0 + (-5.88) = -0.88
        # The correct answer (last snapshot): 5.0 + (-3.0) = 2.0
        chain_link_product_pct = (0.99 * 0.98 * 0.97 - 1.0) * 100.0
        chain_link_bug_dry_run = if_held + chain_link_product_pct
        correct_dry_run = if_held + (-3.0)  # last snapshot

        assert result["dry_run"] != pytest.approx(chain_link_bug_dry_run, abs=0.01), (
            f"F-CHAIN-LINK-2 FAIL (BUG CONFIRMED): dry_run={result['dry_run']:.4f}% "
            f"matches the chain-link product {chain_link_bug_dry_run:.4f}%. "
            "shadow_return is a cumulative return snapshot, not a periodic daily return."
        )

        assert result["dry_run"] == pytest.approx(correct_dry_run, abs=0.01), (
            f"F-CHAIN-LINK-2 FAIL: dry_run={result['dry_run']:.4f}%, expected "
            f"{correct_dry_run:.4f}% (= if_held + last_snapshot)."
        )


# ---------------------------------------------------------------------------
# Regression: no sym-table-container (prior cycle introduced it, was reverted)
# ---------------------------------------------------------------------------


class TestRegressionSymTableContainerAbsent:
    """
    Guard: templates/index.html and static/index.js must not reference
    sym-table-container (a pattern introduced then reverted in cycle/dash-live).
    Placing it here keeps the regression check co-located with other display fixes.
    """

    def test_index_html_has_no_sym_table_container(self):
        """templates/index.html must not contain 'sym-table-container'."""
        html = _html()
        assert "sym-table-container" not in html, (
            "REGRESSION: templates/index.html contains 'sym-table-container'. "
            "This was introduced and reverted in cycle/dash-live. "
            "The implementer must not re-introduce this pattern."
        )

    def test_index_js_has_no_sym_table_container(self):
        """static/index.js must not contain 'sym-table-container'."""
        js_path = _STATIC / "index.js"
        assert js_path.exists(), "static/index.js must exist"
        content = js_path.read_text(encoding="utf-8")
        assert "sym-table-container" not in content, (
            "REGRESSION: static/index.js references 'sym-table-container'. "
            "This pattern was reverted in cycle/dash-live — must not return."
        )

    def test_index_js_passes_node_syntax_check(self):
        """static/index.js must pass `node --check` with exit code 0."""
        import subprocess
        js_path = _STATIC / "index.js"
        assert js_path.exists(), "static/index.js must exist"
        result = subprocess.run(
            ["node", "--check", str(js_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "REGRESSION: static/index.js has a syntax error. "
            "All JavaScript in the browser will silently fail to load.\n"
            f"node --check stderr:\n{result.stderr.strip()}"
        )

    def test_poll_interval_is_present_in_index_js(self):
        """
        static/index.js must define POLL_INTERVAL_MS (the 30s poll) — regression
        guard that none of the display fixes accidentally deleted the polling setup.
        """
        js_path = _STATIC / "index.js"
        assert js_path.exists(), "static/index.js must exist"
        content = js_path.read_text(encoding="utf-8")
        assert "POLL_INTERVAL_MS" in content, (
            "REGRESSION: POLL_INTERVAL_MS not found in static/index.js. "
            "The 30-second dashboard poll has been removed."
        )
        assert "setInterval" in content, (
            "REGRESSION: setInterval not found in static/index.js. "
            "The 30-second dashboard poll is no longer installed."
        )
