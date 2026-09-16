from datetime import datetime
from pathlib import Path
import os

TEST_DB = Path(
    "/data/data/com.termux/files/home/amination_full_64_test.db"
)

if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from app import app, db, create_next_round_match
from models import Tournament, Player, Match
from constants import (
    TOURNAMENT_IN_PROGRESS,
    MATCH_FINISHED,
)

print("=" * 72)
print("AMINATION ESPORTS — FULL 64-PLAYER ADVANCEMENT TEST")
print("=" * 72)

if not str(app.config["SQLALCHEMY_DATABASE_URI"]).startswith("sqlite:///"):
    raise SystemExit("SAFETY FAILURE: not SQLite.")

if str(TEST_DB) not in str(app.config["SQLALCHEMY_DATABASE_URI"]):
    raise SystemExit("SAFETY FAILURE: wrong database.")

with app.app_context():
    db.drop_all()
    db.create_all()

    tournament = Tournament(
        name="Full 64 Advancement Test",
        status=TOURNAMENT_IN_PROGRESS,
        max_players=64,
    )
    db.session.add(tournament)
    db.session.flush()

    players = []

    for number in range(64):
        player = Player(
            name=f"Player {number + 1}",
            fc_username=f"full64_player_{number + 1}",
            email=f"full64_{number + 1}@example.com",
            application_status="approved",
            terms_accepted=True,
            competent_person_consent_status="not_required",
        )
        db.session.add(player)
        players.append(player)

    db.session.flush()

    print()
    print("Players created:", len(players))

    # Create all Round 1 matches.
    previous_round_matches = []

    for position in range(1, 33):
        index = (position - 1) * 2

        match = Match(
            tournament_id=tournament.id,
            player1_id=players[index].id,
            player2_id=players[index + 1].id,
            player1_score=1,
            player2_score=0,
            status=MATCH_FINISHED,
            round_name="Round 1",
            round_number=1,
            match_number=position,
            bracket_position=position,
            winner_id=players[index].id,
            loser_id=players[index + 1].id,
            finished_at=datetime.utcnow(),
        )

        db.session.add(match)
        previous_round_matches.append(match)

    db.session.flush()

    print("Round 1 matches:", len(previous_round_matches))

    # Advance every round.
    round_names = [
        "Round 1",
        "Round 2",
        "Round 3",
        "Quarter-Final",
        "Semi-Final",
        "Final",
    ]

    for round_index in range(len(round_names) - 1):
        current_round = round_names[round_index]
        next_round = round_names[round_index + 1]

        current_matches = Match.query.filter_by(
            tournament_id=tournament.id,
            round_name=current_round,
        ).order_by(
            Match.bracket_position
        ).all()

        print()
        print(
            f"{current_round}: "
            f"{len(current_matches)} matches → {next_round}"
        )

        expected_next_count = len(current_matches) // 2

        for match in current_matches:
            result = create_next_round_match(
                tournament,
                match,
            )

            assert result is not None

        db.session.flush()

        next_matches = Match.query.filter_by(
            tournament_id=tournament.id,
            round_name=next_round,
        ).order_by(
            Match.bracket_position
        ).all()

        assert len(next_matches) == expected_next_count

        print(
            f"Created {len(next_matches)} {next_round} matches: PASS"
        )

        # Finish every match in this next round so advancement
        # can continue through the complete bracket.
        for match in next_matches:
            assert match.player1_id is not None
            assert match.player2_id is not None

            match.player1_score = 1
            match.player2_score = 0
            match.status = MATCH_FINISHED
            match.winner_id = match.player1_id
            match.loser_id = match.player2_id
            match.finished_at = datetime.utcnow()

        db.session.flush()

    print()
    print("===== FINAL VERIFICATION =====")

    final_matches = Match.query.filter_by(
        tournament_id=tournament.id,
        round_name="Final",
    ).all()

    assert len(final_matches) == 1

    final = final_matches[0]

    assert final.player1_id is not None
    assert final.player2_id is not None

    final.winner_id = final.player1_id
    final.loser_id = final.player2_id
    final.status = MATCH_FINISHED

    db.session.flush()

    further_matches = Match.query.filter(
        Match.tournament_id == tournament.id,
        Match.round_name != "Final",
        Match.bracket_position == final.bracket_position,
    ).all()

    # Final must not create another tournament round.
    result = create_next_round_match(
        tournament,
        final,
    )

    assert result is None

    print("Final exists exactly once: PASS")
    print("Final has two players: PASS")
    print("Final has a champion: PASS")
    print("Final produces no further round: PASS")

    print()
    print("=" * 72)
    print("FULL 64-PLAYER ADVANCEMENT TEST COMPLETE")
    print("=" * 72)
    print()
    print(f"Database: {TEST_DB}")
