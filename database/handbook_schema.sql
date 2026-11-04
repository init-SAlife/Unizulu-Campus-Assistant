-- Handbook tables (programmes and modules per handbook edition), the same as the live database.
-- Safe to run more than once. Then load the rows with handbook_data.sql.
--   psql -d your_database -f database/handbook_schema.sql

-- one row per handbook: 1 = 2026 handbook, 2 = 2023 prospectus
CREATE TABLE IF NOT EXISTS handbook_editions (
    id     serial PRIMARY KEY,
    year   integer NOT NULL UNIQUE,
    label  text NOT NULL
);

-- one row per module per edition
CREATE TABLE IF NOT EXISTS modules (
    module_id                          serial PRIMARY KEY,
    module_code                        varchar(20) NOT NULL,
    module_name                        varchar(255),
    classification                     varchar(5),
    credit_value                       integer,
    nqf_level                          integer,
    year                               integer,
    semester                           integer,
    prerequisite_subjects              text[],
    corequisite_subjects               text[],
    needs_manual_review                boolean DEFAULT false,
    counts_toward_progression_credits  boolean DEFAULT true,
    handbook_edition_id                integer NOT NULL REFERENCES handbook_editions(id),
    CONSTRAINT modules_code_edition_unique UNIQUE (module_code, handbook_edition_id)
);

-- one row per programme per edition
CREATE TABLE IF NOT EXISTS programme_info (
    programme_code                 varchar(20) NOT NULL,
    programme_name                 text NOT NULL,
    programme_type                 varchar(20) NOT NULL DEFAULT 'Mainstream',
    faculty                        text,
    departments                    text,
    degree_designator              text,
    qualifier                      text,
    abbreviation                   varchar(20),
    qualification_code_saqf        varchar(20),
    exit_nqf_level                 integer,
    minimum_credits_for_admission  text,
    minimum_duration_years         integer,
    presentation_mode              text,
    intake                         text,
    registration_cycle             text,
    readmission_policy             text,
    total_credits_to_graduate      integer,
    handbook_edition_id            integer NOT NULL REFERENCES handbook_editions(id),
    PRIMARY KEY (programme_code, handbook_edition_id)
);

-- school subjects needed to get into a programme
CREATE TABLE IF NOT EXISTS programme_admission_requirements (
    id                   serial PRIMARY KEY,
    programme_code       varchar(20),
    subject_name         text NOT NULL,
    min_percentage       integer,
    min_level            integer,
    handbook_edition_id  integer NOT NULL REFERENCES handbook_editions(id),
    CONSTRAINT programme_admission_requirements_programme_fkey
        FOREIGN KEY (programme_code, handbook_edition_id) REFERENCES programme_info(programme_code, handbook_edition_id)
);

-- which modules each programme has, and how (Compulsory / Major / Elective, elective groups)
CREATE TABLE IF NOT EXISTS programme_modules (
    id                     serial PRIMARY KEY,
    programme_code         varchar(20) NOT NULL,
    module_id              integer REFERENCES modules(module_id),
    module_classification  varchar(5),
    elective_group         varchar(5),
    choose_count           integer,
    effective_type         varchar(20) NOT NULL
                           CONSTRAINT programme_modules_effective_type_check
                           CHECK (effective_type IN ('Compulsory', 'Major', 'Elective')),
    expected_year          integer,
    expected_semester      integer,
    handbook_edition_id    integer NOT NULL REFERENCES handbook_editions(id)
);
