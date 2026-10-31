"""RED: every POST the page scripts send goes through ``PSRequest.postAction``, and nothing else holds a token.

A source census over ``static/*.js`` and ``templates/*.html`` (text scans, after the pattern of
``tests/ui/test_f020_history_drilldown.py``): the one place a refused POST is refreshed and never
repeated is ``static/request_guard.js``, so any other POST call site would be a path that can send
twice or hold a stale token.

CONTRACT PINNED
* Every ``method: 'POST'`` literal in a script other than ``request_guard.js`` sits inside the
  argument list of a ``PSRequest.postAction(`` call. (A POST built as a variable and handed to
  ``postAction`` later does not satisfy this; build the init object inside the call.)
* The only HTML form that posts is the login form; no template carries a script-side POST.
* No script other than ``request_guard.js`` names ``/api/csrf-token``, keeps a ``_csrfToken`` or
  ``_chromeCsrfToken`` variable, or sets an ``X-CSRF-Token`` header from anything but
  ``PSRequest.csrfToken()``.
* ``request_guard.js`` exists and is loaded before ``chrome.js`` by the shared chrome template.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "static"
TEMPLATES = REPO / "templates"
GUARD = STATIC / "request_guard.js"
POST_LITERAL = re.compile(r"""method\s*:\s*(['"])POST\1""")
SCRIPTS = sorted(p for p in STATIC.glob("*.js") if p.name != "request_guard.js")


def sanitised(text: str) -> str:
    """``text`` with comment bodies and string contents blanked, same length, so parentheses match."""
    out = []
    i = 0
    quote = None
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                out.append("  ")
                i += 2
                continue
            if c == quote:
                quote = None
                out.append(c)
            else:
                out.append("\n" if c == "\n" else " ")
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            out.append(" " * (end - i))
            i = end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i)
            end = len(text) if end < 0 else end + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:end]))
            i = end
            continue
        if c in "'\"`":
            quote = c
        out.append(c)
        i += 1
    return "".join(out)


def enclosing_call_name(clean: str, index: int) -> str:
    """The callee text before the innermost unmatched ``(`` that encloses ``index``."""
    depth = 0
    for i in range(index - 1, -1, -1):
        c = clean[i]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth == 0:
                return re.search(r"[\w$.]*\s*$", clean[:i]).group(0).strip()
            depth -= 1
    return ""


def test_the_shared_module_exists_and_loads_before_chrome():
    assert GUARD.exists(), "static/request_guard.js does not exist"
    chrome = (TEMPLATES / "_chrome.html").read_text(encoding="utf-8")
    assert "request_guard.js" in chrome, "_chrome.html does not load request_guard.js"
    assert chrome.index("request_guard.js") < chrome.index("chrome.js")


def test_the_scan_sees_the_post_sites_it_is_meant_to_guard():
    found = sum(len(POST_LITERAL.findall(p.read_text(encoding="utf-8"))) for p in SCRIPTS)
    assert found >= 20, (
        f"only {found} POST literals found: the scan is not looking at the right files"
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_every_post_literal_is_inside_a_post_action_call(script):
    text = script.read_text(encoding="utf-8")
    clean = sanitised(text)
    outside = []
    for match in POST_LITERAL.finditer(text):
        callee = enclosing_call_name(clean, match.start())
        if not callee.endswith("PSRequest.postAction"):
            line = text.count("\n", 0, match.start()) + 1
            outside.append(f"{script.name}:{line} (inside {callee or 'no call'!r})")
    assert not outside, f"POST call sites outside PSRequest.postAction: {outside}"


def test_no_template_sends_a_post_from_a_script_and_only_login_posts_a_form():
    offenders = []
    for template in sorted(TEMPLATES.glob("*.html")):
        text = template.read_text(encoding="utf-8")
        if POST_LITERAL.search(text):
            offenders.append(f"{template.name}: a script-side POST")
        if (
            re.search(r"""<form[^>]*method\s*=\s*['"]post['"]""", text, re.I)
            and template.name != "login.html"
        ):
            offenders.append(f"{template.name}: a posting form")
    assert not offenders, offenders


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_no_script_but_the_guard_holds_a_token(script):
    text = script.read_text(encoding="utf-8")
    assert "/api/csrf-token" not in text, f"{script.name} fetches the token itself"
    assert not re.search(
        r"\b_(chrome)?[cC]srfToken\b|\bcsrfToken\s*=", text.replace("PSRequest.csrfToken", "")
    ), f"{script.name} keeps its own token variable"
    for match in re.finditer(r"""['"]X-CSRF-Token['"]\s*:\s*([^,}\n]+)""", text):
        assert "PSRequest.csrfToken()" in match.group(1), (
            f"{script.name} sets X-CSRF-Token from {match.group(1).strip()!r}"
        )
