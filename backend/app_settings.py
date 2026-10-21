"""Validated, versioned per-user InvoiceFlow preferences."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .pdf_templates import BrandingConfig, InvoiceTemplate

SETTINGS_VERSION = 1


def user_data_dir() -> Path:
    """Return a writable platform user-data location (or an explicit test override)."""
    override = os.environ.get("INVOICEFLOW_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(root) / "InvoiceFlow"
    if sys_platform := os.environ.get("XDG_CONFIG_HOME"):
        return Path(sys_platform) / "InvoiceFlow"
    return Path.home() / ".config" / "InvoiceFlow"


@dataclass(frozen=True)
class AppSettings:
    version: int = SETTINGS_VERSION
    company_name: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""
    website: str = ""
    gstin: str = ""
    logo_path: str = ""
    signature_path: str = ""
    stamp_path: str = ""
    primary_color: str = "#253858"
    accent_color: str = "#2878B5"
    footer_text: str = ""
    preferred_template: str = InvoiceTemplate.MINIMAL.value
    default_currency: str = "INR"
    decimal_separator: str = "."
    default_tax_rate: str = "0"
    tax_mode: str = "none"
    inclusive_tax: bool = False
    discount_kind: str = "fixed"
    filename_pattern: str = "{InvoiceNo}.pdf"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_tls_mode: str = "starttls"
    smtp_sender: str = ""
    smtp_password_env: str = "INVOICEFLOW_SMTP_PASSWORD"

    def validate(self) -> "AppSettings":
        if self.version != SETTINGS_VERSION:
            raise ValueError(f"Unsupported settings version: {self.version}")
        for key in ("primary_color", "accent_color"):
            value = getattr(self, key)
            if len(value) != 7 or value[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in value[1:]):
                raise ValueError(f"{key} must be a six-digit hex color.")
        if self.preferred_template not in {template.value for template in InvoiceTemplate}:
            raise ValueError("Unsupported preferred invoice template.")
        if self.default_currency not in {"INR", "USD", "EUR", "GBP"}:
            raise ValueError("Unsupported default currency.")
        if self.decimal_separator not in {".", ","}:
            raise ValueError("Decimal separator must be '.' or ','.")
        if self.tax_mode not in {"none", "single_rate", "cgst_sgst", "igst"}:
            raise ValueError("Unsupported tax mode.")
        if self.discount_kind not in {"fixed", "percent"}:
            raise ValueError("Unsupported discount kind.")
        if not self.filename_pattern or len(self.filename_pattern) > 180:
            raise ValueError("Filename pattern must contain 1 to 180 characters.")
        if not isinstance(self.smtp_port, int) or not 1 <= self.smtp_port <= 65535:
            raise ValueError("SMTP port must be an integer from 1 to 65535.")
        if self.smtp_tls_mode not in {"none", "starttls", "ssl"}:
            raise ValueError("SMTP TLS mode must be none, starttls, or ssl.")
        if "\n" in self.smtp_host or "\r" in self.smtp_host or "\n" in self.smtp_sender or "\r" in self.smtp_sender:
            raise ValueError("SMTP host and sender cannot contain line breaks.")
        if not self.smtp_password_env or not self.smtp_password_env.replace("_", "").isalnum():
            raise ValueError("SMTP password environment variable name is invalid.")
        try:
            from decimal import Decimal
            rate = Decimal(self.default_tax_rate)
            if not rate.is_finite() or rate < 0 or rate > 100:
                raise ValueError
        except Exception as error:
            raise ValueError("Default tax rate must be a finite value from 0 to 100.") from error
        return self

    def branding(self) -> BrandingConfig:
        fields = {key: getattr(self, key) for key in (
            "company_name", "address", "email", "phone", "website", "gstin",
            "primary_color", "accent_color", "footer_text",
        )}
        for key in ("logo_path", "signature_path", "stamp_path"):
            raw = getattr(self, key)
            fields[key] = raw if raw and Path(raw).is_file() else None
        return BrandingConfig(**fields)


class SettingsStore:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else user_data_dir() / "settings.json"

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Settings root must be a JSON object.")
            version = payload.get("version", 0)
            if version == 0:  # First unversioned settings format: branding-only values.
                payload = {**payload, "version": SETTINGS_VERSION}
            elif version != SETTINGS_VERSION:
                raise ValueError(f"Unsupported settings version: {version}")
            allowed = AppSettings.__dataclass_fields__.keys()
            return AppSettings(**{key: value for key, value in payload.items() if key in allowed}).validate()
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"Could not load InvoiceFlow settings at {self.path}: {error}") from error

    def save(self, settings: AppSettings) -> None:
        settings.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".invoiceflow-settings-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(asdict(settings), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise

    def reset(self) -> AppSettings:
        defaults = AppSettings()
        self.save(defaults)
        return defaults
