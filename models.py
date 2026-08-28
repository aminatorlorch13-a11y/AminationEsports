from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Numeric
from datetime import datetime


from constants import (
    DEFAULT_MAX_PLAYERS,
    TOURNAMENT_REGISTRATION,
    MATCH_SCHEDULED,
)


db = SQLAlchemy()


# ============================================================
# PLAYER
# ============================================================

class Player(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(100),
        nullable=False
    )

    fc_username = db.Column(
        db.String(100),
        nullable=False,
        unique=True
    )

    # Player account authentication
    email = db.Column(
        db.String(255),
        nullable=True,
        unique=True
    )

    password_hash = db.Column(
        db.String(255),
        nullable=True
    )

    # Password recovery
    reset_token = db.Column(
        db.String(255),
        nullable=True,
        unique=True
    )

    reset_token_expires = db.Column(
        db.DateTime,
        nullable=True
    )

    # International-ready player information
    country = db.Column(
        db.String(100),
        default="South Africa"
    )

    squad_ovr = db.Column(
        db.Integer,
        nullable=True
    )

    # Player's FC Mobile team/squad name
    team_name = db.Column(
        db.String(150),
        nullable=True
    )

    registered_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    active = db.Column(
        db.Boolean,
        default=True
    )

    # Global account/application lifecycle
    #
    # pending
    # approved
    # waitlist
    # withdrawn
    # removed
    # suspended
    # eliminated
    # champion
    # runner_up

    application_status = db.Column(
        db.String(30),
        default="pending",
        nullable=False
    )

    terms_accepted = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    terms_version = db.Column(
        db.String(20),
        default="1.0"
    )

    terms_accepted_at = db.Column(
        db.DateTime,
        nullable=True
    )

    # Championship stars
    championship_stars = db.Column(
        db.Integer,
        default=0,
        nullable=False
    )



# ============================================================
# TOURNAMENT
# ============================================================

class Tournament(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(150),
        nullable=False
    )

    status = db.Column(
        db.String(30),
        default=TOURNAMENT_REGISTRATION
    )

    max_players = db.Column(
        db.Integer,
        default=DEFAULT_MAX_PLAYERS
    )

    # Entry fee
    entry_fee = db.Column(
        db.Float,
        default=0
    )

    # Payment system master switch
    #
    # False = free tournament
    # True = payment required
    payment_enabled = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    # Currency used for the tournament
    currency = db.Column(
        db.String(10),
        default="ZAR",
        nullable=False
    )

    # International participation switch
    international_enabled = db.Column(
        db.Boolean,
        default=True,
        nullable=False
    )

    competition_day = db.Column(
        db.String(20),
        default="Saturday"
    )

    final_day = db.Column(
        db.String(20),
        default="Sunday"
    )

    # Season number for historical records
    season_number = db.Column(
        db.Integer,
        nullable=True
    )

    # Official tournament WhatsApp group
    whatsapp_group_link = db.Column(
        db.String(500),
        nullable=True
    )

    # Payment instructions shown to players
    payment_instructions = db.Column(
        db.Text,
        nullable=True
    )

    # Payment deadline
    payment_deadline = db.Column(
        db.DateTime,
        nullable=True
    )

    # Availability confirmation deadline
    availability_deadline = db.Column(
        db.DateTime,
        nullable=True
    )

    # Tournament creation
    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    # Tournament completion
    completed_at = db.Column(
        db.DateTime,
        nullable=True
    )

    # Automatically recorded champion
    champion_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=True
    )

    # Automatically recorded runner-up
    runner_up_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=True
    )


# ============================================================
# TOURNAMENT PARTICIPATION
# ============================================================

class TournamentParticipant(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    tournament_id = db.Column(
        db.Integer,
        db.ForeignKey("tournament.id"),
        nullable=False
    )

    player_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=False
    )

    # Tournament-specific team/squad name
    team_name = db.Column(
        db.String(150),
        nullable=True
    )

    # Participation status
    #
    # registered
    # approved
    # waitlist
    # active
    # unavailable
    # replaced
    # eliminated
    # champion
    # runner_up
    # withdrawn
    # suspended
    # forfeited

    status = db.Column(
        db.String(30),
        default="registered",
        nullable=False
    )

    # Availability
    #
    # unknown
    # available
    # unavailable
    # confirmed
    # no_response

    availability_status = db.Column(
        db.String(30),
        default="unknown",
        nullable=False
    )

    availability_confirmed_at = db.Column(
        db.DateTime,
        nullable=True
    )

    # WhatsApp
    whatsapp_joined = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    whatsapp_joined_at = db.Column(
        db.DateTime,
        nullable=True
    )

    # Payment status
    #
    # not_required
    # unpaid
    # pending
    # received
    # verified
    # overpayment
    # reversed
    # disputed
    # refund_pending
    # refunded
    # rejected

    payment_status = db.Column(
        db.String(30),
        default="not_required",
        nullable=False
    )

    payment_required_amount = db.Column(
        db.Float,
        default=0
    )

    payment_received_amount = db.Column(
        db.Float,
        default=0
    )

    payment_reference = db.Column(
        db.String(150),
        nullable=True
    )

    payment_transaction_id = db.Column(
        db.String(255),
        nullable=True
    )

    payment_provider = db.Column(
        db.String(100),
        nullable=True
    )

    payment_received_at = db.Column(
        db.DateTime,
        nullable=True
    )

    payment_verified_at = db.Column(
        db.DateTime,
        nullable=True
    )

    founder_payment_verified = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    founder_payment_verified_at = db.Column(
        db.DateTime,
        nullable=True
    )

    # Overpayment
    overpayment_amount = db.Column(
        db.Float,
        default=0
    )

    overpayment_reviewed = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    # Reversal / chargeback
    payment_reversed = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    payment_reversed_at = db.Column(
        db.DateTime,
        nullable=True
    )

    payment_reversal_reason = db.Column(
        db.Text,
        nullable=True
    )

    # Refund
    refund_requested = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    refund_requested_at = db.Column(
        db.DateTime,
        nullable=True
    )

    refund_approved = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    refund_approved_at = db.Column(
        db.DateTime,
        nullable=True
    )

    refund_amount = db.Column(
        db.Float,
        default=0
    )

    refund_completed = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    refund_completed_at = db.Column(
        db.DateTime,
        nullable=True
    )

    refund_reason = db.Column(
        db.Text,
        nullable=True
    )

    # Registration timestamp
    registered_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


# ============================================================
# MATCH
# ============================================================

class Match(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    tournament_id = db.Column(
        db.Integer,
        db.ForeignKey("tournament.id"),
        nullable=False
    )

    # Players are nullable in V2.
    #
    # This allows:
    #
    # TBD vs TBD
    # Player vs TBD
    # BYE slots
    #
    # Existing V1 matches remain valid because existing
    # player IDs are preserved.

    player1_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=True
    )

    player2_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=True
    )

    # Basic score
    player1_score = db.Column(
        db.Integer,
        default=0
    )

    player2_score = db.Column(
        db.Integer,
        default=0
    )

    status = db.Column(
        db.String(30),
        default=MATCH_SCHEDULED
    )

    # Round name
    #
    # Round 1
    # Quarter-finals
    # Semi-finals
    # Final

    round_name = db.Column(
        db.String(50),
        nullable=True
    )

    # V2 bracket numbering
    round_number = db.Column(
        db.Integer,
        nullable=True
    )

    match_number = db.Column(
        db.Integer,
        nullable=True
    )

    # Position within the bracket
    bracket_position = db.Column(
        db.Integer,
        nullable=True
    )

    # Feeder relationships
    #
    # This is the major V2 bracket improvement.
    #
    # Example:
    #
    # Match 1 ──┐
    #           ├── Match 5
    # Match 2 ──┘

    source_match1_id = db.Column(
        db.Integer,
        db.ForeignKey("match.id"),
        nullable=True
    )

    source_match2_id = db.Column(
        db.Integer,
        db.ForeignKey("match.id"),
        nullable=True
    )

    # BYE support
    is_bye = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    bye_reason = db.Column(
        db.String(255),
        nullable=True
    )

    # Forfeit support
    is_forfeit = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )

    forfeit_player_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=True
    )

    forfeit_reason = db.Column(
        db.Text,
        nullable=True
    )

    # Winner
    winner_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=True
    )

    loser_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=True
    )

    # Scheduling
    scheduled_time = db.Column(
        db.DateTime,
        nullable=True
    )

    started_at = db.Column(
        db.DateTime,
        nullable=True
    )

    finished_at = db.Column(
        db.DateTime,
        nullable=True
    )

    # Live match
    is_live = db.Column(
        db.Boolean,
        default=False
    )

    live_minute = db.Column(
        db.Integer,
        default=0,
        nullable=False
    )

    live_period = db.Column(
        db.String(30),
        nullable=True
    )

    # Public live status message
    live_message = db.Column(
        db.String(255),
        nullable=True
    )


# ============================================================
# MATCH EVENTS
# ============================================================

class MatchEvent(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    match_id = db.Column(
        db.Integer,
        db.ForeignKey("match.id"),
        nullable=False
    )

    # goal
    # assist
    # yellow_card
    # red_card
    # substitution
    # halftime
    # second_half
    # fulltime
    # correction
    # announcement

    event_type = db.Column(
        db.String(50),
        nullable=False
    )

    player_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=True
    )

    # Minute of event
    minute = db.Column(
        db.Integer,
        nullable=True
    )

    # Score after the event
    player1_score = db.Column(
        db.Integer,
        nullable=True
    )

    player2_score = db.Column(
        db.Integer,
        nullable=True
    )

    # Public description
    description = db.Column(
        db.String(500),
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    # Founder/admin who created the event
    created_by = db.Column(
        db.String(100),
        nullable=True
    )

    # Allows event corrections without deleting history
    corrected = db.Column(
        db.Boolean,
        default=False,
        nullable=False
    )


# ============================================================
# PLAYER STATISTICS
# ============================================================

class PlayerStatistic(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    player_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=False
    )

    matches_played = db.Column(
        db.Integer,
        default=0
    )

    wins = db.Column(
        db.Integer,
        default=0
    )

    draws = db.Column(
        db.Integer,
        default=0
    )

    losses = db.Column(
        db.Integer,
        default=0
    )

    goals = db.Column(
        db.Integer,
        default=0
    )

    assists = db.Column(
        db.Integer,
        default=0
    )

    clean_sheets = db.Column(
        db.Integer,
        default=0
    )

    saves = db.Column(
        db.Integer,
        default=0
    )

    critical_saves = db.Column(
        db.Integer,
        default=0
    )


# ============================================================
# RECORDS
# ============================================================

class Record(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    record_type = db.Column(
        db.String(100),
        nullable=False
    )

    record_value = db.Column(
        db.Float,
        nullable=False
    )

    player_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=False
    )

    tournament_id = db.Column(
        db.Integer,
        db.ForeignKey("tournament.id"),
        nullable=True
    )

    achieved_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    is_current = db.Column(
        db.Boolean,
        default=True
    )


# ============================================================
# HALL OF CHAMPIONS
# ============================================================

class HallOfChampion(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # The tournament that produced this champion
    tournament_id = db.Column(
        db.Integer,
        db.ForeignKey("tournament.id"),
        nullable=False,
        unique=True
    )

    # Winning player
    player_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=False
    )

    # Team/squad represented by the player in that tournament
    team_name = db.Column(
        db.String(150),
        nullable=True
    )

    # Season
    season_number = db.Column(
        db.Integer,
        nullable=True
    )

    tournament_name = db.Column(
        db.String(150),
        nullable=False
    )

    # Final score
    final_score = db.Column(
        db.String(50),
        nullable=True
    )

    # Performance summary
    matches_played = db.Column(
        db.Integer,
        nullable=True
    )

    wins = db.Column(
        db.Integer,
        nullable=True
    )

    goals_scored = db.Column(
        db.Integer,
        nullable=True
    )

    goals_conceded = db.Column(
        db.Integer,
        nullable=True
    )

    champion_announced_at = db.Column(
        db.DateTime,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


# ============================================================
# PAYMENT TRANSACTION LEDGER
# ============================================================

class PaymentTransaction(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # Internal unique transaction reference.
    transaction_reference = db.Column(
        db.String(100),
        nullable=False,
        unique=True,
        index=True
    )

    # Player making the payment.
    player_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=False,
        index=True
    )

    # Tournament the payment belongs to.
    tournament_id = db.Column(
        db.Integer,
        db.ForeignKey("tournament.id"),
        nullable=False,
        index=True
    )

    # Amount expected at the time of payment.
    required_amount = db.Column(
        db.Float,
        nullable=False,
        default=0
    )

    # Amount actually reported as received.
    received_amount = db.Column(
        db.Float,
        nullable=False,
        default=0
    )

    # Currency used for the transaction.
    currency = db.Column(
        db.String(10),
        nullable=False,
        default="ZAR"
    )

    # ========================================================
    # INTERNATIONAL MULTI-CURRENCY PAYMENT FOUNDATION
    # ========================================================

    # Canonical tournament price currency.
    # AminationEsports stores the tournament price in this
    # currency regardless of the player's local currency.
    base_currency = db.Column(
        db.String(3),
        nullable=False,
        default="ZAR"
    )

    # Canonical tournament price at transaction creation.
    # Numeric is used instead of Float for financial precision.
    base_amount = db.Column(
        Numeric(18, 2),
        nullable=False,
        default=0
    )

    # Currency in which the player is actually paying.
    payment_currency = db.Column(
        db.String(3),
        nullable=False,
        default="ZAR"
    )

    # Exact amount presented/charged in the player's currency.
    payment_amount = db.Column(
        Numeric(18, 2),
        nullable=False,
        default=0
    )

    # Exchange rate used for THIS transaction.
    # Example:
    # 1 ZAR = 0.054321 USD
    exchange_rate = db.Column(
        Numeric(20, 10),
        nullable=True
    )

    # Where the exchange rate came from.
    # Examples:
    # "provider"
    # "fx_service"
    # "manual_founder_rate"
    rate_source = db.Column(
        db.String(100),
        nullable=True
    )

    # Exact moment the exchange rate was obtained.
    rate_fetched_at = db.Column(
        db.DateTime,
        nullable=True
    )

    # Once true, the conversion used by the transaction
    # must never silently change.
    conversion_locked = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )

    # Payment lifecycle:
    # initiated
    # pending
    # received
    # verified
    # rejected
    # reversed
    # refunded
    status = db.Column(
        db.String(30),
        nullable=False,
        default="initiated",
        index=True
    )

    # Human-readable payment reference.
    payment_reference = db.Column(
        db.String(150),
        nullable=True,
        index=True
    )

    # External provider transaction ID.
    # NULL until an actual payment provider is connected.
    provider_transaction_id = db.Column(
        db.String(255),
        nullable=True,
        unique=True
    )

    # Provider name, e.g. a future payment gateway.
    provider = db.Column(
        db.String(100),
        nullable=True
    )

    # When payment was reported received.
    received_at = db.Column(
        db.DateTime,
        nullable=True
    )

    # When the founder/system verified it.
    verified_at = db.Column(
        db.DateTime,
        nullable=True
    )

    # Founder who verified the payment.
    verified_by = db.Column(
        db.String(255),
        nullable=True
    )

    # Reversal information.
    reversed_at = db.Column(
        db.DateTime,
        nullable=True
    )

    reversal_reason = db.Column(
        db.Text,
        nullable=True
    )

    # Refund information.
    refund_amount = db.Column(
        db.Float,
        nullable=False,
        default=0
    )

    refund_requested_at = db.Column(
        db.DateTime,
        nullable=True
    )

    refund_approved_at = db.Column(
        db.DateTime,
        nullable=True
    )

    refund_completed_at = db.Column(
        db.DateTime,
        nullable=True
    )

    refund_reason = db.Column(
        db.Text,
        nullable=True
    )

    # Creation timestamp.
    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    # Last modification timestamp.
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )


# ============================================================
# FOUNDER ACTION HISTORY
# ============================================================

class AdminAction(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    player_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=True
    )

    action = db.Column(
        db.String(100),
        nullable=False
    )

    old_status = db.Column(
        db.String(30),
        nullable=True
    )

    new_status = db.Column(
        db.String(30),
        nullable=True
    )

    notes = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


# ============================================================
# PLAYER → FOUNDER MESSAGES
# ============================================================

class FounderMessage(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    player_id = db.Column(
        db.Integer,
        db.ForeignKey("player.id"),
        nullable=False
    )

    subject = db.Column(
        db.String(150),
        nullable=False
    )

    message = db.Column(
        db.Text,
        nullable=False
    )

    status = db.Column(
        db.String(30),
        default="unread"
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    founder_reply = db.Column(
        db.Text,
        nullable=True
    )

    replied_at = db.Column(
        db.DateTime,
        nullable=True
    )
