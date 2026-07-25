// Visor de Capturas (§3.1): dibuja hammer + geo de un disparo con el trigger
// marcado, pidiendo la serie ya decimada al servidor (GET /api/signal).
// Sólo lectura: arrastrar el marcador y guardar (POST /api/pick) es el
// próximo ítem — ver PORT_PLAN §3.1 y la nota al pie del visor.
import { drawMinMax, drawVLine } from '../plot.js';

function fmt(n, d = 2) {
  return (n === null || n === undefined) ? '—' : Number(n).toFixed(d);
}

function plotColors() {
  // Mismo criterio que theme.js: data-theme manual, si no prefers-color-scheme.
  const forced = document.documentElement.dataset.theme;
  const dark = forced ? forced === 'dark'
    : !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  // _plot_colors() de field_review_app.py: claro/oscuro para hammer, geo y trigger.
  return dark
    ? { hammer: '#ffb86b', geo: '#69b7ff', trigger: '#ff9f43' }
    : { hammer: '#cc5a00', geo: '#0066cc', trigger: '#e67e22' };
}

export function mountViewer(host) {
  host.innerHTML = `
    <h2>Visor</h2>
    <div class="viewer-meta" id="vm-meta">Elegí una captura con el botón <code>ver</code>.</div>
    <div class="row" style="margin:8px 0 12px;gap:14px;display:flex;align-items:center;flex-wrap:wrap">
      <label><input type="radio" name="vm-kind" value="raw" checked> raw</label>
      <label title="El filt_f32le.bin que grabó el nodo, no el pasa-banda de §3.2 (todavía no está portado)">
        <input type="radio" name="vm-kind" value="filt"> filt</label>
      <label><input type="checkbox" id="vm-flip"> Invertir geo (preview)</label>
      <button id="vm-reload" type="button">recargar</button>
    </div>
    <div class="plot-label">Hammer</div>
    <canvas class="plot" id="vm-hammer"></canvas>
    <div class="plot-label">Geófono</div>
    <canvas class="plot" id="vm-geo"></canvas>
    <p class="sub" style="margin:8px 0 0">Sólo lectura: arrastrar el trigger y guardar el pick
      es el próximo paso (POST /api/pick, PORT_PLAN §3.1).</p>
  `;

  const elMeta = host.querySelector('#vm-meta');
  const elHammer = host.querySelector('#vm-hammer');
  const elGeo = host.querySelector('#vm-geo');
  const elFlip = host.querySelector('#vm-flip');
  const elReload = host.querySelector('#vm-reload');

  let shotId = null;
  let lastPayload = null;
  let abortCtrl = null;

  function sizeCanvas(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 300;
    const h = canvas.clientHeight || 160;
    canvas.width = Math.max(1, Math.round(w * dpr));
    canvas.height = Math.max(1, Math.round(h * dpr));
    canvas.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function maxPointsFor(canvas) {
    const dpr = window.devicePixelRatio || 1;
    // Un bucket por píxel físico: pedir más es tirar bytes (spec §5.2).
    return Math.min(20000, Math.max(100, Math.round((canvas.clientWidth || 300) * dpr)));
  }

  function render() {
    if (!lastPayload) return;
    const colors = plotColors();
    const { channels, trigger_s: triggerS } = lastPayload;
    const hammer = channels.hammer;
    const geo = channels.geo;

    sizeCanvas(elHammer);
    sizeCanvas(elGeo);

    // Ventana igual que field_review_app.py `_refresh_plot` (:1136-1137).
    const hDur = hammer.duration_s || 0;
    const hxMin = Math.max(0, triggerS - 0.15);
    const hxMax = Math.min(hDur, triggerS + 0.65);
    drawMinMax(elHammer, hammer, { color: colors.hammer, xMin: hxMin, xMax: hxMax, xOffset: 0 });
    drawVLine(elHammer, triggerS, { color: colors.trigger, xMin: hxMin, xMax: hxMax });

    const gDur = geo.duration_s || 0;
    const gxMin = Math.max(-triggerS, -0.08);
    const gxMax = Math.min(gDur - triggerS, 1.1);
    drawMinMax(elGeo, geo, { color: colors.geo, xMin: gxMin, xMax: gxMax, xOffset: triggerS });
    drawVLine(elGeo, 0, { color: colors.trigger, xMin: gxMin, xMax: gxMax, dashed: true });

    elMeta.innerHTML =
      `${lastPayload.folder} / ${lastPayload.capture} · ${fmt(lastPayload.distance_m)} m · ` +
      `${fmt(lastPayload.fs, 0)} Hz · ${hammer.samples} muestras · ` +
      `trigger ${fmt(triggerS, 4)} s (${lastPayload.trigger_source})`;
  }

  async function fetchAndRender() {
    if (!shotId) return;
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    const kindEl = host.querySelector('input[name="vm-kind"]:checked');
    const kind = kindEl ? kindEl.value : 'raw';
    const maxPoints = maxPointsFor(elHammer);
    const params = new URLSearchParams({ shot_id: shotId, kind, max_points: String(maxPoints) });
    if (elFlip.checked) params.set('geo_flip', '1');
    try {
      const res = await fetch(`/api/signal?${params.toString()}`,
        { signal: abortCtrl.signal, cache: 'no-store' });
      if (!res.ok) {
        elMeta.textContent = `No se pudo cargar la señal (HTTP ${res.status}).`;
        lastPayload = null;
        return;
      }
      lastPayload = await res.json();
      render();
    } catch (err) {
      if (err.name !== 'AbortError') {
        elMeta.textContent = `Error al cargar la señal: ${err}`;
      }
    }
  }

  host.querySelectorAll('input[name="vm-kind"]').forEach((el) =>
    el.addEventListener('change', fetchAndRender));
  elFlip.addEventListener('change', fetchAndRender);
  elReload.addEventListener('click', fetchAndRender);

  const onResize = () => render();
  window.addEventListener('resize', onResize);

  return {
    show(id, meta) {
      shotId = id;
      lastPayload = null;
      if (meta) {
        elMeta.textContent = `${meta.folder || ''} / ${meta.capture || ''} — cargando…`;
      }
      fetchAndRender();
    },
    destroy() {
      if (abortCtrl) abortCtrl.abort();
      window.removeEventListener('resize', onResize);
    },
  };
}
