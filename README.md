# Bulk Video Overlay Tool for FFMPEG

A dark-themed desktop app for bulk-adding overlays to videos, with extra options for scaling, cropping, encoding, and trimming. No command line needed.

**Requirements:** Python 3.x · ffmpeg on PATH

```
pip install tkinter   # usually bundled with Python
```

---

## Overview

┌──────────────────────────────────────────────────────────────────────────┐
│  ▶ FFMPEG BATCH PIPELINE                              ffmpeg found ✓     │
├──────────────────────────────────────────┬───────────────────────────────┤
│  VIDEO                OVERLAY            │  DEFAULT OVERLAY (fallback)   │
│ ┌─────────────────────────────────────┐  │  ┌─────────────────┐ [Browse] │
│ │ clip_01.mp4  logo.png   [Browse][✕]     │   │  └─────────────────┘          │
│ │ clip_02.mp4  — no overlay — [Br][✕]     │   ├───────────────────────────────┤
│ │ clip_03.mp4  intro.mov  [Browse][✕]     │   │  PARAMETERS                           │
│ └─────────────────────────────────────┘  │  ☑ Scale  W [1440]  H [  -1]      │
│  [＋ Add files] [＋ Add folder] [Clear]       │  ☑ Crop   W [1080]  H [1440]      │
│  3 files queued                          │             X [ 180]  Y [   0]│
├──────────────────────────────────────────┤                               │
│  OUTPUT FOLDER                           ├───────────────────────────────┤
│  ┌────────────────────────┐ [Browse…]    │  ▶ ADVANCED OPTIONS           │
│  └────────────────────────┘              ├───────────────────────────────┤
│  Output suffix  [ _out ]                 │  COMMAND PREVIEW              │
├──────────────────────────────────────────┤  ┌───────────────────────┐    │
│  SCREENSHOT                              │  │ ffmpeg -y -i clip_01  │    │
│  Video [clip_01.mp4 ▼]  at [00:00:05]    │  │ .mp4 -i logo.png ...  │    │
│  [📷 Screenshot]  [🎬 Preview…]             │  └───────────────────────┘    │
│                                          ├───────────────────────────────┤
│                                          │  QUEUE STATUS                 │
│                                          │  ✓ clip_01.mp4                │
│                                          │  ▶ clip_02.mp4                │
│                                          │  ⏸ clip_03.mp4                  │
├──────────────────────────────────────────┴───────────────────────────────┤
│  LOG                                                                     │
│  [1/3] clip_01.mp4  ✓ Saved → output/clip_01_out.mp4                     │
│  ████████████░░░░░░░░  1 / 3                                             │
│  [▶ RUN BATCH ───────────────────────────────────────]   [■ STOP]        │
└──────────────────────────────────────────────────────────────────────────┘


---

## Step 1 — Add Videos

### Add individual files
Click **＋ Add files** and pick one or more video files (`.mp4`, `.avi`, `.mkv`, `.webm`).

### Add a whole folder
Click **＋ Add folder** to load every video in a folder at once.  
`.mov` files are skipped automatically (they are treated as overlays).

### Auto-pairing
When adding a folder, any video that has a file with the **same name** is automatically paired as its overlay:

```
/videos
  ├── clip_01.mp4  ←─┐ auto-paired
  ├── clip_01.png  ←─┘
  ├── clip_02.mp4  ←─┐ auto-paired
  └── clip_02.mov  ←─┘
```

---

## Step 2 — Assign Overlays

Each row shows the video, its paired overlay, and a **Browse…** button to swap it.


┌──────────────────────┬─────────────────────┬──────────┬──────┐
│ VIDEO                │ OVERLAY             │          │              │
├──────────────────────┼─────────────────────┼──────────┼──────┤
│ clip_01.mp4          │ logo.png            │ [Browse] │ [✕]       │
│ clip_02.mp4          │ — no overlay —      │ [Browse] │ [✕]       │
│ clip_03.mp4          │ intro.mov           │ [Browse] │ [✕]       │
└──────────────────────┴─────────────────────┴──────────┴──────┘


Set the **Default Overlay (fallback)** in the right column — any video without its own overlay uses it.

Overlays can be **images** (PNG, JPG, WEBP) or **videos** (MP4, MOV, MKV…).  
Video overlays loop automatically — only complete cycles play, so the overlay never cuts off mid-loop.

---

## Step 3 — Set Parameters


┌──────────────────────────────────────────────┐
│ ☑ Scale   W [ 1440 ]   H [   -1 ]                │
├──────────────────────────────────────────────┤
│ ☑ Crop    W [ 1080 ]   H [ 1440 ]                │
│           X [  180 ]   Y [    0 ]            │
└──────────────────────────────────────────────┘


| Setting | Description |
|---------|-------------|
| **Scale W / H** | Output resolution. Use `-1` to preserve aspect ratio on that axis. |
| **Crop W / H** | Width and height of the cropped region. |
| **Crop X / Y** | Top-left offset. Expressions like `(1440-1080)/2` are valid. |

Uncheck **Scale** or **Crop** to skip that step entirely.

---

## Step 4 — Output Folder & Suffix


┌────────────────────────────────────────────────┐
│  OUTPUT FOLDER                                 │
│  ┌──────────────────────────────┐  [Browse…]   │
│  │ C:\Users\me\output\          │              │
│  └──────────────────────────────┘              │
├────────────────────────────────────────────────┤
│  Output suffix  [ _out ]                       │
│  (appended before .mp4)                        │
└────────────────────────────────────────────────┘

Each file is saved as `<original_name><suffix>.mp4`.  
Leave the output folder blank to save next to the source files.

---

## Step 5 — Run

Click **▶ RUN BATCH**. Each file's status is tracked live in the Queue Status panel.


┌──────────────────────────────────────┐
│  ✓ clip_01.mp4                       │  ← done    (green)
│  ▶ clip_02.mp4                       │  ← running (yellow)
│  ⏸ clip_03.mp4                         │  ← pending (grey)
└──────────────────────────────────────┘

  ████████████████░░░░░░░░   2 / 3


Click **■ STOP** to finish the current file then halt.

---

## Screenshot & Preview

### Preview window
Click **🎬 Preview…** to open a live scrubber with all filters applied.


┌──────────────────────────────────────────────────────┐
│  ┌────────────────────────────────────────────────┐  │
│  │                                                │  │
│  │          scaled · cropped · overlay            │  │
│  │                                                │  │
│  └────────────────────────────────────────────────┘  │
│  00:00:08.30                                         │
│  ───────────────────●────────────────────────────    │
│  00:00:00.00                           00:00:30.00   │
├──────────────────────────────────────────────────────┤
│  [📷 Export Screenshot]  [Use timestamp]  [Close]         │
└──────────────────────────────────────────────────────┘


Drag the slider to any frame. **Export Screenshot** saves that frame as a PNG. **Use timestamp** copies the time back to the Screenshot field.

### Quick screenshot
Pick a video in the **Screenshot** row, enter a timestamp, and click **📷 Screenshot** to save a PNG directly.

---

## Advanced Options

Click **▶ ADVANCED OPTIONS** to expand the encoding panel.


┌───────────────────────────────────────────────────┐
│  ▼ ADVANCED OPTIONS                               │
├───────────────────────────────────────────────────┤
│  Codec  [ libx264      ▼ ]   CRF     [  23 ]      │
├───────────────────────────────────────────────────┤
│  Audio  [ copy         ▼ ]   Bitrate [ 128k ]     │
├───────────────────────────────────────────────────┤
│  FPS    [        ]   (blank = source fps)         │
├───────────────────────────────────────────────────┤
│  Trim   [ 00:00:05 ]   →   [ 00:01:30 ]           │
└───────────────────────────────────────────────────┘


| Option | Description |
|--------|-------------|
| **Codec** | `libx264` (H.264) · `libx265` (H.265/HEVC) · `libvpx-vp9` (VP9) |
| **CRF** | Quality — lower = better quality, larger file. Typical range: 18–28. |
| **Audio** | `copy` pass through · `encode` re-encode to AAC · `strip` remove audio |
| **Bitrate** | AAC bitrate when Audio is set to `encode` (e.g. `128k`, `192k`) |
| **FPS** | Output frame rate. Leave blank to keep the source frame rate. |
| **Trim** | Start and end timestamps (`HH:MM:SS`). Leave blank for the full video. |

---

## Command Preview

Shows the exact ffmpeg command for the first queued video. Updates live as you change any setting.


┌────────────────────────────────────────────────────────────┐
│  ffmpeg -y -i clip_01.mp4 -i logo.png                      │
│  -filter_complex                                           │
│    "[0:v]scale=1440:-1[s0];                                │
│     [s0]crop=1080:1440:180:0[s1];                          │
│     [1:v]format=rgba[s2];                                  │
│     [s1][s2]overlay=0:0:eof_action=pass[out]"              │
│  -map [out] -map 0:a? -c:a copy                            │
│  -c:v libx264 -crf 23 output/clip_01_out.mp4               │
└────────────────────────────────────────────────────────────┘


---

## Tips

- **Alpha transparency** is fully supported for PNG overlays.
- **Video overlay looping** only repeats complete cycles — if the overlay is longer than the source it plays once and stops cleanly.
- **Expressions in crop fields** like `(iw-1080)/2` are passed directly to ffmpeg and evaluated at runtime.
- Batch processing runs in a background thread — the UI stays responsive during encoding.
