"""Round 3 (tech debt): a runaway token fails the symphony, each symphony is logged when decided,
and regime_label_job.py carries the documentation the reviewers named.

Provenance: HTTP is the captured-shape AlpacaStub of tests/regime_label_job/_sip.py; the static
checks read regime_label_job.py and the two engine-pin test files as source text.
"""

from __future__ import annotations

import ast
import io
import logging
import re
import tokenize
from pathlib import Path

import pytest

import database
import regime_label_job
from tests.regime_label_job import _sip
from tests.regime_label_job.conftest import (
    ID_ONE,
    ID_THREE,
    ID_TWO,
    captured_entry,
    golden_closes,
)

LOGGER = "alphabot.regime_label"
_REQUEST_CEILING = 500


class RunawayRequests(BaseException):
    """Raised past a request ceiling so a missing cap fails the test instead of hanging it.

    A BaseException so no `except Exception` in the job can turn it into a handled failure."""


_REPO = Path(__file__).resolve().parents[2]
_JOB_SOURCE = _REPO / "regime_label_job.py"


@pytest.fixture()
def log(caplog):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="alphabot")
    return caplog


def _entries(*items) -> dict:
    return {sid: captured_entry(name, _sip.holdings_of(*pairs)) for sid, name, pairs in items}


def _job_messages(log) -> list[str]:
    return [r.getMessage() for r in log.records if r.name == LOGGER and r.levelno >= logging.INFO]


class TestRunawayTokenAtJobLevel:
    def test_a_runaway_token_fails_the_symphony_with_price_fetch_failed_and_writes_nothing(
        self, job_harness
    ):
        counter = {"n": 0}

        def runaway(params):
            counter["n"] += 1
            if counter["n"] > _REQUEST_CEILING:
                raise RunawayRequests("the job kept requesting past the ceiling")
            body = _sip.page_body({"AAA": golden_closes("trending")}, f"t{counter['n']}")
            return _sip.Resp(200, body)

        http = job_harness.serve({}, runaway)
        entries = _entries((ID_ONE, "Alpha Test Symphony", [("AAA", 1.0)]))

        run = job_harness.run(entries)

        (outcome,) = run.outcomes
        assert outcome.reason == regime_label_job.REASON_PRICE_FETCH_FAILED
        assert outcome.label is None
        assert job_harness.rows() == {}
        assert len(http.calls) <= regime_label_job.MAX_PAGES_PER_BATCH


class TestEachSymphonyIsLoggedWhenDecided:
    def test_a_symphony_line_exists_before_the_next_symphony_is_written(
        self, job_harness, log, monkeypatch
    ):
        job_harness.serve(
            {"AAA": golden_closes("trending"), "BBB": golden_closes("mean-reverting")}
        )
        entries = _entries(
            (ID_ONE, "Alpha Test Symphony", [("AAA", 1.0)]),
            (ID_TWO, "Beta Test Symphony", [("BBB", 1.0)]),
        )
        real_save = database.save_regime_label
        seen_at_write: dict[str, list[str]] = {}

        def spying_save(sid, label, as_of):
            seen_at_write[sid] = _job_messages(log)
            return real_save(sid, label, as_of)

        monkeypatch.setattr(database, "save_regime_label", spying_save)

        job_harness.run(entries)

        assert set(seen_at_write) == {ID_ONE, ID_TWO}
        assert not any(f"symphony={ID_ONE}" in m for m in seen_at_write[ID_ONE]), "premise"
        assert any(f"symphony={ID_ONE}" in m for m in seen_at_write[ID_TWO]), (
            "the first symphony's line must be logged before the second symphony's row is written"
        )

    def test_a_symphony_decided_without_a_write_is_logged_before_a_later_write(
        self, job_harness, log, monkeypatch
    ):
        job_harness.serve({"BBB": golden_closes("mean-reverting")})
        entries = _entries(
            (ID_ONE, "No Holdings Symphony", []),
            (ID_TWO, "Beta Test Symphony", [("BBB", 1.0)]),
        )
        real_save = database.save_regime_label
        before_write: list[str] = []

        def spying_save(sid, label, as_of):
            before_write.extend(_job_messages(log))
            return real_save(sid, label, as_of)

        monkeypatch.setattr(database, "save_regime_label", spying_save)

        job_harness.run(entries)

        assert any(f"symphony={ID_ONE}" in m for m in before_write)

    def test_the_summary_is_last_and_symphony_lines_follow_roster_order(self, job_harness, log):
        job_harness.serve({"AAA": golden_closes("trending")})
        entries = _entries(
            (ID_ONE, "Alpha Test Symphony", [("AAA", 1.0)]),
            (ID_TWO, "No Holdings Symphony", []),
            (ID_THREE, "Short Symphony", [("ZZZ", 1.0)]),
        )

        job_harness.run(entries)

        messages = _job_messages(log)
        assert len(messages) == 4, messages
        assert "summary" in messages[-1].split()[:2]
        assert all("summary" not in m.split()[:2] for m in messages[:-1])
        found = [re.search(r"symphony=(\S+)", m).group(1) for m in messages[:-1]]
        assert found == [ID_ONE, ID_TWO, ID_THREE]


# ---------------------------------------------------------------------------
# Static documentation checks on regime_label_job.py (CR-1, CR-2, QR-1)
# ---------------------------------------------------------------------------

_COMMENTED_CONSTANTS = (
    "SIP_END_LAG",
    "SIP_BATCH_SIZE",
    "MAX_ATTEMPTS",
    "REQUEST_TIMEOUT_SECONDS",
    "RETRY_AFTER_CAP_SECONDS",
    "REGIME_WINDOW_MAX_LOOKBACK_SESSIONS",
    "MAX_PAGES_PER_BATCH",
    "_BACKOFF_START_SECONDS",
    "_PAGE_PAUSE_SECONDS",
)
_COPIED_FROM_SIBLING = ("MAX_ATTEMPTS", "REQUEST_TIMEOUT_SECONDS", "RETRY_AFTER_CAP_SECONDS")


def _comment_rows() -> set[int]:
    source = _JOB_SOURCE.read_text(encoding="utf-8")
    stream = io.StringIO(source).readline
    return {t.start[0] for t in tokenize.generate_tokens(stream) if t.type == tokenize.COMMENT}


def _comment_text_for(name: str) -> str:
    """The same-line comment plus the contiguous comment block directly above the assignment."""
    source = _JOB_SOURCE.read_text(encoding="utf-8")
    lines = source.splitlines()
    commented = _comment_rows()
    for node in ast.parse(source).body:
        targets = [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            text = []
            if node.lineno in commented:
                text.append(lines[node.lineno - 1].split("#", 1)[1])
            row = node.lineno - 1
            while row in commented:
                text.append(lines[row - 1].lstrip().lstrip("#"))
                row -= 1
            return " ".join(text)
    raise AssertionError(f"{name} is not a module-level constant of regime_label_job.py")


@pytest.mark.parametrize("name", _COMMENTED_CONSTANTS)
def test_each_tuning_constant_carries_a_source_comment(name):
    assert len(_comment_text_for(name).strip()) >= 10, f"{name} needs a comment giving its source"


@pytest.mark.parametrize("name", _COPIED_FROM_SIBLING)
def test_a_constant_copied_from_the_composer_series_client_names_it_as_the_approach(name):
    assert "composer_series_client" in _comment_text_for(name)


def _all_functions() -> list:
    tree = ast.parse(_JOB_SOURCE.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]


def test_every_function_annotates_every_parameter_and_its_return():
    missing = []
    for fn in _all_functions():
        params = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
        params = [p for p in params if p.arg not in ("self", "cls")]
        extra = [a for a in (fn.args.vararg, fn.args.kwarg) if a is not None]
        if any(p.annotation is None for p in [*params, *extra]) or fn.returns is None:
            missing.append(fn.name)
    assert missing == []


def test_every_non_dunder_function_has_a_docstring():
    missing = [
        fn.name
        for fn in _all_functions()
        if not (fn.name.startswith("__") and fn.name.endswith("__")) and not ast.get_docstring(fn)
    ]
    assert missing == []


# ---------------------------------------------------------------------------
# CR-4: the comments above the re-pinned engine hashes describe the current pin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "anchor"),
    [
        ("tests/test_mdd_window_truth_zero_diff_guard.py", '"alpha_bot_execution.py":'),
        ("tests/engine/test_close_snapshot_builder_scope.py", "_PINNED_HASH_WITHOUT_BUILDER ="),
    ],
)
def test_the_comment_above_a_re_pinned_hash_names_the_session_date_keyword_change(relative, anchor):
    lines = (_REPO / relative).read_text(encoding="utf-8").splitlines()
    index = next(i for i, line in enumerate(lines) if line.strip().startswith(anchor))
    block = []
    row = index - 1
    while row >= 0 and lines[row].lstrip().startswith("#"):
        block.append(lines[row])
        row -= 1
    assert "session_date" in " ".join(block), "the pin comment must describe the current re-pin"


# ---------------------------------------------------------------------------
# The summary line's `outcomes` annotation names what the function does with it
# ---------------------------------------------------------------------------


def _outcome(reason: str | None, label: str | None = None) -> regime_label_job.SymphonyOutcome:
    labelled = reason is None
    return regime_label_job.SymphonyOutcome(
        "kJ2hG8dS5aQ1wE9rT4yU",
        "Alpha Test Symphony",
        label if labelled else None,
        reason,
        _sip.NEWEST_ELIGIBLE if labelled else None,
        _sip.NEWEST_ELIGIBLE if labelled else None,
        20 if labelled else 0,
        None,
    )


def test_the_summary_outcomes_annotation_supports_len_and_repeated_iteration():
    import collections.abc
    import typing

    hint = typing.get_type_hints(regime_label_job._log_summary)["outcomes"]
    origin = typing.get_origin(hint) or hint

    assert isinstance(origin, type), f"outcomes is annotated {hint!r}, not a single class"
    assert issubclass(origin, collections.abc.Sized), (
        f"{hint!r} has no len(), yet the summary counts the outcomes with it"
    )
    assert issubclass(origin, collections.abc.Iterable)
    assert not issubclass(origin, collections.abc.Iterator), (
        f"{hint!r} may be iterated once, yet the summary iterates it three times"
    )


def _summary_message(caplog) -> str:
    (line,) = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    return line


def test_the_summary_of_an_empty_run_and_of_a_run_of_outcomes_keeps_its_lines(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    session = _sip.TARGET_D
    outcomes = (
        _outcome(None, "trending"),
        _outcome("price_fetch_failed"),
        _outcome("insufficient_history"),
        _outcome("price_fetch_failed"),
    )

    regime_label_job._log_summary(session, (), None)
    empty = _summary_message(caplog)
    caplog.clear()
    regime_label_job._log_summary(session, outcomes, None)
    full = _summary_message(caplog)
    caplog.clear()
    regime_label_job._log_summary(session, (), "OperationalError")
    failed = _summary_message(caplog)

    day = session.isoformat()
    assert empty == f"regime_label summary session={day} seen=0 labelled=0"
    assert full == (
        f"regime_label summary session={day} seen=4 labelled=1"
        " insufficient_history=1 price_fetch_failed=2"
    )
    assert failed == f"regime_label summary session={day} seen=0 labelled=0 failed=OperationalError"


def test_the_summary_counts_a_list_of_outcomes_the_same_as_a_tuple(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    outcomes = [_outcome(None, "trending"), _outcome("no_holdings")]

    regime_label_job._log_summary(_sip.TARGET_D, outcomes, None)

    assert _summary_message(caplog).endswith("seen=2 labelled=1 no_holdings=1")
