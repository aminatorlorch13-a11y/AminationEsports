"""
AminationEsports — PayFast Gateway Adapter

Secure custom PayFast integration.

Design goals:
- Sandbox by default.
- Production only when explicitly enabled.
- Secrets come from environment variables.
- Payment success is NEVER trusted from the browser.
- ITNs are independently validated.
- Payment amounts are compared using Decimal.
- Transaction references are validated against our database.
- The PayFast signature uses the documented custom-payment field order.
- The ITN signature uses the exact order received from PayFast.
- The public application URL is configurable and therefore domain-safe.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import socket
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen


# ============================================================
# PAYFAST ENDPOINTS
# ============================================================

PAYFAST_SANDBOX_PROCESS_URL = (
    "https://sandbox.payfast.co.za/eng/process"
)

PAYFAST_LIVE_PROCESS_URL = (
    "https://www.payfast.co.za/eng/process"
)

PAYFAST_SANDBOX_VALIDATE_URL = (
    "https://sandbox.payfast.co.za/eng/query/validate"
)

PAYFAST_LIVE_VALIDATE_URL = (
    "https://www.payfast.co.za/eng/query/validate"
)


# ============================================================
# EXCEPTIONS
# ============================================================

class PayFastConfigurationError(Exception):
    """Raised when PayFast configuration is incomplete."""


class PayFastValidationError(Exception):
    """Raised when PayFast payment data fails validation."""


class PayFastITNError(PayFastValidationError):
    """Raised when a PayFast ITN fails security validation."""


# ============================================================
# ENVIRONMENT
# ============================================================

def payfast_sandbox_enabled() -> bool:
    """
    Sandbox is the safe default.

    Only PAYFAST_MODE=live enables production.
    """
    mode = os.getenv("PAYFAST_MODE", "sandbox").strip().lower()
    return mode != "live"


def get_payfast_process_url() -> str:
    """Return the PayFast checkout endpoint."""
    if payfast_sandbox_enabled():
        return PAYFAST_SANDBOX_PROCESS_URL

    return PAYFAST_LIVE_PROCESS_URL


def get_payfast_validate_url() -> str:
    """Return the PayFast ITN validation endpoint."""
    if payfast_sandbox_enabled():
        return PAYFAST_SANDBOX_VALIDATE_URL

    return PAYFAST_LIVE_VALIDATE_URL


# ============================================================
# MERCHANT CONFIGURATION
# ============================================================

def get_payfast_merchant_id() -> str:
    merchant_id = os.getenv("PAYFAST_MERCHANT_ID", "").strip()

    if not merchant_id:
        raise PayFastConfigurationError(
            "PAYFAST_MERCHANT_ID is not configured."
        )

    return merchant_id


def get_payfast_merchant_key() -> str:
    merchant_key = os.getenv("PAYFAST_MERCHANT_KEY", "").strip()

    if not merchant_key:
        raise PayFastConfigurationError(
            "PAYFAST_MERCHANT_KEY is not configured."
        )

    return merchant_key


def get_payfast_passphrase() -> str | None:
    """
    Return the configured PayFast passphrase.

    The passphrase is secret and must never be exposed to
    templates, browser JavaScript, logs, or API responses.
    """
    value = os.getenv("PAYFAST_PASSPHRASE", "").strip()

    return value or None


# ============================================================
# PUBLIC APPLICATION URL
# ============================================================

def get_public_base_url() -> str:
    """
    Return the canonical public URL of AminationEsports.

    This makes the payment integration domain-independent.

    Example:

        PAYFAST_PUBLIC_BASE_URL=https://aminationesports.com

    Later, moving from Render to a custom domain only requires
    changing the environment variable.
    """
    value = os.getenv(
        "PAYFAST_PUBLIC_BASE_URL",
        "",
    ).strip().rstrip("/")

    if not value:
        raise PayFastConfigurationError(
            "PAYFAST_PUBLIC_BASE_URL is not configured."
        )

    if not value.startswith(("https://", "http://")):
        raise PayFastConfigurationError(
            "PAYFAST_PUBLIC_BASE_URL must begin with http:// or https://."
        )

    return value


# ============================================================
# MONEY
# ============================================================

MONEY_QUANTUM = Decimal("0.01")


def money_to_payfast_amount(amount) -> str:
    """
    Convert a monetary value to PayFast's two-decimal format.
    """
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PayFastValidationError(
            "Invalid payment amount."
        ) from exc

    if not value.is_finite():
        raise PayFastValidationError(
            "Payment amount must be finite."
        )

    if value <= Decimal("0"):
        raise PayFastValidationError(
            "PayFast payment amount must be greater than zero."
        )

    value = value.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )

    return f"{value:.2f}"


def validate_payfast_amount(
    expected_amount,
    received_amount,
) -> bool:
    """
    Compare two monetary values using Decimal.
    """
    try:
        expected = Decimal(str(expected_amount)).quantize(
            MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        )

        received = Decimal(str(received_amount)).quantize(
            MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        )

    except (InvalidOperation, ValueError, TypeError):
        return False

    return expected == received


# ============================================================
# URL ENCODING
# ============================================================

def _urlencode_payfast_value(value) -> str:
    """
    PayFast custom integration encoding.

    Spaces are represented as '+'.
    """
    return quote_plus(
        str(value).strip(),
        safe="",
    )


# ============================================================
# CUSTOM PAYMENT SIGNATURE
# ============================================================

def generate_payfast_signature(
    data: dict,
    passphrase: str | None = None,
) -> str:
    """
    Generate the PayFast custom-payment MD5 signature.

    IMPORTANT:
    Custom payment signatures use the documented field order.

    Do NOT alphabetically sort these fields.
    """
    parts: list[str] = []

    for key, value in data.items():

        if value is None:
            continue

        value_string = str(value).strip()

        if value_string == "":
            continue

        parts.append(
            f"{key}={_urlencode_payfast_value(value_string)}"
        )

    parameter_string = "&".join(parts)

    if passphrase is not None:
        parameter_string += (
            "&passphrase="
            + _urlencode_payfast_value(passphrase)
        )

    return hashlib.md5(
        parameter_string.encode("utf-8")
    ).hexdigest()


def verify_payfast_signature(
    data: dict,
    received_signature: str,
    passphrase: str | None = None,
) -> bool:
    """
    Verify a PayFast custom-payment/ITN signature.

    The supplied signature is excluded from the calculation.
    """
    if not received_signature:
        return False

    payload = {
        key: value
        for key, value in data.items()
        if key != "signature"
    }

    expected_signature = generate_payfast_signature(
        payload,
        passphrase,
    )

    return hmac.compare_digest(
        expected_signature.lower(),
        str(received_signature).strip().lower(),
    )


# ============================================================
# CHECKOUT DATA
# ============================================================

def build_payfast_checkout_data(
    *,
    return_url: str,
    cancel_url: str,
    notify_url: str,
    name_first: str,
    email_address: str,
    m_payment_id: str,
    amount,
    item_name: str,
    name_last: str = "",
    item_description: str = "",
) -> dict:
    """
    Build a PayFast checkout payload.

    Secrets are retrieved server-side and never need to be
    stored in the database.
    """
    merchant_id = get_payfast_merchant_id()
    merchant_key = get_payfast_merchant_key()
    passphrase = get_payfast_passphrase()

    amount_string = money_to_payfast_amount(amount)

    transaction_reference = str(
        m_payment_id
    ).strip()

    if not transaction_reference:
        raise PayFastValidationError(
            "Payment reference cannot be empty."
        )

    if len(transaction_reference) > 100:
        raise PayFastValidationError(
            "Payment reference is too long."
        )

    data = {
        # Merchant details
        "merchant_id": merchant_id,
        "merchant_key": merchant_key,
        "return_url": return_url,
        "cancel_url": cancel_url,
        "notify_url": notify_url,

        # Buyer details
        "name_first": name_first,
        "name_last": name_last,
        "email_address": email_address,

        # Transaction details
        "m_payment_id": transaction_reference,
        "amount": amount_string,
        "item_name": item_name,
        "item_description": item_description,
    }

    data["signature"] = generate_payfast_signature(
        data,
        passphrase,
    )

    return data


# ============================================================
# ITN SERVER VALIDATION
# ============================================================

def validate_payfast_itn_with_server(
    data: dict,
) -> bool:
    """
    Send the COMPLETE ITN payload back to PayFast.

    PayFast must respond with exactly:

        VALID

    The signature is intentionally included in this request.
    """
    if not isinstance(data, dict):
        raise PayFastITNError(
            "ITN payload must be a dictionary."
        )

    if not data:
        raise PayFastITNError(
            "ITN payload is empty."
        )

    payload = {
        str(key): str(value)
        for key, value in data.items()
        if value is not None
    }

    encoded = urlencode(payload).encode("utf-8")

    request = Request(
        get_payfast_validate_url(),
        data=encoded,
        headers={
            "Content-Type": (
                "application/x-www-form-urlencoded"
            ),
            "User-Agent": (
                "AminationEsports/2.0"
            ),
        },
        method="POST",
    )

    try:
        with urlopen(
            request,
            timeout=10,
        ) as response:

            result = (
                response
                .read()
                .decode("utf-8")
                .strip()
            )

    except Exception as exc:
        raise PayFastITNError(
            "Unable to validate the PayFast ITN with PayFast."
        ) from exc

    return result == "VALID"


# ============================================================
# ITN LOCAL VALIDATION
# ============================================================

def validate_payfast_itn_payload(
    data: dict,
    *,
    expected_m_payment_id: str | None = None,
    expected_amount=None,
) -> bool:
    """
    Validate the important fields contained in a PayFast ITN.

    This function does NOT replace server-side validation.

    It validates:
    - signature
    - merchant ID
    - transaction reference
    - payment status
    - expected amount
    """
    if not isinstance(data, dict):
        raise PayFastITNError(
            "Invalid ITN payload."
        )

    received_signature = data.get(
        "signature"
    )

    if not received_signature:
        raise PayFastITNError(
            "ITN signature is missing."
        )

    if not verify_payfast_signature(
        data,
        received_signature,
        get_payfast_passphrase(),
    ):
        raise PayFastITNError(
            "Invalid PayFast ITN signature."
        )

    # --------------------------------------------------------
    # Merchant identity
    # --------------------------------------------------------

    received_merchant_id = str(
        data.get("merchant_id", "")
    ).strip()

    if received_merchant_id != get_payfast_merchant_id():
        raise PayFastITNError(
            "PayFast merchant ID does not match."
        )

    # --------------------------------------------------------
    # Payment reference
    # --------------------------------------------------------

    m_payment_id = str(
        data.get("m_payment_id", "")
    ).strip()

    if not m_payment_id:
        raise PayFastITNError(
            "PayFast payment reference is missing."
        )

    if expected_m_payment_id is not None:
        if m_payment_id != str(
            expected_m_payment_id
        ).strip():
            raise PayFastITNError(
                "PayFast transaction reference does not match."
            )

    # --------------------------------------------------------
    # Payment status
    # --------------------------------------------------------

    payment_status = str(
        data.get("payment_status", "")
    ).strip().upper()

    if payment_status not in {
        "COMPLETE",
        "CANCELLED",
    }:
        raise PayFastITNError(
            "Unsupported PayFast payment status."
        )

    # --------------------------------------------------------
    # Amount
    # --------------------------------------------------------

    if expected_amount is not None:

        amount_gross = data.get(
            "amount_gross"
        )

        if amount_gross is None:
            raise PayFastITNError(
                "PayFast amount_gross is missing."
            )

        if not validate_payfast_amount(
            expected_amount,
            amount_gross,
        ):
            raise PayFastITNError(
                "PayFast payment amount does not match "
                "the locked transaction amount."
            )

    # --------------------------------------------------------
    # PayFast transaction ID
    # --------------------------------------------------------

    pf_payment_id = str(
        data.get("pf_payment_id", "")
    ).strip()

    if payment_status == "COMPLETE" and not pf_payment_id:
        raise PayFastITNError(
            "Completed PayFast payment has no pf_payment_id."
        )

    return True


# ============================================================
# PAYFAST SOURCE IP VALIDATION
# ============================================================

PAYFAST_HOSTNAMES = (
    "www.payfast.co.za",
    "w1w.payfast.co.za",
    "w2w.payfast.co.za",
    "sandbox.payfast.co.za",
)


def _resolve_payfast_ips() -> set[str]:
    """
    Resolve PayFast's documented hostnames to IP addresses.

    DNS is resolved at validation time rather than hard-coding
    a single address.
    """
    resolved: set[str] = set()

    for hostname in PAYFAST_HOSTNAMES:

        try:
            _, _, addresses = socket.gethostbyname_ex(
                hostname
            )

            resolved.update(addresses)

        except socket.gaierror:
            continue

    return resolved


def is_valid_payfast_source_ip(
    remote_ip: str | None,
) -> bool:
    """
    Check whether the incoming ITN source IP belongs to one of
    PayFast's currently resolved official hostnames.

    This is an additional defense layer.
    """
    if not remote_ip:
        return False

    try:
        source = ipaddress.ip_address(
            remote_ip.strip()
        )
    except ValueError:
        return False

    resolved_ips = _resolve_payfast_ips()

    return str(source) in resolved_ips


# ============================================================
# COMPLETE ITN SECURITY CHECK
# ============================================================

def validate_complete_payfast_itn(
    data: dict,
    *,
    remote_ip: str | None,
    expected_m_payment_id: str,
    expected_amount,
) -> bool:
    """
    Perform the complete AminationEsports PayFast ITN
    security sequence.

    Order:

    1. Source validation
    2. Local signature validation
    3. Merchant/reference/status/amount validation
    4. PayFast server validation

    Nothing should update the payment as verified before
    this function succeeds.
    """
    if not is_valid_payfast_source_ip(
        remote_ip
    ):
        raise PayFastITNError(
            "PayFast ITN originated from an untrusted source IP."
        )

    validate_payfast_itn_payload(
        data,
        expected_m_payment_id=expected_m_payment_id,
        expected_amount=expected_amount,
    )

    if not validate_payfast_itn_with_server(
        data
    ):
        raise PayFastITNError(
            "PayFast rejected the ITN during server validation."
        )

    return True

# ============================================================
# PAYMENT TRANSACTION → PAYFAST CHECKOUT
# ============================================================

def build_checkout_for_transaction(
    transaction,
    *,
    return_url: str,
    cancel_url: str,
    notify_url: str,
    name_first: str,
    name_last: str = "",
    email_address: str,
    item_name: str,
    item_description: str = "",
) -> dict:
    """
    Build a PayFast checkout payload from an existing
    PaymentTransaction.

    The transaction is the source of truth for:
        - internal payment reference
        - payment amount
        - currency/conversion snapshot

    The browser must never be allowed to choose the amount.
    """

    if transaction is None:
        raise PayFastValidationError(
            "Payment transaction is required."
        )

    transaction_reference = str(
        transaction.transaction_reference or ""
    ).strip()

    if not transaction_reference:
        raise PayFastValidationError(
            "Payment transaction has no internal reference."
        )

    if transaction.conversion_locked is not True:
        raise PayFastValidationError(
            "Payment conversion must be locked before checkout."
        )

    if str(transaction.status).strip().lower() not in {
        "initiated",
        "pending",
    }:
        raise PayFastValidationError(
            "Only initiated or pending payments can be sent to PayFast."
        )

    return build_payfast_checkout_data(
        return_url=return_url,
        cancel_url=cancel_url,
        notify_url=notify_url,
        name_first=name_first,
        name_last=name_last,
        email_address=email_address,
        m_payment_id=transaction_reference,
        amount=transaction.payment_amount,
        item_name=item_name,
        item_description=item_description,
    )
