"""Endpoints de la deconvolución Kalman **opcional**.

Ninguna ruta de acá es obligatoria para el resto de la app. Cuando el paquete
``kalman_deconv`` no está, los GET responden ``200`` con ``available: false`` en
vez de ``500``: la interfaz simplemente muestra la sección deshabilitada y todo
lo demás sigue funcionando igual.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import campaigns, kalman
from ..api import get_pipeline
from ..pipeline import Pipeline
from ..state import RevisionConflict

router = APIRouter()


def _campaign_root(pipeline: Pipeline, campaign: str) -> Path:
    if not campaign:
        return Path(pipeline.raw_root)
    if campaign not in campaigns.discover_campaign_ids(pipeline.raw_root):
        raise HTTPException(404, f"campaña desconocida: {campaign}")
    return campaigns.campaign_path(pipeline.raw_root, campaign)


@router.get("/api/kalman/catalog")
def kalman_catalog() -> dict:
    """Geófonos, acondicionadores y priors disponibles para poblar los combos."""
    return kalman.catalog()


@router.get("/api/kalman/settings")
def kalman_settings_get(
    campaign: str = Query(""), pipeline: Pipeline = Depends(get_pipeline)
) -> dict:
    return kalman.load_settings(_campaign_root(pipeline, campaign))


@router.post("/api/kalman/settings")
def kalman_settings_post(body: dict, pipeline: Pipeline = Depends(get_pipeline)) -> dict:
    root = _campaign_root(pipeline, str(body.get("campaign", "")))
    patch = {k: v for k, v in body.items() if k in kalman.DEFAULTS}
    try:
        return kalman.save_settings(
            root, patch, base_revision=str(body.get("base_revision", ""))
        )
    except RevisionConflict as exc:
        raise HTTPException(
            409, {"message": str(exc), "revision": exc.current}
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"ajuste inválido: {exc}") from exc


@router.get("/api/kalman/preview")
def kalman_preview(
    shot_id: str = Query(""),
    campaign: str = Query(""),
    max_points: int = Query(2000),
    geophone: str | None = Query(None),
    conditioner: str | None = Query(None),
    estimate: str | None = Query(None),
    input_model: str | None = Query(None),
    band_low_hz: float | None = Query(None),
    band_high_hz: float | None = Query(None),
    leak_hz: float | None = Query(None),
    disc_method: str | None = Query(None),
    q_source: str | None = Query(None),
    q_scale: float | None = Query(None),
    r_source: str | None = Query(None),
    r_var: float | None = Query(None),
    pre_arrival_s: float | None = Query(None),
    post_band_enabled: bool | None = Query(None),
    post_low_hz: float | None = Query(None),
    post_high_hz: float | None = Query(None),
    pipeline: Pipeline = Depends(get_pipeline),
):
    """Vista previa del estimador sobre **un** disparo.

    Handler síncrono a propósito, igual que ``/api/filter/preview``: corre el
    filtro sobre la señal entera y no puede ocupar el event loop.
    """
    if not kalman.AVAILABLE:
        raise HTTPException(
            503,
            "la deconvolución Kalman no está disponible en este entorno: "
            f"{kalman.UNAVAILABLE_REASON}",
        )
    if not shot_id:
        raise HTTPException(400, "falta shot_id")
    overrides = {
        key: value
        for key, value in {
            "geophone": geophone,
            "conditioner": conditioner,
            "estimate": estimate,
            "input_model": input_model,
            "band_low_hz": band_low_hz,
            "band_high_hz": band_high_hz,
            "leak_hz": leak_hz,
            "disc_method": disc_method,
            "q_source": q_source,
            "q_scale": q_scale,
            "r_source": r_source,
            "r_var": r_var,
            "pre_arrival_s": pre_arrival_s,
            "post_band_enabled": post_band_enabled,
            "post_low_hz": post_low_hz,
            "post_high_hz": post_high_hz,
        }.items()
        if value is not None
    }
    try:
        payload = kalman.build_preview(
            _campaign_root(pipeline, campaign),
            shot_id=shot_id,
            max_points=max(100, min(20000, max_points)),
            overrides=overrides,
        )
    except kalman.KalmanUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except kalman.UnknownShot as exc:
        raise HTTPException(404, f"shot_id desconocido: {exc}") from exc
    except (ValueError, KeyError) as exc:
        # Un modelo inexistente o una combinación impropia son pedidos malos,
        # no fallas del servidor.
        raise HTTPException(400, f"no se pudo estimar: {exc}") from exc
    body = json.dumps(payload, allow_nan=False, ensure_ascii=False)
    return Response(content=body, media_type="application/json")


@router.post("/api/kalman/masw-window")
def kalman_masw_window(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    """Ventana admisible del segundo Kalman, para superponer sobre la imagen.

    Devuelve una **envolvente**, no un picking: dice dónde puede estar la curva
    de dispersión, no dónde está.
    """
    if not kalman.AVAILABLE:
        raise HTTPException(
            503,
            "la deconvolución Kalman no está disponible en este entorno: "
            f"{kalman.UNAVAILABLE_REASON}",
        )
    root = _campaign_root(pipeline, str(body.get("campaign", "")))
    params = body.get("params") or {}
    raw_weights = body.get("group_weights") or {}
    weights: dict[int, float] = {}
    for key, value in raw_weights.items():
        try:
            weights[int(key)] = float(value)
        except (TypeError, ValueError):
            continue
    try:
        payload = kalman.build_masw_window(
            root,
            group_weights=weights or None,
            group_id=int(body.get("group_id", 1) or 1),
            c_min=float(params.get("c_min", 50.0)),
            c_max=float(params.get("c_max", 800.0)),
            c_step=float(params.get("c_step", 1.0)),
            f_min=float(params.get("f_min", 1.0)),
            f_max=float(params.get("f_max", 100.0)),
            window=body.get("window") or {},
        )
    except kalman.KalmanUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, f"no se pudo calcular la ventana: {exc}") from exc
    body_json = json.dumps(payload, allow_nan=False, ensure_ascii=False)
    return Response(content=body_json, media_type="application/json")
