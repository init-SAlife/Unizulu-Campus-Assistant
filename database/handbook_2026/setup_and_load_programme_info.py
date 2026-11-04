import csv
import os
import psycopg2
from dotenv import load_dotenv

# the project .env, two folders up
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
load_dotenv(dotenv_path=env_path)

DB_CONFIG = {
    "dbname": os.environ.get("DB_NAME"),
    "user": os.environ.get("DB_USER"),
    "password": os.environ.get("DB_PASSWORD"),
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", "5432"),
}

CREATE_PROGRAMME_INFO = """
CREATE TABLE IF NOT EXISTS programme_info (
    programme_code VARCHAR(20) PRIMARY KEY,
    programme_name TEXT NOT NULL,
    programme_type VARCHAR(20) NOT NULL DEFAULT 'Mainstream',
    faculty TEXT,
    departments TEXT,
    degree_designator TEXT,
    qualifier TEXT,
    abbreviation VARCHAR(20),
    qualification_code_saqf VARCHAR(20),
    exit_nqf_level INTEGER,
    minimum_credits_for_admission TEXT,
    minimum_duration_years INTEGER,
    presentation_mode TEXT,
    intake TEXT,
    registration_cycle TEXT,
    readmission_policy TEXT,
    total_credits_to_graduate INTEGER
);
"""

CREATE_ADMISSION_REQUIREMENTS = """
CREATE TABLE IF NOT EXISTS programme_admission_requirements (
    id SERIAL PRIMARY KEY,
    programme_code VARCHAR(20) REFERENCES programme_info(programme_code),
    subject_name TEXT NOT NULL,
    min_percentage INTEGER,
    min_level INTEGER
);
"""


def create_tables(cur):
    cur.execute(CREATE_PROGRAMME_INFO)
    cur.execute(CREATE_ADMISSION_REQUIREMENTS)


def load_programme_info(cur):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "programme_info.csv")
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cur.execute("""
                INSERT INTO programme_info (
                    programme_code, programme_name, programme_type, faculty, departments,
                    degree_designator, qualifier, abbreviation, qualification_code_saqf,
                    exit_nqf_level, minimum_credits_for_admission, minimum_duration_years,
                    presentation_mode, intake, registration_cycle, readmission_policy,
                    total_credits_to_graduate
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (programme_code) DO UPDATE SET
                    programme_name = EXCLUDED.programme_name,
                    programme_type = EXCLUDED.programme_type,
                    faculty = EXCLUDED.faculty,
                    departments = EXCLUDED.departments,
                    degree_designator = EXCLUDED.degree_designator,
                    qualifier = EXCLUDED.qualifier,
                    abbreviation = EXCLUDED.abbreviation,
                    qualification_code_saqf = EXCLUDED.qualification_code_saqf,
                    exit_nqf_level = EXCLUDED.exit_nqf_level,
                    minimum_credits_for_admission = EXCLUDED.minimum_credits_for_admission,
                    minimum_duration_years = EXCLUDED.minimum_duration_years,
                    presentation_mode = EXCLUDED.presentation_mode,
                    intake = EXCLUDED.intake,
                    registration_cycle = EXCLUDED.registration_cycle,
                    readmission_policy = EXCLUDED.readmission_policy,
                    total_credits_to_graduate = EXCLUDED.total_credits_to_graduate
            """, (
                row["programme_code"] or None,
                row["programme_name"] or None,
                row["programme_type"] or "Mainstream",
                row["faculty"] or None,
                row["departments"] or None,
                row["degree_designator"] or None,
                row["qualifier"] or None,
                row["abbreviation"] or None,
                row["qualification_code_saqf"] or None,
                int(row["exit_nqf_level"]) if row["exit_nqf_level"] else None,
                row["minimum_credits_for_admission"] or None,
                int(row["minimum_duration_years"]) if row["minimum_duration_years"] else None,
                row["presentation_mode"] or None,
                row["intake"] or None,
                row["registration_cycle"] or None,
                row["readmission_policy"] or None,
                int(row["total_credits_to_graduate"]) if row["total_credits_to_graduate"] else None,
            ))


def load_admission_requirements(cur):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "programme_admission_requirements.csv")

    # re-runnable: clear out old rows for the programmes in this CSV first,
    # so re-running the script doesn't duplicate subject rows
    with open(path, newline="", encoding="utf-8") as f:
        codes_in_csv = {row["programme_code"] for row in csv.DictReader(f)}
    for code in codes_in_csv:
        cur.execute("DELETE FROM programme_admission_requirements WHERE programme_code = %s", (code,))

    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cur.execute("""
                INSERT INTO programme_admission_requirements
                    (programme_code, subject_name, min_percentage, min_level)
                VALUES (%s,%s,%s,%s)
            """, (
                row["programme_code"],
                row["subject_name"],
                int(row["min_percentage"]) if row["min_percentage"] else None,
                int(row["min_level"]) if row["min_level"] else None,
            ))


if __name__ == "__main__":
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    create_tables(cur)
    load_programme_info(cur)
    load_admission_requirements(cur)

    conn.commit()
    cur.close()
    conn.close()
    print("Tables ready. Loaded programme_info and programme_admission_requirements.")
