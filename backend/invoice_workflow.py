"""Application-facing ingestion, invoice preparation, and template export services."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Callable, Mapping

import pandas as pd

from .invoice_calculations import CURRENCIES, CustomerInfo, Discount, DiscountKind, InvoiceDetails, InvoiceDocument, LineItem, TaxMode, TaxRule, calculate_invoice
from .invoice_generator import InvoiceRowGroup, _safe_pdf_filename, group_invoice_rows, normalize_date_value, normalize_numeric_value, normalize_text_value, validate_column_mapping
from .pdf_templates import BrandingConfig, InvoiceTemplate, render_invoice_template_pdf


@dataclass(frozen=True)
class WorkflowOptions:
    tax_mode: TaxMode = TaxMode.NONE
    inclusive_tax: bool = False
    discount_kind: DiscountKind = DiscountKind.FIXED
    default_currency: str = "INR"
    default_tax_rate: Decimal = Decimal("0")
    decimal_separator: str = "."


@dataclass(frozen=True)
class ExportItemResult:
    invoice_number: str
    output_path: Path | None
    error: str | None = None


@dataclass(frozen=True)
class BatchExportResult:
    results: tuple[ExportItemResult, ...]

    @property
    def succeeded(self) -> int:
        return sum(result.error is None for result in self.results)

    @property
    def failed(self) -> int:
        return sum(result.error is not None for result in self.results)


_EXPORT_LOCK = threading.Lock()


def _mapping_dict(mapping: Mapping | object) -> dict:
    if hasattr(mapping, "as_dict"):
        mapping = mapping.as_dict()
    if not isinstance(mapping, Mapping):
        raise TypeError("mapping must be a field-to-source-column mapping.")
    return dict(mapping)


def _column_value(row, mapping, field, default=None):
    column = mapping.get(field)
    if column is None:
        return default
    return normalize_text_value(row.get(column))


def _number(value, field, options, default=None, currency=None):
    if value is None:
        return default
    thousands = "," if options.decimal_separator == "." else "."
    symbol = CURRENCIES.get(str(currency or "").upper())
    return normalize_numeric_value(
        value,
        field=field,
        decimal_separator=options.decimal_separator,
        thousands_separator=thousands,
        currency_symbol=symbol.symbol if symbol else None,
    )


def _as_date(value):
    if value is None:
        return None
    text = normalize_date_value(value, output_format="%Y-%m-%d")
    return date.fromisoformat(text) if text else None


def build_invoice_documents(frame: pd.DataFrame, mapping: Mapping | object, *, options: WorkflowOptions | None = None) -> list[InvoiceDocument]:
    """Prepare typed invoices; item rows group only when item fields and IDs are mapped."""
    if frame is None or frame.empty:
        raise ValueError("The input file contains no invoice records.")
    options = options or WorkflowOptions()
    mapping = _mapping_dict(mapping)
    validate_column_mapping(mapping, source_columns=frame.columns, required_fields=("name",))
    line_mode = bool(mapping.get("item_description") and mapping.get("unit_price"))
    if not line_mode and not mapping.get("amount"):
        raise ValueError("Map Amount, or map both Item description and Unit price.")
    source_frame = frame.reset_index(drop=True)
    invoice_column = mapping.get("invoice_number")
    if line_mode and invoice_column:
        invoice_fields = ("name", "address", "date", "customer_email", "currency", "tax_rate")
        consistency_columns = [mapping[field] for field in invoice_fields if mapping.get(field) is not None]
        groups = group_invoice_rows(source_frame, invoice_column, consistency_columns)
    else:
        groups = [InvoiceRowGroup(invoice_id=str(i + 1), rows=source_frame.iloc[[i]].copy()) for i in range(len(source_frame))]

    documents = []
    for group in groups:
        records = [group.rows.iloc[i] for i in range(len(group.rows))]
        first = records[0]
        customer_name = _column_value(first, mapping, "name")
        if customer_name is None:
            raise ValueError(f"Customer name is blank for invoice {group.invoice_id!r}.")
        number = _column_value(first, mapping, "invoice_number")
        invoice_number = str(number) if number is not None else ""
        issue_date = _as_date(_column_value(first, mapping, "date"))
        currency_value = _column_value(first, mapping, "currency")
        currency = str(currency_value).upper() if currency_value else options.default_currency
        raw_tax = _column_value(first, mapping, "tax_rate")
        tax_rate = _number(raw_tax, "tax rate", options, options.default_tax_rate)
        if tax_rate is None:
            tax_rate = Decimal("0")
        if line_mode:
            items = []
            for item_index, row in enumerate(records, start=1):
                description = _column_value(row, mapping, "item_description")
                if description is None:
                    raise ValueError(f"Item description is blank in invoice {invoice_number!r}, item {item_index}.")
                quantity = _number(_column_value(row, mapping, "quantity"), "quantity", options, Decimal("1"))
                row_currency_value = _column_value(row, mapping, "currency")
                row_currency = str(row_currency_value).upper() if row_currency_value else options.default_currency
                unit_price = _number(_column_value(row, mapping, "unit_price"), "unit price", options, currency=row_currency)
                if quantity is None or unit_price is None:
                    raise ValueError(f"Quantity or unit price is missing in invoice {invoice_number!r}, item {item_index}.")
                discount_value = _number(_column_value(row, mapping, "discount"), "discount", options, currency=row_currency)
                discount = Discount(discount_value, options.discount_kind) if discount_value is not None else None
                items.append(LineItem(str(description), quantity, unit_price, discount))
            invoice_discount = None
        else:
            amount = _number(_column_value(first, mapping, "amount"), "amount", options, currency=currency)
            if amount is None:
                raise ValueError(f"Invoice amount is blank in row {len(documents) + 2}.")
            items = [LineItem("Invoice amount", Decimal("1"), amount)]
            discount_value = _number(_column_value(first, mapping, "discount"), "discount", options, currency=currency)
            invoice_discount = Discount(discount_value, options.discount_kind) if discount_value is not None else None
        document = InvoiceDocument(
            details=InvoiceDetails(invoice_number=invoice_number, issue_date=issue_date),
            customer=CustomerInfo(name=str(customer_name), address=str(_column_value(first, mapping, "address") or ""), email=str(_column_value(first, mapping, "customer_email") or "")),
            items=tuple(items), currency=currency, invoice_discount=invoice_discount,
            tax_rule=TaxRule(mode=options.tax_mode, rate=tax_rate, inclusive=options.inclusive_tax),
        )
        calculate_invoice(document)
        documents.append(document)
    if not documents:
        raise ValueError("The input file contains no invoice records.")
    return documents


def _preflight_paths(documents, output_folder):
    paths, names = [], set()
    for index, document in enumerate(documents, start=1):
        name = _safe_pdf_filename(document.details.invoice_number, index)
        if name in names:
            raise ValueError(f"Duplicate invoice identifier would create '{name}'.")
        names.add(name)
        path = output_folder / name
        if path.exists():
            raise FileExistsError(f"Output already exists; refusing to overwrite: {path}")
        paths.append(path)
    return paths


_FILENAME_FIELDS = {"InvoiceNo", "ClientName", "Date"}


def _pattern_filename(document, index, pattern):
    """Expand the supported filename tokens; each value is sanitized independently."""
    if pattern is None or pattern == "{InvoiceNo}.pdf":
        return _safe_pdf_filename(document.details.invoice_number, index)
    if not isinstance(pattern, str) or len(pattern) > 180 or ".." in pattern or "/" in pattern or "\\" in pattern:
        raise ValueError("Filename pattern contains an unsafe path or is too long.")
    tokens = re.findall(r"\{([^{}]+)\}", pattern)
    unknown = set(tokens) - _FILENAME_FIELDS
    if unknown:
        raise ValueError(f"Unsupported filename pattern field: {sorted(unknown)[0]}.")
    stripped = re.sub(r"\{[^{}]+\}", "", pattern)
    if "{" in stripped or "}" in stripped:
        raise ValueError("Filename pattern contains an invalid placeholder.")
    values = {
        "InvoiceNo": document.details.invoice_number or str(index),
        "ClientName": document.customer.name,
        "Date": document.details.issue_date.isoformat() if document.details.issue_date else "",
    }
    expanded = pattern
    for key, value in values.items():
        expanded = expanded.replace("{" + key + "}", _safe_pdf_filename(value, index)[:-4])
    if not expanded.casefold().endswith(".pdf"):
        expanded += ".pdf"
    # Sanitize the combined literal separators too, preventing reserved characters.
    return _safe_pdf_filename(Path(expanded).stem, index)


def _pattern_paths(documents, output_folder, pattern):
    paths, names = [], set()
    for index, document in enumerate(documents, start=1):
        name = _pattern_filename(document, index, pattern)
        if name.casefold() in names:
            raise ValueError(f"Duplicate invoice filename would create '{name}'.")
        names.add(name.casefold())
        target = output_folder / name
        if target.exists():
            raise FileExistsError(f"Output already exists; refusing to overwrite: {target}")
        paths.append(target)
    return paths


def export_invoice_batch(documents, output_folder, *, template: InvoiceTemplate | str, branding: BrandingConfig | None = None, progress_callback: Callable[[int, int], None] | None = None, filename_pattern: str = "{InvoiceNo}.pdf", pdf_password: str | None = None) -> BatchExportResult:
    """Export invoices serially, rejecting collisions and recording item failures."""
    documents = tuple(documents)
    if not documents:
        raise ValueError("There are no invoices to export.")
    destination = Path(output_folder)
    destination.mkdir(parents=True, exist_ok=True)
    with _EXPORT_LOCK:
        paths = _pattern_paths(documents, destination, filename_pattern)
        results = []
        for index, (document, path) in enumerate(zip(documents, paths), start=1):
            try:
                render_invoice_template_pdf(document, path, template=template, branding=branding, pdf_password=pdf_password)
                results.append(ExportItemResult(document.details.invoice_number, path))
            except Exception as error:
                results.append(ExportItemResult(document.details.invoice_number, None, str(error)))
            if progress_callback:
                progress_callback(index, len(documents))
        return BatchExportResult(tuple(results))


def export_single_invoice(document, output_folder, *, template: InvoiceTemplate | str, branding: BrandingConfig | None = None, filename_pattern: str = "{InvoiceNo}.pdf", pdf_password: str | None = None) -> Path:
    """Export one invoice with the same collision and serialization policy as a batch."""
    destination = Path(output_folder)
    destination.mkdir(parents=True, exist_ok=True)
    with _EXPORT_LOCK:
        path = _pattern_paths((document,), destination, filename_pattern)[0]
        render_invoice_template_pdf(document, path, template=template, branding=branding, pdf_password=pdf_password)
        return path


def render_invoice_preview(document, *, template, branding=None, directory=None) -> Path:
    """Render an export-identical temporary preview PDF."""
    directory = Path(directory or tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="InvoiceFlow_preview_", suffix=".pdf", dir=str(directory))
    os.close(fd)
    Path(name).unlink(missing_ok=True)
    render_invoice_template_pdf(document, name, template=template, branding=branding)
    return Path(name)
