// Tab Filtros (§3.2): pasa-banda Butterworth de fase cero + vista previa.
// Porta `FilterPanel` de field_review_app.py (:1811): a la izquierda los
// parámetros, a la derecha la señal original, la filtrada y el espectro log-log.
//
// Diferencia con la app: acá probar y guardar son dos actos distintos. Los
// ajustes mandan sobre promedios, waterfall, MASW y export, así que cambiar un
// número sólo cambia la vista previa hasta que se aprieta «Guardar y aplicar».
//
// Los parámetros se guardan en el mismo archivo que la app
// (filter_settings.json de la campaña) y se aplican a promedios, waterfall,
// MASW y export. Acá sólo se previsualizan sobre la captura elegida en Capturas.
import { createFrame, drawMinMax, attachViewControls } from '../plot.js';

const fmt = (n, d = 2) => (n === null || n === undefined) ? '—' : Number(n).toFixed(d);

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// Qué captura está seleccionada en Capturas. Se guarda ahí mismo, así que las
// dos pestañas hablan del mismo disparo sin acoplarse entre sí.
function currentSelection() {
  try {
    const ui = JSON.parse(localStorage.getItem('geo-capturas-ui')) || {};
    if (!ui.currentKey) return null;
    const [campaign] = String(ui.currentKey).split('|');
    return { key: ui.currentKey, campaign };
  } catch (_) {
    return null;
  }
}

export function mount(root) {
  root.innerHTML = `
    <div class="workspace">
      <div class="ws-left">
        <div class="card">
          <h2>Filtro pasa-banda</h2>
          <p class="note">Butterworth + <code>sosfiltfilt</code>: <strong>fase cero</strong>, o sea
          que no corre los tiempos de arribo. Es la condición que no se puede romper acá.</p>

          <div class="mark-grid">
            <label class="control-check span-4"><input type="checkbox" id="f-enabled">
              Aplicar a promedios / waterfall / MASW / export</label>

            <label for="f-low">Corte bajo</label>
            <input type="number" id="f-low" step="0.5" min="0" max="5000" class="num-input">
            <label for="f-high">Corte alto</label>
            <input type="number" id="f-high" step="5" min="0" max="5000" class="num-input">

            <label for="f-order">Orden</label>
            <input type="number" id="f-order" step="1" min="1" max="10" class="num-input">
            <label for="f-fs">fs común</label>
            <input type="number" id="f-fs" step="10" min="0" max="20000" class="num-input">
          </div>
          <p class="note" id="f-hint-values"></p>

          <div class="toolbar">
            <button type="button" id="f-preview">Probar acá</button>
            <button type="button" id="f-save">Guardar y aplicar</button>
            <button type="button" id="f-revert" class="btn-quiet">Volver a lo guardado</button>
          </div>
          <p class="note" id="f-saved"></p>
          <p class="warn-note" id="f-dirty" hidden>Estos valores están <strong>sólo en la vista
            previa</strong>. Lo guardado —y lo que usan promedios, waterfall, MASW y export— sigue
            siendo lo otro hasta que le des «Guardar y aplicar».</p>
        </div>

        <div class="card">
          <details class="foot-details" open>
            <summary>Cómo se combinan fs distintas</summary>
            <p class="note">Dentro de cada grupo (misma distancia) las capturas se resamplean a la
            fs común —por defecto la mínima del grupo, p. ej. 2929 Hz baja a 1020 Hz— y se alinean
            por su trigger. Las capturas viejas de 3 s aportan al promedio sólo hasta donde llegan;
            la cola larga la definen las de 10.59 s (NaN donde no hay dato: no se inventa señal).</p>
            <p class="note">Órdenes 5–10 aplican bien, pero conviene orden bajo (2–4) si se ve el
            inicio de la señal deformado: un orden alto tiene más transitorio.</p>
          </details>
        </div>
      </div>

      <div class="ws-right">
        <section class="card viewer">
          <div class="viewer-head">
            <h2>Vista previa</h2>
            <div class="viewer-meta" id="f-meta">Elegí una captura en la pestaña Capturas.</div>
          </div>
          <div class="plot-stack">
            <figure class="plot-box">
              <figcaption class="plot-title"><span class="lg lg-orig"></span>Geófono original</figcaption>
              <canvas class="plot" id="f-orig"></canvas>
            </figure>
            <figure class="plot-box">
              <figcaption class="plot-title"><span class="lg lg-filt"></span>Geófono filtrado
                <span class="legend" id="f-band"></span></figcaption>
              <canvas class="plot" id="f-filt"></canvas>
            </figure>
            <figure class="plot-box">
              <figcaption class="plot-title">Espectro, log-log
                <span class="legend">
                  <span class="lg lg-orig"></span>original
                  <span class="lg lg-filt"></span>filtrado
                </span></figcaption>
              <canvas class="plot" id="f-spec"></canvas>
            </figure>
          </div>
        </section>
      </div>
    </div>
  `;

  const $ = (s) => root.querySelector(s);
  const elOrig = $('#f-orig');
  const elFilt = $('#f-filt');
  const elSpec = $('#f-spec');
  const elMeta = $('#f-meta');
  const elSaved = $('#f-saved');

  let settings = null;
  let preview = null;
  let campaign = '';
  let abortReq = null;
  const frames = { orig: null, filt: null, spec: null };

  // Rueda para acercar sobre el cursor, arrastre para mover, doble click para
  // reencuadrar. En el espectro los ejes son logarítmicos y el zoom lo respeta.
  // Los dos marcos de tiempo comparten vista: mirar la original y la filtrada
  // en ventanas distintas no sirve para compararlas.
  const viewTiempo = attachViewControls([elOrig, elFilt], {
    getFrame: (el) => (el === elFilt ? frames.filt : frames.orig),
    onChange: () => render(),
  });
  const viewSpec = attachViewControls(elSpec, {
    getFrame: () => frames.spec,
    onChange: () => render(),
  });

  function params() {
    return {
      low_hz: Number($('#f-low').value) || 0,
      high_hz: Number($('#f-high').value) || 0,
      order: Math.max(1, Math.min(10, Number($('#f-order').value) || 4)),
      target_fs: Number($('#f-fs').value) || 0,
      enabled: $('#f-enabled').checked,
    };
  }

  function renderHint() {
    const p = params();
    const banda = (!p.low_hz && !p.high_hz) ? 'sin filtrar'
      : (!p.low_hz ? `pasa-bajos ≤ ${p.high_hz} Hz`
        : (!p.high_hz ? `pasa-altos ≥ ${p.low_hz} Hz`
          : `pasa-banda ${p.low_hz}–${p.high_hz} Hz`));
    const fs = p.target_fs ? `${p.target_fs} Hz` : 'auto (la mínima del grupo)';
    $('#f-hint-values').textContent = `${banda}, orden ${p.order} · fs común: ${fs}`;
  }

  function render() {
    const c = {
      orig: cssVar('--sig-overlay', 'rgba(120,120,120,.5)'),
      filt: cssVar('--sig-geo', '#0066cc'),
    };
    const vacio = (canvas, opts) => createFrame(canvas, {
      xMin: 0, xMax: 1, yMin: -1, yMax: 1,
      xLabel: 'tiempo relativo al hammer [s]', yLabel: 'Geo [V]', ...opts });

    if (!preview) {
      frames.orig = vacio(elOrig);
      frames.filt = vacio(elFilt);
      frames.spec = vacio(elSpec, { xMin: 1, xMax: 1000, yMin: 1e-3, yMax: 1,
        xLog: true, yLog: true, xLabel: 'frecuencia [Hz]', yLabel: '|FFT|' });
      return;
    }

    // Original y filtrada en marcos separados, uno sobre otro y con el MISMO
    // eje vertical: superpuestas se tapaban entre sí y no se podía ver qué
    // cambió. Compartir la escala es lo que permite comparar de un vistazo.
    const t = preview.time;
    const trazas = [t.original, t.filtered].filter(Boolean);
    if (trazas.length) {
      const lo = Math.min(...trazas.map((x) => x.y_min ?? 0));
      const hi = Math.max(...trazas.map((x) => x.y_max ?? 0));
      const pad = Math.max((hi - lo) * 0.05, 1e-9);
      const dur = Math.max(...trazas.map((x) => (x.t0 || 0) + x.samples / (x.fs || 1)));
      const eje = viewTiempo.apply({
        xMin: -0.08, xMax: Math.min(dur, 1.1), yMin: lo - pad, yMax: hi + pad,
        xLabel: 'tiempo relativo al hammer [s]', yLabel: 'Geo [V]' });
      frames.orig = createFrame(elOrig, eje);
      if (t.original) drawMinMax(frames.orig, t.original, { color: c.orig, lineWidth: 1 });
      frames.filt = createFrame(elFilt, eje);
      if (t.filtered) drawMinMax(frames.filt, t.filtered, { color: c.filt, lineWidth: 1 });
    }

    const s = preview.spectrum;
    const specs = [s.original, s.filtered].filter(Boolean);
    if (specs.length) {
      const yHi = Math.max(...specs.map((x) => x.y_max ?? 1));
      const frame = createFrame(elSpec, viewSpec.apply({
        xMin: Math.min(...specs.map((x) => x.f_min || 1)),
        xMax: Math.max(...specs.map((x) => x.f_max || 1000)),
        // Seis décadas por debajo del máximo: más abajo es sólo ruido numérico.
        yMin: Math.max(yHi * 1e-6, 1e-12), yMax: yHi,
        xLog: true, yLog: true,
        xLabel: 'frecuencia [Hz]', yLabel: '|FFT|',
      }));
      frames.spec = frame;
      // El espectro trae su abscisa punto por punto (bins en log, no pasos iguales).
      if (s.original) drawMinMax(frame, { ...s.original, x: s.original.f },
                                 { color: c.orig, lineWidth: 1, alpha: 0.7 });
      if (s.filtered) drawMinMax(frame, { ...s.filtered, x: s.filtered.f },
                                 { color: c.filt, lineWidth: 1 });
    }

    const a = preview.applied;
    $('#f-band').textContent = (!a.low_hz && !a.high_hz) ? 'sin filtrar'
      : (!a.low_hz ? `≤ ${a.high_hz} Hz` : (!a.high_hz ? `≥ ${a.low_hz} Hz`
        : `${a.low_hz}–${a.high_hz} Hz`)) + `, orden ${a.order}`;

    const estado = preview.enabled ? 'ACTIVO en promedios/export' : 'sólo vista previa (no aplicado)';
    const resamp = preview.resampled
      ? ` · resampleada ${fmt(preview.fs, 0)}→${fmt(preview.work_fs, 0)} Hz` : '';
    elMeta.innerHTML =
      `<strong>${preview.folder} / ${preview.capture}</strong>` +
      `<span class="meta-num">${fmt(preview.distance_m)} m</span>` +
      `<span class="meta-num">${fmt(preview.fs, 0)} Hz</span>` +
      `<span class="meta-num">guardado: filtro ${estado}${resamp}</span>`;
  }

  // ¿Los valores del formulario difieren de lo guardado? Mientras difieran, lo
  // que se ve es sólo una prueba y hay que decirlo.
  function marcarSucio() {
    if (!settings) return;
    const p = params();
    const sucio = ['low_hz', 'high_hz', 'order', 'target_fs'].some(
      (k) => Number(p[k]) !== Number(settings[k])) || p.enabled !== !!settings.enabled;
    $('#f-dirty').hidden = !sucio;
    $('#f-save').classList.toggle('is-primary', sucio);
  }

  async function loadSettings() {
    const sel = currentSelection();
    campaign = sel ? sel.campaign : '';
    try {
      settings = await fetch(`/api/filter?campaign=${encodeURIComponent(campaign)}`,
        { cache: 'no-store' }).then((r) => r.json());
      $('#f-enabled').checked = !!settings.enabled;
      $('#f-low').value = settings.low_hz || 0;
      $('#f-high').value = settings.high_hz || 0;
      $('#f-order').value = settings.order || 4;
      $('#f-fs').value = settings.target_fs || 0;
      elSaved.textContent = `guardado en ${settings.path}`;
      renderHint();
      marcarSucio();
    } catch (err) {
      elSaved.textContent = `no se pudieron leer los ajustes: ${err}`;
    }
  }

  async function saveSettings() {
    try {
      const res = await fetch('/api/filter', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign, ...params() }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      settings = await res.json();
      elSaved.textContent = 'guardado y aplicado';
    } catch (err) {
      elSaved.textContent = `no se pudo guardar: ${err}`;
    }
  }

  // Fila (disparo) que se está previsualizando. Se resuelve una vez por
  // selección y no en cada preview: /api/captures son ~2000 filas.
  let filaCache = null;

  async function resolverFila() {
    const sel = currentSelection();
    if (!sel) return null;
    campaign = sel.campaign;
    if (filaCache && filaCache.key === sel.key) return filaCache;
    try {
      const caps = await fetch('/api/captures', { cache: 'no-store' }).then((r) => r.json());
      filaCache = (caps.rows || []).find((r) => r.key === sel.key) || null;
    } catch (_) {
      filaCache = null;
    }
    return filaCache;
  }

  // Sólo la respuesta del último pedido pinta. Sin esto, una vista previa vieja
  // que tarda más que la nueva la sobrescribe y parece que el botón no hace
  // nada (era exactamente el síntoma).
  let pedido = 0;

  async function refreshPreview() {
    const mio = ++pedido;
    const row = await resolverFila();
    if (mio !== pedido) return;
    if (!row) {
      elMeta.textContent = 'Elegí una captura en la pestaña Capturas y volvé acá.';
      preview = null;
      render();
      return;
    }
    if (!row.shot_id) {
      elMeta.textContent = 'La captura elegida no es un disparo: no hay geófono que filtrar.';
      preview = null;
      render();
      return;
    }

    if (abortReq) abortReq.abort();
    abortReq = new AbortController();
    const p = params();
    const q = new URLSearchParams({
      shot_id: row.shot_id, campaign: row.campaign || '',
      max_points: String(Math.max(200, Math.round(elOrig.clientWidth || 800))),
      low_hz: String(p.low_hz), high_hz: String(p.high_hz),
      order: String(p.order), target_fs: String(p.target_fs),
    });
    $('#f-preview').disabled = true;
    elMeta.textContent = 'calculando…';
    try {
      const res = await fetch(`/api/filter/preview?${q}`,
        { signal: abortReq.signal, cache: 'no-store' });
      if (mio !== pedido) return;
      if (!res.ok) {
        const detalle = await res.text();
        elMeta.textContent = `no se pudo filtrar (HTTP ${res.status}): ${detalle.slice(0, 160)}`;
        preview = null;
        render();
        return;
      }
      preview = await res.json();
      if (mio !== pedido) return;
      render();
    } catch (err) {
      if (err.name !== 'AbortError' && mio === pedido) elMeta.textContent = `error: ${err}`;
    } finally {
      if (mio === pedido) $('#f-preview').disabled = false;
    }
  }

  // Cambiar un parámetro sólo re-previsualiza: NO guarda. Guardar cambia lo que
  // usan promedios, waterfall, MASW y export, así que es un acto aparte y
  // explícito. (Antes guardaba en cada tecleo y era muy fácil pisar sin querer
  // los ajustes de una campaña.)
  for (const id of ['#f-enabled', '#f-low', '#f-high', '#f-order', '#f-fs']) {
    $(id).addEventListener('change', () => { renderHint(); marcarSucio(); refreshPreview(); });
  }
  $('#f-preview').addEventListener('click', refreshPreview);
  $('#f-save').addEventListener('click', async () => {
    await saveSettings();
    marcarSucio();
    refreshPreview();
  });
  $('#f-revert').addEventListener('click', () => {
    loadSettings().then(() => { marcarSucio(); refreshPreview(); });
  });

  const ro = new ResizeObserver(() => render());
  ro.observe(elOrig);
  const mo = new MutationObserver(() => render());
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

  loadSettings().then(refreshPreview);

  return {
    // Al volver a la pestaña puede haber cambiado la captura elegida en Capturas.
    resume() { filaCache = null; loadSettings().then(refreshPreview); },
    destroy() { viewTiempo.destroy(); viewSpec.destroy(); ro.disconnect(); mo.disconnect(); },
  };
}
