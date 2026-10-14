"use strict";
// "Who won more measures?" for two or more result files.
// A descriptive count, not a verdict: it says on how many measures each result is in front, never that one setup is better or why.
// Pure functions over result documents (strata-bench/v1 and v2), no DOM, so the page and the tests run the same code.
(function (root) {
  var SIZES = ["probe", "gprobe150", "gprobe300", "gprobe450", "chat", "short", "medium", "long", "xlong"];
  // Inside the band a difference is run-to-run noise: prompt speed moves up to about 5%, decode and the busy-server numbers up to about 10%.
  var BAND = { prompt: 5, decode: 10, latency: 10, load: 10 };
  var HIT_POINTS = 2;                                           // the agent cache hit rate is a percentage: two points are a tie

  var isNum = function (v) { return typeof v === "number" && isFinite(v); };
  var stat = function (d, size, field) { var s = ((d && d.sizes) || {})[size]; var f = s && s[field]; return f && isNum(f.median) ? f.median : null; };
  var hostOf = function (d) { return ((d && d.runner) || {}).host_relation || ""; };

  // The measures a result can hold, in the order they are shown. Each: key, label, better ("high" | "low"), band (percent, or points when abs), get(d).
  function candidates(docs) {
    var out = [];
    SIZES.forEach(function (s) {
      var hasSpeed = docs.some(function (d) { return stat(d, s, "prompt_tps") !== null; });
      if (hasSpeed) {
        out.push({ key: s + ":prompt_tps", label: "Prompt speed · " + s, better: "high", band: BAND.prompt, digits: 0, get: function (d) { return stat(d, s, "prompt_tps"); } });
        out.push({ key: s + ":decode_tps", label: "Decode speed · " + s, better: "high", band: BAND.decode, digits: 1, get: function (d) { return stat(d, s, "decode_tps"); } });
      } else {
        out.push({ key: s + ":prompt_ms", label: "Prompt time · " + s + " (ms)", better: "low", band: BAND.prompt, digits: 0, get: function (d) { return stat(d, s, "prompt_ms"); } });   // the latency probes have no speed
      }
      out.push({ key: s + ":ttft_ms", label: "First token · " + s + " (ms)", better: "low", band: BAND.latency, digits: 0, get: function (d) { return stat(d, s, "ttft_ms"); }, network: true });
    });
    out.push({ key: "agent:hit_pct", label: "Agent cache hit (%)", better: "high", band: HIT_POINTS, abs: true, digits: 1, get: function (d) { var a = (d && d.agent) || {}; return a.hit_pct && isNum(a.hit_pct.median) ? a.hit_pct.median : null; } });
    [2, 4, 8].forEach(function (n) {
      out.push({ key: "clients" + n + ":total_tps", label: n + " clients · total tok/s", better: "high", band: BAND.load, digits: 1, get: function (d) { var c = ((d && d.clients) || []).filter(function (x) { return x && x.n === n; })[0]; return c && isNum(c.total_tps) ? c.total_tps : null; } });
    });
    return out;
  }

  // Is v within the noise band of the best value? The gap is read the way the rest of the page reads a change: how far the best is above v
  // (for a time, how far v is above the best), to one decimal as shown, so +5.0% is on the band and not past it.
  function inFront(m, v, best) {
    if (m.abs) return Math.round(Math.abs(best - v) * 10) / 10 <= m.band;
    var gap;
    if (m.better === "high") gap = v > 0 ? best / v - 1 : (best > 0 ? Infinity : 0);
    else gap = best > 0 ? v / best - 1 : (v > 0 ? Infinity : 0);
    return Math.round(gap * 1000) / 10 <= m.band;
  }
  // 100 means as good as the best; lower means that much of the best's performance (or, for a time, that much slower).
  function support(m, v, best) {
    if (m.better === "high") return best > 0 ? 100 * v / best : 100;
    return v > 0 ? 100 * best / v : 100;
  }

  function compute(docs) {
    if (!Array.isArray(docs) || docs.length < 2) return null;
    var sameHost = docs.every(function (d) { return hostOf(d) === hostOf(docs[0]); });
    var metrics = [], excluded = [];
    candidates(docs).forEach(function (m) {
      var values = docs.map(m.get), have = values.filter(function (v) { return v !== null; }).length;
      if (have === 0) return;                                        // nobody measured it: not worth a mention
      if (have < docs.length) { excluded.push(m.label); return; }    // a missing value is never a zero: the measure stays out of the count
      if (m.network && !sameHost) { excluded.push(m.label + " (the client ran in different places)"); return; }
      var best = m.better === "high" ? Math.max.apply(null, values) : Math.min.apply(null, values);
      var front = values.map(function (v) { return inFront(m, v, best); });
      metrics.push({ key: m.key, label: m.label, better: m.better, band: m.band, abs: !!m.abs, digits: m.digits, values: values, best: best, front: front, tie: front.every(Boolean) });
    });
    var results = docs.map(function (d, i) {
      var wins = 0, ties = 0, sum = 0;
      metrics.forEach(function (m) { if (m.tie) ties++; else if (m.front[i]) wins++; sum += support(m, m.values[i], m.best); });
      return { wins: wins, ties: ties, total: metrics.length, support: metrics.length ? sum / metrics.length : null };
    });
    return { metrics: metrics, excluded: excluded, results: results, headline: headline(results, metrics.length) };
  }

  function headline(results, total) {
    if (!total) return { kind: "none", leaders: [] };
    var most = Math.max.apply(null, results.map(function (r) { return r.wins; }));
    if (most === 0) return { kind: "all-ties", leaders: [], wins: 0, total: total };
    var leaders = [];
    results.forEach(function (r, i) { if (r.wins === most) leaders.push(i); });
    return { kind: leaders.length === 1 ? "leader" : "shared", leaders: leaders, wins: most, total: total, ties: results[leaders[0]].ties };
  }

  var api = { SIZES: SIZES, BAND: BAND, HIT_POINTS: HIT_POINTS, compute: compute };
  root.StrataScoreboard = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
