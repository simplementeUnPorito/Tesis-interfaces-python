"""Tab Waterfall (§3.4).

Todos los handlers son síncronos a propósito (§4.6): armar el waterfall lee los
promedios del grupo entero y no puede correr en el event loop.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import campaigns
from ..api import get_pipeline
from ..pipeline import Pipeline
from ..waterfall import (KFILTER_MODES, auto_polarity, build_waterfall,
                         flip_distance, save_view)
from ..state import RevisionConflict

router = APIRouter()


def _campaign_root(pipeline: Pipeline, campaign: str) -> Path:
    if not campaign:
        return Path(pipeline.raw_root)
    if campaign not in campaigns.discover_campaign_ids(pipeline.raw_root):
        raise HTTPException(404, f"campaña desconocida: {campaign}")
    return campaigns.campaign_path(pipeline.raw_root, campaign)


def _json(payload: dict) -> Response:
    return Response(content=json.dumps(payload, allow_nan=False, ensure_ascii=False),
                    media_type="application/json")


@router.get("/api/waterfall")
def waterfall_get(
    campaign: str = Query(""),
    group_id: int = Query(1),
    max_points: int = Query(1400),
    pipeline: Pipeline = Depends(get_pipeline),
):
    """El waterfall de un grupo con los ajustes de vista guardados."""
    return _json(build_waterfall(
        _campaign_root(pipeline, campaign),
        group_id=group_id,
        max_points=max(100, min(20000, max_points)),
    ))


@router.post("/api/waterfall/view")
def waterfall_view(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    """Guarda el encuadre (recorte, trazas ocultas, escala, f-k) y devuelve el
    waterfall ya redibujado con él. Es sólo vista: no toca las anotaciones."""
    raw_root = _campaign_root(pipeline, str(body.get("campaign", "")))
    group_id = int(body.get("group_id", 1) or 1)
    modo = str(body.get("kfilter_mode", "")).strip().lower()
    if modo and modo not in KFILTER_MODES:
        raise HTTPException(400, f"kfilter_mode inválido: {modo}")
    patch = {k: body[k] for k in
             ("trim_enabled", "trim_start", "trim_end", "raw_amplitude",
              "wiggle", "kfilter_mode", "hidden_distances") if k in body}
    try:
        save_view(
            raw_root,
            group_id,
            patch,
            base_revision=str(body.get("base_revision", "")),
        )
    except RevisionConflict as exc:
        raise HTTPException(
            409, {"message": str(exc), "revision": exc.current}
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"valor inválido: {exc}") from exc
    return _json(build_waterfall(
        raw_root, group_id=group_id,
        max_points=max(100, min(20000, int(body.get("max_points", 1400) or 1400))),
    ))


@router.post("/api/waterfall/auto_polarity")
def waterfall_auto_polarity(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    """«Auto polaridad»: las dos etapas de ``auto_align_polarity``. Persiste."""
    raw_root = _campaign_root(pipeline, str(body.get("campaign", "")))
    try:
        reporte = auto_polarity(
            raw_root, base_revision=str(body.get("base_revision", ""))
        )
    except RevisionConflict as exc:
        raise HTTPException(
            409, {"message": str(exc), "revision": exc.current}
        ) from exc
    payload = build_waterfall(
        raw_root, group_id=int(body.get("group_id", 1) or 1),
        max_points=max(100, min(20000, int(body.get("max_points", 1400) or 1400))),
    )
    payload["auto_polarity"] = reporte
    return _json(payload)


@router.post("/api/waterfall/flip")
def waterfall_flip(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    """«Invertir traza»: NO es de vista. Togglea ``geo_flip`` en todas las
    capturas de esa distancia y lo escribe en las anotaciones, así que llega a
    promedios, MASW y export."""
    raw_root = _campaign_root(pipeline, str(body.get("campaign", "")))
    if body.get("distance_m") is None:
        raise HTTPException(400, "falta distance_m")
    try:
        resultado = flip_distance(
            raw_root,
            float(body["distance_m"]),
            base_revision=str(body.get("base_revision", "")),
        )
    except RevisionConflict as exc:
        raise HTTPException(
            409, {"message": str(exc), "revision": exc.current}
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"distance_m inválida: {exc}") from exc
    payload = build_waterfall(
        raw_root, group_id=int(body.get("group_id", 1) or 1),
        max_points=max(100, min(20000, int(body.get("max_points", 1400) or 1400))),
    )
    payload["flip"] = resultado
    return _json(payload)
