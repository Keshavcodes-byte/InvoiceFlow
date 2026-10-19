from email import message_from_bytes
from unittest.mock import MagicMock

import pytest

from backend.email_dispatch import SMTPConfig, send_invoice_pdf


def test_smtp_dispatch_uses_tls_and_attaches_pdf_without_live_network(tmp_path, monkeypatch):
    import backend.email_dispatch as dispatch

    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"%PDF-synthetic")
    transport = MagicMock()
    transport.__enter__.return_value = transport
    monkeypatch.setattr(dispatch.smtplib, "SMTP", lambda *args, **kwargs: transport)
    monkeypatch.setenv("TEST_INVOICEFLOW_SECRET", "do-not-log")
    status = send_invoice_pdf(
        SMTPConfig("smtp.example.test", 587, "starttls", "billing@example.test", "TEST_INVOICEFLOW_SECRET"),
        "client@example.test", pdf, subject="Invoice INV-1", body="Please see attached.",
    )
    assert "recipient delivery and reading are unverified" in status
    transport.starttls.assert_called_once()
    transport.send_message.assert_called_once()
    message = transport.send_message.call_args.args[0]
    assert message["To"] == "client@example.test"
    attachment = list(message.iter_attachments())[0]
    assert attachment.get_filename() == "invoice.pdf"
    assert attachment.get_payload(decode=True) == b"%PDF-synthetic"


@pytest.mark.parametrize("recipient", ["bad", "x@", "a b@example.test"])
def test_smtp_dispatch_rejects_invalid_recipient_without_transport(tmp_path, monkeypatch, recipient):
    import backend.email_dispatch as dispatch
    transport = MagicMock()
    monkeypatch.setattr(dispatch.smtplib, "SMTP", transport)
    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(ValueError, match="recipient email"):
        send_invoice_pdf(SMTPConfig("smtp.example.test", 25, "none", "from@example.test"), recipient, pdf, subject="x", body="y")
    transport.assert_not_called()


def test_smtp_dispatch_requires_existing_pdf(tmp_path):
    with pytest.raises(ValueError, match="existing PDF"):
        send_invoice_pdf(SMTPConfig("smtp.example.test", 25, "none", "from@example.test"), "to@example.test", tmp_path / "missing.pdf", subject="x", body="y")
