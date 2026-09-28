"""
Sending emails (the "Forgotten password?" codes).

Two ways to send, chosen by what is set in .env / Render's Environment:

  1. Brevo (recommended on Render -- it sends over HTTPS, which Render's free
     plan allows; Render's free plan BLOCKS normal SMTP email ports):
         BREVO_API_KEY    your Brevo API key
         MAIL_FROM        the sender email you verified in Brevo

  2. SMTP, e.g. Gmail with an app password (works on your own PC and on
     paid Render plans, NOT on Render's free plan):
         SMTP_HOST        smtp.gmail.com
         SMTP_PORT        587
         SMTP_USER        you@gmail.com
         SMTP_PASSWORD    the 16-letter Gmail app password
         MAIL_FROM        you@gmail.com   (optional, defaults to SMTP_USER)

  Optional:  MAIL_FROM_NAME  (default "UNIZULU Campus AI Assistant")

If neither is set and the app is running on your own PC, the email is not
sent -- it is printed in the terminal instead, so you can still test.
"""

import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage


class EmailNotSent(RuntimeError):
    """The email could not be sent (the reason is in the message, for the server log)."""


def _sender():
    name = os.environ.get("MAIL_FROM_NAME", "UNIZULU Campus AI Assistant")
    address = os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER") or ""
    return name, address


def email_is_configured():
    return bool(os.environ.get("BREVO_API_KEY") or os.environ.get("SMTP_HOST"))


def running_on_server():
    # Render sets RENDER=true on every service
    return os.environ.get("RENDER", "").lower() == "true"


def send_email(to, subject, text, html):
    if os.environ.get("BREVO_API_KEY"):
        _send_with_brevo(to, subject, text, html)
    elif os.environ.get("SMTP_HOST"):
        _send_with_smtp(to, subject, text, html)
    elif running_on_server():
        raise EmailNotSent("No email service is set up: add BREVO_API_KEY and MAIL_FROM in Render's Environment.")
    else:
        print("\n" + "=" * 70)
        print(f"[email] NOT SENT (no email service set up in .env). It would go to {to}:")
        print(f"[email] Subject: {subject}")
        print(text)
        print("=" * 70 + "\n")


def _send_with_brevo(to, subject, text, html):
    name, address = _sender()
    if not address:
        raise EmailNotSent("MAIL_FROM is not set (the sender address you verified in Brevo).")
    body = json.dumps({
        "sender": {"name": name, "email": address},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": text,
        "htmlContent": html,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=body,
        method="POST",
        headers={
            "api-key": os.environ["BREVO_API_KEY"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status >= 300:
                raise EmailNotSent(f"Brevo answered {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise EmailNotSent(f"Brevo refused the email ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise EmailNotSent(f"Could not reach Brevo: {exc.reason}") from exc


def _send_with_smtp(to, subject, text, html):
    name, address = _sender()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{name} <{address}>"
    message["To"] = to
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.starttls(context=ssl.create_default_context())
        with server:
            if user:
                server.login(user, password)
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailNotSent(f"SMTP error: {exc}") from exc


def reset_code_email(full_name, code, minutes):
    """Subject, plain text and HTML for the password-reset code email."""
    first = (full_name or "").strip().split(" ")[0] or "there"
    subject = f"{code} is your UNIZULU Campus AI Assistant code"
    text = (
        f"Sawubona {first},\n\n"
        f"Your code to reset your password is: {code}\n\n"
        f"It works for {minutes} minutes. If you did not ask to reset your password, "
        f"you can ignore this email -- your password stays the same.\n\n"
        f"UNIZULU Campus AI Assistant"
    )
    html = f"""\
<div style="font-family:Segoe UI,Roboto,Arial,sans-serif;background:#f5f8fd;padding:24px">
  <div style="max-width:440px;margin:0 auto;background:#ffffff;border-radius:14px;padding:28px;border:1px solid #d8e2f0">
    <p style="margin:0 0 4px;font-size:12px;letter-spacing:2px;font-weight:700;color:#0b3d91">UNIZULU</p>
    <h1 style="margin:0 0 18px;font-size:20px;color:#0e1e3c">Reset your password</h1>
    <p style="margin:0 0 16px;color:#3a4d70;font-size:15px">Sawubona {first}, use this code to reset your password:</p>
    <p style="margin:0 0 16px;font-size:34px;font-weight:700;letter-spacing:10px;color:#0b3d91;text-align:center;
              background:#e7eefa;border-radius:10px;padding:14px 0">{code}</p>
    <p style="margin:0 0 6px;color:#3a4d70;font-size:14px">It works for {minutes} minutes.</p>
    <p style="margin:0;color:#6a7b98;font-size:13px">If you did not ask to reset your password, you can ignore this email.
       Your password stays the same.</p>
  </div>
</div>"""
    return subject, text, html
