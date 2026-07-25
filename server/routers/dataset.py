"""Rutas de lectura: catálogo, capturas, cola de trabajos."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from ..pipeline import Pipeline, frd
from ..catalog import scan_catalog
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
