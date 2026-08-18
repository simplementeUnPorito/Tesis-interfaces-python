"""App FastAPI: routers, estáticos y DI del Pipeline.

No define un ``app`` a nivel de módulo a propósito: ``main()`` construye un solo
``Pipeline`` y lo pasa a ``create_app``. Si uvicorn recibiera el string
``"server.api:app"`` re-importaría este módulo y construiría un segundo
``Pipeline`` sobre el mismo ``jobs.json``.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .analysis_jobs import AnalysisJobs
from .pipeline import Pipeline

STATIC = Path(__file__).resolve().parent / "static"


def get_pipeline(request: Request) -> Pipeline:
    return request.app.state.pipeline


def get_analysis_jobs(request: Request) -> AnalysisJobs:
    return request.app.state.analysis_jobs


# Importados después de get_pipeline: los routers hacen `from ..api import
# get_pipeline` y, al estar este módulo en medio de su propia importación,
# necesitan encontrarlo ya definido.
from .routers import (admin, alignment, averages, dataset, deletion, filters,  # noqa: E402
                      grouping, ingest, jobs, kalman, masw, picks, waterfall)


class _TitleCaseHeaders:
    """Re-castea los nombres de header salientes a Title-Case.

    Los nombres de header HTTP son case-insensitive por spec (RFC 7230), pero el
    ``Handler`` viejo (``send_header``) los mandaba en Title-Case y el gate
    (``smoke_test.py``) hace ``dict(headers).get("Access-Control-Allow-Origin")``
    con esa case exacta. ASGI/Starlette normaliza todo a minúsculas al armar la
    respuesta; sin este middleware ``base.cors_preflight`` (que no se puede
    editar) deja de pasar aunque el CORS real ande bien en cualquier navegador.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [
                    (b"-".join(p[:1].upper() + p[1:] for p in name.split(b"-")), value)
                    for name, value in message.get("headers", [])
                ]
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_app(pipeline: Pipeline, *, read_only: bool = False) -> FastAPI:
    app = FastAPI(title="Servidor de datos Geophone", version="2")
    app.state.pipeline = pipeline
    app.state.analysis_jobs = AnalysisJobs(pipeline.data_root)
    app.state.started_at = datetime.now(timezone.utc)
    app.state.read_only = bool(read_only)

    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Geo-Filename"],
        max_age=86400,
    )

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        if app.state.read_only and request.method.upper() == "POST":
            response = JSONResponse(
                {
                    "detail": (
                        "servidor en modo sólo lectura; el POST fue rechazado "
                        "sin modificar datos"
                    )
                },
                status_code=405,
            )
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    # Starlette.add_middleware() inserta al principio de la lista: cada llamada
    # queda MÁS afuera que las anteriores. Esta va última a propósito, para
    # quedar la más externa y recastear también los headers que agregan
    # CORSMiddleware y no_store (si fuera más interna, corre antes que ellos y
    # sus headers le llegan sin recastear).
    app.add_middleware(_TitleCaseHeaders)

    app.include_router(ingest.router)
    app.include_router(dataset.router)
    app.include_router(admin.router)
    app.include_router(picks.router)
    app.include_router(filters.router)
    app.include_router(grouping.router)
    app.include_router(alignment.router)
    app.include_router(averages.router)
    app.include_router(waterfall.router)
    app.include_router(masw.router)
    # Opcional: si kalman_deconv no esta, sus rutas responden available=false y
    # ningun otro tab se entera. Ver server/kalman.py.
    app.include_router(kalman.router)
    app.include_router(deletion.router)
    app.include_router(jobs.router)

    @app.get("/api/meta")
    def meta() -> dict:
        python_root = Path(__file__).resolve().parents[1]
        build = os.environ.get("TESIS_BUILD_VERSION", "").strip()
        if not build:
            try:
                build = subprocess.run(
                    ["git", "rev-parse", "--short=12", "HEAD"],
                    cwd=python_root,
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=True,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                build = "unknown"
        now = datetime.now(timezone.utc)
        return {
            "version": app.version,
            "build": build,
            "git": build,
            "started_at": app.state.started_at.isoformat(timespec="seconds"),
            "uptime_s": round((now - app.state.started_at).total_seconds(), 3),
            "data_root": str(pipeline.data_root),
            "raw_root": str(pipeline.raw_root),
            "roots": {
                "server": str(pipeline.data_root),
                "raw": str(pipeline.raw_root),
                "processed": str(Path(
                    os.environ.get(
                        "TESIS_PROCESSED_ROOT",
                        Path(
                            os.environ.get(
                                "TESIS_DATA_ROOT",
                                Path(__file__).resolve().parents[4] / "data",
                            )
                        )
                        / "processed",
                    )
                ).resolve()),
            },
            "pid": os.getpid(),
            "read_only": app.state.read_only,
        }

    # Escanear el volumen tarda ~30 s en una campaña de ~950 capturas. Se hace
    # en un hilo al arrancar para que el primer pedido de la web no lo pague:
    # cuando el navegador llega, el cache ya está caliente. Es sólo lectura y
    # si falla no importa (el primer request lo vuelve a intentar).
    @app.on_event("startup")
    def _warm_cache() -> None:
        import threading

        def run() -> None:
            try:
                from .captures import build_all_campaigns
                build_all_campaigns(pipeline.raw_root, pipeline.data_root)
            except Exception:
                pass

        threading.Thread(target=run, name="warm-cache", daemon=True).start()

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    return app
