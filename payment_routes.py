"""
AminationEsports PayFast payment routes.

Flow:

registration
    -> payment transaction
    -> PayFast checkout
    -> PayFast ITN
    -> security validation
    -> received
    -> verified
    -> TournamentParticipant activated

The browser return URL NEVER verifies payment.
Only a valid PayFast ITN can verify the transaction.
"""

from datetime import datetime
from decimal import Decimal

from flask import request, redirect, url_for

from models import (
    db,
    Player,
    Tournament,
    TournamentParticipant,
    PaymentTransaction,
)

from payment_service import (
    create_payment_transaction,
    transition_payment_status,
    record_payment_received,
    verify_payment_transaction,
)

from payfast_service import (
    PayFastConfigurationError,
    PayFastITNError,
    PayFastValidationError,
    build_checkout_for_transaction,
    get_public_base_url,
    validate_complete_payfast_itn,
)


def register_payment_routes(app):

    def current_tournament():
        return (
            Tournament.query
            .order_by(Tournament.id.desc())
            .first()
        )

    def render_payment_message(title, message, status=200):
        safe_title = str(title)
        safe_message = str(message)

        return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">
    <title>{safe_title} | Amination Esports</title>
    <style>
        body {{
            margin: 0;
            padding: 40px 20px;
            background: #090909;
            color: #fff;
            font-family: Arial, sans-serif;
        }}
        .box {{
            max-width: 650px;
            margin: 60px auto;
            padding: 32px;
            border: 1px solid #333;
            border-radius: 16px;
            background: #111;
            text-align: center;
        }}
        h1 {{
            margin-top: 0;
        }}
        p {{
            line-height: 1.6;
            color: #ccc;
        }}
        a {{
            display: inline-block;
            margin-top: 20px;
            padding: 13px 22px;
            border-radius: 8px;
            background: #d4af37;
            color: #000;
            text-decoration: none;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <div class="box">
        <h1>{safe_title}</h1>
        <p>{safe_message}</p>
        <a href="/">RETURN TO AMINATION ESPORTS</a>
    </div>
</body>
</html>
""", status


    @app.route(
        "/payment/start/<int:player_id>",
        methods=["GET"],
    )
    def start_payment(player_id):

        player = Player.query.get_or_404(player_id)
        tournament = current_tournament()

        if not tournament:
            return render_payment_message(
                "Payment unavailable",
                "There is currently no tournament configured.",
                404,
            )

        if not tournament.payment_enabled:
            return render_payment_message(
                "No payment required",
                "This tournament does not currently require an entry payment.",
                400,
            )

        if Decimal(str(tournament.entry_fee or 0)) <= Decimal("0"):
            return render_payment_message(
                "No payment required",
                "The current tournament entry fee is zero.",
                400,
            )

        if tournament.status != "registration":
            return render_payment_message(
                "Registration closed",
                "Payments for this tournament are no longer being accepted.",
                409,
            )

        # PayFast's normal transaction amount is ZAR.
        # International customers can still be handled by PayFast's
        # own multi-currency checkout where enabled.
        if str(tournament.currency or "ZAR").strip().upper() != "ZAR":
            return render_payment_message(
                "Payment configuration error",
                "PayFast checkout currently requires the tournament's "
                "canonical payment currency to be ZAR.",
                409,
            )

        # Reuse an unfinished/verified transaction instead of creating
        # duplicates when the player refreshes or returns to this page.
        existing = (
            PaymentTransaction.query
            .filter_by(
                player_id=player.id,
                tournament_id=tournament.id,
            )
            .order_by(PaymentTransaction.id.desc())
            .first()
        )

        if existing:
            if existing.status == "verified":
                return redirect(
                    url_for(
                        "payment_return",
                        transaction_reference=existing.transaction_reference,
                    )
                )

            if existing.status in {"initiated", "pending"}:
                transaction = existing
            else:
                transaction = None
        else:
            transaction = None

        try:
            if transaction is None:
                transaction, snapshot = create_payment_transaction(
                    player_id=player.id,
                    tournament_id=tournament.id,
                    base_amount=tournament.entry_fee,
                    base_currency="ZAR",
                    payment_currency="ZAR",
                    exchange_rate=Decimal("1"),
                    rate_source="payfast_zar",
                    rate_fetched_at=datetime.utcnow(),
                    payment_reference=None,
                    provider="payfast",
                )

                db.session.add(transaction)
                db.session.flush()

            if transaction.status == "initiated":
                transition_payment_status(
                    transaction,
                    "pending",
                )

            transaction.provider = "payfast"

            db.session.commit()

        except Exception:
            db.session.rollback()
            raise

        public_base_url = get_public_base_url()

        checkout = build_checkout_for_transaction(
            transaction,
            return_url=(
                f"{public_base_url}/payment/return/"
                f"{transaction.transaction_reference}"
            ),
            cancel_url=(
                f"{public_base_url}/payment/cancel/"
                f"{transaction.transaction_reference}"
            ),
            notify_url=(
                f"{public_base_url}/payment/itn"
            ),
            name_first=(
                player.name.strip().split(" ", 1)[0]
                if player.name.strip()
                else "Player"
            ),
            name_last=(
                player.name.strip().split(" ", 1)[1]
                if " " in player.name.strip()
                else ""
            ),
            email_address=player.email,
            item_name=tournament.name,
            item_description=(
                f"Amination Esports tournament entry — "
                f"{tournament.name}"
            ),
        )

        process_url = (
            "https://sandbox.payfast.co.za/eng/process"
            if __import__("payfast_service").payfast_sandbox_enabled()
            else "https://www.payfast.co.za/eng/process"
        )

        hidden_fields = []

        for key, value in checkout.items():
            escaped_key = (
                str(key)
                .replace("&", "&amp;")
                .replace('"', "&quot;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

            escaped_value = (
                str(value)
                .replace("&", "&amp;")
                .replace('"', "&quot;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

            hidden_fields.append(
                f'<input type="hidden" name="{escaped_key}" '
                f'value="{escaped_value}">'
            )

        html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">
    <title>Secure PayFast Checkout | Amination Esports</title>
</head>
<body style="
    margin:0;
    min-height:100vh;
    display:flex;
    align-items:center;
    justify-content:center;
    background:#090909;
    color:#fff;
    font-family:Arial,sans-serif;
">
    <div style="
        width:min(92%,520px);
        padding:35px;
        background:#111;
        border:1px solid #333;
        border-radius:18px;
        text-align:center;
    ">
        <h1>SECURE PAYMENT</h1>

        <p>
            Redirecting you to PayFast for secure payment.
        </p>

        <p>
            <strong>{tournament.name}</strong>
        </p>

        <p>
            Amount:
            <strong>
                R{transaction.payment_amount}
            </strong>
        </p>

        <form id="payfast-form"
              method="POST"
              action="{process_url}">
            {"".join(hidden_fields)}

            <noscript>
                <button type="submit">
                    CONTINUE TO PAYFAST
                </button>
            </noscript>
        </form>
    </div>

    <script>
        document.getElementById("payfast-form").submit();
    </script>
</body>
</html>
"""

        return html


    @app.route(
        "/payment/return/<transaction_reference>",
        methods=["GET"],
    )
    def payment_return(transaction_reference):

        transaction = (
            PaymentTransaction.query
            .filter_by(
                transaction_reference=transaction_reference
            )
            .first_or_404()
        )

        if transaction.status == "verified":
            return render_payment_message(
                "PAYMENT VERIFIED",
                "Your payment has been verified successfully. "
                "Your Amination Esports tournament participation "
                "has been recorded.",
                200,
            )

        return render_payment_message(
            "PAYMENT PROCESSING",
            "PayFast has returned you to Amination Esports. "
            "Your browser return does not prove payment. "
            "The secure PayFast notification must still be validated "
            "by the server. Refresh later to see the verified status.",
            200,
        )


    @app.route(
        "/payment/cancel/<transaction_reference>",
        methods=["GET"],
    )
    def payment_cancel(transaction_reference):

        transaction = (
            PaymentTransaction.query
            .filter_by(
                transaction_reference=transaction_reference
            )
            .first_or_404()
        )

        return render_payment_message(
            "PAYMENT CANCELLED",
            "The PayFast payment was cancelled or was not completed. "
            "No tournament participation has been granted.",
            200,
        )


    @app.route(
        "/payment/itn",
        methods=["POST"],
    )
    def payment_itn():

        data = request.form.to_dict(flat=True)

        transaction_reference = str(
            data.get("m_payment_id", "")
        ).strip()

        if not transaction_reference:
            return "INVALID", 400

        transaction = (
            PaymentTransaction.query
            .filter_by(
                transaction_reference=transaction_reference
            )
            .first()
        )

        if transaction is None:
            return "INVALID", 404

        provider_payment_id = str(
            data.get("pf_payment_id", "")
        ).strip()

        # Idempotent duplicate ITN.
        if (
            transaction.status == "verified"
            and transaction.provider_transaction_id
            == provider_payment_id
        ):
            return "OK", 200

        if provider_payment_id:
            other_transaction = (
                PaymentTransaction.query
                .filter(
                    PaymentTransaction.provider_transaction_id
                    == provider_payment_id,
                    PaymentTransaction.id
                    != transaction.id,
                )
                .first()
            )

            if other_transaction is not None:
                return "INVALID", 409

        try:
            validate_complete_payfast_itn(
                data,
                remote_ip=request.remote_addr,
                expected_m_payment_id=(
                    transaction.transaction_reference
                ),
                expected_amount=transaction.payment_amount,
            )

            payment_status = str(
                data.get("payment_status", "")
            ).strip().upper()

            if payment_status == "CANCELLED":
                if transaction.status not in {
                    "verified",
                    "reversed",
                    "refunded",
                    "rejected",
                }:
                    transition_payment_status(
                        transaction,
                        "rejected",
                    )

                db.session.commit()
                return "OK", 200

            if payment_status != "COMPLETE":
                return "INVALID", 400

            amount_gross = data.get("amount_gross")

            record_payment_received(
                transaction,
                amount_gross,
            )

            verify_payment_transaction(
                transaction
            )

            transaction.provider = "payfast"
            transaction.provider_transaction_id = (
                provider_payment_id
            )
            transaction.payment_reference = (
                transaction.transaction_reference
            )

            participant = (
                TournamentParticipant.query
                .filter_by(
                    tournament_id=transaction.tournament_id,
                    player_id=transaction.player_id,
                )
                .first()
            )

            if participant is None:
                participant = TournamentParticipant(
                    tournament_id=transaction.tournament_id,
                    player_id=transaction.player_id,
                    status="registered",
                )
                db.session.add(participant)

            participant.payment_status = "verified"
            participant.payment_required_amount = float(
                transaction.payment_amount
            )
            participant.payment_received_amount = float(
                amount_gross
            )
            participant.payment_reference = (
                transaction.transaction_reference
            )
            participant.payment_transaction_id = (
                provider_payment_id
            )
            participant.payment_provider = "payfast"
            participant.payment_received_at = (
                transaction.received_at
            )
            participant.payment_verified_at = (
                transaction.verified_at
            )
            participant.founder_payment_verified = False

            tournament = (
                Tournament.query
                .filter_by(
                    id=transaction.tournament_id
                )
                .first()
            )

            player = (
                Player.query
                .filter_by(
                    id=transaction.player_id
                )
                .first()
            )

            if tournament is None or player is None:
                raise PayFastITNError(
                    "Payment references a missing tournament or player."
                )

            # Capacity is enforced per tournament.
            approved_count = (
                TournamentParticipant.query
                .filter_by(
                    tournament_id=transaction.tournament_id,
                    status="approved",
                )
                .count()
            )


            if (
                player.application_status == "approved"
                or approved_count < tournament.max_players
            ):
                player.application_status = "approved"
                player.active = True
                participant.status = "approved"
            else:
                player.application_status = "waitlist"
                player.active = True
                participant.status = "waitlist"

            db.session.commit()

            return "OK", 200

        except (
            PayFastITNError,
            PayFastValidationError,
        ):
            db.session.rollback()
            return "INVALID", 400

        except Exception:
            db.session.rollback()
            return "INVALID", 500
