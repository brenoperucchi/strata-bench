"use strict";
(function () {
  var state = { rows: [], filters: { gpu: "", model: "", engine: "", status: "", by: "", machine_id: "", protocol: "" }, selected: [], sort: { key: "date", dir: -1 } };
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  // every value that goes into a table cell as HTML: numbers are formatted, anything else is escaped (a result file is data, never markup)
  var fmt = function (v, d) { return v === null || v === undefined ? "–" : (typeof v === "number" && isFinite(v) ? v.toLocaleString(undefined, { maximumFractionDigits: d === undefined ? 1 : d }) : esc(v)); };
  var uniq = function (rows, key) { var s = {}; rows.forEach(function (r) { if (r[key]) s[r[key]] = 1; }); return Object.keys(s).sort(); };
  var size = function (r, name, field) { return r.sizes && r.sizes[name] ? r.sizes[name][field] : null; };

  function cards(rows) {
    if (typeof StrataOverview !== "undefined") { $("cards").innerHTML = StrataOverview.kpi(rows); return; }
    var perf = rows.filter(function (r) { return r.status !== "failed"; });
    var failed = rows.length - perf.length;
    var items = [[rows.length, "results"], [uniq(rows, "gpu").length, "GPU models"], [uniq(rows, "engine").length, "engine versions"], [failed, "failed runs"]];
    $("cards").innerHTML = items.map(function (i) { return '<div class="card"><b>' + esc(i[0]) + "</b><span>" + esc(i[1]) + "</span></div>"; }).join("");
  }

  // the overview panels need the insight modules; the table and the filters below work without them
  function overview(rows) {
    var O = typeof StrataOverview !== "undefined" ? StrataOverview : null;
    if (!O || !rows.length) return null;
    $("overview").hidden = false;                                  // shown first: the speed map is drawn at the width its card has
    [["ov-pairs", O.pairs], ["ov-same", O.sameModel]].forEach(function (p) { var html = p[1](rows); $(p[0]).innerHTML = html; $(p[0]).hidden = !html; });
    var map = $("ov-map");
    var view = {};                                                  // the axis ranges typed in; empty means the default
    var draw = function () {
      var w = map.clientWidth, open = !!(map.classList && map.classList.contains("expanded"));
      map.innerHTML = O.speedMap(rows, w ? w - 32 : 0, view, open && typeof window !== "undefined" && window.innerHeight ? window.innerHeight - 270 : 0);   // opened over the page, the map takes the screen's height
      map.hidden = !map.innerHTML; syncExpand(map);
    };
    draw();
    expandable(map, draw, view, function () { return O.fit(rows); });
    return draw;                                                    // drawn again when the Dashboard tab is shown: its card has no width while the tab is hidden
  }

  // The speed map can be opened over the page: a button on the card, and Escape or the button again to close it. It is redrawn at each size.
  function syncExpand(panel) {
    var on = !!(panel.classList && panel.classList.contains("expanded")), b = panel.querySelector("[data-expand]");
    if (b) { b.setAttribute("aria-expanded", String(on)); b.textContent = on ? "Close" : "Expand"; b.title = on ? "Back to the page" : "Show the speed map larger"; }
  }
  function expandable(panel, draw, view, fit) {
    if (!panel || !panel.addEventListener) return;
    var set = function (on) { panel.classList.toggle("expanded", on); draw(); };
    var closest = function (e, sel) { return e.target && e.target.closest && e.target.closest(sel); };
    panel.addEventListener("click", function (e) {
      if (closest(e, "[data-expand]")) set(!panel.classList.contains("expanded"));
      else if (closest(e, "[data-fit]")) { var f = fit(); if (f) { ["xmin", "xmax", "ymin", "ymax"].forEach(function (k) { view[k] = f[k]; }); draw(); } }
      else if (closest(e, "[data-reset]")) { ["xmin", "xmax", "ymin", "ymax"].forEach(function (k) { delete view[k]; }); draw(); }
    });
    // + and − on an axis: the range shrinks or grows around its middle; a drag on the chart zooms into the rectangle
    var current = function () { var svg = panel.querySelector("svg[data-view]"); if (!svg) return null; var v = svg.getAttribute("data-view").split(",").map(Number); return { svg: svg, x: [v[0], v[1]], y: [v[2], v[3]] }; };
    var round = function (v) { return +v.toPrecision(4); };
    var zoomAxis = function (axis, factor) {
      var c = current(); if (!c) return;
      var lo = c[axis][0], hi = c[axis][1], mid = (lo + hi) / 2, half = (hi - lo) / 2 / factor;
      view[axis + "min"] = round(Math.max(0, mid - half)); view[axis + "max"] = round(mid + half);
      draw();
    };
    panel.addEventListener("click", function (e) {
      var b = closest(e, "[data-zoom]"); if (!b) return;
      var d = b.getAttribute("data-zoom"); zoomAxis(d.charAt(0), d.charAt(1) === "+" ? 1.6 : 1 / 1.6);
    });
    var drag = null;
    panel.addEventListener("pointerdown", function (e) {
      var svg = closest(e, "svg[data-view]");
      if (!svg || e.button !== 0 || closest(e, "a")) return;
      var r = svg.getBoundingClientRect(), p = svg.getAttribute("data-plot").split(",").map(Number);
      var k = p[4] / r.width, at = function (ev) { return [Math.max(p[0], Math.min(p[4] - p[1], (ev.clientX - r.left) * k)), Math.max(p[2], Math.min(p[5] - p[3], (ev.clientY - r.top) * k))]; };
      var sel = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      sel.setAttribute("class", "sel"); svg.appendChild(sel);
      drag = { svg: svg, from: at(e), at: at, sel: sel, p: p };
      e.preventDefault();
    });
    if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
      window.addEventListener("pointermove", function (e) {
        if (!drag) return;
        var a = drag.from, b = drag.at(e);
        drag.sel.setAttribute("x", Math.min(a[0], b[0])); drag.sel.setAttribute("y", Math.min(a[1], b[1]));
        drag.sel.setAttribute("width", Math.abs(a[0] - b[0])); drag.sel.setAttribute("height", Math.abs(a[1] - b[1]));
      });
      window.addEventListener("pointerup", function (e) {
        if (!drag) return;
        var d = drag, a = d.from, b = d.at(e), c = current(); drag = null;
        if (d.sel.parentNode) d.sel.parentNode.removeChild(d.sel);
        if (!c || Math.abs(a[0] - b[0]) < 8 || Math.abs(a[1] - b[1]) < 8) return;          // a click, not a drag
        var p = d.p, w = p[4] - p[0] - p[1], h = p[5] - p[2] - p[3];
        var xv = function (px) { return c.x[0] + (px - p[0]) / w * (c.x[1] - c.x[0]); }, yv = function (py) { return c.y[0] + ((p[5] - p[3]) - py) / h * (c.y[1] - c.y[0]); };
        view.xmin = round(Math.max(0, Math.min(xv(a[0]), xv(b[0])))); view.xmax = round(Math.max(xv(a[0]), xv(b[0])));
        view.ymin = round(Math.max(0, Math.min(yv(a[1]), yv(b[1])))); view.ymax = round(Math.max(yv(a[1]), yv(b[1])));
        draw();
      });
    }
    panel.addEventListener("change", function (e) {                 // an axis box was edited: empty, or a number
      var box = closest(e, "[data-axis]"); if (!box) return;
      var v = parseFloat(box.value);
      if (isFinite(v) && v >= 0) view[box.getAttribute("data-axis")] = v; else delete view[box.getAttribute("data-axis")];
      draw();
    });
    if (typeof document.addEventListener === "function") document.addEventListener("keydown", function (e) { if (e.key === "Escape" && panel.classList.contains("expanded")) set(false); });
    if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
      var width = panel.clientWidth, timer = null;
      window.addEventListener("resize", function () { clearTimeout(timer); timer = setTimeout(function () { if (Math.abs(panel.clientWidth - width) > 8) { width = panel.clientWidth; draw(); } }, 150); });
    }
  }

  function filters(rows) {
    var defs = [["gpu", "GPU"], ["model", "Model"], ["engine", "Engine"], ["status", "Status"], ["by", "Submitted by"], ["machine_id", "Machine"], ["protocol", "Protocol"]].filter(function (d) {
      if (d[0] === "protocol") return uniq(rows, "protocol").length > 1;                       // only worth a filter once both protocols are on the page
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

  var clients8 = function (r) { var c = (Array.isArray(r.clients) ? r.clients : []).filter(function (x) { return x && x.n === 8; })[0]; return c ? c.total_tps : null; };

  function sortVal(r, key) {
    if (key === "date") return r.date || "";
    if (key === "decode") return size(r, "medium", "decode_tps") || 0;
    if (key === "ttft") return size(r, "medium", "ttft_ms") || 0;
    if (key === "agent") return (r.agent && r.agent.hit_pct) || 0;
    if (key === "clients8") return clients8(r) || 0;
    if (key.indexOf("prompt_") === 0) return size(r, key.slice(7), "prompt_tps") || 0;
    return (r[key] || "").toString().toLowerCase();
  }

  function drawPerf() {
    var rows = state.rows.filter(function (r) {
      return r.status !== "failed" && (!state.filters.gpu || r.gpu === state.filters.gpu) && (!state.filters.model || r.model === state.filters.model) &&
        (!state.filters.engine || r.engine === state.filters.engine) && (!state.filters.status || r.status === state.filters.status) &&
        (!state.filters.by || r.by === state.filters.by) && (!state.filters.machine_id || r.machine_id === state.filters.machine_id) &&
        (!state.filters.protocol || r.protocol === state.filters.protocol);
    });
    rows.sort(function (a, b) { var x = sortVal(a, state.sort.key), y = sortVal(b, state.sort.key); return (x < y ? -1 : x > y ? 1 : 0) * state.sort.dir; });
    var maxDecode = Math.max.apply(null, rows.map(function (r) { return size(r, "medium", "decode_tps") || 0; }).concat([1]));
    var hasBy = state.rows.some(function (r) { return r.by; });
    var hasV2 = state.rows.some(function (r) { return r.protocol === "standard-v2"; });
    var head = [["select", ""], ["gpu", "GPU"], ["quant", "Quant", 0, "The quantisation; hover a cell for the model it belongs to"], ["engine", "Engine"]].concat(hasBy ? [["by", "By"]] : []).concat([["prompt_medium", "Medium", 1, "Prompt speed for the medium prompt (tokens per second)"], ["prompt_long", "Long", 1, "Prompt speed for the long prompt, in tokens per second"], ["prompt_xlong", "XLong", 1, "Prompt speed for the xlong prompt, in tokens per second"], ["decode", "Decode", 1, "Decode speed for the medium prompt, in tokens per second"]]).concat(hasV2 ? [["ttft", "TTFT ms", 1, "Time to first token for the medium prompt, in milliseconds"], ["agent", "Agent", 1, "Agent cache: percent of the earlier conversation the cache served in the agent session"]] : []).concat([["date", "Date"]]);
    var h = "<thead><tr>" + head.map(function (c) { return '<th data-key="' + c[0] + '"' + (c[2] ? ' class="num"' : "") + (c[3] ? ' title="' + esc(c[3]) + '"' : "") + ">" + c[1] + (state.sort.key === c[0] ? (state.sort.dir > 0 ? " ▲" : " ▼") : "") + "</th>"; }).join("") + "</tr></thead>";
    var b = rows.map(function (r) {
      var dec = size(r, "medium", "decode_tps"); if (typeof dec !== "number") dec = null;
      var args = (Array.isArray(r.args) ? r.args : []).join(" ");
      return "<tr class='openable' title='Click for more details' data-id='" + esc(r.id) + "'><td class='pick-cell'><input type='checkbox' class='pick' data-id='" + esc(r.id) + "' aria-label='Select for comparison'" + (state.selected.indexOf(r.id) >= 0 ? " checked" : "") + "></td><td" + (r.gpu_full ? " title='" + esc(r.gpu_full) + "'" : "") + ">" + esc(r.gpu) + (r.vram_gb ? " <span class='hint'>" + esc(r.vram_gb) + " GB</span>" : "") + (r.gpu_other > 0 ? " <span class='hint'>+" + esc(r.gpu_other) + "</span>" : r.gpu_count > 1 ? " <span class='hint'>×" + esc(r.gpu_count) + "</span>" : "") +
        (r.protocol === "standard-v2" ? "<br><span class='hint'>v2 " + esc(r.suite) + "</span>" + (r.host_relation && r.host_relation !== "same-host" ? " <span class='pill warnings' title='The client ran on another machine, so first-token times include the network'>" + esc(r.host_relation) + "</span>" : "") : "") +
        "</td><td title='" + esc(r.model_full || r.model || "") + "'>" + (r.quant ? "<b>" + esc(r.quant) + "</b>" : esc(r.model)) + "</td><td>" + esc(r.engine) + (r.engine_source && !/^official/i.test(r.engine_source) ? "<br><span class='pill warnings' title='" + esc(r.engine_source) + "'>custom build</span>" : "") + "</td>" +
        (hasBy ? "<td class='by'>" + (r.by ? "<a href='?by=" + encodeURIComponent(r.by) + "' title='" + esc(r.by) + "'>" + esc(r.by) + "</a>" : "–") + "</td>" : "") +
        ["medium", "long", "xlong"].map(function (n) { return "<td class='num'>" + fmt(size(r, n, "prompt_tps"), 0) + "</td>"; }).join("") +
        "<td class='num'>" + (dec ? "<span class='bar' style='width:" + Math.round(40 * dec / maxDecode) + "px'></span>" : "") + fmt(dec) + "</td>" + (hasV2 ? "<td class='num'>" + fmt(size(r, "medium", "ttft_ms"), 0) + "</td><td class='num'>" + fmt(r.agent && r.agent.hit_pct, 1) + "</td>" : "") + "<td class='nowrap'>" + esc((r.date || "").slice(0, 10)) + "</td></tr>";
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
    $("compare-count").textContent = n ? n + " selected" + (n === 1 ? ", tick one more" : "") + (n >= MAX_COMPARE ? " (the most that fit)" : "") : "Tick two or three rows to compare them: a scoreboard shows who is in front on more measures.";
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

  // Dashboard (the overview), Results (the table) and Health. The address keeps the tab: #results, #health, nothing for the Dashboard.
  // A link that carries a filter (?by=, ?machine=, ?compare=) is about the table, so it opens on Results.
  var TABS = [["dash", "dashboard"], ["perf", "results"], ["health", "health"]];
  function tabs(redrawMap) {
    var fromHash = function () {
      var h = String(location.hash || "").replace(/^#/, "").toLowerCase(), t = TABS.filter(function (x) { return x[1] === h; })[0];
      if (t) return t[0];
      try { var q = new URLSearchParams(location.search); if (q.get("by") || q.get("machine") || q.get("compare")) return "perf"; } catch (e) { /* no URL support */ }
      return "dash";
    };
    var sel = function (which, keep) {
      TABS.forEach(function (t) { $("tab-" + t[0]).setAttribute("aria-selected", t[0] === which); $("view-" + t[0]).hidden = t[0] !== which; });
      if (which === "dash" && redrawMap) redrawMap();
      if (keep) return;
      var name = TABS.filter(function (t) { return t[0] === which; })[0][1];
      try { history.replaceState(null, "", location.pathname + location.search + (which === "dash" ? "" : "#" + name)); } catch (e) { /* file:// */ }
    };
    TABS.forEach(function (t) { $("tab-" + t[0]).addEventListener("click", function () { sel(t[0]); }); });
    if (typeof window !== "undefined" && typeof window.addEventListener === "function") window.addEventListener("hashchange", function () { sel(fromHash(), true); });
    sel(fromHash(), true);
  }

  fetch("data.json", { cache: "no-cache" }).then(function (r) { return r.json(); }).then(function (data) {
    state.rows = data.results || [];
    cards(state.rows);
    var redrawMap = overview(state.rows);
    tabs(redrawMap);
    if (!state.rows.length) { $("empty").hidden = false; $("view-dash").hidden = true; $("view-perf").hidden = true; $("view-health").hidden = true; document.querySelector(".tabs").hidden = true; return; }
    readLink();
    try { var pre = new URLSearchParams(location.search).get("compare"); if (pre && state.rows.some(function (r) { return r.id === pre; })) state.selected = [pre]; } catch (e) { /* no URL support */ }   // from a detail page: start the comparison with that result
    ["by", "machine_id"].forEach(function (k) { if (uniq(state.rows, k).indexOf(state.filters[k]) < 0) state.filters[k] = ""; });   // a link to an unknown name shows everything
    filters(state.rows); drawPerf(); drawHealth();
  }).catch(function () {
    $("cards").innerHTML = '<div class="card"><b>–</b><span>data.json could not be loaded</span></div>';
  });
})();
