from flask import Flask, render_template, request, redirect, url_for, session, flash
from datetime import datetime, timedelta
import random
import secrets
from werkzeug.security import generate_password_hash, check_password_hash

from sqlalchemy import or_

from config import Config
from constants import (
    SUPPORTED_PLAYER_COUNTS,
    DEFAULT_MAX_PLAYERS,
    TOURNAMENT_REGISTRATION,
    TOURNAMENT_DRAW_RELEASED,
    TOURNAMENT_IN_PROGRESS,
    TOURNAMENT_PAUSED,
    TOURNAMENT_COMPLETED,
    MATCH_SCHEDULED,
    MATCH_IN_PROGRESS,
    MATCH_LIVE,
    MATCH_FINISHED,
    ROUND_1,
    QUARTER_FINAL,
    SEMI_FINAL,
    FINAL,
)

from models import (
    db,
    Player,
    Match,
    Tournament,
    TournamentParticipant,
    PaymentTransaction,
    Record,
    AdminAction,
    FounderMessage
)

from payment_service import (
    create_payment_transaction,
    persist_payment_transaction,
    record_payment_received,
    verify_payment_transaction,
    find_transaction_by_provider_id,
    ensure_transaction_not_duplicated,
    transition_payment_status,
)

from payfast_service import (
    build_checkout_for_transaction,
    validate_complete_payfast_itn,
)



app = Flask(__name__)

app.config.from_object(Config)

db.init_app(app)

# Secure PayFast payment routes.
from payment_routes import register_payment_routes
register_payment_routes(app)


# ============================================================
# FOUNDER AUTHENTICATION
# ============================================================

def founder_required():
    """
    Protect founder/admin routes.

    Returns a redirect response when the founder is not
    authenticated. Returns None when authenticated.
    """

    if not session.get("founder_authenticated"):
        return redirect(url_for("admin_login"))

    return None





# ============================================================
# TOURNAMENT HELPERS — V2 BRACKET ENGINE
# ============================================================

def tournament_bracket_capacity(player_count):
    try:
        player_count = int(player_count or 0)
    except (TypeError, ValueError):
        player_count = 0

    if player_count <= 2:
        return 2
    if player_count <= 4:
        return 4
    if player_count <= 8:
        return 8
    if player_count <= 16:
        return 16
    if player_count <= 32:
        return 32

    raise ValueError(
        "AminationEsports supports a maximum of 32 players."
    )


def tournament_rounds(player_count):
    capacity = tournament_bracket_capacity(player_count)

    if capacity == 2:
        return [FINAL]

    if capacity == 4:
        return [
            SEMI_FINAL,
            FINAL
        ]

    if capacity == 8:
        return [
            QUARTER_FINAL,
            SEMI_FINAL,
            FINAL
        ]

    if capacity == 16:
        return [
            ROUND_1,
            QUARTER_FINAL,
            SEMI_FINAL,
            FINAL
        ]

    return [
        ROUND_1,
        "Round 2",
        QUARTER_FINAL,
        SEMI_FINAL,
        FINAL
    ]


def next_round_name(current_round, rounds):
    if not current_round or not rounds:
        return None

    if current_round not in rounds:
        return None

    index = rounds.index(current_round)

    if index >= len(rounds) - 1:
        return None

    return rounds[index + 1]


def calculate_bye_count(player_count):
    capacity = tournament_bracket_capacity(player_count)

    try:
        player_count = int(player_count or 0)
    except (TypeError, ValueError):
        player_count = 0

    return max(capacity - player_count, 0)


def calculate_bye_positions(player_count):
    capacity = tournament_bracket_capacity(player_count)
    bye_count = calculate_bye_count(player_count)

    if bye_count <= 0:
        return []

    positions = []

    for index in range(bye_count):
        position = int(
            round(
                ((index + 0.5) * capacity / bye_count) - 0.5
            )
        )

        position = max(
            0,
            min(position, capacity - 1)
        )

        if position not in positions:
            positions.append(position)

    if len(positions) < bye_count:
        for position in range(capacity):
            if position not in positions:
                positions.append(position)

            if len(positions) >= bye_count:
                break

    return sorted(positions)


def build_round_one_slots(players):
    players = list(players or [])
    player_count = len(players)

    if player_count < 2:
        return []

    capacity = tournament_bracket_capacity(player_count)

    bye_positions = set(
        calculate_bye_positions(player_count)
    )

    slots = []
    player_index = 0

    for position in range(capacity):

        if position in bye_positions:
            slots.append(
                {
                    "position": position + 1,
                    "player": None,
                    "is_bye": True
                }
            )
        else:
            slots.append(
                {
                    "position": position + 1,
                    "player": players[player_index],
                    "is_bye": False
                }
            )

            player_index += 1

    return slots


def build_round_one_pairings(players):
    slots = build_round_one_slots(players)
    pairings = []

    for index in range(0, len(slots), 2):

        player1_slot = slots[index]
        player2_slot = slots[index + 1]

        pairings.append(
            {
                "match_number": index // 2 + 1,
                "bracket_position": index // 2 + 1,

                "player1": player1_slot["player"],
                "player2": player2_slot["player"],

                "player1_is_bye":
                    player1_slot["is_bye"],

                "player2_is_bye":
                    player2_slot["is_bye"],

                "is_bye":
                    (
                        player1_slot["is_bye"]
                        or
                        player2_slot["is_bye"]
                    )
            }
        )

    return pairings


def get_player_match(player_id):
    if not player_id:
        return None

    active_match = Match.query.filter(
        or_(
            Match.player1_id == player_id,
            Match.player2_id == player_id
        ),
        Match.status.in_(
            [
                MATCH_SCHEDULED,
                MATCH_IN_PROGRESS,
                MATCH_LIVE
            ]
        )
    ).order_by(
        Match.id.desc()
    ).first()

    if active_match:
        return active_match

    return Match.query.filter(
        or_(
            Match.player1_id == player_id,
            Match.player2_id == player_id
        )
    ).order_by(
        Match.id.desc()
    ).first()


def _set_next_match_player(next_match, winner_id, source_match):
    if not next_match or not winner_id:
        return False

    current_position = source_match.bracket_position

    if current_position is None:
        return False

    if (int(current_position) - 1) % 2 == 0:

        next_match.player1_id = winner_id
        next_match.source_match1_id = source_match.id

    else:

        next_match.player2_id = winner_id
        next_match.source_match2_id = source_match.id

    return True


def create_next_round_match(tournament, finished_match):
    """
    Create or update the next-round match for a completed feeder.

    V2 bracket behavior:
    - A next-round match may contain one known player and one TBD slot.
    - The next-round match is created as soon as either feeder produces
      a winner.
    - The unresolved feeder remains NULL until that match is completed.
    - Repeated calls update the existing next-round match instead of
      creating duplicates.
    """

    if not tournament:
        return None

    if not finished_match:
        return None

    if not finished_match.winner_id:
        return None

    rounds = tournament_rounds(
        tournament.max_players
    )

    next_round = next_round_name(
        finished_match.round_name,
        rounds
    )

    if not next_round:
        return None

    current_position = finished_match.bracket_position

    if current_position is None:
        return None

    current_position = int(current_position)

    # Determine which position in the next round this match feeds.
    next_position = (
        ((current_position - 1) // 2) + 1
    )

    next_round_number = (
        rounds.index(next_round) + 1
    )

    # ------------------------------------------------------------
    # Find the paired feeder.
    #
    # The other feeder does NOT need to be finished yet.
    # Its winner can remain TBD / NULL.
    # ------------------------------------------------------------

    other_position = (
        current_position - 1
        if current_position % 2 == 0
        else current_position + 1
    )

    other_match = Match.query.filter_by(
        tournament_id=tournament.id,
        round_name=finished_match.round_name,
        bracket_position=other_position
    ).first()

    # ------------------------------------------------------------
    # Determine feeder order.
    #
    # Odd source position -> Player 1
    # Even source position -> Player 2
    # ------------------------------------------------------------

    if current_position % 2 == 1:
        source_match1 = finished_match
        source_match2 = other_match
    else:
        source_match1 = other_match
        source_match2 = finished_match

    # ------------------------------------------------------------
    # Determine known winners.
    #
    # Either side may still be TBD.
    # ------------------------------------------------------------

    player1_id = (
        source_match1.winner_id
        if source_match1 is not None
        else None
    )

    player2_id = (
        source_match2.winner_id
        if source_match2 is not None
        else None
    )

    # At least the finished feeder must provide a winner.
    if not player1_id and not player2_id:
        return None

    # ------------------------------------------------------------
    # Look for an existing next-round match.
    #
    # This is important for idempotency.
    # ------------------------------------------------------------

    next_match = Match.query.filter_by(
        tournament_id=tournament.id,
        round_name=next_round,
        bracket_position=next_position
    ).first()

    if next_match is not None:

        # Update only the feeder slot that now has a winner.
        if player1_id:
            next_match.player1_id = player1_id

        if player2_id:
            next_match.player2_id = player2_id

        if source_match1 is not None:
            next_match.source_match1_id = source_match1.id

        if source_match2 is not None:
            next_match.source_match2_id = source_match2.id

        db.session.flush()

        return next_match

    # ------------------------------------------------------------
    # Create next-round match.
    #
    # One player may be known while the other remains TBD.
    # This is now valid because player1_id/player2_id are nullable
    # in the V2 database schema.
    # ------------------------------------------------------------

    next_match = Match(
        tournament_id=tournament.id,

        player1_id=player1_id,
        player2_id=player2_id,

        player1_score=0,
        player2_score=0,

        status=MATCH_SCHEDULED,

        round_name=next_round,
        round_number=next_round_number,

        match_number=next_position,
        bracket_position=next_position,

        source_match1_id=(
            source_match1.id
            if source_match1 is not None
            else None
        ),

        source_match2_id=(
            source_match2.id
            if source_match2 is not None
            else None
        ),

        is_bye=False,
        bye_reason=None,

        is_forfeit=False,
        forfeit_player_id=None,
        forfeit_reason=None,

        winner_id=None,
        loser_id=None,

        is_live=False
    )

    db.session.add(next_match)
    db.session.flush()

    return next_match


def advance_bye_match(tournament, match):
    if not tournament:
        return None

    if not match:
        return None

    if not match.is_bye:
        return None

    # A finished match is immutable.
    # Never resolve or advance the same BYE twice.
    if match.status == MATCH_FINISHED:
        return None

    player1 = match.player1_id
    player2 = match.player2_id

    if player1 and not player2:
        winner_id = player1

    elif player2 and not player1:
        winner_id = player2

    else:
        return None

    match.winner_id = winner_id
    match.loser_id = None

    match.status = MATCH_FINISHED
    match.finished_at = datetime.utcnow()
    match.is_live = False

    return create_next_round_match(
        tournament,
        match
    )


def resolve_forfeit_match(
    tournament,
    match,
    forfeiting_player_id,
    reason=None
):
    if not tournament:
        return None

    if not match:
        return None

    # A finished match is immutable.
    # Never overwrite an existing final result.
    if match.status == MATCH_FINISHED:
        return None

    if forfeiting_player_id not in (
        match.player1_id,
        match.player2_id
    ):
        return None

    if forfeiting_player_id == match.player1_id:
        winner_id = match.player2_id
    else:
        winner_id = match.player1_id

    if not winner_id:
        return None

    match.is_forfeit = True
    match.forfeit_player_id = forfeiting_player_id
    match.forfeit_reason = reason

    match.winner_id = winner_id
    match.loser_id = forfeiting_player_id

    match.status = MATCH_FINISHED
    match.finished_at = datetime.utcnow()
    match.is_live = False

    return create_next_round_match(
        tournament,
        match
    )

# ============================================================
# PUBLIC PAGES
# ============================================================

@app.route("/")
def home():

    return render_template(
        "index.html"
    )


@app.route("/tournament")
def tournament():

    matches = Match.query.order_by(
        Match.id.asc()
    ).all()

    players = {
        player.id: player
        for player in Player.query.all()
    }

    latest_notice = AdminAction.query.filter_by(
        action="public_notice"
    ).order_by(
        AdminAction.created_at.desc()
    ).first()

    return render_template(
        "tournament.html",
        matches=matches,
        players=players,
        latest_notice=latest_notice
    )


@app.route("/live")
def live():

    # Public live page is driven directly from Founder-controlled
    # Match records. No separate live-state system is created.

    live_matches = Match.query.filter(
        Match.is_live.is_(True)
    ).order_by(
        Match.scheduled_time.asc(),
        Match.id.asc()
    ).all()

    players = {
        player.id: player
        for player in Player.query.all()
    }

    return render_template(
        "live.html",
        matches=live_matches,
        players=players
    )


@app.route("/standings")
def standings():

    players = Player.query.filter_by(
        active=True
    ).order_by(
        Player.name.asc()
    ).all()

    return render_template(
        "standings.html",
        players=players
    )


@app.route("/players")
def players():

    players = Player.query.filter_by(
        active=True
    ).order_by(
        Player.name.asc()
    ).all()

    return render_template(
        "players.html",
        players=players
    )


@app.route("/player/<int:player_id>")
def player_profile(player_id):

    player = Player.query.get_or_404(
        player_id
    )

    player_match = get_player_match(
        player.id
    )

    players = {
        p.id: p
        for p in Player.query.all()
    }

    return render_template(
        "player_profile.html",
        player=player,
        player_match=player_match,
        players=players
    )


@app.route("/matches")
def matches():

    tournament_matches = Match.query.order_by(
        Match.id.asc()
    ).all()

    players = {
        player.id: player
        for player in Player.query.all()
    }

    return render_template(
        "matches.html",
        matches=tournament_matches,
        players=players
    )


@app.route("/terms")
def terms():

    return render_template(
        "terms.html"
    )


# ============================================================
# PLAYER AUTHENTICATION
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def player_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            return "Please enter your email and password.", 400

        player = Player.query.filter_by(email=email).first()

        if not player or not player.password_hash:
            return "Invalid email or password.", 401

        if not check_password_hash(player.password_hash, password):
            return "Invalid email or password.", 401

        if not player.active:
            return "This player account is currently inactive.", 403

        session.clear()
        session["player_id"] = player.id

        return redirect(
            url_for("player_profile", player_id=player.id)
        )

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def player_logout():
    session.pop("player_id", None)
    return redirect(url_for("home"))

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        if not email:
            return "Please enter your email address.", 400

        player = Player.query.filter_by(email=email).first()

        # Do not reveal whether an email exists.
        # This prevents account enumeration.
        if player:
            player.reset_token = secrets.token_urlsafe(32)
            player.reset_token_expires = datetime.utcnow() + timedelta(minutes=30)
            db.session.commit()

            # Development/testing only:
            # In production this token should be delivered by email.
            return render_template(
                "reset_requested.html",
                reset_token=player.reset_token
            )

        return render_template("reset_requested.html")

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    player = Player.query.filter_by(reset_token=token).first()

    if not player:
        return "This password reset link is invalid or has already been used.", 400

    if not player.reset_token_expires:
        return "This password reset link is invalid.", 400

    if datetime.utcnow() > player.reset_token_expires:
        player.reset_token = None
        player.reset_token_expires = None
        db.session.commit()
        return "This password reset link has expired. Please request a new one.", 400

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(password) < 8:
            return "Password must be at least 8 characters long.", 400

        if password != confirm_password:
            return "Passwords do not match.", 400

        player.password_hash = generate_password_hash(password)

        # Make the reset token single-use.
        player.reset_token = None
        player.reset_token_expires = None

        db.session.commit()

        return redirect(url_for("player_login"))

    return render_template("reset_password.html")



# ============================================================
# PLAYER REGISTRATION
# ============================================================

@app.route(
    "/register",
    methods=["GET", "POST"]
)
def register():

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        fc_username = request.form.get(
            "fc_username",
            ""
        ).strip()

        country = request.form.get(
            "country",
            ""
        ).strip()

        squad_ovr = request.form.get(
            "squad_ovr",
            ""
        ).strip()


        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )
        terms_accepted = request.form.get(
            "terms_accepted"
        )

        if not name or not fc_username or not country:

            return (
                "Please complete all required fields."
            ), 400

        if not email or not password:
            return "Email and password are required.", 400

        if "@" not in email or "." not in email:
            return "Please enter a valid email address.", 400

        if len(password) < 8:
            return "Password must be at least 8 characters long.", 400

        existing_email = Player.query.filter_by(email=email).first()

        if existing_email:
            return "This email address is already registered.", 409

        if not squad_ovr.isdigit():

            return (
                "Squad OVR must be a number."
            ), 400

        squad_ovr = int(
            squad_ovr
        )

        if squad_ovr < 1 or squad_ovr > 200:

            return (
                "Invalid Squad OVR."
            ), 400

        if terms_accepted != "yes":

            return (
                "You must accept the tournament rules."
            ), 400

        existing_player = Player.query.filter_by(
            fc_username=fc_username
        ).first()

        if existing_player:

            return (
                "This FC Mobile username is already registered."
            ), 409

        # --------------------------------------------------------
        # Tournament registration
        # --------------------------------------------------------
        # Registration belongs to the current tournament.
        # Do not create an orphan player account if no tournament exists.
        tournament = Tournament.query.filter_by(
            status=TOURNAMENT_REGISTRATION
        ).order_by(
            Tournament.id.desc()
        ).first()

        if not tournament:
            return (
                "Registration is currently unavailable because "
                "there is no tournament open for registration."
            ), 503

        # Prevent duplicate participation in the same tournament.
        existing_participant = TournamentParticipant.query.filter_by(
            tournament_id=tournament.id
        ).join(
            Player,
            TournamentParticipant.player_id == Player.id
        ).filter(
            Player.fc_username == fc_username
        ).first()

        if existing_participant:
            return (
                "This FC Mobile username is already registered "
                "for this tournament."
            ), 409

        player = Player(
            name=name,
            fc_username=fc_username,
            country=country,
            squad_ovr=squad_ovr,
            email=email,
            password_hash=generate_password_hash(password),
            application_status="pending",
            terms_accepted=True,
            terms_version="1.1",
            terms_accepted_at=datetime.utcnow(),
            active=True
        )

        db.session.add(player)

        # Flush so player.id exists before creating the participant.
        # Both records remain inside the same database transaction.
        db.session.flush()

        payment_required = bool(tournament.payment_enabled)

        participant = TournamentParticipant(
            tournament_id=tournament.id,
            player_id=player.id,
            team_name=player.team_name,
            status="registered",
            availability_status="unknown",

            payment_status=(
                "unpaid"
                if payment_required
                else "not_required"
            ),

            payment_required_amount=(
                float(tournament.entry_fee)
                if payment_required
                else 0
            ),

            payment_received_amount=0,
            founder_payment_verified=False,

            overpayment_amount=0,
            overpayment_reviewed=False,

            payment_reversed=False,

            refund_requested=False,
            refund_approved=False,
            refund_amount=0,
            refund_completed=False,

            registered_at=datetime.utcnow()
        )

        db.session.add(participant)

        # Player account and tournament participation are committed together.
        # If either operation fails, the transaction can roll back.
        db.session.commit()

        return redirect(
            url_for(
                "registration_success",
                player_id=player.id
            )
        )

    return render_template(
        "register.html"
    )


# ============================================================
# REGISTRATION SUCCESS
# ============================================================

@app.route(
    "/registration-success/<int:player_id>"
)
def registration_success(player_id):

    player = Player.query.get_or_404(
        player_id
    )

    tournament = (
        Tournament.query
        .order_by(Tournament.id.desc())
        .first()
    )

    if (
        tournament
        and tournament.payment_enabled
        and float(tournament.entry_fee or 0) > 0
        and tournament.status == TOURNAMENT_REGISTRATION
    ):
        return redirect(
            url_for(
                "start_payment",
                player_id=player.id,
            )
        )

    return render_template(
        "registration_success.html",
        player=player
    )


# ============================================================
# FOUNDER LOGIN
# ============================================================

@app.route(
    "/admin/login",
    methods=["GET", "POST"]
)
def admin_login():

    if session.get("founder_authenticated"):
        return redirect(
            url_for("admin_dashboard")
        )

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        configured_email = (
            app.config.get("FOUNDER_EMAIL", "")
            .strip()
            .lower()
        )

        configured_password = (
            app.config.get("FOUNDER_PASSWORD", "")
        )

        if (
            email
            and password
            and configured_email
            and configured_password
            and secrets.compare_digest(
                email,
                configured_email
            )
            and secrets.compare_digest(
                password,
                configured_password
            )
        ):

            session["founder_authenticated"] = True
            session["founder_email"] = configured_email

            return redirect(
                url_for("admin_dashboard")
            )

        flash(
            "Invalid founder credentials.",
            "error"
        )

    return render_template(
        "admin/login.html"
    )


# ============================================================
# FOUNDER LOGOUT
# ============================================================

@app.route(
    "/admin/logout",
    methods=["POST"]
)
def admin_logout():

    session.pop(
        "founder_authenticated",
        None
    )

    session.pop(
        "founder_email",
        None
    )

    return redirect(
        url_for("admin_login")
    )


# ============================================================
# FOUNDER DASHBOARD
# ============================================================

@app.route("/admin/dashboard")
def admin_dashboard():

    access = founder_required()
    if access:
        return access

    players = Player.query.order_by(
        Player.registered_at.desc()
    ).all()

    players_by_id = {
        player.id: player
        for player in players
    }


    approved_count = Player.query.filter_by(
        application_status="approved"
    ).count()

    pending_count = Player.query.filter_by(
        application_status="pending"
    ).count()

    waitlist_count = Player.query.filter_by(
        application_status="waitlist"
    ).count()

    withdrawn_count = Player.query.filter_by(
        application_status="withdrawn"
    ).count()

    removed_count = Player.query.filter_by(
        application_status="removed"
    ).count()

    messages = FounderMessage.query.order_by(
        FounderMessage.created_at.desc()
    ).all()

    unread_messages = FounderMessage.query.filter_by(
        status="unread"
    ).count()

    actions = AdminAction.query.order_by(
        AdminAction.created_at.desc()
    ).limit(20).all()

    tournament = Tournament.query.order_by(
        Tournament.id.desc()
    ).first()

    tournament_matches = []

    if tournament:

        tournament_matches = Match.query.filter_by(
            tournament_id=tournament.id
        ).order_by(
            Match.id.asc()
        ).all()

    player_map = {
        player.id: player
        for player in players
    }

    return render_template(
        "admin/dashboard.html",
        players=players,
        players_by_id=players_by_id,
        player_map=player_map,
        approved_count=approved_count,
        pending_count=pending_count,
        waitlist_count=waitlist_count,
        withdrawn_count=withdrawn_count,
        removed_count=removed_count,
        messages=messages,
        unread_messages=unread_messages,
        actions=actions,
        tournament=tournament,
        tournament_matches=tournament_matches,
        matches=tournament_matches
    )


# ============================================================
# CHANGE PLAYER STATUS
# ============================================================

@app.route(
    "/admin/player/<int:player_id>/status",
    methods=["POST"]
)
def change_player_status(player_id):

    access = founder_required()
    if access:
        return access


    player = Player.query.get_or_404(
        player_id
    )

    new_status = request.form.get(
        "status"
    )

    allowed_statuses = [
        "pending",
        "approved",
        "waitlist",
        "withdrawn",
        "removed",
        "suspended"
    ]

    if new_status not in allowed_statuses:

        return (
            "Invalid player status."
        ), 400

    old_status = player.application_status

    if new_status == "approved":

        approved_count = Player.query.filter_by(
            application_status="approved"
        ).count()

        if (
            old_status != "approved"
            and approved_count >= 16
        ):

            return (
                "The tournament already has "
                "16 approved players. "
                "Use the waitlist until a place "
                "becomes available."
            ), 400

    player.application_status = new_status

    if new_status in [
        "withdrawn",
        "removed",
        "suspended"
    ]:

        player.active = False

    elif new_status in [
        "pending",
        "approved",
        "waitlist"
    ]:

        player.active = True

    action = AdminAction(
        player_id=player.id,
        action="status_change",
        old_status=old_status,
        new_status=new_status,
        notes="Founder changed player application status.",
        created_at=datetime.utcnow()
    )

    db.session.add(
        action
    )

    db.session.commit()

    return redirect(
        url_for(
            "admin_dashboard"
        )
    )


# ============================================================
# PLAYER CONTACT FOUNDER
# ============================================================

@app.route(
    "/player/<int:player_id>/contact",
    methods=["GET", "POST"]
)
def player_contact(player_id):

    player = Player.query.get_or_404(
        player_id
    )

    if request.method == "POST":

        subject = request.form.get(
            "subject",
            ""
        ).strip()

        message = request.form.get(
            "message",
            ""
        ).strip()

        if not subject or not message:

            return (
                "Please complete the subject and message."
            ), 400

        founder_message = FounderMessage(
            player_id=player.id,
            subject=subject,
            message=message,
            status="unread",
            created_at=datetime.utcnow()
        )

        db.session.add(
            founder_message
        )

        db.session.commit()

        return render_template(
            "player/message_sent.html",
            player=player
        )

    return render_template(
        "player/contact_founder.html",
        player=player
    )


# ============================================================
# MARK MESSAGE AS READ
# ============================================================

@app.route(
    "/admin/message/<int:message_id>/read",
    methods=["POST"]
)
def mark_message_read(message_id):

    access = founder_required()
    if access:
        return access


    message = FounderMessage.query.get_or_404(
        message_id
    )

    message.status = "read"

    db.session.commit()

    return redirect(
        url_for(
            "admin_dashboard"
        )
    )


# ============================================================
# FOUNDER REPLY
# ============================================================

@app.route(
    "/admin/message/<int:message_id>/reply",
    methods=["POST"]
)
def reply_to_message(message_id):

    access = founder_required()
    if access:
        return access


    message = FounderMessage.query.get_or_404(
        message_id
    )

    reply = request.form.get(
        "founder_reply",
        ""
    ).strip()

    if not reply:

        return (
            "Please enter a reply."
        ), 400

    message.founder_reply = reply
    message.replied_at = datetime.utcnow()
    message.status = "replied"

    db.session.commit()

    return redirect(
        url_for(
            "admin_dashboard"
        )
    )


# ============================================================
# PLAYER MESSAGE CENTER
# ============================================================

@app.route(
    "/player/<int:player_id>/messages"
)
def player_messages(player_id):

    player = Player.query.get_or_404(
        player_id
    )

    messages = FounderMessage.query.filter_by(
        player_id=player.id
    ).order_by(
        FounderMessage.created_at.desc()
    ).all()

    return render_template(
        "player/messages.html",
        player=player,
        messages=messages
    )


# ============================================================
# FOUNDER — CREATE OFFICIAL DRAW
# ============================================================

@app.route(
    "/admin/draw-tournament",
    methods=["POST"]
)

# ============================================================
# FOUNDER — TOURNAMENT CONTROL
# ============================================================

@app.route(
    "/admin/tournament/control",
    methods=["POST"]
)
def founder_tournament_control():

    access = founder_required()
    if access:
        return access

    tournament = Tournament.query.order_by(
        Tournament.id.desc()
    ).first()

    if not tournament:
        return "No tournament exists.", 404

    action_type = request.form.get(
        "action",
        ""
    ).strip()

    allowed_actions = {
        "open_registration": TOURNAMENT_REGISTRATION,
        "start_tournament": TOURNAMENT_IN_PROGRESS,
        "pause_tournament": TOURNAMENT_PAUSED,
        "resume_tournament": TOURNAMENT_IN_PROGRESS,
        "complete_tournament": TOURNAMENT_COMPLETED
    }

    if action_type == "release_draw":
        return redirect(
            url_for("draw_tournament")
        )

    if action_type == "reset_registration":
        return redirect(
            url_for("reset_tournament")
        )

    if action_type not in allowed_actions:
        return "Invalid tournament control action.", 400

    old_status = tournament.status
    new_status = allowed_actions[action_type]

    if action_type == "start_tournament":
        if old_status not in [TOURNAMENT_DRAW_RELEASED, TOURNAMENT_PAUSED]:
            return (
                "The tournament must have a released draw "
                "before it can start."
            ), 409

    if action_type == "pause_tournament":
        if old_status != TOURNAMENT_IN_PROGRESS:
            return (
                "Only an in-progress tournament can be paused."
            ), 409

    if action_type == "resume_tournament":
        if old_status != TOURNAMENT_PAUSED:
            return (
                "Only a paused tournament can be resumed."
            ), 409

    if action_type == "complete_tournament":
        if old_status not in [TOURNAMENT_IN_PROGRESS, TOURNAMENT_PAUSED]:
            return (
                "The tournament must be in progress or paused "
                "before it can be completed."
            ), 409

    tournament.status = new_status

    labels = {
        "open_registration":
            "Founder opened tournament registration.",

        "start_tournament":
            "Founder started the tournament.",

        "pause_tournament":
            "Founder paused the tournament.",

        "resume_tournament":
            "Founder resumed the tournament.",

        "complete_tournament":
            "Founder marked the tournament completed."
    }

    action = AdminAction(
        action="tournament_control",
        old_status=old_status,
        new_status=new_status,
        notes=labels[action_type],
        created_at=datetime.utcnow()
    )

    db.session.add(action)
    db.session.commit()

    return redirect(
        url_for("admin_dashboard")
    )


# ============================================================
# FOUNDER — TOURNAMENT SETTINGS
# ============================================================

@app.route(
    "/admin/tournament/settings",
    methods=["POST"]
)
def founder_tournament_settings():

    access = founder_required()
    if access:
        return access

    tournament = Tournament.query.order_by(
        Tournament.id.desc()
    ).first()

    if not tournament:
        return "No tournament exists.", 404

    name = request.form.get(
        "name",
        ""
    ).strip()

    max_players_raw = request.form.get(
        "max_players",
        ""
    ).strip()

    entry_fee_raw = request.form.get(
        "entry_fee",
        ""
    ).strip()

    competition_day = request.form.get(
        "competition_day",
        ""
    ).strip()

    final_day = request.form.get(
        "final_day",
        ""
    ).strip()

    if not name:
        return "Tournament name is required.", 400

    if not max_players_raw.isdigit():
        return (
            "Maximum players must be a whole number."
        ), 400

    max_players = int(max_players_raw)

    if not entry_fee_raw:
        return "Entry fee is required.", 400

    if not entry_fee_raw.isdigit():
        return "Entry fee must be a whole number.", 400

    entry_fee = int(entry_fee_raw)

    if entry_fee < 0:
        return "Entry fee cannot be negative.", 400

    if max_players not in [2, 4, 8, 16]:
        return (
            "Maximum players must be 2, 4, 8 or 16."
        ), 400

    if not competition_day:
        return "Competition day is required.", 400

    if not final_day:
        return "Final day is required.", 400

    old_values = (
        f"name={tournament.name}, "
        f"max_players={tournament.max_players}, "
        f"entry_fee={tournament.entry_fee}, "
        f"competition_day={tournament.competition_day}, "
        f"final_day={tournament.final_day}"
    )

    tournament.name = name
    tournament.max_players = max_players
    tournament.entry_fee = entry_fee
    tournament.competition_day = competition_day
    tournament.final_day = final_day

    new_values = (
        f"name={tournament.name}, "
        f"max_players={tournament.max_players}, "
        f"entry_fee={tournament.entry_fee}, "
        f"competition_day={tournament.competition_day}, "
        f"final_day={tournament.final_day}"
    )

    action = AdminAction(
        action="tournament_settings_updated",
        notes=(
            "Founder updated tournament settings. "
            f"Previous: {old_values}. "
            f"New: {new_values}."
        ),
        created_at=datetime.utcnow()
    )

    db.session.add(action)
    db.session.commit()

    return redirect(
        url_for("admin_dashboard")
    )


# ============================================================
# FOUNDER — OFFICIAL TOURNAMENT DRAW
# ============================================================

@app.route(
    "/admin/tournament/draw",
    methods=["POST"]
)
def draw_tournament():
    """
    Release the official V2 tournament draw.

    Supports 2 through 32 approved players.
    Non-power-of-two player counts receive BYEs.
    """

    access = founder_required()

    if access:
        return access

    tournament = Tournament.query.order_by(
        Tournament.id.desc()
    ).first()

    if not tournament:
        return "No tournament exists.", 404

    if tournament.status not in [
        TOURNAMENT_REGISTRATION,
        TOURNAMENT_DRAW_RELEASED
    ]:
        return (
            "The tournament draw cannot be changed "
            "from its current state."
        ), 409

    existing_matches = Match.query.filter_by(
        tournament_id=tournament.id
    ).all()

    if existing_matches:
        return (
            "An official draw already exists. "
            "Reset the tournament before creating another draw."
        ), 409

    approved_players = Player.query.filter_by(
        application_status="approved",
        active=True
    ).order_by(
        Player.id.asc()
    ).all()

    player_count = len(approved_players)

    if player_count < 2:
        return (
            "At least two approved active players are required."
        ), 400

    if player_count > tournament.max_players:
        return (
            "There are more approved players than "
            "the tournament player limit."
        ), 400

    if player_count > 32:
        return (
            "AminationEsports currently supports "
            "a maximum of 32 players."
        ), 400

    try:
        capacity = tournament_bracket_capacity(player_count)
        rounds = tournament_rounds(player_count)
    except ValueError as exc:
        return str(exc), 400

    random.shuffle(approved_players)

    pairings = build_round_one_pairings(
        approved_players
    )

    first_round = rounds[0]

    for pairing in pairings:

        player1 = pairing["player1"]
        player2 = pairing["player2"]

        player1_id = (
            player1.id
            if player1 is not None
            else None
        )

        player2_id = (
            player2.id
            if player2 is not None
            else None
        )

        is_bye = bool(pairing["is_bye"])

        bye_reason = None

        if is_bye:
            bye_reason = (
                "Automatic BYE: "
                + str(player_count)
                + " players entered a "
                + str(capacity)
                + "-slot bracket."
            )

        match = Match(
            tournament_id=tournament.id,

            player1_id=player1_id,
            player2_id=player2_id,

            player1_score=0,
            player2_score=0,

            status=(
                MATCH_FINISHED
                if is_bye
                else MATCH_SCHEDULED
            ),

            round_name=first_round,
            round_number=1,

            match_number=pairing["match_number"],
            bracket_position=pairing["bracket_position"],

            source_match1_id=None,
            source_match2_id=None,

            is_bye=is_bye,
            bye_reason=bye_reason,

            is_forfeit=False,
            forfeit_player_id=None,
            forfeit_reason=None,

            winner_id=None,
            loser_id=None,

            is_live=False
        )

        if is_bye:

            if player1_id and not player2_id:
                match.winner_id = player1_id

            elif player2_id and not player1_id:
                match.winner_id = player2_id

            else:
                return (
                    "Invalid BYE pairing generated."
                ), 500

            match.finished_at = datetime.utcnow()

        db.session.add(match)

    db.session.flush()

    first_round_matches = Match.query.filter_by(
        tournament_id=tournament.id,
        round_name=first_round
    ).order_by(
        Match.bracket_position.asc()
    ).all()

    for match in first_round_matches:

        if not match.is_bye:
            continue

        if not match.winner_id:
            continue

        create_next_round_match(
            tournament,
            match
        )

    tournament.status = TOURNAMENT_DRAW_RELEASED

    action = AdminAction(
        action="tournament_draw_released",
        notes=(
            "Founder released the official V2 draw "
            "for "
            + tournament.name
            + " with "
            + str(player_count)
            + " players in a "
            + str(capacity)
            + "-slot bracket. BYEs: "
            + str(calculate_bye_count(player_count))
            + "."
        ),
        created_at=datetime.utcnow()
    )

    db.session.add(action)
    db.session.commit()

    return redirect(
        url_for("admin_dashboard")
    )


# ============================================================
# FOUNDER — RESET TOURNAMENT
# ============================================================

@app.route(
    "/admin/tournament/reset",
    methods=["POST"]
)
def reset_tournament():

    access = founder_required()
    if access:
        return access

    tournament = Tournament.query.order_by(
        Tournament.id.desc()
    ).first()

    if not tournament:
        return "No tournament exists.", 404

    if tournament.status == TOURNAMENT_COMPLETED:
        return (
            "A completed tournament cannot be reset."
        ), 409

    matches = Match.query.filter_by(
        tournament_id=tournament.id
    ).all()

    for match in matches:
        db.session.delete(match)

    tournament.status = TOURNAMENT_REGISTRATION

    action = AdminAction(
        action="tournament_reset",
        notes=(
            f"Founder reset {tournament.name} "
            "to registration."
        ),
        created_at=datetime.utcnow()
    )

    db.session.add(action)
    db.session.commit()

    return redirect(
        url_for("admin_dashboard")
    )




# ============================================================
# FOUNDER — PLAYER ADMINISTRATION
# ============================================================

@app.route(
    "/admin/founder/player/<int:player_id>/status",
    methods=["POST"]
)
def founder_player_status(player_id):

    access = founder_required()
    if access:
        return access


    player = Player.query.get_or_404(player_id)

    new_status = request.form.get(
        "status",
        ""
    ).strip()

    allowed_statuses = [
        "pending",
        "approved",
        "waitlist",
        "withdrawn",
        "removed",
        "suspended",
        "eliminated",
        "champion",
        "runner_up"
    ]

    if new_status not in allowed_statuses:
        return "Invalid player status.", 400

    old_status = player.application_status

    # Do not allow more than 16 approved players.
    # A player already approved does not consume another slot.
    if new_status == "approved" and old_status != "approved":
        approved_count = Player.query.filter_by(
            application_status="approved",
            active=True
        ).count()

        if approved_count >= 16:
            return (
                "The tournament already has 16 approved players. "
                "Use the waitlist until a place becomes available."
            ), 400


    player.application_status = new_status

    if new_status in [
        "removed",
        "suspended",
        "withdrawn"
    ]:
        player.active = False

    elif new_status in [
        "pending",
        "approved",
        "waitlist"
    ]:
        player.active = True

    action = AdminAction(
        player_id=player.id,
        action="founder_player_status_changed",
        old_status=old_status,
        new_status=new_status,
        notes=(
            f"Founder changed {player.name} "
            f"from {old_status} to {new_status}."
        ),
        created_at=datetime.utcnow()
    )

    db.session.add(action)
    db.session.commit()

    return redirect(
        url_for("admin_dashboard")
    )


# ============================================================
# FOUNDER — RESTORE PLAYER
# ============================================================

@app.route(
    "/admin/founder/player/<int:player_id>/restore",
    methods=["POST"]
)
def founder_restore_player(player_id):

    access = founder_required()
    if access:
        return access


    player = Player.query.get_or_404(player_id)

    old_status = player.application_status

    player.application_status = "approved"
    player.active = True

    action = AdminAction(
        player_id=player.id,
        action="founder_player_restored",
        old_status=old_status,
        new_status="approved",
        notes=(
            f"Founder restored {player.name} "
            "to active approved status."
        ),
        created_at=datetime.utcnow()
    )

    db.session.add(action)
    db.session.commit()

    return redirect(
        url_for("admin_dashboard")
    )


# ============================================================
# FOUNDER — CHAMPIONSHIP STARS
# ============================================================

@app.route(
    "/admin/founder/player/<int:player_id>/stars",
    methods=["POST"]
)
def founder_player_stars(player_id):

    access = founder_required()
    if access:
        return access


    player = Player.query.get_or_404(player_id)

    operation = request.form.get(
        "operation",
        ""
    ).strip()

    amount_raw = request.form.get(
        "amount",
        "1"
    ).strip()

    if operation not in [
        "add",
        "remove"
    ]:
        return "Invalid star operation.", 400

    if not amount_raw.isdigit():
        return "Star amount must be a whole number.", 400

    amount = int(amount_raw)

    if amount < 1 or amount > 100:
        return (
            "Star amount must be between 1 and 100."
        ), 400

    old_stars = player.championship_stars or 0

    if operation == "add":
        player.championship_stars = old_stars + amount
    else:
        player.championship_stars = max(
            0,
            old_stars - amount
        )

    action = AdminAction(
        player_id=player.id,
        action="founder_championship_stars",
        notes=(
            f"Founder {operation}ed {amount} "
            f"championship star(s) for {player.name}. "
            f"Previous: {old_stars}. "
            f"New: {player.championship_stars}."
        ),
        created_at=datetime.utcnow()
    )

    db.session.add(action)
    db.session.commit()

    return redirect(
        url_for("admin_dashboard")
    )



# ============================================================
# FOUNDER — PUBLIC ANNOUNCEMENT
# ============================================================

@app.route(
    "/admin/founder/public-announcement",
    methods=["POST"]
)
def founder_public_announcement():

    access = founder_required()
    if access:
        return access

    tournament = Tournament.query.order_by(
        Tournament.id.desc()
    ).first()

    if not tournament:
        return (
            "No tournament exists."
        ), 404

    title = request.form.get(
        "title",
        ""
    ).strip()

    message = request.form.get(
        "message",
        ""
    ).strip()

    if not title:
        return (
            "Announcement title is required."
        ), 400

    if not message:
        return (
            "Announcement message is required."
        ), 400

    if len(title) > 150:
        return (
            "Announcement title is too long."
        ), 400

    if len(message) > 5000:
        return (
            "Announcement message is too long."
        ), 400

    action = AdminAction(
        action="public_notice",
        notes=(
            f"{title}\n\n"
            f"{message}"
        ),
        created_at=datetime.utcnow()
    )

    db.session.add(action)
    db.session.commit()

    return redirect(
        url_for("admin_dashboard")
    )

# ============================================================
# FOUNDER — MATCH PLAYER SUBSTITUTION
# ============================================================

@app.route(
    "/admin/founder/match/<int:match_id>/substitute",
    methods=["POST"]
)
def founder_substitute_match_player(match_id):

    access = founder_required()
    if access:
        return access

    match = Match.query.get_or_404(match_id)

    if match.is_live:
        return "A live match cannot be substituted.", 409

    if match.status != MATCH_SCHEDULED:
        return "Only scheduled matches can be substituted.", 409

    if match.winner_id:
        return "A match with a winner cannot be changed.", 409

    player_to_replace = request.form.get("player_to_replace", "").strip()
    replacement_player = request.form.get("replacement_player", "").strip()

    try:
        player_to_replace = int(player_to_replace)
        replacement_player = int(replacement_player)
    except (TypeError, ValueError):
        return "Invalid player selection.", 400

    if player_to_replace == replacement_player:
        return "The replacement player must be different.", 400

    if player_to_replace not in [match.player1_id, match.player2_id]:
        return "The selected player is not part of this match.", 400

    if replacement_player in [match.player1_id, match.player2_id]:
        return "That player is already in this match.", 400

    replacement = Player.query.filter_by(
        id=replacement_player,
        active=True,
        application_status="approved"
    ).first()

    if not replacement:
        return "The replacement player is not an approved active player.", 400

    # If the replacement player is already in another scheduled match,
    # swap the two players instead of rejecting the operation.
    replacement_match = Match.query.filter(
        Match.id != match.id,
        Match.status == MATCH_SCHEDULED,
        Match.is_live.is_(False),
        Match.winner_id.is_(None),
        or_(
            Match.player1_id == replacement_player,
            Match.player2_id == replacement_player
        )
    ).first()

    if replacement_match:
        if replacement_match.player1_id == replacement_player:
            replacement_match.player1_id = player_to_replace
        else:
            replacement_match.player2_id = player_to_replace

    if match.player1_id == player_to_replace:
        match.player1_id = replacement_player
    else:
        match.player2_id = replacement_player

    old_player = Player.query.get(player_to_replace)
    old_name = old_player.name if old_player else f"Player #{player_to_replace}"

    if replacement_match:
        notes = (
            f"Founder swapped {old_name} with {replacement.name}. "
            f"Match #{match.id} now contains {replacement.name}; "
            f"Match #{replacement_match.id} now contains {old_name}."
        )
    else:
        notes = (
            f"Founder substituted {old_name} with "
            f"{replacement.name} in Match #{match.id}."
        )

    action = AdminAction(
        action="match_player_substituted",
        notes=notes,
        created_at=datetime.utcnow()
    )

    db.session.add(action)
    db.session.commit()

    return redirect(url_for("admin_dashboard"))

# ============================================================
# FOUNDER — MATCH SCHEDULING
# ============================================================

@app.route(
    "/admin/founder/match/<int:match_id>/schedule",
    methods=["POST"]
)
def founder_schedule_match(match_id):

    access = founder_required()
    if access:
        return access

    match = Match.query.get_or_404(match_id)

    scheduled_time = request.form.get(
        "scheduled_time",
        ""
    ).strip()

    if not scheduled_time:
        return "A match date and time are required.", 400

    try:
        match.scheduled_time = datetime.fromisoformat(
            scheduled_time
        )
    except ValueError:
        return "Invalid match date/time.", 400

    db.session.commit()

    return redirect(
        url_for("admin_dashboard")
    )


# ============================================================
# FOUNDER — LIVE MATCH CONTROL
# ============================================================

@app.route(
    "/admin/founder/match/<int:match_id>/live",
    methods=["POST"]
)
def founder_live_match_control(match_id):

    access = founder_required()
    if access:
        return access

    match = Match.query.get_or_404(match_id)
    action_type = request.form.get("action", "").strip()

    if action_type not in ["start", "stop", "update", "update_score", "finish"]:
        return "Invalid live match action.", 400

    # ========================================================
    # BYE PROTECTION
    # ========================================================

    if match.is_bye and action_type in ["start", "update", "finish"]:
        return (
            "A BYE match is automatically resolved and "
            "does not require gameplay."
        ), 409

    # ========================================================
    # START
    # ========================================================

    if action_type == "start":

        if not match.player1_id or not match.player2_id:
            return (
                "This match is waiting for both players "
                "to advance into the bracket."
            ), 409

        if match.status == MATCH_FINISHED:
            return (
                "A finished match cannot be started again."
            ), 409

        if match.status == MATCH_LIVE and match.is_live:
            return "This match is already live.", 409

        # Only one match can be live at a time.
        other_live_matches = Match.query.filter(
            Match.is_live.is_(True),
            Match.id != match.id
        ).all()

        for other_match in other_live_matches:
            other_match.is_live = False

            if other_match.status == MATCH_LIVE:
                other_match.status = MATCH_SCHEDULED

        match.is_live = True
        match.status = MATCH_LIVE

        tournament = db.session.get(
            Tournament,
            match.tournament_id
        )

        if (
            tournament
            and tournament.status == TOURNAMENT_DRAW_RELEASED
        ):
            tournament.status = TOURNAMENT_IN_PROGRESS

        if not match.started_at:
            match.started_at = datetime.utcnow()

    # ========================================================
    # STOP
    # ========================================================

    elif action_type == "stop":

        if match.status == MATCH_FINISHED:
            return (
                "A finished match cannot be stopped."
            ), 409

        match.is_live = False

        if match.status == MATCH_LIVE:
            match.status = MATCH_SCHEDULED

    # ========================================================
    # LIVE SCORE UPDATE
    # ========================================================

    elif action_type in ["update", "update_score"]:

        if (
            match.status != MATCH_LIVE
            or not match.is_live
        ):
            return (
                "Score updates are only allowed "
                "while the match is live."
            ), 409

        try:
            player1_score = int(
                request.form.get("player1_score", "")
            )
            player2_score = int(
                request.form.get("player2_score", "")
            )
        except (TypeError, ValueError):
            return (
                "Both player scores must be valid whole numbers."
            ), 400

        if player1_score < 0 or player2_score < 0:
            return "Scores cannot be negative.", 400

        # Live update only.
        # Winner and loser remain unset.
        match.player1_score = player1_score
        match.player2_score = player2_score

    # ========================================================
    # FINISH
    # ========================================================

    elif action_type == "finish":

        if match.status == MATCH_FINISHED:
            return (
                "This match is already finished."
            ), 409

        if (
            match.status != MATCH_LIVE
            or not match.is_live
        ):
            return (
                "Only a live match can be finished."
            ), 409

        try:
            player1_score = int(
                request.form.get("player1_score", "")
            )
            player2_score = int(
                request.form.get("player2_score", "")
            )
        except (TypeError, ValueError):
            return (
                "Both player scores must be valid whole numbers."
            ), 400

        if player1_score < 0 or player2_score < 0:
            return "Scores cannot be negative.", 400

        if player1_score == player2_score:
            return (
                "A finished match must have a winning score."
            ), 400

        match.player1_score = player1_score
        match.player2_score = player2_score

        if player1_score > player2_score:
            match.winner_id = match.player1_id
            match.loser_id = match.player2_id
        else:
            match.winner_id = match.player2_id
            match.loser_id = match.player1_id

        match.is_live = False
        match.status = MATCH_FINISHED
        match.finished_at = datetime.utcnow()

        # ====================================================
        # GET TOURNAMENT
        # ====================================================
        tournament = db.session.get(
            Tournament,
            match.tournament_id
        )

        # ====================================================
        # ADVANCE WINNER TO NEXT ROUND
        # ====================================================
        if tournament:
            create_next_round_match(
                tournament,
                match
            )

        # ====================================================
        # TOURNAMENT COMPLETION — FINAL
        # ====================================================

        if (
            tournament
            and match.round_name == "Final"
            and match.winner_id
            and match.loser_id
        ):
            tournament.status = TOURNAMENT_COMPLETED
            tournament.completed_at = datetime.utcnow()
            tournament.champion_id = match.winner_id
            tournament.runner_up_id = match.loser_id

        # ====================================================
        # PLAYER STATISTICS
        # ====================================================

        from models import PlayerStatistic

        for player_id in [
            match.player1_id,
            match.player2_id
        ]:
            stats = PlayerStatistic.query.filter_by(
                player_id=player_id
            ).first()

            if not stats:
                stats = PlayerStatistic(
                    player_id=player_id,
                    matches_played=0,
                    wins=0,
                    draws=0,
                    losses=0,
                    goals=0,
                    assists=0,
                    clean_sheets=0,
                    saves=0,
                    critical_saves=0
                )
                db.session.add(stats)

            stats.matches_played += 1

        winner_stats = PlayerStatistic.query.filter_by(
            player_id=match.winner_id
        ).first()

        loser_stats = PlayerStatistic.query.filter_by(
            player_id=match.loser_id
        ).first()

        if winner_stats:
            winner_stats.wins += 1

            if match.winner_id == match.player1_id:
                winner_stats.goals += match.player1_score
            else:
                winner_stats.goals += match.player2_score

        if loser_stats:
            loser_stats.losses += 1

            if match.loser_id == match.player1_id:
                loser_stats.goals += match.player1_score
            else:
                loser_stats.goals += match.player2_score

    # ========================================================
    # SAVE
    # ========================================================

    db.session.commit()

    # Score updates return directly.
    if action_type in ["update", "update_score"]:
        return (
            f"Live score updated: "
            f"{match.player1_score} - {match.player2_score}"
        ), 200

    return redirect(
        url_for("admin_dashboard")
    )

@app.route("/production-diagnostic", methods=["GET"])
def production_diagnostic():

    from sqlalchemy import text

    try:
        player_count = Player.query.count()

        approved_count = Player.query.filter_by(
            active=True,
            application_status="approved"
        ).count()

        tournament_count = Tournament.query.count()

        tournament = Tournament.query.order_by(
            Tournament.id.desc()
        ).first()

        if tournament:
            tournament_info = {
                "id": tournament.id,
                "name": tournament.name,
                "status": tournament.status,
                "max_players": tournament.max_players
            }
        else:
            tournament_info = None

        try:
            from models import Match
            match_count = Match.query.count()
        except Exception:
            match_count = "unable to check"

        return {
            "diagnostic": "READ ONLY",
            "database": "production",
            "players": player_count,
            "approved_active_players": approved_count,
            "tournaments": tournament_count,
            "latest_tournament": tournament_info,
            "matches": match_count
        }, 200

    except Exception as e:
        return {
            "diagnostic": "READ ONLY",
            "database_check": "FAILED",
            "error_type": type(e).__name__,
            "error": str(e)
        }, 500

# TEMPORARY PRODUCTION TOURNAMENT REPAIR
# Creates the tournament only when production has no tournament.
# Founder authentication is required.
@app.route("/admin/repair-tournament", methods=["GET", "POST"])
def repair_tournament():
    access = founder_required()
    if access:
        return access

    existing = Tournament.query.order_by(
        Tournament.id.desc()
    ).first()

    if existing:
        return (
            f"Tournament already exists: "
            f"{existing.name} | max_players={existing.max_players} | "
            f"status={existing.status}"
        )

    tournament = Tournament(
        name="Amination FC Season 1",
        status=TOURNAMENT_REGISTRATION,
        max_players=16
    )

    db.session.add(tournament)
    db.session.commit()

    return (
        f"Created tournament: {tournament.name} | "
        f"id={tournament.id} | max_players={tournament.max_players} | "
        f"status={tournament.status}"
    )



# ============================================================
# STEP 8R.67 — TEMPORARY ERROR LOGGER
# ============================================================

@app.errorhandler(500)
def temporary_internal_error_logger(error):
    import traceback

    print("\n" + "=" * 70, flush=True)
    print("STEP 8R.67 — PRODUCTION 500 TRACEBACK", flush=True)
    print("=" * 70, flush=True)
    traceback.print_exc()
    print("=" * 70, flush=True)

    return (
        "Internal Server Error",
        500
    )


# ============================================================
# STEP 8R.69 — PRODUCTION SCHEMA DIAGNOSTIC
# ============================================================

@app.route("/admin/diagnostic/match-schema")
def diagnostic_match_schema():
    access = founder_required()
    if access:
        return access

    from sqlalchemy import inspect

    inspector = inspect(db.engine)

    columns = inspector.get_columns("match")

    return {
        "table": "match",
        "columns": [
            {
                "name": column["name"],
                "type": str(column["type"]),
                "nullable": column["nullable"]
            }
            for column in columns
        ]
    }

# ============================================================
if __name__ == "__main__":

    app.run(
        debug=True,
        host="0.0.0.0",
        port=5000
    )
