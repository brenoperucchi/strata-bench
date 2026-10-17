"use strict";
// The thank-you page: who ran strata-bench on their own machine and sent a result, read from data.json.
// A name is declared by whoever sent the result (submission.by), not verified; every value is escaped before it reaches the page.
(function (root) {
  var esc = function (s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var day = function (d) { return String(d || "").slice(0, 10); };

  // one entry per declared name: how many results, on which GPUs and engines, first and last day; most results first
  function people(rows) {
    var by = {};
    rows.forEach(function (r) {
      if (!r || !r.by) return;
      var p = by[r.by] = by[r.by] || { name: r.by, results: 0, gpus: {}, engines: {}, first: "", last: "" };
      p.results++;
      if (r.gpu) p.gpus[r.gpu] = 1;
      if (r.engine) p.engines[r.engine] = 1;
      var d = day(r.date);
      if (d && (!p.first || d < p.first)) p.first = d;
      if (d && d > p.last) p.last = d;
    });
    return Object.keys(by).map(function (k) { var p = by[k]; p.gpus = Object.keys(p.gpus).sort(); p.engines = Object.keys(p.engines).sort(); return p; })
      .sort(function (a, b) { return b.results - a.results || a.name.localeCompare(b.name); });
  }

  function render(rows) {
    var list = people(rows || []);
    if (!list.length) return "<p class='hint'>No name has been declared yet. Add <code>--by your-name</code> to your run and your result is listed here.</p>";
    var total = list.reduce(function (n, p) { return n + p.results; }, 0);
    return "<p class='lede-text'>Every row is someone who ran strata-bench on their own machine and sent the result. " + esc(list.length) + " people so far, " + esc(total) + " results between them. Thank you.</p>" +
      "<div class='scroll'><table class='contributors'><thead><tr><th>Name</th><th class='num'>Results</th><th>GPUs</th><th>Strata versions</th><th>First</th><th>Latest</th></tr></thead><tbody>" +
      list.map(function (p) {
        return "<tr><td><a href='index.html?by=" + encodeURIComponent(p.name) + "'>" + esc(p.name) + "</a></td><td class='num'>" + esc(p.results) + "</td><td>" + esc(p.gpus.join(", ") || "–") + "</td><td>" + esc(p.engines.join(", ") || "–") + "</td><td class='nowrap'>" + esc(p.first || "–") + "</td><td class='nowrap'>" + esc(p.last || "–") + "</td></tr>";
      }).join("") + "</tbody></table></div>" +
      "<p class='hint'>A name is declared by whoever sent the result and is not verified. Click a name to see that person's results. Want to be on this list? <a href='submit.html'>Send a result</a>.</p>";
  }

  var api = { people: people, render: render };
  root.StrataContributors = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
