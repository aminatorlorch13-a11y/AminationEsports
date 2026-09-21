import os
import tempfile

from app import (
    app,
    db,
    normalize_live_provider_url,
    configure_official_live_broadcast,
    TOURNAMENT_IN_PROGRESS,
    MATCH_SCHEDULED,
    MATCH_LIVE,
    MATCH_FINISHED,
)
from models import Tournament, Player, Match


def fail(message):
    raise AssertionError(message)


print("=" * 72)
print("AMINATION ESPORTS — LIVE BROADCAST LIFECYCLE REGRESSION TEST")
print("=" * 72)


with tempfile.TemporaryDirectory() as tmp:
    database_path = os.path.join(
        tmp,
        "live_broadcast_lifecycle.db"
    )

    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
        WTF_CSRF_ENABLED=False,
        SECRET_KEY="live-broadcast-lifecycle-test",
    )

    with app.app_context():
        db.drop_all()
        db.create_all()

        # ========================================================
        # TEST DATA
        # ========================================================
        players = [
            Player(
                name="Live Test Player 1",
                fc_username="live_test_player_1",
                active=True,
                application_status="approved",
            ),
            Player(
                name="Live Test Player 2",
                fc_username="live_test_player_2",
                active=True,
                application_status="approved",
            ),
            Player(
                name="Live Test Player 3",
                fc_username="live_test_player_3",
                active=True,
                application_status="approved",
            ),
            Player(
                name="Live Test Player 4",
                fc_username="live_test_player_4",
                active=True,
                application_status="approved",
            ),
        ]

        tournament = Tournament(
            name="Live Broadcast Lifecycle Test",
            status=TOURNAMENT_IN_PROGRESS,
            max_players=4,
            entry_fee=0,
            payment_enabled=False,
            currency="ZAR",
            international_enabled=True,
            competition_day="Saturday",
            final_day="Sunday",
            season_number=99,
        )

        db.session.add(tournament)
        db.session.add_all(players)
        db.session.flush()

        match_one = Match(
            tournament_id=tournament.id,
            player1_id=players[0].id,
            player2_id=players[1].id,
            status=MATCH_SCHEDULED,
            round_name="Semi Final",
            round_number=1,
            match_number=1,
            bracket_position=1,
        )

        match_two = Match(
            tournament_id=tournament.id,
            player1_id=players[2].id,
            player2_id=players[3].id,
            status=MATCH_SCHEDULED,
            round_name="Semi Final",
            round_number=1,
            match_number=2,
            bracket_position=2,
        )

        db.session.add_all([match_one, match_two])
        db.session.commit()

        # ========================================================
        # TEST 1 — PROVIDER URL SECURITY
        # ========================================================
        print("\n===== TEST 1 — PROVIDER URL SECURITY =====")

        valid_youtube = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/live/dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        ]

        for url in valid_youtube:
            normalized, error = normalize_live_provider_url(
                "youtube",
                url,
            )

            if error or not normalized:
                fail(
                    f"Valid YouTube URL was rejected: {url} — {error}"
                )

            if not normalized.startswith(
                "https://www.youtube-nocookie.com/embed/"
            ):
                fail(
                    f"YouTube URL did not normalize to the safe embed host: "
                    f"{normalized}"
                )

        valid_tiktok = (
            "https://www.tiktok.com/@amination/video/1234567890123456789"
        )

        normalized, error = normalize_live_provider_url(
            "tiktok",
            valid_tiktok,
        )

        if error or not normalized:
            fail(
                f"Valid TikTok video URL was rejected: {error}"
            )

        if not normalized.startswith(
            "https://www.tiktok.com/player/v1/"
        ):
            fail(
                f"TikTok URL did not normalize to the official player: "
                f"{normalized}"
            )

        rejected_urls = [
            (
                "youtube",
                "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ),
            (
                "youtube",
                "https://evil.example/watch?v=dQw4w9WgXcQ",
            ),
            (
                "youtube",
                "javascript:alert(1)",
            ),
            (
                "youtube",
                "https://www.youtube.com/watch?v=bad",
            ),
            (
                "tiktok",
                "https://evil.example/video/123456789",
            ),
            (
                "tiktok",
                "https://www.tiktok.com/@amination/live",
            ),
        ]

        for provider, url in rejected_urls:
            normalized, error = normalize_live_provider_url(
                provider,
                url,
            )

            if normalized is not None or not error:
                fail(
                    f"Unsafe/invalid URL was accepted: "
                    f"{provider} {url}"
                )

        print("Provider validation/security: PASS")

        # ========================================================
        # TEST 2 — BROADCAST CONFIGURATION
        # ========================================================
        print("\n===== TEST 2 — BROADCAST CONFIGURATION =====")

        success, message = configure_official_live_broadcast(
            tournament=tournament,
            match=match_one,
            provider="youtube",
            raw_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            title="Amination eSports — Semi Final 1",
        )

        if not success:
            fail(
                f"Valid broadcast configuration failed: {message}"
            )

        if not tournament.live_enabled:
            fail("Tournament broadcast was not enabled.")

        if tournament.live_match_id != match_one.id:
            fail(
                "Tournament live_match_id does not point to "
                "the configured match."
            )

        if not tournament.live_embed_url:
            fail("Normalized broadcast embed URL was not stored.")

        print("Valid broadcast configuration: PASS")

        # ========================================================
        # TEST 3 — CONFIGURED DOES NOT MEAN PUBLICLY LIVE
        # ========================================================
        print(
            "\n===== TEST 3 — CONFIGURED BROADCAST "
            "DOES NOT APPEAR BEFORE MATCH IS LIVE ====="
        )

        with app.test_client() as client:
            response = client.get("/live")

            if response.status_code != 200:
                fail(
                    f"/live returned unexpected status: "
                    f"{response.status_code}"
                )

            body = response.get_data(as_text=True)

            if "youtube-nocookie.com/embed/" in body:
                fail(
                    "Configured broadcast appeared publicly "
                    "before the match became live."
                )

        print("Pre-live broadcast gating: PASS")

        # ========================================================
        # TEST 4 — START MATCH
        # ========================================================
        print("\n===== TEST 4 — START MATCH =====")

        with app.test_client() as client:
            with client.session_transaction() as session:
                session["founder_authenticated"] = True

            response = client.post(
                f"/admin/founder/match/{match_one.id}/live",
                data={"action": "start"},
                follow_redirects=False,
            )

            if response.status_code not in (302, 303):
                fail(
                    f"Starting live match returned "
                    f"{response.status_code}"
                )

        db.session.refresh(match_one)
        db.session.refresh(tournament)

        if match_one.status != MATCH_LIVE:
            fail(
                f"Match did not become MATCH_LIVE: "
                f"{match_one.status}"
            )

        if not match_one.is_live:
            fail("Match is_live flag was not enabled.")

        if tournament.live_match_id != match_one.id:
            fail(
                "Starting configured match did not preserve "
                "its broadcast ownership."
            )

        print("Match start lifecycle: PASS")

        # ========================================================
        # TEST 5 — PUBLIC LIVE EMBED
        # ========================================================
        print("\n===== TEST 5 — PUBLIC LIVE EMBED =====")

        with app.test_client() as client:
            response = client.get("/live")

            if response.status_code != 200:
                fail(
                    f"/live returned unexpected status: "
                    f"{response.status_code}"
                )

            body = response.get_data(as_text=True)

            if (
                "youtube-nocookie.com/embed/dQw4w9WgXcQ"
                not in body
            ):
                fail(
                    "Actual live match did not expose "
                    "the configured broadcast embed."
                )

        print("Public live embed: PASS")

        # ========================================================
        # TEST 6 — STARTING ANOTHER MATCH CLEARS STALE BROADCAST
        # ========================================================
        print(
            "\n===== TEST 6 — SECOND MATCH "
            "CLEARS STALE BROADCAST ====="
        )

        with app.test_client() as client:
            with client.session_transaction() as session:
                session["founder_authenticated"] = True

            response = client.post(
                f"/admin/founder/match/{match_two.id}/live",
                data={"action": "start"},
                follow_redirects=False,
            )

            if response.status_code not in (302, 303):
                fail(
                    f"Starting second live match returned "
                    f"{response.status_code}"
                )

        db.session.refresh(match_one)
        db.session.refresh(match_two)
        db.session.refresh(tournament)

        if match_one.is_live:
            fail(
                "Previous match remained publicly marked as live."
            )

        if match_one.status != MATCH_SCHEDULED:
            fail(
                "Previous live match was not returned to "
                "MATCH_SCHEDULED."
            )

        if not match_two.is_live:
            fail("Second match did not become live.")

        if tournament.live_match_id is not None:
            fail(
                "Stale tournament live_match_id survived "
                "the match transition."
            )

        if tournament.live_enabled:
            fail(
                "Stale tournament live_enabled survived "
                "the match transition."
            )

        if tournament.live_embed_url is not None:
            fail(
                "Stale broadcast embed URL survived "
                "the match transition."
            )

        print("Single-live-match isolation: PASS")

        # ========================================================
        # TEST 7 — RECONFIGURE SECOND MATCH
        # ========================================================
        print("\n===== TEST 7 — RECONFIGURE SECOND MATCH =====")

        success, message = configure_official_live_broadcast(
            tournament=tournament,
            match=match_two,
            provider="tiktok",
            raw_url=valid_tiktok,
            title="Amination eSports TikTok Match",
        )

        if not success:
            fail(
                f"TikTok broadcast configuration failed: {message}"
            )

        db.session.commit()
        db.session.refresh(tournament)

        if tournament.live_match_id != match_two.id:
            fail(
                "Second broadcast did not become authoritative."
            )

        if tournament.live_provider != "tiktok":
            fail(
                f"Expected TikTok provider, got "
                f"{tournament.live_provider}"
            )

        print("Second broadcast provider: PASS")

        # ========================================================
        # TEST 8 — STOP LIVE MATCH CLEARS BROADCAST
        # ========================================================
        print("\n===== TEST 8 — STOP LIVE MATCH =====")

        with app.test_client() as client:
            with client.session_transaction() as session:
                session["founder_authenticated"] = True

            response = client.post(
                f"/admin/founder/match/{match_two.id}/live",
                data={"action": "stop"},
                follow_redirects=False,
            )

            if response.status_code not in (302, 303):
                fail(
                    f"Stopping live match returned "
                    f"{response.status_code}"
                )

        db.session.refresh(match_two)
        db.session.refresh(tournament)

        if match_two.is_live:
            fail("Stopped match still has is_live=True.")

        if match_two.status != MATCH_SCHEDULED:
            fail(
                "Stopped live match did not return "
                "to MATCH_SCHEDULED."
            )

        if tournament.live_enabled:
            fail("Broadcast remained enabled after stop.")

        if tournament.live_match_id is not None:
            fail(
                "live_match_id remained after stopping "
                "the broadcast-owned match."
            )

        if tournament.live_embed_url is not None:
            fail(
                "Broadcast embed URL remained after stop."
            )

        print("Stop lifecycle: PASS")

        # ========================================================
        # TEST 9 — INVALID MATCH CONFIGURATION STATES
        # ========================================================
        print("\n===== TEST 9 — INVALID CONFIGURATION STATES =====")

        bye_match = Match(
            tournament_id=tournament.id,
            player1_id=players[0].id,
            player2_id=None,
            status=MATCH_SCHEDULED,
            round_name="Quarter Final",
            round_number=2,
            match_number=3,
            bracket_position=3,
            is_bye=True,
        )

        finished_match = Match(
            tournament_id=tournament.id,
            player1_id=players[0].id,
            player2_id=players[1].id,
            status=MATCH_FINISHED,
            round_name="Quarter Final",
            round_number=2,
            match_number=4,
            bracket_position=4,
        )

        incomplete_match = Match(
            tournament_id=tournament.id,
            player1_id=players[0].id,
            player2_id=None,
            status=MATCH_SCHEDULED,
            round_name="Quarter Final",
            round_number=2,
            match_number=5,
            bracket_position=5,
        )

        db.session.add_all([
            bye_match,
            finished_match,
            incomplete_match,
        ])
        db.session.commit()

        invalid_cases = [
            (
                bye_match,
                "BYE match",
            ),
            (
                finished_match,
                "finished match",
            ),
            (
                incomplete_match,
                "incomplete match",
            ),
        ]

        for invalid_match, label in invalid_cases:
            success, message = configure_official_live_broadcast(
                tournament=tournament,
                match=invalid_match,
                provider="youtube",
                raw_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                title="Should Not Configure",
            )

            if success:
                fail(
                    f"Invalid {label} was accepted."
                )

            if not message:
                fail(
                    f"Invalid {label} was rejected without a reason."
                )

        print("Invalid match-state protection: PASS")

        # ========================================================
        # TEST 10 — PUBLIC PAGE AFTER STOP
        # ========================================================
        print(
            "\n===== TEST 10 — PUBLIC PAGE "
            "AFTER BROADCAST STOP ====="
        )

        with app.test_client() as client:
            response = client.get("/live")

            if response.status_code != 200:
                fail(
                    f"/live returned unexpected status: "
                    f"{response.status_code}"
                )

            body = response.get_data(as_text=True)

            if "youtube-nocookie.com/embed/" in body:
                fail(
                    "Public live page still exposed a stale "
                    "YouTube broadcast after stop."
                )

            if "tiktok.com/player/v1/" in body:
                fail(
                    "Public live page still exposed a stale "
                    "TikTok broadcast after stop."
                )

        print("Post-stop public state: PASS")

print("\n" + "=" * 72)
print("ALL LIVE BROADCAST LIFECYCLE TESTS PASSED")
print("=" * 72)
