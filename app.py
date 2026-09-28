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


@app.route("/")
def serve_index():
    if not current_user():
        return redirect("/login")
    return send_from_directory(app.static_folder, "index.html")


@app.route("/login")
def serve_login():
    if current_user():
        return redirect("/")
    return send_from_directory(app.static_folder, "login.html")


if __name__ == "__main__":
    print("UNIZULU Campus AI Assistant running at http://localhost:8000")
    app.run(debug=True, port=8000)
