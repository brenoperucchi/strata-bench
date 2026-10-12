"use strict";
(function () {
  var state = { rows: [], filters: { gpu: "", model: "", engine: "", status: "", by: "", machine_id: "" }, selected: [], sort: { key: "date", dir: -1 } };
  var $ = function (id) { return document.getElementById(id); };
  var fmt = function (v, d) { return v === null || v === undefined ? "–" : (typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: d === undefined ? 1 : d }) : String(v)); };
  var esc = function (s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var uniq = function (rows, key) { var s = {}; rows.forEach(function (r) { if (r[key]) s[r[key]] = 1; }); return Object.keys(s).sort(); };
  var size = function (r, name, field) { return r.sizes && r.sizes[name] ? r.sizes[name][field] : null; };

  function cards(rows) {
    var perf = rows.filter(function (r) { return r.status !== "failed"; });
    var failed = rows.length - perf.length;
    var items = [[rows.length, "results"], [uniq(rows, "gpu").length, "GPU models"], [uniq(rows, "engine").length, "engine versions"], [failed, "failed runs"]];
    $("cards").innerHTML = items.map(function (i) { return '<div class="card"><b>' + esc(i[0]) + "</b><span>" + esc(i[1]) + "</span></div>"; }).join("");
  }

  function filters(rows) {
    var defs = [["gpu", "GPU"], ["model", "Model"], ["engine", "Engine"], ["status", "Status"], ["by", "Submitted by"], ["machine_id", "Machine"]].filter(function (d) {
      return d[0] === "status" || d[0] === "gpu" || d[0] === "model" || d[0] === "engine" || uniq(rows, d[0]).length > 0;   // the optional filters appear only when some result declares them
    });
    $("filters").innerHTML = defs.map(function (d) {
      var opts = d[0] === "status" ? ["ok", "warnings"] : uniq(rows, d[0]);
      return '<label>' + d[1] + '<select data-key="' + d[0] + '"><option value="">All</option>' +
        opts.map(function (o) { return '<option value="' + esc(o) + '"' + (state.filters[d[0]] === o ? " selected" : "") + ">" + esc(o) + "</option>"; }).join("") + "</select></label>";
    }).join("");
    Array.prototype.forEach.call($("filters").querySelectorAll("select"), function (s) {
      s.addEventListener("change", function () { state.filters[s.dataset.key] = s.value; shareLink(); drawPerf(); });
    });
  }

  // ?by=name and ?machine=id preselect a filter, so a link can point at one person's or one machine's results
  function readLink() {
    try { var q = new URLSearchParams(location.search); state.filters.by = q.get("by") || ""; state.filters.machine_id = q.get("machine") || ""; } catch (e) { /* no URL support */ }
  }
  function shareLink() {
    try {
      var q = new URLSearchParams(); if (state.filters.by) q.set("by", state.filters.by); if (state.filters.machine_id) q.set("machine", state.filters.machine_id);
      history.replaceState(null, "", location.pathname + (q.toString() ? "?" + q : "") + location.hash);
    } catch (e) { /* file:// */ }
  }

  function sortVal(r, key) {
    if (key === "date") return r.date || "";
    if (key === "decode") return size(r, "medium", "decode_tps") || 0;
    if (key.indexOf("prompt_") === 0) return size(r, key.slice(7), "prompt_tps") || 0;
    return (r[key] || "").toString().toLowerCase();
  }

  function drawPerf() {
    var rows = state.rows.filter(function (r) {
      return r.status !== "failed" && (!state.filters.gpu || r.gpu === state.filters.gpu) && (!state.filters.model || r.model === state.filters.model) &&
        (!state.filters.engine || r.engine === state.filters.engine) && (!state.filters.status || r.status === state.filters.status) &&
        (!state.filters.by || r.by === state.filters.by) && (!state.filters.machine_id || r.machine_id === state.filters.machine_id);
    });
    rows.sort(function (a, b) { var x = sortVal(a, state.sort.key), y = sortVal(b, state.sort.key); return (x < y ? -1 : x > y ? 1 : 0) * state.sort.dir; });
    var maxDecode = Math.max.apply(null, rows.map(function (r) { return size(r, "medium", "decode_tps") || 0; }).concat([1]));
    var hasBy = state.rows.some(function (r) { return r.by; });
    var head = [["select", ""], ["gpu", "GPU"], ["model", "Model"], ["engine", "Engine"]].concat(hasBy ? [["by", "By"]] : []).concat([["config_id", "Config"], ["prompt_medium", "Prompt medium", 1], ["prompt_long", "Prompt long", 1], ["prompt_xlong", "Prompt xlong", 1], ["decode", "Decode", 1], ["date", "Date"], ["open", ""]]);
    var h = "<thead><tr>" + head.map(function (c) { return '<th data-key="' + c[0] + '"' + (c[2] ? ' class="num"' : "") + ">" + c[1] + (state.sort.key === c[0] ? (state.sort.dir > 0 ? " ▲" : " ▼") : "") + "</th>"; }).join("") + "</tr></thead>";
    var b = rows.map(function (r) {
      var dec = size(r, "medium", "decode_tps");
      var args = (r.args || []).join(" ");
      return "<tr class='openable' data-id='" + esc(r.id) + "'><td class='pick-cell'><input type='checkbox' class='pick' data-id='" + esc(r.id) + "' aria-label='Select for comparison'" + (state.selected.indexOf(r.id) >= 0 ? " checked" : "") + "></td><td>" + esc(r.gpu) + (r.vram_gb ? " <span class='hint'>" + esc(r.vram_gb) + " GB</span>" : "") + (r.gpu_count > 1 ? " <span class='hint'>×" + esc(r.gpu_count) + "</span>" : "") +
        "</td><td>" + esc(r.model) + (r.quant ? " <span class='hint'>" + esc(r.quant) + "</span>" : "") + "</td><td>" + esc(r.engine) + "</td>" +
        (hasBy ? "<td>" + (r.by ? "<a href='?by=" + encodeURIComponent(r.by) + "'>" + esc(r.by) + "</a>" : "–") + "</td>" : "") +
        "<td><details><summary>" + esc(r.config_id) + '</summary><pre>' + esc(args) + "</pre><a href='result.html?id=" + encodeURIComponent(r.id) + "'>details</a> · <a href='" + esc(r.id) + "'>raw file</a>" +
        (r.status === "warnings" ? " <span class='pill warnings'>" + esc(r.warnings) + " warning(s)</span>" : "") + "</details></td>" +
        ["medium", "long", "xlong"].map(function (n) { return "<td class='num'>" + fmt(size(r, n, "prompt_tps"), 0) + "</td>"; }).join("") +
        "<td class='num'>" + (dec ? "<span class='bar' style='width:" + Math.round(60 * dec / maxDecode) + "px'></span>" : "") + fmt(dec) + "</td><td class='nowrap'>" + esc((r.date || "").slice(0, 10)) + "</td><td class='nowrap'><a class='open' href='result.html?id=" + encodeURIComponent(r.id) + "'>details →</a></td></tr>";
    }).join("");
    $("perf").innerHTML = h + "<tbody>" + (b || "<tr><td colspan='" + head.length + "'>No result matches the filters.</td></tr>") + "</tbody>";
    wirePicks();
    Array.prototype.forEach.call($("perf").querySelectorAll("tbody tr[data-id]"), function (tr) {
      tr.addEventListener("click", function (ev) { if (!ev.target.closest("a, input, summary, details, pre, label")) location.href = "result.html?id=" + encodeURIComponent(tr.dataset.id); });
    });
    Array.prototype.forEach.call($("perf").querySelectorAll("th"), function (th) {
      if (th.dataset.key === "select" || th.dataset.key === "open") return;
      th.addEventListener("click", function () { var k = th.dataset.key; state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : -1 }; drawPerf(); });
    });
  }

  var MAX_COMPARE = 3;
  function updateCompare() {
    var n = state.selected.length;
    $("compare-count").textContent = n ? n + " selected" + (n === 1 ? ", tick one more" : "") + (n >= MAX_COMPARE ? " (the most that fit)" : "") : "Tick two or three rows to compare them.";
    var go = $("compare-go");
    go.setAttribute("aria-disabled", n < 2 ? "true" : "false");
    go.href = n < 2 ? "#" : "compare.html?ids=" + state.selected.map(encodeURIComponent).join(",");
    Array.prototype.forEach.call($("perf").querySelectorAll("input.pick"), function (c) { c.disabled = !c.checked && n >= MAX_COMPARE; });
  }
  function wirePicks() {
    Array.prototype.forEach.call($("perf").querySelectorAll("input.pick"), function (c) {
      c.addEventListener("change", function () {
        var i = state.selected.indexOf(c.dataset.id);
        if (c.checked && i < 0 && state.selected.length < MAX_COMPARE) state.selected.push(c.dataset.id);
        if (!c.checked && i >= 0) state.selected.splice(i, 1);
        updateCompare();
      });
    });
    updateCompare();
  }

  function drawHealth() {
    var failed = state.rows.filter(function (r) { return r.status === "failed"; });
    var byKind = {}, byEngine = {};
    failed.forEach(function (r) { var k = r.failure && r.failure.kind; byKind[k] = (byKind[k] || 0) + 1; });
    state.rows.forEach(function (r) { var e = r.engine || "unknown"; byEngine[e] = byEngine[e] || { total: 0, failed: 0 }; byEngine[e].total++; if (r.status === "failed") byEngine[e].failed++; });
    var cardsHtml = Object.keys(byKind).sort().map(function (k) { return '<div class="card"><b>' + esc(byKind[k]) + "</b><span>" + esc(k) + "</span></div>"; }).join("");
    $("health-summary").innerHTML = cardsHtml || '<div class="card"><b>0</b><span>failures recorded</span></div>';
    var rowsHtml = failed.map(function (r) {
      return "<tr><td><span class='pill failed'>" + esc(r.failure && r.failure.kind) + "</span></td><td>" + esc(r.failure && r.failure.where) + "</td><td>" + esc(r.gpu) + "</td><td>" + esc(r.model) +
        "</td><td>" + esc(r.engine) + "</td><td>" + esc(r.config_id) + "</td><td>" + esc((r.date || "").slice(0, 10)) + "</td><td><a href='result.html?id=" + encodeURIComponent(r.id) + "'>details</a></td></tr>";
    }).join("");
    var eng = Object.keys(byEngine).sort().map(function (e) { return "<tr><td>" + esc(e) + "</td><td class='num'>" + byEngine[e].total + "</td><td class='num'>" + byEngine[e].failed + "</td></tr>"; }).join("");
    $("health").innerHTML = "<thead><tr><th>Failure</th><th>Where</th><th>GPU</th><th>Model</th><th>Engine</th><th>Config</th><th>Date</th><th></th></tr></thead><tbody>" +
      (rowsHtml || "<tr><td colspan='8'>No failures recorded.</td></tr>") + "</tbody>" +
      "<thead><tr><th>Engine</th><th class='num'>Runs</th><th class='num'>Failed</th><th colspan='5'></th></tr></thead><tbody>" + eng + "</tbody>";
  }

  function tabs() {
    var sel = function (which) {
      $("tab-perf").setAttribute("aria-selected", which === "perf"); $("tab-health").setAttribute("aria-selected", which === "health");
      $("view-perf").hidden = which !== "perf"; $("view-health").hidden = which !== "health";
      try { history.replaceState(null, "", which === "health" ? "#health" : "#"); } catch (e) { /* file:// */ }
    };
    $("tab-perf").addEventListener("click", function () { sel("perf"); });
    $("tab-health").addEventListener("click", function () { sel("health"); });
    if (location.hash === "#health") sel("health");
  }

  fetch("data.json", { cache: "no-cache" }).then(function (r) { return r.json(); }).then(function (data) {
    state.rows = data.results || [];
    cards(state.rows);
    tabs();
    if (!state.rows.length) { $("empty").hidden = false; $("view-perf").hidden = true; $("view-health").hidden = true; document.querySelector(".tabs").hidden = true; return; }
    readLink();
    try { var pre = new URLSearchParams(location.search).get("compare"); if (pre && state.rows.some(function (r) { return r.id === pre; })) state.selected = [pre]; } catch (e) { /* no URL support */ }   // from a detail page: start the comparison with that result
    ["by", "machine_id"].forEach(function (k) { if (uniq(state.rows, k).indexOf(state.filters[k]) < 0) state.filters[k] = ""; });   // a link to an unknown name shows everything
    filters(state.rows); drawPerf(); drawHealth();
  }).catch(function () {
    $("cards").innerHTML = '<div class="card"><b>–</b><span>data.json could not be loaded</span></div>';
  });
})();
