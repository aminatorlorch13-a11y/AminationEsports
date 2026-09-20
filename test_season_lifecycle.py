import os
import tempfile

from app import app
from models import (
    db,
    Player,
    Tournament,
    TournamentParticipant,
    Match,
    TournamentEvent,
    HallOfChampion,
)


def fail(message):
    raise AssertionError(message)


with tempfile.TemporaryDirectory() as tmp:
    database_path = os.path.join(tmp, "season_lifecycle.db")

    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
        WTF_CSRF_ENABLED=False,
        SECRET_KEY="season-lifecycle-test",
    )

    with app.app_context():
        db.drop_all()
        db.create_all()

        # ---------------------------------------------------------
        # Fake Season 1 historical record.
        # ---------------------------------------------------------
        champion = Player(
            name="Season One Champion",
            fc_username="SeasonOneChampion",
            email="season1@example.test",
            password_hash="test",
            active=True,
            application_status="champion",
        )

        runner_up = Player(
            name="Season One Runner Up",
            fc_username="SeasonOneRunnerUp",
            email="runner@example.test",
            password_hash="test",
            active=True,
            application_status="runner_up",
        )

        db.session.add_all([champion, runner_up])
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
            whatsapp_group_link="https://example.test/season1",
            completed_at=db.func.now(),
            champion_id=champion.id,
            runner_up_id=runner_up.id,
        )

        db.session.add(season_one)
        db.session.flush()

        # Historical data must remain attached to Season 1.
        historical_participant = TournamentParticipant(
            tournament_id=season_one.id,
            player_id=champion.id,
            status="champion",
            team_name="Season One Squad",
        )

        historical_match = Match(
            tournament_id=season_one.id,
            player1_id=champion.id,
            player2_id=runner_up.id,
            player1_score=3,
            player2_score=1,
            status="completed",
            winner_id=champion.id,
            loser_id=runner_up.id,
        )

        historical_event = TournamentEvent(
            tournament_id=season_one.id,
            event_type="CHAMPION_CONFIRMED",
            sequence_number=1,
            payload={
                "season_number": 1,
                "champion_id": champion.id,
            },
        )

        historical_hall = HallOfChampion(
            tournament_id=season_one.id,
            player_id=champion.id,
            team_name="Season One Squad",
            season_number=1,
            tournament_name=season_one.name,
            final_score="3-1",
        )

        db.session.add_all([
            historical_participant,
            historical_match,
            historical_event,
            historical_hall,
        ])
        db.session.commit()

        season_one_id = season_one.id
        season_one_champion_id = champion.id
        season_one_runner_up_id = runner_up.id
        season_one_match_id = historical_match.id
        season_one_event_id = historical_event.id
        season_one_hall_id = historical_hall.id

    # -------------------------------------------------------------
    # Founder creates Season 2.
    # -------------------------------------------------------------
    client = app.test_client()

    with client.session_transaction() as session:
        session["founder_authenticated"] = True

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
            f"Season creation returned {response.status_code}: "
            f"{response.data.decode(errors='replace')}"
        )

    with app.app_context():
        season_one = db.session.get(Tournament, season_one_id)

        season_two = (
            Tournament.query
            .filter_by(season_number=2)
            .one_or_none()
        )

        if season_two is None:
            fail("Season 2 was not created.")

        # ---------------------------------------------------------
        # Identity / lifecycle checks.
        # ---------------------------------------------------------
        if season_two.id == season_one.id:
            fail("Season 2 reused the Season 1 database row.")

        if season_two.status != "registration":
            fail(f"Season 2 status is {season_two.status!r}.")

        if season_two.season_number != 2:
            fail(
                f"Expected Season 2 number, got "
                f"{season_two.season_number!r}."
            )

        if season_two.max_players != 64:
            fail(
                f"Expected capacity 64, got "
                f"{season_two.max_players}."
            )

        if season_two.champion_id is not None:
            fail("Season 2 inherited a champion.")

        if season_two.runner_up_id is not None:
            fail("Season 2 inherited a runner-up.")

        if season_two.completed_at is not None:
            fail("Season 2 inherited completed_at.")

        if season_two.live_enabled:
            fail("Season 2 started with live_enabled=True.")

        if season_two.live_match_id is not None:
            fail("Season 2 inherited a live match.")

        # New season must start empty.
        if TournamentParticipant.query.filter_by(
            tournament_id=season_two.id
        ).count() != 0:
            fail("Season 2 contains participants immediately after creation.")

        if Match.query.filter_by(
            tournament_id=season_two.id
        ).count() != 0:
            fail("Season 2 contains matches immediately after creation.")

        if TournamentEvent.query.filter_by(
            tournament_id=season_two.id
        ).count() != 0:
            fail("Season 2 contains events immediately after creation.")

        if HallOfChampion.query.filter_by(
            tournament_id=season_two.id
        ).count() != 0:
            fail("Season 2 contains a Hall of Champion record.")

        # ---------------------------------------------------------
        # Historical Season 1 must be untouched.
        # ---------------------------------------------------------
        season_one_after = db.session.get(Tournament, season_one_id)

        if season_one_after.status != "completed":
            fail("Season 1 status changed.")

        if season_one_after.season_number != 1:
            fail("Season 1 season number changed.")

        if season_one_after.champion_id != season_one_champion_id:
            fail("Season 1 champion changed.")

        if season_one_after.runner_up_id != season_one_runner_up_id:
            fail("Season 1 runner-up changed.")

        if db.session.get(Match, season_one_match_id) is None:
            fail("Season 1 historical match disappeared.")

        if db.session.get(TournamentEvent, season_one_event_id) is None:
            fail("Season 1 historical event disappeared.")

        if db.session.get(HallOfChampion, season_one_hall_id) is None:
            fail("Season 1 Hall of Champion record disappeared.")

        # ---------------------------------------------------------
        # Current-season helper must resolve Season 2.
        # ---------------------------------------------------------
        from app import current_tournament

        current = current_tournament()

        if current is None:
            fail("current_tournament() returned None.")

        if current.id != season_two.id:
            fail(
                f"current_tournament() returned ID {current.id}, "
                f"expected {season_two.id}."
            )

        print("PASS: new Tournament row created")
        print("PASS: Season 2 assigned season_number=2")
        print("PASS: Season 2 starts in registration")
        print("PASS: Season 2 capacity = 64")
        print("PASS: Season 2 starts with zero participants")
        print("PASS: Season 2 starts with zero matches")
        print("PASS: Season 2 starts with zero events")
        print("PASS: Season 2 has no champion or runner-up")
        print("PASS: Season 2 live state starts disabled")
        print("PASS: Season 1 historical tournament preserved")
        print("PASS: Season 1 match preserved")
        print("PASS: Season 1 event preserved")
        print("PASS: Season 1 Hall of Champion preserved")
        print("PASS: current_tournament() resolves Season 2")
        print("\nSEASON LIFECYCLE TEST: PASS")
