"""RED — BL-10 (audit #118 T5, docs/audit/TWO-WEEK-REVIEW-2026-08-04.md §4/§6):
6 never-wired autotune_runs columns lack any documentation note.

THE BUG: ``autotune_runs.deflated_sharpe`` is intentionally, already-
documented dead (tests/autotuner/test_c4_dsr_machinery_removed.py, Decision
D3 — and verified here at consumer-discovery time: deflated_sharpe was
actually REMOVED from the schema entirely, not merely commented as dead —
grepped database.py, zero occurrences). Six OTHER columns —
``ce_metric``/``cvar_feasible``/``lambda_budget``/``sortino_sentinel_pct``/
``fold_role``/``account_id`` — exist in the schema (database.py's
autotune_runs CREATE TABLE, ~:176-201) but have NO parameter in
save_autotune_run's signature at all (database.py:692-715).

CONSUMER-DISCOVERY CORRECTION (escalated + PM-approved before writing):
the plan's own "account_id/sortino_sentinel_pct/fold_role are actively-used
on a DIFFERENT table" framing is imprecise. Directly verified:
  (a) ce_metric/cvar_feasible/lambda_budget — truly never wired: no writer,
      no reader beyond the bare schema column, anywhere in the repo.
  (b) account_id/sortino_sentinel_pct — have an ORPHANED WRITER:
      database.record_autotune_run (database.py:873) inserts both into
      autotune_runs (NOT port_state, as the plan's own port_state claim
      also mischaracterizes) — but that writer has ZERO callers repo-wide
      (grepped the whole tree; only match is its own `def` line). The
      production save_autotune_run path never touches them.
  (c) fold_role — migration 019 (019_fold_role_columns.sql) adds it to
      autotune_runs with a real intended semantic (train/validation/
      frozen_eval/NULL) and it IS actively READ — by advisor_ro_query's
      defensive wall guard (database.py ~2469-2605, the frozen_eval-row
      exclusion). But it has NO WRITER ANYWHERE — not even the orphaned
      record_autotune_run (whose signature also has no fold_role param).
      Read-only-defensive, never written, full stop.

THE FIX (AC-15): each of the 6 columns gains an explicit schema-comment or
save_autotune_run docstring note marking it never-implemented for THIS table
— distinct wording from deflated_sharpe's intentionally-removed status,
since these 6 were never wired in the first place rather than removed.

AC-16: documentation-only — no schema migration, no column drop (project's
additive-first / never-destructive-in-one-step standard).

Edge case (plan): a legacy row where one of the 6 columns is somehow non-NULL
from a historical accessor no longer in use — the note must say "never
populated by the current save_autotune_run path," never "always NULL" (a
claim this test explicitly guards against, since it isn't provably true).
"""

from __future__ import annotations

import pathlib

_DATABASE_PY = pathlib.Path(__file__).parent.parent.parent / "database.py"

_NEVER_WIRED_COLUMNS = (
    "ce_metric",
    "cvar_feasible",
    "lambda_budget",
    "sortino_sentinel_pct",
    "fold_role",
    "account_id",
)

_NON_IMPLEMENTATION_SIGNAL_WORDS = (
    "never wired",
    "not wired",
    "never implemented",
    "unimplemented",
    "never populated",
    "not populated",
)

_FORBIDDEN_OVERCLAIM = "always null"


def _bounded_region_near_save_autotune_run(
    src: str, *, before: int = 400, after: int = 4000
) -> str:
    idx = src.find("def save_autotune_run(")
    assert idx != -1, "fixture integrity: save_autotune_run not found"
    start = max(0, idx - before)
    end = min(len(src), idx + after)
    return src[start:end]


def _bounded_region_near_autotune_runs_schema(
    src: str, *, before: int = 100, after: int = 3000
) -> str:
    """The AC-15 note may instead live as a schema comment on the
    `CREATE TABLE ... autotune_runs` definition (~database.py:176-201) rather
    than in save_autotune_run's docstring — the plan permits either
    placement ('an explicit schema-comment OR save_autotune_run docstring
    note'). Checking only ONE of the two real placements would false-negative
    a valid implementation that chose the other."""
    idx = src.find("CREATE TABLE IF NOT EXISTS autotune_runs")
    assert idx != -1, "fixture integrity: autotune_runs CREATE TABLE not found"
    start = max(0, idx - before)
    end = min(len(src), idx + after)
    return src[start:end]


def _combined_search_region(src: str) -> str:
    """Union of both permitted placements — a column note satisfying AC-15
    in EITHER location is accepted."""
    return (
        _bounded_region_near_save_autotune_run(src)
        + "\n"
        + _bounded_region_near_autotune_runs_schema(src)
    )


class TestSixNeverWiredColumnsDocumented:
    def test_each_column_has_a_non_implementation_signal_nearby(self):
        src = _DATABASE_PY.read_text(encoding="utf-8")
        region = _combined_search_region(src)
        lowered = region.lower()

        missing = []
        for col in _NEVER_WIRED_COLUMNS:
            col_positions = [m for m in range(len(lowered)) if lowered.startswith(col, m)]
            if not col_positions:
                missing.append((col, "not mentioned at all near save_autotune_run"))
                continue
            has_signal = any(
                any(
                    w in lowered[max(0, pos - 300) : pos + 300]
                    for w in _NON_IMPLEMENTATION_SIGNAL_WORDS
                )
                for pos in col_positions
            )
            if not has_signal:
                missing.append((col, "mentioned but no non-implementation signal word nearby"))

        assert not missing, (
            "BL-10 (AC-15) requires each of the 6 never-wired autotune_runs columns "
            "to carry an explicit never-implemented-for-this-table note near "
            f"save_autotune_run's docstring/schema comment. Missing/insufficient: {missing}"
        )

    def test_no_overclaiming_always_null(self):
        """Edge case guard: a legacy row could be non-NULL from a historical
        accessor no longer in use — the note must not claim 'always NULL',
        only 'never populated by the current save_autotune_run path.'"""
        src = _DATABASE_PY.read_text(encoding="utf-8")
        region = _combined_search_region(src)
        assert _FORBIDDEN_OVERCLAIM not in region.lower(), (
            "the BL-10 documentation note must not claim columns are 'always NULL' "
            "— only that they are never populated by the CURRENT save_autotune_run "
            "path (a legacy accessor may have written a non-NULL value historically)"
        )
