// MASW · 1. Dispersión (§3.5). Porta la parte de `MaswPanel._build_dispersion_tab`
// que muestra la imagen frecuencia-velocidad de fase (phase-shift, Park et al.
// 1998).
//
// La imagen la calcula el servidor con `masw_dispersion.py`, el MISMO módulo
// que usa la app, sobre lo que el waterfall tenga a la vista: si ahí se recortó
// el tiempo o se destildó una traza mala, acá tampoco están.
//
// Llega como PNG en escala de grises y el color se aplica en el navegador con
// la misma rampa que `_dispersion_colormap` (:4824). Mandar los ~95 000 floats
// de la grilla en JSON sería casi medio mega por recálculo.
import { createFrame, attachViewControls } from '../plot.js';
import { mountCampaignPicker } from '../campaign_picker.js';

// Paradas de `_dispersion_colormap` (:4824): azul oscuro → azul → verde →
// amarillo → rojo → bordó.
const PARADAS = [
  [0.00, [0, 0, 130]], [0.28, [0, 90, 255]], [0.48, [0, 190, 90]],
  [0.68, [255, 230, 0]], [0.86, [255, 0, 0]], [1.00, [90, 0, 0]],
];

// LUT de 256 entradas, interpolando linealmente entre paradas.
const LUT = (() => {
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let k = 0;
    while (k < PARADAS.length - 2 && t > PARADAS[k + 1][0]) k++;
    const [t0, c0] = PARADAS[k];
    const [t1, c1] = PARADAS[k + 1];
    const u = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
    for (let ch = 0; ch < 3; ch++) lut[i * 3 + ch] = c0[ch] + (c1[ch] - c0[ch]) * u;
  }
  return lut;
})();

const fmt = (n, d = 1) => (n === null || n === undefined) ? '—' : Number(n).toFixed(d);

export function mount(root) {
  root.innerHTML = `
    <div class="workspace">
      <div class="ws-left">
        <div class="card" id="ms-campaign"></div>

        <div class="card">
          <h2>Imagen de dispersión</h2>
          <p class="note">Método phase-shift (Park et al. 1998), el mismo
          <code>masw_dispersion.py</code> que usa la app. Entra lo que la pestaña
          Waterfall tenga a la vista: su recorte de tiempo, su filtro f-k y sólo
          las trazas tildadas.</p>

          <div class="field-row">
            <label for="ms-group">Grupo</label>
            <select id="ms-group"></select>
          </div>

          <div class="mark-grid">
            <label for="ms-cmin">c mín</label>
            <input type="number" id="ms-cmin" step="10" value="50" class="num-input">
            <label for="ms-cmax">c máx</label>
            <input type="number" id="ms-cmax" step="10" value="800" class="num-input">
            <label for="ms-cstep">Δc</label>
            <input type="number" id="ms-cstep" step="0.5" value="2" class="num-input">
            <label for="ms-fmin">f mín</label>
            <input type="number" id="ms-fmin" step="1" value="1" class="num-input">
            <label for="ms-fmax">f máx</label>
            <input type="number" id="ms-fmax" step="5" value="100" class="num-input">
          </div>

          <div class="toolbar">
            <button type="button" id="ms-calc">Calcular</button>
          </div>
          <p class="note" id="ms-status">La imagen tarda unos segundos: se promedia
          todo el grupo antes del slant-stack.</p>
        </div>
      </div>

      <div class="ws-right">
        <section class="card viewer">
          <div class="viewer-head">
            <h2>Frecuencia — velocidad de fase</h2>
            <div class="viewer-meta" id="ms-meta">todavía sin calcular</div>
          </div>
          <div class="plot-stack">
            <figure class="plot-box">
              <figcaption class="plot-title">Dispersión
                <span class="legend" id="ms-coord"></span></figcaption>
              <canvas class="plot" id="ms-plot"></canvas>
            </figure>
          </div>
          <p class="note">Falta el picking: las regiones-polígono editables que dan N
          curvas por modo (§3.5, anotado en DUDAS_LUNES.md #17).</p>
        </section>
      </div>
    </div>
  `;

  const $ = (s) => root.querySelector(s);
  const elPlot = $('#ms-plot');
  let campaign = '';
  let grupo = 1;
  let grupos = 1;
  let img = null;             // canvas ya coloreado, a resolución nativa
  let datos = null;
  let frame = null;
  let pedido = 0;

  const picker = mountCampaignPicker($('#ms-campaign'), {
    onChange: (id) => {
      campaign = id;
      grupo = 1;
      img = null;
      datos = null;
      cargarGrupos();
    },
  });

  const view = attachViewControls(elPlot, {
    getFrame: () => frame, onChange: () => dibujar(),
  });

  function params() {
    return {
      c_min: Number($('#ms-cmin').value) || 50,
      c_max: Number($('#ms-cmax').value) || 800,
      c_step: Number($('#ms-cstep').value) || 2,
      f_min: Number($('#ms-fmin').value) || 0,
      f_max: Number($('#ms-fmax').value) || 100,
    };
  }

  // El PNG viene en gris; acá se le aplica la rampa de color una sola vez y
  // queda cacheado. Redibujar por zoom no vuelve a mapear 95 000 píxeles.
  async function colorear(base64, w, h) {
    const blob = await fetch(`data:image/png;base64,${base64}`).then((r) => r.blob());
    const bitmap = await createImageBitmap(blob);
    const off = document.createElement('canvas');
    off.width = w;
    off.height = h;
    const octx = off.getContext('2d', { willReadFrequently: true });
    octx.drawImage(bitmap, 0, 0);
    const px = octx.getImageData(0, 0, w, h);
    const d = px.data;
    for (let i = 0; i < d.length; i += 4) {
      const g = d[i] * 3;
      d[i] = LUT[g]; d[i + 1] = LUT[g + 1]; d[i + 2] = LUT[g + 2]; d[i + 3] = 255;
    }
    octx.putImageData(px, 0, 0);
    return off;
  }

  function dibujar() {
    $('#ms-group').innerHTML = Array.from({ length: grupos }, (_, i) =>
      `<option value="${i + 1}"${(i + 1) === grupo ? ' selected' : ''}>Grupo ${i + 1}</option>`).join('');

    if (!datos || !img) {
      frame = createFrame(elPlot, view.apply({
        xMin: 0, xMax: 100, yMin: 0, yMax: 800,
        xLabel: 'frecuencia [Hz]', yLabel: 'velocidad de fase [m/s]' }));
      return;
    }
    frame = createFrame(elPlot, view.apply({
      xMin: datos.f_min, xMax: datos.f_max, yMin: datos.c_min, yMax: datos.c_max,
      xLabel: 'frecuencia [Hz]', yLabel: 'velocidad de fase [m/s]',
    }));

    // La imagen ocupa exactamente el rectángulo de datos, así que se estira al
    // área que le corresponde según el encuadre actual: con el zoom se recorta
    // sola y sigue coincidiendo con los ejes.
    const { ctx } = frame;
    const x0 = frame.xOf(datos.f_min);
    const x1 = frame.xOf(datos.f_max);
    const y0 = frame.yOf(datos.c_max);
    const y1 = frame.yOf(datos.c_min);
    ctx.save();
    ctx.beginPath();
    ctx.rect(frame.x0, frame.y0, frame.pw, frame.ph);
    ctx.clip();
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(img, x0, y0, x1 - x0, y1 - y0);
    ctx.restore();
  }

  async function calcular() {
    const mio = ++pedido;
    $('#ms-calc').disabled = true;
    $('#ms-meta').textContent = 'calculando la imagen de dispersión…';
    try {
      const p = params();
      const q = new URLSearchParams({ campaign, group_id: String(grupo),
        c_min: String(p.c_min), c_max: String(p.c_max), c_step: String(p.c_step),
        f_min: String(p.f_min), f_max: String(p.f_max) });
      const r = await fetch(`/api/masw/dispersion?${q}`, { cache: 'no-store' });
      if (mio !== pedido) return;
      if (!r.ok) {
        const detalle = await r.json().catch(() => ({}));
        $('#ms-meta').textContent = detalle.detail || `no se pudo calcular (HTTP ${r.status})`;
        datos = null; img = null; dibujar();
        return;
      }
      const nuevo = await r.json();
      const lienzo = await colorear(nuevo.image_png, nuevo.width, nuevo.height);
      if (mio !== pedido) return;
      datos = nuevo;
      img = lienzo;
      $('#ms-meta').innerHTML =
        `<span class="meta-num">${datos.n_channels} receptores</span>` +
        `<span class="meta-num">${fmt(datos.fs, 0)} Hz</span>` +
        `<span class="meta-num">${datos.width}×${datos.height}</span>` +
        `<span class="meta-num">f ${fmt(datos.f_min)}–${fmt(datos.f_max)} Hz</span>` +
        `<span class="meta-num">c ${fmt(datos.c_min, 0)}–${fmt(datos.c_max, 0)} m/s</span>`;
      $('#ms-status').textContent = 'listo';
      dibujar();
    } catch (err) {
      if (mio === pedido) $('#ms-meta').textContent = `error: ${err}`;
    } finally {
      if (mio === pedido) $('#ms-calc').disabled = false;
    }
  }

  $('#ms-calc').addEventListener('click', calcular);
  $('#ms-group').addEventListener('change', (ev) => {
    grupo = Number(ev.target.value) || 1;
    datos = null; img = null;
    view.reset();
  });

  elPlot.addEventListener('pointermove', (ev) => {
    if (!frame) return;
    const rect = elPlot.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const y = ev.clientY - rect.top;
    if (x < frame.x0 || x > frame.x0 + frame.pw) { $('#ms-coord').textContent = ''; return; }
    const f = frame.xMin + ((x - frame.x0) / frame.pw) * (frame.xMax - frame.xMin);
    const c = frame.yMin + ((frame.y0 + frame.ph - y) / frame.ph) * (frame.yMax - frame.yMin);
    $('#ms-coord').textContent = `${f.toFixed(2)} Hz · ${c.toFixed(0)} m/s`;
  });
  elPlot.addEventListener('pointerleave', () => { $('#ms-coord').textContent = ''; });

  // Cuántos grupos hay: lo dice el mismo endpoint que usa Agrupamiento.
  function cargarGrupos() {
    fetch(`/api/groups?campaign=${encodeURIComponent(campaign)}`, { cache: 'no-store' })
      .then((r) => r.json())
      .then((g) => { grupos = Math.max(1, Number(g.group_count) || 1); dibujar(); })
      .catch(() => dibujar());
  }
  cargarGrupos();

  const ro = new ResizeObserver(() => dibujar());
  ro.observe(elPlot);
  const mo = new MutationObserver(() => dibujar());
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

  dibujar();

  return {
    resume() { picker.reload(); cargarGrupos(); },
    destroy() { view.destroy(); ro.disconnect(); mo.disconnect(); },
  };
}
