"""Explicit SMTP submission for already-rendered invoice PDFs."""
from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
import os
from pathlib import Path
import re
import smtplib
import ssl


_EMAIL = re.compile(r"^[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+$")


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    tls_mode: str
    sender: str
    password_env: str = "INVOICEFLOW_SMTP_PASSWORD"
    timeout: float = 20.0

    def validate(self):
        if not self.host.strip() or any(ch in self.host for ch in "\r\n"):
            raise ValueError("Configure a valid SMTP host first.")
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("SMTP port must be from 1 to 65535.")
        if self.tls_mode not in {"none", "starttls", "ssl"}:
            raise ValueError("SMTP TLS mode must be none, starttls, or ssl.")
        if not _EMAIL.fullmatch(self.sender):
            raise ValueError("Configure a valid sender email address.")
        if not self.password_env or not self.password_env.replace("_", "").isalnum():
            raise ValueError("SMTP password environment variable name is invalid.")
        return self


def send_invoice_pdf(config: SMTPConfig, recipient: str, pdf_path: str | Path, *, subject: str, body: str) -> str:
    """Submit one attachment. A returned message means accepted by SMTP, not read by recipient."""
    config.validate()
    if not _EMAIL.fullmatch(recipient):
        raise ValueError("Enter a valid recipient email address.")
    path = Path(pdf_path)
    if not path.is_file() or path.suffix.casefold() != ".pdf":
        raise ValueError("Choose an existing PDF invoice to attach.")
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = recipient
    message["Subject"] = subject[:200]
    message.set_content(body[:10000])
    message.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=path.name)
    password = os.environ.get(config.password_env)
    context = ssl.create_default_context()
    if config.tls_mode == "ssl":
        with smtplib.SMTP_SSL(config.host, config.port, timeout=config.timeout, context=context) as transport:
            if password:
                transport.login(config.sender, password)
            transport.send_message(message)
    else:
        with smtplib.SMTP(config.host, config.port, timeout=config.timeout) as transport:
            transport.ehlo()
            if config.tls_mode == "starttls":
                transport.starttls(context=context)
                transport.ehlo()
            if password:
                transport.login(config.sender, password)
            transport.send_message(message)
    return "SMTP accepted the invoice message; recipient delivery and reading are unverified."
