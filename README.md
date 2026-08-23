# FFL Weekly Newsletter Pipeline

Runs every Tuesday via GitHub Actions: pulls last week's Sleeper data,
writes newsletter copy with Claude, generates a meme with Claude + Imgflip,
publishes an HTML page to this repo's GitHub Pages site. You paste the link
into Sleeper chat / your email manually — nothing sends itself.

## One-time setup

1. **Merge this into the existing war room repo** at the same paths shown
   here (`.github/workflows/newsletter.yml`, `newsletter/...`).
2. **Add repo secrets** (Settings → Secrets and variables → Actions):
   - `ANTHROPIC_API_KEY` — your Anthropic API key
   - `IMGFLIP_USERNAME` / `IMGFLIP_PASSWORD` — any free Imgflip account
3. **Confirm GitHub Pages is serving this repo's root or `/docs`** so
   `newsletter/output/*.html` is reachable at a public URL once committed.
4. **Test manually first** — go to the Actions tab, select "FFL Weekly
   Newsletter," click "Run workflow." Don't wait for the first real Tuesday
   to find out something's broken.

## Data sourcing — deliberately Sleeper-only

Your VORP board and re-scored projections are proprietary and are NOT
referenced anywhere in this pipeline, because this repo is public. Nothing
here pulls from the Google Sheets workbook, the Ciely refresh, or any
external ranking — deliberately, not just as an oversight.

Transaction grading instead uses a two-part, fully Sleeper-native approach —
adapted for **rolling waivers, no FAAB**:
1. **Process grade (week of the move):** was it a `waiver` claim (burns
   priority, sends the roster to the back of the order) or a free
   `free_agent` pickup (uncontested, no cost)? Was the position actually
   needed, based on what the roster already carried? What got dropped?
   All pulled from `/league/{id}/rosters` and `/league/{id}/transactions/{week}`.
2. **Results grade (weeks after):** `fetch_sleeper.py` tracks each add for
   4 weeks and tallies real points scored, since Sleeper computes those
   points under this league's exact custom scoring already — no external
   model needed.

Note the priority-diff mechanic is inherently one-week-lagged: we snapshot
each roster's `waiver_position` AFTER processing a week's claims, so the
"before" value used in next week's process grade is really "end of the
prior week." Good enough for narrative purposes, not perfectly real-time.

If you ever DO want proprietary numbers folded in, that has to happen in a
private layer that never gets committed to this repo — e.g. you paste board
values into the Action run manually, or the workflow reads from a private
repo/secret rather than a public Sheets URL. Don't wire the public Sheets
CSV in here even for convenience.

## Draft Recap (special edition)

A separate, manually-triggered workflow (`.github/workflows/draft-recap.yml`)
for a one-off recap of the draft itself — not part of the weekly Tuesday
cron. Run it once from the Actions tab whenever you want it.

Uses Sleeper's draft API (`/league/{id}/drafts` and `/draft/{id}/picks`),
not matchup/transaction data. Since there's no external ADP source wired
into this repo, all "reach"/"value" framing is **self-referential to this
draft alone**, and — after a round of real bugs — computed in Python rather
than left for the model to infer:

- **Exact pick labels** ("3.02" etc.) are computed from `pick_no` and
  `round`, not trusted to Sleeper's `draft_slot` field or to the model's own
  arithmetic. The model is instructed to quote `pick_label` verbatim.
- **Position-gap analysis** (`picks_since_previous_same_position` /
  `picks_until_next_same_position`) gives a real, quotable basis for "this
  was an outlier" claims — a QB taken with a 20+ pick gap before the next
  one really was alone at the position in this room, independent of any
  outside ranking.
- **`position_acquisition_summary`** computes, per position, the league-wide
  median round of each team's first pick at that position. This is what
  makes a "patient value pick" claim legitimate instead of vibes — e.g. a
  QB grabbed several rounds after the league's median first-QB round.
- **`confirmed_tendency_hits`** deterministically matches a known pattern
  (e.g. a specific team's stack of one NFL team's players) against actual
  picks, computed in code so it's guaranteed to surface rather than hoping
  the model notices it in 180 picks of data.

`owner_username` (a real Sleeper account handle) is used internally by
`fetch_draft.py` to compute the above, then stripped from every record
before it's written to the file the model actually reads — the model has no
access to it at all, not just an instruction not to print it.

Files: `fetch_draft.py` → `generate_draft_recap.py` → `generate_draft_meme.py`
→ `render_draft_recap.py`, output to
`newsletter/output/{season}-draft-recap.html`. Same secrets as the weekly
pipeline (`ANTHROPIC_API_KEY`, `IMGFLIP_USERNAME`, `IMGFLIP_PASSWORD`) — no
new ones needed.

## Known gaps / things to fix before trusting this fully

- **Week-number logic** (`fetch_sleeper.py::determine_weeks`) assumes
  Sleeper's current week has already ticked forward by Tuesday. Verify
  against the actual Sleeper UI for the first 2-3 weeks — this is the
  single most likely off-by-one bug, especially around Week 1.
- **Trade grading isn't tracked yet** — `update_transaction_tracking` only
  tracks waiver/free-agent adds, not trades, since a trade's "results" cuts
  both ways (who gave up more). Worth a dedicated tracker later that follows
  both sides of a trade's output, still Sleeper-only.
- **No optimal-lineup calc yet.** "Worst bench decision" in the recap is
  currently just asked of the model from matchup data without a real
  starters-vs-best-possible-lineup computation, since that needs Sleeper's
  full player database (position eligibility) joined against `players_points`.
  Worth adding once the format is proven out — don't over-build before the
  first real newsletter ships.
- **Meme template list is a curated shortlist** (12 templates in
  `generate_meme.py`), not Imgflip's full catalog. Expand freely; just keep
  it to templates that read as a "story" rather than pure reaction images.
- **Story-state file is public** (public repo). Fine for trash talk between
  friends, but don't put anything in there you wouldn't want searchable.
- **DST/cron drift**: the schedule is fixed UTC and will land an hour off
  during EST months. Not worth automating around for a Tuesday morning job —
  just notice if it shows up at 7am instead of 8am after daylight saving ends.

## Manual weekly routine (once automated)

1. Action runs Tuesday morning, commits the new page.
2. You get GitHub's default "workflow run" notification email — that's your
   cue to check it.
3. Open `newsletter/output/{season}-wk{NN}.html` via GitHub Pages, eyeball
   the meme and copy (the meme in particular — a bad read is more
   embarrassing than a bad stat).
4. Forward the link via email / paste into Sleeper league chat.
