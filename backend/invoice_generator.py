from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from numbers import Real
from pathlib import Path
from typing import Mapping
import math
import json
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

# Extended fields are used by mapping and grouping workflows. The existing
# single-amount PDF path continues to use FIELD_ALIASES for compatibility.
EXTENDED_FIELD_ALIASES = {
    "item_description": [
        "item description", "description", "product description",
        "item", "product", "service",
    ],
    "quantity": ["quantity", "qty", "units"],
    "unit_price": ["unit price", "price per unit", "rate per unit"],
    "discount": ["discount", "discount amount", "discount rate"],
    "tax_rate": ["tax rate", "tax %", "gst rate", "vat rate"],
    "customer_email": ["customer email", "client email", "email", "e-mail"],
    "currency": ["currency", "currency code"],
}
CANONICAL_FIELD_ALIASES = {**FIELD_ALIASES, **EXTENDED_FIELD_ALIASES}
REQUIRED_SINGLE_AMOUNT_FIELDS = ("name", "amount")
OPTIONAL_INVOICE_FIELDS = tuple(
    field for field in CANONICAL_FIELD_ALIASES
    if field not in REQUIRED_SINGLE_AMOUNT_FIELDS
)


FIELD_LABELS = {
    "invoice_number": "invoice number",
    "name": "customer name",
    "address": "address",
    "date": "date",
    "amount": "amount",
    "item_description": "item description",
    "quantity": "quantity",
    "unit_price": "unit price",
    "discount": "discount",
    "tax_rate": "tax rate",
    "customer_email": "customer email",
    "currency": "currency",
}


@dataclass(frozen=True)
class ColumnMapping:
    """Explicit mapping from canonical invoice fields to source headers."""

    field_to_column: Mapping[str, object]

    def as_dict(self):
        return dict(self.field_to_column)

    def validate(self, source_columns, required_fields=REQUIRED_SINGLE_AMOUNT_FIELDS):
        return validate_column_mapping(
            self.field_to_column,
            source_columns=source_columns,
            required_fields=required_fields,
        )


@dataclass(frozen=True)
class InvoiceRowGroup:
    """A stable, ordered group of source rows belonging to one invoice."""

    invoice_id: str
    rows: pd.DataFrame


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


def normalize_text_value(value):
    """Trim text and map conventional null tokens to None without recasing."""
    if _is_blank(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        if value.casefold() in {"null", "none", "nan"}:
            return None
        return value
    return value


def normalize_date_value(value, input_format=None, output_format="%d-%m-%Y"):
    """Normalize dates; absent a format, accept ISO dates/timestamps only."""
    value = normalize_text_value(value)
    if value is None:
        return ""

    if isinstance(value, pd.Timestamp):
        parsed = value.to_pydatetime()
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        try:
            parsed = (
                datetime.strptime(value, input_format)
                if input_format
                else datetime.fromisoformat(value.replace("Z", "+00:00"))
            )
        except ValueError as error:
            if input_format is None:
                reason = "ambiguous or invalid; provide input_format explicitly"
            else:
                reason = f"does not match format {input_format!r}"
            raise ValueError(f"Invalid invoice date {value!r}: {reason}.") from error
    else:
        raise ValueError(f"Invalid invoice date value: {value!r}.")

    return parsed.strftime(output_format)


def normalize_numeric_value(
    value,
    *,
    field="number",
    decimal_separator=None,
    thousands_separator=None,
    currency_symbol=None,
):
    """Parse a finite Decimal with explicit policy for separated strings.

    Plain integer strings and numeric Python values are unambiguous. Strings
    containing punctuation require a declared decimal separator; grouping
    punctuation is validated rather than silently removed.
    """
    if isinstance(value, Real) and not isinstance(value, bool) and not math.isfinite(value):
        raise ValueError(f"Invalid {field}: value must be finite.")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError(f"Invalid {field}: value must be finite.")
    value = normalize_text_value(value)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Invalid {field}: boolean values are not numeric.")

    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, Real):
        try:
            result = Decimal(str(value))
        except InvalidOperation as error:
            raise ValueError(f"Invalid {field}: {value!r}.") from error
    elif isinstance(value, str):
        text = value.strip()
        if currency_symbol:
            if text.startswith(currency_symbol):
                text = text[len(currency_symbol):].strip()
        elif re.search(r"[₹$€£]|\b(?:INR|USD|EUR|GBP|Rs\.?|Rs)\b", text, re.I):
            raise ValueError(
                f"Invalid {field}: currency prefix requires an explicit currency_symbol."
            )

        if any(separator in text for separator in ".,"):
            if decimal_separator not in {".", ","}:
                raise ValueError(
                    f"Ambiguous separators in {field} {value!r}; "
                    "provide decimal_separator explicitly."
                )
            if thousands_separator not in {None, ".", ","}:
                raise ValueError("thousands_separator must be '.' or ','.")
            if thousands_separator == decimal_separator:
                raise ValueError("Decimal and thousands separators must differ.")

            signless = text[1:] if text.startswith(("+", "-")) else text
            if signless.count(decimal_separator) > 1:
                raise ValueError(f"Invalid {field} separators: {value!r}.")
            integer_part, separator, fraction_part = signless.partition(decimal_separator)
            if separator and (not fraction_part or not fraction_part.isdigit()):
                raise ValueError(f"Invalid {field} fractional part: {value!r}.")
            if thousands_separator and thousands_separator in integer_part:
                groups = integer_part.split(thousands_separator)
                if (
                    not 1 <= len(groups[0]) <= 3
                    or not groups[0].isdigit()
                    or any(len(group) != 3 or not group.isdigit() for group in groups[1:])
                ):
                    raise ValueError(f"Invalid {field} thousands grouping: {value!r}.")
                integer_part = "".join(groups)
            elif not integer_part.isdigit():
                raise ValueError(f"Invalid {field}: {value!r}.")

            canonical = ("-" if text.startswith("-") else "") + integer_part
            if separator:
                canonical += "." + fraction_part
            text = canonical
        elif not re.fullmatch(r"[+-]?\d+", text):
            raise ValueError(f"Invalid {field}: {value!r}.")

        try:
            result = Decimal(text)
        except InvalidOperation as error:
            raise ValueError(f"Invalid {field}: {value!r}.") from error
    else:
        raise ValueError(f"Invalid {field}: {value!r}.")

    if not result.is_finite():
        raise ValueError(f"Invalid {field}: value must be finite.")
    return result


def _header_score(row):
    values = row.dropna().astype(str).str.strip()
    values = values[values != ""]
    if len(values) < 2:
        return float("-inf")

    known_headers = {
        normalize_column_name(alias)
        for aliases in CANONICAL_FIELD_ALIASES.values()
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
        raise ValueError("Header detection supports CSV and Excel files only.")

    best_row = 0
    best_score = float("-inf")
    for index, row in raw_df.iterrows():
        score = _header_score(row)
        if score > best_score:
            best_score = score
            best_row = index

    return best_row


def _json_object_without_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field name: {key!r}.")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value):
    raise ValueError(f"Unsupported non-standard JSON numeric constant: {value}.")


def read_input_file(file_path):
    path = Path(file_path)

    if not path.exists():
        raise ValueError("The selected input file does not exist.")
    if not path.is_file():
        raise ValueError("The selected input path is not a file.")

    suffix = path.suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xls", ".json"}:
        raise ValueError("Unsupported file format. Use CSV, Excel, or JSON.")

    if suffix == ".json":
        try:
            with path.open("r", encoding="utf-8-sig") as json_file:
                payload = json.load(
                    json_file,
                    object_pairs_hook=_json_object_without_duplicate_keys,
                    parse_constant=_reject_nonstandard_json_constant,
                )
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError(f"Could not read the selected JSON file: {error}") from error

        if isinstance(payload, dict) and set(payload) == {"invoices"}:
            records = payload["invoices"]
        elif isinstance(payload, list):
            records = payload
        else:
            raise ValueError(
                "Unsupported JSON structure. Use an array of invoice objects "
                "or an object containing an 'invoices' array."
            )

        if not isinstance(records, list) or not records:
            raise ValueError("The selected JSON file contains no invoice records.")
        if any(not isinstance(record, dict) for record in records):
            raise ValueError("Each JSON invoice record must be an object.")
        if any(
            not isinstance(key, str)
            or isinstance(value, (dict, list))
            for record in records
            for key, value in record.items()
        ):
            raise ValueError(
                "JSON invoice records must use string field names and scalar values."
            )
        df = pd.DataFrame.from_records(records)
        if df.empty or len(df.columns) == 0:
            raise ValueError("The selected JSON file contains no invoice records.")
        df = df.dropna(how="all").reset_index(drop=True)
        if df.empty:
            raise ValueError("The selected JSON file contains no invoice records.")
        return df

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
    if field not in CANONICAL_FIELD_ALIASES:
        raise ValueError(f"Unknown invoice field: {field}")

    normalized_columns = {}
    for column in df.columns:
        normalized = normalize_column_name(column)
        if normalized:
            normalized_columns.setdefault(normalized, []).append(column)

    aliases = [
        normalize_column_name(alias)
        for alias in CANONICAL_FIELD_ALIASES[field]
    ]
    alias_set = set(aliases)

    duplicate_exact_headers = [
        column_name
        for column_name, original_columns in normalized_columns.items()
        if column_name in alias_set and len(original_columns) > 1
    ]
    if duplicate_exact_headers:
        label = FIELD_LABELS[field]
        columns = ", ".join(str(column) for column in duplicate_exact_headers)
        raise ValueError(
            f"Ambiguous {label} columns found: {columns}. "
            "Please keep only one matching column."
        )

    if field == "name":
        # "Customer" and "Customer Name" are both exact aliases, so neither
        # should silently win merely because its alias is more specific.
        exact_columns = list(dict.fromkeys(
            column
            for column_name, original_columns in normalized_columns.items()
            if column_name in alias_set
            for column in original_columns
        ))
        if len(exact_columns) > 1:
            label = FIELD_LABELS[field]
            columns = ", ".join(str(column) for column in exact_columns)
            raise ValueError(
                f"Ambiguous {label} columns found: {columns}. "
                "Please keep only one matching column."
            )

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
    """Return legacy workflow mappings without changing its public shape."""
    return {
        field: find_column(df, field)
        for field in FIELD_ALIASES
    }


def suggest_column_mapping(df):
    """Suggest mappings for all supported canonical invoice fields."""
    return ColumnMapping({
        field: find_column(df, field)
        for field in CANONICAL_FIELD_ALIASES
    })


def validate_column_mapping(
    mapping,
    source_columns=None,
    required_fields=REQUIRED_SINGLE_AMOUNT_FIELDS,
):
    unknown_fields = [field for field in mapping if field not in CANONICAL_FIELD_ALIASES]
    if unknown_fields:
        raise ValueError(f"Unknown invoice mapping fields: {', '.join(unknown_fields)}")
    unknown_required = [
        field for field in required_fields if field not in CANONICAL_FIELD_ALIASES
    ]
    if unknown_required:
        raise ValueError(
            f"Unknown required invoice fields: {', '.join(unknown_required)}"
        )

    if source_columns is not None:
        source_columns = list(source_columns)
        missing_columns = [
            str(column)
            for column in mapping.values()
            if column is not None and column not in source_columns
        ]
        if missing_columns:
            raise ValueError(
                "Mapped source columns were not found: " + ", ".join(missing_columns)
            )

    chosen_columns = [column for column in mapping.values() if column is not None]
    if len(chosen_columns) != len(set(chosen_columns)):
        raise ValueError("A source column cannot be mapped to multiple invoice fields.")

    missing_fields = [
        field for field in required_fields
        if mapping.get(field) is None
    ]

    if missing_fields:
        missing = ", ".join(FIELD_LABELS[field] for field in missing_fields)
        raise ValueError(f"Required invoice columns not found: {missing}")

    return dict(mapping)


def apply_column_overrides(df, overrides, suggestions=None):
    """Apply manual field-to-header choices to automatic mapping suggestions."""
    unknown_fields = [field for field in overrides if field not in CANONICAL_FIELD_ALIASES]
    if unknown_fields:
        raise ValueError(f"Unknown invoice mapping fields: {', '.join(unknown_fields)}")
    if suggestions is None:
        # A manual choice must be able to resolve ambiguity for that same
        # field, so do not attempt automatic detection for overridden fields.
        mapping = {
            field: None if field in overrides else find_column(df, field)
            for field in CANONICAL_FIELD_ALIASES
        }
    else:
        mapping = (
            suggestions.as_dict()
            if isinstance(suggestions, ColumnMapping)
            else dict(suggestions)
        )
    mapping.update(overrides)
    validate_column_mapping(mapping, source_columns=df.columns)
    return ColumnMapping(mapping)


def validate_mapping(mapping):
    return validate_column_mapping(mapping)


def group_invoice_rows(
    df,
    invoice_id_column,
    invoice_level_columns=(),
):
    """Group rows by invoice ID in encounter order and reject conflicts.

    ``invoice_level_columns`` names source headers whose nonblank values must
    agree within a group. Item rows retain their original relative order.
    """
    if df is None or df.empty:
        raise ValueError("Cannot group an empty invoice data set.")
    if invoice_id_column not in df.columns:
        raise ValueError(f"Invoice grouping column not found: {invoice_id_column}")

    invoice_level_columns = tuple(invoice_level_columns or ())
    missing_columns = [column for column in invoice_level_columns if column not in df.columns]
    if missing_columns:
        raise ValueError(
            "Invoice-level columns not found: "
            + ", ".join(str(column) for column in missing_columns)
        )

    grouped_positions = {}
    normalized_values = {}
    for position in range(len(df)):
        row = df.iloc[position]
        raw_key = normalize_text_value(row[invoice_id_column])
        if raw_key is None:
            raise ValueError(
                f"Missing invoice identifier in row {position + 2} "
                f"({invoice_id_column})."
            )
        key = str(raw_key)
        grouped_positions.setdefault(key, []).append(position)
        normalized_values.setdefault(key, {column: [] for column in invoice_level_columns})

        for column in invoice_level_columns:
            value = normalize_text_value(row[column])
            if value is not None and value not in normalized_values[key][column]:
                normalized_values[key][column].append(value)

    for key, fields in normalized_values.items():
        conflicts = [column for column, values in fields.items() if len(values) > 1]
        if conflicts:
            raise ValueError(
                f"Conflicting invoice-level values for invoice {key!r}: "
                + ", ".join(str(column) for column in conflicts)
            )

    return [
        InvoiceRowGroup(invoice_id=key, rows=df.iloc[positions].copy())
        for key, positions in grouped_positions.items()
    ]


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
