"""Shared capture script for the refresh-timing fixtures.

Stdlib only; run as `python -I capture.py ...` so nothing beside it is imported.

PROVENANCE (captured-from-producer):
    A capture reads a real producer's stored output and writes it verbatim into
    a record file, with a sidecar naming the source table, the row key and the
    capture moment. Nothing here synthesizes a value.

Safety:
    A database is opened only as ``sqlite3.connect("file:<path>?mode=ro", uri=True)``
    (plus ``PRAGMA query_only``), so a capture can never write its source.

Usage:
    python -I capture.py list
    python -I capture.py <case> --db <alphabot_state.db> --out <dir> [--force] [case options]
    python -I capture.py get --base-url http://127.0.0.1:<port> --path /api/state --out <dir>

Cases come from ``_cases_<NN>.py`` modules beside this file. Each module defines
``register(helpers) -> dict[str, helpers.Case]``; ``helpers`` is this module, so a
case module imports nothing from here (``-I`` keeps this directory off sys.path).
A case name is ``<NN>-c<K>-<case>`` where ``<NN>`` is its module's number, so two
plans' cases can never collide and no plan edits this file.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import importlib.util
import io
import json
import re
import sqlite3
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

SCRIPT_PATH = "tests/fixtures/refresh_timing/capture.py"
CASE_MODULE_GLOB = "_cases_*.py"
CASE_MODULE_RE = re.compile(r"^_cases_(?P<plan>\d{2})\.py$")
CASE_NAME_RE = re.compile(r"^(?P<plan>\d{2})-c(?P<cycle>\d+)-[a-z0-9]+(?:-[a-z0-9]+)*$")
RESERVED_COMMANDS = ("get", "list")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
SQLITE_BUSY_TIMEOUT_S = 10.0
GET_DEFAULT_TIMEOUT_S = 30.0
RECORD_SUFFIXES = (".json.gz", ".json", ".html", ".body")


class CaptureError(Exception):
    """A capture cannot proceed; the message is shown to the operator verbatim."""


@dataclass
class Case:
    """One capture subcommand: `run(args)` writes records under `args.out`."""

    help: str
    run: Callable[[argparse.Namespace], None]
    add_arguments: Callable[[argparse.ArgumentParser], None] | None = None


# ---------------------------------------------------------------------------
# Read-only database access
# ---------------------------------------------------------------------------


def open_readonly(db_path) -> sqlite3.Connection:
    """Open `db_path` read-only; refuse a path that cannot be opened that way."""
    path = Path(db_path)
    if not path.is_file():
        raise CaptureError(f"database file not found: {path}")
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=SQLITE_BUSY_TIMEOUT_S, isolation_level=None)
        conn.execute("PRAGMA query_only = ON")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as exc:
        raise CaptureError(f"cannot open {path} read-only: {type(exc).__name__}") from exc
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def read_snapshot(conn: sqlite3.Connection):
    """Hold one read transaction so every query inside sees the same database state."""
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        conn.execute("ROLLBACK")


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


# ---------------------------------------------------------------------------
# Record writers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_bytes(path, data: bytes, *, overwrite: bool = False) -> Path:
    """Write `data` verbatim; an existing file is never replaced without `overwrite`."""
    target = Path(path)
    if target.exists() and not overwrite:
        raise CaptureError(f"refusing to overwrite {target} (pass --force to replace it)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def json_bytes(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def write_json(path, obj, *, overwrite: bool = False) -> Path:
    return write_bytes(path, json_bytes(obj), overwrite=overwrite)


def gzip_bytes(data: bytes) -> bytes:
    """Gzip with no embedded name or time, so the same input gives the same bytes."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        gz.write(data)
    return buf.getvalue()


def write_gzip(path, data: bytes, *, overwrite: bool = False) -> Path:
    return write_bytes(path, gzip_bytes(data), overwrite=overwrite)


def sidecar_path(record_path) -> Path:
    """`x.json.gz` and `x.json` both give `x.capture.json`."""
    record = Path(record_path)
    name = record.name
    for suffix in RECORD_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return record.with_name(f"{name}.capture.json")


def write_sidecar(
    record_path,
    *,
    case: str,
    db_path,
    source_table: str,
    row_key: str,
    facts: dict | None = None,
    overwrite: bool = False,
) -> Path:
    """Write the record's sidecar: source table, row key, capture moment, record hash."""
    record = Path(record_path)
    data = record.read_bytes()
    body = {
        "case": case,
        "script": SCRIPT_PATH,
        "captured_at_utc": utc_now_iso(),
        "source": {
            "database": Path(db_path).name,
            "table": source_table,
            "row_key": row_key,
        },
        "record": {"file": record.name, "bytes": len(data), "sha256": sha256_hex(data)},
        "facts": facts or {},
    }
    return write_json(sidecar_path(record), body, overwrite=overwrite)


# ---------------------------------------------------------------------------
# HTTP GET recorder (launcher captures)
# ---------------------------------------------------------------------------


def _name_from_path(path: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
    return cleaned or "root"


def _body_extension(content_type: str | None) -> str:
    media = (content_type or "").split(";")[0].strip().lower()
    if media == "application/json":
        return ".json"
    if media == "text/html":
        return ".html"
    return ".body"


def record_get(
    base_url: str,
    path: str,
    out_dir,
    *,
    name: str | None = None,
    timeout: float = GET_DEFAULT_TIMEOUT_S,
    allow_remote: bool = False,
    served_sha: str | None = None,
    snapshot_time: str | None = None,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    """GET `base_url + path` once and save status, Content-Type, Date and the verbatim body.

    A redirect is recorded, not followed. The body is requested uncompressed so the
    saved bytes are exactly what the server produced.
    """
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise CaptureError(f"--base-url must be an http(s) URL with a host: {base_url!r}")
    if not path.startswith("/"):
        raise CaptureError(f"--path must start with '/': {path!r}")
    if parts.hostname not in LOOPBACK_HOSTS and not allow_remote:
        raise CaptureError(
            f"{parts.hostname} is not a loopback host; the recorder targets the local "
            "launcher only (pass --allow-remote to override)"
        )

    conn_cls = (
        http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    )
    conn = conn_cls(parts.hostname, parts.port, timeout=timeout)
    try:
        conn.request(
            "GET",
            path,
            headers={"Accept": "*/*", "Accept-Encoding": "identity", "User-Agent": "rt-capture"},
        )
        response = conn.getresponse()
        body = response.read()
        status = response.status
        content_type = response.getheader("Content-Type")
        date_header = response.getheader("Date")
    except (OSError, http.client.HTTPException) as exc:
        raise CaptureError(f"GET {base_url}{path} failed: {type(exc).__name__}") from exc
    finally:
        conn.close()

    stem = name or _name_from_path(path)
    out = Path(out_dir)
    body_path = write_bytes(
        out / f"{stem}{_body_extension(content_type)}", body, overwrite=overwrite
    )
    meta = {
        "request": {"method": "GET", "base_url": base_url, "path": path},
        "status": status,
        "content_type": content_type,
        "date": date_header,
        "body": {"file": body_path.name, "bytes": len(body), "sha256": sha256_hex(body)},
        "captured_at_utc": utc_now_iso(),
        "served_sha": served_sha,
        "launcher_snapshot_time": snapshot_time,
        "script": SCRIPT_PATH,
    }
    meta_path = write_json(out / f"{stem}.response.json", meta, overwrite=overwrite)
    return body_path, meta_path


# ---------------------------------------------------------------------------
# Case discovery and the command line
# ---------------------------------------------------------------------------


def discover_cases(directory=None) -> tuple[dict[str, Case], dict[str, str]]:
    """Load every `_cases_<NN>.py` beside this file; return (cases, load errors by module)."""
    base = Path(directory) if directory is not None else Path(__file__).resolve().parent
    helpers = sys.modules[__name__]
    cases: dict[str, Case] = {}
    errors: dict[str, str] = {}
    for module_path in sorted(base.glob(CASE_MODULE_GLOB)):
        match = CASE_MODULE_RE.match(module_path.name)
        if match is None:
            errors[module_path.name] = "module name must be _cases_<two digits>.py"
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"rt_capture_cases_{match['plan']}", module_path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            registered = module.register(helpers)
            for case_name, case in registered.items():
                name_match = CASE_NAME_RE.match(case_name)
                if name_match is None or name_match["plan"] != match["plan"]:
                    raise CaptureError(
                        f"case name {case_name!r} must be {match['plan']}-c<K>-<case>"
                    )
                if case_name in RESERVED_COMMANDS:
                    raise CaptureError(f"case name {case_name!r} is reserved")
                if case_name in cases:
                    raise CaptureError(f"case name {case_name!r} is defined twice")
            cases.update(registered)
        except Exception as exc:  # a broken module must not hide the other plans' cases
            errors[module_path.name] = f"{type(exc).__name__}: {exc}"
    return cases, errors


def _build_parser(cases: dict[str, Case]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="capture.py", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True, metavar="<case>|get|list")

    sub.add_parser("list", help="list the discovered cases")

    get = sub.add_parser("get", help="record one HTTP GET from the local launcher")
    get.add_argument("--base-url", required=True)
    get.add_argument("--path", required=True)
    get.add_argument("--out", required=True)
    get.add_argument("--name", help="record file stem (default: derived from --path)")
    get.add_argument("--timeout", type=float, default=GET_DEFAULT_TIMEOUT_S)
    get.add_argument("--served-sha", help="SHA the launcher serves, recorded in the response file")
    get.add_argument("--snapshot-time", help="the launcher's data snapshot time, recorded as given")
    get.add_argument("--allow-remote", action="store_true")
    get.add_argument("--force", action="store_true")

    for case_name in sorted(cases):
        case = cases[case_name]
        case_parser = sub.add_parser(case_name, help=case.help)
        case_parser.add_argument(
            "--db", required=True, help="path of alphabot_state.db (opened read-only)"
        )
        case_parser.add_argument("--out", required=True, help="output directory")
        case_parser.add_argument("--force", action="store_true", help="replace existing records")
        if case.add_arguments is not None:
            case.add_arguments(case_parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    cases, load_errors = discover_cases()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in RESERVED_COMMANDS and argv[0] not in cases and load_errors:
        for module_name, message in sorted(load_errors.items()):
            print(f"error: case module {module_name} failed to load: {message}", file=sys.stderr)
    try:
        args = _build_parser(cases).parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    try:
        if args.command == "list":
            for case_name in sorted(cases):
                print(f"{case_name}\t{cases[case_name].help}")
            for module_name, message in sorted(load_errors.items()):
                print(f"(failed to load {module_name}: {message})")
            return 0
        if args.command == "get":
            body_path, meta_path = record_get(
                args.base_url,
                args.path,
                args.out,
                name=args.name,
                timeout=args.timeout,
                allow_remote=args.allow_remote,
                served_sha=args.served_sha,
                snapshot_time=args.snapshot_time,
                overwrite=args.force,
            )
            print(body_path)
            print(meta_path)
            return 0
        cases[args.command].run(args)
        return 0
    except CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
