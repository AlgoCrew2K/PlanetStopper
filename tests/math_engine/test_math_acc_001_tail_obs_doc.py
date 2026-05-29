"""
MATH-ACC-001 [MEDIUM, doc-only] — tail_obs_count canonical-formula docstring pin.

WHY THIS TEST EXISTS
--------------------
The math-reaudit (2026-05-27, accuracy-findings.md §MATH-ACC-001) flagged that
the dispatch brief's phrasing "tail_obs_count canonical = floor(α·N)" telescopes
two distinct council bindings and omits the atom contribution. The *code* at
`compute_cvar_5pct_general_distribution` (math_engine.py:1253-1367) is correct —
it returns `n_tail_distinct = k + (1 if fractional_weight > 0 else 0)` per
Rockafellar-Uryasev (2002) general distribution with the Acerbi-Tasche (2002)
atom-contribution discipline.

Risk vector: a future doc-derived contract test could mis-pin the contract by
asserting `tail_obs_count == floor(α·N)` (without the atom term), which would
falsely fail the atom-contributes case (αN non-integer). This test family pins
the doctring source-text so doc drift is caught at pytest time, before it
propagates to a downstream contract test.

WHAT IS ASSERTED (RED before doc-only fix)
------------------------------------------
T1. `compute_cvar_5pct_general_distribution.__doc__` contains the substring
    `floor(alpha*N)` (or an equivalent the implementer commits to in their
    fix — the test asserts on what the doc-only fix commits to).
T2. The same docstring contains the substring `fractional_weight` somewhere
    in the formula explanation.
T3. The same docstring contains an Acerbi-Tasche 2002 (or "Acerbi & Tasche
    (2002)") citation — the atom-contribution discipline anchor.
T4. The same docstring contains the explicit single-line canonical formula
    `tail_obs_count = floor(alpha*N) + (1 if fractional_weight > 0 else 0)`
    so a future careless reader cannot telescope to just `floor(α·N)`.
T5. `CVaREstimate.__doc__` (the dataclass) contains the canonical formula
    substring with both terms — so the dataclass field-doc cannot drift
    independently of the function docstring.
T6. Behavior sanity (proves the doc matches the code, not just itself):
    with a pool that forces an αN-non-integer (atom contributes) case,
    the returned `tail_obs_count` equals `k + 1` where `k = floor(αN)`.
    This pins that any future doc edit cannot quietly walk away from
    the runtime contract.
T7. Behavior sanity (zero-fractional / αN-integer case): when αN is an
    exact integer, `fractional_weight == 0` and `tail_obs_count == k`.
    This pins the second branch of the canonical formula.

NOT ASSERTED (out of scope)
---------------------------
- Numeric CVaR values (those are pinned in test_cvar_5pct_replay_determinism
  and the M2-CVaR golden-pool fixtures; no producer-value duplication here per
  feedback_no_hardcoded_test_values).
- README §3.2 prose (a separate test would pin README drift; this file is
  source-code-docstring scoped per the brief).

SOURCE PROVENANCE
-----------------
- docs/audit/math-reaudit-2026-05-27/accuracy-findings.md §MATH-ACC-001
  (audit verdict + canonical formula `tail_obs_count = floor(α·N) +
  (1 if fractional_weight > 0 else 0)`)
- decision-science-council-synthesis.md §2.6 (canonical field-name binding)
- math_engine.py:1253-1367 (current correct code; line :1321 — `n_tail_distinct`)
- Rockafellar & Uryasev (2002), Optimization of Conditional Value-at-Risk
- Acerbi & Tasche (2002), On the coherence of expected shortfall — the
  atom-contribution discipline anchor that the audit asks be cited.

RED STATUS at fork-point e44944b
--------------------------------
- T1 PASS (docstring already says `floor(alpha * N)` at line 1262).
- T2 PASS (docstring already says `fractional_weight` at line 1264, 1271).
- T3 RED — docstring cites only Rockafellar-Uryasev, not Acerbi-Tasche.
- T4 RED — docstring splits the formula across lines (1262 + 1271); no
  single-line canonical `tail_obs_count = floor(alpha*N) + (1 if
  fractional_weight > 0 else 0)` substring exists.
- T5 RED — CVaREstimate dataclass docstring uses prose ("k_below + 1 if
  atom contributes, else k_below") rather than the canonical-formula
  substring; a doc-extractor scanning for `floor(alpha*N)` would miss it.
- T6 PASS at fork-point (code already correct) — guards GREEN against
  any production drift the implementer might accidentally introduce.
- T7 PASS at fork-point — same rationale as T6.

The GREEN fix is doc-only: update the two docstrings to (a) cite
Acerbi-Tasche 2002, (b) include the explicit single-line canonical formula
in both the function docstring and the dataclass docstring. No production
code change.
"""

from __future__ import annotations

import inspect
import re

import math_engine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Collapse whitespace so the canonical formula can be matched across
    line wraps in the docstring. We don't care about exact whitespace; we
    care about the *tokens* appearing in the right order on a single
    logical line."""
    return re.sub(r"\s+", " ", text).strip()


def _function_doc() -> str:
    fn = math_engine.compute_cvar_5pct_general_distribution
    doc = inspect.getdoc(fn) or ""
    assert doc, "compute_cvar_5pct_general_distribution must have a docstring"
    return doc


def _dataclass_doc() -> str:
    doc = inspect.getdoc(math_engine.CVaREstimate) or ""
    assert doc, "CVaREstimate must have a docstring"
    return doc


# ---------------------------------------------------------------------------
# T1 — floor(alpha*N) substring in function docstring
# ---------------------------------------------------------------------------

def test_function_docstring_contains_floor_alpha_n_substring():
    """The function docstring must contain `floor(alpha*N)` (whitespace-
    insensitive). This is the FIRST term of the canonical formula; it must
    be discoverable by a doc-extractor scanning for the canonical anchor."""
    doc_norm = _normalize(_function_doc())
    # Accept either `floor(alpha*N)` or `floor(alpha * N)` — they normalize
    # the same once spaces are collapsed AROUND tokens. We assert presence
    # of `floor(alpha` followed by `* N)` or `*N)` after normalization.
    assert re.search(r"floor\(alpha\s*\*\s*N\)", doc_norm), (
        "compute_cvar_5pct_general_distribution docstring must contain "
        "the canonical-formula token `floor(alpha*N)` (or `floor(alpha * N)`). "
        f"Got normalized docstring: {doc_norm!r}"
    )


# ---------------------------------------------------------------------------
# T2 — fractional_weight token in function docstring
# ---------------------------------------------------------------------------

def test_function_docstring_contains_fractional_weight_token():
    """The function docstring must mention `fractional_weight` — the atom
    partial weight that drives the +1 atom-contribution term. This is the
    SECOND token of the canonical formula."""
    doc = _function_doc()
    assert "fractional_weight" in doc, (
        "compute_cvar_5pct_general_distribution docstring must mention "
        "`fractional_weight` (the atom partial weight). Got docstring: "
        f"{doc!r}"
    )


# ---------------------------------------------------------------------------
# T3 — Acerbi-Tasche 2002 citation in function docstring
# ---------------------------------------------------------------------------

def test_function_docstring_cites_acerbi_tasche_2002():
    """The function docstring must cite Acerbi & Tasche (2002) — the
    primary source for the atom-contribution discipline. The audit brief
    (MATH-ACC-001) requires this citation be present so a reader landing
    on the function understands WHY the +1 atom-term exists, not just
    that it does."""
    doc = _function_doc()
    # Accept either "Acerbi & Tasche (2002)", "Acerbi-Tasche 2002",
    # "Acerbi-Tasche (2002)", or "Acerbi and Tasche (2002)".
    pattern = re.compile(
        r"Acerbi\s*(?:&|-|and)\s*Tasche\s*\(?\s*2002\s*\)?",
        re.IGNORECASE,
    )
    assert pattern.search(doc), (
        "compute_cvar_5pct_general_distribution docstring must cite "
        "Acerbi-Tasche (2002) — the atom-contribution discipline anchor. "
        f"Got docstring: {doc!r}"
    )


# ---------------------------------------------------------------------------
# T4 — explicit canonical-formula single-line substring in function docstring
# ---------------------------------------------------------------------------

def test_function_docstring_contains_canonical_formula_on_one_line():
    """The function docstring must contain the explicit canonical
    formula on a single logical line:

        tail_obs_count = floor(alpha*N) + (1 if fractional_weight > 0 else 0)

    Splitting `floor(alpha*N)` and the atom-term across distant lines
    (current state) is what enabled the brief's telescoped phrasing. A
    single-line canonical formula in the docstring prevents future
    doc-extractor drift. Whitespace-insensitive."""
    doc_norm = _normalize(_function_doc())
    # Tolerate `alpha*N` or `alpha * N`; tolerate `> 0` or `>0`.
    pattern = re.compile(
        r"tail_obs_count\s*=\s*floor\(alpha\s*\*\s*N\)\s*\+\s*"
        r"\(\s*1\s+if\s+fractional_weight\s*>\s*0\s+else\s+0\s*\)"
    )
    assert pattern.search(doc_norm), (
        "compute_cvar_5pct_general_distribution docstring must contain the "
        "canonical formula on a single line: "
        "`tail_obs_count = floor(alpha*N) + (1 if fractional_weight > 0 else 0)`. "
        f"Got normalized docstring: {doc_norm!r}"
    )


# ---------------------------------------------------------------------------
# T5 — CVaREstimate dataclass docstring contains canonical-formula substring
# ---------------------------------------------------------------------------

def test_dataclass_docstring_contains_canonical_formula_substring():
    """The CVaREstimate dataclass docstring must also contain the
    canonical-formula substring so the field-level documentation cannot
    drift independently of the function docstring. The prose form
    `k_below + 1 if atom contributes, else k_below` is insufficient
    because a doc-extractor scanning for the canonical anchor
    `floor(alpha*N)` will miss it."""
    doc_norm = _normalize(_dataclass_doc())
    pattern = re.compile(
        r"floor\(alpha\s*\*\s*N\)\s*\+\s*"
        r"\(\s*1\s+if\s+fractional_weight\s*>\s*0\s+else\s+0\s*\)"
    )
    assert pattern.search(doc_norm), (
        "CVaREstimate dataclass docstring must contain the canonical-formula "
        "substring "
        "`floor(alpha*N) + (1 if fractional_weight > 0 else 0)` "
        "so it cannot drift independently of the function docstring. "
        f"Got normalized docstring: {doc_norm!r}"
    )


# ---------------------------------------------------------------------------
# T6 — code behavior pin: atom-contributes case returns k+1
# ---------------------------------------------------------------------------

def test_atom_contributes_case_returns_k_plus_one():
    """Code/doc sync check: with a pool where alpha*N is non-integer,
    `tail_obs_count` must equal `floor(alpha*N) + 1`. This pins the
    code-side semantics so a future doc edit can never quietly walk
    away from the runtime contract.

    Setup: alpha=0.05, N=150 — alpha*N = 7.5, k = floor(7.5) = 7,
    fractional_weight = 0.5 > 0, so canonical formula → tail_obs_count = 8.
    Pool composition: use a deterministic mix that guarantees enough
    spread for stderr to be defined (>=2 distinct tail values, std>0).
    Values are NOT producer-computed quantities (they are test inputs);
    the assertion is on a structural count, not a CVaR magnitude."""
    # Deterministic pool of 150 distinct values from -1.0 to +1.0.
    # Negative tail contains 8 strictly-distinct values; atom contributes;
    # stderr is well-defined.
    pool = [(-1.0 + i * (2.0 / 149)) for i in range(150)]
    assert len(pool) == 150

    result = math_engine.compute_cvar_5pct_general_distribution(
        pool, alpha=0.05
    )

    # k = floor(0.05 * 150) = 7; atom contributes → tail_obs_count = 8.
    expected_k = int(0.05 * 150)
    assert expected_k == 7, "fixture sanity: floor(0.05*150) must be 7"
    expected_tail_obs = expected_k + 1
    assert result.tail_obs_count == expected_tail_obs, (
        f"With alpha*N=7.5 (atom contributes), tail_obs_count must equal "
        f"k+1={expected_tail_obs}, got {result.tail_obs_count}. The "
        "canonical formula docstring must stay in sync with this behavior."
    )
    # Sanity: the estimate itself must NOT be the insufficient sentinel —
    # otherwise tail_obs_count would be 0 (the dataclass invariant) and the
    # above assertion would be vacuous.
    assert result.cvar_pct is not None, (
        "Test fixture must produce a non-sentinel CVaREstimate; otherwise "
        "tail_obs_count is 0 by the dataclass invariant and this test is "
        "vacuous. Adjust the pool to ensure k >= CVAR_MIN_TAIL_OBS."
    )


# ---------------------------------------------------------------------------
# T7 — code behavior pin: zero-fractional / αN-integer case returns k
# ---------------------------------------------------------------------------

def test_zero_fractional_case_returns_k():
    """Code/doc sync check: when alpha*N is an exact integer,
    fractional_weight == 0 and tail_obs_count must equal k (NOT k+1).
    This pins the SECOND branch of the canonical formula — without it,
    a careless GREEN edit could collapse the conditional to always-+1.

    Setup: alpha=0.05, N=200 — alpha*N = 10.0 exactly, k = 10,
    fractional_weight = 0.0, canonical formula → tail_obs_count = 10.

    NOTE: When fractional_weight == 0, the atom-contribution branch is
    NOT taken; the tail set is exactly `sorted_pool[:k]`. This is the
    Acerbi-Tasche "no atom" branch of the discipline."""
    # Deterministic pool of 200 distinct values; well-spread so stderr is
    # well-defined.
    pool = [(-1.0 + i * (2.0 / 199)) for i in range(200)]
    assert len(pool) == 200

    result = math_engine.compute_cvar_5pct_general_distribution(
        pool, alpha=0.05
    )

    # k = floor(0.05 * 200) = 10; fractional_weight = 0 → tail_obs_count = 10.
    expected_k = int(0.05 * 200)
    assert expected_k == 10, "fixture sanity: floor(0.05*200) must be 10"
    expected_tail_obs = expected_k  # no atom term
    assert result.tail_obs_count == expected_tail_obs, (
        f"With alpha*N=10.0 (zero fractional_weight), tail_obs_count must "
        f"equal k={expected_tail_obs} (NO atom term), got "
        f"{result.tail_obs_count}. The canonical formula docstring must "
        "preserve the second branch of the conditional."
    )
    assert result.cvar_pct is not None, (
        "Fixture sanity: must produce a non-sentinel CVaREstimate."
    )
