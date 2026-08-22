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
    Record,
    AdminAction,
    FounderMessage
)


app = Flask(__name__)

app.config.from_object(Config)

db.init_app(app)


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
# TOURNAMENT HELPERS
# ============================================================

def tournament_rounds(player_count):
    """
    Return the correct bracket rounds for the number of players.

    16 players:
        Round 1
        Quarter-finals
        Semi-finals
        Final

    8 players:
        Quarter-finals
        Semi-finals
        Final

    4 players:
        Semi-finals
        Final

    2 players:
        Final
    """

    if player_count >= 16:
        return [
            ROUND_1,
            QUARTER_FINAL,
            SEMI_FINAL,
            FINAL
        ]

    if player_count >= 8:
        return [
            QUARTER_FINAL,
            SEMI_FINAL,
            FINAL
        ]

    if player_count >= 4:
        return [
            SEMI_FINAL,
            FINAL
        ]

    return [
        FINAL
    ]


def next_round_name(current_round, rounds):
    """
    Find the next round after the current round.
    """

    if current_round not in rounds:
        return None

    index = rounds.index(current_round)

    if index >= len(rounds) - 1:
        return None

    return rounds[index + 1]


def get_player_match(player_id):
    """
    Return the player's current active tournament match.

    Active matches are preferred over completed matches.
    If there is no active match, the most recent match is returned.
    """

    active_match = Match.query.filter(
        or_(
            Match.player1_id == player_id,
            Match.player2_id == player_id
        ),
        Match.status.in_([
            MATCH_SCHEDULED,
            MATCH_IN_PROGRESS,
            MATCH_LIVE
        ])
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


def create_next_round_match(tournament, finished_match):
    """
    Create the next-round match when both feeder matches
    have completed and both winners are known.
    """

    if not finished_match.winner_id:
        return None

    # Get all matches belonging to this round.
    round_matches = Match.query.filter_by(
        tournament_id=tournament.id,
        round_name=finished_match.round_name
    ).order_by(
        Match.id.asc()
    ).all()

    # Find the completed match's position.
    current_index = next(
        (
            index
            for index, current_match in enumerate(round_matches)
            if current_match.id == finished_match.id
        ),
        None
    )

    if current_index is None:
        return None

    # Pair matches like:
    # Match 1 + Match 2 -> next round
    # Match 3 + Match 4 -> next round
    # etc.
    if current_index % 2 == 0:
        sibling_index = current_index + 1
    else:
        sibling_index = current_index - 1

    if sibling_index < 0 or sibling_index >= len(round_matches):
        return None

    sibling_match = round_matches[sibling_index]

    # Both feeder matches must have winners.
    if not sibling_match.winner_id:
        return None

    # Determine the tournament's original player count.
    # The first generated round contains half the players.
    all_matches = Match.query.filter_by(
        tournament_id=tournament.id
    ).order_by(
        Match.id.asc()
    ).all()

    if not all_matches:
        return None

    first_round_name = all_matches[0].round_name

    first_round_matches = [
        current_match
        for current_match in all_matches
        if current_match.round_name == first_round_name
    ]

    original_player_count = len(first_round_matches) * 2

    rounds = tournament_rounds(
        original_player_count
    )

    next_round = next_round_name(
        finished_match.round_name,
        rounds
    )

    # The Final has no next round.
    if not next_round:
        return None

    winner_a = finished_match.winner_id
    winner_b = sibling_match.winner_id

    # Prevent duplicate advancement.
    existing_matches = Match.query.filter_by(
        tournament_id=tournament.id,
        round_name=next_round
    ).all()

    for existing_match in existing_matches:

        if {
            existing_match.player1_id,
            existing_match.player2_id
        } == {
            winner_a,
            winner_b
        }:
            return existing_match

    # Create the next-round match.
    next_match = Match(
        tournament_id=tournament.id,
        player1_id=winner_a,
        player2_id=winner_b,
        player1_score=0,
        player2_score=0,
        status=MATCH_SCHEDULED,
        round_name=next_round,
        is_live=False
    )

    db.session.add(next_match)

    return next_match


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

        player = Player(
            name=name,
            fc_username=fc_username,
            country=country,
            squad_ovr=squad_ovr,
            email=email,
            password_hash=generate_password_hash(password),
            application_status="pending",
            terms_accepted=True,
            terms_version="1.0",
            terms_accepted_at=datetime.utcnow(),
            active=True
        )

        db.session.add(
            player
        )

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
        f"competition_day={tournament.competition_day}, "
        f"final_day={tournament.final_day}"
    )

    tournament.name = name
    tournament.max_players = max_players
    tournament.competition_day = competition_day
    tournament.final_day = final_day

    new_values = (
        f"name={tournament.name}, "
        f"max_players={tournament.max_players}, "
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

    if len(approved_players) < 2:
        return (
            "At least two approved active players "
            "are required."
        ), 400

    if len(approved_players) > tournament.max_players:
        return (
            "There are more approved players than "
            "the tournament player limit."
        ), 400

    if len(approved_players) not in [2, 4, 8, 16]:
        return (
            "The official draw currently requires "
            "2, 4, 8 or 16 approved players."
        ), 400

    random.shuffle(
        approved_players
    )

    if len(approved_players) == 16:
        round_name = ROUND_1
    elif len(approved_players) == 8:
        round_name = QUARTER_FINAL
    elif len(approved_players) == 4:
        round_name = SEMI_FINAL
    else:
        round_name = FINAL

    for index in range(
        0,
        len(approved_players),
        2
    ):

        player1 = approved_players[index]
        player2 = approved_players[index + 1]

        match = Match(
            tournament_id=tournament.id,
            player1_id=player1.id,
            player2_id=player2.id,
            player1_score=0,
            player2_score=0,
            status=MATCH_SCHEDULED,
            round_name=round_name,
            is_live=False
        )

        db.session.add(match)

    tournament.status = TOURNAMENT_DRAW_RELEASED

    action = AdminAction(
        action="tournament_draw_released",
        notes=(
            f"Founder released the official draw for "
            f"{tournament.name} with "
            f"{len(approved_players)} players."
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

    if action_type not in ["start", "stop", "finish"]:
        return "Invalid live match action.", 400

    old_status = match.status
    old_live = bool(match.is_live)

    if action_type == "start":
        if match.status == MATCH_FINISHED:
            return "A finished match cannot be started again.", 409

        other_live_matches = Match.query.filter(
            Match.is_live.is_(True),
            Match.id != match.id
        ).all()

        for other_match in other_live_matches:
            other_match.is_live = False

        match.is_live = True
        match.status = MATCH_LIVE

        tournament = db.session.get(Tournament,
            match.tournament_id
        )

        if (
            tournament
            and tournament.status == TOURNAMENT_DRAW_RELEASED
        ):
            tournament.status = TOURNAMENT_IN_PROGRESS

        if not match.started_at:
            match.started_at = datetime.utcnow()

    elif action_type == "stop":
        match.is_live = False

        if match.status == MATCH_LIVE:
            match.status = MATCH_SCHEDULED

    elif action_type == "finish":
        if match.status == MATCH_FINISHED:
            return "This match is already finished.", 409

        try:
            player1_score = int(
                request.form.get("player1_score", "")
            )
            player2_score = int(
                request.form.get("player2_score", "")
            )
        except (TypeError, ValueError):
            return "Both player scores must be valid whole numbers.", 400

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
            loser_id = match.player2_id
        else:
            match.winner_id = match.player2_id
            loser_id = match.player1_id

        match.is_live = False
        match.status = MATCH_FINISHED
        match.finished_at = datetime.utcnow()

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

            stats.matches_played = (
                stats.matches_played or 0
            ) + 1

            if player_id == match.winner_id:
                stats.wins = (stats.wins or 0) + 1
            elif player_id == loser_id:
                stats.losses = (stats.losses or 0) + 1

            if player_id == match.player1_id:
                stats.goals = (
                    stats.goals or 0
                ) + player1_score
            else:
                stats.goals = (
                    stats.goals or 0
                ) + player2_score

        tournament = db.session.get(Tournament,
            match.tournament_id
        )

        if tournament:
            next_match = create_next_round_match(
                tournament,
                match
            )

            # Final completion establishes the official champion.
            if (
                match.round_name == FINAL
                and next_match is None
            ):
                champion = Player.query.get(match.winner_id)
                runner_up = Player.query.get(loser_id)

                if champion:
                    champion.application_status = "champion"

                if runner_up:
                    runner_up.application_status = "runner_up"

                # Prevent duplicate championship records.
                existing_record = Record.query.filter_by(
                    tournament_id=tournament.id,
                    record_type="Tournament Champion"
                ).first()

                if not existing_record and champion:
                    champion_record = Record(
                        record_type="Tournament Champion",
                        record_value=1,
                        player_id=champion.id,
                        tournament_id=tournament.id,
                        achieved_at=datetime.utcnow(),
                        is_current=True
                    )
                    db.session.add(champion_record)

                tournament.status = TOURNAMENT_COMPLETED

    action_labels = {
        "start": "started live match",
        "stop": "stopped live match",
        "finish": "finished match"
    }

    action = AdminAction(
        action="founder_live_match_control",
        notes=(
            f"Founder {action_labels[action_type]}. "
            f"Match ID: {match.id}. "
            f"Previous status: {old_status}. "
            f"New status: {match.status}. "
            f"Previous live state: {old_live}. "
            f"New live state: {bool(match.is_live)}."
        ),
        created_at=datetime.utcnow()
    )

    db.session.add(action)
    db.session.commit()

    return redirect(
        url_for("admin_dashboard")
    )


# ============================================================
# DATABASE
# ============================================================

with app.app_context():

    db.create_all()


# ============================================================
# START SERVER
# ============================================================

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


if __name__ == "__main__":

    app.run(
        debug=True,
        host="0.0.0.0",
        port=5000
    )
