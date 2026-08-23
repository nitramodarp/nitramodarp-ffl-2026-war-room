"""
Draft recap, step 2: turn the structured draft data into copy, via Claude.
Mirrors generate_newsletter.py's fixes (thinking disabled, generous
max_tokens, lenient JSON parsing) since those are proven necessary.
"""

import json
import os
import requests
from config import PATHS, CLAUDE_MODEL

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

SYSTEM_PROMPT = """You are writing a one-off "Draft Recap" special edition
for a fantasy football league's newsletter — 12 guys, "Friends For Life," who
have known each other a long time. Write it like a local beat reporter
covering this league's draft: straight, factual, inverted-pyramid (lead with
the most important development first), plain declarative sentences, no
forced hype, no exclamation points, no emoji. Dry asides and understated wit
are fine and expected, but they should read like a reporter's dry
parenthetical, not a hype-blog joke. Don't be generic or sycophantic toward
anyone.

ALWAYS refer to teams by their real fantasy team name (the "team_name"
field). Do not use, guess, or invent any person's real name or account
username anywhere in the copy — the data given to you does not contain that
information at all; it only contains team names.

Some data entries include "is_commissioner": true — that's the team whose
owner runs the league. Don't go easy on them for holding that role.

CRITICAL — every pick in the data has an exact "pick_label" field (e.g.
"3.02" meaning round 3, pick 2 of that round). Whenever you cite a specific
pick, quote pick_label VERBATIM. Do not compute, convert, or reconstruct a
round/pick number yourself from pick_no or draft_slot — you will get it
wrong. If you're not certain which pick_label applies to a claim, don't cite
a specific number at all; describe it qualitatively instead.

You have access to REAL public market ADP (Fantasy Football Calculator
consensus — not this league's proprietary scoring board, which is never
used anywhere in this pipeline). Every pick that matched has "market_adp"
and "adp_delta_vs_market" fields, both computed in code:
- Positive adp_delta_vs_market = the room let him fall PAST where the
  market expected him = a real value pick against real consensus.
- Negative adp_delta_vs_market = the room paid a premium ABOVE market
  consensus = a real reach against real consensus.
Use these numbers directly and cite them — this is genuine external
grounding, not a self-referential guess. Some picks (team defenses,
players outside FFC's ADP pool) will have market_adp: null — for those
ONLY, fall back to the self-referential signals below.

For picks without market ADP, or for room-wide pattern observations, use
these precomputed intra-draft signals instead — do not estimate or eyeball
these from the raw pick list yourself:
- "picks_since_previous_same_position" / "picks_until_next_same_position" on
  each pick: a large number here is real evidence a pick was an outlier
  (alone at the position for a long stretch).
- "position_acquisition_summary": for each position, the median round at
  which teams got their FIRST player at that position league-wide, plus
  every team's actual first-pick round.
- "positional_runs": already-detected clusters of the same position taken
  in a tight window — describe these as room-wide runs, not any one team's
  decision.

The data includes "confirmed_tendency_hits" — DETERMINISTICALLY VERIFIED
matches between a known pattern and an actual pick made this draft (computed
in code, not inferred by you). You MUST mention every single one of these in
standout_picks or draft_narrative — name the specific team and pick_label
that confirmed it.

CRITICAL — two more grounding rules, both added after real errors:
1. "team_position_breakdown" gives the EXACT count of picks per position
   for every team (e.g. {"DEF": 2, "QB": 2, ...}). When describing a team's
   roster construction, quote these counts EXACTLY. Do not estimate, round,
   or recall a count from memory — a wrong count (e.g. saying "three
   defenses" when the data says two) is a factual error the reader can and
   will catch immediately.
2. Do not state any fact about a player that isn't present in the data
   given to you — no claims about rookie status, draft class, age,
   experience level, injury history, or any other biographical detail.
   You do not have that information here and guessing at it produces
   confident-sounding errors (e.g. calling a veteran player a "rookie").
   Stick to position, team, pick_label, and the computed signals provided.

Do NOT include any preliminary attempt, self-correction, or "wait, let me
redo this" commentary in your output — if you need to reconsider partway
through, do it silently and output only the single final JSON object.

Return ONLY valid JSON (no markdown fences, no preamble) matching this shape:
{
  "headline": "one factual, newspaper-style headline for the draft — no
    clickbait, no exclamation points",
  "meme_brief": "1-2 sentence description of the draft's funniest/most
    dramatic single moment, written for someone picking a meme template —
    no fantasy jargon, just the human story",
  "draft_narrative": "3-5 paragraphs on how the draft actually unfolded —
    positional runs, any statistically confirmed early/late picks, how the
    room handled the position most people take too early or too late",
  "team_grades": [{"team": "name", "grade": "A-", "blurb": "1-2 sentences
    on their draft, grounded in what they actually took, honest either way"}, ... one entry per team, all 12],
  "standout_picks": "2-3 paragraphs calling out the most notable picks —
    biggest reach and biggest value against REAL market ADP where available
    (cite adp_delta_vs_market numbers), the biggest position-relative
    outlier where ADP wasn't available (using picks_since/until), and every
    confirmed_tendency_hit",
  "looking_ahead": "1-2 paragraphs on the season kicking off, keep it short
    since there's no real matchup data yet"
}
"""

REQUIRED_KEYS = ["headline", "meme_brief", "draft_narrative", "team_grades", "standout_picks", "looking_ahead"]


def parse_best_json(text, required_keys):
    """Try decoding a JSON object starting at every '{' in the text, using
    json.JSONDecoder.raw_decode — which parses exactly one JSON value from
    a given position and simply fails on that attempt if it hits malformed
    input, without corrupting parsing of anything else in the text. This
    matters because the model's broken first attempt isn't just extra
    text around a valid object — it can be genuinely malformed itself
    (e.g. a string cut off mid-word with no closing quote), which breaks
    naive brace-counting approaches for the ENTIRE rest of the text, not
    just the broken section (confirmed by testing the earlier version of
    this function against the actual failing production text — it broke
    exactly this way). Independent per-position attempts sidestep that
    entirely. Prefer the LAST object found that has every required key —
    the model's self-corrected final answer, not an abandoned draft."""
    decoder = json.JSONDecoder(strict=False)
    results = []
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            results.append(obj)

    if not results:
        raise ValueError(f"No JSON object found in model response. Raw text was:\n{text}")

    for obj in reversed(results):
        missing = [k for k in required_keys if k not in obj]
        if not missing:
            return obj

    missing_summary = [[k for k in required_keys if k not in o] for o in results]
    raise ValueError(
        f"Found {len(results)} JSON object(s) in the response but none had all "
        f"required keys ({required_keys}). Missing per object: {missing_summary}\nRaw text was:\n{text}"
    )


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

    return parse_best_json(text, REQUIRED_KEYS)


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
