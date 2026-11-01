"""The offline stubs of tests/ai_advisor/ leave every seam the way they found it.

The directory's autouse fixture stubs `time.sleep`, `requests.get` and the technicals bar fetch for
every test. A test that patches one of the same seams through `monkeypatch` is torn down in the
opposite order of their setup, and the shared `monkeypatch` fixture is created by an outer autouse
fixture before the directory's one. If the directory's stubs are context managers they are
exited first and put the real function back; `monkeypatch` is undone last and puts back what it
found when the test patched the seam, which is the stub. The stub then stays for every later test
of that worker: a `time.sleep` that is a `MagicMock` turns every polling loop in a later file into
a busy spin that records each call, which grows without bound (a hung fake engine process in the
daemon's run-bound tests used 12 GB in 90 seconds).

The directory's real conftest is run here, in a nested session next to an outer autouse fixture
that creates `monkeypatch` first (what `tests/conftest.py` does), with a test that patches each
seam through `monkeypatch`. What the three seams are after the nested session's last teardown is
what a later test would see.
"""

from __future__ import annotations

import pathlib
import time

import pytest
import requests

import advisors.lens_technicals as lens_technicals

DIRECTORY_CONFTEST = pathlib.Path(__file__).with_name("conftest.py")

# What a test that patches the seams through monkeypatch looks like.
PATCHING_TESTS = '''
import time

import requests

import advisors.lens_technicals as lens_technicals


def test_a_test_patches_each_seam_through_monkeypatch(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: None)
    monkeypatch.setattr(lens_technicals, "_get_bars", lambda *_a, **_k: {})


def test_the_next_test_runs_on_the_directory_stubs():
    assert callable(time.sleep)
'''

# Stands in for the outer autouse fixture of tests/conftest.py that requests monkeypatch.
OUTER_CONFTEST = """
import pytest


@pytest.fixture(autouse=True)
def _outer_autouse_creates_monkeypatch_first(monkeypatch):
    return monkeypatch
"""


class _SeamsAfterEachTest:
    """The identity of each seam after the last teardown of every nested test."""

    def __init__(self) -> None:
        self.after_each: list[dict[str, object]] = []

    def pytest_runtest_logfinish(self, nodeid, location):
        self.after_each.append(
            {
                "nodeid": nodeid,
                "time.sleep": time.sleep,
                "requests.get": requests.get,
                "lens_technicals._get_bars": lens_technicals._get_bars,
            }
        )


@pytest.fixture()
def real_seams():
    """The three seams as they are now, put back whatever the nested session does."""
    real = {
        "time.sleep": time.sleep,
        "requests.get": requests.get,
        "lens_technicals._get_bars": lens_technicals._get_bars,
    }
    yield real
    time.sleep = real["time.sleep"]
    requests.get = real["requests.get"]
    lens_technicals._get_bars = real["lens_technicals._get_bars"]


def test_every_seam_is_the_real_one_again_after_a_test_that_patched_it_through_monkeypatch(
    tmp_path, real_seams
):
    suite = tmp_path / "nested_suite"
    (suite / "ai_advisor").mkdir(parents=True)
    (suite / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (suite / "conftest.py").write_text(OUTER_CONFTEST, encoding="utf-8")
    (suite / "ai_advisor" / "conftest.py").write_text(
        DIRECTORY_CONFTEST.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (suite / "ai_advisor" / "test_patching.py").write_text(PATCHING_TESTS, encoding="utf-8")

    seen = _SeamsAfterEachTest()
    status = pytest.main(
        [
            str(suite),
            f"--rootdir={suite}",
            "-c",
            str(suite / "pytest.ini"),
            "-p",
            "no:cacheprovider",
            "--capture=sys",
            "--disable-warnings",
            "-q",
        ],
        plugins=[seen],
    )

    assert status == pytest.ExitCode.OK, f"the nested session did not pass: {status!r}"
    assert len(seen.after_each) == 2, "the nested session did not run both of its tests"
    leaked = [
        f"{name} after {entry['nodeid']}: {entry[name]!r}"
        for entry in seen.after_each
        for name in real_seams
        if entry[name] is not real_seams[name]
    ]
    assert not leaked, (
        "a seam of the directory's stubs was left patched for the next test: " + "; ".join(leaked)
    )
