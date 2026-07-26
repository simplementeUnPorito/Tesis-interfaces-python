"""Borrado con banderas y subgrupos (PORT_PLAN §4).

Handlers síncronos (§4.6): armar la tabla escanea el volumen y borrar toca el
disco; nada de eso va en el event loop.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..api import get_pipeline
from ..deletion import build_rows, delete_folders
from ..pipeline import Pipeline

router = APIRouter()


@router.get("/api/deletion")
def deletion_get(pipeline: Pipeline = Depends(get_pipeline)):
    """Carpetas con sus banderas y conteos por bandera."""
    payload = build_rows(pipeline.raw_root, pipeline.data_root)
    return Response(content=json.dumps(payload, allow_nan=False, ensure_ascii=False),
                    media_type="application/json")


@router.post("/api/deletion/delete")
def deletion_delete(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    """Borra las carpetas seleccionadas. Sólo por pedido explícito.

    El ZIP original se conserva salvo que venga ``with_zip``. El trabajo queda
    marcado ``borrado`` en el historial, no se elimina.
    """
    keys = body.get("keys")
    if not isinstance(keys, list) or not keys:
        raise HTTPException(400, "hace falta keys: una lista de 'campaña|carpeta'")
    if len(keys) > 500:
        raise HTTPException(400, f"demasiadas carpetas de una vez ({len(keys)}); máximo 500")
    resultado = delete_folders(pipeline, [str(k) for k in keys],
                               with_zip=bool(body.get("with_zip")))
    return Response(content=json.dumps(resultado, ensure_ascii=False),
                    media_type="application/json")
