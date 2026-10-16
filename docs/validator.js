// The public-export gate in JavaScript. A thin interpreter of docs/schema.json, which tools/build_validator_data.py writes from the Python
// validator (strata_bench.validate). tests/test_js_validator.py checks that both return EXACTLY the same problems. No dependencies, no I/O:
// it runs in the browser (upload page), in the Cloudflare Worker and under Node.

const isScalar = (v) => v === null || typeof v === "string" || typeof v === "boolean" || (typeof v === "number" && Number.isFinite(v));
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);        // own keys only: "constructor" or "toString" are not fields of any object
const isNum = (v) => v === null || (typeof v === "number" && Number.isFinite(v));
const tnum = (v) => typeof v === "number";
const byUnit = ([a], [b]) => (a < b ? -1 : a > b ? 1 : 0);                      // UTF-16 code unit order: the order Python's validator sorts in
const SAFE = /^[A-Za-z0-9._ -]{0,40}$/;
// A value for a message, only when it is short and plain; anything else is not echoed (so no spelling of an exotic value can differ from Python's).
const shown = (v) => (v === undefined || v === null ? "None" : v === true ? "True" : v === false ? "False" : typeof v === "number" && Number.isInteger(v) && Math.abs(v) < 2 ** 53 ? String(v) : typeof v === "string" && SAFE.test(v) ? `'${v}'` : "(value not shown)");
const LONE = /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/;
// Containers nested more than `limit` levels, found without recursion (a recursive walker would meet the end of the stack first).
function tooDeep(x, limit) {
  const stack = [[x, 1]];
  while (stack.length) {
    const [node, depth] = stack.pop();
    if (depth > limit && (Array.isArray(node) || isObj(node))) return true;
    if (Array.isArray(node)) for (const v of node) stack.push([v, depth + 1]);
    else if (isObj(node)) for (const v of Object.values(node)) stack.push([v, depth + 1]);
  }
  return false;
}
// Every string and every key of the result, decoded, in one fixed order (keys in UTF-16 code unit order, lists in order), without recursion.
function* strings(doc) {
  const stack = [doc];
  while (stack.length) {
    const node = stack.pop();
    if (typeof node === "string") yield node;
    else if (Array.isArray(node)) for (let i = node.length - 1; i >= 0; i--) stack.push(node[i]);
    else if (isObj(node)) {
      const items = Object.entries(node).sort(byUnit);
      for (let i = items.length - 1; i >= 0; i--) { stack.push(items[i][1]); stack.push(items[i][0]); }
    }
  }
}
function loneSurrogate(doc) {
  for (const text of strings(doc)) if (LONE.test(text)) return true;
  return false;
}
// [a, b, ...] as "a:b:..." when every part is an integer-valued number, else null: how a request names itself.
const ident = (...parts) => (parts.every((p) => tnum(p) && Number.isSafeInteger(p)) ? parts.join(":") : null);

function check(value, spec, where, problems, data) {
  const hex = new RegExp(data.hex_re);
  if (spec === "scalar") {
    if (!isScalar(value)) problems.push(`${where}: not a scalar`);
  } else if (spec === "num") {
    if (!isNum(value)) problems.push(`${where}: not a number`);
  } else if (spec.t === "enum") {
    if (!(typeof value === "string" ? spec.v.includes(value) : value === null && spec.v.includes(null))) problems.push(`${where}: not one of ` + spec.v.filter((x) => x !== null).join(", ") + (spec.v.includes(null) ? ", null" : ""));
  } else if (spec === "hex") {
    if (!(typeof value === "string" && hex.test(value))) problems.push(`${where}: not a hex digest`);
  } else if (spec.t === "list") {
    if (!Array.isArray(value)) problems.push(`${where}: not a list`);
    else value.forEach((v, i) => check(v, spec.s, `${where}[${i}]`, problems, data));
  } else if (spec.t === "map") {
    if (!isObj(value)) problems.push(`${where}: not an object`);
    else {
      const key = new RegExp(spec.re);
      for (const [k, v] of Object.entries(value).sort(byUnit)) {
        if (!key.test(String(k))) problems.push(`${where}.${k}: key not allowed`);
        else check(v, spec.s, `${where}.${k}`, problems, data);
      }
    }
  } else if (spec.t === "obj") {
    if (!isObj(value)) problems.push(`${where}: not an object`);
    else {
      for (const [k, v] of Object.entries(value).sort(byUnit)) {
        if (!has(spec.k, k)) problems.push(`${where}.${k}: not in the public schema`.replace(/^\./, ""));
        else check(v, spec.k[k], `${where}.${k}`.replace(/^\./, ""), problems, data);
      }
    }
  }
  return problems;
}

// Python's repr() for the values that appear in the messages (None, booleans, numbers, short strings, and lists or objects of those).
function pyRepr(v) {
  if (v === undefined || v === null) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  if (typeof v === "number") return String(v);
  if (Array.isArray(v)) return "[" + v.map(pyRepr).join(", ") + "]";
  if (typeof v === "object") return "{" + Object.entries(v).map(([k, x]) => pyRepr(k) + ": " + pyRepr(x)).join(", ") + "}";
  const s = String(v);
  const esc = s.replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t").replace(/[\x00-\x1f\x7f]/g, (c) => "\\x" + c.charCodeAt(0).toString(16).padStart(2, "0"));
  if (s.includes("'") && !s.includes('"')) return `"${esc}"`;
  return `'${esc.replace(/'/g, "\\'")}'`;
}

// v2 adds first-token timing, the suite and the host relation; a v1 result must not carry them, a v2 result must declare the closed ones.
function versionProblems(doc, proto, data, out) {
  const v2 = doc.schema === "strata-bench/v2";
  const runner = isObj(doc.runner) ? doc.runner : {};
  const sizes = isObj(doc.sizes) ? doc.sizes : {};
  const runs = Array.isArray(doc.runs) ? doc.runs.filter(isObj) : [];
  if (!v2) {
    const present = [["runner.host_relation", has(runner, "host_relation")], ["protocol.suite", has(proto, "suite")], ["protocol.blocks", has(proto, "blocks")], ["protocol.skipped", has(proto, "skipped")]];
    for (const [block, data] of [["agent", has(doc, "agent")], ["chat", has(sizes, "chat")], ["clients", has(doc, "clients")]]) present.push([`protocol.${block}`, has(proto, block)], [block === "chat" ? "sizes.chat" : block, data]);
    for (const k of data.v2_run_keys) present.push([`runs[].${k}`, runs.some((r) => has(r, k))]);
    present.push(["sizes.*.ttft_ms", Object.values(sizes).some((s) => isObj(s) && has(s, "ttft_ms"))]);
    for (const [path, here] of present) if (here) out.push(`${path} is only allowed in strata-bench/v2`);
    return;
  }
  if (!data.host_relations.includes(runner.host_relation)) out.push("runner.host_relation must be one of " + data.host_relations.join(", "));
  if (!data.suites.includes(proto.suite)) out.push("protocol.suite must be one of " + data.suites.join(", "));
  const blocks = proto.blocks;
  if (!(Array.isArray(blocks) && blocks.length > 0 && blocks.every((b) => data.blocks.includes(b)))) {
    out.push("protocol.blocks must be a list of " + data.blocks.join(", "));
    return;
  }
  const suiteBlocks = typeof proto.suite === "string" && Object.prototype.hasOwnProperty.call(data.suite_blocks, proto.suite) ? data.suite_blocks[proto.suite] : null;
  if (suiteBlocks && !(blocks.length === suiteBlocks.length && blocks.every((b, i) => b === suiteBlocks[i]))) out.push(`protocol.suite ${proto.suite} lists the blocks ` + suiteBlocks.join(", "));
  let skipped = proto.skipped;
  if (skipped !== undefined && skipped !== null && !(Array.isArray(skipped) && skipped.every((k) => isObj(k) && blocks.includes(k.block) && has(data.skip_pairs, k.block) && data.skip_pairs[k.block] === k.reason))) {
    out.push("protocol.skipped must list blocks of this result with their reason: " + Object.entries(data.skip_pairs).map(([b, r]) => `${b} ${r}`).join(", "));
    skipped = [];
  }
  const skippedBlocks = (Array.isArray(skipped) ? skipped : []).map((k) => (isObj(k) ? k.block : undefined));
  const status = isObj(doc.validity) ? doc.validity.status : undefined;
  const agent = doc.agent, clients = doc.clients, P = data.block_protocols;
  const of = (size, kind = "run") => runs.filter((r) => r.size === size && r.kind === kind);
  // a finished request: its first-token time, total time, the engine's timings and token counts, and both hashes
  const hex = new RegExp(data.hex_re);
  const streamed = (r) => isObj(r.stream) && tnum(r.stream.ttft_ms) && tnum(r.wall_ms) && isObj(r.timings) && data.required_timings.every((x) => tnum(r.timings[x]))
    && isObj(r.tokens) && tnum(r.tokens.prompt) && tnum(r.tokens.generated) && ["request_sha256", "output_sha256"].every((h) => typeof r[h] === "string" && hex.test(r[h]));
  const summary = (sz, key = "ttft_ms") => isObj(sz) && isObj(sz[key]) && tnum(sz[key].median);
  const summaries = (sz) => data.size_summaries.every((k) => summary(sz, k));
  // the runs name exactly the wanted requests, each once (`entries` are the idents of the runs, null when a run does not name itself)
  const exactly = (entries, wanted) => !entries.includes(null) && entries.length === new Set(entries).size && entries.length === wanted.length && wanted.every((w) => entries.includes(w));
  const range = (a, b) => Array.from({ length: b - a + 1 }, (_, i) => a + i);
  const sessions = isObj(agent) ? agent.sessions : undefined;
  const counts = P.clients.counts.split(",").map(Number);
  const agentOk = Array.isArray(sessions) && sessions.length === P.agent.sessions && sessions.every((x) => isObj(x) && data.agent_session_keys.every((k) => tnum(x[k])))
    && isObj(agent.turns) && Object.keys(agent.turns).sort().join() === range(1, P.agent.turns).map(String).join()
    && Object.values(agent.turns).every((v) => isObj(v) && ["prompt_n", "cache_n", "ttft_ms"].every((k) => summary(v, k)))
    && summary(agent, "hit_pct") && summary(agent, "reread_tokens")
    && exactly(sessions.map((x) => ident(x.index)), range(1, P.agent.sessions).map(String))
    && exactly(of("agent").map((r) => ident(r.index, r.turn)), range(1, P.agent.sessions).flatMap((i) => range(1, P.agent.turns).map((t) => `${i}:${t}`)))
    && of("agent").every(streamed);
  const chat = sizes.chat;
  const chatOk = summaries(chat) && exactly(of("chat").map((r) => ident(r.index)), range(1, P.chat.runs).map(String)) && of("chat").every(streamed);
  const clientsOk = () => {
    if (!(Array.isArray(clients) && clients.length === counts.length)) return false;
    const pool = of("clients");
    if (pool.length !== counts.reduce((t, n) => t + n * P.clients.rounds, 0)) return false;
    if (runs.some((r) => r.size === "clients" && has(r, "error_status") && (!data.busy_status.includes(r.error_status) || has(r, "stream")))) return false;       // warm-ups too
    for (const [i, n] of counts.entries()) {
      const row = clients[i], mine = pool.filter((r) => r.clients === n);
      if (!(isObj(row) && data.clients_row_keys.every((k) => has(row, k)) && row.n === n && row.requests === n * P.clients.rounds
        && tnum(row.rounds) && row.rounds === P.clients.rounds && tnum(row.errors)
        && data.clients_metrics.every((k) => (row.requests - row.errors > 0 ? tnum(row[k]) : row[k] === null))
        && exactly(mine.map((r) => ident(r.index, r.client)), range(1, P.clients.rounds).flatMap((rd) => range(1, n).map((k) => `${rd}:${k}`))))) return false;
      const refused = mine.filter((r) => has(r, "error_status"));
      if (refused.some((r) => !data.busy_status.includes(r.error_status) || has(r, "stream")) || !mine.filter((r) => !has(r, "error_status")).every(streamed)) return false;
      if (!(tnum(row.errors) && row.errors === refused.length)) return false;
    }
    return true;
  };
  const summarized = data.text_sizes.filter((z) => isObj(sizes[z]) && isObj(sizes[z].ttft_ms));
  const firstOk = summarized.length > 0 && summarized.every((z) => summaries(sizes[z]) && tnum(proto.runs) && of(z).length === proto.runs && of(z).every(streamed));
  // per block: [the content a finished result must carry, where that data is, what it is called in the message]
  const parts = {
    agent: [agentOk, has(doc, "agent"), "agent.sessions"],
    chat: [chatOk, has(sizes, "chat"), "sizes.chat"],
    clients: [clientsOk(), has(doc, "clients"), "clients"],
    "first-token": [firstOk, false, "ttft_ms in a text size and its streamed runs"],
  };
  for (const [block, [complete, hasData, label]] of Object.entries(parts)) {
    const ran = blocks.includes(block) && !skippedBlocks.includes(block);
    if (block !== "first-token" && !ran && (hasData || has(proto, block) || runs.some((r) => r.size === block))) out.push(`${block} data is only allowed when the ${block} block ran`);
    else if (ran && status !== "failed" && !complete) out.push(`a result with the ${block} block needs ${label}`);
    if (block !== "first-token" && ran && !sameFlat(proto[block], P[block])) out.push(`protocol.${block} is not standard-v2's`);
  }
  if (skippedBlocks.includes("agent") && !(tnum(doc.n_ctx) && doc.n_ctx < data.agent_min_ctx)) out.push(`protocol.skipped agent needs a server context under ${data.agent_min_ctx} tokens`);
}

// Two flat objects with equal values; a boolean never equals a number (Python's True == 1 would, JavaScript's does not).
function sameFlat(a, b) {
  return isObj(a) && Object.keys(a).length === Object.keys(b).length && Object.keys(b).every((k) => has(a, k) && a[k] === b[k]);
}

// 'secret' or 'host' when a command-line flag's name says it carries a credential or an address, else null.
function flagKind(arg, data) {
  const name = arg.split("=")[0].replace(/^-+/, "").toLowerCase();
  if (data.secret_flag_words.some((w) => name.includes(w))) return "secret";
  if (data.host_flag_words.some((w) => name.includes(w))) return "host";
  return null;
}
// The runner replaces the value of a credential or address flag; a result whose args still carry one is refused.
function configArgsProblems(doc, data, out) {
  const args = isObj(doc.config) ? doc.config.args : undefined;
  if (!Array.isArray(args)) return;
  args.forEach((a, i) => {
    if (typeof a !== "string" || !a.startsWith("-") || !flagKind(a, data)) return;
    const next = args[i + 1];
    const value = a.includes("=") ? a.slice(a.indexOf("=") + 1) : (i + 1 < args.length && !(typeof next === "string" && next.startsWith("-")) ? next : null);
    if (value !== null && !data.flag_placeholders.includes(value)) out.push(`config.args[${i}] carries a credential or an address`);
  });
}
// What a result says about its health must agree with itself.
function healthProblems(doc, out) {
  const validity = isObj(doc.validity) ? doc.validity : {};
  const { status, failure, warnings } = validity;
  const runs = Array.isArray(doc.runs) ? doc.runs.filter(isObj) : [];
  if (runs.some((r) => r.output_check === "garbage") && status !== "failed") out.push("a run with degenerate output needs a failed result");
  if (status === "ok" && runs.some((r) => has(r, "error_status"))) out.push("a result with refused requests needs status warnings");
  const listed = Array.isArray(warnings) && warnings.length > 0;
  if (status === "ok" && listed) out.push("status ok cannot carry warnings");
  if (status === "warnings" && !listed) out.push("status warnings needs at least one warning");
  if (validity.error !== undefined && validity.error !== null && status !== "failed") out.push("validity.error is only allowed on a failed result");
}

export function problems(doc, data) {
  if (tooDeep(doc, data.max_depth)) return [`nested more than ${data.max_depth} levels deep`];
  const out = check(doc, data.public, "", [], data);
  if (!isObj(doc)) return out;
  const schemaIds = data.schema_ids;
  const schemaKnown = Object.values(schemaIds).includes(doc.schema);
  if (!schemaKnown) out.push("schema is not strata-bench/v1 or strata-bench/v2");
  const proto = isObj(doc.protocol) ? doc.protocol : {};
  const pid = proto.id;
  if (!(typeof pid === "string" && Object.prototype.hasOwnProperty.call(schemaIds, pid))) out.push(`protocol ${shown(pid)} is not public`);
  else if (schemaKnown && schemaIds[pid] !== doc.schema) out.push(`protocol ${pid} is written in ${schemaIds[pid]}, not ${doc.schema}`);
  versionProblems(doc, proto, data, out);
  if (isObj(proto.prompt_sha256)) {
    const known = pid === "standard-v2" ? { ...data.prompts, ...data.prompts_v2 } : data.prompts;
    const label = typeof pid === "string" && has(schemaIds, pid) ? pid : "standard-v1";
    for (const [name, h] of Object.entries(proto.prompt_sha256).sort(byUnit)) if (!has(known, name) || known[name] !== h) out.push(`prompt ${name} is not ${label}'s`);
  }
  if (proto.probe_system_sha256 !== undefined && proto.probe_system_sha256 !== null && proto.probe_system_sha256 !== data.probe_system_sha256) out.push("probe system prompt is not standard-v1's");
  const gp = isObj(proto.gprobe) ? proto.gprobe : {};
  if (Object.keys(gp).length) {
    const chars = isObj(gp.chars) ? gp.chars : null;
    const same = chars && Object.keys(chars).length === Object.keys(data.gprobe_chars).length && Object.entries(data.gprobe_chars).every(([k, v]) => chars[k] === v);
    if (gp.system_sha256 !== data.gprobe_system_sha256 || !same) out.push("gprobe is not standard-v1's");
  }
  const validity = isObj(doc.validity) ? doc.validity : {};
  const status = validity.status;
  const failure = validity.failure;
  if (!data.statuses.includes(status)) out.push(`result is not complete (status ${shown(status)})`);
  if (status === "failed") {
    if (!isObj(failure) || !data.failure_kinds.includes(failure.kind)) out.push("a failed result needs failure.kind from " + data.failure_kinds.join(", "));
    else if (!(typeof failure.where === "string" && new RegExp(data.failure_where_re).test(failure.where))) out.push("failure.where is not one of the protocol's call names");
  } else if (failure !== undefined && failure !== null) out.push("failure is only allowed on a failed result");
  for (const [block, key] of [["submission", "by"], ["machine", "machine_id"]]) {   // declared names: short, no free text
    const section = doc[block];
    if (isObj(section) && section[key] !== undefined && section[key] !== null && !(typeof section[key] === "string" && new RegExp("^(?:" + data.id_re + ")$").test(section[key]))) out.push(`${block}.${key} must be a short name (letters, digits, '.', '_' and '-')`);
  }
  configArgsProblems(doc, data, out);
  healthProblems(doc, out);
  if (loneSurrogate(doc)) out.push("contains text that is not valid Unicode");
  else if ([...strings(doc)].some((text) => [...text].length > data.max_string)) out.push(`contains a string longer than ${data.max_string} characters`);   // declared text is limited: a name, a label, a short note, never a document
  else {
    const found = {};
    for (const text of strings(doc)) {      // the decoded strings and keys, not their JSON spelling: a newline or a NUL in front of a token hides nothing
      for (const [re, what] of data.leaks) if (!has(found, what)) { const m = new RegExp(re).exec(text); if (m) found[what] = m[0]; }
    }
    for (const [, what] of data.leaks) if (has(found, what)) out.push(`contains ${what}: ${pyRepr(found[what])}`);
  }
  return out;
}

// A result measured exactly what its profile says: standard-v1 is probe, medium, long and xlong; standard-v2 is the quick, standard or full suite. Results are only
// comparable when everybody measured the same things, so nobody picks sizes. A failed result is a health entry and may stop anywhere.
export function profileProblems(doc, data) {
  if (!isObj(doc) || (isObj(doc.validity) ? doc.validity : {}).status === "failed") return [];
  const proto = isObj(doc.protocol) ? doc.protocol : {};
  const sizes = isObj(doc.sizes) ? doc.sizes : {};
  let want;
  let label;
  if (proto.id === "standard-v1") { want = data.profile_sizes["standard-v1"]; label = "standard-v1"; }
  else if (proto.id === "standard-v2") {
    if (!["quick", "standard", "full"].includes(proto.suite)) return ["protocol.suite must be quick, standard or full: a set of sizes of one's own is not accepted"];
    want = data.profile_sizes[proto.suite];
    label = `the ${proto.suite} suite`;
  } else return [];
  const have = Object.keys(sizes);
  if (have.length !== want.length || !want.every((k) => has(sizes, k))) return [`sizes must be exactly ${want.join(", ")} for ${label} (found ${[...have].sort(byUnit2).join(", ") || "none"})`];
  return [];
}
const byUnit2 = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

// The export gate of the upload page and the Worker: the data rules, then the profile. (CI and the site build also let the closed list of legacy files skip the profile.)
export function gate(doc, data) {
  return problems(doc, data).concat(profileProblems(doc, data));
}
