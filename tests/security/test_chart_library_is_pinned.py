"""No page runs a script from another host that it has not pinned.

Three pages load Chart.js from a CDN by a URL with no version and no integrity attribute, on the
origin that holds the panic and sleeve buttons: whatever that URL serves runs with the operator's
session. A script is either served from ``static/`` or loaded from another host by an exact version with
a Subresource Integrity hash and a ``crossorigin`` attribute.

Contract pinned
* Every ``<script src>`` in a template that names another host carries ``integrity="sha256|384|512-..."``
  (a base64 digest of the right length) and ``crossorigin``, and the URL names an exact version
  (``@major.minor.patch``), never a floating tag or none.
* Every script a template serves from ``static/`` exists there.
* Chart.js is still loaded by every page that draws a chart (the library is pinned or vendored, not
  dropped).
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted((ROOT / "templates").glob("*.html"))
STATIC = ROOT / "static"
SCRIPT_TAG = re.compile(r"<script\b[^>]*\bsrc\s*=\s*([\"'])(.+?)\1[^>]*>", re.I | re.S)
STATIC_REFERENCE = re.compile(
    r"""(?:url_for\(\s*['"]static['"]\s*,\s*filename\s*=\s*['"]([^'"]+)['"]\s*\)|^/static/([^'"?]+))"""
)
DIGEST_LENGTHS = {"sha256": 32, "sha384": 48, "sha512": 64}
CHART_PAGES = ("index.html", "performance.html", "ai_advisor.html")


def _script_tags() -> list[tuple[str, str, str]]:
    found = []
    for path in TEMPLATES:
        for match in SCRIPT_TAG.finditer(path.read_text(encoding="utf-8")):
            found.append((path.name, match.group(2), match.group(0)))
    return found


EXTERNAL = [(name, src, tag) for name, src, tag in _script_tags() if re.match(r"(https?:)?//", src)]
INTERNAL = [
    (name, src, tag) for name, src, tag in _script_tags() if not re.match(r"(https?:)?//", src)
]


def test_the_scan_sees_the_scripts_it_is_about():
    # A guard on the scan: it reads this repository's templates and finds both kinds of script.
    assert len(TEMPLATES) >= 5
    assert INTERNAL, "no script served from static/ was found: the pattern no longer matches"


def _problems(check) -> list[str]:
    """One line per external script for which ``check(name, src, tag)`` finds a fault."""
    problems = []
    for name, src, tag in EXTERNAL:
        fault = check(name, src, tag)
        if fault:
            problems.append(f"{name}: {src}: {fault}")
    return problems


def _integrity_fault(name, src, tag):
    match = re.search(r"""\bintegrity\s*=\s*["'](sha(?:256|384|512))-([A-Za-z0-9+/=]+)["']""", tag)
    if not match:
        return "no integrity attribute"
    try:
        digest = base64.b64decode(match.group(2), validate=True)
    except ValueError:
        return "the digest is not base64"
    if len(digest) != DIGEST_LENGTHS[match.group(1)]:
        return "the digest has the wrong length"
    return None


def _crossorigin_fault(name, src, tag):
    return None if re.search(r"\bcrossorigin\b", tag, re.I) else "no crossorigin attribute"


def _version_fault(name, src, tag):
    return None if re.search(r"@\d+\.\d+\.\d+(?=[/?#]|$)", src) else "no exact version in the URL"


def test_a_script_from_another_host_carries_an_integrity_hash_of_the_right_length():
    assert _problems(_integrity_fault) == []


def test_a_script_from_another_host_declares_crossorigin():
    assert _problems(_crossorigin_fault) == []


def test_a_script_from_another_host_names_an_exact_version():
    assert _problems(_version_fault) == []


@pytest.mark.parametrize(
    ("name", "src", "tag"), INTERNAL, ids=[f"{n}: {s}" for n, s, _ in INTERNAL]
)
def test_a_script_served_from_static_exists(name, src, tag):
    reference = STATIC_REFERENCE.search(src)
    if reference is None:
        assert src.startswith("/"), f"{name}: unrecognized script reference {src!r}"
        return
    filename = reference.group(1) or reference.group(2)
    assert (STATIC / filename).is_file(), f"{name}: static/{filename} does not exist"


@pytest.mark.parametrize("page", CHART_PAGES)
def test_every_page_that_draws_a_chart_still_loads_the_library(page):
    sources = [src for name, src, _ in _script_tags() if name == page]
    loads_chart = [s for s in sources if re.search(r"chart", s, re.I)]
    assert loads_chart, f"{page} loads no Chart.js script: {sources}"
