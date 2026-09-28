"""
Load the corrected 2023 FSAE handbook CSVs into Campus_AI_Assistant_database
as handbook edition 2, next to the existing 2026 data (edition 1).

Setup (once):   pip install psycopg2-binary
Run:            python load_fsae_2023.py
                python load_fsae_2023.py --env "C:\\path\\to\\.env" --csv-dir "C:\\path\\to\\csvs"

Database settings are read from your .env file (see ENV_NAMES below for the
variable names it understands, including DATABASE_URL).

The 5 CSVs must be in the same folder as this script (or in --csv-dir):
  handbook_editions.csv, modules.csv, programme_info.csv,
  programme_admission_requirements.csv, programme_modules.csv

What it does, all in ONE transaction (any error = nothing is changed):
  1. One-time schema change: adds handbook_edition_id to programme_info and
     programme_admission_requirements (existing rows -> edition 1) and makes
     programme_info's primary key (programme_code, handbook_edition_id).
  2. Inserts/updates the 2023 handbook edition row.
  3. Deletes any earlier edition-2 rows (so it is safe to re-run).
  4. Loads modules, programme_info, admission requirements, and
     programme_modules (module_code is converted to the 2023 module_id).
  5. Prints row counts per edition.
"""

import argparse
import csv
import getpass
import os
import sys

import psycopg2
from psycopg2.extras import execute_values

# ------------------------------------------------------------------ settings
# Connection details come from your .env file. Any of these names work:
#   DATABASE_URL=postgresql://user:password@host:5432/dbname
# or separate values:
#   DB_HOST / POSTGRES_HOST / PGHOST             (default localhost)
#   DB_PORT / POSTGRES_PORT / PGPORT             (default 5432)
#   DB_NAME / POSTGRES_DB / DB_DATABASE / PGDATABASE
#   DB_USER / POSTGRES_USER / DB_USERNAME / PGUSER
#   DB_PASSWORD / POSTGRES_PASSWORD / DB_PASS / PGPASSWORD
ENV_NAMES = {
    "host": ["DB_HOST", "POSTGRES_HOST", "PGHOST"],
    "port": ["DB_PORT", "POSTGRES_PORT", "PGPORT"],
    "dbname": ["DB_NAME", "POSTGRES_DB", "DB_DATABASE", "DATABASE_NAME", "PGDATABASE"],
    "user": ["DB_USER", "POSTGRES_USER", "DB_USERNAME", "PGUSER"],
    "password": ["DB_PASSWORD", "POSTGRES_PASSWORD", "DB_PASS", "PGPASSWORD"],
}
DEFAULTS = {"host": "localhost", "port": "5432"}
EDITION_ID = 2
SCRIPT_VERSION = "3 (safe to re-run schema step)"
OLD_EDITION_ID = 1  # edition that existing rows belong to (2026)
EXPECTED = {"modules": 120, "programme_info": 37,
            "programme_admission_requirements": 134, "programme_modules": 1044}


# ------------------------------------------------------------------ .env
def load_env_file(path):
    """Read KEY=VALUE lines from a .env file into os.environ (no extra package needed).
    Values already set in the real environment are not overwritten."""
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            elif " #" in value:
                value = value.split(" #", 1)[0].strip()
            os.environ.setdefault(key, value)


def find_env_file(explicit, csv_dir):
    candidates = [explicit] if explicit else [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.getcwd(), ".env"),
        os.path.join(csv_dir, ".env"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def db_settings():
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if url:
        return {"dsn": url.replace("postgresql+psycopg2://", "postgresql://")}, url
    cfg = {}
    for key, names in ENV_NAMES.items():
        cfg[key] = next((os.environ[n] for n in names if os.environ.get(n)), DEFAULTS.get(key))
    missing = [k for k in ("dbname", "user") if not cfg[k]]
    if missing:
        sys.exit("Your .env is missing: " + ", ".join(
            f"{k} (e.g. {ENV_NAMES[k][0]})" for k in missing))
    if not cfg["password"]:
        cfg["password"] = getpass.getpass(f"Password for {cfg['user']}@{cfg['dbname']}: ")
    shown = f"{cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['dbname']}"
    return cfg, shown


# ------------------------------------------------------------------ helpers
def read_csv(folder, name):
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        sys.exit(f"Missing file: {path}")
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def val(x):
    """Empty string -> None, otherwise the stripped string."""
    x = (x or "").strip()
    return x or None


def num(x):
    x = val(x)
    return int(float(x)) if x is not None else None


def boolean(x):
    x = val(x)
    return None if x is None else x.lower() in ("true", "t", "1", "yes")


def array(x):
    """'{A,B}' -> ['A', 'B'];  '{}' -> []."""
    x = val(x)
    if x is None:
        return []
    return [p.strip() for p in x.strip("{}").split(",") if p.strip()]


# ------------------------------------------------------------------ steps
def migrate_schema(cur):
    """Safe to run any number of times, from any starting state."""
    # 2026 edition id is looked up by year, not hard-coded
    cur.execute("SELECT id FROM handbook_editions WHERE year = 2026")
    row = cur.fetchone()
    old_id = row[0] if row else OLD_EDITION_ID

    for table in ("programme_info", "programme_admission_requirements"):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS handbook_edition_id integer")
        cur.execute(f"UPDATE {table} SET handbook_edition_id = %s WHERE handbook_edition_id IS NULL",
                    (old_id,))
        if cur.rowcount:
            print(f"  {table}: {cur.rowcount} existing rows set to the 2026 edition (id {old_id})")
        cur.execute(f"ALTER TABLE {table} ALTER COLUMN handbook_edition_id SET NOT NULL")

    # Rebuild the keys/constraints. Each one is dropped first if it exists,
    # so it does not matter what was run before (earlier script, manual query, ...).
    steps = [
        # admission -> programme links (old single-column and new two-column versions)
        "ALTER TABLE programme_admission_requirements DROP CONSTRAINT IF EXISTS "
        "programme_admission_requirements_programme_code_fkey",
        "ALTER TABLE programme_admission_requirements DROP CONSTRAINT IF EXISTS "
        "programme_admission_requirements_programme_fkey",
        # programme_info primary key: programme_code + edition
        "ALTER TABLE programme_info DROP CONSTRAINT IF EXISTS programme_info_pkey",
        "ALTER TABLE programme_info ADD CONSTRAINT programme_info_pkey "
        "PRIMARY KEY (programme_code, handbook_edition_id)",
        # edition foreign keys
        "ALTER TABLE programme_info DROP CONSTRAINT IF EXISTS programme_info_handbook_edition_id_fkey",
        "ALTER TABLE programme_info ADD CONSTRAINT programme_info_handbook_edition_id_fkey "
        "FOREIGN KEY (handbook_edition_id) REFERENCES handbook_editions(id)",
        "ALTER TABLE programme_admission_requirements DROP CONSTRAINT IF EXISTS "
        "programme_admission_requirements_handbook_edition_id_fkey",
        "ALTER TABLE programme_admission_requirements ADD CONSTRAINT "
        "programme_admission_requirements_handbook_edition_id_fkey "
        "FOREIGN KEY (handbook_edition_id) REFERENCES handbook_editions(id)",
        # admission rows must belong to a programme of the same edition
        "ALTER TABLE programme_admission_requirements ADD CONSTRAINT "
        "programme_admission_requirements_programme_fkey "
        "FOREIGN KEY (programme_code, handbook_edition_id) "
        "REFERENCES programme_info(programme_code, handbook_edition_id)",
    ]
    for sql in steps:
        cur.execute(sql)
    print("  keys and constraints OK (programme_info is keyed on programme_code + edition)")


def load_edition(cur, rows):
    for r in rows:
        cur.execute("""INSERT INTO handbook_editions (id, year, label) VALUES (%s, %s, %s)
                       ON CONFLICT (id) DO UPDATE SET year = EXCLUDED.year, label = EXCLUDED.label""",
                    (num(r["id"]), num(r["year"]), val(r["label"])))
    cur.execute("SELECT setval('handbook_editions_id_seq', (SELECT max(id) FROM handbook_editions))")


def delete_old(cur):
    for table in ("programme_modules", "programme_admission_requirements",
                  "programme_info", "modules"):
        cur.execute(f"DELETE FROM {table} WHERE handbook_edition_id = %s", (EDITION_ID,))
        print(f"  {table}: {cur.rowcount} old rows removed")


def load_modules(cur, rows):
    data = [(val(r["module_code"]), val(r["module_name"]), val(r["classification"]),
             num(r["credit_value"]), num(r["nqf_level"]), num(r["year"]), num(r["semester"]),
             array(r["prerequisite_subjects"]), array(r["corequisite_subjects"]),
             boolean(r["needs_manual_review"]), boolean(r["counts_toward_progression_credits"]),
             num(r["handbook_edition_id"])) for r in rows]
    execute_values(cur, """INSERT INTO modules (module_code, module_name, classification,
        credit_value, nqf_level, year, semester, prerequisite_subjects, corequisite_subjects,
        needs_manual_review, counts_toward_progression_credits, handbook_edition_id) VALUES %s""",
        data, template="(%s,%s,%s,%s,%s,%s,%s,%s::text[],%s::text[],%s,%s,%s)")
    return len(data)


def load_programme_info(cur, rows):
    cols = ["programme_code", "programme_name", "programme_type", "faculty", "departments",
            "degree_designator", "qualifier", "abbreviation", "qualification_code_saqf",
            "exit_nqf_level", "minimum_credits_for_admission", "minimum_duration_years",
            "presentation_mode", "intake", "registration_cycle", "readmission_policy",
            "total_credits_to_graduate", "handbook_edition_id"]
    ints = {"exit_nqf_level", "minimum_duration_years", "total_credits_to_graduate",
            "handbook_edition_id"}
    data = [tuple(num(r[c]) if c in ints else val(r[c]) for c in cols) for r in rows]
    execute_values(cur, f"INSERT INTO programme_info ({', '.join(cols)}) VALUES %s", data)
    return len(data)


def load_admission(cur, rows):
    data = [(val(r["programme_code"]), val(r["subject_name"]), num(r["min_percentage"]),
             num(r["min_level"]), num(r["handbook_edition_id"])) for r in rows]
    execute_values(cur, """INSERT INTO programme_admission_requirements
        (programme_code, subject_name, min_percentage, min_level, handbook_edition_id)
        VALUES %s""", data)
    return len(data)


def load_programme_modules(cur, rows):
    cur.execute("SELECT module_code, module_id FROM modules WHERE handbook_edition_id = %s",
                (EDITION_ID,))
    ids = dict(cur.fetchall())
    missing = sorted({r["module_code"] for r in rows if r["module_code"] not in ids})
    if missing:
        raise RuntimeError(f"programme_modules refers to codes not in modules: {missing}")
    data = [(val(r["programme_code"]), ids[r["module_code"]], val(r["module_classification"]),
             val(r["elective_group"]), num(r["choose_count"]), val(r["effective_type"]),
             num(r["expected_year"]), num(r["expected_semester"]),
             num(r["handbook_edition_id"])) for r in rows]
    execute_values(cur, """INSERT INTO programme_modules (programme_code, module_id,
        module_classification, elective_group, choose_count, effective_type,
        expected_year, expected_semester, handbook_edition_id) VALUES %s""", data)
    return len(data)


def report(cur):
    cur.execute("""
        SELECT e.id, e.year,
          (SELECT count(*) FROM modules m WHERE m.handbook_edition_id = e.id),
          (SELECT count(*) FROM programme_info p WHERE p.handbook_edition_id = e.id),
          (SELECT count(*) FROM programme_admission_requirements a WHERE a.handbook_edition_id = e.id),
          (SELECT count(*) FROM programme_modules pm WHERE pm.handbook_edition_id = e.id)
        FROM handbook_editions e ORDER BY e.id""")
    print("\n  edition  year  modules  programmes  admission  programme_modules")
    for row in cur.fetchall():
        print("  {:>7}  {:>4}  {:>7}  {:>10}  {:>9}  {:>17}".format(*row))


# ------------------------------------------------------------------ main
def main():
    print(f"load_fsae_2023.py version {SCRIPT_VERSION}")
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--env", help="path to your .env file (default: search next to this "
                                  "script, the current folder, the CSV folder, and one level up)")
    args = ap.parse_args()

    env_path = find_env_file(args.env, args.csv_dir)
    if args.env and not env_path:
        sys.exit(f".env file not found: {args.env}")
    if env_path:
        load_env_file(env_path)
        print(f"Using settings from {env_path}")
    else:
        print("No .env file found; using environment variables only")
    cfg, shown = db_settings()
    print(f"Connecting to {shown.split('@')[-1] if 'dsn' in cfg else shown}")

    print(f"Reading CSVs from {args.csv_dir}")
    data = {name: read_csv(args.csv_dir, name + ".csv") for name in (
        "handbook_editions", "modules", "programme_info",
        "programme_admission_requirements", "programme_modules")}

    try:
        conn = psycopg2.connect(**cfg)
    except psycopg2.OperationalError as e:
        sys.exit(f"Could not connect to the database. Check the values in your .env.\n{e}")
    try:
        with conn:  # commits on success, rolls back on any error
            with conn.cursor() as cur:
                print("1. Schema check");       migrate_schema(cur)
                print("2. Handbook edition");   load_edition(cur, data["handbook_editions"])
                print("3. Removing old 2023 rows"); delete_old(cur)
                print("4. Loading data")
                counts = {
                    "modules": load_modules(cur, data["modules"]),
                    "programme_info": load_programme_info(cur, data["programme_info"]),
                    "programme_admission_requirements": load_admission(
                        cur, data["programme_admission_requirements"]),
                    "programme_modules": load_programme_modules(cur, data["programme_modules"]),
                }
                for k, n in counts.items():
                    flag = "" if n == EXPECTED[k] else f"   (expected {EXPECTED[k]})"
                    print(f"  {k}: {n} rows inserted{flag}")
                report(cur)
        print("\nDone. All changes committed.")
    except Exception as e:
        print(f"\nERROR: {e}\nNothing was changed (transaction rolled back).")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()