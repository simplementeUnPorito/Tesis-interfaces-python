// Panel derecho de Capturas: los dos gráficos, con todo lo que la app PyQt
// hace sobre ellos (`_refresh_plot` :1091 y alrededores):
//
//   · hammer arriba / geo abajo, grilla y ejes rotulados
//   · trigger arrastrable con su etiqueta (InfiniteLine movable :1114)
//   · zona auto: dos clicks sobre el hammer, líneas azules (:1140)
//   · overlays: mismo label, promedio OK, promedio de carpeta (:1186-1325)
//   · zoom con ↑/↓ (`_zoom_plot` :1571)
//
// Se guarda solo, como la app: soltar el trigger o invertir el geófono avisa
// al panel izquierdo, que es el que escribe (POST /api/pick). El trigger va a
// TODOS los geófonos de la captura; la inversión, sólo a este.
import { createFrame, drawMinMax, drawVLine } from '../plot.js';

const fmt = (n, d = 2) => (n === null || n === undefined) ? '—' : Number(n).toFixed(d);

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// Una sola fuente de verdad para los colores de señal: app.css replica
// _plot_colors() de field_review_app.py (:1510).
function colors() {
  return {
    hammer: cssVar('--sig-hammer', '#cc5a00'),
    geo: cssVar('--sig-geo', '#0066cc'),
    trigger: cssVar('--sig-trigger', '#e67e22'),
    overlay: cssVar('--sig-overlay', 'rgba(120,120,120,.45)'),
    okAvg: cssVar('--sig-ok-avg', '#d62728'),
    folderAvg: cssVar('--sig-folder-avg', '#2ca02c'),
    zone: cssVar('--sig-zone', '#1f77b4'),
  };
}

function yRange(ch, zoom) {
  const lo = ch.y_min ?? 0;
  const hi = ch.y_max ?? 0;
  let range = hi - lo;
  if (range <= 0) range = 1;
  const mid = (lo + hi) / 2;
  const half = (range / 2) * 1.05 * zoom;
  return { yMin: mid - half, yMax: mid + half };
}

export function mountViewer(host, opts = {}) {
  host.innerHTML = `
    <div class="viewer-head">
      <h2>Visor</h2>
      <div class="viewer-meta" id="vm-meta">Elegí una captura de la tabla.</div>
    </div>
    <div class="viewer-tools">
      <div class="control-group" role="group" aria-label="Origen de la señal">
        <label><input type="radio" name="vm-kind" value="raw" checked> raw</label>
        <label title="El filt_f32le.bin que grabó el nodo, no el pasa-banda de §3.2 (todavía no está portado)">
          <input type="radio" name="vm-kind" value="filt"> filt</label>
      </div>
      <label class="control-check"><input type="checkbox" id="vm-flip"> Invertir geo</label>
      <button id="vm-zoom-reset" type="button" class="btn-quiet btn-sm">Reencuadrar</button>
      <span class="viewer-status" id="vm-status"></span>
    </div>
    <div class="plot-stack">
      <figure class="plot-box">
        <figcaption class="plot-title"><span class="ch-dot ch-hammer"></span>Hammer</figcaption>
        <canvas class="plot" id="vm-hammer"></canvas>
      </figure>
      <figure class="plot-box">
        <figcaption class="plot-title"><span class="ch-dot ch-geo"></span>Geófono, alineado al hammer
          <span class="legend" id="vm-legend"></span></figcaption>
        <canvas class="plot" id="vm-geo"></canvas>
      </figure>
    </div>
  `;

  const elMeta = host.querySelector('#vm-meta');
  const elStatus = host.querySelector('#vm-status');
  const elLegend = host.querySelector('#vm-legend');
  const elHammer = host.querySelector('#vm-hammer');
  const elGeo = host.querySelector('#vm-geo');
  const elFlip = host.querySelector('#vm-flip');

  let row = null;                 // fila de /api/captures
  let payload = null;             // /api/signal
  let overlays = null;            // /api/overlays
  let trigger = 0;                // el que se está mostrando (puede ser arrastrado)
  let zone = null;                // [a, b] de la zona auto, en segundos
  let markingZone = false;
  let zoneClicks = [];
  let flip = false;
  let kind = 'raw';
  const zoomLevel = { hammer: 1, geo: 1 };
  let frames = { hammer: null, geo: null };
  let abortSignalReq = null;
  let abortOverlayReq = null;
  let kindListener = null;
  let dragging = false;

  function status(text) { elStatus.textContent = text || ''; }

  // ── Dibujo ───────────────────────────────────────────────────────────────
  function render() {
    if (!payload) return;
    const c = colors();
    const ch = payload.channels || {};

    if (ch.hammer) {
      const dur = ch.hammer.duration_s || 0;
      // Ventana de `_refresh_plot` (:1136): [trigger-0.15, trigger+0.65],
      // escalada por el zoom alrededor de su centro.
      const center = trigger + 0.25;
      const half = 0.4 * zoomLevel.hammer;
      frames.hammer = createFrame(elHammer, {
        xMin: Math.max(0, center - half),
        xMax: Math.min(dur, center + half),
        ...yRange(ch.hammer, zoomLevel.hammer),
        xLabel: 'tiempo [s]',
        yLabel: 'Hammer [V]',
      });
      drawMinMax(frames.hammer, ch.hammer, { color: c.hammer });
      if (zone) {
        drawVLine(frames.hammer, zone[0], { color: c.zone, dashed: true, lineWidth: 1.5 });
        drawVLine(frames.hammer, zone[1], { color: c.zone, dashed: true, lineWidth: 1.5 });
      }
      if (payload.trigger_source !== 'none') {
        drawVLine(frames.hammer, trigger, {
          color: c.trigger, lineWidth: 3, label: `hammer ${trigger.toFixed(4)}s`,
        });
      }
    } else {
      frames.hammer = createFrame(elHammer, { xMin: 0, xMax: 1, yMin: -1, yMax: 1,
        xLabel: 'sin canal de martillo', yLabel: 'Hammer [V]' });
    }

    if (ch.geo) {
      const dur = ch.geo.duration_s || 0;
      // Ventana de `_refresh_plot` (:1137): [-0.08, 1.1] relativo al trigger.
      const center = 0.51;
      const half = 0.59 * zoomLevel.geo;
      frames.geo = createFrame(elGeo, {
        xMin: Math.max(-trigger, center - half),
        xMax: Math.min(dur - trigger, center + half),
        ...yRange(ch.geo, zoomLevel.geo),
        xLabel: 'tiempo relativo al hammer [s]',
        yLabel: 'Geo [V]',
      });
      // Overlays primero: van por debajo de la traza actual, como los zValue de la app.
      if (overlays) {
        for (const t of overlays.same_label || []) {
          drawMinMax(frames.geo, t, { color: c.overlay, lineWidth: 1, alpha: 0.45 });
        }
        if (overlays.folder_average) {
          drawMinMax(frames.geo, overlays.folder_average,
                     { color: c.folderAvg, lineWidth: 1.25, alpha: 0.9 });
        }
        if (overlays.ok_average) {
          drawMinMax(frames.geo, overlays.ok_average,
                     { color: c.okAvg, lineWidth: 1.25, alpha: 0.9 });
        }
      }
      drawMinMax(frames.geo, ch.geo, { color: c.geo, xOffset: trigger, lineWidth: 1 });
      drawVLine(frames.geo, 0, { color: c.trigger, dashed: true });
    } else {
      frames.geo = createFrame(elGeo, { xMin: 0, xMax: 1, yMin: -1, yMax: 1,
        xLabel: 'sin canal de geófono', yLabel: 'Geo [V]' });
    }

    renderMeta();
    renderLegend();
  }

  function renderMeta() {
    if (!payload) return;
    const ch = payload.channels || {};
    const ref = ch.hammer || ch.geo || {};
    const src = { annotation: 'guardado', auto: 'auto', override: 'arrastrado', none: 'sin trigger' };
    // Con varios geófonos en el tendido hay que saber cuál se está mirando.
    const geoTag = (ch.geo && (ch.geo.pcb_id || ch.geo.label))
      ? `<span class="meta-num">geófono ${ch.geo.pcb_id || ch.geo.label}</span>` : '';
    elMeta.innerHTML =
      `<strong>${payload.folder} / ${payload.capture}</strong>` + geoTag +
      `<span class="meta-num">${fmt(payload.distance_m)} m</span>` +
      `<span class="meta-num">${fmt(payload.fs, 0)} Hz</span>` +
      `<span class="meta-num">${ref.samples || 0} muestras</span>` +
      `<span class="meta-num">trigger ${fmt(trigger, 4)} s (${src[payload.trigger_source] || payload.trigger_source})</span>`;
  }

  function renderLegend() {
    const parts = [];
    if (overlays && (overlays.same_label || []).length) {
      parts.push(`<span class="lg lg-overlay"></span>${overlays.same_label.length} mismo label`);
    }
    if (overlays && overlays.folder_average) {
      parts.push(`<span class="lg lg-folder"></span>promedio carpeta (n=${overlays.folder_average.n})`);
    }
    if (overlays && overlays.ok_average) {
      parts.push(`<span class="lg lg-ok"></span>promedio OK (n=${overlays.ok_average.n})`);
    }
    elLegend.innerHTML = parts.join('');
  }

  // ── Datos ────────────────────────────────────────────────────────────────
  function maxPointsFor(canvas) {
    const dpr = window.devicePixelRatio || 1;
    // Un bucket por píxel físico: pedir más es tirar bytes.
    return Math.min(20000, Math.max(100, Math.round((canvas.clientWidth || 300) * dpr)));
  }

  async function fetchSignal({ triggerOverride = null } = {}) {
    if (!row) return;
    if (abortSignalReq) abortSignalReq.abort();
    abortSignalReq = new AbortController();
    const p = new URLSearchParams({ kind, max_points: String(maxPointsFor(elHammer)) });
    // Cada campaña es su propia raíz de datos: sin esto el shot_id se busca
    // en la raíz equivocada y no aparece.
    if (row.campaign) p.set('campaign', row.campaign);
    if (row.shot_id) p.set('shot_id', row.shot_id);
    else { p.set('folder', row.folder); p.set('capture', row.capture); }
    if (flip) p.set('geo_flip', '1');
    if (triggerOverride !== null) p.set('trigger_s', String(triggerOverride));
    try {
      const res = await fetch(`/api/signal?${p}`, { signal: abortSignalReq.signal, cache: 'no-store' });
      if (!res.ok) {
        elMeta.textContent = `No se pudo cargar la señal (HTTP ${res.status}).`;
        payload = null;
        return;
      }
      payload = await res.json();
      trigger = Number(payload.trigger_s) || 0;
      // El panel "Marca actual" muestra el trigger que se está dibujando, no
      // el 0.0000 de la fila sin anotar: la app hace lo mismo (usa el auto).
      if (opts.onTriggerPreview) opts.onTriggerPreview(trigger, payload.trigger_source);
      render();
    } catch (err) {
      if (err.name !== 'AbortError') elMeta.textContent = `Error al cargar la señal: ${err}`;
    }
  }

  async function fetchOverlays() {
    overlays = null;
    if (!row || !row.shot_id) { render(); return; }
    const o = opts.getOverlayOpts ? opts.getOverlayOpts() : {};
    if (!o.same_label && !o.folder_average) { render(); return; }
    if (abortOverlayReq) abortOverlayReq.abort();
    abortOverlayReq = new AbortController();
    const p = new URLSearchParams({
      shot_id: row.shot_id,
      campaign: row.campaign || '',
      kind: o.kind || kind,
      max_points: String(maxPointsFor(elGeo)),
      same_label: String(o.same_label ?? 1),
      max_count: String(o.max_count ?? 12),
      folder_average: String(o.folder_average ?? 1),
    });
    try {
      const res = await fetch(`/api/overlays?${p}`, { signal: abortOverlayReq.signal, cache: 'no-store' });
      if (res.ok) overlays = await res.json();
    } catch (err) {
      if (err.name === 'AbortError') return;
    }
    render();
  }

  // ── Interacción sobre el hammer: arrastrar el trigger y marcar la zona ────
  function timeAt(frame, clientX, canvas) {
    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const t = frame.xMin + ((x - frame.x0) / frame.pw) * (frame.xMax - frame.xMin);
    return Math.max(frame.xMin, Math.min(frame.xMax, t));
  }

  elHammer.addEventListener('pointerdown', (ev) => {
    if (!frames.hammer || !payload) return;
    const t = timeAt(frames.hammer, ev.clientX, elHammer);

    if (markingZone) {
      zoneClicks.push(t);
      if (zoneClicks.length === 2) {
        zone = [Math.min(...zoneClicks), Math.max(...zoneClicks)];
        markingZone = false;
        zoneClicks = [];
        status(`zona auto ${zone[0].toFixed(3)}–${zone[1].toFixed(3)} s`);
      } else {
        status('zona auto: falta el segundo click');
      }
      render();
      return;
    }

    if (payload.trigger_source === 'none') return;
    // Agarrar el trigger sólo si el click cae cerca de la línea (8 px).
    const px = frames.hammer.xOf(trigger);
    const rect = elHammer.getBoundingClientRect();
    if (Math.abs((ev.clientX - rect.left) - px) > 8) return;
    dragging = true;
    elHammer.setPointerCapture(ev.pointerId);
    elHammer.classList.add('is-dragging');
  });

  elHammer.addEventListener('pointermove', (ev) => {
    if (!frames.hammer) return;
    if (!dragging) {
      // Cursor de agarre cuando estás encima de la línea.
      const near = payload && payload.trigger_source !== 'none' &&
        Math.abs((ev.clientX - elHammer.getBoundingClientRect().left) - frames.hammer.xOf(trigger)) <= 8;
      elHammer.classList.toggle('is-grabbable', !!near);
      return;
    }
    trigger = timeAt(frames.hammer, ev.clientX, elHammer);
    if (opts.onTriggerPreview) opts.onTriggerPreview(trigger, 'override');
    render();
  });

  function endDrag(ev) {
    if (!dragging) return;
    dragging = false;
    elHammer.classList.remove('is-dragging');
    try { elHammer.releasePointerCapture(ev.pointerId); } catch (_) { /* ya soltado */ }
    // La línea de base se mide antes del trigger: al moverlo hay que
    // recalcularla en el servidor, no sólo correr la línea.
    fetchSignal({ triggerOverride: trigger });
    if (opts.onTriggerCommit) opts.onTriggerCommit(trigger);
  }
  elHammer.addEventListener('pointerup', endDrag);
  elHammer.addEventListener('pointercancel', endDrag);

  host.querySelectorAll('input[name="vm-kind"]').forEach((el) =>
    el.addEventListener('change', () => {
      kind = host.querySelector('input[name="vm-kind"]:checked').value;
      if (kindListener) kindListener(kind);
      fetchSignal();
      fetchOverlays();
    }));

  elFlip.addEventListener('change', () => {
    flip = elFlip.checked;
    fetchSignal();
    if (opts.onFlipCommit) opts.onFlipCommit(flip);
  });

  host.querySelector('#vm-zoom-reset').addEventListener('click', () => {
    zoomLevel.hammer = 1;
    zoomLevel.geo = 1;
    render();
  });

  const ro = new ResizeObserver(() => render());
  ro.observe(elHammer);
  const mo = new MutationObserver(() => render());
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

  return {
    hasShot: () => !!row,

    show(nextRow, { kind: k } = {}) {
      if (k && k !== kind) {
        kind = k;
        const el = host.querySelector(`input[name="vm-kind"][value="${kind}"]`);
        if (el) el.checked = true;
      }
      row = nextRow;
      payload = null;
      overlays = null;
      // La zona auto es de la muestra en la que se marcó: al cambiar de
      // muestra se limpia sola, igual que en `_select_row` (:1007).
      zone = null;
      markingZone = false;
      zoneClicks = [];
      flip = !!nextRow.geo_flip;
      elFlip.checked = flip;
      status('');
      elMeta.textContent = `${nextRow.folder} / ${nextRow.capture} — cargando…`;
      fetchSignal();
      fetchOverlays();
    },

    refreshOverlays: fetchOverlays,

    redraw() { render(); },

    async reautoPick() {
      if (!row || !row.shot_id) return;
      const p = new URLSearchParams({ shot_id: row.shot_id, campaign: row.campaign || '', kind });
      if (zone) { p.set('zone_a', String(zone[0])); p.set('zone_b', String(zone[1])); }
      try {
        const r = await fetch(`/api/autopick?${p}`, { cache: 'no-store' }).then((x) => x.json());
        trigger = Number(r.trigger_s) || 0;
        status(zone ? 'auto dentro de la zona (no guardado)' : 'auto (no guardado)');
        fetchSignal({ triggerOverride: trigger });
      } catch (_) {
        status('no se pudo recalcular el auto');
      }
    },

    startZoneMarking() {
      markingZone = true;
      zoneClicks = [];
      status('zona auto: dos clicks sobre el hammer para marcar inicio y fin');
    },

    clearZone() {
      zone = null;
      markingZone = false;
      zoneClicks = [];
      status('zona limpia');
      render();
    },

    // A/D mueven el borde izquierdo, ←/→ el derecho. El paso son 3 muestras,
    // como `_ZONE_NUDGE_SAMPLES` (:1527).
    nudgeZone(edge, dir) {
      if (!zone || !payload) return;
      const fs = payload.fs || 1;
      const step = (3 / fs) * dir;
      const i = edge === 'a' ? 0 : 1;
      zone[i] = Math.max(0, zone[i] + step);
      zone = [Math.min(zone[0], zone[1]), Math.max(zone[0], zone[1])];
      status(`zona auto ${zone[0].toFixed(3)}–${zone[1].toFixed(3)} s`);
      render();
    },

    zoom(which, zoomIn) {
      // `_zoom_plot` (:1571): 0.8 para acercar, 1.25 para alejar.
      const f = zoomIn ? 0.8 : 1.25;
      zoomLevel[which] = Math.max(0.02, Math.min(8, zoomLevel[which] * f));
      render();
    },

    toggleFlip() {
      flip = !flip;
      elFlip.checked = flip;
      fetchSignal();
      if (opts.onFlipCommit) opts.onFlipCommit(flip);
    },

    setStatus: status,

    onKindChange(cb) { kindListener = cb; },

    destroy() {
      if (abortSignalReq) abortSignalReq.abort();
      if (abortOverlayReq) abortOverlayReq.abort();
      ro.disconnect();
      mo.disconnect();
    },
  };
}
