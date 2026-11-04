import csv
import os
import sys
from collections import Counter

import psycopg2
from dotenv import load_dotenv

# the project .env, two folders up
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
load_dotenv(dotenv_path=env_path)

MODULES_CSV = "modules.csv"
PROGRAMME_MODULES_CSV = "programme_modules.csv"

DB_CONFIG = {
    "dbname": os.environ.get("DB_NAME"),
    "user": os.environ.get("DB_USER"),
    "password": os.environ.get("DB_PASSWORD"),
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", "5432"),
}

CREATE_MODULES_SQL = """
CREATE TABLE IF NOT EXISTS modules (
    module_id              SERIAL PRIMARY KEY,
    module_code             VARCHAR(20) UNIQUE NOT NULL,
    module_name             VARCHAR(255),
    credit_value             INTEGER,
    nqf_level                 INTEGER,
    year                      INTEGER,
    semester                  INTEGER,
    prerequisite_subjects    TEXT[],
    corequisite_subjects     TEXT[]
);
"""

CREATE_PROGRAMME_MODULES_SQL = """
CREATE TABLE IF NOT EXISTS programme_modules (
    id                       SERIAL PRIMARY KEY,
    programme_code           VARCHAR(20) NOT NULL,
    module_id                INTEGER REFERENCES modules(module_id),
    module_classification    VARCHAR(5),
    elective_group           VARCHAR(5),
    choose_count             INTEGER,
    effective_type           VARCHAR(20)
);
"""


def parse_int(v):
    return int(v) if v not in (None, "") else None


def parse_array(v):
    return v.split(";") if v else []


def validate(modules_rows, pm_rows):
    problems = []

    module_codes = set(r["module_code"] for r in modules_rows)

    dupe_modules = [c for c, n in Counter(r["module_code"] for r in modules_rows).items() if n > 1]
    if dupe_modules:
        problems.append(f"Duplicate module_code in modules.csv: {sorted(dupe_modules)}")

    pm_codes = set(r["module_code"] for r in pm_rows)
    orphans = pm_codes - module_codes
    if orphans:
        problems.append(f"module_code in programme_modules.csv not found in modules.csv: {sorted(orphans)}")

    # Exact duplicate rows (same programme, module, and every other field).
    row_counts = Counter(tuple(r.items()) for r in pm_rows)
    exact_dupes = [dict(row) for row, n in row_counts.items() if n > 1]
    if exact_dupes:
        problems.append(f"Exact duplicate rows in programme_modules.csv: {exact_dupes}")

    return problems


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
                (module_code, module_name, credit_value, nqf_level,
                 year, semester, prerequisite_subjects, corequisite_subjects)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (module_code) DO UPDATE SET
                module_name = EXCLUDED.module_name,
                credit_value = EXCLUDED.credit_value,
                nqf_level = EXCLUDED.nqf_level,
                year = EXCLUDED.year,
                semester = EXCLUDED.semester,
                prerequisite_subjects = EXCLUDED.prerequisite_subjects,
                corequisite_subjects = EXCLUDED.corequisite_subjects
            """,
            (
                r["module_code"], r["module_name"],
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
            INSERT INTO programme_modules
                (programme_code, module_id, module_classification,
                 elective_group, choose_count, effective_type)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                r["programme_code"], module_id,
                r["module_classification"] or None,
                r["elective_group"] or None,
                parse_int(r["choose_count"]),
                r["effective_type"] or None,
            ),
        )
        inserted += 1

    conn.commit()
    cur.close()
    print(f"Loaded {inserted} rows into programme_modules ({skipped} skipped -- module_code not found).")


if __name__ == "__main__":
    with open(MODULES_CSV, newline="", encoding="utf-8") as f:
        modules_rows = list(csv.DictReader(f))
    with open(PROGRAMME_MODULES_CSV, newline="", encoding="utf-8") as f:
        pm_rows = list(csv.DictReader(f))

    problems = validate(modules_rows, pm_rows)
    if problems:
        print("Validation found issues -- fix these in the CSV before loading:\n")
        for p in problems:
            print(f"  - {p}")
        print("\nNothing was loaded into the database.")
        sys.exit(1)

    print("Validation passed. Connecting to database...\n")
    conn = psycopg2.connect(**DB_CONFIG)
    print("Connected.\n")

    load_modules(conn, MODULES_CSV)
    load_programme_modules(conn, PROGRAMME_MODULES_CSV)

    conn.close()
    print("\nDone. Try a test query, e.g.:")
    print("  SELECT m.module_name, pm.effective_type FROM programme_modules pm")
    print("  JOIN modules m ON pm.module_id = m.module_id")
    print("  WHERE pm.programme_code = '4BSC21' AND pm.effective_type = 'Elective';")
