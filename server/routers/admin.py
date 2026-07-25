"""Rutas de escritura: borrado explícito y reencolado."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse

from ..pipeline import Pipeline
from ..catalog import folders_without_hammer
from ..api import get_pipeline

router = APIRouter()


@router.post("/api/delete")
def delete(folder: str = "", zip: int = 0, pipeline: Pipeline = Depends(get_pipeline)):
    # Borrado explícito de una carpeta. Nada se borra solo: ni por estar
    # incompleta, ni por antigüedad. El ZIP se conserva salvo zip=1.
    res = pipeline.delete_folder(folder, with_zip=bool(zip))
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@router.post("/api/delete-sin-hammer")
def delete_sin_hammer(zip: int = 0, pipeline: Pipeline = Depends(get_pipeline)):
    # Barrido de las capturas sin martillo, pedido a mano. Conservador: sólo
    # carpetas donde NINGUNA captura tiene martillo.
    with_zip = bool(zip)
    borradas, errores = [], []
    for name in folders_without_hammer(pipeline.raw_root):
        res = pipeline.delete_folder(name, with_zip=with_zip)
        (borradas if res.get("ok") else errores).append(res.get("folder", name))
    return JSONResponse({"ok": True, "deleted": borradas, "errors": errores})


@router.post("/api/requeue")
def requeue(job_id: str = "", pipeline: Pipeline = Depends(get_pipeline)):
    ok = pipeline.requeue(job_id)
    return PlainTextResponse("reencolado\n" if ok else "no existe\n",
                              status_code=200 if ok else 404)
