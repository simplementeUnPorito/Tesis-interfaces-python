// Flujo MASW completo: dispersión multimodal, inversión cancelable y perfil Vs.
import { createFrame, attachViewControls } from '../plot.js';
import { mountCampaignPicker } from '../campaign_picker.js';

const COLORS = ['#ff3b30', '#34c759', '#0a84ff', '#ff9f0a', '#bf5af2', '#ffd60a'];
const STOPS = [
  [0, [0, 0, 130]], [.28, [0, 90, 255]], [.48, [0, 190, 90]],
  [.68, [255, 230, 0]], [.86, [255, 0, 0]], [1, [90, 0, 0]],
];
const LUT = (() => {
  const lut = new Uint8ClampedArray(768);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let k = 0;
    while (k < STOPS.length - 2 && t > STOPS[k + 1][0]) k++;
    const [a, ca] = STOPS[k], [b, cb] = STOPS[k + 1];
    const u = (t - a) / (b - a);
    for (let c = 0; c < 3; c++) lut[i * 3 + c] = ca[c] + (cb[c] - ca[c]) * u;
  }
  return lut;
})();
const fmt = (v, n = 2) => Number.isFinite(Number(v)) ? Number(v).toFixed(n) : '—';

async function colorImage(base64, width, height) {
  const blob = await fetch(`data:image/png;base64,${base64}`).then((r) => r.blob());
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(bitmap, 0, 0);
  const image = ctx.getImageData(0, 0, width, height);
  for (let i = 0; i < image.data.length; i += 4) {
    const p = image.data[i] * 3;
    image.data[i] = LUT[p]; image.data[i + 1] = LUT[p + 1];
    image.data[i + 2] = LUT[p + 2]; image.data[i + 3] = 255;
  }
  ctx.putImageData(image, 0, 0);
  return canvas;
}

function activate(root, name) {
  root.querySelectorAll('.subtab-btn').forEach((b) =>
    b.classList.toggle('active', b.dataset.subtab === name));
  root.querySelectorAll('.subtab-panel').forEach((p) =>
    p.hidden = p.id !== `subpanel-${name}`);
}

export function mount(root) {
  const dispersionPanel = root.querySelector('#subpanel-dispersion');
  const inversionPanel = root.querySelector('#subpanel-inversion');
  const profilePanel = root.querySelector('#subpanel-perfil');
  dispersionPanel.innerHTML = `
    <div class="workspace"><div class="ws-left">
      <div class="card" id="mw-campaign"></div>
      <div class="card"><h2>Dispersión</h2>
        <div class="mark-grid">
          <label>c mín</label><input id="mw-cmin" class="num-input" type="number" value="50">
          <label>c máx</label><input id="mw-cmax" class="num-input" type="number" value="800">
          <label>Δc</label><input id="mw-cstep" class="num-input" type="number" value="2" step=".5">
          <label>f mín</label><input id="mw-fmin" class="num-input" type="number" value="1">
          <label>f máx</label><input id="mw-fmax" class="num-input" type="number" value="100">
          <label>pick f mín</label><input id="mw-pfmin" class="num-input" type="number" value="3">
          <label>pick f máx</label><input id="mw-pfmax" class="num-input" type="number" value="80">
        </div>
        <div id="mw-weights"></div>
        <label class="control-check"><input id="mw-freq-log" type="checkbox"> Frecuencia log</label>
        <label class="control-check"><input id="mw-int-log" type="checkbox"> Intensidad log</label>
        <label class="control-check"><input id="mw-int-freq" type="checkbox" checked> Normalizar por frecuencia</label>
        <label class="control-check"><input id="mw-kalman" type="checkbox">
          Ventana Kalman <span class="legend">opcional</span></label>
        <p class="note" id="mw-kalman-note" hidden></p>
        <div class="toolbar"><button id="mw-calc">Calcular / combinar</button>
          <button id="mw-save" class="btn-quiet">Guardar análisis</button></div>
        <p id="mw-status" class="note"></p>
      </div>
      <div class="card"><h2>Curvas y regiones</h2>
        <div class="field-row"><label>Modo</label><select id="mw-mode"></select>
          <button id="mw-add-mode" class="btn-sm">+ modo</button></div>
        <div class="toolbar">
          <button id="mw-tool-pick">Agregar/mover pick</button>
          <button id="mw-tool-delete" class="btn-quiet">Borrar pick</button>
          <button id="mw-tool-region" class="btn-quiet">Dibujar región</button>
          <button id="mw-close-region" class="btn-quiet">Cerrar región</button>
        </div>
        <div class="toolbar"><button id="mw-auto">Auto-pick</button>
          <button id="mw-clear" class="btn-quiet">Limpiar modo</button>
          <button id="mw-inversion">Ir a inversión</button></div>
        <p class="note">Líneas punteadas: piso anti-alias c≥2·dx·f y techo λ≤L.</p>
      </div>
    </div><div class="ws-right"><section class="card viewer">
      <div class="viewer-head"><h2>Frecuencia — velocidad de fase</h2>
        <div id="mw-meta" class="viewer-meta">sin calcular</div></div>
      <figure class="plot-box"><figcaption class="plot-title">Dispersión
        <span id="mw-coord" class="legend"></span></figcaption>
        <canvas id="mw-plot" class="plot masw-plot"></canvas></figure>
    </section></div></div>`;
  inversionPanel.innerHTML = `
    <div class="workspace"><div class="ws-left">
      <section class="card"><h2>Inversión</h2>
        <div class="field-row"><label>Motor</label><select id="mi-backend"></select></div>
        <p id="mi-backend-status" class="note"></p>
        <div class="mark-grid">
          <label>Capas</label><input id="mi-layers" class="num-input" type="number" value="4" min="2">
          <label>Iteraciones</label><input id="mi-iter" class="num-input" type="number" value="1000" min="1">
          <label>Semilla</label><input id="mi-seed" class="num-input" type="number" value="42">
          <label>bs</label><input id="mi-bs" class="num-input" type="number" value="8" step=".5">
          <label>bh</label><input id="mi-bh" class="num-input" type="number" value="12" step=".5">
          <label>ν</label><input id="mi-nu" class="num-input" type="number" value=".35" step=".01">
          <label>ρ</label><input id="mi-rho" class="num-input" type="number" value="1850">
        </div>
        <label id="mi-launch-wrap" class="note" hidden>
          <input id="mi-launch-geopsy" type="checkbox">
          Abrir Geopsy al terminar (sólo Windows, si ya está instalado)
        </label>
        <div class="toolbar"><button id="mi-run">Correr inversión</button>
          <button id="mi-stop" disabled>Detener</button></div>
        <progress id="mi-progress" max="1" value="0"></progress>
        <p id="mi-status" class="note">Definí al menos 3 picks en Dispersión.</p>
        <div id="mi-artifacts"></div>
      </section>
    </div><div class="ws-right"><section class="card viewer">
      <div class="viewer-head"><h2>Curvas observadas</h2><div id="mi-meta" class="viewer-meta"></div></div>
      <figure class="plot-box"><canvas id="mi-plot" class="plot"></canvas></figure>
    </section></div></div>`;
  profilePanel.innerHTML = `
    <div class="workspace"><div class="ws-left"><section class="card">
      <h2>Perfil Vs</h2>
      <p class="note">El resultado original es inmutable. La tabla es una copia editable
      que conserva la procedencia de la inversión.</p>
      <div class="wrap"><table class="rows-table"><thead><tr><th>Capa</th>
        <th>Vs [m/s]</th><th>Espesor [m]</th></tr></thead><tbody id="mp-rows"></tbody></table></div>
      <div class="toolbar"><button id="mp-save">Guardar perfil editado</button>
        <button id="mp-reset" class="btn-quiet">Restaurar original</button>
        <button id="mp-csv" class="btn-quiet">Descargar CSV</button></div>
      <p id="mp-status" class="note"></p>
    </section></div><div class="ws-right"><section class="card viewer">
      <div class="viewer-head"><h2>Vs — profundidad</h2><div id="mp-meta" class="viewer-meta"></div></div>
      <figure class="plot-box"><canvas id="mp-plot" class="plot"></canvas></figure>
    </section></div></div>`;

  const $ = (q) => root.querySelector(q);
  let campaign = '', groupCount = 1, state = { revision: 'missing', masw: {}, backends: [] };
  let dispersion = null, image = null, frame = null, activeMode = 0;
  // Ventana del segundo Kalman: overlay OPCIONAL. Nace apagado y, mientras lo
  // este, no se pide nada al servidor ni se dibuja nada.
  let kalmanWindow = null, kalmanBusy = false;
  let tool = 'pick', regionDraft = [], drag = null, currentJob = null, pollTimer = null;
  let pedidoCarga = 0, pollFailures = 0;
  const view = attachViewControls($('#mw-plot'), {
    getFrame: () => frame, onChange: drawDispersion,
    leftPan: () => tool === 'view',
  });
  const picker = mountCampaignPicker($('#mw-campaign'), {
    onChange: (id) => {
      campaign = id;
      loadAll().catch((err) => {
        $('#mw-status').textContent = `no se pudo cargar la campaña: ${err}`;
      });
    },
  });

  const imageParams = () => ({
    c_min: Number($('#mw-cmin').value) || 50, c_max: Number($('#mw-cmax').value) || 800,
    c_step: Number($('#mw-cstep').value) || 2, f_min: Number($('#mw-fmin').value) || 1,
    f_max: Number($('#mw-fmax').value) || 100,
  });
  const weights = () => Object.fromEntries([...root.querySelectorAll('.mw-weight')]
    .map((el) => [el.dataset.group, Math.max(0, Number(el.value) || 0)]));
  const masw = () => state.masw || (state.masw = {});
  const picks = () => {
    const all = masw().picks_by_mode || (masw().picks_by_mode = { '0': [] });
    return all[String(activeMode)] || (all[String(activeMode)] = []);
  };
  const regions = () => {
    const all = masw().regions_by_mode || (masw().regions_by_mode = { '0': [] });
    return all[String(activeMode)] || (all[String(activeMode)] = []);
  };

  async function loadAll({ consumeRequest = true } = {}) {
    if (!campaign) return false;
    const campaignPedida = campaign;
    const mio = ++pedidoCarga;
    $('#mw-status').textContent = 'cargando estado compartido…';
    const [stateResponse, groupsResponse] = await Promise.all([
      fetch(`/api/masw/state?campaign=${encodeURIComponent(campaignPedida)}`, { cache: 'no-store' }),
      fetch(`/api/groups?campaign=${encodeURIComponent(campaignPedida)}`, { cache: 'no-store' }),
    ]);
    const [s, g] = await Promise.all([stateResponse.json(), groupsResponse.json()]);
    if (!stateResponse.ok) throw new Error(s.detail || `HTTP ${stateResponse.status}`);
    if (!groupsResponse.ok) throw new Error(g.detail || `HTTP ${groupsResponse.status}`);
    if (mio !== pedidoCarga || campaignPedida !== campaign) return false;
    state = s; groupCount = Math.max(1, Number(g.group_count) || 1);
    applyStateToControls(); renderWeights(); renderModes(); renderBackends();
    renderInversion(); renderProfile(); drawDispersion();
    $('#mw-status').textContent = `estado ${state.revision === 'missing' ? 'nuevo' : 'restaurado'} · ${state.path}`;
    if (consumeRequest) await consumeWaterfallRequest();
    return true;
  }

  function applyStateToControls() {
    const m = masw(), p = m.image_params || {}, inv = m.inversion_params || {};
    $('#mw-cmin').value = p.cmin ?? p.c_min ?? 50; $('#mw-cmax').value = p.cmax ?? p.c_max ?? 800;
    $('#mw-cstep').value = p.cstep ?? p.c_step ?? 2; $('#mw-fmin').value = p.fmin ?? p.f_min ?? 1;
    $('#mw-fmax').value = p.fmax ?? p.f_max ?? 100;
    $('#mw-pfmin').value = m.pick_range?.fmin ?? 3; $('#mw-pfmax').value = m.pick_range?.fmax ?? 80;
    $('#mw-freq-log').checked = !!m.display_options?.freq_log;
    $('#mw-int-log').checked = !!m.display_options?.intensity_log;
    $('#mw-int-freq').checked = m.display_options?.intensity_per_freq !== false;
    activeMode = Number(m.active_mode) || 0;
    $('#mi-layers').value = inv.nlayers ?? 4; $('#mi-iter').value = inv.niter ?? 1000;
    $('#mi-bs').value = inv.bs ?? 8; $('#mi-bh').value = inv.bh ?? 12;
    $('#mi-nu').value = inv.nu ?? .35; $('#mi-rho').value = inv.rho ?? 1850;
  }

  function renderWeights() {
    const stored = masw().group_weights || {};
    $('#mw-weights').innerHTML = `<h3>Pesos por grupo</h3>` + Array.from({ length: groupCount }, (_, i) => {
      const id = i + 1;
      return `<div class="field-row"><label>Grupo ${id}</label>
        <input class="num-input mw-weight" data-group="${id}" type="number"
          min="0" max="10" step=".05" value="${stored[id] ?? 1}"></div>`;
    }).join('');
  }
  function renderModes() {
    const ids = new Set([0, ...Object.keys(masw().picks_by_mode || {}).map(Number),
      ...Object.keys(masw().regions_by_mode || {}).map(Number)]);
    $('#mw-mode').innerHTML = [...ids].sort((a, b) => a - b).map((id) =>
      `<option value="${id}"${id === activeMode ? ' selected' : ''}>M${id}</option>`).join('');
  }
  function renderBackends() {
    const requested = masw().backend || 'maswavespy';
    const usable = (state.backends || []).filter((b) => b.available || b.kind === 'export');
    const requestedInfo = (state.backends || []).find((b) => b.key === requested);
    const current = requestedInfo ? requested : (usable[0]?.key || requested);
    $('#mi-backend').innerHTML = (state.backends || []).map((b) =>
      `<option value="${b.key}"${b.key === current ? ' selected' : ''}>${b.label}</option>`).join('');
    backendStatus();
  }
  function backendStatus() {
    const item = (state.backends || []).find((b) => b.key === $('#mi-backend').value);
    $('#mi-backend-status').textContent = item ? `${item.status} · ${item.kind}` : '';
    const canLaunch = item?.can_launch === true;
    $('#mi-launch-wrap').hidden = !canLaunch;
    if (!canLaunch) $('#mi-launch-geopsy').checked = false;
    const pickCount = Object.values(masw().picks_by_mode || {}).flat().length;
    const usable = !!item && (item.available || item.kind === 'export');
    $('#mi-run').disabled = !!currentJob || !usable || pickCount < 3;
  }

  function statePatch() {
    const p = imageParams();
    return {
      image_params: { cmin: p.c_min, cmax: p.c_max, cstep: p.c_step, fmin: p.f_min, fmax: p.f_max },
      pick_range: { fmin: Number($('#mw-pfmin').value), fmax: Number($('#mw-pfmax').value) },
      inversion_params: {
        nlayers: Number($('#mi-layers').value), niter: Number($('#mi-iter').value),
        bs: Number($('#mi-bs').value), bh: Number($('#mi-bh').value),
        nu: Number($('#mi-nu').value), rho: Number($('#mi-rho').value),
      },
      active_mode: activeMode, picks_by_mode: masw().picks_by_mode || { '0': [] },
      regions_by_mode: masw().regions_by_mode || { '0': [] }, group_weights: weights(),
      display_options: { freq_log: $('#mw-freq-log').checked,
        intensity_log: $('#mw-int-log').checked, intensity_per_freq: $('#mw-int-freq').checked },
      backend: $('#mi-backend').value || 'maswavespy',
    };
  }
  async function saveState(extra = {}) {
    const response = await fetch('/api/masw/state', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ campaign, base_revision: state.revision, masw: { ...statePatch(), ...extra } }),
    });
    if (response.status === 409) {
      await loadAll({ consumeRequest: false });
      throw new Error('el estado cambió desde PyQt; se recargó');
    }
    const out = await response.json();
    if (!response.ok) throw new Error(out.detail || `HTTP ${response.status}`);
    state = out; return out;
  }

  async function calculate() {
    $('#mw-calc').disabled = true; $('#mw-status').textContent = 'calculando dispersión…';
    try {
      const response = await fetch('/api/masw/dispersion', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign, base_revision: state.revision,
          params: imageParams(), group_weights: weights(),
          intensity_log: $('#mw-int-log').checked, intensity_per_freq: $('#mw-int-freq').checked }),
      });
      const out = await response.json();
      if (response.status === 409) {
        await loadAll({ consumeRequest: false });
        throw new Error('el estado MASW cambió; se recargó');
      }
      if (!response.ok) throw new Error(out.detail || `HTTP ${response.status}`);
      dispersion = out; image = await colorImage(out.image_png, out.width, out.height);
      if (out.state) state = out.state;
      masw().geophone_spacing_m = out.geophone_spacing_m;
      masw().array_length_m = out.array_length_m;
      $('#mw-meta').textContent = `${out.groups.length} grupo(s) · ${out.width}×${out.height} · dx ${fmt(out.geophone_spacing_m)} m · L ${fmt(out.array_length_m)} m`;
      $('#mw-status').textContent = 'imagen combinada lista';
      await saveState();
      drawDispersion();
      // La ventana depende de la imagen: si el overlay esta prendido, se
      // recalcula sola. Si esta apagado, esta llamada no hace nada.
      loadKalmanWindow();
      return true;
    } catch (err) {
      $('#mw-status').textContent = `falló: ${err}`;
      return false;
    }
    finally { $('#mw-calc').disabled = false; }
  }

  // Se pide una sola vez por combinacion de parametros: el calculo recorre la
  // imagen entera y tarda varios segundos.
  async function loadKalmanWindow() {
    if (!$('#mw-kalman')?.checked) { kalmanWindow = null; return; }
    if (kalmanBusy) return;
    kalmanBusy = true;
    const note = $('#mw-kalman-note');
    note.hidden = false;
    note.textContent = 'calculando la ventana...';
    try {
      const res = await fetch('/api/kalman/masw-window', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign, params: imageParams(),
          group_weights: weights() }),
      });
      if (!res.ok) {
        const detalle = await res.text();
        note.textContent = res.status === 503
          ? 'La deconvolucion Kalman no esta disponible en este entorno.'
          : `no se pudo calcular (HTTP ${res.status}): ${detalle.slice(0, 160)}`;
        kalmanWindow = null;
        return;
      }
      kalmanWindow = await res.json();
      const r = kalmanWindow.nonempty_frequency_range_hz;
      note.innerHTML = r
        ? `region admisible ${fmt(r[0])}-${fmt(r[1])} Hz` +
          ` (${kalmanWindow.nonempty_rows}/${kalmanWindow.total_rows} filas,` +
          ` ${(100 * kalmanWindow.grid_fraction.combined).toFixed(1)} % de la grilla).` +
          ' <strong>Es una region, no un picking.</strong>'
        : 'la interseccion quedo vacia: no hay region admisible con estos parametros.';
    } catch (err) {
      note.textContent = `error: ${err}`;
      kalmanWindow = null;
    } finally {
      kalmanBusy = false;
      drawDispersion();
    }
  }

  function drawDispersion() {
    const p = dispersion || { f_min: Number($('#mw-fmin')?.value) || 1,
      f_max: Number($('#mw-fmax')?.value) || 100, c_min: Number($('#mw-cmin')?.value) || 50,
      c_max: Number($('#mw-cmax')?.value) || 800 };
    frame = createFrame($('#mw-plot'), view.apply({ xMin: p.f_min, xMax: p.f_max,
      yMin: p.c_min, yMax: p.c_max, xLog: !!$('#mw-freq-log')?.checked,
      xLabel: 'frecuencia [Hz]', yLabel: 'velocidad de fase [m/s]' }));
    const ctx = frame.ctx;
    if (image) {
      ctx.save(); ctx.beginPath(); ctx.rect(frame.x0, frame.y0, frame.pw, frame.ph); ctx.clip();
      if (!frame.xLog) {
        ctx.drawImage(image, frame.xOf(p.f_min), frame.yOf(p.c_max),
          frame.xOf(p.f_max) - frame.xOf(p.f_min), frame.yOf(p.c_min) - frame.yOf(p.c_max));
      } else {
        for (let x = 0; x < image.width; x++) {
          const fa = p.f_min + (x / image.width) * (p.f_max - p.f_min);
          const fb = p.f_min + ((x + 1) / image.width) * (p.f_max - p.f_min);
          ctx.drawImage(image, x, 0, 1, image.height, frame.xOf(fa), frame.yOf(p.c_max),
            Math.max(1, frame.xOf(fb) - frame.xOf(fa)), frame.yOf(p.c_min) - frame.yOf(p.c_max));
        }
      }
      ctx.restore();
    }
    line(dispersion?.alias_boundary || [], '#ffffff', [5, 4]);
    line(dispersion?.lambda_boundary || [], '#111111', [5, 4]);
    // Envolvente de la region admisible del segundo Kalman. Son DOS bordes,
    // no una curva: acotan donde puede estar la dispersion, no dicen donde
    // esta. Por eso se dibujan punteados y nunca se exportan como picking.
    if ($('#mw-kalman')?.checked && kalmanWindow) {
      // Halo oscuro debajo: sobre la paleta arcoiris un trazo fino de un solo
      // color se pierde, y el borde de la region es justo lo que hay que ver.
      for (const pts of [kalmanWindow.lower || [], kalmanWindow.upper || []]) {
        line(pts, 'rgba(0,0,0,.85)', [], 3.5);
        line(pts, '#00e5ff', [3, 3], 1.6);
      }
    }
    Object.entries(masw().regions_by_mode || {}).forEach(([mode, polys]) =>
      (polys || []).forEach((poly) => line([...poly, poly[0]], COLORS[Number(mode) % COLORS.length], [6, 3])));
    if (regionDraft.length) line(regionDraft, COLORS[activeMode % COLORS.length], [3, 2]);
    Object.entries(masw().picks_by_mode || {}).forEach(([mode, pts]) =>
      (pts || []).forEach((pt) => dot(pt, COLORS[Number(mode) % COLORS.length], Number(mode) === activeMode ? 4 : 3)));
  }
  function line(points, color, dash = [], width = 1.5) {
    if (!frame || !points.length) return;
    const ctx = frame.ctx; ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = width;
    ctx.setLineDash(dash); ctx.beginPath();
    points.forEach((p, i) => (i ? ctx.lineTo(frame.xOf(p[0]), frame.yOf(p[1]))
      : ctx.moveTo(frame.xOf(p[0]), frame.yOf(p[1])))); ctx.stroke(); ctx.restore();
  }
  function dot(point, color, radius) {
    const ctx = frame.ctx; ctx.save(); ctx.fillStyle = color; ctx.strokeStyle = '#fff';
    ctx.beginPath(); ctx.arc(frame.xOf(point[0]), frame.yOf(point[1]), radius, 0, Math.PI * 2);
    ctx.fill(); ctx.stroke(); ctx.restore();
  }
  function dataAt(ev) {
    const rect = $('#mw-plot').getBoundingClientRect(), x = ev.clientX - rect.left, y = ev.clientY - rect.top;
    const u = (x - frame.x0) / frame.pw, v = (frame.y0 + frame.ph - y) / frame.ph;
    const f = frame.xLog ? 10 ** (Math.log10(frame.xMin) + u * (Math.log10(frame.xMax) - Math.log10(frame.xMin)))
      : frame.xMin + u * (frame.xMax - frame.xMin);
    return [f, frame.yMin + v * (frame.yMax - frame.yMin)];
  }
  function nearest(point) {
    let best = null, distance = Infinity;
    picks().forEach((p, i) => {
      const d = Math.hypot(frame.xOf(p[0]) - frame.xOf(point[0]), frame.yOf(p[1]) - frame.yOf(point[1]));
      if (d < distance) { best = i; distance = d; }
    });
    return distance <= 14 ? best : null;
  }

  $('#mw-plot').addEventListener('pointerdown', (ev) => {
    if (!frame || ev.button !== 0 || tool === 'view') return;
    const point = dataAt(ev), index = nearest(point);
    if (tool === 'region') { regionDraft.push(point); drawDispersion(); return; }
    if (tool === 'delete') { if (index !== null) picks().splice(index, 1); drawDispersion(); return; }
    if (index === null) { picks().push(point); picks().sort((a, b) => a[0] - b[0]); drag = nearest(point); }
    else drag = index;
    $('#mw-plot').setPointerCapture(ev.pointerId); drawDispersion();
  });
  $('#mw-plot').addEventListener('pointermove', (ev) => {
    if (!frame) return; const point = dataAt(ev);
    $('#mw-coord').textContent = `${fmt(point[0])} Hz · ${fmt(point[1], 0)} m/s`;
    if (drag !== null) { picks()[drag] = point; drawDispersion(); }
  });
  $('#mw-plot').addEventListener('pointerup', async () => {
    if (drag !== null) { picks().sort((a, b) => a[0] - b[0]); drag = null; await saveState().catch((e) => $('#mw-status').textContent = e); }
  });

  async function autoPick() {
    $('#mw-status').textContent = 'auto-pick…';
    const response = await fetch('/api/masw/auto-pick', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ campaign, params: imageParams(), group_weights: weights(),
        regions_by_mode: masw().regions_by_mode || { '0': [] },
        pick_fmin: Number($('#mw-pfmin').value), pick_fmax: Number($('#mw-pfmax').value) }),
    });
    const out = await response.json();
    if (!response.ok) throw new Error(out.detail || `HTTP ${response.status}`);
    masw().picks_by_mode = { ...(masw().picks_by_mode || {}), ...out.picks_by_mode };
    await saveState(); drawDispersion(); renderInversion();
    $('#mw-status').textContent = 'curvas picadas y guardadas';
  }

  function renderInversion() {
    const canvas = $('#mi-plot');
    const all = Object.values(masw().picks_by_mode || {}).flat();
    const fValues = all.map((p) => Number(p[0])).filter(Number.isFinite);
    const cValues = all.map((p) => Number(p[1])).filter(Number.isFinite);
    const fmin = fValues.length ? Math.min(...fValues) : 1;
    const fmax = fValues.length ? Math.max(...fValues) : 100;
    const cmin = cValues.length ? Math.min(...cValues) : 0;
    const cmax = cValues.length ? Math.max(...cValues) : 800;
    const fpad = Math.max((fmax - fmin) * .08, .5);
    const cpad = Math.max((cmax - cmin) * .12, 10);
    const fr = createFrame(canvas, {
      xMin: Math.max(0, fmin - fpad), xMax: fmax + fpad,
      yMin: Math.max(0, cmin - cpad), yMax: cmax + cpad,
      xLabel: 'frecuencia [Hz]', yLabel: 'c [m/s]' });
    Object.entries(masw().picks_by_mode || {}).forEach(([mode, pts]) => {
      if (!pts.length) return; const ctx = fr.ctx; ctx.save(); ctx.strokeStyle = COLORS[Number(mode) % COLORS.length];
      ctx.lineWidth = 2; ctx.beginPath(); pts.forEach((p, i) => i ? ctx.lineTo(fr.xOf(p[0]), fr.yOf(p[1]))
        : ctx.moveTo(fr.xOf(p[0]), fr.yOf(p[1]))); ctx.stroke(); ctx.restore();
    });
    $('#mi-meta').textContent = `${all.length} picks · ${Object.keys(masw().picks_by_mode || {}).length} modo(s)`;
    if (!currentJob) {
      $('#mi-status').textContent = all.length >= 3
        ? `${all.length} picks listos para invertir`
        : 'Definí al menos 3 picks en Dispersión.';
    }
    backendStatus();
  }
  function inversionParams() {
    const layers = Number($('#mi-layers').value), iterations = Number($('#mi-iter').value);
    return { n_layers: layers, n_iter: iterations, maxiter: iterations,
      popsize: Math.max(8, Math.min(100, Math.round(Math.sqrt(iterations)))),
      seed: Number($('#mi-seed').value), bs: Number($('#mi-bs').value),
      bh: Number($('#mi-bh').value), nu: Number($('#mi-nu').value), rho: Number($('#mi-rho').value) };
  }
  async function runInversion() {
    await saveState();
    const response = await fetch('/api/masw/inversions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ campaign, backend: $('#mi-backend').value,
        base_revision: state.revision,
        curves_by_mode: masw().picks_by_mode || {}, params: inversionParams(),
        launch: $('#mi-launch-geopsy').checked }),
    });
    const out = await response.json(); if (!response.ok) throw new Error(out.detail || `HTTP ${response.status}`);
    currentJob = out.id; $('#mi-stop').disabled = false; $('#mi-run').disabled = true;
    pollFailures = 0;
    $('#mi-status').textContent = `inversión encolada · ${currentJob}`;
    void pollJob();
  }
  async function pollJob() {
    if (!currentJob) return;
    const jobId = currentJob;
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
        cache: 'no-store',
      });
      const job = await response.json();
      if (!response.ok) throw new Error(job.detail || `HTTP ${response.status}`);
      if (jobId !== currentJob) return;
      pollFailures = 0;
      $('#mi-progress').value = job.progress || 0;
      $('#mi-status').textContent =
        `${job.state} · ${job.stage}${job.error ? ` · ${job.error}` : ''}`;
      if (job.state === 'listo') {
        const result = job.result || {};
        $('#mi-run').disabled = false;
        $('#mi-stop').disabled = true;
        $('#mi-artifacts').innerHTML = (result.artifacts || []).map((a) =>
          `<a class="artifact-link" href="/api/artifacts/${encodeURIComponent(a.id)}">${a.name}</a>`).join(' ');
        currentJob = null;
        await loadAll({ consumeRequest: false });
        if (result.persisted === false) {
          $('#mi-status').textContent =
            `inversión terminada; el estado cambió y el resultado quedó sólo en los artefactos · ${result.persistence_error || ''}`;
          activate(root, 'inversion');
        } else {
          activate(root, 'perfil');
        }
        return;
      }
      if (['error', 'cancelado'].includes(job.state)) {
        $('#mi-run').disabled = false;
        $('#mi-stop').disabled = true;
        currentJob = null;
        return;
      }
      pollTimer = setTimeout(() => void pollJob(), 800);
    } catch (err) {
      if (jobId !== currentJob) return;
      pollFailures += 1;
      const espera = Math.min(5000, 800 * (2 ** Math.min(pollFailures - 1, 3)));
      $('#mi-status').textContent =
        `sin conexión al trabajo; reintentando en ${(espera / 1000).toFixed(1)} s · ${err}`;
      // No se limpia currentJob ni se habilita «Correr»: el proceso puede
      // seguir vivo en el servidor y el siguiente poll recupera su resultado.
      pollTimer = setTimeout(() => void pollJob(), espera);
    }
  }

  function profileData() {
    const original = { beta: (state.result_arrays?.beta || []).filter((v) => v !== null),
      h: (state.result_arrays?.h || []).filter((v) => v !== null) };
    return { original, edited: masw().edited_profile || original };
  }
  function renderProfile() {
    const { original, edited } = profileData();
    $('#mp-rows').innerHTML = (edited.beta || []).map((vs, i) => `<tr>
      <td>${i + 1}</td><td><input class="num-input mp-vs" data-i="${i}" type="number" value="${vs}"></td>
      <td>${i < (edited.h || []).length ? `<input class="num-input mp-h" data-i="${i}" type="number" value="${edited.h[i]}">` : 'semiespacio'}</td></tr>`).join('');
    drawProfile(original, edited);
    const scalars = masw().inv_scalars || {};
    $('#mp-meta').textContent = original.beta.length ? `${original.beta.length} capas · misfit ${fmt(scalars.misfit)} % · ${scalars.engine || masw().backend || ''}` : 'sin inversión';
  }
  function drawProfile(original, edited) {
    const canvas = $('#mp-plot'), allVs = [...(original.beta || []), ...(edited.beta || [])];
    const depths = (profile) => (profile.h || []).reduce((a, h) => [...a, a[a.length - 1] + Number(h)], [0]);
    const maxDepth = Math.max(...depths(original), ...depths(edited), 10);
    const fr = createFrame(canvas, { xMin: Math.min(...allVs, 0) * .9, xMax: Math.max(...allVs, 800) * 1.1,
      yMin: 0, yMax: maxDepth * 1.2, xLabel: 'Vs [m/s]', yLabel: 'profundidad [m]' });
    const step = (profile, color, dash) => {
      if (!profile.beta?.length) return; const z = depths(profile), ctx = fr.ctx;
      ctx.save(); ctx.strokeStyle = color; ctx.setLineDash(dash); ctx.lineWidth = 2; ctx.beginPath();
      ctx.moveTo(fr.xOf(profile.beta[0]), fr.yOf(0));
      profile.beta.forEach((vs, i) => {
        const bottom = z[Math.min(i + 1, z.length - 1)] ?? maxDepth * 1.15;
        ctx.lineTo(fr.xOf(vs), fr.yOf(bottom));
        if (i + 1 < profile.beta.length) ctx.lineTo(fr.xOf(profile.beta[i + 1]), fr.yOf(bottom));
      }); ctx.stroke(); ctx.restore();
    };
    step(original, '#888', [6, 4]); step(edited, '#2ecc71', []);
  }
  function editedFromTable() {
    return { beta: [...root.querySelectorAll('.mp-vs')].map((e) => Number(e.value)),
      h: [...root.querySelectorAll('.mp-h')].map((e) => Number(e.value)) };
  }

  root.querySelector('#masw-subtabs').addEventListener('click', (ev) => {
    const button = ev.target.closest('.subtab-btn'); if (button) { activate(root, button.dataset.subtab); renderInversion(); renderProfile(); }
  });
  $('#mw-calc').addEventListener('click', calculate);
  $('#mw-save').addEventListener('click', () => saveState().then(() => $('#mw-status').textContent = 'guardado').catch((e) => $('#mw-status').textContent = e));
  $('#mw-freq-log').addEventListener('change', drawDispersion);
  // Prender el overlay lo calcula; apagarlo lo borra y no deja pedidos colgando.
  $('#mw-kalman').addEventListener('change', () => {
    if ($('#mw-kalman').checked) {
      loadKalmanWindow();
    } else {
      kalmanWindow = null;
      $('#mw-kalman-note').hidden = true;
      drawDispersion();
    }
  });
  $('#mw-mode').addEventListener('change', (ev) => { activeMode = Number(ev.target.value); masw().active_mode = activeMode; drawDispersion(); });
  $('#mw-add-mode').addEventListener('click', () => {
    const ids = Object.keys(masw().picks_by_mode || {}).map(Number); activeMode = Math.max(-1, ...ids) + 1;
    (masw().picks_by_mode ||= {})[activeMode] = []; (masw().regions_by_mode ||= {})[activeMode] = [];
    renderModes(); drawDispersion();
  });
  [['#mw-tool-pick', 'pick'], ['#mw-tool-delete', 'delete'], ['#mw-tool-region', 'region']]
    .forEach(([id, value]) => $(id).addEventListener('click', () => { tool = value; regionDraft = []; }));
  $('#mw-close-region').addEventListener('click', async () => {
    if (regionDraft.length >= 3) regions().push(regionDraft); regionDraft = []; await saveState(); drawDispersion();
  });
  $('#mw-clear').addEventListener('click', async () => { masw().picks_by_mode[String(activeMode)] = [];
    masw().regions_by_mode[String(activeMode)] = []; await saveState(); drawDispersion(); });
  $('#mw-auto').addEventListener('click', () => autoPick().catch((e) => $('#mw-status').textContent = e));
  $('#mw-inversion').addEventListener('click', () => { renderInversion(); activate(root, 'inversion'); });
  $('#mi-backend').addEventListener('change', () => {
    masw().backend = $('#mi-backend').value;
    backendStatus();
    saveState().catch((e) => { $('#mi-status').textContent = `no se pudo guardar el motor: ${e}`; });
  });
  $('#mi-run').addEventListener('click', () => runInversion().catch((e) => { $('#mi-status').textContent = e; $('#mi-run').disabled = false; }));
  $('#mi-stop').addEventListener('click', async () => {
    if (currentJob) await fetch(`/api/jobs/${encodeURIComponent(currentJob)}/cancel`, { method: 'POST' });
  });
  $('#mp-save').addEventListener('click', async () => {
    try { await saveState({ edited_profile: editedFromTable() }); renderProfile(); $('#mp-status').textContent = 'perfil editado guardado'; }
    catch (e) { $('#mp-status').textContent = e; }
  });
  $('#mp-reset').addEventListener('click', async () => {
    const original = profileData().original; await saveState({ edited_profile: original }); renderProfile();
  });
  $('#mp-csv').addEventListener('click', () => {
    const p = editedFromTable(); let csv = 'layer,vs_m_s,thickness_m\n';
    p.beta.forEach((vs, i) => { csv += `${i + 1},${vs},${p.h[i] ?? ''}\n`; });
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    a.download = `perfil_vs_${campaign || 'campaña'}.csv`; a.click(); URL.revokeObjectURL(a.href);
  });

  async function consumeWaterfallRequest() {
    let rawRequest = null;
    let request = null;
    try {
      rawRequest = localStorage.getItem('geo-masw-request');
      request = rawRequest ? JSON.parse(rawRequest) : null;
    } catch (_) {
      request = null;
    }
    const discard = () => {
      try {
        if (localStorage.getItem('geo-masw-request') === rawRequest) {
          localStorage.removeItem('geo-masw-request');
        }
      } catch (_) { /* modo privado */ }
    };
    if (!rawRequest) return;
    if (!request || !Number.isFinite(Number(request.at))) {
      discard();
      $('#mw-status').textContent = 'se descartó una solicitud Waterfall inválida';
      return;
    }
    if ((Date.now() - Number(request.at)) > 120000) {
      discard();
      $('#mw-status').textContent = 'la solicitud de Waterfall venció; volvé a enviarla';
      return;
    }
    if (request.campaign !== campaign) {
      discard();
      const currentLabel = campaign === '.' ? 'raíz' : campaign;
      const requestedLabel = request.campaign === '.' ? 'raíz' : request.campaign;
      $('#mw-status').textContent =
        `se descartó el envío de ${requestedLabel || 'otra campaña'} porque MASW está en ${currentLabel}`;
      return;
    }
    root.querySelectorAll('.mw-weight').forEach((el) => { el.value = Number(el.dataset.group) === Number(request.group_id) ? 1 : 0; });
    $('#mw-status').textContent = 'datos recibidos desde Waterfall…';
    const calculated = await calculate();
    if (!calculated) {
      $('#mw-status').textContent += ' · el envío se conservó para reintentar';
      return;
    }
    if (!request.auto) {
      discard();
      return;
    }
    try {
      await autoPick();
      activate(root, 'inversion');
      await runInversion();
      discard();
    } catch (err) {
      $('#mw-status').textContent =
        `falló el flujo automático: ${err} · el envío se conservó para reintentar`;
      $('#mi-status').textContent = `no se pudo iniciar la inversión automática: ${err}`;
    }
  }

  const ro = new ResizeObserver(() => { drawDispersion(); renderInversion(); renderProfile(); });
  ro.observe($('#mw-plot'));
  return {
    resume() {
      picker.reload().then((selectedCampaign) => {
        campaign = selectedCampaign;
        return loadAll();
      }).catch((err) => {
        $('#mw-status').textContent = `no se pudo recargar la campaña: ${err}`;
      });
    },
    destroy() { view.destroy(); ro.disconnect(); if (pollTimer) clearTimeout(pollTimer); },
  };
}
