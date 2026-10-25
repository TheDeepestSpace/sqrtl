// Assemble the static site into web/dist:
//   app.js        esbuild bundle of web/src (CodeMirror, WaveDrom, app code)
//   vendor/       Pyodide runtime and PGlite (served self-hosted, no CDN)
//   py/           the sqrtl Python modules + the sqlglot wheel for Pyodide
//   examples/     design.sql / stim.sql of every example, as JSON
//   index.html, style.css, favicon.svg
import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SQLGLOT_VERSION = "30.24.0";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const web = path.join(root, "web");
const dist = path.join(web, "dist");
const nm = path.join(root, "node_modules");

fs.rmSync(dist, { recursive: true, force: true });
fs.mkdirSync(dist, { recursive: true });

// 1. static files
for (const f of ["index.html", "style.css", "favicon.svg"]) {
  fs.copyFileSync(path.join(web, f), path.join(dist, f));
}

// 2. app bundle
await build({
  entryPoints: [path.join(web, "src", "app.js")],
  bundle: true,
  format: "esm",
  target: "es2020",
  minify: true,
  sourcemap: true,
  outfile: path.join(dist, "app.js"),
  logLevel: "info",
});

// 3. Pyodide runtime (core only; packages are installed from py/)
const pyodideSrc = path.join(nm, "pyodide");
const pyodideDst = path.join(dist, "vendor", "pyodide");
fs.mkdirSync(pyodideDst, { recursive: true });
for (const f of ["pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]) {
  fs.copyFileSync(path.join(pyodideSrc, f), path.join(pyodideDst, f));
}

// 4. PGlite (ESM entry + chunks + wasm/data; skip maps, types, cjs, extensions)
const pgSrc = path.join(nm, "@electric-sql", "pglite", "dist");
const pgDst = path.join(dist, "vendor", "pglite");
const skip = (f) => /\.(map|d\.ts|d\.cts|cjs|tar\.gz)$/.test(f) || f === "contrib";
fs.cpSync(pgSrc, pgDst, { recursive: true, filter: (src) => !skip(path.basename(src)) });

// 5. Python: sqrtl modules the browser needs + the sqlglot wheel
const pyDst = path.join(dist, "py");
fs.mkdirSync(path.join(pyDst, "sqrtl"), { recursive: true });
const pyFiles = ["__init__.py", "parse.py", "verilog.py", "trace.py", "web.py"];
for (const f of pyFiles) fs.copyFileSync(path.join(root, "sqrtl", f), path.join(pyDst, "sqrtl", f));
const wheel = `sqlglot-${SQLGLOT_VERSION}-py3-none-any.whl`;
const cache = path.join(web, ".cache");
fs.mkdirSync(cache, { recursive: true });
if (!fs.existsSync(path.join(cache, wheel))) {
  execFileSync("python3", ["-m", "pip", "download", "--no-deps", "--only-binary=:all:", "-d", cache, `sqlglot==${SQLGLOT_VERSION}`],
    { stdio: "inherit" });
}
fs.copyFileSync(path.join(cache, wheel), path.join(pyDst, wheel));
fs.writeFileSync(path.join(pyDst, "manifest.json"), JSON.stringify({ wheel, files: pyFiles }, null, 2));

// 6. examples
const exDir = path.join(root, "examples");
const examples = fs.readdirSync(exDir)
  .filter((d) => fs.existsSync(path.join(exDir, d, "design.sql")))
  .sort((a, b) => (a === "incrementer" ? -1 : b === "incrementer" ? 1 : a.localeCompare(b)))
  .map((d) => ({
    name: d,
    design: fs.readFileSync(path.join(exDir, d, "design.sql"), "utf8"),
    stim: fs.readFileSync(path.join(exDir, d, "stim.sql"), "utf8"),
  }));
fs.mkdirSync(path.join(dist, "examples"), { recursive: true });
fs.writeFileSync(path.join(dist, "examples", "index.json"), JSON.stringify(examples));

fs.writeFileSync(path.join(dist, ".nojekyll"), "");

const size = (dir) => fs.readdirSync(dir, { withFileTypes: true })
  .reduce((n, e) => n + (e.isDirectory() ? size(path.join(dir, e.name)) : fs.statSync(path.join(dir, e.name)).size), 0);
console.log(`web/dist: ${(size(dist) / 1e6).toFixed(1)} MB, ${examples.length} examples`);
