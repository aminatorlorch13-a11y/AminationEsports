from datetime import datetime
from pathlib import Path
import os

TEST_DB = Path(
    "/data/data/com.termux/files/home/amination_tbd_advancement_test.db"
)

if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from app import (
    app,
    db,
    tournament_bracket_capacity,
    tournament_rounds,
    create_next_round_match,
)
from models import Tournament, Player, Match
from constants import (
    TOURNAMENT_IN_PROGRESS,
    MATCH_SCHEDULED,
    MATCH_FINISHED,
)


print("=" * 72)
print("AMINATION ESPORTS — TBD ADVANCEMENT TEST")
print("=" * 72)
print(f"Database: sqlite:///{TEST_DB}")

if not str(app.config["SQLALCHEMY_DATABASE_URI"]).startswith("sqlite:///"):
    raise SystemExit("SAFETY FAILURE: test database is not SQLite.")

if str(TEST_DB) not in str(app.config["SQLALCHEMY_DATABASE_URI"]):
    raise SystemExit("SAFETY FAILURE: unexpected database path.")

print("PASS: Dedicated SQLite database only.")

with app.app_context():
    db.drop_all()
    db.create_all()

    print()
    print("===== ONE FEEDER FINISHES FIRST =====")

    tournament = Tournament(
        name="TBD Advancement Test",
        status=TOURNAMENT_IN_PROGRESS,
        max_players=64,
    )
    db.session.add(tournament)
    db.session.flush()

    players = []

    for number in range(1, 5):
        player = Player(
            name=f"Test Player {number}",
            fc_username=f"tbd_test_player_{number}",
            email=f"tbd_test_{number}@example.com",
            application_status="approved",
            terms_accepted=True,
            competent_person_consent_status="not_required",
        )
        db.session.add(player)
        players.append(player)

    db.session.flush()

    # Feeder 1 has finished and has a winner.
    feeder_one = Match(
        tournament_id=tournament.id,
        player1_id=players[0].id,
        player2_id=players[1].id,
        player1_score=3,
        player2_score=1,
        status=MATCH_FINISHED,
        round_name="Round 1",
        round_number=1,
        match_number=1,
        bracket_position=1,
        winner_id=players[0].id,
        loser_id=players[1].id,
        finished_at=datetime.utcnow(),
    )

    # Feeder 2 exists, but has NOT finished yet.
    feeder_two = Match(
        tournament_id=tournament.id,
        player1_id=players[2].id,
        player2_id=players[3].id,
        player1_score=0,
        player2_score=0,
        status=MATCH_SCHEDULED,
        round_name="Round 1",
        round_number=1,
        match_number=2,
        bracket_position=2,
        winner_id=None,
        loser_id=None,
    )

    db.session.add(feeder_one)
    db.session.add(feeder_two)
    db.session.flush()

    next_match = create_next_round_match(
        tournament,
        feeder_one,
    )

    assert next_match is not None
    assert next_match.round_name == "Round 2"
    assert next_match.bracket_position == 1

    assert next_match.player1_id == players[0].id
    assert next_match.player2_id is None

    assert next_match.source_match1_id == feeder_one.id
    assert next_match.source_match2_id == feeder_two.id

    print("One feeder creates next match with TBD opponent: PASS")
    print("Next round:", next_match.round_name)
    print("Next position:", next_match.bracket_position)
    print("Known player:", next_match.player1_id)
    print("TBD player:", next_match.player2_id)
    print("Source feeder 1:", next_match.source_match1_id)
    print("Source feeder 2:", next_match.source_match2_id)

    print()
    print("===== SECOND FEEDER FINISHES =====")

    feeder_two.status = MATCH_FINISHED
    feeder_two.player1_score = 2
    feeder_two.player2_score = 0
    feeder_two.winner_id = players[2].id
    feeder_two.loser_id = players[3].id
    feeder_two.finished_at = datetime.utcnow()

    db.session.flush()

    updated_match = create_next_round_match(
        tournament,
        feeder_two,
    )

    assert updated_match is not None
    assert updated_match.id == next_match.id

    assert updated_match.player1_id == players[0].id
    assert updated_match.player2_id == players[2].id

    assert updated_match.source_match1_id == feeder_one.id
    assert updated_match.source_match2_id == feeder_two.id

    print("Second feeder fills existing TBD slot: PASS")
    print("Player 1:", updated_match.player1_id)
    print("Player 2:", updated_match.player2_id)

    print()
    print("===== IDEMPOTENCY CHECK =====")

    repeated = create_next_round_match(
        tournament,
        feeder_two,
    )

    assert repeated is not None
    assert repeated.id == updated_match.id

    matching_matches = Match.query.filter_by(
        tournament_id=tournament.id,
        round_name="Round 2",
        bracket_position=1,
    ).all()

    assert len(matching_matches) == 1

    print("Repeated advancement does not duplicate match: PASS")

    print()
    print("=" * 72)
    print("TBD ADVANCEMENT TEST COMPLETE")
    print("=" * 72)
    print()
    print(f"Dedicated test database: {TEST_DB}")

