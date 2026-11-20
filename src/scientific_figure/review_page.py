"""Self-contained review page: no external scripts, fonts or network access."""

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scientific Figure · 预览</title>
<style>
:root { color-scheme: light; --ink:#1b2430; --muted:#6a7686; --line:#e3e7ec; --bg:#f2f4f7; --panel:#ffffff; --accent:#2f6f9f; --accent-soft:#eaf2f9; --warn:#8a5a00; }
* { box-sizing: border-box; }
body { margin:0; padding:28px 18px 40px; font:14px/1.55 system-ui, "Segoe UI", "Microsoft YaHei", Arial, sans-serif; color:var(--ink); background:var(--bg); }
.panel { max-width:880px; margin:0 auto; background:var(--panel); border:1px solid var(--line); border-radius:14px; box-shadow:0 8px 30px rgb(20 35 50 / 6%); overflow:hidden; }
header { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:14px 18px; }
.title { display:flex; align-items:center; gap:8px; font-size:16px; letter-spacing:.01em; }
.title .icon { color:var(--muted); }
.title strong { font-weight:600; }
.title span { color:var(--muted); font-weight:400; }
.chip { display:inline-flex; align-items:center; gap:6px; padding:3px 9px; border:1px solid var(--line); border-radius:999px; color:var(--muted); font-size:12px; background:#fbfcfd; }
.chip::before { content:""; width:6px; height:6px; border-radius:50%; background:#3a9a63; }
#banner { display:none; gap:10px; align-items:center; padding:9px 18px; background:#fff6dc; border-top:1px solid #ecdcae; border-bottom:1px solid #ecdcae; color:#5c4300; font-size:13px; }
#banner.show { display:flex; }
#banner button { margin-left:auto; }
.filebar { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 18px; border-top:1px solid var(--line); border-bottom:1px solid var(--line); background:#fcfdfe; }
.file { display:flex; align-items:center; gap:8px; min-width:0; color:var(--muted); font-size:13px; }
.file code { color:var(--ink); font:13px/1.4 ui-monospace, Consolas, monospace; }
.file .dot { width:7px; height:7px; border-radius:50%; background:#3a9a63; }
#meta { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tools { display:flex; gap:4px; }
button { font:inherit; border:1px solid var(--line); background:#fff; color:var(--ink); border-radius:7px; padding:5px 10px; cursor:pointer; transition:color .12s, border-color .12s, background .12s; }
button:hover { border-color:var(--accent); color:var(--accent); }
button.icon { padding:5px; width:30px; height:30px; display:inline-flex; align-items:center; justify-content:center; color:var(--muted); }
button.icon:hover { background:var(--accent-soft); }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button.primary:hover { filter:brightness(1.06); color:#fff; }
.icon { width:16px; height:16px; display:block; }
#canvas-wrap { position:relative; height:clamp(320px, 56vh, 520px); margin:16px 18px 0; border:1px solid var(--line); border-radius:10px; overflow:hidden; background:#fbfcfe; cursor:grab; touch-action:none; }
#canvas-wrap.dragging { cursor:grabbing; }
#stage { position:absolute; top:0; left:0; transform-origin:0 0; background:transparent; }
#stage svg { display:block; max-width:none; }
#stage svg text { user-select:none; }
#stage .sf-pin circle { fill:#d64545; stroke:#fff; stroke-width:1.5; cursor:pointer; }
#stage .sf-pin.stale circle { fill:#b07a00; }
#stage .sf-pin.selected circle { fill:#1f5f8b; }
#stage .sf-pin text { fill:#fff; font-family:system-ui, "Segoe UI", Arial, sans-serif; font-weight:600; text-anchor:middle; pointer-events:none; }
.toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:9px 18px 2px; color:var(--muted); font-size:12px; }
.toolbar .left, .toolbar .right { display:flex; align-items:center; gap:8px; min-width:0; }
.toolbar .icon { color:var(--muted); }
.sep { width:1px; height:14px; background:var(--line); margin:0 2px; }
#pins { padding:10px 18px 0; }
#pins .empty { margin:2px 0 6px; color:var(--muted); font-size:13px; }
.pin { border:1px solid var(--line); border-radius:9px; padding:10px 12px; margin-bottom:9px; background:#fff; }
.pin.selected { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent-soft); }
.pin.stale { border-color:#e6cf94; background:#fffaf0; }
.pin-head { display:flex; align-items:center; gap:8px; color:var(--muted); font-size:12px; margin-bottom:4px; }
.pin-head .num { flex:none; width:18px; height:18px; border-radius:50%; background:#d64545; color:#fff; font:600 11px/18px system-ui; text-align:center; }
.pin.stale .pin-head .num { background:#b07a00; }
.pin-head .target { color:var(--ink); font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pin-head .remove { margin-left:auto; border:0; background:none; color:#a33; padding:0; font-size:12px; }
.pin-head .remove:hover { color:#d33; }
.pin textarea { width:100%; min-height:46px; resize:vertical; border:0; background:transparent; padding:0; color:var(--ink); font:inherit; line-height:1.5; outline:none; }
.pin textarea::placeholder { color:#9aa5b1; }
.actions { display:flex; align-items:center; gap:8px; padding:6px 18px 12px; }
#status { flex:1; margin:0; color:var(--muted); font-size:12px; min-height:18px; }
#status.error { color:#a1231b; }
footer { display:flex; justify-content:space-between; gap:12px; padding:10px 18px; border-top:1px solid var(--line); color:var(--muted); font-size:12px; background:#fcfdfe; }
footer span { display:inline-flex; align-items:center; gap:6px; }
@media (max-width:560px) {
  body { padding:12px 8px 24px; }
  header, .filebar, .toolbar, #pins, .actions, footer { padding-left:12px; padding-right:12px; }
  #canvas-wrap { margin:12px 12px 0; height:52vh; }
  #meta { display:none; }
}
</style>
</head>
<body>
<div class="panel">
  <header>
    <div class="title">
      <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
      <strong>Scientific Figure</strong><span>· 预览</span>
    </div>
    <span class="chip" id="mode">实时预览</span>
  </header>
  <div id="banner" role="status"><span id="banner-text"></span><button id="banner-load" class="primary">加载新版本</button></div>
  <div class="filebar">
    <div class="file"><span class="dot"></span><code>figure.svg</code><span id="meta">当前版本</span></div>
    <div class="tools">
      <button class="icon" id="zoom-in" title="放大" aria-label="放大"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M11 8v6M8 11h6M20 20l-3.5-3.5"/></svg></button>
      <button class="icon" id="reset" title="适应窗口" aria-label="适应窗口"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5"/></svg></button>
      <button class="icon" id="refresh" title="重新加载" aria-label="重新加载"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg></button>
    </div>
  </div>
  <section id="canvas-wrap" aria-label="图稿画布"><div id="stage"></div></section>
  <div class="toolbar">
    <div class="left">
      <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 21s-6-5.6-6-10a6 6 0 0 1 12 0c0 4.4-6 10-6 10Z"/><circle cx="12" cy="11" r="2"/></svg>
      <span id="count">0 条批注</span>
      <span class="sep"></span>
      <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/></svg>
      <span>拖拽平移 · 滚轮缩放</span>
    </div>
    <div class="right"><span id="zoom">100%</span><span>SVG 原生</span></div>
  </div>
  <div id="pins"><p class="empty" id="no-pins">点击图稿上的对象或空白处添加批注。</p></div>
  <div class="actions">
    <p id="status" role="status"></p>
    <button id="request">请求修改</button>
    <button id="approve" class="primary">通过此版本</button>
  </div>
  <footer>
    <span><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>后续有效版本自动更新</span>
    <span>可反馈给宿主</span>
  </footer>
</div>
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
  let hasFit = false;
  let userAdjusted = false;

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

  function updateZoomLabel() {
    document.getElementById("zoom").textContent = Math.round(view.scale * 100) + "%";
  }

  function svgSize() {
    const box = svg && svg.viewBox && svg.viewBox.baseVal;
    return {w: Math.max(box && box.width ? box.width : 300, 1),
            h: Math.max(box && box.height ? box.height : 150, 1)};
  }

  function fitView() {
    if (!svg) return;
    const box = wrap.getBoundingClientRect();
    const size = svgSize();
    const pad = 26;
    if (!box.width || !box.height) return;
    const scale = Math.min((box.width - pad * 2) / size.w,
                           (box.height - pad * 2) / size.h, 1);
    view.scale = Math.max(scale, 0.05);
    view.x = (box.width - size.w * view.scale) / 2;
    view.y = (box.height - size.h * view.scale) / 2;
    userAdjusted = false;
    applyView();
    updateZoomLabel();
    renderPinMarks();
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

  function renderPinMarks() {
    const layer = pinLayer();
    if (layer) layer.replaceChildren();
    pending.forEach((pin, index) => {
      if (!layer) return;
      const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
      const radius = 11 / Math.max(view.scale, 0.05);
      group.setAttribute("class", "sf-pin" + (pin.sha !== loadedSha ? " stale" : "")
                         + (selected === pin.uid ? " selected" : ""));
      group.setAttribute("transform", "translate(" + pin.x + "," + pin.y + ")");
      group.dataset.uid = pin.uid;
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("r", radius);
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("y", radius * 0.34);
      label.setAttribute("font-size", radius * 1.02);
      label.textContent = String(index + 1);
      group.append(circle, label);
      layer.appendChild(group);
    });
  }

  function renderPinCards() {
    pinsBox.querySelectorAll(".pin").forEach(node => node.remove());
    const empty = document.getElementById("no-pins");
    empty.style.display = pending.length ? "none" : "";
    document.getElementById("count").textContent = pending.length + " 条批注";
    pending.forEach((pin, index) => {
      const card = document.createElement("div");
      card.className = "pin" + (pin.sha !== loadedSha ? " stale" : "")
                       + (selected === pin.uid ? " selected" : "");
      card.dataset.uid = pin.uid;
      const head = document.createElement("div");
      head.className = "pin-head";
      const num = document.createElement("span");
      num.className = "num"; num.textContent = String(index + 1);
      const title = document.createElement("span");
      title.className = "target";
      title.textContent = "批注 " + (index + 1) + " · " + (pin.objectId || "整体");
      const remove = document.createElement("button");
      remove.className = "remove"; remove.textContent = "删除";
      remove.addEventListener("click", () => {
        pending = pending.filter(item => item.uid !== pin.uid);
        selected = null; renderPins(); setStatus("");
      });
      head.append(num, title, remove);
      const area = document.createElement("textarea");
      area.placeholder = "写下修改意见…";
      area.value = pin.text;
      area.addEventListener("input", () => { pin.text = area.value; });
      area.addEventListener("focus", () => { selected = pin.uid; updateSelection(); });
      card.append(head, area);
      pinsBox.appendChild(card);
    });
  }

  function renderPins() { renderPinMarks(); renderPinCards(); }

  function updateSelection() {
    pinsBox.querySelectorAll(".pin").forEach(node => {
      node.classList.toggle("selected", Number(node.dataset.uid) === selected);
    });
    const layer = svg && svg.querySelector("#sf-review-pins");
    if (layer) layer.querySelectorAll(".sf-pin").forEach(node => {
      node.classList.toggle("selected", Number(node.dataset.uid) === selected);
    });
  }

  function select(uid) {
    selected = uid;
    updateSelection();
    const card = pinsBox.querySelector('.pin[data-uid="' + uid + '"] textarea');
    if (card) card.focus();
  }

  function addPin(objectId, x, y) {
    pending.push({ uid: nextId++, objectId: objectId, x: x, y: y, text: "", sha: loadedSha });
    selected = pending[pending.length - 1].uid;
    renderPins();
    const card = pinsBox.querySelector('.pin[data-uid="' + selected + '"]');
    if (card) {
      card.scrollIntoView({block: "nearest"});
      const area = card.querySelector("textarea");
      if (area) area.focus();
    }
  }

  async function loadSvg() {
    const response = await fetch(api("/svg"), { cache: "no-store" });
    if (!response.ok) throw new Error("无法加载图稿");
    const text = await response.text();
    const doc = new DOMParser().parseFromString(text, "image/svg+xml");
    if (!doc || doc.querySelector("parsererror")) throw new Error("图稿不是有效的 SVG");
    svg = doc.documentElement;
    svg.removeAttribute("width"); svg.removeAttribute("height");
    const size = svgSize();
    svg.style.width = size.w + "px"; svg.style.height = size.h + "px";
    stage.replaceChildren(svg);
    loadedSha = state.svg_sha256;
    document.getElementById("meta").textContent = "当前版本 · rev "
      + (state.revision === null ? "?" : state.revision) + " · " + state.svg_sha256.slice(0, 10);
    if (!hasFit) { hasFit = true; fitView(); }
    else { applyView(); renderPinMarks(); updateZoomLabel(); }
    renderPins();
  }

  function onCanvasClick(event) {
    if (!svg) return;
    if (suppressClick) { suppressClick = false; return; }
    // The click may be retargeted to the capture element, so hit-test by point.
    const hit = document.elementFromPoint(event.clientX, event.clientY);
    if (!hit || (hit !== svg && !svg.contains(hit))) return;
    const pinNode = hit.closest(".sf-pin");
    if (pinNode) { select(Number(pinNode.dataset.uid)); return; }
    if (hit.closest("#sf-review-pins")) return;
    const rect = svg.getBoundingClientRect();
    const box = svg.viewBox.baseVal;
    if (!rect.width || !rect.height || !box.width || !box.height) return;
    const x = box.x + (event.clientX - rect.left) * box.width / rect.width;
    const y = box.y + (event.clientY - rect.top) * box.height / rect.height;
    const owner = hit.closest("[id]");
    addPin(owner && owner !== svg ? owner.id : null,
           Math.round(x * 100) / 100, Math.round(y * 100) / 100);
  }

  async function refreshState(initial) {
    try {
      const next = await fetchJson("/state");
      const previous = state;
      state = next;
      document.getElementById("mode").textContent = next.kind === "draft_preview" ? "草稿预览" : "实时预览";
      if (!initial && previous && next.svg_sha256 !== previous.svg_sha256 && next.svg_sha256 !== loadedSha) {
        bannerText.textContent = "有新版本（rev " + (next.revision === null ? "?" : next.revision)
          + "）可用。旧版本上的批注会被标记。";
        banner.classList.add("show");
      }
      const last = next.feedback;
      if (last && !statusBox.textContent) {
        const label = last.status === "approved" ? "已通过" : "请求修改";
        setStatus("上次提交：" + label + " · " + last.comments.length
                  + " 条 · rev " + (last.scene_revision === null ? "?" : last.scene_revision));
      }
      if (initial) await loadSvg();
    } catch (error) {
      setStatus(error.message, true);
    }
  }

  wrap.addEventListener("wheel", event => {
    if (!svg) return;
    event.preventDefault();
    userAdjusted = true;
    const box = wrap.getBoundingClientRect();
    const px = event.clientX - box.left, py = event.clientY - box.top;
    const factor = Math.exp(-event.deltaY * 0.0015);
    const scale = Math.min(40, Math.max(0.05, view.scale * factor));
    view.x = px - (px - view.x) * (scale / view.scale);
    view.y = py - (py - view.y) * (scale / view.scale);
    view.scale = scale;
    applyView();
    updateZoomLabel();
    renderPinMarks();
  }, { passive: false });

  wrap.addEventListener("click", onCanvasClick);
  wrap.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    suppressClick = false;
    dragging = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y, captured: false };
    wrap.classList.add("dragging");
  });
  wrap.addEventListener("pointermove", event => {
    if (!dragging) return;
    if (Math.abs(event.clientX - dragging.x) > 4 || Math.abs(event.clientY - dragging.y) > 4) {
      suppressClick = true;
      userAdjusted = true;
      if (!dragging.captured) {
        wrap.setPointerCapture(event.pointerId);
        dragging.captured = true;
      }
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
    userAdjusted = true;
    applyView(); updateZoomLabel(); renderPinMarks();
  }

  document.getElementById("zoom-in").addEventListener("click", () => zoom(1.25));
  document.getElementById("reset").addEventListener("click", () => fitView());
  document.getElementById("refresh").addEventListener("click", () => {
    banner.classList.remove("show");
    refreshState(false).then(loadSvg);
  });
  document.getElementById("banner-load").addEventListener("click", () => {
    banner.classList.remove("show");
    refreshState(false).then(loadSvg);
  });

  async function submit(kind) {
    const comments = pending
      .filter(pin => pin.text.trim())
      .map(pin => ({ object_id: pin.objectId, position: [pin.x, pin.y], comment: pin.text.trim(), svg_sha256: pin.sha }));
    if (kind === "changes_requested" && comments.length === 0) {
      setStatus("请先添加至少一条带文字的批注。", true); return;
    }
    try {
      await fetchJson("/feedback", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ schema_version: "0.1", figure_id: state.figure_id,
          svg_sha256: state.svg_sha256, scene_revision: state.revision,
          status: kind, comments: comments })
      });
      pending = []; selected = null; renderPins();
      setStatus(kind === "approved" ? "已通过此版本。" : "修改意见已发送。");
      refreshState(false);
    } catch (error) {
      setStatus(error.message + (error.message.indexOf("stale") >= 0
        ? " — 请先加载新版本再提交。" : ""), true);
    }
  }
  document.getElementById("approve").addEventListener("click", () => submit("approved"));
  document.getElementById("request").addEventListener("click", () => submit("changes_requested"));

  if (window.ResizeObserver) {
    new ResizeObserver(() => { if (!userAdjusted) fitView(); }).observe(wrap);
  }

  refreshState(true);
  setInterval(() => refreshState(false), 2500);
})();
</script>
</body>
</html>
"""
