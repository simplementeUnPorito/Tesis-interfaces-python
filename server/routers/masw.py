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
from ..analysis_jobs import AnalysisJobs
from ..api import get_analysis_jobs, get_pipeline
from ..masw import (DEFAULTS, auto_pick_dispersion, backend_catalog,
                    build_dispersion, load_analysis_state,
                    save_analysis_state)
from ..pipeline import Pipeline
from ..state import RevisionConflict

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


@router.get("/api/masw/state")
def masw_state_get(
    campaign: str = Query(""),
    pipeline: Pipeline = Depends(get_pipeline),
):
    return load_analysis_state(_campaign_root(pipeline, campaign))


@router.post("/api/masw/state")
def masw_state_post(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    root = _campaign_root(pipeline, str(body.get("campaign", "")))
    patch = body.get("masw") if isinstance(body.get("masw"), dict) else body
    try:
        return save_analysis_state(
            root,
            patch,
            base_revision=str(body.get("base_revision", "")),
        )
    except RevisionConflict as exc:
        raise HTTPException(
            409, {"message": str(exc), "revision": exc.current}
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _weights(raw: object, fallback: int = 1) -> dict[int, float]:
    if not isinstance(raw, dict):
        return {max(1, int(fallback)): 1.0}
    try:
        parsed = {
            max(1, int(key)): max(0.0, float(value))
            for key, value in raw.items()
            if float(value) > 0
        }
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"pesos inválidos: {exc}") from exc
    return parsed or {max(1, int(fallback)): 1.0}


@router.post("/api/masw/dispersion")
def masw_dispersion_post(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    root = _campaign_root(pipeline, str(body.get("campaign", "")))
    params = body.get("params") if isinstance(body.get("params"), dict) else body
    try:
        payload = build_dispersion(
            root,
            group_id=int(body.get("group_id", 1) or 1),
            c_min=float(params.get("c_min", DEFAULTS["c_min"])),
            c_max=float(params.get("c_max", DEFAULTS["c_max"])),
            c_step=float(params.get("c_step", DEFAULTS["c_step"])),
            f_min=float(params.get("f_min", DEFAULTS["f_min"])),
            f_max=float(params.get("f_max", DEFAULTS["f_max"])),
            group_weights=_weights(
                body.get("group_weights"), int(body.get("group_id", 1) or 1)
            ),
            intensity_log=bool(body.get("intensity_log", False)),
            intensity_per_freq=bool(body.get("intensity_per_freq", True)),
            persist_groups=True,
            base_revision=str(body.get("base_revision", "")),
        )
    except RevisionConflict as exc:
        raise HTTPException(
            409, {"message": str(exc), "revision": exc.current}
        ) from exc
    except (TypeError, ValueError) as exc:
        # 409 queda reservado a revisión optimista. Una geometría vacía,
        # recorte sin muestras o campaña sin promedios es entrada no
        # procesable, no una escritura concurrente.
        raise HTTPException(422, str(exc)) from exc
    payload["state"] = load_analysis_state(root)
    return Response(
        content=json.dumps(payload, allow_nan=False, ensure_ascii=False),
        media_type="application/json",
    )


@router.post("/api/masw/auto-pick")
def masw_auto_pick(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    root = _campaign_root(pipeline, str(body.get("campaign", "")))
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    state = load_analysis_state(root)["masw"]
    regions = body.get("regions_by_mode", state.get("regions_by_mode", {"0": []}))
    try:
        picks = auto_pick_dispersion(
            root,
            group_weights=_weights(body.get("group_weights"), 1),
            regions_by_mode=regions,
            pick_fmin=float(body.get("pick_fmin", params.get("f_min", 1.0))),
            pick_fmax=float(body.get("pick_fmax", params.get("f_max", 100.0))),
            c_min=float(params.get("c_min", DEFAULTS["c_min"])),
            c_max=float(params.get("c_max", DEFAULTS["c_max"])),
            c_step=float(params.get("c_step", DEFAULTS["c_step"])),
            f_min=float(params.get("f_min", DEFAULTS["f_min"])),
            f_max=float(params.get("f_max", DEFAULTS["f_max"])),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"picks_by_mode": picks}


@router.post("/api/masw/inversions")
def masw_inversion_post(
    body: dict,
    pipeline: Pipeline = Depends(get_pipeline),
    analysis: AnalysisJobs = Depends(get_analysis_jobs),
):
    campaign = str(body.get("campaign", ""))
    root = _campaign_root(pipeline, campaign)
    bundle = load_analysis_state(root)
    stored = bundle["masw"]
    curves = body.get("curves_by_mode") or stored.get("picks_by_mode") or {}
    backend = str(body.get("backend") or stored.get("backend") or "maswavespy")
    known = {item["key"]: item for item in backend_catalog()}
    if backend not in known:
        raise HTTPException(400, f"backend desconocido: {backend}")
    if known[backend]["kind"] == "inproc" and not known[backend]["available"]:
        raise HTTPException(409, known[backend]["status"])
    base_revision = str(body.get("base_revision", ""))
    if known[backend]["kind"] == "inproc" and (
        not base_revision or base_revision != bundle["revision"]
    ):
        raise HTTPException(
            409,
            {
                "message": "el estado MASW cambió antes de encolar la inversión",
                "revision": bundle["revision"],
            },
        )
    return analysis.submit(
        "masw_inversion",
        campaign,
        {
            "raw_root": str(root),
            "backend": backend,
            "curves_by_mode": curves,
            "params": body.get("params") or stored.get("inversion_params") or {},
            "base_revision": base_revision,
            "launch": bool(body.get("launch", False)),
        },
    )
