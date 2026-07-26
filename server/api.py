"""App FastAPI: routers, estáticos y DI del Pipeline.

No define un ``app`` a nivel de módulo a propósito: ``main()`` construye un solo
``Pipeline`` y lo pasa a ``create_app``. Si uvicorn recibiera el string
``"server.api:app"`` re-importaría este módulo y construiría un segundo
``Pipeline`` sobre el mismo ``jobs.json``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .pipeline import Pipeline

STATIC = Path(__file__).resolve().parent / "static"


def get_pipeline(request: Request) -> Pipeline:
    return request.app.state.pipeline


# Importados después de get_pipeline: los routers hacen `from ..api import
# get_pipeline` y, al estar este módulo en medio de su propia importación,
# necesitan encontrarlo ya definido.
from .routers import (admin, alignment, averages, dataset, filters,  # noqa: E402
                      grouping, ingest, masw, picks)


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


def create_app(pipeline: Pipeline) -> FastAPI:
    app = FastAPI(title="Servidor de datos Geophone", version="1")
    app.state.pipeline = pipeline

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
    app.include_router(masw.router)

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
