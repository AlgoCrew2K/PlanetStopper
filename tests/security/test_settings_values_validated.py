"""``POST /api/settings`` validates every value before it writes any, all or nothing.

The key allowlist (tests/security/test_a1_settings_allowlist.py) is not enough on its own: the file
writer quotes a value and escapes only the quote character, so a value ending in a backslash lets the
next quote in the file close it, and a second allowlisted key can then carry a whole line of its own
(``LIVE_EXECUTION=True``). The route therefore checks the values too.

Contract pinned
* Algorithm parameters (the ``_ALGO_PARAM_META`` keys) take a finite number; ``EXECUTION_START_TIME`` takes
  exactly ``HH:MM``; ``EXIT_AUTHORITY`` takes one of the two values the engine knows.
* One bad value anywhere in the payload (a global or a symphony parameter) is a 400 and nothing is
  written: the ``.env`` file stays byte-identical and no symphony strategy is saved.
* The payload that injected ``LIVE_EXECUTION=True`` is a 400 and leaves ``.env`` as it was.
* A value that is accepted leaves a well-formed file: no key appears that the payload did not name.
* Valid payloads still work.

The ``.env`` is a temp file; the database is a stand-in that records what the route saves. Nothing
reaches the developer's ``.env``.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest
from dotenv import dotenv_values

import app as app_module

ORIGINAL_ENV = (
    "LIVE_EXECUTION=False\n"
    "EXECUTION_START_TIME=09:30\n"
    "EXIT_AUTHORITY=per_symphony\n"
    "TRIGGER_THRESHOLD_PCT=40\n"
    "COMPOSER_KEY_ID=placeholder\n"
)
ALGO_KEY = "TRIGGER_THRESHOLD_PCT"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_bytes(ORIGINAL_ENV.encode("utf-8"))
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(path))
    return path


@pytest.fixture
def database_stub(monkeypatch):
    stub = MagicMock()
    stub.save_symphony_strategy.return_value = None
    monkeypatch.setattr(app_module, "database", stub)
    return stub


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _post(client, globals_payload=None, symphonies=None):
    body = {}
    if globals_payload is not None:
        body["globals"] = globals_payload
    if symphonies is not None:
        body["symphonies"] = symphonies
    return client.post("/api/settings", json=body)


def _assert_refused_and_untouched(response, env_file, database_stub):
    assert response.status_code == 400, (
        f"answered {response.status_code}: {response.get_data(as_text=True)[:200]!r}"
    )
    assert env_file.read_bytes() == ORIGINAL_ENV.encode("utf-8"), "the .env file changed"
    database_stub.save_symphony_strategy.assert_not_called()


class TestTheInjectionPayload:
    def test_the_payload_that_wrote_live_execution_is_a_400_and_env_is_byte_identical(
        self, client, env_file, database_stub
    ):
        # The backslash ends the first value so that the file writer's closing quote reads as
        # escaped and the parser runs on into the next value's line.
        response = _post(
            client,
            {"EXECUTION_START_TIME": "09:30\\", "EXIT_AUTHORITY": "\nLIVE_EXECUTION=True\n#"},
        )
        _assert_refused_and_untouched(response, env_file, database_stub)
        assert dotenv_values(env_file)["LIVE_EXECUTION"] == "False"

    def test_a_line_break_hidden_in_an_algorithm_value_is_a_400(
        self, client, env_file, database_stub
    ):
        response = _post(client, {ALGO_KEY: "35\nLIVE_EXECUTION=True"})
        _assert_refused_and_untouched(response, env_file, database_stub)


BAD_START_TIMES = [
    "9:30",
    "09:30\n",
    "09:30\\",
    "09:30'",
    "09:30:00",
    "0930",
    "",
    "ab:cd",
    930,
    ["09:30"],
    None,
    True,
]
BAD_AUTHORITIES = [
    "LIVE",
    "per_symphony\n",
    "PER_SYMPHONY",
    "",
    "port_level;x",
    None,
    1,
    ["port_level"],
]
BAD_NUMBERS = [
    "abc",
    "nan",
    "NaN",
    "inf",
    "-inf",
    "Infinity",
    "1e999",
    "",
    None,
    [1],
    {"a": 1},
    "1.5\nLIVE_EXECUTION=True",
    "0x10",
    float("inf"),
]


class TestEachGlobalKeyTakesItsOwnKindOfValue:
    @pytest.mark.parametrize("value", BAD_START_TIMES, ids=repr)
    def test_a_bad_start_time_is_a_400_and_nothing_is_written(
        self, client, env_file, database_stub, value
    ):
        response = _post(client, {"EXECUTION_START_TIME": value})
        _assert_refused_and_untouched(response, env_file, database_stub)

    @pytest.mark.parametrize("value", BAD_AUTHORITIES, ids=repr)
    def test_an_unknown_exit_authority_is_a_400_and_nothing_is_written(
        self, client, env_file, database_stub, value
    ):
        response = _post(client, {"EXIT_AUTHORITY": value})
        _assert_refused_and_untouched(response, env_file, database_stub)

    @pytest.mark.parametrize("value", BAD_NUMBERS, ids=repr)
    def test_a_value_that_is_not_a_finite_number_for_an_algorithm_key_is_a_400(
        self, client, env_file, database_stub, value
    ):
        response = _post(client, {ALGO_KEY: value})
        _assert_refused_and_untouched(response, env_file, database_stub)

    @pytest.mark.parametrize("key", sorted(app_module._ALGO_PARAM_META))
    def test_every_algorithm_key_checks_its_value(self, client, env_file, database_stub, key):
        response = _post(client, {key: "not-a-number"})
        _assert_refused_and_untouched(response, env_file, database_stub)


class TestAllOrNothing:
    def test_one_bad_value_beside_good_ones_writes_none_of_them(
        self, client, env_file, database_stub
    ):
        response = _post(
            client,
            {"EXECUTION_START_TIME": "09:35", "EXIT_AUTHORITY": "port_level", ALGO_KEY: "nan"},
        )
        _assert_refused_and_untouched(response, env_file, database_stub)

    def test_a_bad_global_beside_a_good_symphony_saves_no_strategy(
        self, client, env_file, database_stub
    ):
        response = _post(
            client,
            {"EXECUTION_START_TIME": "9:30"},
            {"alpha": {"params": {ALGO_KEY: "30"}, "locked_vars": []}},
        )
        _assert_refused_and_untouched(response, env_file, database_stub)

    @pytest.mark.parametrize("value", ["abc", "nan", "inf", None, [1]], ids=repr)
    def test_a_bad_symphony_parameter_beside_a_good_global_is_a_400_and_writes_nothing(
        self, client, env_file, database_stub, value
    ):
        response = _post(
            client,
            {"EXECUTION_START_TIME": "09:35"},
            {"alpha": {"params": {ALGO_KEY: value}, "locked_vars": []}},
        )
        _assert_refused_and_untouched(response, env_file, database_stub)

    def test_a_bad_parameter_in_the_second_symphony_saves_neither(
        self, client, env_file, database_stub
    ):
        response = _post(
            client,
            None,
            {
                "alpha": {"params": {ALGO_KEY: "30"}, "locked_vars": []},
                "beta": {"params": {ALGO_KEY: "oops"}, "locked_vars": []},
            },
        )
        _assert_refused_and_untouched(response, env_file, database_stub)


class TestValidPayloadsStillWork:
    def test_good_globals_are_written(self, client, env_file, database_stub):
        response = _post(
            client,
            {
                "EXECUTION_START_TIME": "09:35",
                "EXIT_AUTHORITY": "port_level",
                ALGO_KEY: "35.5",
                "MAX_SQUEEZE_FLOOR": 0.25,
            },
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        written = dotenv_values(env_file)
        assert written["EXECUTION_START_TIME"] == "09:35"
        assert written["EXIT_AUTHORITY"] == "port_level"
        assert float(written[ALGO_KEY]) == 35.5
        assert float(written["MAX_SQUEEZE_FLOOR"]) == 0.25
        assert written["LIVE_EXECUTION"] == "False", "an unrelated key moved"

    @pytest.mark.parametrize("time", ["00:00", "09:30", "23:59"])
    def test_the_edges_of_the_clock_are_accepted(self, client, env_file, database_stub, time):
        response = _post(client, {"EXECUTION_START_TIME": time})
        assert response.status_code == 200
        assert dotenv_values(env_file)["EXECUTION_START_TIME"] == time

    @pytest.mark.parametrize("value", ["-1.5", "0", 0, 12, "1e-3", 3.75])
    def test_any_finite_number_is_accepted_for_an_algorithm_key(
        self, client, env_file, database_stub, value
    ):
        response = _post(client, {ALGO_KEY: value})
        assert response.status_code == 200, response.get_data(as_text=True)
        assert float(dotenv_values(env_file)[ALGO_KEY]) == float(value)

    def test_good_symphony_strategies_are_saved_as_floats(self, client, env_file, database_stub):
        response = _post(
            client,
            {"EXIT_AUTHORITY": "per_symphony"},
            {"alpha": {"params": {ALGO_KEY: "30", "MAX_SQUEEZE_FLOOR": 0.2}, "locked_vars": ["X"]}},
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        database_stub.save_symphony_strategy.assert_called_once_with(
            "alpha", {ALGO_KEY: 30.0, "MAX_SQUEEZE_FLOOR": 0.2}, ["X"]
        )


class TestWhatIsAcceptedLeavesAWellFormedFile:
    @pytest.mark.parametrize("value", ["1.5\n", " 7 ", "7\r", "7\t"], ids=repr)
    def test_whitespace_around_a_number_is_refused_or_never_reaches_the_file(
        self, client, env_file, database_stub, value
    ):
        response = _post(client, {ALGO_KEY: value})
        if response.status_code == 400:
            assert env_file.read_bytes() == ORIGINAL_ENV.encode("utf-8")
            return
        assert response.status_code == 200
        written = dotenv_values(env_file)
        assert set(written) <= {
            "LIVE_EXECUTION",
            "EXECUTION_START_TIME",
            "EXIT_AUTHORITY",
            ALGO_KEY,
            "COMPOSER_KEY_ID",
            "_exit_authority_changed_at",
        }, f"the payload added a key to the file: {sorted(written)}"
        assert math.isfinite(float(written[ALGO_KEY]))
        raw = env_file.read_text(encoding="utf-8")
        assert len(raw.splitlines()) == len(ORIGINAL_ENV.splitlines()), raw
