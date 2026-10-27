"""Text measurement, balanced line wrapping and readability-based placement."""
import functools
import json
import math
import os
import re
from pathlib import Path

import hashlib
import subprocess
import sys
import tempfile

import numpy as np
from PIL import ImageFont

HERE = Path(__file__).parent
CFG = json.loads((HERE / "presets.json").read_text(encoding="utf-8"))
W, H = CFG["canvas"]["w"], CFG["canvas"]["h"]
BODY = CFG["body"]
ZONES_V = ("top", "middle", "bottom")
ZONES_H = ("left", "center", "right")

PUNCT_END = re.compile(r"[.,:;!?–—-]$")
LIST_SIZE = 32          # list-style text is set slightly smaller (livetasting slide 5)
LIST_ITEM_WIDTH = 520
MIN_AVG_LINE = 450      # never balance into lines shorter than this on average


# ── fonts ───────────────────────────────────────────────────────────────────
def _font_dirs():
    dirs = [HERE / "assets" / "fonts"]
    if os.name == "nt":
        dirs.append(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    else:
        dirs += [Path.home() / "Library/Fonts", Path("/Library/Fonts"),
                 Path.home() / ".fonts", Path("/usr/share/fonts")]
    return dirs


@functools.lru_cache(maxsize=None)
def font_path(key):
    for name in CFG["fonts"][key]:
        for d in _font_dirs():
            if (d / name).is_file():
                return str(d / name)
    raise FileNotFoundError(f"Font '{key}' not found. Put one of {CFG['fonts'][key]} "
                            f"into carousel/assets/fonts/.")


@functools.lru_cache(maxsize=64)
def get_font(key, size):
    return ImageFont.truetype(font_path(key), round(size * 4) / 4)


def measure(text, font_key, size, tracking):
    """Advance width in px; tracking is in em (Illustrator tracking / 1000)."""
    if not text:
        return 0.0
    return get_font(font_key, size).getlength(text) + tracking * size * (len(text) - 1)


def upper(text):
    return text.upper().replace("ẞ", "SS")


# ── wrapping ────────────────────────────────────────────────────────────────
def _balanced(words, widths, space, n, max_width):
    """Split words into exactly n lines minimising squared deviation from the mean width."""
    k = len(words)
    pre = [0.0]
    for w in widths:
        pre.append(pre[-1] + w)

    def line_w(i, j):  # words[i:j]
        return pre[j] - pre[i] + space * (j - i - 1)

    target = line_w(0, k) / n
    INF = float("inf")
    cost = [[INF] * (k + 1) for _ in range(n + 1)]
    back = [[0] * (k + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for l in range(1, n + 1):
        for j in range(l, k + 1):
            for i in range(l - 1, j):
                if cost[l - 1][i] == INF:
                    continue
                w = line_w(i, j)
                over = max(0.0, w - max_width)
                c = cost[l - 1][i] + (w - target) ** 2 + over * over * 1000
                if j < k and PUNCT_END.search(words[j - 1]):
                    c -= 0.3 * target * target      # designers break after punctuation
                if any(PUNCT_END.search(x) and x[-1] in ".!?" for x in words[i:j - 1]):
                    c += 0.2 * target * target      # avoid a sentence ending mid-line
                if c < cost[l][j]:
                    cost[l][j], back[l][j] = c, i
    lines, j = [], k
    for l in range(n, 0, -1):
        i = back[l][j]
        lines.append(" ".join(words[i:j]))
        j = i
    return lines[::-1], cost[n][k]


def wrap_balanced(text, font_key, size, tracking, max_width, min_lines=1, max_lines=6, n=None):
    words = text.split()
    if not words:
        return []
    widths = [measure(w, font_key, size, tracking) for w in words]
    space = measure("A A", font_key, size, tracking) - 2 * measure("A", font_key, size, tracking)
    total = sum(widths) + space * (len(words) - 1)
    if n is None:
        need = max(1, math.ceil(total / max_width))
        n = max(need, min(min_lines, max(1, int(total // MIN_AVG_LINE))))
        n = max(need, min(n, max_lines))
    n = max(1, min(n, len(words)))
    return _balanced(words, widths, space, n, max_width)[0]


def is_list(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 3:
        return False
    short = sum(1 for l in lines if len(l) <= 45 and not re.search(r"[.!?]$", l))
    return short >= len(lines) - 1


def wrap_body(text, size=None, n_lines=None):
    """Return (lines, size). '' entries are blank spacer lines."""
    text = upper(text.strip())
    if not text:
        return [], size or BODY["size"]
    tr = BODY["tracking"]
    if is_list(text):
        size = size or LIST_SIZE
        items = [l.strip() for l in text.splitlines() if l.strip()]
        out = []
        for i, item in enumerate(items):
            if i > 0 and not items[i - 1].endswith(":"):
                out.append("")
            out += wrap_balanced(item, "neutra", size, tr, LIST_ITEM_WIDTH)
        return out, size
    size = size or BODY["size"]
    out = []
    for p, para in enumerate(re.split(r"\n\s*\n", text)):
        if p:
            out.append("")
        out += wrap_balanced(" ".join(para.split()), "neutra", size, tr, BODY["max_width"],
                             BODY["min_lines"], BODY["max_lines"], n_lines)
    return out, size


def wrap_subline(text, spec):
    """ON TOUR sub line: the part before the first '|' is balanced, the rest is one line."""
    text = upper(text.strip())
    if not text:
        return []
    parts = [p.strip() for p in text.split("|")]
    head, tail = parts[0], " | ".join(parts[1:])
    lines = wrap_balanced(head, spec["font"], spec["size"], spec["tracking"], spec["max_width"])
    return lines + ([tail] if tail else [])


# ── body block geometry ─────────────────────────────────────────────────────
def block_metrics(lines, size):
    lead = size * BODY["leading"]
    font = get_font("neutra", size)
    asc = font.getbbox("H")[1]                # negative-ish: offset from top to cap
    cap = -font.getbbox("H", anchor="ls")[1]  # cap height above baseline
    widths = [measure(l, "neutra", size, BODY["tracking"]) for l in lines]
    return {"lead": lead, "cap": cap, "width": max(widths) if widths else 0,
            "height": cap + lead * (len(lines) - 1) + size * 0.12, "widths": widths, "_asc": asc}


def zone_anchor(zone, lines, size):
    """Return (align, anchor_x, first_baseline) for a zone like 'bottom-center'."""
    v, h = zone.split("-")
    m = block_metrics(lines, size)
    z = BODY["zones"]
    if v == "top":
        y0 = z["top"]["first_baseline"]
    elif v == "bottom":
        y0 = z["bottom"]["last_baseline"] - m["lead"] * (len(lines) - 1)
    else:
        y0 = z["middle"]["center"] - m["height"] / 2 + m["cap"]
    mx = BODY["margin_x"]
    ax = {"left": mx, "center": W / 2, "right": W - mx}[h]
    return h, ax, y0


def block_rect(zone, lines, size):
    align, ax, y0 = zone_anchor(zone, lines, size)
    m = block_metrics(lines, size)
    x = {"left": ax, "center": ax - m["width"] / 2, "right": ax - m["width"]}[align]
    return int(x), int(y0 - m["cap"]), int(m["width"]), int(m["height"])


# ── readability scoring ─────────────────────────────────────────────────────
_FACE_CACHE = {}


def detect_faces(small):
    """Detect faces on a grayscale PIL image via faces_worker.py (see its docstring).
    Returns [x, y, w, h] rects in that image's pixels."""
    key = hashlib.sha1(small.tobytes()).hexdigest()
    if key in _FACE_CACHE:
        return _FACE_CACHE[key]
    faces = []
    try:
        with tempfile.TemporaryDirectory() as td:
            png = Path(td) / "bg.png"
            small.save(png)
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            r = subprocess.run([sys.executable, str(HERE / "faces_worker.py"), str(png)],
                               capture_output=True, text=True, timeout=30, creationflags=flags)
            faces = json.loads(r.stdout) if r.returncode == 0 else []
    except Exception:
        faces = []
    _FACE_CACHE[key] = faces
    return faces


def _edges(gray):
    """Cheap edge map: thresholded gradient magnitude of a blurred image.
    Threshold is per canvas px, so it is scaled with the map size."""
    k = np.array([1, 4, 6, 4, 1], np.float32) / 16
    p = np.pad(gray, 2, mode="edge")
    g = sum(k[i] * p[:, i:i + gray.shape[1]] for i in range(5))
    g = sum(k[i] * g[i:i + gray.shape[0], :] for i in range(5))
    gy, gx = np.gradient(g)
    return (np.hypot(gx, gy) > 0.045 * W / gray.shape[1]).astype(np.float32)


def analyse(bg_rgb, faces=()):
    """Precompute maps for scoring. bg_rgb: the cropped background at any size with the
    canvas aspect (half size is plenty); faces: rects in canvas px."""
    gray = np.asarray(bg_rgb.convert("L"), dtype=np.float32) / 255.0
    k = gray.shape[1] / W
    face_mask = np.zeros(gray.shape, dtype=np.uint8)
    for (x, y, w, h) in faces:
        pad = w * 0.35
        x0, y0 = int(max(0, (x - pad) * k)), int(max(0, (y - pad) * k))
        x1, y1 = int(max(0, (x + w + pad) * k)), int(max(0, (y + h + pad) * k))
        face_mask[y0:y1, x0:x1] = 1
    return {"gray": gray, "edges": _edges(gray), "faces": face_mask, "k": k}


def score_rect(maps, rect, pad=28):
    k = maps.get("k", 1.0)
    x, y, w, h = rect
    x0, y0 = int(max(0, x - pad) * k), int(max(0, y - pad) * k)
    x1, y1 = int(min(W, x + w + pad) * k), int(min(H, y + h + pad) * k)
    if x1 <= x0 or y1 <= y0:
        return 9.0, 1.0
    g = maps["gray"][y0:y1, x0:x1]
    lum = float(g.mean())
    std = float(g.std())
    edge = float(maps["edges"][y0:y1, x0:x1].mean())
    face = float(maps["faces"][y0:y1, x0:x1].mean())
    bright = max(0.0, lum - 0.35) * 2.2
    score = edge * 6.0 + std * 2.0 + bright + face * 5.0
    return score, lum


def auto_zone(maps, lines, size, prefer=None):
    """Pick the zone where white text reads best. maps: from analyse(). Returns (zone, info)."""
    if maps is None or not lines:
        return prefer or "bottom-center", {"score": None, "low": False}
    best = None
    for v in ZONES_V:
        for h in ZONES_H:
            zone = f"{v}-{h}"
            s, lum = score_rect(maps, block_rect(zone, lines, size))
            if v == "middle":
                s += 0.08          # designers rarely use the middle band
            if zone == prefer:
                s -= 0.05
            if best is None or s < best[1]:
                best = (zone, s, lum)
    zone, s, lum = best
    return zone, {"score": round(s, 3), "lum": round(lum, 3), "low": s > 1.1 or lum > 0.62}


def legibility(maps, rect):
    if maps is None:
        return {"score": None, "low": False}
    s, lum = score_rect(maps, rect)
    return {"score": round(s, 3), "lum": round(lum, 3), "low": s > 1.1 or lum > 0.62}
