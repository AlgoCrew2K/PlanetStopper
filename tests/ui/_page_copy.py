"""The expected text of every copy row, read from the committed fixture and nothing else.

TEST-ONLY. ``tests/fixtures/page_explainers/copy.json`` is written once by ``extract_copy.py``
from the approved plan's Copy tables A, B and C and its Values table; ``PROVENANCE.md`` beside it
names the plan commit. This module never reads ``feature-plans/``, so revising or archiving the
plan breaks no test, and it never calls a production formatter: a test fills a slot from the
authority it patched, in the format the Values table's Format column states, so the expected text
never just repeats what the code produced.

Row records carry: ``table`` ("A", "B" or "C"), ``key``, ``kind`` ("new", "kept" or "reworded"),
``new_text`` (cross references already resolved), ``old_text``, ``builder``, ``slots`` (names in
braces) and, where they apply, ``new_resolved``/``new_unresolved`` (the model rows), ``states``
(the header state sentences), ``new_texts`` (Y14b), ``unchanged`` and ``ref_text`` (a kept row).
A slot row of the Values table carries ``slot``, ``copy_row``, ``phrase`` (with the slot),
``fallback`` (the whole phrase that replaces it), ``authority`` and ``format``.
"""

from __future__ import annotations

import functools
import json
import pathlib

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "page_explainers" / "copy.json"


@functools.lru_cache(maxsize=1)
def load() -> dict:
    """The whole fixture (read once per process; callers must not mutate it)."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def rows(table: str | None = None) -> list[dict]:
    """Every row, or the rows of one table ("A", "B" or "C"), in the plan's order."""
    return [r for r in load()["rows"] if table is None or r["table"] == table]


def row(key: str, table: str | None = None) -> dict:
    """The one row named ``key``; a key present in two tables needs ``table``."""
    found = [r for r in rows(table) if r["key"] == key]
    if len(found) != 1:
        raise KeyError(f"{len(found)} rows named {key!r} (table {table!r})")
    return found[0]


def text(key: str, table: str | None = None) -> str:
    """The text the page must show for the row: the new text, or the ref text of a kept row."""
    found = row(key, table)
    if found.get("unchanged"):
        return found["ref_text"]
    return found["new_text"]


def slot_rows() -> list[dict]:
    return list(load()["values"])


def slot_row(slot: str, copy_row: str) -> dict:
    found = [v for v in slot_rows() if v["slot"] == slot and v["copy_row"] == copy_row]
    if len(found) != 1:
        raise KeyError(f"{len(found)} Values rows for {{{slot}}} in {copy_row!r}")
    return found[0]


def fill(template: str, **slots: str) -> str:
    """``template`` with each ``{name}`` replaced by the given string; unknown braces stay."""
    out = template
    for name, value in slots.items():
        out = out.replace("{" + name + "}", value)
    return out


def host_of(record: dict) -> str:
    """The host a row belongs to, as the table words it (``/``, ``Performance``, ``header``...)."""
    place = record.get("host") or record.get("host_place") or ""
    return place.split(",", 1)[0].strip().strip("`")
