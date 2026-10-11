#!/usr/bin/env node
// Headless Chrome screenshot through the DevTools protocol (Node 22+, no dependencies).
//   node tools/screenshot.mjs <url> <out.png> [--width 1280] [--height 800] [--wait "table tbody tr"] [--theme light|dark] [--full] [--chrome /path/to/chrome]
// It waits for the page's load event, then (with --wait) until the CSS selector matches, so pages that fill themselves
// from a fetch are captured after the data arrives. --theme saves the page's own theme choice (localStorage) and reloads, as a visitor's click would.
// The browser profile lives in a fixed directory that is reused; the tool never deletes anything.
import { spawn } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";

const args = process.argv.slice(2);
const opt = (name, dflt) => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : dflt; };
const flag = (name) => args.includes(`--${name}`);
const positional = args.filter((a, i) => !a.startsWith("--") && !(i > 0 && args[i - 1].startsWith("--") && !["full"].includes(args[i - 1].slice(2))));
const [url, out] = positional;
if (!url || !out) { console.error("usage: screenshot.mjs <url> <out.png> [--width N] [--height N] [--wait selector] [--theme light|dark] [--full] [--chrome path]"); process.exit(2); }

const width = Number(opt("width", 1280)), height = Number(opt("height", 800));
const wait = opt("wait", ""), theme = opt("theme", "");
const port = Number(opt("port", 9333));
const profile = opt("profile", "/tmp/strata-bench-screenshot-profile");
const candidates = [opt("chrome", ""), process.env.CHROME, `${homedir()}/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome`,
  "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser"].filter(Boolean);
const chrome = candidates.find((p) => existsSync(p));
if (!chrome) { console.error("no Chrome found; pass --chrome /path/to/chrome or set CHROME"); process.exit(2); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const child = spawn(chrome, [
  "--headless=new", "--ozone-platform=headless", "--no-sandbox", "--disable-gpu", "--disable-sync", "--disable-background-networking",
  "--disable-component-update", "--no-first-run", "--hide-scrollbars", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  `--window-size=${width},${height}`, "about:blank"], { stdio: "ignore" });
const stop = () => { try { child.kill("SIGKILL"); } catch { /* already gone */ } };
process.on("exit", stop);
const timer = setTimeout(() => { console.error("timed out after 60 s"); stop(); process.exit(1); }, 60000);

async function pageSocket() {
  for (let i = 0; i < 100; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* browser still starting */ }
    await sleep(100);
  }
  throw new Error("Chrome did not open its debugging port");
}

const ws = new WebSocket(await pageSocket());
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error("websocket failed")); });
let id = 0; const pending = new Map(); const events = [];
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else if (d.method) events.push(d.method); };
const send = (method, params = {}) => new Promise((res, rej) => { const n = ++id; pending.set(n, (d) => (d.error ? rej(new Error(`${method}: ${d.error.message}`)) : res(d.result))); ws.send(JSON.stringify({ id: n, method, params })); });
const ev = async (expression) => (await send("Runtime.evaluate", { expression, returnByValue: true })).result.value;
const until = async (cond, ms) => { const end = Date.now() + ms; while (Date.now() < end) { if (await cond()) return true; await sleep(100); } return false; };

try {
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url });
  if (!(await until(() => events.includes("Page.loadEventFired"), 30000))) throw new Error("page did not finish loading");
  if (theme) {                                                              // save the choice, then reload so the page builds itself in that theme
    await ev(`try { localStorage.setItem("strata-bench-theme", ${JSON.stringify(theme)}); } catch (e) {}`);
    events.length = 0;
    await send("Page.reload");
    if (!(await until(() => events.includes("Page.loadEventFired"), 30000))) throw new Error("page did not finish reloading");
  }
  if (wait && !(await until(() => ev(`!!document.querySelector(${JSON.stringify(wait)})`), 15000))) throw new Error(`selector never matched: ${wait}`);
  await sleep(300);                                                         // let the last paint settle
  let clip;
  if (flag("full")) {
    const m = await send("Page.getLayoutMetrics");
    clip = { x: 0, y: 0, width, height: Math.ceil(m.cssContentSize.height), scale: 1 };
  }
  const shot = await send("Page.captureScreenshot", { format: "png", ...(clip ? { clip, captureBeyondViewport: true } : {}) });
  writeFileSync(out, Buffer.from(shot.data, "base64"));
  console.log(`wrote ${out}`);
  try { await send("Browser.close"); } catch { /* the socket closes with the browser */ }
} catch (e) {
  console.error(String(e.message || e));
  process.exitCode = 1;
} finally {
  clearTimeout(timer);
  stop();
}
