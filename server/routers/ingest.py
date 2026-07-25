"""POST /ingest — recibe el ZIP de la SPA (lo manda el navegador, no el ESP)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from ..pipeline import Pipeline
from ..api import get_pipeline

MAX_UPLOAD = 256 * 1024 * 1024

router = APIRouter()


@router.post("/ingest")
async def ingest(request: Request, pipeline: Pipeline = Depends(get_pipeline)):
    try:
        length = int(request.headers.get("Content-Length") or 0)
    except ValueError:
        return PlainTextResponse("Content-Length inválido\n", status_code=400)
    if length <= 0:
        return PlainTextResponse("cuerpo vacío\n", status_code=400)
    if length > MAX_UPLOAD:
        return PlainTextResponse(f"demasiado grande: {length} B\n", status_code=413)

    body = await request.body()
    if len(body) != length:
        return PlainTextResponse(f"recibido incompleto: {len(body)}/{length} B\n",
                                  status_code=500)
    if body[:2] != b"PK":
        return PlainTextResponse("no parece un ZIP\n", status_code=415)

    name = (request.headers.get("X-Geo-Filename") or "captura.zip").strip()
    job = await run_in_threadpool(pipeline.submit_zip, body, name)
    host = request.client.host if request.client else "?"
    print(f"[ingest] {host} -> {job.job_id} ({len(body)} B) encolado", flush=True)
    return PlainTextResponse(f"OK {job.job_id} encolado ({len(body)} B)\n")
