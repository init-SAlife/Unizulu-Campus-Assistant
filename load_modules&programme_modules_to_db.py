"""
Load modules.csv and programme_modules.csv into Postgres
-------------------------------------------------------------
Creates (if needed) and populates two tables:
    modules             -- what a module IS (shared across programmes)
    programme_modules   -- how a specific programme uses a module
                            (year/semester placement is on `modules`;
                             elective grouping is here)

No embeddings involved -- these are structured, exact-lookup tables,
not RAG/semantic search data.

BEFORE RUNNING:
  pip install psycopg2-binary python-dotenv
  Fill in DB_CONFIG below, or set DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT
  in your .env file (same folder as this script).
"""

import csv
import os
import psycopg2
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=env_path)

MODULES_CSV = "modules.csv"
PROGRAMME_MODULES_CSV = "programme_modules.csv"

DB_CONFIG = {
    "dbname": os.environ.get("DB_NAME", "Campus_AI_Assistant_database"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", "0nce&always@Pirates"),
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", "5432"),
}

CREATE_MODULES_SQL = """
CREATE TABLE IF NOT EXISTS modules (
    module_id              SERIAL PRIMARY KEY,
    module_code            VARCHAR(20) UNIQUE NOT NULL,
    module_name            VARCHAR(255),
    classification          VARCHAR(5),
    credit_value            INTEGER,
    nqf_level                INTEGER,
    year                     INTEGER,
    semester                 INTEGER,
    prerequisite_subjects   TEXT[],
    corequisite_subjects    TEXT[],
);
"""

CREATE_PROGRAMME_MODULES_SQL = """
CREATE TABLE IF NOT EXISTS programme_modules (
    id              SERIAL PRIMARY KEY,
    programme_code  VARCHAR(20) NOT NULL,
    module_id       INTEGER REFERENCES modules(module_id),
    elective_group  VARCHAR(5),
    choose_count    INTEGER
);
"""


def parse_int(v):
    return int(v) if v not in (None, "") else None


def parse_array(v):
    return v.split(";") if v else []


def load_modules(conn, csv_path):
    cur = conn.cursor()
    cur.execute(CREATE_MODULES_SQL)
    conn.commit()

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    inserted = 0
    for r in rows:
        cur.execute(
            """
            INSERT INTO modules
                (module_code, module_name, classification, credit_value, nqf_level,
                 year, semester, prerequisite_subjects, corequisite_subjects,)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (module_code) DO UPDATE SET
                module_name = EXCLUDED.module_name,
                classification = EXCLUDED.classification,
                credit_value = EXCLUDED.credit_value,
                nqf_level = EXCLUDED.nqf_level,
                year = EXCLUDED.year,
                semester = EXCLUDED.semester,
                prerequisite_subjects = EXCLUDED.prerequisite_subjects,
                corequisite_subjects = EXCLUDED.corequisite_subjects,
            """,
            (
                r["module_code"], r["module_name"], r["module_classification"],
                parse_int(r["credit_value"]), parse_int(r["nqf_level"]),
                parse_int(r["year"]), parse_int(r["semester"]),
                parse_array(r["prerequisite_subjects"]), parse_array(r["corequisite_subjects"]),
            ),
        )
        inserted += 1

    conn.commit()
    cur.close()
    print(f"Loaded {inserted} rows into modules.")


def load_programme_modules(conn, csv_path):
    cur = conn.cursor()
    cur.execute(CREATE_PROGRAMME_MODULES_SQL)
    conn.commit()

    # Clear existing links so re-running this script doesn't duplicate rows
    cur.execute("DELETE FROM programme_modules;")
    conn.commit()

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    inserted, skipped = 0, 0
    for r in rows:
        cur.execute("SELECT module_id FROM modules WHERE module_code = %s", (r["module_code"],))
        result = cur.fetchone()
        if not result:
            skipped += 1
            continue
        module_id = result[0]

        cur.execute(
            """
            INSERT INTO programme_modules (programme_code, module_id, elective_group, choose_count)
            VALUES (%s, %s, %s, %s)
            """,
            (
                r["programme_code"], module_id,
                r["elective_group"] or None,
                parse_int(r["choose_count"]),
            ),
        )
        inserted += 1

    conn.commit()
    cur.close()
    print(f"Loaded {inserted} rows into programme_modules ({skipped} skipped -- module_code not found).")


if __name__ == "__main__":
    conn = psycopg2.connect(**DB_CONFIG)
    print("Connected to database.\n")

    load_modules(conn, MODULES_CSV)
    load_programme_modules(conn, PROGRAMME_MODULES_CSV)

    conn.close()
    print("\nDone. Try a test query, e.g.:")
    print("  SELECT * FROM modules WHERE module_code = '4CPS212';")
    print("  SELECT module_code FROM modules WHERE needs_manual_review = true;")
