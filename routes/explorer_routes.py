# the Programme & Module Explorer (the "📚 Explorer" tab).

import re
import threading
import time
from collections import defaultdict

from flask import Blueprint, jsonify, request, session

from services import accounts

explorer_bp = Blueprint("explorer", __name__)

CACHE_SECONDS = 600          # re-read the handbook tables at most every 10 minutes
POPULAR_DAYS = 7             # "most viewed this week"
POPULAR_COUNT = 4
DEFAULT_POPULAR = ["4BSC01", "4BSC21", "4BSC18", "4BSC09"]   # until enough views are recorded
TRY_EXAMPLES = ["4CPS211", "Calculus", "Hydrology", "4BSC21"]

MODULE_CODE = re.compile(r"^\d[A-Z]{3}\d{3}$")       # 4MTH222
PROGRAMME_CODE = re.compile(r"^\d[A-Z]{3}\d{2}$")    # 4BSC21

# icons for the "Browse by subject" tiles (a subject not listed gets 📘)
SUBJECT_ICONS = {
    "applied mathematics": "➗", "biochemistry": "🧪", "botany": "🌿", "chemistry": "⚗️",
    "computer science": "💻", "geography": "🗺️", "human movement science": "🏃",
    "hydrology": "💧", "mathematics": "📐", "microbiology": "🦠", "physics": "🔭",
    "statistics": "📊", "zoology": "🦓",
}



# text helpers
SMALL_WORDS = {"and", "of", "for", "to", "in", "the", "with", "on", "a", "an", "or", "at", "by"}
KEEP_UPPER = {"I", "II", "III", "IV", "V", "VI", "NSC", "BSC", "NQF", "GIS", "DNA", "IT", "HIV", "AIDS"}


def nice(text):
    # 'LINEAR ALGEBRA & DIFFERENTIAL EQUATIONS' -> 'Linear Algebra & Differential Equations'.
    if not text:
        return text
    out = []
    for i, word in enumerate(str(text).strip().split()):
        bare = word.strip("(),.:;")
        if bare.upper() in KEEP_UPPER or any(ch.isdigit() for ch in bare):
            out.append(word.upper() if bare.upper() in KEEP_UPPER else word)
        elif i > 0 and bare.lower() in SMALL_WORDS:
            out.append(word.lower())
        else:
            out.append("-".join(_cap(part) for part in word.split("-")))
    return " ".join(out)


def _cap(word):
    # Capitalise the first letter, even after a bracket: '(CALCULUS' -> '(Calculus'.
    w = word.lower()
    for i, ch in enumerate(w):
        if ch.isalpha():
            return w[:i] + ch.upper() + w[i + 1:]
    return w


def sentence(text):
    # 'DAY CLASSES' -> 'Day classes'.
    if not text:
        return text
    t = str(text).strip().lower()
    return t[:1].upper() + t[1:]


def admission_note(text):
    # 'NATIONAL SENIOR CERTIFICATE WITH DEGREE ENDORSEMENT WITH AT LEAST 28 NSC POINTS' -> 'NSC
    # with degree endorsement, at least 28 NSC points.'
    if not text:
        return None
    t = str(text).strip().lower()
    t = t.replace("national senior certificate", "NSC").replace(" nsc ", " NSC ")
    t = re.sub(r"\s+with at least\s+", ", at least ", t)
    t = t[:1].upper() + t[1:]
    return t if t.endswith(".") else t + "."


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


# reading one edition
class Edition:
    # Everything the explorer shows for one handbook edition, read in a few queries.

    def __init__(self, edition):
        self.id = edition["id"]
        self.year = edition["year"]
        self.label = edition["label"]
        self.loaded_at = time.time()
        with accounts._cursor() as cur:
            pm_cols = _columns(cur, "programme_modules")
            year_sql = "COALESCE(pm.expected_year, m.year)" if "expected_year" in pm_cols else "m.year"
            sem_sql = "COALESCE(pm.expected_semester, m.semester)" if "expected_semester" in pm_cols else "m.semester"

            cur.execute("""SELECT programme_code, programme_name, programme_type, faculty, departments,
                                  degree_designator, abbreviation, exit_nqf_level,
                                  minimum_credits_for_admission, minimum_duration_years,
                                  presentation_mode, intake, readmission_policy, total_credits_to_graduate
                           FROM programme_info WHERE handbook_edition_id = %s""", (self.id,))
            prog_rows = cur.fetchall()
            cur.execute("""SELECT programme_code, subject_name, min_percentage, min_level
                           FROM programme_admission_requirements WHERE handbook_edition_id = %s
                           ORDER BY id""", (self.id,))
            adm_rows = cur.fetchall()
            cur.execute("""SELECT module_code, module_name, credit_value, nqf_level, year, semester,
                                  prerequisite_subjects, corequisite_subjects
                           FROM modules WHERE handbook_edition_id = %s""", (self.id,))
            mod_rows = cur.fetchall()
            cur.execute(f"""SELECT pm.programme_code, m.module_code, pm.effective_type, pm.elective_group,
                                   pm.choose_count, {year_sql}, {sem_sql}, pm.id
                            FROM programme_modules pm JOIN modules m ON pm.module_id = m.module_id
                            WHERE pm.handbook_edition_id = %s ORDER BY pm.id""", (self.id,))
            pm_rows = cur.fetchall()

        # modules
        self.modules = {}
        for code, name, credits, nqf, year, sem, pre, co in mod_rows:
            self.modules[code] = {
                "code": code, "name": nice(name), "credits": credits, "nqf": nqf,
                "year": year, "semester": sem,
                "prerequisites": [c.strip() for c in (pre or []) if c and c.strip()],
                "corequisites": [c.strip() for c in (co or []) if c and c.strip()],
            }
        self.leads_to = defaultdict(list)            # module -> modules that need it
        for m in self.modules.values():
            for req in m["prerequisites"]:
                if req in self.modules and m["code"] not in self.leads_to[req]:
                    self.leads_to[req].append(m["code"])

        # programmes
        self.programmes = {}
        for (code, name, ptype, faculty, departments, degree, abbr, nqf, min_adm, years,
             mode, intake, readmission, total) in prog_rows:
            raw = (name or "").strip()
            not_offered = "NOT OFFERED" in raw.upper()
            clean = re.sub(r"\s*\(NOT OFFERED\)\s*", " ", raw, flags=re.I).strip()
            subjects = [nice(s.strip()) for s in re.split(r"\s+AND\s+", clean, flags=re.I) if s.strip()]
            self.programmes[code] = {
                "code": code, "name": nice(clean), "type": (ptype or "Mainstream").strip(),
                "not_offered": not_offered, "subjects": subjects,
                "faculty": nice(faculty), "department": nice(departments),
                "degree": nice(degree), "abbreviation": abbr or "", "short_degree": _short_degree(abbr),
                "nqf": nqf, "years": years, "credits": total,
                "presentation": sentence(mode), "intake": sentence(intake),
                "readmission": sentence(readmission), "admission_note": admission_note(min_adm),
            }

        self.admissions = defaultdict(list)
        for code, subject, pct, level in adm_rows:
            spaced = re.sub(r"\s*\(\s*", " (", subject or "")          # 'MATHEMATICS(OTHER' -> 'MATHEMATICS (OTHER'
            self.admissions[code].append({"subject": nice(spaced), "raw": (subject or "").upper().strip(),
                                          "percentage": pct, "level": level})

        # which modules each programme has
        self.placements = defaultdict(list)          # programme -> rows
        self.module_programmes = defaultdict(list)   # module -> rows
        for prog, mcode, etype, group, choose, year, sem, _id in pm_rows:
            row = {"programme": prog, "module": mcode, "type": (etype or "").strip() or "Compulsory",
                   "group": (group or "").strip() or None, "choose": choose, "year": year, "semester": sem}
            self.placements[prog].append(row)
            self.module_programmes[mcode].append(row)

        # subjects (the two halves of "X AND Y" programme names)
        by_subject = defaultdict(list)
        for p in self.programmes.values():
            for s in p["subjects"]:
                by_subject[s].append(p["code"])
        self.subjects = {}
        for name, codes in by_subject.items():
            self.subjects[slug(name)] = {
                "slug": slug(name), "name": name, "icon": SUBJECT_ICONS.get(name.lower(), "📘"),
                "programmes": sorted(codes), "prefix": None,
            }
        self._match_subject_prefixes()

    def _match_subject_prefixes(self):
        # Which module codes belong to a subject (Computer Science -> 4CPS...)
        major_prefixes = defaultdict(set)            # prefix -> programmes where it is a Major
        for prog, rows in self.placements.items():
            for r in rows:
                if r["type"] == "Major":
                    major_prefixes[r["module"][:4]].add(prog)
        everything = set(self.programmes)
        for s in self.subjects.values():
            mine = set(s["programmes"])
            others = everything - mine
            best, best_score = None, 0.0
            for prefix, progs in major_prefixes.items():
                score = len(progs & mine) / len(mine) - (len(progs & others) / len(others) if others else 0)
                if score > best_score:
                    best, best_score = prefix, score
            s["prefix"] = best if best_score >= 0.5 else None

    # small building blocks for the pages
    def programme_card(self, code):
        p = self.programmes[code]
        return {k: p[k] for k in ("code", "name", "type", "department", "short_degree", "years",
                                  "credits", "nqf", "not_offered")}

    def module_card(self, code):
        m = self.modules.get(code)
        if not m:
            return {"code": code, "name": None, "known": False}
        return {"code": code, "name": m["name"], "credits": m["credits"], "nqf": m["nqf"],
                "year": m["year"], "semester": m["semester"], "known": True}

    def subject_of(self, programme_code):
        p = self.programmes.get(programme_code)
        return p["subjects"][0] if p and p["subjects"] else None

    def admission_list(self, code):
        # Admission rows, with the handbook's "one of" alternatives grouped together.
        rows = self.admissions.get(code, [])
        raws = {r["raw"] for r in rows}
        one_of = set()
        if any("INFO" in r for r in raws) and any("PHYSICAL" in r for r in raws):
            one_of = {r for r in raws if "INFO" in r or "PHYSICAL" in r or "LIFE" in r}
        maths = {r for r in raws if r.startswith("MATHEMATICS")}
        if len(maths) > 1:
            one_of |= maths
        required = [r for r in rows if r["raw"] not in one_of]
        alternatives = [r for r in rows if r["raw"] in one_of]
        clean = lambda r: {"subject": _subject_label(r["subject"]), "percentage": r["percentage"], "level": r["level"]}
        groups = [{"one_of": False, "items": [clean(r)]} for r in required]
        if alternatives:
            groups.append({"one_of": True, "items": [clean(r) for r in alternatives]})
        return groups


def _subject_label(subject):
    s = subject.replace("Info Technology", "Information Technology")
    s = re.sub(r"Mathematics\s*\(\s*", "Mathematics (", s)
    return s


def _short_degree(abbr):
    a = (abbr or "").strip().upper()
    return {"BSC": "BSc", "BA": "BA", "BCOM": "BCom", "BED": "BEd"}.get(a, a.title() if a else "")


def _columns(cur, table):
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s""", (table,))
    return {r[0] for r in cur.fetchall()}


# edition cache
_cache = {}
_cache_lock = threading.Lock()
_editions = {"at": 0, "list": []}


def editions():
    # Every handbook edition, newest first (re-read every CACHE_SECONDS).
    if time.time() - _editions["at"] > CACHE_SECONDS or not _editions["list"]:
        with accounts._cursor() as cur:
            cur.execute("SELECT id, year, label FROM handbook_editions ORDER BY year DESC, id DESC")
            _editions["list"] = [{"id": i, "year": y, "label": l} for i, y, l in cur.fetchall()]
        _editions["at"] = time.time()
    return _editions["list"]


def edition_from_request():
    # The edition asked for (?edition=ID or ?year=2026), else the newest one.
    all_editions = editions()
    if not all_editions:
        return None
    wanted = request.args.get("edition")
    year = request.args.get("year")
    chosen = None
    if wanted and wanted.isdigit():
        chosen = next((e for e in all_editions if e["id"] == int(wanted)), None)
    elif year and year.isdigit():
        chosen = next((e for e in all_editions if e["year"] == int(year)), None)
    chosen = chosen or all_editions[0]
    with _cache_lock:
        ed = _cache.get(chosen["id"])
        if ed is None or time.time() - ed.loaded_at > CACHE_SECONDS:
            ed = Edition(chosen)
            _cache[chosen["id"]] = ed
    return ed


def _edition_info(ed):
    return {"id": ed.id, "year": ed.year, "label": ed.label}


def _unavailable():
    return jsonify({"error": "The handbook database cannot be reached right now. Please try again shortly."}), 503


def _with_edition(handler):
    # Runs handler(ed) with the requested edition; database problems become a clear 503.
    def wrapped(*args, **kwargs):
        try:
            ed = edition_from_request()
        except Exception as exc:
            print(f"[explorer] could not read the handbook: {exc}")
            return _unavailable()
        if ed is None:
            return jsonify({"error": "No handbook has been loaded into the database yet."}), 404
        return handler(ed, *args, **kwargs)
    wrapped.__name__ = handler.__name__
    return wrapped


# popular programmes
def _record_view(code):
    # Count a programme page view (once per browser per programme per day).
    today = time.strftime("%Y-%m-%d")
    seen = session.get("explorer_seen") or {}
    if seen.get("day") != today:
        seen = {"day": today, "codes": []}
    if code in seen["codes"]:
        return
    seen["codes"] = (seen["codes"] + [code])[-60:]
    session["explorer_seen"] = seen
    try:
        with accounts._cursor() as cur:
            cur.execute("INSERT INTO explorer_views (programme_code) VALUES (%s)", (code,))
            if time.time() % 50 < 1:                 # now and then: forget views older than 30 days
                cur.execute("DELETE FROM explorer_views WHERE viewed_at < now() - interval '30 days'")
    except Exception as exc:
        print(f"[explorer] view not counted: {exc}")


def _popular(ed):
    codes = []
    try:
        with accounts._cursor() as cur:
            cur.execute("""SELECT programme_code, count(*) FROM explorer_views
                           WHERE viewed_at >= now() - make_interval(days => %s)
                           GROUP BY programme_code ORDER BY count(*) DESC, programme_code LIMIT 20""",
                        (POPULAR_DAYS,))
            codes = [c for c, _n in cur.fetchall() if c in ed.programmes]
    except Exception as exc:
        print(f"[explorer] popular programmes not available: {exc}")
    for c in DEFAULT_POPULAR + sorted(ed.programmes):        # fill up until enough views exist
        if len(codes) >= POPULAR_COUNT:
            break
        if c in ed.programmes and c not in codes and not ed.programmes[c]["not_offered"]:
            codes.append(c)
    return [ed.programme_card(c) for c in codes[:POPULAR_COUNT]]


# endpoints
@explorer_bp.route("/api/explorer/editions")
def api_editions():
    try:
        return jsonify({"editions": editions()})
    except Exception as exc:
        print(f"[explorer] editions not available: {exc}")
        return _unavailable()


@explorer_bp.route("/api/explorer/overview")
@_with_edition
def api_overview(ed):
    subjects = sorted(ed.subjects.values(), key=lambda s: s["name"])
    faculty = next((p["faculty"] for p in ed.programmes.values() if p["faculty"]), None)
    types = sorted({p["type"].lower() for p in ed.programmes.values()})
    return jsonify({
        "edition": _edition_info(ed),
        "faculty": faculty,
        "programme_types": types,
        "programme_count": len(ed.programmes),
        "module_count": len(ed.modules),
        "popular": _popular(ed),
        "subjects": [{"slug": s["slug"], "name": s["name"], "icon": s["icon"],
                      "count": len(s["programmes"])} for s in subjects],
        "try": [t for t in TRY_EXAMPLES if not MODULE_CODE.match(t) or t in ed.modules],
    })


@explorer_bp.route("/api/explorer/subject/<subject_slug>")
@_with_edition
def api_subject(ed, subject_slug):
    s = ed.subjects.get(slug(subject_slug))
    if not s:
        return jsonify({"error": "There is no subject with that name in this handbook."}), 404
    programmes = [ed.programme_card(c) for c in s["programmes"]]
    modules = []
    if s["prefix"]:
        codes = sorted((c for c in ed.modules if c.startswith(s["prefix"])),
                       key=lambda c: (ed.modules[c]["year"] or 9, ed.modules[c]["semester"] or 9, c))
        for c in codes:
            card = ed.module_card(c)
            card["programme_count"] = len({r["programme"] for r in ed.module_programmes.get(c, [])})
            modules.append(card)
    # "core" modules: per year + semester, the one found in most of this subject's programmes
    core, seen_slots = [], set()
    mine = set(s["programmes"])
    for m in sorted(modules, key=lambda m: (m["year"] or 9, m["semester"] or 9,
                                            -len({r["programme"] for r in ed.module_programmes.get(m["code"], [])
                                                  if r["programme"] in mine and r["type"] != "Elective"}),
                                            m["code"])):
        slot = (m["year"], m["semester"])
        if slot not in seen_slots:
            seen_slots.add(slot)
            core.append(m)
    return jsonify({
        "edition": _edition_info(ed),
        "subject": {"slug": s["slug"], "name": s["name"], "icon": s["icon"], "prefix": s["prefix"]},
        "programmes": programmes,
        "modules": modules,
        "core_modules": core[:6],
    })


@explorer_bp.route("/api/explorer/programme/<code>")
@_with_edition
def api_programme(ed, code):
    code = code.strip().upper()
    p = ed.programmes.get(code)
    if not p:
        return jsonify({"error": f"There is no programme {code} in the {ed.year} handbook."}), 404
    _record_view(code)

    # curriculum: year -> semester -> modules (elective groups kept together)
    years = defaultdict(lambda: defaultdict(list))
    for r in ed.placements.get(code, []):
        m = ed.modules.get(r["module"], {})
        years[r["year"] or 0][r["semester"] or 0].append({
            "code": r["module"], "name": m.get("name") or r["module"], "credits": m.get("credits"),
            "type": r["type"], "group": r["group"], "choose": r["choose"],
            "prerequisites": m.get("prerequisites", []), "corequisites": m.get("corequisites", []),
        })
    curriculum = []
    for y in sorted(years):
        semesters, year_credits = [], 0
        for sem in sorted(years[y]):
            rows = years[y][sem]
            plain = [r for r in rows if not r["group"]]          # kept in handbook order
            groups = defaultdict(list)
            for r in rows:
                if r["group"]:
                    groups[r["group"]].append(r)
            group_list = []
            for g in sorted(groups):
                members = groups[g]
                choose = next((r["choose"] for r in members if r["choose"]), 1)
                group_list.append({"name": g, "choose": choose, "modules": members})
                credits = sorted((r["credits"] or 0 for r in members), reverse=True)
                year_credits += sum(credits[:choose])
            year_credits += sum(r["credits"] or 0 for r in plain)
            semesters.append({"semester": sem, "modules": plain, "groups": group_list})
        curriculum.append({"year": y, "semesters": semesters, "credits": year_credits})

    detail = dict(p)
    detail.update({
        "edition": _edition_info(ed),
        "admission": ed.admission_list(code),
        "curriculum": curriculum,
        "subject_slugs": [slug(s) for s in p["subjects"]],
    })
    return jsonify(detail)


@explorer_bp.route("/api/explorer/module/<code>")
@_with_edition
def api_module(ed, code):
    code = code.strip().upper()
    m = ed.modules.get(code)
    if not m:
        return jsonify({"error": f"There is no module {code} in the {ed.year} handbook."}), 404
    rows = ed.module_programmes.get(code, [])
    found, counts = [], {"Compulsory": 0, "Major": 0, "Elective": 0}
    for r in sorted(rows, key=lambda r: r["programme"]):
        p = ed.programmes.get(r["programme"])
        if not p:
            continue
        note = None
        if r["group"]:
            note = f"Choose {r['choose'] or 1} from group {r['group']}"
        if (r["year"], r["semester"]) != (m["year"], m["semester"]) and r["year"]:
            where = f"Year {r['year']} · Semester {r['semester']}"
            note = f"{note} · {where}" if note else f"Taken in {where}"
        found.append({"code": p["code"], "name": p["name"], "type": r["type"], "note": note,
                      "programme_type": p["type"],
                      "not_offered": p["not_offered"], "subject_slug": slug(p["subjects"][0]) if p["subjects"] else None})
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    subject = next((s for s in ed.subjects.values() if s["prefix"] and code.startswith(s["prefix"])), None)
    detail = dict(m)
    detail.update({
        "edition": _edition_info(ed),
        "prerequisites": [ed.module_card(c) for c in sorted(m["prerequisites"])],
        "corequisites": [ed.module_card(c) for c in sorted(m["corequisites"])],
        "leads_to": [ed.module_card(c) for c in sorted(ed.leads_to.get(code, []))],
        "programmes": found,
        "type_counts": counts,
        "subject": {"slug": subject["slug"], "name": subject["name"]} if subject else None,
    })
    return jsonify(detail)


@explorer_bp.route("/api/explorer/search")
@_with_edition
def api_search(ed):
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"edition": _edition_info(ed), "query": q, "programmes": [], "modules": [], "subjects": []})
    code = re.sub(r"\s+", "", q).upper()
    # an exact code: the page opens it straight away (the lists below still include it)
    exact = None
    if MODULE_CODE.match(code) and code in ed.modules:
        exact = {"kind": "module", "code": code}
    elif PROGRAMME_CODE.match(code) and code in ed.programmes:
        exact = {"kind": "programme", "code": code}

    words = [w for w in re.split(r"\s+", q.lower()) if w]

    def hit(*texts):
        hay = " ".join(t.lower() for t in texts if t)
        return all(w in hay for w in words)

    subjects = [{"slug": s["slug"], "name": s["name"], "icon": s["icon"], "count": len(s["programmes"])}
                for s in sorted(ed.subjects.values(), key=lambda s: s["name"]) if hit(s["name"])]
    programmes = [ed.programme_card(c) for c in sorted(ed.programmes)
                  if hit(c, ed.programmes[c]["name"], ed.programmes[c]["department"])]
    modules = [ed.module_card(c) for c in sorted(ed.modules,
               key=lambda c: (ed.modules[c]["year"] or 9, ed.modules[c]["semester"] or 9, c))
               if hit(c, ed.modules[c]["name"])]
    return jsonify({"edition": _edition_info(ed), "query": q, "exact": exact, "subjects": subjects,
                    "programmes": programmes, "modules": modules[:60], "module_total": len(modules)})
