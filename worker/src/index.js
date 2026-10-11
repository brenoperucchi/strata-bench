// strata-bench submission worker. It receives a result.json from the upload page, runs the SAME export gate as the Python validator, and opens a
// pull request that adds the file under results/. It never merges anything and never publishes anything by itself: the CI validator and a human decide.
//
// Secrets (wrangler secret put): GITHUB_TOKEN (a fine-grained token limited to this repository: contents and pull requests, read and write),
// TURNSTILE_SECRET. Vars (wrangler.toml): ALLOWED_ORIGIN, GITHUB_REPO, BASE_BRANCH.
import { problems } from "../../docs/validator.js";
import schema from "../../docs/schema.json" with { type: "json" };

export const MAX_BYTES = 1_000_000;
const GH = "https://api.github.com";

export function safeLabel(label) {
  const s = String(label ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40).replace(/-+$/g, "");
  return s || "result";
}

// Text that came from the file and goes into a PR title or body: only a plain character set, no markdown, mentions or links.
export function plain(v, max = 60) {
  return String(v ?? "").replace(/\s+/g, " ").replace(/[^A-Za-z0-9 ._+()/:-]/g, "").replace(/ +/g, " ").trim().slice(0, max);
}

export function toBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

export async function sha256Hex(text) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function fileBase(doc, hash8, today) {
  const raw = String(doc?.submission?.date ?? "").slice(0, 10);
  const date = /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : today;
  return `${date}-${safeLabel(doc?.label ?? doc?.submission?.label)}-${hash8}`;
}

const json = (status, body, headers = {}) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

function cors(request, env) {
  const origin = request.headers.get("origin");
  const allowed = String(env.ALLOWED_ORIGIN || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!origin || !allowed.includes(origin)) return { ok: false, headers: {} };
  return { ok: true, headers: { "access-control-allow-origin": origin, vary: "origin", "access-control-allow-methods": "POST, OPTIONS", "access-control-allow-headers": "content-type", "access-control-max-age": "600" } };
}

async function gh(env, method, path, body) {
  const r = await fetch(GH + path, {
    method,
    headers: { authorization: `Bearer ${env.GITHUB_TOKEN}`, accept: "application/vnd.github+json", "x-github-api-version": "2022-11-28", "user-agent": "strata-bench-submit", "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await r.json(); } catch { /* no body */ }
  if (r.status >= 400) console.log(`github ${method} ${path.replace(/\/contents\/.*/, "/contents/...")} -> ${r.status} ${String(data?.message ?? "").slice(0, 120)}`);   // our logs only, never in the response
  return { status: r.status, data };
}

async function verifyTurnstile(env, token, ip) {
  const form = new URLSearchParams({ secret: env.TURNSTILE_SECRET, response: String(token || "") });
  if (ip) form.set("remoteip", ip);
  const r = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", { method: "POST", body: form });
  const d = await r.json().catch(() => null);                              // a 400 still carries the error codes
  if (r.ok && d && d.success === true) return { ok: true, codes: [] };
  const codes = Array.isArray(d?.["error-codes"]) ? d["error-codes"].filter((x) => typeof x === "string" && /^[a-z0-9-]{1,40}$/.test(x)).slice(0, 5) : [];
  return { ok: false, codes: codes.length ? codes : [`http-${r.status}`] };                                             // Cloudflare's closed error codes only, never free text
}

export default {
  async fetch(request, env, ctx, now = () => new Date()) {
    const url = new URL(request.url);
    const c = cors(request, env);
    if (request.method === "OPTIONS") return c.ok ? new Response(null, { status: 204, headers: c.headers }) : new Response(null, { status: 403 });
    if (url.pathname === "/" && request.method === "GET") return json(200, { ok: true, service: "strata-bench-submit" }, c.headers);
    if (url.pathname !== "/submit") return json(404, { ok: false, error: "not_found" }, c.headers);
    if (request.method !== "POST") return json(405, { ok: false, error: "method_not_allowed" }, c.headers);
    if (!c.ok) return json(403, { ok: false, error: "origin_not_allowed" });
    if (!env.GITHUB_TOKEN || !env.TURNSTILE_SECRET || !env.GITHUB_REPO) return json(500, { ok: false, error: "not_configured" }, c.headers);

    const len = Number(request.headers.get("content-length") || 0);
    if (len > MAX_BYTES) return json(413, { ok: false, error: "too_large", max_bytes: MAX_BYTES }, c.headers);
    const text = await request.text();
    if (new TextEncoder().encode(text).length > MAX_BYTES) return json(413, { ok: false, error: "too_large", max_bytes: MAX_BYTES }, c.headers);
    let body;
    try { body = JSON.parse(text); } catch { return json(400, { ok: false, error: "not_json" }, c.headers); }
    if (!body || typeof body !== "object" || Array.isArray(body) || !("result" in body)) return json(400, { ok: false, error: "bad_request" }, c.headers);

    const ip = request.headers.get("cf-connecting-ip");
    if (env.LIMITER && typeof env.LIMITER.limit === "function") {
      const { success } = await env.LIMITER.limit({ key: ip || "unknown" });
      if (!success) return json(429, { ok: false, error: "rate_limited" }, c.headers);
    }
    const ts = await verifyTurnstile(env, body.turnstile, ip);
    if (!ts.ok) return json(403, { ok: false, error: "challenge_failed", codes: ts.codes }, c.headers);

    const doc = body.result;
    const probs = problems(doc, schema);
    if (probs.length) return json(422, { ok: false, error: "not_valid", problems: probs.slice(0, 20), more: Math.max(0, probs.length - 20) }, c.headers);

    const content = JSON.stringify(doc, null, 1) + "\n";                      // what is stored is the re-serialized, validated object, not the uploaded bytes
    const hash8 = (await sha256Hex(content)).slice(0, 8);
    const base = fileBase(doc, hash8, now().toISOString().slice(0, 10));
    const path = `results/${base}.result.json`;
    const branch = `submit/${base}`;
    const repo = env.GITHUB_REPO, baseBranch = env.BASE_BRANCH || "main";

    const ref = await gh(env, "GET", `/repos/${repo}/git/ref/heads/${baseBranch}`);
    if (ref.status !== 200 || !ref.data?.object?.sha) return json(502, { ok: false, error: "github", step: "base_ref", status: ref.status }, c.headers);
    const made = await gh(env, "POST", `/repos/${repo}/git/refs`, { ref: `refs/heads/${branch}`, sha: ref.data.object.sha });
    if (made.status === 422) return json(409, { ok: false, error: "already_submitted" }, c.headers);
    if (made.status !== 201) return json(502, { ok: false, error: "github", step: "branch", status: made.status }, c.headers);
    const put = await gh(env, "PUT", `/repos/${repo}/contents/${path}`, { message: `Add result ${base}`, content: toBase64(content), branch });
    if (put.status !== 201 && put.status !== 200) return json(502, { ok: false, error: "github", step: "file", status: put.status }, c.headers);

    const gpu = plain(doc?.machine?.gpus?.[0]?.name), engine = plain(doc?.engine?.version ?? doc?.build_info), model = plain(doc?.model?.family ?? doc?.model_path);
    const pr = await gh(env, "POST", `/repos/${repo}/pulls`, {
      title: `Result: ${plain(doc.label, 50) || "unnamed"}${gpu ? ` (${gpu})` : ""}`,
      head: branch, base: baseBranch, maintainer_can_modify: true,
      body: ["Submitted through the upload page. The validator in CI is the gate; merging adds the result to the dashboard.", "",
             `- status: ${plain(doc.validity?.status)}`, `- GPU: ${gpu || "not declared"}`, `- engine: ${engine || "unknown"}`, `- model: ${model || "unknown"}`, `- file: \`${path}\``].join("\n"),
    });
    if (pr.status !== 201 || typeof pr.data?.html_url !== "string") return json(502, { ok: false, error: "github", step: "pull_request", status: pr.status }, c.headers);
    return json(201, { ok: true, pull_request: pr.data.html_url, file: path }, c.headers);
  },
};
