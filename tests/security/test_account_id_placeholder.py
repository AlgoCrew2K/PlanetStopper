"""No tracked file holds the operator's Composer account id.

The id is never written here and never read from a file: the family checks accept a
UUID-shaped token only when it is the nil UUID or is listed as invented in a
PROVENANCE.md, so a real id fails them without this module knowing it. The checks that
need the reference commit live in ``tests/fixtures/hygiene_waves_3_4/`` as a local script,
because CI clones at depth one.

Every failure names a file and a field path and nothing else. Values, including
dictionary keys that are account ids, are masked, and no check puts a fixture value into
an ``assert`` expression, because pytest would print it.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

PLACEHOLDER = "ACCOUNT_ID_REDACTED"
NIL_UUID = "00000000-0000-0000-0000-000000000000"
REPO = Path(__file__).resolve().parents[2]

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_URL_ACCOUNT_RE = re.compile(r"/accounts/([0-9A-Za-z_{}.:+=%-]+)")
_SHA256_RE = re.compile(r"\b[0-9a-f]{64}\b")
_ROW_FILE_RE = re.compile(r"`([^`\s|]+\.(?:json|gz|jsonl|md|js|py|txt))`")
_MAX_LISTED = 25

# Every tracked file that held the id, or only its first 8 characters, before the
# redaction (paths only, counted at the reference commit).
REDACTION_SCOPE = (
    ".design-handoff/project/mock-data.js",
    "DECISIONS.md",
    "docs/handoff/RECONCILE-2026-05-20.md",
    "docs/handoff/RECONCILE-final-2026-05-20.md",
    "docs/handoff/RECONCILE-pass4-2026-05-20.md",
    "docs/handoff/V3-AUDIT.md",
    "docs/handoff/VERIFY-behavioral-FINAL-2026-05-20T20-30-00Z.md",
    "docs/handoff/VERIFY-behavioral-final-2026-05-20T16-47-00Z.md",
    "docs/handoff/cycle-1-contract.md",
    "docs/research/claude-integration/composer-symphony-logic-endpoint.md",
    "docs/research/dashboard/actual-return-diagnosis.md",
    "docs/research/dashboard/composer-per-symphony-stats.md",
    "tests/advisors/test_composer_series_client.py",
    "tests/fixtures/composer/v3-audit/dashboard_api_state.json",
    "tests/fixtures/prod_droplet/bot_state_positions.json",
    "tests/fixtures/prod_droplet/exit_triggers.json",
    "tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-06-22.json",
    "tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-06-23.json",
    "tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-06-24.json",
    "tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-06-25.json",
    "tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-06-29.json",
    "tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-06-30.json",
    "tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-07-01.json",
    "tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-07-02.json",
    "tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-07-06.json",
    "tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-07-07.json",
    "tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-07-08.json",
    "tests/fixtures/prod_droplet/shadow_last_rows.json",
    "tests/fixtures/prod_droplet/shadow_postclose_rows.json",
    "tests/fixtures/prod_droplet/shadow_snapshot_rows.json",
    "tests/fixtures/refresh_timing/01-c1/PROVENANCE.md",
    "tests/fixtures/refresh_timing/01-c1/dict_without_name_bot_state_row.json.gz",
    "tests/fixtures/refresh_timing/01-c1/flat_bot_state_row.json.gz",
    "tests/fixtures/refresh_timing/01-c1/golden_ref_eod_snapshot.json",
    "tests/fixtures/refresh_timing/01-c1/nested_bot_state_row.json.gz",
    "tests/fixtures/refresh_timing/01-c1/no_snapshot_bot_state_row.json.gz",
    "tests/fixtures/refresh_timing/01-c1/symphony_without_account_bot_state_row.json.gz",
    "tests/fixtures/refresh_timing/01-c2/exit_trigger_row.json",
    "tests/fixtures/refresh_timing/01-c2/golden_ref_api_state_closed_frozen.json",
    "tests/fixtures/refresh_timing/01-c2/golden_ref_api_state_open.json",
    "tests/fixtures/refresh_timing/01-c2/golden_ref_api_state_pre_market.json",
    "tests/fixtures/refresh_timing/01-c2/live_state_two_cycles.json",
    "tests/fixtures/refresh_timing/01-c2/shadow_rows_cycle_m_moved.json",
    "tests/fixtures/refresh_timing/01-c2/shadow_rows_two_cycles.json",
    "tests/fixtures/refresh_timing/01-c2/snapshot_two_symphonies.json",
    "tests/fixtures/refresh_timing/02-c3/bot_state_entry_iaSOOUsmnCJHiZvbrWfs.json",
    "tests/fixtures/refresh_timing/02-c4/derived_records.json",
    "tests/fixtures/refresh_timing/02-c4/exit_trigger_row.json",
    "tests/fixtures/refresh_timing/02-c4/shadow_post_trigger_row.json",
    "tests/fixtures/refresh_timing/02-c4/shadow_trajectory_rows.json",
    "tests/fixtures/refresh_timing/02-c4/symphony_state_entry.json",
    "tests/fixtures/refresh_timing/02-c5/symphony_state_entry.json",
    "tests/fixtures/settings/derived_name_records.json",
    "tests/fixtures/state_poll_no_table/golden_ref_closed_frozen_two_accounts.json",
    "tests/fixtures/state_poll_no_table/golden_ref_closed_guard_alpha_fail.json",
    "tests/fixtures/state_poll_no_table/golden_ref_closed_missing_defaults.json",
    "tests/fixtures/state_poll_no_table/golden_ref_closed_no_longer_held.json",
    "tests/fixtures/state_poll_no_table/golden_ref_closed_no_snapshot_two_accounts.json",
    "tests/fixtures/state_poll_no_table/golden_ref_closed_null_defaults.json",
    "tests/fixtures/state_poll_no_table/golden_ref_closed_unnamed_entry.json",
    "tests/fixtures/state_poll_no_table/golden_ref_held_view.json",
    "tests/fixtures/state_poll_no_table/golden_ref_open_analytics_fail.json",
    "tests/fixtures/state_poll_no_table/golden_ref_open_panels_fail.json",
    "tests/fixtures/state_poll_no_table/golden_ref_open_prev_return_cases.json",
    "tests/fixtures/state_poll_no_table/golden_ref_open_triggers_fail.json",
    "tests/fixtures/state_poll_no_table/golden_ref_open_two_accounts.json",
    "tests/fixtures/state_poll_no_table/golden_ref_open_two_accounts__account_first.json",
    "tests/fixtures/state_poll_no_table/golden_ref_open_with_snapshot_key.json",
    "tests/fixtures/state_poll_no_table/golden_ref_pre_market.json",
    "tests/fixtures/state_poll_no_table/golden_ref_stalled_open.json",
    "tests/fixtures/state_poll_no_table/live_state_prev_return_cases.json",
    "tests/fixtures/state_poll_no_table/live_state_two_accounts.json",
    "tests/fixtures/state_poll_no_table/snapshot_missing_defaults.json",
    "tests/fixtures/state_poll_no_table/snapshot_null_defaults.json",
    "tests/fixtures/state_poll_no_table/snapshot_two_accounts.json",
    "tests/fixtures/state_poll_no_table/snapshot_unnamed_entry.json",
    "tests/ui/test_cycle_1_foundation.py",
    "tests/ui/test_static_js_page_harness.py",
)

# Files written with sorted keys whose `accounts_map` keeps the id as a key beside a
# second account key, so the redacted key moves to where sorting places it.
SORTED_KEY_RECORDS = tuple(
    f"tests/fixtures/state_poll_no_table/{name}.json"
    for name in (
        "snapshot_two_accounts",
        "snapshot_missing_defaults",
        "snapshot_null_defaults",
        "snapshot_unnamed_entry",
        "golden_ref_closed_frozen_two_accounts",
        "golden_ref_closed_guard_alpha_fail",
        "golden_ref_closed_missing_defaults",
        "golden_ref_closed_null_defaults",
        "golden_ref_closed_unnamed_entry",
    )
)

CONSUMER_TEST_SOURCES = (
    "tests/advisors/test_composer_series_client.py",
    "tests/ui/test_static_js_page_harness.py",
    "tests/ui/test_cycle_1_foundation.py",
)

FAMILIES = ("account", "account_id", "accounts_map_key", "url_segment")


def _mask(text: str) -> str:
    return _UUID_RE.sub("<uuid>", text)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in _UUID_RE.findall(text))


@lru_cache(maxsize=1)
def _tracked() -> tuple[str, ...]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode("utf-8")
    return tuple(p for p in out.split("\0") if p and (REPO / p).is_file())


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _text(rel: str) -> str:
    data = (REPO / rel).read_bytes()
    if rel.endswith(".gz"):
        data = gzip.decompress(data)
    return data.decode("utf-8", errors="replace")


# --- the registry of invented ids --------------------------------------------------


def _provenance_blocks(text: str) -> Iterator[str]:
    for paragraph in text.replace("\r\n", "\n").split("\n\n"):
        if paragraph.lstrip().startswith("|"):
            yield from paragraph.split("\n")
        else:
            yield paragraph


@lru_cache(maxsize=1)
def _invented_registry() -> frozenset[str]:
    """UUIDs that a fixtures PROVENANCE.md names inside a block that says "invented"."""
    found: set[str] = set()
    for rel in _tracked():
        if not (rel.startswith("tests/fixtures/") and rel.endswith("PROVENANCE.md")):
            continue
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        for block in _provenance_blocks(text):
            if re.search(r"invent", block, re.IGNORECASE):
                found.update(_tokens(block))
    return frozenset(found)


def _allowed() -> frozenset[str]:
    return _invented_registry() | {NIL_UUID}


# --- the walker --------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    file: str
    family: str
    path: str
    tokens: tuple[str, ...]


def walk_account_fields(doc: object, file: str) -> Iterator[Observation]:
    """Yield every account-bearing field of a parsed JSON document, iteratively.

    The captured bot-state record nests about 265 levels deep, so recursion is unsafe.
    """
    stack: list[tuple[object, tuple[str, ...], bool]] = [(doc, ("$",), False)]
    while stack:
        node, path, is_account_map = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                key_text = str(key)
                if is_account_map:
                    child = (*path, "[*]")
                    yield Observation(file, "accounts_map_key", ".".join(child), _tokens(key_text))
                else:
                    child = (*path, _mask(key_text))
                    if key_text in ("account", "account_id") and isinstance(value, str):
                        yield Observation(file, key_text, ".".join(child), _tokens(value))
                stack.append((value, child, key_text == "accounts_map" and isinstance(value, dict)))
        elif isinstance(node, list):
            stack.extend((item, (*path, "[]"), False) for item in node)
        elif isinstance(node, str):
            for match in _URL_ACCOUNT_RE.finditer(node):
                yield Observation(file, "url_segment", ".".join(path), _tokens(match.group(1)))
            stripped = node.strip()
            if len(stripped) > 1 and stripped[0] in "{[":
                try:
                    embedded = json.loads(stripped)
                except ValueError:
                    continue
                stack.append((embedded, (*path, "(embedded json)"), False))


def offenders(observations: list[Observation], family: str, allowed: frozenset[str]) -> list[str]:
    return sorted(
        {
            f"{obs.file} :: {obs.path}"
            for obs in observations
            if obs.family == family and any(token not in allowed for token in obs.tokens)
        }
    )


@dataclass(frozen=True)
class Scan:
    observations: list[Observation]
    parsed_files: frozenset[str]
    unparseable: dict[str, tuple[str, ...]]


@lru_cache(maxsize=1)
def _scan() -> Scan:
    observations: list[Observation] = []
    parsed: set[str] = set()
    unparseable: dict[str, tuple[str, ...]] = {}
    for rel in _tracked():
        if not rel.endswith((".json", ".json.gz", ".jsonl")):
            continue
        try:
            raw = (REPO / rel).read_bytes()
            if rel.endswith(".gz"):
                raw = gzip.decompress(raw)
            text = raw.decode("utf-8")
            docs = (
                [json.loads(line) for line in text.splitlines() if line.strip()]
                if rel.endswith(".jsonl")
                else [json.loads(text)]
            )
        except (OSError, ValueError):
            unparseable[rel] = _tokens((REPO / rel).read_bytes().decode("utf-8", errors="replace"))
            continue
        parsed.add(rel)
        for doc in docs:
            observations.extend(walk_account_fields(doc, rel))
    return Scan(observations, frozenset(parsed), unparseable)


def _fail_with_list(header: str, items: list[str]) -> None:
    shown = items[:_MAX_LISTED]
    more = f" (+{len(items) - len(shown)} more)" if len(items) > len(shown) else ""
    pytest.fail(f"{header}: {len(items)} field(s){more}:\n" + "\n".join(shown), pytrace=False)


# --- every account-bearing field holds the placeholder or a recorded invented id ---------


@pytest.mark.parametrize("family", FAMILIES)
def test_account_fields_hold_the_placeholder_or_a_recorded_invented_id(family: str):
    bad = offenders(_scan().observations, family, _allowed())
    if bad:
        _fail_with_list(
            f"{family}: a UUID-shaped value is neither {PLACEHOLDER}, the nil UUID, nor listed as "
            "invented in a PROVENANCE.md",
            bad,
        )


def test_an_unparseable_fixture_holds_no_unrecorded_uuid():
    bad = sorted(
        rel
        for rel, tokens in _scan().unparseable.items()
        if any(t not in _allowed() for t in tokens)
    )
    if bad:
        _fail_with_list("an unparseable fixture holds a UUID that is not recorded as invented", bad)


# --- the scan can see: each family is observed, and every gzip record is read ------------


@pytest.mark.parametrize("family", FAMILIES)
def test_the_scan_observes_every_family(family: str):
    seen = sum(1 for obs in _scan().observations if obs.family == family)
    if not seen:
        pytest.fail(f"no tracked fixture field of the {family} family was scanned", pytrace=False)


def test_every_tracked_gzip_record_is_decompressed_and_scanned():
    gz = {rel for rel in _tracked() if rel.endswith(".gz")}
    if not gz:
        pytest.fail(
            "no tracked gzip record found; the scan would hide the five records", pytrace=False
        )
    missing = sorted(gz - _scan().parsed_files)
    if missing:
        _fail_with_list("a gzip record was not parsed by the scan", missing)


_PLANTED = (
    "3f2b8c1d-6a4e-4d97-8b05-1e9c7a2d4f60"  # invented for this test only, never a record value
)


@pytest.mark.parametrize(
    ("family", "doc"),
    [
        ("account", {"entry": {"account": _PLANTED}}),
        ("account_id", [{"row": {"account_id": _PLANTED}}]),
        ("accounts_map_key", {"accounts_map": {_PLANTED: [1]}}),
        ("url_segment", {"url": f"https://host/accounts/{_PLANTED}/symphonies"}),
        ("account_id", {"body": json.dumps({"account_id": _PLANTED})}),
    ],
    ids=["account", "account_id", "accounts_map_key", "url_segment", "embedded_json_body"],
)
def test_a_planted_uuid_is_flagged_in_every_family_and_masked_in_the_report(
    family: str, doc: object
):
    found = list(walk_account_fields(doc, "planted.json"))

    flagged = offenders(found, family, frozenset())
    if len(flagged) != 1:
        pytest.fail(f"the scan did not flag a planted UUID in the {family} family", pytrace=False)
    if _PLANTED in flagged[0] or _PLANTED[:8] in flagged[0]:
        pytest.fail("the report of a flagged field carries the value", pytrace=False)
    if offenders(found, family, frozenset({_PLANTED})):
        pytest.fail(f"a registered invented id was flagged in the {family} family", pytrace=False)


@pytest.mark.parametrize(
    "value",
    [PLACEHOLDER, NIL_UUID, "ACC-1", "acct-001", "", "{account}"],
    ids=lambda v: v or "empty",
)
def test_placeholder_nil_uuid_and_non_uuid_labels_are_not_flagged(value: str):
    doc = {"account": value, "accounts_map": {value: 1}, "u": f"/accounts/{value or 'x'}/y"}
    found = list(walk_account_fields(doc, "labels.json"))

    for family in FAMILIES:
        if offenders(found, family, frozenset({NIL_UUID})):
            pytest.fail(
                f"a harmless account value was flagged in the {family} family", pytrace=False
            )


def test_a_deeply_nested_record_is_walked_without_recursion():
    doc: object = {"account": _PLANTED}
    for _ in range(5000):
        doc = {"next": [doc]}

    found = list(walk_account_fields(doc, "deep.json"))

    if len(offenders(found, "account", frozenset())) != 1:
        pytest.fail("the walker lost the field of a 5000-level nested record", pytrace=False)


# --- the redaction replaced the id with the placeholder, not a deletion ----------------


@pytest.mark.parametrize("rel", REDACTION_SCOPE)
def test_a_redacted_file_holds_the_placeholder(rel: str):
    if rel not in _tracked():
        pytest.fail(f"{rel} is in the redaction scope but is not a tracked file", pytrace=False)
    if PLACEHOLDER not in _text(rel):
        pytest.fail(
            f"{rel} does not hold {PLACEHOLDER}; the id was not replaced by it", pytrace=False
        )


# A UUID-shaped value that keeps the placeholder as a prefix is a hybrid of the real id's shape and
# the placeholder: it is neither the plain placeholder nor an invented UUID recorded as such.
_HYBRID_RE = re.compile(PLACEHOLDER + r"-[0-9a-fA-F]")


def _hybrid_lines(text: str) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1) if _HYBRID_RE.search(line)]


def _plain_or_decompressed(rel: str) -> str:
    data = (REPO / rel).read_bytes()
    if data[:2] == bytes((0x1F, 0x8B)):  # gzip by content, whatever the file is called
        data = gzip.decompress(data)
    return data.decode("utf-8", errors="replace")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (PLACEHOLDER + "-0000-0000-0000-000000000000", [1]),
        ("a" + chr(10) + PLACEHOLDER + "-a1b2c3d4" + chr(10), [2]),
        (PLACEHOLDER, []),
        (PLACEHOLDER + "-", []),
        (PLACEHOLDER + " " + NIL_UUID, []),
        (PLACEHOLDER + '"', []),
    ],
)
def test_the_hybrid_detector_flags_a_placeholder_followed_by_a_hyphen_and_a_hex_digit(
    text: str, expected: list[int]
):
    if _hybrid_lines(text) != expected:
        pytest.fail("the hybrid detector reads a planted sample wrongly", pytrace=False)


def test_no_tracked_file_holds_the_placeholder_followed_by_a_hyphen_and_a_hex_digit():
    hits = []
    for rel in _tracked():
        try:
            lines = _hybrid_lines(_plain_or_decompressed(rel))
        except (OSError, EOFError, gzip.BadGzipFile):
            continue  # an unreadable or truncated blob holds nothing this scan can read
        hits.extend(f"{rel}:{n}" for n in lines)

    if hits:
        _fail_with_list(
            f"{len(hits)} line(s) hold {PLACEHOLDER} as the prefix of a UUID-shaped value; "
            "use the plain placeholder or an invented UUID v4 recorded in a PROVENANCE.md",
            hits,
        )


@pytest.mark.parametrize("rel", CONSUMER_TEST_SOURCES)
def test_a_consumer_test_source_holds_no_unrecorded_uuid_literal(rel: str):
    stray = [token for token in _tokens(_text(rel)) if token not in _allowed()]
    if stray:
        pytest.fail(
            f"{rel} holds {len(stray)} UUID-shaped literal(s) not recorded as invented",
            pytrace=False,
        )


# --- recorded digests ----------------------------------------------------------------------


def _provenance_rows() -> list[tuple[str, str, tuple[str, ...], str]]:
    tracked = set(_tracked())
    rows: list[tuple[str, str, tuple[str, ...], str]] = []
    provenance_files = sorted(
        rel
        for rel in tracked
        if rel.startswith("tests/fixtures/") and rel.endswith("PROVENANCE.md")
    )
    for prov in provenance_files:
        folder = prov.rsplit("/", 1)[0]
        for line in (REPO / prov).read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("|"):
                continue
            digests = tuple(_SHA256_RE.findall(line))
            names = _ROW_FILE_RE.findall(line)
            if not digests or not names:
                continue
            subject = next((c for c in (f"{folder}/{names[0]}", names[0]) if c in tracked), None)
            if subject:
                rows.append((prov, subject, digests, line))
    return rows


_ROWS = _provenance_rows()
_ROW_IDS = [f"{prov}::{subject}" for prov, subject, _, _ in _ROWS]
_SCOPE_ROWS = [row for row in _ROWS if row[1] in REDACTION_SCOPE]
_SCOPE_ROW_IDS = [f"{row[0]}::{row[1]}" for row in _SCOPE_ROWS]


def test_provenance_rows_are_discovered():
    if not _ROWS:
        pytest.fail("no PROVENANCE.md row with a file and a sha256 was found", pytrace=False)


def _digest_of(rel: str) -> str:
    data = (REPO / rel).read_bytes()
    return hashlib.sha256(data if rel.endswith(".gz") else _lf(data)).hexdigest()


@pytest.mark.parametrize(("prov", "subject", "digests", "line"), _ROWS, ids=_ROW_IDS)
def test_a_provenance_row_states_the_digest_of_the_bytes_on_disk(prov, subject, digests, line):
    if _digest_of(subject) not in digests:
        pytest.fail(
            f"{prov} row for {subject} does not state the file's current sha256", pytrace=False
        )


@pytest.mark.parametrize(("prov", "subject", "digests", "line"), _SCOPE_ROWS, ids=_SCOPE_ROW_IDS)
def test_a_provenance_row_for_a_redacted_file_names_the_replacement_as_a_changed_field(
    prov, subject, digests, line
):
    if PLACEHOLDER not in line:
        pytest.fail(
            f"{prov} row for {subject} does not name {PLACEHOLDER} as a changed field",
            pytrace=False,
        )


def _sidecars_with_raw_digest() -> list[str]:
    out = []
    for rel in _tracked():
        if rel.startswith("tests/fixtures/") and rel.endswith(".capture.json"):
            facts = json.loads((REPO / rel).read_bytes()).get("facts", {})
            if "raw_sha256" in facts:
                out.append(rel)
    return sorted(out)


_RAW_SIDECARS = _sidecars_with_raw_digest()


def test_a_sidecar_with_a_raw_digest_is_discovered():
    if not _RAW_SIDECARS:
        pytest.fail("no sidecar states a raw_sha256 for a compressed record", pytrace=False)


@pytest.mark.parametrize("sidecar", _RAW_SIDECARS)
def test_a_sidecar_states_the_decompressed_digest_and_sizes_of_its_record(sidecar: str):
    meta = json.loads((REPO / sidecar).read_bytes())
    record = f"{sidecar.rsplit('/', 1)[0]}/{meta['record']['file']}"
    compressed = (REPO / record).read_bytes()
    raw = gzip.decompress(compressed)

    if hashlib.sha256(raw).hexdigest() != meta["facts"]["raw_sha256"]:
        pytest.fail(f"{sidecar} raw_sha256 differs from its decompressed record", pytrace=False)
    if len(raw) != meta["facts"]["raw_bytes"]:
        pytest.fail(f"{sidecar} raw_bytes differs from its decompressed record", pytrace=False)
    if len(compressed) != meta["facts"]["gzip_bytes"]:
        pytest.fail(f"{sidecar} gzip_bytes differs from its record", pytrace=False)


@pytest.mark.parametrize("rel", sorted(r for r in _tracked() if r.endswith(".json.gz")))
def test_a_gzip_record_carries_no_file_name_and_no_time_so_its_bytes_reproduce(rel: str):
    head = (REPO / rel).read_bytes()[:10]

    if head[:2] != b"\x1f\x8b":
        pytest.fail(f"{rel} is not gzip", pytrace=False)
    if head[3] & 0x08:
        pytest.fail(f"{rel} header carries a file name", pytrace=False)
    if head[4:8] != b"\x00\x00\x00\x00":
        pytest.fail(f"{rel} header carries a modification time", pytrace=False)


# --- key-sorted records stay key-sorted, with the placeholder as a key -------------------


@pytest.mark.parametrize("rel", SORTED_KEY_RECORDS)
def test_a_key_sorted_record_stays_exactly_what_a_sorted_dump_writes(rel: str):
    text = _lf((REPO / rel).read_bytes()).decode("utf-8")

    if text != json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n":
        pytest.fail(f"{rel} is not the key-sorted, 2-space, LF serialization", pytrace=False)


@pytest.mark.parametrize("rel", SORTED_KEY_RECORDS)
def test_the_redacted_account_key_sits_where_sorting_places_it(rel: str):
    keys = list(json.loads((REPO / rel).read_bytes())["accounts_map"])

    if PLACEHOLDER not in keys:
        pytest.fail(f"{rel} accounts_map has no {PLACEHOLDER} key", pytrace=False)
    if len(keys) < 2:
        pytest.fail(f"{rel} accounts_map lost its second account key", pytrace=False)
    if keys != sorted(keys):
        pytest.fail(f"{rel} accounts_map keys are not in sorted order", pytrace=False)
