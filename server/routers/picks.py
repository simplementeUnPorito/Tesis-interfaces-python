"""Anotaciones: escribir el pick de un disparo (§3.1 del PORT_PLAN)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from .. import campaigns
from ..api import get_pipeline
from ..pipeline import Pipeline
from ..picks import UnknownShot, save_pick

router = APIRouter()


def _campaign_root(pipeline: Pipeline, campaign: str) -> Path:
    if not campaign:
        return Path(pipeline.raw_root)
    if campaign not in campaigns.discover_campaign_ids(pipeline.raw_root):
        raise HTTPException(404, f"campaña desconocida: {campaign}")
    return campaigns.campaign_path(pipeline.raw_root, campaign)


def _opt_float(body: dict, key: str) -> float | None:
    value = body.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{key} no es un número: {value!r}")


def _opt_bool(body: dict, key: str) -> bool | None:
    value = body.get(key)
    return None if value is None else bool(value)


@router.post("/api/pick")
def pick_route(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    """Guarda la marca de un disparo en el mismo archivo que la app PyQt.

    Sólo se escriben los campos que vengan en el cuerpo: mandar `{shot_id,
    reviewed}` no pisa la distancia ni las notas.
    """
    shot_id = str(body.get("shot_id", ""))
    if not shot_id:
        raise HTTPException(400, "falta shot_id")
    try:
        return save_pick(
            _campaign_root(pipeline, str(body.get("campaign", ""))),
            shot_id=shot_id,
            trigger_s=_opt_float(body, "trigger_s"),
            accepted=_opt_bool(body, "accepted"),
            reviewed=_opt_bool(body, "reviewed"),
            geo_flip=_opt_bool(body, "geo_flip"),
            distance_m=_opt_float(body, "distance_m"),
            notes=None if body.get("notes") is None else str(body["notes"]),
            apply_distance_to_folder=bool(body.get("apply_distance_to_folder")),
            flip_folder=bool(body.get("flip_folder")),
        )
    except UnknownShot as exc:
        raise HTTPException(404, f"shot_id desconocido: {exc}") from exc
