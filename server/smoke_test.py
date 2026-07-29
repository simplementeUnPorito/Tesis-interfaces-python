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
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
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

# Contrato del gate completo. Es deliberadamente explícito: si durante una
# refactorización se borra el decorador de una comprobación, "todos los checks
# registrados pasaron" ya no alcanza para producir un verde falso.
REQUIRED_FULL_GATE_IDS = frozenset({
    "base.health",
    "base.meta",
    "base.read_only_guard",
    "base.interfaces_publicas",
    "distribucion.contrato",
    "distribucion.data_root_aisla_todo",
    "base.index",
    "base.dataset_completo",
    "base.dataset_no_descarta",
    "base.jobs",
    "base.cors_preflight",
    "base.ingest_responde_rapido",
    "refactor.stack_fastapi",
    "refactor.estaticos",
    "refactor.index_sin_logica",
    "refactor.tabs_presentes",
    "ui.waterfall_masw_handoff",
    "refactor.contrato_dataset",
    "refactor.raw_root_del_flag",
    "refactor.cors_en_post",
    "refactor.worker_no_bloquea",
    "ingesta.zip_seguro_idempotente",
    "ingesta.limites_env_finitos",
    "tabs.nombres_y_orden",
    "tabs.masw_subtabs",
    "tabs.panel_por_tab",
    "tabs.sin_placeholders",
    "filtros.ajustes_persisten",
    "filtros.preview_fase_cero",
    "filtros.supresor_armonico_adaptativo",
    "estado.revision_optimista",
    "estado.campanas_revision",
    "waterfall.vista_vs_persistente",
    "masw.geometria_multigrupo",
    "masw.sintetico_semiespacio_multimodo",
    "enfase.clave_compartida",
    "enfase.auto_y_restauracion",
    "tabs.tema_toggle",
    "tabs.tema_sin_flash",
    "capturas.signal.contrato",
    "capturas.signal.decimado_conserva_picos",
    "capturas.signal.max_points_clamp",
    "capturas.signal.sin_nan_en_json",
    "capturas.signal.kind_filt",
    "capturas.signal.shot_desconocido",
    "capturas.signal.trigger_marcado",
    "capturas.signal.no_bloquea",
    "capturas.signal.ui_usa_endpoint",
    "capturas.signal.polaridad_fija",
    "capturas.signal.nan_a_null",
    "capturas.signal.geo_flip_override",
    "tabs.implementacion_conectada",
    "masw.canchita_compatibilidad",
    "masw.roundtrip_json_npz",
    "masw.trabajos_artefactos_cancelacion",
    "reinicio.trabajos_persistentes",
    "pipeline.e2e_zip_a_vs",
    "borrado.preview_confirmacion",
})


def check(cid: str, desc: str, mode: str = "read"):
    def deco(fn):
        REGISTRY.append(Check(cid, desc, mode, fn))
        return fn
    return deco


def _master_zip(
    *,
    prefix: str = "",
    extra: list[tuple[str, bytes]] | None = None,
    compression: int | None = None,
) -> bytes:
    """ZIP mínimo con el mismo contrato estructural que exporta el master."""
    import io
    import zipfile

    root = prefix.strip("/")
    root = f"{root}/" if root else ""
    method = zipfile.ZIP_STORED if compression is None else compression
    metadata = json.dumps({
        "schema": "geophone_scope_web_zip_v4",
        "schema_target": "geophone_scope_mat_node_prefix_v1",
        "source": "server_smoke_test",
        "layout": "maestro_metadata_plus_capture_metadata_and_pcb_dirs",
    })
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=method) as zf:
        zf.writestr(f"{root}metadata.json", metadata)
        zf.writestr(f"{root}maestro/metadata.json", metadata)
        zf.writestr(
            f"{root}captures/001_smoke/metadata.json",
            json.dumps({"capture_index": 1, "event": "smoke"}),
        )
        zf.writestr(
            f"{root}captures/001_smoke/geo1_s1/raw_f32le.bin",
            b"\0\0\0\0",
        )
        for name, payload in extra or []:
            zf.writestr(f"{root}{name.lstrip('/')}", payload)
    return buf.getvalue()


# ── Checks base (deben pasar siempre, antes y después del refactor) ───────────
@check("base.health", "GET /health responde 200")
def _health(ctx: Ctx) -> None:
    code, _, _ = ctx.get("/health")
    assert code == 200, f"/health -> {code}"


@check("base.meta", "/api/meta identifica proceso, versión, uptime y raíces activas")
def _meta(ctx: Ctx) -> None:
    data = ctx.json_get("/api/meta")
    for key in ("version", "git", "started_at", "uptime_s", "pid", "roots"):
        assert key in data, f"/api/meta no trae {key!r}: {data}"
    assert int(data["pid"]) > 0, data
    assert float(data["uptime_s"]) >= 0, data
    assert Path(data["roots"]["raw"]).resolve() == ctx.raw_root.resolve(), data["roots"]
    assert data["roots"].get("server"), data["roots"]
    assert Path(data["roots"]["processed"]).resolve() == (
        ctx.raw_root.parent / "processed"
    ).resolve(), data["roots"]


@check(
    "base.read_only_guard",
    "el servidor del gate sobre datos reales rechaza todos los POST",
)
def _read_only_guard(ctx: Ctx) -> None:
    meta = ctx.json_get("/api/meta")
    assert meta.get("read_only") is True, meta
    code, raw, _ = ctx.post(
        "/api/masw/state",
        b"{}",
        {"Content-Type": "application/json"},
    )
    assert code == 405, f"POST en modo read-only -> {code}: {raw[:200]!r}"
    detail = json.loads(raw).get("detail", "")
    assert "sólo lectura" in detail, detail


@check("base.interfaces_publicas", "OpenAPI expone todas las interfaces nuevas del pipeline")
def _interfaces_publicas(ctx: Ctx) -> None:
    paths = ctx.json_get("/openapi.json").get("paths", {})
    expected = {
        "/api/meta", "/api/jobs/{job_id}", "/api/jobs/{job_id}/cancel",
        "/api/exports", "/api/artifacts/{artifact_id}", "/api/masw/state",
        "/api/masw/dispersion", "/api/masw/inversions", "/api/deletion",
        "/api/deletion/preview", "/api/deletion/delete",
    }
    missing = sorted(expected - set(paths))
    assert not missing, f"faltan rutas públicas: {missing}"
    invalid_methods = sorted(
        f"{method.upper()} {path}"
        for path, operations in paths.items()
        for method in operations
        if method.lower() in {"put", "patch", "delete"}
    )
    assert not invalid_methods, \
        f"el contrato ESP/navegador sólo admite GET/POST: {invalid_methods}"
    assert not any("geoq" in path.lower() for path in paths), \
        "el transporte .geoq quedó expuesto aunque está fuera de alcance"


@check(
    "distribucion.contrato",
    "Docker, volúmenes, healthcheck, usuario no privilegiado y dependencias fijadas",
)
def _distribucion_contrato(ctx: Ctx) -> None:
    dockerfile = (PYTHON_ROOT / "server" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    compose = (PYTHON_ROOT / "server" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    for token in (
        "USER geophone",
        "HEALTHCHECK",
        "http://127.0.0.1:8000/health",
        'VOLUME ["/data/raw", "/data/processed", "/data/server"]',
    ):
        assert token in dockerfile, f"Dockerfile no contiene {token!r}"
    for token in (
        "/data/raw",
        "/data/processed",
        "/data/server",
        "restart: unless-stopped",
    ):
        assert token in compose, f"docker-compose.yml no contiene {token!r}"

    for filename in ("requirements-core.txt", "requirements-full.txt"):
        lines = (PYTHON_ROOT / "server" / filename).read_text(
            encoding="utf-8"
        ).splitlines()
        unpinned = [
            line.strip()
            for line in lines
            if line.strip()
            and not line.lstrip().startswith(("#", "-r "))
            and "==" not in line
        ]
        assert not unpinned, f"{filename} tiene dependencias sin fijar: {unpinned}"


@check(
    "distribucion.data_root_aisla_todo",
    "TESIS_DATA_ROOT por sí sola mueve raw, processed, ZIPs y jobs",
    mode="sandbox",
)
def _data_root_aisla_todo(ctx: Ctx) -> None:
    repo_server = REPO / "data" / "server"

    def snapshot() -> dict[str, tuple[int, int]]:
        if not repo_server.is_dir():
            return {}
        return {
            str(path.relative_to(repo_server)): (
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
            for path in repo_server.rglob("*")
            if path.is_file()
        }

    before = snapshot()
    isolated = Path(tempfile.mkdtemp(prefix="smoke_storage_root_"))
    log_path = LOG_DIR / f"storage_root_{time.strftime('%Y%m%d_%H%M%S')}.log"
    srv = start_server_from_storage_root(isolated, log_path)
    assert srv is not None, f"servidor aislado no arrancó: {log_path}"
    try:
        nested = Ctx(srv.base_url, isolated / "raw", sandbox=True)
        meta = nested.json_get("/api/meta")
        expected = {
            "raw": isolated / "raw",
            "processed": isolated / "processed",
            "server": isolated / "server",
        }
        for key, path in expected.items():
            assert Path(meta["roots"][key]).resolve() == path.resolve(), (
                key, meta["roots"], expected
            )
        code, raw, _ = nested.post(
            "/ingest",
            _master_zip(),
            {
                "Content-Type": "application/zip",
                "X-Geo-Filename": "storage_root_only.zip",
            },
        )
        assert code == 200, f"ingesta aislada -> {code}: {raw[:200]!r}"
        assert list((isolated / "server" / "zips").glob("*.zip")), \
            "el ZIP no quedó bajo TESIS_DATA_ROOT/server"
        assert (isolated / "server" / "jobs.json").is_file(), \
            "jobs.json no quedó bajo TESIS_DATA_ROOT/server"
    finally:
        srv.stop()
    assert snapshot() == before, \
        "arrancar sólo con TESIS_DATA_ROOT tocó el data/server real"


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
    started = time.monotonic()
    code, body, _ = ctx.post("/ingest", _master_zip(prefix="smoke_fast"),
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


@check(
    "ui.waterfall_masw_handoff",
    "wiggle responde en el click, el handoff conserva campaña y los motores son elegibles",
)
def _waterfall_masw_handoff(ctx: Ctx) -> None:
    static = PYTHON_ROOT / "server" / "static"
    waterfall_js = (static / "js" / "tabs" / "waterfall.js").read_text(
        encoding="utf-8"
    )
    masw_js = (static / "js" / "tabs" / "masw.js").read_text(encoding="utf-8")
    main_js = (static / "js" / "main.js").read_text(encoding="utf-8")
    index = (static / "index.html").read_text(encoding="utf-8")

    assert "patchVistaPendiente = { ...patchVistaPendiente, ...patch }" in waterfall_js
    assert "while (Object.keys(patchVistaPendiente).length" in waterfall_js
    assert "base_revision: data.revision || ''" in waterfall_js
    assert "Este mensaje va DESPUÉS de load()" in waterfall_js
    assert "geo-masw-request" in waterfall_js
    assert "campaign = selectedCampaign" in masw_js
    assert "const calculated = await calculate()" in masw_js
    assert "el envío se conservó para reintentar" in masw_js
    assert "se descartó el envío de" in masw_js
    assert "sin conexión al trabajo; reintentando" in masw_js
    assert "loadAll({ consumeRequest: false })" in masw_js
    assert "mio !== pedidoCarga || campaignPedida !== campaign" in masw_js
    assert "item?.can_launch === true" in masw_js
    assert "masw().backend = $('#mi-backend').value" in masw_js
    assert "!b.available && b.kind !== 'export' ? ' disabled'" not in masw_js
    for filename in (
        "waterfall.js", "agrupamiento.js", "enfase.js", "promedios.js",
    ):
        tab_js = (static / "js" / "tabs" / filename).read_text(
            encoding="utf-8"
        )
        assert "resume() { picker.reload(); load(); }" not in tab_js, filename
        assert "picker.reload().then((id) =>" in tab_js, filename
        assert "campaign = id;" in tab_js, filename
    assert 'id="server-meta"' in index
    assert "servidor anterior · reiniciar" in main_js


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
    code, body, headers = ctx.post(
        "/ingest", _master_zip(prefix="smoke_cors"),
        {"Content-Type": "application/zip",
         "X-Geo-Filename": "smoke_cors.zip",
         "Origin": "http://192.168.4.1"})
    assert code in (200, 202), f"/ingest -> {code}: {body[:200]!r}"
    origin = headers.get("Access-Control-Allow-Origin")
    assert origin == "*", f"Access-Control-Allow-Origin={origin!r}"


@check("refactor.worker_no_bloquea", "el preprocesado corre en hilo aparte, no en el event loop",
       mode="sandbox")
def _worker_no_bloquea(ctx: Ctx) -> None:
    def zip_bytes(i: int) -> bytes:
        return _master_zip(prefix=f"smoke_worker_{i}")

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


@check(
    "ingesta.zip_seguro_idempotente",
    "valida estructura/límites/traversal/truncado/bomba, deduplica y conserva ZIP",
    mode="sandbox",
)
def _ingesta_segura(ctx: Ctx) -> None:
    import io
    import zipfile
    from concurrent.futures import ThreadPoolExecutor

    def archive(name: str, payload: bytes, *, compression=zipfile.ZIP_STORED) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=compression) as zf:
            zf.writestr(name, payload)
        return buf.getvalue()

    # El master real pone metadata.json en la raíz, sin carpeta contenedora.
    root_payload = _master_zip()
    code, raw, _ = ctx.post(
        "/ingest",
        root_payload,
        {
            "Content-Type": "application/zip",
            "X-Geo-Filename": "smoke_layout_real_sin_prefijo.zip",
        },
    )
    assert code == 200, f"layout real sin prefijo -> {code}: {raw[:200]!r}"

    payload = _master_zip(prefix="smoke_idem")
    headers = {
        "Content-Type": "application/zip",
        "X-Geo-Filename": "smoke_idempotente.zip",
    }
    code1, body1, h1 = ctx.post("/ingest", payload, headers)
    code2, body2, h2 = ctx.post("/ingest", payload, headers)
    assert code1 == 200 and code2 == 200, (code1, body1[:100], code2, body2[:100])
    job1 = h1.get("X-Geo-Job")
    job2 = h2.get("X-Geo-Job")
    assert job1 and job1 == job2, f"reintento duplicó trabajo: {job1!r} != {job2!r}"

    concurrent_payload = _master_zip(prefix="smoke_idem_concurrent")
    concurrent_headers = {
        "Content-Type": "application/zip",
        "X-Geo-Filename": "smoke_idempotente_concurrente.zip",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: ctx.post(
                    "/ingest", concurrent_payload, concurrent_headers
                ),
                range(2),
            )
        )
    concurrent_ids = [response[2].get("X-Geo-Job") for response in responses]
    assert all(response[0] == 200 for response in responses), responses
    assert concurrent_ids[0] and len(set(concurrent_ids)) == 1, (
        f"reintentos simultáneos publicaron trabajos distintos: {concurrent_ids}"
    )

    traversal = _master_zip(extra=[("../escape.txt", b"nunca")])
    code, raw, _ = ctx.post("/ingest", traversal, headers)
    assert code == 400 and b"ruta sospechosa" in raw, (
        f"ZIP traversal -> {code}: {raw[:200]!r}"
    )
    assert not (ctx.raw_root.parent / "escape.txt").exists(), "el traversal escribió fuera de raw"

    code, raw, _ = ctx.post("/ingest", payload[:-9], headers)
    assert code == 400, f"ZIP truncado -> {code}: {raw[:200]!r}"

    unexpected = archive("README.txt", b"no es una captura del master\n")
    code, raw, _ = ctx.post("/ingest", unexpected, headers)
    assert code == 400 and b"estructura inesperada" in raw, (
        f"ZIP arbitrario -> {code}: {raw[:200]!r}"
    )

    too_many = _master_zip(
        prefix="smoke_many",
        extra=[(f"extra/{index:03d}.txt", b"x") for index in range(61)],
    )
    code, raw, _ = ctx.post("/ingest", too_many, headers)
    assert code == 400 and b"demasiados archivos" in raw, (
        f"límite de archivos -> {code}: {raw[:200]!r}"
    )

    too_expanded = _master_zip(
        prefix="smoke_expanded",
        extra=[("extra/payload.bin", b"x" * (1024 * 1024))],
    )
    code, raw, _ = ctx.post("/ingest", too_expanded, headers)
    assert code == 400 and b"expandido demasiado grande" in raw, (
        f"límite expandido -> {code}: {raw[:200]!r}"
    )

    bomb = _master_zip(
        prefix="smoke_bomb",
        extra=[("extra/zeros.bin", b"\0" * (512 * 1024))],
        compression=zipfile.ZIP_DEFLATED,
    )
    code, raw, _ = ctx.post("/ingest", bomb, headers)
    assert code == 400 and b"relaci" in raw and b"sospechosa" in raw, (
        f"relación de compresión extrema -> {code}: {raw[:200]!r}"
    )

    unsupported = _master_zip(
        extra=[("extra/bzip2.bin", b"metodo no permitido")],
        compression=zipfile.ZIP_BZIP2,
    )
    code, raw, _ = ctx.post("/ingest", unsupported, headers)
    assert code == 400 and b"compresi" in raw and b"no soportada" in raw, (
        f"método de compresión no permitido -> {code}: {raw[:200]!r}"
    )

    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        duplicate = _master_zip(
            extra=[("metadata.json", b'{"schema":"otro"}')]
        )
    code, raw, _ = ctx.post("/ingest", duplicate, headers)
    assert code == 400 and b"duplicada" in raw, (
        f"ruta duplicada -> {code}: {raw[:200]!r}"
    )

    over_upload = b"x" * (2 * 1024 * 1024 + 1)
    code, raw, _ = ctx.post("/ingest", over_upload, headers)
    assert code == 413, f"límite de subida -> {code}: {raw[:200]!r}"

    deadline = time.time() + 15
    job = {}
    while time.time() < deadline:
        job = ctx.json_get(f"/api/jobs/{job1}")
        if job.get("state") in {"listo", "error"}:
            break
        time.sleep(0.2)
    assert job.get("state") == "listo", f"trabajo idempotente no terminó: {job}"
    for key in (
        "id", "kind", "state", "stage", "progress", "created_at",
        "started_at", "finished_at", "error",
    ):
        assert key in job, f"trabajo sin {key!r}: {job}"

    roots = ctx.json_get("/api/meta")["roots"]
    zip_path = Path(roots["server"]) / "zips" / f"{job1}.zip"
    assert zip_path.is_file(), f"el ZIP original no fue retenido: {zip_path}"
    staging = ctx.raw_root / ".ingest_staging" / str(job1)
    assert not staging.exists(), f"quedó publicación temporal: {staging}"


@check(
    "ingesta.limites_env_finitos",
    "los límites rechazan NaN, infinito, cero, negativos y texto al arrancar",
    mode="sandbox",
)
def _limites_env_finitos(ctx: Ctx) -> None:
    for invalid in ("nan", "inf", "-inf", "0", "-1", "abc"):
        env = dict(os.environ)
        env["TESIS_MAX_COMPRESSION_RATIO"] = invalid
        result = subprocess.run(
            [sys.executable, "-c", "import server.limits"],
            cwd=str(PYTHON_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode != 0, (
            invalid, result.stdout, result.stderr
        )


# ── Checks tabs (§2: tabs de navegación + toggle de tema) ─────────────────────
def _index_text(ctx: Ctx) -> str:
    code, body, _ = ctx.get("/")
    assert code == 200, f"/ -> {code}"
    return body.decode("utf-8", "replace")


TAB_NAMES = ("Capturas", "Filtros", "Agrupamiento", "Enfase",
             "Promedios / arrivals", "Waterfall", "MASW", "Borrado")

MASW_SUBTABS = (
    ("1. Dispersion", "dispersion"),
    ("2. Inversion", "inversion"),
    ("3. Perfil Vs", "perfil"),
)

# `filtros` salió de esta lista el 2026-07-26 (§3.2) y `waterfall` el mismo día
# (§3.4): ya están portados, así que sus paneles los llena el JS y no tienen
# —ni deben tener— un placeholder.
PANELES_PENDIENTES = ()


@check("tabs.nombres_y_orden", "los 8 tabs de la app aparecen en / en el orden exacto")
def _tabs_nombres_y_orden(ctx: Ctx) -> None:
    text = _index_text(ctx)
    positions = []
    for nombre in TAB_NAMES:
        # Bordes de letra: si no, "Waterfall" matchea dentro de "Waterfalll".
        m = re.search(r'(?<![A-Za-z])' + re.escape(nombre) + r'(?![A-Za-z])', text)
        assert m, f"falta el tab {nombre!r} en /"
        positions.append(m.start())
    for a, b, nombre_a, nombre_b in zip(positions, positions[1:], TAB_NAMES, TAB_NAMES[1:]):
        assert a < b, f"orden de tabs roto: {nombre_a!r} debería ir antes que {nombre_b!r}"
    count = text.count('data-tab="')
    assert count == 8, f'se esperaban 8 data-tab="..." y hay {count}'


@check("tabs.masw_subtabs", "las 3 subtabs de MASW están en / con sus data-subtab e id")
def _tabs_masw_subtabs(ctx: Ctx) -> None:
    text = _index_text(ctx)
    for nombre, subtab in MASW_SUBTABS:
        assert nombre in text, f"falta la subtab {nombre!r} en /"
        assert f'data-subtab="{subtab}"' in text, f'falta data-subtab="{subtab}" en /'
        assert f'id="subpanel-{subtab}"' in text, f'falta id="subpanel-{subtab}" en /'
    code, body, _ = ctx.get("/static/css/app.css")
    assert code == 200, f"app.css -> {code}"
    assert ".subtab-panel[hidden]" in body.decode("utf-8", "replace"), (
        "las subtabs ocultas pueden reaparecer por la regla posterior "
        "`.subtab-panel { display:block }`"
    )


@check("tabs.panel_por_tab", "cada data-tab tiene su panel-<X> y no hay paneles huérfanos")
def _tabs_panel_por_tab(ctx: Ctx) -> None:
    text = _index_text(ctx)
    tabs = re.findall(r'data-tab="([a-z]+)"', text)
    assert tabs, "no se encontró ningún data-tab en /"
    for name in tabs:
        assert f'id="panel-{name}"' in text, (
            f'el botón data-tab="{name}" no tiene su id="panel-{name}"')
    panels = re.findall(r'id="panel-([a-z]+)"', text)
    for name in panels:
        assert f'data-tab="{name}"' in text, (
            f'panel-{name} no tiene ningún botón data-tab="{name}" (panel huérfano)')


@check("tabs.sin_placeholders", "ningún tab terminado conserva carteles de implementación pendiente")
def _tabs_placeholders_honestos(ctx: Ctx) -> None:
    text = _index_text(ctx)
    assert "Falta:" not in text, "la interfaz conserva un placeholder «Falta:»"
    assert "Pendiente · PORT_PLAN" not in text, "la interfaz conserva un cartel pendiente"
    # Límites de bloque: el próximo id="panel-..." o id="subpanel-..." (o fin de
    # documento). Nunca se confunden entre sí: "subpanel-" no contiene la
    # subcadena literal 'id="panel-'.
    markers = [(m.start(), m.group(1))
               for m in re.finditer(r'id="((?:sub)?panel-[a-z]+)"', text)]
    for target in PANELES_PENDIENTES:
        marker_id = target if target.startswith("subpanel-") else f"panel-{target}"
        starts = [pos for pos, name in markers if name == marker_id]
        assert starts, f'no se encontró id="{marker_id}" en /'
        start = starts[0]
        later = [pos for pos, _ in markers if pos > start]
        end = min(later) if later else len(text)
        block = text[start:end]
        assert "Falta:" in block, f'{marker_id}: falta el literal "Falta:" en su placeholder'
        assert "§" in block, f'{marker_id}: falta la referencia "§" al PORT_PLAN'
        assert len(block) >= 120, f'{marker_id}: placeholder de {len(block)} caracteres (< 120)'


@check("filtros.ajustes_persisten", "GET/POST /api/filter guarda en el archivo de la app",
      mode="sandbox")
def _filtros_ajustes(ctx: Ctx) -> None:
    antes = ctx.json_get("/api/filter")
    for clave in (
        "enabled", "low_hz", "high_hz", "order", "target_fs", "dc_enabled",
        "line_suppress_enabled", "line_f0_hz", "line_harmonics",
        "line_search_hz", "path", "revision",
    ):
        assert clave in antes, f"/api/filter sin {clave!r}: {antes}"
    # El archivo tiene que ser el MISMO que lee la app PyQt, no uno paralelo.
    assert antes["path"].replace("\\", "/").endswith("filter_settings.json"), antes["path"]

    body = json.dumps({
        "low_hz": 7.5,
        "high_hz": 120.0,
        "order": 3,
        "enabled": True,
        "base_revision": antes["revision"],
    })
    code, raw, _ = ctx.post("/api/filter", body.encode("utf-8"),
                            {"Content-Type": "application/json"})
    assert code == 200, f"POST /api/filter -> {code}: {raw[:200]!r}"

    despues = ctx.json_get("/api/filter")
    assert despues["low_hz"] == 7.5 and despues["high_hz"] == 120.0, despues
    assert despues["order"] == 3 and despues["enabled"] is True, despues

    # Un POST parcial no pisa lo que no vino.
    ctx.post("/api/filter", json.dumps({
        "order": 5, "base_revision": despues["revision"]
    }).encode("utf-8"),
             {"Content-Type": "application/json"})
    final = ctx.json_get("/api/filter")
    assert final["order"] == 5, final
    assert final["low_hz"] == 7.5, f"un POST parcial pisó low_hz: {final}"


@check("filtros.preview_fase_cero", "la vista previa filtra sin correr el primer arribo",
      mode="sandbox")
def _filtros_preview(ctx: Ctx) -> None:
    # spike propio: dos fixtures byte-idénticas quedan marcadas duplicadas por
    # discover_dataset y la segunda se queda sin shot (ver _write_fixture).
    shot_id = _write_fixture(ctx, "smoke_filtro", spike=0.77, with_nan=False)
    crudo = ctx.json_get(f"/api/filter/preview?shot_id={shot_id}&max_points=4000"
                         "&low_hz=0&high_hz=0&order=4")
    for clave in ("time", "spectrum", "applied", "work_fs"):
        assert clave in crudo, f"falta {clave!r} en la vista previa"
    assert crudo["time"]["original"] and crudo["time"]["filtered"], crudo["time"]

    filt = ctx.json_get(f"/api/filter/preview?shot_id={shot_id}&max_points=4000"
                        "&low_hz=5&high_hz=200&order=4")
    orig = filt["time"]["original"]
    fil = filt["time"]["filtered"]

    def centroide(traza):
        """Centro de energía, en buckets.

        No se usa el argmax: el spike de la fixture es plano (5 muestras
        iguales) y cuál de ellas "gana" depende del desempate, no del filtro.
        El centroide es justo lo que un filtro de fase cero conserva.
        """
        peso = suma = 0.0
        for i, (lo, hi) in enumerate(zip(traza["min"], traza["max"])):
            if lo is None or hi is None:
                continue
            a = max(abs(lo), abs(hi))
            suma += a
            peso += a * i
        return (peso / suma) if suma > 0 else -1.0

    c_orig, c_filt = centroide(orig), centroide(fil)
    assert c_orig >= 0 and c_filt >= 0, "no se pudo medir el centro de energía"
    # Fase cero: el centro de energía no se corre. Un Butterworth causal del
    # mismo orden lo desplazaría decenas de muestras; 3 es margen de sobra para
    # el transitorio y el redondeo, y sigue detectando el caso malo.
    assert abs(c_orig - c_filt) <= 3.0, (
        f"el filtro corrió el centro de energía: {c_orig:.1f} -> {c_filt:.1f} buckets. "
        "Eso rompe el picking (tiene que ser fase cero, sosfiltfilt)")

    code, body, _ = ctx.get("/api/filter/preview?shot_id=0000000000000000")
    assert code == 404, f"shot_id inexistente -> {code}, se esperaba 404"


def _spectrum_peak(spectrum: dict, frequency: float, tolerance: float = 1.2) -> float:
    values = [
        float(value)
        for freq, value in zip(spectrum["f"], spectrum["max"])
        if value is not None and abs(float(freq) - frequency) <= tolerance
    ]
    if not values:
        raise AssertionError(f"el espectro no cubre {frequency}±{tolerance} Hz")
    return max(values)


@check(
    "filtros.supresor_armonico_adaptativo",
    "estima línea desplazada, sustrae varios armónicos por LS y deja Hilbert sólo visual",
    mode="sandbox",
)
def _filtros_linea_adaptativa(ctx: Ctx) -> None:
    for nominal, actual, suffix in ((50.0, 51.2, "50"), (60.0, 58.9, "60")):
        shot_id = _write_fixture(
            ctx,
            f"smoke_linea_{suffix}",
            spike=0.41 + nominal / 1000.0,
            with_nan=False,
            line_frequency=actual,
            line_harmonics=3,
        )
        query = (
            f"/api/filter/preview?shot_id={shot_id}&max_points=4000"
            f"&low_hz=0&high_hz=0&dc_enabled=true"
            f"&line_suppress_enabled=true&line_f0_hz={nominal}"
            "&line_harmonics=3&line_search_hz=2&include_envelope=true"
        )
        data = ctx.json_get(query)
        assert data["time"]["envelope"] is not None, "Hilbert no llegó a la vista previa"
        assert "envelope" not in data["applied"], (
            "Hilbert apareció en los ajustes científicos persistibles"
        )
        original = data["spectrum"]["original"]
        filtered = data["spectrum"]["filtered"]
        for harmonic in (1, 2, 3):
            frequency = actual * harmonic
            if frequency >= 500:
                continue
            before = _spectrum_peak(original, frequency)
            after = _spectrum_peak(filtered, frequency)
            assert after < before * 0.2, (
                f"{nominal:g} Hz nominal, armónico {harmonic}: "
                f"{before:.3g} -> {after:.3g}; no fue suprimido por el ajuste global"
            )
        scientific_before = _spectrum_peak(original, 17.0)
        scientific_after = _spectrum_peak(filtered, 17.0)
        assert scientific_after > scientific_before * 0.65, (
            f"la componente científica de 17 Hz fue dañada: "
            f"{scientific_before:.3g} -> {scientific_after:.3g}"
        )


@check(
    "estado.revision_optimista",
    "las mutaciones requieren revisión y una revisión obsoleta devuelve 409",
    mode="sandbox",
)
def _revision_optimista(ctx: Ctx) -> None:
    current = ctx.json_get("/api/filter")
    payload = {
        "base_revision": current["revision"],
        "notes": "smoke revision",
    }
    code, raw, _ = ctx.post(
        "/api/filter",
        json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"primera escritura -> {code}: {raw[:200]!r}"
    code, raw, _ = ctx.post(
        "/api/filter",
        json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 409, f"revisión obsoleta -> {code}, se esperaba 409: {raw[:200]!r}"

    groups = ctx.json_get("/api/groups")
    code, raw, _ = ctx.post(
        "/api/groups",
        json.dumps({
            "group_count": 2,
            "base_revision": groups["revision"],
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"grupos con revisión -> {code}: {raw[:200]!r}"
    code, _, _ = ctx.post(
        "/api/groups",
        json.dumps({
            "group_count": 1,
            "base_revision": groups["revision"],
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 409, f"grupos con revisión vieja -> {code}, se esperaba 409"


@check(
    "estado.campanas_revision",
    "la configuración compartida de campañas también rechaza escrituras obsoletas",
    mode="sandbox",
)
def _campanas_revision(ctx: Ctx) -> None:
    _write_fixture(ctx, "smoke_campaign_state", spike=0.601, with_nan=False)
    state = ctx.json_get("/api/campaigns")
    assert "revision" in state, state
    assert any(row["id"] == "." for row in state["campaigns"]), state
    payload = {
        "id": ".",
        "name": "Campaña smoke",
        "enabled": True,
        "base_revision": state["revision"],
    }
    code, raw, _ = ctx.post(
        "/api/campaigns",
        json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"campaña con revisión -> {code}: {raw[:200]!r}"
    saved = json.loads(raw)
    assert saved["revision"] != state["revision"], saved
    assert saved["campaign"]["name"] == "Campaña smoke", saved
    code, raw, _ = ctx.post(
        "/api/campaigns",
        json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 409, \
        f"campaña con revisión vieja -> {code}, se esperaba 409: {raw[:200]!r}"

    code, body, _ = ctx.get("/static/js/campaigns_panel.js")
    assert code == 200, "no se sirve campaigns_panel.js"
    source = body.decode("utf-8", "replace")
    assert "base_revision: revision" in source, \
        "el panel de campañas no envía su revisión"


@check("waterfall.vista_vs_persistente",
       "el recorte/ocultar es sólo vista; «Invertir traza» sí escribe geo_flip",
       mode="sandbox")
def _waterfall_vista_vs_persistente(ctx: Ctx) -> None:
    """La distinción que documenta `WaterfallPanel` (:3341) y que es fácil de
    romper: tildar trazas y recortar el tiempo NO pueden tocar las anotaciones,
    porque el export de Promedios sigue usando todo. «Invertir traza» sí, y por
    eso llega a promedios, MASW y export."""
    import hashlib

    shot_id = _write_fixture(ctx, "smoke_waterfall", spike=0.61, with_nan=False)
    # Sólo entran al promedio las capturas accepted Y reviewed: sin validarla el
    # waterfall queda vacío a propósito.
    vacio = ctx.json_get("/api/waterfall")
    assert vacio["traces"] == [], f"waterfall con capturas sin validar: {vacio['traces']}"

    campaign, ann_revision = _annotation_revision(ctx, shot_id)
    code, raw, _ = ctx.post("/api/pick", json.dumps(
        {"shot_id": shot_id, "campaign": campaign, "reviewed": True,
         "accepted": True, "base_revision": ann_revision}).encode("utf-8"),
        {"Content-Type": "application/json"})
    assert code == 200, f"POST /api/pick -> {code}: {raw[:200]!r}"

    wf = ctx.json_get("/api/waterfall")
    assert len(wf["traces"]) == 1, f"se esperaba 1 traza, hay {len(wf['traces'])}"
    traza = wf["traces"][0]
    assert abs(float(traza["distance_m"]) - 4.0) < 1e-6, traza["distance_m"]
    for clave in ("min", "max", "rising", "t0", "bucket_dt", "label", "peak"):
        assert clave in traza, f"la traza no trae {clave!r}"
    assert len(traza["rising"]) == len(traza["min"]), "rising y min de largo distinto"
    # La curva viene corrida a su distancia: oscila alrededor de 4 m, no de 0.
    centro = (min(v for v in traza["min"] if v is not None)
              + max(v for v in traza["max"] if v is not None)) / 2
    assert abs(centro - 4.0) < 1.0, (
        f"la traza no está centrada en su distancia (centro={centro:.3f}, esperado ~4.0)")

    def _huella() -> str:
        # El archivo de anotaciones lo resuelve el servidor; se lo pregunta a él.
        ruta = Path(ctx.json_get("/api/filter")["path"]).with_name(
            "field_review_annotations.json")
        return hashlib.md5(ruta.read_bytes()).hexdigest() if ruta.is_file() else ""

    antes = _huella()
    assert antes, "no se encontró el archivo de anotaciones del sandbox"

    # Sólo vista: ocultar la traza la saca del dibujo y no toca las anotaciones.
    code, raw, _ = ctx.post("/api/waterfall/view", json.dumps(
        {"hidden_distances": [4.0], "base_revision": wf["revision"]}).encode("utf-8"),
        {"Content-Type": "application/json"})
    assert code == 200, f"POST /api/waterfall/view -> {code}: {raw[:200]!r}"
    oculto = json.loads(raw)
    assert oculto["traces"] == [], "ocultar la traza no la sacó del waterfall"
    assert oculto["n_averages"] == 1, (
        "ocultar una traza cambió la cantidad de promedios: es sólo vista, "
        "el export sigue usando todas")
    assert _huella() == antes, "un cambio de VISTA escribió las anotaciones"

    code, raw, _ = ctx.post("/api/waterfall/view", json.dumps({
        "hidden_distances": [], "base_revision": oculto["revision"]
    }).encode("utf-8"), {"Content-Type": "application/json"})
    assert code == 200, f"restaurar vista -> {code}: {raw[:200]!r}"
    restaurado = json.loads(raw)

    # Persistente: invertir la traza sí escribe geo_flip.
    code, raw, _ = ctx.post("/api/waterfall/flip", json.dumps(
        {"distance_m": 4.0,
         "base_revision": restaurado["annotation_revision"]}).encode("utf-8"),
        {"Content-Type": "application/json"})
    assert code == 200, f"POST /api/waterfall/flip -> {code}: {raw[:200]!r}"
    volteado = json.loads(raw)
    assert volteado["flip"]["changed"] >= 1, volteado["flip"]
    assert _huella() != antes, "«Invertir traza» no escribió las anotaciones"

    señal = ctx.json_get(f"/api/signal?shot_id={shot_id}")
    assert señal["geo_flip"] is True, (
        "el flip del waterfall no llegó al visor: tiene que ser el mismo geo_flip")

    # La exportación científica usa la cola separada y entrega los tres
    # formatos prometidos por la interfaz más metadatos reproducibles.
    code, raw, _ = ctx.post(
        "/api/exports",
        json.dumps({"kind": "waterfall", "group_id": 1}).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"encolar export Waterfall -> {code}: {raw[:200]!r}"
    export_job = json.loads(raw)
    deadline = time.time() + 30
    while time.time() < deadline:
        export_job = ctx.json_get(f"/api/jobs/{export_job['id']}")
        if export_job["state"] in {"listo", "error", "cancelado"}:
            break
        time.sleep(0.2)
    assert export_job["state"] == "listo", export_job
    artifacts = export_job.get("result", {}).get("artifacts") or []
    extensions = {Path(item["name"]).suffix for item in artifacts}
    assert {".csv", ".png", ".pdf", ".json"} <= extensions, artifacts
    for item in artifacts:
        code, content, _ = ctx.get(f"/api/artifacts/{item['id']}")
        assert code == 200 and content, (
            f"artefacto Waterfall {item['name']} -> {code}, {len(content)} B"
        )


@check(
    "masw.geometria_multigrupo",
    "la combinación ponderada usa mayor dx y menor apertura, igual que PyQt",
    mode="sandbox",
)
def _masw_geometria_multigrupo(ctx: Ctx) -> None:
    assignments = {}
    fixtures = (
        ("smoke_geom_g1_d2", 2.0, 1, 0.511),
        ("smoke_geom_g1_d4", 4.0, 1, 0.523),
        ("smoke_geom_g1_d6", 6.0, 1, 0.537),
        ("smoke_geom_g2_d2", 2.0, 2, 0.551),
        ("smoke_geom_g2_d5", 5.0, 2, 0.563),
        ("smoke_geom_g2_d8", 8.0, 2, 0.577),
    )
    for folder, distance, group_id, spike in fixtures:
        shot_id = _write_fixture(
            ctx,
            folder,
            spike=spike,
            with_nan=False,
            distance_m=distance,
        )
        campaign, ann_revision = _annotation_revision(ctx, shot_id)
        code, raw, _ = ctx.post(
            "/api/pick",
            json.dumps({
                "shot_id": shot_id,
                "campaign": campaign,
                "reviewed": True,
                "accepted": True,
                "base_revision": ann_revision,
            }).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        assert code == 200, f"validar {folder} -> {code}: {raw[:200]!r}"
        assignments[folder] = group_id

    groups = ctx.json_get("/api/groups")
    code, raw, _ = ctx.post(
        "/api/groups",
        json.dumps({
            "group_count": 2,
            "assign": assignments,
            "base_revision": groups["revision"],
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"guardar geometría de grupos -> {code}: {raw[:200]!r}"

    state = ctx.json_get("/api/masw/state")
    code, raw, _ = ctx.post(
        "/api/masw/dispersion",
        json.dumps({
            "base_revision": state["revision"],
            "group_weights": {"1": 1.0, "2": 0.75},
            "params": {
                "c_min": 50.0,
                "c_max": 500.0,
                "c_step": 10.0,
                "f_min": 2.0,
                "f_max": 50.0,
            },
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
        timeout_s=45,
    )
    assert code == 200, f"dispersión multigrupo -> {code}: {raw[:300]!r}"
    dispersion = json.loads(raw)
    assert abs(float(dispersion["geophone_spacing_m"]) - 3.0) < 1e-6, dispersion
    assert abs(float(dispersion["array_length_m"]) - 4.0) < 1e-6, dispersion
    raw_groups = dispersion["state"]["masw"]["raw_groups"]
    assert abs(float(raw_groups["1"]["spacing"]) - 2.0) < 1e-6, raw_groups
    assert abs(float(raw_groups["1"]["length"]) - 4.0) < 1e-6, raw_groups
    assert abs(float(raw_groups["2"]["spacing"]) - 3.0) < 1e-6, raw_groups
    assert abs(float(raw_groups["2"]["length"]) - 6.0) < 1e-6, raw_groups


@check(
    "masw.sintetico_semiespacio_multimodo",
    "semiespacio homogéneo es no dispersivo y el phase-shift separa dos modos",
    mode="sandbox",
)
def _masw_sintetico_semiespacio_multimodo(ctx: Ctx) -> None:
    import numpy as np

    # Al ejecutar ``python server/smoke_test.py`` Python pone ``server/`` —no
    # su padre— en sys.path. Esta prueba numérica importa únicamente la capa
    # científica compartida; el resto del gate continúa hablando sólo HTTP.
    python_root = str(PYTHON_ROOT)
    if python_root not in sys.path:
        sys.path.insert(0, python_root)
    from geophone_scope.masw_demo import synthetic_gather
    from geophone_scope.masw_dispersion import phase_shift_dispersion_image
    from geophone_scope.masw_inversion import theoretical_dispersion_curve

    del ctx  # prueba numérica pura de las mismas funciones compartidas con PyQt

    vs = 300.0
    vp = vs * np.sqrt(3.0)  # Poisson 0.25
    wavelengths = np.asarray([2.0, 4.0, 8.0, 16.0, 32.0])
    c_test = np.arange(150.0, 330.0, 0.5)
    homogeneous = theoretical_dispersion_curve(
        c_test,
        wavelengths,
        np.asarray([vp]),
        np.asarray([vs]),
        np.asarray([1800.0]),
        np.asarray([]),
    )
    assert np.all(np.isfinite(homogeneous)), homogeneous
    assert float(np.ptp(homogeneous)) <= 0.5, (
        f"el semiespacio homogéneo resultó dispersivo: {homogeneous}"
    )
    rayleigh_ratio = float(np.mean(homogeneous) / vs)
    assert 0.90 <= rayleigh_ratio <= 0.94, rayleigh_ratio

    t, offsets, gather = synthetic_gather()
    fs = 1.0 / float(t[1] - t[0])
    freqs, velocities, image = phase_shift_dispersion_image(
        gather.T,
        np.asarray(offsets),
        fs,
        100.0,
        450.0,
        2.0,
        f_min=8.0,
        f_max=60.0,
    )
    recovered = []
    for target_frequency in (20.0, 34.0):
        row = int(np.argmin(np.abs(freqs - target_frequency)))
        recovered.append(float(velocities[int(np.argmax(image[row]))]))
    assert abs(recovered[0] - 180.0) <= 6.0, recovered
    assert abs(recovered[1] - 340.0) <= 8.0, recovered


@check(
    "enfase.clave_compartida",
    "Enfase web usa la misma clave ::grupoN que PyQt y limpia la clave web vieja",
    mode="sandbox",
)
def _enfase_clave_compartida(ctx: Ctx) -> None:
    folder = "smoke_enfase_grupo"
    shot_id = _write_fixture(ctx, folder, spike=0.682, with_nan=False)
    campaign, ann_revision = _annotation_revision(ctx, shot_id)
    code, raw, _ = ctx.post(
        "/api/pick",
        json.dumps({
            "shot_id": shot_id,
            "campaign": campaign,
            "reviewed": True,
            "accepted": True,
            "base_revision": ann_revision,
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"validar fixture Enfase -> {code}: {raw[:200]!r}"

    groups = ctx.json_get("/api/groups")
    code, raw, _ = ctx.post(
        "/api/groups",
        json.dumps({
            "group_count": 2,
            "assign": {folder: 2},
            "base_revision": groups["revision"],
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"asignar Grupo 2 -> {code}: {raw[:200]!r}"

    alignment = ctx.json_get("/api/alignment?group_id=2")
    row = next((item for item in alignment["folders"] if item["folder"] == folder), None)
    assert row, alignment
    label = alignment["label"]
    code, raw, _ = ctx.post(
        "/api/alignment",
        json.dumps({
            "action": "reject",
            "label": label,
            "folder": folder,
            "group_id": 2,
            "rejected": True,
            "base_revision": alignment["revision"],
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"rechazar Enfase -> {code}: {raw[:200]!r}"
    rejected = json.loads(raw)
    assert next(item for item in rejected["folders"] if item["folder"] == folder)[
        "rejected"
    ] is True
    disabled_path = Path(rejected["disabled_path"])
    disabled_doc = json.loads(disabled_path.read_text(encoding="utf-8"))
    disabled_rows = disabled_doc.get("disabled") or []
    assert {"label": f"{label}::grupo2", "folder": folder} in disabled_rows, disabled_doc
    assert {"label": f"{label}#g2", "folder": folder} not in disabled_rows, disabled_doc

    # Simula un archivo escrito por el primer port web y comprueba que aceptar
    # limpia tanto esa clave como la compartida.
    disabled_rows.append({"label": f"{label}#g2", "folder": folder})
    disabled_path.write_text(json.dumps(disabled_doc), encoding="utf-8")
    refreshed = ctx.json_get(f"/api/alignment?group_id=2&label={label}")
    code, raw, _ = ctx.post(
        "/api/alignment",
        json.dumps({
            "action": "reject",
            "label": label,
            "folder": folder,
            "group_id": 2,
            "rejected": False,
            "base_revision": refreshed["revision"],
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"aceptar Enfase -> {code}: {raw[:200]!r}"
    cleaned = json.loads(disabled_path.read_text(encoding="utf-8"))
    cleaned_rows = cleaned.get("disabled") or []
    assert {"label": f"{label}::grupo2", "folder": folder} not in cleaned_rows, cleaned
    assert {"label": f"{label}#g2", "folder": folder} not in cleaned_rows, cleaned


@check(
    "enfase.auto_y_restauracion",
    "Autoenfase estima el signo correcto y puede restaurarse",
    mode="sandbox",
)
def _enfase_auto_y_restauracion(ctx: Ctx) -> None:
    reference_folder = "smoke_autoenfase_ref"
    delayed_folder = "smoke_autoenfase_delayed"
    reference_id = _write_fixture(
        ctx,
        reference_folder,
        spike=0.91,
        with_nan=False,
        distance_m=7.0,
        geo_delay_samples=50,
    )
    delayed_id = _write_fixture(
        ctx,
        delayed_folder,
        spike=0.71,
        with_nan=False,
        distance_m=7.0,
        geo_delay_samples=70,
    )
    for shot_id in (reference_id, delayed_id):
        campaign, ann_revision = _annotation_revision(ctx, shot_id)
        code, raw, _ = ctx.post(
            "/api/pick",
            json.dumps({
                "shot_id": shot_id,
                "campaign": campaign,
                "reviewed": True,
                "accepted": True,
                "trigger_s": 1.0,
                "base_revision": ann_revision,
            }).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        assert code == 200, f"validar autoenfase -> {code}: {raw[:200]!r}"

    groups = ctx.json_get("/api/groups")
    code, raw, _ = ctx.post(
        "/api/groups",
        json.dumps({
            "group_count": 3,
            "assign": {
                reference_folder: 3,
                delayed_folder: 3,
            },
            "base_revision": groups["revision"],
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"aislar autoenfase en Grupo 3 -> {code}: {raw[:200]!r}"

    alignment = ctx.json_get("/api/alignment?group_id=3")
    assert {row["folder"] for row in alignment["folders"]} == {
        reference_folder,
        delayed_folder,
    }, alignment
    label = str(alignment["label"])
    code, raw, _ = ctx.post(
        "/api/alignment",
        json.dumps({
            "action": "auto_align",
            "label": label,
            "group_id": 3,
            "max_shift_ms": 50,
            "min_score": 0.6,
            "ambiguity_ratio": 0.9,
            "base_revision": alignment["revision"],
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"autoenfase -> {code}: {raw[:300]!r}"
    aligned = json.loads(raw)
    diagnostics = aligned.get("auto_align") or {}
    assert diagnostics.get("reference") == reference_folder, diagnostics
    target = next(
        (row for row in diagnostics.get("applied", [])
         if row["folder"] == delayed_folder),
        None,
    )
    assert target, diagnostics
    # La señal target llega 20 ms tarde. Con t_mostrado=t_original+offset
    # debe correrse a la izquierda: el signo correcto es negativo.
    assert abs(float(target["offset_ms"]) + 20.0) <= 1.1, target
    delayed = next(
        row for row in aligned["folders"] if row["folder"] == delayed_folder
    )
    assert abs(float(delayed["offset_ms"]) + 20.0) <= 1.1, delayed

    code, raw, _ = ctx.post(
        "/api/alignment",
        json.dumps({
            "action": "reset_label",
            "label": label,
            "group_id": 3,
            "base_revision": aligned["revision"],
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"restaurar autoenfase -> {code}: {raw[:300]!r}"
    restored = json.loads(raw)
    assert all(not row["confirmed"] and abs(float(row["offset_ms"])) < 1e-9
               for row in restored["folders"]), restored


@check("tabs.tema_toggle", "el botón de tema y las reglas [data-theme] de los dos temas existen")
def _tabs_tema_toggle(ctx: Ctx) -> None:
    text = _index_text(ctx)
    assert 'id="btn-theme"' in text, 'falta id="btn-theme" en /'

    code, body, _ = ctx.get("/static/js/theme.js")
    assert code == 200, f"/static/js/theme.js -> {code}"
    theme_js = body.decode("utf-8", "replace")
    for token in ("localStorage", "data-theme", "prefers-color-scheme"):
        assert token in theme_js, f"theme.js no contiene {token!r}"

    code, body, _ = ctx.get("/static/css/app.css")
    assert code == 200, f"/static/css/app.css -> {code}"
    css = body.decode("utf-8", "replace")
    assert '[data-theme="dark"]' in css, 'app.css no define [data-theme="dark"]'
    assert '[data-theme="light"]' in css, 'app.css no define [data-theme="light"]'


@check("tabs.tema_sin_flash", "theme-boot.js corre en <head> como script clásico, misma clave que theme.js")
def _tabs_tema_sin_flash(ctx: Ctx) -> None:
    text = _index_text(ctx)
    head_end = text.find("</head>")
    assert head_end >= 0, "/ no tiene </head>"
    boot_pos = text.find("/static/js/theme-boot.js")
    assert boot_pos >= 0, "/ no referencia /static/js/theme-boot.js"
    assert boot_pos < head_end, "theme-boot.js debe estar antes de </head>"

    tag_start = text.rfind("<script", 0, boot_pos)
    tag_end = text.find(">", boot_pos)
    tag = text[tag_start:tag_end + 1]
    assert 'type="module"' not in tag, "theme-boot.js no puede ser type=module (corre diferido)"
    assert "defer" not in tag, "theme-boot.js no puede ser defer (corre diferido)"

    code, body, _ = ctx.get("/static/js/theme-boot.js")
    assert code == 200, f"/static/js/theme-boot.js -> {code}"
    boot_js = body.decode("utf-8", "replace")

    code, body, _ = ctx.get("/static/js/theme.js")
    assert code == 200, f"/static/js/theme.js -> {code}"
    theme_js = body.decode("utf-8", "replace")

    m = re.search(r"localStorage\.getItem\('([^']+)'\)", boot_js)
    assert m, "theme-boot.js no lee localStorage.getItem('...')"
    key_boot = m.group(1)
    assert key_boot in theme_js, (
        f"la clave {key_boot!r} de theme-boot.js no aparece igual en theme.js")


# ── Checks capturas/señal (§3.1: GET /api/signal + dibujo) ────────────────────
def _first_shot(ctx: Ctx) -> tuple[str, dict]:
    """Primer (shot_id, captura) del dataset real que se pueda dibujar.

    Filtra por pick.shot_id y NO por pickable: son distintos (spec §5.1 —
    catalog.py deduce el rol sólo de node["role"], frd._node_role mira además
    type/hw_type/name/data_dir/raw_file, y 194 shots != 186 pickable).
    """
    data = ctx.json_get("/api/dataset")
    for folder in data.get("folders", []):
        for cap in folder.get("captures", []):
            pick = cap.get("pick") or {}
            if pick.get("shot_id"):
                return pick["shot_id"], cap
    raise AssertionError("ninguna captura de /api/dataset trae pick.shot_id: "
                         "sin eso la web no puede dibujar nada")


def _write_fixture(
    ctx: Ctx,
    folder: str,
    *,
    spike: float,
    with_nan: bool,
    line_frequency: float | None = None,
    line_harmonics: int = 3,
    distance_m: float = 4.0,
    geo_delay_samples: int = 0,
) -> str:
    """Escribe una captura sintética en el raw_root del SANDBOX y devuelve su shot_id.

    Cinturón de seguridad: nunca contra el dataset real (regla 2 del prompt).
    Se escribe directo a disco (no por /ingest) para no correr auto_pick_shot +
    save_annotations sobre el archivo de picks real (§8.1 del spec: el sandbox
    y el dataset real comparten el mismo default_annotations_path por nombre
    de carpeta). El shot_id se obtiene por HTTP (/api/dataset), no reimportando
    field_review_data acá: el gate sigue hablando sólo HTTP.
    """
    assert ctx.sandbox and "smoke_sandbox" in str(ctx.raw_root), \
        f"_write_fixture sólo va en sandbox; raw_root={ctx.raw_root}"
    import numpy as np

    cap_dir = ctx.raw_root / folder / "captures" / "001_smoke"
    hammer_dir = cap_dir / "hammer_s1"
    geo_dir = cap_dir / "geo1_s2"
    hammer_dir.mkdir(parents=True, exist_ok=True)
    geo_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "order": 1, "fs": 1000,
        "nodes": [
            {"index": 1, "pcb_id": "S1", "role": "hammer", "type": "Hammer", "fs": 1000,
             "data_dir": "captures/001_smoke/hammer_s1",
             "raw_file": "captures/001_smoke/hammer_s1/raw_f32le.bin"},
            {"index": 2, "pcb_id": "S2", "role": "geo", "type": "Geo", "fs": 1000,
             "position_m": float(distance_m),
             "data_dir": "captures/001_smoke/geo1_s2",
             "raw_file": "captures/001_smoke/geo1_s2/raw_f32le.bin"},
        ],
    }
    (cap_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    # Pico +spike en la muestra 1000 (5 muestras, salto abrupto sobre línea de
    # base plana) para que detect_hammer_trigger lo tome. Cada fixture con
    # contenido distinto (spike distinto): dos carpetas byte-idénticas quedan
    # marcadas duplicadas por discover_dataset y la segunda pierde su shot.
    n = 4000
    hammer = np.zeros(n, dtype=np.float32)
    hammer[998:1003] = np.float32(spike)
    geo = np.zeros(n, dtype=np.float32)
    geo_start = int(np.clip(998 + int(geo_delay_samples), 0, n - 5))
    geo[geo_start:geo_start + 5] = np.float32(spike)
    if line_frequency is not None:
        t = np.arange(n, dtype=np.float64) / 1000.0
        scientific = 0.15 * np.sin(2.0 * np.pi * 17.0 * t)
        hum = sum(
            (0.8 / harmonic)
            * np.sin(2.0 * np.pi * harmonic * float(line_frequency) * t
                     + 0.17 * harmonic)
            for harmonic in range(1, max(1, int(line_harmonics)) + 1)
            if harmonic * float(line_frequency) < 500.0
        )
        geo = np.asarray(2.5 + scientific + hum, dtype=np.float32)
        geo[geo_start:geo_start + 5] += np.float32(spike)
    if with_nan:
        geo[3000:3003] = np.nan
    hammer.tofile(hammer_dir / "raw_f32le.bin")
    geo.tofile(geo_dir / "raw_f32le.bin")

    data = ctx.json_get("/api/dataset")
    for f in data.get("folders", []):
        if f["folder"] != folder:
            continue
        for cap in f.get("captures", []):
            if cap["capture"] == "001_smoke":
                shot_id = (cap.get("pick") or {}).get("shot_id")
                assert shot_id, f"fixture {folder}/001_smoke sin shot_id en /api/dataset: {cap}"
                return shot_id
    raise AssertionError(f"fixture {folder}/001_smoke no aparece en /api/dataset tras escribirla")


def _annotation_revision(ctx: Ctx, shot_id: str) -> tuple[str, str]:
    """Devuelve (campaña, revisión) para un disparo visto en /api/captures."""
    data = ctx.json_get("/api/captures")
    revisions = data.get("annotation_revisions") or {}
    for row in data.get("rows", []):
        if row.get("shot_id") == shot_id:
            campaign = str(row.get("campaign", ""))
            return campaign, str(revisions.get(campaign, "missing"))
    raise AssertionError(f"{shot_id} no aparece en /api/captures")


@check("capturas.signal.contrato", "GET /api/signal trae todas las claves del contrato (spec §4.2)")
def _signal_contrato(ctx: Ctx) -> None:
    shot_id, _cap = _first_shot(ctx)
    data = ctx.json_get(f"/api/signal?shot_id={shot_id}&max_points=500")
    for key in ("shot_id", "folder", "capture", "fs", "kind", "max_points",
                "trigger_s", "trigger_source", "geo_flip", "channels"):
        assert key in data, f"falta la clave {key!r} en /api/signal: {sorted(data)}"
    channels = data["channels"]
    assert "hammer" in channels and "geo" in channels, f"channels={sorted(channels)}"
    assert data["fs"] > 0, f"fs={data['fs']}"
    for role, ch in channels.items():
        for key in ("role", "pcb_id", "file", "used_filtered", "invert_applied",
                    "flip_applied", "samples", "duration_s", "stride", "buckets",
                    "bucket_dt", "decimated", "y_min", "y_max", "min", "max"):
            assert key in ch, f"falta la clave {key!r} en channels.{role}: {sorted(ch)}"
        assert "t" not in ch, f"channels.{role} manda 't': el eje se deriva, no se manda (§4.2.3)"
        assert ch["samples"] > 0, f"channels.{role}.samples={ch['samples']}"
        assert len(ch["min"]) == len(ch["max"]) == ch["buckets"], (
            f"channels.{role}: len(min)={len(ch['min'])} len(max)={len(ch['max'])} "
            f"buckets={ch['buckets']}")
        assert ch["buckets"] <= 500, f"channels.{role}.buckets={ch['buckets']} > max_points=500"
        assert ch["decimated"] is True, (
            f"channels.{role}.decimated={ch['decimated']}, con max_points=500 y "
            f"samples={ch['samples']} tiene que decimar")
        assert ch["bucket_dt"] > 0, f"channels.{role}.bucket_dt={ch['bucket_dt']}"
        assert ":" not in ch["file"] and "\\" not in ch["file"], (
            f"channels.{role}.file no es relativo con '/': {ch['file']!r}")


@check("capturas.signal.decimado_conserva_picos", "min/max conserva los extremos exactos de la señal cruda")
def _signal_decimado(ctx: Ctx) -> None:
    shot_id, _cap = _first_shot(ctx)
    dec = ctx.json_get(f"/api/signal?shot_id={shot_id}&max_points=500")
    raw = ctx.json_get(f"/api/signal?shot_id={shot_id}&max_points=100000")
    for role in ("hammer", "geo"):
        chd = dec["channels"][role]
        chr_ = raw["channels"][role]
        assert chr_["stride"] == 1, (
            f"{role}: stride={chr_['stride']} con max_points=100000, se esperaba 1 (crudo)")
        assert chr_["decimated"] is False, f"{role}: decimated={chr_['decimated']} con stride==1"
        assert chr_["min"] == chr_["max"], f"{role}: con stride==1 min y max tienen que ser iguales"
        assert chd["buckets"] <= 500 < chr_["samples"], (
            f"{role}: buckets={chd['buckets']} samples={chr_['samples']}")

        raw_maxs = [v for v in chr_["max"] if v is not None]
        raw_mins = [v for v in chr_["min"] if v is not None]
        dec_maxs = [v for v in chd["max"] if v is not None]
        dec_mins = [v for v in chd["min"] if v is not None]
        assert raw_maxs and raw_mins and dec_maxs and dec_mins, f"{role}: buckets vacíos"

        max_dec, max_raw = max(dec_maxs), max(raw_maxs)
        min_dec, min_raw = min(dec_mins), min(raw_mins)
        assert math.isclose(max_dec, max_raw, rel_tol=1e-5), (
            f"{role}: max(max_decimado)={max_dec} != max(max_crudo)={max_raw} "
            f"(un decimado que promedia o saltea se come el pico)")
        assert math.isclose(min_dec, min_raw, rel_tol=1e-5), (
            f"{role}: min(min_decimado)={min_dec} != min(min_crudo)={min_raw}")

    body_size = len(json.dumps(dec).encode("utf-8"))
    assert body_size < 60_000, f"cuerpo del decimado (max_points=500) pesa {body_size} B >= 60 kB"


@check("capturas.signal.max_points_clamp", "max_points se clampea a [100,20000], no se rechaza ni explota")
def _signal_clamp(ctx: Ctx) -> None:
    shot_id, _cap = _first_shot(ctx)

    data = ctx.json_get(f"/api/signal?shot_id={shot_id}&max_points=1")
    buckets = data["channels"]["hammer"]["buckets"]
    assert buckets >= 100, f"max_points=1: buckets={buckets} < 100 (mínimo del clamp)"

    data = ctx.json_get(f"/api/signal?shot_id={shot_id}&max_points=1000000000")
    buckets = data["channels"]["hammer"]["buckets"]
    assert buckets <= 20000, f"max_points=1e9: buckets={buckets} > 20000 (máximo del clamp)"

    code, body, _ = ctx.get(f"/api/signal?shot_id={shot_id}&max_points=abc")
    assert code == 422, f"max_points=abc -> {code} (se esperaba 422, no 400 ni 500): {body[:200]!r}"


@check("capturas.signal.sin_nan_en_json", "el JSON no lleva literales NaN/Infinity (no son JSON válido)")
def _signal_sin_nan(ctx: Ctx) -> None:
    shot_id, _cap = _first_shot(ctx)
    code, body, _ = ctx.get(f"/api/signal?shot_id={shot_id}&max_points=2000")
    assert code == 200, f"/api/signal -> {code}: {body[:200]!r}"
    assert b"NaN" not in body, "el cuerpo crudo contiene el literal NaN (JSON.parse falla en el navegador)"
    assert b"Infinity" not in body, "el cuerpo crudo contiene el literal Infinity"

    def _no_constants(name):
        raise AssertionError(f"json.loads encontró la constante no-JSON {name!r} en el cuerpo")

    json.loads(body.decode("utf-8", "replace"), parse_constant=_no_constants)


@check("capturas.signal.kind_filt", "kind distingue raw_f32le.bin de filt_f32le.bin; kind inválido -> 400")
def _signal_kind(ctx: Ctx) -> None:
    shot_id, _cap = _first_shot(ctx)

    data = ctx.json_get(f"/api/signal?shot_id={shot_id}&kind=filt&max_points=500")
    for role, ch in data["channels"].items():
        assert ch["used_filtered"] is True, f"{role}: used_filtered={ch['used_filtered']} con kind=filt"
        assert ch["file"].endswith("filt_f32le.bin"), (
            f"{role}: file={ch['file']!r} no termina en filt_f32le.bin")

    data = ctx.json_get(f"/api/signal?shot_id={shot_id}&kind=raw&max_points=500")
    for role, ch in data["channels"].items():
        assert ch["used_filtered"] is False, f"{role}: used_filtered={ch['used_filtered']} con kind=raw"
        assert ch["file"].endswith("raw_f32le.bin"), (
            f"{role}: file={ch['file']!r} no termina en raw_f32le.bin")

    code, body, _ = ctx.get(f"/api/signal?shot_id={shot_id}&kind=xxx&max_points=500")
    assert code == 400, f"kind=xxx -> {code} (se esperaba 400): {body[:200]!r}"


@check("capturas.signal.shot_desconocido", "shot_id inexistente -> 404; shot_id vacío -> 400/422, nunca 200/500")
def _signal_desconocido(ctx: Ctx) -> None:
    code, body, _ = ctx.get("/api/signal?shot_id=0000000000000000&max_points=500")
    assert code == 404, f"shot_id inexistente -> {code} (se esperaba 404): {body[:200]!r}"

    code, body, _ = ctx.get("/api/signal?shot_id=&max_points=500")
    assert code in (400, 422), f"shot_id vacío -> {code} (se esperaba 400 o 422): {body[:200]!r}"


@check("capturas.signal.trigger_marcado", "trigger_s sale de la anotación o de auto_pick_shot, nunca inventado")
def _signal_trigger(ctx: Ctx) -> None:
    shot_id, cap = _first_shot(ctx)
    data = ctx.json_get(f"/api/signal?shot_id={shot_id}&max_points=500")
    trigger_s = data["trigger_s"]
    duration_s = data["channels"]["hammer"]["duration_s"]
    assert trigger_s is not None and math.isfinite(trigger_s), f"trigger_s={trigger_s} no finito"
    assert 0 <= trigger_s <= duration_s, f"trigger_s={trigger_s} fuera de [0, {duration_s}]"
    assert data["trigger_source"] in ("annotation", "auto"), f"trigger_source={data['trigger_source']!r}"

    pick_trigger = (cap.get("pick") or {}).get("trigger_s")
    if pick_trigger is not None:
        assert data["trigger_source"] == "annotation", (
            f"/api/dataset trae pick.trigger_s={pick_trigger} pero /api/signal dice "
            f"trigger_source={data['trigger_source']!r}")
        assert abs(data["trigger_s"] - pick_trigger) < 1e-9, (
            f"trigger_s difiere: /api/signal={data['trigger_s']} /api/dataset={pick_trigger}")
    else:
        assert data["trigger_source"] == "auto", (
            f"/api/dataset no trae pick.trigger_s pero /api/signal dice "
            f"trigger_source={data['trigger_source']!r}")


@check("capturas.signal.no_bloquea", "GET /api/signal no bloquea el event loop (handler síncrono, spec §4.6)")
def _signal_no_bloquea(ctx: Ctx) -> None:
    from concurrent.futures import ThreadPoolExecutor

    shot_id, _cap = _first_shot(ctx)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = [pool.submit(ctx.get, f"/api/signal?shot_id={shot_id}&max_points=2000&_r={i}")
               for i in range(3)]

        started = time.monotonic()
        code, _, _ = ctx.get("/health")
        elapsed_health = time.monotonic() - started
        assert code == 200, f"/health -> {code}"
        assert elapsed_health < 2.0, (
            f"/health tardó {elapsed_health:.1f}s mientras /api/signal corría en paralelo: "
            f"¿el handler quedó async def con trabajo bloqueante adentro? (spec §4.6)")

        started = time.monotonic()
        ctx.json_get("/api/jobs")
        elapsed_jobs = time.monotonic() - started
        assert elapsed_jobs < 2.0, f"/api/jobs tardó {elapsed_jobs:.1f}s"

        for i, fut in enumerate(futs):
            code, body, _ = fut.result(timeout=30)
            assert code == 200, f"/api/signal #{i} -> {code}: {body[:200]!r}"


@check("capturas.signal.ui_usa_endpoint", "el JS del visor usa /api/signal y está enganchado a la tabla")
def _signal_ui(ctx: Ctx) -> None:
    code, body, _ = ctx.get("/static/js/tabs/capturas_signal.js")
    assert code == 200, f"/static/js/tabs/capturas_signal.js -> {code}"
    js = body.decode("utf-8", "replace")
    for token in ("/api/signal", "max_points", "geo_flip"):
        assert token in js, f"capturas_signal.js no contiene {token!r}"

    code, body, _ = ctx.get("/static/js/plot.js")
    assert code == 200, f"/static/js/plot.js -> {code}"
    assert "export function drawMinMax" in body.decode("utf-8", "replace"), (
        "plot.js no exporta drawMinMax")

    code, body, _ = ctx.get("/static/js/tabs/capturas.js")
    assert code == 200, f"/static/js/tabs/capturas.js -> {code}"
    caps_js = body.decode("utf-8", "replace")
    assert "capturas_signal.js" in caps_js, "capturas.js no importa capturas_signal.js"
    assert "shot_id" in caps_js, "capturas.js no usa shot_id para habilitar el botón ver"


@check("capturas.signal.polaridad_fija", "convención fija: hammer sale invertido, geo no (fixture sintética)",
      mode="sandbox")
def _signal_polaridad(ctx: Ctx) -> None:
    shot_id = _write_fixture(ctx, "smoke_polaridad", spike=1.0, with_nan=False)
    data = ctx.json_get(f"/api/signal?shot_id={shot_id}&max_points=100000")
    hammer = data["channels"]["hammer"]
    geo = data["channels"]["geo"]

    h_min = min(v for v in hammer["min"] if v is not None)
    h_max = max(v for v in hammer["max"] if v is not None)
    g_min = min(v for v in geo["min"] if v is not None)
    g_max = max(v for v in geo["max"] if v is not None)
    assert h_min <= -0.9, f"hammer: min={h_min}, se esperaba <= -0.9 (invertido)"
    assert h_max <= 0.1, f"hammer: max={h_max}, se esperaba <= 0.1 (invertido, no queda el +spike)"
    assert g_max >= 0.9, f"geo: max={g_max}, se esperaba >= 0.9 (no invertido)"
    assert g_min >= -0.1, f"geo: min={g_min}, se esperaba >= -0.1 (no invertido)"

    assert hammer["invert_applied"] is True, f"hammer.invert_applied={hammer['invert_applied']}"
    assert geo["invert_applied"] is False, f"geo.invert_applied={geo['invert_applied']}"
    assert hammer["flip_applied"] is False, f"hammer.flip_applied={hammer['flip_applied']}"
    assert geo["flip_applied"] is False, f"geo.flip_applied={geo['flip_applied']}"

    # La fixture tiene el golpe en la muestra 998-1002 de 4000 a fs=1000, así
    # que auto_pick_shot (onset: una muestra antes del flanco) tiene que dar
    # trigger_s ~= 0.997. Un trigger fuera de [0.9, 1.1] está inventado (p.ej.
    # trigger_s=0.0 fijo), no sale de auto_pick_shot: revisión del intento 1.
    assert 0.9 <= data["trigger_s"] <= 1.1, (
        f"trigger_s={data['trigger_s']}: la fixture tiene el golpe en la muestra "
        f"1000 de 4000 a fs=1000, o sea ~0.997 s. Un trigger fuera de [0.9, 1.1] "
        f"está inventado, no sale de auto_pick_shot")
    assert data["trigger_source"] == "auto", (
        f"trigger_source={data['trigger_source']!r}, se esperaba 'auto' (la fixture "
        f"no tiene anotación). NOTA: la rama trigger_source=='annotation' no tiene "
        f"cobertura en este ítem porque hoy reviewed_count==0 y este ítem no puede "
        f"escribir anotaciones (spec §8.1) — queda declarado, no tapado.")


@check("capturas.signal.nan_a_null", "NaN de la señal se convierte a null, nunca a 0.0 ni pasa crudo",
      mode="sandbox")
def _signal_nan(ctx: Ctx) -> None:
    shot_id = _write_fixture(ctx, "smoke_nan", spike=0.8, with_nan=True)
    code, body, _ = ctx.get(f"/api/signal?shot_id={shot_id}&max_points=100000")
    assert code == 200, f"/api/signal -> {code}: {body[:200]!r}"
    assert b"NaN" not in body, "el cuerpo crudo contiene el literal NaN"

    data = json.loads(body.decode("utf-8", "replace"))
    geo = data["channels"]["geo"]
    assert geo["stride"] == 1, f"geo.stride={geo['stride']}, se esperaba 1 (max_points alto)"
    assert None in geo["min"], "geo.min no tiene ningún null: el NaN de la fixture no se convirtió"
    assert None in geo["max"], "geo.max no tiene ningún null: el NaN de la fixture no se convirtió"
    numeric = [v for v in geo["min"] if v is not None]
    assert numeric, "geo.min no tiene ningún bucket con dato real (además de los null)"


@check("capturas.signal.geo_flip_override", "geo_flip=1 invierte sólo el geo, en preview, sin tocar el hammer",
      mode="sandbox")
def _signal_geo_flip(ctx: Ctx) -> None:
    shot_id = _write_fixture(ctx, "smoke_polaridad", spike=1.0, with_nan=False)

    base = ctx.json_get(f"/api/signal?shot_id={shot_id}&max_points=100000")
    flipped = ctx.json_get(f"/api/signal?shot_id={shot_id}&max_points=100000&geo_flip=1")

    g0, g1 = base["channels"]["geo"], flipped["channels"]["geo"]
    g0_max = max(v for v in g0["max"] if v is not None)
    g1_min = min(v for v in g1["min"] if v is not None)
    g1_max = max(v for v in g1["max"] if v is not None)
    assert g0_max >= 0.9, f"sin geo_flip: geo.max={g0_max}, se esperaba >= 0.9"
    assert g0["flip_applied"] is False, f"sin geo_flip: geo.flip_applied={g0['flip_applied']}"
    assert g1_min <= -0.9, f"con geo_flip=1: geo.min={g1_min}, se esperaba <= -0.9"
    assert g1_max <= 0.1, f"con geo_flip=1: geo.max={g1_max}, se esperaba <= 0.1"
    assert g1["flip_applied"] is True, f"con geo_flip=1: geo.flip_applied={g1['flip_applied']}"

    h0, h1 = base["channels"]["hammer"], flipped["channels"]["hammer"]
    assert h0["min"] == h1["min"] and h0["max"] == h1["max"], (
        "el hammer cambió entre las dos respuestas: geo_flip no tiene que tocarlo")


@check(
    "tabs.implementacion_conectada",
    "cada tab carga su módulo terminado y sus endpoints críticos",
)
def _tabs_implementacion_conectada(ctx: Ctx) -> None:
    expected = {
        "capturas.js": ("/api/pick", "base_revision"),
        "filtros.js": ("line_suppress_enabled", "include_envelope"),
        "agrupamiento.js": ("/api/groups", "base_revision"),
        "enfase.js": ("/api/alignment", "base_revision", "auto_align"),
        "promedios.js": ("/api/averages/arrival", "base_revision"),
        "waterfall.js": ("wiggle", "/api/exports", "geo-masw-request"),
        "masw.js": (
            "/api/masw/dispersion", "/api/masw/inversions",
            "/api/jobs/", "regions_by_mode", "edited_profile",
            "mi-launch-geopsy",
        ),
        "borrado.js": (
            "/api/deletion/preview", "/api/deletion/disable",
            "/api/deletion/restore", "No se borrará ningún archivo",
        ),
    }
    for filename, tokens in expected.items():
        code, body, _ = ctx.get(f"/static/js/tabs/{filename}")
        assert code == 200, f"{filename} -> {code}"
        source = body.decode("utf-8", "replace")
        for token in tokens:
            assert token in source, f"{filename} no contiene {token!r}"


@check(
    "masw.canchita_compatibilidad",
    "Canchita abre 607 anotaciones, 2 grupos, picks e inversión sin reescribir JSON/NPZ",
)
def _masw_canchita(ctx: Ctx) -> None:
    import hashlib

    processed = REPO / "data" / "processed" / "Canchita"
    state_path = processed / "field_review_masw_state.json"
    arrays_path = processed / "field_review_masw_state.npz"
    annotations_path = processed / "field_review_annotations.json"
    assert state_path.is_file() and arrays_path.is_file() and annotations_path.is_file()

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    before = (digest(state_path), digest(arrays_path))
    state = ctx.json_get("/api/masw/state?campaign=Canchita")
    groups = ctx.json_get("/api/groups?campaign=Canchita")
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    assert len(annotations.get("annotations") or []) == 607, (
        f"Canchita tiene {len(annotations.get('annotations') or [])} anotaciones"
    )
    assert groups["group_count"] == 2, groups["group_count"]
    picks = (state.get("masw") or {}).get("picks_by_mode") or {}
    assert len(picks.get("0") or []) == 112, {
        key: len(value) for key, value in picks.items()
    }
    assert {"inv_beta", "inv_h", "inv_freqs", "inv_c_obs", "inv_c_t"} <= set(
        state.get("array_keys") or []
    ), state.get("array_keys")
    assert state.get("result_arrays", {}).get("beta"), "no se restauró el perfil Vs"
    assert before == (digest(state_path), digest(arrays_path)), (
        "GET /api/masw/state reescribió el estado histórico"
    )


@check(
    "masw.roundtrip_json_npz",
    "round-trip web conserva claves desconocidas y el NPZ autoritativo; stale -> 409",
    mode="sandbox",
)
def _masw_roundtrip(ctx: Ctx) -> None:
    import hashlib
    import numpy as np

    initial = ctx.json_get("/api/masw/state")
    path = Path(initial["path"])
    arrays_path = Path(initial["arrays_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": 1,
        "vendor_unknown": {"keep": [1, 2, 3]},
        "masw": {
            "unknown_masw": {"keep": True},
            "picks_by_mode": {
                "0": [[5.0, 180.0], [10.0, 220.0], [20.0, 280.0]],
                "1": [[8.0, 320.0], [14.0, 350.0], [22.0, 390.0]],
            },
            "regions_by_mode": {
                "0": [
                    [[4.0, 100.0], [25.0, 100.0], [25.0, 400.0]],
                    [[30.0, 150.0], [40.0, 180.0], [35.0, 300.0]],
                ],
                "1": [[[6.0, 260.0], [25.0, 300.0], [24.0, 450.0]]],
            },
        },
    }, indent=2), encoding="utf-8")
    np.savez_compressed(arrays_path, vendor_array=np.arange(9, dtype=np.float64))
    npz_before = hashlib.sha256(arrays_path.read_bytes()).hexdigest()

    loaded = ctx.json_get("/api/masw/state")
    code, raw, _ = ctx.post(
        "/api/masw/state",
        json.dumps({
            "base_revision": loaded["revision"],
            "masw": {
                "display_options": {"freq_log": True, "intensity_per_freq": True},
                "edited_profile": {"beta": [180.0, 320.0], "h": [4.5]},
            },
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"round-trip MASW -> {code}: {raw[:300]!r}"
    saved = json.loads(raw)
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert disk["vendor_unknown"]["keep"] == [1, 2, 3], disk
    assert disk["masw"]["unknown_masw"]["keep"] is True, disk["masw"]
    assert set(disk["masw"]["picks_by_mode"]) == {"0", "1"}, disk["masw"]
    assert len(disk["masw"]["regions_by_mode"]["0"]) == 2, disk["masw"]
    assert hashlib.sha256(arrays_path.read_bytes()).hexdigest() == npz_before, (
        "guardar opciones JSON reescribió el NPZ"
    )
    assert "vendor_array" in saved["array_keys"], saved["array_keys"]

    # La revisión cubre el bundle JSON+NPZ: si PyQt cambia sólo los arrays,
    # una pestaña web abierta no puede guardar sobre un resultado científico
    # que ya no es el mismo.
    fresh = ctx.json_get("/api/masw/state")
    with np.load(arrays_path, allow_pickle=False) as archive:
        externally_changed = {key: archive[key] for key in archive.files}
    externally_changed["pyqt_external_array"] = np.arange(3, dtype=np.float64)
    np.savez_compressed(arrays_path, **externally_changed)
    code, _, _ = ctx.post(
        "/api/masw/state",
        json.dumps({
            "base_revision": fresh["revision"],
            "masw": {"active_mode": 2},
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 409, f"NPZ obsoleto no produjo 409: {code}"

    code, _, _ = ctx.post(
        "/api/masw/state",
        json.dumps({
            "base_revision": loaded["revision"],
            "masw": {"active_mode": 1},
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 409, f"estado MASW obsoleto -> {code}, se esperaba 409"


@check(
    "masw.trabajos_artefactos_cancelacion",
    "la cola científica exporta artefactos y termina un proceso hijo al cancelar",
    mode="sandbox",
)
def _masw_jobs(ctx: Ctx) -> None:
    curves = {
        "0": [[5.0, 180.0], [8.0, 205.0], [12.0, 235.0], [18.0, 270.0]],
        "1": [[6.0, 315.0], [10.0, 340.0], [15.0, 370.0], [22.0, 405.0]],
    }
    code, raw, _ = ctx.post(
        "/api/masw/inversions",
        json.dumps({
            "backend": "geopsy",
            "curves_by_mode": curves,
            "params": {"seed": 123},
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"encolar Geopsy -> {code}: {raw[:300]!r}"
    export_job = json.loads(raw)
    deadline = time.time() + 30
    while time.time() < deadline:
        export_job = ctx.json_get(f"/api/jobs/{export_job['id']}")
        if export_job["state"] in {"listo", "error", "cancelado"}:
            break
        time.sleep(0.25)
    assert export_job["state"] == "listo", export_job
    assert export_job.get("result", {}).get("launched") is False, export_job
    artifacts = export_job.get("result", {}).get("artifacts") or []
    assert artifacts, export_job
    artifact_names = [str(item.get("name") or "") for item in artifacts]
    assert any("mode0" in name for name in artifact_names), artifact_names
    assert any("mode1" in name for name in artifact_names), artifact_names
    code, content, _ = ctx.get(f"/api/artifacts/{artifacts[0]['id']}")
    assert code == 200 and content, f"artefacto -> {code}, {len(content)} B"

    # Ejecución reducida del backend in-proc siempre disponible. Valida el
    # contrato normalizado, la persistencia del original y los CSV/JSON.
    code, raw, _ = ctx.post(
        "/api/masw/inversions",
        json.dumps({
            "backend": "maswavespy",
            "base_revision": ctx.json_get("/api/masw/state")["revision"],
            "curves_by_mode": curves,
            "params": {
                "n_layers": 3, "n_iter": 3, "bs": 5.0, "bh": 8.0,
                "nu": 0.35, "rho": 1850.0,
            },
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"encolar inversión reducida -> {code}: {raw[:300]!r}"
    reduced = json.loads(raw)
    deadline = time.time() + 30
    while time.time() < deadline:
        reduced = ctx.json_get(f"/api/jobs/{reduced['id']}")
        if reduced["state"] in {"listo", "error", "cancelado"}:
            break
        time.sleep(0.2)
    assert reduced["state"] == "listo", reduced
    profile = reduced.get("result", {}).get("profile") or {}
    assert len(profile.get("beta") or []) == 4, profile
    assert len(profile.get("h") or []) == 3, profile
    assert len(reduced.get("result", {}).get("artifacts") or []) == 3, reduced
    persisted = ctx.json_get("/api/masw/state")
    assert persisted.get("result_arrays", {}).get("beta") == profile["beta"], (
        "el mejor modelo original no quedó persistido"
    )

    code, raw, _ = ctx.post(
        "/api/masw/inversions",
        json.dumps({
            "backend": "maswavespy",
            "base_revision": ctx.json_get("/api/masw/state")["revision"],
            "curves_by_mode": curves,
            "params": {"n_layers": 4, "n_iter": 1_000_000},
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"encolar inversión cancelable -> {code}: {raw[:300]!r}"
    job = json.loads(raw)
    deadline = time.time() + 15
    while time.time() < deadline:
        job = ctx.json_get(f"/api/jobs/{job['id']}")
        if job["state"] == "running":
            break
        if job["state"] in {"listo", "error"}:
            raise AssertionError(f"la inversión terminó antes de poder cancelarla: {job}")
        time.sleep(0.1)
    assert job["state"] == "running", f"el proceso hijo no arrancó: {job}"

    # Encolar detrás del trabajo largo una inversión con revisión todavía
    # vigente y cambiar el estado antes de que llegue a persistir. El cálculo
    # debe sobrevivir como artefacto, sin mezclarse sobre el estado nuevo.
    stale_base = ctx.json_get("/api/masw/state")["revision"]
    code, raw, _ = ctx.post(
        "/api/masw/inversions",
        json.dumps({
            "backend": "maswavespy",
            "base_revision": stale_base,
            "curves_by_mode": curves,
            "params": {
                "n_layers": 2,
                "n_iter": 2,
                "bs": 5.0,
                "bh": 8.0,
                "nu": 0.35,
                "rho": 1850.0,
            },
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"encolar inversión obsoleta -> {code}: {raw[:300]!r}"
    stale_job = json.loads(raw)
    code, raw, _ = ctx.post(
        "/api/masw/state",
        json.dumps({
            "base_revision": stale_base,
            "masw": {"display_options": {"freq_log": True}},
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"mutar estado durante inversión -> {code}: {raw[:300]!r}"

    # La recepción usa una cola independiente: una inversión larga no puede
    # impedir que el master entregue un ZIP nuevo.
    upload = _master_zip(prefix="smoke_during_inversion")
    started = time.monotonic()
    code, raw, _ = ctx.post(
        "/ingest",
        upload,
        {
            "Content-Type": "application/zip",
            "X-Geo-Filename": "smoke_during_inversion.zip",
        },
    )
    elapsed = time.monotonic() - started
    assert code == 200 and elapsed < 2.0, (
        f"ingesta durante inversión -> {code} en {elapsed:.2f}s: {raw[:200]!r}"
    )

    code, raw, _ = ctx.post(f"/api/jobs/{job['id']}/cancel")
    assert code == 200, f"cancel -> {code}: {raw[:200]!r}"
    deadline = time.time() + 15
    while time.time() < deadline:
        job = ctx.json_get(f"/api/jobs/{job['id']}")
        if job["state"] == "cancelado":
            break
        time.sleep(0.2)
    assert job["state"] == "cancelado", f"el proceso no fue terminado: {job}"

    deadline = time.time() + 30
    while time.time() < deadline:
        stale_job = ctx.json_get(f"/api/jobs/{stale_job['id']}")
        if stale_job["state"] in {"listo", "error", "cancelado"}:
            break
        time.sleep(0.2)
    assert stale_job["state"] == "listo", stale_job
    stale_result = stale_job.get("result") or {}
    assert stale_result.get("persisted") is False, stale_result
    assert "revisi" in str(stale_result.get("persistence_error", "")).lower(), \
        stale_result
    stale_artifacts = stale_result.get("artifacts") or []
    assert len(stale_artifacts) == 3, stale_result
    config = next(
        item for item in stale_artifacts
        if str(item.get("name", "")).endswith(".json")
    )
    code, content, _ = ctx.get(f"/api/artifacts/{config['id']}")
    assert code == 200 and json.loads(content).get("persisted") is False, (
        code, content[:300]
    )


@check(
    "reinicio.trabajos_persistentes",
    "al reiniciar se recuperan una ingesta y un análisis interrumpidos",
    mode="sandbox",
)
def _trabajos_persistentes(ctx: Ctx) -> None:
    """Arranca un segundo servidor sobre estados que quedaron ``running``.

    Es la forma determinista de representar un corte abrupto: ambos archivos
    son exactamente lo que queda en disco si el proceso muere mientras trabaja.
    El servidor nuevo debe reencolar las dos colas separadas y terminarlas.
    """
    import hashlib
    nested = Path(tempfile.mkdtemp(prefix="smoke_restart_"))
    raw_root = nested / "raw"
    data_root = nested / "server"
    raw_root.mkdir(parents=True)
    (data_root / "zips").mkdir(parents=True)

    zip_payload = _master_zip(prefix="restart_ingest")
    digest = hashlib.sha256(zip_payload).hexdigest()
    ingest_id = "restart_ingest_job"
    (data_root / "zips" / f"{ingest_id}.zip").write_bytes(zip_payload)
    (data_root / "jobs.json").write_text(
        json.dumps({
            "jobs": [{
                "id": ingest_id,
                "job_id": ingest_id,
                "kind": "ingest",
                "filename": "restart_ingest.zip",
                "bytes": len(zip_payload),
                "state": "procesando",
                "stage": "autopick",
                "progress": 0.8,
                "created_at": "2026-07-26T00:00:00+00:00",
                "started_at": "2026-07-26T00:00:01+00:00",
                "finished_at": "",
                "folder": "",
                "captures": 0,
                "nodes": 0,
                "shots": 0,
                "picks": 0,
                "error": "",
                "sha256": digest,
                "duplicate_of": "",
                "log": ["interrumpido por el gate"],
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    analysis_id = "restart_analysis_job"
    curves = {
        "0": [[5.0, 180.0], [8.0, 205.0], [12.0, 235.0], [18.0, 270.0]]
    }
    (data_root / "analysis_jobs.json").write_text(
        json.dumps({
            "jobs": [{
                "id": analysis_id,
                "kind": "masw_inversion",
                "campaign": ".",
                "payload": {
                    "raw_root": str(raw_root),
                    "backend": "geopsy",
                    "curves_by_mode": curves,
                    "params": {"seed": 321},
                },
                "state": "running",
                "stage": "computing",
                "progress": 0.45,
                "created_at": "2026-07-26T00:00:00+00:00",
                "started_at": "2026-07-26T00:00:01+00:00",
                "finished_at": "",
                "error": "",
                "result": {},
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    nested_log = LOG_DIR / (
        f"gate_{time.strftime('%Y%m%d_%H%M%S')}_servidor_restart.log"
    )
    server = start_server(raw_root, data_root, nested, nested_log)
    assert server is not None, f"el servidor de reinicio no arrancó: {nested_log}"
    restart_ctx = Ctx(server.base_url, raw_root, sandbox=True)
    try:
        deadline = time.time() + 30
        ingest = analysis = {}
        while time.time() < deadline:
            ingest = restart_ctx.json_get(f"/api/jobs/{ingest_id}")
            analysis = restart_ctx.json_get(f"/api/jobs/{analysis_id}")
            if (
                ingest.get("state") in {"listo", "error", "cancelado"}
                and analysis.get("state") in {"listo", "error", "cancelado"}
            ):
                break
            time.sleep(0.2)
        assert ingest.get("state") == "listo", ingest
        assert ingest.get("stage") == "complete", ingest
        assert any("recuperado después de reiniciar" in line
                   for line in ingest.get("log", [])), ingest
        assert (raw_root / "restart_ingest").is_dir(), \
            "la publicación de la ingesta recuperada no quedó en raw"

        assert analysis.get("state") == "listo", analysis
        assert analysis.get("stage") == "complete", analysis
        assert analysis.get("result", {}).get("export_only") is True, analysis
        assert analysis.get("result", {}).get("artifacts"), analysis
    finally:
        server.stop()


@check(
    "pipeline.e2e_zip_a_vs",
    "un ZIP atraviesa validación, Waterfall, dispersión, picks e inversión hasta perfil Vs",
    mode="sandbox",
)
def _pipeline_e2e_zip_a_vs(ctx: Ctx) -> None:
    import io
    import zipfile
    import numpy as np

    root_name = "smoke_e2e_zip_vs"
    fs = 1000
    samples = 2500
    distances = (2.0, 4.0, 6.0, 8.0, 10.0, 12.0)
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(
        archive_buffer, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr(
            f"{root_name}/metadata.json",
            json.dumps({
                "schema": "geophone_scope_web_zip_v4",
                "schema_target": "geophone_scope_mat_node_prefix_v1",
                "source": "server_smoke_test",
                "layout": "maestro_metadata_plus_capture_metadata_and_pcb_dirs",
            }),
        )
        for order, distance in enumerate(distances, start=1):
            capture = f"{order:03d}_e2e"
            base = f"{root_name}/captures/{capture}"
            hammer_rel = f"captures/{capture}/hammer_s1/raw_f32le.bin"
            geo_rel = f"captures/{capture}/geo_s2/raw_f32le.bin"
            metadata = {
                "order": order,
                "fs": fs,
                "nodes": [
                    {
                        "index": 1,
                        "pcb_id": "S1",
                        "role": "hammer",
                        "type": "Hammer",
                        "fs": fs,
                        "data_dir": f"captures/{capture}/hammer_s1",
                        "raw_file": hammer_rel,
                    },
                    {
                        "index": 2,
                        "pcb_id": "S2",
                        "role": "geo",
                        "type": "Geo",
                        "fs": fs,
                        "position_m": distance,
                        "data_dir": f"captures/{capture}/geo_s2",
                        "raw_file": geo_rel,
                    },
                ],
            }
            hammer = np.zeros(samples, dtype=np.float32)
            hammer[498:503] = np.float32(0.8 + order * 0.01)
            geo = np.zeros(samples, dtype=np.float32)
            arrival = int(round((0.5 + distance / 220.0) * fs))
            tail = np.arange(samples - arrival, dtype=np.float64)
            geo[arrival:] = np.asarray(
                np.sin(2.0 * np.pi * 18.0 * tail / fs)
                * np.exp(-tail / (0.35 * fs)),
                dtype=np.float32,
            )
            archive.writestr(
                f"{base}/metadata.json",
                json.dumps(metadata, ensure_ascii=False),
            )
            archive.writestr(
                f"{root_name}/{hammer_rel}", hammer.tobytes()
            )
            archive.writestr(
                f"{root_name}/{geo_rel}", geo.tobytes()
            )

    code, raw, headers = ctx.post(
        "/ingest",
        archive_buffer.getvalue(),
        {
            "Content-Type": "application/zip",
            "X-Geo-Filename": f"{root_name}.zip",
        },
    )
    assert code == 200, f"ingesta E2E -> {code}: {raw[:300]!r}"
    ingest_job_id = headers.get("X-Geo-Job")
    assert ingest_job_id, headers
    deadline = time.time() + 30
    ingest_job = {}
    while time.time() < deadline:
        ingest_job = ctx.json_get(f"/api/jobs/{ingest_job_id}")
        if ingest_job["state"] in {"listo", "error", "cancelado"}:
            break
        time.sleep(0.2)
    assert ingest_job["state"] == "listo", ingest_job
    assert ingest_job["folder"] == root_name, ingest_job
    assert ingest_job["shots"] == len(distances), ingest_job

    captures = ctx.json_get("/api/captures")
    e2e_rows = [
        row for row in captures.get("rows", [])
        if row.get("folder") == root_name and row.get("shot_id")
    ]
    assert len(e2e_rows) == len(distances), e2e_rows
    for row in e2e_rows:
        current = ctx.json_get("/api/captures")
        campaign = str(row.get("campaign", ""))
        revision_value = str(
            (current.get("annotation_revisions") or {}).get(campaign, "missing")
        )
        code, raw, _ = ctx.post(
            "/api/pick",
            json.dumps({
                "shot_id": row["shot_id"],
                "campaign": campaign,
                "reviewed": True,
                "accepted": True,
                "base_revision": revision_value,
            }).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        assert code == 200, (
            f"validar {row['shot_id']} del ZIP E2E -> {code}: {raw[:200]!r}"
        )

    groups = ctx.json_get("/api/groups")
    assignments = {
        item["folder"]: (1 if item["folder"] == root_name else 2)
        for item in groups.get("folders", [])
    }
    code, raw, _ = ctx.post(
        "/api/groups",
        json.dumps({
            "group_count": 2,
            "assign": assignments,
            "base_revision": groups["revision"],
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"aislar grupo E2E -> {code}: {raw[:200]!r}"

    waterfall = ctx.json_get("/api/waterfall?group_id=1")
    assert len(waterfall["traces"]) == len(distances), waterfall
    state = ctx.json_get("/api/masw/state")
    dispersion_params = {
        "c_min": 50.0,
        "c_max": 500.0,
        "c_step": 5.0,
        "f_min": 3.0,
        "f_max": 50.0,
    }
    code, raw, _ = ctx.post(
        "/api/masw/dispersion",
        json.dumps({
            "base_revision": state["revision"],
            "group_id": 1,
            "group_weights": {"1": 1.0},
            "params": dispersion_params,
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
        timeout_s=45,
    )
    assert code == 200, f"dispersión E2E -> {code}: {raw[:300]!r}"
    dispersion = json.loads(raw)
    assert dispersion["n_channels"] == len(distances), dispersion

    code, raw, _ = ctx.post(
        "/api/masw/auto-pick",
        json.dumps({
            "group_weights": {"1": 1.0},
            "regions_by_mode": {
                "0": [[[3.0, 50.0], [50.0, 50.0],
                       [50.0, 500.0], [3.0, 500.0]]]
            },
            "pick_fmin": 6.0,
            "pick_fmax": 45.0,
            "params": dispersion_params,
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
        timeout_s=45,
    )
    assert code == 200, f"auto-pick E2E -> {code}: {raw[:300]!r}"
    curves = json.loads(raw).get("picks_by_mode") or {}
    assert len(curves.get("0") or []) >= 3, curves

    state_revision = dispersion["state"]["revision"]
    code, raw, _ = ctx.post(
        "/api/masw/inversions",
        json.dumps({
            "backend": "maswavespy",
            "base_revision": state_revision,
            "curves_by_mode": curves,
            "params": {
                "n_layers": 3,
                "n_iter": 3,
                "bs": 5.0,
                "bh": 8.0,
                "nu": 0.35,
                "rho": 1850.0,
                "seed": 123,
            },
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"inversión E2E -> {code}: {raw[:300]!r}"
    inversion = json.loads(raw)
    deadline = time.time() + 30
    while time.time() < deadline:
        inversion = ctx.json_get(f"/api/jobs/{inversion['id']}")
        if inversion["state"] in {"listo", "error", "cancelado"}:
            break
        time.sleep(0.2)
    assert inversion["state"] == "listo", inversion
    profile = inversion.get("result", {}).get("profile") or {}
    assert len(profile.get("beta") or []) == 4, profile
    assert len(profile.get("h") or []) == 3, profile
    artifacts = inversion.get("result", {}).get("artifacts") or []
    assert {".csv", ".json"} <= {Path(item["name"]).suffix for item in artifacts}


@check(
    "borrado.preview_confirmacion",
    "Borrado sólo aplica una bandera reversible; raw y ZIP nunca se eliminan",
    mode="sandbox",
)
def _borrado_seguro(ctx: Ctx) -> None:
    import hashlib
    import numpy as np

    def tree_digest(path: Path) -> str:
        digest = hashlib.sha256()
        for item in sorted(p for p in path.rglob("*") if p.is_file()):
            digest.update(item.relative_to(path).as_posix().encode("utf-8"))
            digest.update(item.read_bytes())
        return digest.hexdigest()

    # Las rutas heredadas conservan compatibilidad, pero incluso confirmadas
    # sólo pueden poner la bandera global. El directorio debe quedar idéntico.
    _write_fixture(ctx, "smoke_legacy_borrar", spike=0.735, with_nan=False)
    legacy_path = ctx.raw_root / "smoke_legacy_borrar"
    legacy_before = tree_digest(legacy_path)
    code, raw, _ = ctx.post("/api/delete?folder=smoke_legacy_borrar", b"")
    assert code == 409, f"/api/delete heredado sin token -> {code}: {raw[:200]!r}"
    assert legacy_path.is_dir(), "/api/delete heredado tocó el raw en la primera llamada"
    legacy_preview = json.loads(raw)
    assert legacy_preview["confirmation_required"] is True, legacy_preview
    assert legacy_preview["physical_deletion"] is False, legacy_preview
    legacy_token = urllib.parse.quote(str(legacy_preview["token"]), safe="")
    code, raw, _ = ctx.post(
        f"/api/delete?folder=smoke_legacy_borrar&token={legacy_token}",
        b"",
    )
    assert code == 200, f"/api/delete heredado confirmado -> {code}: {raw[:200]!r}"
    legacy_outcome = json.loads(raw)
    assert legacy_outcome["disabled"] == [".|smoke_legacy_borrar"], legacy_outcome
    assert legacy_outcome["deleted"] == [], legacy_outcome
    assert legacy_path.is_dir() and tree_digest(legacy_path) == legacy_before, \
        "/api/delete heredado modificó o eliminó archivos"
    assert all(
        row["folder"] != "smoke_legacy_borrar"
        for row in ctx.json_get("/api/captures")["rows"]
    ), "la carpeta desactivada sigue entrando en Capturas"

    # El barrido legado de carpetas sin martillo tampoco puede actuar al primer
    # POST. Se construye una carpeta inequívoca sin canal hammer.
    no_hammer = ctx.raw_root / "smoke_sin_martillo" / "captures" / "001_smoke"
    geo_dir = no_hammer / "geo1_s2"
    geo_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "order": 1,
        "fs": 1000,
        "nodes": [{
            "index": 1,
            "pcb_id": "S2",
            "role": "geo",
            "type": "Geo",
            "fs": 1000,
            "position_m": 4.0,
            "data_dir": "captures/001_smoke/geo1_s2",
            "raw_file": "captures/001_smoke/geo1_s2/raw_f32le.bin",
        }],
    }
    (no_hammer / "metadata.json").write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )
    np.zeros(1000, dtype=np.float32).tofile(geo_dir / "raw_f32le.bin")
    catalog = ctx.json_get("/api/deletion")
    no_hammer_row = next(
        (item for item in catalog["rows"]
         if item["folder"] == "smoke_sin_martillo"),
        None,
    )
    assert no_hammer_row and "sin_martillo" in no_hammer_row["flags"], \
        f"fixture sin martillo no clasificada: {no_hammer_row}"
    code, raw, _ = ctx.post("/api/delete-sin-hammer", b"")
    assert code == 409, \
        f"/api/delete-sin-hammer sin token -> {code}: {raw[:200]!r}"
    assert (ctx.raw_root / "smoke_sin_martillo").is_dir(), \
        "/api/delete-sin-hammer modificó sin confirmación"

    target_shot = _write_fixture(
        ctx, "smoke_borrar", spike=0.734, with_nan=False
    )
    campaign_id, ann_revision = _annotation_revision(ctx, target_shot)
    code, raw, _ = ctx.post(
        "/api/pick",
        json.dumps({
            "shot_id": target_shot,
            "campaign": campaign_id,
            "reviewed": True,
            "accepted": True,
            "base_revision": ann_revision,
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"validar fixture de cuarentena -> {code}: {raw[:200]!r}"
    target = ctx.raw_root / "smoke_borrar"
    before = tree_digest(target)
    server_root = Path(ctx.json_get("/api/meta")["roots"]["server"])
    zip_probe = server_root / "zips" / "smoke_never_delete.zip"
    zip_probe.parent.mkdir(parents=True, exist_ok=True)
    zip_probe.write_bytes(b"PK\\x03\\x04smoke-preserved")
    zip_before = hashlib.sha256(zip_probe.read_bytes()).hexdigest()
    averages_before = ctx.json_get("/api/averages?group_id=1")
    average_n_before = sum(int(group["n"]) for group in averages_before["groups"])
    catalog = ctx.json_get("/api/deletion")
    row = next((item for item in catalog["rows"]
                if item["folder"] == "smoke_borrar"), None)
    assert row, f"smoke_borrar no aparece en Borrado: {catalog}"
    for key in ("date", "site", "distances", "flags"):
        assert key in row, f"fila de Borrado sin {key!r}: {row}"

    body = {"keys": [row["key"]]}
    code, raw, _ = ctx.post(
        "/api/deletion/delete",
        json.dumps(body).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 409, f"desactivación sin preview -> {code}: {raw[:200]!r}"
    assert target.is_dir() and tree_digest(target) == before, \
        "se modificó el raw sin confirmación"

    code, raw, _ = ctx.post(
        "/api/deletion/preview",
        json.dumps({**body, "action": "disable"}).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"preview -> {code}: {raw[:200]!r}"
    preview = json.loads(raw)
    assert preview["count"] == 1 and preview["zip_preserved"] is True, preview
    assert preview["physical_deletion"] is False, preview
    assert preview["rows"][0]["key"] == row["key"], preview

    # El lock compartido debe coordinar procesos, no sólo hilos de FastAPI.
    holder_code = (
        "import time\n"
        "from pathlib import Path\n"
        "from geophone_scope import field_review_data as frd\n"
        f"root = Path({str(ctx.raw_root)!r})\n"
        "path = frd.default_disabled_folders_path(root)\n"
        "with frd.disabled_folders_lock(path):\n"
        " print('locked', flush=True)\n"
        " time.sleep(0.6)\n"
    )
    holder_env = dict(os.environ)
    holder_env["TESIS_DATA_ROOT"] = str(ctx.raw_root.parent)
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        cwd=str(PYTHON_ROOT),
        env=holder_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "locked", (
        holder.stderr.read() if holder.stderr else ""
    )
    lock_wait_started = time.monotonic()
    code, raw, _ = ctx.post(
        "/api/deletion/disable",
        json.dumps({
            "keys": [row["key"]],
            "token": preview["token"],
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    lock_wait_s = time.monotonic() - lock_wait_started
    holder.wait(timeout=10)
    assert lock_wait_s >= 0.35, f"el servidor ignoró el lock PyQt ({lock_wait_s:.3f}s)"
    assert code == 200, f"desactivación confirmada -> {code}: {raw[:200]!r}"
    outcome = json.loads(raw)
    assert outcome["disabled"] == [row["key"]] and outcome["deleted"] == [], outcome
    assert target.is_dir() and tree_digest(target) == before, \
        "desactivar modificó o eliminó el raw"
    assert zip_probe.is_file() and hashlib.sha256(zip_probe.read_bytes()).hexdigest() == zip_before, \
        "desactivar eliminó o modificó un ZIP"
    active = ctx.json_get("/api/captures")
    assert all(item["folder"] != "smoke_borrar" for item in active["rows"]), \
        "la cuarentena no excluyó la carpeta de Capturas"
    grouped = ctx.json_get("/api/groups")
    assert all(item["folder"] != "smoke_borrar" for item in grouped["folders"]), \
        "la cuarentena no excluyó la carpeta de Agrupamiento"
    averages_disabled = ctx.json_get("/api/averages?group_id=1")
    average_n_disabled = sum(
        int(group["n"]) for group in averages_disabled["groups"]
    )
    assert average_n_disabled == average_n_before - 1, (
        average_n_before, average_n_disabled
    )

    # Simula una ventana PyQt que tenía un snapshot viejo (sin __all__) y
    # guarda un rechazo de Enfase después de la web. El helper compartido debe
    # hacer merge bajo lock entre procesos y conservar la cuarentena.
    pyqt_code = (
        "from pathlib import Path\n"
        "from geophone_scope import field_review_data as frd\n"
        f"root = Path({str(ctx.raw_root)!r})\n"
        "path = frd.default_disabled_folders_path(root)\n"
        "frd.save_disabled_folders(path, {'999m': ['smoke_borrar']})\n"
    )
    pyqt_env = dict(os.environ)
    pyqt_env["TESIS_DATA_ROOT"] = str(ctx.raw_root.parent)
    pyqt_save = subprocess.run(
        [sys.executable, "-c", pyqt_code],
        cwd=str(PYTHON_ROOT),
        env=pyqt_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert pyqt_save.returncode == 0, (
        pyqt_save.stdout, pyqt_save.stderr
    )
    disabled_catalog = ctx.json_get("/api/deletion")
    disabled_row = next(item for item in disabled_catalog["rows"]
                        if item["key"] == row["key"])
    assert disabled_row["disabled"] is True, disabled_row
    assert "desactivada" in disabled_row["flags"], disabled_row

    # Restaurar usa la misma preview exacta y debe devolver el mismo raw al
    # pipeline sin reescribir un solo byte.
    code, raw, _ = ctx.post(
        "/api/deletion/preview",
        json.dumps({"keys": [row["key"]], "action": "restore"}).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"preview restauración -> {code}: {raw[:200]!r}"
    stale_restore_preview = json.loads(raw)
    code, raw, _ = ctx.post(
        "/api/deletion/preview",
        json.dumps({"keys": [row["key"]], "action": "restore"}).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"segunda preview restauración -> {code}: {raw[:200]!r}"
    restore_preview = json.loads(raw)
    code, raw, _ = ctx.post(
        "/api/deletion/restore",
        json.dumps({
            "keys": [row["key"]],
            "token": restore_preview["token"],
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 200, f"restauración -> {code}: {raw[:200]!r}"
    restored = json.loads(raw)
    assert restored["restored"] == [row["key"]] and restored["deleted"] == [], restored
    code, raw, _ = ctx.post(
        "/api/deletion/restore",
        json.dumps({
            "keys": [row["key"]],
            "token": stale_restore_preview["token"],
        }).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    assert code == 409, f"preview obsoleta no produjo 409: {code}: {raw[:200]!r}"
    assert target.is_dir() and tree_digest(target) == before, \
        "restaurar reescribió el raw"
    active = ctx.json_get("/api/captures")
    assert any(item["folder"] == "smoke_borrar" for item in active["rows"]), \
        "la carpeta restaurada no volvió a Capturas"
    averages_restored = ctx.json_get("/api/averages?group_id=1")
    assert sum(int(group["n"]) for group in averages_restored["groups"]) == average_n_before, \
        "la carpeta restaurada no volvió a los promedios"

    history = server_root / "deletion_history.json"
    assert history.is_file(), "no se guardó historial de cuarentena"
    events = json.loads(history.read_text(encoding="utf-8")).get("events") or []
    target_events = [event for event in events if event.get("keys") == [row["key"]]]
    assert [event.get("action") for event in target_events[-2:]] == [
        "disable", "restore",
    ], target_events[-2:]
    assert all(event.get("deleted", []) == [] for event in target_events), target_events


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
                 log_path: Path, boot_timeout_s: float = 60.0,
                 read_only: bool = False) -> Server | None:
    """Arranca el servidor bajo prueba con su salida A UN ARCHIVO.

    Nunca a subprocess.PIPE sin leerlo: el buffer del pipe son ~64 kB y cuando se
    llena el servidor se **bloquea escribiendo**, así que las requests empiezan a
    colgarse. Pasó de verdad: `refactor.worker_no_bloquea` daba timeout a los 30 s
    en tanda y pasaba en 0.1 s aislado, y el bug era del gate, no del servidor.
    Además el archivo es el log que hace falta para no adivinar.
    """
    port = free_port()
    processed_root = raw_root.parent / "processed"
    cmd = [sys.executable, "-m", "server", "--port", str(port),
           "--raw-root", str(raw_root), "--processed-root", str(processed_root),
           "--data-root", str(data_root)]
    if read_only:
        cmd.append("--read-only")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8", errors="replace")

    env = dict(os.environ)
    if tmp is not None and "smoke_sandbox" in str(raw_root):
        # SANDBOX: encerrar también lo que se ESCRIBE.
        #
        # `frd._procesados_dir_for` resuelve la carpeta de salida por
        # `raw_root.name`, no por la ruta completa. El raw del sandbox se llama
        # "raw", igual que `data/raw`, así que sin esto
        # `default_annotations_path(<tmp>/raw)` apunta al MISMO archivo que
        # `default_annotations_path(data/raw)`: una corrida del gate que
        # ingestara una captura completa borraría los picks validados a mano.
        # Lo mismo vale para filter_settings, alignment_offsets y average_arrivals.
        #
        # `frd._discover_data_root` respeta TESIS_DATA_ROOT, así que apuntándola
        # al temporal todo lo que escriba el sandbox cae adentro. El modo `read`
        # (tmp=None) no la define y sigue viendo los datos reales.
        env["TESIS_DATA_ROOT"] = str(tmp)
        # Cotas reducidas pero proporcionales para ejercitar las cuatro defensas
        # sin fabricar gigabytes durante cada gate.
        env["TESIS_MAX_UPLOAD_BYTES"] = str(2 * 1024 * 1024)
        env["TESIS_MAX_ZIP_FILES"] = "64"
        env["TESIS_MAX_UNCOMPRESSED_BYTES"] = str(1024 * 1024)
        env["TESIS_MAX_COMPRESSION_RATIO"] = "250"

    proc = subprocess.Popen(cmd, cwd=str(PYTHON_ROOT), stdout=handle,
                            stderr=subprocess.STDOUT, text=True, env=env)
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


def start_server_from_storage_root(
    storage_root: Path,
    log_path: Path,
    boot_timeout_s: float = 60.0,
) -> Server | None:
    """Arranca sin banderas de raíces para probar TESIS_DATA_ROOT de verdad."""
    port = free_port()
    cmd = [
        sys.executable,
        "-m",
        "server",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8", errors="replace")
    env = dict(os.environ)
    env["TESIS_DATA_ROOT"] = str(storage_root)
    for name in (
        "TESIS_RAW_ROOT",
        "TESIS_PROCESSED_ROOT",
        "TESIS_SERVER_DATA_ROOT",
        "TESIS_READ_ONLY",
    ):
        env.pop(name, None)
    proc = subprocess.Popen(
        cmd,
        cwd=str(PYTHON_ROOT),
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    srv = Server(proc, port, storage_root, log_path)
    deadline = time.time() + boot_timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            rec(
                "el servidor TESIS_DATA_ROOT murió al arrancar "
                f"(rc={proc.returncode}):\n{srv.log_tail()}",
                echo=True,
            )
            srv.stop()
            return None
        try:
            with urllib.request.urlopen(f"{srv.base_url}/health", timeout=2) as response:
                if response.status == 200:
                    return srv
        except Exception:
            time.sleep(0.4)
    rec(
        "el servidor TESIS_DATA_ROOT no respondió /health:\n"
        f"{srv.log_tail()}",
        echo=True,
    )
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

    if not args.only:
        registered_ids = [c.cid for c in REGISTRY]
        duplicate_ids = sorted({
            cid for cid in registered_ids if registered_ids.count(cid) > 1
        })
        missing_ids = sorted(REQUIRED_FULL_GATE_IDS - set(registered_ids))
        if duplicate_ids or missing_ids:
            if duplicate_ids:
                print(
                    "[gate] FALLA: ids de check duplicados: "
                    + ", ".join(duplicate_ids),
                    file=sys.stderr,
                )
            if missing_ids:
                print(
                    "[gate] FALLA: faltan checks obligatorios: "
                    + ", ".join(missing_ids),
                    file=sys.stderr,
                )
            return 1

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
            srv = start_server(
                raw_root, tmp / "server", tmp, srv_log, read_only=True,
            )
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
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps({"passed": passed, "total": len(results),
                        "run_log": str(run_log), "checks": results},
                       indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
