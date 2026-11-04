"""The advisor and frontrunner row parsers turn a stored JSON column into a value in one place.

``database.py`` parses an ``advisor_observations`` row and a ``frontrunner_proposals`` row into
a dict. The two parsers differ in one decision only: the observation parser keeps a row whose
``raw_response`` is not JSON (it comes back as ``{}`` plus ``raw_response_unreadable: True``),
the frontrunner parser lets the decoder's exception out. Everything else is the same loop over
the columns, so it is to be written once.

Expected values come from a verbatim copy of each parser's body at the base (``_base_*`` below),
run on the very tuples the code under test is given, and from the plain statement of each rule
in the tables, so a parser bug cannot pass its own test. The stored kinds are synthetic and
labelled so: they are the shapes a writer can store (``insert_advisor_observation`` accepts a
pre-serialised string as it is), one per kind the parsers distinguish.

* S-4.1: every accessor that reads either table returns what the base returned for the same rows;
* S-4.2: each stored kind through each parser (characterization of the base);
* S-4.3: exactly one definition parses JSON columns, and both parsers use it (structural).
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

import database

DATABASE_PY = pathlib.Path(__file__).resolve().parents[2] / "database.py"

OBS_COLUMNS = list(database._ADVISOR_OBSERVATION_COLUMNS)
FR_COLUMNS = list(database._FRONTRUNNER_PROPOSAL_COLUMNS)


def _base_parse_observation(row: tuple, columns: list[str]) -> dict:
    """Verbatim body of the observation parser at the base."""
    result = {}
    for col, val in zip(columns, row):
        if col == "raw_response" and val is not None:
            try:
                result[col] = json.loads(val)
            except (TypeError, ValueError):
                result[col] = {}
                result["raw_response_unreadable"] = True
        else:
            result[col] = val
    return result


def _base_parse_frontrunner(row: tuple, columns: list[str]) -> dict:
    """Verbatim body of the frontrunner parser at the base."""
    result = {}
    for col, val in zip(columns, row):
        if col in ("candidate_tree", "metrics_json") and val is not None:
            result[col] = json.loads(val)
        else:
            result[col] = val
    return result


_NOT_JSON_BLOB = b"\x80\x81\x82"
_JSON_BLOB = b'{"from": "blob", "n": [1, 2]}'

# (label, stored value, what the observation parser must return for raw_response,
#  whether the row is flagged unreadable). Each rule is stated here, not derived.
OBSERVATION_KINDS = [
    ("text_object", '{"a": 1, "b": [true, null]}', {"a": 1, "b": [True, None]}, False),
    ("text_array", "[1, 2, 3]", [1, 2, 3], False),
    ("text_string", '"plain text"', "plain text", False),
    ("text_number", "42", 42, False),
    ("text_unreadable", "not json {", {}, True),
    ("sql_null", None, None, False),
    ("integer", 7, {}, True),
    ("real", 1.5, {}, True),
    ("blob_json", _JSON_BLOB, {"from": "blob", "n": [1, 2]}, False),
    ("blob_not_json", _NOT_JSON_BLOB, {}, True),
]

# (label, stored value, parsed value or the exception type the frontrunner parser raises).
FRONTRUNNER_KINDS = [
    ("text_object", '{"a": 1, "b": [true, null]}', {"a": 1, "b": [True, None]}),
    ("text_array", "[1, 2, 3]", [1, 2, 3]),
    ("text_string", '"plain text"', "plain text"),
    ("text_number", "42", 42),
    ("text_unreadable", "not json {", json.JSONDecodeError),
    ("sql_null", None, None),
    ("integer", 7, TypeError),
    ("real", 1.5, TypeError),
    ("blob_json", _JSON_BLOB, {"from": "blob", "n": [1, 2]}),
    ("blob_not_json", _NOT_JSON_BLOB, UnicodeDecodeError),
]


def _observation_tuple(raw_response) -> tuple:
    return (
        11,
        "2026-11-04 01:02:03",
        "SPEC_CRITIC",
        "autotune_run",
        "subject-x",
        "CLEAR",
        raw_response,
        1,
        None,
        "sym-x",
    )


def _frontrunner_tuple(candidate_tree, metrics_json) -> tuple:
    return (
        12,
        "2026-11-04 01:02:03",
        "2026-11-04 01:02:04",
        "sym-x",
        "frontrunner_builder",
        "pending",
        candidate_tree,
        metrics_json,
        None,
        None,
    )


class TestObservationParserKinds:
    @pytest.mark.parametrize(
        ("label", "stored", "expected_raw", "unreadable"),
        OBSERVATION_KINDS,
        ids=[k[0] for k in OBSERVATION_KINDS],
    )
    def test_each_stored_kind(self, label, stored, expected_raw, unreadable):
        row = _observation_tuple(stored)

        parsed = database._parse_advisor_observation_row(row, OBS_COLUMNS)

        assert parsed["raw_response"] == expected_raw, f"kind {label}"
        assert ("raw_response_unreadable" in parsed) is unreadable, f"kind {label}"
        if unreadable:
            assert parsed["raw_response_unreadable"] is True
        # Whole dict and key order equal the base body's result.
        base = _base_parse_observation(row, OBS_COLUMNS)
        assert list(parsed.items()) == list(base.items()), f"kind {label}"

    def test_a_readable_row_carries_no_unreadable_key(self):
        parsed = database._parse_advisor_observation_row(_observation_tuple("{}"), OBS_COLUMNS)
        assert "raw_response_unreadable" not in parsed

    def test_the_other_columns_pass_through_untouched(self):
        row = _observation_tuple('{"k": 1}')
        parsed = database._parse_advisor_observation_row(row, OBS_COLUMNS)
        assert [parsed[c] for c in OBS_COLUMNS if c != "raw_response"] == [
            v for c, v in zip(OBS_COLUMNS, row) if c != "raw_response"
        ]

    def test_a_json_looking_value_in_another_column_is_not_parsed(self):
        row = list(_observation_tuple("{}"))
        row[OBS_COLUMNS.index("verdict")] = '{"not": "parsed"}'
        parsed = database._parse_advisor_observation_row(tuple(row), OBS_COLUMNS)
        assert parsed["verdict"] == '{"not": "parsed"}'


class TestFrontrunnerParserKinds:
    @pytest.mark.parametrize("column", ["candidate_tree", "metrics_json"])
    @pytest.mark.parametrize(
        ("label", "stored", "outcome"),
        FRONTRUNNER_KINDS,
        ids=[k[0] for k in FRONTRUNNER_KINDS],
    )
    def test_each_stored_kind(self, column, label, stored, outcome):
        other = "{}"
        row = (
            _frontrunner_tuple(stored, other)
            if column == "candidate_tree"
            else _frontrunner_tuple(other, stored)
        )

        if isinstance(outcome, type) and issubclass(outcome, BaseException):
            with pytest.raises(outcome) as raised:
                database._parse_frontrunner_proposal_row(row, FR_COLUMNS)
            with pytest.raises(BaseException) as base_raised:  # noqa: PT011
                _base_parse_frontrunner(row, FR_COLUMNS)
            assert type(raised.value) is type(base_raised.value), f"{column} kind {label}"
            return
        parsed = database._parse_frontrunner_proposal_row(row, FR_COLUMNS)
        assert parsed[column] == outcome, f"{column} kind {label}"
        assert list(parsed.items()) == list(_base_parse_frontrunner(row, FR_COLUMNS).items())

    def test_a_row_never_gains_an_unreadable_flag(self):
        parsed = database._parse_frontrunner_proposal_row(
            _frontrunner_tuple("{}", "{}"), FR_COLUMNS
        )
        assert "raw_response_unreadable" not in parsed
        assert list(parsed) == FR_COLUMNS

    def test_a_json_looking_value_in_another_column_is_not_parsed(self):
        row = list(_frontrunner_tuple("{}", "{}"))
        row[FR_COLUMNS.index("error_message")] = '{"not": "parsed"}'
        parsed = database._parse_frontrunner_proposal_row(tuple(row), FR_COLUMNS)
        assert parsed["error_message"] == '{"not": "parsed"}'

    def test_the_two_parsers_disagree_only_on_an_unreadable_value(self):
        """The observation parser keeps the row, the frontrunner parser raises: one decision."""
        bad = "not json {"
        parsed = database._parse_advisor_observation_row(_observation_tuple(bad), OBS_COLUMNS)
        assert parsed["raw_response_unreadable"] is True
        with pytest.raises(json.JSONDecodeError):
            database._parse_frontrunner_proposal_row(_frontrunner_tuple(bad, "{}"), FR_COLUMNS)


# ---------------------------------------------------------------------------
# S-4.1: every accessor over the stored rows returns what the base returned
# ---------------------------------------------------------------------------


def _insert_observation(role: str, subject_id: str, raw_response, symphony_id: str | None) -> None:
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO advisor_observations "
            "(advisor_role, subject_type, subject_id, verdict, raw_response, is_advisory_only, "
            "spec_bundle_id, symphony_id) VALUES (?, ?, ?, ?, ?, 1, NULL, ?)",
            (role, "autotune_run", subject_id, "CLEAR", raw_response, symphony_id),
        )
        conn.commit()
    finally:
        conn.close()


def _stored_observation_tuples(where: str = "", params: tuple = ()) -> list[tuple]:
    conn = database.get_ro_connection()
    try:
        return conn.execute(
            f"SELECT {', '.join(OBS_COLUMNS)} FROM advisor_observations {where} ORDER BY id ASC",
            params,
        ).fetchall()
    finally:
        conn.close()


def _stored_frontrunner_tuples(where: str = "", params: tuple = ()) -> list[tuple]:
    conn = database.get_ro_connection()
    try:
        return conn.execute(
            f"SELECT {', '.join(FR_COLUMNS)} FROM frontrunner_proposals {where} ORDER BY id ASC",
            params,
        ).fetchall()
    finally:
        conn.close()


def _as_items(rows: list[dict]) -> list[list[tuple]]:
    return [list(r.items()) for r in rows]


class TestObservationAccessorsReturnWhatTheBaseReturned:
    def _seed_every_kind(self, role: str, symphony_id: str) -> None:
        for label, stored, _expected, _unreadable in OBSERVATION_KINDS:
            if stored is None:
                continue  # raw_response is NOT NULL in the table
            _insert_observation(role, label, stored, symphony_id)
        # Rows the real writer produced: a dict, the default, and a pre-serialised string.
        database.insert_advisor_observation(
            advisor_role=role,
            subject_type="autotune_run",
            subject_id="writer_dict",
            verdict=None,
            raw_response={"k": "v", "nested": {"n": 1}},
            symphony_id=symphony_id,
        )
        database.insert_advisor_observation(
            advisor_role=role,
            subject_type="autotune_run",
            subject_id="writer_default",
            symphony_id=symphony_id,
        )
        database.insert_advisor_observation(
            advisor_role=role,
            subject_type="autotune_run",
            subject_id="writer_string",
            raw_response="not serialised json",
            symphony_id=symphony_id,
        )

    def test_symphony_reader_returns_every_row_as_the_base_parser_would(self):
        self._seed_every_kind("SPEC_CRITIC", "sym-parse")
        expected = [
            _base_parse_observation(t, OBS_COLUMNS)
            for t in _stored_observation_tuples("WHERE symphony_id = ?", ("sym-parse",))
        ]
        assert len(expected) >= len(OBSERVATION_KINDS)

        got = database.get_advisor_observations_for_symphony("sym-parse")

        assert _as_items(got) == _as_items(expected)

    def test_role_reader_returns_every_row_newest_first_as_the_base_parser_would(self):
        self._seed_every_kind("DIVERGENCE_EXPLAINER", "sym-parse-role")
        expected = [
            _base_parse_observation(t, OBS_COLUMNS)
            for t in _stored_observation_tuples("WHERE advisor_role = ?", ("DIVERGENCE_EXPLAINER",))
        ][::-1]

        got = database.get_advisor_observations_for_role("DIVERGENCE_EXPLAINER", limit=100)

        assert _as_items(got) == _as_items(expected)

    def test_the_unreadable_rows_are_flagged_and_the_rest_are_not(self):
        self._seed_every_kind("SPEC_CRITIC", "sym-flags")
        got = {
            r["subject_id"]: r for r in database.get_advisor_observations_for_symphony("sym-flags")
        }
        flagged = {k for k, r in got.items() if r.get("raw_response_unreadable")}
        # A number written to a text column is stored as text and reads back as JSON.
        assert flagged == {"text_unreadable", "blob_not_json", "writer_string"}

    @pytest.mark.parametrize(
        ("role", "accessor_name"),
        [
            ("MARKET_PRISM", "get_latest_market_prism_summary"),
            ("MARKET_LENS_CACHE", "get_latest_market_lens_cache"),
        ],
    )
    def test_latest_row_readers_return_the_newest_row_as_the_base_parser_would(
        self, role, accessor_name
    ):
        accessor = getattr(database, accessor_name)
        _insert_observation(role, "older", '{"order": "older"}', None)
        _insert_observation(role, "newest_unreadable", "not json {", None)

        got = accessor()

        (expected,) = [
            _base_parse_observation(t, OBS_COLUMNS)
            for t in _stored_observation_tuples("WHERE subject_id = ?", ("newest_unreadable",))
        ]
        assert list(got.items()) == list(expected.items())
        assert got["raw_response_unreadable"] is True

        _insert_observation(role, "newest_readable", '{"order": "newest"}', None)
        got = accessor()
        assert got["raw_response"] == {"order": "newest"}
        assert "raw_response_unreadable" not in got

    @pytest.mark.parametrize(
        ("role", "accessor_name"),
        [
            ("MARKET_PRISM_SOURCES", "get_latest_market_prism_sources_for_run"),
            ("MARKET_PRISM_VERIFICATION", "get_latest_market_prism_verification_for_run"),
        ],
    )
    def test_per_run_readers_return_the_matching_row_as_the_base_parser_would(
        self, role, accessor_name
    ):
        accessor = getattr(database, accessor_name)
        _insert_observation(role, "other", json.dumps({"run_id": "run-other", "x": 1}), None)
        _insert_observation(role, "wanted", json.dumps({"run_id": "run-wanted", "x": 2}), None)

        got = accessor("run-wanted")

        (expected,) = [
            _base_parse_observation(t, OBS_COLUMNS)
            for t in _stored_observation_tuples("WHERE subject_id = ?", ("wanted",))
        ]
        assert list(got.items()) == list(expected.items())
        assert accessor("run-absent") is None

    def test_a_per_run_reader_still_degrades_to_none_when_a_stored_value_is_not_json(self):
        _insert_observation("MARKET_PRISM_SOURCES", "bad", "not json {", None)

        assert database.get_latest_market_prism_sources_for_run("anything") is None


# A number written to a text column is stored as text, so only these two kinds stay unreadable
# when they go through the table.
TEXT_COLUMN_UNREADABLE = [
    k for k in FRONTRUNNER_KINDS if k[0] in {"text_unreadable", "blob_not_json"}
]


class TestFrontrunnerAccessorsReturnWhatTheBaseReturned:
    READABLE = [k for k in FRONTRUNNER_KINDS if not isinstance(k[2], type) and k[1] is not None]

    def _insert_raw(self, symphony_id: str, candidate_tree, metrics_json) -> int:
        conn = database.get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO frontrunner_proposals "
                "(symphony_id, proposal_source, candidate_tree, metrics_json) VALUES (?, ?, ?, ?)",
                (symphony_id, "frontrunner_builder", candidate_tree, metrics_json),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def test_every_readable_kind_through_every_accessor(self):
        for _label, stored, _outcome in self.READABLE:
            self._insert_raw("sym-fr-read", stored, stored)
        database.insert_frontrunner_proposal(
            symphony_id="sym-fr-read",
            proposal_source="strategy_builder_retrofit",
            candidate_tree={"step": "root", "children": [{"step": "asset", "ticker": "SPY"}]},
            metrics_json={"calmar": {"candidate": 1.5}},
        )
        database.insert_frontrunner_proposal(
            symphony_id="sym-fr-read",
            proposal_source="frontrunner_builder",
            candidate_tree=json.dumps({"serialised": True}),
        )
        stored = _stored_frontrunner_tuples("WHERE symphony_id = ?", ("sym-fr-read",))
        expected_oldest_first = [_base_parse_frontrunner(t, FR_COLUMNS) for t in stored]
        assert len(expected_oldest_first) == len(self.READABLE) + 2

        for_symphony = database.get_frontrunner_proposals_for_symphony("sym-fr-read")
        pending = [
            r
            for r in database.get_pending_frontrunner_proposals(limit=100)
            if r["symphony_id"] == "sym-fr-read"
        ]
        by_id = [database.get_frontrunner_proposal(t[0]) for t in stored]

        assert _as_items(for_symphony) == _as_items(expected_oldest_first[::-1])
        assert _as_items(pending) == _as_items(expected_oldest_first[::-1])
        assert _as_items(by_id) == _as_items(expected_oldest_first)

    @pytest.mark.parametrize(
        ("label", "stored", "outcome"),
        TEXT_COLUMN_UNREADABLE,
        ids=[k[0] for k in TEXT_COLUMN_UNREADABLE],
    )
    def test_an_unreadable_stored_value_raises_the_base_exception_from_the_single_row_reader(
        self, label, stored, outcome
    ):
        proposal_id = self._insert_raw(f"sym-fr-{label}", stored, "{}")

        with pytest.raises(outcome):
            database.get_frontrunner_proposal(proposal_id)

    def test_a_number_stored_in_a_text_column_reads_back_as_the_json_number(self):
        proposal_id = self._insert_raw("sym-fr-affinity", 7, 1.5)

        stored = _stored_frontrunner_tuples("WHERE id = ?", (proposal_id,))[0]
        expected = _base_parse_frontrunner(stored, FR_COLUMNS)

        assert database.get_frontrunner_proposal(proposal_id) == expected


# ---------------------------------------------------------------------------
# S-4.3 (structural): exactly one definition parses JSON columns
# ---------------------------------------------------------------------------


def _call_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
                if isinstance(func.value, ast.Name):
                    names.add(f"{func.value.id}.{func.attr}")
    return names


def _json_column_parse_definitions(tree: ast.AST) -> list[ast.FunctionDef]:
    """Functions that walk a row's columns (zip) and decode JSON (json.loads) in the same body."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            names = _call_names(node)
            if "zip" in names and "json.loads" in names:
                found.append(node)
    return found


class TestOneJsonColumnParseDefinition:
    def test_the_scan_counts_a_planted_duplicate(self):
        planted = ast.parse(
            "import json\n"
            "def a(row, cols):\n"
            "    return {c: json.loads(v) for c, v in zip(cols, row)}\n"
            "def b(row, cols):\n"
            "    out = {}\n"
            "    for c, v in zip(cols, row):\n"
            "        out[c] = json.loads(v)\n"
            "    return out\n"
        )
        assert [f.name for f in _json_column_parse_definitions(planted)] == ["a", "b"]

    def test_database_py_holds_exactly_one_json_column_parse_definition(self):
        tree = ast.parse(DATABASE_PY.read_text(encoding="utf-8"))
        definitions = _json_column_parse_definitions(tree)
        assert len(definitions) == 1, (
            "database.py must parse JSON columns of a row in one definition; found "
            f"{[(f.name, f.lineno) for f in definitions]}"
        )

    def test_both_remaining_parsers_use_that_definition(self):
        tree = ast.parse(DATABASE_PY.read_text(encoding="utf-8"))
        definitions = _json_column_parse_definitions(tree)
        assert len(definitions) == 1, "precondition: exactly one definition (see the test above)"
        shared = definitions[0].name
        parsers = {
            n.name: n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)
            and n.name in {"_parse_advisor_observation_row", "_parse_frontrunner_proposal_row"}
        }
        assert set(parsers) == {"_parse_advisor_observation_row", "_parse_frontrunner_proposal_row"}
        missing = [
            name
            for name, node in parsers.items()
            if name != shared and shared not in _call_names(node)
        ]
        assert not missing, f"{missing} must call the shared definition {shared!r}"
