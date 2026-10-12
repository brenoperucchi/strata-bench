// The public-export gate in JavaScript. A thin interpreter of docs/schema.json, which tools/build_validator_data.py writes from the Python
// validator (strata_bench.validate). tests/test_js_validator.py checks that both return EXACTLY the same problems. No dependencies, no I/O:
// it runs in the browser (upload page), in the Cloudflare Worker and under Node.

const isScalar = (v) => v === null || ["string", "number", "boolean"].includes(typeof v);
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

function check(value, spec, where, problems, data) {
  const hex = new RegExp(data.hex_re);
  if (spec === "scalar") {
    if (!isScalar(value)) problems.push(`${where}: not a scalar`);
  } else if (spec === "hex") {
    if (!(typeof value === "string" && hex.test(value))) problems.push(`${where}: not a hex digest`);
  } else if (spec.t === "list") {
    if (!Array.isArray(value)) problems.push(`${where}: not a list`);
    else value.forEach((v, i) => check(v, spec.s, `${where}[${i}]`, problems, data));
  } else if (spec.t === "map") {
    if (!isObj(value)) problems.push(`${where}: not an object`);
    else {
      const key = new RegExp(spec.re);
      for (const [k, v] of Object.entries(value)) {
        if (!key.test(String(k))) problems.push(`${where}.${k}: key not allowed`);
        else check(v, spec.s, `${where}.${k}`, problems, data);
      }
    }
  } else if (spec.t === "obj") {
    if (!isObj(value)) problems.push(`${where}: not an object`);
    else {
      for (const [k, v] of Object.entries(value)) {
        if (!(k in spec.k)) problems.push(`${where}.${k}: not in the public schema`.replace(/^\./, ""));
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
  const esc = s.replace(/\\/g, "\\\\");
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
    const present = [["runner.host_relation", "host_relation" in runner], ["protocol.suite", "suite" in proto], ["protocol.blocks", "blocks" in proto], ["protocol.skipped", "skipped" in proto]];
    for (const [block, data] of [["agent", "agent" in doc], ["chat", "chat" in sizes], ["clients", "clients" in doc]]) present.push([`protocol.${block}`, block in proto], [block === "chat" ? "sizes.chat" : block, data]);
    for (const k of data.v2_run_keys) present.push([`runs[].${k}`, runs.some((r) => k in r)]);
    present.push(["sizes.*.ttft_ms", Object.values(sizes).some((s) => isObj(s) && "ttft_ms" in s)]);
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
  if (skipped !== undefined && skipped !== null && !(Array.isArray(skipped) && skipped.every((k) => isObj(k) && blocks.includes(k.block) && data.skip_reasons.includes(k.reason)))) {
    out.push("protocol.skipped must list blocks of this result with a reason from " + data.skip_reasons.join(", "));
    skipped = [];
  }
  const skippedBlocks = (Array.isArray(skipped) ? skipped : []).map((k) => (isObj(k) ? k.block : undefined));
  const status = isObj(doc.validity) ? doc.validity.status : undefined;
  // per block: [the data a finished result must carry, where that data is, what it is called in the message]
  const parts = {
    agent: [isObj(doc.agent) && Array.isArray(doc.agent.sessions) && doc.agent.sessions.length > 0, "agent" in doc, "agent.sessions"],
    chat: [isObj(sizes.chat), "chat" in sizes, "sizes.chat"],
    clients: [Array.isArray(doc.clients) && doc.clients.length > 0, "clients" in doc, "clients"],
  };
  for (const [block, [complete, hasData, label]] of Object.entries(parts)) {
    const ran = blocks.includes(block) && !skippedBlocks.includes(block);
    if (!ran && (hasData || block in proto || runs.some((r) => r.size === block))) out.push(`${block} data is only allowed when the ${block} block ran`);
    else if (ran && status !== "failed" && !complete) out.push(`a result with the ${block} block needs ${label}`);
    if (ran && !sameFlat(proto[block], data.block_protocols[block])) out.push(`protocol.${block} is not standard-v2's`);
  }
}

// Two flat objects with equal values; a boolean never equals a number (Python's True == 1 would, JavaScript's does not).
function sameFlat(a, b) {
  return isObj(a) && Object.keys(a).length === Object.keys(b).length && Object.keys(b).every((k) => k in a && a[k] === b[k]);
}

export function problems(doc, data) {
  const out = check(doc, data.public, "", [], data);
  if (!isObj(doc)) return out;
  const schemaIds = data.schema_ids;
  const schemaKnown = Object.values(schemaIds).includes(doc.schema);
  if (!schemaKnown) out.push("schema is not strata-bench/v1 or strata-bench/v2");
  const proto = isObj(doc.protocol) ? doc.protocol : {};
  const pid = proto.id;
  if (!(typeof pid === "string" && Object.prototype.hasOwnProperty.call(schemaIds, pid))) out.push(`protocol ${pyRepr(pid)} is not public`);
  else if (schemaKnown && schemaIds[pid] !== doc.schema) out.push(`protocol ${pid} is written in ${schemaIds[pid]}, not ${doc.schema}`);
  versionProblems(doc, proto, data, out);
  if (isObj(proto.prompt_sha256)) {
    const known = pid === "standard-v2" ? { ...data.prompts, ...data.prompts_v2 } : data.prompts;
    for (const [name, h] of Object.entries(proto.prompt_sha256)) if (!Object.prototype.hasOwnProperty.call(known, name) || known[name] !== h) out.push(`prompt ${name} is not ${typeof pid === "string" ? pid : pyRepr(pid)}'s`);
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
  if (!data.statuses.includes(status)) out.push(`result is not complete (status ${pyRepr(status)})`);
  if (status === "failed") {
    if (!isObj(failure) || !data.failure_kinds.includes(failure.kind)) out.push("a failed result needs failure.kind from " + data.failure_kinds.join(", "));
    else if (!(typeof failure.where === "string" && new RegExp(data.failure_where_re).test(failure.where))) out.push("failure.where is not one of the protocol's call names");
  } else if (failure !== undefined && failure !== null) out.push("failure is only allowed on a failed result");
  for (const [block, key] of [["submission", "by"], ["machine", "machine_id"]]) {   // declared names: short, no free text
    const section = doc[block];
    if (isObj(section) && section[key] !== undefined && section[key] !== null && !(typeof section[key] === "string" && new RegExp("^(?:" + data.id_re + ")$").test(section[key]))) out.push(`${block}.${key} must be a short name (letters, digits, '.', '_' and '-')`);
  }
  const text = JSON.stringify(doc);
  for (const [re, what] of data.leaks) {
    const m = new RegExp(re).exec(text);
    if (m) out.push(`contains ${what}: ${pyRepr(m[0])}`);
  }
  return out;
}
