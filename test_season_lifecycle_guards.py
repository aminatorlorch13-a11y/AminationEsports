import os
import tempfile

from app import app
from models import db, Player, Tournament


def fail(message):
    raise AssertionError(message)


with tempfile.TemporaryDirectory() as tmp:
    database_path = os.path.join(tmp, "season_lifecycle_guards.db")

    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
        WTF_CSRF_ENABLED=False,
        SECRET_KEY="season-lifecycle-guards-test",
    )

    with app.app_context():
        db.drop_all()
        db.create_all()

        champion = Player(
            name="Season One Champion",
            fc_username="GuardChampion",
            email="guardchamp@example.test",
            password_hash="test",
            active=True,
            application_status="champion",
        )
        db.session.add(champion)
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
            completed_at=db.func.now(),
            champion_id=champion.id,
        )

        db.session.add(season_one)
        db.session.commit()

        client = app.test_client()

        with client.session_transaction() as session:
            session["founder_authenticated"] = True

        # ---------------------------------------------------------
        # Valid Season 2 creation.
        # ---------------------------------------------------------
        response = client.post(
            "/admin/tournament/create-season",
            data={
                "name": "Amination FC Season 2",
                "max_players": "64",
            },
            follow_redirects=False,
        )

        if response.status_code != 302:
            fail(
                f"Valid Season 2 creation returned "
                f"{response.status_code}: "
                f"{response.data.decode(errors='replace')}"
            )

        season_two = (
            Tournament.query
            .filter_by(season_number=2)
            .one_or_none()
        )

        if season_two is None:
            fail("Season 2 was not created.")

        if season_two.status != "registration":
            fail(
                f"Season 2 status is {season_two.status!r}, "
                "expected registration."
            )

        # ---------------------------------------------------------
        # Active Season 2 must block Season 3 creation.
        # ---------------------------------------------------------
        response = client.post(
            "/admin/tournament/create-season",
            data={
                "name": "Amination FC Season 3",
                "max_players": "128",
            },
            follow_redirects=False,
        )

        if response.status_code != 409:
            fail(
                "Season 3 creation while Season 2 is active was "
                f"not rejected with 409. Got {response.status_code}."
            )

        season_three = (
            Tournament.query
            .filter_by(season_number=3)
            .one_or_none()
        )

        if season_three is not None:
            fail("Season 3 was created despite Season 2 being active.")

        # ---------------------------------------------------------
        # Complete Season 2 so capacity validation can be tested
        # independently of the active-season guard.
        # ---------------------------------------------------------
        season_two.status = "completed"
        db.session.commit()

        # ---------------------------------------------------------
        # Invalid capacities must be rejected.
        # ---------------------------------------------------------
        invalid_capacities = [
            ("1", "below minimum"),
            ("3", "not power of two"),
            ("6", "not power of two"),
            ("12", "not power of two"),
            ("100", "not power of two"),
            ("0", "zero"),
            ("-2", "negative"),
        ]

        for value, reason in invalid_capacities:
            response = client.post(
                "/admin/tournament/create-season",
                data={
                    "name": "Invalid Capacity Test",
                    "max_players": value,
                },
                follow_redirects=False,
            )

            if response.status_code != 400:
                fail(
                    f"Capacity {value!r} ({reason}) returned "
                    f"{response.status_code}, expected 400."
                )

        # ---------------------------------------------------------
        # Missing capacity must be rejected.
        # ---------------------------------------------------------
        response = client.post(
            "/admin/tournament/create-season",
            data={
                "name": "Missing Capacity Test",
            },
            follow_redirects=False,
        )

        if response.status_code != 400:
            fail(
                "Missing capacity was not rejected with 400. "
                f"Got {response.status_code}."
            )

        # ---------------------------------------------------------
        # Season 2 remains the only active numbered season.
        # ---------------------------------------------------------
        numbered_seasons = (
            Tournament.query
            .filter(Tournament.season_number.isnot(None))
            .order_by(Tournament.season_number.asc())
            .all()
        )

        if len(numbered_seasons) != 2:
            fail(
                f"Expected exactly 2 numbered seasons, "
                f"found {len(numbered_seasons)}."
            )

        print("PASS: Season 2 creation succeeds after completed Season 1")
        print("PASS: active Season 2 blocks Season 3 creation")
        print("PASS: no Season 3 row was created")
        print("PASS: capacity 1 rejected")
        print("PASS: non-power-of-two capacities rejected")
        print("PASS: zero capacity rejected")
        print("PASS: negative capacity rejected")
        print("PASS: missing capacity rejected")
        print("PASS: only Seasons 1 and 2 exist")

        print("\nSEASON LIFECYCLE GUARDS TEST: PASS")
