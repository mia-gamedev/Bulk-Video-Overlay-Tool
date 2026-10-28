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
import dialogs  # noqa: E402
import export_ai  # noqa: E402
import layout as L  # noqa: E402
import media as M  # noqa: E402
import preset_import  # noqa: E402
import project as P  # noqa: E402
import settings  # noqa: E402
import render as R  # noqa: E402
from ffmpeg_utils import find_ffmpeg  # noqa: E402

PORT = int(os.environ.get("CAROUSEL_PORT", 5173))
preset_import.load_user_presets()
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


# ── one server at a time, and it knows when its code is out of date ─────────
def _code_stamp():
    """Hash of the server's own Python files – changes when the tool is updated."""
    h = hashlib.sha1()
    for f in sorted(HERE.glob("*.py")) + [HERE.parent / "ffmpeg_utils.py"]:
        try:
            h.update(f.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


STARTED_STAMP = _code_stamp()


@app.post("/api/shutdown")
def shutdown():
    """Used by a newly started server to replace this one (local requests only)."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        abort(403)
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return jsonify({"ok": True})


def _port_busy(port):
    import socket
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def take_over_port(port):
    """Stop older Carousel Builder servers still listening on the port (they would keep
    answering with old code). Returns False if something else holds the port."""
    if not _port_busy(port):
        return True
    import requests
    try:                                            # newer versions: ask nicely
        requests.post(f"http://127.0.0.1:{port}/api/shutdown", timeout=2)
    except Exception:
        pass
    for _ in range(10):
        if not _port_busy(port):
            return True
        time.sleep(0.2)
    try:                                            # older versions: end those processes
        import psutil
    except ImportError:
        return False
    for c in psutil.net_connections(kind="tcp"):
        if c.status != psutil.CONN_LISTEN or not c.laddr or c.laddr.port != port or not c.pid:
            continue
        if c.pid == os.getpid():
            continue
        try:
            proc = psutil.Process(c.pid)
            cmd = " ".join(proc.cmdline()).replace("\\", "/").lower()
            if "carousel/app.py" in cmd:            # only ever our own app
                print(f"Stopping an older Carousel Builder (pid {c.pid})…", flush=True)
                proc.terminate()
                proc.wait(timeout=5)
        except (psutil.Error, OSError):
            pass
    time.sleep(0.3)
    return not _port_busy(port)


@app.get("/api/status")
def status():
    ffmpeg, ffprobe = find_ffmpeg()
    fonts = {}
    for k in ("neutra", "trajan"):
        try:
            fonts[k] = L.font_path(k)
        except FileNotFoundError as e:
            fonts[k] = None
    return jsonify({"ffmpeg": ffmpeg, "ai": settings.all_status(), "fonts": fonts,
                    "presets": preset_list(), "outdated": _code_stamp() != STARTED_STAMP})


# ── app settings (⚙ menu): AI API keys ──────────────────────────────────────
@app.get("/api/settings")
def get_settings():
    return jsonify({"ai": settings.all_status()})


def _check_key(provider, key):
    """None if fine, ("error", msg) to refuse, ("warning", msg) to save anyway."""
    label = settings.PROVIDERS[provider]["label"]
    try:
        import anthropic
        import ai_parser
    except ImportError:
        return "error", "The anthropic package is missing – run: pip install anthropic"
    try:
        ai_parser.check_key(provider, key)
    except anthropic.AuthenticationError:
        return "error", f"The {label} rejected this key – check that it was copied completely"
    except anthropic.PermissionDeniedError:
        return "error", f"This key has no access to the {label}"
    except (anthropic.APIConnectionError, anthropic.APITimeoutError):
        return "warning", f"Saved, but couldn't reach the {label} to check it (offline?)"
    except anthropic.APIStatusError as e:
        return "warning", f"Saved, but the check returned {e.status_code}"
    return None


@app.post("/api/settings/provider")
def set_ai_provider():
    """Toggle which API "Parse with AI" talks to."""
    name = (request.get_json(silent=True) or {}).get("provider")
    if name not in settings.PROVIDERS:
        return err("Unknown API")
    settings.set_provider(name)
    return jsonify({"ai": settings.all_status()})


@app.post("/api/settings/key")
def set_api_key():
    """Check a new key with its provider, then save it locally. Empty key = remove."""
    data = request.get_json(silent=True) or {}
    provider = data.get("provider")
    if provider not in settings.PROVIDERS:
        return err("Unknown provider")
    key = (data.get("key") or "").strip()
    warning = None
    if key:
        result = _check_key(provider, key)
        if result and result[0] == "error":
            return err(result[1])
        warning = result[1] if result else None
    settings.set_key(provider, key)
    return jsonify({"ai": settings.all_status(), "warning": warning})


def preset_list():
    return {k: {"label": v["label"], "template": v.get("type") == "template",
                "slots": [{"id": sid, "role": v["slots"][sid]["role"], "sample": v["slots"][sid]["sample"]}
                          for sid in v.get("order", [])]}
            for k, v in R.PRESETS.items()}


@app.post("/api/presets/import")
def import_preset():
    """Upload a title-slide .ai/.pdf and turn one artboard into a preset."""
    f = request.files.get("file")
    name = (request.form.get("name") or "").strip()
    if not f or not name:
        return err("Choose a file and give the preset a name")
    tmp = preset_import.USER_DIR / "_upload"
    tmp.parent.mkdir(exist_ok=True)
    f.save(tmp)
    try:
        pages = preset_import.page_count(tmp)
        page = request.form.get("page", type=int)
        if page is None and pages > 1:
            return jsonify({"pages": pages})          # the page asks which artboard to use
        key, preset, warnings = preset_import.import_preset(tmp, name, page or 1)
    except ValueError as e:
        return err(str(e))
    finally:
        tmp.unlink(missing_ok=True)
    return jsonify({"key": key, "label": preset["label"], "warnings": warnings,
                    "slots": {sid: {k: sp[k] for k in ("role", "font", "size", "align", "sample")}
                              for sid, sp in preset["slots"].items()},
                    "graphics": bool(preset.get("graphics")), "presets": preset_list()})


@app.get("/api/presets/<key>/thumb.jpg")
def preset_thumb(key):
    return send_from_directory(preset_import.USER_DIR, f"{key}_thumb.jpg", max_age=0)


# ── projects ────────────────────────────────────────────────────────────────
@app.get("/api/projects")
def projects():
    return jsonify(P.list_projects())


@app.post("/api/projects")
def create_project():
    data = request.get_json(force=True)
    brief = data.get("brief", "")
    try:
        if data.get("mode") == "ai":
            import ai_parser
            parsed = ai_parser.parse_brief(brief)
        else:
            parsed = brief_parser.parse_brief(brief)
    except Exception as e:
        traceback.print_exc()
        msg = str(e)
        if type(e).__name__ == "AuthenticationError" or "api_key" in msg.lower():
            label = settings.PROVIDERS[settings.provider()]["label"]
            msg = f"no valid {label} key – add one in the ⚙ menu"
        return err(f"Parsing failed: {msg}")
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
            s["preset"] = data["preset"]
            P.fill_slots(s)
        if "slots" in data:
            s.setdefault("slots", {}).update(data.pop("slots"))
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
        if "logo_shade" in data:
            ls = {**R.logo_shade_settings(s), **data.pop("logo_shade")}
            ls.pop("shape", None)
            s["logo_shade"] = ls
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
    if kind == "video":
        M.ensure_preview(path, mdir)


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
                    P.save(proj)
                for si, _, src in hits:
                    print(f"[downloads] {src.name} -> slide {proj['slides'][si]['index']}", flush=True)
            except Exception:
                traceback.print_exc()


@app.post("/api/p/<name>/open_links")
def open_links(name):
    """Start watching Downloads for this project. The page opens the share links itself;
    any it couldn't (popup blocker) are sent here and opened in the default browser."""
    proj = P.load(name)
    waiting = list(dict.fromkeys(m["url"] for _, _, m in _waiting(proj) if m.get("url")))
    blocked = (request.get_json(silent=True) or {}).get("open", [])
    for u in blocked:
        if u in waiting:                       # only links that belong to this project
            webbrowser.open_new_tab(u)
    _active.add(name)
    return jsonify({"waiting": len(waiting), "downloads": str(M.downloads_dir())})


@app.get("/api/p/<name>/media_state")
def media_state(name):
    proj = P.load(name)
    mdir = P.media_dir(name)

    def ready(m):
        return bool(m.get("file") and m.get("kind") == "video"
                    and M.preview_path(m["file"], mdir).with_suffix(".json").exists())
    return jsonify({"state": [[m["status"], m.get("file"), ready(m)]
                              for s in proj["slides"] for m in s["media"]],
                    "waiting": len(_waiting(proj)), "outdated": _code_stamp() != STARTED_STAMP})


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
                if k not in ("media", "legibility", "media_sel", "crop", "shade", "logo_shade")}
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
    video = None
    m = R.selected_media(s)
    if m and m.get("kind") == "video" and m.get("file"):
        info = M.preview_info(m["file"], mdir)
        video = ({"url": f"/files/{name}/media/{info['name']}", "w": info["w"], "h": info["h"]}
                 if info else {"pending": True})
    s["shade"] = R.shade_settings(s)
    s["logo_shade"] = R.logo_shade_settings(s)
    return {"slide": s, "index": i, "src": src, "video": video, "elements": els}


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
EXPORT_DEFAULTS = {"folder": None, "png": True, "jpg": True, "ai": True, "pdf": True}


def export_options(proj):
    return {**EXPORT_DEFAULTS, **(proj.get("export") or {})}


def export_dir(name, proj):
    """Images, videos and the preview PDF. The .ai files always go to the project folder."""
    folder = export_options(proj)["folder"]
    return Path(folder) if folder else P.project_dir(name) / "export"


def _export(name):
    st = _exports[name]
    try:
        proj = P.load(name)
        opts = export_options(proj)
        root, out, mdir = P.project_dir(name), export_dir(name, proj), P.media_dir(name)
        formats = [f for f in ("png", "jpg") if opts[f]]
        # progress: each step weighted by roughly how long it takes
        is_video = lambda s: (R.selected_media(s) or {}).get("kind") == "video" \
            and (R.selected_media(s) or {}).get("file")
        weights = [6 if is_video(s) else 1 for s in proj["slides"]]
        total = sum(weights) + (1 if opts["pdf"] else 0) + (2 if opts["ai"] else 0)
        done = 0.0

        def advance(step, fraction=1.0):
            st["progress"] = min(1.0, (done + step * fraction) / total)

        stills = []
        for s, w in zip(proj["slides"], weights):
            st["log"].append(f"{R.slide_stem(s)}…")
            still, written = R.export_slide(s, mdir, out, log=st["log"].append, formats=formats,
                                            progress=lambda f, w=w: advance(w, f))
            stills.append(still)
            st["files"] += written
            done += w
            advance(0)
        if opts["pdf"]:
            R.export_pdf(stills, out / "preview.pdf")
            st["files"].append("preview.pdf")
            done += 1
            advance(0)
        st["log"].append(f"Images / videos → {out}")
        if opts["ai"]:
            st["log"].append("Writing Illustrator files…")
            st["files"] += export_ai.export_ai(proj["slides"], mdir, root, name)
            st["log"].append(f"Illustrator files → {root}")
            done += 2
        st["progress"] = 1.0
        st["log"].append("Done")
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
    _exports[name] = {"running": True, "log": [], "files": [], "error": None, "progress": 0.0,
                      "started": time.time()}
    threading.Thread(target=_export, args=(name,), daemon=True).start()
    return jsonify(_exports[name])


@app.get("/api/p/<name>/export")
def export_status(name):
    return jsonify(_exports.get(name) or {"running": False, "log": [], "files": []})


# ── export options ──────────────────────────────────────────────────────────
@app.get("/api/p/<name>/export_options")
def get_export_options(name):
    proj = P.load(name)
    return jsonify({**export_options(proj), "resolved": str(export_dir(name, proj)),
                    "project_dir": str(P.project_dir(name))})


@app.post("/api/p/<name>/export_options")
def set_export_options(name):
    data = request.get_json(force=True)
    with lock(name):
        proj = P.load(name)
        opts = export_options(proj)
        if data.pop("pick_folder", False):
            chosen = dialogs.pick_folder(start=export_dir(name, proj))
            if not chosen:
                return jsonify({**opts, "cancelled": True, "resolved": str(export_dir(name, proj)),
                                "project_dir": str(P.project_dir(name))})
            data["folder"] = chosen
        opts.update({k: v for k, v in data.items() if k in EXPORT_DEFAULTS})
        proj["export"] = opts
        P.save(proj)
    return jsonify({**opts, "resolved": str(export_dir(name, proj)),
                    "project_dir": str(P.project_dir(name))})


# ── single slide: ask where to save ─────────────────────────────────────────
def _save_dialog(name, i, ext):
    proj = P.load(name)
    s = proj["slides"][i]
    start = export_dir(name, proj)
    start.mkdir(parents=True, exist_ok=True)
    return s, dialogs.save_as(R.slide_stem(s) + ext, start=start,
                              title=f"Save {R.slide_stem(s)}{ext}")


@app.post("/api/p/<name>/slide/<int:i>/save_png")
def save_slide_png(name, i):
    s, path = _save_dialog(name, i, ".png")
    if not path:
        return jsonify({"cancelled": True})
    R.compose(s, P.media_dir(name)).save(path)
    return jsonify({"saved": path})


@app.post("/api/p/<name>/open_export")
def open_export(name):
    out = export_dir(name, P.load(name))
    out.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(out)  # noqa: S606
    else:
        import subprocess
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(out)])
    return jsonify({"ok": True})


if __name__ == "__main__":
    # file names are often non-ASCII; never let a console encoding crash a request or thread
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    if not take_over_port(PORT):
        print(f"Port {PORT} is used by another program. Close it, or start with "
              f"CAROUSEL_PORT=<other port>.", flush=True)
        sys.exit(1)
    threading.Thread(target=_watch_downloads, daemon=True).start()
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
