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
  updateAiButtons(s.ai);
  const missing = [];
  if (!s.ffmpeg) missing.push("ffmpeg");
  for (const [k, v] of Object.entries(s.fonts)) if (!v) missing.push(`font ${k}`);
  if (missing.length) setStatus("Missing: " + missing.join(", "), true);

  fillPresets(s.presets);
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
  bindVideo();
  $("presetFile").onchange = (e) => { if (e.target.files[0]) importPreset(e.target.files[0]); e.target.value = ""; };
  window.addEventListener("resize", layoutStage);
  const p = new URLSearchParams(location.search).get("p");
  if (p) await openProject(p);
}

function fillPresets(presets) {
  state.presets = presets;
  const sel = $("presetSel"), cur = sel.value;
  sel.length = 0;
  for (const [k, p] of Object.entries(presets)) sel.add(new Option(p.label, k));
  if (cur) sel.value = cur;
}

function slideLabel(s) {
  return s.alt ? `${s.index} alt${s.alt > 1 ? " " + s.alt : ""}` : `${s.index}`;
}

// ── import a title-slide design as a preset ───────────────────────────────
async function importPreset(file) {
  const name = prompt("Name for this title preset (used in briefs as “Typo wie NAME”):",
                      file.name.replace(/\.(ai|pdf)$/i, ""));
  if (!name) return;
  const send = async (page) => {
    const fd = new FormData();
    fd.append("file", file); fd.append("name", name);
    if (page) fd.append("page", page);
    return api("/api/presets/import", { method: "POST", body: fd });
  };
  setStatus(`Analysing ${file.name}…`);
  try {
    let r = await send();
    if (r.pages) {
      const page = prompt(`The file has ${r.pages} artboards. Which one is the title slide?`, "1");
      if (!page) return setStatus("");
      setStatus(`Analysing artboard ${page}…`);
      r = await send(page);
    }
    fillPresets(r.presets);
    const slots = Object.entries(r.slots).map(([id, sp]) => `${id}: ${sp.font} ${sp.size}pt ${sp.align}`);
    $("exportLog").textContent = [`Imported preset “${r.label}”`, ...slots,
      r.graphics ? "graphics layer: yes" : "graphics layer: none", ...r.warnings.map((w) => "⚠ " + w)].join("\n");
    setStatus(`Preset “${r.label}” added – pick it under Preset${r.warnings.length ? " (see warnings)" : ""}`,
              r.warnings.length > 0);
  } catch (e) { setStatus(e.message, true); }
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
  const api_ = state.ai && state.ai.providers[state.ai.active];
  setStatus(mode === "ai" ? `Asking the ${api_ ? api_.label : "AI"}…` : "Parsing…");
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
$("aiBtn").onclick = () => parse("ai");

async function openProject(name) {
  state.name = name;
  state.project = await api(`/api/p/${name}`);
  $("projectName").textContent = name;
  $("briefInput").value = state.project.brief || "";
  $("nameInput").value = name;
  $("exportBtn").disabled = false;
  $("openExportBtn").disabled = false;
  $("exportOpts").hidden = true;
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
      <span class="num">${s.alt ? `${s.index}<small> alt${s.alt > 1 ? s.alt : ""}</small>` : s.index}</span><span class="badge ${cls}">${txt}</span>`;
    d.onclick = () => select(i);
    strip.appendChild(d);
  });
}

// ── selection / view ──────────────────────────────────────────────────────
async function select(i) {
  if (i !== state.sel) $("bgVideo").pause();
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
  const v = state.view;
  if (!v) return;
  const place = (el, dims) => {
    if (!dims || !dims.w) return;
    const [sw, sh, x, y] = cropParams(dims.w, dims.h, v.slide.crop);
    Object.assign(el.style, { width: sw + "px", height: sh + "px", left: -x + "px", top: -y + "px" });
  };
  place($("bgImg"), v.src);
  place($("bgVideo"), v.video && v.video.url ? v.video : null);
}

function bgDims() {
  const v = state.view;
  return v.video && v.video.url ? v.video : v.src;
}

function renderStage() {
  const v = state.view;
  const bg = $("bgImg"), vid = $("bgVideo");
  const hasVideo = !!(v.video && v.video.url);
  if (v.src) {
    if (bg.getAttribute("src") !== v.src.url) bg.src = v.src.url;
    $("noMedia").style.display = "none";
  } else if (!hasVideo) { $("noMedia").style.display = "flex"; }
  if (hasVideo) {
    if (vid.getAttribute("src") !== v.video.url) {
      vid.src = v.video.url;
      vid.currentTime = 0;
      updateVideoBar();
    }
    vid.style.display = "block";
  } else {
    if (vid.getAttribute("src")) { vid.pause(); vid.removeAttribute("src"); vid.load(); }
    vid.style.display = "none";
  }
  bg.style.display = v.src && !hasVideo ? "block" : "none";
  $("videoBar").hidden = !hasVideo;
  $("vidPending").hidden = !(v.video && v.video.pending);
  placeBg(false);
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
  const saveCrop = debounce(() => patch({ crop: state.view.slide.crop }), 400);
  for (const bg of [$("bgImg"), $("bgVideo")]) bg.onpointerdown = (ev) => {
    if (!state.view || !bgDims()) return;
    ev.preventDefault();
    bg.setPointerCapture(ev.pointerId);
    const sc = stageScale(), sx = ev.clientX, sy = ev.clientY;
    const c0 = { ...state.view.slide.crop };
    const [sw, sh] = cropParams(bgDims().w, bgDims().h, c0);
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
    if (!state.view || !bgDims() || e.target.closest("#videoBar")) return;
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

// ── video preview ─────────────────────────────────────────────────────────
function fmtTime(t) {
  t = Math.max(0, t || 0);
  return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
}

function updateVideoBar() {
  const vid = $("bgVideo");
  $("vidPlay").textContent = vid.paused ? "▶" : "❚❚";
  $("vidMute").textContent = vid.muted ? "🔇" : "🔊";
  if (document.activeElement !== $("vidSeek"))
    $("vidSeek").value = vid.duration ? Math.round((vid.currentTime / vid.duration) * 1000) : 0;
  $("vidTime").textContent = `${fmtTime(vid.currentTime)} / ${fmtTime(vid.duration)}`;
}

function bindVideo() {
  const vid = $("bgVideo");
  const toggle = () => (vid.paused ? vid.play() : vid.pause());
  $("vidPlay").onclick = toggle;
  $("vidMute").onclick = () => { vid.muted = !vid.muted; updateVideoBar(); };
  $("vidSeek").oninput = () => { if (vid.duration) vid.currentTime = ($("vidSeek").value / 1000) * vid.duration; };
  for (const ev of ["play", "pause", "timeupdate", "loadedmetadata", "volumechange"])
    vid.addEventListener(ev, updateVideoBar);
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !$("videoBar").hidden &&
        !e.target.closest("input:not([type=range]), textarea, select, button")) {
      e.preventDefault(); toggle();
    }
  });
}

// ── shade preview (same geometry/blur/opacity/blend as render.apply_shade) ──
function shadeOf(s) {
  const d = { on: false, opacity: 0.55, softness: 45, spread: 60, mode: "multiply", color: "#000000",
              shape: "box" };
  return typeof s.shade === "object" && s.shade ? { ...d, ...s.shade } : { ...d, on: !!s.shade };
}

const LINEAR_FADE = 3;   // same as render.LINEAR_FADE

// render.linear_profile(): solid over the text, linear fade; rises from the bottom edge for
// low text, hangs from the top for high text, band in the middle. Returns CSS gradient stops.
function linearStops(rects, softness) {
  const fade = Math.max(1, softness * LINEAR_FADE);
  const at = (y) => Math.max(0, ...rects.map(([, y0, , y1]) => {
    const c = (y0 + y1) / 2, lo = c < CH * 0.4 ? 0 : y0, hi = c > CH * 0.6 ? CH : y1;
    return y >= lo && y <= hi ? 1 : y < lo ? Math.max(0, 1 - (lo - y) / fade) : Math.max(0, 1 - (y - hi) / fade);
  }));
  // the profile is piecewise linear: sample at every corner so the CSS ramp is exact
  const ys = new Set([0, CH]);
  for (const [, y0, , y1] of rects) for (const y of [y0, y1, y0 - fade, y1 + fade]) ys.add(Math.min(CH, Math.max(0, y)));
  for (let y = 0; y <= CH; y += 40) ys.add(y);               // overlaps between blocks
  return [...ys].sort((a, b) => a - b).map((y) => `rgba(0,0,0,${at(y).toFixed(4)}) ${y}px`).join(", ");
}

function logoShadeOf(s) {
  const d = { on: false, opacity: 0.5, softness: 60, spread: 30, mode: "multiply", color: "#000000" };
  return { ...d, ...(s.logo_shade || {}), shape: "linear" };
}

// one full-width div whose CSS mask is the server's linear profile (render.linear_profile)
function linearShadeDiv(sh, rects) {
  const d = document.createElement("div");
  const grad = `linear-gradient(to bottom, ${linearStops(rects, sh.softness)})`;
  Object.assign(d.style, {
    inset: "0", background: sh.color, opacity: sh.opacity,
    webkitMaskImage: grad, maskImage: grad,
    mixBlendMode: sh.mode === "multiply" ? "multiply" : "normal",
  });
  return d;
}

const grow = (e, sp) => [e.x - sp, e.y - sp, e.x + e.w + sp, e.y + e.h + sp];

function renderShades(moved) {
  const box = $("shades");
  box.innerHTML = "";
  if (!state.view) return;
  const all = state.view.elements
    .map((e) => (moved && moved.id === e.id ? { ...e, x: e.x + moved.dx, y: e.y + moved.dy } : e));
  const isGraphic = (e) => e.id === "logo" || e.id === "graphic";
  const layers = [];                                  // [settings, build()] like render.shade_layers
  const sh = shadeOf(state.view.slide);
  const texts = all.filter((e) => !isGraphic(e));
  if (sh.on && texts.length) layers.push([sh, () => {
    if (sh.shape === "linear") return [linearShadeDiv(sh, texts.map((e) => grow(e, sh.spread)))];
    return texts.map((e) => {
      const d = document.createElement("div");
      d.dataset.id = e.id;
      const w = e.w + 2 * sh.spread, h = e.h + 2 * sh.spread;
      Object.assign(d.style, {
        left: e.x - sh.spread + "px", top: e.y - sh.spread + "px", width: w + "px", height: h + "px",
        borderRadius: Math.min(sh.spread, w / 2, h / 2) + "px", background: sh.color,
        opacity: sh.opacity, filter: sh.softness > 0 ? `blur(${sh.softness}px)` : "none",
        mixBlendMode: sh.mode === "multiply" ? "multiply" : "normal",
      });
      return d;
    });
  }]);
  const ls = logoShadeOf(state.view.slide);
  const logos = all.filter(isGraphic);
  if (ls.on && logos.length) layers.push([ls, () => [linearShadeDiv(ls, logos.map((e) => grow(e, ls.spread)))]]);
  // multiply layers first, normal after – the order the export applies them in
  layers.sort((a, b) => (a[0].mode !== "multiply") - (b[0].mode !== "multiply"));
  for (const [, build] of layers) for (const d of build()) box.appendChild(d);
}

// imported presets: one field per text block of the design
function renderSlotFields(s, preset) {
  const box = $("slotFields"), thumb = $("presetThumb");
  box.hidden = !preset.template;
  thumb.hidden = !preset.template;
  if (!preset.template) { box.innerHTML = ""; return; }
  thumb.src = `/api/presets/${s.preset}/thumb.jpg`;
  if (box.dataset.preset !== s.preset || box.dataset.slide !== String(state.sel)) {
    box.innerHTML = "";
    for (const sl of preset.slots) {
      const lab = document.createElement("label");
      lab.innerHTML = `${sl.role[0].toUpperCase() + sl.role.slice(1)} <span class="dim">(${sl.id})</span>`;
      const ta = document.createElement("textarea");
      ta.rows = Math.min(4, Math.max(sl.role === "headline" ? 2 : 1, sl.sample.split("\n").length));
      ta.placeholder = sl.sample;
      ta.id = "slot_" + sl.id;
      ta.oninput = () => patchSoon({ slots: { [sl.id]: ta.value } });
      lab.appendChild(ta);
      box.appendChild(lab);
    }
    box.dataset.preset = s.preset;
    box.dataset.slide = String(state.sel);
  }
  for (const sl of preset.slots) {
    const v = (s.slots || {})[sl.id];
    setVal("slot_" + sl.id, v ?? sl.sample);
  }
}

function showLogoShadeVals(ls) {
  $("lsOpacityVal").textContent = Math.round(ls.opacity * 100) + "%";
  $("lsSoftnessVal").textContent = ls.softness;
  $("lsSpreadVal").textContent = ls.spread;
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
    const sh = shadeOf(state.view.slide);
    if ((sh.on && sh.shape === "linear") || (logoShadeOf(state.view.slide).on && (el.id === "logo" || el.id === "graphic")))
      renderShades({ id: el.id, dx, dy });
    const shd = $("shades").querySelector(`[data-id="${el.id}"]`);
    if (shd) {
      shd.style.left = el.x + dx - sh.spread + "px"; shd.style.top = el.y + dy - sh.spread + "px";
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
  $("inspTitle").textContent = `Slide ${slideLabel(s)}`;
  $("footerSlide").textContent = `Slide ${slideLabel(s)}`;
  $("presetSel").value = s.preset;
  const preset = (state.presets || {})[s.preset] || {};
  const isTitle = s.preset !== "body" && !preset.template;
  $("titleFields").hidden = !isTitle;
  renderSlotFields(s, preset);
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
  setVal("shMode", sh.mode); setVal("shColor", sh.color); setVal("shShape", sh.shape);
  showShadeVals(sh);
  const ls = logoShadeOf(s);
  $("logoShadeSection").hidden = !state.view.elements.some((e) => e.id === "logo" || e.id === "graphic");
  $("lsChk").checked = ls.on;
  $("lsControls").style.opacity = ls.on ? 1 : 0.45;
  setVal("lsOpacity", ls.opacity); setVal("lsSoftness", ls.softness); setVal("lsSpread", ls.spread);
  setVal("lsMode", ls.mode); setVal("lsColor", ls.color);
  showLogoShadeVals(ls);
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
      : m.status === "pending" ? "downloading…"
      : m.url && m.url.includes("frame.io") ? "click Download on Frame.io – picked up from Downloads"
      : (m.error || "drop the file");
    d.innerHTML = `${pic ? `<img src="${mediaUrl(pic)}" alt="">` : `<img alt="">`}
      <div class="info"><div>${m.file || m.expected_name || m.url || ""}</div>
      <div class="${m.status === "ok" ? "badge ok" : "badge warn"}">${status}</div>
      ${m.expected_name && !m.file ? `<div class="dim">expects: ${m.expected_name}</div>` : ""}
      ${m.url && m.status !== "ok" ? `<div><a href="${m.url}" target="_blank" rel="noopener">open on Frame.io ↗</a></div>` : ""}</div>`;
    // braces matter: returning false from onclick would cancel the Frame.io link click
    d.onclick = (e) => { if (!e.target.closest("a") && i !== s.media_sel) patch({ media_sel: i }); };
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
    mode: $("shMode").value, color: $("shColor").value, shape: $("shShape").value });
  const liveShade = () => {                       // instant preview, no request
    state.view.slide.shade = readShade();
    renderShades(); showShadeVals(state.view.slide.shade);
    $("shadeControls").style.opacity = state.view.slide.shade.on ? 1 : 0.45;
  };
  for (const id of ["shOpacity", "shSoftness", "shSpread", "shColor"]) {
    $(id).oninput = () => { $("shadeChk").checked = true; liveShade(); };
    $(id).onchange = () => patch({ shade: readShade() });
  }
  const readLogoShade = () => ({ on: $("lsChk").checked, opacity: +$("lsOpacity").value,
    softness: +$("lsSoftness").value, spread: +$("lsSpread").value,
    mode: $("lsMode").value, color: $("lsColor").value });
  const liveLogoShade = () => {
    state.view.slide.logo_shade = readLogoShade();
    renderShades(); showLogoShadeVals(state.view.slide.logo_shade);
    $("lsControls").style.opacity = state.view.slide.logo_shade.on ? 1 : 0.45;
  };
  for (const id of ["lsOpacity", "lsSoftness", "lsSpread", "lsColor"]) {
    $(id).oninput = () => { $("lsChk").checked = true; liveLogoShade(); };
    $(id).onchange = () => patch({ logo_shade: readLogoShade() });
  }
  for (const id of ["lsChk", "lsMode"]) $(id).onchange = () => { liveLogoShade(); patch({ logo_shade: readLogoShade() }); };
  for (const id of ["shadeChk", "shMode", "shShape"]) $(id).onchange = () => { liveShade(); patch({ shade: readShade() }); };
  $("frameioBtn").onclick = async () => {
    const urls = [...new Set(state.project.slides.flatMap((s) => s.media)
      .filter((m) => m.url && m.status !== "ok" && m.expected_name).map((m) => m.url))];
    if (!urls.length) return setStatus("No Frame.io videos missing");
    // browsers allow one popup per click; the server opens whatever was blocked
    const blocked = urls.filter((u) => !window.open(u, "_blank"));
    try {
      const r = await api(`/api/p/${state.name}/open_links`, { json: { open: blocked } });
      setStatus(`Opened ${urls.length} Frame.io link(s) – click Download there; watching ${r.downloads}`);
    } catch (e) { setStatus(e.message, true); }
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
  $("exportBtn").onclick = () => exportAll();
  $("exportSlideBtn").onclick = saveSlide;
  bindExportOptions();
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
const EXPORT_BTNS = ["exportBtn", "exportSlideBtn"];

// ── export options menu ───────────────────────────────────────────────────
function showExportOptions(o) {
  $("exportFolder").textContent = o.resolved + (o.folder ? "" : "  (default)");
  $("projectFolder").textContent = o.project_dir;
  $("optPng").checked = o.png; $("optJpg").checked = o.jpg;
  $("optPdf").checked = o.pdf; $("optAi").checked = o.ai;
}

// ── AI API keys (app-wide, kept on this computer) ─────────────────────────
const AI = ["claude", "deepseek"];

// ai = {active, providers: {claude: status, deepseek: status}}
function updateAiButtons(ai) {
  const c = ai.providers[ai.active], btn = $("aiBtn");
  state.ai = ai;
  btn.disabled = !(c.installed && c.source);
  btn.title = !c.installed ? `Run: pip install ${c.package}`
    : !c.source ? `Add your ${c.label} key in the ⚙ menu (or switch the API there)`
    : `Parse the brief with the ${c.label} – change it in the ⚙ menu`;
}

function showAiStatus(ai) {
  for (const r of document.querySelectorAll("input[name=aiProvider]")) r.checked = r.value === ai.active;
  for (const p of AI) {
    const c = ai.providers[p];
    $("keyStatus_" + p).textContent = !c.installed ? `– package missing: pip install ${c.package}`
      : c.source === "settings" ? `– key saved (${c.hint})`
      : c.source === "environment" ? `– using ${c.env} from the environment`
      : "– no key yet";
    $("keyRemove_" + p).disabled = c.source !== "settings";
  }
  updateAiButtons(ai);
}

async function setAiProvider(provider) {
  try { showAiStatus((await api("/api/settings/provider", { json: { provider } })).ai); }
  catch (e) { setStatus(e.message, true); }
}

async function saveApiKey(provider, key) {
  const label = provider === "claude" ? "Anthropic API" : "DeepSeek API";
  setStatus(key ? `Checking the key with ${label}…` : "Removing key…");
  try {
    const r = await api("/api/settings/key", { json: { provider, key } });
    showAiStatus(r.ai);
    $("keyInput_" + provider).value = "";
    setStatus(r.warning || `${label} API key ${key ? "saved" : "removed"}`, !!r.warning);
  } catch (e) { setStatus(e.message, true); }
}

function bindExportOptions() {
  const url = () => `/api/p/${state.name}/export_options`;
  $("exportOptsBtn").onclick = async (e) => {
    e.stopPropagation();
    const box = $("exportOpts");
    if (box.hidden) {
      $("projectExportOpts").hidden = !state.name;
      $("noProjectOpts").hidden = !!state.name;
      if (state.name) showExportOptions(await api(url()));
      showAiStatus((await api("/api/settings")).ai);
    }
    box.hidden = !box.hidden;
  };
  for (const p of AI) {
    $("keySave_" + p).onclick = () => {
      const key = $("keyInput_" + p).value.trim();
      if (!key) return setStatus("Paste a key first", true);
      saveApiKey(p, key);
    };
    $("keyInput_" + p).onkeydown = (e) => { if (e.key === "Enter") $("keySave_" + p).click(); };
    $("keyRemove_" + p).onclick = () => saveApiKey(p, "");
  }
  for (const r of document.querySelectorAll("input[name=aiProvider]")) r.onchange = () => setAiProvider(r.value);
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#exportOptsWrap")) $("exportOpts").hidden = true;
  });
  const save = async (body) => {
    try { showExportOptions(await api(url(), { json: body })); } catch (err) { setStatus(err.message, true); }
  };
  $("pickFolderBtn").onclick = () => { setStatus("Choose a folder in the dialog…"); save({ pick_folder: true }).then(() => setStatus("")); };
  $("defaultFolderBtn").onclick = () => save({ folder: null });
  for (const [id, key] of [["optPng", "png"], ["optJpg", "jpg"], ["optPdf", "pdf"], ["optAi", "ai"]])
    $(id).onchange = () => save({ [key]: $(id).checked });
}

// ── single slide: native "Save as" dialog on this computer ─────────────────
async function saveSlide() {
  for (const id of EXPORT_BTNS) $(id).disabled = true;
  setStatus("Choose where to save it in the dialog…");
  try {
    const r = await api(`/api/p/${state.name}/slide/${state.sel}/save_png`, { method: "POST" });
    setStatus(r.cancelled ? "Not saved" : `Saved → ${r.saved}`);
  } catch (e) { setStatus(e.message, true); }
  for (const id of EXPORT_BTNS) $(id).disabled = false;
}

async function exportAll() {
  for (const id of EXPORT_BTNS) $(id).disabled = true;
  $("exportOpts").hidden = true;
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
  for (const id of EXPORT_BTNS) $(id).disabled = false;
}

// ── pick up files that arrive via the Downloads watcher ───────────────────
let lastMediaState = null;
setInterval(async () => {
  if (!state.name || document.hidden) return;
  try {
    const r = await api(`/api/p/${state.name}/media_state`);
    const sig = JSON.stringify(r.state);
    const previewPending = state.view && state.view.video && state.view.video.pending;
    if ((lastMediaState !== null && sig !== lastMediaState) || previewPending) {
      state.project = await api(`/api/p/${state.name}`);
      state.project.slides.forEach((_, i) => state.dirtyThumbs.add(i));
      await select(state.sel);
      setStatus("Media updated");
    }
    lastMediaState = sig;
  } catch { /* server restarting */ }
}, 2500);

init().catch((e) => setStatus(e.message, true));
