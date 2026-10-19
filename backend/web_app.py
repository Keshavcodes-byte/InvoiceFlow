"""Loopback-only FastAPI adapter for the existing InvoiceFlow backend."""
from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import uuid
import webbrowser
from urllib.parse import unquote
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from reportlab.lib.utils import ImageReader

from .app_logging import configure_logging
from .app_settings import AppSettings, SettingsStore, user_data_dir
from .email_dispatch import SMTPConfig, send_invoice_pdf
from .history_repository import HistoryRepository
from .invoice_calculations import DiscountKind, TaxMode, calculate_invoice, format_currency
from .invoice_generator import CANONICAL_FIELD_ALIASES, find_column, read_input_file
from .invoice_workflow import (
    WorkflowOptions, build_invoice_documents, export_invoice_batch,
    export_single_invoice, render_invoice_preview,
)
from .pdf_templates import BrandingConfig, InvoiceTemplate


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_LOG_EVENTS = 150
ALLOWED_SUFFIXES = {".csv", ".xlsx", ".xls", ".json"}
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")
_SETTINGS_FIELDS = {
    "company_name", "address", "email", "phone", "website", "gstin",
    "primary_color", "accent_color", "footer_text", "preferred_template",
    "default_currency", "decimal_separator", "default_tax_rate", "tax_mode",
    "inclusive_tax", "discount_kind", "filename_pattern", "smtp_host",
    "smtp_port", "smtp_tls_mode", "smtp_sender", "smtp_password_env",
}


def resource_path(relative: str) -> Path:
    """Resolve bundled HTML assets from source or a PyInstaller one-folder build."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Resource path is outside the application bundle.")
    return path


def _safe_filename(value: str) -> str:
    name = _SAFE_FILENAME.sub("_", str(value or "invoice"))
    name = name.replace("..", "_").strip(" .")[:120]
    return name or "invoice"


def _configure_bundled_tk_paths() -> dict[str, str | None]:
    """Point frozen Tk at bundled scripts and return the prior env values."""
    if not getattr(sys, "frozen", False):
        return {}

    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    tcl_dirs = [path.parent for path in (bundle_root / "tcl").glob("tcl*/init.tcl")]
    tk_dirs = [path.parent for path in (bundle_root / "tcl").glob("tk*/pkgIndex.tcl")]
    if len(tcl_dirs) != 1 or len(tk_dirs) != 1:
        raise RuntimeError("The bundled Tcl/Tk script directories are missing or ambiguous.")
    if not (bundle_root / "bin").is_dir() or not any((bundle_root / "bin").glob("*.dll")):
        raise RuntimeError("The bundled Tk runtime DLL directory is missing.")

    paths = {"TCL_LIBRARY": tcl_dirs[0], "TK_LIBRARY": tk_dirs[0]}
    previous: dict[str, str | None] = {}
    for name, path in paths.items():
        previous[name] = os.environ.get(name)
        # These are the two Tcl/Tk-specific search paths. Keep all other
        # environment settings untouched and point these at this bundle.
        os.environ[name] = str(path)
    return previous


def _restore_tk_paths(previous: dict[str, str | None]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def choose_output_directory(initial: Path) -> str | None:
    """Open the native folder picker. All Tk objects stay on this worker thread."""
    previous_tk_paths = _configure_bundled_tk_paths()
    root = None
    try:
        # Set bundle-derived paths before importing _tkinter so Tcl sees the
        # intended script directories when its runtime is initialized.
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            title="Choose InvoiceFlow output folder", initialdir=str(initial), mustexist=True,
        )
        return selected or None
    finally:
        if root is not None:
            root.destroy()
        _restore_tk_paths(previous_tk_paths)


class InvoiceFlowService:
    """Per-process user session, uploads, work queue, and safe output handles."""

    def __init__(self, data_dir: str | os.PathLike | None = None, *, output_picker: Callable | None = None, max_upload_bytes: int = MAX_UPLOAD_BYTES):
        self.data_dir = Path(data_dir) if data_dir else user_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        logger_name = "invoiceflow.web." + hashlib.sha1(str(self.data_dir.resolve()).encode()).hexdigest()[:10]
        self.logger = configure_logging(self.data_dir / "logs", logger_name=logger_name)
        self.settings_store = SettingsStore(self.data_dir / "settings.json")
        try:
            self.settings = self.settings_store.load()
            self.settings_error = ""
        except ValueError as error:
            self.settings = AppSettings()
            self.settings_error = str(error)
            self.logger.error("settings_load_failed type=%s", type(error).__name__)
        self.history = HistoryRepository(self.data_dir / "invoice_history.sqlite3")
        self.output_dir = self.data_dir / "exports"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._assert_writable(self.output_dir)
        self.approved_output_dirs = {self.output_dir.resolve()}
        self.output_picker = output_picker or choose_output_directory
        self.max_upload_bytes = int(max_upload_bytes)
        self.upload_dir = Path(tempfile.mkdtemp(prefix="invoiceflow-upload-"))
        self.preview_dir = Path(tempfile.mkdtemp(prefix="invoiceflow-preview-"))
        self.frame = None
        self.source_path: Path | None = None
        self.source_filename = ""
        self.headers: list[str] = []
        self.mapping_suggestions: dict[str, str | None] = {}
        self.mapping_ambiguities: dict[str, str] = {}
        self.documents = []
        self.current_mapping: dict = {}
        self.current_options = WorkflowOptions()
        self.current_template = self.settings.preferred_template
        self.logo_candidate_path = self.settings.logo_path
        self.signature_candidate_path = self.settings.signature_path
        self.stamp_candidate_path = self.settings.stamp_path
        self.jobs: dict[str, dict] = {}
        self.output_handles: dict[str, Path] = {}
        self.record_documents: dict[int, object] = {}
        self.events = deque(maxlen=MAX_LOG_EVENTS)
        self.event_counter = 0
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="InvoiceFlow")
        self.active_job_id: str | None = None
        self.closing = False
        self._event("SUCCESS", "Local InvoiceFlow service is ready.")

    @staticmethod
    def _assert_writable(path: Path):
        fd, probe = tempfile.mkstemp(prefix=".invoiceflow-write-check-", dir=path)
        os.close(fd)
        Path(probe).unlink(missing_ok=True)

    def _event(self, level: str, message: str):
        safe_message = str(message).replace("\r", " ").replace("\n", " ")[:500]
        with self.lock:
            self.event_counter += 1
            item = {"id": self.event_counter, "time": datetime.now().astimezone().strftime("%H:%M:%S"), "level": level, "message": safe_message}
            self.events.append(item)
        log_method = {"ERROR": "error", "WARNING": "warning", "SUCCESS": "info"}.get(level, "info")
        getattr(self.logger, log_method)("ui_event level=%s context=%s", level, safe_message[:80])

    def public_settings(self):
        value = asdict(self.settings)
        for key in ("logo_path", "signature_path", "stamp_path"):
            value.pop(key, None)
        value["logo_available"] = bool(self.settings.logo_path and Path(self.settings.logo_path).is_file())
        value["logo_name"] = Path(self.settings.logo_path).name if value["logo_available"] else ""
        for slot in ("signature", "stamp"):
            path = getattr(self.settings, f"{slot}_path")
            value[f"{slot}_available"] = bool(path and Path(path).is_file())
            value[f"{slot}_name"] = Path(path).name if value[f"{slot}_available"] else ""
        value["settings_error"] = self.settings_error
        return value

    def save_settings(self, data: dict):
        if not isinstance(data, dict):
            raise ValueError("Settings request must be an object.")
        removal_fields = {"remove_logo", "remove_signature", "remove_stamp"}
        unknown = set(data) - _SETTINGS_FIELDS - removal_fields
        if unknown:
            raise ValueError(f"Unsupported settings field: {sorted(unknown)[0]}.")
        values = asdict(self.settings)
        values.update({key: value for key, value in data.items() if key in _SETTINGS_FIELDS})
        if data.get("remove_logo"):
            values["logo_path"] = ""
            self.logo_candidate_path = ""
        else:
            values["logo_path"] = self.logo_candidate_path or ""
        for slot in ("signature", "stamp"):
            if data.get(f"remove_{slot}"):
                setattr(self, f"{slot}_candidate_path", "")
            values[f"{slot}_path"] = getattr(self, f"{slot}_candidate_path") or ""
        settings = AppSettings(**values).validate()
        self.settings_store.save(settings)
        self.settings = settings
        self.settings_error = ""
        self.current_template = settings.preferred_template
        self._event("SUCCESS", "Branding and workflow settings saved.")
        return self.public_settings()

    def reset_settings(self):
        self.settings = self.settings_store.reset()
        self.logo_candidate_path = ""
        self.signature_candidate_path = ""
        self.stamp_candidate_path = ""
        self.current_template = self.settings.preferred_template
        self.settings_error = ""
        self._event("SUCCESS", "Settings reset to defaults.")
        return self.public_settings()

    def _read_source(self, path: Path):
        frame = read_input_file(path)
        headers = [str(column) for column in frame.columns]
        suggestions, ambiguities = {}, {}
        for field in CANONICAL_FIELD_ALIASES:
            try:
                column = find_column(frame, field)
                suggestions[field] = None if column is None else str(column)
            except ValueError as error:
                suggestions[field] = None
                ambiguities[field] = str(error)
        self.frame = frame
        self.source_path = path
        self.headers = headers
        self.mapping_suggestions = suggestions
        self.mapping_ambiguities = ambiguities
        self.current_mapping = dict(suggestions)
        self.documents = []
        self.source_filename = path.name
        self._event("SUCCESS", f"Input loaded: {len(frame)} record(s), {len(headers)} column(s).")
        if ambiguities:
            self._event("WARNING", f"Ambiguous column suggestions need review ({len(ambiguities)} field(s)).")
        return self.source_response()

    def source_response(self):
        return {
            "filename": self.source_filename,
            "headers": self.headers,
            "row_count": 0 if self.frame is None else int(len(self.frame)),
            "mapping": self.mapping_suggestions,
            "ambiguities": self.mapping_ambiguities,
        }

    def _resolve_mapping(self, mapping):
        if not isinstance(mapping, dict):
            raise ValueError("Column mapping must be an object.")
        allowed = set(CANONICAL_FIELD_ALIASES)
        if set(mapping) - allowed:
            raise ValueError("Column mapping contains an unsupported invoice field.")
        resolved = {}
        for field, display_name in mapping.items():
            if display_name in (None, ""):
                resolved[field] = None
                continue
            matches = [column for column in self.frame.columns if str(column) == str(display_name)]
            if len(matches) != 1:
                raise ValueError(f"Mapped source column for {field!r} was not found uniquely.")
            resolved[field] = matches[0]
        for field in allowed:
            resolved.setdefault(field, None)
        return resolved

    def _options(self, value):
        if not isinstance(value, dict):
            raise ValueError("Calculation options must be an object.")
        try:
            tax_mode = TaxMode(value.get("tax_mode", self.settings.tax_mode))
            discount_kind = DiscountKind(value.get("discount_kind", self.settings.discount_kind))
            tax_rate = Decimal(str(value.get("default_tax_rate", self.settings.default_tax_rate)))
            if not tax_rate.is_finite() or not 0 <= tax_rate <= 100:
                raise ValueError("Tax rate must be a finite value from 0 to 100.")
            currency = str(value.get("default_currency", self.settings.default_currency)).upper()
            decimal_separator = value.get("decimal_separator", self.settings.decimal_separator)
            if currency not in {"INR", "USD", "EUR", "GBP"}:
                raise ValueError("Unsupported currency.")
            if decimal_separator not in {".", ","}:
                raise ValueError("Decimal separator must be '.' or ','.")
            inclusive_tax = value.get("inclusive_tax", self.settings.inclusive_tax)
            if not isinstance(inclusive_tax, bool):
                raise ValueError("Inclusive tax must be true or false.")
            return WorkflowOptions(
                tax_mode=tax_mode,
                inclusive_tax=inclusive_tax,
                discount_kind=discount_kind, default_currency=currency,
                default_tax_rate=tax_rate, decimal_separator=decimal_separator,
            )
        except (InvalidOperation, TypeError) as error:
            raise ValueError("Calculation options contain an invalid number or selection.") from error

    def prepare(self, mapping, options, template):
        with self.lock:
            return self._prepare(mapping, options, template)

    def _prepare(self, mapping, options, template):
        if self.frame is None:
            raise ValueError("Upload an invoice data file first.")
        try:
            invoice_template = InvoiceTemplate(template)
        except (ValueError, TypeError) as error:
            raise ValueError("Choose a supported PDF template.") from error
        resolved = self._resolve_mapping(mapping)
        workflow_options = self._options(options)
        documents = build_invoice_documents(self.frame.copy(), resolved, options=workflow_options)
        summaries = []
        for document in documents:
            result = calculate_invoice(document)
            summaries.append({
                "invoice_number": document.details.invoice_number,
                "customer_name": document.customer.name,
                "customer_email": document.customer.email,
                "invoice_date": document.details.issue_date.isoformat() if document.details.issue_date else "",
                "currency": result.currency,
                "subtotal": str(result.subtotal_before_discounts),
                "tax_total": str(result.tax.total),
                "grand_total": str(result.grand_total),
                "amount_payable": str(result.amount_payable),
                "formatted_total": format_currency(result.grand_total, result.currency),
                "payment_status": result.payment_status.value,
                "item_count": len(document.items),
            })
        self.current_mapping = dict(mapping)
        self.current_options = workflow_options
        self.current_template = invoice_template.value
        self.documents = documents
        self._event("SUCCESS", f"Validated {len(documents)} invoice(s) with backend calculations.")
        return {"invoices": summaries, "count": len(summaries), "template": invoice_template.value}

    def duplicate_count(self, documents):
        count = 0
        for document in documents:
            result = calculate_invoice(document)
            if self.history.is_duplicate(document.details.invoice_number, document.customer.name, document.currency, result.grand_total):
                count += 1
        return count

    def _approved_output(self, path: Path):
        resolved = path.resolve()
        return any(resolved.parent == directory for directory in self.approved_output_dirs)

    def record_generation(self, document, output_path, template, error=None):
        result = calculate_invoice(document)
        record_id = self.history.record(
            invoice_number=document.details.invoice_number,
            customer_name=document.customer.name,
            source_filename=str(self.source_path or ""),
            output_path=str(output_path or ""), template=template,
            currency=result.currency, total=result.grand_total,
            invoice_date=document.details.issue_date.isoformat() if document.details.issue_date else "",
            status="failed" if error else "succeeded", error_summary=error or "",
        )
        if not error and output_path:
            self.record_documents[record_id] = document
            self.output_handles[secrets.token_urlsafe(24)] = Path(output_path)
        return record_id

    def _start_job(self, operation: str, target: Callable, total=1):
        with self.lock:
            if self.active_job_id and self.jobs.get(self.active_job_id, {}).get("status") in {"queued", "running"}:
                raise HTTPException(status_code=409, detail={"code": "job_in_progress", "message": "Another operation is still running."})
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "operation": operation, "status": "queued", "progress": 0, "total": max(1, int(total)), "succeeded": 0, "failed": 0, "results": [], "error": "", "message": "Queued"}
            self.jobs[job_id] = job
            self.active_job_id = job_id

        def run():
            with self.lock:
                job["status"] = "running"
                job["message"] = "Running"
            try:
                target(job)
                with self.lock:
                    if job["status"] == "running":
                        job["status"] = "complete"
                        if job["message"] == "Running":
                            job["message"] = "Complete"
            except Exception as error:
                with self.lock:
                    job["status"] = "failed"
                    job["error"] = str(error)[:500]
                    job["message"] = "Operation failed"
                self.logger.error("background_job_failed operation=%s error_type=%s", operation, type(error).__name__)
                self._event("ERROR", f"{operation.capitalize()} failed: {type(error).__name__}.")
            finally:
                with self.lock:
                    if self.active_job_id == job_id:
                        self.active_job_id = None

        self.executor.submit(run)
        self._event("INFO", f"Started {operation} operation.")
        return job_id

    def start_export(self, operation, template, pdf_password=None):
        documents = list(self.documents)
        if not documents:
            raise ValueError("Validate invoice data before exporting.")
        try:
            template = InvoiceTemplate(template).value
        except (ValueError, TypeError) as error:
            raise ValueError("Choose a supported PDF template.") from error
        if pdf_password is not None and (not isinstance(pdf_password, str) or len(pdf_password) < 8 or "\x00" in pdf_password):
            raise ValueError("PDF password must contain at least 8 characters.")
        branding = self.settings.branding()

        def task(job):
            if operation == "single":
                document = documents[0]
                try:
                    path = export_single_invoice(document, self.output_dir, template=template, branding=branding, filename_pattern=self.settings.filename_pattern, pdf_password=pdf_password)
                    record_id = self.record_generation(document, path, template)
                    job["results"] = [{"record_id": record_id, "invoice_number": document.details.invoice_number, "output_path": str(path), "status": "succeeded"}]
                    job["succeeded"] = 1
                    job["progress"] = 1
                    self._event("SUCCESS", "Single invoice PDF exported.")
                except Exception as error:
                    self.record_generation(document, None, template, error=str(error))
                    raise
            else:
                def report_progress(current, count):
                    with self.lock:
                        job["progress"] = current
                        job["total"] = count
                result = export_invoice_batch(
                    documents, self.output_dir, template=template, branding=branding,
                    filename_pattern=self.settings.filename_pattern, pdf_password=pdf_password,
                    progress_callback=report_progress,
                )
                rows = []
                for document, item in zip(documents, result.results):
                    record_id = self.record_generation(document, item.output_path, template, error=item.error)
                    rows.append({
                        "record_id": record_id, "invoice_number": item.invoice_number,
                        "output_path": str(item.output_path) if item.output_path else "",
                        "status": "failed" if item.error else "succeeded", "error": item.error or "",
                    })
                    self._event("ERROR" if item.error else "SUCCESS", f"Invoice {item.invoice_number or '(unnumbered)'}: {'export failed' if item.error else 'PDF exported'}.")
                job["results"] = rows
                job["succeeded"] = result.succeeded
                job["failed"] = result.failed
                job["progress"] = len(documents)
                job["message"] = f"{result.succeeded} succeeded, {result.failed} failed"
                if result.failed:
                    job["status"] = "partial" if result.succeeded else "failed"
            return

        return self._start_job(operation, task, len(documents) if operation == "batch" else 1)

    def start_email(self, record_id: int, recipient: str):
        document = self.record_documents.get(record_id)
        row = self.history.get(record_id)
        if document is None or row is None or row["status"] != "succeeded":
            raise ValueError("Reload and validate this invoice in the current session before emailing it.")
        path = Path(row["output_path"])
        if not self._approved_output(path) or not path.is_file():
            raise ValueError("The recorded PDF is not available in the selected InvoiceFlow output folder.")
        config = SMTPConfig(
            self.settings.smtp_host, self.settings.smtp_port, self.settings.smtp_tls_mode,
            self.settings.smtp_sender, self.settings.smtp_password_env,
        ).validate()

        def task(job):
            try:
                message = send_invoice_pdf(
                    config, recipient, path,
                    subject=f"Invoice {document.details.invoice_number}".strip(),
                    body=f"Please find invoice {document.details.invoice_number} attached.",
                )
                job["message"] = message
                job["succeeded"] = 1
                self._event("SUCCESS", "SMTP accepted an invoice message; recipient delivery is unverified.")
            except Exception:
                self._event("ERROR", "SMTP submission failed; inspect the message and try again when appropriate.")
                raise
        return self._start_job("email", task)

    def close(self):
        self.closing = True
        self.executor.shutdown(wait=True, cancel_futures=False)
        shutil.rmtree(self.upload_dir, ignore_errors=True)
        shutil.rmtree(self.preview_dir, ignore_errors=True)
        for handler in list(self.logger.handlers):
            if getattr(handler, "baseFilename", "").startswith(str((self.data_dir / "logs").resolve())):
                handler.flush()
                handler.close()
                self.logger.removeHandler(handler)


def create_app(
    service: InvoiceFlowService | None = None, *, data_dir=None,
    expected_origin="http://127.0.0.1:48765", max_upload_bytes=MAX_UPLOAD_BYTES,
    output_picker=None,
) -> FastAPI:
    service = service or InvoiceFlowService(data_dir, output_picker=output_picker, max_upload_bytes=max_upload_bytes)
    expected_netloc = expected_origin.split("://", 1)[-1]
    @asynccontextmanager
    async def lifespan(_app):
        app.state.session_token = secrets.token_urlsafe(32)
        app.state.csrf_token = secrets.token_urlsafe(32)
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="InvoiceFlow Local", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.invoiceflow = service
    app.state.expected_origin = expected_origin

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        if request.headers.get("host", "").casefold() != expected_netloc.casefold():
            return JSONResponse({"detail": {"code": "invalid_host", "message": "This local service only accepts its bound address."}}, status_code=421)
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/").casefold() != expected_origin.rstrip("/").casefold():
            return JSONResponse({"detail": {"code": "invalid_origin", "message": "Cross-origin requests are not accepted."}}, status_code=403)
        is_api = request.url.path.startswith("/api/")
        public_health = request.url.path == "/api/health" and request.method == "GET"
        if is_api and not public_health:
            session = request.cookies.get("if_session", "")
            if not hmac.compare_digest(session, app.state.session_token):
                return JSONResponse({"detail": {"code": "unauthorized", "message": "Reload the InvoiceFlow page to start a secure session."}}, status_code=401)
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                csrf_cookie = request.cookies.get("if_csrf", "")
                csrf_header = request.headers.get("x-csrf-token", "")
                if not csrf_cookie or not hmac.compare_digest(csrf_cookie, csrf_header):
                    return JSONResponse({"detail": {"code": "csrf", "message": "Request security check failed. Reload InvoiceFlow and retry."}}, status_code=403)
                if origin != expected_origin:
                    return JSONResponse({"detail": {"code": "invalid_origin", "message": "A same-origin request is required."}}, status_code=403)
                if request.headers.get("sec-fetch-site", "same-origin") not in {"same-origin", "none"}:
                    return JSONResponse({"detail": {"code": "cross_site", "message": "Cross-site requests are not accepted."}}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index():
        try:
            source = resource_path("web/index.html").read_text(encoding="utf-8")
        except OSError as error:
            raise HTTPException(500, "InvoiceFlow dashboard asset is missing.") from error
        nonce = secrets.token_urlsafe(18)
        source = source.replace("__CSP_NONCE__", nonce)
        response = HTMLResponse(source)
        response.set_cookie("if_session", app.state.session_token, httponly=True, samesite="strict", secure=False, path="/")
        response.set_cookie("if_csrf", app.state.csrf_token, httponly=False, samesite="strict", secure=False, path="/")
        response.headers["Content-Security-Policy"] = f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; frame-src 'self'; object-src 'self'; base-uri 'none'; frame-ancestors 'none'"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/api/health")
    async def health():
        return {"app": "InvoiceFlow", "status": "ready", "version": "2.0"}

    @app.get("/api/settings")
    async def get_settings():
        return service.public_settings()

    @app.get("/api/output-folder")
    async def get_output_folder():
        return {"display_path": str(service.output_dir)}

    @app.post("/api/settings")
    async def save_settings(request: Request):
        try:
            return service.save_settings(await request.json())
        except (ValueError, TypeError) as error:
            service._event("ERROR", "Settings validation failed.")
            raise HTTPException(422, str(error)) from error
        except OSError as error:
            raise HTTPException(500, "Settings could not be written to the user data folder.") from error

    @app.post("/api/settings/reset")
    async def reset_settings():
        try:
            return service.reset_settings()
        except OSError as error:
            raise HTTPException(500, "Default settings could not be saved.") from error

    @app.put("/api/branding/{slot}")
    async def upload_branding_image(slot: str, request: Request):
        if slot not in {"logo", "signature", "stamp"}:
            raise HTTPException(404, "Unknown branding image slot.")
        filename = unquote(request.headers.get("x-file-name", f"{slot}.png"))
        suffix = Path(_safe_filename(filename)).suffix.casefold()
        if suffix not in {".png", ".jpg", ".jpeg"}:
            raise HTTPException(415, "Branding images must be PNG or JPEG.")
        data = await _read_limited_body(request, 5 * 1024 * 1024)
        logo_dir = service.data_dir / "branding"
        logo_dir.mkdir(parents=True, exist_ok=True)
        path = logo_dir / f"{slot}-{uuid.uuid4().hex}{suffix}"
        path.write_bytes(data)
        try:
            width, height = ImageReader(str(path)).getSize()
            if not width or not height or width * height > 24_000_000:
                raise ValueError("Logo dimensions are too large.")
        except Exception as error:
            path.unlink(missing_ok=True)
            raise HTTPException(422, "Logo image is invalid or too large to render.") from error
        setattr(service, f"{slot}_candidate_path", str(path))
        service._event("SUCCESS", f"Branding {slot} image uploaded for settings.")
        return {f"{slot}_available": True, f"{slot}_name": path.name}

    @app.post("/api/branding/{slot}/remove")
    async def remove_branding_image(slot: str):
        if slot not in {"logo", "signature", "stamp"}:
            raise HTTPException(404, "Unknown branding image slot.")
        setattr(service, f"{slot}_candidate_path", "")
        return {f"{slot}_available": False, f"{slot}_name": ""}

    @app.post("/api/settings/cancel")
    async def cancel_settings():
        service.logo_candidate_path = service.settings.logo_path
        service.signature_candidate_path = service.settings.signature_path
        service.stamp_candidate_path = service.settings.stamp_path
        return service.public_settings()

    @app.get("/api/source")
    async def get_source():
        if service.frame is None:
            raise HTTPException(404, "No input file is loaded.")
        return service.source_response()

    @app.put("/api/source")
    async def upload_source(request: Request):
        filename = unquote(request.headers.get("x-file-name", ""))
        if not filename or len(filename) > 255 or any(ord(char) < 32 for char in filename):
            raise HTTPException(422, "Input filename is missing or invalid.")
        safe_name = _safe_filename(re.split(r"[/\\]", filename)[-1])
        suffix = Path(safe_name).suffix.casefold()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(415, "Supported input formats are CSV, XLSX, XLS, and JSON.")
        data = await _read_limited_body(request, service.max_upload_bytes)
        if not data:
            raise HTTPException(422, "The selected file is empty.")
        target = service.upload_dir / f"{uuid.uuid4().hex}{suffix}"
        target.write_bytes(data)
        try:
            result = await asyncio.to_thread(service._read_source, target)
        except Exception as error:
            target.unlink(missing_ok=True)
            service.logger.error("input_read_failed extension=%s error_type=%s", suffix, type(error).__name__)
            service._event("ERROR", "Input could not be read. Check its format and headers.")
            status = 422 if isinstance(error, (ValueError, UnicodeError)) else 400
            raise HTTPException(status, str(error)[:500]) from error
        service.source_filename = safe_name
        result["filename"] = safe_name
        service._event("INFO", "Original filename retained only as safe display metadata.")
        return result

    @app.post("/api/source/clear")
    async def clear_source():
        service.frame = None
        service.source_path = None
        service.source_filename = ""
        service.headers = []
        service.documents = []
        service.mapping_suggestions = {}
        service.mapping_ambiguities = {}
        service._event("INFO", "Active input cleared; retained history sources were not removed.")
        return {"status": "cleared"}

    @app.post("/api/validate")
    async def validate_source(request: Request):
        try:
            body = await request.json()
            result = await asyncio.to_thread(
                service.prepare, body.get("mapping", {}), body.get("options", {}),
                body.get("template", service.settings.preferred_template),
            )
            duplicates = await asyncio.to_thread(service.duplicate_count, service.documents)
            result["duplicate_count"] = duplicates
            if duplicates:
                service._event("WARNING", f"{duplicates} invoice(s) match a prior successful generation.")
            service._event("INFO", f"Mapped {len([v for v in body.get('mapping', {}).values() if v])} source field(s).")
            return result
        except HTTPException:
            raise
        except Exception as error:
            service._event("ERROR", "Invoice validation failed; review mapping and input values.")
            raise HTTPException(422, str(error)[:500]) from error

    @app.post("/api/output-folder/select")
    async def select_output_folder():
        try:
            selected = await asyncio.to_thread(service.output_picker, service.output_dir)
        except Exception as error:
            service.logger.error("output_folder_picker_failed type=%s", type(error).__name__)
            raise HTTPException(500, "The native folder picker could not be opened.") from error
        if not selected:
            return {"cancelled": True, "display_path": str(service.output_dir)}
        path = Path(selected).expanduser().resolve()
        if not path.is_dir():
            raise HTTPException(422, "Choose an existing output folder.")
        try:
            service._assert_writable(path)
        except OSError as error:
            raise HTTPException(422, "The selected output folder is not writable.") from error
        service.output_dir = path
        service.approved_output_dirs.add(path)
        service._event("SUCCESS", "Output folder selected and write access verified.")
        return {"cancelled": False, "display_path": str(path)}

    @app.post("/api/output-folder/open")
    async def open_output_folder():
        try:
            _open_external(service.output_dir)
        except Exception as error:
            service.logger.error("output_folder_open_failed type=%s", type(error).__name__)
            raise HTTPException(500, "Could not open the selected output folder.") from error
        service._event("INFO", "Output folder opened in the system file manager.")
        return {"status": "opened"}

    @app.post("/api/preview")
    async def preview_invoice(request: Request):
        try:
            body = await request.json()
            await asyncio.to_thread(service.prepare, body.get("mapping", {}), body.get("options", {}), body.get("template", service.settings.preferred_template))
            await asyncio.to_thread(render_invoice_preview, service.documents[0], template=service.current_template, branding=service.settings.branding(), directory=service.preview_dir)
            # render_invoice_preview generates a unique filename in preview_dir; select its latest file.
            paths = sorted(service.preview_dir.glob("InvoiceFlow_preview_*.pdf"), key=lambda item: item.stat().st_mtime_ns)
            if not paths:
                raise OSError("Preview renderer produced no PDF.")
            path = paths[-1]
            service.preview_path = path
            service._event("SUCCESS", "Invoice preview PDF rendered.")
            return {"url": "/api/preview/file", "display_name": "InvoiceFlow preview.pdf"}
        except Exception as error:
            service._event("ERROR", "Invoice preview failed.")
            raise HTTPException(422, str(error)[:500]) from error

    @app.get("/api/preview/file")
    async def preview_file():
        path = getattr(service, "preview_path", None)
        if not path or not path.is_file() or not path.resolve().is_relative_to(service.preview_dir.resolve()):
            raise HTTPException(404, "No current invoice preview is available.")
        return FileResponse(path, media_type="application/pdf", filename="InvoiceFlow-preview.pdf", content_disposition_type="inline")

    @app.post("/api/jobs/{operation}")
    async def start_job(operation: str, request: Request):
        if operation not in {"single", "batch"}:
            raise HTTPException(404, "Unknown job type.")
        try:
            body = await request.json()
            await asyncio.to_thread(service.prepare, body.get("mapping", {}), body.get("options", {}), body.get("template", service.settings.preferred_template))
            duplicate_count = await asyncio.to_thread(service.duplicate_count, service.documents)
            if duplicate_count and not body.get("confirm_duplicates", False):
                raise HTTPException(409, detail={"code": "duplicate_warning", "count": duplicate_count, "message": f"{duplicate_count} invoice(s) match prior successful generations. Confirm to continue."})
            password = body.get("pdf_password")
            job_id = service.start_export(operation, body.get("template", service.settings.preferred_template), password)
            return {"job_id": job_id}
        except HTTPException:
            raise
        except Exception as error:
            service._event("ERROR", f"Could not start {operation} export.")
            raise HTTPException(422, str(error)[:500]) from error

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str):
        with service.lock:
            job = service.jobs.get(job_id)
            if not job:
                raise HTTPException(404, "InvoiceFlow job was not found.")
            return dict(job)

    @app.get("/api/history")
    async def history(query: str = "", status: str = ""):
        try:
            rows = service.history.search(query[:100], status=status or None)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(503, "Invoice history is temporarily unavailable.") from error
        items = []
        for row in rows:
            path = Path(row["output_path"]) if row["output_path"] else None
            available = bool(path and path.suffix.casefold() == ".pdf" and path.is_file() and service._approved_output(path))
            items.append({
                "id": row["id"], "invoice_number": row["invoice_number"],
                "customer_name": row["customer_name"], "invoice_date": row.get("invoice_date", ""),
                "template": row["template"], "currency": row["currency"], "total": row["total"] or "",
                "created_at": row["created_at"], "status": row["status"],
                "error_summary": row["error_summary"], "output_name": path.name if path else "",
                "can_open": available, "can_reload": bool(row["source_filename"] and Path(row["source_filename"]).is_file() and Path(row["source_filename"]).resolve().is_relative_to(service.upload_dir.resolve())),
            })
        service._event("INFO", f"History search returned {len(items)} record(s).")
        return {"items": items}

    @app.get("/api/history/{record_id}/file")
    async def history_file(record_id: int):
        row = service.history.get(record_id)
        if not row or row["status"] != "succeeded" or not row["output_path"]:
            raise HTTPException(404, "A successful output file is not recorded for this invoice.")
        path = Path(row["output_path"])
        if path.suffix.casefold() != ".pdf" or not path.is_file() or not service._approved_output(path):
            raise HTTPException(404, "The PDF is unavailable. Select its output folder first if it is outside the current folder.")
        try:
            with path.open("rb") as stream:
                signature = stream.read(5)
            if signature != b"%PDF-":
                raise ValueError
        except (OSError, ValueError) as error:
            raise HTTPException(404, "The recorded output is not a readable PDF.") from error
        service._event("INFO", "Opened a recorded invoice PDF.")
        return FileResponse(path, media_type="application/pdf", filename=path.name, content_disposition_type="inline")

    @app.post("/api/history/{record_id}/reload")
    async def reload_history_source(record_id: int):
        row = service.history.get(record_id)
        if not row or not row["source_filename"]:
            raise HTTPException(404, "The original source is not recorded.")
        path = Path(row["source_filename"]).resolve()
        if not path.is_file() or not path.is_relative_to(service.upload_dir.resolve()):
            raise HTTPException(410, "The uploaded source is no longer available in this application session. Select the original file again.")
        try:
            return await asyncio.to_thread(service._read_source, path)
        except Exception as error:
            raise HTTPException(422, "The recorded source could not be reloaded.") from error

    @app.post("/api/email")
    async def email_invoice(request: Request):
        try:
            body = await request.json()
            if body.get("confirm") is not True:
                raise HTTPException(400, "Confirm the email submission first.")
            recipient = str(body.get("recipient", "")).strip()
            job_id = service.start_email(int(body.get("record_id")), recipient)
            service._event("INFO", "User confirmed SMTP submission for an invoice.")
            return {"job_id": job_id}
        except HTTPException:
            raise
        except Exception as error:
            service._event("ERROR", "Could not start invoice email submission.")
            raise HTTPException(422, str(error)[:500]) from error

    @app.get("/api/events")
    async def events(after: int = 0):
        with service.lock:
            items = [item for item in service.events if item["id"] > max(0, int(after))]
            counter = service.event_counter
        return {"items": items, "count": counter}

    @app.post("/api/quit")
    async def quit_app():
        service._event("INFO", "Quit requested from the dashboard.")
        callback = getattr(app.state, "shutdown_callback", None)
        if callback:
            threading.Timer(.15, callback).start()
        return {"status": "shutting_down"}

    return app


async def _read_limited_body(request: Request, limit: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > limit:
                raise HTTPException(413, f"Upload exceeds the {limit // (1024 * 1024)} MB limit.")
        except ValueError as error:
            raise HTTPException(400, "Invalid Content-Length header.") from error
    result = bytearray()
    async for chunk in request.stream():
        result.extend(chunk)
        if len(result) > limit:
            raise HTTPException(413, f"Upload exceeds the {limit // (1024 * 1024)} MB limit.")
    return bytes(result)


def _open_external(path: Path):
    value = str(path)
    if os.name == "nt":
        os.startfile(value)
    elif sys.platform == "darwin":
        import subprocess
        subprocess.Popen(["open", value])
    else:
        import subprocess
        subprocess.Popen(["xdg-open", value])


def run_web_app(port: int = 48765):
    """Start a loopback-only service and open the local dashboard in a browser."""
    import uvicorn

    address = "127.0.0.1"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((address, int(port)))
        sock.listen(128)
    except OSError as error:
        sock.close()
        if error.errno in {getattr(__import__("errno"), "EADDRINUSE", 98), 10048}:
            try:
                from urllib.request import urlopen
                with urlopen(f"http://{address}:{port}/api/health", timeout=1.5) as response:
                    payload = json.loads(response.read())
                if payload.get("app") == "InvoiceFlow":
                    webbrowser.open(f"http://{address}:{port}/")
                    return 0
            except Exception:
                pass
            raise RuntimeError(f"Local port {port} is already in use by another application.") from error
        raise
    origin = f"http://{address}:{sock.getsockname()[1]}"
    service = InvoiceFlowService()
    app = create_app(service, expected_origin=origin)
    server = uvicorn.Server(uvicorn.Config(app, host=address, port=sock.getsockname()[1], log_config=None, access_log=False, reload=False))
    app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    timer = threading.Timer(.8, lambda: webbrowser.open(origin + "/"))
    timer.daemon = True
    timer.start()
    try:
        server.run(sockets=[sock])
    finally:
        timer.cancel()
        service.close()
        sock.close()
    return 0
