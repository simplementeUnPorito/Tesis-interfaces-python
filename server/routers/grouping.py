"""Tab Agrupamiento (§3.3): grupos de dispersión por carpeta."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import campaigns
from ..api import get_pipeline
from ..grouping import load_groups, save_groups
from ..pipeline import Pipeline

router = APIRouter()


def _campaign_root(pipeline: Pipeline, campaign: str) -> Path:
    if not campaign:
        return Path(pipeline.raw_root)
    if campaign not in campaigns.discover_campaign_ids(pipeline.raw_root):
        raise HTTPException(404, f"campaña desconocida: {campaign}")
    return campaigns.campaign_path(pipeline.raw_root, campaign)


@router.get("/api/groups")
def groups_get(campaign: str = Query(""), pipeline: Pipeline = Depends(get_pipeline)):
    """Carpetas de la campaña con su grupo, capturas, distancias y validadas."""
    return load_groups(_campaign_root(pipeline, campaign))


@router.post("/api/groups")
def groups_post(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    """Cambia la cantidad de grupos y/o asigna carpetas.

    ``assign`` es ``{carpeta: grupo}``; sólo toca las que vengan.
    """
    root = _campaign_root(pipeline, str(body.get("campaign", "")))
    assign = body.get("assign") or {}
    if not isinstance(assign, dict):
        raise HTTPException(400, "assign tiene que ser un objeto {carpeta: grupo}")
    try:
        return save_groups(
            root,
            group_count=None if body.get("group_count") is None else int(body["group_count"]),
            assign={str(k): int(v) for k, v in assign.items()},
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"valor inválido: {exc}") from exc
