"""
Step 1 of the pipeline: pull everything the newsletter needs from Sleeper's
read-only API and write it to newsletter/state/raw_week_data.json.

IMPORTANT: this pipeline uses ONLY Sleeper-native data. No external
projections/VORP board is referenced anywhere in this repo (it's public).
Sleeper's own weekly player points already reflect this league's exact
custom scoring settings (§3) — that's the whole trick. Transaction grading
is done retrospectively off real scored points, not a proprietary model.
"""

import json
import os
import time
import requests
from config import LEAGUE_ID, OWNER_MAP, PATHS, COMMISSIONER_OWNER_USERNAME, OWNER_NOTES, resolve_player_name, NFL_TEAM_NAMES

BASE = "https://api.sleeper.app/v1"
PLAYERS_CACHE_PATH = "newsletter/state/players_cache.json"
PLAYERS_CACHE_MAX_AGE_DAYS = 7
TRANSACTION_TRACKING_WINDOW_WEEKS = 4  # how many weeks to keep tallying an add's output


def get(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def get_nfl_state():
    return get(f"{BASE}/state/nfl")


def get_league():
    return get(f"{BASE}/league/{LEAGUE_ID}")


def get_rosters():
    return get(f"{BASE}/league/{LEAGUE_ID}/rosters")


def get_users():
    return get(f"{BASE}/league/{LEAGUE_ID}/users")


def get_matchups(week):
    return get(f"{BASE}/league/{LEAGUE_ID}/matchups/{week}")


def get_transactions(week):
    return get(f"{BASE}/league/{LEAGUE_ID}/transactions/{week}")


def get_players_cached():
    """The full Sleeper player DB is ~5MB — cache it locally instead of
    pulling it every single run. Only used for position/name lookups when
    grading transactions, never for scoring (Sleeper already scores)."""
    if os.path.exists(PLAYERS_CACHE_PATH):
        age_days = (time.time() - os.path.getmtime(PLAYERS_CACHE_PATH)) / 86400
        if age_days < PLAYERS_CACHE_MAX_AGE_DAYS:
            with open(PLAYERS_CACHE_PATH) as f:
                return json.load(f)
    players = get(f"{BASE}/players/nfl")
    with open(PLAYERS_CACHE_PATH, "w") as f:
        json.dump(players, f)
    return players


def roster_id_to_team_info(rosters, users):
    """Map roster_id -> {team_name, owner_username, is_commissioner}.

    team_name is the REAL fantasy team name — Sleeper stores it on each
    user's league-specific metadata.team_name — and should ALWAYS be what
    shows up in newsletter copy. owner_username (OWNER_MAP, falling back to
    Sleeper's display_name) is kept only as an internal key for matching
    OWNER_NOTES tendencies; it should never be printed in the output."""
    user_by_id = {u["user_id"]: u for u in users}
    out = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        user = user_by_id.get(owner_id, {})
        owner_username = OWNER_MAP.get(owner_id) or user.get("display_name", "Unknown")
        team_name = (user.get("metadata") or {}).get("team_name") or owner_username
        out[r["roster_id"]] = {
            "owner_id": owner_id,
            "owner_username": owner_username,
            "team_name": team_name,
            "is_commissioner": owner_username == COMMISSIONER_OWNER_USERNAME,
        }
    return out


def build_standings(rosters, roster_names):
    standings = []
    for r in rosters:
        s = r.get("settings", {})
        standings.append({
            "roster_id": r["roster_id"],
            "team_name": roster_names[r["roster_id"]]["team_name"],
            "is_commissioner": roster_names[r["roster_id"]]["is_commissioner"],
            "wins": s.get("wins", 0),
            "losses": s.get("losses", 0),
            "ties": s.get("ties", 0),
            "fpts": s.get("fpts", 0) + s.get("fpts_decimal", 0) / 100,
            "fpts_against": s.get("fpts_against", 0) + s.get("fpts_against_decimal", 0) / 100,
        })
    standings.sort(key=lambda x: (-x["wins"], -x["fpts"]))
    return standings


def compute_playoff_picture(standings):
    """This league's playoff format is NOT standard top-6-by-record: the
    top 5 teams by record get in normally, but the 6th and final spot goes
    to whichever of the REMAINING 7 teams has the highest total points
    scored on the season — not the next-best record. Confirmed directly by
    the commissioner. Computed here rather than left to the model, since
    an unusual rule like this is exactly the kind of thing that's easy to
    silently get wrong by defaulting to the standard assumption."""
    top_5 = standings[:5]
    remaining = sorted(standings[5:], key=lambda x: -x["fpts"])
    sixth_seed = remaining[0] if remaining else None
    on_the_bubble = remaining[1] if len(remaining) > 1 else None
    return {
        "note": "6th and final playoff spot goes to the highest-scoring team "
                "among those NOT in the top 5 by record — not the next-best record.",
        "top_5_by_record": top_5,
        "sixth_seed_by_points": sixth_seed,
        "on_the_bubble_by_points": on_the_bubble,
        "teams_outside_playoff_picture": remaining[2:],
    }


def determine_weeks(nfl_state):
    current = nfl_state["week"]
    return max(current - 1, 1), current


FLEX_ELIGIBLE = {"RB", "WR", "TE"}


def enrich_matchup_players(matchups, players_db):
    """Resolve raw player_id -> name/position for every rostered player in
    each matchup entry (Sleeper's players_points is only {player_id:
    points}, no names at all), then precompute the two facts the
    newsletter actually needs names for: this roster's single top scorer,
    and any bench player who outscored a starter they could plausibly have
    swapped for. Without this the model could only ever write vague lines
    like "by 47.95 from one starter" — it genuinely had no name to use.

    The bench-mistake check is a reasonable approximation, not a full
    optimal-lineup solver: QB and DEF have exactly one dedicated slot, so
    a bench player there outscoring the starter is a clean, certain
    mistake. RB/WR/TE share 2 dedicated RB, 2 dedicated WR, 1 dedicated
    TE, and 2 position-agnostic FLEX slots — comparing the best bench
    RB/WR/TE against the worst starting RB/WR/TE usually, but not always,
    reflects a real available swap (a rare edge case: needing to keep 2
    dedicated RBs could occasionally block it). Documented here rather
    than silently assumed perfect."""
    for m in matchups:
        players_points = m.get("players_points") or {}
        starters = set(m.get("starters") or [])

        resolved = []
        for pid, pts in players_points.items():
            player = players_db.get(str(pid), {})
            position = player.get("position") or ("DEF" if pid in NFL_TEAM_NAMES else "UNK")
            resolved.append({
                "player_id": pid,
                "name": resolve_player_name(pid, players_db),
                "position": position,
                "points": pts or 0,
                "started": pid in starters,
            })

        starter_entries = [p for p in resolved if p["started"]]
        bench_entries = [p for p in resolved if not p["started"]]
        top_scorer = max(resolved, key=lambda p: p["points"], default=None)

        bench_mistake = None
        for pos in ("QB", "DEF"):
            starter_at_pos = next((p for p in starter_entries if p["position"] == pos), None)
            bench_at_pos = [p for p in bench_entries if p["position"] == pos]
            if starter_at_pos and bench_at_pos:
                best_bench = max(bench_at_pos, key=lambda p: p["points"])
                if best_bench["points"] > starter_at_pos["points"]:
                    margin = best_bench["points"] - starter_at_pos["points"]
                    if not bench_mistake or margin > bench_mistake["margin"]:
                        bench_mistake = {
                            "benched_player": best_bench["name"], "benched_points": best_bench["points"],
                            "started_instead": starter_at_pos["name"], "started_points": starter_at_pos["points"],
                            "margin": round(margin, 2),
                        }

        starter_flex = [p for p in starter_entries if p["position"] in FLEX_ELIGIBLE]
        bench_flex = [p for p in bench_entries if p["position"] in FLEX_ELIGIBLE]
        if starter_flex and bench_flex:
            worst_starter = min(starter_flex, key=lambda p: p["points"])
            best_bench = max(bench_flex, key=lambda p: p["points"])
            if best_bench["points"] > worst_starter["points"]:
                margin = best_bench["points"] - worst_starter["points"]
                if not bench_mistake or margin > bench_mistake["margin"]:
                    bench_mistake = {
                        "benched_player": best_bench["name"], "benched_points": best_bench["points"],
                        "started_instead": worst_starter["name"], "started_points": worst_starter["points"],
                        "margin": round(margin, 2),
                    }

        m["players_resolved"] = resolved
        m["top_scorer_on_roster"] = top_scorer
        m["bench_mistake"] = bench_mistake  # null if no valid swap found
    return matchups


def compute_league_top_scorer(matchups):
    """Single highest-scoring STARTER across the whole league this week —
    restricted to starters since a huge outlier sitting on a bench is
    already covered by bench_mistake, not "the" story on its own."""
    best = None
    for m in matchups:
        for p in m.get("players_resolved", []):
            if p["started"] and (best is None or p["points"] > best["points"]):
                best = {**p, "team_name": m.get("team_name")}
    return best


def roster_position_counts(roster, players_db):
    """How many players at each position a roster carried BEFORE this
    week's moves — used to judge whether an add addressed a real need or
    was just a name-brand panic add at an already-stacked position."""
    counts = {}
    for pid in roster.get("players") or []:
        pos = players_db.get(str(pid), {}).get("position", "UNK")
        counts[pos] = counts.get(pos, 0) + 1
    return counts


def enrich_transactions(transactions, players_db, roster_names, rosters_by_id, story_state):
    """Attach names, positions, and pre-move roster construction to each
    transaction — all sourced from Sleeper, nothing external.

    This is a rolling-waivers league (no FAAB). The cost signal here is
    priority, not budget: a 'waiver' transaction burns a claim and sends
    that roster to the back of the priority order; a 'free_agent'
    transaction is a free, no-cost pickup because no one else wanted the
    player badly enough to claim him. We track waiver_position week over
    week in story_state so we can say "spent his #1 priority slot on a
    guy who's since done nothing" rather than a dollar amount.

    CRITICAL: only status == "complete" transactions are processed. Sleeper's
    transactions endpoint also returns FAILED waiver claims (e.g. all 4
    teams that bid on a player, not just the winner) — including those
    made it look like 4 teams each burned priority on the same player,
    when really 3 of them just... didn't get him. Only the winning claim
    actually happened and only it burns priority.
    """
    prev_priority = story_state.get("last_known_waiver_priority", {})
    enriched = []
    skipped_incomplete = 0
    for t in transactions:
        if t.get("type") not in ("waiver", "free_agent", "trade"):
            continue
        if t.get("status") != "complete":
            skipped_incomplete += 1
            continue
        adds = t.get("adds") or {}
        drops = t.get("drops") or {}
        used_priority = t.get("type") == "waiver"

        for pid, roster_id in adds.items():
            player = players_db.get(str(pid), {})
            roster = rosters_by_id.get(roster_id, {})
            counts_before = roster_position_counts(roster, players_db)
            dropped_pid = next((dpid for dpid, drid in drops.items() if drid == roster_id), None)
            owner_username = roster_names.get(roster_id, {}).get("owner_username")  # internal only, not written out below
            note = OWNER_NOTES.get(owner_username)
            enriched.append({
                "type": t["type"],
                "team_name": roster_names.get(roster_id, {}).get("team_name", "Unknown"),
                "roster_id": roster_id,
                "player_added": resolve_player_name(pid, players_db),
                "player_added_id": pid,
                "position": player.get("position") or ("DEF" if pid in NFL_TEAM_NAMES else None),
                "player_dropped": resolve_player_name(dropped_pid, players_db) if dropped_pid else None,
                "used_waiver_priority": used_priority,
                "waiver_priority_before_move": prev_priority.get(str(roster_id)),
                "confirmed_homer_transaction": bool(note and note.get("homer_team") == player.get("team")),
            })
    return enriched, skipped_incomplete


def track_waiver_priority(story_state, rosters):
    """Snapshot each roster's current waiver_position so next week's run
    can tell whether a team burned a good priority slot on this week's
    claim. Rolling waivers reset/reorder after every claim, so this only
    means anything as a week-over-week diff, never a single-week value."""
    current = {str(r["roster_id"]): r.get("settings", {}).get("waiver_position")
               for r in rosters}
    story_state["last_known_waiver_priority"] = current


def update_transaction_tracking(story_state, new_transactions, week):
    """Add this week's adds to the rolling tracker so future weeks can
    report real cumulative points scored since the pickup.

    Deduplicated by (roster_id, player_added_id, week_added) — confirmed in
    production that re-running the workflow multiple times for the same
    week (routine during testing/debugging) silently appended a fresh
    duplicate entry every single time, with no cleanup. One case grew to
    105 tracked entries where 22 distinct ones should have existed. This
    function must be safe to call any number of times for the same week's
    data without changing the result beyond the first call."""
    tracked = story_state.setdefault("transaction_tracking", [])
    existing_keys = {(t["roster_id"], t["player_added_id"], t["week_added"]) for t in tracked}
    for tx in new_transactions:
        if tx["type"] == "trade":
            continue  # trades tracked separately if desired later; keep v1 simple
        key = (tx["roster_id"], tx["player_added_id"], week)
        if key in existing_keys:
            continue  # already tracked from a prior run of this same week — don't duplicate
        tracked.append({
            "week_added": week,
            "roster_id": tx["roster_id"],
            "team_name": tx["team_name"],
            "player_added": tx["player_added"],
            "player_added_id": tx["player_added_id"],
            "used_waiver_priority": tx["used_waiver_priority"],
            "cumulative_points_since_add": 0.0,
            "weeks_tracked": 0,
            "weeks_counted": [],  # which week numbers have already had points applied — prevents double-counting on re-runs
        })
        existing_keys.add(key)
    return tracked


def refresh_tracked_points(story_state, current_week_matchups, current_week):
    """For every add still inside its tracking window, add this week's
    actual scored points (already under the league's real scoring) to its
    running total. Drops tracking after TRANSACTION_TRACKING_WINDOW_WEEKS.

    Idempotent per week via weeks_counted — re-running this for the same
    current_week (routine during testing) must not add the same week's
    points to the total a second time. Older entries from before this
    field existed default to an empty list via .setdefault, so they'll
    correctly accept the next real week's points without re-adding
    anything already (incorrectly) baked into their total."""
    tracked = story_state.get("transaction_tracking", [])
    points_by_roster = {m["roster_id"]: m.get("players_points", {}) for m in current_week_matchups}
    still_tracking = []
    for entry in tracked:
        entry.setdefault("weeks_counted", [])
        weeks_elapsed = current_week - entry["week_added"]
        if 0 <= weeks_elapsed <= TRANSACTION_TRACKING_WINDOW_WEEKS:
            if current_week not in entry["weeks_counted"]:
                pts = points_by_roster.get(entry["roster_id"], {}).get(str(entry["player_added_id"]), 0) or 0
                entry["cumulative_points_since_add"] += pts
                entry["weeks_tracked"] += 1
                entry["weeks_counted"].append(current_week)
            still_tracking.append(entry)
        elif weeks_elapsed < 0:
            still_tracking.append(entry)  # not reached yet, shouldn't happen but keep safe
    story_state["transaction_tracking"] = still_tracking
    return still_tracking


def main():
    nfl_state = get_nfl_state()
    week_to_recap, week_to_preview = determine_weeks(nfl_state)

    rosters = get_rosters()
    rosters_by_id = {r["roster_id"]: r for r in rosters}
    users = get_users()
    roster_names = roster_id_to_team_info(rosters, users)
    players_db = get_players_cached()

    matchups_recap = get_matchups(week_to_recap)
    matchups_preview = get_matchups(week_to_preview)
    raw_transactions = get_transactions(week_to_recap)
    standings = build_standings(rosters, roster_names)

    with open(PATHS["story_state"]) as f:
        story_state = json.load(f)

    enriched_tx, skipped_incomplete = enrich_transactions(raw_transactions, players_db, roster_names, rosters_by_id, story_state)
    update_transaction_tracking(story_state, enriched_tx, week_to_recap)
    tracked_results = refresh_tracked_points(story_state, matchups_recap, week_to_recap)  # needs raw players_points — must run BEFORE enrich_matchup_players
    track_waiver_priority(story_state, rosters)  # snapshot AFTER this week's claims for next week's diff

    with open(PATHS["story_state"], "w") as f:
        json.dump(story_state, f, indent=2)

    def annotate(matchups):
        for m in matchups:
            m["team_name"] = roster_names.get(m["roster_id"], {}).get("team_name", "Unknown")
        return matchups

    annotate(matchups_recap)
    enrich_matchup_players(matchups_recap, players_db)  # adds named players_resolved/top_scorer_on_roster/bench_mistake
    league_top_scorer = compute_league_top_scorer(matchups_recap)
    annotate(matchups_preview)  # preview has no scores yet — names not useful there, skip enrichment

    data = {
        "season": nfl_state["season"],
        "week_recapped": week_to_recap,
        "week_upcoming": week_to_preview,
        "standings": standings,
        "playoff_picture": compute_playoff_picture(standings),
        "matchups_recap": matchups_recap,
        "matchups_preview": matchups_preview,
        "league_top_scorer": league_top_scorer,
        "transactions_this_week": enriched_tx,
        "transaction_tracking_all_active": tracked_results,
    }

    with open(PATHS["raw_data"], "w") as f:
        json.dump(data, f, indent=2)

    print(f"Fetched week {week_to_recap} recap + week {week_to_preview} preview. "
          f"{len(enriched_tx)} completed transactions ({skipped_incomplete} failed/incomplete claims filtered out), "
          f"{len(tracked_results)} adds under active tracking.")


if __name__ == "__main__":
    main()
