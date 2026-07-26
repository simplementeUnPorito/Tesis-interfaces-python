"""Tab Promedios / arrivals (§3.3)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import campaigns
from ..api import get_pipeline
from ..averages import build_averages, save_arrival
from ..pipeline import Pipeline

router = APIRouter()


def _campaign_root(pipeline: Pipeline, campaign: str) -> Path:
    if not campaign:
        return Path(pipeline.raw_root)
    if campaign not in campaigns.discover_campaign_ids(pipeline.raw_root):
        raise HTTPException(404, f"campaña desconocida: {campaign}")
    return campaigns.campaign_path(pipeline.raw_root, campaign)


@router.get("/api/averages")
def averages_get(
    campaign: str = Query(""),
    max_points: int = Query(2000),
    kind: str = Query("raw"),
    group_id: int = Query(1),
    pipeline: Pipeline = Depends(get_pipeline),
):
    """Promedios por distancia. Handler síncrono: promediar lee todo el grupo."""
    payload = build_averages(
        _campaign_root(pipeline, campaign),
        max_points=max(100, min(20000, max_points)),
        prefer_filtered=(kind == "filt"),
        group_id=group_id,
    )
    return Response(content=json.dumps(payload, allow_nan=False, ensure_ascii=False),
                    media_type="application/json")


@router.post("/api/averages/arrival")
def averages_arrival(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    """Anota el primer arribo del promedio de un label."""
    label = str(body.get("label", ""))
    if not label:
        raise HTTPException(400, "falta label")
    try:
        return save_arrival(
            _campaign_root(pipeline, str(body.get("campaign", ""))),
            label=label,
            distance_m=float(body.get("distance_m", 0.0)),
            arrival_s=None if body.get("arrival_s") is None else float(body["arrival_s"]),
            reviewed=None if body.get("reviewed") is None else bool(body["reviewed"]),
            notes=None if body.get("notes") is None else str(body["notes"]),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"valor inválido: {exc}") from exc
