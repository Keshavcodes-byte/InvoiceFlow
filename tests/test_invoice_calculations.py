from datetime import date
from decimal import Decimal

import pytest

from backend.invoice_calculations import (
    CustomerInfo,
    Discount,
    DiscountKind,
    InvoiceDetails,
    InvoiceDocument,
    LineItem,
    PaymentStatus,
    TaxMode,
    TaxRule,
    amount_to_words,
    calculate_invoice,
    format_currency,
)


D = Decimal


def make_invoice(
    items=None,
    *,
    currency="INR",
    invoice_discount=None,
    shipping="0",
    tax=None,
    amount_paid="0",
    details=None,
    customer=None,
):
    return InvoiceDocument(
        details=details or InvoiceDetails(invoice_number="INV-CALC-1"),
        customer=customer or CustomerInfo(name="Apex Logistics"),
        items=tuple(items if items is not None else [LineItem("Service", D("1"), D("100"))]),
        currency=currency,
        invoice_discount=invoice_discount,
        shipping_charge=D(shipping),
        tax_rule=tax or TaxRule(),
        amount_paid=D(amount_paid),
    )


def test_calculates_ordinary_line_and_invoice_totals():
    result = calculate_invoice(
        make_invoice([LineItem("Consulting", D("2"), D("50"))])
    )

    assert result.line_items[0].gross_amount == D("100.00")
    assert result.subtotal_before_discounts == D("100.00")
    assert result.grand_total == D("100.00")
    assert result.amount_payable == D("100.00")
    assert result.payment_status is PaymentStatus.UNPAID


def test_applies_percentage_and_fixed_item_discounts_once_per_line():
    result = calculate_invoice(
        make_invoice(
            [
                LineItem("A", D("2"), D("50"), Discount(D("10"), DiscountKind.PERCENT)),
                LineItem("B", D("1"), D("50"), Discount(D("5"), DiscountKind.FIXED)),
            ]
        )
    )

    assert [line.gross_amount for line in result.line_items] == [D("100.00"), D("50.00")]
    assert [line.discount_amount for line in result.line_items] == [D("10.00"), D("5.00")]
    assert [line.net_amount for line in result.line_items] == [D("90.00"), D("45.00")]
    assert result.item_discount_total == D("15.00")
    assert result.subtotal_after_item_discounts == D("135.00")


def test_fixed_item_discount_applies_once_not_per_quantity():
    result = calculate_invoice(
        make_invoice(
            [LineItem("Units", D("3"), D("10"), Discount(D("2"), DiscountKind.FIXED))]
        )
    )

    assert result.line_items[0].gross_amount == D("30.00")
    assert result.line_items[0].discount_amount == D("2.00")
    assert result.line_items[0].net_amount == D("28.00")


def test_applies_invoice_discount_then_shipping_then_exclusive_tax():
    result = calculate_invoice(
        make_invoice(
            [LineItem("Goods", D("1"), D("100"))],
            invoice_discount=Discount(D("10"), DiscountKind.PERCENT),
            shipping="10",
            tax=TaxRule(TaxMode.SINGLE_RATE, D("10")),
        )
    )

    assert result.subtotal_after_invoice_discount == D("90.00")
    assert result.taxable_base == D("90.00")
    assert result.tax.total == D("9.00")
    assert result.shipping_charge == D("10.00")
    assert result.grand_total == D("109.00")


def test_can_configure_shipping_as_taxable():
    result = calculate_invoice(
        make_invoice(
            [LineItem("Goods", D("1"), D("100"))],
            shipping="10",
            tax=TaxRule(TaxMode.SINGLE_RATE, D("10"), tax_shipping=True),
        )
    )

    assert result.taxable_base == D("110.00")
    assert result.tax.total == D("11.00")
    assert result.grand_total == D("121.00")


def test_extracts_single_rate_tax_from_inclusive_price():
    result = calculate_invoice(
        make_invoice(
            [LineItem("Inclusive item", D("1"), D("110"))],
            tax=TaxRule(TaxMode.SINGLE_RATE, D("10"), inclusive=True),
        )
    )

    assert result.tax.taxable_base == D("110.00")
    assert result.tax.total == D("10.00")
    assert result.grand_total == D("110.00")


def test_invoice_discount_is_applied_once_before_inclusive_tax_extraction():
    result = calculate_invoice(
        make_invoice(
            [LineItem("Inclusive item", D("1"), D("110"))],
            invoice_discount=Discount(D("10")),
            tax=TaxRule(TaxMode.SINGLE_RATE, D("10"), inclusive=True),
        )
    )

    assert result.subtotal_after_invoice_discount == D("100.00")
    assert result.tax.total == D("9.09")
    assert result.grand_total == D("100.00")


def test_inclusive_tax_and_non_taxable_shipping_are_not_double_counted():
    result = calculate_invoice(
        make_invoice(
            [LineItem("Inclusive item", D("1"), D("110"))],
            shipping="10",
            tax=TaxRule(TaxMode.SINGLE_RATE, D("10"), inclusive=True),
        )
    )

    assert result.tax.total == D("10.00")
    assert result.grand_total == D("120.00")


def test_calculates_cgst_and_sgst_as_configured_halves_of_combined_rate():
    result = calculate_invoice(
        make_invoice(
            tax=TaxRule(TaxMode.CGST_SGST, D("18")),
        )
    )

    assert result.tax.total == D("18.00")
    assert result.tax.components == {"cgst": D("9.00"), "sgst": D("9.00")}
    assert sum(result.tax.components.values()) == result.tax.total
    assert result.grand_total == D("118.00")


def test_cgst_sgst_rounding_remainder_keeps_split_equal_to_total():
    result = calculate_invoice(
        make_invoice(
            [LineItem("Small item", D("1"), D("0.05"))],
            tax=TaxRule(TaxMode.CGST_SGST, D("10")),
        )
    )

    assert result.tax.total == D("0.01")
    assert sum(result.tax.components.values()) == D("0.01")


def test_calculates_igst_using_the_configured_full_rate():
    result = calculate_invoice(
        make_invoice(tax=TaxRule(TaxMode.IGST, D("18")))
    )

    assert result.tax.components == {"igst": D("18.00")}
    assert result.grand_total == D("118.00")


def test_tax_none_has_no_tax_components():
    result = calculate_invoice(make_invoice())

    assert result.tax.total == D("0.00")
    assert result.tax.components == {}
    assert result.grand_total == D("100.00")


def test_rounds_line_totals_half_up_at_currency_precision():
    result = calculate_invoice(
        make_invoice([LineItem("Rounding", D("1"), D("1.005"))])
    )

    assert result.line_items[0].gross_amount == D("1.01")
    assert result.grand_total == D("1.01")


def test_zero_total_is_paid_and_has_zero_payable():
    result = calculate_invoice(
        make_invoice([LineItem("No charge", D("0"), D("100"))])
    )

    assert result.grand_total == D("0.00")
    assert result.amount_payable == D("0.00")
    assert result.payment_status is PaymentStatus.PAID


def test_supports_large_decimal_totals_without_float_arithmetic():
    result = calculate_invoice(
        make_invoice([LineItem("Large", D("1"), D("1234567890123456.78"))])
    )

    assert result.grand_total == D("1234567890123456.78")
    assert format_currency(result.grand_total, "USD") == "$1,234,567,890,123,456.78"


def test_calculates_partial_and_full_payment_status():
    partial = calculate_invoice(make_invoice(amount_paid="25"))
    paid = calculate_invoice(make_invoice(amount_paid="100"))

    assert partial.amount_payable == D("75.00")
    assert partial.payment_status is PaymentStatus.PARTIALLY_PAID
    assert paid.amount_payable == D("0.00")
    assert paid.payment_status is PaymentStatus.PAID


@pytest.mark.parametrize(
    ("currency", "expected"),
    [
        ("INR", "₹1,00,000.00"),
        ("USD", "$100,000.00"),
        ("EUR", "€100.000,00"),
        ("GBP", "£100,000.00"),
    ],
)
def test_formats_supported_currencies(currency, expected):
    assert format_currency(D("100000"), currency) == expected


def test_currency_formatting_accepts_an_explicit_locale_style():
    assert format_currency(D("1234.5"), "EUR", locale="en_US") == "€1,234.50"


def test_amount_to_words_supports_indian_and_international_scales():
    assert amount_to_words(D("1234.56"), "INR") == (
        "One Thousand Two Hundred Thirty Four Rupees and Fifty Six Paise Only"
    )
    assert amount_to_words(D("100.01"), "USD") == "One Hundred Dollars and One Cent Only"
    assert amount_to_words(
        D("123456789"), "INR", numbering_system="indian"
    ) == "Twelve Crore Thirty Four Lakh Fifty Six Thousand Seven Hundred Eighty Nine Rupees Only"


def test_amount_to_words_handles_zero_negative_and_currency_rounding():
    assert amount_to_words(D("0"), "INR") == "Zero Rupees Only"
    assert amount_to_words(D("-1.5"), "GBP") == "Minus One Pound and Fifty Pence Only"
    assert amount_to_words(D("1.999"), "INR") == "Two Rupees Only"


def test_amount_to_words_handles_large_international_amounts():
    assert amount_to_words(
        D("1000000000000000"), "USD"
    ) == "One Quadrillion Dollars Only"


@pytest.mark.parametrize(
    "item",
    [
        LineItem("Negative qty", D("-1"), D("10")),
        LineItem("Negative price", D("1"), D("-10")),
        LineItem("Infinite qty", D("Infinity"), D("10")),
        LineItem("", D("1"), D("10")),
        LineItem("Too much", D("1"), D("1e31")),
    ],
)
def test_rejects_invalid_line_items(item):
    with pytest.raises(ValueError):
        calculate_invoice(make_invoice([item]))


@pytest.mark.parametrize(
    "discount",
    [
        Discount(D("-1")),
        Discount(D("101"), DiscountKind.PERCENT),
        Discount(D("101")),
        Discount(D("NaN")),
    ],
)
def test_rejects_invalid_item_discounts(discount):
    invoice = make_invoice(
        [LineItem("Discounted", D("1"), D("100"), discount)]
    )

    with pytest.raises(ValueError):
        calculate_invoice(invoice)


@pytest.mark.parametrize("rate", [D("-1"), D("100.01"), D("Infinity")])
def test_rejects_invalid_tax_rates(rate):
    with pytest.raises(ValueError):
        calculate_invoice(make_invoice(tax=TaxRule(TaxMode.SINGLE_RATE, rate)))


def test_rejects_tax_rate_when_tax_mode_is_none():
    with pytest.raises(ValueError, match="rate must be zero"):
        calculate_invoice(make_invoice(tax=TaxRule(TaxMode.NONE, D("5"))))


def test_rejects_negative_shipping_and_overpayment():
    with pytest.raises(ValueError, match="Shipping charge cannot be negative"):
        calculate_invoice(make_invoice(shipping="-1"))
    with pytest.raises(ValueError, match="cannot exceed"):
        calculate_invoice(make_invoice(amount_paid="101"))


def test_rejects_unsupported_currency_and_inconsistent_dates():
    with pytest.raises(ValueError, match="Unsupported currency"):
        calculate_invoice(make_invoice(currency="CAD"))
    with pytest.raises(ValueError, match="due date cannot be earlier"):
        calculate_invoice(
            make_invoice(
                details=InvoiceDetails(
                    issue_date=date(2026, 2, 1), due_date=date(2026, 1, 1)
                )
            )
        )
