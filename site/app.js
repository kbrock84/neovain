// Renders the benchmark numbers, charts and demo from data/results.json. No dependencies.
(function () {
  "use strict";

  var ARMS = [
    { key: "neovain", label: "neovain" },
    { key: "edit", label: "Edit tool" }
  ];
  var MODELS = { opus: "Opus", sonnet: "Sonnet" };

  var fmt = {
    int: function (v) { return Math.round(v).toLocaleString("en-US"); },
    tokens: function (v) { return v >= 10000 ? (v / 1000).toFixed(1) + "k" : Math.round(v).toLocaleString("en-US"); },
    seconds: function (v) { return Math.round(v) + "s"; },
    usd: function (v) { return "$" + v.toFixed(2); },
    ratio: function (v) { return (v >= 10 ? Math.round(v) : v.toFixed(1)) + "×"; },
    chars: function (v) { return v >= 10000 ? (v / 1000).toFixed(1) + "k" : Math.round(v).toLocaleString("en-US"); }
  };

  function el(tag, attrs, text) {
    var node = document.createElementNS(tag === "svg" || attrs && attrs.svg ? "http://www.w3.org/2000/svg" : "http://www.w3.org/1999/xhtml", tag);
    for (var k in attrs || {}) if (k !== "svg") node.setAttribute(k, attrs[k]);
    if (text != null) node.textContent = text;
    return node;
  }
  function svgEl(tag, attrs, text) { attrs = attrs || {}; attrs.svg = true; return el(tag, attrs, text); }

  function get(obj, path) {
    return path.split(".").reduce(function (o, k) { return o == null ? o : o[k]; }, obj);
  }

  // Bar with a 4px rounded data end and a square baseline end.
  function barPath(x, y, w, h) {
    var r = Math.min(4, w / 2, h / 2);
    if (w <= 0) return "";
    return "M" + x + "," + y + "h" + (w - r) + "a" + r + "," + r + " 0 0 1 " + r + "," + r +
      "v" + (h - 2 * r) + "a" + r + "," + r + " 0 0 1 " + -r + "," + r + "h" + (r - w) + "z";
  }

  function niceMax(v) {
    var p = Math.pow(10, Math.floor(Math.log10(v)));
    var steps = [1, 2, 2.5, 5, 10];
    for (var i = 0; i < steps.length; i++) if (steps[i] * p >= v) return steps[i] * p;
    return 10 * p;
  }

  function tooltip(container) {
    var tip = container.querySelector(".tooltip");
    if (!tip) { tip = el("div", { class: "tooltip", role: "status" }); container.appendChild(tip); }
    return {
      show: function (text, x, y) {
        tip.textContent = text;
        tip.style.opacity = "1";
        var maxLeft = container.clientWidth - tip.offsetWidth - 4;
        tip.style.left = Math.max(4, Math.min(x - tip.offsetWidth / 2, maxLeft)) + "px";
        tip.style.top = (y - tip.offsetHeight - 8) + "px";
      },
      hide: function () { tip.style.opacity = "0"; }
    };
  }

  // Grouped horizontal bars: one group per model, one bar per arm.
  function barChart(container, groups, metric, format) {
    var host = container.querySelector("[data-plot]");
    host.textContent = "";
    var width = host.clientWidth || 300;
    var labelW = 64, valueW = 52, barH = 16, gap = 2, groupGap = 18, top = 4, axisH = 22;
    var plotW = Math.max(60, width - labelW - valueW);
    var max = niceMax(Math.max.apply(null, groups.map(function (g) {
      return Math.max.apply(null, ARMS.map(function (a) { return g[a.key][metric]; }));
    })));
    var groupH = ARMS.length * barH + (ARMS.length - 1) * gap;
    var height = top + groups.length * groupH + (groups.length - 1) * groupGap + axisH;
    var svg = svgEl("svg", { width: width, height: height, viewBox: "0 0 " + width + " " + height, role: "img",
      "aria-label": container.querySelector("h3").textContent });
    var x = function (v) { return labelW + (v / max) * plotW; };

    [0, 0.5, 1].forEach(function (t) {
      var gx = x(max * t);
      svg.appendChild(svgEl("line", { class: t === 0 ? "baseline" : "grid", x1: gx, x2: gx, y1: top - 2, y2: height - axisH + 2 }));
      var label = svgEl("text", { x: gx, y: height - 6, "text-anchor": t === 0 ? "start" : t === 1 ? "end" : "middle" }, format(max * t));
      svg.appendChild(label);
    });

    var tip = tooltip(container);
    groups.forEach(function (g, gi) {
      var gy = top + gi * (groupH + groupGap);
      svg.appendChild(svgEl("text", { class: "row-label", x: 0, y: gy + groupH / 2 + 4 }, MODELS[g.model] || g.model));
      ARMS.forEach(function (a, ai) {
        var v = g[a.key][metric];
        var y = gy + ai * (barH + gap);
        var node = svgEl("g", {});
        node.appendChild(svgEl("path", { class: "bar bar-" + a.key, d: barPath(labelW, y, x(v) - labelW, barH) }));
        node.appendChild(svgEl("text", { class: "value", x: x(v) + 6, y: y + barH / 2 + 4 }, format(v)));
        var hit = svgEl("rect", { class: "hit", x: labelW, y: y - 1, width: plotW + valueW, height: barH + 2 });
        var text = (MODELS[g.model] || g.model) + " · " + a.label + ": " + format(v) + " (mean of " + g[a.key].n + " runs)";
        hit.addEventListener("mousemove", function (e) {
          var r = container.getBoundingClientRect();
          node.classList.add("active");
          tip.show(text, e.clientX - r.left, y + 16 + container.querySelector("[data-plot]").offsetTop);
        });
        hit.addEventListener("mouseleave", function () { node.classList.remove("active"); tip.hide(); });
        node.appendChild(hit);
        svg.appendChild(node);
      });
    });
    host.appendChild(svg);
  }

  function renderCharts(data) {
    var groups = ["opus", "sonnet"].map(function (m) {
      return { model: m, neovain: data.large[m].neovain, edit: data.large[m].edit };
    });
    document.querySelectorAll("[data-chart]").forEach(function (c) {
      var metric = c.getAttribute("data-chart");
      barChart(c, groups, metric, fmt[c.getAttribute("data-format")] || fmt.int);
    });
  }

  function renderTable(target, rows, cols) {
    var table = el("table");
    var head = el("tr");
    cols.forEach(function (c) { head.appendChild(el("th", c.num ? { class: "num" } : {}, c.label)); });
    table.appendChild(el("thead")).appendChild(head);
    var body = table.appendChild(el("tbody"));
    rows.forEach(function (r) {
      var tr = el("tr");
      cols.forEach(function (c) {
        var td = el("td", c.num ? { class: "num" } : {});
        if (c.arm) {
          td.appendChild(el("span", { class: "swatch " + r.arm, "aria-hidden": "true" }));
          td.appendChild(document.createTextNode(" " + (r.arm === "edit" ? "Edit tool" : r.arm === "neovain" ? "neovain" : "neovain (ex-only)")));
        } else {
          td.textContent = c.fmt ? c.fmt(r[c.key]) : r[c.key];
        }
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    target.textContent = "";
    target.appendChild(table);
  }

  var COLS = [
    { key: "model", label: "Model", fmt: function (m) { return MODELS[m] || m; } },
    { key: "ctx", label: "Context" },
    { key: "arm", label: "Arm", arm: true },
    { key: "pass", label: "Passed", num: true },
    { key: "out_tok", label: "Output tokens", num: true, fmt: fmt.int },
    { key: "api_requests", label: "API requests", num: true, fmt: function (v) { return v.toFixed(1); } },
    { key: "tool_calls", label: "Tool calls", num: true, fmt: function (v) { return v.toFixed(1); } },
    { key: "wall_s", label: "Wall time", num: true, fmt: fmt.seconds },
    { key: "cost_usd", label: "Cost", num: true, fmt: fmt.usd }
  ];

  function renderDemo(data) {
    var cmd = document.querySelector("[data-demo-command]");
    if (cmd) cmd.textContent = data.demo.neovain_command;
    var list = document.querySelector("[data-demo-edits]");
    if (!list) return;
    var max = Math.max.apply(null, data.demo.edit_calls.map(function (c) { return c.old_chars + c.new_chars; }));
    list.textContent = "";
    data.demo.edit_calls.forEach(function (c, i) {
      var row = el("div", { class: "edit-call" });
      var label = el("div", { class: "edit-call-label" });
      label.appendChild(el("code", {}, "Edit #" + (i + 1)));
      label.appendChild(document.createTextNode(" " + c.summary));
      row.appendChild(label);
      var bar = el("div", { class: "edit-call-bar", title: fmt.int(c.old_chars) + " characters removed, " + fmt.int(c.new_chars) + " typed" });
      bar.appendChild(el("span", { class: "old", style: "width:" + (100 * c.old_chars / max) + "%" }));
      bar.appendChild(el("span", { class: "new", style: "width:" + (100 * c.new_chars / max) + "%" }));
      row.appendChild(bar);
      row.appendChild(el("div", { class: "edit-call-num" }, fmt.chars(c.old_chars + c.new_chars)));
      list.appendChild(row);
    });
  }

  function bind(data) {
    document.querySelectorAll("[data-bind]").forEach(function (node) {
      var v = get(data, node.getAttribute("data-bind"));
      if (v == null) return;
      var f = fmt[node.getAttribute("data-format")];
      node.textContent = f ? f(v) : v;
    });
  }

  fetch("data/results.json")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      bind(data);
      renderDemo(data);
      renderCharts(data);
      var large = document.querySelector("[data-table='large']");
      if (large) renderTable(large, data.large_rows, COLS);
      var small = document.querySelector("[data-table='small']");
      if (small) renderTable(small, data.small_rows, COLS);
      var timer;
      window.addEventListener("resize", function () {
        clearTimeout(timer);
        timer = setTimeout(function () { renderCharts(data); }, 120);
      });
    })
    .catch(function (err) { console.error("neovain: could not load results", err); });
})();
