"""Tab MASW (§3.5). Por ahora la etapa 1: la imagen de dispersión.

Handler síncrono (§4.6): el phase-shift es un slant-stack sobre todo el
registro y no puede correr en el event loop.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import campaigns
from ..api import get_pipeline
from ..masw import DEFAULTS, build_dispersion
from ..pipeline import Pipeline

router = APIRouter()


def _campaign_root(pipeline: Pipeline, campaign: str) -> Path:
    if not campaign:
        return Path(pipeline.raw_root)
    if campaign not in campaigns.discover_campaign_ids(pipeline.raw_root):
        raise HTTPException(404, f"campaña desconocida: {campaign}")
    return campaigns.campaign_path(pipeline.raw_root, campaign)


@router.get("/api/masw/dispersion")
def masw_dispersion(
    campaign: str = Query(""),
    group_id: int = Query(1),
    c_min: float = Query(DEFAULTS["c_min"]),
    c_max: float = Query(DEFAULTS["c_max"]),
    c_step: float = Query(DEFAULTS["c_step"]),
    f_min: float = Query(DEFAULTS["f_min"]),
    f_max: float = Query(DEFAULTS["f_max"]),
    pipeline: Pipeline = Depends(get_pipeline),
):
    if c_step <= 0 or c_max <= c_min or f_max <= f_min:
        raise HTTPException(400, "rango inválido: hace falta c_step>0, c_max>c_min, f_max>f_min")
    try:
        payload = build_dispersion(
            _campaign_root(pipeline, campaign), group_id=group_id,
            c_min=c_min, c_max=c_max, c_step=c_step, f_min=f_min, f_max=f_max)
    except ValueError as exc:
        # Recorte vacío o pocos receptores: es una condición del dato, no un
        # error del servidor, y la web la muestra tal cual.
        raise HTTPException(409, str(exc)) from exc
    return Response(content=json.dumps(payload, allow_nan=False, ensure_ascii=False),
                    media_type="application/json")
