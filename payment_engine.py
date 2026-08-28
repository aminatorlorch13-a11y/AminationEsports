"""
AminationEsports Secure Multi-Currency Payment Engine

Financial rules:
- Decimal is mandatory for monetary calculations.
- Exchange rates are supplied externally and snapshotted.
- No live exchange-rate network request occurs in this module.
- A transaction's conversion can be locked so it cannot silently change.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_HALF_UP,
)
from math import isfinite
from typing import Final


# ------------------------------------------------------------
# Supported currencies
# ------------------------------------------------------------

SUPPORTED_CURRENCIES: Final[frozenset[str]] = frozenset(
    {
        "ZAR",
        "USD",
        "EUR",
        "GBP",
        "AUD",
        "CAD",
        "NZD",
        "CHF",
        "JPY",
        "CNY",
        "INR",
        "BRL",
        "NGN",
        "KES",
        "GHS",
        "AED",
    }
)


# Standard minor-unit precision for currencies used by the system.
# JPY is zero-decimal; the others currently use two decimals.
CURRENCY_DECIMALS: Final[dict[str, int]] = {
    "ZAR": 2,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "AUD": 2,
    "CAD": 2,
    "NZD": 2,
    "CHF": 2,
    "JPY": 0,
    "CNY": 2,
    "INR": 2,
    "BRL": 2,
    "NGN": 2,
    "KES": 2,
    "GHS": 2,
    "AED": 2,
}


class PaymentEngineError(ValueError):
    """Base exception for payment-engine validation errors."""


class UnsupportedCurrencyError(PaymentEngineError):
    """Raised when an unsupported currency is supplied."""


class InvalidAmountError(PaymentEngineError):
    """Raised when a monetary amount is invalid."""


class InvalidExchangeRateError(PaymentEngineError):
    """Raised when an exchange rate is invalid."""


class ConversionLockedError(PaymentEngineError):
    """Raised when a locked conversion is modified."""


def normalize_currency(currency: str) -> str:
    """
    Normalize and validate a currency code.
    """
    if not isinstance(currency, str):
        raise UnsupportedCurrencyError("Currency must be a string.")

    normalized = currency.strip().upper()

    if len(normalized) != 3:
        raise UnsupportedCurrencyError(
            f"Invalid currency code: {currency!r}"
        )

    if normalized not in SUPPORTED_CURRENCIES:
        raise UnsupportedCurrencyError(
            f"Unsupported currency: {normalized}"
        )

    return normalized


def currency_quantum(currency: str) -> Decimal:
    """
    Return the smallest monetary unit used for a currency.
    """
    currency = normalize_currency(currency)
    decimals = CURRENCY_DECIMALS[currency]

    return Decimal("1").scaleb(-decimals)


def to_decimal(value) -> Decimal:
    """
    Safely convert a value to Decimal.

    Strings are preferred for financial values.
    Floats are accepted only after converting through str(),
    avoiding direct binary-float contamination.
    """
    if isinstance(value, bool):
        raise InvalidAmountError("Boolean values are not valid monetary amounts.")

    try:
        if isinstance(value, Decimal):
            result = value
        else:
            result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise InvalidAmountError(
            f"Invalid monetary value: {value!r}"
        ) from None

    if not result.is_finite():
        raise InvalidAmountError(
            f"Monetary value must be finite: {value!r}"
        )

    return result


def validate_non_negative_amount(value, field_name: str = "amount") -> Decimal:
    """
    Validate a non-negative monetary amount.
    """
    amount = to_decimal(value)

    if amount < Decimal("0"):
        raise InvalidAmountError(
            f"{field_name} cannot be negative."
        )

    return amount


def validate_positive_rate(value) -> Decimal:
    """
    Validate an exchange rate.

    Example:
        1 ZAR = 0.054321 USD

    rate = 0.054321
    """
    rate = to_decimal(value)

    if rate <= Decimal("0"):
        raise InvalidExchangeRateError(
            "Exchange rate must be greater than zero."
        )

    # Defensive finite check.
    if not rate.is_finite():
        raise InvalidExchangeRateError(
            "Exchange rate must be finite."
        )

    # Prevent absurdly large accidental values.
    if rate > Decimal("1000000000"):
        raise InvalidExchangeRateError(
            "Exchange rate is outside the permitted safety range."
        )

    return rate


def quantize_money(amount, currency: str) -> Decimal:
    """
    Round an amount to the correct currency precision.
    """
    currency = normalize_currency(currency)
    amount = to_decimal(amount)

    quantum = currency_quantum(currency)

    return amount.quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    )


def convert_amount(
    base_amount,
    base_currency: str,
    payment_currency: str,
    exchange_rate,
) -> Decimal:
    """
    Convert a canonical/base amount into the player's payment currency.

    The supplied exchange rate means:

        1 base_currency = exchange_rate payment_currency
    """
    base_currency = normalize_currency(base_currency)
    payment_currency = normalize_currency(payment_currency)

    amount = validate_non_negative_amount(
        base_amount,
        "base_amount",
    )

    rate = validate_positive_rate(exchange_rate)

    # Same-currency conversion should normally use rate 1.
    if base_currency == payment_currency and rate != Decimal("1"):
        raise InvalidExchangeRateError(
            "Same-currency conversion requires an exchange rate of 1."
        )

    converted = amount * rate

    return quantize_money(
        converted,
        payment_currency,
    )


@dataclass(frozen=True)
class ConversionSnapshot:
    """
    Immutable record of the conversion decision.

    Once created, the calculation cannot accidentally be mutated.
    """

    base_currency: str
    base_amount: Decimal
    payment_currency: str
    payment_amount: Decimal
    exchange_rate: Decimal
    rate_source: str

    def __post_init__(self):
        object.__setattr__(
            self,
            "base_currency",
            normalize_currency(self.base_currency),
        )

        object.__setattr__(
            self,
            "payment_currency",
            normalize_currency(self.payment_currency),
        )

        object.__setattr__(
            self,
            "base_amount",
            quantize_money(
                validate_non_negative_amount(self.base_amount),
                self.base_currency,
            ),
        )

        object.__setattr__(
            self,
            "payment_amount",
            quantize_money(
                validate_non_negative_amount(self.payment_amount),
                self.payment_currency,
            ),
        )

        object.__setattr__(
            self,
            "exchange_rate",
            validate_positive_rate(self.exchange_rate),
        )

        if not isinstance(self.rate_source, str):
            raise InvalidExchangeRateError(
                "rate_source must be a string."
            )

        if not self.rate_source.strip():
            raise InvalidExchangeRateError(
                "rate_source cannot be empty."
            )


def create_conversion_snapshot(
    base_amount,
    base_currency: str,
    payment_currency: str,
    exchange_rate,
    rate_source: str,
) -> ConversionSnapshot:
    """
    Create a complete immutable conversion snapshot.
    """
    base_currency = normalize_currency(base_currency)
    payment_currency = normalize_currency(payment_currency)

    rate = validate_positive_rate(exchange_rate)

    payment_amount = convert_amount(
        base_amount=base_amount,
        base_currency=base_currency,
        payment_currency=payment_currency,
        exchange_rate=rate,
    )

    return ConversionSnapshot(
        base_currency=base_currency,
        base_amount=quantize_money(
            base_amount,
            base_currency,
        ),
        payment_currency=payment_currency,
        payment_amount=payment_amount,
        exchange_rate=rate,
        rate_source=rate_source,
    )


def verify_snapshot(snapshot: ConversionSnapshot) -> bool:
    """
    Recalculate a snapshot and confirm that its stored payment amount
    still matches the original conversion decision.
    """
    recalculated = convert_amount(
        snapshot.base_amount,
        snapshot.base_currency,
        snapshot.payment_currency,
        snapshot.exchange_rate,
    )

    return recalculated == snapshot.payment_amount
