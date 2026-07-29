"""Rutas heredadas de administración.

Las dos rutas que históricamente borraban se conservan por compatibilidad, pero
ahora sólo aplican la cuarentena reversible. El parámetro ``zip`` se ignora de
forma deliberada: ni el raw ni el ZIP pueden eliminarse desde la API.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from .. import campaigns
from ..deletion import (
    build_rows,
    consume_preview,
    preview_deactivation,
    record_deactivation,
    set_folders_disabled,
)
from ..pipeline import Pipeline
from ..api import get_pipeline
from ..state import RevisionConflict

router = APIRouter()


@router.post("/api/delete")
def delete(
    folder: str = "",
    zip: int = 0,
    token: str = "",
    pipeline: Pipeline = Depends(get_pipeline),
):
    """Compatibilidad de ``/api/delete`` como desactivación obligatoriamente confirmada.

    La primera llamada devuelve ``409`` y una preview. Repetirla con el token
    exacto aplica la bandera; nunca se elimina ningún archivo.
    """
    key = f"{campaigns.ROOT_ID}|{folder}"
    if not token:
        try:
            preview = preview_deactivation(
                pipeline.raw_root,
                pipeline.data_root,
                [key],
                action="disable",
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(
            {"confirmation_required": True, **preview},
            status_code=409,
        )
    try:
        expected = consume_preview(token, [key], "disable")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        outcome = set_folders_disabled(
            pipeline,
            [key],
            disabled=True,
            expected_revisions=expected,
        )
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    record_deactivation(pipeline.data_root, outcome, [key])
    return JSONResponse(outcome)


@router.post("/api/delete-sin-hammer")
def delete_sin_hammer(
    zip: int = 0,
    token: str = "",
    pipeline: Pipeline = Depends(get_pipeline),
):
    """Barrido conservador legado, ahora también en dos pasos."""
    catalog = build_rows(pipeline.raw_root, pipeline.data_root)
    keys = [
        row["key"]
        for row in catalog["rows"]
        if "sin_martillo" in row.get("flags", ())
        and "desactivada" not in row.get("flags", ())
    ]
    if not keys:
        return JSONResponse(
            {
                "confirmation_required": False,
                "count": 0,
                "disabled": [],
                "deleted": [],
                "errors": [],
                "zip_preserved": True,
                "physical_deletion": False,
            }
        )
    if not token:
        preview = preview_deactivation(
            pipeline.raw_root,
            pipeline.data_root,
            keys,
            action="disable",
        )
        return JSONResponse(
            {"confirmation_required": True, **preview},
            status_code=409,
        )
    try:
        expected = consume_preview(token, keys, "disable")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        outcome = set_folders_disabled(
            pipeline,
            keys,
            disabled=True,
            expected_revisions=expected,
        )
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    record_deactivation(pipeline.data_root, outcome, keys)
    return JSONResponse(outcome)


@router.post("/api/requeue")
def requeue(job_id: str = "", pipeline: Pipeline = Depends(get_pipeline)):
    ok = pipeline.requeue(job_id)
    return PlainTextResponse("reencolado\n" if ok else "no existe\n",
                              status_code=200 if ok else 404)
