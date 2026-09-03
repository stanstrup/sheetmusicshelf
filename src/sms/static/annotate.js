/* Annotation overlay for the reader.
 *
 * Coordinates are stored normalised to 0..1 of the page box, never pixels: the
 * same page is served at 320/800/1200/1800px depending on the device, and a
 * phone turned to landscape asks for a different one again. Pixels would put
 * the ink in the wrong place on every surface but the one it was drawn on.
 *
 * Pointer events rather than touch/mouse events, so a stylus, a finger and a
 * trackpad all take the same path — and a stylus reports pressure, which a
 * pen ought to use.
 */
(function () {
  "use strict";

  var root = document.getElementById("pages");
  if (!root) { return; }

  var pieceId = root.dataset.piece;
  var canWrite = root.dataset.canWrite === "1";
  var bar = document.getElementById("inkbar");

  var state = {
    on: false,
    tool: "pen",
    color: "#c0392b",
    width: 0.004,
    layers: {},        // page -> [stroke]
    dirty: {},         // page -> true
    drawing: null      // {page, stroke, canvas}
  };

  var HIGHLIGHTER_ALPHA = 0.35;

  // --- drawing ------------------------------------------------------------

  function canvasFor(figure) {
    var canvas = figure.querySelector("canvas.ink");
    if (canvas) { return canvas; }
    canvas = document.createElement("canvas");
    canvas.className = "ink";
    figure.appendChild(canvas);
    return canvas;
  }

  function sizeCanvas(figure) {
    var img = figure.querySelector("img");
    var canvas = canvasFor(figure);
    if (!img || !img.clientWidth) { return canvas; }
    // Back the canvas at device resolution so ink is not blurry, but keep its
    // CSS box exactly over the image.
    var ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(img.clientWidth * ratio);
    canvas.height = Math.round(img.clientHeight * ratio);
    canvas.style.width = img.clientWidth + "px";
    canvas.style.height = img.clientHeight + "px";
    return canvas;
  }

  function strokePath(ctx, stroke, w, h) {
    if (!stroke.points.length) { return; }
    ctx.save();
    ctx.strokeStyle = stroke.color;
    ctx.globalAlpha = stroke.tool === "highlighter" ? HIGHLIGHTER_ALPHA : 1;
    ctx.lineWidth = Math.max(stroke.width * w, 1);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(stroke.points[0][0] * w, stroke.points[0][1] * h);
    for (var i = 1; i < stroke.points.length; i++) {
      ctx.lineTo(stroke.points[i][0] * w, stroke.points[i][1] * h);
    }
    if (stroke.points.length === 1) {
      // A tap is a dot, not nothing.
      ctx.lineTo(stroke.points[0][0] * w + 0.01, stroke.points[0][1] * h);
    }
    ctx.stroke();
    ctx.restore();
  }

  function redraw(figure) {
    var page = parseInt(figure.dataset.page, 10);
    var canvas = sizeCanvas(figure);
    var ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    (state.layers[page] || []).forEach(function (stroke) {
      strokePath(ctx, stroke, canvas.width, canvas.height);
    });
    figure.classList.toggle("has-ink", (state.layers[page] || []).length > 0);
  }

  function redrawAll() {
    root.querySelectorAll(".page").forEach(redraw);
  }

  // --- input --------------------------------------------------------------

  function pointIn(figure, event) {
    var rect = figure.querySelector("img").getBoundingClientRect();
    return [
      Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1),
      Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1)
    ];
  }

  function eraseAt(page, point) {
    var strokes = state.layers[page] || [];
    var before = strokes.length;
    // Erase whole strokes rather than pixels: on a score you want the mark
    // gone, and hit-testing a polyline is cheap.
    state.layers[page] = strokes.filter(function (stroke) {
      return !stroke.points.some(function (p) {
        var dx = p[0] - point[0], dy = p[1] - point[1];
        return Math.sqrt(dx * dx + dy * dy) < Math.max(stroke.width * 2, 0.015);
      });
    });
    if (state.layers[page].length !== before) { state.dirty[page] = true; }
  }

  function onDown(event) {
    if (!state.on || !canWrite) { return; }
    var figure = event.target.closest(".page");
    if (!figure) { return; }
    var page = parseInt(figure.dataset.page, 10);
    event.preventDefault();
    figure.querySelector("canvas.ink").setPointerCapture(event.pointerId);

    if (state.tool === "eraser") {
      eraseAt(page, pointIn(figure, event));
      redraw(figure);
      state.drawing = {page: page, figure: figure, erasing: true};
      return;
    }

    var stroke = {
      tool: state.tool,
      color: state.color,
      // A stylus that reports pressure should vary the line; a finger reports
      // 0.5 and gets a constant width.
      width: state.width * (event.pressure && event.pressure !== 0.5 ? 0.5 + event.pressure : 1),
      points: [pointIn(figure, event)]
    };
    state.layers[page] = state.layers[page] || [];
    state.layers[page].push(stroke);
    state.dirty[page] = true;
    state.drawing = {page: page, figure: figure, stroke: stroke};
  }

  function onMove(event) {
    if (!state.drawing) { return; }
    event.preventDefault();
    var point = pointIn(state.drawing.figure, event);
    if (state.drawing.erasing) {
      eraseAt(state.drawing.page, point);
    } else {
      var points = state.drawing.stroke.points;
      var last = points[points.length - 1];
      // Drop points the eye cannot tell apart; a 60Hz drag otherwise stores
      // thousands per page.
      if (Math.abs(point[0] - last[0]) + Math.abs(point[1] - last[1]) < 0.002) { return; }
      points.push(point);
    }
    redraw(state.drawing.figure);
  }

  function onUp() {
    if (!state.drawing) { return; }
    var page = state.drawing.page;
    state.drawing = null;
    save(page);
  }

  // --- persistence --------------------------------------------------------

  var timers = {};
  function save(page) {
    if (!canWrite || !state.dirty[page]) { return; }
    clearTimeout(timers[page]);
    // Debounced: a phone can be locked mid-page, so save soon, not on every
    // stroke.
    timers[page] = setTimeout(function () {
      var strokes = state.layers[page] || [];
      fetch("/api/v1/pieces/" + pieceId + "/annotations/" + page, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({page: page, strokes: strokes})
      }).then(function (response) {
        if (response.ok) {
          delete state.dirty[page];
          flash("saved");
        } else {
          flash("not saved", true);
        }
      }).catch(function () { flash("not saved", true); });
    }, 700);
  }

  function flash(message, bad) {
    var el = document.getElementById("inkstatus");
    if (!el) { return; }
    el.textContent = message;
    el.classList.toggle("bad", !!bad);
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.textContent = ""; }, 2000);
  }

  function load() {
    fetch("/api/v1/pieces/" + pieceId + "/annotations")
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (layers) {
        layers.forEach(function (layer) { state.layers[layer.page] = layer.strokes; });
        redrawAll();
      })
      .catch(function () { /* an unreachable server should not break reading */ });
  }

  // --- wiring -------------------------------------------------------------

  root.querySelectorAll(".page").forEach(function (figure) {
    var canvas = canvasFor(figure);
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointercancel", onUp);
    var img = figure.querySelector("img");
    if (img) { img.addEventListener("load", function () { redraw(figure); }); }
  });

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(redrawAll, 150);
  });

  // The toggle lives in the reader bar, not in the ink bar, so it needs its own
  // listener -- delegating from #inkbar alone never sees it.
  var toggle = document.querySelector('[data-ink="toggle"]');
  if (toggle && bar) {
    toggle.addEventListener("click", function () {
      state.on = !state.on;
      root.classList.toggle("inking", state.on);
      bar.classList.toggle("open", state.on);
      toggle.setAttribute("aria-pressed", String(state.on));
    });
  }

  if (bar) {
    bar.addEventListener("click", function (event) {
      var button = event.target.closest("[data-ink]");
      if (!button) { return; }
      var action = button.dataset.ink;

      if (action === "tool") {
        state.tool = button.dataset.tool;
        state.width = button.dataset.tool === "highlighter" ? 0.02 : 0.004;
        bar.querySelectorAll("[data-ink='tool']").forEach(function (b) {
          b.classList.toggle("on", b === button);
        });
        return;
      }
      if (action === "color") {
        state.color = button.dataset.color;
        if (state.tool === "eraser") { state.tool = "pen"; }
        bar.querySelectorAll("[data-ink='color']").forEach(function (b) {
          b.classList.toggle("on", b === button);
        });
        return;
      }
      if (action === "undo") {
        var page = currentPage();
        var strokes = state.layers[page] || [];
        if (strokes.length) {
          strokes.pop();
          state.dirty[page] = true;
          redraw(figureFor(page));
          save(page);
        }
        return;
      }
      if (action === "clear") {
        var target = currentPage();
        if ((state.layers[target] || []).length && window.confirm("Clear all marks on page " + target + "?")) {
          state.layers[target] = [];
          state.dirty[target] = true;
          redraw(figureFor(target));
          save(target);
        }
      }
    });
  }

  function figureFor(page) {
    return root.querySelector('.page[data-page="' + page + '"]');
  }

  function currentPage() {
    var progress = document.getElementById("progress");
    return parseInt((progress && progress.textContent) || "1", 10) || 1;
  }

  load();
}());
