// Tab Enfase (§3.2): alinear entre sí las carpetas de un mismo label.
// Porta `AlignmentPanel` de field_review_app.py (:2259).
//
// Se trabaja POR CARPETA, no por señal: el gráfico muestra el promedio de cada
// carpeta y el offset la mueve entera, rígida. Las carpetas van ordenadas por
// pico a pico de su promedio, mayor primero; esa primera define el cero.
import { createFrame, drawMinMax, attachViewControls } from '../plot.js';
import { mountCampaignPicker } from '../campaign_picker.js';

const fmt = (n, d = 2) => (n === null || n === undefined) ? '—' : Number(n).toFixed(d);

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// Un color por carpeta, estable: la de referencia tiene que verse siempre igual.
const PALETA = ['#69b7ff', '#ffb86b', '#3ddc84', '#ff6b6b', '#c792ea',
                '#f0c26b', '#5fd3c4', '#ff9ec7', '#9aa5b4', '#8fd14f'];

export function mount(root) {
  root.innerHTML = `
    <div class="workspace">
      <div class="ws-left">
        <div class="card" id="en-campaign"></div>

        <div class="card">
          <div class="field-row">
            <label for="en-group">Grupo</label>
            <select id="en-group"></select>
          </div>
          <div class="field-row">
            <label for="en-label">Label</label>
            <select id="en-label"></select>
          </div>
          <p class="note" id="en-current">-</p>

          <div class="toolbar">
            <button type="button" id="en-prev">Anterior</button>
            <button type="button" id="en-next">Siguiente</button>
          </div>

          <h2 style="margin-top:12px">Offset de esta carpeta</h2>
          <p class="note">Mueve todas sus señales a la vez.</p>
          <div class="field-row">
            <label for="en-offset">Offset</label>
            <input type="number" id="en-offset" step="0.5" class="num-input"> <span class="note">ms</span>
          </div>

          <div class="toolbar">
            <button type="button" id="en-ok">OK alineado</button>
            <button type="button" id="en-reject" class="btn-quiet">Rechazar esta carpeta</button>
          </div>
          <div class="toolbar">
            <button type="button" id="en-reset-folder" class="btn-quiet btn-sm">Reset esta carpeta</button>
            <button type="button" id="en-reset-label" class="btn-quiet btn-sm">Reset todo el label</button>
          </div>
          <p class="note" id="en-status"></p>

          <p class="note">«Rechazar» excluye la carpeta de promedios, waterfall, MASW y del
          promedio del export, aunque sus señales sean válidas. Las muestras individuales se
          siguen exportando: es una decisión aparte de la validez.</p>
        </div>
      </div>

      <div class="ws-right">
        <section class="card viewer">
          <div class="viewer-head">
            <h2>Promedios por carpeta</h2>
            <div class="viewer-meta" id="en-meta">cargando…</div>
          </div>
          <div class="plot-stack">
            <figure class="plot-box">
              <figcaption class="plot-title">Alineación del label
                <span class="legend" id="en-legend"></span></figcaption>
              <canvas class="plot" id="en-plot"></canvas>
            </figure>
          </div>
          <div class="wrap" style="max-height:34%;overflow:auto">
            <table class="rows-table"><thead><tr>
              <th>Carpeta</th><th class="num">Señales</th><th class="num">p2p</th>
              <th class="num">Offset ms</th><th>Estado</th>
            </tr></thead><tbody id="en-rows"></tbody></table>
          </div>
        </section>
      </div>
    </div>
  `;

  const $ = (s) => root.querySelector(s);
  const elPlot = $('#en-plot');
  let data = { labels: [], folders: [], group_count: 1, group_id: 1, label: null };
  let campaign = '';
  let indice = 0;
  let cargando = false;
  let frame = null;

  // Alinear a ojo pide mirar de cerca el primer arribo: rueda para acercar,
  // arrastre para mover, doble click para volver.
  const view = attachViewControls(elPlot, {
    getFrame: () => frame, onChange: () => dibujar(),
  });

  const picker = mountCampaignPicker($('#en-campaign'), {
    onChange: (id) => { campaign = id; indice = 0; load(); },
  });

  const actual = () => data.folders[indice] || null;

  function render() {
    $('#en-group').innerHTML = Array.from({ length: data.group_count || 1 }, (_, i) =>
      `<option value="${i + 1}"${(i + 1) === data.group_id ? ' selected' : ''}>Grupo ${i + 1}</option>`).join('');
    $('#en-label').innerHTML = (data.labels || []).map((l) =>
      `<option value="${l}"${l === data.label ? ' selected' : ''}>${l}</option>`).join('');

    const cur = actual();
    // Offsets por señal viejos: tienen prioridad sobre el de carpeta y pelean
    // con este ajuste. La app avisa igual y «OK alineado» los limpia.
    const viejos = cur && cur.legacy_shot_offsets
      ? ` — ${cur.legacy_shot_offsets} offset(s) por señal viejos (OK los limpia)` : '';
    $('#en-current').textContent = cur
      ? `${indice + 1} / ${data.folders.length} · ${cur.folder}${viejos}`
      : (cargando ? 'calculando promedios…' : 'sin carpetas en este label');
    $('#en-offset').value = cur ? cur.offset_ms : 0;
    $('#en-reject').classList.toggle('is-primary', !!(cur && cur.rejected));
    $('#en-reject').textContent = cur && cur.rejected
      ? 'Rechazada (click para aceptar)' : 'Rechazar esta carpeta';

    $('#en-rows').innerHTML = data.folders.map((f, i) => `
      <tr data-i="${i}"${i === indice ? ' class="is-selected"' : ''}>
        <td class="ell" title="${f.folder}">
          <span class="ch-dot" style="background:${PALETA[i % PALETA.length]}"></span> ${f.folder}</td>
        <td class="num">${f.shots}</td>
        <td class="num">${fmt(f.p2p, 4)}</td>
        <td class="num">${fmt(f.offset_ms, 2)}</td>
        <td>${f.rejected ? '<span class="estado estado-rechazada">rechazada</span>'
                         : (f.confirmed ? '<span class="estado estado-ok">alineada</span>'
                                        : '<span class="estado estado-sinvalidar">sin alinear</span>')}</td>
      </tr>`).join('');

    $('#en-legend').innerHTML = data.folders.slice(0, 8).map((f, i) =>
      `<span class="lg" style="background:${PALETA[i % PALETA.length]}"></span>` +
      `${f.folder.slice(0, 14)}`).join('');

    dibujar();
  }

  function dibujar() {
    const trazas = data.folders.filter((f) => f.trace && !f.rejected);
    if (!trazas.length) {
      frame = createFrame(elPlot, view.apply({ xMin: -0.05, xMax: 1, yMin: -1, yMax: 1,
        xLabel: 'tiempo relativo al hammer [s]', yLabel: 'Geo promedio [V]' }));
      return;
    }
    const lo = Math.min(...trazas.map((f) => f.trace.y_min ?? 0));
    const hi = Math.max(...trazas.map((f) => f.trace.y_max ?? 0));
    const pad = Math.max((hi - lo) * 0.05, 1e-9);
    frame = createFrame(elPlot, view.apply({
      xMin: -0.05, xMax: 0.6,
      yMin: lo - pad, yMax: hi + pad,
      xLabel: 'tiempo relativo al hammer [s]', yLabel: 'Geo promedio [V]',
    }));
    data.folders.forEach((f, i) => {
      if (!f.trace || f.rejected) return;
      // El offset se aplica al dibujar: mover el número corre la traza al
      // instante, sin recalcular el promedio en el servidor.
      const t0 = (f.trace.t0 || 0) + (Number(f.offset_ms) || 0) / 1000;
      drawMinMax(frame, { ...f.trace, t0 }, {
        color: PALETA[i % PALETA.length],
        lineWidth: i === indice ? 1.6 : 1,
        alpha: i === indice ? 1 : 0.5,
      });
    });
  }

  async function load({ label = null } = {}) {
    cargando = true;
    $('#en-meta').textContent = 'calculando promedios por carpeta…';
    render();
    const q = new URLSearchParams({
      campaign, group_id: String(data.group_id || 1),
      max_points: String(Math.max(200, Math.round(elPlot.clientWidth || 800))),
    });
    if (label) q.set('label', label);
    else if (data.label) q.set('label', data.label);
    try {
      data = await fetch(`/api/alignment?${q}`, { cache: 'no-store' }).then((r) => r.json());
      indice = Math.min(indice, Math.max(0, data.folders.length - 1));
      $('#en-meta').textContent =
        `${data.folders.length} carpetas · label ${data.label || '—'} · grupo ${data.group_id}`;
    } catch (err) {
      $('#en-meta').textContent = `no se pudo cargar: ${err}`;
    } finally {
      cargando = false;
      render();
    }
  }

  async function accion(body) {
    const cur = actual();
    if (!cur && body.action !== 'reset_label') return;
    $('#en-status').textContent = 'guardando…';
    try {
      const res = await fetch('/api/alignment', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          campaign, group_id: data.group_id, label: data.label,
          folder: cur ? cur.folder : '', max_points: 1200, ...body,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      indice = Math.min(indice, Math.max(0, data.folders.length - 1));
      $('#en-status').textContent = data.legacy_cleared
        ? `guardado · ${data.legacy_cleared} offset(s) por señal viejos limpiados`
        : 'guardado';
      render();
    } catch (err) {
      $('#en-status').textContent = `no se pudo guardar: ${err}`;
    }
  }

  $('#en-group').addEventListener('change', (ev) => {
    data.group_id = Number(ev.target.value) || 1;
    data.label = null;
    indice = 0;
    load();
  });
  $('#en-label').addEventListener('change', (ev) => { indice = 0; load({ label: ev.target.value }); });
  $('#en-prev').addEventListener('click', () => { indice = Math.max(0, indice - 1); render(); });
  $('#en-next').addEventListener('click', () => {
    indice = Math.min(data.folders.length - 1, indice + 1); render();
  });
  $('#en-rows').addEventListener('click', (ev) => {
    const tr = ev.target.closest('tr[data-i]');
    if (tr) { indice = Number(tr.dataset.i); render(); }
  });

  // Mover el número redibuja al instante; se guarda al soltar el campo.
  $('#en-offset').addEventListener('input', (ev) => {
    const cur = actual();
    if (cur) { cur.offset_ms = Number(ev.target.value) || 0; dibujar(); }
  });
  $('#en-offset').addEventListener('change', (ev) =>
    accion({ action: 'offset', offset_ms: Number(ev.target.value) || 0 }));

  $('#en-ok').addEventListener('click', async () => {
    await accion({ action: 'offset', offset_ms: Number($('#en-offset').value) || 0 });
    indice = Math.min(data.folders.length - 1, indice + 1);
    render();
  });
  $('#en-reject').addEventListener('click', () => {
    const cur = actual();
    if (cur) accion({ action: 'reject', rejected: !cur.rejected });
  });
  $('#en-reset-folder').addEventListener('click', () => accion({ action: 'reset_folder' }));
  $('#en-reset-label').addEventListener('click', () => {
    if (confirm(`¿Borrar los offsets de TODO el label ${data.label}?`)) {
      accion({ action: 'reset_label' });
    }
  });

  const ro = new ResizeObserver(() => dibujar());
  ro.observe(elPlot);

  return {
    resume() { picker.reload(); load(); },
    destroy() { view.destroy(); ro.disconnect(); },
  };
}
