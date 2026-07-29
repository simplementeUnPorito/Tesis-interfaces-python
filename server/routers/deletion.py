"""Cuarentena reversible con banderas y subgrupos (PORT_PLAN §4).

Handlers síncronos: armar la tabla escanea el volumen y cambiar la bandera
compartida toma locks por campaña; nada de eso va en el event loop.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..api import get_pipeline
from ..deletion import (build_rows, consume_preview, preview_deactivation,
                        record_deactivation, set_folders_disabled)
from ..pipeline import Pipeline
from ..state import RevisionConflict

router = APIRouter()


@router.get("/api/deletion")
def deletion_get(pipeline: Pipeline = Depends(get_pipeline)):
    """Carpetas con sus banderas y conteos por bandera."""
    payload = build_rows(pipeline.raw_root, pipeline.data_root)
    return Response(content=json.dumps(payload, allow_nan=False, ensure_ascii=False),
                    media_type="application/json")


@router.post("/api/deletion/delete")
def deletion_delete(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    """Nombre heredado: desactiva carpetas, nunca elimina datos ni ZIP."""
    return _apply(body, pipeline, action="disable")


@router.post("/api/deletion/disable")
def deletion_disable(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    return _apply(body, pipeline, action="disable")


@router.post("/api/deletion/restore")
def deletion_restore(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    return _apply(body, pipeline, action="restore")


def _apply(body: dict, pipeline: Pipeline, *, action: str):
    keys = body.get("keys")
    if not isinstance(keys, list) or not keys:
        raise HTTPException(400, "hace falta keys: una lista de 'campaña|carpeta'")
    if len(keys) > 500:
        raise HTTPException(400, f"demasiadas carpetas de una vez ({len(keys)}); máximo 500")
    try:
        expected = consume_preview(str(body.get("token", "")), keys, action)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        resultado = set_folders_disabled(
            pipeline,
            [str(k) for k in keys],
            disabled=action == "disable",
            expected_revisions=expected,
        )
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    record_deactivation(pipeline.data_root, resultado, [str(k) for k in keys])
    return Response(content=json.dumps(resultado, ensure_ascii=False),
                    media_type="application/json")


@router.post("/api/deletion/preview")
def deletion_preview(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    keys = body.get("keys")
    if not isinstance(keys, list) or not keys:
        raise HTTPException(400, "hace falta keys")
    if len(keys) > 500:
        raise HTTPException(400, "máximo 500 carpetas")
    try:
        payload = preview_deactivation(
            pipeline.raw_root,
            pipeline.data_root,
            [str(key) for key in keys],
            action=str(body.get("action", "disable")),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(
        content=json.dumps(payload, allow_nan=False, ensure_ascii=False),
        media_type="application/json",
    )
