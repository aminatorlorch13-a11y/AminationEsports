import os
from datetime import datetime

DB_PATH = "/data/data/com.termux/files/home/amination_capacity_advancement_test.db"

os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["SECRET_KEY"] = "capacity-agnostic-advancement-test"

from app import (
    app,
    db,
    Tournament,
    Player,
    Match,
    MATCH_SCHEDULED,
    MATCH_FINISHED,
    tournament_rounds,
    create_next_round_match,
)


print("=" * 72)
print("AMINATION ESPORTS — CAPACITY-AGNOSTIC ADVANCEMENT TEST")
print("=" * 72)


def make_player(number):
    return Player(
        name=f"Advancement Player {number}",
        fc_username=f"advancement_player_{number}",
        country="South Africa",
        squad_ovr=115,
        email=f"advancement-{number}@example.invalid",
        password_hash="TEST_HASH",
        application_status="approved",
        terms_accepted=True,
        terms_version="1.0",
        active=True,
    )


def make_match(
    tournament,
    round_name,
    position,
    player1_id,
    player2_id,
):
    match = Match(
        tournament_id=tournament.id,
        player1_id=player1_id,
        player2_id=player2_id,
        player1_score=0,
        player2_score=0,
        status=MATCH_FINISHED,
        round_name=round_name,
        round_number=1,
        match_number=position,
        bracket_position=position,
        winner_id=player1_id,
        loser_id=player2_id,
        is_bye=False,
        is_forfeit=False,
        is_live=False,
        finished_at=datetime.utcnow(),
    )

    db.session.add(match)
    db.session.flush()

    return match


def test_single_advancement(tournament_capacity):
    rounds = tournament_rounds(tournament_capacity)

    first_round = rounds[0]
    second_round = rounds[1]

    print()
    print(
        f"===== {tournament_capacity}-SLOT ADVANCEMENT ====="
    )
    print("First round:", first_round)
    print("Second round:", second_round)

    players = [
        make_player(1),
        make_player(2),
        make_player(3),
        make_player(4),
    ]

    db.session.add_all(players)
    db.session.flush()

    # Two completed feeder matches in the first round.
    feeder_one = make_match(
        tournament,
        first_round,
        1,
        players[0].id,
        players[1].id,
    )

    feeder_two = make_match(
        tournament,
        first_round,
        2,
        players[2].id,
        players[3].id,
    )

    # First feeder produces a winner.
    next_match = create_next_round_match(
        tournament,
        feeder_one,
    )

    assert next_match is not None
    assert next_match.round_name == second_round
    assert next_match.bracket_position == 1
    assert next_match.player1_id == players[0].id
    assert next_match.player2_id == players[2].id
    assert next_match.source_match1_id == feeder_one.id
    assert next_match.source_match2_id == feeder_two.id

    print("First feeder creates paired next match: PASS")
    print("Next round position:", next_match.bracket_position)
    print("Player 1:", next_match.player1_id)
    print("Player 2:", next_match.player2_id)

    # Re-processing the second feeder must reuse the same
    # next-round match rather than creating a duplicate.
    feeder_two_result = create_next_round_match(
        tournament,
        feeder_two,
    )

    assert feeder_two_result is not None
    assert feeder_two_result.id == next_match.id
    assert feeder_two_result.player1_id == players[0].id
    assert feeder_two_result.player2_id == players[2].id
    assert feeder_two_result.source_match1_id == feeder_one.id
    assert feeder_two_result.source_match2_id == feeder_two.id

    print("Repeated paired-feeder advancement reuses match: PASS")

    # Idempotency: calling the same advancement again must not
    # create a duplicate next-round match.
    before_count = Match.query.filter_by(
        tournament_id=tournament.id,
        round_name=second_round,
        bracket_position=1,
    ).count()

    duplicate_result = create_next_round_match(
        tournament,
        feeder_one,
    )

    after_count = Match.query.filter_by(
        tournament_id=tournament.id,
        round_name=second_round,
        bracket_position=1,
    ).count()

    assert duplicate_result is not None
    assert duplicate_result.id == next_match.id
    assert before_count == 1
    assert after_count == 1

    print("Repeated advancement does not duplicate match: PASS")


def test_final_stops(tournament):
    rounds = tournament_rounds(tournament.max_players)
    final_round = rounds[-1]

    final_match = Match(
        tournament_id=tournament.id,
        player1_id=1,
        player2_id=2,
        player1_score=3,
        player2_score=1,
        status=MATCH_FINISHED,
        round_name=final_round,
        round_number=len(rounds),
        match_number=1,
        bracket_position=1,
        winner_id=1,
        loser_id=2,
        is_bye=False,
        is_forfeit=False,
        is_live=False,
        finished_at=datetime.utcnow(),
    )

    db.session.add(final_match)
    db.session.commit()

    result = create_next_round_match(
        tournament,
        final_match,
    )

    assert result is None

    matches_after = Match.query.filter_by(
        tournament_id=tournament.id,
    ).count()

    assert matches_after == 1

    print("Final produces no further round: PASS")


with app.app_context():
    uri = app.config["SQLALCHEMY_DATABASE_URI"]

    print("Database:", uri)

    if not uri.startswith("sqlite:"):
        raise SystemExit(
            "SAFETY STOP: TEST IS NOT USING SQLITE"
        )

    if DB_PATH not in uri:
        raise SystemExit(
            "SAFETY STOP: WRONG SQLITE DATABASE"
        )

    print("PASS: Dedicated SQLite database only.")

    db.drop_all()
    db.create_all()

    try:
        tournament = Tournament(
            name="Capacity Advancement Test",
            max_players=64,
            entry_fee=0,
            status="registration",
        )

        db.session.add(tournament)
        db.session.commit()

        # The first two rounds of a 64-slot bracket should be
        # Round 1 -> Round 2.
        test_single_advancement(64)

        db.session.rollback()

        # Rebuild a clean tournament for final protection.
        db.drop_all()
        db.create_all()

        tournament = Tournament(
            name="Capacity Advancement Final Test",
            max_players=64,
            entry_fee=0,
            status="in_progress",
        )

        db.session.add(tournament)
        db.session.commit()

        test_final_stops(tournament)

        print()
        print("=" * 72)
        print("CAPACITY-AGNOSTIC ADVANCEMENT TEST COMPLETE")
        print("=" * 72)

    finally:
        db.session.remove()

print()
print("Dedicated test database:")
print(DB_PATH)
