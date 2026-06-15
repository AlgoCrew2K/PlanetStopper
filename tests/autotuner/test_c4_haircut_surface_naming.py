"""
Cluster 4 — Decision D3 surface-consistency: no operator-facing surface may
present the Harvey & Liu selection statistic as a "Deflated Sharpe Ratio".

RED tests, written before the implementation.

D3 deletes the Bailey & López de Prado Deflated Sharpe Ratio and replaces it with
a Harvey & Liu 2015 multiple-testing haircut. The value persisted to the
autotune_runs `deflated_sharpe` column — and surfaced to Discord / the dashboard
— is, after this cycle, the haircut's selection t-statistic (Sortino·sqrt(T)),
NOT a Deflated Sharpe Ratio.

A column named `deflated_sharpe`, a Discord line reading "DSR", and a docstring
citing "Bailey & López de Prado 2014" that all actually carry a Harvey & Liu
t-statistic are a NAMING LIE — exactly what a math audit must not leave behind.
team-lead's ruling: this is a direct, in-scope consequence of the D3 deletion.

team-lead's two acceptable resolutions (team's call — these tests are written so
that EITHER satisfies the operator-facing assertions; only the column-rename
assertion is (a)-specific and is marked as such):
  (a) PREFERRED — additive rename: the autotune_runs column is renamed to an
      accurate name (e.g. `selection_tstat`), additive-first; autotuner writes
      it; reporting.py / dashboard labels update to match.
  (b) FALLBACK — keep the column name internally, but every operator-facing
      LABEL (Discord, dashboard) accurately names the statistic.
UNACCEPTABLE under either: a number labelled "Deflated Sharpe" / "DSR" to the
operator that is actually a t-statistic.

These tests assert the operator-facing surfaces (Discord report text, the
augment-results helper, source docstrings/labels) do not call the haircut
statistic a Deflated Sharpe Ratio. The exact replacement label is the
implementer's choice; the tests reject the WRONG name, they do not pin a
specific RIGHT one.

Reference: feature-plans/autotuner-statistics.md, Decision D3; team-lead ruling
2026-05-22.
"""

from __future__ import annotations

import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_REPORTING_SRC = _WORKTREE_ROOT / "reporting.py"
_ALPHA_BOT_SRC = _WORKTREE_ROOT / "alpha_bot_execution.py"
_DATABASE_SRC = _WORKTREE_ROOT / "database.py"


# ===========================================================================
# Operator-facing Discord label must not say "DSR" / "Deflated Sharpe"
# ===========================================================================


def test_discord_report_does_not_label_the_value_dsr():
    """
    D3 surface-consistency: the Discord end-of-day report must not label the
    autotuner selection statistic "DSR" or "Deflated Sharpe".

    reporting.py builds a per-symphony line that today reads
    `"Sharpe (naive / DSR / frozen-eval): ..."` — the middle slot carries the
    `deflated_sharpe` value. After D3 that value is a Harvey & Liu t-statistic;
    the "DSR" label is now a lie to the operator.

    Source-level check: reporting.py must contain no operator-facing "DSR" token
    and no "Deflated Sharpe" phrase. (A code comment that EXPLAINS the rename is
    fine — this check targets the f-string label text; we scan for the tokens as
    they would appear in the rendered Discord string.)
    """
    src = _REPORTING_SRC.read_text(encoding="utf-8")

    # The literal Discord-line label text. After D3 this exact substring must
    # be gone — it is what the operator reads in Discord.
    assert "/ DSR /" not in src, (
        "reporting.py still renders the Discord line "
        "`Sharpe (naive / DSR / frozen-eval)` — the middle slot carries the "
        "Harvey & Liu selection t-statistic after D3, not a Deflated Sharpe "
        "Ratio. The operator-facing 'DSR' label is a naming lie and must be "
        "replaced with an accurate label."
    )
    assert "Deflated Sharpe" not in src, (
        "reporting.py still uses the phrase 'Deflated Sharpe' on an "
        "operator-facing surface. D3 removed the DSR; the value is a Harvey & "
        "Liu t-statistic — it must not be presented as a Deflated Sharpe Ratio."
    )


def test_augment_results_helper_is_not_named_for_the_deleted_dsr():
    """
    D3 surface-consistency: `augment_optimization_results_with_dsr` in
    alpha_bot_execution.py is named for the deleted DSR. A function name is a
    behaviour description (project rule: names describe runtime behaviour); after
    D3 it injects a Harvey & Liu haircut statistic, not a DSR.

    The function must be renamed (the implementer chooses the accurate name) and
    its `_dsr_data` payload key likewise. This test pins that the stale `_dsr`
    naming is gone from the helper.
    """
    src = _ALPHA_BOT_SRC.read_text(encoding="utf-8")

    assert "augment_optimization_results_with_dsr" not in src, (
        "alpha_bot_execution.py still defines/uses "
        "`augment_optimization_results_with_dsr` — named for the D3-deleted DSR. "
        "Rename it to describe what it actually injects (the Harvey & Liu "
        "selection statistic)."
    )
    assert "_dsr_data" not in src, (
        "alpha_bot_execution.py still uses the `_dsr_data` payload key — named "
        "for the deleted DSR. Rename the key to match the haircut statistic."
    )


def test_reporting_does_not_consume_a_dsr_named_payload_key():
    """
    D3 surface-consistency: reporting.py reads the per-symphony stats payload via
    a `_dsr_data` key (the counterpart to the alpha_bot_execution producer). That
    key name is stale under D3 — it must be renamed in lockstep with the
    producer so the two sides stay consistent.
    """
    src = _REPORTING_SRC.read_text(encoding="utf-8")
    assert "_dsr_data" not in src, (
        "reporting.py still consumes the `_dsr_data` payload key — named for the "
        "deleted DSR. Rename it in lockstep with alpha_bot_execution.py's "
        "producer."
    )


# ===========================================================================
# DB column / docstring honesty
# ===========================================================================


def test_autotune_runs_column_docstring_does_not_misattribute_to_bailey_lopez():
    """
    D3 surface-consistency: database.py's autotune_runs documentation must not
    describe the stored value as a "DSR value ... (Bailey & López de Prado
    2014)". After D3 the value is a Harvey & Liu 2015 selection statistic;
    crediting Bailey & López de Prado is a citation lie.

    This assertion holds under BOTH resolutions (a) and (b) — whether or not the
    column is renamed, its documentation must not misattribute the statistic.
    """
    src = _DATABASE_SRC.read_text(encoding="utf-8")

    # The specific misattributing docstring fragment present today.
    assert "DSR value for the AI-branch best trial" not in src, (
        "database.py still documents the autotune_runs column as a 'DSR value "
        "for the AI-branch best trial' — D3 replaced the DSR with a Harvey & "
        "Liu haircut statistic. The column documentation must describe what is "
        "actually stored."
    )
    # Bailey & López de Prado must not be cited as the source of this column's
    # value (the DSR is gone). A Harvey & Liu citation is the accurate one.
    lowered = src.lower()
    if "lópez de prado" in lowered or "lopez de prado" in lowered:
        # If the name still appears, it must not be next to the column doc.
        # Narrow check: the autotune_runs column block must not cite it.
        col_doc_region = src[
            max(0, src.find("deflated_sharpe")) : src.find("deflated_sharpe") + 600
        ]
        assert "Prado" not in col_doc_region, (
            "database.py's autotune_runs column documentation still cites "
            "López de Prado — the DSR source. After D3 the stored statistic is "
            "from Harvey & Liu 2015; cite that, or describe the haircut."
        )


def test_autotune_runs_column_is_renamed_to_an_accurate_name():
    """
    D3 surface-consistency — RESOLUTION (a), team-lead's PREFERRED path: the
    autotune_runs `deflated_sharpe` column is renamed (additive-first) to a name
    that accurately describes the Harvey & Liu selection statistic it now holds.

    A column literally named `deflated_sharpe` carrying a t-statistic is a naming
    lie that propagates into the /api/autotune-runs JSON and every future reader.
    Resolution (a) renames it; resolution (b) keeps the name internally and
    fixes only the operator labels.

    This test enforces resolution (a). IF the team formally adopts resolution
    (b) instead, this single test is the one to drop — the operator-label tests
    above hold under either resolution and must NOT be dropped. The cycle-complete
    note must state which resolution was chosen and why.
    """
    src = _DATABASE_SRC.read_text(encoding="utf-8")

    assert "deflated_sharpe" not in src, (
        "database.py still defines/writes a column named `deflated_sharpe`. "
        "D3 resolution (a) — team-lead's preferred path — renames it (additive-"
        "first) to an accurate name (e.g. `selection_tstat`) for the Harvey & "
        "Liu statistic it now holds. If the team formally chose resolution (b) "
        "(keep the column name internally, fix only operator labels), drop THIS "
        "test only — and record the (b) decision in cycle-complete."
    )
