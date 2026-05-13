# Migrations

One-time data or schema migrations for AlphaBot's SQLite DBs.

## Convention

- Files numbered sequentially: `001_*`, `002_*`, ...
- Each migration is idempotent (safe to re-run)
- Each migration documents its trigger (which commit/PR motivated it) and risk
- Apply via: `sqlite3 alphabot_state.db < migrations/NNN_name.sql`
- Apply BEFORE restarting the AlphaBot daemon after a deploy
- Track applied migrations operationally (this directory is the record; no in-DB version table yet)

## Index

- `001_normalize_symphony_names.sql` — A6.5 normalize wiring fix. Required before first deploy of commit that wires `normalize_name()` into `get_symphony_strategy` / `save_symphony_strategy`.
