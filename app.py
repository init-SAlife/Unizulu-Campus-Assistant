import os
import secrets
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, redirect, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

import Chatbot   # noqa: E402  (after load_dotenv: reads the database and Claude settings)
import accounts  # noqa: E402
from auth_routes import auth_bp, current_user  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
# The browser must always check for the newest html/js/css instead of reusing
# an old copy, so changes to the files in static/ show up after a reload.
# (Files that have not changed still load quickly: the server just answers
# "not modified".)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# The login cookie is signed with FLASK_SECRET_KEY. Set it in .env so users
# stay signed in when the server restarts; without it a random key is made
# at start-up and everyone has to sign in again after every restart.
app.secret_key = os.environ.get("FLASK_SECRET_KEY")
if not app.secret_key:
    app.secret_key = secrets.token_hex(32)
    print("[auth] FLASK_SECRET_KEY is not set in .env -- using a temporary key; "
          "everyone will be signed out when the server restarts.")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,       # page scripts can't read the cookie
    SESSION_COOKIE_SAMESITE="Lax",      # other sites can't send requests as the user
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true",  # set true when served over HTTPS
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)


# users, chats and chat messages are stored in the database (accounts.py)
accounts.init_pool(Chatbot.DB_CONFIG)
accounts.ensure_schema()

from chatbot_routes import chatbot_bp        # noqa: E402  AI Assistant
from navigation_routes import navigation_bp  # noqa: E402  Campus Navigator

app.register_blueprint(auth_bp)
app.register_blueprint(chatbot_bp)
app.register_blueprint(navigation_bp)


@app.after_request
def no_stale_pages(response):
    # the pages themselves (index.html, login.html, forgot-password.html) are
    # never kept by the browser, so it always sees the newest version
    if response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/")
def serve_index():
    # open to everyone: guests get the Campus Navigator and a few AI
    # Assistant questions (see guest.py); signing in unlocks the rest
    return send_from_directory(app.static_folder, "index.html")


@app.route("/login")
def serve_login():
    if current_user():
        return redirect("/")
    return send_from_directory(app.static_folder, "login.html")


@app.route("/forgot-password")
def serve_forgot_password():
    # the "Forgotten password?" link on the sign-in page opens this page
    return send_from_directory(app.static_folder, "forgot-password.html")


if __name__ == "__main__":
    print("UNIZULU Campus AI Assistant running at http://localhost:8000")
    app.run(debug=True, port=8000)