"""The copy fixture and its loader: a text scan of every row the plan writes (S-1.18).

``tests/fixtures/page_explainers/copy.json`` is written once by ``extract_copy.py`` from the
approved plan's Copy tables and Values table; ``tests/ui/_page_copy.py`` reads only that file.
These tests hold the fixture itself to the plan's text rules, so no later cycle asserts a row that
carries a forbidden character, a banned word or an unresolved reference, and so the loader stays
free of the plan.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from tests.ui import _page_copy as copy

FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "page_explainers"
BANNED_WORDS = (
    "powerful",
    "seamless",
    "effortless",
    "revolutionary",
    "cutting-edge",
    "unlock",
    "supercharge",
    "amazing",
    "simply the best",
)
EMOJI_RANGES = re.compile("[\U0001f000-\U0001faff☀-➿⭐⭕️]")
EM_DASH, EN_DASH = "—", "–"
SENTENCE_BREAK = re.compile(r"(?<=[.?])\s+(?=[A-Z0-9\"'(])")
# Texts a runtime fill supplies, never a Values table slot: a verdict magnitude, a mode word, a
# model name, a symphony name, a proposal id, an aligned-day count.
RUNTIME_FILLS = {"m", "mode", "model", "name", "id", "n"}


def _new_texts(record: dict) -> list[str]:
    """Every text the row says is new (a kept row says nothing new)."""
    if record.get("unchanged"):
        return []
    texts = [record["new_text"]]
    texts += [t for t in (record.get("new_resolved"), record.get("new_unresolved")) if t]
    texts += record.get("new_texts") or []
    texts += list((record.get("states") or {}).values())
    return texts


ROW_IDS = [f"{r['table']}-{r['key']}" for r in copy.rows()]


@pytest.mark.parametrize("record", copy.rows(), ids=ROW_IDS)
def test_a_new_text_has_no_exclamation_mark_dash_emoji_or_banned_word(record):
    for text in _new_texts(record):
        assert "!" not in text, f"{record['key']}: an exclamation mark in {text!r}"
        assert EM_DASH not in text and EN_DASH not in text, f"{record['key']}: a dash in {text!r}"
        assert not EMOJI_RANGES.search(text), f"{record['key']}: an emoji in {text!r}"
        lowered = text.lower()
        found = [w for w in BANNED_WORDS if w in lowered]
        assert not found, f"{record['key']}: the banned word {found} in {text!r}"


@pytest.mark.parametrize(
    "record",
    [r for r in copy.rows("A") if r["kind"] == "new"],
    ids=lambda r: r["key"],
)
def test_a_new_description_is_one_or_two_sentences(record):
    sentences = [s for s in SENTENCE_BREAK.split(record["new_text"].strip()) if s]
    assert 1 <= len(sentences) <= 2, f"{record['key']}: {len(sentences)} sentences"


def test_the_scan_catches_what_it_scans_for():
    """The scan itself fails on a bad text, so a clean fixture means something."""
    bad_texts = [
        "Nice!",
        f"A {EM_DASH} B",
        f"A {EN_DASH} B",
        "A \U0001f680 launch",
        "an unlock step",
    ]
    caught = 0
    for text in bad_texts:
        if (
            "!" in text
            or EM_DASH in text
            or EN_DASH in text
            or EMOJI_RANGES.search(text)
            or any(w in text.lower() for w in BANNED_WORDS)
        ):
            caught += 1
    assert caught == len(bad_texts)


def test_every_row_has_a_text_and_every_reference_is_resolved():
    for record in copy.rows():
        key = f"{record['table']}:{record['key']}"
        assert record["new_text"].strip(), f"{key}: no new text"
        assert not record["new_text"].startswith("(the "), f"{key}: an unresolved reference"
        if record.get("unchanged"):
            assert record.get("ref_text"), f"{key}: a kept row with no text at the ref"
        if record["table"] == "B":
            assert record["placement_kind"] in {"V", "D", "A"}, key


def test_the_three_tables_hold_the_rows_the_plan_names():
    tables = {t: copy.rows(t) for t in ("A", "B", "C")}
    assert all(tables.values()), "a copy table is empty"
    for table, rows in tables.items():
        keys = [r["key"] for r in rows]
        assert len(keys) == len(set(keys)), f"table {table} repeats a key"
    # Rows the later cycles read by name exist in the table that owns them.
    for table, key in (("A", "D01"), ("A", "R22"), ("B", "H02"), ("B", "H08"), ("C", "D30a")):
        assert copy.row(key, table)["key"] == key


@pytest.mark.parametrize("key", ["H02", "H08"])
def test_a_header_row_is_split_into_one_sentence_per_state(key):
    record = copy.row(key, "B")
    states = record["states"]
    assert len(states) >= 2
    for name, sentence in states.items():
        assert sentence.startswith(f"{name}: "), f"{key}: {sentence!r}"
        assert record["new_text"].count(sentence) == 1
    assert "Each state carries only its own sentence" not in " ".join(states.values())


def test_a_term_definition_that_names_a_description_carries_that_description():
    record = copy.row("D02", "B")
    assert record["resolved_from"] == "dash.hero"
    assert record["new_text"] == copy.row("dash.hero", "A")["new_text"]


def test_the_reason_definitions_are_the_four_term_definitions_without_their_names():
    record = copy.row("Y14b", "C")
    names = ("D29a", "D29b", "D29c", "D29d")
    assert len(record["new_texts"]) == 4
    for text, name in zip(record["new_texts"], names, strict=True):
        definition = copy.row(name, "B")["new_text"]
        lead, _, rest = definition.partition(": ")
        assert text == rest, f"{name}: the leading {lead!r} was not removed"


def test_a_model_row_gives_both_the_resolved_and_the_unresolved_text():
    for key in ("MU1", "MU2", "MU3", "MU4", "MU5"):
        record = copy.row(key, "C")
        assert record["new_resolved"] and record["new_unresolved"], key


def test_every_slot_in_a_table_a_or_b_row_is_in_the_values_table_and_back():
    slots_in_values = {(v["copy_row"], v["slot"]) for v in copy.slot_rows()}
    for record in copy.rows():
        if record["table"] == "C":
            continue
        for slot in record["slots"]:
            if slot in RUNTIME_FILLS:
                continue
            assert (record["key"], slot) in slots_in_values, (
                f"{record['key']}: the slot {{{slot}}} has no Values table row"
            )
    rows_by_key = {(r["table"], r["key"]): r for r in copy.rows()}
    for value in copy.slot_rows():
        row = rows_by_key[(value["table"], value["copy_row"])]
        assert value["phrase"] in row["new_text"], value["copy_row"]


def test_each_slot_has_one_whole_fallback_phrase_that_holds_no_slot():
    slots = copy.slot_rows()
    assert {(v["copy_row"], v["slot"]) for v in slots} >= {
        ("R22", "tenure_threshold"),
        ("R02", "screen_threshold"),
        ("R19b", "stress_fraction_pct"),
        ("R27b", "watch_trail_pp"),
        ("D25", "friction_pct"),
        ("F24", "friction_pct"),
        ("D07", "shadow_retention_days"),
        ("F10", "min_history_days"),
    }
    for value in slots:
        assert value["fallback"].strip(), value["copy_row"]
        assert "{" not in value["fallback"], f"{value['copy_row']}: a slot in the fallback"
        assert "{" + value["slot"] + "}" in value["phrase"]
        assert value["fallback"] != value["phrase"]


def test_fill_replaces_only_the_named_slots():
    phrase = copy.slot_row("tenure_threshold", "R22")["phrase"]
    assert copy.fill(phrase, tenure_threshold="90") == phrase.replace("{tenure_threshold}", "90")
    assert "{other}" in copy.fill("a {other} b {x}", x="1")


def test_the_loader_and_the_harness_read_the_fixture_and_never_the_plan():
    here = pathlib.Path(__file__).parent
    for name in ("_page_copy.py", "_page_explainers.py"):
        source = (here / name).read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", source, flags=re.S)
        assert "feature-plans" not in code, f"{name} reads the plan"
    assert copy.FIXTURE == FIXTURE_DIR / "copy.json"


def test_the_fixture_names_the_plan_commit_it_was_read_at():
    document = json.loads((FIXTURE_DIR / "copy.json").read_text(encoding="utf-8"))
    provenance = (FIXTURE_DIR / "PROVENANCE.md").read_text(encoding="utf-8")
    assert re.fullmatch(r"[0-9a-f]{40}", document["plan_sha"])
    assert document["plan_sha"][:8] in provenance
    assert document["plan_path"].startswith("feature-plans/")
    assert "ref_capture.json" in provenance and "1988ce5f" in provenance
