"""The expected text of every copy row, read from the committed fixture and nothing else.

TEST-ONLY. ``tests/fixtures/page_explainers/copy.json`` is written by ``extract_copy.py`` from the
approved plan's Copy table A, Copy table X, Values table and Glossary tables (G, G2, G3), and from
the reference commit's own fixture; ``PROVENANCE.md`` beside it names both commits. This module
never reads ``feature-plans/``, so revising or archiving a plan breaks no test, and it never calls
a production formatter: a test fills a slot from the authority it patched, in the format the Values
table's Format column states, so the expected text never just repeats what the code produced.

Two families of accessors, because the reference's rows are still read by every test no cycle has
re-pinned yet:

* **Reference rows** (``rows``, ``row``, ``text``, ``host_of``): the fixture's ``ref_rows``, the
  earlier plan's Copy tables A, B and C exactly as the reference commit's fixture held them. A row
  record carries ``table`` ("A", "B" or "C"), ``key``, ``kind``, ``new_text`` (cross references
  already resolved), ``old_text``, ``builder``, ``slots``, and where they apply ``new_resolved`` and
  ``new_unresolved``, ``states``, ``new_texts``, ``unchanged`` and ``ref_text``.
* **This plan's rows**: ``a_rows``/``a_row``/``a_text`` (Copy table A, a description per section),
  ``x_rows``/``x_row`` (Copy table X, other text that changes), ``glossary``/``glossary_hosts``
  (the Glossary lines per host, in order) and ``title_rows`` (the labels that carry a Glossary line
  as their title). A row carries ``kind`` (``kept``, ``shortened``, ``removed``, ``moved`` or
  ``reworded``), ``new_text`` (None where the row reads none or points at a rule), ``new_text_note``
  (the plan's pointer where ``new_text`` is None), ``unchanged`` with
  ``ref_text`` (a kept row's text at the reference) and ``slots``.

A slot row of the Values table carries ``slot``, ``copy_row``, ``phrase`` (with the slot),
``fallback`` (the whole phrase that replaces it), ``authority`` and ``format``. ``slot_row`` serves
the reference's; ``new_slot_row`` serves this plan's, where the two Retirement phrases differ.
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


# ---------------------------------------------------------------------------
# The reference's rows (read by every test not yet re-pinned)
# ---------------------------------------------------------------------------


def rows(table: str | None = None) -> list[dict]:
    """Every reference row, or those of one table ("A", "B" or "C"), in the earlier plan's order."""
    return [r for r in load()["ref_rows"] if table is None or r["table"] == table]


def row(key: str, table: str | None = None) -> dict:
    """The one reference row named ``key``; a key present in two tables needs ``table``."""
    found = [r for r in rows(table) if r["key"] == key]
    if len(found) != 1:
        raise KeyError(f"{len(found)} rows named {key!r} (table {table!r})")
    return found[0]


def text(key: str, table: str | None = None) -> str:
    """The text the reference page shows for the row: the new text, or a kept row's ref text."""
    found = row(key, table)
    if found.get("unchanged"):
        return found["ref_text"]
    return found["new_text"]


def slot_rows() -> list[dict]:
    """The reference's slot rows (the earlier plan's Values table)."""
    return list(load()["ref_values"])


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


# ---------------------------------------------------------------------------
# This plan's rows
# ---------------------------------------------------------------------------


def new_slot_rows() -> list[dict]:
    """This plan's slot rows: the reference's, with the two the plan's Values table changes (the
    screen threshold in R02 and the tenure threshold in R22) carrying their new phrase."""
    return list(load()["values"])


def new_slot_row(slot: str, copy_row: str) -> dict:
    found = [v for v in new_slot_rows() if v["slot"] == slot and v["copy_row"] == copy_row]
    if len(found) != 1:
        raise KeyError(f"{len(found)} Values rows for {{{slot}}} in {copy_row!r}")
    return found[0]


def a_rows(host: str | None = None) -> list[dict]:
    """Copy table A: a description per section, in plan order; ``host`` keeps one host's rows."""
    return [r for r in load()["rows"] if r["table"] == "A" and (host is None or host_of(r) == host)]


def a_row(key: str) -> dict:
    found = [r for r in a_rows() if r["key"] == key]
    if len(found) != 1:
        raise KeyError(f"{len(found)} Copy table A rows named {key!r}")
    return found[0]


def a_text(key: str) -> str | None:
    """What the page shows under the section: a kept row's text at the reference, the new text,
    or None where the row reads none (a removed or moved description)."""
    found = a_row(key)
    return found["ref_text"] if found["unchanged"] else found["new_text"]


def x_rows() -> list[dict]:
    """Copy table X: other text that changes, in plan order."""
    return [r for r in load()["rows"] if r["table"] == "X"]


def x_row(key: str) -> dict:
    found = [r for r in x_rows() if r["key"] == key]
    if len(found) != 1:
        raise KeyError(f"{len(found)} Copy table X rows named {key!r}")
    return found[0]


def glossary_hosts() -> list[str]:
    """The hosts that carry a Glossary, in Table G order."""
    return list(load()["glossary"])


def glossary(host: str) -> list[dict]:
    """The Glossary lines of ``host`` in order: ``key``, ``text``, ``lead_term`` (None when the
    text holds no ": "), ``slots`` and ``source`` ("ref" or "G2")."""
    return list(load()["glossary"][host])


def title_rows() -> list[dict]:
    """The labels that carry a Glossary line as their title: ``key``, ``host``, ``element``,
    ``line_keys``."""
    return list(load()["titles"])
