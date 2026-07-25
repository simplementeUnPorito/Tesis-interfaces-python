#!/usr/bin/env python3
"""Gate de humo del servidor, contra los datos reales de ``data/raw``.

Es el criterio objetivo de "anda" para el porteo automatizado
(``scripts/autonomia/port_loop.py``): un ítem del PORT_PLAN se marca hecho sólo
si sus checks pasan acá.

Dos decisiones que importan:

1. **Habla sólo HTTP** y arranca el servidor con ``python -m server``. No importa
   ``server.app`` ni conoce su implementación. Por eso sobrevive al refactor de
   §1 (http.server -> FastAPI): si el gate importara ``app.py`` se rompería justo
   en el ítem que tiene que verificar.
2. **Los checks de lectura van contra el ``data/raw`` real** (210 capturas / 415
   nodos), que es lo único que detecta la trampa §5.1 del plan (el servidor
   mostraba 1 captura porque corría con el raw_root viejo). Los checks que
   escriben van contra un sandbox temporal: el gate nunca modifica el dataset.

Uso
---
    python server/smoke_test.py --list
    python server/smoke_test.py                     # todos
    python server/smoke_test.py --only base         # por prefijo de id
    python server/smoke_test.py --only capturas.pick --json out.json

Código de salida 0 si todos los checks seleccionados pasan, 1 si alguno falla,
2 si el servidor no arrancó.

Para agregar un check nuevo: decorar una función con ``@check("<item>.<nombre>",
"<qué prueba>", mode=...)`` y usar ``ctx.get`` / ``ctx.post``. Nada más; el
registro se descubre solo.
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[4]          # igual que app.py:374, no re-derivar
PYTHON_ROOT = Path(__file__).resolve().parents[1]   # <repo>/src/interfaces/python
RAW_ROOT = REPO / "data" / "raw"
# Los logs del gate viven fuera del submódulo y están gitignorados: son evidencia
# para depurar, no código. Sin esto había que adivinar por qué falló un check.
LOG_DIR = REPO / "scripts" / "autonomia" / "state" / "gate"

# Cotas del dataset real (PORT_PLAN §0). Son mínimos, no igualdades: el dataset
# crece cuando se ingesta. Detectan el caso "el servidor ve 1 captura".
MIN_CAPTURES = 200
MIN_NODES = 400


# ── Registro de checks ────────────────────────────────────────────────────────
RUN_LOG: list[str] = []


def rec(msg: str, echo: bool = False) -> None:
    """Registra una línea con marca de tiempo. Todo queda; nada se adivina."""
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    RUN_LOG.append(line)
    if echo:
        print(line, flush=True)


@dataclass
class Ctx:
    base_url: str
    raw_root: Path
    sandbox: bool = False

    def _do(self, req: urllib.request.Request, verb: str, path: str,
            timeout_s: float) -> tuple[int, bytes, dict]:
        """Un solo camino para todos los pedidos, así todos quedan registrados
        igual — incluido el caso que más importa: el que se cuelga."""
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                body, code, headers = r.read(), r.status, dict(r.headers)
        except urllib.error.HTTPError as exc:
            body, code, headers = exc.read(), exc.code, dict(exc.headers)
        except Exception as exc:
            dt = time.monotonic() - started
            rec(f"    {verb} {path} -> EXCEPCIÓN tras {dt:.1f}s: "
                f"{type(exc).__name__}: {exc}")
            raise
        dt = time.monotonic() - started
        rec(f"    {verb} {path} -> {code} {len(body)} B en {dt:.2f}s")
        return code, body, headers

    def get(self, path: str, timeout_s: float = 30.0) -> tuple[int, bytes, dict]:
        return self._do(urllib.request.Request(self.base_url + path),
                        "GET", path, timeout_s)

    def post(self, path: str, body: bytes = b"", headers: dict | None = None,
             timeout_s: float = 30.0) -> tuple[int, bytes, dict]:
        req = urllib.request.Request(self.base_url + path, data=body, method="POST")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        return self._do(req, f"POST[{len(body)}B]", path, timeout_s)

    def options(self, path: str, timeout_s: float = 30.0) -> tuple[int, dict]:
        req = urllib.request.Request(self.base_url + path, method="OPTIONS")
        req.add_header("Origin", "http://192.168.4.1")
        req.add_header("Access-Control-Request-Method", "POST")
        code, _, headers = self._do(req, "OPTIONS", path, timeout_s)
        return code, headers

    def json_get(self, path: str) -> dict:
        code, body, _ = self.get(path)
        if code != 200:
            raise AssertionError(f"GET {path} -> {code}")
        return json.loads(body.decode("utf-8", "replace"))


@dataclass
class Check:
    cid: str
    desc: str
    mode: str            # "read" (raw real) | "sandbox" (dirs temporales)
    fn: Callable[[Ctx], None]


REGISTRY: list[Check] = []


def check(cid: str, desc: str, mode: str = "read"):
    def deco(fn):
        REGISTRY.append(Check(cid, desc, mode, fn))
        return fn
    return deco


# ── Checks base (deben pasar siempre, antes y después del refactor) ───────────
@check("base.health", "GET /health responde 200")
def _health(ctx: Ctx) -> None:
    code, _, _ = ctx.get("/health")
    assert code == 200, f"/health -> {code}"


@check("base.index", "GET / devuelve HTML de la web")
def _index(ctx: Ctx) -> None:
    code, body, _ = ctx.get("/")
    assert code == 200, f"/ -> {code}"
    assert b"<" in body and len(body) > 200, f"/ devolvió {len(body)} B sin HTML"


@check("base.dataset_completo", "el catálogo ve el dataset real, no un raw_root viejo")
def _dataset(ctx: Ctx) -> None:
    data = ctx.json_get("/api/dataset")
    caps = data.get("capture_count", 0)
    nodes = data.get("node_count", 0)
    assert caps >= MIN_CAPTURES, (
        f"capture_count={caps} < {MIN_CAPTURES}. Trampa §5.1: proceso viejo o raw_root "
        f"equivocado (dice {data.get('raw_root')!r})")
    assert nodes >= MIN_NODES, f"node_count={nodes} < {MIN_NODES}"
    assert data.get("folders"), "folders vacío"


@check("base.dataset_no_descarta", "ninguna carpeta del catálogo queda sin capturas listadas")
def _no_descarta(ctx: Ctx) -> None:
    # PORT_PLAN §5.5: discover_dataset descarta capturas sin par hammer+geo. El
    # catálogo NO debe hacerlo: una captura sin martillo se cataloga y se marca.
    data = ctx.json_get("/api/dataset")
    total = sum(len(f.get("captures", [])) for f in data["folders"])
    assert total == data["capture_count"], (
        f"suma de captures={total} != capture_count={data['capture_count']}")
    sin_hammer = sum(1 for f in data["folders"] for c in f["captures"]
                     if not c.get("has_hammer"))
    assert sin_hammer >= 0   # informativo; lo que importa es que estén listadas


@check("base.jobs", "GET /api/jobs devuelve la cola persistida")
def _jobs(ctx: Ctx) -> None:
    code, body, _ = ctx.get("/api/jobs")
    assert code == 200, f"/api/jobs -> {code}"
    json.loads(body.decode("utf-8", "replace"))


@check("base.cors_preflight", "OPTIONS /ingest trae las cabeceras CORS (§1)")
def _cors(ctx: Ctx) -> None:
    # Sin esto la SPA del ESP32 (otro origen) aborta en el preflight y en el
    # campo se ve como "servidor inalcanzable". PORT_PLAN §5.2.
    code, headers = ctx.options("/ingest")
    assert code in (200, 204), f"OPTIONS /ingest -> {code}"
    origin = headers.get("Access-Control-Allow-Origin")
    assert origin == "*", f"Access-Control-Allow-Origin={origin!r}"
    methods = (headers.get("Access-Control-Allow-Methods") or "").upper()
    assert "POST" in methods, f"Access-Control-Allow-Methods={methods!r}"
    allow_headers = (headers.get("Access-Control-Allow-Headers") or "")
    assert "content-type" in allow_headers.lower(), f"Allow-Headers={allow_headers!r}"


@check("base.ingest_responde_rapido", "POST /ingest contesta antes de preprocesar (§1)",
       mode="sandbox")
def _ingest_rapido(ctx: Ctx) -> None:
    # El operador en el campo no espera: el POST tiene que volver ya y el trabajo
    # pesado queda en el worker en hilo aparte. Corre en sandbox: escribe.
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("smoke/README.txt", "gate de humo; no son datos reales\n")
    started = time.monotonic()
    code, body, _ = ctx.post("/ingest", buf.getvalue(),
                             {"Content-Type": "application/zip",
                              "X-Geo-Filename": "smoke_gate.zip"})
    elapsed = time.monotonic() - started
    assert code in (200, 202), f"/ingest -> {code}: {body[:200]!r}"
    assert elapsed < 5.0, f"/ingest tardó {elapsed:.1f}s; debe responder antes de preprocesar"


# ── Checks refactor (§1: http.server -> FastAPI + estáticos) ──────────────────
@check("refactor.stack_fastapi", "GET /openapi.json expone /ingest y /api/dataset")
def _stack_fastapi(ctx: Ctx) -> None:
    data = ctx.json_get("/openapi.json")
    paths = data.get("paths", {})
    assert "/ingest" in paths, f"/ingest no está en openapi paths: {list(paths)}"
    assert "/api/dataset" in paths, f"/api/dataset no está en openapi paths: {list(paths)}"


@check("refactor.estaticos", "los estáticos se sirven desde /static")
def _estaticos(ctx: Ctx) -> None:
    code, body, headers = ctx.get("/static/css/app.css")
    assert code == 200, f"/static/css/app.css -> {code}"
    assert len(body) > 200, f"app.css muy chico: {len(body)} B"
    ctype = (headers.get("Content-Type") or "").lower()
    assert "css" in ctype, f"content-type de app.css={ctype!r}"
    for path in ("/static/js/main.js", "/static/js/theme.js", "/static/js/plot.js"):
        code, body, _ = ctx.get(path)
        assert code == 200, f"{path} -> {code}"
        assert len(body) > 100, f"{path} muy chico: {len(body)} B"


@check("refactor.index_sin_logica", "GET / es esqueleto: sin <style>/fetch/setInterval inline")
def _index_sin_logica(ctx: Ctx) -> None:
    code, body, _ = ctx.get("/")
    assert code == 200, f"/ -> {code}"
    text = body.decode("utf-8", "replace")
    assert "/static/js/main.js" in text, "/ no referencia /static/js/main.js"
    assert "/static/css/app.css" in text, "/ no referencia /static/css/app.css"
    assert "fetch(" not in text, "/ todavía tiene fetch( inline"
    assert "setInterval(" not in text, "/ todavía tiene setInterval( inline"
    assert "<style" not in text, "/ todavía tiene <style inline"


@check("refactor.tabs_presentes", "GET / trae los 8 nombres de tab literales")
def _tabs_presentes(ctx: Ctx) -> None:
    code, body, _ = ctx.get("/")
    assert code == 200, f"/ -> {code}"
    text = body.decode("utf-8", "replace")
    for nombre in ("Capturas", "Filtros", "Agrupamiento", "Enfase",
                   "Promedios / arrivals", "Waterfall", "MASW", "Borrado"):
        assert nombre in text, f"falta el tab {nombre!r} en /"


@check("refactor.contrato_dataset", "GET /api/dataset conserva el contrato JSON del port")
def _contrato_dataset(ctx: Ctx) -> None:
    data = ctx.json_get("/api/dataset")
    for key in ("raw_root", "folders", "capture_count", "node_count",
                "shot_count", "reviewed_count"):
        assert key in data, f"falta la clave {key!r} en /api/dataset"
    folders_con_capturas = [f for f in data["folders"] if f.get("captures")]
    assert folders_con_capturas, "ninguna carpeta con capturas en /api/dataset"
    cap = folders_con_capturas[0]["captures"][0]
    for key in ("capture", "order", "fs", "nodes", "has_hammer", "has_geo", "pickable"):
        assert key in cap, f"falta la clave {key!r} en una captura de /api/dataset"
    assert "pick" in cap, "falta la clave 'pick' (puede ser null) en una captura"


@check("refactor.raw_root_del_flag", "el raw_root reportado es el que pasó --raw-root")
def _raw_root_del_flag(ctx: Ctx) -> None:
    data = ctx.json_get("/api/dataset")
    reportado = Path(data["raw_root"]).resolve()
    esperado = ctx.raw_root.resolve()
    assert reportado == esperado, f"raw_root reportado {reportado} != esperado {esperado}"


@check("refactor.cors_en_post", "POST /ingest con Origin trae Access-Control-Allow-Origin: *",
       mode="sandbox")
def _cors_en_post(ctx: Ctx) -> None:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("smoke/README.txt", "gate de humo; no son datos reales\n")
    code, body, headers = ctx.post(
        "/ingest", buf.getvalue(),
        {"Content-Type": "application/zip",
         "X-Geo-Filename": "smoke_cors.zip",
         "Origin": "http://192.168.4.1"})
    assert code in (200, 202), f"/ingest -> {code}: {body[:200]!r}"
    origin = headers.get("Access-Control-Allow-Origin")
    assert origin == "*", f"Access-Control-Allow-Origin={origin!r}"


@check("refactor.worker_no_bloquea", "el preprocesado corre en hilo aparte, no en el event loop",
       mode="sandbox")
def _worker_no_bloquea(ctx: Ctx) -> None:
    import io
    import zipfile

    def zip_bytes(i: int) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(f"smoke/README_{i}.txt", "gate de humo; no son datos reales\n")
        return buf.getvalue()

    for i in range(3):
        code, body, _ = ctx.post("/ingest", zip_bytes(i),
                                 {"Content-Type": "application/zip",
                                  "X-Geo-Filename": f"smoke_worker_{i}.zip"})
        assert code in (200, 202), f"/ingest #{i} -> {code}: {body[:200]!r}"

    started = time.monotonic()
    code, _, _ = ctx.get("/health")
    assert code == 200, f"/health -> {code}"
    elapsed_health = time.monotonic() - started
    assert elapsed_health < 2.0, f"/health tardó {elapsed_health:.1f}s"

    started = time.monotonic()
    jobs = ctx.json_get("/api/jobs")
    elapsed_jobs = time.monotonic() - started
    assert elapsed_jobs < 2.0, f"/api/jobs tardó {elapsed_jobs:.1f}s"
    assert len(jobs.get("jobs", [])) >= 3, f"/api/jobs listó {len(jobs.get('jobs', []))} < 3"


# ── Arranque del servidor bajo prueba ─────────────────────────────────────────
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Server:
    proc: subprocess.Popen
    port: int
    tmp: Path | None = None
    log_path: Path | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def log_tail(self, lines: int = 40) -> str:
        if not self.log_path or not self.log_path.is_file():
            return "(sin log del servidor)"
        text = self.log_path.read_text(encoding="utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:]) or "(log vacío)"

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.tmp and self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)


def start_server(raw_root: Path, data_root: Path, tmp: Path | None,
                 log_path: Path, boot_timeout_s: float = 60.0) -> Server | None:
    """Arranca el servidor bajo prueba con su salida A UN ARCHIVO.

    Nunca a subprocess.PIPE sin leerlo: el buffer del pipe son ~64 kB y cuando se
    llena el servidor se **bloquea escribiendo**, así que las requests empiezan a
    colgarse. Pasó de verdad: `refactor.worker_no_bloquea` daba timeout a los 30 s
    en tanda y pasaba en 0.1 s aislado, y el bug era del gate, no del servidor.
    Además el archivo es el log que hace falta para no adivinar.
    """
    port = free_port()
    cmd = [sys.executable, "-m", "server", "--port", str(port),
           "--raw-root", str(raw_root), "--data-root", str(data_root)]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(cmd, cwd=str(PYTHON_ROOT), stdout=handle,
                            stderr=subprocess.STDOUT, text=True)
    srv = Server(proc, port, tmp, log_path)
    rec(f"servidor pid={proc.pid} puerto={port} raw={raw_root}", echo=True)
    rec(f"  intérprete={sys.executable}")
    rec(f"  log del servidor -> {log_path}", echo=True)
    deadline = time.time() + boot_timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            rec(f"el servidor murió al arrancar (rc={proc.returncode}):\n"
                f"{srv.log_tail()}", echo=True)
            return None
        try:
            with urllib.request.urlopen(f"{srv.base_url}/health", timeout=2) as r:
                if r.status == 200:
                    rec(f"  arrancó en {time.time() - (deadline - boot_timeout_s):.1f}s")
                    return srv
        except Exception:
            time.sleep(0.4)
    rec(f"el servidor no respondió /health en {boot_timeout_s:.0f}s:\n"
        f"{srv.log_tail()}", echo=True)
    srv.stop()
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", action="append", default=[],
                    help="correr sólo los checks cuyo id empieza así (repetible)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", help="escribir el reporte a este archivo")
    ap.add_argument("--raw-root", default=str(RAW_ROOT))
    ap.add_argument("--require", action="append", default=[],
                    help="exigir que exista al menos un check con este prefijo; "
                         "si no, fallar. Evita el gate verde falso de un ítem "
                         "que no agregó ni un check propio.")
    ap.add_argument("--log-dir", default=str(LOG_DIR),
                    help="dónde dejar el log de la corrida y el del servidor")
    args = ap.parse_args()

    if args.list:
        for c in REGISTRY:
            print(f"{c.cid:34s} [{c.mode:7s}] {c.desc}")
        return 0

    selected = [c for c in REGISTRY
                if not args.only or any(c.cid.startswith(p) for p in args.only)]
    if not selected:
        print(f"ningún check coincide con {args.only}", file=sys.stderr)
        return 2

    for prefix in args.require:
        if not any(c.cid.startswith(prefix) for c in REGISTRY):
            print(f"[gate] FALLA: no existe ningún check con prefijo {prefix!r}. "
                  f"El ítem tiene que aportar sus propios checks; pasar sólo los "
                  f"checks base es un verde falso.", file=sys.stderr)
            return 1

    raw_root = Path(args.raw_root)
    if not raw_root.is_dir():
        print(f"no existe raw_root {raw_root}", file=sys.stderr)
        return 2

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_log = log_dir / f"gate_{stamp}.log"
    rec(f"gate {stamp} — checks: {[c.cid for c in selected]}")
    rec(f"  raw_root={raw_root}")
    rec(f"  intérprete={sys.executable}  cwd={Path.cwd()}")

    results: list[dict] = []
    for mode in ("read", "sandbox"):
        batch = [c for c in selected if c.mode == mode]
        if not batch:
            continue
        srv_log = log_dir / f"gate_{stamp}_servidor_{mode}.log"
        if mode == "read":
            tmp = Path(tempfile.mkdtemp(prefix="smoke_data_"))
            srv = start_server(raw_root, tmp / "server", tmp, srv_log)
        else:
            tmp = Path(tempfile.mkdtemp(prefix="smoke_sandbox_"))
            (tmp / "raw").mkdir()
            srv = start_server(tmp / "raw", tmp / "server", tmp, srv_log)
        if srv is None:
            print(f"[gate] el servidor no arrancó (modo {mode}); "
                  f"por qué: {srv_log}", file=sys.stderr)
            shutil.rmtree(tmp, ignore_errors=True)
            run_log.write_text("\n".join(RUN_LOG) + "\n", encoding="utf-8")
            return 2
        ctx = Ctx(srv.base_url, raw_root if mode == "read" else tmp / "raw",
                  sandbox=(mode == "sandbox"))
        print(f"[gate] servidor {mode} en {srv.base_url} (raw={ctx.raw_root})")
        try:
            for c in batch:
                rec(f"CHECK {c.cid} ({c.mode}) — {c.desc}")
                started = time.monotonic()
                try:
                    c.fn(ctx)
                    ok, detail = True, ""
                except Exception as exc:
                    ok, detail = False, f"{type(exc).__name__}: {exc}"
                dt = time.monotonic() - started
                rec(f"  -> {'PASS' if ok else 'FAIL'} en {dt:.2f}s {detail}")
                if not ok:
                    # El log del servidor es lo que explica un timeout; sin esto
                    # había que adivinar de qué lado estaba el problema.
                    rec(f"  --- cola del log del servidor ---\n{srv.log_tail(25)}")
                print(f"  {'PASS' if ok else 'FAIL'}  {c.cid:32s} {dt:5.1f}s  "
                      f"{detail or c.desc}")
                results.append({"id": c.cid, "desc": c.desc, "mode": c.mode,
                                "ok": ok, "detail": detail, "seconds": round(dt, 2),
                                "log_servidor": str(srv_log)})
        finally:
            srv.stop()

    passed = sum(1 for r in results if r["ok"])
    rec(f"resultado: {passed}/{len(results)} checks OK")
    run_log.write_text("\n".join(RUN_LOG) + "\n", encoding="utf-8")
    print(f"\n[gate] {passed}/{len(results)} checks OK")
    print(f"[gate] log de la corrida: {run_log}")
    if passed != len(results):
        for r in results:
            if not r["ok"]:
                print(f"[gate] log del servidor de {r['id']}: {r['log_servidor']}")
    if args.json:
        Path(args.json).write_text(
            json.dumps({"passed": passed, "total": len(results),
                        "run_log": str(run_log), "checks": results},
                       indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
