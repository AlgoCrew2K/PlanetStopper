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
    "fraction", [0.0, 0.004], ids=["zero_fraction", "fraction_that_rounds_to_zero_percent"]
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
