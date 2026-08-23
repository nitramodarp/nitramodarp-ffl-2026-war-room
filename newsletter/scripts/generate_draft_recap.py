"""
Draft recap, step 2: turn the structured draft data into copy, via Claude.
Mirrors generate_newsletter.py's fixes (thinking disabled, generous
max_tokens, lenient JSON parsing) since those are proven necessary.
"""

import json
import os
import requests
from config import PATHS, CLAUDE_MODEL, OWNER_NOTES

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

SYSTEM_PROMPT = """You are writing a one-off "Draft Recap" special edition
for a fantasy football league's newsletter — 12 guys, "Friends For Life,"
40 years of friendship, real trash talk aimed at people by name. Same voice
as the weekly newsletter: ESPN recap structure with real edge, not generic
or sycophantic toward anyone including the commissioner (Joe).

IMPORTANT — you have NO external ADP, rankings, or proprietary value board.
Every "reach," "steal," or "value" claim you make must be grounded ONLY in
this draft's own internal pick order — e.g. "first RB off the board," "took
a TE five rounds before anyone else touched the position," "part of a run
where 4 WRs went in a 5-pick window." Do not claim a pick was good or bad
value against real-world consensus you don't have access to — you can only
compare picks to what THIS ROOM did with THIS draft. Frame it that way
explicitly when useful ("relative to how the rest of the room drafted the
position...").

Known owner tendencies (use only if the actual picks support it):
""" + "\n".join(f"- {name}: {note}" for name, note in OWNER_NOTES.items()) + """

Return ONLY valid JSON (no markdown fences, no preamble) matching this shape:
{
  "headline": "one punchy headline for the draft",
  "meme_brief": "1-2 sentence description of the draft's funniest/most
    dramatic single moment, written for someone picking a meme template —
    no fantasy jargon, just the human story",
  "draft_narrative": "3-5 paragraphs on how the draft actually unfolded —
    positional runs, any surprising early picks, how the room handled
    the position most people take too early or too late",
  "team_grades": [{"team": "name", "grade": "A-", "blurb": "1-2 sentences
    on their draft, grounded in what they actually took, roast or praise
    honestly"}, ... one entry per team, all 12],
  "standout_picks": "2-3 paragraphs calling out the most notable picks —
    biggest position-relative reach, most patient value pick (last player
    at a position other teams grabbed early), and anything a specific
    owner's tendencies predicted (e.g. a KC stack)",
  "looking_ahead": "1-2 paragraphs hyping up the season kicking off, keep
    it short since there's no real matchup data yet"
}
"""


def extract_json(text):
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object found in model response. Raw text was:\n{text}")
    return text[start:end + 1]


def call_claude(user_content):
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": 8000,
            "thinking": {"type": "disabled"},
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_content}],
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("stop_reason") == "max_tokens":
        print("WARNING: response was cut off at max_tokens — output may be truncated/incomplete.")

    text = "".join(b["text"] for b in data["content"] if b["type"] == "text")
    print("---- RAW MODEL RESPONSE (for debugging) ----")
    print(text)
    print("---- END RAW MODEL RESPONSE ----")

    if not text.strip():
        raise RuntimeError(f"Model returned no text content. Full API response: {json.dumps(data)}")

    return json.loads(extract_json(text), strict=False)


def main():
    with open(PATHS["draft_raw"]) as f:
        raw = json.load(f)

    user_content = json.dumps({"draft_data": raw}, indent=2)
    draft_recap = call_claude(user_content)

    with open(PATHS["draft_recap_draft"], "w") as f:
        json.dump(draft_recap, f, indent=2)

    print("Draft recap draft written.")


if __name__ == "__main__":
    main()
