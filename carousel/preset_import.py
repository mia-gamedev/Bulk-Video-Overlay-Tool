"""Import a title-slide design from an Illustrator (.ai, PDF compatible) or PDF file
as a new preset.

What is measured from the artboard:
    text blocks   font, size, tracking, leading, alignment, anchor, upper case
    roles         largest text = headline, text above it = kicker, text below = subline;
                  text far away from the title group is the body text (size + zone)
    graphics      all vector art (e.g. a logo) as a transparent layer; photos are ignored

Presets are stored in carousel/presets_user/<key>.json (+ graphics PNG) and merged
into the built-in ones from presets.json.
"""
import functools
import io
import json
import os
import re
import sys
import types
from pathlib import Path

from PIL import Image, ImageFont

import layout as L

HERE = Path(__file__).parent
USER_DIR = Path(os.environ.get("CAROUSEL_PRESETS") or HERE / "presets_user")
W, H = L.W, L.H
BODY_GAP = 150          # text further than this below the title group counts as body text


# ── PyMuPDF (some installs ship a broken optional "layout" add-on) ──────────
def _fitz():
    try:
        import pymupdf
        return pymupdf
    except Exception:
        for k in [k for k in sys.modules if k.split(".")[0] in ("pymupdf", "fitz", "pymupdf4llm")]:
            del sys.modules[k]
        sys.modules["pymupdf.layout"] = types.ModuleType("pymupdf.layout")
        sys.modules["pymupdf4llm"] = types.ModuleType("pymupdf4llm")
        import pymupdf
        return pymupdf


# ── fonts: PostScript name in the file → installed font file ────────────────
def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower()).replace("regular", "")


@functools.lru_cache(maxsize=1)
def _font_index():
    idx = {}
    for d in L._font_dirs():
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.suffix.lower() not in (".ttf", ".otf"):
                continue
            try:
                fam, sty = ImageFont.truetype(str(f), 10).getname()
            except Exception:
                continue
            for key in (_norm(fam + sty), _norm(f.stem)):
                idx.setdefault(key, f.name)
    return idx


def font_key_for(ps_name):
    """Return (font key, None) or (fallback key, warning). Registers new fonts in L.CFG."""
    ps = re.sub(r"^[A-Z]{6}\+", "", ps_name)            # drop subset prefix
    for key, files in L.CFG["fonts"].items():         # already known (neutra, trajan, …)
        try:
            if _norm(Path(L.font_path(key)).stem) == _norm(ps) or \
               _norm("".join(ImageFont.truetype(L.font_path(key), 10).getname())) == _norm(ps):
                return key, None
        except FileNotFoundError:
            continue
    file = _font_index().get(_norm(ps))
    if file:
        key = _norm(ps)
        L.CFG["fonts"].setdefault(key, [file])
        return key, None
    return "neutra", f"font '{ps}' is not installed – using Neutraface Text instead"


# ── text extraction ─────────────────────────────────────────────────────────
def _lines(page, k, fitz):
    """Text lines with metrics, in canvas px. Real spaces only (no synthetic ones from tracking)."""
    flags = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_INHIBIT_SPACES
    raw = page.get_text("rawdict", flags=flags)
    out = []
    for b in raw["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            chars = [c for s in ln["spans"] for c in s["chars"]]
            if not chars or abs(ln["dir"][1]) > 0.01:
                continue
            span = max(ln["spans"], key=lambda s: len(s["chars"]))
            text = "".join(c["c"] for c in chars).rstrip()
            ink = [c for c in chars if not c["c"].isspace()]
            if not ink:
                continue
            out.append({
                "text": text, "font": span["font"], "size": span["size"] * k,
                "baseline": span["origin"][1] * k,
                "x0": ink[0]["bbox"][0] * k, "x1": ink[-1]["bbox"][2] * k,
                "n": len(text.strip()),
            })
    # merge pieces of the same visual line (Illustrator splits at kerning changes)
    out.sort(key=lambda l: (round(l["baseline"]), l["x0"]))
    merged = []
    for l in out:
        m = merged[-1] if merged else None
        if m and abs(m["baseline"] - l["baseline"]) < 2 and m["font"] == l["font"] \
                and abs(m["size"] - l["size"]) < 0.5 and l["x0"] - m["x1"] < l["size"] * 1.5:
            m["text"] += ("" if m["text"].endswith(" ") else " ") + l["text"].strip() \
                if l["x0"] - m["x1"] > l["size"] * 0.4 else l["text"]
            m["x1"], m["n"] = l["x1"], len(m["text"].strip())
        else:
            merged.append(dict(l))
    return merged


def _group(lines):
    """Consecutive lines with the same font/size and regular spacing form one text block."""
    blocks = []
    for l in sorted(lines, key=lambda l: l["baseline"]):
        b = blocks[-1] if blocks else None
        if b and b[-1]["font"] == l["font"] and abs(b[-1]["size"] - l["size"]) < 0.5 \
                and 0 < l["baseline"] - b[-1]["baseline"] < l["size"] * 1.8:
            b.append(l)
        else:
            blocks.append([l])
    return blocks


def _align(block):
    if len(block) > 1:
        spread = {a: max(v) - min(v) for a, v in (
            ("left", [l["x0"] for l in block]), ("right", [l["x1"] for l in block]),
            ("center", [(l["x0"] + l["x1"]) / 2 for l in block]))}
        return min(spread, key=spread.get)
    l = block[0]
    c = (l["x0"] + l["x1"]) / 2
    if abs(c - W / 2) < 25:
        return "center"
    return "right" if W - l["x1"] < l["x0"] else "left"


def _spec(block, warnings):
    l0 = block[0]
    key, warn = font_key_for(l0["font"])
    if warn and warn not in warnings:
        warnings.append(warn)
    size = l0["size"]
    # tracking: measured ink width vs the untracked advance width, per glyph gap
    trs = []
    for l in block:
        if l["n"] > 3:
            base = L.measure(l["text"].strip(), key, size, 0)
            trs.append(((l["x1"] - l["x0"]) - base) / (size * (l["n"] - 1)))
    tracking = round(max(0.0, sorted(trs)[len(trs) // 2]) if trs else 0.0, 3)
    leading = round((block[-1]["baseline"] - block[0]["baseline"]) / (size * (len(block) - 1)), 3) \
        if len(block) > 1 else 1.2
    align = _align(block)
    xs = {"left": min(l["x0"] for l in block), "right": max(l["x1"] for l in block),
          "center": sum((l["x0"] + l["x1"]) / 2 for l in block) / len(block)}
    ax = xs[align]
    room = {"left": W - ax - 60, "right": ax - 60, "center": 2 * min(ax, W - ax) - 120}[align]
    letters = "".join(ch for l in block for ch in l["text"] if ch.isalpha())
    return {"font": key, "size": round(size, 2), "tracking": tracking, "leading": leading,
            "align": align, "x": round(ax, 1), "first_baseline": round(block[0]["baseline"], 1),
            "last_baseline": round(block[-1]["baseline"], 1),
            "max_width": round(max(room, max(l["x1"] - l["x0"] for l in block))),
            "uppercase": bool(letters) and letters == letters.upper(),
            "sample": "\n".join(l["text"].strip() for l in block)}


# ── graphics layer ──────────────────────────────────────────────────────────
def _graphics_png(doc, page_no, k, fitz):
    """Render the artboard with text and images removed: what is left is vector art (logo…)."""
    tmp = fitz.open()
    tmp.insert_pdf(doc, from_page=page_no, to_page=page_no)
    pg = tmp[0]
    pg.add_redact_annot(pg.rect)
    pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE,
                        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                        text=fitz.PDF_REDACT_TEXT_REMOVE)
    pix = pg.get_pixmap(matrix=fitz.Matrix(2 * k, 2 * k), alpha=True)
    im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")
    bb = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if not bb or (bb[2] - bb[0]) * (bb[3] - bb[1]) > 0.9 * im.width * im.height:
        return None, None              # nothing, or a full-bleed backdrop (not a logo)
    return im.crop(bb), [bb[0] / 2, bb[1] / 2, (bb[2] - bb[0]) / 2, (bb[3] - bb[1]) / 2]


# ── import ──────────────────────────────────────────────────────────────────
def page_count(path):
    return len(_fitz().open(str(path)))


def import_preset(path, name, page=1):
    """Analyse one artboard and save it as a preset. Returns (key, preset, warnings)."""
    fitz = _fitz()
    try:
        doc = fitz.open(str(path))
    except Exception as e:
        raise ValueError("Could not read the file. Save the .ai with "
                         "'Create PDF Compatible File' enabled (or export a PDF).") from e
    if not 1 <= page <= len(doc):
        raise ValueError(f"The file has {len(doc)} artboard(s) – page {page} does not exist")
    pg = doc[page - 1]
    k = W / pg.rect.width
    warnings = []
    if abs(pg.rect.height * k - H) > 2:
        warnings.append(f"artboard is {pg.rect.width:.0f}×{pg.rect.height:.0f}, "
                        f"scaled to {W} wide – it is not 3:4")
    blocks = _group(_lines(pg, k, fitz))
    if not blocks:
        raise ValueError("No live text found on that artboard (text converted to outlines?)")

    specs = [_spec(b, warnings) for b in blocks]
    head_i = max(range(len(specs)), key=lambda i: specs[i]["size"])
    slots, body = {}, None
    title_bottom = specs[head_i]["last_baseline"]
    for i, sp in enumerate(specs):
        if i == head_i:
            role = "headline"
        elif i < head_i:
            role = "kicker"
        elif sp["first_baseline"] - title_bottom > BODY_GAP and sp["font"] == "neutra":
            body = sp
            continue
        else:
            role = "subline"
            title_bottom = sp["last_baseline"]
        rid, n = role, 2
        while rid in slots:
            rid, n = f"{role}{n}", n + 1
        slots[rid] = {**sp, "role": role}

    # stacking: blocks keep their gaps; lower-half groups grow upwards, upper-half downwards
    order = sorted(slots, key=lambda s: slots[s]["first_baseline"])
    for a, b in zip(order, order[1:]):
        slots[b]["gap_above"] = round(slots[b]["first_baseline"] - slots[a]["last_baseline"], 1)
    anchor = "bottom" if slots[order[-1]]["last_baseline"] > H * 0.55 else "top"

    key = re.sub(r"[^a-z0-9]+", "", name.lower()) or "custom"
    USER_DIR.mkdir(exist_ok=True)
    preset = {"label": f"{name} (imported)", "type": "template", "aliases": [name.upper()],
              "slots": slots, "order": order, "anchor": anchor, "fonts": {}}
    gfx, rect = _graphics_png(doc, page - 1, k, fitz)
    if gfx is not None:
        gfx.save(USER_DIR / f"{key}_graphics.png")
        preset["graphics"] = {"file": f"{key}_graphics.png", "rect": [round(v, 1) for v in rect]}
    if body:
        v = "top" if body["first_baseline"] < H / 3 else "bottom" if body["first_baseline"] > H * 0.6 else "middle"
        preset["body_size"] = round(body["size"])
        preset["body_default_zone"] = f"{v}-{body['align']}"
    for sp in slots.values():
        if sp["font"] not in ("neutra", "trajan"):
            preset["fonts"][sp["font"]] = L.CFG["fonts"][sp["font"]]
    thumb = pg.get_pixmap(matrix=fitz.Matrix(270 / pg.rect.width, 270 / pg.rect.width))
    Image.open(io.BytesIO(thumb.tobytes("png"))).convert("RGB").save(USER_DIR / f"{key}_thumb.jpg")
    preset["warnings"] = warnings
    (USER_DIR / f"{key}.json").write_text(json.dumps(preset, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    register(key, preset)
    return key, preset, warnings


def register(key, preset):
    from render import PRESETS
    import brief_parser
    for fk, files in preset.get("fonts", {}).items():
        L.CFG["fonts"].setdefault(fk, files)
    preset["_dir"] = str(USER_DIR)            # where its graphics PNG lives (not saved)
    PRESETS[key] = preset
    brief_parser.PRESETS[key] = preset


def load_user_presets():
    if not USER_DIR.is_dir():
        return
    for f in sorted(USER_DIR.glob("*.json")):
        try:
            register(f.stem, json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[presets] could not load {f.name}: {e}", flush=True)
