"""What every AI parser (Claude, DeepSeek) is asked for, and the clean-up of its answer.

The answer has the same schema as brief_parser.parse_brief:
    {index, preset, headline[], subline, body, media_type, media_urls[], alt_media_urls[]}
"""
import copy

SYSTEM = """You turn Instagram carousel briefs (often German) into structured slides.
For each slide return:
- index: slide number
- preset: "ontour" when the slide asks for big headline typography like ON TOUR ("Groß Typo",
  "Typo wie ON TOUR"), "gettoknow" for a small centred title like GET TO KNOW ("Klein Typo"),
  one of the other preset keys listed below when the brief names that design, otherwise "body"
- headline: the short headline lines of a title slide exactly as written (e.g. ["LIVE", "TASTING"]), else []
- subline: the longer line under the headline on a title slide, keeping any "|" separators, else ""
- body: the slide's running text exactly as written in the brief; keep list items on separate lines,
  else ""
- media_type: "image" for Bild/Foto, "video" for Video, null if none
- media_urls: the slide's media link(s), in order, without the alternatives
- alt_media_urls: links offered as alternatives (after "oder" / "or"), one per alternative, else []
Never translate, shorten or rephrase any text."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "preset": {"type": "string", "enum": ["body"]},
                    "headline": {"type": "array", "items": {"type": "string"}},
                    "subline": {"type": "string"},
                    "body": {"type": "string"},
                    "media_type": {"type": ["string", "null"], "enum": ["image", "video", None]},
                    "media_urls": {"type": "array", "items": {"type": "string"}},
                    "alt_media_urls": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["index", "preset", "headline", "subline", "body", "media_type",
                             "media_urls", "alt_media_urls"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["slides"],
    "additionalProperties": False,
}


def _presets():
    from render import PRESETS
    return PRESETS


def schema():
    """JSON schema with the preset keys that exist right now (incl. imported ones)."""
    s = copy.deepcopy(_SCHEMA)
    s["properties"]["slides"]["items"]["properties"]["preset"]["enum"] = list(_presets())
    return s


def system_prompt():
    names = "\n".join(f"- {k}: {', '.join(p.get('aliases', [])) or p.get('label', k)}"
                      for k, p in _presets().items() if k != "body")
    return SYSTEM + "\n\nAvailable presets (key: names used in briefs):\n" + names


def normalize(slides):
    """Make a model's answer safe to use: right types, known presets, sorted by slide."""
    presets = _presets()
    out = []
    for i, s in enumerate(slides if isinstance(slides, list) else []):
        if not isinstance(s, dict):
            continue
        as_list = lambda v: [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])
        try:
            index = int(s.get("index", i + 1))
        except (TypeError, ValueError):
            index = i + 1
        mt = s.get("media_type")
        out.append({
            "index": index,
            "preset": s.get("preset") if s.get("preset") in presets else "body",
            "headline": as_list(s.get("headline")),
            "subline": str(s.get("subline") or ""),
            "body": str(s.get("body") or ""),
            "media_type": mt if mt in ("image", "video") else None,
            "media_urls": as_list(s.get("media_urls")),
            "alt_media_urls": as_list(s.get("alt_media_urls")),
        })
    out.sort(key=lambda s: s["index"])
    return out
