from app import (
    tournament_bracket_capacity,
    tournament_rounds,
    calculate_bye_count,
    calculate_bye_positions,
    build_round_one_slots,
    build_round_one_pairings,
)


print("=" * 70)
print("AMINATION ESPORTS — CAPACITY-AGNOSTIC BRACKET ENGINE TEST")
print("=" * 70)


# ============================================================
# TEST 1 — BRACKET CAPACITY CALCULATION
# ============================================================

capacity_cases = [
    (2, 2),
    (3, 4),
    (4, 4),
    (5, 8),
    (8, 8),
    (9, 16),
    (16, 16),
    (17, 32),
    (32, 32),
    (33, 64),
    (64, 64),
    (65, 128),
    (100, 128),
    (129, 256),
    (256, 256),
    (257, 512),
    (512, 512),
    (513, 1024),
    (1024, 1024),
]

print()
print("===== TEST 1 — GENERIC POWER-OF-TWO CAPACITY =====")

for player_count, expected_capacity in capacity_cases:
    actual_capacity = tournament_bracket_capacity(player_count)

    assert actual_capacity == expected_capacity, (
        f"{player_count} players should produce "
        f"a {expected_capacity}-slot bracket, "
        f"got {actual_capacity}"
    )

    print(
        f"{player_count:>4} players -> "
        f"{actual_capacity:>4}-slot bracket: PASS"
    )


# ============================================================
# TEST 2 — DYNAMIC ROUND GENERATION
# ============================================================

round_cases = {
    2: ["Final"],
    4: ["Semi-Final", "Final"],
    8: ["Quarter-Final", "Semi-Final", "Final"],
    16: [
        "Round 1",
        "Quarter-Final",
        "Semi-Final",
        "Final",
    ],
    32: [
        "Round 1",
        "Round 2",
        "Quarter-Final",
        "Semi-Final",
        "Final",
    ],
    64: [
        "Round 1",
        "Round 2",
        "Round 3",
        "Quarter-Final",
        "Semi-Final",
        "Final",
    ],
    128: [
        "Round 1",
        "Round 2",
        "Round 3",
        "Round 4",
        "Quarter-Final",
        "Semi-Final",
        "Final",
    ],
    256: [
        "Round 1",
        "Round 2",
        "Round 3",
        "Round 4",
        "Round 5",
        "Quarter-Final",
        "Semi-Final",
        "Final",
    ],
}

print()
print("===== TEST 2 — DYNAMIC ROUND GENERATION =====")

for capacity, expected_rounds in round_cases.items():
    actual_rounds = tournament_rounds(capacity)

    assert actual_rounds == expected_rounds, (
        f"{capacity}-slot bracket produced "
        f"unexpected rounds:\n"
        f"Expected: {expected_rounds}\n"
        f"Actual:   {actual_rounds}"
    )

    print(
        f"{capacity:>4}-slot bracket -> "
        f"{len(actual_rounds)} rounds: PASS"
    )


# ============================================================
# TEST 3 — BYE COUNT
# ============================================================

bye_cases = [
    (2, 2, 0),
    (3, 4, 1),
    (4, 4, 0),
    (5, 8, 3),
    (8, 8, 0),
    (17, 32, 15),
    (32, 32, 0),
    (33, 64, 31),
    (57, 64, 7),
    (64, 64, 0),
    (100, 128, 28),
]

print()
print("===== TEST 3 — GENERIC BYE CALCULATION =====")

for player_count, expected_capacity, expected_byes in bye_cases:
    actual_capacity = tournament_bracket_capacity(player_count)
    actual_byes = calculate_bye_count(player_count)

    assert actual_capacity == expected_capacity
    assert actual_byes == expected_byes, (
        f"{player_count} players should have "
        f"{expected_byes} BYEs, got {actual_byes}"
    )

    print(
        f"{player_count:>4} players -> "
        f"{actual_byes:>4} BYEs: PASS"
    )


# ============================================================
# TEST 4 — BYE POSITIONS
# ============================================================

print()
print("===== TEST 4 — BYE POSITION INTEGRITY =====")

for player_count in [3, 5, 9, 17, 33, 57, 100, 129]:
    capacity = tournament_bracket_capacity(player_count)
    bye_count = calculate_bye_count(player_count)
    positions = calculate_bye_positions(player_count)

    assert len(positions) == bye_count
    assert len(set(positions)) == len(positions)

    for position in positions:
        assert 0 <= position < capacity

    print(
        f"{player_count:>4} players -> "
        f"{capacity:>4} slots / "
        f"{bye_count:>4} BYEs: PASS"
    )


# ============================================================
# TEST 5 — ROUND ONE SLOT GENERATION
# ============================================================

print()
print("===== TEST 5 — GENERIC ROUND ONE SLOT GENERATION =====")

for player_count in [2, 3, 5, 8, 17, 32, 33, 57, 64, 100]:
    players = [
        f"Player-{index + 1}"
        for index in range(player_count)
    ]

    capacity = tournament_bracket_capacity(player_count)
    slots = build_round_one_slots(players)

    assert len(slots) == capacity

    real_players = [
        slot["player"]
        for slot in slots
        if slot["player"] is not None
    ]

    bye_slots = [
        slot
        for slot in slots
        if slot["is_bye"]
    ]

    assert len(real_players) == player_count
    assert len(bye_slots) == capacity - player_count

    print(
        f"{player_count:>4} players -> "
        f"{capacity:>4} slots: PASS"
    )


# ============================================================
# TEST 6 — ROUND ONE PAIRINGS
# ============================================================

print()
print("===== TEST 6 — GENERIC ROUND ONE PAIRINGS =====")

for player_count in [2, 4, 8, 16, 32, 33, 57, 64, 100, 128]:
    players = [
        f"Player-{index + 1}"
        for index in range(player_count)
    ]

    capacity = tournament_bracket_capacity(player_count)
    pairings = build_round_one_pairings(players)

    assert len(pairings) == capacity // 2

    player_names = []

    for pairing in pairings:
        player_names.extend(
            [
                pairing["player1"],
                pairing["player2"],
            ]
        )

    actual_players = [
        player
        for player in player_names
        if player is not None
    ]

    assert len(actual_players) == player_count
    assert len(set(actual_players)) == player_count

    print(
        f"{player_count:>4} players -> "
        f"{len(pairings):>4} first-round matches: PASS"
    )


# ============================================================
# FINAL RESULT
# ============================================================

print()
print("=" * 70)
print("CAPACITY-AGNOSTIC ENGINE TEST COMPLETE")
print("=" * 70)
print()
print(
    "These tests define the future architecture:"
)
print(
    "Founder-controlled capacity -> dynamic bracket -> dynamic rounds"
)
print(
    "No architectural 32-player ceiling."
)
print()
