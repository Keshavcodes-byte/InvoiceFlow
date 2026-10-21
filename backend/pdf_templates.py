"""Standalone, data-driven PDF templates for calculated InvoiceFlow invoices.

All monetary values and tax amounts are supplied by ``invoice_calculations``;
this module only formats and lays them out. Optional image paths are decorative.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import secrets
import tempfile
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)
from reportlab.lib.pdfencrypt import StandardEncryption

from .invoice_calculations import (
    CalculationResult,
    InvoiceDocument,
    calculate_invoice,
    format_currency,
)


class InvoiceTemplate(str, Enum):
    MINIMAL = "minimal"
    CORPORATE_CLASSIC = "corporate_classic"
    MODERN_ACCENT = "modern_accent"
    COMPACT_GRID = "compact_grid"


@dataclass(frozen=True)
class BrandingConfig:
    company_name: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""
    website: str = ""
    gstin: str = ""
    logo_path: str | os.PathLike | None = None
    signature_path: str | os.PathLike | None = None
    stamp_path: str | os.PathLike | None = None
    primary_color: str = "#253858"
    accent_color: str = "#2878B5"
    footer_text: str = ""


_PALETTES = {
    InvoiceTemplate.MINIMAL: ("#333333", "#777777", "#FFFFFF"),
    InvoiceTemplate.CORPORATE_CLASSIC: ("#23395B", "#406E8E", "#E9EEF3"),
    InvoiceTemplate.MODERN_ACCENT: ("#202A44", "#087E8B", "#E5F6F5"),
    InvoiceTemplate.COMPACT_GRID: ("#333333", "#555555", "#E8E8E8"),
}


def _color(value: str, field: str):
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        raise ValueError(f"{field} must be a six-digit hex color such as #336699.")
    try:
        return colors.HexColor(value)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{field} must be a six-digit hex color such as #336699.") from error


def _safe_text(value) -> str:
    if value is None:
        return ""
    return escape(str(value)).replace("\n", "<br/>")


def _paragraph(value, style):
    text = _safe_text(value)
    return Paragraph(text if text else "&nbsp;", style)


def _optional_image(path, width=30 * mm, height=18 * mm):
    """Return a scaled image when usable; missing or invalid art is omitted."""
    if not path:
        return None
    try:
        image_path = Path(path)
        if not image_path.is_file():
            return None
        reader = ImageReader(str(image_path))
        iw, ih = reader.getSize()
        if not iw or not ih:
            return None
        scale = min(width / iw, height / ih)
        return Image(str(image_path), width=iw * scale, height=ih * scale)
    except (OSError, ValueError, TypeError, RuntimeError):
        return None


def _styles(primary, accent):
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("InvoiceTitle", parent=base["Title"], fontName="Helvetica-Bold", fontSize=23, leading=27, textColor=primary, alignment=TA_RIGHT, spaceAfter=4),
        "heading": ParagraphStyle("InvoiceHeading", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=primary, spaceBefore=5, spaceAfter=3),
        "body": ParagraphStyle("InvoiceBody", parent=base["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor("#222222")),
        "small": ParagraphStyle("InvoiceSmall", parent=base["BodyText"], fontName="Helvetica", fontSize=7, leading=9, textColor=colors.HexColor("#444444")),
        "right": ParagraphStyle("InvoiceRight", parent=base["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11, alignment=TA_RIGHT),
        "white": ParagraphStyle("InvoiceTableHeader", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=7.5, leading=9, textColor=colors.white),
    }


def _money(value, currency):
    # Currency symbols outside ReportLab's built-in font repertoire are rendered
    # as the currency code so every PDF remains readable on standard viewers.
    formatted = format_currency(value, currency)
    return formatted.replace("₹", "INR ").replace("€", "EUR ").replace("£", "GBP ")


def _table_style(template, primary, accent, pale, compact=False):
    edge = colors.HexColor("#BBBBBB") if template is InvoiceTemplate.COMPACT_GRID else colors.white
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), accent),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.35 if template is InvoiceTemplate.COMPACT_GRID else 0, edge),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 if compact else 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 if compact else 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3 if compact else 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3 if compact else 5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, primary),
    ]
    if template is not InvoiceTemplate.MINIMAL:
        commands.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, pale]))
    return TableStyle(commands)


def _render_story(invoice, result, template, branding, primary, accent, pale, doc_width):
    styles = _styles(primary, accent)
    compact = template is InvoiceTemplate.COMPACT_GRID
    story = []

    logo = _optional_image(branding.logo_path, 36 * mm, 22 * mm)
    company_lines = [branding.company_name, branding.address, branding.email, branding.phone, branding.website]
    if branding.gstin:
        company_lines.append(f"GSTIN: {branding.gstin}")
    company = [_paragraph(line, styles["body"]) for line in company_lines if line]
    left_header = [logo] if logo else []
    left_header.extend(company)
    right_header = [Paragraph("INVOICE", styles["title"]), _paragraph(f"Invoice #: {invoice.details.invoice_number or '—'}", styles["right"])]
    header = Table([[left_header, right_header]], colWidths=[doc_width * .62, doc_width * .38])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 1, accent),
    ]))
    story.extend([header, Spacer(1, 8)])

    issue = invoice.details.issue_date.isoformat() if invoice.details.issue_date else ""
    due = invoice.details.due_date.isoformat() if invoice.details.due_date else ""
    info = [
        [_paragraph("BILL TO", styles["heading"]), _paragraph("INVOICE DETAILS", styles["heading"])],
        [_paragraph(invoice.customer.name, styles["body"]), _paragraph(f"Date: {issue or '—'}", styles["body"])],
        [_paragraph(invoice.customer.address, styles["body"]), _paragraph(f"Due date: {due or '—'}", styles["body"])],
        [_paragraph(invoice.customer.email, styles["body"]), _paragraph(f"Payment status: {result.payment_status.value}", styles["body"])],
    ]
    if invoice.customer.identifier:
        info.insert(2, [_paragraph(f"Customer ID: {invoice.customer.identifier}", styles["body"]), _paragraph("", styles["body"])])
    if invoice.details.terms:
        info.append([_paragraph("", styles["body"]), _paragraph(f"Terms: {invoice.details.terms}", styles["body"])])
    info_table = Table(info, colWidths=[doc_width * .52, doc_width * .48])
    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), pale),
        ("BOX", (0, 0), (-1, -1), .35, colors.HexColor("#D0D0D0")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.extend([info_table, Spacer(1, 12)])

    headers = ["Description", "Qty", "Unit price", "Discount", "Line total"]
    rows = [[_paragraph(item, styles["white"]) for item in headers]]
    for source, calc in zip(invoice.items, result.line_items):
        rows.append([
            _paragraph(source.description, styles["body"]),
            _paragraph(str(calc.quantity), styles["right"]),
            _paragraph(_money(calc.unit_price, result.currency), styles["right"]),
            _paragraph(_money(calc.discount_amount, result.currency), styles["right"]),
            _paragraph(_money(calc.net_amount, result.currency), styles["right"]),
        ])
    widths = [doc_width * .40, doc_width * .10, doc_width * .16, doc_width * .16, doc_width * .18]
    item_table = Table(rows, colWidths=widths, repeatRows=1, splitByRow=1, hAlign="LEFT")
    item_table.setStyle(_table_style(template, primary, accent, pale, compact))
    story.extend([item_table, Spacer(1, 9)])

    totals = [
        ("Subtotal", result.subtotal_before_discounts),
        ("Item discounts", -result.item_discount_total),
        ("Invoice discount", -result.invoice_discount),
        ("Shipping", result.shipping_charge),
        (f"Taxable base ({'inclusive' if result.tax.inclusive else 'exclusive'})", result.taxable_base),
    ]
    if result.tax.mode.value != "none":
        for label, amount in result.tax.components.items():
            totals.append((label.upper(), amount))
    totals.extend([
        ("Invoice total", result.grand_total),
        ("Paid", -result.amount_paid),
        ("Amount payable", result.amount_payable),
    ])
    total_rows = [[_paragraph(label, styles["body"]), _paragraph(_money(amount, result.currency), styles["right"])] for label, amount in totals]
    total_table = Table(total_rows, colWidths=[doc_width * .30, doc_width * .22], hAlign="RIGHT")
    total_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEABOVE", (0, -3), (-1, -3), .7, primary),
        ("BACKGROUND", (0, -1), (-1, -1), pale), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.extend([total_table, Spacer(1, 7)])

    payment = _paragraph(f"Payment status: {result.payment_status.value}", styles["heading"])
    signature = _optional_image(branding.signature_path, 42 * mm, 20 * mm)
    stamp = _optional_image(branding.stamp_path, 24 * mm, 24 * mm)
    art = [obj for obj in [signature, stamp] if obj is not None]
    if art:
        art_table = Table([[payment, art]], colWidths=[doc_width * .58, doc_width * .42])
        art_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("ALIGN", (1, 0), (1, 0), "RIGHT"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        story.append(art_table)
    else:
        story.append(payment)
    if branding.footer_text:
        story.extend([Spacer(1, 8), _paragraph(branding.footer_text, styles["small"])])
    return story


def render_invoice_template_pdf(
    invoice: InvoiceDocument,
    pdf_path: str | os.PathLike,
    *,
    template: InvoiceTemplate | str = InvoiceTemplate.MINIMAL,
    branding: BrandingConfig | None = None,
    pdf_password: str | None = None,
) -> CalculationResult:
    """Calculate and render an invoice, atomically replacing destination on success."""
    if not isinstance(invoice, InvoiceDocument):
        raise TypeError("invoice must be an InvoiceDocument.")
    try:
        template = InvoiceTemplate(template)
    except (ValueError, TypeError) as error:
        raise ValueError(f"Unknown invoice template: {template!r}.") from error
    branding = branding or BrandingConfig()
    if not isinstance(branding, BrandingConfig):
        raise TypeError("branding must be a BrandingConfig.")
    if pdf_password is not None and (not isinstance(pdf_password, str) or len(pdf_password) < 8 or "\x00" in pdf_password):
        raise ValueError("PDF password must contain at least 8 characters and no NUL bytes.")
    primary = _color(branding.primary_color, "primary_color")
    accent = _color(branding.accent_color, "accent_color")
    default_primary, default_accent, pale_hex = _PALETTES[template]
    # The selected template sets its own hierarchy; explicit branding colors
    # override the palette defaults.
    if branding.primary_color == BrandingConfig.primary_color:
        primary = colors.HexColor(default_primary)
    if branding.accent_color == BrandingConfig.accent_color:
        accent = colors.HexColor(default_accent)
    pale = colors.HexColor(pale_hex)

    result = calculate_invoice(invoice)
    destination = Path(pdf_path)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise OSError(f"Could not create the output folder: {destination.parent}") from error
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.stem}.", suffix=".tmp.pdf", dir=str(destination.parent))
    os.close(fd)
    try:
        doc = SimpleDocTemplate(temp_name, pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm, topMargin=15 * mm, bottomMargin=18 * mm, title=f"Invoice {invoice.details.invoice_number}", author=branding.company_name or "InvoiceFlow", pageCompression=0)
        story = _render_story(invoice, result, template, branding, primary, accent, pale, doc.width)

        def footer(canvas, document):
            canvas.saveState()
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#666666"))
            canvas.drawString(document.leftMargin, 9 * mm, _plain_footer(branding.footer_text))
            canvas.drawRightString(A4[0] - document.rightMargin, 9 * mm, f"Page {document.page}")
            canvas.restoreState()

        if pdf_password is not None:
            doc.encrypt = StandardEncryption(
                pdf_password, ownerPassword=secrets.token_urlsafe(32),
                canPrint=1, canModify=0, canCopy=0, canAnnotate=0, strength=128,
            )
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        with open(temp_name, "rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise OSError("Renderer did not produce a valid PDF file.")
        os.replace(temp_name, destination)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return result


def _plain_footer(value: str) -> str:
    # Canvas footer is a single line; strip markup and keep it within page width.
    return str(value or "").replace("\n", " ")[:150]

