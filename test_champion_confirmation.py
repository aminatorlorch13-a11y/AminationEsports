from datetime import datetime
from pathlib import Path
import os

TEST_DB = Path(
    "/data/data/com.termux/files/home/amination_champion_confirmation_test.db"
)

if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["SECRET_KEY"] = "champion-confirmation-test-key"

from app import (
    app,
    db,
    confirm_tournament_champion,
    TOURNAMENT_IN_PROGRESS,
    TOURNAMENT_COMPLETED,
    MATCH_FINISHED,
)
from models import (
    Tournament,
    Player,
    Match,
    TournamentParticipant,
    HallOfChampion,
    TournamentEvent,
)


print("=" * 72)
print("AMINATION ESPORTS — AUTHORITATIVE CHAMPION CONFIRMATION TEST")
print("=" * 72)


# ============================================================
# SAFETY
# ============================================================

uri = str(app.config["SQLALCHEMY_DATABASE_URI"])

print("\n===== SAFETY CHECK =====")
print("Database:", uri)

if not uri.startswith("sqlite:///"):
    raise SystemExit("SAFETY FAILURE: test database is not SQLite.")

if str(TEST_DB) not in uri:
    raise SystemExit("SAFETY FAILURE: wrong SQLite database.")

print("PASS: isolated SQLite database selected.")


with app.app_context():

    db.drop_all()
    db.create_all()

    # ========================================================
    # TEST DATA
    # ========================================================

    print("\n===== TEST 1 — CREATE CHAMPIONSHIP DATA =====")

    ts = int(datetime.utcnow().timestamp())

    champion = Player(
        name="Champion Confirmation Winner",
        fc_username=f"champion_confirmation_{ts}",
        application_status="approved",
        active=True,
        team_name="Amination Test FC",
        championship_stars=0,
    )

    runner_up = Player(
        name="Champion Confirmation Runner Up",
        fc_username=f"runner_confirmation_{ts}",
        application_status="approved",
        active=True,
        team_name="Amination Test United",
        championship_stars=0,
    )

    tournament = Tournament(
        name=f"Champion Confirmation Test {ts}",
        status=TOURNAMENT_IN_PROGRESS,
        max_players=16,
        season_number=1,
    )

    db.session.add_all([
        champion,
        runner_up,
        tournament,
    ])

    db.session.flush()

    champion_participant = TournamentParticipant(
        tournament_id=tournament.id,
        player_id=champion.id,
        team_name="Amination Test FC",
        status="active",
    )

    runner_up_participant = TournamentParticipant(
        tournament_id=tournament.id,
        player_id=runner_up.id,
        team_name="Amination Test United",
        status="active",
    )

    db.session.add_all([
        champion_participant,
        runner_up_participant,
    ])

    db.session.flush()

    final = Match(
        tournament_id=tournament.id,
        player1_id=champion.id,
        player2_id=runner_up.id,
        player1_score=7,
        player2_score=5,
        status=MATCH_FINISHED,
        round_name="Final",
        round_number=4,
        match_number=1,
        bracket_position=1,
        winner_id=champion.id,
        loser_id=runner_up.id,
        is_bye=False,
        is_forfeit=False,
        is_live=False,
        finished_at=datetime.utcnow(),
    )

    db.session.add(final)
    db.session.commit()

    tournament_id = tournament.id
    final_id = final.id
    champion_id = champion.id
    runner_up_id = runner_up.id

    print("Tournament:", tournament_id)
    print("Final:", final_id)
    print("Champion:", champion_id)
    print("Runner-up:", runner_up_id)
    print("DATA SETUP: PASS")


    # ========================================================
    # TEST 2 — FIRST CONFIRMATION
    # ========================================================

    print("\n===== TEST 2 — FIRST CHAMPION CONFIRMATION =====")

    hall = confirm_tournament_champion(
        tournament,
        final,
    )

    db.session.commit()
    db.session.expire_all()

    saved_tournament = db.session.get(
        Tournament,
        tournament_id,
    )

    saved_champion = db.session.get(
        Player,
        champion_id,
    )

    saved_runner_up = db.session.get(
        Player,
        runner_up_id,
    )

    saved_hall = HallOfChampion.query.filter_by(
        tournament_id=tournament_id
    ).first()

    confirmation_events = TournamentEvent.query.filter_by(
        tournament_id=tournament_id,
        event_type="CHAMPION_CONFIRMED",
    ).all()

    print("Tournament status:", saved_tournament.status)
    print("Champion ID:", saved_tournament.champion_id)
    print("Runner-up ID:", saved_tournament.runner_up_id)
    print("Champion stars:", saved_champion.championship_stars)
    print("Runner-up stars:", saved_runner_up.championship_stars)
    print("Hall records:", len([saved_hall]) if saved_hall else 0)
    print("Confirmation events:", len(confirmation_events))

    assert saved_tournament.status == TOURNAMENT_COMPLETED
    assert saved_tournament.champion_id == champion_id
    assert saved_tournament.runner_up_id == runner_up_id

    assert saved_champion.championship_stars == 1
    assert saved_runner_up.championship_stars == 0

    assert saved_hall is not None
    assert saved_hall.tournament_id == tournament_id
    assert saved_hall.player_id == champion_id
    assert saved_hall.tournament_name == tournament.name
    assert saved_hall.season_number == tournament.season_number
    assert saved_hall.final_score == "7 - 5"

    assert len(confirmation_events) == 1

    print("Tournament completion: PASS")
    print("Champion assignment: PASS")
    print("Runner-up assignment: PASS")
    print("Exactly one championship star: PASS")
    print("Hall of Champions record: PASS")
    print("CHAMPION_CONFIRMED event: PASS")


    # ========================================================
    # TEST 3 — PARTICIPATION STATE
    # ========================================================

    print("\n===== TEST 3 — TOURNAMENT PARTICIPATION STATE =====")

    db.session.expire_all()

    saved_champion_participant = TournamentParticipant.query.filter_by(
        tournament_id=tournament_id,
        player_id=champion_id,
    ).first()

    saved_runner_participant = TournamentParticipant.query.filter_by(
        tournament_id=tournament_id,
        player_id=runner_up_id,
    ).first()

    assert saved_champion_participant.status == "champion"
    assert saved_runner_participant.status == "runner_up"

    print("Champion participant state: PASS")
    print("Runner-up participant state: PASS")


    # ========================================================
    # TEST 4 — IDEMPOTENCY ATTACK
    # ========================================================

    print("\n===== TEST 4 — REPEATED CONFIRMATION ATTACK =====")

    confirm_tournament_champion(
        saved_tournament,
        db.session.get(Match, final_id),
    )

    db.session.commit()
    db.session.expire_all()

    attacked_champion = db.session.get(
        Player,
        champion_id,
    )

    attacked_hall = HallOfChampion.query.filter_by(
        tournament_id=tournament_id
    ).all()

    attacked_events = TournamentEvent.query.filter_by(
        tournament_id=tournament_id,
        event_type="CHAMPION_CONFIRMED",
    ).all()

    print("Champion stars after repeat:", attacked_champion.championship_stars)
    print("Hall records after repeat:", len(attacked_hall))
    print("Confirmation events after repeat:", len(attacked_events))

    assert attacked_champion.championship_stars == 1
    assert len(attacked_hall) == 1
    assert len(attacked_events) == 1

    print("Star duplication blocked: PASS")
    print("Hall duplication blocked: PASS")
    print("Event duplication blocked: PASS")


    # ========================================================
    # TEST 5 — HISTORICAL DATA INTEGRITY
    # ========================================================

    print("\n===== TEST 5 — HISTORICAL DATA INTEGRITY =====")

    historical_hall = attacked_hall[0]

    assert historical_hall.player_id == champion_id
    assert historical_hall.tournament_id == tournament_id
    assert historical_hall.season_number == 1
    assert historical_hall.final_score == "7 - 5"
    assert historical_hall.champion_announced_at is not None

    print("Permanent champion identity: PASS")
    print("Permanent tournament identity: PASS")
    print("Season identity: PASS")
    print("Final score snapshot: PASS")
    print("Announcement timestamp: PASS")


    # ========================================================
    # FINAL
    # ========================================================

    print()
    print("=" * 72)
    print("AUTHORITATIVE CHAMPION CONFIRMATION TEST COMPLETE")
    print("=" * 72)
    print("ALL TESTS PASSED")
    print("=" * 72)

    db.session.rollback()
