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

# Cotas del dataset real (PORT_PLAN §0). Son mínimos, no igualdades: el dataset
# crece cuando se ingesta. Detectan el caso "el servidor ve 1 captura".
MIN_CAPTURES = 200
MIN_NODES = 400


# ── Registro de checks ────────────────────────────────────────────────────────
@dataclass
class Ctx:
    base_url: str
    raw_root: Path
    sandbox: bool = False

    def get(self, path: str, timeout_s: float = 30.0) -> tuple[int, bytes, dict]:
        req = urllib.request.Request(self.base_url + path)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def post(self, path: str, body: bytes = b"", headers: dict | None = None,
             timeout_s: float = 30.0) -> tuple[int, bytes, dict]:
        req = urllib.request.Request(self.base_url + path, data=body, method="POST")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def options(self, path: str, timeout_s: float = 30.0) -> tuple[int, dict]:
        req = urllib.request.Request(self.base_url + path, method="OPTIONS")
        req.add_header("Origin", "http://192.168.4.1")
        req.add_header("Access-Control-Request-Method", "POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                return r.status, dict(r.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers)

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
    log: list[str] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

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
                 boot_timeout_s: float = 60.0) -> Server | None:
    port = free_port()
    cmd = [sys.executable, "-m", "server", "--port", str(port),
           "--raw-root", str(raw_root), "--data-root", str(data_root)]
    proc = subprocess.Popen(cmd, cwd=str(PYTHON_ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    srv = Server(proc, port, tmp)
    deadline = time.time() + boot_timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            srv.log.append(proc.stdout.read() if proc.stdout else "")
            return None
        try:
            with urllib.request.urlopen(f"{srv.base_url}/health", timeout=2) as r:
                if r.status == 200:
                    return srv
        except Exception:
            time.sleep(0.4)
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

    results: list[dict] = []
    for mode in ("read", "sandbox"):
        batch = [c for c in selected if c.mode == mode]
        if not batch:
            continue
        tmp = None
        if mode == "read":
            tmp = Path(tempfile.mkdtemp(prefix="smoke_data_"))
            srv = start_server(raw_root, tmp / "server", tmp)
        else:
            tmp = Path(tempfile.mkdtemp(prefix="smoke_sandbox_"))
            (tmp / "raw").mkdir()
            srv = start_server(tmp / "raw", tmp / "server", tmp)
        if srv is None:
            print(f"[gate] el servidor no arrancó (modo {mode})", file=sys.stderr)
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
            return 2
        ctx = Ctx(srv.base_url, raw_root if mode == "read" else tmp / "raw",
                  sandbox=(mode == "sandbox"))
        print(f"[gate] servidor {mode} en {srv.base_url} (raw={ctx.raw_root})")
        try:
            for c in batch:
                started = time.monotonic()
                try:
                    c.fn(ctx)
                    ok, detail = True, ""
                except Exception as exc:
                    ok, detail = False, f"{type(exc).__name__}: {exc}"
                dt = time.monotonic() - started
                print(f"  {'PASS' if ok else 'FAIL'}  {c.cid:32s} {dt:5.1f}s  "
                      f"{detail or c.desc}")
                results.append({"id": c.cid, "desc": c.desc, "mode": c.mode,
                                "ok": ok, "detail": detail, "seconds": round(dt, 2)})
        finally:
            srv.stop()

    passed = sum(1 for r in results if r["ok"])
    print(f"\n[gate] {passed}/{len(results)} checks OK")
    if args.json:
        Path(args.json).write_text(
            json.dumps({"passed": passed, "total": len(results), "checks": results},
                       indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
