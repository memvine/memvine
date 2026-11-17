// A screenshot of one console page, through Chrome's DevTools protocol, with no packages.
//
//   node shot.mjs <project-slug> <out.png> <path> [--width 430] [--height 1400] [--until "Heading"]
//                 [--selector css] [--pad 0] [--click "text"] [--full]
//
// --until stops the picture at the bottom of the card whose heading reads exactly that text.
// The console is MEMVINE_DEMO_CONSOLE (http://127.0.0.1:3119), running against a server with an
// operator key, so the project is chosen by its cookie. Chrome is CHROME, or the macOS default.
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [slug, out, path, ...rest] = process.argv.slice(2);
if (!slug || !out || !path) {
  console.error("usage: node shot.mjs <project-slug> <out.png> <path> [--width N] [--height N] [--until text] [--selector css] [--click text] [--full]");
  process.exit(2);
}
const opt = (name, fallback) => { const i = rest.indexOf(`--${name}`); return i >= 0 ? rest[i + 1] : fallback; };
const all = (name) => rest.flatMap((v, i) => (v === `--${name}` ? [rest[i + 1]] : []));
const width = +opt("width", 430), height = +opt("height", 1400), pad = +opt("pad", 0);
const ORIGIN = (process.env.MEMVINE_DEMO_CONSOLE || "http://127.0.0.1:3119").replace(/\/$/, "");
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const port = 9300 + Math.floor(Math.random() * 500);

const chrome = spawn(CHROME, [
  "--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "memvine-shot-"))}`,
  "--no-first-run", "--hide-scrollbars", "--force-dark-mode", `--window-size=${width},${height}`, "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 50 && !target; i++) {
  await sleep(200);
  try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page"); } catch {}
}
if (!target) { console.error("Chrome did not start:", CHROME); chrome.kill(); process.exit(1); }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0;
const waiting = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); } };
const send = (method, params = {}) => new Promise((resolve) => { const n = ++id; waiting.set(n, resolve); ws.send(JSON.stringify({ id: n, method, params })); });
const evaluate = async (expression) => (await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })).result?.result?.value;
const fail = (message) => { console.error(message); ws.close(); chrome.kill(); process.exit(1); };

await send("Page.enable");
await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 2, mobile: false });
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "dark" }] });
await send("Network.setCookie", { name: "mv_project", value: slug, url: ORIGIN, path: "/" });
await send("Page.navigate", { url: ORIGIN + path });
for (let i = 0; i < 60; i++) { await sleep(250); if (await evaluate("document.readyState === 'complete' && !!document.querySelector('main')")) break; }
await sleep(1200);
for (const text of all("click")) {
  await evaluate(`(() => { const el = [...document.querySelectorAll('button, a, summary, [role=button], span')].filter(e => e.textContent.trim().toLowerCase().includes(${JSON.stringify(text.toLowerCase())})).pop(); if (el) el.click(); return !!el; })()`);
  await sleep(1500);
}

let clip;
const selector = opt("selector"), until = opt("until");
if (selector) {
  const box = await evaluate(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) return null; const r = e.getBoundingClientRect(); return {x: r.x + scrollX, y: r.y + scrollY, width: r.width, height: r.height}; })()`);
  if (!box) fail(`no element for ${selector}`);
  clip = { x: Math.max(0, box.x - pad), y: Math.max(0, box.y - pad), width: box.width + 2 * pad, height: box.height + 2 * pad, scale: 1 };
} else if (until) {
  // The card around the heading: the nearest ancestor at least 150px tall.
  const bottom = await evaluate(`(() => { const h = [...document.querySelectorAll('*')].find(e => e.children.length === 0 && e.textContent.trim() === ${JSON.stringify(until)}); let c = h; while (c && c.getBoundingClientRect().height < 150) c = c.parentElement; return c ? c.getBoundingClientRect().bottom + scrollY : null; })()`);
  if (!bottom) fail(`no card headed "${until}" on ${path}`);
  clip = { x: 0, y: 0, width, height: Math.ceil(bottom) + 24, scale: 1 };
} else if (rest.includes("--full")) {
  const size = await evaluate("({w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight})");
  clip = { x: 0, y: 0, width: size.w, height: size.h, scale: 1 };
}
const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, ...(clip ? { clip } : {}) });
writeFileSync(out, Buffer.from(shot.result.data, "base64"));
console.log(out, clip ? `${Math.round(clip.width)}x${Math.round(clip.height)}` : `${width}x${height}`);
ws.close();
chrome.kill();
