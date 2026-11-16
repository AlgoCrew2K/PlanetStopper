"""The dead-ticker check's Discord text: literal names, mention-proof lines, size-limited messages.

Module under test: advisors.ticker_health (``discord_literal``, ``discord_messages``,
``LEAD_SENTENCE``, ``DISCORD_CONTENT_LIMIT``).

Contract pinned here (the implementer reads this file, not the plan):

- ``DISCORD_CONTENT_LIMIT == 2000`` (Discord's documented limit on a message's ``content``).
- ``discord_literal(text) -> str`` puts one backslash before each of these 16 characters and
  changes nothing else: backslash, asterisk, underscore, tilde, backtick, vertical bar, ``<``,
  ``>``, ``#``, hyphen, ``[``, ``]``, ``(``, ``)``, ``@`` and ``:``. A name or ticker therefore never
  forms Discord markdown, a link or mention syntax.
- ``discord_messages(pairs) -> list[str]``; ``pairs`` is a list of warning dicts (keys
  ``symphony_id``, ``display_name``, ``ticker``, ``reason``, ``asset_nodes``, ``conditions``,
  ``held``, ``found_at``, ``announced``). No pair gives no message (``[]``). Each message
  starts with ``LEAD_SENTENCE`` and then holds one line per pair, in the order given, each line
  being

      "{name} ({symphony_id}): {ticker} is {reason}. Used by {n} asset node(s) and {m} condition(s). {held text}"

  with the name and the ticker passed through ``discord_literal`` (the id, reason and wording
  are not), the counts' plurals and the held text ("Held now.", "Not held now." or "Holding
  unknown.") as on the dashboard line. Lines pack greedily into as few messages as the limit
  allows, every message at most ``DISCORD_CONTENT_LIMIT`` characters long. A single line too long
  for a message keeps its id, ticker, reason, counts and held text, and shortens only the name,
  ending it with "...". A name never adds a line: a line break inside a name must not start a
  new line of the message.

Fixture provenance: names and symphony ids come from the captured roster
``tests/fixtures/composer/ticker-health/symphony_stats_meta_holdings_kept.json`` (PROVENANCE.md
there). The hostile names (a mention, a link, markup, a line break) are DERIVED: invented
strings, labelled where they are made, because the producer of a name is the operator typing it in
Composer and a captured name cannot be hostile by definition. The plan's worked example is the
invented QXGD symphony.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from advisors import ticker_health as th

ROSTER = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures/composer/ticker-health/symphony_stats_meta_holdings_kept.json"
    ).read_text(encoding="utf-8")
)["symphonies"]

LEAD = "These live symphonies reference tickers that can no longer be traded; fix each tree in Composer."
SPECIAL = "\\*_~`|<>#-[]()@:"
ESCAPE = {ord(c): "\\" + c for c in SPECIAL}  # an independent, table-driven reading of the rule

EXAMPLE_ID = "AbCdEf0123456789XyZa"
HELD_TEXT = {"held": "Held now.", "not_held": "Not held now.", "unknown": "Holding unknown."}


def esc(text: str) -> str:
    return text.translate(ESCAPE)


def pair(sid=EXAMPLE_ID, name="Example Copper Rotation", ticker="QXGD", **overrides):
    record = {
        "symphony_id": sid,
        "display_name": name,
        "ticker": ticker,
        "reason": "inactive and not tradable at Alpaca",
        "asset_nodes": 25,
        "conditions": 1,
        "held": "not_held",
        "found_at": "2026-11-17T04:12:00+00:00",
        "announced": False,
    }
    record.update(overrides)
    return record


def expected_line(p: dict) -> str:
    assets, conds = p["asset_nodes"], p["conditions"]
    return (
        f"{esc(p['display_name'])} ({p['symphony_id']}): {esc(p['ticker'])} is {p['reason']}. "
        f"Used by {assets} asset node{'' if assets == 1 else 's'} and "
        f"{conds} condition{'' if conds == 1 else 's'}. {HELD_TEXT[p['held']]}"
    )


def line_break_split(text: str) -> list[str]:
    return re.split(r"\r\n|\n|\r", text)


def body_lines(message: str) -> list[str]:
    """The non-empty lines of a message after the lead."""
    assert message.startswith(LEAD)
    return [ln for ln in line_break_split(message[len(LEAD) :]) if ln.strip()]


def forty_pairs() -> list[dict]:
    pairs = []
    for i in range(40):
        entry = ROSTER[i % len(ROSTER)]
        pairs.append(
            pair(
                entry["id"],
                entry["name"].strip(),
                f"TK{i:02d}",
                held="held" if i % 3 else "unknown",
            )
        )
    return pairs


# ---------------------------------------------------------------------------
# discord_literal
# ---------------------------------------------------------------------------


class TestDiscordLiteral:
    def test_the_limit_is_discords_documented_content_limit(self):
        assert th.DISCORD_CONTENT_LIMIT == 2000

    @pytest.mark.parametrize("char", list(SPECIAL))
    def test_each_listed_character_gets_one_backslash(self, char):
        assert th.discord_literal(f"a{char}b") == f"a\\{char}b"

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("@everyone", "\\@everyone"),
            ("@here", "\\@here"),
            ("<@123456789012345678>", "\\<\\@123456789012345678\\>"),
            ("<@&987654321098765432>", "\\<\\@&987654321098765432\\>"),
            ("[click](http://evil.example)", "\\[click\\]\\(http\\://evil.example\\)"),
            (
                "**bold** __under__ ~~strike~~",
                "\\*\\*bold\\*\\* \\_\\_under\\_\\_ \\~\\~strike\\~\\~",
            ),
            ("`code` ||spoiler||", "\\`code\\` \\|\\|spoiler\\|\\|"),
            ("# heading", "\\# heading"),
            ("- list item", "\\- list item"),
            ("> quote", "\\> quote"),
            (":smile:", "\\:smile\\:"),
            ("back\\slash", "back\\\\slash"),
            ("\\*", "\\\\\\*"),
        ],
    )
    def test_hostile_text_is_made_literal(self, text, expected):
        assert th.discord_literal(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "Example Copper Rotation 123",
            "Planet LQD Run 2/3 Mixup's, v1.2 & more! 50%",
            "SHNY",
            "BRK.B",
        ],
    )
    def test_ordinary_text_is_unchanged(self, text):
        assert th.discord_literal(text) == text

    def test_every_captured_roster_name_is_made_literal_by_the_same_table(self):
        for entry in ROSTER:
            name = entry["name"]
            assert th.discord_literal(name) == esc(name)


# ---------------------------------------------------------------------------
# discord_messages
# ---------------------------------------------------------------------------


class TestMessageText:
    def test_no_pair_gives_no_message(self):
        assert th.discord_messages([]) == []

    def test_the_worked_example_names_the_symphony_its_id_the_ticker_and_the_facts(self):
        messages = th.discord_messages([pair()])
        assert len(messages) == 1
        assert th.LEAD_SENTENCE == LEAD
        assert messages[0].startswith(LEAD)
        assert body_lines(messages[0]) == [
            "Example Copper Rotation (AbCdEf0123456789XyZa): QXGD is inactive and not tradable at "
            "Alpaca. Used by 25 asset nodes and 1 condition. Not held now."
        ]

    @pytest.mark.parametrize("held", ["held", "not_held", "unknown"])
    @pytest.mark.parametrize(("assets", "conds"), [(1, 0), (0, 1), (2, 3)])
    def test_the_line_follows_the_dashboard_wording(self, held, assets, conds):
        p = pair(held=held, asset_nodes=assets, conditions=conds, reason="not listed at Alpaca")
        assert body_lines(th.discord_messages([p])[0]) == [expected_line(p)]

    def test_two_symphonies_naming_one_dead_ticker_are_both_named(self):
        pairs = [
            pair("idAAAAAAAAAAAAAAAAAA", "First fund", "QXGD"),
            pair("idBBBBBBBBBBBBBBBBBB", "Second fund", "QXGD"),
        ]
        assert body_lines(th.discord_messages(pairs)[0]) == [expected_line(p) for p in pairs]

    def test_a_captured_name_with_a_colon_is_escaped_in_its_line(self):
        entry = next(e for e in ROSTER if ":" in e["name"])
        p = pair(entry["id"], entry["name"].strip(), "SHNY")
        assert body_lines(th.discord_messages([p])[0]) == [expected_line(p)]
        assert "\\:" in th.discord_messages([p])[0]

    def test_pairs_are_not_modified(self):
        pairs = [pair()]
        before = json.dumps(pairs)
        th.discord_messages(pairs)
        assert json.dumps(pairs) == before


class TestMessagesStayInsideTheLimit:
    def test_forty_pairs_are_all_named_once_in_order_across_messages_within_the_limit(self):
        pairs = forty_pairs()
        messages = th.discord_messages(pairs)
        assert len(messages) > 1, "40 captured-name lines do not fit one 2000 character message"
        assert all(len(m) <= th.DISCORD_CONTENT_LIMIT for m in messages)
        assert all(m.startswith(LEAD) for m in messages)
        assert [ln for m in messages for ln in body_lines(m)] == [expected_line(p) for p in pairs]

    def test_the_messages_are_packed_not_one_pair_each(self):
        pairs = forty_pairs()
        room = th.DISCORD_CONTENT_LIMIT - len(LEAD) - 1
        needed = math.ceil(sum(len(expected_line(p)) + 1 for p in pairs) / room)
        assert len(th.discord_messages(pairs)) <= needed + 1

    def test_a_name_too_long_for_a_message_is_shortened_and_nothing_else_is(self):
        long_pair = pair(name="a" * 3000)
        messages = th.discord_messages([long_pair])
        assert len(messages) == 1 and len(messages[0]) <= th.DISCORD_CONTENT_LIMIT
        facts = (
            f"... ({EXAMPLE_ID}): QXGD is inactive and not tradable at Alpaca. "
            "Used by 25 asset nodes and 1 condition. Not held now."
        )
        (line,) = body_lines(messages[0])
        assert line.endswith(facts)
        assert set(line[: -len(facts)]) == {"a"} and line[: -len(facts)]

    def test_a_long_name_beside_a_normal_pair_leaves_the_normal_line_whole_and_single(self):
        normal = pair("idBBBBBBBBBBBBBBBBBB", "Second fund", "PLRZ")
        messages = th.discord_messages([pair(name="b" * 2500), normal])
        assert all(len(m) <= th.DISCORD_CONTENT_LIMIT for m in messages)
        lines = [ln for m in messages for ln in body_lines(m)]
        assert lines.count(expected_line(normal)) == 1
        assert sum(EXAMPLE_ID in ln for ln in lines) == 1

    def test_every_name_length_around_the_limit_keeps_the_message_inside_it(self):
        for length in range(1700, 2001, 11):
            p = pair(name="n" * length)
            (message,) = th.discord_messages([p])
            assert len(message) <= th.DISCORD_CONTENT_LIMIT, length
            assert EXAMPLE_ID in message and "QXGD" in message and "Not held now." in message
            assert ("n" * length) in message or f"... ({EXAMPLE_ID}): QXGD is" in message, length


class TestMentionsAndMarkupInNamesAndTickers:
    HOSTILE_NAMES = [
        "@everyone",
        "@here ping",
        "<@123456789012345678>",
        "<@&987654321098765432>",
        "[click me](http://evil.example)",
        "**bold** and __under__",
        "# heading",
        "`code`",
        "||spoiler||",
        "Planet: sneaky - dash",
    ]

    @pytest.mark.parametrize("name", HOSTILE_NAMES)
    def test_a_hostile_name_is_escaped_in_its_line(self, name):
        p = pair(name=name)
        assert body_lines(th.discord_messages([p])[0]) == [expected_line(p)]

    @pytest.mark.parametrize("name", HOSTILE_NAMES)
    def test_no_special_character_of_a_name_is_left_bare(self, name):
        (line,) = body_lines(th.discord_messages([pair(name=name)])[0])
        name_part = line.split(f" ({EXAMPLE_ID}): ")[0]
        for index, char in enumerate(name_part):
            if char in SPECIAL and char != "\\":
                backslashes = len(name_part[:index]) - len(name_part[:index].rstrip("\\"))
                assert backslashes % 2 == 1, (name, index)

    @pytest.mark.parametrize("ticker", ["BRK/B", "A B", "..", "@everyone", "<@1>", "_X_", "A:B"])
    def test_a_ticker_is_escaped_too(self, ticker):
        p = pair(ticker=ticker)
        assert body_lines(th.discord_messages([p])[0]) == [expected_line(p)]

    @pytest.mark.parametrize(
        "name", ["Evil\nFAKE LINE (ZZZZZZZZZZZZZZZZZZZZ): SPY is not tradable.", "Evil\rFAKE"]
    )
    def test_a_line_break_in_a_name_never_adds_a_line(self, name):
        other = pair("idBBBBBBBBBBBBBBBBBB", "Second fund", "PLRZ")
        (message,) = th.discord_messages([pair(name=name), other])
        lines = [ln for ln in line_break_split(message) if ln.strip()]
        assert lines[0] == LEAD
        assert len(lines) == 3, lines
        assert EXAMPLE_ID in lines[1] and "idBBBBBBBBBBBBBBBBBB" in lines[2]

    # Every other character that ``str.splitlines`` and Unicode treat as a line boundary
    # (vertical tab, form feed, the three information separators, next line, line and paragraph
    # separator). DERIVED hostile names, labelled: a captured name cannot be hostile by definition.
    OTHER_LINE_BOUNDARIES = ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "]

    @pytest.mark.parametrize("separator", OTHER_LINE_BOUNDARIES, ids=lambda s: f"U+{ord(s):04X}")
    def test_any_other_line_boundary_in_a_name_never_adds_a_line(self, separator):
        hostile = pair(name=f"Evil{separator}FAKE LINE (ZZZZZZZZZZZZZZZZZZZZ): SPY is dead.")
        other = pair("idBBBBBBBBBBBBBBBBBB", "Second fund", "PLRZ")
        (message,) = th.discord_messages([hostile, other])
        assert separator not in message
        assert len(message.splitlines()) == 3, message.splitlines()
        flattened = pair(name="Evil FAKE LINE (ZZZZZZZZZZZZZZZZZZZZ): SPY is dead.")
        assert body_lines(message) == [expected_line(flattened), expected_line(other)]

    def test_the_module_source_names_the_line_boundaries_by_escape_never_by_the_character(self):
        """A separator character typed into the source splits its lines for every tool that
        reads it by ``splitlines`` (the no-trade scan plants statements by line)."""
        source = Path(th.__file__).read_text(encoding="utf-8")
        for separator in self.OTHER_LINE_BOUNDARIES:
            assert separator not in source, f"U+{ord(separator):04X} is in the source literally"

    @pytest.mark.parametrize("separator", OTHER_LINE_BOUNDARIES, ids=lambda s: f"U+{ord(s):04X}")
    def test_a_long_name_with_a_line_boundary_is_cut_without_one(self, separator):
        long_name = f"Alpha{separator}" + "Beta " * 600
        (message,) = th.discord_messages([pair(name=long_name)])
        assert separator not in message
        assert len(message) <= th.DISCORD_CONTENT_LIMIT
        assert len(message.splitlines()) == 2, message.splitlines()
