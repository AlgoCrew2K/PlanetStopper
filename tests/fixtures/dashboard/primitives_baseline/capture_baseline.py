"""Capture the dashboard's primitive structure and resolved rules from the real ``GET /``.

Run on an UNMODIFIED checkout (the script refuses otherwise) to write ``baseline.json`` beside
this file:

    DB_PATH=<scratch>/state.db ATLAS_CACHE_DB_PATH=<scratch>/atlas.db \
        python tests/fixtures/dashboard/primitives_baseline/capture_baseline.py

A later plan that changes a primitive on purpose records the change with ``--rules-only``: only
``resolved`` and ``ordered`` are replaced (``ordered`` is then read from ``static/layout.css``,
where the rules live since the move), the base's ``structure`` and ``base_sha`` are kept, and the
revision is recorded under ``rules_revision``.

``baseline.json`` holds, from the markup and sheets the route served at ``base_sha``:

* ``structure``: ``[[tag, [class, ...]], ...]`` for every element in ``<body>``;
* ``resolved``: ``{"<at-rule>||<selector>": {property: value}}`` for every selector that names a
  dashboard primitive class, resolved over the page's sheets in document order;
* ``ordered``: the rules naming a primitive in the order the base template's inline
  stylesheet wrote them.

The reading code is ``tests/ui/_dashboard_primitives_capture.py``, the same module the
checking test imports. Provenance: ``PROVENANCE.md`` in this directory.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO))


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def revise_rules(resolved: dict, ordered: list) -> None:
    """Replace only ``resolved`` and ``ordered``, for a primitive a later plan changed on purpose.

    ``structure`` and ``base_sha`` stay the base's. The revision is recorded under
    ``rules_revision`` (the head it was taken at, the time, the counts before and after).
    """
    path = HERE / "baseline.json"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    before = recorded["resolved"], recorded["ordered"]
    recorded["resolved"] = resolved
    recorded["ordered"] = ordered
    recorded["rules_revision"] = {
        "head_sha": _git("rev-parse", "HEAD"),
        "captured_at_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "resolved_before": len(before[0]),
        "resolved_after": len(resolved),
        "ordered_before": len(before[1]),
        "ordered_after": len(ordered),
    }
    path.write_text(json.dumps(recorded, indent=1) + "\n", encoding="utf-8")
    print(
        f"rewrote resolved ({len(before[0])} -> {len(resolved)}) and ordered "
        f"({len(before[1])} -> {len(ordered)}); structure and base_sha kept"
    )


def main() -> None:
    watched = [
        "templates/index.html",
        "static/layout.css",
        "static/tokens.css",
        "static/tweaks.css",
    ]
    dirty = _git("status", "--porcelain", "--", *watched)
    if dirty:
        raise SystemExit(f"refusing to capture: the base files are modified:\n{dirty}")

    scratch = pathlib.Path(tempfile.mkdtemp(prefix="primitives_baseline_"))
    os.environ.setdefault("DB_PATH", str(scratch / "state.db"))
    os.environ.setdefault("ATLAS_CACHE_DB_PATH", str(scratch / "atlas.db"))

    import database

    database.init_db()
    database.run_migrations()

    from tests.ui import _dashboard_primitives_capture as cap

    html = cap.render_dashboard_html()
    structure = cap.body_structure(html)
    resolved = cap.resolved_primitive_rules(html)

    # The route must answer identically twice, or a later comparison proves nothing.
    second = cap.render_dashboard_html()
    if cap.body_structure(second) != structure:
        raise SystemExit("the route's element structure is not stable across two renders")

    if "--rules-only" in sys.argv[1:]:
        layout = (REPO / "static" / "layout.css").read_text(encoding="utf-8")
        revise_rules(resolved, cap.ordered_primitive_rules(layout))
        return

    template = (REPO / "templates" / "index.html").read_text(encoding="utf-8")
    import re

    inline = "\n".join(
        re.findall(r"<style\b[^>]*>(.*?)</style>", template, flags=re.IGNORECASE | re.DOTALL)
    )
    ordered = cap.ordered_primitive_rules(inline)

    missing = [c for c in cap.PRIMITIVE_CLASSES if cap.tags_with_class(structure, c) == 0]
    out = {
        "base_sha": _git("rev-parse", "HEAD"),
        "captured_at_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "primitive_classes_absent_from_structure": missing,
        "structure": structure,
        "resolved": resolved,
        "ordered": ordered,
    }
    (HERE / "baseline.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(
        f"wrote baseline.json at {out['base_sha']}: {len(structure)} body elements, "
        f"{len(resolved)} resolved selectors, {len(ordered)} ordered rules; "
        f"classes absent from the structure: {missing}"
    )


if __name__ == "__main__":
    main()
