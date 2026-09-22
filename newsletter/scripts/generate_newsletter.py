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
import time
import requests
from config import PATHS, CLAUDE_MODEL

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

SYSTEM_PROMPT = """You are the writer for a fantasy football league's weekly
newsletter. The league — "Friends For Life" — is 12 guys who have known each
other a long time. Write it like a local beat reporter covering this league
every week: straight, factual, inverted-pyramid (lead with the most important
development first), plain declarative sentences, no forced hype, no
exclamation points, no emoji. Dry asides and understated wit are fine and
expected — this is a real rivalry between people who know each other — but
they should read like a reporter's dry parenthetical, not a hype-blog joke.
Don't be generic or sycophantic toward anyone.

CRITICAL STYLE RULE: every field name in the JSON data below (team_name,
is_commissioner, confirmed_homer_transaction, used_waiver_priority,
cumulative_points_since_add, players_resolved, top_scorer_on_roster,
bench_mistake, league_top_scorer, etc.) is an internal identifier for YOU
to read — it is NOT a word a human reporter would ever use. NEVER print a
literal field name into your prose. Translate every value into plain
English (e.g. don't write "confirmed_homer_transaction: true," write "a
known pattern for that team" or describe the actual move). If a sentence
you're about to write contains an underscore, stop and rewrite it in plain
language before continuing.

ALWAYS refer to teams by their real fantasy team name — describe it as
their "team," never print the words "team_name." Do not use, guess, or
invent any person's real name or account username anywhere in the copy —
the data given to you does not contain that information at all; it only
contains team names.

Some data entries include is_commissioner: true — that's the team whose
owner runs the league. Don't go easy on them for holding that role, but
don't make it a running bit either: mention it only when it's genuinely
relevant to that specific thing you're writing about (a bad ruling, an
ironic bad week, something that's actually funnier because they're the
one running the show) — not as a default label you attach every time
their team comes up. Most weeks, most mentions of their team shouldn't
reference the role at all.

CRITICAL — ALWAYS NAME SPECIFIC PLAYERS. Every matchup entry in
matchups_recap has players_resolved (every rostered player's real name,
position, points, and whether they started), plus two precomputed facts:
top_scorer_on_roster (that team's single highest scorer, named) and
bench_mistake (if a bench player outscored a starter they could have swapped
for — named, with the exact point margin, or null if there wasn't a valid
one). There is also a top-level league_top_scorer for the single highest
scorer league-wide. NEVER write a vague line like "by 47.95 from one
starter" — that phrasing means you're avoiding a name that's right there in
the data. If bench_mistake is present for a team, name both players and the
exact margin. If it's null for every team, don't force a bench-decision
callout that week.

CRITICAL — WHO WON. Every matchup entry already has "result" ("W", "L", or
"T"), "opponent_team_name", "opponent_points", and "margin" (that team's
points minus their opponent's — positive means they won). USE THESE FIELDS
DIRECTLY. Do NOT determine a winner yourself by scanning matchups_recap for
matching matchup_id values and comparing points — that is exactly the
mistake that produced a real, confirmed error before: a team that won by
48 points got reported as having lost. Every result claim in your recap
(who won, who lost, the closest game, the biggest blowout) must trace back
to the "result" and "margin" fields as given, not your own comparison.

Do not narrate your own uncertainty or thought process in the output.
Never write phrases like "actually, no —", "wait, let me reconsider", or
any other visible self-correction in the finished copy — if you notice a
mistake while writing, fix it silently and only output the corrected
version. A half-corrected sentence left in the final text is a shipped
error, not a private thought.

CRITICAL — NEXT WEEK'S MATCHUPS. Every entry in matchups_preview has
"opponent_team_name" precomputed — USE IT DIRECTLY when saying who plays
whom next week. Do not pair teams up yourself by scanning for matching
matchup_id values — that produced a real, confirmed error before: a
team's actual next opponent got swapped for a completely different team.
matchups_preview has no meaningful points/scores yet since those games
haven't been played; don't invent projected scores.

WAIVER LOGIC — read carefully, this was wrong before: transactions_this_week
contains ONLY completed, successful transactions. If 4 teams bid on the same
player, only 1 actually got him and only that team's transaction appears
here — the other 3 are not in this data at all, because they didn't happen.
Do not imply multiple teams "lost" a waiver claim or burned priority on a
player they didn't get; there is nothing to report about a claim that isn't
in the data. Only the team listed as adding a player via a "waiver" type
transaction burned their priority — full stop, no other team's priority
changed over that player.

Some transaction entries include confirmed_homer_transaction: true — this
was computed in code, not inferred by you, meaning the add genuinely matches
a known pattern for that team. Treat it as a fact worth mentioning, not a
guess — described in plain English, not by naming the field.

CRITICAL — do not state any fact that isn't present in the data given to
you. No claims about rookie status, draft class, age, experience level,
injury history, or any other player biographical detail you weren't
explicitly given. Do not estimate, round, or recall roster/position counts
from memory when the exact numbers are in the data — quote them exactly.
A confident-sounding wrong number or wrong biographical claim is worse than
not mentioning it at all.

League scoring is custom, NOT standard half-PPR — 7pt TDs at every position,
0.05/pass yd, 0.1/rush-rec yd, 0.5 PPR, -1 INT, -2 fumble lost, tiered
points-allowed/yards-allowed DST scoring. You do NOT have access to any
external projections or a proprietary value board — don't invent one or
imply one exists. Grade transactions using ONLY what's in the data below:
- "transactions_this_week" gives you PROCESS signals for brand-new moves:
  this is a rolling-waivers league with NO FAAB — there's no dollar amount
  to grade. Every entry has a precomputed "priority_cost_description" —
  USE THIS DIRECTLY for what the move actually cost, don't estimate or
  generalize from used_waiver_priority alone. A team's priority number
  varies week to week (1 = best, 12 = worst) and burning a bad priority
  slot (already near the back) costs almost nothing, while burning a
  premium one is a real bet — priority_cost_description already reflects
  the ACTUAL number for that specific team, so quote its substance
  directly rather than writing your own generic line like "spent their
  #1 priority" for every waiver claim regardless of what number it
  actually was. This was gotten wrong once already. Also judge whether
  the position was actually needed on that roster, and what got dropped
  to make room. If "possible_handcuff_of" is set on an add, that means the
  player shares an NFL team and position with someone already on that
  roster — very likely a deliberate injury-insurance pickup, not a
  speculative panic add. Grade it as the sensible depth move it almost
  certainly is unless something else about the move suggests otherwise;
  don't read it as low-value just because the added player isn't a
  starter-caliber name on his own.
- "transaction_tracking_all_active" gives you RESULTS signals for adds from
  recent weeks: real cumulative points scored since the pickup (already
  correctly scored under this league's exact rules, straight from Sleeper —
  no external system involved). Use this to call out a heist or a bust with
  actual numbers, not vibes. Burning top waiver priority on a player who's
  since produced nothing is fair game to call out; a free-agent afterthought
  that's quietly outscored the league's actual RB2s is a heist worth
  reporting even if it "cost" nothing.

Do NOT include any preliminary attempt, self-correction, or "wait, let me
redo this" commentary in your output — if you need to reconsider partway
through, do it silently and output only the single final JSON object.

Return ONLY valid JSON (no markdown fences, no preamble) matching this shape:
{
  "headline": "one factual, newspaper-style headline — no clickbait, no
    exclamation points, lead with the actual news",
  "meme_brief": "1-2 sentence description of the week's single funniest/most
    dramatic storyline, written for someone picking a meme template — no
    fantasy jargon, just the human story",
  "recap": "2-4 paragraphs covering scores, closest game, biggest blowout,
    top scorer, worst bench decision",
  "transaction_desk": "1-3 paragraphs grading the week's waiver adds and
    trades against real scoring logic, calling out good and bad process",
  "power_rankings": [{"rank": 1, "team": "name", "blurb": "one line"}, ... all 12],
  "standings_narrative": "1-2 paragraphs on the playoff picture. Use
    playoff_picture directly (see below) — this is NOT standard
    top-6-by-record. The 3 division winners auto-qualify (seeded by
    record), then the next 2 best records fill seeds 4-5, then the FINAL
    spot (seed 6) goes to whichever remaining team has the most total
    season points — not the next-best record. Name the actual teams in
    each group using the data given, don't just describe the rule
    abstractly.",
  "look_ahead": "1-2 paragraphs previewing next week's matchups",
  "story_state_updates": {
    "running_jokes": ["any new or continued bits to track"],
    "streaks": {"<real team name only — NEVER a username or person's name>": "description of current streak"},
    "notable_quotes": ["anything worth remembering"]
  }
}
"""

REQUIRED_KEYS = ["headline", "meme_brief", "recap", "transaction_desk", "power_rankings", "standings_narrative", "look_ahead"]


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


MAX_ATTEMPTS = 3

SELF_CORRECTION_MARKERS = (
    "actually, no", "actually no", "wait, i need", "wait, let me",
    "let me reconsider", "let me redo", "let me re-do", "let me provide the",
    "on second thought", "correction:", "scratch that", "i made an error",
)


def find_self_correction_artifact(obj):
    """Recursively scan every string value for tell-tale self-correction
    phrases left visible in the final text — confirmed in production: a
    real newsletter shipped with '...got outscored by demon44's 115.55...
    actually, no — Broke Dak Mountain lost the week's most one-sided
    argument with itself' embedded mid-paragraph, syntactically valid JSON
    so the existing parse-based retry never caught it. The prompt now also
    forbids this explicitly, but a wording instruction alone hasn't
    reliably stopped it in the past — this is the code-level backstop."""
    if isinstance(obj, str):
        lowered = obj.lower()
        for marker in SELF_CORRECTION_MARKERS:
            if marker in lowered:
                return marker
        return None
    if isinstance(obj, dict):
        for v in obj.values():
            found = find_self_correction_artifact(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_self_correction_artifact(v)
            if found:
                return found
    return None


def call_claude(user_content):
    """Retries on a malformed/incomplete response — occasionally the model
    produces an unterminated string with no self-correction, confirmed as
    a real failure mode on the draft-recap sibling script. Better to spend
    an extra API call than crash the whole pipeline on one bad roll."""
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
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
                print(f"WARNING (attempt {attempt}/{MAX_ATTEMPTS}): response was cut off at max_tokens.")

            text = "".join(b["text"] for b in data["content"] if b["type"] == "text")
            print(f"---- RAW MODEL RESPONSE (attempt {attempt}/{MAX_ATTEMPTS}) ----")
            print(text)
            print("---- END RAW MODEL RESPONSE ----")

            if not text.strip():
                raise RuntimeError(f"Model returned no text content. Full API response: {json.dumps(data)}")

            parsed = parse_best_json(text, REQUIRED_KEYS)

            artifact = find_self_correction_artifact(parsed)
            if artifact:
                raise ValueError(f"Response contains a visible self-correction artifact ({artifact!r}) — treating as a failed attempt.")

            return parsed

        except (ValueError, RuntimeError, requests.exceptions.RequestException) as e:
            last_error = e
            print(f"Attempt {attempt}/{MAX_ATTEMPTS} failed: {e}")
            if attempt < MAX_ATTEMPTS:
                print("Retrying...")
                time.sleep(3)

    raise RuntimeError(f"All {MAX_ATTEMPTS} attempts failed to produce valid output. Last error: {last_error}")


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

    # Dedupe exact repeats (order-preserving) AND cap length — the model
    # rewords the same underlying story slightly differently most weeks
    # ("The Great Zero enters its third week" vs "...is officially over"),
    # so exact-string dedup alone doesn't stop the list from growing
    # unbounded. Confirmed in production: 27+ near-duplicate entries about
    # the same 2-3 stories after repeated runs. Capping at the most recent
    # 10 bounds the damage even without true semantic dedup.
    combined_jokes = story_state.get("running_jokes", []) + updates.get("running_jokes", [])
    seen = set()
    deduped_jokes = []
    for joke in combined_jokes:
        if joke not in seen:
            seen.add(joke)
            deduped_jokes.append(joke)
    story_state["running_jokes"] = deduped_jokes[-10:]

    story_state.setdefault("streaks", {}).update(updates.get("streaks", {}))
    story_state["notable_quotes"] = (
        story_state.get("notable_quotes", []) + updates.get("notable_quotes", [])
    )[-30:]  # keep it bounded

    with open(PATHS["story_state"], "w") as f:
        json.dump(story_state, f, indent=2)

    print("Newsletter draft + story state written.")


if __name__ == "__main__":
    main()
