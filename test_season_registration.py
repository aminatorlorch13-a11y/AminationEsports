import os
import tempfile

from werkzeug.security import generate_password_hash

from app import app
from models import (
    db,
    Player,
    Tournament,
    TournamentParticipant,
)


def fail(message):
    raise AssertionError(message)


with tempfile.TemporaryDirectory() as tmp:
    database_path = os.path.join(tmp, "season_registration.db")

    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
        WTF_CSRF_ENABLED=False,
        SECRET_KEY="season-registration-test",
    )

    with app.app_context():
        db.drop_all()
        db.create_all()

        champion = Player(
            name="Season One Champion",
            fc_username="ReturningChampion",
            email="champion@example.test",
            password_hash=generate_password_hash("Password123!"),
            active=True,
            application_status="champion",
            team_name="Champion Squad",
        )

        runner_up = Player(
            name="Season One Runner Up",
            fc_username="ReturningRunnerUp",
            email="runner@example.test",
            password_hash=generate_password_hash("Password123!"),
            active=True,
            application_status="runner_up",
            team_name="Runner Squad",
        )

        global_approved = Player(
            name="Globally Approved Player",
            fc_username="GlobalApproved",
            email="approved@example.test",
            password_hash=generate_password_hash("Password123!"),
            active=True,
            application_status="approved",
            team_name="Approved Squad",
        )

        db.session.add_all([
            champion,
            runner_up,
            global_approved,
        ])
        db.session.flush()

        season_one = Tournament(
            name="Amination FC Season 1",
            status="completed",
            max_players=32,
            entry_fee=0,
            payment_enabled=False,
            currency="ZAR",
            international_enabled=True,
            competition_day="Saturday",
            final_day="Sunday",
            season_number=1,
            champion_id=champion.id,
            runner_up_id=runner_up.id,
        )

        db.session.add(season_one)
        db.session.flush()

        # Season 1 historical participation.
        db.session.add_all([
            TournamentParticipant(
                tournament_id=season_one.id,
                player_id=champion.id,
                status="champion",
                team_name="Champion Squad",
            ),
            TournamentParticipant(
                tournament_id=season_one.id,
                player_id=runner_up.id,
                status="runner_up",
                team_name="Runner Squad",
            ),
        ])

        db.session.commit()

        client = app.test_client()

        with client.session_transaction() as session:
            session["founder_authenticated"] = True

        # ---------------------------------------------------------
        # Create Season 2.
        # ---------------------------------------------------------
        response = client.post(
            "/admin/tournament/create-season",
            data={
                "name": "Amination FC Season 2",
                "max_players": "32",
            },
            follow_redirects=False,
        )

        if response.status_code != 302:
            fail(
                f"Season 2 creation returned {response.status_code}: "
                f"{response.data.decode(errors='replace')}"
            )

        season_two = (
            Tournament.query
            .filter_by(season_number=2)
            .one_or_none()
        )

        if season_two is None:
            fail("Season 2 was not created.")

        # ---------------------------------------------------------
        # Register returning champion.
        # ---------------------------------------------------------
        response = client.post(
            "/register",
            data={
                "name": "Season One Champion",
                "fc_username": "ReturningChampion",
                "country": "South Africa",
                "date_of_birth": "2000-01-01",
                "email": "champion@example.test",
                "password": "Password123!",
                "confirm_password": "Password123!",
                "squad_ovr": "100",
                "terms_accepted": "yes",
            },
            follow_redirects=False,
        )

        if response.status_code not in (200, 302):
            fail(
                "Returning champion registration failed with "
                f"{response.status_code}: "
                f"{response.data.decode(errors='replace')}"
            )

        champion_s2 = (
            TournamentParticipant.query
            .filter_by(
                tournament_id=season_two.id,
                player_id=champion.id,
            )
            .one_or_none()
        )

        if champion_s2 is None:
            fail("Returning champion did not receive a Season 2 participant record.")

        if champion_s2.status != "pending":
            fail(
                "Returning champion was not placed into pending status. "
                f"Got {champion_s2.status!r}."
            )

        if champion_s2.priority_type == "none":
            fail(
                "Returning champion did not receive priority metadata."
            )

        if champion_s2.status in ("champion", "approved"):
            fail(
                "Returning champion bypassed the Season 2 pending approval stage."
            )

        # ---------------------------------------------------------
        # Register returning runner-up.
        # ---------------------------------------------------------
        response = client.post(
            "/register",
            data={
                "name": "Season One Runner Up",
                "fc_username": "ReturningRunnerUp",
                "country": "South Africa",
                "date_of_birth": "2000-01-01",
                "email": "runner@example.test",
                "password": "Password123!",
                "confirm_password": "Password123!",
                "squad_ovr": "100",
                "terms_accepted": "yes",
            },
            follow_redirects=False,
        )

        if response.status_code not in (200, 302):
            fail(
                "Returning runner-up registration failed with "
                f"{response.status_code}: "
                f"{response.data.decode(errors='replace')}"
            )

        runner_s2 = (
            TournamentParticipant.query
            .filter_by(
                tournament_id=season_two.id,
                player_id=runner_up.id,
            )
            .one_or_none()
        )

        if runner_s2 is None:
            fail("Returning runner-up did not receive a Season 2 participant record.")

        if runner_s2.status != "pending":
            fail(
                "Returning runner-up was not placed into pending status. "
                f"Got {runner_s2.status!r}."
            )

        if runner_s2.priority_type == "none":
            fail(
                "Returning runner-up did not receive priority metadata."
            )

        # ---------------------------------------------------------
        # Globally approved player must NOT automatically enter S2.
        # ---------------------------------------------------------
        global_s2 = (
            TournamentParticipant.query
            .filter_by(
                tournament_id=season_two.id,
                player_id=global_approved.id,
            )
            .one_or_none()
        )

        if global_s2 is not None:
            fail(
                "Globally approved player was automatically added "
                "to Season 2 without registering."
            )

        # ---------------------------------------------------------
        # Season 1 records remain separate.
        # ---------------------------------------------------------
        season_one_champion_records = (
            TournamentParticipant.query
            .filter_by(
                tournament_id=season_one.id,
                player_id=champion.id,
            )
            .count()
        )

        season_one_runner_records = (
            TournamentParticipant.query
            .filter_by(
                tournament_id=season_one.id,
                player_id=runner_up.id,
            )
            .count()
        )

        if season_one_champion_records != 1:
            fail("Season 1 champion participation record changed.")

        if season_one_runner_records != 1:
            fail("Season 1 runner-up participation record changed.")

        # ---------------------------------------------------------
        # Exactly two Season 2 applications exist.
        # ---------------------------------------------------------
        season_two_count = (
            TournamentParticipant.query
            .filter_by(tournament_id=season_two.id)
            .count()
        )

        if season_two_count != 2:
            fail(
                f"Expected 2 Season 2 registrations, found {season_two_count}."
            )

        print("PASS: returning champion must register again")
        print("PASS: returning champion starts pending")
        print("PASS: returning champion receives priority metadata")
        print("PASS: returning runner-up must register again")
        print("PASS: returning runner-up starts pending")
        print("PASS: returning runner-up receives priority metadata")
        print("PASS: global approval does not create Season 2 participation")
        print("PASS: Season 1 participation remains separate")
        print("PASS: exactly two Season 2 applications exist")

        print("\nSEASON REGISTRATION ISOLATION TEST: PASS")
