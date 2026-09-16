import os
import sys
import tempfile

TEST_DB = os.path.expanduser(
    "~/amination_draw_route_capacity_test.db"
)

if os.path.exists(TEST_DB):
    os.remove(TEST_DB)

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"

sys.path.insert(
    0,
    os.path.dirname(os.path.abspath(__file__))
)

from app import (
    app,
    db,
    Tournament,
    Player,
    TournamentParticipant,
    Match,
    MATCH_FINISHED,
    TOURNAMENT_DRAW_RELEASED,
)


def fail(message):
    print(f"FAIL: {message}")
    raise AssertionError(message)


print("=" * 72)
print("AMINATION ESPORTS — DRAW ROUTE CAPACITY TEST")
print("=" * 72)
print(f"Database: sqlite:///{TEST_DB}")
print("PASS: Dedicated SQLite database only.")

with app.app_context():
    db.drop_all()
    db.create_all()

    tournament = Tournament(
        name="Capacity Draw Test",
        status="registration",
        max_players=64,
        entry_fee=0,
        payment_enabled=False,
        currency="ZAR",
        international_enabled=True,
        season_number=99,
    )

    db.session.add(tournament)
    db.session.flush()

    # Create 57 approved, eligible players for a 64-slot bracket.
    players = []

    for number in range(1, 58):
        player = Player(
            name=f"Test Player {number}",
            fc_username=f"drawtest_{number}",
            email=f"drawtest_{number}@example.com",
            country="South Africa",
            active=True,
            application_status="approved",
            terms_accepted=True,
            terms_version="2.0",
            competent_person_consent_status="not_required",
        )

        db.session.add(player)
        db.session.flush()

        participant = TournamentParticipant(
            tournament_id=tournament.id,
            player_id=player.id,
            status="approved",
        )

        db.session.add(participant)
        players.append(player)

    db.session.commit()

    print()
    print("===== TEST 1 — DRAW 57 PLAYERS INTO 64 SLOTS =====")

    client = app.test_client()

    # Test the route directly inside the isolated application.
    with client.session_transaction() as session:
        session["founder_authenticated"] = True

    response = client.post(
        "/admin/tournament/draw",
        follow_redirects=False,
    )

    print(f"Draw route HTTP status: {response.status_code}")

    if response.status_code not in (200, 302):
        print(response.get_data(as_text=True)[:2000])
        fail("Draw route did not complete successfully.")

    db.session.expire_all()

    tournament = db.session.get(
        Tournament,
        tournament.id
    )

    if tournament.status != TOURNAMENT_DRAW_RELEASED:
        fail(
            f"Unexpected tournament status: "
            f"{tournament.status}"
        )

    round_one = TournamentParticipant.query.filter_by(
        tournament_id=tournament.id,
        status="approved",
    ).count()


    print(f"Approved participants: {round_one}")
    print(f"Configured capacity: {tournament.max_players}")

    all_matches = Match.query.filter_by(tournament_id=tournament.id).all()

    first_round_matches = [
        match
        for match in all_matches
        if match.round_name == "Round 1"
    ]

    bye_matches = [
        match
        for match in first_round_matches
        if match.is_bye
    ]

    normal_matches = [
        match
        for match in first_round_matches
        if not match.is_bye
    ]

    print(f"Round 1 matches: {len(first_round_matches)}")
    print(f"Normal matches: {len(normal_matches)}")
    print(f"BYE matches: {len(bye_matches)}")

    if len(first_round_matches) != 32:
        fail(
            "64-slot bracket must create exactly "
            "32 Round 1 matches."
        )

    if len(bye_matches) != 7:
        fail(
            "57 players in a 64-slot bracket must "
            "create exactly 7 BYE matches."
        )

    if len(normal_matches) != 25:
        fail(
            "57 players in a 64-slot bracket must "
            "create exactly 25 normal matches."
        )

    print("57 players / 64-slot bracket: PASS")

    print()
    print("===== TEST 2 — BYE ADVANCEMENT =====")

    for match in bye_matches:
        if match.status != MATCH_FINISHED:
            fail(
                f"BYE match {match.id} was not automatically finished."
            )

        if not match.winner_id:
            fail(
                f"BYE match {match.id} has no automatic winner."
            )

    db.session.commit()
    db.session.expire_all()


    round_two = [
        match
        for match in all_matches
        if match.round_name == "Round 2"
    ]

    print(f"Round 2 matches after BYE processing: {len(round_two)}")

    if len(round_two) == 0:
        fail(
            "BYE advancement did not create any Round 2 matches."
        )

    bye_advanced = sum(
        1
        for match in round_two
        if match.player1_id in {
            bye.winner_id for bye in bye_matches
        }
        or match.player2_id in {
            bye.winner_id for bye in bye_matches
        }
    )

    print(f"BYE winners advanced: {bye_advanced}")

    if bye_advanced != 7:
        fail(
            "All 7 BYE winners must advance into Round 2."
        )

    print("All 7 BYEs advanced: PASS")

    print()
    print("===== TEST 3 — NO 32-PLAYER ASSUMPTION =====")

    if tournament.max_players != 64:
        fail("Tournament capacity changed unexpectedly.")

    if len(first_round_matches) != tournament.max_players // 2:
        fail(
            "Round 1 match count is not based on configured capacity."
        )

    print("Configured capacity remains 64: PASS")
    print("Round 1 derives from 64-slot capacity: PASS")

    print()
    print("=" * 72)
    print("DRAW ROUTE CAPACITY TEST COMPLETE")
    print("=" * 72)
    print(f"Dedicated test database: {TEST_DB}")
    print("=" * 72)

