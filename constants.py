# ============================================================
# AMINATION ESPORTS — APPLICATION CONSTANTS
# ============================================================

# ------------------------------------------------------------
# Tournament sizes
# ------------------------------------------------------------

# Founder-controlled tournament capacities.
#
# The bracket engine itself is capacity-agnostic and can calculate
# any power-of-two bracket. This tuple is intentionally no longer
# used as an artificial application-wide ceiling.
SUPPORTED_PLAYER_COUNTS = ()

DEFAULT_MAX_PLAYERS = 32


# ------------------------------------------------------------
# Tournament statuses
# ------------------------------------------------------------

TOURNAMENT_REGISTRATION = "registration"
TOURNAMENT_DRAW_RELEASED = "draw_released"
TOURNAMENT_IN_PROGRESS = "in_progress"
TOURNAMENT_PAUSED = "paused"
TOURNAMENT_COMPLETED = "completed"


# ------------------------------------------------------------
# Match statuses
# ------------------------------------------------------------

MATCH_SCHEDULED = "scheduled"
MATCH_IN_PROGRESS = "in_progress"
MATCH_LIVE = "live"
MATCH_FINISHED = "finished"


# ------------------------------------------------------------
# Bracket rounds
# ------------------------------------------------------------

ROUND_1 = "Round 1"
QUARTER_FINAL = "Quarter-Final"
SEMI_FINAL = "Semi-Final"
FINAL = "Final"
