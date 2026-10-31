"""The production Caddyfile sets the response headers that stop framing, sniffing and plain-HTTP use.

``deploy/caddy/Caddyfile`` is the tracked copy of the proxy in front of the dashboard. It sets none of
the browser-facing security headers, so a page on a neighbouring host could frame the dashboard (the
panic and sleeve buttons are on it) and the first plain-HTTP request is not upgraded for good.

The app's pages carry inline ``<script>`` blocks and inline ``onclick`` handlers and load Chart.js from
a CDN and fonts from Google Fonts, so a policy that governs where scripts, styles, fonts or
connections may come from would break them. The one policy directive allowed here is the one that
governs who may frame the page.

Contract pinned
* The site block sets, with a ``header`` directive at the site level: ``Strict-Transport-Security`` with
  a ``max-age`` of at least 180 days, ``X-Content-Type-Options: nosniff``, a ``Referrer-Policy`` that
  sends no cross-origin path, and framing protection (``X-Frame-Options`` DENY or SAMEORIGIN, or a
  ``frame-ancestors`` policy of none or self).
* A ``Content-Security-Policy``, if present, holds no directive that restricts what the page loads.
* The rest of the file is untouched in its parts the encoder test pins (the proxy target).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CADDYFILE = Path(__file__).resolve().parents[2] / "deploy" / "caddy" / "Caddyfile"

HSTS_MIN_SECONDS = 15_552_000  # 180 days
SAFE_REFERRER_POLICIES = {
    "no-referrer",
    "same-origin",
    "strict-origin",
    "strict-origin-when-cross-origin",
    "origin",
    "origin-when-cross-origin",
}
# Directives that govern what the page may load or connect to: with the app's inline scripts and
# handlers, its CDN script and its font stylesheet, any of them blocks something the page needs.
LOAD_RESTRICTING_DIRECTIVES = {
    "default-src",
    "script-src",
    "script-src-elem",
    "script-src-attr",
    "style-src",
    "style-src-elem",
    "style-src-attr",
    "font-src",
    "connect-src",
    "img-src",
    "frame-src",
    "child-src",
    "worker-src",
    "manifest-src",
    "media-src",
    "object-src",
    "prefetch-src",
}


def _site_level_headers(text: str) -> dict[str, list[str]]:
    """Header name (lower case) -> values, from ``header`` directives at the site block's level."""
    headers: dict[str, list[str]] = {}
    depth = 0
    in_block_at_depth = None
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        opens, closes = line.count("{"), line.count("}")
        if in_block_at_depth is not None and depth == in_block_at_depth and "}" not in line:
            name, _, value = line.partition(" ")
            headers.setdefault(name.lstrip("+-?").lower(), []).append(value.strip().strip('"'))
        elif depth == 1:
            single = re.match(r"header\s+(?:@\S+\s+)?([+\-?]?[A-Za-z][\w-]*)\s+(.+)$", line)
            if single and "{" not in single.group(2):
                headers.setdefault(single.group(1).lstrip("+-?").lower(), []).append(
                    single.group(2).strip().strip('"')
                )
            elif re.match(r"header(\s+@\S+)?\s*\{$", line):
                in_block_at_depth = depth + 1
        depth += opens - closes
        if in_block_at_depth is not None and depth < in_block_at_depth:
            in_block_at_depth = None
    return headers


@pytest.fixture(scope="module")
def headers() -> dict[str, list[str]]:
    return _site_level_headers(CADDYFILE.read_text(encoding="utf-8"))


def test_the_proxy_target_is_still_the_dashboard():
    text = CADDYFILE.read_text(encoding="utf-8")
    assert "reverse_proxy localhost:8090" in text


def test_the_header_reader_sees_a_block_and_a_single_line_form():
    # A guard on the reader this file relies on: it must find both spellings Caddy accepts, and it
    # must not read a header set inside another directive's block.
    text = "\n".join(
        [
            "site.example {",
            "    header {",
            "        X-One a",
            '        Y-Two "b c"',
            "    }",
            "    header X-Three d",
            "    reverse_proxy localhost:1 {",
            "        header_up X-Forwarded-For x",
            "        header Z-Nested n",
            "    }",
            "}",
        ]
    )
    assert _site_level_headers(text) == {"x-one": ["a"], "y-two": ["b c"], "x-three": ["d"]}


def test_strict_transport_security_is_set_for_at_least_180_days(headers):
    values = headers.get("strict-transport-security")
    assert values, "the site block sets no Strict-Transport-Security header"
    match = re.search(r"max-age=(\d+)", values[-1], flags=re.I)
    assert match, f"no max-age in {values[-1]!r}"
    assert int(match.group(1)) >= HSTS_MIN_SECONDS


def test_content_type_sniffing_is_off(headers):
    values = headers.get("x-content-type-options")
    assert values and values[-1].lower() == "nosniff", values


def test_the_referrer_policy_sends_no_cross_origin_path(headers):
    values = headers.get("referrer-policy")
    assert values, "the site block sets no Referrer-Policy header"
    assert values[-1].lower() in SAFE_REFERRER_POLICIES, values[-1]


def test_framing_is_refused(headers):
    frame_options = [v.lower() for v in headers.get("x-frame-options", [])]
    policy = " ".join(headers.get("content-security-policy", [])).lower()
    ancestors = re.search(r"frame-ancestors\s+([^;]+)", policy)
    by_options = bool(frame_options) and frame_options[-1] in {"deny", "sameorigin"}
    by_policy = bool(ancestors) and ancestors.group(1).strip().strip("'\"") in {"none", "self"}
    assert by_options or by_policy, (
        f"nothing stops the dashboard from being framed: X-Frame-Options={frame_options}, "
        f"CSP={policy!r}"
    )


def test_a_content_security_policy_restricts_nothing_the_page_loads(headers):
    for policy in headers.get("content-security-policy", []):
        directives = {d.strip().split()[0].lower() for d in policy.split(";") if d.strip()}
        restricting = directives & LOAD_RESTRICTING_DIRECTIVES
        assert not restricting, (
            f"the policy restricts {sorted(restricting)}, which the inline scripts and handlers, "
            "the CDN script and the font stylesheet need"
        )
