"""Native folder / save dialogs for the browser UI.

The server runs on the same machine as the browser, so it can show the normal
Windows (or macOS / Linux) dialog. Each dialog runs in its own short-lived Python
process: Tk then always has its own main thread and never blocks the web server.
"""
import json
import os
import subprocess
import sys

_SCRIPT = r"""
import json, sys, tkinter as tk
from tkinter import filedialog
a = json.loads(sys.argv[1])
root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True); root.update()
if a["kind"] == "folder":
    p = filedialog.askdirectory(parent=root, title=a["title"], initialdir=a["dir"] or None,
                                mustexist=False)
else:
    p = filedialog.asksaveasfilename(parent=root, title=a["title"], initialdir=a["dir"] or None,
                                     initialfile=a["file"], defaultextension=a["ext"],
                                     filetypes=[(a["label"], "*" + a["ext"]), ("All files", "*.*")])
print(json.dumps(p or None))
"""


def _run(args):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    r = subprocess.run([sys.executable, "-c", _SCRIPT, json.dumps(args)],
                       capture_output=True, text=True, timeout=1800, creationflags=flags)
    if r.returncode != 0:
        raise RuntimeError("Could not open the dialog: " + r.stderr.strip()[-300:])
    lines = r.stdout.strip().splitlines()
    return json.loads(lines[-1]) if lines else None


def pick_folder(title="Choose the export folder", start=None):
    """Absolute folder path, or None when cancelled."""
    return _run({"kind": "folder", "title": title, "dir": str(start or "")})


def save_as(filename, start=None, title="Save as"):
    """Absolute file path chosen by the user, or None when cancelled."""
    ext = os.path.splitext(filename)[1]
    labels = {".png": "PNG image", ".jpg": "JPEG image", ".ai": "Illustrator file", ".mp4": "MP4 video"}
    return _run({"kind": "save", "title": title, "dir": str(start or ""), "file": filename,
                 "ext": ext, "label": labels.get(ext.lower(), ext)})
