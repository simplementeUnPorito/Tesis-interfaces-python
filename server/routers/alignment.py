"""Tab Enfase (§3.2): offsets por carpeta y rechazo de carpetas."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import campaigns
from ..alignment import (auto_align_label, load_alignment, reset_folder,
                         reset_label, set_offset, set_rejected, state_paths)
from ..api import get_pipeline
from ..pipeline import Pipeline
from ..state import (RevisionConflict, locked,
                     require_composite_revision)

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

    limpiados = 0
    auto_result = None
    try:
        # Una acción puede tocar offsets, rechazos y offsets por señal. Se
        # serializa como una unidad lógica y se compara una revisión conjunta.
        with locked(root / ".server_alignment_state"):
            require_composite_revision(
                state_paths(root), str(body.get("base_revision", ""))
            )
            if accion == "offset":
                if not folder:
                    raise HTTPException(400, "falta folder")
                limpiados = set_offset(root, label=label, folder=folder,
                                       offset_ms=float(body.get("offset_ms", 0.0)),
                                       group_id=group_id)
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
                limpiados = reset_label(root, label=label, group_id=group_id)
            elif accion == "auto_align":
                auto_result = auto_align_label(
                    root,
                    label=label,
                    group_id=group_id,
                    max_shift_ms=float(body.get("max_shift_ms", 100.0)),
                    min_score=float(body.get("min_score", 0.6)),
                    ambiguity_ratio=float(body.get("ambiguity_ratio", 0.9)),
                    ambiguity_separation_ms=float(
                        body.get("ambiguity_separation_ms", 8.0)
                    ),
                    window_start_s=float(body.get("window_start_s", -0.02)),
                    window_end_s=float(body.get("window_end_s", 0.35)),
                )
                limpiados = int(auto_result.get("legacy_cleared", 0))
            else:
                raise HTTPException(400, f"acción desconocida: {accion!r}")
    except RevisionConflict as exc:
        raise HTTPException(
            409, {"message": str(exc), "revision": exc.current}
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"valor inválido: {exc}") from exc

    payload = load_alignment(root, group_id=group_id, label=label,
                             max_points=int(body.get("max_points", 2000) or 2000))
    payload["legacy_cleared"] = int(limpiados)
    if auto_result is not None:
        payload["auto_align"] = auto_result
    return Response(content=json.dumps(payload, allow_nan=False, ensure_ascii=False),
                    media_type="application/json")
