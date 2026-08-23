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
    candidates = [by_name[name] for name in CANDIDATE_TEMPLATES if name in by_name]
    # Filter to box_count <= 2 at runtime rather than trusting prompt
    # compliance for higher box counts — a 4-box template like Expanding
    # Brain kept rendering with blank panels because the model reliably
    # under-delivers captions on complex templates even when told the
    # exact count required. Determined from Imgflip's own live metadata,
    # not a hardcoded guess about which named templates have how many boxes.
    filtered = [t for t in candidates if t.get("box_count", 99) <= 2]
    return filtered if filtered else candidates[:1]  # never return an empty list


def parse_best_json(text, required_keys):
    """See generate_draft_recap.py for the full rationale — raw_decode from
    every '{' handles a genuinely malformed first attempt without letting
    it corrupt parsing of a valid object elsewhere in the text."""
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
                      "football story and write the caption text. Each template "
                      "in the list has a box_count — you MUST return exactly "
                      "that many captions, in order top-to-bottom/left-to-right, "
                      "or the image will render with blank boxes. Return ONLY "
                      "valid JSON: {\"template_name\": \"...\", \"captions\": "
                      "[\"...\", \"...\"]} where captions has EXACTLY box_count "
                      "entries for the template you chose. Keep captions short "
                      "and punchy — longtime friends who talk real trash, not "
                      "corporate-safe humor.",
            "messages": [{
                "role": "user",
                "content": f"Story: {meme_brief}\n\nAvailable templates (with required box_count): {json.dumps(template_options)}"
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
    return parse_best_json(text, ["template_name", "captions"])


def render_meme(template_id, captions):
    """captions is a list — length MUST match the template's box_count, or
    Imgflip silently leaves the missing boxes blank (this was the original
    bug: only text0/text1 were ever sent, so 4-box templates like Expanding
    Brain rendered with two empty panels)."""
    data = {
        "template_id": template_id,
        "username": IMGFLIP_USERNAME,
        "password": IMGFLIP_PASSWORD,
    }
    for i, caption in enumerate(captions):
        data[f"text{i}"] = caption

    resp = requests.post("https://api.imgflip.com/caption_image", data=data, timeout=30)
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
    captions = choice.get("captions", [])
    if len(captions) != template["box_count"]:
        print(f"WARNING: model returned {len(captions)} captions but "
              f"{choice['template_name']} needs {template['box_count']} — "
              f"padding/truncating to fit rather than failing the run.")
        captions = (captions + [""] * template["box_count"])[:template["box_count"]]

    image_url = render_meme(template["id"], captions)

    with open(PATHS["meme"], "w") as f:
        json.dump({"image_url": image_url, "template_name": choice["template_name"], "captions": captions}, f, indent=2)

    print(f"Meme rendered: {image_url}")


if __name__ == "__main__":
    main()
