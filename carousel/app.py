"""Carousel Builder – local web app.

    python carousel/app.py            # opens http://127.0.0.1:5173 in the browser
"""
import hashlib
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, abort

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import brief_parser  # noqa: E402
import layout as L  # noqa: E402
import media as M  # noqa: E402
import project as P  # noqa: E402
import render as R  # noqa: E402
from ffmpeg_utils import find_ffmpeg  # noqa: E402

PORT = int(os.environ.get("CAROUSEL_PORT", 5173))
app = Flask(__name__, static_folder=str(HERE / "static"), static_url_path="/static")
app.json.ensure_ascii = False

_locks = {}
_exports = {}


def lock(name):
    return _locks.setdefault(name, threading.RLock())


def err(msg, code=400):
    return jsonify({"error": msg}), code


# ── pages / static ──────────────────────────────────────────────────────────
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/files/<name>/<path:sub>")
def files(name, sub):
    d = P.project_dir(name)
    if not (d / "project.json").exists():
        abort(404)
    return send_from_directory(d, sub, max_age=0)


@app.get("/api/status")
def status():
    ffmpeg, ffprobe = find_ffmpeg()
    try:
        import anthropic  # noqa: F401
        claude = True
    except ImportError:
        claude = False
    fonts = {}
    for k in ("neutra", "trajan"):
        try:
            fonts[k] = L.font_path(k)
        except FileNotFoundError as e:
            fonts[k] = None
    return jsonify({"ffmpeg": ffmpeg, "claude": claude, "fonts": fonts,
                    "presets": {k: v["label"] for k, v in R.PRESETS.items()}})


# ── projects ────────────────────────────────────────────────────────────────
@app.get("/api/projects")
def projects():
    return jsonify(P.list_projects())


@app.post("/api/projects")
def create_project():
    data = request.get_json(force=True)
    brief = data.get("brief", "")
    try:
        if data.get("mode") == "claude":
            import claude_parser
            parsed = claude_parser.parse_brief(brief)
        else:
            parsed = brief_parser.parse_brief(brief)
    except Exception as e:
        traceback.print_exc()
        return err(f"Parsing failed: {e}")
    if not parsed:
        return err("No slides found. Each slide needs a line like 'Slide 1:'.")
    proj = P.create(data.get("name") or "", brief, parsed)
    _active.add(proj["name"])
    return jsonify(proj)


@app.get("/api/p/<name>")
def get_project(name):
    try:
        proj = P.load(name)
        _active.add(name)
        return jsonify(proj)
    except FileNotFoundError:
        return err("Project not found", 404)


# ── slides ──────────────────────────────────────────────────────────────────
TEXT_FIELDS = {"body", "n_lines", "body_size", "lines_auto"}


@app.post("/api/p/<name>/slide/<int:i>")
def patch_slide(name, i):
    data = request.get_json(force=True)
    with lock(name):
        proj = P.load(name)
        s = proj["slides"][i]
        mdir = P.media_dir(name)
        replace = False
        if "headline" in data and isinstance(data["headline"], str):
            data["headline"] = [l for l in data["headline"].splitlines() if l.strip()]
        if "lines" in data:
            s["lines"] = [l.rstrip() for l in (data.pop("lines") or "").upper().splitlines()]
            s["lines_auto"] = False
        if data.get("preset") and data["preset"] != s["preset"]:
            s["body_size"] = R.PRESETS[data["preset"]].get("body_size")
            s["lines_auto"] = True
        if "zone" in data:
            s["zone_auto"] = False
            s["offsets"].pop("body", None)
        if "offsets" in data:
            moved = data.pop("offsets")
            s["offsets"].update(moved)
            if "body" in moved:
                s["zone_auto"] = False      # a hand-placed block stays where it was dropped
        if "media_urls" in data:
            urls = data.pop("media_urls")
            old = {m["url"]: m for m in s["media"] if m.get("url")}
            local = [m for m in s["media"] if not m.get("url")]
            s["media"] = [old.get(u) or {"url": u, "kind": s["media_type"], "status": "pending",
                                         "file": None, "poster": None, "expected_name": None,
                                         "error": None} for u in urls] + local
            s["media_sel"] = min(s["media_sel"], max(0, len(s["media"]) - 1))
        if data.pop("autoplace", False):
            s["zone_auto"] = True
            replace = True
        if "shade" in data:
            s["shade"] = {**R.shade_settings(s), **data.pop("shade")}
        for k in ("preset", "headline", "subline", "kicker", "body", "lines_auto", "n_lines",
                  "body_size", "zone", "crop", "media_sel", "media_type"):
            if k in data:
                s[k] = data[k]
        if TEXT_FIELDS & data.keys() or "preset" in data:
            P.rewrap(s)
            replace = replace or s["zone_auto"]
        if "media_sel" in data and s["zone_auto"]:
            replace = True              # crop changes keep the text where it is
        if replace:
            P.autoplace(s, mdir)
        else:
            P.update_legibility(s, mdir)
        P.save(proj)
    return jsonify(view_of(name, proj, i))


@app.post("/api/p/<name>/slide/<int:i>/fetch")
def fetch_media(name, i):
    with lock(name):
        proj = P.load(name)
        s = proj["slides"][i]
        todo = [(k, m) for k, m in enumerate(s["media"]) if m.get("url") and m["status"] != "ok"]
    results = {k: M.fetch(m["url"], P.media_dir(name), s["media_type"]) for k, m in todo}
    with lock(name):
        proj = P.load(name)
        s = proj["slides"][i]
        for k, res in results.items():
            m = s["media"][k]
            if m["status"] == "ok":       # a file was dropped meanwhile
                continue
            m.update({kk: v for kk, v in res.items() if v is not None or kk == "error"})
        if s["zone_auto"]:
            P.autoplace(s, P.media_dir(name))
        P.save(proj)
    return jsonify(view_of(name, proj, i))


@app.post("/api/p/<name>/upload")
def upload(name):
    target = request.form.get("slide", type=int)
    target_media = request.form.get("media", type=int)
    changed, unmatched = set(), []
    with lock(name):
        proj = P.load(name)
        mdir = P.media_dir(name)
        mdir.mkdir(parents=True, exist_ok=True)
        for f in request.files.getlist("files"):
            fname = Path(f.filename).name
            kind = M.kind_of(fname)
            if not kind:
                unmatched.append(fname)
                continue
            dest = mdir / fname
            f.save(dest)
            if target is not None:
                si, mi = target, target_media
            else:
                hit = M.match_upload(fname, proj["slides"])
                if not hit:
                    unmatched.append(fname)
                    continue
                si, mi = hit
            assign_file(proj["slides"][si], mi, dest, mdir)
            changed.add(si)
        for si in changed:
            s = proj["slides"][si]
            if s["zone_auto"]:
                P.autoplace(s, mdir)
        P.save(proj)
    return jsonify({"changed": sorted(changed), "unmatched": unmatched, "project": proj})


def assign_file(s, mi, path, mdir):
    """Put a file that already sits in the media folder into slide s at media slot mi."""
    kind = M.kind_of(path.name)
    entry = {"kind": kind, "status": "ok", "file": path.name, "error": None,
             "poster": M.make_poster(path, mdir) if kind == "video" else None}
    if mi is None or mi >= len(s["media"]):
        s["media"].append({"url": None, "expected_name": None, **entry})
        mi = len(s["media"]) - 1
    else:
        s["media"][mi].update(entry)
    s["media_sel"] = mi
    s["media_type"] = s["media_type"] or kind


# ── Frame.io: open links in the browser, pick downloads up automatically ────
_active = set()      # projects opened in this session; only these are watched


def _waiting(proj):
    return [(si, mi, m) for si, s in enumerate(proj["slides"]) for mi, m in enumerate(s["media"])
            if m["status"] != "ok" and m.get("expected_name")]


def _watch_downloads():
    import shutil
    while True:
        time.sleep(2)
        folder = M.downloads_dir()
        for name in list(_active):
            try:
                with lock(name):
                    proj = P.load(name)
                    hits = [(si, mi, M.find_download(m["expected_name"], folder))
                            for si, mi, m in _waiting(proj)]
                    hits = [h for h in hits if h[2]]
                    if not hits:
                        continue
                    mdir = P.media_dir(name)
                    for si, mi, src in hits:
                        dest = mdir / src.name
                        shutil.copy2(src, dest)
                        assign_file(proj["slides"][si], mi, dest, mdir)
                        if proj["slides"][si]["zone_auto"]:
                            P.autoplace(proj["slides"][si], mdir)
                        print(f"[downloads] {src.name} → slide {proj['slides'][si]['index']}")
                    P.save(proj)
            except Exception:
                traceback.print_exc()


@app.post("/api/p/<name>/open_links")
def open_links(name):
    """Open every share link whose file is still missing in the default browser."""
    proj = P.load(name)
    urls = list(dict.fromkeys(m["url"] for _, _, m in _waiting(proj) if m.get("url")))
    for u in urls:
        webbrowser.open_new_tab(u)
    _active.add(name)
    return jsonify({"opened": len(urls), "downloads": str(M.downloads_dir())})


@app.get("/api/p/<name>/media_state")
def media_state(name):
    proj = P.load(name)
    return jsonify({"state": [[m["status"], m.get("file")] for s in proj["slides"] for m in s["media"]],
                    "waiting": len(_waiting(proj))})


# ── views (rendered layers for the editor) ──────────────────────────────────
def _h(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:10]


def view_of(name, proj, i):
    s = proj["slides"][i]
    d = P.project_dir(name)
    cache = d / "cache"
    cache.mkdir(exist_ok=True)
    keep = set()
    mdir = P.media_dir(name)

    still = R.source_still(s, mdir)
    src = None
    if still:
        fn, w, h = R.proxy(still, cache)
        src = {"url": f"/files/{name}/cache/{fn}", "w": w, "h": h}

    els = []
    for e in R.elements(s):
        base = {k: v for k, v in s.items()
                if k not in ("media", "legibility", "media_sel", "crop", "shade")}
        key = _h(base, e["id"], e["x"], e["y"])
        fn = f"s{i}_{e['id']}_{key}.png"
        if not (cache / fn).exists():
            e["img"].save(cache / fn)
        keep.add(fn)
        els.append({"id": e["id"], "url": f"/files/{name}/cache/{fn}", "x": e["x"], "y": e["y"],
                    "w": e["img"].width, "h": e["img"].height})
    for f in cache.glob(f"s{i}_*"):
        if f.name not in keep:
            try:
                f.unlink()
            except OSError:
                pass
    s["shade"] = R.shade_settings(s)
    return {"slide": s, "index": i, "src": src, "elements": els}


@app.get("/api/p/<name>/slide/<int:i>/view")
def view(name, i):
    with lock(name):
        proj = P.load(name)
        return jsonify(view_of(name, proj, i))


@app.get("/api/p/<name>/slide/<int:i>/thumb.jpg")
def thumb(name, i):
    proj = P.load(name)
    im = R.compose(proj["slides"][i], P.media_dir(name))
    im.thumbnail((270, 360))
    d = P.project_dir(name) / "cache"
    d.mkdir(exist_ok=True)
    im.save(d / f"thumb{i}.jpg", quality=80)
    return send_from_directory(d, f"thumb{i}.jpg", max_age=0)


# ── export ──────────────────────────────────────────────────────────────────
def _export(name):
    st = _exports[name]
    try:
        proj = P.load(name)
        out = P.project_dir(name) / "export"
        stills = []
        for s in proj["slides"]:
            st["log"].append(f"Slide {s['index']}…")
            still, written = R.export_slide(s, P.media_dir(name), out, log=st["log"].append)
            stills.append(still)
            st["files"] += written
        R.export_pdf(stills, out / "preview.pdf")
        st["files"].append("preview.pdf")
        st["log"].append(f"Done → {out}")
    except Exception as e:
        traceback.print_exc()
        st["log"].append(f"ERROR: {e}")
        st["error"] = str(e)
    finally:
        st["running"] = False


@app.post("/api/p/<name>/export")
def export(name):
    st = _exports.get(name)
    if st and st["running"]:
        return err("Export already running")
    _exports[name] = {"running": True, "log": [], "files": [], "error": None, "started": time.time()}
    threading.Thread(target=_export, args=(name,), daemon=True).start()
    return jsonify(_exports[name])


@app.get("/api/p/<name>/export")
def export_status(name):
    return jsonify(_exports.get(name) or {"running": False, "log": [], "files": []})


@app.post("/api/p/<name>/open_export")
def open_export(name):
    out = P.project_dir(name) / "export"
    out.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(out)  # noqa: S606
    else:
        import subprocess
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(out)])
    return jsonify({"ok": True})


if __name__ == "__main__":
    threading.Thread(target=_watch_downloads, daemon=True).start()
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
