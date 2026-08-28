from datetime import datetime
from app import (
    app,
    db,
    Tournament,
    Player,
    Match,
    MATCH_FINISHED,
    TOURNAMENT_COMPLETED,
)

print("=" * 60)
print("STEP 8R.46 — COMPLETION DASHBOARD RENDER TEST")
print("=" * 60)

app.config["TESTING"] = True

with app.app_context():

    ts = int(datetime.utcnow().timestamp())

    champion = Player(
        name="8R46 Champion",
        fc_username=f"8r46_champion_{ts}",
        application_status="approved",
        active=True,
    )

    runner_up = Player(
        name="8R46 Runner Up",
        fc_username=f"8r46_runner_{ts}",
        application_status="approved",
        active=True,
    )

    tournament = Tournament(
        name=f"8R46 Dashboard Test {ts}",
        max_players=16,
        status=TOURNAMENT_COMPLETED,
        completed_at=datetime.utcnow(),
    )

    db.session.add_all([
        champion,
        runner_up,
        tournament,
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

    tournament.champion_id = champion.id
    tournament.runner_up_id = runner_up.id

    db.session.add(final)
    db.session.commit()

    tournament_id = tournament.id

    print("===== TEST 1 — TEST DATA CREATED =====")
    print("Tournament ID:", tournament.id)
    print("Champion:", champion.name)
    print("Runner-up:", runner_up.name)
    print("Final score: 7 - 5")
    print("DATA SETUP: PASS")

    client = app.test_client()

    with client.session_transaction() as session:
        session["founder_authenticated"] = True

    response = client.get("/admin/dashboard")

    print("===== TEST 2 — DASHBOARD REQUEST =====")
    print("HTTP status:", response.status_code)

    assert response.status_code == 200

    print("DASHBOARD HTTP 200: PASS")

    body = response.get_data(as_text=True)

    print("===== TEST 3 — COMPLETION UI CONTENT =====")

    required_text = [
        "TOURNAMENT COMPLETED",
        "8R46 Champion",
        "8R46 Runner Up",
        "Final Result",
        "7",
        "5",
        "Champion and runner-up have been permanently recorded",
    ]

    for item in required_text:
        assert item in body
        print(f"FOUND: {item}: PASS")

    print("COMPLETION SUMMARY RENDERED: PASS")

    print("===== TEST 4 — TOURNAMENT DATA =====")

    db.session.expire_all()

    saved = db.session.get(Tournament, tournament_id)

    assert saved.status == TOURNAMENT_COMPLETED
    assert saved.champion_id == champion.id
    assert saved.runner_up_id == runner_up.id
    assert saved.completed_at is not None

    print("STATUS COMPLETED: PASS")
    print("CHAMPION ID PRESENT: PASS")
    print("RUNNER-UP ID PRESENT: PASS")
    print("COMPLETION TIMESTAMP PRESENT: PASS")

    print("=" * 60)
    print("STEP 8R.46 COMPLETE — ALL TESTS PASSED")
    print("=" * 60)

    db.session.rollback()
