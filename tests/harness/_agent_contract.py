"""Shared scaffolding for the agent-definition contract tests.

Three test modules (spec-writer, spec-auditor, architect) check compiled agent files that
share one shape: YAML-style frontmatter, compiler header comments, the global template body,
then a ``## Project bindings (...)`` section this repository owns. This module holds the parts
that were duplicated across them: frontmatter parsing, bindings slicing, template-body
comparison, the pins record, the flat-layout rule, the prose and history scanners, the
checklist identity checks, and the mutation helpers.

Every check stays an explicit pure function returning a list of problems (empty means the rule
holds). The test modules call these and keep their own agent-specific rules (tools, anchors).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

HEADING_RE = re.compile(r"^## Project bindings \([^)]*\)[ \t\r]*$", re.MULTILINE)

# Header comments the compiler writes between the frontmatter and the template body.
COMPILER_HEADER_RE = re.compile(
    r"^<!-- (?:compiled-from: |project bindings below |template-owned )[^\n]*-->[ \t]*$"
)
PINS_RECORD_RE = re.compile(r"<!-- template-owned pins: ([^>]*) -->")

# sha256 of the spec-review checklist, computed from the LF-normalized committed content.
CHECKLIST_SHA256 = "b3756604ab28e9d46c7d18b7ceab6fdb4da0deb5bbfa0b863c2a87083d7be10c"

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

# Text that carries history or identity instead of a rule and its reason. Each pattern has at
# least one sample in HISTORY_SAMPLES; the mutation controls feed those samples to the scanner.
HISTORY_PATTERNS: dict[str, re.Pattern[str]] = {
    "iso-date": re.compile(r"\b(?:19|20)\d{2}-\d{2}(?:-\d{2})?\b"),
    "slash-date": re.compile(r"\b\d{1,4}/\d{1,2}/\d{1,4}\b"),
    "month-day-date": re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b"),
    "day-month-date": re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\b"),
    "month-year-date": re.compile(rf"\b{_MONTH}\.?,?\s+(?:19|20)\d{{2}}\b"),
    "quarter-year": re.compile(
        r"\bQ[1-4][ \-/']*(?:(?:19|20)\d{2}|\d{2})\b|\b(?:19|20)\d{2}[ \-/]*Q[1-4]\b"
    ),
    "letters-digits-id": re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+\b"),
    "id-family-prefix": re.compile(r"\b(?:DE|ADR|ARCH|MA|BL|F|QA)-[A-Z0-9]+(?:-[A-Z0-9]+)*\b"),
    "ac-id": re.compile(r"\bAC[ -]?\d+[a-z]?\b"),
    "pr-reference": re.compile(r"\bPR\s?#?\d+\b|(?<![\w&])#\d+\b|\bpull request #?\d+\b", re.I),
    "attribution-phrase": re.compile(
        r"operator (?:ruling|directive|verbatim)|ruled by|per the operator|"
        r"was approved|approved:|incident|postmortem|post-mortem",
        re.I,
    ),
    "history-wording": re.compile(
        r"\b(?:previously|formerly|used to|no longer|was removed|was fixed)\b", re.I
    ),
    "machine-local-path": re.compile(r"\b[A-Za-z]:[\\/]|/Users/|/home/\w+|~/\.claude"),
    "person-name": re.compile(r"\bpaulm\b|\bpaul\b", re.I),
}

HISTORY_SAMPLES: dict[str, list[str]] = {
    "iso-date": ["Decided on 2026-01-02.", "Recorded in 2026-06."],
    "slash-date": ["Decided on 2026/01/02.", "Decided on 01/02/2026."],
    "month-day-date": ["Recorded on June 11.", "Recorded on Jun 11th."],
    "day-month-date": ["Recorded on 11 June 2026.", "Recorded on 3rd March."],
    "month-year-date": ["Recorded in Jun 2026.", "Recorded in September, 2026."],
    "quarter-year": ["Planned for Q3 2026.", "Planned for 2026 Q3.", "Planned for Q3-26."],
    "letters-digits-id": [
        "See ARCH-002 for this.",
        "See DE-RETIRE-001.",
        "See F-008.",
        "See QA-3.",
    ],
    "id-family-prefix": ["See DE-EXAMPLE for this.", "See ADR-LOCAL for this."],
    "ac-id": ["Covered by AC-7.", "Covered by AC 7.", "Covered by AC7."],
    "pr-reference": ["Added in PR 140.", "Added in PR #140.", "Added in #140.", "Added in PR#140."],
    "attribution-phrase": ["Per the operator, never do this.", "Added after an incident."],
    "history-wording": ["Previously the route raised.", "This was fixed later."],
    "machine-local-path": [
        "Stored at C:\\work\\repo.",
        "Stored at /Users/someone/f.",
        "~/.claude/x",
    ],
    "person-name": ["Owner is paulm.", "Asked for by Paul."],
}

PROSE_PATTERNS: dict[str, re.Pattern[str]] = {
    "em-dash": re.compile("\u2014"),
    "en-dash": re.compile("\u2013"),
    "spaced-hyphen": re.compile(r"(?<=\S)[ \t]-[ \t](?=\S)"),
}
PROSE_SAMPLES: dict[str, str] = {
    "em-dash": "a dash \u2014 here",
    "en-dash": "a range 1\u20132",
    "spaced-hyphen": "a spaced - hyphen",
}

PLACEHOLDER_RE = re.compile(r"<[A-Za-z][^<>\n]*>|\b(?:TODO|TBD|FIXME|XXX)\b")

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*])|(?<=[.!?]\*\*)\s+(?=[A-Z`*])")


# ---------------------------------------------------------------------------
# Reading and slicing
# ---------------------------------------------------------------------------


def lf(text: str) -> str:
    """Normalize line endings so a CRLF working copy compares equal to the LF blob."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_lf(path: Path) -> str:
    return lf(path.read_text(encoding="utf-8"))


def read_lf_if_present(path: Path) -> str | None:
    return read_lf(path) if path.is_file() else None


def split_frontmatter(text: str) -> tuple[dict[str, str | list[str]], str]:
    """Return (top-level frontmatter keys, body). ``key: value`` gives a string; a key with no
    value takes the ``- item`` lines under it as a list; other indented lines are nested
    mappings and are not top-level keys. Raises ValueError on a malformed fence or line."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("file does not open with a frontmatter fence")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("frontmatter fence never closes")
    fields: dict[str, str | list[str]] = {}
    current: str | None = None
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        stripped = raw.strip()
        if raw[0] in " \t" or stripped.startswith("- "):
            if stripped.startswith("- "):
                if current is None or not isinstance(fields.get(current), list):
                    raise ValueError(f"list item without a list key: {raw!r}")
                fields[current].append(stripped[2:].strip())  # type: ignore[union-attr]
            continue
        key, sep, value = raw.partition(":")
        if not sep:
            raise ValueError(f"frontmatter line without a colon: {raw!r}")
        current = key.strip()
        fields[current] = value.strip().strip('"') if value.strip() else []
    return fields, "\n".join(lines[end + 1 :])


def frontmatter_text(text: str) -> str:
    """The raw lines between the fences, or empty when the fences are missing."""
    try:
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return ""
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return ""
    return "\n".join(lines[1:end])


def bindings_section(text: str) -> str:
    """Text from the last ``## Project bindings (...)`` heading to the end, or empty."""
    matches = list(HEADING_RE.finditer(text))
    return text[matches[-1].start() :] if matches else ""


def without_bindings_phrase(text: str, phrase: str) -> str:
    """Remove every occurrence of ``phrase`` from the bindings section only."""
    start = len(text) - len(bindings_section(text))
    return text[:start] + text[start:].replace(phrase, "REMOVED")


def without_bindings_sentence(text: str, sentence: str) -> str:
    """Delete one sentence from the bindings section only."""
    start = len(text) - len(bindings_section(text))
    return text[:start] + text[start:].replace(sentence, "", 1)


def binding_sentences(text: str) -> list[str]:
    """Every sentence of the bindings section, bullet markers removed."""
    sentences: list[str] = []
    for line in bindings_section(text).splitlines()[1:]:
        content = re.sub(r"^\s*-\s+", "", line).strip()
        if content:
            sentences += [s.strip() for s in _SENTENCE_BOUNDARY.split(content) if s.strip()]
    return sentences


def compiled_template_body(text: str) -> str | None:
    """The template body inside a compiled file: after the frontmatter and the compiler's
    header comments, up to the bindings heading. None when no bindings heading exists."""
    match = HEADING_RE.search(text)
    if match is None:
        return None
    try:
        _, body = split_frontmatter(text[: match.start()] + "\n")
    except ValueError:
        return None
    lines = body.split("\n")
    first = 0
    while first < len(lines) and (
        not lines[first].strip() or COMPILER_HEADER_RE.match(lines[first])
    ):
        first += 1
    return "\n".join(lines[first:]).strip()


def without_template_body(text: str) -> str:
    """The file with the template body cut out and everything else kept."""
    body = compiled_template_body(text)
    return text if not body else text.replace(body, "", 1)


def bullet_of(section: str, label: str) -> str:
    """The bindings bullet that opens with ``- **label``, up to the next top-level bullet."""
    start = section.find(f"- **{label}")
    if start < 0:
        return ""
    end = section.find("\n- **", start + 1)
    return section[start:] if end < 0 else section[start:end]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_template_body_present(text: str, h1: str, directive_marker: str) -> list[str]:
    """CI-safe presence check: the compiled body opens with the template's title and carries its
    Prime Directive. The compiler's own check never verifies body presence."""
    body = compiled_template_body(text)
    if body is None:
        return ["bindings heading missing, so the template body cannot be located"]
    problems = []
    if not body.startswith(h1):
        problems.append(f"template body does not open with {h1!r}")
    if directive_marker not in body:
        problems.append(f"template body lacks {directive_marker!r}")
    return problems


def check_template_body_matches(compiled_text: str, template_text: str) -> list[str]:
    """The compiled file's template body equals the global template body, frontmatter stripped
    and line endings normalized."""
    body = compiled_template_body(lf(compiled_text))
    if body is None:
        return ["bindings heading missing, so the template body cannot be located"]
    try:
        _, template_body = split_frontmatter(lf(template_text))
    except ValueError as exc:
        return [f"template does not parse: {exc}"]
    if body != template_body.strip():
        return [
            f"compiled template body ({len(body)} chars) differs from the global template "
            f"body ({len(template_body.strip())} chars)"
        ]
    return []


def check_pins_record(text: str) -> list[str]:
    """The compiler's template-owned pins record names both model@ and effort@."""
    record = PINS_RECORD_RE.search(text)
    if record is None:
        return ["template-owned pins record missing"]
    return [
        f"{pin.rstrip('@')} is not recorded as template-owned"
        for pin in ("model@", "effort@")
        if pin not in record.group(1)
    ]


def check_flat_layout(agent_file: Path, nested: Path) -> list[str]:
    problems = []
    if not agent_file.is_file():
        problems.append("flat agent file missing")
    if agent_file.parent.name != "agents":
        problems.append(f"agent file sits in {agent_file.parent.name!r}, not an agents folder")
    if nested.exists():
        problems.append("nested AGENT.md layout present; the registry cannot dispatch it")
    return problems


def scan_scope(text: str) -> str:
    """Frontmatter plus bindings: the text this repository owns."""
    return frontmatter_text(text) + "\n" + bindings_section(text)


def check_no_history_or_machine_text(text: str) -> list[str]:
    if not bindings_section(text):
        return ["bindings section missing"]
    scope = scan_scope(text)
    return [
        f"{name}: {m.group(0)!r}"
        for name, pattern in HISTORY_PATTERNS.items()
        for m in pattern.finditer(scope)
    ]


def check_prose_rules(text: str) -> list[str]:
    section = bindings_section(text)
    if not section:
        return ["bindings section missing"]
    return [
        f"{name}: {m.group(0)!r}"
        for name, pattern in PROSE_PATTERNS.items()
        for m in pattern.finditer(section)
    ]


def check_no_placeholder(text: str) -> list[str]:
    section = bindings_section(text)
    if not section:
        return ["bindings section missing"]
    return [f"placeholder {m!r}" for m in PLACEHOLDER_RE.findall(frontmatter_text(text) + section)]


def check_rule_anchors(text: str, anchors: dict[str, str]) -> list[str]:
    """Rule ids whose anchor phrase is absent from the bindings section."""
    section = bindings_section(text)
    if not section:
        return ["bindings section missing"]
    return [rule for rule, anchor in anchors.items() if anchor not in section]


def check_anchor_table(text: str, anchors: dict[str, str]) -> list[str]:
    """Structural soundness of an anchor table against the real bindings: each anchor occurs
    exactly once (so a duplicate sentence cannot hide a deletion) and no anchor is a substring
    of another (so one removal reports exactly one rule)."""
    section = bindings_section(text)
    problems = [
        f"anchor for {rule!r} occurs {section.count(anchor)} times"
        for rule, anchor in anchors.items()
        if section.count(anchor) != 1
    ]
    problems += [
        f"anchor for {a!r} is inside the anchor for {b!r}"
        for a, anchor_a in anchors.items()
        for b, anchor_b in anchors.items()
        if a != b and anchor_a in anchor_b
    ]
    return problems


def check_checklist_pin(checklist_text: str | None) -> list[str]:
    """The project checklist copy equals the content pinned in this module."""
    if checklist_text is None:
        return ["project checklist copy missing"]
    digest = hashlib.sha256(lf(checklist_text).encode("utf-8")).hexdigest()
    if digest != CHECKLIST_SHA256:
        return [f"project checklist sha256 {digest} != pinned {CHECKLIST_SHA256}"]
    return []


def check_checklist_matches_global(checklist_text: str | None, global_text: str) -> list[str]:
    """The project checklist copy equals the global checklist, line endings normalized."""
    if checklist_text is None:
        return ["project checklist copy missing"]
    if lf(checklist_text) != lf(global_text):
        return ["project checklist copy differs from the global checklist"]
    return []
