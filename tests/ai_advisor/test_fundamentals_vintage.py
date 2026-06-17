"""
Fundamentals lens — vintage fix (F5 RED tests).

Drives AC-1..AC-7 for the two concurrent vintage defects:
  Mode A — XBRL concept deprecation: _SEC_KEY_CONCEPTS hardcodes a single
            us-gaap tag per concept with no fallback; migrated tags are never
            reached.
  Mode B — wrong sort key: entries_to_check sorted by filed descending is
            stable; Python's stable sort yields the OLDEST end first when all
            entries share one filed date.

Golden fixtures (tests/fixtures/math/fundamentals_vintage_*.json) are
schema-derived from the real SEC companyfacts shape documented in the F5
closeout's runnable SEC evidence.  Provenance: schema-derived, NOT
parser-co-designed.

Mocking strategy
----------------
* requests.get is patched per-test to return fixture-shaped companyfacts
  responses.  The autouse _stub_live_lens_seams in conftest.py already patches
  requests.get to raise ConnectionError; each test's ``with patch(...)`` block
  overrides that autouse stub for the duration of the context manager (inner
  patch wins — established pattern across this test directory).
* _sec_ticker_to_cik for JPM and MSFT resolves from _SEC_TICKER_CIK_CACHE
  (no HTTP) — only the companyfacts fetch hits requests.get.
* database.load_state is patched for portfolio-path tests.
* Math engine is NOT mocked — not used by this codepath.
* No live SEC calls in any test in this file.

NEVER hardcode financial literal values as expected assertions.  All value
and date assertions derive from the fixture JSON using max() / fixture lookup.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FX_DIR = pathlib.Path(__file__).parents[1] / "fixtures" / "math"
_FX_MODE_B = _FX_DIR / "fundamentals_vintage_mode_b_bundled_comparative.json"
_FX_MODE_A = _FX_DIR / "fundamentals_vintage_mode_a_migrated_concept.json"
_FX_CROSS = _FX_DIR / "fundamentals_vintage_cross_tag_later_listed_wins.json"
_FX_MALFORMED = _FX_DIR / "fundamentals_vintage_malformed_payloads.json"

# The five outer logical keys that key_facts MUST have (AC-4 shape guard).
_EXPECTED_KEY_FACTS_KEYS = frozenset(
    {"Revenues", "NetIncomeLoss", "Assets", "Liabilities", "StockholdersEquity"}
)
# The per-entry fields that MUST be present in each key_facts value (AC-4).
_EXPECTED_ENTRY_FIELDS = frozenset({"label", "value", "unit", "end", "filed", "form"})


# ---------------------------------------------------------------------------
# Fixture loading helpers (schema validated on load)
# ---------------------------------------------------------------------------


def _load_mode_b() -> dict:
    data = json.loads(_FX_MODE_B.read_text(encoding="utf-8"))
    _validate_companyfacts_shape(data["companyfacts"])
    return data


def _load_mode_a() -> dict:
    data = json.loads(_FX_MODE_A.read_text(encoding="utf-8"))
    _validate_companyfacts_shape(data["companyfacts"])
    return data


def _load_cross() -> dict:
    data = json.loads(_FX_CROSS.read_text(encoding="utf-8"))
    _validate_companyfacts_shape(data["companyfacts"])
    return data


def _load_malformed() -> dict:
    return json.loads(_FX_MALFORMED.read_text(encoding="utf-8"))


def _validate_companyfacts_shape(cf: dict) -> None:
    """Assert the minimum schema required for the producer to process this fixture."""
    assert "cik" in cf, "fixture companyfacts must have 'cik'"
    assert "entityName" in cf, "fixture companyfacts must have 'entityName'"
    assert "facts" in cf, "fixture companyfacts must have 'facts'"
    assert "us-gaap" in cf["facts"], "fixture 'facts' must have 'us-gaap'"


# ---------------------------------------------------------------------------
# Mock response helpers
# ---------------------------------------------------------------------------


def _mock_resp(json_data: dict, status_code: int = 200) -> MagicMock:
    """Build a requests.Response-like mock returning json_data."""
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data
    m.text = json.dumps(json_data)
    m.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests.exceptions import HTTPError
        m.raise_for_status.side_effect = HTTPError(
            f"HTTP {status_code}", response=m
        )
    return m


def _empty_holdings_state() -> dict:
    return {}


def _holdings_state(tickers: list[str]) -> dict:
    return {
        "test_symphony": {
            "logic_holdings": {t: {"weight": 1.0 / len(tickers)} for t in tickers}
        }
    }


# ---------------------------------------------------------------------------
# AC-1: Mode B — latest end selected from bundled comparatives
# ---------------------------------------------------------------------------


class TestModeB:
    """AC-1: Mode B defect — sort-by-filed is stable; Python stable sort yields
    the OLDEST end first when all entries share one filed date.  Fix: sort by
    end descending, filed descending as secondary tiebreak.
    """

    def test_selects_latest_end_not_oldest_from_shared_filed_date(self):
        """FAILS on current: filed-sort stable → oldest end first.

        AC-1: given 3 x 10-K entries all sharing filed=2025-02-01 but with
        end dates 2022-12-31, 2023-12-31, 2024-12-31, the producer must select
        the entry with end == max(end for all entries in fixture).

        The assertion derives max_end from the fixture — no hardcoded date string.
        """
        fx = _load_mode_b()
        cf = fx["companyfacts"]

        # Derive the expected answer from the fixture itself (no hardcoding).
        revenues_entries = (
            cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
        )
        max_end = max(e["end"] for e in revenues_entries)

        import ai_advisor

        with patch("requests.get", return_value=_mock_resp(cf)):
            result = ai_advisor._fetch_fundamentals_for_ticker("JPM")

        assert result.get("available") is True, (
            f"_fetch_fundamentals_for_ticker('JPM') returned available=False. "
            f"reason={result.get('reason')!r}. Fixture has valid entries."
        )
        key_facts = result["payload"]["key_facts"]
        assert "Revenues" in key_facts, (
            "key_facts is missing 'Revenues' concept. Fixture has Revenues entries."
        )
        selected_end = key_facts["Revenues"]["end"]
        assert selected_end == max_end, (
            f"Mode B defect: producer selected end={selected_end!r} "
            f"but the correct (max) end is {max_end!r}. "
            "CAUSE: sort by filed descending is stable — Python's stable sort "
            "preserves original list order on equal keys, so the OLDEST end "
            "(first in the chronological list) is returned. "
            "FIX: sort by end descending, filed descending as secondary key."
        )

    def test_selected_value_matches_fixture_entry_at_max_end(self):
        """FAILS on current: wrong entry is selected, so value is also wrong.

        AC-1: the selected value must equal the fixture entry's val at the
        max-end entry.  Assertion derives both max_end and the corresponding
        val from the fixture — no hardcoded numbers.
        """
        fx = _load_mode_b()
        cf = fx["companyfacts"]

        revenues_entries = cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
        max_end = max(e["end"] for e in revenues_entries)
        # Find the entry at max_end and read its val from the fixture.
        expected_val = next(
            e["val"] for e in revenues_entries if e["end"] == max_end
        )

        import ai_advisor

        with patch("requests.get", return_value=_mock_resp(cf)):
            result = ai_advisor._fetch_fundamentals_for_ticker("JPM")

        assert result.get("available") is True, (
            f"available=False unexpectedly. reason={result.get('reason')!r}"
        )
        selected_val = result["payload"]["key_facts"]["Revenues"]["value"]
        assert selected_val == expected_val, (
            f"Mode B defect: producer returned value={selected_val!r} "
            f"but the value at the correct (max-end) entry is {expected_val!r}. "
            "The wrong entry was selected."
        )

    def test_jpm_control_all_five_concepts_select_latest_end(self):
        """FAILS on current for all 5 concepts.

        AC-1 (JPM-control regression): all 5 logical concepts in the Mode B
        fixture have entries with identical filed dates.  After the fix, every
        concept must select end == max(end for entries in that concept's list).
        """
        fx = _load_mode_b()
        cf = fx["companyfacts"]
        us_gaap = cf["facts"]["us-gaap"]

        import ai_advisor

        with patch("requests.get", return_value=_mock_resp(cf)):
            result = ai_advisor._fetch_fundamentals_for_ticker("JPM")

        assert result.get("available") is True, (
            f"available=False. reason={result.get('reason')!r}"
        )
        key_facts = result["payload"]["key_facts"]

        for concept in _EXPECTED_KEY_FACTS_KEYS:
            if concept not in us_gaap:
                continue  # skip if fixture doesn't have this concept
            entries = us_gaap[concept]["units"]["USD"]
            max_end = max(e["end"] for e in entries)

            assert concept in key_facts, (
                f"key_facts is missing concept '{concept}' but the fixture "
                "has entries for it."
            )
            selected_end = key_facts[concept]["end"]
            assert selected_end == max_end, (
                f"Mode B defect on concept '{concept}': selected end={selected_end!r}, "
                f"correct max end={max_end!r}. "
                "The filed-sort-only bug affects ALL concepts, not just Revenues."
            )


# ---------------------------------------------------------------------------
# AC-2: Mode A — migrated tag reached
# ---------------------------------------------------------------------------


class TestModeA:
    """AC-2: Mode A defect — _SEC_KEY_CONCEPTS hardcodes a single tag per concept.
    MSFT migrated Revenues→SalesRevenueNet→RevenueFromContractWith....
    The legacy 'Revenues' tag is frozen at 2010.  The fix unions ALL candidate
    tags so the most-recent end across any of them is returned.
    """

    def test_reaches_migrated_tag_when_legacy_revenues_tag_is_frozen(self):
        """FAILS on current: only 'Revenues' tag queried, returns 2010 frozen value.

        AC-2: when the legacy 'Revenues' tag has end=2010-06-30 but the migrated
        'RevenueFromContractWithCustomerExcludingAssessedTax' tag has end=2025-06-30,
        the producer must return the 2025 entry (fresher end wins).

        The assertion derives the expected end from the fixture.
        """
        fx = _load_mode_a()
        cf = fx["companyfacts"]
        us_gaap = cf["facts"]["us-gaap"]

        # Derive expected values from fixture metadata (not hardcoded).
        expected_end = fx["_expected"]["Revenues_logical_concept"]["expected_end"]
        frozen_end = fx["_expected"]["Revenues_logical_concept"]["buggy_end"]

        import ai_advisor

        with patch("requests.get", return_value=_mock_resp(cf)):
            result = ai_advisor._fetch_fundamentals_for_ticker("MSFT")

        assert result.get("available") is True, (
            f"available=False. reason={result.get('reason')!r}"
        )
        key_facts = result["payload"]["key_facts"]
        assert "Revenues" in key_facts, (
            "key_facts is missing 'Revenues' concept. Fixture has revenue tags."
        )
        selected_end = key_facts["Revenues"]["end"]

        assert selected_end != frozen_end, (
            f"Mode A defect: producer returned the FROZEN legacy end={frozen_end!r}. "
            "The 'Revenues' tag is frozen at this date; the migrated tag "
            "'RevenueFromContractWithCustomerExcludingAssessedTax' carries current "
            "data. The fix must union candidate tags and pick the freshest end. "
            "CAUSE: _SEC_KEY_CONCEPTS only queries 'Revenues' (single tag); the "
            "migrated tag is never looked up."
        )
        assert selected_end == expected_end, (
            f"Mode A defect: producer returned end={selected_end!r} "
            f"but the correct migrated-tag end is {expected_end!r}. "
            "After fix, the union across all candidate tags picks the freshest end."
        )

    def test_selected_end_is_migrated_not_legacy_frozen(self):
        """FAILS on current: legacy frozen entry wins.

        AC-2: the val returned must be from the migrated-tag entry at the
        freshest end — not the legacy frozen entry's val.
        """
        fx = _load_mode_a()
        cf = fx["companyfacts"]

        expected_val = fx["_expected"]["Revenues_logical_concept"]["expected_val"]
        frozen_val = fx["_expected"]["Revenues_logical_concept"]["buggy_val"]

        import ai_advisor

        with patch("requests.get", return_value=_mock_resp(cf)):
            result = ai_advisor._fetch_fundamentals_for_ticker("MSFT")

        assert result.get("available") is True, (
            f"available=False. reason={result.get('reason')!r}"
        )
        selected_val = result["payload"]["key_facts"]["Revenues"]["value"]
        assert selected_val != frozen_val, (
            f"Mode A defect: producer returned the frozen legacy value={frozen_val!r}. "
            "This value is from the stale 'Revenues' tag (end=2010). "
            "Fix must union all candidate tags and return the migrated-tag value."
        )
        assert selected_val == expected_val, (
            f"Mode A defect: producer returned value={selected_val!r} but the "
            f"correct migrated-tag value is {expected_val!r}."
        )


# ---------------------------------------------------------------------------
# AC-3: Cross-tag latest end wins over first-listed candidate
# ---------------------------------------------------------------------------


class TestCrossTag:
    """AC-3: when multiple candidate tags are present, the producer must pick
    the SINGLE entry with the most recent end ACROSS ALL candidate tags —
    not merely the first tag in the candidate list.

    Fixture: Revenues(frozen 2020), SalesRevenueNet(fresh 2024),
    RevenueFromContract...(stale 2021).  First-listed candidate tag is the staler
    one; the correct answer comes from SalesRevenueNet (middle candidate).
    """

    def test_later_listed_candidate_tag_wins_when_freshest(self):
        """FAILS on current: only first-listed tag queried, missing the fresher entry.

        AC-3: the cross-tag fixture has SalesRevenueNet (middle candidate, end=2024)
        as the freshest.  The first-listed candidate tag
        (RevenueFromContractWithCustomerExcludingAssessedTax, end=2021) and the last
        (Revenues, end=2020) are both staler.  The correct producer returns 2024.
        """
        fx = _load_cross()
        cf = fx["companyfacts"]
        expected_end = fx["_expected"]["Revenues_logical_concept"]["expected_end"]
        expected_val = fx["_expected"]["Revenues_logical_concept"]["expected_val"]

        # Use a ticker not in the CIK cache to force the tickers-JSON slow path.
        # We mock requests.get to return the tickers-JSON first, then companyfacts.
        cik = cf["cik"]
        tickers_json = {
            "0": {"ticker": "CRST", "cik_str": str(cik), "title": "CrossTag Test Corp"}
        }

        responses = iter([_mock_resp(tickers_json), _mock_resp(cf)])

        import ai_advisor

        with patch("requests.get", side_effect=lambda *a, **k: next(responses)):
            result = ai_advisor._fetch_fundamentals_for_ticker("CRST")

        assert result.get("available") is True, (
            f"available=False for CrossTag fixture. reason={result.get('reason')!r}"
        )
        key_facts = result["payload"]["key_facts"]
        assert "Revenues" in key_facts, "key_facts missing 'Revenues' concept"

        selected_end = key_facts["Revenues"]["end"]
        assert selected_end == expected_end, (
            f"AC-3 failure: producer selected end={selected_end!r}, "
            f"correct (cross-tag max) end is {expected_end!r}. "
            "The first-listed candidate tag in the fixture has a STALER end than "
            "the middle candidate. The producer must union ALL candidate tags and "
            "pick the freshest end, not stop at the first present tag."
        )
        selected_val = key_facts["Revenues"]["value"]
        assert selected_val == expected_val, (
            f"AC-3 failure: producer returned value={selected_val!r}, "
            f"correct value (at max-end entry) is {expected_val!r}."
        )

    def test_union_covers_all_candidate_tags(self):
        """FAILS on current: current loop iterates only the single hardcoded tag.

        AC-3: the selection union must actually collect entries from ALL candidate
        tags that are present in us-gaap — not just the first tag whose key exists.
        Verified by checking the selected end equals max across all three tags
        present in the cross-tag fixture.
        """
        fx = _load_cross()
        cf = fx["companyfacts"]
        us_gaap = cf["facts"]["us-gaap"]

        # Compute the true max end across all revenue-related tags in the fixture.
        revenue_tags = [
            "Revenues",
            "SalesRevenueNet",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
        ]
        all_ends: list[str] = []
        for tag in revenue_tags:
            tag_data = us_gaap.get(tag)
            if tag_data:
                for unit_entries in tag_data.get("units", {}).values():
                    if isinstance(unit_entries, list):
                        all_ends.extend(
                            e["end"] for e in unit_entries if "end" in e
                        )
        true_max_end = max(all_ends)

        cik = cf["cik"]
        tickers_json = {
            "0": {"ticker": "CRST", "cik_str": str(cik), "title": "CrossTag Test Corp"}
        }
        responses = iter([_mock_resp(tickers_json), _mock_resp(cf)])

        import ai_advisor

        with patch("requests.get", side_effect=lambda *a, **k: next(responses)):
            result = ai_advisor._fetch_fundamentals_for_ticker("CRST")

        if not result.get("available"):
            pytest.skip(
                f"available=False — cannot verify cross-tag union. "
                f"reason={result.get('reason')!r}"
            )
        selected_end = result["payload"]["key_facts"]["Revenues"]["end"]
        assert selected_end == true_max_end, (
            f"AC-3 union failure: selected end={selected_end!r}, "
            f"true max end across ALL candidate tags={true_max_end!r}. "
            "The producer did not union all candidate tags before picking max end."
        )


# ---------------------------------------------------------------------------
# AC-4: Payload shape preserved
# ---------------------------------------------------------------------------


class TestPayloadShape:
    """AC-4: the key_facts output dict keeps its existing logical keys and per-entry
    field shape.  Downstream consumers (Overview render, synthesis prompt) must
    see no key-name or shape change after the fix.
    """

    def test_key_facts_outer_keys_exact_current_set(self):
        """Passes on current (regression guard) — must still pass after fix.

        AC-4: key_facts outer keys must be exactly the 5 canonical logical concept
        names.  No new keys added, no existing keys removed by the fix.
        """
        fx = _load_mode_b()
        cf = fx["companyfacts"]

        import ai_advisor

        with patch("requests.get", return_value=_mock_resp(cf)):
            result = ai_advisor._fetch_fundamentals_for_ticker("JPM")

        assert result.get("available") is True, (
            f"available=False. reason={result.get('reason')!r}"
        )
        actual_keys = set(result["payload"]["key_facts"].keys())
        # The fixture has all 5 concepts so we expect exactly the full set.
        assert actual_keys == _EXPECTED_KEY_FACTS_KEYS, (
            f"key_facts outer keys changed. "
            f"Expected: {sorted(_EXPECTED_KEY_FACTS_KEYS)}, "
            f"Got: {sorted(actual_keys)}. "
            "AC-4: the fix must NOT rename, add, or remove logical concept keys."
        )

    def test_each_key_facts_entry_has_exact_field_set(self):
        """Passes on current (regression guard) — must still pass after fix.

        AC-4: each key_facts entry must have exactly {label, value, unit, end,
        filed, form}.  No extra fields added, no fields removed.
        """
        fx = _load_mode_b()
        cf = fx["companyfacts"]

        import ai_advisor

        with patch("requests.get", return_value=_mock_resp(cf)):
            result = ai_advisor._fetch_fundamentals_for_ticker("JPM")

        assert result.get("available") is True, (
            f"available=False. reason={result.get('reason')!r}"
        )
        key_facts = result["payload"]["key_facts"]
        for concept, entry in key_facts.items():
            actual_fields = set(entry.keys())
            assert actual_fields == _EXPECTED_ENTRY_FIELDS, (
                f"key_facts['{concept}'] field set changed. "
                f"Expected: {sorted(_EXPECTED_ENTRY_FIELDS)}, "
                f"Got: {sorted(actual_fields)}. "
                "AC-4: the fix must NOT add or remove fields from the per-entry dict."
            )

    def test_sources_citation_structure_preserved(self):
        """Passes on current (regression guard) — must still pass after fix.

        AC-4: sources list entries must each have {title, url, published, lens}.
        The citation structure is consumed by the synthesis prompt and Overview render.
        """
        fx = _load_mode_b()
        cf = fx["companyfacts"]

        import ai_advisor

        with patch("requests.get", return_value=_mock_resp(cf)):
            result = ai_advisor._fetch_fundamentals_for_ticker("JPM")

        assert result.get("available") is True, (
            f"available=False. reason={result.get('reason')!r}"
        )
        sources = result.get("sources", [])
        assert isinstance(sources, list), (
            f"sources must be a list, got {type(sources).__name__}"
        )
        for i, src in enumerate(sources):
            assert isinstance(src, dict), (
                f"sources[{i}] must be a dict, got {type(src).__name__}"
            )
            for field in ("title", "url", "published", "lens"):
                assert field in src, (
                    f"sources[{i}] missing field '{field}'. "
                    f"AC-4: citation structure must be preserved. Got keys: {list(src.keys())}"
                )


# ---------------------------------------------------------------------------
# AC-5: Honest degradation preserved
# ---------------------------------------------------------------------------


class TestHonestDegradation:
    """AC-5: no false freshness — missing concepts omitted, fetch failures degrade."""

    def test_concept_absent_when_no_candidate_tag_present(self):
        """Passes on current (regression guard) — must still pass after fix.

        AC-5: when no candidate tag for a logical concept is in us-gaap at all,
        that concept must be OMITTED from key_facts — never fabricated.
        """
        # Companyfacts with Assets + NetIncomeLoss but NO revenue-related tags.
        cf = {
            "cik": 1111111,
            "entityName": "NoRevenue Corp",
            "facts": {
                "us-gaap": {
                    "Assets": {
                        "label": "Assets",
                        "units": {
                            "USD": [
                                {
                                    "accn": "0001111111-24-000001",
                                    "end": "2024-12-31",
                                    "val": 4000001,
                                    "form": "10-K",
                                    "filed": "2025-01-30",
                                }
                            ]
                        },
                    },
                    "NetIncomeLoss": {
                        "label": "Net Income",
                        "units": {
                            "USD": [
                                {
                                    "accn": "0001111111-24-000001",
                                    "end": "2024-12-31",
                                    "val": 4000003,
                                    "form": "10-K",
                                    "filed": "2025-01-30",
                                }
                            ]
                        },
                    },
                }
            },
        }

        # Use a CIK-cache miss ticker so we need a tickers-JSON response first.
        tickers_json = {
            "0": {"ticker": "NREV", "cik_str": "1111111", "title": "NoRevenue Corp"}
        }
        responses = iter([_mock_resp(tickers_json), _mock_resp(cf)])

        import ai_advisor

        with patch("requests.get", side_effect=lambda *a, **k: next(responses)):
            result = ai_advisor._fetch_fundamentals_for_ticker("NREV")

        if not result.get("available"):
            pytest.skip(
                "available=False even with Assets+NetIncomeLoss present "
                "— producer requires at least one key_facts entry. "
                "This may be a pre-existing behaviour; the Revenues-omission "
                "check is still valid if available=True."
            )
        key_facts = result["payload"]["key_facts"]
        assert "Revenues" not in key_facts, (
            "AC-5: 'Revenues' concept must be ABSENT from key_facts when no "
            "revenue-related candidate tag is in the companyfacts us-gaap. "
            "A missing concept must be omitted, never fabricated."
        )

    def test_fetch_failure_returns_available_false_d1_reason(self):
        """Passes on current (regression guard) — must still pass after fix.

        AC-5: when the companyfacts HTTP fetch raises, the function must return
        available=False with a D-1 reason (type(exc).__name__ only — no stack
        trace, no URL, no message detail).
        """
        import ai_advisor
        from requests.exceptions import ConnectionError as ReqConnErr

        with patch(
            "ai_advisor._fetch_with_backoff",
            side_effect=ReqConnErr("unit-test-stub connection error"),
        ):
            result = ai_advisor._fetch_fundamentals_for_ticker("JPM")

        assert result.get("available") is False, (
            "fetch failure must return available=False. "
            f"Got available={result.get('available')!r}"
        )
        reason = result.get("reason", "")
        assert isinstance(reason, str) and reason.strip(), (
            "fetch failure must carry a non-empty reason string."
        )
        # D-1: reason must be "ConnectionError fetching SEC EDGAR fundamentals"
        # (type(exc).__name__ + context) — not a full traceback or URL.
        assert "ConnectionError" in reason, (
            f"D-1 contract: reason must start with the exception type name. "
            f"Got: {reason!r}. The fix must NOT change the D-1 error format."
        )
        # Verify no raw exception message or URL leaks into the reason.
        assert "unit-test-stub" not in reason, (
            "D-1 violation: raw exception message leaked into reason string. "
            "Only type(exc).__name__ is permitted."
        )


# ---------------------------------------------------------------------------
# AC-6: Both single-ticker and portfolio fan-out paths apply the fix
# ---------------------------------------------------------------------------


class TestBothPaths:
    """AC-6: both call paths consume the corrected selection logic."""

    def test_single_ticker_path_applies_corrected_end_sort(self):
        """FAILS on current: single-ticker path uses the buggy filed-sort.

        AC-6: _build_fundamentals_section(ticker='JPM') must return the latest
        end across all entries (Mode B fix applied on the single-ticker path).
        """
        fx = _load_mode_b()
        cf = fx["companyfacts"]

        revenues_entries = cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
        max_end = max(e["end"] for e in revenues_entries)

        import ai_advisor

        with patch("requests.get", return_value=_mock_resp(cf)):
            block = ai_advisor._build_fundamentals_section(ticker="JPM")

        assert block.get("available") is True, (
            f"_build_fundamentals_section(ticker='JPM') returned available=False. "
            f"reason={block.get('reason')!r}"
        )
        assert block.get("lens") == "fundamentals", (
            f"block['lens'] must be 'fundamentals', got {block.get('lens')!r}"
        )
        payload = block.get("payload", {})
        key_facts = payload.get("key_facts", {})
        assert "Revenues" in key_facts, "key_facts missing 'Revenues' on single-ticker path"
        selected_end = key_facts["Revenues"]["end"]
        assert selected_end == max_end, (
            f"AC-6 single-ticker path: selected end={selected_end!r}, "
            f"correct max end={max_end!r}. "
            "The single-ticker path did not receive the corrected end sort."
        )

    def test_portfolio_fanout_path_applies_corrected_end_sort(self):
        """FAILS on current: portfolio fan-out path uses the buggy filed-sort.

        AC-6: _build_fundamentals_section() with no ticker (portfolio path)
        must apply the same corrected selection to each ticker in the universe.
        Checked via the per-ticker key_facts in the aggregate payload.
        """
        fx = _load_mode_b()
        cf = fx["companyfacts"]

        revenues_entries = cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
        max_end = max(e["end"] for e in revenues_entries)

        import ai_advisor

        # Patch load_state to return empty holdings so the proxy floor drives
        # the universe; requests.get returns the Mode B fixture for every ticker.
        with (
            patch("database.load_state", return_value=_empty_holdings_state()),
            patch("requests.get", return_value=_mock_resp(cf)),
        ):
            block = ai_advisor._build_fundamentals_section()

        assert block.get("available") is True, (
            f"Portfolio fan-out returned available=False. "
            f"reason={block.get('reason')!r}. "
            "The proxy floor should guarantee ≥1 ticker resolves."
        )
        assert block.get("lens") == "fundamentals"
        tickers_payload = block["payload"].get("tickers", {})
        assert len(tickers_payload) >= 1, (
            "Portfolio payload has no per-ticker facts. "
            "Expected ≥1 ticker to resolve from the proxy floor."
        )
        # Check at least one ticker has the corrected Revenues end.
        corrected_found = False
        for ticker_name, ticker_payload in tickers_payload.items():
            kf = ticker_payload.get("key_facts", {})
            if "Revenues" in kf:
                end = kf["Revenues"]["end"]
                if end == max_end:
                    corrected_found = True
                    break
        assert corrected_found, (
            f"AC-6 portfolio path: no ticker in the payload has "
            f"Revenues.end == {max_end!r}. "
            "The portfolio fan-out path did not apply the corrected end sort."
        )


# ---------------------------------------------------------------------------
# AC-7: Never-raising on malformed payloads
# ---------------------------------------------------------------------------


class TestNeverRaising:
    """AC-7: malformed/partial companyfacts payloads must never raise.
    The producer must degrade honestly: omit the broken concept or return
    available=False — but never propagate an exception.
    """

    def _call_for_malformed(self, companyfacts_data: dict) -> dict:
        """Helper: patch requests.get to return the given companyfacts, call producer."""
        import ai_advisor

        cik = companyfacts_data.get("cik", 9999999)
        tickers_json = {
            "0": {"ticker": "MALF", "cik_str": str(cik), "title": "Malformed Corp"}
        }
        responses = iter([_mock_resp(tickers_json), _mock_resp(companyfacts_data)])
        with patch("requests.get", side_effect=lambda *a, **k: next(responses)):
            return ai_advisor._fetch_fundamentals_for_ticker("MALF")

    def test_missing_units_key_does_not_raise(self):
        """AC-7: concept data with no 'units' key — no exception."""
        cases = _load_malformed()["cases"]
        case = next(c for c in cases if c["label"] == "missing_units_key")
        result = self._call_for_malformed(case["companyfacts"])
        # Result must be a dict with an 'available' bool key — no exception.
        assert isinstance(result, dict), (
            "Producer must return a dict even for malformed input."
        )
        assert "available" in result, "Result must have 'available' key."
        assert isinstance(result["available"], bool), (
            "result['available'] must be bool."
        )

    def test_nonlist_unit_entries_does_not_raise(self):
        """AC-7: units value is a dict instead of a list — no exception."""
        cases = _load_malformed()["cases"]
        case = next(c for c in cases if c["label"] == "nonlist_unit_entries")
        result = self._call_for_malformed(case["companyfacts"])
        assert isinstance(result, dict)
        assert "available" in result
        assert isinstance(result["available"], bool)

    def test_entry_missing_end_does_not_raise(self):
        """AC-7: unit entry has no 'end' key — treated as oldest, no raise."""
        cases = _load_malformed()["cases"]
        case = next(c for c in cases if c["label"] == "entry_missing_end")
        result = self._call_for_malformed(case["companyfacts"])
        assert isinstance(result, dict)
        assert "available" in result
        assert isinstance(result["available"], bool)
        # If the entry was selected, 'end' in key_facts may be None — not a raise.
        if result.get("available"):
            kf = result["payload"]["key_facts"]
            if "Revenues" in kf:
                # The entry's missing 'end' must not have caused a raise.
                # selected end may be None — that is acceptable degradation.
                assert "end" in kf["Revenues"], (
                    "key_facts['Revenues'] must still carry 'end' key even if None."
                )

    def test_entry_missing_filed_does_not_raise(self):
        """AC-7: unit entry has 'end' but no 'filed' key — secondary sort key missing, no raise."""
        cases = _load_malformed()["cases"]
        case = next(c for c in cases if c["label"] == "entry_missing_filed")
        result = self._call_for_malformed(case["companyfacts"])
        assert isinstance(result, dict)
        assert "available" in result
        assert isinstance(result["available"], bool)

    def test_empty_us_gaap_returns_available_false_not_raise(self):
        """AC-7: empty us-gaap dict — no recognized concepts, available=False, no raise."""
        cases = _load_malformed()["cases"]
        case = next(c for c in cases if c["label"] == "empty_us_gaap")
        result = self._call_for_malformed(case["companyfacts"])
        assert isinstance(result, dict)
        assert result.get("available") is False, (
            "Empty us-gaap must return available=False — no facts to report."
        )
        reason = result.get("reason", "")
        assert isinstance(reason, str) and reason.strip(), (
            "available=False must carry a non-empty reason string."
        )


# ---------------------------------------------------------------------------
# Edge cases from the plan's Edge Cases section
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases from plan §Edge Cases not fully covered by AC-1..AC-7 tests."""

    def test_10q_only_issuer_falls_back_to_all_entries_latest_end(self):
        """Edge case: no 10-K entries; falls back to all entries, latest end selected.

        Plan: 'Non-10-K-only issuer → falls back to all entries, latest end
        (existing behavior preserved, now with correct sort).'

        Current behaviour: the fallback to all entries already exists.
        But the sort-by-filed bug also affects the fallback path.  After the fix,
        the fallback must also sort by end descending.
        """
        cf = {
            "cik": 2222222,
            "entityName": "10Q Only Corp",
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "units": {
                            "USD": [
                                {
                                    "accn": "0002222222-24-q1",
                                    "end": "2024-03-31",
                                    "val": 8100001,
                                    "form": "10-Q",
                                    "filed": "2024-11-15",
                                },
                                {
                                    "accn": "0002222222-24-q3",
                                    "end": "2024-09-30",
                                    "val": 8100007,
                                    "form": "10-Q",
                                    "filed": "2024-11-15",
                                },
                                {
                                    "accn": "0002222222-23-q3",
                                    "end": "2023-09-30",
                                    "val": 8100003,
                                    "form": "10-Q",
                                    "filed": "2024-11-15",
                                },
                            ]
                        },
                    }
                }
            },
        }
        # All three entries share filed=2024-11-15; ends differ.
        # The fix must select max end = 2024-09-30, val = 8100007.
        all_entries = cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
        expected_max_end = max(e["end"] for e in all_entries)
        expected_val = next(e["val"] for e in all_entries if e["end"] == expected_max_end)

        tickers_json = {
            "0": {"ticker": "TQOC", "cik_str": "2222222", "title": "10Q Only Corp"}
        }
        responses = iter([_mock_resp(tickers_json), _mock_resp(cf)])

        import ai_advisor

        with patch("requests.get", side_effect=lambda *a, **k: next(responses)):
            result = ai_advisor._fetch_fundamentals_for_ticker("TQOC")

        assert result.get("available") is True, (
            f"10-Q-only issuer with Revenues entries should return available=True. "
            f"Got: available=False, reason={result.get('reason')!r}"
        )
        kf = result["payload"]["key_facts"]
        assert "Revenues" in kf, "Revenues missing from key_facts for 10-Q-only issuer"

        selected_end = kf["Revenues"]["end"]
        assert selected_end == expected_max_end, (
            f"10-Q-only fallback: selected end={selected_end!r}, "
            f"expected max end={expected_max_end!r}. "
            "The fallback-to-all-entries path must also sort by end descending."
        )
        selected_val = kf["Revenues"]["value"]
        assert selected_val == expected_val, (
            f"10-Q-only fallback: selected val={selected_val!r}, "
            f"expected val at max end={expected_val!r}."
        )

    def test_end_tie_uses_filed_descending_as_tiebreak(self):
        """Edge case: all entries share the same end; latest filed wins.

        Plan: 'All entries share the same end (genuine restatement) → secondary
        filed-desc tiebreak prefers the most recently filed.'

        Two 10-K entries: same end=2024-12-31 but different filed dates.
        The most recently filed (filed=2025-03-01) should be selected over the
        earlier filing (filed=2025-01-15).  Each has a distinct val sentinel
        so the test can verify which was selected without hardcoding the value.
        """
        cf = {
            "cik": 3333333,
            "entityName": "Restatement Corp",
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "units": {
                            "USD": [
                                {
                                    "accn": "0003333333-25-000001",
                                    "end": "2024-12-31",
                                    "val": 7700001,
                                    "form": "10-K",
                                    "filed": "2025-01-15",
                                },
                                {
                                    "accn": "0003333333-25-000002",
                                    "end": "2024-12-31",
                                    "val": 7700007,
                                    "form": "10-K",
                                    "filed": "2025-03-01",
                                },
                            ]
                        },
                    }
                }
            },
        }
        entries = cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
        # All share the same end; the tiebreak picks the most recent filed.
        max_filed = max(e["filed"] for e in entries)
        expected_val = next(e["val"] for e in entries if e["filed"] == max_filed)

        tickers_json = {
            "0": {"ticker": "REST", "cik_str": "3333333", "title": "Restatement Corp"}
        }
        responses = iter([_mock_resp(tickers_json), _mock_resp(cf)])

        import ai_advisor

        with patch("requests.get", side_effect=lambda *a, **k: next(responses)):
            result = ai_advisor._fetch_fundamentals_for_ticker("REST")

        assert result.get("available") is True, (
            f"Restatement fixture should return available=True. "
            f"Got: {result.get('reason')!r}"
        )
        kf = result["payload"]["key_facts"]
        assert "Revenues" in kf

        selected_val = kf["Revenues"]["value"]
        assert selected_val == expected_val, (
            f"End-tie tiebreak: selected val={selected_val!r}, "
            f"expected val (most-recently-filed entry)={expected_val!r}. "
            "When end ties, the secondary filed-descending sort must pick the "
            "most recently filed entry."
        )
        # Also assert the end itself is the shared end (sanity).
        selected_end = kf["Revenues"]["end"]
        all_ends = {e["end"] for e in entries}
        assert selected_end in all_ends, (
            f"selected end={selected_end!r} is not in the fixture entries."
        )
