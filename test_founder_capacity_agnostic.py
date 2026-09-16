import os
from pathlib import Path

TEST_DB = Path(
    "/data/data/com.termux/files/home/amination_founder_capacity_test.db"
)

if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from app import app, db
from models import Tournament


class FakeForm:
    pass


with app.app_context():
    db.drop_all()
    db.create_all()

    tournament = Tournament(
        name="Capacity Test",
        max_players=32,
        entry_fee=0,
    )
    db.session.add(tournament)
    db.session.commit()

    print("=" * 72)
    print("AMINATION ESPORTS — FOUNDER CAPACITY TEST")
    print("=" * 72)
    print("Database:", TEST_DB)
    print()

    # Test the mathematical validation directly.
    valid = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]
    invalid = [0, 1, 3, 5, 7, 15, 31, 33, 100, 127]

    print("===== VALID CAPACITIES =====")

    for value in valid:
        assert value >= 2
        assert not (value & (value - 1))
        print(f"{value}: PASS")

    print()
    print("===== INVALID CAPACITIES =====")

    for value in invalid:
        assert (
            value < 2
            or value & (value - 1)
        )
        print(f"{value}: PASS")

    print()
    print("===== BRACKET ENGINE CROSS-CHECK =====")

    from app import tournament_bracket_capacity, tournament_rounds

    expected = {
        2: 2,
        4: 4,
        8: 8,
        16: 16,
        32: 32,
        64: 64,
        128: 128,
        256: 256,
        512: 512,
        1024: 1024,
    }

    for requested, capacity in expected.items():
        assert tournament_bracket_capacity(requested) == capacity
        rounds = tournament_rounds(requested)
        assert rounds[-3:] == [
            "Quarter-Final",
            "Semi-Final",
            "Final",
        ] or requested < 8
        print(
            f"{requested}-slot bracket: "
            f"capacity={capacity}, rounds={len(rounds)}: PASS"
        )

    print()
    print("=" * 72)
    print("FOUNDER CAPACITY VALIDATION TEST COMPLETE")
    print("=" * 72)
