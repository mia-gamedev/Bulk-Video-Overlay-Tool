"""Render slide text elements, compose slides and export PNG / MP4 / preview PDF."""
import functools
import hashlib
import json
import math
import subprocess
import sys
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


def draw_lines(draw, lines, align, ax, y0, font_key, size, tracking, leading):
    for i, line in enumerate(lines):
        if not line:
            continue
        w = L.measure(line, font_key, size, tracking)
        x = {"left": ax, "center": ax - w / 2, "right": ax - w}[align]
        draw_tracked(draw, line, x, y0 + i * size * leading, font_key, size, tracking)


# ── shade (soft cushion behind text) ────────────────────────────────────────
SHADE_DEFAULTS = {"on": False, "opacity": 0.55, "softness": 45, "spread": 60,
                  "mode": "multiply", "color": "#000000"}


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
            for e in els if e["id"] != "logo"]


def shade_mask(slide, els):
    """L-mode mask 0..255 (already multiplied by opacity), or None when the shade is off."""
    sh = shade_settings(slide)
    rects = shade_rects(slide, els)
    if not sh["on"] or not rects or sh["opacity"] <= 0:
        return None
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


def _hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else (0, 0, 0)


def apply_shade(bg_rgb, slide, els):
    """normal: mix towards the colour; multiply: bg * colour, both weighted by the mask."""
    mask = shade_mask(slide, els)
    if mask is None:
        return bg_rgb
    sh = shade_settings(slide)
    color = Image.new("RGB", (W, H), _hex_rgb(sh["color"]))
    target = ImageChops.multiply(bg_rgb, color) if sh["mode"] == "multiply" else color
    return Image.composite(target, bg_rgb, mask)


def shade_multiplier(slide, els):
    """RGB image that ffmpeg's blend=multiply turns into the same shade on video frames."""
    mask = shade_mask(slide, els)
    if mask is None:
        return None
    sh = shade_settings(slide)
    if sh["mode"] != "multiply":
        return None
    c = _hex_rgb(sh["color"])
    white = Image.new("RGB", (W, H), (255, 255, 255))
    return Image.composite(Image.new("RGB", (W, H), c), white, mask)


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


def elements(slide):
    els = []
    if slide["preset"] == "ontour":
        els += ontour_elements(slide)
    elif slide["preset"] == "gettoknow":
        els += gettoknow_elements(slide)
    b = body_element(slide)
    if b:
        els.append(b)
    for e in els:
        dx, dy = slide.get("offsets", {}).get(e["id"], (0, 0))
        e["x"] += round(dx)
        e["y"] += round(dy)
    return els


def text_layer(slide, els=None, with_normal_shade=False):
    """Transparent full-frame text layer. with_normal_shade bakes a normal-mode shade into
    the alpha (used for video, where it is simply overlaid)."""
    els = elements(slide) if els is None else els
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if with_normal_shade and shade_settings(slide)["mode"] != "multiply":
        mask = shade_mask(slide, els)
        if mask is not None:
            layer = Image.new("RGBA", (W, H), _hex_rgb(shade_settings(slide)["color"]) + (0,))
            layer.putalpha(mask)
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


def export_slide(slide, media_dir, out_dir, log=print):
    """Write slideNN.png/.jpg, or slideNN.mp4 (+ slideNN_text.png) for video slides."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"slide{slide['index']:02d}"
    m = selected_media(slide)
    still = compose(slide, media_dir)
    written = []
    if m and m.get("kind") == "video":
        els = elements(slide)
        layer_png = out_dir / f"{stem}_text.png"
        text_layer(slide, els, with_normal_shade=True).save(layer_png)
        written.append(layer_png.name)
        mult = shade_multiplier(slide, els)
        mult_png = None
        if mult is not None:
            mult_png = out_dir / f"{stem}_shade_multiply.png"
            mult.save(mult_png)
            written.append(mult_png.name)
        if not m.get("file"):
            log(f"{stem}: video file missing – exported text PNG and poster preview only")
            still.save(out_dir / f"{stem}_poster.jpg", quality=95)
            written.append(f"{stem}_poster.jpg")
            return still, written
        ffmpeg, _ = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("ffmpeg not found")
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
    else:
        still.save(out_dir / f"{stem}.png")
        still.save(out_dir / f"{stem}.jpg", quality=95, subsampling=0)
        written += [f"{stem}.png", f"{stem}.jpg"]
    return still, written


def export_pdf(stills, out):
    if not stills:
        return
    first, rest = stills[0], stills[1:]
    first.save(out, save_all=True, append_images=rest, resolution=72.0, quality=92)
