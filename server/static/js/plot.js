// Andamio de dibujo, calcado del pyqtgraph de field_review_app.py:
//   plot_widget.addPlot(title=...) + showGrid(x, y, alpha=0.25)
//   + setLabel("bottom"/"left") + pen width=2
// O sea: marco con grilla y ejes rotulados, y la traza como una línea continua.
// Antes esto dibujaba un segmento vertical suelto por bucket, sin ejes: se veía
// como un peine, no como una traza.

const PAD = { left: 52, right: 12, top: 10, bottom: 26 };

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// Paso de grilla "redondo" (1, 2, 5 × 10^n) para el span y la cantidad de
// divisiones pedidas. Es lo que hace el AxisItem de pyqtgraph.
function niceStep(span, target) {
  const raw = Math.abs(span) / Math.max(1, target);
  if (!(raw > 0) || !Number.isFinite(raw)) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const mult = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
  return mult * mag;
}

function ticks(min, max, target) {
  const step = niceStep(max - min, target);
  const first = Math.ceil(min / step) * step;
  const out = [];
  for (let v = first; v <= max + step * 1e-6; v += step) out.push(v);
  return { values: out, step };
}

// Una marca por década, para los ejes logarítmicos.
function decadeTicks(min, max) {
  const lo = Math.floor(Math.log10(Math.max(min, 1e-12)));
  const hi = Math.ceil(Math.log10(Math.max(max, 1e-12)));
  const values = [];
  for (let e = lo; e <= hi; e++) values.push(Math.pow(10, e));
  return { values, step: 0, log: true };
}

// Etiqueta con los decimales que el paso realmente necesita (0.05 -> 2).
function tickLabel(v, step, log = false) {
  if (log) {
    const e = Math.round(Math.log10(v));
    if (e >= 0 && e <= 3) return String(Math.pow(10, e));
    return `1e${e}`;
  }
  const dec = Math.max(0, Math.min(6, -Math.floor(Math.log10(step)) + (step < 1 ? 0 : 0)));
  const s = v.toFixed(Number.isFinite(dec) ? dec : 2);
  return s === '-0' ? '0' : s;
}

/**
 * Prepara el canvas: escala por DPR, pinta fondo, grilla y ejes, y devuelve el
 * marco con las funciones de proyección. Todo lo que se dibuja después (traza,
 * marcador de trigger) usa ese marco, así nada se sale del área de ploteo.
 *
 * opts: { xMin, xMax, yMin, yMax, xLabel, yLabel, xUnits, yUnits }
 */
export function createFrame(canvas, opts = {}) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 300;
  const h = canvas.clientHeight || 160;
  canvas.width = Math.max(1, Math.round(w * dpr));
  canvas.height = Math.max(1, Math.round(h * dpr));
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const x0 = PAD.left;
  const y0 = PAD.top;
  const pw = Math.max(1, w - PAD.left - PAD.right);
  const ph = Math.max(1, h - PAD.top - PAD.bottom);

  const xMin = opts.xMin ?? 0;
  const xMax = opts.xMax ?? 1;
  const yMin = opts.yMin ?? -1;
  const yMax = opts.yMax ?? 1;
  const xSpan = (xMax - xMin) || 1;
  const ySpan = (yMax - yMin) || 1;

  // Escala log opcional (el espectro va log-log, como el setLogMode de la app).
  // Los valores que llegan son lineales; acá se convierten a década.
  const xLog = !!opts.xLog;
  const yLog = !!opts.yLog;
  const lg = (v) => Math.log10(Math.max(v, 1e-12));
  const xA = xLog ? lg(xMin) : xMin;
  const xB = xLog ? lg(xMax) : xMax;
  const yA = yLog ? lg(yMin) : yMin;
  const yB = yLog ? lg(yMax) : yMax;
  const xRange = (xB - xA) || 1;
  const yRange = (yB - yA) || 1;

  const xOf = (t) => x0 + (((xLog ? lg(t) : t) - xA) / xRange) * pw;
  const yOf = (v) => y0 + ph - (((yLog ? lg(v) : v) - yA) / yRange) * ph;

  const bg = cssVar('--plot-bg', '#ffffff');
  const gridColor = cssVar('--plot-grid', 'rgba(31,41,55,0.16)');
  const axisColor = cssVar('--plot-axis', '#475569');

  ctx.fillStyle = bg;
  ctx.fillRect(x0, y0, pw, ph);

  // Grilla. alpha 0.25 como en showGrid() de la app. En log, una marca por
  // década: los pasos "redondos" lineales no tienen sentido ahí.
  const xt = xLog ? decadeTicks(xMin, xMax) : ticks(xMin, xMax, Math.max(2, Math.round(pw / 90)));
  const yt = yLog ? decadeTicks(yMin, yMax) : ticks(yMin, yMax, Math.max(2, Math.round(ph / 46)));
  ctx.save();
  ctx.strokeStyle = gridColor;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (const t of xt.values) {
    const x = Math.round(xOf(t)) + 0.5;
    ctx.moveTo(x, y0);
    ctx.lineTo(x, y0 + ph);
  }
  for (const v of yt.values) {
    const y = Math.round(yOf(v)) + 0.5;
    ctx.moveTo(x0, y);
    ctx.lineTo(x0 + pw, y);
  }
  ctx.stroke();
  ctx.restore();

  // Ejes y rótulos.
  ctx.save();
  ctx.strokeStyle = axisColor;
  ctx.fillStyle = axisColor;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x0 + 0.5, y0);
  ctx.lineTo(x0 + 0.5, y0 + ph + 0.5);
  ctx.lineTo(x0 + pw, y0 + ph + 0.5);
  ctx.stroke();

  ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  for (const t of xt.values) {
    const x = xOf(t);
    if (x < x0 - 1 || x > x0 + pw + 1) continue;
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + 0.5, y0 + ph);
    ctx.lineTo(Math.round(x) + 0.5, y0 + ph + 3);
    ctx.stroke();
    ctx.fillText(tickLabel(t, xt.step, xt.log), x, y0 + ph + 5);
  }
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (const v of yt.values) {
    const y = yOf(v);
    if (y < y0 - 1 || y > y0 + ph + 1) continue;
    ctx.beginPath();
    ctx.moveTo(x0 - 3, Math.round(y) + 0.5);
    ctx.lineTo(x0, Math.round(y) + 0.5);
    ctx.stroke();
    ctx.fillText(tickLabel(v, yt.step, yt.log), x0 - 6, y);
  }

  if (opts.xLabel) {
    // Arriba a la derecha: abajo pisa la fila de ticks.
    ctx.textAlign = 'right';
    ctx.textBaseline = 'top';
    ctx.fillText(opts.xLabel, x0 + pw - 4, y0 + 3);
  }
  if (opts.yLabel) {
    ctx.save();
    ctx.translate(11, y0 + ph / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText(opts.yLabel, 0, 0);
    ctx.restore();
  }
  ctx.restore();

  return { ctx, x0, y0, pw, ph, xMin, xMax, yMin, yMax, xLog, yLog, xOf, yOf };
}

/**
 * Traza de un canal decimado (GET /api/signal): { min, max, bucket_dt, ... }.
 *
 * Cada bucket trae el mínimo y el máximo de su franja de tiempo, y `rising[i]`
 * dice cuál de los dos ocurrió ANTES. Se dibuja una sola polilínea de 1 px que
 * emite los dos puntos de cada columna en ese orden temporal.
 *
 * El orden no es un detalle: emitir siempre máximo→mínimo (que es lo que se
 * hacía antes) dibuja cada subida real como una bajada, y el salto a la
 * columna siguiente cierra el diente. Sobre una traza suave el resultado es un
 * serrucho parejo que no existe en la señal. Con `rising` la polilínea sigue la
 * forma de onda de verdad y a resolución de píxel queda igual que la línea sin
 * decimar de pyqtgraph.
 *
 * Antes de eso se rellenaba la envolvente cerrada. Sobre señal ruidosa cada
 * columna abarca casi todo el rango vertical, así que el relleno la convertía
 * en una mancha donde no se distinguía nada.
 *
 * opts.xOffset se resta al tiempo de cada bucket (el geo va relativo al trigger:
 * field_review_app.py `_refresh_plot`, geo_time = time - trigger_s).
 */
export function drawMinMax(frame, ch, opts = {}) {
  if (!frame || !ch || !ch.min || !ch.min.length) return;
  const { ctx } = frame;
  const xOffset = opts.xOffset || 0;
  const t0 = ch.t0 || 0;

  ctx.save();
  ctx.beginPath();
  ctx.rect(frame.x0, frame.y0, frame.pw, frame.ph);
  ctx.clip();
  ctx.strokeStyle = opts.color || '#0066cc';
  ctx.lineWidth = opts.lineWidth || 1;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'butt';
  if (opts.alpha !== undefined) ctx.globalAlpha = opts.alpha;

  // Una sola polilínea que recorre los dos extremos de cada columna en el orden
  // en que ocurrieron. Un bucket sin dato (null) corta el trazo, no vale 0.
  const rising = ch.rising;
  ctx.beginPath();
  let pen = false;
  for (let i = 0; i < ch.min.length; i++) {
    const mn = ch.min[i];
    const mx = ch.max[i];
    if (mn === null || mx === null || mn === undefined || mx === undefined) {
      pen = false;
      continue;
    }
    // `ch.x` da la abscisa de cada punto explícitamente (el espectro, que va en
    // bins log y no en pasos iguales). Si no está, se deduce del bucket.
    const x = frame.xOf(ch.x ? ch.x[i] : (t0 + i * ch.bucket_dt - xOffset));
    if (x < frame.x0 - 2 || x > frame.x0 + frame.pw + 2) { pen = false; continue; }
    // Primero el extremo que llegó antes en el tiempo. Sin `rising` (trazas
    // viejas o el espectro, que no oscila) se cae al orden de siempre.
    const sube = rising ? !!rising[i] : false;
    const yA = frame.yOf(sube ? mn : mx);
    const yB = frame.yOf(sube ? mx : mn);
    if (!pen) { ctx.moveTo(x, yA); pen = true; }
    else ctx.lineTo(x, yA);
    if (yB !== yA) ctx.lineTo(x, yB);
  }
  ctx.stroke();
  ctx.restore();
}

// ── Vista: zoom y paneo con el mouse ────────────────────────────────────────
//
// Equivale al ViewBox de pyqtgraph, que en la app PyQt viene puesto de fábrica
// con cada PlotWidget: rueda para acercar sobre el cursor, arrastre para
// mover, doble click para volver al encuadre automático.
//
// El estado son los rangos en unidades de dato (segundos, volts, Hz), no un
// factor: así el encuadre sobrevive a que la traza cambie de escala. `null` =
// automático, o sea "lo que decida quien dibuja".

function scaleAround(a, b, pivot, factor, log) {
  const f = log ? Math.log10 : (v) => v;
  const g = log ? (v) => Math.pow(10, v) : (v) => v;
  const lo = f(log ? Math.max(a, 1e-12) : a);
  const hi = f(log ? Math.max(b, 1e-12) : b);
  const p = f(log ? Math.max(pivot, 1e-12) : pivot);
  return [g(p + (lo - p) * factor), g(p + (hi - p) * factor)];
}

function shiftBy(a, b, delta, log) {
  if (!log) return [a + delta, b + delta];
  // En log el paneo es multiplicativo: correr una fracción de década.
  const k = Math.pow(10, delta);
  return [a * k, b * k];
}

/**
 * Engancha zoom/paneo de mouse a un canvas y devuelve el estado de la vista.
 *
 *   rueda            zoom sobre el cursor, los dos ejes
 *   shift + rueda    sólo el eje vertical
 *   alt/ctrl + rueda sólo el eje horizontal
 *   arrastre         mover (botón del medio o derecho siempre; el izquierdo
 *                    sólo si `leftPan()` lo permite, porque en el hammer ese
 *                    botón ya arrastra el trigger y marca la zona)
 *   doble click      volver al encuadre automático
 *
 * `target` puede ser un canvas o una lista: varios gráficos que comparten una
 * sola vista (la señal original y la filtrada, que sólo sirven comparadas en la
 * misma ventana). `getFrame` recibe el canvas donde ocurrió el evento.
 *
 * opts: { getFrame, onChange, leftPan }
 */
export function attachViewControls(target, opts = {}) {
  const canvases = Array.isArray(target) ? target : [target];
  const canvas = canvases[0];
  const getFrame = opts.getFrame || (() => null);
  const onChange = opts.onChange || (() => {});
  const leftPan = opts.leftPan || (() => true);
  let over = { x: null, y: null };

  const dataAt = (frame, ev) => {
    const rect = ev.currentTarget.getBoundingClientRect();
    const mx = ev.clientX - rect.left;
    const my = ev.clientY - rect.top;
    const fx = (mx - frame.x0) / frame.pw;
    const fy = (frame.y0 + frame.ph - my) / frame.ph;
    const lin = (min, max, t, log) => (log
      ? Math.pow(10, Math.log10(Math.max(min, 1e-12)) +
          t * (Math.log10(Math.max(max, 1e-12)) - Math.log10(Math.max(min, 1e-12))))
      : min + t * (max - min));
    return {
      x: lin(frame.xMin, frame.xMax, fx, frame.xLog),
      y: lin(frame.yMin, frame.yMax, fy, frame.yLog),
    };
  };

  function onWheel(ev) {
    const frame = getFrame(ev.currentTarget);
    if (!frame) return;
    ev.preventDefault();
    const factor = ev.deltaY > 0 ? 1.25 : 0.8;   // mismos pasos que `_zoom_plot` (:1571)
    const p = dataAt(frame, ev);
    const soloY = ev.shiftKey;
    const soloX = ev.altKey || ev.ctrlKey || ev.metaKey;
    if (!soloY) over.x = scaleAround(frame.xMin, frame.xMax, p.x, factor, frame.xLog);
    if (!soloX) over.y = scaleAround(frame.yMin, frame.yMax, p.y, factor, frame.yLog);
    onChange();
  }

  let pan = null;
  function onDown(ev) {
    const el = ev.currentTarget;
    const frame = getFrame(el);
    if (!frame) return;
    if (ev.button === 0 && !leftPan(ev)) return;
    if (ev.button !== 0 && ev.button !== 1 && ev.button !== 2) return;
    ev.preventDefault();
    // Se guardan los rangos del momento: el paneo se calcula siempre contra el
    // encuadre en que empezó el arrastre, así no se acumula deriva.
    pan = { x: ev.clientX, y: ev.clientY, frame, el };
    el.setPointerCapture(ev.pointerId);
    el.classList.add('is-panning');
  }

  function onMove(ev) {
    if (!pan) return;
    const f = pan.frame;
    const dx = (ev.clientX - pan.x) / f.pw;
    const dy = (ev.clientY - pan.y) / f.ph;
    const spanX = f.xLog
      ? Math.log10(Math.max(f.xMax, 1e-12)) - Math.log10(Math.max(f.xMin, 1e-12))
      : f.xMax - f.xMin;
    const spanY = f.yLog
      ? Math.log10(Math.max(f.yMax, 1e-12)) - Math.log10(Math.max(f.yMin, 1e-12))
      : f.yMax - f.yMin;
    over.x = shiftBy(f.xMin, f.xMax, -dx * spanX, f.xLog);
    over.y = shiftBy(f.yMin, f.yMax, dy * spanY, f.yLog);
    onChange();
  }

  function onUp(ev) {
    if (!pan) return;
    const el = pan.el;
    pan = null;
    el.classList.remove('is-panning');
    try { el.releasePointerCapture(ev.pointerId); } catch (_) { /* ya soltado */ }
  }

  const onDbl = () => { over = { x: null, y: null }; onChange(); };
  const onMenu = (ev) => ev.preventDefault();   // el botón derecho panea

  for (const el of canvases) {
    el.addEventListener('wheel', onWheel, { passive: false });
    el.addEventListener('pointerdown', onDown);
    el.addEventListener('pointermove', onMove);
    el.addEventListener('pointerup', onUp);
    el.addEventListener('pointercancel', onUp);
    el.addEventListener('dblclick', onDbl);
    el.addEventListener('contextmenu', onMenu);
  }

  return {
    /** Mezcla el encuadre automático con lo que el usuario haya movido. */
    apply(auto) {
      const o = { ...auto };
      if (over.x) { o.xMin = over.x[0]; o.xMax = over.x[1]; }
      if (over.y) { o.yMin = over.y[0]; o.yMax = over.y[1]; }
      return o;
    },
    /** Zoom por teclado: mismo estado que el mouse, centrado en el marco. */
    zoom(factor, axis = 'both') {
      const frame = getFrame(canvas);
      if (!frame) return;
      if (axis !== 'y') {
        over.x = scaleAround(frame.xMin, frame.xMax,
          frame.xLog ? Math.sqrt(Math.max(frame.xMin * frame.xMax, 1e-24))
                     : (frame.xMin + frame.xMax) / 2, factor, frame.xLog);
      }
      if (axis !== 'x') {
        over.y = scaleAround(frame.yMin, frame.yMax,
          frame.yLog ? Math.sqrt(Math.max(frame.yMin * frame.yMax, 1e-24))
                     : (frame.yMin + frame.yMax) / 2, factor, frame.yLog);
      }
      onChange();
    },
    reset() { over = { x: null, y: null }; onChange(); },
    isCustom() { return !!(over.x || over.y); },
    destroy() {
      for (const el of canvases) {
        el.removeEventListener('wheel', onWheel);
        el.removeEventListener('pointerdown', onDown);
        el.removeEventListener('pointermove', onMove);
        el.removeEventListener('pointerup', onUp);
        el.removeEventListener('pointercancel', onUp);
        el.removeEventListener('dblclick', onDbl);
        el.removeEventListener('contextmenu', onMenu);
      }
    },
  };
}

// Marcador de trigger: línea llena sobre el hammer, punteada sobre el geo
// (allá el trigger está en x=0 porque el geo ya se dibuja relativo a él).
// `label` se rotula arriba, como el InfiniteLine con label de la app.
export function drawVLine(frame, xSec, opts = {}) {
  if (!frame) return;
  const { ctx } = frame;
  const x = frame.xOf(xSec);
  if (x < frame.x0 - 1 || x > frame.x0 + frame.pw + 1) return;

  ctx.save();
  ctx.beginPath();
  ctx.rect(frame.x0, frame.y0, frame.pw, frame.ph + 1);
  ctx.clip();
  ctx.strokeStyle = opts.color || '#e67e22';
  ctx.lineWidth = opts.lineWidth || 2;
  if (opts.dashed) ctx.setLineDash([5, 4]);
  ctx.beginPath();
  ctx.moveTo(x, frame.y0);
  ctx.lineTo(x, frame.y0 + frame.ph);
  ctx.stroke();
  ctx.restore();

  if (opts.label) {
    ctx.save();
    ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
    const tw = ctx.measureText(opts.label).width;
    const bx = Math.min(frame.x0 + frame.pw - tw - 8, Math.max(frame.x0 + 2, x + 4));
    ctx.fillStyle = opts.color || '#e67e22';
    ctx.fillRect(bx - 3, frame.y0 + 2, tw + 6, 13);
    ctx.fillStyle = cssVar('--plot-bg', '#fff');
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillText(opts.label, bx, frame.y0 + 4);
    ctx.restore();
  }
}
