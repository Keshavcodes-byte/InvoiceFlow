"""Deterministic Decimal-based invoice calculations, formatting, and wording."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from enum import Enum
import re
from typing import Mapping


ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
MAX_MONEY = Decimal("1e30")
MAX_QUANTITY = Decimal("1e12")


class DiscountKind(str, Enum):
    FIXED = "fixed"
    PERCENT = "percent"


class TaxMode(str, Enum):
    NONE = "none"
    SINGLE_RATE = "single_rate"
    CGST_SGST = "cgst_sgst"
    IGST = "igst"


class PaymentStatus(str, Enum):
    UNPAID = "unpaid"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"


@dataclass(frozen=True)
class CurrencySpec:
    code: str
    symbol: str
    minor_unit: str
    minor_unit_plural: str
    major_unit: str
    major_unit_plural: str
    minor_units: int = 2
    default_locale: str = "en_US"


CURRENCIES: Mapping[str, CurrencySpec] = {
    "INR": CurrencySpec("INR", "₹", "Paisa", "Paise", "Rupee", "Rupees", 2, "en_IN"),
    "USD": CurrencySpec("USD", "$", "Cent", "Cents", "Dollar", "Dollars", 2, "en_US"),
    "EUR": CurrencySpec("EUR", "€", "Cent", "Cents", "Euro", "Euros", 2, "de_DE"),
    "GBP": CurrencySpec("GBP", "£", "Penny", "Pence", "Pound", "Pounds", 2, "en_GB"),
}


@dataclass(frozen=True)
class InvoiceDetails:
    invoice_number: str = ""
    issue_date: date | None = None
    due_date: date | None = None
    terms: str = ""


@dataclass(frozen=True)
class CustomerInfo:
    name: str
    address: str = ""
    email: str = ""
    identifier: str = ""


@dataclass(frozen=True)
class Discount:
    """A fixed amount or percentage. Fixed discounts apply once per scope."""

    value: Decimal
    kind: DiscountKind = DiscountKind.FIXED


@dataclass(frozen=True)
class LineItem:
    description: str
    quantity: Decimal
    unit_price: Decimal
    discount: Discount | None = None


@dataclass(frozen=True)
class TaxRule:
    """Tax configuration supplied by the caller, never inferred from address."""

    mode: TaxMode = TaxMode.NONE
    rate: Decimal = ZERO
    inclusive: bool = False
    tax_shipping: bool = False


@dataclass(frozen=True)
class InvoiceDocument:
    details: InvoiceDetails
    customer: CustomerInfo
    items: tuple[LineItem, ...]
    currency: str = "INR"
    invoice_discount: Discount | None = None
    shipping_charge: Decimal = ZERO
    tax_rule: TaxRule = field(default_factory=TaxRule)
    amount_paid: Decimal = ZERO


@dataclass(frozen=True)
class LineItemCalculation:
    description: str
    quantity: Decimal
    unit_price: Decimal
    gross_amount: Decimal
    discount_amount: Decimal
    net_amount: Decimal


@dataclass(frozen=True)
class TaxBreakdown:
    mode: TaxMode
    rate: Decimal
    inclusive: bool
    taxable_base: Decimal
    total: Decimal
    components: Mapping[str, Decimal]


@dataclass(frozen=True)
class CalculationResult:
    currency: str
    line_items: tuple[LineItemCalculation, ...]
    subtotal_before_discounts: Decimal
    item_discount_total: Decimal
    subtotal_after_item_discounts: Decimal
    invoice_discount: Decimal
    subtotal_after_invoice_discount: Decimal
    shipping_charge: Decimal
    taxable_base: Decimal
    tax: TaxBreakdown
    grand_total: Decimal
    amount_paid: Decimal
    amount_payable: Decimal
    payment_status: PaymentStatus


def _currency_spec(currency: str) -> CurrencySpec:
    code = str(currency).upper()
    try:
        return CURRENCIES[code]
    except KeyError as error:
        raise ValueError(f"Unsupported currency: {currency!r}.") from error


def _decimal(value, field_name: str, *, default: Decimal | None = None) -> Decimal:
    if value is None and default is not None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite number.")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a finite number.") from error
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be a finite number.")
    return parsed


def _check_money_bound(value: Decimal, field_name: str) -> Decimal:
    if abs(value) > MAX_MONEY:
        raise ValueError(f"{field_name} exceeds the supported limit.")
    return value


def _quantum(spec: CurrencySpec) -> Decimal:
    return Decimal(1).scaleb(-spec.minor_units)


def _money(value: Decimal, spec: CurrencySpec, field_name: str) -> Decimal:
    _check_money_bound(value, field_name)
    try:
        with localcontext() as context:
            context.prec = 50
            return value.quantize(_quantum(spec), rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise ValueError(f"{field_name} cannot be rounded for {spec.code}.") from error


def _discount_kind(discount: Discount) -> DiscountKind:
    try:
        return DiscountKind(discount.kind)
    except ValueError as error:
        raise ValueError(f"Unsupported discount kind: {discount.kind!r}.") from error


def _calculate_discount(discount: Discount | None, base: Decimal, spec: CurrencySpec, label: str) -> Decimal:
    if discount is None:
        return ZERO.quantize(_quantum(spec))
    value = _decimal(discount.value, f"{label} value")
    if value < ZERO:
        raise ValueError(f"{label} cannot be negative.")
    kind = _discount_kind(discount)
    if kind is DiscountKind.PERCENT:
        if value > ONE_HUNDRED:
            raise ValueError(f"{label} percentage must be between 0 and 100.")
        amount = _money(base * value / ONE_HUNDRED, spec, label)
    else:
        amount = _money(value, spec, f"{label} value")
    if amount > base:
        raise ValueError(f"{label} cannot exceed its discountable amount.")
    return amount


def _tax_mode(rule: TaxRule) -> TaxMode:
    try:
        return TaxMode(rule.mode)
    except ValueError as error:
        raise ValueError(f"Unsupported tax mode: {rule.mode!r}.") from error


def _tax_components(mode: TaxMode, total: Decimal, spec: CurrencySpec) -> dict[str, Decimal]:
    if mode is TaxMode.NONE:
        return {}
    if mode is TaxMode.CGST_SGST:
        half = _money(total / Decimal("2"), spec, "CGST split")
        return {"cgst": half, "sgst": total - half}
    if mode is TaxMode.IGST:
        return {"igst": total}
    return {"tax": total}


def calculate_invoice(invoice: InvoiceDocument) -> CalculationResult:
    """Calculate a complete invoice using Decimal arithmetic and half-up rounding."""
    spec = _currency_spec(invoice.currency)
    if not invoice.customer.name or not invoice.customer.name.strip():
        raise ValueError("Customer name is required.")
    if (
        invoice.details.issue_date is not None
        and invoice.details.due_date is not None
        and invoice.details.due_date < invoice.details.issue_date
    ):
        raise ValueError("Invoice due date cannot be earlier than its issue date.")
    if not invoice.items:
        raise ValueError("At least one invoice line item is required.")

    mode = _tax_mode(invoice.tax_rule)
    rate = _decimal(invoice.tax_rule.rate, "Tax rate")
    if rate < ZERO or rate > ONE_HUNDRED:
        raise ValueError("Tax rate must be between 0 and 100 percent.")
    if mode is TaxMode.NONE and rate != ZERO:
        raise ValueError("Tax rate must be zero when tax mode is none.")
    if mode is TaxMode.NONE and invoice.tax_rule.inclusive:
        raise ValueError("Tax-inclusive pricing requires an active tax mode.")

    with localcontext() as context:
        context.prec = 50
        line_results = []
        subtotal = ZERO.quantize(_quantum(spec))
        item_discount_total = ZERO.quantize(_quantum(spec))

        for index, item in enumerate(invoice.items, start=1):
            description = item.description.strip() if isinstance(item.description, str) else ""
            if not description:
                raise ValueError(f"Line item {index} requires a description.")
            quantity = _decimal(item.quantity, f"Line item {index} quantity")
            unit_price = _decimal(item.unit_price, f"Line item {index} unit price")
            if quantity < ZERO:
                raise ValueError(f"Line item {index} quantity cannot be negative.")
            if quantity > MAX_QUANTITY:
                raise ValueError(f"Line item {index} quantity exceeds the supported limit.")
            if unit_price < ZERO:
                raise ValueError(f"Line item {index} unit price cannot be negative.")
            _check_money_bound(unit_price, f"Line item {index} unit price")

            gross = _money(quantity * unit_price, spec, f"Line item {index} total")
            item_discount = _calculate_discount(
                item.discount, gross, spec, f"Line item {index} discount"
            )
            net = _money(gross - item_discount, spec, f"Line item {index} net total")
            subtotal += gross
            item_discount_total += item_discount
            line_results.append(
                LineItemCalculation(
                    description=description,
                    quantity=quantity,
                    unit_price=unit_price,
                    gross_amount=gross,
                    discount_amount=item_discount,
                    net_amount=net,
                )
            )

        subtotal = _money(subtotal, spec, "Subtotal")
        item_discount_total = _money(item_discount_total, spec, "Item discounts")
        after_items = _money(subtotal - item_discount_total, spec, "Subtotal after item discounts")
        invoice_discount = _calculate_discount(
            invoice.invoice_discount, after_items, spec, "Invoice discount"
        )
        after_invoice_discount = _money(
            after_items - invoice_discount, spec, "Subtotal after invoice discount"
        )
        shipping = _money(_decimal(invoice.shipping_charge, "Shipping charge"), spec, "Shipping charge")
        if shipping < ZERO:
            raise ValueError("Shipping charge cannot be negative.")

        taxable_base = after_invoice_discount
        if invoice.tax_rule.tax_shipping:
            taxable_base += shipping
        taxable_base = _money(taxable_base, spec, "Taxable base")

        if mode is TaxMode.NONE or rate == ZERO:
            tax_total = ZERO.quantize(_quantum(spec))
        elif invoice.tax_rule.inclusive:
            tax_total = _money(
                taxable_base * rate / (ONE_HUNDRED + rate), spec, "Tax"
            )
        else:
            tax_total = _money(taxable_base * rate / ONE_HUNDRED, spec, "Tax")

        tax = TaxBreakdown(
            mode=mode,
            rate=rate,
            inclusive=invoice.tax_rule.inclusive,
            taxable_base=taxable_base,
            total=tax_total,
            components=_tax_components(mode, tax_total, spec),
        )
        taxable_shipping = shipping if invoice.tax_rule.tax_shipping else ZERO
        non_taxable_shipping = shipping - taxable_shipping
        if invoice.tax_rule.inclusive:
            grand_total = taxable_base + non_taxable_shipping
        else:
            grand_total = taxable_base + non_taxable_shipping + tax_total
        grand_total = _money(grand_total, spec, "Grand total")

        amount_paid = _money(_decimal(invoice.amount_paid, "Amount paid"), spec, "Amount paid")
        if amount_paid < ZERO:
            raise ValueError("Amount paid cannot be negative.")
        if amount_paid > grand_total:
            raise ValueError("Amount paid cannot exceed the invoice grand total.")
        amount_payable = _money(grand_total - amount_paid, spec, "Amount payable")
        if amount_payable == ZERO:
            status = PaymentStatus.PAID
        elif amount_paid > ZERO:
            status = PaymentStatus.PARTIALLY_PAID
        else:
            status = PaymentStatus.UNPAID

        return CalculationResult(
            currency=spec.code,
            line_items=tuple(line_results),
            subtotal_before_discounts=subtotal,
            item_discount_total=item_discount_total,
            subtotal_after_item_discounts=after_items,
            invoice_discount=invoice_discount,
            subtotal_after_invoice_discount=after_invoice_discount,
            shipping_charge=shipping,
            taxable_base=taxable_base,
            tax=tax,
            grand_total=grand_total,
            amount_paid=amount_paid,
            amount_payable=amount_payable,
            payment_status=status,
        )


def _group_words(value: int) -> str:
    ones = [
        "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
        "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
        "Seventeen", "Eighteen", "Nineteen",
    ]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
    words = []
    hundreds, remainder = divmod(value, 100)
    if hundreds:
        words.extend((ones[hundreds], "Hundred"))
    if remainder < 20:
        if remainder:
            words.append(ones[remainder])
    else:
        words.append(tens[remainder // 10])
        if remainder % 10:
            words.append(ones[remainder % 10])
    return " ".join(words)


def _integer_words(value: int, numbering_system: str) -> str:
    if value == 0:
        return "Zero"
    if value < 0:
        return "Minus " + _integer_words(-value, numbering_system)

    if numbering_system == "international":
        scales = ["", "Thousand", "Million", "Billion", "Trillion", "Quadrillion", "Quintillion"]
        chunks = []
        while value:
            chunks.append(value % 1000)
            value //= 1000
        if len(chunks) > len(scales):
            raise ValueError("Amount is too large for international amount-to-words conversion.")
        parts = [
            " ".join(part for part in (_group_words(chunk), scales[index]) if part)
            for index, chunk in enumerate(chunks)
            if chunk
        ]
    elif numbering_system == "indian":
        scales = ["", "Thousand", "Lakh", "Crore", "Arab", "Kharab", "Neel", "Padma", "Shankh"]
        chunks = [value % 1000]
        value //= 1000
        while value:
            chunks.append(value % 100)
            value //= 100
        if len(chunks) > len(scales):
            raise ValueError("Amount is too large for Indian amount-to-words conversion.")
        parts = [
            " ".join(part for part in (_group_words(chunk), scales[index]) if part)
            for index, chunk in enumerate(chunks)
            if chunk
        ]
    else:
        raise ValueError("numbering_system must be 'indian' or 'international'.")
    return " ".join(reversed(parts))


def amount_to_words(amount, currency: str, numbering_system: str | None = None) -> str:
    """Render a rounded amount in words; this is descriptive, not legal advice."""
    spec = _currency_spec(currency)
    value = _money(_decimal(amount, "Amount"), spec, "Amount")
    if numbering_system is None:
        numbering_system = "indian" if spec.code == "INR" else "international"
    negative = value < ZERO
    value = abs(value)
    whole = int(value)
    minor = int((value - Decimal(whole)) * (10 ** spec.minor_units))
    major_label = spec.major_unit if whole == 1 else spec.major_unit_plural
    parts = [_integer_words(whole, numbering_system), major_label]
    if minor:
        minor_label = spec.minor_unit if minor == 1 else spec.minor_unit_plural
        parts.extend(("and", _integer_words(minor, numbering_system), minor_label))
    if negative:
        parts.insert(0, "Minus")
    return " ".join(parts) + " Only"


def _indian_group(integer_text: str) -> str:
    if len(integer_text) <= 3:
        return integer_text
    last_three = integer_text[-3:]
    prefix = integer_text[:-3]
    groups = []
    while prefix:
        groups.append(prefix[-2:])
        prefix = prefix[:-2]
    return ",".join(reversed(groups)) + "," + last_three


def format_currency(amount, currency: str, locale: str | None = None) -> str:
    """Format money independently of the calculation and tax logic."""
    spec = _currency_spec(currency)
    value = _money(_decimal(amount, "Amount"), spec, "Amount")
    locale = locale or spec.default_locale
    if locale not in {"en_IN", "en_US", "en_GB", "de_DE"}:
        raise ValueError(f"Unsupported number-format locale: {locale!r}.")

    negative = value < ZERO
    fixed = f"{abs(value):.{spec.minor_units}f}"
    integer_text, fraction = fixed.split(".")
    if locale == "en_IN":
        grouped = _indian_group(integer_text)
        decimal_separator = "."
    elif locale == "de_DE":
        grouped = f"{int(integer_text):,}".replace(",", ".")
        decimal_separator = ","
    else:
        grouped = f"{int(integer_text):,}"
        decimal_separator = "."
    formatted_number = grouped + decimal_separator + fraction
    sign = "-" if negative else ""
    return f"{sign}{spec.symbol}{formatted_number}"
