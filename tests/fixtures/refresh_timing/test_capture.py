"""Tests for the shared capture script: read-only opener, case discovery, GET recorder.

Run serially (`-n0`): the GET tests bind a loopback port and the discovery tests spawn
a `python -I` subprocess.
"""

from __future__ import annotations

import gzip
import hashlib
import http.server
import json
import socket
import sqlite3
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parent
FIXED_DATE_HEADER = "Thu, 22 Oct 2026 14:31:07 GMT"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plain_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('one')")
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# Read-only opener
# ---------------------------------------------------------------------------


class TestReadOnlyOpen:
    def test_write_through_the_opened_connection_fails_and_leaves_the_file_unchanged(
        self, capture, tmp_path
    ):
        db = _plain_db(tmp_path / "state.db")
        before = _sha(db)
        conn = capture.open_readonly(db)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO t (v) VALUES ('two')")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE extra (x INTEGER)")
        conn.close()
        assert _sha(db) == before

    def test_mode_ro_alone_blocks_writes_when_query_only_is_switched_off(self, capture, tmp_path):
        db = _plain_db(tmp_path / "state.db")
        conn = capture.open_readonly(db)
        conn.execute("PRAGMA query_only = OFF")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO t (v) VALUES ('two')")
        conn.close()

    def test_reads_return_rows_by_column_name(self, capture, tmp_path):
        db = _plain_db(tmp_path / "state.db")
        conn = capture.open_readonly(db)
        assert capture.row_to_dict(conn.execute("SELECT id, v FROM t").fetchone()) == {
            "id": 1,
            "v": "one",
        }
        conn.close()

    def test_a_missing_path_is_refused_and_not_created(self, capture, tmp_path):
        missing = tmp_path / "absent.db"
        with pytest.raises(capture.CaptureError):
            capture.open_readonly(missing)
        assert not missing.exists()

    def test_a_file_that_is_not_a_database_is_refused(self, capture, tmp_path):
        junk = tmp_path / "junk.db"
        junk.write_text("this is not a sqlite file, " * 20)
        with pytest.raises(capture.CaptureError) as excinfo:
            capture.open_readonly(junk)
        assert "DatabaseError" in str(excinfo.value)

    def test_path_with_a_space_and_non_ascii_character_opens(self, capture, tmp_path):
        folder = tmp_path / "copy of état"
        folder.mkdir()
        db = _plain_db(folder / "state.db")
        conn = capture.open_readonly(db)
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 1
        conn.close()

    def test_read_snapshot_holds_one_state_while_a_writer_commits(self, capture, tmp_path):
        db = tmp_path / "wal.db"
        writer = sqlite3.connect(db)
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        writer.execute("INSERT INTO t (v) VALUES ('one')")
        writer.commit()

        reader = capture.open_readonly(db)
        with capture.read_snapshot(reader):
            first = reader.execute("SELECT count(*) FROM t").fetchone()[0]
            writer.execute("INSERT INTO t (v) VALUES ('two')")
            writer.commit()
            second = reader.execute("SELECT count(*) FROM t").fetchone()[0]
        after = reader.execute("SELECT count(*) FROM t").fetchone()[0]
        reader.close()
        writer.close()
        assert (first, second, after) == (1, 1, 2)


# ---------------------------------------------------------------------------
# Record writers
# ---------------------------------------------------------------------------


class TestRecordWriters:
    def test_existing_record_is_not_replaced_without_force(self, capture, tmp_path):
        target = tmp_path / "out" / "rec.json"
        capture.write_json(target, {"a": 1})
        with pytest.raises(capture.CaptureError):
            capture.write_json(target, {"a": 2})
        assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
        capture.write_json(target, {"a": 2}, overwrite=True)
        assert json.loads(target.read_text(encoding="utf-8")) == {"a": 2}

    def test_gzip_round_trips_the_exact_bytes_and_is_reproducible(self, capture, tmp_path):
        payload = b'{"k": "caf\xc3\xa9"}\n  trailing space \n'
        first = capture.write_gzip(tmp_path / "a.json.gz", payload)
        second = capture.write_gzip(tmp_path / "b.json.gz", payload)
        assert gzip.decompress(first.read_bytes()) == payload
        assert first.read_bytes() == second.read_bytes()

    @pytest.mark.parametrize(
        ("record_name", "sidecar_name"),
        [
            ("x.json.gz", "x.capture.json"),
            ("x.json", "x.capture.json"),
            ("y.body", "y.capture.json"),
        ],
    )
    def test_sidecar_name_drops_the_record_suffix(self, capture, record_name, sidecar_name):
        assert capture.sidecar_path(Path("dir") / record_name) == Path("dir") / sidecar_name

    def test_sidecar_names_source_table_row_key_moment_and_the_records_hash(
        self, capture, tmp_path
    ):
        record = capture.write_json(tmp_path / "rec.json", {"a": 1})
        sidecar = capture.write_sidecar(
            record,
            case="90-c1-demo",
            db_path=tmp_path / "deep" / "alphabot_state.db",
            source_table="bot_state",
            row_key="id = 1",
            facts={"n": 3},
        )
        body = json.loads(sidecar.read_text(encoding="utf-8"))
        assert body["source"] == {
            "database": "alphabot_state.db",
            "table": "bot_state",
            "row_key": "id = 1",
        }
        assert body["record"] == {
            "file": "rec.json",
            "bytes": record.stat().st_size,
            "sha256": _sha(record),
        }
        assert body["case"] == "90-c1-demo"
        assert body["facts"] == {"n": 3}
        assert body["captured_at_utc"].endswith("Z") and len(body["captured_at_utc"]) == 20
        assert "deep" not in sidecar.read_text(
            encoding="utf-8"
        )  # the source path is never recorded


# ---------------------------------------------------------------------------
# Case discovery and the command line
# ---------------------------------------------------------------------------

DEMO_CASES = textwrap.dedent(
    """
    def register(h):
        def run(args):
            conn = h.open_readonly(args.db)
            row = conn.execute("SELECT v FROM t WHERE id = 1").fetchone()
            conn.close()
            record = h.write_json(args.out + "/90-c1/demo.json", {"v": row["v"]}, overwrite=args.force)
            print(record)
        return {"90-c1-demo": h.Case(help="demo case", run=run)}
    """
)


def _write_case_module(directory: Path, name: str, body: str) -> None:
    (directory / name).write_text(body, encoding="utf-8")


class TestDiscovery:
    def test_cases_are_found_beside_the_script_and_others_are_ignored(self, capture, tmp_path):
        _write_case_module(tmp_path, "_cases_90.py", DEMO_CASES)
        _write_case_module(tmp_path, "helpers.py", "raise RuntimeError('not a case module')")
        cases, errors = capture.discover_cases(tmp_path)
        assert sorted(cases) == ["90-c1-demo"]
        assert errors == {}

    def test_a_broken_module_is_reported_and_does_not_hide_a_working_one(self, capture, tmp_path):
        _write_case_module(tmp_path, "_cases_90.py", DEMO_CASES)
        _write_case_module(tmp_path, "_cases_91.py", "raise RuntimeError('boom')")
        cases, errors = capture.discover_cases(tmp_path)
        assert "90-c1-demo" in cases
        assert "RuntimeError" in errors["_cases_91.py"]

    def test_a_case_named_for_another_plans_number_is_rejected(self, capture, tmp_path):
        _write_case_module(tmp_path, "_cases_92.py", DEMO_CASES.replace("90-c1-demo", "93-c1-demo"))
        cases, errors = capture.discover_cases(tmp_path)
        assert cases == {}
        assert "_cases_92.py" in errors

    @pytest.mark.parametrize("bad_name", ["demo", "90-demo", "90-c1-Demo", "90-c1-", "get", "list"])
    def test_a_malformed_or_reserved_case_name_is_rejected(self, capture, tmp_path, bad_name):
        _write_case_module(tmp_path, "_cases_90.py", DEMO_CASES.replace("90-c1-demo", bad_name))
        cases, errors = capture.discover_cases(tmp_path)
        assert cases == {}
        assert "_cases_90.py" in errors

    def test_a_module_that_does_not_follow_the_numbering_is_reported(self, capture, tmp_path):
        _write_case_module(tmp_path, "_cases_demo.py", DEMO_CASES)
        cases, errors = capture.discover_cases(tmp_path)
        assert cases == {}
        assert "_cases_demo.py" in errors

    def test_the_shipped_script_discovers_its_own_case_module(self, capture):
        cases, errors = capture.discover_cases()
        assert errors == {}
        assert {"01-c1-bot-state-row", "01-c2-shadow-rows", "01-c2-exit-trigger"} <= set(cases)

    def test_script_runs_a_discovered_case_under_isolated_mode(self, tmp_path):
        script_dir = tmp_path / "tool"
        script_dir.mkdir()
        (script_dir / "capture.py").write_bytes((TOOL_DIR / "capture.py").read_bytes())
        _write_case_module(script_dir, "_cases_90.py", DEMO_CASES)
        db = _plain_db(tmp_path / "state.db")
        out = tmp_path / "out"

        listed = subprocess.run(
            [sys.executable, "-I", str(script_dir / "capture.py"), "list"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert listed.returncode == 0, listed.stderr
        assert "90-c1-demo" in listed.stdout

        ran = subprocess.run(
            [
                sys.executable,
                "-I",
                str(script_dir / "capture.py"),
                "90-c1-demo",
                "--db",
                str(db),
                "--out",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert ran.returncode == 0, ran.stderr
        assert json.loads((out / "90-c1" / "demo.json").read_text(encoding="utf-8")) == {"v": "one"}

    def test_a_missing_database_gives_exit_status_2_and_writes_nothing(
        self, capture, tmp_path, capsys
    ):
        out = tmp_path / "out"
        code = capture.main(
            ["01-c1-bot-state-row", "--db", str(tmp_path / "absent.db"), "--out", str(out)]
        )
        assert code == 2
        assert "error:" in capsys.readouterr().err
        assert not out.exists()

    def test_an_unknown_case_is_a_usage_error(self, capture, tmp_path):
        assert capture.main(["99-c9-nothing", "--db", "x", "--out", "y"]) == 2


# ---------------------------------------------------------------------------
# HTTP GET recorder, against a local stub
# ---------------------------------------------------------------------------

JSON_BODY = '{"state": "café", "n": 1.50}  \n'.encode()  # exact bytes, trailing spaces kept


class _StubHandler(http.server.BaseHTTPRequestHandler):
    seen_headers: list = []

    def date_time_string(self, timestamp=None):
        return FIXED_DATE_HEADER

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).seen_headers.append(dict(self.headers))
        if self.path == "/api/state?x=1":
            self._reply(200, "application/json; charset=utf-8", JSON_BODY)
        elif self.path == "/page":
            self._reply(200, "text/html; charset=utf-8", b"<html>\r\n<body>ok</body></html>")
        elif self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._reply(404, "text/plain", b"nope")

    def _reply(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def stub_server():
    _StubHandler.seen_headers = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)


class TestGetRecorder:
    def test_saves_status_content_type_date_and_the_verbatim_body(
        self, capture, stub_server, tmp_path
    ):
        body_path, meta_path = capture.record_get(stub_server, "/api/state?x=1", tmp_path)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert body_path.read_bytes() == JSON_BODY
        assert body_path.suffix == ".json"
        assert meta["status"] == 200
        assert meta["content_type"] == "application/json; charset=utf-8"
        assert meta["date"] == FIXED_DATE_HEADER
        assert meta["body"] == {
            "file": body_path.name,
            "bytes": len(JSON_BODY),
            "sha256": hashlib.sha256(JSON_BODY).hexdigest(),
        }
        assert meta["request"] == {
            "method": "GET",
            "base_url": stub_server,
            "path": "/api/state?x=1",
        }

    def test_requests_an_uncompressed_body(self, capture, stub_server, tmp_path):
        capture.record_get(stub_server, "/page", tmp_path)
        assert _StubHandler.seen_headers[-1]["Accept-Encoding"] == "identity"

    def test_html_body_keeps_its_line_endings(self, capture, stub_server, tmp_path):
        body_path, _ = capture.record_get(stub_server, "/page", tmp_path)
        assert body_path.suffix == ".html"
        assert body_path.read_bytes() == b"<html>\r\n<body>ok</body></html>"

    def test_an_error_status_is_recorded_not_raised(self, capture, stub_server, tmp_path):
        body_path, meta_path = capture.record_get(stub_server, "/missing", tmp_path)
        assert json.loads(meta_path.read_text(encoding="utf-8"))["status"] == 404
        assert body_path.read_bytes() == b"nope"

    def test_a_redirect_is_recorded_not_followed(self, capture, stub_server, tmp_path):
        body_path, meta_path = capture.record_get(stub_server, "/moved", tmp_path)
        assert json.loads(meta_path.read_text(encoding="utf-8"))["status"] == 302
        assert body_path.read_bytes() == b""
        assert len(_StubHandler.seen_headers) == 1

    def test_served_sha_and_snapshot_time_are_recorded_as_given(
        self, capture, stub_server, tmp_path
    ):
        _, meta_path = capture.record_get(
            stub_server,
            "/page",
            tmp_path,
            served_sha="37c8e983b64c6980c28199a3f9318486b60f3864",
            snapshot_time="2026-10-22T14:00:00Z",
        )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["served_sha"] == "37c8e983b64c6980c28199a3f9318486b60f3864"
        assert meta["launcher_snapshot_time"] == "2026-10-22T14:00:00Z"

    def test_a_second_record_of_the_same_name_is_refused_without_force(
        self, capture, stub_server, tmp_path
    ):
        capture.record_get(stub_server, "/page", tmp_path, name="shot")
        with pytest.raises(capture.CaptureError):
            capture.record_get(stub_server, "/page", tmp_path, name="shot")
        capture.record_get(stub_server, "/page", tmp_path, name="shot", overwrite=True)

    def test_a_non_loopback_host_is_refused_before_any_connection(self, capture, tmp_path):
        with pytest.raises(capture.CaptureError) as excinfo:
            capture.record_get("http://production.example.invalid", "/api/state", tmp_path)
        assert "loopback" in str(excinfo.value)
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize("base_url", ["ftp://127.0.0.1", "127.0.0.1:5000", "http://"])
    def test_a_base_url_that_is_not_http_with_a_host_is_refused(self, capture, tmp_path, base_url):
        with pytest.raises(capture.CaptureError):
            capture.record_get(base_url, "/x", tmp_path)

    def test_a_path_without_a_leading_slash_is_refused(self, capture, stub_server, tmp_path):
        with pytest.raises(capture.CaptureError):
            capture.record_get(stub_server, "api/state", tmp_path)

    def test_a_refused_connection_is_reported_by_exception_type_only(self, capture, tmp_path):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with pytest.raises(capture.CaptureError) as excinfo:
            capture.record_get(f"http://127.0.0.1:{port}", "/x", tmp_path, timeout=5)
        assert "Error" in str(excinfo.value)

    def test_command_line_get_writes_the_records(self, capture, stub_server, tmp_path, capsys):
        code = capture.main(
            [
                "get",
                "--base-url",
                stub_server,
                "--path",
                "/page",
                "--out",
                str(tmp_path),
                "--name",
                "home",
            ]
        )
        assert code == 0
        assert (tmp_path / "home.html").read_bytes() == b"<html>\r\n<body>ok</body></html>"
        assert (
            json.loads((tmp_path / "home.response.json").read_text(encoding="utf-8"))["status"]
            == 200
        )
