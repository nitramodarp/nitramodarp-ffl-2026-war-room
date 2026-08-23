"""
Step 3 of the pipeline: turn the week's headline story into an actual meme
image, using Imgflip's template library + captioning API.

Requires GitHub secrets: IMGFLIP_USERNAME, IMGFLIP_PASSWORD (any free
Imgflip account works — sign up once, store the credentials as secrets).
Some templates are Imgflip-Premium-only; caption_image will error on those,
so we only offer Claude the templates flagged safe/free.
"""

import json
import os
import re
import requests
from config import PATHS, CLAUDE_MODEL

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
IMGFLIP_USERNAME = os.environ["IMGFLIP_USERNAME"]
IMGFLIP_PASSWORD = os.environ["IMGFLIP_PASSWORD"]

# A curated shortlist rather than dumping Imgflip's full top-100 at Claude —
# keeps template choice relevant to fantasy-football-shaped stories instead
# of drifting into templates that don't fit a recap. Extend this list freely;
# names must match Imgflip's own template names exactly for the lookup below.
CANDIDATE_TEMPLATES = [
    "Drake Hotline Bling",
    "Distracted Boyfriend",
    "Two Buttons",
    "Change My Mind",
    "This Is Fine",
    "Expanding Brain",
    "Woman Yelling at Cat",
    "Surprised Pikachu",
    "Disaster Girl",
    "Is This A Pigeon",
    "Gru's Plan",
    "Bernie I Am Once Again Asking For Your Support",
]


def get_templates():
    resp = requests.get("https://api.imgflip.com/get_memes", timeout=20)
    resp.raise_for_status()
    all_templates = resp.json()["data"]["memes"]
    by_name = {t["name"]: t for t in all_templates}
    return [by_name[name] for name in CANDIDATE_TEMPLATES if name in by_name]


def pick_template_and_captions(meme_brief, templates):
    template_options = [{"name": t["name"], "box_count": t["box_count"]} for t in templates]
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": 300,
            "system": "You pick the best-fit meme template for a fantasy "
                      "football story and write the caption text. Return ONLY "
                      "valid JSON: {\"template_name\": \"...\", \"top_text\": "
                      "\"...\", \"bottom_text\": \"...\"}. Keep captions short "
                      "and punchy — this is for 40-year friends who talk trash, "
                      "not corporate-safe humor.",
            "messages": [{
                "role": "user",
                "content": f"Story: {meme_brief}\n\nAvailable templates: {json.dumps(template_options)}"
            }],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    text = "".join(b["text"] for b in data["content"] if b["type"] == "text")
    cleaned = re.sub(r"^```json|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


def render_meme(template_id, top_text, bottom_text):
    resp = requests.post(
        "https://api.imgflip.com/caption_image",
        data={
            "template_id": template_id,
            "username": IMGFLIP_USERNAME,
            "password": IMGFLIP_PASSWORD,
            "text0": top_text,
            "text1": bottom_text,
        },
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("success"):
        raise RuntimeError(f"Imgflip captioning failed: {result.get('error_message')}")
    return result["data"]["url"]


def main():
    with open(PATHS["newsletter_draft"]) as f:
        draft = json.load(f)

    meme_brief = draft["meme_brief"]
    templates = get_templates()
    choice = pick_template_and_captions(meme_brief, templates)

    template = next(t for t in templates if t["name"] == choice["template_name"])
    image_url = render_meme(template["id"], choice.get("top_text", ""), choice.get("bottom_text", ""))

    with open(PATHS["meme"], "w") as f:
        json.dump({"image_url": image_url, **choice}, f, indent=2)

    print(f"Meme rendered: {image_url}")


if __name__ == "__main__":
    main()
