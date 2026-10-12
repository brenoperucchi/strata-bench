"use strict";
// Side by side comparison: one column per result (up to MAX), one row per metric. Everything from the files is written with textContent.
(function () {
  var MAX = 3;
  var ID_RX = /^results\/[A-Za-z0-9._\/-]+\.result\.json$/;
  var SIZES = ["probe", "gprobe150", "gprobe300", "gprobe450", "chat", "short", "medium", "long", "xlong"];
  var $ = function (id) { return document.getElementById(id); };

  function el(tag, text, cls) { var e = document.createElement(tag); if (text !== undefined && text !== null) e.textContent = String(text); if (cls) e.className = cls; return e; }
  var isNum = function (v) { return typeof v === "number" && isFinite(v); };
  var num = function (v, d) { return isNum(v) ? v.toLocaleString(undefined, { maximumFractionDigits: d === undefined ? 1 : d }) : "–"; };
  function median(a) { a = a.filter(isNum).sort(function (x, y) { return x - y; }); if (!a.length) return null; var m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; }
  var runsOf = function (d, size) { return (d.runs || []).filter(function (r) { return r.size === size && r.kind === "run"; }); };
  var promptTokens = function (d, size) { return median(runsOf(d, size).map(function (r) { return (r.tokens || {}).prompt; })); };
  var sizeStat = function (d, size, field) { var s = (d.sizes || {})[size]; return s && s[field] && isNum(s[field].median) ? s[field] : null; };
  function accept(d, size) {
    var dr = 0, ac = 0;
    runsOf(d, size).forEach(function (r) { dr += (r.timings || {}).draft_n || 0; ac += (r.timings || {}).draft_n_accepted || 0; });
    return dr > 0 ? 100 * ac / dr : null;
  }
  var gpuText = function (d) { return ((d.machine || {}).gpus || []).map(function (g) { return g.name + (g.vram_gb ? " " + g.vram_gb + " GB" : ""); }).join(", ") || null; };

  // rows: {group, label, get(d) -> text | number | {median,min,max}, better: "high" | "low" | null, digits}
  function rows() {
    var R = [];
    var text = function (group, label, get) { R.push({ group: group, label: label, get: get, text: true }); };
    var G = "Result";
    text(G, "Date", function (d) { return ((d.submission || {}).date || "").slice(0, 10) || null; });
    text(G, "Submitted by (declared)", function (d) { return (d.submission || {}).by || null; });
    text(G, "Status", function (d) { return (d.validity || {}).status || null; });
    text(G, "Protocol", function (d) { var p = d.protocol || {}; return p.id ? p.id + (p.suite ? " · " + p.suite : "") : null; });
    text(G, "Client ran on", function (d) { return (d.runner || {}).host_relation || null; });
    G = "Machine (declared)";
    text(G, "GPU", gpuText);
    text(G, "CPU", function (d) { return (d.machine || {}).cpu || null; });
    text(G, "RAM", function (d) { var m = d.machine || {}; return m.ram_gb ? m.ram_gb + " GB" + (m.ram_type ? " " + m.ram_type : "") + (m.ram_speed_mts ? " " + m.ram_speed_mts + " MT/s" : "") : null; });
    text(G, "OS", function (d) { return (d.machine || {}).os || null; });
    text(G, "Driver", function (d) { return (d.machine || {}).driver || null; });
    text(G, "Machine id", function (d) { return (d.machine || {}).machine_id || null; });
    G = "Engine and model";
    text(G, "Engine", function (d) { return (d.engine || {}).version || d.build_info || null; });
    text(G, "Commit", function (d) { return (d.engine || {}).commit || null; });
    text(G, "Model", function (d) { return (d.model || {}).family || d.model_path || null; });
    text(G, "Quantization", function (d) { return (d.model || {}).quant || null; });
    text(G, "Context tokens", function (d) { return isNum(d.n_ctx) ? num(d.n_ctx, 0) : null; });
    G = "Configuration";
    text(G, "Config origin", function (d) { var c = d.config || {}; return c.config_origin ? c.config_origin + (c.config_origin_verified ? " (verified)" : "") : null; });
    text(G, "Config id", function (d) { return (d.config || {}).config_id || null; });
    text(G, "Server arguments", function (d) { return ((d.config || {}).args || []).length ? d.config.args.length + " arguments" : null; });
    SIZES.forEach(function (s) {
      var g = "Speeds: " + s;
      R.push({ group: g, label: "Prompt tokens", get: function (d) { return promptTokens(d, s); }, digits: 0 });
      R.push({ group: g, label: "Prompt tok/s", get: function (d) { return sizeStat(d, s, "prompt_tps"); }, better: "high", digits: 0 });
      R.push({ group: g, label: "Decode tok/s", get: function (d) { return sizeStat(d, s, "decode_tps"); }, better: "high", digits: 1 });
      R.push({ group: g, label: "Prompt time (ms)", get: function (d) { return sizeStat(d, s, "prompt_ms"); }, better: "low", digits: 0 });
      R.push({ group: g, label: "First token (ms)", get: function (d) { return sizeStat(d, s, "ttft_ms"); }, better: "low", digits: 0 });
      R.push({ group: g, label: "Speculative acceptance (%)", get: function (d) { return accept(d, s); }, better: "high", digits: 1 });
    });
    var agent = function (d, f) { var a = d.agent || {}; return a[f] && isNum(a[f].median) ? a[f] : null; };
    var turnStat = function (t, f) { return function (d) { var T = ((d.agent || {}).turns || {})[t]; return T && T[f] && isNum(T[f].median) ? T[f] : null; }; };
    var lastTurn = function (d) { var k = Object.keys((d.agent || {}).turns || {}).sort(); return k.length ? k[k.length - 1] : null; };
    R.push({ group: "Agent session", label: "Cache hit (%)", get: function (d) { return agent(d, "hit_pct"); }, better: "high", digits: 1 });
    R.push({ group: "Agent session", label: "Tokens read again", get: function (d) { return agent(d, "reread_tokens"); }, better: "low", digits: 0 });
    R.push({ group: "Agent session", label: "Turn 1: first token (ms)", get: turnStat("1", "ttft_ms"), better: "low", digits: 0 });
    R.push({ group: "Agent session", label: "Last turn: tokens read", get: function (d) { var t = lastTurn(d); return t ? turnStat(t, "prompt_n")(d) : null; }, better: "low", digits: 0 });
    R.push({ group: "Agent session", label: "Last turn: first token (ms)", get: function (d) { var t = lastTurn(d); return t ? turnStat(t, "ttft_ms")(d) : null; }, better: "low", digits: 0 });
    [2, 4, 8].forEach(function (n) {
      var row = function (d) { return ((d.clients || []).filter(function (c) { return c.n === n; })[0]) || null; };
      var g = n + " clients at once";
      R.push({ group: g, label: "Total tokens per second", get: function (d) { var c = row(d); return c && isNum(c.total_tps) ? c.total_tps : null; }, better: "high", digits: 1 });
      R.push({ group: g, label: "First token p95 (ms)", get: function (d) { var c = row(d); return c && isNum(c.ttft_p95_ms) ? c.ttft_p95_ms : null; }, better: "low", digits: 0 });
      R.push({ group: g, label: "Latency p95 (ms)", get: function (d) { var c = row(d); return c && isNum(c.latency_p95_ms) ? c.latency_p95_ms : null; }, better: "low", digits: 0 });
      R.push({ group: g, label: "Requests turned away", get: function (d) { var c = row(d); return c && isNum(c.errors) ? c.errors : null; }, better: "low", digits: 0 });
    });
    return R;
  }

  var med = function (v) { return v && typeof v === "object" ? v.median : v; };
  function cell(row, v) {
    if (v === null || v === undefined) return "–";
    if (row.text) return String(v);
    if (typeof v === "object") return num(v.median, row.digits) + (isNum(v.min) && isNum(v.max) ? " (" + num(v.min, row.digits) + " to " + num(v.max, row.digits) + ")" : "");
    return num(v, row.digits);
  }

  function build(docs, ids) {
    var wrap = $("table-wrap"); wrap.textContent = "";
    var t = el("table", null, "compare"), thead = el("thead"), hr = el("tr");
    hr.append(el("th", "Metric"));
    docs.forEach(function (d, i) {
      var th = el("th", null, "colhead"), a = el("a", d.label || "result"); a.href = "result.html?id=" + encodeURIComponent(ids[i]);
      th.append(a);
      var rest = ids.filter(function (_, j) { return j !== i; });
      var x = el("a", "remove", "remove"); x.href = rest.length ? "compare.html?ids=" + rest.map(encodeURIComponent).join(",") : "index.html"; x.title = "Take this column out";
      th.append(el("br"), el("span", i === 0 ? "baseline" : "vs baseline", "hint"), document.createTextNode(" · "), x);
      hr.append(th);
    });
    thead.append(hr); t.append(thead);
    var tb = el("tbody"), lastGroup = null, shown = 0;
    var only = $("only-diff").checked;
    rows().forEach(function (row) {
      var vals = docs.map(row.get);
      if (vals.every(function (v) { return v === null || v === undefined; })) return;      // nobody has this metric
      var cells = vals.map(function (v) { return cell(row, v); });
      var allSame = cells.every(function (c) { return c === cells[0]; });
      if (only && allSame) return;
      if (row.group !== lastGroup) { var gr = el("tr", null, "group"), gt = el("td", row.group); gt.colSpan = docs.length + 1; gr.append(gt); tb.append(gr); lastGroup = row.group; }
      var tr = el("tr"); tr.append(el("th", row.label, "rowhead"));
      var nums = vals.map(med), best = null;
      if (row.better && nums.filter(isNum).length > 1 && !allSame) { var ok = nums.filter(isNum); best = row.better === "high" ? Math.max.apply(null, ok) : Math.min.apply(null, ok); }
      cells.forEach(function (c, i) {
        var td = el("td", c, row.text ? "" : "num");
        if (best !== null && isNum(nums[i]) && nums[i] === best) td.className += " best";
        if (!row.text && i > 0 && isNum(nums[i]) && isNum(nums[0]) && nums[0] !== 0 && nums[i] !== nums[0]) {
          var pct = 100 * (nums[i] - nums[0]) / nums[0];
          td.append(document.createElement("br"), el("span", (pct > 0 ? "+" : "") + num(pct, 1) + " %", "delta"));
        }
        tr.append(td);
      });
      tb.append(tr); shown++;
    });
    if (!shown) { var er = el("tr"), ec = el("td", "No row differs."); ec.colSpan = docs.length + 1; er.append(ec); tb.append(er); }
    t.append(tb); wrap.append(t);
  }

  function verdict(docs) {
    var checks = [["model", function (d) { return ((d.model || {}).family || d.model_path || "") + " " + ((d.model || {}).quant || ""); }], ["context", function (d) { return d.n_ctx; }],
      ["engine", function (d) { return (d.engine || {}).version || d.build_info; }], ["configuration", function (d) { return (d.config || {}).config_id; }],
      ["GPU", gpuText], ["protocol", function (d) { var p = d.protocol || {}; return (p.id || "") + " " + (p.suite || ""); }], ["client location", function (d) { return (d.runner || {}).host_relation || ""; }]];
    var differ = checks.filter(function (c) { var v = docs.map(c[1]).map(String); return v.some(function (x) { return x !== v[0]; }); }).map(function (c) { return c[0]; });
    var box = $("verdict"); box.textContent = "";
    box.append(el("p", differ.length ? "These results differ in: " + differ.join(", ") + ". Where the setup differs, the speeds are different measurements, not a ranking of the same thing." : "Same GPU, model, context, engine, configuration and protocol. The differences below are between runs.", "note"));
  }

  var ids = [];
  try { ids = (new URLSearchParams(location.search).get("ids") || "").split(",").filter(Boolean); } catch (e) { /* no URL support */ }
  var seen = {}; ids = ids.filter(function (i) { if (seen[i]) return false; seen[i] = 1; return true; });
  if (ids.length < 1 || ids.length > MAX || ids.some(function (i) { return !ID_RX.test(i) || i.indexOf("..") >= 0; })) {
    $("subtitle").textContent = "Pick results to compare from the dashboard.";
    $("status").textContent = ids.length > MAX ? "Up to " + MAX + " results can be compared at once." : "No valid results were asked for. Tick two or three rows on the dashboard and press Compare.";
    return;
  }
  Promise.all(ids.map(function (id) { return fetch(id, { cache: "no-cache" }).then(function (r) { if (!r.ok) throw new Error("http " + r.status); return r.json(); }); })).then(function (docs) {
    $("subtitle").textContent = docs.length + (docs.length === 1 ? " result" : " results") + ", one column each.";
    $("legend").hidden = false; $("diff-toggle").hidden = docs.length < 2;
    document.title = "Compare " + docs.map(function (d) { return d.label || "result"; }).join(" vs ") + " · strata-bench";
    try { $("only-diff").checked = new URLSearchParams(location.search).get("diff") === "1"; } catch (e) { /* no URL support */ }
    verdict(docs); build(docs, ids);
    $("only-diff").addEventListener("change", function () {
      build(docs, ids);
      try { var q = new URLSearchParams(location.search); if ($("only-diff").checked) q.set("diff", "1"); else q.delete("diff"); history.replaceState(null, "", location.pathname + "?" + q.toString().replace(/%2C/g, ",").replace(/%2F/g, "/")); } catch (e) { /* file:// */ }
    });
  }).catch(function () {
    $("subtitle").textContent = "A result file could not be loaded.";
    $("status").textContent = "One of the files may have been removed. Go back to the dashboard and pick again.";
  });
})();
