"""Write ``copy.json``: the text of every row of the plan's three copy tables and Values table.

TEST-ONLY. Run from the repository root:

    python tests/fixtures/page_explainers/extract_copy.py --pass-one  # copy.json without the kept rows' ref text
    python tests/fixtures/page_explainers/extract_copy.py            # write copy.json
    python tests/fixtures/page_explainers/extract_copy.py --check <sha>   # compare with the plan at <sha>

The plan is read through ``git show <sha>:<path>`` and never from the working tree, and no test
reads the plan: ``tests/ui/_page_copy.py`` reads ``copy.json`` only, so revising or archiving the
plan breaks no test. ``PROVENANCE.md`` beside the output names the plan commit it was read at.

A row this script cannot parse stops it with the row's key (never a silent skip). Cross
references are resolved here, at extraction time, so a test never has to:

* a Copy table B definition that names another row ("(the dash.hero description)") takes that
  row's new text (``new_text`` below is then the resolved text, ``resolved_from`` names the row);
* Copy table C's Y14b takes the D29a to D29d definitions of Copy table B without the leading
  "Name: ";
* a "resolved: ...; unresolved: ..." text (the model-name rows) is split into ``new_resolved`` and
  ``new_unresolved``;
* the per-state sentences of H02 and H08 are split into ``states`` (state name to sentence);
* a kept row whose ref text is a pointer to a template line takes the rendered text recorded in
  ``ref_capture.json`` (``kept_rows``), written at the reference commit by
  ``produce_ref_capture.py``. That file needs Copy table C's old texts, which do not depend on the
  kept rows, so the order is: ``--pass-one`` (kept rows unresolved), the ref capture, then the
  full extraction.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
PLAN_PATH = "feature-plans/page-explainers-and-density.md"
OUT = HERE / "copy.json"
REF_CAPTURE = HERE / "ref_capture.json"

TABLE_HEADINGS = {
    "A": "#### Copy table A",
    "B": "#### Copy table B",
    "C": "#### Copy table C",
    "V": "#### Values table",
}
COLUMNS = {
    "A": ["key", "host_place", "builder", "kind", "old_text", "new_text", "true_of"],
    "B": ["key", "host", "term", "placement", "builder", "new_text", "true_of"],
    "C": ["key", "host_place", "builder", "old_text", "new_text", "true_of"],
    "V": [
        "slot",
        "copy_row",
        "phrase",
        "fallback",
        "authority",
        "in_context",
        "format",
    ],
}
CELL_SPLIT = re.compile(r"(?<!\\)\|")
CROSS_REF = re.compile(r"^\(the ([A-Za-z0-9_.]+) description\)$")
RESOLVED = re.compile(r"^resolved: (?P<res>.*?); unresolved: (?P<unres>.*)$")
SLOT = re.compile(r"\{([a-z_]+)\}")
STATE_SENTENCE = re.compile(r"(?P<name>[A-Z][A-Za-z ]*?): (?P<text>[^.]*\.(?: [^A-Z][^.]*\.)*)")


class ExtractionError(SystemExit):
    pass


def _git_show(sha: str, path: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{sha}:{path}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise ExtractionError(f"git show {sha}:{path} failed: {result.stderr.strip()}")
    return result.stdout


def _section_lines(plan: str, table: str) -> list[str]:
    lines = plan.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(TABLE_HEADINGS[table]))
    out = []
    for ln in lines[start + 1 :]:
        if ln.startswith("#### ") or ln.startswith("## "):
            break
        out.append(ln)
    return out


def _table_rows(lines: list[str], table: str) -> list[list[str]]:
    rows = []
    header_seen = False
    width = len(COLUMNS[table])
    for ln in lines:
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in CELL_SPLIT.split(ln.strip())[1:-1]]
        if not header_seen:
            header_seen = True
            if len(cells) != width:
                raise ExtractionError(
                    f"table {table}: header has {len(cells)} columns, not {width}"
                )
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        if len(cells) != width:
            raise ExtractionError(f"table {table}: row {cells[0]!r} has {len(cells)} columns")
        rows.append(cells)
    return rows


def _unquote(text: str) -> str:
    return text.replace("\\|", "|")


def _slots(*texts: str | None) -> list[str]:
    found: list[str] = []
    for text in texts:
        for name in SLOT.findall(text or ""):
            if name not in found:
                found.append(name)
    return found


def _split_states(key: str, text: str) -> dict[str, str]:
    """``Live: ... Closed: ...`` into ``{state: sentence}``; the closing remark is dropped."""
    body = text.replace(" Each state carries only its own sentence.", "")
    # A state name is a capitalised phrase followed by a colon, at the start of a sentence.
    pieces = re.split(r"(?:(?<=\.)|^) (?=[A-Z][A-Za-z ]*: )|^(?=[A-Z][A-Za-z ]*: )", body)
    states: dict[str, str] = {}
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        name, _, rest = piece.partition(": ")
        if not rest:
            raise ExtractionError(f"{key}: cannot split a state sentence from {piece!r}")
        states[name] = f"{name}: {rest}".strip()
    if len(states) < 2:
        raise ExtractionError(f"{key}: expected several state sentences, found {len(states)}")
    return states


def extract(sha: str, *, allow_unresolved_kept: bool = False) -> dict:
    plan = _git_show(sha, PLAN_PATH)
    try:
        ref = json.loads(REF_CAPTURE.read_text(encoding="utf-8"))
        kept_rows = ref.get("kept_rows", {})
    except FileNotFoundError:
        kept_rows = {}

    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for table in ("A", "B", "C"):
        for cells in _table_rows(_section_lines(plan, table), table):
            record = dict(zip(COLUMNS[table], (_unquote(c) for c in cells), strict=True))
            key = record["key"]
            if (table, key) in seen:
                raise ExtractionError(f"table {table}: key {key!r} appears twice")
            seen.add((table, key))
            record["table"] = table
            # A criterion reference after the text ("... #{id} (AC-8)") is the plan's pointer, not
            # page text.
            annotated = re.match(r"^(?P<text>.*\S) \((?P<ref>AC-\d+)\)$", record["new_text"])
            record["annotation"] = annotated["ref"] if annotated else None
            if annotated:
                record["new_text"] = annotated["text"]
            record.setdefault("kind", "reworded" if table == "C" else "new")
            if table == "B":
                placement = record["placement"].split(" ", 1)
                record["placement_kind"] = placement[0]
                record["placement_target"] = placement[1] if len(placement) > 1 else None
                if placement[0] not in {"V", "D", "A"}:
                    raise ExtractionError(f"{key}: unknown placement {record['placement']!r}")
            rows.append(record)

    by_key = {(r["table"], r["key"]): r for r in rows}

    for record in rows:
        key = record["key"]
        text = record["new_text"]
        record["unchanged"] = text == "unchanged"
        record["resolved_from"] = None
        reference = CROSS_REF.match(text)
        if reference:
            target = by_key.get(("A", reference.group(1)))
            if target is None:
                raise ExtractionError(
                    f"{key}: cross reference to unknown row {reference.group(1)!r}"
                )
            record["new_text"] = target["new_text"]
            record["resolved_from"] = target["key"]
        split = RESOLVED.match(record["new_text"])
        if split:
            record["new_resolved"] = split["res"]
            record["new_unresolved"] = split["unres"]
        if record["table"] == "B" and record["placement_kind"] == "A" and key in {"H02", "H08"}:
            record["states"] = _split_states(key, record["new_text"])
        if record["unchanged"]:
            old = record["old_text"]
            if old.startswith("the ") and " at `" in old:
                text_at_ref = kept_rows.get(key)
                if not text_at_ref and allow_unresolved_kept:
                    text_at_ref = None
                elif not text_at_ref:
                    raise ExtractionError(
                        f"{key}: a kept row whose ref text is a pointer has no entry under "
                        f"kept_rows in {REF_CAPTURE.name} (write the ref capture first)"
                    )
                record["ref_text"] = text_at_ref
            else:
                record["ref_text"] = old
        if key == "Y14b" and record["table"] == "C":
            names = ("D29a", "D29b", "D29c", "D29d")
            definitions = []
            for name in names:
                target = by_key.get(("B", name))
                if target is None:
                    raise ExtractionError(f"Y14b: Copy table B has no {name}")
                definitions.append(target["new_text"].split(": ", 1)[1])
            record["new_texts"] = definitions
            record["resolved_from"] = "B:" + ",".join(names)
        record["slots"] = _slots(
            record["new_text"], record.get("new_resolved"), record.get("new_unresolved")
        )

    values = []
    for cells in _table_rows(_section_lines(plan, "V"), "V"):
        record = dict(zip(COLUMNS["V"], (_unquote(c) for c in cells), strict=True))
        slot = record["slot"]
        if not (slot.startswith("{") and slot.endswith("}")):
            raise ExtractionError(f"Values table: slot cell {slot!r} is not in braces")
        record["slot"] = slot[1:-1]
        row = next((r for r in rows if r["key"] == record["copy_row"]), None)
        if row is None:
            raise ExtractionError(f"Values table: {record['copy_row']!r} is in no copy table")
        if record["slot"] not in row["slots"]:
            raise ExtractionError(
                f"Values table: {record['copy_row']} holds no slot {{{record['slot']}}}"
            )
        if record["phrase"] not in row["new_text"]:
            raise ExtractionError(
                f"Values table: the phrase for {record['copy_row']} is not inside its new text"
            )
        record["table"] = row["table"]
        values.append(record)

    return {
        "plan_path": PLAN_PATH,
        "plan_sha": sha,
        "rows": rows,
        "values": values,
    }


def _canonical(document: dict) -> str:
    return json.dumps(document, indent=1, sort_keys=False, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "--check":
        fresh = extract(argv[2])
        stored = json.loads(OUT.read_text(encoding="utf-8"))
        fresh_cmp = {k: v for k, v in fresh.items() if k != "plan_sha"}
        stored_cmp = {k: v for k, v in stored.items() if k != "plan_sha"}
        if fresh_cmp == stored_cmp:
            print(f"copy.json equals the plan at {argv[2]}")
            return 0
        fresh_rows = {(r["table"], r["key"]): r for r in fresh["rows"]}
        stored_rows = {(r["table"], r["key"]): r for r in stored["rows"]}
        for key in sorted(set(fresh_rows) | set(stored_rows)):
            if fresh_rows.get(key) != stored_rows.get(key):
                print(f"DIFFERS: {key}")
        if fresh["values"] != stored["values"]:
            print("DIFFERS: values table")
        return 1
    pass_one = "--pass-one" in argv
    argv = [a for a in argv if a != "--pass-one"]
    sha = argv[1] if len(argv) == 2 else "2681e693"
    full = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", sha], capture_output=True, text=True, check=True
    ).stdout.strip()
    document = extract(full, allow_unresolved_kept=pass_one)
    with open(OUT, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical(document))
    print(
        f"wrote {OUT.name}: {len(document['rows'])} rows, {len(document['values'])} slot rows at {full}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
