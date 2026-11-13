"""Write ``copy.json``: the expected text of every copy row, Glossary line and Glossary title.

TEST-ONLY. Run from the repository root:

    python tests/fixtures/page_explainers/extract_copy.py                 # write copy.json
    python tests/fixtures/page_explainers/extract_copy.py --check <sha>   # compare with the plan at <sha>

Two sources are read, both through ``git show`` and never from the working tree, and no test reads
either: ``tests/ui/_page_copy.py`` reads ``copy.json`` only, so revising or archiving a plan breaks
no test. ``PROVENANCE.md`` beside the output names both commits.

* the approved plan ``feature-plans/aligned-tables-and-quiet-copy.md`` at ``PLAN_COMMIT``: Copy
  table A (section descriptions), Copy table X (other text that changes), the Values table, Table G
  (Glossary lines per host), Table G2 (Glossary lines whose text changes or is new) and Table G3
  (labels that carry a Glossary line as their title);
* the reference commit's own fixture, ``tests/fixtures/page_explainers/copy.json`` at
  ``REFERENCE_COMMIT``: the rows the plan marks "(unchanged)" and every Glossary key that stands for
  "the ``new_text`` of that row at the reference" take their text from it, and its rows are kept
  whole under ``ref_rows`` so a test not yet re-pinned keeps reading what it read before.

Output keys: ``rows`` (Copy tables A and X, in plan order), ``glossary`` (host to its lines in Table
G order), ``titles`` (Table G3), ``values`` (the reference's slot rows with the plan's Values
table rows replacing the two it changes), ``ref_rows`` and ``ref_values`` (the reference fixture's
own, unchanged).

A row this script cannot parse stops it with the row's key (never a silent skip). Cross references
are resolved here, at extraction time, so a test never has to:

* a Copy table A row reading "(unchanged)" takes the text the reference fixture's row of the same
  key shows (``ref_text`` of a kept row, else ``new_text``);
* Table G2's O31a, O31b and O31c are the first three sentences of the reference's guardrail
  paragraph (``templates/ai_advisor.html`` at the reference, the paragraph marked
  ``guardrail-uniform-note``), whitespace collapsed, each after its "Term: " prefix without the
  term's own leading name and without a following "is" or "are", every other word kept;
* a Table G3 cell naming Glossary lines ("F15a to F15g; F15h") is expanded into ``line_keys``.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
PLAN_PATH = "feature-plans/aligned-tables-and-quiet-copy.md"
REF_FIXTURE_PATH = "tests/fixtures/page_explainers/copy.json"
REF_TEMPLATE_PATH = "templates/ai_advisor.html"
REFERENCE_COMMIT = "c0760e70eb6f8c6620002570b493bb9ca4ec6b1e"
# The approved commit; the tables read here are byte-equal at ac4563cb, the commit that last
# changed them.
PLAN_COMMIT = "75243235"
OUT = HERE / "copy.json"

HEADINGS = {
    "A": "#### Copy table A",
    "X": "#### Copy table X",
    "V": "#### Values table",
    "G": "#### Table G:",
    "G2": "#### Table G2",
    "G3": "#### Table G3",
}
COLUMNS = {
    "A": ["key", "host_place", "builder", "disposition", "new_text", "true_of"],
    "X": ["key", "host_place", "builder", "disposition", "new_text", "why"],
    "V": ["slot", "copy_row", "phrase", "fallback", "authority"],
    "G": ["host", "lines"],
    "G2": ["key", "host", "disposition", "text", "why"],
    "G3": ["key", "host", "element", "glossary_line"],
}
CELL_SPLIT = re.compile(r"(?<!\\)\|")
SLOT = re.compile(r"\{([a-z_]+)\}")
GUARDRAIL_NAMES = ("Spec Critic", "Overfitting Conscience", "Divergence Explainer")
GUARDRAIL_KEYS = ("O31a", "O31b", "O31c")


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
    start = next(i for i, ln in enumerate(lines) if ln.startswith(HEADINGS[table]))
    out = []
    for ln in lines[start + 1 :]:
        if ln.startswith("#### ") or ln.startswith("## ") or ln.startswith("### "):
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


def _plain(cell: str) -> str:
    """A cell without the backticks the plan wraps code in."""
    return cell.replace("`", "")


def _ref_text(record: dict) -> str:
    """What the reference page shows for a row of the reference fixture."""
    return record["ref_text"] if record.get("unchanged") else record["new_text"]


def _copy_a_rows(plan: str, ref_by_key: dict) -> list[dict]:
    out = []
    for table in ("A", "X"):
        for cells in _table_rows(_section_lines(plan, table), table):
            record = dict(zip(COLUMNS[table], (_unquote(c) for c in cells), strict=True))
            key = record["key"]
            if table == "A" and key == "none at ref":
                continue  # the plan's note about sections that carry no description
            record["table"] = table
            record["host_place"] = _plain(record["host_place"])
            disposition = record["disposition"]
            # kept, shortened, removed, moved or reworded: the first word of the disposition.
            record["kind"] = disposition.split()[0].rstrip(":;,")
            text = record["new_text"]
            record["new_text_note"] = None
            record["unchanged"] = False
            record["ref_text"] = None
            if text == "none":
                record["new_text"] = None
            elif text == "(unchanged)":
                record["unchanged"] = True
                record["new_text"] = None
                if table == "A":
                    found = ref_by_key.get(key)
                    if found is None:
                        raise ExtractionError(f"{key}: unchanged, but the reference holds no row")
                    record["ref_text"] = _ref_text(found)
            elif text.startswith("per AC-") or text.startswith("label "):
                record["new_text_note"] = text
                record["new_text"] = None
            record["slots"] = _slots(record["new_text"])
            out.append(record)
    return out


def _values(plan: str, rows: list[dict], ref_values: list[dict]) -> list[dict]:
    changed: list[dict] = []
    for cells in _table_rows(_section_lines(plan, "V"), "V"):
        record = dict(zip(COLUMNS["V"], (_unquote(c) for c in cells), strict=True))
        slot = record["slot"]
        row = next((r for r in rows if r["key"] == record["copy_row"]), None)
        if row is None:
            raise ExtractionError(f"Values table: {record['copy_row']!r} is in no copy table")
        if slot not in row["slots"]:
            raise ExtractionError(f"Values table: {record['copy_row']} holds no slot {{{slot}}}")
        if record["phrase"] not in row["new_text"]:
            raise ExtractionError(
                f"Values table: the phrase for {record['copy_row']} is not inside its new text"
            )
        previous = next(
            (v for v in ref_values if v["slot"] == slot and v["copy_row"] == record["copy_row"]),
            None,
        )
        if previous is None:
            raise ExtractionError(f"Values table: the reference has no {slot} row for the copy row")
        merged = dict(previous)
        merged["phrase"] = record["phrase"]
        merged["fallback"] = record["fallback"]
        merged["authority_now"] = _plain(record["authority"])
        merged["table"] = row["table"]
        changed.append(merged)
    keep = [
        v
        for v in ref_values
        if not any(c["slot"] == v["slot"] and c["copy_row"] == v["copy_row"] for c in changed)
    ]
    return [*keep, *changed]


def _guardrail_lines(ref_template: str) -> dict[str, str]:
    """O31a to O31c from the reference's guardrail paragraph (the rule that drops a term's own name and a following "is", applied here)."""
    match = re.search(
        r'<p data-testid="guardrail-uniform-note"[^>]*>(?P<body>.*?)</p>', ref_template, re.S
    )
    if not match:
        raise ExtractionError("the reference template holds no guardrail-uniform-note paragraph")
    body = " ".join(re.sub(r"<[^>]+>", " ", match["body"]).split())
    sentences = re.split(r"(?<=\.)\s+(?=[A-Z])", body)
    if len(sentences) != 4:
        raise ExtractionError(f"the guardrail paragraph has {len(sentences)} sentences, not 4")
    lines = {}
    for key, name, sentence in zip(GUARDRAIL_KEYS, GUARDRAIL_NAMES, sentences, strict=False):
        if not sentence.startswith(name + " "):
            raise ExtractionError(f"{key}: the sentence does not start with {name!r}")
        rest = sentence[len(name) + 1 :]
        rest = re.sub(r"^(is|are) ", "", rest)
        lines[key] = f"{name}: {rest}"
    return lines


def _expand_keys(cell: str) -> list[str]:
    keys: list[str] = []
    for part in re.split(r"[;,]\s*", cell):
        part = part.strip()
        if not part:
            continue
        span = re.fullmatch(r"([A-Z]+\d+)([a-z]) to \1([a-z])", part)
        if span:
            first, last = span.group(2), span.group(3)
            keys += [span.group(1) + chr(c) for c in range(ord(first), ord(last) + 1)]
        elif " to " in part:
            raise ExtractionError(f"cannot expand the Glossary key range {part!r}")
        else:
            keys.append(part)
    return keys


def _glossary(plan: str, ref_by_key_b: dict, ref_template: str) -> tuple[dict, list[dict]]:
    guardrail = _guardrail_lines(ref_template)
    g2: dict[str, dict] = {}
    for cells in _table_rows(_section_lines(plan, "G2"), "G2"):
        record = dict(zip(COLUMNS["G2"], (_unquote(c) for c in cells), strict=True))
        text = record["text"]
        if record["key"] in GUARDRAIL_KEYS:
            text = guardrail[record["key"]]
            # The plan quotes the start of the line; the line must start the same way.
            quoted = re.match(r'^"([^"]+)"', record["text"])
            if not quoted or not text.startswith(quoted.group(1)):
                raise ExtractionError(f"{record['key']}: the paragraph does not start as G2 says")
        record["text"] = text
        g2[record["key"]] = record
    glossary: dict[str, list[dict]] = {}
    for cells in _table_rows(_section_lines(plan, "G"), "G"):
        host, lines = (_plain(c) for c in cells)
        entries = []
        for token in [t.strip() for t in lines.split(",")]:
            starred = token.endswith("*")
            key = token.rstrip("*")
            if starred:
                if key not in g2:
                    raise ExtractionError(f"Table G: {key}* is in no Table G2 row")
                text, source = g2[key]["text"], "G2"
            else:
                found = ref_by_key_b.get(key)
                if found is None:
                    raise ExtractionError(f"Table G: {key} is a row of no reference table B")
                text, source = found["new_text"], "ref"
            entries.append(
                {
                    "key": key,
                    "text": text,
                    # The text before the first ": "; a line with none has no lead term, and a
                    # label reaches it through Table G3 alone.
                    "lead_term": text.split(": ", 1)[0] if ": " in text else None,
                    "slots": _slots(text),
                    "source": source,
                }
            )
        glossary[host] = entries
    titles = []
    for cells in _table_rows(_section_lines(plan, "G3"), "G3"):
        record = dict(zip(COLUMNS["G3"], (_unquote(c) for c in cells), strict=True))
        record["element"] = _plain(record["element"])
        record["host"] = _plain(record["host"])
        record["line_keys"] = _expand_keys(record["glossary_line"])
        titles.append(record)
    return glossary, titles


def extract(plan_sha: str) -> dict:
    plan = _git_show(plan_sha, PLAN_PATH)
    ref_doc = json.loads(_git_show(REFERENCE_COMMIT, REF_FIXTURE_PATH))
    ref_rows = ref_doc["rows"]
    ref_by_key = {r["key"]: r for r in ref_rows if r["table"] == "A"}
    ref_by_key_b = {r["key"]: r for r in ref_rows if r["table"] == "B"}
    rows = _copy_a_rows(plan, ref_by_key)
    seen: set[tuple[str, str]] = set()
    for record in rows:
        pair = (record["table"], record["key"])
        if pair in seen:
            raise ExtractionError(f"table {record['table']}: key {record['key']!r} appears twice")
        seen.add(pair)
    glossary, titles = _glossary(plan, ref_by_key_b, _git_show(REFERENCE_COMMIT, REF_TEMPLATE_PATH))
    return {
        "plan_path": PLAN_PATH,
        "plan_sha": plan_sha,
        "reference_commit": REFERENCE_COMMIT,
        "rows": rows,
        "glossary": glossary,
        "titles": titles,
        "values": _values(plan, rows, ref_doc["values"]),
        "ref_rows": ref_rows,
        "ref_values": ref_doc["values"],
    }


def _canonical(document: dict) -> str:
    return json.dumps(document, indent=1, sort_keys=False, ensure_ascii=False) + "\n"


def _resolve(sha: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", sha], capture_output=True, text=True, check=True
    ).stdout.strip()


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "--check":
        fresh = extract(_resolve(argv[2]))
        stored = json.loads(OUT.read_text(encoding="utf-8"))
        fresh_cmp = {k: v for k, v in fresh.items() if k != "plan_sha"}
        stored_cmp = {k: v for k, v in stored.items() if k != "plan_sha"}
        if fresh_cmp == stored_cmp:
            print(f"copy.json equals the plan at {argv[2]}")
            return 0
        for section in ("rows", "titles"):
            fresh_rows = {r["key"]: r for r in fresh[section]}
            stored_rows = {r["key"]: r for r in stored[section]}
            for key in sorted(set(fresh_rows) | set(stored_rows)):
                if fresh_rows.get(key) != stored_rows.get(key):
                    print(f"DIFFERS: {section} {key}")
        for host in sorted(set(fresh["glossary"]) | set(stored["glossary"])):
            if fresh["glossary"].get(host) != stored["glossary"].get(host):
                print(f"DIFFERS: glossary {host}")
        for section in ("values", "ref_rows", "ref_values"):
            if fresh[section] != stored[section]:
                print(f"DIFFERS: {section}")
        return 1
    sha = _resolve(argv[1] if len(argv) == 2 else PLAN_COMMIT)
    document = extract(sha)
    with open(OUT, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical(document))
    lines = sum(len(v) for v in document["glossary"].values())
    print(
        f"wrote {OUT.name}: {len(document['rows'])} rows, {lines} Glossary lines in "
        f"{len(document['glossary'])} hosts, {len(document['titles'])} title rows, "
        f"{len(document['values'])} slot rows at {sha}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
