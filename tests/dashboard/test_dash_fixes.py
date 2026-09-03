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
F-CHAIN-LINK  GOLDEN REGRESSION tests pinning chain-link CR/MDD as correct
              per-day compounding (AC-M1F.3.2 / PA-M1F-16). NOT A BUG.

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
        mdd_context = html[max(0, mdd_section_start - 200) : mdd_section_start + 600]
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
            "F-2d FAIL: no comp-mdd-bot-text element found. Cannot verify alpha-delta suppression."
        )
        mdd_row_context = html[max(0, mdd_row_start - 100) : mdd_row_start + 800]
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
            "insufficient history" in html.lower() and "cfg-cell" in html
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
        has_title_attr = "includes" in html.lower() and "cash" in html.lower() and "Today" in html
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
        media_block = html[media_idx : media_idx + 400]
        assert "hero-grid" in media_block, (
            "F-4b FAIL: @media block does not target .hero-grid. "
            "The 1-column layout for narrow viewports is not applied."
        )
        # The block must set a single-column layout.
        has_one_column = "1fr" in media_block and "grid-template-columns" in media_block
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
        nearby = html[gave_up_idx : gave_up_idx + 200]
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
        assert saved_idx != -1, "F-5b FAIL: 'saved +' text not found in templates/index.html."
        nearby = html[saved_idx : saved_idx + 200]
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
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_file = str(tmp_path / f"shadow_{symphony_id[:12]}.db")
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
    conn.execute("CREATE INDEX idx_sym_day ON shadow_history (symphony_id, trading_day, ts_utc)")
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


class TestChainLinkGoldenPerDayReturns:
    """
    F-CHAIN-LINK: GOLDEN tests for the CORRECTED guard-alpha divergence formula.

    FINAL ORACLE (independent adjudicator 2026-06-03):
      shadow_return is PER-DAY (daily reset confirmed: database.py:342-343).
      The bug is NOT about removing if_held. The correct divergence formula is:
          divergence = (prod_shadow - prod_current) * 100
          dry_run = if_held + divergence

      _build_shadow_db_with_rows sets current_return == shadow_return on all rows
      (untriggered scenario). With shadow == current, divergence == 0 exactly,
      so dry_run == if_held for any untriggered position.

      These tests are RED against the current analytics.py (which yields
      if_held + chain_link instead of if_held when shadow==current) and will go
      GREEN after the divergence fix in _get_shadow_divergence_trajectory +
      get_symphony_cumulative_return.

    Fixture: tests/fixtures/math/shadow_cr_chain_link_golden.json (rows only).
    Expected values: divergence==0 → dry_run==if_held. Tolerance abs=1e-9.
    """

    def test_flat_per_day_returns_compound_correctly(self, tmp_path):
        """
        F-CHAIN-LINK-1 (GOLDEN): three per-day shadow_return = -3.0% each.
        Each -3% is today's daily change, not a cumulative snapshot.
        Chain-link CR = (0.97^3 - 1)*100 = -8.7327%.
        MDD = peak-to-trough of cumulative series starting at cum[0]=-3.0:
              cum_series = [-3.0, -5.91, -8.7327]; peak=-3.0, trough=-8.7327 -> MDD=5.7327%

        Fixture: tests/fixtures/math/shadow_cr_chain_link_golden.json (rows only;
        expected values are derived in-test from the formula).

        Tolerance: rel=1e-9 — these are exact floating-point arithmetic; any deviation
        from the formula is a consumer bug, not a rounding artefact.
        """
        fixture = _load_chain_link_fixture()
        rows = fixture["shadow_history_rows"]
        if_held = fixture["if_held_pct"]
        symphony_id = "sym_chain_link_t1"

        db_file = _build_shadow_db_with_rows(rows, symphony_id, tmp_path)

        sym_dict = {
            "id": symphony_id,
            "simple_return": if_held / 100.0,
            "net_deposits": 100.0,  # non-zero -> uses simple_return path, not TWR fallback
            "time_weighted_return": if_held / 100.0,
            # get_symphony_max_drawdown gates on max_drawdown presence (analytics.py:721)
            "max_drawdown": 0.05,  # 5% if_held MDD; value is not tested here
        }

        from analytics import get_symphony_cumulative_return, get_symphony_max_drawdown

        cr_result = get_symphony_cumulative_return(sym_dict, bot_state_entry=None, db_path=db_file)
        mdd_result = get_symphony_max_drawdown(sym_dict, bot_state_entry=None, db_path=db_file)

        assert cr_result is not None and "dry_run" in cr_result, (
            f"F-CHAIN-LINK-1 FAIL: CR returned {cr_result!r}."
        )
        assert mdd_result is not None and "dry_run" in mdd_result, (
            f"F-CHAIN-LINK-1 FAIL: MDD returned {mdd_result!r}."
        )

        # CORRECTED oracle: _build_shadow_db_with_rows sets current_return == shadow_return.
        # divergence = (prod_shadow - prod_current)*100 = 0 when shadow == current.
        # dry_run = if_held + 0 = if_held exactly.
        # Buggy code: dry_run = if_held + (prod_shadow - 1)*100 = if_held + chain_link.
        expected_dry_run = if_held  # divergence == 0

        assert cr_result["dry_run"] == pytest.approx(expected_dry_run, abs=1e-9), (
            f"F-CHAIN-LINK-1 CR FAIL: dry_run={cr_result['dry_run']:.6f}%, "
            f"expected if_held={expected_dry_run:.6f}% (divergence=0 when shadow==current). "
            "Correct formula: dry_run = if_held + (prod_shadow - prod_current)*100."
        )

        # [SUPERSEDED formula, DE-PERF-WINDOW-TRUTH-001]: the OLD if_held-anchored
        # divergence formula made the bot equity path flat at if_held whenever
        # shadow==current (any divergence chain contributes exactly 0), so MDD
        # was trivially 0.0 for THIS scenario regardless of the shadow_return
        # values themselves. Under the AC-1 remediation (analytics.py @
        # 3ec97048), dry_run is the NORMALIZED peak-to-trough of the
        # shadow_return series alone -- computed with zero reference to
        # if_held -- so a genuinely declining series like [-3.0, -3.0, -3.0]
        # produces a real, non-zero drawdown. Since current_return ==
        # shadow_return on every row here, if_held (also independently
        # recomputed, normalized peak-to-trough of current_return) equals
        # dry_run exactly -- both compound the identical sequence.
        # Verified via quantstats.stats.max_drawdown() directly (matches
        # tests/analytics/test_mdd_window_truth.py's cross-check convention),
        # not hand-derived: 8.732700000000005.
        expected_mdd = 8.732700000000005
        assert mdd_result["dry_run"] == pytest.approx(expected_mdd, rel=1e-9), (
            f"F-CHAIN-LINK-1 MDD FAIL: dry_run={mdd_result['dry_run']:.6f}%, "
            f"expected {expected_mdd:.6f}% (normalized peak-to-trough of the "
            f"shadow_return series [-3.0, -3.0, -3.0] alone -- NOT 0.0, which "
            f"would mean dry_run is still anchored to a flat if_held-based "
            f"equity path instead of being computed from shadow_return "
            f"independently)."
        )
        assert mdd_result["if_held"] == pytest.approx(mdd_result["dry_run"], abs=1e-9), (
            "if_held must equal dry_run here -- current_return == "
            "shadow_return on every row, so both legs compound the identical "
            "sequence through the identical normalized formula"
        )

    def test_mixed_per_day_returns_cr_and_mdd_match_formula(self, tmp_path):
        """
        F-CHAIN-LINK-2 (GOLDEN): four per-day returns [-0.5, 0.8, -1.2, 2.0].
        Derives CR and MDD entirely from the documented formula — not from producer
        output — to avoid circular validation.

        Expected CR chain-link: (0.995 * 1.008 * 0.988 * 1.020 - 1) * 100
        Expected MDD: peak(cum_series[1]=0.296) - trough(cum_series[2]=-0.9076) = 1.2036%

        Tolerance: rel=1e-9 — same rationale as T1.
        """
        rows = [
            {"trading_day": "2026-01-05", "shadow_return": -0.5, "ts_utc": "2026-01-05T21:00:00Z"},
            {"trading_day": "2026-01-06", "shadow_return": 0.8, "ts_utc": "2026-01-06T21:00:00Z"},
            {"trading_day": "2026-01-07", "shadow_return": -1.2, "ts_utc": "2026-01-07T21:00:00Z"},
            {"trading_day": "2026-01-08", "shadow_return": 2.0, "ts_utc": "2026-01-08T21:00:00Z"},
        ]
        if_held = 3.0
        symphony_id = "sym_chain_link_t2"

        db_file = _build_shadow_db_with_rows(rows, symphony_id, tmp_path)

        sym_dict = {
            "id": symphony_id,
            "simple_return": if_held / 100.0,
            "net_deposits": 100.0,
            "time_weighted_return": if_held / 100.0,
            "max_drawdown": 0.05,  # gates get_symphony_max_drawdown (analytics.py:721)
        }

        from analytics import get_symphony_cumulative_return, get_symphony_max_drawdown

        cr_result = get_symphony_cumulative_return(sym_dict, bot_state_entry=None, db_path=db_file)
        mdd_result = get_symphony_max_drawdown(sym_dict, bot_state_entry=None, db_path=db_file)

        # CORRECTED oracle: _build_shadow_db_with_rows sets current_return == shadow_return.
        # divergence = (prod_shadow - prod_current)*100 = 0 → dry_run = if_held.
        expected_dry_run = if_held  # divergence == 0

        assert cr_result["dry_run"] == pytest.approx(expected_dry_run, abs=1e-9), (
            f"F-CHAIN-LINK-2 CR FAIL: dry_run={cr_result['dry_run']:.6f}%, "
            f"expected if_held={expected_dry_run:.6f}% (divergence=0 when shadow==current)."
        )

        # [SUPERSEDED formula, DE-PERF-WINDOW-TRUTH-001]: see test 1's comment
        # above for the full rationale -- MDD is no longer anchored to a flat
        # if_held-based equity path. This test's own ORIGINAL docstring
        # ("Expected MDD: ... = 1.2036%") already computed the real
        # peak-to-trough of this shadow_return series a few lines above the
        # (superseded) 0.0 assertion below it -- an internal contradiction
        # that is itself evidence the 0.0 pinned the retired formula, not a
        # genuine property of this series. That 1.2036% figure was itself the
        # OLD UN-NORMALIZED peak-to-trough (peak - value, no /peak) --
        # verified via quantstats.stats.max_drawdown() directly (the
        # confirmed AC-0b convention), the correct NORMALIZED value is
        # 1.1999999999999966 (peak is NOT at the series start here -- day 1's
        # +0.8% return makes the peak ~0.296% NAV, so normalizing by that peak
        # genuinely differs from the un-normalized subtraction, by design).
        expected_mdd = 1.1999999999999966
        assert mdd_result["dry_run"] == pytest.approx(expected_mdd, rel=1e-9), (
            f"F-CHAIN-LINK-2 MDD FAIL: dry_run={mdd_result['dry_run']:.6f}%, "
            f"expected {expected_mdd:.6f}% (normalized peak-to-trough of "
            f"shadow_return=[-0.5, 0.8, -1.2, 2.0] alone)."
        )
        assert mdd_result["if_held"] == pytest.approx(mdd_result["dry_run"], abs=1e-9), (
            "if_held must equal dry_run here -- current_return == "
            "shadow_return on every row"
        )

    def test_portfolio_aggregate_dry_run_equals_value_weighted_per_symphony(self, tmp_path):
        """
        F-CHAIN-LINK-3 (consistency): the portfolio-level dry_run from
        get_portfolio_cumulative_return must equal the value-weighted combination
        of the two per-symphony dry_runs. This confirms the roll-up path is
        consistent with the per-symphony formula; no new numbers are introduced.
        """
        # Symphony A: 3-day, per-day returns [-1.0, 0.5, -0.5], value=10000
        rows_a = [
            {"trading_day": "2026-01-05", "shadow_return": -1.0, "ts_utc": "2026-01-05T21:00:00Z"},
            {"trading_day": "2026-01-06", "shadow_return": 0.5, "ts_utc": "2026-01-06T21:00:00Z"},
            {"trading_day": "2026-01-07", "shadow_return": -0.5, "ts_utc": "2026-01-07T21:00:00Z"},
        ]
        # Symphony B: 3-day, per-day returns [0.3, -0.2, 0.6], value=5000
        rows_b = [
            {"trading_day": "2026-01-05", "shadow_return": 0.3, "ts_utc": "2026-01-05T21:00:00Z"},
            {"trading_day": "2026-01-06", "shadow_return": -0.2, "ts_utc": "2026-01-06T21:00:00Z"},
            {"trading_day": "2026-01-07", "shadow_return": 0.6, "ts_utc": "2026-01-07T21:00:00Z"},
        ]
        # Per-symphony DBs are not used here (shared_db handles both); these calls
        # are kept only to exercise the helper and ensure cache isolation.
        _build_shadow_db_with_rows(rows_a, "sym_agg_a", tmp_path)
        _build_shadow_db_with_rows(rows_b, "sym_agg_b", tmp_path)

        # The portfolio aggregator takes a common db_path; build one DB with both.
        shared_db = str(tmp_path / "shared.db")
        # Seed both symphonies into one DB.
        conn = sqlite3.connect(shared_db)
        conn.execute("""
            CREATE TABLE shadow_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symphony_id TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                trading_day TEXT NOT NULL,
                current_return REAL NOT NULL,
                shadow_return REAL NOT NULL,
                is_post_trigger INTEGER NOT NULL DEFAULT 0,
                position_epoch TEXT
            )
        """)
        epoch = "2026-01-04T14:30:00Z"
        for sym_id, rows in [("sym_agg_a", rows_a), ("sym_agg_b", rows_b)]:
            for row in rows:
                conn.execute(
                    "INSERT INTO shadow_history "
                    "(symphony_id, ts_utc, trading_day, current_return, shadow_return, "
                    "is_post_trigger, position_epoch) VALUES (?, ?, ?, ?, ?, 0, ?)",
                    (
                        sym_id,
                        row["ts_utc"],
                        row["trading_day"],
                        row["shadow_return"],
                        row["shadow_return"],
                        epoch,
                    ),
                )
        conn.commit()
        conn.close()

        import database as _db

        _db._shadow_cr_cache.clear()

        if_held_a = 4.0  # percent
        if_held_b = 2.0
        value_a = 10000.0
        value_b = 5000.0

        sym_a = {
            "id": "sym_agg_a",
            "simple_return": if_held_a / 100.0,
            "net_deposits": 100.0,
            "time_weighted_return": if_held_a / 100.0,
            "value": value_a,
        }
        sym_b = {
            "id": "sym_agg_b",
            "simple_return": if_held_b / 100.0,
            "net_deposits": 100.0,
            "time_weighted_return": if_held_b / 100.0,
            "value": value_b,
        }

        from analytics import (
            get_portfolio_cumulative_return,
            get_symphony_cumulative_return,
        )

        per_sym_a = get_symphony_cumulative_return(sym_a, bot_state_entry=None, db_path=shared_db)
        per_sym_b = get_symphony_cumulative_return(sym_b, bot_state_entry=None, db_path=shared_db)
        portfolio = get_portfolio_cumulative_return([sym_a, sym_b], bot_state={}, db_path=shared_db)

        # Value-weighted dry_run: (A.dry_run * value_a + B.dry_run * value_b) / (value_a + value_b)
        expected_portfolio_dry = (
            per_sym_a["dry_run"] * value_a + per_sym_b["dry_run"] * value_b
        ) / (value_a + value_b)

        assert portfolio["dry_run"] == pytest.approx(expected_portfolio_dry, rel=1e-9), (
            f"F-CHAIN-LINK-3 FAIL: portfolio dry_run={portfolio['dry_run']:.6f}%, "
            f"expected value-weighted {expected_portfolio_dry:.6f}%. "
            "The portfolio roll-up is not consistent with per-symphony chain-link."
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
