"""Regression tests for InvoiceFlow's existing input and output behavior."""

import math
from decimal import Decimal

import pandas as pd
import pytest

from backend.invoice_generator import (
    _safe_pdf_filename,
    ColumnMapping,
    apply_column_overrides,
    build_invoices,
    detect_header_row,
    find_column,
    generate_pdfs,
    group_invoice_rows,
    load_invoice_input,
    map_columns,
    normalize_invoice_amount,
    normalize_date_value,
    normalize_numeric_value,
    normalize_text_value,
    read_input_file,
    suggest_column_mapping,
    validate_mapping,
)


def test_maps_common_invoice_headers():
    frame = pd.DataFrame(
        columns=["Invoice No", "Customer Name", "Address", "Date", "Grand Total"]
    )

    mapping = map_columns(frame)

    assert mapping == {
        "invoice_number": "Invoice No",
        "name": "Customer Name",
        "address": "Address",
        "date": "Date",
        "amount": "Grand Total",
    }
    assert validate_mapping(mapping) == mapping


def test_rejects_ambiguous_matching_columns():
    frame = pd.DataFrame(columns=["Customer", "Customer Name"])

    with pytest.raises(ValueError, match="Ambiguous customer name"):
        find_column(frame, "name")


@pytest.mark.parametrize(
    "headers",
    [
        ["Customer", "Customer Name"],
        ["Client", "Buyer Name"],
    ],
)
def test_rejects_multiple_exact_customer_name_aliases(headers):
    frame = pd.DataFrame(columns=headers)

    with pytest.raises(ValueError, match="Ambiguous customer name"):
        find_column(frame, "name")


def test_maps_a_single_customer_name_alias():
    frame = pd.DataFrame(columns=["Customer"])

    assert find_column(frame, "name") == "Customer"


def test_exact_customer_alias_wins_over_a_fuzzy_match():
    frame = pd.DataFrame(columns=["Customer Name", "Preferred Customer Name Notes"])

    assert find_column(frame, "name") == "Customer Name"


def test_customer_alias_normalization_preserves_unambiguous_match():
    frame = pd.DataFrame(columns=["  customer_name  "])

    assert find_column(frame, "name") == "  customer_name  "


def test_reads_csv_and_detects_header_after_preamble(tmp_path):
    csv_path = tmp_path / "invoices.csv"
    csv_path.write_text(
        "Invoice export,,\n,,\nInvoice No,Customer Name,Amount\nINV-1,Ada,125.50\n",
        encoding="utf-8",
    )

    assert detect_header_row(csv_path) == 2
    frame, mapping = load_invoice_input(csv_path)

    assert list(frame.columns) == ["Invoice No", "Customer Name", "Amount"]
    assert frame.iloc[0]["Customer Name"] == "Ada"
    assert mapping["name"] == "Customer Name"
    assert mapping["amount"] == "Amount"


def test_preserves_existing_csv_reading_for_plain_headers(tmp_path):
    csv_path = tmp_path / "plain.csv"
    csv_path.write_text(
        "Invoice No,Customer Name,Amount\nINV-CSV,Ada,12.50\n",
        encoding="utf-8",
    )

    frame = read_input_file(csv_path)

    assert frame.to_dict(orient="records") == [
        {"Invoice No": "INV-CSV", "Customer Name": "Ada", "Amount": 12.5}
    ]


def test_reads_json_array_of_invoice_objects(tmp_path):
    json_path = tmp_path / "invoices.json"
    json_path.write_text(
        '[{"Invoice No":"INV-J1","Customer Name":"Ada","Amount":12.5}]',
        encoding="utf-8",
    )

    frame = read_input_file(json_path)

    assert frame.to_dict(orient="records") == [
        {"Invoice No": "INV-J1", "Customer Name": "Ada", "Amount": 12.5}
    ]


def test_reads_json_object_with_invoices_array(tmp_path):
    json_path = tmp_path / "wrapped.json"
    json_path.write_text(
        '{"invoices":[{"Client":"Grace","Grand Total":40}]}',
        encoding="utf-8",
    )

    frame = read_input_file(json_path)

    assert frame.iloc[0]["Client"] == "Grace"
    assert frame.iloc[0]["Grand Total"] == 40


def test_rejects_malformed_json_with_actionable_error(tmp_path):
    json_path = tmp_path / "broken.json"
    json_path.write_text('{"invoices":[', encoding="utf-8")

    with pytest.raises(ValueError, match="Could not read the selected JSON file"):
        read_input_file(json_path)


def test_rejects_duplicate_json_field_names(tmp_path):
    json_path = tmp_path / "duplicate-key.json"
    json_path.write_text(
        '[{"Customer Name":"Ada","Customer Name":"Grace","Amount":10}]',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate JSON field name"):
        read_input_file(json_path)


def test_rejects_unsupported_json_structure(tmp_path):
    json_path = tmp_path / "unsupported.json"
    json_path.write_text('{"customers":[]}', encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported JSON structure"):
        read_input_file(json_path)


def test_rejects_json_with_no_records(tmp_path):
    json_path = tmp_path / "empty.json"
    json_path.write_text('{"invoices":[]}', encoding="utf-8")

    with pytest.raises(ValueError, match="contains no invoice records"):
        read_input_file(json_path)


def test_rejects_nested_values_in_json_invoice_records(tmp_path):
    json_path = tmp_path / "nested.json"
    json_path.write_text(
        '[{"Customer Name":"Ada","Items":[{"Description":"Book"}]}]',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="scalar values"):
        read_input_file(json_path)


def test_rejects_unsupported_file_extension(tmp_path):
    unsupported = tmp_path / "invoices.tsv"
    unsupported.write_text("Customer Name\tAmount\nAda\t10\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported file format"):
        read_input_file(unsupported)


def test_reads_xlsx_and_detects_header_after_blank_rows(tmp_path):
    pytest.importorskip("openpyxl", reason="XLSX input requires openpyxl")
    xlsx_path = tmp_path / "invoices.xlsx"
    pd.DataFrame(
        [{"Invoice No": "INV-2", "Customer Name": "Grace", "Amount": 75.0}]
    ).to_excel(xlsx_path, index=False, startrow=2)

    assert detect_header_row(xlsx_path) == 2
    frame = read_input_file(xlsx_path)

    assert frame.iloc[0]["Invoice No"] == "INV-2"
    assert frame.iloc[0]["Customer Name"] == "Grace"
    assert frame.iloc[0]["Amount"] == 75.0


def test_reads_xls_when_legacy_reader_and_fixture_writer_are_available(tmp_path):
    pytest.importorskip("xlrd", reason="Legacy XLS input requires xlrd")
    xlwt = pytest.importorskip(
        "xlwt", reason="Creating a synthetic legacy XLS fixture requires xlwt"
    )
    xls_path = tmp_path / "invoices.xls"
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("Invoices")
    for column, value in enumerate(["Invoice No", "Customer Name", "Amount"]):
        sheet.write(0, column, value)
    for column, value in enumerate(["INV-3", "Katherine", 88.25]):
        sheet.write(1, column, value)
    workbook.save(str(xls_path))

    frame = read_input_file(xls_path)

    assert frame.iloc[0]["Invoice No"] == "INV-3"
    assert frame.iloc[0]["Customer Name"] == "Katherine"
    assert frame.iloc[0]["Amount"] == 88.25


def test_rejects_missing_required_invoice_columns():
    frame = pd.DataFrame(columns=["Customer Name"])

    with pytest.raises(ValueError, match="amount"):
        validate_mapping(map_columns(frame))


def test_maps_arbitrary_headers_with_explicit_overrides():
    frame = pd.DataFrame(
        columns=["INV_ID", "Cust_Name", "Amt_Due", "Contact", "SKU_Text", "Units"]
    )
    suggestions = suggest_column_mapping(frame)
    mapping = apply_column_overrides(
        frame,
        {
            "invoice_number": "INV_ID",
            "name": "Cust_Name",
            "amount": "Amt_Due",
            "customer_email": "Contact",
            "item_description": "SKU_Text",
            "quantity": "Units",
        },
        suggestions=suggestions,
    )

    assert mapping.as_dict()["name"] == "Cust_Name"
    assert mapping.as_dict()["amount"] == "Amt_Due"
    assert mapping.as_dict()["customer_email"] == "Contact"
    assert mapping.as_dict()["item_description"] == "SKU_Text"
    assert mapping.validate(frame.columns)["quantity"] == "Units"


def test_suggests_all_supported_canonical_fields():
    headers = [
        "Invoice No", "Customer Name", "Address", "Invoice Date", "Amount",
        "Item Description", "Quantity", "Unit Price", "Discount", "Tax Rate",
        "Email", "Currency",
    ]
    mapping = suggest_column_mapping(pd.DataFrame(columns=headers)).as_dict()

    assert mapping == {
        "invoice_number": "Invoice No",
        "name": "Customer Name",
        "address": "Address",
        "date": "Invoice Date",
        "amount": "Amount",
        "item_description": "Item Description",
        "quantity": "Quantity",
        "unit_price": "Unit Price",
        "discount": "Discount",
        "tax_rate": "Tax Rate",
        "customer_email": "Email",
        "currency": "Currency",
    }


def test_column_mapping_rejects_missing_required_fields():
    mapping = ColumnMapping({"name": "Customer Name", "amount": None})

    with pytest.raises(ValueError, match="Required invoice columns not found: amount"):
        mapping.validate(["Customer Name"])


def test_amount_and_customer_are_required_but_other_fields_are_optional():
    mapping = ColumnMapping({"name": "Client", "amount": "Amount"})

    assert mapping.validate(["Client", "Amount"]) == {
        "name": "Client",
        "amount": "Amount",
    }


def test_column_mapping_rejects_a_header_not_in_the_source():
    mapping = ColumnMapping({"name": "Client", "amount": "Total"})

    with pytest.raises(ValueError, match="Mapped source columns were not found"):
        mapping.validate(["Client", "Amount"])


def test_manual_mapping_rejects_one_source_for_two_fields():
    frame = pd.DataFrame(columns=["Customer Name", "Amount"])
    suggestions = suggest_column_mapping(frame)

    with pytest.raises(ValueError, match="cannot be mapped to multiple"):
        apply_column_overrides(
            frame,
            {"amount": "Customer Name"},
            suggestions=suggestions,
        )


def test_suggested_mapping_rejects_ambiguous_customer_headers():
    frame = pd.DataFrame(columns=["Customer", "Customer Name", "Amount"])

    with pytest.raises(ValueError, match="Ambiguous customer name"):
        suggest_column_mapping(frame)


def test_manual_override_can_resolve_ambiguous_customer_headers():
    frame = pd.DataFrame(columns=["Customer", "Customer Name", "Amount"])

    mapping = apply_column_overrides(frame, {"name": "Customer"})

    assert mapping.as_dict()["name"] == "Customer"
    assert mapping.validate(frame.columns)["amount"] == "Amount"


def test_alias_normalization_does_not_merge_distinct_headers():
    frame = pd.DataFrame(columns=["Customer Name", "customer_name", "Amount"])

    with pytest.raises(ValueError, match="Ambiguous customer name"):
        suggest_column_mapping(frame)


def test_rejects_duplicate_physical_customer_headers():
    frame = pd.DataFrame(columns=["Customer Name", "Customer Name", "Amount"])

    with pytest.raises(ValueError, match="Ambiguous customer name"):
        suggest_column_mapping(frame)


def test_normalizes_whitespace_and_null_like_text_without_title_casing():
    assert normalize_text_value("  Acme logistics LLC  ") == "Acme logistics LLC"
    assert normalize_text_value("  NULL ") is None
    assert normalize_text_value("   ") is None


def test_normalizes_iso_dates_and_requires_policy_for_ambiguous_dates():
    assert normalize_date_value("2026-10-18") == "18-10-2026"
    assert normalize_date_value("18/10/2026", input_format="%d/%m/%Y") == "18-10-2026"

    with pytest.raises(ValueError, match="ambiguous or invalid"):
        normalize_date_value("01/02/2026")


def test_normalizes_numbers_only_with_an_explicit_separator_policy():
    assert normalize_numeric_value("125") == Decimal("125")
    assert normalize_numeric_value(pd.Series([125]).iloc[0]) == Decimal("125")
    assert normalize_numeric_value(
        "1,234.50", decimal_separator=".", thousands_separator=","
    ) == Decimal("1234.50")
    assert normalize_numeric_value(
        "1.234,50", decimal_separator=",", thousands_separator="."
    ) == Decimal("1234.50")

    with pytest.raises(ValueError, match="Ambiguous separators"):
        normalize_numeric_value("1,234")


def test_numeric_normalization_requires_explicit_currency_prefix():
    assert normalize_numeric_value("₹125.50", decimal_separator=".", currency_symbol="₹") == Decimal("125.50")

    with pytest.raises(ValueError, match="currency_symbol"):
        normalize_numeric_value("₹125.50", decimal_separator=".")


@pytest.mark.parametrize(
    "value",
    [
        math.inf,
        -math.inf,
        math.nan,
        pd.Series([math.nan], dtype="float32").iloc[0],
        Decimal("NaN"),
    ],
)
def test_numeric_normalization_rejects_non_finite_values(value):
    with pytest.raises(ValueError, match="finite"):
        normalize_numeric_value(value)


def test_numeric_normalization_rejects_malformed_grouping():
    with pytest.raises(ValueError, match="thousands grouping"):
        normalize_numeric_value(
            "12,34.50", decimal_separator=".", thousands_separator=","
        )


def test_groups_rows_by_invoice_id_and_preserves_order():
    frame = pd.DataFrame(
        [
            {"Invoice No": "INV-1", "Customer Name": "Ada", "Date": "2026-01-01", "Item": "A"},
            {"Invoice No": "INV-2", "Customer Name": "Grace", "Date": "2026-01-02", "Item": "B"},
            {"Invoice No": "INV-1", "Customer Name": "Ada", "Date": "2026-01-01", "Item": "C"},
        ]
    )

    groups = group_invoice_rows(
        frame,
        "Invoice No",
        invoice_level_columns=["Customer Name", "Date"],
    )

    assert [group.invoice_id for group in groups] == ["INV-1", "INV-2"]
    assert groups[0].rows["Item"].tolist() == ["A", "C"]
    assert groups[1].rows["Item"].tolist() == ["B"]


@pytest.mark.parametrize(
    ("column", "conflicting_values"),
    [("Customer Name", ("Ada", "Grace")), ("Date", ("2026-01-01", "2026-01-02"))],
)
def test_grouping_rejects_conflicting_invoice_level_data(column, conflicting_values):
    frame = pd.DataFrame(
        [
            {"Invoice No": "INV-1", "Customer Name": conflicting_values[0], "Date": "2026-01-01"},
            {"Invoice No": "INV-1", "Customer Name": conflicting_values[1], "Date": conflicting_values[1]},
        ]
    )

    with pytest.raises(ValueError, match="Conflicting invoice-level values"):
        group_invoice_rows(frame, "Invoice No", invoice_level_columns=[column])


def test_grouping_rejects_missing_invoice_identifiers():
    frame = pd.DataFrame([{"Invoice No": " ", "Item": "A"}])

    with pytest.raises(ValueError, match="Missing invoice identifier"):
        group_invoice_rows(frame, "Invoice No")


@pytest.mark.parametrize(
    "value",
    ["not an amount", math.inf, -math.inf, 1e309],
)
def test_rejects_malformed_and_non_finite_amounts(value):
    with pytest.raises(ValueError, match="Invalid invoice amount"):
        normalize_invoice_amount(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("₹1,234.50", 1234.5), ("1,234", 1234.0), (None, 0.0), ("", 0.0)],
)
def test_normalizes_existing_amount_inputs(value, expected):
    assert normalize_invoice_amount(value) == expected


def test_builds_an_invoice_from_a_single_record():
    frame = pd.DataFrame(
        [{"Customer": "Apex Logistics", "Amount": "41300", "Invoice No": "INV-1"}]
    )
    mapping = map_columns(frame)

    invoices = build_invoices(frame, mapping)

    assert len(invoices) == 1
    assert invoices[0].invoice_number == "INV-1"
    assert invoices[0].customer_name == "Apex Logistics"
    assert invoices[0].amount == 41300.0


@pytest.mark.parametrize(
    ("number", "expected"),
    [("INV/2026:01", "INV_2026_01.pdf"), ("", "Invoice_001.pdf"), ("CON", "Invoice_001_CON.pdf")],
)
def test_sanitizes_existing_pdf_filenames(number, expected):
    assert _safe_pdf_filename(number, 1) == expected


def test_sanitizes_all_windows_forbidden_filename_characters():
    assert _safe_pdf_filename('INV<>:"/\\|?*', 1) == "INV_________.pdf"


def test_truncates_long_invoice_numbers_to_supported_filename_length():
    filename = _safe_pdf_filename("A" * 250, 1)

    assert filename == f"{'A' * 180}.pdf"


def test_duplicate_invoice_numbers_fail_before_any_pdf_is_written(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    frame = pd.DataFrame(
        [
            {"Invoice No": "DUP-1", "Customer Name": "Ada", "Amount": 10},
            {"Invoice No": "DUP-1", "Customer Name": "Grace", "Amount": 20},
        ]
    )

    with pytest.raises(ValueError, match="Duplicate invoice identifier"):
        generate_pdfs(frame, output)

    assert list(output.glob("*.pdf")) == []


def test_generates_non_empty_pdf_and_reports_progress(tmp_path):
    output = tmp_path / "output"
    frame = pd.DataFrame(
        [{"Invoice No": "INV-PDF-1", "Customer Name": "Ada Lovelace", "Amount": 125.5}]
    )
    progress = []

    total = generate_pdfs(
        frame,
        output,
        progress_callback=lambda *values: progress.append(values),
    )

    pdf_path = output / "INV-PDF-1.pdf"
    assert total == 1
    assert pdf_path.is_file()
    assert pdf_path.stat().st_size > 0
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    assert progress == [(100, 1, 1)]
