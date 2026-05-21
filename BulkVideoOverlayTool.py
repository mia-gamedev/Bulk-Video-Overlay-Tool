import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import threading
import tempfile
import math
import os
import shutil
from pathlib import Path

# ── theme ───────────────────────────────────────────────────────────────────
BG      = "#0f0f0f"
SURFACE = "#1a1a1a"
SURFACE2= "#1e1e1e"
BORDER  = "#2a2a2a"
ACCENT  = "#ff6b00"
ACCENT2 = "#ff9a00"
FG      = "#f0f0f0"
FG_DIM  = "#888888"
GREEN   = "#44cc66"
RED     = "#cc4444"
YELLOW  = "#ccaa00"
FONT    = ("Consolas", 10)
FONT_LG = ("Consolas", 13, "bold")
FONT_SM = ("Consolas", 9)
FONT_XS = ("Consolas", 8)


class FFmpegGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FFMPEG // BATCH SCALE · CROP · OVERLAY")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(820, 700)

        self.input_files = []   # list of str paths
        self.overlays    = {}   # video path -> overlay path
        self.running     = False
        self.stop_flag   = False

        self._check_ffmpeg()
        self._build_ui()

    # ── ffmpeg check ────────────────────────────────────────────────────────
    def _check_ffmpeg(self):
        self.ffmpeg_ok = shutil.which("ffmpeg") is not None

    # ── UI ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── header ──
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 4))
        tk.Label(header, text="▶  BULK VIDEO OVERLAY TOOL for FFMPEG", font=FONT_LG,
                 bg=BG, fg=ACCENT).pack(side="left")
        sc = GREEN if self.ffmpeg_ok else RED
        st = "ffmpeg found ✓" if self.ffmpeg_ok else "ffmpeg NOT found ✗"
        tk.Label(header, text=st, font=FONT_SM, bg=BG, fg=sc).pack(side="right")
        tk.Frame(self, bg=ACCENT, height=1).pack(fill="x", padx=24, pady=(0, 14))

        # ── two-column layout ──
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=24)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)

        left  = tk.Frame(body, bg=BG)
        right = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right.grid(row=0, column=1, sticky="nsew")

        # ════ LEFT COLUMN ════════════════════════════════════════════════════

        # column header row
        hdr = tk.Frame(left, bg=BG)
        hdr.pack(fill="x", pady=(0, 2))
        hdr.columnconfigure(0, weight=2)
        hdr.columnconfigure(1, weight=2)
        tk.Label(hdr, text="VIDEO", font=("Consolas", 8, "bold"),
                 bg=BG, fg=FG_DIM).grid(row=0, column=0, sticky="w")
        tk.Label(hdr, text="OVERLAY", font=("Consolas", 8, "bold"),
                 bg=BG, fg=FG_DIM).grid(row=0, column=1, sticky="w", padx=(8, 0))

        # scrollable file+overlay list
        list_frame = tk.Frame(left, bg=SURFACE, highlightthickness=1,
                              highlightbackground=BORDER)
        list_frame.pack(fill="both", expand=True)

        self.list_canvas = tk.Canvas(list_frame, bg=SURFACE, bd=0,
                                     highlightthickness=0)
        sb = ttk.Scrollbar(list_frame, orient="vertical",
                           command=self.list_canvas.yview)
        self.rows_frame = tk.Frame(self.list_canvas, bg=SURFACE)
        self.rows_window = self.list_canvas.create_window(
            (0, 0), window=self.rows_frame, anchor="nw")
        self.rows_frame.bind("<Configure>", self._on_rows_configure)
        self.list_canvas.bind("<Configure>", self._on_canvas_configure)
        self.list_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.list_canvas.pack(side="left", fill="both", expand=True)
        self.list_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.rows_frame.bind("<MouseWheel>", self._on_mousewheel)

        # list buttons
        lb_row = tk.Frame(left, bg=BG)
        lb_row.pack(fill="x", pady=(6, 0))
        self._btn(lb_row, "＋  Add files",   self._add_files,   ACCENT).pack(side="left", padx=(0, 6))
        self._btn(lb_row, "＋  Add folder",  self._add_folder,  ACCENT).pack(side="left", padx=(0, 6))
        self._btn(lb_row, "Clear all",       self._clear_files, SURFACE, FG_DIM).pack(side="left")

        self.count_lbl = tk.Label(left, text="0 files queued", font=FONT_XS,
                                  bg=BG, fg=FG_DIM, anchor="w")
        self.count_lbl.pack(anchor="w", pady=(4, 0))

        # OUTPUT FOLDER
        tk.Frame(left, bg=BORDER, height=1).pack(fill="x", pady=(12, 8))
        tk.Label(left, text="OUTPUT FOLDER", font=("Consolas", 9, "bold"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", pady=(0, 4))

        out_row = tk.Frame(left, bg=BG)
        out_row.pack(fill="x")
        self.out_dir_var = tk.StringVar()
        tk.Entry(out_row, textvariable=self.out_dir_var, font=FONT,
                 bg=SURFACE, fg=FG, insertbackground=ACCENT, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._btn(out_row, "Browse…", self._pick_out_dir, SURFACE, ACCENT).pack(side="left")

        # suffix
        suf_row = tk.Frame(left, bg=BG)
        suf_row.pack(fill="x", pady=(8, 0))
        tk.Label(suf_row, text="Output suffix", font=FONT_SM, bg=BG, fg=FG_DIM
                 ).pack(side="left", padx=(0, 8))
        self.suffix_var = tk.StringVar(value="_out")
        tk.Entry(suf_row, textvariable=self.suffix_var, font=FONT, width=12,
                 bg=SURFACE, fg=FG, insertbackground=ACCENT, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).pack(side="left")
        tk.Label(suf_row, text="(appended before .mp4)", font=FONT_XS,
                 bg=BG, fg=FG_DIM).pack(side="left", padx=(8, 0))

        # SCREENSHOT
        tk.Frame(left, bg=BORDER, height=1).pack(fill="x", pady=(12, 8))
        tk.Label(left, text="SCREENSHOT", font=("Consolas", 9, "bold"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", pady=(0, 4))
        scr_row = tk.Frame(left, bg=BG)
        scr_row.pack(fill="x")

        tk.Label(scr_row, text="Video", font=FONT_SM, bg=BG, fg=FG_DIM
                 ).pack(side="left", padx=(0, 6))
        self.scr_video_var = tk.StringVar()
        self.scr_combo = ttk.Combobox(scr_row, textvariable=self.scr_video_var,
                                      font=FONT_XS, state="readonly", width=20)
        self.scr_combo.pack(side="left", padx=(0, 8))

        tk.Label(scr_row, text="at", font=FONT_SM, bg=BG, fg=FG_DIM
                 ).pack(side="left", padx=(0, 6))
        self.scr_ts_var = tk.StringVar(value="00:00:05")
        tk.Entry(scr_row, textvariable=self.scr_ts_var, font=FONT, width=10,
                 bg=SURFACE, fg=FG, insertbackground=ACCENT, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).pack(side="left", padx=(0, 8))

        self._btn(scr_row, "📷  Screenshot", self._export_screenshot,
                  SURFACE, ACCENT).pack(side="left", padx=(0, 6))
        self._btn(scr_row, "🎬  Preview…", self._open_preview_window,
                  SURFACE, FG).pack(side="left")

        # ════ RIGHT COLUMN ════════════════════════════════════════════════════

        # OVERLAY
        tk.Label(right, text="DEFAULT OVERLAY (fallback)", font=("Consolas", 9, "bold"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", pady=(0, 4))
        ov_row = tk.Frame(right, bg=BG)
        ov_row.pack(fill="x")
        self.overlay_var = tk.StringVar()
        tk.Entry(ov_row, textvariable=self.overlay_var, font=FONT_XS,
                 bg=SURFACE, fg=FG, insertbackground=ACCENT, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._btn(ov_row, "Browse…", self._pick_overlay, SURFACE, ACCENT).pack(side="left")

        # PARAMETERS
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=(12, 8))
        tk.Label(right, text="PARAMETERS", font=("Consolas", 9, "bold"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", pady=(0, 6))

        self.scale_enabled = tk.BooleanVar(value=True)
        self.crop_enabled  = tk.BooleanVar(value=True)

        params_grid = tk.Frame(right, bg=BG)
        params_grid.pack(fill="x")
        params_grid.columnconfigure(2, weight=1)
        params_grid.columnconfigure(4, weight=1)

        self.param_vars     = []
        self._scale_entries = []
        self._crop_entries  = []

        def _entry(row, col, val):
            v = tk.StringVar(value=val)
            e = tk.Entry(params_grid, textvariable=v, font=FONT, bg=SURFACE, fg=FG,
                         insertbackground=ACCENT, relief="flat",
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT, width=10)
            e.grid(row=row, column=col, sticky="ew", pady=2)
            return v, e

        def _lbl(row, col, text):
            tk.Label(params_grid, text=text, font=FONT_XS, bg=BG, fg=FG_DIM
                     ).grid(row=row, column=col, sticky="w", padx=(4, 4), pady=2)

        def _chk(row, text, var, callback):
            tk.Checkbutton(params_grid, text=text, variable=var,
                           font=FONT_XS, bg=BG, fg=FG, selectcolor=BG,
                           activebackground=BG, activeforeground=FG,
                           command=callback).grid(row=row, column=0, sticky="w", pady=2)

        def toggle_scale():
            s = "normal" if self.scale_enabled.get() else "disabled"
            for e in self._scale_entries:
                e.configure(state=s,
                            fg=FG if s == "normal" else FG_DIM,
                            highlightbackground=BORDER if s == "normal" else "#1f1f1f")
            self._update_cmd_preview()

        def toggle_crop():
            s = "normal" if self.crop_enabled.get() else "disabled"
            for e in self._crop_entries:
                e.configure(state=s,
                            fg=FG if s == "normal" else FG_DIM,
                            highlightbackground=BORDER if s == "normal" else "#1f1f1f")
            self._update_cmd_preview()

        # Scale — Width and Height on one row
        _chk(0, "Scale", self.scale_enabled, toggle_scale)
        _lbl(0, 1, "W");  v, e = _entry(0, 2, "1440"); self.param_vars.append(v); self._scale_entries.append(e)
        _lbl(0, 3, "H");  v, e = _entry(0, 4, "-1");   self.param_vars.append(v); self._scale_entries.append(e)

        # Crop — W/H on one row, X/Y on the next
        _chk(1, "Crop", self.crop_enabled, toggle_crop)
        _lbl(1, 1, "W");  v, e = _entry(1, 2, "1080");           self.param_vars.append(v); self._crop_entries.append(e)
        _lbl(1, 3, "H");  v, e = _entry(1, 4, "1440");           self.param_vars.append(v); self._crop_entries.append(e)
        _lbl(2, 1, "X");  v, e = _entry(2, 2, "(1440-1080)/2");  self.param_vars.append(v); self._crop_entries.append(e)
        _lbl(2, 3, "Y");  v, e = _entry(2, 4, "0");              self.param_vars.append(v); self._crop_entries.append(e)

        # ADVANCED OPTIONS (collapsible encoding)
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=(12, 8))

        adv_header = tk.Frame(right, bg=BG)
        adv_header.pack(fill="x")
        self._adv_open = tk.BooleanVar(value=False)

        adv_toggle = tk.Button(
            adv_header, text="▶  ADVANCED OPTIONS", font=("Consolas", 9, "bold"),
            bg=BG, fg=FG_DIM, activebackground=BG, activeforeground=FG,
            relief="flat", cursor="hand2", bd=0, anchor="w"
        )
        adv_toggle.pack(fill="x")

        self.codec_var         = tk.StringVar(value="libx264")
        self.crf_var           = tk.StringVar(value="23")
        self.fps_var           = tk.StringVar(value="")
        self.trim_start_var    = tk.StringVar(value="")
        self.trim_end_var      = tk.StringVar(value="")
        self.audio_var         = tk.StringVar(value="copy")
        self.audio_bitrate_var = tk.StringVar(value="128k")

        adv_body = tk.Frame(right, bg=BG)
        # not packed yet — hidden by default

        enc_grid = tk.Frame(adv_body, bg=BG)
        enc_grid.pack(fill="x")
        enc_grid.columnconfigure(1, weight=1)
        enc_grid.columnconfigure(3, weight=1)

        def _enc_lbl(row, col, text, cspan=1):
            tk.Label(enc_grid, text=text, font=FONT_XS, bg=BG, fg=FG_DIM
                     ).grid(row=row, column=col, columnspan=cspan,
                            sticky="w", padx=(4, 2), pady=2)

        def _enc_entry(row, col, var, width=7):
            e = tk.Entry(enc_grid, textvariable=var, font=FONT, bg=SURFACE, fg=FG,
                         insertbackground=ACCENT, relief="flat",
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT, width=width)
            e.grid(row=row, column=col, sticky="ew", padx=(0, 4), pady=2)
            return e

        def _enc_combo(row, col, var, values, width=10):
            cb = ttk.Combobox(enc_grid, textvariable=var, values=values,
                              font=FONT_XS, state="readonly", width=width)
            cb.grid(row=row, column=col, sticky="ew", padx=(0, 4), pady=2)
            return cb

        # Row 0: Codec + CRF
        _enc_lbl(0, 0, "Codec")
        _enc_combo(0, 1, self.codec_var, ["libx264", "libx265", "libvpx-vp9"])
        _enc_lbl(0, 2, "CRF")
        _enc_entry(0, 3, self.crf_var, width=6)

        # Row 1: Audio + Bitrate
        _enc_lbl(1, 0, "Audio")
        audio_cb = _enc_combo(1, 1, self.audio_var, ["copy", "encode", "strip"])
        _enc_lbl(1, 2, "Bitrate")
        self._audio_br_entry = _enc_entry(1, 3, self.audio_bitrate_var, width=6)
        self._audio_br_entry.configure(state="disabled", fg=FG_DIM,
                                       highlightbackground="#1f1f1f")

        def toggle_audio(_=None):
            s = "normal" if self.audio_var.get() == "encode" else "disabled"
            self._audio_br_entry.configure(
                state=s,
                fg=FG if s == "normal" else FG_DIM,
                highlightbackground=BORDER if s == "normal" else "#1f1f1f"
            )
            self._update_cmd_preview()

        audio_cb.bind("<<ComboboxSelected>>", toggle_audio)

        # Row 2: FPS
        _enc_lbl(2, 0, "FPS")
        _enc_entry(2, 1, self.fps_var, width=7)
        _enc_lbl(2, 2, "(blank = source)", cspan=2)

        # Row 3: Trim
        _enc_lbl(3, 0, "Trim")
        _enc_entry(3, 1, self.trim_start_var, width=9)
        _enc_lbl(3, 2, "→")
        _enc_entry(3, 3, self.trim_end_var, width=9)

        def toggle_adv():
            if self._adv_open.get():
                adv_body.pack_forget()
                self._adv_open.set(False)
                adv_toggle.configure(text="▶  ADVANCED OPTIONS", fg=FG_DIM)
            else:
                adv_body.pack(fill="x", pady=(6, 0), after=adv_header)
                self._adv_open.set(True)
                adv_toggle.configure(text="▼  ADVANCED OPTIONS", fg=FG)

        adv_toggle.configure(command=toggle_adv)

        # COMMAND PREVIEW
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=(12, 8))
        tk.Label(right, text="COMMAND PREVIEW", font=("Consolas", 9, "bold"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", pady=(0, 4))
        cmd_frame = tk.Frame(right, bg=SURFACE, highlightthickness=1,
                             highlightbackground=BORDER)
        cmd_frame.pack(fill="x")
        self.cmd_preview = tk.Text(
            cmd_frame, font=FONT_XS, bg=SURFACE, fg=ACCENT2,
            relief="flat", bd=0, state="disabled", height=4,
            padx=6, pady=4, wrap="word"
        )
        self.cmd_preview.pack(fill="x")

        # BATCH STATUS TABLE
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=(12, 8))
        tk.Label(right, text="QUEUE STATUS", font=("Consolas", 9, "bold"),
                 bg=BG, fg=FG_DIM).pack(anchor="w", pady=(0, 4))

        tbl_frame = tk.Frame(right, bg=SURFACE, highlightthickness=1,
                             highlightbackground=BORDER)
        tbl_frame.pack(fill="both", expand=True)
        self.status_list = tk.Text(
            tbl_frame, font=FONT_XS, bg=SURFACE, fg=FG_DIM,
            relief="flat", bd=0, state="disabled", height=8,
            padx=6, pady=4
        )
        stbl_sb = ttk.Scrollbar(tbl_frame, orient="vertical",
                                command=self.status_list.yview)
        self.status_list.configure(yscrollcommand=stbl_sb.set)
        self.status_list.pack(side="left", fill="both", expand=True)
        stbl_sb.pack(side="right", fill="y")

        self.status_list.tag_configure("done",    foreground=GREEN)
        self.status_list.tag_configure("error",   foreground=RED)
        self.status_list.tag_configure("active",  foreground=YELLOW)
        self.status_list.tag_configure("pending", foreground=FG_DIM)

        # ════ BOTTOM ════════════════════════════════════════════════════════

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(12, 0))

        # log
        log_frame = tk.Frame(self, bg=BG)
        log_frame.pack(fill="x", padx=24, pady=(8, 0))
        tk.Label(log_frame, text="LOG", font=("Consolas", 9, "bold"),
                 bg=BG, fg=FG_DIM).pack(anchor="w")
        self.log = tk.Text(log_frame, height=5, font=FONT_XS,
                           bg=SURFACE, fg=FG_DIM, relief="flat",
                           highlightthickness=1, highlightbackground=BORDER,
                           state="disabled", padx=8, pady=6)
        self.log.pack(fill="x", pady=(4, 0))

        # progress
        prog_row = tk.Frame(self, bg=BG)
        prog_row.pack(fill="x", padx=24, pady=(8, 0))
        self.progress = ttk.Progressbar(prog_row, mode="determinate", length=400)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.prog_lbl = tk.Label(prog_row, text="0 / 0", font=FONT_SM,
                                 bg=BG, fg=FG_DIM, width=10)
        self.prog_lbl.pack(side="left")

        # run / stop buttons
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", padx=24, pady=(10, 20))
        self.run_btn = tk.Button(
            btn_row, text="▶  RUN BATCH", font=("Consolas", 12, "bold"),
            bg=ACCENT, fg="#000", activebackground=ACCENT2,
            activeforeground="#000", relief="flat", cursor="hand2",
            bd=0, pady=14, command=self._run
        )
        self.run_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.stop_btn = tk.Button(
            btn_row, text="■  STOP", font=("Consolas", 12, "bold"),
            bg=SURFACE, fg=RED, activebackground=BORDER,
            activeforeground=RED, relief="flat", cursor="hand2",
            bd=0, pady=14, width=10, state="disabled", command=self._stop
        )
        self.stop_btn.pack(side="left")

        # wire live command preview
        for v in self.param_vars:
            v.trace_add("write", self._update_cmd_preview)
        self.overlay_var.trace_add("write", self._update_cmd_preview)
        self.out_dir_var.trace_add("write", self._update_cmd_preview)
        self.suffix_var.trace_add("write", self._update_cmd_preview)
        for v in [self.codec_var, self.crf_var, self.fps_var,
                  self.trim_start_var, self.trim_end_var,
                  self.audio_var, self.audio_bitrate_var]:
            v.trace_add("write", self._update_cmd_preview)
        self._update_cmd_preview()

    # ── canvas scroll helpers ────────────────────────────────────────────────
    def _on_rows_configure(self, _):
        self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.list_canvas.itemconfig(self.rows_window, width=event.width)

    def _on_mousewheel(self, event):
        self.list_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _update_cmd_preview(self, *_):
        if not self.input_files:
            txt = "— add videos to see command —"
        else:
            inp     = self.input_files[0]
            out     = self._make_output_path(inp)
            overlay = self.overlays.get(inp) or self.overlay_var.get()
            if overlay:
                cmd = self._build_command(inp, out, overlay)
            else:
                fc, map_out = self._build_filter_complex(with_overlay=False)
                pre  = self._trim_pre_args()
                post = self._trim_post_args()
                if fc:
                    cmd = ["ffmpeg", "-y", *pre, "-i", inp,
                           "-filter_complex", fc, "-map", map_out,
                           *self._audio_args(), *self._fps_args(),
                           *self._codec_args(), *post, out]
                else:
                    cmd = ["ffmpeg", "-y", *pre, "-i", inp,
                           *self._audio_args(), *self._fps_args(),
                           *self._codec_args(), *post, out]
            txt = " ".join(cmd)
        self.cmd_preview.configure(state="normal")
        self.cmd_preview.delete("1.0", "end")
        self.cmd_preview.insert("1.0", txt)
        self.cmd_preview.configure(state="disabled")

    # ── helpers ─────────────────────────────────────────────────────────────
    def _btn(self, parent, text, cmd, bg=SURFACE, fg=FG):
        return tk.Button(parent, text=text, font=FONT_SM, bg=bg, fg=fg,
                         activebackground=BORDER, activeforeground=FG,
                         relief="flat", cursor="hand2", bd=0,
                         padx=10, pady=5, command=cmd)

    # ── file management ──────────────────────────────────────────────────────
    def _add_files(self):
        files = filedialog.askopenfilenames(
            title="Select input videos",
            filetypes=[("Video files", "*.mp4 *.mov *.avi *.mkv *.webm"), ("All", "*.*")]
        )
        for f in files:
            if f not in self.input_files:
                self.input_files.append(f)
        self._refresh_list()

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select folder of videos")
        if not folder:
            return
        exts = {".mp4", ".avi", ".mkv", ".webm"}
        for p in sorted(Path(folder).iterdir()):
            if p.suffix.lower() in exts and str(p) not in self.input_files:
                self.input_files.append(str(p))
                # auto-pair: look for a .png with the same stem
                png = p.with_suffix(".png")
                if png.exists() and str(p) not in self.overlays:
                    self.overlays[str(p)] = str(png)
        self._refresh_list()

    def _remove_at(self, idx):
        self.overlays.pop(self.input_files[idx], None)
        self.input_files.pop(idx)
        self._refresh_list()

    def _clear_files(self):
        self.input_files.clear()
        self.overlays.clear()
        self._refresh_list()

    def _refresh_list(self):
        for w in self.rows_frame.winfo_children():
            w.destroy()
        for i, f in enumerate(self.input_files):
            self._build_row(i, f)
        n = len(self.input_files)
        self.count_lbl.configure(text=f"{n} file{'s' if n != 1 else ''} queued")
        # keep screenshot combobox in sync
        names = [Path(f).name for f in self.input_files]
        self.scr_combo["values"] = names
        if names and self.scr_video_var.get() not in names:
            self.scr_combo.current(0)
        self._update_cmd_preview()

    def _build_row(self, idx, video_path):
        row_bg = SURFACE if idx % 2 == 0 else SURFACE2
        row = tk.Frame(self.rows_frame, bg=row_bg)
        row.pack(fill="x")
        row.columnconfigure(0, weight=2)
        row.columnconfigure(1, weight=2)
        row.bind("<MouseWheel>", self._on_mousewheel)

        # video filename
        vname = Path(video_path).name
        vlbl = tk.Label(row, text=vname, font=FONT_XS, bg=row_bg, fg=FG,
                        anchor="w")
        vlbl.grid(row=0, column=0, sticky="ew", padx=(6, 4), pady=4)
        vlbl.bind("<MouseWheel>", self._on_mousewheel)

        # overlay filename
        ov = self.overlays.get(video_path, "")
        ov_text  = Path(ov).name if ov else "— no overlay —"
        ov_color = FG if ov else FG_DIM
        ovlbl = tk.Label(row, text=ov_text, font=FONT_XS, bg=row_bg, fg=ov_color,
                         anchor="w")
        ovlbl.grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=4)
        ovlbl.bind("<MouseWheel>", self._on_mousewheel)

        # browse button
        browse_btn = tk.Button(
            row, text="Browse…", font=FONT_XS, bg=BORDER, fg=ACCENT,
            activebackground=ACCENT, activeforeground="#000",
            relief="flat", cursor="hand2", bd=0, padx=8, pady=3,
            command=lambda i=idx: self._pick_overlay_for(i)
        )
        browse_btn.grid(row=0, column=2, padx=(0, 4), pady=3)
        browse_btn.bind("<MouseWheel>", self._on_mousewheel)

        # remove button
        rm_btn = tk.Button(
            row, text="✕", font=FONT_XS, bg=BORDER, fg=FG_DIM,
            activebackground=RED, activeforeground="#fff",
            relief="flat", cursor="hand2", bd=0, padx=6, pady=3,
            command=lambda i=idx: self._remove_at(i)
        )
        rm_btn.grid(row=0, column=3, padx=(0, 4), pady=3)
        rm_btn.bind("<MouseWheel>", self._on_mousewheel)

    # ── file dialogs ─────────────────────────────────────────────────────────
    _OVERLAY_TYPES = [
        ("Image / Video", "*.png *.jpg *.jpeg *.webp *.mp4 *.mov *.avi *.mkv *.webm"),
        ("Images", "*.png *.jpg *.jpeg *.webp"),
        ("Videos", "*.mp4 *.mov *.avi *.mkv *.webm"),
        ("All", "*.*"),
    ]

    def _pick_overlay(self):
        f = filedialog.askopenfilename(
            title="Select default overlay (image or video)",
            filetypes=self._OVERLAY_TYPES
        )
        if f:
            self.overlay_var.set(f)

    def _pick_overlay_for(self, idx):
        f = filedialog.askopenfilename(
            title=f"Select overlay for: {Path(self.input_files[idx]).name}",
            filetypes=self._OVERLAY_TYPES
        )
        if f:
            self.overlays[self.input_files[idx]] = f
            self._refresh_list()

    def _pick_out_dir(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self.out_dir_var.set(d)

    # ── screenshot / preview ─────────────────────────────────────────────────
    def _probe_duration(self, path):
        try:
            cf = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, creationflags=cf, timeout=10
            )
            return float(r.stdout.strip())
        except Exception:
            return 3600.0

    def _open_preview_window(self):
        sel_name = self.scr_video_var.get()
        if not sel_name:
            messagebox.showwarning("No video", "Add videos to the queue first.")
            return
        inp = next((f for f in self.input_files if Path(f).name == sel_name), None)
        if inp is None:
            return

        win = tk.Toplevel(self)
        win.title(f"Preview — {Path(inp).name}")
        win.configure(bg=BG)
        win.resizable(False, False)

        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)

        generation = [0]
        after_id   = [None]

        def fmt_ts(secs):
            h = int(secs // 3600)
            m = int((secs % 3600) // 60)
            s = secs % 60
            return f"{h:02d}:{m:02d}:{s:05.2f}"

        def parse_ts(ts_str):
            try:
                parts = ts_str.split(":")
                if len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                if len(parts) == 2:
                    return int(parts[0]) * 60 + float(parts[1])
                return float(parts[0])
            except Exception:
                return 5.0

        # ── preview image area ──
        preview_frame = tk.Frame(win, bg="#111", width=320, height=240)
        preview_frame.pack(padx=16, pady=(16, 0))
        preview_frame.pack_propagate(False)
        preview_lbl = tk.Label(preview_frame, bg="#111", text="loading…",
                               font=FONT_SM, fg=FG_DIM)
        preview_lbl.pack(expand=True)

        ts_lbl = tk.Label(win, text="--:--:--.--", font=FONT_SM, bg=BG, fg=FG)
        ts_lbl.pack(pady=(6, 0))

        # ── probe duration then build slider ──
        duration   = self._probe_duration(inp)
        cur_secs   = min(parse_ts(self.scr_ts_var.get()), duration)
        slider_var = tk.DoubleVar(value=cur_secs)

        def load_frame(secs):
            generation[0] += 1
            gen = generation[0]
            overlay = self.overlays.get(inp) or self.overlay_var.get()
            ts_str  = fmt_ts(secs)
            if overlay:
                fc, map_out = self._build_filter_complex(with_overlay=True,
                                                         preview_width=320)
                cmd = ["ffmpeg", "-y", "-ss", ts_str, "-i", inp,
                       *self._overlay_input_args(overlay),
                       "-filter_complex", fc, "-map", map_out, "-vframes", "1", tmp_path]
            else:
                fc, map_out = self._build_filter_complex(with_overlay=False,
                                                         preview_width=320)
                if fc:
                    cmd = ["ffmpeg", "-y", "-ss", ts_str, "-i", inp,
                           "-filter_complex", fc, "-map", map_out,
                           "-vframes", "1", tmp_path]
                else:
                    cmd = ["ffmpeg", "-y", "-ss", ts_str, "-i", inp,
                           "-vframes", "1", tmp_path]

            def run():
                try:
                    cf = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                    subprocess.run(cmd, creationflags=cf, timeout=15,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if win.winfo_exists():
                        win.after(0, lambda g=gen: update_image(g))
                except Exception:
                    pass

            def update_image(gen_at_call):
                if gen_at_call != generation[0] or not win.winfo_exists():
                    return
                try:
                    img = tk.PhotoImage(file=tmp_path)
                    preview_frame.configure(width=img.width(), height=img.height())
                    preview_lbl.configure(image=img, text="")
                    preview_lbl.image = img
                except Exception:
                    pass

            threading.Thread(target=run, daemon=True).start()

        def on_slider_move(val):
            secs = float(val)
            ts_lbl.configure(text=fmt_ts(secs))
            if after_id[0]:
                win.after_cancel(after_id[0])
            after_id[0] = win.after(350, lambda s=secs: load_frame(s))

        slider = tk.Scale(
            win, variable=slider_var, from_=0, to=duration,
            orient="horizontal", resolution=0.1,
            bg=BG, fg=FG_DIM, troughcolor=SURFACE2, highlightthickness=0,
            sliderrelief="flat", activebackground=ACCENT,
            command=on_slider_move, length=420, showvalue=False
        )
        slider.pack(padx=16, fill="x", pady=(4, 0))

        dur_row = tk.Frame(win, bg=BG)
        dur_row.pack(fill="x", padx=20)
        tk.Label(dur_row, text="00:00:00.00", font=FONT_XS, bg=BG, fg=FG_DIM).pack(side="left")
        tk.Label(dur_row, text=fmt_ts(duration), font=FONT_XS, bg=BG, fg=FG_DIM).pack(side="right")

        # ── buttons ──
        tk.Frame(win, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(10, 0))
        btn_row = tk.Frame(win, bg=BG)
        btn_row.pack(fill="x", padx=16, pady=(8, 16))

        def use_ts():
            self.scr_ts_var.set(fmt_ts(slider_var.get()))

        def use_and_close():
            use_ts()
            win.destroy()

        def export_and_close():
            use_ts()
            win.destroy()
            self._export_screenshot()

        self._btn(btn_row, "📷  Export Screenshot", export_and_close,
                  ACCENT, "#000").pack(side="left", padx=(0, 8))
        self._btn(btn_row, "Use timestamp", use_and_close,
                  SURFACE, ACCENT).pack(side="left", padx=(0, 8))
        self._btn(btn_row, "Close", win.destroy,
                  SURFACE, FG_DIM).pack(side="left")

        def on_close():
            if after_id[0]:
                win.after_cancel(after_id[0])
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        ts_lbl.configure(text=fmt_ts(cur_secs))
        load_frame(cur_secs)

    def _export_screenshot(self):
        if not self.ffmpeg_ok:
            messagebox.showerror("ffmpeg not found", "ffmpeg not on PATH.")
            return
        sel_name = self.scr_video_var.get()
        if not sel_name:
            messagebox.showwarning("No video", "Add videos to the queue first.")
            return
        inp = next((f for f in self.input_files if Path(f).name == sel_name), None)
        if inp is None:
            messagebox.showwarning("Video not found", f"Could not resolve '{sel_name}'.")
            return

        ts      = self.scr_ts_var.get().strip() or "00:00:05"
        overlay = self.overlays.get(inp) or self.overlay_var.get()
        p       = Path(inp)
        ts_safe = ts.replace(":", "-")
        out_dir = self.out_dir_var.get()
        out     = str((Path(out_dir) if out_dir else p.parent) /
                      f"{p.stem}_screenshot_{ts_safe}.png")

        if overlay:
            fc, map_out = self._build_filter_complex(with_overlay=True)
            cmd = ["ffmpeg", "-y", "-ss", ts, "-i", inp,
                   *self._overlay_input_args(overlay),
                   "-filter_complex", fc, "-map", map_out, "-vframes", "1", out]
        else:
            fc, map_out = self._build_filter_complex(with_overlay=False)
            if fc:
                cmd = ["ffmpeg", "-y", "-ss", ts, "-i", inp,
                       "-filter_complex", fc, "-map", map_out, "-vframes", "1", out]
            else:
                cmd = ["ffmpeg", "-y", "-ss", ts, "-i", inp, "-vframes", "1", out]

        self._log(f"\n📷  Screenshot: {p.name} @ {ts}\n$ {' '.join(cmd)}\n")
        threading.Thread(target=self._run_screenshot, args=(cmd, out), daemon=True).start()

    def _run_screenshot(self, cmd, out):
        try:
            cf   = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, creationflags=cf)
            for line in proc.stdout:
                self._log(line)
            proc.wait()
            if proc.returncode == 0:
                self._log(f"✓  Screenshot saved → {out}\n")
            else:
                self._log(f"✗  ffmpeg error (code {proc.returncode})\n")
        except Exception as e:
            self._log(f"✗  Exception: {e}\n")

    # ── command builder ──────────────────────────────────────────────────────
    _VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

    def _overlay_input_args(self, path):
        """Return ffmpeg -i args for an overlay; loop video overlays."""
        if Path(path).suffix.lower() in self._VIDEO_EXTS:
            return ["-stream_loop", "-1", "-i", path]
        return ["-i", path]

    def _trim_pre_args(self):
        s = self.trim_start_var.get().strip()
        return ["-ss", s] if s else []

    def _trim_post_args(self):
        e = self.trim_end_var.get().strip()
        return ["-to", e] if e else []

    def _codec_args(self):
        codec = self.codec_var.get()
        crf   = self.crf_var.get().strip()
        args  = ["-c:v", codec]
        if crf:
            args += ["-crf", crf]
        return args

    def _fps_args(self):
        fps = self.fps_var.get().strip()
        return ["-r", fps] if fps else []

    def _audio_args(self):
        mode = self.audio_var.get()
        if mode == "strip":
            return ["-an"]
        br = self.audio_bitrate_var.get().strip()
        if mode == "encode":
            return ["-map", "0:a?", "-c:a", "aac"] + (["-b:a", br] if br else [])
        return ["-map", "0:a?", "-c:a", "copy"]

    def _build_filter_complex(self, with_overlay, preview_width=None):
        """Build (filter_complex_str | None, map_label | None).

        with_overlay:  True when a second -i (overlay) is already in the command.
        preview_width: if set, append a final scale for display-only use.
        Returns (None, None) when no filters are needed at all.
        """
        scale_on = self.scale_enabled.get()
        crop_on  = self.crop_enabled.get()
        sw, sh, cw, ch, cx, cy = [v.get() for v in self.param_vars]

        steps = []
        prev  = "0:v"
        n     = [0]

        def add(filt):
            lbl = f"s{n[0]}"; n[0] += 1
            steps.append(f"[{prev}]{filt}[{lbl}]")
            return lbl

        if scale_on:
            prev = add(f"scale={sw}:{sh}")
        if crop_on:
            prev = add(f"crop={cw}:{ch}:{cx}:{cy}")
        if with_overlay:
            ov_lbl  = f"s{n[0]}"; n[0] += 1
            out_lbl = f"s{n[0]}"; n[0] += 1
            steps.append(f"[1:v]format=rgba[{ov_lbl}]")
            steps.append(f"[{prev}][{ov_lbl}]overlay=0:0:eof_action=pass[{out_lbl}]")
            prev = out_lbl
        if preview_width:
            prev = add(f"scale={preview_width}:-2")

        if not steps:
            return None, None

        # rename the last output to [out]
        last = steps[-1]
        steps[-1] = last[: last.rfind("[")] + "[out]"
        return ";".join(steps), "[out]"

    def _calc_overlay_loops(self, source_path, overlay_path):
        """Return how many complete overlay cycles fit in the source duration.
        Returns None for image overlays or if probing fails."""
        if Path(overlay_path).suffix.lower() not in self._VIDEO_EXTS:
            return None
        src_dur = self._probe_duration(source_path)
        ov_dur  = self._probe_duration(overlay_path)
        if src_dur <= 0 or ov_dur <= 0:
            return None
        return math.floor(src_dur / ov_dur)

    def _build_command(self, inp, out, overlay, n_loops=None):
        is_vid_ov = Path(overlay).suffix.lower() in self._VIDEO_EXTS
        fc, map_out = self._build_filter_complex(with_overlay=True)
        if is_vid_ov and n_loops is not None:
            ov_args = ["-stream_loop", str(n_loops - 1), "-i", overlay]
        else:
            ov_args = self._overlay_input_args(overlay)
        return [
            "ffmpeg", "-y", *self._trim_pre_args(), "-i", inp, *ov_args,
            "-filter_complex", fc, "-map", map_out,
            *self._audio_args(), *self._fps_args(), *self._codec_args(),
            *self._trim_post_args(), out
        ]

    def _make_output_path(self, inp):
        p       = Path(inp)
        suffix  = self.suffix_var.get() or "_out"
        out_dir = self.out_dir_var.get()
        if out_dir:
            return str(Path(out_dir) / (p.stem + suffix + ".mp4"))
        return str(p.parent / (p.stem + suffix + ".mp4"))

    # ── run / stop ───────────────────────────────────────────────────────────
    def _run(self):
        if not self.ffmpeg_ok:
            messagebox.showerror("ffmpeg not found",
                "ffmpeg was not found on your PATH.\n"
                "Download it from https://ffmpeg.org/download.html")
            return
        if not self.input_files:
            messagebox.showwarning("No files", "Add at least one input video.")
            return
        self.stop_flag = False
        self.running   = True
        self.run_btn.configure(state="disabled", text="⏳  PROCESSING…")
        self.stop_btn.configure(state="normal")
        self._log_clear()
        self._status_clear()
        self.progress.configure(maximum=len(self.input_files), value=0)
        self.prog_lbl.configure(text=f"0 / {len(self.input_files)}")

        for f in self.input_files:
            self._status_append(f"⏸  {Path(f).name}\n", "pending")

        threading.Thread(target=self._run_batch, daemon=True).start()

    def _stop(self):
        self.stop_flag = True
        self._log("\n⚠  Stop requested — finishing current file…\n")

    def _run_batch(self):
        total  = len(self.input_files)
        done   = 0
        errors = 0

        for idx, inp in enumerate(self.input_files):
            if self.stop_flag:
                self._log("\n■  Batch stopped by user.\n")
                break

            out     = self._make_output_path(inp)
            name    = Path(inp).name
            overlay = self.overlays.get(inp) or self.overlay_var.get()
            if overlay:
                n_loops = self._calc_overlay_loops(inp, overlay)
                cmd = self._build_command(inp, out, overlay, n_loops=n_loops)
            else:
                fc, map_out = self._build_filter_complex(with_overlay=False)
                pre  = self._trim_pre_args()
                post = self._trim_post_args()
                if fc:
                    cmd = ["ffmpeg", "-y", *pre, "-i", inp,
                           "-filter_complex", fc, "-map", map_out,
                           *self._audio_args(), *self._fps_args(),
                           *self._codec_args(), *post, out]
                else:
                    cmd = ["ffmpeg", "-y", *pre, "-i", inp,
                           *self._audio_args(), *self._fps_args(),
                           *self._codec_args(), *post, out]

            self._status_update(idx, f"▶  {name}\n", "active")
            self._log(f"\n[{idx+1}/{total}] {name}\n$ {' '.join(cmd)}\n")

            try:
                cf = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, creationflags=cf
                )
                for line in proc.stdout:
                    self._log(line)
                proc.wait()

                if proc.returncode == 0:
                    done += 1
                    self._status_update(idx, f"✓  {name}\n", "done")
                    self._log(f"✓  Saved → {out}\n")
                else:
                    errors += 1
                    self._status_update(idx, f"✗  {name}\n", "error")
                    self._log(f"✗  ffmpeg error (code {proc.returncode})\n")

            except Exception as e:
                errors += 1
                self._status_update(idx, f"✗  {name}\n", "error")
                self._log(f"✗  Exception: {e}\n")

            self.after(0, lambda v=idx+1: (
                self.progress.configure(value=v),
                self.prog_lbl.configure(text=f"{v} / {total}")
            ))

        summary = f"\n{'─'*40}\nDone: {done}  Errors: {errors}  Total: {total}\n"
        self._log(summary)
        self.after(0, self._done)

    def _done(self):
        self.running = False
        self.run_btn.configure(state="normal", text="▶  RUN BATCH")
        self.stop_btn.configure(state="disabled")

    # ── log ──────────────────────────────────────────────────────────────────
    def _log(self, text):
        self.after(0, lambda t=text: self._log_append(t))

    def _log_append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log_clear(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ── status table ─────────────────────────────────────────────────────────
    def _status_append(self, text, tag="pending"):
        self.status_list.configure(state="normal")
        self.status_list.insert("end", text, tag)
        self.status_list.configure(state="disabled")

    def _status_clear(self):
        self.status_list.configure(state="normal")
        self.status_list.delete("1.0", "end")
        self.status_list.configure(state="disabled")

    def _status_update(self, idx, text, tag):
        def _do():
            self.status_list.configure(state="normal")
            start = f"{idx + 1}.0"
            end   = f"{idx + 1}.end"
            self.status_list.delete(start, end)
            self.status_list.insert(start, text.rstrip("\n"), tag)
            self.status_list.configure(state="disabled")
        self.after(0, _do)


if __name__ == "__main__":
    app = FFmpegGUI()
    app.mainloop()
