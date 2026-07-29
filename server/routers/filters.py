"""Tab Filtros (§3.2): ajustes persistidos + vista previa del pasa-banda."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import campaigns
from ..api import get_pipeline
from ..filters import UnknownShot, build_preview, load_settings, save_settings
from ..pipeline import Pipeline
from ..state import RevisionConflict

router = APIRouter()


def _campaign_root(pipeline: Pipeline, campaign: str) -> Path:
    if not campaign:
        return Path(pipeline.raw_root)
    if campaign not in campaigns.discover_campaign_ids(pipeline.raw_root):
        raise HTTPException(404, f"campaña desconocida: {campaign}")
    return campaigns.campaign_path(pipeline.raw_root, campaign)


@router.get("/api/filter")
def filter_get(campaign: str = Query(""), pipeline: Pipeline = Depends(get_pipeline)):
    """Ajustes de filtro de la campaña. Vive en el mismo archivo que la app."""
    return load_settings(_campaign_root(pipeline, campaign))


@router.post("/api/filter")
def filter_post(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    """Guarda los ajustes. Sólo pisa lo que venga en el cuerpo."""
    root = _campaign_root(pipeline, str(body.get("campaign", "")))
    patch = {k: v for k, v in body.items()
             if k in (
                 "enabled", "low_hz", "high_hz", "order", "target_fs",
                 "dc_enabled", "line_suppress_enabled", "line_f0_hz",
                 "line_harmonics", "line_search_hz", "notes",
             )}
    try:
        return save_settings(
            root, patch, base_revision=str(body.get("base_revision", ""))
        )
    except RevisionConflict as exc:
        raise HTTPException(
            409,
            {
                "message": str(exc),
                "revision": exc.current,
            },
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"ajuste inválido: {exc}") from exc


@router.get("/api/filter/preview")
def filter_preview(
    shot_id: str = Query(""),
    campaign: str = Query(""),
    max_points: int = Query(2000),
    low_hz: float | None = Query(None),
    high_hz: float | None = Query(None),
    order: int | None = Query(None),
    target_fs: float | None = Query(None),
    dc_enabled: bool | None = Query(None),
    line_suppress_enabled: bool | None = Query(None),
    line_f0_hz: float | None = Query(None),
    line_harmonics: int | None = Query(None),
    line_search_hz: float | None = Query(None),
    include_envelope: bool = Query(False),
    pipeline: Pipeline = Depends(get_pipeline),
):
    """Original vs filtrada del geófono, en tiempo y espectro.

    Handler síncrono a propósito: lee la señal entera y filtra, así que no puede
    correr en el event loop (§4.6).
    """
    if not shot_id:
        raise HTTPException(400, "falta shot_id")
    max_points = max(100, min(20000, max_points))
    try:
        payload = build_preview(
            _campaign_root(pipeline, campaign),
            shot_id=shot_id, max_points=max_points,
            low_hz=low_hz, high_hz=high_hz, order=order, target_fs=target_fs,
            dc_enabled=dc_enabled,
            line_suppress_enabled=line_suppress_enabled,
            line_f0_hz=line_f0_hz,
            line_harmonics=line_harmonics,
            line_search_hz=line_search_hz,
            include_envelope=include_envelope,
        )
    except UnknownShot as exc:
        raise HTTPException(404, f"shot_id desconocido: {exc}") from exc
    except ValueError as exc:
        # Un corte por encima de Nyquist, u orden imposible: es un pedido malo,
        # no un fallo del servidor.
        raise HTTPException(400, f"no se pudo aplicar el filtro: {exc}") from exc
    body = json.dumps(payload, allow_nan=False, ensure_ascii=False)
    return Response(content=body, media_type="application/json")
