// sqrtl web UI: edit design.sql / stim.sql on the left, read the SystemVerilog
// on the right, watch the waveform below. The transpiler is the real Python
// package running under Pyodide; the simulator is the real PostgreSQL running
// under PGlite. Nothing leaves the browser.

import { EditorView, basicSetup } from "codemirror";
import { EditorState, Compartment } from "@codemirror/state";
import { keymap } from "@codemirror/view";
import { indentWithTab } from "@codemirror/commands";
import { sql, PostgreSQL } from "@codemirror/lang-sql";
import { StreamLanguage } from "@codemirror/language";
import { verilog } from "@codemirror/legacy-modes/mode/verilog";
import { setDiagnostics, lintGutter } from "@codemirror/lint";
import { oneDark } from "@codemirror/theme-one-dark";
import wavedrom from "wavedrom";
import { WAVE_SKIN_CSS } from "./waveskin.js";

const $ = (id) => document.getElementById(id);
const STORAGE_KEY = "sqrtl.v1";
const SCHEMA = "sqrtl_web";

// ---------------------------------------------------------------------------
// Status / toast

const statusEl = $("status");
const statusText = $("status-text");
function setStatus(state, text) {
  statusEl.dataset.state = state;
  statusText.textContent = text;
  statusEl.title = text;
}

let toastTimer;
function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 1600);
}

// ---------------------------------------------------------------------------
// Editors

const readOnly = new Compartment();
const baseTheme = EditorView.theme({
  "&": { backgroundColor: "transparent" },
  ".cm-gutters": { backgroundColor: "transparent" },
});

function makeEditor(parent, doc, language, { editable = true, onChange } = {}) {
  const extensions = [
    basicSetup,
    keymap.of([indentWithTab]),
    oneDark,
    baseTheme,
    language,
    EditorView.lineWrapping,
  ];
  if (editable) {
    extensions.push(lintGutter());
    extensions.push(EditorView.updateListener.of((u) => u.docChanged && onChange && onChange()));
  } else {
    extensions.push(readOnly.of([EditorState.readOnly.of(true), EditorView.editable.of(false)]));
  }
  return new EditorView({ state: EditorState.create({ doc, extensions }), parent });
}

const sqlLang = sql({ dialect: PostgreSQL, upperCaseKeywords: true });
const verilogLang = StreamLanguage.define(verilog);

const designView = makeEditor($("editor-design"), "", sqlLang, { onChange: () => scheduleRun("design") });
const stimView = makeEditor($("editor-stim"), "", sqlLang, { onChange: () => scheduleRun("stim") });
const verilogView = makeEditor($("editor-verilog"), "", verilogLang, { editable: false });

function setDoc(view, text) {
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } });
}
const getDoc = (view) => view.state.doc.toString();

function setError(view, err) {
  // err: {message, line, col} or null
  let diagnostics = [];
  if (err && err.line) {
    const doc = view.state.doc;
    const lineNo = Math.min(Math.max(err.line, 1), doc.lines);
    const line = doc.line(lineNo);
    const from = Math.min(line.from + Math.max((err.col || 1) - 1, 0), line.to);
    const rest = doc.sliceString(from, line.to);
    const m = /^[A-Za-z0-9_.'"]+/.exec(rest);
    const to = Math.max(from + (m ? m[0].length : 1), from + 1);
    diagnostics = [{ from, to: Math.min(to, line.to || to), severity: "error", message: err.message }];
  }
  view.dispatch(setDiagnostics(view.state, diagnostics));
}

// Tabs
const tabs = [...document.querySelectorAll(".tab")];
function showTab(name) {
  for (const t of tabs) t.setAttribute("aria-selected", String(t.dataset.tab === name));
  $("editor-design").classList.toggle("hidden", name !== "design");
  $("editor-stim").classList.toggle("hidden", name !== "stim");
  (name === "design" ? designView : stimView).focus();
}
for (const t of tabs) t.addEventListener("click", () => showTab(t.dataset.tab));

// ---------------------------------------------------------------------------
// Examples + persistence

let examples = [];
const exampleSelect = $("example");

function currentExample() {
  return examples.find((e) => e.name === exampleSelect.value) || examples[0];
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      example: exampleSelect.value, design: getDoc(designView), stim: getDoc(stimView),
    }));
  } catch { /* private mode etc. */ }
}

function restore() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function loadExample(ex, { design, stim } = {}) {
  exampleSelect.value = ex.name;
  setDoc(designView, design ?? ex.design);
  setDoc(stimView, stim ?? ex.stim);
  setError(designView, null);
  setError(stimView, null);
  showTab("design");
}

exampleSelect.addEventListener("change", () => loadExample(currentExample()));
$("reset").addEventListener("click", () => { loadExample(currentExample()); toast("Example reloaded"); });

// ---------------------------------------------------------------------------
// Copy / download

$("copy").addEventListener("click", async () => {
  const text = getDoc(verilogView);
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    toast("SystemVerilog copied");
  } catch {
    // Fallback: select all in the read-only editor.
    verilogView.dispatch({ selection: { anchor: 0, head: verilogView.state.doc.length } });
    verilogView.focus();
    toast("Select-all applied; press Ctrl/Cmd+C");
  }
});

function showVerilog(meta) {
  if (getDoc(verilogView) !== meta.systemverilog) setDoc(verilogView, meta.systemverilog);
  $("verilog-name").textContent = `${meta.name}.sv`;
}

$("download").addEventListener("click", () => {
  const text = getDoc(verilogView);
  if (!text) return;
  const name = (lastMeta && lastMeta.name) || "design";
  const blob = new Blob([text], { type: "text/plain" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${name}.sv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});

// ---------------------------------------------------------------------------
// Backends: Pyodide (transpiler) and PGlite (simulator)

let analyzeJson = null; // (sql: string) => JSON string
let pg = null;

async function loadPython() {
  setStatus("loading", "Loading Python runtime…");
  const indexURL = new URL("vendor/pyodide/", document.baseURI).href;
  const pyodide = await loadPyodide({ indexURL });
  const manifest = await (await fetch("py/manifest.json")).json();
  setStatus("loading", "Installing sqlglot…");
  const wheel = await (await fetch(`py/${manifest.wheel}`)).arrayBuffer();
  pyodide.unpackArchive(wheel, "wheel");
  pyodide.FS.mkdirTree("/home/pyodide/sqrtl");
  for (const f of manifest.files) {
    const text = await (await fetch(`py/sqrtl/${f}`)).text();
    pyodide.FS.writeFile(`/home/pyodide/sqrtl/${f}`, text);
  }
  analyzeJson = pyodide.runPython("from sqrtl.web import analyze_json\nanalyze_json");
}

async function loadPostgres() {
  const mod = await import(new URL("vendor/pglite/index.js", document.baseURI).href);
  pg = await mod.PGlite.create();
}

// ---------------------------------------------------------------------------
// The pipeline

let timer = null;
let runId = 0;
let lastMeta = null;

function scheduleRun(source) {
  if (source) tabs.find((t) => t.dataset.tab === source)?.classList.remove("has-error");
  clearTimeout(timer);
  timer = setTimeout(run, 300);
  persist();
}

async function run() {
  const id = ++runId;
  const design = getDoc(designView);
  const stim = getDoc(stimView);
  if (!analyzeJson || !pg) return; // boot() reruns once ready

  setStatus("busy", "Transpiling…");
  const result = JSON.parse(analyzeJson(design));
  if (id !== runId) return;

  if (!result.ok) {
    const { message, line, col } = result.error;
    setError(designView, result.error);
    tabs[0].classList.add("has-error");
    $("sql-note").textContent = `design.sql:${line}:${col}`;
    $("sql-note").classList.add("error");
    $("verilog-name").textContent = "stale";
    setStatus("error", `design.sql:${line}:${col}: ${message}`);
    showWaveError(`design.sql:${line}:${col}: ${message}`);
    return;
  }

  setError(designView, null);
  tabs[0].classList.remove("has-error");
  $("sql-note").textContent = "";
  $("sql-note").classList.remove("error");
  lastMeta = result;
  showVerilog(result);

  setStatus("busy", "Simulating in PostgreSQL…");
  try {
    const { stimRows, traceRows } = await simulate(result, design, stim);
    if (id !== runId) return;
    setError(stimView, null);
    tabs[1].classList.remove("has-error");
    renderWave(result, stimRows, traceRows);
    const cycles = traceRows.length;
    $("wave-note").textContent = `${cycles} cycles`;
    setStatus("ready", `${result.name}: ${cycles} cycles simulated`);
  } catch (e) {
    if (id !== runId) return;
    const msg = (e && e.message) || String(e);
    const isStim = /stimulus|_in\b|INSERT|violates|duplicate key|invalid input syntax/i.test(msg);
    if (isStim) tabs[1].classList.add("has-error");
    setStatus("error", msg);
    showWaveError(msg);
  }
}

async function simulate(meta, design, stim) {
  await pg.exec(`DROP SCHEMA IF EXISTS ${SCHEMA} CASCADE; CREATE SCHEMA ${SCHEMA}; SET search_path TO ${SCHEMA};`);
  try {
    await pg.exec(design);
  } catch (e) {
    throw new Error(`postgres (design.sql): ${e.message}`);
  }
  try {
    await pg.exec(stim);
  } catch (e) {
    throw new Error(`postgres (stim.sql): ${e.message}`);
  }
  const cycles = (await pg.query(`SELECT cycle FROM ${meta.name}_in ORDER BY cycle`)).rows.map((r) => Number(r.cycle));
  if (cycles.length === 0) throw new Error("stimulus is empty");
  cycles.forEach((c, k) => { if (c !== k) throw new Error(`stimulus has gap at cycle ${k}`); });
  const stimRows = (await pg.query(meta.stim_select)).rows;
  let traceRows;
  try {
    traceRows = (await pg.query(meta.trace_select)).rows;
  } catch (e) {
    throw new Error(`postgres (design.sql): ${e.message}`);
  }
  for (const row of traceRows) {
    for (const [k, v] of Object.entries(row)) {
      if (v === null) throw new Error(`NULL reached the trace at cycle ${row.cycle} (${k})`);
    }
  }
  return { stimRows, traceRows };
}

// ---------------------------------------------------------------------------
// Waveform

const skin = structuredClone(wavedrom.waveSkin.default);
skin[2][2] = WAVE_SKIN_CSS; // ['svg', attrs, ['style', {type}, css], ['defs', ...], ...]
const waveSkin = { default: skin };

// WaveDrom's '0'/'1' bricks slope into the new level over the 6 px after a
// cycle boundary, which reads as skew next to the clock's vertical edges.
// 'h'/'l' use the clock's own glyphs, so 1-bit lanes get a vertical edge
// exactly on the boundary. Bus lanes have no such glyph; renderWave shifts
// their bricks by SLOPE_HALF so each value crossing is centred on the boundary.
const SLOPE_HALF = 6;

function laneFor(sig, values, color) {
  // values: array of strings or null (unknown) per column; '.' compresses repeats.
  let wave = "";
  const data = [];
  let prev = undefined;
  for (const v of values) {
    if (v === null) { wave += "x"; prev = null; continue; }
    if (v === prev) { wave += "."; continue; }
    prev = v;
    if (sig.width === 1 && !sig.enum) wave += v === "1" ? "h" : "l";
    else { wave += color; data.push(v); }
  }
  const lane = { name: sig.name, wave, bus: sig.width > 1 || !!sig.enum };
  if (data.length) lane.data = data;
  return lane;
}

function renderWave(meta, stimRows, traceRows) {
  const n = traceRows.length; // cycles 0..n-1 ; stimRows.length === n-1
  const cols = n + 1; // one extra leading column for the reset clock
  const ticks = ["rst", ...Array.from({ length: n }, (_, k) => String(k))];

  // Input lanes stop after the last stimulus row: there is nothing to sample
  // at the final edge, and the lane ending early says so without a glyph.
  // Enum columns arrive as ordinals (the trace format); show the labels.
  const show = (sig, v) => (sig.enum && meta.enums[sig.enum] ? (meta.enums[sig.enum][Number(v)] ?? String(v)) : String(v));
  const inputs = meta.inputs.map((sig) =>
    laneFor(sig, [null, ...stimRows.map((r) => show(sig, r[sig.name]))], "5"));
  const regs = Object.fromEntries(meta.regs.map((r) => [r.name, r]));
  const outputs = meta.outputs.map((name) =>
    laneFor(regs[name], [null, ...traceRows.map((r) => show(regs[name], r[name]))], "7"));

  const signal = [
    { name: "clk", wave: "p" + ".".repeat(cols - 1) },
    { name: "rst", wave: "hl" + ".".repeat(cols - 2) },
    { name: "cycle", wave: "=".repeat(cols), data: ticks, bus: true },
  ];
  if (inputs.length) signal.push({}, { name: "INPUTS" }, ...inputs);
  if (outputs.length) signal.push({}, { name: "OUTPUTS" }, ...outputs);

  const hscale = cols <= 12 ? 3 : cols <= 40 ? 2 : 1;
  const src = { signal, config: { hscale } };
  const host = $("wave");
  const buses = new Set(signal.filter((l) => l && l.bus).map((l) => l.name));
  for (const l of signal) if (l) delete l.bus;
  wavedrom.renderWaveElement(0, src, host, waveSkin);
  colorLanes(host, meta, buses);
  for (const r of host.querySelectorAll('rect[style*="fill:white"]')) r.style.fill = "transparent";
  host.classList.remove("stale");
  $("wave-empty").hidden = true;
  $("wave-empty").classList.remove("error");
}

// WaveDrom draws every lane with the same stroke. Tag each lane <g> with its
// role so the stylesheet can colour inputs, outputs and the clock via
// `color` (the skin's traces use currentColor, which inherits into <use>).
function colorLanes(host, meta, buses) {
  const inputs = new Set(meta.inputs.map((s) => s.name));
  const outputs = new Set(meta.outputs);
  for (const g of host.querySelectorAll('g[id^="wavelane_"]')) {
    if (g.id.startsWith("wavelane_draw")) continue;
    const label = g.querySelector("text");
    const name = label ? label.textContent.trim() : "";
    if (buses.has(name)) alignBusLane(g);
    const cls = name === "clk" ? "lane-clk"
      : name === "rst" ? "lane-rst"
      : name === "cycle" ? "lane-cycle"
      : name === "INPUTS" || name === "OUTPUTS" ? "lane-header"
      : inputs.has(name) ? "lane-in"
      : outputs.has(name) ? "lane-out" : "";
    if (cls) g.classList.add(cls);
  }
}

// Centre a bus lane's value crossings on the cycle boundaries. WaveDrom draws
// each crossing in the 6 px after a boundary, so the lane's bricks are shifted
// left by SLOPE_HALF. That alone would let the first brick poke out before the
// lane origin and leave the last one short, so the lane is clipped to its own
// span [0, L] and the final (always flat) brick is cloned 6 px to the right
// to fill up to L.
const SVG_NS = "http://www.w3.org/2000/svg";
function alignBusLane(lane) {
  const draw = lane.querySelector('g[id^="wavelane_draw"]');
  if (!draw) return;
  const bricks = [...draw.querySelectorAll("use")];
  if (!bricks.length) return;
  const length = bricks.length * 20;
  const last = bricks[bricks.length - 1];
  const filler = last.cloneNode(true);
  filler.setAttribute("transform", `translate(${length - 20 + SLOPE_HALF})`);
  draw.insertBefore(filler, last);
  draw.setAttribute("transform", `translate(${-SLOPE_HALF},0)`);

  const svg = lane.ownerSVGElement;
  let defs = svg.querySelector("defs");
  if (!defs) { defs = document.createElementNS(SVG_NS, "defs"); svg.insertBefore(defs, svg.firstChild); }
  const clip = document.createElementNS(SVG_NS, "clipPath");
  clip.id = `sqrtl-clip-${lane.id}`;
  clip.setAttribute("clipPathUnits", "userSpaceOnUse");
  const rect = document.createElementNS(SVG_NS, "rect");
  rect.setAttribute("x", "0"); rect.setAttribute("y", "-5");
  rect.setAttribute("width", String(length)); rect.setAttribute("height", "30");
  clip.appendChild(rect);
  defs.appendChild(clip);
  const wrap = document.createElementNS(SVG_NS, "g");
  wrap.setAttribute("clip-path", `url(#${clip.id})`);
  draw.parentNode.insertBefore(wrap, draw);
  wrap.appendChild(draw);
}

function showWaveError(msg) {
  $("wave").classList.add("stale");
  const el = $("wave-empty");
  el.textContent = msg;
  el.classList.add("error");
  el.hidden = false;
}

// ---------------------------------------------------------------------------
// Boot

async function boot() {
  examples = await (await fetch("examples/index.json")).json();
  for (const ex of examples) {
    const opt = document.createElement("option");
    opt.value = ex.name;
    opt.textContent = ex.name;
    exampleSelect.appendChild(opt);
  }
  const saved = restore();
  const ex = examples.find((e) => e.name === saved?.example)
    || examples.find((e) => e.name === "incrementer") || examples[0];
  loadExample(ex, saved && saved.example === ex.name ? saved : {});

  await Promise.all([loadPython(), loadPostgres()]);
  setStatus("ready", "Ready");
  run();
}

boot().catch((e) => {
  console.error(e);
  setStatus("error", `Failed to start: ${e.message || e}`);
  showWaveError(`Failed to start: ${e.message || e}`);
});
