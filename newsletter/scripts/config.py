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

# Confirmed owner-psychology notes to feed the newsletter's voice.
# Keep this list honest and update it as real 2026 behavior confirms/refutes
# these — don't let it calcify into a joke that stops being true.
OWNER_NOTES = {
    "wazimo": "Real, repeatable KC homer — reaches on Chiefs players in any format.",
}

PATHS = {
    "raw_data": "newsletter/state/raw_week_data.json",
    "story_state": "newsletter/state/story_state.json",
    "newsletter_draft": "newsletter/state/newsletter_draft.json",
    "meme": "newsletter/state/meme.json",
    "template": "newsletter/templates/newsletter_template.html",
    "output_dir": "newsletter/output",
}

# Claude model used for both newsletter copy and meme selection.
CLAUDE_MODEL = "claude-sonnet-5"
