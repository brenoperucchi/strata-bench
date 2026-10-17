"use strict";
// The "Strata reports" page: the community reports of the Strata repository, read into community.json by tools/import_strata_reports.py.
// They were measured with other prompts than strata-bench's, so they are listed apart and never ranked with the results.
// Pure functions that return HTML text; every value that comes from the file goes through esc() or fmt() first.
(function (root) {
  var esc = function (s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var num = function (v) { return typeof v === "number" && isFinite(v) ? v : null; };
  var fmt = function (v, d) { v = num(v); return v === null ? "–" : v.toLocaleString(undefined, { maximumFractionDigits: d === undefined ? 0 : d }); };
  // only a link to the Strata repository on GitHub is ever written as a link
  var safeUrl = function (u) { return /^https:\/\/github\.com\/Niko1221\/Strata\/[A-Za-z0-9._\/-]+$/.test(String(u || "")) ? u : null; };
  var speed = function (r, k) { return num((r.prompt_tps || {})[k]); };

  // "0.1.40.3" > "0.1.9": numeric parts compared in order
  function versionCmp(a, b) {
    var x = String(a || "").match(/\d+/g) || [], y = String(b || "").match(/\d+/g) || [];
    for (var i = 0; i < Math.max(x.length, y.length); i++) { var d = (+x[i] || 0) - (+y[i] || 0); if (d) return d; }
    return 0;
  }

  // Only the columns most reports fill. The short chat, the 16K prompt and HumanEval are in community.json but only one or two reports state them, so a column would be empty.
  var COLUMNS = [
    { key: "date", label: "Date", get: function (r) { return r.date || ""; } },
    { key: "strata", label: "Strata", get: function (r) { return r.strata || ""; }, title: "The Strata version the report says it ran" },
    { key: "cpu", label: "CPU", get: function (r) { return r.cpu || ""; } },
    { key: "ram", label: "RAM", get: function (r) { return r.ram || ""; } },
    { key: "gpu", label: "GPU · VRAM", get: function (r) { return r.gpu || ""; } },
    { key: "quant", label: "Quant", get: function (r) { return r.quant || ""; } },
    { key: "context", label: "Context", num: true, get: function (r) { return num(r.context); } },
    { key: "p4k", label: "4K", num: true, get: function (r) { return speed(r, "4k"); }, title: "Prompt tok/s at a prompt of about 4K tokens" },
    { key: "p32k", label: "32K", num: true, get: function (r) { return speed(r, "32k"); }, title: "Prompt tok/s at about 32K tokens" },
    { key: "p128k", label: "128K", num: true, get: function (r) { return speed(r, "128k"); }, title: "Prompt tok/s at about 128K tokens" },
    { key: "decode", label: "Decode", num: true, get: function (r) { return num(r.decode_tps); }, title: "Decode tok/s of the 4K prompt" },
  ];

  var hasSpeed = function (r) { return ["4k", "32k", "128k"].some(function (k) { return speed(r, k) !== null; }) || num(r.decode_tps) !== null; };
  // full: the prompt speed at 4K, 32K and 128K and the decode speed are all stated
  var isFull = function (r) { return ["4k", "32k", "128k"].every(function (k) { return speed(r, k) !== null; }) && num(r.decode_tps) !== null; };
  var haystack = function (r) { return [r.headline, r.gpu, r.cpu, r.ram, r.board, r.strata, r.quant, r.summary, r.id].join(" ").toLowerCase(); };
  // "up to" limits of the context size, in tokens; the last one is everything above 1M
  var CONTEXT = [["32k", "Up to 32K", 32768], ["128k", "Up to 128K", 131072], ["256k", "Up to 256K", 262144], ["1m", "Up to 1M", 1048576], ["over", "Over 1M", Infinity]];
  var inContext = function (r, key) {
    var n = num(r.context);
    if (!key) return true;
    if (n === null) return false;                                                    // a report that does not state its context cannot be placed
    if (key === "over") return n > 1048576;
    return n <= CONTEXT.filter(function (c) { return c[0] === key; })[0][2];
  };

  // opts: {query, strata, quant, show: "full" | "speeds" | "all", context: "32k" | "128k" | "256k" | "1m" | "over"}
  function filter(reports, opts) {
    opts = typeof opts === "string" ? { query: opts } : (opts || {});
    var words = String(opts.query || "").toLowerCase().trim().split(/\s+/).filter(Boolean);
    return reports.filter(function (r) {
      if (opts.strata && r.strata !== opts.strata) return false;
      if (opts.quant && r.quant !== opts.quant) return false;
      if (opts.show === "full" && !isFull(r)) return false;
      if (opts.show === "speeds" && !hasSpeed(r)) return false;
      if (!inContext(r, opts.context)) return false;
      return words.every(function (w) { return haystack(r).indexOf(w) >= 0; });
    });
  }

  // the values the Strata and Quant selects offer, newest version first
  function options(reports) {
    var uniq = function (f) { var s = {}; reports.forEach(function (r) { var v = f(r); if (v) s[v] = 1; }); return Object.keys(s); };
    return { strata: uniq(function (r) { return r.strata; }).sort(function (a, b) { return -versionCmp(a, b); }), quant: uniq(function (r) { return r.quant; }).sort() };
  }

  function sorted(list, sort) {
    var col = COLUMNS.filter(function (c) { return c.key === (sort && sort.key); })[0];
    if (!col) return list;
    var dir = sort.dir < 0 ? -1 : 1;
    return list.slice().sort(function (a, b) {
      var x = col.get(a), y = col.get(b);
      if (x === null || x === "") return (y === null || y === "") ? 0 : 1;            // a missing value goes last, whichever way it is sorted
      if (y === null || y === "") return -1;
      var c = col.key === "strata" ? versionCmp(x, y) : (x < y ? -1 : x > y ? 1 : 0);
      return c * dir;
    });
  }

  function row(r) {
    var link = safeUrl(r.source && r.source.url), v = function (x, d) { return "<td class='num'>" + fmt(x, d) + "</td>"; };
    var gpu = r.gpu ? esc(r.gpu) + (r.vram_gb ? " <span class='hint'>" + esc(r.vram_gb) + " GB</span>" : "") : "–";
    return "<tr><td class='nowrap'>" + esc(r.date || "–") + "</td><td class='strata'>" + esc(r.strata || "–") + "</td>" +
      "<td title='" + esc(r.headline || "") + "'>" + esc(r.cpu || "–") + (r.board ? "<br><span class='hint' title='Motherboard, as the report states it'>" + esc(r.board) + "</span>" : "") + "</td>" +
      "<td>" + esc(r.ram || "–") + "</td><td>" + gpu + "</td><td>" + esc(r.quant || "–") + "</td>" +
      v(r.context) + v(speed(r, "4k")) + v(speed(r, "32k")) + v(speed(r, "128k")) + v(r.decode_tps, 1) +
      "<td>" + (link ? "<a href='" + esc(link) + "' rel='noopener' title='" + esc(r.headline || r.id) + "'>report</a>" : "–") + "</td></tr>";
  }

  function render(data, opts) {
    var all = (data && Array.isArray(data.reports)) ? data.reports : [];
    if (!all.length) return "<p class='hint'>No report could be read.</p>";
    opts = typeof opts === "string" ? { query: opts } : (opts || {});
    var list = sorted(filter(all, opts), opts.sort), withSpeed = all.filter(hasSpeed).length, src = data.source || {};
    var from = safeUrl(src.repository ? src.repository + "/x" : "") ? "<a href='" + esc(src.repository) + "' rel='noopener'>Strata repository</a>" : "Strata repository";
    var head = COLUMNS.map(function (c) {
      var on = opts.sort && opts.sort.key === c.key;
      return "<th data-sort='" + c.key + "'" + (c.num ? " class='num'" : "") + (c.title ? " title='" + esc(c.title) + "'" : "") + ">" + esc(c.label) + (on ? (opts.sort.dir < 0 ? " ▼" : " ▲") : "") + "</th>";
    }).join("") + "<th></th>";
    var full = all.filter(isFull).length;
    return "<div class='notice'><b>Not comparable with the Results tab.</b> These reports were measured with the Strata script's own prompts and sizes, by the people who sent them; strata-bench did not run them. " +
      "They are listed here so the cards the community has tried are easy to find, and they are never ranked against the results.</div>" +
      "<p class='hint'>" + esc(list.length) + " of " + esc(all.length) + " reports shown, from the " + from + " (MIT): " + esc(full) + " are <b>full</b> (they state the prompt speed at 4K, 32K and 128K and the decode speed) and " + esc(withSpeed) + " state at least one speed. " +
      "Figures are read from each report's README as written; a dash means the report does not state it in the usual place. The context column is the context the reporter ran with, not a measurement. " +
      (src.commit ? "Read at commit " + esc(String(src.commit).slice(0, 8)) + ". " : "") + "Open the report for the full method and its limits.</p>" +
      "<div class='scroll'><table class='reports'><thead><tr>" + head + "</tr></thead><tbody>" +
      (list.length ? list.map(row).join("") : "<tr><td colspan='" + (COLUMNS.length + 1) + "'>No report matches.</td></tr>") + "</tbody></table></div>";
  }

  var api = { render: render, filter: filter, options: options, CONTEXT: CONTEXT, versionCmp: versionCmp, safeUrl: safeUrl };
  root.StrataCommunity = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
