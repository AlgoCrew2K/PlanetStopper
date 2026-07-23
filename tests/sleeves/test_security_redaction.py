"""
RED tests — sleeves security: no secrets in fixtures, no hardcoded credentials
in sleeves/alpaca_orders.py (D-1 pattern extended to the new order module).

Two independent checks:

  1. Fixture hygiene: every schema-derived fixture under
     tests/fixtures/alpaca/orders/ must be free of anything that looks like a
     real Alpaca API key/secret shape. This guards against a future
     contributor "helpfully" replacing a hand-built fixture with a captured
     real response that still has live-looking credential material in it.

  2. Source hygiene: sleeves/alpaca_orders.py must read ALPACA_KEY /
     ALPACA_SECRET / ALPACA_LIVE_KEY / ALPACA_LIVE_SECRET from the
     environment (os.getenv / os.environ) — it must never contain a
     hardcoded credential-shaped string literal as a header value.

D-1 error-message redaction itself (never echo raw exception text) is
covered in test_alpaca_orders_lifecycle.py::TestNoSecretLeakage — this file
does not duplicate those cases.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_WORKTREE_ROOT = Path(__file__).resolve().parents[2]
_ORDER_MODULE_PATH = _WORKTREE_ROOT / "sleeves" / "alpaca_orders.py"
_FIXTURES_DIR = _WORKTREE_ROOT / "tests" / "fixtures" / "alpaca" / "orders"

# Alpaca key-ID prefixes are well-documented and stable: paper keys start
# "PK", live keys start "AK". A 20-character alnum run after either prefix
# is a live-looking key shape, whether or not it's a real credential.
_ALPACA_KEY_SHAPE_RE = re.compile(r"\b(PK|AK)[A-Z0-9]{16,}\b")

# A bearer-token-shaped secret (Alpaca secrets are long base64/alnum blobs).
_LONG_SECRET_SHAPE_RE = re.compile(r"\b[A-Za-z0-9/+]{40,}\b")


# ---------------------------------------------------------------------------
# 1. Fixture hygiene
# ---------------------------------------------------------------------------


class TestFixturesContainNoCredentialShapedValues:
    def _all_fixture_files(self):
        assert _FIXTURES_DIR.exists(), f"fixtures dir missing: {_FIXTURES_DIR}"
        return sorted(_FIXTURES_DIR.glob("*.json"))

    def test_at_least_one_fixture_file_exists(self):
        files = self._all_fixture_files()
        assert files, f"no fixture files found under {_FIXTURES_DIR}"

    def test_no_fixture_contains_an_alpaca_key_id_shape(self):
        offenders = []
        for path in self._all_fixture_files():
            text = path.read_text(encoding="utf-8")
            if _ALPACA_KEY_SHAPE_RE.search(text):
                offenders.append(path.name)
        assert not offenders, (
            f"fixture file(s) contain an Alpaca key-ID-shaped string (PK.../AK...): "
            f"{offenders}. Fixtures must be hand-built/schema-derived, never a captured "
            f"response carrying real-looking credential material."
        )

    def test_no_fixture_contains_a_long_secret_shaped_token(self):
        # asset_id / order id UUIDs are 36 chars with hyphens — excluded by
        # this regex (hyphens break the alnum run). Genuine risk is a long
        # unbroken alnum/base64 blob, which no legitimate Order/Account/
        # Position field in our fixtures should ever contain.
        offenders = []
        for path in self._all_fixture_files():
            text = path.read_text(encoding="utf-8")
            for match in _LONG_SECRET_SHAPE_RE.finditer(text):
                offenders.append((path.name, match.group(0)[:8] + "..."))
        assert not offenders, (
            f"fixture file(s) contain a long unbroken alnum token that could be a "
            f"real secret: {offenders}. Verify these are not accidental credential leaks."
        )

    def test_no_fixture_references_a_dotenv_style_key_name_with_a_value(self):
        # Catches an accidental raw .env dump pasted into a fixture, e.g.
        # `"ALPACA_SECRET": "..."` — a field name that should never appear in
        # an Alpaca API response object.
        offenders = []
        for path in self._all_fixture_files():
            text = path.read_text(encoding="utf-8")
            for key in ("ALPACA_KEY", "ALPACA_SECRET", "ALPACA_LIVE_KEY", "ALPACA_LIVE_SECRET"):
                if key in text:
                    offenders.append((path.name, key))
        assert not offenders, f"fixture(s) reference raw env-var key names: {offenders}"


# ---------------------------------------------------------------------------
# 2. Source hygiene — credentials read from environment, never hardcoded
# ---------------------------------------------------------------------------


class TestOrderModuleReadsCredentialsFromEnvironment:
    def test_alpaca_orders_module_exists(self):
        assert _ORDER_MODULE_PATH.exists(), (
            f"sleeves/alpaca_orders.py not found at {_ORDER_MODULE_PATH}"
        )

    def test_module_references_os_getenv_or_os_environ_for_credentials(self):
        if not _ORDER_MODULE_PATH.exists():
            pytest.skip("alpaca_orders.py not found")
        source = _ORDER_MODULE_PATH.read_text(encoding="utf-8")
        assert "os.getenv" in source or "os.environ" in source, (
            "sleeves/alpaca_orders.py must read credentials via os.getenv/os.environ — "
            "no other credential source (hardcoded literal, config file) is permitted."
        )

    def test_module_contains_no_hardcoded_key_id_shaped_string_literal(self):
        if not _ORDER_MODULE_PATH.exists():
            pytest.skip("alpaca_orders.py not found")
        source = _ORDER_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        offending_literals = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _ALPACA_KEY_SHAPE_RE.search(node.value):
                    offending_literals.append(node.value)
        assert not offending_literals, (
            f"sleeves/alpaca_orders.py contains a hardcoded Alpaca key-ID-shaped string "
            f"literal: {offending_literals}. Credentials must come from the environment only."
        )

    def test_module_does_not_log_or_print_the_secret_env_values(self):
        if not _ORDER_MODULE_PATH.exists():
            pytest.skip("alpaca_orders.py not found")
        source = _ORDER_MODULE_PATH.read_text(encoding="utf-8")
        # A defensive static check: the module must not pass the secret env
        # var directly into a logging/print call. This won't catch every
        # possible indirection, but it catches the common direct mistake.
        suspicious_patterns = [
            re.compile(r"(print|log\w*\.\w+)\([^)]*ALPACA_SECRET"),
            re.compile(r"(print|log\w*\.\w+)\([^)]*ALPACA_LIVE_SECRET"),
        ]
        offenders = [p.pattern for p in suspicious_patterns if p.search(source)]
        assert not offenders, (
            f"sleeves/alpaca_orders.py appears to log/print a secret env var directly: {offenders}"
        )
