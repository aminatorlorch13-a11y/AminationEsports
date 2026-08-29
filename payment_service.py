"""
AminationEsports Payment Transaction Service

This module connects the secure payment engine to the SQLAlchemy
PaymentTransaction ledger.

Financial principles:
- Decimal is used for all monetary calculations.
- Currency conversion is calculated before persistence.
- The exact FX rate is snapshotted.
- Transaction references are unique.
- Payment creation does NOT mean payment was received.
- Payment verification remains a separate operation.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from secrets import token_hex

from sqlalchemy.exc import IntegrityError

from models import PaymentTransaction
from payment_engine import (
    ConversionSnapshot,
    create_conversion_snapshot,
    normalize_currency,
    validate_non_negative_amount,
)


class PaymentServiceError(Exception):
    """Base payment service exception."""


class TransactionCreationError(PaymentServiceError):
    """Raised when a payment transaction cannot be created."""


class InvalidTransactionStateError(PaymentServiceError):
    """Raised when a transaction state is invalid."""


# ------------------------------------------------------------
# Transaction reference generation
# ------------------------------------------------------------

def generate_transaction_reference() -> str:
    """
    Generate a cryptographically strong internal transaction reference.

    Example:
        AMN-2026-A1B2C3D4E5F6
    """

    year = datetime.utcnow().year
    random_part = token_hex(6).upper()

    return f"AMN-{year}-{random_part}"


# ------------------------------------------------------------
# Conversion snapshot creation
# ------------------------------------------------------------

def build_conversion_snapshot(
    base_amount,
    base_currency: str,
    payment_currency: str,
    exchange_rate,
    rate_source: str,
) -> ConversionSnapshot:
    """
    Create and validate the immutable conversion snapshot.
    """

    return create_conversion_snapshot(
        base_amount=base_amount,
        base_currency=base_currency,
        payment_currency=payment_currency,
        exchange_rate=exchange_rate,
        rate_source=rate_source,
    )


# ------------------------------------------------------------
# Payment transaction creation
# ------------------------------------------------------------

def create_payment_transaction(
    *,
    player_id: int,
    tournament_id: int,
    base_amount,
    base_currency: str,
    payment_currency: str,
    exchange_rate,
    rate_source: str,
    rate_fetched_at: datetime | None = None,
    payment_reference: str | None = None,
    provider: str | None = None,
) -> tuple[PaymentTransaction, ConversionSnapshot]:
    """
    Build a new PaymentTransaction.

    This function intentionally does NOT commit to the database.

    The caller owns the SQLAlchemy transaction and can decide whether
    to commit or roll back.

    Returns:
        (PaymentTransaction, ConversionSnapshot)
    """

    # --------------------------------------------------------
    # Basic identity validation
    # --------------------------------------------------------

    if not isinstance(player_id, int) or player_id <= 0:
        raise TransactionCreationError(
            "player_id must be a positive integer."
        )

    if not isinstance(tournament_id, int) or tournament_id <= 0:
        raise TransactionCreationError(
            "tournament_id must be a positive integer."
        )

    # --------------------------------------------------------
    # Currency normalization
    # --------------------------------------------------------

    base_currency = normalize_currency(base_currency)
    payment_currency = normalize_currency(payment_currency)

    # --------------------------------------------------------
    # Validate base amount
    # --------------------------------------------------------

    base_amount_decimal = validate_non_negative_amount(
        base_amount,
        "base_amount",
    )

    # --------------------------------------------------------
    # Build immutable conversion snapshot
    # --------------------------------------------------------

    snapshot = build_conversion_snapshot(
        base_amount=base_amount_decimal,
        base_currency=base_currency,
        payment_currency=payment_currency,
        exchange_rate=exchange_rate,
        rate_source=rate_source,
    )

    # --------------------------------------------------------
    # Generate unique internal reference
    # --------------------------------------------------------

    transaction_reference = generate_transaction_reference()

    # --------------------------------------------------------
    # Create ledger object
    # --------------------------------------------------------

    transaction = PaymentTransaction(
        transaction_reference=transaction_reference,

        player_id=player_id,
        tournament_id=tournament_id,

        # Legacy/base ledger fields.
        required_amount=float(snapshot.base_amount),
        received_amount=0,
        currency=snapshot.base_currency,

        # International payment foundation.
        base_currency=snapshot.base_currency,
        base_amount=snapshot.base_amount,

        payment_currency=snapshot.payment_currency,
        payment_amount=snapshot.payment_amount,

        exchange_rate=snapshot.exchange_rate,
        rate_source=snapshot.rate_source,
        rate_fetched_at=rate_fetched_at,

        # The conversion becomes immutable once the transaction
        # has been constructed.
        conversion_locked=True,

        # Payment has not actually been received yet.
        status="initiated",

        payment_reference=payment_reference,
        provider=provider,

        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    return transaction, snapshot


# ------------------------------------------------------------
# Safe persistence
# ------------------------------------------------------------

def persist_payment_transaction(db, transaction: PaymentTransaction):
    """
    Add a transaction to the SQLAlchemy session.

    This function does NOT commit.

    The caller should commit the surrounding transaction.
    """

    if transaction is None:
        raise TransactionCreationError(
            "Payment transaction cannot be None."
        )

    if not transaction.transaction_reference:
        raise TransactionCreationError(
            "Transaction reference is required."
        )

    if transaction.status != "initiated":
        raise InvalidTransactionStateError(
            "A newly created transaction must start in 'initiated' state."
        )

    try:
        db.session.add(transaction)

    except IntegrityError as exc:
        db.session.rollback()

        raise TransactionCreationError(
            "Payment transaction could not be added because "
            "a database integrity rule was violated."
        ) from exc


# ------------------------------------------------------------
# State helpers
# ------------------------------------------------------------

VALID_PAYMENT_STATUSES = frozenset(
    {
        "initiated",
        "pending",
        "received",
        "verified",
        "rejected",
        "reversed",
        "refunded",
    }
)


def validate_payment_status(status: str) -> str:
    """
    Validate a payment lifecycle status.
    """

    if not isinstance(status, str):
        raise InvalidTransactionStateError(
            "Payment status must be a string."
        )

    normalized = status.strip().lower()

    if normalized not in VALID_PAYMENT_STATUSES:
        raise InvalidTransactionStateError(
            f"Unsupported payment status: {status!r}"
        )

    return normalized


# ============================================================
# SECURE PAYMENT STATE MACHINE
# ============================================================

PAYMENT_STATE_TRANSITIONS = {
    "initiated": frozenset({
        "pending",
        "received",
        "rejected",
    }),

    "pending": frozenset({
        "received",
        "rejected",
    }),

    "received": frozenset({
        "verified",
        "rejected",
        "reversed",
        "refunded",
    }),

    "verified": frozenset({
        "reversed",
        "refunded",
    }),

    "rejected": frozenset(),

    "reversed": frozenset({
        "refunded",
    }),

    "refunded": frozenset(),
}


def can_transition_payment_status(
    current_status: str,
    new_status: str,
) -> bool:
    """
    Return True only when a payment status transition
    is explicitly allowed by the payment state machine.
    """

    current = validate_payment_status(current_status)
    new = validate_payment_status(new_status)

    if current == new:
        return True

    return new in PAYMENT_STATE_TRANSITIONS.get(
        current,
        frozenset(),
    )


def validate_payment_transition(
    current_status: str,
    new_status: str,
) -> str:
    """
    Validate and return the normalized new payment status.
    """

    current = validate_payment_status(current_status)
    new = validate_payment_status(new_status)

    if not can_transition_payment_status(
        current,
        new,
    ):
        raise InvalidTransactionStateError(
            f"Invalid payment transition: "
            f"{current!r} -> {new!r}"
        )

    return new


# ============================================================
# PAYMENT AMOUNT VALIDATION
# ============================================================

def validate_received_amount(
    received_amount,
    payment_amount,
) -> tuple[Decimal, Decimal]:
    """
    Validate the amount reported as received.

    Returns:
        (received, expected)
    """

    received = validate_non_negative_amount(
        received_amount,
        "received_amount",
    )

    expected = validate_non_negative_amount(
        payment_amount,
        "payment_amount",
    )

    return received, expected


def classify_received_amount(
    received_amount,
    payment_amount,
) -> str:
    """
    Classify a received payment.

    Results:

        unpaid
        underpaid
        exact
        overpaid

    Monetary comparisons use Decimal only.
    """

    received, expected = validate_received_amount(
        received_amount,
        payment_amount,
    )

    if received == Decimal("0"):
        return "unpaid"

    if received < expected:
        return "underpaid"

    if received == expected:
        return "exact"

    return "overpaid"


# ============================================================
# SAFE PAYMENT STATUS TRANSITION
# ============================================================

def transition_payment_status(
    transaction: PaymentTransaction,
    new_status: str,
) -> PaymentTransaction:
    """
    Safely transition an existing payment transaction.

    This function does NOT commit the database transaction.
    """

    if transaction is None:
        raise InvalidTransactionStateError(
            "Payment transaction cannot be None."
        )

    current_status = validate_payment_status(
        transaction.status
    )

    normalized_new_status = validate_payment_transition(
        current_status,
        new_status,
    )

    transaction.status = normalized_new_status
    transaction.updated_at = datetime.utcnow()

    return transaction


# ============================================================
# RECORD PAYMENT RECEIPT
# ============================================================

def record_payment_received(
    transaction: PaymentTransaction,
    received_amount,
) -> PaymentTransaction:
    """
    Record the amount reported as received.

    Important:
    Recording money as received is NOT the same as verification.

    Verification remains a separate operation.
    """

    if transaction is None:
        raise InvalidTransactionStateError(
            "Payment transaction cannot be None."
        )

    received, expected = validate_received_amount(
        received_amount,
        transaction.payment_amount,
    )

    classification = classify_received_amount(
        received,
        expected,
    )

    if classification == "unpaid":
        raise InvalidTransactionStateError(
            "A received payment amount must be greater than zero."
        )

    current_status = validate_payment_status(
        transaction.status
    )

    if current_status not in {
        "initiated",
        "pending",
        "received",
    }:
        raise InvalidTransactionStateError(
            f"Cannot record payment receipt while "
            f"transaction status is {current_status!r}."
        )

    transaction.received_amount = received

    if current_status != "received":
        transition_payment_status(
            transaction,
            "received",
        )
    else:
        transaction.updated_at = datetime.utcnow()

    return transaction


# ============================================================
# PAYMENT VERIFICATION
# ============================================================

def verify_payment_transaction(
    transaction: PaymentTransaction,
) -> PaymentTransaction:
    """
    Verify a received payment.

    Verification is intentionally strict.

    Requirements:
        - transaction exists
        - status must be received
        - received amount must be positive
        - received amount must equal expected payment amount

    Overpayments and underpayments are NOT automatically verified.
    """

    if transaction is None:
        raise InvalidTransactionStateError(
            "Payment transaction cannot be None."
        )

    status = validate_payment_status(
        transaction.status
    )

    if status != "received":
        raise InvalidTransactionStateError(
            "Only a received payment can be verified."
        )

    received = validate_non_negative_amount(
        transaction.received_amount,
        "received_amount",
    )

    expected = validate_non_negative_amount(
        transaction.payment_amount,
        "payment_amount",
    )

    if received <= Decimal("0"):
        raise InvalidTransactionStateError(
            "Cannot verify a zero-value payment."
        )

    if received != expected:
        raise InvalidTransactionStateError(
            "Payment amount does not exactly match "
            "the required converted amount. "
            "Manual review is required."
        )

    transaction.status = "verified"

    transaction.received_at = (
        transaction.received_at
        or datetime.utcnow()
    )

    transaction.verified_at = datetime.utcnow()

    transaction.updated_at = datetime.utcnow()

    return transaction

# ============================================================
# STEP 8R.17 — IDEMPOTENCY + DATABASE SAFETY
# ============================================================

def find_transaction_by_provider_id(db, provider_transaction_id: str):
    """
    Find an existing transaction using the external provider
    transaction ID.

    This is the primary protection against duplicate webhook
    processing.
    """
    if not provider_transaction_id:
        return None

    normalized = str(provider_transaction_id).strip()

    if not normalized:
        return None

    return (
        PaymentTransaction.query
        .filter_by(provider_transaction_id=normalized)
        .first()
    )


def ensure_transaction_not_duplicated(
    db,
    provider_transaction_id: str | None = None,
    transaction_reference: str | None = None,
):
    """
    Reject duplicate payment identities.

    A payment must never be silently duplicated because the same
    provider event or internal transaction reference was processed
    more than once.
    """
    if provider_transaction_id:
        existing = find_transaction_by_provider_id(
            db,
            provider_transaction_id,
        )

        if existing is not None:
            raise TransactionCreationError(
                "A payment transaction with this provider "
                "transaction ID already exists."
            )

    if transaction_reference:
        existing = (
            PaymentTransaction.query
            .filter_by(
                transaction_reference=transaction_reference
            )
            .first()
        )

        if existing is not None:
            raise TransactionCreationError(
                "A payment transaction with this transaction "
                "reference already exists."
            )


def flush_payment_transaction(db, transaction):
    """
    Safely flush a payment transaction without committing.

    SQLAlchemy's flush causes database constraints to be checked
    while leaving the surrounding transaction under caller control.
    """
    if transaction is None:
        raise TransactionCreationError(
            "Payment transaction cannot be None."
        )

    if not transaction.transaction_reference:
        raise TransactionCreationError(
            "Transaction reference is required."
        )

    try:
        db.session.add(transaction)
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()

        raise TransactionCreationError(
            "Payment transaction failed database integrity "
            "validation."
        ) from exc

    return transaction


def get_payment_transaction(db, transaction_id: int):
    """
    Retrieve a payment transaction by primary key.
    """
    if not isinstance(transaction_id, int) or transaction_id <= 0:
        raise TransactionCreationError(
            "transaction_id must be a positive integer."
        )

    transaction = db.session.get(
        PaymentTransaction,
        transaction_id,
    )

    if transaction is None:
        raise TransactionCreationError(
            "Payment transaction was not found."
        )

    return transaction


def safe_commit_payment_transaction(db):
    """
    Commit the caller's payment transaction.

    A failed commit is rolled back so the SQLAlchemy session is
    not left in a broken state.
    """
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()

        raise TransactionCreationError(
            "Payment transaction could not be committed because "
            "a database integrity rule was violated."
        ) from exc

    return True
