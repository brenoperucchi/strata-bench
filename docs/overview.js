"use strict";
// The overview at the top of the dashboard: summary strip, speed map, same-machine changes, same-model comparison.
// Everything is returned as HTML text; every value that comes from a result file goes through esc() or fmt() first.
(function (root) {
  var I = root.StrataInsights || (typeof require !== "undefined" ? require("./insights.js") : null);
  var esc = function (s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var fmt = function (v, d) { return v === null || v === undefined ? "–" : (typeof v === "number" && isFinite(v) ? v.toLocaleString(undefined, { maximumFractionDigits: d === undefined ? 1 : d }) : esc(v)); };
  var PALETTE = 6;
  var link = function (id) { return "result.html?id=" + encodeURIComponent(id); };
  var signed = function (p) { return (p > 0 ? "+" : p < 0 ? "−" : "") + Math.abs(p).toFixed(1) + "%"; };

  // One colour and one short name per machine, in the same order everywhere on the page.
  function machines(rows) {
    var keys = I.machineKeys(rows), first = {}, order = [];
    rows.forEach(function (r) { var k = keys[r.id]; if (!first[k]) { first[k] = r; order.push(k); } });
    order.sort();
    var count = {}; order.forEach(function (k) { count[first[k].gpu] = (count[first[k].gpu] || 0) + 1; });
    var out = {};
    order.forEach(function (k, i) {
      var r = first[k];
      out[k] = { cls: "c" + (i % PALETTE), label: String(r.gpu || "GPU") + (count[r.gpu] > 1 ? " · " + String(r.machine_id || k.replace(/^\w+:/, "")) : "") };
    });
    return out;
  }

  function kpi(rows) {
    var s = I.summary(rows), latest = s.latest;
    var eng = s.engines.length > 1 ? s.engines[0] + " to " + s.engines[s.engines.length - 1] : s.engines[0] || "";
    var who = s.contributors.map(function (n) { return "<a href='?by=" + encodeURIComponent(n) + "'>" + esc(n) + "</a>"; }).join(", ");
    var items = [
      [fmt(s.results, 0), "results", fmt(s.protocols.v1, 0) + " standard-v1 · " + fmt(s.protocols.v2, 0) + " standard-v2"],
      [fmt(s.machines, 0), "machines", fmt(s.gpus, 0) + " GPU model" + (s.gpus === 1 ? "" : "s")],
      [fmt(s.engines.length, 0), "engine versions", esc(eng)],
      [fmt(s.contributors.length, 0), "contributors", who || "no name declared yet"],
      [latest ? esc(String(latest.date || "").slice(0, 10)) : "–", "latest", latest ? esc(latest.gpu) + " · " + esc(latest.engine) : ""],
      [fmt(s.failed, 0), "failed runs", "listed in the Health tab"]
    ];
    return items.map(function (i) { return '<div class="card kpi"><b>' + i[0] + "</b><span>" + i[1] + "</span><small>" + i[2] + "</small></div>"; }).join("");
  }

  var niceStep = function (v) { var p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; };

  // The axes the map opens with start at zero; fit() gives the ones that just hold the data, so a cluster of results can be pulled apart.
  var numOr = function (v) { return typeof v === "number" && isFinite(v) ? v : null; };
  function fit(rows) {
    var pts = I.speedPoints(rows);
    if (!pts.length) return null;
    var span = function (vals) {
      var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), pad = Math.max((hi - lo) * 0.1, hi * 0.02, 1e-6), step = niceStep((hi - lo + 2 * pad) / 6);
      return [Math.max(0, Math.floor((lo - pad) / step) * step), Math.ceil((hi + pad) / step) * step];
    };
    var x = span(pts.map(function (p) { return p.x; })), y = span(pts.map(function (p) { return p.y; }));
    return { xmin: x[0], xmax: x[1], ymin: y[0], ymax: y[1] };
  }

  // The map is drawn at the width its card has (in CSS pixels, so the text stays at its size): wide on a desktop, narrow on a phone, larger still when expanded.
  // view = {xmin, xmax, ymin, ymax}: any of them may be missing (zero, or room for the data); results outside the range are left out and counted.
  function speedMap(rows, width, view) {
    var all = I.speedPoints(rows);
    if (!all.length) return "";
    view = view || {};
    var W = Math.max(320, Math.min(1400, Math.round(width) || 520)), H = Math.max(260, Math.min(520, Math.round(W * 0.36)));
    var mc = machines(rows), L = 58, R = 28, T = 12, B = 46;
    var axis = function (key, grow) {
      var lo = numOr(view[key + "min"]), hi = numOr(view[key + "max"]), top = Math.max.apply(null, all.map(function (p) { return p[key]; })) * grow;
      lo = lo === null ? 0 : lo;
      var open = hi === null || hi <= lo;                          // no usable upper end: leave room for the data
      if (open) hi = Math.max(top, lo + 1e-6);
      var step = niceStep((hi - lo) / 4);
      return { min: lo, max: open ? lo + Math.ceil((hi - lo) / step) * step : hi, step: step };
    };
    var ax = axis("x", 1.06), ay = axis("y", 1.12);
    var xmin = ax.min, xmax = ax.max, sx = ax.step, ymin = ay.min, ymax = ay.max, sy = ay.step;
    var pts = all.filter(function (p) { return p.x >= xmin && p.x <= xmax && p.y >= ymin && p.y <= ymax; });
    var X = function (v) { return L + (W - L - R) * (v - xmin) / (xmax - xmin); }, Y = function (v) { return H - B - (H - B - T) * (v - ymin) / (ymax - ymin); };
    var g = "";
    for (var yv = Math.ceil(ymin / sy - 1e-9) * sy + 0; yv <= ymax + 1e-9; yv += sy) g += "<line class='grid' x1='" + L + "' x2='" + (W - R) + "' y1='" + Y(yv) + "' y2='" + Y(yv) + "'/><text x='" + (L - 8) + "' y='" + (Y(yv) + 4) + "' text-anchor='end'>" + fmt(yv, 0) + "</text>";
    for (var xv = Math.ceil(xmin / sx - 1e-9) * sx + 0; xv <= xmax + 1e-9; xv += sx) g += "<text x='" + X(xv) + "' y='" + (H - B + 18) + "' text-anchor='middle'>" + fmt(xv, 0) + "</text>";
    g += "<text class='axis-title' x='" + (L + W - R) / 2 + "' y='" + (H - 6) + "' text-anchor='middle'>prompt speed, medium prompt (tokens per second)</text>" +
      "<text class='axis-title' x='14' y='" + (T + H - B) / 2 + "' text-anchor='middle' transform='rotate(-90 14 " + (T + H - B) / 2 + ")'>decode speed (tokens per second)</text>";
    pts.forEach(function (p) {
      var r = p.row, m = mc[p.machine], cx = X(p.x), cy = Y(p.y), v2 = r.protocol === "standard-v2";
      var shape = v2 ? "<rect class='dot " + m.cls + "' x='" + (cx - 6) + "' y='" + (cy - 6) + "' width='12' height='12' transform='rotate(45 " + cx + " " + cy + ")'/>" : "<circle class='dot " + m.cls + "' cx='" + cx + "' cy='" + cy + "' r='6.5'/>";
      g += "<a href='" + esc(link(r.id)) + "'>" + shape + "<title>" + esc(m.label + " · " + (r.model || "") + " " + (r.quant || "") + " · engine " + r.engine + " · " + r.protocol + (r.by ? " · by " + r.by : "")) + "\n" + fmt(p.x, 0) + " tok/s prompt, " + fmt(p.y, 1) + " tok/s decode</title></a>";
    });
    var clusters = {};
    pts.forEach(function (p) { var c = clusters[p.machine] = clusters[p.machine] || { n: 0, x: 0, y: 0 }; c.n++; c.x += X(p.x); c.y += Y(p.y); });
    var boxes = [];                                                  // a label is drawn only where it overlaps no other; the legend names every machine anyway
    Object.keys(clusters).sort().forEach(function (k) {
      var c = clusters[k], cx = c.x / c.n, cy = c.y / c.n, w = mc[k].label.length * 7 + 8;
      [cy - 16, cy + 26].some(function (ty) {
        var b = { x0: cx - w / 2, x1: cx + w / 2, y0: ty - 12, y1: ty + 4 };
        if (b.x0 < 0 || b.x1 > W || boxes.some(function (o) { return b.x0 < o.x1 && b.x1 > o.x0 && b.y0 < o.y1 && b.y1 > o.y0; })) return false;
        boxes.push(b);
        g += "<text class='tag " + mc[k].cls + "' x='" + cx + "' y='" + ty + "' text-anchor='middle'>" + esc(mc[k].label) + "</text>";
        return true;
      });
    });
    var used = {}; pts.forEach(function (p) { used[p.machine] = (used[p.machine] || 0) + 1; });
    var legend = Object.keys(used).sort().map(function (k) { return "<span class='key'><i class='sw " + mc[k].cls + "'></i>" + esc(mc[k].label) + "</span>"; }).join("");
    var shapes = "<span class='key'><i class='sw round'></i>standard-v1</span><span class='key'><i class='sw diamond'></i>standard-v2</span>";
    var field = function (key, name, ph) { return "<input type='number' step='any' min='0' data-axis='" + key + "' aria-label='" + name + "' placeholder='" + ph + "' value='" + (numOr(view[key]) === null ? "" : esc(view[key])) + "'>"; };
    var controls = "<div class='axes'><span>Prompt speed (x) " + field("xmin", "x axis from", "0") + " to " + field("xmax", "x axis to", "auto") + "</span><span>Decode speed (y) " + field("ymin", "y axis from", "0") + " to " + field("ymax", "y axis to", "auto") + "</span>" +
      "<button type='button' data-fit>Fit to data</button><button type='button' data-reset>Reset</button></div>";
    var outside = all.length - pts.length ? "<p class='hint'>" + (all.length - pts.length) + " of " + all.length + " results are outside this range. <b>Reset</b> shows them all.</p>" : "";
    return "<button type='button' class='expand' data-expand aria-expanded='false' title='Show the speed map larger'>Expand</button><h2>Speed map</h2><p class='hint'>One point per result. Right is faster at reading a prompt, up is faster at writing the answer. Click a point for the full result. Set the axes, or use Fit to data, to pull a crowded cluster apart.</p>" + controls +
      "<svg class='viz' viewBox='0 0 " + W + " " + H + "' role='img' aria-label='Decode speed against prompt speed, one point per result'>" + g + "</svg>" +
      "<div class='legend'>" + legend + "</div><div class='legend'>" + shapes + "</div>" +
      outside + "<p class='hint'>A map of what was measured, not a ranking: the points differ in model, quantisation, engine and configuration.</p>";
  }

  var DOMAIN = 20;                                           // the strips show -20% to +20%; a larger change sits at the edge and its value is written out
  var CAP = 5;                                               // rows shown before "Show N more"
  var short = function (label) { return String(label).replace(" speed, ", " "); };
  var proto = function (r) { return String(r.protocol || "").replace(/^standard-/, ""); };
  // the version, and the binary when two builds share one version number
  var tag = function (r, other) { return esc(r.engine) + (other && other.engine === r.engine && r.binary ? " <small>[" + esc(r.binary) + "]</small>" : ""); };
  // "0.1.43.1 → 0.1.44"; two builds that share a version number are told apart on a second line by their binary
  var arrow = function (p) {
    var same = p.a.engine === p.b.engine && (p.a.binary || p.b.binary);
    return "<span class='nw'>" + esc(p.a.engine) + " → " + esc(p.b.engine) + "</span>" + (same ? "<br><small>" + esc(p.a.binary || "?") + " → " + esc(p.b.binary || "?") + "</small>" : "");
  };
  var val = function (v) { return v === true ? "" : " " + esc(v); };

  // The configuration changes between two results as small chips; the first three, and a count of the rest.
  function argChips(args) {
    if (!args || !args.length) return "";
    var one = function (c) {
      return c.from === null ? "<code class='add'>+ " + esc(c.opt) + val(c.to) + "</code>" : c.to === null ? "<code class='del'>− " + esc(c.opt) + val(c.from) + "</code>" : "<code>" + esc(c.opt) + " " + esc(c.from === true ? "on" : c.from) + " → " + esc(c.to === true ? "on" : c.to) + "</code>";
    };
    var rest = args.slice(3).map(function (c) { return c.opt; }).join(", ");
    return "<span class='args'>" + args.slice(0, 3).map(one).join(" ") + (args.length > 3 ? " <span class='hint' title='" + esc(rest) + "'>+" + (args.length - 3) + " more</span>" : "") + "</span>";
  }

  // A small strip: the grey band is the noise, one dot per measured change.
  function strip(points, band) {
    var W = 92, H = 18, X = function (pct) { return 4 + (W - 8) * (Math.max(-DOMAIN, Math.min(DOMAIN, pct)) + DOMAIN) / (2 * DOMAIN); };
    var g = "<rect class='band' x='" + X(-band) + "' y='3' width='" + (X(band) - X(-band)) + "' height='12' rx='2'><title>noise band: ±" + band + "%</title></rect><line class='zero' x1='" + X(0) + "' x2='" + X(0) + "' y1='0' y2='" + H + "'/>";
    points.forEach(function (q) { g += "<circle class='dot " + q.verdict + "' cx='" + X(q.pct) + "' cy='9' r='4'><title>" + esc(q.title) + "</title></circle>"; });
    return "<svg class='viz mini' viewBox='0 0 " + W + " " + H + "' width='" + W + "' height='" + H + "' role='img' aria-label='Change in speed, noise band in grey'>" + g + "</svg>";
  }

  function releasesTab(list, mc) {
    var rel = I.releases(list);
    if (!rel.length) return "<p class='hint'>No release step yet. The same configuration measured on two engine builds of one machine appears here.</p>";
    var head = "<tr><th>Release</th>" + I.METRICS.map(function (m) { return "<th>" + esc(short(m.label)) + "</th>"; }).join("") + "</tr>";
    var row = function (r) {
      var verdict = r.enough ? "<span class='pill " + (r.moved ? "warnings" : "ok") + "'>" + (r.moved ? r.moved + " of " + r.metrics.length + " measures moved" : "inside the noise band") + "</span>"
        : "<span class='pill muted' title='A verdict needs " + I.MIN_MACHINES + " machines'>insufficient data</span> <small>" + r.machines + " of " + I.MIN_MACHINES + " machines</small>";
      var cells = r.metrics.map(function (m) {
        if (m.median === null) return "<td class='num'>–</td>";
        var pts = m.points.map(function (q) { return { pct: q.pct, verdict: q.verdict, title: (mc[q.machine] || { label: "machine" }).label + " · " + (q.pair.b.quant || "") + ": " + signed(q.pct) }; });
        return "<td class='num' title='" + (m.points.length > 1 ? "median of " + m.points.length + " pairs" : "one pair") + "'>" + strip(pts, m.band) + "<span class='val " + (r.enough ? m.verdict : "weak") + "'>" + signed(m.median) + "</span></td>";
      }).join("");
      return "<tr><td class='what'><b>" + arrow({ a: r.from, b: r.to }) + "</b><br>" + verdict + "</td>" + cells + "</tr>";
    };
    return "<div class='scroll'><table class='pairs'>" + head + rel.slice(0, CAP).map(row).join("") + "</table></div>" +
      (rel.length > CAP ? "<details class='more'><summary>Show " + (rel.length - CAP) + " more</summary><div class='scroll'><table class='pairs'>" + head + rel.slice(CAP).map(row).join("") + "</table></div></details>" : "") +
      "<p class='hint'>One row per engine release, over every machine that measured both builds with the same configuration. A verdict needs " + I.MIN_MACHINES + " machines; below that the numbers are one machine's observation. Each dot is one pair; with more than one pair the number is their median.</p>";
  }

  // One row per pair: what changed, then the change in each measure.
  function pairRow(p, mc, withMachine) {
    var what = p.kind === "config" ? "engine " + esc(p.a.engine) + " · configuration changed" : arrow(p);
    var note = p.kind === "mixed" ? " <span class='pill warnings' title='The engine build and the configuration both changed, so these numbers do not say which of the two moved them.'>build and configuration</span>" : "";
    var who = withMachine ? "<br><small>" + esc((mc[p.machine] || { label: "machine" }).label) + "</small>" : "";
    var cells = I.METRICS.map(function (m) {
      var d = p.deltas.filter(function (x) { return x.key === m.key; })[0];
      return d ? "<td class='num'><span class='chip " + d.verdict + "' title='" + esc(d.label + ": " + fmt(d.from, 1) + " to " + fmt(d.to, 1) + " (noise band ±" + d.band + "%)") + "'>" + signed(d.pct) + "</span></td>" : "<td class='num'>–</td>";
    }).join("");
    return "<tr><td class='what'><b>" + what + "</b> <small>" + esc(p.b.quant || "") + " · " + esc(proto(p.b)) + "</small>" + note + who + (p.args.length ? "<br>" + argChips(p.args) : "") + "</td>" + cells + "</tr>";
  }
  function pairTable(list, mc, withMachine) {
    var head = "<tr><th>Change</th>" + I.METRICS.map(function (m) { return "<th>" + esc(short(m.label)) + "</th>"; }).join("") + "</tr>";
    var t = function (l) { return "<div class='scroll'><table class='pairs'>" + head + l.map(function (p) { return pairRow(p, mc, withMachine); }).join("") + "</table></div>"; };
    return t(list.slice(0, CAP)) + (list.length > CAP ? "<details class='more'><summary>Show " + (list.length - CAP) + " more</summary>" + t(list.slice(CAP)) + "</details>" : "");
  }
  function gpuTab(list, mc) {
    var by = {}, order = [];
    list.forEach(function (p) { if (!by[p.machine]) { by[p.machine] = []; order.push(p.machine); } by[p.machine].push(p); });
    order.sort(function (x, y) { return by[y].length - by[x].length || String((mc[x] || {}).label).localeCompare(String((mc[y] || {}).label)); });
    var group = function (k, i) {
      var m = mc[k] || { cls: "c0", label: "machine" };
      return "<details class='gpu'" + (i === 0 ? " open" : "") + "><summary><i class='sw " + m.cls + "'></i> " + esc(m.label) + " <span class='hint'>" + by[k].length + (by[k].length === 1 ? " comparison" : " comparisons") + "</span></summary>" + pairTable(by[k], mc, false) + "</details>";
    };
    return order.slice(0, CAP).map(group).join("") + (order.length > CAP ? "<details class='more'><summary>Show " + (order.length - CAP) + " more machines</summary>" + order.slice(CAP).map(function (k, i) { return group(k, CAP + i); }).join("") + "</details>" : "");
  }

  function pairs(rows) {
    var list = I.pairs(rows), mc = machines(rows);
    var head = "<h2>What changed</h2><p class='hint'>Change from the older result to the newer one, per measure, on one machine. The grey band is the run-to-run noise (about 5% for prompt speed, 10% for decode); a change inside it is not measurable.</p>";
    if (!list.length) return head + "<p class='hint'>No pair yet. Two results from one machine that differ only in the engine build, or only in the configuration, appear here.</p>";
    var rel = I.releases(list), cfg = list.filter(function (p) { return p.kind === "config"; }), gpus = {};
    list.forEach(function (p) { gpus[p.machine] = 1; });
    var tab = function (id, label, n, on) { return "<input type='radio' name='ov-tab' id='ov-tab-" + id + "'" + (on ? " checked" : "") + "><label for='ov-tab-" + id + "'>" + label + " <span class='count'>" + n + "</span></label>"; };
    return head + "<div class='ptabs'>" + tab("rel", "Releases", rel.length, true) + tab("gpu", "By GPU", Object.keys(gpus).length, false) + tab("cfg", "Configurations", cfg.length, false) +
      "<div class='pview v-rel'>" + releasesTab(list, mc) + "</div>" +
      "<div class='pview v-gpu'>" + gpuTab(list, mc) + "</div>" +
      "<div class='pview v-cfg'>" + (cfg.length ? pairTable(cfg, mc, true) + "<p class='hint'>Same engine build, only the configuration changed. A change in the numbers is not by itself proof that the setting caused it.</p>" : "<p class='hint'>No configuration pair yet. Two results of one machine on the same engine build with different settings appear here.</p>") + "</div></div>";
  }

  function sameModel(rows) {
    var list = I.sameModel(rows), mc = machines(rows);
    if (!list.length) return "<h2>Same model, different machines</h2><p class='hint'>Nothing to compare yet. Results for a model and quantisation that two machines both measured appear here. <a href='submit.html'>Submit a result</a> for a model that is already listed.</p>";
    var metrics = [["prefill_medium", "Prompt speed, medium", 0], ["prefill_long", "Prompt speed, long", 0], ["decode_medium", "Decode speed", 1]];
    var one = function (g) {
      var cols = metrics.map(function (m) {
        var vals = g.machines.map(function (x) { return x[m[0]]; }).filter(function (v) { return v !== null; });
        if (vals.length < 2) return "";
        var max = Math.max.apply(null, vals), min = Math.min.apply(null, vals);
        return "<div><h4>" + m[1] + " <span class='hint'>tok/s</span></h4>" + g.machines.map(function (x) {
          var v = x[m[0]]; if (v === null) return "";
          return "<div class='hrow'><span class='who'>" + esc(mc[x.machine].label) + "</span><span class='track'><span class='hbar " + mc[x.machine].cls + "' style='width:" + Math.max(2, Math.round(100 * v / max)) + "%'></span></span><span class='v'>" + fmt(v, m[2]) + (v === max && min > 0 && max / min >= 1.05 ? " <em>" + (max / min).toFixed(1) + "×</em>" : "") + "</span></div>";
        }).join("") + "</div>";
      }).join("");
      var sec = g.machines.filter(function (x) { return x.long_seconds !== null; }).map(function (x) { return "<b>" + fmt(x.long_seconds, 1) + " s</b> on " + esc(mc[x.machine].label); }).join(", ");
      var diff = g.differs.length ? " The " + esc(g.differs.join(" and ")) + " differ between these results, so this is not a controlled comparison." : "";
      if (g.differs.indexOf("engine build") >= 0) diff += " Engine: " + g.machines.map(function (x) { return esc(mc[x.machine].label) + " " + esc(x.row.engine) + (x.row.binary ? " [" + esc(x.row.binary) + "]" : ""); }).join(", ") + ".";
      return "<div class='same'><h3>" + esc(g.model) + " " + esc(g.quant) + " <span class='hint'>engine " + esc(g.engine) + " · " + esc(g.protocol) + "</span></h3><div class='grid3'>" + cols + "</div>" +
        (sec && g.long_tokens ? "<p>A long prompt of about " + fmt(g.long_tokens, 0) + " tokens is read in " + sec + ".</p>" : "") +
        "<p class='hint'>Same model, quantisation and protocol; the latest result of each machine, on the engine build most machines share.<span>" + diff + "</span></p></div>";
    };
    return "<h2>Same model, different machines</h2>" + one(list[0]) + (list.length > 1 ? "<details class='more'><summary>Show " + (list.length - 1) + " more</summary>" + list.slice(1, 8).map(one).join("") + "</details>" : "");
  }

  var api = { kpi: kpi, speedMap: speedMap, fit: fit, pairs: pairs, sameModel: sameModel, machines: machines };
  root.StrataOverview = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
