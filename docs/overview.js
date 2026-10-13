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

  function speedMap(rows) {
    var pts = I.speedPoints(rows);
    if (!pts.length) return "";
    var mc = machines(rows), W = 520, H = 280, L = 58, R = 28, T = 12, B = 46;
    var mx = Math.max.apply(null, pts.map(function (p) { return p.x; })) * 1.06, my = Math.max.apply(null, pts.map(function (p) { return p.y; })) * 1.12;
    var sx = niceStep(mx / 4), sy = niceStep(my / 4), xmax = Math.ceil(mx / sx) * sx, ymax = Math.ceil(my / sy) * sy;
    var X = function (v) { return L + (W - L - R) * v / xmax; }, Y = function (v) { return H - B - (H - B - T) * v / ymax; };
    var g = "";
    for (var yv = 0; yv <= ymax + 1e-9; yv += sy) g += "<line class='grid' x1='" + L + "' x2='" + (W - R) + "' y1='" + Y(yv) + "' y2='" + Y(yv) + "'/><text x='" + (L - 8) + "' y='" + (Y(yv) + 4) + "' text-anchor='end'>" + fmt(yv, 0) + "</text>";
    for (var xv = 0; xv <= xmax + 1e-9; xv += sx) g += "<text x='" + X(xv) + "' y='" + (H - B + 18) + "' text-anchor='middle'>" + fmt(xv, 0) + "</text>";
    g += "<text class='axis-title' x='" + (L + W - R) / 2 + "' y='" + (H - 6) + "' text-anchor='middle'>prompt speed, medium prompt (tokens per second)</text>" +
      "<text class='axis-title' x='14' y='" + (T + H - B) / 2 + "' text-anchor='middle' transform='rotate(-90 14 " + (T + H - B) / 2 + ")'>decode speed (tokens per second)</text>";
    pts.forEach(function (p) {
      var r = p.row, m = mc[p.machine], cx = X(p.x), cy = Y(p.y), v2 = r.protocol === "standard-v2";
      var shape = v2 ? "<rect class='dot " + m.cls + "' x='" + (cx - 6) + "' y='" + (cy - 6) + "' width='12' height='12' transform='rotate(45 " + cx + " " + cy + ")'/>" : "<circle class='dot " + m.cls + "' cx='" + cx + "' cy='" + cy + "' r='6.5'/>";
      g += "<a href='" + esc(link(r.id)) + "'>" + shape + "<title>" + esc(m.label + " · " + (r.model || "") + " " + (r.quant || "") + " · engine " + r.engine + " · " + r.protocol + (r.by ? " · by " + r.by : "")) + "\n" + fmt(p.x, 0) + " tok/s prompt, " + fmt(p.y, 1) + " tok/s decode</title></a>";
    });
    var clusters = {};
    pts.forEach(function (p) { var c = clusters[p.machine] = clusters[p.machine] || { n: 0, x: 0, y: 0 }; c.n++; c.x += X(p.x); c.y += Y(p.y); });
    var placed = [];
    Object.keys(clusters).sort().forEach(function (k) {
      var c = clusters[k], cx = c.x / c.n, cy = c.y / c.n, below = placed.some(function (q) { return Math.abs(q.x - cx) < 90 && Math.abs(q.y - cy) < 26; });
      placed.push({ x: cx, y: cy });
      g += "<text class='tag " + mc[k].cls + "' x='" + cx + "' y='" + (below ? cy + 26 : cy - 14) + "' text-anchor='middle'>" + esc(mc[k].label) + "</text>";
    });
    var used = {}; pts.forEach(function (p) { used[p.machine] = (used[p.machine] || 0) + 1; });
    var legend = Object.keys(used).sort().map(function (k) { return "<span class='key'><i class='sw " + mc[k].cls + "'></i>" + esc(mc[k].label) + "</span>"; }).join("");
    var shapes = "<span class='key'><i class='sw round'></i>standard-v1</span><span class='key'><i class='sw diamond'></i>standard-v2</span>";
    return "<h2>Speed map</h2><p class='hint'>One point per result. Right is faster at reading a prompt, up is faster at writing the answer. Click a point for the full result.</p>" +
      "<svg class='viz' viewBox='0 0 " + W + " " + H + "' role='img' aria-label='Decode speed against prompt speed, one point per result'>" + g + "</svg>" +
      "<div class='legend'>" + legend + "</div><div class='legend'>" + shapes + "</div>" +
      "<p class='hint'>A map of what was measured, not a ranking: the points differ in model, quantisation, engine and configuration.</p>";
  }

  var DOMAIN = 20;                                           // the strip shows -20% to +20%; a larger change sits at the edge and its value is written out
  function pairBlock(p, mc, support) {
    var W = 440, rowH = 30, L = 0, x0 = 150, x1 = 340, H = p.deltas.length * rowH + 26;
    var X = function (pct) { return x0 + (x1 - x0) * (Math.max(-DOMAIN, Math.min(DOMAIN, pct)) + DOMAIN) / (2 * DOMAIN); };
    var g = "<line class='zero' x1='" + X(0) + "' x2='" + X(0) + "' y1='0' y2='" + (H - 24) + "'/>";
    p.deltas.forEach(function (d, i) {
      var y = i * rowH + 15;
      g += "<text x='" + L + "' y='" + (y + 4) + "'>" + esc(d.label) + "</text>" +
        "<rect class='band' x='" + X(-d.band) + "' y='" + (y - 9) + "' width='" + (X(d.band) - X(-d.band)) + "' height='18' rx='3'><title>noise band: ±" + d.band + "%</title></rect>" +
        "<circle class='dot " + d.verdict + "' cx='" + X(d.pct) + "' cy='" + y + "' r='6'><title>" + esc(d.label) + ": " + fmt(d.from, 1) + " to " + fmt(d.to, 1) + " (" + signed(d.pct) + ")</title></circle>" +
        "<text class='val " + d.verdict + "' x='" + (W - 4) + "' y='" + (y + 4) + "' text-anchor='end'>" + signed(d.pct) + "</text>";
    });
    [-DOMAIN, 0, DOMAIN].forEach(function (t) { g += "<text x='" + X(t) + "' y='" + (H - 6) + "' text-anchor='middle'>" + (t > 0 ? "+" : t < 0 ? "−" : "") + Math.abs(t) + "%</text>"; });
    var m = mc[p.machine] || { label: "machine" }, a = p.a, b = p.b;
    var what = p.kind === "build" ? "engine " + a.engine + " → " + b.engine + ", same configuration" : "engine " + a.engine + ", configuration changed";
    var note = p.kind === "build"
      ? ((support[a.engine + " → " + b.engine] || 0) < I.MIN_MACHINES ? "Insufficient data for a verdict on this release: " + (support[a.engine + " → " + b.engine] || 0) + " of " + I.MIN_MACHINES + " machines needed. " : "")
      : "Single-machine observation. ";
    var val = function (v) { return v === true ? "" : " " + esc(v); };
    var changes = p.args && p.args.length ? "<p class='args'>Configuration changes: " + p.args.map(function (c) {
      return c.from === null ? "<code class='add'>+ " + esc(c.opt) + val(c.to) + "</code>" : c.to === null ? "<code class='del'>− " + esc(c.opt) + val(c.from) + "</code>" : "<code>" + esc(c.opt) + " " + esc(c.from === true ? "on" : c.from) + " → " + esc(c.to === true ? "on" : c.to) + "</code>";
    }).join(" ") + "</p>" : "";
    return "<div class='pair'><h3>" + esc(m.label) + " · " + esc(b.model || "") + " " + esc(b.quant || "") + " <span class='pill " + (p.moved ? "warnings" : "ok") + "'>" + esc(p.headline) + "</span></h3>" +
      "<p class='hint'>" + esc(what) + ". " + esc(note) + "</p>" + changes +
      "<svg class='viz' viewBox='0 0 " + W + " " + H + "' role='img' aria-label='Change in speed between two results of one machine'>" + g + "</svg></div>";
  }
  function pairs(rows) {
    var list = I.pairs(rows), mc = machines(rows), support = I.transitionSupport(list);
    var body = list.length
      ? list.slice(0, 4).map(function (p) { return pairBlock(p, mc, support); }).join("")
      : "<p class='hint'>No pair yet. Two results from one machine that differ only in the engine build, or only in the configuration, appear here.</p>";
    return "<h2>Same machine, what changed</h2><p class='hint'>Change from the older result to the newer one, per measure. The grey band is the run-to-run noise (about 5% for prompt speed, 10% for decode); a dot inside it is not a measurable change.</p>" + body;
  }

  function sameModel(rows) {
    var list = I.sameModel(rows), mc = machines(rows);
    if (!list.length) return "<h2>Same model, different machines</h2><p class='hint'>Nothing to compare yet. Results for a model and engine build that two machines both measured appear here. <a href='submit.html'>Submit a result</a> for a model that is already listed.</p>";
    var metrics = [["prefill_medium", "Prompt speed, medium", 0], ["prefill_long", "Prompt speed, long", 0], ["decode_medium", "Decode speed", 1]];
    return "<h2>Same model, different machines</h2>" + list.slice(0, 2).map(function (g) {
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
      return "<div class='same'><h3>" + esc(g.model) + " " + esc(g.quant) + " <span class='hint'>engine " + esc(g.engine) + " · " + esc(g.protocol) + "</span></h3><div class='grid3'>" + cols + "</div>" +
        (sec && g.long_tokens ? "<p>A long prompt of about " + fmt(g.long_tokens, 0) + " tokens is read in " + sec + ".</p>" : "") +
        "<p class='hint'>Same model and engine build; the latest result of each machine.<span>" + diff + "</span></p></div>";
    }).join("");
  }

  var api = { kpi: kpi, speedMap: speedMap, pairs: pairs, sameModel: sameModel, machines: machines };
  root.StrataOverview = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
