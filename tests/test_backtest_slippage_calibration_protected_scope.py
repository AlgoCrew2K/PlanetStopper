"""Scope pin for the backtest slippage calibration: two protected files, only the listed edits.

The slice edits two files that other pins hold byte for byte:

* ``advisors/composer_backtest_client.py``: the slippage default (``_DEFAULT_SLIPPAGE_PERCENT``)
  with the comment block above it, the comment block above ``_DEFAULT_CAPITAL``, and the one
  ``slippage_percent:`` unit line of the ``run_backtest`` docstring.
* ``autotuner.py``: the comment block above ``SIM_EXIT_FRICTION_PCT``; the assignment itself is
  unchanged.

A full-file pin cannot tell "only the listed edits" from "something else too", so each file is
pinned with its listed edit nodes CUT, by AST, with the cut helpers of
``tests/test_hygiene_protected_surfaces.py`` (imported, not copied): the text without the listed
nodes must equal the reference commit's text with the same cut. Because a cut takes a whole
statement, the statements that keep their value or their shape are pinned exactly as well, and the
``run_backtest`` docstring is pinned with its one listed line removed.

The digests are the reference commit's (``c3e12882``, the plan's base): each was computed from
``git show c3e12882:<path>``, LF-normalized, cut with the selectors below, and equals the digest of
the worktree file before any production edit. A change that stays inside the listed edits leaves
every digest unchanged; a byte changed anywhere else moves one.

STOP and ask the PM before re-taking any digest here: a change means a file moved outside what the
slice may touch. The four content pins on these two files are re-taken once these pass.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import re

import pytest

from tests import test_hygiene_protected_surfaces as hygiene_pins
from tests import test_preconditions_replay_protected_scope as precondition_pins
from tests import test_regime_label_protected_files as regime_pins
from tests.ai_advisor import test_advisor_backtest_client as client_pins
from tests.test_hygiene_protected_surfaces import (
    Selector,
    _ranges,
    _read,
    _union,
    cut_text,
    scope_digest,
)
from tests.test_preconditions_replay_protected_scope import _module_assignments_with_comments

_CLIENT = "advisors/composer_backtest_client.py"
_AUTOTUNER = "autotuner.py"


def _function_docstring(name: str) -> Selector:
    """The docstring statement of the named module-level function."""

    def select(tree: ast.Module):
        found = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name and node.body:
                first = node.body[0]
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    found.append((first, False))
        return found

    return select


_CLIENT_EDITS = _union(
    _module_assignments_with_comments("_DEFAULT_CAPITAL", "_DEFAULT_SLIPPAGE_PERCENT"),
    _function_docstring("run_backtest"),
)
_AUTOTUNER_EDITS = _module_assignments_with_comments("SIM_EXIT_FRICTION_PCT")

_SCOPE = {_CLIENT: _CLIENT_EDITS, _AUTOTUNER: _AUTOTUNER_EDITS}

# LF-normalized sha256 of each file with its listed edits cut (blank-line runs collapsed),
# computed from `git show c3e12882:<path>`; equal to the worktree file before any production edit.
_PINNED_SCOPE_DIGESTS = {
    _CLIENT: "a17f764929eb28617ee53099fd8f2e19c76943302fefbeab050bd1719a4519fc",
    _AUTOTUNER: "d5db23d1e4a5a427b2e6820dad5f3ec0091f76deeca5894772801bad793104c4",
}

# LF-normalized sha256 of the run_backtest docstring with the line after `slippage_percent:`
# removed, from `git show c3e12882:advisors/composer_backtest_client.py`.
_PINNED_DOCSTRING_WITHOUT_UNIT_LINE = (
    "d6c8a8c2533780d8ababa83db558259cf4428837b7eebdbe3e92d20f8acbeab7"
)


def _docstring_without_unit_line(text: str) -> str:
    tree = ast.parse(text.replace("\r\n", "\n"))
    function = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_backtest"
    )
    lines = (ast.get_docstring(function, clean=False) or "").split("\n")
    index = next(i for i, line in enumerate(lines) if line.strip() == "slippage_percent:")
    del lines[index + 1]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _statement(rel: str, name: str) -> ast.AnnAssign:
    tree = ast.parse(_read(rel))
    found = [
        n
        for n in tree.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == name
    ]
    assert len(found) == 1, f"expected one module-level {name} in {rel}, found {len(found)}"
    return found[0]


class TestTheFilesHoldOnlyTheListedEdits:
    @pytest.mark.parametrize("rel", sorted(_SCOPE))
    def test_file_outside_its_listed_edits_is_the_reference_text(self, rel):
        actual = scope_digest(_read(rel), _SCOPE[rel])
        assert actual == _PINNED_SCOPE_DIGESTS[rel], (
            f"{rel} changed outside the slice's listed edits (pinned "
            f"{_PINNED_SCOPE_DIGESTS[rel]}, now {actual}). STOP and ask the PM."
        )

    @pytest.mark.parametrize(
        ("rel", "expected_nodes"), [(_CLIENT, 3), (_AUTOTUNER, 1)], ids=["client", "autotuner"]
    )
    def test_each_selector_cuts_exactly_the_listed_nodes(self, rel, expected_nodes):
        """A selector that matched nothing would cut nothing and widen the pin silently."""
        cut = list(_SCOPE[rel](ast.parse(_read(rel))))
        assert len(cut) == expected_nodes

    def test_the_client_statements_that_keep_their_value_or_shape_are_exact(self):
        assert ast.unparse(_statement(_CLIENT, "_DEFAULT_CAPITAL")) == (
            "_DEFAULT_CAPITAL: float = 10000.0"
        )
        slippage = _statement(_CLIENT, "_DEFAULT_SLIPPAGE_PERCENT")
        assert isinstance(slippage.annotation, ast.Name) and slippage.annotation.id == "float"
        assert isinstance(slippage.value, ast.Constant)
        assert isinstance(slippage.value.value, float)

    def test_the_exit_friction_assignment_is_the_reference_statement(self):
        assert ast.unparse(_statement(_AUTOTUNER, "SIM_EXIT_FRICTION_PCT")) == (
            "SIM_EXIT_FRICTION_PCT: float = 0.5"
        )

    def test_the_run_backtest_docstring_differs_from_the_reference_by_one_line_only(self):
        assert _docstring_without_unit_line(_read(_CLIENT)) == _PINNED_DOCSTRING_WITHOUT_UNIT_LINE

    def test_the_value_of_the_two_costs_and_the_signature_default(self):
        import autotuner
        from advisors import composer_backtest_client as client

        assert autotuner.SIM_EXIT_FRICTION_PCT == 0.5
        assert client._DEFAULT_SLIPPAGE_PERCENT == 0.001
        default = inspect.signature(client.run_backtest).parameters["slippage_percent"].default
        assert default == 0.001, "the parameter default is bound where the function is defined"


class TestThePinIsSensitiveToTheRightThings:
    """Each case plants a change in the real file's text and checks the digests, writing no file."""

    @pytest.mark.parametrize("rel", sorted(_SCOPE))
    def test_a_comment_planted_outside_every_cut_node_moves_the_slice_digest(self, rel):
        planted = _read(rel) + "\n# planted outside the listed edits\n"
        assert scope_digest(planted, _SCOPE[rel]) != _PINNED_SCOPE_DIGESTS[rel]

    @pytest.mark.parametrize("rel", sorted(_SCOPE))
    def test_one_line_planted_mid_file_outside_the_cuts_moves_the_slice_digest(self, rel):
        text = _read(rel)
        lines = text.split("\n")
        inside = {i for s, e in _ranges(text, _SCOPE[rel]) for i in range(s, e + 1)}
        target = next(
            i for i in range(len(lines) // 2, len(lines)) if i not in inside and lines[i].strip()
        )
        lines.insert(target, "# one line planted outside every listed edit")
        assert scope_digest("\n".join(lines), _SCOPE[rel]) != _PINNED_SCOPE_DIGESTS[rel]

    @pytest.mark.parametrize("rel", sorted(_SCOPE))
    def test_a_change_inside_a_cut_node_does_not_move_the_slice_digest(self, rel):
        text = _read(rel)
        start, end = _ranges(text, _SCOPE[rel])[0]
        lines = text.split("\n")
        lines[end] = lines[end] + "  # changed inside the cut"
        assert scope_digest("\n".join(lines), _SCOPE[rel]) == scope_digest(text, _SCOPE[rel])

    def test_a_byte_changed_in_the_request_body_build_moves_the_client_digest(self):
        text = _read(_CLIENT)
        planted = text.replace('"broker": broker,', '"broker": broker ,', 1)
        assert planted != text
        assert scope_digest(planted, _CLIENT_EDITS) != _PINNED_SCOPE_DIGESTS[_CLIENT]

    def test_a_byte_changed_in_another_docstring_line_moves_the_docstring_digest(self):
        text = _read(_CLIENT)
        planted = text.replace("Starting portfolio value (USD).", "Starting portfolio value.", 1)
        assert planted != text
        assert _docstring_without_unit_line(planted) != _PINNED_DOCSTRING_WITHOUT_UNIT_LINE

    def test_a_byte_planted_outside_the_cuts_moves_every_pin_on_the_client(self):
        planted = _read(_CLIENT) + "\n# planted\n"
        assert scope_digest(planted, _CLIENT_EDITS) != _PINNED_SCOPE_DIGESTS[_CLIENT]
        full_file = regime_pins._sha256_bytes(planted.encode("utf-8"))
        assert full_file != client_pins._LF_DIGESTS[_CLIENT]

    def test_a_byte_planted_outside_the_cuts_moves_every_pin_on_autotuner(self):
        planted = _read(_AUTOTUNER) + "\n# planted\n"
        assert scope_digest(planted, _AUTOTUNER_EDITS) != _PINNED_SCOPE_DIGESTS[_AUTOTUNER]
        assert (
            regime_pins._sha256_bytes(planted.encode("utf-8"))
            != regime_pins._PINNED_HASHES[_AUTOTUNER]
        )
        assert (
            scope_digest(planted, hygiene_pins._SCOPE[_AUTOTUNER])
            != hygiene_pins._PINNED_SCOPE_DIGESTS[_AUTOTUNER]
        )
        assert (
            scope_digest(planted, precondition_pins._SCOPE[_AUTOTUNER])
            != precondition_pins._PINNED_SCOPE_DIGESTS[_AUTOTUNER]
        )

    def test_a_byte_planted_in_a_listed_edit_moves_the_full_file_pins_but_not_the_slice_digest(
        self,
    ):
        text = _read(_AUTOTUNER)
        planted = text.replace(
            "SIM_EXIT_FRICTION_PCT: float = 0.5",
            "# planted inside the listed statement\nSIM_EXIT_FRICTION_PCT: float = 0.5",
            1,
        )
        assert planted != text
        assert scope_digest(planted, _AUTOTUNER_EDITS) == scope_digest(text, _AUTOTUNER_EDITS)
        assert regime_pins._sha256_bytes(planted.encode("utf-8")) != regime_pins._sha256_bytes(
            text.encode("utf-8")
        )

    def test_crlf_and_lf_checkouts_hash_the_same(self):
        text = _read(_CLIENT)
        assert scope_digest(text.replace("\n", "\r\n"), _CLIENT_EDITS) == scope_digest(
            text, _CLIENT_EDITS
        )
        assert cut_text(text, _CLIENT_EDITS) == cut_text(text.replace("\n", "\r\n"), _CLIENT_EDITS)


# Every entry of the pin dictionaries other than the two re-taken ones, as `git show c3e12882`
# holds it in each pin module.
_UNTOUCHED_PINS = {
    "client fixtures": (
        client_pins._LF_DIGESTS,
        {
            "tests/fixtures/composer/backtest_inline_v1.json": (
                "a2dfa7992f11d0a008476591a526f0e20386e29b227fcfe61ff93940aa7ee7bf"
            ),
            "tests/fixtures/composer/backtest_response_v1.json": (
                "8112a737dbc56827b18d1dda7d4a825e383700dfb3a7a92706de2af4af0ada21"
            ),
        },
    ),
    "regime pin": (
        {k: v for k, v in regime_pins._PINNED_HASHES.items() if k != _AUTOTUNER},
        {
            "math_engine.py": "343d303c29ee9aa4c9f390791603a12f3130eec456fe589b3959732cd4477f79",
            "regime_classifier.py": (
                "04f8e1aa174c1802b8a4981522ce57d0fe9b801f7782323c298b719e3d9c1d52"
            ),
            "synthetic_history.py": (
                "71a5736d157733d459b1bd58b4fb652fb3a25945264bb6f2022d09318b629e2c"
            ),
            "migrations/027_regime_label_cache.sql": (
                "46b63e476670db9eb57afcccb6a15e8e78b8a9ddc08d7ca50680d6c2a26fe8d2"
            ),
        },
    ),
    "hygiene pin": (
        {k: v for k, v in hygiene_pins._PINNED_SCOPE_DIGESTS.items() if k != _AUTOTUNER},
        {
            "alpha_bot_execution.py": (
                "2d555aacd952b4848483db6ba5fdbb44fe898442fa0b30d91009ca7c498e9a7e"
            ),
            "math_engine.py": "de998d4bb0f7b57c98c06b15b9f48850b92c5fd58426b0b98ed0141765b6ac3e",
            "regime_classifier.py": (
                "7a5fc8dae2c3d1c88d4c84513b4f9b8a20b00ab133b29a75d0986340964e7693"
            ),
        },
    ),
    "preconditions pin": (
        {k: v for k, v in precondition_pins._PINNED_SCOPE_DIGESTS.items() if k != _AUTOTUNER},
        {
            "synthetic_history.py": "1b284b587a208f354732fb6d5836c46dbd99fc7530a8c8946db047b3a957d6ad"
        },
    ),
}


class TestEveryOtherPinKeepsItsReferenceDigest:
    @pytest.mark.parametrize("pin", sorted(_UNTOUCHED_PINS))
    def test_the_entries_other_than_the_re_taken_ones_equal_the_reference(self, pin):
        actual, reference = _UNTOUCHED_PINS[pin]
        keys = {k: v for k, v in actual.items() if k != _CLIENT}
        assert keys == reference

    def test_the_engine_pin_without_the_regime_read_equals_the_reference(self):
        assert regime_pins._PINNED_ENGINE_WITHOUT_REGIME_READ == (
            "3d865b08ac6e2f977e48fc4078db59e3910e9be21158b2431a9e4e273295ad21"
        )

    def test_the_captured_client_fixtures_still_hash_to_their_pinned_digests(self):
        for rel in (
            "tests/fixtures/composer/backtest_inline_v1.json",
            "tests/fixtures/composer/backtest_response_v1.json",
        ):
            assert client_pins._lf_sha256(rel) == client_pins._LF_DIGESTS[rel]


_RETAKEN_PINS = (
    (
        "tests/ai_advisor/test_advisor_backtest_client.py",
        "_LF_DIGESTS",
        "advisors/composer_backtest_client.py",
    ),
    ("tests/test_regime_label_protected_files.py", "_PINNED_HASHES", "autotuner.py"),
    ("tests/test_hygiene_protected_surfaces.py", "_PINNED_SCOPE_DIGESTS", "autotuner.py"),
    (
        "tests/test_preconditions_replay_protected_scope.py",
        "_PINNED_SCOPE_DIGESTS",
        "autotuner.py",
    ),
)
_RETAKE_COMMENT = re.compile(
    r"re-taken at [0-9a-f]{7,40} for backtest-slippage-calibration "
    r"\(calibrated backtest slippage default\); "
    r"tests/test_backtest_slippage_calibration_protected_scope\.py proves the change stays "
    r"inside its listed edits\."
)


def _comment_above_dictionary_key(rel: str, dictionary: str, key: str) -> str:
    text = _read(rel)
    lines = text.split("\n")
    for node in ast.parse(text).body:
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        if not any(isinstance(t, ast.Name) and t.id == dictionary for t in targets):
            continue
        assert isinstance(node.value, ast.Dict), f"{dictionary} in {rel} is not a dictionary"
        for entry in node.value.keys:
            if isinstance(entry, ast.Constant) and entry.value == key:
                above = []
                for line in reversed(lines[: entry.lineno - 1]):
                    if not line.strip().startswith("#"):
                        break
                    above.append(line.strip())
                return " ".join(reversed(above))
    raise AssertionError(f"{rel}: no {dictionary}[{key!r}] entry")


class TestTheReTakenPinsNameThisChange:
    @pytest.mark.parametrize(
        ("rel", "dictionary", "key"),
        _RETAKEN_PINS,
        ids=[p[0].rsplit("/", 1)[-1] for p in _RETAKEN_PINS],
    )
    def test_each_re_taken_entry_carries_the_comment_naming_the_change(self, rel, dictionary, key):
        comment = _comment_above_dictionary_key(rel, dictionary, key)
        assert _RETAKE_COMMENT.search(comment), (
            f"{rel} {dictionary}[{key!r}] must be re-taken with the comment naming this change; "
            f"the comment above it reads: {comment!r}"
        )
