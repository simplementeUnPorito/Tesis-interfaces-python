// Andamio de dibujo reutilizable. En este ítem sólo hace falta que exista y
// dibuje algo mínimo (línea + ejes); §3.1 lo completa de verdad.

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
