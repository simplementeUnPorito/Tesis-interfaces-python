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

            <label class="control-check span-4"><input type="checkbox" id="f-dc">
              Remover componente continua antes del filtro</label>
            <label class="control-check span-4"><input type="checkbox" id="f-line">
              Supresor armónico adaptativo de línea</label>

            <label for="f-line-f0">Línea nominal</label>
            <input type="number" id="f-line-f0" step="1" min="1" max="200" class="num-input">
            <label for="f-line-harm">Armónicos</label>
            <input type="number" id="f-line-harm" step="1" min="1" max="12" class="num-input">
            <label for="f-line-search">Búsqueda ±Hz</label>
            <input type="number" id="f-line-search" step="0.1" min="0" max="10" class="num-input">
            <label class="control-check"><input type="checkbox" id="f-envelope">
              Ver Hilbert</label>
          </div>
          <p class="note">El supresor no es un notch IIR: estima la frecuencia real de
          línea y resta el modelo armónico ajustado sobre la captura completa.
          Hilbert es exclusivamente visual.</p>
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

        <div class="card" id="k-card">
          <details class="foot-details" id="k-details">
            <summary>Deconvolución Kalman <span class="legend">opcional</span></summary>
            <p class="note">Estima el <strong>movimiento del suelo</strong> invirtiendo el modelo
            físico geófono × acondicionador, en vez de solo filtrar. Es una vista previa aparte:
            <strong>no cambia</strong> lo que usan promedios, waterfall, MASW ni export.</p>
            <p class="warn-note" id="k-unavailable" hidden></p>

            <div class="mark-grid">
              <label class="control-check span-4"><input type="checkbox" id="k-enabled">
                Mostrar el brazo Kalman en la vista previa</label>

              <label for="k-geo">Geófono</label>
              <select id="k-geo" class="span-3"></select>
              <label for="k-cond">Acondicionador</label>
              <select id="k-cond" class="span-3"></select>
              <label for="k-estimate">Magnitud</label>
              <select id="k-estimate" class="span-3"></select>
              <label for="k-input">Prior de entrada</label>
              <select id="k-input" class="span-3"></select>

              <label for="k-band-low" id="k-band-lo-label">Banda del prior</label>
              <input type="number" id="k-band-low" step="1" min="0.01" max="500" class="num-input">
              <label for="k-band-high" id="k-band-hi-label">a</label>
              <input type="number" id="k-band-high" step="1" min="0.02" max="500" class="num-input">

              <label for="k-leak" id="k-leak-label">Fuga del prior</label>
              <input type="number" id="k-leak" step="0.1" min="0.001" max="100" class="num-input">
              <label for="k-disc">Discretización</label>
              <select id="k-disc"></select>

              <label for="k-q-source">Origen de Q</label>
              <select id="k-q-source"></select>
              <label for="k-q">q fijo</label>
              <input type="number" id="k-q" step="1e-5" min="0" class="num-input">
              <label for="k-r-source">Origen de R</label>
              <select id="k-r-source"></select>
              <label for="k-r">R fijo</label>
              <input type="number" id="k-r" step="1e-6" min="0" class="num-input">

              <label class="control-check span-4"><input type="checkbox" id="k-rts">
                Suavizador RTS hacia atras (no causal, usa toda la ventana)</label>
              <label class="control-check span-4"><input type="checkbox" id="k-post">
                Pasa-banda posterior sobre la salida del Kalman</label>
              <label for="k-post-low">Pasa-banda</label>
              <input type="number" id="k-post-low" step="0.5" min="0" max="500" class="num-input">
              <label for="k-post-high">a</label>
              <input type="number" id="k-post-high" step="5" min="0" max="1000" class="num-input">
            </div>
            <p class="note" id="k-help"></p>
            <p class="note">El RTS recorre la ventana hacia atras, asi que no sirve en tiempo real
            pero si en procesamiento diferido: quita el retardo de grupo del filtro causal. Es una
            eleccion explicita, no un default.</p>

            <div class="toolbar">
              <button type="button" id="k-save">Guardar preferencias</button>
              <button type="button" id="k-revert" class="btn-quiet">Volver a lo guardado</button>
            </div>
            <p class="note" id="k-saved"></p>
            <p class="note" id="k-diag"></p>
            <p class="note">Las preferencias son de la campaña y sólo afectan a esta vista previa
            y al overlay opcional de MASW. Contexto y límites del método en
            <code>geophone_scope/HANDOFF_KALMAN.md</code>.</p>
          </details>
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
                <span class="legend" id="f-band"></span>
                <span class="legend" id="f-env-label"></span></figcaption>
              <canvas class="plot" id="f-filt"></canvas>
            </figure>
            <figure class="plot-box" id="k-plot-box" hidden>
              <figcaption class="plot-title"><span class="lg lg-kalman"></span>Kalman
                <span class="legend" id="k-plot-label"></span></figcaption>
              <canvas class="plot" id="k-plot"></canvas>
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
  // Kalman: todo opcional. Mientras `kEnabled()` sea false no se pide nada al
  // servidor y la vista previa es exactamente la de siempre.
  let kCatalog = null;
  let kSettings = null;
  let kPreview = null;
  let abortReq = null;
  const frames = { orig: null, filt: null, spec: null, kalman: null };

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

  // ------------------------------------------------------------------ //
  // Deconvolucion Kalman: bloque enteramente opcional.
  // ------------------------------------------------------------------ //

  const kEl = (id) => root.querySelector(id);
  const kEnabled = () => !!kEl('#k-enabled')?.checked;

  function kFillSelect(sel, items, value) {
    if (!sel) return;
    sel.innerHTML = (items || []).map((it) =>
      `<option value="${it.id}">${it.name || it.id}</option>`).join('');
    if (value !== undefined && value !== null) sel.value = value;
  }

  function kParams() {
    return {
      geophone: kEl('#k-geo').value,
      conditioner: kEl('#k-cond').value,
      estimate: kEl('#k-estimate').value,
      input_model: kEl('#k-input').value,
      band_low_hz: Number(kEl('#k-band-low').value) || 10,
      band_high_hz: Number(kEl('#k-band-high').value) || 50,
      leak_hz: Number(kEl('#k-leak').value) || 0.7,
      disc_method: kEl('#k-disc').value,
      q_source: kEl('#k-q-source').value,
      q_scale: Number(kEl('#k-q').value) || 0,
      r_source: kEl('#k-r-source').value,
      r_var: Number(kEl('#k-r').value) || 0,
      smoother_enabled: kEl('#k-rts').checked,
      post_band_enabled: kEl('#k-post').checked,
      post_low_hz: Number(kEl('#k-post-low').value) || 0,
      post_high_hz: Number(kEl('#k-post-high').value) || 80,
    };
  }

  // Muestra solo los controles que el prior elegido usa: una banda para
  // `ou_band`, una fuga para `leaky_rw`. Un numero que no se aplica confunde.
  function kSyncControls() {
    const kind = kEl('#k-input')?.value;
    const model = (kCatalog?.input_models || []).find((m) => m.id === kind);
    const showBand = !!model?.needs_band;
    const showLeak = !!model?.needs_leak;
    const pairs = [['#k-band-lo-label', showBand], ['#k-band-low', showBand],
      ['#k-band-hi-label', showBand], ['#k-band-high', showBand],
      ['#k-leak-label', showLeak], ['#k-leak', showLeak]];
    for (const [id, on] of pairs) {
      const el = kEl(id);
      if (el) el.hidden = !on;
    }
    const qManual = kEl('#k-q-source')?.value === 'manual';
    const rManual = kEl('#k-r-source')?.value === 'manual';
    if (kEl('#k-q')) kEl('#k-q').disabled = !qManual;
    if (kEl('#k-r')) kEl('#k-r').disabled = !rManual;
    const post = kEl('#k-post')?.checked;
    for (const id of ['#k-post-low', '#k-post-high']) {
      const el = kEl(id);
      if (el) el.disabled = !post;
    }
    const help = kEl('#k-help');
    if (help) {
      const qMode = (kCatalog?.q_sources || [])
        .find((m) => m.id === kEl('#k-q-source')?.value);
      const partes = [model?.help, qMode?.help].filter(Boolean);
      help.textContent = partes.join(' ');
      help.className = model?.warn ? 'warn-note' : 'note';
    }
    const box = kEl('#k-plot-box');
    if (box) box.hidden = !kEnabled();
  }

  async function kLoad() {
    try {
      if (!kCatalog) {
        kCatalog = await fetch('/api/kalman/catalog', { cache: 'no-store' })
          .then((r) => r.json());
      }
    } catch (err) {
      kCatalog = { available: false, reason: String(err) };
    }
    if (!kCatalog.available) {
      // No disponible no es un error del usuario: se deshabilita y se explica.
      kEl('#k-unavailable').hidden = false;
      kEl('#k-unavailable').textContent =
        `No disponible en este entorno: ${kCatalog.reason || 'falta el paquete kalman_deconv'}. ` +
        'El resto de la pestana funciona igual.';
      kEl('#k-details').querySelectorAll('input, select, button')
        .forEach((el) => { el.disabled = true; });
      return;
    }
    try {
      kSettings = await fetch(
        `/api/kalman/settings?campaign=${encodeURIComponent(campaign)}`,
        { cache: 'no-store' }).then((r) => r.json());
    } catch (_) {
      kSettings = { ...(kCatalog.defaults || {}), revision: 'missing' };
    }
    kFillSelect(kEl('#k-geo'), kCatalog.geophones, kSettings.geophone);
    kFillSelect(kEl('#k-cond'), kCatalog.conditioners, kSettings.conditioner);
    kFillSelect(kEl('#k-estimate'), kCatalog.magnitudes, kSettings.estimate);
    kFillSelect(kEl('#k-input'), kCatalog.input_models, kSettings.input_model);
    kFillSelect(kEl('#k-disc'), kCatalog.discretizations, kSettings.disc_method);
    kFillSelect(kEl('#k-q-source'), kCatalog.q_sources, kSettings.q_source);
    kFillSelect(kEl('#k-r-source'), kCatalog.r_sources, kSettings.r_source);
    kEl('#k-enabled').checked = !!kSettings.enabled;
    kEl('#k-band-low').value = kSettings.band_low_hz ?? 10;
    kEl('#k-band-high').value = kSettings.band_high_hz ?? 50;
    kEl('#k-leak').value = kSettings.leak_hz ?? 0.7;
    kEl('#k-q').value = kSettings.q_scale ?? 0;
    kEl('#k-r').value = kSettings.r_var ?? 0;
    kEl('#k-rts').checked = !!kSettings.smoother_enabled;
    kEl('#k-post').checked = kSettings.post_band_enabled !== false;
    kEl('#k-post-low').value = kSettings.post_low_hz ?? 1;
    kEl('#k-post-high').value = kSettings.post_high_hz ?? 80;
    kEl('#k-saved').textContent = kSettings.path ? `preferencias en ${kSettings.path}` : '';
    // Si quedo prendido de una sesion anterior, se abre solo: si no, el
    // usuario veria el grafico Kalman sin encontrar de donde salio.
    if (kSettings.enabled) kEl('#k-details').open = true;
    kSyncControls();
  }

  async function kSave() {
    if (!kCatalog?.available) return;
    try {
      const res = await fetch('/api/kalman/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign, base_revision: kSettings?.revision || '',
          enabled: kEnabled(), ...kParams() }),
      });
      if (res.status === 409) {
        await kLoad();
        throw new Error('cambio el archivo; se recargo');
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      kSettings = await res.json();
      kEl('#k-saved').textContent = 'preferencias guardadas';
    } catch (err) {
      kEl('#k-saved').textContent = `no se pudo guardar: ${err}`;
    }
  }

  let kPedido = 0;

  async function kRefresh(row) {
    if (!kEnabled() || !kCatalog?.available || !row?.shot_id) {
      kPreview = null;
      return;
    }
    const mio = ++kPedido;
    const p = kParams();
    const q = new URLSearchParams({
      shot_id: row.shot_id,
      campaign: row.campaign || '',
      max_points: String(Math.max(200, Math.round(elOrig.clientWidth || 800))),
    });
    for (const [key, value] of Object.entries(p)) q.set(key, String(value));
    kEl('#k-diag').textContent = 'estimando...';
    try {
      const res = await fetch(`/api/kalman/preview?${q}`, { cache: 'no-store' });
      if (mio !== kPedido) return;
      if (!res.ok) {
        const detalle = await res.text();
        kEl('#k-diag').textContent =
          `no se pudo estimar (HTTP ${res.status}): ${detalle.slice(0, 200)}`;
        kPreview = null;
        render();
        return;
      }
      kPreview = await res.json();
      if (mio !== kPedido) return;
      const d = kPreview.diagnostics;
      const pl = kPreview.plant;
      const railed = d.railed_at_bound
        ? ' <strong>(pegado al borde: el modelo no ajusta)</strong>' : '';
      kEl('#k-diag').innerHTML =
        `planta ${pl.n_zeros}c/${pl.n_poles}p, ${pl.n_states} estados` +
        ` +${pl.n_states_augmented - pl.n_states} de entrada` +
        ` &middot; log10 q = ${fmt(d.log10_q_scale, 2)}${railed}` +
        ` &middot; NIS ${fmt(d.mean_nis, 2)}` +
        ` (pre ${fmt(d.mean_nis_pre_arrival, 3)} / evento ${fmt(d.mean_nis_event, 2)})` +
        ` &middot; min eig P = ${d.min_cov_eigenvalue}` +
        `<br>energia 10-50 Hz: medida ${fmt(d.frac_10_50_hz_measured, 3)}` +
        ` &rarr; Kalman ${fmt(d.frac_10_50_hz_post, 3)}` +
        ` &middot; deriva &lt;1 Hz: ${fmt(d.frac_below_1_hz, 4)}` +
        ` &rarr; ${fmt(d.frac_below_1_hz_post, 4)} tras el pasa-banda`;
      render();
    } catch (err) {
      if (mio === kPedido) kEl('#k-diag').textContent = `error: ${err}`;
    }
  }

  function kRenderPlot() {
    const box = kEl('#k-plot-box');
    if (!box) return;
    box.hidden = !kEnabled();
    if (box.hidden) return;
    const canvas = kEl('#k-plot');
    const color = cssVar('--sig-ok-avg', '#d62728');
    if (!kPreview) {
      frames.kalman = createFrame(canvas, { xMin: 0, xMax: 1, yMin: -1, yMax: 1,
        xLabel: 'tiempo relativo al hammer [s]', yLabel: 'suelo' });
      kEl('#k-plot-label').textContent = '';
      return;
    }
    const t = kPreview.time;
    const traza = t.kalman_post || t.kalman;
    if (!traza) return;
    const lo = traza.y_min ?? -1;
    const hi = traza.y_max ?? 1;
    const pad = Math.max((hi - lo) * 0.05, 1e-12);
    // Comparte el eje de tiempo con los otros dos marcos: si no, no se puede
    // comparar el arribo contra la senal medida.
    const eje = viewTiempo.apply({
      xMin: -0.08,
      xMax: Math.min((traza.t0 || 0) + traza.samples / (traza.fs || 1), 1.1),
      yMin: lo - pad, yMax: hi + pad,
      xLabel: 'tiempo relativo al hammer [s]',
      yLabel: `suelo [${kPreview.units || ''}]`,
    });
    frames.kalman = createFrame(canvas, eje);
    drawMinMax(frames.kalman, traza, { color, lineWidth: 1 });
    const a = kPreview.applied;
    kEl('#k-plot-label').textContent =
      `${kPreview.estimate} - ${a.input_model}` +
      (a.smoother_enabled ? ' - RTS' : ' - solo forward') +
      (a.post_band_enabled ? ` - pasa-banda ${a.post_low_hz}-${a.post_high_hz} Hz` : '');
  }

  function params() {
    return {
      low_hz: Number($('#f-low').value) || 0,
      high_hz: Number($('#f-high').value) || 0,
      order: Math.max(1, Math.min(10, Number($('#f-order').value) || 4)),
      target_fs: Number($('#f-fs').value) || 0,
      dc_enabled: $('#f-dc').checked,
      line_suppress_enabled: $('#f-line').checked,
      line_f0_hz: Number($('#f-line-f0').value) || 50,
      line_harmonics: Math.max(1, Math.min(12, Number($('#f-line-harm').value) || 3)),
      line_search_hz: Math.max(0, Number($('#f-line-search').value) || 0),
      include_envelope: $('#f-envelope').checked,
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
    const extra = [
      p.dc_enabled ? 'sin DC' : '',
      p.line_suppress_enabled
        ? `línea ${p.line_f0_hz}±${p.line_search_hz} Hz × ${p.line_harmonics}`
        : '',
    ].filter(Boolean).join(' · ');
    $('#f-hint-values').textContent =
      `${banda}, orden ${p.order} · fs común: ${fs}${extra ? ` · ${extra}` : ''}`;
  }

  function render() {
    const c = {
      orig: cssVar('--sig-overlay', 'rgba(120,120,120,.5)'),
      filt: cssVar('--sig-geo', '#0066cc'),
      env: cssVar('--sig-ok-avg', '#d62728'),
    };
    const vacio = (canvas, opts) => createFrame(canvas, {
      xMin: 0, xMax: 1, yMin: -1, yMax: 1,
      xLabel: 'tiempo relativo al hammer [s]', yLabel: 'Geo [V]', ...opts });

    if (!preview) {
      frames.orig = vacio(elOrig);
      frames.filt = vacio(elFilt);
      frames.spec = vacio(elSpec, { xMin: 1, xMax: 1000, yMin: 1e-3, yMax: 1,
        xLog: true, yLog: true, xLabel: 'frecuencia [Hz]', yLabel: '|FFT|' });
      kRenderPlot();
      return;
    }

    // Original y filtrada en marcos separados, uno sobre otro y con el MISMO
    // eje vertical: superpuestas se tapaban entre sí y no se podía ver qué
    // cambió. Compartir la escala es lo que permite comparar de un vistazo.
    const t = preview.time;
    const trazas = [t.original, t.filtered, t.envelope].filter(Boolean);
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
      if (t.envelope) {
        drawMinMax(frames.filt, t.envelope, { color: c.env, lineWidth: 1, alpha: 0.8 });
      }
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
    $('#f-env-label').textContent = t.envelope ? ' · envolvente Hilbert' : '';

    const estado = preview.enabled ? 'ACTIVO en promedios/export' : 'sólo vista previa (no aplicado)';
    const resamp = preview.resampled
      ? ` · resampleada ${fmt(preview.fs, 0)}→${fmt(preview.work_fs, 0)} Hz` : '';
    elMeta.innerHTML =
      `<strong>${preview.folder} / ${preview.capture}</strong>` +
      `<span class="meta-num">${fmt(preview.distance_m)} m</span>` +
      `<span class="meta-num">${fmt(preview.fs, 0)} Hz</span>` +
      `<span class="meta-num">guardado: filtro ${estado}${resamp}</span>`;

    // El brazo Kalman se dibuja al final y solo si esta prendido: con la
    // funcionalidad apagada esta llamada devuelve enseguida y la pestana se
    // comporta exactamente como antes de existir.
    kRenderPlot();
  }

  // ¿Los valores del formulario difieren de lo guardado? Mientras difieran, lo
  // que se ve es sólo una prueba y hay que decirlo.
  function marcarSucio() {
    if (!settings) return;
    const p = params();
    const sucio = ['low_hz', 'high_hz', 'order', 'target_fs', 'line_f0_hz',
      'line_harmonics', 'line_search_hz'].some(
      (k) => Number(p[k]) !== Number(settings[k])) || p.enabled !== !!settings.enabled;
    const flagsSucias = p.dc_enabled !== !!settings.dc_enabled
      || p.line_suppress_enabled !== !!settings.line_suppress_enabled;
    $('#f-dirty').hidden = !(sucio || flagsSucias);
    $('#f-save').classList.toggle('is-primary', sucio || flagsSucias);
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
      $('#f-dc').checked = !!settings.dc_enabled;
      $('#f-line').checked = !!settings.line_suppress_enabled;
      $('#f-line-f0').value = settings.line_f0_hz || 50;
      $('#f-line-harm').value = settings.line_harmonics || 3;
      $('#f-line-search').value = settings.line_search_hz ?? 2;
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
        body: JSON.stringify({ campaign, base_revision: settings?.revision || '', ...params() }),
      });
      if (res.status === 409) {
        await loadSettings();
        throw new Error('el archivo cambió desde PyQt; se recargaron sus valores');
      }
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
      dc_enabled: String(p.dc_enabled),
      line_suppress_enabled: String(p.line_suppress_enabled),
      line_f0_hz: String(p.line_f0_hz),
      line_harmonics: String(p.line_harmonics),
      line_search_hz: String(p.line_search_hz),
      include_envelope: String(p.include_envelope),
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
      // Independiente del pasa-banda: si esta apagado no cuesta nada.
      kRefresh(row);
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
  for (const id of ['#f-enabled', '#f-low', '#f-high', '#f-order', '#f-fs',
    '#f-dc', '#f-line', '#f-line-f0', '#f-line-harm', '#f-line-search',
    '#f-envelope']) {
    $(id).addEventListener('change', () => { renderHint(); marcarSucio(); refreshPreview(); });
  }
  // Los controles Kalman no tocan el pasa-banda: solo re-estiman su propio
  // brazo. Prender o apagar el maestro tampoco guarda nada por si solo.
  for (const id of ['#k-enabled', '#k-geo', '#k-cond', '#k-estimate', '#k-input',
    '#k-band-low', '#k-band-high', '#k-leak', '#k-disc', '#k-q-source', '#k-q',
    '#k-r-source', '#k-r', '#k-rts', '#k-post', '#k-post-low', '#k-post-high']) {
    const el = $(id);
    if (el) {
      el.addEventListener('change', () => {
        kSyncControls();
        render();
        resolverFila().then((row) => kRefresh(row));
      });
    }
  }
  $('#k-save')?.addEventListener('click', kSave);
  $('#k-revert')?.addEventListener('click', () => {
    kLoad().then(() => { render(); resolverFila().then((row) => kRefresh(row)); });
  });

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
  kLoad();

  return {
    // Al volver a la pestaña puede haber cambiado la captura elegida en Capturas.
    resume() { filaCache = null; kLoad(); loadSettings().then(refreshPreview); },
    destroy() { viewTiempo.destroy(); viewSpec.destroy(); ro.disconnect(); mo.disconnect(); },
  };
}
