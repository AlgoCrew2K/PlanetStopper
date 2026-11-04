"""Read stored rows back with a plain read-only SELECT, for tests that check a writer.

TEST-ONLY. Writer tests used to read through accessors that production never called; the
readers are gone, so a writer's stored column values are read here, on a read-only connection
of the same database the writer wrote to. Each helper returns what the column holds, with the
JSON columns parsed, and nothing else.

The ``advisor_observations`` rows are parsed by the parser the remaining readers use, so a
row comes back exactly as ``get_advisor_observations_for_role`` and
``get_advisor_observations_for_symphony`` return it.
"""

from __future__ import annotations

import json

import database

# Column order of the llm_suggestions table as the writer fills it.
LLM_SUGGESTION_COLUMNS = (
    "id",
    "session_id",
    "created_at",
    "symphony_name",
    "operator_identity",
    "prompt_inputs",
    "model_id",
    "generation_settings",
    "raw_response",
    "validation_results",
    "param_name",
    "operator_decision",
    "decision_at",
    "operator_note",
    "before_value",
    "after_value",
    "oos_revalidation",
)
LLM_SUGGESTION_JSON_COLUMNS = frozenset(
    {
        "prompt_inputs",
        "generation_settings",
        "raw_response",
        "validation_results",
        "oos_revalidation",
        "before_value",
        "after_value",
    }
)
_LLM_FILTER_COLUMNS = frozenset({"session_id", "symphony_name"})


def advisor_observations_for_subject(subject_type: str, subject_id: str) -> list[dict]:
    """Rows of one (subject_type, subject_id), oldest first, as the remaining readers return them."""
    conn = database.get_ro_connection()
    try:
        rows = conn.execute(
            "SELECT "
            + ", ".join(database._ADVISOR_OBSERVATION_COLUMNS)
            + " FROM advisor_observations WHERE subject_type = ? AND subject_id = ? ORDER BY id ASC",
            (subject_type, subject_id),
        ).fetchall()
    finally:
        conn.close()
    return [
        database._parse_advisor_observation_row(row, database._ADVISOR_OBSERVATION_COLUMNS)
        for row in rows
    ]


def llm_suggestions_where(column: str, value: str) -> list[dict]:
    """Every llm_suggestions row whose `column` (session_id or symphony_name) equals `value`, oldest first."""
    assert column in _LLM_FILTER_COLUMNS, f"unsupported filter column {column!r}"
    conn = database.get_ro_connection()
    try:
        rows = conn.execute(
            "SELECT "
            + ", ".join(LLM_SUGGESTION_COLUMNS)
            + f" FROM llm_suggestions WHERE {column} = ? ORDER BY id ASC",
            (value,),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        stored = {}
        for col, val in zip(LLM_SUGGESTION_COLUMNS, row):
            stored[col] = (
                json.loads(val) if col in LLM_SUGGESTION_JSON_COLUMNS and val is not None else val
            )
        result.append(stored)
    return result


def llm_suggestions_for_symphony(symphony_name: str) -> list[dict]:
    return llm_suggestions_where("symphony_name", symphony_name)


def llm_suggestions_for_session(session_id: str) -> list[dict]:
    return llm_suggestions_where("session_id", session_id)
