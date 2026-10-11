import { problems } from "./validator.js";
import { config } from "./config.js";

const MAX_BYTES = 1_000_000;
const $ = (id) => document.getElementById(id);
const state = { doc: null, valid: false, token: "", schema: null };

const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const get = (o, path) => path.split(".").reduce((a, k) => (a && a[k] !== undefined ? a[k] : undefined), o);
const show = (v) => (v === undefined || v === null || v === "" ? "not declared" : String(v));

function render(doc, probs) {
  const box = $("report");
  box.replaceChildren();
  box.hidden = false;
  if (probs.length) {
    box.append(el("h2", "This file cannot be published"));
    const ul = el("ul", undefined, "problems");
    probs.slice(0, 20).forEach((p) => ul.append(el("li", p)));
    if (probs.length > 20) ul.append(el("li", `and ${probs.length - 20} more`));
    box.append(ul, el("p", "Fix the cause and run the validator locally (python3 -m strata_bench validate FILE) before trying again.", "hint"));
    return;
  }
  box.append(el("h2", doc.validity.status === "failed" ? "Valid: a failed run, it goes to the health view" : "Valid: this file can be published"));
  const dl = el("dl");
  [["Status", get(doc, "validity.status")], ["Failure", doc.validity.failure ? `${doc.validity.failure.kind} at ${doc.validity.failure.where}` : null], ["GPU", get(doc, "machine.gpus.0.name") ?? (Array.isArray(doc.machine?.gpus) ? doc.machine.gpus[0]?.name : undefined)],
    ["Engine", doc.engine?.version ?? doc.build_info], ["Model", doc.model?.family ?? doc.model_path], ["Config id", doc.config?.config_id], ["Runs", Array.isArray(doc.runs) ? doc.runs.length : undefined]]
    .filter(([k, v]) => k !== "Failure" || v).forEach(([k, v]) => { dl.append(el("dt", k), el("dd", show(v))); });
  box.append(dl, el("p", "These values come from your file. Hardware is shown as declared by you.", "hint"));
}

function update() {
  const ok = state.valid;
  $("send").hidden = !(ok && config.workerUrl);
  $("other").hidden = !(ok && !config.workerUrl);
  $("submit").disabled = !(ok && config.workerUrl && state.token);
}

function check(text) {
  $("outcome").textContent = "";
  state.doc = null; state.valid = false;
  if (new TextEncoder().encode(text).length > MAX_BYTES) { render(null, ["the file is larger than 1 MB"]); update(); return; }
  let doc;
  try { doc = JSON.parse(text); } catch { render(null, ["this is not valid JSON"]); update(); return; }
  const probs = problems(doc, state.schema);
  render(doc, probs);
  if (!probs.length) { state.doc = doc; state.valid = true; }
  update();
}

async function init() {
  state.schema = await (await fetch("schema.json", { cache: "no-cache" })).json();
  const read = async (file) => { if (file) check(await file.text()); };
  $("file").addEventListener("change", (e) => read(e.target.files[0]));
  const drop = $("drop");
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => read(e.dataTransfer.files[0]));
  $("paste").addEventListener("input", (e) => { if (e.target.value.trim()) check(e.target.value); });

  if (config.workerUrl && config.turnstileSiteKey) {
    const s = document.createElement("script");
    s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    s.async = true;
    s.onload = () => window.turnstile.render("#challenge", { sitekey: config.turnstileSiteKey, theme: document.documentElement.dataset.theme || "light", callback: (t) => { state.token = t; update(); }, "expired-callback": () => { state.token = ""; update(); } });
    document.head.append(s);
  }
  $("submit").addEventListener("click", async () => {
    $("submit").disabled = true;
    const out = $("outcome");
    out.replaceChildren(el("span", "Sending..."));
    try {
      const r = await fetch(config.workerUrl.replace(/\/$/, "") + "/submit", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ result: state.doc, turnstile: state.token }) });
      const d = await r.json().catch(() => ({}));
      if (r.status === 201 && typeof d.pull_request === "string" && d.pull_request.startsWith("https://github.com/")) {
        const a = el("a", d.pull_request); a.href = d.pull_request; a.rel = "noopener";
        out.replaceChildren(el("span", "Sent. A pull request was opened: "), a);
      } else {
        const why = { already_submitted: "This exact result was already submitted.", challenge_failed: "The check against automated use failed. Reload the page and try again.", too_large: "The file is too large.", rate_limited: "Too many submissions from your network. Try again later.", not_valid: "The server's validator rejected the file." }[d.error] || "The upload failed. Use the pull request or issue route instead." + (d.step ? ` (${String(d.step).slice(0, 20)} ${Number(d.status) || ""})` : "");
        out.replaceChildren(el("span", why));
      }
    } catch { out.replaceChildren(el("span", "Could not reach the upload service. Use the pull request or issue route instead.")); }
    if (window.turnstile) { try { window.turnstile.reset(); } catch { /* widget not ready */ } }
    state.token = ""; update();
  });
}
init().catch(() => { $("report").hidden = false; $("report").replaceChildren(el("p", "The page could not load its validator data. Reload it or use the pull request route.")); });
