import os
from datetime import date

from flask import request, session

from services import accounts


def _limit(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


PER_DAY = _limit("GUEST_QUESTIONS_PER_DAY", 10)
PER_IP_HOUR = _limit("GUEST_QUESTIONS_PER_IP_HOUR", 40)
PER_HOUR_ALL = _limit("GUEST_QUESTIONS_PER_HOUR", 200)

SIGN_IN_PROMPT = "Sign in or create a free account to keep asking questions and to save your chats."


def client_ip():
    # The visitor's network address.
    if _on_render():
        for header in ("CF-Connecting-IP", "True-Client-IP"):
            if request.headers.get(header):
                return request.headers[header].strip()
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _on_render():
    return os.environ.get("RENDER", "").lower() == "true"


def _today_state():
    # This browser's guest state for today
    state = session.get("guest") or {}
    today = date.today().isoformat()
    if state.get("day") != today:
        state = {"day": today, "count": 0, "chat": state.get("chat")}
    return state


def status():
    # What the page shows: how many questions this guest has left today.
    used = _today_state()["count"]
    return {"limit": PER_DAY, "questions_left": max(0, PER_DAY - used)}


def current_chat_id():
    return session.get("guest", {}).get("chat")


def check_and_count():
    # Call before answering a guest question.
    state = _today_state()
    if state["count"] >= PER_DAY:
        return f"You've used all {PER_DAY} guest questions for today. {SIGN_IN_PROMPT}"
    blocked = accounts.record_guest_question(accounts.hash_ip(client_ip()), PER_IP_HOUR, PER_HOUR_ALL)
    if blocked == "busy":
        return f"Guest mode is very busy right now, so please try again later. {SIGN_IN_PROMPT}"
    if blocked == "network":
        return f"A lot of guest questions have come from your network in the last hour. {SIGN_IN_PROMPT}"
    state["count"] += 1
    session["guest"] = state
    return None


def remember_chat(chat_id):
    state = _today_state()
    state["chat"] = chat_id
    session["guest"] = state
