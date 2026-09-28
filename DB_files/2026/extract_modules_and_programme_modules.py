import fitz
import pdfplumber
import re
import csv

PDF_PATH = "FSAE-2026-Undergraduate-Handbook.pdf"
START_PAGE = 30
END_PAGE = 120

# Programme boundary detection

CODE_PREFIX_PATTERN = re.compile(r'^([0-9][A-Z]{2,6}\d{0,2})\s+(.+)$')
CODE_SUFFIX_PATTERN = re.compile(r'^(.+?)\s*\(([0-9][A-Z]{2,6}\d{0,2})\)\s*$')
ANCHOR_WORDS = ("FACULTY OF", "DEPARTMENT:", "DEPARTMENTS:", "DEPARTMENT")


def get_bold_lines(page):
    d = page.get_text("dict")
    lines_out = []
    for block in d["blocks"]:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            spans = line["spans"]
            if not spans:
                continue
            txt = "".join(s["text"] for s in spans)
            txt_clean = re.sub(r'\s+', ' ', txt).strip()
            if not txt_clean:
                continue
            all_bold = all("Bold" in s["font"] for s in spans if s["text"].strip())
            lines_out.append((txt_clean, all_bold))
    return lines_out


def looks_like_header_text(text):
    if CODE_PREFIX_PATTERN.match(text):
        return True
    if CODE_SUFFIX_PATTERN.match(text):
        return True
    if re.search(r'\b(BACHELOR OF|DIPLOMA|BACHELOR IN)\b', text, re.IGNORECASE):
        return True
    return False


def extract_code_and_name(text):
    m = CODE_PREFIX_PATTERN.match(text)
    if m:
        return m.group(1), m.group(2).strip()
    m = CODE_SUFFIX_PATTERN.match(text)
    if m:
        return m.group(2), m.group(1).strip()
    return None, text.strip()


def find_programme_boundaries(pdf_path, start_page, end_page):
    """Returns list of {code, name, start_page, end_page}."""
    doc = fitz.open(pdf_path)
    all_lines = []
    for pno in range(start_page - 1, end_page):
        page = doc[pno]
        for txt, bold in get_bold_lines(page):
            all_lines.append((txt, bold, pno + 1))
    doc.close()

    programmes = []
    current = None
    i, n = 0, len(all_lines)
    while i < n:
        txt, bold, pno = all_lines[i]
        if bold and looks_like_header_text(txt):
            lookahead = " ".join(l[0] for l in all_lines[i + 1:i + 4])
            if any(a in lookahead.upper() for a in ANCHOR_WORDS) or any(a in txt.upper() for a in ANCHOR_WORDS):
                if current is not None:
                    programmes.append(current)
                code, name = extract_code_and_name(txt)
                current = {"code": code or "UNKNOWN", "name": name,
                           "start_page": pno, "end_page": pno}
                i += 1
                continue
        if current is not None:
            current["end_page"] = pno
        i += 1
    if current is not None:
        programmes.append(current)

    return programmes


# pdfplumber row reconstruction

SEMESTER_PATTERN = re.compile(r'^(FIRST|SECOND|THIRD|FOURTH)\s+YEAR\s+SEMESTER\s+(\d)', re.IGNORECASE)
YEAR_WORDS = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4}
MODULE_CODE_PATTERN = re.compile(r'^\d[A-Z]{2,6}\d{2,3}$')
CELL_CODE_PATTERN = re.compile(r'^(\d[A-Z]{2,6}\d{2,3})')


def is_empty(v):
    return v is None or v == ""


def is_terminator_row(row):
    return all(is_empty(v) for v in row)


def split_cell(v):
    if is_empty(v):
        return []
    return [p.strip() for p in v.split("\n") if p.strip()]


def get_programme_table_rows(pdf, start_page, end_page):
    """Collect merged table rows across a programme's full page range,
    trimmed to start at the module table (skip admission-requirements
    metadata)."""
    all_rows = []
    started = False
    for pno in range(start_page - 1, end_page):
        if pno >= len(pdf.pages):
            break
        page = pdf.pages[pno]
        tables = page.find_tables()
        if not tables:
            continue
        data = tables[0].extract()
        for row in data:
            if not started:
                if row and row[0] == "SUBJECT NAME":
                    started = True
                continue
            all_rows.append(row)
    return all_rows


def merge_module_rows(rows):
    """Group physical table rows into logical module entries, merging
    wrapped multi-line cells, and tag each with its year/semester."""
    modules = []
    buffer = []
    current_year, current_semester = None, None
    entries = []  # (year, semester, buffer)

    def flush():
        nonlocal buffer
        if buffer:
            entries.append((current_year, current_semester, buffer))
            buffer = []

    def buffer_has_code():
        for r in buffer:
            if len(r) > 1:
                for part in split_cell(r[1]):
                    if CELL_CODE_PATTERN.match(part):
                        return True
        return False

    for row in rows:
        col0_parts = split_cell(row[0]) if row else []
        if col0_parts and SEMESTER_PATTERN.match(col0_parts[0]):
            flush()
            m = SEMESTER_PATTERN.match(col0_parts[0])
            current_year = YEAR_WORDS.get(m.group(1).upper())
            current_semester = int(m.group(2))
            remainder = col0_parts[1:]
            row = (" ".join(remainder),) + tuple(row[1:]) if remainder else tuple([""] * len(row))

        # Some modules fit entirely on one physical row and appear
        # back-to-back with no blank separator row between them -- without
        # this check, two such modules would silently merge into one and
        # the second module's code would be lost.
        row_code_parts = split_cell(row[1]) if len(row) > 1 else []
        row_has_new_code = any(CELL_CODE_PATTERN.match(p) for p in row_code_parts)
        if row_has_new_code and buffer_has_code():
            flush()

        if is_terminator_row(row):
            flush()
            continue
        buffer.append(row)
    flush()

    results = []
    for year, semester, buf in entries:
        # Pad every row to 7 columns in case a malformed table row (from an
        # irregular page layout) has fewer cells than expected.
        buf = [tuple(row) + ("",) * (7 - len(row)) if len(row) < 7 else row[:7] for row in buf]
        cols = list(zip(*buf))

        def collect(i):
            vals = []
            for cell in cols[i]:
                vals.extend(split_cell(cell))
            return vals

        name_parts = collect(0)
        code_parts = collect(1)
        class_letter = collect(2)
        credits = collect(3)
        nqf = collect(4)
        prereq = collect(5)
        coreq = collect(6)

        name = " ".join(name_parts).strip()
        if not name:
            continue

        # The code cell is usually split from its timetable letter by a
        # newline (handled by split_cell above), but on some pages
        # pdfplumber merges them with a plain space instead (e.g.
        # '4AMT111 G'), which would otherwise fail the exact-match code
        # pattern and silently drop the whole module. Extract just the
        # leading code portion regardless of what follows it.
        code = ""
        if code_parts:
            code_match = CELL_CODE_PATTERN.match(code_parts[0].strip())
            code = code_match.group(1) if code_match else code_parts[0]

        results.append({
            "name": name,
            "code": code,
            "classification": class_letter[0] if class_letter else "",
            "credits": credits[0] if credits else "",
            "nqf": nqf[0] if nqf else "",
            "prereq": prereq,   # keep as list for now (needed for LEVEL detection)
            "coreq": coreq,
            "year": year,
            "semester": semester,
        })
    return results


def detect_elective_groups(modules):
    """Tag consecutive EITHER/OR/OR runs (within the same year/semester)
    with a shared elective_group letter.

    Every "EITHER" always starts a BRAND NEW group, even if the previous
    module was also part of an elective group -- two independent EITHER/OR
    choices can appear back-to-back with no standalone module between them
    (e.g. "EITHER A OR B" immediately followed by "EITHER C OR D"), and
    without this check they would incorrectly get merged into one group,
    letting a student satisfy both choices by picking just one module.
    """
    group_letter_ord = ord('A')
    in_group = False
    for i, m in enumerate(modules):
        name_upper = m["name"].upper()
        if name_upper.startswith("EITHER"):
            if in_group:
                group_letter_ord += 1
            m["elective_group"] = chr(group_letter_ord)
            in_group = True
        elif name_upper.startswith("OR ") or name_upper == "OR":
            if in_group:
                m["elective_group"] = chr(group_letter_ord)
            else:
                m["elective_group"] = None
        else:
            if in_group:
                group_letter_ord += 1
            in_group = False
            m["elective_group"] = None
    return modules


def finalize_module(m):
    """Convert raw merged module dict into final clean fields."""
    prereq_codes = [p for p in m["prereq"] if MODULE_CODE_PATTERN.match(p)]
    coreq_codes = [p for p in m["coreq"] if MODULE_CODE_PATTERN.match(p)]
    needs_review = any("LEVEL" in p.upper() for p in m["prereq"]) or any("LEVEL" in c.upper() for c in m["coreq"])

    return {
        "module_code": m["code"],
        "module_name": re.sub(r'^(EITHER|OR)\s+', '', m["name"], flags=re.IGNORECASE).strip(),
        "classification": m["classification"],
        "credit_value": m["credits"],
        "nqf_level": m["nqf"],
        "year": m["year"],
        "semester": m["semester"],
        "prerequisite_subjects": ";".join(prereq_codes),
        "corequisite_subjects": ";".join(coreq_codes),
        "needs_manual_review": needs_review,
        "elective_group": m.get("elective_group"),
    }


def compute_choose_counts(finalized_modules):
    """Compute choose_count per (year, semester, elective_group) using the
    rule confirmed by hand against the real handbook for several
    programmes (4BSC21, 4BSC01, 4BSC03, 4BSC35, ...):

      - Year 1 semesters target 5 total modules
      - Year 2/3 semesters target 4 total modules
      - If a semester has exactly ONE elective group, that group's
        choose_count = target - (number of standalone modules that
        semester) -- e.g. 3 standalone + 1 group of 3 alternatives in
        Year 1 => choose_count = 5 - 3 = 2.
      - If a semester has MORE THAN ONE elective group, each group gets
        choose_count = 1 individually (the student picks 1 from each
        separate group, rather than splitting the remaining count
        across groups).

    Falls back to choose_count = 1 for anything outside Year 1-3, or
    where the computed count would be non-positive (a signal something
    upstream was mis-parsed rather than a real 0-or-negative choice).
    """
    TARGET = {1: 5, 2: 4, 3: 4}

    by_sem = {}  # (year, semester) -> {"standalone": int, "groups": {group: [codes]}}
    for m in finalized_modules:
        key = (m["year"], m["semester"])
        bucket = by_sem.setdefault(key, {"standalone": 0, "groups": {}})
        if m["elective_group"]:
            bucket["groups"].setdefault(m["elective_group"], []).append(m["module_code"])
        else:
            bucket["standalone"] += 1

    choose_counts = {}  # (year, semester, group) -> choose_count
    for (year, semester), bucket in by_sem.items():
        groups = bucket["groups"]
        if not groups:
            continue
        target = TARGET.get(year)
        if len(groups) == 1:
            group = next(iter(groups))
            if target is not None:
                cc = target - bucket["standalone"]
                if cc < 1:
                    cc = 1  # fell outside the expected pattern -- default safely rather than emit 0/negative
            else:
                cc = 1
            choose_counts[(year, semester, group)] = cc
        else:
            for group in groups:
                choose_counts[(year, semester, group)] = 1

    return choose_counts


def extract_all(pdf_path, start_page, end_page):
    print("Finding programme boundaries...")
    programmes = find_programme_boundaries(pdf_path, start_page, end_page)
    print(f"  Found {len(programmes)} programmes.\n")

    # The end_page reported above can overlap with the next programme's
    # start_page (off-by-one in the boundary walk), which pulls the next
    # programme's header/table rows into this one's data. start_page is
    # reliable (it's set exactly where the bold title line was found), so
    # derive a corrected, non-overlapping end_page from the next
    # programme's start_page instead.
    for i in range(len(programmes) - 1):
        programmes[i]["end_page"] = programmes[i + 1]["start_page"] - 1

    all_modules = {}       # module_code -> module dict (deduped)
    programme_modules = [] # list of {programme_code, module_code, elective_group, choose_count}

    with pdfplumber.open(pdf_path) as pdf:
        for prog in programmes:
            if prog["code"] == "UNKNOWN":
                continue
            try:
                rows = get_programme_table_rows(pdf, prog["start_page"], prog["end_page"])
                if not rows:
                    continue
                merged = merge_module_rows(rows)
                merged = detect_elective_groups(merged)

                # Finalize every module for this programme first, so
                # choose_count can be computed with the full semester
                # picture (needs to know standalone vs elective counts
                # across the whole semester, not just one module at a time).
                finalized = []
                for m in merged:
                    if not m["code"] or not MODULE_CODE_PATTERN.match(m["code"]):
                        continue  # skip section-heading fragments that leaked through
                    finalized.append(finalize_module(m))

                choose_counts = compute_choose_counts(finalized)

                for final in finalized:
                    code = final["module_code"]

                    if code not in all_modules:
                        all_modules[code] = final
                    else:
                        # Prefer whichever occurrence has more complete data --
                        # the same module can appear in multiple programmes,
                        # and one occurrence's row may have been cut awkwardly
                        # at a page boundary while another wasn't.
                        existing = all_modules[code]
                        existing_score = len(existing["prerequisite_subjects"]) + len(existing["corequisite_subjects"])
                        new_score = len(final["prerequisite_subjects"]) + len(final["corequisite_subjects"])
                        if new_score > existing_score:
                            all_modules[code] = final

                    # Elective status is derived from the EITHER/OR text
                    # pattern (elective_group), since the handbook's own
                    # classification letter is sometimes wrong for modules
                    # that are really part of an elective alternative (e.g.
                    # 4AMT111 is labelled 'C' despite being an OR option).
                    # effective_type resolves this once, here, so downstream
                    # queries don't need to re-derive it every time.
                    if final["elective_group"]:
                        effective_type = "Elective"
                    elif final["classification"] == "M":
                        effective_type = "Major"
                    elif final["classification"] == "C":
                        effective_type = "Compulsory"
                    else:
                        effective_type = final["classification"] or ""

                    group = final["elective_group"]
                    if group:
                        choose_count = choose_counts.get(
                            (final["year"], final["semester"], group), 1)
                    else:
                        choose_count = ""

                    programme_modules.append({
                        "programme_code": prog["code"],
                        "module_code": code,
                        "module_classification": final["classification"],
                        "elective_group": group or "",
                        "choose_count": choose_count,
                        "effective_type": effective_type,
                    })

                print(f"  {prog['code']:10s} {prog['name']:45s} -> {len(merged)} module rows")
            except Exception as e:
                print(f"  {prog['code']:10s} {prog['name']:45s} -> FAILED: {e}")
                continue

    return list(all_modules.values()), programme_modules


if __name__ == "__main__":
    modules, programme_modules = extract_all(PDF_PATH, START_PAGE, END_PAGE)

    with open("modules.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "module_code", "module_name", "credit_value",
            "nqf_level", "year", "semester", "prerequisite_subjects",
            "corequisite_subjects"
        ])
        writer.writeheader()
        for m in modules:
            row = {k: v for k, v in m.items()
                   if k not in ("elective_group", "classification", "needs_manual_review")}
            writer.writerow(row)

    with open("programme_modules.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "programme_code", "module_code", "module_classification",
            "elective_group", "choose_count", "effective_type"
        ])
        writer.writeheader()
        writer.writerows(programme_modules)

    print(f"\nSaved {len(modules)} unique modules to modules.csv")
    print(f"Saved {len(programme_modules)} programme-module links to programme_modules.csv")
    print("Review both CSVs before loading into the database.")
