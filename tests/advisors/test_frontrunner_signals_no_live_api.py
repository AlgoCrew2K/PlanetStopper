"""RED tests — AC-8 (no live API anywhere) + AC-9 (off-execution-path) for
the whole frontrunner-signals feature.

CONTRACT SOURCE (feature-plans/frontrunner-signals.md):
AC-8: "All tests mock the Atlas seam (the module's fetch fn) and the
Composer seam (composer_backtest_client.run_backtest /
symphony_logic.fetch_symphony_score); the full battery passes CREDENTIAL-LESS
(all 7 cred env vars = '') AND with-creds; zero live network in pytest
(execution_seam_detector must stay green, extended to the Mongo/pymongo seam
if not already covered)."
AC-9: "Nothing in this cycle is imported by alpha_bot_execution.py / the
minute engine path; CC-2 lazy imports at every daemon-adjacent boundary;
is_live semantics untouched; _SETTINGS_WRITE_ALLOWLIST untouched;
acceptance_gate.py / autotuner.py / math_engine.py ZERO diff."

This file is the WHOLE-BATCH meta-test, written last so it can exercise
every new module name this cycle introduces (advisors.frontrunner_signals,
extract_fr_checks/detect_frontrunner_cascades additions to
advisors.frontrunner_detector, and the new signal-gating surface on
advisors.frontrunner_builder). It does NOT duplicate the per-module
unmocked-fetch-fails-fast proofs already covered in
test_frontrunner_signals_ingest.py — it proves the CREDENTIAL-LESS +
off-execution-path invariants across the FEATURE as a whole.

The Mongo/pymongo seam itself is guarded session-wide by the EXISTING
autouse fixture in tests/conftest.py (verified: `pymongo.MongoClient` is
patched to raise for ANY unmocked construction, proven by
tests/test_no_live_mongo_guard.py) — this file adds this cycle's OWN
"unmocked construction still fails fast, doesn't hang" proof rather than
re-testing the guard mechanism itself.

acceptance_gate.py/autotuner.py/math_engine.py zero-diff is a PM/reviewer
diff check against the merge-base, not a pytest assertion — no test for it
here (there is nothing this test process can assert about a git diff that a
reviewer verifying the actual PR diff can't do more reliably).
"""

from __future__ import annotations

import ast
import pathlib
import time
from unittest.mock import patch

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The 7 credential env vars this project's live integrations read (matches
# the established "7 cred env vars" convention used across R2-1/R2-2/R2-3).
_CRED_ENV_VARS = (
    "COMPOSER_KEY_ID",
    "COMPOSER_SECRET",
    "ALPACA_KEY",
    "ALPACA_SECRET",
    "DISCORD_WEBHOOK_URL",
    "ANTHROPIC_API_KEY",
    "FRED_API_KEY",
)


@pytest.fixture
def credential_less_env(monkeypatch):
    """Sets all 7 cred vars to "" (empty, NOT unset — .env auto-refills unset
    per the project's known credential-less test gotcha), plus MONGO_URI
    (the 8th credential this feature specifically introduces a live-read
    path for). Explicit per-test fixture (not autouse) so the with-creds
    test in this file can opt out without needing a custom pytest marker
    (this project's pyproject.toml runs --strict-markers, so an
    unregistered marker would fail collection)."""
    for var in _CRED_ENV_VARS:
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("MONGO_URI", "")


def _read_source(relative_path: str) -> str:
    path = _REPO_ROOT / relative_path
    if not path.exists():
        pytest.fail(f"expected module source not found: {relative_path}")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-8: credential-less full path completes honestly, never raises
# ---------------------------------------------------------------------------


def test_load_frontrunner_signals_credential_less_degrades_honestly(credential_less_env):
    """With MONGO_URI="" and every other cred var empty, calling
    load_frontrunner_signals must never raise and must degrade to
    available=False (there is no credential to attempt a connection with)."""
    from advisors import frontrunner_signals

    with patch("advisors.atlas_cache.cached_pull", return_value=None):
        try:
            result = frontrunner_signals.load_frontrunner_signals()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"credential-less call raised {type(exc).__name__}: {exc}")

    assert isinstance(result, dict)
    assert result.get("available") is False


def test_extract_fr_checks_credential_less_is_pure_no_network_at_all(credential_less_env):
    """extract_fr_checks is a pure tree-walk function — credential-less env
    must have zero effect on it; it must succeed identically regardless."""
    from advisors import frontrunner_detector

    tree = {"step": "if", "id": "x", "children": []}
    try:
        result = frontrunner_detector.extract_fr_checks(tree)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"extract_fr_checks raised {type(exc).__name__}: {exc} under credential-less env"
        )
    assert isinstance(result, list)


def test_classify_fr_checks_credential_less_is_pure_no_network_at_all(credential_less_env):
    from advisors import frontrunner_signals

    try:
        result = frontrunner_signals.classify_fr_checks([], [])
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"classify_fr_checks raised {type(exc).__name__}: {exc} under credential-less env"
        )
    assert result == []


def test_load_frontrunner_signals_with_creds_still_uses_the_mocked_seam_not_live(monkeypatch):
    """With-creds path: a real-looking (but fake) MONGO_URI must still route
    through the mocked atlas_cache seam, never attempt a genuine connection —
    proves the with-creds path is exercised too, not just credential-less."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-with-creds-test/db")
    from advisors import frontrunner_signals

    with patch("advisors.atlas_cache.cached_pull", return_value=None):
        result = frontrunner_signals.load_frontrunner_signals()

    assert isinstance(result, dict)
    assert result.get("available") is False


# ---------------------------------------------------------------------------
# AC-8: unmocked Atlas construction fails fast, never hangs (per-module proof
# for advisors.frontrunner_signals specifically, mirroring the established
# community_strats / test_no_live_mongo_guard.py convention)
# ---------------------------------------------------------------------------


def test_unmocked_frontrunner_signals_fetch_fails_fast_via_session_guard(monkeypatch):
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-never-actually-dialed/db")
    from advisors import frontrunner_signals

    start = time.monotonic()
    result = frontrunner_signals.load_frontrunner_signals(force_refresh=True)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, (
        f"load_frontrunner_signals took {elapsed:.2f}s with an unmocked pymongo.MongoClient — "
        "the session-autouse guard (tests/conftest.py) should make this fail in milliseconds."
    )
    assert result["available"] is False


# ---------------------------------------------------------------------------
# AC-9: off-execution-path — alpha_bot_execution.py never imports this cycle's modules
# ---------------------------------------------------------------------------


def _imports_module(source: str, target: str) -> bool:
    """AST-based import check — never a substring/text search (avoids false
    positives from a comment or unrelated string mentioning the module name)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == target or alias.name.startswith(target + "."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == target or module.startswith(target + "."):
                return True
            if module in ("advisors",) and any(a.name == target.split(".")[-1] for a in node.names):
                return True
    return False


def test_alpha_bot_execution_never_imports_frontrunner_signals():
    source = _read_source("alpha_bot_execution.py")
    assert not _imports_module(source, "advisors.frontrunner_signals"), (
        "alpha_bot_execution.py imports advisors.frontrunner_signals — AC-9 violation: "
        "nothing in this cycle may be imported by the minute engine path."
    )


def test_alpha_bot_execution_never_imports_frontrunner_builder_or_detector():
    """Pre-existing modules too — this cycle extends them; the off-execution-
    path boundary must hold for the extended surface, not just the new module."""
    source = _read_source("alpha_bot_execution.py")
    for module_name in ("advisors.frontrunner_builder", "advisors.frontrunner_detector"):
        assert not _imports_module(source, module_name), (
            f"alpha_bot_execution.py imports {module_name} — AC-9 violation."
        )


def test_frontrunner_signals_module_lazy_imports_pymongo_inside_fetch_fn_only():
    """CC-2: pymongo is a lazy, function-scoped import — never a module-level
    import (which would make advisors.frontrunner_signals unimportable
    without pymongo installed, and would risk an import-time connection
    attempt on daemon startup)."""
    source = (
        _read_source("advisors/frontrunner_signals.py")
        if (_REPO_ROOT / "advisors" / "frontrunner_signals.py").exists()
        else pytest.fail(
            "advisors/frontrunner_signals.py does not exist yet — expected RED until GREEN lands"
        )
    )
    tree = ast.parse(source)
    module_level_pymongo_imports = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            (isinstance(node, ast.Import) and any(a.name == "pymongo" for a in node.names))
            or (isinstance(node, ast.ImportFrom) and node.module == "pymongo")
        )
    ]
    assert not module_level_pymongo_imports, (
        "advisors/frontrunner_signals.py imports pymongo at MODULE level — must be lazy-"
        "imported inside the fetch closure only (CC-2 boundary)."
    )


# ---------------------------------------------------------------------------
# AC-9: is_live / _SETTINGS_WRITE_ALLOWLIST untouched — structural, not policy
# ---------------------------------------------------------------------------


def test_frontrunner_signals_module_never_references_settings_write_allowlist():
    """AC-9: '_SETTINGS_WRITE_ALLOWLIST untouched' — the new module must never
    even reference this app.py symbol; it has no legitimate reason to."""
    source = (
        _read_source("advisors/frontrunner_signals.py")
        if (_REPO_ROOT / "advisors" / "frontrunner_signals.py").exists()
        else pytest.fail(
            "advisors/frontrunner_signals.py does not exist yet — expected RED until GREEN lands"
        )
    )
    assert "_SETTINGS_WRITE_ALLOWLIST" not in source, (
        "advisors/frontrunner_signals.py references _SETTINGS_WRITE_ALLOWLIST — "
        "AC-9 violation, this module has no legitimate reason to touch dashboard write gating."
    )


def test_frontrunner_signals_module_never_sets_live_execution():
    source = (
        _read_source("advisors/frontrunner_signals.py")
        if (_REPO_ROOT / "advisors" / "frontrunner_signals.py").exists()
        else pytest.fail(
            "advisors/frontrunner_signals.py does not exist yet — expected RED until GREEN lands"
        )
    )
    assert "LIVE_EXECUTION" not in source, (
        "advisors/frontrunner_signals.py references LIVE_EXECUTION — AC-9 violation, "
        "this module is advisory-only ingest/classification, never a trade-execution path."
    )
