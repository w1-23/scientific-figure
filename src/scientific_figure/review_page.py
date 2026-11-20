"""Self-contained review page: no external scripts, fonts or network access."""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scientific Figure review</title>
<style>
:root { color-scheme: light; --ink:#1c2430; --muted:#5d6b7a; --line:#d7dee6; --bg:#f3f5f7; --panel:#ffffff; --accent:#1f5f8b; --warn:#8a5a00; }
* { box-sizing: border-box; }
body { margin:0; font:14px/1.45 system-ui, "Segoe UI", Arial, sans-serif; color:var(--ink); background:var(--bg); height:100vh; display:flex; flex-direction:column; }
header { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 14px; background:var(--panel); border-bottom:1px solid var(--line); }
header strong { font-size:15px; }
#meta { margin-left:10px; color:var(--muted); font-size:12px; }
.actions { display:flex; gap:6px; }
button { font:inherit; border:1px solid var(--line); background:#fff; color:var(--ink); border-radius:6px; padding:5px 10px; cursor:pointer; }
button:hover { border-color:var(--accent); color:var(--accent); }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button.primary:hover { color:#fff; filter:brightness(1.08); }
#banner { display:none; gap:10px; align-items:center; padding:8px 14px; background:#fff4d6; border-bottom:1px solid #e6cf94; color:#5c4300; }
#banner.show { display:flex; }
main { flex:1; min-height:0; display:flex; }
#canvas-wrap { flex:1; overflow:hidden; position:relative; background:#e9edf1; cursor:grab; touch-action:none; }
#canvas-wrap.dragging { cursor:grabbing; }
#stage { position:absolute; top:0; left:0; transform-origin:0 0; background:#fff; box-shadow:0 1px 6px rgb(0 0 0 / 12%); }
#stage svg { display:block; max-width:none; }
#stage svg text { user-select:none; }
#stage .sf-pin { fill:#d64545; stroke:#fff; stroke-width:1.2; cursor:pointer; }
#stage .sf-pin.stale { fill:#b07a00; }
#stage .sf-pin.selected { fill:#1f5f8b; }
aside { width:340px; min-width:300px; background:var(--panel); border-left:1px solid var(--line); display:flex; flex-direction:column; min-height:0; }
aside h2 { font-size:14px; margin:12px 14px 4px; }
#hint { margin:0 14px 8px; color:var(--muted); font-size:12px; }
#pins { flex:1; overflow:auto; padding:0 14px 8px; }
.pin { border:1px solid var(--line); border-radius:8px; padding:8px; margin-bottom:8px; background:#fbfcfd; }
.pin.stale { border-color:#e6cf94; background:#fffaf0; }
.pin .where { display:flex; justify-content:space-between; gap:8px; color:var(--muted); font-size:12px; margin-bottom:5px; }
.pin textarea { width:100%; min-height:52px; resize:vertical; font:inherit; border:1px solid var(--line); border-radius:5px; padding:5px; }
.pin .remove { border:0; background:none; color:#a33; cursor:pointer; padding:0; font-size:12px; }
.buttons { display:flex; gap:8px; padding:10px 14px; border-top:1px solid var(--line); }
.buttons button { flex:1; }
#status { margin:0; padding:8px 14px 12px; color:var(--muted); font-size:12px; min-height:32px; }
#status.error { color:#a1231b; }
.empty { color:var(--muted); font-size:12px; }
</style>
</head>
<body>
<header>
  <div><strong id="figure">figure</strong><span id="meta"></span></div>
  <div class="actions">
    <button id="zoom-out" aria-label="Zoom out">−</button>
    <button id="zoom-in" aria-label="Zoom in">+</button>
    <button id="reset">Reset view</button>
    <button id="refresh">Reload figure</button>
  </div>
</header>
<div id="banner" role="status"><span id="banner-text"></span><button id="banner-load" class="primary">Load new version</button></div>
<main>
  <section id="canvas-wrap" aria-label="Figure canvas"><div id="stage"></div></section>
  <aside>
    <h2>Pinned comments</h2>
    <p id="hint">Click a figure object to pin a comment. Click empty space to comment without an object binding.</p>
    <div id="pins"><p class="empty" id="no-pins">No pins yet.</p></div>
    <div class="buttons">
      <button id="approve" class="primary">Approve</button>
      <button id="request">Request changes</button>
    </div>
    <p id="status" role="status"></p>
  </aside>
</main>
<script>
(function () {
  "use strict";
  const token = new URLSearchParams(location.search).get("t") || "";
  const stage = document.getElementById("stage");
  const wrap = document.getElementById("canvas-wrap");
  const pinsBox = document.getElementById("pins");
  const statusBox = document.getElementById("status");
  const banner = document.getElementById("banner");
  const bannerText = document.getElementById("banner-text");
  let state = null, loadedSha = null, svg = null;
  let view = { scale: 1, x: 0, y: 0 };
  let pending = [];
  let nextId = 1, selected = null;
  let dragging = null;
  let suppressClick = false;

  function api(path) { return path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(token); }
  function setStatus(text, error) { statusBox.textContent = text; statusBox.className = error ? "error" : ""; }

  async function fetchJson(path, options) {
    const response = await fetch(api(path), options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
    return data;
  }

  function applyView() {
    stage.style.transform = "translate(" + view.x + "px," + view.y + "px) scale(" + view.scale + ")";
  }

  function svgSize() {
    const box = svg && svg.viewBox && svg.viewBox.baseVal;
    return {w: Math.max(box && box.width ? box.width : 300, 1),
            h: Math.max(box && box.height ? box.height : 150, 1)};
  }

  function placeInitialView() {
    if (!svg || !state || view.x || view.y || view.scale !== 1) return;
    const box = wrap.getBoundingClientRect();
    const size = svgSize();
    const pad = 24;
    const scale = Math.min((box.width - pad * 2) / size.w,
                           (box.height - pad * 2) / size.h, 1);
    if (scale > 0) {
      view.scale = scale;
      view.x = pad; view.y = pad;
      applyView();
    }
  }

  function pinLayer() {
    if (!svg) return null;
    let layer = svg.querySelector("#sf-review-pins");
    if (!layer) {
      layer = document.createElementNS("http://www.w3.org/2000/svg", "g");
      layer.setAttribute("id", "sf-review-pins");
      svg.appendChild(layer);
    }
    return layer;
  }

  function renderPins() {
    const layer = pinLayer();
    if (layer) layer.replaceChildren();
    pinsBox.querySelectorAll(".pin").forEach(node => node.remove());
    const empty = document.getElementById("no-pins");
    empty.style.display = pending.length ? "none" : "";
    pending.forEach(pin => {
      if (layer) {
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        const radius = 7 / Math.max(view.scale, 0.05);
        circle.setAttribute("cx", pin.x); circle.setAttribute("cy", pin.y);
        circle.setAttribute("r", radius);
        circle.setAttribute("class", "sf-pin" + (pin.sha !== loadedSha ? " stale" : "")
                            + (selected === pin.uid ? " selected" : ""));
        circle.dataset.uid = pin.uid;
        circle.addEventListener("click", event => { event.stopPropagation(); select(pin.uid); });
        layer.appendChild(circle);
      }
      const card = document.createElement("div");
      card.className = "pin" + (pin.sha !== loadedSha ? " stale" : "");
      card.dataset.uid = pin.uid;
      const where = document.createElement("div");
      where.className = "where";
      const label = document.createElement("span");
      label.textContent = (pin.objectId || "no object") + " · " + Math.round(pin.x) + "," + Math.round(pin.y)
                          + (pin.sha !== loadedSha ? " · earlier version" : "");
      const remove = document.createElement("button");
      remove.className = "remove"; remove.textContent = "remove";
      remove.addEventListener("click", () => { pending = pending.filter(item => item.uid !== pin.uid); selected = null; renderPins(); });
      where.append(label, remove);
      const area = document.createElement("textarea");
      area.placeholder = "What should change here?";
      area.value = pin.text;
      area.addEventListener("input", () => { pin.text = area.value; });
      area.addEventListener("focus", () => select(pin.uid));
      card.append(where, area);
      pinsBox.appendChild(card);
    });
  }

  function select(uid) { selected = uid; renderPins(); const card = pinsBox.querySelector('.pin[data-uid="' + uid + '"] textarea'); if (card) card.focus(); }

  function addPin(objectId, x, y) {
    pending.push({ uid: nextId++, objectId: objectId, x: x, y: y, text: "", sha: loadedSha });
    selected = pending[pending.length - 1].uid;
    renderPins();
    const card = pinsBox.querySelector('.pin[data-uid="' + selected + '"] textarea');
    if (card) card.focus();
  }

  async function loadSvg() {
    const response = await fetch(api("/svg"), { cache: "no-store" });
    if (!response.ok) throw new Error("could not load the figure");
    const text = await response.text();
    const doc = new DOMParser().parseFromString(text, "image/svg+xml");
    if (!doc || doc.querySelector("parsererror")) throw new Error("the figure is not valid SVG");
    svg = doc.documentElement;
    svg.removeAttribute("width"); svg.removeAttribute("height");
    const size = svgSize();
    svg.style.width = size.w + "px"; svg.style.height = size.h + "px";
    svg.addEventListener("click", onCanvasClick);
    stage.replaceChildren(svg);
    loadedSha = state.svg_sha256;
    document.getElementById("meta").textContent = "rev " + (state.revision === null ? "?" : state.revision)
      + " · " + state.svg_sha256.slice(0, 10) + (state.kind === "draft_preview" ? " · preview" : "");
    placeInitialView();
    renderPins();
  }

  function onCanvasClick(event) {
    if (!svg) return;
    if (suppressClick) { suppressClick = false; return; }
    if (event.target.closest("#sf-review-pins")) return;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(svg.getScreenCTM().inverse());
    const owner = event.target.closest("[id]");
    addPin(owner ? owner.id : null, Math.round(point.x * 100) / 100, Math.round(point.y * 100) / 100);
  }

  async function refreshState(initial) {
    try {
      const next = await fetchJson("/state");
      const previous = state;
      state = next;
      document.getElementById("figure").textContent = next.figure_id || "figure";
      if (!initial && previous && next.svg_sha256 !== previous.svg_sha256 && next.svg_sha256 !== loadedSha) {
        bannerText.textContent = "New version available (revision " + (next.revision === null ? "?" : next.revision)
          + "). Pins placed on the previous version are marked.";
        banner.classList.add("show");
      }
      const last = next.feedback;
      if (last) {
        setStatus("Last submission: " + last.status.replace("_", " ") + " · " + last.comments.length
                  + " comment(s) · rev " + (last.scene_revision === null ? "?" : last.scene_revision));
      }
      if (initial) await loadSvg();
    } catch (error) {
      setStatus(error.message, true);
    }
  }

  wrap.addEventListener("wheel", event => {
    if (!svg) return;
    event.preventDefault();
    const box = wrap.getBoundingClientRect();
    const px = event.clientX - box.left, py = event.clientY - box.top;
    const factor = Math.exp(-event.deltaY * 0.0015);
    const scale = Math.min(40, Math.max(0.05, view.scale * factor));
    view.x = px - (px - view.x) * (scale / view.scale);
    view.y = py - (py - view.y) * (scale / view.scale);
    view.scale = scale;
    applyView();
    renderPins();
  }, { passive: false });

  wrap.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    suppressClick = false;
    dragging = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y };
    wrap.classList.add("dragging");
    wrap.setPointerCapture(event.pointerId);
  });
  wrap.addEventListener("pointermove", event => {
    if (!dragging) return;
    if (Math.abs(event.clientX - dragging.x) > 4 || Math.abs(event.clientY - dragging.y) > 4) {
      suppressClick = true;
    }
    view.x = dragging.vx + event.clientX - dragging.x;
    view.y = dragging.vy + event.clientY - dragging.y;
    applyView();
  });
  wrap.addEventListener("pointerup", () => { dragging = null; wrap.classList.remove("dragging"); });

  function zoom(factor) {
    const box = wrap.getBoundingClientRect();
    const px = box.width / 2, py = box.height / 2;
    const scale = Math.min(40, Math.max(0.05, view.scale * factor));
    view.x = px - (px - view.x) * (scale / view.scale);
    view.y = py - (py - view.y) * (scale / view.scale);
    view.scale = scale;
    applyView(); renderPins();
  }

  document.getElementById("zoom-in").addEventListener("click", () => zoom(1.25));
  document.getElementById("zoom-out").addEventListener("click", () => zoom(0.8));
  document.getElementById("reset").addEventListener("click", () => { view = { scale: 1, x: 0, y: 0 }; applyView(); placeInitialView(); renderPins(); });
  document.getElementById("refresh").addEventListener("click", () => { banner.classList.remove("show"); refreshState(false).then(loadSvg); });
  document.getElementById("banner-load").addEventListener("click", () => { banner.classList.remove("show"); refreshState(false).then(loadSvg); });

  async function submit(kind) {
    const comments = pending
      .filter(pin => pin.text.trim())
      .map(pin => ({ object_id: pin.objectId, position: [pin.x, pin.y], comment: pin.text.trim(), svg_sha256: pin.sha }));
    if (kind === "changes_requested" && comments.length === 0) {
      setStatus("Add at least one pin with text before requesting changes.", true); return;
    }
    try {
      await fetchJson("/feedback", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ schema_version: "0.1", figure_id: state.figure_id,
          svg_sha256: state.svg_sha256, scene_revision: state.revision,
          status: kind, comments: comments })
      });
      pending = []; selected = null; renderPins();
      setStatus(kind === "approved" ? "Approved this version." : "Change request sent.");
      refreshState(false);
    } catch (error) {
      setStatus(error.message + (error.message.indexOf("stale") >= 0
        ? " — load the newer version before submitting." : ""), true);
    }
  }
  document.getElementById("approve").addEventListener("click", () => submit("approved"));
  document.getElementById("request").addEventListener("click", () => submit("changes_requested"));

  refreshState(true);
  setInterval(() => refreshState(false), 2500);
})();
</script>
</body>
</html>
"""
