from datetime import date
from decimal import Decimal as D
import base64

import pytest

from backend.invoice_calculations import (
    CustomerInfo, Discount, DiscountKind, InvoiceDetails, InvoiceDocument,
    LineItem, TaxMode, TaxRule,
)
from backend.pdf_templates import (
    BrandingConfig, InvoiceTemplate, render_invoice_template_pdf,
)


def invoice(items=None, **kwargs):
    return InvoiceDocument(
        details=InvoiceDetails(invoice_number="PDF-TEST-01", issue_date=date(2026, 5, 10)),
        customer=CustomerInfo("Example Customer", "12 Market Street", "buyer@example.test"),
        items=tuple(items or [LineItem("Consulting service", D("2"), D("50"))]),
        **kwargs,
    )


def _plain_pdf_text(path):
    """Decode uncompressed PDF text operators emitted by ReportLab Helvetica."""
    data = path.read_bytes()
    assert data.startswith(b"%PDF-")
    # Strings are encoded as PDF literal strings; tests use ASCII-only fixture text.
    import re
    values = re.findall(rb"\(((?:\\.|[^)])*)\)\s*Tj", data)
    return b" ".join(value.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\") for value in values).decode("latin-1")


@pytest.mark.parametrize("template", list(InvoiceTemplate))
def test_each_template_renders_readable_invoice_and_calculated_total(tmp_path, template):
    path = tmp_path / f"{template.value}.pdf"
    result = render_invoice_template_pdf(
        invoice(), path, template=template,
        branding=BrandingConfig(company_name="Northwind Studio", footer_text="Thank you for your business"),
    )

    assert path.read_bytes().startswith(b"%PDF-")
    assert result.grand_total == D("100.00")
    text = _plain_pdf_text(path)
    assert "Northwind Studio" in text
    assert "Example Customer" in text
    assert "Consulting service" in text
    assert "100.00" in text


def test_calculation_output_includes_discount_shipping_and_tax(tmp_path):
    doc = invoice(
        [LineItem("Widget", D("2"), D("100"), Discount(D("10"), DiscountKind.PERCENT))],
        currency="USD", invoice_discount=Discount(D("5")), shipping_charge=D("10"),
        tax_rule=TaxRule(TaxMode.SINGLE_RATE, D("10")), amount_paid=D("20"),
    )
    path = tmp_path / "calculated.pdf"

    result = render_invoice_template_pdf(doc, path, template=InvoiceTemplate.MODERN_ACCENT)

    assert result.grand_total == D("202.50")
    assert result.amount_payable == D("182.50")
    text = _plain_pdf_text(path)
    assert "Invoice total" in text
    assert "Amount payable" in text
    assert "182.50" in text
    assert "TAX" in text


def test_long_invoice_paginates_and_preserves_last_items_and_totals(tmp_path):
    items = [LineItem(f"Service row {index:03}", D("1"), D("1")) for index in range(1, 121)]
    path = tmp_path / "long.pdf"

    render_invoice_template_pdf(invoice(items), path, template=InvoiceTemplate.COMPACT_GRID)

    content = path.read_bytes()
    assert content.count(b"/Type /Page") > 1
    text = _plain_pdf_text(path)
    assert "Service row 001" in text
    assert "Service row 120" in text
    assert "Amount payable" in text


def test_optional_logo_signature_stamp_are_loaded_when_available(tmp_path):
    # A minimal valid 1x1 PNG; it is synthetic and exists only in tmp_path.
    image = tmp_path / "brand.png"
    image.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZioAAAAASUVORK5CYII="
    ))
    path = tmp_path / "assets.pdf"

    render_invoice_template_pdf(
        invoice(), path,
        branding=BrandingConfig(logo_path=image, signature_path=image, stamp_path=image),
    )

    assert path.stat().st_size > 1000


def test_missing_optional_assets_are_ignored(tmp_path):
    path = tmp_path / "missing-assets.pdf"
    render_invoice_template_pdf(
        invoice(), path,
        branding=BrandingConfig(logo_path=tmp_path / "missing.png", signature_path="", stamp_path=None),
    )
    assert path.read_bytes().startswith(b"%PDF-")


def test_user_text_is_escaped_and_invalid_calculation_preserves_existing_pdf(tmp_path):
    safe_path = tmp_path / "safe.pdf"
    doc = invoice([LineItem("<script>alert</script>", D("1"), D("5"))])
    render_invoice_template_pdf(doc, safe_path)
    assert b"<script>" not in safe_path.read_bytes()
    assert "script" in _plain_pdf_text(safe_path)
    assert "alert" in _plain_pdf_text(safe_path)

    existing = tmp_path / "existing.pdf"
    existing.write_bytes(b"existing user content")
    invalid = invoice([LineItem("Bad quantity", D("-1"), D("10"))])
    with pytest.raises(ValueError, match="quantity cannot be negative"):
        render_invoice_template_pdf(invalid, existing)
    assert existing.read_bytes() == b"existing user content"


def test_render_failure_keeps_existing_pdf_and_cleans_temporary_file(tmp_path, monkeypatch):
    import backend.pdf_templates as pdf_templates

    destination = tmp_path / "existing.pdf"
    destination.write_bytes(b"original pdf bytes")

    def fail_build(*args, **kwargs):
        raise OSError("synthetic renderer failure")

    monkeypatch.setattr(pdf_templates.SimpleDocTemplate, "build", fail_build)
    with pytest.raises(OSError, match="synthetic renderer failure"):
        render_invoice_template_pdf(invoice(), destination)

    assert destination.read_bytes() == b"original pdf bytes"
    assert list(tmp_path.glob("*.tmp.pdf")) == []


@pytest.mark.parametrize("kwargs", [
    {"template": "unknown"},
    {"branding": BrandingConfig(accent_color="blue")},
])
def test_invalid_template_configuration_fails_before_touching_destination(tmp_path, kwargs):
    path = tmp_path / "keep.pdf"
    path.write_bytes(b"preserve me")
    with pytest.raises(ValueError):
        render_invoice_template_pdf(invoice(), path, **kwargs)
    assert path.read_bytes() == b"preserve me"


def test_creates_output_folder_using_existing_application_convention(tmp_path):
    path = tmp_path / "new" / "nested" / "invoice.pdf"
    render_invoice_template_pdf(invoice(), path)
    assert path.is_file()


def test_password_protected_pdf_is_encrypted_and_password_is_not_embedded(tmp_path):
    path = tmp_path / "protected.pdf"
    render_invoice_template_pdf(invoice(), path, pdf_password="synthetic-secret-42")
    content = path.read_bytes()
    assert content.startswith(b"%PDF-")
    assert b"/Encrypt" in content
    assert b"synthetic-secret-42" not in content


def test_invalid_pdf_password_does_not_replace_existing_output(tmp_path):
    path = tmp_path / "protected.pdf"
    path.write_bytes(b"keep-existing-content")
    with pytest.raises(ValueError, match="at least 8 characters"):
        render_invoice_template_pdf(invoice(), path, pdf_password="short")
    assert path.read_bytes() == b"keep-existing-content"
