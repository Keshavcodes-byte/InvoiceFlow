from decimal import Decimal as D

import pandas as pd
import pytest

from backend.invoice_calculations import DiscountKind, TaxMode
from backend.invoice_generator import suggest_column_mapping
from backend.invoice_workflow import (
    WorkflowOptions,
    build_invoice_documents,
    export_invoice_batch,
    export_single_invoice,
)
from backend.pdf_templates import InvoiceTemplate


def test_legacy_single_amount_input_builds_invoice_document():
    frame = pd.DataFrame([{"Invoice No": "LEG-1", "Customer Name": "Ada", "Amount": 125.50}])
    mapping = suggest_column_mapping(frame)

    documents = build_invoice_documents(frame, mapping)

    assert len(documents) == 1
    assert documents[0].details.invoice_number == "LEG-1"
    assert documents[0].customer.name == "Ada"
    assert documents[0].items[0].unit_price == D("125.5")


def test_legacy_currency_prefixed_amount_uses_selected_currency_policy():
    frame = pd.DataFrame([{"Customer": "Ada", "Amount": "\u20b91,234.50"}])
    documents = build_invoice_documents(frame, {"name": "Customer", "amount": "Amount"})
    assert documents[0].items[0].unit_price == D("1234.50")


def test_manual_custom_headers_supports_calculation_options():
    frame = pd.DataFrame([{
        "INV_ID": "CUS-1", "Cust_Name": "Acme Ltd", "Product": "Design",
        "Qty": "2", "Rate": "100.00", "Tax_Rate": "10.00", "Currency": "USD",
    }])
    mapping = {
        "invoice_number": "INV_ID", "name": "Cust_Name", "item_description": "Product",
        "quantity": "Qty", "unit_price": "Rate", "tax_rate": "Tax_Rate", "currency": "Currency",
    }

    documents = build_invoice_documents(
        frame, mapping,
        options=WorkflowOptions(tax_mode=TaxMode.SINGLE_RATE, default_currency="INR"),
    )

    assert documents[0].currency == "USD"
    assert documents[0].items[0].quantity == D("2")
    assert documents[0].tax_rule.rate == D("10.00")


def test_multirow_item_invoice_groups_and_preserves_item_order():
    frame = pd.DataFrame([
        {"Invoice": "INV-2", "Client": "Acme", "Product": "First", "Qty": 1, "Unit Price": 10},
        {"Invoice": "INV-2", "Client": "Acme", "Product": "Second", "Qty": 2, "Unit Price": 15},
    ])
    mapping = {
        "invoice_number": "Invoice", "name": "Client", "item_description": "Product",
        "quantity": "Qty", "unit_price": "Unit Price",
    }

    documents = build_invoice_documents(frame, mapping)

    assert len(documents) == 1
    assert [item.description for item in documents[0].items] == ["First", "Second"]


def test_grouping_rejects_conflicting_customer_names():
    frame = pd.DataFrame([
        {"Invoice": "INV-2", "Client": "Acme", "Product": "First", "Rate": 10},
        {"Invoice": "INV-2", "Client": "Other", "Product": "Second", "Rate": 15},
    ])
    mapping = {"invoice_number": "Invoice", "name": "Client", "item_description": "Product", "unit_price": "Rate"}
    with pytest.raises(ValueError, match="Conflicting invoice-level values"):
        build_invoice_documents(frame, mapping)


def test_missing_amount_or_item_fields_is_actionable():
    frame = pd.DataFrame([{"Client": "Acme"}])
    with pytest.raises(ValueError, match="Map Amount"):
        build_invoice_documents(frame, {"name": "Client"})


def test_numeric_separator_policy_is_explicit_and_validated():
    frame = pd.DataFrame([{"Client": "Acme", "Total": "1.234,50"}])
    mapping = {"name": "Client", "amount": "Total"}
    with pytest.raises(ValueError, match="Invalid amount"):
        build_invoice_documents(frame, mapping)
    docs = build_invoice_documents(frame, mapping, options=WorkflowOptions(decimal_separator=","))
    assert docs[0].items[0].unit_price == D("1234.50")


def test_batch_export_uses_template_progress_and_refuses_existing_files(tmp_path):
    frame = pd.DataFrame([
        {"Invoice No": "B-1", "Customer Name": "Ada", "Amount": 50},
        {"Invoice No": "B-2", "Customer Name": "Grace", "Amount": 75},
    ])
    documents = build_invoice_documents(frame, suggest_column_mapping(frame))
    progress = []
    result = export_invoice_batch(
        documents, tmp_path, template=InvoiceTemplate.MODERN_ACCENT,
        progress_callback=lambda current, total: progress.append((current, total)),
    )
    assert result.succeeded == 2
    assert result.failed == 0
    assert progress == [(1, 2), (2, 2)]
    assert (tmp_path / "B-1.pdf").read_bytes().startswith(b"%PDF-")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        export_invoice_batch(documents, tmp_path, template=InvoiceTemplate.MINIMAL)


def test_single_export_refuses_to_overwrite(tmp_path):
    frame = pd.DataFrame([{"Invoice No": "SAFE-1", "Customer Name": "Ada", "Amount": 50}])
    document = build_invoice_documents(frame, suggest_column_mapping(frame))[0]
    export_single_invoice(document, tmp_path, template=InvoiceTemplate.MINIMAL)
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        export_single_invoice(document, tmp_path, template=InvoiceTemplate.MINIMAL)


def test_filename_pattern_sanitizes_tokens_and_preserves_extension(tmp_path):
    frame = pd.DataFrame([{"Invoice": "INV/7", "Client": "Acme: North", "Date": "2026-10-18", "Amount": "10"}])
    mapping = {"invoice_number": "Invoice", "name": "Client", "date": "Date", "amount": "Amount"}
    document = build_invoice_documents(frame, mapping)[0]
    path = export_single_invoice(document, tmp_path, template="minimal", filename_pattern="{InvoiceNo}_{ClientName}_{Date}.pdf")
    assert path.name == "INV_7_Acme_ North_2026-10-18.pdf"
    assert path.read_bytes().startswith(b"%PDF-")


@pytest.mark.parametrize("pattern", ["../{InvoiceNo}.pdf", "{BadField}.pdf", "{InvoiceNo", "subdir/{InvoiceNo}.pdf"])
def test_filename_pattern_rejects_unsafe_or_unknown_syntax(tmp_path, pattern):
    frame = pd.DataFrame([{"Invoice": "INV-1", "Client": "Acme", "Amount": "10"}])
    document = build_invoice_documents(frame, {"invoice_number": "Invoice", "name": "Client", "amount": "Amount"})[0]
    with pytest.raises(ValueError, match="pattern|placeholder|field"):
        export_single_invoice(document, tmp_path, template="minimal", filename_pattern=pattern)


def test_filename_pattern_collision_is_detected_before_rendering(tmp_path):
    frame = pd.DataFrame([
        {"Invoice": "INV-1", "Client": "Same", "Amount": "10"},
        {"Invoice": "INV-2", "Client": "Same", "Amount": "20"},
    ])
    docs = build_invoice_documents(frame, {"invoice_number": "Invoice", "name": "Client", "amount": "Amount"})
    with pytest.raises(ValueError, match="Duplicate invoice filename"):
        export_invoice_batch(docs, tmp_path, template="minimal", filename_pattern="{ClientName}.pdf")
    assert list(tmp_path.iterdir()) == []


def test_single_export_supports_explicit_pdf_password(tmp_path):
    frame = pd.DataFrame([{"Invoice No": "SEC-1", "Customer Name": "Ada", "Amount": 50}])
    document = build_invoice_documents(frame, suggest_column_mapping(frame))[0]
    output = export_single_invoice(document, tmp_path, template=InvoiceTemplate.MINIMAL, pdf_password="synthetic-password")
    content = output.read_bytes()
    assert content.startswith(b"%PDF-")
    assert b"/Encrypt" in content
    assert b"synthetic-password" not in content


def test_batch_reports_partial_render_failure_and_keeps_successful_output(tmp_path, monkeypatch):
    import backend.invoice_workflow as workflow

    frame = pd.DataFrame([
        {"Invoice No": "PART-1", "Customer Name": "Ada", "Amount": 50},
        {"Invoice No": "PART-2", "Customer Name": "Grace", "Amount": 75},
    ])
    docs = build_invoice_documents(frame, suggest_column_mapping(frame))
    original = workflow.render_invoice_template_pdf

    def render(document, path, **kwargs):
        if document.details.invoice_number == "PART-1":
            raise OSError("synthetic write failure")
        return original(document, path, **kwargs)

    monkeypatch.setattr(workflow, "render_invoice_template_pdf", render)
    result = export_invoice_batch(docs, tmp_path, template="minimal")
    assert (result.succeeded, result.failed) == (1, 1)
    assert result.results[0].error == "synthetic write failure"
    assert result.results[1].error is None
    assert not (tmp_path / "PART-1.pdf").exists()
    assert (tmp_path / "PART-2.pdf").read_bytes().startswith(b"%PDF-")
