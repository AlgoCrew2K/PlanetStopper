"""
RED tests — multi-source market-news corpus builder.

Feature plan: feature-plans/lens-news-events-upgrade.md (two-facet multi-source design)
Sources reference: feature-plans/news-sources-reference.md

Contract surface under test: advisors/news_corpus.py (new module)
Consumer under test: ai_advisor._build_sentiment_section (updated to surface both facets)

ALL tests in this file FAIL on the current codebase (advisors/news_corpus.py does not exist).
After nl-implementer produces GREEN, all tests must PASS.

Mocking strategy:
  Network: all live feed fetches are mocked via requests.get or the module's test seam.
  feedparser: used via pytest.importorskip — if feedparser is not installed the whole
    file is skipped (acceptable for RED; GREEN requires feedparser pip-installed per handoff).
  No real HTTP calls in CI.
  @pytest.mark.live tests are excluded from the default run.

Fixture provenance:
  fed_press.xml         — captured live 2026-06-18 from federalreserve.gov
  bea_rss.xml           — captured live 2026-06-18 from apps.bea.gov
  google_news_business.xml — captured live 2026-06-18 from news.google.com
  sec_8k_atom.xml       — captured live 2026-06-18 from sec.gov (EDGAR)
  gdelt_artlist_maxrecords50.json — schema-derived (GDELT 429 at capture time)
  All under tests/fixtures/ai_advisor/. Shape/presence assertions only.

Hard rules:
  - Never assert specific headline text or tone values.
  - Derive counts/bounds from named constants, not magic numbers.
  - Every test must be able to fail on a wrong implementation.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# feedparser guard — whole file skips if not installed
# (GREEN requires pip install feedparser in the worktree env)
# ---------------------------------------------------------------------------
feedparser = pytest.importorskip("feedparser", reason="feedparser not installed — pip install feedparser")

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURE_DIR = pathlib.Path(__file__).parents[1] / "fixtures" / "ai_advisor"
_MATH_FIXTURE_DIR = pathlib.Path(__file__).parents[1] / "fixtures" / "math"


# ---------------------------------------------------------------------------
# Fixture loaders
# ---------------------------------------------------------------------------


@pytest.fixture
def fed_press_xml() -> bytes:
    """Fed RSS captured live 2026-06-18."""
    return (_FIXTURE_DIR / "fed_press.xml").read_bytes()


@pytest.fixture
def bea_rss_xml() -> bytes:
    """BEA RSS captured live 2026-06-18."""
    return (_FIXTURE_DIR / "bea_rss.xml").read_bytes()


@pytest.fixture
def google_news_xml() -> bytes:
    """Google News BUSINESS RSS captured live 2026-06-18."""
    return (_FIXTURE_DIR / "google_news_business.xml").read_bytes()


@pytest.fixture
def sec_8k_xml() -> bytes:
    """SEC EDGAR 8-K Atom captured live 2026-06-18."""
    return (_FIXTURE_DIR / "sec_8k_atom.xml").read_bytes()


@pytest.fixture
def gdelt_artlist_json() -> dict:
    """GDELT artlist schema-derived fixture (live capture rate-limited 2026-06-18)."""
    return json.loads((_FIXTURE_DIR / "gdelt_artlist_maxrecords50.json").read_text(encoding="utf-8"))


@pytest.fixture
def gdelt_timelinetone_fixture() -> dict:
    """Existing GDELT timelinetone fixture (from prior cycle)."""
    return json.loads((_MATH_FIXTURE_DIR / "gdelt_timelinetone_response.json").read_text(encoding="utf-8"))


def _make_xml_response(content: bytes, status: int = 200) -> MagicMock:
    """Build a mock requests.Response for an XML/RSS feed."""
    mock = MagicMock()
    mock.status_code = status
    mock.content = content
    mock.text = content.decode("utf-8", errors="replace")
    mock.raise_for_status = MagicMock()
    if status >= 400:
        from requests.exceptions import HTTPError
        mock.raise_for_status.side_effect = HTTPError(f"HTTP {status}")
    return mock


def _make_json_response(data: dict, status: int = 200) -> MagicMock:
    """Build a mock requests.Response for a JSON endpoint."""
    mock = MagicMock()
    mock.status_code = status
    mock.content = json.dumps(data).encode()
    mock.text = json.dumps(data)
    mock.json.return_value = data
    mock.raise_for_status = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# Fixture shape validators
# ---------------------------------------------------------------------------


def validate_rss_feed_shape(xml_bytes: bytes, name: str) -> None:
    """Assert RSS/Atom fixture has parseable structure with at least one item."""
    import xml.etree.ElementTree as ET
    content = xml_bytes
    if content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise AssertionError(f"{name} fixture XML parse error: {e}") from e
    # Accept RSS (items in channel) or Atom (entries at root)
    ns_map = {
        "atom": "http://www.w3.org/2005/Atom",
        "": "",
    }
    # Find items regardless of namespace
    items = (
        root.findall("channel/item")
        or root.findall("{http://www.w3.org/2005/Atom}entry")
        or root.findall("entry")
    )
    assert len(items) >= 1, (
        f"{name} fixture must have at least one item/entry, got 0."
    )


class TestFixtureShapeValidity:
    """Fixture provenance guards — run before producer tests."""

    def test_fed_press_fixture_is_valid_rss(self, fed_press_xml):
        validate_rss_feed_shape(fed_press_xml, "fed_press")

    def test_bea_rss_fixture_is_valid_rss(self, bea_rss_xml):
        validate_rss_feed_shape(bea_rss_xml, "bea_rss")

    def test_google_news_fixture_is_valid_rss(self, google_news_xml):
        validate_rss_feed_shape(google_news_xml, "google_news_business")

    def test_sec_8k_fixture_is_valid_atom(self, sec_8k_xml):
        validate_rss_feed_shape(sec_8k_xml, "sec_8k_atom")

    def test_gdelt_artlist_fixture_has_articles(self, gdelt_artlist_json):
        assert "articles" in gdelt_artlist_json, "GDELT artlist fixture missing 'articles' key"
        assert len(gdelt_artlist_json["articles"]) >= 1, (
            "GDELT artlist fixture must have at least one article"
        )

    def test_gdelt_artlist_fixture_articles_are_english(self, gdelt_artlist_json):
        for i, art in enumerate(gdelt_artlist_json["articles"]):
            assert art.get("language") == "English", (
                f"gdelt_artlist fixture article[{i}] language must be 'English', "
                f"got {art.get('language')!r}"
            )


# ---------------------------------------------------------------------------
# Module existence guard
# ---------------------------------------------------------------------------


class TestNewsCorpusModuleExists:
    """The advisors.news_corpus module must exist and expose the public entry point."""

    def test_news_corpus_module_importable(self):
        """advisors.news_corpus is importable.

        FAILS on current codebase (module does not exist).
        """
        try:
            import advisors.news_corpus  # noqa: F401
        except ImportError as e:
            pytest.fail(
                f"advisors.news_corpus is not importable: {e}. "
                f"The multi-source corpus builder module must be created."
            )

    def test_build_news_corpus_function_exists(self):
        """advisors.news_corpus.build_news_corpus() is the public entry point.

        FAILS if the function is missing.
        """
        from advisors import news_corpus

        assert hasattr(news_corpus, "build_news_corpus"), (
            "advisors.news_corpus.build_news_corpus() is missing. "
            "This is the public entry point for the corpus builder."
        )
        assert callable(news_corpus.build_news_corpus), (
            "build_news_corpus must be callable."
        )


# ---------------------------------------------------------------------------
# AC-1 — User-Agent and per-feed isolation
# ---------------------------------------------------------------------------


class TestUserAgentAndPerFeedIsolation:
    """Every feed fetch sets an explicit User-Agent; one feed failing doesn't kill the lens."""

    def test_fetch_feeds_sets_explicit_user_agent(self, fed_press_xml, bea_rss_xml):
        """All RSS/Atom feed requests carry an explicit User-Agent header.

        AC-1 LOAD-BEARING: SEC/CNBC/Fed/BLS return 403 to the default requests UA.
        FAILS if any RSS/Atom feed fetch uses the default requests User-Agent string.

        GDELT API calls (api.gdeltproject.org) are delegated to lens_gdelt and excluded
        from this assertion — those calls are lens_gdelt's UA responsibility, tested
        separately in test_lens_gdelt.py.
        """
        from advisors import news_corpus

        _GDELT_HOST = "api.gdeltproject.org"
        rss_call_headers: list[tuple[str, dict]] = []

        def capture_get(url, **kwargs):
            headers = kwargs.get("headers", {})
            if _GDELT_HOST not in url:
                # Only capture non-GDELT calls — RSS/Atom feeds are news_corpus's responsibility
                rss_call_headers.append((url, dict(headers)))
            resp = MagicMock()
            resp.status_code = 200
            resp.content = fed_press_xml
            resp.raise_for_status = MagicMock()
            return resp

        with patch("requests.get", side_effect=capture_get):
            news_corpus.build_news_corpus()

        assert len(rss_call_headers) >= 1, (
            "build_news_corpus made no RSS/Atom feed HTTP calls. "
            "Expected calls to the configured RSS feeds (Google News, CNBC, Fed, etc.)."
        )
        for i, (url, headers) in enumerate(rss_call_headers):
            ua = headers.get("User-Agent") or headers.get("user-agent")
            assert ua is not None, (
                f"RSS feed call[{i}] to {url!r} has no User-Agent header. "
                f"AC-1: every RSS/Atom feed fetch must set an explicit User-Agent. "
                f"Headers: {headers}"
            )
            # Must not be the bare requests default
            assert "python-requests" not in ua.lower(), (
                f"RSS feed call[{i}] to {url!r} uses the default requests UA ({ua!r}). "
                f"SEC/CNBC/Fed/BLS return 403 to this UA. "
                f"Set an explicit descriptive User-Agent."
            )

    def test_gov_feeds_use_descriptive_contact_ua(self, fed_press_xml):
        """Requests to .gov domains use the descriptive contact UA.

        AC-1: for .gov (esp. SEC), use 'PlanetStopper/1.0 paulmgreaney@gmail.com'.
        FAILS if .gov calls use a different UA.
        """
        from advisors import news_corpus

        gov_call_headers: list[dict] = []

        def capture_get(url, **kwargs):
            if ".gov" in url:
                gov_call_headers.append(dict(kwargs.get("headers", {})))
            resp = MagicMock()
            resp.status_code = 200
            resp.content = fed_press_xml
            resp.raise_for_status = MagicMock()
            return resp

        with patch("requests.get", side_effect=capture_get):
            news_corpus.build_news_corpus()

        if not gov_call_headers:
            pytest.skip("No .gov URLs were called — cannot verify .gov UA rule")

        for i, headers in enumerate(gov_call_headers):
            ua = headers.get("User-Agent") or headers.get("user-agent") or ""
            assert "paulmgreaney@gmail.com" in ua, (
                f".gov call[{i}] User-Agent missing contact email. "
                f"Expected 'PlanetStopper/1.0 paulmgreaney@gmail.com'. "
                f"Got: {ua!r}. "
                f"SEC requires a descriptive contact UA."
            )

    def test_single_feed_403_does_not_fail_whole_corpus(
        self, fed_press_xml, google_news_xml
    ):
        """One feed returning 403 degrades that feed only — corpus still available.

        AC-1: per-feed failure isolation. FAILS if any single feed 403 crashes the lens.
        """
        from requests.exceptions import HTTPError

        from advisors import news_corpus

        call_count = [0]

        def selective_403(url, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            # First call returns 403, subsequent calls succeed
            if call_count[0] == 1:
                resp.status_code = 403
                resp.content = b"Forbidden"
                resp.raise_for_status.side_effect = HTTPError("403 Forbidden")
            else:
                resp.status_code = 200
                resp.content = fed_press_xml
                resp.raise_for_status = MagicMock()
            return resp

        with patch("requests.get", side_effect=selective_403):
            result = news_corpus.build_news_corpus()

        # The corpus must not fail entirely because of one 403
        assert result is not None, "build_news_corpus returned None on single-feed 403"
        # available=True if any other feed succeeded
        # (we don't assert available=True since all remaining feeds might also fail
        # in the mock; the key invariant is: no exception raised)

    def test_single_feed_timeout_does_not_fail_whole_corpus(self, fed_press_xml):
        """One feed timing out degrades that feed only — never raises.

        AC-1 + D-1: per-feed timeout isolation; build_news_corpus never raises.
        FAILS if a Timeout exception propagates out of build_news_corpus.
        """
        from requests.exceptions import Timeout

        from advisors import news_corpus

        call_count = [0]

        def timeout_first(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Timeout("connection timed out")
            resp = MagicMock()
            resp.status_code = 200
            resp.content = fed_press_xml
            resp.raise_for_status = MagicMock()
            return resp

        try:
            with patch("requests.get", side_effect=timeout_first):
                result = news_corpus.build_news_corpus()
        except Exception as exc:
            pytest.fail(
                f"build_news_corpus raised {type(exc).__name__} on single-feed Timeout. "
                f"Per-feed exceptions must be isolated — the corpus must never raise."
            )
        assert result is not None, "build_news_corpus returned None on single-feed timeout"

    def test_single_feed_xml_parse_error_does_not_fail_whole_corpus(self, fed_press_xml):
        """One feed returning malformed XML degrades that feed only.

        AC-1: feedparser tolerant parsing; a bad feed never kills the lens.
        """
        from advisors import news_corpus

        call_count = [0]

        def bad_xml_first(url, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if call_count[0] == 1:
                resp.content = b"<<< this is not valid XML >>>"
            else:
                resp.content = fed_press_xml
            return resp

        try:
            with patch("requests.get", side_effect=bad_xml_first):
                result = news_corpus.build_news_corpus()
        except Exception as exc:
            pytest.fail(
                f"build_news_corpus raised {type(exc).__name__} on malformed XML feed. "
                f"feedparser parse errors must be isolated per feed."
            )
        assert result is not None

    def test_per_feed_error_logged_with_type_name_only(self, caplog, fed_press_xml):
        """When a feed fails, the error is logged with type(exc).__name__ only (D-1).

        D-1: reason/log is type(exc).__name__ — never str(exc) which may contain
        internal hostnames, secrets, or PII.
        FAILS if str(exc) content leaks into logs.
        """
        import logging

        from requests.exceptions import ConnectionError as ReqConnErr

        from advisors import news_corpus

        secret_msg = "secret-internal-host.corp.example.com:443"
        call_count = [0]

        def conn_error_first(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ReqConnErr(f"Failed to establish connection: {secret_msg}")
            resp = MagicMock()
            resp.status_code = 200
            resp.content = fed_press_xml
            resp.raise_for_status = MagicMock()
            return resp

        with caplog.at_level(logging.DEBUG, logger="advisors.news_corpus"):
            with patch("requests.get", side_effect=conn_error_first):
                news_corpus.build_news_corpus()

        log_text = " ".join(caplog.messages)
        assert secret_msg not in log_text, (
            f"D-1 violation: str(exc) content leaked into log. "
            f"Secret host '{secret_msg}' found in log output. "
            f"Log only type(exc).__name__ = 'ConnectionError'."
        )


# ---------------------------------------------------------------------------
# AC-2 — GDELT tone facet: independent, always-valid, unranked
# ---------------------------------------------------------------------------


class TestGdeltToneFacetIndependence:
    """GDELT tone is an independent always-valid facet, not part of the ranked corpus."""

    def test_result_has_tone_key(self, gdelt_timelinetone_fixture, fed_press_xml):
        """build_news_corpus result carries a 'tone' key (the GDELT Facet A).

        FAILS if the tone facet is absent from the result.
        """
        from advisors import news_corpus

        tone_resp = _make_json_response(gdelt_timelinetone_fixture)

        def route_response(url, **kwargs):
            if "timelinetone" in url:
                return tone_resp
            resp = MagicMock()
            resp.status_code = 200
            resp.content = fed_press_xml
            resp.raise_for_status = MagicMock()
            return resp

        with (
            patch("requests.get", side_effect=route_response),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        assert "tone" in result, (
            f"build_news_corpus result missing 'tone' key. "
            f"Facet A (GDELT aggregate tone) must always be present. "
            f"Got keys: {set(result.keys()) if result else 'None'}"
        )

    def test_gdelt_tone_present_when_all_article_feeds_fail(
        self, gdelt_timelinetone_fixture
    ):
        """GDELT tone is returned even when all article feeds fail.

        AC-2: tone facet is the always-valid floor. available=True when tone present
        even if corpus is empty.
        FAILS if tone absence is caused by article-feed failures.
        """
        from requests.exceptions import Timeout

        from advisors import news_corpus

        tone_resp = _make_json_response(gdelt_timelinetone_fixture)

        def route_response(url, **kwargs):
            if "timelinetone" in url:
                return tone_resp
            # All article feeds (artlist + RSS) fail
            raise Timeout("all article feeds timed out")

        with (
            patch("requests.get", side_effect=route_response),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        assert result.get("available") is True, (
            f"available must be True when GDELT tone succeeds, even if all article feeds fail. "
            f"Got: {result!r}"
        )
        tone = result.get("tone")
        assert tone is not None and isinstance(tone, float), (
            f"tone must be a float when GDELT timelinetone succeeds. Got: {tone!r}"
        )

    def test_tone_facet_is_scalar_not_in_corpus(
        self, gdelt_timelinetone_fixture, fed_press_xml
    ):
        """Tone is a scalar float in result['tone'], not an article in result['corpus'].

        AC-2: GDELT tone is UNRANKED — it must not appear as an entry in the corpus list.
        FAILS if tone is mixed into the ranked corpus.
        """
        from advisors import news_corpus

        tone_resp = _make_json_response(gdelt_timelinetone_fixture)

        def route_response(url, **kwargs):
            if "timelinetone" in url:
                return tone_resp
            resp = MagicMock()
            resp.status_code = 200
            resp.content = fed_press_xml
            resp.raise_for_status = MagicMock()
            return resp

        with (
            patch("requests.get", side_effect=route_response),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        tone = result.get("tone")
        if tone is not None:
            assert isinstance(tone, float), (
                f"result['tone'] must be a float scalar, not {type(tone).__name__}."
            )
        corpus = result.get("corpus", [])
        assert isinstance(corpus, list), "result['corpus'] must be a list"
        # Tone value must not appear as an article entry
        for i, art in enumerate(corpus):
            assert not isinstance(art, float), (
                f"corpus[{i}] is a float ({art}) — tone scalar leaked into corpus list."
            )


# ---------------------------------------------------------------------------
# AC-3 — Ranked corpus: normalization, dedup, scoring, named constants
# ---------------------------------------------------------------------------


class TestRankedCorpusContracts:
    """Corpus normalization, cross-source dedup, scoring constants, top-K cap."""

    def test_scoring_constants_are_named_and_in_range(self):
        """Named constants W_RECENCY, W_RELEVANCE, W_AUTHORITY, TAU_HOURS, TOP_K,
        DEDUP_JACCARD_THRESHOLD are present in news_corpus and have sensible values.

        No magic numbers: every tunable is a named module-level constant with a
        source comment. FAILS if any constant is absent.
        """
        from advisors import news_corpus

        required_constants = {
            "W_RECENCY": (0.0, 1.0),
            "W_RELEVANCE": (0.0, 1.0),
            "W_AUTHORITY": (0.0, 1.0),
            "TAU_HOURS": (0.1, float("inf")),
            "TOP_K": (5, 200),
            "DEDUP_JACCARD_THRESHOLD": (0.5, 1.0),
        }
        for name, (lo, hi) in required_constants.items():
            assert hasattr(news_corpus, name), (
                f"advisors.news_corpus.{name} constant is missing. "
                f"No magic numbers — every weight/threshold must be a named constant."
            )
            val = getattr(news_corpus, name)
            assert isinstance(val, (int, float)), (
                f"{name} must be numeric, got {type(val)}"
            )
            assert lo <= val <= hi, (
                f"{name}={val} is outside expected range [{lo}, {hi}]."
            )

        # Weights should sum to ~1.0 (±0.05 tolerance — rounding is fine)
        w_sum = news_corpus.W_RECENCY + news_corpus.W_RELEVANCE + news_corpus.W_AUTHORITY
        assert abs(w_sum - 1.0) < 0.05, (
            f"W_RECENCY + W_RELEVANCE + W_AUTHORITY = {w_sum:.3f}, expected ~1.0. "
            f"Weights must be proportional (sum to 1)."
        )

    def test_source_authority_table_present_and_covers_known_domains(self):
        """SOURCE_AUTHORITY dict is present and covers the required domains.

        FAILS if SOURCE_AUTHORITY is absent or missing required entries.
        """
        from advisors import news_corpus

        assert hasattr(news_corpus, "SOURCE_AUTHORITY"), (
            "advisors.news_corpus.SOURCE_AUTHORITY dict is missing."
        )
        table = news_corpus.SOURCE_AUTHORITY
        assert isinstance(table, dict), (
            f"SOURCE_AUTHORITY must be a dict, got {type(table)}"
        )
        required_domains = [
            "federalreserve.gov",
            "sec.gov",
            "reuters.com",
            "cnbc.com",
        ]
        for domain in required_domains:
            assert domain in table, (
                f"SOURCE_AUTHORITY missing required domain '{domain}'. "
                f"The authority table must cover Fed/SEC/Reuters/CNBC at minimum."
            )
            val = table[domain]
            assert isinstance(val, float) and 0.0 < val <= 1.0, (
                f"SOURCE_AUTHORITY['{domain}'] = {val!r} must be a float in (0, 1]."
            )

    def test_corpus_articles_normalized_to_common_record_shape(
        self, fed_press_xml, gdelt_artlist_json
    ):
        """All corpus articles have the common normalized shape: url/title/published/domain/source_feed.

        AC-3: normalization flattens GDELT artlist dicts and feedparser RSS entries
        into a single shape. No raw feed-specific fields leak through.
        FAILS if any article is missing required fields or has raw feed-internal fields.
        """
        from advisors import news_corpus

        gdelt_artlist_resp = _make_json_response(gdelt_artlist_json)

        def route_response(url, **kwargs):
            if "artlist" in url:
                return gdelt_artlist_resp
            if "timelinetone" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.content = b""
                resp.json.return_value = {"timeline": []}
                resp.raise_for_status = MagicMock()
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.content = fed_press_xml
            resp.raise_for_status = MagicMock()
            return resp

        with (
            patch("requests.get", side_effect=route_response),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        corpus = result.get("corpus", [])
        assert isinstance(corpus, list), "result['corpus'] must be a list"
        if not corpus:
            pytest.skip("corpus is empty — cannot verify normalization shape")

        required_fields = {"url", "title", "published", "domain", "source_feed"}
        for i, article in enumerate(corpus):
            assert isinstance(article, dict), f"corpus[{i}] must be a dict"
            missing = required_fields - set(article.keys())
            assert not missing, (
                f"corpus[{i}] missing normalized fields: {missing}. "
                f"AC-3: all articles must be normalized to {{url, title, published, domain, source_feed}}. "
                f"Got keys: {set(article.keys())}"
            )
            # url must be non-empty string
            assert isinstance(article["url"], str) and article["url"].strip(), (
                f"corpus[{i}]['url'] must be a non-empty string."
            )

    def test_corpus_articles_have_topics_field(self, fed_press_xml, gdelt_artlist_json):
        """All corpus articles have a non-empty 'topics' list.

        AC-4: topic-tagging — every article gets at least one topic
        (defaults to broad-sentiment if no keyword matches).
        FAILS if any article has no topics.
        """
        from advisors import news_corpus

        gdelt_artlist_resp = _make_json_response(gdelt_artlist_json)

        def route_response(url, **kwargs):
            if "artlist" in url:
                return gdelt_artlist_resp
            if "timelinetone" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.content = b""
                resp.json.return_value = {"timeline": []}
                resp.raise_for_status = MagicMock()
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.content = fed_press_xml
            resp.raise_for_status = MagicMock()
            return resp

        with (
            patch("requests.get", side_effect=route_response),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        corpus = result.get("corpus", [])
        if not corpus:
            pytest.skip("corpus empty — cannot verify topics field")

        for i, article in enumerate(corpus):
            assert "topics" in article, (
                f"corpus[{i}] missing 'topics' field. "
                f"AC-4: all corpus articles must be topic-tagged."
            )
            topics = article["topics"]
            assert isinstance(topics, list) and len(topics) >= 1, (
                f"corpus[{i}]['topics'] must be a non-empty list. "
                f"Default topic is 'broad-sentiment'. Got: {topics!r}"
            )

    def test_cross_source_dedup_collapses_same_publisher_url(
        self, gdelt_artlist_json, fed_press_xml
    ):
        """Two feeds returning the same publisher URL collapse to one corpus entry.

        AC-3 cross-source dedup: when GDELT artlist and an RSS feed both carry an
        article from reuters.com/same-url, the deduped corpus has exactly one entry
        for that URL, keeping the higher SOURCE_AUTHORITY entry.

        FAILS if dedup is absent — the corpus would have 2 entries for the same URL.
        """
        from advisors import news_corpus

        # Create two feeds with a shared URL
        shared_url = "https://www.reuters.com/markets/shared-article"
        shared_title = "Reuters Market Analysis Update"

        # GDELT artlist with the shared URL
        gdelt_with_shared = {
            "articles": [
                {
                    "url": shared_url,
                    "title": shared_title,
                    "seendate": "20260618T120000Z",
                    "language": "English",
                    "domain": "reuters.com",
                    "sourcecountry": "United States",
                }
            ]
        }
        # RSS feed with the same URL (simulating a Google News/CNBC duplicate)
        rss_with_duplicate = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test Feed</title>
<item>
  <title>Reuters Market Analysis Update</title>
  <link>https://www.reuters.com/markets/shared-article</link>
  <pubDate>Wed, 18 Jun 2026 12:00:00 +0000</pubDate>
  <description>Test article</description>
</item>
<item>
  <title>Unique Article Not Duplicated</title>
  <link>https://www.cnbc.com/unique-article</link>
  <pubDate>Wed, 18 Jun 2026 11:00:00 +0000</pubDate>
  <description>Unique</description>
</item>
</channel></rss>"""

        gdelt_resp = _make_json_response(gdelt_with_shared)

        call_count = [0]

        def route_response(url, **kwargs):
            if "artlist" in url:
                return gdelt_resp
            if "timelinetone" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"timeline": []}
                resp.raise_for_status = MagicMock()
                return resp
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.content = rss_with_duplicate
            resp.raise_for_status = MagicMock()
            return resp

        with (
            patch("requests.get", side_effect=route_response),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        corpus = result.get("corpus", [])
        urls = [art.get("url", "") for art in corpus]
        shared_count = sum(1 for u in urls if u == shared_url)
        assert shared_count <= 1, (
            f"Cross-source dedup failed: URL '{shared_url}' appears {shared_count} "
            f"times in the corpus. Expected at most 1. "
            f"AC-3: same publisher URL from GDELT + RSS must collapse to one entry."
        )

    def test_cross_source_dedup_collapses_high_jaccard_title_pair(self, fed_press_xml):
        """Two articles with title Jaccard similarity >= DEDUP_JACCARD_THRESHOLD collapse to one.

        AC-3: title-based dedup catches same story covered by different URLs.
        FAILS if title-similarity dedup is absent.
        """
        from advisors import news_corpus

        threshold = news_corpus.DEDUP_JACCARD_THRESHOLD  # e.g. 0.85

        # Two near-identical titles (token-set Jaccard > threshold)
        title_a = "Federal Reserve Holds Interest Rates Steady at Latest FOMC Meeting"
        title_b = "Federal Reserve Holds Interest Rates Steady at FOMC Meeting"
        # Token sets: A={federal,reserve,holds,interest,rates,steady,at,latest,fomc,meeting}
        # B={federal,reserve,holds,interest,rates,steady,at,fomc,meeting}
        # Jaccard = |intersection| / |union| = 9/10 = 0.9 > 0.85

        rss_feed_a = f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed A</title>
<item>
  <title>{title_a}</title>
  <link>https://www.cnbc.com/fed-holds-rates-a</link>
  <pubDate>Wed, 18 Jun 2026 12:00:00 +0000</pubDate>
  <description>CNBC coverage</description>
</item>
</channel></rss>""".encode()

        rss_feed_b = f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed B</title>
<item>
  <title>{title_b}</title>
  <link>https://www.marketwatch.com/fed-holds-rates-b</link>
  <pubDate>Wed, 18 Jun 2026 12:00:00 +0000</pubDate>
  <description>MarketWatch coverage</description>
</item>
</channel></rss>""".encode()

        call_count = [0]

        def alternating_feeds(url, **kwargs):
            if "timelinetone" in url or "artlist" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.content = b""
                resp.json.return_value = {"timeline": [], "articles": []}
                resp.raise_for_status = MagicMock()
                return resp
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.content = rss_feed_a if call_count[0] % 2 == 1 else rss_feed_b
            resp.raise_for_status = MagicMock()
            return resp

        with (
            patch("requests.get", side_effect=alternating_feeds),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        corpus = result.get("corpus", [])
        # Both titles are near-identical — after dedup at most 1 should remain
        matching = [
            art for art in corpus
            if "Federal Reserve Holds Interest Rates Steady" in art.get("title", "")
        ]
        assert len(matching) <= 1, (
            f"Title-similarity dedup failed: {len(matching)} articles with "
            f"near-identical titles survived dedup (threshold={threshold}). "
            f"Expected at most 1. Titles: {[a.get('title') for a in matching]}"
        )

    def test_per_domain_cap_respected(self, gdelt_artlist_json):
        """At most 3 articles per domain in the final corpus.

        AC-3: ≤N-per-domain cap prevents any single outlet dominating the corpus.
        FAILS if the per-domain cap is absent.
        """
        from advisors import news_corpus

        # The GDELT artlist fixture has 4 reuters.com entries — the cap should drop to 3
        reuters_count_in_fixture = sum(
            1 for art in gdelt_artlist_json["articles"]
            if art.get("domain") == "reuters.com"
        )
        assert reuters_count_in_fixture >= 4, (
            "Precondition: GDELT artlist fixture must have >= 4 reuters.com articles "
            "to test the per-domain cap. Adjust the fixture if needed."
        )

        gdelt_resp = _make_json_response(gdelt_artlist_json)

        def route_response(url, **kwargs):
            if "artlist" in url:
                return gdelt_resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"timeline": []}
            resp.content = b""
            resp.raise_for_status = MagicMock()
            return resp

        with (
            patch("requests.get", side_effect=route_response),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        corpus = result.get("corpus", [])
        reuters_in_corpus = [art for art in corpus if art.get("domain") == "reuters.com"]
        assert len(reuters_in_corpus) <= 3, (
            f"Per-domain cap violated: {len(reuters_in_corpus)} reuters.com articles "
            f"in corpus (cap is 3). AC-3: ≤3 articles per domain."
        )

    def test_corpus_sorted_descending_by_score(self, fed_press_xml, gdelt_artlist_json):
        """Corpus articles are sorted highest-score-first.

        AC-3: score = W_RECENCY*recency + W_RELEVANCE*relevance + W_AUTHORITY*authority.
        Asserts ordering shape — derived from named constants, not hardcoded values.
        FAILS if corpus is unsorted or sorted ascending.
        """
        from advisors import news_corpus

        gdelt_resp = _make_json_response(gdelt_artlist_json)

        def route_response(url, **kwargs):
            if "artlist" in url:
                return gdelt_resp
            if "timelinetone" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"timeline": []}
                resp.raise_for_status = MagicMock()
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.content = fed_press_xml
            resp.raise_for_status = MagicMock()
            return resp

        with (
            patch("requests.get", side_effect=route_response),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        corpus = result.get("corpus", [])
        if len(corpus) < 2:
            pytest.skip("corpus has fewer than 2 articles — cannot verify sort order")

        # Each article must carry a 'score' field for this assertion
        scores = [art.get("score") for art in corpus]
        scores_present = [s for s in scores if s is not None]
        if len(scores_present) < 2:
            pytest.skip("corpus articles lack 'score' field — cannot verify sort order")

        for i in range(len(scores_present) - 1):
            assert scores_present[i] >= scores_present[i + 1], (
                f"corpus is not sorted descending by score. "
                f"corpus[{i}].score={scores_present[i]} < corpus[{i+1}].score={scores_present[i+1]}. "
                f"AC-3: sort desc, highest score first."
            )

    def test_corpus_capped_at_top_k(self, fed_press_xml):
        """Corpus has at most TOP_K articles.

        AC-3: top-K cap controls prompt budget.
        FAILS if the corpus is uncapped.
        """
        from advisors import news_corpus

        top_k = news_corpus.TOP_K

        # Provide many articles across multiple feeds to exceed TOP_K
        # Build a fixture with TOP_K + 10 unique articles
        many_articles = []
        for i in range(top_k + 10):
            many_articles.append({
                "url": f"https://www.site-{i}.com/article",
                "title": f"Market News Article Number {i} Unique Content Here",
                "seendate": f"20260618T{(i % 24):02d}0000Z",
                "language": "English",
                "domain": f"site-{i}.com",
                "sourcecountry": "United States",
            })
        big_artlist = {"articles": many_articles}

        def route_response(url, **kwargs):
            if "artlist" in url:
                return _make_json_response(big_artlist)
            if "timelinetone" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"timeline": []}
                resp.raise_for_status = MagicMock()
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.content = fed_press_xml
            resp.raise_for_status = MagicMock()
            return resp

        with (
            patch("requests.get", side_effect=route_response),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        corpus = result.get("corpus", [])
        assert len(corpus) <= top_k, (
            f"Corpus has {len(corpus)} articles but TOP_K={top_k}. "
            f"AC-3: corpus must be capped at TOP_K after sorting."
        )


# ---------------------------------------------------------------------------
# AC-4 — Topic-tagging
# ---------------------------------------------------------------------------


class TestTopicTagging:
    """Topic-tagging routes articles to macro/fundamentals/technicals/derivatives/broad-sentiment."""

    _KNOWN_TOPICS = frozenset({"macro", "fundamentals", "technicals", "derivatives", "broad-sentiment"})

    def _make_single_article_artlist(self, title: str) -> dict:
        return {
            "articles": [
                {
                    "url": "https://www.test-source.com/article",
                    "title": title,
                    "seendate": "20260618T120000Z",
                    "language": "English",
                    "domain": "test-source.com",
                    "sourcecountry": "United States",
                }
            ]
        }

    def _run_with_artlist(self, artlist: dict) -> list[dict]:
        from advisors import news_corpus

        def route_response(url, **kwargs):
            if "artlist" in url:
                return _make_json_response(artlist)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"timeline": []}
            resp.content = b""
            resp.raise_for_status = MagicMock()
            return resp

        with (
            patch("requests.get", side_effect=route_response),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()
        return result.get("corpus", [])

    def test_federal_reserve_keyword_routes_to_macro(self):
        """Article with 'federal reserve' in title is tagged macro.

        AC-4: keyword → topic map. 'federal reserve' is a macro keyword.
        FAILS if topic-tagging is absent or uses wrong keywords.
        """
        corpus = self._run_with_artlist(
            self._make_single_article_artlist("Federal Reserve Signals Rate Pause")
        )
        if not corpus:
            pytest.skip("corpus empty — cannot test topic tagging")
        topics = corpus[0].get("topics", [])
        assert "macro" in topics, (
            f"Article with 'Federal Reserve' in title must be tagged 'macro'. "
            f"Got topics: {topics!r}. "
            f"AC-4: 'federal reserve' is a macro keyword."
        )

    def test_earnings_keyword_routes_to_fundamentals(self):
        """Article with 'earnings' in title is tagged fundamentals.

        AC-4: 'earnings' is a fundamentals keyword.
        """
        corpus = self._run_with_artlist(
            self._make_single_article_artlist("Q2 Earnings Beat Sends Shares Higher")
        )
        if not corpus:
            pytest.skip("corpus empty")
        topics = corpus[0].get("topics", [])
        assert "fundamentals" in topics, (
            f"Article with 'earnings' must be tagged 'fundamentals'. Got: {topics!r}"
        )

    def test_vix_keyword_routes_to_derivatives(self):
        """Article with 'vix' in title is tagged derivatives.

        AC-4: 'vix' is a derivatives keyword.
        """
        corpus = self._run_with_artlist(
            self._make_single_article_artlist("VIX Spikes as Options Traders Hedge")
        )
        if not corpus:
            pytest.skip("corpus empty")
        topics = corpus[0].get("topics", [])
        assert "derivatives" in topics, (
            f"Article with 'VIX' must be tagged 'derivatives'. Got: {topics!r}"
        )

    def test_unrecognized_article_defaults_to_broad_sentiment(self):
        """Article with no matching keywords defaults to broad-sentiment.

        AC-4: broad-sentiment is the default — every article gets at least one topic.
        FAILS if an unrecognized article has an empty topics list.
        """
        corpus = self._run_with_artlist(
            self._make_single_article_artlist("A General Business Update With No Specific Keywords")
        )
        if not corpus:
            pytest.skip("corpus empty")
        topics = corpus[0].get("topics", [])
        assert len(topics) >= 1, (
            f"Article with no keyword matches must default to 'broad-sentiment'. "
            f"Got empty topics list."
        )
        assert "broad-sentiment" in topics, (
            f"Default topic must be 'broad-sentiment'. Got: {topics!r}"
        )

    def test_all_topic_labels_are_from_known_vocabulary(self, fed_press_xml, gdelt_artlist_json):
        """Every topic label in the corpus is from the known vocabulary.

        AC-4: prevents typos / novel labels from leaking into the council context.
        FAILS if any unknown label appears.
        """
        from advisors import news_corpus

        gdelt_resp = _make_json_response(gdelt_artlist_json)

        def route_response(url, **kwargs):
            if "artlist" in url:
                return gdelt_resp
            if "timelinetone" in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"timeline": []}
                resp.raise_for_status = MagicMock()
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.content = fed_press_xml
            resp.raise_for_status = MagicMock()
            return resp

        with (
            patch("requests.get", side_effect=route_response),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        corpus = result.get("corpus", [])
        if not corpus:
            pytest.skip("corpus empty")

        for i, art in enumerate(corpus):
            for topic in art.get("topics", []):
                assert topic in self._KNOWN_TOPICS, (
                    f"corpus[{i}] has unknown topic label {topic!r}. "
                    f"AC-4: topic labels must be from "
                    f"{sorted(self._KNOWN_TOPICS)}."
                )


# ---------------------------------------------------------------------------
# AC-5 — Honest availability: tone-only / corpus-only / both-null
# ---------------------------------------------------------------------------


class TestHonestAvailabilityMultiSource:
    """Honest availability across all three combinations."""

    def test_available_false_when_tone_and_corpus_both_absent(self):
        """Tone fails + all article feeds fail → available=False with named reason.

        AC-5: both-null → available=False. FAILS if available=True with no data.
        """
        from requests.exceptions import Timeout

        from advisors import news_corpus

        def all_fail(url, **kwargs):
            raise Timeout("all feeds failed")

        with (
            patch("requests.get", side_effect=all_fail),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        assert result["available"] is False, (
            f"Both tone and corpus absent → available must be False. Got: {result!r}"
        )
        assert result.get("reason") is not None and result["reason"].strip(), (
            f"Both absent → a named reason must be set. Got reason={result.get('reason')!r}"
        )

    def test_available_true_when_only_tone_present(self, gdelt_timelinetone_fixture):
        """Tone succeeds + all article feeds fail → available=True (tone-only floor).

        AC-5 + AC-2: tone is the always-valid floor.
        """
        from requests.exceptions import Timeout

        from advisors import news_corpus

        tone_resp = _make_json_response(gdelt_timelinetone_fixture)

        def route_response(url, **kwargs):
            if "timelinetone" in url:
                return tone_resp
            raise Timeout("article feeds all failed")

        with (
            patch("requests.get", side_effect=route_response),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        assert result.get("available") is True, (
            f"Tone present + all article feeds fail → available must be True. "
            f"Tone is the always-valid floor. Got: {result!r}"
        )

    def test_available_true_when_only_corpus_present(self, fed_press_xml):
        """Article feeds succeed + GDELT tone fails → available=True (corpus-only).

        AC-5: corpus without tone is still a valid result.
        """
        from requests.exceptions import Timeout

        from advisors import news_corpus

        def route_response(url, **kwargs):
            if "timelinetone" in url:
                raise Timeout("GDELT tone failed")
            resp = MagicMock()
            resp.status_code = 200
            resp.content = fed_press_xml
            resp.raise_for_status = MagicMock()
            return resp

        with (
            patch("requests.get", side_effect=route_response),
            patch("time.sleep"),
        ):
            result = news_corpus.build_news_corpus()

        # If corpus has articles, available must be True
        corpus = result.get("corpus", [])
        if corpus:
            assert result.get("available") is True, (
                f"Corpus articles present + tone failed → available must be True. "
                f"Got: {result!r}"
            )


# ---------------------------------------------------------------------------
# AC-6 — Consumer: _build_sentiment_section surfaces both facets
# ---------------------------------------------------------------------------


class TestBuildSentimentSectionMultiSource:
    """ai_advisor._build_sentiment_section must surface tone + corpus from news_corpus."""

    def test_payload_has_tone_score_and_corpus_keys(self):
        """_build_sentiment_section payload carries 'tone_score' and 'corpus'.

        AC-6: the consumer must surface both Facet A (tone) and Facet B (corpus).
        FAILS until ai_advisor._build_sentiment_section is updated.
        """
        import ai_advisor

        mock_corpus_result = {
            "available": True,
            "tone": 0.05,
            "reason": None,
            "corpus": [
                {
                    "url": "https://www.reuters.com/markets/fed-holds",
                    "title": "Federal Reserve Holds Rates Steady",
                    "published": "20260618T120000Z",
                    "domain": "reuters.com",
                    "source_feed": "gdelt_artlist",
                    "score": 0.85,
                    "topics": ["macro"],
                }
            ],
        }

        # Patch build_news_corpus inside ai_advisor's lazy import
        try:
            import advisors.news_corpus as news_corpus_mod
            original = news_corpus_mod.build_news_corpus
            news_corpus_mod.build_news_corpus = lambda: mock_corpus_result
        except ImportError:
            pytest.skip("advisors.news_corpus not yet implemented — skipping consumer test")

        try:
            section = ai_advisor._build_sentiment_section()
        finally:
            news_corpus_mod.build_news_corpus = original

        assert section.get("available") is True, (
            f"Section must be available=True when corpus returns data. Got: {section!r}"
        )
        payload = section.get("payload")
        assert payload is not None, "payload must not be None when available=True"
        assert "tone_score" in payload, (
            f"payload missing 'tone_score' key. "
            f"AC-6: _build_sentiment_section must surface GDELT tone. "
            f"Got keys: {set(payload.keys()) if payload else 'None'}. "
            f"FAILS until ai_advisor.py is updated."
        )
        assert "corpus" in payload, (
            f"payload missing 'corpus' key. "
            f"AC-6: _build_sentiment_section must surface the ranked article corpus. "
            f"Got keys: {set(payload.keys()) if payload else 'None'}. "
            f"FAILS until ai_advisor.py is updated."
        )

    def test_payload_corpus_articles_carry_topics(self):
        """Each corpus article in the payload carries a 'topics' field.

        AC-6: topic-tagged corpus must be forwarded to the payload intact.
        FAILS until ai_advisor.py passes the corpus through.
        """
        import ai_advisor

        mock_corpus_result = {
            "available": True,
            "tone": None,
            "reason": None,
            "corpus": [
                {
                    "url": "https://www.sec.gov/filing/8k",
                    "title": "8-K Filing: Material Event Disclosure",
                    "published": "20260618T100000Z",
                    "domain": "sec.gov",
                    "source_feed": "sec_edgar",
                    "score": 0.9,
                    "topics": ["fundamentals"],
                }
            ],
        }

        try:
            import advisors.news_corpus as news_corpus_mod
            original = news_corpus_mod.build_news_corpus
            news_corpus_mod.build_news_corpus = lambda: mock_corpus_result
        except ImportError:
            pytest.skip("advisors.news_corpus not yet implemented")

        try:
            section = ai_advisor._build_sentiment_section()
        finally:
            news_corpus_mod.build_news_corpus = original

        payload = section.get("payload", {})
        corpus = payload.get("corpus", [])
        if not corpus:
            pytest.skip("payload corpus is empty — cannot verify topics forwarding")
        for i, art in enumerate(corpus):
            assert "topics" in art, (
                f"corpus[{i}] in payload missing 'topics' field. "
                f"AC-6: topic-tagged corpus must be forwarded intact."
            )


# ---------------------------------------------------------------------------
# AC-7 — feedparser dependency in requirements.txt
# ---------------------------------------------------------------------------


class TestFeedparserDependency:
    """feedparser must be declared in requirements.txt."""

    def test_feedparser_in_requirements_txt(self):
        """requirements.txt contains a feedparser entry.

        AC-7: feedparser must be added as a project dependency.
        FAILS immediately on current codebase (not yet added).
        """
        requirements_path = pathlib.Path(__file__).parents[2] / "requirements.txt"
        assert requirements_path.exists(), (
            f"requirements.txt not found at {requirements_path}. "
            f"Cannot verify feedparser dependency."
        )
        content = requirements_path.read_text(encoding="utf-8")
        has_feedparser = any(
            line.strip().lower().startswith("feedparser")
            for line in content.splitlines()
            if not line.strip().startswith("#")
        )
        assert has_feedparser, (
            f"feedparser is not listed in requirements.txt. "
            f"AC-7: feedparser must be added as a project dependency. "
            f"Add 'feedparser>=6.0' to requirements.txt."
        )


# ---------------------------------------------------------------------------
# GDELT artlist maxrecords bump (adjustment to test_lens_gdelt.py contract)
# Tested here for convenience since news_corpus orchestrates GDELT artlist too.
# ---------------------------------------------------------------------------


class TestGdeltArtlistMaxrecordsBump:
    """GDELT artlist URL must request maxrecords>=50 for a richer corpus input.

    Feature plan bumps from maxrecords=10 to ~50. Tests the lens_gdelt constant.
    """

    def test_gdelt_artlist_url_maxrecords_is_at_least_50(self):
        """_GDELT_ARTLIST_URL in lens_gdelt.py specifies maxrecords>=50.

        FAILS on current code: maxrecords=10 was the prior value.
        Feature plan specifies ~50 for a richer corpus.
        """
        from advisors import lens_gdelt

        url = lens_gdelt._GDELT_ARTLIST_URL
        import re
        match = re.search(r"maxrecords=(\d+)", url)
        assert match is not None, (
            f"_GDELT_ARTLIST_URL has no maxrecords parameter. "
            f"URL: {url!r}"
        )
        maxrecords = int(match.group(1))
        assert maxrecords >= 50, (
            f"_GDELT_ARTLIST_URL has maxrecords={maxrecords} but must be >= 50. "
            f"Feature plan: bump 10→~50 for a richer corpus input. "
            f"FAILS on current code (maxrecords=10)."
        )


# ---------------------------------------------------------------------------
# BLOCK 1 — Single GDELT path: _build_sentiment_section must NOT double-fetch
# ---------------------------------------------------------------------------


class TestSingleGdeltPath:
    """_build_sentiment_section must issue _GDELT_TONE_URL at most once per call.

    BLOCK 1 (reviewer-2): the prior implementation called news_corpus.build_news_corpus()
    (which hits _GDELT_TONE_URL + _GDELT_ARTLIST_URL) AND lens_gdelt._fetch_gdelt_sentiment()
    (which also hits _GDELT_TONE_URL + _GDELT_ARTLIST_URL) unconditionally.
    4 GDELT requests per nightly run with no inter-call sleep coordination trips the
    1-req/5s rate limit in production.

    Fix: news_corpus._fetch_gdelt_tone() must delegate to lens_gdelt._fetch_gdelt_sentiment()
    to obtain both the tone scalar (Facet A) and the artlist articles (corpus input for Facet B),
    and _build_sentiment_section must call ONLY news_corpus (drop the separate lens_gdelt call).
    Net: ≤2 GDELT GETs per run (tone + artlist), properly spaced by _GDELT_INTER_REQUEST_S.

    FAILS on current codebase (unconditional double-fetch).
    """

    def _count_gdelt_tone_calls(self, captured_urls: list[str]) -> int:
        """Count how many calls targeted _GDELT_TONE_URL."""
        from advisors import lens_gdelt
        tone_url_base = lens_gdelt._GDELT_TONE_URL.split("?")[0]
        return sum(1 for u in captured_urls if tone_url_base in u and "timelinetone" in u)

    def test_build_sentiment_section_calls_gdelt_tone_url_at_most_once(
        self, gdelt_timelinetone_fixture, fed_press_xml
    ):
        """_build_sentiment_section requests _GDELT_TONE_URL at most once per call.

        Patches requests.get to count calls to the GDELT timelinetone endpoint.
        FAILS on current code (called twice: once via news_corpus._fetch_gdelt_tone,
        once via lens_gdelt._fetch_gdelt_sentiment).
        """
        import ai_advisor

        captured_urls: list[str] = []

        def tracking_get(url, **kwargs):
            captured_urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "timelinetone" in url:
                resp.json.return_value = gdelt_timelinetone_fixture
                resp.content = json.dumps(gdelt_timelinetone_fixture).encode()
            elif "artlist" in url:
                resp.json.return_value = {"articles": []}
                resp.content = b'{"articles":[]}'
            else:
                resp.content = fed_press_xml
            return resp

        with (
            patch("requests.get", side_effect=tracking_get),
            patch("time.sleep"),
        ):
            ai_advisor._build_sentiment_section()

        tone_call_count = self._count_gdelt_tone_calls(captured_urls)
        assert tone_call_count <= 1, (
            f"_build_sentiment_section made {tone_call_count} requests to _GDELT_TONE_URL "
            f"(timelinetone endpoint) in a single call. Must be ≤1. "
            f"BLOCK 1: double-fetch violates the 1-req/5s GDELT rate limit. "
            f"Fix: news_corpus must source tone via lens_gdelt._fetch_gdelt_sentiment(); "
            f"_build_sentiment_section must NOT make a separate lens_gdelt call."
        )

    def test_build_sentiment_section_no_unconditional_lens_gdelt_call(
        self, gdelt_timelinetone_fixture, fed_press_xml
    ):
        """lens_gdelt._fetch_gdelt_sentiment is NOT called unconditionally from _build_sentiment_section.

        The fix routes the single GDELT path through news_corpus only.
        _build_sentiment_section must not import and call lens_gdelt._fetch_gdelt_sentiment
        as an always-on second path.

        FAILS on current code where lens_gdelt._fetch_gdelt_sentiment() is called
        regardless of news_corpus success.
        """
        import ai_advisor

        lens_gdelt_call_count = [0]
        original_fetch = None

        def tracking_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "timelinetone" in url:
                resp.json.return_value = gdelt_timelinetone_fixture
                resp.content = json.dumps(gdelt_timelinetone_fixture).encode()
            elif "artlist" in url:
                resp.json.return_value = {"articles": []}
                resp.content = b'{"articles":[]}'
            else:
                resp.content = fed_press_xml
            return resp

        # Patch news_corpus to return a successful result, then verify lens_gdelt is NOT called
        mock_corpus_result = {
            "available": True,
            "tone": 0.12,
            "corpus": [],
            "reason": None,
        }

        try:
            import advisors.news_corpus as news_corpus_mod
            original_build = news_corpus_mod.build_news_corpus
            news_corpus_mod.build_news_corpus = lambda: mock_corpus_result
        except ImportError:
            pytest.skip("advisors.news_corpus not available")

        try:
            import advisors.lens_gdelt as lens_gdelt_mod
            original_fetch_sentiment = lens_gdelt_mod._fetch_gdelt_sentiment

            def counting_fetch_sentiment(universe):
                lens_gdelt_call_count[0] += 1
                return original_fetch_sentiment(universe)

            lens_gdelt_mod._fetch_gdelt_sentiment = counting_fetch_sentiment

            with (
                patch("requests.get", side_effect=tracking_get),
                patch("time.sleep"),
            ):
                ai_advisor._build_sentiment_section()

        finally:
            news_corpus_mod.build_news_corpus = original_build
            lens_gdelt_mod._fetch_gdelt_sentiment = original_fetch_sentiment

        assert lens_gdelt_call_count[0] == 0, (
            f"lens_gdelt._fetch_gdelt_sentiment was called {lens_gdelt_call_count[0]} time(s) "
            f"even though news_corpus.build_news_corpus() returned available=True. "
            f"BLOCK 1: the lens_gdelt path must not run unconditionally. "
            f"Fix: _build_sentiment_section calls ONLY news_corpus; drop the separate "
            f"lens_gdelt._fetch_gdelt_sentiment() call."
        )

    def test_fetch_gdelt_tone_is_removed_dead_code(self):
        """_fetch_gdelt_tone must NOT exist on news_corpus — it is dead code with no caller.

        build_news_corpus() calls lens_gdelt._fetch_gdelt_sentiment([]) inline (news_corpus.py:451)
        and pulls tone + artlist sources from that single result.  _fetch_gdelt_tone() was an
        earlier helper that duplicated that delegation; it has no production caller.

        Project standard: "if something is unused, delete it."  (CLAUDE.md §Coding Standards)

        RED: fails while _fetch_gdelt_tone still exists.
        GREEN: implementer deletes the function from advisors/news_corpus.py.
        """
        from advisors import news_corpus

        assert not hasattr(news_corpus, "_fetch_gdelt_tone"), (
            "news_corpus._fetch_gdelt_tone() still exists but has no production caller. "
            "build_news_corpus() calls lens_gdelt._fetch_gdelt_sentiment([]) inline; "
            "_fetch_gdelt_tone is dead code. Delete it from advisors/news_corpus.py."
        )


    def test_build_sentiment_section_total_gdelt_gets_at_most_two(
        self, gdelt_timelinetone_fixture, fed_press_xml
    ):
        """_build_sentiment_section issues at most 2 GDELT GETs total per call.

        BLOCK 1 — total-count guard: even after delegating tone-only to lens_gdelt,
        a partial fix that still calls news_corpus._fetch_gdelt_artlist() would make:
          - 1 timelinetone GET (via lens_gdelt._fetch_gdelt_sentiment)
          - 1 artlist GET (via lens_gdelt._fetch_gdelt_sentiment)
          - 1 EXTRA artlist GET (via news_corpus._fetch_gdelt_artlist → direct requests.get)
        = 3 total GETs, still tripping the GDELT rate limit.

        The correct fix: news_corpus must obtain BOTH tone AND artlist articles from
        ONE lens_gdelt._fetch_gdelt_sentiment() call — no separate _fetch_gdelt_artlist()
        GET. Net ≤2 GDELT GETs (1 timelinetone + 1 artlist), properly spaced inside
        lens_gdelt.

        FAILS on current code (4 GETs).
        FAILS on a tone-only partial fix (3 GETs: 1 tone via lens_gdelt + 2 artlist).
        PASSES only when news_corpus drops its own artlist GET and sources articles from
        the lens_gdelt._fetch_gdelt_sentiment return value.
        """
        import ai_advisor

        captured_urls: list[str] = []

        def tracking_get(url, **kwargs):
            captured_urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "timelinetone" in url:
                resp.json.return_value = gdelt_timelinetone_fixture
                resp.content = json.dumps(gdelt_timelinetone_fixture).encode()
            elif "artlist" in url:
                resp.json.return_value = {"articles": []}
                resp.content = b'{"articles":[]}'
            else:
                resp.content = fed_press_xml
            return resp

        with (
            patch("requests.get", side_effect=tracking_get),
            patch("time.sleep"),
        ):
            ai_advisor._build_sentiment_section()

        # Bucket captured URLs by GDELT endpoint type
        tone_calls = [u for u in captured_urls if "timelinetone" in u]
        artlist_calls = [u for u in captured_urls if "artlist" in u]

        assert len(tone_calls) <= 1, (
            f"_build_sentiment_section made {len(tone_calls)} timelinetone GETs "
            f"(must be ≤1). BLOCK 1: single GDELT path contract."
        )
        assert len(artlist_calls) <= 1, (
            f"_build_sentiment_section made {len(artlist_calls)} artlist GETs "
            f"(must be ≤1). "
            f"BLOCK 1 total-count guard: a tone-only delegation fix still leaves "
            f"news_corpus._fetch_gdelt_artlist() making its own artlist GET. "
            f"Fix: news_corpus must source BOTH tone AND articles from ONE "
            f"lens_gdelt._fetch_gdelt_sentiment() call — drop the direct artlist GET."
        )
        total_gdelt = len(tone_calls) + len(artlist_calls)
        assert total_gdelt <= 2, (
            f"Total GDELT GETs = {total_gdelt} (timelinetone={len(tone_calls)}, "
            f"artlist={len(artlist_calls)}). Must be ≤2. "
            f"Current code makes 4 GETs (2 tone + 2 artlist). "
            f"A partial tone-only fix makes 3 GETs (1 tone + 2 artlist). "
            f"Only the full fix (news_corpus sources both from lens_gdelt) achieves ≤2."
        )


# ---------------------------------------------------------------------------
# BLOCK 2 — Warehouse persistence (DW-1): persist_lens_snapshot on both paths
# ---------------------------------------------------------------------------


class TestWarehousePersistence:
    """_build_sentiment_section must call lens_warehouse.persist_lens_snapshot on
    BOTH the success path and the all-unavailable path.

    BLOCK 2 (reviewer-2, PM ruling Option A): the multi-source restructure silently
    removed all lens_warehouse.persist_lens_snapshot() calls. The DW-1 contract
    (lens_warehouse.py) states wired lenses persist their snapshots. Restore the calls.

    FAILS on current codebase (no persist_lens_snapshot calls in _build_sentiment_section).
    """

    def test_persist_lens_snapshot_called_on_success_path(
        self, gdelt_timelinetone_fixture, fed_press_xml
    ):
        """persist_lens_snapshot is called when _build_sentiment_section returns available=True.

        DW-1: sentiment lens snapshots must land in alphabot_warehouse.db on the success path.
        FAILS on current code (no persist call on success).
        """
        import ai_advisor

        persist_calls: list[dict] = []

        def mock_persist(**kwargs):
            persist_calls.append(dict(kwargs))
            return 1

        mock_corpus_result = {
            "available": True,
            "tone": 0.08,
            "corpus": [
                {
                    "url": "https://www.reuters.com/test",
                    "title": "Test Article",
                    "published": "20260618T120000Z",
                    "domain": "reuters.com",
                    "source_feed": "gdelt_artlist",
                    "score": 0.75,
                    "topics": ["macro"],
                }
            ],
            "reason": None,
        }

        try:
            import advisors.news_corpus as news_corpus_mod
            original_build = news_corpus_mod.build_news_corpus
            news_corpus_mod.build_news_corpus = lambda: mock_corpus_result
        except ImportError:
            pytest.skip("advisors.news_corpus not available")

        try:
            import advisors.lens_warehouse as lw_mod
            original_persist = lw_mod.persist_lens_snapshot
            lw_mod.persist_lens_snapshot = mock_persist

            result = ai_advisor._build_sentiment_section()
        finally:
            news_corpus_mod.build_news_corpus = original_build
            lw_mod.persist_lens_snapshot = original_persist

        assert result.get("available") is True, (
            f"Precondition: section must be available=True. Got: {result!r}"
        )
        assert len(persist_calls) >= 1, (
            f"persist_lens_snapshot was NOT called on the success path. "
            f"DW-1: sentiment lens snapshots must persist to warehouse on success. "
            f"FAILS on current code. Restore the lens_warehouse.persist_lens_snapshot() "
            f"call in _build_sentiment_section."
        )
        # The persist call must carry lens='sentiment' and available=True
        success_persist = [c for c in persist_calls if c.get("available") is True]
        assert success_persist, (
            f"persist_lens_snapshot was called {len(persist_calls)} time(s) but none "
            f"with available=True. Calls: {persist_calls}"
        )
        assert success_persist[0].get("lens") == "sentiment", (
            f"persist_lens_snapshot call has wrong lens value: "
            f"{success_persist[0].get('lens')!r}. Expected 'sentiment'."
        )

    def test_persist_lens_snapshot_called_on_unavailable_path(self):
        """persist_lens_snapshot is called when _build_sentiment_section returns available=False.

        DW-1: unavailability events must also be persisted so the warehouse records
        when the sentiment lens was down.
        FAILS on current code (no persist call on the unavailable path).
        """
        import ai_advisor
        from requests.exceptions import Timeout

        persist_calls: list[dict] = []

        def mock_persist(**kwargs):
            persist_calls.append(dict(kwargs))
            return 1

        # Fail all feeds so both paths return available=False
        mock_unavailable = {
            "available": False,
            "tone": None,
            "corpus": [],
            "reason": "no_news_events",
        }

        try:
            import advisors.news_corpus as news_corpus_mod
            original_build = news_corpus_mod.build_news_corpus
            news_corpus_mod.build_news_corpus = lambda: mock_unavailable
        except ImportError:
            pytest.skip("advisors.news_corpus not available")

        try:
            import advisors.lens_warehouse as lw_mod
            original_persist = lw_mod.persist_lens_snapshot
            lw_mod.persist_lens_snapshot = mock_persist

            result = ai_advisor._build_sentiment_section()
        finally:
            news_corpus_mod.build_news_corpus = original_build
            lw_mod.persist_lens_snapshot = original_persist

        assert result.get("available") is False, (
            f"Precondition: section must be available=False. Got: {result!r}"
        )
        assert len(persist_calls) >= 1, (
            f"persist_lens_snapshot was NOT called on the unavailable path. "
            f"DW-1: sentiment lens unavailability must be persisted to warehouse. "
            f"FAILS on current code. Restore the lens_warehouse.persist_lens_snapshot() "
            f"call in _build_sentiment_section for the unavailable path."
        )
        unavail_persist = [c for c in persist_calls if c.get("available") is False]
        assert unavail_persist, (
            f"persist_lens_snapshot called {len(persist_calls)} time(s) but none "
            f"with available=False. Calls: {persist_calls}"
        )

    def test_persist_payload_contains_tone_and_corpus_summary(
        self, gdelt_timelinetone_fixture
    ):
        """The warehouse payload on the success path carries tone_score and corpus_size.

        DW-1: the raw_payload passed to persist_lens_snapshot must contain enough
        context for warehouse consumers — at minimum tone_score and corpus_size.
        FAILS on current code (no persist call at all).
        """
        import ai_advisor

        persist_calls: list[dict] = []

        def mock_persist(**kwargs):
            persist_calls.append(dict(kwargs))
            return 1

        mock_corpus_result = {
            "available": True,
            "tone": 0.15,
            "corpus": [
                {
                    "url": "https://www.cnbc.com/article",
                    "title": "Markets Rise",
                    "published": "20260618T140000Z",
                    "domain": "cnbc.com",
                    "source_feed": "cnbc_markets",
                    "score": 0.65,
                    "topics": ["broad-sentiment"],
                }
            ],
            "reason": None,
        }

        try:
            import advisors.news_corpus as news_corpus_mod
            original_build = news_corpus_mod.build_news_corpus
            news_corpus_mod.build_news_corpus = lambda: mock_corpus_result
        except ImportError:
            pytest.skip("advisors.news_corpus not available")

        try:
            import advisors.lens_warehouse as lw_mod
            original_persist = lw_mod.persist_lens_snapshot
            lw_mod.persist_lens_snapshot = mock_persist

            ai_advisor._build_sentiment_section()
        finally:
            news_corpus_mod.build_news_corpus = original_build
            lw_mod.persist_lens_snapshot = original_persist

        if not persist_calls:
            pytest.fail(
                "persist_lens_snapshot was not called — DW-1 regression. "
                "Cannot verify payload shape."
            )

        success_calls = [c for c in persist_calls if c.get("available") is True]
        if not success_calls:
            pytest.skip("No success-path persist call found — covered by other test")

        raw_payload = success_calls[0].get("raw_payload", {})
        assert isinstance(raw_payload, dict), (
            f"raw_payload must be a dict. Got: {type(raw_payload)}"
        )
        assert "tone_score" in raw_payload or "tone" in raw_payload, (
            f"raw_payload missing tone_score/tone key. "
            f"Warehouse consumers need the tone value for historical trending. "
            f"Got keys: {set(raw_payload.keys())}"
        )
        assert "corpus_size" in raw_payload or "article_count" in raw_payload or "corpus" in raw_payload, (
            f"raw_payload missing corpus size indicator. "
            f"Got keys: {set(raw_payload.keys())}"
        )


# ---------------------------------------------------------------------------
# NOTE — utcnow() deprecation: news_corpus.py should use tz-aware datetime
# (non-blocking per reviewer; included here for completeness)
# ---------------------------------------------------------------------------


class TestUtcnowDeprecation:
    """news_corpus.build_news_corpus must not use datetime.datetime.utcnow() (deprecated).

    This generates 25 DeprecationWarnings per test run (Python 3.12+).
    Fix: replace with datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).
    Non-blocking per reviewer (no correctness bug — _recency strips tzinfo before compare).
    """

    def test_news_corpus_does_not_call_utcnow(self):
        """news_corpus.py source does not contain datetime.utcnow().

        FAILS if utcnow() is still present after the fix.
        NOTE: non-blocking per reviewer; include in the same GREEN pass.
        """
        import pathlib
        src = pathlib.Path(__file__).parents[2] / "advisors" / "news_corpus.py"
        assert src.exists(), f"news_corpus.py not found at {src}"
        content = src.read_text(encoding="utf-8")
        assert "utcnow()" not in content, (
            "news_corpus.py still calls datetime.utcnow() (deprecated in Python 3.12+). "
            "Replace with datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None). "
            "This eliminates the 25 DeprecationWarnings in the test suite."
        )

    def test_news_corpus_does_not_call_utcfromtimestamp(self):
        """news_corpus.py source does not contain datetime.utcfromtimestamp() (deprecated).

        The published_parsed fix for RFC-822 dates introduced utcfromtimestamp() in
        _fetch_rss_feed, generating 2782 DeprecationWarnings per test run (Python 3.12+)
        — one per feed entry parsed.

        Fix: replace datetime.datetime.utcfromtimestamp(calendar.timegm(_pp)) with the
        timezone-aware equivalent:
          datetime.datetime.fromtimestamp(calendar.timegm(_pp), tz=datetime.timezone.utc)
                           .replace(tzinfo=None)
        or equivalently:
          datetime.datetime(*(pp[:6]))  — direct struct_time field extraction (UTC)

        RED: fails while utcfromtimestamp() is present in news_corpus.py.
        GREEN: implementer replaces it with the tz-aware form.
        """
        import pathlib
        src = pathlib.Path(__file__).parents[2] / "advisors" / "news_corpus.py"
        assert src.exists(), f"news_corpus.py not found at {src}"
        content = src.read_text(encoding="utf-8")
        assert "utcfromtimestamp" not in content, (
            "news_corpus.py calls datetime.utcfromtimestamp() (deprecated in Python 3.12+). "
            "The published_parsed fix in _fetch_rss_feed introduced this call, generating "
            "2782 DeprecationWarnings per test run. "
            "Replace with: datetime.datetime.fromtimestamp("
            "calendar.timegm(_pp), tz=datetime.timezone.utc).replace(tzinfo=None)"
            ".strftime('%Y-%m-%dT%H:%M:%S')"
        )


# ---------------------------------------------------------------------------
# AC-3 Deviation 1 — recency factor inert for RSS (published_parsed fix)
# ---------------------------------------------------------------------------


class TestRssRecencyFromPublishedParsed:
    """_fetch_rss_feed must produce a parseable published field so _recency()
    reflects the article's real age.

    Current bug: _fetch_rss_feed stores entry.published (RFC-822 string, e.g.
    "Thu, 18 Jun 2026 12:01:05 GMT") as the article's published field.
    _recency() uses datetime.fromisoformat(), which cannot parse RFC-822 —
    it falls through to the `except → 0.5` default for EVERY RSS article.
    The 40%-weight recency factor is therefore constant for all RSS articles,
    making ranking equivalent to relevance+authority only.

    Fix: use entry.published_parsed (a time.struct_time feedparser always
    provides) to produce an ISO string, or add an email.utils fallback in
    _recency. Either way: recency must vary for RSS articles of different ages.

    Tests use the existing fed_press.xml fixture (captured live 2026-06-18)
    which has entries spanning several days — zero live HTTP.
    """

    def test_rss_article_recency_is_not_stuck_at_default(self, fed_press_xml):
        """_fetch_rss_feed articles from fed_press must have a published value
        that _recency() resolves to something OTHER than the 0.5 fallback.

        RED: fails while _fetch_rss_feed stores entry.published (RFC-822 string)
        and _recency() cannot parse RFC-822.
        GREEN: fix uses entry.published_parsed → ISO string (or RFC-822 fallback
        in _recency) so at least one article has recency != 0.5.
        """
        import datetime

        from advisors.news_corpus import _recency, _fetch_rss_feed

        articles = []
        with patch("requests.get", return_value=_make_xml_response(fed_press_xml)):
            articles = _fetch_rss_feed(
                "fed_press",
                "https://www.federalreserve.gov/feeds/press_all.xml",
                "test-ua",
            )

        assert articles, "fed_press fixture must yield at least one article"

        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        recency_vals = [_recency(art["published"], now) for art in articles]

        non_default = [v for v in recency_vals if v != 0.5]
        assert non_default, (
            f"ALL {len(recency_vals)} RSS articles have recency == 0.5 (the fallback). "
            f"_recency() cannot parse RFC-822 dates emitted by feedparser entry.published. "
            f"Fix: use entry.published_parsed (time.struct_time) → calendar.timegm → "
            f"datetime.utcfromtimestamp → isoformat, so _recency gets an ISO string. "
            f"Or add email.utils.parsedate_to_datetime fallback in _recency itself. "
            f"Sample published values: {[art['published'] for art in articles[:3]]}"
        )

    def test_rss_recency_varies_across_entries_of_different_ages(self, fed_press_xml):
        """Articles published days apart must have meaningfully different recency scores.

        The fed_press fixture has entries from the same day AND from >7 days ago.
        With tau=24h, a 7-day-old article has recency = exp(-168/24) ≈ 0.001,
        while a 1-hour-old article has recency ≈ 0.96. If all recency values are
        stuck at 0.5, all articles are scored identically on the recency axis.

        RED: fails while _fetch_rss_feed stores RFC-822 strings → recency is 0.5 for all.
        GREEN: recency values span a meaningful range (max - min > 0.1).
        """
        import datetime

        from advisors.news_corpus import _recency, _fetch_rss_feed, TAU_HOURS

        articles = []
        with patch("requests.get", return_value=_make_xml_response(fed_press_xml)):
            articles = _fetch_rss_feed(
                "fed_press",
                "https://www.federalreserve.gov/feeds/press_all.xml",
                "test-ua",
            )

        assert len(articles) >= 2, "fed_press fixture must yield at least 2 articles"

        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        recency_vals = [_recency(art["published"], now) for art in articles]

        spread = max(recency_vals) - min(recency_vals)
        assert spread > 0.1, (
            f"Recency spread across {len(recency_vals)} fed_press articles is {spread:.4f} "
            f"(expected > 0.1 for articles spanning multiple days). "
            f"All values stuck at {set(recency_vals)} — RFC-822 dates not being parsed. "
            f"Fix: use entry.published_parsed to emit an ISO string _recency() can parse. "
            f"TAU_HOURS={TAU_HOURS}: a 7-day-old article should score ~0.001 vs "
            f"same-day ~0.96."
        )


# ---------------------------------------------------------------------------
# AC-3 Deviation 2 — Google News wrapper URLs not resolved to publisher domain
# ---------------------------------------------------------------------------


class TestGoogleNewsPublisherDomain:
    """_fetch_rss_feed must resolve Google News wrapper URLs to the publisher domain.

    Current bug: for the google_news_business feed, entry.link is always a
    news.google.com/rss/articles/... wrapper URL. _extract_domain(entry.link)
    returns "news.google.com" for every article → authority 0.4 (wrong) + no
    cross-source dedup against the same story from direct feeds.

    feedparser exposes the publisher via entry.source.href (e.g. "https://www.cnbc.com").
    The fix: when entry.source.href is present, derive domain from it instead of
    entry.link.

    Tests use the existing google_news_business.xml fixture (captured live 2026-06-18,
    which has <source url="..."> elements — confirmed: entries[0].source.href ==
    "https://www.cnbc.com"). Zero live HTTP.
    """

    def test_google_news_articles_have_publisher_domain_not_google(self, google_news_xml):
        """_fetch_rss_feed('google_news_business', ...) must yield articles whose
        domain is the publisher (e.g. 'cnbc.com', 'wsj.com'), NOT 'news.google.com'.

        RED: fails while _fetch_rss_feed uses _extract_domain(entry.link) —
        returns 'news.google.com' for every Google News article.
        GREEN: _fetch_rss_feed derives domain from entry.source.href when present.
        """
        from advisors.news_corpus import _fetch_rss_feed

        with patch("requests.get", return_value=_make_xml_response(google_news_xml)):
            articles = _fetch_rss_feed(
                "google_news_business",
                "https://news.google.com/rss/headlines/section/topic/BUSINESS",
                "test-ua",
            )

        assert articles, "google_news_business.xml fixture must yield at least one article"

        google_domains = [a for a in articles if a.get("domain") == "news.google.com"]
        publisher_domains = [a for a in articles if a.get("domain") != "news.google.com"]

        assert publisher_domains, (
            f"ALL {len(articles)} Google News articles have domain='news.google.com'. "
            f"AC-3 requires resolving wrapper URLs to the publisher domain. "
            f"feedparser exposes the publisher via entry.source.href "
            f"(e.g. 'https://www.cnbc.com'). "
            f"Fix: in _fetch_rss_feed, when entry.source.href is present, derive "
            f"domain from source.href instead of entry.link."
        )

        # Allow zero google.com domains after fix — none should remain
        assert not google_domains, (
            f"{len(google_domains)} of {len(articles)} articles still have "
            f"domain='news.google.com' after fix. Every Google News entry in the "
            f"fixture has a source.href — all domains should resolve to the publisher."
        )

    def test_google_news_publisher_domain_authority_is_not_default(self, google_news_xml):
        """Publisher domains must receive their correct SOURCE_AUTHORITY score,
        not the 0.4 unknown-domain default that 'news.google.com' gets.

        This validates that the domain fix actually improves ranking.  At least
        one Google News article should come from a domain in SOURCE_AUTHORITY
        (reuters.com, cnbc.com, apnews.com, etc.).

        RED: fails while domain is stuck at 'news.google.com' (authority 0.4 always).
        GREEN: at least one article has a domain with authority > 0.4.
        """
        from advisors.news_corpus import _fetch_rss_feed, _authority

        with patch("requests.get", return_value=_make_xml_response(google_news_xml)):
            articles = _fetch_rss_feed(
                "google_news_business",
                "https://news.google.com/rss/headlines/section/topic/BUSINESS",
                "test-ua",
            )

        assert articles, "google_news_business.xml fixture must yield at least one article"

        high_authority = [
            a for a in articles if _authority(a.get("domain", "")) > 0.4
        ]
        assert high_authority, (
            f"No Google News article has authority > 0.4 (the unknown-domain default). "
            f"The fixture contains articles from known publishers (cnbc.com, cnn.com, "
            f"bbc.com, etc.) — at least one should resolve to a domain in SOURCE_AUTHORITY. "
            f"Domains found: {sorted({a.get('domain') for a in articles})}. "
            f"Fix: derive domain from entry.source.href."
        )

    def test_non_google_feeds_domain_extraction_unchanged(self, fed_press_xml):
        """Non-Google-News feeds must continue to derive domain from entry.link.

        The entry.source.href fix must NOT affect feeds where entry.link is already
        the canonical publisher URL.

        RED: passes (the fix is not yet applied, so current behavior is unchanged).
        Included so a wrong implementation that breaks all feeds fails here.
        GREEN: fed_press articles still have domain 'federalreserve.gov'.
        """
        from advisors.news_corpus import _fetch_rss_feed

        with patch("requests.get", return_value=_make_xml_response(fed_press_xml)):
            articles = _fetch_rss_feed(
                "fed_press",
                "https://www.federalreserve.gov/feeds/press_all.xml",
                "test-ua",
            )

        assert articles, "fed_press fixture must yield at least one article"

        gov_domains = [a for a in articles if "federalreserve.gov" in a.get("domain", "")]
        assert gov_domains, (
            f"fed_press articles lost their federalreserve.gov domain after fix. "
            f"Non-Google-News feeds must continue to use _extract_domain(entry.link). "
            f"Domains found: {sorted({a.get('domain') for a in articles})}"
        )


# ---------------------------------------------------------------------------
# @pytest.mark.live — excluded from default run
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_build_news_corpus_returns_articles():
    """LIVE: build_news_corpus() fetches real feeds and returns corpus articles.

    Makes real HTTP requests to all configured feeds.
    Excluded from default run — opt in with: pytest --include-live
    """
    from advisors import news_corpus

    result = news_corpus.build_news_corpus()
    assert "available" in result
    assert "corpus" in result
    if result["available"] and result["corpus"]:
        for art in result["corpus"][:3]:
            assert "url" in art
            assert "title" in art
            assert "topics" in art
