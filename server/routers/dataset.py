"""Rutas de lectura: catálogo, capturas, cola de trabajos."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response

from ..pipeline import Pipeline, frd
from .. import campaigns
from ..catalog import scan_catalog
from ..datacache import get_dataset
from ..captures import build_all_campaigns, peak_to_peak_map
from ..overlays import build_overlays
from ..signal_view import InvalidKind, UnknownShot, build_signal_payload
from ..api import get_pipeline

router = APIRouter()


def dataset_summary(pipeline: Pipeline) -> dict:
    """Catálogo de todo lo que llegó, con los picks encima cuando existen.

    El catálogo manda: lista cada captura y cada nodo con señal, tenga martillo o
    no. Los disparos MASW (que sí exigen el par hammer+geo) se superponen sobre esa
    base. Antes esto usaba sólo discover_dataset y una captura de un nodo suelto
    desaparecía de la vista, que es justo lo que no tiene que pasar.

    Se recalcula por pedido en vez de cachearse: leer metadata es barato y así la
    web nunca muestra un estado viejo si alguien toca el volumen por fuera (la app
    PyQt, por ejemplo).
    """
    cat = scan_catalog(pipeline.raw_root)

    # Índice de picks por (carpeta, captura) para colgarlos del catálogo.
    picks_por_captura: dict[tuple[str, str], dict] = {}
    shot_count = 0
    reviewed = 0
    try:
        dataset = get_dataset(pipeline.raw_root)
        anns = frd.load_annotations(frd.default_annotations_path(pipeline.raw_root))
        shot_count = len(dataset.shots)
        reviewed = sum(1 for a in anns.values() if a.reviewed)
        for shot in dataset.shots:
            ann = anns.get(shot.shot_id)
            picks_por_captura[(shot.folder_name, shot.capture_name)] = {
                "shot_id": shot.shot_id,
                "distance_m": shot.distance_m,
                "trigger_s": None if ann is None else ann.trigger_s,
                "source": None if ann is None else ann.source,
                "reviewed": bool(ann and ann.reviewed),
                "accepted": bool(ann and ann.accepted),
            }
    except FileNotFoundError:
        pass   # todavía no llegó nada; el catálogo ya viene vacío

    for folder in cat["folders"]:
        for capture in folder["captures"]:
            capture["pick"] = picks_por_captura.get((folder["folder"], capture["capture"]))

    cat["shot_count"] = shot_count
    cat["reviewed_count"] = reviewed
    return cat


@router.get("/health")
def health():
    return PlainTextResponse("ok\n")


@router.get("/api/jobs")
def jobs(pipeline: Pipeline = Depends(get_pipeline)):
    return {"jobs": pipeline.jobs()}


@router.get("/api/dataset")
def dataset_route(pipeline: Pipeline = Depends(get_pipeline)):
    return dataset_summary(pipeline)


def _campaign_root(pipeline: Pipeline, campaign: str) -> Path:
    """Raíz de datos de la campaña pedida. Vacío = la raíz entera (compatible
    con los clientes que todavía no mandan `campaign`)."""
    if not campaign:
        return Path(pipeline.raw_root)
    ids = campaigns.discover_campaign_ids(pipeline.raw_root)
    if campaign not in ids:
        raise HTTPException(404, f"campaña desconocida: {campaign}")
    return campaigns.campaign_path(pipeline.raw_root, campaign)


@router.get("/api/campaigns")
def campaigns_route(pipeline: Pipeline = Depends(get_pipeline)):
    """Qué campañas hay bajo raw_root, con su nombre, si se usan y su metadata."""
    return {"campaigns": campaigns.list_campaigns(pipeline.raw_root, pipeline.data_root),
            "raw_root": str(pipeline.raw_root)}


@router.post("/api/campaigns")
def campaigns_update_route(body: dict, pipeline: Pipeline = Depends(get_pipeline)):
    """Renombrar una campaña y/o habilitarla. Queda guardado en campaigns.json."""
    campaign_id = str(body.get("id", ""))
    if not campaign_id:
        raise HTTPException(400, "falta id")
    if campaign_id not in campaigns.discover_campaign_ids(pipeline.raw_root):
        raise HTTPException(404, f"campaña desconocida: {campaign_id}")
    name = body.get("name")
    enabled = body.get("enabled")
    campaigns.update_campaign(
        pipeline.data_root, campaign_id,
        name=None if name is None else str(name),
        enabled=None if enabled is None else bool(enabled),
    )
    return {"ok": True,
            "campaign": campaigns.campaign_info(pipeline.raw_root, pipeline.data_root, campaign_id)}


@router.get("/api/captures")
def captures_route(pipeline: Pipeline = Depends(get_pipeline)):
    """Filas de la tabla de Capturas, con las columnas de la app PyQt.

    Une todas las campañas habilitadas: cada fila dice de cuál viene y su clave
    la incluye. Cada campaña se escanea con su propia raíz, así que sus
    anotaciones son las que ya existen (no se migra ni se reescribe nada).
    """
    return build_all_campaigns(pipeline.raw_root, pipeline.data_root)


@router.get("/api/captures/p2p")
def captures_p2p_route(pipeline: Pipeline = Depends(get_pipeline)):
    """``{key: pico a pico del geo}`` para el orden "Pico a pico".

    Va aparte de ``/api/captures`` porque exige leer el geo entero de cada
    captura: así la tabla pinta al instante y el orden se aplica cuando llega.
    """
    return {"p2p": peak_to_peak_map(pipeline.raw_root, pipeline.data_root)}


@router.get("/api/autopick")
def autopick_route(
    shot_id: str = Query(""),
    campaign: str = Query(""),
    kind: str = Query("raw"),
    zone_a: float | None = Query(None),
    zone_b: float | None = Query(None),
    pipeline: Pipeline = Depends(get_pipeline),
):
    """Re-corre el picking automático (botón «Auto» de la app, :1674).

    Con ``zone_a``/``zone_b`` busca sólo dentro de esa ventana: es la "zona
    auto" que se marca con dos clicks sobre el hammer. No escribe nada.
    """
    if not shot_id:
        raise HTTPException(400, "falta shot_id")
    dataset = get_dataset(_campaign_root(pipeline, campaign))
    shot = next((s for s in dataset.shots if s.shot_id == shot_id), None)
    if shot is None:
        raise HTTPException(404, f"shot_id desconocido: {shot_id}")
    window = None
    if zone_a is not None and zone_b is not None:
        window = (float(zone_a), float(zone_b))
    pick = frd.auto_pick_shot(shot, prefer_filtered=(kind == "filt"), search_window_s=window)
    return {"shot_id": shot_id, "trigger_s": float(pick.trigger_s), "source": "auto"}


@router.get("/api/overlays")
def overlays_route(
    shot_id: str = Query(""),
    campaign: str = Query(""),
    kind: str = Query("raw"),
    max_points: int = Query(2000),
    same_label: int = Query(1),
    max_count: int = Query(12),
    folder_average: int = Query(1),
    pipeline: Pipeline = Depends(get_pipeline),
):
    """Señales del mismo label + promedio OK + promedio de carpeta (§3.1)."""
    if not shot_id:
        raise HTTPException(400, "falta shot_id")
    max_points = max(100, min(20000, max_points))
    max_count = max(1, min(50, max_count))
    payload = build_overlays(
        _campaign_root(pipeline, campaign),
        shot_id=shot_id,
        kind=kind,
        max_points=max_points,
        same_label=bool(same_label),
        max_count=max_count,
        folder_average=bool(folder_average),
    )
    body = json.dumps(payload, allow_nan=False, ensure_ascii=False)
    return Response(content=body, media_type="application/json")


@router.get("/api/signal")
def signal_route(
    shot_id: str = Query(""),
    campaign: str = Query(""),
    folder: str = Query(""),
    capture: str = Query(""),
    kind: str = Query("raw"),
    max_points: int = Query(2000),
    geo_flip: int | None = Query(None),
    trigger_s: float | None = Query(None),
    pipeline: Pipeline = Depends(get_pipeline),
):
    """Serie decimada min/max de un disparo, para dibujar hammer + geo (§3.1).

    Handler síncrono (``def``, no ``async def``) a propósito: ``discover_dataset``
    y el decimado tardan ~1.2 s y no pueden correr en el event loop de asyncio
    (§4.6). FastAPI corre los handlers síncronos en su threadpool solo.
    """
    if not shot_id and not folder:
        raise HTTPException(400, "falta shot_id (o folder + capture)")
    # Clampeado, no rechazado (spec §4.1): un cliente que pide 1 merece el mínimo.
    max_points = max(100, min(20000, max_points))
    geo_flip_param = None if geo_flip is None else bool(geo_flip)
    try:
        payload = build_signal_payload(
            _campaign_root(pipeline, campaign),
            shot_id=shot_id,
            folder=folder,
            capture=capture,
            kind=kind,
            max_points=max_points,
            geo_flip_param=geo_flip_param,
            trigger_override=trigger_s,
        )
    except InvalidKind as exc:
        raise HTTPException(400, f"kind inválido: {exc}") from exc
    except UnknownShot as exc:
        raise HTTPException(404, f"shot_id desconocido: {exc}") from exc
    # allow_nan=False es la red de seguridad: si algún no-finito se coló pese al
    # redondeo de signal_view, mejor un 500 ruidoso que un NaN literal en el
    # cuerpo (JSON.parse del navegador lo rechaza sin decir por qué, spec §4.2.5).
    body = json.dumps(payload, allow_nan=False, ensure_ascii=False)
    return Response(content=body, media_type="application/json")
