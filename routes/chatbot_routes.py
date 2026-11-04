import os
import tempfile
from datetime import date

from flask import Blueprint, g, jsonify, request, session
from werkzeug.utils import secure_filename

from services import Chatbot, accounts, guest
from routes.auth_routes import current_user, login_required

chatbot_bp = Blueprint("chatbot", __name__)

ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}

# a chat without a title is offered to Claude for naming on each of its
# first few messages; if it's still only small talk by then, it's named anyway
TITLE_FORCE_AFTER_MESSAGES = 3

# start-up Chatbot.py only READS handbook data on its connection; autocommit means every read is
# its own transaction
Chatbot.conn.autocommit = True
# In the web app each chat's memory lives in the database, not in the
# single conversation_memory.json file used by terminal test mode.
Chatbot.PERSIST_TO_FILE = False

# loaded once when the server starts, same as `known_codes = get_programme_names()`
# in Chatbot.py's own __main__ block
known_codes = Chatbot.get_programme_names()


#helpers
def load_chat(chat_id):
    # The signed-in user's chat with its memory, or a brand-new unsaved chat when no chat_id is
    # given.
    if chat_id:
        chat = accounts.get_chat(g.user["id"], chat_id)
        if not chat:
            return None, (jsonify({"error": "That chat was not found."}), 404)
        chat["context"] = Chatbot.complete_context(chat["context"])
        return chat, None
    return {"id": None, "title": None, "context": Chatbot.new_context()}, None


def save_chat(chat, new_messages):
    # Creates the chat if it's new, stores the new messages and its updated memory, then names
    # it with Claude if it has no title yet.
    if chat["id"] is None:
        created = accounts.create_chat(g.user["id"], chat["context"])
        chat["id"] = created["id"]
    accounts.save_exchange(g.user["id"], chat["id"], chat["context"], new_messages)

    if not chat["title"]:
        messages = accounts.get_messages(g.user["id"], chat["id"])
        questions = [m["content"] for m in messages if m["role"] == "user"]
        answers = [m["content"] for m in messages if m["role"] == "assistant"]
        exchanges = list(zip(questions, answers + [""] * len(questions)))[:TITLE_FORCE_AFTER_MESSAGES]
        force = len(questions) >= TITLE_FORCE_AFTER_MESSAGES
        title = Chatbot.generate_session_title(exchanges, force=force)
        if title:
            chat["title"] = accounts.set_title(g.user["id"], chat["id"], title)
    return chat


def handbook_of(context):
    edition = Chatbot.get_edition(context.get("edition_id"))
    return {"edition_id": edition["id"], "year": edition["year"], "label": edition["label"]} if edition else None


def chat_payload(chat):
    return {"chat_id": chat["id"], "title": chat["title"], "handbook": handbook_of(chat["context"])}


#chats
@chatbot_bp.route("/chats", methods=["GET"])
@login_required
def chats_list():
    return jsonify({"chats": accounts.list_chats(g.user["id"])})


@chatbot_bp.route("/chats/<chat_id>", methods=["GET"])
@login_required
def chats_open(chat_id):
    chat = accounts.get_chat(g.user["id"], chat_id)
    if not chat:
        return jsonify({"error": "That chat was not found."}), 404
    return jsonify({**chat_payload({**chat, "context": Chatbot.complete_context(chat["context"])}),
                    "messages": accounts.get_messages(g.user["id"], chat["id"])})


@chatbot_bp.route("/chats/<chat_id>", methods=["PATCH"])
@login_required
def chats_rename(chat_id):
    data = request.get_json(silent=True) or {}
    try:
        title = accounts.set_title(g.user["id"], chat_id, data.get("title"))
    except accounts.AccountError as exc:
        return jsonify({"error": str(exc)}), 400
    if title is None:
        return jsonify({"error": "That chat was not found."}), 404
    return jsonify({"chat_id": chat_id, "title": title})


@chatbot_bp.route("/chats/<chat_id>", methods=["DELETE"])
@login_required
def chats_delete(chat_id):
    if not accounts.delete_chat(g.user["id"], chat_id):
        return jsonify({"error": "That chat was not found."}), 404
    return jsonify({"deleted": chat_id})


#chatbot
@chatbot_bp.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()

    if not question:
        return jsonify({"error": "No question provided."}), 400

    user = current_user()
    if not user:
        return guest_chat(question, data.get("chat_id"))
    g.user = user

    conversation, error = load_chat(data.get("chat_id"))
    if error:
        return error

    try:
        answer = Chatbot.answer_question(question, known_codes, conversation["context"])
    except Exception as e:
        # don't let a Chatbot.py-side error take the whole server down --
        # surface something the frontend can display instead
        print(f"[chat] error answering question: {e}")
        return jsonify({"error": "Something went wrong answering that question."}), 500

    try:
        # "Regenerate" under the newest answer: the new answer replaces the old one
        if data.get("regenerate") and conversation["id"]:
            accounts.drop_last_exchange(g.user["id"], conversation["id"], question)
        conversation = save_chat(conversation, [("user", question), ("assistant", answer)])
    except Exception as e:
        print(f"[chat] error saving the chat: {e}")
        return jsonify({"answer": answer, "chat_id": conversation["id"], "title": conversation["title"],
                        "warning": "This answer could not be saved to your chat history."})

    return jsonify({"answer": answer, **chat_payload(conversation)})


# Questions a guest can't get answered: they need the student's own uploaded documents, and
# uploading needs an account.
GUEST_REGISTRATION_REPLY = (
    "I'm sorry, I can't verify your registration because you are not signed in. "
    "To check your registration I need your proof of registration, and uploading "
    "documents is only available with an account.\n\n"
    "Sign in or create a free account, upload your proof of registration, "
    "and then ask me again."
)
GUEST_RECORD_REPLY = (
    "I'm sorry, I can't check your results or progress because you are not signed in. "
    "For that I need your academic record, and uploading documents is only "
    "available with an account.\n\n"
    "Sign in or create a free account, upload your academic record, and then ask me again. "
    "In guest mode I can still answer general questions about programmes and modules."
)


def guest_cannot_answer(question):
    # The reply for a question that needs a signed-in student, or None.
    if Chatbot.is_registration_check_question(question):
        return GUEST_REGISTRATION_REPLY
    if Chatbot.is_progress_question(question):
        return GUEST_RECORD_REPLY
    return None


def guest_chat(question, chat_id):
    # A question from someone who is not signed in.
    needs_account = guest_cannot_answer(question)
    if needs_account:
        # keep the chat as it was (same chat, same handbook)
        same = chat_id if chat_id and chat_id == guest.current_chat_id() else None
        kept = accounts.get_guest_chat(same) if same else None
        return jsonify({"answer": needs_account, "chat_id": kept["id"] if kept else None, "title": None,
                        "handbook": handbook_of(kept["context"]) if kept else None,
                        "signin_prompt": True, "guest": guest.status()})

    try:
        blocked = guest.check_and_count()
    except Exception as e:
        print(f"[chat] error checking guest limits: {e}")
        return jsonify({"error": "Something went wrong. Please try again."}), 500
    if blocked:
        return jsonify({"error": blocked, "signin_prompt": True, "guest": guest.status()}), 429

    # continue this browser's guest chat only; any other id starts a new one
    conversation = None
    if chat_id and chat_id == guest.current_chat_id():
        conversation = accounts.get_guest_chat(chat_id)
    context = Chatbot.complete_context(conversation["context"]) if conversation else Chatbot.new_context()

    try:
        answer = Chatbot.answer_question(question, known_codes, context)
    except Exception as e:
        print(f"[chat] error answering guest question: {e}")
        return jsonify({"error": "Something went wrong answering that question.",
                        "guest": guest.status()}), 500

    new_id = conversation["id"] if conversation else None
    try:
        new_id = accounts.save_guest_chat(new_id, context)
        guest.remember_chat(new_id)
    except Exception as e:
        print(f"[chat] error saving guest chat: {e}")

    return jsonify({"answer": answer, "chat_id": new_id, "title": None,
                    "handbook": handbook_of(context), "guest": guest.status()})


FEEDBACK_PER_DAY = 200     # ratings one browser may send in a day (stops a script flooding the table)


@chatbot_bp.route("/chat/feedback", methods=["POST"])
def chat_feedback():
    # The thumbs button under an answer
    data = request.get_json(silent=True) or {}
    rating = str(data.get("rating") or "").lower()
    answer = str(data.get("answer") or "").strip()
    if rating not in ("good", "bad") or not answer:
        return jsonify({"error": "A rating (good or bad) and the answer are needed."}), 400

    today = date.today().isoformat()
    count = session.get("feedback") or {}
    if count.get("day") != today:
        count = {"day": today, "n": 0}
    if count["n"] >= FEEDBACK_PER_DAY:
        return jsonify({"error": "Thanks -- you have sent a lot of ratings today."}), 429
    count["n"] += 1
    session["feedback"] = count

    user = current_user()
    chat_id = str(data.get("chat_id") or "")[:64] or None
    try:
        accounts.save_feedback(user["id"] if user else None, chat_id,
                               str(data.get("question") or "")[:4000] or None, answer[:20000], rating)
    except Exception as exc:
        print(f"[feedback] could not save a rating: {exc}")
        return jsonify({"error": "Your rating could not be saved. Please try again."}), 500
    return jsonify({"ok": True})


@chatbot_bp.route("/upload", methods=["POST"])
@login_required
def upload_documents():
    document_type = (request.form.get("document_type") or "").strip().lower()
    if document_type not in {"academic_record", "proof_of_registration"}:
        return jsonify({"error": "Choose an academic record or proof of registration."}), 400

    files = [file for file in request.files.getlist("files") if file and file.filename]
    if not files:
        return jsonify({"error": "Select at least one PDF or image file."}), 400

    conversation, error = load_chat(request.form.get("chat_id"))
    if error:
        return error
    context = conversation["context"]

    temporary_paths = []
    try:
        for file in files:
            extension = os.path.splitext(file.filename)[1].lower()
            if extension not in ALLOWED_UPLOAD_EXTENSIONS:
                return jsonify({"error": "Only PDF, PNG, JPG, and JPEG files are supported."}), 400

            safe_name = secure_filename(file.filename) or "document"
            handle = tempfile.NamedTemporaryFile(
                prefix="campus_ai_", suffix=os.path.splitext(safe_name)[1], delete=False
            )
            file.save(handle.name)
            handle.close()
            temporary_paths.append(handle.name)

        if document_type == "academic_record":
            extracted = Chatbot.extract_academic_record(temporary_paths, context)
            message = "Academic record uploaded and extracted. You can now ask about your results or registration."
        else:
            extracted = Chatbot.extract_proof_of_registration(temporary_paths, context)
            message = "Proof of registration uploaded and extracted. You can now ask about your registered modules."

            # proactively check right away -- if something can't be confirmed without an
            # academic record
            outcome = Chatbot.check_registration_from_context(
                context, fallback_programme_code=context.get("last_programme_code")
            )
            if outcome["status"] == "needs_edition":
                # the check needs a handbook first -- ask now, and hold a registration check so
                # that replying with a year in the chat runs it straight away
                context["awaiting_edition"] = True
                context["pending_question"] = {
                    "question": "Check my registration",
                    "detected": {"intent": "registration_check",
                                 "programme_code": context.get("last_programme_code"),
                                 "module_code": None, "programme_type": None},
                }
                message += " To check it against your programme, I need to know which handbook applies. " \
                           + outcome["message"]
            elif outcome["status"] == "ok":
                has_unresolved = any(
                    yr["missing_compulsory"] or yr["unsatisfied_electives"]
                    for yr in outcome["result"]["year_results"]
                )
                if has_unresolved and not outcome["result"]["academic_record_used"]:
                    message += (
                        " I can see a few things I can't fully confirm without your "
                        "academic record too -- feel free to upload that when you can."
                    )

        if extracted.get("extraction_status") == "low_confidence":
            message += (
                " Note: I had to use my backup reader for this document, so some "
                "modules or results may be missing or misread. If anything looks "
                "wrong, try uploading it again or a clearer copy."
            )
        if extracted.get("failed_files"):
            message += " Some files could not be processed."

        label = "academic record" if document_type == "academic_record" else "proof of registration"
        names = ", ".join(secure_filename(f.filename) or "document" for f in files)
        conversation = save_chat(conversation, [("user", f"📎 Uploaded {label}: {names}"),
                                                ("assistant", message)])
        # admin dashboard counts: every upload, and a proof of registration is a registration check
        accounts.log_event("upload", g.user["id"], label)
        if document_type == "proof_of_registration":
            accounts.log_event("registration_check", g.user["id"], "Proof of registration uploaded")
        return jsonify({"message": message, "document_type": document_type, "data": extracted,
                        **chat_payload(conversation)})
    except Exception as exc:
        print(f"[upload] error processing documents: {exc}")
        return jsonify({"error": "The document could not be processed."}), 500
    finally:
        for path in temporary_paths:
            try:
                os.remove(path)
            except OSError:
                pass


def resolve_edition_for_request(data, context):
    # Which handbook edition a direct API request should use: an explicit "edition_id" or
    # "handbook_year" in the request body
    editions = Chatbot.get_handbook_editions()
    available = [{"edition_id": e["id"], "year": e["year"], "label": e["label"]} for e in editions]

    if data.get("edition_id") is not None or data.get("handbook_year") is not None:
        for e in editions:
            if (data.get("edition_id") is not None and str(e["id"]) == str(data["edition_id"])) or \
               (data.get("handbook_year") is not None and str(e["year"]) == str(data["handbook_year"])):
                return e, None
        return None, (jsonify({"error": "That handbook edition does not exist.",
                               "available_editions": available}), 400)

    edition_id = Chatbot.get_valid_edition_id(context)
    if edition_id is None:
        return None, (jsonify({"error": "Choose a handbook edition: send edition_id or handbook_year.",
                               "available_editions": available}), 400)
    return Chatbot.get_edition(edition_id), None


@chatbot_bp.route("/handbooks", methods=["GET"])
@login_required
def handbooks():
    # The handbook editions the chatbot can answer from, plus the one the given chat
    # (?chat_id=...) is using (null if none chosen yet).
    current = None
    chat_id = request.args.get("chat_id")
    if chat_id:
        conversation = accounts.get_chat(g.user["id"], chat_id)
        if not conversation:
            return jsonify({"error": "That chat was not found."}), 404
        current = Chatbot.get_edition(conversation["context"].get("edition_id"))
    return jsonify({
        "editions": [{"edition_id": e["id"], "year": e["year"], "label": e["label"]}
                     for e in Chatbot.get_handbook_editions()],
        "current_edition_id": current["id"] if current else None,
    })


@chatbot_bp.route("/registration-check", methods=["POST"])
@login_required
def registration_check():
    data = request.get_json(silent=True) or {}
    programme_code = (data.get("programme_code") or "").strip().upper()
    module_codes = [str(code).strip().upper() for code in data.get("module_codes", []) if str(code).strip()]

    if not programme_code or not module_codes:
        return jsonify({"error": "programme_code and module_codes are required."}), 400

    # optional: a chat whose uploaded academic record and handbook to use
    conversation, error = load_chat(data.get("chat_id"))
    if error:
        return error
    context = conversation["context"]

    edition, error = resolve_edition_for_request(data, context)
    if error:
        return error

    try:
        academic_record = Chatbot.get_cached_document(context, "academic_record")
        passes = Chatbot.build_academic_record_passes(academic_record) if academic_record else None
        result = Chatbot.check_full_registration(programme_code, module_codes, passes,
                                                 edition_id=edition["id"])
        result["handbook"] = {"edition_id": edition["id"], "year": edition["year"],
                              "label": edition["label"]}
        accounts.log_event("registration_check", g.user["id"], programme_code)
        return jsonify(result)
    except Exception as exc:
        print(f"[registration-check] error: {exc}")
        return jsonify({"error": "Registration could not be checked."}), 500
