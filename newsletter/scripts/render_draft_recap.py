"""
Draft recap, step 4: stitch draft_recap_draft.json + draft_meme.json into
the final HTML page, written to newsletter/output/{season}-draft-recap.html.
"""

import json
import os
from config import PATHS

import html as _html
import re as _re


def to_paragraphs(text):
    """Turn model text into one <p> per paragraph. The model separates
    paragraphs with blank lines, which HTML collapses if the whole string
    sits inside a single <p>. Falls back to single newlines if the model
    never used a blank line. Text is escaped so stray < or & can't break
    the page."""
    text = (text or "").strip()
    chunks = [c.strip() for c in _re.split(r"\n\s*\n", text) if c.strip()]
    if len(chunks) <= 1:
        chunks = [c.strip() for c in text.split("\n") if c.strip()]
    return "\n  ".join(f"<p>{_html.escape(c, quote=False)}</p>" for c in chunks)



def main():
    with open(PATHS["draft_raw"]) as f:
        raw = json.load(f)
    with open(PATHS["draft_recap_draft"]) as f:
        draft = json.load(f)
    with open(PATHS["draft_meme"]) as f:
        meme = json.load(f)
    with open(PATHS["draft_template"]) as f:
        template = f.read()

    grades_html = "\n".join(
        f'      <li><span class="grade-letter">{g["grade"]}</span> '
        f'<b>{g["team"]}</b> — {g["blurb"]}</li>'
        for g in draft["team_grades"]
    )

    html = (template
        .replace("{{HEADLINE}}", draft["headline"])
        .replace("{{MEME_URL}}", meme["image_url"])
        .replace("{{DRAFT_NARRATIVE}}", to_paragraphs(draft["draft_narrative"]))
        .replace("{{TEAM_GRADES_HTML}}", grades_html)
        .replace("{{STANDOUT_PICKS}}", to_paragraphs(draft["standout_picks"]))
        .replace("{{LOOKING_AHEAD}}", to_paragraphs(draft["looking_ahead"]))
    )

    out_path = f"{PATHS['output_dir']}/{raw['season']}-draft-recap.html"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(html)

    print(f"Draft recap published to {out_path}")


if __name__ == "__main__":
    main()
