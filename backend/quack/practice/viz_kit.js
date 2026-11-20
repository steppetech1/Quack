/* Quack visualization kit: small helpers every generated demo page may use through window.QV.
   Written once and checked by hand, so a model-written page is mostly its own physics or math, not layout code.
   The page is inlined into the document before the check and before publication (the same bytes in both). */
(function () {
  "use strict";
  var QV = (window.QV = {});
  var theme = (QV.theme = {
    bg: "#141311", panel: "#1c1a17", ink: "#e8e2d6", dim: "#9b9486", grid: "#2e2a25",
    accent: "#edb966", blue: "#7ab0e6", red: "#e8806f", green: "#8fcf8f", violet: "#b99be6"
  });
  var params = (QV.params = Object.assign({}, window.QUACK_VIZ_PARAMS || {}));
  QV.param = function (name, fallback) {
    return Object.prototype.hasOwnProperty.call(params, name) ? params[name] : fallback;
  };

  var css = document.createElement("style");
  css.textContent =
    "*{box-sizing:border-box}html,body{margin:0;height:100%;background:" + theme.bg + ";color:" + theme.ink +
    ";font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}" +
    ".qv{display:flex;flex-direction:column;gap:10px;min-height:100%;padding:12px}" +
    ".qv h1{margin:0;font-size:15px;font-weight:600}.qv .sub{color:" + theme.dim + ";font-size:12px;margin-top:2px}" +
    ".qv .stage{position:relative;flex:1;min-height:220px;background:" + theme.panel + ";border:1px solid " + theme.grid + ";border-radius:6px;overflow:hidden}" +
    ".qv canvas{display:block}.qv svg.stage-svg{display:block;width:100%;height:100%}" +
    ".qv .controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px 16px}" +
    ".qv label{display:flex;flex-direction:column;gap:3px;font-size:12px;color:" + theme.dim + "}" +
    ".qv label b{color:" + theme.ink + ";font-weight:500;display:flex;justify-content:space-between}" +
    ".qv input[type=range]{width:100%;accent-color:" + theme.accent + "}" +
    ".qv .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}" +
    ".qv button{background:transparent;color:" + theme.ink + ";border:1px solid " + theme.dim + ";border-radius:999px;padding:6px 14px;font:inherit;cursor:pointer}" +
    ".qv button:hover{border-color:" + theme.accent + ";color:" + theme.accent + "}" +
    ".qv .readout{display:flex;flex-wrap:wrap;gap:4px 18px;font:12px ui-monospace,Consolas,monospace;color:" + theme.dim + "}" +
    ".qv .readout span b{color:" + theme.ink + ";font-weight:500}.qv .note{font-size:12px;color:" + theme.dim + "}";
  (document.head || document.documentElement).appendChild(css);

  function el(tag, cls, parent, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  }

  /* QV.mount({title, subtitle}) -> {root, stage, controls, readout, note(text)} : the frame of the page */
  QV.mount = function (o) {
    o = o || {};
    var root = el("div", "qv", document.body);
    var head = el("div", "", root);
    el("h1", "", head, o.title || "");
    if (o.subtitle) el("div", "sub", head, o.subtitle);
    var stage = el("div", "stage", root);
    var readout = el("div", "readout", root);
    var controls = el("div", "controls", root);
    var noteEl = el("div", "note", root);
    return { root: root, stage: stage, controls: controls, readout: readout, note: function (t) { noteEl.textContent = t || ""; } };
  };

  /* QV.canvas(stage, draw) -> {ctx, canvas, size(), redraw()} : a sharp canvas that fills the stage and calls draw(ctx, w, h) after
     every resize and every redraw(). Sharp means the backing store is exactly the device pixels the stage covers: its size is taken in
     device pixels (the browser reports them for the content box; else css size times the pixel ratio, rounded), the css size is that
     number divided by the ratio, and the canvas is out of the layout (absolute) so it cannot change the size it is fitted to. With a
     fractional screen scale (125%, 150%), a page zoom or a window moved to another screen the ratio changes: the canvas is fitted
     again. w and h are the css size of the canvas. */
  QV.canvas = function (stage, draw) {
    var c = el("canvas", "", stage);
    c.style.position = "absolute"; c.style.left = "0"; c.style.top = "0";
    var ctx = c.getContext("2d");
    var w = 0, h = 0;
    function fit(devW, devH) {
      var dpr = window.devicePixelRatio || 1;
      if (!devW || !devH) { devW = Math.round(stage.clientWidth * dpr); devH = Math.round(stage.clientHeight * dpr); }
      devW = Math.max(50, Math.round(devW)); devH = Math.max(50, Math.round(devH));
      c.width = devW; c.height = devH;
      w = devW / dpr; h = devH / dpr;
      c.style.width = w + "px"; c.style.height = h + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    function redraw() { ctx.setTransform(c.width / w, 0, 0, c.height / h, 0, 0); ctx.clearRect(0, 0, w, h); draw(ctx, w, h); }
    fit();
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(function (entries) {
        var e = entries[entries.length - 1], box = e && e.devicePixelContentBoxSize && e.devicePixelContentBoxSize[0];
        if (box) fit(box.inlineSize, box.blockSize); else fit();
        redraw();
      });
      try { ro.observe(stage, { box: "device-pixel-content-box" }); } catch (err) { ro.observe(stage); }
    }
    window.addEventListener("resize", function () { fit(); redraw(); });
    function watchRatio() {
      if (!window.matchMedia) return;
      var mq = window.matchMedia("(resolution: " + (window.devicePixelRatio || 1) + "dppx)");
      var onChange = function () { fit(); redraw(); watchRatio(); };
      if (mq.addEventListener) mq.addEventListener("change", onChange, { once: true });
    }
    watchRatio();
    return { ctx: ctx, canvas: c, size: function () { return { w: w, h: h }; }, redraw: redraw };
  };

  /* QV.slider(controls, {id,label,min,max,step,value,unit,digits}, onChange) -> {get(), set(v)} ; the start value comes
     from QUACK_VIZ_PARAMS when the page is opened with one */
  QV.slider = function (controls, o, onChange) {
    var start = Number(QV.param(o.id, o.value));
    var lab = el("label", "", controls);
    var top = el("b", "", lab);
    var name = el("span", "", top, o.label);
    var val = el("span", "", top);
    var inp = el("input", "", lab);
    inp.type = "range"; inp.min = o.min; inp.max = o.max; inp.step = o.step || (o.max - o.min) / 100; inp.value = start;
    var digits = o.digits == null ? 2 : o.digits;
    function show() { val.textContent = Number(inp.value).toFixed(digits) + (o.unit ? " " + o.unit : ""); params[o.id] = Number(inp.value); }
    inp.addEventListener("input", function () { show(); if (onChange) onChange(Number(inp.value)); });
    show();
    return { get: function () { return Number(inp.value); }, set: function (v) { inp.value = v; show(); if (onChange) onChange(Number(inp.value)); }, label: name };
  };

  /* QV.toggle(controls, {id,label,value}, onChange) -> {get()} */
  QV.toggle = function (controls, o, onChange) {
    var lab = el("label", "", controls);
    lab.style.flexDirection = "row"; lab.style.alignItems = "center"; lab.style.gap = "8px";
    var inp = el("input", "", lab);
    inp.type = "checkbox"; inp.checked = !!QV.param(o.id, o.value);
    el("span", "", lab, o.label);
    function sync() { params[o.id] = inp.checked; }
    inp.addEventListener("change", function () { sync(); if (onChange) onChange(inp.checked); });
    sync();
    return { get: function () { return inp.checked; } };
  };

  /* QV.button(parent, label, onClick) */
  QV.button = function (parent, label, onClick) {
    var b = el("button", "", parent, label);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  };

  /* QV.readout(node, [[name, value], ...]) : "name value" pairs, replaced on each call */
  QV.readout = function (node, rows) {
    node.textContent = "";
    rows.forEach(function (r) {
      var s = el("span", "", node, r[0] + " ");
      el("b", "", s, String(r[1]));
    });
  };

  /* QV.loop(step) -> {play(), pause(), toggle(), reset(), running()} ; step(dt seconds) is called each frame while it runs;
     dt is capped so a hidden tab does not jump. */
  QV.loop = function (step) {
    var raf = 0, last = 0, on = false;
    function frame(t) {
      if (!on) return;
      var dt = Math.min(0.05, (t - last) / 1000 || 0);
      last = t;
      step(dt);
      raf = requestAnimationFrame(frame);
    }
    var api = {
      play: function () { if (on) return; on = true; last = performance.now(); raf = requestAnimationFrame(frame); },
      pause: function () { on = false; cancelAnimationFrame(raf); },
      toggle: function () { on ? api.pause() : api.play(); return on; },
      running: function () { return on; }
    };
    return api;
  };

  /* QV.arrow(ctx, x0, y0, x1, y1, color, width) : a vector arrow; label optional */
  QV.arrow = function (ctx, x0, y0, x1, y1, color, width, label) {
    var dx = x1 - x0, dy = y1 - y0, len = Math.hypot(dx, dy);
    if (len < 1) return;
    var a = Math.atan2(dy, dx), head = Math.min(12, len * 0.4);
    ctx.save();
    ctx.strokeStyle = ctx.fillStyle = color || theme.accent;
    ctx.lineWidth = width || 2;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1 - Math.cos(a) * head * 0.6, y1 - Math.sin(a) * head * 0.6); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x1, y1);
    ctx.lineTo(x1 - head * Math.cos(a - 0.4), y1 - head * Math.sin(a - 0.4));
    ctx.lineTo(x1 - head * Math.cos(a + 0.4), y1 - head * Math.sin(a + 0.4));
    ctx.closePath(); ctx.fill();
    if (label) { ctx.font = "12px system-ui,sans-serif"; ctx.fillText(label, x1 + 6, y1 - 6); }
    ctx.restore();
  };

  function niceTicks(lo, hi, n) {
    var span = hi - lo; if (!(span > 0)) return [lo];
    var raw = span / n, p = Math.pow(10, Math.floor(Math.log10(raw)));
    var step = [1, 2, 5, 10].map(function (m) { return m * p; }).filter(function (s) { return s >= raw; })[0];
    var out = [];
    for (var v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
    return out;
  }

  /* QV.axes(ctx, box{x,y,w,h}, xr[min,max], yr[min,max], {xlabel, ylabel, grid}) -> {sx(x), sy(y)} : draws axes with ticks and labels and
     returns the maps from data to pixels, to draw the curves with. */
  QV.axes = function (ctx, box, xr, yr, o) {
    o = o || {};
    var sx = function (x) { return box.x + ((x - xr[0]) / (xr[1] - xr[0])) * box.w; };
    var sy = function (y) { return box.y + box.h - ((y - yr[0]) / (yr[1] - yr[0])) * box.h; };
    ctx.save();
    ctx.font = "11px ui-monospace,Consolas,monospace"; ctx.fillStyle = theme.dim; ctx.strokeStyle = theme.grid; ctx.lineWidth = 1;
    niceTicks(xr[0], xr[1], 6).forEach(function (v) {
      var x = sx(v);
      if (o.grid !== false) { ctx.beginPath(); ctx.moveTo(x, box.y); ctx.lineTo(x, box.y + box.h); ctx.stroke(); }
      ctx.textAlign = "center"; ctx.fillText(String(+v.toPrecision(4)), x, box.y + box.h + 14);
    });
    niceTicks(yr[0], yr[1], 5).forEach(function (v) {
      var y = sy(v);
      if (o.grid !== false) { ctx.beginPath(); ctx.moveTo(box.x, y); ctx.lineTo(box.x + box.w, y); ctx.stroke(); }
      ctx.textAlign = "right"; ctx.fillText(String(+v.toPrecision(4)), box.x - 6, y + 3);
    });
    ctx.strokeStyle = theme.dim; ctx.lineWidth = 1.2;
    var ax = yr[0] <= 0 && yr[1] >= 0 ? sy(0) : box.y + box.h, ay = xr[0] <= 0 && xr[1] >= 0 ? sx(0) : box.x;
    ctx.beginPath(); ctx.moveTo(box.x, ax); ctx.lineTo(box.x + box.w, ax); ctx.moveTo(ay, box.y); ctx.lineTo(ay, box.y + box.h); ctx.stroke();
    ctx.fillStyle = theme.ink; ctx.font = "12px system-ui,sans-serif";
    if (o.xlabel) { ctx.textAlign = "right"; ctx.fillText(o.xlabel, box.x + box.w, box.y + box.h + 28); }
    if (o.ylabel) { ctx.textAlign = "left"; ctx.fillText(o.ylabel, box.x + 6, box.y + 12); }
    ctx.restore();
    return { sx: sx, sy: sy };
  };

  /* QV.curve(ctx, maps, fn, xr, color, width, steps) : draws y = fn(x) over xr, breaking the line at non-finite values */
  QV.curve = function (ctx, maps, fn, xr, color, width, steps) {
    steps = steps || 300;
    ctx.save(); ctx.strokeStyle = color || theme.accent; ctx.lineWidth = width || 2; ctx.lineJoin = "round"; ctx.beginPath();
    var pen = false;
    for (var i = 0; i <= steps; i++) {
      var x = xr[0] + ((xr[1] - xr[0]) * i) / steps, y = fn(x);
      if (!isFinite(y)) { pen = false; continue; }
      if (pen) ctx.lineTo(maps.sx(x), maps.sy(y)); else { ctx.moveTo(maps.sx(x), maps.sy(y)); pen = true; }
    }
    ctx.stroke(); ctx.restore();
  };

  /* QV.drag(canvas, onMove(x, y, down)) : pointer position in CSS pixels while the button is down (mouse and touch) */
  QV.drag = function (canvas, onMove) {
    var down = false;
    function pos(e) { var r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
    canvas.style.touchAction = "none";
    canvas.addEventListener("pointerdown", function (e) { down = true; canvas.setPointerCapture(e.pointerId); var p = pos(e); onMove(p[0], p[1], true); });
    canvas.addEventListener("pointermove", function (e) { if (down) { var p = pos(e); onMove(p[0], p[1], true); } });
    canvas.addEventListener("pointerup", function (e) { down = false; var p = pos(e); onMove(p[0], p[1], false); });
  };
})();
