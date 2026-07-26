// Tab Waterfall (§3.4). Porta `WaterfallPanel` de field_review_app.py (:3341):
// el tendido entero en un gráfico, cada promedio corrido a su distancia.
//
// Lo que es SÓLO VISTA y lo que PERSISTE es la distinción del panel original y
// se respeta tal cual:
//   · sólo vista → recorte de tiempo, trazas destildadas, escala de amplitud y
//     filtro f-k. El export de Promedios sigue con todo y el rango completo.
//   · persiste  → «Invertir traza», que togglea geo_flip en TODAS las capturas
//     de esa distancia y llega a promedios, MASW y export.
//
// El servidor devuelve las curvas YA escaladas y corridas a su distancia: la
// separación depende del espaciado mediano del tendido, que es del conjunto y
// no de cada traza (`_redraw` :3845). Reimplementarlo acá sería tener la misma
// cuenta en dos lados.
import { createFrame, drawMinMax, attachViewControls } from '../plot.js';
import { mountCampaignPicker } from '../campaign_picker.js';

const fmt = (n, d = 2) => (n === null || n === undefined) ? '—' : Number(n).toFixed(d);

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export function mount(root) {
  root.innerHTML = `
    <div class="workspace">
      <div class="ws-left">
        <div class="card" id="wf-campaign"></div>

        <div class="card">
          <div class="field-row">
            <label for="wf-group">Grupo</label>
            <select id="wf-group"></select>
          </div>

          <label class="control-check" title="Por defecto cada traza se normaliza a su propio pico para comparar formas. Con esto todas comparten la misma escala y se ve caer la amplitud con la distancia.">
            <input type="checkbox" id="wf-raw"> Amplitud real (ver atenuación)</label>

          <div class="field-row">
            <label for="wf-kfilter">Filtro K</label>
            <select id="wf-kfilter" title="Filtro direccional frecuencia-número de onda (f-k). Separa las ondas por dirección de propagación. Cuál es el 'correcto' depende de cómo quedó el tendido: elegí el que deje la onda directa y no el rebote.">
              <option value="off">Off</option>
              <option value="directo">Directo</option>
              <option value="inverso">Inverso</option>
            </select>
          </div>

          <label class="control-check" title="Sólo afecta la vista y lo que se manda a MASW; el export de Promedios sigue con el rango completo.">
            <input type="checkbox" id="wf-trim"> Recortar tiempo</label>
          <div class="field-row">
            <label for="wf-trim-a">desde</label>
            <input type="number" id="wf-trim-a" step="0.01" class="num-input">
            <label for="wf-trim-b">hasta</label>
            <input type="number" id="wf-trim-b" step="0.01" class="num-input">
          </div>
          <p class="note" id="wf-status"></p>
        </div>

        <div class="card pane-list">
          <h2>Trazas (distancia)</h2>
          <div class="wrap table-scroll">
            <table class="rows-table"><thead><tr>
              <th>Ver</th><th>Distancia</th><th class="num">Pico</th><th class="num">Arribo s</th>
            </tr></thead><tbody id="wf-rows"></tbody></table>
          </div>
          <div class="toolbar">
            <button type="button" id="wf-all" class="btn-quiet btn-sm">Todas</button>
            <button type="button" id="wf-none" class="btn-quiet btn-sm">Ninguna</button>
          </div>
          <div class="toolbar">
            <button type="button" id="wf-flip">Invertir traza</button>
            <button type="button" id="wf-auto-pol" title="Corrige la polaridad en dos etapas. 1) Intra-punto: las capturas SIN validar se enfasan contra el consenso de las validadas de su distancia (las validadas no se tocan; el flip queda como propuesta que aceptás al revisarlas en Capturas). 2) Inter-punto: cada promedio se correlaciona con el del punto vecino ya alineado; si da en contrafase se invierte el punto COMPLETO.">Auto polaridad</button>
          </div>
          <p class="note">«Invertir traza» y «Auto polaridad» no son de vista: tocan
          <code>geo_flip</code> en las anotaciones, así que persisten y llegan a promedios,
          MASW y export.</p>
          <div id="wf-reporte" class="note" hidden></div>
        </div>
      </div>

      <div class="ws-right">
        <section class="card viewer">
          <div class="viewer-head">
            <h2>Waterfall</h2>
            <div class="viewer-meta" id="wf-meta">cargando…</div>
          </div>
          <div class="plot-stack">
            <figure class="plot-box">
              <figcaption class="plot-title">Tendido completo
                <span class="legend" id="wf-coord"></span></figcaption>
              <canvas class="plot" id="wf-plot"></canvas>
            </figure>
          </div>
        </section>
      </div>
    </div>
  `;

  const $ = (s) => root.querySelector(s);
  const elPlot = $('#wf-plot');
  let data = { traces: [], view: { hidden_distances: [] }, group_count: 1, group_id: 1 };
  let campaign = '';
  let seleccion = null;         // distancia elegida en la lista
  let frame = null;
  let pedido = 0;

  const picker = mountCampaignPicker($('#wf-campaign'), {
    onChange: (id) => { campaign = id; seleccion = null; load(); },
  });

  // Rueda, arrastre y doble click, igual que el ViewBox del PlotWidget de la app.
  const view = attachViewControls(elPlot, {
    getFrame: () => frame, onChange: () => dibujar(),
  });

  const ocultas = () => new Set((data.view.hidden_distances || []).map(Number));

  function render() {
    $('#wf-group').innerHTML = Array.from({ length: data.group_count || 1 }, (_, i) =>
      `<option value="${i + 1}"${(i + 1) === data.group_id ? ' selected' : ''}>Grupo ${i + 1}</option>`).join('');
    const v = data.view || {};
    $('#wf-raw').checked = !!v.raw_amplitude;
    $('#wf-trim').checked = !!v.trim_enabled;
    $('#wf-trim-a').value = v.trim_start ?? 0;
    $('#wf-trim-b').value = v.trim_end ?? 1;
    $('#wf-kfilter').value = v.kfilter_mode || 'off';

    // La lista muestra TODAS las distancias del grupo, no sólo las visibles:
    // si no, destildar una la haría desaparecer y no habría cómo devolverla.
    const off = ocultas();
    const porDist = new Map(data.traces.map((t) => [Number(t.distance_m), t]));
    $('#wf-rows').innerHTML = (data.all_distances || []).map((d) => {
      const t = porDist.get(Number(d));
      const visible = !off.has(Number(d));
      const label = t ? t.label : `${fmt(d, 1)} m`;
      return `<tr data-d="${d}"${Number(d) === seleccion ? ' class="is-selected"' : ''}>
        <td><input type="checkbox" class="wf-vis" data-d="${d}"${visible ? ' checked' : ''}></td>
        <td>${label}</td>
        <td class="num">${t ? fmt(t.peak, 4) : '—'}</td>
        <td class="num">${t && t.arrival_s !== null ? fmt(t.arrival_s, 4) : '—'}</td>
      </tr>`;
    }).join('');

    const modo = v.raw_amplitude ? 'amplitud real (atenuación visible)' : 'normalizada por traza';
    const conHammer = data.hammer ? ' + hammer global' : '';
    $('#wf-meta').innerHTML = data.message
      ? data.message
      : `<strong>${data.group_name}</strong>` +
        `<span class="meta-num">${data.n_averages} promedios${conHammer}</span>` +
        `<span class="meta-num">${data.traces.length} visibles</span>` +
        `<span class="meta-num">escala: ${modo}</span>` +
        (data.filter_enabled ? '<span class="meta-num">filtro ACTIVO</span>' : '');
    dibujar();
  }

  function dibujar() {
    const c = {
      traza: cssVar('--plot-axis', '#222'),
      hammer: cssVar('--sig-geo', '#0066cc'),
      arribo: cssVar('--sig-ok-avg', '#d62728'),
    };
    if (!data.traces.length) {
      frame = createFrame(elPlot, view.apply({ xMin: 0, xMax: 1, yMin: 0, yMax: 1,
        xLabel: 'tiempo relativo al hammer [s]',
        yLabel: 'Distancia [m] + amplitud normalizada' }));
      return;
    }
    frame = createFrame(elPlot, view.apply({
      xMin: data.t_min, xMax: data.t_max, yMin: data.y_min, yMax: data.y_max,
      xLabel: 'tiempo relativo al hammer [s]',
      yLabel: 'Distancia [m] + amplitud normalizada',
    }));

    const spacing = Number(data.spacing) || 1;
    for (const t of data.traces) {
      const elegida = Number(t.distance_m) === seleccion;
      drawMinMax(frame, t, { color: c.traza, lineWidth: elegida ? 1.6 : 1 });
      // Rótulo de la distancia al borde izquierdo, como el TextItem de la app.
      etiqueta(t.label, t.distance_m, c.traza, elegida);
      // El arribo, sólo si está validado: un tramo vertical centrado en su traza.
      if (t.arrival_s !== null && t.arrival_s !== undefined) {
        segmentoVertical(t.arrival_s, t.distance_m - spacing * 0.35,
                         t.distance_m + spacing * 0.35, c.arribo);
      }
    }
    if (data.hammer) {
      drawMinMax(frame, data.hammer, { color: c.hammer, lineWidth: 1.5 });
      etiqueta(`hammer prom. (n=${data.hammer.n})`, data.hammer.base, c.hammer, false);
    }
  }

  function etiqueta(texto, y, color, fuerte) {
    const { ctx } = frame;
    const py = frame.yOf(y);
    if (py < frame.y0 - 6 || py > frame.y0 + frame.ph + 6) return;
    ctx.save();
    ctx.font = `${fuerte ? 'bold ' : ''}10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
    ctx.fillStyle = color;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(texto, frame.x0 + 3, py);
    ctx.restore();
  }

  function segmentoVertical(x, yA, yB, color) {
    const px = frame.xOf(x);
    if (px < frame.x0 || px > frame.x0 + frame.pw) return;
    const { ctx } = frame;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(px, frame.yOf(yA));
    ctx.lineTo(px, frame.yOf(yB));
    ctx.stroke();
    ctx.restore();
  }

  function maxPoints() {
    const dpr = window.devicePixelRatio || 1;
    return Math.min(20000, Math.max(200, Math.round((elPlot.clientWidth || 800) * dpr)));
  }

  async function load() {
    const mio = ++pedido;
    $('#wf-meta').textContent = 'armando el waterfall…';
    try {
      const q = new URLSearchParams({ campaign, group_id: String(data.group_id || 1),
        max_points: String(maxPoints()) });
      const r = await fetch(`/api/waterfall?${q}`, { cache: 'no-store' }).then((x) => x.json());
      if (mio !== pedido) return;
      data = r;
      render();
    } catch (err) {
      if (mio === pedido) $('#wf-meta').textContent = `no se pudo armar: ${err}`;
    }
  }

  // Los ajustes de vista se guardan (comparten archivo con la app) y el
  // servidor devuelve el waterfall ya redibujado con ellos.
  async function guardarVista(patch) {
    const mio = ++pedido;
    $('#wf-status').textContent = 'aplicando…';
    try {
      const r = await fetch('/api/waterfall/view', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign, group_id: data.group_id || 1,
                               max_points: maxPoints(), ...patch }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const nuevo = await r.json();
      if (mio !== pedido) return;
      data = nuevo;
      $('#wf-status').textContent = 'guardado';
      render();
    } catch (err) {
      if (mio === pedido) $('#wf-status').textContent = `no se pudo aplicar: ${err}`;
    }
  }

  $('#wf-group').addEventListener('change', (ev) => {
    data.group_id = Number(ev.target.value) || 1;
    seleccion = null;
    view.reset();
    load();
  });
  $('#wf-raw').addEventListener('change', (ev) =>
    guardarVista({ raw_amplitude: ev.target.checked }));
  $('#wf-kfilter').addEventListener('change', (ev) =>
    guardarVista({ kfilter_mode: ev.target.value }));
  $('#wf-trim').addEventListener('change', (ev) =>
    guardarVista({ trim_enabled: ev.target.checked }));
  for (const id of ['#wf-trim-a', '#wf-trim-b']) {
    $(id).addEventListener('change', () => guardarVista({
      trim_start: Number($('#wf-trim-a').value) || 0,
      trim_end: Number($('#wf-trim-b').value) || 0,
    }));
  }

  $('#wf-rows').addEventListener('click', (ev) => {
    const chk = ev.target.closest('.wf-vis');
    if (chk) {
      const off = ocultas();
      const d = Number(chk.dataset.d);
      if (chk.checked) off.delete(d); else off.add(d);
      guardarVista({ hidden_distances: [...off] });
      return;
    }
    const tr = ev.target.closest('tr[data-d]');
    if (tr) { seleccion = Number(tr.dataset.d); render(); }
  });

  $('#wf-all').addEventListener('click', () => guardarVista({ hidden_distances: [] }));
  $('#wf-none').addEventListener('click', () =>
    guardarVista({ hidden_distances: (data.all_distances || []).map(Number) }));

  $('#wf-flip').addEventListener('click', async () => {
    if (seleccion === null) {
      $('#wf-status').textContent = 'elegí una traza en la lista para invertirla';
      return;
    }
    $('#wf-status').textContent = 'invirtiendo…';
    try {
      const r = await fetch('/api/waterfall/flip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign, group_id: data.group_id || 1,
                               distance_m: seleccion, max_points: maxPoints() }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
      $('#wf-status').textContent =
        `polaridad invertida en ${data.flip.changed} capturas de ${fmt(seleccion, 1)} m`;
      render();
    } catch (err) {
      $('#wf-status').textContent = `no se pudo invertir: ${err}`;
    }
  });

  // «Auto polaridad»: las dos etapas de `auto_align_polarity`. El reporte se
  // muestra igual que el QMessageBox de la app (`_auto_polarity` :820), porque
  // saber QUÉ se invirtió es la mitad del valor del botón.
  $('#wf-auto-pol').addEventListener('click', async () => {
    $('#wf-auto-pol').disabled = true;
    $('#wf-status').textContent = 'corriendo las dos etapas…';
    try {
      const r = await fetch('/api/waterfall/auto_polarity', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign, group_id: data.group_id || 1,
                               max_points: maxPoints() }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
      const rep = data.auto_polarity || {};
      const partes = [];
      if ((rep.stage_a_flipped || []).length) {
        const muestra = rep.stage_a_flipped.slice(0, 12).join(', ')
          + (rep.stage_a_flipped.length > 12 ? ', …' : '');
        partes.push(`<strong>Intra-punto:</strong> ${rep.stage_a_flipped.length} captura(s)
          SIN validar invertidas para quedar en fase con las validadas de su punto.
          Las validadas no se tocaron: aceptá las propuestas al revisarlas en Capturas.
          <br><span class="mono">${muestra}</span>`);
      }
      if ((rep.stage_b_flipped || []).length) {
        partes.push(`<strong>Inter-punto:</strong> punto(s) completo(s) invertidos por
          contrafase: <span class="mono">${rep.stage_b_flipped.join(', ')}</span>`);
      }
      if ((rep.stage_b_skipped || []).length) {
        partes.push(`Sin promedio validado, no participaron del enfase entre puntos:
          <span class="mono">${rep.stage_b_skipped.join(', ')}</span>`);
      }
      if (!partes.length) {
        partes.push('No hizo falta ningún cambio: todos los puntos ya están en fase.');
      }
      partes.push('Si algún punto quedó al revés igual, usá «Invertir traza».');
      $('#wf-reporte').innerHTML = partes.map((p) => `<p>${p}</p>`).join('');
      $('#wf-reporte').hidden = false;
      $('#wf-status').textContent = rep.changed ? 'polaridad corregida y guardada' : 'sin cambios';
      render();
    } catch (err) {
      $('#wf-status').textContent = `no se pudo correr: ${err}`;
    } finally {
      $('#wf-auto-pol').disabled = false;
    }
  });

  // Cursor: tiempo y distancia bajo el mouse (el crosshair de `_on_mouse_moved`
  // :3618). Va en el rótulo, no como líneas, para no ensuciar el dibujo.
  elPlot.addEventListener('pointermove', (ev) => {
    if (!frame) return;
    const rect = elPlot.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const y = ev.clientY - rect.top;
    if (x < frame.x0 || x > frame.x0 + frame.pw) { $('#wf-coord').textContent = ''; return; }
    const t = frame.xMin + ((x - frame.x0) / frame.pw) * (frame.xMax - frame.xMin);
    const d = frame.yMin + ((frame.y0 + frame.ph - y) / frame.ph) * (frame.yMax - frame.yMin);
    $('#wf-coord').textContent = `t = ${t.toFixed(4)} s · y = ${d.toFixed(2)} m`;
  });
  elPlot.addEventListener('pointerleave', () => { $('#wf-coord').textContent = ''; });

  const ro = new ResizeObserver(() => dibujar());
  ro.observe(elPlot);
  const mo = new MutationObserver(() => dibujar());
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

  load();

  return {
    resume() { picker.reload(); load(); },
    destroy() { view.destroy(); ro.disconnect(); mo.disconnect(); },
  };
}
