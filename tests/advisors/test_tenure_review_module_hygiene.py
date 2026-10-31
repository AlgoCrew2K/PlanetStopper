"""What the review module loads at import and how its source documents itself.

Module under test: advisors.tenure_review.

The import test starts a fresh interpreter, because the pytest process has already
imported most of the repository. The source tests read the module file with ``ast``
and ``tokenize``; they pin conventions the project applies to every math module
(a named constant with a comment that says what it is, no repeated literal, no
process vocabulary in a comment), not the module's structure.
"""

from __future__ import annotations

import ast
import io
import json
import math
import os
import re
import subprocess
import sys
import textwrap
import tokenize
from collections import Counter
from pathlib import Path

import pytest

import advisors.tenure_review as tr
from advisors import retirement_recommender

_MODULE_PATH = Path(tr.__file__).resolve()
_REPO_ROOT = _MODULE_PATH.parents[1]

# Modules whose presence after importing the compute module means it reached for the
# network, the engine or a Composer client. Any module name containing "composer" counts.
_NEVER_LOADED_AT_IMPORT = ("alpha_bot_execution", "requests", "symphony_logic")

_THRESHOLD_ENV_KEY_TEXT = "TENURE_REVIEW_MIN_TRADING_DAYS"

# A named constant is a module level SCREAMING_SNAKE name, with or without a leading
# underscore.
_CONSTANT_NAME = re.compile(r"^_?[A-Z][A-Z0-9_]*$")

# The smallest comment that can say what a constant is and where it comes from.
_MIN_COMMENT_WORDS = 3

# Numeric literals that carry no meaning of their own (identity and sentinel values).
_TRIVIAL_NUMBERS = frozenset({0.0, 1.0})

# Process vocabulary that belongs in a commit message and nowhere in code: a cycle, a
# round, a hand-off, or a plan, acceptance-criterion, scenario or review-row identifier.
_PROCESS_WORDS = re.compile(
    r"\bcycle\b|\bhand-?off\b|\bround\s+(?:\d+|one|two|three)\b|\bC[1-4]\b", re.IGNORECASE
)
_PROCESS_IDENTIFIER = re.compile(r"\b(?:AC|S|E|CR|QA|Q|DC|NB|DE)-\d+(?:\.\d+)?\b")


def _source() -> str:
    return _MODULE_PATH.read_text(encoding="utf-8")


def _tree() -> ast.Module:
    return ast.parse(_source())


def _docstring_nodes(tree: ast.Module) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def _fstring_nodes(tree: ast.Module) -> set[int]:
    return {
        id(child)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for child in ast.walk(node)
    }


def _comment_by_line(source: str) -> dict[int, str]:
    return {
        token.start[0]: token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    }


def _module_constant_statements(tree: ast.Module):
    """(name, statement) for every module level assignment to a named constant."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        for name in names:
            if _CONSTANT_NAME.match(name):
                yield name, node


def _comment_for(statement: ast.stmt, lines: list[str], comments: dict[int, str]) -> str:
    """The comment block directly above a statement plus any comment on its own lines."""
    parts: list[str] = []
    row = statement.lineno - 1
    above: list[str] = []
    while row >= 1 and lines[row - 1].strip().startswith("#"):
        above.append(lines[row - 1].strip().lstrip("#").strip())
        row -= 1
    parts.extend(reversed(above))
    for number in range(statement.lineno, statement.end_lineno + 1):
        if number in comments:
            parts.append(comments[number].lstrip("#").strip())
    return " ".join(parts)


def _is_pure_numeric_definition(statement: ast.stmt) -> bool:
    value = getattr(statement, "value", None)
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, (ast.USub, ast.UAdd)):
        value = value.operand
    return (
        isinstance(value, ast.Constant)
        and isinstance(value.value, (int, float))
        and not isinstance(value.value, bool)
    )


# ---------------------------------------------------------------------------
# Import closure
# ---------------------------------------------------------------------------


def test_importing_the_module_loads_no_engine_http_library_or_composer_client(tmp_path):
    """The compute layer has no I/O. A Composer client module imports the engine module
    (which loads the environment file) and the HTTP library, so reaching it for a type
    annotation is enough to break the layering. The check runs in a fresh interpreter
    and fails on any loaded module that is on the banned list or has composer in its name."""
    code = textwrap.dedent(
        f"""
        import json
        import sys

        sys.path.insert(0, {str(_REPO_ROOT)!r})
        import advisors.tenure_review  # noqa: F401

        banned = {list(_NEVER_LOADED_AT_IMPORT)!r}
        loaded = sorted(m for m in sys.modules if m in banned or "composer" in m.lower())
        print(json.dumps(loaded))
        """
    )
    env = dict(os.environ)
    env["DB_PATH"] = str(tmp_path / "fresh_interpreter_state.db")
    done = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == []


_IMPORT_SECONDS = 60  # a bare import takes about a second; a hang is what this bounds

_STRESS_PROBE = """
import sys

sys.path.insert(0, {root!r})
from advisors import retirement_recommender

retirement_recommender.STRESS_WINDOW_FRACTION = {fraction!r}
import advisors.tenure_review as tr

print("imported", hasattr(tr, "build_review"))
"""

# A pair whose first series is a wave and whose second follows it at about 0.99, with enough
# days for the uncertainty gate. For each requested day count the probe prints the structural
# gate of the review and whether the recommender's own stressed-window function gave no answer
# for the same two series (its subset is too thin, as these series have variance everywhere).
_NEEDS_PROBE = """
import json
import math
import sys
from datetime import date, timedelta

sys.path.insert(0, {root!r})
from advisors import retirement_recommender

retirement_recommender.STRESS_WINDOW_FRACTION = {fraction!r}
import advisors.tenure_review as tr


def gate(days):
    keys = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(days)]
    first = [math.sin(0.37 * i) + 0.1 * math.sin(2.9 * i) for i in range(days)]
    second = [0.95 * v + 0.05 * math.cos(1.3 * i) for i, v in enumerate(first)]
    pair = tr.evaluate_redundancy({{"a": dict(zip(keys, first)), "b": dict(zip(keys, second))}})[0]
    thin = retirement_recommender._compute_stressed_correlation(first, second) is None
    return {{
        "state": pair["structural_gate"]["state"],
        "text": pair["structural_gate"]["text"],
        "recommender_gives_no_answer": thin,
    }}


print(json.dumps({{str(days): gate(days) for days in {sweep!r}}}))
"""


# The setup both review probes share: the stress fraction is set before the module is
# imported, and ``review_of`` builds two Composer symphonies that end on the as-of day from
# two lists of percent returns.
_REVIEW_PROBE_PRELUDE = """
import json
import math
import sys
from datetime import UTC, date, datetime, timedelta

sys.path.insert(0, {root!r})
from advisors import retirement_recommender

retirement_recommender.STRESS_WINDOW_FRACTION = {fraction!r}
import advisors.tenure_review as tr
import market_calendar


def sessions_ending(count):
    found, cursor = [], date(2026, 12, 31)
    while len(found) < count:
        if market_calendar.is_trading_day(cursor):
            found.append(cursor)
        cursor -= timedelta(days=1)
    return found[::-1]


def values(sessions, returns):
    level, out = 100.0, {{sessions[0].isoformat(): 100.0}}
    for day, change in zip(sessions[1:], returns):
        level *= 1.0 + change / 100.0
        out[day.isoformat()] = level
    return out


def review_of(first, second):
    sessions = sessions_ending(len(first) + 1)
    symphonies = [
        tr.SymphonyInput(
            symphony_id=sid,
            display_name=name,
            invested_since=sessions[0].isoformat(),
            composer_values=values(sessions, returns),
        )
        for sid, name, returns in (("a", "Alpha", first), ("b", "Bravo", second))
    ]
    return tr.build_review(
        tr.ReviewInputs(
            run_id="probe",
            trigger="nightly",
            started_at=datetime(2026, 12, 31, 22, 0, tzinfo=UTC),
            as_of=date(2026, 12, 31),
            threshold=tr.ThresholdSetting(20, None),
            symphonies=symphonies,
        )
    )
"""

# Two series that follow each other at about 0.99 over 60 sessions. The probe prints the
# structural gate of their pair, the pair's uncertainty gate and what the review says of the
# first symphony's redundancy item.
_TINY_FRACTION_PROBE = (
    _REVIEW_PROBE_PRELUDE
    + """
days = 60
first = [0.5 * (math.sin(0.37 * i) + 0.1 * math.sin(2.9 * i)) for i in range(days)]
second = [0.95 * v + 0.025 * math.cos(1.3 * i) for i, v in enumerate(first)]
keys = [d.isoformat() for d in sessions_ending(days + 1)[1:]]
pair = tr.evaluate_redundancy(
    {{"a": dict(zip(keys, first)), "b": dict(zip(keys, second))}}
)[0]
entry = next(e for e in review_of(first, second).entries if e["symphony_id"] == "a")
item = entry["evidence"]["redundancy"]
print(
    json.dumps(
        {{
            "aligned_days": pair["n_obs"],
            "uncertainty": pair["uncertainty_gate"]["state"],
            "structural": pair["structural_gate"],
            "item_status": item["status"],
            "item_text": json.dumps(item),
            "missing_items": entry["missing_items"],
        }}
    )
)
"""
)

# Two uncorrelated series (sines of different whole frequencies) over each requested number of
# returns; the probe prints what the review says of the first symphony's redundancy item.
_FLOOR_PROBE = (
    _REVIEW_PROBE_PRELUDE
    + """
out = {{}}
for returns in {sweep!r}:
    first = [0.3 * math.sin(2 * math.pi * 2 * i / returns) for i in range(returns)]
    second = [0.3 * math.sin(2 * math.pi * 3 * i / returns) for i in range(returns)]
    entry = next(e for e in review_of(first, second).entries if e["symphony_id"] == "a")
    item = entry["evidence"]["redundancy"]
    out[str(returns)] = {{
        "status": item["status"],
        "summary": item.get("summary"),
        "pair": item["pair"],
        "missing": "redundancy" in entry["missing_items"],
    }}
print(json.dumps(out))
"""
)


def _run_probe(code: str, tmp_path):
    env = dict(os.environ)
    env["DB_PATH"] = str(tmp_path / "fresh_interpreter_state.db")
    try:
        return subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            timeout=_IMPORT_SECONDS,
            env=env,
            cwd=tmp_path,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"the interpreter did not finish within {_IMPORT_SECONDS} seconds")


@pytest.mark.parametrize(
    "fraction",
    [0.0, 0.004, 1e-100, 1e-300],
    ids=[
        "zero_fraction",
        "fraction_that_rounds_to_zero_percent",
        "fraction_far_below_float_steps_of_an_integer",
        "fraction_near_the_smallest_normal_float",
    ],
)
def test_importing_the_module_returns_whatever_the_recommenders_stress_fraction_is(
    fraction, tmp_path
):
    """The days a stressed window needs follow from the recommender's stress fraction. A
    fraction that gives no stressed day at all has no such count, and finding that out must
    not be a search with no end. Rejects an unbounded search at import, which never
    returns, and a division that raises."""
    done = _run_probe(_STRESS_PROBE.format(root=str(_REPO_ROOT), fraction=fraction), tmp_path)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().splitlines()[-1] == "imported True"


@pytest.mark.parametrize(
    "fraction",
    [0.02, 0.025, 0.0333, 0.037, 0.05, 0.0667, 0.004],
    ids=lambda fraction: f"stress_fraction_{fraction}",
)
def test_the_days_the_structural_gate_needs_follow_the_recommenders_stress_fraction(
    fraction, tmp_path
):
    """The recommender's stressed window holds ceil(fraction * n) of n aligned days and is
    read from 10 of them. The least n that gives 10 is found here by a bounded search over
    integers with that same product. For a sweep of day counts around it, in a fresh
    interpreter that sets the fraction before the module is imported, the review's
    structural gate reads insufficient data exactly where the recommender's own function
    gives no answer, and the text names that count. Rejects a count typed for the default
    fraction, and a fraction rounded to a whole percent before it is applied (2.5 percent
    read as 2 needs 451 days where the recommender's window reaches 10 days at 361)."""
    minimum = retirement_recommender.STRESS_MIN_OBS
    limit = math.ceil(minimum / fraction) + 2
    needed = next(n for n in range(1, limit) if math.ceil(fraction * n) >= minimum)
    uncertainty_floor = retirement_recommender.MIN_OBS_FLOOR
    sweep = sorted(
        {max(uncertainty_floor + 5, needed - 25), needed - 1, needed, needed + 1, needed + 30}
    )
    code = _NEEDS_PROBE.format(root=str(_REPO_ROOT), fraction=fraction, sweep=sweep)
    done = _run_probe(code, tmp_path)
    assert done.returncode == 0, done.stderr[-2000:]
    gates = {
        int(days): gate for days, gate in json.loads(done.stdout.strip().splitlines()[-1]).items()
    }
    assert sorted(gates) == sweep
    for days, gate in gates.items():
        too_few = days < needed
        # fixture sanity: the recommender's function agrees with the search above
        assert gate["recommender_gives_no_answer"] is too_few, days
        assert (gate["state"] == "insufficient data") is too_few, days
        if too_few:
            assert gate["text"] == f"insufficient data: needs {needed} aligned days", days


@pytest.mark.parametrize(
    "fraction",
    [1e-100, 1e-300],
    ids=["fraction_far_below_float_steps_of_an_integer", "fraction_near_the_smallest_normal_float"],
)
def test_a_stress_fraction_too_small_for_float_arithmetic_reads_insufficient_data_promptly(
    fraction, tmp_path
):
    """At these fractions the integer that would give 10 stressed days is so large that adding
    one to it no longer changes the float product, so a search that steps by one never ends.
    The review must still answer: a pair that passes the uncertainty gate reads insufficient
    data for the structural gate with a count of days larger than the pair's own, and the item
    that shows the pair is not used in the verdict. The fresh interpreter has the 60 second
    bound of the import probe above. Rejects a search with no end, a count smaller than the
    days the pair already has (which would contradict the gate's own state), and a pair that
    counts as evidence for a gate that could not run."""
    done = _run_probe(
        _TINY_FRACTION_PROBE.format(root=str(_REPO_ROOT), fraction=fraction), tmp_path
    )
    assert done.returncode == 0, done.stderr[-2000:]
    got = json.loads(done.stdout.strip().splitlines()[-1])
    assert got["uncertainty"] == "passed"  # fixture sanity: the structural gate is reached
    gate = got["structural"]
    assert gate["state"] == "insufficient data"
    match = re.fullmatch(r"insufficient data: needs (\d+) aligned days", gate["text"])
    assert match, gate["text"]
    assert int(match.group(1)) > got["aligned_days"]
    assert got["item_status"] == "unavailable"
    assert "redundancy" in got["missing_items"]
    assert "no redundant sibling" not in got["item_text"]


def test_a_comparison_needs_the_larger_of_the_days_the_structural_gate_needs_and_thirty(tmp_path):
    """With a stress fraction of 0.5 the stressed window holds 10 days from 19 aligned days, far
    below the 30 the uncertainty gate needs. An uncorrelated pair compared on 25 days is not a
    finding (the correlation interval needs 30), and on exactly 30 it is. Rejects a floor that
    is the structural gate's days alone."""
    fraction = 0.5
    structural_days = next(
        n for n in range(1, 100) if math.ceil(fraction * n) >= retirement_recommender.STRESS_MIN_OBS
    )
    uncertainty_days = retirement_recommender.MIN_OBS_FLOOR
    assert structural_days < uncertainty_days  # fixture sanity: the two floors differ
    sweep = [uncertainty_days - 5, uncertainty_days]
    done = _run_probe(
        _FLOOR_PROBE.format(root=str(_REPO_ROOT), fraction=fraction, sweep=sweep), tmp_path
    )
    assert done.returncode == 0, done.stderr[-2000:]
    got = json.loads(done.stdout.strip().splitlines()[-1])
    short, enough = (got[str(n)] for n in sweep)
    assert short["pair"] is None and enough["pair"] is None  # fixture sanity: no screen hit
    assert short["status"] == "unavailable"
    assert short["summary"] is None
    assert short["missing"] is True
    assert enough["status"] == "available"
    assert enough["summary"] == "no redundant sibling"
    assert enough["missing"] is False


# ---------------------------------------------------------------------------
# The days the structural gate needs, for every float the stress fraction can be
# ---------------------------------------------------------------------------

# Prints the days the structural gate needs and, for each requested number of returns, what the
# review says of the first symphony's redundancy item for two uncorrelated symphonies (sines of
# different whole frequencies). An error is reported by its class name, never raised, so a test
# can tell the answer from a failure to give one. The fraction arrives as text because nan and
# the infinities have no literal.
_NEEDS_REVIEW_PROBE = (
    _REVIEW_PROBE_PRELUDE.replace("= {fraction!r}", "= float({fraction_text!r})")
    + """
out = {{"needs": None, "reviews": {{}}}}
try:
    out["needs"] = tr._structural_needs_days()
except Exception as exc:
    out["needs"] = {{"raised": type(exc).__name__}}
for returns in {sweep!r}:
    first = [0.3 * math.sin(2 * math.pi * 2 * i / returns) for i in range(returns)]
    second = [0.3 * math.sin(2 * math.pi * 3 * i / returns) for i in range(returns)]
    try:
        entry = next(e for e in review_of(first, second).entries if e["symphony_id"] == "a")
        item = entry["evidence"]["redundancy"]
        out["reviews"][str(returns)] = {{
            "status": item["status"],
            "summary": item.get("summary"),
            "missing": "redundancy" in entry["missing_items"],
        }}
    except Exception as exc:
        out["reviews"][str(returns)] = {{"raised": type(exc).__name__}}
print(json.dumps(out))
"""
)

# Fractions that give no stressed day at all: not a number, minus infinity, negative, zero.
NO_STRESSED_DAY_FRACTIONS = ["nan", "-inf", "-1.0", "0.0"]
# Fractions that reach the minimum from one session or a few, up to the largest float and
# infinity. The product of 1.95e289 with the largest integer the search could try is the first
# one past the largest float; 1.94e289 is the last one below it.
STRESS_FRACTIONS_AT_OR_ABOVE_ONE = [
    "1.0",
    "9.99",
    "10.0",
    "1.94e+289",
    "1.95e+289",
    "1e+300",
    repr(sys.float_info.max),
    "inf",
]
# Returns the review of the probe is asked about: one fewer than the uncertainty gate's floor,
# the floor, and a long pair.
_PROBE_RETURNS = [
    retirement_recommender.MIN_OBS_FLOOR - 1,
    retirement_recommender.MIN_OBS_FLOOR,
    200,
]


def _days_the_structural_gate_needs(fraction: float) -> int:
    """The least number of aligned days n with ceil(fraction * n) at the recommender's minimum:
    no number of days when the fraction is not positive. The comparison is the one a ceiling
    makes, a product above the minimum less one, which holds for an infinite product as well."""
    minimum = retirement_recommender.STRESS_MIN_OBS
    if not fraction > 0:
        return sys.maxsize
    return next(n for n in range(1, minimum + 1) if fraction * n > minimum - 1)


@pytest.fixture(
    scope="module",
    params=[*NO_STRESSED_DAY_FRACTIONS, *STRESS_FRACTIONS_AT_OR_ABOVE_ONE],
    ids=lambda text: f"stress_fraction_{text}",
)
def stress_fraction_probe(request, tmp_path_factory):
    """(the fraction as text, what a fresh interpreter that sets it before importing the module
    answers): one interpreter per fraction, shared by the tests that read it."""
    code = _NEEDS_REVIEW_PROBE.format(
        root=str(_REPO_ROOT), fraction_text=request.param, sweep=_PROBE_RETURNS
    )
    done = _run_probe(code, tmp_path_factory.mktemp("stress_fraction"))
    assert done.returncode == 0, done.stderr[-2000:]
    return request.param, json.loads(done.stdout.strip().splitlines()[-1])


def test_the_days_the_structural_gate_needs_are_answered_for_every_float_stress_fraction(
    stress_fraction_probe,
):
    """A fraction that is not positive (or not a number) gives no stressed day, so no number of
    aligned days is enough. A fraction of one session's worth of minimum or more reaches the
    minimum from the fewest days, found without error however large the fraction is, up to
    infinity. Rejects an error where the product of the fraction and a day count is beyond the
    largest float, and a count that treats such a fraction as never reaching the minimum."""
    text, probe = stress_fraction_probe
    assert probe["needs"] == _days_the_structural_gate_needs(float(text)), text


def test_a_review_of_two_uncorrelated_symphonies_completes_at_every_float_stress_fraction(
    stress_fraction_probe,
):
    """Whatever the stress fraction, the review returns. An uncorrelated pair is a finding that
    the two are not redundant from the larger of the days the structural gate needs and the
    uncertainty gate's floor, and until then the item is unavailable and listed missing.
    Rejects a review that stops, and a floor taken from a fraction the structural gate cannot
    use."""
    text, probe = stress_fraction_probe
    floor = max(_days_the_structural_gate_needs(float(text)), retirement_recommender.MIN_OBS_FLOOR)
    for returns in _PROBE_RETURNS:
        got = probe["reviews"][str(returns)]
        assert "raised" not in got, (text, returns, got)
        decided = returns >= floor
        assert got["status"] == ("available" if decided else "unavailable"), (text, returns)
        assert got["summary"] == ("no redundant sibling" if decided else None), (text, returns)
        assert got["missing"] is (not decided), (text, returns)


# ---------------------------------------------------------------------------
# Grammar: the module imports on the oldest interpreter the project supports
# ---------------------------------------------------------------------------

# The oldest Python grammar the module must parse under.
_OLDEST_GRAMMAR = (3, 11)


def test_the_module_parses_under_the_grammar_of_python_3_11():
    """Type parameter lists on a function or class, and the type statement, are a syntax error
    before Python 3.12, so a module that uses them stops importing there. Rejects any of them."""
    try:
        ast.parse(_source(), feature_version=_OLDEST_GRAMMAR)
    except SyntaxError as exc:
        pytest.fail(f"the module does not parse under Python 3.11: {exc}")


def _lines_reusing_the_enclosing_quote(source: str) -> list[int]:
    """Lines where a string inside an f-string's replacement field is delimited by the quote
    that delimits an enclosing f-string, which only Python 3.12 accepts. The tokenizer of an
    older interpreter cannot read such a source at all, so nothing is reported there."""
    if not hasattr(tokenize, "FSTRING_START"):
        return []

    def delimiter(text: str) -> str:
        body = text.lstrip("rRbBfFuU")
        return body[:3] if body[:3] in ('"""', "'''") else body[:1]

    open_quotes: list[str] = []
    found: list[int] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.FSTRING_END:
            open_quotes.pop()
            continue
        if token.type not in (tokenize.FSTRING_START, tokenize.STRING):
            continue
        quote = delimiter(token.string)
        for outer in open_quotes:
            reused = quote[:1] == outer[:1] and not (len(outer) == 3 and len(quote) == 1)
            if reused:
                found.append(token.start[0])
        if token.type == tokenize.FSTRING_START:
            open_quotes.append(quote)
    return found


def test_no_f_string_of_the_module_reuses_its_own_quote_inside_a_replacement_field():
    """Quote reuse inside an f-string is accepted only from Python 3.12, and the parse test
    above does not see it. The scan is shown to flag a source that reuses the quote and to pass
    one that does not, then run over the module. Rejects an f-string that older interpreters
    cannot read."""
    if hasattr(tokenize, "FSTRING_START"):
        reuse = 'x = f"{"a"}"'
        no_reuse = "x = f\"{'a'}\""
        assert _lines_reusing_the_enclosing_quote(reuse) == [1]  # the scan can fail
        assert _lines_reusing_the_enclosing_quote(no_reuse) == []
    assert _lines_reusing_the_enclosing_quote(_source()) == []


# ---------------------------------------------------------------------------
# Constants: one definition, a comment that says what it is, a derived note
# ---------------------------------------------------------------------------


def test_exactly_one_constant_holds_the_threshold_environment_key():
    holders = sorted(
        name
        for name, value in vars(tr).items()
        if isinstance(value, str) and value == _THRESHOLD_ENV_KEY_TEXT
    )
    assert holders == ["TENURE_THRESHOLD_ENV_KEY"]


def test_every_named_module_constant_carries_a_source_comment():
    """Labels, reason texts, the invalid-threshold note and every derived count are
    named constants; each needs a comment that says what it is and where it comes from.
    The comment sits on the line block directly above the constant or on its own line."""
    source = _source()
    lines = source.splitlines()
    comments = _comment_by_line(source)
    missing = []
    for name, statement in _module_constant_statements(_tree()):
        text = _comment_for(statement, lines, comments)
        if len(text.split()) < _MIN_COMMENT_WORDS:
            missing.append(f"{name} (line {statement.lineno})")
    assert not missing, "named constants without a source comment: " + ", ".join(missing)


def test_the_constant_that_converts_a_fraction_to_percent_says_so_in_its_comment():
    """A comment that describes a different constant misleads the next reader. The scale
    factor of 100 is the one constant every percent figure depends on."""
    source = _source()
    lines = source.splitlines()
    comments = _comment_by_line(source)
    scale_constants = [
        (name, statement)
        for name, statement in _module_constant_statements(_tree())
        if _is_pure_numeric_definition(statement) and float(statement.value.value) == 100.0
    ]
    assert scale_constants, "no named constant holds the scale factor of 100"
    for name, statement in scale_constants:
        text = _comment_for(statement, lines, comments).lower()
        assert "percent" in text, (name, text)
        assert "annualiz" not in text and "sessions" not in text, (name, text)


def test_the_invalid_threshold_note_is_derived_from_the_default_never_typed():
    """A typed default in a message goes stale the day the default changes. The only
    place the default's digits may appear is the assignment that defines it."""
    tree = _tree()
    docstrings = _docstring_nodes(tree)
    allowed = {
        id(statement.value)
        for name, statement in _module_constant_statements(tree)
        if name == "DEFAULT_MIN_TENURE_TRADING_DAYS"
    }
    digits = str(tr.DEFAULT_MIN_TENURE_TRADING_DAYS)
    typed = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in docstrings or id(node) in allowed:
            continue
        if isinstance(node.value, bool):
            continue
        if isinstance(node.value, int) and node.value == tr.DEFAULT_MIN_TENURE_TRADING_DAYS:
            typed.append(node.lineno)
        if isinstance(node.value, str) and digits in node.value:
            typed.append(node.lineno)
    assert not typed, f"the default threshold is typed again at lines {typed}"
    note = tr.parse_threshold("not a number").note
    assert note == f"configured threshold invalid; {digits} in use"


# ---------------------------------------------------------------------------
# Literals: a phrase or a number that recurs is a named constant
# ---------------------------------------------------------------------------


def test_no_reason_or_label_phrase_is_repeated_in_executable_code():
    """A phrase with a space or a colon is text a person reads (a reason, a label, a
    unit). Written twice it can drift apart, so it is defined once and referenced."""
    tree = _tree()
    skip = _docstring_nodes(tree) | _fstring_nodes(tree)
    counts = Counter(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and re.search(r"[ :]", node.value)
    )
    repeated = {text: n for text, n in counts.items() if n > 1}
    assert not repeated, f"phrases written more than once: {repeated}"


def test_no_numeric_literal_is_repeated_in_executable_code():
    """A number other than 0 and 1 that appears twice carries a meaning that deserves a
    name. A constant defined as a bare number is the definition, not a repeat."""
    tree = _tree()
    definitions = {
        id(statement.value)
        for _name, statement in _module_constant_statements(tree)
        if _is_pure_numeric_definition(statement)
    }
    counts: Counter[float] = Counter()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
            and id(node) not in definitions
            and float(node.value) not in _TRIVIAL_NUMBERS
        ):
            counts[float(node.value)] += 1
    repeated = {value: n for value, n in counts.items() if n > 1}
    assert not repeated, f"numeric literals written more than once: {repeated}"


def test_no_cache_decorator_takes_a_bare_integer_size():
    """A cache size typed into a decorator has no derivation. A named constant, or no
    bound at all, can carry one."""
    tree = _tree()
    bare = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = (
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        )
        if callee != "lru_cache":
            continue
        sizes = [kw.value for kw in node.keywords if kw.arg == "maxsize"] + list(node.args[:1])
        bare.extend(
            node.lineno
            for size in sizes
            if isinstance(size, ast.Constant) and isinstance(size.value, int)
        )
    assert not bare, f"lru_cache with a literal size at lines {bare}"


# ---------------------------------------------------------------------------
# Nothing is carried that nothing reads
# ---------------------------------------------------------------------------


def _is_dataclass(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "dataclass":
            return True
    return False


def test_every_field_of_a_private_dataclass_is_read_somewhere_in_the_module():
    """A field set at construction and never read is dead weight that later work must
    read around. Public dataclasses are the module's interface and are left alone."""
    tree = _tree()
    read = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
    }
    unread = [
        f"{cls.name}.{item.target.id}"
        for cls in ast.walk(tree)
        if isinstance(cls, ast.ClassDef) and cls.name.startswith("_") and _is_dataclass(cls)
        for item in cls.body
        if isinstance(item, ast.AnnAssign)
        and isinstance(item.target, ast.Name)
        and item.target.id not in read
    ]
    assert not unread, f"fields never read: {unread}"


# ---------------------------------------------------------------------------
# Comments describe the code, not the work that produced it
# ---------------------------------------------------------------------------


def test_no_comment_or_docstring_cites_a_cycle_round_or_hand_off():
    source = _source()
    tree = _tree()
    texts: list[tuple[int, str]] = [(row, text) for row, text in _comment_by_line(source).items()]
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                texts.append((getattr(node, "lineno", 1), doc))
    offenders = [
        f"line {row}: {match.group(0)!r}"
        for row, text in texts
        for match in [_PROCESS_WORDS.search(text) or _PROCESS_IDENTIFIER.search(text)]
        if match
    ]
    assert not offenders, "process vocabulary in code text: " + "; ".join(offenders)
