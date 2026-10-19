import json
import logging
import sqlite3

import pytest

from backend.app_logging import configure_logging
from backend.app_settings import AppSettings, SettingsStore
from backend.history_repository import HistoryRepository


def test_settings_first_run_defaults_and_persistence(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    assert store.load() == AppSettings()
    saved = AppSettings(company_name="Acme & Sons", preferred_template="modern_accent", default_currency="USD")
    store.save(saved)
    assert SettingsStore(store.path).load() == saved


def test_settings_migrates_unversioned_branding_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"company_name": "Legacy Co", "primary_color": "#112233"}), encoding="utf-8")
    loaded = SettingsStore(path).load()
    assert loaded.version == 1
    assert loaded.company_name == "Legacy Co"
    assert loaded.default_currency == "INR"


def test_settings_malformed_or_unsupported_values_are_actionable(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="Could not load InvoiceFlow settings"):
        SettingsStore(path).load()
    with pytest.raises(ValueError, match="hex color"):
        AppSettings(primary_color="blue").validate()
    with pytest.raises(ValueError, match="Unsupported default currency"):
        AppSettings(default_currency="XYZ").validate()


def test_missing_branding_assets_are_ignored_and_reset_persists_defaults(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save(AppSettings(logo_path=str(tmp_path / "missing.png")))
    assert store.load().branding().logo_path is None
    assert store.reset() == AppSettings()
    assert store.load() == AppSettings()


def test_history_schema_persistence_search_duplicate_and_eligibility(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("invoice", encoding="utf-8")
    repo = HistoryRepository(tmp_path / "history.sqlite3")
    assert repo.record(invoice_number="INV-1", customer_name="Northwind", source_filename=str(source), output_path="x.pdf", template="minimal", currency="INR", total="42.00")
    assert repo.record(invoice_number="INV-2", customer_name="Contoso", status="failed", error_summary="disk full")
    assert repo.is_duplicate("INV-1", "Northwind", "INR", "42.00")
    assert not repo.is_duplicate("INV-2", "Contoso", "INR", None)
    assert [row["invoice_number"] for row in repo.search("north")] == ["INV-1"]
    assert repo.search(status="failed")[0]["error_summary"] == "disk full"
    assert repo.regeneration_eligible(repo.search("INV-1")[0])
    assert not repo.regeneration_eligible(repo.search("INV-2")[0])
    with sqlite3.connect(repo.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_history_v1_schema_migrates_additively_to_invoice_dates(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE invoice_history (id INTEGER PRIMARY KEY, invoice_number TEXT NOT NULL DEFAULT '', customer_name TEXT NOT NULL DEFAULT '', source_filename TEXT NOT NULL DEFAULT '', output_path TEXT NOT NULL DEFAULT '', template TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL DEFAULT '', total TEXT, created_at TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('succeeded','failed')), error_summary TEXT NOT NULL DEFAULT '', fingerprint TEXT NOT NULL)")
        db.execute("PRAGMA user_version = 1")
    repo = HistoryRepository(path)
    record_id = repo.record(invoice_number="DATE-1", invoice_date="2026-10-18")
    assert repo.get(record_id)["invoice_date"] == "2026-10-18"
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_history_corrupt_database_is_not_silently_replaced(tmp_path):
    path = tmp_path / "history.sqlite3"
    path.write_bytes(b"not a database")
    with pytest.raises(RuntimeError, match="Could not initialize invoice history"):
        HistoryRepository(path)
    assert path.read_bytes() == b"not a database"


def test_rotating_logger_writes_without_secret_values(tmp_path):
    logger = configure_logging(tmp_path)
    logger.error("SMTP password=private-token api_key=private-key")
    for handler in logger.handlers:
        handler.flush()
    contents = (tmp_path / "invoiceflow.log").read_text(encoding="utf-8")
    assert "[REDACTED]" in contents
    assert "private-token" not in contents
    assert "private-key" not in contents
    assert isinstance(logger.handlers[0], logging.handlers.RotatingFileHandler)
