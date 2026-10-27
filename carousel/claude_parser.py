"""Optional: parse a brief with Claude. Returns the same schema as brief_parser.parse_brief.

Needs `pip install anthropic` and credentials (ANTHROPIC_API_KEY or `ant auth login`).
"""
import json

import anthropic

MODEL = "claude-opus-5-5"

SYSTEM = """You turn Instagram carousel briefs (often German) into structured slides.
For each slide return:
- index: slide number
- preset: "ontour" when the slide asks for big headline typography like ON TOUR ("Groß Typo",
  "Typo wie ON TOUR"), "gettoknow" for a small centred title like GET TO KNOW ("Klein Typo"),
  otherwise "body"
- headline: the short headline lines of a title slide exactly as written (e.g. ["LIVE", "TASTING"]), else []
- subline: the longer line under the headline on a title slide, keeping any "|" separators, else ""
- body: the slide's running text exactly as written in the brief; keep list items on separate lines,
  else ""
- media_type: "image" for Bild/Foto, "video" for Video, null if none
- media_urls: every media link for the slide, in order (alternatives after "oder" included)
Never translate, shorten or rephrase any text."""

SCHEMA = {
    "type": "object",
    "properties": {
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "preset": {"type": "string", "enum": ["body", "ontour", "gettoknow"]},
                    "headline": {"type": "array", "items": {"type": "string"}},
                    "subline": {"type": "string"},
                    "body": {"type": "string"},
                    "media_type": {"type": ["string", "null"], "enum": ["image", "video", None]},
                    "media_urls": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["index", "preset", "headline", "subline", "body", "media_type",
                             "media_urls"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["slides"],
    "additionalProperties": False,
}


def parse_brief(brief):
    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": brief}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to parse this brief – use the rule-based parser")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off – the brief may be too long")
    text = next(b.text for b in response.content if b.type == "text")
    slides = json.loads(text)["slides"]
    slides.sort(key=lambda s: s["index"])
    return slides
