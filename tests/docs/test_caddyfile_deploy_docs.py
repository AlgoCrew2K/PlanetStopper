"""RED tests: the tracked Caddyfile and the deployment guide show the same compressing proxy.

Caddy cannot run in a unit test, so this is a document check. The live half (the droplet's
``/etc/caddy/Caddyfile`` equals the tracked file, responses arrive gzip-encoded, the event
stream arrives uncompressed within 2 s) is the operator's read after the apply.

Contract:
  - ``deploy/caddy/Caddyfile`` is one site block: ``encode gzip zstd`` (gzip first, because Caddy
    picks the first configured encoding when a client weights them equally and browsers send
    ``gzip, deflate, br, zstd``) with an explicit ``match`` block that lists the content types the
    dashboard serves. The match never holds a ``text/*`` wildcard, so ``text/event-stream`` is
    never compressed: a compressed stream may be buffered, and events would stop arriving.
  - ``reverse_proxy localhost:8090`` stays, and the site keeps Caddy's automatic TLS.
  - The first fenced block under Step 4 of ``docs/DEPLOYMENT.md`` equals the tracked file apart
    from comments and the site name, and the guide documents the apply, verify and rollback steps.
"""

from __future__ import annotations

import fnmatch
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CADDYFILE = REPO_ROOT / "deploy" / "caddy" / "Caddyfile"
DEPLOYMENT_DOC = REPO_ROOT / "docs" / "DEPLOYMENT.md"
PRODUCTION_SITE = "104-248-7-101.sslip.io"
EVENT_STREAM = "text/event-stream"
# Content types the dashboard serves (one representative header value each).
SERVED_TYPES = (
    "application/json",
    "text/html; charset=utf-8",
    "text/css; charset=utf-8",
    "text/javascript; charset=utf-8",
    "application/javascript",
    "image/svg+xml",
    "text/plain; charset=utf-8",
)


def _tracked_text() -> str:
    assert CADDYFILE.is_file(), "deploy/caddy/Caddyfile must be tracked in the repository"
    return CADDYFILE.read_text(encoding="utf-8")


def _step4_block() -> str:
    text = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    start = text.index("## Step 4")
    end = text.index("## Step 5", start)
    blocks = re.findall(r"```[a-z]*\n(.*?)```", text[start:end], flags=re.S)
    caddy = [b for b in blocks if "reverse_proxy" in b]
    assert len(caddy) == 1, f"Step 4 must show exactly one Caddyfile block, found {len(caddy)}"
    return caddy[0]


def _code_lines(text: str) -> list[str]:
    """The directive lines of a Caddyfile: comments and blanks dropped, whitespace trimmed."""
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _with_site_name_normalized(lines: list[str]) -> list[str]:
    assert lines and lines[0].endswith("{"), f"the first directive must open the site: {lines[:1]}"
    return ["SITE {", *lines[1:]]


def _match_patterns(lines: list[str]) -> list[str]:
    """The Content-Type patterns inside the encode directive's match block."""
    patterns: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("match"):
            inside = True
            continue
        if inside and line == "}":
            break
        if inside:
            parts = line.split()
            assert parts[:2] == ["header", "Content-Type"], f"unexpected matcher line: {line!r}"
            patterns.append(parts[2])
    return patterns


class TestTrackedCaddyfile:
    def test_the_site_is_the_production_host_and_proxies_to_the_daemon(self):
        lines = _code_lines(_tracked_text())

        assert lines[0] == f"{PRODUCTION_SITE} {{"
        assert lines.count("reverse_proxy localhost:8090") == 1
        assert lines[-1] == "}"

    def test_tls_is_left_to_caddys_automatic_certificates(self):
        lines = _code_lines(_tracked_text())

        assert not any(line.startswith(("tls", "auto_https")) for line in lines)

    def test_gzip_is_offered_first_and_zstd_second(self):
        lines = _code_lines(_tracked_text())

        (encode,) = [line for line in lines if line.startswith("encode")]
        assert encode.split()[:3] == ["encode", "gzip", "zstd"]

    def test_the_match_lists_what_the_dashboard_serves_and_never_the_event_stream(self):
        patterns = _match_patterns(_code_lines(_tracked_text()))

        assert patterns, "an explicit match block replaces Caddy's default matcher"
        assert not any(p.startswith("text/*") or p == "*" for p in patterns), (
            "a text/* wildcard would compress the event stream"
        )
        assert not any(fnmatch.fnmatchcase(EVENT_STREAM, p) for p in patterns)
        for served in SERVED_TYPES:
            assert any(fnmatch.fnmatchcase(served, p) for p in patterns), (
                f"{served!r} is served by the dashboard and must be compressed"
            )

    def test_the_header_says_how_to_apply_verify_and_roll_back(self):
        header = "\n".join(
            line for line in _tracked_text().splitlines() if line.strip().startswith("#")
        )

        for word in ("caddy validate", "systemctl reload caddy", "cmp", "Caddyfile.bak"):
            assert word in header, f"the header comment must mention {word!r}"


class TestDeploymentGuideShowsTheSameBlock:
    def test_the_step_4_block_equals_the_tracked_file_apart_from_comments_and_the_site_name(self):
        tracked = _with_site_name_normalized(_code_lines(_tracked_text()))
        documented_lines = _code_lines(_step4_block())
        documented = _with_site_name_normalized(documented_lines)

        assert documented == tracked
        assert documented_lines[0] != f"{PRODUCTION_SITE} {{", (
            "the guide shows a placeholder site name, not the production host"
        )

    def test_the_guide_documents_apply_verify_and_rollback(self):
        text = DEPLOYMENT_DOC.read_text(encoding="utf-8")
        step4 = text[text.index("## Step 4") : text.index("## Step 5")]

        for word in (
            "caddy validate",
            "systemctl reload caddy",
            "cmp /etc/caddy/Caddyfile",
            "Caddyfile.bak",
        ):
            assert word in step4, f"Step 4 must document {word!r}"
        assert "systemctl restart caddy" not in step4, "a reload, never a restart"
