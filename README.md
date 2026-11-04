# UNIZULU Campus AI Assistant

Group 30 final-year project. One Flask app with four parts:

* **AI Assistant**: answers questions from the FSAE handbooks (2026 and 2023) and checks registration from an uploaded proof of registration or academic record
* **Programme & Module Explorer**: browse programmes and modules, free for everyone
* **Campus Navigator**: walking, car + walk or car only routes on the KwaDlangezwa campus, with parking status
* **Admin dashboard** (`/admin`): usage, users, admin requests, feedback, admin sessions and audit log

## Folders

```
app.py              starts the app (run this)
routes/             the web routes (one file per part)
services/           Chatbot.py (the AI Assistant), accounts, guest limits, email
navigation/         campus map, route engine, parking status (map.osm, buildings.json)
database/           accounts_schema.sql (created on start) and the handbook loaders
static/             the frontend: html pages, css, js, images
templates/          admin.html (only served through /admin after the admin check)
scripts/            make_admin.py
documents/          sample handbooks and student documents for testing (not uploaded)
```

## Run it

```
pip install -r requirements.txt
copy .env.example .env      (then fill in your values)
python app.py
```

Open http://localhost:8000. The admin dashboard is at http://localhost:8000/admin.

## Accounts and sign-in

* **Create account:** name, Gmail address, account type (nothing is chosen for you) and password.
  Only `@gmail.com` addresses can be used, for students and admins, when signing up, signing in
  and resetting a password. The last step is the 6-digit code sent to that email; the account is
  only made once it is right.
* **Sign in:** password, then a 6-digit code by email. Tick "Remember this device" to skip the code
  on that browser for 5 days (students) or 1 day (admins). A new password or a disabled account
  forgets every remembered device.
* **Limits:** 5 code emails per address per day (sign-up, sign-in and forgotten password together),
  2 minutes between them, 10 minutes to use a code and 5 wrong tries. The page shows how many are
  left and counts down the wait. The daily count starts again at midnight.
* **Email:** Brevo (`BREVO_API_KEY` + a verified `MAIL_FROM`) with SMTP as the backup. Without
  either, emails are printed in the terminal (handy for testing on your own computer).

## Admins

* **Asking to be admin:** choose Administrator on Create account. The sign-up code already proves
  the email, so the request goes straight to Admin Requests, where an existing admin approves or
  rejects it. Until then the account works as a normal account.
* **Making an existing account an admin, with a script** (works straight away):
  `python scripts/make_admin.py name@gmail.com`. `--list` shows every admin and `--remove` takes
  the role away. It uses the database in `.env`, so point `.env` at Render's database to change the
  live site.
* **Or with `.env` / Render's Environment:** `ADMIN_EMAILS=name@gmail.com,other@gmail.com`. Those
  accounts become admins when the server starts, when they sign in, or as soon as they finish
  signing up. Any admin request they made is marked approved.

## Database

* `database/accounts_schema.sql`: users, chats, admin and sign-in tables. The app runs it every
  time it starts, so these are made for you.
* `database/handbook_schema.sql` + `database/handbook_data.sql`: the handbook tables and all their
  rows (2026 handbook = edition 1, 2023 prospectus = edition 2). To build a new database:

```
psql -d your_database -f database/handbook_schema.sql
psql -d your_database -f database/handbook_data.sql
python app.py
```

* `database/update_render_db.sql`: the one-off update for the existing Render database (data
  fixes, new sign-in tables, unused table removed).
* `database/handbook_2026/` and `database/handbook_2023/`: the PDFs, CSVs and scripts the handbook
  data was first extracted with. Only needed to extract a handbook again.

## Campus map

`navigation/buildings.json` is made from `navigation/map.osm` with
`python -m navigation.campus_graph` (add `--force` to overwrite it, which loses manual edits).
