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
print("STEP 8R.44 — COMPLETION SAFETY TEST")
print("=" * 60)

app.config["TESTING"] = True

with app.app_context():
    ts = int(datetime.utcnow().timestamp())

    champion = Player(
        name="8R44 Champion",
        fc_username=f"8r44_champion_{ts}",
        application_status="approved",
        active=True,
    )

    runner_up = Player(
        name="8R44 Runner Up",
        fc_username=f"8r44_runner_{ts}",
        application_status="approved",
        active=True,
    )

    tournament = Tournament(
        name=f"8R44 Safety Test {ts}",
        max_players=16,
        status=TOURNAMENT_COMPLETED,
        completed_at=datetime.utcnow(),
        champion_id=None,
        runner_up_id=None,
    )

    db.session.add_all([champion, runner_up, tournament])
    db.session.flush()

    final = Match(
        tournament_id=tournament.id,
        player1_id=champion.id,
        player2_id=runner_up.id,
        player1_score=6,
        player2_score=4,
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
    final_id = final.id
    champion_id = champion.id
    runner_up_id = runner_up.id

    print("===== TEST 1 — COMPLETED TOURNAMENT SETUP =====")
    print("Tournament ID:", tournament_id)
    print("Final ID:", final_id)
    print("Status:", tournament.status)
    print("Champion ID:", tournament.champion_id)
    print("Runner-up ID:", tournament.runner_up_id)

    assert tournament.status == TOURNAMENT_COMPLETED
    assert tournament.champion_id == champion_id
    assert tournament.runner_up_id == runner_up_id

    print("COMPLETED TOURNAMENT READY: PASS")

    # ------------------------------------------------------------
    # TEST 2 — COMPLETED TOURNAMENT CANNOT BE STARTED
    # ------------------------------------------------------------

    client = app.test_client()

    with client.session_transaction() as session:
        session["founder_authenticated"] = True

    response = client.post(
        "/admin/tournament/control",
        data={"action": "start_tournament"},
        follow_redirects=False,
    )

    print("===== TEST 2 — START PROTECTION =====")
    print("HTTP status:", response.status_code)

    assert response.status_code == 409

    db.session.expire_all()
    tournament = db.session.get(Tournament, tournament_id)

    assert tournament.status == TOURNAMENT_COMPLETED

    print("COMPLETED TOURNAMENT CANNOT START: PASS")

    # ------------------------------------------------------------
    # TEST 3 — COMPLETED TOURNAMENT CANNOT BE PAUSED
    # ------------------------------------------------------------

    response = client.post(
        "/admin/tournament/control",
        data={"action": "pause_tournament"},
        follow_redirects=False,
    )

    print("===== TEST 3 — PAUSE PROTECTION =====")
    print("HTTP status:", response.status_code)

    assert response.status_code == 409

    db.session.expire_all()
    tournament = db.session.get(Tournament, tournament_id)

    assert tournament.status == TOURNAMENT_COMPLETED

    print("COMPLETED TOURNAMENT CANNOT PAUSE: PASS")

    # ------------------------------------------------------------
    # TEST 4 — COMPLETED TOURNAMENT CANNOT BE RESUMED
    # ------------------------------------------------------------

    response = client.post(
        "/admin/tournament/control",
        data={"action": "resume_tournament"},
        follow_redirects=False,
    )

    print("===== TEST 4 — RESUME PROTECTION =====")
    print("HTTP status:", response.status_code)

    assert response.status_code == 409

    db.session.expire_all()
    tournament = db.session.get(Tournament, tournament_id)

    assert tournament.status == TOURNAMENT_COMPLETED

    print("COMPLETED TOURNAMENT CANNOT RESUME: PASS")

    # ------------------------------------------------------------
    # TEST 5 — CHAMPION / RUNNER-UP MUST REMAIN INTACT
    # ------------------------------------------------------------

    db.session.expire_all()

    tournament = db.session.get(Tournament, tournament_id)

    print("===== TEST 5 — COMPLETION IDENTITY PROTECTION =====")
    print("Tournament status:", tournament.status)
    print("Champion ID:", tournament.champion_id)
    print("Runner-up ID:", tournament.runner_up_id)

    assert tournament.status == TOURNAMENT_COMPLETED
    assert tournament.champion_id == champion_id
    assert tournament.runner_up_id == runner_up_id
    assert tournament.champion_id != tournament.runner_up_id

    print("TOURNAMENT REMAINS COMPLETED: PASS")
    print("CHAMPION REMAINS INTACT: PASS")
    print("RUNNER-UP REMAINS INTACT: PASS")

    # ------------------------------------------------------------
    # TEST 6 — FINISHED FINAL CANNOT BE FINISHED AGAIN
    # ------------------------------------------------------------

    response = client.post(
        f"/admin/founder/match/{final_id}/live",
        data={
            "action": "finish",
            "player1_score": "10",
            "player2_score": "0",
        },
        follow_redirects=False,
    )

    print("===== TEST 6 — FINAL REFINISH PROTECTION =====")
    print("HTTP status:", response.status_code)

    assert response.status_code == 409

    db.session.expire_all()

    final = db.session.get(Match, final_id)
    tournament = db.session.get(Tournament, tournament_id)

    assert final.status == MATCH_FINISHED
    assert final.winner_id == champion_id
    assert final.loser_id == runner_up_id
    assert final.player1_score == 6
    assert final.player2_score == 4

    assert tournament.status == TOURNAMENT_COMPLETED
    assert tournament.champion_id == champion_id
    assert tournament.runner_up_id == runner_up_id

    print("FINISHED FINAL CANNOT BE REFINISHED: PASS")
    print("ORIGINAL SCORE PRESERVED: PASS")
    print("ORIGINAL CHAMPION PRESERVED: PASS")

    print("=" * 60)
    print("STEP 8R.44 COMPLETE — ALL TESTS PASSED")
    print("=" * 60)

    db.session.rollback()
