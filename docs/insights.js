"use strict";
// What the overview page says about the results: which results come from the same machine, what changed between two builds or two configs of it, and how machines compare when they ran the same model and engine.
// Pure functions over the rows of data.json (no DOM), so the same code runs in the page and in the tests.
(function (root) {
  // A change smaller than the band is inside the run-to-run spread seen on the reference machine: prompt speed moves up to about 5%, decode up to about 10% (speculative decoding accepts a different number of tokens each run).
  var METRICS = [
    { key: "prefill_medium", label: "Prompt speed, medium", size: "medium", field: "prompt_tps", band: 5 },
    { key: "prefill_long", label: "Prompt speed, long", size: "long", field: "prompt_tps", band: 5 },
    { key: "decode_medium", label: "Decode speed, medium", size: "medium", field: "decode_tps", band: 10 },
    { key: "decode_long", label: "Decode speed, long", size: "long", field: "decode_tps", band: 10 }
  ];
  var MIN_MACHINES = 3;     // a statement about a release needs this many machines (the community proposal's own floor)

  var num = function (v) { return typeof v === "number" && isFinite(v) ? v : null; };
  var size = function (r, name, field) { return r && r.sizes && r.sizes[name] ? num(r.sizes[name][field]) : null; };

  // Rows of one machine share a declared machine_id; a row that declares none is matched to a machine with the same GPU, CPU and RAM.
  function machineKeys(rows) {
    var hw = function (r) { return [r.gpu, r.cpu, r.ram_gb].join("|"); };
    var idOf = {};
    rows.forEach(function (r) { if (r.machine_id && !idOf[hw(r)]) idOf[hw(r)] = r.machine_id; });
    var out = {};
    rows.forEach(function (r) { out[r.id] = r.machine_id ? "id:" + r.machine_id : idOf[hw(r)] ? "id:" + idOf[hw(r)] : "hw:" + hw(r); });
    return out;
  }

  // "0.1.43.1" > "0.1.42": numeric parts compared in order
  function engineCmp(a, b) {
    var x = String(a).match(/\d+/g) || [], y = String(b).match(/\d+/g) || [];
    for (var i = 0; i < Math.max(x.length, y.length); i++) { var d = (+x[i] || 0) - (+y[i] || 0); if (d) return d; }
    return 0;
  }
  var byDate = function (a, b) { return String(a.date || "").localeCompare(String(b.date || "")); };
  var usable = function (r) { return r.status !== "failed"; };
  // The model name is free text ("Swift", "Swift Qwen3.8 Flash Next", "Swift (… 125B MoE)"), so results are matched on the family word and the quant.
  var family = function (r) { return String(r.model_full || r.model || "").trim().split(/[\s(]+/)[0].toLowerCase(); };
  var kindOf = function (r) { return [r.protocol, family(r), r.quant].join("|"); };

  function delta(a, b, m) {
    var x = size(a, m.size, m.field), y = size(b, m.size, m.field);
    if (x === null || y === null || x <= 0) return null;
    var pct = Math.round((y / x - 1) * 1000) / 10;                       // one decimal, as shown: +5.0% is on the band, not past it
    return { key: m.key, label: m.label, band: m.band, from: x, to: y, pct: pct, verdict: Math.abs(pct) <= m.band ? "same" : pct > 0 ? "up" : "down" };
  }
  // The server arguments as {option: value}; an option without a value counts as true. Paths are already replaced by their role in the file.
  function argMap(args) {
    var m = {}, last = null;
    (Array.isArray(args) ? args : []).forEach(function (t) {
      t = String(t);
      if (t.indexOf("--") === 0) { last = t; m[t] = true; } else if (last !== null) { m[last] = t; last = null; }
    });
    return m;
  }
  function argsDiff(a, b) {
    var x = argMap(a.args), y = argMap(b.args), out = [];
    Object.keys(y).sort().forEach(function (k) { if (!(k in x)) out.push({ opt: k, from: null, to: y[k] }); else if (x[k] !== y[k]) out.push({ opt: k, from: x[k], to: y[k] }); });
    Object.keys(x).sort().forEach(function (k) { if (!(k in y)) out.push({ opt: k, from: x[k], to: null }); });
    return out;
  }
  function pairOf(kind, a, b, key, why) {
    var deltas = METRICS.map(function (m) { return delta(a, b, m); }).filter(Boolean);
    if (!deltas.length) return null;
    var moved = deltas.filter(function (d) { return d.verdict !== "same"; });
    return { kind: kind, why: why, machine: key, a: a, b: b, deltas: deltas, moved: moved.length, args: kind === "config" ? argsDiff(a, b) : [],
      headline: moved.length ? moved.length + " of " + deltas.length + " measures moved past the noise band" : "inside the noise band" };
  }

  // Same machine, same model, protocol and config, different engine build: "did the new build change the speed".
  // Same machine, same model, protocol and engine, different config: "did the config change help".
  function pairs(rows) {
    var good = rows.filter(usable), keys = machineKeys(good), out = [];
    var group = function (fn) { var g = {}; good.forEach(function (r) { var k = fn(r); (g[k] = g[k] || []).push(r); }); return g; };
    var builds = group(function (r) { return [keys[r.id], kindOf(r), r.config_id].join("#"); });
    Object.keys(builds).forEach(function (k) {
      var latest = {};
      builds[k].forEach(function (r) { if (!latest[r.engine] || byDate(latest[r.engine], r) < 0) latest[r.engine] = r; });
      var engines = Object.keys(latest).sort(engineCmp);
      for (var i = 1; i < engines.length; i++) {
        var p = pairOf("build", latest[engines[i - 1]], latest[engines[i]], keys[latest[engines[i]].id], "same configuration, new engine build");
        if (p) out.push(p);
      }
    });
    var configs = group(function (r) { return [keys[r.id], kindOf(r), r.engine].join("#"); });
    Object.keys(configs).forEach(function (k) {
      var latest = {};
      configs[k].forEach(function (r) { if (!latest[r.config_id] || byDate(latest[r.config_id], r) < 0) latest[r.config_id] = r; });
      var list = Object.keys(latest).map(function (c) { return latest[c]; }).sort(byDate);
      for (var i = 1; i < list.length; i++) {
        var p = pairOf("config", list[i - 1], list[i], keys[list[i].id], "same engine, configuration changed");
        if (p) out.push(p);
      }
    });
    return out.sort(function (p, q) { return -byDate(p.b, q.b); });
  }

  // How many machines back each (engine A to engine B) transition; below the floor the page says "insufficient data" instead of a verdict.
  function transitionSupport(list) {
    var t = {};
    list.filter(function (p) { return p.kind === "build"; }).forEach(function (p) {
      var k = p.a.engine + " → " + p.b.engine; t[k] = t[k] || {}; t[k][p.machine] = 1;
    });
    var out = {};
    Object.keys(t).forEach(function (k) { out[k] = Object.keys(t[k]).length; });
    return out;
  }

  // The same model on the same engine build, on different machines: the closest thing to a hardware comparison the data allows.
  // The latest result of each machine is used; the differences that remain (context size, configuration) are listed so they are never hidden.
  function sameModel(rows) {
    var good = rows.filter(usable), keys = machineKeys(good), groups = {};
    good.forEach(function (r) { var k = [kindOf(r), r.engine].join("#"); (groups[k] = groups[k] || []).push(r); });
    var out = [];
    Object.keys(groups).forEach(function (k) {
      var latest = {};
      groups[k].forEach(function (r) { var m = keys[r.id]; if (!latest[m] || byDate(latest[m], r) < 0) latest[m] = r; });
      var list = Object.keys(latest).map(function (m) { return latest[m]; });
      if (list.length < 2) return;
      var differs = [];
      if (new Set(list.map(function (r) { return r.n_ctx; })).size > 1) differs.push("context size");
      if (new Set(list.map(function (r) { return r.config_id; })).size > 1) differs.push("configuration");
      var longTokens = list.map(function (r) { var ms = size(r, "long", "prompt_ms"), tps = size(r, "long", "prompt_tps"); return ms && tps ? ms * tps / 1000 : null; });
      var shown = list.map(function (r, i) {
        return { row: r, machine: keys[r.id], prefill_medium: size(r, "medium", "prompt_tps"), prefill_long: size(r, "long", "prompt_tps"), decode_medium: size(r, "medium", "decode_tps"), long_seconds: size(r, "long", "prompt_ms") === null ? null : size(r, "long", "prompt_ms") / 1000, long_tokens: longTokens[i] };
      }).filter(function (m) { return m.prefill_medium !== null || m.decode_medium !== null; });
      if (shown.length < 2) return;
      shown.sort(function (a, b) { return (b.decode_medium || 0) - (a.decode_medium || 0); });
      var toks = shown.map(function (m) { return m.long_tokens; }).filter(Boolean);
      out.push({ model: list[0].model, model_full: list[0].model_full, quant: list[0].quant, engine: list[0].engine, protocol: list[0].protocol, differs: differs, machines: shown, long_tokens: toks.length ? Math.round(Math.min.apply(null, toks) / 100) * 100 : null });
    });
    return out.sort(function (a, b) { return b.machines.length - a.machines.length; });
  }

  // One point per result: prompt speed on the medium prompt against decode speed.
  function speedPoints(rows) {
    var good = rows.filter(usable), keys = machineKeys(good);
    return good.map(function (r) { return { row: r, machine: keys[r.id], x: size(r, "medium", "prompt_tps"), y: size(r, "medium", "decode_tps") }; })
      .filter(function (p) { return p.x !== null && p.y !== null; });
  }

  // Counts for the strip at the top.
  function summary(rows) {
    var good = rows.filter(usable), keys = machineKeys(rows), set = function (f) { var s = {}; rows.forEach(function (r) { var v = f(r); if (v) s[v] = 1; }); return Object.keys(s); };
    var engines = set(function (r) { return r.engine; }).sort(engineCmp);
    var latest = rows.slice().sort(byDate).pop();
    return {
      results: rows.length, failed: rows.length - good.length,
      machines: set(function (r) { return keys[r.id]; }).length, gpus: set(function (r) { return r.gpu; }).length,
      engines: engines, contributors: set(function (r) { return r.by; }).sort(), latest: latest || null,
      protocols: { v1: rows.filter(function (r) { return r.protocol === "standard-v1"; }).length, v2: rows.filter(function (r) { return r.protocol === "standard-v2"; }).length }
    };
  }

  var api = { METRICS: METRICS, MIN_MACHINES: MIN_MACHINES, machineKeys: machineKeys, engineCmp: engineCmp, pairs: pairs, transitionSupport: transitionSupport, sameModel: sameModel, speedPoints: speedPoints, summary: summary };
  root.StrataInsights = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
