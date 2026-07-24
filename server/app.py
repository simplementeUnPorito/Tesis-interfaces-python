"""Servidor de datos: recibe las capturas, las preprocesa y las sirve por web.

Corre sobre la biblioteca estándar a propósito — sin FastAPI ni Flask. Dos
razones: no hay framework instalado en este entorno, y el objetivo declarado es
meter todo en un contenedor portable, donde cada dependencia menos es una cosa
menos que puede romperse al mudarlo.

Rutas:
    POST /ingest        recibe el ZIP de la SPA (lo manda el navegador, no el ESP)
    GET  /              tablero: subidas, estado del preprocesado, disparos
    GET  /api/jobs      estado de las subidas en JSON
    GET  /api/dataset   disparos descubiertos con sus picks
    POST /api/requeue   reprocesa un ZIP ya subido

Uso:
    python -m server                  # 0.0.0.0:8000, datos en data/server
    python -m server --port 9000 --data-root D:/geo
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .pipeline import Pipeline, frd

MAX_UPLOAD = 256 * 1024 * 1024


def _dataset_summary(pipeline: Pipeline) -> dict:
    """Disparos descubiertos + el pick de cada uno.

    Se recalcula por pedido en vez de cachearse: descubrir es leer metadata, no
    señales, y así la web nunca muestra un estado viejo si alguien toca el
    volumen por fuera (la app PyQt, por ejemplo).
    """
    try:
        dataset = frd.discover_dataset(pipeline.raw_root)
    except FileNotFoundError:
        return {"folders": [], "shot_count": 0, "raw_root": str(pipeline.raw_root)}

    picks = frd.load_annotations(frd.default_annotations_path(pipeline.raw_root))
    folders: dict[str, list[dict]] = {}
    for shot in dataset.shots:
        ann = picks.get(shot.shot_id)
        folders.setdefault(shot.folder_name, []).append({
            "shot_id": shot.shot_id,
            "capture": shot.capture_name,
            "order": shot.order,
            "fs": shot.fs,
            "distance_m": shot.distance_m,
            "hammer_pcb": shot.hammer.pcb_id,
            "geo_pcb": shot.geo.pcb_id,
            "trigger_s": None if ann is None else ann.trigger_s,
            "arrival_s": None if ann is None else ann.arrival_s,
            "source": None if ann is None else ann.source,
            "reviewed": bool(ann and ann.reviewed),
            "accepted": bool(ann and ann.accepted),
        })
    for shots in folders.values():
        shots.sort(key=lambda d: (d["order"], d["capture"]))
    return {
        "raw_root": str(pipeline.raw_root),
        "shot_count": len(dataset.shots),
        "reviewed_count": sum(1 for a in picks.values() if a.reviewed),
        "folders": [{"folder": k, "shots": v} for k, v in sorted(folders.items())],
    }


INDEX_HTML = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Servidor de datos - Geophone</title>
<style>
:root{color-scheme:light dark}
body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;padding:24px;
     background:#f6f7f9;color:#16181d}
@media (prefers-color-scheme:dark){body{background:#12141a;color:#e8eaee}}
h1{font-size:1.3rem;margin:0 0 4px}
p.sub{margin:0 0 24px;color:#6b7280;font-size:.9rem}
section{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:16px;
        margin-bottom:20px}
@media (prefers-color-scheme:dark){section{background:#1a1d24;border-color:#2a2f38}}
h2{font-size:1rem;margin:0 0 12px}
table{width:100%;border-collapse:collapse;font-size:.85rem}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #eceef1}
@media (prefers-color-scheme:dark){th,td{border-color:#262b34}}
th{font-weight:600;color:#6b7280;font-size:.75rem;text-transform:uppercase;
   letter-spacing:.04em}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.72rem;
     font-weight:600}
.listo{background:#dcfce7;color:#166534}
.procesando,.descomprimiendo,.pendiente{background:#fef3c7;color:#92400e}
.error{background:#fee2e2;color:#991b1b}
.wrap{overflow-x:auto}
.empty{color:#6b7280;font-size:.9rem;padding:8px 0}
code{background:#f1f3f5;padding:1px 5px;border-radius:4px;font-size:.85em}
@media (prefers-color-scheme:dark){code{background:#22262e}}
</style>
<h1>Servidor de datos</h1>
<p class="sub">Las capturas llegan de la SPA del maestro, se descomprimen y se
preprocesan solas. Acá se ve qué está listo para validar.</p>

<section>
  <h2>Subidas</h2>
  <div class="wrap"><table id="jobs"><thead><tr>
    <th>Cuándo</th><th>Archivo</th><th class="num">kB</th><th>Estado</th>
    <th>Carpeta</th><th class="num">Disparos</th><th class="num">Picks</th><th>Detalle</th>
  </tr></thead><tbody></tbody></table></div>
  <div id="jobs-empty" class="empty" hidden>Todavía no llegó ninguna captura.
    Desde la SPA del maestro: pestaña Captura → <code>Subir al server</code>.</div>
</section>

<section>
  <h2>Disparos <span id="ds-count" class="tag listo" hidden></span></h2>
  <div id="dataset"></div>
</section>

<script>
const fmt = (n, d = 1) => (n === null || n === undefined) ? '—' : Number(n).toFixed(d);

async function tick() {
  try {
    const [jobs, ds] = await Promise.all([
      fetch('api/jobs', {cache: 'no-store'}).then(r => r.json()),
      fetch('api/dataset', {cache: 'no-store'}).then(r => r.json()),
    ]);
    renderJobs(jobs.jobs || []);
    renderDataset(ds);
  } catch (e) {
    // Sin conexión: no borrar lo que ya se mostraba, solo esperar el próximo tick.
  }
}

function renderJobs(rows) {
  const tb = document.querySelector('#jobs tbody');
  document.getElementById('jobs-empty').hidden = rows.length > 0;
  tb.innerHTML = '';
  for (const j of rows) {
    const tr = document.createElement('tr');
    const detalle = j.error ? j.error.split('\\n').slice(-1)[0]
                            : (j.log && j.log.length ? j.log[j.log.length - 1] : '');
    tr.innerHTML =
      `<td>${(j.created_at || '').replace('T', ' ').replace('+00:00', '')}</td>` +
      `<td>${j.filename}</td>` +
      `<td class="num">${(j.bytes / 1024).toFixed(0)}</td>` +
      `<td><span class="tag ${j.state}">${j.state}</span></td>` +
      `<td>${j.folder || '—'}</td>` +
      `<td class="num">${j.shots}</td>` +
      `<td class="num">${j.picks}</td>` +
      `<td>${detalle}</td>`;
    tb.appendChild(tr);
  }
}

function renderDataset(ds) {
  const host = document.getElementById('dataset');
  const badge = document.getElementById('ds-count');
  if (!ds.folders || !ds.folders.length) {
    host.innerHTML = '<div class="empty">Nada descubierto todavía en ' +
                     `<code>${ds.raw_root || ''}</code>.</div>`;
    badge.hidden = true;
    return;
  }
  badge.hidden = false;
  badge.textContent = `${ds.shot_count} disparos · ${ds.reviewed_count || 0} validados`;
  host.innerHTML = ds.folders.map((f) => `
    <h3 style="font-size:.9rem;margin:16px 0 6px">${f.folder}</h3>
    <div class="wrap"><table><thead><tr>
      <th>Captura</th><th class="num">fs</th><th class="num">dist (m)</th>
      <th class="num">trigger (s)</th><th>pick</th><th>validado</th>
    </tr></thead><tbody>
    ${f.shots.map((s) => `<tr>
      <td>${s.capture}</td>
      <td class="num">${fmt(s.fs, 0)}</td>
      <td class="num">${fmt(s.distance_m, 2)}</td>
      <td class="num">${s.trigger_s === null ? '—' : fmt(s.trigger_s, 4)}</td>
      <td>${s.source || '—'}</td>
      <td>${s.reviewed ? 'sí' : 'no'}</td>
    </tr>`).join('')}
    </tbody></table></div>`).join('');
}

tick();
setInterval(tick, 3000);
</script>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    pipeline: Pipeline = None   # inyectado en main()

    # ── helpers ─────────────────────────────────────────────────────────────
    def _cors(self) -> None:
        # La SPA se sirve desde el ESP32, o sea otro origen: sin estas cabeceras
        # el navegador aborta el POST en el preflight y desde la SPA se ve como
        # "servidor inalcanzable", que hace buscar el problema en la red.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Geo-Filename")
        self.send_header("Access-Control-Max-Age", "86400")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _text(self, code: int, text: str) -> None:
        self._send(code, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _json(self, code: int, obj) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8")

    # ── verbos ──────────────────────────────────────────────────────────────
    def do_OPTIONS(self) -> None:          # noqa: N802
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:              # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in ("/", "/index.html"):
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/jobs":
            self._json(200, {"jobs": self.pipeline.jobs()})
        elif path == "/api/dataset":
            self._json(200, _dataset_summary(self.pipeline))
        elif path == "/health":
            self._text(200, "ok\n")
        else:
            self._text(404, "no existe\n")

    def do_POST(self) -> None:             # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/ingest":
            self._ingest()
        elif path == "/api/requeue":
            qs = parse_qs(urlparse(self.path).query)
            job_id = (qs.get("job_id") or [""])[0]
            ok = self.pipeline.requeue(job_id)
            self._text(200 if ok else 404, "reencolado\n" if ok else "no existe\n")
        else:
            self._text(404, "no existe\n")

    def _ingest(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._text(400, "Content-Length inválido\n")
            return
        if length <= 0:
            self._text(400, "cuerpo vacío\n")
            return
        if length > MAX_UPLOAD:
            self._text(413, f"demasiado grande: {length} B\n")
            return

        # Leer por bloques: un read(length) de una sola vez sobre una conexión
        # móvil lenta parece un servidor colgado.
        buf = bytearray()
        while len(buf) < length:
            chunk = self.rfile.read(min(65536, length - len(buf)))
            if not chunk:
                break
            buf.extend(chunk)
        if len(buf) != length:
            self._text(500, f"recibido incompleto: {len(buf)}/{length} B\n")
            return
        if bytes(buf[:2]) != b"PK":
            self._text(415, "no parece un ZIP\n")
            return

        name = (self.headers.get("X-Geo-Filename") or "captura.zip").strip()
        job = self.pipeline.submit_zip(bytes(buf), name)
        print(f"[ingest] {self.client_address[0]} -> {job.job_id} "
              f"({len(buf)} B) encolado", flush=True)
        # Se contesta ya, sin esperar el preprocesado: el operador en el campo no
        # tiene por qué quedarse mirando la pantalla mientras corre el picking.
        self._text(200, f"OK {job.job_id} encolado ({len(buf)} B)\n")

    def log_message(self, fmt: str, *args) -> None:
        pass   # los prints propios dicen lo que importa


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Servidor de datos Geophone")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--data-root", default=None,
                    help="dónde viven zips/, raw/ y jobs.json "
                         "(por defecto <repo>/data/server)")
    args = ap.parse_args(argv)

    if args.data_root:
        data_root = Path(args.data_root)
    else:
        # src/interfaces/python/server/app.py -> subir 4 = raíz del superproyecto
        data_root = Path(__file__).resolve().parents[4] / "data" / "server"
    data_root.mkdir(parents=True, exist_ok=True)

    Handler.pipeline = Pipeline(data_root)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"servidor de datos en http://{args.host}:{args.port}")
    print(f"datos en {data_root}")
    print(f"raw_root: {Handler.pipeline.raw_root}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\ncortado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
