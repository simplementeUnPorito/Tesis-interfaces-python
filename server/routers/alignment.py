"""Tab Enfase (§3.2): offsets por carpeta y rechazo de carpetas."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import campaigns
from ..alignment import (load_alignment, reset_folder, reset_label, set_offset,
                         set_rejected)
from ..api import get_pipeline
from ..pipeline import Pipeline

router = APIRouter()


def _campaign_root(pipeline: Pipeline, campaign: str) -> Path:
    if not campaign:
        return Path(pipeline.raw_root)
    if campaign not in campaigns.discover_campaign_ids(pipeline.raw_root):
        raise HTTPException(404, f"campaña desconocida: {campaign}")
    return campaigns.campaign_path(pipeline.raw_root, campaign)


@router.get("/api/alignment")
def alignment_get(
    campaign: str = Query(""),
    group_id: int = Query(1),
    label: str = Query(""),
    max_points: int = Query(2000),
    pipeline: Pipeline = Depends(get_pipeline),
):
    """Carpetas del label con su promedio, offset y si está rechazada.

    Handler síncrono: promediar una carpeta lee todas sus señales (§4.6).
    """
    max_points = max(100, min(20000, max_points))
    payload = load_alignment(
        _campaign_root(pipeline, campaign),
        group_id=max(1, int(group_id)),
        label=label or None,
        max_points=max_points,
    )
    body = json.dumps(payload, allow_nan=False, ensure_ascii=False)
    return Response(content=body, media_type="application/json")


@router.post("/api/alignment")
def alignment_post(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    """Acciones de Enfase: ``offset``, ``reject``, ``reset_folder``, ``reset_label``."""
    root = _campaign_root(pipeline, str(body.get("campaign", "")))
    accion = str(body.get("action", ""))
    label = str(body.get("label", ""))
    folder = str(body.get("folder", ""))
    group_id = max(1, int(body.get("group_id", 1) or 1))
    if not label:
        raise HTTPException(400, "falta label")

    try:
        if accion == "offset":
            if not folder:
                raise HTTPException(400, "falta folder")
            set_offset(root, label=label, folder=folder,
                       offset_ms=float(body.get("offset_ms", 0.0)))
        elif accion == "reject":
            if not folder:
                raise HTTPException(400, "falta folder")
            set_rejected(root, label=label, folder=folder,
                         rejected=bool(body.get("rejected")), group_id=group_id)
        elif accion == "reset_folder":
            if not folder:
                raise HTTPException(400, "falta folder")
            reset_folder(root, label=label, folder=folder)
        elif accion == "reset_label":
            reset_label(root, label=label)
        else:
            raise HTTPException(400, f"acción desconocida: {accion!r}")
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"valor inválido: {exc}") from exc

    payload = load_alignment(root, group_id=group_id, label=label,
                             max_points=int(body.get("max_points", 2000) or 2000))
    return Response(content=json.dumps(payload, allow_nan=False, ensure_ascii=False),
                    media_type="application/json")
