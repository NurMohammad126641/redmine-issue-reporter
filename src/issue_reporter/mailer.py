"""Send the HTML report with CSV attachments over SMTP (STARTTLS)."""

from __future__ import annotations

import smtplib
from collections.abc import Sequence
from email.message import EmailMessage
from pathlib import Path

from issue_reporter.settings import MailSettings


def build_message(settings: MailSettings, subject: str, html: str,
                  attachments: Sequence[Path] = ()) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.sender
    msg["To"] = ", ".join(settings.recipients)
    msg.set_content("This report is best viewed in an HTML-capable email client.")
    msg.add_alternative(html, subtype="html")
    for path in attachments:
        if path.suffix.lower() != ".csv":
            continue
        msg.add_attachment(
            path.read_bytes(), maintype="text", subtype="csv", filename=path.name
        )
    return msg


def send(settings: MailSettings, message: EmailMessage) -> None:
    with smtplib.SMTP(settings.host, settings.port, timeout=30) as smtp:
        smtp.starttls()
        if settings.user:
            smtp.login(settings.user, settings.password)
        smtp.send_message(message)
