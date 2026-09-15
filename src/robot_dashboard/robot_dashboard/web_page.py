"""Embedded single-page HTML UI served by dashboard_node at '/'."""

PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Robot RAG Agent — Dashboard</title>
<style>
  :root {
    --bg: #0f1117; --panel: #181c25; --border: #2a3040;
    --text: #d7dce5; --muted: #8b93a3; --accent: #4da3ff;
    --ok: #46c46e; --warn: #e0b53f; --err: #e06a5a; --goal: #b07fe8;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.45 system-ui, "Segoe UI", sans-serif;
    display: flex; flex-direction: column; height: 100vh;
  }
  header {
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    padding: 10px 16px; background: var(--panel); border-bottom: 1px solid var(--border);
  }
  header h1 { font-size: 16px; margin: 0; font-weight: 600; }
  .chip {
    font-size: 12px; padding: 3px 10px; border-radius: 12px;
    background: #232939; color: var(--muted); white-space: nowrap;
  }
  .chip b { color: var(--text); font-weight: 600; }
  #conn.ok { color: var(--ok); } #conn.bad { color: var(--err); }
  #goalbar {
    display: flex; gap: 8px; padding: 10px 16px; background: var(--panel);
    border-bottom: 1px solid var(--border); align-items: center; flex-wrap: wrap;
  }
  #goalInput {
    flex: 1; min-width: 220px; padding: 9px 12px; border-radius: 8px;
    border: 1px solid var(--border); background: #10141d; color: var(--text); font-size: 14px;
  }
  #goalInput:focus { outline: 1px solid var(--accent); }
  button {
    padding: 8px 13px; border-radius: 8px; border: 1px solid var(--border);
    background: #232939; color: var(--text); cursor: pointer; font-size: 13px;
  }
  button:hover { background: #2b3245; }
  button.primary { background: var(--accent); border-color: var(--accent); color: #0b0e14; font-weight: 600; }
  button.mic.listening { background: var(--err); border-color: var(--err); animation: pulse 1s infinite; }
  @keyframes pulse { 50% { opacity: 0.6; } }
  button.small { font-size: 11px; padding: 3px 8px; }
  main {
    display: grid; grid-template-columns: minmax(340px, 1.1fr) 1fr 1fr; gap: 10px;
    padding: 10px 16px; flex: 1; min-height: 0;
  }
  section {
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    display: flex; flex-direction: column; min-height: 0;
  }
  section h2 {
    font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); margin: 0; padding: 10px 14px 8px;
    display: flex; justify-content: space-between; align-items: center; gap: 8px;
  }
  .list { overflow-y: auto; padding: 0 10px 10px; flex: 1; }
  .ev { display: flex; gap: 8px; padding: 5px 6px; border-radius: 6px; align-items: baseline; }
  .ev:hover { background: #1f2534; }
  .ev .ts { color: var(--muted); font-size: 11px; font-family: ui-monospace, monospace; white-space: nowrap; }
  .ev .tag {
    font-size: 10px; font-weight: 700; padding: 1px 7px; border-radius: 9px;
    text-transform: uppercase; white-space: nowrap;
  }
  .tag.goal { background: #3a2a55; color: var(--goal); }
  .tag.status { background: #1e3a52; color: var(--accent); }
  .tag.response { background: #1d4029; color: var(--ok); }
  .ev .msg { word-break: break-word; }
  .ev.response .msg { color: var(--ok); font-weight: 600; }
  .log { font-family: ui-monospace, "Cascadia Mono", monospace; font-size: 12px; }
  .log .node { color: var(--accent); }
  .log.l30 .msg { color: var(--warn); } .log.l40 .msg, .log.l50 .msg { color: var(--err); }
  select, label.f { font-size: 12px; color: var(--muted); }
  select { background: #10141d; color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 3px 6px; }
  /* --- mapa --- */
  #mapWrap { position: relative; flex: 1; min-height: 220px; margin: 0 10px; }
  #mapCanvas {
    position: absolute; inset: 0; width: 100%; height: 100%;
    border-radius: 8px; background: #0b0e14; cursor: crosshair;
  }
  #zoneForm {
    display: none; position: absolute; z-index: 5; background: #232939;
    border: 1px solid var(--accent); border-radius: 10px; padding: 10px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.5); width: 230px;
  }
  #zoneForm input {
    width: 100%; margin-bottom: 8px; padding: 7px 9px; border-radius: 7px;
    border: 1px solid var(--border); background: #10141d; color: var(--text);
  }
  #zoneForm .row { display: flex; gap: 6px; }
  #zonesList { padding: 6px 10px 12px; display: flex; flex-wrap: wrap; gap: 6px; max-height: 110px; overflow-y: auto; }
  .zone-chip {
    display: flex; align-items: center; gap: 5px; background: #232939;
    border: 1px solid var(--border); border-radius: 9px; padding: 4px 8px; font-size: 12px;
  }
  .zone-chip b { color: var(--goal); }
  .hint { color: var(--muted); font-size: 11px; padding: 0 14px 8px; }
  @media (max-width: 1100px) { main { grid-template-columns: 1fr 1fr; } #mapSection { grid-column: 1 / -1; min-height: 320px; } }
  @media (max-width: 700px) { main { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header>
  <h1>🤖 Robot RAG Agent</h1>
  <span class="chip" id="conn">●&nbsp;conectando…</span>
  <span class="chip">Mapa: <b id="mapdims">—</b></span>
  <span class="chip">Robot: <b id="robotpos">—</b></span>
  <span class="chip">Zonas: <b id="zonecount">0</b></span>
</header>

<div id="goalbar">
  <input id="goalInput" placeholder="Escribe una orden para el robot… (o usa el micrófono)"
         onkeydown="if(event.key==='Enter')sendGoal()">
  <button class="primary" onclick="sendGoal()">Enviar</button>
  <button class="mic" id="micBtn" onclick="toggleMic()" title="Comando de voz">🎤</button>
  <button class="small" onclick="preset('Explora el entorno durante 60 segundos')">Explorar 60s</button>
  <button class="small" onclick="preset('Dime dónde estás y qué ves')">¿Dónde estás?</button>
</div>

<main>
  <section id="mapSection">
    <h2>Mapa SLAM</h2>
    <div id="mapWrap">
      <canvas id="mapCanvas"></canvas>
      <div id="zoneForm">
        <input id="zoneName" placeholder="nombre (ej: cocina)" onkeydown="if(event.key==='Enter')saveZone(false)">
        <div class="row">
          <button class="primary small" onclick="saveZone(false)">💾 Guardar</button>
          <button class="small" onclick="saveZone(true)">🧭 Explorar</button>
          <button class="small" onclick="hideZoneForm()">✕</button>
        </div>
      </div>
    </div>
    <div class="hint">Arrastra sobre el mapa para seleccionar un área → nómbrala para guardarla como zona o explorarla.</div>
    <div id="zonesList"></div>
  </section>

  <section>
    <h2>Planificación y respuestas
      <button class="small" onclick="document.getElementById('timeline').innerHTML=''" title="Limpiar">🧹</button>
    </h2>
    <div class="list" id="timeline"></div>
  </section>

  <section>
    <h2>Logs ROS (/rosout)
      <span style="display:flex;gap:6px;align-items:center">
        <label class="f"><input type="checkbox" id="onlyAgent" checked> agente</label>
        <select id="minLevel">
          <option value="20" selected>INFO+</option>
          <option value="30">WARN+</option>
          <option value="40">ERROR+</option>
        </select>
        <button class="small" onclick="document.getElementById('logs').innerHTML=''" title="Limpiar">🧹</button>
      </span>
    </h2>
    <div class="list log" id="logs"></div>
  </section>
</main>

<script>
const AGENT_NODES = /^(rag_node|skills_executor_node|llm_planner_node|basic_navigator|dashboard_node|bt_navigator|planner_server|controller_server|slam_toolbox)/;
let lastId = 0;
const timeline = document.getElementById('timeline');
const logs = document.getElementById('logs');

function fmtTs(t) { return new Date(t * 1000).toLocaleTimeString('es-ES'); }
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function atBottom(el) { return el.scrollHeight - el.scrollTop - el.clientHeight < 40; }
function stick(el, was) { if (was) el.scrollTop = el.scrollHeight; }

function addTimeline(ev) {
  const was = atBottom(timeline);
  const div = document.createElement('div');
  div.className = 'ev ' + ev.type;
  div.innerHTML = `<span class="ts">${fmtTs(ev.ts)}</span>` +
    `<span class="tag ${ev.type}">${ev.type}</span><span class="msg">${esc(ev.text)}</span>`;
  timeline.appendChild(div); stick(timeline, was);
}

function addLog(ev) {
  const was = atBottom(logs);
  const div = document.createElement('div');
  div.className = 'ev log l' + ev.level;
  div.dataset.node = ev.source; div.dataset.level = ev.level;
  div.innerHTML = `<span class="ts">${fmtTs(ev.ts)}</span>` +
    `<span class="node">${esc(ev.source)}</span><span class="msg">${esc(ev.text)}</span>`;
  applyLogFilter(div);
  logs.appendChild(div); stick(logs, was);
  while (logs.children.length > 1500) logs.removeChild(logs.firstChild);
}

function applyLogFilter(div) {
  const onlyAgent = document.getElementById('onlyAgent').checked;
  const minLevel = +document.getElementById('minLevel').value;
  const show = (+div.dataset.level >= minLevel) && (!onlyAgent || AGENT_NODES.test(div.dataset.node));
  div.style.display = show ? '' : 'none';
}
document.getElementById('onlyAgent').onchange =
document.getElementById('minLevel').onchange = () => {
  for (const d of logs.children) applyLogFilter(d);
};

async function pollEvents() {
  try {
    const r = await fetch(`/api/events?since=${lastId}`);
    const data = await r.json();
    for (const ev of data.events) {
      lastId = ev.id;
      if (ev.type === 'rosout') addLog(ev); else addTimeline(ev);
    }
    setConn(true);
  } catch (e) { setConn(false); }
  setTimeout(pollEvents, 700);
}
function setConn(ok) {
  const c = document.getElementById('conn');
  c.className = 'chip ' + (ok ? 'ok' : 'bad');
  c.innerHTML = ok ? '●&nbsp;conectado' : '●&nbsp;sin conexión';
}

function preset(t) { document.getElementById('goalInput').value = t; sendGoal(); }
async function sendGoal(text) {
  const input = document.getElementById('goalInput');
  const t = (text || input.value).trim();
  if (!t) return;
  await fetch('/api/goal', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: t }),
  });
  if (!text) input.value = '';
}

// --- Voz (Web Speech API: Chrome/Edge) ---
let rec = null, listening = false;
function toggleMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { alert('Reconocimiento de voz no soportado en este navegador (usa Chrome/Edge).'); return; }
  if (listening) { rec.stop(); return; }
  rec = new SR();
  rec.lang = 'es-ES'; rec.interimResults = true;
  const input = document.getElementById('goalInput');
  rec.onresult = (e) => {
    input.value = Array.from(e.results).map(r => r[0].transcript).join('');
    if (e.results[e.results.length - 1].isFinal) sendGoal();
  };
  rec.onstart = () => { listening = true; document.getElementById('micBtn').classList.add('listening'); };
  rec.onend = () => { listening = false; document.getElementById('micBtn').classList.remove('listening'); };
  rec.onerror = rec.onend;
  rec.start();
}

// ===================== Mapa interactivo =====================
const canvas = document.getElementById('mapCanvas');
const ctx = canvas.getContext('2d');
const mapImg = new Image();
let mapMeta = null, mapStamp = null, robot = null, zones = {};
let view = null;              // {ox, oy, scale} px de canvas por metro
let sel = null, dragging = false, pendingArea = null;

async function pollMap() {
  try {
    const r = await fetch('/api/map');
    const data = await r.json();
    robot = data.robot; zones = data.zones || {};
    if (data.map) {
      mapMeta = data.map;
      // Refetch the image only when SLAM has published a newer grid. The stamp
      // in the URL is what makes the browser skip the request otherwise;
      // /api/map/png's ETag covers reloads and back/forward.
      if (data.map.stamp !== mapStamp) {
        mapStamp = data.map.stamp;
        mapImg.src = '/api/map/png?v=' + mapStamp;
      }
      document.getElementById('mapdims').textContent =
        `${(mapMeta.width * mapMeta.resolution).toFixed(1)}×${(mapMeta.height * mapMeta.resolution).toFixed(1)} m`;
    }
    document.getElementById('robotpos').textContent =
      robot ? `(${robot.x.toFixed(2)}, ${robot.y.toFixed(2)})` : '—';
    document.getElementById('zonecount').textContent = Object.keys(zones).length;
    renderZoneChips();
  } catch (e) { /* setConn lo gestiona pollEvents */ }
  setTimeout(pollMap, 2000);
}

function resizeCanvas() {
  // Called every frame (see draw()), not just on window resize: the page
  // layout shifts constantly (zone chips appearing, timeline growing) without
  // ever firing a 'resize' event, which desynced canvas.width/height (the
  // pixel buffer used for all w2c/c2w math) from the actual on-screen box —
  // every drag-to-select landed on the wrong world coordinates.
  const rect = canvas.parentElement.getBoundingClientRect();
  if (canvas.width !== rect.width || canvas.height !== rect.height) {
    canvas.width = rect.width; canvas.height = rect.height;
  }
}

function computeView() {
  if (!mapMeta) return null;
  const mw = mapMeta.width * mapMeta.resolution, mh = mapMeta.height * mapMeta.resolution;
  const scale = Math.min(canvas.width / mw, canvas.height / mh) * 0.96;
  const ox = (canvas.width - mw * scale) / 2, oy = (canvas.height - mh * scale) / 2;
  return { ox, oy, scale };
}
function w2c(wx, wy) {  // mundo → canvas
  const mw = mapMeta.width * mapMeta.resolution, mh = mapMeta.height * mapMeta.resolution;
  return [
    view.ox + (wx - mapMeta.origin_x) * view.scale,
    view.oy + (mh - (wy - mapMeta.origin_y)) * view.scale,
  ];
}
function c2w(cx, cy) {  // canvas → mundo
  const mh = mapMeta.height * mapMeta.resolution;
  return [
    mapMeta.origin_x + (cx - view.ox) / view.scale,
    mapMeta.origin_y + mh - (cy - view.oy) / view.scale,
  ];
}

function draw() {
  requestAnimationFrame(draw);
  resizeCanvas();
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!mapMeta || !mapImg.complete || !mapImg.naturalWidth) return;
  view = computeView();
  const mw = mapMeta.width * mapMeta.resolution, mh = mapMeta.height * mapMeta.resolution;
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(mapImg, view.ox, view.oy, mw * view.scale, mh * view.scale);

  // zonas guardadas
  ctx.font = 'bold 12px system-ui';
  for (const [name, z] of Object.entries(zones)) {
    const [x1, y1] = w2c(z.x_min, z.y_max), [x2, y2] = w2c(z.x_max, z.y_min);
    ctx.fillStyle = 'rgba(176,127,232,0.16)'; ctx.strokeStyle = 'rgba(176,127,232,0.85)';
    ctx.lineWidth = 1.5;
    ctx.fillRect(x1, y1, x2 - x1, y2 - y1); ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    ctx.fillStyle = '#b07fe8'; ctx.fillText(name, x1 + 5, y1 + 15);
  }

  // selección en curso
  if (sel) {
    const [x1, y1] = w2c(sel.x_min, sel.y_max), [x2, y2] = w2c(sel.x_max, sel.y_min);
    ctx.fillStyle = 'rgba(77,163,255,0.18)'; ctx.strokeStyle = '#4da3ff';
    ctx.setLineDash([5, 4]); ctx.lineWidth = 1.5;
    ctx.fillRect(x1, y1, x2 - x1, y2 - y1); ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    ctx.setLineDash([]);
  }

  // robot
  if (robot) {
    const [rx, ry] = w2c(robot.x, robot.y);
    ctx.beginPath(); ctx.arc(rx, ry, 7, 0, Math.PI * 2);
    ctx.fillStyle = '#46c46e'; ctx.fill();
    ctx.strokeStyle = '#0b0e14'; ctx.lineWidth = 2; ctx.stroke();
  }
}

function evPos(e) {
  const r = canvas.getBoundingClientRect();
  return [e.clientX - r.left, e.clientY - r.top];
}
canvas.addEventListener('mousedown', (e) => {
  if (!view) return;
  hideZoneForm();
  dragging = true;
  const [wx, wy] = c2w(...evPos(e));
  sel = { x_min: wx, y_min: wy, x_max: wx, y_max: wy, ax: wx, ay: wy };
});
canvas.addEventListener('mousemove', (e) => {
  if (!dragging || !view) return;
  const [wx, wy] = c2w(...evPos(e));
  sel.x_min = Math.min(sel.ax, wx); sel.x_max = Math.max(sel.ax, wx);
  sel.y_min = Math.min(sel.ay, wy); sel.y_max = Math.max(sel.ay, wy);
});
canvas.addEventListener('mouseup', (e) => {
  if (!dragging) return;
  dragging = false;
  if (!sel || (sel.x_max - sel.x_min) < 0.15 || (sel.y_max - sel.y_min) < 0.15) { sel = null; return; }
  pendingArea = { x_min: sel.x_min, y_min: sel.y_min, x_max: sel.x_max, y_max: sel.y_max };
  const form = document.getElementById('zoneForm');
  const [px, py] = evPos(e);
  form.style.left = Math.min(px, canvas.width - 240) + 'px';
  form.style.top = Math.min(py, canvas.height - 100) + 'px';
  form.style.display = 'block';
  document.getElementById('zoneName').focus();
});
function hideZoneForm() {
  document.getElementById('zoneForm').style.display = 'none';
  document.getElementById('zoneName').value = '';
  sel = null; pendingArea = null;
}

async function saveZone(exploreAfter) {
  const name = document.getElementById('zoneName').value.trim();
  if (!name || !pendingArea) return;
  const r = await fetch('/api/zones', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, ...pendingArea }),
  });
  const data = await r.json();
  if (data.ok && exploreAfter) {
    sendGoal(`Explora la zona ${data.name} durante 60 segundos`);
  }
  hideZoneForm();
  pollNow();
}

function renderZoneChips() {
  const el = document.getElementById('zonesList');
  el.innerHTML = '';
  for (const name of Object.keys(zones).sort()) {
    const chip = document.createElement('div');
    chip.className = 'zone-chip';
    chip.innerHTML = `<b>${esc(name)}</b>` +
      `<button class="small" title="Ir">🧭</button>` +
      `<button class="small" title="Explorar">🔍</button>` +
      `<button class="small" title="Borrar">🗑</button>`;
    const [goBtn, exBtn, delBtn] = chip.querySelectorAll('button');
    goBtn.onclick = () => sendGoal(`Ve a la zona ${name}`);
    exBtn.onclick = () => sendGoal(`Explora la zona ${name} durante 60 segundos`);
    delBtn.onclick = async () => { await fetch(`/api/zones/${name}`, { method: 'DELETE' }); pollNow(); };
    el.appendChild(chip);
  }
}

async function pollNow() {
  try {
    const r = await fetch('/api/map'); const data = await r.json();
    zones = data.zones || {}; renderZoneChips();
  } catch (e) {}
}

resizeCanvas();
pollEvents();
pollMap();
draw();
</script>
</body>
</html>
"""
