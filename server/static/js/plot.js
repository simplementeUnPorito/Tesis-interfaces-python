// Andamio de dibujo reutilizable.

export function drawSeries(canvas, series, opts = {}) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  if (!series || !series.length) return;

  const color = opts.color || '#2563eb';
  const min = opts.min ?? Math.min(...series);
  const max = opts.max ?? Math.max(...series);
  const span = max - min || 1;

  // Ejes.
  ctx.strokeStyle = opts.axisColor || '#9ca3af';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, h - 1);
  ctx.lineTo(w, h - 1);
  ctx.stroke();

  // Serie.
  ctx.strokeStyle = color;
  ctx.lineWidth = opts.lineWidth || 1.5;
  ctx.beginPath();
  series.forEach((v, i) => {
    const x = (i / (series.length - 1 || 1)) * w;
    const y = h - ((v - min) / span) * h;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

// §3.1: envolvente min/max de un canal decimado del servidor (GET /api/signal).
// `ch` es el objeto `channels.hammer`/`channels.geo` de esa respuesta:
// { min, max, bucket_dt, duration_s, y_min, y_max, ... }.
//
// opts.xMin/xMax: ventana visible, en segundos, en el mismo eje que `xOffset`.
// opts.xOffset: se resta al tiempo de cada bucket antes de ubicarlo (el geo se
// dibuja relativo al trigger: field_review_app.py `_refresh_plot`, geo_time =
// time - trigger_s).
export function drawMinMax(canvas, ch, opts = {}) {
  const ctx = canvas.getContext('2d');
  const w = canvas.clientWidth || canvas.width;
  const h = canvas.clientHeight || canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (!ch || !ch.min || !ch.min.length) return;

  const xMin = opts.xMin ?? 0;
  const xMax = opts.xMax ?? (ch.duration_s || 1);
  const xOffset = opts.xOffset || 0;
  const xSpan = (xMax - xMin) || 1;

  const yMinV = ch.y_min ?? 0;
  const yMaxV = ch.y_max ?? 0;
  let range = yMaxV - yMinV;
  if (range <= 0) range = 1;                 // y_max == y_min: ancho 1, no dividir por 0
  const padY = range * 0.05;                 // 5% de aire
  const yLo = yMinV - padY;
  const yHi = yMaxV + padY;
  const ySpan = (yHi - yLo) || 1;

  const xOf = (i) => (((i * ch.bucket_dt - xOffset) - xMin) / xSpan) * w;
  const yOf = (v) => h - ((v - yLo) / ySpan) * h;

  ctx.strokeStyle = opts.color || '#2563eb';
  ctx.lineWidth = opts.lineWidth || 1;
  ctx.beginPath();
  // Un moveTo/lineTo por bucket: cada columna es el rango real de esa franja
  // de tiempo, así el pico se ve. Bucket null (sin dato) -> no se dibuja, no
  // se lo trata como 0 (se corta el trazo y sigue el próximo bucket con dato).
  for (let i = 0; i < ch.min.length; i++) {
    const mn = ch.min[i];
    const mx = ch.max[i];
    if (mn === null || mx === null) continue;
    const x = xOf(i);
    ctx.moveTo(x, yOf(mn));
    ctx.lineTo(x, yOf(mx));
  }
  ctx.stroke();
}

// Marca vertical (el trigger): línea llena sobre el hammer, punteada sobre el
// geo (a x=0, porque el geo ya se dibuja relativo al trigger).
export function drawVLine(canvas, xSec, opts = {}) {
  const ctx = canvas.getContext('2d');
  const w = canvas.clientWidth || canvas.width;
  const h = canvas.clientHeight || canvas.height;
  const xMin = opts.xMin ?? 0;
  const xMax = opts.xMax ?? 1;
  const xSpan = (xMax - xMin) || 1;
  const x = ((xSec - xMin) / xSpan) * w;

  ctx.save();
  ctx.strokeStyle = opts.color || '#e67e22';
  ctx.lineWidth = opts.lineWidth || 1.5;
  if (opts.dashed) ctx.setLineDash([5, 4]);
  ctx.beginPath();
  ctx.moveTo(x, 0);
  ctx.lineTo(x, h);
  ctx.stroke();
  ctx.restore();
}
