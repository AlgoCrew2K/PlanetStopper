"""
RED-phase pinning tests for Concern #27 — Composer response.text echo
(secret-leakage risk) in `alpha_bot_execution.py`.

CONTEXT
-------
Three call sites in `alpha_bot_execution.py` print the raw body of a
Composer.trade API error response to stdout:

  L89   fetch_symphony_stats        -- "Error fetching account ...: {response.text}"
  L115  execute_sell_to_cash (retry)-- "[COMPOSER ERROR HTTP {sc}]: {response.text}"
  L120  execute_sell_to_cash (reject)-- "[COMPOSER REJECTED]: {response.text}"

Composer error bodies are not contractually scoped. In practice they have
been observed to contain (a) account identifiers, (b) auth-debug strings
mentioning bearer-token fragments, and (c) Python stack traces echoed
from the Composer side. All three land in the operator's stdout and from
there into whatever log aggregator the daemon is wired to. That is a
secret-leakage / PII-leakage path on a live-trading box.

The required production fix: log the HTTP status code (already present
on L101 / L115 partly) and DROP the response body. Operators who need
the body for debugging can capture it manually in a one-off REPL session
against the same endpoint with curl; they do NOT need it in the live
daemon log stream.

DESIGN DECISION — STATIC-SOURCE-SCAN, NOT IMPORT-AND-PATCH
----------------------------------------------------------
We do NOT import `alpha_bot_execution` because module import triggers
config loading and (depending on environment) scheduler / DB side
effects. We scan the source text with a regex, identical to the pattern
established in tests/alpaca/test_feed_pinning.py (merged at ce043fd).

DESIGN DECISION — TWO FILES, NOT ONE COMBINED FILE
--------------------------------------------------
Concern #27 (this file) is a regex-on-source check. Concern #28
(test_exception_specificity.py) is an AST walk. Different mechanics,
different failure signals. Keeping them apart means a regression in one
does not cross-contaminate the diagnostic output of the other. The
task statement explicitly authorized either layout; this is the call.

EXPECTED STATE AT AUTHORING
---------------------------
RED. All four `response.text`-scoped assertions in this file are
expected to fail against the current `alpha_bot_execution.py`. The
positive `status_code is logged` assertion is expected to PASS (status
codes are already in the print strings on L101 and L115); it is
included as a guardrail so the fix doesn't over-correct and strip
status-code logging along with the body.
"""

from __future__ import annotations

import pathlib
import re

import pytest


# ---------------------------------------------------------------------------
# Source-file path. Resolved once at import time; tests read fresh each run.
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALPHA_BOT_PATH = REPO_ROOT / "alpha_bot_execution.py"


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------
# `RESPONSE_TEXT_TOKEN`: matches `response.text` anywhere on a line. We
# intentionally do NOT scope to f-strings only — a future contributor
# could write `print("err: " + response.text)`, `print(f"{r.text}")`
# (after renaming the variable), `logger.error(response.text)`, etc. We
# match the bare attribute access. Case-insensitive defends against
# `Response.Text` typing of any kind, though Python convention is lower.
RESPONSE_TEXT_TOKEN = re.compile(r"\bresponse\s*\.\s*text\b", re.IGNORECASE)

# `PRINT_CALL`: a line that contains a `print(` call. Tolerant of extra
# whitespace before the paren and of inline conditionals leading up to it.
PRINT_CALL = re.compile(r"\bprint\s*\(")

# `STATUS_CODE_TOKEN`: matches `response.status_code`. Used by the
# positive guardrail assertion.
STATUS_CODE_TOKEN = re.compile(r"\bresponse\s*\.\s*status_code\b", re.IGNORECASE)

# `COMPOSER_ERROR_PRINT_HINT`: detects the Composer-error print lines
# specifically. Looking for any of the bracket-tagged log markers used
# on the three known call sites. This lets us assert positive behavior
# (status code present) on the exact lines that should be retaining it
# AFTER the body is scrubbed, without false-positives on unrelated
# print() lines (cycle banners, mode lines, etc.).
COMPOSER_ERROR_PRINT_HINT = re.compile(
    r"\[(?:COMPOSER\s+ERROR|COMPOSER\s+REJECTED|API\s+Status)|Error\s+fetching\s+account",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_source(path: pathlib.Path) -> str:
    """Read a source file as UTF-8 text. Fails loudly if the file is missing.

    We do NOT import `alpha_bot_execution`; importing the module would
    trigger config / scheduler side effects inappropriate for a static
    analysis test. Same convention as tests/alpaca/test_feed_pinning.py.
    """
    assert path.exists(), f"Required source file missing: {path}"
    return path.read_text(encoding="utf-8")


def _numbered_lines(source: str) -> list[tuple[int, str]]:
    """Return (1-indexed line number, raw line text) for every line."""
    return [(idx + 1, line) for idx, line in enumerate(source.splitlines())]


# ---------------------------------------------------------------------------
# Test 1: no print() in alpha_bot_execution.py may contain response.text
# ---------------------------------------------------------------------------


def test_no_print_statement_echoes_composer_response_text():
    """A `print(...)` call in `alpha_bot_execution.py` must NEVER include
    `response.text`.

    Composer error bodies have been observed to contain account ids,
    auth-debug fragments, and remote stack traces. Echoing the body to
    stdout puts those values into operator logs. The production fix is
    to drop `response.text` from the f-string and rely on the status
    code (already present on the same / adjacent lines) for debugging.
    """
    source = _read_source(ALPHA_BOT_PATH)
    offenders: list[tuple[int, str]] = []
    for lineno, line in _numbered_lines(source):
        if PRINT_CALL.search(line) and RESPONSE_TEXT_TOKEN.search(line):
            offenders.append((lineno, line.strip()))

    formatted = "\n  ".join(f"L{lineno}: {text}" for lineno, text in offenders)
    assert not offenders, (
        "One or more `print(...)` lines in alpha_bot_execution.py echo "
        "`response.text`. Composer error bodies can leak account ids, "
        "auth-debug strings, and remote stack traces into operator logs. "
        "Drop the body from the f-string and keep only the status code.\n"
        f"  {formatted}"
    )


# ---------------------------------------------------------------------------
# Test 2: no occurrence of response.text ANYWHERE in alpha_bot_execution.py
# ---------------------------------------------------------------------------


def test_response_text_never_referenced_anywhere():
    """Defense-in-depth: `response.text` must not appear in
    `alpha_bot_execution.py` AT ALL — not in prints, not in logger
    calls, not in string concatenation, not even in a comment.

    A future contributor could replace `print(f"... {response.text}")`
    with `logger.error("...", extra={"body": response.text})` and slip
    past Test 1 (which only scopes to `print(` lines). This test catches
    that broader shape. If a legitimate non-leaking use of
    `response.text` is needed in the future (e.g., parsing a known-safe
    plain-text endpoint), this test should be widened with an explicit
    line-number allowlist documenting WHY the use is safe — NEVER
    weakened.
    """
    source = _read_source(ALPHA_BOT_PATH)
    offenders: list[tuple[int, str]] = []
    for lineno, line in _numbered_lines(source):
        if RESPONSE_TEXT_TOKEN.search(line):
            offenders.append((lineno, line.strip()))

    formatted = "\n  ".join(f"L{lineno}: {text}" for lineno, text in offenders)
    assert not offenders, (
        "`response.text` is referenced in alpha_bot_execution.py. "
        "Composer (and Alpaca) error bodies are not contractually "
        "scoped and have been observed to contain sensitive data. "
        "Remove all references; log status codes only.\n"
        f"  {formatted}"
    )


# ---------------------------------------------------------------------------
# Test 3: anti-drift — count of response.text occurrences must be zero
# ---------------------------------------------------------------------------


def test_response_text_occurrence_count_is_zero():
    """Numeric guardrail asserting the exact count. Gives a clearer
    failure message ("found 3 occurrences, expected 0") than the
    line-by-line offender list when a regression re-introduces the
    pattern.

    Kept separate from Test 2 so that a refactor introducing a
    new offender on a previously-clean line still produces a distinct
    diagnostic (count change vs new line content).
    """
    source = _read_source(ALPHA_BOT_PATH)
    occurrences = RESPONSE_TEXT_TOKEN.findall(source)
    assert len(occurrences) == 0, (
        f"Expected zero `response.text` references in "
        f"alpha_bot_execution.py; found {len(occurrences)}. Every "
        "occurrence is a potential secret-leak path through operator "
        "stdout. Replace with status-code-only logging."
    )


# ---------------------------------------------------------------------------
# Test 4: positive guardrail — status_code must remain logged on Composer
# error paths (don't over-correct by stripping status codes too)
# ---------------------------------------------------------------------------


def test_composer_error_print_lines_still_log_status_code():
    """Positive assertion paired with the negative `response.text`
    scrub. Once the fix lands, the three Composer error print lines
    (L89, L115, L120 at authoring time) must STILL include
    `response.status_code` — operators need that signal to diagnose
    rate-limit / auth / server-error states.

    This test walks the print lines tagged with the Composer-error
    markers (`[COMPOSER ERROR ...]`, `[COMPOSER REJECTED]`, etc.)
    and asserts each one references `response.status_code` somewhere
    on the line. NOTE: the current `L120` "[COMPOSER REJECTED]"
    line at authoring time does NOT include a status code in the
    f-string (the status code is printed separately on L101 inside
    the same loop body). The fix-author should add the status code
    here as part of removing `response.text` — this test pins that
    intent so the fix doesn't silently regress operator diagnostics.
    """
    source = _read_source(ALPHA_BOT_PATH)
    composer_error_prints: list[tuple[int, str]] = []
    for lineno, line in _numbered_lines(source):
        if PRINT_CALL.search(line) and COMPOSER_ERROR_PRINT_HINT.search(line):
            composer_error_prints.append((lineno, line.strip()))

    assert composer_error_prints, (
        "Could not locate any Composer-error print lines in "
        "alpha_bot_execution.py. Either the call sites were "
        "removed/renamed (in which case this test needs updating) or "
        "the COMPOSER_ERROR_PRINT_HINT regex no longer matches the "
        "marker format. Investigate before merging."
    )

    missing_status: list[tuple[int, str]] = [
        (lineno, text)
        for lineno, text in composer_error_prints
        if not STATUS_CODE_TOKEN.search(text)
    ]
    formatted = "\n  ".join(f"L{lineno}: {text}" for lineno, text in missing_status)
    assert not missing_status, (
        "One or more Composer-error print lines do not log "
        "`response.status_code`. After scrubbing `response.text` the "
        "status code MUST remain (or be added) so operators can still "
        "diagnose 4xx/5xx/429 conditions from the log stream.\n"
        f"  {formatted}"
    )


# ---------------------------------------------------------------------------
# Sanity guard — ensure the matcher regexes are not silently wrong
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "regex,sample,should_match",
    [
        (RESPONSE_TEXT_TOKEN, "print(f'err: {response.text}')", True),
        (RESPONSE_TEXT_TOKEN, "logger.error(response.text)", True),
        (RESPONSE_TEXT_TOKEN, "print(f'err: {response . text}')", True),
        (RESPONSE_TEXT_TOKEN, "print(f'err: {response.status_code}')", False),
        (RESPONSE_TEXT_TOKEN, "text = 'hello'", False),
        (PRINT_CALL, "print(x)", True),
        (PRINT_CALL, "    print (   x)", True),
        (PRINT_CALL, "sprint(x)", False),
        (STATUS_CODE_TOKEN, "print(f'{response.status_code}')", True),
        (STATUS_CODE_TOKEN, "print(f'{response.text}')", False),
        (
            COMPOSER_ERROR_PRINT_HINT,
            "print(f'     !!! [COMPOSER ERROR HTTP 500]: body')",
            True,
        ),
        (
            COMPOSER_ERROR_PRINT_HINT,
            "print(f'     !!! [COMPOSER REJECTED]: body')",
            True,
        ),
        (
            COMPOSER_ERROR_PRINT_HINT,
            "print(f'Error fetching account 123: body')",
            True,
        ),
        (COMPOSER_ERROR_PRINT_HINT, "print('hello world')", False),
    ],
)
def test_regex_sanity(regex: re.Pattern[str], sample: str, should_match: bool):
    """Self-tests for the matcher regexes. If these break, the
    production-scoped tests above are silently meaningless. Kept
    separate so a regex bug surfaces with a distinct diagnostic
    signal vs an actual production-fix regression.
    """
    matched = bool(regex.search(sample))
    assert matched is should_match, (
        f"Regex {regex.pattern!r} on sample {sample!r}: "
        f"expected match={should_match}, got match={matched}"
    )
