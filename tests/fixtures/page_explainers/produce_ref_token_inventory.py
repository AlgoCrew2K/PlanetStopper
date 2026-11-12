"""Write ``ref_token_inventory.json``: the attribute, id and class tokens the five pages carried at the reference.

TEST-ONLY. Run from a checkout whose history holds the reference commit:

    python tests/fixtures/page_explainers/produce_ref_token_inventory.py

The tokens of every file the change touches are read with ``git show 1988ce5f:<path>`` (the only
source: nothing is typed here, and the working tree is never read). The checks never call git, so a
depth-one clone can run them. A token is one of:

* ``testid:<value>``: a literal ``data-testid`` value;
* ``data:<name>=<value>``: any other literal ``data-*`` attribute with a value holding no template
  expression;
* ``id:<value>``: a literal ``id`` value;
* ``class:<token>``: one CSS class of a literal ``class`` attribute (a template expression in the
  attribute is dropped, the literal classes beside it are kept);
* ``name:<key>``: one of the contract names the plan pins (``blocked_uncertainty``,
  ``_verdict_text``, ``exit_reason``).
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[3]
OUT = pathlib.Path(__file__).with_name("ref_token_inventory.json")
REFERENCE_COMMIT = "c0760e70"

FILES = (
    "templates/_chrome.html",
    "templates/ai_advisor.html",
    "templates/history.html",
    "templates/index.html",
    "templates/performance.html",
    "templates/settings.html",
    "static/ai_advisor.js",
    "static/ai_advisor_asset_swaps.js",
    "static/ai_advisor_logic_changes.js",
    "static/chrome.js",
    "static/history.js",
    "static/index.js",
    "static/performance.js",
    "static/settings-modal.js",
    "static/settings.js",
)
CONTRACT_NAMES = ("blocked_uncertainty", "_verdict_text", "exit_reason")
# Three controls an earlier change removed: none is in the markup at this reference, so the
# list recorded is empty; the producer still records any such button's tokens apart.
REMOVED_CONTROL_TEXTS = ("View logs", "Go to cash", "Edit variables")
_BUTTON = re.compile(r"<button[\s>][^>]*>?.*?</button>", re.S | re.I)

_ATTR = re.compile(r"""\b(data-[\w-]+|id|class)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_EXPRESSION = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)


def tokens_of(source: str) -> set[str]:
    found: set[str] = set()
    for match in _ATTR.finditer(source):
        name = match.group(1)
        raw = match.group(2) if match.group(2) is not None else match.group(3)
        if name == "class":
            for token in _EXPRESSION.sub(" ", raw).split():
                if re.fullmatch(r"[A-Za-z_][\w-]*", token):
                    found.add(f"class:{token}")
            continue
        if _EXPRESSION.search(raw) or not raw or any(c in raw for c in "{}'\"+\\"):
            continue
        if name == "data-testid":
            found.add(f"testid:{raw}")
        elif name == "id":
            found.add(f"id:{raw}")
        else:
            found.add(f"data:{name}={raw}")
    for key in CONTRACT_NAMES:
        if re.search(rf"(?<![\w-]){re.escape(key)}(?![\w-])", source):
            found.add(f"name:{key}")
    return found


_TITLE = re.compile(r"""(?<![\w-])title\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def titles_of(source: str) -> list[str]:
    """Literal ``title`` attribute values (no template expression, no quote inside)."""
    found = set()
    for match in _TITLE.finditer(source):
        raw = match.group(1) if match.group(1) is not None else match.group(2)
        if raw and not _EXPRESSION.search(raw) and not any(c in raw for c in "{}"):
            found.add(" ".join(raw.split()))
    return sorted(found)


def main() -> None:
    inventory = {}
    titles = {}
    removed: set[str] = set()
    for path in FILES:
        shown = subprocess.run(
            ["git", "show", f"{REFERENCE_COMMIT}:{path}"],
            cwd=REPO,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
        inventory[path] = sorted(tokens_of(shown))
        titles[path] = titles_of(shown)
        for button in _BUTTON.findall(shown):
            if any(text in button for text in REMOVED_CONTROL_TEXTS):
                removed |= tokens_of(button)
    record = {
        "reference_commit": REFERENCE_COMMIT,
        "command": f"git show {REFERENCE_COMMIT}:<path> for each file below, tokens by tokens_of()",
        "removed_control_tokens": sorted(removed),
        "files": inventory,
        "titles": titles,
    }
    OUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print({p: len(t) for p, t in inventory.items()})


if __name__ == "__main__":
    main()
