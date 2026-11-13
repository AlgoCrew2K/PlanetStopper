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
    assert "ref_capture.json" in provenance and "c0760e70" in provenance
    assert document["reference_commit"].startswith("c0760e70")
    # The tables were last changed at this commit; the approved commit the fixture was read at
    # follows it. Both are named, so a later revision of the plan is a visible re-extraction.
    assert "ac4563cb" in provenance and document["plan_sha"][:8] in provenance


# ---------------------------------------------------------------------------
# The regenerated fixture: this plan's rows, Glossary lines, titles and slot phrases
# ---------------------------------------------------------------------------

GLOSSARY_HOSTS = (
    "/",
    "detail panel",
    "Performance",
    "History",
    "Settings",
    "settings modal",
    "Overview",
    "Correlations",
    "Retirement",
    "Asset Swaps",
    "Logic Changes",
    "Chat",
    "Strategy Builder",
    "Frontrunner Builder",
)


def test_the_fixture_holds_a_row_for_every_description_and_every_other_changed_text():
    a_keys = [r["key"] for r in copy.a_rows()]
    x_keys = [r["key"] for r in copy.x_rows()]
    assert len(a_keys) == len(set(a_keys)) and len(x_keys) == len(set(x_keys))
    assert x_keys == [f"CX-{n}" for n in range(1, 21)]
    for key in ("D01", "A01", "O01", "B03", "X05", "D20"):
        assert copy.a_row(key)["kind"] in {"removed", "moved"} and copy.a_text(key) is None, key
    assert copy.a_row("dp.lead")["unchanged"] and copy.a_text("dp.lead"), (
        "a kept row carries its text"
    )
    assert copy.x_row("CX-1")["new_text"] == "No suggestions yet."
    assert copy.x_row("CX-5")["new_text"] == "Show all checks"
    assert copy.x_row("CX-11")["new_text"] == "Proposals appear here after a build."
    assert copy.x_row("CX-15")["slots"] == ["k", "n", "needs_days"]
    for key in ("CX-17", "CX-18", "CX-19"):
        assert copy.x_row(key)["kind"] == "removed" and copy.x_row(key)["new_text"] is None, key
    for key in ("CX-4", "CX-6", "CX-7"):
        assert copy.x_row(key)["new_text"] is None and copy.x_row(key)["new_text_note"]


def test_the_glossary_lists_every_host_in_the_plans_order_with_the_lines_it_names():
    assert tuple(copy.glossary_hosts()) == GLOSSARY_HOSTS
    for host in GLOSSARY_HOSTS:
        lines = copy.glossary(host)
        keys = [line["key"] for line in lines]
        assert lines and len(keys) == len(set(keys)), host
        assert all(line["text"].strip() for line in lines), host
    assert [line["key"] for line in copy.glossary("Frontrunner Builder")] == [
        "X06",
        "X08",
        "X09",
        "X11a",
        "X11b",
        "X11c",
        "X12",
    ]
    assert [line["key"] for line in copy.glossary("Chat")] == ["A04"]
    assert [line["key"] for line in copy.glossary("settings modal")] == ["M01", "M08"]
    removed = {line["key"] for host in GLOSSARY_HOSTS for line in copy.glossary(host)}
    assert "D40t" not in removed, "the Standby definition is dropped"


def test_a_glossary_line_reads_the_referenced_row_or_the_changed_text():
    for host in GLOSSARY_HOSTS:
        for line in copy.glossary(host):
            if line["source"] == "ref":
                assert line["text"] == copy.row(line["key"], "B")["new_text"], line["key"]
            else:
                assert line["source"] == "G2"
            lead = line["text"].split(": ", 1)[0] if ": " in line["text"] else None
            assert line["lead_term"] == lead, line["key"]
    starred = {"D02", "D20t", "D50e", "F15h", "F17a", "F17h", "Y16", "S14s", "S14p", "R12"}
    seen = {
        line["key"]
        for host in GLOSSARY_HOSTS
        for line in copy.glossary(host)
        if line["source"] == "G2"
    }
    assert starred <= seen and {"O31a", "O31b", "O31c"} <= seen


def test_the_three_guardrail_lines_drop_only_the_term_name_and_its_is():
    lines = {line["key"]: line["text"] for line in copy.glossary("Overview")}
    assert lines["O31a"].startswith("Spec Critic: an active guardrail checking the shared")
    assert lines["O31b"].startswith(
        "Overfitting Conscience: checks one narrow overfitting-risk source"
    )
    assert lines["O31c"].startswith("Divergence Explainer: disabled by default and its rows")
    for text in (lines["O31a"], lines["O31b"], lines["O31c"]):
        assert "Per-symphony recommendations" not in text, "the fourth sentence is dropped"
        assert text.count(": ") >= 1 and text.rstrip().endswith("."), text


def test_every_title_row_names_lines_its_host_holds():
    titles = copy.title_rows()
    assert len(titles) == 16
    for row in titles:
        hosts = [h.strip() for h in row["host"].split(",")]
        held = {line["key"] for h in hosts for line in copy.glossary(h)}
        assert row["line_keys"] and set(row["line_keys"]) <= held, (row["key"], row["line_keys"])
    keys = {t["key"]: t["line_keys"] for t in titles}
    assert keys["TG3-9"] == [f"F15{c}" for c in "abcdefgh"], keys["TG3-9"]
    assert keys["TG3-5"] == ["O09a", "O09c"]


def test_the_two_retirement_phrases_change_and_the_reference_keeps_the_old_ones():
    new_screen = copy.new_slot_row("screen_threshold", "R02")
    new_tenure = copy.new_slot_row("tenure_threshold", "R22")
    assert new_screen["phrase"] == "at {screen_threshold} or more"
    assert new_screen["fallback"] == "past the screen"
    assert new_tenure["phrase"] == "Once invested for {tenure_threshold} trading days,"
    assert new_tenure["fallback"] == "Once past the configured minimum tenure,"
    assert copy.slot_row("screen_threshold", "R02")["phrase"] != new_screen["phrase"]
    for slot, row in (("stress_fraction_pct", "R19b"), ("watch_trail_pp", "R27b")):
        assert copy.new_slot_row(slot, row) == copy.slot_row(slot, row), "an unchanged phrase"


def test_the_reference_rows_are_served_unchanged_to_every_test_not_yet_re_pinned():
    assert copy.text("D01", "A").startswith("This page shows how the bot")
    assert copy.row("H02", "B")["states"]
    assert len(copy.rows("A")) == 55 and len(copy.rows("C")) == 46
    assert copy.a_text("D01") is None, "this plan removes the line the reference rows still hold"
