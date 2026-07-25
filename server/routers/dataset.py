"""Rutas de lectura: catálogo, capturas, cola de trabajos."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response

from ..pipeline import Pipeline, frd
from ..catalog import scan_catalog
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
        dataset = frd.discover_dataset(pipeline.raw_root)
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


@router.get("/api/signal")
def signal_route(
    shot_id: str = Query(""),
    kind: str = Query("raw"),
    max_points: int = Query(2000),
    geo_flip: int | None = Query(None),
    pipeline: Pipeline = Depends(get_pipeline),
):
    """Serie decimada min/max de un disparo, para dibujar hammer + geo (§3.1).

    Handler síncrono (``def``, no ``async def``) a propósito: ``discover_dataset``
    y el decimado tardan ~1.2 s y no pueden correr en el event loop de asyncio
    (§4.6). FastAPI corre los handlers síncronos en su threadpool solo.
    """
    if not shot_id:
        raise HTTPException(400, "falta shot_id")
    # Clampeado, no rechazado (spec §4.1): un cliente que pide 1 merece el mínimo.
    max_points = max(100, min(20000, max_points))
    geo_flip_param = None if geo_flip is None else bool(geo_flip)
    try:
        payload = build_signal_payload(
            pipeline.raw_root,
            shot_id=shot_id,
            kind=kind,
            max_points=max_points,
            geo_flip_param=geo_flip_param,
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
