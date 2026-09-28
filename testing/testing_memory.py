import os
import re
import json

import psycopg2
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer
from anthropic import Anthropic
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=env_path)

DB_CONFIG = {
    "dbname": os.environ.get("DB_NAME", "your_db_name"),
    "user": os.environ.get("DB_USER", "your_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", "5432"),
}

CLAUDE_MODEL = "claude-sonnet-5"
EMBEDDING_MODEL_NAME = "nomic-ai/nomic-embed-text-v1"

MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversation_memory.json")
HISTORY_LIMIT = 6  # how many past exchanges to keep and feed back to Claude

client = Anthropic()
conn = psycopg2.connect(**DB_CONFIG)
register_vector(conn)
embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME, trust_remote_code=True)


def extract_text(response):
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""


MODULE_CODE_PATTERN = re.compile(r"\b(\d[A-Z]{2,6}\d{2,3})\b")
PROGRAMME_CODE_PATTERN = re.compile(r"\b(4[A-Z]{2,4}\d{2})\b")

INTENT_KEYWORDS = {
    "module_count": ["how many modules", "number of modules"],
    "list_electives": ["elective", "electives", "choose", "choice"],
    "list_modules": ["modules", "subjects", "courses"],
    "prerequisites": ["prerequisite", "prereq", "need before", "require", "can i register", "failed"],
    "credits_total": ["total credits", "graduate", "credits to graduate"],
    "majors_compulsory": ["compulsory", "major", "majors"],
    "programme_lookup": ["stand for", "full name", "what is the name of the programme"],
    "list_by_type": ["mainstream", "augmented", "foundation programmes"],
}


def get_programme_names():
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT programme_code FROM programme_modules")
    codes = [r[0] for r in cur.fetchall()]
    cur.close()
    return codes


def match_programme_code(question, known_codes):
    m = PROGRAMME_CODE_PATTERN.search(question.upper())
    if m and m.group(1) in known_codes:
        return m.group(1)
    return None


def match_module_code(question):
    m = MODULE_CODE_PATTERN.search(question.upper())
    return m.group(1) if m else None


def keyword_in_text(keyword, text):
    # word-boundary match, not plain substring -- "require" must not match
    # inside "requirements", "graduate" must not match inside
    # "undergraduate"/"postgraduate", etc.
    return re.search(r"\b" + re.escape(keyword) + r"\b", text) is not None


def detect_intent_keywords(question):
    q = question.lower()
    for intent, words in INTENT_KEYWORDS.items():
        if any(keyword_in_text(w, q) for w in words):
            return intent
    return None


PROGRAMME_TYPE_KEYWORDS = {
    "mainstream": "Mainstream",
    "augmented": "Augmented",
    "foundation": "Foundation",
}


def match_programme_type_keyword(question):
    q = question.lower()
    for word, ptype in PROGRAMME_TYPE_KEYWORDS.items():
        if keyword_in_text(word, q):
            return ptype
    return None


def build_name_phrase(question, skip_words):
    words = [w.strip(".,?!") for w in question.lower().split()]
    kept = [w for w in words if w and (w == "and" or w not in STOPWORDS) and w not in skip_words]
    return " ".join(SYNONYMS.get(w, w) for w in kept)


def prefer_mainstream(candidate_codes, ptype_by_code):
    # only default to Mainstream when the student didn't specify a type
    # AND every candidate is a type-variant of the SAME underlying
    # programme (same core name minus the type suffix) -- never use this
    # to silently pick between genuinely different subject pairings
    types = {ptype_by_code[c] for c in candidate_codes}
    if "Mainstream" in types and len(candidate_codes) == len(types):
        mainstream = [c for c in candidate_codes if ptype_by_code[c] == "Mainstream"]
        if len(mainstream) == 1:
            return mainstream[0]
    return None


def match_programme_by_name(question):
    ptype = match_programme_type_keyword(question)
    skip_words = set(PROGRAMME_TYPE_KEYWORDS) | {w for words in INTENT_KEYWORDS.values() for w in words}

    cur = conn.cursor()
    if ptype:
        cur.execute("SELECT programme_code, programme_name, programme_type FROM programmes WHERE programme_type = %s", (ptype,))
    else:
        cur.execute("SELECT programme_code, programme_name, programme_type FROM programmes")
    rows = cur.fetchall()
    cur.close()
    ptype_by_code = {code: t for code, name, t in rows}

    # exact full-name match beats a partial one -- "mathematics and
    # statistics" should resolve straight to the programme with that
    # EXACT name, not get treated as ambiguous just because a different,
    # longer programme name happens to contain it as a substring (e.g.
    # "applied mathematics and statistics")
    phrase = build_name_phrase(question, skip_words)
    if phrase:
        exact_matches = [code for code, name, t in rows if phrase == name.lower()]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1 and not ptype:
            default = prefer_mainstream(exact_matches, ptype_by_code)
            if default:
                return default

    # try an exact, word-order-preserving phrase match first -- this is
    # what tells "computer science and mathematics" apart from "applied
    # mathematics and computer science", which share the same words but
    # in a different order and would otherwise tie under plain scoring
    if phrase:
        phrase_matches = [code for code, name, t in rows if phrase in name.lower()]
        if len(phrase_matches) == 1:
            return phrase_matches[0]
        if len(phrase_matches) > 1 and not ptype:
            default = prefer_mainstream(phrase_matches, ptype_by_code)
            if default:
                return default

    # fall back to scored keyword matching -- more tolerant of extra
    # unrelated words in the question, at the cost of ignoring word order
    keywords = [kw for kw in extract_keywords(question) if kw not in skip_words]
    if not keywords:
        return None

    scored = []
    for code, name, t in rows:
        name_lower = name.lower()
        score = sum(1 for kw in keywords if kw in name_lower)
        if score > 0:
            scored.append((score, code))

    if not scored:
        return None

    scored.sort(reverse=True)
    top_score = scored[0][0]

    # require at least 2 matching words when there's more than one keyword
    # to match against -- one stray word matching is too weak a signal
    threshold = 1 if len(keywords) == 1 else 2
    if top_score < threshold:
        return None

    # if more than one programme ties for the top score, it's genuinely
    # ambiguous -- don't guess, unless it's just the Mainstream/Augmented/
    # Foundation variants of the same programme, in which case default
    # to Mainstream since the student didn't ask for a specific stream
    tied = [code for score, code in scored if score == top_score]
    if len(tied) > 1:
        if not ptype:
            default = prefer_mainstream(tied, ptype_by_code)
            if default:
                return default
        return None

    return tied[0]


def classify_with_claude(question, known_codes):
    prompt = f"""Classify this message to a UNIZULU campus assistant chatbot.

Known programme codes: {', '.join(known_codes)}

Message: "{question}"

A code like 4BSC21 can be either a PROGRAMME code or, structurally, look
like a module code. Use the wording to decide: if the question refers to
"the programme", asks its name/faculty/duration/admission requirements,
or the code is one of the known programme codes above, treat it as a
programme_code. Only treat a code as a module_code if the question is
clearly about one specific module (credits, prerequisites, etc).

If the message is a greeting, small talk, or not a real question about
UNIZULU (e.g. "hi", "thanks", "how are you"), use intent "casual".

Use "programme_lookup" ONLY when the message asks specifically for a
programme's official name, code, or stream type (Mainstream/Augmented/
Foundation) -- that is all the data this intent has access to.

Use these intents for questions about a programme's modules/curriculum,
answered from structured module data:
- "list_modules": the full list of modules in a programme
- "list_electives": only the elective modules in a programme
- "majors_compulsory": only the major/compulsory modules in a programme
- "credits_total": total credits needed to graduate from a programme
- "module_count": how many modules a student takes to graduate
- "prerequisites" or "module_lookup": facts about one specific module
  (credits, NQF level, prerequisites, co-requisites)

Use "metadata" for anything else about a programme that is NOT its
modules/curriculum and NOT covered by programme_lookup above -- admission
requirements, faculty, department, duration, or other general descriptive
information, pulled from the handbook's prose rather than structured
tables.

If the message asks for a list of programmes by type (mainstream,
augmented, foundation), use "list_by_type" and put the type
("Mainstream", "Augmented", or "Foundation") in programme_type.

Respond with ONLY a JSON object, no other text:
{{"intent": "casual" | "list_modules" | "list_electives" | "module_lookup" | "prerequisites" | "credits_total" | "module_count" | "majors_compulsory" | "programme_lookup" | "list_by_type" | "metadata" | "unknown",
  "programme_code": "<code from the list above, or null>",
  "module_code": "<a module code if the question is about one specific module, or null>",
  "programme_type": "<Mainstream, Augmented, or Foundation, or null>"}}"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        return json.loads(extract_text(response))
    except (json.JSONDecodeError, IndexError):
        print(f"[warning] classify_with_claude failed to parse a response for: {question!r}")
        return {"intent": "unknown", "programme_code": None, "module_code": None, "programme_type": None}


def detect_intent(question, known_codes, context=None):
    context = context or {}
    module_code = match_module_code(question)
    programme_code = match_programme_code(question, known_codes)
    if not programme_code:
        # a spoken name ("computer science and mathematics") won't match
        # the code regex above -- try resolving it against the real
        # programmes table before doing anything else, so a programme
        # gets identified no matter what the rest of the question asks
        programme_code = match_programme_by_name(question)

    intent = detect_intent_keywords(question)

    # a code shaped like "4BSC21" can be a module code or a programme
    # code -- only guess module_lookup when programme_code didn't also
    # match, otherwise let Claude disambiguate from the question wording
    if module_code and not intent and not programme_code:
        intent = "module_lookup"

    if intent == "list_by_type":
        return {"intent": intent, "programme_code": None, "module_code": None,
                "programme_type": match_programme_type_keyword(question)}

    if intent and (module_code or programme_code):
        result = {"intent": intent, "programme_code": programme_code,
                  "module_code": module_code, "programme_type": None}
    else:
        result = classify_with_claude(question, known_codes)
        if not result.get("programme_code"):
            result["programme_code"] = programme_code
        if not result.get("module_code"):
            result["module_code"] = module_code

    # if this question genuinely didn't resolve a code on its own, fall
    # back to whatever was last discussed -- lets a follow-up like "what
    # are its electives" work without repeating the code/name
    if not result.get("programme_code"):
        result["programme_code"] = context.get("last_programme_code")
    if not result.get("module_code"):
        result["module_code"] = context.get("last_module_code")

    return result


def get_modules_for_programme(programme_code):
    cur = conn.cursor()
    cur.execute("""
        SELECT m.module_code, m.module_name, m.credit_value, m.year, m.semester,
               pm.effective_type, pm.elective_group, pm.choose_count
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s
        ORDER BY m.year, m.semester
    """, (programme_code,))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_electives_for_programme(programme_code):
    cur = conn.cursor()
    cur.execute("""
        SELECT m.module_code, m.module_name, m.year, m.semester,
               pm.elective_group, pm.choose_count
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND pm.effective_type = 'Elective'
        ORDER BY m.year, m.semester, pm.elective_group
    """, (programme_code,))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_majors_compulsory_for_programme(programme_code):
    cur = conn.cursor()
    cur.execute("""
        SELECT m.module_code, m.module_name, m.year, m.semester, pm.effective_type
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND pm.effective_type IN ('Major', 'Compulsory')
        ORDER BY m.year, m.semester
    """, (programme_code,))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_module_by_code(module_code):
    cur = conn.cursor()
    cur.execute("""
        SELECT module_code, module_name, credit_value, nqf_level, year, semester,
               prerequisite_subjects, corequisite_subjects
        FROM modules WHERE module_code = %s
    """, (module_code,))
    row = cur.fetchone()
    cur.close()
    return row


def get_credits_total(programme_code):
    cur = conn.cursor()
    cur.execute("""
        SELECT m.year, m.semester, m.credit_value, pm.elective_group, pm.choose_count
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s
    """, (programme_code,))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_module_count_data(programme_code):
    cur = conn.cursor()
    cur.execute("""
        SELECT m.year, m.semester, pm.elective_group, pm.choose_count
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s
    """, (programme_code,))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_programme_by_code(programme_code):
    cur = conn.cursor()
    cur.execute("""
        SELECT programme_code, programme_name, programme_type
        FROM programmes WHERE programme_code = %s
    """, (programme_code,))
    row = cur.fetchone()
    cur.close()
    return row


def get_programmes_by_type(programme_type):
    cur = conn.cursor()
    cur.execute("""
        SELECT programme_code, programme_name, programme_type
        FROM programmes WHERE programme_type = %s
        ORDER BY programme_code
    """, (programme_type,))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_embedding(text, prefix):
    embedding = embedding_model.encode(f"{prefix}{text}")
    return embedding.tolist()


STOPWORDS = {
    "what", "are", "is", "the", "for", "of", "in", "to", "a", "an", "and",
    "do", "does", "i", "need", "modules", "module", "requirements",
    "requirement", "admission", "programme", "program", "course", "degree",
    "year", "years", "will", "can", "you", "tell", "me", "about", "on",
    "with", "my", "please",
}

SYNONYMS = {
    "maths": "mathematics",
    "math": "mathematics",
    "cs": "computer science",
    "it": "information technology",
    "stats": "statistics",
    "bio": "biology",
    "chem": "chemistry",
    "phys": "physics",
    "geo": "geography",
}


def extract_keywords(question):
    words = [w.strip(".,?!") for w in question.lower().split()]
    keywords = [w for w in words if w and w not in STOPWORDS]
    return [SYNONYMS.get(kw, kw) for kw in keywords]


def vector_search(question, top_k=5):
    embedding = get_embedding(question, "search_query: ")
    cur = conn.cursor()
    cur.execute("""
        SELECT chunk_id, section_title, chunk_text, page_number,
               embedding <=> %s::vector AS distance
        FROM document_chunks
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """, (embedding, embedding, top_k))
    rows = cur.fetchall()
    cur.close()
    return rows


def keyword_search(keywords, limit=5):
    if not keywords:
        return []
    cur = conn.cursor()
    conditions = " AND ".join(["section_title ILIKE %s"] * len(keywords))
    params = [f"%{kw}%" for kw in keywords]
    cur.execute(f"""
        SELECT chunk_id, section_title, chunk_text, page_number, 0.0 AS distance
        FROM document_chunks
        WHERE {conditions}
        LIMIT %s
    """, (*params, limit))
    rows = cur.fetchall()
    cur.close()
    return rows


def retrieve_chunks(question, top_k=5):
    vector_rows = vector_search(question, top_k)
    keyword_rows = keyword_search(extract_keywords(question))

    seen_ids = set()
    merged = []
    for r in list(vector_rows) + list(keyword_rows):
        if r[0] not in seen_ids:
            seen_ids.add(r[0])
            merged.append(r)

    return [{"chunk_id": r[0], "section_title": r[1], "chunk_text": r[2], "page_number": r[3]} for r in merged]


def format_structured_answer(question, data_summary):
    prompt = f"""Answer the student's question using ONLY the data below.
Do not add, infer, or guess anything not present in this data.

Question: "{question}"

DATA:
{data_summary}"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return extract_text(response)


def format_rag_answer(question, chunks, history=None):
    context = "\n\n---\n\n".join(
        f"[Source: {c['section_title']}, page {c['page_number']}]\n{c['chunk_text']}"
        for c in chunks
    )

    system_prompt = (
        "You are a helpful assistant answering questions about UNIZULU using "
        "ONLY the context provided below.\n\n"
        "Students will refer to programmes casually by name (e.g. 'computer "
        "science and maths', 'CS and stats') rather than by their official "
        "programme code (e.g. '4BSC21'). Match the student's wording against "
        "the programme names shown in the context's [Source: ...] labels "
        "yourself -- NEVER ask the student to provide a programme code, and "
        "never require exact official phrasing. If the context contains "
        "multiple distinct programmes that could match what the student "
        "means, briefly list the matching programme names (by name, not "
        "code) and ask which one they mean.\n\n"
        "If the answer genuinely isn't in the context, say you don't have "
        "that information rather than guessing.\n\n"
        f"CONTEXT:\n{context}"
    )

    messages = []
    for turn in (history or []):
        messages.append({"role": "user", "content": turn["question"]})
        messages.append({"role": "assistant", "content": turn["answer"]})
    messages.append({"role": "user", "content": question})

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=messages,
    )
    return extract_text(response)


def summarize_modules(programme_code, rows):
    if not rows:
        return f"No modules found for {programme_code}."
    lines = [f"Modules for {programme_code}:"]
    for code, name, credit, year, sem, etype, group, choose in rows:
        line = f"{code} - {name} ({credit} credits, Year {year} Sem {sem}, {etype}"
        if group:
            line += f", group {group}, choose {choose}"
        line += ")"
        lines.append(line)
    return "\n".join(lines)


def summarize_electives(programme_code, rows):
    if not rows:
        return f"{programme_code} has no elective modules -- every module in this programme is compulsory or a major."
    lines = [f"Electives for {programme_code}:"]
    for code, name, year, sem, group, choose in rows:
        lines.append(f"{code} - {name} (Year {year} Sem {sem}, group {group}, choose {choose})")
    return "\n".join(lines)


def summarize_majors_compulsory(programme_code, rows):
    if not rows:
        return f"No major/compulsory modules found for {programme_code}."
    lines = [f"Majors/Compulsory modules for {programme_code}:"]
    for code, name, year, sem, etype in rows:
        lines.append(f"{code} - {name} (Year {year} Sem {sem}, {etype})")
    return "\n".join(lines)


def summarize_module(row):
    if not row:
        return "No module found with that code."
    code, name, credit, nqf, year, sem, prereq, coreq = row
    lines = [
        f"{code} - {name}",
        f"Credits: {credit}, NQF level: {nqf}, Year {year} Semester {sem}",
        f"Prerequisites: {', '.join(prereq) if prereq else 'None'}",
        f"Co-requisites: {', '.join(coreq) if coreq else 'None'}",
    ]
    if prereq:
        lines.append(
            f"Registration rule: a student must have PASSED every listed "
            f"prerequisite ({', '.join(prereq)}) before registering for {code}. "
            f"A student who failed a prerequisite cannot register for {code} "
            f"until they pass it."
        )
    else:
        lines.append(f"Registration rule: {code} has no prerequisites, so it can be registered for directly.")
    return "\n".join(lines)


def summarize_credits(programme_code, rows):
    if not rows:
        return f"No credit data found for {programme_code}."
    seen_groups = {}
    total = 0
    for year, sem, credit, group, choose in rows:
        if group:
            key = (year, sem, group)
            if key not in seen_groups:
                seen_groups[key] = True
                total += credit * (choose or 1)
        else:
            total += credit
    return f"{programme_code} total credits to graduate: {total}"


def summarize_module_count(programme_code, rows):
    if not rows:
        return f"No module data found for {programme_code}."
    seen_groups = {}
    count = 0
    for year, sem, group, choose in rows:
        if group:
            key = (year, sem, group)
            if key not in seen_groups:
                seen_groups[key] = True
                count += choose or 1
        else:
            count += 1
    return f"A student completing {programme_code} takes {count} modules in total to graduate."


def summarize_programme(row):
    if not row:
        return "No programme found with that code."
    code, name, ptype = row
    return f"{code} - {name} ({ptype})"


def summarize_programme_list(programme_type, rows):
    if not rows:
        return f"No {programme_type} programmes found."
    lines = [f"{programme_type} programmes:"]
    for code, name, ptype in rows:
        lines.append(f"{code} - {name}")
    return "\n".join(lines)


def casual_reply(question, history=None):
    messages = []
    for turn in (history or []):
        messages.append({"role": "user", "content": turn["question"]})
        messages.append({"role": "assistant", "content": turn["answer"]})
    messages.append({"role": "user", "content": question})

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        messages=messages,
    )
    return extract_text(response)


def answer_question(question, known_codes, context=None):
    context = context if context is not None else {}
    history = context.get("history", [])

    result = detect_intent(question, known_codes, context)
    intent = result.get("intent")
    programme_code = result.get("programme_code")
    module_code = result.get("module_code")
    programme_type = result.get("programme_type")

    # remember what this turn resolved, so a follow-up question can
    # reuse it without repeating the code/name
    if programme_code:
        context["last_programme_code"] = programme_code
    if module_code:
        context["last_module_code"] = module_code

    if intent == "casual":
        return casual_reply(question, history)

    if intent == "programme_lookup" and programme_code:
        row = get_programme_by_code(programme_code)
        return format_structured_answer(question, summarize_programme(row))

    if intent == "list_by_type" and programme_type:
        rows = get_programmes_by_type(programme_type)
        return format_structured_answer(question, summarize_programme_list(programme_type, rows))

    if intent == "module_lookup" and module_code:
        row = get_module_by_code(module_code)
        return format_structured_answer(question, summarize_module(row))

    if intent == "list_modules" and programme_code:
        rows = get_modules_for_programme(programme_code)
        return format_structured_answer(question, summarize_modules(programme_code, rows))

    if intent == "list_electives" and programme_code:
        rows = get_electives_for_programme(programme_code)
        return format_structured_answer(question, summarize_electives(programme_code, rows))

    if intent == "majors_compulsory" and programme_code:
        rows = get_majors_compulsory_for_programme(programme_code)
        return format_structured_answer(question, summarize_majors_compulsory(programme_code, rows))

    if intent == "prerequisites" and module_code:
        row = get_module_by_code(module_code)
        return format_structured_answer(question, summarize_module(row))

    if intent == "credits_total" and programme_code:
        rows = get_credits_total(programme_code)
        return format_structured_answer(question, summarize_credits(programme_code, rows))

    if intent == "module_count" and programme_code:
        rows = get_module_count_data(programme_code)
        return format_structured_answer(question, summarize_module_count(programme_code, rows))

    chunks = retrieve_chunks(question)
    if chunks:
        return format_rag_answer(question, chunks, history)

    return "I couldn't find information to answer that. Try rephrasing, or ask about a specific programme or module code."


def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_programme_code": None, "last_module_code": None, "history": []}


def save_memory(context):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(context, f, indent=2)


if __name__ == "__main__":
    known_codes = get_programme_names()
    context = load_memory()

    print("UNIZULU Campus AI Assistant (terminal test mode)")
    print("Type 'exit' to quit.\n")
    if context["history"]:
        print(f"(resuming previous session -- {len(context['history'])} past exchange(s) loaded)\n")

    while True:
        question = input("You: ").strip()
        if question.lower() in ("exit", "quit"):
            break
        if not question:
            continue

        answer = answer_question(question, known_codes, context)
        print(f"\nAssistant: {answer}\n")

        context["history"].append({"question": question, "answer": answer})
        context["history"] = context["history"][-HISTORY_LIMIT:]
        save_memory(context)

    conn.close()

    conn.close()