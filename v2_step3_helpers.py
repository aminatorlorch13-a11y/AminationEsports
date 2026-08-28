# ============================================================
# TOURNAMENT HELPERS — V2 BRACKET ENGINE
# ============================================================

def tournament_bracket_capacity(player_count):
    try:
        player_count = int(player_count or 0)
    except (TypeError, ValueError):
        player_count = 0

    if player_count <= 2:
        return 2
    if player_count <= 4:
        return 4
    if player_count <= 8:
        return 8
    if player_count <= 16:
        return 16
    if player_count <= 32:
        return 32

    raise ValueError(
        "AminationEsports supports a maximum of 32 players."
    )


def tournament_rounds(player_count):
    capacity = tournament_bracket_capacity(player_count)

    if capacity == 2:
        return [FINAL]

    if capacity == 4:
        return [
            SEMI_FINAL,
            FINAL
        ]

    if capacity == 8:
        return [
            QUARTER_FINAL,
            SEMI_FINAL,
            FINAL
        ]

    if capacity == 16:
        return [
            ROUND_1,
            QUARTER_FINAL,
            SEMI_FINAL,
            FINAL
        ]

    return [
        ROUND_1,
        "Round 2",
        QUARTER_FINAL,
        SEMI_FINAL,
        FINAL
    ]


def next_round_name(current_round, rounds):
    if not current_round or not rounds:
        return None

    if current_round not in rounds:
        return None

    index = rounds.index(current_round)

    if index >= len(rounds) - 1:
        return None

    return rounds[index + 1]


def calculate_bye_count(player_count):
    capacity = tournament_bracket_capacity(player_count)

    try:
        player_count = int(player_count or 0)
    except (TypeError, ValueError):
        player_count = 0

    return max(capacity - player_count, 0)


def calculate_bye_positions(player_count):
    capacity = tournament_bracket_capacity(player_count)
    bye_count = calculate_bye_count(player_count)

    if bye_count <= 0:
        return []

    positions = []

    for index in range(bye_count):
        position = int(
            round(
                ((index + 0.5) * capacity / bye_count) - 0.5
            )
        )

        position = max(
            0,
            min(position, capacity - 1)
        )

        if position not in positions:
            positions.append(position)

    if len(positions) < bye_count:
        for position in range(capacity):
            if position not in positions:
                positions.append(position)

            if len(positions) >= bye_count:
                break

    return sorted(positions)


def build_round_one_slots(players):
    players = list(players or [])
    player_count = len(players)

    if player_count < 2:
        return []

    capacity = tournament_bracket_capacity(player_count)

    bye_positions = set(
        calculate_bye_positions(player_count)
    )

    slots = []
    player_index = 0

    for position in range(capacity):

        if position in bye_positions:
            slots.append(
                {
                    "position": position + 1,
                    "player": None,
                    "is_bye": True
                }
            )
        else:
            slots.append(
                {
                    "position": position + 1,
                    "player": players[player_index],
                    "is_bye": False
                }
            )

            player_index += 1

    return slots


def build_round_one_pairings(players):
    slots = build_round_one_slots(players)
    pairings = []

    for index in range(0, len(slots), 2):

        player1_slot = slots[index]
        player2_slot = slots[index + 1]

        pairings.append(
            {
                "match_number": index // 2 + 1,
                "bracket_position": index // 2 + 1,

                "player1": player1_slot["player"],
                "player2": player2_slot["player"],

                "player1_is_bye":
                    player1_slot["is_bye"],

                "player2_is_bye":
                    player2_slot["is_bye"],

                "is_bye":
                    (
                        player1_slot["is_bye"]
                        or
                        player2_slot["is_bye"]
                    )
            }
        )

    return pairings


def get_player_match(player_id):
    if not player_id:
        return None

    active_match = Match.query.filter(
        or_(
            Match.player1_id == player_id,
            Match.player2_id == player_id
        ),
        Match.status.in_(
            [
                MATCH_SCHEDULED,
                MATCH_IN_PROGRESS,
                MATCH_LIVE
            ]
        )
    ).order_by(
        Match.id.desc()
    ).first()

    if active_match:
        return active_match

    return Match.query.filter(
        or_(
            Match.player1_id == player_id,
            Match.player2_id == player_id
        )
    ).order_by(
        Match.id.desc()
    ).first()


def _set_next_match_player(next_match, winner_id, source_match):
    if not next_match or not winner_id:
        return False

    current_position = source_match.bracket_position

    if current_position is None:
        return False

    if (int(current_position) - 1) % 2 == 0:

        next_match.player1_id = winner_id
        next_match.source_match1_id = source_match.id

    else:

        next_match.player2_id = winner_id
        next_match.source_match2_id = source_match.id

    return True


def create_next_round_match(tournament, finished_match):
    if not tournament:
        return None

    if not finished_match:
        return None

    if not finished_match.winner_id:
        return None

    rounds = tournament_rounds(
        tournament.max_players
    )

    next_round = next_round_name(
        finished_match.round_name,
        rounds
    )

    if not next_round:
        return None

    current_position = finished_match.bracket_position

    if current_position is None:
        return None

    next_position = (
        ((int(current_position) - 1) // 2) + 1
    )

    next_round_number = (
        rounds.index(next_round) + 1
    )

    next_match = Match.query.filter_by(
        tournament_id=tournament.id,
        round_name=next_round,
        bracket_position=next_position
    ).first()

    if next_match is None:

        next_match = Match(
            tournament_id=tournament.id,

            player1_id=None,
            player2_id=None,

            player1_score=0,
            player2_score=0,

            status=MATCH_SCHEDULED,

            round_name=next_round,
            round_number=next_round_number,

            match_number=next_position,
            bracket_position=next_position,

            source_match1_id=None,
            source_match2_id=None,

            is_bye=False,
            bye_reason=None,

            is_forfeit=False,
            forfeit_player_id=None,
            forfeit_reason=None,

            winner_id=None,
            loser_id=None,

            is_live=False
        )

        db.session.add(next_match)
        db.session.flush()

    _set_next_match_player(
        next_match,
        finished_match.winner_id,
        finished_match
    )

    db.session.flush()

    return next_match


def advance_bye_match(tournament, match):
    if not tournament:
        return None

    if not match:
        return None

    if not match.is_bye:
        return None

    player1 = match.player1_id
    player2 = match.player2_id

    if player1 and not player2:
        winner_id = player1

    elif player2 and not player1:
        winner_id = player2

    else:
        return None

    match.winner_id = winner_id
    match.loser_id = None

    match.status = MATCH_FINISHED
    match.finished_at = datetime.utcnow()
    match.is_live = False

    return create_next_round_match(
        tournament,
        match
    )


def resolve_forfeit_match(
    tournament,
    match,
    forfeiting_player_id,
    reason=None
):
    if not tournament:
        return None

    if not match:
        return None

    if forfeiting_player_id not in (
        match.player1_id,
        match.player2_id
    ):
        return None

    if forfeiting_player_id == match.player1_id:
        winner_id = match.player2_id
    else:
        winner_id = match.player1_id

    if not winner_id:
        return None

    match.is_forfeit = True
    match.forfeit_player_id = forfeiting_player_id
    match.forfeit_reason = reason

    match.winner_id = winner_id
    match.loser_id = forfeiting_player_id

    match.status = MATCH_FINISHED
    match.finished_at = datetime.utcnow()
    match.is_live = False

    return create_next_round_match(
        tournament,
        match
    )
