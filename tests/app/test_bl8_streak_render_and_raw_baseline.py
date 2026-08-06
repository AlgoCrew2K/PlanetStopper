"""Sufficiency-review tightening — BL-8 (audit #118 T3), pre-agreed direct
pins per team-lead priority (1): the RENDERED text contract for both streak
variants (with genuine escHtml usage) + the streak computed off the RAW
(pre-normalization) baseline_decision, not `_normalize_autotune_row`'s
short-token remap.

Both concerns were flagged by bl5impl's own GREEN commit (9b1f349b) as
subtleties worth a dedicated regression guard:

  "the streak is computed from the RAW (pre-normalization) rows so its
  'Adopted AI' comparison always matches autotuner.py's real persisted
  literal, independent of _normalize_autotune_row's short-token remap."
  (app.py's api_autotune_runs docstring)

TestRawBaselineDecisionIndependence proves this is a REAL invariant, not
just a comment — even though `_BASELINE_DECISION_TOKENS` today happens not
to remap "Adopted AI" (confirmed: only "Reverted to Fallback"/"Applied"/
"Rejected" are mapped), a future change to that table must not silently
break the streak boundary detection. Same "defensive-code correctness,
currently unreachable, second-layer defense" class as this codebase's own
`_resolve_admission_mdd_baseline` precedent (advisors/strategy_builder_
engine.py) — worth testing on its own merits, not contingent on today's
map contents.

TestStreakRenderTextContract source-scans static/ai_advisor.js (no JS test
runner in this repo — consistent with every other JS-behavior test here,
e.g. tests/app/test_bl11_ai_advisor_oos_alpha_label_disclosure.py) for both
rendered text variants, confirms escHtml wraps the dynamic (streak_weeks)
variant specifically, and confirms the <2/streak case renders nothing.
"""

from __future__ import annotations

import pathlib

import pytest

import app as app_module
import database

_AI_ADVISOR_JS = pathlib.Path(__file__).parent.parent.parent / "static" / "ai_advisor.js"

_SYMPHONY_ID = "sym-bl8-tighten-001"


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestRawBaselineDecisionIndependence:
    def test_streak_boundary_survives_a_future_adopted_ai_remap(self, client, monkeypatch):
        """Seeds 3 real autotune_runs rows for one symphony: oldest is a
        genuine "Adopted AI" streak-breaker, the 2 more recent rows are
        non-adopted. Monkeypatches _BASELINE_DECISION_TOKENS to ALSO remap
        "Adopted AI" -> a short token (simulating a future extension of that
        table, which does not exist today) and asserts the streak still
        reads 2 (not 3) — proving the streak computation reads the RAW
        persisted baseline_decision literal via database.get_all_autotune_
        runs, never the row dict AFTER _normalize_autotune_row's remap has
        already been applied to it.
        """
        monkeypatch.setattr(
            app_module,
            "_BASELINE_DECISION_TOKENS",
            {**app_module._BASELINE_DECISION_TOKENS, "Adopted AI": "adopted"},
        )

        database.save_autotune_run(
            "2026-06-01T04:00:00",
            _SYMPHONY_ID,
            None,
            None,
            "Adopted AI",
            None,
            None,
        )
        database.save_autotune_run(
            "2026-06-08T04:00:00",
            _SYMPHONY_ID,
            None,
            None,
            "Reverted to Fallback",
            None,
            None,
        )
        database.save_autotune_run(
            "2026-06-15T04:00:00",
            _SYMPHONY_ID,
            None,
            None,
            "Reset to Global Default",
            None,
            None,
        )

        resp = client.get("/api/autotune-runs")
        assert resp.status_code == 200
        rows = resp.get_json()
        this_symphony_rows = [r for r in rows if r["symphony_id"] == _SYMPHONY_ID]
        assert len(this_symphony_rows) == 3, (
            f"fixture integrity: expected 3 rows for {_SYMPHONY_ID}, got {len(this_symphony_rows)}"
        )

        # Sanity: the DISPLAYED baseline_decision on the oldest row IS
        # normalized under the monkeypatched map (proves the monkeypatch
        # actually took effect on the render side).
        oldest = min(this_symphony_rows, key=lambda r: r["run_timestamp"])
        assert oldest["baseline_decision"] == "adopted", (
            "fixture integrity: the monkeypatched remap must apply to the "
            "row's own displayed baseline_decision field"
        )

        # The streak, computed from the SAME rows, must still correctly
        # recognize the Adopted AI boundary — 2, not 3 (which would mean the
        # remapped "adopted" token was compared against the literal
        # "Adopted AI" and never matched, silently inflating the streak).
        streak = this_symphony_rows[0]["never_adopted_streak"]
        assert streak["status"] == "streak"
        assert streak["streak_weeks"] == 2, (
            f"never_adopted_streak.streak_weeks={streak['streak_weeks']} — the "
            f"streak computation must read the RAW 'Adopted AI' literal (via "
            f"the un-normalized rows database.get_all_autotune_runs returns), "
            f"never the row dict AFTER _normalize_autotune_row's remap has "
            f"already replaced it with a short token that no longer matches "
            f"analytics._NEVER_ADOPTED_ADOPTED_TOKEN"
        )


class TestStreakRenderTextContract:
    def test_multi_run_streak_variant_wrapped_in_eschtml(self):
        """status=='streak' && streak_weeks>=2 branch: text must contain
        'consecutive runs without adopting a tuned config', and the dynamic
        (streak_weeks-containing) string must be passed through escHtml —
        never raw string concatenation directly into the DOM.
        """
        src = _AI_ADVISOR_JS.read_text(encoding="utf-8")
        idx = src.find("consecutive runs without adopting a tuned config")
        assert idx != -1, (
            "static/ai_advisor.js must render 'N consecutive runs without "
            "adopting a tuned config' for a genuine multi-run streak"
        )
        # The literal lives inside a string argument to escHtml(...) — walk
        # backward from the match to the nearest escHtml( call opening.
        window_before = src[max(0, idx - 200) : idx]
        assert "escHtml(" in window_before, (
            "the multi-run streak text must be passed through escHtml(...) — "
            f"found the text but no escHtml( call within 200 chars before it:"
            f"\n{window_before}"
        )

    def test_insufficient_history_variant_present(self):
        """status=='insufficient_history' branch: distinct informative text,
        AC-11's degrade — must never be confused with the multi-run variant."""
        src = _AI_ADVISOR_JS.read_text(encoding="utf-8")
        assert "adoption streak: insufficient history" in src, (
            "static/ai_advisor.js must render the AC-11 insufficient-history "
            "degrade text distinctly from the multi-run streak variant"
        )

    def test_both_variants_gated_on_never_adopted_streak_field(self):
        """Both variants must read from r.never_adopted_streak (the field
        /api/autotune-runs stamps per row) — not a re-derived or differently
        named field."""
        src = _AI_ADVISOR_JS.read_text(encoding="utf-8")
        assert "r.never_adopted_streak" in src or ".never_adopted_streak" in src, (
            "the streak render must read the never_adopted_streak field the "
            "route actually stamps onto each row"
        )

    def test_sub_two_week_streak_status_renders_nothing(self):
        """streak_weeks < 2 with status=='streak': the code must NOT fall
        through to an unconditional render — structural proof: streakHtml's
        only non-empty assignments are inside the two explicitly-guarded
        branches (>= 2 multi-run, or insufficient_history); there is no bare
        `else` branch between them that would render for the <2 case."""
        src = _AI_ADVISOR_JS.read_text(encoding="utf-8")
        decl_idx = src.find("var streakHtml = '';")
        assert decl_idx != -1, (
            "expected streakHtml to default to an empty string before the "
            "guarded branches assign it"
        )
        # The two branches are `if (...) { streakHtml = ...; } else if (...) { streakHtml = ...; }`
        # -- confirm there is NO bare `} else {` between the declaration and
        # the next `streakHtml;` usage (which would mean a fallback render
        # exists for every other case, including streak_weeks < 2).
        window_after = src[decl_idx : decl_idx + 900]
        assert "} else {" not in window_after, (
            "found a bare `else` branch after streakHtml's declaration — this "
            "would render something for the streak_weeks < 2 case, which "
            "must render NOTHING (a single non-adopted run isn't a pattern "
            f"worth flagging); window:\n{window_after}"
        )
