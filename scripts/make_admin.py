# give (or take away) the admin role.
import os
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)  # so services/ can be imported
load_dotenv(os.path.join(ROOT, ".env"))

from services import Chatbot   # noqa: E402  (reads the database settings)
from services import accounts  # noqa: E402


def main(args):
    accounts.init_pool(Chatbot.DB_CONFIG)
    accounts.ensure_schema()
    if not args or args[0] in ("-h", "--help"):
        print("python scripts/make_admin.py EMAIL            make this account an admin")
        print("python scripts/make_admin.py EMAIL --remove   make it a student account again")
        print("python scripts/make_admin.py --list           show every admin")
        return 0
    if args[0] == "--list":
        with accounts._cursor() as cur:
            cur.execute("SELECT email, full_name FROM users WHERE role = 'admin' ORDER BY email")
            rows = cur.fetchall()
        print("\n".join(f"  {e}  ({n})" for e, n in rows) or "  No admins yet.")
        return 0
    email = args[0].strip().lower()
    role = "student" if "--remove" in args else "admin"
    with accounts._cursor() as cur:
        cur.execute("UPDATE users SET role = %s WHERE lower(email) = %s RETURNING full_name", (role, email))
        row = cur.fetchone()
    if not row:
        print(f"No account with the email {email}. Register it on the sign-in page first.")
        return 1
    print(f"{row[0]} ({email}) is now {'an admin' if role == 'admin' else 'a student account'}.")
    if os.environ.get("ADMIN_EMAILS") is not None:
        print("Note: ADMIN_EMAILS is set, so the server will apply that list again when it starts.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
