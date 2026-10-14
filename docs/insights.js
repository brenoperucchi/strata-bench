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
  // The version number alone is not the engine: an official build and a fork can both say 0.1.43.1. The binary hash tells them apart when the result declares it.
  var buildOf = function (r) { return r.engine + (r.binary ? "#" + r.binary : ""); };
  // The model name is free text ("Qwen3.8-Flash-Next", "Swift Qwen3.8 Flash Next", "Swift (Qwen3.8-Flash-Next 125B MoE)", a bare "Swift"), so results are matched on a normalised name:
  // case, punctuation and the "Swift" prefix are ignored, and a note in parentheses is read only when nothing else names the model (its size and "MoE" are dropped).
  // A bare "Swift" is taken as Qwen3.8-Flash-Next, which is what the results that spell it out say it is.
  var BARE_SWIFT = "qwen3.8flashnext";
  var words = function (t) { return String(t).toLowerCase().split(/[^a-z0-9.]+/).filter(function (w) { return w && w !== "swift"; }); };
  var family = function (r) {
    var name = String(r.model_full || r.model || ""), note = (name.match(/\(([^)]*)\)/) || [])[1] || "";
    var main = words(name.replace(/\([^)]*\)/g, " ")).join("");
    if (main) return main;
    return words(note).filter(function (w) { return w !== "moe" && !/^\d+(\.\d+)?b$/.test(w); }).join("") || BARE_SWIFT;
  };
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
    return { kind: kind, why: why, machine: key, a: a, b: b, deltas: deltas, moved: moved.length, args: kind === "build" ? [] : argsDiff(a, b),
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
      builds[k].forEach(function (r) { var b = buildOf(r); if (!latest[b] || byDate(latest[b], r) < 0) latest[b] = r; });
      var engines = Object.keys(latest).sort(function (x, y) { return engineCmp(latest[x].engine, latest[y].engine) || byDate(latest[x], latest[y]); });
      for (var i = 1; i < engines.length; i++) {
        var p = pairOf("build", latest[engines[i - 1]], latest[engines[i]], keys[latest[engines[i]].id], "same configuration, other engine build");
        if (p) out.push(p);
      }
    });
    var configs = group(function (r) { return [keys[r.id], kindOf(r), buildOf(r)].join("#"); });
    Object.keys(configs).forEach(function (k) {
      var latest = {};
      configs[k].forEach(function (r) { if (!latest[r.config_id] || byDate(latest[r.config_id], r) < 0) latest[r.config_id] = r; });
      var list = Object.keys(latest).map(function (c) { return latest[c]; }).sort(byDate);
      for (var i = 1; i < list.length; i++) {
        var p = pairOf("config", list[i - 1], list[i], keys[list[i].id], "same engine, configuration changed");
        if (p) out.push(p);
      }
    });

    // Same machine and model, but the engine build and the configuration both changed: shown, flagged as not telling which of the two moved the numbers.
    var built = {};
    out.forEach(function (p) { if (p.kind === "build") built[keys[p.b.id] + "#" + kindOf(p.b) + "#" + buildOf(p.a) + ">" + buildOf(p.b)] = 1; });
    var machineKind = group(function (r) { return [keys[r.id], kindOf(r)].join("#"); });
    Object.keys(machineKind).forEach(function (k) {
      var latest = {};
      machineKind[k].forEach(function (r) { var b = buildOf(r); if (!latest[b] || byDate(latest[b], r) < 0) latest[b] = r; });
      var builds = Object.keys(latest).sort(function (x, y) { return engineCmp(latest[x].engine, latest[y].engine) || byDate(latest[x], latest[y]); });
      for (var i = 1; i < builds.length; i++) {
        var a = latest[builds[i - 1]], b = latest[builds[i]];
        if (a.config_id === b.config_id || built[keys[b.id] + "#" + kindOf(b) + "#" + builds[i - 1] + ">" + builds[i]]) continue;
        var p = pairOf("mixed", a, b, keys[b.id], "engine build and configuration both changed");
        if (p) out.push(p);
      }
    });
    return out.sort(function (p, q) { return -byDate(p.b, q.b); });
  }

  // How many machines back each (engine A to engine B) transition; below the floor the page says "insufficient data" instead of a verdict.
  function transitionSupport(list) {
    var t = {};
    list.filter(function (p) { return p.kind === "build"; }).forEach(function (p) {
      var k = buildOf(p.a) + " → " + buildOf(p.b); t[k] = t[k] || {}; t[k][p.machine] = 1;
    });
    var out = {};
    Object.keys(t).forEach(function (k) { out[k] = Object.keys(t[k]).length; });
    return out;
  }

  // One row per engine release step (build A to build B), gathered over every machine that measured it: the answer to "did the release change the speed".
  // Only the pairs that isolate the build count; the median of the changes is the release's number, and with fewer than MIN_MACHINES machines it stays an observation.
  function releases(list) {
    var t = {}, order = [];
    list.filter(function (p) { return p.kind === "build"; }).forEach(function (p) {
      var k = buildOf(p.a) + " → " + buildOf(p.b);
      if (!t[k]) { t[k] = { key: k, from: p.a, to: p.b, pairs: [], machines: {} }; order.push(k); }
      t[k].pairs.push(p); t[k].machines[p.machine] = 1;
      if (byDate(t[k].to, p.b) < 0) t[k].to = p.b;
    });
    return order.map(function (k) {
      var r = t[k], n = Object.keys(r.machines).length;
      var metrics = METRICS.map(function (m) {
        var points = [];
        r.pairs.forEach(function (p) { var d = p.deltas.filter(function (x) { return x.key === m.key; })[0]; if (d) points.push({ pct: d.pct, verdict: d.verdict, machine: p.machine, pair: p }); });
        if (!points.length) return { key: m.key, label: m.label, band: m.band, points: [], median: null, verdict: null };
        var v = points.map(function (q) { return q.pct; }).sort(function (x, y) { return x - y; }), h = Math.floor(v.length / 2);
        var median = Math.round((v.length % 2 ? v[h] : (v[h - 1] + v[h]) / 2) * 10) / 10;
        return { key: m.key, label: m.label, band: m.band, points: points, median: median, verdict: Math.abs(median) <= m.band ? "same" : median > 0 ? "up" : "down" };
      });
      var moved = metrics.filter(function (m) { return m.verdict && m.verdict !== "same"; }).length;
      return { key: k, from: r.from, to: r.to, machines: n, enough: n >= MIN_MACHINES, pairs: r.pairs.length, metrics: metrics, moved: moved };
    }).sort(function (a, b) { return -byDate(a.to, b.to); });
  }

  // The same model and quantisation under one protocol, on different machines: the closest thing to a hardware comparison the data allows.
  // The latest result of each machine is used, on the engine build most machines share, so the comparison is as controlled as the results allow;
  // the differences that remain (engine build, context size, configuration) are listed so they are never hidden.
  function sameModel(rows) {
    var good = rows.filter(usable), keys = machineKeys(good), groups = {};
    good.forEach(function (r) { var k = kindOf(r); (groups[k] = groups[k] || []).push(r); });
    var out = [];
    Object.keys(groups).forEach(function (k) {
      var byMachine = {};
      groups[k].forEach(function (r) {
        var m = keys[r.id], b = buildOf(r); byMachine[m] = byMachine[m] || {};
        if (!byMachine[m][b] || byDate(byMachine[m][b], r) < 0) byMachine[m][b] = r;
      });
      var machines = Object.keys(byMachine);
      if (machines.length < 2) return;
      var count = {}, newest = {};
      machines.forEach(function (m) {
        Object.keys(byMachine[m]).forEach(function (b) { var r = byMachine[m][b]; count[b] = (count[b] || 0) + 1; if (!newest[b] || byDate(newest[b], r) < 0) newest[b] = r; });
      });
      var order = Object.keys(count).sort(function (x, y) { return count[y] - count[x] || byDate(newest[y], newest[x]); });
      var list = machines.map(function (m) { return byMachine[m][order.filter(function (b) { return byMachine[m][b]; })[0]]; });
      var distinct = function (f) { return new Set(list.map(f)).size > 1; };
      var differs = [];
      if (distinct(buildOf)) differs.push("engine build");
      if (distinct(function (r) { return r.n_ctx; })) differs.push("context size");
      if (distinct(function (r) { return r.config_id; })) differs.push("configuration");
      var longTokens = list.map(function (r) { var ms = size(r, "long", "prompt_ms"), tps = size(r, "long", "prompt_tps"); return ms && tps ? ms * tps / 1000 : null; });
      var shown = list.map(function (r, i) {
        return { row: r, machine: keys[r.id], build: buildOf(r), prefill_medium: size(r, "medium", "prompt_tps"), prefill_long: size(r, "long", "prompt_tps"), decode_medium: size(r, "medium", "decode_tps"), long_seconds: size(r, "long", "prompt_ms") === null ? null : size(r, "long", "prompt_ms") / 1000, long_tokens: longTokens[i] };
      }).filter(function (m) { return m.prefill_medium !== null || m.decode_medium !== null; });
      if (shown.length < 2) return;
      shown.sort(function (a, b) { return (b.decode_medium || 0) - (a.decode_medium || 0); });
      var toks = shown.map(function (m) { return m.long_tokens; }).filter(Boolean);
      var engines = []; shown.forEach(function (m) { if (engines.indexOf(m.row.engine) < 0) engines.push(m.row.engine); });
      out.push({ model: list[0].model, model_full: list[0].model_full, quant: list[0].quant, engine: engines.join(", "), protocol: list[0].protocol, differs: differs, machines: shown, long_tokens: toks.length ? Math.round(Math.min.apply(null, toks) / 100) * 100 : null });
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

  var api = { buildOf: buildOf, kindOf: kindOf, METRICS: METRICS, MIN_MACHINES: MIN_MACHINES, machineKeys: machineKeys, engineCmp: engineCmp, pairs: pairs, transitionSupport: transitionSupport, releases: releases, sameModel: sameModel, speedPoints: speedPoints, summary: summary };
  root.StrataInsights = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
