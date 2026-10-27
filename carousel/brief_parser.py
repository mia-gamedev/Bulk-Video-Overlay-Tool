"""Rule-based parser for carousel briefs.

A brief looks like:

    Slide 1 (Typo wie ON TOUR)
    Groß Typo:
    LIVE
    TASTING
    SUMMER EDITION & HOUSE RIESLING | 01.06.2030 | 18:00 UHR
    Bild:
    https://...
    oder:
    https://...

    Slide 2:
    Video:
    https://...
    Text:
    Verkostet gemeinsam mit ...

Every slide becomes a dict with the schema shared with claude_parser.py:
    {index, preset, headline[], subline, body, media_type, media_urls[]}
"""
import json
import re
from pathlib import Path

PRESETS = json.loads((Path(__file__).parent / "presets.json").read_text(encoding="utf-8"))["presets"]

SLIDE_RE = re.compile(r"^\s*(?:slide|folie)\s*(\d+)\s*(.*)$", re.I)
LABEL_RE = re.compile(
    r"^\s*(bild|foto|image|video|text|copy|gro(?:ß|ss)e?\s*typo|klein\w*\s*typo|typo|headline|titel)\s*:\s*(.*)$",
    re.I)
URL_RE = re.compile(r"https?://\S+")
OR_RE = re.compile(r"^\s*(oder|or|alternativ\w*)\s*:?\s*$", re.I)


def _label_kind(label):
    l = label.lower()
    if l in ("bild", "foto", "image"):
        return "image"
    if l == "video":
        return "video"
    if l in ("text", "copy"):
        return "text"
    if l.startswith("klein"):
        return "title_small"
    return "title"


def _match_preset(text):
    t = re.sub(r"[^a-z]", "", text.lower())
    for key, p in PRESETS.items():
        for alias in p.get("aliases", []):
            if re.sub(r"[^a-z]", "", alias.lower()) in t:
                return key
    return None


def _split_title(lines):
    """Short leading lines are the headline; everything after is the sub line."""
    headline, rest = [], []
    for ln in lines:
        words = ln.split()
        if not rest and "|" not in ln and len(words) <= 3 and len(ln) <= 18:
            headline.append(ln.strip())
        else:
            rest.append(ln.strip())
    return headline, " ".join(rest)


def _new_slide(index, header_rest):
    return {"index": index, "preset": _match_preset(header_rest) if header_rest else None,
            "headline": [], "subline": "", "body": "", "media_type": None, "media_urls": [],
            "_title_kind": None}


def parse_brief(text):
    slides, cur, section = [], None, None
    sections = {}

    def flush():
        if cur is None:
            return
        title_lines = [l.strip() for l in sections.get("title", []) if l.strip() and not URL_RE.search(l)]
        if cur["preset"] is None:
            if cur["_title_kind"] == "title_small":
                cur["preset"] = "gettoknow"
            elif cur["_title_kind"] == "title" or (cur["index"] == 1 and title_lines):
                cur["preset"] = "ontour"
            else:
                cur["preset"] = "body"
        if cur["preset"] == "gettoknow":
            cur["headline"] = title_lines          # kicker line + title line
        elif title_lines:
            cur["headline"], cur["subline"] = _split_title(title_lines)
        body_lines = sections.get("text", [])
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        cur["body"] = "\n".join(l.rstrip() for l in body_lines)
        del cur["_title_kind"]
        slides.append(cur)

    for raw in text.splitlines():
        m = SLIDE_RE.match(raw)
        if m:
            flush()
            cur, section, sections = _new_slide(int(m.group(1)), m.group(2)), None, {}
            continue
        if cur is None:            # text before the first "Slide N" line
            continue
        m = LABEL_RE.match(raw)
        if m:
            kind = _label_kind(m.group(1))
            if kind in ("image", "video"):
                cur["media_type"] = cur["media_type"] or kind
                section = "media"
            elif kind == "text":
                section = "text"
            else:
                cur["_title_kind"] = cur["_title_kind"] or kind
                section = "title"
            inline = m.group(2)
            if inline.strip():
                raw = inline
            else:
                continue
        if OR_RE.match(raw):
            continue
        urls = URL_RE.findall(raw)
        if urls:
            cur["media_urls"] += [u for u in urls if u not in cur["media_urls"]]
            if cur["media_type"] is None:
                cur["media_type"] = "video" if "frame.io" in urls[0] else "image"
            continue
        if section in ("text", "title"):
            sections.setdefault(section, []).append(raw)
        elif section is None and raw.strip() and cur["preset"] is None and not cur["body"]:
            # header line like "(Typo wie ON TOUR)" on its own line
            p = _match_preset(raw)
            if p:
                cur["preset"] = p
    flush()
    return slides


if __name__ == "__main__":
    import sys
    print(json.dumps(parse_brief(Path(sys.argv[1]).read_text(encoding="utf-8")), indent=2, ensure_ascii=False))
