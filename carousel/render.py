"""Render slide text elements, compose slides and export PNG / MP4 / preview PDF."""
import functools
import hashlib
import json
import math
import subprocess
import sys
import threading
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

import layout as L

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ffmpeg_utils import find_ffmpeg, probe_video, overlay_cmd, NO_WINDOW  # noqa: E402

W, H = L.W, L.H
WHITE = tuple(L.CFG["color"]) + (255,)
PRESETS = L.CFG["presets"]
LOGO = Path(__file__).parent / "assets" / "logo.png"


# ── background ──────────────────────────────────────────────────────────────
def crop_params(iw, ih, crop):
    """Cover-fit iw x ih into the canvas with zoom and focus (0..1). Shared by images and ffmpeg."""
    zoom = max(1.0, float(crop.get("zoom", 1.0)))
    s = max(W / iw, H / ih) * zoom
    sw, sh = max(W, math.ceil(iw * s)), max(H, math.ceil(ih * s))
    sw += sw % 2
    sh += sh % 2
    x = round((sw - W) * min(1, max(0, crop.get("fx", 0.5))))
    y = round((sh - H) * min(1, max(0, crop.get("fy", 0.5))))
    return sw, sh, x, y


def source_still(slide, media_dir):
    """Path of the still image used for layout (image file, or video poster)."""
    if not slide["media"]:
        return None
    m = slide["media"][min(slide.get("media_sel", 0), len(slide["media"]) - 1)]
    name = m.get("poster") if m.get("kind") == "video" else m.get("file")
    name = name or m.get("file")
    if not name or Path(name).suffix.lower() in (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"):
        return None
    p = Path(media_dir) / name
    return p if p.exists() else None


@functools.lru_cache(maxsize=6)
def _load_source(path, mtime):
    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def load_source(path):
    """Decoded source image, kept in memory so crop changes don't re-decode the JPEG."""
    path = Path(path)
    return _load_source(str(path), path.stat().st_mtime)


def background(slide, media_dir, scale=1.0, fast=False):
    """The cropped background at canvas size (or `scale` of it, e.g. 0.5 for analysis)."""
    src = source_still(slide, media_dir)
    if src is None:
        return None
    im = load_source(src)
    sw, sh, x, y = crop_params(im.width, im.height, slide["crop"])
    sx, sy = im.width / sw, im.height / sh
    # resample only the visible region instead of resizing the whole source
    return im.resize((round(W * scale), round(H * scale)),
                     Image.BILINEAR if fast else Image.LANCZOS,
                     box=(x * sx, y * sy, (x + W) * sx, (y + H) * sy),
                     reducing_gap=2.0 if fast else None)


def proxy(src, cache_dir, max_side=2000):
    """Downscaled copy of the source for the browser to crop/zoom live. Returns (name, w, h)."""
    src = Path(src)
    key = hashlib.sha1(f"{src}{src.stat().st_mtime}".encode()).hexdigest()[:10]
    name = f"src_{key}.jpg"
    out = Path(cache_dir) / name
    im = load_source(src)
    f = min(1.0, max_side / max(im.size))
    w, h = round(im.width * f), round(im.height * f)
    if not out.exists():
        im.resize((w, h), Image.LANCZOS).save(out, quality=88)
    return name, w, h


def faces_on_canvas(slide, media_dir):
    """Face rects in canvas px for the current crop. Detection runs once per source image."""
    src = source_still(slide, media_dir)
    if src is None:
        return []
    cache = Path(media_dir) / ".faces"
    cache.mkdir(exist_ok=True)
    cf = cache / (src.name + ".json")
    if cf.exists() and cf.stat().st_mtime >= src.stat().st_mtime:
        faces = json.loads(cf.read_text())
    else:
        im = load_source(src)
        f = min(1.0, 1200 / max(im.size))
        small = im.convert("L").resize((round(im.width * f), round(im.height * f)))
        faces = [[v / f for v in r] for r in L.detect_faces(small)]
        cf.write_text(json.dumps(faces))
    im = load_source(src)
    sw, sh, x, y = crop_params(im.width, im.height, slide["crop"])
    s = sw / im.width
    return [[fx * s - x, fy * s - y, fw * s, fh * s] for fx, fy, fw, fh in faces]


# ── text drawing ────────────────────────────────────────────────────────────
def draw_tracked(draw, text, x, baseline, font_key, size, tracking):
    font = L.get_font(font_key, size)
    tr = tracking * size
    for i, ch in enumerate(text):
        if ch == " ":
            continue
        # prefix width incl. kerning into this glyph
        cx = x + font.getlength(text[:i + 1]) - font.getlength(ch) + tr * i
        draw.text((cx, baseline), ch, font=font, fill=WHITE, anchor="ls")


_rec = threading.local()      # while text_runs() runs, every drawn line is recorded here


def draw_lines(draw, lines, align, ax, y0, font_key, size, tracking, leading):
    for i, line in enumerate(lines):
        if not line:
            continue
        w = L.measure(line, font_key, size, tracking)
        x = {"left": ax, "center": ax - w / 2, "right": ax - w}[align]
        baseline = y0 + i * size * leading
        runs = getattr(_rec, "runs", None)
        if runs is not None:
            runs.append({"element": getattr(_rec, "eid", None),
                         "text": line, "font": font_key, "size": size, "tracking": tracking,
                         "align": align, "ax": ax, "x": x, "baseline": baseline})
        draw_tracked(draw, line, x, baseline, font_key, size, tracking)


# ── shade (soft cushion behind text) ────────────────────────────────────────
SHADE_DEFAULTS = {"on": False, "opacity": 0.55, "softness": 45, "spread": 60,
                  "mode": "multiply", "color": "#000000", "shape": "box"}
LOGO_SHADE_DEFAULTS = {"on": False, "opacity": 0.5, "softness": 60, "spread": 30,
                       "mode": "multiply", "color": "#000000"}
LINEAR_FADE = 3          # linear shade: fade length = softness × this (px)
GRAPHIC_IDS = ("logo", "graphic")


def shade_settings(slide):
    sh = slide.get("shade")
    if isinstance(sh, bool) or sh is None:      # older projects stored a plain on/off
        sh = {"on": bool(sh)}
    return {**SHADE_DEFAULTS, **sh}


def shade_rects(slide, els):
    """One rounded rect per text element (the logo gets none), grown by `spread`.
    The browser draws the same rects with CSS blur for the live preview."""
    sp = shade_settings(slide)["spread"]
    return [(e["x"] - sp, e["y"] - sp, e["x"] + e["img"].width + sp, e["y"] + e["img"].height + sp)
            for e in els if e["id"] not in ("logo", "graphic")]


def _mask(sh, rects):
    """L-mode mask 0..255 (already multiplied by opacity) for one shade layer, or None."""
    if not sh["on"] or not rects or sh["opacity"] <= 0:
        return None
    if sh.get("shape") == "linear":
        profile = linear_profile(rects, sh["softness"])
        col = Image.frombytes("L", (1, H), bytes(round(v * 255 * sh["opacity"]) for v in profile))
        return col.resize((W, H), Image.NEAREST)
    q = 4                                        # blur at quarter resolution for speed
    small = Image.new("L", (W // q, H // q), 0)
    d = ImageDraw.Draw(small)
    for x0, y0, x1, y1 in rects:
        r = min(sh["spread"], (y1 - y0) / 2, (x1 - x0) / 2) / q
        d.rounded_rectangle((x0 / q, y0 / q, x1 / q, y1 / q), radius=max(0, r), fill=255)
    if sh["softness"] > 0:
        small = small.filter(ImageFilter.GaussianBlur(sh["softness"] / q))
    mask = small.resize((W, H), Image.BILINEAR)
    return mask.point(lambda v: round(v * sh["opacity"]))


def shade_mask(slide, els):
    """Mask of the shade behind the text, or None when it is off."""
    return _mask(shade_settings(slide), shade_rects(slide, els))


# ── gradient from the top behind the logo ───────────────────────────────────
def logo_shade_settings(slide):
    return {**LOGO_SHADE_DEFAULTS, **(slide.get("logo_shade") or {}), "shape": "linear"}


def logo_rects(slide, els):
    sp = logo_shade_settings(slide)["spread"]
    return [(e["x"] - sp, e["y"] - sp, e["x"] + e["img"].width + sp, e["y"] + e["img"].height + sp)
            for e in els if e["id"] in GRAPHIC_IDS]


def shade_layers(slide, els):
    """Active shade layers as (settings, mask), multiply layers first – the order video
    rendering applies them in (multiply on the frames, normal baked into the overlay)."""
    layers = [(shade_settings(slide), shade_mask(slide, els))]
    ls = logo_shade_settings(slide)
    layers.append((ls, _mask(ls, logo_rects(slide, els))))
    layers = [(sh, m) for sh, m in layers if m is not None]
    return sorted(layers, key=lambda l: l[0]["mode"] != "multiply")


def linear_profile(rects, softness):
    """Full-width linear shade: one value 0..1 per pixel row (max over all text blocks).
    Text in the lower part rises from the bottom edge, in the upper part hangs from the top,
    in the middle it is a band; solid over the (spread-grown) text, then a linear fade.
    static/app.js renderShades() builds the identical CSS gradient for the live preview."""
    fade = max(1.0, softness * LINEAR_FADE)
    prof = [0.0] * H
    for _, y0, _, y1 in rects:
        c = (y0 + y1) / 2
        lo = 0 if c < H * 0.4 else y0              # solid from lo to hi
        hi = H if c > H * 0.6 else y1
        for y in range(H):
            if lo <= y <= hi:
                v = 1.0
            elif y < lo:
                v = max(0.0, 1 - (lo - y) / fade)
            else:
                v = max(0.0, 1 - (y - hi) / fade)
            if v > prof[y]:
                prof[y] = v
    return prof


def _hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else (0, 0, 0)


def apply_shade(bg_rgb, slide, els):
    """normal: mix towards the colour; multiply: bg * colour, both weighted by the mask."""
    for sh, mask in shade_layers(slide, els):
        color = Image.new("RGB", (W, H), _hex_rgb(sh["color"]))
        target = ImageChops.multiply(bg_rgb, color) if sh["mode"] == "multiply" else color
        bg_rgb = Image.composite(target, bg_rgb, mask)
    return bg_rgb


def shade_multiplier(slide, els):
    """RGB image that ffmpeg's blend=multiply turns into the multiply shades on video frames."""
    white = Image.new("RGB", (W, H), (255, 255, 255))
    out = None
    for sh, mask in shade_layers(slide, els):
        if sh["mode"] != "multiply":
            continue
        m = Image.composite(Image.new("RGB", (W, H), _hex_rgb(sh["color"])), white, mask)
        out = m if out is None else ImageChops.multiply(out, m)
    return out


def _crop_element(canvas, eid, pad=2):
    bb = canvas.getbbox()
    if not bb:
        return None
    x0, y0 = max(0, bb[0] - pad), max(0, bb[1] - pad)
    x1, y1 = min(W, bb[2] + pad), min(H, bb[3] + pad)
    return {"id": eid, "img": canvas.crop((x0, y0, x1, y1)), "x": x0, "y": y0}


# ── elements ────────────────────────────────────────────────────────────────
def body_element(slide):
    lines, size = slide["lines"], slide["body_size"]
    if not any(lines):
        return None
    align, ax, y0 = L.zone_anchor(slide["zone"], lines, size)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_lines(ImageDraw.Draw(canvas), lines, align, ax, y0, "neutra", size,
               L.BODY["tracking"], L.BODY["leading"])
    return _crop_element(canvas, "body")


def ontour_elements(slide):
    p = PRESETS["ontour"]
    hs, ss = p["headline"], p["subline"]
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    sub = L.wrap_subline(slide.get("subline", ""), ss)
    sub_lead = ss["size"] * ss["leading"]
    sub_y0 = ss["last_baseline"] - sub_lead * (len(sub) - 1)
    draw_lines(d, sub, "left", ss["x"], sub_y0, ss["font"], ss["size"], ss["tracking"], ss["leading"])

    head = [L.upper(h) for h in slide.get("headline", []) if h.strip()]
    if head:
        size = hs["size"]
        widest = max(L.measure(h, hs["font"], size, hs["tracking"]) for h in head)
        if widest > W - 2 * hs["x"]:
            size *= (W - 2 * hs["x"]) / widest
        lead = size * hs["leading"]
        last = (sub_y0 if sub else ss["last_baseline"]) - (p["gap_headline_subline"] if sub else 0)
        y0 = last - lead * (len(head) - 1)
        draw_lines(d, head, "left", hs["x"], y0, hs["font"], size, hs["tracking"], hs["leading"])
    els = [e for e in [_crop_element(canvas, "title")] if e]
    if p.get("logo") and LOGO.exists():
        lg = Image.open(LOGO).convert("RGBA")
        lw = p["logo"]["width"]
        lg = lg.resize((lw, round(lg.height * lw / lg.width)), Image.LANCZOS)
        els.append({"id": "logo", "img": lg, "x": round(p["logo"]["center_x"] - lw / 2),
                    "y": p["logo"]["top"]})
    return els


def gettoknow_elements(slide):
    p = PRESETS["gettoknow"]
    head = [h for h in slide.get("headline", []) if h.strip()]
    kicker = slide.get("kicker") if slide.get("kicker") is not None else (head[0] if len(head) > 1 else "")
    title = " ".join(head[1:] if len(head) > 1 else head)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    for spec, txt in ((p["kicker"], kicker), (p["title"], title)):
        if txt:
            draw_lines(d, [L.upper(txt)], "center", spec["center_x"], spec["baseline"],
                       spec["font"], spec["size"], spec["tracking"], 1.2)
    return [e for e in [_crop_element(canvas, "title")] if e]


# ── imported presets (preset_import.py) ─────────────────────────────────────
def is_template(slide_or_key):
    key = slide_or_key if isinstance(slide_or_key, str) else slide_or_key.get("preset")
    return PRESETS.get(key, {}).get("type") == "template"


def slide_stem(slide):
    """File name stem: slide03, or slide03_alt / slide03_alt2 for "oder:" alternatives."""
    alt = slide.get("alt") or 0
    return f"slide{slide['index']:02d}" + ("" if not alt else "_alt" if alt == 1 else f"_alt{alt}")


def _slot_lines(text, sp):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if sp.get("uppercase"):
        lines = [L.upper(l) for l in lines]
    size = sp["size"]
    if sp["role"] == "headline":                      # headline lines are kept, shrink to fit
        widest = max((L.measure(l, sp["font"], size, sp["tracking"]) for l in lines), default=0)
        if widest > sp["max_width"]:
            size *= sp["max_width"] / widest
        return lines, size
    out = []
    for l in lines:
        head, _, tail = l.partition("|")
        if tail:                                      # "TITLE | DATE | TIME": rest on its own line
            out += L.wrap_balanced(head.strip(), sp["font"], size, sp["tracking"], sp["max_width"])
            out.append(" | ".join(p.strip() for p in tail.split("|")))
        elif L.measure(l, sp["font"], size, sp["tracking"]) > sp["max_width"]:
            out += L.wrap_balanced(l, sp["font"], size, sp["tracking"], sp["max_width"])
        else:
            out.append(l)
    return out, size


def template_elements(slide):
    p = PRESETS[slide["preset"]]
    texts = slide.get("slots") or {}
    laid = {}
    for sid in p["order"]:
        sp = p["slots"][sid]
        lines, size = _slot_lines(texts.get(sid, sp["sample"]), sp)
        if lines:
            laid[sid] = (sp, lines, size)
    # stack the blocks with the template's gaps, growing away from the anchored edge
    pos, order = {}, [s for s in p["order"] if s in laid]
    if p["anchor"] == "bottom":
        last = p["slots"][p["order"][-1]]["last_baseline"]
        for i, sid in enumerate(reversed(order)):
            sp, lines, size = laid[sid]
            if i:
                below = order[len(order) - i]
                last = pos[below] - p["slots"][below].get("gap_above", sp["size"])
            pos[sid] = last - size * sp["leading"] * (len(lines) - 1)
    else:
        first = p["slots"][p["order"][0]]["first_baseline"]
        prev = None
        for sid in order:
            sp, lines, size = laid[sid]
            if prev:
                psp, plines, psize = laid[prev]
                first = pos[prev] + psize * psp["leading"] * (len(plines) - 1) + sp.get("gap_above", sp["size"])
            pos[sid] = first
            prev = sid
    els = []
    for sid in order:
        sp, lines, size = laid[sid]
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        prev_eid, _rec.eid = getattr(_rec, "eid", None), f"t_{sid}"
        try:
            draw_lines(ImageDraw.Draw(canvas), lines, sp["align"], sp["x"], pos[sid], sp["font"],
                       size, sp["tracking"], sp["leading"])
        finally:
            _rec.eid = prev_eid
        e = _crop_element(canvas, f"t_{sid}")
        if e:
            els.append(e)
    g = p.get("graphics")
    gfile = Path(p.get("_dir", Path(__file__).parent / "presets_user")) / g["file"] if g else None
    if gfile and gfile.exists():
        x, y, w, h = g["rect"]
        img = Image.open(gfile).convert("RGBA").resize((max(1, round(w)), max(1, round(h))), Image.LANCZOS)
        els.append({"id": "graphic", "img": img, "x": round(x), "y": round(y), "hires": str(gfile)})
    return els


def elements(slide):
    els = []
    if slide["preset"] == "ontour":
        els += ontour_elements(slide)
    elif slide["preset"] == "gettoknow":
        els += gettoknow_elements(slide)
    elif is_template(slide):
        els += template_elements(slide)
    b = body_element(slide)
    if b:
        els.append(b)
    for e in els:
        dx, dy = slide.get("offsets", {}).get(e["id"], (0, 0))
        e["x"] += round(dx)
        e["y"] += round(dy)
    return els


def text_runs(slide):
    """Every text line as a positioned run (for the vector/Illustrator export),
    laid out by exactly the same code as the raster render, drag offsets applied."""
    jobs = []
    if slide["preset"] == "ontour":
        jobs.append(("title", ontour_elements))
    elif slide["preset"] == "gettoknow":
        jobs.append(("title", gettoknow_elements))
    elif is_template(slide):
        jobs.append((None, template_elements))       # sets its own per-block element ids
    jobs.append(("body", body_element))
    out = []
    for eid, fn in jobs:
        _rec.runs, _rec.eid = [], eid
        try:
            fn(slide)
        finally:
            runs, _rec.runs, _rec.eid = _rec.runs, None, None
        for r in runs:
            dx, dy = slide.get("offsets", {}).get(r["element"], (0, 0))
            r.update(x=r["x"] + dx, ax=r["ax"] + dx, baseline=r["baseline"] + dy)
        out += runs
    return out


def text_layer(slide, els=None, with_normal_shade=False):
    """Transparent full-frame text layer. with_normal_shade bakes a normal-mode shade into
    the alpha (used for video, where it is simply overlaid)."""
    els = elements(slide) if els is None else els
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if with_normal_shade:
        for sh, mask in shade_layers(slide, els):
            if sh["mode"] != "multiply":
                tint = Image.new("RGBA", (W, H), _hex_rgb(sh["color"]) + (0,))
                tint.putalpha(mask)
                layer.alpha_composite(tint)
    for e in els:
        layer.alpha_composite(e["img"], (max(0, e["x"]), max(0, e["y"])),
                              (max(0, -e["x"]), max(0, -e["y"])))
    return layer


def compose(slide, media_dir):
    els = elements(slide)
    bg = background(slide, media_dir) or Image.new("RGB", (W, H), (40, 40, 40))
    base = apply_shade(bg, slide, els).convert("RGBA")
    base.alpha_composite(text_layer(slide, els))
    return base.convert("RGB")


# ── export ──────────────────────────────────────────────────────────────────
def selected_media(slide):
    if not slide["media"]:
        return None
    return slide["media"][min(slide.get("media_sel", 0), len(slide["media"]) - 1)]


def save_still(still, out_dir, stem, formats):
    written = []
    if "png" in formats:
        still.save(Path(out_dir) / f"{stem}.png")
        written.append(f"{stem}.png")
    if "jpg" in formats:
        still.save(Path(out_dir) / f"{stem}.jpg", quality=95, subsampling=0)
        written.append(f"{stem}.jpg")
    return written


def export_slide(slide, media_dir, out_dir, log=print, formats=("png", "jpg")):
    """Image slides: slideNN.png / .jpg (as chosen in formats).
    Video slides: slideNN.mp4, plus the transparent text layer slideNN_text.png when PNG is on.
    Returns (still image for the preview PDF, written file names)."""
    import tempfile
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = slide_stem(slide)
    m = selected_media(slide)
    still = compose(slide, media_dir)
    if not (m and m.get("kind") == "video"):
        return still, save_still(still, out_dir, stem, formats)
    written = []
    els = elements(slide)
    layer = text_layer(slide, els, with_normal_shade=True)
    if "png" in formats:
        layer.save(out_dir / f"{stem}_text.png")
        written.append(f"{stem}_text.png")
    if not m.get("file"):
        log(f"{stem}: video file missing – exported the first-frame still instead")
        return still, written + save_still(still, out_dir, f"{stem}_poster", formats or ("jpg",))
    ffmpeg, _ = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    with tempfile.TemporaryDirectory() as tmp:         # ffmpeg inputs that aren't deliverables
        layer_png = Path(tmp) / "text.png"
        layer.save(layer_png)
        mult = shade_multiplier(slide, els)
        mult_png = None
        if mult is not None:
            mult_png = Path(tmp) / "shade_multiply.png"
            mult.save(mult_png)
        video = Path(media_dir) / m["file"]
        iw, ih, _dur = probe_video(ffmpeg, video)
        sw, sh, x, y = crop_params(iw, ih, slide["crop"])
        out = out_dir / f"{stem}.mp4"
        cmd = overlay_cmd(ffmpeg, video, layer_png, out, sw, sh, x, y, multiply_png=mult_png)
        log(f"{stem}: rendering video…")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {stem}:\n{r.stderr[-1500:]}")
    written.append(out.name)
    return still, written


def export_pdf(stills, out):
    if not stills:
        return
    first, rest = stills[0], stills[1:]
    first.save(out, save_all=True, append_images=rest, resolution=72.0, quality=92)
