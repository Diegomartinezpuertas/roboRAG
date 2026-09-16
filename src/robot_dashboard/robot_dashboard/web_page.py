"""Embedded single-page UI served by dashboard_node at '/'.

A control station for a robot mapping a house, drawn as what the SLAM map
literally is — a floor plan. The plan fills the left of the screen and carries
its instruments on it: the drive controls (with who holds /cmd_vel, whether
the keyboard reaches the page, and how fast the simulation runs), the rooms,
a legend, and a title block. The right column is the conversation with the
agent — the order, its reasoning, ROS logs one tab away — and the robot's
memory. Design rationale: docs/decisions/ADR-030-dashboard-redesign-and-browser-tests.md.

Kept as one string with no build step and no external assets: the dashboard
must come up from a bare `ros2 launch`, offline. Icons are inline SVG, not
emoji, so they render the same with or without an emoji font; text uses
Bahnschrift (ships with Windows) with DIN-like fallbacks.
"""

PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Robot RAG Agent</title>
<style>
  :root {
    --ink: #0f2233;       /* blueprint page */
    --plan: #16304a;      /* panels */
    --plan-2: #1b3957;    /* raised controls */
    --line: #2b4d68;      /* linework */
    --chalk: #e6edf2;     /* text, walls */
    --muted: #93aabb;
    --faint: #6b8699;
    --amber: #f2a93b;     /* the robot, primary actions */
    --amber-ink: #231602;
    --cyan: #5ec4dc;      /* memory */
    --ok: #86d9a0;
    --warn: #f2c14e;
    --err: #ff7a6b;
    --r-sm: 3px; --r-md: 7px;
    font-size: 15px;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; background: var(--ink); color: var(--chalk);
    font-family: "Bahnschrift", "Barlow", "DIN Alternate", "Roboto Condensed", "Segoe UI", sans-serif;
    font-variant-numeric: tabular-nums; line-height: 1.4;
  }
  button, input, select { font: inherit; color: inherit; }
  button { cursor: pointer; }
  :focus-visible { outline: 2px solid var(--amber); outline-offset: 2px; }
  .icon { width: 1em; height: 1em; stroke: currentColor; fill: none; stroke-width: 1.8;
          stroke-linecap: round; stroke-linejoin: round; flex: none; }

  .station { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(380px, 1fr); height: 100vh; }

  /* ---------------- the plan (hero) ---------------- */
  .plan { position: relative; min-height: 0; overflow: hidden; background: var(--ink);
          border-right: 1px solid var(--line); }
  #mapCanvas { position: absolute; inset: 0; width: 100%; height: 100%; cursor: crosshair; display: block; }
  .overlay { position: absolute; background: rgba(15, 34, 51, 0.88); border: 1px solid var(--line);
             border-radius: var(--r-md); backdrop-filter: blur(3px); }
  .plan-empty { position: absolute; inset: 0; display: grid; place-items: center; color: var(--muted);
                pointer-events: none; text-align: center; padding: 2rem; }
  .plan-empty strong { display: block; color: var(--chalk); font-weight: 500; font-size: 1.15rem; margin-bottom: .3rem; }

  /* the plan is framed by a top row (rooms, legend) and a bottom bar (drive,
     title block); the map is fitted into the space between them, never under */
  .plan-top { position: absolute; top: 14px; left: 14px; right: 14px; display: flex; gap: 10px;
              align-items: flex-start; pointer-events: none; }
  .plan-top > * { pointer-events: auto; }
  .plan-bottom { position: absolute; left: 14px; right: 14px; bottom: 14px; display: flex; gap: 10px;
                 align-items: flex-end; pointer-events: none; }
  .plan-bottom > * { pointer-events: auto; }
  .plan-top .overlay, .plan-bottom .overlay { position: relative; }
  .rooms { flex: 1; background: none; border: none; backdrop-filter: none;
           display: flex; flex-wrap: wrap; gap: 6px; align-items: flex-start; }
  .room { display: inline-flex; align-items: center; gap: 2px; background: rgba(15, 34, 51, 0.88);
          border: 1px solid var(--line); border-radius: var(--r-sm); padding: 2px 2px 2px 10px; }
  .room span { font-weight: 500; margin-right: 4px; }
  .room button { background: none; border: 0; color: var(--muted); padding: 5px 6px; border-radius: var(--r-sm);
                 display: grid; place-items: center; }
  .room button:hover { color: var(--chalk); background: var(--plan-2); }
  .rooms-hint { color: var(--faint); font-size: .85rem; padding: 6px 2px; }

  /* legend, top right */
  .legend { padding: 8px 12px; font-size: .82rem; color: var(--muted); flex: none;
            display: grid; grid-template-columns: auto auto; gap: 4px 10px; align-items: center; }
  .legend svg { width: 16px; height: 16px; }

  /* drive controls, bottom left */
  .drive { padding: 10px 12px; margin-right: auto; max-width: 640px; }
  .drive-head { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .drive-toggle { display: flex; align-items: center; justify-content: center; gap: 8px;
                  padding: 9px 12px; border-radius: var(--r-md); border: 1px solid var(--line);
                  background: var(--plan-2); font-weight: 500; }
  .drive-toggle:hover { border-color: var(--muted); }
  .drive.on .drive-toggle { background: var(--amber); border-color: var(--amber); color: var(--amber-ink); }
  .drive-body { display: none; margin-top: 10px; gap: 16px; align-items: center; }
  .drive.on .drive-body { display: flex; flex-wrap: wrap; }
  .keys { display: grid; grid-template-columns: repeat(3, 42px); grid-template-rows: 38px 38px; gap: 5px; }
  .key { display: grid; place-items: center; border: 1px solid var(--line); border-radius: var(--r-sm);
         background: var(--ink); color: var(--muted); font-weight: 500; user-select: none; touch-action: none;
         box-shadow: inset 0 -2px 0 var(--line); }
  .key.w { grid-column: 2; }
  .key.a { grid-column: 1; grid-row: 2; } .key.s { grid-column: 2; grid-row: 2; } .key.d { grid-column: 3; grid-row: 2; }
  .key.down { background: var(--amber); color: var(--amber-ink); border-color: var(--amber); box-shadow: none; }
  .readout { font-size: .85rem; color: var(--muted); display: grid; gap: 2px; }
  .readout b { color: var(--chalk); font-weight: 500; }
  .drive-state { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: .88rem; }
  .state[hidden] { display: none; }
  .state { display: flex; gap: 8px; align-items: flex-start; color: var(--muted); }
  .state::before { content: ""; width: 8px; height: 8px; border-radius: 50%; margin-top: 6px; flex: none;
                   background: var(--faint); }
  .state.good { color: var(--chalk); } .state.good::before { background: var(--ok); }
  .state.bad { color: var(--warn); } .state.bad::before { background: var(--warn); }
  .drive-actions { display: flex; flex-direction: column; gap: 6px; }
  .drive-actions button { flex: 1; display: flex; gap: 6px; align-items: center; justify-content: center;
                          padding: 7px 8px; border-radius: var(--r-sm); border: 1px solid var(--line);
                          background: none; color: var(--chalk); font-size: .88rem; }
  .drive-actions button:hover { background: var(--plan-2); }

  /* title block, bottom right — cells, as on a drawing */
  .titleblock { flex: none; margin: 0; display: grid; grid-template-columns: auto auto;
                font-size: .85rem; min-width: 250px; overflow: hidden; }
  .titleblock .name { grid-column: 1 / -1; padding: 8px 12px; font-weight: 600; font-size: 1.05rem;
                      border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; gap: 12px; }
  .titleblock dt, .titleblock dd { margin: 0; padding: 5px 12px; border-bottom: 1px solid var(--line); }
  .titleblock dt { color: var(--muted); border-right: 1px solid var(--line); }
  .titleblock dd { text-align: right; }
  .titleblock dt:nth-last-of-type(1), .titleblock dd:last-of-type { border-bottom: 0; }
  #conn { font-weight: 500; font-size: .85rem; color: var(--muted); }
  #conn.ok { color: var(--ok); } #conn.bad { color: var(--err); }
  dd.slow { color: var(--warn); }

  #zoneForm { display: none; position: absolute; z-index: 5; width: 250px; padding: 12px; }
  #zoneForm label { display: block; font-size: .85rem; color: var(--muted); margin-bottom: 6px; }
  #zoneForm input { width: 100%; padding: 8px 10px; border-radius: var(--r-sm); border: 1px solid var(--line);
                    background: var(--ink); margin-bottom: 8px; }
  .row { display: flex; gap: 6px; }
  .btn { padding: 7px 12px; border-radius: var(--r-sm); border: 1px solid var(--line); background: var(--plan-2); }
  .btn:hover { border-color: var(--muted); }
  .btn.primary { background: var(--amber); border-color: var(--amber); color: var(--amber-ink); font-weight: 500; }

  .toast { position: absolute; left: 50%; top: 14px; transform: translateX(-50%); max-width: min(560px, 70%);
           padding: 10px 14px; font-size: .92rem; display: none; z-index: 6; }
  .toast.show { display: block; }
  .toast b { color: var(--amber); font-weight: 500; }

  /* ---------------- side column ---------------- */
  .side { display: grid; grid-template-rows: auto minmax(0, 1fr) minmax(0, 1.05fr); min-height: 0; background: var(--plan); }
  .order { padding: 14px 16px 12px; border-bottom: 1px solid var(--line); }
  .order-row { display: flex; gap: 8px; }
  #goalInput { flex: 1; min-width: 0; padding: 11px 13px; border-radius: var(--r-md); border: 1px solid var(--line);
               background: var(--ink); font-size: 1rem; }
  #goalInput::placeholder { color: var(--faint); }
  .icon-btn { display: grid; place-items: center; width: 44px; border-radius: var(--r-md);
              border: 1px solid var(--line); background: var(--plan-2); font-size: 1.15rem; }
  .icon-btn:hover { border-color: var(--muted); }
  .send { background: var(--amber); border-color: var(--amber); color: var(--amber-ink); }
  .mic.listening { background: var(--err); border-color: var(--err); color: var(--ink); }
  .presets { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 9px; font-size: .88rem; }
  .presets button { background: none; border: 0; padding: 0; color: var(--muted); text-decoration: underline;
                    text-decoration-color: var(--line); text-underline-offset: 4px; }
  .presets button:hover { color: var(--chalk); text-decoration-color: var(--amber); }

  .panel { display: flex; flex-direction: column; min-height: 0; border-bottom: 1px solid var(--line); }
  .panel-head { display: flex; align-items: center; gap: 10px; padding: 10px 16px 0; flex-wrap: wrap; }
  .panel-head h2 { margin: 0; font-size: 1.05rem; font-weight: 600; margin-right: auto; }
  .tabs { display: flex; gap: 2px; }
  .tab { background: none; border: 0; border-bottom: 2px solid transparent; padding: 6px 10px; color: var(--muted); }
  .tab[aria-selected="true"] { color: var(--chalk); border-bottom-color: var(--amber); }
  .scroll { overflow-y: auto; min-height: 0; flex: 1; padding: 10px 16px 14px; }
  .muted-note { color: var(--faint); font-size: .9rem; padding: 6px 0; }

  /* the agent's reasoning, as a thread in time */
  #timeline { display: flex; flex-direction: column; gap: 6px; }
  .t-goal { margin-top: 10px; padding: 8px 12px; border-left: 3px solid var(--amber); background: var(--ink);
            border-radius: 0 var(--r-sm) var(--r-sm) 0; font-size: 1.02rem; }
  .t-goal small, .t-answer small { display: block; color: var(--faint); font-size: .75rem; }
  .t-step { margin-left: 14px; padding-left: 12px; border-left: 1px solid var(--line); color: var(--muted); font-size: .9rem; }
  .t-step code { font-family: inherit; color: var(--chalk); }
  .t-step.ok { color: var(--ok); } .t-step.fail { color: var(--err); }
  .t-think { margin-left: 14px; padding: 6px 12px; border-left: 1px solid var(--cyan); color: #bfe6f0; font-size: .92rem; }
  .t-answer { margin: 4px 0 4px 14px; padding: 9px 12px; border-left: 3px solid var(--ok); background: var(--ink);
              border-radius: 0 var(--r-sm) var(--r-sm) 0; color: var(--chalk); }
  #logs { font-family: "Cascadia Mono", ui-monospace, monospace; font-size: .8rem; display: none; }
  .log { display: grid; grid-template-columns: auto auto 1fr; gap: 10px; padding: 2px 0; }
  .log .ts { color: var(--faint); } .log .node { color: var(--cyan); }
  .log.l30 .msg { color: var(--warn); } .log.l40 .msg, .log.l50 .msg { color: var(--err); }
  .log-filters { display: none; gap: 10px; align-items: center; font-size: .85rem; color: var(--muted); }
  .log-filters select { background: var(--ink); border: 1px solid var(--line); border-radius: var(--r-sm); padding: 2px 6px; }

  /* memory, as annotations on the plan */
  .mem-search { display: flex; gap: 8px; align-items: center; padding: 8px 16px 0; }
  #memQuery { flex: 1; min-width: 0; padding: 8px 11px; border-radius: var(--r-md); border: 1px solid var(--line); background: var(--ink); }
  #memQuery::placeholder { color: var(--faint); }
  .check { display: flex; gap: 6px; align-items: center; font-size: .85rem; color: var(--muted); white-space: nowrap; }
  .check input { accent-color: var(--cyan); }
  #memTabs { padding: 4px 16px 0; }
  #memTabs .tab b { font-weight: 500; color: var(--faint); margin-left: 4px; }
  #memList { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 10px; align-content: start; }
  .note { position: relative; border-left: 3px solid var(--cyan); background: var(--ink); padding: 10px 12px 9px;
          border-radius: 0 var(--r-sm) var(--r-sm) 0; }
  .note.stale { border-left-color: var(--faint); opacity: .62; }
  .note.sel { box-shadow: inset 0 0 0 1px var(--cyan); }
  .note .bar { position: absolute; top: 0; left: 0; height: 2px; background: var(--cyan); }
  .note h3 { margin: 0; font-size: 1rem; font-weight: 600; }
  .note h3 small { font-weight: 400; color: var(--muted); font-size: .85rem; margin-left: 6px; }
  .note .meta { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: .8rem; color: var(--muted); margin: 3px 0 6px; }
  .note .meta .warn { color: var(--warn); }
  .note .doc { font-size: .88rem; color: #c9d6df; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
  .note .doc.open { -webkit-line-clamp: unset; }
  .note .fact + .fact { margin-top: 5px; }
  .note .fact b { color: var(--chalk); font-weight: 500; }
  .note .acts { display: flex; gap: 2px; margin-top: 6px; }
  .note .acts button { background: none; border: 0; color: var(--muted); padding: 5px 7px; border-radius: var(--r-sm);
                       display: flex; align-items: center; gap: 5px; font-size: .82rem; }
  .note .acts button:hover { color: var(--chalk); background: var(--plan-2); }
  .note .acts .del { margin-left: auto; }
  .note .acts .del:hover { color: var(--err); }

  @media (max-width: 1100px) {
    .station { grid-template-columns: 1fr; height: auto; }
    .plan { height: 72vh; border-right: 0; border-bottom: 1px solid var(--line); }
    .side { grid-template-rows: auto 60vh 70vh; }
  }
  @media (max-width: 640px) {
    .legend, .titleblock { display: none; }
  }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <symbol id="i-send" viewBox="0 0 24 24"><path d="M4 12h14M13 6l6 6-6 6"/></symbol>
  <symbol id="i-mic" viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></symbol>
  <symbol id="i-go" viewBox="0 0 24 24"><path d="M12 3l7 17-7-4-7 4z"/></symbol>
  <symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></symbol>
  <symbol id="i-trash" viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></symbol>
  <symbol id="i-locate" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/></symbol>
  <symbol id="i-pin" viewBox="0 0 24 24"><path d="M12 21s-6-6.2-6-11a6 6 0 0 1 12 0c0 4.8-6 11-6 11z"/><circle cx="12" cy="10" r="2"/></symbol>
  <symbol id="i-save" viewBox="0 0 24 24"><path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/></symbol>
  <symbol id="i-wheel" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/><path d="M12 4v6M5.5 16l5-3M18.5 16l-5-3"/></symbol>
  <symbol id="i-x" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></symbol>
  <symbol id="g-robot" viewBox="0 0 16 16"><path d="M8 2l5 12-5-3-5 3z" fill="#f2a93b" stroke="none"/></symbol>
  <symbol id="g-zone" viewBox="0 0 16 16"><rect x="2" y="3" width="12" height="10" fill="none" stroke="#e6edf2" stroke-dasharray="2 2"/><path d="M2 9l6-6M5 13l9-9M10 13l4-4" stroke="#e6edf2" stroke-opacity=".45"/></symbol>
  <symbol id="g-mem" viewBox="0 0 16 16"><path d="M8 2l6 6-6 6-6-6z" fill="#5ec4dc" stroke="none"/></symbol>
  <symbol id="g-stale" viewBox="0 0 16 16"><path d="M8 3l5 5-5 5-5-5z" fill="none" stroke="#6b8699" stroke-width="1.5"/></symbol>
</svg>

<div class="station">
  <main class="plan" aria-label="Plano del mapa SLAM">
    <canvas id="mapCanvas"></canvas>
    <div class="plan-empty" id="planEmpty"><div><strong>Esperando el mapa</strong>SLAM publicará el plano en cuanto el robot empiece a escanear.</div></div>

    <div class="plan-top" id="planTop">
    <div class="overlay rooms" id="zonesList"></div>
    <div class="overlay legend" aria-label="Leyenda">
      <svg><use href="#g-robot"/></svg><span>Robot</span>
      <svg><use href="#g-zone"/></svg><span>Habitación</span>
      <svg><use href="#g-mem"/></svg><span>Recuerdo</span>
      <svg><use href="#g-stale"/></svg><span>De otro mapa</span>
    </div>
    </div>

    <form id="zoneForm" class="overlay" onsubmit="saveZone(false); return false;">
      <label for="zoneName">Nombre de la habitación</label>
      <input id="zoneName" list="roomNames" placeholder="cocina, salón, dormitorio…" autocomplete="off">
      <datalist id="roomNames"></datalist>
      <div class="row">
        <button type="submit" class="btn primary">Guardar</button>
        <button type="button" class="btn" onclick="saveZone(true)">Guardar y explorar</button>
        <button type="button" class="btn" onclick="hideZoneForm()" aria-label="Cancelar"><svg class="icon"><use href="#i-x"/></svg></button>
      </div>
    </form>

    <div class="plan-bottom" id="planBottom">
    <section class="overlay drive" id="drive" aria-label="Conducción manual">
      <div class="drive-head">
        <button class="drive-toggle" id="teleBtn" onclick="toggleTeleop()" aria-pressed="false">
          <svg class="icon"><use href="#i-wheel"/></svg><span id="teleLabel">Conducir con el teclado</span>
        </button>
        <div class="drive-state">
          <div class="state" id="driverState">Nadie conduce</div>
          <div class="state" id="kbdState" hidden></div>
          <div class="state" id="moveState" hidden></div>
        </div>
      </div>
      <div class="drive-body">
        <div class="keys" aria-hidden="true">
          <div class="key w" data-key="w">W</div>
          <div class="key a" data-key="a">A</div><div class="key s" data-key="s">S</div><div class="key d" data-key="d">D</div>
        </div>
        <div class="readout">
          <span>Avance <b id="teleLin">0.00</b> m/s</span>
          <span>Giro <b id="teleAng">0.00</b> rad/s</span>
          <span>Mayús acelera, espacio frena</span>
        </div>
        <div class="drive-actions">
          <button onclick="markZoneHere()"><svg class="icon"><use href="#i-pin"/></svg>Nombrar esta habitación</button>
          <button onclick="saveMap()"><svg class="icon"><use href="#i-save"/></svg>Guardar mapa</button>
        </div>
      </div>
    </section>

    <dl class="overlay titleblock">
      <div class="name"><span>Robot RAG Agent</span><span id="conn">conectando</span></div>
      <dt>Simulación</dt><dd id="simRtf">—</dd>
      <dt>Plano</dt><dd id="mapdims">—</dd>
      <dt>Robot</dt><dd id="robotpos">—</dd>
      <dt>Memorias</dt><dd id="memcount">—</dd>
    </dl>
    </div>

    <div class="overlay toast" id="toast" role="status" aria-live="polite"></div>
  </main>

  <aside class="side">
    <form class="order" onsubmit="sendGoal(); return false;">
      <div class="order-row">
        <input id="goalInput" placeholder="Dile al robot qué hacer" aria-label="Orden para el robot" autocomplete="off">
        <button type="button" class="icon-btn mic" id="micBtn" onclick="toggleMic()" aria-label="Dictar por voz"><svg class="icon"><use href="#i-mic"/></svg></button>
        <button type="submit" class="icon-btn send" aria-label="Enviar orden"><svg class="icon"><use href="#i-send"/></svg></button>
      </div>
      <div class="presets">
        <button type="button" onclick="preset('Ve donde se suele cocinar')">Ve donde se suele cocinar</button>
        <button type="button" onclick="preset('Explora el entorno durante 60 segundos')">Explora 60 s</button>
        <button type="button" onclick="preset('Dime dónde estás y qué ves')">¿Dónde estás?</button>
      </div>
    </form>

    <section class="panel" aria-label="Agente">
      <div class="panel-head">
        <h2>El agente</h2>
        <div class="log-filters" id="logFilters">
          <label class="check"><input type="checkbox" id="onlyAgent" checked> solo el agente</label>
          <select id="minLevel" aria-label="Nivel mínimo">
            <option value="20" selected>info</option><option value="30">avisos</option><option value="40">errores</option>
          </select>
        </div>
        <div class="tabs" role="tablist">
          <button class="tab" role="tab" aria-selected="true" id="tabThread" onclick="showAgentTab('thread')">Razonamiento</button>
          <button class="tab" role="tab" aria-selected="false" id="tabLogs" onclick="showAgentTab('logs')">Logs ROS</button>
        </div>
      </div>
      <div class="scroll" id="agentScroll">
        <div id="timeline"><div class="muted-note" id="threadEmpty">Aún no hay órdenes. Escribe una arriba o pulsa una sugerencia.</div></div>
        <div id="logs"></div>
      </div>
    </section>

    <section class="panel" aria-label="Memoria">
      <div class="panel-head"><h2>Memoria</h2></div>
      <div class="mem-search">
        <input id="memQuery" placeholder="Pregúntale a la memoria: dónde se suele cocinar" aria-label="Buscar en la memoria"
               onkeydown="if(event.key==='Enter')loadMemory()">
        <label class="check" title="Solo lo que el planificador puede recuperar en este mapa">
          <input type="checkbox" id="memActiveOnly" checked onchange="loadMemory()"> este mapa
        </label>
        <button class="icon-btn" style="width:38px;height:36px" onclick="loadMemory()" aria-label="Buscar"><svg class="icon"><use href="#i-search"/></svg></button>
      </div>
      <div class="tabs" id="memTabs" role="tablist"></div>
      <div class="scroll"><div id="memList"></div></div>
    </section>
  </aside>
</div>

<script>
const AGENT_NODES = /^(rag_node|skills_executor_node|llm_planner_node|basic_navigator|dashboard_node|cmd_vel_mux_node|bt_navigator|planner_server|controller_server|slam_toolbox)/;
const $ = (id) => document.getElementById(id);
function esc(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
function fmtTs(t) { return new Date(t * 1000).toLocaleTimeString('es-ES'); }
function nearBottom(el) { return el.scrollHeight - el.scrollTop - el.clientHeight < 60; }
const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

let toastTimer = null;
function toast(html, ms) {
  const el = $('toast'); el.innerHTML = html; el.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), ms || 7000);
}

// ===================== the agent's thread =====================
let lastId = 0;
function showAgentTab(which) {
  const logs = which === 'logs';
  $('timeline').style.display = logs ? 'none' : 'flex';
  $('logs').style.display = logs ? 'block' : 'none';
  $('logFilters').style.display = logs ? 'flex' : 'none';
  $('tabThread').setAttribute('aria-selected', String(!logs));
  $('tabLogs').setAttribute('aria-selected', String(logs));
  const sc = $('agentScroll'); sc.scrollTop = sc.scrollHeight;
}

function threadItem(ev) {
  const text = ev.text || '';
  const div = document.createElement('div');
  if (ev.type === 'goal') {
    div.className = 't-goal';
    div.innerHTML = `<small>Orden, ${fmtTs(ev.ts)}</small>${esc(text)}`;
    return div;
  }
  if (ev.type === 'response') {
    div.className = 't-answer';
    div.innerHTML = `<small>Respuesta</small>${esc(text)}`;
    return div;
  }
  if (text.startsWith('Received goal:')) return null;       // the goal is already shown
  if (text.startsWith('Qwen razona:')) {
    div.className = 't-think';
    div.textContent = text.slice('Qwen razona:'.length).trim();
    return div;
  }
  let m = text.match(/^Plan paso (\d+)\/(\d+): (\w+)\((.*)\)$/);
  if (m) {
    div.className = 't-step';
    div.innerHTML = `Paso ${m[1]} de ${m[2]}: <code>${esc(m[3])}</code> ${esc(m[4])}`;
    return div;
  }
  m = text.match(/^Step (\d+)\/(\d+) \((\w+)\): (.*)$/);
  if (m) {
    const ok = m[4] === 'ok';
    div.className = 't-step ' + (ok ? 'ok' : 'fail');
    div.textContent = ok ? `Paso ${m[1]} hecho` : `Paso ${m[1]} falló: ${m[4].replace(/^error:\s*/, '')}`;
    return div;
  }
  const plain = { 'Generating plan with Qwen...': 'Planificando…', 'Generando respuesta final...': 'Redactando la respuesta…',
                  'Dry run: plan produced, execution skipped': 'Plan decidido sin ejecutarlo (modo benchmark)' };
  div.className = 't-step';
  div.textContent = plain[text] || text;
  return div;
}

function addEvent(ev) {
  const sc = $('agentScroll'); const stick = nearBottom(sc);
  if (ev.type === 'rosout') {
    const div = document.createElement('div');
    div.className = 'log l' + ev.level;
    div.dataset.node = ev.source; div.dataset.level = ev.level;
    div.innerHTML = `<span class="ts">${fmtTs(ev.ts)}</span><span class="node">${esc(ev.source)}</span><span class="msg">${esc(ev.text)}</span>`;
    filterLog(div); $('logs').appendChild(div);
    while ($('logs').children.length > 1500) $('logs').removeChild($('logs').firstChild);
  } else {
    const item = threadItem(ev);
    if (item) { $('threadEmpty')?.remove(); $('timeline').appendChild(item); }
  }
  if (stick) sc.scrollTop = sc.scrollHeight;
}
function filterLog(div) {
  const show = (+div.dataset.level >= +$('minLevel').value) && (!$('onlyAgent').checked || AGENT_NODES.test(div.dataset.node));
  div.style.display = show ? '' : 'none';
}
$('onlyAgent').onchange = $('minLevel').onchange = () => { for (const d of $('logs').children) filterLog(d); };

async function pollEvents() {
  try {
    const data = await (await fetch(`/api/events?since=${lastId}`)).json();
    for (const ev of data.events) { lastId = ev.id; addEvent(ev); }
    setConn(true);
  } catch (e) { setConn(false); }
  setTimeout(pollEvents, 700);
}
function setConn(ok) { const c = $('conn'); c.className = ok ? 'ok' : 'bad'; c.textContent = ok ? 'conectado' : 'sin conexión'; }

function preset(t) { $('goalInput').value = t; sendGoal(); }
async function sendGoal(text) {
  const input = $('goalInput'); const t = (text || input.value).trim();
  if (!t) return;
  await post('/api/goal', { text: t });
  if (!text) input.value = '';
}

let rec = null, listening = false;
function toggleMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { toast('Este navegador no reconoce voz. Usa Chrome o Edge.'); return; }
  if (listening) { rec.stop(); return; }
  rec = new SR(); rec.lang = 'es-ES'; rec.interimResults = true;
  rec.onresult = (e) => {
    $('goalInput').value = Array.from(e.results).map(r => r[0].transcript).join('');
    if (e.results[e.results.length - 1].isFinal) sendGoal();
  };
  rec.onstart = () => { listening = true; $('micBtn').classList.add('listening'); };
  rec.onend = () => { listening = false; $('micBtn').classList.remove('listening'); };
  rec.onerror = rec.onend; rec.start();
}

// ===================== the plan =====================
const canvas = $('mapCanvas'), ctx = canvas.getContext('2d');
const mapImg = new Image();
let mapMeta = null, mapStamp = null, robot = null, zones = {}, sim = { rtf: null, driver: null };
let view = null, sel = null, dragging = false, pendingArea = null;
const trail = [];
let hatch = null;

async function pollMap() {
  try {
    const data = await (await fetch('/api/map')).json();
    robot = data.robot; zones = data.zones || {}; sim = data.sim || sim;
    if (robot) {
      const last = trail[trail.length - 1];
      if (!last || Math.hypot(last.x - robot.x, last.y - robot.y) > 0.05) { trail.push({ x: robot.x, y: robot.y, t: Date.now() }); }
      while (trail.length > 400) trail.shift();
    }
    if (data.map) {
      mapMeta = data.map;
      // The version, not the stamp: simulation time restarts with every launch,
      // so a stamp alone let the browser show a previous run's cached image.
      if (data.map.version !== mapStamp) { mapStamp = data.map.version; mapImg.src = '/api/map/png?v=' + encodeURIComponent(mapStamp); }
      $('mapdims').textContent = `${(mapMeta.width * mapMeta.resolution).toFixed(1)} × ${(mapMeta.height * mapMeta.resolution).toFixed(1)} m`;
      $('planEmpty').style.display = 'none';
    }
    $('robotpos').textContent = robot
      ? `${robot.x.toFixed(2)}, ${robot.y.toFixed(2)}` + (robot.yaw != null ? `  ${Math.round(robot.yaw * 180 / Math.PI)}°` : '') : '—';
    const rtf = $('simRtf');
    rtf.textContent = sim.rtf == null ? 'sin reloj' : `×${sim.rtf.toFixed(2)}`;
    rtf.className = sim.rtf != null && sim.rtf < 0.35 ? 'slow' : '';
    renderRooms(); updateDriveState();
  } catch (e) { /* the connection state comes from pollEvents */ }
  setTimeout(pollMap, 1000);
}

function resizeCanvas() {
  // Every frame, not only on 'resize': overlays and panels shift the layout
  // without firing a resize event, which used to desync canvas pixels from its
  // box and send every drag-to-select to the wrong coordinates (ADR-010).
  const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  const w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
function computeView() {
  // Fit the plan between the top row and the bottom bar, so no panel ever
  // hides part of the house.
  const r = canvas.getBoundingClientRect();
  const top = $('planTop').getBoundingClientRect().bottom - r.top + 16;
  const bottom = r.bottom - $('planBottom').getBoundingClientRect().top + 16;
  const mw = mapMeta.width * mapMeta.resolution, mh = mapMeta.height * mapMeta.resolution;
  const availH = Math.max(80, r.height - top - bottom), side = 24;
  const scale = Math.min((r.width - side * 2) / mw, availH / mh);
  return { ox: (r.width - mw * scale) / 2, oy: top + (availH - mh * scale) / 2, scale, mw, mh };
}
function w2c(x, y) { return [view.ox + (x - mapMeta.origin_x) * view.scale, view.oy + (view.mh - (y - mapMeta.origin_y)) * view.scale]; }
function c2w(cx, cy) { return [mapMeta.origin_x + (cx - view.ox) / view.scale, mapMeta.origin_y + view.mh - (cy - view.oy) / view.scale]; }

function hatchPattern() {
  if (hatch) return hatch;
  const c = document.createElement('canvas'); c.width = c.height = 10;
  const g = c.getContext('2d'); g.strokeStyle = 'rgba(230,237,242,0.22)'; g.lineWidth = 1;
  g.beginPath(); g.moveTo(0, 10); g.lineTo(10, 0); g.stroke();
  hatch = ctx.createPattern(c, 'repeat'); return hatch;
}

function draw() {
  requestAnimationFrame(draw);
  resizeCanvas();
  const r = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, r.width, r.height);
  if (!mapMeta || !mapImg.complete || !mapImg.naturalWidth) return;
  view = computeView();
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(mapImg, view.ox, view.oy, view.mw * view.scale, view.mh * view.scale);

  // one-metre grid over the plan
  ctx.strokeStyle = 'rgba(94,196,220,0.07)'; ctx.lineWidth = 1; ctx.beginPath();
  for (let x = Math.ceil(mapMeta.origin_x); x < mapMeta.origin_x + view.mw; x++) { const [cx] = w2c(x, 0); ctx.moveTo(cx, view.oy); ctx.lineTo(cx, view.oy + view.mh * view.scale); }
  for (let y = Math.ceil(mapMeta.origin_y); y < mapMeta.origin_y + view.mh; y++) { const [, cy] = w2c(0, y); ctx.moveTo(view.ox, cy); ctx.lineTo(view.ox + view.mw * view.scale, cy); }
  ctx.stroke();

  // rooms: hatched, dashed outline, named
  ctx.font = '500 13px Bahnschrift, Barlow, sans-serif';
  for (const [name, z] of Object.entries(zones)) {
    const [x1, y1] = w2c(z.x_min, z.y_max), [x2, y2] = w2c(z.x_max, z.y_min);
    ctx.fillStyle = hatchPattern(); ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
    ctx.setLineDash([6, 4]); ctx.strokeStyle = 'rgba(230,237,242,0.7)'; ctx.lineWidth = 1.2;
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1); ctx.setLineDash([]);
    const tw = ctx.measureText(name).width;
    ctx.fillStyle = 'rgba(15,34,51,0.9)'; ctx.fillRect(x1 + 3, y1 + 3, tw + 10, 20);
    ctx.fillStyle = '#e6edf2'; ctx.fillText(name, x1 + 8, y1 + 17);
  }

  // memories where they were learned
  for (const entry of memEntries) {
    if (entry.x == null || entry.y == null) continue;
    const [px, py] = w2c(entry.x, entry.y), s = entry.id === memSelected ? 7 : 5;
    ctx.beginPath(); ctx.moveTo(px, py - s); ctx.lineTo(px + s, py); ctx.lineTo(px, py + s); ctx.lineTo(px - s, py); ctx.closePath();
    if (entry.stale) { ctx.strokeStyle = '#6b8699'; ctx.lineWidth = 1.5; ctx.stroke(); }
    else { ctx.fillStyle = '#5ec4dc'; ctx.fill(); }
    if (entry.id === memSelected) {
      ctx.beginPath(); ctx.arc(px, py, 14, 0, Math.PI * 2); ctx.strokeStyle = '#5ec4dc'; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.fillStyle = '#5ec4dc'; ctx.fillText(entry.zone || entry.label || entry.title, px + 18, py + 4);
    }
  }

  // selection being dragged
  if (sel) {
    const [x1, y1] = w2c(sel.x_min, sel.y_max), [x2, y2] = w2c(sel.x_max, sel.y_min);
    ctx.fillStyle = 'rgba(242,169,59,0.10)'; ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
    ctx.setLineDash([5, 4]); ctx.strokeStyle = '#f2a93b'; ctx.lineWidth = 1.5; ctx.strokeRect(x1, y1, x2 - x1, y2 - y1); ctx.setLineDash([]);
  }

  // where the robot has been, and where it faces
  if (trail.length > 1) {
    ctx.beginPath();
    trail.forEach((p, i) => { const [cx, cy] = w2c(p.x, p.y); i ? ctx.lineTo(cx, cy) : ctx.moveTo(cx, cy); });
    ctx.strokeStyle = 'rgba(242,169,59,0.35)'; ctx.lineWidth = 2; ctx.stroke();
  }
  if (robot) {
    const [rx, ry] = w2c(robot.x, robot.y), yaw = robot.yaw || 0;
    ctx.save(); ctx.translate(rx, ry); ctx.rotate(-yaw);
    ctx.beginPath(); ctx.arc(0, 0, 15, 0, Math.PI * 2); ctx.fillStyle = 'rgba(242,169,59,0.16)'; ctx.fill();
    ctx.beginPath(); ctx.moveTo(12, 0); ctx.lineTo(-8, 8); ctx.lineTo(-4, 0); ctx.lineTo(-8, -8); ctx.closePath();
    ctx.fillStyle = '#f2a93b'; ctx.fill(); ctx.strokeStyle = '#0f2233'; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.restore();
  }
}

function evPos(e) { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
canvas.addEventListener('mousedown', (e) => {
  if (!view) return;
  hideZoneForm(); dragging = true;
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
  const form = $('zoneForm'), [px, py] = evPos(e), r = canvas.getBoundingClientRect();
  form.style.left = Math.min(px, r.width - 270) + 'px'; form.style.top = Math.min(py, r.height - 150) + 'px';
  form.style.display = 'block'; $('zoneName').focus();
});
function hideZoneForm() { $('zoneForm').style.display = 'none'; $('zoneName').value = ''; sel = null; pendingArea = null; }

function reportZoneSaved(data) {
  toast(data.room_type
    ? `Guardada <b>${esc(data.name)}</b>, reconocida como ${esc(data.room_type)}: ya puedes pedir cosas como «ve donde se suele cocinar».`
    : `Guardada <b>${esc(data.name)}</b>. No es un nombre de habitación que el robot reconozca, así que solo la encontrará por su nombre.`);
}
async function saveZone(exploreAfter) {
  const name = $('zoneName').value.trim();
  if (!name || !pendingArea) return;
  const data = await (await post('/api/zones', { name, ...pendingArea })).json();
  if (data.ok) { reportZoneSaved(data); if (exploreAfter) sendGoal(`Explora la zona ${data.name} durante 60 segundos`); }
  hideZoneForm(); refreshRooms(); loadMemory();
}
async function markZoneHere() {
  const name = prompt('¿Cómo se llama la habitación donde está el robot? (cocina, salón, dormitorio…)');
  if (!name) return;
  const data = await (await post('/api/zones/here', { name, size: 2.0 })).json();
  if (!data.ok) { toast(`No se pudo nombrar: ${esc(data.error || 'error desconocido')}`); return; }
  reportZoneSaved(data); refreshRooms(); loadMemory();
}
async function saveMap() {
  const name = prompt('Nombre del mapa (usa «house» para que la demo lo cargue sola):', 'house');
  if (name === null) return;
  toast('Guardando el mapa…', 30000);
  const data = await (await post('/api/map/save', { name })).json();
  toast(data.ok
    ? `Mapa guardado como <b>${esc(data.result.map_id)}</b>. Para empezar desde él: saved_map:=${esc(data.result.map_id)}`
    : `No se pudo guardar el mapa: ${esc(data.error || 'error desconocido')}`, 12000);
}

function renderRooms() {
  const el = $('zonesList'); const names = Object.keys(zones).sort();
  if (el.dataset.names === names.join('|')) return;
  el.dataset.names = names.join('|'); el.innerHTML = '';
  if (!names.length) { el.innerHTML = '<div class="rooms-hint">Arrastra sobre el plano para nombrar una habitación.</div>'; return; }
  for (const name of names) {
    const chip = document.createElement('div'); chip.className = 'room';
    chip.innerHTML = `<span>${esc(name)}</span>` +
      `<button aria-label="Ir a ${esc(name)}" title="Ir"><svg class="icon"><use href="#i-go"/></svg></button>` +
      `<button aria-label="Explorar ${esc(name)}" title="Explorar"><svg class="icon"><use href="#i-search"/></svg></button>` +
      `<button aria-label="Borrar ${esc(name)}" title="Borrar"><svg class="icon"><use href="#i-trash"/></svg></button>`;
    const [go, ex, del] = chip.querySelectorAll('button');
    go.onclick = () => sendGoal(`Ve a la zona ${name}`);
    ex.onclick = () => sendGoal(`Explora la zona ${name} durante 60 segundos`);
    del.onclick = async () => {
      if (!confirm(`¿Borrar la habitación «${name}»?`)) return;
      await fetch(`/api/zones/${encodeURIComponent(name)}`, { method: 'DELETE' }); refreshRooms();
    };
    el.appendChild(chip);
  }
}
async function refreshRooms() {
  try { const data = await (await fetch('/api/map')).json(); zones = data.zones || {}; $('zonesList').dataset.names = ''; renderRooms(); } catch (e) {}
}

// ===================== driving =====================
// The browser sends the keys it holds, never a velocity; the node picks the
// speed and stops the robot on its own if the refreshes stop (ADR-023).
const TELE_KEYS = { w: 'w', a: 'a', s: 's', d: 'd', arrowup: 'w', arrowleft: 'a', arrowdown: 's', arrowright: 'd' };
let teleopOn = false, held = new Set(), boost = false, teleTimer = null, driveStart = null, driveStartPose = null;

function toggleTeleop() {
  teleopOn = !teleopOn;
  $('drive').classList.toggle('on', teleopOn);
  $('teleBtn').setAttribute('aria-pressed', String(teleopOn));
  $('teleLabel').textContent = teleopOn ? 'Dejar de conducir' : 'Conducir con el teclado';
  if (teleopOn) { teleTimer = setInterval(refreshTeleop, 150); $('teleBtn').blur(); canvas.focus(); }
  else { clearInterval(teleTimer); teleTimer = null; releaseAll(); }
  paintKeys(); updateDriveState();
}
function releaseAll() {
  held.clear(); boost = false; paintKeys(); setTele(0, 0); driveStart = null;
  post('/api/teleop/stop').catch(() => {});
}
function setTele(lin, ang) { $('teleLin').textContent = lin.toFixed(2); $('teleAng').textContent = ang.toFixed(2); }
async function refreshTeleop() {
  if (!teleopOn || held.size === 0) return;
  try {
    const data = await (await post('/api/teleop', { keys: [...held], boost })).json();
    setTele(data.linear, data.angular);
  } catch (e) { /* network gone: the node's deadman stops the robot */ }
}
function paintKeys() { for (const el of document.querySelectorAll('.key')) el.classList.toggle('down', teleopOn && held.has(el.dataset.key)); }
function typing() { const el = document.activeElement; return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable); }
function press(key) {
  if (!held.has(key)) { held.add(key); if (!driveStart) { driveStart = Date.now(); driveStartPose = robot && { ...robot }; } }
  paintKeys(); refreshTeleop();
}
function release(key) { held.delete(key); paintKeys(); if (held.size === 0) releaseAll(); else refreshTeleop(); }

document.addEventListener('keydown', (e) => {
  if (!teleopOn || typing() || e.repeat) return;
  if (e.key === 'Shift') { boost = true; refreshTeleop(); return; }
  if (e.key === ' ') { e.preventDefault(); releaseAll(); return; }
  const key = TELE_KEYS[e.key.toLowerCase()]; if (!key) return;
  e.preventDefault(); press(key);
});
document.addEventListener('keyup', (e) => {
  if (!teleopOn) return;
  if (e.key === 'Shift') { boost = false; refreshTeleop(); return; }
  const key = TELE_KEYS[e.key.toLowerCase()]; if (key) release(key);
});
// Switching window with a key down must not leave the robot driving: that keyup never arrives.
window.addEventListener('blur', () => { if (teleopOn) releaseAll(); updateDriveState(); });
window.addEventListener('focus', updateDriveState);
for (const el of document.querySelectorAll('.key')) {
  el.addEventListener('pointerdown', (e) => { e.preventDefault(); if (teleopOn) press(el.dataset.key); });
  const up = () => { if (teleopOn && held.has(el.dataset.key)) release(el.dataset.key); };
  el.addEventListener('pointerup', up); el.addEventListener('pointerleave', up);
}

function setState(id, text, kind) {
  const el = $(id); el.hidden = !text; el.textContent = text || '';
  el.className = 'state' + (kind ? ' ' + kind : '');
}
// Everything a person needs to tell why the robot is not moving, in the order
// it goes wrong: keys never reaching the page, nothing forwarding them to the
// robot, or a simulation too slow to see the motion.
function updateDriveState() {
  const driver = { teleop: ['Conduces tú', 'good'], nav2: ['Conduce Nav2: tus teclas le quitan el control', ''],
                   idle: ['Nadie conduce', ''] }[sim.driver];
  setState('driverState', driver ? driver[0] : 'Sin árbitro de velocidad: ¿cmd_vel_mux_node está corriendo?', driver ? driver[1] : 'bad');
  if (!teleopOn) { setState('kbdState', ''); setState('moveState', ''); return; }
  if (!document.hasFocus()) setState('kbdState', 'El teclado está en otra ventana: haz clic en el plano', 'bad');
  else setState('kbdState', 'Teclado listo', 'good');
  let move = '';
  if (held.size && driveStart && Date.now() - driveStart > 2500) {
    if (sim.driver && sim.driver !== 'teleop') move = 'Las teclas no llegan al robot: el árbitro no ve comandos manuales';
    else if (robot && driveStartPose && Math.hypot(robot.x - driveStartPose.x, robot.y - driveStartPose.y) < 0.03
             && Math.abs((robot.yaw || 0) - (driveStartPose.yaw || 0)) < 0.05) {
      move = sim.rtf != null && sim.rtf < 0.35
        ? `La simulación va a ×${sim.rtf.toFixed(2)}: el robot se mueve, pero muy despacio`
        : 'El robot no avanza: puede estar contra un obstáculo';
    }
  }
  setState('moveState', move, move ? 'bad' : '');
}
setInterval(updateDriveState, 500);

// ===================== memory =====================
const COLLECTIONS = [['semantic_map', 'Lugares'], ['knowledge_base', 'Conocimiento'], ['task_history', 'Tareas']];
const ROOM_ES = { kitchen: 'cocina', living_room: 'salón', dining_room: 'comedor', bedroom: 'dormitorio', bathroom: 'baño',
                  office: 'despacho', hallway: 'pasillo', entrance: 'entrada', garage: 'garaje', laundry: 'lavadero',
                  storage: 'trastero', area: 'zona observada', zone: 'zona' };
let memCollection = 'semantic_map', memEntries = [], memStats = {}, memSelected = null, memError = '';

async function loadMemory() {
  const q = $('memQuery').value.trim();
  const url = `/api/memory?collection=${encodeURIComponent(memCollection)}&q=${encodeURIComponent(q)}&active_only=${$('memActiveOnly').checked}&limit=60`;
  try {
    const data = await (await fetch(url)).json();
    memEntries = data.entries || []; memStats = data.stats || {};
    memError = data.ok ? '' : (data.error || 'la memoria no respondió');
  } catch (e) { memEntries = []; memError = 'no se pudo consultar la memoria'; }
  const total = Object.values(memStats).reduce((a, b) => a + b, 0);
  $('memcount').textContent = total || '—';
  renderMemTabs(); renderMemList();
}
function renderMemTabs() {
  const el = $('memTabs'); el.innerHTML = '';
  for (const [name, label] of COLLECTIONS) {
    const tab = document.createElement('button');
    tab.className = 'tab'; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', String(name === memCollection));
    tab.innerHTML = `${label}<b>${memStats[name] ?? 0}</b>`;
    tab.onclick = () => { memCollection = name; memSelected = null; loadMemory(); };
    el.appendChild(tab);
  }
}
function memoryKind(entry) {
  const id = entry.id || '';
  if (id.startsWith('zone-')) return 'habitación nombrada';
  if (id.startsWith('landmark-')) return 'lugar con nombre';
  if (id.startsWith('scene-seed-')) return 'descripción sembrada';
  if (id.startsWith('scene-')) return 'lo que vio';
  if (id.startsWith('task-')) return 'tarea';
  return entry.source ? 'documento' : '';
}
function noteTitle(entry) {
  if (entry.zone) return [entry.zone, memoryKind(entry)];
  if (entry.label && entry.label !== 'area' && entry.label !== 'zone') return [ROOM_ES[entry.label] || entry.label, memoryKind(entry)];
  if (entry.x != null) return [`${entry.x.toFixed(1)}, ${entry.y.toFixed(1)}`, memoryKind(entry)];
  return [entry.source || entry.title, memoryKind(entry)];
}
// "kitchen at (x=3.55, y=-2.80) in cocina: …" — the title and coordinates already
// say that; show the part that says something new.
function memoryText(text) { return text.replace(/^[^:]*? at \(x=-?[\d.]+, y=-?[\d.]+\) in [^:]+:\s*/, ''); }
function renderMemList() {
  const el = $('memList'); el.innerHTML = '';
  if (memError) { el.innerHTML = `<div class="muted-note">${esc(memError)}</div>`; return; }
  if (!memEntries.length) {
    el.innerHTML = '<div class="muted-note">Todavía no hay nada aquí. Cuando el robot explore, perciba o nombres una habitación, aparecerá.</div>';
    return;
  }
  for (const entry of memEntries) {
    const note = document.createElement('article');
    note.className = 'note' + (entry.stale ? ' stale' : '') + (entry.id === memSelected ? ' sel' : '');
    const [title, sub] = noteTitle(entry);
    const meta = [];
    if (entry.x != null) meta.push(`${entry.x.toFixed(2)}, ${entry.y.toFixed(2)}`);
    if (entry.observations > 1) meta.push(`visto ${entry.observations} veces`);
    if (entry.count > 1) meta.push(`${entry.count} recuerdos del mismo sitio`);
    if (entry.stale) meta.push('<span class="warn">de otro mapa</span>');
    const distinct = (entry.facts || []).filter((f, i, all) => all.findIndex(g => g.document === f.document) === i);
    const doc = distinct.length > 1
      ? distinct.map(f => `<div class="fact"><b>${esc(ROOM_ES[f.title] || f.title)}</b> ${esc(memoryText(f.document))}</div>`).join('')
      : esc(memoryText(entry.document));
    note.innerHTML =
      (entry.score >= 0 ? `<div class="bar" style="width:${Math.round(entry.score * 100)}%" title="Parecido con la búsqueda: ${entry.score.toFixed(2)}"></div>` : '') +
      `<h3>${esc(title)}${sub ? `<small>${esc(sub)}</small>` : ''}</h3>` +
      `<div class="meta">${meta.map(m => m.startsWith('<span') ? m : `<span>${esc(m)}</span>`).join('')}</div>` +
      `<div class="doc">${doc}</div>`;
    const acts = document.createElement('div'); acts.className = 'acts';
    if (entry.x != null && !entry.stale) {
      acts.innerHTML += `<button data-a="see"><svg class="icon"><use href="#i-locate"/></svg>En el plano</button>` +
                        `<button data-a="go"><svg class="icon"><use href="#i-go"/></svg>Ir</button>`;
    }
    if (memCollection === 'semantic_map') {
      acts.innerHTML += `<button class="del" data-a="del" aria-label="Borrar este recuerdo" title="Borrar"><svg class="icon"><use href="#i-trash"/></svg></button>`;
    }
    acts.addEventListener('click', async (e) => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.dataset.a === 'see') { memSelected = entry.id; renderMemList(); }
      if (b.dataset.a === 'go') sendGoal(`Navega a las coordenadas x=${entry.x.toFixed(2)}, y=${entry.y.toFixed(2)}`);
      if (b.dataset.a === 'del') {
        const n = (entry.ids || [entry.id]).length;
        if (!confirm(n > 1 ? `¿Borrar estos ${n} recuerdos del mismo sitio?` : '¿Borrar este recuerdo?')) return;
        const data = await (await post('/api/memory/delete', { collection: memCollection, ids: entry.ids || [entry.id] })).json();
        toast(data.ok ? `Borrado${data.deleted > 1 ? `s ${data.deleted} recuerdos` : ''}.` : `No se pudo borrar: ${esc(data.error)}`);
        loadMemory();
      }
    });
    if (acts.children.length) note.appendChild(acts);
    note.querySelector('.doc').addEventListener('click', (e) => e.currentTarget.classList.toggle('open'));
    el.appendChild(note);
  }
}

async function loadRoomNames() {
  try {
    const data = await (await fetch('/api/room-types')).json();
    $('roomNames').innerHTML = (data.names || []).map(n => `<option value="${esc(n)}">`).join('');
  } catch (e) {}
}
// Browsing the memory embeds nothing, so a slow refresh costs nothing; it pauses
// while a question is typed so it cannot replace the answer on screen.
setInterval(() => { if (!$('memQuery').value.trim()) loadMemory(); }, 20000);

canvas.tabIndex = 0;
pollEvents(); pollMap(); loadRoomNames(); loadMemory(); draw();
</script>
</body>
</html>
"""
