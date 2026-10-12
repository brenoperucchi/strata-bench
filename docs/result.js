"use strict";
// The detail page of one result. Everything from the file is written with textContent (never as HTML).
(function () {
  var SIZE_ORDER = ["probe", "gprobe150", "gprobe300", "gprobe450", "short", "medium", "long", "xlong"];
  var LATENCY_SIZES = ["probe", "gprobe150", "gprobe300", "gprobe450"];
  var SPEED_SIZES = ["short", "medium", "long", "xlong"];
  var $ = function (id) { return document.getElementById(id); };

  function el(tag, text, cls) {
    var e = document.createElement(tag);
    if (text !== undefined && text !== null) e.textContent = String(text);
    if (cls) e.className = cls;
    return e;
  }
  function svg(tag, attrs, text) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined) e.textContent = String(text);
    return e;
  }
  function put(parent) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.append(arguments[i]); return parent; }
  var num = function (v, d) { return v === null || v === undefined || isNaN(v) ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d === undefined ? 1 : d }); };
  var rng = function (s, d) { return s && s.median !== undefined && s.median !== null ? num(s.median, d) + (s.min !== undefined && s.max !== undefined && s.min !== null ? " (" + num(s.min, d) + " to " + num(s.max, d) + ")" : "") : "–"; };
  function median(a) { a = a.filter(function (x) { return typeof x === "number"; }).sort(function (x, y) { return x - y; }); if (!a.length) return null; var m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; }
  function stat(a) { a = a.filter(function (x) { return typeof x === "number"; }); return a.length ? { median: median(a), min: Math.min.apply(null, a), max: Math.max.apply(null, a) } : null; }

  function section(title, hint) {
    var s = el("section", null, "block");
    put(s, el("h2", title));
    if (hint) put(s, el("p", hint, "hint"));
    $("sections").append(s);
    return s;
  }
  function table(head, rows, numFrom) {
    var t = el("table"), thead = el("thead"), tr = el("tr");
    head.forEach(function (h, i) { var th = el("th", h); if (numFrom !== undefined && i >= numFrom) th.className = "num"; tr.append(th); });
    thead.append(tr); t.append(thead);
    var tb = el("tbody");
    rows.forEach(function (r) {
      var row = el("tr");
      r.forEach(function (c, i) { var td = el("td", c); if (numFrom !== undefined && i >= numFrom) td.className = "num"; row.append(td); });
      tb.append(row);
    });
    t.append(tb);
    var w = el("div", null, "scroll"); w.append(t);
    return w;
  }
  function kv(obj, keys) {
    var dl = el("dl", null, "kv"), n = 0;
    keys.forEach(function (k) {
      var v = obj ? obj[k[0]] : undefined;
      if (v === undefined || v === null || v === "") return;
      put(dl, el("dt", k[1]), el("dd", typeof v === "object" ? JSON.stringify(v) : v)); n++;
    });
    return n ? dl : null;
  }

  // a line chart with optional min/max whiskers: points = [{x, y, lo, hi, label}]
  function chart(points, xTitle, yTitle) {
    var W = 560, H = 250, L = 62, R = 16, T = 14, B = 46;
    var xs = points.map(function (p) { return p.x; }), ys = points.map(function (p) { return p.hi !== undefined ? p.hi : p.y; });
    var x0 = 0, x1 = Math.max.apply(null, xs) * 1.08, y1 = Math.max.apply(null, ys) * 1.12 || 1;
    var sx = function (v) { return L + (W - L - R) * (v - x0) / (x1 - x0 || 1); };
    var sy = function (v) { return T + (H - T - B) * (1 - v / y1); };
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": yTitle + " by " + xTitle, class: "chart" });
    for (var i = 0; i <= 4; i++) {
      var yv = y1 * i / 4;
      put(s, svg("line", { x1: L, x2: W - R, y1: sy(yv), y2: sy(yv), class: "grid" }), svg("text", { x: L - 8, y: sy(yv) + 4, "text-anchor": "end" }, num(yv, yv < 10 ? 1 : 0)));
    }
    points.forEach(function (p) { put(s, svg("text", { x: sx(p.x), y: H - B + 18, "text-anchor": "middle" }, num(p.x, 0))); });
    put(s, svg("text", { x: (L + W - R) / 2, y: H - 6, "text-anchor": "middle", class: "axis-title" }, xTitle), svg("text", { x: 14, y: (T + H - B) / 2, "text-anchor": "middle", transform: "rotate(-90 14 " + (T + H - B) / 2 + ")", class: "axis-title" }, yTitle));
    put(s, svg("polyline", { points: points.map(function (p) { return sx(p.x) + "," + sy(p.y); }).join(" "), class: "ln", fill: "none" }));
    points.forEach(function (p) {
      if (p.lo !== undefined && p.hi !== undefined) put(s, svg("line", { x1: sx(p.x), x2: sx(p.x), y1: sy(p.lo), y2: sy(p.hi), class: "whisker" }));
      var c = svg("circle", { cx: sx(p.x), cy: sy(p.y), r: 4, class: "pt" });
      put(c, svg("title", {}, p.label));
      s.append(c);
    });
    var w = el("div", null, "chart-wrap"); w.append(s);
    return w;
  }

  function runsOf(doc, size, kind) { return (doc.runs || []).filter(function (r) { return r.size === size && (kind ? r.kind === kind : true); }); }

  function render(doc, id) {
    var m = doc.machine || {}, e = doc.engine || {}, mo = doc.model || {}, cfg = doc.config || {}, val = doc.validity || {}, proto = doc.protocol || {};
    var gpus = (m.gpus || []).map(function (g) { return g.name + (g.vram_gb ? " " + g.vram_gb + " GB" : ""); }).join(", ");
    document.title = (doc.label || "result") + " · strata-bench";
    $("title").textContent = doc.label || "Result";
    var sub = $("subtitle"); sub.textContent = "";
    sub.append(document.createTextNode(((doc.submission || {}).date || "").slice(0, 10) + (e.version || doc.build_info ? " · " + (e.version || doc.build_info) : "")));
    if ((doc.submission || {}).by) { var a = el("a", doc.submission.by); a.href = "index.html?by=" + encodeURIComponent(doc.submission.by); put(sub, document.createTextNode(" · submitted by "), a, document.createTextNode(" (declared)")); }
    $("raw").href = id;
    $("status").textContent = "";

    var pill = el("span", val.status || "", "pill " + (val.status || ""));
    var cards = [[gpus || "–", "GPU"], [(mo.family || doc.model_path || "–") + (mo.quant ? " " + mo.quant : ""), "model"], [e.version || doc.build_info || "–", "engine"], [num(doc.n_ctx, 0), "context tokens"], [(cfg.config_origin || "–") + (cfg.config_origin_verified ? " (verified)" : ""), "config origin"]];
    cards.forEach(function (c) { var d = el("div", null, "card"); put(d, el("b", c[0]), el("span", c[1])); $("cards").append(d); });
    var sc = el("div", null, "card"); put(sc, put(el("b"), pill), el("span", (val.warnings || []).length ? (val.warnings.length + " warning(s)") : "status")); $("cards").append(sc);

    // 1. speeds with their ranges
    var sizes = SIZE_ORDER.filter(function (s) { return doc.sizes && doc.sizes[s]; });
    var s1 = section("Speeds by prompt size", "Median of the measured runs, with the lowest and highest run in parentheses. Prompt speed is the engine's own prompt processing rate; decode speed is the rate while generating.");
    s1.append(table(["Size", "Prompt tokens", "Reused", "Generated", "Prompt tok/s", "Decode tok/s", "Prompt ms", "Wall ms", "Runs"], sizes.map(function (sz) {
      var S = doc.sizes[sz], rs = runsOf(doc, sz, "run");
      return [sz, num(median(rs.map(function (r) { return (r.tokens || {}).prompt; })), 0), num(median(rs.map(function (r) { return (r.tokens || {}).reused; })), 0), num(median(rs.map(function (r) { return (r.tokens || {}).generated; })), 0),
        S.prompt_tps ? rng(S.prompt_tps, 0) : "–", S.decode_tps ? rng(S.decode_tps, 1) : "–", rng(S.prompt_ms, 0), rng(S.wall_ms, 0), rs.length];
    }), 1));

    // 2. latency by prompt length (one generated token: close to the time to first token)
    var lat = LATENCY_SIZES.filter(function (s) { return doc.sizes && doc.sizes[s] && doc.sizes[s].prompt_ms; }).map(function (s) {
      var rs = runsOf(doc, s, "run"), t = median(rs.map(function (r) { return (r.tokens || {}).prompt; })), P = doc.sizes[s].prompt_ms;
      return { x: t, y: P.median, lo: P.min, hi: P.max, label: s + ": " + num(P.median, 0) + " ms for " + num(t, 0) + " prompt tokens" };
    }).filter(function (p) { return p.x !== null; }).sort(function (a, b) { return a.x - b.x; });
    if (lat.length > 1) {
      var s2 = section("Latency by prompt length", "Short requests that generate a single token, so the time is almost all prompt processing: the wait before the first word of an answer. Dots are medians, bars run from the lowest to the highest request.");
      s2.append(chart(lat, "prompt tokens", "prompt time (ms)"));
    }

    // 3. prompt speed by prompt length
    var spd = SPEED_SIZES.filter(function (s) { return doc.sizes && doc.sizes[s] && doc.sizes[s].prompt_tps; }).map(function (s) {
      var t = median(runsOf(doc, s, "run").map(function (r) { return (r.tokens || {}).prompt; })), P = doc.sizes[s].prompt_tps;
      return { x: t, y: P.median, lo: P.min, hi: P.max, label: s + ": " + num(P.median, 0) + " tok/s at " + num(t, 0) + " prompt tokens" };
    }).filter(function (p) { return p.x !== null; }).sort(function (a, b) { return a.x - b.x; });
    if (spd.length > 1) {
      var s3 = section("Prompt speed by prompt length", "Longer prompts usually process faster per token, until memory or context gets in the way.");
      s3.append(chart(spd, "prompt tokens", "prompt tokens per second"));
    }

    // 4. speculative decoding
    var specRows = sizes.map(function (sz) {
      var rs = runsOf(doc, sz, "run"), d = 0, acc = 0;
      rs.forEach(function (r) { d += (r.timings || {}).draft_n || 0; acc += (r.timings || {}).draft_accepted || (r.timings || {}).draft_n_accepted || 0; });
      return d > 0 ? [sz, num(d, 0), num(acc, 0), num(100 * acc / d, 1) + " %"] : null;
    }).filter(Boolean);
    var s4 = section("Speculative decoding", "Tokens the draft proposed and how many the main model accepted. A higher acceptance rate means more tokens per step.");
    if (specRows.length) s4.append(table(["Size", "Drafted", "Accepted", "Acceptance"], specRows, 1));
    else put(s4, el("p", "No speculative decoding was recorded for this run.", "hint"));

    // 5. hardware, engine, model, calibration
    var s5 = section("Machine, engine and model", "The hardware fields are declared by the person who ran the benchmark; the engine and model fields come from the server or from the same declaration.");
    var grid = el("div", null, "grid3");
    var blocks = [["Machine (declared)", kv(Object.assign({ gpu: gpus }, m), [["gpu", "GPU"], ["cpu", "CPU"], ["cores", "Cores"], ["ram_gb", "RAM (GB)"], ["ram_type", "RAM type"], ["ram_speed_mts", "RAM speed (MT/s)"], ["os", "OS"], ["driver", "Driver"], ["cuda", "CUDA"], ["hip", "HIP"], ["cpu_isa", "CPU instructions"], ["machine_id", "Machine id"]])],
      ["Engine", kv(e, [["version", "Version"], ["commit", "Commit"], ["backend", "Backend"], ["build_info", "Build"], ["binary_sha256", "Binary SHA-256"], ["source", "Source"]])],
      ["Model", kv(mo, [["family", "Family"], ["quant", "Quantization"], ["file", "File"], ["n_ctx", "Context"], ["repo", "Repository"], ["storage", "Storage"], ["mtp", "MTP"], ["expert_profile", "Expert profile"]])]];
    blocks.forEach(function (b) { if (b[1]) { var d = el("div"); put(d, el("h3", b[0]), b[1]); grid.append(d); } });
    s5.append(grid);
    if (cfg.calibration) { var cal = kv(cfg.calibration, [["ran", "Calibration ran"], ["pool_workers_asked", "Pool workers asked"], ["pool_workers_resolved", "Pool workers resolved"], ["state", "State"], ["kept_params", "Kept parameters"]]); if (cal) put(s5, el("h3", "Calibration"), cal); }

    // 6. configuration
    var s6 = section("Configuration", "The server arguments as run, with every path replaced by its role. A result with a different configuration is a different measurement.");
    put(s6, kv(cfg, [["config_id", "Config id"], ["config_origin", "Origin"]]));
    if ((cfg.args || []).length) put(s6, el("h3", "Arguments"), el("pre", cfg.args.join(" ")));
    var envKeys = Object.keys(cfg.env || {});
    if (envKeys.length) put(s6, el("h3", "Environment"), el("pre", envKeys.map(function (k) { return k + "=" + cfg.env[k]; }).join("\n")));
    if (cfg.server && Object.keys(cfg.server).length) put(s6, el("h3", "Server settings"), el("pre", JSON.stringify(cfg.server, null, 1)));

    // 7. validity and protocol
    var s7 = section("Validity and protocol");
    put(s7, kv({ status: val.status, protocol: proto.id, warmup: proto.warmup, runs: proto.runs, max_tokens: proto.max_tokens, runner: (doc.runner || {}).name ? doc.runner.name + " " + doc.runner.version + (doc.runner.commit ? " (" + doc.runner.commit + ")" : "") : null },
      [["status", "Status"], ["protocol", "Protocol"], ["warmup", "Warm-up requests"], ["runs", "Measured runs"], ["max_tokens", "Max generated tokens"], ["runner", "Runner"]]));
    if (val.failure) put(s7, el("p", "Failure: " + val.failure.kind + " at " + val.failure.where, "pill failed"));
    if ((val.warnings || []).length) { var ul = el("ul", null, "warnlist"); val.warnings.forEach(function (w) { ul.append(el("li", w)); }); put(s7, el("h3", "Warnings"), ul); }
    var hashes = Object.keys(proto.prompt_sha256 || {});
    if (hashes.length) { var det = el("details"); put(det, el("summary", "Prompt hashes (SHA-256)"), el("pre", hashes.map(function (k) { return k + "  " + proto.prompt_sha256[k]; }).join("\n"))); put(s7, det); }

    // 8. every request
    var s8 = section("Every request", "One row per request the runner sent, warm-up requests included.");
    var all = el("details"); all.append(el("summary", "Show all " + (doc.runs || []).length + " requests"));
    var sel = el("select"); sel.append(el("option", "All sizes")); sel.firstChild.value = "";
    sizes.forEach(function (s) { var o = el("option", s); o.value = s; sel.append(o); });
    var holder = el("div");
    function draw() {
      var rs = (doc.runs || []).filter(function (r) { return !sel.value || r.size === sel.value; });
      holder.textContent = "";
      holder.append(table(["Size", "Kind", "#", "Prompt", "Reused", "Generated", "Prompt ms", "Prompt tok/s", "Decode tok/s", "Wall ms", "Finish", "Output"], rs.map(function (r) {
        var t = r.timings || {}, k = r.tokens || {};
        return [r.size, r.kind, r.index, num(k.prompt, 0), num(k.reused, 0), num(k.generated, 0), num(t.prompt_ms, 0), num(t.prompt_per_second, 0), num(t.predicted_per_second, 1), num(r.wall_ms, 0), r.finish_reason || "–", r.output_check || "–"];
      }), 3));
    }
    sel.addEventListener("change", draw);
    var lab = el("label", "Show "); lab.className = "inline"; lab.append(sel);
    put(all, lab, holder); s8.append(all); draw();
  }

  var id = null;
  try { id = new URLSearchParams(location.search).get("id"); } catch (e) { /* no URL support */ }
  if (!id || !/^results\/[A-Za-z0-9._\/-]+\.result\.json$/.test(id) || id.indexOf("..") >= 0) {
    $("subtitle").textContent = "Pick a result from the dashboard.";
    $("status").textContent = "No result was asked for. Open one from the dashboard's table.";
    return;
  }
  // Links to the neighbours in the dashboard's order (newest first) and to the comparison. Best effort: the page works without them.
  function neighbours() {
    fetch("data.json", { cache: "no-cache" }).then(function (r) { return r.json(); }).then(function (data) {
      var ids = (data.results || []).map(function (r) { return r.id; }), i = ids.indexOf(id), nav = document.querySelector("header nav");
      if (i < 0 || !nav) return;
      var add = function (text, href) { var a = el("a", text); a.href = href; nav.append(a); };
      if (i > 0) add("← Newer", "result.html?id=" + encodeURIComponent(ids[i - 1]));
      if (i < ids.length - 1) add("Older →", "result.html?id=" + encodeURIComponent(ids[i + 1]));
      if (ids.length > 1) add("Compare with another", "index.html?compare=" + encodeURIComponent(id));
    }).catch(function () { /* no neighbours */ });
  }
  fetch(id, { cache: "no-cache" }).then(function (r) { if (!r.ok) throw new Error("http " + r.status); return r.json(); }).then(function (doc) { render(doc, id); neighbours(); }).catch(function () {
    $("subtitle").textContent = "The result file could not be loaded.";
    $("status").textContent = "The file may have been removed, or the page was opened without its data. Go back to the dashboard.";
  });
})();
