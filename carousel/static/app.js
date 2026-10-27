"use strict";
const CW = 1080, CH = 1440;
const SNAP_X = [100, 540, 980], SNAP_Y = [195, 720, 1300], SNAP_PX = 12;
const ZONES = [["top", "left", "↖"], ["top", "center", "↑"], ["top", "right", "↗"],
               ["middle", "left", "←"], ["middle", "center", "•"], ["middle", "right", "→"],
               ["bottom", "left", "↙"], ["bottom", "center", "↓"], ["bottom", "right", "↘"]];

const $ = (id) => document.getElementById(id);
const state = { name: null, project: null, sel: 0, view: null, status: null, thumbV: {}, dirtyThumbs: new Set() };

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(path, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function setStatus(msg, isErr) {
  $("statusLine").textContent = msg || "";
  $("statusLine").style.color = isErr ? "var(--red)" : "";
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ── init ──────────────────────────────────────────────────────────────────
async function init() {
  state.status = await api("/api/status");
  const s = state.status;
  if (!s.claude) {
    $("claudeBtn").disabled = true;
    $("claudeBtn").title = "pip install anthropic (and set ANTHROPIC_API_KEY) to enable";
  }
  const missing = [];
  if (!s.ffmpeg) missing.push("ffmpeg");
  for (const [k, v] of Object.entries(s.fonts)) if (!v) missing.push(`font ${k}`);
  if (missing.length) setStatus("Missing: " + missing.join(", "), true);

  for (const [k, label] of Object.entries(s.presets)) $("presetSel").add(new Option(label, k));
  const grid = $("zoneGrid");
  for (const [v, h, sym] of ZONES) {
    const b = document.createElement("button");
    b.textContent = sym; b.dataset.zone = `${v}-${h}`; b.title = `${v} ${h}`;
    b.onclick = () => patch({ zone: b.dataset.zone });
    grid.appendChild(b);
  }
  await refreshProjectList();
  bindInspector();
  bindDrops();
  bindBgControls();
  window.addEventListener("resize", layoutStage);
  const p = new URLSearchParams(location.search).get("p");
  if (p) await openProject(p);
}

async function refreshProjectList() {
  const sel = $("projectSelect");
  const names = await api("/api/projects");
  sel.length = 1;
  for (const n of names) sel.add(new Option(n, n));
  sel.value = state.name || "";
}

$("projectSelect").onchange = (e) => e.target.value && openProject(e.target.value);

// ── parse ─────────────────────────────────────────────────────────────────
async function parse(mode) {
  const brief = $("briefInput").value;
  if (!brief.trim()) return setStatus("Paste a brief first", true);
  setStatus(mode === "claude" ? "Asking Claude…" : "Parsing…");
  try {
    const proj = await api("/api/projects", { json: { brief, mode, name: $("nameInput").value } });
    await openProject(proj.name);
    setStatus(`${proj.slides.length} slides – downloading media…`);
    await Promise.all(proj.slides.map((_, i) =>
      api(`/api/p/${state.name}/slide/${i}/fetch`, { method: "POST" }).then(applyView).catch(() => {})));
    setStatus("Ready");
  } catch (e) { setStatus(e.message, true); }
}
$("parseBtn").onclick = () => parse("rules");
$("claudeBtn").onclick = () => parse("claude");

async function openProject(name) {
  state.name = name;
  state.project = await api(`/api/p/${name}`);
  $("projectName").textContent = name;
  $("briefInput").value = state.project.brief || "";
  $("nameInput").value = name;
  $("exportBtn").disabled = false;
  $("openExportBtn").disabled = false;
  history.replaceState(null, "", `?p=${encodeURIComponent(name)}`);
  await refreshProjectList();
  renderStrip();
  await select(0);
}

// ── strip ─────────────────────────────────────────────────────────────────
function badge(s) {
  const m = s.media[s.media_sel];
  if (!m) return ["bad", "no media"];
  if (m.status === "pending") return ["warn", "loading…"];
  if (m.kind === "video" && !m.file) return [m.poster ? "warn" : "bad", "video missing"];
  if (m.status !== "ok") return ["bad", "drop file"];
  if (s.legibility && s.legibility.low) return ["warn", "check contrast"];
  return ["ok", m.kind === "video" ? "video ✓" : "ready"];
}

function renderStrip() {
  const strip = $("strip");
  strip.innerHTML = "";
  state.project.slides.forEach((s, i) => {
    const d = document.createElement("div");
    d.className = "thumb" + (i === state.sel ? " sel" : "");
    const [cls, txt] = badge(s);
    const v = state.thumbV[i] || 0;
    d.innerHTML = `<img src="/api/p/${state.name}/slide/${i}/thumb.jpg?v=${v}" alt="">
      <span class="num">${s.index}</span><span class="badge ${cls}">${txt}</span>`;
    d.onclick = () => select(i);
    strip.appendChild(d);
  });
}

// ── selection / view ──────────────────────────────────────────────────────
async function select(i) {
  state.sel = i;
  applyView(await api(`/api/p/${state.name}/slide/${i}/view`));
}

const refreshThumbs = debounce(() => {
  for (const i of state.dirtyThumbs) state.thumbV[i] = Date.now();
  state.dirtyThumbs.clear();
  renderStrip();
}, 1000);

function applyView(v) {
  // keep the crop the user is actively changing; the server copy may be a step behind
  if (state.view && v.index === state.view.index && Date.now() < (state.cropLocalUntil || 0))
    v.slide.crop = state.view.slide.crop;
  state.project.slides[v.index] = v.slide;
  state.dirtyThumbs.add(v.index);
  if (v.index === state.sel) {
    state.view = v;
    renderStage();
    renderInspector();
  }
  refreshThumbs();
}

function stageScale() { return $("stage").clientWidth / CW; }

function layoutStage() {
  $("canvas").style.transform = `scale(${stageScale()})`;
}

// same maths as render.crop_params, so the live preview matches the export
function cropParams(iw, ih, crop) {
  const zoom = Math.max(1, crop.zoom || 1);
  const s = Math.max(CW / iw, CH / ih) * zoom;
  let sw = Math.max(CW, Math.ceil(iw * s)), sh = Math.max(CH, Math.ceil(ih * s));
  sw += sw % 2; sh += sh % 2;
  const clamp = (v) => Math.min(1, Math.max(0, v));
  return [sw, sh, Math.round((sw - CW) * clamp(crop.fx)), Math.round((sh - CH) * clamp(crop.fy))];
}

function placeBg(local = true) {
  if (local) state.cropLocalUntil = Date.now() + 1500;
  const src = state.view && state.view.src, bg = $("bgImg");
  if (!src) return;
  const [sw, sh, x, y] = cropParams(src.w, src.h, state.view.slide.crop);
  Object.assign(bg.style, { width: sw + "px", height: sh + "px", left: -x + "px", top: -y + "px" });
}

function renderStage() {
  const v = state.view;
  const bg = $("bgImg");
  if (v.src) {
    if (bg.getAttribute("src") !== v.src.url) bg.src = v.src.url;
    bg.style.display = "block"; $("noMedia").style.display = "none";
    placeBg(false);
  } else { bg.style.display = "none"; $("noMedia").style.display = "flex"; }
  const layers = $("layers");
  layers.innerHTML = "";
  for (const e of v.elements) {
    const img = document.createElement("img");
    img.src = e.url; img.draggable = false;
    Object.assign(img.style, { left: e.x + "px", top: e.y + "px", width: e.w + "px", height: e.h + "px" });
    img.title = `Drag to move ${e.id}`;
    img.onpointerdown = (ev) => startDrag(ev, img, e);
    layers.appendChild(img);
  }
  renderShades();
  layoutStage();
}

// ── background pan (drag) and zoom (wheel) ────────────────────────────────
function bindBgControls() {
  const bg = $("bgImg");
  const saveCrop = debounce(() => patch({ crop: state.view.slide.crop }), 400);
  bg.onpointerdown = (ev) => {
    if (!state.view || !state.view.src) return;
    ev.preventDefault();
    bg.setPointerCapture(ev.pointerId);
    const sc = stageScale(), sx = ev.clientX, sy = ev.clientY;
    const c0 = { ...state.view.slide.crop };
    const [sw, sh] = cropParams(state.view.src.w, state.view.src.h, c0);
    const move = (e) => {
      const c = state.view.slide.crop;
      if (sw > CW) c.fx = Math.min(1, Math.max(0, c0.fx - (e.clientX - sx) / sc / (sw - CW)));
      if (sh > CH) c.fy = Math.min(1, Math.max(0, c0.fy - (e.clientY - sy) / sc / (sh - CH)));
      placeBg(); syncCropInputs();
    };
    bg.addEventListener("pointermove", move);
    bg.addEventListener("pointerup", () => {
      bg.removeEventListener("pointermove", move);
      patch({ crop: state.view.slide.crop });
    }, { once: true });
  };
  $("stage").addEventListener("wheel", (e) => {
    if (!state.view || !state.view.src) return;
    e.preventDefault();
    const c = state.view.slide.crop;
    c.zoom = Math.min(2.5, Math.max(1, (c.zoom || 1) * (e.deltaY < 0 ? 1.04 : 1 / 1.04)));
    placeBg(); syncCropInputs(); saveCrop();
  }, { passive: false });
}

function syncCropInputs() {
  const c = state.view.slide.crop;
  $("fxRange").value = c.fx; $("fyRange").value = c.fy; $("zoomRange").value = c.zoom;
}

// ── shade preview (same geometry/blur/opacity/blend as render.apply_shade) ──
function shadeOf(s) {
  const d = { on: false, opacity: 0.55, softness: 45, spread: 60, mode: "multiply", color: "#000000" };
  return typeof s.shade === "object" && s.shade ? { ...d, ...s.shade } : { ...d, on: !!s.shade };
}

function renderShades() {
  const box = $("shades");
  box.innerHTML = "";
  if (!state.view) return;
  const sh = shadeOf(state.view.slide);
  if (!sh.on) return;
  for (const e of state.view.elements) {
    if (e.id === "logo") continue;
    const d = document.createElement("div");
    d.dataset.id = e.id;
    const w = e.w + 2 * sh.spread, h = e.h + 2 * sh.spread;
    Object.assign(d.style, {
      left: e.x - sh.spread + "px", top: e.y - sh.spread + "px", width: w + "px", height: h + "px",
      borderRadius: Math.min(sh.spread, w / 2, h / 2) + "px", background: sh.color,
      opacity: sh.opacity, filter: sh.softness > 0 ? `blur(${sh.softness}px)` : "none",
      mixBlendMode: sh.mode === "multiply" ? "multiply" : "normal",
    });
    box.appendChild(d);
  }
}

function showShadeVals(sh) {
  $("shOpacityVal").textContent = Math.round(sh.opacity * 100) + "%";
  $("shSoftnessVal").textContent = sh.softness;
  $("shSpreadVal").textContent = sh.spread;
}

// ── dragging ──────────────────────────────────────────────────────────────
function startDrag(ev, img, el) {
  ev.preventDefault();
  img.setPointerCapture(ev.pointerId);
  img.classList.add("dragging");
  const sc = stageScale(), sx = ev.clientX, sy = ev.clientY;
  let dx = 0, dy = 0;
  const gv = $("guideV"), gh = $("guideH");
  const move = (e) => {
    dx = (e.clientX - sx) / sc; dy = (e.clientY - sy) / sc;
    gv.style.display = gh.style.display = "none";
    if (!e.altKey) {
      const xs = [el.x + dx, el.x + dx + el.w / 2, el.x + dx + el.w];
      snap: for (const t of SNAP_X) for (const x of xs)
        if (Math.abs(x - t) < SNAP_PX) { dx += t - x; gv.style.left = t * sc + "px"; gv.style.display = "block"; break snap; }
      const ys = [el.y + dy, el.y + dy + el.h / 2, el.y + dy + el.h];
      snapy: for (const t of SNAP_Y) for (const y of ys)
        if (Math.abs(y - t) < SNAP_PX) { dy += t - y; gh.style.top = t * sc + "px"; gh.style.display = "block"; break snapy; }
    }
    img.style.left = el.x + dx + "px"; img.style.top = el.y + dy + "px";
    const shd = $("shades").querySelector(`[data-id="${el.id}"]`);
    if (shd) {
      const sp = shadeOf(state.view.slide).spread;
      shd.style.left = el.x + dx - sp + "px"; shd.style.top = el.y + dy - sp + "px";
    }
  };
  const up = () => {
    img.removeEventListener("pointermove", move);
    img.classList.remove("dragging");
    gv.style.display = gh.style.display = "none";
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
    const cur = state.view.slide.offsets[el.id] || [0, 0];
    patch({ offsets: { [el.id]: [Math.round(cur[0] + dx), Math.round(cur[1] + dy)] } });
  };
  img.addEventListener("pointermove", move);
  img.addEventListener("pointerup", up, { once: true });
}

// ── inspector ─────────────────────────────────────────────────────────────
let patchSeq = 0;
async function patch(fields) {
  if (!state.name) return;
  const seq = ++patchSeq, idx = state.sel;
  try {
    const v = await api(`/api/p/${state.name}/slide/${idx}`, { json: fields });
    if (seq === patchSeq) applyView(v);        // drop responses overtaken by newer edits
    else state.project.slides[v.index] = v.slide;
  } catch (e) { setStatus(e.message, true); }
}

const patchSoon = debounce(patch, 180);

function renderInspector() {
  const s = state.view.slide;
  $("emptyInspector").hidden = true;
  $("insp").hidden = false;
  $("inspTitle").textContent = `Slide ${s.index}`;
  $("presetSel").value = s.preset;
  const isTitle = s.preset !== "body";
  $("titleFields").hidden = !isTitle;
  $("sublineWrap").hidden = s.preset !== "ontour";
  $("kickerWrap").hidden = s.preset !== "gettoknow";
  setVal("headlineInput", (s.headline || []).join("\n"));
  setVal("sublineInput", s.subline || "");
  setVal("kickerInput", s.kicker ?? ((s.headline || []).length > 1 ? s.headline[0] : ""));
  setVal("bodyInput", s.body || "");
  setVal("linesInput", (s.lines || []).join("\n"));
  $("linesMode").textContent = s.lines_auto ? "(auto-balanced – edit to override)" : "(manual)";
  $("sizeVal").textContent = Math.round(s.body_size);
  const sh = shadeOf(s);
  $("shadeChk").checked = sh.on;
  $("shadeControls").style.opacity = sh.on ? 1 : 0.45;
  setVal("shOpacity", sh.opacity); setVal("shSoftness", sh.softness); setVal("shSpread", sh.spread);
  setVal("shMode", sh.mode); setVal("shColor", sh.color);
  showShadeVals(sh);
  for (const b of $("zoneGrid").children) b.classList.toggle("on", b.dataset.zone === s.zone && !s.offsets.body);
  $("autoBtn").textContent = s.zone_auto ? "Auto-place ✓" : "Auto-place";
  const lg = s.legibility || {};
  $("legibility").innerHTML = lg.score == null ? "" :
    `<span class="${lg.low ? "badge warn" : "badge ok"}">${lg.low ? "⚠ busy/bright background – try another spot or soft shade"
      : "✓ good contrast"}</span>`;
  setVal("fxRange", s.crop.fx); setVal("fyRange", s.crop.fy); setVal("zoomRange", s.crop.zoom);
  renderMedia(s);
}

function setVal(id, v) {
  const el = $(id);
  if (document.activeElement !== el) el.value = v;
}

function mediaUrl(file) { return `/files/${state.name}/media/${encodeURIComponent(file)}`; }

function renderMedia(s) {
  const list = $("mediaList");
  list.innerHTML = "";
  s.media.forEach((m, i) => {
    const d = document.createElement("div");
    d.className = "media" + (i === s.media_sel ? " sel" : "");
    const pic = m.kind === "video" ? m.poster : m.file;
    const status = m.status === "ok" ? (m.kind === "video" ? "video ✓" : "image ✓")
      : m.status === "pending" ? "downloading…" : (m.error || "drop the file");
    d.innerHTML = `${pic ? `<img src="${mediaUrl(pic)}" alt="">` : `<img alt="">`}
      <div class="info"><div>${m.file || m.expected_name || m.url || ""}</div>
      <div class="${m.status === "ok" ? "badge ok" : "badge warn"}">${status}</div>
      ${m.expected_name && !m.file ? `<div class="dim">expects: ${m.expected_name}</div>` : ""}
      ${m.url && m.status !== "ok" ? `<div><a href="${m.url}" target="_blank" rel="noopener">open on Frame.io ↗</a></div>` : ""}</div>`;
    d.onclick = () => i !== s.media_sel && patch({ media_sel: i });
    list.appendChild(d);
  });
  if (!s.media.length) list.innerHTML = `<p class="dim">No media – drop or add a file.</p>`;
}

function bindInspector() {
  $("presetSel").onchange = (e) => patch({ preset: e.target.value });
  $("headlineInput").oninput = (e) => patchSoon({ headline: e.target.value });
  $("sublineInput").oninput = (e) => patchSoon({ subline: e.target.value });
  $("kickerInput").oninput = (e) => patchSoon({ kicker: e.target.value });
  $("bodyInput").oninput = (e) => patchSoon({ body: e.target.value });
  $("linesInput").oninput = (e) => patchSoon({ lines: e.target.value });
  $("rewrapBtn").onclick = () => patch({ lines_auto: true, n_lines: null });
  const lineCount = () => state.view.slide.lines.filter((l) => l).length;
  $("linesMinus").onclick = () => patch({ lines_auto: true, n_lines: Math.max(1, lineCount() - 1) });
  $("linesPlus").onclick = () => patch({ lines_auto: true, n_lines: lineCount() + 1 });
  $("sizeMinus").onclick = () => patch({ body_size: state.view.slide.body_size - 2 });
  $("sizePlus").onclick = () => patch({ body_size: state.view.slide.body_size + 2 });
  $("autoBtn").onclick = () => patch({ autoplace: true });
  const readShade = () => ({ on: $("shadeChk").checked, opacity: +$("shOpacity").value,
    softness: +$("shSoftness").value, spread: +$("shSpread").value,
    mode: $("shMode").value, color: $("shColor").value });
  const liveShade = () => {                       // instant preview, no request
    state.view.slide.shade = readShade();
    renderShades(); showShadeVals(state.view.slide.shade);
    $("shadeControls").style.opacity = state.view.slide.shade.on ? 1 : 0.45;
  };
  for (const id of ["shOpacity", "shSoftness", "shSpread", "shColor"]) {
    $(id).oninput = () => { $("shadeChk").checked = true; liveShade(); };
    $(id).onchange = () => patch({ shade: readShade() });
  }
  for (const id of ["shadeChk", "shMode"]) $(id).onchange = () => { liveShade(); patch({ shade: readShade() }); };
  $("frameioBtn").onclick = async () => {
    const r = await api(`/api/p/${state.name}/open_links`, { method: "POST" });
    setStatus(r.opened ? `Opened ${r.opened} link(s) – click Download on Frame.io; watching ${r.downloads}`
                       : "No Frame.io videos missing");
  };
  const readCrop = () => ({ fx: +$("fxRange").value, fy: +$("fyRange").value, zoom: +$("zoomRange").value });
  for (const id of ["fxRange", "fyRange", "zoomRange"]) {
    $(id).oninput = () => { state.view.slide.crop = readCrop(); placeBg(); };   // live, no request
    $(id).onchange = () => patch({ crop: readCrop() });                          // save on release
  }
  $("fileInput").onchange = (e) => {
    const s = state.view.slide, m = s.media[s.media_sel];
    upload(e.target.files, state.sel, m && m.status !== "ok" ? s.media_sel : null);
    e.target.value = "";
  };
  $("refetchBtn").onclick = async () => {
    setStatus("Downloading…");
    applyView(await api(`/api/p/${state.name}/slide/${state.sel}/fetch`, { method: "POST" }));
    setStatus("");
  };
  $("exportBtn").onclick = exportAll;
  $("openExportBtn").onclick = () => api(`/api/p/${state.name}/open_export`, { method: "POST" });
}

// ── uploads ───────────────────────────────────────────────────────────────
async function upload(files, slide, mediaIdx) {
  if (!state.name || !files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  if (slide != null) fd.append("slide", slide);
  if (mediaIdx != null) fd.append("media", mediaIdx);
  setStatus(`Uploading ${files.length} file(s)…`);
  try {
    const r = await api(`/api/p/${state.name}/upload`, { method: "POST", body: fd });
    state.project = r.project;
    r.changed.forEach((i) => (state.thumbV[i] = Date.now()));
    setStatus(r.unmatched.length ? `Not matched: ${r.unmatched.join(", ")} – drop them on the slide itself` : "Media added",
              r.unmatched.length > 0);
    await select(r.changed.includes(state.sel) || !r.changed.length ? state.sel : r.changed[0]);
  } catch (e) { setStatus(e.message, true); }
}

function bindDrops() {
  let depth = 0;
  document.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; document.body.classList.add("dragover"); });
  document.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; document.body.classList.remove("dragover"); } });
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => {
    e.preventDefault(); depth = 0; document.body.classList.remove("dragover");
    if (!state.name) return setStatus("Parse a brief first", true);
    const onStage = $("stage").contains(e.target);
    const s = state.view && state.view.slide;
    const m = s && s.media[s.media_sel];
    upload(e.dataTransfer.files, onStage ? state.sel : null,
           onStage && m && m.status !== "ok" ? s.media_sel : null);
  });
}

// ── export ────────────────────────────────────────────────────────────────
async function exportAll() {
  $("exportBtn").disabled = true;
  try {
    await api(`/api/p/${state.name}/export`, { method: "POST" });
    for (;;) {
      await new Promise((r) => setTimeout(r, 1000));
      const st = await api(`/api/p/${state.name}/export`);
      $("exportLog").textContent = st.log.join("\n");
      if (!st.running) {
        setStatus(st.error ? "Export failed" : `Exported ${st.files.length} files`, !!st.error);
        break;
      }
    }
  } catch (e) { setStatus(e.message, true); }
  $("exportBtn").disabled = false;
}

// ── pick up files that arrive via the Downloads watcher ───────────────────
let lastMediaState = null;
setInterval(async () => {
  if (!state.name || document.hidden) return;
  try {
    const r = await api(`/api/p/${state.name}/media_state`);
    const sig = JSON.stringify(r.state);
    if (lastMediaState !== null && sig !== lastMediaState) {
      state.project = await api(`/api/p/${state.name}`);
      state.project.slides.forEach((_, i) => state.dirtyThumbs.add(i));
      await select(state.sel);
      setStatus("Media updated");
    }
    lastMediaState = sig;
  } catch { /* server restarting */ }
}, 2500);

init().catch((e) => setStatus(e.message, true));
