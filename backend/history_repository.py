"""Small SQLite persistence layer for invoice generation outcomes."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Iterable

from .app_settings import user_data_dir

SCHEMA_VERSION = 2


class HistoryRepository:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else user_data_dir() / "invoice_history.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        try:
            with self._connect() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise RuntimeError(f"History database version {version} is newer than supported version {SCHEMA_VERSION}.")
                if version == 0:
                    db.executescript("""
                        CREATE TABLE IF NOT EXISTS invoice_history (
                            id INTEGER PRIMARY KEY,
                            invoice_number TEXT NOT NULL DEFAULT '',
                            customer_name TEXT NOT NULL DEFAULT '',
                            source_filename TEXT NOT NULL DEFAULT '',
                            output_path TEXT NOT NULL DEFAULT '',
                            template TEXT NOT NULL DEFAULT '',
                            currency TEXT NOT NULL DEFAULT '',
                            total TEXT,
                            invoice_date TEXT NOT NULL DEFAULT '',
                            created_at TEXT NOT NULL,
                            status TEXT NOT NULL CHECK(status IN ('succeeded','failed')),
                            error_summary TEXT NOT NULL DEFAULT '',
                            fingerprint TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_history_created ON invoice_history(created_at DESC);
                        CREATE INDEX IF NOT EXISTS idx_history_fingerprint ON invoice_history(fingerprint);
                        CREATE INDEX IF NOT EXISTS idx_history_status ON invoice_history(status);
                    """)
                    db.execute("PRAGMA user_version = 1")
                    version = 1
                if version == 1:
                    columns = {row[1] for row in db.execute("PRAGMA table_info(invoice_history)")}
                    if "invoice_date" not in columns:
                        db.execute("ALTER TABLE invoice_history ADD COLUMN invoice_date TEXT NOT NULL DEFAULT ''")
                    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except sqlite3.DatabaseError as error:
            raise RuntimeError(f"Could not initialize invoice history database {self.path}: {error}") from error

    @staticmethod
    def fingerprint(invoice_number, customer_name, currency, total):
        identity = "\x1f".join(str(value or "").strip().casefold() for value in (invoice_number, customer_name, currency, total))
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def record(self, *, invoice_number="", customer_name="", source_filename="", output_path="", template="", currency="", total=None, invoice_date="", status="succeeded", error_summary=""):
        if status not in {"succeeded", "failed"}:
            raise ValueError("History status must be succeeded or failed.")
        fingerprint = self.fingerprint(invoice_number, customer_name, currency, total)
        try:
            with self._connect() as db:
                cursor = db.execute("""INSERT INTO invoice_history
                    (invoice_number,customer_name,source_filename,output_path,template,currency,total,invoice_date,created_at,status,error_summary,fingerprint)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (invoice_number, customer_name, source_filename, output_path, template, currency,
                     None if total is None else str(total), invoice_date, datetime.now(timezone.utc).isoformat(), status,
                     str(error_summary)[:1000], fingerprint))
                return cursor.lastrowid
        except sqlite3.DatabaseError as error:
            raise RuntimeError(f"Could not write invoice history: {error}") from error

    def search(self, query="", *, status=None, limit=500):
        clauses, params = [], []
        if query:
            clauses.append("(invoice_number LIKE ? OR customer_name LIKE ?)")
            term = f"%{query}%"
            params.extend((term, term))
        if status:
            if status not in {"succeeded", "failed"}:
                raise ValueError("Unsupported history status filter.")
            clauses.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            return tuple(dict(row) for row in db.execute(f"SELECT * FROM invoice_history{where} ORDER BY id DESC LIMIT ?", (*params, max(1, min(int(limit), 2000)))))

    def is_duplicate(self, invoice_number, customer_name, currency, total):
        fingerprint = self.fingerprint(invoice_number, customer_name, currency, total)
        with self._connect() as db:
            return db.execute("SELECT 1 FROM invoice_history WHERE fingerprint = ? AND status = 'succeeded' LIMIT 1", (fingerprint,)).fetchone() is not None

    def get(self, record_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM invoice_history WHERE id = ?", (int(record_id),)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def regeneration_eligible(record):
        # Source data are intentionally not copied into the history database.
        return bool(record.get("source_filename") and Path(record["source_filename"]).is_file())
