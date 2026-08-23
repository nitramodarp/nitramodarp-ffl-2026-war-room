"""
Draft recap, step 1: pull the completed draft from Sleeper and build
structured, fully self-referential "value" signals — no external ADP or
proprietary board involved anywhere, per the same rule as the weekly
pipeline.

Two design principles enforced here, both added after real bugs:
1. Never make the model compute round/pick arithmetic itself — a wrong
   round citation (e.g. misattributing which round a TE went in) is a
   model math error, not a data error. Every pick gets an exact
   "pick_label" (e.g. "3.02") computed in Python; the model is instructed
   to quote it verbatim rather than reconstruct it.
2. Never let owner_username (a real Sleeper account handle) reach the
   model at all. It's used internally, right here, to compute deterministic
   tendency matches (e.g. a KC-homer pick) — but stripped from every
   record before it's written to the file the model actually reads.
"""

import json
import os
import statistics
import time
import re
import requests
from config import LEAGUE_ID, OWNER_MAP, PATHS, OWNER_NOTES, COMMISSIONER_OWNER_USERNAME

BASE = "https://api.sleeper.app/v1"
FFC_ADP_URL = "https://fantasyfootballcalculator.com/api/v1/adp/half-ppr"
PLAYERS_CACHE_PATH = "newsletter/state/players_cache.json"
PLAYERS_CACHE_MAX_AGE_DAYS = 7
RUN_WINDOW = 4       # picks within this window counted toward a "run"
RUN_MIN_COUNT = 3    # this many same-position picks within the window = a run


def normalize_name(name):
    """Loose match key for joining Sleeper player names against Fantasy
    Football Calculator's ADP names — different sources format suffixes
    and punctuation differently (e.g. 'Kenneth Walker III' vs 'Kenneth
    Walker'), so strip all of that down to a bare comparable string."""
    if not name:
        return ""
    name = name.lower()
    name = re.sub(r"[.\']", "", name)
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def get_market_adp(team_count=12, year=2026):
    """Public consensus ADP from Fantasy Football Calculator — NOT the
    proprietary Ciely/VORP board, which never appears anywhere in this
    repo. This is exactly the same public ADP source already referenced in
    the project's own methodology docs. Wrapped defensively: this script's
    exact response shape hasn't been verified against a live call (it's
    outside the dev sandbox's reachable domains), so a schema surprise
    should degrade to 'no ADP data' rather than break the whole pipeline —
    check the run log's printed sample if this returns an empty dict."""
    try:
        resp = requests.get(
            FFC_ADP_URL,
            params={"teams": team_count, "year": year, "position": "all"},
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        players = payload.get("players", [])
        adp_by_name = {}
        for pl in players:
            key = normalize_name(pl.get("name"))
            if key:
                adp_by_name[key] = pl.get("adp")
        print(f"Fetched market ADP for {len(adp_by_name)} players from Fantasy Football Calculator.")
        if players[:1]:
            print(f"Sample ADP record (for verifying the response shape): {players[0]}")
        return adp_by_name
    except Exception as e:
        print(f"WARNING: could not fetch/parse market ADP ({e}) — proceeding without ADP comparison. "
              f"adp_delta_vs_market will be null on every pick.")
        return {}


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


def roster_id_to_team_info(rosters, users):
    """team_name is always the real fantasy team name. owner_username is
    internal-only — computed here so THIS FILE can match tendency notes,
    but it never leaves this file in any record written out to disk."""
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


def enrich_picks(picks, players_db, roster_names, team_count, adp_by_name):
    """team_count picks per round lets us derive pick-within-round purely
    from pick_no and round — both given directly by Sleeper — rather than
    trusting the semantics of Sleeper's own 'draft_slot' field, which does
    NOT reliably equal on-screen pick position in snake rounds. This always
    matches what the actual draft board displays (verified against the
    1.1 / 2.12 / 3.1 / 4.12 pattern visible on Sleeper's UI)."""
    enriched = []
    position_seen_count = {}
    for p in picks:
        pid = str(p.get("player_id"))
        player = players_db.get(pid, {})
        position = player.get("position", p.get("metadata", {}).get("position", "UNK"))
        position_seen_count[position] = position_seen_count.get(position, 0) + 1
        team_info = roster_names.get(p.get("roster_id"), {})
        nfl_team = player.get("team") or p.get("metadata", {}).get("team")
        owner_username = team_info.get("owner_username")  # used below, stripped before output
        note = OWNER_NOTES.get(owner_username)

        pick_no = p["pick_no"]
        round_no = p["round"]
        pick_within_round = pick_no - (round_no - 1) * team_count
        pick_label = f"{round_no}.{pick_within_round:02d}"

        player_name = player.get("full_name") or (p.get("metadata", {}).get("first_name", "") + " " + p.get("metadata", {}).get("last_name", ""))
        market_adp = adp_by_name.get(normalize_name(player_name))
        # Positive = fell PAST market ADP (this room valued him less than
        # consensus = value pick). Negative = taken BEFORE market ADP
        # (this room valued him more than consensus = reach).
        adp_delta_vs_market = (market_adp - pick_no) if market_adp is not None else None

        enriched.append({
            "pick_no": pick_no,
            "round": round_no,
            "pick_within_round": pick_within_round,
            "pick_label": pick_label,  # ALWAYS quote this verbatim, never recompute
            "roster_id": p.get("roster_id"),
            "team_name": team_info.get("team_name", "Unknown"),
            "is_commissioner": team_info.get("is_commissioner", False),
            "player_name": player_name,
            "position": position,
            "nfl_team": nfl_team,
            "position_rank_at_pick": position_seen_count[position],  # e.g. "3rd RB taken"
            "market_adp": market_adp,  # public Fantasy Football Calculator consensus, or null if unmatched
            "adp_delta_vs_market": adp_delta_vs_market,
            "confirmed_homer_pick": bool(note and note.get("homer_team") == nfl_team),
            "_owner_username": owner_username,  # leading underscore = stripped before write, see strip_internal_fields()
        })
    return enriched


def strip_internal_fields(pick):
    """Remove every key that shouldn't reach the model. Called right
    before anything is written to draft_raw_data.json."""
    return {k: v for k, v in pick.items() if not k.startswith("_")}


def detect_positional_runs(enriched_picks):
    """A 'run' = RUN_MIN_COUNT or more picks at the same position within a
    sliding window of RUN_WINDOW consecutive picks, regardless of team."""
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
                    "start_pick_label": window[0]["pick_label"],
                    "end_pick_label": window[-1]["pick_label"],
                    "players": [p["player_name"] for p in window if p["position"] == pos],
                    "teams": [p["team_name"] for p in window if p["position"] == pos],
                })
                i += RUN_WINDOW - 1
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


def compute_position_gaps(enriched_picks):
    """For every pick, how many overall picks separated it from the
    PREVIOUS pick at the same position, and from the NEXT one. This is
    what actually lets the model correctly judge 'early' or 'late' without
    external ADP: a QB taken with a 21-pick gap before the next QB was a
    real outlier in THIS room, independent of any outside ranking."""
    by_position = {}
    for idx, p in enumerate(enriched_picks):
        by_position.setdefault(p["position"], []).append(idx)

    gap_before = {}
    gap_after = {}
    for pos, indices in by_position.items():
        for i, idx in enumerate(indices):
            pick_no = enriched_picks[idx]["pick_no"]
            if i > 0:
                prev_pick_no = enriched_picks[indices[i - 1]]["pick_no"]
                gap_before[idx] = pick_no - prev_pick_no
            if i < len(indices) - 1:
                next_pick_no = enriched_picks[indices[i + 1]]["pick_no"]
                gap_after[idx] = next_pick_no - pick_no

    for idx, p in enumerate(enriched_picks):
        p["picks_since_previous_same_position"] = gap_before.get(idx)  # None = first ever at this position
        p["picks_until_next_same_position"] = gap_after.get(idx)       # None = last ever at this position
    return enriched_picks


def team_first_pick_by_position(enriched_picks):
    """For every team, the first (presumably starter) pick at each
    position, plus the league-wide median round for that 'first pick at
    position' across all 12 teams. This is the real basis for a legitimate
    value claim like 'got their QB in round 9, five rounds after the
    league's median team already had theirs' — computed, not inferred."""
    first_by_team_position = {}
    for p in enriched_picks:
        key = (p["team_name"], p["position"])
        if key not in first_by_team_position or p["pick_no"] < first_by_team_position[key]["pick_no"]:
            first_by_team_position[key] = p

    by_position = {}
    for (team, pos), p in first_by_team_position.items():
        by_position.setdefault(pos, []).append({
            "team_name": team,
            "pick_label": p["pick_label"],
            "round": p["round"],
            "pick_no": p["pick_no"],
            "player_name": p["player_name"],
        })

    summary = {}
    for pos, entries in by_position.items():
        entries.sort(key=lambda e: e["pick_no"])
        median_round = statistics.median(e["round"] for e in entries)
        summary[pos] = {
            "median_round_of_first_pick_across_league": median_round,
            "per_team_first_pick": entries,
        }
    return summary


def team_position_breakdown(enriched_picks, roster_names):
    breakdown = {info["team_name"]: {} for info in roster_names.values()}
    for p in enriched_picks:
        team = p["team_name"]
        breakdown.setdefault(team, {})
        breakdown[team][p["position"]] = breakdown[team].get(p["position"], 0) + 1
    return breakdown


def get_confirmed_tendency_hits(enriched_picks):
    """Deterministic, not left to the model to notice."""
    return [p for p in enriched_picks if p["confirmed_homer_pick"]]


def main():
    nfl_state = get_nfl_state()
    rosters = get_rosters()
    users = get_users()
    roster_names = roster_id_to_team_info(rosters, users)
    players_db = get_players_cached()
    team_count = len(rosters)

    draft = get_most_recent_draft()
    picks = get_picks(draft["draft_id"])
    adp_by_name = get_market_adp(team_count=team_count, year=int(nfl_state["season"]))
    enriched = enrich_picks(picks, players_db, roster_names, team_count, adp_by_name)
    enriched = compute_position_gaps(enriched)
    tendency_hits = get_confirmed_tendency_hits(enriched)
    position_first_last = position_first_and_last(enriched)
    acquisition_summary = team_first_pick_by_position(enriched)
    breakdown = team_position_breakdown(enriched, roster_names)
    runs = detect_positional_runs(enriched)

    # Strip internal-only fields (owner_username) from EVERYTHING before
    # it's written to the file the model reads.
    clean_picks = [strip_internal_fields(p) for p in enriched]
    clean_tendency_hits = [strip_internal_fields(p) for p in tendency_hits]
    clean_position_first_last = {
        pos: {"first": strip_internal_fields(v["first"]), "last": strip_internal_fields(v["last"])}
        for pos, v in position_first_last.items()
    }

    data = {
        "season": nfl_state["season"],
        "draft_id": draft["draft_id"],
        "draft_status": draft.get("status"),
        "team_count": team_count,
        "total_picks": len(clean_picks),
        "picks_in_order": clean_picks,
        "positional_runs": runs,
        "position_first_last": clean_position_first_last,
        "position_acquisition_summary": acquisition_summary,
        "team_position_breakdown": breakdown,
        "confirmed_tendency_hits": clean_tendency_hits,
    }

    with open(PATHS["draft_raw"], "w") as f:
        json.dump(data, f, indent=2)

    matched_adp_count = sum(1 for p in enriched if p["market_adp"] is not None)
    print(f"Fetched {len(clean_picks)} picks from draft {draft['draft_id']}. "
          f"{len(runs)} positional runs detected. "
          f"{len(clean_tendency_hits)} confirmed owner-tendency hits. "
          f"{matched_adp_count}/{len(enriched)} picks matched to market ADP.")


if __name__ == "__main__":
    main()
