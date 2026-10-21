"""End-to-end tests for InvoiceFlow's loopback web adapter."""
from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from backend import web_app
from backend.web_app import create_app


ORIGIN = "http://127.0.0.1:48765"


@pytest.fixture
def web(tmp_path):
    app = create_app(data_dir=tmp_path / "data", max_upload_bytes=2 * 1024 * 1024)
    with TestClient(app, base_url=ORIGIN) as client:
        client.get("/")
        headers = {"Origin": ORIGIN, "X-CSRF-Token": client.cookies.get("if_csrf")}
        yield client, headers, app.state.invoiceflow


def upload(client, headers, name, content, content_type="application/octet-stream"):
    return client.put(
        "/api/source", content=content,
        headers={**headers, "Content-Type": content_type, "X-File-Name": name},
    )


def validate(client, headers, mapping=None, **extra):
    return client.post("/api/validate", headers=headers, json={
        "mapping": mapping or {"invoice_number": "Invoice No", "name": "Customer Name", "amount": "Amount"},
        "options": {}, "template": "minimal", **extra,
    })


def wait_job(client, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(.02)
    pytest.fail("InvoiceFlow background operation did not finish")


def test_dashboard_has_nonce_csp_and_expected_controls(web):
    client, _, _ = web
    response = client.get("/")
    assert response.status_code == 200
    assert "Content-Security-Policy" in response.headers
    assert "nonce-" in response.headers["Content-Security-Policy"]
    assert "__CSP_NONCE__" not in response.text
    for label in ("Export Single PDF", "Export Batch", "Branding", "History", "JetBrains Mono"):
        assert label in response.text
    assert client.get("/api/health").json()["status"] == "ready"
    assert client.get("/api/output-folder").status_code == 200


def test_frozen_tk_paths_are_resolved_from_bundle_and_restored(tmp_path, monkeypatch):
    bundle = tmp_path / "_internal"
    tcl_dir = bundle / "tcl" / "tcl8.6"
    tk_dir = bundle / "tcl" / "tk8.6"
    tcl_dir.mkdir(parents=True)
    tk_dir.mkdir(parents=True)
    (tcl_dir / "init.tcl").write_text("# Tcl test library", encoding="utf-8")
    (tk_dir / "pkgIndex.tcl").write_text("# Tk test library", encoding="utf-8")
    bin_dir = bundle / "bin"
    bin_dir.mkdir()
    (bin_dir / "tk86t.dll").write_bytes(b"test")
    monkeypatch.setattr(web_app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(web_app.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("TCL_LIBRARY", "old-tcl-value")
    monkeypatch.setenv("TK_LIBRARY", "old-tk-value")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    old_path = os.environ["PATH"]

    previous = web_app._configure_bundled_tk_paths()

    assert Path(os.environ["TCL_LIBRARY"]) == tcl_dir
    assert Path(os.environ["TK_LIBRARY"]) == tk_dir
    assert os.environ["PATH"] == old_path
    web_app._restore_tk_paths(previous)
    assert os.environ["TCL_LIBRARY"] == "old-tcl-value"
    assert os.environ["TK_LIBRARY"] == "old-tk-value"


def test_frozen_tk_paths_fail_clearly_when_bundle_is_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(web_app.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("TCL_LIBRARY", "preserved-tcl")
    monkeypatch.setenv("TK_LIBRARY", "preserved-tk")

    with pytest.raises(RuntimeError, match="Tcl/Tk script directories"):
        web_app._configure_bundled_tk_paths()

    assert os.environ["TCL_LIBRARY"] == "preserved-tcl"
    assert os.environ["TK_LIBRARY"] == "preserved-tk"


def test_frozen_tk_paths_are_set_before_tkinter_import(tmp_path, monkeypatch):
    import tkinter
    from tkinter import filedialog

    bundle = tmp_path / "_internal"
    tcl_dir = bundle / "tcl" / "tcl8.6"
    tk_dir = bundle / "tcl" / "tk8.6"
    tcl_dir.mkdir(parents=True)
    tk_dir.mkdir(parents=True)
    (tcl_dir / "init.tcl").write_text("# Tcl test library", encoding="utf-8")
    (tk_dir / "pkgIndex.tcl").write_text("# Tk test library", encoding="utf-8")
    bin_dir = bundle / "bin"
    bin_dir.mkdir()
    (bin_dir / "tk86t.dll").write_bytes(b"test")
    monkeypatch.setattr(web_app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(web_app.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("TCL_LIBRARY", "old-tcl-value")
    monkeypatch.setenv("TK_LIBRARY", "old-tk-value")
    original_import = builtins.__import__
    paths_seen_at_import = []

    def inspect_tk_import(name, *args, **kwargs):
        if name == "tkinter":
            paths_seen_at_import.append((os.environ.get("TCL_LIBRARY"), os.environ.get("TK_LIBRARY")))
        return original_import(name, *args, **kwargs)

    class FakeRoot:
        def withdraw(self):
            pass

        def attributes(self, *_args):
            pass

        def destroy(self):
            pass

    monkeypatch.setattr(builtins, "__import__", inspect_tk_import)
    monkeypatch.setattr(tkinter, "Tk", FakeRoot)
    monkeypatch.setattr(filedialog, "askdirectory", lambda **_kwargs: "")

    assert web_app.choose_output_directory(Path(".")) is None
    assert paths_seen_at_import
    assert all(paths == (str(tcl_dir), str(tk_dir)) for paths in paths_seen_at_import)
    assert os.environ["TCL_LIBRARY"] == "old-tcl-value"
    assert os.environ["TK_LIBRARY"] == "old-tk-value"


def test_api_requires_session_csrf_and_same_origin(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/settings").status_code == 401
        client.get("/")
        csrf = client.cookies.get("if_csrf")
        assert client.post("/api/settings", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}, json={"preferred_template": "unrecognized"}).status_code == 422
        assert client.post("/api/settings", headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong"}, json={}).status_code == 403
        assert client.post("/api/settings", headers={"Origin": "http://evil.invalid", "X-CSRF-Token": csrf}, json={}).status_code == 403
        wrong_host = client.get("/api/health", headers={"Host": "localhost:48765"})
        assert wrong_host.status_code == 421


def test_file_extensions_and_malformed_inputs_are_actionable(web):
    client, headers, _ = web
    assert upload(client, headers, "data.txt", b"hello").status_code == 415
    assert upload(client, headers, "empty.json", b"").status_code == 422
    malformed = upload(client, headers, "bad.json", b"{oops", "application/json")
    assert malformed.status_code == 422
    assert "JSON" in malformed.text
    too_big = upload(client, headers, "large.csv", b"x" * (2 * 1024 * 1024 + 1))
    assert too_big.status_code == 413


@pytest.mark.parametrize("extension,payload,content_type", [
    ("csv", b"Invoice No,Customer Name,Amount\nCSV-1,CSV Client,10.25\n", "text/csv"),
    ("json", b'{"invoices":[{"Invoice No":"JSON-1","Customer Name":"JSON Client","Amount":"11.50"}]}', "application/json"),
])
def test_csv_and_json_ingestion_and_custom_mapping(web, extension, payload, content_type):
    client, headers, _ = web
    result = upload(client, headers, f"in.{extension}", payload, content_type)
    assert result.status_code == 200, result.text
    assert result.json()["row_count"] == 1
    mapping = {"invoice_number": "Invoice No", "name": "Customer Name", "amount": "Amount"}
    checked = validate(client, headers, mapping)
    assert checked.status_code == 200, checked.text
    assert checked.json()["invoices"][0]["amount_payable"] in {"10.25", "11.50"}


def test_xlsx_ingestion_reaches_backend_reader(web):
    import pandas as pd
    from io import BytesIO

    stream = BytesIO()
    pd.DataFrame([{"Invoice No": "X-1", "Customer Name": "XLSX Client", "Amount": 3.5}]).to_excel(stream, index=False)
    client, headers, _ = web
    response = upload(client, headers, "workbook.xlsx", stream.getvalue())
    assert response.status_code == 200, response.text
    assert response.json()["headers"] == ["Invoice No", "Customer Name", "Amount"]


def test_xls_ingestion_reaches_backend_reader(web):
    xlwt = pytest.importorskip("xlwt", reason="xlwt is needed only to synthesize an XLS test fixture")
    from io import BytesIO

    book = xlwt.Workbook()
    sheet = book.add_sheet("Invoices")
    for col, value in enumerate(("Invoice No", "Customer Name", "Amount")):
        sheet.write(0, col, value)
    for col, value in enumerate(("XL-1", "XLS Client", 5.5)):
        sheet.write(1, col, value)
    stream = BytesIO()
    book.save(stream)
    client, headers, _ = web
    response = upload(client, headers, "workbook.xls", stream.getvalue())
    assert response.status_code == 200, response.text
    assert response.json()["row_count"] == 1


def test_mapping_ambiguity_missing_fields_and_option_validation(web):
    client, headers, _ = web
    response = upload(client, headers, "ambiguous.json", json.dumps([
        {"Invoice No": "A-1", "Customer": "A", "Customer Name": "B", "Amount": 2},
    ]).encode(), "application/json")
    assert response.status_code == 200
    assert "name" in response.json()["ambiguities"]
    rejected = validate(client, headers, {"invoice_number": "Invoice No", "name": None, "amount": "Amount"})
    assert rejected.status_code == 422
    assert "customer name" in rejected.text.casefold()
    manual = validate(client, headers, {"invoice_number": "Invoice No", "name": "Customer Name", "amount": "Amount"})
    assert manual.status_code == 200
    invalid = client.post("/api/validate", headers=headers, json={
        "mapping": {"name": "Customer Name", "amount": "Amount"}, "options": {"inclusive_tax": "false"},
    })
    assert invalid.status_code == 422


def test_settings_persist_branding_and_invalid_color_is_rejected(web):
    client, headers, service = web
    saved = client.post("/api/settings", headers=headers, json={"company_name": "Northwind Works", "accent_color": "#123ABC"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["company_name"] == "Northwind Works"
    assert service.settings.accent_color == "#123ABC"
    from io import BytesIO
    from PIL import Image
    image = BytesIO()
    Image.new("RGB", (8, 8), "white").save(image, format="PNG")
    uploaded = client.put("/api/branding/signature", headers={
        **headers, "X-File-Name": "sign.png", "Content-Type": "application/octet-stream",
    }, content=image.getvalue())
    assert uploaded.status_code == 200, uploaded.text
    saved = client.post("/api/settings", headers=headers, json={})
    assert saved.status_code == 200 and saved.json()["signature_available"]
    assert "signature_path" not in saved.json()
    invalid = client.post("/api/settings", headers=headers, json={"primary_color": "red"})
    assert invalid.status_code == 422
    assert client.post("/api/settings/reset", headers=headers).json()["company_name"] == ""


def test_preview_and_export_share_real_pdf_renderer_and_history(web):
    client, headers, _ = web
    data = b'[{"Invoice No":"PDF-42","Customer Name":"PDF Customer","Amount":"250.00"}]'
    assert upload(client, headers, "pdf.json", data).status_code == 200
    mapping = {"invoice_number": "Invoice No", "name": "Customer Name", "amount": "Amount"}
    assert validate(client, headers, mapping).status_code == 200
    for template in ("minimal", "corporate_classic", "modern_accent", "compact_grid"):
        preview = client.post("/api/preview", headers=headers, json={"mapping": mapping, "template": template, "options": {}})
        assert preview.status_code == 200, preview.text
        preview_file = client.get(preview.json()["url"])
        assert preview_file.status_code == 200 and preview_file.content.startswith(b"%PDF-")
    start = client.post("/api/jobs/single", headers=headers, json={"mapping": mapping, "template": "corporate_classic", "options": {}})
    assert start.status_code == 200, start.text
    job = wait_job(client, start.json()["job_id"])
    assert job["status"] == "complete" and job["succeeded"] == 1
    row = client.get("/api/history", params={"query": "PDF-42"}).json()["items"][0]
    assert row["can_open"] and row["customer_name"] == "PDF Customer"
    pdf = client.get(f"/api/history/{row['id']}/file")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")


def test_email_requires_confirmation_and_reports_mocked_smtp_acceptance(web, monkeypatch):
    import backend.web_app as web_module

    client, headers, service = web
    assert upload(client, headers, "mail.json", b'[{"Invoice No":"MAIL-1","Customer Name":"Mail Client","Amount":"20.00"}]', "application/json").status_code == 200
    mapping = {"invoice_number": "Invoice No", "name": "Customer Name", "amount": "Amount"}
    start = client.post("/api/jobs/single", headers=headers, json={"mapping": mapping, "options": {}, "template": "minimal"})
    assert start.status_code == 200
    job = wait_job(client, start.json()["job_id"])
    record_id = job["results"][0]["record_id"]
    assert client.post("/api/email", headers=headers, json={"record_id": record_id, "recipient": "billing@example.com", "confirm": False}).status_code == 400
    monkeypatch.setenv("INVOICEFLOW_SMTP_PASSWORD", "test-only-secret")
    client.post("/api/settings", headers=headers, json={"smtp_host": "smtp.example.test", "smtp_sender": "sender@example.test"})
    monkeypatch.setattr(web_module, "send_invoice_pdf", lambda *_args, **_kwargs: "SMTP accepted synthetic message")
    sent = client.post("/api/email", headers=headers, json={"record_id": record_id, "recipient": "billing@example.com", "confirm": True})
    assert sent.status_code == 200, sent.text
    mail_job = wait_job(client, sent.json()["job_id"])
    assert mail_job["status"] == "complete"
    assert mail_job["message"] == "SMTP accepted synthetic message"
    assert "test-only-secret" not in json.dumps(client.get("/api/settings").json())
    assert service.settings.smtp_host == "smtp.example.test"


def test_event_cursor_is_monotonic(web):
    client, headers, service = web
    first = client.get("/api/events").json()
    service._event("INFO", "cursor check")
    next_page = client.get("/api/events", params={"after": first["count"]}).json()
    assert len(next_page["items"]) == 1
    assert next_page["items"][0]["message"] == "cursor check"


def test_batch_export_reports_results_and_protects_existing_outputs(web):
    client, headers, _ = web
    data = json.dumps([
        {"Invoice No": "BATCH-A", "Customer Name": "Batch Client A", "Amount": 12},
        {"Invoice No": "BATCH-B", "Customer Name": "Batch Client B", "Amount": 13},
    ]).encode()
    assert upload(client, headers, "batch.json", data, "application/json").status_code == 200
    mapping = {"invoice_number": "Invoice No", "name": "Customer Name", "amount": "Amount"}
    started = client.post("/api/jobs/batch", headers=headers, json={"mapping": mapping, "options": {}, "template": "compact_grid"})
    assert started.status_code == 200, started.text
    job = wait_job(client, started.json()["job_id"])
    assert job["status"] == "complete" and job["succeeded"] == 2 and job["failed"] == 0
    duplicate = client.post("/api/jobs/batch", headers=headers, json={"mapping": mapping, "options": {}, "template": "compact_grid"})
    assert duplicate.status_code == 409
    confirmed = client.post("/api/jobs/batch", headers=headers, json={
        "mapping": mapping, "options": {}, "template": "compact_grid", "confirm_duplicates": True,
    })
    assert confirmed.status_code == 200, confirmed.text
    collision_job = wait_job(client, confirmed.json()["job_id"])
    assert collision_job["status"] == "failed"
    assert "already exists" in collision_job["error"]
