"""
Step 4 of the pipeline: combine newsletter_draft.json + meme.json into the
final HTML page and write it to newsletter/output/. This is what gets
published to GitHub Pages and is the link you drop in Sleeper chat / email.
"""

import json
import os
from config import PATHS

def main():
    with open(PATHS["raw_data"]) as f:
        raw = json.load(f)
    with open(PATHS["newsletter_draft"]) as f:
        draft = json.load(f)
    with open(PATHS["meme"]) as f:
        meme = json.load(f)
    with open(PATHS["template"]) as f:
        template = f.read()

    rankings_html = "\n".join(
        f"      <li><b>{r['team']}</b> — {r['blurb']}</li>"
        for r in sorted(draft["power_rankings"], key=lambda x: x["rank"])
    )

    html = (template
        .replace("{{HEADLINE}}", draft["headline"])
        .replace("{{WEEK}}", str(raw["week_recapped"]))
        .replace("{{SEASON}}", str(raw["season"]))
        .replace("{{MEME_URL}}", meme["image_url"])
        .replace("{{RECAP}}", draft["recap"])
        .replace("{{TRANSACTION_DESK}}", draft["transaction_desk"])
        .replace("{{POWER_RANKINGS_HTML}}", rankings_html)
        .replace("{{STANDINGS_NARRATIVE}}", draft["standings_narrative"])
        .replace("{{LOOK_AHEAD}}", draft["look_ahead"])
    )

    out_path = f"{PATHS['output_dir']}/{raw['season']}-wk{raw['week_recapped']:02d}.html"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(html)

    print(f"Newsletter published to {out_path}")


if __name__ == "__main__":
    main()
