import os

DB_PATH = "/data/data/com.termux/files/home/amination_tournament_sim.db"

os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["SECRET_KEY"] = "clean-tournament-simulation-key"

from app import app, db
from models import Player, Tournament, Match, PlayerStatistic
from app import (
    TOURNAMENT_REGISTRATION,
    TOURNAMENT_DRAW_RELEASED,
    TOURNAMENT_COMPLETED,
    MATCH_SCHEDULED,
    MATCH_LIVE,
    MATCH_FINISHED,
)

print("=" * 60)
print(" AMINATION ESPORTS — CLEAN TOURNAMENT TEST")
print("=" * 60)

with app.app_context():

    # SAFETY
    print("\n=== SAFETY CHECK ===")

    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    print("Database:", uri)

    if not uri.startswith("sqlite:"):
        raise SystemExit("SAFETY STOP: NOT SQLITE")

    if "amination_tournament_sim.db" not in uri:
        raise SystemExit("SAFETY STOP: WRONG DATABASE")

    print("PASS: Test SQLite database only.")
    print("PASS: Production PostgreSQL is NOT being used.")

    # FRESH DATABASE
    print("\n=== FRESH DATABASE ===")

    db.drop_all()
    db.create_all()

    print("PASS: Fresh schema created.")

    # TOURNAMENT
    print("\n=== TOURNAMENT ===")

    tournament = Tournament(
        name="Amination Clean Tournament Simulation",
        max_players=8,
        entry_fee=0,
        competition_day="Saturday",
        final_day="Sunday",
        status=TOURNAMENT_REGISTRATION,
    )

    db.session.add(tournament)
    db.session.commit()

    print("Tournament ID:", tournament.id)
    print("Status:", tournament.status)

    # PLAYERS
    print("\n=== PLAYERS ===")

    for i in range(1, 9):
        player = Player(
            name=f"Simulation Player {i}",
            fc_username=f"SimPlayer{i}",
            country="South Africa",
            squad_ovr=115,
            email=f"sim-player-{i}@example.invalid",
            password_hash="SIMULATION_HASH",
            application_status="approved",
            terms_accepted=True,
            terms_version="1.0",
            active=True,
        )

        db.session.add(player)

    db.session.commit()

    players = Player.query.filter_by(
        application_status="approved",
        active=True,
    ).all()

    print("Approved players:", len(players))

    if len(players) != 8:
        raise SystemExit("FAIL: Expected 8 approved players.")

    print("PASS: 8 approved players.")

    # FOUNDER SESSION
    client = app.test_client()

    with client.session_transaction() as session:
        session["founder_authenticated"] = True

    # DRAW
    print("\n=== OFFICIAL DRAW ===")

    response = client.post(
        "/admin/tournament/draw",
        follow_redirects=False,
    )

    print("Draw HTTP:", response.status_code)

    if response.status_code not in (302, 303):
        print(response.get_data(as_text=True)[:2000])
        raise SystemExit("FAIL: Draw failed.")

    db.session.expire_all()
    tournament = db.session.get(Tournament, tournament.id)

    print("Tournament status:", tournament.status)

    if tournament.status != TOURNAMENT_DRAW_RELEASED:
        raise SystemExit(
            f"FAIL: Expected {TOURNAMENT_DRAW_RELEASED}."
        )

    print("PASS: Draw released.")

    # ROUND 1
    print("\n=== ROUND 1 ===")

    matches = (
        Match.query
        .filter_by(tournament_id=tournament.id)
        .order_by(
            Match.round_number.asc(),
            Match.bracket_position.asc(),
        )
        .all()
    )

    round_one = [
        m for m in matches
        if m.round_number == 1
    ]

    print("Round 1 matches:", len(round_one))

    if len(round_one) != 4:
        raise SystemExit("FAIL: Round 1 should have 4 matches.")

    if any(m.is_bye for m in round_one):
        raise SystemExit("FAIL: Round 1 unexpectedly contains a BYE.")

    print("PASS: Round 1 structure.")

    # PLAY ROUND 1
    print("\n=== PLAY ROUND 1 ===")

    for n, match in enumerate(round_one, 1):

        print(
            f"Match {match.id}: "
            f"{match.player1_id} vs {match.player2_id}"
        )

        response = client.post(
            f"/admin/founder/match/{match.id}/live",
            data={"action": "start"},
            follow_redirects=False,
        )

        if response.status_code not in (302, 303):
            print(response.get_data(as_text=True)[:1500])
            raise SystemExit("FAIL: Could not start Round 1 match.")

        db.session.refresh(match)

        if match.status != MATCH_LIVE or not match.is_live:
            raise SystemExit("FAIL: Match did not become LIVE.")

        if n % 2:
            score1, score2 = 3, 1
        else:
            score1, score2 = 1, 0

        response = client.post(
            f"/admin/founder/match/{match.id}/live",
            data={
                "action": "finish",
                "player1_score": str(score1),
                "player2_score": str(score2),
            },
            follow_redirects=False,
        )

        if response.status_code not in (302, 303):
            print(response.get_data(as_text=True)[:1500])
            raise SystemExit("FAIL: Could not finish Round 1 match.")

        db.session.refresh(match)

        if match.status != MATCH_FINISHED:
            raise SystemExit("FAIL: Round 1 match not finished.")

        if not match.winner_id or not match.loser_id:
            raise SystemExit("FAIL: Round 1 winner/loser missing.")

        print(
            f"PASS: {score1}-{score2}, "
            f"winner={match.winner_id}"
        )

    print("PASS: Round 1 completed.")

    # SEMI FINALS
    print("\n=== SEMI-FINALS ===")

    db.session.expire_all()

    matches = (
        Match.query
        .filter_by(tournament_id=tournament.id)
        .order_by(
            Match.round_number.asc(),
            Match.bracket_position.asc(),
        )
        .all()
    )

    semi_finals = [
        m for m in matches
        if m.round_number == 2
    ]

    print("Semi-finals:", len(semi_finals))

    if len(semi_finals) != 2:
        raise SystemExit("FAIL: Expected 2 semi-finals.")

    for match in semi_finals:

        print(
            f"Semi {match.id}: "
            f"{match.player1_id} vs {match.player2_id}"
        )

        if not match.player1_id or not match.player2_id:
            raise SystemExit("FAIL: Semi-final player missing.")

        response = client.post(
            f"/admin/founder/match/{match.id}/live",
            data={"action": "start"},
            follow_redirects=False,
        )

        if response.status_code not in (302, 303):
            print(response.get_data(as_text=True)[:1500])
            raise SystemExit("FAIL: Could not start semi-final.")

        response = client.post(
            f"/admin/founder/match/{match.id}/live",
            data={
                "action": "finish",
                "player1_score": "2",
                "player2_score": "0",
            },
            follow_redirects=False,
        )

        if response.status_code not in (302, 303):
            print(response.get_data(as_text=True)[:1500])
            raise SystemExit("FAIL: Could not finish semi-final.")

        db.session.refresh(match)

        if match.status != MATCH_FINISHED:
            raise SystemExit("FAIL: Semi-final not finished.")

        if not match.winner_id:
            raise SystemExit("FAIL: Semi-final winner missing.")

        print(
            f"PASS: Semi-final {match.id}, "
            f"winner={match.winner_id}"
        )

    print("PASS: Both semi-finals completed.")

    # FINAL
    print("\n=== FINAL CHECK ===")

    db.session.expire_all()

    matches = (
        Match.query
        .filter_by(tournament_id=tournament.id)
        .order_by(
            Match.round_number.asc(),
            Match.bracket_position.asc(),
        )
        .all()
    )

    finals = [
        m for m in matches
        if m.round_name == "Final"
    ]

    print("Finals found:", len(finals))

    if len(finals) != 1:
        raise SystemExit(
            f"FAIL: Expected 1 final, found {len(finals)}."
        )

    final = finals[0]

    print("Final ID:", final.id)
    print("Round:", final.round_number)
    print("Name:", final.round_name)
    print("Status:", final.status)
    print("Player 1:", final.player1_id)
    print("Player 2:", final.player2_id)

    if not final.player1_id or not final.player2_id:
        raise SystemExit("FAIL: Final players missing.")

    if final.status != MATCH_SCHEDULED:
        raise SystemExit(
            f"FAIL: Final status is {final.status}."
        )

    print("PASS: Final correctly scheduled.")

    # START FINAL
    print("\n=== START FINAL ===")

    response = client.post(
        f"/admin/founder/match/{final.id}/live",
        data={"action": "start"},
        follow_redirects=False,
    )

    print("Start HTTP:", response.status_code)

    if response.status_code not in (302, 303):
        print(response.get_data(as_text=True)[:3000])
        raise SystemExit("FAIL: Final could not start.")

    db.session.refresh(final)

    print("Final status:", final.status)
    print("Final live:", final.is_live)

    if final.status != MATCH_LIVE:
        raise SystemExit("FAIL: Final did not become LIVE.")

    if not final.is_live:
        raise SystemExit("FAIL: Final is_live is not True.")

    print("PASS: Final is LIVE.")

    # FINISH FINAL
    print("\n=== FINISH FINAL ===")

    response = client.post(
        f"/admin/founder/match/{final.id}/live",
        data={
            "action": "finish",
            "player1_score": "3",
            "player2_score": "1",
        },
        follow_redirects=False,
    )

    print("Finish HTTP:", response.status_code)

    if response.status_code not in (302, 303):
        print(response.get_data(as_text=True)[:3000])
        raise SystemExit("FAIL: Final could not finish.")

    db.session.expire_all()

    final = db.session.get(Match, final.id)
    tournament = db.session.get(Tournament, tournament.id)

    print("Final status:", final.status)
    print("Final live:", final.is_live)
    print("Final winner:", final.winner_id)
    print("Final loser:", final.loser_id)

    if final.status != MATCH_FINISHED:
        raise SystemExit("FAIL: Final is not FINISHED.")

    if final.is_live:
        raise SystemExit("FAIL: Final is still LIVE.")

    if not final.winner_id:
        raise SystemExit("FAIL: Final winner missing.")

    if not final.loser_id:
        raise SystemExit("FAIL: Final loser missing.")

    print("PASS: Final finished.")

    # COMPLETION
    print("\n=== TOURNAMENT COMPLETION ===")

    print("Tournament status:", tournament.status)
    print("Champion:", tournament.champion_id)
    print("Runner-up:", tournament.runner_up_id)

    if tournament.status != TOURNAMENT_COMPLETED:
        raise SystemExit(
            f"FAIL: Tournament status is {tournament.status}, "
            f"expected {TOURNAMENT_COMPLETED}."
        )

    if tournament.champion_id != final.winner_id:
        raise SystemExit(
            "FAIL: Champion does not equal final winner."
        )

    if tournament.runner_up_id != final.loser_id:
        raise SystemExit(
            "FAIL: Runner-up does not equal final loser."
        )

    print("PASS: Tournament COMPLETED.")
    print("PASS: Champion recorded.")
    print("PASS: Runner-up recorded.")

    # MATCH COUNT
    print("\n=== MATCH COUNT ===")

    matches = (
        Match.query
        .filter_by(tournament_id=tournament.id)
        .all()
    )

    print("Total matches:", len(matches))

    if len(matches) != 7:
        raise SystemExit(
            f"FAIL: Expected 7 matches, got {len(matches)}."
        )

    unfinished = [
        m for m in matches
        if m.status != MATCH_FINISHED
    ]

    if unfinished:
        raise SystemExit(
            f"FAIL: {len(unfinished)} matches unfinished."
        )

    live = [
        m for m in matches
        if m.is_live
    ]

    if live:
        raise SystemExit(
            f"FAIL: {len(live)} matches still live."
        )

    print("PASS: 7 matches finished.")
    print("PASS: No matches live.")

    # STATS
    print("\n=== STATISTICS ===")

    stats = PlayerStatistic.query.all()

    print("Statistics records:", len(stats))

    if len(stats) != 8:
        raise SystemExit(
            f"FAIL: Expected 8 statistics records, got {len(stats)}."
        )

    champion_stats = PlayerStatistic.query.filter_by(
        player_id=tournament.champion_id
    ).first()

    if not champion_stats:
        raise SystemExit("FAIL: Champion statistics missing.")

    print("Champion wins:", champion_stats.wins)
    print("Champion matches:", champion_stats.matches_played)
    print("Champion goals:", champion_stats.goals)

    if champion_stats.wins < 3:
        raise SystemExit(
            "FAIL: Champion should have at least 3 wins."
        )

    print("PASS: Statistics verified.")

    # FINAL RESULT
    champion = db.session.get(
        Player,
        tournament.champion_id,
    )

    runner_up = db.session.get(
        Player,
        tournament.runner_up_id,
    )

    print("\n" + "=" * 60)
    print(" ALL CLEAN TOURNAMENT TESTS PASSED")
    print("=" * 60)

    print("Registration: PASS")
    print("8 players: PASS")
    print("Draw: PASS")
    print("Round 1: PASS")
    print("Semi-finals: PASS")
    print("Final: PASS")
    print("Champion: PASS")
    print("Runner-up: PASS")
    print("Statistics: PASS")
    print("Completion: PASS")
    print("7 matches: PASS")
    print("No live matches: PASS")

    print("\nChampion:", champion.name)
    print("Runner-up:", runner_up.name)

    print("\n" + "=" * 60)
    print(" AMINATION ESPORTS TOURNAMENT ENGINE: VERIFIED")
    print("=" * 60)
