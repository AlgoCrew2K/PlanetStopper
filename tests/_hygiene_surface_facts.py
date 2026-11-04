"""The facts of the Flask app and the state database that a clean-up must not change.

TEST-ONLY. One extractor, used by the producer that records the facts at the base commit
(``tests/fixtures/hygiene_waves_3_4/app_database/produce_ref_records.py``) and by the test that
compares the head with that record (``tests/app/test_hygiene_surface_unchanged.py``). It reads
structure only: the URL map, the names of the hooks, the two endpoint/key sets, the migration
files, and the SQL text of a freshly migrated database. It reads no row and no environment value.
"""

from __future__ import annotations

import hashlib
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO / "migrations"
_IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})


def url_map_facts(app_module) -> dict:
    flask_app = app_module.app
    rules = sorted(
        [
            rule.rule,
            sorted(rule.methods - _IGNORED_METHODS),
            rule.endpoint,
            bool(rule.strict_slashes) if rule.strict_slashes is not None else None,
        ]
        for rule in flask_app.url_map.iter_rules()
    )

    def hook_names(registry: dict) -> dict[str, list[str]]:
        return {
            str(scope): [f.__name__ for f in funcs]
            for scope, funcs in sorted(registry.items(), key=lambda kv: str(kv[0]))
        }

    return {
        "rules": rules,
        "auth_exempt_endpoints": sorted(app_module._AUTH_EXEMPT_ENDPOINTS),
        "settings_write_allowlist": sorted(app_module._SETTINGS_WRITE_ALLOWLIST),
        "before_request_hooks": hook_names(flask_app.before_request_funcs),
        "after_request_hooks": hook_names(flask_app.after_request_funcs),
        "teardown_request_hooks": hook_names(flask_app.teardown_request_funcs),
        "error_handlers": sorted(
            f"{scope}:{code}:{handler.__name__}"
            for scope, by_code in flask_app.error_handler_spec.items()
            for code, by_class in by_code.items()
            for handler in by_class.values()
        ),
    }


def _lf_digest(path: pathlib.Path) -> str:
    """sha256 of the file with CRLF read as LF: a Windows checkout and a Linux one agree."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def schema_facts(database_module, ro_connection) -> dict:
    files = {p.name: _lf_digest(p) for p in sorted(MIGRATIONS_DIR.glob("*.sql"))}
    rows = ro_connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    return {
        "migration_file_digests": files,
        "migration_order": list(database_module._MIGRATION_FILES),
        "sqlite_master": [list(r) for r in rows],
    }
