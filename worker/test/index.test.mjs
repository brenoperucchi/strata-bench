// node --test worker/test/ : the worker with a mocked fetch. Nothing here touches the network.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import worker, { safeLabel, plain, toBase64, fileBase, MAX_BYTES } from "../src/index.js";

const VALID = JSON.parse(readFileSync(new URL("./fixtures/valid.result.json", import.meta.url), "utf8"));
const ORIGIN = "https://brenoperucchi.github.io";
const ENV = { ALLOWED_ORIGIN: ORIGIN, GITHUB_REPO: "owner/repo", BASE_BRANCH: "main", GITHUB_TOKEN: "ghp_SECRETTOKEN123456", TURNSTILE_SECRET: "ts-SECRET" };
const NOW = () => new Date("2026-10-12T08:00:00Z");

function mockFetch(routes) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method || "GET", headers: init.headers, body: init.body });
    for (const [pattern, handler] of routes) if ((calls.at(-1).method + " " + String(url)).startsWith(pattern) || String(url).includes(pattern)) {
      const r = typeof handler === "function" ? handler(calls.at(-1)) : handler;
      return new Response(JSON.stringify(r.body ?? {}), { status: r.status ?? 200 });
    }
    return new Response("{}", { status: 599 });
  };
  return calls;
}
const goodRoutes = () => [
  ["siteverify", { body: { success: true } }],
  ["GET https://api.github.com/repos/owner/repo/git/ref/heads/main", { body: { object: { sha: "basesha" } } }],
  ["POST https://api.github.com/repos/owner/repo/git/refs", { status: 201, body: {} }],
  ["PUT https://api.github.com/repos/owner/repo/contents/", { status: 201, body: {} }],
  ["POST https://api.github.com/repos/owner/repo/pulls", { status: 201, body: { html_url: "https://github.com/owner/repo/pull/7" } }],
];
const req = (body, { origin = ORIGIN, method = "POST", path = "/submit", headers = {} } = {}) =>
  new Request("https://worker.example" + path, { method, headers: { "content-type": "application/json", ...(origin ? { origin } : {}), ...headers }, body: method === "GET" || method === "OPTIONS" ? undefined : typeof body === "string" ? body : JSON.stringify(body) });
const call = (r, env = ENV) => worker.fetch(r, env, {}, NOW);
const submit = (result = VALID, extra = {}) => call(req({ result, turnstile: "tok", ...extra }));

test("a valid result opens a pull request through the GitHub API, in order, with the validated content", async () => {
  const calls = mockFetch(goodRoutes());
  const res = await submit();
  const out = await res.json();
  assert.equal(res.status, 201);
  assert.equal(out.ok, true);
  assert.equal(out.pull_request, "https://github.com/owner/repo/pull/7");
  assert.match(out.file, /^results\/2026-10-11-[a-z0-9-]+-[0-9a-f]{8}\.result\.json$/);
  assert.deepEqual(calls.map((c) => c.method + " " + new URL(c.url).pathname), [
    "POST /turnstile/v0/siteverify", "GET /repos/owner/repo/git/ref/heads/main", "POST /repos/owner/repo/git/refs", `PUT /repos/owner/repo/contents/${out.file}`, "POST /repos/owner/repo/pulls"]);
  const put = JSON.parse(calls[3].body);
  assert.equal(JSON.parse(Buffer.from(put.content, "base64").toString("utf8")).label, VALID.label);
  assert.equal(put.branch, JSON.parse(calls[2].body).ref.replace("refs/heads/", ""));
  assert.equal(JSON.parse(calls[2].body).sha, "basesha");
  const pr = JSON.parse(calls[4].body);
  assert.equal(pr.base, "main");
  assert.match(pr.body, /CI is the gate|validator in CI is the gate/);
  assert.equal(res.headers.get("access-control-allow-origin"), ORIGIN);
});

test("the token and the turnstile secret are only ever sent where they belong and never come back", async () => {
  const calls = mockFetch(goodRoutes());
  const res = await submit();
  const text = await res.text();
  assert.ok(!text.includes("SECRET"));
  for (const c of calls) {
    const sentTo = new URL(c.url).host;
    const hdr = JSON.stringify(c.headers || {}) + String(c.body || "");
    if (sentTo !== "api.github.com") assert.ok(!hdr.includes("ghp_SECRETTOKEN"), "the GitHub token went to " + sentTo);
    if (sentTo === "api.github.com") assert.ok(!String(c.body || "").includes("ts-SECRET"));
  }
  assert.ok(String(calls[0].body).includes("secret=ts-SECRET"));                       // siteverify gets the turnstile secret
});

test("CORS: only the allowed origin may post, preflight is answered for it and refused for others", async () => {
  mockFetch(goodRoutes());
  assert.equal((await call(req(null, { method: "OPTIONS" }))).status, 204);
  assert.equal((await call(req(null, { method: "OPTIONS", origin: "https://evil.example" }))).status, 403);
  assert.equal((await call(req(null, { method: "OPTIONS", origin: null }))).status, 403);
  const bad = await call(req({ result: VALID, turnstile: "t" }, { origin: "https://evil.example" }));
  assert.equal(bad.status, 403);
  assert.equal(bad.headers.get("access-control-allow-origin"), null);
  assert.equal((await call(req({ result: VALID }, { origin: null }))).status, 403);
});

test("routes and methods", async () => {
  mockFetch(goodRoutes());
  assert.equal((await call(req(null, { method: "GET", path: "/" }))).status, 200);
  assert.equal((await call(req(null, { method: "GET", path: "/submit" }))).status, 405);
  assert.equal((await call(req({}, { path: "/other" }))).status, 404);
});

test("an oversized body is refused before anything else (header and real size)", async () => {
  const calls = mockFetch(goodRoutes());
  const big = JSON.stringify({ result: VALID, turnstile: "t", pad: "x".repeat(MAX_BYTES) });
  assert.equal((await call(req(big))).status, 413);
  assert.equal((await call(req("{}", { headers: { "content-length": String(MAX_BYTES + 1) } }))).status, 413);
  assert.equal(calls.length, 0);
});

test("bad JSON and a malformed request body", async () => {
  mockFetch(goodRoutes());
  assert.equal((await call(req("{nope"))).status, 400);
  for (const b of [[], null, 5, {}, { turnstile: "t" }]) assert.equal((await call(req(typeof b === "object" && b !== null ? JSON.stringify(b) : String(b)))).status, 400);
});

test("a failed turnstile challenge stops before validation or GitHub", async () => {
  const calls = mockFetch([["siteverify", { body: { success: false, "error-codes": ["invalid-input-secret", "Bad Value: <x>"] } }]]);
  const refused = await submit();
  assert.equal(refused.status, 403);
  assert.deepEqual((await refused.json()).codes, ["invalid-input-secret"]);   // closed codes pass through, anything else is dropped
  assert.equal(calls.length, 1);
  const calls2 = mockFetch([["siteverify", { status: 500, body: {} }]]);
  assert.equal((await submit()).status, 403);
  assert.equal(calls2.length, 1);
});

test("an invalid result is refused with the validator's problems and never reaches GitHub", async () => {
  const calls = mockFetch(goodRoutes());
  const bad = structuredClone(VALID);
  bad.config.args = ["--pack", "E:\\private\\pack"];
  bad.hostname = "my-box";
  const res = await submit(bad);
  const out = await res.json();
  assert.equal(res.status, 422);
  assert.equal(out.error, "not_valid");
  assert.ok(out.problems.some((p) => p.includes("a Windows path")));
  assert.ok(out.problems.some((p) => p.includes("hostname")));
  assert.equal(calls.filter((c) => c.url.includes("api.github.com")).length, 0);
});

test("an incomplete or non-object result is refused", async () => {
  mockFetch(goodRoutes());
  const inc = structuredClone(VALID);
  inc.validity.status = "incomplete";
  assert.equal((await submit(inc)).status, 422);
  for (const r of [[], "x", 5, null]) assert.equal((await submit(r)).status, 422);
});

test("many problems are capped", async () => {
  mockFetch(goodRoutes());
  const bad = structuredClone(VALID);
  for (let i = 0; i < 50; i++) bad["k" + i] = 1;
  const out = await (await submit(bad)).json();
  assert.equal(out.problems.length, 20);
  assert.ok(out.more >= 30);
});

test("the same result submitted twice is a conflict, not a second pull request", async () => {
  const routes = goodRoutes();
  routes[2] = ["POST https://api.github.com/repos/owner/repo/git/refs", { status: 422, body: { message: "Reference already exists" } }];
  const calls = mockFetch(routes);
  const res = await submit();
  assert.equal(res.status, 409);
  assert.equal((await res.json()).error, "already_submitted");
  assert.equal(calls.some((c) => c.method === "PUT"), false);
});

test("a GitHub failure at any step is a 502 with the step and status only", async () => {
  for (const [idx, step] of [[1, "base_ref"], [2, "branch"], [3, "file"], [4, "pull_request"]]) {
    const routes = goodRoutes();
    routes[idx] = [routes[idx][0], { status: 403, body: { message: "Resource not accessible token ghp_SECRETTOKEN123456" } }];
    mockFetch(routes);
    const res = await submit();
    const out = await res.json();
    assert.equal(res.status, 502, step);
    assert.deepEqual([out.error, out.step, out.status], ["github", step, 403]);
    assert.ok(!JSON.stringify(out).includes("SECRET"));
  }
});

test("a worker without its secrets refuses instead of guessing", async () => {
  mockFetch(goodRoutes());
  for (const k of ["GITHUB_TOKEN", "TURNSTILE_SECRET", "GITHUB_REPO"]) {
    const env = { ...ENV };
    delete env[k];
    assert.equal((await call(req({ result: VALID, turnstile: "t" }), env)).status, 500, k);
  }
});

test("the rate limiter, when bound, is consulted per client before the challenge", async () => {
  const calls = mockFetch(goodRoutes());
  const seen = [];
  const env = { ...ENV, LIMITER: { limit: async ({ key }) => (seen.push(key), { success: false }) } };
  const res = await call(req({ result: VALID, turnstile: "t" }, { headers: { "cf-connecting-ip": "203.0.113.9" } }), env);
  assert.equal(res.status, 429);
  assert.deepEqual(seen, ["203.0.113.9"]);
  assert.equal(calls.length, 0);
});

test("hostile text in the file cannot become markdown, mentions or links in the pull request", async () => {
  const calls = mockFetch(goodRoutes());
  const doc = structuredClone(VALID);
  doc.label = "@everyone [click](http://evil) <script>";
  doc.machine.gpus = [{ name: "RTX @maintainer #1 ![x](http://e)" }];
  const res = await submit(doc);
  assert.equal(res.status, 201);
  const pr = JSON.parse(calls.at(-1).body);
  for (const s of [pr.title, pr.body.split("\n").slice(2, 5).join("\n")]) assert.ok(!/[@#<>\[\]!]/.test(s), s);
  assert.ok(JSON.parse(calls.at(-1).body).head.startsWith("submit/"));
  assert.match(JSON.parse(calls.at(-1).body).head, /^submit\/[a-z0-9-]+$/);
});

test("file names are derived safely and deterministically", async () => {
  assert.equal(safeLabel("My Rig / 5090!!"), "my-rig-5090");
  assert.equal(safeLabel("../../etc/passwd"), "etc-passwd");
  assert.equal(safeLabel(""), "result");
  assert.equal(safeLabel(null), "result");
  assert.equal(safeLabel("é✓"), "result");
  assert.ok(safeLabel("a".repeat(100)).length <= 40);
  assert.equal(fileBase({ label: "x", submission: { date: "2026-01-02T03:04:05+00:00" } }, "abcd1234", "2026-10-12"), "2026-01-02-x-abcd1234");
  assert.equal(fileBase({ label: "x", submission: { date: "garbage" } }, "abcd1234", "2026-10-12"), "2026-10-12-x-abcd1234");
  assert.equal(fileBase({ label: "x", submission: { date: "../../2026-01-02" } }, "abcd1234", "2026-10-12"), "2026-10-12-x-abcd1234");
  assert.equal(plain("a b\n@c #d [e]"), "a b c d e");
  assert.equal(plain(null), "");
  assert.equal(Buffer.from(toBase64("héllo ✓"), "base64").toString("utf8"), "héllo ✓");
});

test("the stored file is the re-serialized validated object, with a hash in its name", async () => {
  const calls = mockFetch(goodRoutes());
  const noisy = JSON.parse(JSON.stringify(VALID));
  const text = JSON.stringify(noisy) ;                                  // compact upload
  const res = await call(req(`{"result":${text},"turnstile":"t"}`));
  assert.equal(res.status, 201);
  const put = JSON.parse(calls[3].body);
  const stored = Buffer.from(put.content, "base64").toString("utf8");
  assert.equal(stored, JSON.stringify(VALID, null, 1) + "\n");
  const again = await call(req({ result: VALID, turnstile: "t" }));
  const out = await again.json();
  assert.match(calls.at(-2).url, new RegExp(out.file.replace(/[.]/g, "\\.")));
});
