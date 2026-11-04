# Provenance: app and database structure at the base commit

Class: captured producer output (structure only). Both files are written by the committed
producer `produce_ref_records.py`, run once at RED on a tree whose `app.py`, `database.py`,
`analytics.py`, `migrations/` and `sleeves/` equal commit 7adc2c56953a3873c013425ebb06033375b62c53
(the producer refuses otherwise). Both are read by `tests/app/test_hygiene_surface_unchanged.py`
through the extractor `tests/_hygiene_surface_facts.py`.

| File | Case it covers | sha256 |
|---|---|---|
| `url_map.json` | every Flask rule with its methods, endpoint and slash rule; `_AUTH_EXEMPT_ENDPOINTS`; `_SETTINGS_WRITE_ALLOWLIST` (key names only); the before-request, after-request and teardown hook names in registration order; the error handlers | 0d5f286149db6795f282f92f93fe39cca10b3e8c6063e868b3f52827beb1f51c |
| `schema.json` | the sha256 of each `migrations/*.sql` file (CRLF read as LF), the order of `database._MIGRATION_FILES`, and the `(type, name, tbl_name, sql)` of every `sqlite_master` entry of a database made by `init_db()` and `run_migrations()` | 7f9f3f509aac368254e48022740a193f032f18bd187137869d8b9a0566b61512 |

Not recorded: any row, any environment value, any credential or account id. The scheduler slots
are not recorded here; `tests/app/test_scheduler_survival.py` pins them.

Regenerating: the producer refuses on any tree that differs from the base in the files it reads.
Run it in a detached worktree at 7adc2c56953a3873c013425ebb06033375b62c53 with the extractor
`tests/_hygiene_surface_facts.py` copied in. A record is never regenerated from a later tree: the
point of the record is that it is the base.
