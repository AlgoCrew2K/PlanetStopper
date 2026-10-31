"""Every page that loads the tweaks script loads it in ``<head>``, after the last stylesheet, blocking.

The stored theme was applied when the document finished loading, so a dark-theme operator saw a light
flash on every page load. The script now runs while the head is parsed, which puts the theme on the root
element before the body exists. A script in the head is blocking only without ``defer``, ``async`` or
``type="module"``, and it must come after the stylesheets because it reads the accent token's computed
value for its default.

Real call path: ``GET`` of each page through the Flask client. The visible effect (no light flash) is judged
in a browser: tests/ui/test_theme_prepaint_browser.py.
"""

from __future__ import annotations

import re

import pytest

import app as app_module

PAGES = ["/", "/history", "/performance", "/ai-advisor", "/settings"]
SCRIPT_TAG = re.compile(r"<script\b[^>]*\bsrc=\"[^\"]*tweaks\.js[^\"]*\"[^>]*>", re.I)
STYLESHEET = re.compile(r"<link\b[^>]*\brel=\"stylesheet\"[^>]*>", re.I)


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _head(html: str) -> tuple[int, int]:
    start = html.lower().index("<head")
    end = html.lower().index("</head>")
    return start, end


@pytest.mark.parametrize("path", PAGES)
class TestTheTweaksScriptIsInTheHead:
    def test_the_page_loads_the_script_exactly_once(self, client, path):
        response = client.get(path)
        assert response.status_code == 200, f"GET {path} -> {response.status_code}"
        assert len(SCRIPT_TAG.findall(response.get_data(as_text=True))) == 1

    def test_the_tag_is_inside_the_head(self, client, path):
        html = client.get(path).get_data(as_text=True)
        start, end = _head(html)
        tag = SCRIPT_TAG.search(html)
        assert tag and start < tag.start() < end, f"{path}: the tweaks script is not in <head>"

    def test_the_tag_comes_after_the_last_stylesheet_of_the_head(self, client, path):
        html = client.get(path).get_data(as_text=True)
        start, end = _head(html)
        sheets = [m for m in STYLESHEET.finditer(html) if start < m.start() < end]
        tag = SCRIPT_TAG.search(html)
        assert sheets, f"{path}: the head links no stylesheet"
        assert tag and tag.start() > max(m.start() for m in sheets), (
            f"{path}: the script runs before the stylesheets whose token it reads"
        )

    def test_the_tag_is_blocking(self, client, path):
        html = client.get(path).get_data(as_text=True)
        tag = SCRIPT_TAG.search(html)
        assert tag
        text = tag.group(0).lower()
        assert not re.search(r"\b(defer|async)\b", text), f"{path}: the tag is deferred or async"
        assert 'type="module"' not in text
