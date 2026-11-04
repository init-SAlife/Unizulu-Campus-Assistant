import os
import re
import json
import base64
import time
import psycopg2
from anthropic import Anthropic
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(dotenv_path=env_path)

DB_CONFIG = {
    "dbname": os.environ.get("DB_NAME", "your_db_name"),
    "user": os.environ.get("DB_USER", "your_user"),
    "password": os.environ.get("DB_PASSWORD", "your_password"),
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", "5432"),
}

CLAUDE_MODEL = "claude-sonnet-5"

POR_EXTRACTION_PROMPT = """Extract ONLY the following from this proof-of-registration document:
- qualification/programme code (e.g. 4BSC21)
- for each row in the module table: the subject code, and whether it's cancelled (the Cancel column, Y or N)

Do NOT extract, transcribe, or repeat: ID number, student name, banking
details, outstanding balance, or any other field. If those appear in the
document, ignore them completely -- do not include them in the output
even if visible.

Return ONLY this JSON object, no other text, no markdown fences:
{"programme_code": string or null, "modules": [{"module_code": string, "is_cancelled": boolean}]}

If a field isn't clearly legible, use null for that field rather than guessing."""

ACADEMIC_RECORD_EXTRACTION_PROMPT = """Extract academic record data from this document.

IMPORTANT: A single document may cover ONE academic year or SEVERAL --
some records show one year per file, others show a student's entire
history (e.g. 2023 through 2026) in a single file. Look through the
WHOLE document and identify every distinct academic year present,
however many there are. Do not stop after the first year you find.

For EACH academic year found, extract:
- the academic year (e.g. 2024) and its overall annual result if shown (e.g. "PR - PROMOTED TO NEXT LEVEL", "FY - NOT PROMOTED: REPEAT YEAR")
- for each module block under that year: subject code, academic period (e.g. Semester 1, Re-exam), year mark, final mark, and result exactly as printed (e.g. PASS, PASS WITH MERIT, FAIL SUBMINIMUM ON EXAMINATION, FAIL RE-EXAM ALLOWED, ABSENT FROM EXAMINATION, or a note that the subject was cancelled)

If the same module appears more than once under the same year (e.g. an
original attempt and a re-exam), include BOTH as separate entries rather
than only the final one -- do not silently drop or merge them.

Do NOT extract, transcribe, or repeat: student name, ID number, or any
other identifying field. If those appear in the document, ignore them
completely.

Return ONLY this JSON object, no other text, no markdown fences:
{"years": [
  {"academic_year": integer or null, "annual_result_code": string or null, "annual_result_description": string or null,
   "modules": [{"module_code": string, "academic_period": string or null, "year_mark": integer or null, "final_mark": integer or null, "result": string or null}]}
]}

If a field isn't clearly legible, use null for that field rather than guessing."""

MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversation_memory.json")
HISTORY_LIMIT = 6  # how many past exchanges to keep when the topic changes
FOCUSED_HISTORY_LIMIT = 20  # a much larger cap while the student stays on
                            # the same programme/module/document -- lets a focused session run
                            # 10+ questions deep without losing earlier context

client = Anthropic()


class _ReconnectingConnection:
    # The handbook database connection, made safe for a hosted server.
    CHECK_EVERY_S = 30

    def __init__(self, config):
        self._config = config
        self._conn = None
        self._pid = None
        self._last_check = 0.0

    def _open(self):
        self._conn = psycopg2.connect(
            **self._config,
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
        )
        self._conn.autocommit = True
        self._pid = os.getpid()
        self._last_check = time.monotonic()

    def _live(self):
        if self._pid != os.getpid():
            # we are in a new gunicorn worker (a copy of the process that opened the
            # connection): never share it -- over SSL that corrupts it.
            self._conn = None
        if self._conn is None or self._conn.closed:
            self._open()
        elif time.monotonic() - self._last_check > self.CHECK_EVERY_S:
            try:
                with self._conn.cursor() as cur:
                    cur.execute("SELECT 1")
                self._last_check = time.monotonic()
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._open()
        return self._conn

    def cursor(self, *args, **kwargs):
        return self._live().cursor(*args, **kwargs)

    @property
    def autocommit(self):
        return True

    @autocommit.setter
    def autocommit(self, value):
        pass  # always on (see the class description)

    def close(self):
        if self._conn is not None and not self._conn.closed and self._pid == os.getpid():
            self._conn.close()


conn = _ReconnectingConnection(DB_CONFIG)


def extract_text(response):
    # All text blocks of a reply joined together (a reply can contain more than one text block).
    return "".join(block.text for block in response.content if block.type == "text")


# Handbook editions.

HANDBOOK_WORDS = ["handbook", "prospectus", "edition", "rule book", "rulebook"]
ASK_CURRENT_EDITION_PHRASES = [
    "which handbook are you using", "what handbook are you using",
    "which handbook am i using", "what handbook am i using",
    "which handbook is this", "what handbook is this",
    "current handbook", "which edition are you using",
]
SWITCH_EDITION_PHRASES = [
    "switch handbook", "switch the handbook", "switch to another handbook",
    "change handbook", "change the handbook", "change handbooks",
    "different handbook", "another handbook", "other handbook",
    "switch edition", "change edition", "change the edition",
]
YEAR_PATTERN = re.compile(r"\b((?:19|20)\d{2})\b")
# "in 2023", "for 2023", "under the 2023", "2023 one" -- a year used as a
# choice rather than a year mentioned in passing
YEAR_AS_CHOICE_PATTERN = re.compile(
    r"\b(?:in|for|under|from|of|using|use|per|according to)\s+(?:the\s+)?((?:19|20)\d{2})\b"
    r"|\b((?:19|20)\d{2})\s+(?:one|version)\b"
)


class EditionRequiredError(ValueError):
    # Raised when a handbook query is attempted without an edition -- a programming error, never
    # something to paper over with a default.
    pass


def require_edition(edition_id):
    if edition_id is None:
        raise EditionRequiredError(
            "A handbook_edition_id is required for every handbook query."
        )
    return int(edition_id)


def get_handbook_editions():
    # Every edition in handbook_editions, newest first, as dicts.
    cur = conn.cursor()
    cur.execute("SELECT id, year, label FROM handbook_editions ORDER BY year DESC, id DESC")
    rows = cur.fetchall()
    cur.close()
    return [{"id": i, "year": y, "label": l} for i, y, l in rows]


def get_edition(edition_id):
    if edition_id is None:
        return None
    for e in get_handbook_editions():
        if e["id"] == int(edition_id):
            return e
    return None


def get_valid_edition_id(context):
    # The conversation's chosen edition, if it still exists in the database.
    edition = get_edition(context.get("edition_id"))
    if edition:
        return edition["id"]
    editions = get_handbook_editions()
    if len(editions) == 1:
        context["edition_id"] = editions[0]["id"]
        return editions[0]["id"]
    context["edition_id"] = None
    return None


def edition_display(edition):
    return f"the {edition['year']} handbook ({edition['label']})"


def build_edition_question(editions=None):
    editions = editions or get_handbook_editions()
    options = "\n".join(f"- {e['year']}: {e['label']}" for e in editions)
    return (
        "Which handbook should I use for this? The rules can differ between "
        "handbooks, so I want to answer from the right one. I have:\n"
        f"{options}\n"
        "Just reply with the year (for example, "
        f"\"{editions[0]['year']}\"). I'll keep using it for the rest of our "
        "chat until you ask me to switch."
    )


def _strip_edition_words(text):
    # What's left of a message once the year, handbook words and filler are removed
    t = YEAR_PATTERN.sub(" ", text.lower())
    for w in HANDBOOK_WORDS:
        t = re.sub(r"\b" + re.escape(w) + r"s?\b", " ", t)
    filler = {"the", "use", "using", "one", "please", "lets", "let's", "go", "with",
              "i", "want", "would", "like", "to", "switch", "change", "a", "an",
              "for", "in", "under", "that", "this", "ok", "okay", "yes", "version",
              "me", "can", "you", "we", "us", "it", "is", "of", "from", "on", "and"}
    words = [w.strip(".,!?'\"") for w in t.split()]
    return [w for w in words if w and w not in filler]


def find_edition_in_message(question, awaiting_edition=False):
    # Looks for a handbook-edition choice in a message.
    q = question.lower()
    years = [int(y) for y in YEAR_PATTERN.findall(q)]
    if not years:
        return None, None

    has_handbook_word = any(keyword_in_text(w, q) or keyword_in_text(w + "s", q) for w in HANDBOOK_WORDS)
    choice_years = [int(a or b) for a, b in YEAR_AS_CHOICE_PATTERN.findall(q)]
    pure_choice = not _strip_edition_words(question)

    if not (awaiting_edition or has_handbook_word or choice_years or pure_choice):
        return None, None

    by_year = {e["year"]: e for e in get_handbook_editions()}
    candidates = choice_years or years
    for y in candidates:
        if y in by_year:
            return "found", by_year[y]
    # a year that isn't a handbook we have: only say so when the student is
    # clearly naming a handbook -- "I failed it in 2024" is not a handbook choice
    if awaiting_edition or has_handbook_word or pure_choice:
        return "unknown", candidates[0]
    return None, None


def is_pure_edition_choice(question):
    return not _strip_edition_words(question)


def is_ask_current_edition(question):
    q = question.lower()
    return any(keyword_in_text(p, q) for p in ASK_CURRENT_EDITION_PHRASES)


def is_switch_edition_request(question):
    q = question.lower()
    return any(keyword_in_text(p, q) for p in SWITCH_EDITION_PHRASES)


def edition_needs_for_intent(intent, programme_code, module_code, programme_type, context):
    # True only when this intent will actually query handbook data with the codes it has -- so
    # the student is never asked "which handbook?" for a greeting
    if intent in ("programme_lookup", "list_modules", "list_electives", "majors_compulsory",
                  "credits_total", "module_count", "admission_requirements"):
        return bool(programme_code)
    if intent in ("module_lookup", "prerequisites"):
        return bool(module_code)
    if intent == "list_by_type":
        return bool(programme_type)
    if intent == "failure_impact":
        return bool(programme_code and module_code)
    if intent == "registration_check":
        # no upload yet means the answer is "please upload" -- no edition needed
        por = get_cached_document(context, "proof_of_registration")
        return bool(por and por.get("modules"))
    if intent == "module_eligibility":
        return bool(module_code)
    if intent == "academic_progress":
        # no record or no programme means a plain "please upload / which
        # programme?" reply -- no edition needed for that
        return bool(get_academic_history(context) and resolve_personal_programme(programme_code, context))
    return False


MODULE_CODE_PATTERN = re.compile(r"\b(\d[A-Z]{2,6}\d{3})\b")
PROGRAMME_CODE_PATTERN = re.compile(r"\b(4[A-Z]{2,4}\d{2})\b")

INTENT_KEYWORDS = {
    "module_count": ["how many modules", "number of modules"],
    "list_electives": ["elective", "electives", "choose", "choice"],
    "list_modules": ["modules", "subjects", "courses"],
    "prerequisites": ["prerequisite", "prerequisites", "prereq", "need before", "require", "can i register", "failed"],
    "credits_total": ["total credits", "graduate", "credits to graduate"],
    "majors_compulsory": ["compulsory", "major", "majors"],
    "programme_lookup": ["stand for", "full name", "what is the name of the programme"],
    "list_by_type": ["mainstream", "augmented", "foundation programmes"],
    # deliberately avoids "require"/"requirement" alone (collides with prerequisites) and
    # "major"/"majors" alone (collides with majors_compulsory)
    "admission_requirements": [
        "admission requirement", "admission requirements",
        "entry requirement", "entry requirements",
        "requirements to get into", "requirements to enter",
        "matric requirements", "matric points", "nsc points",
        "what marks do i need", "what percentage do i need",
    ],
    # distinct from "prerequisites" ("what do I need before registering")
    "failure_impact": [
        "will it add a year", "add a year to my degree", "add a year to my studies",
        "delay my degree", "delay my studies",
        "what happens if i fail", "if i fail this module", "if i fail that module",
        "how many modules will it block", "how many modules would it block",
        "block me from registering",
    ],
}

# "modules" alone (list_modules' keyword) is too generic and wins the match even for questions
# like "which modules will be blocked if I fail X"
FAIL_WORDS = ["fail", "failed", "failing"]
CONSEQUENCE_WORDS = ["block", "blocked", "blocking", "add a year", "delay", "affect", "impact"]


def is_failure_impact_question(question):
    q = question.lower()
    has_fail = any(keyword_in_text(w, q) for w in FAIL_WORDS)
    has_consequence = any(keyword_in_text(w, q) for w in CONSEQUENCE_WORDS)
    return has_fail and has_consequence


# same collision problem as failure_impact: "which modules are registered" or "check my
# registration" both naturally contain "modules"/"registered"
REGISTRATION_CHECK_PHRASES = [
    "verify my registration", "verify my proof of registration",
    "validate my registration", "validate my proof of registration",
    "check my registration", "is my registration correct",
    "registration correct", "registration valid",
    "registration check", "check registration", "do a registration check",
    "which modules are registered", "what modules are registered",
    "modules registered in", "modules i uploaded", "modules i registered for",
    "check the document i uploaded", "check what i uploaded",
]


def is_registration_check_question(question):
    q = question.lower()
    return any(keyword_in_text(p, q) for p in REGISTRATION_CHECK_PHRASES)


# Personal questions answered from the student's OWN academic record (plus the handbook).
PERSONAL_MODULE_PHRASES = [
    "should i", "should i be doing", "should i take", "should i register",
    "can i register", "can i take", "can i do", "am i allowed", "am i eligible",
    "eligible", "do i qualify", "do i need to do", "do i need to take",
    "based on my", "my academic record", "my record", "my results", "my result",
    "my marks", "my mark", "did i pass", "have i passed", "did i fail",
    "have i failed", "what did i get", "how did i do",
]
PROGRESS_PHRASES = [
    "what have i passed", "which modules have i passed", "modules i passed",
    "modules i have passed", "modules have i passed",
    "what have i failed", "which modules have i failed", "modules i failed",
    "modules i have failed", "modules have i failed",
    "what do i still need", "what's left", "whats left", "what is left",
    "still need to pass", "still need to complete", "still need to do",
    "outstanding modules", "my progress", "my results", "my marks",
    "my academic record", "how far am i", "credits have i", "credits do i have",
    "how many credits have i", "am i on track",
]


def is_personal_module_question(question):
    q = question.lower()
    return any(keyword_in_text(p, q) for p in PERSONAL_MODULE_PHRASES)


def is_progress_question(question):
    q = question.lower()
    return any(keyword_in_text(p, q) for p in PROGRESS_PHRASES)


def get_programme_names():
    # Every programme code in any edition.
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
    # word-boundary match, not plain substring -- "require" must not match inside "requirements"
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
    types = {ptype_by_code[c] for c in candidate_codes}
    if "Mainstream" in types and len(candidate_codes) == len(types):
        mainstream = [c for c in candidate_codes if ptype_by_code[c] == "Mainstream"]
        if len(mainstream) == 1:
            return mainstream[0]
    return None


def _unique(codes):
    # Order-preserving de-duplication -- the same programme/module code appears once per
    # handbook edition, and must count as ONE match.
    seen, out = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def match_programme_by_name(question, edition_id=None):
    # Resolves a spoken programme name to its code.
    ptype = match_programme_type_keyword(question)
    skip_words = set(PROGRAMME_TYPE_KEYWORDS) | {w for words in INTENT_KEYWORDS.values() for w in words}

    sql = "SELECT DISTINCT programme_code, programme_name, programme_type FROM programme_info WHERE TRUE"
    params = []
    if ptype:
        sql += " AND programme_type = %s"
        params.append(ptype)
    if edition_id is not None:
        sql += " AND handbook_edition_id = %s"
        params.append(edition_id)
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    ptype_by_code = {code: t for code, name, t in rows}

    # exact full-name match beats a partial one -- "mathematics and statistics" should resolve
    # straight to the programme with that EXACT name
    phrase = build_name_phrase(question, skip_words)
    if phrase:
        exact_matches = _unique(code for code, name, t in rows if phrase == name.lower())
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1 and not ptype:
            default = prefer_mainstream(exact_matches, ptype_by_code)
            if default:
                return default

    # try an exact, word-order-preserving phrase match first
    if phrase:
        phrase_matches = _unique(code for code, name, t in rows if phrase in name.lower())
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

    best_by_code = {}
    for code, name, t in rows:
        name_lower = name.lower()
        score = sum(1 for kw in keywords if kw in name_lower)
        if score > best_by_code.get(code, 0):
            best_by_code[code] = score
    scored = [(score, code) for code, score in best_by_code.items()]

    if not scored:
        return None

    scored.sort(reverse=True)
    top_score = scored[0][0]

    # require at least 2 matching words when there's more than one keyword
    # to match against one stray word matching is too weak a signal
    threshold = 1 if len(keywords) == 1 else 2
    if top_score < threshold:
        return None

    # if more than one programme ties for the top score, it's genuinely ambiguous - don't guess
    tied = [code for score, code in scored if score == top_score]
    if len(tied) > 1:
        if not ptype:
            default = prefer_mainstream(tied, ptype_by_code)
            if default:
                return default
        return None

    return tied[0]


def match_module_by_name(question, edition_id=None):
    # Resolves a spoken module name to its code, using the chosen edition's names when one is
    # set, otherwise every edition's.
    q = question.lower()

    cur = conn.cursor()
    if edition_id is not None:
        cur.execute("SELECT DISTINCT module_code, module_name FROM modules WHERE handbook_edition_id = %s",
                    (edition_id,))
    else:
        cur.execute("SELECT DISTINCT module_code, module_name FROM modules")
    rows = cur.fetchall()
    cur.close()

    # module questions are usually long, wordy sentences with the module name embedded somewhere
    # inside
    matches = [(code, name) for code, name in rows if name.lower() in q]
    if not matches:
        return None
    if len(_unique(code for code, name in matches)) == 1:
        return matches[0][0]

    # if multiple module names match (e.g. one name is itself a substring
    # of another), prefer the longest/most specific match
    max_len = max(len(name) for code, name in matches)
    longest = _unique(code for code, name in matches if len(name) == max_len)
    if len(longest) == 1:
        return longest[0]

    return None


def classify_with_claude(question, known_codes, context=None):
    context = context or {}
    last_programme = context.get("last_programme_code")
    last_module = context.get("last_module_code")

    continuity_note = ""
    if last_programme or last_module:
        parts = []
        if last_programme:
            parts.append(f"programme {last_programme}")
        if last_module:
            parts.append(f"module {last_module}")
        continuity_note = (
            f"\nThe student was just discussing {' and '.join(parts)}. "
            "If this message is a vague follow-up (e.g. \"tell me more\", "
            "\"what about this module/programme/course\") without a new "
            "code or name of its own, assume it continues that same topic "
            "-- don't treat loose wording like \"module\" literally if the "
            "conversation so far has clearly been about a programme, or "
            "vice versa.\n"
        )

    prompt = f"""Classify this message to a UNIZULU campus assistant chatbot.

Known programme codes: {', '.join(known_codes)}
{continuity_note}
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

Use "admission_requirements" when the question is about what's needed to
GET INTO a programme -- matric subjects, percentages/levels, NSC points,
degree endorsement. This is different from "prerequisites", which is
about registering for one specific MODULE, not admission to a programme.

Use "failure_impact" when the question asks about the CONSEQUENCE of
failing a specific module the student already names -- will it delay
their degree, add a year, or block them from other modules. This needs
BOTH a programme_code and a module_code. This is different from
"prerequisites", which is about what's needed BEFORE registering for a
module, not what happens AFTER failing one.

Use "module_eligibility" when the student asks about THEIR OWN situation
with one specific module, using their results -- e.g. "should I be doing
4CPS232", "can I register for X based on my results", "did I pass X",
"what did I get for X". Needs a module_code.

Use "academic_progress" when the student asks about their OWN results or
progress overall -- e.g. "what have I passed/failed", "what do I still
need to complete", "how many credits have I passed". No module_code needed.

Use "registration_check" when the student wants their proof of
registration (or academic record) checked, verified, or validated against
their programme's curriculum -- e.g. "can you verify my registration",
"is my registration correct", "which modules are registered in the
document I uploaded". This does not require a module_code, and the
programme_code may be unknown at this point (it can come from the
uploaded document itself instead).

If the message asks for a list of programmes by type (mainstream,
augmented, foundation), use "list_by_type" and put the type
("Mainstream", "Augmented", or "Foundation") in programme_type.

Respond with ONLY a JSON object, no other text:
{{"intent": "casual" | "list_modules" | "list_electives" | "module_lookup" | "prerequisites" | "credits_total" | "module_count" | "majors_compulsory" | "programme_lookup" | "list_by_type" | "admission_requirements" | "failure_impact" | "registration_check" | "module_eligibility" | "academic_progress" | "unknown",
  "programme_code": "<code from the list above, or null>",
  "module_code": "<a module code if the question is about one specific module, or null>",
  "programme_type": "<Mainstream, Augmented, or Foundation, or null>"}}"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = extract_text(response)
    try:
        # same tolerant reader as document extraction: copes with ```json
        # fences or a sentence before/after the JSON object
        result = _parse_json_reply(raw)
    except (ValueError, IndexError) as e:
        print(f"[warning] classify_with_claude failed to parse a response for: {question!r} "
              f"({e}; reply started with {raw[:80]!r})")
        return {"intent": "unknown", "programme_code": None, "module_code": None, "programme_type": None}
    if not isinstance(result, dict):
        return {"intent": "unknown", "programme_code": None, "module_code": None, "programme_type": None}

    # Claude can guess/invent a code for something it doesn't actually have -- only trust values
    # that are real
    module_code = result.get("module_code")
    if module_code and not MODULE_CODE_PATTERN.match(module_code.upper()):
        result["module_code"] = None
    programme_code = result.get("programme_code")
    if programme_code and programme_code.upper() not in known_codes:
        result["programme_code"] = None

    return result


def detect_intent(question, known_codes, context=None):
    context = context or {}
    module_code = match_module_code(question)
    module_named_explicitly = False
    if not module_code:
        # a spoken name ("Real Analysis") won't match the code regex
        # above -- try resolving it against the real modules table
        name_match = match_module_by_name(question, context.get("edition_id"))
        if name_match:
            module_code = name_match
            module_named_explicitly = True

    programme_code = match_programme_code(question, known_codes)
    if not programme_code:
        # a spoken name ("computer science and mathematics") won't match the code regex above
        programme_code = match_programme_by_name(question, context.get("edition_id"))

    intent = detect_intent_keywords(question)

    # the combination check overrides a generic "modules" match -- e.g.
    if is_failure_impact_question(question):
        intent = "failure_impact"

    # same reasoning: "which modules are registered" / "check my registration" would otherwise
    # be caught by list_modules or prerequisites' generic keywords
    if is_registration_check_question(question):
        intent = "registration_check"

    # the student asking about THEIR OWN situation -- answered from their academic record, not
    # just the handbook.
    if intent not in ("failure_impact", "registration_check"):
        if module_code and is_personal_module_question(question):
            intent = "module_eligibility"
        elif not module_code and is_progress_question(question):
            intent = "academic_progress"

    # a code shaped like "4BSC21" can be a module code or a programme code -- only guess
    # module_lookup when programme_code didn't also match
    if module_code and not intent and not programme_code:
        intent = "module_lookup"

    # a generic word like "modules" can win the keyword match even when the question actually
    # names one specific module by name
    if module_named_explicitly and intent == "list_modules":
        intent = "module_lookup"

    if intent == "list_by_type":
        return {"intent": intent, "programme_code": None, "module_code": None,
                "programme_type": match_programme_type_keyword(question)}

    if intent and (module_code or programme_code or intent in ("registration_check", "academic_progress")):
        result = {"intent": intent, "programme_code": programme_code,
                  "module_code": module_code, "programme_type": None}
    else:
        result = classify_with_claude(question, known_codes, context)
        if not result.get("programme_code"):
            result["programme_code"] = programme_code
        if not result.get("module_code"):
            result["module_code"] = module_code

    # if this question genuinely didn't resolve a code on its own, fall back to whatever was
    # last discussed
    if not result.get("programme_code"):
        result["programme_code"] = context.get("last_programme_code")
    if not result.get("module_code"):
        result["module_code"] = context.get("last_module_code")

    return result


def get_modules_for_programme(programme_code, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT m.module_code, m.module_name, m.credit_value, m.year, m.semester,
               pm.effective_type, pm.elective_group, pm.choose_count
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND pm.handbook_edition_id = %s
        ORDER BY m.year, m.semester
    """, (programme_code, require_edition(edition_id)))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_electives_for_programme(programme_code, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT m.module_code, m.module_name, m.year, m.semester,
               pm.elective_group, pm.choose_count
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND pm.handbook_edition_id = %s
          AND pm.effective_type = 'Elective'
        ORDER BY m.year, m.semester, pm.elective_group
    """, (programme_code, require_edition(edition_id)))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_majors_compulsory_for_programme(programme_code, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT m.module_code, m.module_name, m.year, m.semester, pm.effective_type
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND pm.handbook_edition_id = %s
          AND pm.effective_type IN ('Major', 'Compulsory')
        ORDER BY m.year, m.semester
    """, (programme_code, require_edition(edition_id)))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_module_by_code(module_code, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT module_code, module_name, credit_value, nqf_level, year, semester,
               prerequisite_subjects, corequisite_subjects
        FROM modules WHERE module_code = %s AND handbook_edition_id = %s
    """, (module_code, require_edition(edition_id)))
    row = cur.fetchone()
    cur.close()
    return row


def get_credits_total(programme_code, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT m.year, m.semester, m.credit_value, pm.elective_group, pm.choose_count
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND pm.handbook_edition_id = %s
    """, (programme_code, require_edition(edition_id)))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_module_count_data(programme_code, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT m.year, m.semester, pm.elective_group, pm.choose_count
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND pm.handbook_edition_id = %s
    """, (programme_code, require_edition(edition_id)))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_programme_by_code(programme_code, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT programme_code, programme_name, programme_type, faculty, departments,
               degree_designator, minimum_duration_years, total_credits_to_graduate,
               intake, readmission_policy
        FROM programme_info WHERE programme_code = %s AND handbook_edition_id = %s
    """, (programme_code, require_edition(edition_id)))
    row = cur.fetchone()
    cur.close()
    return row


def get_programmes_by_type(programme_type, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT programme_code, programme_name, programme_type
        FROM programme_info WHERE programme_type = %s AND handbook_edition_id = %s
        ORDER BY programme_code
    """, (programme_type, require_edition(edition_id)))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_admission_requirements(programme_code, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT subject_name, min_percentage, min_level
        FROM programme_admission_requirements
        WHERE programme_code = %s AND handbook_edition_id = %s
        ORDER BY id
    """, (programme_code, require_edition(edition_id)))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_minimum_credits_for_admission(programme_code, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT minimum_credits_for_admission
        FROM programme_info
        WHERE programme_code = %s AND handbook_edition_id = %s
    """, (programme_code, require_edition(edition_id)))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


def get_module_info_in_programme(programme_code, module_code, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT m.year, m.credit_value, pm.effective_type, pm.elective_group, pm.choose_count
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND m.module_code = %s AND pm.handbook_edition_id = %s
    """, (programme_code, module_code, require_edition(edition_id)))
    row = cur.fetchone()
    cur.close()
    return row



# Registration verification: checking a student's registration against what a specific
# curriculum year actually requires

def get_expected_modules_for_year(programme_code, year, *, edition_id):
    # Everything expected at a given curriculum year for a programme.
    cur = conn.cursor()
    cur.execute("""
        SELECT m.module_code, m.module_name, m.credit_value,
               pm.effective_type, pm.elective_group, pm.choose_count
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND COALESCE(pm.expected_year, m.year) = %s
          AND pm.handbook_edition_id = %s
    """, (programme_code, year, require_edition(edition_id)))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_effective_years_for_modules(programme_code, module_codes, *, edition_id):
    # Looks up each given module's real curriculum year within this one programme, independent
    # of the order the codes appear in on a registration document.
    if not module_codes:
        return {}
    cur = conn.cursor()
    cur.execute("""
        SELECT m.module_code, COALESCE(pm.expected_year, m.year) AS effective_year
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND m.module_code = ANY(%s)
          AND pm.handbook_edition_id = %s
    """, (programme_code, module_codes, require_edition(edition_id)))
    rows = cur.fetchall()
    cur.close()
    return {code: year for code, year in rows}


def group_registered_modules_by_year(programme_code, registered_module_codes, *, edition_id):
    # Groups a registration's module codes by which curriculum year they actually belong to.
    years = get_effective_years_for_modules(programme_code, registered_module_codes, edition_id=edition_id)
    grouped = {}
    unrecognised = []
    for code in registered_module_codes:
        year = years.get(code)
        if year is None:
            unrecognised.append(code)
        else:
            grouped.setdefault(year, []).append(code)
    return grouped, unrecognised


def check_year_completeness(programme_code, year, registered_codes_for_year, *, edition_id):
    # Compares what's registered for one curriculum year against what that year actually
    # requires: every compulsory/major module
    expected_rows = get_expected_modules_for_year(programme_code, year, edition_id=edition_id)
    registered = set(registered_codes_for_year)

    missing_compulsory = []
    elective_groups = {}

    for module_code, module_name, credit_value, effective_type, elective_group, choose_count in expected_rows:
        if elective_group:
            g = elective_groups.setdefault(elective_group, {
                "choose_count": choose_count or 1, "options": [], "chosen": []
            })
            g["options"].append((module_code, module_name))
            if module_code in registered:
                g["chosen"].append((module_code, module_name))
        elif effective_type in ("Major", "Compulsory") and module_code not in registered:
            missing_compulsory.append((module_code, module_name))

    unsatisfied_electives = [
        {"group": group_name, "expected_count": info["choose_count"],
         "found_count": len(info["chosen"]), "options": info["options"],
         "chosen": info["chosen"]}
        for group_name, info in elective_groups.items()
        if len(info["chosen"]) < info["choose_count"]
    ]

    return {"year": year, "missing_compulsory": missing_compulsory,
            "unsatisfied_electives": unsatisfied_electives}


def resolve_missing_against_academic_record(missing_modules, academic_record_passes):
    # Splits modules that appear missing from a registration into two groups
    resolved, still_missing = [], []
    for module_code, module_name in missing_modules:
        if academic_record_passes and module_code in academic_record_passes:
            resolved.append((module_code, module_name))
        else:
            still_missing.append((module_code, module_name))
    return resolved, still_missing


def check_full_registration(programme_code, registered_module_codes, academic_record_passes=None, *, edition_id):
    # The complete registration check: groups a registration by the real curriculum year each
    # module belongs to
    grouped, unrecognised = group_registered_modules_by_year(programme_code, registered_module_codes, edition_id=edition_id)

    year_results = []
    for year, codes in sorted(grouped.items()):
        check = check_year_completeness(programme_code, year, codes, edition_id=edition_id)
        resolved, still_missing = resolve_missing_against_academic_record(
            check["missing_compulsory"], academic_record_passes
        )
        check["resolved_compulsory"] = resolved
        check["missing_compulsory"] = still_missing

        # electives get the same treatment as compulsory modules
        resolved_groups, still_unsatisfied = [], []
        for g in check["unsatisfied_electives"]:
            chosen_codes = {c for c, n in g["chosen"]}
            passed_earlier = [(c, n) for c, n in g["options"]
                              if academic_record_passes and c in academic_record_passes
                              and c not in chosen_codes]
            g["passed_earlier"] = passed_earlier
            if g["found_count"] + len(passed_earlier) >= g["expected_count"]:
                resolved_groups.append(g)
            else:
                still_unsatisfied.append(g)
        check["resolved_electives"] = resolved_groups
        check["unsatisfied_electives"] = still_unsatisfied
        year_results.append(check)

    return {
        "programme_code": programme_code,
        "edition_id": edition_id,
        "years_covered": sorted(grouped.keys()),
        "year_results": year_results,
        "unrecognised_modules": unrecognised,
        "academic_record_used": academic_record_passes is not None,
    }


def check_registration_from_context(context, fallback_programme_code=None):
    # Ties a cached, already-extracted proof of registration (and academic record, if one was
    # also uploaded) to check_full_registration().
    por = get_cached_document(context, "proof_of_registration")
    if not por or not por.get("modules"):
        return {"status": "needs_por"}

    edition_id = get_valid_edition_id(context)
    if edition_id is None:
        return {"status": "needs_edition", "message": build_edition_question()}

    programme_code = por.get("programme_code") or fallback_programme_code
    if not programme_code:
        return {"status": "needs_programme"}

    # normalise before matching: "4cps231", " 4CPS231 " and "4CPS 231"
    # must all match 4CPS231; duplicates (e.g. from multi-page uploads) removed
    registered_codes = []
    for m in por["modules"]:
        if m.get("is_cancelled"):
            continue
        code = re.sub(r"\s+", "", str(m.get("module_code") or "")).upper()
        if code and code not in registered_codes:
            registered_codes.append(code)

    academic_record = get_cached_document(context, "academic_record")
    academic_record_passes = build_academic_record_passes(academic_record) if academic_record else None

    result = check_full_registration(programme_code, registered_codes, academic_record_passes,
                                     edition_id=edition_id)
    result["academic_record_low_confidence"] = bool(
        academic_record and academic_record.get("extraction_status") == "low_confidence")
    return {"status": "ok", "result": result}


# Personal answers from the student's own academic record.

def _norm_code(code):
    return re.sub(r"\s+", "", str(code or "")).upper()


def get_academic_history(context):
    # The cached academic record as
    record = get_cached_document(context, "academic_record")
    if not record or not record.get("years"):
        return None
    history = {}
    for year in record["years"]:
        for m in year.get("modules", []):
            code = _norm_code(m.get("module_code"))
            if not code:
                continue
            history.setdefault(code, []).append({
                "academic_year": year.get("academic_year"),
                "period": m.get("academic_period"),
                "year_mark": m.get("year_mark"),
                "final_mark": m.get("final_mark"),
                "result": (m.get("result") or "").strip().upper() or None,
            })
    for attempts in history.values():
        attempts.sort(key=lambda a: (a["academic_year"] or 0))
    return history


def module_status(history, module_code):
    # 'passed' | 'failed' (attempted, never passed) | 'cancelled' (only ever cancelled) |
    # 'no_result' (attempt with no result yet) | 'not_taken'.
    attempts = (history or {}).get(module_code, [])
    if not attempts:
        return "not_taken"
    results = [a["result"] or "" for a in attempts]
    if any(r.startswith("PASS") for r in results):
        return "passed"
    if any(r.startswith(("FAIL", "ABSENT", "NOT ADMITTED")) for r in results):
        return "failed"
    if results and all("CANCEL" in r for r in results):
        return "cancelled"
    return "no_result"


def get_registered_now(context):
    # Module codes on the current proof of registration (not cancelled).
    por = get_cached_document(context, "proof_of_registration")
    if not por:
        return set()
    return {_norm_code(m.get("module_code")) for m in por.get("modules", []) if not m.get("is_cancelled")}


def resolve_personal_programme(programme_code, context):
    # The programme for a personal question: named in the question, else from the proof of
    # registration, else the last one discussed.
    por = get_cached_document(context, "proof_of_registration") or {}
    return programme_code or por.get("programme_code") or context.get("last_programme_code")


def get_programme_curriculum(programme_code, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT m.module_code, m.module_name, m.credit_value, m.year, m.semester,
               COALESCE(pm.expected_year, m.year) AS effective_year,
               pm.effective_type, pm.elective_group, pm.choose_count
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND pm.handbook_edition_id = %s
        ORDER BY effective_year, m.semester, m.module_code
    """, (programme_code, require_edition(edition_id)))
    rows = cur.fetchall()
    cur.close()
    return rows


def get_elective_group_options(programme_code, elective_group, effective_year, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT m.module_code, m.module_name, pm.choose_count
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND pm.elective_group = %s
          AND COALESCE(pm.expected_year, m.year) = %s AND pm.handbook_edition_id = %s
        ORDER BY m.module_code
    """, (programme_code, elective_group, effective_year, require_edition(edition_id)))
    rows = cur.fetchall()
    cur.close()
    return rows


def check_module_eligibility(programme_code, module_code, context, *, edition_id):
    # Everything needed to answer "should I / can I do <module>?" for THIS student: their own
    # attempts at it, each prerequisite's status in their record
    history = get_academic_history(context)
    registered_now = get_registered_now(context)
    module = get_module_by_code(module_code, edition_id=edition_id)
    if not module:
        return {"found": False, "module_code": module_code}
    code, name, credit, nqf, year, sem, prereqs, coreqs = module

    result = {
        "found": True, "module_code": code, "module_name": name, "credits": credit,
        "year": year, "semester": sem, "programme_code": programme_code,
        "record_available": history is not None,
        "own_status": module_status(history, code),
        "own_attempts": (history or {}).get(code, []),
        "registered_now": code in registered_now,
        "prerequisites": [(p, module_status(history, p)) for p in (prereqs or [])],
        "corequisites": [(c, module_status(history, c), c in registered_now) for c in (coreqs or [])],
        "in_programme": None, "effective_type": None, "group": None,
    }

    if programme_code:
        cur = conn.cursor()
        cur.execute("""
            SELECT pm.effective_type, pm.elective_group, pm.choose_count,
                   COALESCE(pm.expected_year, m.year)
            FROM programme_modules pm JOIN modules m ON pm.module_id = m.module_id
            WHERE pm.programme_code = %s AND m.module_code = %s AND pm.handbook_edition_id = %s
        """, (programme_code, code, require_edition(edition_id)))
        placements = cur.fetchall()
        cur.close()
        result["in_programme"] = bool(placements)
        if placements:
            # a module can be listed twice (e.g. compulsory AND in a group) --
            # compulsory/major wins, since it then has to be done regardless
            placements.sort(key=lambda r: 0 if r[0] in ("Major", "Compulsory") else 1)
            etype, group, choose, eff_year = placements[0]
            result["effective_type"] = etype
            result["curriculum_year"] = eff_year
            if group and etype not in ("Major", "Compulsory"):
                options = get_elective_group_options(programme_code, group, eff_year, edition_id=edition_id)
                compulsory_codes = {r[0] for r in get_programme_curriculum(programme_code, edition_id=edition_id)
                                    if r[6] in ("Major", "Compulsory")}
                # a module the handbook lists as compulsory AND as a group option is needed
                # anyway, so it can't be what covers the elective group
                others = [(c, n) for c, n, _ in options if c != code and c not in compulsory_codes]
                result["group"] = {
                    "name": group, "choose_count": choose or 1,
                    "other_options": others,
                    "others_passed": [(c, n) for c, n in others if module_status(history, c) == "passed"],
                    "others_registered_now": [(c, n) for c, n in others if c in registered_now],
                }
    return result


def check_academic_progress(programme_code, context, *, edition_id):
    # The student's record laid against their programme's curriculum: per curriculum year, which
    # compulsory modules and elective groups are done
    history = get_academic_history(context) or {}
    registered_now = get_registered_now(context)
    rows = get_programme_curriculum(programme_code, edition_id=edition_id)
    in_programme = {r[0] for r in rows}
    compulsory_codes = {r[0] for r in rows if r[6] in ("Major", "Compulsory")}

    years = {}
    groups = {}
    total_required = 0
    credits_passed = 0
    seen_group_keys = set()
    for code, name, credit, year, sem, eff_year, etype, group, choose in rows:
        y = years.setdefault(eff_year, {"year": eff_year, "passed": [], "registered_now": [],
                                        "failed": [], "not_taken": [], "groups": []})
        if group and etype not in ("Major", "Compulsory"):
            key = (eff_year, group)
            g = groups.get(key)
            if not g:
                g = {"name": group, "choose_count": choose or 1, "options": [], "passed": [],
                     "registered_now": [], "failed": [], "credit": credit}
                groups[key] = g
                y["groups"].append(g)
            g["options"].append((code, name))
            if code in compulsory_codes:
                # listed as compulsory too -- counted there, not towards the group
                continue
            st = module_status(history, code)
            if st == "passed":
                g["passed"].append((code, name))
            elif code in registered_now:
                g["registered_now"].append((code, name))
            elif st == "failed":
                g["failed"].append((code, name))
            # credits: same de-duplication as summarize_credits()
            if (year, sem, group) not in seen_group_keys:
                seen_group_keys.add((year, sem, group))
                total_required += credit * (choose or 1)
            continue

        total_required += credit
        st = module_status(history, code)
        if st == "passed":
            y["passed"].append((code, name))
            credits_passed += credit
        elif code in registered_now:
            y["registered_now"].append((code, name))
        elif st == "failed":
            y["failed"].append((code, name))
        else:
            y["not_taken"].append((code, name))

    for g in groups.values():
        credits_passed += g["credit"] * min(len(g["passed"]), g["choose_count"])

    failed_not_passed = sorted(c for c in history if module_status(history, c) == "failed")
    passed_outside = sorted(c for c in history
                            if module_status(history, c) == "passed" and c not in in_programme)
    return {
        "programme_code": programme_code, "record_available": bool(history),
        "years": [years[k] for k in sorted(years)],
        "credits_passed": credits_passed, "credits_required": total_required,
        "failed_not_yet_passed": failed_not_passed,
        "passed_outside_programme": passed_outside,
        "record_years": sorted({a["academic_year"] for att in history.values() for a in att
                                if a["academic_year"]}),
    }


# Document extraction: proof of registration and academic record uploads.
# Extracted results are cached on `context` for the rest of the session

def _read_file_as_base64(path):
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def _guess_media_type(path):
    ext = os.path.splitext(path)[1].lower()
    return {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(ext, "application/octet-stream")


def _parse_json_reply(raw):
    # Pulls the JSON object out of Claude's reply even when it is wrapped in ```json fences or
    # has a sentence before/after it ("Here is the extracted data
    text = (raw or "").strip()
    if not text:
        raise ValueError("Claude returned an empty reply")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start == -1:
        raise ValueError(f"Claude's reply contained no JSON. It started with: {text[:120]!r}")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def _claude_extract_document(file_path, prompt, max_tokens=1500):
    # Sends a PDF or image directly to Claude for structured extraction.
    media_type = _guess_media_type(file_path)
    data = _read_file_as_base64(file_path)
    block_type = "document" if media_type == "application/pdf" else "image"

    last_error = None
    for attempt in range(2):
        text_prompt = prompt if attempt == 0 else (
            prompt + "\n\nYour previous reply could not be read as JSON. Reply with the "
                     "JSON object ONLY -- start with { and end with }, no other text."
        )
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {"type": block_type, "source": {"type": "base64", "media_type": media_type, "data": data}},
                    {"type": "text", "text": text_prompt},
                ],
            }],
        )
        if getattr(response, "stop_reason", None) == "max_tokens":
            # a cut-off reply is never valid JSON -- retrying the same limit won't help
            raise ValueError(f"Claude's reply was cut off at max_tokens={max_tokens}; "
                             "the document needs a higher limit")
        try:
            return _parse_json_reply(extract_text(response))
        except ValueError as e:
            last_error = e
            print(f"[extract] attempt {attempt + 1} for {file_path} gave unreadable output: {e}")
    raise last_error


def _pdfplumber_extract_text(file_path):
    # Returns extracted text if this PDF has a real text layer, or None if it's essentially
    # empty -- a scanned/photographed PDF
    import pdfplumber
    text = ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text += (page.extract_text() or "") + "\n"
    return text if len(text.strip()) >= 20 else None


def _ocr_extract_text(file_path, is_pdf=False):
    # OCR fallback- always used for images, and for PDFs where pdfplumber found no real text
    # layer.
    import pytesseract
    from PIL import Image

    def _prepare(img):
        img = img.convert("RGB")
        if img.width < 1200:
            scale = 1200 / img.width
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        return img

    if not is_pdf:
        return pytesseract.image_to_string(_prepare(Image.open(file_path)))

    import fitz  # PyMuPDF -- used here only to rasterize pages for OCR
    doc = fitz.open(file_path)
    text = ""
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        tmp_path = f"{file_path}.page{page.number}.png"
        pix.save(tmp_path)
        text += pytesseract.image_to_string(_prepare(Image.open(tmp_path))) + "\n"
        os.remove(tmp_path)
    return text


def _parse_por_text_fallback(text):
    # Best-effort regex parse of raw POR text -- only reached when Claude's own extraction call
    # failed.
    modules = []
    for line in text.splitlines():
        m = MODULE_CODE_PATTERN.search(line.upper())
        if not m:
            continue
        code = m.group(1)
        rest = line.upper().split(code, 1)[-1][:20]
        is_cancelled = bool(re.search(r"\bY\b", rest))
        modules.append({"module_code": code, "is_cancelled": is_cancelled})
    return {"programme_code": None, "modules": modules}


# Backup-parser patterns for academic records.
AR_YEAR_HEADER = re.compile(
    r"^\s*(?:academic\s+)?(?:year|period)\s*(?:of\s+study)?\s*[:\-]?\s*((?:19|20)\d{2})\b"
    r"|^\s*((?:19|20)\d{2})\s*(?:academic\s+year)?\s*$",
    re.IGNORECASE)
AR_ANNUAL_RESULT = re.compile(r"annual\s+result\s*[:\-]?\s*(.+)", re.IGNORECASE)
AR_YEAR_IN_ANNUAL_LINE = re.compile(r"\byear\s*[:\-]?\s*((?:19|20)\d{2})\b", re.IGNORECASE)
AR_PERIOD = re.compile(
    r"\b(semester\s*[12]|first\s+semester|second\s+semester|year\s+module|full\s+year|"
    r"re-?\s*exam(?:ination)?|supplementary(?:\s+exam(?:ination)?)?|special\s+exam(?:ination)?|"
    r"block\s*\d|term\s*\d|quarter\s*\d)\b", re.IGNORECASE)
AR_YEAR_MARK = re.compile(r"\byear\s*mark\s*[:\-]?\s*(\d{1,3})\b", re.IGNORECASE)
AR_FINAL_MARK = re.compile(r"\b(?:final|exam(?:ination)?)\s*mark\s*[:\-]?\s*(\d{1,3})\b", re.IGNORECASE)
AR_RESULT_LABEL = re.compile(r"(?<![A-Za-z])(?<!annual )(?<!annual\s)result\s*[:\-]\s*(.+)", re.IGNORECASE)
AR_RESULT_PHRASE = re.compile(
    r"\b(pass(?:ed)?(?:\s+with\s+(?:merit|distinction))?|"
    r"fail(?:ed)?(?:[\s\-:]+(?:subminimum|sub-minimum|re-?\s*exam|supplementary|on|allowed|examination|exam|not|admitted|year|mark|\w+)){0,6}|"
    r"absent(?:\s+from\s+exam(?:ination)?)?|"
    r"(?:subject\s+)?cancell?ed(?:\s+by\s+\w+)?|"
    r"exempt(?:ed|ion)?|credit\s+granted|deferred(?:\s+exam(?:ination)?)?|incomplete|"
    r"not\s+admitted(?:\s+to\s+exam(?:ination)?)?)\b",
    re.IGNORECASE)


def _clean_result(text):
    return re.sub(r"\s+", " ", text).strip(" .,:;-").upper() or None


def _parse_academic_record_text_fallback(text):
    # Best-effort parse of raw academic record text (fallback tier only -- used when Claude's
    # extraction fails; still less reliable than Claude
    years = []
    current_year = None
    current_module = None

    def year_entry(y):
        for entry in years:
            if entry["academic_year"] == y:
                return entry
        entry = {"academic_year": y, "annual_result_code": None,
                 "annual_result_description": None, "modules": []}
        years.append(entry)
        return entry

    def finish_module():
        nonlocal current_module
        if current_module is None:
            return
        chunk = " ".join(current_module.pop("_lines"))
        m = current_module
        after_code = chunk.split(m["module_code"], 1)[-1]

        period = AR_PERIOD.search(chunk)
        if period:
            m["academic_period"] = re.sub(r"\s+", " ", period.group(1)).title()

        labelled = AR_RESULT_LABEL.search(chunk)
        if labelled:
            phrase = AR_RESULT_PHRASE.search(labelled.group(1))
            m["result"] = _clean_result(phrase.group(1) if phrase else labelled.group(1)[:60])
        else:
            phrase = AR_RESULT_PHRASE.search(after_code)
            if phrase:
                m["result"] = _clean_result(phrase.group(1))
        if m["result"] and "CANCEL" in m["result"]:
            m["result"] = "CANCELLED"

        ym = AR_YEAR_MARK.search(chunk)
        fm = AR_FINAL_MARK.search(chunk)
        if ym:
            m["year_mark"] = int(ym.group(1))
        if fm:
            m["final_mark"] = int(fm.group(1))

        no_marks_expected = bool(m["result"]) and any(
            w in m["result"] for w in ("CANCEL", "ABSENT", "EXEMPT", "CREDIT", "DEFERRED", "NOT ADMITTED"))
        if not ym and not fm and not no_marks_expected:
            # table layout: marks are the bare numbers between the period and the result
            rest = after_code
            p_in_rest = AR_PERIOD.search(rest)
            if p_in_rest:
                rest = rest[p_in_rest.end():]
            phrase = AR_RESULT_PHRASE.search(rest)
            between = rest[:phrase.start()] if phrase else rest
            nums = [int(n) for n in re.findall(r"(?<![\w.])(\d{1,3})(?![\w.%])", between)
                    if 0 <= int(n) <= 100]
            if len(nums) >= 2:
                m["year_mark"], m["final_mark"] = nums[-2], nums[-1]
            elif len(nums) == 1:
                m["final_mark"] = nums[0]

        target = year_entry(m.pop("_year"))
        target["modules"].append(m)
        current_module = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        annual = AR_ANNUAL_RESULT.search(line)
        header = AR_YEAR_HEADER.search(line)
        if header or annual:
            y = None
            if header:
                y = int(header.group(1) or header.group(2))
            elif annual:
                ym = AR_YEAR_IN_ANNUAL_LINE.search(line)
                y = int(ym.group(1)) if ym else None
            if y is not None:
                finish_module()
                current_year = y
                year_entry(y)
            if annual and current_year is not None:
                finish_module()
                desc = re.sub(r"\s+year\s*[:\-]?\s*(?:19|20)\d{2}\b.*$", "", annual.group(1),
                              flags=re.IGNORECASE).strip()
                entry = year_entry(current_year)
                entry["annual_result_description"] = desc
                code_m = re.match(r"([A-Z]{1,4})\s*[-:]", desc)
                if code_m:
                    entry["annual_result_code"] = code_m.group(1)
            # a header line can also carry a module (rare) -- fall through only if so
            if not MODULE_CODE_PATTERN.search(line.upper()):
                continue

        codes = MODULE_CODE_PATTERN.findall(line.upper())
        if codes and current_year is not None:
            # every module code starts a new entry; several codes on one
            # line (a dense table row per module) are split between them
            upper = line.upper()
            positions = [upper.index(c) for c in codes]
            for i, code in enumerate(codes):
                finish_module()
                end = positions[i + 1] if i + 1 < len(codes) else len(line)
                current_module = {"module_code": code, "academic_period": None, "year_mark": None,
                                  "final_mark": None, "result": None,
                                  "_year": current_year, "_lines": [line[positions[i]:end]]}
        elif current_module is not None:
            current_module["_lines"].append(line)

    finish_module()
    return {"years": [y for y in years if y["modules"] or y["annual_result_description"]]}


def extract_document_with_fallback(file_path, prompt, parser_fallback, is_pdf, max_tokens=1500):
    # Tries Claude first.
    try:
        data = _claude_extract_document(file_path, prompt, max_tokens=max_tokens)
        return data, "ok"
    except Exception as e:
        print(f"[extract] Claude extraction failed for {file_path}: {e}")

    text = None
    if is_pdf:
        text = _pdfplumber_extract_text(file_path)
        if text is None:
            text = _ocr_extract_text(file_path, is_pdf=True)
    else:
        text = _ocr_extract_text(file_path, is_pdf=False)

    if not text or len(text.strip()) < 10:
        return None, "failed"

    return parser_fallback(text), "low_confidence"


def get_cached_document(context, doc_type):
    return context.get("documents", {}).get(doc_type)


def cache_document(context, doc_type, data):
    context.setdefault("documents", {})[doc_type] = data


def extract_proof_of_registration(file_paths, context=None):
    # Extracts a proof of registration from one or more files.
    context = context if context is not None else {}

    all_modules, failed_files, low_confidence_files = [], [], []
    programme_code = None

    for path in file_paths:
        is_pdf = path.lower().endswith(".pdf")
        data, status = extract_document_with_fallback(
            path, POR_EXTRACTION_PROMPT, _parse_por_text_fallback, is_pdf
        )
        # a call can succeed mechanically (no exception, valid JSON) while still extracting
        # nothing meaningful
        if data is None or (not data.get("programme_code") and not data.get("modules")):
            failed_files.append(path)
            continue
        if status == "low_confidence":
            low_confidence_files.append(path)
        if data.get("programme_code"):
            programme_code = data["programme_code"]
        all_modules.extend(data.get("modules", []))

    result = {"programme_code": programme_code, "modules": all_modules, "failed_files": failed_files,
              "extraction_status": "low_confidence" if low_confidence_files else "ok"}
    if not failed_files:
        cache_document(context, "proof_of_registration", result)
    return result


def extract_academic_record(file_paths, context=None):
    # Extracts an academic record from one or more files.
    context = context if context is not None else {}

    years, failed_files, low_confidence_files = [], [], []

    for path in file_paths:
        is_pdf = path.lower().endswith(".pdf")
        data, status = extract_document_with_fallback(
            path, ACADEMIC_RECORD_EXTRACTION_PROMPT, _parse_academic_record_text_fallback, is_pdf,
            max_tokens=16000,  # a multi-year record (every module, re-exams, cancellations) needs a lot of room
        )
        # same reasoning as extract_proof_of_registration: a mechanically
        # successful call that extracted zero years is still a real failure
        if data is None or not data.get("years"):
            failed_files.append(path)
            continue
        if status == "low_confidence":
            low_confidence_files.append(path)
        years.extend(data["years"])

    result = {"years": years, "failed_files": failed_files,
              # "ok" = read by Claude; "low_confidence" = the backup parser
              # was used for at least one file, so the record may be incomplete
              "extraction_status": "low_confidence" if low_confidence_files else "ok"}
    if not failed_files:
        cache_document(context, "academic_record", result)
    return result


def build_academic_record_passes(academic_record):
    # Turns an extracted academic record into the flat set of module codes with a confirmed
    # PASS-prefixed result
    passes = set()
    for year in academic_record.get("years", []):
        for module in year.get("modules", []):
            result = (module.get("result") or "").strip().upper()
            code = re.sub(r"\s+", "", str(module.get("module_code") or "")).upper()
            if code and result.startswith("PASS"):
                passes.add(code)
    return passes


def get_all_known_module_codes(edition_id=None):
    # All module codes in one handbook edition, or across every edition if edition_id is None.
    cur = conn.cursor()
    if edition_id is not None:
        cur.execute("SELECT DISTINCT module_code FROM modules WHERE handbook_edition_id = %s", (edition_id,))
    else:
        cur.execute("SELECT DISTINCT module_code FROM modules")
    codes = {r[0] for r in cur.fetchall()}
    cur.close()
    return codes


def validate_extracted_modules(modules, known_module_codes):
    # Checks each extracted module code against the real modules table.
    valid, unrecognised = [], []
    for m in modules:
        code = (m.get("module_code") or "").upper()
        if code in known_module_codes:
            valid.append(m)
        else:
            unrecognised.append(m)
    return valid, unrecognised


def get_modules_blocked_by(programme_code, module_code, *, edition_id):
    # Every module in this programme that lists module_code as a prerequisite.
    cur = conn.cursor()
    cur.execute("""
        SELECT m.module_code, m.module_name, pm.effective_type, pm.elective_group, pm.choose_count
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND %s = ANY(m.prerequisite_subjects)
          AND pm.handbook_edition_id = %s
    """, (programme_code, module_code, require_edition(edition_id)))
    rows = cur.fetchall()
    cur.close()
    return rows


def count_elective_group_alternatives(programme_code, elective_group, exclude_module_code, *, edition_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*)
        FROM programme_modules pm
        JOIN modules m ON pm.module_id = m.module_id
        WHERE pm.programme_code = %s AND pm.elective_group = %s AND m.module_code != %s
          AND pm.handbook_edition_id = %s
    """, (programme_code, elective_group, exclude_module_code, require_edition(edition_id)))
    count = cur.fetchone()[0]
    cur.close()
    return count


def check_module_failure_impact(programme_code, module_code, *, edition_id):
    # Single-hypothetical-failure impact check: assumes everything else in the programme is
    # passed
    info = get_module_info_in_programme(programme_code, module_code, edition_id=edition_id)
    if not info:
        return {"found": False}

    year, credit_value, effective_type, elective_group, choose_count = info

    result = {
        "found": True,
        "module_code": module_code,
        "year": year,
        "effective_type": effective_type,
        "blocked_modules": [],
        "at_risk_electives": [],
        "safe_electives": [],
    }

    # Downstream blocking check - one hop only
    dependents = get_modules_blocked_by(programme_code, module_code, edition_id=edition_id)
    for dep_code, dep_name, dep_effective_type, dep_group, dep_choose in dependents:
        if dep_effective_type in ("Major", "Compulsory") or not dep_group:
            result["blocked_modules"].append((dep_code, dep_name))
        else:
            alternatives = count_elective_group_alternatives(programme_code, dep_group, dep_code,
                                                             edition_id=edition_id)
            if alternatives >= (dep_choose or 1):
                result["safe_electives"].append((dep_code, dep_name, dep_group))
            else:
                result["at_risk_electives"].append((dep_code, dep_name, dep_group))

    return result


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


def format_structured_answer(question, data_summary, edition=None):
    handbook_note = ""
    if edition:
        handbook_note = (
            f"\nAll of this data comes from {edition_display(edition)}. Start the "
            f"answer by saying, in a few words, that it is based on the "
            f"{edition['year']} handbook.\n"
        )

    prompt = f"""Answer the student's question using ONLY the data below.
Do not add, infer, or guess anything not present in this data.
{handbook_note}
Answer directly and specifically what was asked -- don't just recite
every line of the data if only part of it is relevant to the question.

If the data contains a VERDICT line, give that verdict clearly and first --
it was worked out from the student's own academic record and the handbook.
Speak to the student directly ("you have passed...", "you still need...").

Do NOT end with a question, an offer of further help, or suggestions of
what to ask next -- one follow-up suggestion is added separately after
your answer.

Question: "{question}"

DATA:
{data_summary}"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return extract_text(response)


# Follow-up suggestion: after every real data answer, ONE next question is suggested.

SUGGESTION_QUESTIONS = {
    "list_modules": "What are all the modules in {prog}?",
    "list_electives": "What are the elective modules for {prog}?",
    "majors_compulsory": "What are the major and compulsory modules for {prog}?",
    "credits_total": "How many credits do I need to graduate from {prog}?",
    "module_count": "How many modules do I take to complete {prog}?",
    "admission_requirements": "What are the admission requirements for {prog}?",
    "programme_lookup": "Tell me about {prog} (faculty, department and duration).",
    "module_lookup": "What are the details and prerequisites for {mod}?",
    "failure_impact": "Which modules in {prog} would be blocked if I fail {mod}?",
    "module_eligibility": "Based on my academic record, should I be doing {mod}?",
    "academic_progress": "Based on my academic record, what do I still need to complete in {prog}?",
}
SUGGESTION_OFFERS = {
    "list_modules": "Would you like to see all the modules in {prog}?",
    "list_electives": "Would you like to see the elective modules for {prog}?",
    "majors_compulsory": "Would you like to see the major and compulsory modules for {prog}?",
    "credits_total": "Would you like to know how many credits you need to graduate from {prog}?",
    "module_count": "Would you like to know how many modules you take to complete {prog}?",
    "admission_requirements": "Would you like to see the admission requirements for {prog}?",
    "programme_lookup": "Would you like an overview of {prog}, such as its faculty and duration?",
    "module_lookup": "Would you like to see the details and prerequisites for {mod}?",
    "failure_impact": "Would you like to know which modules in {prog} would be blocked if you fail {mod}?",
    "module_eligibility": "Would you like me to check your academic record to see whether you can register for {mod}?",
    "academic_progress": "Would you like me to check your academic record to see what you still need to complete in {prog}?",
}
# for each answered intent: candidate next intents, in order of preference
NEXT_SUGGESTIONS = {
    "list_modules": ["list_electives", "credits_total", "admission_requirements"],
    "list_electives": ["majors_compulsory", "credits_total", "list_modules"],
    "majors_compulsory": ["list_electives", "credits_total", "list_modules"],
    "credits_total": ["module_count", "list_modules", "list_electives"],
    "module_count": ["credits_total", "list_modules", "list_electives"],
    "admission_requirements": ["list_modules", "programme_lookup", "credits_total"],
    "programme_lookup": ["admission_requirements", "list_modules", "credits_total"],
    "module_lookup": ["module_eligibility", "failure_impact", "list_modules"],
    "prerequisites": ["module_eligibility", "failure_impact", "list_modules"],
    "failure_impact": ["module_eligibility", "module_lookup", "list_modules"],
    "registration_check": ["academic_progress", "module_lookup", "list_electives", "credits_total"],
    "module_eligibility": ["academic_progress", "failure_impact", "list_electives"],
    "academic_progress": ["list_electives", "credits_total", "list_modules"],
    "list_by_type": [],
}
YES_WORDS = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please", "definitely",
             "absolutely", "certainly", "y", "ya", "yebo"}
NO_WORDS = {"no", "nope", "nah", "n", "cha"}
YES_NO_FILLER = {"please", "thanks", "thank", "you", "go", "ahead", "do", "it", "that",
                 "sounds", "good", "great", "fine", "why", "not", "of", "course", "i",
                 "would", "like", "want", "to", "that's", "thats", "alright", "all",
                 "right", "now", "maybe", "later", "im", "i'm", "good", "okay", "ok"}


def classify_yes_no(message):
    # 'yes' / 'no' only for short, pure replies ("yes please", "no thanks", "sure, go ahead").
    words = [w.strip(".,!?'\"") for w in message.lower().split()]
    words = [w for w in words if w]
    if not words or len(words) > 6:
        return None
    if "not" in words and ("now" in words or "really" in words):  # "not now"
        return "no" if all(w in YES_NO_FILLER | NO_WORDS | {"really"} for w in words) else None
    if words[0] in NO_WORDS or (words[:2] == ["no", "thanks"]):
        if all(w in NO_WORDS | YES_NO_FILLER for w in words):
            return "no"
        return None
    if any(w in YES_WORDS for w in words) and all(w in YES_WORDS | YES_NO_FILLER for w in words):
        return "yes"
    return None


def get_programme_label(programme_code, edition_id):
    row = get_programme_by_code(programme_code, edition_id=edition_id)
    return f"{programme_code} ({row[1]})" if row else programme_code


def get_module_label(module_code, edition_id):
    row = get_module_by_code(module_code, edition_id=edition_id)
    return f"{module_code} ({row[1]})" if row else module_code


MODULE_ONLY_INTENTS = {"module_lookup", "prerequisites"}
MODULE_AND_PROGRAMME_INTENTS = {"failure_impact", "module_eligibility"}
PERSONAL_INTENTS = {"module_eligibility", "academic_progress"}


def answered_key(intent, programme_code, module_code, edition_id):
    # One key per distinct answer, so the same answer is never suggested twice.
    if intent == "prerequisites":
        intent = "module_lookup"  # same data as module_lookup
    prog = None if intent in MODULE_ONLY_INTENTS else programme_code
    mod = module_code if (intent in MODULE_ONLY_INTENTS or intent in MODULE_AND_PROGRAMME_INTENTS) else None
    return (intent, prog, mod, edition_id)


def pick_suggestion(intent, programme_code, module_code, edition_id, context, extra=None):
    # Chooses ONE follow-up for what was just answered, skipping anything already answered in
    # this conversation, anything that lacks the codes it needs
    extra = extra or {}
    answered = {tuple(a) for a in context.get("answered", [])}
    has_record = bool(get_cached_document(context, "academic_record"))
    for nxt in NEXT_SUGGESTIONS.get(intent, []):
        prog, mod = programme_code, module_code
        if intent == "registration_check" and nxt == "module_lookup":
            mod = extra.get("first_missing_module")
        if nxt in PERSONAL_INTENTS and not has_record:
            continue
        if nxt in MODULE_ONLY_INTENTS and not mod:
            continue
        if nxt in MODULE_AND_PROGRAMME_INTENTS and not (prog and mod):
            continue
        if nxt not in MODULE_ONLY_INTENTS and not prog:
            continue
        key = answered_key(nxt, prog, mod, edition_id)
        if key in answered:
            continue
        prog_label = get_programme_label(prog, edition_id) if prog else ""
        mod_label = get_module_label(mod, edition_id) if mod else ""
        return {
            "intent": nxt,
            "programme_code": key[1],
            "module_code": key[2],
            "edition_id": edition_id,
            "question": SUGGESTION_QUESTIONS[nxt].format(prog=prog, mod=mod),
            "offer": SUGGESTION_OFFERS[nxt].format(prog=prog_label, mod=mod_label),
        }
    return None


def remember_answered(context, intent, programme_code, module_code, edition_id):
    answered = context.get("answered", [])
    answered.append(list(answered_key(intent, programme_code, module_code, edition_id)))
    context["answered"] = answered[-40:]


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
    (code, name, ptype, faculty, departments, degree_designator,
     duration_years, total_credits, intake, readmission_policy) = row
    lines = [
        f"{code} - {name} ({ptype})",
        f"Degree: {degree_designator}",
        f"Faculty: {faculty}",
        f"Department(s): {departments}",
        f"Duration: {duration_years} years, Total credits to graduate: {total_credits}",
        f"Intake: {intake}",
        f"Readmission policy: {readmission_policy}",
    ]
    return "\n".join(lines)


def summarize_admission_requirements(programme_code, rows, minimum_credits=None):
    if not rows and not minimum_credits:
        return f"No admission requirements found for {programme_code}."

    lines = [f"Admission requirements for {programme_code}:"]

    if minimum_credits:
        lines.append(f"- Minimum credits for admission: {minimum_credits}")

    for subject, pct, level in rows:
        if pct is not None and level is not None:
            lines.append(f"- A pass of at least {pct}% (Level {level}) in {subject}")
        else:
            # conditional/non-standard requirements that don't reduce to a
            # single percentage/level are stored as descriptive text instead
            lines.append(f"- {subject}")
    return "\n".join(lines)


def summarize_failure_impact(programme_code, module_code, result):
    if not result["found"]:
        return f"{module_code} is not part of {programme_code}'s curriculum, so no data is available."

    lines = [f"Impact of failing {module_code} in {programme_code} (Year {result['year']}, {result['effective_type']}):"]

    lines.append(
        "- This check covers which modules list this one as a prerequisite. The data "
        "does not include year-progression credit rules, so it cannot say whether "
        "failing this module blocks moving to the next year as a whole or adds a year "
        "to the degree -- only its direct effect on the modules below."
    )

    if result["blocked_modules"]:
        lines.append(f"- These modules require {module_code} as a prerequisite and would be directly blocked until it's passed:")
        for code, name in result["blocked_modules"]:
            lines.append(f"  - {code} ({name})")
    if result["at_risk_electives"]:
        lines.append(f"- These elective options require {module_code}, and failing it leaves too few alternatives to satisfy their elective group's requirement:")
        for code, name, group in result["at_risk_electives"]:
            lines.append(f"  - {code} ({name}), elective group {group}")
    if result["safe_electives"]:
        lines.append(f"- These elective options require {module_code}, but enough alternatives remain in their elective group, so that requirement is still satisfiable without them:")
        for code, name, group in result["safe_electives"]:
            lines.append(f"  - {code} ({name}), elective group {group}")
    if not result["blocked_modules"] and not result["at_risk_electives"] and not result["safe_electives"]:
        lines.append(f"- No other modules in {programme_code} list {module_code} as a prerequisite.")

    return "\n".join(lines)


def summarize_registration_check(result):
    years_str = ", ".join(str(y) for y in result["years_covered"]) or "none recognised"
    lines = [f"Registration check for {result['programme_code']} -- years covered: {years_str}"]

    if result["unrecognised_modules"]:
        lines.append(
            "Modules not recognised as part of this programme at all: "
            + ", ".join(result["unrecognised_modules"])
        )

    has_unresolved = any(yr["missing_compulsory"] or yr["unsatisfied_electives"]
                         for yr in result["year_results"])

    if result.get("academic_record_low_confidence"):
        lines.append(
            "Note: the academic record was read with the backup text reader, not "
            "the main one, so some modules or results may have been missed or "
            "misread. Treat 'confirmed passed' below with some caution."
        )

    if not result["academic_record_used"] and has_unresolved:
        lines.append(
            "Some modules expected in an earlier year are absent from this "
            "registration, and without an academic record there's no way to "
            "confirm whether they've already been passed or are a genuine "
            "gap. Please upload your academic record so this can be checked properly."
        )

    for yr in result["year_results"]:
        lines.append(f"\nYear {yr['year']}:")

        if yr["resolved_compulsory"]:
            codes = ", ".join(f"{c} ({n})" for c, n in yr["resolved_compulsory"])
            lines.append(f"  Confirmed already passed (correctly absent this year): {codes}")

        if yr["missing_compulsory"]:
            codes = ", ".join(f"{c} ({n})" for c, n in yr["missing_compulsory"])
            if result["academic_record_used"]:
                lines.append(f"  MISSING -- expected but not registered, and not found as passed in the academic record: {codes}")
            else:
                lines.append(f"  UNRESOLVED -- expected but not registered this year, and not yet checked against an academic record: {codes}")
        elif not yr["resolved_compulsory"]:
            lines.append("  All compulsory/major modules for this year are accounted for.")

        for g in yr.get("resolved_electives", []):
            parts = [f"{c} ({n})" for c, n in g["chosen"]]
            parts += [f"{c} ({n}), passed in an earlier year" for c, n in g["passed_earlier"]]
            lines.append(f"  Elective group {g['group']}: satisfied by " + "; ".join(parts))

        if yr["unsatisfied_electives"]:
            for g in yr["unsatisfied_electives"]:
                opts = ", ".join(c for c, n in g["options"])
                found = ", ".join(c for c, n in g["chosen"] + g.get("passed_earlier", [])) or "none"
                status = "" if result["academic_record_used"] else \
                    " (not yet checked against an academic record -- it may have been passed in an earlier year)"
                lines.append(
                    f"  Elective group {g['group']}: needs {g['expected_count']} chosen, "
                    f"only {g['found_count'] + len(g.get('passed_earlier', []))} found ({found}) "
                    f"-- options are {opts}{status}"
                )
        elif not yr.get("resolved_electives"):
            lines.append("  All elective group requirements for this year are satisfied.")

    return "\n".join(lines)


STATUS_WORDS = {"passed": "PASSED", "failed": "attempted but NOT passed", "cancelled": "cancelled (never completed)",
                "no_result": "attempted, no result recorded yet", "not_taken": "not taken yet"}


def _attempts_text(attempts):
    parts = []
    for a in attempts:
        bits = [str(a["academic_year"] or "?")]
        if a["period"]:
            bits.append(a["period"])
        if a["final_mark"] is not None:
            bits.append(f"final mark {a['final_mark']}")
        bits.append(a["result"] or "no result")
        parts.append(" / ".join(bits))
    return "; ".join(parts)


def summarize_module_eligibility(r):
    if not r["found"]:
        return f"No module {r['module_code']} was found in this handbook edition."
    code = r["module_code"]
    lines = [f"Personal check for {code} - {r['module_name']} ({r['credits']} credits, "
             f"Year {r['year']} Semester {r['semester']})"]

    if not r["record_available"]:
        lines.append("No academic record has been uploaded, so the student's own results are NOT known. "
                     "Only the handbook rules below can be given; ask the student to upload their "
                     "academic record for a personal answer.")

    if r["own_attempts"]:
        lines.append(f"The student's own attempts at {code}: {_attempts_text(r['own_attempts'])}")
    elif r["record_available"]:
        lines.append(f"The academic record shows no attempt at {code}.")
    if r["registered_now"]:
        lines.append(f"{code} IS on the student's current proof of registration.")

    if r["programme_code"]:
        if r["in_programme"] is False:
            lines.append(f"{code} is NOT part of programme {r['programme_code']} in this handbook.")
        elif r["in_programme"]:
            if r["group"]:
                g = r["group"]
                others = ", ".join(f"{c} ({n})" for c, n in g["other_options"]) or "none"
                lines.append(f"In {r['programme_code']}, {code} is an ELECTIVE in group {g['name']}: "
                             f"choose {g['choose_count']} from {code} and {others}.")
            else:
                lines.append(f"In {r['programme_code']}, {code} is {r['effective_type'].upper()} "
                             f"(required for the programme).")
    else:
        lines.append("The student's programme is not known, so its place in their programme can't be checked.")

    if r["prerequisites"]:
        lines.append("Prerequisites (must be PASSED before registering): " + "; ".join(
            f"{p}: {STATUS_WORDS[st] if r['record_available'] else 'unknown (no record)'}"
            for p, st in r["prerequisites"]))
    else:
        lines.append(f"{code} has no prerequisites.")
    if r["corequisites"]:
        lines.append("Co-requisites (passed before, or taken in the same period): " + "; ".join(
            f"{c}: {STATUS_WORDS[st] if r['record_available'] else 'unknown (no record)'}"
            + (", registered now" if now else "") for c, st, now in r["corequisites"]))

    # verdict computed here, never left to Claude
    missing = [p for p, st in r["prerequisites"] if st != "passed"]
    verdict = []
    if r["own_status"] == "passed":
        verdict.append(f"The student has ALREADY PASSED {code}; they do not need to take it again.")
    elif r["programme_code"] and r["in_programme"] is False:
        verdict.append(f"{code} is not part of the student's programme, so they do not need it for their degree.")
    else:
        g = r["group"]
        covered_by = (g["others_passed"] + g["others_registered_now"]) if g else []
        if g and len(covered_by) >= g["choose_count"]:
            names = ", ".join(f"{c} ({n})" for c, n in covered_by)
            verdict.append(f"The student does NOT need {code}: elective group {g['name']} "
                           f"(choose {g['choose_count']}) is already covered by {names} "
                           f"(passed or registered now).")
        elif r["record_available"] and missing:
            verdict.append(f"The student is NOT yet eligible to register for {code}: prerequisite(s) not "
                           f"passed: {', '.join(missing)}.")
        elif r["record_available"]:
            need = "needs" if (r["in_programme"] and not g) else "may take"
            verdict.append(f"All prerequisites are passed, so the student is ELIGIBLE to register for "
                           f"{code}" + (f" and {need} it for their programme." if r["in_programme"] else "."))
        if r["own_status"] == "failed":
            verdict.append(f"They attempted {code} before without passing it.")
    if verdict:
        lines.append("VERDICT: " + " ".join(verdict))
    return "\n".join(lines)


def summarize_academic_progress(r):
    if not r["record_available"]:
        return "No academic record has been uploaded, so the student's results are not known."
    fmt = lambda items: ", ".join(f"{c} ({n})" for c, n in items)
    lines = [f"Academic progress for {r['programme_code']} (record covers "
             f"{', '.join(str(y) for y in r['record_years']) or 'unknown years'}):",
             f"Credits passed towards the programme: {r['credits_passed']} of {r['credits_required']}."]
    for y in r["years"]:
        lines.append(f"\nYear {y['year']}:")
        if y["passed"]:
            lines.append(f"  Compulsory/major PASSED: {fmt(y['passed'])}")
        if y["registered_now"]:
            lines.append(f"  Compulsory/major registered now (not passed yet): {fmt(y['registered_now'])}")
        if y["failed"]:
            lines.append(f"  Compulsory/major attempted but NOT passed: {fmt(y['failed'])}")
        if y["not_taken"]:
            lines.append(f"  Compulsory/major not taken yet: {fmt(y['not_taken'])}")
        for g in y["groups"]:
            done = len(g["passed"])
            if done >= g["choose_count"]:
                state = "DONE"
            elif done + len(g["registered_now"]) >= g["choose_count"]:
                state = "IN PROGRESS (registered now, not passed yet)"
            else:
                state = "OUTSTANDING"
            detail = []
            if g["passed"]:
                detail.append(f"passed {fmt(g['passed'])}")
            if g["registered_now"]:
                detail.append(f"registered now {fmt(g['registered_now'])}")
            if g["failed"]:
                detail.append(f"failed {fmt(g['failed'])}")
            lines.append(f"  Elective group {g['name']} (choose {g['choose_count']}) {state}: "
                         + ("; ".join(detail) if detail else "nothing taken yet")
                         + f". Options: {', '.join(c for c, n in g['options'])}")
    if r["failed_not_yet_passed"]:
        lines.append(f"\nModules attempted but never passed (any programme): {', '.join(r['failed_not_yet_passed'])}")
    if r["passed_outside_programme"]:
        lines.append(f"Passed modules that are not part of {r['programme_code']} in this handbook: "
                     f"{', '.join(r['passed_outside_programme'])}")
    return "\n".join(lines)


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

    system_prompt = (
        "You are UNIZULU's campus assistant chatbot. Reply briefly and "
        "warmly to this greeting/small talk. If this looks like the start "
        "of a conversation, briefly mention you can help with programme "
        "modules, electives, credits to graduate, admission requirements, "
        "or module prerequisites -- in one short sentence, not a list."
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        system=system_prompt,
        messages=messages,
    )
    return extract_text(response)


def _route_answer(question, intent, programme_code, module_code, programme_type, history, context,
                  edition_id=None):
    # The actual routing chain - pulled out of answer_question() so memory bookkeeping can be
    # centralized there regardless of which branch below fires.
    meta = {"data_answer": False}
    if intent == "casual":
        return casual_reply(question, history), meta

    edition = get_edition(edition_id)
    if edition_needs_for_intent(intent, programme_code, module_code, programme_type, context) and not edition:
        # answer_question() asks before routing, so this is only a safety net
        return build_edition_question(), meta

    def data(summary):
        meta["data_answer"] = True
        return format_structured_answer(question, summary, edition), meta

    if intent == "programme_lookup" and programme_code:
        row = get_programme_by_code(programme_code, edition_id=edition_id)
        return data(summarize_programme(row))

    if intent == "list_by_type" and programme_type:
        rows = get_programmes_by_type(programme_type, edition_id=edition_id)
        return data(summarize_programme_list(programme_type, rows))

    if intent == "module_lookup" and module_code:
        row = get_module_by_code(module_code, edition_id=edition_id)
        return data(summarize_module(row))

    if intent == "list_modules" and programme_code:
        rows = get_modules_for_programme(programme_code, edition_id=edition_id)
        return data(summarize_modules(programme_code, rows))

    if intent == "list_electives" and programme_code:
        rows = get_electives_for_programme(programme_code, edition_id=edition_id)
        return data(summarize_electives(programme_code, rows))

    if intent == "majors_compulsory" and programme_code:
        rows = get_majors_compulsory_for_programme(programme_code, edition_id=edition_id)
        return data(summarize_majors_compulsory(programme_code, rows))

    if intent == "prerequisites" and module_code:
        row = get_module_by_code(module_code, edition_id=edition_id)
        return data(summarize_module(row))

    if intent == "credits_total" and programme_code:
        rows = get_credits_total(programme_code, edition_id=edition_id)
        return data(summarize_credits(programme_code, rows))

    if intent == "module_count" and programme_code:
        rows = get_module_count_data(programme_code, edition_id=edition_id)
        return data(summarize_module_count(programme_code, rows))

    if intent == "admission_requirements" and programme_code:
        rows = get_admission_requirements(programme_code, edition_id=edition_id)
        minimum_credits = get_minimum_credits_for_admission(programme_code, edition_id=edition_id)
        summary = summarize_admission_requirements(programme_code, rows, minimum_credits)
        return data(summary)

    if intent == "failure_impact" and programme_code and module_code:
        impact = check_module_failure_impact(programme_code, module_code, edition_id=edition_id)
        summary = summarize_failure_impact(programme_code, module_code, impact)
        return data(summary)

    if intent == "module_eligibility" and module_code:
        prog = resolve_personal_programme(programme_code, context)
        r = check_module_eligibility(prog, module_code, context, edition_id=edition_id)
        if prog:
            meta["programme_code"] = prog
        summary = summarize_module_eligibility(r)
        record = get_cached_document(context, "academic_record")
        if record and record.get("extraction_status") == "low_confidence":
            summary += ("\nNote: the academic record was read with the backup text reader, "
                        "so some results may be missing or misread.")
        return data(summary)

    if intent == "academic_progress":
        if not get_academic_history(context):
            return ("I don't have your academic record yet. Please upload it (PDF or a "
                    "clear photo) and I can tell you what you've passed, what's outstanding, "
                    "and how many credits you have."), meta
        prog = resolve_personal_programme(programme_code, context)
        if not prog:
            return ("Which programme are you registered for? Tell me its name or code "
                    "(for example 4BSC21) and I'll check your record against it."), meta
        meta["programme_code"] = prog
        summary = summarize_academic_progress(check_academic_progress(prog, context, edition_id=edition_id))
        record = get_cached_document(context, "academic_record")
        if record and record.get("extraction_status") == "low_confidence":
            summary += ("\nNote: the academic record was read with the backup text reader, "
                        "so some results may be missing or misread.")
        return data(summary)

    if intent == "registration_check":
        outcome = check_registration_from_context(context, fallback_programme_code=programme_code)

        if outcome["status"] == "needs_por":
            return (
                "I don't have a proof of registration to check yet. Please "
                "upload it (PDF or a clear photo) and I can check it against "
                "your programme's curriculum."
            ), meta
        if outcome["status"] == "needs_edition":
            return outcome["message"], meta
        if outcome["status"] == "needs_programme":
            return (
                "I have your proof of registration, but couldn't tell which "
                "programme it's for. Could you confirm your programme name "
                "or code?"
            ), meta

        result = outcome["result"]
        for yr in result["year_results"]:
            if yr["missing_compulsory"]:
                meta["first_missing_module"] = yr["missing_compulsory"][0][0]
                break
        meta["programme_code"] = result["programme_code"]
        summary = summarize_registration_check(result)
        return data(summary)

    return (
        "I couldn't find information to answer that. I can help with: "
        "a programme's modules, electives, or majors/compulsory modules; "
        "total credits or number of modules to graduate; admission "
        "requirements; prerequisites for a specific module; or a "
        "programme's name, faculty, and duration. Try asking about one of "
        "those, or name a specific programme or module code."
    ), meta


def answer_question(question, known_codes, context=None):
    # The single entry point.
    context = context if context is not None else {}
    history = context.get("history", [])
    message = question.strip()

    prior_programme = context.get("last_programme_code")
    prior_module = context.get("last_module_code")

    def finish(answer, same_focus=True):
        limit = FOCUSED_HISTORY_LIMIT if same_focus else HISTORY_LIMIT
        history.append({"question": question, "answer": answer})
        context["history"] = history[-limit:]
        save_memory(context)
        return answer

    awaiting = context.get("awaiting_edition", False)
    pending_question = context.get("pending_question")
    suggestion = context.get("pending_suggestion")
    context["pending_suggestion"] = None   # an offer only lasts one turn
    notes = []
    detected = None
    ask_text = message

    # 1. reply to last turn's suggestion
    reply = classify_yes_no(message) if suggestion else None
    if reply == "no":
        return finish("No problem. What else would you like to know?")
    if reply == "yes":
        detected = {"intent": suggestion["intent"], "programme_code": suggestion["programme_code"],
                    "module_code": suggestion["module_code"], "programme_type": None}
        ask_text = suggestion["question"]

    # 2. handbook edition commands / choices
    if detected is None:
        if is_ask_current_edition(message):
            current = get_edition(context.get("edition_id"))
            if current:
                return finish(f"I'm using {edition_display(current)}. "
                              "Tell me if you'd like to switch to a different handbook.")
            context["awaiting_edition"] = True
            return finish("No handbook has been chosen yet. " + build_edition_question())

        status, edition = find_edition_in_message(message, awaiting_edition=awaiting)
        if status == "unknown":
            context["awaiting_edition"] = True
            return finish(f"I don't have a {edition} handbook. " + build_edition_question())

        if status == "found":
            context["awaiting_edition"] = False
            if edition["id"] != context.get("edition_id"):
                switched = context.get("edition_id") is not None
                context["edition_id"] = edition["id"]
                notes.append(f"{'Switched to' if switched else 'Using'} {edition_display(edition)}.")

            if is_pure_edition_choice(message):
                if pending_question:
                    # the question the student asked before picking a handbook
                    context["pending_question"] = None
                    detected = pending_question["detected"]
                    ask_text = pending_question["question"]
                else:
                    note = " ".join(notes) or f"I'm already using {edition_display(edition)}."
                    return finish(note + " What would you like to know?")
            else:
                context["pending_question"] = None  # a new question replaces the held one

        elif is_switch_edition_request(message):
            context["awaiting_edition"] = True
            return finish(build_edition_question())

        elif awaiting:
            # asked "which handbook?" but got a different message -- treat it
            # as a new question (it will be asked again below if still needed)
            context["awaiting_edition"] = False
            context["pending_question"] = None

    # 3. the question itself
    if detected is None:
        detected = detect_intent(message, known_codes, context)
    intent = detected.get("intent")
    programme_code = detected.get("programme_code")
    module_code = detected.get("module_code")
    programme_type = detected.get("programme_type")
    print(f"[intent] {ask_text!r} -> intent={intent} programme={programme_code} "
          f"module={module_code} type={programme_type} edition={context.get('edition_id')}")

    edition_id = get_valid_edition_id(context)
    if edition_id is None and edition_needs_for_intent(intent, programme_code, module_code,
                                                      programme_type, context):
        context["awaiting_edition"] = True
        context["pending_question"] = {"question": ask_text, "detected": detected}
        if programme_code:
            context["last_programme_code"] = programme_code
        if module_code:
            context["last_module_code"] = module_code
        return finish(build_edition_question())

    # a turn counts as "still focused" if it resolved to the exact same programme/module already
    # being discussed
    same_focus = bool(
        (programme_code and programme_code == prior_programme)
        or (module_code and module_code == prior_module)
        or (not programme_code and not module_code and (prior_programme or prior_module))
    )

    if programme_code:
        context["last_programme_code"] = programme_code
    if module_code:
        context["last_module_code"] = module_code

    answer, meta = _route_answer(ask_text, intent, programme_code, module_code, programme_type,
                                 history, context, edition_id=edition_id)

    if notes:
        answer = " ".join(notes) + "\n\n" + answer

    if meta.get("programme_code"):
        # e.g. a registration check learns the programme from the uploaded
        # document follow-ups ("what are its electives?") should use it
        context["last_programme_code"] = meta["programme_code"]

    if meta.get("data_answer"):
        remember_answered(context, intent, meta.get("programme_code") or programme_code,
                          module_code, edition_id)
        next_step = pick_suggestion(intent,
                                    meta.get("programme_code") or programme_code
                                    or context.get("last_programme_code"),
                                    module_code, edition_id, context, extra=meta)
        if next_step:
            answer += "\n\n" + next_step["offer"]
            context["pending_suggestion"] = next_step

    return finish(answer, same_focus)


def new_context():
    # A fresh, empty conversation memory -- what every new chat starts with.
    return {"last_programme_code": None, "last_module_code": None, "history": [],
            "edition_id": None, "awaiting_edition": False, "pending_question": None,
            "pending_suggestion": None, "answered": []}


def complete_context(context):
    # Fills in any keys an older saved context is missing, so chats saved before a new key was
    # added still load cleanly.
    full = new_context()
    full.update(context or {})
    return full


# Terminal test mode keeps its memory in conversation_memory.json.
PERSIST_TO_FILE = True


def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return complete_context(json.load(f))
    return new_context()


def save_memory(context):
    if not PERSIST_TO_FILE:
        return
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(context, f, indent=2)


def generate_session_title(exchanges, force=False):
    # Asks Claude for a short chat title, the way a chat app names a conversation. `exchanges`
    # is a list of (question
    transcript = "\n\n".join(
        f"Student: {q}\nAssistant: {(a or '')[:400]}" for q, a in exchanges if q
    )
    prompt = f"""Write a short title for this chat between a student and the
University of Zululand campus assistant, like the title a chat app shows in
its list of past chats.

Rules:
- 2 to 6 words, in the same language the student used
- describe what the student wants, e.g. "4BSC21 Elective Modules",
  "Checking 2023 Registration", "Prerequisites for 4CPS232"
- keep programme and module codes exactly as written (e.g. 4BSC21)
- no quotes, no ending punctuation, no emojis
- reply with the title ONLY
{"" if force else '''- if the chat so far is ONLY a greeting or small talk with no real
  question yet, reply with exactly: NONE'''}

Chat:
{transcript}"""
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=30,
            messages=[{"role": "user", "content": prompt}],
        )
        title = extract_text(response).strip().splitlines()[0] if extract_text(response).strip() else ""
        title = title.strip().strip("\"'`*#").strip().rstrip(".!?:;,")
        if title.upper() == "NONE":
            return None if not force else _fallback_title(exchanges)
        return title or _fallback_title(exchanges)
    except Exception as e:
        print(f"[title] could not generate a chat title: {e}")
        return _fallback_title(exchanges)


def _fallback_title(exchanges):
    first = next((q for q, a in exchanges if q), "New chat")
    first = re.sub(r"\s+", " ", first).strip()
    return first if len(first) <= 40 else first[:39].rsplit(" ", 1)[0] + "…"


if __name__ == "__main__":
    known_codes = get_programme_names()
    context = load_memory()

    print("UNIZULU Campus AI Assistant (terminal test mode)")
    print("Type 'exit' to quit.\n")
    if context["history"]:
        print(f"(resuming previous session -- {len(context['history'])} past exchange(s) loaded)")
        current = get_edition(context.get("edition_id"))
        if current:
            print(f"(handbook in use: {edition_display(current)})")
        print()

    while True:
        question = input("You: ").strip()
        if question.lower() in ("exit", "quit"):
            break
        if not question:
            continue

        answer = answer_question(question, known_codes, context)
        print(f"\nAssistant: {answer}\n")

    conn.close()