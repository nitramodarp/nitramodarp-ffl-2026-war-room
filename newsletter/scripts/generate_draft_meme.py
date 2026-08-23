"""
Draft recap, step 3: meme generation, identical mechanics to
generate_meme.py but reading/writing the draft-recap-specific state files
so it doesn't collide with the weekly pipeline.
"""

import json
import os
import requests
from config import PATHS, CLAUDE_MODEL

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
IMGFLIP_USERNAME = os.environ["IMGFLIP_USERNAME"]
IMGFLIP_PASSWORD = os.environ["IMGFLIP_PASSWORD"]

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


def extract_json(text):
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object found in model response. Raw text was:\n{text}")
    return text[start:end + 1]


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
            "max_tokens": 1000,
            "thinking": {"type": "disabled"},
            "system": "You pick the best-fit meme template for a fantasy "
                      "football draft story and write the caption text. Return "
                      "ONLY valid JSON: {\"template_name\": \"...\", \"top_text\": "
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
    print("---- RAW MEME MODEL RESPONSE ----")
    print(text)
    print("---- END ----")
    if not text.strip():
        raise RuntimeError(f"Model returned no text content. Full API response: {json.dumps(data)}")
    return json.loads(extract_json(text), strict=False)


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
    with open(PATHS["draft_recap_draft"]) as f:
        draft = json.load(f)

    meme_brief = draft["meme_brief"]
    templates = get_templates()
    choice = pick_template_and_captions(meme_brief, templates)

    template = next(t for t in templates if t["name"] == choice["template_name"])
    image_url = render_meme(template["id"], choice.get("top_text", ""), choice.get("bottom_text", ""))

    with open(PATHS["draft_meme"], "w") as f:
        json.dump({"image_url": image_url, **choice}, f, indent=2)

    print(f"Draft recap meme rendered: {image_url}")


if __name__ == "__main__":
    main()
