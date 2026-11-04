import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage


class EmailNotSent(RuntimeError):
    # the email could not be sent (the reason is in the message, for the server log)
    pass


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
        try:
            _send_with_brevo(to, subject, text, html)
        except EmailNotSent as exc:
            # Brevo failed: try SMTP as the backup when it is set up
            if not os.environ.get("SMTP_HOST"):
                raise
            print(f"[email] Brevo failed ({exc}), trying SMTP")
            try:
                _send_with_smtp(to, subject, text, html)
            except EmailNotSent as smtp_exc:
                raise EmailNotSent(f"Brevo: {exc} / SMTP: {smtp_exc}") from smtp_exc
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
    # subject, plain text and HTML for the password-reset code email
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


# admin requests
def _card(title, body_html):
    return f"""\
<div style="font-family:Segoe UI,Roboto,Arial,sans-serif;background:#f5f8fd;padding:24px">
  <div style="max-width:440px;margin:0 auto;background:#ffffff;border-radius:14px;padding:28px;border:1px solid #d8e2f0">
    <p style="margin:0 0 4px;font-size:12px;letter-spacing:2px;font-weight:700;color:#0b3d91">UNIZULU</p>
    <h1 style="margin:0 0 18px;font-size:20px;color:#0e1e3c">{title}</h1>
    {body_html}
  </div>
</div>"""


def _first(full_name):
    return html_escape((full_name or "").strip().split(" ")[0] or "there")


def html_escape(text):
    import html
    return html.escape(str(text or ""))


def account_code_email(full_name, code, minutes, purpose):
    # The 6-digit code for creating an account ("signup") or signing in ("login").
    first = _first(full_name)
    if purpose == "signup":
        subject = f"{code} is your code to create your account"
        title, line = "Confirm your email", "use this code to finish creating your account:"
        ignore = "If you did not try to create an account, you can ignore this email."
    else:
        subject = f"{code} is your sign-in code"
        title, line = "Your sign-in code", "use this code to sign in:"
        ignore = "If you did not try to sign in, someone may know your password. Please change it."
    text = (f"Sawubona {first},\n\nYour code is: {code}\n\nIt works for {minutes} minutes.\n{ignore}\n\n"
            f"UNIZULU Campus AI Assistant")
    html = _card(title, f"""
    <p style="margin:0 0 16px;color:#3a4d70;font-size:15px">Sawubona {first}, {line}</p>
    <p style="margin:0 0 16px;font-size:34px;font-weight:700;letter-spacing:10px;color:#0b3d91;text-align:center;
              background:#e7eefa;border-radius:10px;padding:14px 0">{code}</p>
    <p style="margin:0 0 6px;color:#3a4d70;font-size:14px">It works for {minutes} minutes.</p>
    <p style="margin:0;color:#6a7b98;font-size:13px">{ignore}</p>""")
    return subject, text, html


def admin_request_decision_email(full_name, approved):
    first = _first(full_name)
    if approved:
        subject = "Your admin request was approved"
        line = "Your request for an administrator account was approved. Sign in to open the admin dashboard."
    else:
        subject = "Your admin request was not approved"
        line = ("Your request for an administrator account was not approved. You can still use the Campus AI "
                "Assistant with your account. Contact the system administrator if you think this is a mistake.")
    text = f"Sawubona {first},\n\n{line}\n\nUNIZULU Campus AI Assistant"
    html = _card(subject, f'<p style="margin:0;color:#3a4d70;font-size:15px">Sawubona {first}, {html_escape(line[0].lower() + line[1:])}</p>')
    return subject, text, html


def new_admin_request_email(name, email, reason):
    # Tells the existing admins that a request is waiting for them.
    subject = "New admin request waiting for approval"
    text = (f"{name} ({email}) asked for an administrator account.\n"
            f"Role / reason: {reason or '-'}\n\nOpen the admin dashboard -> Admin Requests to approve or reject it.")
    html = _card(subject, f"""
    <p style="margin:0 0 10px;color:#3a4d70;font-size:15px"><b>{html_escape(name)}</b> ({html_escape(email)})
       asked for an administrator account.</p>
    <p style="margin:0 0 10px;color:#3a4d70;font-size:14px">Role / reason: {html_escape(reason or '-')}</p>
    <p style="margin:0;color:#6a7b98;font-size:13px">Open the admin dashboard &rarr; Admin Requests to approve or reject it.</p>""")
    return subject, text, html
