"""Project / slide state: creation from a parsed brief, persistence, auto layout."""
import json
import re
import time
from pathlib import Path

import layout as L
import render as R

ROOT = Path(__file__).resolve().parent.parent / "projects"


def slugify(name):
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-")
    return s or time.strftime("carousel-%Y%m%d-%H%M")


def project_dir(name):
    return ROOT / name


def media_dir(name):
    return project_dir(name) / "media"


def new_slide(parsed):
    preset = parsed.get("preset") or "body"
    size = R.PRESETS.get(preset, {}).get("body_size")
    slide = {
        "index": parsed["index"],
        "preset": preset,
        "media_type": parsed.get("media_type"),
        "media": [{"url": u, "kind": parsed.get("media_type"), "status": "pending", "file": None,
                   "poster": None, "expected_name": None, "error": None}
                  for u in parsed.get("media_urls", [])],
        "media_sel": 0,
        "headline": parsed.get("headline", []),
        "subline": parsed.get("subline", ""),
        "kicker": None,
        "body": parsed.get("body", ""),
        "lines": [],
        "lines_auto": True,
        "n_lines": None,
        "body_size": size,
        "zone": R.PRESETS.get(preset, {}).get("body_default_zone", "bottom-center"),
        "zone_auto": True,
        "offsets": {},
        "shade": dict(R.SHADE_DEFAULTS),
        "crop": {"fx": 0.5, "fy": 0.5, "zoom": 1.0},
        "legibility": {"score": None, "low": False},
    }
    rewrap(slide)
    return slide


def rewrap(slide):
    if slide["lines_auto"]:
        slide["lines"], slide["body_size"] = L.wrap_body(slide["body"], slide.get("body_size"),
                                                         slide.get("n_lines"))
    elif not slide.get("body_size"):
        slide["body_size"] = L.BODY["size"]
    slide["body_size"] = slide["body_size"] or L.BODY["size"]


_MAPS = {}


def readability_maps(slide, mdir):
    """Analysis maps for the slide's current background, cached per source + crop."""
    src = R.source_still(slide, mdir)
    if src is None:
        return None
    key = (str(src), src.stat().st_mtime, json.dumps(slide["crop"], sort_keys=True))
    if key not in _MAPS:
        if len(_MAPS) > 24:
            _MAPS.pop(next(iter(_MAPS)))
        bg = R.background(slide, mdir, scale=0.5, fast=True)
        _MAPS[key] = L.analyse(bg, R.faces_on_canvas(slide, mdir))
    return _MAPS[key]


def autoplace(slide, mdir):
    """Choose the most readable zone for the body text and reset its drag offset."""
    lines = slide["lines"]
    if not any(lines):
        return
    prefer = R.PRESETS.get(slide["preset"], {}).get("body_default_zone")
    zone, info = L.auto_zone(readability_maps(slide, mdir), lines, slide["body_size"], prefer)
    slide["zone"], slide["legibility"] = zone, info
    slide["offsets"].pop("body", None)


def update_legibility(slide, mdir):
    if not any(slide["lines"]):
        slide["legibility"] = {"score": None, "low": False}
        return
    x, y, w, h = L.block_rect(slide["zone"], slide["lines"], slide["body_size"])
    dx, dy = slide["offsets"].get("body", (0, 0))
    slide["legibility"] = L.legibility(readability_maps(slide, mdir), (x + dx, y + dy, w, h))


def save(project):
    d = project_dir(project["name"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")


def load(name):
    return json.loads((project_dir(name) / "project.json").read_text(encoding="utf-8"))


def list_projects():
    if not ROOT.exists():
        return []
    items = [p for p in ROOT.iterdir() if (p / "project.json").exists()]
    items.sort(key=lambda p: (p / "project.json").stat().st_mtime, reverse=True)
    return [p.name for p in items]


def create(name, brief, parsed_slides):
    name = slugify(name)
    base, n = name, 2
    while (project_dir(name) / "project.json").exists():
        name, n = f"{base}-{n}", n + 1
    project = {"name": name, "created": time.time(), "brief": brief,
               "slides": [new_slide(s) for s in parsed_slides]}
    media_dir(name).mkdir(parents=True, exist_ok=True)
    save(project)
    return project
