from dataclasses import dataclass
from pathlib import Path
import math
import os
import re
import tempfile

import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


@dataclass
class Invoice:
    invoice_number: str
    customer_name: str
    date: str
    amount: float
    address: str = ""


FIELD_ALIASES = {
    "invoice_number": [
        "invoice no",
        "invoice number",
        "invoice",
        "bill no",
        "bill number",
    ],
    "name": [
        "name",
        "customer",
        "customer name",
        "client",
        "client name",
        "buyer",
        "buyer name",
    ],
    "address": [
        "address",
        "customer address",
        "client address",
        "billing address",
        "shipping address",
    ],
    "date": [
        "date",
        "invoice date",
        "billing date",
    ],
    "amount": [
        "amount",
        "total",
        "grand total",
        "price",
        "invoice amount",
        "total amount",
    ],
}


FIELD_LABELS = {
    "invoice_number": "invoice number",
    "name": "customer name",
    "address": "address",
    "date": "date",
    "amount": "amount",
}


def normalize_column_name(column):
    return re.sub(
        r"\s+",
        " ",
        str(column).strip().lower().replace("_", " ").replace("-", " "),
    ).strip()


def _is_blank(value):
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(value, str) and not value.strip()


def _header_score(row):
    values = row.dropna().astype(str).str.strip()
    values = values[values != ""]
    if len(values) < 2:
        return float("-inf")

    known_headers = {
        normalize_column_name(alias)
        for aliases in FIELD_ALIASES.values()
        for alias in aliases
    }

    score = min(len(values), 10)
    if len(values.unique()) == len(values):
        score += 3

    text_values = [
        value for value in values
        if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value)
    ]
    score += len(text_values) * 2

    for value in values:
        if normalize_column_name(value) in known_headers:
            score += 5

    numeric_count = len(values) - len(text_values)
    if numeric_count >= len(values) * 0.7:
        score -= 5

    return score


def detect_header_row(file_path):
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".csv":
        try:
            raw_df = pd.read_csv(path, header=None, nrows=20)
        except Exception as error:
            raise ValueError(
                f"Could not inspect the CSV header: {error}"
            ) from error
    elif suffix in {".xlsx", ".xls"}:
        try:
            raw_df = pd.read_excel(path, header=None, nrows=20)
        except Exception as error:
            raise ValueError(
                f"Could not inspect the Excel header: {error}"
            ) from error
    else:
        raise ValueError("Unsupported file format. Use Excel or CSV.")

    best_row = 0
    best_score = float("-inf")
    for index, row in raw_df.iterrows():
        score = _header_score(row)
        if score > best_score:
            best_score = score
            best_row = index

    return best_row


def read_input_file(file_path):
    path = Path(file_path)

    if not path.exists():
        raise ValueError("The selected input file does not exist.")
    if not path.is_file():
        raise ValueError("The selected input path is not a file.")

    suffix = path.suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xls"}:
        raise ValueError("Unsupported file format. Use Excel or CSV.")

    try:
        header_row = detect_header_row(path)
        if suffix == ".csv":
            df = pd.read_csv(path, header=header_row)
        else:
            df = pd.read_excel(path, header=header_row)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(
            f"Could not read the selected file: {error}"
        ) from error

    if df.empty:
        raise ValueError("The selected file contains no data.")

    # Remove rows that contain no values at all.
    df = df.dropna(how="all").reset_index(drop=True)
    if df.empty:
        raise ValueError("The selected file contains no invoice records.")

    return df


def find_column(df, field):
    if field not in FIELD_ALIASES:
        raise ValueError(f"Unknown invoice field: {field}")

    normalized_columns = {}
    for column in df.columns:
        normalized = normalize_column_name(column)
        if normalized:
            normalized_columns.setdefault(normalized, []).append(column)

    aliases = [normalize_column_name(alias) for alias in FIELD_ALIASES[field]]
    candidates = []

    for column_name, original_columns in normalized_columns.items():
        for alias in aliases:
            if column_name == alias:
                score = 100 + len(alias.split())
                candidates.extend((score, column) for column in original_columns)
            elif len(alias.split()) > 1 and set(alias.split()).issubset(column_name.split()):
                score = 60 + len(alias.split())
                candidates.extend((score, column) for column in original_columns)

    if not candidates:
        return None

    best_score = max(score for score, _ in candidates)
    best_columns = list(dict.fromkeys(
        column for score, column in candidates if score == best_score
    ))

    if len(best_columns) > 1:
        label = FIELD_LABELS[field]
        columns = ", ".join(str(column) for column in best_columns)
        raise ValueError(
            f"Ambiguous {label} columns found: {columns}. "
            "Please keep only one matching column."
        )

    return best_columns[0]


def map_columns(df):
    return {
        field: find_column(df, field)
        for field in FIELD_ALIASES
    }


def validate_mapping(mapping):
    required_fields = ("name", "amount")
    missing_fields = [
        field for field in required_fields
        if mapping.get(field) is None
    ]

    if missing_fields:
        missing = ", ".join(FIELD_LABELS[field] for field in missing_fields)
        raise ValueError(f"Required invoice columns not found: {missing}")

    return mapping


def normalize_invoice_date(value):
    if _is_blank(value):
        return ""

    if isinstance(value, (pd.Timestamp,)):
        return value.strftime("%d-%m-%Y")

    return str(value).strip()


def normalize_invoice_amount(value):
    if _is_blank(value):
        return 0.0

    if isinstance(value, bool):
        raise ValueError(f"Invalid invoice amount: {value}")

    if isinstance(value, str):
        value = (
            value.replace("₹", "")
            .replace(",", "")
            .strip()
        )

    try:
        amount = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid invoice amount: {value}") from error

    if not math.isfinite(amount):
        raise ValueError(f"Invalid invoice amount: {value}")

    return amount


def validate_invoice_data(df, mapping):
    if df.empty:
        raise ValueError("The input file contains no invoice records.")

    amount_column = mapping.get("amount")
    if not amount_column:
        raise ValueError("The invoice amount column could not be determined.")

    for row_index, value in enumerate(df[amount_column], start=2):
        try:
            normalize_invoice_amount(value)
        except ValueError as error:
            raise ValueError(
                f"Invalid invoice amount in row {row_index}: {value}"
            ) from error

    return True


def load_invoice_input(file_path):
    df = read_input_file(file_path)
    mapping = map_columns(df)
    validate_mapping(mapping)
    validate_invoice_data(df, mapping)
    return df, mapping


def build_invoices(df, mapping):
    validate_mapping(mapping)
    validate_invoice_data(df, mapping)

    invoices = []
    relevant_columns = [
        column for column in mapping.values()
        if column is not None
    ]

    for _, row in df.iterrows():
        if relevant_columns and all(
            _is_blank(row.get(column, ""))
            for column in relevant_columns
        ):
            continue

        invoice_number = row.get(mapping.get("invoice_number"), "")
        customer_name = row.get(mapping.get("name"), "")
        invoice_date = normalize_invoice_date(
            row.get(mapping.get("date"), "")
        )
        amount = normalize_invoice_amount(
            row.get(mapping.get("amount"), 0)
        )
        address = row.get(mapping.get("address"), "")

        invoices.append(
            Invoice(
                invoice_number="" if _is_blank(invoice_number) else str(invoice_number).strip(),
                customer_name="" if _is_blank(customer_name) else str(customer_name).strip(),
                date=invoice_date,
                amount=amount,
                address="" if _is_blank(address) else str(address).strip(),
            )
        )

    if not invoices:
        raise ValueError("The input file contains no invoice records.")

    return invoices


def _safe_pdf_filename(invoice_number, index):
    invoice_number = str(invoice_number or "").strip()
    if not invoice_number:
        return f"Invoice_{index:03}.pdf"

    safe_name = re.sub(r'[<>:"/\\|?*]', "_", invoice_number).strip(" .")
    if not safe_name:
        return f"Invoice_{index:03}.pdf"

    # Windows reserved device names cannot safely be used as filenames.
    if safe_name.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL",
        *{f"COM{i}" for i in range(1, 10)},
        *{f"LPT{i}" for i in range(1, 10)},
    }:
        safe_name = f"Invoice_{index:03}_{safe_name}"

    # Keep enough room for the output directory and .pdf extension.
    safe_name = safe_name[:180].rstrip(" .")
    return f"{safe_name}.pdf"


def _validate_output_folder(output_folder):
    path = Path(output_folder)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise OSError(
            f"Could not create the output folder: {path}"
        ) from error

    if not path.is_dir():
        raise OSError(f"The output path is not a folder: {path}")

    return path


def render_invoice_pdf(invoice, pdf_path):

    pdf = canvas.Canvas(

        str(pdf_path),

        pagesize=A4,

    )



    width, height = A4



    # -----------------------------

    # Page layout

    # -----------------------------



    left = 50

    right = width - 50

    top = height - 50



    # -----------------------------

    # Header

    # -----------------------------



    pdf.setFont(

        "Helvetica-Bold",

        26,

    )



    pdf.drawString(

        left,

        top,

        "INVOICE",

    )



    pdf.setFont(

        "Helvetica",

        10,

    )



    pdf.drawRightString(

        right,

        top + 2,

        f"Invoice #{invoice.invoice_number}",

    )



    # Header divider

    pdf.setLineWidth(1)



    pdf.line(

        left,

        top - 18,

        right,

        top - 18,

    )



    # -----------------------------

    # Invoice information

    # -----------------------------



    info_y = top - 55



    pdf.setFont(

        "Helvetica-Bold",

        10,

    )



    pdf.drawString(

        left,

        info_y,

        "BILL TO",

    )



    pdf.setFont(

        "Helvetica",

        11,

    )



    pdf.drawString(

        left,

        info_y - 20,

        invoice.customer_name,

    )



    if invoice.address:



        pdf.setFont(

            "Helvetica",

            9,

        )



        pdf.drawString(

            left,

            info_y - 37,

            invoice.address[:80],

        )



    # Date section



    pdf.setFont(

        "Helvetica-Bold",

        10,

    )



    pdf.drawString(

        right - 120,

        info_y,

        "INVOICE DATE",

    )



    pdf.setFont(

        "Helvetica",

        10,

    )



    pdf.drawRightString(

        right,

        info_y - 20,

        invoice.date,

    )



    # -----------------------------

    # Amount section

    # -----------------------------



    amount_top = info_y - 95



    pdf.setFillColorRGB(

        0.96,

        0.97,

        0.99,

    )



    pdf.roundRect(

        left,

        amount_top - 70,

        right - left,

        70,

        8,

        fill=1,

        stroke=0,

    )



    pdf.setFillColorRGB(

        0,

        0,

        0,

    )



    pdf.setFont(

        "Helvetica-Bold",

        11,

    )



    pdf.drawString(

        left + 18,

        amount_top - 28,

        "TOTAL AMOUNT",

    )



    pdf.setFont(

        "Helvetica-Bold",

        18,

    )



    pdf.drawRightString(

        right - 18,

        amount_top - 31,

        f"Rs. {invoice.amount:,.2f}",

    )



    # -----------------------------

    # Summary table

    # -----------------------------



    table_top = amount_top - 110



    pdf.setFont(

        "Helvetica-Bold",

        10,

    )



    pdf.drawString(

        left,

        table_top,

        "DESCRIPTION",

    )



    pdf.drawRightString(

        right,

        table_top,

        "AMOUNT",

    )



    pdf.setLineWidth(0.7)



    pdf.line(

        left,

        table_top - 10,

        right,

        table_top - 10,

    )



    row_y = table_top - 35



    pdf.setFont(

        "Helvetica",

        10,

    )



    pdf.drawString(

        left,

        row_y,

        "Invoice Total",

    )



    pdf.drawRightString(

        right,

        row_y,

        f"Rs. {invoice.amount:,.2f}",

    )



    pdf.line(

        left,

        row_y - 15,

        right,

        row_y - 15,

    )



    # -----------------------------

    # Grand total

    # -----------------------------



    total_y = row_y - 50



    pdf.setFont(

        "Helvetica-Bold",

        13,

    )



    pdf.drawString(

        left,

        total_y,

        "Grand Total",

    )



    pdf.drawRightString(

        right,

        total_y,

        f"Rs. {invoice.amount:,.2f}",

    )



    # -----------------------------

    # Footer

    # -----------------------------



    pdf.setFont(

        "Helvetica",

        8,

    )



    pdf.setFillColorRGB(

        0.4,

        0.4,

        0.4,

    )



    pdf.drawCentredString(

        width / 2,

        35,

        "Generated by InvoiceFlow",

    )



    pdf.save()





def generate_pdfs(
    df,
    output_folder,
    progress_callback=None,
):
    """Validate input and generate one PDF per invoice."""
    if df is None or df.empty:
        raise ValueError("Cannot generate invoices from an empty file.")

    mapping = map_columns(df)
    validate_mapping(mapping)
    validate_invoice_data(df, mapping)
    invoices = build_invoices(df, mapping)

    output_folder = _validate_output_folder(output_folder)
    total = len(invoices)

    # Detect duplicate final filenames before writing anything.
    filenames = {}
    for index, invoice in enumerate(invoices, start=1):
        filename = _safe_pdf_filename(invoice.invoice_number, index)
        if filename in filenames:
            previous = filenames[filename]
            raise ValueError(
                f"Duplicate invoice identifier detected: rows "
                f"{previous} and {index} would create '{filename}'."
            )
        filenames[filename] = index

    for index, invoice in enumerate(invoices, start=1):
        pdf_name = _safe_pdf_filename(invoice.invoice_number, index)
        pdf_path = output_folder / pdf_name
        temp_path = None

        try:
            with tempfile.NamedTemporaryFile(
                dir=output_folder,
                prefix=".invoiceflow_",
                suffix=".pdf.tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)

            render_invoice_pdf(invoice, temp_path)

            if not temp_path.exists() or temp_path.stat().st_size == 0:
                raise OSError("PDF generation produced an empty file.")

            os.replace(temp_path, pdf_path)
            temp_path = None

        except PermissionError as error:
            raise PermissionError(
                f"Could not write '{pdf_name}'. "
                "The file may be open in another program or the folder "
                "may not be writable."
            ) from error
        except OSError as error:
            raise OSError(
                f"Could not create invoice PDF '{pdf_name}': {error}"
            ) from error
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        if progress_callback:
            progress = int((index / total) * 100)
            progress_callback(progress, index, total)

    return total
