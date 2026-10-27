"""Fetch slide media from Picdrop / Frame.io / direct links, and handle dropped files."""
import hashlib
import html
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ffmpeg_utils import NO_WINDOW, find_ffmpeg, grab_frame, probe_video  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


def _slug(url):
    return hashlib.sha1(url.encode()).hexdigest()[:10]


def _download(url, dest):
    with requests.get(url, headers=UA, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return dest


def kind_of(path):
    ext = Path(path).suffix.lower()
    return "video" if ext in VIDEO_EXTS else "image" if ext in IMAGE_EXTS else None


def make_poster(video, media_dir):
    ffmpeg, _ = find_ffmpeg()
    if not ffmpeg:
        return None
    out = Path(media_dir) / (Path(video).stem + "_poster.jpg")
    try:
        grab_frame(ffmpeg, video, out, 0.0)
        return out.name
    except Exception:
        return None


# ── browser preview copies of videos ────────────────────────────────────────
_preview_jobs = set()
_preview_lock = threading.Lock()


def preview_path(video, media_dir):
    return Path(media_dir) / ".preview" / (Path(video).stem + ".mp4")


def preview_info(video, media_dir):
    """{name, w, h} of a finished preview copy, else None (and start making one)."""
    out = preview_path(video, media_dir)
    meta = out.with_suffix(".json")
    if out.exists() and meta.exists():
        import json
        return {"name": f".preview/{out.name}", **json.loads(meta.read_text())}
    ensure_preview(video, media_dir)
    return None


def ensure_preview(video, media_dir):
    """Make a small H.264 copy in the background so any source (HEVC .mov, 4K…) plays in
    the browser. Rotation is applied, so its size is the displayed size."""
    src = Path(media_dir) / Path(video).name
    out = preview_path(video, media_dir)
    with _preview_lock:
        if str(out) in _preview_jobs or out.exists() or not src.exists():
            return
        _preview_jobs.add(str(out))

    def job():
        import json
        try:
            ffmpeg, _ = find_ffmpeg()
            out.parent.mkdir(exist_ok=True)
            tmp = out.with_name(out.stem + ".part.mp4")
            vf = "scale='if(gt(iw,ih),1280,-2)':'if(gt(iw,ih),-2,1280)'"
            r = subprocess.run([ffmpeg, "-y", "-i", str(src), "-vf", vf, "-c:v", "libx264",
                                "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
                                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)],
                               capture_output=True, creationflags=NO_WINDOW)
            if r.returncode == 0:
                w, h, dur = probe_video(ffmpeg, tmp)
                tmp.replace(out)
                out.with_suffix(".json").write_text(json.dumps({"w": w, "h": h, "duration": dur}))
        finally:
            with _preview_lock:
                _preview_jobs.discard(str(out))

    threading.Thread(target=job, daemon=True).start()


# ── Picdrop ─────────────────────────────────────────────────────────────────
def _picdrop(url, media_dir):
    u = urlparse(url)
    parts = [p for p in u.path.split("/") if p]
    key = parse_qs(u.query).get("file", [None])[0]
    if len(parts) < 2 or not key:
        raise ValueError("Picdrop link has no ?file= id – link a single image, not the gallery")
    api = f"https://www.picdrop.com/api/content/{parts[0]}:{parts[1]}/files/{key}"
    r = requests.get(api, headers={**UA, "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    info = r.json()
    thumbs = sorted(info.get("thumbnails", []), key=lambda t: t["width"] * t["height"], reverse=True)
    if not thumbs:
        raise ValueError("Picdrop returned no downloadable preview")
    name = Path(info.get("name") or key).stem
    dest = Path(media_dir) / f"{name}_{_slug(url)}.jpg"
    if not dest.exists():
        _download(thumbs[0]["url"], dest)
    kind = "video" if info.get("type") == "video" else "image"
    return {"kind": kind, "status": "ok", "file": dest.name, "poster": None,
            "expected_name": info.get("name")}


# ── Frame.io ────────────────────────────────────────────────────────────────
def _frameio(url, media_dir):
    """Frame.io only exposes a poster image + file name without a browser session.
    The poster lets us lay out the slide; the real video has to be dropped in."""
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    t = r.text
    title = re.search(r"<title>([^<]+)</title>", t)
    name = html.unescape(title.group(1)).split(" - ")[0].strip() if title else None
    poster = None
    og = re.search(r'property="og:image" content="([^"]+)"', t)
    if og:
        dest = Path(media_dir) / f"frameio_{_slug(url)}_poster.webp"
        if not dest.exists():
            _download(html.unescape(og.group(1)), dest)
        poster = dest.name
    return {"kind": "video", "status": "needs_file", "file": None, "poster": poster,
            "expected_name": name,
            "error": "Open on Frame.io and click Download – it is picked up from Downloads"}


def fetch(url, media_dir, kind_hint=None):
    Path(media_dir).mkdir(parents=True, exist_ok=True)
    base = {"url": url, "kind": kind_hint, "status": "error", "file": None,
            "poster": None, "expected_name": None, "error": None}
    try:
        host = urlparse(url).netloc.lower()
        if "picdrop" in host:
            res = _picdrop(url, media_dir)
        elif "frame.io" in host or host == "f.io":
            res = _frameio(url, media_dir)
        else:
            ext = Path(urlparse(url).path).suffix.lower()
            if ext not in IMAGE_EXTS | VIDEO_EXTS:
                raise ValueError("Unsupported link – drop the file instead")
            dest = Path(media_dir) / f"{_slug(url)}{ext}"
            if not dest.exists():
                _download(url, dest)
            res = {"kind": kind_of(dest), "status": "ok", "file": dest.name}
            if res["kind"] == "video":
                res["poster"] = make_poster(dest, media_dir)
        base.update(res)
    except Exception as e:
        base["error"] = str(e)
        base["status"] = "needs_file"
    return base


def _norm(name):
    stem = re.sub(r"\s*\(\d+\)$", "", Path(name or "").stem)     # "clip (1).mp4" from repeat downloads
    return re.sub(r"[^a-z0-9]", "", stem.lower())


def downloads_dir():
    """The user's Downloads folder (honours a moved folder on Windows)."""
    env = os.environ.get("CAROUSEL_DOWNLOADS")
    if env:
        return Path(env)
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as k:
                return Path(winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")[0])
        except OSError:
            pass
    return Path.home() / "Downloads"


_sizes = {}


def find_download(expected_name, folder):
    """A finished download in `folder` whose name matches `expected_name`, else None.
    A file only counts once its size has stopped changing between two polls."""
    want = _norm(expected_name)
    if not want or not folder.is_dir():
        return None
    for f in folder.iterdir():
        if not f.is_file() or f.suffix.lower() not in VIDEO_EXTS | IMAGE_EXTS or _norm(f.name) != want:
            continue
        size = f.stat().st_size
        stable = _sizes.get(f) == size and size > 0
        _sizes[f] = size
        if stable:
            return f
    return None


def match_upload(filename, slides):
    """Find the slide a dropped file belongs to. Returns (slide_idx, media_idx) or None."""
    n = _norm(filename)
    for si, s in enumerate(slides):
        for mi, m in enumerate(s["media"]):
            if m.get("expected_name") and _norm(m["expected_name"]) == n:
                return si, mi
    kind = kind_of(filename)
    m = re.search(r"(?:slide|folie)\s*_?-?\s*(\d+)", filename, re.I)
    if m:
        num = int(m.group(1))
        for si, s in enumerate(slides):
            if s["index"] == num:
                return si, max(0, s.get("media_sel", 0))
    # first slide still waiting for this kind of file
    for si, s in enumerate(slides):
        for mi, md in enumerate(s["media"]):
            if md["status"] != "ok" and (md.get("kind") or s.get("media_type")) == kind:
                return si, mi
    return None
