import fitz  # pymupdf
import re
import psycopg2
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer


PDF_PATH = "documents/Handbooks/Faculty Of Science/FSAE-2026-Undergraduate-Handbook.pdf"
HANDBOOK_ID = 2          # must match an existing row in your `handbooks` table
START_PAGE = 30          # first page of the "double major combinations" section
END_PAGE = 244           # last page of that section (adjust per your PDF)

DB_CONFIG = {
    "dbname": "Campus_AI_Assistant_database",
    "user": "postgres",
    "password": "0nce&always@Pirates",
    "host": "localhost",
    "port": 5432,
}

CODE_PREFIX_PATTERN = re.compile(r'^([0-9][A-Z]{2,6}\d{0,2})\s+(.+)$')
CODE_SUFFIX_PATTERN = re.compile(r'^(.+?)\s*\(([0-9][A-Z]{2,6}\d{0,2})\)\s*$')
ANCHOR_WORDS = ("FACULTY OF", "DEPARTMENT:", "DEPARTMENTS:", "DEPARTMENT")

# Table column header text that repeats identically in every programme block —
# carries no actual programme information, just adds noise to the embedding.
HEADER_NOISE = {
    "SUBJECT NAME", "SUBJECT", "CODE", "SUBJECT CREDITS", "CREDITS",
    "NQF", "LEVEL", "NQF LEVEL", "PREREQUISITE", "PREREQUISITE SUBJECT(S)",
    "SUBJECT(S)", "CO-REQUISITE", "CO-REQUISITE SUBJECT(S)", "CO-",
    "REQUISITE",
}

# Real credit values and NQF levels in this handbook never exceed this —
# a lone number bigger than this is almost certainly a leaked page number
# (e.g. a stray "79" from a page footer), not real module data.
MAX_PLAUSIBLE_NUMBER = 40

TABLE_START_MARKER = "SUBJECT NAME"


def trim_before_table(lines):
    """Keep only the metadata block (Faculty, Admission Requirements, Total
    Credits, etc.) — stop before the module table begins. This is the
    mirror of extract_module_tables_to_csv.py's trim_to_table_start(),
    which keeps everything AFTER this marker instead."""
    for i, line in enumerate(lines):
        if line.strip().upper() == TABLE_START_MARKER:
            return lines[:i]
    return lines  # marker not found -- keep everything rather than lose data


def clean_lines(lines):
    """Strip repeated table-header noise from a programme's collected
    lines before joining into chunk_text. (The leaked-page-number filter
    used for module-table chunks isn't needed here -- this chunk no
    longer includes the module table, so there's no page-number leakage
    to guard against, and that filter would incorrectly strip legitimate
    values like '416' in TOTAL CREDITS TO GRADUATE.)"""
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped.upper() in HEADER_NOISE:
            continue
        cleaned.append(line)
    return cleaned


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


def extract_programme_blocks(pdf_path, start_page, end_page):
    """Returns list of dicts: {code, name, start_page, end_page, chunk_text}"""
    doc = fitz.open(pdf_path)
    programmes = []
    current = None

    all_lines = []
    for pno in range(start_page - 1, end_page):
        page = doc[pno]
        for txt, bold in get_bold_lines(page):
            all_lines.append((txt, bold, pno + 1))
    doc.close()

    i = 0
    n = len(all_lines)
    while i < n:
        txt, bold, pno = all_lines[i]
        if bold and looks_like_header_text(txt):
            lookahead = " ".join(l[0] for l in all_lines[i + 1:i + 4])
            if any(a in lookahead.upper() for a in ANCHOR_WORDS) or any(a in txt.upper() for a in ANCHOR_WORDS):
                if current is not None:
                    programmes.append(current)
                code, name = extract_code_and_name(txt)
                current = {
                    "code": code or "UNKNOWN",
                    "name": name,
                    "start_page": pno,
                    "end_page": pno,
                    "lines": []
                }
                i += 1
                continue
        if current is not None:
            current["lines"].append(txt)
            current["end_page"] = pno
        i += 1

    if current is not None:
        programmes.append(current)

    # Join lines into one chunk_text per programme, build section_title
    for p in programmes:
        p["lines"] = trim_before_table(p["lines"])
        p["lines"] = clean_lines(p["lines"])
        p["chunk_text"] = "\n".join(p["lines"])
        p["section_title"] = f"{p['name']} ({p['code']})" if p["code"] != "UNKNOWN" else p["name"]

    return programmes


# ============================================================
# STEP 2: EMBEDDING (nomic-embed-text-v1, 768-dim, local & free)
# ============================================================

def load_embedding_model():
    print("Loading nomic-embed-text-v1 (first run will download ~500MB)...")
    model = SentenceTransformer("nomic-ai/nomic-embed-text-v1", trust_remote_code=True)
    return model


def embed_chunk(model, text):
    # Nomic requires this task prefix for documents being stored/searched
    return model.encode("search_document: " + text).tolist()


# ============================================================
# STEP 3: DATABASE INSERT
# ============================================================

def insert_chunks(conn, programmes, handbook_id, model):
    cur = conn.cursor()
    inserted = 0
    skipped = []

    for idx, p in enumerate(programmes):
        if not p["chunk_text"].strip():
            skipped.append(p["name"])
            continue

        embedding = embed_chunk(model, p["chunk_text"])

        cur.execute(
            """
            INSERT INTO document_chunks
                (handbook_id, chunk_index, page_number, section_title, chunk_text, embedding)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (handbook_id, idx, p["start_page"], p["section_title"], p["chunk_text"], embedding),
        )
        inserted += 1
        print(f"  [{inserted}] Inserted: {p['section_title']} (pages {p['start_page']}-{p['end_page']})")

    conn.commit()
    cur.close()

    print(f"\nDone. Inserted {inserted} chunks.")
    if skipped:
        print(f"Skipped {len(skipped)} empty programmes: {skipped}")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("Step 1: Extracting programme blocks from PDF...")
    programmes = extract_programme_blocks(PDF_PATH, START_PAGE, END_PAGE)
    print(f"  Found {len(programmes)} programme blocks.\n")

    print("Step 2: Loading embedding model...")
    model = load_embedding_model()
    print("  Model loaded.\n")

    print("Step 3: Connecting to database...")
    conn = psycopg2.connect(**DB_CONFIG)
    register_vector(conn)  # lets psycopg2 understand VECTOR type
    print("  Connected.\n")

    print("Step 4: Embedding and inserting chunks...")
    insert_chunks(conn, programmes, HANDBOOK_ID, model)

    conn.close()
    print("\nAll done. Check your document_chunks table.")
