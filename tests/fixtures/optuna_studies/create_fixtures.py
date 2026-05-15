"""
One-time script to create the SQLite fixture DBs for O3 migration tests.
Run from the repo root: python tests/fixtures/optuna_studies/create_fixtures.py

The fixtures are committed to the repo so pytest never calls live APIs or
the Optuna storage layer.  Provenance is documented in provenance.json.
"""
import sqlite3
import os

FIXTURES_DIR = os.path.dirname(os.path.abspath(__file__))

OPTUNA_STUDIES_DDL = """
CREATE TABLE IF NOT EXISTS studies (
    study_id   INTEGER NOT NULL,
    study_name VARCHAR(512) NOT NULL,
    PRIMARY KEY (study_id)
);
CREATE TABLE IF NOT EXISTS version_info (
    version_info_id INTEGER NOT NULL,
    schema_version  INTEGER,
    library_version VARCHAR(256),
    PRIMARY KEY (version_info_id)
);
CREATE TABLE IF NOT EXISTS study_directions (
    study_direction_id INTEGER NOT NULL,
    direction          VARCHAR(8) NOT NULL,
    study_id           INTEGER,
    PRIMARY KEY (study_direction_id),
    FOREIGN KEY(study_id) REFERENCES studies (study_id)
);
"""


def make_before_db(path):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executescript(OPTUNA_STUDIES_DDL)
    # Insert three pre-existing studies as if they were created by the old
    # study_name=normalized_name scheme (no timestamp prefix).
    cur.execute("INSERT INTO studies (study_id, study_name) VALUES (1, 'my_symphony')")
    cur.execute("INSERT INTO studies (study_id, study_name) VALUES (2, 'blue_chip_growth')")
    cur.execute("INSERT INTO studies (study_id, study_name) VALUES (3, 'alpha_beta')")
    cur.execute("INSERT INTO version_info (version_info_id, schema_version, library_version) VALUES (1, 12, '3.6.0')")
    conn.commit()
    conn.close()


def make_after_db(path):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executescript(OPTUNA_STUDIES_DDL)
    # Same three studies, now with LEGACY__ prefix applied.
    cur.execute("INSERT INTO studies (study_id, study_name) VALUES (1, 'LEGACY__my_symphony')")
    cur.execute("INSERT INTO studies (study_id, study_name) VALUES (2, 'LEGACY__blue_chip_growth')")
    cur.execute("INSERT INTO studies (study_id, study_name) VALUES (3, 'LEGACY__alpha_beta')")
    cur.execute("INSERT INTO version_info (version_info_id, schema_version, library_version) VALUES (1, 12, '3.6.0')")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    before_path = os.path.join(FIXTURES_DIR, "legacy_studies_before_archive.sqlite")
    after_path = os.path.join(FIXTURES_DIR, "legacy_studies_after_archive.sqlite")

    for p in (before_path, after_path):
        if os.path.exists(p):
            os.remove(p)

    make_before_db(before_path)
    make_after_db(after_path)
    print(f"Created: {before_path}")
    print(f"Created: {after_path}")
