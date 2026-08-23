"""
Draft recap, step 1: pull the completed draft from Sleeper and build
structured, fully self-referential "value" signals — no external ADP or
proprietary board involved anywhere, per the same rule as the weekly
pipeline. Everything here is derived from THIS draft's own pick order:
- position_rank_at_pick: this was the Nth player at that position taken
- positional runs: 3+ of the same position taken in a tight run of picks
- "first at position" / "last at position": purely descriptive of this room
"""

import json
import os
import time
import requests
from config import LEAGUE_ID, OWNER_MAP, PATHS

BASE = "https://api.sleeper.app/v1"
PLAYERS_CACHE_PATH = "newsletter/state/players_cache.json"
PLAYERS_CACHE_MAX_AGE_DAYS = 7
RUN_WINDOW = 4       # picks within this window counted toward a "run"
RUN_MIN_COUNT = 3    # this many same-position picks within the window = a run


def get(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def get_nfl_state():
    return get(f"{BASE}/state/nfl")


def get_rosters():
    return get(f"{BASE}/league/{LEAGUE_ID}/rosters")


def get_users():
    return get(f"{BASE}/league/{LEAGUE_ID}/users")


def get_players_cached():
    if os.path.exists(PLAYERS_CACHE_PATH):
        age_days = (time.time() - os.path.getmtime(PLAYERS_CACHE_PATH)) / 86400
        if age_days < PLAYERS_CACHE_MAX_AGE_DAYS:
            with open(PLAYERS_CACHE_PATH) as f:
                return json.load(f)
    players = get(f"{BASE}/players/nfl")
    with open(PLAYERS_CACHE_PATH, "w") as f:
        json.dump(players, f)
    return players


def roster_id_to_owner_name(rosters, users):
    user_display = {u["user_id"]: u.get("display_name", "Unknown") for u in users}
    out = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        name = OWNER_MAP.get(owner_id) or user_display.get(owner_id, "Unknown")
        out[r["roster_id"]] = name
    return out


def get_most_recent_draft():
    drafts = get(f"{BASE}/league/{LEAGUE_ID}/drafts")
    if not drafts:
        raise RuntimeError("No drafts found for this league via Sleeper's API.")
    drafts.sort(key=lambda d: d.get("start_time") or 0, reverse=True)
    return drafts[0]


def get_picks(draft_id):
    picks = get(f"{BASE}/draft/{draft_id}/picks")
    picks.sort(key=lambda p: p["pick_no"])
    return picks


def enrich_picks(picks, players_db, roster_names):
    enriched = []
    position_seen_count = {}
    for p in picks:
        pid = str(p.get("player_id"))
        player = players_db.get(pid, {})
        position = player.get("position", p.get("metadata", {}).get("position", "UNK"))
        position_seen_count[position] = position_seen_count.get(position, 0) + 1
        enriched.append({
            "pick_no": p["pick_no"],
            "round": p["round"],
            "draft_slot": p.get("draft_slot"),
            "roster_id": p.get("roster_id"),
            "team_name": roster_names.get(p.get("roster_id"), "Unknown"),
            "player_name": player.get("full_name") or p.get("metadata", {}).get("first_name", "") + " " + p.get("metadata", {}).get("last_name", ""),
            "position": position,
            "nfl_team": player.get("team") or p.get("metadata", {}).get("team"),
            "position_rank_at_pick": position_seen_count[position],  # e.g. "3rd RB taken"
        })
    return enriched


def detect_positional_runs(enriched_picks):
    """A 'run' = RUN_MIN_COUNT or more picks at the same position within a
    sliding window of RUN_WINDOW consecutive picks, regardless of team —
    this is what a draft-day 'the room panicked on TEs' storyline looks
    like structurally, derived purely from pick order."""
    runs = []
    i = 0
    n = len(enriched_picks)
    while i < n:
        window = enriched_picks[i:i + RUN_WINDOW]
        positions_in_window = [p["position"] for p in window]
        for pos in set(positions_in_window):
            count = positions_in_window.count(pos)
            if count >= RUN_MIN_COUNT:
                runs.append({
                    "position": pos,
                    "start_pick": window[0]["pick_no"],
                    "end_pick": window[-1]["pick_no"],
                    "players": [p["player_name"] for p in window if p["position"] == pos],
                    "teams": [p["team_name"] for p in window if p["position"] == pos],
                })
                i += RUN_WINDOW - 1  # skip past this window, avoid overlapping dupes
                break
        i += 1
    return runs


def position_first_and_last(enriched_picks):
    summary = {}
    for p in enriched_picks:
        pos = p["position"]
        summary.setdefault(pos, {"first": p, "last": p})
        if p["pick_no"] < summary[pos]["first"]["pick_no"]:
            summary[pos]["first"] = p
        if p["pick_no"] > summary[pos]["last"]["pick_no"]:
            summary[pos]["last"] = p
    return summary


def team_position_breakdown(enriched_picks, roster_names):
    breakdown = {name: {} for name in roster_names.values()}
    for p in enriched_picks:
        team = p["team_name"]
        breakdown.setdefault(team, {})
        breakdown[team][p["position"]] = breakdown[team].get(p["position"], 0) + 1
    return breakdown


def main():
    nfl_state = get_nfl_state()
    rosters = get_rosters()
    users = get_users()
    roster_names = roster_id_to_owner_name(rosters, users)
    players_db = get_players_cached()

    draft = get_most_recent_draft()
    picks = get_picks(draft["draft_id"])
    enriched = enrich_picks(picks, players_db, roster_names)

    data = {
        "season": nfl_state["season"],
        "draft_id": draft["draft_id"],
        "draft_status": draft.get("status"),
        "total_picks": len(enriched),
        "picks_in_order": enriched,
        "positional_runs": detect_positional_runs(enriched),
        "position_first_last": {
            pos: {"first": v["first"], "last": v["last"]}
            for pos, v in position_first_and_last(enriched).items()
        },
        "team_position_breakdown": team_position_breakdown(enriched, roster_names),
    }

    with open(PATHS["draft_raw"], "w") as f:
        json.dump(data, f, indent=2)

    print(f"Fetched {len(enriched)} picks from draft {draft['draft_id']}. "
          f"{len(data['positional_runs'])} positional runs detected.")


if __name__ == "__main__":
    main()
