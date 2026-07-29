"""POST /ingest — recibe el ZIP de la SPA (lo manda el navegador, no el ESP)."""

from __future__ import annotations

import hashlib
import os
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from ..limits import MAX_UPLOAD_BYTES
from ..pipeline import Pipeline
from ..api import get_pipeline

router = APIRouter()


@router.post("/ingest")
async def ingest(request: Request, pipeline: Pipeline = Depends(get_pipeline)):
    try:
        length = int(request.headers.get("Content-Length") or 0)
    except ValueError:
        return PlainTextResponse("Content-Length inválido\n", status_code=400)
    if length < 0:
        return PlainTextResponse("Content-Length inválido\n", status_code=400)
    if length > MAX_UPLOAD_BYTES:
        return PlainTextResponse(f"demasiado grande: {length} B\n", status_code=413)

    temp = pipeline.incoming_root / (
        f"upload-{os.getpid()}-{uuid.uuid4().hex}.tmp"
    )
    received = 0
    digest = hashlib.sha256()
    prefix = b""
    try:
        with temp.open("wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                received += len(chunk)
                if received > MAX_UPLOAD_BYTES:
                    return PlainTextResponse(
                        f"demasiado grande: más de {MAX_UPLOAD_BYTES} B\n",
                        status_code=413,
                    )
                if len(prefix) < 4:
                    prefix += chunk[: 4 - len(prefix)]
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if received <= 0:
            return PlainTextResponse("cuerpo vacío\n", status_code=400)
        if length and received != length:
            return PlainTextResponse(
                f"recibido incompleto: {received}/{length} B\n", status_code=400
            )
        if prefix[:2] != b"PK":
            return PlainTextResponse("no parece un ZIP\n", status_code=415)

        name = (request.headers.get("X-Geo-Filename") or "captura.zip").strip()
        try:
            job = await run_in_threadpool(
                pipeline.submit_zip_file,
                temp,
                name,
                digest=digest.hexdigest(),
                size=received,
            )
        except ValueError as exc:
            return PlainTextResponse(f"ZIP rechazado: {exc}\n", status_code=400)
    finally:
        temp.unlink(missing_ok=True)

    host = request.client.host if request.client else "?"
    print(f"[ingest] {host} -> {job.job_id} ({received} B) encolado", flush=True)
    return PlainTextResponse(
        f"OK {job.job_id} encolado ({received} B)\n",
        headers={"X-Geo-Job": job.job_id},
    )
