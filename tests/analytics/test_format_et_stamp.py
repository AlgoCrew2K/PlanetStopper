"""The one ET time formatter: Python, the Jinja filter and the page script agree.

Subject: ``analytics.format_et_stamp(value, *, naive_is_utc=True, date_only=False) -> str``, the
Jinja filter ``et_stamp`` that calls it, and ``fmtEtStamp(value, opts)`` in ``static/ai_advisor.js``
with ``opts = {naiveIsUtc, dateOnly}`` (both default to the Python defaults).

Contract under test:

* Output is ``YYYY-MM-DD HH:MM ET`` in America/New_York, labelled ``ET`` in summer and winter
  time; minutes are truncated, never rounded; no seconds, no fractions.
* Input is an instant in the shapes the producers write: SQLite ``datetime('now')`` text with no
  zone (read as UTC), ISO with ``Z``, ISO with an offset, a six-digit fraction, and GDELT's
  compact ``YYYYMMDDTHHMMSSZ``.
* ``naive_is_utc=False`` is for feed publish times: a value with no zone prints its date only,
  unconverted; a value with a zone converts as usual.
* ``date_only=True`` prints the ET date of the instant.
* A calendar date with no time of day is not an instant and is returned unchanged.
* ``None``, empty, blank and unparseable values return the missing-value character the Advisor
  page already prints (an em dash, U+2014); the function never raises and never echoes its input.

Expected strings are derived from the fixture value by stated calendar arithmetic (New York is UTC-4
while summer time is in force, UTC-5 otherwise), never copied from a run of the code under test.
Fixture provenance: ``tests/fixtures/ai_advisor/ux_overhaul_02/PROVENANCE.md``.
"""

from __future__ import annotations

import inspect
import json
import pathlib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

import analytics
from tests._ai_advisor_page_harness import call_exports

REPO = pathlib.Path(__file__).resolve().parents[2]
FIX_DIR = REPO / "tests" / "fixtures" / "ai_advisor" / "ux_overhaul_02"
VALUES = json.loads((FIX_DIR / "time_values.json").read_text(encoding="utf-8"))

MISSING = "—"  # the missing-value character the Advisor page prints for an absent value


def _fmt(value, **kw):
    return analytics.format_et_stamp(value, **kw)


def _et(utc: datetime, offset_hours: int) -> str:
    return (utc + timedelta(hours=offset_hours)).strftime("%Y-%m-%d %H:%M") + " ET"


# (fixture key, how the stored text parses to a UTC instant, New York offset in force that day)
# Summer time (UTC-4) ran 2026-03-08 to 2026-11-01, so every fixture date below is UTC-4.
_FIXTURE_CASES = [
    ("sqlite_created_at", "%Y-%m-%d %H:%M:%S", -4),
    ("iso_z_run_at", "%Y-%m-%dT%H:%M:%SZ", -4),
    ("prism_created_at", "%Y-%m-%d %H:%M:%S", -4),
    ("autotune_run_timestamp", "%Y-%m-%dT%H:%M:%S.%fZ", -4),
    ("gdelt_seendate", "%Y%m%dT%H%M%SZ", -4),
]


def _fixture_expected(key: str, fmt: str, offset: int) -> str:
    return _et(datetime.strptime(VALUES[key], fmt), offset)


# ---------------------------------------------------------------------------
# The fixture values are what their producers write
# ---------------------------------------------------------------------------


class TestFixtureValuesAreProducerOutput:
    def test_stored_values_equal_the_captured_row(self):
        row = json.loads(
            (REPO / "tests" / "fixtures" / "retirement" / "run_summary_captured.json").read_text(
                encoding="utf-8"
            )
        )[0]
        assert VALUES["sqlite_created_at"] == row["created_at"]
        assert VALUES["iso_z_run_at"] == json.loads(row["raw_response"])["run_at"]

    def test_prism_created_at_equals_the_captured_prism_row(self):
        row = json.loads(
            (FIX_DIR.parent / "rf1_lens_pipeline_row77.json").read_text(encoding="utf-8")
        )
        assert VALUES["prism_created_at"] == row["created_at"]

    def test_autotune_run_timestamp_is_the_writers_expression(self):
        pinned = datetime(2026, 10, 17, 1, 8, 12, 48917, tzinfo=UTC)
        assert VALUES["autotune_run_timestamp"] == pinned.isoformat().replace("+00:00", "Z")

    def test_lens_cache_captured_at_is_the_writers_expression(self):
        pinned = datetime(2026, 10, 22, 18, 50, 25, 123456, tzinfo=UTC)
        assert VALUES["lens_cache_captured_at"] == pinned.isoformat()

    def test_rss_published_is_the_feed_producers_output(self):
        from advisors import news_corpus

        body = (FIX_DIR.parent / "fed_press.xml").read_bytes()

        class _Reply:
            content = body

            def raise_for_status(self):
                return None

        with patch.object(news_corpus.requests, "get", return_value=_Reply()):
            articles = news_corpus._fetch_rss_feed("fed_press", "https://example.invalid/f", "ua")
        assert articles, "the producer returned no article for the captured feed"
        assert VALUES["rss_published"] == articles[0]["published"]

    def test_gdelt_seendate_is_the_captured_seendate(self):
        capture = json.loads(
            (REPO / "tests" / "fixtures" / "math" / "gdelt_artlist_events_captured.json").read_text(
                encoding="utf-8"
            )
        )
        assert VALUES["gdelt_seendate"] == capture["articles"][0]["seendate"]


# ---------------------------------------------------------------------------
# Python contract
# ---------------------------------------------------------------------------


class TestPythonContract:
    def test_signature(self):
        params = inspect.signature(analytics.format_et_stamp).parameters
        assert list(params)[0] == "value"
        assert params["naive_is_utc"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["naive_is_utc"].default is True
        assert params["date_only"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["date_only"].default is False

    @pytest.mark.parametrize(("key", "fmt", "offset"), _FIXTURE_CASES)
    def test_each_stored_shape_converts_to_et(self, key, fmt, offset):
        assert _fmt(VALUES[key]) == _fixture_expected(key, fmt, offset)

    def test_a_fraction_with_an_offset_form_converts(self):
        pinned = datetime(2026, 10, 22, 18, 50, 25, 123456)
        assert _fmt(VALUES["lens_cache_captured_at"]) == _et(pinned, -4)

    def test_the_date_boundary_moves_the_et_date_back(self):
        # 2026-10-17 01:08 UTC is 2026-10-16 21:08 in New York.
        assert _fmt(VALUES["autotune_run_timestamp"]) == "2026-10-16 21:08 ET"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # Spring forward 2026-03-08: 02:00 EST becomes 03:00 EDT at 07:00 UTC.
            ("2026-03-08 06:59:00", "2026-03-08 01:59 ET"),
            ("2026-03-08 07:00:00", "2026-03-08 03:00 ET"),
            # Fall back 2026-11-01: 02:00 EDT becomes 01:00 EST at 06:00 UTC.
            ("2026-11-01 05:59:00", "2026-11-01 01:59 ET"),
            ("2026-11-01 06:00:00", "2026-11-01 01:00 ET"),
            # Winter time is UTC-5; seconds are dropped, never rounded up.
            ("2026-01-15 17:30:59", "2026-01-15 12:30 ET"),
            ("2026-01-15T17:30:59Z", "2026-01-15 12:30 ET"),
        ],
    )
    def test_each_side_of_a_summer_time_change(self, value, expected):
        assert _fmt(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2026-10-22T14:50:08-04:00", "2026-10-22 14:50 ET"),
            ("2026-10-22T20:50:08+02:00", "2026-10-22 14:50 ET"),
            ("2026-10-22T18:50:08+00:00", "2026-10-22 14:50 ET"),
            ("2026-10-22T18:50:08.999999Z", "2026-10-22 14:50 ET"),
        ],
    )
    def test_offsets_and_fractions(self, value, expected):
        assert _fmt(value) == expected

    def test_a_zone_less_value_with_a_t_separator_reads_as_utc(self):
        assert _fmt("2026-10-22T18:50:25") == "2026-10-22 14:50 ET"

    def test_date_only_flag_gives_the_et_date(self):
        assert _fmt(VALUES["autotune_run_timestamp"], date_only=True) == "2026-10-16"
        assert _fmt("2026-10-17 01:08:12", date_only=True) == "2026-10-16"
        assert _fmt(VALUES["iso_z_run_at"], date_only=True) == "2026-10-22"

    def test_a_publish_time_without_a_zone_prints_its_date_only_unconverted(self):
        # 02:00 UTC would be the previous ET date if it were converted; it is not.
        assert _fmt("2026-10-22T02:00:00", naive_is_utc=False) == "2026-10-22"
        assert _fmt(VALUES["rss_published"], naive_is_utc=False) == VALUES["rss_published"][:10]

    def test_a_publish_time_with_a_zone_still_converts(self):
        assert _fmt(VALUES["gdelt_seendate"], naive_is_utc=False) == _fixture_expected(
            "gdelt_seendate", "%Y%m%dT%H%M%SZ", -4
        )
        assert _fmt("2026-10-22T18:50:08Z", naive_is_utc=False) == "2026-10-22 14:50 ET"

    @pytest.mark.parametrize("kwargs", [{}, {"naive_is_utc": False}, {"date_only": True}])
    def test_a_calendar_date_is_not_an_instant_and_is_returned_unchanged(self, kwargs):
        assert _fmt("2026-10-21", **kwargs) == "2026-10-21"

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "   ",
            "not a time",
            "2026-13-45 99:99:99",
            "2026-02-30 10:00:00",
            "2026-10-22T25:00:00Z",
            "<script>alert(1)</script>",
            12345,
            [],
            {},
        ],
    )
    @pytest.mark.parametrize("kwargs", [{}, {"naive_is_utc": False}, {"date_only": True}])
    def test_missing_and_unparseable_values_read_as_the_dash_and_are_never_echoed(
        self, value, kwargs
    ):
        result = _fmt(value, **kwargs)
        assert result == MISSING
        if isinstance(value, str) and value.strip():
            assert value not in result

    def test_the_result_is_always_text(self):
        for value in (None, "", VALUES["sqlite_created_at"], "2026-10-21", "junk"):
            assert isinstance(_fmt(value), str)


# ---------------------------------------------------------------------------
# The Jinja filter
# ---------------------------------------------------------------------------


class TestJinjaFilter:
    def test_the_filter_is_registered_and_calls_the_formatter(self):
        import app as app_module

        filt = app_module.app.jinja_env.filters.get("et_stamp")
        assert filt is not None, "the et_stamp filter is not registered"
        for value in (
            VALUES["sqlite_created_at"],
            VALUES["autotune_run_timestamp"],
            "2026-10-21",
            None,
            "junk",
        ):
            assert filt(value) == _fmt(value)
        assert filt(VALUES["rss_published"], naive_is_utc=False) == _fmt(
            VALUES["rss_published"], naive_is_utc=False
        )
        assert filt(VALUES["autotune_run_timestamp"], date_only=True) == _fmt(
            VALUES["autotune_run_timestamp"], date_only=True
        )

    def test_the_filter_works_in_a_template(self):
        import app as app_module

        rendered = app_module.app.jinja_env.from_string(
            "{{ v | et_stamp }}|{{ w | et_stamp(date_only=True) }}|{{ x | et_stamp }}"
        ).render(v=VALUES["sqlite_created_at"], w=VALUES["autotune_run_timestamp"], x=None)
        assert rendered == f"2026-10-22 14:50 ET|2026-10-16|{MISSING}"

    def test_the_tenure_review_filter_agrees_with_the_shared_formatter_on_stored_instants(self):
        # The review panel already prints ET stamps through its own filter; both must give one
        # text for the same stored instant, or one panel shows a different time from the other.
        import app as app_module

        tenure = app_module.app.jinja_env.filters["tenure_eastern"]
        for key in ("iso_z_run_at", "autotune_run_timestamp", "lens_cache_captured_at"):
            assert tenure(VALUES[key]) == _fmt(VALUES[key]), key


# ---------------------------------------------------------------------------
# JavaScript parity
# ---------------------------------------------------------------------------

_PARITY_INPUTS = [VALUES[k] for k, _f, _o in _FIXTURE_CASES] + [
    VALUES["lens_cache_captured_at"],
    VALUES["rss_published"],
    "2026-03-08 06:59:00",
    "2026-03-08 07:00:00",
    "2026-11-01 05:59:00",
    "2026-11-01 06:00:00",
    "2026-01-15 17:30:59",
    "2026-10-22T14:50:08-04:00",
    "2026-10-22T20:50:08+02:00",
    "2026-10-22T18:50:08.999999Z",
    "2026-10-22T02:00:00",
    "2026-10-21",
    None,
    "",
    "   ",
    "not a time",
    "2026-13-45 99:99:99",
    "2026-02-30 10:00:00",
    "<script>alert(1)</script>",
]
_OPTION_SETS = [
    ({}, {}),
    ({"naive_is_utc": False}, {"naiveIsUtc": False}),
    ({"date_only": True}, {"dateOnly": True}),
    ({"naive_is_utc": False, "date_only": True}, {"naiveIsUtc": False, "dateOnly": True}),
]


class TestJavaScriptParity:
    def test_the_page_script_defines_fmt_et_stamp(self):
        result = call_exports([{"fn": "fmtEtStamp", "args": [VALUES["sqlite_created_at"]]}])[0]
        assert "missing" not in result, "static/ai_advisor.js defines no fmtEtStamp"
        assert "error" not in result, result

    @pytest.mark.parametrize(("py_kwargs", "js_opts"), _OPTION_SETS)
    def test_every_input_gives_the_python_text(self, py_kwargs, js_opts):
        calls = [{"fn": "fmtEtStamp", "args": [v, js_opts]} for v in _PARITY_INPUTS]
        results = call_exports(calls)
        for value, result in zip(_PARITY_INPUTS, results, strict=True):
            assert "error" not in result and "missing" not in result, (value, result)
            assert result["value"] == _fmt(value, **py_kwargs), (value, py_kwargs, result)

    def test_the_default_options_are_the_python_defaults(self):
        calls = [
            {"fn": "fmtEtStamp", "args": [VALUES["sqlite_created_at"]]},
            {"fn": "fmtEtStamp", "args": [VALUES["sqlite_created_at"], {}]},
            {"fn": "fmtEtStamp", "args": [VALUES["sqlite_created_at"], None]},
        ]
        values = {r.get("value") for r in call_exports(calls)}
        assert values == {_fmt(VALUES["sqlite_created_at"])}

    def test_the_script_never_throws_on_odd_input(self):
        odd = [None, "", 12345, [], {}, True, "x" * 10000]
        results = call_exports([{"fn": "fmtEtStamp", "args": [v]} for v in odd])
        for value, result in zip(odd, results, strict=True):
            assert result.get("value") == MISSING, (value, result)
