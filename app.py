from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
import random
import secrets
import traceback
from werkzeug.security import generate_password_hash, check_password_hash

from sqlalchemy import func, inspect, or_, text
from sqlalchemy.exc import IntegrityError

from config import Config
from highlight_storage import (
    delete_highlight_video,
    upload_highlight_video,
    validate_highlight_upload,
)
from constants import (
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
    FounderMessage,
    Highlight,
    AnalyticsEvent,
    TournamentEvent,
    HallOfChampion
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

# TEMPORARY PRODUCTION DIAGNOSTIC
# Logs unhandled application exceptions so Render captures
# the exact traceback while diagnosing a live registration failure.
@app.errorhandler(Exception)
def log_unhandled_exception(error):
    from werkzeug.exceptions import HTTPException

    if isinstance(error, HTTPException):
        return error

    from flask import request

    app.logger.exception(
        "UNHANDLED APPLICATION EXCEPTION | method=%s | path=%s",
        request.method,
        request.path,
    )

    return "Internal Server Error", 500



app.config.from_object(Config)

db.init_app(app)

# Secure PayFast payment routes.
from payment_routes import register_payment_routes


register_payment_routes(app)


# ============================================================
# FOUNDER AUTHENTICATION
# ============================================================

def calculate_age(date_of_birth, today=None):
    """Return the participant's age in completed years."""
    if date_of_birth is None:
        raise ValueError("Date of birth is required.")

    if today is None:
        today = datetime.utcnow().date()

    age = today.year - date_of_birth.year

    if (today.month, today.day) < (
        date_of_birth.month,
        date_of_birth.day,
    ):
        age -= 1

    return age


def player_is_tournament_eligible(player):
    """
    Return True when the player's age/competent-person consent
    requirements have been satisfied for tournament participation.

    Allowed states:
    - not_required: participant is 18 or older
    - confirmed: required competent-person consent has been confirmed
    - unknown: legacy Season 1 participant registered before the
      age/consent fields were introduced

    Blocked states:
    - required_pending: required consent has not yet been confirmed
    """

    # Season 1 transition:
    # Existing players may have an unknown consent state because they
    # registered before the new age/consent fields were introduced.
    # Founder approval is the authority for the current Season 1 draw.
    #
    # Season 2 onboarding will enforce the new eligibility requirements
    # before a player can become tournament-eligible.
    return player.competent_person_consent_status in {
        "not_required",
        "confirmed",
        "unknown",
    }


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

def current_tournament():
    """
    Return the authoritative current numbered tournament.

    Season identity is based on season_number, not simply the
    newest database row. Legacy tournaments without a season
    number are intentionally ignored by this helper.
    """
    return (
        Tournament.query
        .filter(Tournament.season_number.isnot(None))
        .order_by(
            Tournament.season_number.desc(),
            Tournament.id.desc()
        )
        .first()
    )


def create_next_tournament_season_if_needed(completed_tournament):
    """
    Create the next numbered tournament after a completed season.

    The operation is idempotent:
    - completed seasons are never reopened;
    - only the authoritative latest numbered season can roll over;
    - an existing newer numbered season prevents duplicates;
    - the new season opens in registration.
    """
    if not completed_tournament:
        return None

    season_number = completed_tournament.season_number
    if season_number is None:
        return None

    latest = current_tournament()

    if not latest or latest.id != completed_tournament.id:
        return None

    if completed_tournament.status != TOURNAMENT_COMPLETED:
        return None

    next_season_number = season_number + 1

    existing_next = (
        Tournament.query
        .filter_by(season_number=next_season_number)
        .order_by(Tournament.id.desc())
        .first()
    )

    if existing_next:
        return existing_next

    newer = (
        Tournament.query
        .filter(
            Tournament.season_number.isnot(None),
            Tournament.season_number > season_number,
        )
        .order_by(
            Tournament.season_number.desc(),
            Tournament.id.desc(),
        )
        .first()
    )

    if newer:
        return newer

    capacity = int(completed_tournament.max_players or 0)

    if capacity < 2 or capacity & (capacity - 1):
        raise ValueError(
            "Cannot automatically create the next season because "
            "the completed tournament capacity is not a power of two."
        )

    new_tournament = Tournament(
        name=f"Amination FC Season {next_season_number}",
        status=TOURNAMENT_REGISTRATION,
        max_players=capacity,
        entry_fee=completed_tournament.entry_fee,
        payment_enabled=completed_tournament.payment_enabled,
        currency=completed_tournament.currency,
        international_enabled=completed_tournament.international_enabled,
        competition_day=completed_tournament.competition_day,
        final_day=completed_tournament.final_day,
        season_number=next_season_number,
        whatsapp_group_link=completed_tournament.whatsapp_group_link,
        live_enabled=False,
        live_provider=None,
        live_embed_url=None,
        live_title=None,
        live_match_id=None,
        payment_instructions=completed_tournament.payment_instructions,
        payment_deadline=None,
        availability_deadline=None,
        completed_at=None,
        champion_id=None,
        runner_up_id=None,
    )

    try:
        with db.session.begin_nested():
            db.session.add(new_tournament)
            db.session.flush()
    except IntegrityError:
        existing_next = (
            Tournament.query
            .filter_by(season_number=next_season_number)
            .order_by(Tournament.id.desc())
            .first()
        )

        if existing_next:
            return existing_next

        raise

    db.session.add(
        AdminAction(
            action="tournament_season_auto_created",
            old_status=completed_tournament.status,
            new_status=new_tournament.status,
            notes=(
                f"System automatically created Season "
                f"{next_season_number} after completion of "
                f"Season {completed_tournament.season_number}. "
                f"Source tournament ID={completed_tournament.id}; "
                f"new tournament ID={new_tournament.id}."
            ),
            created_at=datetime.utcnow(),
        )
    )

    return new_tournament


def tournament_bracket_capacity(player_count):
    """
    Return the smallest power-of-two bracket capacity that can
    contain the requested number of players.

    The tournament engine deliberately has no artificial
    32-player ceiling. Founder-controlled tournament capacity
    is handled separately from the mathematical bracket engine.

    Examples:
        2   -> 2
        3   -> 4
        17  -> 32
        33  -> 64
        100 -> 128
        513 -> 1024
    """

    try:
        player_count = int(player_count or 0)
    except (TypeError, ValueError):
        player_count = 0

    if player_count <= 2:
        return 2

    capacity = 2

    while capacity < player_count:
        capacity *= 2

    return capacity


def tournament_rounds(player_count):
    """
    Build the round sequence dynamically from the bracket capacity.

    The final three stages are always:
        Quarter-Final
        Semi-Final
        Final

    Smaller brackets naturally omit the earlier stages.

    Examples:
        2   -> Final
        4   -> Semi-Final, Final
        8   -> Quarter-Final, Semi-Final, Final
        16  -> Round 1, Quarter-Final, Semi-Final, Final
        32  -> Round 1, Round 2, Quarter-Final, Semi-Final, Final
        64  -> Round 1, Round 2, Round 3, Quarter-Final,
               Semi-Final, Final
    """

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

    # A 16-slot bracket needs one preliminary round before
    # reaching the Quarter-Final. Larger brackets add one
    # additional numbered round for every doubling.
    preliminary_round_count = (
        capacity.bit_length() - 4
    )

    rounds = [
        f"Round {number}"
        for number in range(
            1,
            preliminary_round_count + 1
        )
    ]

    rounds.extend([
        QUARTER_FINAL,
        SEMI_FINAL,
        FINAL
    ])

    return rounds


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


def calculate_bye_positions(player_count, capacity=None):
    if capacity is None:
        capacity = tournament_bracket_capacity(player_count)

    try:
        capacity = int(capacity)
    except (TypeError, ValueError):
        capacity = tournament_bracket_capacity(player_count)

    bye_count = max(capacity - player_count, 0)

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


def build_round_one_slots(players, capacity=None):
    players = list(players or [])
    player_count = len(players)

    if player_count < 2:
        return []

    if capacity is None:
        capacity = tournament_bracket_capacity(player_count)

    if capacity < player_count:
        raise ValueError(
            "Bracket capacity cannot be smaller than "
            "the number of players."
        )

    bye_positions = set(
        calculate_bye_positions(
            player_count,
            capacity
        )
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


def build_round_one_pairings(players, capacity=None):
    slots = build_round_one_slots(
        players,
        capacity
    )
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


def get_player_match(player_id, tournament_id=None):
    """
    Return a player's match from the requested tournament only.

    Player accounts are permanent across seasons, while matches are
    tournament-specific. Current-season profile views must never
    display an older season's match.
    """
    if not player_id:
        return None

    player_filter = or_(
        Match.player1_id == player_id,
        Match.player2_id == player_id
    )

    tournament_filter = (
        [Match.tournament_id == tournament_id]
        if tournament_id is not None
        else []
    )

    active_match = Match.query.filter(
        player_filter,
        Match.status.in_(
            [
                MATCH_SCHEDULED,
                MATCH_IN_PROGRESS,
                MATCH_LIVE
            ]
        ),
        *tournament_filter
    ).order_by(
        Match.id.desc()
    ).first()

    if active_match:
        return active_match

    return Match.query.filter(
        player_filter,
        *tournament_filter
    ).order_by(
        Match.id.desc()
    ).first()


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
# ============================================================
# PRIVACY-CONSCIOUS WEBSITE ANALYTICS
# ============================================================

ANALYTICS_PUBLIC_ENDPOINTS = {
    "home",
    "tournament",
    "live",
    "standings",
    "players",
    "player_profile",
    "matches",
    "terms",
    "privacy",
}


def get_analytics_visitor_id():
    """Return the anonymous visitor ID for the current request."""

    visitor_id = request.cookies.get(
        "amination_visitor_id",
        ""
    ).strip()

    if visitor_id and len(visitor_id) <= 64:
        return visitor_id

    return secrets.token_urlsafe(32)


@app.before_request
def prepare_analytics_visit():
    """Prepare anonymous visitor tracking for public pages."""

    if request.method != "GET":
        return

    if request.endpoint not in ANALYTICS_PUBLIC_ENDPOINTS:
        return

    visitor_id = get_analytics_visitor_id()

    g.analytics_visitor_id = visitor_id
    g.analytics_track_visit = True


@app.after_request
def record_analytics_visit(response):
    """Record a privacy-conscious public website visit."""

    if not getattr(g, "analytics_track_visit", False):
        return response

    visitor_id = getattr(
        g,
        "analytics_visitor_id",
        ""
    )

    if not visitor_id:
        return response

    try:
        now = datetime.utcnow()

        recent_visit = (
            AnalyticsEvent.query
            .filter_by(
                visitor_id=visitor_id,
                event_type="visit"
            )
            .order_by(
                AnalyticsEvent.occurred_at.desc()
            )
            .first()
        )

        should_record = (
            recent_visit is None
            or (
                now - recent_visit.occurred_at
            ).total_seconds() >= 1800
        )

        if should_record:
            analytics_event = AnalyticsEvent(
                visitor_id=visitor_id,
                event_type="visit",
                path=request.path,
                occurred_at=now
            )

            db.session.add(analytics_event)
            db.session.commit()

    except Exception:
        db.session.rollback()
        app.logger.exception(
            "Website analytics visit recording failed."
        )

    if request.cookies.get("amination_visitor_id") != visitor_id:
        response.set_cookie(
            "amination_visitor_id",
            visitor_id,
            max_age=31536000,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure
        )

    return response


# ============================================================
# HIGHLIGHT VIEW ANALYTICS
# ============================================================

@app.route("/analytics/highlight-view", methods=["POST"])
def record_highlight_view():
    """Record an anonymous view of a published highlight."""

    visitor_id = request.cookies.get(
        "amination_visitor_id",
        ""
    ).strip()

    if not visitor_id or len(visitor_id) > 64:
        return {
            "success": False,
            "error": "Visitor identifier is required."
        }, 400

    if not request.is_json:
        return {
            "success": False,
            "error": "JSON request required."
        }, 415

    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return {
            "success": False,
            "error": "Invalid request body."
        }, 400

    highlight_id_raw = payload.get("highlight_id")

    if isinstance(highlight_id_raw, bool):
        return {
            "success": False,
            "error": "Invalid highlight ID."
        }, 400

    try:
        highlight_id = int(highlight_id_raw)
    except (TypeError, ValueError):
        return {
            "success": False,
            "error": "Invalid highlight ID."
        }, 400

    if highlight_id <= 0:
        return {
            "success": False,
            "error": "Invalid highlight ID."
        }, 400

    highlight = db.session.get(
        Highlight,
        highlight_id
    )

    if highlight is None or not highlight.is_published:
        return {
            "success": False,
            "error": "Highlight not found."
        }, 404

    try:
        now = datetime.utcnow()

        recent_view = (
            AnalyticsEvent.query
            .filter_by(
                visitor_id=visitor_id,
                event_type="highlight_view",
                highlight_id=highlight_id
            )
            .order_by(
                AnalyticsEvent.occurred_at.desc()
            )
            .first()
        )

        should_record = (
            recent_view is None
            or (
                now - recent_view.occurred_at
            ).total_seconds() >= 1800
        )

        if not should_record:
            return {
                "success": True,
                "recorded": False
            }

        analytics_event = AnalyticsEvent(
            visitor_id=visitor_id,
            event_type="highlight_view",
            highlight_id=highlight_id,
            path="/",
            occurred_at=now
        )

        db.session.add(analytics_event)
        db.session.commit()

        return {
            "success": True,
            "recorded": True
        }

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Highlight view analytics recording failed."
        )

        return {
            "success": False,
            "error": "Analytics event could not be recorded."
        }, 500


# PUBLIC PAGES
# ============================================================

@app.route("/")
def home():

    highlights = (
        Highlight.query
        .filter_by(is_published=True)
        .order_by(
            Highlight.published_at.desc(),
            Highlight.id.desc()
        )
        .all()
    )

    return render_template(
        "index.html",
        highlights=highlights
    )


@app.route("/tournament")
def tournament():

    tournament = current_tournament()
    matches = []

    if tournament:
        matches = (
            Match.query
            .filter_by(tournament_id=tournament.id)
            .order_by(Match.id.asc())
            .all()
        )

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
        tournament=tournament,
        matches=matches,
        players=players,
        latest_notice=latest_notice
    )


@app.route("/live")
def live():

    # Public live page is driven directly from Founder-controlled
    # Match records. No separate live-state system is created.
    # Only the authoritative current season may appear here.

    tournament = current_tournament()
    live_matches = []

    if tournament:
        live_matches = (
            Match.query
            .filter(
                Match.tournament_id == tournament.id,
                Match.is_live.is_(True)
            )
            .order_by(
                Match.scheduled_time.asc(),
                Match.id.asc()
            )
            .all()
        )

    broadcast_match = None

    if tournament and tournament.live_enabled and tournament.live_match_id:
        broadcast_match = next(
            (
                match
                for match in live_matches
                if match.id == tournament.live_match_id
            ),
            None
        )

    players = {
        player.id: player
        for player in Player.query.all()
    }

    return render_template(
        "live.html",
        tournament=tournament,
        matches=live_matches,
        broadcast_match=broadcast_match,
        players=players
    )


@app.route("/standings")
def standings():
    """
    Public tournament bracket.

    The bracket is driven by the tournament's configured capacity
    and the real Match feeder relationships. No bracket size is
    hard-coded.
    """

    tournament = current_tournament()

    if not tournament:
        return render_template(
            "standings.html",
            tournament=None,
            capacity=0,
            rounds=[],
            bracket_matches={},
            players={}
        )

    capacity = tournament_bracket_capacity(
        tournament.max_players
    )

    round_names = tournament_rounds(capacity)

    matches = (
        Match.query
        .filter_by(tournament_id=tournament.id)
        .order_by(
            Match.round_number.asc(),
            Match.bracket_position.asc(),
            Match.id.asc()
        )
        .all()
    )

    players = {
        player.id: player
        for player in Player.query.all()
    }

    bracket_matches = {
        round_name: []
        for round_name in round_names
    }

    for match in matches:
        bracket_matches.setdefault(
            match.round_name,
            []
        ).append(match)

    return render_template(
        "standings.html",
        tournament=tournament,
        capacity=capacity,
        rounds=round_names,
        bracket_matches=bracket_matches,
        players=players
    )


@app.route(
    "/api/tournaments/<int:tournament_id>/events",
    methods=["GET"]
)
def tournament_events_api(tournament_id):
    """
    Read-only tournament replay event stream.

    This endpoint exposes the authoritative tournament event ledger
    in sequence order. It never creates, updates, or deletes data.
    """

    tournament = db.session.get(
        Tournament,
        tournament_id
    )

    if tournament is None:
        return {
            "success": False,
            "error": "Tournament not found."
        }, 404

    events = (
        TournamentEvent.query
        .filter_by(tournament_id=tournament.id)
        .order_by(
            TournamentEvent.sequence_number.asc()
        )
        .all()
    )

    match_ids = {
        event.match_id
        for event in events
        if event.match_id is not None
    }

    player_ids = {
        event.player_id
        for event in events
        if event.player_id is not None
    }

    matches = {
        match.id: match
        for match in Match.query.filter(
            Match.id.in_(match_ids)
        ).all()
    } if match_ids else {}

    players = {
        player.id: player
        for player in Player.query.filter(
            Player.id.in_(player_ids)
        ).all()
    } if player_ids else {}

    serialized_events = []

    for event in events:

        match = matches.get(event.match_id)
        player = players.get(event.player_id)

        serialized_events.append({
            "id": event.id,
            "sequence_number": event.sequence_number,
            "event_type": event.event_type,
            "created_at": (
                event.created_at.isoformat()
                if event.created_at
                else None
            ),
            "created_by": event.created_by,
            "match": (
                {
                    "id": match.id,
                    "round_name": match.round_name,
                    "round_number": match.round_number,
                    "bracket_position": match.bracket_position,
                    "player1_id": match.player1_id,
                    "player2_id": match.player2_id,
                    "player1_score": match.player1_score,
                    "player2_score": match.player2_score,
                    "winner_id": match.winner_id,
                    "loser_id": match.loser_id,
                    "status": match.status,
                    "is_bye": match.is_bye,
                    "is_forfeit": match.is_forfeit
                }
                if match
                else None
            ),
            "player": (
                {
                    "id": player.id,
                    "name": player.name
                }
                if player
                else None
            ),
            "payload": event.payload or {}
        })

    return {
        "success": True,
        "tournament": {
            "id": tournament.id,
            "name": tournament.name,
            "status": tournament.status,
            "season_number": tournament.season_number,
            "capacity": tournament_bracket_capacity(
                tournament.max_players
            )
        },
        "event_count": len(serialized_events),
        "events": serialized_events
    }, 200


@app.route("/hall-of-fame")
def hall_of_fame():
    """
    Public Champions Vault.

    Hall records are created automatically by the authoritative
    champion-confirmation transaction. This route is read-only:
    it never creates, edits, deletes, or repairs Hall records.
    """
    hall_records = (
        HallOfChampion.query
        .join(Tournament, HallOfChampion.tournament_id == Tournament.id)
        .filter(Tournament.status == TOURNAMENT_COMPLETED)
        .order_by(
            HallOfChampion.season_number.asc(),
            HallOfChampion.id.asc()
        )
        .all()
    )

    champion_ids = {
        record.player_id
        for record in hall_records
        if record.player_id is not None
    }

    runner_up_ids = set()

    tournaments = {}
    if hall_records:
        tournament_ids = {
            record.tournament_id
            for record in hall_records
            if record.tournament_id is not None
        }

        tournaments = {
            tournament.id: tournament
            for tournament in Tournament.query.filter(
                Tournament.id.in_(tournament_ids)
            ).all()
        }

        runner_up_ids = {
            tournament.runner_up_id
            for tournament in tournaments.values()
            if tournament.runner_up_id is not None
        }

    player_ids = champion_ids | runner_up_ids

    players = {}
    if player_ids:
        players = {
            player.id: player
            for player in Player.query.filter(
                Player.id.in_(player_ids)
            ).all()
        }

    return render_template(
        "hall_of_fame.html",
        hall_records=hall_records,
        tournaments=tournaments,
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

    # Player identity is permanent. Tournament participation and
    # matches are specific to the current season.
    tournament = current_tournament()
    season_participant = None

    if tournament:
        season_participant = TournamentParticipant.query.filter_by(
            tournament_id=tournament.id,
            player_id=player.id
        ).first()

    # Player profiles show match/score information only for an active
    # tournament. Completed seasons remain historical records and must
    # never appear as the player's current match.
    player_match = (
        get_player_match(
            player.id,
            tournament.id
        )
        if tournament
        and tournament.status != TOURNAMENT_COMPLETED
        else None
    )

    players = {
        p.id: p
        for p in Player.query.all()
    }

    return render_template(
        "player_profile.html",
        player=player,
        tournament=tournament,
        season_participant=season_participant,
        player_match=player_match,
        players=players
    )

@app.route("/matches")
def matches():

    tournament = current_tournament()
    tournament_matches = []

    if tournament:
        tournament_matches = (
            Match.query
            .filter_by(tournament_id=tournament.id)
            .order_by(Match.id.asc())
            .all()
        )

    players = {
        player.id: player
        for player in Player.query.all()
    }

    return render_template(
        "matches.html",
        tournament=tournament,
        matches=tournament_matches,
        players=players
    )


@app.route("/terms")
def terms():

    return render_template(
        "terms.html"
    )


# ============================================================
@app.route("/privacy")
def privacy():
    return render_template(
        "privacy.html"
    )


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

        date_of_birth_raw = request.form.get(
            "date_of_birth",
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

        if not date_of_birth_raw:
            return "Date of birth is required.", 400

        try:
            date_of_birth = datetime.strptime(
                date_of_birth_raw,
                "%Y-%m-%d"
            ).date()
        except ValueError:
            return "Please enter a valid date of birth.", 400

        today = datetime.utcnow().date()

        if date_of_birth > today:
            return "Date of birth cannot be in the future.", 400

        age = calculate_age(
            date_of_birth,
            today
        )

        if age < 0 or age > 120:
            return "Please enter a valid date of birth.", 400

        competent_person_consent_status = (
            "not_required"
            if age >= 18
            else "required_pending"
        )

        if not email or not password:
            return "Email and password are required.", 400

        if "@" not in email or "." not in email:
            return "Please enter a valid email address.", 400

        if len(password) < 8:
            return "Password must be at least 8 characters long.", 400

        # --------------------------------------------------------
        # Account identity + tournament registration
        # --------------------------------------------------------
        # A Player account is permanent across seasons.
        # TournamentParticipant is season-specific.
        #
        # New player:
        #   create Player + pending TournamentParticipant
        #
        # Returning player:
        #   reuse existing Player + create a NEW pending
        #   TournamentParticipant for this tournament.
        #
        # Security:
        #   an existing account may only be reused when the
        #   submitted email, FC username, and password all
        #   identify the same account.
        existing_email = Player.query.filter_by(
            email=email
        ).first()

        existing_player = Player.query.filter_by(
            fc_username=fc_username
        ).first()

        if existing_email and existing_player:
            if existing_email.id != existing_player.id:
                return (
                    "The email address and FC Mobile username "
                    "belong to different player accounts."
                ), 409

        if existing_email and not existing_player:
            return (
                "This email address is already registered "
                "to another player account."
            ), 409

        if existing_player and not existing_email:
            return (
                "This FC Mobile username is already registered "
                "to another player account."
            ), 409

        returning_player = existing_email or existing_player

        if returning_player:
            if not returning_player.password_hash:
                return (
                    "This player account cannot be reused through "
                    "registration. Please contact Amination Esports."
                ), 409

            if not check_password_hash(
                returning_player.password_hash,
                password
            ):
                return (
                    "The password for this existing player account "
                    "is incorrect."
                ), 401

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

        # --------------------------------------------------------
        # Tournament registration
        # --------------------------------------------------------
        # Registration belongs to the current tournament.
        # Do not create an orphan player account if no tournament exists.
        tournament = current_tournament()

        if tournament and tournament.status != TOURNAMENT_REGISTRATION:
            tournament = None

        if not tournament:
            return (
                "Registration is currently unavailable because "
                "there is no tournament open for registration."
            ), 503

        # Prevent duplicate participation in the same tournament.
        # This check uses player identity rather than global Player
        # approval state, so returning players can register for a
        # new season without overwriting their historical records.
        registration_player = returning_player

        if registration_player:
            existing_participant = (
                TournamentParticipant.query.filter_by(
                    tournament_id=tournament.id,
                    player_id=registration_player.id
                ).first()
            )

            if existing_participant:
                return (
                    "You are already registered for this season. "
                    "Your application is currently "
                    f"{existing_participant.status}."
                ), 409

            player = registration_player

        else:
            player = Player(
                name=name,
                fc_username=fc_username,
                country=country,
                date_of_birth=date_of_birth,
                competent_person_consent_status=(
                    competent_person_consent_status
                ),
                squad_ovr=squad_ovr,
                email=email,
                password_hash=generate_password_hash(password),
                application_status="pending",
                terms_accepted=True,
                terms_version="2.0",
                terms_accepted_at=datetime.utcnow(),
                active=True
            )

        # The participant record is the authoritative seasonal
        # registration record. New applications ALWAYS begin pending.
        try:
            db.session.add(player)

            # Flush so player.id exists before creating the participant.
            # Both records remain inside the same database transaction.
            db.session.flush()

            payment_required = bool(tournament.payment_enabled)

            # --------------------------------------------------------
            # Returning-player priority
            # --------------------------------------------------------
            # Priority comes ONLY from the immediately preceding
            # completed numbered season. It never grants automatic
            # approval or automatic tournament entry.
            priority_type = "none"
            priority_reason = None
            priority_source_tournament_id = None

            if registration_player:
                previous_season = (
                    Tournament.query
                    .filter(
                        Tournament.season_number.isnot(None),
                        Tournament.season_number < tournament.season_number,
                        Tournament.status == TOURNAMENT_COMPLETED,
                    )
                    .order_by(
                        Tournament.season_number.desc(),
                        Tournament.id.desc(),
                    )
                    .first()
                )

                if previous_season:
                    if previous_season.champion_id == registration_player.id:
                        priority_type = "champion"
                        priority_reason = (
                            f"Returning champion from {previous_season.name}."
                        )
                        priority_source_tournament_id = previous_season.id

                    elif previous_season.runner_up_id == registration_player.id:
                        priority_type = "runner_up"
                        priority_reason = (
                            f"Returning runner-up from {previous_season.name}."
                        )
                        priority_source_tournament_id = previous_season.id

            participant = TournamentParticipant(
                tournament_id=tournament.id,
                player_id=player.id,
                team_name=player.team_name,
                status="pending",
                priority_type=priority_type,
                priority_reason=priority_reason,
                priority_source_tournament_id=priority_source_tournament_id,
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

        except Exception:
            db.session.rollback()

            print("REGISTRATION DATABASE ERROR")
            traceback.print_exc()

            return (
                "We couldn't complete your registration right now. "
                "Please try again. If the problem continues, "
                "please contact Amination Esports."
            ), 500

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
# ============================================================
# EXISTING PLAYER — CURRENT TOURNAMENT REGISTRATION
# ============================================================

@app.route("/register-current-tournament")
def register_current_tournament():
    """
    Register an already-authenticated player for the current tournament.

    This is tournament participation, not account creation.
    """
    player_id = session.get("player_id")

    if not player_id:
        return redirect(url_for("player_login"))

    player = Player.query.get(player_id)

    if not player:
        session.pop("player_id", None)
        return redirect(url_for("player_login"))

    tournament = current_tournament()

    # Production bootstrap:
    # If no numbered tournament exists, establish the next real
    # registration season so existing players can register directly
    # from their profile. This is idempotent and only creates Season 2.
    if tournament is None:
        existing_season_two = (
            Tournament.query
            .filter(Tournament.season_number == 2)
            .order_by(Tournament.id.desc())
            .first()
        )

        if existing_season_two is not None:
            tournament = existing_season_two
        else:
            tournament = Tournament(
                name="Amination FC Season 2",
                status=TOURNAMENT_REGISTRATION,
                max_players=DEFAULT_MAX_PLAYERS,
                entry_fee=0,
                payment_enabled=False,
                currency="ZAR",
                international_enabled=True,
                competition_day="Saturday",
                final_day="Sunday",
                season_number=2,
                live_enabled=False,
                live_provider=None,
                live_embed_url=None,
                live_title=None,
                live_match_id=None,
                payment_instructions=None,
                payment_deadline=None,
                availability_deadline=None,
                completed_at=None,
                champion_id=None,
                runner_up_id=None,
            )

            try:
                with db.session.begin_nested():
                    db.session.add(tournament)
                    db.session.flush()
            except IntegrityError:
                db.session.rollback()
                tournament = (
                    Tournament.query
                    .filter(Tournament.season_number == 2)
                    .order_by(Tournament.id.desc())
                    .first()
                )
                if tournament is None:
                    raise

            db.session.commit()

    if tournament.status != TOURNAMENT_REGISTRATION:
        return (
            "Registration is currently closed because "
            "the current tournament is no longer open for registration."
        ), 409

    if tournament.status != TOURNAMENT_REGISTRATION:
        return (
            "Registration is currently closed because "
            "the current tournament is no longer open for registration."
        ), 409

    existing = TournamentParticipant.query.filter_by(
        tournament_id=tournament.id,
        player_id=player.id
    ).first()

    if existing:
        if existing.status == "withdrawn":
            existing.status = "pending"
            existing.registered_at = datetime.utcnow()

            db.session.add(existing)
            db.session.commit()

            return redirect(
                url_for(
                    "player_profile",
                    player_id=player.id
                )
            )

        if existing.status == "pending":
            return (
                "You are already pending approval for the current tournament."
            ), 409

        if existing.status in {
            "approved",
            "waitlist",
            "active",
            "champion",
            "runner_up"
        }:
            return (
                "You are already registered for the current tournament."
            ), 409

        return (
            "You already have a participation record for the current "
            "tournament. Please contact Amination eSports if you need "
            "that record reviewed."
        ), 409

    priority_type = "none"
    priority_reason = None
    priority_source_tournament_id = None

    previous_season = (
        Tournament.query
        .filter(
            Tournament.season_number.isnot(None),
            Tournament.season_number < tournament.season_number,
            Tournament.status == TOURNAMENT_COMPLETED,
        )
        .order_by(
            Tournament.season_number.desc(),
            Tournament.id.desc(),
        )
        .first()
    )

    if previous_season:
        if previous_season.champion_id == player.id:
            priority_type = "champion"
            priority_reason = (
                f"Returning champion from {previous_season.name}."
            )
            priority_source_tournament_id = previous_season.id

        elif previous_season.runner_up_id == player.id:
            priority_type = "runner_up"
            priority_reason = (
                f"Returning runner-up from {previous_season.name}."
            )
            priority_source_tournament_id = previous_season.id

    payment_required = (
        tournament.payment_enabled
        and float(tournament.entry_fee or 0) > 0
    )

    participant = TournamentParticipant(
        tournament_id=tournament.id,
        player_id=player.id,
        team_name=player.team_name,
        status="pending",
        priority_type=priority_type,
        priority_reason=priority_reason,
        priority_source_tournament_id=priority_source_tournament_id,
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
        registered_at=datetime.utcnow(),
    )

    try:
        with db.session.begin_nested():
            db.session.add(participant)
            db.session.flush()
    except IntegrityError:
        existing = TournamentParticipant.query.filter_by(
            tournament_id=tournament.id,
            player_id=player.id
        ).first()

        if existing:
            if existing.status == "pending":
                return (
                    "You are already pending approval for the current tournament."
                ), 409

            if existing.status in {
                "approved",
                "waitlist",
                "active",
                "champion",
                "runner_up"
            }:
                return (
                    "You are already registered for the current tournament."
                ), 409

            return (
                "You already have a participation record for the current "
                "tournament. Please contact Amination eSports if you need "
                "that record reviewed."
            ), 409

        db.session.rollback()
        traceback.print_exc()
        return (
            "We couldn't complete your tournament registration right now. "
            "Please try again. If the problem continues, please contact "
            "Amination eSports."
        ), 500
    except Exception:
        db.session.rollback()
        traceback.print_exc()
        return (
            "We couldn't complete your tournament registration right now. "
            "Please try again. If the problem continues, please contact "
            "Amination eSports."
        ), 500

    db.session.commit()

    return redirect(
        url_for(
            "player_profile",
            player_id=player.id
        )
    )

# REGISTRATION SUCCESS
# ============================================================

@app.route(
    "/registration-success/<int:player_id>"
)
def registration_success(player_id):

    player = Player.query.get_or_404(
        player_id
    )

    tournament = current_tournament()

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
# WEBSITE ANALYTICS OVERVIEW
# ============================================================

ANALYTICS_RANGE_DAYS = {
    "today": 1,
    "7d": 7,
    "30d": 30,
}


def get_analytics_period(range_key):
    """Return the UTC period boundaries for an analytics range."""

    if range_key == "all":
        return None, None

    if range_key not in ANALYTICS_RANGE_DAYS:
        range_key = "7d"

    now = datetime.utcnow()

    if range_key == "today":
        start = datetime(
            now.year,
            now.month,
            now.day
        )
    else:
        start = now - timedelta(
            days=ANALYTICS_RANGE_DAYS[range_key]
        )

    return start, now


def get_website_analytics(range_key="7d"):
    """Build an aggregate, privacy-conscious website analytics snapshot."""

    if range_key not in {"today", "7d", "30d", "all"}:
        range_key = "7d"

    start, end = get_analytics_period(range_key)

    visit_query = AnalyticsEvent.query.filter(
        AnalyticsEvent.event_type == "visit"
    )

    highlight_query = AnalyticsEvent.query.filter(
        AnalyticsEvent.event_type == "highlight_view"
    )

    player_query = Player.query

    registration_query = TournamentParticipant.query

    if start is not None:
        visit_query = visit_query.filter(
            AnalyticsEvent.occurred_at >= start,
            AnalyticsEvent.occurred_at < end
        )

        highlight_query = highlight_query.filter(
            AnalyticsEvent.occurred_at >= start,
            AnalyticsEvent.occurred_at < end
        )

        player_query = player_query.filter(
            Player.registered_at >= start,
            Player.registered_at < end
        )

        registration_query = registration_query.filter(
            TournamentParticipant.registered_at >= start,
            TournamentParticipant.registered_at < end
        )

    total_visitors = (
        visit_query
        .with_entities(
            func.count(
                func.distinct(
                    AnalyticsEvent.visitor_id
                )
            )
        )
        .scalar()
        or 0
    )

    first_visit_subquery = (
        db.session.query(
            AnalyticsEvent.visitor_id,
            func.min(
                AnalyticsEvent.occurred_at
            ).label("first_visit")
        )
        .filter(
            AnalyticsEvent.event_type == "visit"
        )
        .group_by(
            AnalyticsEvent.visitor_id
        )
        .subquery()
    )

    if start is None:
        visitor_visit_counts = (
            db.session.query(
                AnalyticsEvent.visitor_id
            )
            .filter(
                AnalyticsEvent.event_type == "visit"
            )
            .group_by(
                AnalyticsEvent.visitor_id
            )
            .subquery()
        )

        visitor_count_rows = (
            db.session.query(
                AnalyticsEvent.visitor_id,
                func.count(AnalyticsEvent.id).label(
                    "visit_count"
                )
            )
            .filter(
                AnalyticsEvent.event_type == "visit"
            )
            .group_by(
                AnalyticsEvent.visitor_id
            )
            .subquery()
        )

        new_visitors = (
            db.session.query(
                func.count(
                    visitor_count_rows.c.visitor_id
                )
            )
            .filter(
                visitor_count_rows.c.visit_count == 1
            )
            .scalar()
            or 0
        )

        returning_visitors = (
            db.session.query(
                func.count(
                    visitor_count_rows.c.visitor_id
                )
            )
            .filter(
                visitor_count_rows.c.visit_count > 1
            )
            .scalar()
            or 0
        )
    else:
        new_visitors = (
            db.session.query(
                func.count(
                    first_visit_subquery.c.visitor_id
                )
            )
            .filter(
                first_visit_subquery.c.first_visit >= start,
                first_visit_subquery.c.first_visit < end
            )
            .scalar()
            or 0
        )

        returning_visitors = (
            total_visitors - new_visitors
        )

    registered_players = player_query.count()

    tournament_registrations = (
        registration_query.count()
    )

    highlight_views = highlight_query.count()

    return {
        "range": range_key,
        "start": start,
        "end": end,
        "total_visitors": int(total_visitors),
        "new_visitors": int(new_visitors),
        "returning_visitors": int(returning_visitors),
        "registered_players": int(registered_players),
        "tournament_registrations": int(
            tournament_registrations
        ),
        "highlight_views": int(highlight_views),
    }



def get_analytics_trend(range_key="7d"):
    """Return aggregate visitor and highlight-view trend data."""

    if range_key not in {"today", "7d", "30d", "all"}:
        range_key = "7d"

    now = datetime.utcnow()

    if range_key == "today":
        start = datetime(
            now.year,
            now.month,
            now.day
        )

        bucket_type = "hour"
        bucket_count = now.hour + 1

    elif range_key == "7d":
        today_start = datetime(
            now.year,
            now.month,
            now.day
        )

        start = today_start - timedelta(days=6)

        bucket_type = "day"
        bucket_count = 7

    elif range_key == "30d":
        today_start = datetime(
            now.year,
            now.month,
            now.day
        )

        start = today_start - timedelta(days=29)

        bucket_type = "day"
        bucket_count = 30

    else:
        start = None

        bucket_type = "month"

        first_visit = (
            db.session.query(
                func.min(AnalyticsEvent.occurred_at)
            )
            .filter(
                AnalyticsEvent.event_type == "visit"
            )
            .scalar()
        )

        if first_visit is None:
            bucket_count = 1
        else:
            bucket_count = (
                (now.year - first_visit.year) * 12
                + now.month
                - first_visit.month
                + 1
            )

    events_query = AnalyticsEvent.query.filter(
        AnalyticsEvent.event_type.in_(
            ["visit", "highlight_view"]
        )
    )

    if start is not None:
        events_query = events_query.filter(
            AnalyticsEvent.occurred_at >= start,
            AnalyticsEvent.occurred_at <= now
        )

    events = events_query.order_by(
        AnalyticsEvent.occurred_at.asc()
    ).all()

    buckets = {}

    for event in events:
        timestamp = event.occurred_at

        if bucket_type == "hour":
            key = timestamp.strftime("%Y-%m-%d-%H")
        elif bucket_type == "day":
            key = timestamp.strftime("%Y-%m-%d")
        else:
            key = timestamp.strftime("%Y-%m")

        if key not in buckets:
            buckets[key] = {
                "visitors": set(),
                "highlight_views": 0,
            }

        if event.event_type == "visit":
            buckets[key]["visitors"].add(
                event.visitor_id
            )
        elif event.event_type == "highlight_view":
            buckets[key]["highlight_views"] += 1

    labels = []
    visitors = []
    highlight_views = []

    if bucket_type == "hour":
        current = start

        for _ in range(bucket_count):
            key = current.strftime("%Y-%m-%d-%H")

            labels.append(
                current.strftime("%H:%M")
            )

            bucket = buckets.get(
                key,
                {
                    "visitors": set(),
                    "highlight_views": 0,
                }
            )

            visitors.append(
                len(bucket["visitors"])
            )

            highlight_views.append(
                bucket["highlight_views"]
            )

            current += timedelta(hours=1)

    elif bucket_type == "day":
        current = start.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

        for _ in range(bucket_count):
            key = current.strftime("%Y-%m-%d")

            labels.append(
                current.strftime("%d %b")
            )

            bucket = buckets.get(
                key,
                {
                    "visitors": set(),
                    "highlight_views": 0,
                }
            )

            visitors.append(
                len(bucket["visitors"])
            )

            highlight_views.append(
                bucket["highlight_views"]
            )

            current += timedelta(days=1)

    else:
        if events:
            first_month = datetime(
                events[0].occurred_at.year,
                events[0].occurred_at.month,
                1
            )
        else:
            first_month = datetime(
                now.year,
                now.month,
                1
            )

        current = first_month

        for _ in range(bucket_count):
            key = current.strftime("%Y-%m")

            labels.append(
                current.strftime("%b %Y")
            )

            bucket = buckets.get(
                key,
                {
                    "visitors": set(),
                    "highlight_views": 0,
                }
            )

            visitors.append(
                len(bucket["visitors"])
            )

            highlight_views.append(
                bucket["highlight_views"]
            )

            if current.month == 12:
                current = datetime(
                    current.year + 1,
                    1,
                    1
                )
            else:
                current = datetime(
                    current.year,
                    current.month + 1,
                    1
                )

    return {
        "bucket": bucket_type,
        "labels": labels,
        "visitors": visitors,
        "highlight_views": highlight_views,
    }



# ============================================================
# FOUNDER DASHBOARD
# ============================================================

@app.route("/admin/dashboard")

# ============================================================
# TEMP CURRENT REGISTRATION DIAGNOSTIC — READ ONLY
# Remove immediately after production diagnosis.
# ============================================================
@app.route("/founder/diagnostics/current-registration")
def founder_current_registration_diagnostic():
    """
    TEMPORARY READ-ONLY production diagnostic.
    Remove immediately after diagnosis.
    """
    access = founder_required()
    if access:
        return access

    tournament = current_tournament()

    if tournament is None:
        return """
        <h1>Temporary Registration Diagnostic</h1>
        <p><strong>Current tournament:</strong> NONE</p>
        """

    participants = (
        TournamentParticipant.query
        .filter_by(tournament_id=tournament.id)
        .order_by(TournamentParticipant.id.asc())
        .all()
    )

    players = Player.query.order_by(Player.id.asc()).all()

    participant_status_counts = {}
    for participant in participants:
        participant_status_counts[participant.status] = (
            participant_status_counts.get(participant.status, 0) + 1
        )

    player_application_counts = {}
    for player in players:
        status = player.application_status or "none"
        player_application_counts[status] = (
            player_application_counts.get(status, 0) + 1
        )

    rows = []

    for participant in participants:
        player = participant.player

        rows.append(
            f"""
            <tr>
                <td>{participant.id}</td>
                <td>{player.id if player else "NO PLAYER"}</td>
                <td>{player.name if player else "NO PLAYER"}</td>
                <td>{player.fc_username if player else "NO PLAYER"}</td>
                <td>{player.team_name if player else "NO PLAYER"}</td>
                <td>{participant.status}</td>
                <td>{participant.registered_at}</td>
                <td>{player.application_status if player else "NO PLAYER"}</td>
            </tr>
            """
        )

    status_rows = "".join(
        f"<li><strong>{status}:</strong> {count}</li>"
        for status, count in sorted(participant_status_counts.items())
    )

    application_rows = "".join(
        f"<li><strong>{status}:</strong> {count}</li>"
        for status, count in sorted(player_application_counts.items())
    )

    return f"""
    <!doctype html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Temporary Registration Diagnostic</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #111;
                color: #eee;
                padding: 24px;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                margin-top: 20px;
            }}
            th, td {{
                border: 1px solid #555;
                padding: 8px;
                text-align: left;
            }}
            th {{
                background: #222;
            }}
            .summary {{
                margin: 16px 0;
            }}
        </style>
    </head>
    <body>
        <h1>Temporary Registration Diagnostic</h1>

        <div class="summary">
            <p><strong>Current tournament:</strong>
                {tournament.name}
                (ID: {tournament.id},
                season: {tournament.season_number},
                status: {tournament.status})
            </p>

            <p><strong>Total current-tournament applications:</strong>
                {len(participants)}
            </p>

            <p><strong>Total Player records:</strong>
                {len(players)}
            </p>
        </div>

        <h2>Current-Tournament Application Status Counts</h2>
        <ul>
            {status_rows or "<li>None</li>"}
        </ul>

        <h2>Global Player Application Status Counts</h2>
        <ul>
            {application_rows or "<li>None</li>"}
        </ul>

        <h2>Every Current-Tournament Application</h2>

        <table>
            <thead>
                <tr>
                    <th>Participant ID</th>
                    <th>Player ID</th>
                    <th>Name</th>
                    <th>FC Username</th>
                    <th>Team Name</th>
                    <th>Season Status</th>
                    <th>Registered At</th>
                    <th>Global Application Status</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows) or "<tr><td colspan='8'>No applications</td></tr>"}
            </tbody>
        </table>
    </body>
    </html>
    """


def admin_dashboard():

    access = founder_required()
    if access:
        return access

    analytics_range = request.args.get(
        "analytics",
        "7d"
    ).strip().lower()

    if analytics_range not in {
        "today",
        "7d",
        "30d",
        "all"
    }:
        analytics_range = "7d"

    analytics = get_website_analytics(
        analytics_range
    )

    analytics_trend = get_analytics_trend(
        analytics_range
    )

    players = Player.query.order_by(
        Player.registered_at.desc()
    ).all()

    players_by_id = {
        player.id: player
        for player in players
    }


    tournament = current_tournament()

    # Founder dashboard fallback:
    # If no numbered current tournament exists, show the latest
    # tournament so completed/legacy records remain visible.
    if tournament is None:
        tournament = (
            Tournament.query
            .order_by(
                Tournament.id.desc()
            )
            .first()
        )

    # --------------------------------------------------------
    # CURRENT-SEASON PARTICIPATION COUNTS
    # --------------------------------------------------------
    # TournamentParticipant is the authoritative source for
    # season-specific registration state.
    #
    # Player.application_status remains global account state
    # and must never grant entry into the current tournament.
    # --------------------------------------------------------
    approved_count = 0
    pending_count = 0
    waitlist_count = 0
    withdrawn_count = 0

    if tournament:
        participant_counts = dict(
            db.session.query(
                TournamentParticipant.status,
                db.func.count(TournamentParticipant.id)
            )
            .filter(
                TournamentParticipant.tournament_id == tournament.id
            )
            .group_by(TournamentParticipant.status)
            .all()
        )

        approved_count = participant_counts.get("approved", 0)
        pending_count = participant_counts.get("pending", 0)
        waitlist_count = participant_counts.get("waitlist", 0)
        withdrawn_count = participant_counts.get("withdrawn", 0)

    # Global account status remains available separately.
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

    # --------------------------------------------------------
    # CURRENT-SEASON REGISTRATIONS
    # --------------------------------------------------------
    current_participants = []

    if tournament:
        current_participants = (
            TournamentParticipant.query
            .filter_by(tournament_id=tournament.id)
            .order_by(TournamentParticipant.id.desc())
            .all()
        )

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
        current_participants=current_participants,
        tournament_matches=tournament_matches,
        matches=tournament_matches,
        analytics=analytics,
        analytics_trend=analytics_trend
    )


# ============================================================
# CURRENT-SEASON PARTICIPANT STATUS
# ============================================================
# This controls participation in the current tournament only.
# It deliberately does NOT modify Player.application_status.
# ============================================================

@app.route(
    "/admin/tournament/participant/<int:participant_id>/status",
    methods=["POST"]
)
def change_tournament_participant_status(participant_id):
    access = founder_required()
    if access:
        return access

    tournament = current_tournament()

    if not tournament:
        return "No tournament exists.", 404

    # Registration decisions are only allowed while registration
    # is open. Once the draw is released, the field is locked.
    if tournament.status != TOURNAMENT_REGISTRATION:
        return (
            "Current-season participant approval is closed because "
            "registration is no longer open."
        ), 409

    participant = TournamentParticipant.query.filter_by(
        id=participant_id,
        tournament_id=tournament.id
    ).first()

    if not participant:
        return (
            "That participant does not belong to the current tournament."
        ), 404

    new_status = request.form.get(
        "status",
        ""
    ).strip().lower()

    allowed_statuses = {
        "pending",
        "approved",
        "waitlist",
        "withdrawn"
    }

    if new_status not in allowed_statuses:
        return "Invalid tournament participant status.", 400

    old_status = participant.status

    # Approval capacity is calculated ONLY from this tournament's
    # TournamentParticipant records.
    if new_status == "approved" and old_status != "approved":
        approved_count = TournamentParticipant.query.filter_by(
            tournament_id=tournament.id,
            status="approved"
        ).count()

        if approved_count >= tournament.max_players:
            return (
                "The tournament has reached "
                f"{tournament.max_players} approved participants. "
                "Use the waitlist until a place becomes available."
            ), 400

    participant.status = new_status

    action = AdminAction(
        player_id=participant.player_id,
        action="tournament_participant_status_changed",
        old_status=old_status,
        new_status=new_status,
        notes=(
            f"Founder changed current tournament participant "
            f"{participant.player_id} from {old_status} to "
            f"{new_status}. Tournament ID={tournament.id}, "
            f"season={tournament.season_number}."
        ),
        created_at=datetime.utcnow()
    )

    db.session.add(action)
    db.session.commit()

    return redirect(
        url_for("admin_dashboard")
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

    tournament = Tournament.query.order_by(
        Tournament.id.desc()
    ).first()

    if not tournament:
        return "No tournament exists.", 404

    if new_status == "approved":

        approved_count = Player.query.filter_by(
            application_status="approved"
        ).count()

        if (
            old_status != "approved"
            and approved_count >= tournament.max_players
        ):

            return (
                "The tournament has reached "
                f"{tournament.max_players} approved players. "
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
    "/admin/tournament/capacity",
    methods=["POST"]
)
def founder_tournament_capacity():
    """
    Founder-controlled capacity for the CURRENT tournament only.

    Capacity may increase or decrease while registration is open.
    The capacity cannot fall below the current approved field.
    Once the official draw is released, capacity is locked.
    """
    access = founder_required()
    if access:
        return access

    tournament = current_tournament()

    if not tournament:
        return "No tournament exists.", 404

    if tournament.status != TOURNAMENT_REGISTRATION:
        return (
            "Tournament capacity is locked after the official draw "
            "has been released."
        ), 409

    max_players_raw = request.form.get(
        "max_players",
        ""
    ).strip()

    if not max_players_raw.isdigit():
        return "Maximum players must be a whole number.", 400

    max_players = int(max_players_raw)

    if max_players < 2 or max_players & (max_players - 1):
        return (
            "Tournament capacity must be a power of two "
            "starting at 2."
        ), 400

    approved_count = (
        TournamentParticipant.query
        .filter_by(
            tournament_id=tournament.id,
            status="approved"
        )
        .count()
    )

    if max_players < approved_count:
        return (
            "Tournament capacity cannot be reduced below the "
            f"current approved field of {approved_count} players."
        ), 409

    if max_players == tournament.max_players:
        return redirect(url_for("admin_dashboard"))

    old_capacity = tournament.max_players
    tournament.max_players = max_players

    direction = (
        "increased"
        if max_players > old_capacity
        else "decreased"
    )

    action = AdminAction(
        action="tournament_capacity_updated",
        notes=(
            f"Founder {direction} capacity for "
            f"{tournament.name}. "
            f"Previous: {old_capacity}. "
            f"New: {max_players}. "
            f"Approved field at change: {approved_count}."
        ),
        created_at=datetime.utcnow()
    )

    db.session.add(action)
    db.session.commit()

    return redirect(url_for("admin_dashboard"))


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

    if not entry_fee_raw:
        return "Entry fee is required.", 400

    if not entry_fee_raw.isdigit():
        return "Entry fee must be a whole number.", 400

    entry_fee = int(entry_fee_raw)

    if entry_fee < 0:
        return "Entry fee cannot be negative.", 400

    # Capacity is deliberately NOT handled by this route.
    # /admin/tournament/capacity is the single authoritative
    # capacity-control endpoint.
    if not competition_day:
        return "Competition day is required.", 400

    if not final_day:
        return "Final day is required.", 400

    old_values = (
        f"name={tournament.name}, "
        f"entry_fee={tournament.entry_fee}, "
        f"competition_day={tournament.competition_day}, "
        f"final_day={tournament.final_day}"
    )

    tournament.name = name
    tournament.entry_fee = entry_fee
    tournament.competition_day = competition_day
    tournament.final_day = final_day

    new_values = (
        f"name={tournament.name}, "
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

def record_tournament_event(
    tournament_id,
    event_type,
    match_id=None,
    player_id=None,
    payload=None,
    created_by=None
):
    """
    Append one immutable event to a tournament's event ledger.

    The caller owns the surrounding transaction.
    This helper NEVER commits independently.

    Sequence numbers are scoped to the tournament.
    """

    last_sequence = (
        db.session.query(
            db.func.max(
                TournamentEvent.sequence_number
            )
        )
        .filter(
            TournamentEvent.tournament_id == tournament_id
        )
        .scalar()
    )

    next_sequence = (
        int(last_sequence)
        if last_sequence is not None
        else 0
    ) + 1

    event = TournamentEvent(
        tournament_id=tournament_id,
        match_id=match_id,
        player_id=player_id,
        event_type=event_type,
        sequence_number=next_sequence,
        payload=payload,
        created_at=datetime.utcnow(),
        created_by=created_by
    )

    db.session.add(event)
    db.session.flush()

    return event


@app.route(
    "/admin/tournament/draw",
    methods=["POST"]
)
def draw_tournament():
    """
    Release the official tournament draw.

    The complete bracket tree is created at draw time.

    Important:
    - Capacity is controlled by Tournament.max_players.
    - No bracket size is hard-coded.
    - Every round exists immediately after the draw.
    - Later-round player slots remain TBD until feeder matches
      produce winners.
    - source_match1_id/source_match2_id permanently describe the
      bracket structure.
    - BYEs are resolved immediately and their winners are placed
      into the correct downstream slots.
    """

    access = founder_required()
    if access:
        return access

    tournament = (
        Tournament.query
        .order_by(Tournament.id.desc())
        .first()
    )

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

    # ------------------------------------------------------------
    # SEASON-SCOPED APPROVED FIELD
    #
    # Global Player.application_status is NOT tournament entry.
    # A player must have an approved TournamentParticipant record
    # belonging to THIS tournament.
    # ------------------------------------------------------------
    approved_players = (
        db.session.query(Player)
        .join(
            TournamentParticipant,
            TournamentParticipant.player_id == Player.id
        )
        .filter(
            TournamentParticipant.tournament_id == tournament.id,
            TournamentParticipant.status == "approved",
            Player.active.is_(True)
        )
        .order_by(TournamentParticipant.id.asc())
        .all()
    )

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

    try:
        capacity = tournament.max_players
        rounds = tournament_rounds(capacity)
    except ValueError as exc:
        return str(exc), 400

    random.shuffle(approved_players)

    pairings = build_round_one_pairings(
        approved_players,
        capacity
    )

    if not pairings:
        return "Unable to build the tournament bracket.", 500

    # ------------------------------------------------------------
    # PHASE 1
    # Create EVERY Round 1 match.
    # ------------------------------------------------------------

    first_round = rounds[0]
    first_round_matches = []

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
        first_round_matches.append(match)

    db.session.flush()

    # ------------------------------------------------------------
    # PHASE 2
    # Create the ENTIRE remaining tree.
    #
    # Every downstream match exists immediately.
    # Its feeder IDs point to the two matches that feed it.
    # Player IDs remain NULL until winners are known.
    # ------------------------------------------------------------

    previous_round_matches = (
        sorted(
            first_round_matches,
            key=lambda match: int(
                match.bracket_position or 0
            )
        )
    )

    for round_index in range(1, len(rounds)):

        round_name = rounds[round_index]
        round_number = round_index + 1

        current_round_matches = []

        for position in range(
            1,
            (capacity // (2 ** (round_index + 1))) + 1
        ):

            source_position_1 = (
                (position * 2) - 1
            )

            source_position_2 = (
                position * 2
            )

            source_match1 = next(
                (
                    match
                    for match in previous_round_matches
                    if int(match.bracket_position or 0)
                    == source_position_1
                ),
                None
            )

            source_match2 = next(
                (
                    match
                    for match in previous_round_matches
                    if int(match.bracket_position or 0)
                    == source_position_2
                ),
                None
            )

            if source_match1 is None:
                return (
                    "Bracket construction error: "
                    + round_name
                    + " is missing feeder "
                    + str(source_position_1)
                    + "."
                ), 500

            if source_match2 is None:
                return (
                    "Bracket construction error: "
                    + round_name
                    + " is missing feeder "
                    + str(source_position_2)
                    + "."
                ), 500

            match = Match(
                tournament_id=tournament.id,

                player1_id=None,
                player2_id=None,

                player1_score=0,
                player2_score=0,

                status=MATCH_SCHEDULED,

                round_name=round_name,
                round_number=round_number,

                match_number=position,
                bracket_position=position,

                source_match1_id=source_match1.id,
                source_match2_id=source_match2.id,

                is_bye=False,
                bye_reason=None,

                is_forfeit=False,
                forfeit_player_id=None,
                forfeit_reason=None,

                winner_id=None,
                loser_id=None,

                is_live=False
            )

            db.session.add(match)
            current_round_matches.append(match)

        db.session.flush()

        previous_round_matches = current_round_matches

    # ------------------------------------------------------------
    # PHASE 3
    # Resolve Round 1 BYEs against the already-created tree.
    #
    # The complete tree already exists, so a BYE only fills the
    # appropriate player slot in its existing Round 2 feeder.
    # ------------------------------------------------------------

    if len(rounds) > 1:
        next_round = rounds[1]

        for match in first_round_matches:

            if not match.is_bye:
                continue

            if not match.winner_id:
                continue

            current_position = int(
                match.bracket_position
            )

            next_position = (
                ((current_position - 1) // 2) + 1
            )

            next_match = Match.query.filter_by(
                tournament_id=tournament.id,
                round_name=next_round,
                bracket_position=next_position
            ).first()

            if next_match is None:
                return (
                    "Bracket construction error: "
                    "BYE destination was not created."
                ), 500

            if current_position % 2 == 1:
                next_match.player1_id = match.winner_id
            else:
                next_match.player2_id = match.winner_id

    # ------------------------------------------------------------
    # Official draw release.
    # ------------------------------------------------------------

    tournament.status = TOURNAMENT_DRAW_RELEASED

    record_tournament_event(
        tournament_id=tournament.id,
        event_type="TOURNAMENT_DRAW",
        payload={
            "capacity": capacity,
            "approved_player_count": player_count,
            "rounds": rounds,
            "match_count": capacity - 1,
            "bye_count": max(capacity - player_count, 0)
        },
        created_by="Founder"
    )

    action = AdminAction(
        action="tournament_draw_released",
        notes=(
            "Founder released the official V2 draw "
            + tournament.name
            + " with "
            + str(player_count)
            + " players in a "
            + str(capacity)
            + "-slot bracket. "
            + "Rounds: "
            + str(len(rounds))
            + ". Matches created: "
            + str(capacity - 1)
            + ". BYEs: "
            + str(max(capacity - player_count, 0))
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

    tournament = Tournament.query.order_by(
        Tournament.id.desc()
    ).first()

    if not tournament:
        return "No tournament exists.", 404

    # Do not allow more approved players than the
    # current tournament capacity.
    # A player already approved does not consume another slot.
    if new_status == "approved" and old_status != "approved":
        approved_count = Player.query.filter_by(
            application_status="approved",
            active=True
        ).count()

        if approved_count >= tournament.max_players:
            return (
                "The tournament has reached "
                f"{tournament.max_players} approved players. "
                "Use the waitlist until a place "
                "becomes available."
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
# FOUNDER — CREATE HIGHLIGHT
# ============================================================
# ============================================================
# FOUNDER — UPLOAD HIGHLIGHT VIDEO
# ============================================================

@app.route(
    "/admin/founder/highlights/upload",
    methods=["POST"]
)
def founder_upload_highlight_video():
    access = founder_required()
    if access:
        return access

    uploaded_file = request.files.get("video")

    if uploaded_file is None:
        return {
            "success": False,
            "error": "Video file is required."
        }, 400

    if not uploaded_file.filename:
        return {
            "success": False,
            "error": "Uploaded video file has no filename."
        }, 400

    filename = uploaded_file.filename.strip()
    extension = Path(filename).suffix.lower()

    if extension not in Config.ALLOWED_HIGHLIGHT_VIDEO_EXTENSIONS:
        return {
            "success": False,
            "error": "Unsupported video file type."
        }, 400

    try:
        upload_result = upload_highlight_video(
            uploaded_file
        )

    except Exception:
        app.logger.exception(
            "Founder highlight video upload failed."
        )

        return {
            "success": False,
            "error": "Video upload failed."
        }, 502

    is_valid, validation_error = validate_highlight_upload(
        upload_result
    )

    if not is_valid:
        public_id = (
            upload_result.get("public_id")
            if isinstance(upload_result, dict)
            else None
        )

        if public_id:
            try:
                delete_highlight_video(public_id)

            except Exception:
                app.logger.exception(
                    "Failed to clean up invalid highlight video "
                    "from Cloudinary."
                )

        app.logger.warning(
            "Rejected uploaded highlight video: %s",
            validation_error
        )

        return {
            "success": False,
            "error": "Uploaded video failed validation."
        }, 400

    secure_url = upload_result["secure_url"]

    return {
        "success": True,
        "video_url": secure_url
    }, 201



@app.route(
    "/admin/founder/highlights/create",
    methods=["POST"]
)
def founder_create_highlight():
    access = founder_required()
    if access:
        return access

    title = request.form.get(
        "title",
        ""
    ).strip()

    player_id_raw = request.form.get(
        "player_id",
        ""
    ).strip()

    tournament_id_raw = request.form.get(
        "tournament_id",
        ""
    ).strip()

    description = request.form.get(
        "description",
        ""
    ).strip()

    video_url = request.form.get(
        "video_url",
        ""
    ).strip()

    if not title:
        flash(
            "Highlight title is required.",
            "error"
        )
        return redirect(url_for("admin_dashboard"))

    if len(title) > 150:
        flash(
            "Highlight title is too long.",
            "error"
        )
        return redirect(url_for("admin_dashboard"))

    if not video_url:
        flash(
            "Video URL is required.",
            "error"
        )
        return redirect(url_for("admin_dashboard"))

    if len(video_url) > 1000:
        flash(
            "Video URL is too long.",
            "error"
        )
        return redirect(url_for("admin_dashboard"))

    parsed_url = urlparse(video_url)

    if parsed_url.scheme not in ("http", "https"):
        flash(
            "Video URL must use HTTP or HTTPS.",
            "error"
        )
        return redirect(url_for("admin_dashboard"))

    if not parsed_url.netloc:
        flash(
            "Please enter a valid video URL.",
            "error"
        )
        return redirect(url_for("admin_dashboard"))

    if len(description) > 5000:
        flash(
            "Highlight description is too long.",
            "error"
        )
        return redirect(url_for("admin_dashboard"))

    player = None
    tournament = None

    if player_id_raw:
        if not player_id_raw.isdigit():
            flash(
                "Invalid featured player.",
                "error"
            )
            return redirect(url_for("admin_dashboard"))

        player = db.session.get(
            Player,
            int(player_id_raw)
        )

        if player is None:
            flash(
                "Selected featured player does not exist.",
                "error"
            )
            return redirect(url_for("admin_dashboard"))

    if tournament_id_raw:
        if not tournament_id_raw.isdigit():
            flash(
                "Invalid tournament selection.",
                "error"
            )
            return redirect(url_for("admin_dashboard"))

        tournament = db.session.get(
            Tournament,
            int(tournament_id_raw)
        )

        if tournament is None:
            flash(
                "Selected tournament does not exist.",
                "error"
            )
            return redirect(url_for("admin_dashboard"))

    now = datetime.utcnow()

    highlight = Highlight(
        title=title,
        player_id=player.id if player else None,
        tournament_id=tournament.id if tournament else None,
        description=description or None,
        video_url=video_url,
        is_published=True,
        published_at=now,
        created_at=now
    )

    db.session.add(highlight)

    try:
        db.session.flush()

        action = AdminAction(
            action="highlight_published",
            notes=(
                f"Founder published highlight "
                f"#{highlight.id}: {title}"
            ),
            created_at=now
        )

        db.session.add(action)
        db.session.commit()

    except Exception:
        db.session.rollback()

        flash(
            "The highlight could not be published. "
            "No changes were saved.",
            "error"
        )

        return redirect(url_for("admin_dashboard"))

    flash(
        f'Highlight "{title}" published successfully.',
        "success"
    )

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

    tournament = current_tournament()
    if not tournament:
        return "No current tournament exists.", 404

    match = Match.query.filter_by(
        id=match_id,
        tournament_id=tournament.id
    ).first()

    if not match:
        return "That match does not belong to the current tournament.", 404

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

    replacement_participant = (
        TournamentParticipant.query
        .filter_by(
            tournament_id=tournament.id,
            player_id=replacement_player,
            status="approved"
        )
        .join(
            Player,
            TournamentParticipant.player_id == Player.id
        )
        .filter(
            Player.active.is_(True)
        )
        .first()
    )

    if not replacement_participant:
        return (
            "The replacement player is not an approved active "
            "participant in the current tournament."
        ), 400

    replacement = db.session.get(Player, replacement_player)

    # If the replacement player is already in another scheduled match,
    # swap the two players instead of rejecting the operation.
    replacement_match = Match.query.filter(
        Match.tournament_id == tournament.id,
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

    tournament = current_tournament()
    if not tournament:
        return "No current tournament exists.", 404

    match = Match.query.filter_by(
        id=match_id,
        tournament_id=tournament.id
    ).first()

    if not match:
        return "That match does not belong to the current tournament.", 404

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
# OFFICIAL LIVE BROADCAST PROVIDER VALIDATION
# ============================================================

SUPPORTED_LIVE_PROVIDERS = {"youtube", "tiktok"}

YOUTUBE_VIDEO_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"
TIKTOK_POST_ID_PATTERN = r"^[0-9]+$"


def normalize_live_provider_url(provider, raw_url):
    """
    Convert a Founder-supplied official provider URL into a safe,
    provider-specific player URL.

    Never accepts arbitrary iframe destinations.
    Returns (normalized_url, error_message).
    """
    import re

    provider = (provider or "").strip().lower()
    raw_url = (raw_url or "").strip()

    if provider not in SUPPORTED_LIVE_PROVIDERS:
        return None, "Unsupported broadcast provider."

    if not raw_url:
        return None, "A broadcast URL is required."

    parsed = urlparse(raw_url)

    if parsed.scheme.lower() != "https":
        return None, "Broadcast URLs must use HTTPS."

    hostname = (parsed.hostname or "").lower().rstrip(".")

    if provider == "youtube":
        allowed_hosts = {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtu.be",
        }

        if hostname not in allowed_hosts:
            return None, "That is not a supported YouTube URL."

        video_id = None

        if hostname == "youtu.be":
            candidate = parsed.path.strip("/").split("/")[0]
            if candidate:
                video_id = candidate

        elif parsed.path.startswith("/watch"):
            from urllib.parse import parse_qs
            video_id = parse_qs(parsed.query).get("v", [None])[0]

        elif parsed.path.startswith("/live/"):
            parts = parsed.path.split("/")
            if len(parts) >= 3:
                video_id = parts[2]

        elif parsed.path.startswith("/embed/"):
            parts = parsed.path.split("/")
            if len(parts) >= 3:
                video_id = parts[2]

        elif parsed.path.startswith("/shorts/"):
            parts = parsed.path.split("/")
            if len(parts) >= 3:
                video_id = parts[2]

        if not video_id or not re.fullmatch(
            YOUTUBE_VIDEO_ID_PATTERN,
            video_id
        ):
            return None, "Could not identify a valid YouTube video ID."

        return (
            f"https://www.youtube-nocookie.com/embed/{video_id}",
            None,
        )

    if provider == "tiktok":
        allowed_hosts = {
            "tiktok.com",
            "www.tiktok.com",
            "m.tiktok.com",
        }

        if hostname not in allowed_hosts:
            return None, "That is not a supported TikTok URL."

        parts = [part for part in parsed.path.split("/") if part]

        # TikTok's official player supports post/video IDs.
        # We deliberately do not claim that a TikTok LIVE URL is
        # an embeddable live broadcast.
        post_id = None

        if "video" in parts:
            index = parts.index("video")
            if index + 1 < len(parts):
                post_id = parts[index + 1]

        if not post_id or not re.fullmatch(
            TIKTOK_POST_ID_PATTERN,
            post_id
        ):
            return (
                None,
                "That TikTok URL is not an embeddable TikTok video/post."
            )

        return (
            f"https://www.tiktok.com/player/v1/{post_id}",
            None,
        )

    return None, "Unsupported broadcast provider."


def configure_official_live_broadcast(tournament, match, provider, raw_url, title):
    """
    Validate and configure the official broadcast for one real match.

    Returns (success, message).
    Caller owns the transaction.
    """
    if tournament is None:
        return False, "No current tournament exists."

    if match is None:
        return False, "The selected match does not exist."

    if match.tournament_id != tournament.id:
        return False, "That match does not belong to the current tournament."

    if match.is_bye:
        return False, "A BYE match cannot have a live broadcast."

    if match.status == MATCH_FINISHED:
        return False, "A finished match cannot be configured as live."

    if not match.player1_id or not match.player2_id:
        return False, "Both players must be present before broadcasting the match."

    normalized_url, error = normalize_live_provider_url(
        provider,
        raw_url,
    )

    if error:
        return False, error

    title = (title or "").strip()

    if not title:
        title = (
            f"{match.round_name or 'Tournament Match'} "
            f"— Match #{match.match_number or match.id}"
        )

    if len(title) > 200:
        return False, "Broadcast title must be 200 characters or fewer."

    tournament.live_enabled = True
    tournament.live_provider = provider.strip().lower()
    tournament.live_embed_url = normalized_url
    tournament.live_title = title
    tournament.live_match_id = match.id

    return True, "Official broadcast configured successfully."

# ============================================================
# FOUNDER — LIVE MATCH CONTROL
# ============================================================

@app.route(
    "/admin/founder/match/<int:match_id>/broadcast",
    methods=["POST"]
)
def founder_configure_live_broadcast(match_id):
    access = founder_required()
    if access:
        return access

    tournament = current_tournament()

    if not tournament:
        return "No current tournament exists.", 404

    match = Match.query.filter_by(
        id=match_id,
        tournament_id=tournament.id
    ).first()

    if not match:
        return "That match does not belong to the current tournament.", 404

    provider = request.form.get("provider", "").strip().lower()
    broadcast_url = request.form.get("broadcast_url", "").strip()
    title = request.form.get("broadcast_title", "").strip()

    success, message = configure_official_live_broadcast(
        tournament=tournament,
        match=match,
        provider=provider,
        raw_url=broadcast_url,
        title=title,
    )

    if not success:
        return message, 400

    db.session.commit()

    return redirect(url_for("admin_dashboard"))


@app.route(
    "/admin/founder/broadcast/disable",
    methods=["POST"]
)
def founder_disable_live_broadcast():
    access = founder_required()
    if access:
        return access

    tournament = current_tournament()

    if not tournament:
        return "No current tournament exists.", 404

    tournament.live_enabled = False
    tournament.live_provider = None
    tournament.live_embed_url = None
    tournament.live_title = None
    tournament.live_match_id = None

    db.session.commit()

    return redirect(url_for("admin_dashboard"))


@app.route(
    "/admin/founder/match/<int:match_id>/live",
    methods=["POST"]
)
def founder_live_match_control(match_id):

    access = founder_required()
    if access:
        return access

    tournament = current_tournament()
    if not tournament:
        return "No current tournament exists.", 404

    match = Match.query.filter_by(
        id=match_id,
        tournament_id=tournament.id
    ).first()

    if not match:
        return "That match does not belong to the current tournament.", 404

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
            Match.tournament_id == tournament.id,
            Match.is_live.is_(True),
            Match.id != match.id
        ).all()

        for other_match in other_live_matches:
            other_match.is_live = False

            if other_match.status == MATCH_LIVE:
                other_match.status = MATCH_SCHEDULED

            # A broadcast belongs to one authoritative match.
            # Starting another match must never leave the previous
            # match publicly marked as live.
            if tournament.live_match_id == other_match.id:
                tournament.live_enabled = False
                tournament.live_provider = None
                tournament.live_embed_url = None
                tournament.live_title = None
                tournament.live_match_id = None

        match.is_live = True
        match.status = MATCH_LIVE

        if (
            tournament.status == TOURNAMENT_DRAW_RELEASED
        ):
            tournament.status = TOURNAMENT_IN_PROGRESS

        if not match.started_at:
            match.started_at = datetime.utcnow()

        record_tournament_event(
            tournament_id=match.tournament_id,
            event_type="MATCH_STARTED",
            match_id=match.id,
            payload={
                "round_name": match.round_name,
                "round_number": match.round_number,
                "bracket_position": match.bracket_position,
                "player1_id": match.player1_id,
                "player2_id": match.player2_id
            },
            created_by="Founder"
        )

    # ========================================================
    # STOP
    # ========================================================

    elif action_type == "stop":

        if match.status == MATCH_FINISHED:
            return (
                "A finished match cannot be stopped."
            ), 409

        match.is_live = False

        if tournament.live_match_id == match.id:
            tournament.live_enabled = False
            tournament.live_provider = None
            tournament.live_embed_url = None
            tournament.live_title = None
            tournament.live_match_id = None

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

        record_tournament_event(
            tournament_id=match.tournament_id,
            event_type="SCORE_UPDATED",
            match_id=match.id,
            payload={
                "player1_score": match.player1_score,
                "player2_score": match.player2_score,
                "live_minute": match.live_minute,
                "live_period": match.live_period,
                "live_message": match.live_message
            },
            created_by="Founder"
        )

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

        # A completed match cannot remain the public broadcast.
        # The tournament-level broadcast state must follow the
        # authoritative match lifecycle.
        # ====================================================
        # GET TOURNAMENT
        # ====================================================
        tournament = db.session.get(
            Tournament,
            match.tournament_id
        )

        if tournament and tournament.live_match_id == match.id:
            tournament.live_enabled = False
            tournament.live_provider = None
            tournament.live_embed_url = None
            tournament.live_title = None
            tournament.live_match_id = None

        # ====================================================
        # ADVANCE WINNER TO NEXT ROUND
        # ====================================================
        next_match = None

        if tournament:
            next_match = create_next_round_match(
                tournament,
                match
            )

        record_tournament_event(
            tournament_id=match.tournament_id,
            event_type="MATCH_COMPLETED",
            match_id=match.id,
            payload={
                "round_name": match.round_name,
                "round_number": match.round_number,
                "bracket_position": match.bracket_position,
                "player1_id": match.player1_id,
                "player2_id": match.player2_id,
                "player1_score": match.player1_score,
                "player2_score": match.player2_score,
                "winner_id": match.winner_id,
                "loser_id": match.loser_id,
                "next_match_id": (
                    next_match.id
                    if next_match is not None
                    else None
                ),
                "is_final": (
                    match.round_name == "Final"
                )
            },
            created_by="Founder"
        )

        record_tournament_event(
            tournament_id=match.tournament_id,
            event_type="PLAYER_ADVANCED",
            match_id=match.id,
            player_id=match.winner_id,
            payload={
                "winner_id": match.winner_id,
                "from_match_id": match.id,
                "next_match_id": (
                    next_match.id
                    if next_match is not None
                    else None
                ),
                "round_name": match.round_name
            },
            created_by="Founder"
        )

        record_tournament_event(
            tournament_id=match.tournament_id,
            event_type="PLAYER_ELIMINATED",
            match_id=match.id,
            player_id=match.loser_id,
            payload={
                "loser_id": match.loser_id,
                "match_id": match.id,
                "round_name": match.round_name
            },
            created_by="Founder"
        )

        # ====================================================
        # TOURNAMENT COMPLETION — FINAL
        # ====================================================

        # Champion confirmation deliberately happens AFTER the
        # authoritative MATCH_COMPLETED / PLAYER_ADVANCED /
        # PLAYER_ELIMINATED events so replay history is chronological.
        if (
            tournament
            and match.round_name == "Final"
            and match.winner_id
            and match.loser_id
        ):
            confirm_tournament_champion(
                tournament,
                match
            )

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


# ============================================================
# TOURNAMENT — AUTHORITATIVE CHAMPION CONFIRMATION
# ============================================================

def confirm_tournament_champion(tournament, final_match):
    """
    Final championship confirmation.

    This function runs inside the caller's existing transaction.

    It is intentionally idempotent:
    - one CHAMPION_CONFIRMED event per tournament
    - one HallOfChampion record per tournament
    - one automatic championship star per confirmed championship

    The browser ceremony is presentation only. The database state
    established here is authoritative.
    """

    if not tournament:
        return None

    if not final_match:
        return None

    champion_id = final_match.winner_id
    runner_up_id = final_match.loser_id

    if not champion_id or not runner_up_id:
        return None

    from models import (
        HallOfChampion,
        PlayerStatistic,
        TournamentParticipant,
    )

    # --------------------------------------------------------
    # IDEMPOTENCY GUARD
    # --------------------------------------------------------

    existing_event = TournamentEvent.query.filter_by(
        tournament_id=tournament.id,
        event_type="CHAMPION_CONFIRMED"
    ).first()

    existing_hall = HallOfChampion.query.filter_by(
        tournament_id=tournament.id
    ).first()

    # --------------------------------------------------------
    # AUTHORITATIVE TOURNAMENT STATE
    # --------------------------------------------------------

    tournament.status = TOURNAMENT_COMPLETED
    tournament.completed_at = (
        tournament.completed_at or datetime.utcnow()
    )
    tournament.champion_id = champion_id
    tournament.runner_up_id = runner_up_id

    champion = db.session.get(
        Player,
        champion_id
    )

    runner_up = db.session.get(
        Player,
        runner_up_id
    )

    if not champion:
        raise ValueError(
            "Champion player could not be loaded."
        )

    if not runner_up:
        raise ValueError(
            "Runner-up player could not be loaded."
        )

    # --------------------------------------------------------
    # TOURNAMENT-SPECIFIC PARTICIPATION STATE
    # --------------------------------------------------------

    champion_participant = (
        TournamentParticipant.query
        .filter_by(
            tournament_id=tournament.id,
            player_id=champion_id
        )
        .first()
    )

    runner_up_participant = (
        TournamentParticipant.query
        .filter_by(
            tournament_id=tournament.id,
            player_id=runner_up_id
        )
        .first()
    )

    if champion_participant:
        champion_participant.status = "champion"

    if runner_up_participant:
        runner_up_participant.status = "runner_up"

    # --------------------------------------------------------
    # GLOBAL PLAYER STATE
    #
    # Kept for compatibility with the existing V1/V2 system.
    # The historical tournament record remains the authoritative
    # source for Hall of Champions.
    # --------------------------------------------------------

    champion.application_status = "champion"
    runner_up.application_status = "runner_up"

    # --------------------------------------------------------
    # AUTOMATIC CHAMPIONSHIP STAR
    # --------------------------------------------------------

    if not existing_event:
        champion.championship_stars = (
            champion.championship_stars or 0
        ) + 1

    # --------------------------------------------------------
    # PERFORMANCE SNAPSHOT
    # --------------------------------------------------------

    stats = PlayerStatistic.query.filter_by(
        player_id=champion_id
    ).first()

    matches_played = (
        stats.matches_played
        if stats
        else None
    )

    wins = (
        stats.wins
        if stats
        else None
    )

    goals_scored = (
        stats.goals
        if stats
        else None
    )

    # The existing PlayerStatistic model does not currently
    # provide a reliable tournament-scoped goals-conceded field,
    # so do not manufacture one here.
    goals_conceded = None

    team_name = None

    if champion_participant:
        team_name = champion_participant.team_name

    if not team_name:
        team_name = champion.team_name

    final_score = (
        f"{final_match.player1_score} - "
        f"{final_match.player2_score}"
    )

    # --------------------------------------------------------
    # PERMANENT HALL RECORD
    # --------------------------------------------------------

    if not existing_hall:
        existing_hall = HallOfChampion(
            tournament_id=tournament.id,
            player_id=champion_id,
            team_name=team_name,
            season_number=tournament.season_number,
            tournament_name=tournament.name,
            final_score=final_score,
            matches_played=matches_played,
            wins=wins,
            goals_scored=goals_scored,
            goals_conceded=goals_conceded,
            champion_announced_at=datetime.utcnow(),
            created_at=datetime.utcnow()
        )

        db.session.add(existing_hall)

    # --------------------------------------------------------
    # AUTHORITATIVE REPLAY EVENT
    # --------------------------------------------------------

    if not existing_event:
        record_tournament_event(
            tournament_id=tournament.id,
            event_type="CHAMPION_CONFIRMED",
            match_id=final_match.id,
            player_id=champion_id,
            payload={
                "champion_id": champion_id,
                "runner_up_id": runner_up_id,
                "final_match_id": final_match.id,
                "tournament_id": tournament.id,
                "tournament_name": tournament.name,
                "season_number": tournament.season_number,
                "final_score": final_score,
                "championship_star_awarded": True
            },
            created_by="System"
        )

        # Season completion is authoritative. The completed season
        # remains historical and immutable; the next numbered season
        # opens automatically inside this transaction.
        create_next_tournament_season_if_needed(tournament)
    return existing_hall


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
        max_players=32
    )

    db.session.add(tournament)
    db.session.commit()

    return (
        f"Created tournament: {tournament.name} | "
        f"id={tournament.id} | max_players={tournament.max_players} | "
        f"status={tournament.status}"
    )



# ============================================================
# SEASON LIFECYCLE — CREATE NEXT SEASON
# ============================================================

@app.route("/admin/tournament/create-season", methods=["POST"])
def create_next_tournament_season():
    """
    Create the next permanent tournament season.

    A new season is a new Tournament row. Historical tournaments
    are never reset or overwritten.

    Preconditions:
      - Founder authentication is required.
      - A numbered current season must exist.
      - The current season must be completed.
      - Capacity must be a power of two >= 2.
      - Only one non-completed numbered season may exist.

    The new season starts with:
      - registration status
      - no participants
      - no matches
      - no events
      - no champion
      - no runner-up
      - live disabled
    """
    access = founder_required()
    if access:
        return access

    current = current_tournament()

    if not current:
        return (
            "No numbered tournament season exists. "
            "The completed historical season must be established "
            "before a new season can be created."
        ), 409

    if current.status != TOURNAMENT_COMPLETED:
        return (
            "A new season cannot be created until the current "
            f"season ({current.season_number}) is completed."
        ), 409

    # Defensive lifecycle guard. Do not allow multiple active
    # numbered seasons even if a future code path creates one.
    active_seasons = (
        Tournament.query
        .filter(
            Tournament.season_number.isnot(None),
            Tournament.status != TOURNAMENT_COMPLETED
        )
        .count()
    )

    if active_seasons:
        return (
            "An active tournament season already exists. "
            "Complete it before creating another season."
        ), 409

    capacity_raw = request.form.get("max_players", "").strip()

    if not capacity_raw.isdigit():
        return "Maximum players must be a whole number.", 400

    capacity = int(capacity_raw)

    if capacity < 2 or capacity & (capacity - 1):
        return (
            "Tournament capacity must be a power of two "
            "starting at 2."
        ), 400

    requested_name = request.form.get("name", "").strip()

    next_season = (
        db.session.query(
            db.func.max(Tournament.season_number)
        ).scalar()
        or 0
    ) + 1

    name = requested_name or f"Amination FC Season {next_season}"

    # Carry forward only tournament configuration. Lifecycle state
    # is deliberately reset for the new season.
    new_tournament = Tournament(
        name=name,
        status=TOURNAMENT_REGISTRATION,
        max_players=capacity,
        entry_fee=current.entry_fee,
        payment_enabled=current.payment_enabled,
        currency=current.currency,
        international_enabled=current.international_enabled,
        competition_day=current.competition_day,
        final_day=current.final_day,
        season_number=next_season,
        whatsapp_group_link=current.whatsapp_group_link,

        live_enabled=False,
        live_provider=None,
        live_embed_url=None,
        live_title=None,
        live_match_id=None,

        payment_instructions=current.payment_instructions,
        payment_deadline=None,
        availability_deadline=None,

        completed_at=None,
        champion_id=None,
        runner_up_id=None
    )

    db.session.add(new_tournament)
    db.session.flush()

    action = AdminAction(
        action="tournament_season_created",
        old_status=current.status,
        new_status=new_tournament.status,
        notes=(
            f"Founder created Season {next_season} "
            f"({new_tournament.name}) from completed "
            f"Season {current.season_number}. "
            f"Previous tournament ID={current.id}. "
            f"New tournament ID={new_tournament.id}. "
            f"Capacity={capacity}."
        ),
        created_at=datetime.utcnow()
    )

    db.session.add(action)
    db.session.commit()

    return redirect(url_for("admin_dashboard"))


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



# TEMPORARY: production Match schema migration for Season 1 BYE support.

@app.route("/admin/migrate/tournament-event", methods=["GET"])
def migrate_tournament_event():
    """
    Founder-only, additive migration for the tournament_event ledger.

    Safety rules:
    - Creates ONLY the tournament_event table.
    - Refuses to modify an existing table.
    - Does not touch Tournament, Match, Player, or Hall of Champion data.
    """
    access = founder_required()
    if access:
        return access

    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)

    if inspector.has_table("tournament_event"):
        return "tournament_event already exists; no changes made."

    dialect = db.engine.dialect.name

    if dialect == "sqlite":
        db.session.execute(text("""
            CREATE TABLE tournament_event (
                id INTEGER NOT NULL PRIMARY KEY,
                tournament_id INTEGER NOT NULL,
                match_id INTEGER,
                player_id INTEGER,
                event_type VARCHAR(60) NOT NULL,
                sequence_number INTEGER NOT NULL,
                payload JSON,
                created_at DATETIME NOT NULL,
                created_by VARCHAR(100),
                CONSTRAINT uq_tournament_event_sequence
                    UNIQUE (tournament_id, sequence_number),
                FOREIGN KEY(tournament_id) REFERENCES tournament (id),
                FOREIGN KEY(match_id) REFERENCES match (id),
                FOREIGN KEY(player_id) REFERENCES player (id)
            )
        """))

        db.session.execute(text("""
            CREATE INDEX ix_tournament_event_tournament_id
            ON tournament_event (tournament_id)
        """))

        db.session.execute(text("""
            CREATE INDEX ix_tournament_event_match_id
            ON tournament_event (match_id)
        """))

        db.session.execute(text("""
            CREATE INDEX ix_tournament_event_player_id
            ON tournament_event (player_id)
        """))

        db.session.execute(text("""
            CREATE INDEX ix_tournament_event_event_type
            ON tournament_event (event_type)
        """))

        db.session.execute(text("""
            CREATE INDEX ix_tournament_event_created_at
            ON tournament_event (created_at)
        """))

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        return "tournament_event migration complete."

    return f"Unsupported database dialect: {dialect}", 500



# ============================================================
# TEMPORARY: production Tournament schema reconciliation.
# ============================================================
@app.route("/admin/migrate/tournament-schema", methods=["GET"])
def migrate_tournament_schema():
    """
    Founder-only, additive reconciliation for the Tournament model.

    Safety rules:
    - Inspects the existing tournament table first.
    - Adds ONLY columns currently required by the Tournament model.
    - Never drops or alters existing Tournament columns.
    - Never deletes or rewrites Tournament records.
    - Is idempotent: already-present columns are left untouched.
    - Does not create refund_completed_at because that field is no
      longer part of the current Tournament model.
    """
    access = founder_required()
    if access:
        return access

    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)

    if not inspector.has_table("tournament"):
        return {
            "success": False,
            "error": "Tournament table does not exist."
        }, 500

    existing = {
        column["name"]
        for column in inspector.get_columns("tournament")
    }

    dialect = db.engine.dialect.name

    # This migration intentionally targets production PostgreSQL.
    # SQLite cannot safely reproduce the ALTER TABLE + foreign-key
    # constraint operations used below without rebuilding the table.
    if dialect != "postgresql":
        return {
            "success": False,
            "error": (
                f"Unsupported database dialect: {dialect}. "
                "This production schema migration requires PostgreSQL."
            )
        }, 500

    column_sql = {
        "season_number": "INTEGER",
        "whatsapp_group_link": "VARCHAR(500)",
        "live_enabled": "BOOLEAN NOT NULL DEFAULT FALSE",
        "live_provider": "VARCHAR(30)",
        "live_embed_url": "VARCHAR(1000)",
        "live_title": "VARCHAR(200)",
        "live_match_id": "INTEGER",
        "payment_instructions": "TEXT",
        "payment_deadline": "TIMESTAMP",
        "availability_deadline": "TIMESTAMP",
        "created_at": "TIMESTAMP",
        "completed_at": "TIMESTAMP",
        "champion_id": "INTEGER",
        "runner_up_id": "INTEGER",
    }

    added = []
    already_present = []

    try:
        with db.engine.begin() as conn:
            for column_name, column_definition in column_sql.items():
                if column_name in existing:
                    already_present.append(column_name)
                    continue

                conn.execute(
                    text(
                        f"ALTER TABLE tournament "
                        f"ADD COLUMN {column_name} {column_definition}"
                    )
                )
                added.append(column_name)

            # Preserve the current application invariant:
            # every Tournament row must have a usable created_at.
            if "created_at" not in existing:
                conn.execute(
                    text(
                        "UPDATE tournament "
                        "SET created_at = CURRENT_TIMESTAMP "
                        "WHERE created_at IS NULL"
                    )
                )

            # Add foreign-key constraints only when the corresponding
            # column was newly introduced. Existing production constraints
            # are deliberately not modified by this migration.
            if "live_match_id" not in existing:
                conn.execute(
                    text(
                        "ALTER TABLE tournament "
                        "ADD CONSTRAINT fk_tournament_live_match "
                        "FOREIGN KEY (live_match_id) REFERENCES match(id)"
                    )
                )

            if "champion_id" not in existing:
                conn.execute(
                    text(
                        "ALTER TABLE tournament "
                        "ADD CONSTRAINT fk_tournament_champion "
                        "FOREIGN KEY (champion_id) REFERENCES player(id)"
                    )
                )

            if "runner_up_id" not in existing:
                conn.execute(
                    text(
                        "ALTER TABLE tournament "
                        "ADD CONSTRAINT fk_tournament_runner_up "
                        "FOREIGN KEY (runner_up_id) REFERENCES player(id)"
                    )
                )

    except Exception as exc:
        db.session.rollback()
        return {
            "success": False,
            "database": dialect,
            "error": str(exc),
            "added_before_failure": added
        }, 500

    return {
        "success": True,
        "database": dialect,
        "added": added,
        "already_present": already_present,
        "message": (
            "Tournament schema reconciliation complete. "
            "No existing Tournament records were deleted."
        )
    }, 200


# ============================================================
# TEMPORARY: production TournamentParticipant schema reconciliation.
# ============================================================
@app.route("/admin/migrate/tournament-participant-schema", methods=["GET"])
def migrate_tournament_participant_schema():
    """
    Founder-only, additive reconciliation for the TournamentParticipant model.

    Safety rules:
    - Inspects the existing tournament_participant table first.
    - Adds ONLY columns currently required by TournamentParticipant.
    - Never drops or alters existing columns.
    - Never deletes or rewrites participant records.
    - Is idempotent: already-present columns are left untouched.
    - Uses PostgreSQL-only ALTER TABLE operations.
    """
    access = founder_required()
    if access:
        return access

    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)

    if not inspector.has_table("tournament_participant"):
        return {
            "success": False,
            "error": "TournamentParticipant table does not exist."
        }, 500

    existing = {
        column["name"]
        for column in inspector.get_columns("tournament_participant")
    }

    existing_foreign_keys = {
        fk.get("name")
        for fk in inspector.get_foreign_keys("tournament_participant")
        if fk.get("name")
    }

    dialect = db.engine.dialect.name

    if dialect != "postgresql":
        return {
            "success": False,
            "error": (
                f"Unsupported database dialect: {dialect}. "
                "This production schema migration requires PostgreSQL."
            )
        }, 500

    column_sql = {
        "priority_type": "VARCHAR(40) NOT NULL DEFAULT 'none'",
        "priority_reason": "VARCHAR(255)",
        "priority_source_tournament_id": "INTEGER",
        "availability_status": "VARCHAR(30) NOT NULL DEFAULT 'unknown'",
        "availability_confirmed_at": "TIMESTAMP",
        "whatsapp_joined": "BOOLEAN NOT NULL DEFAULT FALSE",
        "whatsapp_joined_at": "TIMESTAMP",
        "payment_status": "VARCHAR(30) NOT NULL DEFAULT 'not_required'",
        "payment_required_amount": "DOUBLE PRECISION DEFAULT 0",
        "payment_received_amount": "DOUBLE PRECISION DEFAULT 0",
        "payment_reference": "VARCHAR(150)",
        "payment_transaction_id": "VARCHAR(255)",
        "payment_provider": "VARCHAR(100)",
        "payment_received_at": "TIMESTAMP",
        "payment_verified_at": "TIMESTAMP",
        "founder_payment_verified": "BOOLEAN NOT NULL DEFAULT FALSE",
        "founder_payment_verified_at": "TIMESTAMP",
        "overpayment_amount": "DOUBLE PRECISION DEFAULT 0",
        "overpayment_reviewed": "BOOLEAN NOT NULL DEFAULT FALSE",
        "payment_reversed": "BOOLEAN NOT NULL DEFAULT FALSE",
        "payment_reversed_at": "TIMESTAMP",
        "payment_reversal_reason": "TEXT",
        "refund_requested": "BOOLEAN NOT NULL DEFAULT FALSE",
        "refund_requested_at": "TIMESTAMP",
        "refund_approved": "BOOLEAN NOT NULL DEFAULT FALSE",
        "refund_approved_at": "TIMESTAMP",
        "refund_amount": "DOUBLE PRECISION DEFAULT 0",
        "refund_completed": "BOOLEAN NOT NULL DEFAULT FALSE",
        "refund_completed_at": "TIMESTAMP",
        "refund_reason": "TEXT",
        "registered_at": "TIMESTAMP",
    }

    added = []
    already_present = []

    try:
        with db.engine.begin() as conn:
            for column_name, column_definition in column_sql.items():
                if column_name in existing:
                    already_present.append(column_name)
                    continue

                conn.execute(
                    text(
                        "ALTER TABLE tournament_participant "
                        f"ADD COLUMN {column_name} {column_definition}"
                    )
                )
                added.append(column_name)

            if (
                "priority_source_tournament_id" not in existing_foreign_keys
                and "priority_source_tournament_id" not in existing
            ):
                conn.execute(
                    text(
                        "ALTER TABLE tournament_participant "
                        "ADD CONSTRAINT "
                        "fk_tournament_participant_priority_source "
                        "FOREIGN KEY (priority_source_tournament_id) "
                        "REFERENCES tournament(id)"
                    )
                )

    except Exception as exc:
        db.session.rollback()
        return {
            "success": False,
            "database": dialect,
            "error": str(exc),
            "added_before_failure": added
        }, 500

    return {
        "success": True,
        "database": dialect,
        "added": added,
        "already_present": already_present,
        "message": (
            "TournamentParticipant schema reconciliation complete. "
            "No existing participant records were deleted or rewritten."
        )
    }, 200


@app.route("/admin/migrate/match-player-nullable", methods=["GET"])
def migrate_match_player_nullable():
    access = founder_required()
    if access:
        return access

    from sqlalchemy import text, inspect

    inspector = inspect(db.engine)
    columns = {
        column["name"]: column
        for column in inspector.get_columns("match")
    }

    results = {}

    with db.engine.begin() as conn:
        for column_name in ("player1_id", "player2_id"):
            if column_name not in columns:
                return {
                    "success": False,
                    "error": f"Missing column: {column_name}"
                }, 500

            if columns[column_name]["nullable"]:
                results[column_name] = "already_nullable"
                continue

            conn.execute(text(
                f"ALTER TABLE match "
                f"ALTER COLUMN {column_name} DROP NOT NULL"
            ))
            results[column_name] = "made_nullable"

    return {
        "success": True,
        "database": db.engine.dialect.name,
        "results": results
    }, 200

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
