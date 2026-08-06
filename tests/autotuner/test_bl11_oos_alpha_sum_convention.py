"""RED — BL-11 (audit #118 T6, docs/audit/TWO-WEEK-REVIEW-2026-08-04.md §4/§6):
oos_alpha's multi-day-SUM convention is unannotated at both its computation
site and its DB-column definition.

THE BUG: autotuner.py's ``total_guard_alpha`` (accumulation begins at
autotuner.py:1958, inside ``run_simulation`` — shifted from the plan's :1928+
citation) SUMS guard-alpha across every triggered OOS day; ``avg_oos_alpha =
oos_alpha / test_days_count`` (autotuner.py:3043) exists specifically to
un-inflate it for human reading. This convention is why raw ``oos_alpha``
values like -743%/-581% appear in the DB/logs — a sum artifact, not
corruption — but it is easy to misread without a comment at the point of
definition.

THE FIX:
  AC-17: a comment at (or immediately near) the total_guard_alpha
         accumulation start explaining the multi-day-SUM convention and
         cross-referencing avg_oos_alpha as the un-inflated per-day
         companion.
  AC-18: the SAME annotation (or a cross-reference to it) near the
         autotune_runs.oos_alpha column definition/docstring in database.py
         (save_autotune_run's existing per-column docstring block), so a
         reader inspecting the DB schema directly also sees the warning.

AC-19 (best-effort, verified feasible — team-lead ruling): a real
operator-facing surface DOES display the raw oos_alpha sum with no
avg_oos_alpha companion — static/ai_advisor.js's suggestion-card
"OOS alpha: <code>...</code>" render (sourced from ai_advisor.py:1375's
autotune_run.get("oos_alpha"), the SAME multi-day-SUM DB column). A genuine
companion-value fix is infeasible without a new schema column (avg_oos_alpha
is never persisted, only a local print-statement variable) — team-lead
ruling: disclosure-by-labeling instead, covered by
tests/app/test_bl11_ai_advisor_oos_alpha_label_disclosure.py (kept in
tests/app/ since it's a JS-source-scan, not an autotuner/database concern).
"""

from __future__ import annotations

import pathlib

_AUTOTUNER_PY = pathlib.Path(__file__).parent.parent.parent / "autotuner.py"
_DATABASE_PY = pathlib.Path(__file__).parent.parent.parent / "database.py"

# Flexible keyword sets — the exact wording is the implementer's call; these
# tests pin the CONTRACT (a sum-convention warning exists near the right
# anchor) without over-specifying phrasing.
_SUM_CONVENTION_WORDS = ("sum", "cumulative", "accumulat")
_AVG_CROSS_REF_WORDS = ("avg_oos_alpha",)


def _bounded_region(src: str, anchor: str, *, before: int = 400, after: int = 1200) -> str:
    """A window of source around the first occurrence of `anchor` — bounded,
    not a fixed line-number slice, so it survives unrelated line-shift drift
    elsewhere in the file (the BL-2/BL-4 rfind-fragility sweep lesson)."""
    idx = src.find(anchor)
    assert idx != -1, f"fixture integrity: anchor {anchor!r} not found in source"
    start = max(0, idx - before)
    end = min(len(src), idx + after)
    return src[start:end]


class TestAC17AutotunerAccumulationSiteAnnotated:
    def test_total_guard_alpha_accumulation_has_a_sum_convention_comment(self):
        src = _AUTOTUNER_PY.read_text(encoding="utf-8")
        region = _bounded_region(src, "total_guard_alpha = 0.0")
        lowered = region.lower()

        assert any(w in lowered for w in _SUM_CONVENTION_WORDS), (
            "no sum-convention comment found near autotuner.py's "
            "`total_guard_alpha = 0.0` accumulation start — AC-17 requires "
            "a comment explaining this accumulates a multi-day SUM, not a "
            "per-day or annualized figure"
        )
        assert any(w in region for w in _AVG_CROSS_REF_WORDS), (
            "the accumulation-site comment must cross-reference avg_oos_alpha "
            "(autotuner.py:3043) as the un-inflated per-day companion"
        )


class TestAC18DatabaseColumnDocstringAnnotated:
    def test_save_autotune_run_docstring_documents_oos_alpha_sum_convention(self):
        """CO-LOCATION, not mere presence: database.py's save_autotune_run
        docstring already contains unrelated "sum"/"accumulat" text (the
        migration-023 s_count paragraph: "S accumulator (SUM
        n_configs_searched...") and, separately, bare "oos_alpha" as a
        parameter name in the signature — checking for both anywhere in a
        wide window would pass vacuously today by coincidence, proving
        nothing about whether oos_alpha's OWN sum convention is documented.
        This test requires a sum-convention word within a tight window of an
        "oos_alpha" mention INSIDE THE DOCSTRING BODY specifically (i.e. a
        real prose sentence about the column, not the bare parameter name in
        the function signature)."""
        src = _DATABASE_PY.read_text(encoding="utf-8")
        sig_idx = src.find("def save_autotune_run(")
        assert sig_idx != -1, "fixture integrity: save_autotune_run not found"

        docstring_start = src.find('"""', sig_idx)
        assert docstring_start != -1, "fixture integrity: docstring opening not found"
        docstring_end = src.find('"""', docstring_start + 3)
        assert docstring_end != -1, "fixture integrity: docstring closing not found"
        docstring_body = src[docstring_start + 3 : docstring_end]
        lowered_body = docstring_body.lower()

        oos_alpha_positions = [
            m for m in range(len(lowered_body)) if lowered_body.startswith("oos_alpha", m)
        ]
        assert oos_alpha_positions, (
            "database.py's save_autotune_run DOCSTRING BODY (not just the "
            "parameter list in the signature) must mention oos_alpha in prose "
            "— AC-18 requires documenting its multi-day-SUM convention there"
        )

        _CO_LOCATION_WINDOW = 250
        has_colocated_sum_word = any(
            any(
                w in lowered_body[max(0, pos - _CO_LOCATION_WINDOW) : pos + _CO_LOCATION_WINDOW]
                for w in _SUM_CONVENTION_WORDS
            )
            for pos in oos_alpha_positions
        )
        assert has_colocated_sum_word, (
            "database.py's save_autotune_run docstring mentions oos_alpha but no "
            "sum-convention word (sum/cumulative/accumulat) appears within "
            f"{_CO_LOCATION_WINDOW} chars of any oos_alpha mention — AC-18 "
            "requires the SAME multi-day-SUM warning autotuner.py's "
            "accumulation-site comment carries, co-located with the column's "
            "own docstring prose, not merely present somewhere else in the "
            "same docstring block (this repo's docstring already contains an "
            "unrelated 'SUM' via the s_count/migration-023 paragraph)"
        )
