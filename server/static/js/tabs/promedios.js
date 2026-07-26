// Tab Promedios / arrivals (§3.3). Porta `AverageReviewPanel` (:2842).
//
// El promedio de cada distancia ya viene con TODO lo decidido antes aplicado:
// sólo capturas validadas, sin las carpetas rechazadas en Enfase, corridas por
// su offset y filtradas si el filtro está activo. Acá se marca el primer arribo
// del promedio, que es lo que después alimenta la curva tiempo-distancia.
import { createFrame, drawMinMax, drawVLine } from '../plot.js';
import { mountCampaignPicker } from '../campaign_picker.js';

const fmt = (n, d = 4) => (n === null || n === undefined) ? '—' : Number(n).toFixed(d);

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export function mount(root) {
  root.innerHTML = `
    <div class="workspace">
      <div class="ws-left">
        <div class="card" id="pr-campaign"></div>

        <div class="card pane-list">
          <div id="pr-summary" class="summary">cargando…</div>
          <div class="wrap table-scroll">
            <table class="rows-table"><thead><tr>
              <th>Label</th><th class="num">Dist</th><th class="num">n</th>
              <th class="num">Arribo s</th><th>Estado</th>
            </tr></thead><tbody id="pr-rows"></tbody></table>
          </div>
          <p class="note">Click en el gráfico para poner el arribo; después «Validar».</p>
        </div>

        <div class="card">
          <h2>Arribo del promedio</h2>
          <div class="mark-grid">
            <label for="pr-arrival">Arribo</label>
            <input type="number" id="pr-arrival" step="0.001" class="num-input">
            <label>Distancia</label>
            <span id="pr-dist" class="mono">—</span>
            <label for="pr-notes">Notas</label>
            <input type="text" id="pr-notes" placeholder="nota opcional" class="span-3">
          </div>
          <div class="toolbar">
            <button type="button" id="pr-save">Validar y siguiente</button>
            <button type="button" id="pr-unreview" class="btn-quiet">Desmarcar</button>
          </div>
          <p class="note" id="pr-status"></p>
        </div>
      </div>

      <div class="ws-right">
        <section class="card viewer">
          <div class="viewer-head">
            <h2>Promedio</h2>
            <div class="viewer-meta" id="pr-meta">cargando…</div>
          </div>
          <div class="plot-stack">
            <figure class="plot-box">
              <figcaption class="plot-title"><span class="ch-dot ch-hammer"></span>Hammer promedio</figcaption>
              <canvas class="plot" id="pr-hammer"></canvas>
            </figure>
            <figure class="plot-box">
              <figcaption class="plot-title"><span class="ch-dot ch-geo"></span>Geófono promedio</figcaption>
              <canvas class="plot" id="pr-geo"></canvas>
            </figure>
          </div>
        </section>
      </div>
    </div>
  `;

  const $ = (s) => root.querySelector(s);
  const elGeo = $('#pr-geo');
  const elHam = $('#pr-hammer');
  let data = { groups: [], hammer_global: null };
  let campaign = '';
  let indice = 0;
  let frameGeo = null;

  const picker = mountCampaignPicker($('#pr-campaign'), {
    onChange: (id) => { campaign = id; indice = 0; load(); },
  });

  const actual = () => data.groups[indice] || null;

  function render() {
    const g = actual();
    $('#pr-summary').textContent =
      `${data.groups.length} distancias · ` +
      `${data.groups.filter((x) => x.reviewed).length} validadas` +
      (data.filter_enabled ? ' · filtro ACTIVO' : ' · sin filtrar');

    $('#pr-rows').innerHTML = data.groups.map((x, i) => `
      <tr data-i="${i}"${i === indice ? ' class="is-selected"' : ''}>
        <td>${x.label}</td>
        <td class="num">${fmt(x.distance_m, 1)}</td>
        <td class="num">${x.n}</td>
        <td class="num">${x.arrival_s === null ? '—' : fmt(x.arrival_s)}</td>
        <td>${x.reviewed ? '<span class="estado estado-ok">validado</span>'
                         : '<span class="estado estado-sinvalidar">sin validar</span>'}</td>
      </tr>`).join('');

    $('#pr-arrival').value = g && g.arrival_s !== null ? g.arrival_s : '';
    $('#pr-dist').textContent = g ? `${fmt(g.distance_m, 2)} m` : '—';
    $('#pr-notes').value = g ? (g.notes || '') : '';
    $('#pr-meta').innerHTML = g
      ? `<strong>${g.label}</strong><span class="meta-num">${g.n} capturas</span>` +
        `<span class="meta-num">${fmt(g.fs, 0)} Hz</span>`
      : 'no hay promedios: hace falta validar capturas en Capturas.';
    dibujar();
  }

  function dibujar() {
    const g = actual();
    const c = {
      geo: cssVar('--sig-geo', '#0066cc'),
      hammer: cssVar('--sig-hammer', '#cc5a00'),
      trigger: cssVar('--sig-trigger', '#e67e22'),
    };
    const vacio = (canvas, yLabel) => createFrame(canvas, {
      xMin: -0.05, xMax: 1, yMin: -1, yMax: 1,
      xLabel: 'tiempo relativo al hammer [s]', yLabel });
    if (!g) { vacio(elHam, 'Hammer [V]'); frameGeo = vacio(elGeo, 'Geo [V]'); return; }

    const eje = (tr) => {
      const lo = tr && tr.y_min !== null ? tr.y_min : -1;
      const hi = tr && tr.y_max !== null ? tr.y_max : 1;
      const pad = Math.max((hi - lo) * 0.05, 1e-9);
      return { xMin: -0.05, xMax: 0.8, yMin: lo - pad, yMax: hi + pad,
               xLabel: 'tiempo relativo al hammer [s]' };
    };

    const fh = createFrame(elHam, { ...eje(g.hammer), yLabel: 'Hammer [V]' });
    if (g.hammer) drawMinMax(fh, g.hammer, { color: c.hammer, lineWidth: 1 });
    drawVLine(fh, 0, { color: c.trigger, dashed: true });

    frameGeo = createFrame(elGeo, { ...eje(g.geo), yLabel: 'Geo [V]' });
    if (g.geo) drawMinMax(frameGeo, g.geo, { color: c.geo, lineWidth: 1 });
    drawVLine(frameGeo, 0, { color: c.trigger, dashed: true });
    if (g.arrival_s !== null && g.arrival_s !== undefined) {
      drawVLine(frameGeo, g.arrival_s, {
        color: c.trigger, lineWidth: 2, label: `arribo ${fmt(g.arrival_s)}s` });
    }
  }

  async function load() {
    $('#pr-meta').textContent = 'calculando promedios…';
    try {
      const q = new URLSearchParams({ campaign,
        max_points: String(Math.max(200, Math.round(elGeo.clientWidth || 800))) });
      data = await fetch(`/api/averages?${q}`, { cache: 'no-store' }).then((r) => r.json());
      indice = Math.min(indice, Math.max(0, data.groups.length - 1));
      render();
    } catch (err) {
      $('#pr-meta').textContent = `no se pudo cargar: ${err}`;
    }
  }

  async function guardar(patch, { avanzar = false } = {}) {
    const g = actual();
    if (!g) return;
    $('#pr-status').textContent = 'guardando…';
    try {
      const res = await fetch('/api/averages/arrival', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign, label: g.label, distance_m: g.distance_m, ...patch }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const out = await res.json();
      Object.assign(g, { arrival_s: out.arrival_s, reviewed: out.reviewed, notes: out.notes });
      $('#pr-status').textContent = `guardado en ${out.path}`;
      if (avanzar) indice = Math.min(data.groups.length - 1, indice + 1);
      render();
    } catch (err) {
      $('#pr-status').textContent = `no se pudo guardar: ${err}`;
    }
  }

  // Click en el gráfico del geo = poner el arribo ahí (todavía sin guardar).
  elGeo.addEventListener('click', (ev) => {
    const g = actual();
    if (!g || !frameGeo) return;
    const rect = elGeo.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const t = frameGeo.xMin + ((x - frameGeo.x0) / frameGeo.pw) * (frameGeo.xMax - frameGeo.xMin);
    g.arrival_s = Math.max(frameGeo.xMin, Math.min(frameGeo.xMax, t));
    $('#pr-arrival').value = g.arrival_s.toFixed(4);
    $('#pr-status').textContent = 'arribo movido (sin guardar): «Validar y siguiente» lo guarda';
    render();
  });

  $('#pr-rows').addEventListener('click', (ev) => {
    const tr = ev.target.closest('tr[data-i]');
    if (tr) { indice = Number(tr.dataset.i); render(); }
  });
  $('#pr-arrival').addEventListener('change', (ev) => {
    const g = actual();
    if (g) { g.arrival_s = Number(ev.target.value) || 0; render(); }
  });
  $('#pr-save').addEventListener('click', () => {
    const g = actual();
    if (!g) return;
    guardar({ arrival_s: g.arrival_s ?? 0, reviewed: true, notes: $('#pr-notes').value },
            { avanzar: true });
  });
  $('#pr-unreview').addEventListener('click', () => guardar({ reviewed: false }));

  const ro = new ResizeObserver(() => dibujar());
  ro.observe(elGeo);

  return {
    resume() { picker.reload(); load(); },
    destroy() { ro.disconnect(); },
  };
}
