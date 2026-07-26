// Tab Capturas: panel izquierdo (tabla + controles) y estado compartido.
// Porta la ventana Capturas de field_review_app.py (`_build_ui` :480):
// splitter horizontal, tabla de 6 columnas, orden, filtros, "Marca actual",
// navegación, overlays. El panel derecho (los dos gráficos) vive en
// capturas_signal.js y se maneja por el objeto `viewer`.
//
// Escribe con POST /api/pick, en el mismo archivo y formato que la app de
// escritorio, para que las dos puedan convivir (PORT_PLAN §0.2).
import { mountViewer } from './capturas_signal.js';
import { mountCampaigns } from '../campaigns_panel.js';
import { actionFor, load as loadKeymap, mountKeymapEditor } from '../keymap.js';

const ORDER_P2P = 'Pico a pico (mayor primero)';
const ORDER_ORIGINAL = 'Carpeta / captura (original)';
const FILTER_ALL = 'Todas';
const FILTER_UNREVIEWED = 'Sin revision';
const FILTER_DISTANCE = 'Marcadas con N metros';

const fmt = (n, d = 1) => (n === null || n === undefined) ? '—' : Number(n).toFixed(d);

// El estado que la app guarda en la sesión: acá va a localStorage, así al
// recargar seguís donde quedaste (misma promesa que el cartel de la app).
const STORE_KEY = 'geo-capturas-ui';

function loadUi() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch (_) { return {}; }
}
function saveUi(ui) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(ui)); } catch (_) { /* modo privado */ }
}

export function mount(root) {
  const saved = loadUi();
  const state = {
    rows: [],
    p2p: null,               // null = todavía no llegó
    p2pLoading: false,
    order: saved.order || ORDER_P2P,
    filter: saved.filter || FILTER_ALL,
    filterDistance: Number(saved.filterDistance) || 0,
    currentKey: saved.currentKey || null,
    kind: saved.kind || 'raw',
    overlaySame: saved.overlaySame !== false,
    overlayMax: Number(saved.overlayMax) || 12,
    folderAvg: saved.folderAvg !== false,
    loaded: false,
    summary: { total: 0, duplicate_folder_count: 0, reviewed_count: 0 },
  };

  root.innerHTML = `
    <div class="workspace">
      <div class="ws-left">
        <details class="card camp-panel" id="camp-panel"></details>

        <div class="card pane-list">
          <div id="cap-summary" class="summary">cargando…</div>
          <div class="wrap table-scroll">
            <table class="rows-table"><thead><tr>
              <th>Estado</th><th class="num">Dist</th><th class="num">Trigger s</th>
              <th>Campaña</th><th>Carpeta</th><th>Captura</th>
              <th class="geo-col" hidden>Geófono</th><th>Hash</th>
            </tr></thead><tbody id="cap-rows"></tbody></table>
          </div>

          <div class="field-row">
            <label for="cap-order">Orden</label>
            <select id="cap-order" title="Pico a pico: empieza por la señal donde el golpe se ve más fácil, para calibrar el resto contra esa. Carpeta/captura: orden original de adquisición.">
              <option>${ORDER_P2P}</option>
              <option>${ORDER_ORIGINAL}</option>
            </select>
          </div>

          <div class="field-row">
            <label for="cap-filter">Filtro</label>
            <select id="cap-filter">
              <option>${FILTER_ALL}</option>
              <option>${FILTER_UNREVIEWED}</option>
              <option>${FILTER_DISTANCE}</option>
            </select>
            <input type="number" id="cap-filter-dist" step="1" value="0" class="num-input"
                   aria-label="Distancia del filtro, en metros">
            <button type="button" id="cap-filter-current" class="btn-quiet btn-sm">N = actual</button>
            <button type="button" id="cap-same-label" class="btn-quiet btn-sm">Mostrar mismo label</button>
          </div>
        </div>

        <div class="card pane-mark">
          <div class="card-head">
            <h2>Marca actual</h2>
            <span class="count" id="cap-position">-</span>
          </div>

          <div class="mark-grid">
            <label for="cap-distance">Distancia m</label>
            <input type="number" id="cap-distance" step="1" class="num-input">
            <label for="cap-trigger-v">Trigger hammer</label>
            <span id="cap-trigger" class="mono">-</span>
            <label class="control-check span-2"><input type="checkbox" id="cap-accept">
              Usar esta muestra</label>
            <label for="cap-notes">Notas</label>
            <input type="text" id="cap-notes" placeholder="nota opcional" class="span-3">
          </div>

          <div class="nav-grid">
            <button type="button" id="cap-prev">Anterior</button>
            <button type="button" id="cap-next">Siguiente</button>
            <button type="button" id="cap-auto">Auto</button>
            <button type="button" id="cap-zone">Marcar zona auto</button>
            <button type="button" id="cap-zone-clear">Limpiar zona</button>
            <button type="button" id="cap-flip-single">Invertir esta señal</button>
            <button type="button" id="cap-save-next">Guardar y siguiente</button>
            <button type="button" id="cap-apply-folder">Aplicar dist. a carpeta</button>
            <button type="button" id="cap-flip-folder">Invertir geo de carpeta</button>
          </div>

          <div class="field-row">
            <label class="control-check"><input type="checkbox" id="cap-overlay-same">
              Mismo label</label>
            <label class="control-check">máx
              <input type="number" id="cap-overlay-max" min="1" max="50" class="num-input num-input-sm"></label>
            <label class="control-check"
                   title="Superpone el promedio (alineado por trigger) de las señales YA validadas de la MISMA CARPETA, sin contar la actual: referencia para dejar bien el trigger de cada señal.">
              <input type="checkbox" id="cap-folder-avg"> Promedio carpeta</label>
          </div>

          <details class="foot-details">
            <summary>Teclas y notas</summary>
            <p class="note">Click en una tecla y apretá la nueva. El trigger se mueve
              arrastrando la línea naranja.</p>
            <div id="cap-keymap"></div>
            <p class="note">Todo se guarda solo, en el mismo archivo que la app de escritorio
              (<code>field_review_annotations.json</code> de la campaña). El trigger es de la
              <strong>captura</strong>: moverlo lo mueve para todos sus geófonos. La polaridad
              (<code>geo_flip</code>) es de cada geófono.</p>
          </details>

          <details class="foot-details">
            <summary>Subidas</summary>
            <div class="wrap"><table id="jobs"><thead><tr>
              <th>Cuándo</th><th>Archivo</th><th class="num">kB</th><th>Estado</th>
              <th>Carpeta</th><th class="num">Disparos</th><th class="num">Picks</th><th>Detalle</th>
            </tr></thead><tbody></tbody></table></div>
            <div id="jobs-empty" class="empty" hidden>Todavía no llegó ninguna captura.
              Desde la SPA del maestro: pestaña Captura → <code>Subir al server</code>.</div>
          </details>
        </div>
      </div>

      <div class="ws-right">
        <section class="card viewer" id="viewer-host"></section>
      </div>
    </div>
  `;

  const $ = (sel) => root.querySelector(sel);
  const elRows = $('#cap-rows');
  const elSummary = $('#cap-summary');
  const elOrder = $('#cap-order');
  const elFilter = $('#cap-filter');
  const elFilterDist = $('#cap-filter-dist');

  const SRC_LABEL = {
    annotation: 'guardado', auto: 'auto', override: 'sin guardar', none: 'sin trigger',
  };
  const campaignsPanel = mountCampaigns($('#camp-panel'), {
    // Cambiar qué campañas se usan cambia la tabla entera: el escaneo del
    // servidor ya está cacheado, así que volver a pedir es barato.
    onChange: () => { state.p2p = null; tick(); },
  });

  let keymap = loadKeymap();
  const keymapEditor = mountKeymapEditor($('#cap-keymap'), {
    onChange: (m) => { keymap = m; },
  });

  const viewer = mountViewer($('#viewer-host'), {
    // Soltar el trigger guarda, como el autoguardado de la app.
    onTriggerCommit: (t) => savePick({ trigger_s: t }),
    onFlipCommit: (flip) => savePick({ geo_flip: flip }),
    onTriggerPreview: (t, source) => {
      const tag = SRC_LABEL[source] || source || '';
      $('#cap-trigger').textContent = source === 'none' ? '—' : `${t.toFixed(4)} s (${tag})`;
    },
    getOverlayOpts: () => ({
      same_label: state.overlaySame ? 1 : 0,
      max_count: state.overlayMax,
      folder_average: state.folderAvg ? 1 : 0,
      kind: state.kind,
    }),
  });

  // ── Derivados ────────────────────────────────────────────────────────────
  function matchesFilter(row) {
    if (state.filter === FILTER_UNREVIEWED) return !row.reviewed;
    if (state.filter === FILTER_DISTANCE) {
      if (row.distance_m === null || row.distance_m === undefined) return false;
      return Math.abs(row.distance_m - state.filterDistance) <= 0.0005;
    }
    return true;
  }

  function visibleRows() {
    const rows = state.rows.filter(matchesFilter);
    if (state.order === ORDER_ORIGINAL || !state.p2p) {
      return rows.slice().sort((a, b) =>
        a.folder.localeCompare(b.folder) || a.order - b.order || a.capture.localeCompare(b.capture));
    }
    // `-p2p` como en _compute_row_order (:876): mayor primero, empate por orden original.
    return rows.slice().sort((a, b) =>
      (state.p2p[b.key] || 0) - (state.p2p[a.key] || 0) ||
      a.folder.localeCompare(b.folder) || a.order - b.order);
  }

  const currentRow = () => state.rows.find((r) => r.key === state.currentKey) || null;

  function persist() {
    saveUi({
      order: state.order, filter: state.filter, filterDistance: state.filterDistance,
      currentKey: state.currentKey, kind: state.kind, overlaySame: state.overlaySame,
      overlayMax: state.overlayMax, folderAvg: state.folderAvg,
    });
  }

  // ── Render ───────────────────────────────────────────────────────────────
  const ESTADO_CLASS = {
    'OK': 'estado-ok',
    'Rechazada': 'estado-rechazada',
    'Sin validar': 'estado-sinvalidar',
  };

  function renderTable() {
    const rows = visibleRows();
    // La columna Geófono sólo aparece si en algún lado hay más de uno: con un
    // solo receptor por captura no dice nada.
    const multiGeo = state.rows.some((r) => (r.geo_count || 0) > 1);
    for (const th of root.querySelectorAll('th.geo-col')) th.hidden = !multiGeo;
    const frag = document.createDocumentFragment();
    for (const row of rows) {
      const tr = document.createElement('tr');
      tr.dataset.key = row.key;
      if (row.key === state.currentKey) tr.className = 'is-selected';
      if (!row.plottable) tr.classList.add('is-mute');
      const cls = ESTADO_CLASS[row.estado] || 'estado-otro';
      tr.innerHTML =
        `<td><span class="estado ${cls}">${row.estado}</span></td>` +
        `<td class="num">${row.distance_m === null ? '—' : fmt(row.distance_m, 3)}</td>` +
        `<td class="num">${row.trigger_s === null ? '—' : fmt(row.trigger_s, 4)}</td>` +
        `<td class="ell" title="${row.campaign_name || ''}">${row.campaign_name || '—'}</td>` +
        `<td class="ell" title="${row.folder}">${row.folder}</td>` +
        `<td class="ell">${row.capture}</td>` +
        `<td class="geo-col mono"${multiGeo ? '' : ' hidden'}>${row.geo_label || '—'}</td>` +
        `<td class="mono hash">${row.hash || '—'}</td>`;
      frag.appendChild(tr);
    }
    elRows.replaceChildren(frag);

    if (!state.loaded) {
      elSummary.innerHTML = '<span class="loading">escaneando el volumen… ' +
        '(la primera vez tarda: hay que recorrer todas las capturas)</span>';
      return;
    }
    const p2pNote = (state.order === ORDER_P2P && !state.p2p)
      ? ' · <span class="loading">calculando pico a pico…</span>' : '';
    elSummary.innerHTML =
      `${rows.length}/${state.summary.total} visibles · ` +
      `${state.summary.duplicate_folder_count} carpetas duplicadas ignoradas · ` +
      `${state.summary.reviewed_count} validadas · señal ${state.kind === 'filt' ? 'filtrada' : 'cruda'}` +
      p2pNote;
  }

  function renderCurrent() {
    const row = currentRow();
    const rows = visibleRows();
    const pos = row ? rows.findIndex((r) => r.key === row.key) : -1;
    $('#cap-position').textContent = row ? `${pos + 1} / ${rows.length}` : '-';
    $('#cap-distance').value = row && row.distance_m !== null ? row.distance_m : '';
    $('#cap-accept').checked = row ? !!row.accepted : false;
    $('#cap-notes').value = row ? (row.notes || '') : '';
    // Sin disparo no hay trigger que picar: los controles que dependen de eso
    // se apagan, pero la captura se sigue pudiendo mirar.
    const hasShot = !!(row && row.shot_id);
    $('#cap-auto').disabled = !hasShot;
    $('#cap-zone').disabled = !hasShot;
    $('#cap-flip-single').disabled = !(row && row.has_geo);
  }

  function select(key, { scroll = false } = {}) {
    const row = state.rows.find((r) => r.key === key);
    if (!row) return;
    state.currentKey = key;
    persist();
    for (const tr of elRows.querySelectorAll('tr')) {
      tr.classList.toggle('is-selected', tr.dataset.key === key);
      if (scroll && tr.dataset.key === key) tr.scrollIntoView({ block: 'nearest' });
    }
    renderCurrent();
    viewer.show(row, { kind: state.kind });
  }

  function move(delta) {
    const rows = visibleRows();
    if (!rows.length) return;
    const pos = rows.findIndex((r) => r.key === state.currentKey);
    const next = pos < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, pos + delta));
    select(rows[next].key, { scroll: true });
  }

  // ── Datos ────────────────────────────────────────────────────────────────
  async function loadP2p() {
    if (state.p2p || state.p2pLoading) return;
    state.p2pLoading = true;
    renderTable();
    try {
      const r = await fetch('/api/captures/p2p', { cache: 'no-store' }).then((x) => x.json());
      state.p2p = r.p2p || {};
    } catch (_) {
      state.p2p = {};       // que no se quede colgado: cae al orden original
    } finally {
      state.p2pLoading = false;
      renderTable();
    }
  }

  async function tick() {
    try {
      const [jobs, caps] = await Promise.all([
        fetch('/api/jobs', { cache: 'no-store' }).then((r) => r.json()),
        fetch('/api/captures', { cache: 'no-store' }).then((r) => r.json()),
      ]);
      renderJobs(root, jobs.jobs || []);
      state.rows = caps.rows || [];
      state.loaded = true;
      state.summary = {
        total: caps.total || 0,
        duplicate_folder_count: caps.duplicate_folder_count || 0,
        reviewed_count: caps.reviewed_count || 0,
      };
      const rows = visibleRows();
      if (!currentRow() && rows.length) state.currentKey = rows[0].key;
      renderTable();
      renderCurrent();
      if (!viewer.hasShot() && currentRow()) viewer.show(currentRow(), { kind: state.kind });
    } catch (e) {
      // Sin conexión: no borrar lo que ya se mostraba, esperar el próximo tick.
    }
  }

  // ── Eventos ──────────────────────────────────────────────────────────────
  elRows.addEventListener('click', (ev) => {
    const tr = ev.target.closest('tr[data-key]');
    if (tr) select(tr.dataset.key);
  });

  elOrder.value = state.order;
  elOrder.addEventListener('change', () => {
    state.order = elOrder.value;
    persist();
    if (state.order === ORDER_P2P) loadP2p();
    renderTable();
    renderCurrent();
  });

  elFilter.value = state.filter;
  elFilterDist.value = state.filterDistance;
  const onFilterChange = () => {
    state.filter = elFilter.value;
    state.filterDistance = Number(elFilterDist.value) || 0;
    persist();
    renderTable();
    renderCurrent();
  };
  elFilter.addEventListener('change', onFilterChange);
  elFilterDist.addEventListener('input', onFilterChange);

  $('#cap-filter-current').addEventListener('click', () => {
    const row = currentRow();
    if (!row || row.distance_m === null) return;
    elFilterDist.value = row.distance_m;
    elFilter.value = FILTER_DISTANCE;
    onFilterChange();
  });

  $('#cap-same-label').addEventListener('click', () => {
    const row = currentRow();
    if (!row || row.distance_m === null) return;
    state.overlaySame = true;
    $('#cap-overlay-same').checked = true;
    elFilterDist.value = row.distance_m;
    elFilter.value = FILTER_DISTANCE;
    onFilterChange();
    viewer.refreshOverlays();
  });

  // ── Escritura de anotaciones ─────────────────────────────────────────────
  // Se guarda solo, como la app: no hay botón "guardar" para cada campo.
  async function savePick(patch, { avanzar = false } = {}) {
    const row = currentRow();
    if (!row || !row.shot_id) {
      viewer.setStatus('esta captura no es un disparo: no hay marca que guardar');
      return null;
    }
    try {
      const res = await fetch('/api/pick', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shot_id: row.shot_id, campaign: row.campaign || '', ...patch }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const out = await res.json();
      // Actualizar la fila en memoria para que la tabla no parpadee al valor
      // viejo hasta el próximo tick.
      Object.assign(row, {
        trigger_s: out.annotation.trigger_s,
        distance_m: out.annotation.distance_m,
        accepted: out.annotation.accepted,
        reviewed: out.annotation.reviewed,
        geo_flip: out.annotation.geo_flip,
        notes: out.annotation.notes,
        estado: out.annotation.reviewed ? (out.annotation.accepted ? 'OK' : 'Rechazada') : 'Sin validar',
      });
      viewer.setStatus(out.touched > 1 ? `guardado (${out.touched} disparos)` : 'guardado');
      renderTable();
      renderCurrent();
      if (avanzar) move(1);
      tick();
      return out;
    } catch (err) {
      viewer.setStatus(`no se pudo guardar: ${err}`);
      return null;
    }
  }

  // Rota sin validar -> OK -> rechazada, como `_cycle_estado` (:1572).
  function cycleEstado() {
    const row = currentRow();
    if (!row) return;
    const idx = !row.reviewed ? 0 : (row.accepted ? 1 : 2);
    const estados = [[false, true], [true, true], [true, false]];
    const [reviewed, accepted] = estados[(idx + 1) % 3];
    savePick({ reviewed, accepted });
  }

  $('#cap-distance').addEventListener('change', (ev) => {
    const v = ev.target.value;
    if (v !== '') savePick({ distance_m: Number(v) });
  });
  $('#cap-accept').addEventListener('change', (ev) =>
    savePick({ accepted: ev.target.checked, reviewed: true }));
  $('#cap-notes').addEventListener('change', (ev) => savePick({ notes: ev.target.value }));
  $('#cap-save-next').addEventListener('click', () =>
    savePick({ reviewed: true }, { avanzar: true }));
  $('#cap-apply-folder').addEventListener('click', () => {
    const row = currentRow();
    if (!row) return;
    if (!confirm(`¿Poner ${fmt(row.distance_m, 3)} m en TODA la carpeta "${row.folder}"?`)) return;
    savePick({ distance_m: row.distance_m, apply_distance_to_folder: true });
  });
  $('#cap-flip-folder').addEventListener('click', () => {
    const row = currentRow();
    if (!row) return;
    if (!confirm(`¿Invertir el geófono de TODA la carpeta "${row.folder}"?`)) return;
    savePick({ geo_flip: !row.geo_flip, flip_folder: true });
  });

  $('#cap-prev').addEventListener('click', () => move(-1));
  $('#cap-next').addEventListener('click', () => move(1));
  $('#cap-auto').addEventListener('click', () => viewer.reautoPick());
  $('#cap-zone').addEventListener('click', () => viewer.startZoneMarking());
  $('#cap-zone-clear').addEventListener('click', () => viewer.clearZone());
  $('#cap-flip-single').addEventListener('click', () => viewer.toggleFlip());   // guarda al aplicar

  const elOverlaySame = $('#cap-overlay-same');
  const elOverlayMax = $('#cap-overlay-max');
  const elFolderAvg = $('#cap-folder-avg');
  elOverlaySame.checked = state.overlaySame;
  elOverlayMax.value = state.overlayMax;
  elFolderAvg.checked = state.folderAvg;
  const onOverlayChange = () => {
    state.overlaySame = elOverlaySame.checked;
    state.overlayMax = Math.max(1, Math.min(50, Number(elOverlayMax.value) || 12));
    state.folderAvg = elFolderAvg.checked;
    persist();
    viewer.refreshOverlays();
  };
  elOverlaySame.addEventListener('change', onOverlayChange);
  elOverlayMax.addEventListener('input', onOverlayChange);
  elFolderAvg.addEventListener('change', onOverlayChange);

  viewer.onKindChange((kind) => { state.kind = kind; persist(); renderTable(); });

  // Teclas: mismo esquema que `_handle_review_key` (:1534). No se roban las
  // teclas cuando el foco está en un campo de texto o número.
  const KEY_ACTIONS = {
    prev: () => move(-1),
    next: () => move(1),
    zoneAMinus: () => viewer.nudgeZone('a', -1),
    zoneAPlus: () => viewer.nudgeZone('a', 1),
    zoneBMinus: () => viewer.nudgeZone('b', -1),
    zoneBPlus: () => viewer.nudgeZone('b', 1),
    // Shift sigue funcionando como en la app, pero el geófono tiene además
    // sus propias teclas: con Shift+flecha algunos navegadores hacen scroll.
    zoomIn: (ev) => viewer.zoom(ev.shiftKey ? 'geo' : 'hammer', true),
    zoomOut: (ev) => viewer.zoom(ev.shiftKey ? 'geo' : 'hammer', false),
    zoomInGeo: () => viewer.zoom('geo', true),
    zoomOutGeo: () => viewer.zoom('geo', false),
    flip: () => viewer.toggleFlip(),
    auto: () => viewer.reautoPick(),
    cycle: () => cycleEstado(),
  };

  function onKey(ev) {
    const tag = (ev.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
    if (root.closest('.tab-panel[hidden]')) return;
    const action = actionFor(keymap, ev);
    const fn = action && KEY_ACTIONS[action];
    if (!fn) return;
    fn(ev);
    ev.preventDefault();
  }
  document.addEventListener('keydown', onKey);

  if (state.order === ORDER_P2P) loadP2p();
  tick();
  // 3 s era razonable con 210 filas; con ~950 el escaneo cuesta y no hace
  // falta: lo que cambia seguido son las anotaciones, no qué capturas hay.
  const POLL_MS = 8000;
  let timer = setInterval(tick, POLL_MS);

  // El tab no se desmonta al cambiar de pestaña (se pierde todo el estado):
  // sólo se deja de pedir datos y de escuchar el teclado mientras no se ve.
  return {
    pause() {
      clearInterval(timer);
      timer = null;
      document.removeEventListener('keydown', onKey);
    },
    destroy() {
      clearInterval(timer);
      document.removeEventListener('keydown', onKey);
      keymapEditor.destroy();
      viewer.destroy();
    },
    resume() {
      if (!timer) timer = setInterval(tick, POLL_MS);
      document.addEventListener('keydown', onKey);
      tick();
      // Volver a una pestaña oculta: los canvas quedaron con tamaño 0.
      viewer.redraw();
    },
  };
}

function renderJobs(root, rows) {
  const tb = root.querySelector('#jobs tbody');
  root.querySelector('#jobs-empty').hidden = rows.length > 0;
  tb.innerHTML = '';
  for (const j of rows) {
    const tr = document.createElement('tr');
    const detalle = j.error ? j.error.split('\n').slice(-1)[0]
                            : (j.log && j.log.length ? j.log[j.log.length - 1] : '');
    tr.innerHTML =
      `<td class="mono">${(j.created_at || '').replace('T', ' ').replace('+00:00', '')}</td>` +
      `<td>${j.filename}</td>` +
      `<td class="num">${(j.bytes / 1024).toFixed(0)}</td>` +
      `<td><span class="tag ${j.state}">${j.state}</span></td>` +
      `<td>${j.folder || '—'}</td>` +
      `<td class="num">${j.shots}</td>` +
      `<td class="num">${j.picks}</td>` +
      `<td class="detail">${detalle}</td>`;
    tb.appendChild(tr);
  }
}
