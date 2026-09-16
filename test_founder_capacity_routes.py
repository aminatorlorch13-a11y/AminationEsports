import os
from pathlib import Path

TEST_DB = Path(
    "/data/data/com.termux/files/home/amination_founder_capacity_routes_test.db"
)

if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from app import app, db
from models import Tournament

with app.app_context():
    db.drop_all()
    db.create_all()

    tournament = Tournament(
        name="Route Capacity Test",
        max_players=32,
        entry_fee=0,
        competition_day="Saturday",
        final_day="Sunday",
    )
    db.session.add(tournament)
    db.session.commit()

    tournament_id = tournament.id

print("=" * 72)
print("AMINATION ESPORTS — FOUNDER CAPACITY ROUTE TEST")
print("=" * 72)
print("Database:", TEST_DB)

app.config["TESTING"] = True

with app.test_client() as client:

    # Founder authentication is normally session-based.
    # Test the validation logic without modifying production.
    with client.session_transaction() as session:
        session["founder_authenticated"] = True

    print()
    print("===== CAPACITY ROUTE =====")

    response = client.post(
        "/admin/tournament/capacity",
        data={"max_players": "64"},
        follow_redirects=False,
    )

    print("32 → 64:", response.status_code)

    with app.app_context():
        tournament = db.session.get(Tournament, tournament_id)
        assert tournament.max_players == 64

    print("32 → 64: PASS")

    response = client.post(
        "/admin/tournament/capacity",
        data={"max_players": "128"},
        follow_redirects=False,
    )

    with app.app_context():
        tournament = db.session.get(Tournament, tournament_id)
        assert tournament.max_players == 128

    print("64 → 128: PASS")

    response = client.post(
        "/admin/tournament/capacity",
        data={"max_players": "256"},
        follow_redirects=False,
    )

    with app.app_context():
        tournament = db.session.get(Tournament, tournament_id)
        assert tournament.max_players == 256

    print("128 → 256: PASS")

    print()
    print("===== INVALID CAPACITY =====")

    response = client.post(
        "/admin/tournament/capacity",
        data={"max_players": "100"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    print("100 rejected: PASS")

    response = client.post(
        "/admin/tournament/capacity",
        data={"max_players": "0"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    print("0 rejected: PASS")

    print()
    print("===== CAPACITY REDUCTION =====")

    response = client.post(
        "/admin/tournament/capacity",
        data={"max_players": "128"},
        follow_redirects=False,
    )

    assert response.status_code == 400

    with app.app_context():
        tournament = db.session.get(Tournament, tournament_id)
        assert tournament.max_players == 256

    print("256 → 128 rejected: PASS")
    print("Capacity remains 256: PASS")

    print()
    print("===== SETTINGS ROUTE CAPACITY REDUCTION =====")

    response = client.post(
        "/admin/tournament/settings",
        data={
            "name": "Route Capacity Test",
            "max_players": "128",
            "entry_fee": "0",
            "competition_day": "Saturday",
            "final_day": "Sunday",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400

    with app.app_context():
        tournament = db.session.get(Tournament, tournament_id)
        assert tournament.max_players == 256

    print("Settings 256 → 128 rejected: PASS")
    print("Settings capacity remains 256: PASS")

    response = client.post(
        "/admin/tournament/settings",
        data={
            "name": "Updated Route Capacity Test",
            "max_players": "256",
            "entry_fee": "50",
            "competition_day": "Friday",
            "final_day": "Saturday",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        tournament = db.session.get(Tournament, tournament_id)
        assert tournament.name == "Updated Route Capacity Test"
        assert tournament.max_players == 256
        assert tournament.entry_fee == 50
        assert tournament.competition_day == "Friday"
        assert tournament.final_day == "Saturday"

    print("Settings valid update: PASS")
    print("Settings capacity remains 256 after valid update: PASS")

    print()
    print("=" * 72)
    print("FOUNDER CAPACITY ROUTE TEST COMPLETE")
    print("=" * 72)
