"""ffmpeg discovery shared by BulkVideoOverlayTool and the carousel builder."""
import os
import glob
import json
import shutil
import subprocess
from pathlib import Path

CONFIG_PATH = Path.home() / ".bulk_video_overlay_tool.json"
EXE = ".exe" if os.name == "nt" else ""


def _load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config(cfg):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


def _ffmpeg_candidate_dirs():
    """Folders where ffmpeg commonly lives when it isn't on PATH."""
    here = Path(__file__).resolve().parent
    dirs = [here, here / "ffmpeg", here / "ffmpeg" / "bin", here / "bin"]
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            dirs.append(Path(local) / "Microsoft" / "WinGet" / "Links")
            dirs += [Path(d) for d in glob.glob(os.path.join(
                local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin"))]
        dirs += [Path.home() / "scoop" / "shims",
                 Path("C:/ProgramData/chocolatey/bin"),
                 Path("C:/ffmpeg/bin"), Path("C:/ffmpeg")]
        for k in ("ProgramFiles", "ProgramFiles(x86)"):
            pf = os.environ.get(k)
            if pf:
                dirs += [Path(pf) / "ffmpeg" / "bin", Path(pf) / "ffmpeg"]
    else:
        dirs += [Path("/usr/local/bin"), Path("/opt/homebrew/bin"),
                 Path("/opt/local/bin"), Path("/usr/bin")]
    return dirs


def find_ffmpeg():
    """Return (ffmpeg, ffprobe) paths; either may be None."""
    saved = _load_config().get("ffmpeg_path")
    if saved and Path(saved).is_file():
        probe = Path(saved).with_name("ffprobe" + EXE)
        return saved, str(probe) if probe.is_file() else shutil.which("ffprobe")

    ff = shutil.which("ffmpeg")
    if ff:
        return ff, shutil.which("ffprobe")

    for d in _ffmpeg_candidate_dirs():
        exe = d / ("ffmpeg" + EXE)
        if exe.is_file():
            probe = d / ("ffprobe" + EXE)
            return str(exe), str(probe) if probe.is_file() else None

    try:  # `pip install imageio-ffmpeg` ships a static ffmpeg binary
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe(), None
    except Exception:
        return None, None


NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def probe_video(ffmpeg, path):
    """Return (width, height, duration) as displayed (rotation applied)."""
    import re
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    err = r.stderr
    m = re.search(r"Stream #.*?Video:.*?(\d{2,5})x(\d{2,5})", err)
    if not m:
        raise RuntimeError(f"No video stream found in {path}")
    w, h = int(m.group(1)), int(m.group(2))
    rot = re.search(r"rotate\s*:\s*(-?\d+)|rotation of (-?[\d.]+)", err)
    if rot and abs(round(float(rot.group(1) or rot.group(2)))) % 180 == 90:
        w, h = h, w
    d = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err)
    dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else 0.0
    return w, h, dur


def grab_frame(ffmpeg, video, out, ts=0.0):
    cmd = [ffmpeg, "-y", "-ss", f"{ts:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)]
    subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW, check=True)
    return out


def overlay_cmd(ffmpeg, video, overlay_png, out, scale_w, scale_h, crop_x, crop_y,
                out_w=1080, out_h=1440, crf=18, multiply_png=None):
    """Scale/crop a video to out_w x out_h and burn a full-frame transparent PNG on top.
    multiply_png: optional RGB image multiplied onto the frames first (shade in multiply mode)."""
    base = f"[0:v]scale={scale_w}:{scale_h},crop={out_w}:{out_h}:{crop_x}:{crop_y},setsar=1"
    inputs = ["-i", str(video), "-i", str(overlay_png)]
    if multiply_png:
        inputs += ["-i", str(multiply_png)]
        fc = (f"{base},format=gbrp[bg];[2:v]format=gbrp,setsar=1[m];"
              f"[bg][m]blend=all_mode=multiply[sh];"
              f"[sh][1:v]overlay=0:0:format=auto,format=yuv420p[v]")
    else:
        fc = f"{base}[bg];[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]"
    return [ffmpeg, "-y", *inputs,
            "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
