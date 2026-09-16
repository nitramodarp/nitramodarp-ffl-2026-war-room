"""
Shared constants for the newsletter pipeline.
Nothing sensitive lives here — this file is committed to a public repo.
Secrets (API keys) come from GitHub Actions env vars only, never this file.
"""

LEAGUE_ID = "1359038733340389376"

# owner_id -> display name, confirmed via /league/{id}/users
OWNER_MAP = {
    "559519052479676416": "NateDawg726",
    "603294071460532224": "wazimo",
    "634248191616888832": "Joe",
    "699646352681959424": "Saturn75",
    "699738649817878528": "bnesmithhicks",
    "731683896370008064": "Noah742",
    "739715132984307712": "demon44",
    "801223429184409600": "mikelanta",
    "997289100904370176": "christinanesmith",
    "1133214675782643712": "BrianHixon",
    "1133216109961969664": "derekangl15",
    "1133256991046017024": "jayhix77",
}

MY_OWNER_ID = "634248191616888832"

# Sleeper league display username of the commissioner. Flagged into the
# fetched data as is_commissioner: true rather than hardcoded into any
# prompt text — get this wrong once, fix it here, never again in three
# different prompt strings.
COMMISSIONER_OWNER_USERNAME = "Saturn75"

# Confirmed owner-psychology notes to feed the newsletter's voice.
# "homer_team" (optional) is an NFL team abbreviation — if set, any pick
# fetch_draft.py finds on that team for this owner gets deterministically
# flagged as a confirmed tendency hit, instead of relying on the model to
# notice it buried in 180 picks of data.
OWNER_NOTES = {
    "wazimo": {
        "note": "Real, repeatable KC homer — reaches on Chiefs players in any format.",
        "homer_team": "KC",
    },
}

# Sleeper's player database doesn't populate full_name for team defenses
# the same way it does individual players (confirmed in production: DEF
# adds/drops were resolving to "Player DET" instead of "Detroit Lions").
# The draft-recap pipeline dodges this by using a different metadata field
# draft picks carry that transactions/matchups don't — this static mapping
# is the reliable fallback for everywhere else.
NFL_TEAM_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}


def resolve_player_name(player_id, players_db):
    """Look up a player's real name, with a guaranteed-correct fallback for
    team defenses via NFL_TEAM_NAMES rather than a generic 'Player {id}'
    placeholder."""
    player = players_db.get(str(player_id), {})
    name = player.get("full_name")
    if name:
        return name
    if player_id in NFL_TEAM_NAMES:
        return NFL_TEAM_NAMES[player_id]
    return f"Player {player_id}"

PATHS = {
    "raw_data": "newsletter/state/raw_week_data.json",
    "story_state": "newsletter/state/story_state.json",
    "newsletter_draft": "newsletter/state/newsletter_draft.json",
    "meme": "newsletter/state/meme.json",
    "template": "newsletter/templates/newsletter_template.html",
    "output_dir": "newsletter/output",
    # Draft recap special edition — separate state, doesn't touch the weekly files
    "draft_raw": "newsletter/state/draft_raw_data.json",
    "draft_recap_draft": "newsletter/state/draft_recap_draft.json",
    "draft_meme": "newsletter/state/draft_meme.json",
    "draft_template": "newsletter/templates/draft_recap_template.html",
}

# Claude model used for both newsletter copy and meme selection.
CLAUDE_MODEL = "claude-sonnet-5"
