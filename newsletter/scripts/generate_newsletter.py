"""
Step 2 of the pipeline: turn raw Sleeper data + persistent story-state into
actual newsletter copy, via the Claude API.

Writes:
  - newsletter/state/newsletter_draft.json   (the copy, structured by section)
  - newsletter/state/story_state.json        (updated with this week's events)
"""

import json
import os
import re
import requests
from config import PATHS, CLAUDE_MODEL, OWNER_NOTES

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

SYSTEM_PROMPT = """You are the ghostwriter for a fantasy football league's weekly
newsletter. The league — "Friends For Life" — is 12 guys who have been friends
for 40 years. Tone: ESPN-style recap structure, but with real trash talk aimed
at actual people by name. These guys can take it. Don't be generic or
sycophantic toward anyone, including the commissioner (Joe). If someone made a
bad decision, say so plainly and make fun of it.

League scoring is custom, NOT standard half-PPR — 7pt TDs at every position,
0.05/pass yd, 0.1/rush-rec yd, 0.5 PPR, -1 INT, -2 fumble lost, tiered
points-allowed/yards-allowed DST scoring. You do NOT have access to any
external projections or a proprietary value board — don't invent one or
imply one exists. Grade transactions using ONLY what's in the data below:
- "transactions_this_week" gives you PROCESS signals for brand-new moves:
  this is a rolling-waivers league with NO FAAB — there's no dollar amount
  to grade. Instead judge whether the move was a "waiver" claim (burned
  priority, sends that roster to the back of the order) or a "free_agent"
  pickup (free, uncontested — nobody else wanted him). A team spending its
  #1 priority slot is making a real bet; a free-agent add off the wire the
  day after cut day isn't. Also judge whether the position was actually
  needed on that roster, and what got dropped to make room.
- "transaction_tracking_all_active" gives you RESULTS signals for adds from
  recent weeks: real cumulative points scored since the pickup (already
  correctly scored under this league's exact rules, straight from Sleeper —
  no external system involved). Use this to call out a heist or a bust with
  actual numbers, not vibes. Burning top waiver priority on a player who's
  since produced nothing is fair game to roast by name; a free-agent
  afterthought that's quietly outscored the league's actual RB2s is a heist
  worth celebrating even if it "cost" nothing.

Known owner tendencies (use sparingly, only when the week's actual events
support it — don't force a running bit that isn't earned this week):
""" + "\n".join(f"- {name}: {note}" for name, note in OWNER_NOTES.items()) + """

Return ONLY valid JSON (no markdown fences, no preamble) matching this shape:
{
  "headline": "one punchy headline for the week",
  "meme_brief": "1-2 sentence description of the week's single funniest/most
    dramatic storyline, written for someone picking a meme template — no
    fantasy jargon, just the human story",
  "recap": "2-4 paragraphs covering scores, closest game, biggest blowout,
    top scorer, worst bench decision",
  "transaction_desk": "1-3 paragraphs grading the week's waiver adds and
    trades against real VORP/scoring logic, calling out good and bad process",
  "power_rankings": [{"rank": 1, "team": "name", "blurb": "one line"}, ... all 12],
  "standings_narrative": "1-2 paragraphs on the playoff picture, who's on
    the bubble with 6 teams making it",
  "look_ahead": "1-2 paragraphs previewing next week's matchups",
  "story_state_updates": {
    "running_jokes": ["any new or continued bits to track"],
    "streaks": {"team_name": "description of current streak"},
    "notable_quotes": ["anything worth remembering"]
  }
}
"""


def extract_json(text):
    """Pull the JSON object out of a model response regardless of how it's
    wrapped — code fence, stray preamble sentence, trailing commentary,
    whatever. Slicing between the first '{' and last '}' is more robust
    than trying to regex-match a specific fence format, and is what
    actually failed last time (a fence-stripping regex reduced valid
    output to an empty string)."""
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
            "max_tokens": 4000,
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

    return json.loads(extract_json(text))


def main():
    with open(PATHS["raw_data"]) as f:
        raw = json.load(f)
    with open(PATHS["story_state"]) as f:
        story_state = json.load(f)

    user_content = json.dumps({
        "this_week_raw_data": raw,
        "persistent_story_state_so_far": story_state,
        "instructions": "Write this week's newsletter. Use persistent_story_state_so_far for continuity but don't force references that don't fit — only callback a running joke if this week's data actually supports it."
    }, indent=2)

    draft = call_claude(user_content)

    with open(PATHS["newsletter_draft"], "w") as f:
        json.dump(draft, f, indent=2)

    # Fold this week's updates into persistent state
    updates = draft.get("story_state_updates", {})
    story_state["last_week_updated"] = raw["week_recapped"]
    story_state["running_jokes"] = list(set(
        story_state.get("running_jokes", []) + updates.get("running_jokes", [])
    ))
    story_state.setdefault("streaks", {}).update(updates.get("streaks", {}))
    story_state["notable_quotes"] = (
        story_state.get("notable_quotes", []) + updates.get("notable_quotes", [])
    )[-30:]  # keep it bounded

    with open(PATHS["story_state"], "w") as f:
        json.dump(story_state, f, indent=2)

    print("Newsletter draft + story state written.")


if __name__ == "__main__":
    main()
