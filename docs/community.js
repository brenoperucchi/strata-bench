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

  var haystack = function (r) { return [r.headline, r.gpu, r.cpu, r.engine, r.quant, r.summary, r.id].join(" ").toLowerCase(); };
  function filter(reports, query) {
    var q = String(query || "").toLowerCase().trim();
    if (!q) return reports;
    return reports.filter(function (r) { return q.split(/\s+/).every(function (w) { return haystack(r).indexOf(w) >= 0; }); });
  }

  function row(r) {
    var h = r.headline_row || {}, link = safeUrl(r.source && r.source.url);
    var shown = h.prompt_tokens ? " <span class='hint' title='Prompt size of the row the speeds come from'>@" + esc(fmt(h.prompt_tokens)) + " tokens" + (h.label && !/^[\d,.\s]+$/.test(h.label) ? ", " + esc(h.label) : "") + "</span>" : "";
    return "<tr><td class='nowrap'>" + esc(r.date || "–") + "</td><td class='hw'>" + esc(r.headline || r.id) + (r.summary ? "<br><span class='hint'>" + esc(String(r.summary).slice(0, 160)) + (String(r.summary).length > 160 ? "…" : "") + "</span>" : "") + "</td>" +
      "<td>" + esc(r.quant || "–") + "</td><td class='num'>" + esc(r.context ? fmt(r.context) : "–") + "</td>" +
      "<td class='num'>" + fmt(h.prompt_tps) + shown + "</td><td class='num'>" + fmt(h.decode_tps, 1) + "</td>" +
      "<td>" + (link ? "<a href='" + esc(link) + "' rel='noopener'>report</a>" : "–") + "</td></tr>";
  }

  function render(data, query) {
    var all = (data && Array.isArray(data.reports)) ? data.reports : [];
    if (!all.length) return "<p class='hint'>No report could be read.</p>";
    var list = filter(all, query), withSpeed = all.filter(function (r) { return r.headline_row; }).length;
    var src = data.source || {};
    var from = safeUrl(src.repository ? src.repository + "/x" : "") ? "<a href='" + esc(src.repository) + "' rel='noopener'>Strata repository</a>" : "Strata repository";
    return "<div class='notice'><b>Not comparable with the Results tab.</b> These reports were measured with the Strata script's own prompts and sizes, by the people who sent them; strata-bench did not run them. " +
      "They are listed here so the cards the community has tried are easy to find, and they are never ranked against the results.</div>" +
      "<p class='hint'>" + esc(all.length) + " reports from the " + from + " (MIT), " + esc(withSpeed) + " with a prompt speed that could be read from the table. Figures are read from each report's README as written; a dash means the report does not state it in the usual table. " +
      (src.commit ? "Read at commit " + esc(String(src.commit).slice(0, 8)) + ". " : "") + "Open the report for the full method and its limits.</p>" +
      "<div class='scroll'><table class='reports'><thead><tr><th>Date</th><th>Hardware</th><th>Quant</th><th class='num'>Context</th><th class='num' title='Prompt speed of the row nearest a 4K prompt'>Prompt tok/s</th><th class='num' title='Decode speed of that row'>Decode tok/s</th><th></th></tr></thead><tbody>" +
      (list.length ? list.map(row).join("") : "<tr><td colspan='7'>No report matches.</td></tr>") + "</tbody></table></div>";
  }

  var api = { render: render, filter: filter, safeUrl: safeUrl };
  root.StrataCommunity = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
