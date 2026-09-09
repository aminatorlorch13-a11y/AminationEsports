from datetime import datetime

from app import (
    app,
    db,
    Tournament,
    AdminAction,
)


print("=" * 60)
print("TOURNAMENT CAPACITY — CAPACITY-ONLY ROUTE TEST")
print("=" * 60)

app.config["TESTING"] = True

with app.app_context():
    ts = int(datetime.utcnow().timestamp())

    tournament = Tournament(
        name=f"Capacity Test {ts}",
        max_players=16,
        entry_fee=999,
        competition_day="Saturday",
        final_day="Sunday",
    )

    db.session.add(tournament)
    db.session.commit()

    tournament_id = tournament.id

    print("===== TEST 1 — TEST TOURNAMENT CREATED =====")
    print("Tournament ID:", tournament_id)
    print("Initial capacity:", tournament.max_players)
    print("Initial entry fee:", tournament.entry_fee)
    print("DATA SETUP: PASS")

    client = app.test_client()

    with client.session_transaction() as session:
        session["founder_authenticated"] = True

    # ------------------------------------------------------------
    # TEST 2 — CAPACITY-ONLY UPDATE
    # ------------------------------------------------------------

    response = client.post(
        "/admin/tournament/capacity",
        data={"max_players": "32"},
        follow_redirects=False,
    )

    print("===== TEST 2 — CAPACITY 16 -> 32 =====")
    print("HTTP status:", response.status_code)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/dashboard")

    db.session.expire_all()
    saved = db.session.get(Tournament, tournament_id)

    assert saved.max_players == 32
    assert saved.entry_fee == 999
    assert saved.name == tournament.name
    assert saved.competition_day == "Saturday"
    assert saved.final_day == "Sunday"

    print("CAPACITY CHANGED TO 32: PASS")
    print("ENTRY FEE UNCHANGED: PASS")
    print("TOURNAMENT NAME UNCHANGED: PASS")
    print("COMPETITION DAY UNCHANGED: PASS")
    print("FINAL DAY UNCHANGED: PASS")

    # ------------------------------------------------------------
    # TEST 3 — CAPACITY REDUCTION PROTECTION
    # ------------------------------------------------------------

    response = client.post(
        "/admin/tournament/capacity",
        data={"max_players": "16"},
        follow_redirects=False,
    )

    print("===== TEST 3 — CAPACITY REDUCTION PROTECTION =====")
    print("HTTP status:", response.status_code)

    assert response.status_code == 400

    db.session.expire_all()
    saved = db.session.get(Tournament, tournament_id)

    assert saved.max_players == 32

    print("CAPACITY REDUCTION REJECTED: PASS")
    print("CAPACITY REMAINS 32: PASS")

    # ------------------------------------------------------------
    # TEST 4 — INVALID CAPACITY PROTECTION
    # ------------------------------------------------------------

    response = client.post(
        "/admin/tournament/capacity",
        data={"max_players": "31"},
        follow_redirects=False,
    )

    print("===== TEST 4 — INVALID CAPACITY =====")
    print("HTTP status:", response.status_code)

    assert response.status_code == 400

    db.session.expire_all()
    saved = db.session.get(Tournament, tournament_id)

    assert saved.max_players == 32

    print("INVALID CAPACITY REJECTED: PASS")
    print("CAPACITY REMAINS 32: PASS")

    # ------------------------------------------------------------
    # TEST 5 — CAPACITY AUDIT LOG
    # ------------------------------------------------------------

    action = AdminAction.query.filter_by(
        action="tournament_capacity_updated"
    ).order_by(
        AdminAction.id.desc()
    ).first()

    print("===== TEST 5 — CAPACITY AUDIT LOG =====")

    assert action is not None
    assert "Previous: 16" in action.notes
    assert "New: 32" in action.notes

    print("CAPACITY AUDIT ACTION EXISTS: PASS")
    print("AUDIT DETAILS CORRECT: PASS")

    print("=" * 60)
    print("TOURNAMENT CAPACITY TEST COMPLETE — ALL TESTS PASSED")
    print("=" * 60)

    db.session.rollback()
